# AutoForge 第十四轮审计报告：调用图展开与跨进程边界

> 审计对象：`https://github.com/lidicn/AutoForge`（main 分支快照）
> 本轮主题：**修复第十三轮 lesson 44 的盲区——模板反查只认直接调用，漏掉 helper 封装的实现**
> 新增能力：`CallGraph` 跨函数展开器（3164 个函数索引）+ 污点传播（P5/P6/P7）
> 判定标准：严格档 —— **不实测不升级为缺陷**
> 报告日期：2026-10-06

---

## 一、执行摘要

第十三轮我自己的传播扫描器漏掉了 R13-01（`DeviceGuardRegistry.from_file(path)` 把解析藏在被调用函数里），并把这个错误记成了 lesson 44。第十四轮**先把这个盲区修掉**，再跑。

**结果：跨函数展开验证通过（`af_service.py:722` 已被正确召回），并在此基础上找到 3 个新缺陷——全部落在"跨进程边界"。**

| 编号 | 级别 | 一句话 | 位置 |
|---|---|---|---|
| **R14-01** | 🔴 High | **`pending_asks.json` 裸写 + 读侧静默：并发下半截率实测 73%，挂起的人工应答会"看不见"** | 写 `af_live.py:393` / 读 `af_api.py:585` |
| **R14-02** | 🟡 Medium | **`load_conf` 裸读，调用方只兜 `FileNotFoundError`**——文件损坏/权限错误直接穿出 | `af_store.py:522` |
| **R14-03** | 🔵 Low | **`answer_inbox` 用毫秒时间戳作文件名**，同毫秒两条应答后者覆盖前者，且消费后 `unlink` ⇒ 一条应答永久丢失 | `af_api.py:842` |

### 本轮最值得说的一件事

**我犯了和第十二轮完全相同的错误——第二次。**

`af_insight_queue._load:163` 被我的 P6 扫描器判为"reject_log ✗"。实际它做的是：

```python
except (OSError, ValueError, TypeError) as exc:
    # 记账而不是咽下：坏文件会让"这条投过"变成"这条没投过"，必须看得见
    self.unreadable.append(f"{path.name}: {type(exc).__name__}: {exc}")
    return None
```

它有留痕——只是形式是 **append 到错误收集列表**，不是 logger。第十二轮我把 `af_auth` 的毒化标志误判成"无留痕"，这一轮又把记账列表误判成"无留痕"。**lesson 40 在同一年内第二次应验，犯的还是我。**

已把判定扩展到四叉：logger / raise / 计数 / **错误收集列表 append**。

---

## 二、工作流迭代

| 轮次 | 方法 | 确认缺陷 |
|---|---|---|
| 十一 | 第三方工具 + 召回率验证 | 2 |
| 十二 | 候选自动分诊（T1/T2/T3） | 2 |
| 十三 | 模板反查（只认直接调用） | 2 |
| **十四** | **调用图展开 + 污点传播 + 信任边界分层** | **3** |

### 新增资产

**`scripts/scan_round14.py`**（333 行）：

| 能力 | 说明 |
|---|---|
| `CallGraph` | 3164 个函数索引，跨文件解析 `Class.method` / `func()` 调用 |
| **P2 展开版** | 策略文件加载——跨函数追 `json.loads`，找"解析藏在被调用方"的实现 |
| P5 污点传播 | 下发海点（`Request` / `urlopen` / `/api/services`）是否有 domain 白名单；**跨函数展开委托链** |
| P6 入站硬化 | 入站解析四件套：decode_guarded / type_check / reject_log / whitelist |
| P7 写入纪律 | AST 精确判定 `write_text` / `write_bytes` / `json.dump`（**排除 `json.dumps` 子串误判**） |

### 修正的两处假阳性

1. **P5 委托误判**：`ha.py:262/266/273` 是 `self.transport(action, params)` 委托，校验在被委托方 `HATransport.call`。改为展开委托链后，**P5 归零**——所有下发海点都有校验或委托给有校验方。
2. **P7 子串误判**：`"json.dump"` 会匹配 `"json.dumps"`（误报 `ha.py:104`、`af_api.py:837`）。改用 AST 精确节点判定后，从 114 条降到 11 条。

### 四个新范式（已记入 lessons 第 47–50 条）

**47** "留痕"判定必须认四叉：log / raise / 计数 / **错误收集列表 append**（第二次踩）
**48** 找**跨进程**落盘文件——进程边界最容易被漏掉，因为写侧作者看不到另一个进程
**49** 半截率实测法：验证非原子写不用推理，直接压测（实测 73% 远超直觉）
**50** 先分信任边界（外网 / 跨进程 / 同进程）再套硬化判据，否则全是噪音

---

## 三、确认缺陷

### 🔴 R14-01　`pending_asks.json` 裸写 + 读侧静默：并发下半截率 73%

**写入侧**：`src/autoforge/af_live.py:393`（watch 进程，每个 tick 写一次）

```python
out = {"asks": asks, "ts": time.time()}
(sc_dir / "pending_asks.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
)
# except Exception:
#     pass          ← 写失败也静默
```

**读取侧**：`src/autoforge/af_api.py:585`（API 进程，DB 轮询）

```python
try:
    data = json.loads(sc.read_text(encoding="utf-8"))
except Exception:
    return {"ok": True, "asks": []}      # ← 无日志，静默返回空
```

**实测**（1 写线程 + 3 读线程，120 条 ask 的大 payload，跑 3 秒）：

```
读次数 16634；解析失败（读到半截）12150 次；解析成功但 asks 为空 0 次
⇒ 半截率 73.0%
```

**这个数字远超我的直觉**（原本预计 < 5%）。`write_text` 不是原子操作，且默认无 fsync——大 payload 下读侧撞上半截内容的概率极高。

**后果**：DB 轮询 `/api/asks/pending` 时读到空列表 ⇒ **挂起的人工应答"消失"**。而 `asks` 是人工介入通道——项目最看重的人审闸。

**为什么是"静默"**：读侧 `except Exception` 无日志，返回的是 `{"ok": True, "asks": []}`——**"读取失败"和"确实没有待答"完全无法区分**。这正是项目铁律 #5（EXEMPT ≠ VERIFIED）在进程边界上的形态。

**缓解（必须诚实说明）**：轮询是持续的，下一次 tick 大概率能读到 ⇒ **瞬时自愈**，不会永久丢失。所以危害是"应答被延迟/偶发不可见"，不是数据损坏。但 73% 的失败率意味着**在持续有 ask 的场景下，DB 会频繁看到空列表**，体感是"这系统老是不出 ask"。

**对比正确范式**：`af_store` / `af_persist` / `af_undo._save` / `af_catalog` / `af_predict` **全部**用 `atomic_write_text`（tmp + `os.replace`）。`pending_asks.json` 是**唯一跨进程读写却没走原子写的**。

**修复建议**：

```python
# af_live.py:393
_atomic_write_text(sc_dir / "pending_asks.json",
                   json.dumps(out, ensure_ascii=False, indent=2))

# af_api.py:585 —— 失败与"没有"必须可分
except Exception as exc:
    logger.warning("pending_asks.json 读取失败（%s），如实返回不可用而非空列表", exc)
    raise HTTPException(status_code=503, detail="pending asks 暂时不可读")
```

**回归验证**：
1. 复跑并发压测 → 半截率应为 **0%**
2. 手动截断 `pending_asks.json` → 接口应返回 **503**，不是 `{"ok": True, "asks": []}`

---

### 🟡 R14-02　`load_conf` 裸读，调用方只兜 `FileNotFoundError`

**位置**：`src/autoforge/af_store.py:522`

```python
def load_conf(self, name: str) -> ConfidenceStore:
    path = self.root / f"{self._dir(name).name}.conf.json"
    payload = json.loads(path.read_text(encoding="utf-8"))   # ← 无 try
    return load_confidence(payload["conf"])                   # ← 直接下标，KeyError
```

**调用方只兜了"文件不存在"**：

```python
# af_service.py:1042
def _conf_of(store, name, graph):
    try:
        return store.load_conf(name)
    except FileNotFoundError:
        return ConfidenceStore().seed(graph)
```

`af_service.py:1980` 同形（`except FileNotFoundError: continue`）。

**缺口**：文件**存在但损坏**（JSON 坏、缺 `conf` 键）→ `ValueError` / `KeyError` → **穿出**，调用方兜不住。任何 `OSError`（权限、EIO）同样穿出。

**同文件对照**：`af_store` 的 `_read_tags`（269）、`history`（247）、`_read_jsonl`（104）**都有 try**。只有 `load_conf` 没有——又是同文件内的不对称（第十轮 R10-01 同族）。

**严重度 Medium**：写入侧用了 `atomic_write_text`，损坏概率不高；且置信度是分级展示，失败不会绕过安全闸。但一个未被任何 except 覆盖的裸读，在 `af_service` 主路径上出现，不该留着。

**修复**：`except (OSError, ValueError, KeyError)` → 记 warning + 返回 `ConfidenceStore().seed(graph)`；或至少让调用方把 `FileNotFoundError` 扩成 `(OSError, ValueError, KeyError)`。

---

### 🔵 R14-03　`answer_inbox` 毫秒时间戳文件名，同毫秒应答互相覆盖

**位置**：`src/autoforge/af_api.py:842`

```python
fname = f"{int(time.time() * 1000)}.json"
```

**读侧**（`af_live.py:476`）glob 全部 `*.json`，**消费成功后 `f.unlink(missing_ok=True)`**（501 行）。

**后果**：同毫秒内写入两条应答 → 第二个文件名相同 → **覆盖第一个** → 该文件被消费一次后删除 ⇒ **一条人工应答永久丢失**。

**严重度 Low**：人类在同一毫秒内提交两条应答几乎不可能；但程序化批量应答（脚本/自动化 DB）可以做到。且它是"静默覆盖"——写侧不会报错。

**修复**：文件名加随机后缀（`uuid4().hex[:8]` 或 `os.getpid()`），或写前 `exist_ok=False` 冲突即重试。

---

## 四、扫描结果与分诊

```
══ P2_policy_load_expanded：16 条 ══
   af_auth.py:234          毒化标志 ✓（fail-closed）        ← 模板
   af_insight_queue.py:163 记账 append ✓                    ← **我误判了，实际是正确范式**
   af_service.py:722       return None（有日志）            ← 第十三轮 R13-01
   af_config.py:54/60 · af_catalog.py:1049 · af_metrics.py:163
                           return None（**无日志**）        ← 第十三轮 R13-02 同类

══ P5_sink_domain_guard：0 条 ══
   所有下发海点均有 domain 白名单，或委托给有校验的被委托方 ✓

══ P6_inbound_hardening：2 条 ══
   af_insight_queue.py:163  ✗ 实际是同进程队列文件，非外网入站（误判）
   af_store.py:522          ✓ R14-02

══ P7_write_guard：11 条 → 10 处真裸写 ══
   af_undo.py:287  假阳性（tmp+replace 手写原子，已排除）
   af_live.py:393  ← R14-01（跨进程，危害最高）
   af_api.py:842   ← R14-03
   af_metrics.py:229 ← 第十二轮 R12-01 所在函数（已报）
   af_cli.py:902/1010/1120 · af_runtime_ext.py:91 · af_service.py:2330/2364
   · af_test.py:198 ← CLI/一次性导出场景，单进程无并发，低优先
```

### 验证通过：下发链路硬化完备

P5 归零是真结论，值得一说：

- `af_adapters/ha.py:94` `call()` 有 `_HA_DOMAIN_RE` 校验 domain/service（P0-11，防路径注入）
- `HATransport.call`（100 行）4xx/5xx 与传输异常**均**返回 `CallResult.fail`，不吞异常
- MQTT 入站 `af_mqtt_bridge.handle_message`：主题白名单（只认 `INSIGHTS_TOPIC`）+ JSON 解码兜底 + `Mapping` 类型校验 + `_reject` 留痕计数，**四件套齐全**

**外部不可信输入这一侧做得扎实**，本轮没有新增缺陷。

---

## 五、本轮验证通过（确认无问题）

| 项目 | 验证方式 | 结论 |
|---|---|---|
| **`af_insight_queue._load`** | 读实现 | ✅ **第五个正确范式孤岛**——`unreadable` 记账 + 注释"记账而不是咽下…必须看得见"（我误判了，已修正） |
| **HA 下发海点 domain 校验** | 污点扫描 P5 | ✅ 全仓 0 处无校验下发 |
| **MQTT 入站硬化** | 读 `handle_message` | ✅ 白名单/解码兜底/类型校验/留痕计数四件套齐全 |
| **`af_undo._save`** | 读实现 | ✅ tmp + replace 手写原子（假阳性，已排除） |
| **`answer_inbox` 密钥校验** | 读 `read_answer_inbox` | ✅ HMAC-SHA256 签名校验，校验失败**不删文件**（保留审批证据链） |
| **`af_api.py:581` 读 asks 的门禁** | 读实现 | ✅ 已按裁定 20261004 §一 F-1 补 `Depends(_read)` |

---

## 六、已排除（rejected）

| 候选 | 理由 |
|---|---|
| **P5 的 `ha.py:262/266/273`** | 是 `self.transport(...)` 委托，校验在 `HATransport.call`。跨函数展开后归零 |
| **P7 的 `json.dumps` 命中** | 子串误判，AST 精确判定后排除 |
| **`af_undo.py:287`** | tmp + replace 手写原子，正确 |
| **`ha.py:270-273` 快照捕获日志写反** | 第六轮 R6-02 已报，不重复 |
| **P6 的 `af_insight_queue`** | 同进程队列文件，非外网入站，不适用入站硬化判据 |

---

## 七、修复优先级

| 顺序 | 缺陷 | 工作量 | 说明 |
|---|---|---|---|
| **P1** | R14-01 asks 原子写 + 读侧如实报错 | 约 4 行 | 73% 半截率，影响人工应答闸，且静默 |
| **P2** | R14-02 `load_conf` 加 try | 约 3 行 | 主路径裸读无兜底 |
| **P3** | R14-03 inbox 文件名去重 | 约 2 行 | 低概率，但静默丢应答 |

---

## 八、十四轮审计的横向观察

| 轮次 | 主题 | 确认缺陷 |
|---|---|---|
| 一~六 | 运行时代码 | 22 |
| 七~十三 | 生命周期/门禁/测试/配置/持久化/工具/分诊/模板反查 | 15 |
| 十四 | 调用图展开 + 跨进程边界 | 3 |

**本轮三条缺陷有一个共同点：全部在"边界"上。**

| 缺陷 | 边界 |
|---|---|
| R14-01 | **跨进程**（watch 写 / API 读） |
| R14-02 | **跨调用层**（store 裸读 / service 只兜 FileNotFound） |
| R14-03 | **跨时间**（毫秒级命名冲突） |

这不是巧合。**写 `af_live._write_asks` 的人只看到自己的进程**，不会想到 API 进程正在读同一个文件；写 `load_conf` 的人假设文件一定合法，而调用方只处理了"没有文件"这一种。

**跨进程 / 跨层边界是自动化审计的天然盲区**——因为静态分析默认"单进程、单调用链"。第十四轮靠"调用图展开 + 信任边界分层"补上了这一块，但**没有补完**：`CallGraph` 目前只做一层调用解析，不做跨文件的完整可达性分析。

**"正确范式孤岛"清单现在有五个了**：

| 范式 | 位置 | 孤岛外漏的地方 |
|---|---|---|
| 原子写 `atomic_write_text` | `af_store`/`af_persist`/`af_undo`/`af_catalog`/`af_predict` | **`af_live.pending_asks`**（R14-01） |
| 配置解析 `_env_number` | `af_bus` | 6 处裸转换（R9-01） |
| 策略加载毒化标志 | `af_auth` | `device_acl`（R13-01） |
| 坏行保留 | `af_telemetry` | `af_metrics`（R12-01） |
| 坏文件记账 | `af_insight_queue` | `af_store.load_conf`（R14-02） |
| 坏文件隔离 `.corrupt` | `af_predict` | 无处复用 |

**六个正确范式，六个文件，全仓各一处**；而九到十四轮报的缺陷，全部落在这些孤岛之外。

**给工程团队的一句话**：你们写正确实现的能力毫无疑问——连注释都比多数项目清楚。问题从来不是"不会写"，是**写完就停在那一个文件里**。六个范式如果当初任何一个被抽成公共 helper，后面六轮就至少少报四条缺陷。**第十三次遇到同一形状，真的该抽了。**

---

## 九、已沉淀的复用资产

| 资产 | 位置 |
|---|---|
| **调用图展开 + 污点传播扫描器（P2/P5/P6/P7）** | **`audit-env/scripts/scan_round14.py`** |
| 污点扫描明细 | `audit-env/reports/round14-taint.json` |
| 同形状传播扫描器（P1–P4） | `audit-env/scripts/scan_round13.py` |
| 候选自动分诊器（T1/T2/T3） | `audit-env/scripts/triage_round12.py` |
| 误报修正手册（46 条 + 本轮 4 条 = **50 条**） | `audit-env/scripts/lessons-round2.md` |
| ADM-auditkit（含自建 CLI） | `/data/workspace/adm-auditkit/ADM-auditkit-main/auditkit` |

### 本轮新增 lessons（47–50）

- **47** "留痕"判定必须认四叉：log / raise / 计数 / **错误收集列表 append**（**本轮第二次踩**）
- **48** 找**跨进程**落盘文件——进程边界最容易被漏掉
- **49** 半截率实测法：验证非原子写直接压测，不用推理（实测 73%，远超直觉）
- **50** 先分信任边界（外网 / 跨进程 / 同进程）再套硬化判据

---

## 附：环境说明

| 项 | 值 |
|---|---|
| 沙箱 Python | 3.10.12（项目声明 `>=3.11`） |
| 本轮实测 | 并发半截率压测 1 组（16634 读 / 12150 失败）+ 污点扫描 4 组 + 假阳性修正 2 处 |
| CallGraph 规模 | 98 文件 / 3164 函数索引 / 跨文件方法解析 |
| 仓库状态 | 探针与变异均已还原 |
