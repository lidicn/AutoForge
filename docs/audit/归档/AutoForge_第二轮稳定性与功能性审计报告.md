# AutoForge 第二轮审计报告：稳定性与功能性缺陷

> 审计对象：`https://github.com/lidicn/AutoForge`（main 分支快照）
> 审计范围：`src/autoforge/` 内核（98 个 Python 源文件 / 77,949 行）
> 本轮重点：**影响稳定性与功能性的 bug**（第一轮已覆盖安全与架构，本轮不重复）
> 判定标准：严格档 —— 必须指出**确定的触发条件与可观察后果**，疑似项单列排除
> 报告日期：2026-10-06

---

## 一、执行摘要

第二轮换了靶子：不看安全，只看**会不会崩、会不会静默失效、会不会少干活**。

**结论：找到 7 个真实缺陷，其中 1 个是"必崩"级别的确定性 bug。**

| 编号 | 级别 | 一句话 | 位置 |
|---|---|---|---|
| **BUG-01** | 🔴 Critical | **`--dry-live` 模式下每次动作下发必抛 NameError** | `af_cli.py:317` |
| **BUG-02** | 🟠 High | canary 观察窗口配置解析失败 → 金察机制静默降级 | `af_executor.py:531-535` |
| **BUG-03** | 🟠 High | 快照失败 → 自动化**静默不触发**，无日志 | `af_scheduler.py:281/313` |
| **BUG-04** | 🟠 High | 遍历时异常 `continue` → **静默漏项**（列表消失/批量漏操作/冲突漏检） | `af_service.py:268/295/328`、`af_apply.py:299/304` |
| **BUG-05** | 🟡 Medium | `_PRUNE_COUNTER` 无锁 read-modify-write 竞态 | `af_telemetry.py:46/169-171` |
| **BUG-06** | 🟡 Medium | canary 漂移检查异常被吞 → 记为 `verified`（假证据） | `af_executor.py:245-246` |
| **BUG-07** | 🔵 Low | `Config` 类型注解未定义，`get_type_hints()` 会崩 | `af_live.py:229` |

### 一个贯穿性问题

7 条里有 **4 条是同一个病根：异常被吞掉后不做任何告知**。不是崩溃，而是"悄悄少做一点"——列表少几条、批量操作少几个、观察窗口不生效、漂移检查失败记成成功。用户看到的是"好像没出问题"，实际是功能已经降级。

这类缺陷比崩溃更危险：**崩溃会被发现，静默降级不会。**

有意思的是，AutoForge 的 CI 注释里自己就写过"门永远不可能红"的假绿教训——说明团队对这个形状有认识，但那套警觉还没延伸到运行时代码里。

---

## 二、工作流迭代：这一轮改了什么

第一轮的工具链偏安全扫描，第二轮针对"稳定性/功能性"重做了检测层。所有脚本在 `/data/workspace/audit-env/scripts/`：

### 新增

| 脚本 | 作用 |
|---|---|
| `40-stability-audit.sh` | 稳定性审计主流程，支持 `STEP=ruff/scan/gate/summary` **分段执行**（沙箱资源受限，一次性跑完会崩） |
| `scan_defects.py` | AST 缺陷检测，D01–D12 共 12 类检测项 |
| `scan_returns.py` | 作用域感知的"混合返回"检测 |

### 三个迭代点（都是踩坑后修正的）

**1. 分段执行替代一次性跑**
第一轮 semgrep 横扫反复打爆沙箱（`semgrep-core` 245MB）。本轮主流程从设计上就按 `STEP` 拆分，每个 STEP 独立可跑、断点续跑。

**2. D07 误报修正：2626 条 → 627 条**
初版 `scan_defects.py` 把所有 `.get(...)` 都认成 HTTP 调用，D07「HTTP 无超时」首轮报 **2004 条**，全是 `dict.get` 误报。修正为只认 `urlopen/urllib/requests/httpx/session/self/opener/_opener` 上的调用后，降到 **5 条**。

**3. 作用域感知替代 `ast.walk`：5 条 → 0 条**
初版检测"混合返回"用 `ast.walk(node)`，会穿透嵌套函数，把内层闭包的提前退出误报成外层缺陷。实测 5 条候选**全是误报**：

- `graphops.py:113` 是内层 `add()` 的 `return`
- `af_conflict_runtime.py:141` 是内层 `_walk()` 的 `return`
- `af_live.py:373` 是内层 `_write_asks()` 的 `return`

改为"遇到新函数/类/Lambda 即停止下钻"后，**归零**。这个修正本身值得记下来——AST 分析里 `ast.walk` 穿透作用域是最常见的误报来源。

### 本轮扫描产出

| 阶段 | 产出 | 命中 |
|---|---|---|
| ruff 确定性 bug 规则 | `round2-20261006-124759/ruff-defects.json` | 81 条（B008 36 / S110 27 / BLE001 等） |
| AST 缺陷扫描 D01–D12 | `round2-20261006-124826/defects.json` | 627 条 |
| 门禁违规（critical_globs 内宽泛 except） | `round2-20261006-124835/gate-violations.json` | 14 处 |
| 静默降级候选（D13） | `reports/silent-degrade.json` | 89 条（关键路径 44） |

**627 条 AST 命中里，人工核实后只留下 7 条真缺陷** —— 命中率约 1%，这正是为什么要坚持"无 PoC 不入报告"。

---

## 三、确认缺陷

### 🔴 BUG-01　`--dry-live` 模式下每次动作下发必抛 NameError

**位置**：`src/autoforge/af_cli.py:317`

```python
if dry_live:
    def _on_dry(action, params):
        logging.getLogger(__name__).debug("[DRY-LIVE 意图] %s %s", action, params)
    adapter.on_dry_run = _on_dry
```

**为什么必崩**：`logging` 在 `af_cli.py` 里**没有模块级导入**。全文件只有两处 `import logging`，都在**别的函数内部**（第 92 行、第 109 行），属局部作用域。`_on_dry` 是第三个独立函数，其作用域内没有 `logging` → 运行时 `NameError: name 'logging' is not defined`。

用 AST 核对全文件导入表已确认：**无模块级 `import logging`**。

**调用链无保护**（已逐行核实 `af_adapters/ha.py:254-260`）：

```python
if self.dry_run:
    self.intents.append((action, dict(params)))
    logging.getLogger(__name__).debug("[HAAdapter.dry_run] %s %s", action, dict(params))
    if self.on_dry_run is not None:
        self.on_dry_run(action, params)      # ← 裸调，无 try
    return CallResult.ok({...})
```

`self.on_dry_run(action, params)` **没有任何 try 包裹**，NameError 直接冒泡。

**触发条件**：`forge ... --dry-live` 且任一动作下发（即 `HAAdapter.call()` 走 `dry_run=True` 分支）。

**后果**：dry-live 是"真机演练"通道，本该是最需要可靠性的路径，结果每次下发都抛异常。若调用方未捕获，命令直接失败退出。

**修复**：在 `af_cli.py` 模块级加 `import logging`（或在 `_on_dry` 内局部导入）。一行改动。

> 顺带：`af_adapters/ha.py:258` 的裸调建议也加保护——回调是外部注入的，不该让注入方的 bug 拖垮下发链路。

---

### 🟠 BUG-02　canary 观察窗口配置解析失败 → 静默降级

**位置**：`src/autoforge/af_executor.py:531-535`

```python
canary_duration = None
if isinstance(canary, dict):
    dur_str = canary.get("duration")
    if dur_str:
        try:
            canary_duration = parse_duration(dur_str)
        except (ValueError, TypeError):
            pass                              # ← 静默
if canary_duration and canary_duration > 0:
    instance.ctx.context["pending_canary"] = {...}
    self.instances.suspend(instance, node.id, canary_duration, kind="canary_observe")
    return None
# 无 duration → 立即检查漂移（原行为）
```

**问题**：`duration` 字符串格式非法时（如 `"30x"`、`"abc"`），`parse_duration` 抛异常被 `pass` 吞掉，`canary_duration` 保持 `None` → 走"无 duration"分支 → **挂起观察窗口整体消失**，变成立即判定。

**后果**：用户明确配置了"下发后观察 N 秒再确认漂移"，实际变成"下发后立刻判定"。金丝雀的意义（给状态收敛留时间）被架空，**且无任何日志或告警告知用户配置未生效**。

**为什么严重**：canary 是不可逆动作的回滚保障。配置写错却不报错，用户会以为保护生效了。

**修复**：解析失败应记 `unmodeled` 证据 + 打 WARNING 日志，明确告知"duration 无法解析，已退化为立即判定"。

---

### 🟠 BUG-03　快照失败 → 自动化静默不触发

**位置**：`src/autoforge/af_scheduler.py:281`、`af_scheduler.py:313`

```python
try:
    snapshot = ...
except ...:
    return False        # ← 既不触发，也不记录
```

**后果**：快照获取失败时，该自动化**永远不触发**，且没有任何日志、指标或可观测信号。用户看到的现象是"自动化配好了但不干活"，且无从排查。

**为什么值得单列**：`af_scheduler.py` 被项目自己的 `.gates.toml` 列入 `critical_globs`，注释说明是"去抖那条 NameError 就在 scheduler"——这个模块有过静默失败的历史。

**修复**：失败路径至少打一条日志并计数；连续失败应上报健康状态。

---

### 🟠 BUG-04　遍历时异常 `continue` → 静默漏项（缺陷族）

这是一族同形缺陷，分布在 5 处：

| 位置 | 所在函数 | 后果 |
|---|---|---|
| `af_service.py:268-270` | `list_graphs` | 损坏的自动化从列表里**凭空消失**，用户以为它不存在 |
| `af_service.py:295-300` | `graphs_by_tag` | 按 tag 查不到损坏的图 |
| `af_service.py:328-333` | `enable_by_tag` | **批量启用时损坏项被跳过**，实际生效范围小于预期且无告知 |
| `af_apply.py:299` | 冲突检测 | 损坏的历史记录不参与检测 → **冲突漏检** |
| `af_apply.py:304` | 冲突检测 | 同上（内层 `Automation.from_dict` 失败） |

统一形态：

```python
for name in names:
    try:
        graph = store.load(name, version)
    except (FileNotFoundError, IRValidationError, OSError):
        continue          # ← 静默跳过，不计数、不记录、不告知
```

**为什么比看起来严重**：单独看每处都像合理的防御性写法。但合起来是**"用户看到的集合永远比实际小，且差值不可知"**。批量操作（按 tag 启用/禁用）尤其危险——用户以为对 20 个自动化生效了，实际只有 18 个，剩下 2 个静默跳过。

**修复**：跳过时计数，并在返回值里带上 `skipped: N` + `skip_reasons: [...]`。批量操作必须在结果中体现真实生效范围。

> 排查时发现的同类候选还有 `af_pending.py:152/186`（损坏待批文件跳过）、`af_persist.py:230`、`af_conflict_runtime.py:114/120/255/266`、`af_conflict_audit.py:145/172`，建议按同一模式统一处理。

---

### 🟡 BUG-05　`_PRUNE_COUNTER` 无锁竞态

**位置**：`src/autoforge/af_telemetry.py:46`（定义）、`169-171`（读写）

```python
_PRUNE_COUNTER = 0          # 模块级可变全局
...
# 第 169-171 行：read-modify-write，无锁
_PRUNE_COUNTER = _PRUNE_COUNTER + 1
if _PRUNE_COUNTER >= _PRUNE_EVERY:      # _PRUNE_EVERY = 500
    ...
```

**风险**：`get` → `+1` → `set` 三步非原子，多线程下丢失更新。

**实际影响**：计数器只用于触发周期性清理（`_PRUNE_EVERY=500`），丢失更新最坏结果是**清理推迟**，不会导致数据错误或崩溃。属**低危害竞态**。

**对照**：同仓其他模块级可变全局——`_CONFIGS`（`af_config.py:156`）、`_ENTITY_HEALTH_CACHE`（`af_service.py:672`）、`_SESSIONS`（`af_service.py:1359`）——**都配了对应的 Lock**（`_CONFIGS_LOCK` / `_ENTITY_HEALTH_LOCK` / `_SESSIONS_LOCK`）。唯独 `_PRUNE_COUNTER` 没有。说明这是**遗漏**而非有意设计。

**修复**：改用 `itertools.count()` 或加 `threading.Lock`，与其他全局保持一致。

---

### 🟡 BUG-06　canary 漂移检查异常被吞 → 记为「已验证」

**位置**：`src/autoforge/af_executor.py:245-246`

```python
except Exception:
    logging.getLogger("autoforge.executor").exception(
        "canary 漂移检查失败（已隔离，不阻断流程）")
```

**问题**：漂移检查抛异常时回滚逻辑不执行，而状态被记为 `verified`（真阴性）而非 `failed` / `unmodeled`。**失败被记成成功。**

这是第一轮审计报告里的 MEDIUM-3，本轮从"安全"角度再次确认其在**功能性**上同样成立：它污染的是证据链——后续任何基于 canary 证据的判断（如自愈闭环、审计报表）都会读到假数据。

**修复**：异常应落 `unmodeled` 档，而非静默走 `verified` 路径。

---

### 🔵 BUG-07　`Config` 类型注解未定义

**位置**：`src/autoforge/af_live.py:229`

```python
cfg: "Config | None" = None,
```

`Config` 定义在 `af_config.py:35`，但 `af_live.py` **未导入**它。

**实际影响**：因文件有 `from __future__ import annotations`，注解不求值 → **运行时不崩**。仅当调用 `typing.get_type_hints()`、或被 pydantic / FastAPI 之类的框架做注解求值时才抛 `NameError`。已确认 `start_ticker` / `HALiveClient` 未直接暴露给 FastAPI 路由，当前**风险低**。

**修复**：补 `from .af_config import Config`（可放 `if TYPE_CHECKING:` 下避免运行时循环导入）。

---

## 四、已排除（rejected）

按严格档，以下候选经核实后推翻，列出理由以免后续重复排查：

| 候选 | 数量 | 推翻理由 |
|---|---|---|
| **D07 HTTP 无超时**首轮 2004 条 | 2004 | 初版把 `dict.get()` 全认成 HTTP 调用；修正后仅 5 条真候选 |
| **混合返回** 5 条 | 5 | 全是嵌套函数（`add()` / `_walk()` / `_write_asks()`）的提前退出，非外层缺陷。作用域感知重扫后归零 |
| **B023 闭包延迟绑定** `af_store.py:795` | 1 | `id_map` 在循环外定义，无延迟绑定风险 |
| **F601 重复字典键** `af_actions.py:68`、`af_shadow.py:222` | 2 | 均为良性超集重复，前后值相同 |
| **D03 raise 无 from** 12 条 | 12 | 多为 `typer.Exit` / `HTTPException`，链式异常在此无价值 |
| **D06 subprocess 无超时** 18 条 | 18 | 多为 `adapter.call` / `mcp.call` 等同名方法，非 `subprocess` 模块 |
| **D05 裸 open 无 with** 26 条 | 26 | 均在 try/finally 或已显式 close，无真实泄漏 |
| **`af_live.py:442` daemon 线程** | 1 | `daemon=True` 且由 `af_tick_supervisor.py` 管理生命周期，非失控线程 |
| **`af_instance.py:389` F811 重复导入** | 1 | 函数内 `from .af_state import UnknownEntity`，与模块级重名但属有意的延迟导入（避免循环），安全 |
| **`af_adapters/ha.py` 等 14 处宽泛 except**（critical_globs 命中） | 14 | 逐处核实均为**有意 fail-closed 防御**：`af_scanner.py:232` 跳过非法节点、`ha.py:269` 带 warning 日志、`af_service.py:1315` 防御性 continue。**非缺陷** |

---

## 五、修复优先级

| 顺序 | 缺陷 | 工作量 | 说明 |
|---|---|---|---|
| **P0** | BUG-01 dry-live NameError | 1 行 | 必崩路径，改动最小、收益最大 |
| **P1** | BUG-02 canary duration 静默降级 | 小 | 安全机制失效且无告警 |
| **P1** | BUG-03 快照失败静默不触发 | 小 | 现象是"自动化不干活"，排查成本极高 |
| **P1** | BUG-04 静默漏项族（5 处） | 中 | 建议统一加 `skipped` 计数与原因，一次改掉 |
| **P2** | BUG-06 canary 异常记 verified | 小 | 污染证据链 |
| **P2** | BUG-05 `_PRUNE_COUNTER` 竞态 | 小 | 危害低但属一致性遗漏 |
| **P3** | BUG-07 `Config` 注解 | 极小 | 当前不崩，防御性修复 |

---

## 六、给工程团队的一句话

**BUG-01 今天就能修**（`af_cli.py` 加一行 `import logging`），它能让 `--dry-live` 这条真机演练通道从"必崩"变成可用。

其余 6 条里，有 4 条是同一个形状：**异常被吞掉后不做任何告知**。你们已经把 CI 门禁的假绿问题治掉了，建议把同一套标准延伸到运行时代码——**凡是 `except` 之后改变了系统行为（跳过、降级、记为成功），就必须留下可观测的痕迹**。819 条 ruff 里那 142 条 BLE001 和 27 条 S110，可以借这次机会按"是否改变行为"重新分一遍类。
