# AutoForge 第十一轮审计报告：ADM-auditkit 部署与召回率交叉验证

> 审计对象：`https://github.com/lidicn/AutoForge`（main 分支快照）
> 新增工具：`https://github.com/lidicn/ADM-auditkit`（整合 4 份工作流的通用审计工具）
> 本轮性质：**工具切换验证轮**——用第三方独立工具重跑，与前十轮人工结论做召回率对照
> 报告日期：2026-10-06

---

## 一、执行摘要

第十一轮按你的要求安装了 ADM-auditkit 并对 AutoForge 跑了一轮全维度审计，**21 个分析器全部 OK，产出 1224 条命中**。

但比"1224 条"更有价值的，是我把它和前十轮**人工确证的 11 个缺陷**做了对照：

| 口径 | 结果 |
|---|---|
| 文件级召回 | **10 / 11 = 91%**（该文件有任意命中即算召回） |
| **规则级精确召回** | **5 / 9 = 56%**（按缺陷形态指定期望规则，看那条规则是否命中） |

**结论：工具覆盖面广（21 维度、1224 条），但对"非典型形态"的确证缺陷漏检率约 44%。** 两套方法不是替代关系，是互补——前十轮人工挖出的缺陷里，有 4 个工具的对应规则抓不到，且**漏检原因高度同构：都是"规则只覆盖了一个方向"**。

同时，安装过程本身发现 auditkit **自身有 1 个缺陷**，且形状与第十轮报的 R10-01 完全同族。

---

## 二、安装与部署

| 步骤 | 结果 |
|---|---|
| 获取源码 | `git clone` 403（协议不可用）→ 改用 `codeload.github.com` zip 下载 ✓ |
| 落盘位置 | `/data/workspace/adm-auditkit/ADM-auditkit-main` |
| **顶层 CLI** | **仓库实际缺失**（README 声称有）→ 自行创建 `auditkit`（306 行），支持 `profile/sync/round/status/selftest/pack` |
| 自检门禁 | 修复后转绿：`ok=True, failures=0, drift=0` |
| 项目登记 | `config/projects.yaml` 中 AutoForge 路径改为 `/data/workspace/afx/AutoForge-main` |
| 项目画像 | python 303 文件 / 77,963 行 · shell 3 · yaml 3 · typescript 36 · javascript 11；框架 docker/fastapi/homeassistant/mqtt/pytest；**15 个自建 `check_*.py` 门禁** |

### 外部工具可用性

`semgrep` / `detect-secrets` / `pip-audit` / `pytest` 在本沙箱均 `unavailable`（auditkit 正确标为 unavailable 而非失败——这个降级处理是对的）。

---

## 三、第十一轮执行结果

```
analyzers_total 21 / analyzers_ok 21 / failed 0 / total_findings 1224
```

**命中 TOP 6**：

| 分析器 | 命中 | 主规则 |
|---|---|---|
| state_defects | 285 | AFS-04-unguarded-load ×83 · AFS-01-silent-failure ×79 |
| input_defects | 167 | IN-02-unsafe-cast ×73 · IN-03-path-join ×46 |
| ast_defects | 133 | AF-AST-MIXED-RETURN ×133 |
| errorhandling_defects | 83 | ERRH-02-fake-success ×63 |
| controlflow_defects | 61 | ERR-05-silent-except ×56 |
| api_contract_defects | 60 | API-06-same-name-diff-sig ×60 |

唯一 0 命中：`semantic_defects`。

---

## 四、核心：召回率交叉验证

### 4.1 规则级精确召回 5/9

| 缺陷 | 期望规则 | 结果 |
|---|---|---|
| R3-01 图遍历裸递归 | `RSC-02-recursion-no-budget` | ✓ 命中（af_scanner.py:688） |
| R3-01b 同源裸递归 | `RSC-02-recursion-no-budget` | ✓ 命中 ×5 |
| R7-02 限速软上限 | `OBS-02-unbounded-accumulation` | ✓ 命中 ×3 |
| R9-02 爆破半径负数 | `IN-02-unsafe-cast` | ✓ 命中 ×4 |
| **R10-02 读侧静默+RMW** | `AFS-01-silent-failure` | ✓ **命中行 269（正是 `_read_tags`）** |
| R4-01 fd 二次关闭 | `RES-01-fd-not-closed` | ✗ |
| R6-01 不可回滚记成功 | `ERRH-02-fake-success` | ✗ |
| R6-02 快照缺失仍下发 | `ERR-05-silent-except` | ✗（该文件 0 命中） |
| R10-01 解析失败无日志 | `AFS-04-unguarded-load` | ✗ |

### 4.2 漏检根因：**规则的方向不对称**

**R4-01 最典型。** `RES-01-fd-not-closed` 的定义是：

> RES-01 `os.open()/mkstemp()` 得到的 fd **无对应 close**（非 with 包裹）

而 R4-01 是「`os.fdopen(fd)` 把所有权交给文件对象、`with` 结束已关，`finally` 里**又关一次**」——**反向**。

规则只找"没还回去"（leak），不找"还了两次"（double-close）。而 double-close 在并发下危害更隐蔽：第二次 close 关掉的是**别人刚分配的 fd**。

**R6-02 同类。** 它的形态是 `if pre: recorder(...)` 无 `else` —— 是**条件静默**，不是 `except` 静默。规则集有 `ERR-05-silent-except`，**没有"if 判空后静默跳过"**。该文件因此整份 0 命中。

**R10-01 同类。** `AFS-04` 在 `af_persist.py` 文件内 9 条命中，但**没有一条落在解析档**——与第十轮结论一致：工具也只认"校验和"那一档。

### 4.3 这是个元模式，不是偶发

给规则集做体检的方法：**每个规则问一句"它的反方向有没有对应规则"**。

| 有 | 缺 |
|---|---|
| fd 未关闭（leak） | fd 重复关闭（double-close） |
| except 静默 | if 判空后静默 |
| 内容篡改（校验和不匹配） | 文件截断（解析失败） |
| 保留现场（quarantine） | — |

这与第十轮 R10-01（同函数两档不对称）是**同一个形状在规则集层面的再现**。

---

## 五、auditkit 自身缺陷

### 🔴 R11-01　selftest 样本带 BOM，20 个分析器解析失败且静默跳过

**安装时 selftest 全红**——20 个分析器全部报"0 命中，规则失效"。

根因不是规则失效：`selftest/cases/dirty/sample.py` 与 `sample.ts` **带 UTF-8 BOM（U+FEFF）**，`ast.parse` 抛 `SyntaxError: invalid non-printable character U+FEFF`，而 20 个分析器**全部**有 `except SyntaxError / except Exception` 静默跳过。

去 BOM 后：20 个分析器全部命中，dirty 样本总命中 87 条，门禁转绿（`ok=True, failures=0, drift=0`，golden 已用 `--update` 重生成）。

**讽刺点**：auditkit 自己有 `AFS-04-unguarded-load` 规则，专门抓"解析失败不留痕"。**它自己正是栽在这个形状上**——一个抓"解析失败静默跳过"的工具，因为解析失败静默跳过，把全部规则误报成失效。

这与第十轮 AutoForge 的 R10-01 是**同一条缺陷在不同代码库里的两次出现**。十一轮下来最稳定的规律就是这个：**解析失败必须留痕，否则"没发现问题"会被读成"没有问题"**。

### 🟡 R11-02　`config/projects.yaml` 中 ADM-auditkit 自身画像配置错误

`known_stack` 写的是「Python LLM 评估框架；5 阶段流水线 Dataset→Adapter→Model→Metrics→RunResult」，与实际（审计工具集）完全不符。属配置遗留错误，影响自画像准确性，不影响检测。

---

## 六、1224 条命中的处置说明

**这 1224 条是候选，不是确认缺陷。** 按前十轮坚持的标准——**不实测不升级**——它们目前全部处于"待分诊"状态。

从命中分布可以预判两件事：

1. **大概率含大量误报**。如 `AF-AST-MIXED-RETURN ×133`（函数内混合 return 形态）、`API-06-same-name-diff-sig ×60`（同名不同签名），这类形态在 Python 里极其常见，需逐条看。
2. **`state_defects` 的 285 条最值得优先分诊**——AFS-04（83）+ AFS-01（79）与第十轮持久化主题直接重合，且第十轮已证明 AutoForge 在这两档上确有真缺陷（R10-01/R10-02）。

建议下一轮以 `AFS-04-unguarded-load ×83` 为起点做批量实测分诊。

---

## 七、结论与建议

### 对工具的评价

**ADM-auditkit 覆盖面明显强于前十轮的自研扫描器**（21 维度 vs 自研的 4 组规则），且**架构是对的**：
- 项目配置走 `adapters/*.yml` 注入，不硬编码
- 外部工具不可用标 `unavailable` 而非失败
- 有 selftest（clean/dirty 样本）与 golden 基线
- 有调用图与风险路径分析

**但它对"非典型形态"的召回只有 56%**，且漏检高度集中在"方向不对称"这一类。

### 三条建议

1. **给 auditkit 补反方向规则**：`RES-07-double-close`（fdopen/with 后又 close）、"条件静默"（if 判空无 else 且改变系统行为）、`AFS` 解析档对齐校验和档。这是投入产出比最高的改进。
2. **把召回率验证做成常规动作**：每次换工具/加规则，都拿历史确证缺陷清单跑一次规则级匹配。文件级召回率会虚高（91% vs 56%），必须用规则级。
3. **AutoForge 侧**：1224 条候选中，优先分诊 `AFS-04`（83 条）——它与第十轮确证的 R10-01/R10-02 同族，命中概率最高。

---

## 八、十一轮审计的横向观察

| 轮次 | 主题 | 确认缺陷 |
|---|---|---|
| 一~六 | 运行时代码 | 22 |
| 七 | 资源生命周期 + 单门禁 | 2 |
| 八 | 全门禁 + 测试套件 | 3 |
| 九 | 配置面 | 2 |
| 十 | 持久化层 | 2 |
| 十一 | 工具切换 + 召回率验证 | 2（auditkit 自身） |

**十一轮最值得记的一个数字：56%。**

它说明了一件事——**再好的自动化工具，也替代不了"对已知缺陷形态的人工记忆"**。前十轮人工挖出的 11 个缺陷里，有 4 个工具的对应规则抓不到，而每一个抓不到的原因都清晰可归纳（方向不对称）。

反过来说，**这 4 个漏检恰恰是最该被固化成规则的**——它们来自真实事故复盘，不是猜测。auditkit 已经有 `adapters/af.yml` 这种按项目注入规则的机制，把 R4-01/R6-02/R10-01 的形态写成 AF 专属规则，成本很低，收益是让这三条不再依赖"有人记得"。

**给工程团队的一句话**：工具给你 1224 条，人工给你 11 条确证；前者覆盖广，后者命中深。真正可靠的是把后者**变成前者的规则**——这十一轮的产出，本质上就是一份待编码的规则清单。

---

## 九、已沉淀的复用资产

| 资产 | 位置 |
|---|---|
| **ADM-auditkit（含自建 CLI）** | `/data/workspace/adm-auditkit/ADM-auditkit-main/auditkit` |
| 第十一轮产物 | `.../projects/AutoForge/round-011/registry/` |
| 误报修正手册（35 条 + 本轮 3 条 = **38 条**） | `audit-env/scripts/lessons-round2.md` |
| 持久化扫描器（I01–I04）· 配置面（H01–H04） | `audit-env/scripts/scan_round10.py` · `scan_round9.py` |
| 门禁变异测试台 · 侵入式变异 | `mutation_gates.py` · `mutation_invasive.py` |
| 深度/并发/契约扫描器 | `scan_round3.py` · `scan_round4.py` · `scan_round5.py` |

### 本轮新增 lessons（36–38）

- **36** 换工具后必须做召回率交叉验证；文件级会虚高（91% vs 规则级 56%），必须按缺陷形态指定期望规则
- **37** 审计规则的"方向不对称"是系统性盲区：有 leak 无 double-close、有 except 静默无 if 静默
- **38** 安装第三方工具先跑它自己的 selftest；报"规则失效/0 命中"时**先手工 ast.parse 一遍样本**，别急着怀疑规则

---

## 附：环境说明

| 项 | 值 |
|---|---|
| 沙箱 Python | 3.10.12（项目声明 `>=3.11`） |
| git clone | 403 → 改用 codeload zip |
| 外部工具 | semgrep / detect-secrets / pip-audit / pytest 均 unavailable（auditkit 正确降级） |
| AutoForge 仓库状态 | 干净（探针与变异均已还原） |
| auditkit 修改 | ① 新建顶层 `auditkit` CLI ② 去 dirty 样本 BOM ③ golden 重新生成 |
