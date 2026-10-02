# AutoForge 第七轮审计报告（广度探测 · 关节处）

> 校验器：`coverage-ledger` PASS（12）｜`findings` PASS（1）｜状态 `complete`

## 方法：先建依赖图，再横向比对

本轮不逐模块读代码，而是：
1. 建模块依赖图 → 定位 **fan-in 最高**的抽象（关节）
2. 找出该抽象的**所有实现**
3. **横向比对**它们的契约是否一致

fan-in 排行：`af_ir`(20)、`af_time`(20)、`af_adapters`(12)、`af_state`(10)、`af_store`(9)、`af_conf`(8)、`af_audit`(7)、`af_flock`/`af_bus`(6)。

## 核心发现

**`StateProvider` 的 4 个实现分成两派，仿真与生产对同一 IR 给出相反结论。**

- 抛异常派（fail-closed）：`InMemoryStateProvider`(af_state.py:112)、`FakeHA`(fake.py:75 —— **`forge sim` 实际底座**)
- 静默省略派（fail-open）：`HAStateProvider`(ha.py:194 —— 生产)、`vhass bridge`(bridge.py:35)

端到端实证：IR `or(is_on(motion), is_on(ghost))` + 状态 `motion=on, ghost 不存在`
- 仿真：`snapshot()` 抛 UnknownEntity → 整段软失效 → **不执行**
- 生产：返回部分快照 → `or` 用 `any()` 短路，ghost 永不求值 → **执行**

根因三层耦合：① 策略不一致 ② 失败时机不同（snapshot vs get）③ 表达式短路放大分歧。

触发条件是实体漂移（HA 常态），破坏的是项目铁律「看到即跑的」——**用户在仿真里看到"不执行"而批准，上线后实际会执行**。

## 本轮证伪

关节处大多是干净的：

- **`af_time`(fan-in 20，全仓最高)**：`jump` 只动墙钟不动单调钟是**有意设计**（af_time.py:191 注释明确为 NTP 校时/夏令时），目的是让 `af_persist` 定时器墙钟换算在跳变后仍正确。不误报。
- **`af_state` 的"≥2 个点"判定**：`climate.bedroom.temperature` 等形态全部正确处理。
- **`af_adapters`**：ha/http/mock 的 `call()` 契约一致，均返回 `CallResult`。
- **`af_flock`**：租约（恢复时仲裁）与锁（写入时互斥）职责不同，`af_persist` 只用租约有依据。

## 本轮的独特价值

前六轮缺陷都在**模块内部**，本轮在**抽象与实现的交界**。

这类缺陷每个实现单独看都合理，**只有横向比对才暴露**——逐模块读代码永远发现不了。

## 跨七轮

`instance.id` 连续六轮未修复仍是唯一确定 P0。新增一类：**实现间策略不一致**（`StateProvider` 4 实现两派 + 第四轮 `_undo` 3 域三策略）——**同一抽象的多实现缺乏强制契约**。
