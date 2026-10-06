# AutoForge 第五轮审计报告：契约与语义一致性

> 审计对象：`https://github.com/lidicn/AutoForge`（main 分支快照）
> 审计范围：`src/autoforge/` 内核（98 个 Python 源文件 / 77,949 行）
> 本轮主题：**契约与语义一致性**（声明行为 vs 实际行为）
> 判定标准：严格档 —— 契约类缺陷须**双向比对实测**，行为类须给出具体输入→输出反例
> 报告日期：2026-10-06

---

## 一、执行摘要

第五轮把靶子放在**"代码说它要做什么"与"它实际做了什么"之间的缝隙**。这类缺陷前四轮的扫描器都看不见——因为代码本身没有"错"，只是**语义不一致**。

**结论：找到 2 个缺陷，其中 1 个是同一组算子里的语义分裂。**

| 编号 | 级别 | 一句话 | 位置 |
|---|---|---|---|
| **R5-01** | 🟠 High | **`eq`/`ne` 走字符串比较，`gt`/`lt` 走数值比较——同一组算子两套语义，数值相等却判失败** | `af_expect.py:85-106` |
| **R5-02** | 🟡 Medium | 爆炸半径超限返回 HTTP 500 而非 400 | `af_api.py:450-456` → `af_service.py:338` |

### 一个必须诚实说明的前提

本轮是**五轮里产出最少的一轮**（2 条）。这不是懈怠，而是覆盖率自然收敛的信号——前四轮已经把单点 bug、异常吞咽、资源上限、并发竞态扫过，第五轮能挖到的缝隙确实变窄了。

但更有价值的是：**本轮验证了 3 个此前存疑的契约面，结论都是"一致"**（详见第四节）。对一个要上真机的系统来说，"确认某处没问题"和"找到问题"同样重要——前者能让你把注意力挪走。

---

## 二、工作流迭代：这一轮改了什么

| 轮次 | 主题 | 确认缺陷 |
|---|---|---|
| 一 | 安全 / 架构 / Agent 威胁模型 | 5 |
| 二 | 稳定性 / 功能性（异常吞咽） | 7 |
| 三 | 递归 / 资源上限 | 3 |
| 四 | 并发 / 状态一致性 / fd 生命周期 | 3 |
| **五** | **契约 / 语义一致性** | **2** |

### 三个新范式（已记入 lessons）

**1. 契约验证必须双向比对，单方向会漏**

查 MCP 工具契约时，只查"函数读了但 schema 没声明"会得到 0 命中（因为 `_undeclared_args` 已拦截）；必须**反向再查**"schema 声明了但函数没读"。两个方向都查完才能下"契约一致"的结论。

本轮 **31 个 MCP 工具，双向 0 不匹配**。

**2. 语义一致性 = 同一组算子的处理方式横向对比**

`_apply_op` 里 `eq`/`ne` 与 `lt`/`gt` 处理同一类输入却用了两套类型转换策略。单个函数看毫无问题，**把相邻分支摆在一起才浮现**。

判据：**同一模块内处理同一类输入的不同分支，若类型转换策略不同，即为可疑点。**

**3. 异常映射覆盖要算"传递可达"，直接匹配会全漏**

查"路由是否处理 `ServiceError`"时，只看路由**直接调用**的 svc 函数是否 raise → **0 命中**。必须做调用图**传递闭包**：39 个未包裹路由里，只有 `enable_by_tag` 经 `_check_blast` 传递可达。直接匹配会完全漏掉这条。

### 新增扫描器

`scripts/scan_round5.py`（G01–G03）：`G01` 声明/读取键不匹配 · `G02` 硬取非必填键 · `G03` 过期注释。

产出 25 条（G02 24 条判定方向有误、实际 0 命中；G03 1 条为已报过的过期注释），**两条确认缺陷均由人工横向对比发现，非扫描器产出**——这本身说明：越到深水区，机器扫描的边际收益越低，人工分诊越关键。

---

## 三、确认缺陷

### 🟠 R5-01　`eq`/`ne` 与 `gt`/`lt` 语义分裂：数值相等却判失败

**位置**：`src/autoforge/af_expect.py:85-106`

```python
def _apply_op(op: str, actual: Any, expected: Any) -> bool | None:
    """比较运算。返回 `None` 表示「无法比较」（→ unverified，而非判失败）。"""
    if op == "eq":
        if isinstance(actual, str) or isinstance(expected, str):
            return str(actual) == str(expected)     # ← 字符串比较
        return actual == expected
    if op == "ne":
        inner = _apply_op("eq", actual, expected)
        return None if inner is None else (not inner)
    try:
        left, right = float(actual), float(expected)  # ← 数值比较
    except (TypeError, ValueError):
        return None
    if op == "lt":  return left < right
    ...
```

**问题**：同一组比较算子，`eq`/`ne` 走**字符串**语义，`lt`/`lte`/`gt`/`gte` 走**数值**语义。

**实测反例**（真实函数调用）：

| op | actual | expected | 结果 | 数值上应为 | |
|---|---|---|---|---|---|
| `eq` | `"22.0"` | `22` | **False** | True | ❌ |
| `eq` | `"22"` | `22.0` | **False** | True | ❌ |
| `eq` | `"22.00"` | `"22.0"` | **False** | True | ❌ |
| `eq` | `22.0` | `"22"` | **False** | True | ❌ |
| `eq` | `" 22"` | `"22"` | **False** | True | ❌ |
| `eq` | `"22.0"` | `22.0` | True | True | ✓ |
| `eq` | `22` | `22` | True | True | ✓ |

对照组（语义正确）：`gt("22.0", 22)` → `False`（数值比较，22.0 不大于 22，正确）。

**为什么可达**：Home Assistant 的状态与属性**常以字符串返回**（`sensor.temperature` → `"22.5"`），而 IR 的 `expect.value` 在 schema 里**无类型约束**（`af_ir/schema/ir.schema.json` 的 `$defs/expect/properties/value` 只有 description，无 `type`）。用户写 `value: 22`（数字）对比 HA 返回的 `"22.0"` 即触发。

**后果**：`expect` 后置条件断言**误判 fail**。自动化实际跑对了，却被判"军令状没兑现"——`forge sim` 报告失败，真机运行的验证结果同样失真。

**失败方向是安全的（重要）**：误判方向是 **fail 而非 pass**——不会把失败伪装成成功，**没有安全闸绕过的风险**。这是误报，不是漏报。

**修复建议**：`eq`/`ne` 增加"两侧都像数字时走数值比较"的分支，与 `gt`/`lt` 对齐：

```python
if op == "eq":
    if isinstance(actual, str) or isinstance(expected, str):
        # 两侧都像数字 → 走数值比较，与 lt/gt 语义一致
        try:
            return float(actual) == float(expected)
        except (TypeError, ValueError):
            return str(actual).strip() == str(expected).strip()
    return actual == expected
```

同时建议顺手处理首尾空白（`" 22"` vs `"22"`）。

> 注意：`float("nan")` 与 `float("inf")` 的比较需另行考虑，建议显式排除。

---

### 🟡 R5-02　爆炸半径超限返回 HTTP 500 而非 400

**位置**：`af_api.py:450-456`（路由）→ `af_service.py:338`（`_check_blast`）

```python
# af_api.py:450-456
@app.post("/api/graphs/enable", dependencies=[Depends(_write)])
def api_graph_enable(body: TagEnableBody) -> dict[str, Any]:
    return svc.enable_by_tag(store, body.tag, True)      # ← 无 _svc 包裹、无 try/except

@app.post("/api/graphs/disable", dependencies=[Depends(_write)])
def api_graph_disable(body: TagEnableBody) -> dict[str, Any]:
    return svc.enable_by_tag(store, body.tag, False)
```

传递链：`enable_by_tag`（`af_service.py:313`）→ 第 338 行 `_check_blast(total, ...)` → `raise ServiceError(..., status=400)`。

而 `af_api.py:685` 的 `_svc` 包装器（负责 `ServiceError → HTTPException(status)`）**只包裹了 15 个路由**，这两条不在其中；全文件也**无全局 `@app.exception_handler`**。

**后果**：按标签批量启用/禁用时，若影响的自动化条数超过 `AUTOFORGE_BLAST_RADIUS` 上限，本应返回 **400**（业务拒绝，客户端可提示"请拆分"），实际返回 **500**（服务端错误）。客户端重试逻辑与 UI 错误提示会因此错判——把"你操作太大"当成了"服务器坏了"。

**判定依据（静态推断，非运行时实测）**：沙箱未安装 fastapi，无法用 `TestClient` 实测。判定链为：① 确认无全局异常处理器；② 静态调用图确认 `enable_by_tag` 传递可达 `_check_blast`；③ 确认该路由无 try/except 与 `_svc` 包裹。三步齐备，结论可靠，但**建议在装了 fastapi 的环境补一次端到端验证**。

**修复**：两条路由改用 `_svc` 包裹即可，与项目既有约定一致：

```python
return _svc(svc.enable_by_tag, store, body.tag, True)
```

---

## 四、本轮验证通过（确认无问题）

这几项此前存疑，本轮核实后**结论均为一致**，可放心：

| 契约面 | 验证方式 | 结论 |
|---|---|---|
| **MCP 工具声明 ↔ 实际入参** | 31 个工具，`TOOLS` schema 声明键与函数实际读取键**双向比对** | ✅ 双向 0 不匹配；`_undeclared_args`（`af_mcp.py:880-892`）严格校验已完整生效，无"读得到但传不进"的参数 |
| **节点类型声明 ↔ 执行器覆盖** | `NODE_KINDS` 8 种（on/if/do/ask/wait/set/pass/group）、`EDGE_KINDS` 7 种，与 schema 枚举、执行器分派逐项比对 | ✅ 全部一致，8 种节点均有处理分支，无静默忽略 |
| **`fn` 保留字段** | 读实现 | ✅ "读到即报未实现"（`af_executor.py:731-734`）为有意设计，非缺陷 |
| **时间语义** | `parse_duration` / `matches_at` | ✅ 支持 `HH:MM:SS`，拒绝 `inf`/`nan`/负值；`matches_at` 严格校验 `HH:MM` |
| **样例 IR 端到端仿真** | 17 个样例跑 `sim` | ✅ `case04_ask_timeout` 的 `HIGH_RISK_AFTER_SUSPEND` 是扫描器正常告警；`invalid_case_cross_ring` 的 `CROSS_DEP_CYCLE` 是设计决策"已放行"——**均非 bug** |
| **前置：API 层异常映射** | 39 个调用 `svc.*` 的未包裹路由 | 仅 R5-02 一处传递可达 `ServiceError`；其余 38 个的 svc 函数不抛 `ServiceError`，安全 |

---

## 五、已排除（rejected）

| 候选 | 数量 | 推翻理由 |
|---|---|---|
| **G02「硬取非必填键」** | 24 | 判定方向有误（把必填键的正常硬取也算入），修正方向后**实际 0 命中** |
| **G03 过期注释** | 1 | `af_mcp.py` 声称 `fp-authcode-bruteforce` 加重情节"原样成立"，但下方 `_undeclared_args` 已有修复。**第一轮已报过**，本轮仅复核确认，不重复计入 |
| **`_state_matches` 字符串比较** | — | 实体形态 `state` 由 schema 强制为 string/array[string]，两侧同类型，**无跨类型误判** |
| **`expect` 无 `op` 时** | — | 默认 `eq`（schema 与代码一致），非缺陷 |
| **`op` 非法值** | — | 返回 `None` → 记 `unverified` 而非 fail，处理正确 |
| **G7 零有损往返契约** | — | `graph_to_raw` 不在 `af_ir` 导出中，17 个样例无法直接验证该路径。**已放弃该验证方向**，如实说明 |

---

## 六、修复优先级

| 顺序 | 缺陷 | 工作量 | 说明 |
|---|---|---|---|
| **P1** | R5-01 `_apply_op` 语义对齐 | 约 8 行 | 消除后置断言误报；失败方向安全，不紧急但影响可信度 |
| **P2** | R5-02 两路由加 `_svc` | 2 行 | 一行一个 `return`，与项目既有约定一致 |

**回归验证**：
1. `_apply_op("eq", "22.0", 22)` → 应为 `True`；`_apply_op("eq", " 22", "22")` → `True`
2. `_apply_op("eq", "on", "on")` → 仍为 `True`（字符串场景不受影响）
3. 爆炸半径超限时 `/api/graphs/enable` → 应返回 400 而非 500

---

## 七、五轮审计的横向观察

| 轮次 | 主题 | 确认缺陷 | 主因 |
|---|---|---|---|
| 一 | 安全 / 架构 | 5 | 审批链路身份语义不严密 |
| 二 | 稳定性 / 功能性 | 7 | 异常被吞后不留痕 |
| 三 | 递归 / 资源上限 | 3 | 声明能力与实际能力脱节 |
| 四 | 并发 / 状态一致性 | 3 | 单进程假设 vs 多进程现实 |
| 五 | 契约 / 语义一致性 | 2 | 同一算子两套类型转换策略 |

**产出曲线：5 → 7 → 3 → 3 → 2，是健康信号。** 前两轮扫的是"看得见的 bug"，后三轮逐步进入"需要横向对比才能发现"的深水区，命中率下降符合预期（第五轮 25 条机器命中里人工确认 2 条，约 8%，但这两条扫描器一条都没报出来）。

**一条贯穿五轮的主线**：AutoForge 的工程质量高，防护意识强，但**同样的标准没有在所有子系统里贯彻**——

- `af_store`/`af_catalog`/`af_persist` 用 `FileLock`，`af_pending` 只用进程内锁（第四轮）
- 表达式求值有 `MAX_EXPR_DEPTH`，图遍历没有（第三轮）
- `expect` 的 `gt`/`lt` 有数值转换，`eq`/`ne` 没有（本轮）

这不是能力问题，是**约定没固化**。建议把这几条提升为全局约定，用你们已有的 `check_imports.py` + `.gates.toml` 机制做成机器可校验的门禁——机制现成，只差把规则写进去。

---

## 八、已沉淀的复用资产

| 资产 | 位置 |
|---|---|
| 误报修正手册（11 条 + 本轮 4 条 = 15 条） | `audit-env/scripts/lessons-round2.md` |
| 深度缺陷扫描器（E01–E13） | `audit-env/scripts/scan_round3.py` |
| 并发缺陷扫描器（F01–F05） | `audit-env/scripts/scan_round4.py` |
| 契约扫描器（G01–G03） | `audit-env/scripts/scan_round5.py` |
| 稳定性审计主流程（`STEP=` 分段） | `audit-env/scripts/40-stability-audit.sh` |
| 环境自检（23 项全通过） | `audit-env/scripts/99-verify.sh` |

### 本轮新增 lessons（12–15）

- **12** 契约验证要双向比对，单方向会漏
- **13** 语义一致性：同一组算子里的类型转换策略分裂
- **14** 异常映射覆盖要算调用图传递可达，直接匹配全漏
- **15** 无 fastapi 环境时，用"无全局 handler + 调用图 + 无 try/except"三步静态判定 500
