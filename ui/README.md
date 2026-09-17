# AutoForge 前端控制台（Round 1：只读控制台）

> 版本：0.1.0 · 生成日期：2026-09-14
> 双实现：`index.html`（单文件零依赖） + `src/`（Vite + TS 工程）

---

## 快速启动

### 方式一：单文件版（零依赖，推荐演示）

双击 `index.html` 或在浏览器中打开，无需安装任何依赖：

```bash
start index.html    # Windows
open index.html     # macOS
```

> 单文件版通过 CDN 加载 Vue 3 + Vue Router，所有 Mock 数据内联。

### 方式二：Vite 开发服务器（推荐开发）

```bash
npm install
npm run dev         # → http://localhost:5173
```

### 方式三：构建产物

```bash
npm run build       # vue-tsc -b && vite build → dist/
npx serve dist      # 部署到任意静态服务器
```

---

## Mock ↔ 真后端切换

### 环境变量语义（Vite 工程）

| 变量 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `VITE_USE_MOCK` | `string`（'true'/'false'） | `'true'` | 设为 `'false'` 时切换到真后端；其余任何值（包括不设置）均使用 Mock |
| `VITE_API_BASE` | `string`（URL） | `http://localhost:8787/api` | 真后端的基础地址；仅在 `VITE_USE_MOCK=false` 时生效 |

> **重要**：Vite 在**构建时**将 `import.meta.env.*` 内联进产物，因此 `VITE_API_BASE` / `VITE_USE_MOCK` 是**构建期常量**，切换模式必须重新 `npm run build`。

切换示例：

```bash
# Mock 模式（默认，无需任何配置）
npm run dev

# 真后端模式 — 开发
VITE_USE_MOCK=false npm run dev

# 真后端 + 指定地址 — 开发
VITE_USE_MOCK=false VITE_API_BASE=http://192.168.2.200:8787/api npm run dev

# 真后端模式 — 生产构建（重新构建 dist/）
VITE_USE_MOCK=false npm run build
```

### 单文件版（`index.html` / `standalone.html`）

双文件均为零依赖单 HTML，通过修改内联 JS 切换：

```javascript
// index.html / standalone.html 中的第 ~210 行
let useMock = true    // true = Mock，false = 真后端
const API_BASE = 'http://localhost:8787/api'  // 真后端地址，按需修改
```

> 切换后直接刷新浏览器即可，无需重新构建。

### Vite 工程源码实现

切换逻辑位于 `src/api/index.ts`：

```typescript
const USE_MOCK = import.meta.env.VITE_USE_MOCK !== 'false'
export const facade = {
  health: () => USE_MOCK ? mockApi.health() : api.health(),
  // ... 其余方法同理
}
```

后端地址由 `src/api/client.ts` 读取：

```typescript
const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8787/api'
```

---

## 页面导览

| 页面 | 路由 | 使用的端点 | 说明 |
|------|------|-----------|------|
| ① 概览 | `/overview` | `GET /health` | 健康状态、版本、里程碑 |
| ② 自动化列表 | `/automations` | `GET /graphs` + `GET /conf/_all` | 搜索过滤、置信度 badge、跳转详情 |
| ③ 自动化详情 | `/automations/:name` | `GET /graphs/{name}` | IR 原文、NL 文本（原样展示）、诊断分级 |
| ④ 仿真回放 | `/simulation` | `POST /sim` | IR/Seed/Events 输入 → 轨迹、审计、最终状态 |
| ⑤ 置信度面板 | `/confidence` | `GET /conf/{name}` + `POST /conf/{name}/intervene` | 阈值可视化、模拟人工干预 |
| ⑥ 版本与 Diff | `/versions` | `GET /graphs` + `GET /diff` | 双版本对比、结构化 diff |
| ⑦ AF-Spec 工作台 | `/spec-editor` | `POST /spec/compile` | 左编辑右编译（IR + NL + 诊断） |
| ⑧ 故障注入图鉴 | `/faults` | `GET /faults` | 五类故障 + 四类失败映射 |

---

## API 端点覆盖（11/11）

| # | 端点 | 方法 | 使用页面 |
|---|------|------|----------|
| 1 | `/health` | GET | 概览 |
| 2 | `/graphs` | GET | 自动化列表、版本与 Diff |
| 3 | `/graphs/{name}` | GET | 自动化详情 |
| 4 | `/build` | POST | — 已实现，Round 1 无页面调用 |
| 5 | `/sim` | POST | 仿真回放 |
| 6 | `/conf/{name}` | GET | 置信度面板、自动化列表 |
| 7 | `/conf/{name}/intervene` | POST | 置信度面板 |
| 8 | `/diff` | GET | 版本与 Diff |
| 9 | `/spec/{name}` | GET | — 已实现，Round 1 无页面调用 |
| 10 | `/spec/compile` | POST | AF-Spec 工作台 |
| 11 | `/faults` | GET | 故障注入图鉴 |

> 完整映射详见 [`docs/契约对照表.md`](docs/契约对照表.md)。

---

## Mock 数据

- **IR 数据**：`examples/ir/*.json`（5 个 G1 用例，AutoForge 参考仓库格式）
  - `study_day_light` — 白天人在补光
  - `study_leave_light` — 人离 10 分钟关灯
  - `night_desk_lamp` — 夜晚开电脑开挂灯
  - `study_ask_ac` — 温度高询问开空调（含 L2 诊断）
  - `study_ask_light` — 询问中打断不回滚
- **元数据**：health / graphs 列表 / confidences / faults 内联于 `mock.ts`

---

## SPA Fallback（路由回退）

构建产物 `dist/` 为单页应用（SPA），使用 `createWebHistory`（HTML5 History Mode）。
部署到静态服务器时，**所有未命中静态文件的请求必须回退到 `index.html`**，否则浏览器刷新深度链接路由会返回 404。

### Nginx 示例

```nginx
location / {
  root   /usr/share/nginx/html;
  index  index.html;
  try_files $uri $uri/ /index.html;   # ← 关键：SPA fallback
}
```

### Caddy 示例

```caddy
example.com {
  root * /usr/share/nginx/html
  file_server
  rewrite * {path} /index.html
}
```

### `serve`（npx serve）

已内置 SPA fallback，无需额外配置：

```bash
npx serve dist    # 自动回退到 index.html
```

### Azure Static Web Apps / Vercel / Netlify

在根目录添加路由配置文件即可：

**`dist/_redirects`**（或项目根目录）：
```
/*  /index.html  200
```

**`vercel.json`**：
```json
{ "rewrites": [{ "source": "/(.*)", "destination": "/index.html" }] }
```

**`.netlify/config`**：
```
/*    /index.html   200
```

> 部署到 192.168.2.200 等内网服务器前，确认服务端的 fallback 配置正确，避免深度链接 404。

---

## 项目结构

```
autoforge-ui/
├── index.html              # 单文件版（CDN Vue，零依赖直接运行）
├── package.json            # Vite 工程依赖
├── vite.config.ts          # Vite 配置
├── tsconfig*.json          # TypeScript 配置
├── src/                    # Vite + TS 工程
│   ├── main.ts             # 应用入口
│   ├── App.vue             # 根组件（全局 CSS 变量）
│   ├── router/index.ts     # 路由定义（10 条路由）
│   ├── api/
│   │   ├── client.ts       # 真后端客户端（fetch）
│   │   ├── mock.ts         # 本地 Mock 服务
│   │   └── index.ts        # facade 层（Mock/Real 切换）
│   ├── types/api.ts        # TypeScript 类型定义
│   ├── components/
│   │   ├── DiagnosticPanel.vue  # 诊断分级展示
│   │   └── SafetyAlert.vue      # 安全闸门提示
│   └── views/              # 页面组件（9 个）
├── examples/
│   └── ir/                 # 5 个 G1 用例 IR（JSON）
└── docs/
    ├── architecture.md     # 前端架构设计
    ├── 契约对照表.md        # 端点 ↔ Mock ↔ 页面映射
    └── 交付回执.md
```

---

## Round 2 占位

以下端点本轮未实现：

- `GET /asks` — ask 审批列表
- `POST /asks/{id}/answer` — ask 审批应答
- `POST /live/run` — 真机下发（需白名单 + 二次确认闸）

---

## 已知限制

- Round 1 纯只读，无真实 HA 连接
- `/build` 和 `/spec/{name}` 已实现 Mock，但无页面调用（Round 2 预留）
- 无认证/鉴权，所有端点公开
- `npm install` 在沙箱环境可能受限，单文件版可直接使用
