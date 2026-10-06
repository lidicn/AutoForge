# AutoForge 第七轮审计报告（自动化实测：新增自动 PoC 阶段）

- **审计对象**：`lidicn/AutoForge`（不变）
- **审计工具链**：`lidicn/ADM-auditkit`（本轮新增第 7 阶段）
- **审计焦点**：稳定性与功能性缺陷（口径同前六轮）
- **本轮轮次**：`round-007`
- **产物**：`/data/workspace/repos/adm/projects/AutoForge/round-007/`
- **报告日期**：2026-10-06

---

## 一、本轮核心：把"实测复现"从人工动作变成流水线能力

前六轮最慢的一环是**人工实测**：静态规则报"递归无预算"，我得手动构造输入、跑一遍、看它崩不崩。一轮只能测几个点。

本轮新增 `core/poc.py` 自动 PoC 生成器，并接入 `auditkit round` 成为**第 7 阶段**：

1. 从 findings 里挑出递归类规则（RSC-02 / RSC-05 / RSC-01 / RSC-03）的命中
2. 解析 AST 拿到函数签名，按参数名推断喂什么结构（`expr`/`node`/`obj`/`data`…）
3. **每个探针在独立子进程里跑**，两种输入：
   - `cyclic`：自引用结构（`a` 包含 `a`）
   - `depth`：深嵌套阶梯 50 / 200 / 1000 / 3000
4. 崩溃即记录 `RecursionError` 与实际崩溃深度；参数构造失败标 `unavailable`（不假报安全）

**关键设计**：`unavailable` 与 `safe` **严格分开**。前六轮踩过太多次"规则静默失效表现为没有命中"，如果构造不出输入就标 `safe`，那和假阴性没区别。

---

## 二、新发现：F14 —— 递归遍历缺环检测，实测 9 个函数崩溃

### 结论

**23 个递归类命中里，9 个对自引用输入崩溃**：

| 位置 | 函数 |
|---|---|
| `af_version.py:130` | `_jsonable` |
| `af_orchestrator.py:383` | `iter_strings` |
| `af_orchestrator.py:424` | `substitute_refs` |
| `af_orchestrator.py:551` | `describe_condition` |
| `af_orchestrator.py:1871` | `_probe_expr` |
| `af_ir/expr.py:255` | `_walk` |
| `af_ir/condition_norm.py:45` | `_nnf` |
| `af_evo.py:156` | `_canon` |
| `af_nl.py:163` | `_expr_text` |

**对照组（实测 safe）**：`expr.py:270 _walk_operand`、`af_expect.py:85 _apply_op`——说明这是**写法问题**，不是普遍现象，修起来有明确正面对照。

### 与 F2/F6 的关系：正交的两道防护

这是本轮最重要的认知修正。F2/F6 讲的是"**深度预算**"（线性深嵌套），F14 讲的是"**环检测**"（循环引用）。实测证明两者独立：

| 防护 | 挡自引用 | 挡 depth=5000 |
|---|---|---|
| 有预算（`check_expr`，`MAX_EXPR_DEPTH=32`） | **能挡**（budget 递减耗尽） | **挡不住**（Python 栈上限 ~1000 先触发） |
| 无预算无 visited（`_walk` / `_jsonable` / …） | **挡不住** | 挡不住 |

所以：
- F2/F6 已知"深嵌套会崩"，但**自引用是另一条崩溃路径**，预算补不上
- F1 之所以危险也在这里：无预算的 `_check_vars` 排在有预算的 `_check_expr` 前面，两道防护只剩半道

**9 处崩溃函数连预算都没有，等于两道防护全缺。**

### 严重度定 medium 的理由

JSON 无法表达循环引用，所以自引用必须**先在内部构造**。这拉低了可达性。但：

- `substitute_refs` / `iter_strings` / `_jsonable` 处理的是**任意对象图**，名字就叫"替换引用"——成环是这类函数的天然风险
- 一旦成环就是**进程级 `RecursionError`**，不是降级
- 修复成本极低：加 visited 集合（按 `id()` 记录当前路径上的容器），或改显式栈迭代

---

## 三、工作流迭代（本轮 2 项）

### W16【新增阶段】自动 PoC 生成器

见第一节。`auditkit round` 从 6 阶段变 7 阶段：

```
[1/7] 自检门禁 → [2/7] 画像 → [3/7] 扫描 → [4/7] 台账复核
    → [5/7] 聚合 → [6/7] 分诊排序 → [7/7] 自动 PoC 实测
```

产物 `poc.json` / `poc.md`。

**调试中修的三个问题**（都已写进代码注释）：

1. **变量未定义**：runner 里用了 `STRUCT_HINT` 但没内联定义，首跑 12 条全部 `inconclusive`。这类 bug 的恶劣之处在于——不崩，只是"结论不明"，看起来像"这些函数可能没问题"。
2. **嵌套函数解析不到**：`af_conflict_runtime.py:139 _walk` 是嵌套函数，从模块顶层 `getattr` 拿不到。resolve 补了类内/嵌套函数查找。
3. **异常分类**：`LookupError`/`AttributeError` 归 `unavailable` 而非崩溃——参数构造失败不等于被测函数有缺陷。

### W17【去重】PoC 目标按 (file, line, function) 去重

findings 里同一处代码可能有多条命中（不同规则、跨根重复）。不去重的话同一函数会被测两遍，结论表出现两行完全一样的记录，看着像发现了两个缺陷。

---

## 四、第七轮审计结果

| 指标 | 数值 |
|---|---|
| 原始命中 | 1234 |
| 去重 | -80 |
| 抑制后待办 | **1118**（抑制 36 条） |
| 严重度 | high **61** / medium 735 / low 277 / info 45 |
| 台账 | **F1–F14 全部 still_open**，fixed 空、unknown 空 |
| 跨轮 | `new=0`、`gone=0`、`persistent=1118` |
| PoC | 23 个目标：cyclic_crash **9** / safe 2 / unavailable 9 / inconclusive 3 |

**`new=0`、`gone=0`**：本轮没有改任何分析器规则（只加了流水线阶段），所以静态命中与前一轮完全一致——这本身是个正面信号，说明扫描基线稳定。

### PoC 未覆盖的 9 条 `unavailable`

多为嵌套函数（如 `af_conflict_runtime.py:139 _walk`）或参数形状复杂无法自动构造。它们**没有被判为安全**，只是本次没能测到。人工若要看这几条，需自行构造调用上下文。

### 剩余 61 条 high

| 规则 | 条数 | 归属 |
|---|---|---|
| RSC-02-recursion-no-budget | 23 | F2 / F6 / **F14**（其中 9 条已实测崩溃） |
| AFS-01-silent-failure | 19 | F7 / F8 族 |
| DO-01 / DO-02 / DO-03 / DO-04 | 4 / 2 / 2 / 1 | F8–F11 + F13 |
| RSC-05-limit-inconsistency | 3 | F2 |
| CONC-08-partial-lock-coverage | 2 | F3 |
| FO-01-failopen-guard | 2 | F12 |
| TX-04 / IN-05 / RMW-01 | 各 1 | — |

---

## 五、缺陷台账（F1–F14，七轮累计）

| ID | 严重度 | 一句话 | 优先级 |
|---|---|---|---|
| **F8** | high | 读侧返空 + 写侧覆盖 → 清空全部历史（3 处实测） | **P0** |
| **F10** | high | 凭证三处同形态：损坏后一次签发清空全部 | **P0** |
| F1 | high | 扫描器校验顺序倒置，护栏排在遍历之后 | P0 |
| **F12** | high | 别名归属核对 fail-open，保护被静默绕过 | **P1** |
| F9 | medium | 别名表同形态覆盖 | P1 |
| F6 | medium | Trigger group 嵌套全程无深度上限 | P1 |
| F2 | medium | 静态遍历不内建预算 | P1 |
| **F14** | medium | 递归遍历缺环检测，实测 9 个函数自引用即崩 | P2 |
| F13 | medium | 偏好整档不可解析 → 压缩后历史丢失 | P2 |
| F7 | medium | canary 回滚链双层静默失败 | P2 |
| F3 | medium | 凭证 `_load` 锁外裸写 | P2 |
| F4 | low | `_expr_atom` 递归无预算（已降级） | P3 |
| F5 | low | `held_by_other` 宽捕 `OSError` | P3 |
| F11 | low | 遥测计数同形态覆盖 | P3 |

**按族合并仍是 5 个根因**（F14 归入根因 3「护栏缺失」，但作为独立维度单列）：

1. **读失败静默降级**（F8/F9/F10/F11/F13）—— 10 处代码
2. **读失败静默放行**（F12）
3. **护栏缺失/错位**（F1/F2/F6/**F14**）—— 本轮新增环检测这一子维度
4. **锁粒度不一致**（F3）
5. **有意设计与实现不符**（F7）

---

## 六、仍未覆盖（七轮均未改善）

| 工具 | 未覆盖面 |
|---|---|
| semgrep | SAST 交叉验证 |
| detect-secrets | 密钥扫描 |
| pip-audit | **依赖 CVE / 供应链漏洞**（私有 `homesdk` wheel） |
| pytest | 测试运行时探针、变异测试 |
| skill-scan / graph | MCP 配置面、调用图可达性 |

PoC 本轮只覆盖**递归类**规则（RSC-*）。破坏性覆盖族（DO-*）与失败开放族（FO-01）**尚未接入自动实测**——它们需要构造文件损坏场景，比递归探针复杂，列为下一轮方向。

PoC 的 9 条 `unavailable` 需人工构造调用上下文；`inconclusive` 3 条（`af_draft._resolve_expr`、`af_scheduler._satisfied`、`af_version.diff`）参数形状待确认。

---

## 附：本轮产物

| 文件 | 内容 |
|---|---|
| `round-007/findings.json` | 去重抑制后 1118 条 |
| `round-007/poc.md` | 自动 PoC 实测结论表（23 个目标） |
| `round-007/poc.json` | 每条的探针明细与 verdict |
| `round-007/triage.md` | 分诊排序 Top 40 |
| `round-007/cross-round-diff.json` | new 0 / gone 0 / persistent 1118 |
| `baseline/bugs.json` | 台账 F1–F14 |
| `core/poc.py` | 本轮新增自动 PoC 生成器 |
| `auditkit` | 流水线扩为 7 阶段 |
