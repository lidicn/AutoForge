# AutoForge 第三轮审计报告：深度缺陷（递归 / 资源 / 降级）

> 审计对象：`https://github.com/lidicn/AutoForge`（main 分支快照）
> 审计范围：`src/autoforge/` 内核（98 个 Python 源文件 / 77,949 行）
> 本轮重点：**递归深度、资源上限、状态一致性与静默降级**
> 判定标准：严格档 —— 必须给出**实测复现的阈值与声明上限的对比**，疑似项单列排除
> 报告日期：2026-10-06

---

## 一、执行摘要

第三轮把靶子推进到更深的一层：不看单点 bug，看**系统的声明能力与实际能力之间的落差**。

**结论：找到 1 组 Critical 缺陷（3 处同源），核心问题是——IR 允许 5000 节点的图，但静态扫描器在约 950 节点的链上就崩了。**

| 编号 | 级别 | 一句话 | 位置 |
|---|---|---|---|
| **R3-01** | 🔴 Critical | **图遍历递归无深度防护：合法大图（≳950 节点链）让静态扫描器崩溃，无法归档** | `af_scanner.py:1064` |
| **R3-02** | 🟠 High | 跨自动化依赖环检测同源崩溃 | `af_scanner.py:1091` |
| **R3-03** | 🟠 High | 自治修复环 `fix_cycle` 同源崩溃 | `af_orchestrator.py:1670` |

### 这组缺陷的形状

不是"代码写错了"，而是**两套防护标准不一致**：

| 子系统 | 资源防护 | 声明上限 |
|---|---|---|
| 表达式求值 | ✅ `MAX_EXPR_DEPTH=32`、`MAX_EXPR_NODES=256`（`af_ir/expr.py:50/52`） | 深度 32 |
| **图遍历** | ❌ **无** | **节点 5000**（`af_ir/models.py:640`） |

表达式侧有明确的 DoS 防护（源码注释里甚至写了"资源上限（防 DoS）"），图遍历侧完全没有——而图遍历恰恰是**节点规模可以合法增长到 5000** 的那一侧。

**结果**：一个通过 Schema 校验、无环、无错误的 1000 节点自动化链，会让扫描器抛 `RecursionError`，用户拿到 HTTP 500，图无法归档。

### 一个值得肯定的点

这个崩溃**没有被静默吞掉**——主链路 `save_graph`（`af_service.py:399`）、`submit_pending`（`af_service.py:483`）、真机预检（`af_service.py:1717`）都没有 `try` 包裹 `scan()`，`RecursionError` 直接冒泡，`af_api.py:685` 的 `_svc` 只捕获 `ServiceError` / `IRValidationError`，兜不住它 → **HTTP 500**。

所以这是**可用性破坏（DoS），不是安全闸绕过**。安全闸没有被架空——这点比"静默放行"要好得多，值得记下来。

---

## 二、工作流迭代：这一轮改了什么

第二轮踩过的坑已沉淀到 `scripts/lessons-round2.md`，第三轮在它的基础上继续迭代。

### 新增

| 资产 | 作用 |
|---|---|
| `scripts/scan_round3.py` | E01–E13 共 12 类深度缺陷扫描器（作用域感知遍历） |
| `lessons-round2.md` 第 4–6 条 | 第三轮新增的三条误报修正与 PoC 标准做法 |

### 三个迭代点

**1. 新增深度缺陷检测层（E01–E13）**

第二轮的 D01–D12 覆盖异常吞咽与资源管理；第三轮补上更深的维度：

`E01` 有损 JSON 往返 · `E03` 索引/除零 · `E04` 手工锁 · `E05` **递归无深度上限** · `E10` 状态直写 · `E11` 默认参数调用 · `E12` 静默降级 · `E13` 可变默认

初版扫描 98 个文件产出 **765 条**。

**2. 移除 E13 误报族：765 → 620 条**

E13 把 `d.get(k, [])` 报成"可变默认共享对象"，产出 **145 条，经核实全部误报**。

原因：Python 里 `.get(k, [])` 的字面量在**调用点**求值，每次都是新对象，不跨调用共享。这与 B008（`def f(x=[])`，默认值在**函数定义时**求值一次、跨调用共享）是**完全不同的两件事**。已从扫描器中删除该检测项。

**3. E05 递归缺陷必须区分"有防护"与"裸递归"**

E05 报 31 处递归，但分两类，只有一类真实：

| 类别 | 防护 | 结论 |
|---|---|---|
| 表达式求值递归（`expr.py` `_walk` / `_check_expr` / `_operand_value` 等约 28 处） | 有 `MAX_EXPR_DEPTH=32`、`MAX_EXPR_NODES=256` | ❌ 误报，不可达深度 1000 |
| **图遍历递归**（`af_scanner.py:1064/1091`、`af_orchestrator.py:1670`） | **无** | ✅ **真实，实测崩溃** |

判据已写入 lessons：**报递归缺陷前先查该子系统是否已有全局深度/节点数上限。**

### 本轮扫描产出

| 阶段 | 命中 |
|---|---|
| 初版 E01–E13 | 765 条 |
| 移除 E13 误报族后 | **620 条**（E11 162 / E03_index 161 / E03_div 157 / E12 67 / E05 31 / E10 30 / E01 8 / E04 4） |
| **人工核实后确认** | **3 条（1 组同源）** |

命中率约 0.5%。这是三轮里最低的一轮——检测越深，噪音越多，人工分诊的价值越大。

---

## 三、确认缺陷

### 🔴 R3-01　图遍历递归无深度防护 → 合法大图无法归档

**位置**：`src/autoforge/af_scanner.py:1064`（`_find_cycles` 内的 `dfs`）

```python
def _find_cycles(auto: Automation) -> list[list[str]]:
    """找图里的环（DFS 回边，三色标记）。"""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in auto.nodes}
    ...
    def dfs(node: str) -> None:
        color[node] = GRAY
        path.append(node)
        for edge in auto.outgoing(node_id):      # line 1067
            ...
            dfs(nxt)                              # line 1074 ← 裸递归，无深度上限
```

**实测复现**（用真实 `load_graph()` + `StaticScanner().scan()`，构造通过 Schema 校验的合法无环链）：

```
N=  100  扫描完成 errors=0
N=  500  扫描完成 errors=0
N=  900  扫描完成 errors=0
N=  950  扫描完成 errors=0
N= 1000  *** RecursionError 扫描器崩溃 ***
N= 1200  *** RecursionError 扫描器崩溃 ***
N= 5000  *** RecursionError 扫描器崩溃 ***
```

**崩溃调用栈**（Python 默认递归限制 1000）：

```
File "af_scanner.py", line 1074, in dfs
    dfs(nxt)
File "af_scanner.py", line 1074, in dfs
    dfs(nxt)
[Previous line repeated 989 more times]
RecursionError: maximum recursion depth exceeded in comparison
```

**量化落差**：

| 指标 | 值 | 出处 |
|---|---|---|
| IR 声明节点上限 | **5000** | `af_ir/models.py:640` `max_nodes_per_graph=5000` |
| 图遍历实际崩溃阈值 | **约 950** | 实测（950 通过 / 1000 崩） |
| 表达式求值深度上限 | 32 | `af_ir/expr.py:50` `MAX_EXPR_DEPTH` |
| 表达式求值节点上限 | 256 | `af_ir/expr.py:52` `MAX_EXPR_NODES` |

**IR 允许的规模是扫描器能处理的 5 倍以上。**

**触发条件**：任意一条自动化含 ≳950 节点的**链式**结构（`on → do → do → … → do`，无环、无错误、完全合法）。

**后果**：`RecursionError` 冒泡 → HTTP 500 → **图无法归档、无法提交审批**。用户看到的是服务端错误，不知道是规模问题。

**为什么不是"理论问题"**：链式自动化是这个平台的正常使用形态——一个触发器串起几十上百个动作（全屋场景、批量设备操作）是完全合理的用法。950 不是遥不可及的数字。

**修复建议**（三选一，推荐第一个）：
1. **改迭代 DFS**：用显式栈替代递归，彻底消掉深度限制（改动约 15 行，收益最大）。
2. 加深度上限参数（如 `max_depth=1000`），超限记 `unmodeled` 诊断而非崩溃。
3. 在 Schema 层把 `max_nodes_per_graph` 降到扫描器能承受的范围——**不推荐**，这是削能力迁就实现。

> 注意：Python 递归限制可以用 `sys.setrecursionlimit()` 抬高，但**不建议**——栈溢出会直接 segfault，比抛异常更糟。

---

### 🟠 R3-02　跨自动化依赖环检测同源崩溃

**位置**：`src/autoforge/af_scanner.py:1091`（`_cycles_in` 内的 `dfs`），调用点 `af_scanner.py:980`

```python
def _cycles_in(deps: Mapping[str, set[str]]) -> list[list[str]]:
    """通用有向图环检测（返回环路径列表）。"""
    ...
    def dfs(node: str) -> None:
        color[node] = GRAY
        path.append(node)
        for nxt in sorted(deps.get(node, ())):
            ...
            elif color[nxt] == WHITE:
                dfs(nxt)                          # ← 同形裸递归
```

**与 R3-01 完全同形**，只是数据从 `Automation` 换成跨自动化依赖图 `full_deps`。依赖链长度同样可能超过 950。

**修复**：与 R3-01 统一改为迭代 DFS。

---

### 🟠 R3-03　自治修复环 `fix_cycle` 同源崩溃

**位置**：`src/autoforge/af_orchestrator.py:1670`（`fix_cycle` 内的 `dfs`）

```python
def fix_cycle(ctx: FixContext) -> FixOutcome:
    ...
    def dfs(u):
        stack.append(u)
        onstack.add(u)
        for e in graph.get(u, []):
            v = e["to"]
            if v in onstack:
                found.append(list(stack[stack.index(v):]) + [v])
                return True
            if v not in onstack and dfs(v):        # ← 同形裸递归
                return True
        stack.pop()
```

**为什么单独列**：这是**自治闭环**（`af_closedloop`）的修复器。闭环的立身之本是"发现问题→自动修复"，如果修复器自己在大图上崩溃，闭环会在最需要它的场景（大型自动化）失效。

而且这里崩溃的后果更微妙：`fix_cycle` 崩了，闭环会把它当成"修复失败"还是"无需修复"取决于上层的异常处理——**建议连同 R3-01 一起改，并确认崩溃不会被记成"已修复"**。

---

## 四、已排除（rejected）

按严格档，以下候选经核实后推翻，列出理由避免后续重复排查：

| 候选 | 数量 | 推翻理由 |
|---|---|---|
| **E13 `dict.get(k, 可变默认)`** | 145 | **全族误报**。`.get(k, [])` 字面量在调用点求值，每次新建对象，不跨调用共享；与 `def f(x=[])` 完全不同。已从扫描器删除 |
| **E03 除零 / 索引** | 318 | `/` 多为 `Path` 拼接（`af_ir/models.py:94` SCHEMA_PATH 等）；`[0]` 前多已有非空检查。**关键核实**：`af_ir/expr.py:166` 的 `avg()` **已有空数组防护**（`if not values: raise ExprError`），非缺陷 |
| **E05 表达式递归** | 28 | 有 `MAX_EXPR_DEPTH=32` / `MAX_EXPR_NODES=256` 防护，不可达深度 1000 |
| **E01 有损 JSON 往返** | 8 | `af_instance.py:161` 已由 P2-3 修复（移除 `default=str` 改 fail-fast）。其余 7 处经核实 `bind_ir`（`af_service.py:2132`）等，**实际输入恒为 JSON 原生类型**，无损风险 |
| **InstanceTimer / AskSession 持久化** | — | `due_at` 为 monotonic，但 `to_dict` 只导出 `remaining_s` 相对量；`AskSession.created_at` 仅内存排序用，sidecar 不落盘。**持久化均安全** |
| **af_auth.py:74 令牌过期判定** | — | 用 `time.time()` 墙钟判定（跨进程正确），非 monotonic 误用 |
| **E11 默认参数调用** | 162 | B008 同源族，与第二轮 36 条重叠，需人工逐条看，多为 `field(default_factory=...)` 之外的无害形式 |
| **E10 状态直写** | 30 | 多为状态机内部合法赋值（熔断器 `af_conflict.py:454/475/499`、进化提案 `af_evo.py:1443-1473`），非绕过通知机制 |
| **E12 静默降级** | 67 | 与第二轮 BUG-04 已报位置重叠（`af_apply.py:296/302` 等），本轮不做重复计入 |

---

## 五、修复优先级

| 顺序 | 缺陷 | 工作量 | 说明 |
|---|---|---|---|
| **P0** | R3-01 图遍历改迭代 DFS | 约 15 行 × 1 处 | 彻底消深度限制，收益最大 |
| **P1** | R3-02 `_cycles_in` 同改 | 约 15 行 | 与 R3-01 共用同一套迭代实现即可 |
| **P1** | R3-03 `fix_cycle` 同改 + 确认崩溃不被记成"已修复" | 约 20 行 | 闭环失效比单点崩溃更隐蔽 |
| **P2** | 给图遍历加规模上限诊断（如超 5000 节点提示） | 小 | 与 Schema 上限对齐，让用户知道边界在哪 |

**建议修法**：写一个 `_iter_dfs(adj, on_back_edge)` 工具函数，三处共用。既消掉递归，也让三处行为一致（当前三处的环记录格式各不相同）。

---

## 六、三轮审计的横向观察

| 轮次 | 靶子 | 确认缺陷 | 主因 |
|---|---|---|---|
| 第一轮 | 安全 / 架构 | 5 | 审批链路身份语义不严密 |
| 第二轮 | 稳定性 / 功能性 | 7 | 异常被吞后不留痕 |
| 第三轮 | 递归 / 资源 / 降级 | 3（1 组同源） | **声明能力与实际能力脱节** |

三轮下来有一条清晰的主线：**AutoForge 的"防御意识"很强，但分布不均。**

- CI 门禁做得很扎实（团队甚至自己写过"门永远不可能红"的教训）
- 表达式求值有明确的 DoS 防护（`MAX_EXPR_DEPTH` / `MAX_EXPR_NODES`）
- 真机写入有 fail-closed 快照、金丝雀回滚、单写者租约

但同样的防护标准**没有延伸到图遍历**（本轮 R3-01/02/03）和**运行时异常告知**（第二轮 BUG-02/03/04）。

**建议**：把"资源上限"和"失败必须留痕"这两条从它们各自的子系统里**提升为全局约定**，写进 `.gates.toml`，并用 `check_imports.py` 那种方式做成机器可校验的门禁。你们已经有这个机制了，只是还没用在这两件事上。

---

## 七、已沉淀的复用资产

| 资产 | 位置 | 内容 |
|---|---|---|
| 误报修正手册 | `audit-env/scripts/lessons-round2.md` | 6 条：作用域感知遍历、宿主限定、必崩 vs 条件崩、`.get()` 非默认参数陷阱、递归防护分类、二分定位阈值 |
| 深度缺陷扫描器 | `audit-env/scripts/scan_round3.py` | E01–E13（E13 已删除），输出 `reports/round3-defects.json` |
| 环境自检 | `audit-env/scripts/99-verify.sh` | 20 项全通过 |
| 稳定性审计流程 | `audit-env/scripts/40-stability-audit.sh` | 支持 `STEP=` 分段执行 |
