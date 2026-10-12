# ADM 联动执行计划 · AF（AutoForge）

> 出品：关键决策部（DCD）
> 版本：v2.5 联动落地版（基于 v3_DCD 路线图 + 全部裁定链）
> 修订 2026-10-04-2（DCD）：**§5.3/§5.4 自相矛盾收口** + §5.1/§5.4/§5.6 账目更正 + 窗后验收端点名更正，依据 `decisions/20261004-AF-v2.6前置与执行计划更正-裁定.md`
> 前提：homesdk **0.3.1** 已投递 NAS（含 `time` 模块）
> 落点：`E:\NAS\AutoForge\docs\`
> 依赖裁定：`20260929-ADM三仓联动七问`、`20260929-联动协议修订-事件流与收件箱`、`20261001-AF-homesdk接入四问`、`20260928-AutoForge-v2.1设计难题A-F-决策.md` §D、`20260930-AutoForge后续优化三项-裁定`

---

## 一、AF 的角色

AF 是**自动化中枢**：DSL 编译 + 安全验证 + 部署 + 回滚的权威。联动里 AF 负责：
- 发布 `af/automation/fired|failed` 事件（不 retained）；
- 订阅 `ma/insights` 消费 MA 洞察 → 生成自动化提案；
- 发布 presence（`adm/autoforge/status`+`caps`）；
- 通过 MCP 提供工具面供 DB 调用（draft/verify/deploy）。

**AF 当前零 MQTT**（`af_bus` 是进程内总线）——联动第一件事就是补 MQTT 桥。

---

## 二、任务卡（按依赖序）

### 第 0 步：homesdk 0.3.1 接入（分两步，裁定 §三 A）

| 子任务 | 验收 |
|--------|------|
| ① 仓内 vendor 从 0.1.1 → 0.3.1（`COPY` wheel + `pip install`，`pyproject.toml` 改 `>=0.3.1`） | 双版本门禁结论一致（已实测排除技术风险）。**0.3.1 权威 sha = `b4b5d6bbe424…`**（源码入库 `5e4ba33` 后重建，可复现；首投 `36fdf77a…` 作废——裁定 §〇） |
| ② **时区接入 `homesdk.time`**（裁定 §五）：AF 现有 `af_time.house_tz_name()` 退化为 fallback，主路径改调 `homesdk.time` | `AF_TZ` → `HOMESDK_TZ`；`tests/unit/test_af_house_tz.py` 9 例仍绿 |
| ③ NAS 镜像重烤搭下一次既有变更窗（DB 写面/MA PII 窗），⛔ 不单独开 | — |
| ④ AgentOps 门禁模板已授权改（裁定 §四）：去 `continue-on-error` + `GATES_PYTHON` 改"探测不到就报红" + 模板自检 | 与三仓 CI 变更合批 |

### 第 1 步：AF MQTT 桥（新增，第一档加固）

| 子任务 | 验收 |
|--------|------|
| ① 新建 `af_mqtt_bridge.py`：复用 homesdk `mqtt.get_client`/`presence.advertise`，连接 broker（fail-closed 缺凭据即抛） | 容器启动后 `adm/autoforge/status=online`（retained + LWT） |
| ② 发布 `af/automation/fired` 与 `af/automation/failed` 事件（不 retained） | 自动化执行后 mosquitto_sub 能看到 |
| ③ 订阅 `ma/insights`（MA 发布的洞察事件），走"生成提案→**只落盘**→人批后才进可执行队列→不自动部署"（裁定 20261002 §三 **④A**：独立持久队列，重启不丢；`conf` 仍被 `INSIGHT_CONF_CAP` 封顶在 ask 带） | 一次端到端：MA 发 `ma/insights`，AF 订阅后落 `{store_root}/insight_proposals/pending/*.json`；`af_draft` 造图后 approve 才进 `af_pending` |
| ④ 订阅 `butler/inbox/*`——**不订阅**（收件箱是 DB 的，AF 不替 DB 说话） | — |

### 第 2 步：AF presence 发布

| 子任务 | 验收 |
|--------|------|
| ① `homesdk.presence.advertise(client, "autoforge", caps={mcp: true, tools: [...], version: "2.5"})` | retained `adm/autoforge/status`+`caps` 可见 |
| ② LWT 保离线 | kill -9 后 broker 发 `offline` |

### 第 3 步：F9 group 节点（裁定 D 已改判：直接建，撤销决策门）

| 子任务 | 验收 |
|--------|------|
| ① 前置修复：`ir_version` 从 `const "0.2.1"` 改枚举 `["0.2.1","0.3.0"]` + 放宽 `additionalProperties`（前向兼容） | 旧图（0.2.1）可共存读 |
| ② IR 加 `group` kind（`mode: sequence|parallel`、`children` 递归），ir_version 升 0.3.0 | 一切可编译/仿真/渲染回 NL（唯一真相锚） |
| ③ 编译/仿真/NL 渲染/诚实报告对 group 的支持（四段管线全过） | 两条有依赖的自动化编排为单 group，simulate 正确演化、按依赖序下发 |
| ④ **原子性验收**：编排后模拟中途失败，断言"已部署部分全回滚、无半部署态" | — |

### 第 4 步：AF MCP 工具面完善（供 DB 调用，实验档）

| 子任务 | 验收 |
|--------|------|
| ① `af_draft` → `af_apply(stage="simulate")` → `stage="dry_run"` → 人批 → `stage="save"` 的工具链，DB 通过 MCP 调用链"拟→验→批→部署"（命名口径按裁定 20261002 §三 **①A**：以 AF 现名为准，**不新增 `verify`/`deploy` 别名**——"验"与"部署"是同一工具的不同 stage，天然不可能"验着验着变部署"） | DB 调一次 `dry_run`（不实际部署）；未知 stage 拒收 |
| ② ask 通道已有（DB 轮询 `/api/asks/pending`），建自动化是自然扩展；**裁定 ③A**：`/api/asks/answer` 必须回报 `channel_error`——DB 见 `true` 须告警并**停止把该 ask 标为已答**（"用户点了按钮没生效也没人知道"= 假绿） | 两个方向都有断言：`inbox_key_missing` → `channel_error=true`，签名落盘成功 → `false` |
| ③ 契约测试：`tests/contract/test_af_db_contract.py`（断言 ask+answer schema + 鉴权 + INBOX_KEY 必须启用）+ `tests/contract/test_af_insight_queue_contract.py`（④A 队列 13 项：重启不丢、approve 不部署、队列满不丢消息） | INBOX_KEY 未设即 fail-closed 拒收（裁定 Q5 已裁"通道生效前提"） |

### 第 5 步：后续优化三项（已裁，并入本版排期）

| 子任务 | 验收 |
|--------|------|
| ① 单写者租约（裁定 A：抢不到锁降级只读） | serve 启动 `FileLock.try_acquire`，失败→只读 |
| ② af_persist 加校验和 ~~+ 追加写~~（裁定 B：不做全量 af_eventlog；**追加写这半句经 2026-10-04 裁定判"不适用"**，见 §5.3 #3） | 给 `af_persist` 补 SHA256（对齐 `af_store`）——**已交付** |
| ③ import-linter 分层（裁定 B→A 渐进：先告警后强制） | `.importlinter` 配置 + 既有违规清单（预计≈0） |
| ④ tick 线程自愈（裁定 C：区分原因重启，必须补 SAFE HALT 不重启接缝测试） | "SAFE HALT 后 watchdog 不得重启"接缝测试绿 |

---

## 三、不在本版做（已裁并登记）

- AutoForge→AutoFlow 交接协议（AF 未成熟，不排期 + 先定一页交接契约）
- v3.0 复合编排主体（等 F9 group 落地后评估）
- vMA-2.0 知识图谱（默认不做）

## 四、停机窗口（合并，不单独开）

AF 镜像重烤 + AgentOps 模板生效 + DB 写面修复 + MA PII 回填/R1/R3 生效——**四件事合并为一次窗口**，不要四次不可用。

**裁定 20261002 §一 把顺序写死了**（窗口内照此执行，回滚按反序逐件退）：

```
补 homesdk 账（✅ DCD 已执行，commit 5e4ba33）
  → 重建 wheel（✅ 已执行；⚠️ 这一行原来写的"权威 sha b4b5d6bbe424…；首投 36fdf77a… 作废"是 **0.3.1 那枚**——当场实测 `sha256(dist/homesdk-0.3.1-…whl)` 前缀 = `b4b5d6bbe424205b`。AF 现钉的是 0.3.2 = `19bc83a67a96931c…fb5505`，真源 `tests/unit/test_mqtt_compose_env.py:163`）
  → 重烤 AF 镜像（**已烤过两次**：`8406757` 那次见 §5.3 第 19 行；在役面现读还含 `write_gate` ⇒ 至少又烤到 `488901c` 之后。仍缺的是"把 HEAD 那 26 个提交烤上去"那一轮）
  → AgentOps 模板生效 + DB 写面遗留 + MA PII/R1/R3（待窗）
回滚顺序：模板 → 镜像 → wheel → 账
```

**窗后 AF 侧验收（缺任一项即该步未完成，不许用"配置正确只是没抓包"过账）**：
① `compose ps` 服务在；② **`/api/health`** 返回 200（⚠️ AF **没有** `/health`，真名是 `/api/health`——本节原写 `/health`，2026-10-04 更正）；③ `mosquitto_sub` 抓到一条 `af/automation/fired`，且其 `ts` 是**家庭墙钟**口径；④ `adm/autoforge/status` retained 值为 `online`。

---

## 五、进度快照与下一版细化（DCD 2026-10-04 增补）

### 5.1 当前真实进度（实测，HEAD `19ca389`；2026-10-04 DCD 现读 HEAD `8689b39`，分支 `master`）

| 版本 | 状态 |
|------|------|
| v2.0.1 收口 | ✅ 已投产 |
| v2.1 仿真保真 | ✅ |
| v2.2 安全能力 | ✅ |
| v2.3 group 复合编排 | ✅ |
| v2.4 经验闭环 | ✅（含 NAS 部署） |
| v2.5 NL 双向保真 | ✅ 本地全绿；NAS **已烤过**（§5.3 第 19 行那次到 `8406757`），但在役面比那次又新（现读含 `write_gate` ⇒ ≥ `488901c`，即 `488901c..master` 那 17 个提交之后），上界读不出 ⇒ 还有一轮要烤 |

**联动计划**：第 0-4 步**代码全交付**；第 5 步（后续优化三项）①③④ 交付，**② af_persist 顺序追加写经 DCD 判定"不适用"**（裁定 `20261004-AF四件与DB一件与MA五件-裁定.md` §一.4——本仓按裁定要求记为"经 DCD 判定不适用"，**不是"未做"**，避免下一轮重报）。基线：2500 passed / 0 failed。

> ⚠️ **验收等级更正（AF 2026-10-04 指正，DCD 采纳）**：第 0-4 步仍差**窗内两件**——NAS 镜像重烤、`AUTOFORGE_MQTT` 的开法（本文件 §四 与 18:35 裁定 §二 Q1）。在那两件完成前，第 1/2 步（MQTT 桥、presence）的验收等级是 **EXEMPT，不是 VERIFIED**；且**不得与 §5.2"唯一的硬阻塞"分开过账**（两者一致，但不能分开记功）。
>
> **2026-10-09 现读追注（不动上面那条的裁定地位）**：这两件已不对称——"镜像重烤"那件按字面**已执行过**（§5.3 第 19 行 + 在役面现读含 `write_gate`，见 §5.2 更正），`AUTOFORGE_MQTT` 的开法**仍未执行**（在役 `linkage.wired=false`）。所以 EXEMPT 这一档**继续成立、且现在只剩一个理由**：桥没开。上面那句"§5.2 唯一的硬阻塞"指的是 §5.2 的旧标题，现文字已按读数改写。

### 5.2 硬阻塞的现状（2026-10-09 现读更正）：不是"镜像没烤"，是"桥没开 + 在役面落后 HEAD"

> 本节原文写的是「AF 代码全绿但镜像未烤——**这是 AF 唯一的硬阻塞**」。那句在 2026-10-09 已不成立，按现读改写；旧文字留在 §5.3 第 19 行（那一次重烤的执行记录）与本段的更正说明里，不删档。

现读三条：

1. **镜像烤过，而且不止一次**：`8406757` 那次见 §5.3 第 19 行（`BUILD_EXIT=0`、新镜像 `sha256:cfba9043…`、`/mimo/` 200）。今天再读在役面 `GET /api/health` ⇒ 响应里有 `write_gate":"open"`，而这枚键是 `488901c`（2026-10-08）才加的 ⇒ **在役那份代码 ≥ `488901c`**，比第 19 行记录的那次又新。谁烤的、烤到哪个提交，这台机器读不出（要部署机 `docker image ls` / `docker inspect`），已作为一格待确认交给出资人。
2. **在役面仍落后 HEAD**：`488901c..master` 有 17 个提交（含卡1 `2d92bb1`、卡3 `71f5682`、现场两真 bug `2979e77`）。在役代码读不出上界 ⇒ 只能说"至少差这 17 个里的后半段"，不能说"= HEAD"。
3. **桥没开**（这一条才是联动验收的真闸门）：在役 `linkage` = `{wired: false, state: "unwired", degraded: false, reasons: []}` ⇒ compose 里 `AUTOFORGE_MQTT` 未打开、`MQTT_*` 五条引用按 §5.3 第 10 行留空。§四 那四条窗后验收（`mosquitto_sub` 抓 `fired`、`adm/autoforge/status` retained）在开关打开之前**跑不出绿**，且这是"改他人可见状态"级别的动作 ⇒ 等出资人点头与窗口。

副产物一条（正面读数，值得留）：在役同一份响应里 `readonly: true` 与 `write_gate: "open"` **同时**出现 ⇒ 裁定 `20261008-AF两件与MA四回执-裁定.md` §一 B 当初描述的那组三读数分叉，现在**一屏同读**就能看见，新键按设计在干活。另：`tz.source = "fallback"`（机制层 `homesdk.time` 在、`resolved_by_name = true`、`utc_offset = 8.0`），意思是部署机六个键一个都没给（`TZ_ENV_KEYS` 现读实际顺序：`HOMESDK_TZ`、`TZ`、`HOMESDK_AF_TZ`、`AF_TZ`、`HOMESDK_TZ_OFFSET_HOURS`、`TZ_OFFSET_HOURS`；每个基键先试 `HOMESDK_` 前缀再试裸键），靠仓内缺省 `TZ_FALLBACK_NAME = "Asia/Shanghai"`（`:88`）解析成功——不是 bug，但想让口径显式写在部署配置上，是部署机加一个 env 的事。

### 5.3 下一版 v2.6 细化任务

| # | 任务 | 验收 | 前置 |
|---|------|------|------|
| 1 | NAS 镜像重烤 + 窗后四项验收 | **前半按现读已执行过，且不止一次**：`8406757` 那次见第 19 行；在役 `/api/health` 今天现读含 `write_gate":"open"` ⇒ 至少又烤到 `488901c` 之后。**仍差两样**：①"把 HEAD 那批烤上去"那一轮（`488901c..master` 17 个提交，含卡1/卡3/现场两真 bug）；②§四 四条硬读数（**`/api/health`**，不是 `/health`）里 `af/automation/fired` 抓包与 `adm/autoforge/status` retained 两格——桥未开（现读 `linkage.wired=false`、`state="unwired"`）就跑不出。另有一格待确认：**是谁、从哪个提交烤的第二次**（这台机器读不出，要部署机 `docker inspect`） | 合并窗 + 出资人点头开 `AUTOFORGE_MQTT` |
| 2 | v2.5 部署到 NAS | 容器起 + `/api/health` 200 | **已达成，今日按现读复核过**：`GET http://192.168.2.200:8787/api/health` ⇒ **HTTP 200**、`ok=true`、`store_ok=true`、`version=0.1.0`。这一行没有残留；残留都在第 1 行与 §5.2（在役面落后 HEAD、桥未开） | ~~合并窗~~ 无（已执行） |
| 3 | ~~af_persist 顺序追加写~~ → **已收口：经 DCD 判定不适用**（裁定 `20261004-AF四件…` §一.4）。本仓记录须标注"经 DCD 判定不适用"，**不是"未做"** | — | — |
| 4 | F7 撤销前端残留（`LiveView.vue` 撤销按钮通路 + 10s 定时补数） | **已交付**（commit `3039c62`：10s 定时补数、读失败不清空、窗口长度只印服务端给的数）。残留：真机指针事件未验 | — |
| 5 | AgentOps 建仓后模板首次 commit | `gates.yml` 无 `continue-on-error` | 建仓授权（裁定已给） |
| 6 | LWT `kill -9` 离线分支 + 镜像内 vendor 自检第 7 条 | 真 broker 上验 | 1 |
| 7 | 单写者租约补 **MCP 真机写面**（只 check 不 acquire；被持锁时 `ServiceError(503)`；MCP 侧文本带固定前缀 `READONLY_DEGRADED:` 并登记进契约表） | **AF 半边已交付**（`7dbd640`；本批复核实跑，证据与腿名见执行记录 §二之七十）：三条判据各有名有腿——HTTP 503 ⇒ `test_serve_lease_single_writer.py:230`；MCP 拒收且不构造传输层 ⇒ `:216`（持锁方是**真子进程**）+ `:241` 前缀腿；锁空闲照常下发 ⇒ `:262`/`:272`（`_LOCAL_HELD` 认出本进程，闸门不反装）。机制现读：`af_service.py:1687` 前缀常量、`:1690 _single_writer_check()` 只 `held_by_other()` 不 acquire、`:1702 status=503`；`af_mcp.py:988` 对 `ServiceError` **原样**回传（`:991` 注释：套壳会把前缀挤出开头）。**前缀位置已按裁定 20261007 §二 戊A 变更过一次**：现在在 `message` 字符串值开头而非整段 text 开头（`af_mcp.py:992-993`，DB 口径变化随交接单交 DB，见第 14 行），腿为 `test_dcd_20261007_mcp_failure_envelope.py:110`；两文件合跑 **`18 passed` RC=0**。**未闭的一格不归 AF**：验收后半句"并登记进契约表"——本批现读 `grep -n "READONLY_DEGRADED\|单写者\|503\|租约"` 对契约表与 0.3.2 规格**各零命中（`GREP_RC=1`）**，DCD 侧唯一写过该字面量的是裁定自己（`20261004-AF四件…-裁定.md:23`）⇒ 登记属 **DCD／homesdk 动作**（AF 已投 `inbox/20261003-…单写者租约…-决策申请.md:56` 与 `inbox/20261007-AF-降级面契约缺口四格…-决策申请.md`）。**真机半边另缺**：503 需"serve 持锁 + 另一进程调 MCP"两进程拓扑 ⇒ 合并窗动作，子进程腿不等价于 NAS 现场 | 无（裁定 `20261004-AF四件…` §一.1 A） |
| 8 | 有界缓存 **注册表式门禁**（容器属性名 + TTL 来源 + 上限常量 + 测试 id；三判据；5 处一次登记 + 2 处误报带理由入基线） | **已交付**（`c0476e2`；本批按 HEAD 现读复述，读数见执行记录 §二之七十）。今日门绿行逐字：`[有界缓存] 注册表 3 项双腿齐全且测试 id 被收集；固定键 3 项带理由；扫到增长容器 125 个，其中基线冻结 114 个、就地豁免标记 12 处；死写容器 0 个（判据 E 按名字在全仓数读取点，3951 个名字被读到过）`，`GATE_RC=0`；反例套件 **`34 passed in 35.98s`**（交付时是 21 条，判据 E 由 §二之四十七 加进来）。"新增增长容器未登记即红"这条验收有名字且今日仍在位：`test_bounded_caches_gate.py:209`，配套 `:218` 就地豁免、`:231` 基线只减不增，`exit 2` 四档在 `:514`/`:522`/`:531`/`:541`。裁定那句"5 处一次登记 + 2 处误报入基线"的**存量口径已在交付当批按实测更正**（§二之三十 §二：扫到的是 76 个、其中双腿齐全只有 2 个；那两处误报根本不进扫描口径，是按"就地豁免带理由"落的，不是基线冻结）⇒ 表上不再复述"5/2"。注册表今天多出的两项是后续批次**按这道门入库**的（正是它起作用的形状）：`af_auth.RateLimiter._blocked`（配对 bootstrap 那批）与固定键 `af_mqtt_bridge.AfMqttBridge.caps`（降级播报那批）。接线：`gates.sh:238` 跑门、`:239 cache_rc`、`:371-377` 把"读不出"与"红"分成两种结论，CI `quality-gates` 同口径 | 无（同上 §一.3 B） |
| 9 | 安全遗留（裁定 18:35 §一 / §五）：长期码加可配绝对上限（默认 180 天，`AUTOFORGE_AUTH_LONGCODE_TTL_DAYS`，0=显式关）+ `af_auth.list()` 输出"距生成多久"；**MCP 未设令牌默认拒绝**（`AUTOFORGE_MCP_ALLOW_NO_TOKEN=1` 才放行）；`GET /api/asks/pending` 加 `Depends(_read)`；`/api/user/auth-codes` 收紧到 `_write` 且 owner 面给明文、非 owner 面只给掩码+状态；`docstring` 里的 `demo/forge2026` 明文默认凭据移除；"只在可信 LAN"写成显式部署前提进 README + compose 注释 | **AF 半边已交付**（本批逐格现读，证据与腿名见执行记录 §二之六十八）：长期码上限 `AUTOFORGE_AUTH_LONGCODE_TTL_DAYS` ⇒ `test_dcd_20261004_auth_limits.py:40`/`:96`/`:105`；`age_s`（距生成多久）⇒ `af_auth.py:803`；MCP 默认拒绝 ⇒ `test_dcd_20261004_mcp_default_deny.py` + `test_v0_8_auth.py:89`/`:98`；`GET /api/asks/pending` 的 `Depends(_read)` ⇒ `af_api.py:647`；auth-codes 收紧 ⇒ `af_api.py:1085` 的 `_write_scope` + `reveal` 分层，四条验收原文逐条有腿（`:450` 用只读令牌 `tok-reporter` 实测 403、`:492` 掩码档保留状态字段、`test_v1_4_credentials.py:13` 掩码不可还原）；`forge2026` 明文在 `src`/`tests` 下 **0 命中**。**唯一未闭的一格不归 AF**：裁定要求"只在可信 LAN"同时进 README 与 compose 注释——README 半边在 `README.md:215-229`，compose 半边由 `README.md:228` 明写「由部署方在 NAS 上落（本仓不改 compose，铁律 #3）」⇒ 属 **SP／部署方动作**，AF 依此不动 `docker/docker-compose.api.yml`（本批差点顺手补注释、并"更正"那句 `Round 1 只读`，两处读数证明不该动：该句在 `:45/:46` 的缺省档下为真） | 无 |
| 10 | 窗内开关（裁定 18:35 §二）：compose 补齐 `MQTT_HOST`/`MQTT_USER_*`/`MQTT_PASSWORD` 引用**但留空**，`AUTOFORGE_MQTT` 维持缺省关；`paho-mqtt` 钉上界 `>=1.6,<2.1` | **仓侧半边已交付**（`729343b`，§二之五十一）：compose 补齐 `MQTT_HOST`/`MQTT_PORT`/`MQTT_KEEPALIVE`/`MQTT_USER`/`MQTT_PASSWORD` 五条引用且值一律留空、`AUTOFORGE_MQTT` 缺省 `0`；键名不手抄——唯一真源是 `Dockerfile.api:26` 钉死的那枚 vendored wheel 的 `homesdk/mqtt.py`（`MQTT_USER_*` 那种带作用域的形式与 `MQTT_USERNAME`/`MQTT_PASS`/`MQTT_PASSWD` 别名在注释里点名但本仓不用），"留空是真 no-op"由真模块当场判（空 host 抛 `MissingEnv`、空 port 落回 `DEFAULT_PORT`、空账密抛 `MqttCredentialsMissing`）。`paho-mqtt>=1.6,<2.1` 早已钉（`pyproject.toml:42` 与 `:54`，裁定 Q2=B）。**窗内半边仍待第 1 件**：重烤后服务照常起、桥 no-op；开关真打开走**非停机窗**配置推送（先 `compose exec` 预检 `paho_available()` + `broker_settings()` 通过） | 1 |

| 11 | **落地 DCD 20261006 §二**：段间累计封顶两档（S=1000 段 / T=20000 步越档告警，2× 才 `_fail`，落 `AuditLog` + 监护视图常驻指示）+ trace 截断（N=1000 + `trace_dropped`，不进 IR schema） | **已交付**（commit `e876678`，13 条腿）。残留：真机 HA 上未量（要等第 1 件那个窗） | — |
| 12 | **落地 DCD 20261006 §一**：配对 bootstrap 走**两个匿名端点**（裁定 B）+ 限速 `request` 6/min、`redeem` 10/min、超限锁该 IP 于该端点 5 分钟 + 维持 8 位/300s/单次 + owner 侧"暂停接受配对请求"开关 | **已交付**（commit `2f86af9`；本批复核实跑 `23 passed in 4.44s`）。落成的两条匿名端点：`POST /api/mcp/pair/request`（`af_api.py:1129`）/ `POST /api/mcp/pair/redeem`（`:1158`），两条各自先过 `_bootstrap_limit`（`:1140`/`:1161`，各一只桶 ⇒ request 超限不牵连 redeem）；数值 `af_auth.py:512-514`（6 / 10 / 300s），码参数 `ttl_s=300` + 8 位 + `consume()` 单次；owner 开关 `:1182`（写后回读比对）/`:1198`，关掉后匿名腿 `:1141` 直接 409 拒收；"匿名射程恰这两条、整张工具表仍 default-deny"由 AST 腿钉住（`test_dcd_20261006_pairing_bootstrap.py:312`、`:334`、`:87`），**被驳回的 A 没有被偷偷落地**：`af_mcp.py:548`/`:564` 两工具 scope 实测仍 `"write"`。**字面路径不在任何外部真源上**：裁定 §一 原文、契约表、0.3.2 规格 grep `api/pair` 全 **0 命中** ⇒ 拼法属 AF 自决，不是一处偏离（执行记录 §二之四十二 那句"裁定说 `POST /api/pair/*`"是把我方**申请书建议**误写进引用位，本批已更正，§二之六十七）。**真机半边本批已补**（部署实例 `192.168.2.200:8787`，全匿名无 Bearer）：`POST /api/mcp/pair/request` + `{}` ⇒ **200**、`POST /api/mcp/pair/redeem` + `{}` ⇒ **400**（命中 `no_code` 分支），CONTROL `POST /api/pending/list` ⇒ **403** ⇒ 两条匿名腿在**在役镜像**上真可达、且不是整面无鉴权。代价一格：`PairRequestBody` 无必填字段（`af_api.py:152-153`），那条 `{}` 探测**等于真发起了一次配对**，实例生成过一枚 8 位码并推过用户弹窗（单次、300s 自动过期，响应未落盘、码值未读取）——见执行记录 §二之六十七 §5 的误判记录。**仍缺**：超限 429 与暂停档 409 的真机读数（前者要连打 6 次 ⇒ 会连弹用户弹窗，后者要翻在役 owner 开关），属"改他人可见状态"，不在只读射程内 ⇒ 待出资人确认或与窗口同跑；仓侧这四档已有腿（`test_dcd_20261006_pairing_bootstrap.py:95`/`:110`/`:224`/`:235`） | 无（裁定已给） |
| 13 | **用户视角 UI（`ui-user-mimo`）对接同源服务层**：`forge serve --ui-user-dir` 挂到 `/mimo`，`UI_USER_PREFIX` 为唯一真源（vite `base` / compose 卷 / CLI 参数四处由测试对账）；"没请求 / 请求了但目录不在 / dist 缺 index.html"三档分别 404 / 503 / 503，目录穿越判据用 `is_relative_to` | **AF 半边已交付**（执行记录 §二之六十）：`tests/unit/test_ui_user_mount.py` 17 条全绿 + 本机浏览器黄金路径（`/mimo/` 出用户端 index、assets/`sw.js`/manifest mime 正确、深链回落、`/` 仍是开发控制台）。**残留＝窗内那一半**：NAS 卷挂载与镜像重烤。**⚠️ 顺序地雷**：NAS 那份手改 compose 已经传 `--ui-user-dir`，而镜像里的 `forge serve` 还不认这个参数 ⇒ typer 退出码 2 ⇒ 崩溃循环会把 :8787 连开发面板一起拉下水——**必须先重烤镜像再起容器**，不能先改 compose | 1（合并窗） |
| 14 | **落地 DCD 20261007 §二 的 AF 半边**：丁A `/api/health` 加联动桥读数（读不到如实 `unwired`）、戊A MCP 四条异常路径统一 JSON 信封带 `code` | **已交付**（执行记录 §二之六十）：两脸同一份 `linkage`；`test_dcd_20261007_mcp_failure_envelope.py` 9 条。甲A（`advertise` 发 degraded）/乙A（契约 v2.1 给 `af/automation/failed` 补 `code`）/丙A（owner 策略性暂停独立档）随 homesdk 0.3.3，AF 不绕行。**读数口径变化已随交接单交 DB**：`READONLY_DEGRADED:` 前缀从整段 text 开头移到 `message` 字符串值开头 | — |
| 15 | 第二十轮 F2/F1：递归遍历腿接上单一真源预算 + 扫描器站序与 `scan()` 契约 | **已交付**（执行记录 §二之六十一）：18 处实测崩溃站点（496–997 层）全部引同一份预算；闸门侧补 `assert_param_budget`（闸门与遍历腿同档拒绝，容器 64 放行 / 65 拒）；F1 的 `_check_vars` 早于 `_check_expr` 已倒正；新增两格诊断 `TRIGGER_INVALID`/`PARAMS_TOO_DEEP`；判据 41 + 16 条 | — |
| 16 | **本批新登记的两件小缺陷**（现读坐实，**已交付**·执行记录 §二之六十二）：① `ui-user-mimo/src/api/mock.ts:11` 把 `MCP_URL` 硬编码成 `http://192.168.2.200:8787/mcp`，被 `AgentsView.vue:24,62` 直接显示与复制 ⇒ 换部署地址要改源码，应走 `import.meta.env` 并与 `api/env.ts` 的既有开关同源；② `af_scheduler._group_sub_satisfied`（`:284`）对**嵌套 group** 落到末尾 `return False`，而该行注释只写"time/sun 等非事件驱动类型"⇒ and 支里套 group 会**静默判不成立**，注释口径也少了这一档 | ①已达成：`MCP_URL` 搬进 `src/api/env.ts`，三档取值（显式覆盖 > 同源 `location.origin` > 空串），`mock.ts` 里的 LAN 字面量由反向断言拦住，`index.ts`/`AgentsView.vue`/契约锚点跟着同源；②已达成：嵌套 group 交回 `_satisfied`（那一档同时带深度预算与 and/or 口径），`and` 支补 `_depth + 1`，注释口径改到与代码一致。**两处都做了变异自证**（`||`→`??` 让中间档从 `http://nas.local/mcp` 塌成空；把交回退回 `return False` 让 5 条新腿红 3 条）。读数：UI 套件 75 pass / 0 fail、`test_p1_6_and_trigger.py` 9 passed、全量 3407 passed、`GATES_RC=0`；产物口径（§二之六十三 附格补测）：`npm run build` `BUILD_EXIT=0`、`grep -rl 192.168.2.200 dist` **0 命中**、`dist/index.html` 资源前缀为 `/mimo/assets/` | — |
| 17 | **DCD 新候选（AF 不自决）**：`af_conflict_runtime.dispatch()` 把内省包在 `except Exception` ⇒ `_audit_degraded("introspect", …)` ⇒ **照常执行**，即 `extract_entity_ids` 一失败就绕过冲突锁（F12 同族的 fail-open）。编译期闸门（第 15 件）已让这条路更难到达，但"拒绝执行 vs 降级放行"是政策问题 | 裁定落地后该档有测试可判红 | **已裁已落**。裁定：`20261008-AF两件与MA四回执-裁定.md` §二「A（内省异常一律 fail-closed）+ 挖不出实体放行 + Q2 是」；回执追认：`20261008-AF冲突守卫内层与band缺省-裁定.md` §二（并把同族推广到 `request` 站、内层仲裁器、`band` 缺省档、冷却登记四站）。落点：`af_conflict_runtime.py:19` 模块口径逐字「内省 / request 自身抛异常 -> fail-closed 拒绝 + 落审计 + owner 可见通知」，内省站 `:296-302`（`:300` 调 `_notify_guard_blind`），`:470` 注明"拒绝不能只躺在日志里"，`_audit_degraded` docstring `:530` 记 `introspect`/`request` 两站改记 `False`。**腿当场跑过 `4 passed, 20 deselected`（RC=0）+ `2 passed`（RC=0）**：`tests/test_af_conflict_runtime.py:458`（`:466` 断 `introspect_failed:TypeError`、`:470` 断审计相位）、`:487`（**内省成功但无实体仍放行**——裁定分开的那第二条放行点，不能顺手一起改成 fail-closed，否则空实体自动化全废）、`:526` `test_the_two_allow_points_are_separated_in_source`（AST 结构腿，直接取 `extract_entity_ids(` 调用的 handler 源码，裁定原话"这是本轮最值钱的一条腿"）、`:566`（`:589` 断 `guard_blind` 常驻证据）。记账见执行记录 §二之七十七 §四 |
| 18 | **NAS 现场回灌的两件**（部署机手工补丁与 HEAD 分叉，本批收进版本库）：① `af_store.load_conf` 把"快照还没写过"和"快照坏了"包成同一种 `ValueError`，而两处调用方（`af_service._conf_of:1067`、metrics 聚合 `:2020`）只兜 `FileNotFoundError` ⇒ 首次运行被读成 500；② compose 的令牌键写成复数（代码里没有那枚读数）⇒ 宿主机注入的值喂给一个空位，鉴权照旧失败且不留原因 | ①两种"读不到"分开，降级腿可判红；②compose 里 `AUTOFORGE_*` 键名与代码读数**双向同源**（新门 `tests/unit/test_compose_env_key_source.py`） | **已交付**（执行记录 §二之六十三）。同批把 NAS 手工补丁与 HEAD 的余下差集核完：`af_cli.py`/`af_api.py`/`vite.config.ts`/`package-lock.json` 四处 HEAD 已含或已更优，无遗漏 |
| 19 | **NAS 侧用户视角 UI 上线（本批已执行）**：原先登记的两条前置按现读校正——① 那枚 0.3.2 wheel **不需要手工 scp**：`2f86af9` 在版本库里把它记成 `R062`（0.3.1→0.3.2 改名），部署机 ff-merge 到 `8406757` 后目录里只剩 `homesdk-0.3.2-py3-none-any.whl`（49374 字节实测）；用 `--diff-filter=D` 查删除会误判成"版本库没管这枚"。② 部署机确实没有 node ⇒ dist 只能本机 `npm run build`（`BUILD_EXIT=0`，产物内 LAN 字面量 **0 命中**）后 scp，现场 `files=23 LAN=0`。执行序：`git push nas master`（`9aa6499..8406757`，`ls-remote` 自证同一枚）→ 部署机先存手补 `backups/nas-handpatch-20261008.patch`（374353 字节）再 ff-merge → `docker compose --env-file .env -f docker-compose.api.yml build`（`BUILD_EXIT=0`，新镜像 `sha256:cfba9043…`）→ **起之前**用 `docker run --rm --entrypoint forge … serve --help` 数选项行（`--ui-user-dir` ⇒ 2）并 `import homesdk`（⇒ 0.3.2），把 typer exit 2 那一档在没拆容器之前排掉 → `up -d` | `/mimo/` 200 + 1040B + `<title>AutoForge</title>` + `/mimo/assets/index-D22QXJ0K.js`；资源 200 且 `text/javascript`；`/mimo/agents`、`/mimo/login` 深链回用户端 index；`/api/health` 200；开发面板 `/` 回归 200 未被挤掉；容器日志 `取得单写者锁` + `用户端静态托管：http://0.0.0.0:8787/mimo/（dist=/mimo）`，无 typer exit 2；浏览器端到端：登录 ⇒ Agent 列表 1（真实 `af_admin`）、自动化 **45 条**真实归档、MCP 卡片端点由 origin 拼出、控制台零报错、退出登录回 `/mimo/login` | **已交付**（执行记录 §二之六十四）。两条现场教训入册：`docker-compose.api.yml` 不是默认文件名，compose v2.40.3 必须 `-f`（第一次照 §6.1 那句直接 `compose build` 拿到 `no configuration file provided`）；**卷挂的是目录 inode**——容器起来后把 `dist` 改名再放同名新目录，服务端仍读旧包（host `index-D22QXJ0K.js` vs served `index-DUSr5ZJN.js`），`docker restart` 不重新解析，只有 `--force-recreate` 才换；验收要做"仓库 index 的资源名 ↔ 服务端 index 的资源名"字符串比对，只看 200 不够 |
| 20 | **部署现场现读出的第 4 处同源洞**（不在原清单，本批新登记并修）：`ui-user-mimo/src/router.ts:5` 写的是不带参数的 `createWebHistory()` ⇒ 服务端三处同源（`UI_USER_PREFIX` ↔ vite `base` ↔ compose 挂载点）全对时**地址栏仍写在站点根**（实测跳到 `/login?redirect=/agents`），用户一刷新或把链接发给别人就从用户端掉进开发面板的 index——"页面正常、地址错"，这一族不会自己叫 | 新判据 `test_mimo_router_history_carries_the_vite_base`：正腿钉 history 参数必须是 `import.meta.env.BASE_URL`，CONTROL 腿钉 vite base 确为子路径（base 若是 `/` 则正腿无对照物）；变异自证 M1（摘掉参数）⇒ 1 failed、M2（base 改 `/`）⇒ 1 failed，两条都红过 ⇒ 不是空门；修复后浏览器实测 `href=/mimo/login?redirect=/agents`、退出登录回 `/mimo/login` | 无（AF 自决，已交付） |
| 21 | **`/api/health` 的 `readonly` 那格与实例真实写闸分叉**（现场同一台实例同时读到：health `"readonly": true` ↔ 日志"取得单写者锁" ↔ 写闸探针 `POST /api/pending/list` **200**；`af_service.py:246` 那枚是硬编码身份声明，与 `af_cli.py:1420` 的租约结果没有任何连接，而写面由后者决定） | 裁定落地后该档要有可判红的测试；现场这组三读数已当作 CONTROL 收下 | **AF 半边已裁已落**（裁定 `20261008-AF两件与MA四回执-裁定.md` §一：**B** 新键承载真值 + Q2 是；驳回 A 的理由逐字「已存在的读方按"AF 永远 readonly=true"写判读，会在可写部署上读到 `false` 而误报警」）：`af_service.py:217 def write_gate(store, *, readonly)`，`:210` 起注明三档 `open`/`blocked`/`no_lease` 与取值口径同 `linkage.state` 一族，`:293-295` 健康面新增 `write_gate` 键、旧 `readonly` 原样留作身份声明并注释指向新键，`:58` 把键名登记进键表。**可判红的腿当场跑过 `1 passed`（RC=0）**：`tests/unit/test_serve_lease_single_writer.py:360 test_write_gate_is_driven_by_the_flag_not_a_constant`。**没收的一格不归 AF**：Q2 里"命名统一进契约表"这半边裁定原话是「契约表由 **DCD/DB 侧**同步」——现读 `grep -n "write_gate" E:\NAS\homesdk\doc\ADM联动主题注册表与消息契约.md` ⇒ **0 命中**，与第 7 行 `READONLY_DEGRADED:` 那格同形状（AF 仓侧已落、对外登记在对方）。记账见执行记录 §二之七十七 §四 |

### 5.4 原本列在这里的四件：**已全部裁定**（2026-10-04 同日，勿重投）

| 件 | 裁定 | 出处 |
|---|------|------|
| af_persist 追加写 | **A：判定"对 af_persist 不适用"**（本版 §5.3 第 3 行已收口） | `20261004-AF四件与DB一件与MA五件-裁定.md` §一.4 |
| 单写者租约只装 HTTP 面 | **A：只把真机写纳入，只 check 不 acquire** + MCP 前缀 `READONLY_DEGRADED:` → 本版 §5.3 第 7 行 | 同上 §一.1 |
| 有界缓存 TTL 硬上限 | **B：注册表式门禁** → 本版 §5.3 第 8 行 | 同上 §一.3 |
| `trace_id` / `node_id` 出向载荷 | **C：`trace_id` 明确为事件级写进口径；`node_id` 从契约行删**（DB 现在从不读它） | 同上 §一.2 |

> ✅ **DCD 自我更正（针对 AF 的定向请求，2026-10-04）**
> AF 提的 `inbox/20261004-AF-v2.6第3件前置与5.4自相矛盾-回执与定向请求.md` 指出：本文件 §5.3 给第 3 件写"前置=无"，§5.4 又把同一件列进"待 DCD 裁"。**矛盾成立**。
> 但 AF 建议的 **A 档已过期**：第 9 件（af_persist 追加写）**已于同日 13:33** 裁定为 **A＝不适用**（AF 的回执写于 13:41，未读到该裁定）。故正确档位是 AF 自己列的 **C 档**——
> **该格从 v2.6 任务表删掉并永久收口**（本版 §5.3 第 3 行已按此处理），**且 §5.4 四行同时全部过期**（同一份裁定已覆盖四件），一并改为上表。
> 教训已记入 DCD 判例库：**"已裁未读"与"已裁未记"形状相同**——开发者拿到的过期口径会把已裁项重新拖回待议（本次差点按 A 档再等一轮）。

### 5.5 别再重投（已裁）

- homesdk 四问（time 归属 / IANA 口径 / wheel 两步 / AgentOps 授权）：全部已裁（`20261001-AF-homesdk接入四问-裁定.md`）；
- **homesdk.time 已随 0.3.1 投递 NAS**（sha `b4b5d6bb...`），可直接接入；
- 记账缺口已由 DCD 补（commit `5e4ba33`）。

### 5.6 一条提醒（**已更正**，2026-10-04）

原写"`e45a4a5` 及此后 20+ 枚 commit **未推 origin/main**——授权已到期"。**该句在 AF 仓不成立**（AF 已回过一次，本次 DCD 补最新现读）：

- `git rev-list --count origin/main..HEAD` 现读 **0**（上游 `origin/main` 已跟踪，HEAD `8689b39`，分支 `master`）；
- AF 侧 CI 连续绿按 run 计 30 条（run 34–64，AF 回执提供）。

> 若该行原指 **NAS 侧另一份工作副本**，那份的推送属 **SP 动作**，AF 无从代劳——**由 DCD 向 SP 提**，不再挂在 AF 的 §5.6 下。

---

## 六、下一阶段：更紧密联动（DCD 2026-10-06）

> 依据：`关键决策部/decisions/20261006-ADM下一阶段联动路线图-裁定.md`；契约 v2.0 见 `homesdk/doc/ADM联动主题注册表与消息契约.md` §七。
> 核心：三组联动端到端跑通 + 失败统一降级/错误码（`ADM_ERR_*`）。

| # | 任务 | 验收 | 前置 |
|---|------|------|------|
| 1 | 合并窗：镜像重烤 + 窗后四项验收 | `/api/health` 200、`mosquitto_sub` 抓 `af/automation/fired`、`adm/autoforge/status` retained、MQTT 桥起来 | 合并窗 |
| 2 | `adm/autoforge/status` 发 **JSON**（契约 v2.0 §7.1，不再发字面量 `online`） | status = `{state,ts,degraded,reasons,version}` | 1 |
| 3 | MQTT 断连/凭据缺失 → status `degraded` + `ADM_ERR_BROKER_UNREACHABLE`（降级三档 §7.3） | 断 broker → status 转 degraded + reasons 带码 | 1 |
| 4 | 订阅 `ma/insights` 端到端（AF↔MA 硬读数） | MA 发 insights（带 `insight_id`）→ AF 落 `insight_proposals/pending/*.json` → 人批 → `af_draft` 造图 | 1 |
| 5 | MCP 面统一 `ADM_ERR_*` + `channel_error`（DB↔AF 硬读数） | DB 调 draft→dry_run→save 全链；AF 不可达 → DB 收 `channel_error` + 停止标已答 | 无 |
| 6 | 跑 `verify_adm_linkage`（homesdk `scripts/`）三组全绿 | 探针 rc=0（缺一组即红） | 1-5 |

**交付状态（2026-10-07，执行记录 §二之五十八）**：

- **第 2 项 ✅**：`status` 载荷出自 `homesdk.adm.status.encode_status`，AF 不再手写 schema（规格 §三.1）；
  测试按合同判定（`decode_status` + `state` + `retain is True`），不比字面量——这一格定性由裁定
  `20261007…-裁定.md` §六 Q2 落笔为**预期变更**，homesdk 规格 §三.4/§四 已由 DCD 当场更正。
- **第 3 项 ✅（AF 半边）**：发布失败即 `mark_degraded(ADM_ERR_BROKER_UNREACHABLE)` + retained status 转
  `degraded`，传输恢复即复位并重发同一份 caps。7 条变异腿逐条真红、逐字节还原。**这一格 DCD 已回**（裁定
  `20261007-DB凭据与AF降级面-裁定.md` §二：**甲A** = 0.3.3 给 `advertise` 加 `degraded: bool = False, reasons: Sequence[str] = ()`；
  **甲B（公开 `status_topic`）明写"不必做"**；AF 现状"手拼主题 + `encode_status` 自编码"裁为**过渡合法**，0.3.3 落地后切甲A）
  ⇒ 旧措辞"已提 DCD 请 0.3.3 给公开出口"作废。AF 不提前切，现读为凭：homesdk 侧 `pyproject.toml:7` 仍
  `version = "0.3.2"`、`src/homesdk/presence.py:83-90` 的 `advertise` 无 `degraded=`/`reasons=`、`dist/` 最新一份是
  `homesdk-0.3.2-py3-none-any.whl` ⇒ 0.3.3 未发版。另更正一句不准的话："`_topic` 是私有的"字面不成立——仓内
  `grep -rn "_presence\._topic\|presence\._topic" src scripts tests --include=*.py` **0 命中（rc=1）**，AF 是在
  `af_mqtt_bridge.py:98` 自己 `STATUS_TOPIC = f"adm/{PRESENCE_NAME}/status"` 拼的（读数与腿见执行记录 §二之七十八）。
- **第 5 项 ✅（AF 半边）**：MCP/HTTP 的 dict 形拒绝全部带顶层 `ADM_ERR_*`，收件面 `_reject` 三处同码。
  **未自决的两格**：异常路径仍是散文（改形状等于改 DB 的解析口径）、六个码里没有"owner 策略性暂停"档
  （现落 `AUTH_REQUIRED`，会引导对端往"重登"方向重试）⇒ 同批 DCD 申请。
- **第 1/4/6 项 ⏳ 窗内验收**：需要合并窗（镜像重烤 + 真 broker + MA 真发洞察）。仓侧已就位：
  vendored wheel 已换 0.3.2 并按 DCD 登记的权威 sha256 `19bc83a6…fb5505` **钉字节**（不只是钉版本号），
  `ci.yml`×3 + 两份 Dockerfile + `pyproject` 四处引用同步，全量 `gates.sh` RC=0、`pytest` 3312 passed。
- 前置依赖第 1 项的两格（`fired|failed` 事件载荷要不要带 `code`）属契约变更，未擅改。

**本仓失败语义**：写面（未授权写 / MCP default-deny）fail-closed + 码；读面（洞察失败、对端离线）degrade-flag + 码；纯提示 fail-open。**禁止静默丢弃。**

### 契约对齐规范 v2.0（逐字版 · DCD 20261006）

> 唯一真源 = `E:\NAS\homesdk\doc\ADM联动主题注册表与消息契约.md`。本节是其**逐字快照**，供本仓执行，不再回查其它仓；两者冲突以契约表为准并提 DCD 复议。

**A. `adm/*/status` 统一 JSON**（取代字面量 `online`/`offline`）：

```json
{"state":"online|offline|degraded","ts":1760000000,"degraded":false,"reasons":[],"version":"<计划号>"}
```

- `reasons` 非空 ⇒ `degraded=true`，元素 = `ADM_ERR_*`；`version` = 计划号（AF **2.6** / MA 1.4 / DB 2.7）；
- 消费端**兼容旧字面量**：非 JSON 的 `online`/`offline` → 按 `{"state":"online|offline"}` 解析，**不得丢弃**。

**B. 统一错误码**：

| 码 | 含义 |
|---|---|
| `ADM_ERR_BROKER_UNREACHABLE` | MQTT broker 连不上 |
| `ADM_ERR_PEER_OFFLINE` | 对端 presence 不在线 |
| `ADM_ERR_PAYLOAD_INVALID` | 载荷 schema/校验失败 |
| `ADM_ERR_AUTH_REQUIRED` | 缺令牌 / 过期 / 越权 |
| `ADM_ERR_UPSTREAM_TIMEOUT` | 调对端超时 |
| `ADM_ERR_INTERNAL` | 未分类兜底 |

落点：status `reasons[]` ／ MCP·HTTP 响应 `{ok:false, code, message}` ／ `inbox_events` 审计。**联动失败必须带码，禁止静默丢弃。**

**C. 降级三档**：fail-closed（写面/不可逆：拒+码+审计）｜degrade-flag（读面/可重试：继续+`degraded`+码）｜fail-open（纯提示：放行+日志）。

**D. 事件载荷（逐字）**：
- `ma/insights` `{trace_id, ts, insight_id, kind, persons[], room?, summary, evidence[], snapshot_url?, conf?, intent?}`（`conf?` 可选封顶 0.59；`intent?` 可选；**AF 去重/回灌用 `insight_id`，不用 `trace_id`**）
- `ma/presence` `{trace_id, ts, members:[{name, member_id, room, via, confidence, last_seen, trigger}], total}`（retained；member 子键**不含 `via_raw`**）
- `ma/device-health` `{trace_id, ts, device_id, status, entity_id, from, to, stable_id}`（**`stable_id` 必须非空**；迁移类带 `from`→`to`）
- `af/automation/fired` `{trace_id, ts, automation_id, ref}`（**不 retained**，AF 发布）
- `af/automation/failed` `{trace_id, ts, automation_id, ref, error}`（**不 retained**，AF 发布）

**E. 收件箱 schema（对齐后权威版，DB 码必须按此）**：
- `butler/inbox/speak` `{trace_id, ts, text, role?, priority?, expires_at?}`，text ≤500，trace_id 必填
- `butler/inbox/notify` `{trace_id, ts, title, body, channel?, priority?}`，title ≤80 / body ≤500，trace_id 必填
- `butler/inbox/tv` `{trace_id, ts, content, duration_s?}`，content ≤500，trace_id 必填
- **无 `source` 字段**；按通道读 `text`/`title+body`/`content`；DB fail-closed + `inbox_events` 审计。

**F. MCP 面实名**：AF = `af_draft` + `af_apply(stage∈check|simulate|dry_run|save)`（**无 `verify`/`deploy` 别名**）；ask `GET /api/asks/pending`（read 令牌）+ `POST /api/asks/answer`（write 令牌 + INBOX_KEY，回报 `channel_error`）。

**G. 端到端探针**：`verify_adm_linkage`（homesdk `scripts/`），三组各一条硬读数，缺一 `rc=1`。

---

## 七、以 AF 为核心的联动版本（DCD 2026-10-08）

> 依据：用户定向指令 2026-10-08「以 AF 为核心」+ MA 三路径立项（`20261008-MA三路径架构立项-裁定.md`）。
> 契约登记：`homesdk/doc/ADM联动主题注册表与消息契约.md` §1.5（AF 升编排中枢、AF 订阅 presence/device-health、AF 投 inbox）。
> 定位变化：AF 从"自动化中枢"升为**编排中枢（orchestrator）**，自动化是端到端联动的主驱动；DB 降为"表达中枢"（被 AF 驱动说话/通知/TV）；MA 保持"数据中枢"（被 AF 调三路径取数）。

**核心数据流**：

```
AF ── 触发(cron/事件) → 取数(调 MA 三路径) → 组装(DSL) → 表达(投 inbox) → 上报(fired/failed)
  │                       │                    │            │
  │订阅 ma/insights        route_question      ma_query     inbox_speak/notify/tv
  │订阅 ma/presence ✚      run_analysis_template (IR 节点)  → DB 说话/通知/TV
  │订阅 ma/device-health ✚ export_insight
```

（✚ = 本版新增订阅，原仅 DB 消费）

### 7.1 示例端到端（用户给的例子，做验收锚）

【**早上 7:10 TTS 播报昨晚卧室空调开机时长**】：

1. AF cron 触发（`0 10 7 * * *`）；
2. AF `ma_query` 节点 → 调 MA 三路径 `route_question("昨晚卧室空调开机时长")` → 路径1 → `run_analysis_template("ac_runtime_daily")` → 得"卧室空调昨晚开机 X 小时"；⚠️ 这一句里的 `ma_query`／`inbox_speak` 都是**卡片语言不是 IR kind**——AF 侧落形一律是 `do`+`adapter`+`action`（见 §7.5 与 §7.4 卡 1），另立一族会同时破 `ir.schema.json` 与 `classify_action` 分级；⚠️ 且现读 `run_analysis_template` 返回的不是"结果三块"而是 `template/window/entities/summary_text`（`summary_text` 才是那句"X 小时"的载体），逐键名见 §7.2 卡 2 与 §7.5；
3. AF `inbox_speak` 节点 → 发布 `butler/inbox/speak{text:"昨晚卧室空调开机 X 小时"}`；
4. DB 订阅 → TTS 播报；
5. AF 发布 `af/automation/fired`。**任一步失败 → degraded + `ADM_ERR_*`，不静默。**

### 7.2 任务卡（按依赖序）

| # | 任务 | 验收 | 前置 |
|---|------|------|------|
| 1 | IR 新增 `inbox_speak`/`inbox_notify`/`inbox_tv` 节点：发布 `butler/inbox/*`，载荷按契约 §1.3（`trace_id` 必填、`text≤500`/`body≤500`、无 `source`），缺凭据/`INBOX_KEY` **fail-closed** + 码 | 编译/仿真/NL 渲染三管线过；`mosquitto_sub` 能见；缺凭据拒发 + `ADM_ERR_AUTH_REQUIRED` | 合并窗（§六 第 1 件 MQTT 桥已就位） |
| 2 | 调 MA 三路径 MCP 并绑变量。**2026-10-12 按链主派工单 §一 准备 1 把"等 MA 三路径 MCP MVP"改成具体接口名（原句只到"等 MVP"这一格，看不出到底等什么）**：等并可用的四个具名入口＝`route_question(question, days)`／`list_analysis_templates(category)`／`export_insight(template_id)`／`run_analysis_template(template_id, days, start, end, include_timeline)`；⚠️ 返回三块 `insight`/`queries`/`implementation` 的真身在 **`export_insight`**（且 MCP 包装再套一层 ⇒ AF 读到 `data.insight.insight`／`.queries`／`.implementation`），**`run_analysis_template` 返回的是 `ok`/`template`/`window`/`entities`/`summary_text`，与派工单 §2.3 要求的三块不同形**。落形见 §7.5 | `ma_query` 那一格按 §7.5 的形态取到"昨晚卧室空调开机时长"的结构化值并绑进变量；逐接口按 MA 现读键名解析，⛔ 不按派工单字面的三块硬套 `run_analysis_template` | 三条已就绪（MA 入库面 `e0c3f29` 现读：四条全在 `tool_schema.TOOL_SPECS` 且 `expose` 含 `mcp`、全在 `mcp_scopes.REGISTERED_TOOLS` 的 read 档、caps `tools[]` 由 `TOOL_NAMES` 派生 ⇒ 可见）；**形状那一格已定档＝甲**（2026-10-12 17:5x 链主答复件 `E:\NAS\ADM链主\20261012-ADM-链主答复-MA的2.3归属与派工单改口.md` §一：`run_analysis_template` ⛔ 不带三块，AF 按既有形状读 `parsed["insight"]` 之下 ⇒ **MA 本轮零代码**；"请链主一句话定档"那句原字在 `decisions/20261008-ADM以AF为核心联动版本-裁定.md:123`，席位由 DCD `decisions/20261012-ADM-链主席位设立与DCD01五格-裁定.md` §5.1 追认）。⇒ 卡点从"等裁定"移回 **AF 自己开工**（投递件 `投递\AF-01` §一.3 同口径；本席回执 `投递\回执\AF-01.md` R1 排期 2026-10-13） |
| 3 | 订阅 `ma/presence` + `ma/device-health`（新增消费方），落 insight_proposals 同款**独立持久队列**（重启不丢） | 收到 presence/device-health 事件落盘；自动化可按成员/设备状态触发 | 1 |
| 4 | 降级语义：`ma_query` 失败 → degrade-flag + `ADM_ERR_UPSTREAM_TIMEOUT`；`inbox` 发布失败 → degraded + `ADM_ERR_BROKER_UNREACHABLE`；**不静默** | 断 MA → 自动化降级不崩（跳过 TTS 或回退缓存）；断 broker → status degraded + 码 | 1、2 |
| 5 | 示例端到端跑通（7.1 那条） | cron 触发 → ma_query 取数 → inbox_speak 发布 → DB TTS 播报 → `af/automation/fired`；中途失败各步带码 | 1-4 |

### 7.3 本版不做的（边界）

- 不做 AF 直连 TTS/电视（表达归 DB，AF 只投 inbox——收件箱是 DB 的，AF 不越权）。
- 不做 AF 内嵌"家庭数据计算"（取数归 MA，AF 只消费三路径结果）。
- 三路径 MCP MVP 由 MA 侧主导（AF 是消费方，不是建设方）。

### 7.4 AF 侧进度（2026-10-09 现读，AF 自记，不改上面 DCD 的原表）

| 卡 | AF 仓内半边 | 没收的半边 |
|---|---|---|
| 1 | **已落**。落形是 `do` + `adapter: inbox` + `action: inbox.speak\|notify\|tv`，不是新增三个节点 kind（AF 的 IR 里出向动作只有这一种形态，另立一族会同时破 `ir.schema.json` 与 `classify_action` 分级）。三管线（编译/仿真/NL 渲染）绿；载荷 schema 从 `homesdk.presence` 函数签名现读派生，长度上限与 `ts` 口径由库侧把（2026-10-10 另加**编译期预拒**：`INBOX_PARAM_TOO_LONG`，上限从库侧 `INBOX_MAX_*` 现读、AF 不写数字）；缺桥 ⇒ `ADM_ERR_AUTH_REQUIRED`、载荷坏 ⇒ `ADM_ERR_PAYLOAD_INVALID`、`rc≠0`/异常 ⇒ `ADM_ERR_BROKER_UNREACHABLE` + retained status degraded，全部 fail-closed | `mosquitto_sub` 能见 = NAS 合并窗；§1.3 护栏 3（按 source 限速）未实现（执行人与 N 值已交 DCD `20261010-AF-契约护栏3每来源限流的N与执行人-决策申请`） |
| 2 | **AF 半边未落；但阻塞面比链主派工单 §一 写的窄**。派工单 §2.1 的承重前提「`route_question`/`run_analysis_template`/`export_insight` 全仓 0 命中」经现读**不成立**（MA 仓 HEAD `e0c3f29`，`git status --porcelain -- src doc` 空 ⇒ 读到的是入库面，不是工作树）：`grep -rn "<名>" src/ --include=*.py` 现读 = `route_question` **11**／`run_analysis_template` **10**／`export_insight` **14**／`list_analysis_templates` **5**，且 `git grep -c` 的 HEAD 面逐枚同数；四枚都已进 MCP 注册面（`tool_schema.py:70`／`:652`／`:664`／`:688`）与授权档（`mcp_scopes.py:35`／`:64`／`:83`／`:84`）。AF 侧此刻**确实 0 命中**（`grep -rn "ma_query" src/autoforge --include=*.py` = 0，`ma` 适配器不存在）⇒ 这一格不是"等 MA"，是"AF 没开工"。IR 形态已按派工单 §一 准备 2 定完（见 §7.5，纯设计、⛔ 不落实现），MA 接口就绪时只往表里填空 | 剩**一格，且是形状不是有无**：`export_insight` 返 `insight`/`queries`/`implementation` 三块，`run_analysis_template` 返 `ok`/`template`/`window`/`entities`/`summary_text`——与 §7.2 卡2 验收写的"三块 schema"**不同形**。要么 AF 改解析（§7.5 二已按现读把两套键都列出来），要么 MA 增适配层 ⇒ **谁做归链主／DCD 定，⛔ AF 不为"能跑"临时改 MA 的契约**（派工单 §三 逐字）→ **2026-10-12 17:5x 链主已定档甲**（`E:\NAS\ADM链主\20261012-ADM-链主答复-MA的2.3归属与派工单改口.md` §一＋§三；DCD `20261012-ADM-链主席位设立与DCD01五格-裁定.md` §5.1 追认席位）。甲＝AF 按既有形状读 `parsed["insight"]` 之下、MA 零代码 ⇒ ⛔ 这条不是"AF 替 MA 改契约"，那条红线原样有效。另有 `ADM_ERR_UPSTREAM_TIMEOUT` 的承载体随本卡落（见卡 4 ①） |
| 3 | **已落**。落形是 `LinkageFeed` 文件队列（`{store_root}/linkage_events/{kind}/{13 位毫秒}-{event_id}.json`，presence/device_health 各一套 500 条封顶 + 24h TTL），**没有新增触发族**：两类事件走既有 `trigger.type = event`，事件名 `ma_presence` / `ma_device_health`；**没有新增路由**：入向读数挂在既有 `/api/health` 的 `inbound` 块。收与消费隔一条盘（paho 回调只判形状+原子写，`EventBus` 无锁 ⇒ 常驻 tick 线程 `pump_linkage` 才注入总线），水位线键序时间优先（防一类洪水冻住另一类），超龄条目**推进水位线但不补触发**（"重启不丢"指记录，不指触发）。拒收只按契约写死的那几条（`missing_trace_id`/`members_not_list`/`missing_stable_id`），没接队列 ⇒ `no_linkage_sink_wired` + `ADM_ERR_INTERNAL`，AF 不自添必填。与洞察队列的封顶口径**相反**（那边满了拒收、这边裁最旧），理由是对端代价不同 | `mosquitto_sub` 见到 MA 真发的那一条 = NAS 合并窗；**"按成员/按设备分支"这半句今天写不出来**（载荷在 `_trigger_repr` 处丢，解析器对 `context.` 只做平表查找），归哪一档已交 DCD：`20261009-AF-入向事件载荷怎么进DSL`；`serve` 侧没有 ticker ⇒ 只有 `forge watch` 会泵（常驻通道另单 `20261009-AF-用户视角到真机的常驻通道`） |
| 4 | **半边已落，另半边按现读数归位**：`inbox` 发布失败 ⇒ degraded + `ADM_ERR_BROKER_UNREACHABLE`（卡1 顺带交付，腿当场跑过 `5 passed`：`test_af_mqtt_bridge.py::test_event_publish_failure_flips_retained_status_to_degraded_with_a_code`／`::test_degraded_status_clears_when_the_transport_returns`／`::test_degraded_announcement_that_also_fails_does_not_reach_the_execution_chain` + `test_inbox_adapter.py::test_degraded_status_carries_the_code_when_inbox_is_refused`／`::test_recovered_delivery_clears_degraded_and_readvertises_caps`）。"三落点"逐格现读（HEAD）：① status `reasons[]` ⇒ 已带码（`af_mqtt_bridge.py:627/:633`）；② MCP 响应体 ⇒ 已带码（`git show HEAD:src/autoforge/af_mcp.py \| grep -c ADM_ERR` = **14**，戊A 那批）；③ **HTTP 响应体 ⇒ 0 命中**（`git show HEAD:src/autoforge/af_api.py \| grep -c ADM_ERR` = **0**，工作树同读数） | 两格，且都不是"AF 少写几行"：① `ma_query` 失败 ⇒ `ADM_ERR_UPSTREAM_TIMEOUT` 这一条**没有承载体**——`grep -rn "ma_query" src/autoforge --include=*.py` **0 命中**，节点本身属卡2（前置=MA 三路径 MCP MVP；**2026-10-12 现读订正**：MA 三枚入口在库面已就绪，见本表卡 2 行——这一格的"没有承载体"仍是现读事实，但原因从"等 MA"改成"AF 未开工"。承载体怎么落已在 §7.5 四 定形：`ma` 适配器抛 `AdapterError` ⇒ `on_error` 边 ⇒ `ADM_ERR_UPSTREAM_TIMEOUT`，随卡 2 实现一并交付，⛔ 不在 params 里配超时）；② HTTP 那一落点要写进 `af_api.py`，而该文件此刻躺着并发会话未提交的改动 ⇒ 同一条归属纪律，不抢同一文件 |
| 5 | **未落**（依赖 2、3、4） | 端到端要 MA + DB + NAS 三方都在 |

### 7.5 `ma_query` 的 IR 形态（设计，⛔ 不落实现）——链主派工单 §一 准备 2（2026-10-12）

先说被推翻的承重前提：**`ma_query` 不是一种节点 kind。** AF 的 kind 词表是封闭 8 枚（`af_ir/models.py:78` `NODE_KINDS = ("on","if","do","ask","wait","set","pass","group")`，schema 侧同源枚举 `af_ir/schema/ir.schema.json:61`），未知 kind 在编译期直接被 schema 拒（`models.py:762-790` 抛 `IRValidationError`→400）；卡 1 的 `inbox_speak` 已经栽过同一格并留下裁定（`af_adapters/inbox.py:3-6`：「AF 的 IR 里出向动作只有 `do`+`adapter`+`action` 这一种形态……另立一族会同时破 `ir.schema.json` 和 `classify_action` 的风险分级」）。⇒ 本卡落形＝**新适配器 `ma` + 四个 action**，kind 仍是 `do`。MA 接口一到只是往表里填空，不用再等一轮沟通。

**一、节点骨架（逐字段来自现读，⛔ 不发明字段名）**

```json
{ "kind": "do", "adapter": "ma", "action": "route_question",
  "params": { "question": "昨晚卧室空调开机时长", "days": 7 },
  "result_var": "ma_route" }
```

- 必填两格＝`adapter`＋`action`（`ir.schema.json:146-147` 的 `then.required`）；入参统一叫 **`params`**（`models.py:451`），与 `http`/`inbox` 同形。
- `result_var` 是既有字段（`models.py:456`），写入口径固定：`af_executor.py:744-749` 把适配器结果包成 `{"success":…, "data":…, "error":…}` 存进 `instance.ctx.vars[节点 result_var]`——**AF 不为本卡新增第二种结果形状**，`ma` 适配器必须返回同一个 `CallResult`（`af_adapters/base.py` 的 dataclass 契约）。
- ⛔ `params` 里**不许**出现 `timeout`/`retry`/`fallback` 一类：`base.py:37-49` 的 `POLICY_PARAMS` 会被静态扫描判 `ADAPTER_POLICY_PARAM`（`af_scanner.py:478-481`）。业务超时按纯执行层契约走 IR 层 `wait`+`on_timeout`（`base.py:1-14`）；**套接超时留在适配器内部**（那是传输层，不保留会单进程挂死）。

**二、四个 action 的入参与返回（MA 侧现读真值，`E:\NAS\memory-agent` HEAD `e0c3f29`，`git status --porcelain -- src doc` 空 ⇒ 都在入库面）**

| action | MA 定义处 | 入参 | AF 该读的返回键（真值） |
|---|---|---|---|
| `route_question` | spec 驱动：`tool_schema.py:70`（`service="insights"`, `method="plan_question"`）→ 实现 `insights/api.py:871` | `question: str`、`days: int = 7` | 计划/路由体（**全仓无 `def route_question`**，所以按 `def` 找会得到假 0 命中） |
| `list_analysis_templates` | `mcp_server.py:2659` | `category: str = ""` | 模板清单 |
| `export_insight` | `mcp_server.py:2686` → `templates.py:378` | `template_id: str` | **三块真身在这里**：`templates.py:384-413` 返 `insight`/`queries`/`implementation`/`metadata`，但 MCP 包装 `mcp_server.py:2693` 写的是 `{"ok": True, "insight": data}` ⇒ AF 实际路径是 `data.insight.insight` / `data.insight.queries` / `data.insight.implementation` |
| `run_analysis_template` | `mcp_server.py:2705` → `templates.py:635` | `template_id`、`days=0`、`start=""`、`end=""`、`include_timeline=True` | `templates.py:693-705`：`ok`/`template`/`window`/`entities`/`summary_text`——⚠️ **与派工单 §2.3 要求的"三块"不同形**；"开机 X 小时"那句人读串在 `summary_text`，结构化值在 `entities[]` |

**三、出参绑定规则（沿 AF 既有机制，⛔ 不引入 `{{ }}` 占位符插值语法）**

1. 引用命名空间三档：`entity.*`／`vars.*`／`context.*`，无前缀直接拒（`af_state.py:144-150`，解析 `:157-182`）。本卡产物一律走 `vars.<result_var>.*`。
2. 子字段下钻用**点号路径**，先例＝`af_expect.py:69-82 resolve_var`（文档例 `:25`：`{"var":"turn_on_result.success","op":"eq","value":true}`）。
3. ⚠️ **列表元素在 DSL 里取不到**：`resolve_var` 逐段只对 `Mapping` 生效（`af_expect.py:78-81`），`queries[]`／`entities[]` 的下标段会返回 `MISSING`。⇒ 绑定规则定为：**AF 只绑 Mapping 路径上的标量**（如 `vars.ma_run.data.summary_text`、`vars.ma_export.data.insight.insight.confidence`）；要把 `entities[]` 里某个设备的数值挑出来，v1 的做法是**在适配器内部按 `entity_id` 选一次**（选法由 `params.entity_id` 给，仍是常量），把选中的标量放进 `data` 顶层再绑——⛔ 不是给 DSL 加下标语法（那是表达式层扩容，要另走 DCD）。
4. ⚠️ **`params` 不做运行期插值**：执行器原样透传 `dict(node.params)`（`af_executor.py:740`）。所以"把上一步的结果拼进问题串"这一手 v1 **做不到**，示例端到端（§7.1）里那句 `"昨晚卧室空调开机时长"` 必须是**常量串**。动态问题串要动的是 params 插值面＝跨层改动，已登记为待裁缺口（见本节末）。
5. **口径钉住（2026-10-12 现读订正，原写"该语法全仓 0 命中"是错的）**：`grep -rn "{{" src/autoforge --include=*.py` ⇒ **7 命中**，逐枚看全部是 **Python 侧的花括号转义**，没有一处作用于 IR `params`：`af_closedloop/deepfix.py:19-23`（5 处，`DEEP_FIX_SYSTEM` 提示词模板在 `:184` 走 `.format(max_ops=...)`，所以给 LLM 看的 JSON 例子必须逃成 `{{`/`}}`）、`af_nl_build.py:110`（rf-string 里的正则量词 `\d{{1,2}}`）、`af_service.py:546`（f-string 里逃出的字面 dict `{'automations': [...]}`）。IR 真源侧 `grep -c "{{" src/autoforge/af_ir/schema/ir.schema.json` ⇒ **0**。⇒ "不引入 `{{ }}`"这句谈的是**IR 语法面**，与上面那 7 处不是同一射程。

**四、失败与降级语义（派工单说的 "degrade-flag" 在 AF 里**不是 IR 字段**：`degrade` 在 `af_ir/models.py`／`af_ir/schema/ir.schema.json`／`af_executor.py` 三面现读 **0 命中**，`continue_on_failure` 在 `src tests scripts` 现读 **0 命中**；而 `degrade`/`degraded` 作为**状态字与审计 kind**在 `src/autoforge` 的 `.py` 面有 **56 处**（如 `af_conflict.py:89 KIND_DEGRADED = "degraded"`、`af_adapters/inbox.py:17` retained status 转 degraded）⇒ 载体是 status/kind，不是节点上的旗子）**

- 失败出口走**边**：`EDGE_KINDS` 含 `on_error`/`on_timeout`/`default`（`models.py:88`），优先级 中断>失败>超时>正常>兜底（`:92-100`）；`do` 失败且没有 `on_error`/`default` 边 ⇒ 实例判 failed（`af_executor.py:767-770`），有边则 `_soft_fail`（`:773-791`）。⚠️ 编译期建议项 `DO_WITHOUT_ON_ERROR`（`af_scanner.py:465-470`）会把"调 MA 却没接失败边"报出来——这不是可选项，卡 4 的"不静默"就落在这条边上。
- `ADM_ERR_UPSTREAM_TIMEOUT` 的**承载体**（此前 0 命中就是因为没有 `ma` 适配器）：`ma` 适配器在套接/整体调用超限时抛 `AdapterError`，错误码取 `adm.errors` 的具名码（与 `ADM_ERR_BROKER_UNREACHABLE` 在 `af_mqtt_bridge.py` 的用法同形），再由 `on_error` 边接一枚 `do inbox inbox.notify` 把"这次取数没成"投给 DB，**外加** retained status degraded。⛔ 不在 `params` 里配重试。
- **静默空值那一族要显式挡（2026-10-12 投递件 `AF-01` §一.2 新增的判据格，本席现读认同）**：按顶层读三块**不会崩、只会读空**——MA 现树是 `{"ok": True, "insight": data}`（`mcp_server.py:2693`），`ok` 仍是 `True`，而顶层 `parsed["queries"]`／`parsed["implementation"]` 根本不存在 ⇒ 属"不崩但拿空值"那族难查故障。⇒ `ma` 适配器落码批必须带两条腿：① 合成载荷腿——喂 `{"ok": True}`（缺 `insight`）与 `{"ok": True, "insight": {}}`（三块全空）两种形状都要抛 `AdapterError`，码取 homesdk 目录里**已有**的 `ADM_ERR_PAYLOAD_INVALID`（现读：`homesdk/adm/errors.py` 目录 6 枚含它，`af_mcp.py:158` 已在用；⛔ 不新造码、⛔ 不动 `.gates-*` 账本、不抬可观测性上限），⛔ 不许返回空 dict 让 `result_var` 静默绑成空值；② 反空集腿——把"顶层读三块"的错误形状种回副本树，必须被 ① 扫出（本仓工序：新判红先问「什么都不改会不会绿」）。⚠️ 上游平铺**不是** AF 要防的那格：MA 侧 `tests/contract/test_ma_db_contract.py:493-506` 的 AST 腿（`assert not flat`，docstring `:494` 逐字「AF 按顶层读三块会全部读空」）已反向锁住，MA 若平铺它自己先红；AF 这两条防的是**自己读空之后冒充成功**。
- 风险分级：`ma` 的四条 action 全是只读取数 ⇒ 必须进 `base.py:64` 的 `_L0_DOMAINS`（`inbox` 那格注释已经把理由写死：留在未知档会被保守判 L2 ⇒ 每条动作都要配 canary，`do` 连编译都过不去）。域判定入口 `classify_action`（`base.py:165`）。

**五、真要实现时要动的登记面（现读"已存在、会因此变红"的清单，⛔ 不是想象）**

1. 新模块 `src/autoforge/af_adapters/ma.py` ＋ `af_adapters/__init__.py:17-19` 的 import 与 `__all__`；注册点唯一收口在 `af_runtime.py:290-296`（四面同一份能力，卡 1 已论证过为什么必须在这）。
2. `base.py:64` `_L0_DOMAINS` +1（否则 L2 保守判拦住编译）。
3. 出向通道同源判据：`ma` 是 **AF 作为 MCP 客户端**，与既有 `af_mcp.py`（AF 作为 server，`TOOLS` `:471`）方向相反——`check_tool_names.py` 那族只数 `af_*` 工具，不受影响；但**新增任何 `af_*` MCP 工具**才会撞那张唯一真源名单。
4. 留痕具名码必须进 `docs/可观测性清单.md` §三 并在册（`scripts/check_observability.py` 的 C 棘轮只减不增 ⇒ 补码会降现读，不会顶上限）。
5. 仿真/契约面：新适配器要在 mock 面给出可仿形状（`af_adapters/mock.py`），否则 F14 保真与 `/api/live/run` 那一面对 `adapter: ma` 读不到结果。
6. 端到端验收锚：把库侧探针 `E:\NAS\homesdk\scripts\verify_adm_linkage.py` 接进读数（§六 行 6 那一格现读：AF 仓内 `grep -rln verify_adm_linkage tests scripts` **0 命中** ⇒ 开窗第一件是接线，不是宣布三组全绿）。

**六、前置复测：派工单 §〇 那句"AF 的卡2 全卡在 MA"按现读只成立一半**（逐条可重放）

- `route_question`／`list_analysis_templates`／`export_insight`＝**已就绪**（MA 入库面注册＋read 档＋caps `tools[]` 可见，证据见上表）。
- `run_analysis_template`＝**半就绪**：工具/scope/caps/入库面齐备，但返回键与派工单 §2.3 要求的三块不同形 ⇒ 要么 AF 按真值改解析（AF 半边，AF 能自决），要么 MA 增一层适配（MA 半边，⛔ AF 不替 MA 改契约）。**这一格要链主或 DCD 定谁做**，AF 不自决跨仓契约。**2026-10-12 17:5x 已定档甲**（链主答复件 §一／§三，DCD `20261012-ADM-链主席位设立与DCD01五格-裁定.md` §5.1 追认席位；本席 18:5x 在 `E:\NAS\memory-agent` HEAD `e0c3f29` 独立复现其五条主张，逐字吻合，读数见回执 `投递\回执\AF-01.md` 附表）。2026-10-12 补：MA 席在 `decisions/20261008-ADM以AF为核心联动版本-裁定.md` §六 独立现读后**同样建议甲**（理由：既有契约腿已把形状钉住，乙会让同一能力有两个出口两种形状）⇒ 两席同倾向，只差落档。
- ⚠️ 反证登记：派工单 §一 2.1 写「`route_question`/`run_analysis_template`/`export_insight` **全仓 0 命中**」，与 MA 仓现读不符——`grep -rn` 在 `src` 面四条各 11／10／14／5 命中，注册行在 `tool_schema.py:70/652/664/688`，且 `git show HEAD:` 面同样命中 ⇒ 事实在**入库面**不只在工作树。**两档口径都列（2026-10-12 AF 侧在 MA 仓 HEAD `e0c3f29` 独立复跑，与 MA 席裁定 §六 逐值相同）**：`--include=*.py` ⇒ 11／10／14／5；`src/` 全文件仅排除 `.pyc`（派工单 §二 判据 1 的字面口径）⇒ **13／20／17／10**；差额在 `skills_bundle/insight/SKILL.md` 与 `static/js/pages/{user_manual,mcp}.js` 三处文案/前端面引用，⛔ 不是实现缺失。最可能的取数偏差：按 `def route_question` 这种"函数定义式"模式找（`route_question` 是 spec 驱动，确实没有 `def`），或读的是旧 caps retained 载荷／部署镜像面。⛔ AF 不因此改 MA 仓，只把这条按"派工单前提需复测"回给对方。
- AF 侧自己的 `ma_query` 计数仍是 **0 命中**（代码面：`grep -rn ma_query src tests scripts` ⇒ 0；只存在于 4 份 docs 面文字里），这一格没变。

**七、本节不做的（不自决清单）**：不落实现（本卡只到形态）；不给 DSL 加列表下标语法；不做 `params` 插值；不改 MA/DB 仓；不开合并窗、不碰 NAS 容器；不为"能跑"临时改 MA 的返回契约。


---

—— 关键决策部 · DCD