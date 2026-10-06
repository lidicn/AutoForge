# AutoForge 架构评估与 Token 优化方案（沙箱实测版）

> 本轮全部结论均来自**沙箱内真实运行**，非静态推断。环境：Python 3.10 + homesdk 0.1.1（`--ignore-requires-python` 强装）。
> 测试基线实测：**1276 passed / 51 skipped / 1 failed**（失败项为 MCP stdio 测试，经复现确认是**环境依赖**——子进程未继承 pytest 的 sys.path，非代码缺陷）。

---

## 一、本轮新增实测确认（9 个，全部可复现）

| # | 缺陷 | 复现证据 |
|---|---|---|
| **P0-2** | `persist_dir` + time 触发必崩 | `AttributeError: 'Instance' object has no attribute 'id'`；**对照组（persist=None）正常点火 1 个实例** → 精确锁定只在 persist 分支 |
| **P1-1** | 终态实例永久滞留 | 触发后字典 1 条 / 终态 1 条 / 活跃 0，`remove()` 全仓零调用 |
| **P1-2** | `for` 到期复查未捕获 `UnknownEntity` | 实体消失后 `advance(700)` → `UnknownEntity` 抛出，**整个 tick 失效** |
| **P1-3** | 总线 handler 无异常隔离 | `publish()` 抛出 `RuntimeError`；**后注册的正常 handler 收到 0 条事件**（应为 1）→ 订阅者被连坐 |
| **P1-4** | canary 上下文不可序列化 | `to_dict()` 不抛异常，`(CanaryResult, Adapter)` → `['<obj at 0x...>', '<obj at 0x...>']`，恢复后 `has_drift()` → `AttributeError` |
| **P1-5** | emit 完全不受熔断保护 | 连续 60 次同名 emit → `accepted=60, throttled=0, breaker_open=0` |
| **P1-6** | emit 冲掉实体事件去重 LRU | 先放 1 条实体事件 → 5000 次 emit → 越过节流窗口后重放同一三元组 → **`accepted`（应为 `duplicate`）** |
| **P2-1** | `wait` 非法 duration 抛穿 | `ValueError: 无法解析时长字面量：'abc'`，异常穿透 `run()`，实例丢失而非走 `on_error` |
| **P2-7** | `stats()` 治愈熔断（副作用） | 冷却到期后调 `stats()` → `_open_until` 1→0、`_changes` 20→0 |
| **NEW** | 设备目录 `[:120]` 静默截断 | `af_orchestrator.py:916`，**无排序、无 `truncated` 回报** |

### P1-6 补充说明（一个被节流"意外掩盖"的真实缺陷）

首次测试返回 `throttled` 看似"未复现"，但**正确的期望值是 `duplicate`**——说明去重记录确实被冲掉了，只是恰好被 200ms 节流窗口拦住。推进时间越过窗口后重放，返回 `accepted`。这意味着：**HA 重连/重放场景下（间隔 > 200ms）会重复触发自动化**，节流只是运气好。

### P2-7 补充说明

首次测试未复现是因为冷却未到期。等冷却过去后调用 `stats()`，熔断状态与计数被清空——一个**只读观测接口产生了写副作用**，会让监控系统"治愈"它正在观测的故障。

### 关于那条 failed 测试

`test_af_mcp.py::test_stdio_protocol_roundtrip` 依赖 `autoforge` 已 `pip install -e` 到环境（子进程继承 `os.environ`，**不继承 pytest 插入的 `sys.path`**）。我在沙箱补上 `PYTHONPATH` 后，MCP server 正常返回 `protocolVersion: 2024-11-05`。**不是代码 bug，但属于测试可移植性缺陷**——用 `PYTHONPATH=src` 跑测试的开发者/CI 会遇到假失败。

---

## 二、架构评估：内核优秀，外围失控

### 2.1 量化数据（AST 全量分析，88 模块 / 34,893 行）

```
循环依赖组：0          ← 罕见且优秀
fan-in 最高：af_ir(20)、af_time(20)、af_conf(14)、af_adapters(12)
fan-out 最高：af_cli(24)、af_service(20)、af_runtime(13)、af_executor(9)
超大模块：af_orchestrator 2460 / af_service 2102 / af_evo 1475 / af_cli 1352
          af_catalog 1189 / af_scanner 1181 / af_api 1062
```

### 2.2 判断

**内核是好的，甚至超出预期**——这是我这轮最想强调的正面结论：

- **零循环依赖**，88 个模块里一条环都没有，这在 3.5 万行的项目里很少见；
- **依赖方向正确**：fan-in 集中在 `af_ir` / `af_time` / `af_state` 这些**最稳定、最该稳定的内核原语**上，符合"依赖指向稳定"的稳定依赖原则；
- `af_executor` 只有 9 个外部依赖却承载全部求值语义——边界守得住。

**问题全在外围**：`af_cli`(24) / `af_service`(20) 两个模块吃下了近一半的耦合面，`af_orchestrator` 2460 行、`af_service` 2102 行。这是典型的**"干净内核 + 泥球外围"**——不是设计能力问题，是**缺少分层约束**：内核有 KICKOFF 铁律守着，外围没人守。

**结论：不需要重写，需要划层。** 建议四层：

```
af_core/        IR · time · state · bus · instance · executor · scheduler   （冻结，只允许加测试）
af_orchestration/  NL 编排 · 意图解析 · 闭环修复
af_service/     会话 · 目录 · 治理 · 导出
af_adapters/    HA · HTTP · Mock · vhass
```

用 `import-linter` 或 ruff `tidy-imports` 把"服务层不许反向依赖编排层""编排层不许碰 FastAPI"写成**可执行的契约**，而不是靠 review 记忆。零循环依赖这个底子很好，别让它在下一轮迭代里被破坏。

### 2.3 三个真正的设计缺陷（不是编码 bug）

**① 单写者假设从未被代码化 —— 这是投产的第一拦路石**

`EventBus` / `InstanceManager` / `AuditLog` 全是裸 dict/list，无锁、无界、无进程外可见性。但 `forge serve` 与 `forge watch` 是**两个进程**，靠 `pending_asks.json` 这类 sidecar 文件做 IPC。

`af_flock` 已经写好了跨进程锁原语，却只用在了 `GraphStore` 和 `PersistStore`，**没用在 Runtime 上**。于是"谁是唯一写者"这件事目前只存在于文档和默契里。投产必然遇到：事件双投/漏投、两进程各跑一份实例、状态分叉。

> 建议二选一并**写进代码**：(a) 单写者 + 热备——Runtime 启动用 `FileLock` 抢"唯一写者"租约，抢不到就降级为只读观察者；(b) 引入事件日志做真正的多写者协调。选 (a) 成本低一个数量级，够用。

**② "Agent 为中心"与"确定性内核"之间存在未收口的张力**

项目铁律是「Graph 唯一真相 + NL 确定性渲染」。但 `IntentParser` 让 LLM **直接生成完整 IR 表达式树**（`INTENT_SYSTEM` 里连 `{"op":"gt","left":{"var":"entity.@temp","type":"numeric"}...}` 都由 LLM 产出）。

这里的问题是双向的：**既浪费 token，又引入不确定性**——LLM 要同时做"听懂人话"和"精确生成结构化表达式"两件事，而后者恰恰是项目自己花大力气建静态闸去防的东西。你等于让 LLM 造错误，再用扫描器拦错误。

> 正确姿势：**槽位填充（slot filling）**。LLM 只输出 `{房间, 设备, 属性, 比较符, 阈值, 动作}` 这类**语义槽位**，IR 表达式由确定性模板拼装。LLM 的输出面从"任意表达式树"收窄到"几个枚举 + 一个数字"，token 降一个量级，静态闸的失败率也会跟着降——因为错误源头被掐掉了。

**③ 没有事件日志 —— 无法回答"昨晚 8 点灯为什么亮了"**

`AuditLog` 是内存数组（无上限、不落盘）。对"Agent 写的自动化误动作"这类高敏感事件，**没有任何回溯手段**：当时快照是什么、命中了哪条边、为什么这么判定，一概不知。

这一条同时卡住了三件事：可观测、崩溃恢复（现在是"尽力而为"）、单写者一致性。

---

## 三、建议新增的模块（按 ROI 排序）

### 🥇 `af_eventlog` —— 结构化事件日志（WAL）

**一个模块同时解决三个问题**，是本轮最高 ROI 建议：

- **可观测**：每次求值段落一条 `EvaluationRecord`（快照摘要 + 命中边 + 结果 + 耗时 + 版本），滚动 journal；
- **崩溃恢复**：当前 `restore_persisted` 是"尽力而为"，有日志后可做到**确定性重放**；
- **多进程一致性**：事件日志天然是单写者模型的载体（append-only + 租约）。

设计要点：append-only、原子写（复用已有的 `_atomic_write`）、按天滚动、保留期可配。

### 🥈 `af_index` —— 设备目录检索层

替代 `catalog[:120]` 全量塞 prompt。本地做 BM25/别名/区域预筛，只把 **top-15** 相关设备送 LLM。这是省 token 的核心，见第四节。

额外收益：现在 `[:120]` 是**按 catalog 原始顺序截断**，既无相关性排序也无 `truncated` 回报（与项目 v1.1.0 自己定的"列表强制分页 + 透明回报 `truncated`/`next_offset`"纪律相矛盾）。检索层顺手把这个纪律补上。

### 🥉 `af_slotfilling` —— 槽位填充层

见 2.3 ②。把 LLM 输出面从"生成表达式树"收窄为"填槽位"。

### 其他值得做的

| 模块 | 价值 |
|---|---|
| `af_golden` | NL 渲染 golden-file 回归——IR schema 一改就跑全量存量图的 NL 快照，**防止"你批准的那句话"被渲染器悄悄改写**。这是项目铁律的守卫，目前没人守 |
| `af_reaper` | 终态实例延迟摘除 + 审计 deque 上限 + emit deque 上限（治理 P1-1） |
| `af_blastradius` | 变更爆炸半径可视化（改这条自动化会影响哪些设备/哪些其他自动化） |
| `forklift: 单写者租约` | 把 `af_flock` 用在 Runtime 入口（见 2.3 ①） |
| `forge ps` | 实例池视图：谁在跑、卡在哪、挂了多久、TTL 剩余 |

---

## 四、Token：会浪费，但浪费在"错的地方"

### 4.1 实测数据

| 项 | 实测值 |
|---|---|
| `INTENT_SYSTEM` | 938 字符 ≈ **325 token** |
| 设备目录 30 条 | 单次意图解析 ≈ **1265 token** |
| 设备目录 120 条（当前硬顶） | ≈ **3984 token** |
| 设备目录 300 条 | ≈ **9434 token** |
| `HeuristicIntentParser` 独立覆盖率 | **3/10 = 30%**（10 条常见语句实测） |

### 4.2 浪费在哪

**不是"LLM 用多了"，是"把不该给 LLM 的东西给了 LLM，让 LLM 干了它不该干的精确活"。**

1. **全量设备目录无条件入 prompt**：无论用户说"开灯"还是"书房太暗就开空调"，都塞 120 条设备（3659 token）。典型家庭 HA 实体 300~1500，而当前硬顶 120 且**静默截断**——意味着 LLM 只能看到 8%~40% 的设备，还在为看不全的部分付 token。

2. **让 LLM 生成 IR 表达式树**：这是精确的、有 schema 的、机器更擅长的工作。LLM 生成 → 静态闸拦截 → 闭环修复重发，**一轮修一次，每轮都重带全量目录**。

3. **12 阶段流水线**（`intake→recommend→clarify→resolve→draft→compile→build→simulate→score→confirm→save`）多轮 LLM，每轮都可能重新装配上下文。

### 4.3 省 token 五招（按 ROI 排序）

**① 两阶段检索（省 ~70%）**
本地 `af_index` 预筛 top-15，只送 15 条给 LLM。
> 3984 → 约 **1100 token**，单次省 **~2900 token**。且召回质量**上升**（相关设备一定在 15 条里，而现在是按原始顺序截断）。

**② Prompt Caching（省 ~90% 的重复部分）**
`INTENT_SYSTEM`(325) + 设备目录前缀是**会话内稳定的**。用 Anthropic/OpenAI 的 prompt caching，第二次起前缀命中缓存（读取成本约为常规输入的 10%）。一次会话多轮对话时，这一招比①还猛。

**③ 槽位填充替代全量生成（省输出 + 提准确率）**
LLM 输出从"完整表达式树 JSON"变成"几个枚举 + 一个数字"。输出 token 大幅下降，且**静态闸失败率下降 → 闭环修复轮次下降 → 间接省掉整轮重发**。这是唯一一招同时省 token 和提质量的。

**④ 启发式优先（30% 请求 0 token）**
`HeuristicIntentParser` 已存在且实测能独立处理 30% 的常见语句。当前是"LLM 优先、失败才 fallback"，应改为：**启发式先跑，置信度足够就直接返回，置信度低才上 LLM**。这 30% 的请求 token 成本直接归零。

**⑤ 会话内目录只送一次**
同一会话多轮追问时，设备目录不重复装配（配合 ② 的缓存，成本近乎为零）。

> **组合效果估算**：常见"一句话建自动化"场景，从当前约 **4000+ token/次**，降到 **300~800 token/次**，降幅 **80%+**；且解析准确率与静态闸通过率同步上升。

---

## 五、关于"还能找到更多 bug 吗"

**能。** 本轮已从"静态推断"切换到"运行时实证"，9 个缺陷全部拿到可复现证据，其中 **P1-6、P2-7 是靠修正实验设计才挖出来的**（初版实验因节流窗口/冷却期未过而假阴性）。

尚未实测、但沙箱内**可以继续挖**的方向：

1. **并发与 IPC 专项**：双进程同时 `forge watch` 写同一 store、sidecar 文件竞争——2.3 ① 的实证；
2. **崩溃恢复专项**：`persist=true` + canary + time trigger + 实体消失的组合（P0-2 与 P1-4 已在其中）；
3. **表达式 fuzz**：对 `af_ir/expr.py` 做 property-based 测试，找类型守卫的边界（P2-2 时区问题尚未实测）；
4. **API 鉴权实测**：起 FastAPI + TestClient，逐个端点验证 S-1 的"读端点 fail-open"清单；
5. **类型检查**：接入 pyright/mypy，本项目零类型错误的比例需要实测确认。

需要我接着挖哪些，说一声即可——前两项（并发、崩溃恢复）是投产前最该补的实证。
