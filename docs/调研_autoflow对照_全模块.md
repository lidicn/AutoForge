# 调研报告（二）· autoflow 全模块对照：除实体目录外还有什么值得抄

> 日期：2026-09-15
> 上一份：`docs/调研_autoflow对照_实体链路.md`（只覆盖实体目录）
> 本轮覆盖：autoflow `src/autoflow_gateway/` 全部 50 个模块 + `docs/` 全套
> 方法：3 个子代理分头通读（编译链/安全治理/经验运维）+ 主代理读架构与交接文档

---

## 0. 先给结论

**值得抄的很多，但要分层看：**

| 层 | 数量 | 说明 |
|---|---|---|
| **Node-RED 特有，别抄** | ~12 模块 | flow_linter / api_specs / subflows / tab_organizer / sync / nr_client … 与本项目（直连 HA + JSON-IR）不同域 |
| **真缺口，该抄** | **~9 项** | 见 §2 第一、二梯队 |
| **长期价值，可选** | ~6 项 | 见 §2 第三梯队（经验闭环） |
| **AutoForge 已经更好** | ~8 项 | 见 §3，**别回退** |

其中**最高优先级不是实体目录**，而是这两个我认为你现在就有真问题的：

1. **IR 没有「期望值」声明** → `sim` 只能「跑一遍看结果」，无法自动判对错；
2. **`diff_graphs` 纯按 node id 比对** → agent 重新生成 IR 时 node id 一变，diff 就变成「全删 + 全加」。

---

## 1. 本轮调研覆盖清单

| 分组 | 模块 |
|---|---|
| 编译链/闸门 | `dsl_engine` `flow_linter` `flow_simulator` `flow_diff` `api_specs` `subflows` `template_lib` `templates` `schemas` `build_scene` `errors` |
| 安全/治理 | `identity` `device_guard` `defense` `confirm` `proposals` `deploy_tokens` `api_keys` `audit` `plan_store` `decision_store` `notes` `command_store` `config` `connections` |
| 经验/运维 | `experience` `error_knowledge` `arena` `task_store` `telemetry` `token_stats` `llm_client` `debug_bridge` `snapshot_manager` `self_update` `tab_organizer` `acp_client` `vhass` `sync` |
| 文档 | `02_architecture/ARCHITECTURE.md`、`05_handoff/PROJECT_HANDOFF_20260907.md`、`docs/README.md`、`03_dev/*`、`04_test/findings-ledger.md` |

---

## 2. 真缺口清单（按性价比排序）

### ★★★ 第一梯队：直接提升 Agent 成功率

#### 2.1 IR 强制声明「期望后置条件」——**这条我认为你现在就有问题**

**autoflow 怎么做**：
DSL **必须**写 `预期:` 块，不是可选的。例：

```
场景: 书房电脑开机则开显示器挂灯
触发: switch.xxx on
动作: light.turn_on(light.yyy)
预期:
  light.yyy = on
```

校验链：`schemas.validate_intent` 把 `expected_postconditions` 列为**硬性必填**——「没有成功判据就无法判定 flow 是否跑通」。
然后 vhass 重放后**逐条断言**，对不上就打回给 agent 改，人根本看不到废品。
arena 里更进一步：`锁定必须 result.ok and gate.passed and gate.fully_verified`，
**`gate.passed` 只代表「没抓到反例」**，`fully_verified=false`（零断言/前置已满足/JSONata 保守命中）**不得落锁**。

**AutoForge 现状**：
- IR 里**没有任何期望值字段**（实测 grep `expected|postcondition|assert` 在 `af_ir/` 零命中，命中的都是 `expect_version` 乐观锁和 `expected_handler` 故障元数据）。
- `af_simulate` 返回 `final_states` ——**跑完了，但没人说「应该是什么」**。
- G1 验收用例的 `expect` 写在**测试文件/IR 的 `meta.expect` 注释**里（`case01_day_light.json` 的 `meta.expect` 是纯文本，不参与任何断言）。

**后果**：Agent 提交自动化 → sim 通过 → 但「sim 通过」只意味着「跑完了没报错」，**不意味着「行为符合意图」**。
`forge build` 拦住了「不安全」，`forge sim` **没拦住「逻辑对但语义错」**。

**抄法**：
```jsonc
// IR 顶层加（可选但推荐，af_build 缺省告警）
"expect": [
  {"entity_id": "light.study_main", "state": "on"},
  {"var": "turn_on_result.success", "eq": true}
]
```
`af_simulate` 对 `expect` 做断言，返回 `{"expect": {"passed": [...], "failed": [...], "ok": false}}`。
与现有 `af_scanner` 的 `NL_COVERAGE`（每个节点必须被 NL 提及）正好互补——**一个查"说清楚没"，一个查"做对了没"**。

---

#### 2.2 Diagnostic 缺 `hint`（自修正提示）——Agent 一次往返就能改对

**autoflow 怎么做**：
`DSLError(message, line=None, code=C_PARSE, hint=None)` —— 错误对象**自带「改哪一行」+「怎么改」**，
且 `hint` 能从 message 的「（建议：…）」后缀**自动抽取**（`_extract_hint`）。
50+ 个 `C_*` 错误码，每个都配自修正文案。

**AutoForge 现状**：
```78:90:src/autoforge/af_scanner.py
@dataclass(frozen=True)
class Diagnostic:
    """一条扫描诊断。"""
    code: str
    level: str
    message: str
    automation_id: str = ""
    node_id: str = ""
```
**有 code / 有定位 / 没有 hint**。Agent 拿到 `[error] ENTITY_DEP_NOT_CYCLE [x] 节点 a3: ...` 后，
得自己推断「那我该怎么改」。

**抄法**：`Diagnostic` 加 `hint: str = ""`；建一张 `CODE_HINT: dict[str, str]` 表，
在 `af_scanner` 构造 Diagnostic 时补上。**成本极低，收益直接体现在 Agent 的成功率上。**

---

#### 2.3 `diff_graphs` 纯按 node id 比对——Agent 重生成 IR 时 diff 会「全删全加」**

**autoflow 怎么做**（`flow_diff.diff_flows`）**双模式匹配**：
- 两端**节点 id 集合完全一致** → 按 id 配对，并额外比对 `wires` 拓扑；
- **否则**（编译器产物 id 与 golden 不同）→ 按 **`(type, 签名)` 贪心配对**（签名 = `name > action > entityId > property`），跳过 wires 只比业务字段。

这是个**现实难题的解法**：自动生成的产物 id 天然不稳定。

**AutoForge 现状**：
```607:622:src/autoforge/af_store.py
        old_nodes = {n.id: n for n in old_auto.nodes.values()}
        new_nodes = {n.id: n for n in new_auto.nodes.values()}
        for node_id in sorted(set(new_nodes) - set(old_nodes)):
            diff.added_nodes.append(f"{auto_id}:{node_id}")
        for node_id in sorted(set(old_nodes) - set(new_nodes)):
            diff.removed_nodes.append(f"{auto_id}:{node_id}")
```
**纯 id 集合差，无兜底**。

**为什么这对 AutoForge 是真问题**：AutoForge 的 IR 是 **Agent 撰写的**，node id（`a1`/`i1`/`d1`）由 Agent 随手起。
Agent 第二次改这条自动化时，很可能把 `d1` 起成 `do1` —— 于是：
- `forge diff` 显示「删了 d1、加了 do1」，**实际只是同一个动作节点**
- 人审时看到一堆红绿，无法判断真实变更
- `af_diff` 是 MCP 工具（agent 自己也会看），误导性更强

**抄法**：`diff_graphs` 加签名兜底——id 交集为空或差异率 > 阈值时，
退化到按 `(kind, action, 主 entity_id)` 贪心配对，并在输出里标注 `matched_by`。

---

### ★★ 第二梯队：安全/治理增强

#### 2.4 爆炸半径（blast radius）作为一等公民

**autoflow**：`cfg.blast_radius_max_flows = 1`（默认 1），`defense.check_write(flows_touched=N)` 超过即拒绝，提示「请拆分为单 flow 操作」。

**AutoForge 现状**：有 entity ACL、有 live 白名单，但**没有「一次操作最多动几个自动化」这个概念**。
`af_import_store` 可以一次导入整个 bundle（成百条自动化）；`af_enable_by_tag` 可以一次翻转一个 tag 下全部。

**抄法**：`save_graph` / `import_store` / `enable_by_tag` 加 `blast_radius` 校验，默认阈值可配。
**防「agent 一次手滑改完全部自动化」最有效的一招。**

#### 2.5 所有权隔离（ownership）

**autoflow**：`if owner_agent and owner_agent not in (acting_agent, None, "", "system")` → 拒绝。
语义清晰：agent **只能改自己认领的**；归属为 `None/""/system` 的视为公共流放行。

**AutoForge 现状**：**归档无「谁建的」概念**。`af_save` 只记 `note`，不记 `created_by`。
多 agent 接入（MCP）后，A agent 可以无声覆盖 B agent 的自动化。

**抄法**：Graph 归档元数据加 `created_by` / `owner`；`af_save` 在 `expect_version` 冲突之外再判归属。
与 v0.8.0 的 TokenRegistry `subject` 天然衔接。

#### 2.6 待批队列（PendingOp）+ 执行/批准物理分离

**autoflow**：
- `ConfirmationGate.request(op)` 把写操作入 `pending/<op_id>.json`；
- `approve/reject` **只在 WebUI**，reviewer **硬编码 `"human"`**——**MCP 面根本不注册 approve 工具**（连 `/mcp-admin` 都没有）；
- **per-agent 待批熔断**（`max_pending_per_agent=20`）防提案风暴淹没审批人；
- `PendingOp` 把「执行所需 payload」与「展示用 summary/blast_radius」**同包落盘**，批准时回放 payload → 无二次渲染漂移。

**AutoForge 现状**：
- `ask` 会话是**运行时挂起**（自动化跑到 ask 节点停下来等人答），**不是部署前审批**；
- `live_run` 三重闸是「立即执行 + 前置检查」，**不是「先排队等人点同意」**；
- `af_api.py` 端点清单里**没有 `/pending`**。

**抄法**：新增 pending 队列（store + 3 端点 + `forge pending list/approve/reject`）。
**注意**：AutoForge 的 `ask` 与 autoflow 的 `PendingOp` 是**不同语义**，不要合并——
一个管「运行时问用户」，一个管「部署前人审批」，两者都需要。

#### 2.7 device_guard 的 Tier 分级 + 「取最严」合并

**autoflow**：JSON 注册表 `{match: {type: entity|domain|area, value}, tier: 0|1}`；
命中多条**取最严**（0 最严）；Tier-0 = 必须过人审，Tier-1 = 放行但记审计。

**AutoForge 现状**：`entity_acl: Mapping[str, str]`（`rw`/`r`/`-`），
是**调用时传入的静态 dict**（`af_scanner.py:130`），没有热更新、没有分级、没有 area 维度。

**抄法**：ACL 抽成注册表（可热更新）+ 加 tier 语义 + 「取最严」合并策略。

> ⚠️ **据实说明**：autoflow 的 `DeviceGuardStore.match_tier()` **全仓无任何调用点**——
> 注册表建好了、CRUD 有了，但拦截接线没落地（模块 docstring 自述「拦截逻辑由 WB1 在 D3 后统一裁定」）。
> 所以**抄的是「注册表与判定引擎解耦」的分层思路**，不要以为它在 autoflow 里已经生效。

#### 2.8 api_keys 过期校验的三重 fail-closed

**autoflow**（源码标注 P0 修复）：
- `expires_at` **无法解析**（ValueError/TypeError）→ 直接 401 拒绝（**不是放过**）；
- `expires_at` 是 **naive datetime（无时区）** → 直接 401（无法与 aware 时间可靠比较）；
- 已过期 → 403。

**为什么值得看**：很多系统「解析失败就放过」，导致「永不过期」的幽灵凭据。这里明确反着来。

**AutoForge 现状**：`af_auth.TokenRegistry` 已有撤销黑名单（`revoked.json`）+ 限速。
**建议对照自查**：令牌的过期/时间解析是否有 fail-open 路径。

#### 2.9 凭据热重载（connection_revision 代数）

**autoflow**：
1. WebUI 改 HA 令牌 → 原子落盘（`mkstemp` + `os.replace` + `chmod 600`）→ 注入 env；
2. `bump_revision(cfg)` 把全局单例的 `connection_revision` **+1**；
3. HA/NR 层的 client property **比对代数**，变了就丢弃缓存 client 用新凭据重建；
4. 用「代数」而非直接戳实例，因为同进程可能有多个 Gateway（WebUI 一个、MCP 一个），`cfg` 是全局单例 → 所有实例自愈；
5. `describe()` 对 secret **只回掩码 + 长度，连末 4 位都不露**（WebUI 会被截图/投屏）。

**AutoForge 现状**：`AUTOFORGE_HA_TOKEN` / `AUTOFORGE_API_TOKEN` **只从环境变量读**。
改令牌必须重启进程——对 `forge watch` 常驻进程是实际痛点。

**抄法**：凭据落 `{root}/credentials.json`（gitignore + 0600）+ 代数热重载。
配合已有的 `af_flock` 做原子写。

---

### ★ 第三梯队：经验闭环（长期价值，非紧急）

#### 2.10 telemetry：零侵入失败归因

`tag_action(...)` 给每个动作打 5 类标签之一，**附在返回 dict 的 `_telemetry` 字段**（不改已有结构），
append-only JSONL。**兜底方向是「宁可多归网关，不冤枉 agent」**（未知失败 → `gateway_error`）。

AutoForge 已有 `af_audit`（`ENTITY_DRIFT` / `ACTION_FAILED` / `QUOTA_EXCEEDED` / `WRITE_CONFLICT` …），
但那是**事件型审计**；telemetry 是**归因型标签**，是 `error_knowledge` 的输入。两者互补。

#### 2.11 error_knowledge：有序正则归因 + 类别化修复建议

- `ERROR_PATTERNS` **顺序即优先级**（具体特征在前、泛化在后），首个命中即返回，零 ML；
- **精确类别必须有专门建议**（不落 `other`）；
- 只保留最近 500 条 + 按类型累计 stats。

AutoForge 的 `af_fault.FAULT_META` 有 `expected_handler`，但那是**故障类型 → 预期处置**的静态映射；
缺的是**实际失败 → 归因 + 修复建议**的运行时知识库。

#### 2.12 experience：实体共现 + DSL 模式采集

- 只在 `operation=="propose-dsl" and success` 时更新统计（**不污染失败样本**）；
- 共现对**无向去序**（`sorted([a,b]) → "a|b"`）+ 单实体频次，双结构零图库依赖；
- 正则抽 `domain.entity` + **服务后缀黑名单**剔除误匹配（`.turn_on` 等）；
- 日志 JSONL 按天滚动 + 原子写。

**对 AutoForge 的价值**：直连 HA 后，「哪些实体常一起出现」是**主动记忆**的天然素材——
可以喂给 MA 做假设生成，形成 v0.5.0 指标回灌的**反向补充**（AF 采集事实 → MA 生成假设）。

#### 2.13 task_store：多 agent 独立做题

**关键设计**：`task_claims` 主键 **`(task_id, agent_id)`** —— 同一任务可被多名 agent 各自建一行，
**互不抢占**，从而拿到「同一题的多样化写法」。`claim(prefer_mine=True)` 先返回自己 claimed-未提交的行 → **断点续传**。
发布时 `entity_hint` **即时富化**（含 `friendly_name/domain/area/possible_states/target_service`）——**让 agent 只填空不猜**。

> ⚠️ **别抄它的缺口**：`claimed` 行**没有租约/超时回收**，agent 领了不交就永久悬置。
> AutoForge 有 `af_persist` 的租约机制（v0.9.0），抄的时候要补上。

#### 2.14 判重三层 + 「双向覆盖率取大」

- ① 实体重叠 >60%：`max(inter/len(new), inter/len(exist))` —— 双向覆盖率**取大**，
  防「挂无关实体稀释分母绕过」；命中后交 LLM 仲裁，LLM 不可用则 **fail-safe 判拒**；
- ② 文本相似 >85%：`difflib.SequenceMatcher`（零依赖）直接判重；
- ③ 相似度落 0.6–0.85 模糊区才调 LLM（**省 token**）；
- **判重作用域覆盖全部非终态**（available/in_progress/locked），不是只看已锁定。

AutoForge 若做「自动化去重」（多 agent 写重复自动化），这套是现成答案。

#### 2.15 snapshot：回滚前再自动快照一次

`full_rollback` / `selective_rollback` **执行前都先做一次 incremental 快照**——
「防止回滚错了还能再回滚」。极低成本、极高价值的一个习惯。

AutoForge 的 `af_canary` 有动作级反向回滚，但**归档级回滚**（`import_store overwrite` 会删掉旧版本）值得加这道保险。

#### 2.16 token 统计

按字符数 `/4` 估算（零依赖跨模型近似的土办法），按天/agent/端点/mode 四维聚合，保留 30 天。
AutoForge 若要让 agent 长期跑，这个能直接体现「哪个工具最烧 token」。

---

## 3. AutoForge 已经更好，别回退

| 项 | AutoForge | autoflow |
|---|---|---|
| **静态扫描** | 20 项检查 + IR Schema 冻结，`ENTITY_NOT_FOUND`/`ENTITY_WRITE_CONFLICT`/`L2_NEEDS_CONFIRM` 等成体系 | flow_linter R13/R15/R17/R20/R22（NR 节点结构层，不同域，无法直接比） |
| **置信度分级** | `af_conf`（auto/shadow/ask 三级 + 衰减 + 样本回灌），且**编译期 G2 与运行期共用同一组常量** | 无对应机制 |
| **灰度保护** | `af_canary` 漂移检测 + 自动回滚 + `ENTITY_DRIFT` 审计 | 无 |
| **故障注入** | `af_fault` 五类故障 + 四类失败落点 + 注入层隔离 | 无 |
| **实例持久化** | `af_persist` 跨进程崩溃恢复 + 墙钟/单调时钟换算 | 无 |
| **多写者安全** | `af_flock` 跨进程文件锁 + owner 租约 + `write_conflict` 审计 | 无 |
| **仿真底座** | `af_vhass` 基于 `pytest-homeassistant` = **真 HA 内核** | `vhass.py` 手写 REST 子集（579 行），自述「不是真 HA」 |
| **结构化审计** | `af_audit` 11 类事件 + `ALL_EVENT_TYPES` | `audit.py` 只是 trace 环形缓冲的薄转发（`AuditStore.list()` → `gw.get_recent_traces()`） |
| **工程化** | 双环境绿（本机 323 / NAS 323）、交接卡制度、版本路线图 | 巨石模块（gateway 9172 行）、95 个历史测试红未清 |

---

## 4. 一条容易被忽略的「诚实性设计」主线

autoflow 在几个地方做得比 AutoForge 更「诚实」，值得单独拎出来：

| 场景 | autoflow | AutoForge 现状 |
|---|---|---|
| **仿真遇到未建模的服务** | `vhass._mutate`：**绝不伪造 state**，只落 `attributes._unmodeled_service` + 登记 `store.unmodeled_calls`，让闸门**如实降级为「后置条件未验证」** | `af_vhass/harness.py`：`if new_state is None: continue` —— **静默跳过**，下游无从得知「这条没验到」 |
| **读取结果为空** | `debug_bridge.read()` 用 `status ∈ {empty, filtered, ok}` **区分 count:0 的三义**（真没有 / 被过滤 / 有数据） | — |
| **截断** | `_truncate` **保证截断结果仍是合法 JSON**（闭合未完成括号 + 注入 `__truncated__:true`） | — |
| **错误** | `errors.ErrCode.AMBIGUOUS` 把「未触发 / 过 TTL / id 不存在」**显式编码成一个错误码**，禁止静默 `count:0` | `af_mcp` 有 `ServiceError` → `isError`，但无错误码枚举层 |

**建议**：至少把第一条（未建模服务不留痕 → 留痕）改掉。
`af_vhass/harness.py:69` 的 `if new_state is None: continue` 改成记录到 `harness.unmodeled_services`，
让 `forge sim` 的返回里能显示「有 N 个动作无法验证」——**这直接关系到 2.1 的「期望值断言」能不能可信**。

---

## 5. 一句话总括

> autoflow 最值得抄的不是某个模块，而是一条**贯穿式范式**：
> **「契约一处定义 → 多目标幂等派生；校验分层（结构 → 逻辑 → 硬拦）；错误带码 + 位置 + 自修正提示；对未知一律 fail-open 于报告侧、仅确证才硬拦；每条产物都必须声明「怎么算对」。」**
>
> 而其中**最尖锐的一刀**是：**AutoForge 的 IR 里没有「期望值」**——
> 这让 `forge sim` 只能回答「跑完了吗」，回答不了「跑对了吗」。

---

## 6. 建议的优先级

| 优先 | 项 | 规模 | 为什么现在做 |
|---|---|---|---|
| **P0** | 2.1 IR 期望值 + sim 断言 | M | 直接决定 sim 是否有意义；与 NL 覆盖率互补 |
| **P0** | 2.2 Diagnostic 加 hint | **S** | 半天量，直接提升 agent 一次成功率 |
| **P0** | 实体目录（上一份报告） | M | 你提的痛点 |
| **P1** | 2.3 diff 签名兜底 | S | Agent 重写 IR 时 diff 才可信 |
| **P1** | 4. 未建模服务留痕 | **XS** | 一行改动，但影响 sim 结论可信度 |
| **P1** | 2.9 凭据热重载 | S | `forge watch` 常驻进程的实际痛点 |
| **P2** | 2.4 爆炸半径 / 2.5 所有权 / 2.6 待批队列 | M | 多 agent 接入后必需 |
| **P3** | 2.10–2.16 经验闭环 | L | 长期价值，待真实需求驱动 |
