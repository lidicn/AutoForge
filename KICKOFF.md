# AutoForge 开工令（KICKOFF）

> 用途：**新对话冷启动唯一入口**。开场只需一句「读 `E:\NAS\AutoForge\KICKOFF.md` 开工」。
> 版本：v1.7.1　日期：2026-09-27（v2.0.1 进行中）
> 关联：`docs/IR_AND_RUNTIME.md`（IR v0.2.1，已冻结，唯一真相）｜`docs/NAMING.md`（命名约定）｜`docs/HA_SEMANTIC_DIFF.md`（与 HA 的有意偏离）｜`docs/G1_ACCEPTANCE.md`（验收映射表）｜`docs/ROADMAP.md`（里程碑）
>
> **进度**：G1–G7、真机 HA 接线、UI 服务层（R1/R2 + 鉴权）、真机常驻监听、P1 实例持久化，以及 **v0.2.0–v1.7.1 全部小版本**（含 `v1.6.0-a` 数据源升级薄片、v1.7.0 WebUI 并入主仓、v1.7.1 回归基线对齐）均已交付，**路线图待办已清零**。当前稳定版本 **`v1.7.1`**；**`v2.0.1`「投产收口」进行中**（文档清点 / token secret / 鉴权 fail-closed / 镜像烘入 / 文档鲜度 / ui-user 冻结）。
> **回归基线**：**529 passed / 10 skipped**（本机与 NAS 容器双环境全绿，DoD#4 达成）；v2.0.1 在此基线上增量。
> **下一阶段**：串行主链 `v1.1.0–v1.7.1` 全部交付；内核候选见 `docs/ROADMAP.md` §「不排期项」（待真实需求驱动）。前端控制台已于 2026-09-17 由独立仓库 `autoforge-ui` 合并进主仓 `ui/`（v1.7.0 A/B 批次已交付，C 批次经验闭环待做）。`v2.0.1` 收口后进入 `v2.1`（待规划）。
> 另：`v1.1.0` 已把「自然语言设备名 → entity_id」做进平台自身（`af_catalog`），**不再依赖 MA 取实体**：MA 回归「语义/身份/历史」，AF 管「执行事实」。
> 冷启动只需读本文件 + `docs/ROADMAP.md`；G1 骨架开发顺序（§4/§5）保留作历史参考。

---

## 1. 一句话定位

**AutoForge = Agent 为中心的智能家居自动化平台**：让 agent 撰写自动化，机器验证正确性，人只看自然语言。

- **不连 Node-RED**，自建平台**直连 Home Assistant**
- 沿用 AutoFlow v1 的核心资产 **vhass**（改为基于 `pytest-homeassistant`）
- 生态分工：**AutoForge(AF) = 自动化工厂**｜**MA(memory-agent) = 创造力源泉**｜**DB(doubao-butler) = 连接与执行**

命名：对外产品名 **AutoForge**，子命令 `forge build` / `forge run` / `forge sim`；Python 包 `autoforge`，内部模块前缀 `af_*`。
详见 `docs/NAMING.md`——**改名已完成，`Forge` 与 `an_*` 为历史写法，新代码一律用 AutoForge / `af_*`**。

---

## 2. 已冻结的决策（不得推翻，改动需重新评审）

| # | 决策 | 内容 |
|---|---|---|
| 1 | Graph 唯一真相 | Spec 与 NL 都是投影；NL 由 Spec **确定性渲染**，看到即跑的 |
| 2 | 7 节点 + 6 边 | `on/if/do/ask/wait/set/pass`；`then/yes/no/default/on_timeout/on_cancel/on_error` |
| 3 | 边优先级 | `on_cancel > on_error > on_timeout > yes/no/then > default` |
| 4 | 两道闸串行 | `forge build` 验安全 → `forge sim` 验逻辑，**不可调换** |
| 5 | 适配器纯执行层 | 适配器只做单次下发；重试/降级属 IR 语义层 |
| 6 | 并发 mode | 复用 HA `single/restart/queued/parallel`，`auto` 头部声明 |
| 7 | 两种 Timer 隔离 | `for=10m`=持续条件（边沿+时长，非电平）；`wait`=实例定时器 |
| 8 | 快照 = 求值段 | 段内原子、恢复时重新取；**do 结果只进 `vars`，不回写快照** |
| 9 | on_cancel 不回滚 | 已执行动作保留，清理必须显式写在 `on_cancel` 分支 |
| 10 | fn 语言阶梯 | 内置表达式 → CEL → Lua 5.4 → 远期 Wasm |
| 11 | conf 分级自主 | >0.85 自动｜0.6–0.85 shadow｜<0.6 只出 `ask` 提案 |
| 12 | 序列化 | 原型期 **JSON IR + JSON Schema**；AF-Spec 是 agent 撰写面，编译到同一份 JSON IR |
| 13 | 范围 | G1–G3 必做；G4/G5/G6 入 backlog；多成员 P2 延后 |

---

## 3. 禁止项（红线）

- ❌ **不碰 Node-RED**（不读写 1880/1990，不产出 flow）
- ❌ **不自研仿真器**——vhass 必须基于 `pytest-homeassistant` 二次开发
- ❌ **不上原生 Python 沙箱**（RestrictedPython 有已知绕过，会击穿 L3）
- ❌ **IR 冻结前不写 AF-Spec EBNF**
- ❌ 适配器不得含 `retry`/`fallback`/语义层 `timeout` 等 IR 未定义参数
- ❌ 不得在实例上下文中存不可序列化对象（闭包/Socket/生成器）
- ❌ 不在 G1 引入持久化、跨进程、fn 节点、跨自动化事件（仅留接口空位）
  - ✅ 已落地：实例持久化 `persist`（P1）、跨自动化事件发布侧 `emit`（v0.3.0）与订阅侧 `on event`（v0.4.0）
  - 🔮 仍为保留位：`fn` 节点（v1.0.0 评估）

---

## 4. G1 目标：最小可运行闭环

**范围**：全程内存态。交付 6 个模块，跑通 8 条验收用例。

```
JSON IR（唯一真相）+ JSON Schema
        ↓
静态扫描器（编译期，第一道闸）
        ↓
事件总线 → 调度器 → 实例管理器 → 节点执行器 → 适配器层
```

> ⚠️ **顺序修正**：调度器在实例管理器**之前**——事件到达后先由调度器做 mode 判断与配额决策，再由实例管理器执行状态迁移。

### 4.1 事件总线 EventBus

- `BusEvent{event_id, source, entity_id, state, last_changed, payload}`
- 去重：`(entity_id, state, last_changed)` 三元组
- 节流：单实体 >1 次/200ms 合并
- 熔断：单实体 10s 内变更 ≥5 次 → 熔断该实体所有触发分发，30s 后恢复 + 审计
- 订阅：按 `entity_id`/事件类型精确订阅，**不支持通配符**

### 4.2 ★ 时间源 TimeSource（必须抽象，开工第一件事）

> **修正**：原设计"Runtime 不生成本地时钟"过强且会卡死 vhass。计时器（`wait`/`for`）必须有时钟，且 vhass 需要**注入虚拟时钟**做时间旅行。

```
TimeSource 接口
├─ now()          → 墙钟（事件时间戳取 HA 的 last_changed）
├─ monotonic()    → 单调时钟（计时器用，不受系统时间调整影响）
└─ [仿真] 由 vhass 注入虚拟时钟，支持 async_fire_time_changed 时间旅行
```

**契约**：生产与仿真共用同一接口，**禁止在业务代码里直接调 `time.time()`**。

### 4.3 调度器 Scheduler

| mode | 逻辑 |
|---|---|
| `single` | 已有 active → 忽略 |
| `restart` | 已有 active → 终止旧实例后 spawn 新实例（**见下 ⚠️**） |
| `queued` | 入 FIFO 队列，默认上限 10 |
| `parallel` | 直接 spawn，受配额限制 |

- 配额：全局并发默认 100，单 automation 默认 10，超限拒绝 + 审计
- mode 判断 + 实例操作必须**原子**（G1 单进程用锁）
- `for` = 边沿触发 + 持续时长校验，**不是电平触发**（与 HA 对齐）

> ⚠️ **`restart` 是否触发旧实例 `on_cancel`？** 原设计未定义。**决定：触发**——`on_cancel` 分支本就是清理钩子，不触发则清理逻辑形同虚设。
> **需显式记录与 HA 的差异**：HA 的 `restart` 不执行任何清理动作。此为本平台的有意偏离，须写入文档与 NL 渲染说明。

### 4.4 实例管理器 InstanceManager

- 接口：`spawn` / `suspend` / `resume` / `cancel` / `fail` / `done` / **`expire`**（24h 或超配额强制销毁）
- 状态机：`created → active → suspended → … → done|cancelled|failed|expired`，**非法迁移直接抛异常**
- 上下文严格遵循 §3 JSON 结构，**即使内存态也保持可序列化**
- 快照：每求值段一份，恢复时**必须生成新快照并丢弃旧快照**，禁止跨段复用
- `vars` 命名空间：`entity.*`（只读）/ `vars.*`（可写）/ `context.*`（系统内置），冲突编译报错

### 4.5 节点执行器 NodeExecutor

1. 激活/恢复 → 拉快照 → 初始化求值上下文
2. 按序执行节点，按**边优先级从高到低**匹配下一条边（不允许按定义顺序 fallback）
3. 遇 `wait`/`ask` → 注册计时器 → 挂起，结束求值段
4. 遇 `pass`/终态 → 终止

**契约**
- **快照边界**：段内 `entity.*` 全来自同一快照；`do` 结果只写 `vars.*`，不回写快照
- `do` 单次调用，不重试不降级；失败走 `on_error`，无则 `failed`
- `ask` 默认按 **room 维度**匹配应答；同房间多实例按**创建时间优先**，未匹配走 `default`；一次应答仅生效一次
- 中断立即终止当前节点；`atomic=true` 的 `do` 必须执行完再跳转
- 实体不存在 → 走 `on_error` 软失效 + 漂移告警，**不直接 failed**

### 4.6 适配器层 AdapterLayer（纯执行层）

```
interface Adapter { call(action, params) -> {success, data, error} }
```

- **单次调用**，无重试、无降级、无语义层超时
- 结果标准化，**不隐式吞错**
- G1 实现 **HA 适配器 + HTTP 适配器**；MQTT/TCP 留接口空位
- 静态扫描拦截适配器配置中的 `retry`/`fallback` 等参数

> ⚠️ **修正"无超时"**：**传输层 socket/connect 超时必须保留**（否则单进程永久挂死）。
> 边界：传输层（连接/鉴权/TLS/套接超时）归适配器；语义层（重试/退避/降级/业务超时）归 IR。

### 4.7 静态扫描器 StaticScanner（第一道闸，G1 十项）

1. 高危动作 L3 + 白名单
2. `ask`/`wait` 必须有 `on_timeout` 或 `default`
3. 跨自动化**实体读写依赖矩阵**环检测
4. 静态图内循环（无终止条件）
5. `on_cancel` 分支产生新实例
6. 挂起点内执行高风险动作
7. Shadow 模式 `do` 写设备
8. 跨自动化读写对方实例变量
9. 适配器配置含 IR 未定义策略参数（`retry`/`fallback`）
10. `snapshot=false` + 多条件 AND → 告警

延后 G2：实体存在性校验、非幂等动作告警。

### 4.8 ★ NL 渲染器（建议列入 G1）

> **修正**：原骨架未列 NL 渲染器，但它**直接读 Graph，不依赖 AF-Spec**，可以现在就做。

理由三条：
1. 用户的第一诉求就是"**我只想看到自然语言，不要 YAML/连线**"
2. NL 覆盖率检查（§14-13：每个节点必须被 NL 提及一次）需要它，这是防止"批准的与跑的不一致"的关键校验
3. 它是检验 IR 是否真的可理解的最快手段

---

## 5. 开发顺序（自下而上，每层单测）

```
0. IR 数据模型 + JSON Schema          ← 一切依赖它，先定
1. TimeSource 抽象（生产/仿真两套实现）
2. EventBus        去重/节流/熔断/时钟
3. InstanceManager 状态机 + 上下文 + 快照
4. AdapterLayer    接口 + Mock 适配器
5. NodeExecutor    7 节点 + 6 边 + 求值段（用 Mock 跑通）
6. Scheduler       4 mode + 配额（集成测试）
7. StaticScanner   十项（用非法 IR 验证拦截）
8. NL 渲染器       Graph → 自然语言 + 覆盖率检查
9. 接真实 HA 适配器，跑 8 条验收用例
```

---

## 6. 验收用例（G1 达标标准）

| # | 场景 | 期望 |
|---|---|---|
| 1 | 白天人在 + 光照<200 + 灯灭 | 开灯，断言 `light.study_main=on` |
| 2 | 人离 10 分钟关灯，第 5 分钟人回 | **关灯被取消**（持续条件语义） |
| 3 | 夜晚（太阳历仿真）电脑开 | 挂灯开 |
| 4 | 温度>27 + 门关 → 询问，60s 无应答 | 走 `on_timeout` → 静默，**实例不挂起** |
| 5 | 询问中"人离开" | 走 `on_cancel` → 取消，**已执行动作不回滚** |
| 6 | `http.post delete_all` | **编译期拦截**，不进仿真 |
| 7 | 适配器配置带 `retry=3` | 编译期拦截（违反纯执行层） |
| 8 | A 开灯 → B 关灯 → A | **实体依赖图环检测报错** |

---

## 7. 与本项目其余部分的关系（避免串台）

| 项目 | 位置 | 本对话/本项目职责 |
|---|---|---|
| **AutoFlow v1** | `E:\NAS\autoflow` | **另一条对话维护**（94 测试红清帐、竞技场教程、MA 消费点）。AutoForge **不读写**其代码 |
| **doubao-butler (DB)** | `E:\NAS\doubao-butler` | 实时连接与执行、TTS、PushGuard 风控。`ask` 的话术与对话由 DB 承接 |
| **memory-agent (MA)** | — | 创造力源泉，提供带 conf 的假设 |
| **AutoForge (AF)** | `E:\NAS\AutoForge` | **本项目**。HA 自动化的唯一写入方 |

---

## 8. 开工第一句（新对话直接复制）

> 读 `E:\NAS\AutoForge\KICKOFF.md` 和 `E:\NAS\AutoForge\docs\IR_AND_RUNTIME.md`。
> 按 §5 开发顺序从**第 0 步 IR 数据模型 + JSON Schema** 开始，实现 G1 最小骨架。
> 严格遵守 §2 冻结决策与 §3 禁止项。每一步自测后再进下一步。
