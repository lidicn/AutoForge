# AutoForge 第三轮审计 · 稳定性与功能性缺陷清单

> 运行 ID：`af-audit-run-3`　工具：cloudflare/security-audit-skill（六阶段）
> 目标：`lidicn/AutoForge`（zip 快照，mtime `2026-09-29T22:43`）
> 校验器：`coverage-ledger` PASS（19 单元）｜`findings` PASS（3 记录）
> 实测环境：Python 3.10.12｜**测试基线 1277 passed / 51 skipped / 0 failed**
> **本轮换面**：聚焦**表达式求值边界 + 时间处理**（前两轮均未审此面）

---

## 一句话结论

**找到 1 个硬缺陷：`min`/`max` 参数类型混用会抛 `TypeError`，绕过执行器的 soft-fail 降级。**

外加 1 条 hardening note（`floor`/`ceil` 对 `inf`/`nan`），以及**第一轮 P0-2 连续两轮未修复**的回归确认。

---

## 缺陷 1（中高）· `min`/`max` 混类型抛 `TypeError`，绕过 soft-fail

**位置**：`src/autoforge/af_ir/expr.py:181`（`FUNCTIONS` 表的 `min`/`max`）
**消费侧**：`src/autoforge/af_executor.py:402`

### 端到端实测

构造一个完全合法的表达式：`min(sensor_temp, 20) > 10`

其中 `sensor_temp` 是 **HA 实体状态** —— 在 HA 里，实体状态**原生就是字符串**（`"21.5"`、`"on"`、`"unavailable"`），只有当 IR 里显式声明 `type: numeric` 才会被转成 float。

```
[A] 实体状态为字符串（HA 真实形态，未声明 type=numeric）
    state='21.5'         -> ❌ TypeError: '<' not supported between 'int' and 'str'
    state='on'           -> ❌ TypeError: '<' not supported between 'int' and 'str'
    state='unavailable'  -> ❌ TypeError: '<' not supported between 'int' and 'str'

[B] 复刻 executor if 分支捕获列表 (UnknownEntity, ExprError, KeyError, ValueError)
    state='21.5'         -> ❌ 穿透！TypeError
    state='on'           -> ❌ 穿透！TypeError

[C] 对照：实体状态为数字（已声明 type=numeric）
    state=21.5           -> True ✅ 正常
    state=18.0           -> True ✅ 正常

[D] max 同路径
    max state='21.5'     -> ❌ TypeError（'>' not supported）
    max state=21.5       -> True ✅
```

### 三道防线，前两道都放行

| 防线 | 表现 |
|---|---|
| ① 静态闸 `check_expr` | **放行** —— 编译期无法预知运行时实体状态是 str 还是 float |
| ② 表达式求值 | 抛 `TypeError`（未受控，非 `ExprError`） |
| ③ 执行器捕获列表 | `(UnknownEntity, ExprError, KeyError, ValueError)` —— **不含 `TypeError`** |

结果：`_soft_fail` **不被调用**，实例走了未预期的异常路径。

### 为什么不至于崩进程

外层 `af_runtime.py:104`、`af_scheduler.py:271`、`af_executor.py:192` 都有 `except Exception` 兜底，所以进程不会挂。

但**soft-fail 语义被跳过**了 —— 那条路径本应做的重试、降级、审计记录全部不会发生。这正是"设计好的降级机制在关键时刻不生效"的一类缺陷。

### 修复

两处任选其一（建议都做）：

```python
# 方案 A（表达式层，根治）：min/max 做受控类型校验
def _fn_min(args):
    values = _as_num_list(args)          # 统一转数值，失败抛 ExprError
    if not values: raise ExprError("min() 参数为空")
    return min(values)
# 关键：不允许 str 与 number 混用，混用抛 ExprError 而非让 Python 抛 TypeError

# 方案 B（执行器层，兜底）：把 TypeError 加进捕获列表
except (UnknownEntity, ExprError, KeyError, ValueError, TypeError) as exc:
    return self._soft_fail(...)
```

方案 A 更根治（保留 `TypeError` 通道给真正的编程错误），方案 B 是防御性兜底。

### 回归测试

```python
def test_min_max_mixed_types_soft_fails():
    # min(实体状态字符串, 数字常量) → 必须走 soft-fail，不得抛 TypeError
```

---

## Hardening note · `floor`/`ceil` 对 `inf`/`nan`

`expr.py:177-178`：`floor(inf)` / `ceil(nan)` 抛 `ValueError: cannot convert float NaN to integer`。

**但该 `ValueError` 在 executor 捕获列表内**，且可达性受限 —— `_as_num` 拒绝所有字符串（含 `'inf'`/`'nan'`），所以 `inf`/`nan` 无法经 HA 状态字符串进入，只在状态已被上游转为 float 时才可能。

**不构成实际失效路径**，记为 hardening note：建议 `int()` 前加 `math.isfinite()` 检查。

---

## 回归确认：第一轮 P0-2 连续两轮未修复

`af_scheduler.py:343` 仍是 `lease.confirm(instance.id)`，`Instance` 只有 `instance_id`。

第二轮已确认未修复，第三轮**依然未修复**。这是 `--persist-dir` 下时间触发的必崩点，一个字符级的改动。

---

## 本轮证伪（诚实记录）

| 候选 | 证伪依据 |
|---|---|
| `af_conflict.py:98` naive datetime | `SystemTimeSource`（L94-98）的 `now()` 确实返回 naive，但**全仓检索确认该类无实例化、无外部 import**（所有 `SystemTimeSource` 引用都来自 `af_time`），属死代码。`ConflictArbiter` 内部只用 `float(clock.monotonic())`，安全。**实测 naive/aware 混用确实会 TypeError，但当前无调用路径** |
| `clamp(lo > hi)` 返回 hi | 与 Python `min(max())` 语义一致，非缺陷 |
| `time_hour('2026-01-01')` 返回 0 | 纯日期即午夜，合理 |
| 其余 21 个白名单函数边界 | fuzz 全部受控（抛 `ExprError` 或返回确定值） |

**关于 23 个白名单函数的整体判断**：除 `min`/`max` 混类型与 `floor`/`ceil` 的 `inf`/`nan` 外，**全部受控**。第一轮"表达式沙箱守住了"的判断在本轮更细粒度的 fuzz 下基本仍成立 —— `max_depth=32`、`max_nodes=256`、`eval`/`exec`/`__import__` 全拒，这些硬约束是有效的。

---

## 三轮累积视图

| 轮次 | 面 | 核心发现 |
|---|---|---|
| 一 | 全量 | P0-1 homesdk 未声明（装不上）、P0-2 `instance.id`、P1-4 canary 序列化 |
| 二 | 生命周期/资源管理 | SAFE HALT 分支 `logger` 未定义 → tick 线程静默死亡 |
| 三 | 表达式求值/时间 | `min`/`max` 混类型 `TypeError` 绕过 soft-fail |

**跨三轮未变的两条**：
1. `instance.id`（P0-2）—— 三轮都确认未修复
2. 缺静态检查门禁 —— 第一轮的 `homesdk` 未声明、第二轮的 `logger` 未定义，**都是一行 linter 就能拦住的**

---

## 未覆盖声明

19 个单元中 **14 个 `out_of_scope` 未审**：HTTP 鉴权、MCP 授权码、`af_store` 原子写、`af_persist` 租约与崩溃恢复、`af_bus` 去重熔断、`af_instance` 终态回收、`af_orchestrator`、`af_executor` canary、CLI 输入、`af_catalog`、`af_evo`、`af_service` 会话、`af_scanner`、`af_vhass` 仿真。

**三轮合计仍未覆盖的面远多于已覆盖的面。**
