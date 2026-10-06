# AutoForge 审计报告 · 第十五轮

- **审计对象**：`lidicn/AutoForge`
- **审计工具链**：`lidicn/ADM-auditkit`
- **本轮轮次**：`round-015`
- **产物**：`/data/workspace/repos/adm/projects/AutoForge/round-015/`
- **报告日期**：2026-10-07

---

## 一句话结论

**F15 完成"静态命中 → 实测确证 → 修复 → 回归"闭环；并把审计面第一次扩展到仓库随附的私有依赖源码，在那里发现 F16——homesdk 的三个递归函数没有深度预算，可以打崩 AutoForge 自己的 CI 门禁。**

---

## 一、F15：从静态命中到端到端实测确证

上一轮 F15 只有静态证据（OUTB-01 ×4 + bandit B310 ×2）。本轮新增 `core/poc_outbound.py`，起一个本地 HTTP 服务做重定向靶子，实测：

| 场景 | 结果 |
|---|---|
| 原仓库直连 `urlopen` | **跟随重定向到白名单外主机，拿到 `__F15_EVIL__`** ⇒ `bypass_confirmed_unfixed` |
| 补丁副本 `guarded_urlopen` | 拦截跨主机重定向 |
| 同主机重定向 | 正常放行（不误伤） |

这补上了前十四轮一直缺的一类证据：**此前所有缺陷的确证都靠"崩没崩""数据丢没丢"，F15 是第一个"安全护栏是否真的拦住"的行为级 PoC**。

### 修复（补丁副本）

`af_adapters/http.py` 新增 `build_guarded_opener()` / `guarded_urlopen()`：

- `ALLOWED_SCHEMES = ("http", "https")` —— 挡 `file://` 等
- netloc 含 `@` 直接拒绝（凭证注入）
- 重定向重过白名单（沿用 `_WhitelistRedirector` 的语义）

4 处调用点全部改用它，`allowed_hosts` 取各自 `host_of(ha_url / ma_url / base_url)`：

| 位置 | 原写法 |
|---|---|
| `af_catalog.py:434` | `_u.urlopen(...)` |
| `af_live.py:235` | `opener or urllib.request.urlopen` |
| `af_metrics.py:197` | `urllib.request.urlopen(...)` |
| `af_registry.py:321` | `(opener or urllib.request.urlopen)(...)` |

补丁副本 OUTB-01 静态命中 **4 → 0**。

**关键设计点**：不新写一套护栏，而是把已有护栏（`_WhitelistRedirector`）**包成一个可直接替换 `urlopen` 的函数**——这样调用点改动是一行，且不会长出第二套口径不一致的白名单。

---

## 二、F16：homesdk 递归无预算，可打崩 AutoForge 的 CI 门禁

仓库随附私有 wheel `docker/homesdk/homesdk-0.3.1-py3-none-any.whl`（未上 PyPI）。三个自递归函数无深度预算：

| 函数 | 位置 |
|---|---|
| `_numeric_literal` | `homesdk/gates/scan.py:375` |
| `_dotted` | `homesdk/gates/scan.py:385` |
| `_literal_secret` | `homesdk/gates/scan.py:405` |

**实测**：`ast.parse` 能解析 `'-'×1000` 与 `a.b` 重复 1000 次的源码（解析器自己不崩），但 `homesdk.gates.scan_file` 端到端 **RecursionError**。

**影响面不是"homesdk 自己会崩"——是 AutoForge 的 `gates.sh` 第 38 / 51 / 248 行直接调用 `python -m homesdk.gates` 作为 CI 门禁。** 也就是说，含深嵌套表达式的源码文件可以让 AutoForge 的 CI 门禁整体崩溃。

**与 F14 的关系**：同族不同道。F14 缺环检测，F16 缺深度预算。第十三轮在 F14 补丁上实测过两者正交——只装环检测，9 个崩溃变成 8 个深嵌套崩溃。这次在第三方依赖里又撞见同一模式的另一半。

homesdk 全部命中 **58 条**（high 3 / medium 43 / low 12），其中 high 就是这三条 RSC-02。

---

## 三、工作流迭代（4 项）

### W39：依赖源码审计（`scripts/audit_deps.py`）

前十四轮只扫 `src/`。新增脚本：解包仓库随附的 wheel/目录，用**同一套分析器**跑一遍，命中带 `origin=dependency` 标记。

为什么必须做：F16 的触发点是 AutoForge 自己的 CI 脚本，但缺陷代码在依赖里。**只扫自家源码，永远发现不了"我的门禁被我的依赖打崩"。**

### W40：OUTB-01 分级

新增 `SCHEME_GUARD_PAT`——只校验 scheme、不校验主机的直连，降为 `low` 并注明"仅校验 scheme，未校验主机，且默认跟随 3xx 重定向"。避免把"做了半个防护"和"完全没防护"混为一谈。

### auditkit 8 阶段 → 10 阶段

新增：

- `[9/10]` 出站 PoC（`core/poc_outbound.py`）
- `[10/10]` 依赖源码审计（`scripts/audit_deps.py`）

两者均独立容错，失败不影响前面阶段。

### 门禁 28 → 30 项

新增 `poc_outbound`（判据：原仓库至少 1 条 confirmed）与 `dep_audit`（判据：至少扫到 1 个依赖包）。

调试时踩的坑值得记：**两个新检查都按 stdout 判空，结果一个拿不到 `results`、一个把 list 当 int 取 `len()` 抛 TypeError**。改成读落盘 JSON 后才正常。这跟"规则静默失效表现为没有命中"是同一类——只要检查逻辑有一步没走通，它就不会报错，只会安静地告诉你"没问题"。

---

## 四、正式审计结果

| 指标 | 数值 |
|---|---|
| 分析器 | 26/26 ok（门禁 **30** 项全过） |
| 原始命中 | **1083** |
| 去重后待办 | **1052**（抑制 4 条） |
| 严重度 | high **85** / medium 682 / low 285 |
| 跨轮 diff | **new 0 / gone 0** |
| 台账 | **F1–F16 全部 still_open** |
| 递归 PoC | cyclic_crash 9 / safe 2 / unavailable 9 / inconclusive 4 |
| 状态 PoC | 9/9 data_lost |
| 出站 PoC | bypass_confirmed_unfixed（原仓库） |
| 依赖审计 | homesdk 58 条（high 3 / medium 43 / low 12） |

门禁：`ok=True`、analyzers=30、failures=[]、drift=0；extras 全绿（patch_lint 11 文件 0 问题、poc_outbound 1/1 confirmed、dep_audit 1 包 58 命中）。

**new 0 / gone 0 是合理的**：本轮新增的是 PoC 与依赖审计脚本，没改任何分析器规则，所以主仓库命中面应与上轮完全一致——这个"一致"本身就是基线稳定的证据。

---

## 五、一个必须说明的方法问题

F16 的台账 `file` 字段一度写的是 wheel 内路径，导致台账复核把它判成 `unknown`（文件不存在）。已把解包目录固定到 `projects/AutoForge/deps/homesdk-0.3.1/` 使其可核验。

这件事的教训：**台账里"文件不存在"和"缺陷已修好"在机器上长得一模一样——都是指纹不命中。** 前者是台账写错了，后者是真的修好了，必须分开，否则会让后续每轮都误判。

---

## 六、仍未覆盖

| 工具 | 未覆盖面 | 状态 |
|---|---|---|
| pip-audit | 依赖 CVE / 供应链漏洞 | ❌ 网络策略阻断（osv.dev、pypi.org 均 HTTP 403） |
| semgrep | SAST 交叉验证 | ❌ 安装失败（无 CLI） |
| — | homesdk 运行时探针 | ❌ wheel 要求 Python>=3.11，环境为 3.10，无法安装，只能静态审计 |
| pytest | 测试运行时探针、变异测试 | ❌ 未接入 |

**依赖 CVE 面十五轮都没扫**，`homesdk` 是未上 PyPI 的私有 wheel——这是"风险未知"，不是"无风险"。

F1–F7、F9、F11、F12、F16 未纳入补丁。F8/F10/F13/F14/F15 补丁只存在于只读副本 `/data/workspace/repos/af-patched`，**原仓库未改动**。

递归 PoC 仍有 9 条 `unavailable`、4 条 `inconclusive`；其中相当一部分成因现已查明——**模块因缺 `fastapi` / `typer` / `homesdk` 压根导入不了**，不是探针构造不出输入。
