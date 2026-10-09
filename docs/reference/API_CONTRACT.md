# AutoForge 服务层 API 契约（Round 1 只读）

> 版本：contract v1.0　日期：2026-09-14
> **唯一权威形态**：运行中的 FastAPI 自带 `GET /openapi.json`（OpenAPI 3）与 `GET /docs`（Swagger UI）。
> 本文是速查表；前端联调请以 `/openapi.json` 为准。

Base：`http://<host>:8787`（NAS 部署：`http://192.168.2.200:8787`）

**Round 1 只读红线**：不连真实 Home Assistant、不写设备、不需要令牌。所有端点均为读操作（`/sim` 在本地 FakeHA 内运行，不触真机）。

## 端点

| # | 方法 | 路径 | 说明 |
|---|------|------|------|
| 1 | GET | `/api/health` | 健康与里程碑、契约版本 |
| 2 | GET | `/api/graphs` | 归档列表：`items[{name, latest_version, saved_at, note, mode, automation_ids}]` |
| 3 | GET | `/api/graphs/{name}?version=` | 某版本 Graph：`ir` + 确定性 `nl` + 扫描诊断 |
| 4 | POST | `/api/build` | 静态扫描（第一道闸）：`{ir, known_entities?}` |
| 5 | POST | `/api/sim` | 仿真回放：`{ir, seed?, events?}` |
| 6 | GET | `/api/conf/{name}` | 置信度分级（G4）+ 阈值；`name` 可为 `_all`/`all` 聚合全部归档 |
| 7 | POST | `/api/conf/{name}/intervene` | 模拟人工干预（负样本回灌并落盘）：`{automation_id}`；`_all` 会自动定位归档 |
| 8 | GET | `/api/diff?name=&old=&new=` | 版本 diff（G6）：`render` + `structured` + `notes`（两版本的**归档备注**；`note` 属归档元数据、不参与图内容比对）；`old/new` 为 int |
| 9 | GET | `/api/spec/{name}?version=` | IR → AF-Spec（G7） |
| 10 | POST | `/api/spec/compile` | AF-Spec → IR：`{text}` |
| 11 | GET | `/api/faults` | 故障注入图鉴（G5 静态元数据） |

### 响应要点（前端按此渲染）

- **无信封**：所有响应为**扁平**对象（**没有** `{"data": ...}` 外壳）。前端若用了 envelope 需自行剥离/包装。
- `/api/graphs` 的 `mode` 为最新版本的 automation mode；`automation_ids` 用于把**归档名**映射到**自动化 id**（置信度接口以 `automation_id` 为键）。多自动化归档时 `mode="multi"`。
- `/api/diff` 的 `structured` 为**对象数组**：
  - `added_nodes/removed_nodes`: `[{node_id, address, automation_id, node}]`
  - `changed_nodes`: `[{node_id, address, changes[]}]`
  - `added_edges/removed_edges`: `[{automation_id, from, to, kind, edge}]`
  - `meta_changes`: `[{key, automation_id, field, old_value, new_value}]`（`key` = `"{automation_id}.{field}"`）
- `/api/faults` 的 `failures[].recoverable` 为**布尔**（`true`/`false`），可直接用于 `v-if`。
- `instances[].trace[]` 元素为 `{node, note, state, at}`（前端可只用 `node`/`at`）。

## 关键枚举

- `diagnostics[].level`：`error` | `warning`；`diagnostics[].code` 见 `af_scanner.CHECKS`（如 `L3_ACTION` / `SHADOW_WRITES_DEVICE` / `LOW_CONF_WRITES_DEVICE` / `ENTITY_NOT_FOUND`）
- `band`：`auto`（conf ≥ 0.85）| `shadow`（≥ 0.60）| `ask`（< 0.60）
- `instances[].state`：`created|active|suspended|done|cancelled|failed|expired`
- `audit[].type`（真源 `af_audit.ALL_EVENT_TYPES`，现读 22 枚）：`entity_drift|action_failed|event_emitted|breaker_open|breaker_recover|quota_exceeded|instance_rejected|instance_expired|instance_debounced|instance_restored|instance_restore_dropped|instance_session_lost|write_conflict|instance_lease_held|handler_failed|premiere_issued|premiere_consumed|premiere_trial_started|premiere_trial_paused|trial_assert_failed|confirm_granted|confirm_denied`
  - `confirm_granted` / `confirm_denied` 是裁定 20261009 §三 硬前置那批新增的：`requires_confirm` 节点的放行与拒绝各一条，拒绝侧不许只落在会被截断的实例 trace 里

## 启动方式

```bash
# 本地
pip install -e ".[api]"
forge serve --host 0.0.0.0 --port 8787 --store-root .forge --examples examples/ir

# NAS（容器）
docker compose -f docker/docker-compose.api.yml up -d --build
```

## Round 2-A：会话（ask 审批的"人机回路"）

`/api/sim` 是一次性回放；`ask` 需要"**挂起 → 人工应答 → 继续**"的跨请求状态，
因此引入**进程内会话**（内存、单进程；TTL 默认 3600s，可用 `AUTOFORGE_SESSION_TTL_S` 覆盖；服务重启即丢失）。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/sessions` | 建会话：`{ir, seed?, events?}` → 运行到挂起/终态，返回含 `asks[]` 的状态 |
| GET | `/api/sessions` | 列出活跃会话：`{session_ids[], ttl_s}` |
| GET | `/api/sessions/{sid}` | 当前状态 |
| POST | `/api/sessions/{sid}/answer` | 应答：`{text, ask_id?}` 或 `{text, room?}` → 唤醒并继续执行 |
| POST | `/api/sessions/{sid}/tick` | 推进虚拟时钟：`{advance_s}`（用于演示 `on_timeout`） |
| POST | `/api/sessions/{sid}/cancel` | 取消所有未终态实例：`{reason?}` |
| DELETE | `/api/sessions/{sid}` | 删除会话 |

**统一响应**：`{ok, session_id, mode, instances[], audit[], bus, final_states, nl, asks[]}`

`asks[]` 元素：`{ask_id, instance_id, node_id, room, prompt, automation_id}`（`ask_id` = 挂起实例 id，同一实例同一时刻只有一个 ask）。

## Round 2-B：真机下发（三重闸）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/live/status` | 可用性：`{enabled, ha_url, has_token, allowlist_required, confirm_required, reasons[]}` |
| POST | `/api/live/run` | 真机下发：`{ir, live_allow[], confirm, events?}` |

**三重闸（缺一即拒）**：

1. 服务端开关 `AUTOFORGE_LIVE_ENABLED=1`（**默认关闭**）→ 否则 **403**
2. 服务端令牌 `AUTOFORGE_HA_TOKEN`（**绝不从请求体接收令牌**）→ 否则 **403**
3. 请求体 `confirm=true` + 非空 `live_allow`，且 IR 的写目标 ⊆ 白名单 → 否则 **403/400**

**如何启用（NAS）**：在仓库根目录 `/vol1/1000/docker/autoforge/.env` 写入
`AUTOFORGE_LIVE_ENABLED=1` 与 `AUTOFORGE_HA_TOKEN=<令牌>`（compose 已配 `env_file: ../.env`，`required: false`），
然后 `docker compose -f docker/docker-compose.api.yml up -d`。`.env` 已在 `.gitignore` 中。

响应与 `/api/sim` 同构，额外含：`{mode: "live", live: true, ha_url, allowlist[], writes[]}`（便于前端复用渲染）。
服务端会打 `LIVE RUN` 告警日志（可观测）。

> ⚠️ 安全提示：服务层当前**无鉴权**（原型期，仅局域网）。若暴露到 tailnet/公网，必须先加认证——真机下发尤其。

> 契约变更须同步：本文件、`af_service.py`、`af_api.py`、`tests/unit/test_af_api.py`、以及前端 Mock。
