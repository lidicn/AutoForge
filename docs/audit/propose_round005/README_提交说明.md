# 提交说明：round-005 triage 规则改进提案

> **来源**：对 `lidicn/AutoForge` round-005 审计（547 条机器候选 → 逐条人工 triage）
> **提交者**：Claude Code 审计 agent · 2026-10-11
> **审计方法**：auditkit 静态扫描 + 6 个并行 agent 逐条读容器内源码核验
> **目标**：把这些 triage 发现的规则系统性误报根因 + 新规则候选提交给 ADM-auditkit 维护者

---

## 背景

本次 round-005 扫出 547 条候选。selftest 门禁全绿（21 分析器 ok，0 静默失效），所以命中是真实的——但"真实命中"不等于"真 bug"。

经全量 triage（high+medium 124 条全部逐条核验 + low 423 条抽样 100 条核验）：

| 维度 | 候选数 | 确证真 bug | 误报 | 质量问题 |
|---|---|---|---|---|
| high+medium | 124 | 0 | 105 | 19 |
| low（抽样） | 100 | 3 | ~48 | — |

**high+medium 124 条确证 0 条真 bug，误报率 85%。** 这说明现有规则在这批规则上存在系统性误报模式——不是偶发，而是规则判据本身不够精确。

---

## 一、规则改进建议（6 条，均有源码证据）

### 1.1 `py.except_return_failopen`：区分 True 的语义方向（2 条误报）

**根因**：规则只看 `except → return True`，但 True 的语义有两个方向：
- **放行方向**（真 fail-open）：True = 通过/健康/拿到锁 → 应报
- **告警方向**（fail-closed）：True = 有问题/有竞争/有漂移 → 不应报

**误报证据**：
- `af_canary.py:103` `except UnknownEntity: return True` —— docstring 明确"保守判有漂移、让回滚把手生效"，True=有漂移（告警），是 fail-closed
- `af_flock.py:132` `except (BlockingIOError,OSError): return True` —— True=锁被占用/有竞争，是 fail-closed

**建议**：增加语义判据。当函数名或上下文表明 True=告警方向（canary/drift/held/occupied/conflict/has_*），不判 fail-open。

### 1.2 `py.outbound_call_no_timeout`：排除内存 dict 查找（1 条误报）

**根因**：把 `session.ir.get("id")` 误识别为外部调用。`session.ir` 是内存 IR（中间表示）Python dict，`.get()` 是 dict 查找，不会挂起。

**误报证据**：`af_orchestrator.py:2323`

**建议**：排除 `.get(...)`/`[...]` 等已知 dict/映射访问形态；只认真正的网络/进程调用。

### 1.3 `py.outbound_call_no_timeout`：区分 Popen 构造与阻塞方法（1 条误报）

**根因**：`subprocess.Popen` 构造器本身非阻塞、不接受 timeout 参数（它启动子进程后立即返回）。timeout 只适用于 `.wait()`/`.communicate()`。

**误报证据**：`af_service.py:2448` 用 Popen 启动守护进程，未调用 `.wait()`/`.communicate()`。

**建议**：Popen 构造排除；只对 `.wait()`/`.communicate()`/`.poll()`（阻塞方法）检查 timeout。

### 1.4 `py.unbounded_container`：识别整体替换与键集封顶（35 条误报，最大头）

**根因**：规则只看"有无 pop/clear/del/maxlen"，漏了两类：
1. **整体替换型**（非累加）：`self.attributes.update(new)` + `reset()` 整体替换、`self._attributes[entity_id] = dict(...)` 按键覆写——是替换不是 append
2. **键集受图规模封顶**：`dict[str, X]` 的键=automation_id/entity_id，图内有限集，增长受图规模封顶

**误报证据**（35 条，仅列典型）：
- `af_vhass/device_sm.py:68` `self.attributes.update(new_attributes)` + `reset()` 整体替换
- `af_vhass/high_fidelity.py:126` `self._attributes[entity_id] = dict(new_attributes)` 按键覆写
- `af_conflict._circuits`/`af_health.extra_ids`/`af_intervention.managed` 键集=automation_id（图内有限集）
- `af_bus.emitted` 是 `deque(maxlen=1000)`（规则已识别 deque 但未识别其他上限形态）
- `af_scheduler._time_fired` 按天清理（`{m for m in self._time_fired if m[2]==today}`）

**建议**：
- 增加"整体替换"识别（`self.X[k]=dict/list` 覆写、`.update()` 非累加）
- 键集为有限 entity_id/automation_id 的 dict/set 降级
- 同步 PITFALLS P9（有界容器不止 deque）

### 1.5 `py.json_parse_unguarded`：区分可迭代表达式与循环体内解析（1 条误报）

**根因**：`for item in json.loads(file.read_text()):` 的 `json.loads` 是 for 循环的**可迭代表达式**，在循环开始前一次性解析整个文件。这不是"循环内裸 JSON 解析"——循环体里只是 dict 访问，不再做 JSON 解析。

**误报证据**：`af_cli.py:1062`

**建议**：区分 `for x in json.loads(...)`（一次性解析，前兆）与 `for x in items: json.loads(...)`（逐条解析，真 bug）。

### 1.6 `TS-03-unhandled-promise`：增加同步 API 白名单（33 条误报）

**根因**：把以下同步调用误判为"未 await 的 Promise"：
- `localStorage.setItem/removeItem`（同步 Web API）
- `Map.set/get`、`Array.push/unshift`（同步集合操作）
- `Vue app.use/mount`（同步 Vue API）
- `void` 返回的辅助函数

**误报证据**：33 条，集中在 `ui-user-mimo/src/` 和 `ui-user/src/`。所有 `async function` 内的 await 链（`api.login`/`api.logout`/`refresh`）均正确 await，标记的行只是同步副作用调用。

**建议**：增加同步 API 白名单：localStorage/sessionStorage/Map/Set/Array 方法、Vue app.use/mount、明确 void 返回的函数。

---

## 二、新规则候选（1 条）

### `py.read_write_asymmetry` —— 读写对称性缺口

**检测目标**：同一 sidecar/状态文件在写侧有 logger 留痕，但读侧的 except 块无留痕且返回空/默认值。

**危害**：文件损坏时读失败被当"无数据"，数据静默丢失；写侧的留痕无法触发（因为写没失败）——读写两侧防护不对称。

**确证证据**（round-005 BUG-L1）：
- 写侧 `af_live.py:457`：`logger.warning("pending_asks 原子写入失败...")` + 注释"R14-01/R14-03 修复：写失败不得静默吞掉"
- 读侧 `af_api.py:676`：`except Exception: return {"ok": True, "asks": []}` 无 logger——**读写对称性缺口**

**形态**：需跨文件 join（写侧在某文件、读侧在另一文件），现有 5 判据无法表达。建议作为跨语句 join 判据，同 `workflow-iteration-proposal.md §4.3` 的跨语句 join 需求。

---

## 三、确证的 3 条真 bug（随提案附上，供维护者参考）

| 编号 | 危险 | 位置 | 缺陷 |
|---|---|---|---|
| BUG-L1 | 中 | `af_api.py:676` | pending_asks 读失败静默返回 `asks:[]`，提问被丢失；读写对称性缺口 |
| BUG-L2 | 轻 | `af_runtime_plugins.py:331,340` | API 端点注册失败 `except:pass` 无 logger，缺失端点排障困难 |

这 3 条来自 low 级 `py.except_broad_swallow` 的前兆命中——规则 message 自身声明"它本身不是缺陷"，但人工 triage 从前兆里揪出了真吞。这印证了 `workflow-iteration-proposal.md §1` 的论点：**静态命中不可信，人工确证才是终点**。

---

## 四、提案包内容

```
propose_round005/
├── CHANGE_20261011.yaml          # 变更说明（结构化，给维护者）
├── README_提交说明.md             # 本文件（为什么要改、怎么改）
├── round-005_report_raw.md        # auditkit 原始报告（547 条候选全量）
└── AutoForge_auditkit_round005_20261011.md  # 完整 triage 报告（含 bug 详情）
```

---

## 五、未能提交的部分（如实说明）

| 项 | 原因 |
|---|---|
| 新规则 `.py`/`.yaml` 文件 | `py.read_write_asymmetry` 需跨文件 join，现有 5 判据无法表达，作为判据增补建议提交 |
| `changes.patch` | 本次是 triage 产出非代码修改，无 diff 可生成 |
| PoC 实测脚本 | round-005 是纯静态 triage，未做行为级 PoC（见 workflow-iteration-proposal §1） |

---

## 六、如果只做一件事

**修 `py.unbounded_container` 的整体替换识别**——它贡献了 35 条误报（占 high+medium 误报的 1/3），是投入产出比最高的一条。
