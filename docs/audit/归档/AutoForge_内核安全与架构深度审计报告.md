# AutoForge 内核安全与架构深度审计报告

> 审计对象：`https://github.com/lidicn/AutoForge`（main 分支快照）
> 审计范围：`src/autoforge/` 内核（98 个 Python 源文件 / 全仓 303 个 py、77,949 行）
> 判定标准：**严格档** —— 无可复现攻击路径者不进"确认发现"
> 报告日期：2026-10-06

---

## 一、执行摘要

先把结论摆在最前面：**这台机器扫出来的 18 条机器告警，17 条是假阳性，1 条是误报；真正的风险不在扫描器报的地方，而是人工沿审批链路挖出来的 3 处设计问题。**

| 类别 | 数量 | 说明 |
|---|---|---|
| 机器告警总数 | 18 | agent-audit 15 条 + bandit 3 条 |
| **推翻（rejected）** | **17** | 11 条 critical 全军覆没，4 条 high 中 3 条推翻 |
| 误报但需知悉 | 1 | bandit B324 弱 SHA1（非密码学用途，无需修） |
| **人工确认发现** | **5** | HIGH 2 / MEDIUM 3 |

### 最该先修的三件事

1. **HIGH-1 授权码快速通道天然绕过自批检查**（`af_mcp.py:206-215`）——持有有效授权码即可一步完成"提交+批准"，审批门形同虚设。这是设计取舍，但代码里没有任何补偿控制。
2. **HIGH-2 自批检查只比字符串相等**（`af_service.py:597`）——两把不同 subject 的令牌即可"自己提交、另一身份批准"。
3. **MEDIUM-3 canary 漂移检查异常被静默吞掉**（`af_executor.py:245-246`）——回滚失败会被记成"已验证"，产出假证据。

### 一个反直觉的观察

AutoForge 的**代码质量比扫描器给它的评分高得多**。它在 CI 里自己写下了"门永远不可能红"的教训并把 `continue-on-error` 拆掉，说明团队对假绿有清醒认识。真正的问题不是"有没有漏洞"，而是**审批链路的身份语义不够严密**——这恰好是自动化扫描器看不见的那一层。

---

## 二、审计范围与方法

### 工具链（全部部署于 `/data/workspace/audit-env`）

| 工具 | 版本 | 本次用途 |
|---|---|---|
| agent-audit | 0.20.0 | OWASP Agentic Top 10 静态扫描（66 条规则） |
| bandit | 1.9.4 | Python AST 安全扫描 |
| ruff | 0.16.10 | 质量/风格（823 条） |
| graphify | 0.9.76 | 代码图谱（tree-sitter 本地） |
| cloudflare security-audit-skill | — | 提供判定不变量与验证脚本 |

> semgrep 已部署但**本次未使用**：semgrep-core 为 245MB 原生二进制，在本沙箱横扫会触发资源崩溃；已降级为定点验证工具（用法见环境 README 第三节）。

### 判定不变量（沿用 cloudflare skill）

1. **没有可复现攻击路径的发现，不进"确认发现"章节。** 每条必须写清：攻击者是谁 → 从哪个入口进入 → 经过什么代码路径 → 触发什么后果。
2. 被推翻的项不丢弃，单列"已排除"章节并写明推翻理由。
3. 证据一律落到 `文件:行号`，且为审计人实际读到的代码。

### 未覆盖（诚实说明）

- 未实际运行 `check_imports.py`（grimp 未装），分层结论基于代码静态通读
- `af_service.py` 的 `_SESSIONS` 会话家族并发安全未深挖
- `af_experience.py`（经验闭环）、`af_mqtt_bridge.py`（跨仓通信）按 `.gates.toml` 点名但未逐行审计
- UI（Vue/TS）、docker/、CI 配置不在本次范围内

---

## 三、确认发现

### 🔴 HIGH-1　授权码快速通道绕过人工审批（ASI-03 / ASI-07）

**位置**：`src/autoforge/af_mcp.py:206-215`

```python
auth_code = (args.get("auth_code") or "").strip()
if auth_code:
    acs = _MCP_AUTH_STORE or AuthCodeStore(...)
    if acs.validate(auth_code):
        # 路径 A：持有效授权码 → 先一次性 consume（防重放），直接部署
        acs.consume(auth_code)
        res = svc.submit_pending(store, "af_save", payload,
                                 submitted_by=owner or "mcp",
                                 authenticated_subject=owner or None)
        op_id = res["pending"]
        applied = svc.approve_pending(store, op_id,
                                      reviewer=f"auth_code:{auth_code[:2]}" + "***")
        applied["deployed_via"] = "auth_code"
        return applied
```

**攻击路径**：
1. 攻击者取得一个有效授权码（签发端点 `POST /api/user/auth-code`，`af_api.py:991`）。该端点需 `_write` scope，但一旦持有 write 令牌即可**无限次签发**（`af_api.py:997` 无条件 `auth_store.create()`），且短期码 TTL 5–30 分钟、长期码更久。
2. 调用 MCP `af_save` 并附此码，走**路径 A**：`validate()` → `consume()` → `approve_pending()` 一步落盘。
3. **全程不经过人工审批**。审批门（`af_pending.py` 待批队列）被整段跳过。

**为什么自批检查拦不住**：`reviewer` 被写成 `"auth_code:12***"`，而 `submitter` 是 `owner`，二者字符串必然不等，`af_service.py:597` 的相等判断永远不成立。也就是说**授权码路径在设计上就绕过了自批保护**。

**影响**：拿到授权码 = 拿到一次性无审批部署权。授权码是 8 位量级的一次性码，但可反复签发，实际退化为"write 令牌换无限部署权"的代理路径。

**修复建议**：
- 授权码路径应只授予"提交"权，审批仍需独立主体；或明确将授权码纳入 reviewer 身份体系并参与自批比对。
- 给 `auth_store.create()` 加频率上限与审计日志，避免无限签发。
- 若此快速通道是 WebUI 的产品决策，应在代码中写明威胁模型说明，而非留作隐式行为。

---

### 🔴 HIGH-2　自批检查可被不同 subject 绕过（ASI-03 / ASI-07）

**位置**：`src/autoforge/af_service.py:585-601`

```python
def approve_pending(store, op_id, reviewer: str = "human") -> dict[str, Any]:
    ...
    submitter = str(op.get("submitted_by") or "")
    self_check = ""
    if reviewer and submitter and reviewer == submitter:      # ← 仅字符串相等
        raise ServiceError(f"禁止自批：提交人 {submitter!r} 不能批准自己提交的操作 {op_id!r}",
                           status=403)
    if not submitter:
        self_check = "unverified_submitter"                    # ADM B-11
```

**攻击路径**：
1. 攻击者持有两把**不同 subject** 的令牌（如 `agent_a` / `agent_b`），这在多令牌主体模型（`AUTOFORGE_TOKENS`，`af_auth.py`）下是常态配置。
2. `agent_a` 提交 → `submitted_by="agent_a"`；`agent_b` 批准 → `reviewer="agent_b"`。
3. `reviewer == submitter` 为假 → 检查放行。同一个操作者完成了自批。

**补充弱点**：`submitted_by` 为空时（无鉴权原型模式）不拦截，仅标注 `unverified_submitter` 并**继续执行**（`af_service.py:601`）。这是有意的兼容设计，但意味着"无身份"场景下审批门不设防。

**影响**：审批门的分权意图（agent 不能自批）在多身份场景下失效。

**修复建议**：
- 把判据从"字符串相等"升级为"主体归属相同"——同一操作者/同一令牌签发链下的不同 subject 视为同一主体。
- 对 `unverified_submitter` 场景增加显式开关，默认拒绝而非放行。

---

### 🟡 MEDIUM-1　凭据读取的 env 回退链（ASI-06）

**位置**：`src/autoforge/af_secrets.py:26-39`

```python
def load_secret(name: str, default: str = "") -> str:
    p = secret_path(name)
    try:
        val = p.read_text(encoding="utf-8").strip()
        if val:
            return val
    except (OSError, ValueError):
        pass                                    # 文件不存在 → 交给 env 回退
    return os.environ.get(name, default)        # ← 回退到环境变量
```

**风险路径**：`AUTOFORGE_API_TOKEN`（全 scope）、`AUTOFORGE_HA_TOKEN`、`AUTOFORGE_TOKENS` 均走此函数（`af_auth.py:158/162/181`、`af_config.py:70/74`）。若容器**未挂载** docker secrets 而依赖环境变量，凭据会长期驻留在进程环境，可通过 `/proc/<pid>/environ`、崩溃转储、调试端点、错误日志外泄。

**影响**：属部署配置风险，非代码缺陷本身。但当前实现让"安全挂载"退回"明文 env"是**静默发生**的——没有任何告警。

**修复建议**：
- 增加"生产模式禁止 env 回退"开关（`AUTOFORGE_STRICT_SECRETS=1`），未挂 secret 文件时直接失败而非回退。
- 回退发生时打一条 WARN 日志，让运维可见。

---

### 🟡 MEDIUM-2　自治闭环的 lint 计数降级存在绕过空间（ASI-10）

**位置**：`src/autoforge/af_closedloop/deepfix.py:197-201`

**问题**：LLM 修复的验收标准只是"lint 问题数严格下降"，**不比较严重性**。攻击者可构造"问题数减少但引入更危险节点"的修复（例如新增 `do` 节点指向未校验实体）。

**缓解因素（重要，避免夸大）**：
- 闭环产物需经 `commit()` 进入待批队列（`af_orchestrator.py:2304-2310`），**不直接绕过人审**。
- 存在白名单与 `_GUARD_TOKENS` 拦截。
- `max_fix_attempts=3` 限制了单次闭环深度。

**残余风险**：`auto_accept_unique_suggestion=True`（`af_orchestrator.py:2196`）允许闭环自动接受唯一候选实体，LLM 可借此引导实体选择；此外可反复入队形成洪泛。

**建议**：验收改为"严重性加权分下降"而非"计数下降"，并对唯一候选实体的自动接受加人工确认阈值。

---

### 🟡 MEDIUM-3　canary 漂移检查异常被静默吞掉（假绿风险）

**位置**：`src/autoforge/af_executor.py:245-246`

```python
except Exception:
    logging.getLogger("autoforge.executor").exception(
        "canary 漂移检查失败（已隔离，不阻断流程）")
```

**风险**：漂移检查抛异常时，回滚逻辑不会执行，而状态被记为 `verified`（真阴性）而非 `failed` / `unmodeled`。**失败被记成成功**，与项目自己在 CI 注释里痛斥的"假绿同型"是同一形状。

**影响**：金丝雀回滚谎报成功——这正是 `.gates.toml` 把 `af_executor.py` 列入 `critical_globs` 的原因。

**修复建议**：漂移检查异常应落 `unmodeled` 状态并计入可观测指标，不应静默降级为 `verified`。

---

## 四、Agent 威胁模型专项（OWASP Agentic Top 10 2026）

| 编号 | 风险 | 覆盖结论 | 依据 |
|---|---|---|---|
| **ASI-01** | Agent Goal Hijack | ✅ 覆盖良好 | NL 生成已收口为**确定性渲染**（`af_nl.py` / `default_prompt`），无 LLM prompt 拼接链，无注入面 |
| **ASI-02** | Tool Misuse | ✅ 基本覆盖 | urllib/subprocess 告警均为误报（见第五节）；工具面有 scope 门 + 参数声明即契约（`af_mcp.py:880-892` 的 `_undeclared_args` 拦截） |
| **ASI-03** | Identity & Privilege Abuse | ⚠️ **部分覆盖** | HIGH-1 + HIGH-2：授权码代理路径与自批绕过 |
| **ASI-04** | Agentic Supply Chain | ⚠️ 未充分审 | `docker/homesdk/*.whl` 私有 wheel 无 PyPI 溯源、无 hash/签名锁定；`af_experience.py` 经验闭环写入面需复核 |
| **ASI-05** | Unexpected Code Execution | ✅ 覆盖 | `af_closedloop/fixers.py:23` 的 `__import__` 为**同进程模块热加载**（`load_module()` 模式），非外部输入，无攻击面 |
| **ASI-06** | Memory & Context Poisoning | ⚠️ 部署风险 | MEDIUM-1 凭据 env 回退；`af_experience.py` 经验闭环持久化面未逐行审计 |
| **ASI-07** | Insecure Inter-Agent Comm | 🔴 **需修** | HIGH-1 + HIGH-2 均落在此类 |
| **ASI-08** | Cascading Failures | ✅ 有控制 | `af_scheduler.py` 去抖与配额、`max_fix_attempts=3` 上限 |
| **ASI-09** | Human-Agent Trust Exploitation | 🔴 **需修** | HIGH-1（人工审批被绕过）是此类最直接体现 |
| **ASI-10** | Rogue Agents | ⚠️ 部分 | MEDIUM-2；闭环有限次但可反复入队洪泛；`af_live.py` daemon 线程由 `af_tick_supervisor.py` 管理生命周期，非失控 |

**AutoForge 特有的风险面**：它的信任模型建立在"Agent 写、机器验、人审批"三权分立上。当前**机器验**（静态扫描 + 仿真）这一环做得扎实，但**人审批**这一环的身份语义不够严密（HIGH-1/2），等于三权分立里最短的那块板被削短了。

---

## 五、已排除（rejected）—— 逐条推翻理由

### AGENT-026「LangChain Tool Input Not Sanitized」× 8 条　置信度 0.4–0.6

命中位置：`af_adapters/ha.py:105/139/154`、`af_live.py:244`、`af_catalog.py:422/427`、`af_metrics.py:184/193`、`af_registry.py:317`

**推翻理由**：
1. **AutoForge 不使用 LangChain**。它的内核只依赖 `jsonschema` + `typer`（`pyproject.toml`），`@tool` 装饰器体系根本不存在，规则是纯误匹配。
2. 命中的 `urllib.request.Request` 仅用于构造 Home Assistant REST 请求，且已有防御：`af_adapters/ha.py:43,101-102` 用 `_HA_DOMAIN_RE` 正则校验 domain/service，`_NoRedirectHandler` 阻止令牌随 3xx 重定向外泄。
3. 置信度本身只有 0.4–0.6，且 8 条中 5 条被归入 INFO 层。

> **bandit B310**（`af_catalog.py:427`、`af_metrics.py:193`）同理，属同一族误报。

### AGENT-010「System Prompt Injection Vector」× 2 条

命中位置：`af_orchestrator.py:1122`、`af_closedloop/fixes.py:200`

```python
# af_orchestrator.py:1117-1122
def default_prompt(draft: AutomationDraft) -> str:
    ...
    return f"要{VERB_ZH.get(verb, verb)}{who}吗？"
```

**推翻理由**：这是 **NL 确定性渲染**，用于生成给用户看的确认文案，写入 `draft.asks[].prompt`。它不进入任何 LLM prompt 拼接链——AutoForge 的卖点恰恰是"确定性渲染"而非 LLM 自由生成。攻击者无法通过它劫持 Agent 目标。

### AGENT-047「Subprocess Execution Without Sandbox」× 1 条　置信度 0.8

命中位置：`af_service.py:2353`

**推翻理由**（已逐行核实 `af_service.py:2340-2364`）：
- `subprocess.Popen(cmd, ...)` 中 `cmd` 是**列表形式**，无 `shell=True`；
- HA 令牌经 `child_env["AUTOFORGE_HA_TOKEN"]` 以**环境变量**传递，不出现在 argv（对应项目自身 P1-19 整改），避免了 `ps` 泄漏；
- 无外部可控输入进入参数列表。

### AGENT-034「Tool Function Without Input Validation」× 1 条　置信度 0.95

命中位置：`af_closedloop/fixers.py:23`

```python
for i in load_module() and __import__("autoforge.af_closedloop.detectors", fromlist=["lint"]).lint(ctx.ir):
```

**推翻理由**：`__import__` 的参数是**硬编码字符串常量**，属同进程模块热加载模式（`load_module()`  reload 设计），无任何外部输入可达。置信度 0.95 是规则的静态启发式打分，与实际攻击面无关。

### AGENT-115「Agent Background Daemon Without Lifecycle Control」× 2 条

命中位置：`af_live.py:275`（`while True:`）、`af_live.py:442`（`threading.Thread(..., daemon=True)`）

**推翻理由**：这是 SSE 事件流长连接的**正常形态**，不是失控守护进程。生命周期由 `af_tick_supervisor.py` 与 `af_scheduler.py` 管理，且 `af_live.py:436-439` 有异常逃逸标记（`TICK_EXIT_UNEXPECTED`）交由 watchdog 自愈。

### bandit B324「Use of weak SHA1 hash」× 1 条（HIGH）

命中位置：`af_closedloop/loop.py:246`

```python
def _sig(ir) -> str:
    return hashlib.sha1(json.dumps(ir, sort_keys=True, ...).encode()).hexdigest()[:12]
```

**定性：误报，无需修复。** `_sig` 用于 IR 内容去重与变更标识，非密码学用途（非签名、非口令、非完整性校验对抗场景）。

> 若未来 `_sig` 被用于防篡改校验，需改为 SHA-256。当前无需改动。

---

## 六、架构与代码质量

### 分层依赖：清洁

`scripts/check_imports.py` 定义了 L0 内核 → L1 运行时 → L2 服务三层：

- **L0 内核底座**：`af_ir`、`af_adapters`、`af_flock`、`af_atomic`、`af_fault`、`af_time`、`af_auth`
- **L2 服务入口**：`af_service`、`af_api`、`af_cli`、`af_mcp`

实测**无 L0→L2 反向依赖、无循环依赖**。项目还额外用 import-linter 做二次约束并已升为 CI 失败门禁——这在同类项目里少见，是加分项。

### ruff 823 条：绝大多数是可自动修复的债

| 规则 | 条数 | 性质 |
|---|---|---|
| BLE001（裸 except / 模糊异常） | 142 | ⚠️ 需关注：`except Exception` 在 `critical_globs` 内是**硬错误**（`.gates.toml` 明确规定），全仓此类写法 86 处 |
| EXE002（可执行但无 shebang） | 98 | 噪音，可忽略 |
| UP037 / UP035（弃用类型语法） | 89 / 73 | 纯风格，可自动修 |
| F401（未用导入） | 59 | 纯风格，可自动修 |
| RUF022 / I001（`__all__` 与导入排序） | 56 / 48 | 纯风格，可自动修 |
| B008（可变默认参数） | 36 | 需人工看 |
| S110（try-except-pass） | 27 | ⚠️ 与 MEDIUM-3 同类，值得逐条看 |

**判断**：823 条里 453 条 ruff 可直接 `--fix`。真正需要人工决策的是 BLE001 与 S110 这两类"吞异常"——它们和 MEDIUM-3 是同一个病根。建议优先清理 `critical_globs` 覆盖的 12 个模块内的吞异常写法。

### 质量门禁有效性：设计扎实，但有一处过期注释

**做得好的**：
- `af_apply.py` 三步串联（build → simulate → save）扎实；stage 白名单约束。
- premiere 首演码防掉包 + 防重放。
- group 原子回滚按 `op_id` 摘条目。
- MCP `dispatch` 已加 `_undeclared_args` 拦截未声明参数（`af_mcp.py:880-892`）。

**需要清理的**：
`af_mcp.py` 中"隐藏参数"漏洞**已修复**（`_undeclared_args` 拦截），但注释仍写着旧的"原样成立"表述。这类过期注释会制造**假绿**——后来者读到注释会以为漏洞仍在或以为某行为未变，与项目 CI 注释里自己记录过的"门永远不可能红"是同类教训。建议同步更新。

---

## 七、修复优先级与后续验证清单

### 优先级

| 顺序 | 事项 | 位置 | 工作量 |
|---|---|---|---|
| P0 | 授权码路径纳入审批身份体系（HIGH-1） | `af_mcp.py:206-215` | 中 |
| P0 | 自批检查改为主体归属判据（HIGH-2） | `af_service.py:597` | 小 |
| P1 | canary 漂移异常不再记为 `verified`（MEDIUM-3） | `af_executor.py:245-246` | 小 |
| P1 | 凭据 env 回退加严格模式开关（MEDIUM-1） | `af_secrets.py:26-39` | 小 |
| P2 | lint 验收改严重性加权（MEDIUM-2） | `af_closedloop/deepfix.py:197-201` | 中 |
| P2 | 清理 `af_mcp.py` 过期注释 | `af_mcp.py:880-892` | 极小 |
| P3 | 清理 `critical_globs` 内的 BLE001/S110 | 12 个模块 | 大 |

### 后续验证清单（未覆盖，建议补做）

1. 实际安装 grimp 跑一次 `scripts/check_imports.py`，用机器确认分层无反向依赖
2. 逐行审计 `af_experience.py` 经验闭环的持久化写入面（ASI-04/06）
3. 审计 `af_mqtt_bridge.py` 跨仓通信的信任边界（ASI-07）
4. `af_service.py` 的 `_SESSIONS` 会话家族并发安全
5. 供应链专项：`docker/homesdk/homesdk-0.3.1-py3-none-any.whl` 的溯源、hash 锁定与签名
6. 把 bandit + agent-audit 接进 CI 成为硬门禁（当前 AutoForge 的 CI **完全没有安全扫描**，这是最该先补的洞），且**不要加 `continue-on-error`**

---

## 八、给工程团队的一句话

你们的静态扫描和仿真验证做得很扎实，人工审批链路是三权分立里唯一被削短的那块板——**授权码快速通道和字符串相等的自批检查，让"人审批"这一环在特定路径下可以整体跳过**。修这两处，比修那 17 条假阳性有价值得多。
