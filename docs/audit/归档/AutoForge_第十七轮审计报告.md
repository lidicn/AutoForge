# AutoForge 审计报告 · 第十七轮

- **审计对象**：`lidicn/AutoForge`
- **审计工具链**：`lidicn/ADM-auditkit`
- **本轮轮次**：`round-017`
- **产物**：`/data/workspace/repos/adm/projects/AutoForge/round-017/`
- **报告日期**：2026-10-07

---

## 一句话结论

**递归 PoC 的 `unavailable` 从 9 条归零：9 个"测不到"的目标全部变成可判定，其中 4 个确证崩溃——F6（trigger group 无深度上限）在第二轮提出后，直到本轮才第一次被自动实测确证。**

---

## 一、W43：递归 PoC 的三处改造

### 1. 支持任意深度嵌套函数

`_exec_nested` 从源码 `exec` 提取嵌套函数。此前 `getattr(module, func)` 拿不到内嵌在其他函数里的目标——**不报错，只是拿不到**，于是目标直接归到 `unavailable`。

### 2. 形状推断改为「参数名 + 类型注解」驱动

新增 `_is_recursive_target` / `_probe_for`。对宽泛型注解（`Mapping` / `dict[` / `Any` / `object` / 空 / `list[` / `Sequence`）回退到**函数名推断**，并把 `fn_hint` 改成 `模块名 + "." + 函数名`——只传函数名时 `expr.py:255 _walk` 会被误判成 safe，加上模块名才定位正确。

非目标参数用 `_Standin` 惰性替身（属性/调用返回自身、迭代空、比较 False）。

### 3. `_NestStandin` 自展开容器替身 + 降级重试

对 `unavailable` 的 cyclic 探针，换 `_NestStandin` 重试一次：它的 `get` / `__getitem__` / `__getattr__` 返回 `[self]`、`__bool__` 为 True、`__len__` 为 1。

调试时踩的一个坑值得记：初版 `__iter__` 返回空迭代，`for node in nodes:` 循环体一次都不执行 ⇒ 函数安静返回 ok，**探针判"没问题"**。改成 `iter([self])` 产出自身后才真正触发递归。

**这第四次踩了同一类坑**：替身太保守 ⇒ 被测代码根本不进递归 ⇒ 判 safe/unavailable。失效方向永远是"看起来没问题"。

---

## 二、unavailable 归零：9 条去向

| 目标 | 第十六轮 | 第十七轮 |
|---|---|---|
| `af_fidelity._trigger_to_dict` | unavailable | **cyclic_crash** |
| `af_nl._trigger_text` | unavailable | **cyclic_crash** |
| `af_scanner._triggers_node` | unavailable | **cyclic_crash** |
| `af_orchestrator._rep` | unavailable | **cyclic_crash** |
| `af_nl_parse._iter_nodes` | unavailable | **cyclic_crash**（`via_fallback`） |
| `af_conflict_runtime._walk` | unavailable | safe |
| `af_evo._interpret` | unavailable | safe |
| `af_nl_parse._expr_atom` | unavailable | safe |
| `af_nl_parse._carry_runtime` | unavailable | safe |

**这是覆盖面改善，不是缺陷变多**——此前这些目标的"测不到"和"没问题"在报表里长得一模一样。

最终：`cyclic_crash 14 / safe 6 / inconclusive 4 / unavailable 0`。

---

## 三、F6 首次自动实测确证

F6 从**第二轮**就提出：trigger group 嵌套全程无深度上限（`expr` 有 `MAX_EXPR_DEPTH`、`condition_norm` 有 `MAX_CNF_CLAUSES`，trigger 侧一个都没有）。此后十五轮它一直是"静态命中 + 人工推理"，从未被自动实测确证——因为探针构造不出 `Trigger` 对象。

本轮实测结果：

| 路径 | 结果 |
|---|---|
| `af_fidelity._trigger_to_dict` | 自引用 Trigger ⇒ **RecursionError** |
| `af_nl._trigger_text` | 自引用 Trigger ⇒ **RecursionError** |
| 深度阶梯 | depth=50 / 200 通过；**1000 / 3000 崩** |
| `af_scanner._triggers_node` | 运行期路径实测崩溃 |

**运行期路径尤其重要**：`af_scheduler` 对每个事件都遍历一次 trigger group，所以它的暴露面比保存时校验更宽。证据已更新进台账。

---

## 四、一处必须说明的证据强度区分

`af_nl_parse.py:1357 _iter_nodes` 的崩溃由 `_NestStandin` 降级替身触发，已在 `poc.json` 顶层标 `via_fallback=True`（此前只写在 probe 层，本轮透出到 rec 顶层）。

**含义**：它证明被测代码确实裸递归，但**不代表真实业务输入能构造出这种自引用结构**。不标出来就会和"用真实输入实测崩了"混为一谈，虚增确证强度。14 条 crash 里只有这一条带此标注。

---

## 五、`ensure_tools` 纳入项目运行时依赖

新增 `PROJECT_DEPS`（8 项：`fastapi` / `typer` / `jsonschema` / `httpx` / `websockets` / `uvicorn` / `paho-mqtt` / `tomli`），做 **import 型检查 + 自动补装**。

结果：**项目依赖就绪 8 / 缺 0**。这直接解释了递归 PoC 里一部分 `unavailable` 的成因——不是探针构造不出输入，是模块压根导入不了。

外部工具仍是就绪 3 / 受阻 1（`semgrep` 安装期 `OSError [Errno 27] File too large`，包可 import 但无 CLI）。

---

## 六、正式审计结果

| 指标 | 数值 |
|---|---|
| 分析器 | 26/26 ok（门禁 **31** 项全过） |
| 原始命中 | **1083** |
| 去重后待办 | **1052**（抑制 4 条） |
| 严重度 | high **85** / medium 682 / low 285 |
| 跨轮 diff | **new 0 / gone 0** |
| 台账 | **F1–F16 全部 still_open** |
| 递归 PoC | **cyclic_crash 14 / safe 6 / inconclusive 4 / unavailable 0** |
| 状态 PoC | 9/9 data_lost |
| 出站 PoC | bypass_confirmed_unfixed（原仓库） |
| 依赖审计 | homesdk 58 条（high 3 / medium 43 / low 12） |

门禁：`ok=True`、analyzers=31、failures=[]、drift=0；extras 全绿。

**new 0 / gone 0 合理**：本轮改的是 PoC 覆盖面，没动分析器规则，静态命中面应与上轮一致（1083）。

---

## 七、仍未覆盖

| 工具 | 未覆盖面 | 状态 |
|---|---|---|
| pip-audit | 依赖 CVE / 供应链漏洞 | ❌ osv.dev、pypi.org 均 HTTP 403；且装上也跨 bash 调用丢失 |
| semgrep | SAST 交叉验证 | ❌ 安装失败，无 CLI |
| — | homesdk 运行时探针 | ❌ wheel 要求 Python>=3.11，环境 3.10 |
| pytest | 测试运行时探针、变异测试 | ❌ 未接入 |

**4 条 `inconclusive` 未解**：`af_draft._resolve_expr`、`af_scheduler._satisfied`、`af_version.diff`、`detectors.expr_nodes`。

**依赖 CVE 面十七轮都没扫。** `homesdk` 是未上 PyPI 的私有 wheel——**风险未知，不是无风险**。

F1–F7、F9、F11、F12、F16 未纳入补丁。F8/F10/F13/F14/F15 补丁只存在于只读副本 `/data/workspace/repos/af-patched`，**原仓库未改动**。

---

## 八、一条硬约束（延续第十六轮）

**凡涉及外部工具的步骤，必须在同一次 bash 调用内先跑 `scripts/ensure_tools.py` 再执行。** 分开调用会静默丢掉工具能力，且表现为"没有发现"而非报错。
