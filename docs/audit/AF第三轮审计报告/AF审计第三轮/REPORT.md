# AutoForge 第三轮审计报告（稳定性 / 功能性）

> 校验器：`coverage-ledger` PASS（19 单元）｜`findings` PASS（3 记录）｜状态 `complete`
> 测试基线：1277 passed / 51 skipped / 0 failed

## 本轮换面

前两轮分别审了「生命周期/资源管理」和「未定义名/视图函数」。
**本轮聚焦表达式求值与时间处理** —— 这是 Agent 可控输入进入内核的主入口，前两轮都只做了整体判断，没做细粒度 fuzz。

## 核心发现

**`min`/`max` 参数类型混用抛 `TypeError`，绕过执行器的 soft-fail 降级。**

表达式 `min(sensor_temp, 20) > 10`：当 `sensor_temp` 是 HA 实体状态字符串（原生形态）时，求值抛 `TypeError: '<' not supported between 'int' and 'str'`。

三道防线：静态闸放行（编译期无法预知运行时类型）→ 求值抛 TypeError（非受控）→ executor 捕获列表 `(UnknownEntity, ExprError, KeyError, ValueError)` **不含 TypeError** → `_soft_fail` 被跳过。

对照组：同为数字时正常返回 `True`，证明是类型混用触发。

外层有 `except Exception` 兜底，不崩进程，但**设计好的降级机制在关键时刻不生效**。

## 对 23 个白名单函数的整体判断

细粒度 fuzz 后：**除 `min`/`max` 混类型与 `floor`/`ceil` 的 `inf`/`nan` 外，全部受控**。

硬约束有效：`MAX_EXPR_DEPTH=32`、`MAX_EXPR_NODES=256`、`eval`/`exec`/`__import__` 全拒、`_as_num` 拒绝所有字符串隐式转换。

**结论：表达式沙箱的骨架是扎实的，漏洞在类型校验的细节层。**

## 回归确认

`af_scheduler.py:343` `lease.confirm(instance.id)` —— **连续两轮确认未修复**。

## 诚实记录：本轮证伪 1 条

`af_conflict.py:98` 的 `SystemTimeSource.now()` 确实返回 naive datetime，我一度判定为时区缺陷。但全仓检索确认：**该类无实例化、无外部 import**（所有 `SystemTimeSource` 引用都来自 `af_time`），是死代码。`ConflictArbiter` 只用 `float(clock.monotonic())`。

实测 naive/aware 混用会 `TypeError` 属实，但**当前无调用路径能触发**。

## 三轮跨轮观察

1. `instance.id`（P0-2）三轮都未修复
2. **第一轮的 `homesdk` 未声明、第二轮的 `logger` 未定义**，都是一行 linter 能拦住的 —— 门禁缺位是本项目的系统性短板

## 未覆盖

19 单元中 14 个 `out_of_scope`。三轮合计未覆盖面仍远多于已覆盖面。
