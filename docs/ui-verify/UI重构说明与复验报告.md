# AutoForge 控制台 UI 重构与复验报告

- **时间**：2026-09-14
- **目标**：把原「毛坯房」式原型 UI 升级为可用的专业控制台
- **线上地址**：`http://192.168.2.200:8787`

---

## 一、结论

> **✅ 重构完成并已上线。** 引入 **Naive UI** 组件库，重构全局布局 + 8 个页面 + 2 个组件；复验 9 个页面全部 200、**零 JS 报错、零资源失败、零缺失内容**，关键交互（运行仿真 / 版本对比 / Spec 编译）真实点击通过。

---

## 二、技术选型

| 项 | 选择 | 理由 |
|---|---|---|
| 组件库 | **Naive UI** | Vue3 原生、完整 TS 类型、CSS-in-JS 无需额外样式配置、组件级 tree-shaking（按需 import） |
| 主题 | `n-config-provider` themeOverrides | 统一主色 `#4f46e5`、圆角 8px，品牌色一致 |
| 布局 | `n-layout` + 顶栏 `n-menu` | 固定顶栏 + 内容区自适应滚动 |
| 表格 | `n-data-table` | 列定义式渲染、内置 loading/空态/排序扩展位 |

---

## 三、改动文件清单（共 12 个）

| 文件 | 改动 |
|---|---|
| `src/App.vue` | **新增全局布局**：顶栏品牌区 + 水平导航菜单 + 内容容器；`n-config-provider` 主题与中文 locale |
| `src/views/OverviewView.vue` | 统计卡片（运行状态/版本/里程碑/归档数）+ 里程碑标签墙 + 快捷入口卡片网格 |
| `src/views/AutomationsListView.vue` | `n-data-table` 表格 + 筛选框 + 置信度彩色标签 + 详情跳转 |
| `src/views/AutomationDetailView.vue` | 卡片化：NL 描述 + 诊断 + IR 原文 |
| `src/views/SimulationView.vue` | 三输入框 + 运行按钮 + 4 张结果卡片（NL/轨迹/审计/最终状态）全部 `n-data-table` |
| `src/views/ConfidenceView.vue` | 阈值条 + `n-data-table` + `n-progress` 进度 + 干预按钮（带 `useMessage` 反馈） |
| `src/views/VersionsView.vue` | `n-select` 版本选择 + Diff 渲染 + 结构化 Diff 分组 |
| `src/views/SpecEditorView.vue` | 左右分栏（编辑 / 结果）+ `n-collapse` 折叠 IR |
| `src/views/FaultsView.vue` | 两张 `n-data-table` |
| `src/views/NotFoundView.vue` | `n-result` 404 页面 |
| `src/components/DiagnosticPanel.vue` | 卡片化诊断分组 |
| `src/components/SafetyAlert.vue` | `n-alert` 安全闸门告警 |
| `package.json` | 新增依赖 `naive-ui` |

---

## 四、前后对比

| 方面 | 重构前 | 重构后 |
|---|---|---|
| 全局导航 | 无（各页只有一个「← 概览」返回） | 顶栏品牌 + 水平导航，当前页高亮 |
| 首页信息密度 | 纯文本版号 + 里程碑列表 | 4 张统计卡片 + 标签墙 + 6 个快捷入口卡 |
| 表格 | 手写 `<table>` 无交互 | `n-data-table`，含筛选、loading、空态 |
| 状态标识 | 简单色块 | `n-tag` 语义色（success/warning/error） |
| 进度展示 | 手写 div 宽条 | `n-progress` 统一组件 |
| 反馈交互 | 无 | `n-message` 操作反馈 |
| 空态/错误态 | 纯文字 | `n-alert` / `n-result` / 骨架 spin |

**截图对比**（旧图在本目录，新图在 `after/`）：

- 概览：`01-overview.png` → `after/01-overview.png`
- 自动化列表：`02-automations.png` → `after/02-automations.png`
- 置信度面板：`05-confidence.png` → `after/05-confidence.png`

---

## 五、复验结果（Playwright 无头浏览器实测）

| 页面 | 状态 | 期望内容 | 交互 | 控制台错误 |
|---|---|---|---|---|
| `/overview` | 200 | 全命中 | — | 0 |
| `/automations` | 200 | 全命中 | — | 0 |
| `/automations/case01_day_light` | 200 | 全命中 | — | 0 |
| `/simulation` | 200 | 全命中 | 点「运行仿真」→ 出结果 | 0 |
| `/confidence` | 200 | 全命中 | — | 0 |
| `/versions` | 200 | 全命中 | 点「对比」→ 出 Diff | 0 |
| `/spec-editor` | 200 | 全命中 | 点「编译」→ 出 NL + 诊断 | 0 |
| `/faults` | 200 | 全命中 | — | 0 |
| `/xyz-nope` | 200 | 404 兜底 | — | 0 |

**失败请求：0；缺失文本：0。**

---

## 六、部署记录与踩坑（重要）

**部署流程**
1. 本地 `npm run build`（vite，58.9s，主包 328KB / gzip 108KB）
2. `scp dist → NAS /vol1/1000/docker/autoforge-ui/dist_new`
3. 远端 `mv dist dist_old && mv dist_new dist`（意图原子替换）

**踩坑：`mv` 替换目录导致 Docker bind mount 失效**
- 容器把 `dist` 以 bind mount 方式挂进 `/ui`，**绑定的是目录 inode**；
- `mv dist dist_old` 后新建 `dist`（新 inode），容器内 `/ui` 仍指向**已删除的旧 inode** → 首页与静态资源全部 **500 Internal Server Error**；
- 修复：`docker restart autoforge-api` 让容器按路径重新解析挂载 → 立即恢复 200。

**结论 / 后续规范**
- 更新挂载目录内容时，**应在原地覆盖文件，不要用 `mv` 替换目录本身**；
- 若必须替换目录，则随后**需要一次容器重启**（单次重启安全，避免频繁 churn）。

---

## 七、后续建议（可选）

1. **响应式**：顶栏菜单在窄屏会折叠为「…」，可再补移动端抽屉式侧栏；
2. **暗色模式**：Naive UI 原生支持 `darkTheme`，可加一个顶栏切换开关（一行代码级别）；
3. **`all` / `_all` 别名统一**：沿用验收报告 5.2 的建议；
4. **表格分页/排序**：数据量增大后可开启 `n-data-table` 的 `pagination` / `sorter`。
