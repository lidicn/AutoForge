# AutoForge 演进路线图与开发计划（v3 · DCD 版）

> 出品：关键决策部（Directorate of Critical Decisions · DCD）
> 文档日期：2026-09-28
> 基准：v2.0 四里程碑（M1-M4）已提交未投产；`src/autoforge` 84 个 .py / 约 2.9 万行 / tests 108 文件
> 替代关系：不替代 `演进路线图_AF_v2.1+.md`（保留为初稿与历史记录），本文档为现行版
> 决策依据：`E:\NAS\关键决策部\decisions\20260928-AutoForge-v2.1设计难题A-F-决策.md`（A-F 全部已裁定，另新增 G/H）
> 铁律沿用：IR 唯一真相锚、测试采信台真跑、文档阶段不部署、零 LLM 红线

---

## 〇、与初稿（v2.1+ 初版）的关键差异

1. **初稿 §1.2 的"两轨人工同步"判断被源码推翻**：FakeHA 与 HiFi 本就共享 `fake.py:135` 唯一效果真值表，不存在漂移源。F2 从"统一真值源"缩减为"双轨对拍测试"（详见决策 A）。
2. **F3 的"UNVERIFIABLE 豁免"降级为兜底**：大部分不可推导动作（toggle/set_temperature/set_cover_position/volume_set）实际可推导，扩 resolver 表约 20 行；真豁免走 EXEMPT  verdict + 人审，不破坏诚实铁律（决策 B）。
3. **新增 v2.0.1 投产收口版**：103 个未提交文件、P0-9 鉴权 fail-closed、镜像未烘、token 硬编码——这些不解决，v2.1 做得越多欠账越厚（决策 H）。
4. **新增 UI 收敛决策**：ForgeSight 两个并行实现（ui-user / ui-user-mimo）冻结其一（决策 G）。
5. **每个版本挂决策门**：路线图是假设不是承诺，取消一个版本和发布它同样是成功的决策。

---

## 一、版本总览

| 版本 | 代号 | 核心命题 | 工期 | 决策门 |
|------|------|---------|------|--------|
| **v2.0.1** | 投产收口 | 硬阻塞清零：未提交文件、鉴权 fail-closed、镜像、token、文档鲜度 | 2-3 天 | 决策 H |
| **v2.1** | 仿真保真与可信闭环 | 补域（P1 优先）、shadow 推导扩展、契约冻结、premiere 落盘 | 5-7 天 | 决策 A/B/C/F |
| **v2.2** | 安全能力补齐 | 通用 undo、试演期自动暂停接执行闸、band 归一 | 4-5 天 | 决策 E/F，门 G2 |
| **v2.3** | 复合编排 | IR schema 扩 `group` 节点（先修版本机制） | 5-8 天 | 决策 D，门 G3 |
| **v2.4** | 经验闭环 | experience 接 executor/predict、predict 与 G4 联动、evo 真 IR | 3-4 天 | 门 G4 |
| **v2.5** | NL 双向保真 | NL→IR 结构化、词表单一真源 | 3-5 天 | 门 G5 |

### 依赖关系

```
v2.0.1 投产收口 ──► 一切（103 个未提交文件不清零，后续版本的"测试真跑"无意义）
v2.1
  ├─ 契约冻结（af-stage/1）────────► v2.4 经验闭环 / v2.3 复合编排都消费 build/simulate 返回
  ├─ shadow 推导扩展 ──────────────► v2.2 undo 的 pre_snapshot 复用同一状态解析基础设施
  └─ premiere 落盘 ────────────────► v2.2 自动暂停的前置（内存态接执行闸无意义）
v2.2 undo / band 归一 ─────────────► v2.3 复合部署原子性依赖 undo 能力
v2.3 复合编排 ─────────────────────► 依赖 IR 版本机制修复（决策 D 前置项）
```

---

## 二、v2.0.1 投产收口（最先做，无决策争议）

| # | 任务 | 依据 | 工期 |
|---|------|------|------|
| 0.1 | 103 个未提交文件清点提交（或明确删除） | D 组交接回复；铁律"测试真跑"的前提 | 1 天 |
| 0.2 | P0-9 鉴权 fail-closed 修复 | 投产硬阻塞 | 0.5-1 天 |
| 0.3 | token 移出 env_file，走 secret 管理 | 安全硬债（关联 R-56） | 0.5 天 |
| 0.4 | api 镜像烘入 + 两容器基线分叉收口（sync 52 vs api 0 同源：部署树 vs 镜像烘入） | ADM-路线图 bug#1 | 0.5 天 |
| 0.5 | README/KICKOFF 鲜度修复（488→529 passed，版本号 v1.7.1） | 交接文档已记，入口文档未跟上 | 0.5 天 |
| 0.6 | ui-user 冻结归档（决策 G） | ForgeSight 主线收敛到 ui-user-mimo | 0.5 天 |

**验收**：投产检查单全绿；`git status` 干净；gates.sh 退出码 0；两容器基线读数一致。

---

## 三、v2.1 仿真保真与可信闭环

### 任务卡

| # | 任务 | 工期 | 要点 |
|---|------|------|------|
| 1.1 | 补域 P1：scene/script 间接触发展开 + notify 标"副作用不可观测" | 1 天 | 决策 A 清单；不许超清单建模（✅ 已交付：IR `effects` 展开 + notify/scene 域分层 → 诚实报告 non_simulable 收缩；附带修复 honest_report 摊平 automations 内嵌 expect 项） |
| 1.2 | 双轨对拍测试：同一组自动化在 FakeHA/HiFi 下仿真结果一致性断言 | 1 天 | 决策 A；已知分歧点：sun 触发器（fake.py:52 vs HiFi 真实太阳几何），对拍时白名单豁免（✅ 已交付：`simulate_track(track)` 双轨 + `compare_dual_track` 对拍 + sun 白名单；对拍实测暴露 `fake.py:service_effect` 对 `climate.set_temperature` 强制返回 `cool` 的真实漂移，已修正为保留当前开关机态使两轨一致） |
| 1.3 | shadow 期望态推导扩表：toggle（前态取反）、set_temperature/set_cover_position/volume_set（参数即期望） | 0.5 天 | 决策 B；`af_shadow.py:168-180` 扩约 20 行（✅ 已交付） |
| 1.4 | EXEMPT 豁免通道：verdict 枚举 + streak 语义 + 人审入口 + 审计事件 + 诚实报告 exempted 分区 | 1 天 | 决策 B；EXEMPT 永不计入 MATCHED streak（✅ 已交付；诚实报告 exempted 分区已就位，喂入由 1.1 标 notify / 1.8 af_watch 接） |
| 1.5 | shadow_log 落盘 + 重启回放 | 0.5 天 | 抄 `af_version.py:743-758` 原子替换模式（✅ 已交付） |
| 1.6 | 契约冻结：build/simulate 返回加 `"schema":"af-stage/1"`，issues_from 严格模式 + 一版过渡期容错 | 1 天 | 决策 C；消费点仅 3 处（✅ 已交付） |
| 1.7 | premiere 落盘（TrialStore + PremiereStore JSON 写透 + 启动 load） | 0.5 天 | 决策 F 前置（✅ 已交付） |
| 1.8 | af_watch 聚合层雏形：订阅 shadow/canary/conflict 事件，回灌诚实报告 `verified_in_prod` 分区 | 1.5 天 | 初稿 F4；注意运行态/仿真态实体标识符对齐（✅ 已交付；shadow 落点已接，canary/conflict 同款一行喂入即可） |
| 1.9 | 全量回归 + 采信台真跑 + 评审窗 | 0.5 天 | 铁律 2、3（✅ 已交付；采集台真跑 776 passed / 5 既有环境性失败与 v2.1 无关） |

### 测试补强（本版必须补的债）

扫描发现 **shadow / canary / premiere / orchestrator 无专项测试文件**（orchestrator 仅靠 dispatch/api 间接覆盖）。本版任务 1.3/1.4/1.7 全部触及这三个模块，**每个任务交付时必须带上对应模块的首个专项测试文件**——不为覆盖率，为"改过的模块必须有网"。

### 验收标准

1. 诚实报告 `non_simulable` 区收缩（scene/script/notify 不再计入）；
2. 含 toggle/set_temperature 的自动化可凭 `streak_to_promote=3` 正常转正；
3. EXEMPT 自动化在诚实报告中单列，不冒充 verified；
4. build/simulate 返回带 `af-stage/1`，旧消费方过渡期无报错；
5. premiere 试演态重启不丢；
6. 新增 3 个专项测试文件；采信台实跑全绿。

---

## 四、v2.2 安全能力补齐（过 G2）

| # | 任务 | 工期 | 要点 |
|---|------|------|------|
| 2.1 | `af_undo.py`：pre_snapshot（状态+属性）+ DOMAIN_SETTER 参数化回放 + fail-closed | 2 天 | 决策 E；窗口默认 60s 可配 0-300s；undo 走同级 approve band，风险域二次确认 |
| 2.2 | 试演期自动暂停接执行闸：af_apply 检查 Trial.paused | 0.5 天 | 决策 F 分级：低风险二次失败暂停，高风险首次即暂停；恢复必须人工 |
| 2.3 | band 归一：conflict/canary/conf 三套优先级概念合并为单一真值源 | 1.5 天 | 先出术语对照表再动代码；与 G4 auto/shadow/ask 联动 |
| 2.4 | CLI `forge undo <deploy_id>` + WebUI 撤销按钮 | 0.5 天 | ForgeSight（ui-user-mimo）侧 |
| 2.5 | 回归 + 验收 | 0.5 天 | undo 验收：light.turn_on 下发后 60s 内恢复前态（含亮度属性）；climate/cover 同理 |

**G2 决策门问题**：v2.1 投产后 shadow/canary 的真实告警量是多少？若 undo 无真实需求场景，2.1 可缩减为只做属性回放增强。

---

## 五、v2.3 复合编排（过 G3，最贵的一项，允许整体取消）

**前置（无论 G3 结果如何都该做，0.5 天）**：IR 版本机制修复——`ir.schema.json:10` 的 `const "0.2.1"` 改为枚举 + `af_ir/models.py:45` + orchestrator 三处同步问题；新增字段前向兼容策略（决策 D）。不修这个，IR 永远不能演进。

主体（G3 通过才做）：

1. IR schema 新增 `group` 节点 kind（`mode: sequence|parallel`，`children` 递归），ir_version 升 0.3.0；
2. 编译/仿真/NL 渲染/诚实报告对 group 的支持——四段管线全部要过，这是成本大头；
3. 冲突域跨子图合并；复合部署原子性（全成功或全回滚，依赖 v2.2 undo）。

**G3 决策门问题**：统计 compose 会话中真实多意图请求占比，<5% 则整体推迟。IR→NL→IR 往返保真测试是验收硬门。

---

## 六、v2.4 / v2.5 预登记（不展开）

- **v2.4 经验闭环**：experience 接 executor 实体解析 + predict 先验（初稿 F10/F11/F12）。G4 门问题：v2.1 后 experience 库积累了多少条有效记录？数据不够时闭环无意义。
- **v2.5 NL 双向保真**：NL→IR 结构化（复用 M3 AskSpec 元数据反向生成）+ 词表单一真源。方向性裁定先给：**接受"IR 为唯一真源，NL 仅视图"**——这与 IR 铁律一致，NL→IR 解析的任何歧义都必须落到 Ask 追问，不许静默猜测。G5 门问题：真实 NL 输入中超出 `_ACTION_VERBS` 覆盖的比例。

---

## 七、贯穿纪律（在初稿四条铁律上增补）

5. **文档鲜度也是验收项**：每次发版必须同步 README/KICKOFF/交接总览的数字与版本号（v2.0.1 任务 0.5 固化此习惯）。
6. **改过的模块必须有测试网**：无专项测试的模块（shadow/canary/premiere/orchestrator）首次改动时补建（v2.1 §三）。
7. **决策门记录**：每个版本启动/取消的裁定写入 `E:\NAS\关键决策部\decisions\`，不接受口头决定。

---

## 八、一页总结

- **先收口再演进**：v2.0.1（103 个未提交文件 + 鉴权 + 镜像 + token + 文档鲜度 + UI 收敛）是一切的前提，2-3 天。
- **初稿最值得修正的判断**：双轨真值源已存在（别做架构动作）；UNVERIFIABLE 大多可推导（别急着开豁免口子）；契约冻结最便宜杠杆最大（v2.1 就做）。
- **最贵项目设了取消门**：复合编排（v2.3）先修 IR 版本机制，主体过 G3 需求门，<5% 多意图占比就推迟。
- **治理哲学的一次明确表态**：试演期从"只统计不封禁"改为"失败即暂停、恢复必须人工"——暂停可逆就不是粗暴，放任失败自动化继续触发才是失职。

—— 关键决策部 · Directorate of Critical Decisions (DCD)
