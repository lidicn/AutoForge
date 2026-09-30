# 交接单 · 用户 WebUI（ForgeSight）端到端 v1.9.0

> 范围：用户 WebUI 端到端（A 前端 + C 后端 B1-B7）。**不含** ADM 09-30 投产稳定化、**不含** 开发面板恢复（工作流 B）。
> 多用户决策：v1 **单 owner、无登录页、无用户表**（已回退前端定稿稿 §6/§7/§9.1）。

## 一、文件改动

### 后端（src/autoforge/）
- `af_auth.py`
  - 新增 `PairCodeStore` / `AuthCodeStore`（落盘 `.auth/pair_codes.json` / `.auth/auth_codes.json`，与 `revoked.json` 同目录同模式；read 时重新加载以支持 MCP→API 跨进程同步）。
  - `TokenRegistry` 扩展 `issue_for_agent(agent_name, scopes)`（运行时签发 Bearer 令牌并落盘 `.auth/issued_tokens.json`）、`revoke_by_subject`、`rename_subject`；构造器新增 `issued_path`。
- `af_mcp.py`
  - 新增 MCP 工具 `af_request_pair`（生成配对码）与 `af_pair`（兑换令牌）；注册进 `TOOLS`（scope=write）。
  - `serve_mcp` 注入 `_MCP_REGISTRY` / `_MCP_PAIR_STORE` / `_MCP_AUTH_STORE`。
- `af_api.py`
  - 新增端点：`GET /api/mcp/pair-request`（SSE，支持 `?token=` 因 EventSource 不能带头）、`POST/GET/DELETE /api/user/auth-code`、`GET/DELETE/PATCH /api/user/agents`、`GET /api/automations`（group_by=agent）、`GET /api/automations/{id}`、enable/disable/archive/unarchive/`DELETE /api/automations/{id}`。
  - `build_app` 传入 `issued_path`，并创建 `pair_store` / `auth_store` 实例。
- `af_service.py`（仅 `_t_save`）
  - `af_save` 增加授权码路径 A/B：持有效授权码 → `submit_pending` 后自动 `approve_pending`（路径 A 直部署）；无效/缺失 → 入待批（路径 B）。

### 前端（ui-user/，新建工程）
- Vue3 + Vite5 + TS + Pinia + vue-router + Tailwind + PWA（vite-plugin-pwa）。
- `src/api/client.ts`（真端点，Bearer 取自 `localStorage`）、`src/api/mock.ts`（`VITE_USE_MOCK` 桩数据）、`src/api/index.ts`（切换）。
- `src/stores/{agents,automations,authCodes}.ts`。
- `src/views/{AgentView,AutomationView,AuthCodeView}.vue`、`src/App.vue`（响应式导航 + SSE 配对弹窗）、`src/components/{SettingsDrawer,PairDialog}.vue`。
- 配置：`package.json` / `vite.config.ts` / `tailwind.config.js` / `tsconfig.json` / `.env`（dev mock）/ `.env.production`（真端点）/ `public/icon.svg`。

### 文档
- `doc/设计_ForgeSight用户端UI_前端定稿_20260924.md`：§6/§7/§9.1 回退单 owner。
- `docs/开发计划_用户WebUI与测试通道.md`：§C.7 标注 B1-B7 实施状态。

## 二、行为增量
- **配对**：agent 调 `af_request_pair` → 后端生成 6 位单次短时效码 → SSE 推前端弹窗（仅本人可见）→ 用户口述码给 agent → `af_pair` 兑换运行时 Bearer 令牌（subject=agent_name）。前端无生成按钮。
- **授权码**：长期码（可撤销、expires_at=null）+ 短期码（5–30 分钟）；`af_save` 持有效码直部署，否则入待批。
- **自动化**：按 agent 分组的卡片列表（启用/归档子标签），含预览一句话、设备、近 7 天触发、试演期状态（异常变红脉冲）、待批项内联批准/驳回；支持启停/归档/删除。
- **设置抽屉**：给 agent 改名、跳转开发面板（恒显）、长期授权码管理。
- **单 owner**：启动直进 Agent Tab，无登录页；令牌 `localStorage` 持久化。

## 三、验证方式
- 后端：`py_compile` 通过；`af_api`/`af_mcp`/`af_auth` 无 lint 错误。
- 前端：`cd ui-user && npm install && npm run build` 通过（1590 模块转换，产出 `dist/` + PWA `sw.js`/`manifest.webmanifest`）。`npm run dev` 默认 `VITE_USE_MOCK=true` 可离线浏览四区块。
- 真机联调（需部署 NAS）：浏览器开 `ui-user/dist` → 触发配对 SSE、生成授权码、查看自动化分组。

## 四、已知风险
- `E:\NAS` 为断开副本：后端改动必须 scp 到 `/vol1/1000/docker/autoforge/src` + `docker restart autoforge-api` 才对运行容器生效；前端 scp 到 `/vol1/1000/docker/autoforge/ui-user/dist`（独立端口）。
- src/autoforge 当前 103 文件零提交 WIP，本次改动叠加其上（投产稳定化不在本范围）。
- B5/B6/B7（试演期聚合 / Pending SSE / 触发历史）为 P1：端点已返回**诚实默认值**（trial=auto、last_triggered=null、trigger_7d=0），接运行时 persist_dir 精确聚合留后续。
- 安全债（P0-9 fail-closed 等）属投产线，不在本范围；联调 NAS 时留意鉴权口径。

## 五、合并影响
- 新增能力不改动既有 `pending` / `TokenRegistry.revoke` / MCP `/mcp` 行为；`TokenRegistry.__init__` 仅新增可选 `issued_path` 形参（向后兼容）。
- 前端与开发面板 `ui/` 独立工程、独立端口，互不干扰。
- 部署后建议回执：① 配对全链路（af_request_pair→SSE→af_pair→签发令牌）；② 授权码路径 A 直部署 / 路径 B 入待批；③ 自动化分组列表与启停/归档；④ 单 owner 无登录页、令牌持久化。
