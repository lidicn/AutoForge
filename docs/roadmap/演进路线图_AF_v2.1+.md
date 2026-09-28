# AutoForge 演进路线图（v2.1+）

> 文档性质：基于 **2026-09-27 真实代码盘点**的初稿，已融入 **【关键决策部 DCD】2026-09-28 决策 A–H**（`E:/NAS/关键决策部/decisions/20260928-AutoForge-v2.1设计难题A-F-决策.md`）。
> 用途：细分功能 × 小版本；设计难题已全部裁定，本文件为执行基线。
> 铁律沿用：`docs/design/v2/实施路线图_v2.0.md` 的 IR 唯一真相锚、测试由采信台真跑、文档阶段不部署、零 LLM 红线。
> **决策状态**：§4 原 6 项难题已由 DCD 裁定（A–F），新增 G（UI 目录收敛）、H（v2.0.1 投产收口版先于 v2.1）。

---

## §0 版本基线

**v2.0 业务设计四里程碑（M1–M4）代码 + 测试已全部提交 git**：
- M3 结构化 Ask `0ecced8` + `3f30f3d`；M1 首演码 `d464f6b`；M4 诚实报告 `0d3fd9e`；M2 forbid `2ea0f8c`
- 按 `实施路线图_v2.0.md` §4.3 铁律**未部署、未过评审窗**（文档阶段不部署）。

**投产路线图 `roadmap/ADM-路线图_v2.0投产.md` 剩余项（待执行）**：两容器基线分叉收口 🔄50%、投产准备 ⏳0%（部署+回滚步骤文档）、09-29 全量回归、09-30 硬墙交付；延后项 R-56/R-61。

**决策 H 裁定**：v2.1 之前必须先收口 **v2.0.1 投产收口版**（见 §2.0），只做阻塞项 + 文档鲜度修复，工期 2–3 天，完成后才启动 v2.1。

---

## §1 能力盘点（DCD 已核实，附裁定）

### §1.1 已有且较完整
| 能力 | 模块 | 代码证据 |
|---|---|---|
| 仿真双轨（FakeHA + HighFidelityHA） | `af_vhass/` | `fake.py:66` `high_fidelity.py:110` |
| 影子只读回放 + 转正 | `af_shadow.py` | `ShadowRunner.install:281` `promote_if_ready:406` |
| 编译/仿真自修正闭环 | `af_closedloop/` | `loop.py:112` `fixers.py:144` |
| IR 快照 / diff / 回滚 / tag | `af_version.py` | `snapshot:523` `rollback:551` |
| IR→中文渲染 + 覆盖校验 | `af_nl.py` | `render_automation:150` |
| 金丝雀 + 冲突仲裁闭环 | `af_canary*` `af_conflict*` | `canary.py:140` `conflict.py:131` |
| 反馈权重中枢 | `af_feedback.py` | `emit:212` |
| 首演码 + 试演期 | `af_premiere.py` | `issue/consume:101/125` `enter_trial:180` |

### §1.2 已有雏形但需强化
- **仿真保真度**：已建模 ~8 域，其余 unmodeled（`fake.py:179`）。**决策 A 定达标域清单**：P1 scene/script/notify；P2 vacuum/valve/water_heater；P3 等需求。
- **Shadow 转正**：`toggle/set_cover_position` 等 UNVERIFIABLE 永不转正（`af_shadow.py:185`）。**决策 B 裁定：先做期望态推导扩展，真不可推导走 EXEMPT 人审，不破坏诚实铁律。**
- **Premiere**：内存-only（`af_premiere.py:252`），试演期"只统计不封禁"。**决策 F 裁定：改"失败即暂停"分级执行。**
- **NL→IR**：启发式 + LLM（`af_orchestrator.py:847`）。
- **Experience / Predict**：`af_experience.py` 无消费方；`af_predict.py` 与 G4 未联动（**决策 C 将统一 stage schema**）。

### §1.3 真实净缺口（无中生有，需新建）
1. **多意图 / 复合编排**：`af_orchestrator` 无 parallel/sequence/subgraph 复合原语（`af_orchestrator.py:804`）。**决策 D 裁定：扩展 IR `group` 容器节点（v2.3，过需求决策门）。**
2. **下发后通用撤销 / 设备态回滚**：除 canary on/off 外无 undo（检索命中 0）。**决策 E 裁定：做，60s 窗口 + 风险域二次确认 + fail-closed。**
3. **预测触发未联动**（决策 C 修 stage schema 后联动）。
4. **仿真补域**（决策 A 域清单）。
5. **Premiere 持久化 + 试演期封禁**（决策 F）。

### §1.4 设计难题状态
原 §4 的 A–F **已由 DCD 2026-09-28 裁定**（结论见 §4）；新增 G（UI 目录收敛）、H（v2.0.1 收口版）。

---

## §2 小版本规划（细分功能矩阵，已融入 DCD 裁定）

### §2.0 v2.0.1 投产收口版（决策 H，先于 v2.1）
**目标**：收口 v2.0 投产硬阻塞，让"测试由采信台真跑"的铁律重新成立（跑的不是真相 = 铁律失效）。工期 2–3 天。
- **F0.1 阻塞项收口**：P0-9 鉴权 fail-closed、103 个未提交文件（优先，否则测试失真）、镜像未烘、token 在 env_file 迁移。
- **F0.2 文档鲜度修复**：README/KICKOFF 仍写 488 passed，实际 v1.7.1 已 529 passed；同步 ADM 路线图剩余项。
- **验收**：全量回归 gate 绿；未提交文件清零；投产步骤 + 回滚步骤文档齐备；09-30 硬墙可签。
- 决策结论（H）：**v2.0.1 完成前不启动任何 v2.1 功能开发。**

### v2.1 仿真保真与真实闭环强化（对应"最想加的 af_replay + af_watch"）
**目标**：收缩 M4 诚实报告 `non_simulable` 区；影子验证 + 运行时监护做成可信闭环；并落地决策 C 阶段契约。

- **F1 仿真底座补域建模** `[新建域 SM]`
  - 现状：~8 域已建模，其余 unmodeled（`fake.py:179`）。
  - 涉及：`af_vhass/device_sm.py`（`SM_REGISTRY:402`）、`fake.py`（`SERVICE_STATE:136` `DYNAMIC_SERVICES:163`）、`high_fidelity.py`。
  - 细分（按决策 A 达标域清单）：① **P1** `scene.turn_on`/`script.turn_on` 间接触发展开 + `notify.*` 标"副作用不可观测"；② **P2** vacuum/valve/water_heater 最小状态机；③ **P3** alarm_control_panel/humidifier/button/input_* 等真实需求出现再做（**不许为凑数字建模**）。
  - 验收：P1 域完成并单测；诚实报告 `non_simulable` 区下降；P2/P3 按需求排期。
  - 决策结论（A）：保真度达标线 = P1 必做、P2 按需、P3 等需求；**不追求全域建模**。

- **F2 双轨对拍测试（防漂移，非架构动作）** `[决策 A 修正]`
  - 现状：原"单一真值源 + 切 HiFi 默认"前提错误——`SERVICE_STATE`（`fake.py:135`）本就是单一真值源，HiFi 仅在 7 域加增强层、未建模域自动回落（`device_sm.py:100-106` `_fallback`）。
  - 涉及：`af_vhass/` 双轨。
  - 细分：① **保持 FakeHA 默认、HiFi opt-in**（`AUTOFORGE_VHASS_HIFI` 默认关）；② 新增"双轨对拍测试"防止 `SERVICE_STATE` 与 `SM_REGISTRY` 漂移（无需冻结真值源，加测试即可）。
  - 验收：双轨对拍测试通过；HiFi 切默认 = 否（切默认需全量断言重写 + 太阳几何语义分歧，不在 v2.1）。
  - 决策结论（A）：前提错误、问题比预想小；不切默认、不冻结真值源。

- **F3 Shadow 期望态推导扩展 + EXEMPT 人审** `[强化，对应 af_replay，决策 B]`
  - 现状：`af_shadow.py:185` `DEFAULT_EFFECTS` 仅 8 动作；toggle/set_cover_position 等 UNVERIFIABLE 永不转正。
  - 涉及：`af_shadow.py`（`ShadowRunner` `DefaultExpectedStateResolver:185` `shadow_log.json:151`）。
  - 细分（按决策 B）：① **期望态推导扩展**（约 20 行）：toggle=前态取反、set_temperature/set_cover_position/volume_set=参数值，与现有 `set_hvac_mode/set_state(@params)` 同模式；② **新 verdict `EXEMPT`**（与 MATCHED/MISMATCH/UNVERIFIABLE 并列，**不计入 streak**）；③ 真不可推导动作走 EXEMPT 人审通道（与 approve 同级权限、附理由、写审计）；④ 诚实报告新增 `exempted` 分区单独展示（EXEMPT ≠ VERIFIED，诚实铁律 `af_expect.py:238-271` 毫发无损）；⑤ shadow_log 落盘持久化 + 重启回放。
  - 验收：含 toggle 自动化连续命中 `streak_to_promote=3` 可转正；play_media 类走 EXEMPT 人审且诚实报告显式标 exempted；shadow 记录跨重启不丢。
  - 决策结论（B）：**不许"人工豁免转正"**；先做推导扩展，真不可推导走 EXEMPT 人审，豁免不冒充验证。

- **F4 运行时监护聚合（af_watch 雏形）** `[新建/强化]`
  - 现状：shadow compare + canary 观察期 + conflict_audit 分散，无统一聚合层。
  - 涉及：`af_shadow.py:353` `af_canary_supervisor.py:133` `af_conflict_audit.py:161` `af_live.py:256` `af_expect.py`。
  - 细分：① 新建 `af_watch.py` 聚合层，按 IR 维度统计 real-verified/real-failed/unmodeled-in-prod；② 回灌 M4 诚实报告（新增 `verified_in_prod` 分区/脚注）；③ WebUI 监护视图。
  - 验收：上线自动化真实 HA 跑 24h 后，诚实报告能显示"仿真 non_simulable 项 X，其中 Y 真实验证通过、Z 真实失败"。
  - 设计困难（非决策项）：运行态/仿真态标识符对齐；真实状态采集隐私边界。

- **F5 阶段契约冻结（决策 C，v2.1 必做）** `[契约]`
  - 现状：`issues_from()`（`loop.py:266-302`）多形状兜底解析，掩盖漂移（注释自承"容错解析"）。
  - 涉及：`af_closedloop/loop.py` `af_orchestrator.py:2300-2315`（消费点 3 处）。
  - 细分：① build/simulate 返回加 `"schema":"af-stage/1"` 版本字段；② `issues_from` 改严格模式（无版本→降级警告+容错过渡，有版本→严格校验）；③ 消费点集中（约 1 天）。
  - 验收：stage 返回带 schema 字段；strict 模式对缺字段告警但不崩；3 处消费点通过。
  - 决策结论（C）：**冻结，v2.1 就做**——6 项里成本最低、杠杆最高。

### v2.2 意图生命周期与安全红线
- **F6 Premiere 持久化 + 试演期失败即暂停** `[决策 F]`
  - 现状：内存-only（`af_premiere.py:252`）；试演期 assert 失败仅 `pause_and_notify:206`，但 paused 未接执行闸（`af_premiere.py:221-235`）。
  - 涉及：`af_premiere.py` `TrialStore:180` `af_apply.py`（`enter_trial:143`）。
  - 细分（按决策 F：改"只统计不封禁"为"失败即暂停"，分级）：① 两 Store 加 JSON 写透（抄 `af_version.py:743-758` 原子替换 + 启动 load）；② paused 状态接进 `af_apply` 执行闸；③ **分级**：低风险 band 首次失败→通知、24h 内二次→暂停；高风险 band 首次失败→立即暂停+通知；④ 恢复必须人工（WebUI/CLI），不许自动恢复。
  - 验收：试演期失败的真实自动化在生产环境被拦截（不再继续触发）；高风险首败即暂停；恢复仅人工。
  - 决策结论（F）：治理正确答案 = "暂停可逆、恢复要人"，非"不封禁"。
  - **PR 序（DCD 强制）**：落盘持久化（细案 §4 PR 2.1）先于接执行闸（2.2）合入——premiere 落盘是自动暂停前置，必须先合入。

- **F7 下发后通用撤销 / 设备态回滚** `[新建，决策 E]` ✅ **已交付（2026-09-28）**
  - 现状（交付前）：canary on/off 回滚丢属性（`canary.py:61`），`af_version` 回滚纯 IR 级，两者正交 → undo 需新建。
  - 涉及：`af_apply.py` `af_live.py` `af_canary.py` `af_version.py`。
  - 交付：① `CanaryResult.pre_states` 扩成 `pre_snapshot`（状态+属性，Snapshot 已支持 attributes）；② 新增 `af_undo.py` 的 `DOMAIN_SETTER` 映射：light→turn_on(带 brightness/color_temp)、switch/fan/lock→on-off、cover→set_cover_position、climate→set_temperature(带 hvac_mode) 参数化回放；③ 不可映射动作 **fail-closed**（回滚不了就告警跳过，绝不瞎滚，沿用决策 E）；④ undo 走下发同级 approve band，risk 域（climate/cover/lock/fan/vacuum）二次确认 `--confirm`；⑤ 窗口默认 60s（AUTOFORGE_UNDO_WINDOW_S 可配 0–300s），CLI `forge undo <deploy_id>` + `--live --undo` 自动落盘 pre-snapshot（落 `.forge/undo_log.json`）；HAAdapter.call 真实下发前钩子 `undo_recorder` 捕获首拍、fail-closed 不影响下发。
  - 验收（tests/test_undo.py + tests/unit/test_p1_10_canary_real_snapshot.py 共 48 项）：light 含亮度参数化恢复；climate/cover 同；不可映射/状态未知跳过不崩；60s 过期拒绝；风险域缺 confirm 拒绝；全量离线门 1215 passed / 51 skipped 绿。
  - 残留（非阻塞）：WebUI 撤销按钮（CLI 已就绪，WebUI 按钮待接入，决策 E ⑤ 余「WebUI 按钮」一项）；`forge undo` 真机下发走真实 HA，仅用户显式触发。
  - 决策结论（E）：**做，但不叫"安全红线"——常规安全能力**；60s 窗口 + 风险域二次确认 + fail-closed。

- **F8 冲突仲裁 / canary 默认开启与 band 归一** `[强化]` ✅ **部分交付（2026-09-28）：① band 单一真值源 + ③ G4 联动已落地；② 产销默认开启属部署配置待投产步骤开启**
  - 现状（交付前）：conflict/canary/conf 三套 band/priority 并存（`af_conf.band` `canary` `conflict.py:131`），默认 OFF。
  - 涉及：`af_conflict*.py` `af_canary*.py` `af_conf.py`。
  - 细分：① 统一"优先级/分级"单一真值源（与决策 C stage schema 协同）；② 投产默认开启冲突仲裁；③ 与 G4 auto/shadow/ask 联动。
  - 决策结论（C 协同）：stage schema 冻结后，band 语义以统一 schema 为准，避免三套漂移。
  - 交付：① `af_conf` 新增 `BAND_PRIORITY / PASSIVE_BANDS / CONFIRM_REQUIRED_BANDS` 单一真值源，`af_conflict_runtime.ConflictService.dispatch` 改用统一 band 判定（不再硬编码 `"shadow"`）；③ **ask 域防御纵深**——冲突仲裁拒绝 ask-band 自动下发（`ask_band_requires_confirmation`，G4 联动，G2 编译期已拦截写设备此处兜底），shadow 仍只读旁路；canary 在 live/观察期路径本就默认开启（`af_executor`/`af_runtime` 已挂 `CanarySupervisor`）。② 投产默认开启属**部署配置**：生产经 env_file 注入 `AUTOFORGE_CONFLICT_ARBITER=enforce`（或 `observe` 观察期）开启；按"文档阶段不部署"铁律，代码默认 OFF 以保持离线测试门绿、不破坏"测试由采信台真跑"。
  - 验收：tests/test_conflict_band.py（band 源 + shadow 旁路 + ask 拒绝自动下发 + observe 放行 + auto 正常仲裁）全过；全量离线门 **1220 passed / 51 skipped 绿**。

### v2.3 复合编排（真正新建，决策 D）
- **F9 多意图 / 复合 IR `group` 容器节点** `[新建，决策 D → DCD 裁定方案 B]` 🟡 **执行中（2026-09-28）**：DCD 裁定撤销决策门（门真值在本 ADM 生态结构性不可采集，见 `decisions/20260928-AutoForge-F9-group决策门-裁定.md`），group 按架构价值直接建、并入 F10。Slice1（IR 基础）已交付：schema 加 `group` kind+`children`+`ir_version "0.3.0"`、`additionalProperties` 前向兼容（字段白名单+未知字段忽略）、`af_ir` 模型、`af_nl` 渲染；全量离线门绿（1212）。Slice2（编排 build/simulate + `af_apply` 原子部署/单 ref 回滚/组合冲突预检=F10②）待做。
  - 现状：`af_orchestrator` 无 parallel/sequence/subgraph（`af_orchestrator.py:804` `"parallel"` 仅单条 mode）。
  - 涉及：`af_ir/schema/ir.schema.json` `af_ir/models.py:45` `af_orchestrator.py:2057`（compose）。
  - 细分（按决策 D）：① **扩展 IR schema 新增 `group` 容器节点**（字段 `mode: sequence|parallel`、`children:[节点]`），**不建新 DSL、不做独立图组合层**（复用"一切可编译/仿真/渲染回 NL"管线，唯一真相锚）；② **前置修复（方向已校准）**：ir.schema.json 顶层**当前已是** `additionalProperties:false`，该限制会致旧校验器硬拒含 group 的新图——前置工作是**放宽它**（改 `true` 或新增字段白名单 + 未知字段忽略策略），同步 `ir_version` 由 `const "0.2.1"` 改枚举 `["0.2.1","0.3.0"]`（否则灰度期新图被旧校验器拒）；③ 编译/仿真/下发对 group 支持；④ 冲突域跨子图合并。
  - 验收：两条有依赖自动化编排为含 group 的单 IR，simulate 正确演化，下发按依赖顺序；嵌套 group 支持子图。
  - 决策结论（D）：**扩展 IR schema，不建新 DSL**；v2.3 排期 + **需求决策门**：统计 compose 会话多意图请求占比 **<5% 则 v2.3 整体推迟**（最贵一项，不许为架构美感做）。
  - **前置交付（2026-09-28，安全切片）**：
    - **ir_version 枚举化**：schema `ir_version` 由 `const "0.2.1"` 改为 `enum ["0.2.1"]`（灰度兼容，单一真值源 `af_ir.SUPPORTED_IR_VERSIONS` + `is_supported_ir_version`）；group 节点落地时在此追加 `"0.3.0"` 即可，无需改校验逻辑。
    - **决策门数据采集方案就位**：`af_draft.draft_intent` 新增可选 `session_id`，接入 `ComposeMetrics`（进程级多意图会话计数，`compose_metrics_summary()` 暴露 `sessions/multi_intent_sessions/multi_intent_ratio`）；不传 `session_id` 时零采样。生产在 compose/LLM 层对同一用户请求传稳定 `session_id` 即可评估门。
    - **生产接线就位（2026-09-28）**：`af_mcp._t_draft`（LLM/agent 唯一的 draft 工具入口）已把 `args.get("session_id")` 透传给 `draft_intent`；draft 工具描述已告知 agent「同一用户 compose 请求内多次调用传相同 `session_id`」。工具 inputSchema 无 `additionalProperties:false`，透传不被拒。不传则零采样（默认无侵入）。至此决策门采集闭环完整：生产只需在单次用户请求内用稳定 `session_id` 聚合多次 draft 调用，即可由 `compose_metrics_summary()` 评估多意图占比。
    - **未做（受决策门约束→已撤销）**：原「决策门 <5% 则推迟」经 DCD 裁定撤销（门真值结构性不可采集）；`group` 节点本体按架构价值直接建。Slice1 已交付 `additionalProperties` 前向兼容 + schema group+children + `ir_version "0.3.0"` + 模型/NL；`group` 节点本体（编排/仿真/原子部署/组合冲突预检）归 Slice2（并入 F10②）。
  - 验收（前置）：tests/test_af_ir_version.py（schema 枚举同源 / 非法版本被拒）、tests/test_af_draft_compose.py（多意图占比统计 / 无 session_id 零采样）全过。

- **F10 复合部署与跨自动化一致性** `[新建]`
  - 现状：单 IR 部署，`af_version.wrap_deployer` 单文件快照（`af_version.py:657`）。
  - 涉及：`af_version.py` `af_apply.py` `af_pending.py`。
  - 细分：① 复合图原子部署（全成功或全回滚）；② 跨自动化一致性校验（共享实体冲突预检）。

### v2.4 经验闭环与预测
- **F11 af_experience 消费闭环接 executor/predict** `[强化]`
  - 现状：`af_experience.py` 纯采集+导出，无消费方（`observe:56`）。
  - 涉及：`af_experience.py` `af_executor.py` `af_predict.py`。
  - 细分：① 经验库接进 executor 实体解析/动作选择；② 接进 `af_predict` 作为先验。

- **F12 af_predict 与 G4 分级联动** `[强化，决策 C 协同]`
  - 现状：`af_predict.py` 自注"纯新增"，与 auto/shadow/ask 两套口径（`af_predict.py:327`）。
  - 涉及：`af_predict.py` `af_shadow.py` `af_conf.py`。
  - 细分：① 预测预触发与 G4 band 联动（stage schema 冻结后统一口径）；② pre_trigger at-most-once 与 conflict 协调。
  - 决策结论（C 协同）：两套置信口径随 stage schema 冻结归一。

- **F13 af_evo 提案真 IR 内联与 band 口径** `[强化]`
  - 现状：`af_evo.py` `suggested_ir` 是 delta 信封，真 IR schema 未内联；`require_shadow_band` 默认 False（`EvoPolicy:454`）。
  - 涉及：`af_evo.py` `af_ir/schema/ir.schema.json`。
  - 细分：① evo 提案直接产真 IR delta（对准 v0.3.0 group 锚）；② 默认要求 shadow band 才允许 promote。

### v2.5 NL 双向结构化保真
- **F14 NL→IR 结构化可逆编译器** `[新建/强化]`
  - 现状：IR→NL 成熟（`af_nl.py`）；NL→IR 是启发式+LLM（`af_orchestrator.py:847`）。
  - 涉及：`af_nl.py` `af_orchestrator.py` `af_draft.py`。
  - 细分：① NL→IR 改结构化（复用 M3 AskSpec 控件元数据反向生成）；② 保证 IR→NL→IR 往返保真（同 IR 往返文本可 diff）。
  - 验收：给定 IR，render→解析→render 文本稳定；复杂条件句 NL 能反解回等价 IR。
  - 设计困难（非决策项）：自然语言歧义→结构化可逆保真边界（建议接受"IR 为唯一真源，NL 仅视图"）。

- **F15 词表 / 算子单一真值源** `[强化]`
  - 现状：词表/算子手写映射（`af_nl.py:23` `_ACTION_VERBS` `:43` `_CMP_SYMBOL`）。
  - 涉及：`af_nl.py` `af_ir/schema/ir.schema.json`（与 M2 forbid 词表同源）。
  - 细分：① NL 词表与 AF-Spec forbid 词表统一真值源；② 新域自动派生 NL 文案（顺带覆盖决策 A 的补域词表）。

---

## §3 验收纪律与铁律（沿用 v2.0 + 决策补充）
1. **IR 唯一真相锚**：凡涉 IR 字段增删，先改 `ir.schema.json` 再改 Python 投影（决策 D 重申：复合编排必须扩展 IR，不建第二真相源）。
2. **测试由采信台真跑**：v2.0.1（F0.1）先清 103 未提交文件，否则铁律失效（决策 H）。
3. **文档阶段不部署**：各里程碑过变更窗口 + 评审签字才合主干。
4. **零 LLM 红线**：内核不引入 `do:python`；auto-fix/深修不猜设备、不突破 canary/risk/approve。
5. **诚实铁律不可破**：EXEMPT ≠ VERIFIED（决策 B）；回滚不了 fail-closed（决策 E）。
6. **每个功能卡片独立可验收**：目标 / 涉及文件 / 细分迭代 / 验收门 四要素齐备。

---

## §4 决策记录（DCD 2026-09-28 已裁定，L3 战略）
来源：`E:/NAS/关键决策部/decisions/20260928-AutoForge-v2.1设计难题A-F-决策.md`，全部经源码核实。

| # | 难题 | 裁定摘要 | 落实动作（关联功能） |
|---|---|---|---|
| **A** | 仿真底座默认 + 单一真值源 | 前提错误，问题更小。**保持 FakeHA 默认、HiFi opt-in；单一真值源已存在（`SERVICE_STATE`），无需冻结**。 | 改 F2 为"双轨对拍测试"；F1 按 P1/P2/P3 域清单建模（不凑数字）。 |
| **B** | UNVERIFIABLE 豁免转正 | **不许人工豁免转正**。先做期望态推导扩展（toggle/set_温度/position/volume 可推导）；真不可推导走 `EXEMPT` verdict 人审，不计入 streak，诚实报告显式 exempted。 | F3：推导扩表 ~20 行 + EXEMPT 枚举 + 人审通道 + 落盘。 |
| **C** | build/simulate schema 冻结 | **冻结，v2.1 就做**（成本最低杠杆最高）。 | F5：返回加 `"schema":"af-stage/1"`；`issues_from` 改严格模式；约 1 天。 |
| **D** | 复合编排模型选型 | **扩展 IR `group` 容器节点**（不建新 DSL/图组合层）；v2.3 排期 + **需求决策门**（多意图占比 <5% 则推迟）；前置修 `ir_version` 枚举 + `additionalProperties:false`。 | F9：group 节点 + ir_version 枚举化 + 灰度兼容。 |
| **E** | 下发后撤销红线 | **做，但叫常规安全能力（非红线）**。60s 窗口 + 风险域二次确认 + 不可映射 fail-closed；pre_snapshot 扩属性。 | F7：pre_snapshot + DOMAIN_SETTER(~15 行) + fail-closed + CLI/WebUI undo。 |
| **F** | 试演期失败封禁 | **改"只统计不封禁"为"失败即暂停"，分级**。低风首败通知/二次暂停；高风首败即暂停；恢复须人工。 | F6：Store JSON 写透 + paused 接执行闸 + 分级。 |
| **G** | UI 三目录分裂（新增） | **`ui-user-mimo` 为 ForgeSight 主线；`ui-user/` 冻结归档；`ui/` 运维控制台独立**。下个大版本前完成归档 + 交接文档。 | 见 `文档总索引.md` 治理项。 |
| **H** | v2.1 前置阻塞（新增） | **先收口 v2.0.1 投产版**：阻塞项（P0-9 鉴权 fail-closed / 103 未提交文件 / 镜像未烘 / token 在 env_file）+ 文档鲜度修复；2–3 天，先于 v2.1。 | 见 §2.0。 |

---

## §5 治理项 G：UI 目录收敛（决策 G）
- **现状**：`ui/`（运维控制台，最成熟）、`ui-user/`（ForgeSight 一代）、`ui-user-mimo/`（ForgeSight 二代，MiMo 生成含 tests）三者并行；ForgeSight 两实现是浪费与分裂源。
- **裁定**：`ui-user-mimo` 主线（更新、含测试）；`ui-user/` 冻结归档（保留可读不开发）；`ui/` 独立存续。
- **执行**：下个大版本（v2.3 前）完成归档 + 更新交接文档；新 UI 需求只落 `ui-user-mimo`。

---

## §6 待关键决策部下一步
本路线图已把 A–H 裁定落到 v2.0.1–v2.5 功能卡。建议决策部据此**制定更全面的版本规划与里程碑拆分**（含每个 F 的 PR 拆分、工时、依赖序），并补充：
- v2.3 需求决策门的数据采集方案（多意图请求占比统计）。
- F4/F14 两项非决策类设计困难的进一步评审。

> 路线图 v2.1+ 决策融合版完。A–F 已裁定落地，新增 G/H 治理项已纳入；所有功能卡均附 DCD 裁定引用。
