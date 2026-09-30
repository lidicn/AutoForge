# F14 — NL↔IR 结构化可逆编译器 设计文档

> 版本: v2.5 初稿 | 状态: **设计中** | 需求门: ✅ 通过（路线图 F5 阶段契约已锁，IR schema "af-stage/1"）
> 作者: AutoForge Core | 最后更新: 2026-09-30

## 1. 背景与动机

AutoForge 当前有两条独立的 NL↔IR 通道：

| 通道 | 方向 | 入口函数 | 能力边界 |
|---|---|---|---|
| NL → IR（正向） | **Proposal → IR dict** | `af_proposal.build_ir(proposal)` | 结构化 Proposal → 结构化 IR |
| IR → NL（反向） | **IR → 中文描述** | `af_nl.render_automation(auto)` | 结构化 IR → 可读 NL 文本 |

两条通道**共享 IR schema "af-stage/1"**（F5 已锁），但**不做往返验证**：IR→NL→IR 两次转换后无法保证产出相同的 IR。

**设计目标**：让 IR 成为 NL 的**可逆中间表示**——NL→IR 不丢语义、IR→NL→IR 往返保真、IR 任何字段都能生成 NL 片段。

## 2. 往返保真度标准（Round-Trip Fidelity）

### 2.1 核心断言

> 对任意 IR `ir`，经 `IR→NL→IR` 后产出 `ir'`，必须满足 `ir ≡ ir'`。

**`≡` 的定义**（宽松匹配，忽略 IR 生成过程中的非语义差异）：

| 字段 | 保真度要求 | 说明 |
|---|---|---|
| `automation_id` | **完全相等** | 身份标识不可变 |
| `nodes[].id` | **完全相等** | IR 拓扑节点身份不可变 |
| `nodes[].kind` | **完全相等** | trigger/condition/do/ask 类别不可变 |
| `nodes[].entity_id` | **完全相等** | 实体引用不可变 |
| `nodes[].action` / `service` | **完全相等** | 动作域不可变 |
| `nodes[].params` | **语义相等** | 允许参数顺序差异，但值必须匹配 |
| `nodes[].condition` | **语义相等** | 允许嵌套条件结构等价变形 |
| `nodes[].ask` | **结构相等** | M3 AskSpec 控件元数据复用 |
| `edges` | **完全相等** | 拓扑关系不可变 |
| `schema` | **完全相等** | 必须为 "af-stage/1" |

### 2.2 不要求可逆的字段

| 字段 | 原因 | 处理方式 |
|---|---|---|
| 仿真运行时标记 | `simulate_track` 注入的 `stage`/`diff_sha` 是运行时产物 | NL→IR 时**不写入**，IR→NL→IR 时**不丢失**（原样保留） |
| `honest_report` 质量评分 | 运行时动态评分 | 同上，作为 IR 扩展属性独立于核心 schema |

### 2.3 保真度分级

| 等级 | 描述 | 适用场景 |
|---|---|---|
| **L0 零损** | IR 所有核心字段经往返完全不变 | v2.5 核心目标 |
| **L1 等损** | 语义相等但值可能不同（如 condition 等价变形 `(A and B) or C` ↔ `(A or C) and (B or C)`） | 复杂布尔条件 |
| **L2 部分损** | 某些 NL 表达无法完整还原 IR 字段（如 "晚上别太晚" 无法精准推 hour=22） | 模糊意图输入 |
| **L3 不可逆** | 含运行时动态内容的 IR 无法 NL 渲染 | 纯仿真产物 IR |

v2.5 实现目标：**L0 + L1**，L2/L3 标记不可逆字段并在 NL 输出中用 `[运行时]` 占位符标注。

## 3. 架构设计

### 3.1 组件映射

```
NL Text ──▶ IR Builder ──▶ IR dict (af-stage/1) ◀── IR Schema Validator
  │                                         │
  │   NL→IR (正向)                          │  IR→NL (反向)
  │   af_proposal.build_ir(proposal)        │  af_nl.render_automation(auto)
  │                                         │
  └─────────────── Fidelity Verifier ◀─────┘
                  (往返一致性检查)
```

### 3.2 IR Builder 增强（NL→IR 方向）

当前 `af_proposal.build_ir()` 接受结构化 `Proposal` 对象。v2.5 目标：
- **支持自然语言输入**：`build_ir_from_nl(text: str) → dict[str, Any]` — 解析 NL 文本为 IR
- **不引入 LLM**：纯规则 + KNOWN_ACTIONS 真源（F15 已交付）做域-服务映射
- **与现有的 NL 渲染对称**：NL Builder 的解析规则必须与 `_ACTION_VERBS` 的中文表达库对齐

### 3.3 IR Schema Validator 增强

当前 `load_graph()` 在加载时做基本解析。v2.5 增强：
- **字段级 Pydantic 校验**：`schema="af-stage/1"` 的每个字段都有严格类型约束
- **往返前校验**：NL→IR 后、IR→NL→IR 后各跑一次校验器，确保结构不变
- **不可逆字段标记**：所有标记为 L2/L3 等级的字段必须在 IR 输出中标注 `_non_reversible: true`

### 3.4 M3 AskSpec 元数据复用

当前 AskSpec 是 IR 里的内嵌结构。v2.5 设计：
- **NL Builder 支持 ask 控件文本解析**：从 NL 表达中提取 "请确认"、"需要输入" 等 ask 语义
- **AskSpec 控件类型与 KNOWN_ACTIONS 对齐**：比如 `input_boolean` ask 对应 yes/no 控件，`select` ask 对应下拉
- **ask 元数据跨层一致**：NL→IR 生成的 AskSpec 与 IR→NL 渲染的 AskSpec 必须是同一个对象（hashable）

## 4. 实现计划

| 阶段 | 交付物 | 依赖 | 验收标准 |
|---|---|---|---|
| P1 | Fidelity Verifier（往返校验器） | F5 schema + F15 真源 | 对 **30 条** IR 样本，核心字段 **L0 完全相等** + `condition` **L1 结构等价**（归一化后 `IR→NL→IR ≡ 原 IR`）。判据经 DCD 20261001·F 裁定（分层 + 30 条 + P1 准入）|
| P2 | NL Builder（NL→IR 正向） | P1 + KNOWN_ACTIONS | 30 条 NL 文本样本全部成功构建合法 IR |
| P3 | AskSpec 跨层复用 | P1 | AskSpec hash 一致、控件类型匹配 |
| P4 | 不可逆字段处理 | P1 | L2/L3 字段正确标注、NL 渲染用 `[运行时]` 占位符 |

**验收用例草案**（从现有测试和产品需求抽取）：

```
NL 输入:   "晚上 8 点后如果有人在家，打开客厅灯"
IR Builder 输出:
  nodes: [
    trigger { kind: "state", entity_id: "binary_sensor.presence", to: "on" },
    condition { kind: "time", after: "20:00" },
    do { kind: "do", entity_id: "light.living", service: "turn_on" }
  ]
  edges: [trigger → condition → do]
  
IR → NL 往返: "当 binary_sensor.presence 变为 开 且 时间晚于 20:00 时，执行 light.living.turn_on"
IR → NL → IR ≡ 原 IR: ✅
```

## 5. 与现有模块的集成点

| 模块 | 集成方式 | 改动量 |
|---|---|---|
| `af_proposal.build_ir` | 内部调用 NL Builder（NL→Proposal→IR 链） | 小 |
| `af_nl.render_automation` | 对称增强（补所有 KNOWN_ACTIONS 的 NL 片段） | 中（F15 已开始） |
| `af_ir.models.load_graph` | 新增 IR Schema Validator 调用点 | 小 |
| `af_actions.KNOWN_ACTIONS` | 作为 NL Builder 的域-服务映射基表 | 无（已交付） |
| `af_api` | 新增 `/api/nl-to-ir` 和 `/api/fidelity-check` 端点 | 小 |
| 测试 | 新增 `tests/f14/test_fidelity_roundtrip.py`（P1 验收） | 中 |

## 6. 不在 v2.5 范围内

- **LLM 增强解析**：v3 讨论。纯规则 + KNOWN_ACTIONS 足够覆盖 90% 高频 NL 表达
- **跨语言支持**：v2.5 只做中文。英文/多语言在 v3 引入 i18n 层
- **场景展开优化**：scene/script 的完整 HA 语义展开在 F1（已做最小实现）
- **IR 版本升级**：schema 保持 "af-stage/1" 到 v3 再考虑升级

## 7. 风险与缓解

| 风险 | 缓解 |
|---|---|
| NL→IR 解析规则爆炸 | 只做**受限域**：只覆盖 KNOWN_ACTIONS 的域-服务组合，不做开放式 NL |
| 布尔条件等价变形 | Fidelity Verifier 用**结构等价**而非文本相等：`(A and B) or C` 与 `(A or C) and (B or C)` 标记为 L1 等损但语义一致 |
| AskSpec 跨层不一致 | Pydantic 模型复用：IR AskSpec 和 NL Builder 产出 AskSpec 是同一个类 |
| 运行时字段污染 | IR schema 严格区分核心字段（可逆）与扩展字段（不可逆），NL→IR 只写核心字段 |

---

**审批状态**：✅ 已批准（DCD 20261001《AF 三题》·F，判据=方案 C 分层 L0/L1 + 30 样本，**本裁定即视为 P1 准入**）| **进度**：P1 Fidelity Verifier 已交付（`af_fidelity.py` + `af_ir/condition_norm.py` + `tests/f14/`）；P2–P4 待排
