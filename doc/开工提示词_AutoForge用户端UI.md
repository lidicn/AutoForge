# 开工提示词：AutoForge 用户端 WebUI（ui-user/）

> 把以下整段复制给新豆包对话作为首条消息。

---

## 任务

在 `E:/NAS/AutoForge/ui-user/` 完成 AutoForge 用户端 WebUI。这是一个面向家庭用户的移动端 PWA，技术栈 Vue3 + Vite6 + Naive UI。

**产品名：AutoForge**（浏览器标题、顶栏）。页面底部最下方小字灰色显示 **ForgeSight**。

## 已完成的基础工程

工程已经搭好，`npm run dev` 在 5174 端口跑。以下文件已存在：

- `package.json` / `vite.config.ts` / `tsconfig.json`
- `index.html`（favicon.svg 已配）
- `public/favicon.svg` + `public/icon-192.png` + `public/icon-512.png`
- `src/main.ts` / `src/App.vue`（全局琥珀橙主题 #F59E0B）
- `src/types/api.ts`（全部类型定义）
- `src/api/client.ts` + `src/api/mock.ts`（mock 数据，VITE_USE_MOCK 默认 true）
- `src/router/index.ts`（login + 三 tab 路由守卫）
- `src/stores/auth.ts` + `src/stores/main.ts`
- `src/views/LoginView.vue` / `MainLayout.vue` / `AgentsView.vue` / `AutomationsView.vue` / `AuthCodesView.vue`
- `src/components/PairingModal.vue` / `AutomationCard.vue`

## 你要做的事

基础工程和四大区块已经跑通 mock。你需要：

1. **审查现有代码**，跑 `npm run dev` 在浏览器看效果
2. **按以下设计规范修正细节**（用户已确认的设计决定）
3. **补全交互细节**，打磨动效和布局
4. **不要改后端**，不要动 `src/autoforge/`，不要动 `ui/`（开发面板）

---

## 设计规范（用户确认的决定）

### 整体布局
- 手机底部三 Tab：【Agent】【自动化】【授权码】
- 顶栏左侧显示"AutoForge"（琥珀橙 #F59E0B），右侧齿轮设置按钮
- 页面内容区最底部小字灰色 "ForgeSight"
- 最大宽度 600px 居中
- 设置：右上角齿轮点开抽屉（用户信息、登出、admin 可见开发面板链接）

### Tab 1：Agent
- **顶部 MCP 连接卡片**（琥珀色背景 #fef3c7）：标题"新 Agent 连接"，说明"把下面的 MCP 地址告诉 agent，agent 连入后会弹出配对码"，显示 MCP URL `http://192.168.2.200:8000/mcp`（等宽字体）+ [复制]按钮
- 下方"已连接"列表，每项：头像图标、名称、最后活跃时间、[删除配对]按钮
- 点名称旁铅笔图标可内联改名
- 删除配对需二次确认弹窗
- **不嵌套自动化列表**，自动化全在第二个 Tab
- agent 拿 MCP URL 连入后，屏幕中间弹窗显示配对码（6位数字逐位翻入+呼吸光晕+倒计时）

### Tab 2：自动化
- 顶部子 Tab：【启用】【归档】
- 启用列表**按 agent 名称分组**（折叠分组头，显示 agent 名+数量）
- 待批项混在启用列表中，带红色角标数字在子 Tab 上
- 卡片内容：名称、状态标签（待批/启用/禁用/异常暂停）、预演效果中文一句话、涉及设备 friendly_name 标签（点开看 entity_id 明细）、最近触发时间+近7天次数、试演期状态条
- 操作：批准/驳回（待批时）、禁用/启用、归档/恢复、删除
- 异常暂停卡片红色边框 pulse 动画

### Tab 3：授权码
- 顶部说明文字："agent 写好自动化后部署时需要授权码。有授权码则直接部署，没有则进待批。"
- **长期授权码**：列表展示（码+生成时间+删除按钮），[生成长期授权码]按钮。不撤销一直可用。
- **短期授权码**：同时只存在一个有效短期码。有则展示码+实时倒计时（MM:SS格式每秒刷新）+删除+重新生成按钮；没有则显示滑块选时长（5-30分钟，步进5）+[生成]按钮。
- 新生成码弹窗：逐位翻入动画，提示"把这个码告诉 agent，用于部署当前自动化"

### 登录
- 居中白色卡片，AutoForge logo + 标题，用户名+密码输入框+登录按钮
- 渐变背景（#fafafa 到 #fef3c7）
- mock 模式下任意输入可登录

### 视觉
- 主色 #F59E0B（琥珀橙）
- 成功 #22C55E，错误 #EF4444，禁用灰 #9CA3AF
- 卡片白底+轻阴影，圆角 12px
- 系统字体栈，不加载 web font
- Mobile-first，安全区域适配（env(safe-area-inset)）

### 动画
- Tab 切换：fade + 轻微滑动 150ms
- 配对码弹窗：scale 0.9→1 + 6位数字逐位翻入 80ms 间隔 + 呼吸光晕
- 异常卡片：红色边框 pulse 1.5s 循环
- 授权码生成：数字 flip 入场 100ms 间隔
- 操作反馈：Naive UI useMessage toast

## API 契约

类型在 `src/types/api.ts`，mock 在 `src/api/mock.ts`。后端就绪后改 `VITE_USE_MOCK=false`。

关键端点（详见 `doc/设计_ForgeSight用户端UI_前端定稿_20260924.md` §9）：
- POST /api/auth/login, POST /api/auth/logout, GET /api/auth/me
- GET/DELETE/PATCH /api/user/agents
- GET /api/mcp/pair-request（SSE 推送配对码）
- GET/POST/DELETE /api/user/auth-codes
- GET /api/automations, POST enable/disable/archive/unarchive, DELETE
- GET /api/pending, POST approve/reject

## 关键环境事实

- `E:/NAS/AutoForge/` 是本地副本，改完不会自动同步到 NAS
- 构建：`cd ui-user && npm run build` → 产物 `ui-user/dist`
- 部署到 NAS：`scp ui-user/dist/* lidicn@192.168.2.200:/vol1/1000/docker/autoforge/ui-user/dist/`
- SSH：`C:\Users\lidicn\.ssh\openssh\OpenSSH-Win64\ssh.exe -i C:\Users\lidicn\.ssh\id_ed25519 lidicn@192.168.2.200`
- 后端在 192.168.2.200:8787，vite.config.ts 已配 /api proxy

## 验收门

- `npm run build` 通过
- 三个 Tab 在 mock 下可演示
- 手机宽度（375px）布局正常
- PWA manifest 可安装到主屏幕
