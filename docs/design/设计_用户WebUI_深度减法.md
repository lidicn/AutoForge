# 用户 WebUI 设计稿（Deep reduction · 深度减法）

> 状态：设计稿 v0.1 ｜ 日期：2026-09-24 ｜ 作者：产品决策（用户）+ 设计落盘（AF-agent）
> 配套：本文件同时是「新豆包对话」做前端活的**启动包**（见 §8 前置环境）。

---

## 0. 来源核对（mimo 产出 vs 本设计）

在 `E:\NAS\Lever-Hub\out` 核对了 mimo 关于 AF 的全部产出：

| 文件 | 主题 | 层级 |
|---|---|---|
| `48d313…md` | AutoForge-冲突仲裁器 | 引擎 |
| `1b924d…md` | AutoForge-场景模式 | 引擎 |
| `8b00c8…/bc51cc…/a7e603…md` | MiMo 高仿真增强 / 分级引擎 / 行为洞察重构 / Agent 编排层 / 运行时健壮性 | 引擎 |

**结论**：mimo 产出全是引擎层（conf 分级、冲突仲裁、场景、高仿真），**没有任何用户 WebUI 设计**。
→ 用户 WebUI 是纯本项目的产品决策，不属于 mimo 蓝图。后续 WebUI 的设计意图以本文件为唯一权威，不引用 mimo 输出。

---

## 1. 总体架构：两份 UI 彻底分离

| | 开发面板（现有 `ui/`） | 用户 WebUI（新建 `ui-user/`） |
|---|---|---|
| 定位 | 内部 / admin，含 IR、Graph、SpecEditor、治理、冲突审计、scanner、日志 | 面向家庭用户，只暴露用户该碰的 |
| 技术栈 | Vue3 + Vite6 + TS + pinia + vue-router + naive-ui | **同栈**（见 §6），新增 PWA |
| 部署 | NAS `/vol1/1000/docker/autoforge/ui/dist` | NAS `/vol1/1000/docker/autoforge/ui-user/dist`（独立路径/端口，不与开发面板混） |
| 现状 | 当前「挂了访问不了」→ 定义为开发面板，可访问性另修，**不在本设计范围** | 本设计新建 |

**铁律**：用户 WebUI 不得复用开发面板的视图组件、不得反向耦合后端 `src/autoforge` 的内部（只走 §5 契约端点）。

---

## 2. 设计理念：Deep reduction

- **留**：配对、自动化（按 agent 分组）、待确认/试演期、设置。
- **砍（全部留在开发面板，用户 WebUI 不出现）**：IR/Graph 原始查看、SpecEditor、治理/待批管理后台、冲突审计明细、scanner、日志、凭据/令牌原始编辑（用户侧只暴露「设置」里的极简入口）。
- 每个区块只展示**结论性信息**（如预演效果用中文一句话），不展示中间过程（不展示 IR、节点、表达式）。

---

## 3. 四大区块详细设计

### 3.1 配对（Pairing）
- **生成授权码**：按钮「生成配对码」→ 弹窗选 `短期`（默认 5 分钟、单次性）或 `长期`（用户主动选，可撤销）；
  返回：`授权码` + `MCP URL` + `一键复制`（码+URL 合并成一段可粘贴文本给 agent）。
- **已连 agent 列表**：每项显示 `agent 名称` / `通过授权码连入时间` / `授权类型(短/长)` / `最近活跃` / `[删除]`。
  - `[删除]` = 吊销该 agent 的 MCP token + 从列表移除（撤销即时生效）。
- **agent 改名**：在列表项内联编辑（见 §3.4）。

> 授权码语义：用户生成 → 复制给 agent（聊天里）→ agent 调 MCP `af_pair(code)` 兑换为该用户作用域的 MCP token → 落库连接记录。码**单次性 + 短时效 + 绑定用户**。

### 3.2 自动化（Automations）— 按 agent 名称分组
- 列表**按 agent 名称分组成区**（每个已连 agent 一个分组；无 agent 归属的归入「未归属/本地」）。
- 每张自动化卡显示：
  - 名称 + 来源 agent（分组头已体现，卡片内可省）
  - **预演效果描述**（中文一句话，来自后端仿真 `af_vhass` + `af_nl` 渲染；标注「仿真估算，仅供参考」若保真度不足）
  - 涉及设备：`friendly_name` / `entity_id` 标签（可点开看明细）
  - **[何时触发过]**：最近触发时间 + 近 7 天次数（需触发历史，见 §5 `last_triggered`）
  - **[禁用]**：开关（`.forge` 加 `disabled` 标记，apply 时跳过）
  - **[删除]**：硬删
  - **[归档]**：软藏（保留可恢复，不占活跃视线）
- 分组可折叠；禁用/归档项默认收起或置底。

### 3.3 待确认 / 试演期（Pending）← 用户的「最后一道手」
- **入口**：agent 提交的自动化先入此区（后端已有 `/api/pending/*` + `PendingItem` 类型，见 `ui/src/types/api.ts:366`，**后端非白纸**）。
- 每条显示：`摘要` / `来源 agent` / `提交时间` / `影响面(blast_radius)` / `[批准]` `[驳回]`。
  - `[批准]` → 自动化生效，转入 §3.2 并进入**试演期**。
  - `[驳回]` → 需填理由，移除。
- **试演期状态**（部署后展示）：卡片显示该自动化当前处于 `auto / shadow / canary`（对应 conf 分级引擎的 bands）及「异常自动暂停」事件（来自冲突仲裁器/断路器）。
  - 试演期内若后端检测到异常（冲突、断路器打开、连续失败），状态变红并自动暂停，用户可在本区看到原因并决定保留/回退。

### 3.4 设置（Settings）
- 账户（登录态、登出）。
- **跳转开发面板**（链接，仅内部/高级用户可见）。
- **给 agent 改名**（与 §3.1 列表内联改名同源，落 `PATCH /api/user/agents/{id}`）。
- （可选）长期授权码管理（查看/撤销）。

---

## 4. 部署流程（两条路径，用户选择）

```
路径 A（有授权码，立即生效）：
  agent 部署阶段 → 向用户要一次性授权码
  用户在 WebUI 生成（短期 or 长期，用户选）→ 复制给 agent
  agent 拿码 → MCP af_pair(code) 兑换 token → 直接部署到 AF
  → 自动化立即执行（无需用户再到 WebUI 批准）

路径 B（无授权码，需用户批准）：
  agent 无码 → 提交到 Pending 区
  用户到 WebUI「待确认」→ [批准] 生效 / [驳回]
```

- 两条路径**并存**：有码走 A（快），无码走 B（人工把关）。
- 路径 A 部署的自动化同样进入试演期（§3.3 状态展示）。

---

## 5. 后端 API 契约（UI 需要的端点）

> 约定：`现有`=后端已有或 dev panel 类型已定义；`待新增`=需 AFD 另开对话补。
> UI 通过 `src/api/client.ts` 调这些端点；先用 mock adapter（§7），后端就绪切真。

| 端点 | 方法 | 说明 | 状态 |
|---|---|---|---|
| `GET /api/mcp/pair-request` | SSE | agent 请求 MCP 连接时后端**自动生成** 6 位配对码并推送 `{agent_name_hint, code, expires_at}` 到前端（**无按钮**，ForgeSight 只展示） | 待新增（SSE 基建 + 配对码存储） |
| `af_pair(code, agent_name?)` | MCP | agent 拿码兑换 Bearer 令牌（scopes 由配置定）；写入 `agent_name` 作令牌 subject | 待新增（运行时发令牌，扩展 `af_auth`） |
| `/api/user/agents` | GET | 已连 agent 列表 `{agent_id, name, connected_at, last_seen}`（源自令牌注册表） | 待新增 |
| `/api/user/agents/{id}` | DELETE | 吊销连接（删令牌+移除） | 待新增 |
| `/api/user/agents/{id}` | PATCH | 改名 `{name}`（改令牌 subject） | 待新增 |
| `POST /api/user/auth-code` | POST | 生成**部署授权码** `{ttl:'short'\|'long', duration_minutes?}` → `{code, expires_at}`（长期码 `expires_at=null`，6 位可撤销） | 待新增 |
| `GET /api/user/auth-codes` | GET | 列出当前 owner 已发授权码（含已用/未用） | 待新增 |
| `DELETE /api/user/auth-code/{code}` | DELETE | 撤销长期授权码 | 待新增 |
| 部署入口（af_save/af_apply 链路） | — | agent 部署携带授权码：有效→路径A 直部署；无效/缺失→路径B 入 pending | 待新增 |
| `/api/automations?group_by=agent` | GET | 按 agent 分组的自动化列表（无归属归入"未归属/本地"） | 待新增（列表逻辑） |
| `/api/automations/{id}` | GET | 卡片详情 `{name, agent, preview_nl, devices[], last_triggered, trigger_7d, enabled, archived, trial{state,since,anomaly}}` | 待新增 |
| `/api/automations/{id}/preview` | GET | 仿真预演 NL（驱动 `af_vhass`+`af_nl`）；`preview_nl` 未就绪返回 `null`，前端可点此现算 | 待新增 |
| `/api/automations/{id}/enable`·`/disable` | POST | 禁用/启用 | 待新增（`.forge` disabled 标记） |
| `/api/automations/{id}` | DELETE | 删除 | 待新增 |
| `/api/automations/{id}/archive` | POST | 归档（软藏） | 待新增 |
| `POST /api/pending/list` | POST `{agent?}` | 待批队列（**现有** `PendingListBody`/`PendingItem`） | 现有 |
| `POST /api/pending/approve` | POST `{op_id}` | 批准 → 生效+试演期 | 现有 |
| `POST /api/pending/reject` | POST `{op_id, reason}` | 驳回（`reason` 入库） | 现有 |
| `/api/user/settings` | GET/POST | 账户/设置（v1 单 owner，无登录态） | 待新增（轻量） |

**MCP URL 格式（纠正原假设）**：MCP 为 HTTP `POST /mcp`（Bearer 令牌在 `Authorization` 头，**不是** `?token=` query 参数），另有 stdio `forge mcp`。前端"配对"页只显示**静态 MCP URL**（如 `http://192.168.2.200:8787/mcp`，写进开发面板/配置）+ 后端 SSE 推送的 6 位配对码（口述给 agent）；**不要拼"码+URL"**。agent 拿码调 `af_pair(code)` 换 Bearer 令牌，令牌不入 URL。

**多用户（v1 关键决策）**：后端当前**无用户体系**——`af_auth` 是"多令牌主体模型"（token→`subject` 字符串标签，仅审计/限速用），没有用户表、登录、session/JWT、role 字段。v1 用户 WebUI 定为**单 owner（一户）**：不建登录/用户表，agent 与自动化归属单 owner；"跳转开发面板"对 owner 恒显（或简单 owner=admin 标记）。多用户（按用户隔离、role、登录）推迟到 v2.0（路线图已列范围外）。前端按"单 owner、无登录页"实现；设置页只做 agent 改名 + 开发面板跳转。

**SSE 基建**：后端现无任何面向用户的 SSE 推送（现有 SSE 全是 HA 事件流/仿真）。配对码推送需新建 SSE 端点；Pending 通知 v1 先用轮询 `POST /api/pending/list`，SSE 推送留作增强。

**预演描述来源**：后端在自动化入库/编辑时跑 `af_vhass` 仿真 → `af_nl` 渲染中文 → 存到自动化记录；UI 只读 `preview_nl`（异步算+缓存，编辑后刷新）。若保真度不足，`preview_nl` 前缀「仿真估算」。

**触发历史**：需新增轻量触发日志（复用现有 `fire_recorder` / SSE 历史，或新增 per-automation trigger 表），供 `last_triggered` / `trigger_7d`。

---

## 6. 技术栈与构建

- **复用 `ui/` 同栈**：Vue 3.5 + Vite 6 + TypeScript + pinia 3 + vue-router 4 + naive-ui 2.45。
- **新增 PWA**：`vite-plugin-pwa`（manifest + 可安装）；**离线 Service Worker 谨慎**——此前 memory-agent 踩过 SW 与 `NoCacheStaticMiddleware` 冲突导致缓存陈旧的坑，故离线 SW 先不做或单独策略，manifest 已使「添加到主屏幕」可用。
- **响应式**：mobile-first；手机底部 tab 导航，平板/电脑侧边栏。naive-ui 响应式栅格。
- **目录**：新建 `ui-user/`（与 `ui/` 平级），结构同 `ui/src`（api/ components/ router/ types/ views/）。
- **构建命令**（本地）：`cd ui-user && npm i && npm run dev`（开发） / `npm run build`（产物 `ui-user/dist`）。
- **部署**：`ui-user/dist` → NAS `/vol1/1000/docker/autoforge/ui-user/dist`，独立路径/端口托管（不与开发面板混）。

---

## 7. 是不是 mock？（澄清）

**不是 throwaway mock，是 API-first 前端 + 可切换的 dev mock adapter：**

- 前端严格按 §5 契约开发，类型定义放 `ui-user/src/types/api.ts`。
- 数据层 `ui-user/src/api/` 下分 `client.ts`（真端点）与 `mock.ts`（桩数据）；用 env `VITE_USE_MOCK`（或构建变量）切换。
- mock 仅用于**当前后端未实现时让 UI 可跑可演示**；后端（ADF 另开对话）实现 §5 端点后，切 `VITE_USE_MOCK=false` 即真联通。
- 交付物是**真实 UI + 契约**，mock 是临时适配器，不是最终产物。

---

## 8. 新对话前置环境 / 前置条件（给「新豆包对话」的启动包）

> 本节能整段复制给新对话作为首条上下文。

**任务**：在 `E:/NAS/AutoForge/ui-user/` 新建「用户 WebUI」（Vue3 + Vite + PWA，响应式），按本设计稿 §1–§7 实现四大区块。

**关键环境事实（必读）**：
1. **`E:/NAS` 是 NAS 的断开副本**：本地改 `E:/NAS/AutoForge/...` 不会自动同步到运行容器。前端在本地编辑+构建后，需 `scp ui-user/dist` 到 NAS `/vol1/1000/docker/autoforge/ui-user/dist` 才生效（参考 dev panel 部署记忆 `88451591`/`42379579`）。SSH：`C:\Users\lidicn\.ssh\openssh\OpenSSH-Win64\ssh.exe -i C:\Users\lidicn\.ssh\id_ed25519 lidicn@192.168.2.200`。
2. **只动 `ui-user/`**：不要改 `src/autoforge/`（后端）、不要改 `ui/`（开发面板）。后端端点由 ADF 另开对话实现，你只消费 §5 契约。
3. **技术栈同 `ui/`**：直接参考 `ui/package.json` / `ui/vite.config.ts` / `ui/src/{router,types,api,views}` 的约定（别名 `@`→`src`，pinia 状态，vue-router 路由）。**不要引入新重型依赖**；PWA 用 `vite-plugin-pwa`。
4. **联调**：`vite.config.ts` 加 `server.proxy` 把 `/api` 转到后端（本地起 AF 后端或 `192.168.2.200:8787`）；`VITE_API_BASE=/api` 同源。
5. **契约与 mock**：端点清单见 §5；先写 `src/api/mock.ts` 桩数据（`VITE_USE_MOCK=true` 默认开），类型见 `ui/src/types/api.ts`（dev panel 已有 `PendingItem` 等可参考，但**视图组件不要复用**）。
6. **设计意图唯一权威 = 本文件**：mimo 没有 WebUI 设计，勿去引用 mimo 输出。
7. **命名 TBD**：`workbuddy` / 配对码 / 预演 等均为占位/待定产品名，先用占位，最终命名另定（不要硬写成产品名）。
8. **交付方式**：完成后写 handoff card（文件改动、行为增量、验证方式、已知风险），并同步更新本设计稿状态。

**验收门（建议）**：
- `npm run build` 通过（vite 打包即可，vue-tsc 类型层已知有退化问题，不阻塞）。
- 四大区块在 `VITE_USE_MOCK=true` 下可演示：生成授权码、自动化按 agent 分组、Pending 批准/驳回、设置改名。
- 手机/平板/电脑三种宽度下布局正常；`manifest` 使页面可「添加到主屏幕」。

---

## 9. 安全要点
- 授权码：单次性 + 短时效（短期 5min）+ 绑定用户；长期码可撤销。
- MCP token：按用户作用域隔离（agent 只能管该用户的自动化）；`[删除]` 即时吊销。
- 用户 WebUI 与开发面板用**不同 auth 域**（用户角色 vs admin/dev）；配对码与 token 均用户作用域。
- 试演期异常自动暂停：后端冲突仲裁器/断路器触发时前端只展示，不自动恢复（恢复需用户确认）。

## 10. 命名 TBD / 待定
- 产品名、`workbuddy`、配对码、预演、试演期 → 最终命名另定（见 §8.7）。
- 「开发面板」对外是否仍叫 AutoForge 控制台 → 待定。

## 11. 下一步
1. **本对话（AF-agent）**：本设计稿即交付；不在此构建前端。
2. **新豆包对话**：按 §8 启动包实现 `ui-user/`。
3. **ADF 另开对话**：按 §5 实现后端端点（先 `/api/user/pairing-code`、`/api/mcp/pair`、`/api/user/agents*`、自动化列表+预演+触发历史+禁用/归档、试演期状态）。
4. 三方联调：前端 `VITE_USE_MOCK=false` 接通后端，端到端验证两条部署路径（§4）。
