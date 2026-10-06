# AutoForge 第三轮审计报告（工作流迭代 + 破坏性数据丢失模式）

- **审计对象**：`lidicn/AutoForge`（不变）
- **审计工具链**：`lidicn/ADM-auditkit`（本轮继续迭代）
- **审计焦点**：稳定性与功能性缺陷（口径同前两轮）
- **本轮轮次**：`round-003`
- **产物**：`/data/workspace/repos/adm/projects/AutoForge/round-003/`
- **报告日期**：2026-10-06

---

## 一、本轮最重要的发现：F8 破坏性数据丢失模式

**先给结论**：AutoForge 存在一类系统性缺陷 —— **读侧静默失败返回空 + 写侧全量覆盖 = 一次读取失败即永久清空全部历史数据**。已在 **3 处独立实测复现**，三处都是同一个形状。

这不是三个孤立的 bug，是**一个模式的三个实例**。写侧都认真做了防护（`FileLock` + `atomic_write_text` 防并发竞态），但**完全没有防"读失败"这一侧**。结果就是：防住了并发，没防住损坏。

### 复现 ①：归档标签（tags.json）

`af_store.py:343` `_read_tags()` 在 JSON 解析失败时返回 `{}`；`set_tags()` 在 `FileLock` 内做读-改-写**全量覆盖**。

```
写入 a/b/c 三个归档的标签 → 成功
损坏 tags.json（截断成非法 JSON）
_read_tags() → {}          ← 无报错、无日志
set_tags("c", [...])  → 落盘
tags.json 现在只剩 {"c": [...]}
```

**a、b 两个归档的标签被永久清除**，全程无异常、无警告、不可恢复。

### 复现 ②：经验库（experience.json）

`af_experience._load()` 在 `OSError/ValueError` 时返回空骨架；`observe()` 持锁读-改-写后**全量覆盖**。

```
观测 3 次 → observed=3, entities={e1:3, e2:3, e3:3}
截断 experience.json
_load() → {'observed': 0, 'pairs': {}, 'entities': {}, ...}
再观测一次 → 文件被重写为 observed=1
```

**累积的经验计数全部归零**。这个计数是实体解析做先验/破同分的输入，静默归零会让解析质量无声退化。

### 复现 ③：凭据（credentials.json）

`af_config._load_credentials()` 返回 `{}`；`refresh()` 用它覆盖内存缓存；`update_credentials()` 基于内存缓存**全量覆盖**落盘。

```
初始：ha_token + api_token 都在
损坏 credentials.json → refresh() → _creds = {}
update_credentials(ha_token="HA-NEW") → 落盘 {'ha_token': 'HA-NEW'}
```

**api_token 被静默丢弃**。

### 为什么这条排 P0

- **失败方向反直觉**：不是"写入出错"，是"**写成功了，但写的是空的**"。操作返回成功。
- **不可恢复**：`atomic_write_text` 保证原子性，也意味着旧内容没有半点残留可捞。
- **无声**：三处均无 error 级日志，运维只会看到"数据没了"，看不到"为什么没了"。
- **触发条件平常**：文件损坏不需要攻击，磁盘满、断电、手工编辑、跨版本格式不兼容都能造成。

**修复方向**：读失败与"文件不存在"必须区分开 —— 不存在可以当空，解析失败必须 error 级留痕并**阻断写侧**（或写侧改 merge 而非全量覆盖）。

**同类风险未实测**：`af_preference._load()` 同样经 `read_jsonl_bounded`（失败返回 `[]`）加载，且 `_rewrite_all()` 是"内存有多少写多少"的全量覆盖。理论上读失败后触发一次压缩即清空全部偏好历史，但本轮未构造出复现，故**不列入 F8 的已确证范围**，仅登记为同类风险待验。

---

## 二、工作流迭代（本轮 4 项）

### W3【分析器缺陷】`ast_defects` 未排除嵌套函数作用域 → 制造大量假阳性

**现象**：分诊榜首长期被 `AF-AST-MIXED-RETURN` 占据，`af_api.build_app()` 被报"107 处返回值 + 4 处 return None，标注 FastAPI 却返回 None"。

**实测**：

```
build_app: Return 总数 111，内层函数/Lambda 98 个
  归属内层的 return: 110
  真正属于 build_app 的: 1   → return app（行 1231）
```

`ast.walk(fn)` 会走进内层 `def` 和 lambda，把 98 个路由处理函数的 return 全算到了外层工厂函数头上。**这条假阳性一度占据分诊榜前两位**，把人工确证精力引向一个完全正确的函数。

**根因值得记一笔**：PITFALLS 里早就记过"嵌套函数"这个坑，但当时只在**调用链还原**（`_chain`）处修了，**返回收集**这条路径没修 —— 同一个坑，换个位置又踩一次。

**修复**：新增 `own_returns()`，统计 return 时排除嵌套 `FunctionDef`/`AsyncFunctionDef`/`Lambda` 作用域。同时在 clean 样本加了"工厂函数内含嵌套处理函数"的回归用例锁住这个修复。

**效果**：`AF-AST-MIXED-RETURN` 减少 20 条，high 从 60 降到 **50**，分诊榜首从 `AF-AST-MIXED-RETURN` 变成 `RSC-02-recursion-no-budget`（真问题）。

### W4【纠错】第一轮对 `RMW-01/af_experience` 的抑制是**错的**，已推翻

第一轮我把 `af_experience` 的 RMW-01 判为假阳性，理由是"observe 持锁读-改-写、clear 持锁全量重置，语义上就是要清空"。

**这个推理答错了问题。** 真正的风险不在 `observe` 与 `clear` 的语义对比，而在 `_load()` 失败返回空之后 `observe` 的覆盖行为 —— 也就是 F8 的第二个实例。

已把该条从抑制清单移除，`af_experience` 现在重新出现在待办里（本轮 `new=1` 就是它），并作为 F8 的证据之一写进台账。

> 记这一条是因为：抑制清单是"人工确证过"的权威声明，一条错误的抑制比一条误报更危险 —— 它会让后续每一轮都系统性地跳过真缺陷。抑制必须可推翻。

### W5【新增】`core/triage.py` 分诊排序

1107 条待办不可能逐条人工看。新增分诊模块，按 4 个信号打分排序：

| 信号 | 含义 | 本轮计数 |
|---|---|---|
| `ext_reachable` | 该函数能从外部入口（API/MCP/CLI）可达 | 303 |
| `fix_comment` | 源码里有修复注释（大概率已修） | 95 |
| `intent_doc` | 有意图声明（fail-closed 等，大概率有意设计） | 75 |
| `startup_only` | 仅启动期调用（无并发窗口） | 51 |

**关键设计：分诊只排序，不删命中。** 输出 `triage.md` + `triage.json`。

### W6【新增】聚合层去重 + 保留原始字段 + 抑制支持字段级条件

- **去重**：跨根扫描（`src` + `scripts`）会产生重复命中，聚合时按 key 去重，本轮 **deduped 80**
- **保留原始字段**：原来聚合会丢掉 `function`/`annotated` 等字段，导致分诊的 `startup_only` 恒为 0 —— 修好后才真正生效
- **字段级抑制**：`suppress.py` 支持 `when` 条件（如 `{"annotated": "Any"}`），用于精确压制"`Any` 标注本就什么都可能是，返回 None 不算违背契约"这类需要看具体值的误报

`auditkit round` 现在是 6 阶段：自检门禁 → 画像 → 扫描 → 台账复核 → 聚合 → 分诊排序。

---

## 三、第三轮审计结果

| 指标 | 数值 |
|---|---|
| 原始命中 | 1237（与前两轮一致，扫描范围稳定） |
| 去重 | -80 |
| 抑制后待办 | **1107**（抑制 35 条） |
| 严重度 | high **50** / medium 735 / low 277 / info 45 |
| 台账 | **F1–F8 全部 still_open**，fixed 空、unknown 空 |
| 跨轮 | `new=1`、`gone=22`、`persistent=1106` |

**跨轮差异逐条可解释**（这是本轮才建立起来的纪律）：
- `gone=22` = 20 条 MIXED-RETURN（W3 规则修复消除的假阳性）+ 2 条 CONC-08（premiere 已确证假阳性）
- `new=1` = `RMW-01/af_experience`（W4 推翻抑制后回归，即 F8 第二实例）

**没有任何一条差异来源不明。** 对比第二轮那次 125 条"假消失"——那次是扫描范围漂移伪装成修复进展。

### 剩余 50 条 high 分布

| 规则 | 条数 | 归属 |
|---|---|---|
| RSC-02-recursion-no-budget | 23 | F2 / F6 族（递归无预算） |
| AFS-01-silent-failure | 19 | F7 / F8 族（静默失败） |
| RSC-05-limit-inconsistency | 3 | F2 |
| CONC-08-partial-lock-coverage | 2 | F3 |
| TX-04 / IN-05 / RMW-01 | 各 1 | F8（RMW-01 即 F8②） |

**剩余 high 已高度收敛到 F2/F6/F7/F8 四个已知族**，不再是无结构的散点。

---

## 四、缺陷台账（F1–F8）

| ID | 严重度 | 一句话 | 位置 | 优先级 |
|---|---|---|---|---|
| **F8** | **high** | 读侧静默返空 + 写侧全量覆盖 → 一次读失败永久清空全部历史（3 处实测） | `af_store.py:343` | **P0** |
| F1 | high | 扫描器校验顺序倒置，护栏排在遍历之后 | `af_scanner.py:348` | P0 |
| F6 | medium | Trigger group 嵌套全程无深度上限（连常量都没有） | `af_ir/models.py:114` | P1 |
| F2 | medium | 静态遍历不内建预算，护栏靠调用约定 | `af_ir/expr.py:255` | P1 |
| F7 | medium | canary 回滚链双层静默失败，docstring 说 fail-closed 实为 fail-silent | `af_adapters/ha.py:164` | P2 |
| F3 | medium | 凭证 `_load` 锁外裸写，锁粒度不一致 | `af_auth.py:539` | P2 |
| F4 | low | `_expr_atom` 递归无预算（上层已降级） | `af_nl_parse.py:629` | P3 |
| F5 | low | `held_by_other` 宽捕 `OSError` | `af_flock.py:132` | P3 |

**F8 与 F1 同为 P0，但理由不同**：F1 是"护栏装错了位置"，F8 是"防护只做了一半"。F8 的后果更直接 —— 它丢的是用户数据。

---

## 五、本轮补充的假阳性登记

| 规则 | 位置 | 结论 |
|---|---|---|
| CONC-08 | `af_premiere.py:216/476` | **假阳性**：`load()` 仅启动期单线程调用（调用点 `:128/287/551/555`），无并发窗口。与 `af_auth._load`（F3）形状相同但**调用时机不同** —— 后者在 `pending_events` 遍历中被调用，是真竞态。静态分析看不到调用时机 |
| AF-AST-MIXED-RETURN | `annotated == "Any"` 的 11 条 | **假阳性**：`Any` 语义本就"什么都可能是"，返回 None 不违背契约。用新增的字段级 `when` 条件精确压制 |

抑制按 verdict 分布：false_positive 22 / fixed 7 / degraded 6。

---

## 六、仍未覆盖（三轮均未改善）

| 工具 | 未覆盖面 |
|---|---|
| semgrep | SAST 交叉验证 |
| detect-secrets | 密钥扫描 |
| pip-audit | **依赖 CVE / 供应链漏洞**（私有 `homesdk` wheel） |
| pytest | 测试运行时探针、变异测试 |
| skill-scan / graph | MCP 配置面、调用图可达性 |

依赖漏洞面三轮都完全未扫，建议单独补扫。另外 medium 735 条仍只做了抽样确证。

---

## 附：本轮产物

| 文件 | 内容 |
|---|---|
| `round-003/findings.json` | 去重抑制后 1107 条 |
| `round-003/triage.md` | 分诊排序 Top 40（人工确证优先级） |
| `round-003/suppressed.json` | 35 条抑制（含理由与 verdict） |
| `round-003/cross-round-diff.json` | new/gone/persistent |
| `baseline/bugs.json` | 台账 F1–F8 |
| `core/triage.py` | 本轮新增分诊模块 |
