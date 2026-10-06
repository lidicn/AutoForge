# AutoForge 第八轮审计报告（工具链重建 + 两份 PoC 首次同轮产出）

- **审计对象**：`lidicn/AutoForge`（不变）
- **审计工具链**：`lidicn/ADM-auditkit`（本轮前半程在重建工具链）
- **审计焦点**：稳定性与功能性缺陷（口径同前七轮）
- **本轮轮次**：`round-008`
- **产物**：`/data/workspace/repos/adm/projects/AutoForge/round-008/`
- **报告日期**：2026-10-06

---

## 零、先说本轮的特殊性：审计工具链自己被写坏了

第七轮新增的状态损坏探针在调试中把**审计工具链自己的源文件**写成了坏内容（`{"__corrupt": [`）。 `core/` 下 300+ 文件受损，包括：

- 全部 24 个分析器
- 审计主程序 `auditkit`
- 抑制清单 `config/suppressions.json`
- 自建分析器（DO-* 四态、FO-01、GOD-01/02）与 PoC 生成器
- 缺陷台账 `baseline/bugs.json`

**恢复路径**：从上游归档 `/data/workspace/repos/adm.tar.gz` 拉取原始副本，恢复了 117 个文件；备份中不存在的文件（全部是历轮本地新增的）按前七轮的判据**重写**。

**这件事本身就是一个审计结论**：一个"往盘上写东西来测数据会不会丢"的探针，把宿主工具链的数据弄丢了。它验证了 F8 那族缺陷的破坏力——**不需要恶意输入，一个路径解析失误就够了**。

---

## 一、工具链重建做了什么

### 1.1 重写的自建分析器（判据按历轮确证实例重述，未编造）

| 分析器 | 规则 | 对应确证缺陷 |
|---|---|---|
| `destructive_overwrite_defects.py` | DO-01/02/03/04 | F8/F9/F10/F11/F13 |
| `failopen_guard_defects.py` | FO-01 | F12 |
| `guard_order_defects.py` | GOD-01/02 | F1 / F14 辅助 |
| `poc.py` | 递归实测 | F14 |
| `poc_state.py` | 状态损坏实测 | F8/F10/F13 |
| `triage.py` `suppress.py` | 分诊与抑制 | — |

### 1.2 重放被恢复操作冲掉的本地修复

从上游备份恢复会**静默丢掉历轮的本地修复**。逐个重放：

| 修复 | 内容 |
|---|---|
| W0 | 21 个分析器 25 处 `read_text` 改 `utf-8-sig`（BOM 会让规则静默失效） |
| W3 | `ast_defects` 排除嵌套作用域（见第二节） |
| W14 | `resource_defects` RES-03 改为按线程变量配对，不再用全文件正则 |
| W18 | 读侧有隔离/告警证据时不判 DO-02（可见地丢失 ≠ 静默清空） |

### 1.3 门禁

24 个分析器全过，`clean` 样本零假阳性，`drift=0`。并为每条重放的修复补了**回归样本**——这是本轮最重要的反思：PITFALLS 里记的每一条教训，都必须有对应的自检样本钉住，否则下次恢复/重构就会再丢一次。

---

## 二、W3 假阳性回归：同一个坑，第三次踩

重建完成后首次扫描，`AF-AST-MIXED-RETURN` 报出 19 条 high，其中 **`af_api.py:236 build_app`** 正是第三轮修掉的那条假阳性。

**根因**：`rets = [n for n in ast.walk(fn) if isinstance(n, ast.Return)]`

`ast.walk` 会走进内层 `def` / `lambda`，于是 `build_app` 内部那 98 个路由函数的返回值全被算到它头上——"107 处返回值"，而它自己只有 1 处 `return app`。

**为什么值得单列一节**：这个坑 PITFALLS 里早就记过（"嵌套函数"），但第三轮只在**调用链还原**那条路径上修了，**返回值收集**这条路径没修。同一个坑换个位置又踩了一次；第八轮从备份恢复，两份修复一起丢，第三条路径又踩一次。

**修法**：新增 `_own_returns()`，遇 `FunctionDef`/`AsyncFunctionDef`/`ClassDef` 即停，不进嵌套作用域。修复后 `build_app` 不再命中，`AF-AST-MIXED-RETURN` high 从 19 降到 8。

**补了回归样本** `build_app_nested()`：外层 1 处 return、内层两个路由函数各自 return——钉住修复，防止再丢。

---

## 三、W23（本轮新方法）：抑制清单不能用行号当唯一锚点

重建抑制清单时发现：第五轮写的 `CONC-08` 抑制锚在 `af_premiere.py:202/463`，而本轮命中落在 **216/476**。

**后果链**：行号一漂 → 抑制静默失效 → 缺陷以 `new` 的身份重新回到待办 → 看起来像"新发现"，实际是抑制漏了。这与第四轮 W9 记过的"永不匹配的指纹"是同一类事故，只是换了个载体。

**改法**：抑制锚点优先用 `function`（+ `rule`），只有行号能区分同函数内多处时才带 `line`。清单里写进 `_discipline` 字段，作为长期纪律。

改完后抑制从 2 条生效变 4 条（新增 2 条 premiere 的 `load`）。

---

## 四、第八轮审计结果

| 指标 | 数值 |
|---|---|
| 分析器 | 24/24 ok，原始命中 **1076** |
| 去重 | -27 |
| 抑制后待办 | **1045**（抑制 4 条） |
| 严重度 | high **85** / medium 676 / low 284 |
| 台账 | **F1–F14 全部 still_open**，fixed 空、unknown 空 |
| 跨轮 diff | **不可用**（见下） |
| 递归 PoC | 24 目标：cyclic_crash **9** / safe 2 / unavailable 9 / inconclusive 4 |
| 状态 PoC | 9 目标：data_lost **1** / preserved 1 / unavailable 7 |

### 跨轮 diff 不可用

`round-007/findings.json` 被第七轮探针写坏，本轮无法与之比对，`new` / `gone` **不能计算**。已在 `cross-round-diff.json` 里记 `prev_unreadable`，而不是填 0 假装没事。

### 两份 PoC 是本轮首次同轮产出

**递归 PoC**（9 个函数自引用即崩，与第七轮完全一致，F14 稳定）：

`_canon`、`_nnf`、`_walk`(expr.py:255)、`_expr_text`、`iter_strings`、`substitute_refs`、`describe_condition`、`_probe_expr`、`_jsonable`

对照组实测 safe：`expr.py:270 _walk_operand`、`af_expect.py:85 _apply_op`。

**状态损坏 PoC**：`af_store.py:358 GraphStore.set_tags` → **data_lost**（种子标记 `seedalpha` 在损坏后一次写入中消失）。这是 F8 的第一个实例第一次被**端到端自动确证**——前七轮它是人工实测出来的。

`af_config.py:113 Config.update_credentials` 判 `preserved`（count 模式，条目数未下降）。

**7 条 unavailable 诚实地标着"没测到"**，分两类：
- `no-public-seeder`：`PairCodeStore._persist`、`AuthCodeStore._persist`、`ExperienceStore.observe`——找不到公开播种入口
- `seed-not-observable`：`TokenRegistry._persist_issued`、`DeviceCatalog._record_bucket`、`DeviceCatalog.set_alias`、`PreferenceModel._rewrite_all`——种下了但落盘内容里观测不到

这两类都不是"安全"。F10 的三处（配对码/授权码/已签发令牌）本轮**没能自动复现**，仍依赖第五轮的人工实测。

### 剩余 85 条 high

| 规则 | 条数 | 归属 |
|---|---|---|
| RSC-02-recursion-no-budget | 20 | F2 / F6 / **F14**（9 条已实测崩） |
| AFS-01-silent-failure | 19 | F7 / F8 族（15 条喂给覆盖写） |
| AF-AST-MIXED-RETURN | 8 | — （W3 修复后从 19 降到 8） |
| IN-02-unsafe-cast | 6 | — |
| GOD-01-guard-after-danger | 5 | F1 族 |
| AUTH-04-credential-growth | 4 | — |
| CONC-08-partial-lock-coverage | 4 | F3 |
| DO-01 / DO-02 / DO-03 / DO-04 | 4 / 2 / 2 / 1 | F8–F11 + F13（9 条 = 9 条确证，1:1） |
| RSC-05 / TX-04 | 3 / 3 | F2 / — |
| FO-01 / AUTH-01 / RMW-01 | 2 / 1 / 1 | F12 / — / — |

**DO-* 9 条 = 9 条人工确证实例，无漏无多**（F8①②③ + F9 + F10①②③ + F11 + F13）。

---

## 五、缺陷台账（F1–F14，八轮累计）

本轮**未新增缺陷**。F1–F14 全部 still_open。

| ID | 严重度 | 一句话 | 优先级 |
|---|---|---|---|
| **F8** | high | 读侧返空 + 写侧覆盖 → 清空全部历史（本轮自动端到端确证 1 处） | **P0** |
| **F10** | high | 凭证三处同形态：损坏后一次签发清空全部 | **P0** |
| F1 | high | 扫描器校验顺序倒置 | P0 |
| **F12** | high | 别名归属核对 fail-open | **P1** |
| F9 | medium | 别名表同形态覆盖 | P1 |
| F6 | medium | Trigger group 嵌套无深度上限 | P1 |
| F2 | medium | 静态遍历不内建预算 | P1 |
| **F14** | medium | 递归遍历缺环检测，实测 9 个函数自引用即崩 | P2 |
| F13 | medium | 偏好整档不可解析 → 压缩后历史丢失 | P2 |
| F7 | medium | canary 回滚链双层静默失败 | P2 |
| F3 | medium | 凭证 `_load` 锁外裸写 | P2 |
| F4 / F5 / F11 | low | — | P3 |

**5 个根因**（与前几轮一致）：读失败静默降级（10 处代码）、读失败静默放行、护栏缺失/错位（含环检测）、锁粒度不一致、有意设计与实现不符。

---

## 六、本轮工作流迭代汇总

| 编号 | 内容 |
|---|---|
| W0 重放 | 25 处 `read_text` → `utf-8-sig`（BOM 静默失效） |
| W3 重放 | `ast_defects` 排除嵌套作用域；**补回归样本** |
| W14 重放 | RES-03 按线程变量配对，弃用全文件正则 |
| W18 重放 | 有隔离/告警证据时不判 DO-02 |
| W20 | 产物写入原子化 + 回读校验（`_atomic_write_json`） |
| W21 | 阶段 6/7/8 独立 try/except，一处失败不带走整轮 |
| W22 | 状态探针加 count 模式回退（条目数下降也算丢） |
| **W23** | **抑制锚点改用 function，弃用行号**（行号会漂） |

**W20/W21 的起因**：本轮首次跑完时 `findings.json` 落盘被截断（376KB → 315KB），阶段 6/7/8 全部因 `JSONDecodeError` 崩溃。原子写 + 回读校验后未再复现。

---

## 七、仍未覆盖

| 工具 | 未覆盖面 |
|---|---|
| semgrep | SAST 交叉验证 |
| detect-secrets | 密钥扫描 |
| pip-audit | **依赖 CVE / 供应链漏洞**（私有 `homesdk` wheel） |
| pytest | 测试运行时探针、变异测试 |

状态 PoC 的 7 条 `unavailable` 需人工构造调用上下文——**F10 三处本轮未能自动复现**，仍靠第五轮人工实测支撑。递归 PoC 的 9 条 `unavailable` 多为嵌套函数或参数形状复杂。

跨轮 diff 本轮不可用（round-007 产物损坏），下一轮起恢复。

---

## 附：本轮产物

| 文件 | 内容 |
|---|---|
| `round-008/findings.json` | 待办 1045 条 |
| `round-008/poc.md` | 递归 PoC 结论表（24 目标，9 崩） |
| `round-008/poc_state.md` | 状态损坏 PoC 结论表（9 目标，1 确证） |
| `round-008/triage.md` | 分诊排序 Top 40 |
| `round-008/suppressed.json` | 4 条抑制（含 verdict 与 reason） |
| `round-008/cross-round-diff.json` | prev_unreadable（不可用，已标明） |
| `baseline/bugs.json` | 台账 F1–F14，指纹全匹配 |
