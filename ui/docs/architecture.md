# AutoForge UI 前端架构设计

> Round 1（只读控制台）· 生成日期：2026-09-14

---

## 1. 技术栈

| 层 | 技术 | 版本 |
|----|------|------|
| 框架 | Vue 3 | ^3.5.0 |
| 语言 | TypeScript | ~5.7.0 |
| 构建 | Vite | ^6.2.0 |
| 路由 | Vue Router | ^4.5.0 |
| 状态管理 | Pinia | ^3.0.0 |
| 类型检查 | vue-tsc | ^2.2.0 |

---

## 2. 项目目录结构

```
autoforge-ui/
├── index.html              # 单文件版本（Vite 构建入口）
├── package.json            # 依赖声明
├── vite.config.ts          # Vite 配置（@ 别名 → ./src）
├── tsconfig*.json          # TypeScript 配置（app / node / 根）
├── src/
│   ├── main.ts             # 应用入口：createApp → Pinia → Router → mount
│   ├── App.vue             # 根组件：RouterView + 全局 CSS 变量
│   ├── env.d.ts            # Vite 客户端类型声明
│   ├── router/
│   │   └── index.ts        # 路由表定义
│   ├── api/
│   │   ├── client.ts       # 真实后端客户端（fetch + baseURL）
│   │   ├── mock.ts         # 本地 Mock 实现（零依赖）
│   │   └── index.ts        # facade 层（根据环境变量切换 mock/real）
│   ├── types/
│   │   └── api.ts          # 全部 API 响应类型定义
│   ├── views/              # 页面组件（9 个）
│   │   ├── OverviewView.vue
│   │   ├── AutomationsListView.vue
│   │   ├── AutomationDetailView.vue
│   │   ├── SimulationView.vue
│   │   ├── ConfidenceView.vue
│   │   ├── VersionsView.vue
│   │   ├── SpecEditorView.vue
│   │   ├── FaultsView.vue
│   │   └── NotFoundView.vue
│   └── components/         # 公共组件（2 个）
│       ├── DiagnosticPanel.vue
│       └── SafetyAlert.vue
└── docs/
    ├── architecture.md     # 本文档
    ├── 契约对照表.md
    └── 交付回执.md
```

---

## 3. 路由设计

### 3.1 路由表

| # | 路径 | name | 组件 | 说明 |
|---|------|------|------|------|
| 1 | `/` | — | redirect → `/overview` | 首页重定向 |
| 2 | `/overview` | `overview` | `OverviewView.vue` | 概览：健康状态、里程碑、快捷导航 |
| 3 | `/automations` | `automations` | `AutomationsListView.vue` | 自动化列表：搜索、置信度 badge、跳转详情 |
| 4 | `/automations/:name` | `automation-detail` | `AutomationDetailView.vue` | 自动化详情：IR 原文、NL 文本、诊断 |
| 5 | `/simulation` | `simulation` | `SimulationView.vue` | 仿真回放：IR/Seed/Events 输入 → 轨迹/审计/状态 |
| 6 | `/confidence` | `confidence` | `ConfidenceView.vue` | 置信度面板：阈值可视化、干预按钮 |
| 7 | `/versions` | `versions` | `VersionsView.vue` | 版本与 Diff：双版本对比、结构化 diff |
| 8 | `/spec-editor` | `spec-editor` | `SpecEditorView.vue` | AF-Spec 工作台：左编辑右编译 |
| 9 | `/faults` | `faults` | `FaultsView.vue` | 故障注入图鉴：五类故障 + 四类失败 |
| 10 | `/:pathMatch(.*)*` | `not-found` | `NotFoundView.vue` | 404 兜底 |

> 共 10 条路由（8 个页面 + 1 个重定向 + 1 个 404），覆盖验收要求的 9 个页面。

### 3.2 路由策略

- **懒加载**：所有页面组件使用 `() => import(...)` 动态导入，按路由分割 chunk。
- **参数传递**：详情页通过 `route.params.name` 获取自动化名称，不依赖 Pinia 缓存。
- **导航**：页面间跳转使用 `<router-link>` 组件，无需编程式导航（Round 1 无复杂流）。

---

## 4. 状态管理

### 4.1 当前方案：View 本地状态

Round 1 所有页面使用 **Vue 3 Composition API + `ref()` / `reactive()` 局部状态**，不依赖 Pinia store。

每个 View 遵循相同的 `loading / data / error` 三态模式：

```ts
const data = ref<SomeResponse | null>(null)
const loading = ref(true)
const error = ref('')

onMounted(async () => {
  try {
    const res = await facade.someEndpoint(...)
    data.value = res.data
  } catch (e) {
    error.value = String(e)
  } finally {
    loading.value = false
  }
})
```

### 4.2 Pinia 集成状态

- `createPinia()` 已在 `main.ts` 中注册，`app.use(createPinia())` 已执行。
- Round 1 中 **Pinia 未创建任何 store**：因为 8 个页面均为独立只读视图，无跨页面共享状态需求。
- Round 2 引入写操作（如 ask 审批、live/run）后，应创建以下 Pinia store：

| Store | 职责 |
|-------|------|
| `useAuthStore` | 用户认证状态、白名单权限 |
| `useAutomationStore` | 缓存当前选中的自动化，跨页面共享 |
| `useSimStore` | 仿真运行状态、轨迹数据缓存 |

### 4.3 设计决策

> **为什么不用 Pinia？** — 当前 8 个页面完全独立，无共享状态。引入空 store 增加复杂度但无收益。Pinia 已在 `main.ts` 中注册，Round 2 可直接创建 store 无需修改入口。

---

## 5. API 层架构

### 5.1 三层架构

```
View → facade (src/api/index.ts)
            ├── mockApi (src/api/mock.ts)  ← 默认
            └── api (src/api/client.ts)     ← VITE_USE_MOCK=false 时启用
```

### 5.2 端点覆盖（11 个）

| # | 端点 | 方法 | facade 方法 | mockApi 方法 | api 方法 |
|---|------|------|-------------|-------------|----------|
| 1 | `/health` | GET | `facade.health()` | `mockApi.health()` | `api.health()` |
| 2 | `/graphs` | GET | `facade.graphs()` | `mockApi.graphs()` | `api.graphs()` |
| 3 | `/graphs/{name}` | GET | `facade.graph(name, version?)` | `mockApi.graph(name)` | `api.graph(name, version?)` |
| 4 | `/build` | POST | `facade.build(ir)` | `mockApi.build(ir)` | `api.build(ir)` |
| 5 | `/sim` | POST | `facade.sim(ir, seed?, events?)` | `mockApi.sim(ir, seed?, events?)` | `api.sim(ir, seed?, events?)` |
| 6 | `/conf/{name}` | GET | `facade.conf(name)` | `mockApi.conf(name)` | `api.conf(name)` |
| 7 | `/conf/{name}/intervene` | POST | `facade.intervene(name, id)` | `mockApi.intervene(name, id)` | `api.intervene(name, id)` |
| 8 | `/diff` | GET | `facade.diff(name, old, new)` | `mockApi.diff(name, old, new)` | `api.diff(name, old, new)` |
| 9 | `/spec/{name}` | GET | `facade.spec(name, version?)` | `mockApi.spec(name)` | `api.spec(name, version?)` |
| 10 | `/spec/compile` | POST | `facade.specCompile(text)` | `mockApi.specCompile(text)` | `api.specCompile(text)` |
| 11 | `/faults` | GET | `facade.faults()` | `mockApi.faults()` | `api.faults()` |

> **覆盖率：11/11（100%）**

### 5.3 Mock/Real 切换机制

```ts
// src/api/index.ts
const USE_MOCK = import.meta.env.VITE_USE_MOCK !== 'false'

export const facade = {
  health: () => USE_MOCK ? mockApi.health() : api.health(),
  // ... 每个方法同理
}
```

| 场景 | 命令 / 配置 |
|------|-------------|
| 默认（Mock） | 无需额外配置 |
| 切换真后端 | `VITE_USE_MOCK=false npm run dev` |
| 指定后端地址 | `VITE_API_BASE=https://your-server/api npm run dev` |

### 5.4 真实客户端（`client.ts`）

- 使用 `fetch()` 原生 API，不引入 axios。
- `baseURL` 通过 `import.meta.env.VITE_API_BASE` 配置，默认 `http://localhost:8787/api`。
- 统一 `request<T>(method, path, body?)` 泛型封装，返回 `Promise<T>`。
- 错误处理：HTTP 非 2xx 时抛出 `Error('HTTP {status}: {statusText}')`。

### 5.5 Mock 服务（`mock.ts`）

- 零依赖实现，使用 `setTimeout` 模拟网络延迟（50–200ms）。
- 数据来源于附录 A 的 G1 用例（5 个自动化 IR）。
- 全部 11 个端点均有完整返回，覆盖正向 + 边界场景：
  - `build`：对 `dangerous_delete_all` 返回 L3_ACTION 错误，对 `study_ask_ac` 返回 L2 警告
  - `conf`：按名称过滤或返回全部
  - `intervene`：置信度降低 0.25（模拟人工干预）
  - `specCompile`：返回完整 IR + NL + 诊断

---

## 6. 组件边界

### 6.1 组件层级图

```
App.vue (根组件)
└── RouterView
    ├── OverviewView          ← 独立页面，无子组件
    ├── AutomationsListView   ← 独立页面，无子组件
    ├── AutomationDetailView  ← 独立页面
    │   ├── SafetyAlert       ← 安全闸门提示
    │   └── DiagnosticPanel   ← 诊断分级展示
    ├── SimulationView        ← 独立页面
    │   └── SafetyAlert       ← 安全闸门提示（当前禁用）
    ├── ConfidenceView        ← 独立页面，无子组件
    ├── VersionsView          ← 独立页面，无子组件
    ├── SpecEditorView        ← 独立页面
    │   ├── SafetyAlert       ← 安全闸门提示
    │   └── DiagnosticPanel   ← 诊断分级展示
    ├── FaultsView            ← 独立页面，无子组件
    └── NotFoundView          ← 独立页面，无子组件
```

### 6.2 公共组件（2 个）

#### `DiagnosticPanel.vue`

| 属性 | 类型 | 说明 |
|------|------|------|
| `diagnostics` | `Diagnostic[]` | 诊断列表 |

- 按 `level` 分为 error（红色）和 warning（黄色）两组。
- 每条诊断显示 `code`、位置（`automation_id/node_id`）、消息。
- 无诊断时显示 ✓ 无诊断问题。
- **使用场景**：自动化详情页、AF-Spec 编译结果。

#### `SafetyAlert.vue`

| 属性 | 类型 | 说明 |
|------|------|------|
| `diagnostics` | `Diagnostic[]` | 诊断列表 |

- 过滤特定安全码：`L3_ACTION`、`HTTP_NOT_WHITELISTED`、`SHADOW_WRITES_DEVICE`、`LOW_CONF_WRITES_DEVICE`、`L2_NEEDS_CONFIRM`。
- 红色醒目警告框，列出所有触发的安全闸门。
- **使用场景**：自动化详情页、AF-Spec 编译结果、仿真回放。

### 6.3 组件职责划分原则

| 组件类型 | 职责 | 是否接受 props |
|----------|------|---------------|
| View（页面） | 数据获取 + 布局 + 交互 | 否（通过 route/API 获取数据） |
| Component（公共） | 纯展示 + 数据格式化 | 是（通过 props 接收数据） |
| Component 不含逻辑 | 无 API 调用、无路由跳转 | — |

### 6.4 Round 2 计划新增组件

| 组件 | 职责 | 依赖端点 |
|------|------|----------|
| `AskCard.vue` | Ask 审批卡片（展示/应答） | `/asks`、`/asks/{id}/answer` |
| `RunPanel.vue` | 真机下发面板（白名单 + 二次确认） | `/live/run` |
| `NodeCard.vue` | IR 节点可视化卡片 | 复用 IR 类型 |
| `EdgeArrow.vue` | IR 边连接箭头 | 复用 Edge 类型 |

---

## 7. 类型系统

### 7.1 类型定义文件

`src/types/api.ts` 包含全部 API 响应类型，与附录 A 契约一一对应：

| 类型 | 用途 |
|------|------|
| `HealthResponse` | 健康状态 |
| `GraphItem` / `GraphListResponse` | 自动化列表项 / 列表 |
| `Node` / `Edge` / `IR` | IR 数据结构 |
| `Diagnostic` | 诊断条目 |
| `GraphResponse` | 自动化详情（IR + NL + 诊断） |
| `BuildResponse` | 构建结果（errors/warnings/nl） |
| `Instance` / `AuditEntry` / `SimResponse` | 仿真结果 |
| `ConfidenceItem` / `Thresholds` / `ConfResponse` | 置信度 |
| `DiffStructured` / `DiffResponse` | Diff 结果 |
| `SpecCompileResponse` | Spec 编译结果 |
| `FaultKind` / `FailureKind` / `FaultsResponse` | 故障图鉴 |
| `ApiError` / `ApiResponse<T>` | 统一错误/响应包装 |

### 7.2 类型使用约定

- View 通过 `facade` 获取数据后，直接使用 `res.data` 作为 `ref` 的泛型参数。
- 所有 JSON 序列化（如详情页 IR 展示）使用 `JSON.stringify(obj, null, 2)`。
- 输入 JSON 解析（仿真页）使用 `JSON.parse()`，异常时 `catch` 展示错误消息。

---

## 8. 样式系统

### 8.1 全局 CSS 变量（`App.vue`）

| 变量 | 值 | 用途 |
|------|-----|------|
| `--af-bg` | `#f5f5f5` | 页面背景 |
| `--af-surface` | `#ffffff` | 卡片/面板背景 |
| `--af-primary` | `#4f46e5` | 主色（链接、按钮） |
| `--af-error` | `#dc2626` | 错误 |
| `--af-warning` | `#f59e0b` | 警告 |
| `--af-success` | `#10b981` | 成功/通过 |
| `--af-info` | `#3b82f6` | 信息（NL 文本左边框） |
| `--af-text` | `#1f2937` | 主文字 |
| `--af-text-secondary` | `#6b7280` | 次要文字 |
| `--af-border` | `#e5e7eb` | 边框 |
| `--af-radius` | `8px` | 圆角 |

### 8.2 样式策略

- 每个 View / Component 使用 `<style scoped>`，避免全局污染。
- 全局仅定义 CSS 变量和基础重置（`margin: 0; padding: 0; box-sizing: border-box`）。
- 无 CSS 预处理器（不使用 SCSS/Less），纯 CSS 即可满足 Round 1 需求。
- 响应式：页面使用 `max-width: 960px~1200px` 居中，小屏通过 `grid-template-columns: repeat(auto-fill, minmax(...))` 自适应。

---

## 9. 构建与部署

### 9.1 开发

```bash
npm install
npm run dev       # Vite dev server → http://localhost:5173
```

### 9.2 构建

```bash
npm run build     # vue-tsc -b && vite build → dist/
```

### 9.3 单文件部署

`index.html` 是 Vite 构建后的 HTML 入口，配合 `dist/` 目录可部署到任意静态服务器（Nginx、GitHub Pages 等）。

---

## 10. 架构约束与扩展点

### 10.1 Round 1 约束

| 约束 | 说明 |
|------|------|
| 纯只读 | 所有写操作（build/sim/compile）仅调用 Mock 或只读分析，不执行真实动作 |
| 无认证 | 无登录/鉴权，所有端点公开访问 |
| 无持久化 | 所有状态在内存中，刷新即丢失 |
| NL 原样展示 | 后端返回的 `nl` 字段不做前端拼接或修改 |

### 10.2 Round 2 扩展点

| 扩展 | 涉及模块 | 预估改动 |
|------|----------|----------|
| 认证与权限 | 新增 `AuthProvider`、`useAuthStore` | 路由守卫 + Pinia store |
| Ask 审批 | 新增 `AskCard` 组件、`useAskStore` | 2 个新端点 + 1 个新页面 |
| 真机下发 | 新增 `RunPanel` 组件、二次确认闸 | 1 个新端点 + SafetyAlert 扩展 |
| IR 可视化 | 新增 `NodeCard`/`EdgeArrow`、SVG 渲染 | 详情页重写 |
| 持久化 | Pinia store 增加 `localStorage` 持久化 | `pinia-plugin-persistedstate` |
| 测试框架 | 引入 Vitest + Vue Test Utils | 新增 `test/` 目录 |

---

## 11. 验收检查清单

| # | 验收项 | 状态 | 说明 |
|---|--------|:----:|------|
| 1 | 路由设计覆盖全部 9 个页面 | ✓ | 8 个命名路由 + 1 个 404 兜底 |
| 2 | 状态管理方案明确 | ✓ | View 本地状态（Round 1），Pinia 已注册待用（Round 2） |
| 3 | API 层封装 baseURL 可切换 mock/real | ✓ | `VITE_USE_MOCK` + `VITE_API_BASE` 双环境变量 |
| 4 | 组件边界定义清晰 | ✓ | 2 个公共组件 + 9 个独立页面，职责分离 |
| 5 | 架构文档输出到 docs/architecture.md | ✓ | 本文档 |
