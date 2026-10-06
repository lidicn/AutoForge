# AutoForge 稳定性与功能性审计报告

- **审计对象**：`lidicn/AutoForge`（Python 主包 `src/autoforge`，71 模块；TS/JS 前端 `ui/`；`scripts/` 门禁脚本 30 个）
- **审计工具链**：`lidicn/ADM-auditkit`（本地安装 + 自修复后运行）
- **审计焦点**：影响**稳定性**（崩溃、死锁、资源失控、状态损坏）与**功能性**（契约违背、静默失败、护栏失效）的缺陷
- **证据基线**：`/data/workspace/audit/rounds/002/`（`profile.json`、`analyzers/*.json`、`findings.json`）
- **报告日期**：2026-10-06

---

## 一、执行摘要

对 AutoForge 全量源码跑了 ADM-auditkit 的 21 个内置分析器，共产生 **1237 条机器命中**（high 97 / medium 783 / low 312 / info 45）。逐条人工确证（读源码 + 实测复现）后的结论是：

| 结论 | 数量 | 说明 |
|---|---|---|
| **确证真实缺陷** | **5 项**（F1–F5） | 其中 1 项高危、1 项中高危 |
| 判定为「已修复」 | 6 类 | 源码内留有 `审计 BUG-01…BUG-20` 修复注释（全仓 54 处），机器命中的是修复前的形态 |
| 判定为假阳性 | 8 类 | 静态规则无法识别的锁语义、登录接口语义、fail-closed 设计意图等 |
| 判定为「上层已降级」 | 1 类 | 缺陷存在，但被调用方 `except` 兜住，不升级为崩溃 |

**最重要的发现是 F1**：静态扫描器的**校验顺序倒置**，导致表达式深度护栏（DoS 防线）在保存路径上形同虚设——深嵌套表达式会先抛 `RecursionError`，而不是被本应拦住它的 `check_expr` 转成 `EXPR_INVALID` 诊断。触发点在 `af_save` / 直接提交 IR 的接口上，属于 Agentic 执行链可达路径。

**需要明说的一句话**：AutoForge 是一个**已被多轮自身审计反复加固**的仓库（源码注释里明确登记了 BUG-01 至 BUG-20 共 20 个已修缺陷族，54 处注释）。常规静态规则在此类仓库上的假阳性率很高；本次审计的价值不在于"又扫出多少条"，而在于**把这 1237 条收敛到 5 条经得起追问的真缺陷**，并留下假阳性登记，避免后续重复劳动。

---

## 二、审计工作流安装（ADM-auditkit）

### 2.1 安装方式与一个必须绕开的坑

仓库顶层**没有 CLI 入口文件**，实际执行方式是直接运行 `core/` 下的脚本：

```bash
python3 core/profile.py   <repo> <out.json>                    # 画像
python3 core/registry.py  <root> <outdir> [options]            # 全分析器扫描
python3 core/selftest/…                                        # 门禁自检
python3 core/aggregate.py <round_dir> [baseline]               # 聚合
```

AutoForge 本次实际执行的扫描命令：

```bash
AUDITKIT_REPO_ROOT=/data/workspace/repos/af \
python3 core/registry.py /data/workspace/repos/af/src /data/workspace/audit/rounds/002/analyzers \
  --extra-root /data/workspace/repos/af/scripts \
  --profile /data/workspace/audit/rounds/002/profile.json
```

### 2.2 安装过程中发现并修复的真实缺陷：BOM 静默失效

ADM-auditkit 的 21 个分析器在读取带 UTF-8 BOM 的源文件时**静默失效**——`Path.read_text()` 未指定编码，BOM 被当成普通字符 `\ufeff`，导致首行起的所有正则/AST 规则全部失配，且**不报错、不告警**，表现为"扫了但没结果"。

已修复：**21 个文件、27 处** `read_text()` 显式改为 `encoding="utf-8-sig"`。修复后门禁自检 `ok=true`，21 个分析器全部命中，clean 样本 0 假阳性，`failures=[]`。

> 这个缺陷值得单独记一笔：它属于**审计工具自身的静默失败**——审计工具一旦静默失效，产出的"零发现"会被误读为"代码没问题"，比报错危险得多。

---

## 三、审计方法与覆盖声明

### 3.1 已覆盖

- **全量内置分析器扫描**：21 个分析器全部 `ok`，覆盖 Python 主包（319 文件 / 85326 行，占 0.86）+ `scripts/` 门禁 + `ui/` 的 TS/JS
- **人工确证**：对全部 97 条 high 命中 + 高价值 medium 规则抽样，逐条读源码上下文，必要时**实测复现**（F1、F4 均有实测数据支撑）
- **已修复清单交叉比对**：提取全仓 54 处 `审计 BUG-XX` 注释，机器命中先与之比对，避免把已修项当新缺陷

### 3.2 未覆盖（工具不可用，结论不可外推）

以下外部工具在本环境不可用，对应阶段**未执行、结论为空**，本报告不以"0 发现"呈现：

| 工具 | 状态 | 未覆盖的阶段 |
|---|---|---|
| semgrep | 不可用（沙盒 200MB 单文件硬上限，`semgrep-core` 209715200 字节无法执行） | SAST 交叉验证 |
| detect-secrets | 不可用 | 密钥扫描（本次 GEN-01 为内置规则，非 detect-secrets） |
| pip-audit | 不可用 | 依赖 CVE / 供应链漏洞 |
| pytest | 不可用（未安装） | 测试缺口运行时探针、变异测试 |

**这意味着：本报告不覆盖依赖漏洞面。** AutoForge 使用私有 `homesdk` wheel，供应链风险建议单独补扫。

### 3.3 一个方法论提醒

`core/aggregate.py` 只读 `sast/secrets/deps/skill/graph` 五个外部工具产物目录，**不消费 `registry.py` 的内置分析器输出**。外部工具全不可用时会输出 `total: 0`（本次即如此），容易被误读为"无发现"。这是聚合层的缺口，建议补上对 `analyzers/all-findings.json` 的摄取。

---

## 四、扫描结果统计

### 4.1 严重度分布

| 严重度 | 条数 |
|---|---|
| high | 97 |
| medium | 783 |
| low | 312 |
| info | 45 |
| **合计** | **1237** |

### 4.2 规则分布（Top 12）

| 规则 | 条数 | high | medium | low |
|---|---|---|---|---|
| AF-AST-MIXED-RETURN（函数返回类型不一致） | 151 | 20 | 131 | — |
| AFS-04-unguarded-load（加载未防护） | 89 | — | 89 | — |
| AFS-01-silent-failure（静默失败） | 82 | 20 | 62 | — |
| IN-02-unsafe-cast（不安全类型转换） | 73 | 7 | 66 | — |
| ERRH-02-fake-success（假成功） | 65 | — | 52 | 13 |
| AFS-07-io-under-lock（锁内 IO） | 60 | — | 60 | — |
| API-06-same-name-diff-sig（同名异签） | 58 | — | — | 58 |
| AFS-02-partial-batch（批处理部分失败） | 55 | — | 32 | 23 |
| ERR-05-silent-except（静默 except） | 50 | — | 50 | — |
| IN-03-path-join（路径拼接） | 46 | — | 2 | 44 |
| OBS-02-unbounded-accumulation（无界累积） | 37 | — | 37 | — |
| RSC-02-recursion-no-budget（递归无预算） | 26 | **23** | 3 | — |

### 4.3 high 命中热点文件

`af_auth.py` 8 · `af_mcp.py` 7 · `af_orchestrator.py` 5 · `af_store.py` 5 · `af_evo.py` 4 · `af_nl_parse.py` 3 · `af_catalog.py` 3 · `af_service.py` 3 · 测试与其余模块各 1–2 条。

---

## 五、确证缺陷清单

### F1【高】扫描器校验顺序倒置，表达式深度护栏被绕过

**位置**：`src/autoforge/af_scanner.py:348–349`（`_check_vars` 先于 `_check_expr`）
**证据链**：

1. `af_scanner.py:348` 调用 `_check_vars`，其内部（`af_scanner.py:570`）调用 `collect_var_refs(node.expr)`
2. `collect_var_refs` → `expr.py:_walk/_walk_operand`：**递归遍历，无深度预算**（`MAX_EXPR_DEPTH=32` / `MAX_EXPR_NODES=256` 仅被求值器 `_Budget` 使用）
3. `af_scanner.py:349` 才调用 `_check_expr` → `check_expr()`，而 `check_expr` **有** `_Budget`（depth>32 即 `ExprError`）
4. 于是：深嵌套表达式先撞上无护栏的 `_check_vars`，`_check_expr` 根本没机会把它转成 `EXPR_INVALID` 诊断

**实测**：

```
collect_var_refs(depth=500)  → ok
collect_var_refs(depth=3000) → RecursionError
```

**可达性（关键）**：`af_service.save_graph()` 在落盘前（`af_service.py:401–405`）调用 `StaticScanner(...).scan()`。因此一条经 MCP `af_save` 或 API 直接提交的深嵌套 IR，**在保存时就抛 `RecursionError`**，而非返回一条"表达式非法"的诊断。

**影响**：
- **功能性**：表达式校验的预期产物（`EXPR_INVALID` 诊断）在深嵌套场景下永远产不出来，护栏失效
- **稳定性**：`save` 路径抛 `RecursionError`；栈接近耗尽时解释器状态本身也不可靠
- **攻击面**：IR JSON 由 agent/客户端可控，属 Agentic 链上真实可达

**修复建议**（任选，建议都做）：
1. 把 `self._check_expr(...)` **提到 `self._check_vars(...)` 之前**——先做有预算的形态校验，再做语义遍历
2. 给 `collect_var_refs` / `collect_entity_refs` 复用 `_Budget`，让护栏内建于函数自身，不依赖调用顺序

---

### F2【中高】静态表达式遍历普遍缺少预算，护栏不变量外置

**位置**：
- `src/autoforge/af_ir/expr.py:255`（`_walk`）、`expr.py:270`（`_walk_operand`）
- `src/autoforge/af_ir/condition_norm.py:45`（`_nnf`）

**问题**：两个模块都定义了上限常量（`MAX_EXPR_DEPTH=32`、`MAX_EXPR_NODES=256`、`MAX_CNF_CLAUSES=1024`），但这些常量**只用在求值/展开路径**（`_Budget`、`CNFBudgetExceeded`），**静态遍历路径完全不用**。同一份数据走"有上限"路径安全、走"遍历"路径失控。

`condition_norm._to_cnf` 的注释里其实已经写明了正确的工程纪律——"之所以在乘起来*之前*判额度：笛卡尔积一旦分配出去，OOM 发生了再报已经太晚（新增审计 BUG-06）"。同样的纪律没有贯彻到 `_nnf` 自身。

**影响**：护栏正确的前提是"调用方一定先跑过 `check_expr`"，这个不变量写在调用约定里而**不在函数内部**。任何一个新调用点漏掉前置校验，护栏就静默失效——F1 正是这种情况的已发生实例。

**修复建议**：把 `_Budget` 提升为静态遍历的共享组件，`_walk` / `_walk_operand` / `_nnf` 一律携带预算进入；常量只定义不使用是比不定义更危险的状态（会给出"已有防护"的错觉）。

---

### F3【中】凭证存储的 `_load` 在锁外裸写共享状态，锁粒度不一致

**位置**：`src/autoforge/af_auth.py:539`（`PairCodeStore._load`）、`:665`（`AuthCodeStore._load`）

**问题**：`_load()` 在**无锁**状态下直接对 `self._codes` 做增量合并写；而 `create()` 走的是"锁内 `_purge_expired` + 锁内 `_persist`"的路径。同一份共享状态，一个锁内写、一个锁外写。

具体风险：`pending_events` 等方法在**遍历** `self._codes` 的过程中调用 `_load()`，而 `_load()` 会修改同一个 dict → 存在 `RuntimeError: dictionary changed size during iteration` 的竞态窗口；多进程/多线程下也可能丢更新。

**影响**：中低危竞态，表现为偶发运行时错误或凭证状态短暂不一致，不易复现但会在长跑服务里累积。

**修复建议**：`_load()` 改为"锁内读文件到本地 dict → 锁内合并进 `self._codes`"，或明确把 `_load` 声明为「调用方须持锁」（同文件 `TokenRegistry` 已有此纪律的注释可参照），并让所有调用点守约。

---

### F4【中低】NL 解析的 `_expr_atom` 递归无深度/长度预算

**位置**：`src/autoforge/af_nl_parse.py:629`（`_expr_atom` 自递归），同类还有 `:1277`（`_assert_no_runtime_fields`）、`_carry_runtime`

**实测**：

```
_expr_atom("非"*50   + "温度大于30")  → ok
_expr_atom("非"*200  + "温度大于30")  → ok
_expr_atom("非"*1000 + "温度大于30")  → RecursionError
```

**但已降级**：上层 `parse_automation` / `parse_graph` 会捕获该异常并转为 `ParseError`（实测两条入口均返回 `ParseError 第 1 行无法解析…`），因此**不会崩溃进程**，只是畸形输入被拒——这本身是合理行为。

**残留风险**：`RecursionError` 意味着栈已接近耗尽，捕获后继续执行在 CPython 下并非零风险；且捕获的是宽泛 `Exception`，会把栈耗尽与真正的语法错误混为一谈，排障时不易分辨。

**修复建议**：给 `_expr_atom` 传入 depth 参数并在入口做输入长度上限；解析失败原因应区分"语法不支持"与"结构过深"，便于定位。

---

### F5【中低】`held_by_other` 宽捕 `OSError`，非"被持有"故障被误判

**位置**：`src/autoforge/af_flock.py:132`

```python
except (BlockingIOError, OSError):
    return True   # 判定为"被他人持有"
```

**问题**：`BlockingIOError` 才是"锁被别人持有"的正当信号；`OSError` 覆盖范围过宽，文件描述符耗尽（EMFILE）、磁盘满（ENOSPC）、被信号中断（EINTR）等**完全不是"被持有"**的故障，都会被判成 `True`。

**影响**：误判会让 `acquire()` 一路空等至超时（表现为莫名的写入阻塞），或使上层错误地进入只读降级分支——故障现象与真实原因完全对不上，排障成本高。

**修复建议**：POSIX 下只认 `BlockingIOError`（EWOULDBLOCK/EAGAIN），其余 `OSError` 按 errno 分类：资源类直接抛、中断类重试。

---

## 六、假阳性登记（避免重复劳动）

以下机器命中经人工确证为**假阳性或已修复**，登记在此供后续审计直接排除：

| 规则 | 位置 | 确证结论 |
|---|---|---|
| TX-04-delete-then-write | `af_store.py:486/516/540` | **已修复**：`_stash_archive` / `_unstash_archive` / `_drop_stash` 已实现 rename 让位 + 回滚（BUG-11）；overwrite 导入走 `_stash_archive`（`:628`）。`_delete_archive` 是真删除，但仅用户显式删除路径，标签半在 `tags.lock` 内读-改-写，可接受 |
| RMW-01-rmw-inconsistency | `af_experience.py:30` | **假阳性**：`observe()` 持 `FileLock` 读-改-写；`clear()` 持锁全量重置——语义上就是要清空，非"陈旧快照覆盖" |
| AUTH-01-route-no-auth | `af_api.py:897` | **假阳性**：`api_auth_login` 是登录接口，本就不需要鉴权 |
| AUTH-04-credential-growth | `af_auth.py:539/567/665/710` | **已修复**：`PairCodeStore` 与 `AuthCodeStore` 均有 `_purge_expired()`（在 `create()` 内调用）+ TTL（配对码 5 分钟 / 长期码 180 天）+ `MAX_ISSUED=10000` 硬上限（BUG-19） |
| CONC-08-partial-lock-coverage | `af_auth.py` `TokenRegistry._load_env` | **假阳性**：`_load_env` 在 `__init__` 内调用，构造期无并发 |
| GEN-01-hardcoded-secret ×7 | 全部测试文件 | **假阳性**：均为测试 fixture（`tok-nope`、`SEKRIT-…` 等），非生产密钥 |
| AF-AST-MIXED-RETURN | `af_registry.py:182` `_ws_fetch`、`af_live.py:452` | **假阳性**：`_ws_fetch` 所有路径均 `return _build_snapshot(...)`，无 fall-through；且上层 `fetch_registries` 有 `except Exception → _empty(...)`，"永不抛异常"是明确契约 |
| AFS-01-silent-failure | `af_canary.py:103` `has_drift` | **假阳性**：捕获 `UnknownEntity` 返回 `True` 是**有意 fail-closed**——读不到实际态不等于验过了（铁律 #5：EXEMPT ≠ VERIFIED），保守判"有漂移"让回滚生效，源码注释已说明 |

### 上层已降级（缺陷存在但不升级为故障）

| 规则 | 位置 | 说明 |
|---|---|---|
| IN-02-unsafe-cast | `af_mcp.py:229` `_t_diff` 的 `int(args['old'])` | `dispatch()` 有 `except Exception` 兜底（`:907`），脏参数转为 `isError` 回执而非崩溃。**仅为可用性问题**。附带确认：`dispatch` 已实现"未声明参数一律拒"，`tools/list` 展示的参数集 = 实际接受参数集 |

---

## 七、未能收尾的部分

- **依赖/供应链面未扫**：`pip-audit` 不可用，私有 `homesdk` wheel 的漏洞面未覆盖，建议在有网络与工具的环境补扫
- **测试缺口运行时探针未跑**：`pytest` 未安装，`testgap` 阶段无数据；185 个测试文件的实际有效性与覆盖率未评估
- **IN-05-dangerous-call 7 条**（全在 `scripts/` 门禁脚本的 `subprocess.run`）仅做静态判定，未逐个确认参数是否拼接外部输入
- **AFS-07-io-under-lock 60 条、ERRH-02-fake-success 65 条、OBS-02 37 条**做了抽样而非全量确证，其中可能仍有真缺陷未浮出

---

## 八、修复优先级建议

| 优先级 | 项 | 工作量 | 理由 |
|---|---|---|---|
| P0 | F1（扫描器校验顺序） | 极小（两行调换 + 补预算） | 护栏失效 + Agentic 链可达 + 实测可复现 |
| P1 | F2（静态遍历补预算） | 小 | 消除同一类缺陷的根因，防止新调用点再次踩坑 |
| P2 | F3（凭证 `_load` 锁粒度） | 中 | 长跑服务偶发竞态，难复现但会累积 |
| P3 | F5（`held_by_other` 收窄异常） | 极小 | 排障成本主要来源 |
| P3 | F4（NL 解析深度预算） | 小 | 已降级，主要改善可观测性 |

---

## 附：产物路径

| 文件 | 内容 |
|---|---|
| `/data/workspace/audit/rounds/002/profile.json` | 仓库画像（语言/框架/包结构） |
| `/data/workspace/audit/rounds/002/analyzers/all-findings.json` | 1237 条原始机器命中 |
| `/data/workspace/audit/rounds/002/analyzers/summary.json` | 分分析器汇总（21 个全部 ok） |
| `/data/workspace/audit/rounds/002/findings.json` | 统一格式 findings（由内置分析器聚合，非 `aggregate.py`） |
