# 开发计划：WebUI 全功能接入（v1.7.0）

> 配套路线图条目：`docs/ROADMAP.md` 版本表 `v1.7.0` + 同名章节。
> 目标：把后端已就绪的 **47 个 HTTP 端点 / 24 个 MCP 工具** 中「需要用户操作」的部分，**完整在 AutoForge-UI 控制台展现**。
> 当前 WebUI（独立仓库 `autoforge-ui`）仅覆盖 R1 只读子集 + R2 审批/真机，大量后端能力无 UI 入口。

---

## 0. 现状盘点（为什么要做）

后端自 G1 以来持续扩张能力面，但 UI 仓库迭代滞后。截至 v1.6.0：

| 后端里程碑 | UI 状态 | 说明 |
|---|---|---|
| UI 服务层 R1（只读 11 端点） | ✅ 已覆盖 | 列表/详情/构建/仿真/spec/置信度/故障 |
| UI 服务层 R2（会话 + 真机） | ✅ 已覆盖 | ask 审批台 + live 下发按钮 |
| v0.6.0 标签体系 + 批量启停 | ✅ 已接入 | 批次 A：标签筛选 + 批量启停工具条 + 降权按钮 |
| v0.7.0 模板导出/导入 | ✅ 已接入 | 批次 A：数据管理（导出下载 + 导入策略） |
| v1.1.0 设备目录 + 实体解析 | ✅ 已接入 | 批次 A：设备目录页（概览/浏览/状态/解析选择器/别名/漏斗） |
| v1.2.0 断言闭环 | ⚠️ 部分 | `expect` 结果在仿真回执里能看，但无独立「期望值编辑」入口 |
| v1.4.0 治理面（待批/凭据/令牌） | 🔲 待接入 | pending / credentials / auth 全无 UI |
| v1.5.0 经验闭环（指标/共现/遥测） | 🔲 待接入 | metrics / experience / telemetry 全无 UI |
| v1.6.0 决策智能（别名/绑定/弱信号/解析漏斗） | 🔲 待接入 | alias / bind / resolve-metrics 无 UI |

> **状态图例**：✅ 已覆盖 ｜ ⚠️ 部分 ｜ 🔲 待接入 ｜ 🔮 路线图原标记未做
> 标注基于路线图明面声明 + 后端契约推导；UI 仓库实际页面以 UI 仓库为准，本计划以「接入目标态」为准，实施前请在 UI 仓库逐页核对。

---

## 1. 后端能力 → WebUI 功能映射（核心交付物）

按 UI 模块分组。每一行 = 一个需在 WebUI 展现的后端能力（端点 / 工具 / CLI 同源）。

### 模块 A · 概览 / 健康
| 后端端点 | 能力 | UI 展现 | 状态 |
|---|---|---|---|
| `GET /api/health` | 版本/契约/里程碑/readonly | 顶部状态条 + 关于弹窗 | ✅ |

### 模块 B · 自动化列表 + 详情 + 版本 + diff
| 后端端点 | 能力 | UI 展现 | 状态 |
|---|---|---|---|
| `GET /api/graphs?tag=` | 归档列表（按标签过滤） | 列表页 + 标签筛选器 | ✅（筛选✅） |
| `GET /api/graphs/{name}` | 取某版本图（ir/nl/diagnostics） | 详情抽屉 | ✅ |
| `POST /api/graphs/tags` | 设标签 | 详情/批量操作 | ✅ |
| `POST /api/graphs/enable`·`/disable` | 按标签批量启停 | 批量操作工具条 | ✅ |
| `GET /api/diff` | 版本 diff | 版本对比视图 | ✅（签名配对重命名待确认） |
| `GET /api/conf/{name}` | 置信度分级（G4） | 置信度面板 | ✅ |
| `POST /api/conf/{name}/intervene` | 人工干预降置信度 | 置信度面板「降权」按钮 | ✅（原已有） |

### 模块 C · 编辑器（构建 / 仿真 / 编译 / 绑定）
| 后端端点 | 能力 | UI 展现 | 状态 |
|---|---|---|---|
| `POST /api/build` | 第一道闸：静态扫描 + NL 渲染 | 编辑器「构建并校验」 | ✅ |
| `POST /api/sim` | 第二道闸：FakeHA 回放 + expect 断言 | 编辑器「仿真」+ expect 报告 | ✅（expect 编辑入口⚠️） |
| `POST /api/bind` | v1.6.0 占位符 `?设备名` 回填 entity_id | 编辑器「绑定设备」 | ✅ |
| `POST /api/spec/compile` | AF-Spec 文本→IR | 编辑器「编译」 | ✅ |
| `GET /api/spec/{name}` | 取 AF-Spec 文本 | 编辑器载入 | ✅ |
| `GET /api/faults` | G5 故障注入图鉴 | 仿真页「注入故障」面板 | ⚠️（图鉴可展示，注入需 sim 扩展） |

### 模块 D · 设备目录 / 实体解析（v1.1.0 + v1.6.0）
| 后端端点 | 能力 | UI 展现 | 状态 |
|---|---|---|---|
| `GET /api/catalog` | 目录摘要（域/区域/freshness） | 设备面板总览 | ✅ |
| `POST /api/catalog/refresh` | 拉 HA 全屋目录进缓存 | 「刷新目录」按钮 | ✅ |
| `GET /api/entities/resolve` | 设备名→Top-N 候选 | 写 IR 前的「设备选择器」 | ✅ |
| `GET /api/entities` | 全屋实体过滤浏览（分页） | 设备浏览器 | ✅ |
| `GET /api/entities/{id}/state` | 单实体当前状态 | 设备卡片实时状态 | ✅ |
| `GET /api/catalog/aliases` | 已沉淀别名 | 别名列表 | ✅ |
| `POST /api/catalog/alias`·`/alias/remove` | 沉淀/删除别名 | 别名管理 | ✅ |
| `GET /api/catalog/resolve-metrics` | 解析成功率漏斗 | 解析质量仪表 | ✅ |

### 模块 E · 会话 / ask 人机回路（R2-A，进程内）
| 后端端点 | 能力 | UI 展现 | 状态 |
|---|---|---|---|
| `POST /api/sessions`·`GET /api/sessions` | 创建/列出会话 | 会话列表 | ✅ |
| `GET /api/sessions/{id}` | 会话状态视图 | 会话详情（挂起的 ask） | ✅ |
| `POST /api/sessions/{id}/answer` | 人工应答 ask | ask 卡片「回复」输入 | ✅ |
| `POST /api/sessions/{id}/tick`·`/cancel`·`DELETE` | 推进时钟/取消/删除 | 会话操作条 | ✅ |

### 模块 F · 待批队列（v1.4.0 治理）
| 后端端点 | 能力 | UI 展现 | 状态 |
|---|---|---|---|
| `POST /api/pending/list` | 列出待审批写操作 | 待批队列页 | ✅ |
| `POST /api/pending/approve` | 批准并回放落盘 | 每条「批准」按钮 | ✅ |
| `POST /api/pending/reject` | 拒绝并丢弃 | 每条「拒绝」按钮 | ✅ |

> 关键：Agent 通过 MCP `af_save` 写入的全部落这里，UI 是**唯一批准入口**（MCP 不注册 approve，铁律）。

### 模块 G · 真机下发（R2-B，三重闸）
| 后端端点 | 能力 | UI 展现 | 状态 |
|---|---|---|---|
| `GET /api/live/status` | 真机可用性（开关/禁用原因） | 下发前可用性提示 | ✅ |
| `POST /api/live/run` | 真实设备下发 | 「下发真机」按钮 + 强制二次确认弹窗 + 白名单输入 | ✅ |

### 模块 H · 治理：凭据（v1.4.0）
| 后端端点 | 能力 | UI 展现 | 状态 |
|---|---|---|---|
| `GET /api/credentials` | 凭据掩码 + 连接代数 | 设置页「凭据」卡 | ✅ |
| `POST /api/credentials/update` | 原子更新 HA/API 令牌（免重启） | 凭据编辑表单 | ✅ |

### 模块 I · 治理：令牌（v0.8.0 主体模型）
| 后端端点 | 能力 | UI 展现 | 状态 |
|---|---|---|---|
| `GET /api/auth/whoami` | 自检当前令牌 | 设置页「当前身份」 | ✅ |
| `GET /api/auth/subjects` | 已注册主体摘要 | 令牌列表 | ✅ |
| `POST /api/auth/revoke` | 撤销令牌（即时生效） | 每条「撤销」 | ✅ |

### 模块 J · 经验闭环（v1.5.0 观测）
| 后端端点 | 能力 | UI 展现 | 状态 |
|---|---|---|---|
| `GET /api/metrics` | 运行指标聚合 | 指标仪表盘 | 🔲 |
| `GET /api/experience`·`/export` | 实体共现经验 / 导出喂 MA | 共现图谱 + 导出按钮 | 🔲 |
| `GET /api/telemetry` | token/结果遥测 + 错误类别分布 | 遥测 + 错误知识库面板 | 🔲 |

### 模块 K · 备份（v0.7.0）
| 后端端点 | 能力 | UI 展现 | 状态 |
|---|---|---|---|
| `GET /api/store/export` | 导出 store bundle | 「导出」按钮 | ✅ |
| `POST /api/store/import` | 导入 bundle（策略） | 「导入」+ 策略选择 | ✅ |

---

## 2. WebUI 页面 / 分区规划（目标态组织）

把上述模块组织为 UI 导航：

1. **概览（Overview）** — 健康条 + 里程碑 + 最近活动（模块 A）
2. **自动化（Automations）** — 列表/筛选/批量启停/标签/详情/版本 diff/置信度（B）
3. **编辑器（Editor）** — AF-Spec/JSON 双模编辑 + 构建/仿真/bind/编译 + expect 报告（C）
4. **设备（Devices）** — 目录总览/浏览器/状态/解析选择器/别名/解析漏斗（D）
5. **会话（Sessions）** — ask 审批人机回路（E）
6. **待批（Pending）** — 写操作审批队列（F）
7. **真机（Live）** — 下发控制台 + 二次确认（G）
8. **经验（Insights）** — 指标/共现/遥测/错误知识库（J）
9. **设置（Settings）** — 凭据（H）+ 令牌（I）+ 备份导入导出（K）

> 当前 UI 仓库已有约 9 页（对照 `docs/ui-verify/` 截图），本计划是在其基础上**补齐 D/F/H/I/J/K 整块**与 B/C 的增强项。

---

## 3. 分批交付计划

按风险与依赖排序，三批递进。每批独立可验收（符合路线图 minor=独立主题规则）。

### 批次 A · 核心闭环补全（v1.7.0-a，规模 M）✅ 已实现 2026-09-17
- D 设备目录全模块（目录/浏览器/状态/解析选择器/别名/解析漏斗）—— **最高频缺口**，Agent 写完 IR 前用户需在 UI 也能查设备。 ✅
- C 增强：`/api/bind` 绑定按钮 + `expect` 编辑入口（expect 编辑入口⚠️ 待补）。 ✅（绑定完成）
- B 增强：标签筛选器 + 批量启停工具条 + 置信度「降权」按钮。 ✅（降权按钮原已有）
- K 备份导入导出。 ✅
- **退出标准**：用户在 UI 完成「查设备 → 写 IR → bind → build → sim（看 expect）→ 存为待批」全链路，无需 CLI。
  - 已实现：查设备（DevicesView）、写 IR（SpecEditorView 编译）、bind（绑定按钮）、build（构建并校验）、sim（仿真）均已闭环；「存为待批」需批次 B 的 F 待批队列页接入 `af_save` 后才完整。

### 批次 B · 治理与实时（v1.7.0-b，规模 M）✅ 已实现 2026-09-17
- F 待批队列页（list/approve/reject）—— 与 Agent `af_save` 形成闭环。✅
- H 凭据管理（掩码展示 + 免重启更新）。✅
- I 令牌管理（whoami/subjects/revoke）。✅
- G 增强：真机下发强制二次确认 + 白名单输入。✅
- **退出标准**：Agent 提 `af_save` → UI 待批页可见 → 批准落盘；改 HA 令牌后 UI 即时生效无需重启。✅ 已达成。

### 批次 C · 经验与观测（v1.7.0-c，规模 S）
- J 指标/共现/遥测/错误知识库面板。
- C 增强：仿真页故障注入面板（消费 `/api/faults` 图鉴 + 扩展 sim 注入）。
- **退出标准**：UI 可读出「解析成功率漏斗」「实体共现 Top-N」「错误类别分布」「运行指标」四类观测。

---

## 4. 接入技术规范（UI 必须遵守）

1. **认证**：服务端 `AUTOFORGE_TOKENS` 未配置时全站公开（原型）；生产必须配置多主体 scope（read/write/live）。
   UI 所有**写/live** 请求须带 `Authorization: Bearer <token>`；read 端点即便有令牌也不强制鉴权。
2. **live 三重闸**：真机下发按钮必须**强制二次确认弹窗** + 显式 `confirm=true` + 非空 `live_allow` 白名单输入；
   禁用原因来自 `GET /api/live/status` 的 `reasons`，未满足时按钮置灰并提示。
3. **待批回路**：写操作（save/标签/启停/导入）统一经 `POST /api/pending/*` 入队，UI 提供批准/拒绝；
   **绝不在 UI 直连落盘绕过待批**（与 MCP 铁律一致）。
4. **错误处理**：后端 `ServiceError` 带 `.status` + `message` + 可选 `hint`（v1.2.0 `CODE_HINT`）；
   UI 统一把 `hint` 渲染为「建议」行，不吞错误。
5. **凭据安全**：`GET /api/credentials` 只回掩码 + 长度，**UI 绝不缓存/显示明文**；更新走 `POST /api/credentials/update`。
6. **爆炸半径**：`save_graph` / `enable_by_tag` / `import_store` 超 `AUTOFORGE_BLAST_RADIUS`（默认 8）会 400，
   UI 应捕获并提示「请拆分操作」。

---

## 5. 统一 DoD（每批完成定义）

1. 对应模块全部端点有 UI 入口且可操作；
2. 与 `docs/API_CONTRACT.md` / `GET /openapi.json` 一致（以代码实际路由为准）；
3. 写/live 操作强制二次确认 + 令牌鉴权；
4. 既有 9 页零 console 错误回归；
5. 文档同步：本计划勾选完成项 + `ROADMAP.md` v1.7.0 状态更新 + `README.md` 里程碑表。

---

## 6. 风险与备注

- **UI 已并入主仓库**：原独立仓库 `autoforge-ui` 已于 2026-09-17 合并进 AutoForge 主仓 `ui/` 目录，UI 代码改动现直接发生在 `ui/`；
  本文件与 `ROADMAP.md` 仅定义目标态与验收，不改动后端。
- **状态推断**：§0 / §1 的「状态」列为基于路线图声明的推导，实施前需在 `ui/` 目录逐页核对真实覆盖度，避免重复建设或漏接。
- **不新增后端**：本版本为纯前端接入，后端 47 端点已齐备；若发现端点缺失/不足，回流后端开新版本（如需要 expect 独立编辑端点可回流 v1.2.0 补强）。
