# AutoForge 完整知识文档

> **更新时间**：2026-10-09　**鲜度基准**：`master` HEAD `e5b3fd5`
> **本文是"面与清单"**：IR 语言、31 个 MCP 工具、90 条 HTTP 路由、18 个 CLI 命令、43 项安全闸、测试通道与预演怎么用、两棵 WebUI 怎么用、部署与排障。
> **机制与不变量在**《AF完整架构与运行时说明.md》（同一份代码、同一个基准；那里讲"为什么这档不碰真机"，这里讲"这档怎么调"）。
> **清单不是手抄**：工具表来自 `len(af_mcp.TOOLS)=31` 的注册表、路由表来自 `build_app().routes`（90 条含 methods）、CLI 表来自 `typer` 命令注册表、检查项来自 `af_scanner.CHECKS`（43 项）、枚举来自 `af_ir/schema/ir.schema.json`。这四份都是**系统自己的信号**，改代码后重取即可，别信任何一处散文副本。

---

## 一、AF 是什么

**AutoForge（AF）** = 智能家居自动化的"设计 + 运行"一体化平台：Agent 用 MCP 设计自动化（意图 JSON → IR → 安全闸 → 仿真 → 预演/入队），人 approve 后归档，`forge watch` 常驻订阅 HA 事件流真机驱动；治理层（置信度 band / shadow / canary / 冲突仲裁 / 撤销 / 生产态证据）负责"让它敢自动跑"。

核心能力（现状，非计划）：

- 自然语言 → 意图 JSON → IR（`af_draft`，服务端解析中文设备名）
- 编译期安全闸 **43 项**（`af_scanner.CHECKS`）
- 双轨仿真 + 后置条件断言（`simulate` / `simulate_track`，`expect`）
- **预演档 `dry_run`**：校验 + 仿真，零写入（§八）
- **测试通道 `af_test_*`**：批量跑题、自动放行、与正式区隔离（§九）
- 待批队列 → 人工 approve → GraphStore 版本化归档
- 首演码 + 24h 试演期（premiere）
- 常驻运行时（watch + tick 自愈 + SSE）
- 治理面：conf/auto-shadow-ask/canary/conflict/undo/insights/evidence
- ADM 联动出向（`af/automation/fired|failed`、`adm/autoforge/status`、`butler/inbox/speak|notify|tv`——AF 只**投递**，播不播由 DB 的 Sentinel 判；AF **不订阅**这三条主题）
- 两棵在用 WebUI：开发者控制台 `/ui`、**用户视角 `/mimo`**

设计哲学没变：fail-closed 默认拒绝、安全闸优先、版本化可追溯、真机需三重闸（服务端开关 + confirm + 白名单）。**新增的一条是"证据分层"**：`ok=True` 只算"没抓到反例"，`fully_verified` / `verified_in_prod` 才算验过（§八）。

---

## 二、三条上手路径

| 你是谁 | 走哪条 | 第一步 |
|---|---|---|
| Agent / MCP 客户端 | 黄金路径两跳 | `af_draft(intent)` → `af_apply(ref, stage)` |
| 家庭成员 / 运维 | 用户视角 WebUI | `http://<nas>:8787/mimo/` → 首设账号密码 → Agent 配对 |
| 开发 / 排障 | 控制台 + CLI | `http://<nas>:8787/`（控制台）或 `forge build / sim / watch / …` |

---

## 三、IR 语言参考

### 3.1 节点：8 种 kind（`af_ir/models.py:75`，与 schema `node.kind.enum` 同源）

| kind | 语义 | 关键点 |
|---|---|---|
| `on` | 触发 | 5 类 trigger，见 3.4 |
| `if` | 条件 | 表达式算子见 3.6 |
| `do` | 动作 | 走适配器：`ha`/`http`/`mock`/`inbox`（收件箱投递）。DSL 的 `do d1 inbox.speak {…}` 会被 partition 成 `adapter=inbox` + `action=speak`（`af_spec.py:286-287`，域名前缀不进 `action`）；`on_error` 边缺省直接 failed（检查项 `DO_WITHOUT_ON_ERROR`） |
| `ask` | 挂起等人答 | 必须给 `on_timeout` 或 `default`（`MISSING_TIMEOUT_OR_DEFAULT`）；5 类 `ask.kind`，4 类 `session` 作用域 |
| `wait` | 延时 | 到期自动走 `then` |
| `set` | 写实例变量 | 变量需声明（`UNDECLARED_VAR`），4 种 `var_decl.type` |
| `pass` | 终止 | — |
| `group` | 容器（v2.3/F9） | `mode: sequence\|parallel`；**IR 版本必须打 `0.3.0`**（`GROUP_IR_VERSION`，`models.py:66`）；嵌套有深度预算 |

> `emit` **不是节点 kind**：它是节点/自动化上的字段，`models.py:103-104` 明写「`emit` 已于 v0.3.0 实现，`persist` 于 P1 实现」，当前保留字段只剩 `fn`（`_RESERVED_NODE_KEYS = ("fn",)`）。旧本文把 emit 列成第 7 种节点，是错的。事件相关检查项：`EMIT_SELF_LOOP`、`EMIT_STORM_LIMIT`。

### 3.2 边：7 种（`models.py:85`）与优先级（`:88-95`，强制）

`then` / `yes` / `no` / `default` / `on_timeout` / `on_cancel` / `on_error`

优先级（高→低，冲突消解强制）：**`on_cancel` > `on_error` > `on_timeout` > `yes` = `no` = `then` > `default`**。同节点同优先级重复定义 → `DUPLICATE_EDGE_PRIORITY`。旧本文写的 `true`/`false` 边名不存在（真名是 `yes`/`no`/`default`），且漏了 `on_cancel`。

### 3.3 自动化 mode：4 种（schema `child_automation.mode.enum`）

`single` / `restart` / `queued` / `parallel`。非幂等动作配 `restart`/`parallel` → `NON_IDEMPOTENT_CONCURRENT`。

### 3.4 触发：5 类（schema `trigger.type.enum`）

| type | 字段 | 例 |
|---|---|---|
| `state` | `entity_id`、`from`、`to` | `{"type":"state","entity_id":"binary_sensor.door","to":"on"}` |
| `time` | `at`；周期用 `every`（**`today_only` 不支持**，见执行记录 §9b9f336 的文档更正） | `{"type":"time","at":"08:00"}` |
| `sun` | `event`(sunrise/sunset)、`offset`（秒） | `{"type":"sun","event":"sunset","offset":-1800}` |
| `event` | `event`（**字段名就叫 `event`，不是 `event_type`**） | `{"type":"event","event":"my_event"}` |
| `group` | `op`(and/or)、`sources`（递归） | 嵌套深度受 `MAX_TRIGGER_DEPTH` 预算约束，超预算/自引用 → `TRIGGER_INVALID` |

`trigger.op = and|or`（`group` 用）。AF **不用 `visited` 去环**，走的是"每绕一圈深度 +1 ⇒ 必然撞预算"的每站点预算路线（`af_ir/models.py:166` 的注释；对照口径见 `docs/architecture/HA_SEMANTIC_DIFF.md`）。

**两个联动事件名走的就是上面这行 `event`，没有第六类 trigger**（计划 §七 卡3）：`{"type":"event","event":"ma_presence"}` 按"谁在家"触发、`{"type":"event","event":"ma_device_health"}` 按"哪台设备掉线/迁移"触发。它们的来源不是 HA 事件流，而是 MQTT 桥订 `ma/presence` / `ma/device-health` 后落进 `af_linkage_feed.LinkageFeed`（盘上队列），由**常驻 tick 线程**抽出来注入内部总线（`af_live.pump_linkage`）。三条口径要记住：

- 只有 `forge watch` 会抽这条队列。**`serve` 没有 ticker**（它只起桥：入队、不触发），所以在 `serve` 里 `linkage.inbound.presence_in` 会涨、自动化却不会动 —— 不是 bug，是进程分工。
- 队列里的条目**按年龄决定要不要当触发用**：`received_at` 距今 >`TRIGGER_MAX_AGE_S`（120 秒）只留档、不回放，水位线照样推进 ⇒ "重启不丢记录，重启不补触发"。开机瞬间拿一条小时级的旧掉线快照去真实下发设备，是这条闸要防的事。
- 事件**载荷按"哪台设备"这一格读得进节点，按"哪个成员"这一格还读不进**（裁定 20261009 §四 甲/直译已落地）：`spawn`（`af_instance.py:264`）除 `{entity_id, state}` 外再摊三枚平键进 `context` —— `trigger_entity_id` / `trigger_subject` / `trigger_kind`，取值逐字直译契约 §1.2 的键名（device-health 有 `entity_id` 就用它当主标识；presence 没有 `entity_id`，主标识落 `subject`，那一格正是 `member_id` 串），名单真源 `TRIGGER_FLAT_KEYS`（`af_instance.py:479`）。对端没报的那一格落**空串**：三枚键必须恒定在场，否则 `make_resolver`（`af_state.py:157-182`）对缺失的 `context.*` 抛 `KeyError`，一条用了新词汇的自动化会整段失败。**解析器仍是平表**，`split_namespace` 用 `partition(".")` ⇒ `context.trigger.members` 这类嵌套路径读不出，所以**别在 DSL 里假装能按成员名分支**（那半边按裁定留给乙或卡2 的 `ma_query` 绑定）。能写的形状举例：`if context.trigger_entity_id == "switch.ac_plug"` —— 判据是"读出的值真会改分支"，不是"读得出"。这三枚键**已由 DCD 登记进契约表 §1.6**（跨仓公开词汇）；主标识口径裁**甲A**：DSL 的 `trigger_subject` 走 `entity_id` 优先，盘上 `LinkageRecord.subject` 维持 `device_id` 优先（展示身份），**两格并存、各有用途**，不改 `af_mqtt_bridge.py:906`。

### 3.5 ask：`ask.kind` 5 种 + `session` 4 种

`choice` / `entity` / `time_range` / `threshold` / `text`；`session ∈ room|device|user|global`（会话匹配消歧，见 `docs/architecture/IR_AND_RUNTIME.md` §5.2）。

### 3.6 条件表达式算子（`af_ir/expr.py:46-48`）

- 比较：`eq` `ne` `lt` `lte` `gt` `gte`
- 一元判定：`is_on` `is_off` `is_home` `is_not_home` `truthy` `not_is_on` `not_is_off`
- 逻辑：`and` `or` `not`
- 操作数形态：`{"var": ...}` / `{"const": ...}`
- 校验失败 → `EXPR_INVALID`（未知算子/函数、参数个数、深度/节点上限）
- **等价判定**：`condition_norm.normalize_condition`（CNF 归一化）+ `condition_equivalent` —— 允许布尔等价变形（分配律、双重否定、恒真子句消除、顺序无关）；**无法证明等价 ⇒ 判 False**，叶子不可 JSON 序列化时抛 `LeafUnserializable`（第十轮族残余已修）。

### 3.7 IR 版本

`SUPPORTED_IR_VERSIONS = ("0.2.1", "0.3.0")`（`models.py:62`），`GROUP_IR_VERSION = "0.3.0"`。schema 的 `ir_version.enum` 与之同源（灰度兼容旧 IR，旧版不拒；但 group 用 0.2.1 承载会被判"版本号不诚实反映能力"）。

---

## 四、MCP 工具全表（**31 个**，注册表 `af_mcp.py:463`，名单唯一真源由 `scripts/check_tool_names.py` 钉住）

`scope` 列：`-` = 无需令牌也能调（仍受 fail-closed 鉴权缺省约束）；`write` = 需写权限；`live` = 需真机权限。

### 4.1 黄金路径 + 测试通道

| 工具 | scope | 必填 | 可选 | 一句 |
|---|---|---|---|---|
| `af_draft` | - | `intent` | `session_id` | 意图 JSON → IR，返回 `ref`；实体写中文名即可，服务端解析。`session_id` 供 v2.3 决策门多意图占比统计 |
| `af_apply` | write | `ref` | `stage` | 一次走完 校验→仿真→入队；`stage ∈ check/simulate/dry_run/save`（缺省 `save`） |
| `af_test_submit` | write | `intents` | `batch_id` | 测试通道批量：draft→build→simulate→自动放行→落测试区（≤500 条/批） |
| `af_test_report` | - | `batch_id` | — | 取测试报告（通过率/失败原因/每题详情） |
| `af_test_clear` | write | — | — | 清空测试区：删除前过形状守卫（等于/包住正式存储根、盘根 ⇒ 拒判），删除后实测残留（§九） |

### 4.2 配对与身份

| 工具 | scope | 必填 | 一句 |
|---|---|---|---|
| `af_request_pair` | write | — (`agent_name_hint`) | 第 1 步：生成 8 位单次短时效码，SSE 推给用户弹窗；**Agent 不拿码** |
| `af_pair` | write | `code` (`agent_name`) | 第 2 步：用用户口述的码兑换 Bearer 令牌，返回 `{ok,token,subject}` |
| `af_whoami` | - | — | 自检当前会话 subject/scopes（排查 scope 不足导致的写工具被拒） |

### 4.3 设备目录与实体解析

| 工具 | scope | 必填 | 可选 | 一句 |
|---|---|---|---|---|
| `af_refresh_catalog` | - | — | `full,domain,area` | 拉 HA 全屋目录进本地缓存（首连/设备大增减后调一次） |
| `af_resolve_entity` | - | `name` | `area,domain,top_n` | **设备名→entity_id 唯一正路**；写 IR 前必调，只准用返回里的 ID |
| `af_remember_entity` | write | `name`,`entity_id` | — | 把选择沉淀为精确别名（写 `{root}/.catalog/aliases.json`） |
| `af_list_entities` | - | — | `domain,area,keyword,limit,offset` | 按条件浏览缓存；**强制分页**（默认 50） |
| `af_get_entity_state` | - | `entity_id` | — | 查当前状态；实时读失败自动回退目录缓存并标 `source=catalog_cache` |
| `af_catalog` | - | — | — | 目录摘要（按域统计+区域+freshness）；**刻意不 dump 全量**，防数千实体撑爆上下文 |

### 4.4 旧接口三件套（保留，非黄金路径）

| 工具 | 必填 | 一句 |
|---|---|---|
| `af_build` | `ir` (`known_entities`) | 第一道闸：Schema + 静态扫描，返回 `ok/errors/warnings/diagnostics/nl` |
| `af_compile_spec` | `text`（别名 `spec`/`prompt`） | AF-Spec 文本 → IR（同一真相） |
| `af_simulate` | `ir` (`seed`,`events`) | 第二道闸：内存 FakeHA 回放验逻辑 |

### 4.5 归档读写与批量

| 工具 | scope | 必填 | 可选 | 一句 |
|---|---|---|---|---|
| `af_save` | write | `name`,`ir` | `note,tags,expect_version,auth_code,allow_bulk` | 归档新版本，**先过第一道闸** |
| `af_list_graphs` | - | — | — | 列归档（最新版本/模式/id/标签） |
| `af_get_graph` | - | `name` | `version` | 取某版本：`ir/nl/diagnostics` |
| `af_graphs_by_tag` | - | `tag` | — | 按标签筛 |
| `af_set_tags` | write | `name`,`tags` | — | 覆盖式设标签 |
| `af_enable_by_tag` | write | `tag`,`enabled` | `allow_bulk` | 批量启停（`allow_bulk` = 显式绕过爆炸半径护栏） |
| `af_export_store` | - | — | — | 导出 bundle（含 tags + 校验和） |
| `af_import_store` | write | `bundle` | `strategy(skip\|overwrite\|rename),allow_bulk` | 导入 bundle |
| `af_diff` | - | `name`,`old`,`new` | — | 同归档两版本结构化差异 + 可读 render |

### 4.6 治理 / 观测 / 真机

| 工具 | scope | 必填 | 一句 |
|---|---|---|---|
| `af_conf` | - | `name`（或 `_all`） | 置信度分级（G4） |
| `af_health` | - | — | 版本、契约版本、里程碑、只读标记、**`write_gate`**、linkage、tick、tz |
| `af_live_run` | **live** | `ir`,`live_allow`,`confirm` (`events`) | 真机下发，三重闸 |
| `af_experience` | - | (`limit`) | 实体共现经验（只在成功落盘后采集） |
| `af_telemetry` | - | (`days`) | token/结果遥测 + 错误类别四维分布 |

**异常信封**：MCP 侧失败统一 `{"ok": false, "code": <ADM_ERR_*>, "message": ...}`（`af_mcp.py:915-925`），JSON-RPC 层 `{"content":[...],"isError":true}`（`:1112`）。**判降级态要看 `message` 字段的开头**是不是 `READONLY_DEGRADED:`（裁定 20261007 §二 戊A 把散文改成 JSON 信封后，判别点从"整段文本开头"挪到"`message` 值开头"；对端若还按老口径判，会永远不匹配 ⇒ 降级被静默读成"没降级"。这条变化已写进交接卡交 DB）。

**参数注入封死**：工具只认 `inputSchema` 声明的参数，"未声明却可用"的透传族已由 `scripts/check_param_injection.py` 判红（现读 100 文件 / 98 函数 / 3 处豁免，豁免按"数量 + 所在文件"一起钉）。

---

## 五、HTTP 路由表（**90 条**参与匹配的路由，当场从 `build_app(store_root, examples_dir, ui_dir, ui_user_dir, readonly)` 的 `app.routes` 取；鉴权缺省 fail-closed，`_read`/`_write`/`_live` 三档 scope）

| 族 | 路由 |
|---|---|
| 健康/元 | `GET /api/health`（开放） |
| 待批 | `POST /api/pending/list`、`POST /api/pending/approve`、`POST /api/pending/reject` |
| 凭据 | `GET /api/credentials`、`POST /api/credentials/update` |
| 归档 | `GET /api/graphs`、`GET /api/graphs/{name}`、`POST /api/graphs/tags`、`POST /api/graphs/enable`、`POST /api/graphs/disable`、`GET /api/store/export`、`POST /api/store/import`、`GET /api/diff` |
| 设计链 | `POST /api/build`、`POST /api/bind`、`POST /api/sim`（三者在 `_readonly_guard` 后） |
| AF-Spec | `GET /api/spec/{name}`、`POST /api/spec/compile` |
| 治理 | `GET /api/conf/{name}`、`POST /api/conf/{name}/intervene`、`GET /api/evidence/prod`、`GET /api/metrics`、`GET /api/faults` |
| 经验 | `GET /api/experience`、`GET /api/experience/export`、`GET /api/telemetry` |
| 洞察 | `GET /api/insights/pending`、`POST /api/insights/approve`、`POST /api/insights/reject` |
| ask 会话 | `GET /api/asks`、`GET /api/asks/{name}`、`GET /api/asks/pending`、`POST /api/asks/answer`、`GET/POST/DELETE /api/sessions*`、`POST /api/sessions/{id}/answer`、`/cancel`、`/tick` |
| 设备目录 | `GET /api/catalog`、`POST /api/catalog/refresh`、`POST /api/catalog/alias`、`POST /api/catalog/alias/remove`、`GET /api/catalog/aliases`、`GET /api/catalog/resolve-metrics`、`GET /api/entities`、`GET /api/entities/resolve`、`GET /api/entities/{entity_id}/state` |
| 真机 | `GET /api/live/status`、`POST /api/live/run`（`_live` scope） |
| 撤销 | `GET /api/undo/available`、`GET /api/undo/{deploy_id}`、`POST /api/undo/{deploy_id}`（`_live` + guard） |
| watcher | `GET /api/watch/list`、`POST /api/watch/start`（**`dry_live` 缺省 `True`**）、`POST /api/watch/stop`。**HTTP 侧没有常驻真机通道**：返回值如实给 `tier`=`dry_live`/`live_unconfirmed` 与 `real_device=false`，且 `ok=true` 必须由 sidecar 的 `graph` 等于本次那份 IR 路径证明 |
| 登录正规化 | `GET /api/auth/has-admin`、`POST /api/auth/register`、`POST /api/auth/login`、`POST /api/auth/logout`、`GET /api/auth/me`、`GET /api/auth/whoami`、`GET /api/auth/subjects`、`POST /api/auth/revoke` |
| 用户视角（ForgeSight） | `GET/DELETE/PATCH /api/user/agents(/{agent_id})`、`POST/GET/DELETE /api/user/auth-code(s)`、`GET/POST /api/user/pair/accepting`、`POST /api/user/pair/{code}/confirm` |
| 配对 | `POST /api/mcp/pair/request`、`POST /api/mcp/pair/redeem`、`GET /api/mcp/pair-request`（SSE） |
| MCP over HTTP | `POST /mcp` |
| 自动化 CRUD | `GET /api/automations`、`GET/DELETE /api/automations/{name}`、`POST /api/automations/{name}/enable\|disable\|archive\|unarchive` |
| 文档面 | `GET /docs`、`/redoc`、`/openapi.json`、`/docs/oauth2-redirect` |

另有 SPA catch-all 静态托管（`af_api.py:1389-1448`）与 1 个运行期挂载文件（路径由插件声明，静态读不出 —— 门禁把这条射程边界登记在册）。

**读写分档**：写端点过 `_readonly_guard`（503 带 `READONLY_DEGRADED:` 前缀），真机端点过 `_live` scope + `AUTOFORGE_LIVE_ENABLED`，批量端点过 `allow_bulk` 爆炸半径护栏。

---

## 六、CLI 命令表（18 条，来自 `typer` 命令注册表现读，不是 `--help` 文本抄写）

| 命令 | 用途 |
|---|---|
| `forge build` | 编译期安全闸：Schema + 静态扫描 + NL 渲染（`--entities --acl --guard --bind --root --json`） |
| `forge sim` | 逻辑闸：仿真里跑一遍（**先过安全闸**） |
| `forge run` | 内存态 Runtime（回放事件后退出）；`--live` 真下发、`--dry-live` 只记意图 |
| `forge watch` | 真机常驻监听（`--confirm` + 令牌 + `--live-allow` 白名单 + `--persist-dir`/`--tick-s`/`--undo`）。**没有 `--live`、也没有 `--vhass` 这两枚旗子**：真机档就是"不带 `--dry-live`"（`live = not dry_live`，`af_cli.py:585`），缺 `--confirm` 直接拒启动（`af_cli.py:565-566`） |
| `forge serve` | 只读 HTTP 服务层（`--ui-dir`、`--ui-user-dir`、`--store-root`、`--examples`） |
| `forge mcp` | stdio 模式 MCP server |
| `forge store` | 版本化存储：`save/log/tag/tags/enable/disable/export/import` |
| `forge pending` | 待批队列：`list/approve/reject` |
| `forge credentials` | HA/API 凭据（原子写 + 掩码）：`show/update` |
| `forge auth` | 令牌主体摘要/撤销：`list/revoke` |
| `forge spec` | AF-Spec ⇄ IR：`compile/render` |
| `forge diff` | 两份 IR 结构化差异 |
| `forge conf` | 置信度与自主级别（含衰减后） |
| `forge entities` | 设备目录：`refresh/summary/list/resolve/remember/aliases/forget/state` |
| `forge metrics` | 运行指标聚合与回灌 MA |
| `forge experience` | 实体共现经验 |
| `forge telemetry` | token/结果遥测 |
| `forge undo` | 回滚一次部署的设备态（F7） |

---

## 七、安全闸：`af_scanner.CHECKS` **43 项**（唯一真源 `af_scanner.py:39`）

按族分组（括号里是注册表原话的短版）：

| 族 | 检查项 |
|---|---|
| 结构与依赖 | `ENTITY_DEP_CYCLE`、`CROSS_DEP_CYCLE`、`STATIC_LOOP`、`EMIT_SELF_LOOP`、`EMIT_STORM_LIMIT`、`DUPLICATE_EDGE_PRIORITY`、`RESERVED_NOT_IMPLEMENTED` |
| 实体与目录 | `ENTITY_NOT_FOUND`、`ENTITY_OFFLINE_NOW`、`ENTITY_WRITE_CONFLICT`、`ENTITY_ACL_DENIED`、`ENTITY_GUARD_TIER0` |
| 风险分级 | `L3_ACTION`、`L2_NEEDS_CONFIRM`、`L2_NEEDS_CANARY`、`HIGH_RISK_AFTER_SUSPEND`、`LOW_CONF_WRITES_DEVICE`、`SHADOW_WRITES_DEVICE` |
| 中断与挂起 | `MISSING_TIMEOUT_OR_DEFAULT`、`CANCEL_SPAWNS_INSTANCE`、`NESTED_SUSPEND_IN_CANCEL`、`ASK_AT_RUNTIME`、`DO_WITHOUT_ON_ERROR`、`CONFIRM_WITHOUT_DENY_PATH` |
| 变量与表达式 | `UNDECLARED_VAR`、`CROSS_AUTOMATION_VAR`、`EXPR_INVALID`、`PARAMS_TOO_DEEP`、`TRIGGER_INVALID`、`TRIGGER_STALE` |
| 后置条件 | `EXPECT_MISSING`、`EXPECT_UNREACHABLE`、`EXPECT_STATE_INVALID` |
| 并发与快照 | `NON_IDEMPOTENT_CONCURRENT`、`SNAPSHOT_FALSE_MULTI_AND`、`ADAPTER_POLICY_PARAM` |
| 真机三重闸 | `LIVE_TOKEN_REQUIRED`、`LIVE_CONFIRM_REQUIRED`、`LIVE_WHITELIST_REQUIRED`、`LIVE_ENTITY_NOT_WHITELISTED` |
| 出站与覆盖 | `HTTP_NOT_WHITELISTED`、`NL_COVERAGE`、`INBOX_PARAM_TOO_LONG` |

两个"名字像检查项但性质不同"的说明，别混进上表的读法：

- **`L2_NEEDS_CANARY`**：会真发诊断（`af_scanner.py:429`，ERROR 级）——L2 动作标了 `requires_confirm=true` 却没配 `canary` 灰度时拒（P1-2：不给"用确认位换免费豁免"）。**该批（执行记录 §二之八十三）已登记进 `CHECKS`（`:55`）与 `CODE_HINT`（`:112`）**，所以 43 项含它。收口的不是那条判断（一直在），是"这项检查在目录里查得到、且发得出时 `hint` 非空"这两格；防复发由 `tests/unit/test_diagnostic_code_catalog.py` 八条腿盯（通用腿＝仓里 `Diagnostic("X")` 字面量发得出的码必须两本目录都在）。
- **`IR_SCHEMA`**：不是扫描项，是错误知识的**分类键**（`af_error_knowledge.py:60/81/115`），把 schema 校验报错归到"补必填字段"的修复建议上。

43 项里唯一一枚"由裁定新增、而不是由缺陷新增"的诊断是 **`CONFIRM_WITHOUT_DENY_PATH`**（裁定 20261009（第二份）§二 2.2）：节点标了 `requires_confirm` 却没有 `no`/`on_timeout`/`default` 任一出口时出**告警**。级别必须是 WARN——被拒后照既有边纪律落 `done` 是 **Q2 裁的甲**（与 `ask` 答"不要"同一条路），不是缺陷，所以这条诊断**不拦发布**（`ScanResult.ok` 只看 errors）。它要挪走的只是"作者没写拒绝出口 ⇒ 那条走向只在运行期静默发生、图里读不出来"。落点：检查方法 `af_scanner.py:509-527`（`Diagnostic` 字面量 `:520`）、调用点 `:369`（紧跟 `_check_suspension`）、`CHECKS :56`、`CODE_HINT :99-100`；判据 `tests/unit/test_confirm_exit_diagnostic.py` 十一条腿（含"三枚出口名 = 执行器拒绝分支词汇"那条同源腿）。

第 43 枚 **`INBOX_PARAM_TOO_LONG`**（执行记录 §十八 残余 B.9 的后半格）管的是**收件箱载荷超长**：`adapter: inbox` 的 `do` 节点里 `text`/`title`/`body`/`content` 任何一枚超过库侧上限 ⇒ **ERROR、拦发布**。级别与上一条相反的理由是契约 §1.3 已经写明 DB 侧对超长载荷 fail-closed **丢弃**：这条动作发出去也必死，让它进库、进演练台账、跑到执行才红，等于把"必然失败"伪装成"已配置好"（与 `L2_NEEDS_CANARY` 同族）。**上限一个数字都不写在 AF 里**：`af_adapters/inbox.py:57` 的 `INBOX_LEN_LIMITS` 格子里放的**就是** `from homesdk.presence import INBOX_MAX_TEXT/INBOX_MAX_TITLE/INBOX_MAX_BODY` 那三枚常量本身，`inbox_overlong_fields()` `:74` 是唯一判定入口。这里刻意不走按名派发：对 `homesdk.presence` 成员的 `getattr` 访问在 `af_adapters` 里是 `scripts/check_mqtt_writers.py` D 判据的射程（机制层入口只许在桥内），而直接 import 让"库侧改名"变成 AF 的 **import 期** `ImportError`，比运行期才炸响亮；扫描器 `af_scanner.py:709-728`（字面量 `:720`、调用点 `:379`）里连一个整数字面量都不许出现——那条"判超长的数＝拒超长的数"由 `tests/unit/test_inbox_param_length_diagnostic.py` 用 AST 解析库侧源码自证（三个 builder 里 `_len_bounded(arg, CONST, "field")` 的字段名单必须与该表逐 kind 相等，且每格的值等于 `getattr(_presence, CONST)` 的现读值；另有两条腿钉住"本文件零 `getattr` 派发、零整模块 import"与"表里的值就是库侧那枚常量对象"）。满射程的依据：执行器把 `node.params` 原样交给适配器（`af_executor.py:741`，运行期不做插值），编译期量的就是运行期那份长度；同一份超长载荷在两端的读数由两条腿同时钉（扫描器 ERROR + 适配器 `ADM_ERR_PAYLOAD_INVALID`）。

另外：`ENTITY_DEP_CYCLE`/`CROSS_DEP_CYCLE`/`EMIT_SELF_LOOP` 走变量传码（`af_scanner.py:1147-1158`），`LIVE_*` 四项走模块级字符串常量（`:1287-1290`）——只按 `Diagnostic("字面量", …)` 的 AST 扫这七项会误判成"注册了却从不发出"。

高风险面：`lock`（门锁）、`water_heater`、`climate` 等按 Tier 取最严（`DeviceGuardRegistry`，Tier-0 读取/写入都要人工审批）；`conf < 0.6` 只出 ask 提案、禁写设备。

`classify_action(adapter, action)` 的 domain 取值有两条容易踩的规定（`af_adapters/base.py:165-190`）：① domain 优先取 `action` 的 `<domain>.<service>` 前缀，**动作名不带点时退回适配器名**——因为 DSL 的 `do d1 inbox.speak {…}` 会被 partition 成 `adapter=inbox` + `action=speak`，只按动作名判会让所有非 HA 适配器掉进"未知 domain ⇒ L2"，`af_scanner` 随即报 `L2_NEEDS_CONFIRM`/`L2_NEEDS_CANARY`，这条自动化连编译都过不去；② `inbox` 登记在 `_L0_DOMAINS`（IR §8.1 的"L0 只读/通知"）：AF 只把话交给 DB，播不播由 DB 的 Sentinel 判，AF 侧没有"动设备"的后果可言。缺省档（未知 domain ⇒ L2）与删除类关键字 ⇒ L3 的优先级都**没有**放宽，`tests/unit/test_inbox_contract_keys.py` 与 `tests/unit/test_inbox_pipelines.py` 各钉了一条反例腿。

---

## 八、执行档位速查：预演 / 仿真 / dry-live / 真机（**四组正交的档，别混**）

| 组 | 取值 | 碰真机 | 落盘 | 怎么调 |
|---|---|---|---|---|
| **写侧 stage** | `check` / `simulate` / **`dry_run`** / `save` | ❌ 全不碰 | 只有 `save` 入队 | `af_apply(ref, stage=…)`、`POST /api/…`、CLI |
| **真机侧** | 无 live / **`--dry-live`** / `--live`（`--live` 只有 `forge run` 有） | dry-live ❌、live ✅ | — | `forge run --live --confirm --live-allow`、`forge watch --confirm --live-allow`（**watch 无 `--live` 旗子**）、`POST /api/watch/start`（缺省 dry-live，且**只能到 dry-live**）、`POST /api/live/run`（一次性下发，要 `confirm` + `live_allow`） |
| **自主 band** | `auto` / `shadow` / `ask` | shadow/ask 不真动 | shadow 写 `shadow_log` | 运行时 conf，非入参 |
| **冲突档** | `off` / `observe` / `enforce` | — | 冲突审计 | `AUTOFORGE_CONFLICT_ARBITER` |

**安全旗子哪枚真有人在跑的时候读**（2026-10-09 现读，锚点按本批落码后的 `af_executor.py`）：`canary` **有**运行期消费者——`:646` 取 `dry_run`、`:663-670` 判 `use_canary`（含 `not dry_run`）、`:610-619` 起 `CanaryGuard.perform`、`:650` 挂观察期（`kind="canary_observe"`），漂移与反向回滚在 `resume` 的 `:243-331`。`requires_confirm` **现在也有**（裁定 20261009 §三 硬前置，本批补上）：`_do` 入口 `:648-659` 判这枚旗——没拿到授权就一次都不下发，挂成一次人工确认会话（`_suspend_for_confirm` `:864-891`，复用现成的 `pending_asks` 与 `/api/asks`、`/api/asks/answer`、sidecar/inbox 那张已有脸，`AskSession` 字段一字未加）；唤醒在 `resume` `:211-241`——yes ⇒ `confirm_granted` 是一次性把手，就地重进同一节点执行一次并落一条 `confirm_granted` 审计；no / `on_timeout` ⇒ 一条动作都不发（fail-closed），落 `confirm_denied` 审计，终态照既有边纪律选路——图里没有 `no`/`default` 边就落 `done`（`resume` 尾 `:333-338`，与 `ask` 拒绝同一条路，不为确认单开特例）；**这一格现在编译期也看得见**：受确认节点缺 `no`/`on_timeout`/`default` 任一出口时出 `CONFIRM_WITHOUT_DENY_PATH` **告警**（裁定 20261009（第二份）§二 2.2，落点见 §七，不拦发布）（两枚事件名在 `af_audit.py:51-52`、注册进 `ALL_EVENT_TYPES :75-76`）。编译期那三张脸照旧在（字段 `af_ir/models.py:398/:439`、L2 策略表 `af_scanner.py:412-436`（§二之八十四 钉 `:406-430`，本批两本目录插行后按现读再钉）、编排与闭环的修与检 `af_orchestrator.py:683/:793/:1597`、`af_closedloop/fixers.py:53`/`detectors.py:84`/`deepfix.py:123`），判例 §六 3 那句「编译期看见 ≠ 运行期兑现」现在两半都对得上了。⇒ "节点标了 `requires_confirm` 就会先问人"从今天起才是真的：但**只在实际会写出去的那条路上问**——适配器是 dry 时（预演 / dry-live）这一格不挂起，只留 `confirm_skipped_dry_run` 痕（`:657-659`，与 canary 跳过 dry 同一口径）；这一处与先前写下的落码档「预演档不豁免」不一致，改口理由与代价记在执行记录 §二之八十一，并按纪律向 DCD 交追认——**裁定 20261009（第二份）§一 已回、追认甲**：确认闸的存在理由是"不可逆真写要人点头"，预演档零字节上线就没有可确认的实体，逐格挂起会把演练场卡死；裁的正是"跳过 + 记痕"而不是"静默跳过"，所以 `confirm_skipped_dry_run` 那一痕是这条裁定的**关键半边**，不许省。进程重启也不等于放行：`restore_persisted()` 末尾调 `reseed_sessions`（`af_runtime.py:203`、`af_executor.py:507-565`）把落盘时挂起的 `ask` / 人工确认会话重挂回 `pending_asks`，实例照旧 suspended、恢复过程零下发；挂起节点已不在当前图就落 `instance_session_lost` 具名审计（`af_audit.py:35-37`），不许静默跳过。**这一格闭合 ≠ 常驻真机通道可以开**：还押着 Q2=甲 的逐条勾与 Q3 试演台账（UI/HTTP 脸，#83）与现场写闸关回 0（#84）。

**预演（`dry_run`）的准确定义**：`build` + `simulate` 都真跑，然后**在入队之前返回**，因此——

- 不消费首演码（一次性码用掉就没了，"先看看"不该有代价）；
- 不进待批队列、不进试演期、不写 pending 文件；
- 返回值自带正面证据：`{"stage":"dry_run","dry_run":true,"would_enqueue":true,"pending_ref":null}`。

读法：`would_enqueue=True` 说"真跑 `save` 就会入队"；`pending_ref=None` 说"此刻队列里确实没有它"。**这条档就是 DB 侧「拟→验→批→部署」里的"验"**，可以随便重复调。

收件箱动作（`do d1 inbox.speak {"text": …}`）在这档同样**零上线**：`InboxAdapter(dry_run=True)` 走记录代理，只在进程内留一条意图，而留的就是库侧真要发出去的那份字节（`payload` 由上线的 `body` 反解回来，AF 不重拼第二遍）。所以"预演说音箱播这句"与"音箱该收到这句"结构上不可能不一致。

仿真"跑对了吗"看两格：`expect.ok`（没抓到反例）与 `expect.fully_verified`（声明过断言且全验过）。证据强度：`verified_in_prod` > `fully_verified` > `verified` > `inferred` > `non_simulable` > `exempted`（详见架构文档 §九）。

---

## 九、测试通道怎么用（批量跑题 / FFL 类）

```python
# MCP over HTTP（推荐 Python，PowerShell 传嵌套 JSON 会崩）
payload = {"jsonrpc":"2.0","id":1,"method":"tools/call","params":{
    "name":"af_test_submit",
    "arguments":{"intents":[{"name":"防盗门开→开客厅灯","when":{...},"do":{...}}, ...],
                 "batch_id":"ffl-20261009"}}}
```

三步：`af_test_submit(intents ≤500)` → `af_test_report(batch_id)` → `af_test_clear()`。

- 隔离：正式区 `/data`，测试区 `/data/test/{pending,graphs,reports}`；报告是 `reports/{batch_id}.json`，原子写（WebUI 轮询读，崩半截会被读成"没有这份报告"）。
- 每条走 `draft → build → simulate → 直接归档到测试区（tags=["test"]）`，**不占正式待批队列**（旧文那条"跑 200 题必须用 simulate 否则熔断"的坑，正是被这个通道取代了）。
- **不碰真机**：中间档只到 simulate。
- 报告字段：`batch_id/total/pass/fail/pass_rate/fail_reasons/details/created_at`；`fail_reasons` 按失败 stage/错误码归类，适合直接当通过率报表。
- `af_test_clear()` 的删除面（执行记录 §二之八十六收口，架构文档 §十八 B.3）：删除目标**由服务端定**——工具 schema 零参数，Agent 递不进路径；`TestChannel.clear()`（`af_test.py:298-328`）删之前必过 `assert_test_area_deletable(test_root, protected_root)`（`:71-97`）两档形状守卫：目标就是盘根 ⇒ `TEST_ROOT_IS_FILESYSTEM_ROOT`，目标**等于或包住**这个进程真正在用的正式存储根 ⇒ `TEST_ROOT_COVERS_PRODUCTION`（对照量是 dispatch 手里那枚 `store.root`，本模块不抄第二份配置，也不拿 `/data/test` 那种字面默认值当判据）。`protected_root` 是**必填关键字参数**，少递一个就是 `TypeError`——宁可当场炸，也不在"没人知道正式区在哪"的状态下做不可逆删除。删之后 `residual` 是**数出来的**：`shutil.rmtree(..., ignore_errors=True)` 已撤，部分失败如实回 `ok=false/cleared=false` + 封顶 20 条的现场明细（`errors_total` 报真实条数）。守卫拒判走**工具语义**（`ok:false` + 具名 `code`），不把异常抛给对端。判据 17 条在 `tests/unit/test_test_area_delete_guard.py`。
- **政策半边已由 DCD 裁掉**（`20261010-AF测试区删除面守卫与政策口-裁定`，2026-10-10，L2；申请见 `20261010-AF-测试区删除面守卫落地与剩余政策口-决策申请`）：**§二 裁甲＝维持形状守卫**，不加"只准删 `{store.root}/test` 前缀"的白名单闸。裁定给的理由是乙的正确取值取决于 NAS 部署里正式根与测试区的真实位置关系（AF 这台机器读不到），硬写前缀等于 AF 自己抄第二份部署事实；**乙仍列候选**，改判需要 DB 先给一句权威树的现读路径。⇒ 记牢这条**已知射程边界**：`test_root` 被指到正式根的**同级**目录（例如 `.forge-backup`）仍然照删，这是裁定口径，不是遗漏。**§三 裁甲＝维持现状**：scope `write`、只有 MCP 脸、无面板格（现读两棵第一方 UI 树对 `af_test` 0 命中、`/api/test/*` 路由 0 条）。**§四 判例入册**：**不可逆操作的守卫，对照量必须由调用方现递，被守卫的模块不许自带第二份真值**——威胁不来自 Agent 递路径（schema 零参数递不进），而来自服务端自己那份 `test_root` 指错了地方。
- 只有 MCP 脸，没有 `/api/test/*` HTTP 路由（现读：`build_app` 路由表零命中）。

---

## 十、用户视角 WebUI（`/mimo/`）怎么用

**树 = `ui-user-mimo/`**（不是 `ui-user/`，那棵已冻结归档，且登录是坏的）。Vue 3.5 + naive-ui 2.40 + pinia + PWA。

| 路由 | 用途 | 守卫 |
|---|---|---|
| `/login` | 首设（注册）/ 登录 | 公开 |
| `/` → `MainLayout` | 外壳 | 需登录 |
| `/agents` | Agent 配对：生成 8 位码、SSE 等兑换、列已配对、改名/撤销 | `beforeEach` 查 `store.user` |
| `/automations` | 自动化列表 + 待批 approve/reject | 同上 |
| `/auth-codes` | 授权码（明文按 owner 分层掩码） | 同上 |
| `*` | → `/agents` | — |

**卡片那几个格子读的是哪里**（2026-10-09 修）：列表页/详情页字段唯一正源是 `svc.automation_card`（`af_service.py:461-518`），端点只做参数搬运。此前端点直接在归档容器层读 `nodes`/`enabled`/`nl`，而 `_graph_raw` 写出的容器层只有 `{"automations": [...]}`（`af_store.py:56-58`）——所以 12/12 条一律渲染成「无设备 / 空预演 / 恒已启用」，"停用"也落在没人读的位置（**运行期真读这枚旗子的是调度器**：`af_scheduler.py:87-89` 不为禁用项注册触发、`:241-243` 不排空队列）。现在：设备 = `auto.reads() | auto.writes()` 去重 + `DeviceCatalog.display_names()`（**只读缓存、不发网络**，缓存没有的实体回显 entity_id 本身）；启停 = `all(auto.enabled)`，空归档算"未启用"；预演 = `render_graph(graph).text`，一条坏归档只在自己那格写 `⚠ 归档无法解析…`，不再把整个列表打成 500。**试演期/最近触发/近 7 天没有按自动化名的落盘正源**（首演-试演台账按 `store_diff_sha256` 记，`af_apply.py:208`），所以 `trial: null`、`last_triggered: null`、`trigger_7d: 0`——读不出就留空，不替用户宣布"已进全自动档"。启停写侧 `svc.set_automation_enabled` → `store.resave_raw`（锁、版本号、归属核对都在 store），容器形状不认识 ⇒ 抛 409 且**不落新版本**。判据：`tests/unit/test_user_ui_card_and_toggle.py`。

登录与首设（`e5b3fd5`）：

1. `LoginView.vue` 挂载即 `GET /api/auth/has-admin`；`false` ⇒ 界面切到"设定账号密码"（注册），`true` ⇒ 登录。
2. 注册 `POST /api/auth/register`：已存在则 409，成功即自动发令牌。密码 **PBKDF2-HMAC-SHA256 / 100000 轮 / 16 字节随机盐**，落 `{store_root}/.auth/admin.json`（0600），校验走常数时间（用户名不存在也照样算一次哈希，防时序探测）。
3. 令牌 TTL 缺省 86400s；前端存 localStorage（键 `forgesight_token`），`Authorization: Bearer` 发出；401 ⇒ 清 token 回登录页。
4. 管理员尚未注册这一窗口内，`login` 接受任意非空凭据并回 `warning`（兼容档）；注册后转严格校验。

数据面：单 store（`src/stores/main.ts`：user/agents/automations/pendings/authCodes/pair/darkMode + 派生 getter），纯逻辑在 `src/logic/*.ts`；API 层 `src/api/env.ts`（`USE_MOCK` **显式开**才走 mock，缺省真后端；`API_BASE=VITE_API_BASE ?? ''`；`MCP_URL` 同源 `${location.origin}/mcp`）+ `src/api/http.ts`（错误信封按 `detail||error||message` 取，SSE `pair-request` 带 `?token=` 回退）。

本地开发：`npm run dev`（端口 5175）→ 代理 `/api`、`/mcp` 到 `192.168.2.200:8787`。构建：`npm run build` 出 `dist`，NAS 挂到容器 `/mimo`。**`vite base` 必须等于后端 `UI_USER_PREFIX`**，不一致就"200 但白屏"（`/mimo/assets/*` 对不上挂载前缀）。

---

## 十一、开发者控制台（`/`，dist 挂 `/ui`）

`ui/`：Vue 3.5 + naive-ui 2.45 + pinia 3，20 条懒加载路由：
`/overview` `/automations` `/automations/:name` `/devices` `/data` `/simulation` `/confidence` `/versions` `/spec-editor` `/faults` `/metrics` `/pending` `/live` `/governance` `/running` `/evidence` `/insights` `/asks` `/404`。
API base：`VITE_API_BASE ?? 'http://localhost:8787/api'`。这三棵树的调用点由 UI↔路由门禁双向对账（`scripts/check_ui_api_paths.py`），含"反空洞自证"（0 调用点会判红）。

---

## 十二、部署与运维

### 12.1 环境变量（键名真源在代码，不在本文）

| 键 | 缺省 | 语义 |
|---|---|---|
| `AUTOFORGE_LIVE_ENABLED` | 仓内 compose 写 **`${AUTOFORGE_LIVE_ENABLED:-0}`**（`docker/docker-compose.api.yml:45`）；**NAS 现场被宿主 env 注入成 1** | 真机总闸；没开 ⇒ `live_run` 403。**仓内缺省 ≠ 部署事实**：判这一格读 `GET /api/live/status`，别拿 compose 默认值当现场 |
| `AUTOFORGE_HA_URL` / `AUTOFORGE_HA_TOKEN` | 空 | HA 地址/长期令牌（**值只从 secret 或宿主 shell env 注入**） |
| `AUTOFORGE_TOKENS` | 空 | 多主体令牌表（**单数**，JSON，每条自报 subject+scopes） |
| `AUTOFORGE_SECRET_DIR` | `/run/secrets` | secret 文件目录 |
| `AUTOFORGE_SESSION_TTL_S` | 3600 | 会话 TTL |
| `AUTOFORGE_INBOX_KEY` | 空 | ask 通道 HMAC（与 DB 共享同一 key；未设 ⇒ 读侧拒收全部 inbox 答案）。**不是收件箱投递的凭据**：计划 §七 卡1 说的"缺凭据拒发"在 AF 侧指**桥缺席**（`AUTOFORGE_MQTT=0` 或 `start()` 没成功）⇒ `ADM_ERR_AUTH_REQUIRED`，与这枚 key 无关，别拿它当投递门 |
| `AUTOFORGE_MQTT` | 0 | 联动桥开关（空串/0/false/no/off 都读成"关"） |
| `MQTT_HOST/PORT/KEEPALIVE/USER/PASSWORD` | 空 | 键名真源是烘进镜像的 wheel（`homesdk/mqtt.py`）；空 `MQTT_HOST` 抛 `MissingEnv` 而非匿名连出 |
| `AUTOFORGE_CONFLICT_ARBITER` | `off` | 冲突守卫 `off/observe/enforce` |
| `AUTOFORGE_CONFLICT_*` | 见 `_ENV_FLOATS/_INTS/_BOOLS` | 锁 TTL/冷却/抖动/熔断/老化/等待等 14 枚 |
| `AUTOFORGE_MCP_ALLOW_NO_TOKEN` | 未设 | 只认字面量 `1`；原型全放行 |
| `AF_ALLOW_NOAUTH` | 未设 | 匿名读逃生阀（`AF_REQUIRE_AUTH` **已废弃**） |
| `HOMESDK_TZ`（规范）/ `AF_TZ`（别名） | 未设 ⇒ `Asia/Shanghai` | 家庭墙钟时区。读取序 `af_time.py:97-104`：每个基键先试 `HOMESDK_` 前缀再试裸键，全序 `HOMESDK_TZ` → `TZ` → `HOMESDK_AF_TZ` → `AF_TZ` → `HOMESDK_TZ_OFFSET_HOURS` → `TZ_OFFSET_HOURS`；`/api/health` 的 `tz` 会报"env 生效还是落 fallback" |

读取优先级：**credentials.json > secret 文件 > 环境变量**。compose 自动加载的 `.env` 在**文件同目录**（`docker/.env`），不是仓库根。

### 12.2 怎么读健康（每个键都是哪一道）

```json
{"ok": true, "version": "0.1.0", "contract_version": "1.0",
 "milestones": ["G1","G2","G3","G4","G5","真机接线","G6","G7"],
 "readonly": true,                      // 身份声明（v1.x 能力面），不是运行期档位
 "write_gate": "open|blocked|no_lease", // 运行期写闸真值（裁定 20261008 §一 B）
 "store_ok": true, "linkage": {...},    // 没递桥 => wired=false / unwired，不许读成健康
 "tick_health": {...}, "ticker_alive": true, "tick_exit_reason": null,
 "tz": {...}}
```

`linkage` 展开（`af_mqtt_bridge.linkage_status`，AF 不抄第四套状态词，`state` 取 `homesdk.adm.status` 三常量）：

```json
{"wired": true, "state": "online|degraded|offline", "degraded": true,
 "reasons": ["ADM_ERR_BROKER_UNREACHABLE"],     // 与 retained status 载荷同一来源
 "publish_errors": 0,
 "inbound": {"subscribed": ["ma/presence", "ma/device-health"],  // subscribe_linkage=False ⇒ []
             "presence_in": 12, "device_health_in": 3, "rejected": 1,
             "feed": {"wired": true, "root": "…/linkage_events", "limit": 500,
                      "ttl_s": 86400.0, "per_kind": {"presence": 12, "device_health": 3},
                      "unreadable": [], "watermark": "1759996800123|presence|-9f3c1a2b.json"}}}
```

`inbound` 是卡3 那一半的读数面，四条都可证伪：`subscribed` 为空 ⇒ 这条进程根本没订（`--no-linkage-subscribe` 一类），不是"对端没发"；`feed.wired=false` ⇒ 桥在但没给落盘队列，于是每条入向事件都进 `rejected`、`presence_in` 恒零（这比"看着健康其实全丢了"好读）；`unreadable` 非空 ⇒ 盘上有读不出来的条目（诊断环形清单，最多 50 条，不参与任何判定）；`watermark` 是**已消费到哪一条**的水位，只随 tick 线程抽取而推进，`serve` 里它恒为空串（见 3.4）。

**"readonly=true 但写面 200"不是矛盾**：前者是身份，后者看 `write_gate`。`no_lease` = 探不到租约状态，**绝不塌回 `open`**。

### 12.3 起停与验收

- 起：`docker compose -f docker/docker-compose.api.yml up -d --build`（后端变更**必须**重烘镜像，运行期不挂 src）。
- watch 不在 compose 里起（`command:` 只有 `forge serve`，`docker/docker-compose.api.yml:23`）：要么 `POST /api/watch/start`（**只到 dry-live**，真机常驻得进容器手敲 `forge watch … --confirm --live-allow …`）；单实例靠 `{persist}/watch.lock`(+`.info`)。
- `POST /api/watch/start` 的 `ok=true` 现在要 sidecar 自证身份：旧形状读到目录里那个**唯一**的 `watch.lock.info` 就回成功，上一条 watch 的残留会被当成"本次启动成功"（2026-10-09 现场：1.18s 回 `ok=true`，带的是 9-29 另一条 IR 的路径）。认不上就分 `child_exited` / `coord_lock_held_by_other` / `not_registered` 三种如实失败。
- 窗内验收一条命令：`docker compose exec autoforge python scripts/verify_adm_window.py`（PASS/FAIL/UNAVAILABLE 三态，缺项读不成绿）。
- CI：`.github/workflows/ci.yml`；远端读数用仓内脚本 `scripts/gh_ci_status.py runs|jobs|log`（本机无 `gh`）。
- 推：`git push origin master:main` + `git push nas master`，推后 `ls-remote` 自证。

---

## 十三、命令速查（凭据用占位符，不写真值）

```bash
# MCP over HTTP（Python 传参，别用 PowerShell 传嵌套 JSON）
curl -X POST http://192.168.2.200:8787/mcp \
  -H "Authorization: Bearer $AF_MCP_TOKEN" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"af_apply",
       "arguments":{"ref":"af:xxx","stage":"dry_run"}}}'

# 预演（零写入，可反复调）
... "name":"af_apply" ... "stage":"dry_run"

# 测试通道批量 → 报告
... "name":"af_test_submit" ... "intents":[...], "batch_id":"ffl-20261009"
... "name":"af_test_report"  ... "batch_id":"ffl-20261009"

# CLI
forge pending list --root /data
forge pending approve --root /data <op_id>
forge store log --root /data
forge sim <ir.json> --seed ... --events ...
forge run <ir.json> --dry-live --ha-url http://192.168.2.200:8123
forge watch <ir.json> --confirm --ha-url ... --live-allow light.xxx
forge undo <deploy_id>

# 本地整树判据
GATES_PYTHON="<py313>" bash gates.sh
PYTHONPATH=src <py313> -m pytest tests -q
```

⚠️ 本文旧版在 §5.4/§11.1/§9.1 里写过**一枚真实 MCP 令牌的明文**。现已换成占位符，但那枚值仍在 git 历史与 GitHub 上 ⇒ **按已泄漏处理，去 NAS 侧换掉并改 `AUTOFORGE_TOKENS`**。今后任何令牌/口令字面量都不进文档、不进 compose 入库文件。

---

## 十四、关键实体映射（实测过的样例，用于手工回归）

| 中文名 | entity_id |
|-------|-----------|
| 防盗门 | `binary_sensor.0x00158d0001f34db6_contact` |
| 木门 | `binary_sensor.0x00158d0000d6de14_contact` |
| 房间门 | `binary_sensor.0x00158d0001a2237a_contact` |
| 书房人体 | `binary_sensor.0x00158d0001a2520d_motion` |
| 卫生间存在 | `binary_sensor.649e314cdeeb_occupancy` |
| 客厅存在 | `binary_sensor.649e3151e45f_occupancy` |
| 显示器挂灯 | `light.yeelink_cn_555003624_lamp22_s_2` |
| 客厅灯 | `light.mijia_cn_group_1861372413196005378_group4_s_2_light` |
| 书房温度 | `sensor.duka_cn_blt_3_1orsfvt24cc01_th2_temperature_p_2_1001` |
| 书房空调 | `climate.lumi_cn_84159632_v2` |
| 门锁 | `lock.smart_lock` |
| 客厅电视 | `media_player.xiaomi_rmh1_6103_play_control` |
| 客厅人数 | `sensor.xiaomi_cn_820783783_p1_people_num_p_3_12` |

**这份映射的真源是设备目录缓存，不是这张表**：日常写 IR 请走 `af_resolve_entity`（中文名→候选 ID）/ `af_refresh_catalog`，表只用于回归样例对照。表里的 ID 含设备 MAC 派生段，属本户数据，不外发。

---

## 十五、常见坑与判读（都是踩过的）

1. **打错 stage = 部署**？不会了：未知 `stage` 直接拒（`af_apply.py:92-98`）。
2. **`ok=True` 不等于验过**：看 `fully_verified`；断言实体图里既不读也不写 → `EXPECT_UNREACHABLE` 编译期就拒。
3. **前端 200 白屏**：`/mimo` 三处不同源（vite base / `UI_USER_PREFIX` / compose 挂载）。
4. **改的是废树**：用户视角 UI = `ui-user-mimo`，`ui-user` 已冻结且登录是假的。
5. **compose 改了不生效**：后端源码烘在镜像里，要 `--build`；inode 类变更要 `--force-recreate`。
6. **鉴权键名写成复数**：`AUTOFORGE_TOKENS` 单数，写错就是"注入得再认真也是喂空位"，且不留原因。
7. **`.env` 放错目录**：compose 认 `docker/.env`，不是仓库根。
8. **打开 MQTT 桥 = 可能整个服务起不来**：桥排在 `uvicorn.run` 前且不吞异常，推送前先跑 `paho_available()` / `broker_settings()` 预检。
9. **降级被读成没降级**：MCP 异常面已是 JSON 信封，判前缀要判 `message` 值开头。
10. **名字哨兵罩住 docstring**：按名字 grep 的门禁会扫到注释/散文（同源代码里的第二副本），改常量名要连文案一起改。
11. **"没比成"被读成"相等"或"不等"**：把整格值交给 `json.dumps(sort_keys=True)` 做规范串，会让 `("x","y")` 与 `["x","y"]`、`{1:1}` 与 `{"1":1}` 塌成同一个串（假绿），而 date/set/自引用是直接抛穿（连报告都没有）。判据的诚实分档只有三档：相等／不等／**无法比较**——第三档必须是一等输出（`FidelityReport.not_comparable`），不许用 `False` 兼职（返回 `False` 会被读成"两条不等"），也不许用异常兼职（调用方拿到崩而不是报告）。见执行记录 §二之八十七。

---

## 十六、状态与限制（更正旧"待办"）

**旧文列为待办、现已交付**：CI（`.github/workflows/ci.yml`）、前端构建与同源、watch 的 HTTP 拉起 + dry-live 档、`af_draft` 实体解析接目录、IR 导出 `{"automations":[…]}` 形状约定、待批熔断的替代路径（测试通道）、真机三重闸在 MCP 面也装上、单写者租约、`write_gate`、登录正规化。

**仍然成立/新增的限制**：

1. `forge watch` 不由 compose 自动起（要显式 `POST /api/watch/start` 或容器内拉起）——这是**设计**，避免只读服务层意外获得下发能力。
2. `af_nl_parse`（自由文本 → IR）有实现但**无产品调用方**；F14 P2 的 `build_ir_from_nl` 仍未接上主链。
3. `af_irreversible` 只有 NL 渲染侧调用方，执行面未接。
4. ~~`af_fidelity._canon` 仍是裸 `json.dumps`（与第十三轮修掉的 `_leaf_key` 同形），需要给 `FidelityReport` 加一档才能如实表达"无法比较"~~ **已由执行记录 §二之八十七 收口**（编号保留、不挪后续条目）：`_canon` 改成带类型标记的递归编码（JSON 词汇表；`bool` 先于 `int` 判；tuple 与 list 分别打 `t`/`l`；非 str 键与非有限浮点当场拒；深度走 `check_param_depth` 那份单一真值源，本模块零手抄数字），表外形状抛具名 `FidelityNotComparable`（带 `code`/`where`/`detail`，仍继承 `ValueError`），`FidelityReport` 新增 `not_comparable` 一档：`ok` 与 `projection_fidelity` 同时 False、`detail` 逐字写"无法比较（不是不等）"。判据 21 条腿 + 副本树变异 8 枚（7 枚有杀腿、1 枚是等价变异并如实登记）。**消费面**：`af_fidelity` 在 `src` 无产品代码调用方（全仓 src 唯一命中是 `af_nl_build.py:7` 的 docstring 提及，不 import、不调用），调用面只有 `tests/f14` 的开发/CI 验收，所以这一格修的是"校验器会不会自己造假绿"。
5. ~~`af_test_clear` 的无守卫 `rmtree`（§九）~~ **已由执行记录 §二之八十六 收口**（编号保留、不挪后续条目）：删除前过两档形状守卫（盘根／等于或包住正式存储根），对照量取调用方现递的 `store.root`；`ignore_errors=True` 撤掉，残留按盘面数出来、明细封顶 20 条。仍待裁的政策半边**已由裁定 20261010 收尾**（§二 甲＝维持形状守卫、不收成前缀白名单；§三 甲＝维持 scope `write` 且无面板；§四 判例入册），同级目录照删从此是裁定的已知边界，见 §九 那一格。
6. **本批（2026-10-09）已收，原先是 HEAD 上的 4 条红**：`_cooldown_pending` 没进有界容器注册表（现按"键空间是实体 id、同一实体反复失败不增长、给它封顶等于把实体放出 fail-closed 守卫"走就地豁免并写理由），路由/UI 计数棘轮没重钉（现钉 87 / 90 / mimo 22，两条新路由 `/api/auth/has-admin`、`/api/auth/register` 各有 `LoginView.vue` 里的真 fetch）。逐条定性与两树复测见执行记录 §二之七十六。**还剩的整树红只有 AST 棘轮 2 条**（`af_api.py:980`/`:1001` 返回字面量 `ok=True`，登录正规化 `e5b3fd5` 自带；HEAD 干净树带基线复测逐字 `新增/未获批 2 条（error 0 / warn 2），基线内存量 97 条`，全量口径 `99 / 上限 97`，涨的两条全在 `fake-ok-const`（77→79），`except-pass-broad=20` 未动）。上调登记上限是门禁自己措辞里的**评审动作**，改成真校验派生要动并发会话正在改的那两个函数体 ⇒ **AF 不自决，已交 DCD** `20261009-AF-AST两条未获批的ok字面量归属`（甲改派生 / 乙上调上限 / 丙入基线，AF 建议甲，另带 Q2「纯查询端点能否不写 `ok` 键」——现读唯一调用点 `LoginView.vue:30-33` 读 `data.has_admin`、注册读 HTTP 层 `res.ok`，无人读响应体 `ok`）。**裁定已回**（`20261009-AF两件ok字面量与平键登记-裁定` §一）：Q1=**甲**——`api_auth_register` 的 `ok` 改成派生自真读数（令牌真签出来才算成，即 `bool(token)` 那一族，与 `2f86af9` 的 `persisted`/`verified` 同形）；Q2=**允许删**——`api_auth_has_admin` 是纯查询，删掉 `ok` 直接返回 `{"has_admin": …}`，并明写**不接受拿恒真表达式骗门**；乙（上调棘轮上限）/丙（塞基线）双双驳回。**落码窗口也被钉死**：这两个函数正被登录线在途改写，裁定要求"由登录线提交后（或该线提交后的窗口）落码，不抢在途函数"⇒ 今天这两条红照旧在，AF 不动 `af_api.py`。判例 §四：假成功标记分两类——写操作→真校验派生，纯查询→删掉。远端同形：run 127 只有 `quality-gates` 红、`pytest` 已转绿。
7. `READONLY_DEGRADED:` 前缀在 homesdk 契约表的登记半边归 DCD／homesdk，现读两文档零命中。
8. 收件箱投递（计划 §七 卡1）三管线（编译/仿真/NL）与 fail-closed 已绿，但**`mosquitto_sub` 那一半验收要在 NAS 上做**，属合并窗动作，本批只到"上线字节由库侧生成并被测试反解核对"为止。原先并列的两格未接项**已收掉一半**：长度上限的**编译期预拒**由第 43 项 `INBOX_PARAM_TOO_LONG` 接住（上限现读库侧常量、不在 AF 写数字，见 §七），ERROR 级别已被**追认**；**契约 §1.3 护栏 3（按 source 每分钟限速）也已由裁定 20261010 §四 明确收尾**：Q1=**甲**（DB 在 `inbox_events` 落表处按 `source` 计数，超了丢弃+审计——DB 是唯一看得见"这个来源这一分钟总共收到多少"的地方），Q2=**不设独立分钟级 N**（由 Sentinel 的冷却期 + 每日预算承担，分钟级再加一层是重复约束）。⇒ **AF 侧这一格没有可落码的半边**（现读 `af_mqtt_bridge.py` 对 inbox 出站零限速代码，裁定也不要求 AF 加），两本册子的措辞由 DCD 同日改到位：`ADM联动主题注册表与消息契约.md` §1.3 护栏 3 已改写，并补了「上限数字为摘录，**真源＝`homesdk.presence.INBOX_MAX_TEXT`(500)/`INBOX_MAX_TITLE`(80)/`INBOX_MAX_BODY`(500)**；改数须同批改本表」那一行（这是我申请里点名的第三份会漂的手抄面）。**这一路还剩的只有 NAS 合并窗的 `mosquitto_sub` 验收**。
9. publish 失败目前只归一个码（`ADM_ERR_BROKER_UNREACHABLE`，原始 `rc=…` 写在 message 里）：ACL 拒绝与 broker 不可达在 AF 侧不做区分，是否要独立码已列为 DCD 待问项。
10. 计划 §七 卡3（订阅 `ma/presence`/`ma/device-health` 并落独立持久队列）仓内半边已绿，**没收的三样点名写出**：① 对端实际载荷是否逐键符合契约 §1.2 那两行，只有 NAS 合并窗的 `mosquitto_sub` 能对撞，仓内证到的是"契约要求的必填项缺了就拒收并带 `ADM_ERR_*`"；② 面板上没有"入向联动"这一格，唯一读数面是 `/api/health` 的 `linkage.inbound`；③ 队列没有 per-source 限速（与第 8 条同一护栏）。
11. 入向事件的**载荷按设备读得进、按成员数组仍读不进**（裁定 20261009 §四 甲已落地）：`spawn`（`af_instance.py:264`）除 `{entity_id, state}` 外再摊三枚平键进 `context`（`trigger_entity_id`/`trigger_subject`/`trigger_kind`，真源 `TRIGGER_FLAT_KEYS` `:479`），所以"哪台设备掉线"这一格现在能写进 DSL 分支；`_trigger_repr`（`af_instance.py:466-475`）本身一字未动。仍堵的是结构半边：`make_resolver`（`af_state.py:157-182`）是平表、`split_namespace` 用 `partition(".")` ⇒ `context.trigger.members` 这类嵌套路径读不出，"是妈妈回家才开灯"要等乙或卡2 的 `ma_query` 变量绑定。
12. `serve` 只入队、**不抽队**（该进程没有 ticker）：`linkage.inbound.presence_in` 会涨而自动化不动。分工不是缺陷，但读健康的人要知道这一格在 `serve` 里不代表"触发链活着"。
13. ~~**overwrite 导入的备份回收是静默的**~~ **已由执行记录 §二之八十九 收口**（编号保留、不占残余格；与早前收口后删除的第 13 条无关）：`af_store.py` 里那 5 枚 `shutil.rmtree(..., ignore_errors=True)`（修前 `:616`/`:627`/`:640`/`:653`/`:658`）全部撤掉，删除收成一个入口 `_purge_tree`（`:73-95`，rmtree 站点唯一在 `:86`）——失败按 `onerror` 逐站收集、残留按盘上现数。写新成功后回收失败 ⇒ `_drop_stash`（`:738`）返回明细并落 WARNING（`:749`），`import_bundle` 把它带进报告新格 `residual_backups`（`:806`/`:867-869`；MCP 整份透传、CLI `af_cli.py:956-959` 有 echo），`ok`/`imported` 不动（导入确实做成了，这一格不冒充条目失败）；回滚成功后壳没回收 ⇒ 只有 WARNING（`:732`，那一档在 raise 路径上、没有报告可写）；让位前预清理失败 ⇒ 碰正式区之前抛 `BackupNotReclaimed`（`:58` 继承 `ValueError`，抛出点 `:673`），不再抛那柄伪装成撞车的 `FileExistsError [WinError 183]`。真源：后缀 `BACKUP_DIR_SUFFIX :53`、明细封顶 `BACKUP_ERROR_DETAIL_MAX :55`。判据 `tests/unit/test_overwrite_backup_reclaim.py` 11 条腿（注入点在 `os.unlink`/`os.rmdir`＝rmtree 内部；日志读数面先发自证哨兵）＋变异 9 枚全杀。**上一批两处措辞已就地更正**：清理触发点不是"没有"而是只有一枚（同名下次 overwrite 前的预清理，实测确实回收得掉）；"备份目录越攒越多"不成立（名字按归档固定，同名复用）。剩半边：HTTP `_svc`（`af_api.py:777-784`）不映射这枚新错，到 API 仍 500（与修前同形），接 409 要动在途文件 ⇒ 随 #79。

---

## 十七、回归基线（HEAD `e5b3fd5` 当场面跑，原样贴回）

```
$ cd <git archive HEAD 副本树> && PYTHONPATH=<copy>/src <py313> -m pytest tests -q
5 failed, 3479 passed, 53 skipped, 1 warning, 65 subtests passed in 331.18s (0:05:31)
PYTEST_RC=1
FAILED tests/unit/test_bounded_caches_gate.py::test_real_repo_is_green
FAILED tests/unit/test_bounded_caches_gate.py::test_real_repo_measurements_are_pinned
FAILED tests/unit/test_pkg_markers_gate.py::test_real_repo_is_green_on_the_index_reading
FAILED tests/unit/test_ui_api_paths_gate.py::test_real_ui_and_src_are_clean_and_counted
FAILED tests/unit/test_ui_api_paths_gate.py::test_all_trees_of_this_repo_are_in_scope_and_green
```

逐条定性与归口在《AF完整架构与运行时说明.md》§十八 A：4 条真红（`_cooldown_pending` 未注册带来 2 条、登录新路由带来 2 条计数漂移）+ 1 条是副本树无 `.git` 索引的**测量口径**（工作区里那条是绿的）。这些与本次文档重写无关，属"产物已上线、钉住的读数没重钉"那一族；2026-10-09 工作区混合态整树跑批读数是 `10 failed, 3504 passed, 53 skipped, 65 subtests passed in 887.87s`（`PYTEST_RC=1`）——比 HEAD 多的 6 条全在鉴权线，对 HEAD 副本树单跑那四份鉴权文件得 `48 passed`（`PYTEST_RC=0`，51.38s），逐条定性见《AF完整架构与运行时说明.md》§十八 A；工作区当前有并发会话未提交的 `af_api.py`/`af_auth.py`/`ui-user-mimo/*`/`docker/*`，重钉要等那批落定后一次做，否则钉的是混合态。

---

## 十八、文档索引（真实路径）

| 文档 | 路径 |
|---|---|
| 本文 | `docs/architecture/AF完整知识文档.md` |
| 架构与运行时（机制/不变量） | `docs/architecture/AF完整架构与运行时说明.md` |
| IR & Runtime 模型 | `docs/architecture/IR_AND_RUNTIME.md` |
| 与 HA 的语义差异 | `docs/architecture/HA_SEMANTIC_DIFF.md` |
| AF-Spec 语法 | `docs/reference/AF-Spec语法参考.md` |
| API 契约 | `docs/reference/API_CONTRACT.md` |
| MCP 接入指南 | `docs/reference/MCP接入指南.md` |
| 测试通道设计 | `docs/reference/AF测试通道设计方案.md` |
| MiMo MCP 交互优化 | `docs/architecture/MiMo-MCP交互优化方案.md` |
| 治理面设计 | `docs/architecture/设计_v1.4.0_治理面.md` |
| 经验闭环设计 | `docs/architecture/设计_v1.5.0_经验闭环.md` |
| 用户端 UI 减法设计 | `docs/design/设计_用户WebUI_深度减法.md` |
| 联动执行计划 / 记录 | `docs/ADM联动执行计划-AF.md` / `docs/ADM联动执行记录-AF.md` |
| 审计索引 | `docs/audit/index.md` |
| 文档总索引 | `docs/文档总索引.md` |
| 蓝图 | `KICKOFF.md` |

---

## 十九、变更记录

| 日期 | 基准 | 动作 |
|---|---|---|
| 2026-10-09 | HEAD `e5b3fd5` | 全量重列四张清单（31 工具 / 90 路由 / 18 命令 / 40 检查项，均来自注册表现读）；订正节点·边·触发枚举；新增预演 `dry_run`、双轨仿真与五档证据、测试通道、用户视角 WebUI、登录正规化、部署 env 键名真源、HEAD 回归红态；删除明文 MCP 令牌；文档索引路径全部改为真实位置（旧索引 7 条里 6 条指向不存在的 `docs/` 顶层文件） |
| 2026-10-09 | 工作区混合态（未提交，含并发批次的鉴权改动） | 现场回灌五件：`POST /api/watch/start` 假绿已修（sidecar 身份必须等于本次 IR，失败分三档）+ 档位如实命名 `tier`/`real_device`；**HTTP/用户视角到今天没有常驻真机通道**（申请 `20261009-AF-用户视角到真机的常驻通道`）；`/api/automations` 卡片改按 automation 级取数、`trial` 读不出就给 `null`、启停写侧走 `store.resave_raw`；订正 `forge watch` 没有 `--live`/`--vhass` 两枚旗子；记入 NAS `AUTOFORGE_LIVE_ENABLED=1` 与仓内缺省 0 的分歧；记入 `requires_confirm` 无运行期消费者、`canary` 有；重钉 12 处行号锚点 |
| 2026-10-09 | 同上，保真复核 | 四项清单与注册表逐项对撞（31/90/18/40 全等）；§七 安全闸表 42 名收成 40 键，并写明 `IR_SCHEMA` 是错误知识分类、`L2_NEEDS_CANARY` 发诊断却未注册；§十二 时区键补 `HOMESDK_TZ`（规范）与全序 |
| 2026-10-09 | HEAD `e5b3fd5` + 收件箱批次 | 计划 §七 卡1 落地：`adapter: inbox` 三动作（`speak`/`notify`/`tv`）走 `af_mqtt_bridge.inbox_publish`，载荷 schema 直接读 `homesdk.presence` 函数签名、不在 AF 重抄；`dry_run` 零字节上线；桥缺席＝缺凭据 ⇒ `ADM_ERR_AUTH_REQUIRED`。§七 补 `classify_action` 的「动作名不带点退回适配器名」段落（这是本批由漂移测试抓出的真 bug：DSL 会把 `inbox.speak` partition 成 `adapter=inbox`+`action=speak`，只按动作名判会落进未知 domain 的 L2 缺省档，扫描阶段就红）；§八 补收件箱在预演档下的口径。同时修掉 publish `rc` 被丢弃（bug D）：`rc≠0` 现在计 `publish_errors`、置 degraded、重发 status |
| 2026-10-09 | HEAD `2d92bb1` + 联动入向批次 | 计划 §七 卡3 落地（AF 侧半边）：桥订 `ma/presence` + `ma/device-health`，入向事件落**独立持久队列** `af_linkage_feed.LinkageFeed`（`{store}/linkage_events/{kind}/{13位毫秒}-{event_id}.json`，每类各 500 封顶 + 24h TTL，都裁最旧）；触发**复用 `on event`**（`ma_presence`/`ma_device_health` 两个事件名，不立第六类 trigger）；收/消费分线程——paho 回调只判形状＋原子写，常驻 tick 线程经 `af_live.pump_linkage` 抽水位线注总线（`EventBus` 没有锁），"重启不丢记录、不补触发"由 `TRIGGER_MAX_AGE_S=120` 那档分开。`/api/health` 的 `linkage.inbound` 是这一路的唯一读数面（`subscribed`/`presence_in`/`device_health_in`/`rejected`/`feed`，未新增路由）。顺手关掉 HEAD 上就红的 4 条计数棘轮（有界缓存 125→129、路由 85→87、routes 88→90、mimo 20→22），并给 `_cooldown_pending` 补上带理由的就地豁免 |
| 2026-10-09 | HEAD `925f56e` + 平键批次 | 裁定 20261009 §四 甲/直译落地：§3.4 第三条口径改成"按设备读得进、按成员数组读不进"，`context` 新增 `trigger_entity_id`/`trigger_subject`/`trigger_kind` 三枚平键（名单真源 `af_instance.TRIGGER_FLAT_KEYS`，缺失格落空串而不是缺席），解析器 `make_resolver` 与 `_trigger_repr` 一字未动；判据两树 12+17 绿、相关七腿 983 passed、变异两枚分别杀 3 条与 7 条 |
| 2026-10-09 | HEAD `bb800bc` | 裁定 20261009 两份同日均已回：§四 甲那批（三枚触发平键）落码 `bb800bc` 并**由 DCD 登记进契约表 §1.6**，主标识口径裁甲A（DSL `entity_id` 优先 / 盘上 `LinkageRecord.subject` `device_id` 优先，两格并存）；§三 的 `requires_confirm` 按新判例「编译期看见 ≠ 运行期兑现」升格为**开常驻通道的硬前置**（`af_executor.py` 现读 0 命中 ⇒ Q1=C 未落地）；§一 的 AST 两枚 `ok` 裁"写端点派生 / 纯查询删键"、落码窗口在登录线之后 ⇒ 本轮仍不碰 `af_api.py`；§十六 残余第 6、11 条同步现状 |
| 2026-10-09 | HEAD `0300150` + 确认闸批次（未提交态） | 裁定 20261009 §三 硬前置落地：§八 那格从"`requires_confirm` **没有**运行期消费者"翻成"有"——`af_executor.py:648-659` 在 `_do` 入口判旗（未授权一次都不下发）、`:864-891` 挂成一次人工确认会话（复用 `pending_asks` → `/api/asks*` / sidecar / inbox，`AskSession` 不加字段、`af_api.py` 不动）、`:211-241` 唤醒（yes ⇒ `confirm_granted` 一次性把手 + 重进同一节点执行一次；no / `on_timeout` ⇒ fail-closed），放行与拒绝各落一枚具名审计（`af_audit.py:51-52` 新增 `confirm_granted`/`confirm_denied`，注册进 `ALL_EVENT_TYPES :75-76`，`docs/reference/API_CONTRACT.md` 的枚举同步补全到现读 21 枚（本批枚数；下一批加 `instance_session_lost` 后现读 22 枚））。canary 一格的锚点随本批重钉（`:646`/`:663-670`/`:671-680`/`:711`/`:243-331`）。口径差一处如实写出：dry 适配器不挂起、只留 `confirm_skipped_dry_run` 痕，与先前落码档「预演档不豁免」不同，已交 DCD 追认。§十六 加第 13 条残余（崩溃恢复不重挂挂起会话，`ask` 与本批确认共用这一格）——该格已由下一行「恢复重挂批次」收口，第 13 条随之从 §十六 删除。判据与逐条归属见执行记录 §二之八十一 |
| 2026-10-09 | HEAD `0300150` + 恢复重挂批次（未提交态，叠在确认闸批次之上） | §十六 残余第 13 条收口：崩溃恢复后 `ask` / 人工确认会话重挂回 `pending_asks`（`af_executor.py:507-565` 新增 `reseed_sessions`，`af_runtime.py:203` 在 `restore_persisted()` 末尾调用）。同批查出真凶：`pending_confirm` 原先写在 `instances.suspend()` **之后**，而落盘恰好发生在那次状态转换里 ⇒ 盘上记录永远缺这枚标记（`:881-884`、`:845-852` 现在都先写标记再挂起；`wait` 那格的 `_ask_rounds`/`_ask_room` 同口径）。新增具名审计 `instance_session_lost`（`af_audit.py:35-37`、注册 `:66`，枚举现读 **22 枚**，`API_CONTRACT.md` 同步）。判据 `tests/unit/test_restore_pending_sessions.py` 11 条；变异六枚分别杀 10 / 1 / 1 / 1 / 4 / 11 条红。确认闸那批的锚点因本批在执行器前部插入整体后移，两份架构文档 + 契约 + 执行记录按现读重钉（`:646`/`:648-659`/`:663-670`/`:671-680`/`:711`/`:243-331`/`:864-891`/`:211-241`/`:333-338`/`:51-52`/`:75-76`） |
| 2026-10-10 | HEAD `8efc134` + 目录收口批次（未提交态） | §七 读数从 40 项翻成 **41 项**：`L2_NEEDS_CANARY` 已登记进 `CHECKS`（`af_scanner.py:54`）与 `CODE_HINT`（`:107`），风险分级那一行补进该枚；那条"看着像检查项其实不在 40 里"的说明改成两条性质不同的注记（`L2_NEEDS_CANARY`＝本批收口，`IR_SCHEMA`＝错误知识分类键、不是扫描项）。扫描器锚点随本批 +3 重钉：诊断发出点 `:415`→`:419`、L2 策略表 `:399-419`→`:402-426`、变量传码 `:1093-1104`→`:1096-1107`、`LIVE_*` 常量 `:1233`→`:1236-1239`。新增通用判据 `tests/unit/test_diagnostic_code_catalog.py`（8 条腿）。§八"安全旗子"那格的 `requires_confirm` 叙述未动（上一批已翻） |
| 2026-10-10 | HEAD `086cf09` + 拒绝出口诊断批次（未提交态） | §七 读数从 41 项翻成 **42 项**：新增 `CONFIRM_WITHOUT_DENY_PATH`（裁定 20261009（第二份）§二 2.2，**WARNING 级、不拦发布**），族表挂在"中断与挂起"那一行；§八 那格补两句——Q1 追认已回（dry ⇒ 不挂起 + 留痕 `confirm_skipped_dry_run`，裁的就是"跳过 + 记痕"这半边）、Q2 裁甲之下的"被拒落 `done`"现在编译期也喊得出来。扫描器锚点随本批重钉：`__post_init__` 回退 `:171-173`→`:174-176`（+3）、`L2_NEEDS_CANARY` 发出点 `:419`→`:423`、L2 策略表 `:402-426`→`:406-430`（这两处 +4：三行目录 + 一行调用点）、`CODE_HINT` 那枚 `:107`→`:110`，变量传码 `:1096-1107`→`:1120-1131`、`LIVE_*` 常量 `:1236-1239`→`:1260-1263`、说明文档 §五 dry-live 组 `:1248/1275/1285-1286`→`:1272/1299/1309-1310`（本批方法体之后一律 +24）。新增判据 `tests/unit/test_confirm_exit_diagnostic.py`（11 条腿，含"三枚出口名＝执行器拒绝词汇"同源腿）；`test_diagnostic_code_catalog.py` 的目录枚数腿 41→42 |
| 2026-10-10 | HEAD `033866f` + 收件箱长度预拒批次（未提交态） | §七 读数从 42 项翻成 **43 项**：新增 `INBOX_PARAM_TOO_LONG`（**ERROR 级、拦发布**，收 §十八 残余 B.9 后半格），族表挂在"出站与覆盖"那一行；§十六 残余第 8 条随之收掉一半（只剩契约 §1.3 护栏 3 的按 source 限速，执行人与 N 值已交 DCD）。同一批把 `af_adapters/inbox.py` 文档串里手抄的 `text≤500`/`title≤80`/`body≤500` 删掉，改成"上限现读库侧 `INBOX_MAX_*`，本文件一个数字都不写"：终版直接 `from homesdk.presence import` 三枚常量、`INBOX_LEN_LIMITS`（`:57`）格子里放的就是常量本身，`inbox_overlong_fields()` 挪到 `:74`。扫描器锚点随本批重钉（两本目录各插行 + 新检查方法 ⇒ 方法体之后一律 **+27**）：`CHECKS` 声明 `:38`→`:39`、`__post_init__` 回退 `:174-176`→`:179-181`、L2 发出点 `:423`→`:429`、`CONFIRM_WITHOUT_DENY_PATH` 方法 `:503-521`→`:509-527`（字面量 `:520`、调用点 `:369`）、变量传码 `:1120-1131`→`:1147-1158`、`LIVE_*` 常量 `:1260-1263`→`:1287-1290`（用于 `:1313/:1321/:1329/:1347`）、dry-live 组 `:1272/1299/1309-1310`→`:1299/1326/1336-1337`。**形状返工过一次并如实记账**：初版把常量名存进表、运行期 `getattr` 现读，被 `check_mqtt_writers.py` D 判据判红（桥外按名派发机制层成员正是该门要拦的形状），而那条红同时说明"漂移要响亮"该走 import 期——库侧改名现在是 `ImportError` 而不是运行期 `AttributeError`。新增判据 `tests/unit/test_inbox_param_length_diagnostic.py`（30 条腿，含"表与库侧 `_len_bounded` 名单+现读值对撞""扫描器零整数字面量""本文件零 `getattr` 派发/零整模块 import"三条同源腿，全部走 AST 而非按名字 grep，理由见 §十六 第 10 条）；目录枚数腿 42→43 |
| 2026-10-10 | HEAD `bf8c563` + 测试区删除面批次（代码面已提交 `125c646`，未推） | §九 的 `af_test_clear` 那一格从「无守卫 `rmtree` + 待裁」翻成落地：删除前过两档形状守卫（盘根 / 等于或包住正式存储根，对照量＝调用方现递的 `store.root`），`protected_root` 是必填关键字，`ignore_errors=True` 撤掉、残留按盘面数出来、明细封顶 20 条，守卫拒判走工具语义（`ok:false` + 具名 `code`）。§十六 残余第 5 条同步收口；第 8 条的**护栏 3 半边按裁定 20261010 §四 收尾**（Q1=甲＝DB 消费侧按 `source` 计数，Q2=不设独立分钟级 N，由冷却＋每日预算承担 ⇒ AF 侧无可落码），§1.3 的「上限数字为摘录，真源＝`homesdk.presence.INBOX_MAX_*`」那一行由 DCD 同日落笔。判据新增 17 条（`tests/unit/test_test_area_delete_guard.py`，本批前 `af_test` 全模块 0 条判据）。门禁：`fake-ok-const` 79→**78**（`TestChannel.clear` 那格从常量 `ok=True` 改成派生），`.gates-baseline.txt` 随之删掉这枚**已不再命中**的指纹（87→86 行，库侧 `Report.is_red` 把 stale 也算红）；全量 99→**98** 对上限 97 仍红，差额由 +2 收成 +1，那 2 枚仍在登录线（`20261009-AF-AST两条未获批的ok字面量归属` 裁甲/删键，落码窗口在登录线之后）⇒ AF 不自上调上限 |
| 2026-10-10 | HEAD `3b00224` + 保真规范化批次（代码面已提交 `7800d5c`，未推） | §十六 残余第 4 条收口：`af_fidelity._canon` 从裸 `json.dumps` 换成带类型标记的递归编码，`FidelityReport` 新增 `not_comparable` 一档。修前两格都在 HEAD 上探针实测（`%TEMP%/probe_b2_head.out` 与工作区那份 `cmp` 逐字节相同）：抛穿四格（date/set 抛 `TypeError`、自引用抛 `ValueError: Circular reference detected`、混合键型抛 `TypeError: '<' not supported…`）＋塌陷三格（`("x","y")` 与 `["x","y"]` 同串 `[1, 2]`、`{1:1}` 与 `{"1":1}` 同串 `{"1": 1}`、`_canon(nan)='NaN'`，端到端 `fidelity_equal` 前两格返回 `True`）。§十五 加第 11 条坑（"没比成"不许由 `False` 或异常兼职）。新增判据 `tests/unit/test_fidelity_canon_vocabulary.py` 21 条腿（含三枚 AST 形状腿与两枚结构腿），副本树变异 8 枚：7 枚分别杀 12/2/1/1/1/3/8 条，另 1 枚（M5：`ok` 里去掉 `and not not_comparable`）**是等价变异、21 条全绿**——当前代码里该项被 `projection_fidelity=False` 蕴含，如实登记为不变式而不假称能杀腿。归属核对：纯 HEAD 副本树整切片 `3 failed, 3094 passed`，同树只叠本批两份文件 ⇒ `3 failed（同名）, 3115 passed`；工作区 10 枚红全在鉴权线（HEAD 的 auth + 本批 ⇒ 那 10 条单跑 10 绿；在途 auth + 本批 ⇒ 同 10 条 2 红），不由本批引起。门禁读数与本批前逐字相同（`新增/未获批 2 条`／`存量 96`／`过期 0`／`20|78` ⇒ 全量 98 对上限 97），未新增违规、未动上限 |
| 2026-10-10 | HEAD `a734a02` + 裁定 20261010（测试区删除面）回执批次（纯文档面） | §九 那条 ⚠️"还剩的政策半边待裁"翻成落地口径：三格全裁**甲**、AF 侧**零代码改动**——§二 维持形状守卫（不收成 `{root}/test` 前缀白名单；乙列候选但需 DB 给权威树的现读路径；丙要先给测试区落归属标记）⇒ **同级目录照删＝裁定的已知边界，不是遗漏**；§三 维持 scope `write` + 无面板（采用的依据与 AF 现读逐字一致：两棵第一方 UI 树对 `af_test` 0 命中、`/api/test/*` 路由 0 条）；§四 **判例入册**：不可逆操作的守卫，对照量必须由调用方现递，被守卫的模块不许自带第二份真值。§十六 残余第 5 条的尾句同步收尾；说明文档 §十 那格改成三条（射程边界／权限与面板／判例原文）、§十八 B.3 措辞跟着收。**同源自查查出一格新残余并登记成 §十六 第 13 条**（`af_store.py` 的 overwrite 备份 `__ovbak__` 静默回收：5 枚 `ignore_errors=True`、残留对 `history()`/`names()` 隐形、判据 0 条点名），本批只登记不修 |
| 2026-10-10 | HEAD `d6802bc` + §十六 残余第 13 条收口批次（代码面已提交 `3ace9b6`，未推） | 备份回收的静默面落成三枚读数面：5 枚 `ignore_errors=True` 全撤、删除收成一个入口 `_purge_tree`（`af_store.py:73-95`，残留按盘上现数）；报告新格 `residual_backups`（`:806`/`:867-869`，不进 `errors`、不动 `ok`）＋ CLI `af_cli.py:956-959` echo ＋ WARNING `:732`/`:749`；让位前清不掉旧备份改抛 `BackupNotReclaimed`（`:58`，抛出点 `:673`），替掉那柄伪装成撞车的 `FileExistsError [WinError 183]`。判据 11 条腿（`tests/unit/test_overwrite_backup_reclaim.py`，失败注入走 rmtree 内部的 `os.unlink`/`os.rmdir`，日志面带哨兵自证）＋变异 9 枚全杀。上一批两处措辞就地更正（清理触发点只有一枚、不是"没有"；备份目录名固定故"越攒越多"不成立）。剩 HTTP 那半边仍 500，接 409 要动在途 `af_api.py` ⇒ 随 #79。门禁两遍逐字节相同（`GATES_RC=1`、新增未获批 2 条、基线 96、7877 字节） |
| 2026-09-24 | 当时 HEAD | 初版（端到端实测后） |
