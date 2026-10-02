# AutoForge 第七轮审计 · 广度探测（关节处 / 连接处）

> 运行 ID：`af-audit-run-7`
> **本轮 skill**：`code-review`（anthropics/knowledge-work-plugins）+ `security-audit`(cloudflare) 的机器可读契约
> 校验器：`coverage-ledger` PASS（12 单元）｜`findings` PASS（1 记录）
> 测试基线：1277 passed / 51 skipped / 0 failed
> **本轮方向**：广度探测**模块接缝**。先建模块依赖图定位"关节模块"，再逐个验其契约一致性

---

## 一句话结论

**`StateProvider` 的 4 个实现分成两派，导致 `forge sim`（仿真）与生产对同一份 IR 给出相反结论。**

这是七轮来**第一个真正打在"接缝"上的缺陷**——前六轮的缺陷都在模块内部，本轮在抽象与实现的交界。

---

## 先定位：哪些是"关节"

先建模块依赖图（排除测试与相对导入噪声）：

| 维度 | 排行 |
|---|---|
| **fan-in 最高（被依赖最多）** | `af_ir`(20)、`af_time`(20)、`af_adapters`(12)、`af_state`(10)、`af_store`(9)、`af_conf`(8)、`af_audit`(7)、`af_flock`/`af_bus`(6) |
| **fan-out 最高（依赖别人最多）** | `af_cli.py`(25)、`af_service.py`(20)、`af_runtime.py`(12) |

**关节 = 高 fan-in 的抽象**（多人实现、多处调用，一旦实现不一致就全局分歧）。本轮据此验证 `af_time`(20) / `af_state`(10) / `af_adapters`(12) / `af_flock`(6)，外加 `af_ir/expr` 的短路语义。

---

## 缺陷（高）· 仿真与生产对同一 IR 给出相反结论

### 四个实现，两派策略

`StateProvider` 抽象下有 4 个实现，对「未知实体」的处理**截然相反**：

| 实现 | 位置 | 策略 | 后果 |
|---|---|---|---|
| `InMemoryStateProvider` | `af_state.py:112` | **抛异常**（源码注释：P1-12 fail-closed，不静默省略） | snapshot 阶段整段失败 |
| `FakeHA` | `af_vhass/fake.py:75` | **抛异常** | 同上（**`forge sim` 实际用的就是它**） |
| `HAStateProvider` | `af_adapters/ha.py:194` | **静默省略**（`if entity_id in all_states` 不在就跳过） | 返回部分快照 |
| `vhass bridge` | `bridge.py:35` | **静默省略**（`continue`） | 同上 |

**仿真侧两个都 fail-closed，生产侧两个都 fail-open。**

### 端到端实证

构造 IR 表达式 `or(is_on(motion), is_on(ghost))`，状态为 `motion=on`、`ghost` 不存在：

```
【仿真】FakeHA.snapshot()
   → 抛 UnknownEntity（snapshot 阶段整段失败）
   → 软失效，自动化【不执行】

【生产】HAStateProvider.snapshot()
   → 返回仅含 motion 的部分快照
   → or 用 any() 短路（af_ir/expr.py:483），ghost 分支永不被求值
   → 返回 True，自动化【执行】
```

**同一份 IR、同一份状态，仿真判"不执行"，生产判"执行"。**

### 为什么严重

1. **触发条件是 HA 常态** —— 实体漂移（entity_id 变更、设备下线）。项目自己有 `ENTITY_NOT_FOUND` 检查项，说明作者知道实体会漂移。
2. **打的是项目铁律** —— 「看到即跑的」。仿真的全部意义是预测生产行为，此处**预测与事实相反**。
3. **`forge sim` 正是用户批准前的验证手段** —— 用户在仿真里看到"不执行"而放心批准，上线后实际会执行。

### 根因拆解（三层耦合）

```
① 实现策略不一致（fail-closed vs fail-open）
② 失败时机不同（snapshot 阶段 vs get 阶段）
③ 表达式短路（any() 实现 or）—— 把 ② 的分歧放大成结论相反
```

单独看①②都不致命，**③让它变成"结论相反"而非"错误早晚"**。

### 修复方向

统一 4 个实现的行为。两个选项：

- **全部 fail-closed**（推荐）：符合项目已有的 P1-12 纪律，让缺失实体显式进入软失效路径
- **仿真复现生产的省略语义**：则需在表达式层显式处理"实体缺失"（如三态逻辑），不能靠静默省略 + 短路

---

## 本轮证伪（关节处大多是干净的）

### `af_time`(fan-in 20，全仓最高) —— 无缺陷

实测 `VirtualTimeSource`：
- `advance(100)` → Δmonotonic = Δwall = 100 ✅ 双时钟同步
- `jump(100)` → Δwall=100，Δmonotonic=0

`jump` 只动墙钟不动单调钟 —— 我一度判定为缺陷，核实 `af_time.py:191` 注释后确认是**有意设计**：

> 只动墙钟不动单调钟（NTP 校时 / 人为改时钟 / 夏令时切换）

设计目的正是让 `af_persist` 的定时器墙钟换算在时钟跳变后仍然正确。**不误报。**

### `af_state.Snapshot.get` 的"≥2 个点"判定 —— 无缺陷

| 输入 | 结果 |
|---|---|
| `light.x` | `on` ✅ |
| `climate.bedroom.temperature` | `22.5` ✅ |
| `climate.bedroom.missing` | `UnknownEntity` ✅ |
| `sensor.a.b.c` | `UnknownEntity` ✅ |

HA 的 entity_id 规范是 `domain.object_id`（domain 不含点），`rpartition` 按最后一点切分正确。

### `af_adapters` 契约 —— 一致

`ha` / `http` / `mock` 的 `call()` 全部返回 `CallResult.ok/fail`，不向上抛业务异常（仅未知适配器名在 `base.py:141` 抛 `AdapterError`）。

### `af_flock` —— 分工明确

`FileLock` 用于 `af_catalog`(3) / `af_experience`(2) / `af_live`(1)；`af_persist` 只用 `owner_id` 租约标记未用锁。

**差异有依据**：租约解决"恢复时谁可接管"（`claims()` 仲裁），锁解决"写入时互斥"。职责不同。且"单写者假设未代码化"已在第一轮架构评估记录，不重复计入。

---

## 七轮累积视图

| 轮次 | 面 | 核心发现 | 强度 |
|---|---|---|---|
| 一 | 全量 | `homesdk` 未声明、`instance.id`、canary 序列化 | **P0 ×2** |
| 二 | 生命周期 | SAFE HALT `logger` 未定义 → tick 线程死亡 | **高** |
| 三 | 表达式求值 | `min`/`max` 混类型绕过 soft-fail | **中高** |
| 四 | 静态闸/异常吞噬 | 目录截断、undo 策略不一致 | **中 / 低** |
| 五 | 处女地 偏好/预测/总线 | `PreferenceModel` O(N²) | **P1** |
| 六 | 处女地 evo/service | 会话清理只在读路径 | **中** |
| 七 | **接缝 / 关节** | **仿真与生产结论相反** | **高** |

### 本轮的独特价值

前六轮缺陷都在**模块内部**，本轮在**抽象与实现的交界**。

这类缺陷的特点是：**每个实现单独看都合理**（fail-closed 是纪律、fail-open 是健壮），**只有放在一起比对才暴露**。逐模块读代码永远发现不了——**必须先建依赖图定位高 fan-in 的抽象，再横向比对它的所有实现**。

### 跨七轮仍未修复（主要矛盾未变）

1. **`instance.id`（`af_scheduler.py:343`）** —— 连续六轮确认未修复，唯一确定的 P0
2. **设备目录截断（`af_orchestrator.py:916`）** —— 连续六轮
3. **门禁缺位** —— 前两轮那两个 bug 都是一行 linter 能拦住的
4. **"只增不减"一族** —— 已扩至 5 个模块（`AuditLog` / `bus._last_state` / `PreferenceModel._records` / `_SESSIONS`）
5. **本轮新增：实现间策略不一致** —— `StateProvider`(4 实现两派) + 第四轮的 `_undo`(3 域三种策略)。**同一抽象的多实现缺乏强制契约**

---

## 未覆盖声明

12 个单元中 **7 个 `out_of_scope` 未审**：HTTP 鉴权、MCP 授权码、`af_instance` 终态回收、`af_executor` canary、CLI 输入面、`af_audit` 写路径、`af_conflict` 冲突仲裁。

**下一轮建议**：按本轮的"接缝方法论"继续横向比对其余高 fan-in 抽象的实现一致性 —— 优先 `af_store`(fan-in 9) 与 `af_conf`(8)。
