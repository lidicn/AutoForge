# AutoForge 第九轮审计报告（状态损坏 PoC 首次全覆盖确证）

- **审计对象**：`lidicn/AutoForge`（不变）
- **审计工具链**：`lidicn/ADM-auditkit`
- **审计焦点**：稳定性与功能性缺陷（口径同前八轮）
- **本轮轮次**：`round-009`
- **产物**：`/data/workspace/repos/adm/projects/AutoForge/round-009/`
- **报告日期**：2026-10-06

---

## 零、本轮最重要的一件事

**状态损坏探针从「9 个目标只测到 1 个」变成「9 个目标全部实测确证丢数据」。**

| | 第八轮 | 第九轮 |
|---|---|---|
| data_lost | **1** | **9** |
| preserved | 1 | 0 |
| unavailable（没测到） | **7** | **0** |

这意味着：F8（3 处）、F10（3 处凭证）、F13（偏好记录）——**这三条此前全部靠人工实测支撑的缺陷，本轮首次由机器端到端自动复现**。前八轮"F10 三处无法自动复现"这条局限，本轮消掉了。

实现方式是修掉探针自己的四处盲区，每一处都是**假阴性**（看着像"被测代码没问题"，实际是探针看不见）：

| 盲区 | 表现 | 修法 |
|---|---|---|
| `glob` 跳过点目录 | `DeviceCatalog` 把状态写在 `.catalog/` 下，快照里根本没有它 ⇒ 两条命中全程"测不到" | `snapshot()` 改用 `os.walk` |
| 只看"盘上有没有文件" | 目录为空时 `set_alias` 返回 `ok:False` 不写盘，但 `resolve()` 顺手写了一份 `resolve_metrics.json` ⇒ 探针拿 `resolve` 当播种口，判 preserved | 改为**必须种出可辨识标记**才算播种成功 |
| 损坏只截一半 | 标记往往就在文件开头，截断后仍在 ⇒ 判 preserved | 整档覆盖为未闭合 JSON |
| 构造候选只试目录 | `PairCodeStore(path)` 收的是**文件路径**，传目录进去构造不报错、写盘才炸 ⇒ F10 三处全程不可用 | 目录/文件路径/多路径形参/`Callable` 注入全试，由"能不能种出文件"裁决 |

另有一处属于样本自身：`_Duck` 鸭子替身（喂给 `graph: Any` 这类未知形状形参）让 `ExperienceStore.observe` 第一次可播种。

---

## 一、工作流迭代：把 PoC 纳入门禁

第八轮的教训是"从备份恢复会静默丢掉本地修复"。本轮的对策是**给每一处修复配一个门禁**：

新增 `poc_state[dirty]` / `poc_state[clean]` 两项自检：

- **dirty**：4 个 DO 目标必须 **≥1 条 data_lost 且 0 条 unavailable**
- **clean**：必须 **0 条 data_lost**

调试中暴露了样本本身的问题——`TagStoreD51.set_tags(self, k, v)` 的 `k/v` 是未知形状，鸭子替身不可 JSON 序列化，播不进标记；`CodeStoreD52`、`PrefStoreD54` 只有无参的写盘方法，没有公开播种入口。真实仓库里对应的类都有 `create()` / `record_accept()`，**是样本不像真实代码，不是探针的问题**。给样本补上真实形态的入口后，4/4 全部 data_lost。

门禁现在 25 项（原 24 + 状态 PoC），全过，clean 零假阳性，`drift=0`。

---

## 二、第九轮审计结果

| 指标 | 数值 |
|---|---|
| 分析器 | 24/24 ok，原始命中 **1076** |
| 去重 | -27 |
| 抑制后待办 | **1045**（抑制 4 条） |
| 严重度 | high **85** / medium 676 / low 284 |
| 跨轮 diff（vs round-008） | **new 0 / gone 0** |
| 台账 | **F1–F14 全部 still_open**，fixed 空、unknown 空 |
| 递归 PoC | 24 目标：cyclic_crash **9** / safe 2 / unavailable 9 / inconclusive 4 |
| 状态 PoC | 9 目标：**data_lost 9 / unavailable 0** |

### 跨轮 diff 恢复可用

round-008 产物完好，本轮首次算成：**new 0 / gone 0**。这本身是个信号——本轮没动任何分析器规则（只改了探针与样本），静态命中面与前一轮完全一致，说明基线稳定。

### 状态 PoC 9/9 明细

| 位置 | 播种入口 | 丢失内容 |
|---|---|---|
| `af_auth.py:413` TokenRegistry._persist_issued | `issue_for_agent` | state.json：seedalpha、seedbeta |
| `af_auth.py:544` PairCodeStore._persist | `create` | state.json：seedalpha、seedbeta |
| `af_auth.py:670` AuthCodeStore._persist | `create` | state.json：2→1（条目数） |
| `af_catalog.py:499` DeviceCatalog._record_bucket | `_record_bucket` | .catalog/resolve_metrics.json |
| `af_catalog.py:567` DeviceCatalog.set_alias | `set_alias` | .catalog/aliases.json |
| `af_config.py:113` Config.update_credentials | `update_credentials` | credentials.json：seedbeta |
| `af_experience.py:61` ExperienceStore.observe | `observe` | experience.json |
| `af_preference.py:528` PreferenceModel._rewrite_all | `record_accept` | preferences.jsonl |
| `af_store.py:358` GraphStore.set_tags | `set_tags` | tags.json |

**F10 的三处凭证存储（配对码 / 授权码 / 已签发令牌）本轮全部自动复现**——第八轮它们是 7 条"没测到"里的 3 条。

### 递归 PoC

9 个函数自引用即崩，与第七、八轮完全一致（F14 稳定）：`_canon`、`_nnf`、`_walk`(expr.py:255)、`_expr_text`、`iter_strings`、`substitute_refs`、`describe_condition`、`_probe_expr`、`_jsonable`。对照组 safe：`expr.py:270 _walk_operand`、`af_expect.py:85 _apply_op`。

---

## 三、缺陷台账（F1–F14）

本轮**未新增缺陷**。F1–F14 全部 still_open。台账证据已按本轮实测更新：F8 / F10 / F13 标为 `auto-confirmed@round-009`。

| ID | 严重度 | 状态 | 优先级 |
|---|---|---|---|
| **F8** | high | 机器确证（9/9 中的 3 处） | P0 |
| **F10** | high | **机器确证**（三处全中） | P0 |
| F1 | high | 人工确证 | P0 |
| **F12** | high | 人工确证 | P1 |
| F9 | medium | 机器确证（set_alias） | P1 |
| F6 / F2 | medium | 人工确证 | P1 |
| **F14** | medium | **机器确证**（9 崩） | P2 |
| **F13** | medium | **机器确证**（record_accept） | P2 |
| F7 / F3 | medium | 人工确证 | P2 |
| F4 / F5 / F11 | low | 人工/机器确证 | P3 |

**5 个根因不变**：读失败静默降级（10 处代码，现 9 处机器确证）、读失败静默放行、护栏缺失/错位（含环检测）、锁粒度不一致、有意设计与实现不符。

---

## 四、本轮工作流迭代汇总

| 编号 | 内容 |
|---|---|
| W24 | `snapshot()` 改 `os.walk`（glob 跳过点目录） |
| W25 | 播种成功改由"可辨识标记"裁决，不看"盘上有无文件" |
| W26 | 损坏改为整档覆盖未闭合 JSON（截断会残留标记） |
| W27 | 构造候选：目录/文件路径/多路径形参/`Callable` 注入，`_Duck` 未知形状替身 |
| W28 | 状态 PoC 纳入门禁（dirty 要求 ≥1 lost 且 0 unavailable；clean 要求 0 lost） |
| W29 | dirty 样本补真实形态的播种入口（探针没错，是样本不像真实代码） |

**最该记住的一条**：这四处盲区全部表现为**假阴性**——"缺陷消失"而不是"报错"。本项目已第五次遇到同类事故（`scripts/` 漏扫、写盘别名缺失、RES-03 全文件正则、抑制锚点行号漂移、这里）。**任何"命中变少"都必须先怀疑工具，再相信结论。**

---

## 五、仍未覆盖

| 工具 | 未覆盖面 |
|---|---|
| semgrep | SAST 交叉验证 |
| detect-secrets | 密钥扫描 |
| pip-audit | **依赖 CVE / 供应链漏洞**（私有 `homesdk` wheel） |
| pytest | 测试运行时探针、变异测试 |

递归 PoC 仍有 9 条 `unavailable`（嵌套函数或参数形状复杂），4 条 `inconclusive`。RSC-02 剩余 20 条 high 中仍有未浮出者。

---

## 附：本轮产物

| 文件 | 内容 |
|---|---|
| `round-009/findings.json` | 待办 1045 条 |
| `round-009/poc_state.md` | 状态损坏 PoC：9 目标全 data_lost |
| `round-009/poc.md` | 递归 PoC：24 目标，9 崩 |
| `round-009/triage.md` | 分诊排序 Top 40 |
| `round-009/cross-round-diff.json` | vs round-008：new 0 / gone 0 |
| `baseline/bugs.json` | 台账 F1–F14（证据已更新） |
