# ADM 联动执行记录 · AF（AutoForge）

> 对应计划：`docs/ADM联动执行计划-AF.md`（DCD 出品，v2.5 联动落地版）
> 记录人：AutoForge 开发
> 核实基准：代码侧最新 commit = **`da09d43`**（`da09d43` 是**窗后四项验收工具**那一笔：`scripts/verify_adm_window.py` 把裁定的四件做成三态逐项判定（有 FAIL ⇒ 1、有缺项无 FAIL ⇒ 2 并打印"EXEMPT ≠ VERIFIED"、全 PASS ⇒ 0），判据取自契约表 §1.2/§1.3（四键 + `ts` 偏差 + 事件不许 retained + `msg.retain`），连接与凭据走机制层 `homesdk.mqtt` ⇒ 脚本不读任何环境口令；`+268/0` 与测试 `+326/0`，**产品代码一个字节未动**；同批更正本文档 5 处"本机无 paho-mqtt"的理由（实测 `homesdk.mqtt.paho_available()=True`，真缺的是那台 broker），读数见 §二之二十五）；上一笔 `b50bb22` 是**入向订阅门禁**那一笔：`scripts/check_mqtt_subscriptions.py` 三条判据（订阅口只在 `af_mqtt_bridge` / 动态主题要在同一函数体内先过 `FORBIDDEN_SUBSCRIPTIONS` / 收件箱族写死就红）+ `gates.sh` 新节与两条红分支 + 20 条反例，**产品代码一个字节未动**；真实 src 实测计数"订阅点 2 处/1 文件、`INSIGHTS_TOPIC` 1 处、带守卫的动态入口 1 处、豁免 0"，四档变异 `RC=0/1/1/1/2`（M-1 另走完整 `gates.sh` 取到 `结论：入向订阅门禁红（exit=1）`），读数见 §二之二十四）；上一笔 `8b629b8` 是**出向 MQTT 写者门禁**那一笔：`scripts/check_mqtt_writers.py` 三条判据（写者唯一 / 事件只许 `observe_terminal()` 产生 / 载荷必经 `_envelope()`，位置式与 `payload=` 关键字式都认）+ `gates.sh` 新节与两条红分支（`exit 1` 红、`exit 2` 读不到锚点）+ 18 条反例，**产品代码一个字节未动**；真实 src 的实测计数是"写者 2 处/1 个文件、生产者 2 处/1 个文件、`_publish` 载荷 2 处且 2 处来自 `_envelope()`、豁免 0"，读数与两个自踩的坑见 §二之二十三）；上一笔 `159ba00` 是**只加测试**：把 `observe_terminal()` 真实发出的键集合钉死，产品码 `0` 变动；它顺带让一个从未登记的差额可见——观察者路径比契约表 §1.2 多发 `node_id`（`instance_id` 那份额是裁定 20261002 ②A 已登记过的），读数与四档变异见 §二之二十二）；上一笔 `5ff1ea6` 是**出向事件 `error` 装真原因**那一笔：`observe_terminal()` 不再把状态名当 `error` 发，改读 `ctx.context["fail_reason"]`、缺原因发诚实占位句并按契约表 §1.3 的 500 封顶；`+23/−1` 产品码 + `+58/−0` 三条新判据，四档变异 `0/2/1/1` 各自钉一次；同批对完的 `trace_id` 那一半属契约语义 ⇒ **代码未动**、投 DCD §五 第 12 件，读数见 §二之二十二）；上一笔 `d7d1fff` 是**状态源 fail-closed 静态门禁**：`scripts/check_snapshot_policy.py` + `gates.sh` 新节与两条红分支 + 16 条反例，**产品代码一个字节未动**；射程判据取"标注 `-> Snapshot` ∪ 体里造 `Snapshot`"的并集，起因是第一版只认标注被自家三条反例打红，而本仓 `tests/contract` 里的反面样本 `_FailOpenProvider` 正是"没有标注却造 `Snapshot`"那一种；锚点读不到 exit 2、豁免单独计数）；`c7b97b9` 把**工具名单门禁**的"第二份名单"形状从字典键扩到集合/列表/元组（+3 反例，M-6 实测 `RC=1` 两处行号）；`743aadf` 是那一族的**首版**：`scripts/check_tool_names.py` + `gates.sh` 新节 + 22 条反例 + **两处死映射删除**（`af_orchestrator.observe()` 按名调注册表里没有的 `af_live`、`af_runtime_ext.mcp_tools()` 另抄一份含 `af_approve_proposal` 的五人名单），净 `0 3` / `1 14`；`9a83ead`+`4a3e3b1` 是**参数注入门禁**那一组：门禁脚本 + `gates.sh` 新节 + 22 条反例 + 一处真缺陷（`af_mcp._t_health` 从不探它服务的 store，+3/−1）+ 双轨对拍两条**带理由**的现场豁免，且门按 `store`/`readonly` **参数表**收、`clock` 有意不收；`e6d442a`/`0722828`/`9c6deb9` **只加测试**，`fc2bba6` 加的是**一门新门禁** `scripts/check_states_fanout.py` + `gates.sh` 一节 + 它的反例测试，`12cea64`/`dc8ac0d`/`af2ee56` 是动 `src/` 的三笔：时钟口径 + 三条判据（+11/−2）/ 真机闸门在 MCP 面两处 fail-open（4 文件 +196/−24）/ 观察期恢复路径丢 `auto_rollback` 旗子（+21/−8）。§二/二之二…二之二十 各组读数各自当场跑出，非互相引用；跨批次重复的读数（全量 pytest、`gates.sh`）在对应小节里写明当次的解释器与通过/跳过数（铁律 #11）
> 契约真源：`E:\NAS\homesdk\doc\ADM联动主题注册表与消息契约.md`
> 交叉裁定：`20260929-ADM三仓联动七问`、`20261001-AF-homesdk接入四问`、`20260930-AutoForge后续优化三项`、`20261001-DB目标模式与AF三题` §H、**`20261002-homesdk记账与AF-DB-DPP六件-裁定`**（本轮落地依据，§一 末）

---

## 〇、结论先说

| 步骤 | 状态 | 说明 |
|------|------|------|
| 第 0 步 homesdk 0.3.1 接入 | **①② 已交付 / ③④ 不在本回合** | ③ NAS 镜像重烤、④ AgentOps 模板是他仓 main + 生产动作，AF 单方面有代码无权生效 |
| 第 1 步 AF MQTT 桥 | **①②③④ 已交付** | ④ 为"不订阅 `butler/inbox/*`"，实测白名单门禁只有 7 处 topic 且全在契约表内；**CLI 起桥那根线本批补判据**（`proposal_sink` 是只落盘队列、`caps.version` 非空、`tools` 来自 TOOLS 名单、未开启即 `return None`）⇒ 三种改法各钉一次红（§二之十三）；**上游 `_make_runtime` 那条本批也钉上**——只有真机/dry-live 分支起桥、递进去的是 `runtime.clock`（家庭墙钟）与 `store_root`，两次变异各取到 `1 failed`（§二之十三 同族第三条） |
| 第 2 步 presence 发布 | **①② 已交付** | LWT + 优雅下线双路；`kill -9` 分支需真 broker，本机无数。① 的 `caps` 载荷现另有接线判据（`version=PRESENCE_CAPS_VERSION` 由 `test_af_cli_linkage_wiring.py` 钉住，§二之十三） |
| 第 3 步 F9 group 节点 | **①②③④ 已交付** | schema 先行（铁律 #1），原子回滚把手修在 `0dd57b2`、其回归测试在 `34a540c`；验收点按点复测：group 三文件分跑 **`31 passed` RC=0**，④ 的"全回滚、无半部署态"由 `test_af_ir_group_apply.py:91` 钉住（§二之十三） |
| 第 4 步 MCP 工具面 | **①②③ 已交付** | ① 命名口径按裁定 ①A 以 AF 现名为准（计划文档已改口径，正式重发由 DCD 出）；② 加 `channel_error` 判别字段（③A）；③ 契约测试分跑 **`5 passed` + `14 passed` RC=0**（队列侧 14 ≥ 计划写的 13，§二之十三） |
| 裁定 20261002 · AF 侧六件 | **①A ②A(部分) ③A ④A 已落地；StateProvider B 排队；合并窗后验收未做** | ②A 的 `instance_id` 删除时点 = AF v2.6，本轮不删；窗口后验收需**一台真 broker**——本机 `MQTT_HOST` 未配、无 `docker`/`mosquitto`（先前记的"本机无 paho-mqtt"是跑错解释器的结论，实测 `paho_available()=True`，更正见 §二之二十五）。四件已固化成一条命令 `scripts/verify_adm_window.py`（三态结论，缺项退出码 2 而不是 0）|
| 第 5 步 后续优化三项 | **①③④ 已交付；② 只落了半边** | ① 单写者租约降级只读、③ import-linter 分层两档、④ tick 线程自愈，逐条 file:line 见 §一；① 的**接缝判据**（serve 抢不到锁 ⇒ `build_app(readonly=True)`）本批补上，两次变异各取到红（§二之十三）；② af_persist 的「顺序追加」子句**未落**（裁定执行约束第 2 条要求「追加写 + 文件头部元信息」，现实现仍是整文件重写）⇒ 已提 DCD（§五 第 9 件），读数与不自主实现的三条理由见 §二之十二 |
| 第七轮审计 | **finding 属实，已修** | 详见 `docs/audit/审计报告_第七轮_核实与修复.md`；第八轮尚未落 `docs/audit`（本回合实测目录内最新即第七轮，mtime `10-02 11:46`） |
| 门禁肥化防护（计划外补刀） | **已交付，本批又加一门** | `gates.sh` 计数棘轮 + `.gates-tally.txt`（上限 104），三条变异实测能红；AgentOps 模板同步去反引号缺陷。本批新增**状态源扇出门禁** `scripts/check_states_fanout.py`（换 `runtime.states` 必须四个消费方同步；HEAD 绿、两处变异红），并自带反例测试 `test_states_fanout_gate.py` 7 条——第一版实现自己是个假洞（对 HEAD 与对变异同时报绿），那一次形状也钉成用例（§二之十四） |
| 真机路径口径对账（计划外补刀） | **已交付** | 接缝盘点的第二个产物：`live_run`（HTTP `/api/live/run` 与 MCP `af_live_run` 共用）此前用 `build_runtime(graph)` 的**仿真锚点**当墙钟用——同一次真机下发的 `trigger_time`/`audit.at`/canary `at` 与 `at:19:30` 判定全落在 2026-09-14 08:00，而 CLI 的 live/dry-live 分支早已取家庭墙钟。`12cea64` 统一为"锚在真实的现在、仍可推进"的虚拟钟并开 `clock=` 入参；`build_runtime(` 全部 8 个站点逐一点过，其余无 clock 的都在仿真面上（锚点是设计）。三次变异 `3 failed`/`1 failed`/`1 failed`（§二之十五） |
| 真机入口面的闸门对账（计划外补刀） | **已交付两处 / 第三处待裁** | 接缝盘点的第三个产物：同一盘点前两条落在 `af_cli`/`af_service`，这条落在 **HTTP 与 MCP 两个入口面**。实测两处 fail-open——① `af_mcp._t_live` 没递 `store` ⇒ Tier-0 设备保护（`device_acl.json`）与实体健康在 Agent 路径上整个失效，人点被拦的设备 Agent 能写；② events 上限写在端点层 ⇒ MCP 三面（`af_live_run`/`af_simulate`/`af_sessions`）无上限。修法把判据上收到 `af_service`（两条入口共用的咽喉），`MAX_REPLAY_EVENTS` + 三个服务层调用点，`_t_live` 补 `store=store`（`dc8ac0d`）。反真空靠一条**正向对照**（无 ACL 时 MCP 路径真下发）。三次变异 `1 failed, 18 passed`/`2 failed, 17 passed`/`2 failed, 17 passed`（§二之十六）。**同批抓到的第三件（单写者租约只装 HTTP 面）不属 AF 可自决**：它要给 DB 新增一个可见的拒收模式 ⇒ 投裁定（§五 第 10 件），代码未动 |
| 生产态验证证据与 canary 策略位盘点（计划外补刀） | **已交付** | 接缝盘点第六条命中，也是**第三条真缺陷**：`canary: {duration: "15m", auto_rollback: false}` 是"只观察、别自动反向下发"的显式选择，但 `pending_canary` 挂起元信息没存这个旗子 ⇒ 观察期结束的 `resume` 分支直接 `wrapped.rollback()`，绕开 `CanaryGuard.auto_rollback` 那一层判断；无 duration 的立即检查路径却尊重它。方向是"比要求的更自动"，且这条 resume 是挂起观察的**常规**唤醒路、不是崩溃恢复专用。`af2ee56` 把旗子带过接缝（缺键按 True，旧快照行为不变）、恢复侧改用真 `CanaryGuard.check_and_rollback`（回滚与否只留一处实现），不回滚仍记 `failed`+`entity_drift` 并在 reason 写明"未回滚"。同批盘了 `af_watch` 三个喂入点的 status 档对齐（shadow/立即 canary/冲突各在其位），并登记一处同名碰撞（`af_audit.record_conflict` vs `af_watch.record_conflict`，非缺陷）。三次变异各 `1 failed, 25 passed`（§二之十七） |
| 参数递错那一族升成常驻门禁（计划外补刀） | **已交付，且抓到第四处真缺陷** | 前六条同族站点有五条靠手扫；这一批把判据做成 `scripts/check_param_injection.py` + `gates.sh` 新节（CI 的 `quality-gates` 跑的就是 `bash gates.sh` ⇒ 本机与 runner 同口径判红）。按**参数表**收（`store`/`readonly`），`clock` **有意不收**（默认值是仿真锚点、是设计，§二之十五）。首跑抓到 `af_mcp._t_health` 从不探它服务的 store ⇒ Agent 面 `store_ok` 永远 `null` 而 HTTP 面是 `true`（读面证据缺失，铁律 #5）；五处变异各红一次，读数见 §二之十八 |
| 名单手抄那一族扫掉＝§六 挂了四批的最后一条接缝（计划外补刀） | **已交付，盘到两处第二份名单并删除** | 「TOOLS→caps 之外有没有第二次工具名单映射」这条以前**既没清单化也没有能判红的门**。盘点必须走 AST：全文正则扫 `af_*` 得 108 个串/77 个不在注册表，几乎全是模块名（`af_live.py` 本身就是模块）；只取字符串常量且整串 fullmatch 工具名形状 ⇒ 34 个/3 个不在 `TOOLS`（那 3 个是指标标签名，非工具名）。删除前的读数是 40/9，多出的 6 个就是两处真映射：`af_orchestrator.observe()` 按名调 `af_live`（真名 `af_live_run`，`_call_safe` 吞异常 ⇒ 这条路**永不响**）、`af_runtime_ext.mcp_tools()` 另抄五人名单且从未接线，其中 `af_approve_proposal` 与裁定 20261002 §三 ④A「人批后才进可执行队列」正面冲突。两处都是死代码 ⇒ **删除而非接线**（单提交可 revert）。新门 `scripts/check_tool_names.py`：按名调用必须命中 `TOOLS`、`TOOLS` 之外 ≥2 个 `af_*` 键的字典即第二份名单、**读不到注册表锚点 exit 2**（不做假绿）；22 条反例含"不误响"主干。五处变异分档红（`1/1/1/2/0`），并记账两处自踩：变异驱动把四次 patch 都写在循环前 ⇒ 前三条读数被第四条遮蔽成同一种红（"全红同一句"不是门严是没隔离变量）；自家 `gates.sh` 红分支里的裸反引号被 bash 当命令替换 ⇒ `readonly` 把整个 shell 环境 dump 进结论行（三行已转义并实跑复读）。**同批把门扩到集合/列表/元组**（`c7b97b9`：第二份名单不只长成像字典，`WRITE_TOOLS = {…}` 这一族同样算，扩前盘 src 该形状 **0 处** ⇒ 不误响；M-6 实测 `RC=1` 指到两行行号），并把 `TOOLS→caps` 的另一半盘成读数：**scope/caps 是 `TOOLS` 每个元组的第 5 项、与名字同源**，不是第二份名单。见 §二之十九 |
| "实现间契约不一致"补成静态门禁＝§六 那行拆开的两件里能自决的那件（计划外补刀） | **已交付，产品代码零改动** | 第七轮的 `StateProvider.snapshot()` 必须对未知实体 `raise UnknownEntity` 只有四条契约测试守着，而**每条用例只喂自己认识的那个 provider** ⇒ "新增一个忘了 raise 的实现"这条唯一的真实失败模式一条都判不红。新门 `scripts/check_snapshot_policy.py`（`gates.sh` 新节 ⇒ CI `quality-gates` 同口径硬门）射程 = src 全集 11 个类级 `snapshot` 站点里的 5 个实现，`DeviceCatalog`/`HealthEngine`/`MetricsAggregator`/`TickHealth`/`VersionManager` 五个留在射程外。**判据自身的漏法被自家反例打出来**：第一版只按 `-> Snapshot` 标注判 ⇒ 三条本职判据当场不红（`assert 0 == 1`），因为标注是可选的，而本仓 `tests/contract` 里的反面样本 `_FailOpenProvider` 正是"无标注 + 造 `Snapshot`"那一种 ⇒ 射程改成两信号**并集**（诚实记账：并集在今天 src 上零增量，买的是"下一个实现忘写标注"）。锚点 `StateProvider.snapshot() -> Snapshot` 读不到 **exit 2**；**豁免单独计入读数**（绿行原样写"全部抛"而其中一处靠豁免过关＝把未验证算成已验证，铁律 #5）。六档变异 `0/1/1/0/2/2` 全 OK，另记一次自踩：改 `check()` 返回三元组时漏改末尾 `return` ⇒ **M-0（什么都不改那档）先 BAD**，这就是它必须存在的理由。§六 那行同时拆开登记：`BoundedCache` 基类**审计两轮自己判过不做**（`git log -S` 在 `src`/`tests` 全历史 0 命中，它从未进过代码），但"有界缓存须同时给 TTL 与硬上限"的**门禁可见**那一半确实没做（`grep 有界|TTL gates.sh scripts/*.py` 零命中）——是否升级为硬要求留给 DCD，AF 不自决。是否升级为硬要求那一半已投 DCD（§五 第 11 件，随附静态口径实测读数：7 候选 / 真阳性 0/2）。见 §二之二十、§二之二十之一、§六 |
| F4 ③ / F7 前端残留 | **已收口** | `/evidence` 生产态证据视图上线；真机下发的事件回放与撤销按钮端到端真点通（读数见 §一末） |
| GitHub CI | **首次可读，且从"永久红"修到连续绿；读数路已成脚本** | 实测 run 1–27 `conclusion` 全为 `failure`（建仓以来一条没绿过），三个红因全在版本/判据层而非产品逻辑：pydantic-v2-only 的 `Field(max_length=)` 让 CI（pydantic 1.10.12）**0 条测试跑过**、undefined-name 门禁自己用了 3.12+ 的 `ast.TypeAlias`（CI 是 3.11）、真 vhass 的 skip 判据问"包能否 import"而非"插件注册了没"。三条各钉能变红的反例后，run 28 五作业全 `success`，CI 与本机通过/跳过数逐字相同（2621/51）。**run 31（守卫那次提交）五作业再次全 `completed/success`**，且 runner 侧给出安装步的实测读数 `fetch 主机读数：npmmirror=0 npmjs=87`。读数路径固化为 `scripts/gh_ci_status.py`（`runs`/`jobs`/`annotate`/`log`，纯标准库、只 GET、不打印凭证；`log` 先停 302 再无凭证取正文，免得把 token 带给日志存储域）。读数与残留见 §二之五、§二之八。**run 36（commit `3144879`，含架构门禁口径修复那批）五作业再次全 `completed/success`，runner 侧读到 `Baseline lock: 96 modules, 0 violations` 与 `Contracts: 1 kept, 0 broken`** ⇒ CI 架构门的覆盖面与本机同口径已是实测，不再是"已修 + 待复测"（§二之十一）。**run 39（`daa3af7`）与 run 40（`cd5e1bc`）也已 `completed/success`**；**run 41（`1474a7f`）、run 42（`4a63b46`）、run 43（`3d25ed0`）三作业面全绿**，其中 run 43 五作业逐条 `completed/success`（`adm-linkage-contracts`/`ui-typecheck-build`/`quality-gates`/`pytest`/`layering-gates`，`failed_steps` 全空），runner 侧正文读数 `2652 passed, 51 skipped, 1 warning in 76.98s` 与本机 §二之十五 逐字相同，`quality-gates` 正文含新门禁那行 `✓ 状态源扇出门禁干净（扫描 96 个文件…）` ⇒ 连续绿已到 **10 条（run 34–43）**，且新门禁在 runner 上的口径与本机一致（不是只在本机生效的软门）。**此后 run 44（`cf6160d`）、run 45（`a94ca71`）、run 46（`d992f29`）三条 `completed/success`**，**run 47（`b1f0ec4`，工具名单门禁那批）的 `quality-gates` 与 `ui-typecheck-build` 已 `completed/success`**，且 `quality-gates` 正文逐字读到 `✓ 工具名单门禁干净（扫描 96 个文件，注册表 31 个工具，按名调用点全部命中，现场豁免 0 处）`——与本机 §二之十九 的读数**逐字相同**（runner Python 3.11 / 本机 3.13.2，纯标准库 AST 门两版都跑）⇒ 连续绿延伸到 **13 条（run 34–46）**，run 47 其余三作业取读数时仍 `in_progress`（§二之十九）。**run 47/48 事后复查也已 `completed/success`；run 49（`d7d1fff`，状态源 fail-closed 静态门禁那批）五作业逐条 `completed/success`、`failed_steps` 全空**，runner 侧 `quality-gates` 正文读到新门那行 `✓ 状态源 fail-closed 门禁干净（5 个状态源 snapshot() 实现：抛 UnknownEntity 5 / 现场豁免 0）`、`pytest` 正文 `2722 passed, 51 skipped, 1 warning in 81.27s`（与本机通过/跳过数逐字相同）⇒ 连续绿 **18 条（run 34–51）**（run 50/51 为纯文档提交也已绿；run 52 取数时在飞），读数见 §二之二十之一。**run 52（`64f4935`）与 run 53（`24ef0c0`）事后复查也已 `completed/success` ⇒ 连续绿 20 条（run 34–53）**；**run 54/55 同挂 `0a834b9`，其中 54 是我误在远端新建 `master` 分支（`git push origin master`，而本仓默认分支是 `main`、`ci.yml:5` 三叉都触发）多跑的一套**，已 `git push origin master:main`（`PUSH_MAIN_RC=0`）并删除误建分支（`DEL_RC=0`，删后 `ls-remote` 只剩 `refs/heads/main`）；两套的 `quality-gates` 均 `completed/success`（55 那次正文末行读到 `结论：门禁干净…`），`pytest`/`adm-linkage-contracts`/`layering-gates` 取数时 `in_progress`；**事后复查四套（54/55/56/57）全部 `completed/success`** ⇒ 按 main 计连续绿 **23 条（run 34–57）**，run 57（`b148574`，观察者键集判据那批）runner 正文 `2726 passed, 51 skipped, 1 warning in 80.41s (0:01:20)` 与本机逐字相同，见 §二之二十二。**run 58（`a7c2711`）与 run 59（`cc1048b`）事后复查均 `completed/success`** ⇒ 按 main 计连续绿 **26 条（run 34–59）**；run 59 的 `quality-gates` 正文读到出向写者门禁那行、四条计数与本机 §二之二十三 逐字相同（2 处/1 文件、2 处/1 文件、载荷 2/2、豁免 0），`pytest` 正文 `2744 passed, 51 skipped, 1 warning in 46.79s` 与本机同数 ⇒ 那条新门在 runner 上判的是同一份代码。**run 60（`b50bb22`，入向订阅门禁那批）的 `quality-gates` 已 `completed/success`**，正文读到本批新门那行且与本机逐字相同；其余三作业取数时 `in_progress` ⇒ 该套结论落地后再抬 streak。**run 60 事后复查已 `completed/success`**，**run 61（`16ad74e`）亦 `completed/success`**（五作业逐条 `success`、`failed_steps=[]`；`pytest` 正文 `2764 passed, 51 skipped, 1 warning in 84.57s` 与本机 §二之二十四 同数）⇒ 按 main 计连续绿 **27 条（run 34–61）**；本批 `da09d43` 触发的 run 结论落地后另记，读数见 §二之二十五 |
| DCD 20261002 §一（CI 锁文件源）| **裁定 (b) 已落地并已在 runner 上取到读数** | 只按原文写 `--registry=https://registry.npmjs.org` 经实测是**空操作**（fetch 87+87 行仍在 `registry.npmmirror.com`/`cdn.npmmirror.com`），必须配 `--replace-registry-host=always`；安装步另加"主机自证"守卫（CI 绿不能证明没吃镜像）。逐字节对账 137/137 `integrity` MATCH ⇒ 锁文件字节未动。§二之七 末尾"本机 npm 11.9.0 / runner 10.x，只有 push 之后才知道"的残留**已销账**（runner `npmmirror=0 npmjs=87`）。回执已投 DCD（含 §四 判例 2 的证据更正：`@types/node` tarball 两源逐字节相同、根目录差异不是重打包痕迹）。见 §二之七、§二之八 |
| `ma/insights` **入向与契约表对账**（本批新抓） | **AF 侧已按契约收，跨仓三问已交 DCD** | 拿着裁定 Q3 之后的载荷行逐条对 AF 的 `ingest_insight()`：必填的 `hypothesis_id`/`natural_language`/`conf` **三项契约一个都不发** ⇒ 每一条按契约发来的洞察都被拒（判例 1 的静默归零，面换到 AF 入口）。改法：契约键优先 + 旧键别名、缺 `conf`（含 `{"conf": null}` 这种空占位）按 0.0 入 ask 档并记 `conf_reported=false`（坏报照旧拒、封顶 0.59 不动）、契约字段有界落 `transport` 并出 `/api/insights/pending`，面板对缺报显示「未上报」而不是 `0.00`。`conf`/稳定 id/`intent` 三处空缺 AF 不能自决 → §五 第 7 件。见 §二之八 |
| AF **出向**载荷与契约表对账（本批新抓，与上一行同族、方向相反） | **`error` 那半已自决修；`trace_id` 那半交 DCD** | 对着 §1.2 两行逐字段读码：`fired` 齐（`ref`=实例 id、`ts`=家庭墙钟）；**`failed` 的 `error` 此前恒等于字符串 `"failed"`**（`observe_terminal()` 传 `error=state`），而真原因一直写在 `ctx.context["fail_reason"]`（`af_instance.py:287`）、且 `af_executor.py` 每条 `_fail()` 都带语义（失败的动作 / 软失效异常 / 收敛违约）⇒ DB 唯一能向用户显示的那句话被换成了状态名。修法只装契约已定义的东西、字段名与类型没动：桥读出真原因，缺原因发诚实占位句而不是状态名，按 §1.3 已定过的 500 封顶（`5ff1ea6`，+3 判据：真原因穿得到 / 退化成状态名即红 / 跨模块改键名即红，四档变异 `0/2/1/1` 各自钉一次）。**"为什么不升成静态门"**：`error=state` 与 `error=reason` 在 AST 上同形，静态可判的是 §二之十八 那族的"**没递**"、不是"递错"。`trace_id` 每次现场新造那一半 **AF 未动**：§1.3 那句"trace_id 必填、贯穿洞察→收件箱→播报"实测挂在**收件箱三面**护栏下（`grep -rn "publish.*butler/inbox" src/autoforge` = 0 ⇒ AF 不踩），§1.2 事件面只列了字段名；上游那枚号 AF 收了并落到 GraphStore 记录 `note`，但 `hypothesis_id` 在 `af_ir/`+`af_instance.py`+`af_executor.py` **0 命中** ⇒ 到不了发射点，三种修法两种要动载荷面/DB 检索语义（1:1→1:N）⇒ §五 第 12 件，A/B/C 齐、AF 建议 C（明确为事件级，因果链靠 `ref`＋各自存储元数据）。**同批打真载荷又盘出第三个差额**：观察者路径比契约行多发一个 `node_id`（`instance_id` 那份额是 ②A 登记过的，它没有任何登记）⇒ 已用 `159ba00` 把真实键集合钉死（四档变异 `0/1/1/2`），并作为第 12 件的**第三问**交裁（AF 倾向：DB 不读就删）。见 §二之二十二 |
| AF 出向 MQTT 的**生产者形状**升成静态门禁（`8b629b8`，本批） | **已上线为 CI 硬门，产品码 0 变动** | 上一行那个差额暴露的不是"某个字段错"，而是"**测试绿在一条生产不走的路径上**"。这一族能静态判（判的是调用图形状：哪个文件、哪个函数体内、载荷实参是不是 `_envelope()` 的产物），与 §二之二十二 那条"值语义判不了"正好成对。三条判据：出向 MQTT 写者只在 `af_mqtt_bridge`；`publish_fired/publish_failed` 只在 `observe_terminal()` 体内（最内层归属，闭包也算）；`_publish` 的载荷必须来自 `_envelope()`（位置式与 `payload=` 关键字式都认，只认前者=静默放行）。锚点缺失 `exit 2` 不做假绿；`af_bus`/`af_runtime` 的 `publish()` 是进程内总线，不在射程。真实 src 实测：写者 2 处/1 文件、生产者 2 处/1 文件、载荷 2 处全部经 `_envelope()`、豁免 0；18 条反例 + 三档变异（`RC=1/1/2`），M-1 那处违例另走**完整 `gates.sh`** 取到 `RC=1` 与新结论行。见 §二之二十三 |
| AF 入向订阅的**守卫形状**也升成静态门禁（`b50bb22`，本批） | **已上线为 CI 硬门，产品码 0 变动** | 出向那半盘完，同一形状在入向：契约表 §1.3 护栏与计划 第 1 步 ④ 说"不订阅 `butler/inbox/*`"，而这条今天只有运行时判定（`FORBIDDEN_SUBSCRIPTIONS` + `subscribe_topic()` + `handle_message()` 两道）加行为测试——**行为测试只认识已知入口，下一个不查守卫的 `client.subscribe(…)` 一条都不会红**。三条判据：订阅口只在 `af_mqtt_bridge`；动态主题要在**同一函数体内**先过禁订族判定（守卫在外层、订阅藏在闭包 ⇒ 红）；收件箱族写死成实参就红，带守卫也不给过。射程取两条信号并集（接收者是 `client`，或调用带 `qos=`），进程内 `bus.subscribe(handler)` 不判。锚点缺失 `exit 2`。真实 src 实测：**订阅点 2 处/1 个文件，其中 `INSIGHTS_TOPIC` 1 处、过了守卫的动态入口 1 处、豁免 0** ⇒ 计划 第 1 步 ③④ 那句"只订 `ma/insights`"静态坐实。20 条反例 + 四档变异（`RC=0/1/1/1/2`，M-1 另走完整 `gates.sh` 取到 `结论：入向订阅门禁红（exit=1）`），还原逐字节自证。run 60 的 `quality-gates` 正文已读到本门那行且与本机逐字相同。见 §二之二十四 |
| 窗后四项验收从"手搓命令"升成一条命令（`da09d43`，本批） | **已交付工具 + 27 条反例，验收本身仍 EXEMPT** | 裁定 §四 那四件此前只作为文字挂在 §六，窗当天临时手搓——少跑一项、把 skip 读成 pass、把 `/health` 打错成 404 就判服务没起，都是过账形状。`scripts/verify_adm_window.py` 做成**三态逐项判定**：有 FAIL ⇒ `RC=1`；无 FAIL 但有 UNAVAILABLE ⇒ `RC=2` 且正文打印"EXEMPT ≠ VERIFIED"；全 PASS ⇒ `RC=0`。判据取自契约表：③ 查 §1.2 四个键 + `ts` 偏差（拦仿真锚点当墙钟那族）+ **事件不许 retained**；④ 直接问 `msg.retain`（现发的 `online` 不算快照）；② 的"连不上"由 ① 决定语义（容器在跑却连不上 ⇒ FAIL，本机没 docker ⇒ UNAVAILABLE）。连接与凭据走机制层 `homesdk.mqtt`，脚本**不读任何环境口令**。本机实测：降级跑 `RC=2` 四项皆缺、对 `forge serve` 真跑取到 `[PASS] /api/health 返回 200`（顺带钉下两事实：AF 只注册 `/api/health`，`docker-compose.api.yml` 无 healthcheck）；变异三档各 `1 failed` 且还原逐字节一致；全链 **2791 passed / 51 skipped RC=0**、`gates.sh` RC=0。同批更正 5 处旧登记的理由——"本机无 paho-mqtt"是跑错解释器（实测 `paho_available()=True`），真缺的是那台 broker。见 §二之二十五 |
| UI 类型门禁（上一版登记的债） | **已收口（含下一批零 `any`）** | 15 条 `vue-tsc` 错误清零，`type-check` 改 `--force` 防缓存假绿，`ci.yml` 新增 `ui` 硬门 job；过程中抓到"两个按钮从未接线"与"mock 夹具少回两字段"两条真缺陷（§二之二）。同族第二批：API 层四处 `request<any>` 换成后端真形类型并逐键与在线响应对账，`grep request<any> ui/src` 已无匹配（§二之三） |
| 部署 | **未部署** | 铁律 #3：文档/本地阶段不部署，镜像重烤须进合并停机窗 |

---

## 一、逐步落点与验收证据

### 第 0 步 ①②：vendor 升级 + 时区接入机制层

| 项 | 实测证据 |
|----|----------|
| `pyproject.toml` pin | `pyproject.toml:48` → `homesdk>=0.3.1`（不是 `==`，允许库侧前向修复） |
| 机制层主路径 | `src/autoforge/af_time.py:110-116` `homesdk_time()` 取 `homesdk.time`；`:149-153` 主路径调机制层，缺席才走 AF fallback |
| 键名口径（裁定 §五 的 AF 半边） | `af_time.py:90-103`：规范键 `HOMESDK_TZ`，`HOMESDK_` 前缀优先于裸键，与 `homesdk.config` 同一约定；`:84-85` `AF_TZ` 降为过渡别名 |
| 时区例（裁定 §五 的验收量） | `tests/unit/test_af_house_tz.py` 分跑 = **`9 passed` RC=0**（正是计划验收列写的"9 例"；此前记录只写"在全量内内含"，本批按点复测，见 §二之十三） |

**0.3.1 wheel 逐字节核验（本轮新增实测，此前只有本地哈希）**：

```
$ sha256sum /e/NAS/homesdk/dist/homesdk-0.3.1-py3-none-any.whl \
            '//192.168.2.200/docker/libs/homesdk/homesdk-0.3.1-py3-none-any.whl'
36fdf77a0963233354cefe26908f66c798a68b5539b87434953a42f27247fe95  （本地 dist）
36fdf77a0963233354cefe26908f66c798a68b5539b87434953a42f27247fe95  （NAS 库）
```

⇒ 投递已完成且两份是同一个文件。**但**：NAS `VERSIONS.txt` mtime `Sep 29 20:50` 早于 wheel mtime `Oct 1 22:03`，表内最后一条语义增量仍写 0.3.0，**没有 0.3.1 条目**；homesdk 源树里 `src/homesdk/time.py`、`tests/test_time.py` 仍是未跟踪状态（`git status --short` → `??`），`pyproject.toml / src/homesdk/__init__.py / CHANGELOG.md / .gates.toml` 为 ` M` 未提交。
⇒ 结论：**这个 sha 无法从任何 commit 复现**。AF 已把 `>=0.3.1` 写进 pin，等于把"无源码账、无库侧账"的构建产物变成运行时前提。此风险 AF 单方面消不掉（不得往他仓 main 写代码），已提 DCD（§五 申请 2）。

**0.3.1 换版销账（裁定 §〇/§一 落地，本回合实测）**：DCD 当场补账（homesdk commit `5e4ba33`：`src/homesdk/time.py`、`tests/test_time.py`、契约表、使用说明全部入库 + NAS `VERSIONS.txt` 补 0.3.1 条目），并按 AF 预案**以入库后重建的产物为权威 sha**。首投 `36fdf77a…` 作废。本回合三份副本的读数：

```
$ sha256sum docker/homesdk/homesdk-0.3.1-py3-none-any.whl \
            /e/NAS/homesdk/dist/homesdk-0.3.1-py3-none-any.whl \
            '//192.168.2.200/docker/libs/homesdk/homesdk-0.3.1-py3-none-any.whl'
b4b5d6bbe424205bb425762223576a3b3409c93f262f79bc8e5a7016ad4b814c   （AF vendored，本轮换入）
b4b5d6bbe424205bb425762223576a3b3409c93f262f79bc8e5a7016ad4b814c   （homesdk dist）
b4b5d6bbe424205bb425762223576a3b3409c93f262f79bc8e5a7016ad4b814c   （NAS 库）
```

NAS `VERSIONS.txt`（mtime `10-02 12:54`）第 66-80 行即 0.3.1 段，表内 sha 写的就是 `b4b5d6bb…`，并逐字记录了"两次构建、成员逐字节一致、差异仅 dist-info 时间戳、首投作废"。
**可复现性另开一刀验**（不看台账看内容）：vendored wheel 44126 B / 18 个成员，`zipfile.testzip()` → `None`；`homesdk/time.py` 与 `E:/NAS/homesdk/src/homesdk/time.py`（`5e4ba33` 已入库）**逐字节相同**（6800 B，sha256 前 16 位同为 `795b2e7624ebe39c`），`homesdk/__init__.py` 同样逐字节相同（1627 B）。⇒ 现在"pin 的产物"能对到一个 commit 上，DCD 判例 1（投递产物必须与源码同账）在 AF 侧闭合。
运行时侧口径：本机 `python -c "import homesdk"` → 版本 **0.3.1**、路径 `E:/NAS/homesdk/src/homesdk`（可编辑安装指向源树），`homesdk.time` 可导入 ⇒ vendor 自检第 7 条（`house_tz/house_now/to_house_iso` 存在）在开发机成立；**镜像内**的同一校验仍未做（须进合并窗随重烤验，见 §六）。

**但源树可编辑安装证明不了 vendored 这份产物**，所以另跑一次"不安装"的直读（wheel 本身就是 zip，CPython 的 zipimport 能直接吃）：

```
$ python - <<'EOF'   # sys.path 插入 docker/homesdk/homesdk-0.3.1-py3-none-any.whl 后 import
imported_from E:\NAS\AutoForge\docker\homesdk\homesdk-0.3.1-py3-none-any.whl\homesdk\__init__.py
version 0.3.1
surface_present {'house_tz': True, 'house_now': True, 'to_house_iso': True, 'house_tz_name': True, 'house_tz_status': True}
default_tz Asia/Shanghai
to_house_iso(0) → 1970-01-01T08:00:00        RC=0
```
⇒ vendor 自检第 7 条对**仓内这份 wheel 本体**成立（读的是 `docker/homesdk/` 里的文件，不是源树），且 `to_house_iso` 给的是 +8 家庭墙钟而不是 UTC。全程未 `pip install`（红线），zip 路径只进当次进程。

### 第 0 步 ③④：为什么停在这里

- ③ NAS 镜像重烤 = 生产写动作，铁律 #3 要求变更窗 + 复核；计划 §四 又要求与 AgentOps 模板/DB 写面/MA PII **合并为一次窗口**。
- ④ AgentOps 是**他仓 main**。本会话红线：不往 homesdk / AgentOps / memory-agent 的 main 写代码。
⇒ 两件事合成一份 DCD 申请（§五 申请 2），由 DCD 定窗。

### 第 1 步：MQTT 桥（commit `8db7c5e`）

| 子任务 | 落点 |
|--------|------|
| ① 新建桥，复用 homesdk `mqtt`/`presence`，缺凭据 fail-closed | `src/autoforge/af_mqtt_bridge.py`（模块级 docstring 即写"只发自己的语义主题"）；`start_from_env()` :395 起 |
| ② 发 `af/automation/fired` 与 `failed`，**不 retained** | `:51-52` 主题常量、`:258-262` `publish_fired/publish_failed`；`:240-252` 信封 `{trace_id, ts, automation_id, ref}` |
| ③ 只读 `ma/insights` → 编译候选 → 进审批，不自动部署 | `:53` `INSIGHTS_TOPIC`、`:311` 回调只认该主题、`:340-378` reject 缺 `hypothesis_id`/`conf`、`conf` 被 `INSIGHT_CONF_CAP` 封顶后 `proposal_sink.submit(..., source="ma")` |
| ④ 不订阅 `butler/inbox/*` | 白名单门禁实测：`✓ 主题白名单门禁干净（7 处 topic 字面量全部在契约表内）`（`scripts/check_topic_whitelist.py`，本轮 gates 内） |

`ts` 字段口径：跨仓时间戳走家庭墙钟（契约表 §四），本轮 commit `d602e93` 把信封对齐到 `homesdk.time` 的墙钟口径。

### 第 2 步：presence

| 子任务 | 落点 |
|--------|------|
| ① `advertise` 发 retained `adm/autoforge/status` + `caps` | `af_mqtt_bridge.py:218-220`；`:148` caps 载荷注释——**桥在 L1，不许 import `af_mcp`**，`tools` 由入口层（L2）传入 |
| ② LWT 离线 + 优雅下线双路 | `:220`（`presence.advertise` 带 LWT，覆盖异常断连）+ `:297-298`（`offline=True` 显式 retained offline，覆盖正常退出） |

⛔ 未实测部分（诚实标注，铁律 #5：EXEMPT ≠ VERIFIED）：`kill -9` 后由 broker 代发 `offline` 需要真 MQTT broker；本机缺的是**那台 broker**（`MQTT_HOST` 未配、无 `docker`/`mosquitto`，且禁 pip install）——先前这里写的"本机无 paho-mqtt"是跑错解释器的结论，实测 `homesdk.mqtt.paho_available()` 为 `True`（见 §二之二十五 的更正），但结论不变：桥的测试用 duck-typed client 覆盖到"调用形态正确"，未覆盖 broker 行为。

### 第 3 步：F9 group 节点（commits `34a540c`、`f2d0a5c`）

| 子任务 | 落点 |
|--------|------|
| ① `ir_version` 由 `const` 改枚举 + 放宽 `additionalProperties` | `src/autoforge/af_ir/schema/ir.schema.json:10` → `enum: ["0.2.1","0.3.0"]`，真值源 `af_ir.SUPPORTED_IR_VERSIONS`；枚举与常量同步由测试锁：`tests/test_af_ir_group_mode.py:43` `test_ir_version_enum_syncs_supported_versions` |
| ② `group` kind + `mode: sequence|parallel` + 递归 `children` | schema `:60-64`（节点 kind 含 `group`；`mode` 枚举 `sequence|parallel`，缺省 `sequence`，真值源 `af_ir.GROUP_MODES`）、`:129-133`（`children` → `$defs/child_automation`）、`:161-164`（group 必须 `children` 且 `minItems: 1`） |
| ③ 编译 / 仿真 / NL 渲染 / 诚实报告四段全过 | `af_executor.py`、`af_nl.py` 对 group 的支持；NL 措辞测试 `tests/test_af_ir_group_mode.py:139`；**不静默丢子树**：`:151` `test_spec_text_render_refuses_group_instead_of_dropping_children` |
| ④ 原子性验收：模拟中途失败 ⇒ 已部署部分全回滚 | `tests/test_af_ir_group_apply.py:91` `test_group_atomic_rollback_on_sim_failure`；整组按 ref 原子路由 `:124`；冲突预检 `:106`、`:136` |
| 保真补刀（本轮） | commit `f2d0a5c`：group 容器把 `mode` 与**整棵 children 子树**纳入核心比对，堵掉"子树不同也算绿"的假保真；`tests/f14/test_fidelity_roundtrip.py` |

**归属澄清（记在这里，不改历史）**：`apply_group` 的回滚把手改成"按 pending `op_id` 撤销"（`src/autoforge/af_apply.py:331` 起）落在 **`0dd57b2`**，而它的回归测试落在 **`34a540c`**。commit message 里的描述与实际落点有错位，事实以此处为准。

### 第 4 步：MCP 工具面（commit `0dd57b2`）

| 子任务 | 落点 |
|--------|------|
| ① `draft/verify/deploy` 工具面 + DB 调 dry_run | **已按裁定 ①A 收口**：命名以 AF 现名为准 —— `af_draft` → `af_apply(stage="simulate")` → `stage="dry_run"` → 人批 → `stage="save"`（实测 `af_apply.py` 的 `APPLY_STAGES = ("check","simulate","dry_run","save")`，未知 stage 拒收，旧名 `apply` 经 `STAGE_ALIASES` 归一到 `save`）。裁定给的正是这条链："验"与"部署"是同一工具的不同 stage，天然不可能"验着验着变部署"；**计划文档 §第 4 步 ① 的措辞本回合已对齐**，正式重发由 DCD 出（裁定 §八.1） |
| ② ask 通道 | 已有；裁定 ③A 落地：`af_api.py` 的 `/api/asks/answer` 现在**两条分支都带 `channel_error`**——`inbox_key_missing` 时为 `true`，签名落盘成功时为 `false`。DB 侧义务（见字段即告警、不得把该 ask 记为已答）由 DCD 写进裁定 §八.2，属 DB 仓改动，AF 不能代为生效 |
| ③ 契约测试 | `tests/contract/test_af_db_contract.py` 存在并在本轮 2615 项内绿，断言 ask+answer schema、鉴权、`INBOX_KEY` 未设即 fail-closed 拒收；`tests/contract/test_af_ask_contract.py` 本回合补了 `channel_error` 的两个方向（假绿侧与告警侧都能变红） |

### 裁定 20261002 的 AF 侧落地（本回合新增）

> 真源：`E:\NAS\关键决策部\decisions\20261002-homesdk记账与AF-DB-DPP六件-裁定.md`（申请来源含 AF 的三份申请）。逐件对账：

| 裁定 | 落点（file:line） | 机器判据 | 没做的部分（诚实标注） |
|------|-------------------|----------|------------------------|
| §〇/§一 wheel 换版 | `docker/homesdk/homesdk-0.3.1-py3-none-any.whl` 换成重建产物；`pyproject.toml:48` `homesdk>=0.3.1`；`docker/Dockerfile.api:23-24`、`Dockerfile.test:26-27` 文件名钉死不用通配 | 三份副本 sha256 同为 `b4b5d6bb…`（读数见上一小节）；wheel 内 `homesdk/time.py` 与 homesdk `5e4ba33` 的源文件逐字节相同 | 镜像内 vendor 自检第 7 条须随重烤在窗口内跑（本机装的是可编辑源树，测的是开发机不是镜像） |
| ①A 工具面命名 | 计划文档 §第 4 步 ① 措辞改为 `af_draft` → `af_apply(stage="simulate"/"dry_run"/"save")` 链；代码侧无需改名（现名即口径） | `af_apply.APPLY_STAGES` 与 `STAGE_ALIASES` 既有测试覆盖 | 正式**重发计划**由 DCD 出（裁定 §八.1）；AF 只对齐本地副本，不代替 DCD 定稿 |
| ②A `ref` = 实例 id | `af_mqtt_bridge.py:258-271` `_envelope`：`ref` 与 `instance_id` 同值，docstring 写明"删除时点 = AF v2.6" | `tests/unit/test_af_mqtt_bridge.py:130-132` 钉住字段集与 `ref == instance_id == "inst-1"`；`instance_id` 双写仍是过渡字段 | v2.6 前不删（破坏性变更须与停机窗同做）；契约表的定义由 DCD 补（裁定 §八.3） |
| ③A `channel_error` | `af_api.py:817`（`inbox_key_missing` → `True`）、`:821`（签名落盘成功 → `False`） | `tests/contract/test_af_ask_contract.py:172` `is False` / `:197` `is True` | **DB 侧的告警分支不在 AF**（裁定 §八.2 已把义务写进 DB 计划）；AF 单方面加字段≠该 ask 会被真的告警 |
| ④A 洞察队列持久化 | 新模块 `src/autoforge/af_insight_queue.py`（`InsightRecord:41` / `InsightQueue:68` / `PersistentInsightSink:174` / `InsightQueueFull:36`，`DEFAULT_LIMIT=500`）；桥侧工厂 `af_mqtt_bridge.py:149` `make_durable_ask_sink`；接线 `af_cli.py:1315,1330`（真机/dry-live 起桥时落 `{store_root}/insight_proposals`）；交接 `af_service.py:500 approve_insight` / `:551 reject_insight`；HTTP `af_api.py:752 _insight_queue`、`:761 /api/insights/pending`、`:770 /api/insights/approve`、`:780 /api/insights/reject`（行号按 HEAD 重取；旧读数 `:733/:742/:751/:761` 因 `a5c8aa9` 的 events 上限而下移） | `tests/contract/test_af_insight_queue_contract.py` 当时 **13 项**（本回合新增；该文件现为 **`14 passed` RC=0**，后一批补了"契约形状入向到面板带记账"那条，分跑读数见 §二之十三）：落盘后换队列实例仍读得到、入桥 conf 被 `INSIGHT_CONF_CAP=0.59` 封顶仍在 ask 带、队列满 `InsightQueueFull` 且不丢旧条、approve 只生成 `af_pending` 一条 `af_save` 且归档目录 `*/v*.json` 为空（**没部署**）、重复 approve 409、无 IR / 两条自动化 400 且记录仍在 pending、未知 404、只读实例 200 读 + 503 写、坏 JSON 进 `stats()["unreadable"]`、结构约束（队列模块不 import `af_apply/af_pending/af_service/af_deploy/af_executor`，也不持有 `approve`/`deploy` 方法） | 队列没有 UI（`/api/insights/pending` 已可读，面板未做）→ **面板已上线 `/#/insights`**（本批，读数见 §二之四）；`ProposalManager` 的进程内 ask 档保留给测试与分诊，两条路径并存是刻意的 |
| §四 StateProvider | A（整段 fail-closed）已在 commit `628502a`（第七轮修复）落地，本轮无改动 | `tests/contract/test_state_provider_policy.py` **14 项**（本回合 `--collect-only` 实测）；裁定"共同要求：漂移必须以 `entity_drift` 记账、不许静默"的链是 `af_instance.py:374-392` 空快照回退 → 表达式逐条抛 `UnknownEntity` → `af_executor.py:596-609` `_soft_fail` 记 `ENTITY_DRIFT`（带 `node_id`/`instance_id`/`entity_id`） | **B（逐实体可见漂移）本轮不做**，启动条件按裁定：`af_instance._refresh_snapshot` 下次被真实改动时，或 AF v2.6 窗口。裁定把整段口径判为**有意设计**（不是遗留），所以 B 是加功能不是修 bug |
| §一 合并停机窗 | AF 侧无代码动作 | — | 窗后验收四项（`compose ps` + `/health` 200 + `mosquitto_sub` 抓到一条含家庭墙钟 `ts` 的 `af/automation/fired` + `adm/autoforge/status` retained `online`）**缺任一项即第 0 步未完成**，本机缺一台真 broker（`MQTT_HOST` 未配、无 `docker`/`mosquitto` ⇒ 只能随窗做；先前写的"本机无 paho-mqtt"是跑错解释器的结论，§二之二十五 已更正）⇒ 四件已固化为一条命令 `scripts/verify_adm_window.py`，窗当天在 NAS 上跑一次即得逐项读数，缺项退出码 2 而不是 0。接线级判据本批就位（`_make_runtime` 递 `runtime.clock` 给桥、仿真分支不起桥，两次变异各 `1 failed`）——但它只保证"传错会红"，**不等于窗后抓包已发生**，这一项仍是 EXEMPT 不是 VERIFIED（铁律 #5） |

**④A 为什么这样切**：裁定要的是"重启不丢" **且** "MA 来源与人来源在部署面上不同权"。所以队列只落盘、不持 deployer；`approve` 走 `submit_pending` 的常规路径（静态扫描第一道闸 + per-agent 熔断），与人工提交同闸，落 `af_pending` 后**仍需** af_pending 的 approve 才部署。两道闸不是冗余：前者管"MA 投的东西不许直接跑"，后者管"任何人提交的东西都要人批"。

**铁律 #8 的反证**：`/api/insights/*` 三个端点最初没走 `_svc`（错误映射垫片），`ServiceError` 会以未捕获异常逃逸——本回合这批测试**先红后绿**：修之前 `5 failed, 20 passed`（RC=1），把 400/404/409 包进 `_svc` 后 `25 passed`（RC=0）。红的是判据本身，不是环境。

**棘轮自己咬了一口**：新增三处 `ok=True` 字面量被计数棘轮判红（`全量违规 107 条 / 登记上限 104 条`，RC=1）。处置是**删假安心字段**而不是上调上限：查询/写回成败由 HTTP 状态表达（与 `/api/evidence/prod` 同一口径），`_insight_queue()` 在无 store 时改抛 503 而不是 200 带 `ok:false`。修完 `104 / 104`、RC=0。

### 第 5 步：后续优化三项（① ③ ④ 全落；② 按**本计划验收点**已落，按**裁定执行约束**少一条子句）

| 子任务 | 验收点 | 实测落点 |
|--------|--------|----------|
| ① 单写者租约，抢不到锁降级只读（裁定 A） | serve 启动 `try_acquire` 失败→只读 | `src/autoforge/af_cli.py:1366` `readonly = not _lock.try_acquire()` → `:1376` 传给 `build_app(..., readonly=readonly)`；`af_api.py:357-362` `_readonly_guard`，挂在 `:465 /api/build`、`:473 /api/bind`、`:481 /api/sim`、`:545 /api/spec/compile`、`:714 /api/live/run`、`:735 /api/undo/{deploy_id}`（铁律 #6 写面 503）。**接缝判据本批补上**（此前两端各自有测试、中间那根线一条判据都没有）：`tests/unit/test_af_cli_serve_lease.py` 两条，桩 `uvicorn`/`build_app`/桥，锁分别返回 `False`/`True` ⇒ 断言 `build_app` 收到的 `readonly` 为 `True`/`False`；两次变异各取到红（`readonly=False` ⇒ `1 failed` `assert False is True` @ `:70` RC=1；`readonly=True` ⇒ `1 failed` `assert True is False` @ `:81` RC=1），复原后 `2 passed` RC=0。⚠️ 本行旧读数是 `:1365/:1375/:340-348/:449…`，因 `a5c8aa9` 给 `/api/sessions`、`/api/live/run` 装 events 上限而下移了行号，本批按 HEAD 逐条 `grep -n` 重取 |
| ② `af_persist` 加校验和（裁定 B，不做全量 eventlog） | **本计划的验收点已落；裁定的执行约束少一条子句** | 已落：`src/autoforge/af_persist.py:40` `_SHA256_KEY`、`:76-82` 计算、`:83-89` 校验（无字段=旧格式视为通过，向后兼容）、`records():223-238` 坏文件/校验不过**跳过并 warning**、`save():171-187` 原子替换且**未改存储格式头**；单测三条在库（`tests/unit/test_af_persist.py:106/:224/:235/:249`）。**未落**："顺序追加"这一子句——现状是每实例一个快照文件、覆盖式写。它同裁定另一条"不改存储格式头/不迁移"相抵，也与第五轮把"每条全量重写 + 明细无上限"收成有界的修法反向 ⇒ 不擅自动存储面，提 DCD（§五 第 9 件，见 §二之十二） |
| ③ import-linter 分层（裁定 B→A 渐进：**两档都已落**，第二档在本批） | 配置 + 违规清单 ≈0 ⇒ `pyproject.toml:79-98` `[tool.importlinter]` 契约 "Service boundary never imported by kernel"；实测 `lint-imports` → **Contracts: 1 kept, 0 broken**（RC=0），runner 同契约 `KEPT`；`.github/workflows/ci.yml:96` 的 `architecture` 作业里 `lint-imports` **已去 `continue-on-error`、作失败门禁**（观察窗 shortfall 与本批读数见 §二之十，追认申请见 §五 第 8 件）。真正的架构硬门仍是 `scripts/check_imports.py`（ci.yml:87）→ 本机 **`Baseline lock: 96 modules, 0 violations`**（判红的是 `violations != 0`，模块数是覆盖面读数）。⚠️ 本行曾写 86：那不是环境差异而是缺陷读数——`.gitignore` 的 `_*.py` 吞掉 `af_closedloop/__init__.py`、grimp 在 runner 上不递归该包，**CI 门比本机少分析 10 个模块**；已修（负向规则 + 新复发门 `scripts/check_pkg_markers.py` 进 `gates.sh`），全链与"还原原状仍能取红"的实测见 §二之十一；**runner 侧的 96 已复测取到**（run 36 日志：`Baseline lock: 96 modules, 0 violations`） |
| ④ tick 线程自愈，区分原因重启（裁定 C）+ 必补接缝测试 | "SAFE HALT 后 watchdog 不得重启"绿 | `src/autoforge/af_live.py:77-103` `tick_watchdog_pass`（唯一自愈分支是"意外终止"；`safe_halt`/`stop` 分别返回 `held_safe_halt`/`stopped` 且不重启）；接缝测试 `tests/unit/test_af_live.py:306` `test_watchdog_does_not_restart_after_safe_halt`（断言 `calls == []`），对照 `:315` 意外终止确实重启、`:325` 存活时不动作；`af_tick_supervisor.py:185` HALTED 短路、`:222-224` docstring 写明安全红线 |

### 门禁肥化的另一半：计数棘轮（本回合新增，`gates.sh` + `.gates-tally.txt`）

AST 门只看"新增=0"，指纹一旦进了 `.gates-baseline.txt` 就**永远绿**，存量却在涨。棘轮把**全量总数**钉在登记上限上：

| 项 | 落点 / 实测 |
|----|-------------|
| 上限登记 | `.gates-tally.txt` → `104 # 2026-10-02 登记（AST 全量口径，--no-baseline --no-smoke）`（`except-pass-broad=27 \| fake-ok-const=77`） |
| 判据 | `gates.sh` 的「计数棘轮」段：`total` 取自 `python -m homesdk.gates "$REPO" --no-baseline --no-smoke`，`cap` 取自 `.gates-tally.txt` 首个数字行 |
| 上调 | 红：`棘轮红：总数从 103 涨到 104`（把 cap 改成 103 实测，rc=1） |
| 下调不改账 | 也红：`棘轮提示：总数降到 104，请把「.gates-tally.txt」的上限同步下调（只准降 = 防肥化）`（cap 改成 105 实测，rc=1） |
| 没登记 | rc=2：`缺 /e/NAS/AutoForge/.gates-tally.txt —— 先跑一次…把全量计数登记成「总数 # 日期 说明」`（把文件移走实测） |
| 正常态 | `全量违规 104 条 / 登记上限 104 条`，整条 `gates.sh` rc=0 |

### 第 0 步 ④ 的 AgentOps 模板：本轮抓到并修掉一个真缺陷

模板里 `Tally ratchet (hard)` 的提示文案原先用反引号包文件名。**`run: |` 下那些字符串是双引号**，反引号被 bash 当命令替换真的执行了——实测表现是把 `.gates-tally.txt` 当脚本跑：

```
.gates-tally.txt: line 1: 104: command not found
```

后果不只是难看：命令替换的退出码会污染 `cap`/`total` 的取值路径，棘轮在真 CI 上的 `exit 2` 契约不可靠。修法：文案里的反引号换成「」（`E:/NAS/AgentOps/gates/templates/ci/gates.yml`，同文件已去 `continue-on-error`、加 smoke-gates/ast-gates 存在性硬检）。

配套把 `scripts/mutation_check_templates.py`（模板自检）从"只比退出码"升级为**退出码 + 判语文案**双断言。理由记在脚本 docstring：只看 rc 会放过"因为别的原因红"——上面这个反引号缺陷正是靠两条变异"绿在 rc=1"上蒙过去的。本回合实测 10/10 符合预期、rc=0（读数见 §二）。

⚠️ 记账事实：`E:/NAS/AgentOps` **不是 git 仓**（`git -C E:/NAS/AgentOps rev-parse` → `fatal: not a git repository`）。所以第 0 步④ 授权改的模板**没有提交记录、不可回滚、sha 无出处**——已提 DCD（§五 申请 4）。

### F4 ③ 生产态证据视图 + F7 撤销端到端（本回合收口 §六 两项残留）

| 项 | 落点 / 实测 |
|----|-------------|
| 新视图 | `ui/src/views/EvidenceView.vue`（路由 `/evidence`、菜单「生产态证据」，`ui/src/router/index.ts`、`ui/src/App.vue`）。三档**并列**渲染：验过 / 验出问题 / 无从验证，外加冲突仲裁计数、影子/金丝雀/冲突分列、最近一次时间戳（null 显示 `—`） |
| 契约面 | `ui/src/types/api.ts` 加 `EvidenceAutomation`/`EvidenceSummary`/`EvidenceProdResponse`，字段与 `af_watch.verified_in_prod()`（`src/autoforge/af_watch.py:114-163`）逐字对齐；**刻意没有 `ok` 字段**——查询成败由 HTTP 状态表达 |
| 前端取数 | `ui/src/api/client.ts` `evidenceProd()`；`ui/src/api/index.ts` **不做 mock 兜底**并写明原因：证据面板造 fixture = 假安心 |
| 空态诚实 | `tracked_automations === 0` 时告警"这不等于自动化被验证过…服务重启即清零"；读失败分支**清空数据**而不是显示零 |
| 三档实测（喂数） | 临时夹具起 8791：API 返回 `total_verified_in_prod:4 / failed:2 / unmodeled:1 / conflict:2 / tracked:3 / automations_with_failed:2`，页面读数与 JSON 逐项一致 |
| 空态实测 | 8793（真跑过一次真机下发的进程，未喂 watch）显示 0 + 空态告警 |
| 文案自纠 | 空态原文写"请让 watch 或真机下发跑起来再刷新"，实测**不带 canary 节点的普通真机下发不产证据**（`af_executor.py:494-500` 的 `use_canary` 要 `node.canary` + conf `auto` 带 + 非 dry-run 适配器）。文案改为点名三个来源并明说这一点——我自己的新视图里也不能留"跑了就看得见"这种承诺 |
| F7 端到端 | 浏览器真点：真机下发 → `撤销 ID：dep-28bf327fe8d7` → 点「撤销」→ popconfirm「确认回滚」。假 HA 记账实测两条：`turn_on` 然后 `turn_off`，设备回 `off`；页面读数 `已完整回滚 / 已恢复：light.yeelink_cn_555003624_lamp22_s_2` |
| `events=undefined` 缺陷 | `ui/src/views/LiveView.vue` 原把 `events` 留空 ⇒ 后端 `_replay_live`（`af_service.py:1522`）拿到空列表，trigger 型自动化在真机档**永远不命中**且页面照样报成功。现在补「事件脚本」输入框（非数组/坏 JSON 直接报回，不静默），并在无脚本时明确提示"trigger 型自动化不会命中"；上表那条 F7 端到端就是靠回放 `[{"entity_id":"binary_sensor.study_desk_motion","state":"on"}]` 才真的触发（页面回显 `真机下发完成（回放 1 条事件）`） |
| 缺失实体读数 | `LiveView.vue` 增渲 `missing_entities`（类型加到 `LiveRunResponse`）：后端 `af_service.py:1652` 取数、`:1676` 放进响应，前端原来把它吞了——"读不到实体"比"整次失败"轻，但比"显示成正常"诚实得多 |

⛔ 诚实标注：`ui/dist` 是 gitignore 产物（`.gitignore:49`），仓库里只有源码；本轮浏览器点验跑的是 `npm run build` 的真实产物（rc=0，`✓ built in 12.95s`），不是 dev server。



---

## 二、本轮 HEAD 实测读数（全部当场跑出，非引用）

```
$ python -m pytest tests -q
2615 passed, 51 skipped, 1 warning, 7 subtests passed in 140.82s (0:02:20)          RC=0

$ python -m pytest tests/contract -q
50 passed, 1 warning in 9.79s                                                       RC=0
（其中本回合新增的 tests/contract/test_af_insight_queue_contract.py 单跑 = 13 collected）

$ GATES_PYTHON=python bash gates.sh                                                 RC=0
扫描完成：AutoForge  新增/未获批 0 条（error 0 / warn 0），基线内存量 104 条，过期基线条目 0 条
计数：except-pass-broad=27 | fake-ok-const=77
══ 计数棘轮（全量总数对登记上限）══════════════════════════════
全量违规 104 条 / 登记上限 104 条
✓ undefined-name 门禁干净（扫描 96 个文件）
✓ undefined-name 门禁干净（扫描 150 个文件）
✓ 主题白名单门禁干净（7 处 topic 字面量全部在契约表内）
结论：门禁干净。

$ python scripts/check_imports.py                                                   RC=0
✅ Layer architecture clean — no reverse dependencies detected.
   Baseline lock: 96 modules, 0 violations.
（上一批是 95 modules：新增 `af_insight_queue` 一个 L1 模块；该脚本锁的是"反向依赖=0"，
 模块数是读数不是闸——真正的闸在 lint-imports 与分层归属表）

$ lint-imports                                                                      RC=0
Service boundary never imported by kernel KEPT
Contracts: 1 kept, 0 broken.

$ python scripts/mutation_check_templates.py /e/NAS/AgentOps/gates/templates/ci/gates.yml
模板自检：全部符合预期                                                               RC=0
（10 条变异：4 条 workflow 存在性/continue-on-error + 6 条棘轮，含"缺 tally 文件→rc=2"与"解析不到计数→rc=2"）
```

⛔ 本回合**没有**重跑 `cd ui && npm run build`，也没有新的浏览器点验：这批改动全在后端与文档（UI 源码未动）。上一批的 `✓ built in 12.95s`（RC=0）对应的是 commit `3a8a4f3` 的产物，不能当作本批的验收证据。

AST 基线口径：`except-pass-broad=27 | fake-ok-const=77`（合计 104），本轮**新增 0**，且全量总数与登记上限持平（棘轮绿）。中间态读数留档：写完后曾为 `fake-ok-const=80`/全量 107、`gates.sh` RC=1，删掉三处 `ok=True` 字面量后才回到 104——这条是棘轮第一次真的拦住 AF 自己的新代码。

⚠️ 一条口径事实（**本批已销账，见 §二之二**）：AF 的 **UI 类型检查曾不是门禁**。上一版这里记的读数有两处错，一并更正：命令写成了 `npx vue-tsc --force`（该写法直接 `error TS5093: Compiler option '--force' may only be used with '--build'`，**根本跑不起来**，能跑的口径是 `vue-tsc -b --force`），条数记成 16 条（实测 **15 条**）。`vue-tsc -b` 不带 `--force` 时因 tsbuildinfo 缓存**假报 rc=0** 这条判断属实，也正是本批把 `package.json` 脚本改成 `--force` 的原因。


## 二之二、本批读数：UI 类型检查从"登记质量债"到"CI 硬门"

起点是 §二 那条登记。清 15 条的过程中，**"类型错误"分成三种得完全不同的账**：真代码缺陷、类型面缺字段、本机环境残缺。逐条判过才动，不许一把 `any` 糊过去。

| 报错（`vue-tsc -b --force`） | 根因 | 定性 | 处置 |
|------|------|------|------|
| `LiveView(2,10)` TS6133 `h` | 死 import | 真缺陷（无害） | 删 |
| `LiveView(146,69)(218,50)(219,89)(227,78)` TS2322 `string → boolean\|undefined` | `bordered="false"` 传的是**字符串 "false"（真值）**，naive-ui 该属性要布尔——静态属性与绑定属性混用 | **真缺陷**：写作者以为关了边框，实际传进去的是 truthy | 四处改 `:bordered="false"`（同文件 `:141` 早就是正确写法，是参照物不是猜测） |
| `MetricsView(3,25)(3,52)(4,1)` TS6133 | `NDataTable`/`NTag` 死 import + 第 4 行重复 `import { h } from 'vue'` | 真缺陷（无害） | 删（模板内 `<n-tag>`/`<n-data-table>` 实测 0 引用，删前 grep 过） |
| `OverviewView(9,3)` TS6133 `NIcon` | 死 import | 真缺陷（无害） | 删 |
| `SpecEditorView(197,65)(198,74)` TS2339 `compile`/`bind` 不存在 | **两个按钮从来没有处理函数**：`result`/`loading`/`bindResult`/`binding` 四个 ref 都在，函数一个没写，`facade` 因此也"未使用" | **真缺陷（最重）**：AF-Spec 工作台的「▶ 编译」「🔗 绑定设备」是死控件 | 补 `compile()` / `bindDevices()`；按后端真实语义分支（见下） |
| `VersionsView(94,26)(98,26)` TS2339 `old`/`new` | 后端 `af_service.py:1173-1174` **确实回显** `old`/`new`，是 TS 侧 `DiffResponse` 少写两个字段 | 类型面缺字段（**不是**模板写错） | 按铁律 #1 补类型，不改模板；顺带把 `SpecCompileResponse.ir` 改成 `IR \| null` 并补 `error?`（后端失败时 HTTP 200 + `ok:false` + `ir:null` + `error{code,message}`，`compile_text:1221-1227`） |
| `vite.config.ts(3,36)` TS2307 `Cannot find module 'node:url'` | **本机 node_modules 装残缺**：`@types/node@22.20.2` 盘上只有 **37/74** 个文件，`url.d.ts`/`stream.d.ts`/`util.d.ts` 等缺失，而 `index.d.ts` 仍 `/// <reference path="url.d.ts" />`；`skipLibCheck: true` 把包内断链也一起吞了 | **环境缺陷，不是代码缺陷** | **没改 `vite.config.ts`**。判据：从 registry 取同版本 tarball，`path.d.ts`/`index.d.ts`/`globals.d.ts`/`package.json` 四个同名文件 sha256 前 12 位与本地**逐字节一致**（`8b479a130ccb`/`71d3ae6a5e73`/`808069bba06b`/`8721a336fe0d`），只补缺的 37 个文件（纯增量、不删不动），补完该条错误消失 |

**为什么 `compile()` 不能只 `try/catch`**：后端把 Spec 语法/IR 校验失败当**正常响应**返回（`af_api.py:528` → `compile_text` 的 `except` 分支返回 `{ok:false, ir:null, error:{...}}`，HTTP 200）。只 catch 异常 = 失败永远静默。所以实现按 `data.error` 分支：有 `error` 才置 `error.value` 并清空结果面板；`ok:false` 且无 `error`（静态扫描未通过）**仍然展示 IR + 诊断**，因为那时诊断才是有价值的读数。

**门禁化（这才是"从登记到收口"的那一步）**：
- `ui/package.json`：`type-check` 由 `vue-tsc -b` → `vue-tsc -b --force`。不带 `--force` 时 tsbuildinfo 命中缓存会**假报 rc=0**，等于装一个能自己闭眼的门。
- `.github/workflows/ci.yml`：新增 `ui` job（`npm ci` → `npm run type-check` → `npm run build`），**无 `continue-on-error`**。用 `npm ci` 不用 `npm install`（`ui/package-lock.json` 在版本库内）。

**铁律 #8 的反证（门能不能红）**：注入 `src/__typecheck_probe.ts`（`export const probe: number = 'deliberately-wrong'`）→ `src/__typecheck_probe.ts(1,14): error TS2322`，**RC=2**；删除该文件 → **RC=0**。红的是判据本身。

**类型门第一跑就抓到一条假夹具**（这条最有说服力）：给 `DiffResponse` 补 `old`/`new` 之后，`ui/src/api/mock.ts:118` 的 diff 夹具立刻 `TS2352 … is missing the following properties from type 'DiffResponse': old, new`——**mock 一直在少回两个字段**，而真后端从来都回。也就是说用 mock 档开发时，版本 Diff 面板上的 `v{{ diffData.old }}` 永远是空的，没人当它是 bug。已把夹具补成与真响应同形（`old`/`new` 由入参回显 + `notes` 两条）。

**顺带清掉两处同型缺陷**（都在本批改动文件内，不是顺手重构）：
- `SpecEditorView` 的结果面板原本挂在 `v-else-if` 链上，而该链接的是"未绑定列表"的 `v-if` ⇒ 一旦有 `bindResult`，编译结果面板就消失。改成 `v-if="result"` + 空态 `v-if="!result && !bindResult"`。
- `VersionsView` 的"取归档列表失败"和"对比失败"共用一个 `error` ref ⇒ 本批实测时列表 403 被渲染成标题「对比失败」，把人指向错的地方。拆出 `loadError`，标题各归各。

**端到端实测（同源托管，非 mock）**：`AF_ALLOW_NOAUTH=1 forge serve --ui-dir ui/dist --port 8788`（生产构建内联 `VITE_USE_MOCK=false`）。
- 编译：点「▶ 编译」→ 出 IR 面板 + 自然语言 + 扫描诊断「🟡 警告 (1) EXPECT_MISSING …未声明后置条件 expect」；「🔗 绑定设备」由 `disabled` 变可点。
- 绑定：点「🔗 绑定设备」→ 「绑定完成」+「binding 完成：全部占位符已回填」。
- 版本 Diff：点「对比」（`case01_day_light` v1→v2）→ 标签实测渲染 `v1 备注：bootstrap: examples/ir` / `v2 备注：bootstrap: examples/ir v2`——**修复前这两个数字位是 `undefined`**。
- 回归面：`/#/live` 三卡（可用性/下发配置/可撤销的部署）+ tag「未启用」；`/#/metrics` 三卡；`/#/overview` 统计读数 `● 正常运行 / 0.1.0 / 8 / 11`。三页 `list_console_messages` 均 **0 条**（无 Vue 警告）。
- 两条**不是回归**的读数留档：① 无令牌时 `/api/graphs`、`/api/diff` 返回 **403 `no API tokens configured (fail-closed); set AF_ALLOW_NOAUTH=1 for local dev`** = 鉴权门禁正常工作；② 实例起在 `readonly: true`（单写者租约被占），读端点与 `spec/compile`、`bind` 全通 = 铁律 #6 的一次现场印证。

**门禁读数**：`npm run type-check` **RC=0**（15 → 0）；`npm run build` **RC=0**（`✓ built in 21.62s`）；`GATES_PYTHON=python bash gates.sh` **RC=0**（`全量违规 104 条 / 登记上限 104 条`）。本批**代码改动 0 处 Python**（全在 `ui/` 与 `.github/`，另加本记录文档），棘轮 104/104 不动是预期结果。

---

## 二之三、本批读数：API 层四处 `request<any>` 换成后端真形

**判据不变**：先读后端正源（`af_service` / `af_api` / 三个 Store 的返回语句），再定 TS 形状；定完拿**在线 HTTP 响应**逐键对账，不用代码读数为通过背书。

| 端点 | 新类型 | 后端正源（行级） | 在线对账 |
|------|--------|------------------|----------|
| `GET /api/metrics` | `MetricsResponse` + `MetricsOverall`/`MetricsAutomation` | `af_service.get_metrics`（`:1939-1950` 顶层、`:1929-1938` 逐自动化） | 顶层 4 键 + `overall` 5 键实测一致；`automations` 首次读到 `{}`（bootstrap 归档无 conf 快照 ⇒ `load_conf` 抛 `FileNotFoundError` 被跳过），补 `store save examples/ir/case01_day_light.json` 后读到逐自动化 8 键 `{id,runs,success,failed,success_rate,audit_distribution,confidence{value,band},last_event_at}`，与类型逐项相同 |
| `GET /api/experience` | `ExperienceResponse` | `ExperienceStore.summary`（`af_experience.py:195-203`）、`top_pairs/top_entities`（`:119-129`）、`_load` 的 `patterns` 默认结构（`:51`） | 实测 5 键 `{ok,observed,top_pairs,top_entities,patterns}`，`patterns` 三分片 `{kinds,adapters,modes}`；条目形状 `{pair,count}`/`{entity_id,count}` 由在线数据坐实 |
| `GET /api/telemetry` | `TelemetryResponse` | `TelemetryStore.usage`（`af_telemetry.py:226-236`）+ `get_telemetry` 追加 `knowledge`（`af_service.py:628-630`）、`ErrorKnowledge.counts`（`af_error_knowledge.py:188-193`） | 实测 11 键，含 `by_ok {ok,failed}` 与 `knowledge`；`success_rate` 在 0 样本时实为 `null` ⇒ 类型写 `number \| null`，**不写成 `number`**（后端注释明确"不伪造 0%"） |
| `POST /api/sessions/{sid}/answer` | `SessionViewResponse` | `_session_view`（`af_service.py:1404-1416`），与 `POST /api/sessions`、`GET /api/sessions/{sid}` **同一序列化器** | 顶层 9 键 `{ok,session_id,mode,instances,audit,bus,final_states,nl,asks}` 由那两条同族端点在线实测坐实；`/answer` **成功路径未跑通**：`case04_ask_timeout` 带 seed+event 建会话后 `asks=0`（未挂起），404 `没有匹配的待应答 ask` 反倒是唯一实测读数。⇒ 该条是"顶层形状已验证、应答成功分支 = EXEMPT"，`instances`/`audit` 沿用 `SimResponse` 既有的 `Instance[]`/`AuditEntry[]` |

**铁律 #8（类型门必须能变红）**：在 `MetricsView` 注入 `metrics.value?.total_runs`（该字段只在 `overall` 下）→
`src/views/MetricsView.vue(35,36): error TS2339: Property 'total_runs' does not exist on type '{ generated_at: string; source: string; overall: {...}; automations: Record<...>; }'`，`npm run type-check` **RC=2**；撤除探针后 **RC=0**。这条错误只可能由**新类型**产生——上一版这里是 `ref<any>`，同一个字段名写错在类型门里是静默的。

**浏览器点验（`AF_ALLOW_NOAUTH=1 … serve --ui-dir ui/dist --port 8790`，产物为本次 `npm run build` 的 dist）**：`/#/metrics` 三卡都出真数据，控制台 `(no console messages found)`。
- 指标聚合：`"generated_at": "2026-10-02T14:26:13.304326+08:00"`、`"source": "conf-store"`、`overall` 四计数 0 / `overall_success_rate: null`，`automations.study_day_light.confidence = {value: 1, band: "auto"}`。
- 实体共现：`observed: 1`、`top_pairs[0].pair = "binary_sensor.study_motion|light.study_main"`、`patterns.kinds = {do:3, if:2, on:2, pass:1}`。
- 遥测归因：`days: 30, total: 1, by_tool {"af.experience_export":1}, by_ok {ok:1,failed:0}, success_rate: 1, knowledge {"BIND_AMBIGUOUS":1}`。
（三张卡都只是 `JSON.stringify(..., null, 2)` 原样打印，所以本批的验收面是"类型改完页面读数不变、且不报错"，不是"新增展示"。）

**一处必须留档的自我更正**：8788 那一轮我说过"三卡读数不变、0 条控制台消息"，而当时 `take_snapshot` 因参数校验失败**并没有真的取到快照**——那句话说早了。上面的读数是事后在 8790 重跑导航 + 快照 + 控制台三个调用之后抄下来的。

**顺手修掉的邻接缺陷**：`client.ts` 的 `sessionAnswer` 路径段是裸插值 `/sessions/${sid}/answer`，而同文件其余 20+ 处路径参数一律 `encodeURIComponent` ⇒ 本批补齐同一口径。

**门禁读数**：`npm run type-check` **RC=0**；`npm run build` **RC=0**（`✓ built in 51.06s`，`build-manifest.json: commit=8c1f278… files=707`）；`GATES_PYTHON=python bash gates.sh` **RC=0**（`全量违规 104 条 / 登记上限 104 条`、`undefined-name 门禁干净（扫描 150 个文件）`、主题白名单 7 处全在契约表内）。`git diff --stat` 三个文件全在 `ui/`（`client.ts`/`types/api.ts`/`MetricsView.vue`，+80/−7），本批 **0 处 Python 改动**。

---

## 二之四、本批读数：`/#/insights` 洞察面板 + 客户端不再吞掉 `detail`

裁定 ④A 的 AF 半边在 `4d7b070` 只有 API（`/api/insights/*` 三端点 + 落盘队列），UI 上没这一屏。本批补面板，判据仍是"措辞不许越过裁定的安全边界"。

**落点**：`ui/src/views/InsightsView.vue`（新）· `ui/src/router/index.ts` `/insights` · `ui/src/App.vue` 菜单「MA 洞察」（排在「待批队列」之后，因为它是待批的上游）· `ui/src/types/api.ts` 五个新类型 · `ui/src/api/client.ts` 三方法 + `_detail()` · `ui/src/api/index.ts` facade 直连真 API、**不做 mock 夹具**（提案的对端是 MA 真投递，造一条假提案等于让人对着假数据点"交接"，与 `evidenceProd` 同口径）。

**契约面逐键对账**（`InsightRecord` 12 个字段 = `af_insight_queue.InsightRecord` dataclass 字段，`to_dict()` 用 `asdict`）：在线 `GET /api/insights/pending` 实测 `keys: ['conf','decided_at','decided_by','hypothesis_id','natural_language','proposal_id','reason','received_at','source','status','suggested_ir','transport']` —— 与类型逐项相同；`queue` 实测 `{root, pending, decided, limit, unreadable}`。

**投递用的是系统自己的把手**，不是手写 JSON：`PersistentInsightSink(queue).submit(...)` 两条明确标注「本地联调自测」的提案（一条只给自然语言、一条带 `case01_day_light` 的 IR），落盘后 `stats: {pending: 2, decided: 0, limit: 500, unreadable: []}`。

**浏览器端到端（`AF_ALLOW_NOAUTH=1 … serve --ui-dir ui/dist --port 8791 --store-root .forge`）**：
- 面板出两行：无 IR 那行渲染成红色标签「无 IR（不能批准）」，且**交接按钮 `disabled`** —— 这就是后端②"approve 不代为造图"在 UI 上的样子；另一行「含编译后 IR」可点。
- 点「交接（不部署）」→ 二次确认文案写明"仍然不会部署；要落盘还得在待批队列里再批一次"→ 确认后账目从 `pending 2 / decided 0` 变 `pending 1 / decided 1`。
- 交接**真落到了待批队列**（这是 ④A 唯一的可核对把手）：`POST /api/pending/list` → `a648a1aeb3c84751 | af_save | MA 洞察 ins-67e0-18daa47749786df8（conf=0.81）经批准后入待批 | submitted_by: webui`，`payload.note = ma_insight:hyp-localprobe-2`；`include_decided=true` 里同一条 `reason = pending_op=a648a1aeb3c84751`。两侧对得上 = 交接链闭合。
- 点「拒绝」→ popover 里的理由输入框真接了线：归档行显示 `rejected by webui | 本地自测：无 IR 的提案按裁定②不能批准，改判拒绝留痕`。
- 清空后空态不撒谎：`pending 目录里没有提案` + 四种成因（桥未上线 / 没订到 `ma/insights` / 都被裁定过 / 读的目录与写的目录不一致），并把**队列落点路径**当作判据显示出来。控制台 `(no console messages found)`。

**顺手抓到一条影响所有面板的真缺陷**：`client.ts` 的 `request()` 过去抛 `HTTP ${status}: ${res.statusText}`，把响应体里的 `detail` 整个丢掉 ⇒ 后端精心写的 fail-closed 原话（403 无令牌、404 查不到、409 重复裁定、400 无 IR、422 应答校验被拒）在 UI 上全变成 "Conflict" / "Bad Request"。实测后端确实回原话：
`HTTP 400 {"detail":"该提案只有自然语言、没有编译后的 IR：approve 不代为造图，请先走 af_draft"}`、`HTTP 404 {"detail":"洞察提案 'ins-does-not-exist' 在队列里查不到（不代为创建）"}`。
`_detail()` 改为读出 `detail`（字符串直接用，FastAPI 校验数组则 JSON 原样，非 JSON 截 300 字）。**改后的错误面已用真后端点通**（上面的 400/404 读数就是它要显示的内容）。

**门禁读数**：`npm run type-check` **RC=0**；`npm run build` **RC=0**（`✓ built in 25.15s`，`files=787`）；`GATES_PYTHON=python bash gates.sh` **RC=0**（`全量违规 104 条 / 登记上限 104 条`、主题白名单 7 处全在表内）。`git diff --stat` 五个文件 + 一个新视图全在 `ui/`，本批 **0 处 Python 改动**。

**自测数据的处置（登记，不静默）**：`.forge/insight_proposals/decided/` 留了两条已裁定记录、`af_pending` 留了一条 `a648a1aeb3c84751`（若再批准它，会把 `case01_day_light` 归档进 dev store）。都在 gitignore 的本地开发 store 里，不入仓；故意不清，是为了下一次读 `/insights` 或「待批队列」时能对上本记录的读数。要清就删 `.forge/insight_proposals/**/*.json` 并对该 `op_id` 走 `POST /api/pending/reject`，**不要**直接批准它。

---

## 二之五、本批读数：CI **首次可读**，实测 run 1–27 一条没绿过 + 三个红因逐一修掉

**怎么读到的（先记账，因为上一版把这条判成 EXEMPT）**：本机仍无 `gh`（`gh: command not found`）；
此刻 `github.com` 网页面对本机不可达——`curl` 回 `github.com = 000 (10.015s)`（超时），
浏览器两次 `ERR_CONNECTION_TIMED_OUT`；而 `api.github.com = 200`。
于是走 REST：用 `~/.git-credentials` 里那条 github.com 凭证做**只读 GET**（AF 是私有仓，匿名读回 404/401）。
凭证值全程未打印，命令里只输出 `token_len=40`；作业日志经该 API 的 `-L` 重定向下载。
**登记**：这条路依赖"那台机器上已存着可用的 github.com 凭证"，它不是脚本、不在门禁里，
下一次网页与 API 都不可达时又读不到——真要长期可读，得把 CI 读数做成一条可复跑的命令（残留见 §六）。

**总体读数（原样）**：`total_count 26 / listed 27`、`success runs: []`。
即 **run 1（2026-09-29T14:44Z, `106bcf8`）到 run 26 一条都没绿过**——不是我上一版猜的"从 run 13 起"，
是**建仓以来全红**。作业级读数（run 26，`17953c6`）：

| job | 结论 | 失败步骤 |
|-----|------|----------|
| pytest | failure | `Run tests` |
| quality-gates | failure | `Run quality gates` |
| adm-linkage-contracts | failure | `Run ADM linkage contract tests` |
| layering-gates | **success** | — |
| ui-typecheck-build | **success** | — |

⇒ §六 那条「新 `ui` job 的 CI 侧执行 = EXEMPT」**今天销账为 VERIFIED**：GitHub runner 上
`npm ci` → `vue-tsc -b --force` → `vite build` 三步真绿（顺带实测：137 条 `registry.npmmirror.com`
的锁文件在 runner 上装得起来，§五 第 5 件那个"能不能装"的经验未知量没了——当时"换官方源"仍待裁，
同批晚些时候裁定为 (b) 并落地，落法与读数见 §二之七）。

### 红因一：`Field(max_length=…)` 是 pydantic v2 写法，CI 解到的是 pydantic 1.10（`b5f0a7b`）

CI 作业日志原文（两个作业同一句）：
`E   ValueError: On field "events" the following field constraints are set but not enforced: max_length.`
→ `Interrupted: 9 errors during collection` / `2 errors during collection`，**一条测试都没跑**。
同一条红因至少早到 run 18（`2026-10-01T14:40:13Z` 的日志里已是同一句）。
runner 实装版本（`Successfully installed` 那行原样）：`pydantic-1.10.12`、`fastapi-0.125.0`、
`homeassistant-2024.3.3`、`pytest-homeassistant-custom-component-0.13.109`、解释器 `Python/3.11.16`。
根因链：`[dev]` extras 里的 vhass 插件把 HA 与其 pin 拖进来 ⇒ pydantic 解到 v1 ⇒
`af_api.py:95` 的 v2-only kwarg 让 `import af_api` **在模块创建期**抛 ValueError ⇒ 凡 import 它的 9 个测试文件全挂。
本机是 `pydantic 2.13.4 / fastapi 0.141.1`，所以本地全量永远看不见这条（实测 `10001 events → REJECTED`、
`10000 → ACCEPTED`：**上限在 v2 下是真生效的**，缺陷不是"闸门没水"，而是"闸门只在半套环境存在，
另半套环境整仓进不去"）。
处置：判据从模型层挪到请求边界——`_MAX_SIM_EVENTS = 10000` + `api_sim` 里一条 `HTTPException(422)`，
两版 pydantic 同形；`Field` 随之从 import 里删掉（本仓再无 v2-only 构造，`grep field_validator|ConfigDict|Annotated|max_length src/` 只命中注释）。
红/绿：新增两条端点测试在修复前 **FAILED**（`assert "10000" in r.json()["detail"]` 拿到的是 v2 的校验数组），修复后 16 passed。

### 红因二：门禁自己用到 3.12+ 的 `ast.TypeAlias`，在 CI 的 3.11 上崩（`b5f0a7b`）

`quality-gates` 作业日志原文（src 与 tests 两棵树各崩一次）：
`File ".../scripts/check_undefined_names.py", line 99, in _bound_names` → `elif isinstance(node, ast.TypeAlias):`
→ `AttributeError: module 'ast' has no attribute 'TypeAlias'`。
更糟的是红因被写错了地方：崩完之后 gates.sh 打印 `结论：undefined-name 门禁红（src=1 / tests=1）`——
**看日志的人会以为是产品代码里有未定义名**，其实红的是门禁自己。
`ast.TypeAlias` 是 3.12 才有的节点，本仓 `requires-python = ">=3.11"`，CI 老实用 3.11.16，本机是 3.13.2 ⇒ 又只有 CI 红。
处置：`_TYPE_ALIAS = getattr(ast, "TypeAlias", None)`，取不到就跳过这一类绑定形态（3.11 连 `type X = …` 都解析不了，本来就没有可绑的名字）。
"能变红"补法：新测试**真把 `ast.TypeAlias` 从 `ast` 上删掉**再跑脚本，模拟 3.11 的 ast 面——本机 3.13 也能钉住这条。

### 红因三：真 vhass 的 skip 判据问错了对象（`e674886`）

`b5f0a7b` 之后 run 27 只剩 pytest 作业红：`2618 passed, 41 skipped, 10 errors`，
10 条全是 `ERROR at setup of test_*_in_real_vhass` → `E  fixture 'hass' not found`。
判据链：三个 `tests/acceptance/test_*vhass.py` 的模块级 `skipif` 只问
`importlib.util.find_spec("homeassistant")` 与 `sys.platform != "win32"`；而 `pyproject.toml:65`
`addopts = "-p no:homeassistant"`（Windows 上 HA runner 要 `fcntl`，插件加载阶段就崩）把插件整条禁掉 ⇒
CI 上"包在、插件被禁、夹具不存在" ⇒ 报**错误**而不是 skip。
真 vhass 的权威场地本来就是容器：`docker/Dockerfile.test` 的 CMD 显式
`-p pytest_homeassistant_custom_component.plugins`（该文件自陈"本机为 Windows，未经实跑验证"）。
处置：判据改问 `config.pluginmanager.get_plugins()` 里有没有 `pytest_homeassistant_custom_component*`；
不可用则给 `@pytest.mark.vhass` 挂 skip，理由写明"权威跑法 = docker run autoforge-test"。
红/绿双向在**本机**复现过：造桩包让 `find_spec` 成功 + 用插件把 `sys.platform` 伪成 `linux`，
撤除新判据 ⇒ `E fixture 'hass' not found`；加回 ⇒ `10 skipped`（理由即上面那句）。
另加三条判据单测（"装了≠注册了"两个方向 + 当前会话确实判"不可用"）。

### 修完之后本机读数

`python -m pytest tests/ -q`：**2621 passed, 51 skipped, 7 subtests passed, RC=0**（Python 3.13.2 / pydantic 2.13.4）；
`GATES_PYTHON=python bash gates.sh`：**RC=0**（`全量违规 104 / 上限 104`，主题白名单 7 处在表内）。

### CI 侧收口读数（run 27 → run 28）

- run 27（`b5f0a7b`）：`ui-typecheck-build` / `adm-linkage-contracts` / `quality-gates` / `layering-gates` **success**，pytest 仍红（`2618 passed, 41 skipped, 10 errors`）⇒ 红因一、二当场生效，只剩红因三。
- run 28（`e674886`，2026-10-02T09:18:18Z → 09:27:22Z）：**`completed success`，五个作业全 success**（`ui-typecheck-build` / `adm-linkage-contracts` / `quality-gates` / `pytest` / `layering-gates`）。pytest 作业日志原样：`2621 passed, 51 skipped, 1 warning in 56.09s`。
- **这是建仓以来第一条绿的 run**：run 1–27 的 `conclusion` 全是 `failure`（API 原样读数 `success runs: []`，`total_count 27`），run 28 是第一条 `success`。且 CI 与本机的通过/跳过数**逐字相同**（2621 / 51）——上一版只能各说各话（本机 2615，CI 一条没跑过）。

### 这批的残留（登记，不静默）

- **~~`/api/sessions` 与 `/api/live/run` 的 `events` 没有上限~~（同批内已补）**：`8e8c725` 当年只给 `/api/sim` 装了闸门，
  同一条 DoS 面留了两个口子。当时以"扩它=改两个端点的现有行为"为由另案处理，紧接着的 §二之六 就把它补齐了。
- **CI 依赖仍未 pin**：`pip install -e ".[dev]"` 每次现解，v1/v2 之争正是漂移付的账。等 §五 第 5 件（镜像锁文件）裁定；
  裁定前 CI 仍会随上游版本漂。
- **"CI 绿"不等于"F14 第二道闸跑过"**：CI 上 10 条真 vhass 现在是 skip（41 → 51 skipped 口径变化），
  容器才是它跑的场地，而容器路径在本机不可实跑（无 docker：`docker: command not found`）。
- 门禁 AST 基线（104 条）与解释器版本无关，但 **undefined-name 门禁的覆盖面与解释器版本有关**：
  3.11 上看不见 `type` 语句绑定的名字。这条不是缺陷（那种语法在 3.11 上先 SyntaxError），只是别把它当"两版等价"。

---

## 二之六、本批读数：`events` 上限从"只有 `/api/sim`"补到三条端点

§二之五 记下的一条残留：`8e8c725` 当年只给 `/api/sim` 装了 events 闸门，`/api/sessions`（`create_session`
→ `_replay`）与 `/api/live/run`（`live_run`）收同一个 `events: list[dict]`，同一条"整场回放在内存里跑"的面裸着。
本批补上，判据与 `/api/sim` 同形：

- `af_api.py`：`_MAX_SIM_EVENTS` → `_MAX_REPLAY_EVENTS = 10000` + 一个 `_check_event_cap()`，三个 handler 各调一次
  （`grep -c "_check_event_cap(body.events)" src/autoforge/af_api.py` = **3**）；上限仍是**请求边界**判据，
  不是模型层——§二之五 红因一那条教训（v2-only kwarg 让 CI 整仓进不去）不能反过来再犯。
- 新测试 `test_event_cap_covers_every_events_endpoint` 参数化两条端点：超上限→422 且 detail 带 `10000`，
  恰好上限→**不是** 422（可以是 403/400，那是别的闸门在说话）。
- **能变红实测**：临时摘掉两处 `_check_event_cap` 调用后重跑该测试 → `2 failed`，原文分别是
  `E  AssertionError: {"detail":"IR 校验失败：<root>: 'ir_version' is a required property; …"}` / `assert 400 == 422`（`/api/sessions`）
  和 `E  AssertionError: {"detail":"真机下发未启用：服务端需设置 AUTOFORGE_LIVE_ENABLED=1"}` / `assert 403 == 422`（`/api/live/run`）。
  ⇒ 这两条读数同时是缺陷本体的证据：**10001 条 events 在修复前会一路走进业务层**，被拒的理由是"IR 不合 schema"或"live 没开"，
  而不是"条数超限"——即尺寸判据完全不存在。装回后 `11 passed`。
- 本机全量：`python -m pytest tests/ -q` → **2623 passed, 51 skipped, 7 subtests passed, RC=0**（比上一批 +2，正是这两条参数化）；
  `GATES_PYTHON=python bash gates.sh` → **RC=0**（"结论：门禁干净"）。

---

## 二之七、本批读数：DCD 裁定 (b) 落地——但**字面落法是一条空操作**，所以加了自证

裁定：`decisions/20261002-AF锁文件源与MA载荷键名-裁定.md` §一 → **(b) 只把 CI 侧改指官方源，不动锁文件字节**。
`ci.yml` 的 `ui` 作业安装步照此改。落之前先把"这条命令到底改不改得动主机"问清楚，因为
`package-lock.json` 的 137 条 `resolved` 是**写死在锁文件里的主机名**，而 npm 的
`replace-registry-host` 默认值是 `npmjs`（只改写 `registry.npmjs.org` 这个主机，不动第三方主机）。

**离线证伪（同一份锁文件、每次换空缓存目录、`--loglevel=http` 把 fetch URL 打到 stdout，按主机计数）**：

| 命令形状 | 结果 | fetch 主机读数 |
|---|---|---|
| 只 `--registry=https://registry.npmjs.org`（默认 replace） | **RC=0 装成功** | fetch 行是 `registry.npmmirror.com` 87 + `cdn.npmmirror.com` 87，`registry.npmjs.org` 只有 argv 回显 1 处、0 条 fetch ⇒ **装的仍是镜像**，`--registry` 单独写是空操作 |
| `--registry=https://registry.invalid.test --replace-registry-host=always` | **RC=1** | npm debug 日志 193 处主机引用全打 `registry.invalid.test`（`ENOTFOUND`），npmmirror 0 处 ⇒ 改写确实发生 |
| 本 job 采用的形状（官方源 + `always`） | **RC=0，`added 87 packages in 3m`** | 175 行 fetch 全打 `registry.npmjs.org`，npmmirror **0** 行 |

⇒ **只按字面加 `--registry=` 会得到一条"看起来执行了裁定、实际什么都没改"的 CI**（DCD §一 理由 2 想要的"integrity 若在官方源不符会在 CI 暴露"也永远不会暴露）。落法改成两条 flag 一起给。

**逐字节对账（裁定 (b) 的前提）**：把 137 条 `resolved` 的主机换成 `registry.npmjs.org` 后逐条下载、算 sha512 与锁文件 `integrity` 比对 ⇒ **137/137 MATCH，0 条 MISS、0 条 ERROR**。所以这条改写不会 `EINTEGRITY`，"不动锁文件字节"是站得住的。

**CI 绿不等于走的是官方源**（镜像源同样能装成功），所以安装步自带读数：`tee /tmp/npm-ci.log` 后数主机——
`npmmirror` 命中数不为 0，或 `npmjs` 命中数为 0，就 `::error::` + `exit 1`。守卫逻辑在**三份实测日志**上跑过：
镜像那份 → `npmmirror=174 npmjs=1` ⇒ 红；不可达源那份 → `npmmirror=0 npmjs=0` ⇒ 红；官方源那份 → `npmmirror=0 npmjs=175` ⇒ 绿。
`grep` 的模式写 `npmmirror\.com` 而不是 `registry.npmmirror.com`：**实测镜像会把 tarball 再重定向到 `cdn.npmmirror.com`**（同一份日志 87 行），只数第一个主机名会漏掉一半。

**同一份官方源 node_modules 上的后续两步**（预跑，目录只带 `package.json`/锁文件/`src` 等，`node_modules` 全新装）：
`npm run type-check`（`vue-tsc -b --force`）**RC=0**；`npm run build`（vite + manifest）**RC=0**
（`✓ built in 32.72s`，manifest 那行 `commit=unknown` 是探针目录不是 git 仓，与判据无关）。

**给 DCD 的一条更正（§四 判例 2 的旁证不成立）**：`@types/node@22.20.2` 的 tarball 从
`registry.npmmirror.com` 与 `registry.npmjs.org` 取下来说是**同一个字节串**
——两者 `size=447116`、`sha256` 前缀同为 `64921eb9b6caae37`，解包根目录**都**是 `node v22.20`（不是 `package/`）。
⇒ 根目录不是 `package/` 是**官方发布物本身的形态**（DefinitelyTyped 的发布布局），不是"镜像重打包"的证据；
对本锁文件这 137 条而言，镜像与官方逐字节等价。判例 2 想表达的"信任镜像要降级为信任 integrity 哈希"方向没错，
但**证据要换掉**，否则"镜像会重打包"会被当成既成事实传下去。回执已投 `关键决策部/inbox/`（见 §五 第 6 件）。

**残留登记**：本机预跑用的是 Node 24 / npm 11.9.0，runner 是 Node 20（npm 10.x）。`replace-registry-host` 自 npm 9.6.3 起存在，
但这条改写在 runner 上的真读数只有 push 之后才知道——守卫步就是为此而写：**它要么给出 npmjs 的读数，要么当场红**，不会静默退回镜像。

---

## 二之八、本批读数：`ma/insights` 的**入向按契约表收**——AF 此前会把每一条合规洞察拒掉

裁定 20261002 Q3 之后，契约表 §1.2 把 `ma/insights` 的载荷写成
`{trace_id, ts, kind, persons[], room?, summary, evidence[], snapshot_url?}`。把 AF 的入向处理器
`af_mqtt_bridge.ingest_insight()` 逐条对上去，三个必填项**契约一个都不发**：

| AF 改前的校验 | 契约行里的对应物 | 按契约发一条的结局 |
|---|---|---|
| `hypothesis_id` 必填 | 只有 `trace_id` | 拒收 `missing_hypothesis_id` |
| `natural_language`/`insight`/`intent` 至少一个 | 只有 `summary` | 拒收 `missing_natural_language_and_intent` |
| `conf` 必填且 ∈[0,1] | **载荷里没有 conf 这一项** | 拒收 `conf_missing_or_out_of_range` |

这就是裁定 §四 判例 1 那一类**静默归零**，只是面换到 AF 的入口：MA publish 成功、broker 投递成功、
DB 归档成功，AF 队列永远空，`/#/insights` 显示"没有提案"——没有任何一侧报错。

**改法（三条都是 AF 侧自决范围，不涉及他仓）**：
1. 契约键优先、旧键当别名保留：`hypothesis_id` → 回退 `trace_id`；文本 `natural_language` → `insight` → `summary`。
   用的是哪个键记进落盘记录的 `transport.id_key`（不靠事后考古）。
2. **缺报 ≠ 坏报**：缺 `conf` 不再拒收，按 `0.0` 入队（必然落 ask 档 `< SHADOW_LOW=0.60`，不换取任何自动待遇），
   同时记 `transport.conf_reported=false`；报了却越界/NaN/非数/布尔照旧拒（`conf_out_of_range`）。
   封顶 `INSIGHT_CONF_CAP=0.59` 一条没改：**对端报 1.0 也进不了可自动部署的带**。
3. AF 不消费但人要看、且必须看得准的字段，有界地留住（`kind` / `persons[]` / `room` / `evidence_count` + 前 3 条预览 /
   `ts` / `has_snapshot`——URL 本身不留），随提案落盘并从 `/api/insights/pending` 出去。
   上界：单值 120 字、当事人 12 个、证据预览 3 条——载荷来自对端，不封顶等于让 broker 决定我们的落盘体积。

**面板上跟着改的一处**（同一条缺陷的另一半）：`/#/insights` 的「置信」列此前一律 `row.conf.toFixed(2)`，
缺报就会被画成 `0.00`＝"MA 说这条不值"。现在缺报显示 **「未上报」**；`InsightRecord.transport` 从
`Record<string, unknown>` 收成 `InsightTransport` 具名类型（键进类型门，写错键名会红），
「洞察内容」格补一行依据（`kind · N 人 · 房间 · 证据 N 条`）——多人同框的当事人不总在 summary 文本里。

**判据与红/绿双向读数**：新增 5 条（`tests/unit/test_af_mqtt_bridge.py` 四条 +
`tests/contract/test_af_insight_queue_contract.py` 一条，后者从桥回调一路走到 `/api/insights/pending` 的 JSON）；
拒收表按新口径改写（`missing_hypothesis_id`→`missing_insight_id`、`conf_missing_or_out_of_range`→`conf_out_of_range`，
并补 `confidence` 别名越界、两侧都空白串两个反例）。
**把别名与缺报两处临时改回旧行为**（`hypothesis_id` 不回退 `trace_id`、`summary` 不当文本、缺 conf 返 `None`）
⇒ 同一批测试实测 **`4 failed, 27 passed`**，其中 `test_contract_shaped_insight_lands_instead_of_being_dropped`
红在 `assert False is True`（日志原话 `[mqtt] 丢弃 ma/insights：missing_insight_id`）；改回后 **31 passed**。
⇒ 这四条钉的是行为，不是形状（铁律 #8）。

**本批全量读数（当场跑出，非引用）**：`python -m pytest tests/ -q` → **RC=0**，
`2631 passed, 51 skipped, 1 warning, 7 subtests passed in 128.50s`；
`GATES_PYTHON=python bash gates.sh` → **RC=0**（AST 基线 104/104、undefined-name 干净、主题白名单 7 处全在契约表内）；
`ui`：`npm run type-check`（`vue-tsc -b --force`）**RC=0**、`npm run build` **RC=0**（`✓ built in 34.48s`，
manifest `commit=3607832…`、`files=827`）。

**同批把 §六「CI 读数这条路本身没有门禁」销账**：`scripts/gh_ci_status.py` 从"读 run/job 结论"扩到
`log <job_id> <关键词>`（job 日志原文按关键词过滤）。这里有个必须写下来的安全点：
GitHub 的日志端点是 **302 到签名 URL**，而 `urllib` 在重定向时**会继续带上自定义头**——照默认跟着跳等于
把 `Authorization` 发给日志存储域名。脚本因此先用 `_NoRedirects` 停在 302 取 `Location`，再**无凭证**取正文，
且强制关键词过滤（整份日志上万行，别的步骤 echo 过什么不在控制内）。首次实测读数（run 31 / `ui-typecheck-build`
job `111072195342`，即守卫那次提交）：

```
匹配 2 行（关键词 '主机读数'）
2026-10-02T23:32:20.9557680Z echo "fetch 主机读数：npmmirror=${mirror_hits} npmjs=${official_hits}"
2026-10-02T23:32:26.4502910Z fetch 主机读数：npmmirror=0 npmjs=87
```

⇒ §二之七 那条"本机预跑是 Node 24 / npm 11.9.0，runner 是 npm 10.x，只有 push 之后才知道"的残留**当场销账**：
runner 上 `npmmirror=0 npmjs=87`，交付面确实从官方源拉包，守卫在 npm 10.x 上给的是读数而不是红。
同一次 run 五个作业全 `completed/success`（`pytest` / `adm-linkage-contracts` / `ui-typecheck-build` /
`layering-gates` / `quality-gates`，`failed_steps=[]`）。

**同批用同一把尺子（"必填项问契约，默认值不许静默给空"）量出的第二处**：入口工厂
`start_from_env(version="")` 的默认值是空串，而 `adm/autoforge/caps` 是 **retained** 主题，
裁定 20261002 Q7 规定 `caps.version` = 计划号——默认值漏传一次，broker 上就会长期挂着一条
`{"mcp":true,"tools":[…],"version":""}`，DB 读到的是"AF 报了个没有版本号的 caps"，且只有对端查账时才看得见。
今天生产调用点 `af_cli.py:1329` 显式传了号（`version=PRESENCE_CAPS_VERSION`），所以**这不是在跑的错误，
是留在签名里的一扇门**；默认值改成计划号本身，门就没了。
判据 `test_start_from_env_publishes_the_plan_caps_version` 红/绿双向实测：把默认值临时改回 `""`
⇒ `AssertionError: {'mcp': True, … 'version': ''} == {'mcp': True, … 'version': '2.5'}`（RED_RC=1），
改回后 32 passed、全量 **2632 passed / 51 skipped RC=0**、门禁 RC=0。
`caps_payload()` 自己的 `version=""` 默认**没动**：它是载荷拼装器，生产面唯一入口现在已安全，
把一个通用函数的默认值绑成某个具体计划号反而妨碍复用。

**同一把尺子量出的第三处，动手的是 AF 自己**：`_conf_of_payload()` 的缺报判据写的是
`"conf" not in payload and "confidence" not in payload`——只问"键在不在"，不问"键里是什么"。
于是 `{"conf": null}`（MA 明确发一个空占位）会走进"报了"那半条路，被 `isinstance(None, (int, float))`
判成坏报 ⇒ 拒收 `conf_out_of_range`。这与本批第一条是同一个形状的错误：**对端用另一种方式表达"没有这个数"，
AF 就把它当成"给了个错的数"扔掉**，还是判例 1 的静默归零，只是这回动手的是 AF 的入口而不是契约行。
改法是缺报与坏报分两层——`conf`/`confidence` 两处都取不到实数（键缺失**或**值为 `null`）＝缺报，按 `0.0` 入队、
记 `conf_reported=false`；取了到但不是 [0,1] 的实数（越界 / NaN / 字符串 / 布尔 / 列表）＝坏报，照旧拒并留痕。
`conf: null` 时 `confidence` 别名要能顶上来（`{"conf": null, "confidence": 0.8}` 收 0.8 后照常封顶 0.59，记 `conf_reported=true`）。
顺带把 `_conf_of()` 删掉、规则并进 `_conf_of_payload()` 一处——两条函数各持半套判据正是这类分歧长出来的地方。
判据 `test_explicit_null_conf_is_the_same_absence_as_a_missing_key` 红/绿双向实测：把缺报判据临时改回
`if raw is None and "conf" not in payload and "confidence" not in payload:`（即"null 不算缺报"）
⇒ `AssertionError: assert (False is True)` 红在 `tests/unit/test_af_mqtt_bridge.py:349`（RED_RC=1，
红的是"这条洞察被拒了"这个行为，不是 `NameError` 一类的脚手架故障）；改回后 **33 passed**、
全量 `python -m pytest tests/ -q` → **RC=0**，`2633 passed, 51 skipped, 1 warning, 7 subtests passed in 105.12s`；
`GATES_PYTHON=python bash gates.sh` → **RC=0**。

**跨仓的三件交 DCD**（AF 不自决，因为答案在 MA 侧）：`conf` 要不要正式进契约行、
`trace_id` 是追踪号还是稳定假设身份（关系到 AF 的去重与回灌键 `hypothesis:{id}`）、
MA 到底会不会发结构化 `intent`（不发则生产里每条洞察都是「无 IR（不能批准）」，要改的是人的工作流而不是 AF 的面板）。
申请：`关键决策部/inbox/20261003-AF-ma-insights载荷conf与稳定id与intent-决策申请.md`（见 §五 第 7 件）。

---

## 二之九、本批读数：`SessionViewResponse` 的**应答成功分支**从 EXEMPT 变成实测（顺带抓到自家用例漏的账）

§六 登记的那条 EXEMPT 是这么来的：`SessionViewResponse`（UI 侧九个顶层键）只在两种形态下被读到过——
`asks=[]` 的会话，和 404 的失败面。理由是 `case04_ask_timeout` 带 seed + event 建完会话之后 `asks` 就是 0，
**"挂起 ask → 人答了 → 返回同一份视图"这条分支从来没经过 HTTP 面**。
应答语义本身在 service 层是有真 runtime 用例的（`tests/unit/test_af_ask_flow.py`），
所以这不是"逻辑没测"，是**UI 那份类型的真源没被实测坐实**——`ui/src/types/api.ts` 里写着的
`SessionViewResponse` / `AskItem` 两组键，此前靠的是"读代码抄下来的"。

**补的两条判据**（`tests/contract/test_af_ask_contract.py` 末段，走真 `build_app` + `TestClient`）：
1. `test_suspended_ask_shows_up_in_the_http_session_view`：`POST /api/sessions`（IR 带 `ask` 节点 +
   seed `binary_sensor.motion=off` + 事件把它打到 `on`）之后，视图顶层键**逐字等于**九键集合，
   `asks` 恰一条，且该条的键**逐字等于** `AskItem` 的八个必需键；`control` 整个 dict 对死
   （`{"widget":"select","kind":"choice","prompt":"开灯吗？","options":["开","关"]}`——前端只认
   `widget`/`kind`/`options`，生产者改名而这里不红，面板会静默退化成输入框）；
   实例的 `current_node` 停在 `q1`，证明"真挂在 ask 上"而不是跑完再补记录；`GET /api/sessions/{sid}` 读回同一条。
2. `test_answer_success_branch_returns_the_same_view_with_asks_drained`：`POST /api/sessions/{sid}/answer`
   带结构化应答（`{"kind":"choice","value":"开"}`）⇒ 200、**同一份九键视图**（不是半个对象）、
   `asks` 排空、实例 `vars.ask_answer` 落值且 `vars.decided=="yes"`（走 then 边进了 `d1`），
   随后 `GET` 仍读回 `asks=[]`（应答效果在会话上，不只在那次 POST 的返回值里）。

**两处红/绿读数（当场跑出）**：
- 把 `answer_session` 的返回改成 `{"ok","session_id","asks"}` 三键（即"成功分支只回半个对象"）
  ⇒ `1 failed`，RED_RC=1，原话 `AssertionError: 成功分支返回的是同一份会话视图，不是半个对象`
  + `assert {'asks','ok','session_id'} == {'asks','aud…, 'mode', …}`（六个键逐个列为缺失）。
- 把 `_session_view()` 的 `"asks": _asks_of(runtime)` 改成 `"asks": []`（即"挂起 ask 不进视图"）
  ⇒ **两条新判据同时红**，`2 failed, 12 passed`，RED_RC=1，原话 `事件触发后应停在挂起 ask，实际 []` /
  `assert 0 == 1`（另一条红在后续 `IndexError`，因为它按同一条前置取 `asks[0]`）。
- 撤除两处临时改动后：`tests/contract/test_af_ask_contract.py` **14 passed**；
  全量 `python -m pytest tests/ -q` → **RC=0**，`2635 passed, 51 skipped, 1 warning, 7 subtests passed in 85.39s`；
  `GATES_PYTHON=python bash gates.sh` → **RC=0**（结论原话"门禁干净"）。

**这一条把自己写的用例照了一遍（值得记）**：单跑契约文件是绿的，**全量跑才红**——
`tests/unit/test_af_api.py::test_api_asks_aggregate_endpoint_registered` 断言 `GET /api/asks` 聚合端点
"当前没有挂起 ask"，而 `af_service._SESSIONS` 是**进程内全局**会话表；第一条新判据故意留一条挂起 ask 不答，
就漂到了后面那条用例里 ⇒ 全量 `1 failed, 2634 passed`（PYTEST_RC=1）。
两条都以 `DELETE /api/sessions/{sid}`（返回 `deleted: True`）收尾后全量回到 **2635 passed / RC=0**。
这不是聚合端点的判据写错了——它正好演示了"会话表全局共享、留挂起 ask 就会污染别人"；
登记在此是因为**下一次谁再往这批里加留挂起态的用例，单跑绿不等于全量绿**（铁律 #11 的另一种形态）。

**CI 读数（用 §二之八 那条固化路径取，非手搓）**：run 33（`38051ca`，caps 默认值那次）`completed/success`，
连 run 28–32 一起是**连续六条绿**；本批这次提交的 run 34（`6d7d2a1`）取读数时仍 `in_progress`，
结论留待下一次读，不提前写成绿（铁律 #5）。

---

## 二之十、第 5 步 ③ 的第二档：`lint-imports` 从"告警"升为 CI **失败门禁**（并说明观察窗 shortfall）

计划 §第 5 步 ③ 的验收写的是"配置 + 既有违规清单（预计≈0）"，裁定 20260930 子决策三的执行约束是
**"先出配置（告警）→ 观察一个版本（或 2 周），确认无合法误伤 → 升为 CI 失败"**。
本批把第二档落下：`.github/workflows/ci.yml` 的 `architecture` 作业里 `run: lint-imports`
**去掉 `continue-on-error: true`**（改完用 `yaml.safe_load` 复解析整个 workflow，
`jobs=[test, contracts, gates, architecture, ui]`，全文件 `continue-on-error` 命中数 **0**）。

**读数的三个来源，各自当场跑出（不是互相引用）**：
- 本机 `lint-imports` → **RC=0**，`Analyzed 96 files, 281 dependencies.` /
  `Service boundary never imported by kernel KEPT` / `Contracts: 1 kept, 0 broken.`
  （依赖数从第一次登记的 280 变 281，是 §二之十一 那批的副产品：`af_closedloop/__init__.py`
  从"与 `runtime.py` 逐字节相同的副本"改成薄转发，多出 `af_closedloop → af_closedloop.runtime` 一条边。）
- runner 侧（`python scripts/gh_ci_status.py log 111082312792 "KEPT"`，run 34 的 `layering-gates` 作业）
  → 匹配 1 行原话 `Service boundary never imported by kernel KEPT`；同作业 `violat` 关键词只匹配到
  架构硬门那行 `Baseline lock: 86 modules, 0 violations.`（**0 违规**）。
  ⚠️ 那行的 **86 当时被原样抄进了本记录**，它不是"runner 与本机口径不同的经验值"，而是一个缺陷的读数
  （`.gitignore` 的 `_*.py` 吞掉一个包的 `__init__.py`，grimp 因此不递归）——根因、修法与复发门见 §二之十一。
- 误伤计数：**0 条 BROKEN**——裁定的观察窗要拦的是"合法耦合被误判"，这一条已经用读数回答。

**"能变红"实测（铁律 #8）**：给 `src/autoforge/af_time.py` 末尾加一行 `from autoforge import af_service`
⇒ `lint-imports` **RC=1**、`Contracts: 0 kept, 1 broken.`，并且它报出的不只是那一行——
还连带给出间接链 `autoforge.af_ir -> autoforge.af_bus (l.156) -> autoforge.af_time (l.20) -> autoforge.af_service`，
即 `af_ir` 这个 L0 内核经两跳摸到了 L2 服务层。这条**传递闭包**正是 `check_imports.py`（锁直接反向依赖 baseline）
之外的另一覆盖面，也是把这道门留在 CI 的理由。撤销临时行后回到 KEPT/RC=0，`git status` 干净。

**必须写下来的 shortfall**：裁定给的是"一个版本**或** 2 周"，而 `lint-imports` 进 CI 是
`20356dc`（**2026-10-01**），日历上只走了 2 天（绿的是 run 28–34 这 7 条 push）。
按"误伤 0 + 版本内已收口"这一档落的，日历那一档没满。所以这不叫"按裁定执行完毕"，
而是**AF 单方提前落了第二档**，已投追认申请：
`关键决策部/inbox/20261003-AF-import-linter升硬门时点与gitignore吞包标记-决策申请.md` 第 1 件（§五 第 8 件；
同件第 2 件是 §二之十一 那个跨仓外溢问题）。DCD 若要按日历等到 2026-10-15，回一句即可撤——改动是一行 YAML，可逆，且不涉及他仓。
落这档而不是继续等的理由也摆在这里：留着 `continue-on-error` 就是本项目自己在
`tests/unit/test_honest_ok.py` 开头点名的那个同型缺陷（"CI 的门禁三步全挂 continue-on-error ⇒ 绿灯是假的"），
而一条永远不红的架构门，等于没有门。

**同批不受影响的两条**：真正的架构硬门 `python scripts/check_imports.py`（grimp baseline-lock）**本来就是硬门**
（ci.yml:87，无 `continue-on-error`），本批没动；契约面本身（`pyproject.toml` 的
`[[tool.importlinter.contracts]]`，只做 L0→L2 这一条窄面）**一个字没改**——
配置注释里写明了更宽的 forbidden/layers 组合在本仓库会误报合法耦合，所以升的是**判据强度**，不是**判据范围**。
门禁 `gates.sh` 计数棘轮不受影响：**RC=0**，`except-pass-broad=27 | fake-ok-const=77`，基线 104/104。

---

## 二之十一、`.gitignore` 的 `_*.py` 吞掉了包标记，导致**两条架构门禁在 CI 上比本机弱**（一条数字对账抓出来的真缺陷）

### 起点是本记录自己写错的一行

上一条（§二之十）把 runner 的 `Baseline lock: 86 modules, 0 violations.` 原样抄进了"读数"，
而本机同一时刻的读数是 **96 modules**。两边都绿、都"0 violations"，所以我一开始把它当成
"runner 环境差异的经验值"记下来。这个处置本身是错的：**架构门禁的分析面是判据的一部分，
两个口径不一致就意味着两条门绿的不是同一份代码**。当场对账下去才定位到根因。

### 根因：一条 git 原话就能定位到行

```
$ git check-ignore -v src/autoforge/af_closedloop/__init__.py
.gitignore:83:_*.py	src/autoforge/af_closedloop/__init__.py
```

`.gitignore` 里"一次性调试脚本不入库"那条 `# 下划线前缀，用完即弃` 写的是 `_*.py`，
语义为「`_` + 任意 + `.py`」，而 `__init__.py` 正好是 `_` + `__init__` + `.py` ⇒ **包标记被一起吞掉**。
受害面实测（`git status --ignored --short` 里的 `.py` 只有三条）：
`_tmp_patch_af_live.py`（真·一次性脚本，判对）、`tests/_test_helpers.py`（无已入库的引用者，留在忽略位不动它）、
以及 `src/autoforge/af_closedloop/__init__.py`（**判错，且从未入库**）。
同目录 9 个模块（`loop/deepfix/detectors/fixers/fixes/graphops/history/coverage/runtime`）全在库里，
唯独包标记不在——所以 `git ls-files src/autoforge | grep -c '\.py$'` 一直是 95，磁盘上是 96。

### 后果不是"少一个文件"，是 grimp 少分析一整个包

grimp 对没有 `__init__.py` 的目录**不递归**。Python 3 的命名空间包让 `import autoforge.af_closedloop.loop`
在两边都还能成功（所以 pytest 与冒烟全绿、没人察觉），但架构图里那 10 个模块（包本身 + 9 个子模块）
在 runner 上直接不存在：

| 读数 | 本机 | runner（run 34 / job `111082312792`） |
|---|---|---|
| `check_imports.py` modules | 96 | **86** |
| L0 kernel / L1 runtime / L2 service | 9 / 82 / 5 | 9 / **72** / 5 |
| Baseline lock | `96 modules, 0 violations` | `86 modules, 0 violations` |

差的 10 个全在 L1。**这就是铁律 #5 的假安心**：门禁的覆盖面没人对账，绿变成自我声明；
也解释了为什么这个缺陷能活十几天——它不会让任何东西变红，只会让该红的东西不红。

### 修法与复发门（关键是判据选"索引"而不是"磁盘"）

1. `.gitignore` 在 `_*.py/_*.sh/_*.conf` 之后加 **`!**/__init__.py`**，注释里写明实测后果。
   一次性脚本的忽略语义保留，包标记永远入库。`git check-ignore` 复测：命中规则从
   `.gitignore:83:_*.py` 变成 `.gitignore:90:!**/__init__.py`（负向规则生效），该文件在 `git status`
   里从 `!!`（ignored）变 `??`（untracked），而 `tests/_test_helpers.py` 仍命中 `_*.py` ⇒ 忽略面没被放宽。
2. `scripts/check_pkg_markers.py`（新增，纯标准库 + git CLI）：`src/` 下每个含 `.py` 的包目录，
   必须在 **git 索引**里有 `__init__.py`。**为什么不判磁盘**：磁盘上文件在，本地永远检不出，
   只有 CI 的 checkout 才缺——判磁盘等于把这条门做成"只在本机响的假门"；索引口径本机与 runner 一致。
   `tests/` 不在管辖内（pytest 靠 rootdir 收集，测试目录刻意不放 `__init__.py`）。
   接入 `gates.sh`（新增"包标记门禁"一节 + RC 聚合分支，RC=1/2 都拦）。
3. 顺带暴露的另一处（**这是修 gitignore 才看见的**）：那个从未入库的 `__init__.py` 与
   `runtime.py` **逐字节相同**（`diff` RC=0）。两份 `GuardViolation` 不是同一个类对象，
   从包根导入的 `except GuardViolation` 抓不到 `runtime` 抛出的那个。改成薄转发
   `from .runtime import GuardViolation, load_module, safe_call`，真身只留一份。
   现有引用者不受影响：库里没人 `from autoforge.af_closedloop import X`，
   `fixers.py:13` 与 `tests/test_closedloop.py:19` 都走 `.runtime`。

### 能变红实测（铁律 #8，两条方向）

- 造一个"有模块、无入库包标记"的假包（`src/autoforge/af_probe/mod.py` 入索引、不放 `__init__.py`）
  ⇒ `check_pkg_markers.py` **RC=1**：`✗ src/autoforge/af_probe/__init__.py 不在 git 索引里`；探针随即撤除。
- **还原历史原状**：把 `af_closedloop/__init__.py` 从索引临时摘掉（`git rm --cached`，磁盘保留）
  ⇒ **RC=1** 且指名 `src/autoforge/af_closedloop/__init__.py`，`git add` 装回后 **RC=0**。
  这条最有价值：它证明新门真能抓住已经发生的那次缺陷，不是只防假想形态。
- **整条 `gates.sh` 而不是单脚本**（CI 的 `quality-gates` 作业跑的是这条）：同样 `git rm --cached` 之后
  `GATES_PYTHON=python bash gates.sh` → **RC=1**，末行原话
  `结论：包标记门禁红（exit=1）。1=有包目录的 __init__.py 没入库，CI 上 grimp 不递归、架构门禁比本机少分析模块；2=拿不到 git 索引。`
  ——这条门的红走的是 `gates.sh` 末尾的 RC 聚合，且排在 AST/棘轮/冒烟之前判定，
  所以不会因为前后段各自绿过而被冲淡；装回后单脚本复测 **RC=0**（5 包 / 索引 96 个 `.py`）。

### 本批全链读数（当场跑出）

- `python scripts/check_pkg_markers.py src` → **RC=0**，`✓ 包标记门禁干净（5 个包目录都有入库的 __init__.py，索引内 96 个 .py）`
  ——索引内 `.py` 数与 grimp 的模块数第一次对上，这本身就是对账。
- `python scripts/check_imports.py` → **RC=0**，`modules: 96 / L0 9 / L1 82 / L2 5`，`Baseline lock: 96 modules, 0 violations.`
- `lint-imports` → **RC=0**，`Analyzed 96 files, 281 dependencies.` / `KEPT` / `Contracts: 1 kept, 0 broken.`
- `GATES_PYTHON=python bash gates.sh` → **RC=0**（六道全跑：AST 104/104、棘轮 104/104、undefined-name src 96 + tests 151 文件、
  主题白名单 7 处、**包标记**、import 冒烟）
- `python -m pytest -q` → **RC=0**，`2635 passed, 51 skipped, 1 warning, 7 subtests passed in 77.83s`
- runner 侧 96 的读数**当时**尚未取到（要等本批 push 后那条 run）⇒ 在取到之前，"CI 与本机同口径"是**已修的缺陷 + 待取的复测**，不是已验证事实。**该读数已在同批 push 后取到，见下方「推送后的 runner 读数」。**

跨仓外溢（其余三仓 + AgentOps 模板是否有同款 `_*.py`）AF 不擅动他仓，已作为第 2 件提 DCD（§五 第 8 件）。

### 同族扫描（不止步于抓到的那一个）

缺陷既然是"忽略规则吞掉构建输入"，就把手上三条口径都过了一遍，读数如下：

- `git status --ignored --short`（排除 `node_modules`/`__pycache__`/`dist` 等构建目录后）只剩两条：
  `src/autoforge.egg-info/`（构建产物，忽略正确）与 `tests/_test_helpers.py`。后者按
  `grep -rn --exclude-dir=__pycache__ "_test_helpers" src tests scripts examples docs` 复测
  = **0 命中**（连它自己都不提这个名字，没有任何 import 面）⇒ 是死文件，留在忽略位不动，
  给它补入库等于把一份无人引用的测试副本变成"已验证资产"。
- 磁盘 vs 索引逐文件对账：`find src -type f -not -path '*__pycache__*'` = **103**，`git ls-files src` = **97**，
  差的 6 个经 `comm -23` 列名**全部**是 `src/autoforge.egg-info/*`（PKG-INFO/SOURCES.txt/dependency_links.txt/
  entry_points.txt/requires.txt/top_level.txt）⇒ 源码树除已修的那一个之外，没有第二个被吞的文件。
- `.dockerignore` 是同一类"上下文面"的入口，专门查过：里面**没有** `_*.py` 之类的模式，也不会吃包标记；
  它排除的路径里唯一命中"已入库文件"的是三份 `ui*/.env.production`，而这三份实测不含密钥
  （只有 `VITE_USE_MOCK=false` 与 `VITE_API_BASE=/api`），且 API 镜像本就不带前端——
  `docker/docker-compose.api.yml:20-23` 是 `--ui-dir /ui` + 把 NAS 上的 `.../autoforge/ui/dist` 以卷挂进来。
  所以这条不构成同族缺陷，但**记一笔**：前端的交付是"宿主构建 + 挂卷"，不在镜像里，
  窗后验收若只看 `compose ps` 与 `/health`，WebUI 那一路是没被覆盖的。

runner 侧的复测读数由 `gh_ci_status.py` 取（本批已取到，见下一小节）。

### 推送后的 runner 读数（`3144879` → run 36 / id 37083993838，当场取）

先记两件已经取到的：

- **上一条 push 的 run 35（`b7b158e`）= `completed/success`** ——`runs` 读数里
  `success runs: [35, 34, 33, 32, 31, 30, 29, 28]`，§二之九 登记时那条 run 还是 `in_progress`，现已闭合。
- **本批 `quality-gates` 作业 = `completed/success`**（job `111090382310`，`failed_steps=[]`）
  ⇒ 新门在**真 checkout** 上不 false-red：runner 拿得到 git 索引、`src/` 五个包目录都有入库的包标记，
  RC=0。这条不是"没跑到"——同一作业里 AST/棘轮/undefined-name/主题白名单/冒烟六段都跑到了。

**闭合（同批当场复取）**：run 36 五个作业现已全部 `completed/success`
（`ui-typecheck-build` `111090382146` / `pytest` `111090382277` / `adm-linkage-contracts` `111090382286` /
`quality-gates` `111090382310` / `layering-gates` `111090382332`，各自 `failed_steps=[]`），
作业完成后日志才上传，`log` 这次取到了正文：

```
$ python scripts/gh_ci_status.py log 111090382332 modules
匹配 7 行（关键词 'modules'）
2026-10-03T00:54:34.5034555Z   submodules: false
2026-10-03T00:54:34.7521535Z [command]/usr/bin/git -c protocol.version=2 fetch --no-tags --prune --no-recurse-submodules --depth=1 origin +31448799c88e35cd726d2f89f05f35b09b1e78ef:refs/remotes/origin/main
2026-10-03T01:05:48.6891486Z   modules: 96
2026-10-03T01:05:48.6891677Z   L0 kernel: 9 modules
2026-10-03T01:05:48.6891889Z   L1 runtime: 82 modules
2026-10-03T01:05:48.6892105Z   L2 service: 5 modules
2026-10-03T01:05:48.6892851Z    Baseline lock: 96 modules, 0 violations.

$ python scripts/gh_ci_status.py log 111090382332 Contracts
2026-10-03T01:05:48.8633527Z Contracts: 1 kept, 0 broken.

$ python scripts/gh_ci_status.py log 111090382310 包标记
2026-10-03T00:54:46.9804921Z ══ 包标记门禁（grimp 递归的前提交互，锁 CI/本机同口径）══════════
2026-10-03T00:54:47.0085752Z ✓ 包标记门禁干净（5 个包目录都有入库的 __init__.py，索引内 96 个 .py）
```

⇒ **两侧同口径这条现在是实测事实**：runner 上 `check_imports.py` 读到 `Baseline lock: 96 modules, 0 violations`
（此前是 86），`lint-imports` 读到 `Contracts: 1 kept, 0 broken`，`check_pkg_markers.py` 在 runner 的 git 索引里
数到 **96 个 `.py`**——三个独立入口给的是同一个 96。注意 `--depth=1` 那次 fetch 行：`actions/checkout` 仍建了真索引，
所以包标记门在 runner 上走的是"索引可达"分支而不是 RC=2 分支；哪天改成 tarball 投递才会红。
§六 那条"runner 侧模块数复测未取"的残留**当场销账**（见下方"已收口"）。

---

## 二之十二、本批读数：完成度自审抓到第 5 步 ② 的**半边未落**（"顺序追加"），以及本仓此前的一处过度声明

对 `docs/ADM联动执行计划-AF.md` 逐行核到第 5 步 ② 时，把裁定原文调出来对了一遍，抓到一件事：

- 计划的验收列（`docs/ADM联动执行计划-AF.md:72`）写的是"给 `af_persist` 补 SHA256（对齐 `af_store`）"——**这条满足**；
- 裁定的执行约束（`关键决策部/decisions/20260930-AutoForge后续优化三项-裁定.md` 子决策二）列了 5 条，
  其中"**追加写 / 重放友好（顺序追加，损坏段跳过策略）**"这一条里，`损坏段跳过` 已落、
  `顺序追加` **未落**：`af_persist.py:171-187` 是每实例一个快照文件、`tmp.write_text` → `os.replace` 的覆盖式原子替换。

本仓 §一 此前把整步标成"裁定全部落地"，而实际只满足了计划那一半——**这条过度声明是本节要纠正的对象**，
不是审计报出来的，是自家完成度自审抓的。已把 §一 表头改成"① ③ ④ 全落；② 按计划验收点已落、按裁定执行约束少一条子句"。

### 为什么不顺手把它补掉

三条都在库/实测，不是借口：

1. **同一条裁定自相抵**：紧挨着的另一条执行约束是"不改存储格式头（向后兼容现有数据，不触发迁移）"。
   顺序追加意味着要么新增一类文件（`.jsonl` 之类），要么把现有 `.json` 变成多记录容器——两者都是改格式头。
2. **与第五轮的修法反向**：`docs/audit/审计报告_第五轮_核实与修复.md:38-74` 那条 P1 的根因就是
   "每条全量重写 + 明细无上限"，修法是把无界增长收掉。`af_persist` 现在"终态即删除 + 单实例一文件 + 原子替换"
   正是有界形态；叠一层追加历史等于把刚收掉的那一族重新放出来。
3. **裁定自己把这半边判轻了**：同一份裁定 §子决策二 的结论写"申请把现状想弱了……af_persist 有崩溃恢复、
   af_store 有校验和 + 原子写 + fsync"，并把整项**降级为非硬触发**。"重放友好"要解决的问题
   （崩了能恢复、坏记录不拖垮整轮）实测由 `:83-89` 校验 + `records():223-238` 跳过 + 原子替换覆盖。

存"历史重放"与"证据链"这一层要不要做、以什么有界形状做，是 DCD 的取舍，AF 不靠猜测改存储面：
申请已投 `关键决策部/inbox/20261003-AF-af_persist追加写半边与裁定执行约束张力-决策申请.md`（§五 第 9 件，
三选一 A 判不适用 / B 给有界的 `.jsonl` 形状 N=8 / C 并入未来 `af_eventlog` 立项），AF 倾向 A。

**本条 `af_persist` 一个字节没动**，也没有新增测试或门——所以本节不产生"修了什么"的读数，
只产生"计划/裁定两份口径不一致，且本仓记录此前过度声明"这一条纠正。

---

## 二之十三、本批读数：验收点按"点"复测，抓到第 5 步 ① 的**接缝无判据**与一批过期行号

上一批的自审方式是"整步对整步"，这一批改成**把计划验收列的原文逐条当判据去跑**。差异在于：
整步对账看的是"有没有测试覆盖这块"，逐条对账看的是"验收列写的那个量，实测到没到"。三条读数：

| 计划验收列（原文） | 当场跑的判据 | 实测 |
|---|---|---|
| 第 0 步 ②：`tests/unit/test_af_house_tz.py` **9 例**仍绿 | `python -m pytest -q tests/unit/test_af_house_tz.py` | **`9 passed` RC=0**（此前记录只写"在全量 2602 项内含"，没按 9 例这个量对过） |
| 第 4 步 ③：`test_af_db_contract.py` + `test_af_insight_queue_contract.py`（队列 **13 项**） | 两文件分跑 | `5 passed` + **`14 passed`** RC=0 ⇒ 队列侧 14 ≥ 计划的 13 |
| 第 3 步 ④：编排后模拟中途失败 ⇒ 已部署部分全回滚、无半部署态 | `python -m pytest -q tests/test_af_ir_group{,_apply,_mode}.py` | **`31 passed` RC=0**，其中 `tests/test_af_ir_group_apply.py:91` `test_group_atomic_rollback_on_sim_failure` 就是这一条；① 的"旧图可共存读"由 `tests/test_af_ir_group.py:66` `test_ir_version_0_3_0_supported_and_old_coexists`（0.3.0/0.2.1 支持、0.4.0 拒）钉住 |

### 抓到的一条：① 的两端都有测试，中间那根线没有

`FileLock.try_acquire` 的互斥语义有测试（`tests/unit/test_v0_9_multiproc.py:316-323`），API 层只读拒绝有测试
（`tests/unit/test_af_api.py:89/:96`、`test_af_undo_http.py:142`、`test_af_watch_f4_feeds.py:314`），
但 **serve 把 `not try_acquire()` 的结果传给 `build_app` 这一步一条判据都没有**——按铁律 #5，
"两端绿"不等于"接缝绿"（EXEMPT ≠ VERIFIED）；按铁律 #8，这条接缝当时**不可能变红**。

补上 `tests/unit/test_af_cli_serve_lease.py`（桩 `uvicorn`/`build_app`/联动桥，锁分别返回 `False`/`True`）：

```
$ python -m pytest -q tests/unit/test_af_cli_serve_lease.py
2 passed in 1.43s                                    RC=0

# 变异一：af_cli.py:1376 的 readonly=readonly → readonly=False
1 failed, 1 passed     tests/unit/test_af_cli_serve_lease.py:70: assert False is True   PYTEST_RC=1
# 变异二：同处 → readonly=True
1 failed, 1 passed     tests/unit/test_af_cli_serve_lease.py:81: assert True is False   PYTEST_RC=1
# 复原（`git diff -- src/autoforge/af_cli.py` 空）
2 passed in 1.43s                                    RC=0
```

### 顺带纠的账：§一 第 5 步 ① 那行的行号整批过期

`a5c8aa9` 给 `/api/sessions`、`/api/live/run` 装 events 上限之后，`af_api.py`/`af_cli.py` 的行号整体下移，
而记录里仍写 `:1365/:1375/:340-348/:449/:457/:465/:528/:696/:716`。本批按 HEAD 逐条 `grep -n` 重取为
`:1366/:1376/:357-362/:465/:473/:481/:545/:714/:735`（见 §一 该行的 ⚠️ 注）。**这类过期不会让任何东西变红**，
只会让下一个人按行号跳到别的函数上——和 §二之十一 那条 `.gitignore` 缺陷同属"静默失真"一族，只是危害小一个量级。

### 同族第二条接缝（顺着同一把尺子量出来的）：`_start_linkage_bridge`

① 那条补完后，用同一个问题扫第 1/2 步：**"桥侧每条判据都在测函数本身，那 CLI 递给桥的那四样东西谁在测？"**
答案是没人测。三种改法当时都不红：

| 改法 | 对应已知坑 | 当时的结果 |
|---|---|---|
| `proposal_sink=` 摘成 `None` | 回到进程内 `ProposalManager` ⇒ **重启即清零**（正是裁定 ④A 要消掉的） | 全量绿 |
| `version=` 传空串 | `adm/autoforge/caps` 是 retained ⇒ DB 读到"没版本号的 caps"（本批之十刚钉过默认值，但**接线面**没钉） | 全量绿 |
| 去掉"未开启 ⇒ `return None`" | 默认试连 ⇒ "看着在跑、其实一句都没说"的假桥 | 全量绿 |

补 `tests/unit/test_af_cli_linkage_wiring.py` 三条（桩 `start_from_env`/`attach`，判的是 kwargs 与落盘副作用）：

```
$ python -m pytest -q tests/unit/test_af_cli_linkage_wiring.py
3 passed in 0.33s                                        RC=0

M1 proposal_sink→None : RC=1  `2 failed, 1 passed`
M2 version→""         : RC=1  `1 failed, 2 passed`
M3 env 守卫改 if False: RC=1  `1 failed, 2 passed`
（三次变异后都用 `p.write_text(orig)` 还原，脚本自证 `restored identical: True`，
 且 `git diff -- src/autoforge/af_cli.py` 输出为空 ⇒ 生产代码回到 HEAD）
```

第三条实测还顺带说明：去掉守卫后 `_start_linkage_bridge` 会真的往下走（该轮 pytest 从 1s 涨到 31s），
"默认关"不是一句注释而是一条有人看守的分支。

### 同族第三条接缝（同一把尺子往上游再走一跳）：`_make_runtime` 起桥的那两参数

前两扫的是"CLI 递给桥什么"。同一个问题用在 `_make_runtime` 上：**"什么条件下起桥、起的桥拿谁的钟——这根线谁在测？"**
也没人测。这一跳正是 §四 窗后验收第 ③ 件（`mosquitto_sub` 抓到一条 `af/automation/fired` 且其 `ts` 是**家庭墙钟**）
在代码侧唯一的落点：`af_cli.py:320` `_start_linkage_bridge(clock=runtime.clock, store_root=store_root)`。
桥侧 `af_mqtt_bridge` 的测试只断言"拿到了 clock 并用它打 ts"，**不追问那是谁的 clock**——
所以 `runtime.clock`（`SystemTimeSource`，真墙钟）传成仿真虚拟钟或 `None`，全量 pytest 照绿，
而窗后抓包时事件 `ts` 会是一条虚拟时间；同理，仿真分支若也起桥，broker 收到的就是"没发生过的自动化"。

补两条判据（`_spy_bridge` 桩掉 `af_cli._start_linkage_bridge`，只记 kwargs）：

```
$ python -m pytest -q tests/unit/test_af_cli_linkage_wiring.py
5 passed in 0.46s                                        RC=0

M-a clock=runtime.clock → clock=None : RC=1  `1 failed, 4 passed`
    FAILED ::test_dry_live_runtime_hands_the_bridge_its_own_wall_clock
M-b 仿真分支也起桥                    : RC=1  `1 failed, 4 passed`
    FAILED ::test_simulated_runtime_never_opens_the_bridge
（两次变异都用 p.write_text(orig) 还原，脚本自证 `restored identical: True`，
 `git diff -- src/autoforge/af_cli.py` 输出为空 ⇒ 生产代码回到 HEAD）
```

dry-live 那条顺带把 `store_root` 也钉住了（断言 `Path(calls[0]["store_root"]) == tmp_path`）：
④A 队列的落点由这个参数决定，传错不会让桥报错，只会让提案落进另一个目录、面板永远显示空态。

### 上一环全链读数（HEAD `0722828`，含前两条接缝判据）

- `python -m pytest -q` → **RC=0**，`2640 passed, 51 skipped, 1 warning, 7 subtests passed in 84.97s`
  （2635 → +2 ① 接缝 → +3 桥接线；两条都跑在全量里，没有靠"单跑绿"过关）
- `GATES_PYTHON=python bash gates.sh` → **RC=0**：AST 新增 0 / 存量 104、棘轮 104/104、
  undefined-name src **96** + tests **153** 文件（上批 151，那两个新测试文件；分跑复核 `RC=0`）、主题白名单 7 处、
  包标记 5 包 / 索引 96 个 `.py`、import 冒烟 0 违规

生产代码一个字节没改（两处变异都已复原并核过 `git diff` 为空）；那一环产物 = **两条接缝判据** + 一次验收点逐条对账 + 行号纠偏。

### 本批全链读数（HEAD `9c6deb9`，三条接缝判据都在跑）

- `python -m pytest -q` → **RC=0**，`2642 passed, 51 skipped, 1 warning, 7 subtests passed in 80.01s`
  （2640 → +2 第三条接缝；跑在全量里，与 `test_af_cli_linkage_wiring.py` 分跑 `5 passed` 同口径）
- `GATES_PYTHON=python bash gates.sh` → **RC=0**：AST 新增 0 / 存量 104、棘轮 104/104、
  undefined-name src **96** + tests **153** 文件（本批未新增文件，只扩了既有用例文件里的两条，
  故 tests 计数与上批同）、主题白名单 7 处、包标记 5 包 / 索引 96 个 `.py`、import 冒烟 0 违规

生产代码依旧一个字节没改（两次变异都已复原并核过 `git diff -- src/autoforge/af_cli.py` 为空）；本批产物 = **第三条接缝判据**。

---

## 二之十四、接缝盘点的首个产物：状态源扇出门禁（`fc2bba6`）

§六 那条"接缝类判据没有系统性覆盖"里，最典型的一份不是某根线没测，而是**一段手抄的镜像**：
`Runtime.__post_init__`（`af_runtime.py:55-74`）在构造期把 `states` 分别交给三个消费方——

```
self.instances = InstanceManager(self.states, …)     # :57
self.executor  = NodeExecutor(…, states=self.states, …)  # :62-65
self.scheduler = Scheduler(…, states=self.states, …)     # :66-74
```

但构造**之后**再换源是常态（真机要等 `HAStateProvider`、仿真要等 `seed_from_graph` 的钟），
于是调用点必须手抄四行。现存六处：`af_cli.py:298-301 / 342-345 / 1036-1039`、
`af_service.py:886-889 / 1348-1351 / 1693-1696`；构造期注入的两处（`af_service.py:881` hifi、
`af_vhass/harness.py:165`）走 `states=` 形参，由 `__post_init__` 负责，不在此列。

少抄哪一行都不报错、不崩，只让那一个消费方继续读旧状态源；`executor` 少抄就是 canary 漂移检测
读一份空的 `InMemoryStateProvider`（第七轮审计那一族的静默版）。**六处调用点里删掉任意一行，全量 pytest 一条都不红**——
这条判据放进 `gates.sh`，与主题白名单、包标记同族。

### 实测：绿一次、红两次，外加"门自己坏了"也红一次

```
$ python scripts/check_states_fanout.py
✓ 状态源扇出门禁干净（扫描 96 个文件，换状态源的 runtime 四个消费方都同步）   RC=0

G1 af_cli.py:345 删掉 `runtime.executor.states = ha`   : RC=1
   ✗ …:342：给 runtime `runtime` 换成 `ha` 只写了 ['instances.states','scheduler.states','states']，缺 ['executor.states']
G2 af_cli.py:301 换成 `InMemoryStateProvider()`        : RC=1  两条 finding（provider 组缺 executor、另一组只剩一条）
（两次变异都 `p.write_text(orig)` 还原，脚本自证 `restored identical: True`）
```

### 这一批最值得留下的不是判据，是那次假绿

第一版实现把属性链的根名解析错了（`runtime.executor.states` 被解成 root=`executor`、path=`states`），
后果不是报错而是**永远判绿**：对 HEAD 绿，对"删掉一行"的两次变异 `G1/G2` 也照样绿（RC=0）。
是靠手敲变异脚本才发现的——`_attr_path` 少把根名放进 `parts`（`parts.append(cur.id)` 那行当时不存在）。
修完后又发现按"根名"聚合会把 `_make_runtime` 里的两次独立换源（`provider` 与 `ha`）误报成"混用两份源"，
判据因此改成按 **(根名, 递出去的那份状态源)** 分组。

所以 `tests/unit/test_states_fanout_gate.py` 7 条里，`test_a_lone_top_level_states_assignment_is_flagged`
钉的就是那次假绿的形状。它对 HEAD 绿，把 `parts.append(cur.id)` 删掉则 `3 failed`（RC=1）——
门禁的反例测试必须先证明自己不会跟着一起瞎。两次"让门自己变瞎"的变异实测：

```
V1 删 parts.append(cur.id)（退回第一版缺陷）: RC=1  `3 failed, 4 passed`
V2 missing 判据写死成 []                   : RC=1  `3 failed, 4 passed`
（都还原并自证 `restored identical: True`）
```

### 顺手做的盘点：src 里"同一份值递进多条属性路径"的还有谁

判据不该只钉自己踩到的那一处。用同一段 AST 扫 `src/autoforge/**/*.py`（按 (函数, 根名, 值的文本)
聚合被赋过值的属性路径，列出命中 ≥3 条的组），全仓命中的**四路镜像只有状态源这一族**——
其余命中都是 `self._attempt = 0` 这类构造期各自独立的字段初始化，不构成"少写一条会静默失真"的同族：

```
af_cli.py::_make_runtime   : runtime = ha / runtime = provider        （两组，四条齐）
af_cli.py::_metrics_snapshot: runtime = states
af_service.py::simulate_track / _build_sim_runtime / live_run         （各一组，四条齐）
```

⇒ 本门覆盖的是**全集**而非抽样；这一族的盘点到此结清。`af_watch` 的观察者装配、
`af_service` 的 store/clock 注入仍属"参数递错"类（不是多路镜像类），见 §六。

### 本批全链读数（HEAD `fc2bba6`，含扇出门禁）

- `python -m pytest -q` → **RC=0**，`2649 passed, 51 skipped, 1 warning, 7 subtests passed in 82.87s`
  （2642 → +7 扇出门禁反例测试；新文件 `tests/unit/test_states_fanout_gate.py` 分跑 `7 passed` RC=0）
- `GATES_PYTHON=python bash gates.sh` → **RC=0**：AST 新增 0 / 存量 104、棘轮 104/104、
  undefined-name src **96** + tests **154** 文件（本批新增 1 个测试文件）、主题白名单 7 处、
  包标记 5 包 / 索引 96 个 `.py`、**状态源扇出 96 文件干净**、import 冒烟 0 违规
- 生产代码：本批**只动 `gates.sh` 与新脚本**，`src/` 一个字节没改（六处手抄点原样保留，门只负责盯着它们）

## 二之十五、`af_service` 那半张盘点：两条真机路径的时间轴曾差一天又二十天（`12cea64`）

§二之十四 结尾把 `af_service` 的 clock 注入登记成"参数递错类，未盘"。本批就去盘它。盘法与上批同：
不问"哪里可能有问题"，问"**同一件事在这个仓里有几条路径在做，它们的答案是同一个来源吗**"。

### 缺陷本体

`live_run` 是**唯一**的真机下发服务层，HTTP `/api/live/run` 与 MCP `af_live_run`（DB 实际调的那条）都进这里。
它此前是：

```python
runtime = build_runtime(graph)          # HEAD 之前
```

`build_runtime` 的默认钟是 `VirtualTimeSource(datetime(2026, 9, 14, 8, 0, tzinfo=utc))`——那是**仿真**的锚点，
`af_runtime.build_runtime` 的签名注释写得很清楚。于是同一次对**真实设备**的下发：

- `context.trigger_time`（`af_instance` v1.7.1 明确"取时间源而非墙钟"，因为 `time_hour(context.trigger_time)`
  这类时间窗判断依赖它）= 2026-09-14 08:00；
- `audit[].at`、canary 证据的 `at` 同源；
- `at:19:30` 这类 time 触发按锚点判定。

而 CLI 的 live/dry-live 分支（`af_cli._make_runtime`）早已显式 `clock=SystemTimeSource()`。
**同一个 IR、同一次真机下发，`forge run --live` 与 WebUI/MCP 下发给出两套时间轴**——
第七轮审计"同一 IR 判相反结论"那一族，只是这次歪在时间上。今天（2026-10-03）下发的证据会被记成
09-14，差 **19 天**；按 `at:19:30` 的窗判，晚上七点半的真机下发永远不触发。

### 范围：把 `build_runtime(` 全数点过，不是"看到一处改一处"

```
af_cli.py:295              clock=SystemTimeSource()      真机/dry-live ⇒ 本来看墙钟
af_cli.py:340              无 clock                       仿真分支 ⇒ 锚点是刻意的
af_cli.py:1033             无 clock                       `_metrics_snapshot`：本地回放 ⇒ 刻意
af_service.py:881/884      clock=clock                    `simulate_track` hifi/fake ⇒ 入参，默认仍是仿真锚点
af_service.py:1346         无 clock                       `_build_sim_runtime`（会话/ask）⇒ FakeHA，刻意
af_service.py:1701         本批改：clock=clock             `live_run` ⇒ 唯一歪的那条
af_vhass/harness.py:165    clock=clock                    HiFi 对拍台 ⇒ 入参
```

`undo_deploy` 那条撤销链不建 runtime（直接对 transport 回放快照），不受影响。
⇒ 需要改的只有 `live_run` 一处，其余无 clock 的站点都在仿真面上，锚点是设计而非缺陷。

### 改法：锚在真实"现在"的**虚拟**钟，不是墙钟本体

```python
if clock is None:
    clock = VirtualTimeSource(SystemTimeSource().now())
runtime = build_runtime(graph, clock=clock)
```

为什么不干脆 `SystemTimeSource()`：`_replay_live` 的 `advance_s` 要用 `runtime.advance()`（负值还走
`clock.jump()`），而 `SystemTimeSource` 两个方法都没有——`Runtime.advance` 会当场
`TypeError: 当前时间源 SystemTimeSource 不支持时间旅行`，真机回放能力（`events` 里前跳一小时）会随
这次"修正"一起消失。锚点取"现在的墙钟"、语义仍是可推进的虚拟钟，才同时满足两件事；
需要确定性的调用方显式传 `clock=`（新增的入参就是这条接缝的测试面）。

判这是 AF 内部口径、不必走 DCD：契约表里 `af/automation/fired` 的 ts 由 CLI 那条路径产生，本已按墙钟；
本批改的是让 MCP/HTTP 这条**向同一契约靠**，没有新 topic、没有改任何载荷字段含义。

### 第一版判据是空的——而且它当时报"绿"

先写的三条用例拿 `out["audit"]` 当时间轴。首跑读数 `2 failed, 1 passed`，我把那条 passed 当成了
"显式 clock 已被支持"的证据。拿脚本直接 dump 两条路径的返回体才发现：

```
== default(now-anchored)
  audit: []
  instances: [{... "trigger_time": "2026-10-03T02:01:22.254465+00:00" ...
== explicit 2027
  audit: []
  instances: [{... "trigger_time": "2027-03-04T05:06:07+00:00" ...
```

一次**顺利**的真机下发一条审计都不会产生——`AuditLog` 只记 `entity_drift`/`action_failed`/总线与配额类
事件，成功路径上没有这类事件。于是 `assert all(t.year == 2027 for t in _audit_times(out))` 在空表上恒真，
**那条"绿"是假绿**，而且假在我自己刚写完的判据上（铁律 #8：判据要能红；铁律 #5：EXEMPT ≠ VERIFIED）。
换成必然存在的 `instances[].context.trigger_time`，并把"表为空"本身写成断言：

```python
def _trigger_times(out: dict) -> list[datetime]:
    """取实例上下文里的触发时刻；空表直接判红，避免下游断言在 `all()` 上空转。"""
    times = [datetime.fromisoformat(i["context"]["trigger_time"]) for i in out["instances"]]
    assert times, "真机跑完一个实例都没有 ⇒ 时间轴无从判定，这条用例等于没跑"
    return times
```

这条 guard 不是防御性代码，是**这条判据的唯一防假绿结构**：`all()`/`max()` 面对空表一个是恒真、一个是
`ValueError`，前者静默后者吵，本批实测就是这个静默咬到了我。

### 三次变异：每条用例各钉一次

`tests/unit/test_af_live_run_clock.py` 分跑 **`3 passed` RC=0**。变异驱动 `read_bytes`/`write_bytes`
（避开上次 CRLF 那一坑），锚点块匹配次数先断言 `== 1` 再替换：

```
orig sha256: bda63c532b044b33
M-a 真机路径退回 build_runtime 默认仿真锚点: RC=1  `3 failed in 2.18s`
    FAILED ::test_live_run_anchors_the_real_wall_clock
    FAILED ::test_explicit_clock_is_honoured
    FAILED ::test_advance_s_still_moves_the_live_clock
    restored identical: True
M-b 显式 clock= 被忽略                    : RC=1  `1 failed, 2 passed in 1.14s`
    FAILED ::test_explicit_clock_is_honoured
    restored identical: True
M-c 默认钟换成 SystemTimeSource（不可推进） : RC=1  `1 failed, 2 passed in 1.17s`
    FAILED ::test_advance_s_still_moves_the_live_clock
    restored identical: True
DRIVER_RC=0
```

M-a 一次红三条是预期的（把 clock 整个丢掉，三条断言分别落在锚点、显式入参、可推进面上）；
M-b/M-c 各只红自己那条，说明三条判据问的确实是三件事。`restored identical: True` 是**逐字节**比回
原文件（本批 `af_service.py` 有意是脏的，所以这里不看 `git diff`，看哈希）。

### 本批全链读数（HEAD `12cea64`）

- `python -m pytest -q` → **RC=0**，`2652 passed, 51 skipped, 1 warning, 7 subtests passed in 80.50s`
  （2649 → +3 真机时间轴判据）
- `GATES_PYTHON=python bash gates.sh` → **RC=0**：AST 新增 0 / 存量 104、棘轮 104/104、
  undefined-name src **96** + tests **155** 文件（本批新增 1 个测试文件）、主题白名单 7 处、
  包标记 5 包 / 索引 96 个 `.py`、状态源扇出 96 文件干净、import 冒烟 0 违规
- 生产代码：本批动 **1 个文件**（`src/autoforge/af_service.py`，+11/−2：新增 `clock` 入参、
  `VirtualTimeSource` 导入、默认锚点与 docstring 口径），这是自 `64ca83c`/`c14503d` 之后第一次改 `src/`
- `docs/audit` 无新报告（最新仍是第七轮，含 `审计报告_第七轮_核实与修复.md`），DCD 无新裁定到达

## 二之十六、`af_service` 的 store 注入：真机闸门有两处只装了 HTTP 一面（`dc8ac0d`）

§六 那条残留点名"未盘"的第二面（`live_run` 把 `store` 递进 `StaticScanner` 的 `device_guard`/
`entity_health`，`store=None` 与递错目录之间有没有判据未核）盘完了，**盘出来两处真缺陷**，都在
MCP 那一面：

| # | 缺陷 | 触发条件 | 影响面 |
|---|------|---------|--------|
| 1 | `af_mcp._t_live`（`:254`）调 `svc.live_run` 时**没递 `store`** | Agent 走 `af_live_run`（DB 用的正是这条路径） | Tier-0 设备保护（`{store_root}/device_acl.json`）与实体健康视图全部退化成"没有 store"：`StaticScanner` 拿不到 guard ⇒ **不产 `ENTITY_GUARD_TIER0`**，于是同一个 `light.bedroom`，人点 WebUI 按钮被拦下（400 + 原话），Agent 说同一句话直接下发到真设备 |
| 2 | events 条数上限只写在 HTTP 端点层（`a5c8aa9` 把三条端点补齐，但仍是端点层） | 任何不走 `af_api` 的调用方 | MCP 的 `af_live_run`/`af_simulate`/`af_sessions` 三面**无上限**：`_MAX_REPLAY_EVENTS` 挡的是"整场仿真在内存里跑"的 DoS 面，判据与入口绑在一起，就必然漏入口 |

两处是同一族的第 4、第 5 次命中：**"闸门只装了一面"**。`8e8c725`→`a5c8aa9` 那一串是在 HTTP 内部补面
（`/api/sim` 有、另两条没有），这一批是补到**另一类入口**（HTTP 有、MCP 没有）。

### 修法：判据挪到两条入口共用的咽喉，不在每面重抄一遍

- `af_service.MAX_REPLAY_EVENTS = 10000` + `_check_events_cap()`（`:130/:133`），三个服务层入口各调一次：
  `simulate_track:884`、`create_session:1472`、`live_run:1676`；`af_api` 删掉自家常量与
  `_check_event_cap`，只留一行指针注释（`:85`）。HTTP 侧靠 `_svc` 把 `ServiceError.status` 映射回
  **422**，状态码一个没变。
- `live_run` 的调用点放在**函数第一句**、`cfg = _live_config()` 之前。这条顺序是被测出来的：先判策略
  再判形状，超上限的 `/api/live/run` 在"live 未启用"的环境里拿 403 而不是 422，
  `test_event_cap_covers_every_events_endpoint[/api/live/run]` 当场红。**形状判据先于策略判据**——
  同一个请求不能在两套环境里给两个理由。我没有改动那条既有断言。
- `_t_live` 补 `store=store`（`:259`）。

### 反真空：对照组不是可选项

`tests/unit/test_af_live_entry_seams.py` 5 条。第一条是**正向对照**：
`test_control_without_acl_the_mcp_path_really_dispatches` 在没有 ACL 时断言 `dispatch("af_live_run", …)`
`is_error is False` 且 `FakeTransport` 真收到 `light.turn_off`。没有它，后两条守卫用例可能在
"MCP 这条路径压根跑不通"上集体绿——它们断言的是"被拦下"，而拦下它的原因可能根本不是 ACL。
这一点与 §二之十五 那次假绿（`all()` 跑在空审计表上）是同一个教训的两种形态。

### 变异读数（本批当场重跑，HEAD `dc8ac0d`）

```
orig mcp sha256: 9bff5d94f2e4cdef
orig svc sha256: b1834e88540247b1
M-a `_t_live` 退回不递 store（锚点 1 处，换行口径 CRLF）
    : RC=1  `1 failed, 18 passed, 1 warning in 5.75s`
    FAILED tests/unit/test_af_live_entry_seams.py::test_mcp_live_run_honours_the_store_device_guard
    restored identical: True
M-b live_run 不再判 events 上限（锚点 1 处，LF）
    : RC=1  `2 failed, 17 passed, 1 warning in 5.06s`
    FAILED tests/unit/test_af_live_entry_seams.py::test_mcp_live_run_refuses_oversized_events
    FAILED tests/unit/test_af_api.py::test_event_cap_covers_every_events_endpoint[/api/live/run]
    restored identical: True
M-c simulate_track 不再判 events 上限（锚点 1 处，LF）
    : RC=1  `2 failed, 17 passed, 1 warning in 5.57s`
    FAILED tests/unit/test_af_live_entry_seams.py::test_service_layer_itself_rejects_oversized_events
    FAILED tests/unit/test_af_api.py::test_api_sim_rejects_oversized_event_list
    restored identical: True
DRIVER_RC=0
```

M-b/M-c 各红两条：一条是自己的新判据，一条是**同族的老判据**（HTTP 面）——上限上收到服务层之后，
HTTP 那两条测试改判的是"服务层抛、HTTP 映射成 422"这条链，链条任一环断掉都红。这正是收口的目的：
判据只有一份。

顺带一条事实（驱动脚本第一版在这上面空转）：`af_mcp.py`/`af_api.py` 入库是 **CRLF**，
`af_service.py` 是 **LF**，本仓没有 `.gitattributes` 且 `core.autocrlf=false`。变异驱动按锚点字符串
替换时，跨文件用同一种换行就**匹配 0 处**——M-a 那一次"什么都没改、测试却绿"的假读数就是这么来的。
改成按文件探测（CRLF 计数 > LF 计数则用 CRLF）后才是上表。我没有顺手统一全仓换行：那是会污染
`git blame` 的大 diff，且与任何验收点无关。

### 为什么这两件自己落了、租约那件去投申请

同一条盘点里抓到第三件：`serve` 的单写者租约（`af_cli.py:1365` `FileLock(store_root/".serve.lock")`）
与 `build_app(readonly=…)` 只装在 HTTP 面，`af_mcp.serve_mcp` 与 `dispatch` 里 `grep readonly|lease|acquire`
**零命中**——MCP 进程可以在 serve 已经持有写权时同时开真机写面。三件事的差别不在"是不是缺口"，在
**收口的代价由谁承担**：

- 前两件是把 AF **已经对外承诺**的策略（Tier-0 必须人审、events 有上限）装到它漏掉的那个入口上，
  行为只会更严、与既有裁定同向，且退路是一条 `git revert`；
- 第三件要新增一个 DB 侧看得见的**拒收模式**（MCP 在锁被占用时返回什么码、`check` 还是 `acquire`、
  是否只拦 live），那是跨仓可见的失败面，属 20261002 §一"契约面改动走裁定"的口径 ⇒
  投 `关键决策部/inbox/20261003-AF-单写者租约只装了HTTP面MCP真机写未受约束-决策申请.md`（§五 第 10 件，
  A/B/C，AF 建议 A=check-not-acquire 且只拦 live），**代码一个字节没动**。

### 本批全链读数（HEAD `dc8ac0d`）

- `python -m pytest -q` → **RC=0**，`2657 passed, 51 skipped, 1 warning, 7 subtests passed in 78.97s`
  （2652 → +5 入口接缝判据）
- `GATES_PYTHON=python bash gates.sh` → **RC=0**：AST 新增 0 / 存量 104、棘轮 104/104、
  undefined-name src **96** + tests **156** 文件、主题白名单 7 处、包标记 5 包 / 索引 96 个 `.py`、
  状态源扇出 96 文件干净、import 冒烟 0 违规
- 生产代码：`dc8ac0d` 动 4 个文件（`af_service.py`、`af_mcp.py`、`af_api.py` + 新测试文件），+196/−24
- `docs/audit` 仍是第七轮最新，无第八轮投递；`关键决策部/decisions` 最新仍是 20261002 三份，无新裁定

## 二之十七、`af_watch` 那半张盘点：观察期结束时 `auto_rollback: false` 被无视（`af2ee56`）

§六 点名的最后一条未盘接缝（`af_watch` 的观察者装配）盘完，第三条真缺陷落在喂它的那一侧。

### 盘点范围：三个喂入点 + 一个同名碰撞

`af_watch` 只有 `record_shadow`/`record_canary`/`record_conflict` 三个入口，逐点对过：

| 喂入点 | status 取值 | 与 `af_watch` 计数档是否对齐 |
|---|---|---|
| `af_shadow.compare()` | `"verified"`（MATCHED）/ `"failed"`（MISSED），且只在 MATCHED/MISSED 两种判决下喂 | 对齐；其他判决不喂是刻意的（"没判"不算证据） |
| `af_executor` 立即检查路径（无 duration） | `"unmodeled"` / `"failed"` / `"verified"` 三档齐 | 对齐，且 `has_drift()` 读不到态时保守判漂移（第七轮口径） |
| `af_conflict_runtime._feed_watch_conflict()` | `"conflict"`，ALLOW 放行**不喂** | 对齐——仲裁器存在本身不是"验过了"（铁律 #5） |

顺带记一个**同名碰撞**（非缺陷，不改码）：`af_audit.record_conflict(journal, *, name, expected, actual…)`
与 `af_watch.record_conflict(automation_id, status, at, detail)` 同名不同物，`af_store.py:23` 用的是前者
（乐观锁写冲突落 JSONL），`af_conflict_runtime` 用后者。两处调用点的关键字集合完全不重叠、误用会当场
`TypeError`，所以不是待修的洞；登记是为了下一轮盘点不把它当缺陷重报。

### 真缺陷：挂起观察的 resume 分支把旗子丢在接缝上

`canary: {"duration": "15m", "auto_rollback": false}` 是操作员的选择："只观察，别自动反向下发，留给人判断"。

- **立即检查**那条路（无 duration）走 `CanaryGuard.check_and_rollback()`，`:166` 的
  `if not self.auto_rollback …: return []` 尊重旗子；`tests/unit/test_af_canary.py:65` 早已把这条钉住。
- **挂起观察**那条路把可序列化元信息存进 `instance.ctx.context["pending_canary"]`，存的键只有
  `action`/`params`/`pre_snapshot`/`adapter` —— **`auto_rollback` 没存**。观察期结束 `resume(kind="on_timeout")`
  重建 `CanaryResult` 时用 `SimpleNamespace(states=…)` 当 guard（只为喂 `has_drift()` 的 `.states`），
  然后**直接** `wrapped.rollback(adapter)`，绕开 `CanaryGuard` 那一层判断。

⇒ 同一条 IR，`duration` 写与不写，对 `auto_rollback: false` 给出**相反**的行为，而且错的方向是
"比要求的更自动"。这条路径不是崩溃恢复专用：它是挂起观察的**常规**唤醒路（`pending_canary` 随实例快照
持久化，注释里写的 P1-4 是顺带修的另一件事），所以正常跑 15 分钟观察期的家庭就会走到。
`af_scanner` 的 P1-2 策略只要求 L2 动作"配 canary"（`node.canary` 非空即可），并不要求
`auto_rollback` 为真 ⇒ 这一配置在生产里可达。

### 修法：把旗子带过接缝，并且不在分支里另写一套判断

- 挂起元信息加 `"auto_rollback": guard.auto_rollback`；恢复侧
  `bool(pending_canary.get("auto_rollback", True))` —— **缺键按 True**，改动前已落盘的旧快照行为不变
  （不新增迁移，与 §二之十二 那条"格式头不动"的口径一致）。
- 恢复分支不再用 `SimpleNamespace` 假 guard，改成真的 `CanaryGuard(states=self.states, auto_rollback=旗子)`
  并调 `check_and_rollback()`：**回滚与否只有一处实现**，两条路径不可能再分叉（这是把 §二之十四 那条
  "多路镜像"族的手法用在策略位上）。
- 不回滚时漂移照旧记 `failed` + `entity_drift`，`reason` 写成"auto_rollback=false ⇒ 未回滚，留给人判断"
  ——不回滚 ≠ 没验出问题，面板不能把两者读成同一种（铁律 #5）。

### 判据与变异

`tests/unit/test_af_canary.py::test_observe_window_resume_honours_auto_rollback_false`：先断言挂起前只有
`light.turn_on` 一次调用（否则"没有 turn_off"可能是压根没跑），推进 15m+1s 后断言
①有 `entity_drift`、②调用序列仍是 `["light.turn_on"]`、③喂进 `af_watch` 的是 `status="failed"` 且
detail 含"未回滚"。**正向对照**是同文件既存的
`test_executor_canary_rolls_back_on_drift`（同一条 resume 路径、`auto_rollback: true` ⇒ 真出
`light.turn_off`）；两条合起来才证明读的是旗子而不是路径。改动前它先红在 ②：
`assert ['light.turn_on', 'light.turn_off'] == ['light.turn_on']` RC=1。

```
orig af_executor sha256: b4030323269aa042  newline=LF
M-1 挂起时不带过 auto_rollback 旗子（接缝上丢参数）: RC=1  `1 failed, 25 passed, 1 warning in 2.32s`
    FAILED tests/unit/test_af_canary.py::test_observe_window_resume_honours_auto_rollback_false
    restored identical: True
M-2 恢复分支绕开 CanaryGuard 直接回滚（忽略旗子） : RC=1  `1 failed, 25 passed, 1 warning in 3.58s`
    FAILED tests/unit/test_af_canary.py::test_observe_window_resume_honours_auto_rollback_false
    restored identical: True
M-3 恢复分支的 guard 恒 auto_rollback=True（读了但被覆盖）: RC=1  `1 failed, 25 passed, 1 warning in 2.32s`
    FAILED tests/unit/test_af_canary.py::test_observe_window_resume_honours_auto_rollback_false
    restored identical: True
DRIVER_RC=0
```

三条各只红新判据、其余 25 条（含正向对照那条）不动 ⇒ 旗子的三段（带过来 / 用它 / 不被覆盖）分别有主。

### 本批全链读数（HEAD `af2ee56`）

- 修复前（HEAD `dc8ac0d`）新判据单跑：**RC=1** `1 failed, 9 passed`，红在"不得反向下发"那条断言
- 修复后 `tests/unit/test_af_canary.py` + `test_af_watch_f4_feeds.py` +
  `tests/acceptance/test_g5_fault_injection.py` + `tests/test_undo.py` 分跑：**RC=0** `57 passed, 1 warning`
- `python -m pytest -q` → **RC=0**，`2658 passed, 51 skipped, 1 warning, 7 subtests passed in 94.77s`（2657 → +1）
- `GATES_PYTHON=python bash gates.sh` → **RC=0**：AST 0/104、棘轮 104/104、undefined-name src 96 +
  tests 156 文件（本批未新增测试文件）、主题白名单 7、包标记 96、状态源扇出 96 文件、import 冒烟 0
- 生产代码：`af_executor.py` +21/−8；`docs/audit` 仍无第八轮，`decisions` 无新裁定

## 二之十八、"参数递错"那一族升成常驻门禁：`check_param_injection.py`（`9a83ead` + `4a3e3b1`）

### 为什么这一族该有门，而不是再手扫一次

前面六条同族站点里有五条是"顺着验收点手动追问这根线谁在测"追问出来的（§二之十三 / 之十五 /
之十六 / 之十七）。它们的共同机制只有一个：**带默认值的关键参数少递一个，不报错、不崩，只让
那条入口面的闸门静默退化成"没有这道闸门"**。手扫的产物只能是"这一批修好了"，判不住下一次
少写一个关键字。所以这一批把判据做成 `scripts/check_param_injection.py` + `gates.sh` 一节
——CI 的 `quality-gates` 作业跑的就是 `bash gates.sh`，本机绿一次，runner 就同口径红得起来。

### 门第一次跑就抓到第二处真缺陷：Agent 面的健康读数**从不探**它服务的 store

`af_mcp._t_health(store, args)` 写的是 `return svc.health()`，而 HTTP 面 `af_api.py:381` 是
`return svc.health(store)`。同一次健康检查，HTTP 给 `store_ok: true`、Agent 给 `store_ok: null`
——签名里 `store: GraphStore | None = None` 那句"为 None 时 ok=True 保持向后兼容"就是把它当成了
设计。与 §二之十六 那两件的差别在**代价由谁承担的形状**：那两处是写面闸门（Tier-0 设备保护、
events 上限），这一处是**读面的证据缺失**：`null` 被下游读成"这一栏没问题"，正是 铁律 #5 的假安心
（EXEMPT ≠ VERIFIED）。修法是递 store，不改 `health` 的向后兼容语义。

**判据与修复同批写，所以这里的反向证据是变异而不是"先红"**（不含糊过去）：把 `svc.health(store)`
退回 HEAD 形状的 `svc.health()` 之后，`test_dispatch_health_probes_the_store_it_serves` **RC=1**
`1 failed in 2.36s`，同一时刻门禁与 `test_repo_src_is_clean` 也各自红。

### 口径：只收"漏传即 fail-open"的参数，`clock` 有意不收

- `TARGET_PARAMS = ("store", "readonly")`。第二参数是本批把门从"store 一面"升成"这一族"时加的：
  `serve` 起 `build_app(..., readonly=…)` 漏 `readonly` 是 §二之十三 记录的本族最早一例，形状与
  store 完全相同。**泛化不是装饰**：M-3 把 `readonly=readonly` 摘掉，门禁当场
  `✗ …af_cli.py:1376: `af_api.build_app` 的签名里有 `readonly` 形参，这一处没递`。
- **`clock` 有意不收**，且这是判据的一部分而不是遗漏：`build_runtime(graph)` 的默认时钟是**仿真锚点**，
  那是设计（§二之十五 的结论是"live 路径必须锚墙钟"，不是"处处递 clock"）。按 `clock` 收会对 src 里
  每一处仿真面喊狼来了，噪音会淹掉真漏传。真机时间轴的判据另在 `tests/unit/test_af_live_run_clock.py`，
  本批用 `test_clock_is_deliberately_not_a_target` 把"为什么不收"钉成一条会随判据一起红的断言。
- 目标 = **模块级**函数（首参非 `self`/`cls`）且形参表带某个关键参数；按 **(模块, 函数名)** 解析。
  第一版按裸名查签名，把 `store.snapshot([...])`、`_direction(snapshot, ...)` 这类"变量恰好和函数
  同名"的正常调用判成红——**28 处误报**（真缺陷 1 处混在里面）。重写后降到 3 处（1 真 + 2 合法）。
  一堵会自己响的墙必须先做到不误响，所以 `_not_flagged` 那几条反例是本门的主干，不是边角。
- 认三种调用写法：`别名.X(...)`（别名由 `from . import af_service as svc` 解析）、`X(...)`（只按
  同模块或显式 `from .mod import name` 解析）、转发 `_svc(svc.X, ...)`（第一实参必须是**函数引用**，
  此时关键参数要出现在转发关键字里）。`obj.method(...)` 一律不认（接收者静态判不出）；
  `*`/`**` 解包放过。**已知漏判面**（不做控制流分析、`import *` 不解析、`importlib` 动态取函数不在射程）
  写在脚本 docstring 里，不写成"已全覆盖"。
- 现场豁免的形状是 `# param-injection: exempt(理由)`（调用行或紧邻上一行），**理由不能空**：
  空理由的豁免和没有门禁没区别，且会随时间变成无人敢删的注释。M-5 就是把理由删空验证它还红。
  本批从 `store-injection` 改名成 `param-injection`，并留一条 `test_old_marker_name_does_not_clear`
  ——改名后旧前缀**不再作数**，豁免必须能被一次 grep 数干净。

### 两处现场豁免不是"绕过"，是"这一面确实没有存储根"

`af_vhass/dual_track.py:80,81` 的 `simulate_track("fake"/"hifi", …)`：`simulate_track` 的 `store`
只用来回填报告里的 `root` 字段，双轨对拍是纯仿真面、拿不到也不需要存储根。留空是"没这一栏"，
不是"验过了"——所以标记写的是理由，不是 `noqa`。

### 能变红实测（铁律 #8，五处变异各钉一次）

```
sha256(工作区): {'af_mcp.py': 'bed6d41c30d7a228', 'dual_track.py': 'b19a6d64eadf3fcd', 'af_cli.py': '6d8183355a31bb82'}
M-1 `_t_health` 退回 `svc.health()`        [af_mcp.py newline=CRLF]
    gate  RC=1 | ✗ 参数注入门禁发现 1 处漏传（扫描 96 个文件）：af_mcp.py:102 `af_service.health` … `store` 形参
    test_af_mcp::…probes_the_store_it_serves  RC=1 | 1 failed in 2.36s
    test_param_injection_gate::test_repo_src_is_clean  RC=1 | 1 failed in 2.71s
M-2 `_t_live` 少递 `store=store`（§二之十六 原缺陷形状）  gate RC=1 | af_mcp.py:259 `af_service.live_run` … `store`
M-3 `serve` 少递 `readonly=readonly`（泛化的牙齿）        gate RC=1 | af_cli.py:1376 `af_api.build_app` … `readonly`
M-4 删掉 dual_track 的豁免标记            gate RC=1 | dual_track.py:80 `af_service.simulate_track` … `store`
M-5 豁免理由写空 `exempt()`                gate RC=1 | dual_track.py:80 同上（空理由不认）
restored identical af_mcp.py: True / dual_track.py: True / af_cli.py: True
DRIVER_RC=0
```

### 这批自己踩到的两个坑，比判据更值得留下

1. **变异驱动用 `git checkout` 恢复 = 自毁**。上一版驱动恢复步骤写的是 `git checkout -- <file>`，
   而这一批要恢复的是**未提交**的工作区改动 ⇒ checkout 把 `_t_health` 的修复一起抹回了 HEAD。
   抓到它的是驱动末尾那句 `assert digest(path) == orig[path]`，报 `AssertionError: 恢复失败：af_mcp.py`；
   按记录的 sha256 重新落回同一份字节（`bed6d41c30d7a228`）才继续。**口径**：驱动一律用内存字节备份恢复，
   不碰 git。
2. **编辑工具把整份 CRLF 文件重写成 LF**。`af_mcp.py` 入库是 CRLF，一次编辑后
   `git diff --numstat` 报 **929/927**——不是"改了 3 行"，是全文件换行被换掉。本仓无 `.gitattributes`
   且 `core.autocrlf=false`（§二之十六 记过一次），所以这种污染**不会让任何东西变红**，只会污染 blame。
   修法与验证：从 HEAD 取回、按 `\r\n` 锚点重落那一处，`git diff --numstat` 回到 **3/1**，
   并逐文件确认 `CRLF pairs 929 / bare LF 0`。**推论给判据用**：变异驱动的锚点必须按各文件实际换行
   匹配，且匹配数 != 1 就中止——LF 锚点打在 CRLF 文件上会匹配 0 处，驱动会"没改动、照样绿"地假绿。

### 本批全链读数（HEAD `4a3e3b1`，全部当场跑出）

- 门的首跑（HEAD `a94ca71` + 未修 `_t_health`）：**RC=1** 三处命中
  （`af_mcp.py:100` 真缺陷 + `dual_track.py:78,79` 合法），修完 + 加标记后：**RC=0**
  `✓ 参数注入门禁干净（扫描 96 个文件，96 个带 store/readonly 的模块级函数，现场豁免 2 处）`
- `python -m pytest -q` → **RC=0**，`2681 passed, 51 skipped, 1 warning, 7 subtests passed in 87.66s`
  （2658 → +23：门禁反例 22 条 + Agent 面健康判据 1 条）
- `GATES_PYTHON=python bash gates.sh` → **RC=0**：AST 0 新增/基线 104、棘轮 104/104、
  undefined-name src 96 + tests **157** 文件（本批多一个测试文件）、主题白名单 7、包标记 96、
  状态源扇出 96、**参数注入 96 文件/96 签名/豁免 2**、import 冒烟 0
- 提交两笔：`9a83ead`（门禁 + `_t_health` 修复 + 判据，6 文件）与 `4a3e3b1`（同批把门按参数表泛化，
  含脚本/测试改名与标记前缀改名）；`docs/audit` 仍无第八轮，`decisions` 无新裁定

---

## 二之十九、"名单手抄"这一族扫掉：`check_tool_names.py` 与它删掉的两处死映射（`743aadf` + `c7b97b9`）

§六 挂了四批的最后一条同族缺口是「**TOOLS→caps 之外有没有第二次工具名单映射**」——它和
§二之十四/十五/十六/十七 那几条一样，**不会让任何东西变红，只会让该红的不红**。本批盘点并做成门禁。

**为什么这条不能靠 grep 回答**（两种口径当场各跑一次，读数量级差一个数量级）：
- 全文正则扫 `af_[a-z][a-z0-9_]*`：src 里 **108** 个不同串，其中 **77 个**不在 `TOOLS` 的 31 个名字里。
  77 个几乎全是**模块名**（`af_store`、`af_ir`、`af_time`、`af_live.py` 本身就是个模块）——前缀一视同仁，
  噪音淹掉信号。
- AST 只取**字符串常量**且整串是工具名形状（`fullmatch`）：**34** 个去重，**3 个**不在 `TOOLS`：
  `af_action_dispatched`（`af_intervention.py:180`）、`af_caused`（`af_health.py:607`、
  `af_intervention.py:41`）、`af_local`（`af_time.py:238`）——三个都是**指标/标签名**，不是工具名。
  删除前那份读数是 40 去重 / 9 不在注册表，多出来的 6 个正是下面两处映射。

**盘到的两处真映射（都是第二份名单，都不是运行时错误）**：
1. `af_orchestrator.observe()` → `self._call_safe("af_live", id=…, duration=…)`，而注册名是
   **`af_live_run`**。`_call_safe` 把异常吞成 `{"ok": False}` ⇒ 这条"观察真机"的路径**永远不会响**，
   src/tests 两侧无调用方，所以它既没报错也没被任何测试抓到过。
2. `af_runtime_ext.mcp_tools()` 另抄一份 5 个 `af_*` 名字的字典（`af_list_proposals` /
   `af_approve_proposal` / `af_reject_proposal` / `af_export_feedback` / `af_pretrigger_status`），
   **从未被任何调用方接线**。其中 `af_approve_proposal` 是"Agent 自己批准提案"的写面，与裁定
   20261002 §三 ④A「洞察只落盘、**人批后**才进可执行队列」以及 `af_api.py` 里那条
   "approve/reject 只在服务层，MCP 面绝不注册"正面冲突—— orchestrator 侧甚至有一道
   `if "approve" in str(tool).lower(): raise OrchestratorError` 的硬闸。

**处置：删除，而不是接线**。三条理由：① 两处都是死代码，接上线等于**新造一个写面**，那不是修缺陷；
② `mcp_tools()` 里含 approve 写面，把它接进 MCP 就是自行推翻 ④A，属裁定面；③ 删除是
`0 3 / 1 14` 的净删行，单提交 revert 即可回退，没有任何测试依赖它。判据留下，防止日后**再抄第三份**。

**门禁口径**（`scripts/check_tool_names.py`，纯标准库 AST）：
- 唯一真源 = `af_mcp.py` 里 `TOOLS: list[tuple[...]]` 每个元组的第一个字符串常量（当前 31 个）。
  **读不到锚点就 exit 2**——§二之十四 学过"锚点消失时报 0 处发现就是假绿"。
- 按名调用的字面量必须命中注册表：调用面白名单 `_call` / `_call_safe` / `call` / `dispatch`
  的第一实参、`submit_pending` 的第二实参、以及 `tool=` 关键字。
- 形参默认值只在**形参名是工具形状**（`tool` / `*_tool`）时判；`topic="af_automation_fired"` 这种不判。
- `TOOLS` 之外出现 ≥2 个 `af_*` 键的字典字面量 = 第二份名单，直接红（`af_mcp.py` 自己是注册表，不收这条）。
- 现场豁免 `# tool-name: exempt(理由)`，理由不能空；隔壁门的 `param-injection:` 标记不作数。

**不误响**：第一版 `_literal_tool` 没查调用面，把 4 处日志文案判成工具名——
`logger.warning("af_persist: 落盘记录校验和不匹配…")`、`af_watch 聚合冲突证据失败…` 等。
修法是"调用面在名单里 + 整串 fullmatch 工具名形状"两道同时成立才判，并用
`test_logger_text_is_not_judged` / `test_ha_service_name_is_not_judged` /
`test_variable_tool_name_is_not_judged` / `test_non_tool_parameter_default_is_not_judged` 钉住。

**变异读数**（驱动 `mutate_tool_names.py`，五个形状一次一处、每次跑门再从**内存原始 bytes** 写回）：
```
[基准] RC=0
✓ 工具名单门禁干净（扫描 96 个文件，注册表 31 个工具，按名调用点全部命中，现场豁免 0 处）

[M-1 observe() 贴回 af_live] RC=1（期望 1）
      src/autoforge/af_orchestrator.py:2319: 按名调用 `af_live`，但 `af_mcp.TOOLS` 里没有这个名字
[M-2 mcp_tools() 第二份名单贴回] RC=1（期望 1）
      src/autoforge/af_runtime_ext.py:287: 这里是第二份工具名单（af_approve_proposal、af_export_feedback、af_list_proposals）
[M-3 调用点改名 af_health -> af_hea1th] RC=1（期望 1）
      src/autoforge/af_orchestrator.py:2247: 按名调用 `af_hea1th`，但 `af_mcp.TOOLS` 里没有这个名字
[M-4 TOOLS 锚点改名（读不到注册表）] RC=2（期望 2）
      ✗ 工具名单门禁读不到注册表：没找到 `TOOLS: list[...]` 的列表字面量
[M-5 未注册名 + 就地豁免（正控制）] RC=0（期望 0）
      ✓ 工具名单门禁干净（… 现场豁免 1 处）
    恢复：三个文件与基准逐字节一致 ✓   （×5 次）
[收尾复跑] RC=0
```
**驱动 v1 自己踩的坑要记账**：v1 把四处 `patch()` 写在**循环之前**，所以 M-1/M-2/M-3 跑的时候 M-4
早就把 `TOOLS` 改了名 ⇒ 四条读数**全是 RC=2「读不到注册表」**，看着像"门很灵敏"，实际一条本职判据都没验到。
改成"一次一处、跑完即还原"才拿到分档读数。**教训：变异出现"全红同一句"不是门很严，是驱动没隔离变量。**

**顺带抓到自家门禁的一处真缺陷**（§二之十八 那批我自己写的、只在门判红时才走到的分支）：`gates.sh`
的结论行写在双引号里却用了**裸反引号** ⇒ bash 当命令替换执行。实测：
```
bash -c 'echo "签名里有 `store`/`readonly` 的函数"'
bash: line 1: store: command not found
签名里有 /declare -r BASHOPTS="checkwinsize:cmdhist:…（整个 shell 环境被 dump 出来）… 的函数
```
`readonly` 是 bash 内建，会把**全部 shell 变量**打进结论行；`store` 则是 command not found。三处
（参数注入 / 工具名单两条新分支 + 参数注入那条旧分支）已把反引号转义，红分支实跑复读：
```
结论：工具名单门禁读不到注册表（exit=2）。`af_mcp.TOOLS` 的锚点形状变了……报『干净』就是假绿……
结论：工具名单门禁红（exit=1）。按名调 MCP 工具只能用 `af_mcp.TOOLS` 里的名字……
```
这是铁律 #8 的另一面：**红路径自身也要读一次**——一条从没走过的输出分支和一条从没写过的判据一样不可信。

**`TOOLS→caps` 那一半的答案：caps 不是第二份名单**。`af_mcp.py:812` 解包的是
`_name, _desc, _schema, fn, scope = tool`——scope/caps 是 **`TOOLS` 每个元组的第 5 项**，
和名字同源，不存在"改名时 caps 那边不知道"。所以这一半无需第二真源，登记为已盘。

**同批将门扩到集合/列表/元组（`c7b97b9`）**：上一版只判字典键，但"手抄枚举"这一族不只长成像字典——
`WRITE_TOOLS = {"af_save", "af_live_run"}` 这种集合/元组同样是第二真源。扩之前当场盘 src：
容器字面量里 ≥2 个工具名形状字符串的形状 **0 处**（今天没有 ⇒ 扩过去不会误响）。变异 M-6 实测：
```
[M-6 集合/元组式第二份名单贴进 af_orchestrator] RC= 1
  src/autoforge/af_orchestrator.py:2618: 这里是第二份工具名单（af_live_run、af_save）——…
  src/autoforge/af_orchestrator.py:2619: 这里是第二份工具名单（af_health、af_live_run）——…
还原逐字节一致 = True
[收尾复跑] RC= 0
```
反例 +3 条（集合红 / 元组红 / 只有一个工具名的列表不红）。**`bytes` 字面量只能 ASCII 这个坑本批又踩一次**
（驱动里写 `anchor=b'\n\n# ---------- 内部流程 ----------'` 直接 `SyntaxError`）——§二之十八 记过，
记了还会踩，说明它得靠"写完立刻跑一遍"而不是靠记忆兜。

**CI 复测（`743aadf`+`b1f0ec4` 那次 push）**：run 46（`d992f29`）`completed/success`；
**run 47（`b1f0ec4`）`quality-gates` 与 `ui-typecheck-build` 已 `completed/success`**，runner（Python 3.11）
正文逐字读到新门那两行：
```
2026-10-03T03:34:25.7364258Z ══ 工具名单门禁（MCP 工具名只有一个注册表）═══════════════════════
2026-10-03T03:34:26.1934643Z ✓ 工具名单门禁干净（扫描 96 个文件，注册表 31 个工具，按名调用点全部命中，现场豁免 0 处）
```
——与本机 §二之十九 的读数**逐字相同**（96 文件 / 31 工具 / 豁免 0），新门不是只在本机生效的软门。
`pytest`/`adm-linkage-contracts`/`layering-gates` 三作业读数写作时仍 `in_progress`，取到结论后另记。

**本批全链读数**：
- `python scripts/check_tool_names.py src` → **RC=0**，
  `✓ 工具名单门禁干净（扫描 96 个文件，注册表 31 个工具，按名调用点全部命中，现场豁免 0 处）`
- `python -m pytest -q`（`743aadf` 那次）→ **RC=0**，
  `2703 passed, 51 skipped, 1 warning, 7 subtests passed in 101.45s`（2681 → +22：门禁反例 22 条）
- `python -m pytest -q`（扩到集合/元组之后，`c7b97b9`）→ **RC=0**，
  `2706 passed, 51 skipped, 1 warning, 7 subtests passed in 89.08s`（再 +3：集合红 / 元组红 / 单名列表不红）
- `GATES_PYTHON=python bash gates.sh` → **RC=0**：AST 0 新增/基线 104、棘轮 104/104、
  undefined-name src 96 + tests **158** 文件、主题白名单 7、包标记 96、状态源扇出 96、
  参数注入 96/96/豁免 2、**工具名单 96 文件/31 工具/豁免 0**、import 冒烟 0
- `git diff --numstat`（删除前后各读一次）→ `0 3 af_orchestrator.py` / `1 14 af_runtime_ext.py`，
  两个文件都是 LF 文件、净删行为无异常；`gates.sh` 仍 `CRLF 0`
- 提交两笔：`743aadf`（门禁 + 两处死映射删除 + 22 条反例 + `gates.sh` 新节与红分支转义）与
  `c7b97b9`（同族扩到集合/列表/元组 + 3 条反例 + caps 同源这一条盘成读数）

---

## 二之二十、"实现间契约不一致"补上静态门禁：`check_snapshot_policy.py`（`d7d1fff`）

§六 挂着一行「`BoundedCache` 基类收敛（第五轮遗留）与"实现间契约不一致"的**静态**门禁：本轮以契约
测试覆盖，未做静态门禁」。这一行把**两件不同的事写在同一行**，本批拆开：

**(1) `BoundedCache` 那一半不是"未做的活"，是审计自己判过"不做"的建议。** 复核证据（不是凭印象）：

- `git log --oneline -S"BoundedCache" --all -- src tests` → **0 命中**；`grep -rn BoundedCache src tests` → 空。
  这个名字从未进过代码，因为它从一开始就是**建议**：`docs/audit/AF审计第六轮/REPORT.md:47`
  写的是"建议引入统一的 `BoundedCache` 基类"。
- 第六轮核实报告 §三 把它归入**「建议（不当缺陷处理）」**并给了不做的理由（`审计报告_第六轮_核实与修复.md:70`
  起）：四处现状已是"超时 + 硬上限"两条腿、抽基类要动 5 个模块的存储层而它们各挂持久化格式，
  "统一基类的收益在可观测的内存曲线上看不出来，风险在持久化兼容上"；并明写"如果 DCD 认为应当升级为
  硬要求（统一基类 + 门禁扫描无界容器），那是一次跨模块重构"。
- 第七轮核实报告再次确认不判红：`审计报告_第七轮_核实与修复.md:86` →
  "报告把 `BoundedCache` 抽象列为建议而非缺陷，本轮同样不当缺陷处理（**不为此加基类**，见第五轮处置记录）"。

  ⇒ "加基类"这一半**已裁不做**，AF 侧不再排期；但同一句里的"**门禁可见**"那一半确实没做：
  `grep -rn "有界\|TTL" gates.sh scripts/*.py` → **零命中**，即"新增有界缓存必须同时给 TTL 与硬上限"
  目前只是写在审计报告正文里的约定，没有任何能判红的东西。这一条已单列进 §六（要不要升级为硬要求
  是第六轮原话里留给 DCD 的问题，AF 不自决）。

**(2) "实现间契约不一致"的静态门禁——本批交付。** 契约是第七轮那条：`StateProvider.snapshot()`
必须在取快照这一刻对未知实体 `raise UnknownEntity`。当时用 `tests/contract/test_state_provider_policy.py`
把四个实现钉在同口径上，但这批测试有个**结构性缺口**：每条用例只喂自己认识的那个 provider，
所以"新增一个 `XxxStateProvider` 忘了 raise"这个**唯一的真实失败模式**一条都不会红。
静态门覆盖的正是"下一个实现"。

射程盘点（`src` 全集，非抽样）：类级 `def snapshot` 共 **11** 个站点——

| 站点 | 类 | 返回标注 | 体里出现 `Snapshot` | 在射程 |
|------|----|----------|--------------------|--------|
| `af_state.py:103` | `StateProvider`（Protocol 纯声明） | `Snapshot` | 是 | 跳过（无实现可判） |
| `af_state.py:124` / `ha.py:195` / `bridge.py:35` / `fake.py:75` / `high_fidelity.py:152` | 五个实现 | `Snapshot` | 是 | **是** |
| `af_catalog.py:1102`、`af_metrics.py:30`、`af_tick_supervisor.py:124` | `DeviceCatalog`/`MetricsAggregator`/`TickHealth` | `dict[str, Any]` | 否 | 否 |
| `af_health.py:790` | `HealthEngine` | `list[dict]` | 否 | 否 |
| `af_version.py:523` | `VersionManager` | `Version` | 否 | 否 |

模块级的 `af_version.py:823 snapshot(...) -> Version` 也在射程外（门判的是**类里的**状态源实现）。

**判据取两条信号的并集，而不是只看标注——这是被自己的反例打出来的。** 第一版只按 `-> Snapshot` 判，
16 条反例里三条本职判据当场不红：

```
FAILED tests/unit/test_snapshot_policy_gate.py::test_provider_that_silently_drops_unknown_is_red
FAILED tests/unit/test_snapshot_policy_gate.py::test_provider_that_invents_domain_default_is_red
FAILED tests/unit/test_snapshot_policy_gate.py::test_raising_the_wrong_exception_is_red
3 failed, 11 passed in 1.97s
E       assert 0 == 1
E        +  where 0 = len([])
```

红的不是产品代码，是**判据本身漏了**：Python 的返回标注是可选的，不写标注的 fail-open 实现静态上
就隐形。而这**不是假想题**——本仓自己就存着一个：`tests/contract/test_state_provider_policy.py`
里的反面样本 `_FailOpenProvider.snapshot(self, entity_ids)` 没有返回标注、却构造 `Snapshot`
（它就是被写出来证明契约测试能判红的那个类）。所以射程改成
"标注 `-> Snapshot` **或** 方法体出现 `Snapshot` 这个值类型"。诚实记账一句：并集**在今天的 src 上是
零增量的**（盘点表里两列完全重合，"只靠体里造 `Snapshot` 才进射程" = `[]`），它买的是"下一个实现
忘了写标注"那一种，不是修当前读数。为把它钉住，反例里加两条：
`test_annotation_only_provider_is_in_scope`（只有标注、快照由工厂造）与
`test_repo_own_fail_open_sample_is_in_scope`（直接对**本仓那个文件**跑 `_findings`，
断言 `[类名] == ["_FailOpenProvider"]` 且"没有 raise"判为真）。

其余口径与 `check_tool_names.py` 同一族：

- **锚点读不到 → exit 2**（`StateProvider.snapshot() -> Snapshot` 的纯声明）。签名改名会让射程判据
  整体失效，此时报"干净"就是假绿（§二之十四 那一课的同一形状）。
- **豁免单独计入读数**：绿色行原样写"5 个实现全部对未知实体抛 `UnknownEntity`"，而实际其中一处可能
  是靠 `# fail-closed: exempt(理由)` 过关 ⇒ 把未验证的算成已验证（铁律 #5）。改成
  `抛 UnknownEntity 5 / 现场豁免 0` 两段读数，`exempted` 从 `check()` 里单独返回。

**六档变异（一次只改一处，从内存字节恢复）：**

```
[OK ] M-0 HEAD 基线: RC=0 (期望 0)
      ✓ 状态源 fail-closed 门禁干净（5 个状态源 `snapshot()` 实现：抛 `UnknownEntity` 5 / 现场豁免 0）
[OK ] M-1 有标注 + 静默省略 ⇒ 红: RC=1 (期望 1)
        src/autoforge/_snap_probe.py:5: `SilentDrop.snapshot()` 是状态源（标注 `-> Snapshot` 或造出 `Snapshot`）却没有任何 `raise UnknownEntity(…)`——…
[OK ] M-2 无标注 + 造 Snapshot ⇒ 红（并集射程）: RC=1 (期望 1)
        src/autoforge/_snap_probe.py:5: `NoAnnotation.snapshot()` 是状态源（标注 `-> Snapshot` 或造出 `Snapshot`）却没有任何 `raise UnknownEntity(…)`——…
[OK ] M-3 带理由豁免 ⇒ 绿（阳性对照）: RC=0 (期望 0)
      ✓ 状态源 fail-closed 门禁干净（6 个状态源 `snapshot()` 实现：抛 `UnknownEntity` 5 / 现场豁免 1）
[OK ] M-4 Protocol 改名 ⇒ RC=2: RC=2 (期望 2)
      ✗ 状态源 fail-closed 门禁读不到锚点：没找到 `StateProvider.snapshot()` 的声明（改名/挪走会让本门静默全绿）
[OK ] M-5 契约返回标注改掉 ⇒ RC=2: RC=2 (期望 2)
      ✗ 状态源 fail-closed 门禁读不到锚点：`StateProvider.snapshot` 找到了但返回标注不是 `Snapshot`（契约签名变了，本门的射程判据也就失效了）
恢复后 af_state.py 与内存字节一致： True
```

M-1/M-2 是**同一件事的两种写法**（有标注/无标注），缺一不可：只测前者，判据退化回"只认标注"也照样全绿。
M-3 那一行同时是"豁免单独计数"的读数证据：总数从 5 变 6、抛的仍是 5、豁免那 1 处**写在绿行里而不被算成已验证**。

**第二版第一次跑驱动器时 M-0 与 M-3 双双 BAD（RC=1，基线就不绿）**，原因是我改 `check()` 返回三元组时
把末尾的 `return findings, implemented` 留在了原地。这条值得记：**变异档里必须有一条"什么都不改"的
M-0**——它管的是"门自己是不是坏的"，而这恰恰是解包类错误唯一会露馅的地方（ pytest 侧同批报
`ValueError` 两条，两处读数是同一次错误的两面）。

**红路径也真跑了一次**（§二之十九 记的裸反引号教训，这次是先防）：往 `src/autoforge/` 贴一个 fail-open
探针后 `GATES_PYTHON=python bash gates.sh` → **RC=1**，新节与结论行原样：

```
══ 状态源 fail-closed 门禁（snapshot() 不许静默省略未知实体）══════
✗ 状态源 fail-closed 门禁发现 1 处（扫描到 6 个状态源 `snapshot()` 实现，另有 0 处现场豁免）：
  src/autoforge/_snap_probe.py:5: `ProbeFailOpen.snapshot()` 是状态源（…）
结论：状态源 fail-closed 门禁红（exit=1）。返回 `Snapshot` 的状态源必须对未知实体 `raise UnknownEntity(…)`：`and`/`or` 短路 ⇒ …
```

反引号在双引号串里已逐处转义，输出里是字面 `` `Snapshot` `` 而不是上一次那种把 shell 环境 dump 出来。
探针文件删掉后复跑为绿。

**全链读数（本机 `Python 3.13.2`，当场实跑）**

- `python -m pytest tests/unit/test_snapshot_policy_gate.py -q` → **RC=0**，`16 passed in 1.55s`
- `python -m pytest -q` → **RC=0**，`2722 passed, 51 skipped, 1 warning, 7 subtests passed in 93.00s`
  （`c7b97b9` 那批是 2706 ⇒ +16 全在本文件）
- `GATES_PYTHON=python bash gates.sh` → **RC=0**：AST 0 新增/基线 104、棘轮 104/104、
  undefined-name src 96 + tests **159** 文件（新测试文件让 tests 侧从 158 进 1，符合预期）、
  主题白名单 7、包标记 96、状态源扇出 96、参数注入 96/96/豁免 2、工具名单 96/31/豁免 0、
  **状态源 fail-closed：`✓ …（5 个状态源 snapshot() 实现：抛 UnknownEntity 5 / 现场豁免 0）`**、import 冒烟 0
- 行尾：`gates.sh` / 两个新文件均 `CRLF: 0`；`git diff --numstat` → `19 0 gates.sh`（只加不改）
- 提交一笔：`d7d1fff`（门禁脚本 + `gates.sh` 新节与两条红分支 + 16 条反例），产品代码一个字节未动

**runner 侧正文读数（run 49 = `d7d1fff`，五作业逐条 `completed/success`、`failed_steps` 全空）**

- `quality-gates` 作业日志里 `fail-closed` 关键词命中两行，第二行与本机 §二之二十 的绿行**逐字相同**：
  `✓ 状态源 fail-closed 门禁干净（5 个状态源 \`snapshot()\` 实现：抛 \`UnknownEntity\` 5 / 现场豁免 0）`
  （runner Python 3.11 / 本机 3.13.2 —— 纯标准库 AST 判据两版同口径，不是只在本机生效的软门）
- `pytest` 作业正文：`2722 passed, 51 skipped, 1 warning in 81.27s (0:01:21)`
  ⇒ 通过/跳过数与本机 **逐字相同**（2722/51），只有墙钟不同（81.27s vs 本机 93.00s）
- 其余三作业（`adm-linkage-contracts` / `layering-gates` / `ui-typecheck-build`）同 run 全绿；
  run 47（`b1f0ec4`）与 run 48（`7b98f28`）事后复查也已 `completed/success`
  ⇒ 连续绿从 §二之十九 记的 13 条延伸到 **16 条（run 34–49）**；
  其后两笔纯文档提交 run 50（`04fc402`）与 run 51（`0bae3af`）也已 `completed/success` ⇒ **18 条（run 34–51）**，
  run 52（`64f4935`，本行所属那批文档）取读数时仍 `in_progress`

## 二之二十之一、顺带把"有界缓存"那条约定的静态口径做到有读数（未做门，已投 DCD）

§六 那条残留拆开的第二件不是"顺手记一句"，而是先把"能不能做成门"问到有读数为止：

| 判据 | 实测 |
|---|---|
| 约定的"门禁可见"那一半 | `grep -rn "有界\|TTL" gates.sh scripts/*.py` → **零命中** |
| 天真静态口径（AST：类里 `self.X = {}/[]/deque/dict/list`，且同类内出现 `X[k]=`/`append`/`update`/`setdefault`/`add`） | src 全集 **7 个候选**；其中 **5 个**同类内已有界证据（`ConflictAuditor.events`、`JsonFireStore._records`、`InstanceManager.context`、`PreferenceModel._records`、`UndoStore._records`）——与第六轮"四处已是超时+硬上限两条腿 + 本批 `_SESSIONS`"的账一致 |
| 该口径报"缺界"的那 2 个，逐条读码 | ①`af_pretrigger.py:218 PreTriggerService._stats`：固定键计数器（七个键只累加值）；②`af_vhass/device_sm.py:68 DeviceSM.attributes`：单实体属性字典，键集由该 domain 的属性词表决定，`reset()` 整体替换 ⇒ **真阳性 0/2** |
| 结论 | "扫无界容器"静态上**判不出可靠口径**（Python 的"有界"常长在键空间或调用方，不在容器自身）。做成 CI 硬门第一天就带两条永久红，只能挂豁免表 ⇒ 与 §二之十九 刚扫掉的"第二份名单"同形 |

⇒ 三档 A（清单 + 成对断言测试）/ B（注册表式门，AF 建议）/ C（统一基类 + 扫描，跨 5 模块存储层）
已投 `关键决策部/inbox/20261003-AF-有界缓存生命周期约定要不要升级为硬门禁-决策申请.md`
（§五 第 11 件），**AF 侧代码一个字节未动**。同批另把 `af_vhass` 的"未建模服务"那面盘了一遍：
`is_modeled()` 由 vhass 与 FakeHA **共用同一判据**（`fake.py:212`），未建模动作经
`unmodeled_actions` 单列一档进诚实报告（`af_expect.py:310-329` 还会把"间接触发已展开 effects"的
移出 unmodeled），不是静默 OK ⇒ **这一面不是新接缝**，登记为已盘。

---

## 二之二十二、出向载荷与契约表对账：`failed` 的 `error` 一直在发状态名（`5ff1ea6`），`trace_id` 的语义去问 DCD

第 7 件把 **入向**（`ma/insights` → AF）对完了，本批对**出向**（AF → DB）那两面。对着契约表 §1.2 两行逐字段读码：

| 字段 | 契约 | AF 现状（读码，非推测） | 定性 |
|---|---|---|---|
| `af/automation/fired` = `{trace_id, ts, automation_id, ref}` | 消费方"DB（告知用户）" | `_envelope()` 全给，`ref`=实例 id（裁定 ②A）、`ts` 家庭墙钟 ⇒ **符合** | — |
| `af/automation/failed` 多一个 `error` | 消费方 DB | `observe_terminal()` 传的是 **`error=state`**，而它只在 `state == "failed"` 分支里被调用 ⇒ **每条失败事件的 `error` 恒等于字符串 `"failed"`** | **真缺陷，AF 自决修**：字段名与类型都没动，只是把契约定义的那个东西装进去 |
| 两条的 `trace_id` | §1.2 只列字段名；§1.3 护栏第 4 条"必填、贯穿'洞察→收件箱→播报'" | 每次发布现场 `uuid4().hex[:12]` ⇒ 同一实例的 fired 与 failed 也不是同一枚号 | **不是缺陷，是 §1.2 没写清**（见下） |

### `error`：原因一直存在，只是没被递到接缝上

- 写侧一直在写：`af_instance.py:287` `instance.ctx.context["fail_reason"] = reason`，由
  `InstanceManager.fail()` 在 `_transition(FAILED)` **之前**调用 ⇒ 观察者拿到的实例上必然已就位。
- 内容是真原因，不是日志点缀：`af_executor.py` 每条 `_fail()` 都带语义 —— `:123` 段步数上限、
  `:605` "`{action}` 失败且无 on_error/default 兜底：`{result.error}`"、`:625` 软失效无兜底（含异常本身）、
  `:662` 收敛纪律违反。也就是说 **DB 唯一能向用户显示的那句话，此前被换成了 `"failed"`**。
- 修法（`5ff1ea6`）：桥读 `ctx.context["fail_reason"]`；读不到（无 `ctx` 的鸭子类型、空串、纯空白）
  发 `NO_FAILURE_REASON = "未记录失败原因（执行链未写入 fail_reason）"` —— **诚实占位，不拿状态名冒充原因**；
  封顶 `MAX_ERROR_CHARS = 500`，取契约表 §1.3 给展示类文本已经定过的那个数（原因里会拼异常 repr 与真机回执，
  不封顶等于把任意长度正文塞进 QoS 1 事件流）。跨模块键名不做第二份真源：两侧都走 `InstanceManager.fail()`
  这条写路，测试从真状态机产出实例（见下 M-2）。

### 变异读数（四档，全部从内存字节还原、收尾逐字节自证）

```
M-0 不改（基线：门自身必须是绿的） -> RC=0   | 36 passed in 0.23s
M-1 error 退回状态名               -> RC=1   | 2 failed, 34 passed in 0.98s
M-2 桥读的键名和写入侧不一致        -> RC=1   | 1 failed, 35 passed in 0.95s
M-3 error 不封顶                    -> RC=1   | 1 failed, 35 passed in 0.92s
还原后 -> RC=0 | 36 passed in 0.23s
结论： 全部符合预期
```

M-0 那行驱动打的是它对各档的通用标签"BAD(未抓到)"，对基线档的正确预期正是 **RC=0**（§二之二十 记过：
M-0 存在的理由就是抓"判据自己坏了"）。M-1 红 **两条**而不是零条，是这批想要的形状：一条正向（真原因穿到载荷）、
一条反向（退化成状态名即红）；M-2 是**跨模块改名**那条唯一的真实失败模式——桥把读键写成 `failure_reason`
即红，说明锁住了接缝而不是只锁住了字符串；M-3 证明 500 那个数不是装饰。

### 为什么这一件没升成静态门禁（同族的第 7 条判据）

`error=state` 与 `error=reason` 在 AST 上**同形**（都是"把一个局部变量递给某个关键字"），静态无从判定哪个是原因、
哪个是状态名——这与 §二之十八 的"参数递错"不同族：那门判的是**没递**（漏关键参数，静态可数），
这族判的是**递错**（值语义）。唯一能判红的是行为断言 ⇒ 三条测试各钉一次，不造一条只会误报的门。

### `trace_id`：AF 不动，去问裁定（§五 第 12 件）

三条实测决定了这不是 AF 可自决的一件：

1. §1.3 那句"trace_id 必填：贯穿'洞察→收件箱→播报'"挂在**收件箱三面**（`butler/inbox/speak|notify|tv`）的护栏下；
   `grep -rn "publish.*butler/inbox" src/autoforge` = **0**，`grep -rn "butler/inbox" src/autoforge` 的 6 处命中全是
   拒订判定 ⇒ AF 没有违反那条，问题是 §1.2 对事件面**只列了字段名**。
2. 上游那枚号在 AF 侧并没丢：`ingest_insight()` 把 `payload["trace_id"]` 收进 `hypothesis_id`（`af_mqtt_bridge.py:449`）
   → 提案落盘（`af_proposal.py:242`、`af_insight_queue.py:199`）→ approve 时写进 GraphStore 记录元数据
   `note="ma_insight:{hypothesis_id}"`（`af_service.py:552` → `af_store.py:232`，可经 `load_record`/版本列表读回）。
   **但它不进 IR、不进实例上下文**（`grep -rn hypothesis_id src/autoforge/af_ir/ …/af_instance.py …/af_executor.py` = **0**）
   ⇒ 发射点手上只有 `automation_id` + `instance_id`，拿不到那枚号。
3. 于是三种修法代价不同且都超出实现选择：沿用上游 = 同一字段两种语义、DB 的"按 trace_id 拉一屏日志"从 1:1 变 1:N；
   新增 `insight_trace_id` = 三方共读面；把口径写成"事件级、因果链靠 `ref`＋各自存储" = 只欠契约表一句话（AF 建议）。
   ⇒ 按 20261002 §一"契约面改动走裁定"投申请，**`_envelope()` 的 trace_id 生成方式一个字节未动**。

### 本批全链读数（HEAD `5ff1ea6`，全部当场跑出）

- `python -m pytest -q`：**2725 passed / 51 skipped / 7 subtests passed，RC=0（94.78s）**（较上批 2722 净 +3，即本批三条新判据）
- `GATES_PYTHON=python bash gates.sh`：**RC=0**，逐节同前（undefined-name src 96 + tests **159** 未变——本批没新增测试文件；
  主题白名单 7 处全在契约表内；参数注入 96/96/豁免 2；工具名单 96/31/豁免 0；状态源 fail-closed 5 实现/抛 5/豁免 0；计数棘轮 104/104）
- 分跑 `tests/unit/test_af_mqtt_bridge.py`：**36 passed RC=0**
- 文档/代码换行：`src/autoforge/af_mqtt_bridge.py`、`tests/unit/test_af_mqtt_bridge.py`、本记录 **CRLF: 0**；
  `git diff --numstat` = `23 1` / `58 0`（无整文件重写）

### 覆盖面与兼容面（两条"这改动会不会伤到谁"的实测）

- **每条 failed 事件都经过写原因那一层**：`grep -rn "_transition(" src/autoforge/af_instance.py` 里通向 `FAILED` 的只有
  `:288` 一处，而它就在 `InstanceManager.fail()` 内、且排在 `on_terminal()` 之前 ⇒ 不存在"进了 failed 但没写 `fail_reason`"
  的第二条路（`expire()` 走 EXPIRED、`cancel()` 走 CANCELLED，两条都不发事件——契约 §1.2 也没有 cancelled 主题）。
- **AF 内部没有任何东西读这个字段**：`grep -rn "af/automation/failed\|FAILED_TOPIC"` 除发布点外只剩
  `scripts/check_topic_whitelist.py:36`（白名单本体）与 `af_cli.py:1336`（启动文案），消费方只有 DB ⇒
  把值从状态名换成真原因不会撞任何自家判据。**对端**的展示宽度是未知的，已在 §五 第 12 件里向 DB 问一句（不属裁定，只需回个数值）。

### 顺带盘出的第三个差额：观察者路径多发一个契约外的 `node_id`（`159ba00`，只加测试）

对完 `error` 之后，我把**真实发出去的载荷**打了一遍（不是读代码猜），读到的是：

```
af/automation/fired  ['automation_id', 'instance_id', 'node_id', 'ref', 'trace_id', 'ts']
af/automation/failed ['automation_id', 'error', 'instance_id', 'node_id', 'ref', 'trace_id', 'ts']
```

契约表 §1.2 那两行只有 `{trace_id, ts, automation_id, ref}`（failed 多 `error`），裁定 20261002 §三 ②A
**登记过**的差额是 `instance_id`（同值过渡字段，v2.6 删）——而 `node_id` **既不在契约行也不在任何裁定里**。

- **为什么一条"逐字段对契约"的测试没抓到它**（这条是本节真正的教训）：老用例调的是
  `publish_fired()` / `publish_failed()`，也就是**直接调用路径**；生产环境唯一发事件的路径是
  `Runtime.add_terminal_observer` 注册的 `observe_terminal()`，它自己多带一个 `node_id=node_id`
  （`af_mqtt_bridge.py:373/376/382`）⇒ 测试绿了很久，而它对的是**另一条没人在走的路**。
  按铁律 #5 这就是"测到 ≠ 覆盖到"，所以补的判据**必须从 `observe_terminal()` 走**。
- **补的判据**：按真实例（`InstanceManager.spawn` + `.fail`）断言观察者路径的**键集合**——
  fired 钉成 `{trace_id, ts, automation_id, ref, instance_id, node_id}`，failed 钉成"fired 全集 + `error`"，
  并顺手验 `node_id` 这个多发键确实等于 `instance.current_node_id`（不许它是个空壳键）。
- **四档变异（同一份内存字节还原，收尾 `逐字节一致=True`）**：

  ```
  M-0 基线（必须绿）        | RC=0 | 37 passed in 0.24s
  M-1 fired 少发 node_id    | RC=1 | 1 failed, 36 passed in 0.97s
  M-2 failed 少发 node_id   | RC=1 | 1 failed, 36 passed in 0.97s
  M-3 悄悄多发一个没登记的键 | RC=1 | 2 failed, 35 passed in 0.94s
  还原后 逐字节一致=True     | RC=0 | 37 passed in 0.23s
  ```

  M-3 一次红**两条**：`test_observer_path_key_set_is_pinned_including_the_undeclared_one` 与
  `test_event_envelope_matches_adm_contract_table`（后者按名可查，实测 `--tb=no` 输出的两条 `FAILED` 就是它们）。
  也就是说直接调用面**早就**有等价约束（它把信封钉成 `{trace_id, ts, automation_id, ref, instance_id}`），
  缺的只是观察者面那一半——`node_id` 是在 `observe_terminal()` 的调用点经 `**extra` 注进去的，
  那条老判据走的是不带 `extra` 的调用，所以两边都没错、只是没交集。
- **`node_id` 的语义本身也是个坑**（若裁定"留"就必须一并定义）：发出去的是 `instance.current_node_id`
  （`af_instance.py:150-151`，只是 `ctx.current_node` 的读数）。`fail()` 与 `_transition()` 都**不改**这个字段，
  它由执行链在跳边时推进（`af_executor.py:156`、`:252`、`:781`）⇒ failed 事件里的 `node_id` 是
  **"失败时刻实例停在哪"，不是"哪个节点失败"**，二者在推进后不必相等。AF 现在给的是前者。
- **这一件 AF 不自裁**（删字段 = 改三方共读面，按 20261002 §一 走裁定）⇒ 已作为**第 12 件的第三个问题**
  补进同一份申请（没有另开一件，因为它与 `trace_id` 是同一个"§1.2 载荷行没写清"的根），AF 倾向：**DB 若从不读就删**。
- 本小批读数：`python -m pytest -q` **2726 passed / 51 skipped / 7 subtests，RC=0（93.36s）**（较 `5ff1ea6` 那批净 +1），
  分跑 `tests/unit/test_af_mqtt_bridge.py` **37 passed RC=0**；`GATES_PYTHON=python bash gates.sh` **RC=0**
  （undefined-name tests **159** 个文件未变=无新测试文件；主题白名单 7 处、包标记 5 目录/96 文件、参数注入 96/96 豁免 2、
  工具名单 96/31 豁免 0、状态源 fail-closed 5/5/豁免 0、计数棘轮与 import 冒烟同前）；
  测试文件 `CRLF: 0`、`git diff --numstat` = `21 0`（纯增，无整文件重写）。

### CI 侧复查（run 52–55，含一次我自己推错分支的记账）

- **run 52（`64f4935`）与 run 53（`24ef0c0`）事后复查均 `completed/success`** ⇒ 上一版写的"连续绿 18 条（run 34–51）"
  收窄为过时，实际已到 **20 条（run 34–53）**。
- **run 54 与 run 55 都挂在同一个 `0a834b9` 上，54 是重复的一套**：根因是我用了 `git push -q origin master` ——
  本仓本地分支叫 `master`、GitHub 默认分支叫 `main`，那条命令在远端**新建**了 `refs/heads/master`，
  而 `.github/workflows/ci.yml:5` 的触发分支写着 `[main, master, dev]` ⇒ 多跑一套 CI，而 main 那时还没拿到这两个提交。
  纠正：`git push origin master:main` → `24ef0c0..0a834b9  master -> main`，**`PUSH_MAIN_RC=0`（`${PIPESTATUS[0]}`，不是 `tail` 的 0）**；
  随后 `git push origin --delete master`（该分支是我 90 秒前误建的、无任何引用）`DEL_RC=0`，
  删后 `git ls-remote --heads origin` 只剩 `refs/heads/main = 0a834b9` ⇒ 远端回到单分支原状。
  第一次打印的 `PUSH_RC=0` 属于管道尾巴，不是 push 的退出码——同批立刻改用 `${PIPESTATUS[0]}` 并补 `ls-remote` 自证。
- 取数时两套的状态：**54 与 55 的 `quality-gates` 都已 `completed/success`**、`failed_steps=[]`，
  55 那次 runner 正文逐行读到与本机同口径的六节绿行，末行 `结论：门禁干净。注意 import 通过不等于服务能起，验收仍要 compose ps + HTTP。`；
  `pytest`/`adm-linkage-contracts`/`layering-gates` 三作业两套都仍 `in_progress`（连续绿的计数**不**把这两套算进去，等结论落地再记）。
- **事后复查已落地（`gh_ci_status.py runs`，`RUNS_RC=0`）**：`success runs: [57, 56, 55, 54, 53, 52, 51, 50, 49, 48]`
  ⇒ run 54（误建分支那套重复）、55（`0a834b9` 在 main）、56（`f06b162`）、57（`b148574`）**四套全部 `completed/success`**。
  **按 main 计连续绿到 23 条（run 34–57）**，54 属同一提交在已删除分支上的重复套，单列不计入也不隐瞒（它同样绿）。
- run 57 五作业逐条 `completed/success`、`failed_steps` 全空（`quality-gates`/`layering-gates`/`pytest`/`adm-linkage-contracts`/`ui-typecheck-build`）；
  runner `pytest` 正文读到 **`2726 passed, 51 skipped, 1 warning in 80.41s (0:01:20)`** ⇒ 与本机 `python -m pytest -q`
  那次读数**逐字相同**（本机 93.36s / runner 80.41s，时间不同不作判据），新那条观察者键集判据在 CI 里被真实收集并跑绿。
  对照：run 56（上一个提交）正文是 `2725 passed, 51 skipped, 1 warning in 80.54s`，与 §二之二十二 上一版记录的本机数一致 ⇒ +1 的增量确实来自 `159ba00`，
  不是计数口径漂移。

---

## 二之二十三、把 §二之二十二 那个形状升成静态门禁：出向 MQTT 只有一个写者、事件只有一条生产者、载荷必经 `_envelope()`（`8b629b8`）

### 为什么这一件**能**升成静态门（与上一节那条"为什么不升"正好成对）

§二之二十二 记的那族判据是"值语义"——`error=state` 与 `error=reason` 在 AST 上同形，静态无从分辨，
只能靠行为断言。本节这一族判的不是值，是**调用图形状**：这次调用发生在**哪个文件的哪个函数体内**、
载荷实参**是不是 `_envelope()` 的产物**——这三问在 AST 上都有确定答案，所以能做成 CI 硬门。
同一条教训（"测试绿了很久，而它测的是生产不走的那条路"）能升成门，恰好说明**能不能升成门取决于判据的形状，
不取决于事情大小**。

### 三条判据（各自单独可红）

- **A 写者唯一**：`_mqtt.publish(…)` / `_presence.advertise(…)` 的调用点只允许出现在 `af_mqtt_bridge.py`。
  桥外自己拿 client 发 = 同时绕过 QoS、`ts` 家庭墙钟口径、`ref` 语义与"发布失败要留痕不冒到执行链"这四项，
  而主题白名单门只认字面量在不在契约表内，管不住载荷形态。
- **B 事件唯一生产者**：`publish_fired(…)` / `publish_failed(…)` 的调用点必须落在 `af_mqtt_bridge.py` 的
  `observe_terminal()` **函数体内**。归属取**最内层**包围函数——塞进 `observe_terminal()` 里的闭包也算第二条路径。
- **C 载荷必经信封**：桥内 `self._publish(<topic>, payload)` 的 payload 必须是 `self._envelope(...)` 的返回值
  （直接调用，或本函数内先由它赋值、再补 `error` 那种字段——`publish_failed` 现在就是这个形状）。
  **位置式与 `payload=` 关键字式都认**：只认位置参数等于留一条静默放行，而判据的红必须只在真违例时出现。
- **锚点**（读不到 `exit 2`，不做假绿）：模块级 `FIRED_TOPIC`/`FAILED_TOPIC` + 类里
  `_envelope` / `observe_terminal` / 两个 `publish_*` 必须在——三条判据全长在这几个符号上。
- **豁免** `# mqtt-writers: exempt(理由)`：理由不能空，且**单独计入读数**；绿色行的每个数字都是本轮实测计数，
  不写"只在/全部"这类没数过的断言（铁律 #5，反例 `test_green_line_prints_the_counts_not_adjectives` 钉住）。
- **射程边界**：`af_bus` / `af_runtime` 的 `publish()` 是进程内事件总线，不是出向 MQTT ⇒ 不判。
  按方法名一刀切会让本门天天红在无关代码上，最后被人当噪音跳过。

### 真实 src 的实测计数（本轮跑出，非引用上一节）

```
$ python scripts/check_mqtt_writers.py src
✓ 出向 MQTT 写者门禁干净（出向写者调用点 2 处、分布在 1 个文件；事件生产者 2 处、分布在 1 个文件；桥内 `_publish(topic, payload)` 2 处，其中载荷来自 `_envelope()` 的 2 处；现场豁免 0 处）
```

⇒ §二之二十二 盘出的那两条"路"在静态上确实是**一条生产者**：`src` 内 `publish_fired`/`publish_failed`
的调用点只有 2 处，且都在 `observe_terminal()` 体内（`af_mqtt_bridge.py:376/379`）。

### 反例与变异（门本身必须能红，铁律 #8）

`tests/unit/test_mqtt_writers_gate.py` **18 条**（`18 passed RC=0`）：三条判据各正反两组、
关键字调用形式、内部总线不算射程、豁免空理由仍红 / 带理由转绿且计数 `exempted=1`、
锚点三种缺失形状各 `exit 2`、真实 src 干净且计数非零。

对**产品码**注入违例（从内存字节还原）：

```
M-1 桥内多一条生产者（resend 里调 publish_fired） | gate RC=1 | src/autoforge/af_mqtt_bridge.py:367: `…` 的 `resend()` 里调了 `publish_fired()`…
    同一处违例走完整 gates.sh | RC=1 | 结论：出向 MQTT 写者门禁红（exit=1）…
M-2 手搓 dict 当载荷（绕过 _envelope）          | gate RC=1 | `_publish(…, {'automation_id': …})` 的载荷不是 `_envelope()` 的产物…
M-3 锚点被改名（_envelope → wrap）              | gate RC=2 | 读不到锚点：类里找不到 `_envelope()`…
还原后 逐字节一致=True | gate RC=0（绿色行同实测计数）
```

M-1 特意跑了**完整 `gates.sh`** 而不只跑脚本：要证的是"接线也在"——新节名、`exit 1` 与那条 `结论：出向 MQTT 写者门禁红…`
都从 CI 同一入口的正文里读到了。

### 这一批自己踩的两个坑（一样记账，不吞）

1. **变异驱动脚本崩在半路 ⇒ 还原没执行**：第一版驱动在打印 gates.sh 结论时 `IndexError`，`write_bytes(ORIG)` 那行没走到，
   `af_mqtt_bridge.py` 带着 `resend()` 留在盘上。当场用 `git checkout -- <该文件>` 恢复（**变异前该文件与 HEAD 逐字节相同，
   `git diff --stat` 只有我插入的 3 行**，所以这条恢复路径此刻安全），第二版起改成 `try/finally` 包裹全部变异。
   规矩本身不变：**默认仍是从内存字节还原**，`git checkout` 只在"变异前已核实等于 HEAD"时才可用。
2. **`subprocess` 里的 `bash` 不是 Git Bash**：那次 RC=1 的真身是 `C:\Windows\System32\bash.exe` 报
   "适用于 Linux 的 Windows 子系统没有已安装的分发"（UTF-16 输出，346 字节），**不是门禁判红**。
   换成 `C:\Program Files\Git\bin\bash.exe` 绝对路径才拿到真的 `结论：出向 MQTT 写者门禁红（exit=1）`。
   这是"退出码必须实测、代理信号不算结论"的又一次现场版。

### 全链读数（HEAD `8b629b8`，全部当场跑出）

- `python -m pytest -q`：**2744 passed / 51 skipped / 7 subtests，RC=0（97.39s）**（较 `159ba00` 那批净 +18，即本批 18 条反例）
- `GATES_PYTHON=python bash gates.sh`：**RC=0**，新节正文与本机同口径；undefined-name 的 tests 侧从 **159 → 160 个文件**（本批新增一个测试文件），
  其余逐项未变（主题白名单 7 处、包标记 5 目录/96 文件、状态源扇出 96、参数注入 96/96 豁免 2、工具名单 96/31 豁免 0、状态源 fail-closed 5/5 豁免 0）
- `gates.sh` diff 为纯增（`+18/−0`）；三个文件 `CRLF: 0`

---

## 二之二十四、同一族教训用到入向那一面：AF 只订阅 `ma/insights`，动态主题要先过禁订族（`b50bb22`）

### 为什么轮到入向

契约表 §1.3 的护栏与计划 §第 1 步 ④ 写死了方向：**收件箱是 DB 的，AF 不替 DB 说话 ⇒ 不订阅 `butler/inbox/*`**。
今天这条由三件东西撑着：模块级 `FORBIDDEN_SUBSCRIPTIONS`（`af_mqtt_bridge.py:57`，由 homesdk presence 的
`INBOX_TOPICS` 派生再补自家那族）、`subscribe_topic()` 的当场拒收（`:406`）、`handle_message()` 的第二道
（`:417`——即使对端误投到别的主题也只计数不执行）。**行为测试钉的是"已知这三个入口"，缺的还是"下一个入口"**：
谁另开一个 `self.client.subscribe("butler/inbox/#")`，现有测试一条不会红——与 §二之二十三 出向那半完全同形。

### 三条判据（各自单独可红）

- **A 订阅口唯一**：MQTT 层的订阅调用点只允许出现在 `af_mqtt_bridge.py`。桥外订阅 = 绕开禁订族判定、
  绕开 `rejected`/`forbidden_seen` 留痕，等于 AF 悄悄多了一只耳朵。
  射程判据取两条信号的**并集**：接收者名是 `client`/`self.client`，**或**调用带了 `qos=` 关键字。
  只用接收者名会漏掉 `hub.subscribe(topic, qos=…)`；只用 `qos=` 会漏掉不传 QoS 的写法。
- **B 动态主题当场过守卫**：实参是 `INSIGHTS_TOPIC` ⇒ 放行；否则该调用**所在的最内层函数体**里必须出现
  禁订族判定（`FORBIDDEN_SUBSCRIPTIONS` 或 `startswith("butler/inbox…")`）。动态主题本身是契约允许的
  （`subscribe_topic()` 就是干这个的），不允许的是**不判就订**。守卫写在外层、订阅藏在闭包里 ⇒ 照样红。
- **C 收件箱族写死就红，带守卫也不给过**：守卫是给动态入口兜底的，不是"把越界意图写进代码"的通行证。
  判据只认 `butler/inbox/` 前缀——别的字面量主题走 B（有守卫即放行），门不顺手扩权。
- **锚点**（读不到 `exit 2`，不做假绿）：模块级 `INSIGHTS_TOPIC` + `FORBIDDEN_SUBSCRIPTIONS` 两个常量，
  类里 `start`/`subscribe_topic`/`handle_message` 三个方法必须在——三条判据全长在这些符号上。
- **豁免** `# mqtt-subscriptions: exempt(理由)`：理由不能空，单独计入读数；绿色行每个数字都是实测计数。
- **射程边界**：进程内总线不在射程（`af_bus.subscribe(key, handler)`、`af_vhass` 的 `bus.subscribe(callback)`
  既没有主题字符串也没有 QoS，契约面完全不同）。

### 真实 src 的实测计数（本轮当场跑出）

```
$ python scripts/check_mqtt_subscriptions.py src
✓ 入向订阅门禁干净（MQTT 订阅点 2 处、分布在 1 个文件；其中主题为 `INSIGHTS_TOPIC` 的 1 处、动态主题且函数体内有禁订族守卫的 1 处；现场豁免 0 处）
```

⇒ 计划 §第 1 步 ③④ 那句"只订 `ma/insights`、不订 `butler/inbox/*`"在静态上坐实为**两只耳朵各有身份**：
一处是写死的 `INSIGHTS_TOPIC`（`af_mqtt_bridge.py:396`），一处是 `subscribe_topic()` 里过了守卫的动态入口
（`:410`）。进程内那两处 `bus.subscribe(…)`（`af_vhass/high_fidelity.py:136`、`af_vhass/sse_stream.py:32`）
判在射程外——这正是"射程要窄到能长期绿"的那条口径（§二之十七）。

### 反例与变异（门本身必须能红，铁律 #8）

`tests/unit/test_mqtt_subscriptions_gate.py` **20 条**（`20 passed RC=0`）：已知好形状绿**且真的被计数**
（`sites/insights/guarded = 2/1/1`——只断言"绿"会放过"根本没进射程"这种假绿）、桥外订阅红且报文件行号、
`qos=` 把异种接收者拉进射程、进程内 `bus.subscribe`/裸 `subscribe` 不误红、无守卫的动态主题红且**点名函数**、
守卫在外层而订阅在闭包 ⇒ 红在 `do_it()`、`topic=` 关键字式两态各钉一条、模块顶层订阅红、
写死 `butler/inbox/#` 即便带守卫仍红、别的字面量主题不被 C 误伤、豁免空理由仍红 / 带理由转绿且 `exempted=1`、
锚点四种缺失形状各 `exit 2`、真实 src 干净且计数非零、绿色行打印数字而非"全部/只在/都经"。

对产品码注入违例（**从内存字节还原**）：

```
M-0 未变异                                     | gate RC=0 | ✓ …订阅点 2 处…
M-1 桥内多一个不查守卫的订阅口                 | gate RC=1 | `open_channel()` 里订阅 `topic` 却没有禁订族判定…
    同一处违例走完整 gates.sh                  | RC=1      | 结论：入向订阅门禁红（exit=1）…
M-2 桥外 `hub.subscribe(topic, qos=…)`         | gate RC=1 | 桥外 MQTT 订阅…`af_service.py:2370`
M-3 写死 `butler/inbox/#`（外层带守卫）        | gate RC=1 | `relay_inbox()` 里把收件箱主题写死成订阅实参…
M-4 `subscribe_topic` 改名 `attach_topic`      | gate RC=2 | 读不到锚点：类里找不到 `subscribe_topic()`…
还原自证：bridge 逐字节一致 = True / service 逐字节一致 = True / 还原后 gate RC=0
```

M-1 同样跑**完整 `gates.sh`**（用 `C:\Program Files\Git\bin\bash.exe` 绝对路径——上一批那条"WSL 的 `bash`
返回 346 字节 UTF-16 提示却看着像 RC=1"的坑就地复用为规程）：新节名与 `结论：入向订阅门禁红（exit=1）…`
整条都从 CI 同一入口的正文里读到。

### 全链读数（HEAD `b50bb22`，全部当场跑出）

- `python -m pytest -q`：**2764 passed / 51 skipped / 7 subtests，RC=0（96.28s）**（较 `8b629b8` 净 +20，即本批 20 条反例）
- `GATES_PYTHON=python bash gates.sh`：**RC=0**，新节正文与本机同口径；undefined-name 的 tests 侧 **160 → 161 个文件**，
  其余逐项未变（主题白名单 7 处、包标记 5 目录/96 文件、扇出 96、参数注入 96/96 豁免 2、工具名单 96/31 豁免 0、
  状态源 fail-closed 5/5 豁免 0、出向写者 2/1 + 2/1 + 载荷 2/2 豁免 0）
- `gates.sh` diff 纯增（`+17/−0`）；两个新文件 `CRLF: 0`；**产品码一个字节未动**（`git status --short` 只有
  `M gates.sh` + 两个 `??` 新文件）

### CI 侧复查（run 58–60）

- `gh_ci_status.py runs` → `success runs: [59, 58, 57, …]`（`RUNS_RC=0`）⇒ **run 58（`a7c2711`）与 run 59（`cc1048b`）
  两套 `completed/success`**，按 main 计**连续绿 26 条（run 34–59）**。
- run 59 五作业逐条 `completed/success`、`failed_steps` 全空。`quality-gates`（job `111147915931`）正文读到上一批
  那条出向门：`✓ 出向 MQTT 写者门禁干净（出向写者调用点 2 处、分布在 1 个文件；事件生产者 2 处、分布在 1 个文件；
  桥内 _publish(topic, payload) 2 处，其中载荷来自 _envelope() 的 2 处；现场豁免 0 处）` ⇒ 与本机 §二之二十三
  的读数**逐字同口径**，那四条计数不是只在本机成立的软门。
- run 59 的 `pytest`（job `111147915809`）正文 **`2744 passed, 51 skipped, 1 warning in 46.79s`** ⇒ 通过/跳过数与本机
  `8b629b8` 那次（2744/51，97.39s）相同，耗时不作判据。
- **run 60（`b50bb22`，本批）的 `quality-gates`（job `111154531610`）已 `completed/success`**，正文读到本批新门那两行：
  节名 `══ 入向订阅门禁（只订 ma/insights，动态主题要先过禁订族）══` 与
  `✓ 入向订阅门禁干净（MQTT 订阅点 2 处、分布在 1 个文件；其中主题为 INSIGHTS_TOPIC 的 1 处、动态主题且函数体内有禁订族守卫的 1 处；现场豁免 0 处）`
  ⇒ 与本机逐字相同。**该套其余三作业取数时仍 `in_progress`** ⇒ 连续绿仍按 26 条计，等 run 60 结论落地再抬。

---

## 二之二十五、把窗后四项验收做成一条命令：三态结论，缺项读不成绿（`da09d43`）

裁定 §四 的窗后四项（`compose ps` / `/health` 200 / 一条家庭墙钟 `ts` 的 `af/automation/fired` /
`adm/autoforge/status` retained `online`）此前只作为**文字**挂在 §六 的 EXEMPT 行上，窗当天靠临时手搓命令。
"手搓"正是过账出事的形状：少跑一项、把 skip 读成 pass、把 `/health` 打错成 404 就判服务没起。本批把它
做成 `scripts/verify_adm_window.py`——**逐项判定 + 三态结论**（PASS / FAIL / UNAVAILABLE）：

- 有 FAIL ⇒ 退出码 **1**；无 FAIL 但有 UNAVAILABLE ⇒ 退出码 **2** 并打印
  `结论：窗后验收不算完成（EXEMPT ≠ VERIFIED）`；全 PASS ⇒ **0**。优先级是刻意排的（专门一条测试钉住：
  同时有 FAIL 与 UNAVAILABLE 时取 1，真实违例不能被"环境缺项"稀释成 2），而"两绿两没跑"绝不印成"该步可记账"。
  读数行 `PASS n / FAIL n / UNAVAILABLE n（共 4 项）` 是数出来的，不是形容词。
- **③ 的三条判据各自单独可红**：契约表 §1.2 那行的四个键 `{trace_id, ts, automation_id, ref}` 缺哪个点名哪个；
  `ts` 与当下 UTC 的偏差超 `--max-skew`（默认 900 秒）判红——专门拦"把仿真锚点 `2026-09-14T08:00+08:00`
  当墙钟发出去"那一族（§二之十五 修过的同一族），而 `ts` **无偏移**也判红（读不出是不是家庭墙钟就不猜）；
  第三条是对着契约表新加的：§1.2 明确"**事件类永不 retained**"，故 fired 若以 `retain=True` 送达判红
  （事件一旦被 retained，新订阅者会收到几天前的"有人回家"）。多余的键**不判红**（`node_id` 仍等 §五 第 12 件
  裁定，脚本不代为定性），但把实际键集原样印进读数，漂移在日志里可见。
- **④ 用 paho 的 `msg.retain` 判 retained**，不用"两次订阅"的间接法：现发的一条 `online` 与 retained 快照
  在正文上长得一模一样，判据只能问标志位（`test_status_live_sent_online_is_not_retained`）。
- **② 的"连不上"由 ① 的实测决定语义**，这是本批唯一一处项间依赖：容器已确认在跑却连不上 ⇒ **FAIL**
  （AF 起来了但服务面没起，窗当天必须拦得住）；本机根本没 docker、① 未确认 ⇒ **UNAVAILABLE**。
  同一句 `ConnectionRefused` 在两种机器上含义相反，写死成任一种都会假。

**两处草稿缺陷被本批自己纠掉，都记下来**：
① 初稿用自造的 `AF_MQTT_USERNAME`/`AF_MQTT_PASSWORD` 读环境——那是**第二真源**（键名收编在机制层
`homesdk/mqtt.py` 的 `resolve_credentials()`：`MQTT_USER_*`/`MQTT_USER`/`MQTT_USERNAME` 与
`MQTT_PASSWORD`/`MQTT_PASS`/`MQTT_PASSWD`，且"缺一半视同匿名"直接抛）。现改成走 `hm.broker_settings()` +
`hm.get_client(peer=…)`：脚本因此**从不接触口令值**，也就没有把它打印进日志的路径（结构性自证：
`test_script_never_reads_credentials` 断言源码里没有 `os.environ`/`getenv`/`password`/`PASSWD` 任一字样）。
② 初稿 shell 调用外部 `mosquitto_sub` 的 `-W`（等待秒数）与 `-c`（临时凭据文件）两个选项，而本机**没有那个
二进制** ⇒ 选项形状无从实测；把窗当天的验收建在没跑过的命令签名上，正是本计划一直防的过账方式。改用 paho
（AF 已在依赖里），顺带 `retain` 标志也才拿得到。**这与裁定原文的措辞有一处出入**：原文写的是"`mosquitto_sub`
抓到一条"，本批按**意图**（在 broker 侧真收到那条载荷）实现，工具换成机制层自带的客户端；若 DB/DCD 认为
必须是命令行工具，改回来的成本是一个 `_collect`，判据不动。

**"本机无 paho-mqtt"是错的，本批更正**：那条理由在本文档出现过 **5 处**（§〇"裁定 20261002"那行、§一末的
⛔ 行、§一"合并停机窗"那行、§六 的窗后四项行与洞察投递源行），都用它解释窗后四项为何只能进窗做。
实测 `python -V` = `Python 3.13.2`、`paho module:
C:\Users\lidicn\AppData\Local\Programs\Python\Python313\Lib\site-packages\paho\mqtt\client.py`、
`homesdk.mqtt.paho_available() = True` ⇒ paho 一直在**跑测试的那份解释器**里，先前那句是本机多条 Python
环境下"缺包九成是跑错解释器"的又一例。**结论不变但理由换了**：本机缺的是那台 broker——`hm.broker_settings()`
实测抛 `MissingEnv: 环境变量 HOMESDK_MQTT_HOST（或 MQTT_HOST）未设置或为空`，且 `docker`/`mosquitto_sub`/
`mosquitto` 三条 `command -v` 全 `NOT-FOUND`。上面 5 处已逐条改写成"缺真 broker"，避免后来人按错误的理由
找错误的解法（装 paho 解决不了任何事）。

**本机实测读数**（解释器 `Python 3.13.2`；产品码 `0` 变动，全为新增）：
- 降级跑（没服务、没 broker）：`python scripts/verify_adm_window.py --wait 3 --http-timeout 3`
  ⇒ `WINDOW_RC=2`，四行逐项 `[UNAVAILABLE]`，正文含 `EXEMPT ≠ VERIFIED`——**部分没跑印不出绿色**。
- 对真实服务跑 ②：本机起 `forge serve --port 8791 --store-root <Temp>\af-window-store`（只读面、不碰 NAS，
  铁律 #3）⇒ 该项 `[PASS]` 且读数为 `` `/api/health` 返回 200，键 contract_version,milestones,ok,readonly,store_ok,tick_exit_reason ``，整行汇总 `读数：PASS 1 / FAIL 0 / UNAVAILABLE 3`，`WINDOW_RC=2`。
  顺带钉下两条事实：AF 真注册的只有 `/api/health`（裁定原文写的裸 `/health` 会 404），且
  `docker/docker-compose.api.yml` **没有 healthcheck**（只映射 8787，无探针）⇒ 窗当天"容器 Up 但服务没起"
  只能靠 ② 这一条拦，别指望 compose 自己发现。
- 新测试 `tests/unit/test_verify_adm_window.py`：**27 passed in 1.64s**（三态各覆盖、契约四键缺失点名、
  `node_id` 不判红、`ts` 三条判据、`retain` 双向、②/① 联动两态、`_collect` 把 `MissingEnv` 转成 UNAVAILABLE
  而不是 traceback）。
- **变异三档各自单独可红**（驱动器从内存字节还原）：M-1 拆"事件不许 retained" ⇒ `RC=1  1 failed, 26 passed`；
  M-2 拆 `ts` 偏差判据 ⇒ `RC=1  1 failed, 26 passed`；M-3 把 UNAVAILABLE 当通过 ⇒ `RC=1  1 failed, 26 passed`；
  三档还原均 `逐字节一致 = True`，`DRIVER_RC=0`。第一次跑驱动器**没跑到判据**：驱动脚本自身一个字符串引号
  没闭合，`SyntaxError` 在解析期就死 ⇒ 目标文件一个字节未动（这点比"红"更值得记，如实写）。
- 全链：`python -m pytest -q` ⇒ **`2791 passed, 51 skipped, 1 warning, 7 subtests passed in 123.73s`，`PYTEST_RC=0`**
  （2764 + 本批 27 = 2791，净增与用例数逐条对得上）；`GATES_PYTHON=python bash gates.sh` ⇒ **`GATES_RC=0`**，
  出向写者门与入向订阅门两行的计数与 §二之二十三/二十四 逐字相同（本批不碰 `src`，射程不变）。
  `git diff --cached --numstat` = `268 0` + `326 0`（纯增，无整文件重写），两个新文件 `CRLF: 0`。

### CI 侧复查（run 60–61 结论落地）

- **run 60（`b50bb22`）事后复查 `completed/success`**（上一版登记时其余三作业仍 `in_progress`，故当时不预记）；
  **run 61（`16ad74e`）`completed/success`**，`jobs 37106525401` 逐条读到
  `pytest`/`layering-gates`/`ui-typecheck-build`/`adm-linkage-contracts`/`quality-gates` 全 `success`、`failed_steps=[]`；
  `pytest`（job `111156038061`）正文 **`2764 passed, 51 skipped, 1 warning in 84.57s (0:01:24)`** ⇒ 与本机
  §二之二十四 那次通过/跳过数逐字相同（runner 84.57s / 本机 96.28s，耗时不作判据）。
- 按 main 计**连续绿 27 条（run 34–61**，其中 run 54 是误建分支上那套重复、单列不计入也不隐瞒）。
  本批 `da09d43` 触发的 run 结论待落地后另记，不预先抬数。

---

## 三、提交台账（HEAD 之前基线 `04e6c72`）

| commit | 内容 |
|--------|------|
| `8db7c5e` | 第 1+2 步：`af_mqtt_bridge` 上线，presence/LWT、fired\|failed 事件流、`ma/insights` 只进 ask 档 |
| `628502a` | 第七轮审计修复：四个 `StateProvider` 实现统一到 fail-closed，仿真与生产不再对同一 IR 判相反结论 |
| `0dd57b2` | 第 4 步：`af_apply` 加 `dry_run` 与未知 stage 拒收；`apply_group` 回滚把手改按 pending `op_id` |
| `34a540c` | 第 3 步：group 节点 `mode(sequence|parallel)` schema 先行 + group 回归测试 |
| `f2d0a5c` | 保真修复：group 容器把 `mode` 与整棵 `children` 纳入核心比对 |
| `d602e93` | 门禁接线：主题白名单进 gates + 事件信封 `ts` 走家庭墙钟（对齐契约表 §四） |
| `27c14a2` | 第 0 步 ①②：vendor 0.3.1 wheel 入库 + 时区主路径改调 `homesdk.time` |
| `3a8a4f3` | 文档：本记录首版（逐步落点 + 第七轮核实链），**已推 origin/main** |
| `3c8ec11` | `gates.sh` 计数棘轮 + `.gates-tally.txt`（上限 104）+ `scripts/mutation_check_templates.py` 判语断言（含 AgentOps 模板反引号缺陷的实测记录） |
| `177aa1c` | F4 ③ `/#/evidence` 生产态证据视图 + `LiveView` 事件脚本回放与 `missing_entities` 读数（堵 F7 残留） |
| `c5b13a9` | 本记录上一版读数 + `inbox/20261002-AgentOps门禁模板无版本载体-决策申请.md` |
| 本批（即本条记录所在 commit） | **裁定 20261002 的 AF 侧落地**：`af_insight_queue` 只落盘队列 + `make_durable_ask_sink` 接线 + `approve_insight`/`reject_insight` + `/api/insights/*` 三端点；`/api/asks/answer` 加 `channel_error`（③A）；`_envelope` docstring 钉 `ref`=实例 id 与 v2.6 删除时点（②A）；vendored wheel 换 `b4b5d6bb…`；计划文档 §第 4 步 ①/§第 1 步 ③/§四 顺序与窗后验收对齐裁定；测试新增 13 项 |
| 本批之二（UI 类型硬门） | `vue-tsc -b --force` 15 条清零（4 处 `bordered="false"` 传成字符串、`SpecEditorView` 两个**死控件**补 `compile()`/`bindDevices()`、`DiffResponse` 按后端真形补 `old`/`new`、`SpecCompileResponse.ir` 改可空 + `error?`、5 处死 import）；`mock.ts` diff 夹具补成与真响应同形（类型门自己抓出来的假夹具）；`package.json` `type-check` 加 `--force`（防缓存假绿）；`ci.yml` 新增 `ui` job 作硬门；`VersionsView` 拆分 `loadError` 防错指；读数见 §二之二 |
| 本批之三（API 层零 `any`） | `metrics`/`experience`/`telemetry`/`sessionAnswer` 四处 `request<any>` 换成 `MetricsResponse`/`ExperienceResponse`/`TelemetryResponse`/`SessionViewResponse`（形状取自后端返回语句，逐键与在线 HTTP 响应对账；`success_rate` 按后端"0 样本不伪造 0%"写成可空）；`MetricsView` 三个 `ref<any>` 收口；`sessionAnswer` 路径段补 `encodeURIComponent`；类型门红/绿双向读数（探针 TS2339 RC=2 → 撤除 RC=0）；读数见 §二之三 |
| 本批之四（洞察面板 + `detail` 不再吞） | 新屏 `/#/insights`（`InsightsView.vue` + 路由 + 菜单「MA 洞察」+ 五个类型 + 三个 API 方法，facade 刻意不做 mock 夹具）：无 IR 提案的交接按钮 `disabled`、二次确认措辞写死"交接 ≠ 部署"、空态列四种成因并显示队列落点、`unreadable` 记账可见；端到端用 `PersistentInsightSink.submit` 真投递两条自测提案，approve 落到 `af_pending`（`a648a1aeb3c84751`）与 decided 的 `pending_op=` 把手两侧对上；顺带修 `client.ts` 把响应体 `detail` 丢掉的真缺陷（409/400/404 的后端原话以前在 UI 上只剩 "Conflict"）；读数见 §二之四 |
| `17953c6` | 本批之四落盘（`feat(ui): ADM ④A 洞察面板…`），已推 origin/main |
| 本批之五（CI 首读 + 两个永久红因） | `b5f0a7b`：`af_api.SimBody` 的 pydantic-v2-only `Field(max_length=)` 改成 `_MAX_SIM_EVENTS` + `/api/sim` 边界判据（CI 解到 pydantic 1.10.12 时 `import af_api` 直接 ValueError，pytest/contracts 两作业 0 条测试跑过）；`scripts/check_undefined_names.py` 的 `ast.TypeAlias` 改 `getattr`（3.11 无此属性，门禁自己崩且红因被写成"undefined-name 红"）；两条各钉一个能变红的反例（端点 422 测试 + subprocess 真删 `ast.TypeAlias`）。读数见 §二之五 |
| 本批之六（CI 第三个红因） | `e674886`：`tests/conftest.py` 新增 `vhass_plugin_loaded()`，`@pytest.mark.vhass` 的 skip 判据从"包能否 find_spec"换成"插件真注册了没"——CI 上 10 条 `fixture 'hass' not found` 收口为带理由的 skip；本机用桩包 + 伪 `sys.platform` 取到红/绿双向读数 |
| `b31c2ad` | 文档：§二之五 CI 首读全篇 + §〇 CI 行 + §三 台账两行 + §五 第 4 件按 20261002 裁定改写 + §六 四条残留/一条销账；顺带修回被我上一次编辑吃掉的 `## 三` 标题，并把核实基准从"HEAD 自指"改成"代码侧最新 commit"（文档提交不再改判据） |
| 本批之七（events 上限补齐） | `af_api.py`：`_MAX_SIM_EVENTS` → `_MAX_REPLAY_EVENTS` + `_check_event_cap()`，`/api/sim`/`/api/sessions`/`/api/live/run` 三条端点同形判据（8e8c725 只装了第一条）；`test_af_api.py` 参数化钉两条新端点（超上限 422 + 恰好上限非 422），摘掉判据实测 `2 failed`（400/403 而非 422）；本机全量 2623 passed / 51 skipped RC=0，门禁 RC=0。读数见 §二之六 |
| 本批之八（裁定 (b) 落地 + 空操作证伪） | `ci.yml` 的 `ui` 安装步：`--registry=https://registry.npmjs.org` **配** `--replace-registry-host=always`（只写前者经实测是空操作——fetch 全在 `registry.npmmirror.com` 87 + `cdn.npmmirror.com` 87），并加主机自证（`npmmirror` 命中≠0 或 `npmjs` 命中=0 → `::error::` + `exit 1`，三份实测日志上红/红/绿）；逐字节对账 137/137 integrity MATCH，官方源 node_modules 上 type-check/build 均 RC=0。回执投 DCD：裁定落法补正 + §四 判例 2 证据更正。读数见 §二之七 |
| 本批之九（`ma/insights` 入向按契约表收 + CI 日志可读） | `af_mqtt_bridge.ingest_insight()`：契约键优先、旧键别名（`trace_id`/`summary` 回退）、缺 `conf` 从"拒收"改为"按 0.0 入 ask 档 + `conf_reported=false`"（坏报照旧拒）、新增有界 `_transport_of()` 记账（kind/persons/room/证据计数与预览/ts/has_snapshot）；`PersistentInsightSink.submit` 与 `ProposalManager.submit` 同步加 `transport` 参数（两份签名保持对齐＝桥侧不分支），`Proposal` 补 `transport` 字段并进 `to_json`；UI：置信列对缺报显示「未上报」、`InsightRecord.transport` 收成具名 `InsightTransport`、「洞察内容」格补依据行；`scripts/gh_ci_status.py` 扩 `log <job_id> <关键词>`（停在 302 取 Location、无凭证取正文，不把 token 发给日志存储域）。测试新增 5 条 + 拒收表按新口径改写，临时改回旧行为实测 `4 failed` ⇒ 装回后 31 passed；全量 2631 passed / 51 skipped RC=0，门禁 RC=0，type-check/build RC=0。跨仓三件投 DCD（§五 第 7 件）。读数见 §二之八 |
| 本批之十（`caps.version` 的静默空值门） | `start_from_env()` 的 `version` 默认值 `""` → `PRESENCE_CAPS_VERSION`：`adm/autoforge/caps` 是 retained、Q7 规定 `caps.version`=计划号，漏传一次就长期挂一条 `version:""` 给 DB 读（生产调用点 `af_cli.py:1329` 本就显式传号，所以这是签名里的一扇门而非在跑的错误）。新判据 `test_start_from_env_publishes_the_plan_caps_version`，红/绿双向实测（临时改回 `""` ⇒ `'version': ''` vs `'2.5'` 红；改回后 32 passed、全量 2632 passed / 51 skipped RC=0、门禁 RC=0）。`caps_payload()` 的通用默认值未绑计划号，理由见 §二之八 末段 |
| 本批之十一（`{"conf": null}` 归到缺报而不是坏报） | `_conf_of_payload()` 的缺报判据从"键在不在"改成"取不取得到实数"：键缺失**或**值为 `null` 都算缺报（按 `0.0` 入 ask 档 + `conf_reported=false`），`conf: null` 时 `confidence` 别名顶上来；越界/NaN/字符串/布尔/列表仍是坏报照旧拒。`_conf_of()` 删除、判据并进一处。新判据 `test_explicit_null_conf_is_the_same_absence_as_a_missing_key`，红/绿双向实测（临时改回"null 不算缺报" ⇒ `assert (False is True)` 红在 `tests/unit/test_af_mqtt_bridge.py:349`、RED_RC=1；改回后 33 passed、全量 2633 passed / 51 skipped RC=0、门禁 RC=0）。读数见 §二之八 |
| 本批之十二（`SessionViewResponse` 应答成功分支补实测） | `tests/contract/test_af_ask_contract.py` 末段两条：`POST /api/sessions`（带 `ask` 节点 + 事件打到挂起）⇒ 视图九键逐字对 `ui/src/types/api.ts:SessionViewResponse`、`asks` 一条的八个必需键与 `control` 整个 dict 对死、实例 `current_node=q1`；`POST /api/sessions/{sid}/answer` 结构化应答成功 ⇒ 同一份九键视图、`asks` 排空、`vars.ask_answer` 落值且 `vars.decided=yes`（then 边真跑）、`GET` 读回一致。**两处变异各自取红**（返回半个对象 ⇒ `1 failed`；`_session_view` 的 `asks` 写死 `[]` ⇒ `2 failed`），撤除后 14 passed、全量 2635 passed / 51 skipped RC=0、门禁 RC=0。**顺带抓到自家的账**：单跑绿、全量红——`_SESSIONS` 是进程内全局会话表，留挂起 ask 的用例把 `test_af_api.py::test_api_asks_aggregate_endpoint_registered` 带红（全量 `1 failed` PYTEST_RC=1），两条用例都以 `DELETE /api/sessions/{sid}` 收尾才回到 2635。§六 那条 EXEMPT 销账，读数见 §二之九 |
| 本批之十三（第 5 步 ③ 第二档：架构门升硬，`64ca83c` 已推） | `ci.yml` 的 `architecture` 作业 `run: lint-imports` **去 `continue-on-error`**（改后全文件该键命中数 0，`yaml.safe_load` 复解析五个作业）；`pyproject.toml` 只改注释、契约面一个字没动。读数：本机 `Contracts: 1 kept, 0 broken` RC=0、runner run 34 同契约 `KEPT`、`check_imports.py` 那行 `Baseline lock: 86 modules, 0 violations`（**这条 86 下一批被查明是缺陷读数、不是环境差异，见 §二之十一**）。**能变红实测**：给 `af_time.py` 加一行 `from autoforge import af_service` ⇒ RC=1 `0 kept, 1 broken`，并连带报出 `af_ir → af_bus → af_time → af_service` 的**间接**链（`check_imports.py` 只锁直接反向依赖）。**shortfall 已登记**：裁定是"一个版本**或** 2 周"，而该步进 CI 是 `20356dc`（10-01）、日历只走 2 天（绿的是 run 28–34）⇒ 属 AF 单方提前落第二档，投追认申请（§五 第 8 件）。门禁 RC=0、棘轮 104/104 不变。读数见 §二之十 |
| 本批之十四（架构门禁口径修复：gitignore 吞包标记 + 复发门，`c14503d` 已推） | `.gitignore` 加 `!**/__init__.py`（并把 runner `86 modules` 的根因写进注释）；`src/autoforge/af_closedloop/__init__.py` **首次入库**，内容从"与 `runtime.py` 逐字节相同的副本"（`diff` RC=0，两份 `GuardViolation` 不同类、`except` 会漏）改成薄转发；新增 `scripts/check_pkg_markers.py`（判 **git 索引**而非磁盘：磁盘检不出、只有 checkout 才缺）并接进 `gates.sh` 一节 + RC 聚合分支。可红性两条实测：假包 `af_probe/mod.py` 入索引不放标记 ⇒ RC=1；`git rm --cached af_closedloop/__init__.py` 还原历史原状 ⇒ RC=1 并指名该目录，装回 RC=0。全链：包标记门 RC=0（5 包 / 索引 96 个 `.py`）、`check_imports.py` RC=0 / **96 modules**、`lint-imports` RC=0 / 96 files 281 deps KEPT、`gates.sh` RC=0、`pytest -q` RC=0（2635 passed / 51 skipped）。跨仓外溢 + 第二档时点追认合成一份 DCD 申请（§五 第 8 件）。读数见 §二之十一 |
| 本批之十五（完成度自审抓到第 5 步 ② 半边未落 + runner 96 复测销账） | **纯文档，`af_persist` 一个字节没动**。§〇/§一 把"①②③④ 已交付""裁定全部落地"改成逐条精确状态（校验和 + 损坏段跳过已落、顺序追加未落、格式头未动）；新增 §二之十二 记三条张力；DCD 申请投 `20261003-AF-af_persist追加写半边与裁定执行约束张力-决策申请.md`（§五 第 9 件，A/B/C，AF 倾向 A）；§六 增一条残留并与 `BoundedCache` 那条分列。同批把 §二之十一 的"runner 侧 96 待取"补齐为实测（run 36 五作业全 `completed/success`；`log 111090382332 modules` → `Baseline lock: 96 modules, 0 violations`；`log … Contracts` → `1 kept, 0 broken`；`log 111090382310 包标记` → 索引内 96 个 `.py`），§六 该条残留随之销账 |
| 本批之十六（验收点按"点"复测 + ① 的接缝判据，`e6d442a`） | 新增 `tests/unit/test_af_cli_serve_lease.py` 两条：桩 `uvicorn`/`build_app`/联动桥，锁分别返回 `False`/`True` ⇒ `build_app` 收到的 `readonly` 为 `True`/`False`。两次变异各取到红（`readonly=False` ⇒ `:70 assert False is True` RC=1；`readonly=True` ⇒ `:81 assert True is False` RC=1），复原后 `2 passed` RC=0 且 `git diff -- src/autoforge/af_cli.py` 为空。文档侧：§一 第 5 步① 的行号按 HEAD 重取（`:1366/:1376/:357-362/:465/:473/:481/:545/:714/:735`，旧读数因 `a5c8aa9` 全部下移）；§一 第 0 步② 改记"9 例"实测量；§〇 第 3/4 步行补分跑读数（`31 passed`、`5 + 14 passed`）；新增 §二之十三。全链：`pytest -q` **2637 passed / 51 skipped RC=0**、`gates.sh` RC=0（AST 0 新增/存量 104、棘轮 104/104、undefined-name src 96 + tests **152**、主题 7、包标记 96、冒烟 0） |
| 本批之十七（同族第二条接缝：CLI 起桥的接线判据，`0722828`） | 新增 `tests/unit/test_af_cli_linkage_wiring.py` 三条：桩 `start_from_env`/`attach` 判 kwargs 与落盘副作用 ⇒ `proposal_sink` 是 `PersistentInsightSink` 且**没有** `approve` 把手、一条 `submit()` 真落到 `{store_root}/insight_proposals/pending/*.json`、`version == PRESENCE_CAPS_VERSION`、`tools == [t[0] for t in af_mcp.TOOLS]`、`attach` 收到桥本体、env 未开启 ⇒ 返回 `None` 且**一次都不调** `start_from_env`。三次变异分别 `2 failed` / `1 failed` / `1 failed`（RC 均 1），脚本自证还原 `restored identical: True` + `git diff -- src/autoforge/af_cli.py` 空。全链：`pytest -q` **2640 passed / 51 skipped RC=0**、`gates.sh` RC=0（undefined-name tests **153** 文件）。§二之十三 加"同族第二条接缝"小节，§〇 第 1/2 步行补判据 |
| 本批之十八（同族第三条接缝：`_make_runtime` 的起桥条件与 clock 归属，`9c6deb9`） | `tests/unit/test_af_cli_linkage_wiring.py` 扩两条（桩 `_start_linkage_bridge` 只记 kwargs）：dry-live 分支 ⇒ 恰好起一次桥、`clock is runtime.clock` 且 `isinstance(runtime.clock, SystemTimeSource)`、`store_root` 原样递到；仿真分支 ⇒ **一次都不起**。两次变异各 `1 failed, 4 passed`（RC=1：`clock=runtime.clock`→`clock=None`；仿真分支 `typer.echo("· 仿真底座：FakeHA…")` 前插一次起桥调用），脚本自证 `restored identical: True` + `git diff -- src/autoforge/af_cli.py` 空。文件分跑 `5 passed` RC=0。全链：`pytest -q` **2642 passed / 51 skipped RC=0（80.01s）**、`gates.sh` RC=0（undefined-name src 96 + tests **153**，本批未加文件故计数不变；AST 0/104、棘轮 104/104、主题 7、包标记 96、冒烟 0）。文档侧：§二之十三 加"同族第三条接缝"与本批读数小节，§〇 第 1 步行、§一 窗后验收行补判据并写明"接线判据 ≠ 抓包已发生"（该项仍 EXEMPT） |
| 本批之十九（接缝盘点的首个产物：状态源扇出门禁，`fc2bba6`） | 新门禁 `scripts/check_states_fanout.py`（按 (根名, 递出去的状态源) 分组，要求 `states`/`instances.states`/`scheduler.states`/`executor.states` 四条齐）接进 `gates.sh` 并加 RC 聚合分支（`pkg` 之后、`ast` 之前），CI 的 `quality-gates` 作业自动继承为硬门。红/绿实测：HEAD 绿（96 文件 RC=0），`af_cli.py:345` 删一行 ⇒ RC=1 指名缺 `executor.states`，`af_cli.py:301` 换成不同源 ⇒ RC=1 两条 finding。反例测试 `tests/unit/test_states_fanout_gate.py` 7 条（含"根名解析错=假洞"那一形状）；两次"让门自己变瞎"的变异各 `3 failed`（RC=1）。全链：`pytest -q` **2649 passed / 51 skipped RC=0（82.87s）**、`gates.sh` RC=0（undefined-name tests **154**）。§二之十四 新篇（含那次假绿的自证），§〇 门禁行与 CI 行、§六 残留随之改写 |
| 本批之二十（`af_service` 那半张盘点：真机时间轴，`12cea64`） | `af_service.live_run` 由 `build_runtime(graph)`（仿真锚点 2026-09-14 08:00）改为 `build_runtime(graph, clock=VirtualTimeSource(SystemTimeSource().now()))`，并新增 `clock=` 入参给确定性调用方——HTTP `/api/live/run` 与 MCP `af_live_run` 两条真机路径此前与 CLI live/dry-live 分支（已取墙钟）对同一 IR 给出两套时间轴。范围把 `build_runtime(` 八个站点全数点过（§二之十五 表格），撤销链不建 runtime。新判据 `tests/unit/test_af_live_run_clock.py` 3 条分跑 `3 passed` RC=0，取 `instances[].context.trigger_time` 而非 `audit`（**一次顺利下发零条审计**，`all()` 在空表上恒真——我第一版就假绿在这里，M-a 一次红三条、M-b/M-c 各红自己那条，还原逐字节自证 `restored identical: True`）。全链：`pytest -q` **2652 passed / 51 skipped RC=0（80.50s）**、`gates.sh` RC=0（undefined-name src 96 + tests **155**、扇出门禁 96 文件仍绿）。文档侧：§二之十五 新篇、§〇 加"真机路径口径对账"行、核实基准改 `12cea64`、§六 残留收窄 |
| 本批之二十一（真机入口面的两处 fail-open，`dc8ac0d`） | 接缝盘点第三条命中：`af_mcp._t_live`（`:254`）调 `svc.live_run` 时**漏递 `store`** ⇒ Tier-0 设备保护与实体健康视图在 Agent 路径上整个不生效（人点同一个设备被 400 拦下并给原话，Agent 说同一句话直接把 `light.turn_off` 打到真设备）；events 上限此前在**端点层**（`a5c8aa9` 只补齐了 HTTP 三条）⇒ MCP 三面（`af_live_run`/`af_simulate`/`af_sessions`）无上限。修法一律走咽喉、不按面重抄：`af_service.MAX_REPLAY_EVENTS` + `_check_events_cap()` 在 `simulate_track:884`/`create_session:1472`/`live_run:1676` 三处调用，`af_api` 删自家常量只留指针注释，HTTP 侧 `_svc` 把 `ServiceError.status` 映射回 422（状态码一个没变）。**上限调用点在 `live_run` 第一句**是被测出来的：先判策略后判形状时，超上限请求在"live 未启用"环境得 403 而非 422，`test_event_cap_covers_every_events_endpoint[/api/live/run]` 当场红 ⇒ 顺序改为"形状判据先于策略判据"，既有断言未动。新判据 `tests/unit/test_af_live_entry_seams.py` 5 条，首条为**正向对照**（无 ACL 时 MCP 真下发 `light.turn_off`），否则两条守卫用例会红在"MCP 路径压根跑不通"上；三次变异 `1 failed, 18 passed`/`2 failed, 17 passed`/`2 failed, 17 passed`（RC 均 1，`restored identical: True` 逐字节自证）。**顺带一条口径事实**：`af_mcp.py`/`af_api.py` 入库 CRLF、`af_service.py` LF，本仓无 `.gitattributes` 且 `core.autocrlf=false` ⇒ 跨文件同一种换行的锚点替换**匹配 0 处**，驱动第一版就假绿在这里（未统一全仓换行：大 diff 污染 blame 且与验收点无关）。全链：`pytest -q` **2657 passed / 51 skipped RC=0（78.97s）**、`gates.sh` RC=0（undefined-name src 96 + tests **156**）。§二之十六 新篇 |
| 本批之二十二（同批第三件定性为跨仓失败面，投 DCD） | `serve` 的单写者租约（`af_cli.py:1365` `FileLock(store_root/".serve.lock")`）与 `build_app(readonly=…)` 只在 HTTP 面（`af_api.py:344` 及 8 个写端点 `Depends`），`af_mcp.py` 对 `readonly|lease|acquire` **零命中** ⇒ MCP 进程可在 serve 已持写权时同时开真机写面。**与前两件的差别不在是不是缺口，在代价由谁承担**：前两件是把 AF 已承诺的策略装到漏掉的入口（只会更严、退路一条 revert），这一件要给 DB 新增一个可见的拒收模式（返回什么码、`check` 还是 `acquire`、是否只拦 live）⇒ 属"契约面改动走裁定"。申请投 `关键决策部/inbox/20261003-AF-单写者租约只装了HTTP面MCP真机写未受约束-决策申请.md`（§五 第 10 件，A/B/C，AF 建议 A=check-not-acquire + 只拦 live + `READONLY_DEGRADED:` 前缀 + 登记进契约表），**代码一个字节未动**。同批文档侧：核实基准 → `dc8ac0d`、§〇 加"真机入口面的闸门对账"行、§六 那条"未盘"残留收窄为两处已修 + 一件待裁 |
| 本批之二十三（`af_watch` 那半张盘点：观察期把 `auto_rollback` 丢在接缝上，`af2ee56`） | 第三条真缺陷、也是本族第一次落在**安全策略位**上：`canary: {duration: "15m", auto_rollback: false}` 的旗子在 `pending_canary` 挂起元信息里**没有对应键** ⇒ `resume(kind="on_timeout")` 重建 `CanaryResult` 后用 `SimpleNamespace` 假 guard、直接 `wrapped.rollback(adapter)` 无条件反向下发；同 IR 无 duration 的立即检查路径却走 `CanaryGuard.check_and_rollback` 尊重它。错向是"比操作员要求的更自动"，`af_scanner` P1-2 只要求"配了 canary"不要求旗子为真 ⇒ 生产可达；且该 resume 是挂起观察的常规唤醒路（`pending_canary` 随实例快照持久化），不是崩溃恢复专用。修法三条：挂起元信息加 `auto_rollback`（读侧 `… .get("auto_rollback", True)`，**缺键按 True**=旧快照行为不变、不新增迁移），恢复侧改用真 `CanaryGuard(states, auto_rollback=旗子)` 并调 `check_and_rollback()`（回滚与否只留一处实现，两路不可能再分叉），不回滚照旧记 `failed`+`entity_drift` 且 reason 写"未回滚，留给人判断"（铁律 #5）。新判据 `test_observe_window_resume_honours_auto_rollback_false` 修复前先红（`1 failed, 9 passed` RC=1，红在调用序列多出一个 `light.turn_off`），正向对照用同文件既存的 `…_rolls_back_on_drift`（真出 turn_off）；三次变异（不带旗子 / 绕开 guard / 读了又覆盖）各 `1 failed, 25 passed` RC=1、`restored identical: True`。同批盘完 `af_watch` 三个喂入点的 status 档对齐（shadow 只在 MATCHED/MISSED 喂、立即 canary 三档齐、冲突 ALLOW 不喂），并登记 `af_audit.record_conflict` 与 `af_watch.record_conflict` 的同名碰撞为**非缺陷**。全链：`pytest -q` **2658 passed / 51 skipped RC=0（94.77s）**、`gates.sh` RC=0（undefined-name src 96 + tests 156）；`af_executor.py` +21/−8 |
| 本批之二十四（参数递错那一族升成常驻门禁，`9a83ead`+`4a3e3b1`） | 第六条同族站点不再手扫：`scripts/check_param_injection.py` 静态判**调用了"签名里带关键参数且有默认值"的模块级函数却没递那个参数**，`gates.sh` 加一节 ⇒ CI `quality-gates` 继承为硬门。参数表 `TARGET_PARAMS = (store, readonly)`：`store` 是 §二之十六 那两处，`readonly` 是 §二之十三 记的本族最早一例；**`clock` 有意不收**并用 `test_clock_is_deliberately_not_a_target` 钉住理由。门首跑（HEAD `a94ca71`）报三处 ⇒ **第四处真缺陷** `af_mcp._t_health` 写的是 `svc.health()`（HTTP 面 `af_api.py:381` 早递了 store），Agent 面健康读数从不探自己服务的 store ⇒ `store_ok: null` 被下游读成"没问题"（铁律 #5 的假安心，与 §二之十六 那两处写面闸门同形状、不同代价）。新判据 `test_dispatch_health_probes_the_store_it_serves` 与修复同批写，反向证据是 M-1 变异（退回 HEAD 形状）`1 failed in 2.36s` RC=1。反例测试 22 条含**不误响**主干：(模块,名) 解析、`obj.method()` 不判、`*`/`**` 解包放过、`def f(store, *rest)` 位置数得出、转发 `_svc(svc.X, …)` 要带关键字、豁免必须有理由且旧前缀不再作数。现场豁免 2 处（`af_vhass/dual_track.py:80,81` 纯仿真对拍）。**两个自踩的坑记进 §二之十八**：变异驱动用 `git checkout` 恢复 ⇒ 把**未提交**的修复一起抹回 HEAD（`AssertionError: 恢复失败：af_mcp.py` 抓到，按 sha256 重新落回）；编辑工具把 CRLF 的 `af_mcp.py` 整文件重写成 LF（`git diff --numstat` 929/927 是唯一读数，修回后 3/1）⇒ 驱动锚点必须按各文件实际换行匹配、匹配数≠1 即中止。全链：`pytest -q` **2681 passed / 51 skipped RC=0（87.66s）**、`gates.sh` RC=0（undefined-name src 96 + tests **157**、参数注入 96 文件/96 签名/豁免 2） |
| 本批之二十五（名单手抄那一族扫掉，`743aadf`） | §六 挂了四批的最后一条接缝「TOOLS→caps 之外有没有**第二次工具名单映射**」本批清单化并做成常驻门。盘点必须走 AST 而非 grep：全文正则 108 串/77 不在注册表（几乎全是模块名，`af_live.py` 本身就是模块）⇒ 只取字符串常量且整串 fullmatch 工具名形状 = 删除前 **40/9**、删除后 **34/3**（剩三个是指标标签名 `af_action_dispatched`/`af_caused`/`af_local`，不是工具名）。两处真映射：`af_orchestrator.observe()` 按名调 `af_live`（注册名 `af_live_run`；`_call_safe` 吞异常 ⇒ 这条路**永远不会响**、无任何测试依赖）、`af_runtime_ext.mcp_tools()` 另抄五人名单**从未接线**，其中 `af_approve_proposal` 与裁定 20261002 §三 ④A + `af_api.py`「approve 只在服务层，MCP 面绝不注册」+ orchestrator `_call` 里那道 `if "approve" in …: raise` 三层冲突 ⇒ **删除而非接线**（净 `0 3`/`1 14`，单提交可回退）。新门 `scripts/check_tool_names.py` + `gates.sh` 新节（CI `quality-gates` 继承为硬门）：按名调用的字面量必须在 `TOOLS`（调用面白名单 `_call`/`_call_safe`/`call`/`dispatch` + `submit_pending` 第二实参 + `tool=` 关键字）、形参默认值只在形参名是 `tool`/`*_tool` 时判、`TOOLS` 之外 ≥2 个 `af_*` 键的字典即第二份名单、**注册表锚点读不到 exit 2**（§二之十四 的教训：报"0 处发现"就是假绿）、豁免 `# tool-name: exempt(理由)` 必须有理由且隔壁门标记不作数。反例 22 条含不误响主干（第一版没查调用面 ⇒ 4 处日志文案误报）。五处变异**分档**红：M-1/M-2/M-3 `RC=1` 各指到文件行、M-4 `RC=2`、M-5 豁免正控制 `RC=0` 且报「现场豁免 1 处」，五个文件还原逐字节一致。**两处自踩记账**：① 变异驱动 v1 把四次 `patch()` 写在循环之前 ⇒ M-1/M-2/M-3 三条读数全被 M-4 遮蔽成同一句「读不到注册表」——看着像门很灵敏，实际一条本职判据都没验到；② 自家 `gates.sh` 结论行在双引号里用裸反引号 ⇒ bash 命令替换，`readonly` 把**整个 shell 环境 dump** 进红消息、`store: command not found`（实测输出见 §二之十九），三行已转义并实跑红分支复读——红路径自身也要读一次。全链：`pytest -q` **2703 passed / 51 skipped RC=0（101.45s）**、`gates.sh` RC=0（undefined-name src 96 + tests **158**、参数注入 96/96/豁免 2、**工具名单 96 文件/31 工具/豁免 0**） |
| 本批之二十六（名单族的形状扩面 + caps 同源盘成读数，`c7b97b9`） | 两件事都是"顺着验收点再问一句"问出来的。① `TOOLS→caps` 的**另一半**：`af_mcp.py:812` 解包 `_name, _desc, _schema, fn, scope = tool` ⇒ scope/caps 是注册表元组的第 5 项、**与名字同源**，不存在"改名时 caps 那边不知道"，这一半登记为已盘而非新门。② "第二份名单"上一版只判**字典键**，而手抄枚举也会长成 `WRITE_TOOLS = {"af_save", "af_live_run"}` 这种集合/元组 ⇒ 扩到 `Dict/Set/List/Tuple`（`own_registry` 仍跳过 `af_mcp.py` 自己），扩之前当场盘 src：容器字面量里 ≥2 个工具名形状字符串 = **0 处**，所以扩过去不误响。变异 M-6：往 `af_orchestrator.py` 贴集合+元组各一条 ⇒ `RC=1` 且两行各自指到行号，还原 `True`、收尾复跑 `RC=0`。反例 +3（集合红/元组红/单名列表不红），全链 `pytest -q` **2706 passed / 51 skipped RC=0（89.08s）**、`gates.sh` RC=0。**CI 侧首次读到新门在 runner 上的正文**：run 46（`d992f29`）`completed/success`；run 47（`b1f0ec4`）`quality-gates`+`ui-typecheck-build` `completed/success`，`quality-gates` 正文逐字为 `✓ 工具名单门禁干净（扫描 96 个文件，注册表 31 个工具，按名调用点全部命中，现场豁免 0 处）`——与本机读数逐字相同（Python 3.11 vs 本机 3.13.2，纯标准库 AST 门两版都能跑）。另记一次**重复踩坑**：驱动里 `anchor=b'…中文注释…'` 又触发 `bytes 只能 ASCII` 的 `SyntaxError`（§二之十八 记过同一件事）⇒ 这类坑要靠"写完立刻跑"而不是靠记忆 |
| 本批之二十七（"实现间契约不一致"补成静态门禁，`d7d1fff`） | 第 4 步之后的计划外补刀，也是 §六 那行"未做静态门禁"的收口。判据来源是第七轮审计的 key_finding（`and`/`or` 走 `all()`/`any()` 短路 ⇒ "缺失留给运行时发现"在 fail-open 一侧不成立），当时的处置是四对多实现的契约测试——**但那批测试各自只认自己那几个类**，新增一个忘 raise 的 provider 一条都不会红，所以这条契约在"下一个实现"上仍无门禁。交付 `scripts/check_snapshot_policy.py`（纯标准库 AST）+ `gates.sh` 新节与 `-eq 2`/`-ne 0` 两条红分支 + `tests/unit/test_snapshot_policy_gate.py` 16 条反例，**产品代码零改动**。射程盘点走 src 全集：类级 `snapshot` 11 站点 / 5 个实现进射程（`DeviceCatalog`→`dict`、`HealthEngine`→`list[dict]`、`MetricsAggregator`→`dict`、`TickHealth`→`dict`、`VersionManager`→`Version` 五个不进），模块级 `af_version.py:823` 也不进。**第一版被自家反例打红三次**（`assert 0 == 1` × 3）：只按 `-> Snapshot` 判 ⇒ 不写标注的 fail-open 实现隐形，而本仓 `tests/contract/test_state_provider_policy.py` 的 `_FailOpenProvider` 恰好就是那种写法 ⇒ 射程改为"标注 ∪ 体里造 `Snapshot`"，并加一条**直接对本仓那个文件跑 `_findings`** 的反例把并集钉住（同批诚实记账：并集在今天 src 上零增量）。第二处自踩：把 `check()` 改成返回三元组时末尾 `return findings, implemented` 忘了改 ⇒ 变异驱动器里 **M-0（什么都不改）先 BAD、RC=1**，这条档次的价值在此——它管的正是"门自己坏了"；解包错误在 pytest 侧同一时间报 `ValueError` 两条，两处读数是同一次错误的两面。读数：六档变异 `0/1/1/0/2/2` 全 OK（M-3 阳性对照的绿行同时演示"抛 5 / 豁免 1"的分项计数）、`bash gates.sh` 红路径真跑（反引号已按 §二之十九 教训逐处转义，输出是字面 `` `Snapshot` ``，不再 dump 环境）、全链 `pytest -q` **2722 passed / 51 skipped RC=0（93.00s）**、`gates.sh` RC=0（undefined-name tests 158→**159** 因新增测试文件，其余逐项同前）。同批把 §六 那行**两件混写**拆开：`BoundedCache` 基类经核（`git log -S --all -- src tests` 0 命中 + 第六/七轮两份核实报告原话）确认是**审计自判"不做"的建议**、不是欠账；但同一句里"新增有界缓存必须同时给 TTL 与硬上限"的**门禁可见**那一半确实未做（`grep 有界|TTL gates.sh scripts/*.py` 零命中），是否升级硬要求属第六轮原话留给 DCD 的跨模块重构 ⇒ 登记不静默、AF 不自决 |
| 本批之二十八（有界缓存约定投 DCD + run 49 五作业复查，纯文档） | 两件事。**① §五 第 11 件**：上一行拆出来的第二件不是"记一句待办"，而是先把"能不能做成门"问到有读数——AST 口径（类里 `self.X = {}/[]/deque/dict/list` 且同类内 `X[k]=`/`append`/`update`）在 src 全集得 **7 个候选**，5 个同类内已有界证据（与第六轮"四条腿 + `_SESSIONS`"账一致），**"缺界"那 2 个逐条读码全是误报**（`PreTriggerService._stats` 固定键计数器 / `DeviceSM.attributes` 键集由 domain 属性词表决定）⇒ **真阳性 0/2**，结论是"扫无界容器"静态上判不出可靠口径、硬做上门第一天就带两条永久红只能挂豁免表（与 §二之十九 刚扫掉的第二真源同形）。三档 A/B/C 已投 `关键决策部/inbox/20261003-AF-有界缓存生命周期约定要不要升级为硬门禁-决策申请.md`，AF 建议 B（注册表式门），**代码一个字节未动**。同批把 `af_vhass` 的"未建模服务"面也盘了一遍并登记为**已盘非新缝**：`is_modeled()` vhass/FakeHA 共用同一判据、未建模动作单列 `unmodeled_actions` 档进诚实报告（`af_expect.py:310-329` 还把"间接触发已展开 effects"的移出），不是静默 OK。**② CI 复查**：run 47/48 事后均 `completed/success`；**run 49（`d7d1fff`）五作业逐条 `completed/success`、`failed_steps` 全空**，runner 正文读到新门那行与本机逐字相同、`pytest` 正文 `2722 passed, 51 skipped, 1 warning in 81.27s`（通过/跳过数与本机同，墙钟 81.27s vs 本机 93.00s）⇒ 连续绿 13 → **18 条（run 34–51）**（run 50/51 两笔纯文档提交事后亦 `completed/success`，run 52 取数时在飞）；读数进 §二之二十之一 与 §〇 CI 行 |


| 本批之二十九（出向载荷与契约表对账：`error` 修、`trace_id` 交裁，`5ff1ea6`） | 第 7 件对完入向，本批对**出向**两面（AF → DB 的 `af/automation/fired` 与 `failed`）。逐字段读码抓到一条真缺陷：**`failed` 的 `error` 恒等于字符串 `"failed"`**——`observe_terminal()` 传的是 `error=state`，而它只在 `state == "failed"` 分支里跑；真原因一直存在（`af_instance.py:287` 由 `InstanceManager.fail()` 在转 `FAILED` **之前**写入 `ctx.context["fail_reason"]`，`af_executor.py:123/605/625/662` 每条 `_fail()` 都带语义：段步数上限、"`{action}` 失败且无 on_error/default 兜底：`{result.error}`"、软失效异常、收敛纪律违反）⇒ **契约表写明消费方是 DB 且用途"告知用户"，而 DB 唯一能显示的那句话被换成了状态名**。修法定性为"补 AF 漏装的自家闸门"：字段名/类型一个没动，只是把契约已定义的那个东西装进去——桥读出真原因，缺原因（无 `ctx` 的鸭子类型 / 空串 / 纯空白）发 `NO_FAILURE_REASON` 诚实占位而**不拿状态名冒充**，封顶 `MAX_ERROR_CHARS=500` 取契约 §1.3 给展示类文本已定过的同一个数（原因会拼异常 repr 与真机回执，不封顶=任意长度正文进 QoS 1）。判据 3 条从**真状态机**产出实例（`InstanceManager.spawn` + `.fail`）而不是手搓 SimpleNamespace，为的是锁住跨模块键名：**四档变异 `0/2/1/1`**（M-0 基线必须绿、M-1 退回状态名红两条、M-2 桥把读键写成 `failure_reason` 红一条、M-3 不封顶红一条），全部从内存字节还原、收尾逐字节自证。**为什么这一件不升成静态门**（同族第 7 条判据，登记理由而非静默不做）：`error=state` 与 `error=reason` 在 AST 上同形，§二之十八 那门判的是"**没递**"（漏关键参数，静态可数），这族判的是"递错"（值语义）⇒ 唯一能判红的是行为断言。**`trace_id` 那一半不动代码、投裁定**（§五 第 12 件）：§1.3 那句"trace_id 必填、贯穿洞察→收件箱→播报"实测挂在**收件箱三面**护栏下且 `grep -rn "publish.*butler/inbox" src/autoforge` = **0** ⇒ 不是 AF 违规而是 §1.2 对事件面只列字段名；上游那枚号 AF 收了（`af_mqtt_bridge.py:449` → `af_proposal.py:242`/`af_insight_queue.py:199` → approve 时 `af_service.py:552` 写进 GraphStore 记录 `note`，`load_record` 读得回）**但到不了发射点**（`hypothesis_id` 在 `af_ir/`+`af_instance.py`+`af_executor.py` **0 命中**），三种修法两种要动三方共读面或把 DB 的 trace 检索从 1:1 变 1:N ⇒ 按 20261002 §一"契约面改动走裁定"上交，A/B/C 齐、AF 建议 C（`trace_id` 明确为事件级，因果链靠 `ref`=实例 id＋各自存储元数据）。全链：`pytest -q` **2725 passed / 51 skipped / 7 subtests RC=0（94.78s）**（较上批 +3）、`gates.sh` **RC=0**（undefined-name tests **159** 未变=本批无新测试文件；余逐项同前），`test_af_mqtt_bridge.py` 分跑 `36 passed RC=0`；两文件 `CRLF: 0`、`git diff --numstat` `23 1`/`58 0`（无整文件重写）。文档侧：§二之二十二 新篇、§〇 加"出向对账"行、§五 第 12 件、核实基准 → `5ff1ea6` |

| 本批之三十（CI 复查 + 一次推错分支的自证与纠正，纯文档） | **run 52（`64f4935`）/run 53（`24ef0c0`）事后复查 `completed/success`** ⇒ §〇 那行"连续绿 18 条（run 34–51）"过时，实际 **20 条（run 34–53）**。**自己的错要一样记账**：推这批提交时用了 `git push -q origin master`，而本仓**本地叫 `master`、GitHub 默认叫 `main`**（`git status -sb` 就写 `## master...origin/main`）⇒ 在远端**新建**了 `refs/heads/master`（GitHub 回 "Create a pull request for 'master'"），加上 `ci.yml:5` 触发分支含 `[main, master, dev]` ⇒ 多跑一套 CI（run 54），而 main 当时仍停在 `24ef0c0`。纠正 `git push origin master:main`（`24ef0c0..0a834b9`，`PUSH_MAIN_RC=0`），删除误建分支 `git push origin --delete master`（`DEL_RC=0`；删前先确认它等于本地 HEAD、且是 90 秒前自己新建的，无其他引用），删后 `git ls-remote --heads origin` 只剩 `refs/heads/main = 0a834b9` ⇒ 远端回到单分支原状。**同时抓到自家一条取证规矩的破口**：第一次打印的 `PUSH_RC=0` 是管道尾巴 `tail` 的退出码、不是 push 的（`${PIPESTATUS[0]}` 才是），修正后每次推送都取真实 RC 并补 `ls-remote` 自证。取数时 run 54/55 的 `quality-gates` 均 `completed/success`（55 正文读到六节绿行、末行 `结论：门禁干净…`），`pytest`/`adm-linkage-contracts`/`layering-gates` 仍 `in_progress` ⇒ 两套都**不计入**连续绿，等结论再记。另把"覆盖面/兼容面"两条实测补进 §二之二十二：`FAILED` 只有 `af_instance.py:288` 一处入口（且排在 `on_terminal()` 前）⇒ 修复覆盖每条 failed 事件；AF 内部无任何消费方读 `error`（除发布点只剩白名单脚本与 CLI 文案）⇒ 不存在"有人指望它是状态名"的自家兼容风险 |
| 本批之三十一（把观察者路径的真实键集钉死，盘出契约外多发的 `node_id`，`159ba00`，只加测试） | 打完 `error` 那半之后，按**真发出去的载荷**对契约 §1.2 做最后一次全字段清点，读到 `fired = {automation_id, instance_id, node_id, ref, trace_id, ts}`、`failed` 再多一个 `error` ⇒ **`node_id` 既不在契约行也不在裁定 20261002 里**（`instance_id` 那份额是 ②A 登记过的过渡字段）。老那条"逐字段对契约"的判据没有错：它把**直接调用面**钉成 `{trace_id, ts, automation_id, ref, instance_id}`，而 `node_id` 是 `observe_terminal()` 在调用点经 `**extra` 注进去的——生产唯一发事件的路径是后者 ⇒ 两条路各测一头，铁律 #5 意义上的"测到 ≠ 覆盖到"。补一条按真实例断言**观察者路径**键集合的判据（并验多发的键真等于 `instance.current_node_id`，不许是空壳）：**四档变异 `0/1/1/2`**（M-0 `37 passed RC=0` 必须绿；M-1 fired 少发、M-2 failed 少发各红一条；M-3 在 `_envelope()` 凭空加键 `phase` 红**两条**，实测 `--tb=no` 点名的正是新老这两条），从内存字节还原、收尾 `逐字节一致=True`。顺带核到 `node_id` 的语义坑：`current_node_id` 只是 `ctx.current_node` 读数（`af_instance.py:150-151`），`fail()`/`_transition()` 都不改它、由执行链跳边时推进（`af_executor.py:156/252/781`）⇒ failed 事件带的是"失败时刻停在哪"而非"哪个节点失败"。**删字段属三方共读面 ⇒ 不自裁**，作为**第 12 件第三问**并入同一份申请（同根：§1.2 载荷行没写清），AF 倾向"DB 不读就删"。另把 §二之二十二 里 `af_mqtt_bridge.py:427` 两处**过时行号**就地更正为 `:449`（本仓 `5ff1ea6` 之后该文件整体下移，铁律：正文行级引用对当前工作区复测）。全链：`pytest -q` **2726 passed / 51 skipped / 7 subtests RC=0（93.36s）**（较上批 +1）、`gates.sh` **RC=0**（undefined-name tests **159** 未变、主题白名单 7 处、包标记 5/96、参数注入 96/96 豁免 2、工具名单 96/31 豁免 0、状态源 fail-closed 5/5/豁免 0），分跑桥测试 `37 passed RC=0`；测试文件 `CRLF: 0`、`git diff --numstat` `21 0` |
| 本批之三十二（CI 复查到 run 57，纯文档） | `gh_ci_status.py runs` → `success runs: [57, 56, 55, 54, 53, 52, 51, 50, 49, 48]`（`RUNS_RC=0`）⇒ **run 54/55/56/57 四套全 `completed/success`**，上一版"两套不计入"的那句已落地；按 main 计**连续绿 23 条（run 34–57）**，run 54 是误建 `master` 分支那套重复（同样绿，单列不混进 streak 口径）。run 57 五作业逐条 success、`failed_steps` 全空；runner `pytest` 正文 `2726 passed, 51 skipped, 1 warning in 80.41s (0:01:20)` **与本机逐字相同**，对照 run 56 的 `2725 passed…` ⇒ +1 判据确实来自 `159ba00` 而非口径漂移。推送按规矩走 `git push origin master:main`（`PUSH_RC=0`，`f06b162..b148574`），收尾 `git ls-remote --heads origin` 只有 `refs/heads/main = b148574…` ⇒ 远端仍单分支 |
| 本批之三十三（把"测到 ≠ 覆盖到"升成硬门：出向 MQTT 写者门禁上线，`8b629b8`） | 本批之三十一那条不是个案，是**结构缺口**：一条测试长期绿，绿的却是一条生产永远不走的路。契约测试补一条只钉住**已知这一个形状**，下一个破例得有人记得替它补用例 ⇒ 把这轮根因教训 generalize 成静态门。能升成门取决于**判据的形状**：`error=state` 与 `error=reason` 在 AST 上同形（价值语义只能行为断言），而"哪个文件、哪个最内层函数、载荷是不是 `_envelope()` 派生"是调用图形状 ⇒ 可硬判。三条规则（`scripts/check_mqtt_writers.py`，纯标准库 AST）：**A** 事件发布器 `publish_fired`/`publish_failed` 只许出现在 `af_mqtt_bridge.py` 内、且只许由 `observe_terminal()` 调用（归属取**最内层包围函数**，函数里再定义闭包发事件也照样红）；**B** 出向写者调用点（`_mqtt.publish`、`_presence.advertise`）只许在桥内；**C** 桥内 `_publish(topic, payload)` 的载荷必经 `_envelope()`。锚点 = 桥里的 `FIRED_TOPIC`/`FAILED_TOPIC` 两个模块级常量 + `_envelope`/`observe_terminal`/`publish_*` 五个函数名，**读不到即 exit 2**（§二之十四 同一课：改名会让门静默全绿）。本机实测 src 读数：**出向写者调用点 2 处、分布在 1 个文件；事件生产者 2 处、分布在 1 个文件；桥内 `_publish(topic, payload)` 2 处，其中载荷来自 `_envelope()` 的 2 处；现场豁免 0 处** ⇒ 绿色行逐字节只报这些数字。反例 **18 条**（`tests/unit/test_mqtt_writers_gate.py`）覆盖三条规则各自的红/绿两侧、`af_bus.publish` 不在射程、桥内 `advertise` 不误红、手写 dict 与 `json.loads` 载荷皆红、豁免理由不能空且单独计入读数、三种锚点缺失形状皆 `exit 2`。变异三档：**M-1 `RC=1`**（桥内 `resend()` 里多接一条生产者，finding 点名 `resend()`），且**同一处违例另跑完整 `gates.sh`** 拿到真红行 `结论：出向 MQTT 写者门禁红（exit=1）`（要证的是接线也在，不只脚本能红）；**M-2 `RC=1`**（手搓 dict 当载荷、绕过 `_envelope()`）；**M-3 `RC=2`**（`_envelope` 改名 ⇒ 锚点读不到）。两个**自捕的坑**都记进 §二之二十三：① 第一版绿行硬写了"写者 1 个模块"这个形容词式结论（铁律 #5——把未计数当已验证）⇒ 改成逐文件计数并加一条测试，断言打印数字 == `check()` 的 stats 且"只在/全部"这类字样绝不出现；② 规则 C 初版只认位置参数，`self._publish(topic=…, payload=…)` 会**静默放行** ⇒ `_payload_arg` 两态都认，并各钉一条红/绿测试。另有两处执行事故如实记账：变异驱动器首跑崩在还原之前（`subprocess` 文本模式吃 GBK 输出抛 `UnicodeDecodeError`，`af_mqtt_bridge.py` 残留 `resend()`），先 `git diff --stat` 核实差异恰为那 3 行新增、无在飞工作，才用 `git checkout --` 复原（默认口径仍是**内存字节还原**，此为已核实的例外）；第二次是**代理信号**——`bash` 解析到 `C:\Windows\System32\bash.exe`（WSL 无发行版，UTF-16 提示）返回 `RC=1` 看着像门禁红，换 `C:\Program Files\Git\bin\bash.exe` 才拿到上面那条真红行。全链：`pytest -q` **2744 passed / 51 skipped / 7 subtests RC=0（97.39s）**、`GATES_PYTHON=python bash gates.sh` **RC=0**、undefined-name tests **159→160**（+本门测试文件）、门本体 src 实跑 `RC=0`、还原后 `逐字节一致=True`、三份新/改文件 `CRLF: 0`。文档随本条一并落盘：新增 `## 二之二十三`、§〇 加本门一行、核实基准 → `8b629b8` |
| 本批之三十四（同一族教训用到入向：订阅门禁上线；顺带 CI 复查到 run 59，`b50bb22`） | §二之二十三 那条教训（**测试绿在一条生产不走的路径上**）在入向有完全同形的缺口：契约表 §1.3 护栏 + 计划 第 1 步 ④ 说"AF 不订阅 `butler/inbox/*`（收件箱是 DB 的）"，今天撑着的只有运行时两道判定（`af_mqtt_bridge.py:406` 的 `subscribe_topic()`、`:417` 的 `handle_message()`）与认识已知入口的行为测试——**下一个不查禁订族的 `self.client.subscribe(…)` 一条测试都不会红**。判据形状与出向那批同类（哪个文件、哪个最内层函数、实参是不是那族前缀）⇒ 可做硬门。三条判据：A 订阅口只在 `af_mqtt_bridge.py`（射程取两条信号**并集**：接收者是 `client`/`self.client` **或**调用带 `qos=`——只用接收者名漏 `hub.subscribe(topic, qos=…)`，只用 `qos=` 漏不传 QoS 的写法）；B 非 `INSIGHTS_TOPIC` 的实参必须在**同一函数体**内出现禁订族判定（守卫在外层、订阅藏在闭包 ⇒ 红）；C `butler/inbox/` 前缀写死成实参一律红，带守卫也不给过（守卫是给动态入口兜底的，不是把越界意图写进代码的通行证）。锚点 = 模块级 `INSIGHTS_TOPIC`/`FORBIDDEN_SUBSCRIPTIONS` + 类里 `start`/`subscribe_topic`/`handle_message`，读不到 `exit 2`。进程内总线不判（`af_bus.subscribe(key, handler)`、`af_vhass` 两处 `bus.subscribe(callback)` 既无主题字符串也无 QoS）。真实 src 实测：**MQTT 订阅点 2 处/1 个文件，其中 `INSIGHTS_TOPIC` 1 处（`:396`）、过了守卫的动态入口 1 处（`:410`）、豁免 0** ⇒ 计划 第 1 步 ③④ 那句"只订 `ma/insights`"从"测试说"升成"静态说"。反例 **20 条**，含两处防假绿的自证：已知好形状**必须被计数**（`sites/insights/guarded=2/1/1`，否则"没进射程"也报绿）、绿色行只印数字（"全部/只在/都经"字样出现即红）。变异四档 **M-1/M-2/M-3 `RC=1`**（桥内不查守卫的订阅口 / 桥外 `hub.subscribe(topic, qos=…)` / 写死 `butler/inbox/#`）**+ M-4 `RC=2`**（`subscribe_topic` 改名 ⇒ 锚点缺失），M-1 同处违例另走**完整 `gates.sh`** 取到 `结论：入向订阅门禁红（exit=1）`——bash 一律用 `C:\Program Files\Git\bin\bash.exe` 绝对路径（上一批那条 WSL 假红教训就地复用为规程），从内存字节还原自证 `bridge/service 逐字节一致 = True`、还原后 `RC=0`。全链：`pytest -q` **2764 passed / 51 skipped / 7 subtests RC=0（96.28s）**（较 `8b629b8` 净 +20）、`GATES_PYTHON=python bash gates.sh` **RC=0**（undefined-name tests **160→161**，其余七项逐项不变）、`gates.sh` 纯增 `+17/−0`、两个新文件 `CRLF: 0`、**产品码 0 变动**。**CI 复查**：run 58（`a7c2711`）/run 59（`cc1048b`）均 `completed/success` ⇒ 按 main 计**连续绿 26 条（run 34–59）**；run 59 的 `quality-gates` 正文读到出向写者门那行四条计数与本机**逐字相同**、`pytest` 正文 `2744 passed, 51 skipped`；run 60（`b50bb22`）的 `quality-gates` 已 `completed/success` 并读到本批新门那行与本机逐字相同，其余三作业取数时 `in_progress` ⇒ 不预先计入 streak。文档侧：新增 `## 二之二十四`、§〇 加本门一行 + CI 行补 run 58–60、核实基准 → `b50bb22` |
| 本批之三十五（窗后四项验收做成一条命令，并更正"本机无 paho-mqtt"这处理由，`da09d43`） | §六 那行"缺任一项即第 0 步未完成、不许用『配置正确只是没抓包』过账"挂了四批，缺的从来不是判据而是**一条可重复的命令**：四件散在正文里，窗当天靠手搓，而手搓正是过账出事的形状（少跑一项、把 skip 读成 pass、把 `/health` 打错成 404 就判服务没起）。交付 `scripts/verify_adm_window.py`：**逐项判定 + 三态结论**（PASS/FAIL/UNAVAILABLE），有 FAIL ⇒ `RC=1`，无 FAIL 但有缺项 ⇒ `RC=2` 且正文写 `EXEMPT ≠ VERIFIED`，全 PASS ⇒ `RC=0`；优先级专门钉一条测试（FAIL 不被"环境缺项"稀释成 2），读数行 `PASS n / FAIL n / UNAVAILABLE n` 是数出来的。判据取自契约表而非"能连上就行"：**③** 查 §1.2 四个键（缺 `ref` 就点名 `ref`）+ `ts` 与当下的偏差超 `--max-skew`（默认 900 秒）判红 ⇒ 专拦仿真锚点 `2026-09-14T08:00+08:00` 当墙钟那一族，`ts` 无偏移也判红（读不出就不猜）+ §1.2"事件类永不 retained"⇒ fired 以 `retain=True` 送达判红（本批对表新加的一条）；多余键不判红（`node_id` 仍等 §五 第 12 件），只把实际键集印出来。**④** 直接问 `msg.retain`，不用"两次订阅"间接法（现发的 `online` 与 retained 快照正文一模一样）。**②** 的"连不上"由 ① 的实测决定语义：容器确认在跑却连不上 ⇒ FAIL，本机没 docker ⇒ UNAVAILABLE——同一句 `ConnectionRefused` 在两种机器上含义相反，写死任一种都会假。**自纠两处草稿缺陷**：(a) 初稿自造 `AF_MQTT_USERNAME`/`AF_MQTT_PASSWORD` ⇒ **第二真源**，键名收编在机制层 `resolve_credentials()`（`MQTT_USER*`/`MQTT_PASSWORD`/`MQTT_PASS`/`MQTT_PASSWD`，缺一半视同匿名即抛），改成 `hm.broker_settings()` + `hm.get_client()` 后脚本**从不接触口令**，并加结构性自证 `test_script_never_reads_credentials`（源码内 `os.environ`/`getenv`/`password`/`PASSWD` 零命中）；(b) 初稿 shell 调 `mosquitto_sub` 的 `-W`/`-c` 两选项而本机无该二进制 ⇒ 选项形状无从实测（`-c` 尚有 `--clean-session` 之义的嫌疑），把窗当天的验收建在没跑过的命令签名上正是本计划要防的形状 ⇒ 改用 paho。**与裁定措辞有一处出入如实标注**：原文写 `mosquitto_sub` 抓到，本批按**意图**（broker 侧真收到该载荷）实现，若 DB/DCD 要求命令行工具，改回成本是一个 `_collect`、判据不动。**更正一条错了四批的理由**："本机无 paho-mqtt"在本文档出现 **5 处**，实测 `python -V`=`Python 3.13.2`、`paho module: …\Python313\Lib\site-packages\paho\mqtt\client.py`、`homesdk.mqtt.paho_available()=True` ⇒ paho 一直在跑测试那份解释器里，是本机多条 Python 环境"缺包九成跑错解释器"的又一例；**结论不变理由换**：真缺的是那台 broker（`broker_settings()` 实测抛 `MissingEnv: 环境变量 HOMESDK_MQTT_HOST（或 MQTT_HOST）未设置或为空`，`docker`/`mosquitto_sub`/`mosquitto` 三条 `command -v` 全 `NOT-FOUND`），5 处已逐条改写，免得后来人按错理由找错解法（装 paho 解决不了任何事）。**实测读数**：降级跑 `RC=2` 四行全 `[UNAVAILABLE]`；本机起 `forge serve --port 8791 --store-root <Temp>`（只读面、铁律 #3 不碰 NAS）后 ② 取到真读数 `[PASS] /api/health 返回 200，键 contract_version,milestones,ok,readonly,store_ok,tick_exit_reason`，汇总 `PASS 1 / FAIL 0 / UNAVAILABLE 3`、`WINDOW_RC=2`；顺带钉两条事实——AF 真注册的只有 `/api/health`（裁定原文的裸 `/health` 会 404），`docker/docker-compose.api.yml` **没有 healthcheck**（只映射 8787）⇒"容器 Up 但服务没起"只能靠 ② 拦。新反例 **27 条**（三态各覆盖 + `_collect` 把 `MissingEnv` 转成 UNAVAILABLE 而非 traceback）；变异三档各 `RC=1  1 failed, 26 passed`（拆"事件不许 retained"/拆 `ts` 偏差/把 UNAVAILABLE 当通过）且还原 `逐字节一致 = True`、`DRIVER_RC=0`，第一次驱动跑因脚本自身引号未闭合在解析期即死、目标文件零改动（如实记）。全链：`pytest -q` **2791 passed / 51 skipped / 7 subtests RC=0（123.73s）**（2764+27 逐条对得上）、`GATES_PYTHON=python bash gates.sh` **RC=0**（出向/入向两门计数与 §二之二十三/二十四 逐字相同，本批不碰 `src`）、`git diff --cached --numstat` = `268 0` + `326 0`、两新文件 `CRLF: 0`、**产品码 0 变动**。**CI 复查**：run 60（`b50bb22`）与 run 61（`16ad74e`）均 `completed/success`，run 61 五作业逐条 `success`、`failed_steps=[]`，`pytest`（job `111156038061`）正文 `2764 passed, 51 skipped, 1 warning in 84.57s` 与本机同数 ⇒ 按 main 计连续绿 **27 条（run 34–61）**。文档侧：新增 `## 二之二十五`、§〇 加本批一行 + CI 行补 run 60–61、§六 窗后四项那行重写并更正 5 处理由、核实基准 → `da09d43` |

## 四、审计侧

第七轮唯一 finding 在 HEAD 上**属实**（不是已修项的重报）：`HAStateProvider` / `HassStateProvider` 对未知实体静默跳过，而仿真侧 `InMemoryStateProvider` 抛 `UnknownEntity`，同一 IR 两判相反。短路口是 `af_ir/expr.py:514-517`（`and`→`all()`、`or`→`any()` 生成器表达式，未求值分支根本不碰 `Snapshot.get`），所以生产把条件判真、仿真把条件判假。
修复落点 5 处 + 契约测试 `tests/contract/test_state_provider_policy.py`（14 项，含两条铁律 #8 的"能变红"反证）。完整核实链、"连续六轮未修复"四条重验表、部署声明见 `docs/audit/审计报告_第七轮_核实与修复.md`。

⚠️ 审计口径事实：第六轮与第七轮读的是**同一个** zip 快照 `zip-snapshot-2026-09-29T22:43`（不是 GitHub HEAD），故其"连续多轮未修"多数条目已在 HEAD 修复；引用审计报告前须按铁律 #11 先对 commit 复测。

**第八轮截至本批未投递**（`ls -lat docs/audit/*.md` 最新一条 = `审计报告_第七轮_核实与修复.md`，mtime `Oct 2 11:46`）。本批自审抓到一条与审计同型、但审计面按 zip 快照读不出来的**门禁级**缺陷：`.gitignore` 吞包标记 ⇒ CI 的架构门禁比本机少分析 10 个模块（86 vs 96）。它的难看之处在于**不会让任何东西变红**，只会让该红的东西不红，因此只能靠"两个口径的数字对账"发现——登记在此，避免被读成"本轮无自审产出"。见 §二之十一。

## 五、已提 / 待提 DCD

| # | 事项 | 状态 |
|---|------|------|
| 1 | 第 4 步契约面四问：MCP 工具面命名口径（`draft/verify/deploy` vs `af_draft`/`af_apply`）、`af/automation/fired` 的 `ref` 语义（实例 id 还是 deploy ref）、`/api/asks/answer` `ok=False(inbox_key_missing)` 的 DB 侧处置、`ma/insights` 提案队列是否要求持久化 | `inbox/20261002-AF-第4步契约面四问-决策申请.md` → **已裁定（①A ②A ③A ④A，全 A）**，AF 侧四条均已落地（见 §一 末"裁定 20261002"表） |
| 2 | 第 0 步 ③④：0.3.1 记账缺口（源树 `time.py` 未入库 + NAS `VERSIONS.txt` 无 0.3.1 条目 ⇒ sha 不可复现）+ §四 合并停机窗排期与授权 | `inbox/20261002-homesdk0.3.1记账缺口与AF镜像重烤合并窗-决策申请.md` → **记账半边已裁定并已执行**（DCD §〇：源码+契约表入库 `5e4ba33`、VERSIONS 补条目、以重建 `b4b5d6bb…` 为权威，AF pin 与 vendored wheel 已跟新）；**镜像重烤半边 = 合并窗内做，尚未生效**（窗口未开） |
| 3 | 第七轮 `StateProvider` 统一 fail-closed 的方向与代价（整段软失败 vs 逐实体可见漂移） | `inbox/20261002-AF-StateProvider统一fail-closed的方向与代价-决策申请.md` → **已裁定：A 先行 + B 排队**，B 启动条件 = 下次真实改动 `af_instance._refresh_snapshot` 或 AF v2.6；整段口径被裁定为**有意设计**；漂移须以 `entity_drift` 记账 |
| 4 | 第 0 步 ④ 的记账载体：`E:/NAS/AgentOps` **不是 git 仓** ⇒ 已授权改的门禁模板无 commit、无 sha、不可回滚；三仓 CI 都依赖它，却没有任何版本账 | `inbox/20261002-AgentOps门禁模板无版本载体-决策申请.md` → **已裁定**（`decisions/20261002-AgentOps与MA交付面-裁定.md` §一）：**A 建仓为目标态**（NAS bare 加 `agentops.git`、模板首次 commit、此后走 PR），**建仓与推送授权"与合并停机窗同批"** ⇒ AF 侧本窗不动；**C 兜底本窗已落**（申请区贴了模板 sha256 `887c33dd…`）。裁定另点名两件事：模板分发=**各仓按模板重写自己的 CI**（重写即该仓一次 CI 变更，走各自 PR），`memory-agent/.github/workflows/gates.yml:56` 的 `continue-on-error` 由 **MA 侧修**，AF 不碰他仓 main |
| 5 | 新 `ui` CI job 将**首次**让 GitHub runner 按 lockfile 去第三方镜像拉 137 个包（`ui/package-lock.json` 实测 137/137 指 `registry.npmmirror.com`、0 条 npmjs、0 条缺 `integrity`；根因是用户级 `~/.npmrc` 漏进共享产物）。这是 DPP 同题申请（R7）在 AF 的**前提修正**：那边选"只登记"的承重句是"CI 只在我这台机器跑"，AF 今天把这句话拿掉了 | `inbox/20261002-AF-CI首次消费镜像锁文件-DPP-R7同题补充-决策申请.md` → **已裁定 (b)**（`decisions/20261002-AF锁文件源与MA载荷键名-裁定.md` §一：只把 CI 侧改指官方源、**不动锁文件字节**、本机开发照旧）。**AF 已落地**，但落法比裁定原文多一条 flag：只写 `--registry=` 经实测是**空操作**（fetch 全在 `npmmirror` 的两个主机上），必须配 `--replace-registry-host=always`；同时给安装步加了主机自证守卫（CI 绿不能证明没吃镜像）。逐字节对账 137/137 MATCH ⇒ "不动锁文件"成立。读数见 §二之七 |
| 6 | 上一条落地时抓到的两件事要回给 DCD：①裁定原文那条命令是空操作，落法已补一条 flag（问"算不算改判"）；②裁定 §四 判例 2 的证据不成立——`@types/node@22.20.2` 的 tarball 在**两个源逐字节相同**（`size=447116`、`sha256` 前缀同为 `64921eb9b6caae37`、解包根目录都是 `node v22.20`），"根目录不是 `package/`"是 DefinitelyTyped 自己的发布布局，不是镜像重打包的痕迹 | `inbox/20261002-AF-锁文件源裁定落法补正与判例2旁证更正-回执.md`（**已提交，待回话**）。同件另附一条小问：安装步那条"fetch 主机集合 ⊆ {批准的那一个域} 且该域命中数 > 0"的守卫形状要不要提升到 AgentOps 模板——按 20261002 §一"分发=各仓自写"的口径，AF 默认不动 |
| 7 | `ma/insights` 载荷的三处契约空缺（AF 的入向此前按自家键必填，按契约发来的每条洞察都会被拒——判例 1 的静默归零换到了 AF 入口）：① `conf` 要不要正式进契约行（AF 已按"缺报按 0.0 入 ask 档 + `conf_reported=false`，面板显示「未上报」"落地，等追认）；② `trace_id` 是"一次联动的追踪号"还是"这条假设的稳定身份"——AF 现在拿它当 `hypothesis_id` 与回灌键 `hypothesis:{id}`，若每次播报都换，去重与回灌都会散；③ MA 会不会发结构化 `intent`（契约行没有这一项 ⇒ 生产里每条洞察都是「无 IR（不能批准）」，要改的是人的工作流还是契约，AF 不擅自扩面板） | `关键决策部/inbox/20261003-AF-ma-insights载荷conf与稳定id与intent-决策申请.md`（**已提交，待回话**）。AF 侧不等裁定：别名/缺报记账/有界 transport 已在同批进仓，判据见 §二之八 |
| 8 | 两件合一份：①**第 5 步 ③ 第二档的时点**——裁定给的是"一个版本**或** 2 周"，`lint-imports` 进 CI 是 `20356dc`（10-01），AF 在 10-03（日历 2 天、无版本发完）就把 `continue-on-error` 去掉，判据是"runner 连续 7 条 run `KEPT` + 误伤 0 + 一次可红实测"；这是 AF 单方改窗口长度，故不求"裁定已满足"的认定，只求追认时点（A 追认 / B 判回退一行 YAML / C 把口径改成可判定的量）。②**`_*.py` 吞包标记的跨仓外溢**——`.gitignore` 的 `_*.py` 会连带吃掉 `__init__.py`，实测让 AF 两条架构门禁在 runner 上少分析 10 个模块（86 vs 96）；MA/DB/homesdk/AgentOps 模板是否有同款规则，AF 不擅动他仓，建议先做零改动探测（各仓一次 `git check-ignore -v` / `git status --ignored --short \| grep __init__`），是否把"包标记门禁"上收模板请一并裁定（注意 20261002 §三 对 fetch 守卫给过"先不落"） | `关键决策部/inbox/20261003-AF-import-linter升硬门时点与gitignore吞包标记-决策申请.md`（**已提交，待回话**）。AF 侧不等裁定：第 2 件已在本仓自闭合（负向规则 + `scripts/check_pkg_markers.py` 进 `gates.sh`，还原原状仍能取红），第 1 件若判 B 一条 `git revert` 即退，均不占停机窗。判据见 §二之十一 |
| 9 | **第 5 步 ② 的「顺序追加」半边**：裁定 20260930 给 af_persist 的执行约束第 2 条要求"追加写 + 文件头部元信息"，而现实现是每实例一个快照文件、`save()` 整文件原子重写（`af_persist.py:171-187`）。三条张力不是 AF 能自决的：①它同同一份裁定的"不改存储格式头/不迁移"相抵（改成追加写**必然**动格式头）；②第五轮审计已把"每条全量重写 + 明细无上限"收成有界（`docs/audit/审计报告_第五轮_核实与修复.md:38-74`），再上追加写等于把那一轮的修法反向；③裁定本身把这一项降级为"不做全量 eventlog"。要么 A 判该子句对 `af_persist` **不适用**（AF 倾向：B 的形状要新增一类存储产物，已越出裁定自己给的"改动小、不迁移"档位）、要么 B 判必须做但给有界形状（每实例 `.jsonl` 只留最后 N=8 条、写侧纯追加 O(1)、读侧仍以 `.json` 快照为权威）、要么 C 判并入未来 `af_eventlog` 另立项 | `关键决策部/inbox/20261003-AF-af_persist追加写半边与裁定执行约束张力-决策申请.md`（**已提交，待回话**）。AF 侧不在裁定前擅自动存储面：本批只把 §〇/§一 的"全部落地"过度声明改成本节口径。判据见 §二之十二 |
| 10 | **单写者租约只装了 HTTP 一面，MCP 真机写未受约束**：AF 自己定的"一个 store 只能有一个写者"这条纪律目前有三处落点却只在一面上生效——`af_cli.py:1365` 的 `FileLock(store_root/".serve.lock")`（抢不到即 `readonly=True`）、`af_api.py:344` 的 `_readonly_guard`（8 个写端点挂 `Depends`）、以及 `build_app(readonly=…)` 这个入参本身；而 `af_mcp.serve_mcp` 与 `dispatch` 对 `readonly|lease|acquire` 三个词 **grep 零命中**（实测），`af_live_run` 的 scope 又确实是 `"live"` ⇒ Agent 侧可以在 serve 已持写权时并行开真机写。这与 §五 第 1/7 件不同：**它不是补 AF 漏装的自家闸门**（那种我直接落地，见本批 §二之十六 的两处），而是要给 DB 新增一个它此前不会遇到的**拒收模式**——MCP 在锁被占用时返回什么码、用 `check` 还是 `acquire`（Agent 进程与 serve 同机/分机两种拓扑答案不同）、是否只拦 `live` 还是连 `apply`/`bind` 一起拦，三项都会改变 DB 看到的失败面 ⇒ 按 20261002 §一"契约面改动走裁定"的口径申请 | `关键决策部/inbox/20261003-AF-单写者租约只装了HTTP面MCP真机写未受约束-决策申请.md`（**已提交，待回话**）。AF 侧**代码一个字节未动**；同批已自主落地的是前两件（`dc8ac0d`：`_t_live` 补 `store`、events 上限上收到 `af_service`），判据与三次变异读数见 §二之十六 |
| 11 | **"新增有界缓存必须同时给 TTL 与硬上限 + 纯写不读也必须被回收"这条约定要不要升级成能判红的门禁**：本批拆 §六 那行混写时盘出来的。约定的两处落点现状不对等——"加统一基类"那一半第六轮 §三 已判**不做**（`git log -S"BoundedCache" --all -- src tests` **0 命中**，该符号从未进过代码；第七轮 `:86` 重申），但"**门禁可见**"那一半是**真空**：`grep -rn "有界\|TTL" gates.sh scripts/*.py` 零命中 ⇒ 约定只活在审计正文里（§二之十一 记过同一族"写在正文里的约定"）。AF 本批把静态口径真做到有读数：AST 扫"类里 `self.X = {}/[]/deque` 且同类内 `X[k]=`/`append`/`update`" ⇒ src 全集 **7 个候选**，5 个同类内已有界证据（`ConflictAuditor.events`/`JsonFireStore._records`/`InstanceManager.context`/`PreferenceModel._records`/`UndoStore._records`，与第六轮"四条腿 + `_SESSIONS`"的账一致），**"缺界"那 2 个逐条读码后全是误报**（`PreTriggerService._stats` 固定键计数器、`DeviceSM.attributes` 键集由 domain 属性词表决定）⇒ 真阳性 **0/2**，"扫无界容器"静态上判不出可靠口径，硬做上门第一天就带两条永久红、只能挂豁免表，而豁免表正是 §二之十九 刚扫掉的那族第二真源。三档 A（清单 + 成对断言测试，无门）/ **B（注册表式门：仿 `af_mcp.TOOLS` 形状，判"双腿齐全 + 测试 id 真被收集 + 新增增长容器必须登记"，AF 建议）** / C（统一基类 + 无界扫描 ⇒ 动 `af_audit`/`af_preference` 持久化层，需停机窗）；请 DCD 定向两点：①存量是否接受"基线冻结、新增必须登记"；②"固定键 / 词表有界"这一类给不给正式豁免 | `关键决策部/inbox/20261003-AF-有界缓存生命周期约定要不要升级为硬门禁-决策申请.md`（**已提交，待回话**）。AF 侧**代码一个字节未动**；本题不涉 ADM 契约表（无跨仓载荷、无主题），但严格度选型会决定这道门以后拦人还是拦格式 ⇒ 不自裁 |

| 12 | **出向事件 `trace_id` 的语义：事件级还是因果链级**（与第 7 件同族、方向相反——那件是入向载荷，这件是 AF 自己发的两条）。契约表 §1.2 只列字段名（`{trace_id, ts, automation_id, ref}`，failed 多 `error`），没写谁造、是否须等于上游、一条 trace_id 允许对应几条事件；AF 现状每次发布现场 `uuid4().hex[:12]` ⇒ 同一实例的 fired 与 failed 也不是同一枚号，MA→AF→DB 的因果链在 AF 这一跳断。上游那枚号在 AF 侧并没丢（`ingest_insight` 收进 `hypothesis_id` → 提案落盘 → approve 时写进 GraphStore 记录 `note="ma_insight:{…}"`），**但到不了发射点**：`grep -rn hypothesis_id src/autoforge/af_ir/ src/autoforge/af_instance.py src/autoforge/af_executor.py` = 0。三档 A（因果链级：同一字段两种语义、DB 检索由 1:1 变 1:N、要把号搬进 IR/实例上下文）/ B（新增 `insight_trace_id`：三方共读面）/ **C（AF 建议：`trace_id` 明确为事件级，契约表加一句口径，因果链靠 `ref`＋各自存储元数据）**；另核到 §1.3 护栏第 4 条那句"trace_id 必填：贯穿洞察→收件箱→播报"**挂在收件箱三面之下**，而 `grep -rn "publish.*butler/inbox" src/autoforge` = **0** ⇒ AF 不踩那条，本题不是违规而是 §1.2 没写清。同批已自决的另一件（`error` 恒等于状态名）在 §二之二十二，申请里另向 DB 问了展示宽度；**申请现含三问**——第三问是本批打真载荷盘出的 `node_id`（契约行与裁定都没有它；发的是"失败时刻停靠节点"而非"失败节点"，`fail()`/`_transition()` 不改 `ctx.current_node`，执行链在 `af_executor.py:156/252/781` 跳边时才推进），AF 倾向"DB 不读就删"，删/留与语义都请一并裁 | `inbox/20261003-AF-事件载荷trace_id是事件级还是因果链级-决策申请.md`（**已提交，待回话**）。trace_id 生成方式 **AF 侧一个字节未动** |

## 六、未在本版做（登记，不静默）

- 第 0 步 ③④：镜像重烤 + AgentOps 模板生效 = 合并停机窗内的动作（裁定 §一 已把顺序写死：**账 → wheel → 镜像 → 模板**；回滚反序 **模板 → 镜像 → wheel → 账**）。账与 wheel 两件已由 DCD 完成，AF 的 vendored wheel 也跟上；**后两件未做**。
- 窗后 AF 侧验收四项（裁定原文，缺任一项即该步未完成、不许用"配置正确只是没抓包"过账）：`compose ps` 起来、`/health` 200、抓到一条含家庭墙钟 `ts` 的 `af/automation/fired`、`adm/autoforge/status` retained `online`。**本批把四件做成一条命令** `scripts/verify_adm_window.py`（PASS/FAIL/UNAVAILABLE 三态；有缺项 ⇒ 退出码 2 并打印"EXEMPT ≠ VERIFIED"，不给部分绿留一条印成绿色的路；判据取自契约表 §1.2/§1.3；连接与凭据走机制层 `homesdk.mqtt`，脚本自身不读任何环境口令），本机实测 `RC=2`、四项逐项读数见 §二之二十五。**该项仍是 EXEMPT 不是 VERIFIED**：本机没有可连的 broker（`MQTT_HOST` 未配、无 `docker`/`mosquitto`），`kill -9` 与 retained 那两段只能在 NAS 上取数。顺带更正两处旧登记：① "本机无 paho-mqtt"是跑错解释器的结论（`homesdk.mqtt.paho_available()` 实测 `True`）；② 裁定原文里的 `/health` 在 AF **不存在**——真名只有 `/api/health`（`af_api.py` 注册的那一条），且 `docker/docker-compose.api.yml` **没有 healthcheck**，故脚本按 `/api/health` 优先、`/health` 兜底两态都探，并把命中的路径印进读数（"打了 `/health` 拿到 404"不等于服务没起）。
- 第 2 步 `kill -9` → broker 代发 offline 的真 broker 验收：同上，须进窗随镜像重烤一次跑。
- ②A 的 `instance_id` 过渡字段**未删**（删除时点 = AF v2.6，属破坏性变更须与窗口同做）。
- §四 的 B（逐实体可见漂移 + `entity_drift` 记账）**未做**，按裁定的启动条件排队：下一次真实改动 `af_instance._refresh_snapshot` 时顺手做，或 AF v2.6。
- 洞察面板的**投递源仍是本机手投**：§二之四的两条提案是用 `PersistentInsightSink.submit()` 直接写进 dev store 的，走的是"落盘之后的那一段"。本批把**桥回调 → 落盘 → `/api/insights/pending`** 这一段用契约形状的假消息钉住了（`test_contract_shaped_insight_reaches_the_panel_with_its_accounting`，注入假 client、真桥、真队列、真 API），但**真 paho + 真 broker** 那一段仍未端到端（paho 本机实测有，缺的是那台 broker：`MQTT_HOST` 未配），面板的空态文案因此把"桥未上线/没订到主题"列为四种成因之一，而不是当作已验证链路。
- **契约表本身还欠三行改动，且都在 MA/DCD 手里**（§五 第 7 件）：`ma/insights` 的载荷行没有 `conf`、没有稳定的假设 id、也没有 IR 候选（AF 的 `intent`）。AF 已按可回退口径落地（别名 + 缺报记账），但只要契约行不改，MA 侧随时可能按自家形状发而 AF 无从判定"这条到底该不该有 conf"；面板上也因此会长期是「无 IR（不能批准）」。**这不是 AF 能单方面收口的残留**，登记以免被读成"入向已经全对齐"。
- 第 5 步 ② 的「顺序追加」子句**未做**（不是漏，是不在裁定前擅自动存储格式）：af_persist 现为整文件原子重写，追加写会同时撞"不改格式头"与第五轮的有界化修法两面。校验和/坏文件跳过/原子替换三条已落，剩这一条等 DCD 定性（§五 第 9 件，判据与三条理由见 §二之十二）。
- **接缝类判据：多路镜像这一族已结清，参数递错/名单手抄那一族扫过七条且已全部有门**：本批把"同一份值手抄进多条属性路径"这一族做成门禁（`scripts/check_states_fanout.py`，§二之十四 的 AST 盘点显示它覆盖的是 src 全集，不是抽样）。另补的四处判据属**参数递错**类（serve→`build_app` 的 `readonly`、CLI 起桥递出去的四样 kwargs、`_make_runtime` 的起桥条件与 clock 归属、`live_run` 的时钟锚点），靠的是顺着验收点手动追问"这根线谁在测"。第四条不只是"补一条测试"，它**实测出一个真缺陷**：两条真机路径对同一次下发给出两套时间轴（§二之十五，`12cea64` 已修）。同族里曾剩一条「TOOLS→caps 之外有没有第二次工具名单映射」**未做清单化盘点**，也没有对应门禁能判红——这类缺口不会让任何东西变红，只会让该红的不红，与 §二之十一 同族；**本批扫掉**：AST 盘点盘到两处真映射（`af_orchestrator.observe()` 按名调注册表里没有的 `af_live`、`af_runtime_ext.mcp_tools()` 另抄一份含 `af_approve_proposal` 的五人名单），两处都是死代码 ⇒ 删除而非接线，判据做成 `scripts/check_tool_names.py`（§二之十九，`743aadf`）。**第五条已在上一批（§二之十六）扫掉并抓到两处真缺陷**（`af_service` 的 store 注入 + events 上限只装 HTTP 面，`dc8ac0d`，§二之十六）；同批扫到的第三处（单写者租约只在 HTTP 面）因涉及给 DB 新增可见拒收模式而投裁定（§五 第 10 件），代码未动。**第六条 = `af_watch` 的观察者装配，本批扫掉并抓到第三条真缺陷**（观察期把 `auto_rollback` 丢在接缝上，`af2ee56`，§二之十七）。已扫的**七条**覆盖 `af_cli` 的 serve/起桥/runtime 装配三面 + `af_service` 的 clock 与 store/cap 一面 + `af_watch` 的三个喂入点一面 + `af_mcp.TOOLS` 的名字真源一面；`af_watch` 侧本批已盘（§二之十七，抓到第三条真缺陷：观察期把 `auto_rollback` 丢在接缝上）。第七条 = **TOOLS 之外有没有第二份工具名单映射**，本批扫掉：盘到两处死映射并删除，判据进门禁（§二之十九，`743aadf`）。**参数递错那一族的判据已常驻**（§二之十八：`scripts/check_param_injection.py`，按 `store`/`readonly` **参数表**收，`gates.sh` 新节 ⇒ CI `quality-gates` 同口径判红），**名单手抄那一族也已常驻**（§二之十九：`scripts/check_tool_names.py`，注册表锚点读不到就 exit 2），**第三条族——"实现之间对同一条契约给相反结论"——同批补成静态门**（§二之二十：`scripts/check_snapshot_policy.py`，射程 = 类级 `snapshot` 且"标注 `-> Snapshot` ∪ 体里造 `Snapshot`"，锚点读不到 exit 2；此前它只有四条各认自己那几个类的契约测试），所以"这类洞没有门禁能判红"从本批起只对**静态判不出的形状**成立（`clock` 属"默认即设计"、`obj.method()` 与 `**` 解包与变量名工具属读不出值）。登记在此，避免被读成"接缝已系统扫过"。
- **"实现间契约不一致"的静态门禁本批已交付**（`scripts/check_snapshot_policy.py`，`d7d1fff`，§二之二十）：
  第七轮那条 `StateProvider.snapshot()` 必须对未知实体 `raise UnknownEntity` 的跨实现契约，此前只有
  四条契约测试各认自己那几个类，"新增一个忘了 raise 的 provider"这条失败模式静态上无人看守。
  旧条目把这件事与「`BoundedCache` 基类收敛」写在同一行，读起来像"一件活的两个名字"；本批拆开核对：
  **加基类那一半审计自己判过不做**（第六轮 §三 归入"建议（不当缺陷处理）"并给了持久化兼容风险的理由，
  第七轮 §三 重申"不为此加基类"；`git log -S"BoundedCache" --all -- src tests` 0 命中即它从未进过代码），
  但同一句里的"**新增有界缓存必须同时给 TTL 与硬上限**"要靠门禁才可见，而
  `grep -rn "有界\|TTL" gates.sh scripts/*.py` **零命中** ⇒ 目前只是审计报告正文里的约定。
  要不要把"统一基类 + 无界容器扫描"升级为硬要求，是第六轮原话里留给 DCD 的问题（跨模块重构），**AF 不自决**；
  已按此投 `关键决策部/inbox/20261003-AF-有界缓存生命周期约定要不要升级为硬门禁-决策申请.md`（§五 第 11 件，
  A/B/C 三档，AF 建议 B=注册表式门），申请里带本批实测的静态口径读数（7 候选 / "缺界" 2 条**全是误报** ⇒ 真阳性 0/2），
  登记在此，不静默。
- 证据面板的 `evicted_automations` 只做"提示有自动化被挤出内存"，未做跨进程持久化——**注意这与 ④A 不是同一个问题**：④A 裁的是 MA 洞察提案队列（已持久化），证据档的进程内清零仍是遗留。
- `SessionViewResponse` 的**应答成功分支**（本批已收口，见下方"已收口"与 §二之九）：原登记为 EXEMPT——`case04_ask_timeout` 带 seed + event 建会话后 `asks=0`（分支未走到挂起 ask），只实测到 404 失败面；顶层 9 键靠同族 `POST /sessions`/`GET /sessions/{sid}` 的同一 `_session_view` 坐实。
- `/api/metrics` 的 `runs/success/failed/audit_distribution` 仍是 0/空——`get_metrics` 的 docstring 写明这三项需**常驻进程**（`forge serve`/`watch`）才累计，原型期服务层无状态。本批只把"形状"钉成类型，没有把"数值来源"改成真累计；那是另一件事，不在类型面批里偷做。
- 本机 `node_modules` 的完整性没有门禁：本批实测 `@types/node@22.20.2` 曾被装成 **37/74** 文件（见 §二之二）。我用 registry 同版本 tarball 增量补齐了缺的 37 个文件，但**成因未查**（不在本批范围），且这条只在开发机成立——CI 走 `npm ci` 从锁文件装，不复用本机 `node_modules`。若下次又冒出"某个 `node:*` 模块找不到"，先数 `node_modules/@types/node/*.d.ts` 再怀疑代码。
- **CI 读数这条路本身没有门禁**（本批已收口，见下方"已收口"）：本批能读到 run 1–27 全红，靠的是"本机 `~/.git-credentials` 里存着可用的 github.com 凭证 + `api.github.com` 可达"（同一时刻 `github.com` 网页面 `curl` 超时、浏览器 `ERR_CONNECTION_TIMED_OUT`）。这是**一次性条件**，不是可复跑的命令；下一次两者都不可达时又变成 EXEMPT。要收口就得做成脚本（且不能把凭证写进仓）。
- **真 vhass 在 CI 上现在是 skip**：10 条 `*_in_real_vhass` 判"插件未注册 → skip（带理由）"，权威场地是 `docker run autoforge-test`；本机无 docker（`docker: command not found`），该文件也自陈"未经实跑验证" ⇒ **F14 第二道闸至今没有任何真跑读数**，"CI 绿"不许被读成"仿真复核过"。
- **CI 依赖未 pin**（`pip install -e ".[dev]"` 每次现解）：本批那条 pydantic v1/v2 红就是漂移付的账；改 lockfile/加 pin 等 §五 第 5 件裁定。
- **本机开发面仍在吃镜像**：AF 本机 `~/.npmrc` 指 `registry.npmmirror.com`，裁定 (b) 只管 CI 侧，本机不变（这是裁定选的档位，不是遗漏）。CI 侧的守卫已把"交付面必须从官方源"变成会红的判据；开发面要收口得另裁。
- 其余三仓 + AgentOps 模板是否有同款 `_*.py` 吞包标记的规则，**AF 未探测**（不擅动他仓，且探测本身要在那三个工作区跑命令）：已作为第 2 件提 DCD，建议的档位是各仓一次 `git check-ignore -v` 零改动探测（§五 第 8 件）。

已收口（上一版登记、本版实测销账）：
- ~~`ui/src/views/LiveView.vue` `events=undefined` ⇒ `_replay_live` 空转~~ → 补事件脚本输入 + 回放读数，F7 端到端真点通（`dep-28bf327fe8d7`，假 HA `turn_on`→`turn_off`，设备回 `off`）。
- ~~F4 ③ 监护视图缺失~~ → `/#/evidence` 上线，三档并列 + 诚实空态，读数与 `/api/evidence/prod` JSON 逐项一致。
- ~~`/api/asks/answer` 的失败语义未裁（通道失败还是业务失败）~~ → 裁定 ③A：`channel_error` 字段，两个方向都有断言钉住。
- ~~`ma/insights` 提案重启即清零~~ → 裁定 ④A：`af_insight_queue` 落盘队列，approve 只交接不部署。
- ~~0.3.1 wheel sha 不可复现~~ → 裁定 §〇：DCD 补账 + 重建产物 `b4b5d6bb…` 为权威，AF vendored/pin 跟新，且 wheel 内 `time.py` 与源树逐字节相同（实测见 §一）。
- ~~新 `ui` CI job 的 CI 侧执行 = EXEMPT，不是 VERIFIED~~ → 本批首次读到 Actions：**`ui-typecheck-build` 在 GitHub runner 上 success**（`npm ci` 从 137 条 npmmirror 锁文件装起来 → `vue-tsc -b --force` → `vite build` 三步全过）。同批读到的是更难看的事实：**CI 从 run 1（2026-09-29）到 run 26 一条没绿过**，三个红因（pydantic v2-only 上限写法 / 门禁自己用 3.12+ 的 `ast.TypeAlias` / vhass skip 判据问错对象）逐一修掉并各钉反例，读数见 §二之五。
- ~~AF 的 UI 没有类型门禁（登记 16 条待清）~~ → 实测 **15 条**全部清掉（其中 `SpecEditorView` 的两个按钮是**从未接线的死控件**、`LiveView` 四处 `bordered="false"` 把字符串真值当布尔传），`type-check` 加 `--force` 防缓存假绿，`ci.yml` 新增 `ui` job 作硬门；红/绿双向都有实测读数（注入探针 RC=2 → 撤除 RC=0）。见 §二之二。
- ~~API 层四处 `request<any>`（`metrics`/`experience`/`telemetry`/`sessionAnswer`）让新类型门对这四条通道失明~~ → 全部换成后端真形类型，且逐键与在线响应对账（`success_rate` 按后端语义写成可空）；`grep request<any> ui/src` = **No matches found**；类型门能因这些字段变红由探针 TS2339 实测。见 §二之三。
- ~~洞察队列只有 API、UI 没有"MA 洞察"这一屏~~ → `/#/insights` 上线：账目四计数 + 队列落点路径 + `unreadable` 记账可见 + 无 IR 行交接按钮 `disabled` + 二次确认写死"交接 ≠ 部署" + 空态四种成因。approve/reject 两条分支都在真后端上点通，把手 `a648a1aeb3c84751` 在 `af_pending` 与 decided 两侧对得上。见 §二之四。
- ~~`/api/sessions` 与 `/api/live/run` 的 `events` 无上限~~ → `_MAX_REPLAY_EVENTS` + `_check_event_cap()` 覆盖三条 events 端点（`grep -c` = 3）；参数化测试在两处摘掉判据后**实测变红**（`assert 400 == 422` / `assert 403 == 422`，即 10001 条 events 修复前一路走进业务层），装回 11 passed、全量 2623 passed RC=0。见 §二之六。
- ~~CI 读数靠"当场手搓 curl + `~/.git-credentials`"这条一次性条件~~ → `scripts/gh_ci_status.py`（纯标准库、只读 GET、不打印凭证）：`runs`/`jobs <run_id>`/`annotate <run_id>`/`log <job_id> <关键词>` 四个入口。`log` 那一步多一个安全点——日志端点是 **302 到签名 URL**，`urllib` 默认会在跳转时继续带自定义头，脚本因此先停在 302 取 `Location`、再**无凭证**取正文，避免把 token 发给日志存储域。首次 runner 侧读数（run 31 / job `111072195342`）：`fetch 主机读数：npmmirror=0 npmjs=87`，五个作业全 `completed/success`。见 §二之八。
- ~~§二之七 登记的"本机预跑是 npm 11.9.0，runner 是 npm 10.x，改写是否在 CI 上真生效只有 push 之后才知道"~~ → 已由上一条取到实测：**守卫在 runner 上给的是读数而不是红**（`npmmirror=0 npmjs=87`），交付面确认走官方源。
- ~~`SessionViewResponse` 的应答成功分支 = EXEMPT（UI 那九个顶层键里，"挂起 ask → 应答成功"那条分支从没经过 HTTP 面）~~ → 两条契约判据补上：`POST /api/sessions` 打到挂起态后九键与 `AskItem` 八键逐字对死、`control` 整个 dict 对死、实例 `current_node=q1`；`POST /api/sessions/{sid}/answer` 成功分支返回**同一份九键视图**、`asks` 排空、`vars.decided=yes` 证明 then 边真跑、`GET` 读回一致。两处变异分别取到 `1 failed` / `2 failed`（RED_RC=1），撤除后 14 passed、全量 **2635 passed / 51 skipped RC=0**、门禁 RC=0。**同批把自己写的用例照了一遍**：单跑绿、全量红（`_SESSIONS` 全局共享，留挂起 ask 的用法带红了 `test_af_api.py::test_api_asks_aggregate_endpoint_registered`），改用 `DELETE /api/sessions/{sid}` 收尾。见 §二之九。
- ~~两条架构门禁（`check_imports.py` baseline-lock 与 `lint-imports` 契约）在 CI 上分析 86 个模块、本机 96 个，"CI 绿"覆盖的不是本机那份代码~~ → 根因查明是 `.gitignore:83` 的 `_*.py` 连带吞掉 `src/autoforge/af_closedloop/__init__.py`（`git check-ignore -v` 直接指到行），grimp 对无包标记的目录不递归。三件收口：负向规则 `!**/__init__.py`、包标记首次入库（并消掉它与 `runtime.py` 的逐字节重复——两份 `GuardViolation` 会让 `except` 漏抓）、复发门 `scripts/check_pkg_markers.py` 进 `gates.sh`。**判据取 git 索引不是磁盘**这条是关键：磁盘 walk 只能在本机响，本机恰恰是看不出问题的那一面。能变红两条实测（假包 RC=1 / `git rm --cached` 还原原状 RC=1 且指名 `af_closedloop`，装回 RC=0）。本机全链 RC=0：包标记门、`check_imports.py` 96 modules、`lint-imports` 96 files KEPT、`gates.sh`、`pytest` 2635 passed。**runner 侧 96 已复测取到**（run 36 / job `111090382332`：`Baseline lock: 96 modules, 0 violations`、`Contracts: 1 kept, 0 broken`，`quality-gates` 侧包标记门在 runner 的 git 索引里数到 96 个 `.py`）⇒ "两边同口径"由待取转为实测，读数见 §二之十一 末段。

---

—— AutoForge 开发 · 2026-10-03
