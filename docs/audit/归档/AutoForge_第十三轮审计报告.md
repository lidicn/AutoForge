# AutoForge 审计报告 · 第十三轮

- **审计对象**：`lidicn/AutoForge`
- **审计工具链**：`lidicn/ADM-auditkit`
- **本轮轮次**：`round-013`
- **产物**：`/data/workspace/repos/adm/projects/AutoForge/round-013/`
- **报告日期**：2026-10-06

---

## 一句话结论

**F14（递归缺环检测）从"实测 9 个函数崩溃"变成补丁副本上"0 崩溃"，并实测证明了「环检测」与「深度预算」是两道正交防护——只装一道会换一种方式崩。**

---

## 一、F14 修复：9 个函数全部拦住

新建共享模块 `af_cycleguard.py`，提供 `cycle_safe(on_cycle=..., max_depth=400)` 装饰器：

- **回溯式 visited**：进入 `add`、退出 `discard`，只拦真环，不误伤 DAG（共享子结构不重复访问是合法的）
- **按 `id()` 登记**：不要求对象可哈希
- **线程局部状态**：并发调用互不干扰
- **`visited` 与 `depth` 装在同一装饰器内**：避免只装一半

装饰的 9 个函数与各自的环回退值（按语义选择，不是统一 `None`）：

| 函数 | on_cycle |
|---|---|
| `af_version._jsonable` | `"<cycle>"` |
| `af_evo._canon` | `"<cycle>"` |
| `af_nl._expr_text` | `"?"` |
| `af_orchestrator.describe_condition` | `"?"` |
| `af_orchestrator._probe_expr` | `None` |
| `af_orchestrator.substitute_refs` | 原样返回 |
| `condition_norm._nnf` | `("empty",)` |
| `expr._walk`、`af_orchestrator.iter_strings` | 生成器（终止） |

---

## 二、本轮最重要的认知：两道防护正交

只加 `visited` 后回归，**9 个 cyclic_crash 全部消失，但冒出 8 个 depth_crash**：

- 2 个在 depth=1000 崩（`af_evo._canon`、`describe_condition`）
- 6 个在 depth=3000 崩（`_nnf`、`_walk`、`iter_strings`、`substitute_refs`、`_probe_expr`、`_jsonable`）

原因是 Python 栈上限（~1000）先于任何业务预算触发。加上 `max_depth=400` 后，**23 个目标全部 safe，0 崩溃**。

这与第七轮的推测吻合，但这次是实测确认：**预算挡线性深嵌套，环检测挡循环引用，两者互不替代。** 只装一道，缺陷不会消失，只会换个触发条件。

---

## 三、`_nnf` 的第二条失败路径（值得单说）

`_nnf` 加了装饰器后自引用输入**仍然崩**，抛 `ValueError: Circular reference detected`。

原因不在遍历，在序列化：`_leaf_key` 把整个结构交给 `json.dumps`，**绕过了装饰器的防护**。装饰器拦的是递归调用，拦不住 `json.dumps` 内部的环检测。

修法：新增 `safe_json_dumps`（`json.dumps` 失败时回退 `repr`），`_leaf_key` 改用它。修复后自引用输入返回 `('lit', "{'self': {...}}")`。

**教训**：给函数装护栏，不等于这条路径上所有子操作都受保护。序列化、比较、哈希这些"看起来不是遍历"的调用，同样会碰环。

---

## 四、工作流迭代

### W36：补丁自检扩展

`patch_lint.py` 纳入 `cycle_safe` / `safe_json_dumps`，并新增**导入冒烟测试**——真的把整个包 import 一遍。

为什么需要冒烟：第十三轮给 `af_ir` 子包插的 import 写成了 `from .af_cycleguard`（应为 `from ..af_cycleguard`）。**语法合法、名字也对，只有真导入才炸。** 静态的"名字是否导入"检查查不出相对层级写错。

调试中修了两处假阳性：

1. 相对导入的 `node.module` 是 `"af_cycleguard"`（**不带前导点**），层级在 `node.level`。第一版只判 `endswith(".af_cycleguard")` ⇒ 顶层相对导入全部漏判，把 4 个已正确导入的文件报成"没导入"。
2. 12 个模块因缺 `fastapi` / `homesdk` / `typer` 导入不了，那是**环境缺口**，不是补丁的错。混在一起报会把真问题淹没在噪声里，已分开统计。

**并对这个检查做了负向测试**：故意把 `af_ir/expr.py` 改回 `from .af_cycleguard`，自检立刻报 25 个模块导入失败；还原后 OK。**没做过负向测试的检查等于没有检查**——这正是本项目反复栽跟头的地方。

---

## 五、正式审计结果（原仓库，未打补丁）

| 指标 | 数值 |
|---|---|
| 分析器 | 24/24 ok，原始命中 **1076** |
| 去重后待办 | **1045**（抑制 4 条） |
| 严重度 | high **85** / medium 676 / low 284 |
| 跨轮 diff（vs round-012） | **new 0 / gone 0** |
| 台账 | **F1–F14 全部 still_open** |
| 递归 PoC（原仓库） | cyclic_crash **9** / safe 2 / unavailable 9 / inconclusive 4 |
| 递归 PoC（补丁副本） | **cyclic_crash 0 / depth_crash 0 / safe 10** |
| 状态 PoC（原仓库） | 9/9 data_lost |

门禁 26 项全过，`drift=0`，clean 零假阳性。

---

## 六、补丁覆盖现状

| 族 | 状态 |
|---|---|
| F8 / F10 / F13 | 补丁副本 8/8 guarded |
| F9、F11（局部） | 补丁副本 guarded |
| **F14** | **补丁副本 0 崩溃（本轮新增）** |
| F1–F7、F12 | 未纳入补丁 |

补丁只存在于只读副本 `/data/workspace/repos/af-patched`，**原仓库未改动**。

---

## 七、仍未覆盖

| 工具 | 未覆盖面 |
|---|---|
| semgrep | SAST 交叉验证 |
| detect-secrets | 密钥扫描 |
| pip-audit | **依赖 CVE / 供应链漏洞（私有 homesdk wheel）** |
| pytest | 测试运行时探针、变异测试 |

**依赖面新证据**：导入冒烟显示 12 个模块因缺 `fastapi` / `homesdk` / `typer` 无法导入。这说明 `homesdk` 是**硬依赖但不在公开索引**——十三轮都未能扫描其漏洞面，风险未知而非无风险。

递归 PoC 仍有 9 条 `unavailable`（`af_conflict_runtime._walk`、`af_evo._interpret`、`af_fidelity._trigger_to_dict`、`af_nl._trigger_text`、`af_nl_parse._expr_atom`/`_carry_runtime`/`_iter_nodes`、`af_orchestrator._rep`、`af_scanner._triggers_node`）与 4 条 `inconclusive`。其中 `af_scanner.py:688 _triggers_node` 与 `af_scheduler.py:255` 涉及 F6（trigger group 无深度上限），仍未自动验证。
