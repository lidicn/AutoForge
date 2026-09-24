# ForgeSight 用户端 UI · 后端联调清单

> 日期：2026-09-24 ｜ 前端基于 MiMo 方案已完成 mock 版，dev 跑在 5175
> 本文档 = 前端切真实 API 时需要后端提供的全部端点和数据契约

---

## 1. 前端现状

- 代码目录：`E:/NAS/AutoForge/ui-user-mimo/`
- 技术栈：Vue3.5 + Vite6 + Naive UI + Pinia + Vue Router + vite-plugin-pwa
- 当前模式：`src/api/mock.ts` 桩数据，全量 mock 可演示
- 切换真 API：在 `src/stores/main.ts` 把 `mockApi` 换成 `httpApi` 即可，视图层不动
- 已实现四大区块：Agent 列表 / 自动化（按 agent 分组+待批+归档）/ 授权码 / 登录

## 2. 需要后端提供的端点

### 2.1 认证

| 方法 | 路径 | 请求 | 响应 | 说明 |
|---|---|---|---|---|
| POST | `/api/auth/login` | `{username, password}` | `{user: {username, role}, token}` | 登录，返回 JWT 或 session token |
| POST | `/api/auth/logout` | - | `{ok: true}` | 登出 |
| GET | `/api/auth/me` | - | `{user: {username, role}}` | 启动时恢复会话 |

> 前端目前用 localStorage 存 session，切真后改成 Authorization header 带 token。

### 2.2 Agent 管理

| 方法 | 路径 | 请求 | 响应 | 说明 |
|---|---|---|---|---|
| GET | `/api/user/agents` | - | `Agent[]` | 已连 agent 列表 |
| PATCH | `/api/user/agents/{id}` | `{name}` | `Agent` | 改名 |
| DELETE | `/api/user/agents/{id}` | - | `{ok: true}` | 吊销 token + 移除 |

**Agent 类型**：
```ts
interface Agent {
  agent_id: string
  name: string
  connected_at: string   // ISO
  last_seen: string      // ISO
}
```

### 2.3 配对码（Pairing）

> **重要**：配对码不是用户在前端生成的。是 agent 连 MCP 时后端生成码，推送到前端弹窗。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET (SSE) | `/api/user/pair-request` | 长连接，agent 连 MCP 时后端推送 `PairRequest` |
| POST | `/api/mcp/pair` | agent 端用码兑换 token（这个是 MCP 入口，agent 调，不是前端调） |
| POST | `/api/user/pair/{code}/confirm` | 用户在前端点"确认配对成功" |

**PairRequest 类型**：
```ts
interface PairRequest {
  agent_name_hint: string   // agent 自报名称
  code: string              // 6位数字
  expires_at: string        // ISO，比如 5 分钟后
}
```

> 前端目前是 mock：点"配对新Agent"按钮生成码。真实场景应该是 SSE 自动推弹窗。
> **需要后端确认**：SSE 端点路径、事件格式。

### 2.4 自动化

| 方法 | 路径 | 请求 | 响应 | 说明 |
|---|---|---|---|---|
| GET | `/api/automations` | - | `Automation[]` | 全部自动化 |
| GET | `/api/automations/{id}` | - | `Automation` | 详情 |
| POST | `/api/automations/{id}/enable` | `{enabled: bool}` | `Automation` | 启用/停用 |
| POST | `/api/automations/{id}/archive` | `{archived: bool}` | `Automation` | 归档/恢复 |
| DELETE | `/api/automations/{id}` | - | `{ok: true}` | 硬删 |
| GET | `/api/automations/{id}/preview` | - | `{preview_nl: string}` | 预演中文描述 |

**Automation 类型**：
```ts
interface DeviceRef {
  friendly_name: string
  entity_id: string
}

interface TrialInfo {
  state: 'auto' | 'shadow' | 'canary'
  since: string | null
  anomaly: string | null
}

interface Automation {
  id: string
  name: string
  agent_id: string
  agent_name: string
  status: 'pending' | 'enabled' | 'disabled' | 'anomaly'
  preview_nl: string
  devices: DeviceRef[]
  last_triggered: string | null
  trigger_7d: number
  archived: boolean
  trial: TrialInfo
}
```

### 2.5 待批（Pending）

| 方法 | 路径 | 请求 | 响应 |
|---|---|---|---|
| GET | `/api/pending` | - | `PendingItem[]` |
| POST | `/api/pending/{op_id}/approve` | - | `{ok: true}` |
| POST | `/api/pending/{op_id}/reject` | `{reason: string}` | `{ok: true}` |

**PendingItem 类型**：
```ts
interface PendingItem {
  op_id: string
  summary: string
  agent_name: string
  submitted_at: string
  blast_radius: string   // 'low' | 'medium' | 'high' | 'critical' 或自由文本
}
```

### 2.6 授权码（Auth Code）

> **注意区分**：配对码（agent 连 MCP 用）vs 授权码（agent 部署自动化时用）是两个东西。

| 方法 | 路径 | 请求 | 响应 | 说明 |
|---|---|---|---|---|
| GET | `/api/auth-codes` | - | `AuthCode[]` | 全部授权码 |
| POST | `/api/auth-codes/short` | `{minutes: number}` | `AuthCode` | 生成短期码（5-30分钟） |
| POST | `/api/auth-codes/long` | - | `AuthCode` | 生成长期码 |
| DELETE | `/api/auth-codes/{code}` | - | `{ok: true}` | 作废 |

**AuthCode 类型**：
```ts
interface AuthCode {
  code: string           // 6位数字（短期和长期都是）
  type: 'short' | 'long'
  created_at: string
  expires_at: string | null   // null = 长期不撤销一直有效
}
```

> **短期码约束**：同一时刻只能有一个有效短期码，后端需强制。

## 3. 关键业务流程

### 3.1 新 Agent 接入
1. 用户在前端 Agent Tab 看到 MCP URL：`http://192.168.2.200:8000/mcp`
2. 用户把 URL 复制给 agent
3. agent 连 MCP → 后端生成配对码 → **SSE 推送到前端弹窗**
4. 用户把码告诉 agent → agent 调 `/api/mcp/pair` 兑换 token
5. 前端收到 SSE 事件或轮询到 agent 出现在列表里

### 3.2 自动化部署（两条路径）
- **路径A（有授权码）**：agent 有有效授权码 → 直接部署 → 自动化进 enabled 列表 → 进入试演期
- **路径B（无授权码）**：agent 提交到 pending → 用户在前端"待批"区批准/驳回

### 3.3 试演期
- 自动化部署后进入 shadow → canary → auto 三个阶段
- 异常时（冲突/断路器/连续失败）后端把 status 改成 `anomaly`，前端显示红色
- 前端只展示，不自动恢复，需用户手动操作

## 4. 需要后端确认/补充的点

1. **SSE 配对推送**：路径和事件格式？前端现在是 mock 按钮触发，需要改成 SSE 自动弹窗
2. **授权码兑换**：agent 部署自动化时怎么用授权码？是 MCP 调用时 header 带 code，还是有专门端点？
3. **触发历史**：`last_triggered` 和 `trigger_7d` 数据从哪来？需要后端提供 per-automation 触发日志
4. **预演描述**：`preview_nl` 是后端生成还是前端自己拼？目前设计是后端跑仿真后存到自动化记录里
5. **多用户隔离**：agent 的 token 怎么绑定到具体用户？用户之间数据隔离怎么做？
6. **登录态**：JWT？session cookie？前端需要知道 token 放哪、怎么带

## 5. 部署

- 构建产物：`ui-user-mimo/dist/`
- 部署到 NAS：`/vol1/1000/docker/autoforge/ui-user/dist`
- Nginx 需要配：
  - `/ui-user/` 静态文件
  - `/api/` 反代到后端 `127.0.0.1:8787`
  - SPA fallback（所有路由都回 index.html）
  - SSE 端点 `/api/user/pair-request` 需要关 buffering

## 6. 前端已完成的功能（mock 下可演示）

- [x] 登录页（demo/forge2026）
- [x] 深色/浅色主题切换（顶栏太阳/月亮按钮）
- [x] Agent Tab：MCP URL 卡片+复制、agent 列表、改名、删除配对
- [x] 自动化 Tab：启用/归档子 Tab、按 agent 分组折叠、待批区（红色角标）、卡片含预演/设备/试演期状态条
- [x] 授权码 Tab：短期码滑块(5-30分钟)+实时倒计时+复制按钮、长期码列表+复制按钮+作废
- [x] PWA manifest + 图标（可添加到主屏幕）
- [x] 响应式：max-width 600px 居中，手机底部 Tab

## 7. 切真 API 时前端要改的地方

1. `src/api/mock.ts` → 写 `src/api/http.ts`，实现同一个 `ApiClient` 接口
2. `src/stores/main.ts` → import 改成 `httpApi`
3. 加 axios/fetch 拦截器带 token
4. 配对流程：从按钮触发改成 SSE 监听
5. CORS / 代理：vite dev proxy 或 nginx 反代
