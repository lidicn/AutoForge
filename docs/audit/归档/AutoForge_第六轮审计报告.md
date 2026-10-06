# AutoForge 第六轮审计报告（跨作用域覆盖链 + 一条规则曾"变瞎"）

- **审计对象**：`lidicn/AutoForge`（不变）
- **审计工具链**：`lidicn/ADM-auditkit`（本轮继续迭代）
- **审计焦点**：稳定性与功能性缺陷（口径同前五轮）
- **本轮轮次**：`round-006`
- **产物**：`/data/workspace/repos/adm/projects/AutoForge/round-006/`
- **报告日期**：2026-10-06

---

## 一、本轮核心成果：F13 从"人工撞见"变成"机器发现"

第五轮的 F13（偏好记录整档不可解析 → 压缩后历史丢失）是**人工抽样撞见的**，不是规则找出来的。本轮补上判据后，规则自动命中了它。

**为什么之前的规则抓不到**：DO-01/02/03 的 join **全在单个 `ClassDef` 内做**。而 F13 的读侧是**模块级函数** `read_jsonl_bounded`（在 `af_store.py` 里定义，被 `af_preference.py` import），根本不是 `PreferenceModel` 的方法——整条链跨了模块边界，join 直接失联。

实测确认：第五轮结束时 DO-* 共 8 条命中，`af_preference` **零命中**。

### DO-04 判据（跨作用域链）

1. **全仓读侧索引**：先扫一遍所有 `.py`，收集"名字像读 + 有 except + 降级为空/void"的函数，**不论它在哪个作用域**（模块级 / 类内 / 跨文件）
2. **字段填充**：某方法调用该 reader，并把结果灌进 `self._X`（`append` / 下标赋值 / 整体赋值）
3. **字段导出**：另一个方法把 `self._X` **整体序列化**写盘（要求有集合序列化痕迹 `values()/items()`/整体 dumps，排除 `revision` 这类标量计数）

**结果**：`af_preference.py:528 PreferenceModel._rewrite_all` 命中，reader = `read_jsonl_bounded`，field = `_records`。与第五轮人工实测复现的 F13 完全一致。

### DO-* 四态现在与确证缺陷 1:1 对应

| 规则 | 命中 | 对应缺陷 |
|---|---|---|
| DO-01（读侧返回空） | 4 | F8①②③ + F9 |
| DO-02（void 读侧） | 2 | F10①② |
| DO-03（单函数自闭环） | 2 | F10③ + F11 |
| DO-04（跨作用域） | 1 | **F13** |

**9 条机器命中 = 9 条人工确证，无漏、无多。** 破坏性覆盖这一族的机器覆盖现在完整了。

---

## 二、工作流迭代（本轮 3 项）

### W13【新增规则】DO-04-cross-scope-overwrite

见上节。调试中修掉的两个问题：

- **变量遮蔽**：`analyze()` 的参数 `readers`（全仓索引）被函数内同名的类内索引字典**覆盖**，DO-04 拿到的一直是空字典。改名为 `greaders`。这类 bug 的恶劣之处在于——不报错，规则安静地什么都不报。
- **重复上报**：`Config.update_credentials` 被 DO-01 和 DO-04 各报一次（同一行两条）。判据收窄为"**只报读侧不是本类方法的链**"，DO-04 专管跨作用域，DO-01/02/03 管类内，分工不再重叠。

另外，初版 DO-04 在 `af_preference` 上刷出 **6 条**（`suggest`/`stats`/`_rebuild_stats` 等只读方法也被误报）。两处收紧：要求写侧**真的落盘**（自身写或经 helper 写），且同一（字段, 读侧）**只报一条**——同一个缺陷不该按方法数刷屏。

### W14【修复】`resource_defects` 的 RES-03 曾全局性"变瞎"

这是本轮最值得记的一条，因为它是**被新增的测试样本意外暴露的**：

我在 dirty 样本里加了一行 `"\n".join(...)`（纯粹是拼接字符串），自检立刻报：

```
[-dirty] resource_defects: RES-03-thread-no-join 消失（原 ×1）—— 规则可能失效
```

根因：

```python
has_join = bool(re.search(r"\.join\s*\(", src_all))   # 全文件正则
```

**文件里任何一处 `"\n".join(...)` 都会让该文件所有线程被判成"已 join"。** 这是典型的"全文件字符串匹配冒充数据流分析"。

危害在于**失效方向**：它表现为"缺陷消失了"，而不是"报错了"。如果这是在真实审计中发生，会让人以为线程泄漏问题被修好了。

**修复**：按**该线程变量**判断（`t.start()` 与 `t.join()` 同名变量配对），拿不到变量名时才退回全文件判据。

修完后：样本上 RES-03 恢复命中（D-10），真实仓库上仍是 1 条（未引入新误报）。

### W15【纪律】每次加测试样本都要看 drift 的"消失"方向

过去几轮我只看 drift 里的 `[+dirty]`（新增命中），默认"消失"是好事。本轮证明**消失更危险**——它可能是规则失效伪装成修复进展。

这与第二轮 `scripts/` 漏扫那次是同一类教训的第三次出现：**工具静默失效 → 表现为"问题变少" → 被误读为修复进展**。

---

## 三、第六轮审计结果

| 指标 | 数值 |
|---|---|
| 原始命中 | 1234 |
| 去重 | -80 |
| 抑制后待办 | **1118**（抑制 36 条） |
| 严重度 | high **61** / medium 735 / low 277 / info 45 |
| 台账 | **F1–F13 全部 still_open**，fixed 空、unknown 空 |
| 跨轮 | `new=1`、`gone=0`、`persistent=1117` |

`new=1` 即 DO-04 新发现的 F13（此前在台账里但不在机器命中里，本轮补齐）。`gone=0`。

门禁：24 个分析器全通过，clean 样本零假阳性，drift 0。

### 剩余 61 条 high

| 规则 | 条数 | 归属 |
|---|---|---|
| RSC-02-recursion-no-budget | 23 | F2 / F6 族 |
| AFS-01-silent-failure | 19 | F7 / F8 族（其中 3 条喂给覆盖写，16 条为只读降级） |
| DO-01 / DO-02 / DO-03 / DO-04 | 4 / 2 / 2 / 1 | F8–F11 + F13（已确证） |
| RSC-05-limit-inconsistency | 3 | F2 |
| CONC-08-partial-lock-coverage | 2 | F3 |
| FO-01-failopen-guard | 2 | F12（已确证）+ 边缘实例 |
| TX-04 / IN-05 / RMW-01 | 各 1 | — |

**high 里 16 条已归属已知缺陷族**（DO-* 9 + FO-01 2 + CONC-08 2 + RSC-05 3）。

---

## 四、缺陷台账（F1–F13，六轮累计）

| ID | 严重度 | 一句话 | 位置 | 优先级 |
|---|---|---|---|---|
| **F8** | high | 读侧返空 + 写侧覆盖 → 清空全部历史（3 处实测） | `af_store.py:343` | **P0** |
| **F10** | high | 凭证三处同形态：损坏后一次签发清空全部 | `af_auth.py:544` | **P0** |
| F1 | high | 扫描器校验顺序倒置，护栏排在遍历之后 | `af_scanner.py:348` | P0 |
| **F12** | high | 别名归属核对 fail-open，保护被静默绕过 | `af_store.py:157` | **P1** |
| F9 | medium | 别名表同形态覆盖 | `af_catalog.py:567` | P1 |
| F6 | medium | Trigger group 嵌套全程无深度上限 | `af_ir/models.py:114` | P1 |
| F2 | medium | 静态遍历不内建预算 | `af_ir/expr.py:255` | P1 |
| **F13** | medium | 偏好整档不可解析 → 压缩后历史丢失 | `af_preference.py:560` | P2 |
| F7 | medium | canary 回滚链双层静默失败 | `af_adapters/ha.py:164` | P2 |
| F3 | medium | 凭证 `_load` 锁外裸写 | `af_auth.py:539` | P2 |
| F4 | low | `_expr_atom` 递归无预算（已降级） | `af_nl_parse.py:629` | P3 |
| F5 | low | `held_by_other` 宽捕 `OSError` | `af_flock.py:132` | P3 |
| F11 | low | 遥测计数同形态覆盖 | `af_catalog.py:499` | P3 |

**按族合并仍是 5 个根因**（与第五轮一致，本轮未新增根因）：

1. **读失败静默降级**（F8/F9/F10/F11/F13）—— 5 组实例、10 处代码
2. **读失败静默放行**（F12 + `assert_deletable` 边缘）
3. **护栏位置/预算缺失**（F1/F2/F6）
4. **锁粒度不一致**（F3）
5. **有意设计与实现不符**（F7）

**破坏性覆盖族（根因 1）本轮已实现机器全覆盖**：四态规则 DO-01/02/03/04 对应 9 处代码，与人工确证 1:1。

---

## 五、本轮新增的可复用经验

| 教训 | 具体情形 | 如何避免 |
|---|---|---|
| 变量遮蔽让规则静默失效 | `analyze(readers=...)` 被函数内同名 `readers` 覆盖 | 参数名与局部名区分；规则跑完先确认"有命中"而不只是"没报错" |
| 全文件正则冒充数据流 | `\.join\(` 全文件搜索 ⇒ 一处字符串 join 屏蔽全部线程泄漏告警 | 判据必须绑到具体变量/对象，不能绑到文件 |
| 判据重叠导致重复上报 | DO-01 与 DO-04 同报 `update_credentials` | 新规则要明确划边界："只管旧规则管不到的那一态" |
| 只读方法被误报为写侧 | `suggest`/`stats` 遍历字段即被判为覆盖写 | 写侧必须要求真实落盘调用 |
| **drift 的"消失"方向更危险** | RES-03 失效表现为"缺陷消失" | 每次看 drift 都要问：消失的那条，是被修好了，还是规则瞎了 |

第三条教训（工具静默失效伪装成修复进展）在本项目已出现三次：第二轮 `scripts/` 漏扫、第四轮 `_atomic_write_text` 别名缺失、本轮 RES-03 全文件正则。建议在 PITFALLS 单列一节。

---

## 六、仍未覆盖（六轮均未改善）

| 工具 | 未覆盖面 |
|---|---|
| semgrep | SAST 交叉验证 |
| detect-secrets | 密钥扫描 |
| pip-audit | **依赖 CVE / 供应链漏洞**（私有 `homesdk` wheel） |
| pytest | 测试运行时探针、变异测试 |
| skill-scan / graph | MCP 配置面、调用图可达性 |

RSC-02 剩余 23 条中除 F2/F6 已知实例外仍可能有未浮出的真缺陷；medium 735 条仍只做抽样确证。

---

## 附：本轮产物

| 文件 | 内容 |
|---|---|
| `round-006/findings.json` | 去重抑制后 1118 条 |
| `round-006/triage.md` | 分诊排序 Top 40（7 信号） |
| `round-006/suppressed.json` | 36 条抑制（含理由与 verdict） |
| `round-006/cross-round-diff.json` | new 1 / gone 0 / persistent 1117 |
| `baseline/bugs.json` | 台账 F1–F13 |
| `core/analyzers/destructive_overwrite_defects.py` | 新增 DO-04 跨作用域规则 |
| `core/analyzers/resource_defects.py` | 修复 RES-03 全文件正则假阴性 |
