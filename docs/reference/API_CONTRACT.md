# AutoForge 服务层 API 契约

> **本文由脚本生成，⛔ 端点表与枚举不许手抄**（真源：运行中实例的 `GET /openapi.json` ＋ 发布树 `src/autoforge/af_api.py` 现读 ＋ `af_audit.ALL_EVENT_TYPES`／`af_scanner.CHECKS` 现读）。
> **唯一权威形态仍是运行中的 FastAPI**：`GET /openapi.json`（OpenAPI 3）／`GET /docs`（Swagger UI）。
> 生成方式：**脚本生成**（真源口径见上一行）；⛔ **端点表与枚举不许手抄、手改**。生成器：`scripts/render_api_contract.py`（仓内工具，⛔ 只用标准库）；契约漂移门（`scripts/check_contract_drift.py`）会重跑它并只比「端点表＋枚举」两段。本文是**速查表**，联调以 `/openapi.json` 为准。

| 现读坐标 | 值 |
|---|---|
| 发布树版本（`autoforge.__version__`，取自本次生成所用源码树） | `1.23.0` |
| 生成时线上实例 OpenAPI 版本 | `1.23.0`（⛔ **不作发布版号**：发布那一刻线上还跑着上一版；部署后自动对齐） |
| 路径数／操作数 | **88 路径／94 操作** |
| 认证方案 | `securitySchemes` = {"HTTPBearer": {"type": "http", "scheme": "bearer"}} |
| 权限面列来源 | `af_api.py` 的 `Depends(_read/_write/_live/_admin/_readonly_guard)` 现读 |
| `audit[].type` | **24 枚**（`af_audit.ALL_EVENT_TYPES` 现读逐枚相等） |
| `diagnostics[].code` | **52 枚**（`af_scanner.CHECKS` 现读） |

Base：`http://<host>:8787`（NAS 部署：`http://192.168.2.200:8787`）

## 一、鉴权与授权面

- **所有 `/api/*` 端点都要求 Bearer**（`securitySchemes` ＝ `HTTPBearer`）——例外只有两条：`/api/health`（免鉴权、只读）与 `/api/auth/login`／`/api/auth/register`（用户名口令换令牌）。
- **表里「无显式依赖」≠ 免鉴权**：那几行是 `auth/*` 登录族与 `mcp/pair/*` ——它们**没有 route 级 scope 依赖**，但**仍要登录令牌／一次性配对码**；`auth/logout|me|revoke|whoami|has-admin` 用的是**登录令牌**（owner 主体），不是 access token。
- **scope 四档**：`read`（只读）／`write`（写）／`live`（真机下发）／`admin`（管理面：令牌、授权码、自更新、watch 起停、归档删除）。
- **owner 面 vs 非 owner 面**：`_OWNER_SUBJECTS = {owner, shared}` 为 owner 面；非 owner 面的读面**只见自己名下**，并**必报 `hidden_count`**、带 `scope=owner:<主体>`；**owner 面不报 `hidden_count`、不带 `scope`，且能读到他人的归档**（与非 owner 面**正相反**）。读别人的归档时（非 owner 面）**拒绝文案与「不存在」逐字同形**（⛔ 不泄露存在性、⛔ 不点名归属者）。
- **授权码**：往真机加动作才要码；**无效码一律四因合一同形文案**（⛔ 不区分「不存在／已消耗／已过期／已锁定」）；**免码面**：禁用、serve 自动恢复监听、dry-live。
- **免令牌逃生舱 `AF_ALLOW_NOAUTH`**：现网该开关**值为空** ⇒ 生效条件不满足 ⇒ 实测免令牌调用`GET /api/graphs` 与 `POST /api/pending/list` 均 **403**（⛔ **无未授权读面**）。

## 二、端点全表（生成自 OpenAPI ＋ 源码现读）

### asks（人机回路应答）

| 方法 | 路径 | 权限面 | 说明 |
|---|---|---|---|
| `GET` | `/api/asks` | read | 聚合所有在途会话的挂起 ask，含 `spec` 与控件 `control` 元数据。 |
| `POST` | `/api/asks/answer` | write | 写答案到 watch 的 answer_inbox（由 watch ticker 注入 runtime）。 |
| `GET` | `/api/asks/pending` | read | 读 watch 进程写出的 pending_asks sidecar（供 DB 轮询发现挂起 ask）。 |
| `GET` | `/api/asks/{name}` | read | 读取归档内 ask 节点的控件元数据（AF-730 ⑬：与 graphs/{name} 同一单源闸）。 |

### auth（登录/令牌/主体）

| 方法 | 路径 | 权限面 | 说明 |
|---|---|---|---|
| `GET` | `/api/auth/has-admin` | —(无显式依赖) | 检查是否已有管理员用户。前端用来判断显示注册页还是登录页。 |
| `POST` | `/api/auth/login` | —(无显式依赖) | 登录：验证用户名和密码，通过后签发 owner 登录令牌（principal_kind=login）。 |
| `POST` | `/api/auth/logout` | bearer(仅校验持令牌) | 注销：撤销当前令牌（owner 单令牌，无副作用）。 |
| `GET` | `/api/auth/me` | —(无显式依赖) | 登录态恢复：返回当前令牌主体；未登录/失效 401。 |
| `POST` | `/api/auth/register` | —(无显式依赖) | 首次注册管理员。只有当没有管理员时才允许；已有管理员则拒绝。 |
| `POST` | `/api/auth/revoke` | —(无显式依赖) | 撤销令牌（即时生效并落盘 `{store_root}/.auth/revoked.json`）。 |
| `GET` | `/api/auth/subjects` | write | 已注册令牌的主体摘要（不含明文令牌），供审计/管理用。 |
| `GET` | `/api/auth/whoami` | —(无显式依赖) | 令牌自检：返回当前令牌的 subject + scopes；未认证返回 401。 |

### automations（自动化启停归档）

| 方法 | 路径 | 权限面 | 说明 |
|---|---|---|---|
| `GET` | `/api/automations` | read | 自动化卡片列表（AF-731/AF-730：范围收口走服务层薄入口单源）。 |
| `DELETE` | `/api/automations/{name}` | admin | 删除归档（AF-733：owner 面全部；开发者 agent 仅**自己名下**归档）。 |
| `GET` | `/api/automations/{name}` | read | 读取单个自动化卡片（AF-731：先过读面归属闸，再渲染）。 |
| `POST` | `/api/automations/{name}/archive` | write | 归档自动化（AF-731 写面腿：写面归属闸复用读面单源 `_assert_archive_visible`）。 |
| `POST` | `/api/automations/{name}/disable` | write | —(端点无 docstring) |
| `POST` | `/api/automations/{name}/enable` | write | —(端点无 docstring) |
| `POST` | `/api/automations/{name}/unarchive` | write | 解除归档（AF-731 写面腿：与 archive 同一写面闸，同码同文 404）。 |

### bind（实体绑定）

| 方法 | 路径 | 权限面 | 说明 |
|---|---|---|---|
| `POST` | `/api/bind` | read/write(只读装配期) | v1.6.0：可选 binding——把 IR 里的设备描述占位符（`?书房吊灯`）回填为真实 entity_id。 |

### build/sim（两道闸）

| 方法 | 路径 | 权限面 | 说明 |
|---|---|---|---|
| `POST` | `/api/build` | read/write(只读装配期) | —(端点无 docstring) |
| `POST` | `/api/sim` | read/write(只读装配期) | —(端点无 docstring) |

### catalog（设备目录）

| 方法 | 路径 | 权限面 | 说明 |
|---|---|---|---|
| `GET` | `/api/catalog` | read | 设备目录摘要（按域统计 + 区域列表 + 新鲜度），读端点不强制鉴权。 |
| `POST` | `/api/catalog/alias` | write | 沉淀一条别名映射（下次 `resolve` 直中，high 置信）。 |
| `POST` | `/api/catalog/alias/remove` | write | 删除一条别名映射。 |
| `GET` | `/api/catalog/aliases` | read | 列出已沉淀的「设备名 → entity_id」映射。 |
| `POST` | `/api/catalog/refresh` | write | 拉取 HA 全屋设备目录进本地缓存（会发起出站 HA 请求，按写端点保护）。 |
| `GET` | `/api/catalog/resolve-metrics` | read | v1.5.0：解析成功率漏斗（五档 exact/medium/low/ambiguous/none）。 |

### conf（置信度/干预）

| 方法 | 路径 | 权限面 | 说明 |
|---|---|---|---|
| `GET` | `/api/conf/{name}` | read | 置信度读面（AF-730 ⑭：身份下传 `get_conf` 既有参数，服务层 R2 语义）。 |
| `POST` | `/api/conf/{name}/intervene` | write | 干预置信度（AF-733：owner 面全部；开发者 agent 仅**自己名下**归档）。 |

### credentials（凭据热重载）

| 方法 | 路径 | 权限面 | 说明 |
|---|---|---|---|
| `GET` | `/api/credentials` | write | —(端点无 docstring) |
| `POST` | `/api/credentials/update` | write | —(端点无 docstring) |

### entities（实体与状态）

| 方法 | 路径 | 权限面 | 说明 |
|---|---|---|---|
| `GET` | `/api/entities` | read | 全屋实体目录·过滤浏览（强制分页 + 透明截断回报）。读端点。 |
| `GET` | `/api/entities/resolve` | read | 自然语言设备名 → Top-N 候选 entity_id（写 IR 前必调）。读端点。 |
| `GET` | `/api/entities/{entity_id}/state` | read | 单实体当前状态（实时优先，失败回退目录缓存并标注 source）。读端点。 |

### evidence（生产证据）

| 方法 | 路径 | 权限面 | 说明 |
|---|---|---|---|
| `GET` | `/api/evidence/prod` | read | 诚实报告 `verified_in_prod` 分区：verified / failed / unmodeled 三档 + 淘汰计数。 |

### experience（经验库）

| 方法 | 路径 | 权限面 | 说明 |
|---|---|---|---|
| `GET` | `/api/experience` | read | 实体共现经验摘要（**只在成功落盘后采集**，失败样本不污染）。 |
| `GET` | `/api/experience/export` | read | 实体共现经验结构化导出（喂 MA：AF 采集事实 → MA 生成假设）。 |

### faults（故障图鉴）

| 方法 | 路径 | 权限面 | 说明 |
|---|---|---|---|
| `GET` | `/api/faults` | read | —(端点无 docstring) |

### graphs（归档治理）

| 方法 | 路径 | 权限面 | 说明 |
|---|---|---|---|
| `GET` | `/api/graphs` | read | 归档列表（AF-730 收口：身份下传服务层归属判定单源）。 |
| `POST` | `/api/graphs/disable` | write | —(端点无 docstring) |
| `POST` | `/api/graphs/enable` | write | —(端点无 docstring) |
| `POST` | `/api/graphs/tags` | write | —(端点无 docstring) |
| `GET` | `/api/graphs/{name}` | read | 读取单个归档（AF-730 收口：先过读面归属闸，再取数据）。 |

### health

| 方法 | 路径 | 权限面 | 说明 |
|---|---|---|---|
| `GET` | `/api/health` | —(无显式依赖) | —(端点无 docstring) |

### insights（洞察审批）

| 方法 | 路径 | 权限面 | 说明 |
|---|---|---|---|
| `POST` | `/api/insights/approve` | write | —(端点无 docstring) |
| `GET` | `/api/insights/pending` | read | —(端点无 docstring) |
| `POST` | `/api/insights/reject` | write | —(端点无 docstring) |

### live（真机下发）

| 方法 | 路径 | 权限面 | 说明 |
|---|---|---|---|
| `POST` | `/api/live/run` | live／read/write(只读装配期) | —(端点无 docstring) |
| `GET` | `/api/live/status` | live | —(端点无 docstring) |

### mcp（配对引导）

| 方法 | 路径 | 权限面 | 说明 |
|---|---|---|---|
| `GET` | `/api/mcp/pair-request` | —(无显式依赖) | SSE：前端 App 启动建立长连接，收到配对请求即弹窗。 |
| `POST` | `/api/mcp/pair/redeem` | —(无显式依赖) | 用用户口述的 8 位码兑换 Bearer 令牌。与 MCP `af_pair` 共用 `redeem_pair_code`（一份顺序）。 |
| `POST` | `/api/mcp/pair/request` | —(无显式依赖) | 还没有令牌的 agent 用它发起配对——**这正是配对要解决的问题本身**。 |

### metrics/telemetry

| 方法 | 路径 | 权限面 | 说明 |
|---|---|---|---|
| `GET` | `/api/metrics` | read | 运行指标（执行/审计/置信度），读端点不强制鉴权。 |
| `GET` | `/api/telemetry` | read | token/结果遥测 + 错误类别分布（四维：tool/category/ok/day）。 |

### pending（待批队列）

| 方法 | 路径 | 权限面 | 说明 |
|---|---|---|---|
| `POST` | `/api/pending/approve` | write | —(端点无 docstring) |
| `POST` | `/api/pending/list` | write | —(端点无 docstring) |
| `POST` | `/api/pending/reject` | write | —(端点无 docstring) |

### sessions（会话）

| 方法 | 路径 | 权限面 | 说明 |
|---|---|---|---|
| `GET` | `/api/sessions` | read | —(端点无 docstring) |
| `POST` | `/api/sessions` | write | —(端点无 docstring) |
| `DELETE` | `/api/sessions/{session_id}` | write | —(端点无 docstring) |
| `GET` | `/api/sessions/{session_id}` | read | —(端点无 docstring) |
| `POST` | `/api/sessions/{session_id}/answer` | write | —(端点无 docstring) |
| `POST` | `/api/sessions/{session_id}/cancel` | write | —(端点无 docstring) |
| `POST` | `/api/sessions/{session_id}/tick` | write | —(端点无 docstring) |

### spec（AF-Spec）

| 方法 | 路径 | 权限面 | 说明 |
|---|---|---|---|
| `POST` | `/api/spec/compile` | read/write(只读装配期) | —(端点无 docstring) |
| `GET` | `/api/spec/{name}` | read | —(端点无 docstring) |

### store（导入导出）

| 方法 | 路径 | 权限面 | 说明 |
|---|---|---|---|
| `GET` | `/api/store/export` | read | 导出整个 store 为 bundle（含 tags + 校验和）。 |
| `POST` | `/api/store/import` | write | 导入 bundle（写操作，需鉴权）。冲突策略 skip/overwrite/rename。 |

### system（版本/自更新）

| 方法 | 路径 | 权限面 | 说明 |
|---|---|---|---|
| `POST` | `/api/system/update` | admin | 提交更新请求（admin）。 |
| `GET` | `/api/system/update/check` | read | 读取宿主预检脚本写的更新预览文件。 |
| `GET` | `/api/system/update/status` | read | 查看更新进度。 |
| `GET` | `/api/system/version` | read | 返回当前版本信息。 |

### undo（回滚）

| 方法 | 路径 | 权限面 | 说明 |
|---|---|---|---|
| `GET` | `/api/undo/available` | read | 窗口内仍可撤销的部署清单（前端据此决定撤销按钮是否可点）。 |
| `GET` | `/api/undo/{deploy_id}` | read | 撤销前盘点：窗口/风险域/实体清单，口径与 revert 同源。 |
| `POST` | `/api/undo/{deploy_id}` | live／read/write(只读装配期) | 撤销一次部署：回放动作前快照（风险域需 body.confirm=true）。 |

### user（用户面/授权码/配对）

| 方法 | 路径 | 权限面 | 说明 |
|---|---|---|---|
| `POST` | `/api/user/agent-token` | write | 批 4（D8）：owner 面**显式**签发一枚 developer 档令牌（带 admin scope）。 |
| `GET` | `/api/user/agents` | read | —(端点无 docstring) |
| `DELETE` | `/api/user/agents/{agent_id}` | write | —(端点无 docstring) |
| `PATCH` | `/api/user/agents/{agent_id}` | write | —(端点无 docstring) |
| `POST` | `/api/user/auth-code` | write | —(端点无 docstring) |
| `DELETE` | `/api/user/auth-code/{code}` | write | —(端点无 docstring) |
| `GET` | `/api/user/auth-codes` | write | 授权码列表：write 域 + owner 面明文／非 owner 面定形掩码（裁定 20261004 §一 F-3）。 |
| `POST` | `/api/user/pair-link` | write | 批 5：owner 面生成配对链接（link_key + 8 位码，同一 TTL、同一一次性）。 |
| `GET` | `/api/user/pair/accepting` | write | 开关现值。暂停时 SSE 不再产出新事件，前端没有别的途径知道"是我自己按停的"。 |
| `POST` | `/api/user/pair/accepting` | write | owner 侧"暂停接受配对请求"开关——裁定给的是"弹窗骚扰唯一由用户自己就能止血的形状"。 |
| `POST` | `/api/user/pair/{code}/confirm` | write | 用户在前端点'确认配对成功'：agent 已用码经 MCP `af_pair` 兑换令牌。 |

### watch（监听）

| 方法 | 路径 | 权限面 | 说明 |
|---|---|---|---|
| `GET` | `/api/watch/list` | read | 列出当前在跑的 watch 实例（读 persist dir 的 watch.lock.info）。 |
| `POST` | `/api/watch/start` | admin | 启动 watch 进程跑指定 IR（dry-live 默认）。 |
| `POST` | `/api/watch/stop` | admin | 停止运行中的 watch 进程。 |

### 其它

| 方法 | 路径 | 权限面 | 说明 |
|---|---|---|---|
| `GET` | `/api/diff` | read | —(端点无 docstring) |

## 三、关键枚举（全部现读真源）

- `audit[].type`（**24 枚**，真源 `af_audit.ALL_EVENT_TYPES`）：`entity_drift`｜`action_failed`｜`event_emitted`｜`breaker_open`｜`breaker_recover`｜`quota_exceeded`｜`instance_rejected`｜`instance_expired`｜`instance_debounced`｜`instance_restored`｜`instance_restore_dropped`｜`instance_session_lost`｜`write_conflict`｜`instance_lease_held`｜`handler_failed`｜`premiere_issued`｜`premiere_consumed`｜`premiere_trial_started`｜`premiere_trial_paused`｜`trial_assert_failed`｜`token_expired_purged`｜`token_env_legacy_name`｜`token_issued_rejected`｜`approve_pending_applied`
- `diagnostics[].level`：`error`｜`warning`；`diagnostics[].code`（真源 `af_scanner.CHECKS`，**52 枚**）：`L3_ACTION`｜`HTTP_NOT_WHITELISTED`｜`MISSING_TIMEOUT_OR_DEFAULT`｜`ENTITY_DEP_CYCLE`｜`UNRESOLVED_ENTITY`｜`SELF_TRIGGER_LOOP`｜`STATIC_LOOP`｜`CANCEL_SPAWNS_INSTANCE`｜`HIGH_RISK_AFTER_SUSPEND`｜`SHADOW_WRITES_DEVICE`｜`CROSS_AUTOMATION_VAR`｜`ADAPTER_POLICY_PARAM`｜`SNAPSHOT_FALSE_MULTI_AND`｜`ENTITY_NOT_FOUND`｜`ENTITY_WRITE_CONFLICT`｜`L2_NEEDS_CONFIRM`｜`L2_NEEDS_CANARY`｜`LOW_CONF_WRITES_DEVICE`｜`DO_WITHOUT_ON_ERROR`｜`NESTED_SUSPEND_IN_CANCEL`｜`ENTITY_ACL_DENIED`｜`ENTITY_OFFLINE_NOW`｜`TRIGGER_STALE`｜`DUPLICATE_EDGE_PRIORITY`｜`NON_IDEMPOTENT_CONCURRENT`｜`NO_ENTRY_NODE`｜`RESERVED_NOT_IMPLEMENTED`｜`EXPR_INVALID`｜`EXPR_NS_INVALID`｜`TRIGGER_INVALID`｜`PARAMS_TOO_DEEP`｜`INBOX_PARAM_TOO_LONG`｜`UNDECLARED_VAR`｜`NL_COVERAGE`｜`EMIT_SELF_LOOP`｜`CROSS_DEP_CYCLE`｜`EMIT_STORM_LIMIT`｜`LIVE_TOKEN_REQUIRED`｜`LIVE_CONFIRM_REQUIRED`｜`LIVE_WHITELIST_REQUIRED`｜`LIVE_ENTITY_NOT_WHITELISTED`｜`EXPECT_MISSING`｜`EXPECT_UNREACHABLE`｜`EXPECT_STATE_INVALID`｜`ENTITY_GUARD_TIER0`｜`ASK_AT_RUNTIME`｜`TIME_AT_INVALID`｜`SERVICE_UNKNOWN`｜`ENTITY_CHECK_DEGRADED`｜`EDGE_KIND_MISMATCH`｜`ASK_BINARY_NO_DENY_PATH`｜`ASK_STRUCT_NO_THEN`
- `band`：`auto`（conf ≥ 0.85）｜`shadow`（≥ 0.60）｜`ask`（< 0.60）
- `instances[].state`：`created`｜`active`｜`suspended`｜`done`｜`cancelled`｜`failed`｜`expired`
- **响应无信封**：所有响应是**扁平**对象（⛔ 没有 `{"data": ...}` 外壳）。

## 四、真机下发（三重闸，缺一即拒）

1. 服务端开关 `AUTOFORGE_LIVE_ENABLED=1`（**默认关闭**）→ 否则 **403**
2. 服务端 HA 令牌（**绝不从请求体接收令牌**）→ 否则 **403**
3. 请求体 `confirm=true` ＋ 非空 `live_allow`，且 IR 写目标 ⊆ 白名单 → 否则 **403/400**

## 五、变更纪律

> 契约变更须同步：本文件（**重新生成，⛔ 不手改端点表**）、`af_api.py`、`af_service.py`、对应单测、以及前端 Mock。

> **生成口径**：端点表＝`GET /openapi.json` 现读；权限面与说明＝`af_api.py` 现读解析；枚举＝`af_audit.ALL_EVENT_TYPES`／`af_scanner.CHECKS` 现读。**任一真源缺失 ⇒ 生成器报错退出，⛔ 不许手写补洞。**
