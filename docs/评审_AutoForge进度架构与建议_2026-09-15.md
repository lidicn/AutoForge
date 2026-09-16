# AutoForge 进度 / 架构稳定性 / 愿景可行性评审

> 评审日期：2026-09-15
> 评审人：lidicn（AutoFlow v1 PM / 架构协作方，AutoForge 设计共同敲定者）
> 评审方式：**读代码 + 跑测试**（非只读文档）。所有结论附 `文件:行` 证据。
> 目的：回答三件事——① 当前进度到哪了 ② 架构稳不稳 ③ 能否兑现当初设想；并给开发者（codebuddy）可执行的建议。

---

## 0. 一句话结论

**架构骨架非常稳，超出"原型"预期；当初设想（v1.0 内核）已高保真兑现。** 当前最大风险**不是架构，是工程卫生**：仓库**零 commit**、测试基线**已有 2 个红且 ROADMAP 状态未同步**。这两项不解决，后续迭代会越改越难追溯。

风险定级：**P0（必须立刻做）= 建仓打首个 commit + 修红并同步 ROADMAP**；其余为 P1/P2 演进建议。

---

## 1. 当前进度（实测核对，非文档声称）

### 1.1 已交付（G1–G7 + 横切，文档与代码一致）

| 里程碑 | 落点 | 核对 |
|---|---|---|
| G1 最小闭环 | `af_ir` / `af_time` / `af_bus` / `af_instance` / `af_adapters` / `af_executor` / `af_scheduler` / `af_scanner` / `af_nl` | 模块齐全，8 条验收用例结构在 `tests/acceptance/` |
| G2 安全扫描 20 项 | `af_scanner.py` `CHECKS` 字典 + 15+ 检查方法 | 逐条核对实现（见 §2.2）✅ |
| G3 IR 完备 | 7 节点 / 6 边 / 双 Timer | `af_ir/models.py` + `af_executor.py` |
| G4 置信度自治 | `af_conf.py` / `af_canary.py` | 衰减 + 回灌 + 漂移回滚 ✅ |
| G5 故障注入 | `af_fault.py` + 适配器 `FaultQueue` | 五类故障 + 四类失败落点 ✅ |
| 真机接线 | `af_adapters/ha.py`（HATransport/HAStateProvider）+ `live_preflight` | 三重闸 ✅ |
| 常驻监听 | `af_live.py`（SSE 订阅 + ticker） | 零新增依赖 ✅ |
| P1 持久化 | `af_persist.py` + `InstanceManager.attach` | 可序列化红线 + 租约 ✅ |
| G6 版本审计 | `af_store.py`（`GraphStore` / `diff_graphs`） | ✅ |
| G7 AF-Spec | `af_spec.py`（文本 ⇄ JSON IR 零有损往返） | ✅ |
| UI 服务层 + R2 | `af_service.py` / `af_api.py`（FastAPI，只读 + 审批台 + 鉴权） | ✅ |
| v0.2–v0.9 十一个小版本 | emit / on event / 指标回灌 MA / 标签 / 导出 / 鉴权升级 / 跨进程 / 表达力 / MCP | 交接卡齐全 |
| **v1.0.1 MCP stdio** | `af_mcp.py`（16 工具 + scope 门） | ⚠️ 见 §3.2（1 个红） |

代码量：**`src` 约 11,693 行**；测试 `tests/unit` + `tests/acceptance` 双套；文档 `docs/` 含 KICKOFF / IR_AND_RUNTIME / ROADMAP / 30+ 交接卡 / 2 份 autoflow 对照调研——**工程文档成熟度极高**，是本项目最强资产。

### 1.2 实测测试基线（本机，`tests/unit`，`.venv314` Python 3.14）

```
327 passed, 2 failed, 1 warning in 35.08s
```

> 与文档声称的「本机 334 passed / 10 skipped（v1.0.1）」**有出入**。差异来源见 §3.2。

### 1.3 路线图状态与实际代码脱节（重要）

ROADMAP 把 **v1.1.0 标 `⏸ 待办`**，但代码里 **`af_catalog.py` 已存在**，`tests/unit/test_v1_1_catalog.py` 已在跑且**红**。说明 v1.1.0（实体事实内建）**已悄悄开工**，只是路线图状态没更新。这是"文档先行但没回填"的典型漂移，需在首个 commit 前修正。

---

## 2. 架构稳定性评估（逐条核对不变量）

判定方法：不只信文档，读核心实现 + 静态扫描 + 跑测试。结论是——**骨架稳，红线基本都落地**。

### 2.1 Graph 唯一真相 ✅

- `af_ir/models.py` 的 `Automation/Graph/Node/Trigger` 是唯一模型；`af_spec.py` 明确"Spec 是投影、Graph 才是模型"，且 `compile_spec(render_spec(g))` 与 `g` raw 级一致（零有损往返）。
- NL 由 Spec **确定性渲染** + 覆盖率检查（`NL_COVERAGE` warning，防"批准句 ≠ 跑的逻辑"）——`af_scanner._check_nl_coverage` 真实实现。

### 2.2 两道闸串行、不可调换 ✅（实在的）

`af_scanner.py` 的 `CHECKS` 字典 + 方法清单证明第一道闸（安全）是**真代码**而非文档：

```
L3_ACTION / HTTP_NOT_WHITELISTED / MISSING_TIMEOUT_OR_DEFAULT / ENTITY_DEP_CYCLE
STATIC_LOOP / CANCEL_SPAWNS_INSTANCE / HIGH_RISK_AFTER_SUSPEND
SHADOW_WRITES_DEVICE / CROSS_AUTOMATION_VAR / ADAPTER_POLICY_PARAM
SNAPSHOT_FALSE_MULTI_AND / ENTITY_NOT_FOUND / ENTITY_WRITE_CONFLICT
L2_NEEDS_CONFIRM / LOW_CONF_WRITES_DEVICE / DO_WITHOUT_ON_ERROR
NESTED_SUSPEND_IN_CANCEL / ENTITY_ACL_DENIED / DUPLICATE_EDGE_PRIORITY
NON_IDEMPOTENT_CONCURRENT / EXPR_INVALID / UNDECLARED_VAR / NL_COVERAGE
EMIT_SELF_LOOP / EMIT_STORM_LIMIT / LIVE_TOKEN_REQUIRED / LIVE_CONFIRM_REQUIRED
LIVE_WHITELIST_REQUIRED / LIVE_ENTITY_NOT_WHITELISTED
```

- 跨自动化环用 **DFS 三色标记**真实图算法（`_find_cycles` / `_cycles_in`），实体读写依赖矩阵 + emit 事件环 + 抢占冲突三类全有。
- 真机下发 `live_preflight` 是**第二套独立预检**（令牌 / 二次确认 / 白名单），与编译期闸互补。
- 第二道闸（逻辑）走 vhass（`af_vhass/`），基于 `pytest-homeassistant`——红线"不自研仿真器"遵守。

### 2.3 适配器纯执行层契约 ✅（被静态闸强制）

- `af_adapters/base.py:39-46` 定义 `POLICY_PARAMS`（含 `retry/fallback/backoff/retry_delay/on_retry`），扫描器 `ADAPTER_POLICY_PARAM` 直接拦截（`af_scanner._check_risk`）。
- `af_adapters/ha.py:16` 注释"只做单次下发，不含 retry/fallback"。
- 传输层超时保留（socket/connect），语义层重试/降级归 IR——边界清晰。

### 2.4 实例可序列化 ✅（红线守得住）

- `af_instance.py:158-160` `to_dict()` 用 `json.loads(json.dumps(self.ctx.to_dict(), ensure_ascii=False, default=str))` 做**序列化自检**——闭包/Socket/生成器混入会在状态变更时当场炸。
- 全仓 grep `pickle|socket.socket|__closure__|partial(` 在实例路径**零命中**（仅 `af_auth.py`/`af_service.py` 的 `threading.Lock`，属会话/鉴权基础设施，不进可序列化上下文）。
- `_ALLOWED` 状态迁移白名单 + `IllegalTransition` 异常（非法迁移直接抛，不静默漂移）——`af_instance.py:52-60, 349-354`。

### 2.5 快照 = 求值段 ✅

- `af_instance._refresh_snapshot`（`:342-347`）**新建快照丢弃旧快照**；`resume` 同样重取（`af_instance.py:258-262`）。
- `do` 结果只写 `vars`、不回写快照（`IR §7.2`）——`InstanceContext` 结构强制（`snapshot` 只读副本 vs `vars` 可写）。

### 2.6 双 Timer 隔离 ✅

- `InstanceTimer`（`af_instance.py:75-87`）走 `monotonic`（TimeSource）；`for=10m` 持续条件走 IR 静态图——两套原语不混用（`IR §11`）。

### 2.7 TimeSource 抽象 ⚠️（纪律对，但只靠约定）

- 求值核心（executor/scheduler/instance/state）经 `TimeSource` 取时钟，无直接 `time.time()`。
- grep `time.time()` 命中在 `af_auth.py:156`（令牌过期）、`af_service.py:989`（API 响应时间戳）、`af_flock.py`/`af_live.py`/`af_metrics.py`——**全是基础设施层，非自动化求值逻辑**，可接受。
- **隐患**：该红线目前只靠 code review 维持，无 CI/lint 强制。一旦有人在 `af_executor.py` 误写 `time.time()`，文档纪律就破了。建议加一条 grep/ruff 规则拦求值核心模块直接调时钟。

### 2.8 架构稳定性总评

| 维度 | 评级 | 说明 |
|---|---|---|
| 模块边界 | A | 单职责清晰，DI 组装在 `af_runtime.py`，服务层/路由层/CLI 三层分离 |
| 安全不变量 | A | 两道闸、适配器纯执行、序列化、状态机全部落地为代码 |
| 可测试性 | A- | 单测/验收双套，vhass 真仿真，但双环境有 10 errors 残差（§3.3） |
| 版本/可追溯 | **D** | **零 commit**，无历史、无回滚点（§3.1） |
| 文档-代码一致性 | B- | 文档极好，但 ROADMAP 状态未随 v1.1.0 开工回填（§1.3） |
| 测试基线纯净度 | **C** | 已有 2 个红未清（§3.2） |

**结论：骨架是 A 级，工程卫生是 D 级。先补漏卫生，架构本身值得继续投入。**

---

## 3. 关键风险（按优先级）

### 3.1 🔴 P0：仓库零 commit —— 立刻建仓打首个 commit

`git status` → `No commits yet`，branch `master`，**全部源码/文档/测试均未跟踪**。

- 11.7k 行 + v1.0.1 成果，一旦磁盘故障/误删/坏改，**零恢复手段**。
- 你自己的纪律（`E:\NAS\开发规范.md`）：本地写码 → git 管 → 手动 SSH 推送。AutoForge 现在卡在第一步之前。
- **建议**：
  1. 先确认 `.gitignore` 已忽略 `.venv*/` `__pycache__/`、`tmp/`、`*.egg-info/`、`smoke_*.log`、`credentials.json` 等（仓库已有 `.gitignore`，核对一遍）。
  2. 首个 commit 用 **lightweight tag** 或直接在 `master` 打 `v1.0.1` 锚点（AutoFlow v1 的经验：tag 走 lightweight，避免 `^\d+\.\d+\.\d+$` 正则卡住）。
  3. 提交后 `git ls-remote` 校验远端 HEAD，再手动 SSH 推 NAS/GitHub（按你的纪律，git 写操作须 `dangerouslyDisableSandbox`）。
  4. 此后每交付一个版本（交接卡收口即 commit），不要攒到十一个版本一起交。

### 3.2 🔴 P0：清 2 个红 + 同步 ROADMAP

实测 2 红：

1. **`test_af_mcp::test_stdio_protocol_roundtrip`**（v1.0.1 已交付功能）
   - stdio JSON-RPC 往返断言失败。这是**已发布功能回归**，优先级最高——agent 接入是 v1.0.1 的卖点，协议不稳等于对外契约裂。
   - 先定位是 `af_mcp.dispatch()` 的协议处理还是测试桩时序问题，修完补一条协议整轮回归。

2. **`test_v1_1_catalog::test_refresh_persists_and_summarizes`**（`added==5 != 0`）
   - `af_catalog.py` 已存在，`refresh()` 第二次调用仍"新增 5 条"而非"0 变更"——持久化去重/指纹逻辑没生效。
   - **同时把 ROADMAP v1.1.0 状态从 `⏸` 改为 `🔨 进行中`**，并补一张交接卡，避免"代码跑在文档前面"继续扩大。

### 3.3 🟡 P1：双环境 10 errors 应修到 0

README/ROADMAP 注"NAS 容器 10 errors 为缺 `pytest-homeassistant` 夹具的既有环境差异"。
- "既有环境差异"不应长期存在——它让"双环境全绿"的 DoD 打折。
- 建议：在 `docker/Dockerfile.test` 里装齐 `pytest-homeassistant` 夹具依赖，或把缺夹具的用例显式 `skip` 并标注原因，让 CI 数字干净（0 failed / 0 error）。

### 3.4 🟡 P1：MCP 接入默认权限应强制 scope

`af_mcp.py` 设计：无令牌 → 全放行（复用 v0.8.0 语义）。但 v1.0.1 是**给 agent 用**的——agent 连上来若无令牌即拿全权限（含 `af_save` 写、`af_live_run` 真机下发），与"agent 不自批准"铁律相悖。

- 建议：MCP server 启动**默认要求 `AUTOFORGE_API_TOKEN`**；无令牌只允许只读工具（`af_health`/`af_list_graphs`/`af_get_graph`/`af_simulate`/`af_resolve_entity` 等），写/live 工具在无令牌时直接拒。复用 `af_auth.TokenRegistry` 的 scope 语义，不要另搞一套。

### 3.5 🟢 P2：TimeSource 红线加 lint 强制

§2.7 已说明。加一条 CI 规则（grep 或 ruff `T201`/自定义）禁止 `af_executor.py`/`af_scheduler.py`/`af_instance.py`/`af_state.py` 直接 `import time` 或调 `time.time()`/`datetime.now()`，只放行 `af_time.py` 自身与基础设施层。

---

## 4. 能否兑现当初设想？

对照我们敲定 AutoForge 时的核心设想（KICKOFF §1-2 + 你的原始诉求）：

| 当初设想 | 现状 | 判定 |
|---|---|---|
| Agent 写自动化，机器验，人只读 NL | AF-Spec + NL 确定性渲染 + 两道闸 | ✅ 已兑现 |
| 不连 Node-RED，直连 HA | `af_adapters/ha.py` 直连，零 NR 依赖 | ✅ |
| vhass 基于 pytest-homeassistant（不自研） | `af_vhass/` 封装 HA 官方测试框架 | ✅ |
| 适配器纯执行层 | `POLICY_PARAMS` + 扫描器强制 | ✅ |
| 置信度分级自主（G4） | `af_conf` + `af_canary` | ✅ |
| 双 Timer 隔离 | `InstanceTimer`(monotonic) vs `for`(IR) | ✅ |
| 快照 = 求值段 | `_refresh_snapshot` 新建丢弃 | ✅ |
| JSON 序列化 + 跨自动化环双机制 | `GraphStore` + 依赖矩阵 + 运行时熔断 | ✅ |
| 生态分工 AF/MA/DB | MA 指标回灌（`af_metrics`）、DB 接 ask 话术 | ✅ 链路通 |
| **「跑对了吗」断言闭环（v1.2.0）** | IR **无期望值字段**，`forge sim` 只回 `final_states` | ❌ **最大内核缺口** |
| 实体事实内建（v1.1.0） | `af_catalog.py` 已开工但未交付 | 🔨 进行中 |

**结论：当初设想的 v1.0 内核已高保真兑现，骨架比预期还稳。** 唯一"未完全实现"的是 v1.1–v1.5 的**事实 / 断言 / 信任 / 治理 / 经验**层——但这在路线图里已规划，且**不是架构障碍，是增量打磨**。

> 一句话：能实现，且大概率比你我当初担心的更稳。瓶颈已从"能不能做对"变成"工程卫生别拖后腿"。

---

## 5. 给开发者的建议（按 ROI 排序）

### 5.1 立刻做（P0，半天内）
1. **打首个 commit + 推仓**（§3.1）。这是所有后续工作的安全网。
2. **修 `test_af_mcp` stdio 回归 + `test_v1_1_catalog` 红**，并把 ROADMAP v1.1.0 改为 `🔨 进行中`（§3.2）。

### 5.2 近期做（P1，本迭代）
3. **v1.2.0 断言闭环是内核最尖锐缺口**——`forge sim` 现在只能答"跑完了吗"，答不了"跑对了吗"。
   - **直接复用 AutoFlow v1 已验证的语义**，不要重造：
     - IR 顶层加 `expect`（后置条件断言列表 `{entity_id, state}` / `{var, eq/ne/gt/lt}`）；
     - `af_simulate` 逐条断言返回 `{passed, failed, ok}`；
     - **未建模服务留痕**：vhass 跑不到的动作登记 `unmodeled_services`，sim 明确提示"N 个动作无法验证"——否则 `expect.ok=true` 是虚假通过（AutoFlow v1 的 V-F1~F4 零信任闸就是这个教训）；
     - `expect` 缺省 **warning 不 error**（不破坏 18 份既有 examples）。
   - AutoFlow v1 的 `validate_intent` + `expected_postconditions` 是现成参考实现，拿它的契约过来即可。
4. **MCP 默认 scope 强制**（§3.4）——agent 接入的安全前提。
5. **双环境 10 errors 清零**（§3.3）。

### 5.3 中期做（P2）
6. **TimeSource lint 强制**（§2.7/§3.5）。
7. **与 AutoFlow v1 的概念复用，不要各写一套**：两项目都遵循同构铁律——「Graph/DSL 唯一真相 + 两道闸 + agent 不自批准 + 部署前人审与运行时 ask 分野」。具体可共享的已验证模式：
   - 验证闸的 `expected_postconditions` / `fully_verified` / `unmodeled_service` 语义（v1.2.0）；
   - `diff` 签名兜底匹配（`matched_by=signature`，v1.3.0，AutoFlow v1 已有双模式匹配）；
   - 部署前 `PendingOp` 待批队列 + **MCP 面绝不注册 approve**（v1.4.0，AutoFlow v1 铁律：批准/升格只在 WebUI）；
   - 失败归因 `error_knowledge`（v1.5.0，AutoFlow v1 的 `findings-ledger` 同构）。
   - **边界**：AutoFlow v1 的 `arena` 竞技场 / `nr_client` / Node-RED 特有模块**不适用** AutoForge（不同域），ROADMAP「不排期项」已正确排除，保持。
8. **v1.1.0 实体目录**按已定约束落地（不过滤域、绝不静默猜域、area 仅提示、防 DoS 三纪律）——这是用户提出的第一痛点，且 `af_catalog.py` 已铺路，风险最低，建议排在 v1.2.0 之前或并行。

### 5.4 不要做的（守住红线）
- 不碰 Node-RED；不自研仿真器；不上原生 Python 沙箱（`fn` 节点保持保留位，CEL/Lua 待评估）；
- IR 冻结前不写 AF-Spec EBNF（已做对）；适配器不含 retry/fallback（扫描器已强制）；
- 不在实例上下文存不可序列化对象（序列化自检已守住）。

---

## 6. 与 AutoFlow v1 的分工边界（确认互补不重叠）

| 项目 | 角色 | 写入方 |
|---|---|---|
| **AutoForge (AF)** | Agent 创作平台，HA 自动化的**唯一写入方** | 直连 HA（经 `forge run --live` / `watch`） |
| **AutoFlow v1** | 人/网关面，Node-RED flow 的安全部署+验证网关 | 直连 NR（经 `nr_client`） |
| **doubao-butler (DB)** | 实时连接与执行、TTS、ask 话术 | — |
| **memory-agent (MA)** | 创造力源泉 + 指标回灌 | — |

两项目**生态互补、技术同构**：都基于"声明即真相 + 机器验证 + 人只读自然语言 + agent 不自批准"。AutoForge 走"直连 HA + JSON-IR + pytest-homeassistant"，AutoFlow v1 走"NR flow + DSL + vhass 重生"。**不要互相抄代码，但要把已验证的验证/治理语义对齐**（§5.3）。

---

## 7. 下一步建议（一句话路线图）

```
P0  ── 打首个 commit + 推仓 + 清 2 红 + ROADMAP 回填
P1  ── v1.2.0 断言闭环（借 AutoFlow verify_flow 语义）＋ MCP scope 强制 ＋ 双环境 0 error
P1  ── v1.1.0 实体目录收口（用户第一痛点，已铺路）
P2  ── v1.3.0 变更可信 ＋ v1.4.0 治理面 ＋ v1.5.0 经验闭环（均复用 AutoFlow v1 已验证模式）
```

**总体判断：继续投，方向对、骨架稳；先把工程卫生（commit / 红 / ROADMAP 同步）补齐，再冲 v1.1–v1.2 两个 P0 内核缺口。**
