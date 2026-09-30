# 设计吸收：mimo 2.6ultraspeed × HOMESTAGE 优秀设计 → AutoForge

> 日期：2026-09-24　主导：CB（重新主导 AF 后端）　接管：AFD（豆包，转前端/接工单）
> 来源：① 小米旗舰模型 **mimo 2.6ultraspeed**（网页版，经 `E:\NAS\Lever-Hub\需求稿` 前置条件产出设计与代码）；② 技术蓝图 **HOMESTAGE「家演」**（同源 mimo 产出）。
> 定位：**AFD 是吸收落地者，不是原创设计者**。本文件把已验证的优秀设计「落盘」为 AF 的下一步开发依据，供 ROADMAP.md 引用。
>
> **更正（2026-09-24 续）**：初稿曾建议删/降级 `af_runtime_plugins`/`af_nl`/`af_closedloop`/`af_orchestrator`，系未读代码之误。已通读确认这些都是从 mimo 设计长出的真实模块（conf 分级引擎已 env 集成、`af_nl` 被核心 import、闭环依赖 orchestrator 契约），**均保留**，更正见 §3.3。`af_self_repair.py` 实际不存在（交接单笔误）。

---

## 0. 已确认的核心架构（不再回退）

mimo 方案对 Agent→AF 交互做了关键瘦身，已 commit（`184bf39 feat: af_draft + af_apply 意图JSON→IR 黄金路径`）并 NAS 真机实测通过（防盗门→客厅灯）：

| 维度 | 旧方案 | mimo 方案（锁定） |
|------|--------|-------------------|
| Agent 产物 | 写 AF-Spec 文本 | 传**意图 JSON** |
| MCP 调用 | 6–9 次 | **1–2 次** |
| token 消耗 | 2–4k / 题 | **0.3–0.8k / 题** |
| 编译/仿真/入队 | 分开三步 | **`af_apply` 一次搞定** |

**结论**：意图 JSON → `af_draft`(意图→IR) → `af_apply`(校验→仿真→入队) 是 AF 的确认主干，不回退到「Agent 写文本 Spec」旧路径。`af_orchestrator`（旧编排器，2427 行）降为实验参考，不进生产（见 §4）。

---

## 1. HOMESTAGE 设计理念吸收（7 项，产品名待 AF 重定）

> ⚠️ **命名说明**：下列「首演码 / 影子物理特性 / 诚实报告」等为 HOMESTAGE 蓝图原词，**AF 不照搬命名**，后续重新设计产品名。此处只记录「设计理念」，落到 AF 时以 AF 的命名与排期为准。
> 评级：🔴 高价值必做 / 🟠 高价值但需评估 / 🟡 迭代增量。
> 每项给出「设计要点 / AF 现状缺口 / 落地模块 / 拟排版本」。

### 1.1 🔴 首演码仪式 + 试演期（premiere gate）
- **设计要点**：一次性 6 位码绑定 **diff 哈希**（防验码后掉包）+ **5 分钟过期** + **原子消费防重放**；部署后自动进入 **试演期（默认 24h）**，只统计不封禁，assert 失败/抖动自动暂停并推送。
- **AF 现状缺口**：`af_live` 只有「二次确认 + 非空白名单」三重闸；**无一次性码、无哈希绑定、无试演期自动暂停**。部署=终点，信任兜底脆弱。
- **落地模块**：新增 `af_premiere`（码签发/消费/哈希绑定/WebPush 通知）；试演期挂 `af_pending`/watch 守护。
- **拟排版本**：v2.0.0。

### 1.2 🔴 结构化 Ask 协议（typed suspension）
- **设计要点**：`ask` 是一等公民工具，`kind∈{choice,entity,time_range,threshold,text}`，挂起超时、PWA 渲染原生控件；收敛纪律（连问≤3 轮、每轮≤2 问、剩余不确定项必须落成 ask，不允许脑补默认值）。
- **AF 现状缺口**：`af_runtime.executor.answer(room, text)` 是**自由文本应答**；澄清发生在「人待批队列里改」而非「Agent 主动挂起要结构化参数」。运行时挂起机制已具备（ask 节点），缺的是**结构化 kind + 原生控件映射**。
- **落地模块**：`af_api`/`af_runtime` 增 `AskSpec` 类型 + `/api/asks` 控件元数据；前端渲染原生选择器/滑杆/时间轴。
- **拟排版本**：v2.0.0（与首演码同期，同属「部署前人机回路」）。

### 1.3 🔴 封闭原语 + `extra="forbid"` 抗幻觉（AF-Spec 硬化）
- **设计要点**：DSL 词表封闭，Agent 只能从 `trigger/guard/do/wait/assert` 等十几个原语选；Schema `extra="forbid"`，编造字段编译前即报错并回灌自纠。
- **AF 现状缺口**：IR（v0.2.1）Schema 已锁 `additionalProperties`，但 **AF-Spec 文本面**（`af_spec.py`）与 Agent 撰写面未显式强制「封闭词表 + 拒绝额外字段」纪律；mimo 意图 JSON 路径由 `af_draft` 强校验，但文本 Spec 路径仍宽松。
- **落地模块**：`af_spec.py` + `af_draft` 增加「未知原语/字段即拒」硬闸 + 自纠回灌。
- **拟排版本**：v2.0.0。

### 1.4 🟠 影子物理特性拟合（sim-to-real 护城河）
- **设计要点**：每个实体标注 `latency / auto_off / rate_limit / cooldown`，来自设备类目默认 + 从历史事件流（`record_transition` 等价物）实测拟合。这是「彩排全绿、上线翻车」风险的直接对策，也是产品真正壁垒。
- **AF 现状缺口**：`af_vhass` 仿真里 `light.turn_on` **恒成功**，不建模设备物理语义；`af_shadow` 当前只做 conf 分级快照，未存物理特性。
- **落地模块**：`af_shadow` 扩 `physical_profile` 字段 + 拟合器（消费 `af_state`/`af_vhass` 录制）；`af_vhass` 仿真消费该 profile。
- **拟排版本**：v2.1.0（依赖 v1.6.0 注册表数据）。

### 1.5 🟠 诚实报告三栏（verified / inferred / non-simulable）
- **设计要点**：Rehearsal Report 固定三栏，不许把「推断」说成「已验证」；逃生舱（`python` 步骤）标黄。
- **AF 现状缺口**：`af_expect` 已有 `pass/fail/unverified` 三态（好），但 **simulate 报告整体**未强制「已验证/推断/不可仿真」分层；`do: python` 逃生舱概念 AF 未引入（红线「内核零 LLM」与 HOMESTAGE 的 python 沙箱不同，AF 不选 python 逃生舱，见 §3）。
- **落地模块**：`af_service.simulate` 报告加 `verified/inferred/non_simulable` 分区；不可仿真动作显式标黄。
- **拟排版本**：v2.1.0。

### 1.6 🟠 全屋仿真冲突检测（whole-house simulation）
- **设计要点**：部署前把**整屋 automation（含用户原有）**一起编译进仿真，做冲突检测，而非只演新规则。
- **AF 现状缺口**：`af_conflict`（G2 候选）只做静态分析，未接运行态；G2 门禁实际靠 `af_scanner` 静态。缺「仿真式全屋冲突」。
- **落地模块**：`af_conflict_runtime` 接入 `af_executor` 写路径（或 vhass 全屋 replay），与 `af_scanner` 互补。
- **拟排版本**：v2.1.0。

### 1.7 🟡 sim↔real trace 对齐测试集（仿真可信硬证据）
- **设计要点**：同一 IR 的「仿真 trace」与「HA 真机 trace」逐事件对齐，作为「仿真可信」的唯一硬证据。
- **AF 现状缺口**：AF 有双环境回归（本机 FakeHA / NAS 真 vhass），但**没有「同一条 IR 的 sim trace ↔ real trace 逐事件 diff」专项回归**。
- **落地模块**：`tests/` 新增 `test_sim_real_alignment.py`（固定种子 replay + trace 对齐断言）。
- **拟排版本**：v2.1.0。

---

## 2. 不采纳项（明确理由）

| 项 | 不采纳理由 |
|----|-----------|
| 重新发明 HScore DSL | AF 已有 IR + AF-Spec，只需**硬化封闭词表**（见 1.3），不另起 DSL |
| Node-RED 双导出器 | AF 落地后端已是 HA YAML；Node-RED 导出是额外 ~35 人时，非必需 |
| 多服务硬切（api/agent-core/mcp-stage/worker 四容器 + PG/Redis） | AF 单进程内存态更轻；是否升级到 worker+PG+Redis 是独立架构决策，**不阻塞 1.1–1.7 吸收**；若做首演码 WebPush 再评估最小推送通道 |
| `do: python` 逃生舱 | AF 红线「内核纯规则、零 LLM 依赖」；HOMESTAGE 的 python 沙箱与 AF 安全模型冲突，AF 选「IR 表达力覆盖 90% + 不引入代码逃生舱」 |

---

## 3. 模块定级（基于 AFD 交接回复，CB 拍板）

### 3.1 晋级生产（已 commit / NAS 验证 / 09-30 范围）
`af_draft` `af_apply` `af_test` `af_mcp` `af_api` `af_runtime` `af_executor` `af_scanner` `af_store` `af_catalog` `af_service` + 核心基础设施。
`af_conf` 分级引擎（`af_shadow`/`af_intervention`/`af_canary_supervisor`/`af_proposal`/`af_feedback` + `af_runtime_ext`）：**已集成**，环境变量 `AUTOFORGE_CONF_GRADING=1` 启用，默认关 → 保留，灰度推进。

### 3.2 降级为实验 / v2.x 候选（未接运行态，但均保留不删）
`af_evo`、`af_health`（evo/predict 前置）、`af_predict`、`af_preference`、`af_proposal`、`af_scene`、`af_tick_supervisor`。
> 注：`af_orchestrator` / `af_closedloop` / `af_nl` / `af_runtime_plugins` 不在本表——见 §3.3，确认保留。
> `af_health` 是 `af_evo`/`af_predict` 前置，v2.x 优先集成。

### 3.3 保留（已通读代码，确认是 mimo 设计入口，不可删）
- `af_runtime_plugins.py`：conf 分级 + 冲突仲裁的**统一装配入口**（`install()` 装配、`install_api()` 挂载端点，共享 ConfidenceStore/InterventionDetector，模块级 try/except 容错）。**现状：入口就绪，但 `af_runtime_plugins.install()` 尚未在 bootstrap 调用** → 挂进启动流程即启用冲突仲裁器。保留。
- `af_nl.py`：G1「只看自然语言」渲染器（IR→中文，含节点覆盖率检查防「批的不跑」）。被 `af_cli`/`af_service`/`af_scanner` 三个模块 import。**保留，勿删**。
- `af_orchestrator.py`：NL→AF-Spec→IR→build→simulate→save 的**参考实现 + 契约源**（`RepairReport`/`FixContext`/`ErrorCode`/`PROTOCOL_VERSION`）。`af_closedloop` 依赖其契约。生产链路仍是 `af_draft`+`af_apply`；本文件作参考/契约库保留，不进生产管线。
- `af_closedloop/`：建立在 orchestrator 契约上的自修复闭环（detect→fix→deepfix→sim→coverage），带 approve 硬护栏。已实现+有单测，未挂运行态。保留为实验特性。

### 3.4 待集成 / 修正
- `af_conflict_runtime` + `af_conflict` + `af_conflict_audit`：已实现，经 `af_runtime_plugins` 挂载，但 `af_runtime_plugins.install()` 尚未在 bootstrap 调用 → **挂载即生效**（做 G2 运行时冲突门禁，补 `af_scanner` 静态）。
- `af_version`：已写，接 `af_store` 一键回滚 UI 待补。
- `af_self_repair.py`：**实际不存在**（交接单笔误），无需处理。

---

## 4. 版本排期草案（详版见 ROADMAP.md）

| 版本 | 主题 | 关键交付 | 依赖 |
|------|------|---------|------|
| **v1.10.0** | **投产收口（09-30）** | 提交固化 WIP；P0-9 fail-closed；P1-4 跨自动化环 union(deps,emit_deps)；gates.sh 跑绿 + WO-AF-014 基线单源；镜像基于 commit 重建（弃 scp 挂卷）；token→secret；CI(pytest+gates) | — |
| **v2.0.0** | **产品可信层（吸收 HOMESTAGE 理念，产品名待重定）** | 部署仪式+试演期自动暂停(1.1 理念)；结构化 Ask(1.2)；AF-Spec 封闭词表 forbid(1.3)；诚实报告分层(1.5 理念) | v1.10.0 |
| **v2.1.0** | **sim-to-real（吸收 HOMESTAGE 理念，产品名待重定）** | 仿真物理保真(1.4 理念)；全屋仿真冲突(1.6)；sim↔real 对齐测试集(1.7) | v1.6.0 / v2.0.0 |
| **v2.2.0** | **DB↔AF ask 对接层** | AF 挂起→DB TTS→语音回注 answer（生态高优先） | v2.0.0 |
| **v2.3.0** | **高阶模块集成** | af_health→evo/predict；af_conflict_runtime 接 G2；af_scene；af_preference | v2.1.0 |

---

## 5. AFD 工作安排（CB 主导下的分工）

- **AFD 主导/承接**：前端（SpecEditorView、expect 面板）、`af_test` 测试通道完善、单测补测（按 CB 清单：`af_closedloop`/`af_tick_supervisor`/`af_conflict`）、FFL 批量执行、文档维护。
- **CB 主导**：WIP 固化提交、投产硬阻塞收口、安全门禁、吸收 mimo/HOMESTAGE 设计（v2.0/v2.1）、高阶模块定级与集成、**运行态装配**。
- **红线**：AFD 不碰 `af_runtime` 装配与安全闸/`af_scanner` 写路径，避免再次出现「未提交 WIP 漂在线上、无法回滚」。
