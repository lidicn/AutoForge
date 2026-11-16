# 开发计划：用户 WebUI 与测试通道控制台

> 配套路线图：`docs/ROADMAP.md` §用户 WebUI 与测试通道（2026-09-24 补充）。
> 缩写：**AFU** = 用户 WebUI 前端（新豆包对话）｜**AFD** = 后端工单承接（含测试通道 UI）。

---

## 0. 两份 UI 的定位（铁律）

| | 开发者面板 `ui/`（admin） | 用户 WebUI `ui-user/`（家庭用户） |
|---|---|---|
| 内容 | IR/SpecEditor/治理/扫描/日志/测试通道控制台 | 配对 / 自动化(按 agent 分组) / 待确认·试演期 / 设置 |
| 技术栈 | Vue3+Vite6+TS+naive-ui（现有） | 同栈 + vite-plugin-pwa（新建，见 `doc/设计_用户WebUI_深度减法.md`） |
| 部署 | NAS `/vol1/1000/docker/autoforge/ui/dist` | NAS `/vol1/1000/docker/autoforge/ui-user/dist`（独立） |

---

## 工作流 A · 用户 WebUI（AFU 承接，新对话）

设计稿已落盘：`doc/设计_用户WebUI_深度减法.md`（含 §8 新对话前置环境启动包）。
- AFU 只在 `ui-user/` 工作，不碰 `src/autoforge` 后端与 `ui/` 开发面板。
- 按四大区块实现；先用 `VITE_USE_MOCK=true` 桩数据跑通，后端端点（`设计_用户WebUI_深度减法.md` §5）由 AFD 另开对话补。
- 交付：handoff card + 设计稿同步。

---

## 工作流 B · 开发者 WebUI 恢复 + 测试通道控制台（AFD 承接）

### B.1 恢复开发者 WebUI（优先级最高）
- 现象：`ui/` 访问不了。本地 `npm run build` 已验证源码可编译（2742 模块 / 28s / 无错）→ 断点在 NAS 服务/挂载态。
- 恢复动作：
  1. `cd e:/NAS/AutoForge/ui && npm run build`（已执行，产出 `ui/dist`）。
  2. scp `ui/dist/` → NAS `/vol1/1000/docker/autoforge/ui/dist/`。
  3. 确认 `autoforge-api` 容器以 `--ui-dir /ui` 托管且处于 up（`docker compose -f docker/docker-compose.api.yml restart`）。
- ⚠️ 风险：`e:/NAS` 为断开副本 + 本地有未提交 WIP；scp 会把 WIP 推上 NAS（正是 ROADMAP §当前最大风险 #1）。**正式修复须在 v1.10.0 ① 先把 WIP 提交到 git，再从 commit 重建镜像**；本步仅为战术恢复可访问性。

### B.2 测试通道控制台（做进 `ui/`）
后端 `af_test` 已具备（MCP 三工具 + `/data/test/` 隔离）。开发者面板需补 REST 入口与 UI：

| 后端（AFD 补） | UI（AFD 补，做进 `ui/`） |
|---|---|
| `POST /api/test/submit`（`intents`, `batch_id`）→ `TestChannel.submit_batch` | 测试通道页：批量提交（粘贴 intent JSON / 填批次号） |
| `GET /api/test/reports` → `list_reports` | 报告列表（批次/总数/PASS/FAIL/通过率/时间） |
| `GET /api/test/report/{batch_id}` → `get_report` | 报告详情（失败原因分类 + 每题明细） |
| `POST /api/test/clear` → `clear` | 一键清空测试区 |
| （可选）`GET /api/test/status` → 测试区是否存在/条数 | 开关/状态条 |

- 鉴权：复用 `AUTOFORGE_API_TOKEN` write scope（与 MCP 同口径）。
- 铁律：测试通道控制台**只消费测试区**，绝不串到正式 `/data/`。
- 验收：`ui/` 内「测试通道」页可提交 200 条 → 看报告 → 清空；全程不碰真机、不动正式区。

### B.3 稳定性前置（不可绕过）
- 任何上生产前先完成 `v1.10.0` ①（WIP 提交）+ ②（P0-9 fail-closed）+ ⑤（镜像基于 commit 重建，弃 scp 挂卷）。否则测试通道控制台等新功能会再次漂在 WIP 上不可回滚。

---

## 工作流 C · 用户 WebUI 后端跟进（ADF）— 回应咨询单

后端核对结论：`doc/咨询_用户WebUI后端确认_20260924.md` 的条目绝大多数**待新增**。契约以 `doc/设计_用户WebUI_深度减法.md` §5（已对齐实际后端）为准。

### C.1 配对（MCP 连接认证）
- 新增 `af_pair(code, agent_name?)` MCP 方法：校验配对码（单次/短时）→ 运行时签发 Bearer 令牌（scopes 由配置定），写入 `agent_name` 作 subject。扩展 `af_auth.TokenRegistry` 支持运行时增删令牌（参考现有 `revoke` 落盘模式）。
- 新增配对码存储（6 位、单次性、短时效，落盘参考 `.auth/revoked.json`）。
- 新增 SSE `GET /api/mcp/pair-request`：agent 请求 MCP 连接时后端自动生成码并推送 `{agent_name_hint, code, expires_at}` 到前端。**前端无生成按钮**。

### C.2 授权码（部署授权，与配对是两回事）
- `POST /api/user/auth-code` `{ttl:'short'|'long', duration_minutes?}` → `{code, expires_at}`（长期码 6 位、可撤销、`expires_at=null`）。
- `GET /api/user/auth-codes` / `DELETE /api/user/auth-code/{code}`。
- 改部署入口（af_save/af_apply）：agent 携带授权码 → 有效走路径 A 直部署；无效/缺失走路径 B 入 pending。

### C.3 自动化列表/详情
- `GET /api/automations?group_by=agent`（无归属归入"未归属/本地"）。
- `GET /api/automations/{id}`：`preview_nl`（入库异步算+缓存，`/preview` 现算）、`devices[]`、`last_triggered`/`trigger_7d`（借 `af_fire_recorder`）、`trial{state,since,anomaly}`（聚合 conf band + 异常）。
- enable/disable/delete/archive 端点。

### C.4 多用户（决策：v1 单 owner）
- v1 **不建用户系统**；agent 与自动化归属单 owner。"跳转开发面板"对 owner 恒显。多用户推迟 v2.0。

### C.5 已具备、无需新建
- Pending 驳回 `reason` 已支持（`POST /api/pending/reject {op_id, reason}`）；v1 前端轮询 `POST /api/pending/list`，SSE 推送留 P1。
- MCP 为 HTTP `POST /mcp`（Bearer 头）或 stdio；前端配对页显示静态 URL + SSE 推送码。

### C.6 优先级
- **P0**：B1 授权码 + B2 配对 + B3 自动化列表/详情 + B4 预演 NL。
- **P1**：B5 试演期聚合、B6 Pending SSE、B7 触发历史。
- **P2 / v2.0**：多用户。

### C.7 实施状态（v1.9.0 已交付）

| 项 | 状态 | 落点 |
|---|---|---|
| B1 授权码 | ✅ 已交付 | `af_auth.AuthCodeStore`（落盘 `.auth/auth_codes.json`）+ `af_api` 的 `/api/user/auth-code`（POST/GET/DELETE） |
| B2 配对 | ✅ 已交付 | `af_mcp.af_request_pair` / `af_pair` + `af_auth.PairCodeStore` + SSE `GET /api/mcp/pair-request` |
| B3 自动化列表/详情 | ✅ 已交付 | `af_api` 的 `/api/automations`（group_by=agent）与 `/api/automations/{id}` + enable/disable/archive/unarchive/delete |
| B4 预演 NL | ⚠️ 读图 | 卡片 `preview_nl` 取自 IR `nl`；`/preview` 现算接入留后续（见 `doc/设计_用户WebUI_深度减法.md` §5） |
| B5 试演期聚合 | 🟡 P1 部分 | 端点返回 `trial{state:"auto",since:null,anomaly:false}` 诚实默认；接运行时 persist_dir 聚合（canary/shadow/冲突仲裁）留后续 |
| B6 Pending SSE | 🟡 P1 部分 | 待批已通过 `POST /api/pending/list` 轮询接入前端；SSE 推送留后续 |
| B7 触发历史 | 🟡 P1 部分 | 端点返回 `last_triggered=null` / `trigger_7d=0` 默认；接 `af_fire_recorder`（persist_dir）聚合留后续 |

> 注：`E:\NAS` 为断开副本，后端改动需 scp 到 `/vol1/1000/docker/autoforge/src` + `docker restart autoforge-api` 方对运行容器生效（AFD 交接单 5.2 坑：改动须落在被 import 的树）。
> 前端 `ui-user/` 已 `npm run build` 通过（`dist/` 产出），部署到 `/vol1/1000/docker/autoforge/ui-user/dist`（独立端口/路径）。

---

## 交付与验收
- **AFU**：用户 WebUI 四大区块在 mock 下可演示（PWA 可安装、电脑/手机/平板三端自适应）。
- **AFD**：开发者 WebUI 恢复可访问 + 测试通道控制台可用 + WIP 进入 v1.10.0 提交基线。
