# AutoForge 代码审计报告

- **审计对象**：`lidicn/AutoForge`（`main` 分支，README 标注 `v1.7.1`，`v2.0.1` 投产收口进行中）
- **代码规模**：225 个 Python 文件 / 57,624 行（含 tests），`src/autoforge` 约 40k 行、75 个模块
- **审计方式**：全量静态分析（ruff：F/E/B/ASYNC/S/TRY/SIM/RET 等规则集）+ 核心链路人工走查 + 依赖与导入实证
- **重点链路**：`af_ir → af_time/af_state → af_bus → af_instance → af_executor → af_scheduler → af_runtime → af_persist → af_adapters → af_api/af_auth`
- **审计局限（务必知悉）**：
  1. `homesdk` 为私有外部包（仓库内仅 `docker/homesdk/homesdk-0.1.1-py3-none-any.whl`），未安装环境下 `af_executor`/`af_runtime` 无法导入，因此**未能执行运行时回归**（详见 P0-1）；
  2. 项目要求 Python 3.14（pytest-homeassistant），审计环境为 3.10，**未跑 529 条测试基线**；
  3. 结论均基于源码可验证事实，行号对应审计时的 `main` 快照。

---

## 0. 结论速览

| 严重度 | 数量 | 代表问题 |
|---|---|---|
| **P0 阻断** | 2 | `homesdk` 未声明依赖导致核心模块不可导入；`--persist-dir` 下时间触发必抛 `AttributeError` |
| **P1 稳定性** | 8 | 实例/审计/事件队列**无界增长**；`for` 到期复查未捕获 `UnknownEntity` 致 tick 崩溃；总线回调无异常隔离；canary 上下文混入不可序列化对象 |
| **P2 功能性/正确性** | 9 | `queued` 语义偏离；`time_between` 时区未归一；HA 读失败静默降级为"实体不存在"（fail-open）；热路径调试 `print` 残留 |
| **安全/鉴权** | 4 | 约 20 个读端点**鉴权 fail-open**；CORS 方法与 DELETE 路由不匹配；HA Token 可明文 http 发送 |
| **架构/工程** | — | 单进程内存态模型与"投产"目标错配；模块边界外溢（75 模块 / 6 个 800+ 行巨型模块）；observability 靠 stderr |

**总体判断**：**内核（IR / 时间源 / 状态机 / 静态闸 / 表达力收口）设计质量明显高于同类项目水平**——fail-closed 纪律、两种 Timer 隔离、monotonic/墙钟分离、序列化自检都做得扎实。真正拖累投产的是**工程卫生层**：一个未声明的硬依赖、一批无界容器、一套"手工逐个端点加鉴权"的模型，以及调试脚手架未清理。**内核不需要重写，需要的是一次"投产收口"级别的工程加固**（正好是 v2.0.1 的主题）。

---

## 1. P0 阻断级缺陷

### P0-1 `homesdk` 是硬依赖却未声明 —— 干净环境 100% 不可导入

- **位置**：`src/autoforge/af_executor.py:62`（模块顶层 `from homesdk.consent import ...`）、`pyproject.toml:11-14`
- **实证**：

  ```
  FAIL autoforge.af_executor -> ModuleNotFoundError No module named 'homesdk'
  FAIL autoforge.af_runtime  -> ModuleNotFoundError No module named 'homesdk'
  OK   autoforge.af_instance
  OK   autoforge.af_time
  ```

- **为什么是 P0**：`af_executor` 是求值引擎、`af_runtime` 是组装入口，二者不可导入 = **CLI 全命令、MCP、API、watch 全链路不可用**。`pyproject.toml` 的 `dependencies` 只有 `jsonschema`/`typer`，`homesdk` 既不在 `dependencies` 也不在任何 extra；README「快速开始」只写 `pip install -e ".[dev]"`，**新克隆者必然失败**，Dockerfile 也必须靠手工塞 wheel 才能工作。
- **附加风险**：把一个**语义判定（yes/no 同意词表）**放进不可控的外部包，等于把 IR §5.2 的行为契约外包；外部包版本漂移会直接改变自动化走向（`classify_answer` 的 unknown→no 映射是安全收紧语义）。
- **修复建议**（三选一，推荐 a）：
  - (a) 声明依赖：`dependencies += ["homesdk>=0.1.1"]`（私有源用 `dependency-links` / `pip --find-links docker/homesdk`），并在 CI 加一条**干净 venv 的 import smoke**：`python -c "import autoforge.af_runtime"`；
  - (b) 若必须保持零外部依赖：改为**延迟导入 + 内置降级词表**，`homesdk` 缺失时用本地表并记录 `consent_fallback` 审计事件（绝不能静默）；
  - (c) 把 `consent` 判定收口进 `af_ir` 层，做成可测的纯函数，外部包只作为"可替换实现"。
- **回归要求**：新增契约测试钉住 `unknown → no`，无论走哪条实现路径。

### P0-2 `--persist-dir` 下时间触发必崩：`instance.id` 不存在

- **位置**：`src/autoforge/af_scheduler.py:343`（`lease.confirm(instance.id)`）；`Instance` 仅暴露 `instance_id` property（`af_instance.py:137-139`），**无 `id` 属性**
- **触发条件**：`persist_dir is not None` → `af_runtime.py:83-87` 注入 `FireRecorder` → 任何 `type: "time"` 触发成功点火
- **影响链**：`AttributeError` 从 `_fire_time_triggers` 冒泡到 `Scheduler.tick()` → 整个 tick 失败（实例超时、`for` 到期、队列出队**全部**连带失效）；`af_tick_supervisor` 按连续异常判定后可能进入 **SAFE HALT**，即 `forge watch --persist-dir`（正是生产推荐形态）**整条常驻链路停摆**。
- **为什么没被测出来**：内存态（无 persist）走的是 `else` 分支（351-355 行），带 persist 的分支缺少"time trigger + recorder"组合用例。
- **修复**：`instance.id` → `instance.instance_id`；并在 `FireRecorder` 侧做 `getattr` 防御。补一条集成测试：`persist_dir` + 虚拟时钟跨过 `at:` 时刻 + 断言实例被点火且无异常。
- **附带建议**：给 `Instance` 加 `__getattr__` 兜底或直接用 `@property id` 别名，消除这类"看起来该有其实没有"的属性陷阱。

---

## 2. P1 稳定性缺陷

### P1-1 三处容器只增不减 —— 常驻进程内存单调增长

- **位置**：
  - `af_instance.py:298` `InstanceManager.remove()` **在 `src/` 下无任何调用点**（已全量 grep 确认）；`expire_stale()`（324-334）只改状态为 `expired`，**不摘除字典条目**；
  - `af_audit.py:90-97` `AuditLog.events` 无上限（且 `Runtime.stats()` 每次 `for e in self.audit` 全量序列化，随运行时间线性变慢）；
  - `af_bus.py:154` `self.emitted` 无上限（每条 emit 追加一个 `BusEvent`）；
  - `af_scheduler.py:71/354` `_time_fired` 集合按 `(auto,node,date)` 累加，跨天不清理。
- **影响**：`forge watch` 是月级常驻进程。以每天千级事件计，实例字典/审计数组/emit 列表持续增长 → RSS 单调上升、GC 压力上升、`stats()` 与 `due_timers()` 遍历成本上升，最终 OOM 或接口超时。
- **修复**：
  1. `expire_stale()` 内对终态实例调用 `remove()`（先落审计再摘除）；或在 Runtime 增加"终态实例延迟 N 秒摘除"的 reaper（保留可观测窗口）；
  2. `AuditLog` 改为 `deque(maxlen=N)`（如 5000）+ 可选落盘（journal 文件，滚动）；
  3. `bus.emitted` 改 `deque(maxlen=...)`；`_time_fired` 按日期分桶，跨天清理昨日桶；
  4. 给 `stats()` 加上限（审计只返回最近 K 条），避免观测接口自身成为性能杀手。

### P1-2 `for` 持续条件到期复查未捕获 `UnknownEntity`，会拖垮整个 tick

- **位置**：`af_scheduler.py:287-300`
  ```python
  snapshot = self.states.snapshot([pending.entity_id])   # ← 在 try 之外
  try:
      return snapshot.get(pending.entity_id) == pending.to
  except KeyError:
      return False
  ```
- **事实**：`InMemoryStateProvider.snapshot()` 对未知实体 **raise `UnknownEntity`**（`af_state.py:112-118`），而 `Snapshot.get()` 的异常虽是 `UnknownEntity(KeyError)` 子类（可被 `except KeyError` 捕获），**但构造快照这一步在 try 之外**。
- **触发**：自动化引用了已被删除/改名的实体（或 HA 侧实体短暂消失），且该节点带 `for` 条件 → 到期时刻 `tick()` 抛出 → 同批次的实例超时、定时触发、队列出队全部丢失。
- **修复**：把 `snapshot()` 调用移入 `try`，捕获 `(UnknownEntity, KeyError)` 返回 `False`；同时补一条"实体消失后 for 到期"的回归测试。

### P1-3 事件总线回调无异常隔离 —— 一个订阅者炸掉整条分发链

- **位置**：`af_bus.py:291-295`
  ```python
  for handler in list(self._subs.get(key, ())):
      handler(event)     # 无 try/except
  ```
- **影响**：任一 handler（自动化触发逻辑）抛异常 → `publish()` 抛出 → 上层 `Runtime.publish` / SSE 回调 / `tick` 崩溃；且**排在该 handler 之后的订阅者全部收不到事件**（顺序相关、难复现的"偶发不触发"）。
- **修复**：逐个 handler 包 `try/except Exception` + 审计事件 `handler_failed`（带 handler 标识），保证"一个自动化写错不影响其他自动化"——这与项目"自动化之间隔离"的设计意图一致，当前实现与之相悖。

### P1-4 canary 上下文混入不可序列化对象，崩溃恢复后自动回滚静默失效

- **位置**：`af_executor.py:456`（`instance.ctx.context["pending_canary"] = (wrapped, adapter)`）；恢复侧 `af_persist.py:84-113` + `af_store.py:568-584`（`context` 原样还原）
- **事实链**：
  1. `ctx` 的红线是"必须可 JSON 序列化"（`af_instance.py:7`），但 `Instance.to_dict()` 用 `json.dumps(..., default=str)`（`af_instance.py:160`）——**`default=str` 把不可序列化对象静默降级成字符串，使红线自检形同虚设**；
  2. 落盘后 `pending_canary` 变成 `["<CanaryResult object>", "<HAAdapter object>"]`；
  3. 恢复后 `resume()` 解包得到 `str`，`wrapped.has_drift()` → `AttributeError`；
  4. 该异常被 `af_executor.py:192-194` 的 `except Exception: logging.exception` 吞掉 → **canary 漂移回滚静默不生效，且无人知晓**。
- **影响**：G4 灰度保护是"真机安全"的关键兜底；在 `persist=true` + 崩溃恢复路径上它已经失效。这是**安全相关**的稳定性缺陷。
- **修复**：
  1. `ctx.context` 只存**数据**：`{"canary": {"action":..., "params":..., "before": {...}, "node":...}}`，恢复时用 adapter 注册表重建 guard；
  2. `to_dict()` 去掉 `default=str`，改为**严格 `json.dumps`**（让混入库对象时立刻炸在测试里，而不是上线后静默降级）——这才是红线自检该有的样子；
  3. 把 `except Exception: logging.exception` 换成 `except Exception` + 审计事件 + 显式降级（回滚失败必须可被观测）。

### P1-5 自定义事件（emit）实际不受熔断保护，且与注释自相矛盾

- **位置**：`af_bus.py:165-195`（`publish` 对 `event.event is not None` **跳过** `_record_change`/熔断/节流）vs `af_bus.py:209-215`（`publish_custom` 注释声明"事件风暴由熔断兜底"）
- **影响**：注释承诺的保护**不存在**。Agent 生成的图若形成 `A → emit → B → 状态变更 → A` 的闭环，或 emit 在高频触发里被反复发布，**没有任何速率上限**；同时 `emit` 是"内部信号"这个假设在跨自动化场景下并不成立（订阅者是任意自动化）。
- **修复**：二者取一并写死语义——建议对**事件名维度**单独做熔断（key = `event.<name>`，阈值可高于实体事件），避免过度自伤的同时保留风暴兜底；并让注释与实现由同一份测试钉住。

### P1-6 去重缓存被自定义事件污染，实体事件去重窗口被冲掉

- **位置**：`af_bus.py:99-105`（自定义事件 dedup_key 含 `event_id`，天然唯一）+ `246-254`（`_seen`/`_seen_order` 为**共享**的 4096 LRU）
- **影响**：一次 emit 风暴即可把 4096 条去重记录全部换成自定义事件键 → 实体状态事件的去重记录被挤出 → 同一 `(entity_id, state, last_changed)` 再次到达时被判为新事件 → **重复触发自动化**（HA 重连重放场景下尤其明显）。
- **修复**：`_seen` 按 `source`/事件类型分桶（实体事件桶、自定义事件桶各自限额），或自定义事件键不入 LRU（其唯一性决定它本就不需要去重）。

### P1-7 熔断/节流参数从环境变量读取但无容错

- **位置**：`af_bus.py:131-142`
  ```python
  int(os.getenv("AUTOFORGE_BREAKER_THRESHOLD", "12"))
  float(os.getenv("AUTOFORGE_BREAKER_WINDOW_S", "10"))
  ```
- **影响**：运维把环境变量写成 `12s`/空串/超范围值 → 构造 `EventBus` 时 `ValueError` → **服务起不来**（且发生在启动路径，不是运行路径）。
- **修复**：统一一个 `_env_float/_env_int(name, default, lo, hi)` 工具：解析失败/越界 → 记录 warning + 回落默认值（fail-safe 而非 fail-crash），并对齐项目其它 env 读取点（同类模式在仓库里不止一处）。

### P1-8 热路径残留调试 `print`，且带 `flush=True`

- **位置**：`af_executor.py:401/404`（**每个 `if` 节点求值一次**，打印整条表达式）、`af_scheduler.py:106/109`（每 tick 两次）、`af_bus.py:200`（每次丢弃）、`af_live.py:286`、`af_adapters/ha.py:247`、`af_cli.py:318`
- **影响**：
  1. 常驻进程下 stderr 同步 I/O + flush，高频时是实打实的吞吐损耗；
  2. 日志无法分级/关闭/结构化，与 `logging` 双轨；
  3. `af_executor.py:401` 打印 `expr` 全内容，可能包含用户房间/设备语义，属于**不受控的信息出口**；
  4. 每类 print 都说明"这条路径当时没被观测手段覆盖"——是脚手架，不是日志。
- **修复**：全部替换为 `logging.getLogger(__name__).debug(...)`；在 CI 加 ruff `T20`（no-print）门禁，只允许 `af_cli` 的用户输出走 `typer.echo`。

---

## 3. P2 功能性与正确性缺陷

### P2-1 `set` / 挂起节点缺软失效，异常穿透 `run()`

- **位置**：`af_executor.py:569-572`（`parse_duration(node.duration)` 未捕获）、`410`（`set_var` 的 `TypeError` 未捕获）、`616-620`（`_value_of` 取值失败未捕获）
- **对比**：`if` 节点（398-405）与 `do` 节点（420-423）都有 `→ _soft_fail → on_error` 的软失效路径，`set`/`wait`/`ask` 却会直接抛穿 `run()`。
- **影响**：一个 `duration: "abc"` 或 `vars.x` 类型不符，不是走 `on_error` 兜底，而是**实例连同整条触发一起丢失**（异常向上传播到 `Scheduler`/`Runtime`），与"失败落执行器"的设计（G5）不符。
- **修复**：把 `_execute` 整体纳入统一的软失效包装：`except (ValueError, TypeError, KeyError, ExprError, UnknownEntity) → _soft_fail`。

### P2-2 `time_between` 未做时区归一，跨时区家庭时间窗整体偏移

- **位置**：`af_ir/expr.py:162-170`（`moment.hour * 60 + moment.minute`）
- **问题**：`_as_iso` 解析出的 datetime 保留原始 offset，`.hour` 是**该 offset 下的小时**。用户写 `22:00-07:00` 指本地时间，但传入的 ISO 若带 `Z`/`+00:00`（HA 常见），在 +08:00 环境下窗口整体偏移 8 小时。这与项目自己在 B3-AF-04 上确立的"本地时间口径"不一致（`af_time.py:103-107` 已明确 `matches_at` 只接受本地时间）。
- **修复**：`time_between`/`time_hour`/`time_minute` 统一用与 `TimeSource.local_now()` 相同的时区做归一；或给函数增加显式 `tz` 参数（默认取运行时时区）。补跨时区用例（`Z` 时间戳 + `Asia/Shanghai` 运行时）。

### P2-3 `queued` 模式语义近似 `parallel`，与 HA 不一致

- **位置**：`af_scheduler.py:170-177`（只有 `len(active) >= per_automation` 才入队）
- **问题**：HA 的 `queued` 是"有实例在跑就排队，串行执行"；此处前 `per_automation`(默认 10) 个触发**并发直行**，等价于 `parallel`。若这条偏离未在 `docs/HA_SEMANTIC_DIFF.md` 记录，属于**语义契约缺口**——用户按 HA 心智模型配 queued 期望串行，实际拿到并发。
- **修复**：二选一并文档化：严格串行（`if active: 入队`）或保留现状但在 `HA_SEMANTIC_DIFF.md` 明确列出 + NL 渲染中可见（项目已承诺"8 处有意偏离必须在 NL 文本可见"）。

### P2-4 debounce 丢弃事件无任何审计

- **位置**：`af_scheduler.py:149-154`（`return None`，无审计、无计数）
- **影响**：“我明明按了开关却没反应”类问题无法自证——`rejections` 里没有、审计里没有。与项目"拒绝必须可观测"的纪律（`QUOTA_EXCEEDED`/`INSTANCE_REJECTED` 都有审计）不一致。
- **修复**：补 `INSTANCE_DEBOUNCED` 审计事件 + 计数，并在 `stats()` 暴露。

### P2-5 HA 读状态失败被静默降级为"实体不存在"（fail-open）

- **位置**：`af_adapters/ha.py:141-160`（`get_state` 失败返回 `None`、`all_states` 失败返回 `{}`）、`194-204`（`HAStateProvider.snapshot` 对缺失实体仅不进快照）
- **影响**：HA 重启/网络抖动/401 时，快照里该实体"消失" → 表达式按 `UnknownEntity` 软失效，或 `not_is_on` 判定为真 → **在状态未知时执行动作**（例如以为灯是 off 而去开灯）。对"真机下发"场景这是 fail-open，方向与项目整体的 fail-closed 纪律相反。
- **修复**：区分三类结果：`ok` / `entity_missing` / `read_failed`。`read_failed` 必须**阻断**该次触发（走 `on_error` 或拒绝点火）并落审计，而不是当成"实体不存在"。

### P2-6 每次快照都全量拉取 HA 状态，性能随房间规模放大

- **位置**：`af_adapters/ha.py:194-204`（`snapshot()` → `transport.all_states()` → `GET /api/states` 全量）
- **影响**：每个实例 spawn/resume 各一次全量拉取（大户 1000+ 实体的 JSON），`restart` 模式或高频触发下对 HA REST 是放大攻击；同时一次 HTTP 往返进入**求值段同步路径**，直接拉长实例时延。
- **修复**：`HAStateProvider` 增加短 TTL 缓存（如 1s，且必须走 `TimeSource` 以便仿真可测）+ 按批 `GET /api/states/{entity_id}` 兜底 + `has()` 实现（让 `af_instance._refresh_snapshot` 的 `hasattr(states,'has')` 分支能生效）。

### P2-7 `is_open()` 在观测路径上产生副作用

- **位置**：`af_bus.py:223-240`（`is_open` 会删除熔断状态、清理计数、写审计）；`stats()`（297-302）调用 `open_entities()` → 逐个 `is_open()`
- **影响**：调一次监控接口就可能"治愈"熔断（副作用），且 `stats()` 与语义判断耦合。修复：拆分 `is_open`（纯查询）与 `_maybe_recover`（副作用），`stats()` 只走纯查询。

### P2-8 未定义名 / 重复定义（类型检查与 `import *` 的雷）

- `af_flock.py:58` `Mapping` 未导入（注解为字符串，运行时不炸，但 `get_type_hints()`/文档生成会炸）
- `af_live.py:174` `Config` 未导入（同上）；`af_live.py:363` 使用的 `logger` 定义在同文件 **383 行**（先使用后定义，一旦该函数在模块加载早期被调用即 `NameError`）
- `af_draft.py:24` `__all__` 含未定义的 `DraftResult` → `from af_draft import *` 直接 `AttributeError`
- `af_api.py:510 / 536` 同名函数 `api_asks` 定义两次（FastAPI 路由不受影响，但源码层前者被覆盖，易误改错函数）
- **修复**：引入 `ruff` 的 `F` 规则进 CI 门禁（当前 `.gates.toml` 只跑 `homesdk.gates`），一次性清零。

### P2-9 静态闸未校验 operand 类型声明与实体存在性

- **位置**：`af_ir/expr.py:313-340`（`check_expr` 只校验形态/白名单/参数个数）
- **影响**：`{"var":"entity.sensor.temp"}` 未声明 `type: numeric` 参与 `lt` 比较时，`build` 期绿灯，运行期才 `ExprError` → 软失效。安全闸的价值在于"拦截即出局，不进仿真"，这里漏了一类可静态发现的错误。
- **修复**：`check_expr` 增强——比较算子两侧若缺少 `type` 声明且一侧是 `entity.*` 引用，直接报 `EXPR_INVALID`（或 warning 级 `TYPE_UNDECLARED`）；与 `af_scanner._check_expr` 联动，把已实现的实体解析能力（`af_catalog`）用上。

---

## 4. 安全与鉴权

### S-1 读端点鉴权 **fail-open**（最高优先级安全问题）

- **位置**：`af_api.py:331-341`（app 级 `dependencies` 只有 `_rate_limit_dep`，**只限速不鉴权**）；`requires(scope)` 只在**逐个端点手工加**
- **事实**：已加 `_read` 的 GET 仅 6 个（`/api/asks`、`/api/asks/{name}`、`/api/sessions*`、`/api/user/*`）。以下端点**在任何配置下均匿名可达**（只受限速约束）：
  `/api/health`、`/api/graphs`、`/api/graphs/{name}`、`/api/store/export`（**导出全量自动化**）、`/api/conf/{name}`、`/api/metrics`、`/api/experience`、`/api/experience/export`、`/api/telemetry`、`/api/diff`、`/api/spec/{name}`、`/api/asks/pending`（**含 ask prompt，可能含房间/人物语义**）、`/api/faults`、`/api/catalog`、`/api/catalog/aliases`、`/api/entities*`、`/api/watch/list`、`/api/mcp/pair-request`
- **与宣称不符**：README v2.0.1 主题是"鉴权 fail-closed"，写侧（`_write`/`_live`）确实做到了 fail-closed（`af_api.py:265-279`：无令牌即 403），但**读侧是 opt-in**，漏一个就裸奔，且已经漏了近 20 个。
- **修复（推荐）**：**默认 deny + 显式 public 白名单**——在 app 级 `dependencies` 注入 `_read`（或 `authenticated(required=True)`），仅对 `/api/health`、`/api/auth/*` 等少数端点显式豁免；新增端点"忘记加鉴权"从"泄露"变成"403"（安全方向正确）。
- **加固**：`/api/store/export`、`/api/telemetry`、`/api/asks/pending` 即便鉴权通过也应视敏级提权到 `write`/`admin` scope。

### S-2 CORS 允许方法与 DELETE 路由不匹配（功能性 + 安全）

- **位置**：`af_api.py:342-350`（`allow_methods=["GET","POST","OPTIONS"]`）vs 实际存在 DELETE 路由：`656` `/api/sessions/{id}`、`860` `/api/user/auth-code/{code}`、`875` `/api/user/agents/{id}`
- **影响**：浏览器跨域调用 DELETE 会被 CORS 预检拒绝 → **前端"删除会话/撤销授权码"直接不可用**（很可能已被前端绕过或尚未联调）；同时 `allow_credentials=True` 与"读端点无鉴权"叠加放大泄露面。
- **修复**：`allow_methods` 补齐 `DELETE`（及未来 `PUT/PATCH`）；把 `AUTOFORGE_CORS_ORIGINS` 与 `allow_credentials=True` 的取值列入投产检查单，禁止在生产配 `*`。

### S-3 HA Token 可经明文 http 发送

- **位置**：`af_adapters/ha.py:39`（`DEFAULT_HA_URL` 从 env 读，无 scheme 校验）、`102/137/152`（`Bearer {token}`）
- **影响**：内网 HA 常见 `http://homeassistant.local:8123`，长期令牌在明文信道传输；一旦 `AUTOFORGE_HA_URL` 被误配为外网地址，token 直出（虽有 `_NoRedirectHandler` 防 3xx 外泄，这一点做得好）。
- **修复**：非 `https` 且非内网地址时启动告警/拒绝（可用 `AUTOFORGE_HA_ALLOW_INSECURE=1` 显式放行）；token 不进错误串与审计 payload（当前 `CallResult.fail` 会把 HA 返回体前 200 字符塞进审计与 `vars`，可能带鉴权细节）。

### S-4 错误与审计信息未脱敏

- **位置**：`af_adapters/ha.py:123/127`（HA 返回体截断 200 字符进 `CallResult.error`）；`af_executor.py:479-484`（`result.error` 原样写入 `ctx.vars`，可被 NL 渲染/API 返回）
- **修复**：错误信息分类映射（`401 → AUTH_FAILED` 等），原始响应体只在 debug 级保留。

---

## 5. 架构评估

### 5.1 做得好的地方（建议保持，不要重构掉）

1. **Graph/IR 是唯一真相 + NL 确定性渲染**——"看到即跑的那句话"这个铁律在代码里是真被执行的（`af_nl` 由 IR 渲染、覆盖率检查进静态闸），这是项目最值钱的资产。
2. **时间与时钟纪律**：`TimeSource` 抽象 + monotonic/墙钟分离 + `parse_duration` 拒绝 inf/nan/负值 + 持久化边界做墙钟换算（`af_persist`），是同类项目里少见的正确设计。
3. **fail-closed 贯穿**：`_still_holds` 的 `to is None → False`、`not_is_on/off` 的 UnknownEntity→False、`resolve_best` 有歧义返回 `None`、鉴权过期拒绝——方向上一致，是好的工程直觉。
4. **静态闸密度**：`af_scanner` 26 类检查（含环检测、挂起后高风险、重复边、NL 覆盖率、expect 校验），远超"能跑就行"。
5. **表达式边界**：白名单函数 + 双资源上限 + 显式类型声明（禁隐式转换），把"Agent 写出来的表达式"关进了笼子。
6. **持久化原子写 + 租约仲裁**：`_atomic_write` + `FileLock` + owner/lease，v0.9.0 这块设计是扎实的。

### 5.2 结构性问题（投产前必须面对）

**A. 单进程内存态模型与"投产"目标错配**
`EventBus`/`InstanceManager`/`AuditLog` 全是裸 dict/list，无锁、无界、无进程外可见性。`forge watch` 与 `forge serve` 双进程靠 sidecar 文件（`pending_asks.json`）协作——**用文件做 IPC 是原型期手段**，投产会遇到：事件双投/漏投、状态不一致、无 HA 高可用。`af_flock` 已有跨进程锁原语，但只用在 GraphStore/PersistStore，**没用在 Runtime**。
> 建议：明确"单写者"模型（一个 watch 进程独占 Runtime，API 进程只经队列/只读快照交互），并在 Runtime 入口用 `FileLock` 把"唯一写者"这件事**代码化**；同时把"多写者"从路线图的 v0.9.0 目标升级为 v2.1 的显式架构决策（选：单写者 + 热备，还是引入事件日志/SQLite WAL）。

**B. 模块边界外溢，巨型模块正在变成泥球**
75 个 `af_*` 模块，其中 `af_orchestrator`(2460) / `af_service`(2102) / `af_evo`(1474) / `af_cli`(1352) / `af_catalog`(1189) / `af_scanner`(1181) / `af_api`(1062) 均已超千行；`af_service` 同时持有会话管理、实体健康、watch 编排、导出导入、目录解析。内核（IR/executor/scheduler/instance）反而小而清晰——**复杂度堆在服务与编排层**。
> 建议：按"内核 / 编排 / 服务 / 适配"四层划包（`af_core/`、`af_orchestration/`、`af_service/`、`af_adapters/`），用 `import-linter` 或 ruff `tidy-imports` 禁掉跨层反向依赖；`af_service` 按领域拆成 `session/`、`catalog/`、`governance/`、`transport/`。

**C. 异常处理纪律缺失（142 处 blind except / 30 处 try-except-pass）**
代码里既有"该抛的被吞"（canary 回滚、HA 读失败），也有"该软失效的抛穿"（`set`/`wait`）。**没有统一的异常分类与处理策略**，于是每个作者各自裁决。
> 建议：定义异常分层——`InfraError`（HA 不可达/超时，可重试）、`DomainError`（IR/表达式/类型，走 `on_error`）、`BugError`（断言不变量，直接崩）；在 `af_executor` 与 `af_api` 各设**唯一**的边界处理器，其余位置禁止裸 `except Exception`。用 ruff `BLE001`/`S110` 作为门禁（先基线化，只准减少）。

**D. 可观测性缺位**
没有结构化日志、没有指标端点（`/api/metrics` 返回的是业务计数而非进程指标）、没有 trace；排障靠 stderr print 与内存审计数组。对"Agent 写的自动化误动作"这类高敏感事件，**无法回溯"当时为什么这么判定"**。
> 建议：三件事——① 每次求值段产生一条结构化 `EvaluationRecord`（输入快照摘要 + 命中边 + 结果 + 耗时），落滚动 journal；② 进程指标（实例数、挂起数、事件吞吐、熔断次数、tick 耗时 P95）走 `af_metrics` 暴露；③ 关键判定（触发匹配、软失效、回滚、拒绝）统一为审计事件类型枚举，禁止自由文本-only。

**E. 依赖与可复现性**
`homesdk` 未声明（P0-1）；要求 Python 3.14 而 `pyproject` 写 `>=3.11`（README 与配置口径不一致）；vhass 依赖 Windows 不可加载（`addopts = "-p no:homeassistant"` 是必要的 workaround，但意味着**真仿真只在 Linux/CI 跑**）。
> 建议：锁文件（`uv lock` / `pip-compile`）+ CI 三矩阵（Linux 真 vhass、Windows FakeHA、macOS）、README 的 Python 要求与 `requires-python` 对齐（或明确 extras 分档）。

**F. 测试与门禁**
529 条测试基线是好的，但**门禁跑的是 `homesdk.gates` 的 AST 检查 + 基线只减不增**，并未覆盖 ruff/pyright 这类能直接抓到本次 P0/P1 的规则（`F821` 未定义名、`F811`、`T20` print、`BLE001`）。本次 30+ 条发现里，**至少 8 条可被一行 ruff 配置直接拦住**。
> 建议：把 ruff（`F,E,B,T20,BLE,S,TRY,ASYNC`）+ pyright(basic) 并入 `gates.sh`，先 `--add-noqa` 基线化再逐类清零；补**崩溃恢复专项**（persist + canary + time trigger + 实体消失）与**并发专项**（双进程写同一 store）。

---

## 6. 功能层面建议（产品向）

1. **把"崩溃恢复"做成一等公民能力**：当前恢复是"尽力而为"（丢弃规则散在 Runtime）。建议提供 `forge recover --dry-run`：列出待恢复实例、被丢弃实例及原因、预计错过的超时，让人在恢复前就能审阅——与项目"人只看一句话"的定位一致。
2. **实例与挂起的可观测面板**：`pending_asks` 已有 sidecar，缺的是"实例池"视图（谁在跑、卡在哪、挂了多久、TTL 剩余）。建议 `forge ps`（对标 `docker ps`）与 `/api/instances`，并支持按自动化/房间过滤。
3. **熔断/节流的"可解释"**：被熔断时用户只看到"灯没亮"。建议在审计与 NL 渲染里暴露"因 `light.x` 10s 内变更 12 次被熔断 15s"，并可配置"熔断即通知"。
4. **`ask` 收敛纪律的对外可见性**：`MAX_ASK_ROUNDS=3`、`MAX_ASKS_PER_ROOM=2` 是很好的设计，但违约时只落 `ask_violation` 审计。建议在 NL 与 API 中显式回报"本轮已达上限，已停止追问"，避免用户以为系统"不理人"。
5. **NL ←→ IR 漂移的持续校验**：既然"铁律"是 NL 由 IR 确定性渲染，建议加一条 CI 检查：每次 IR schema 变更，跑全量存量图的 NL 回归快照（`golden files`），**NL 措辞变化必须被人工确认**，防止改渲染器悄悄改变"用户批准的那句话"。
6. **MCP 与 API 同源但不同权**：MCP 面已正确拒绝 `approve`（`af_orchestrator` 硬护栏）值得肯定；建议把这套"面能力矩阵"（哪个面能调哪些能力）写成一张显式配置表 + 契约测试，而不是散在各处的注释。
7. **`fn` 节点保留位**：若确定 1.x 不实现，建议在 `build` 遇到 `reserved.fn` 时给出**指向文档的具体 hint**（当前只报"未实现"），降低 Agent 反复试错成本。

---

## 7. 建议的整改顺序

**第一批（1 周内，止血）**
1. P0-1 声明 `homesdk` 依赖 + CI 干净环境 import smoke
2. P0-2 `instance.id` → `instance.instance_id` + 补 persist×time-trigger 用例
3. S-1 读端点默认 deny（app 级依赖 + public 白名单）
4. P1-2 `for` 复查异常捕获；P1-3 总线回调隔离
5. P1-8 清 print → logger，CI 加 `T20`

**第二批（2–3 周，加固）**
6. P1-1 无界容器治理（实例 reaper / 审计 deque / emitted deque / 时间桶清理）
7. P1-4 canary 上下文可序列化化 + `to_dict` 去掉 `default=str`
8. P1-5/1-6 emit 熔断与去重分桶；P1-7 env 容错
9. P2-1 软失效统一；P2-5 HA 读失败 fail-closed；P2-6 状态缓存与批处理
10. S-2 CORS 方法补齐；S-3/S-4 scheme 校验与错误脱敏

**第三批（1–2 月，架构）**
11. 异常分层 + 边界处理器 + CI 门禁基线化（ruff/pyright）
12. 四层包结构重整 + 依赖方向约束
13. 观测三件套（EvaluationRecord / 进程指标 / 审计类型枚举）
14. 单写者模型代码化 + 跨进程一致性专项测试
15. 锁文件 + 三平台 CI 矩阵 + 崩溃恢复/并发专项测试套件

---

## 附录：静态分析数据（审计快照）

| 指标 | 数值 | 说明 |
|---|---|---|
| Python 文件 / 行数 | 225 / 57,624 | 含 tests |
| `F821` 未定义名 | 3 | `af_flock.py:58`、`af_live.py:174/363` |
| `F822` `__all__` 未定义 | 1 | `af_draft.py:24` `DraftResult` |
| `F811` 重复定义 | 2 | `af_api.py:536`、`af_instance.py:353` |
| `BLE001` blind except | 142 | 全仓 |
| `S110` try-except-pass | 30 | 静默吞异常 |
| `T20` 类 print 调试输出 | 9（src） | 其中 5 处在热路径 |
| 直接读墙钟（`time.time()` / `datetime.now()`） | 43（src，除 `af_time.py`） | 违反项目红线"业务代码禁止直读墙钟" |
| `PLW2901` 循环变量重定义 | 14 | 易引入隐蔽逻辑错误 |
| `B904` raise 不带 from | 12 | 异常链断裂，排障困难 |

> 说明：`af_store.py:803` 的闭包告警（`B023`）经人工核对为**误报**（`_map` 与 `id_map` 定义并使用于同一次迭代内），不计入缺陷。
