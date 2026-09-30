# 交接卡 v1.0.0 — 表达力收口（表达式强化 + `fn` 评估）

## 改动清单（1 重写 + 2 修改 + schema + 测试 + 3 文档）

| 文件 | 变更 |
|---|---|
| `src/autoforge/af_ir/expr.py` | **重写扩展**：① operand 第三形态 `{"fn": <名>, "args": [...]}`（`FUNCTIONS` 白名单 24 个纯函数，四族：math `abs/floor/ceil/round/clamp/min/max/sum/avg`、string `lower/upper/trim/length/contains/starts_with/ends_with/replace`、time `time_hour/time_minute/time_weekday/time_between`、list `first/last`，`length/contains/sum/avg/min/max` 多态共享）；② `MAX_EXPR_DEPTH=32` / `MAX_EXPR_NODES=256` 双硬顶（`_Budget`，evaluate 与 check_expr 同一套）；③ 新增 `check_expr`（编译期校验：形态/未知算子/未知函数/参数个数/上限，fail-closed）与 `call_function`；④ `_walk`/`collect_var_refs` 深入 fn 参数（实体引用/ACL 检查不漏）。时间函数作用于**显式 ISO 时间戳**——"表达式是纯函数、不读时钟"红线不破。 |
| `src/autoforge/af_ir/schema/ir.schema.json` | `operand` oneOf 增加 fn 形态（`additionalProperties:false` 仍锁死，args maxItems=32）。 |
| `src/autoforge/af_scanner.py` | 新诊断 `EXPR_INVALID`（ERROR）：对全部含 expr 节点跑 `check_expr`，未知函数/参数错误/超限编译期即拦。 |
| `tests/unit/test_v1_expr.py` | **新增** 17 项单测（函数正确性 / 嵌套 / 多态 / 类型显式报错 / 未知函数 / 参数个数 / 深度与节点上限 / 引用收集穿透 / schema 接受与拒绝 / 扫描器 fail-closed / runtime 端到端真分支执行 do）。 |
| `docs/fn_节点设计评估.md` | **新增**：CEL/Lua/白名单三方案评估、五条沙箱硬条件、与 L3 闸的关系、复核触发条件。 |
| `README.md` / `docs/ROADMAP.md` | 里程碑状态更新到 1.0.0 + v1.0 发布说明；路线图 v1.0.0 标 ✅。 |

## 行为变化

- IR 表达式新增能力，**向后兼容**：既有 var/const operand、比较/逻辑/一元算子行为零变化（evaluate 签名不变）。
- 新增拒绝路径：未知函数 / 参数个数不符 / 深度>32 / 节点>256 → 编译期 `EXPR_INVALID`（ERROR，`forge build` 拒绝）、运行期 `ExprError` → `if` 软失效走 `on_error`。
- `fn` *节点*（node 字段 `fn`）**仍被 `RESERVED_NOT_IMPLEMENTED` 拦截**——本版只交付评估报告（结论：不引入 CEL/Lua）。

## 已知风险 / 注意

- `time_*` 函数需要调用方把时间戳放进 vars/context（如 `set` 节点存 `last_changed`）；表达式不取墙钟是有意设计。
- 白名单函数是 Python 实现，新增函数需同步：`FUNCTIONS` 注册表 + `docs` 函数清单（schema 无需改，fn 名为自由字符串）。
- `round` 为 Python 语义（banker's rounding）；`floor/ceil` 返回 float 以便与 numeric 统一比较。
- 深度上限对 Agent 生成的病态嵌套表达式（如 300 路 or）给出明确 `ExprError`，不会栈溢出。

## 验证

- 本机：`python -m pytest -q` → **323 passed / 10 skipped / 0 failed**（v0.9.0 基线 306 + 17）。
- 关键端到端：`if` 节点 expr 使用 `round(entity.temp, type=numeric) > 25` → 25.6 走真分支执行 `light.turn_on`（MockAdapter calls 断言）、25.3 走 no 分支；扫描器对未知函数 `nope()` 报 `EXPR_INVALID` 拒绝。
- 退出标准达成：新函数求值正确 ✓；无安全回归（全量零失败、fail-closed 拦截）✓；双环境全绿 ✓。

## 合并影响

- AutoForge-UI 前端如需展示 fn operand，`/api/graphs` 返回的 IR 中会自然出现（读端点无结构变化）。
- AF-Spec（af_spec）文本语法不支持 fn operand——文本层写不了的函数调用请用 JSON IR（既有边界，未变）。
- 路线图 v0.2.0–v1.0.0 十个小版本全部交付，无后续排期版本；后续以缺陷修复（patch）或新主题立项。
