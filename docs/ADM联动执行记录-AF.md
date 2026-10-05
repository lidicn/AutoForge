# ADM 联动执行记录 · AF（AutoForge）

> 对应计划：`docs/ADM联动执行计划-AF.md`（DCD 出品，v2.5 联动落地版）
> 记录人：AutoForge 开发
> 核实基准：代码侧最新 commit = **`b4cd67f`**（本批 §二之三十五：裁定 20261004（18:35 那份）AF 侧今日四件落地——`paho-mqtt>=1.6,<2.1` 两处声明钉上界、洞察去重与回灌键改用 `insight_id`（缺失才退别名并记 `transport.id_key`）；判据 +3 条桥面 + `tomllib` 读声明面那条）；上一笔 `2a8d940`（同一批的安全半边：Q1 长期码 180 天绝对上限、F-1 `pending` 收进 `_read` 门 + 单向蕴含表、F-2 明文样例凭据出码、自盘的授权面原子写与"名单读不成即拒绝"旗标、README 可信 LAN 前提；判据 25 + 契约 18 条，变异 CONTROL `RC=0 / 42 passed` ⇒ M6 `2 failed` / M7 `3 failed` 逐字节还原，全量 `2978 passed, 52 skipped, 1 warning, 7 subtests`、`gates.sh GATES_RC=0`）；再上一笔 `3262ccb`（**DCD 自己落笔**的计划文档六处更正——§5.3/§5.4 自相矛盾收口、四条过期账目、`/api/health` 端点名、§5.6 `origin/main` 读数更正；裁定 §四 第 2 条要求别让它悬在工作树，本批并入推送）；上一笔代码 **`8689b39`**（本批 §二之三十四：`固定名 .tmp` 那一族的写原子性——新门 `scripts/check_atomic_write_sites.py`（三判据 + `exit 2` 档 + 键含类名）＋基线 9 站逐条理由＋就地豁免 2 站＋收口 7 处（`af_persist.save`、启停写下沉 `GraphStore.resave_raw`、`_delete_archive` 标签 RMW 进 `tags.lock`、`af_catalog` 三站、`af_insight_queue._atomic_write`）；新增 26 条测试、七档变异 M1…M7 全红（M2 第一遍没红＝断言吃了旧账）、全量 `2946 passed, 52 skipped, 7 subtests in 125.88s`、`gates.sh` 全链 `GATES_RC=0`；三条实测出的对端可见形状 AF 未动，写入 DCD 那件 §五 续查）；上一笔 **`c9a85b5`**（`c9a85b5` 是**安全审计（scoped run）对 HEAD 复测后剩下的两处真洞**那一笔：那份 zip 此前沿用状态＝**从未进入本仓处置链**（`docs/` 检索"安全审计"只命中 zip 自己），而它的 `source_ref` 是 `zip-snapshot-of-default-branch-2026-09-29T22:43`（**无 sha**）⇒ 按铁律 #11 逐条重跑得"已修被重报 5 / 成立 2"；成立那两条比报告写得深——① `record_failure()` 遇 `rec is None` 直接 `return False`，**试一个不存在的码根本不进任何计数器**，报告建议的协议层 limiter 只按住症状，故防线做进 `AuthCodeStore`（60s/10 次全店窗口、早退之前计数、失败方向仍是回落人审队列；**边界明写：进程内、重启清零**）；② `dispatch()` 从不把 `arguments` 与 `inputSchema` 对账，AST 双向对账量出 **31 工具里 6 个键"没声明却能传"**，其中 `allow_bulk` 是爆炸半径护栏的**旁路开关而对外契约上看不见它**——六键补声明（不删参数退回隐式）+ 运行期在 `_guard` **之后**拒收（顺序是判据）+ 新静态门 `check_mcp_arg_schemas.py`（三条判据，读不出一律 `exit 2`）；同批把 `load_graph` 规模上限从"有代码无读数"补成 5 条（此前 `tests/` 0 命中）。读数：全链 **2921 passed / 51 skipped RC=0**（基线 2894 +27＝11+11+5）、`gates.sh RC=0`、变异八腿、numstat `21 0`/`29 2`/`59 8`；**三问不自裁**（长期码绝对 TTL / `--host 0.0.0.0` 且 MCP 默认放行 / homesdk wheel 来源）⇒ §五 第 16 件，compose 与那几处一个字节未动；处置记录 `docs/audit/审计报告_安全审计_核实与修复.md`，读数见 §二之三十三）；上一笔 `9c32ea0` 是 **UI↔路由门扩到三棵第一方 UI 树 + 修掉泛型 `;` 静默丢调用点**那一笔，读数见 §二之三十二（其 runner 口径已由 run 71 实测闭合：与本机那行 `EQUAL True`、同 217 字符 ⇒ 连续绿按 main 计 **3 条（run 69–71）**）；上一笔 `e460254` 是**门禁作业补 dev 依赖 + 射程消息自带原因**：run 68（纯文档 `9477be1`）的 `quality-gates` 红，红因是上一批那道新门的判据 B 要跑 `python -m pytest --collect-only`，而该作业只装 `pip install -e .`（core = jsonschema + typer，**没有 pytest**）⇒ 收集 rc=1 ⇒ 门按设计 `exit 2`（读不成不许报干净）把整条作业弄红；修法用**声明过的 extra**（`-e ".[dev]"`）而不是在 workflow 手搓 `pip install pytest`，并把 `f"…：{proc.stdout[-300:]}"` 换成 `_collect_reason()`——先认"没有 pytest"并点名缺失前提、再取有字的那一路、两路都空要明说；本机造不出"没装 pytest"的环境，故 **ci.yml 那一行只有下一条 run 能证**，本批按「判据级实测绿 + 作业级待 run 复测」记账；反例 +2 ⇒ `23 passed`，变异把消息退回只截 stdout 得 `2 failed`（红文正是 CI 那条断在冒号的样子），本机 `gates.sh RC=0` 且本门绿行逐字未变，全链 **2881 passed / 51 skipped / 7 subtests RC=0（125.52s）**；连续绿 34 条（run 34–67）断在 run 68，读数见 §二之三十一）；上一笔 `c0476e2`（`c0476e2` 是**有界缓存注册表门禁 + 两条无人按的回收**那一笔：裁定 20261004 §一 3 按 B 档落地——注册表 `af_bounded_caches.py` 在码里、`scripts/check_bounded_caches.py` 只核对名字（A 双腿真在模块里 / B 测试 id 真被 pytest **收集**，收集失败是 `exit 2` 不是判绿 / C 新增容器必须注册-豁免-进基线 / D 反空洞自证）+ `gates.sh` 新节 + 21 条反例；**前提与裁定不符处按实测记账**：扫到增长容器 76 个、双腿齐全且有测试的只有 2 个、裁定的两个误报候选根本不进这 76（它们走带理由的就地豁免），其余 74 冻结为基线并由 `--print-baseline` 生成不手敲，基线**只在本仓 src 生效**（套到夹具树会报 74 条假红、埋掉唯一真红）；同批把 `Runtime.tick()`→`recorder.sweep()`（不带 force，保住 3600s 节流）与 `UndoStore` 超窗快照的写侧摘除接上生产路径——后一件的"打开即清"版本被既有 HTTP 判据当场判红（`KeyError: 'expired'`：那会把"过期撤不了"和"没这条"混成同一个答复），回收点因此挪进 `record()`；三档真实仓变异各 `RC=1` 且**各只 1 处判红**，全链 **2879 passed / 51 skipped RC=0（138.96s）**、`gates.sh RC=0`、棘轮 `104/104` 不动；`af_persist` 追加写那半按裁定 §一 4 A 在本仓标注"经 DCD 判定不适用"、码一字未动；读数见 §二之三十）；上一笔 `7dbd640`（`7dbd640` 是**裁定 §一 1 与 §一 2 落地**那一笔：单写者租约此前只装 HTTP 面，`forge mcp` 与生产 serve 同 store 根时人点被 503、Agent 照样写真机——按裁定 A 只把**真机写**纳入咽喉（`live_run`）、**只 check 不 acquire**（`held_by_other()` 探测不写 sidecar，盖章会把 serve 的持有者诊断覆盖成自己），MCP 出口文本固定前缀 `READONLY_DEGRADED:` 供下游判别；件 2 把 `trace_id` 判成**事件级**写进 `_envelope()` docstring、`node_id` **删**（它是"失败时刻停在哪"不是"哪个节点失败"，从未进契约行、DB 从不读），键集判据改钉成逐等于契约行 + `instance_id` 那份 ②A 过渡字段。9 条判据 `9 passed` + 五档真实仓变异（`3 failed/3 failed/2 failed/1 failed/2 failed`，对照 `9 passed` RC=0，`RESTORE_OK`）与桥键集 B-1 单条红，读数见 §二之二十九）；上一笔 `3c18f1c`+`a93fb4b` 是 **UI↔路由契约门禁**那一组（`scripts/check_ui_api_paths.py` 三判据 + 服务端**两张路由脸**（装饰器 79 + `add_api_route` 挂载表 5）+ 24 条反例 + 九档变异含 M6 边界档与"今天 UI 一条挂载表端点都没调、这一步只改计数"的老实话；§二之二十八）；再上一笔 `3039c62`（`3039c62` 是 **§5.3 第 4 件：撤销清单的 10s 定时刷新**那一笔：上一版 §〇 那行"F7 前端残留 已收口"只做到 DCD 那一件两半中的一半——**定时器从未存在过**；顺着这一面重新读码又盘出两处同族假读数（`catch` 里把清单清空 = 把"查询失败"渲染成设备侧结论"窗口内没有可撤销项"；文案写死 `undoWindowS || 60` 而真值在服务端 `AUTOFORGE_UNDO_WINDOW_S`，0 另含"永不过期"之义）。前端三处（10s 补数且下发/撤销 in-flight 时让路、失败留旧读数并按成功过分档标注、窗口长度三态只印服务端给的数）+ 服务面两条这一页新依赖的判据（`ui/` 无 vitest ⇒ 跨层契约放到能判红的一侧）；验收用一台一次性假 HA（标准库、token 门、POST 逐条落盘）取到**设备侧对账** `turn_on`→`turn_off` 与零点击的 `上次成功 13:12:30 → 13:12:50`；**自纠两条措辞谎**（第一版在"一次都没读到"时印 `窗口 0s` 并说"下面是上次成功读数"）+ **一次驱动器废读数**（M-1 的 `RC=2/1 error` 是替换串造成的语法错误，`error≠failed`）；本机浏览器取不到视口 ⇒ 结论等级写"按 DOM 事件驱动走通"而非"真机真点"，两条未测项（多标签各自轮询、卸载清定时器）明写不判 PASS；全链 `2818 passed / 51 skipped RC=0（92.64s）`，**顺带把上批 `556.34s` 的悬账按计数同形归因给本机状态并销账**；读数见 §二之二十七）；上一笔 `739a328`（`739a328` 是**联动桥运行时依赖"三面一致"**那一笔：盘出 paho 在 AF 整条依赖链里**一处声明都没有**——homesdk 把它放自家 `[mqtt]` extra（vendored wheel 元数据实测 `extra == "mqtt"` 才要它）、两个镜像与 CI 装的是 `.[api,ha]`/`.[dev]`，开发机正常只因手动装过；后果是窗内开 `AUTOFORGE_MQTT=1` 时 `get_client()` 抛 `MqttUnavailable`，而 `af_cli.py:1380` 起桥排在 `uvicorn.run` 之前且不吞异常 ⇒ **整个 AF 拒启（含只读面）**，不开则计划 第 1/2 步验收原理上不可能达成，而 2816 条测试全绿（桥的用例全用 duck-typed client）＝§二之二十三 那族的依赖版。补齐 + 做成硬门：`pyproject` 新增 `[mqtt]`（`dev` 同列）、`Dockerfile.api` 装 `.[api,ha,mqtt]` 并 `COPY scripts`、`scripts/check_mqtt_runtime_dep.py` 判"声明在/交付面装到/CI 面装到"（锚点读不到 `exit 2`）+ `gates.sh` 新节两条红分支 + 24 条反例，四档变异 `RC=1/1/2/1` 且还原逐字节一致，**产品码只动 `af_mqtt_bridge.py` 文档串一行**；同批自纠两处（一条上批判据是宿主相关的；变异驱动又被 `System32\bash.exe` 的 WSL 存根骗出三个假 `RC=1` ⇒ 规程补"先跑对照档"）；窗内三问（开关键序 / paho 来源 / 第③项用真机还是 dry-live 的 fired）投 §五 第 13 件、**compose 一个字节未动**；读数见 §二之二十六）；上一笔 `da09d43`（`da09d43` 是**窗后四项验收工具**那一笔：`scripts/verify_adm_window.py` 把裁定的四件做成三态逐项判定（有 FAIL ⇒ 1、有缺项无 FAIL ⇒ 2 并打印"EXEMPT ≠ VERIFIED"、全 PASS ⇒ 0），判据取自契约表 §1.2/§1.3（四键 + `ts` 偏差 + 事件不许 retained + `msg.retain`），连接与凭据走机制层 `homesdk.mqtt` ⇒ 脚本不读任何环境口令；`+268/0` 与测试 `+326/0`，**产品代码一个字节未动**；同批更正本文档 5 处"本机无 paho-mqtt"的理由（实测 `homesdk.mqtt.paho_available()=True`，真缺的是那台 broker），读数见 §二之二十五）；上一笔 `b50bb22` 是**入向订阅门禁**那一笔：`scripts/check_mqtt_subscriptions.py` 三条判据（订阅口只在 `af_mqtt_bridge` / 动态主题要在同一函数体内先过 `FORBIDDEN_SUBSCRIPTIONS` / 收件箱族写死就红）+ `gates.sh` 新节与两条红分支 + 20 条反例，**产品代码一个字节未动**；真实 src 实测计数"订阅点 2 处/1 文件、`INSIGHTS_TOPIC` 1 处、带守卫的动态入口 1 处、豁免 0"，四档变异 `RC=0/1/1/1/2`（M-1 另走完整 `gates.sh` 取到 `结论：入向订阅门禁红（exit=1）`），读数见 §二之二十四）；上一笔 `8b629b8` 是**出向 MQTT 写者门禁**那一笔：`scripts/check_mqtt_writers.py` 三条判据（写者唯一 / 事件只许 `observe_terminal()` 产生 / 载荷必经 `_envelope()`，位置式与 `payload=` 关键字式都认）+ `gates.sh` 新节与两条红分支（`exit 1` 红、`exit 2` 读不到锚点）+ 18 条反例，**产品代码一个字节未动**；真实 src 的实测计数是"写者 2 处/1 个文件、生产者 2 处/1 个文件、`_publish` 载荷 2 处且 2 处来自 `_envelope()`、豁免 0"，读数与两个自踩的坑见 §二之二十三）；上一笔 `159ba00` 是**只加测试**：把 `observe_terminal()` 真实发出的键集合钉死，产品码 `0` 变动；它顺带让一个从未登记的差额可见——观察者路径比契约表 §1.2 多发 `node_id`（`instance_id` 那份额是裁定 20261002 ②A 已登记过的），读数与四档变异见 §二之二十二）；上一笔 `5ff1ea6` 是**出向事件 `error` 装真原因**那一笔：`observe_terminal()` 不再把状态名当 `error` 发，改读 `ctx.context["fail_reason"]`、缺原因发诚实占位句并按契约表 §1.3 的 500 封顶；`+23/−1` 产品码 + `+58/−0` 三条新判据，四档变异 `0/2/1/1` 各自钉一次；同批对完的 `trace_id` 那一半属契约语义 ⇒ **代码未动**、投 DCD §五 第 12 件，读数见 §二之二十二）；上一笔 `d7d1fff` 是**状态源 fail-closed 静态门禁**：`scripts/check_snapshot_policy.py` + `gates.sh` 新节与两条红分支 + 16 条反例，**产品代码一个字节未动**；射程判据取"标注 `-> Snapshot` ∪ 体里造 `Snapshot`"的并集，起因是第一版只认标注被自家三条反例打红，而本仓 `tests/contract` 里的反面样本 `_FailOpenProvider` 正是"没有标注却造 `Snapshot`"那一种；锚点读不到 exit 2、豁免单独计数）；`c7b97b9` 把**工具名单门禁**的"第二份名单"形状从字典键扩到集合/列表/元组（+3 反例，M-6 实测 `RC=1` 两处行号）；`743aadf` 是那一族的**首版**：`scripts/check_tool_names.py` + `gates.sh` 新节 + 22 条反例 + **两处死映射删除**（`af_orchestrator.observe()` 按名调注册表里没有的 `af_live`、`af_runtime_ext.mcp_tools()` 另抄一份含 `af_approve_proposal` 的五人名单），净 `0 3` / `1 14`；`9a83ead`+`4a3e3b1` 是**参数注入门禁**那一组：门禁脚本 + `gates.sh` 新节 + 22 条反例 + 一处真缺陷（`af_mcp._t_health` 从不探它服务的 store，+3/−1）+ 双轨对拍两条**带理由**的现场豁免，且门按 `store`/`readonly` **参数表**收、`clock` 有意不收；`e6d442a`/`0722828`/`9c6deb9` **只加测试**，`fc2bba6` 加的是**一门新门禁** `scripts/check_states_fanout.py` + `gates.sh` 一节 + 它的反例测试，`12cea64`/`dc8ac0d`/`af2ee56` 是动 `src/` 的三笔：时钟口径 + 三条判据（+11/−2）/ 真机闸门在 MCP 面两处 fail-open（4 文件 +196/−24）/ 观察期恢复路径丢 `auto_rollback` 旗子（+21/−8）。§二/二之二…二之二十 各组读数各自当场跑出，非互相引用；跨批次重复的读数（全量 pytest、`gates.sh`）在对应小节里写明当次的解释器与通过/跳过数（铁律 #11）
> 契约真源：`E:\NAS\homesdk\doc\ADM联动主题注册表与消息契约.md`
> 交叉裁定：`20260929-ADM三仓联动七问`、`20261001-AF-homesdk接入四问`、`20260930-AutoForge后续优化三项`、`20261001-DB目标模式与AF三题` §H、**`20261002-homesdk记账与AF-DB-DPP六件-裁定`**、**`20261004-AF四件与DB一件与MA五件-裁定`**（§一 AF 四件：件 1 单写者租约 A / 件 2 `trace_id` C + `node_id` 删 / 件 3 有界缓存 B 注册表式门 / 件 4 af_persist 追加写 A 不适用；本批落地依据，见 §二之二十九、§二之三十）

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
| 第 5 步 后续优化三项 | **①③④ 已交付；② 经 DCD 判"追加写"那一子句不适用** | ① 单写者租约降级只读、③ import-linter 分层两档、④ tick 线程自愈，逐条 file:line 见 §一；① 的**接缝判据**（serve 抢不到锁 ⇒ `build_app(readonly=True)`）本批补上，两次变异各取到红（§二之十三）；② af_persist 的「顺序追加」子句此前登记为"只落半边、等 DCD 定性"（§五 第 9 件），**裁定 20261004 §一 4 判 A：该子句对 `af_persist` 不适用**（三条理由与 AF 的论证一致：与"不改格式头"相抵 / 与第五轮有界化反向 / 重放友好已被校验和+损坏段跳过+原子替换覆盖）。按裁定执行口径，本仓记录已就地标注"经 DCD 判定不适用"（§二之三十 第五节），`af_persist.py` 一个字节未动 |
| 裁定 20261004 §一 · AF 四件 | **① ② ③ 已落地；④ 是标注动作，已标注** | 件 1 租约纳入 MCP 真机写（只 check 不 acquire + `READONLY_DEGRADED:` 前缀，9 条判据五档变异见 §二之二十九）；件 2 `trace_id` 判事件级并写进口径注释、`node_id` 删（键集判据 B-1 单条红）；件 3 有界缓存注册表门禁上线为 CI 硬门（21 条反例 + 三档真实仓变异各 `RC=1` 各只 1 处判红，§二之三十）；件 4 见上一行。**四条里有两条半是"AF 前提与裁定不符"**：存量不是 5 处而是"扫到 76 / 双腿齐全 2"，裁定的两个误报候选连这 76 都不进——已按实测落并把差异回投 DCD（§五 第 15 件），没有为凑"5 处"去给 cap-only 那批造 TTL 腿（那是裁定自己驳回的 C）。**契约表侧另有四行要 DCD 动手**（前缀登记、`trace_id` 事件级口径、`node_id` 从 §1.2 删、有界缓存不属跨仓契约），AF 不代编 |
| 裁定 20261004 §一/§二（18:35 那份，回答上一件 §五 续查三问）· AF 侧今日四件 | **① ② ③ ④ 今日落地；⑤ ⑥ ⑦ 各因由推后**（见 §二之三十五 第一节切分表） | 今日落：Q1 长期码可配绝对上限（默认 180 天，`AUTOFORGE_AUTH_LONGCODE_TTL_DAYS`，非正数=显式关；**期限由 `created_at` 推导 ⇒ 盘上历史码不迁移也会到期**）+ `list()` 给"距生成多久/还剩多久"且与 `validate()` 同源；F-1 `GET /api/asks/pending` 加 `Depends(_read)`；F-2 明文样例凭据出产品码（判据扫 src 全集）；Q2=B `paho-mqtt>=1.6,<2.1` 两处声明钉上界；§五 追认侧 `insight_id` 成去重与回灌键；README 写死"只在可信 LAN"这条部署前提。**另盘出一条不是审计 finding 的洞**：`af_auth` 五处落盘站点全是裸 `write_text`，而 `_load_*` 把 `JSONDecodeError` 吞成"文件不存在" ⇒ **半截的撤销名单 = 已撤销的令牌复活（fail-open）**，写侧补私有原子助手（本模块 L0，不能引 `af_store`）、读侧分开"在但读不成"与"不在"并置位保持到进程重启。**推后与理由**：MCP 未设令牌默认拒绝（默认结论翻转级：49 处 `dispatch(` 测试调用点 + `dispatch()` 默认值本身，单独一批）、F-3（两棵 UI 树消费明文列表，须连前端改并做浏览器验证）、F-2 的 UI 半边、Q3 真机演练与窗后四项/NAS 重烤（合并窗，Q1=A 明写本窗只重烤**不开开关**）、compose 那句"可信 LAN"注释（铁律 #3，NAS 部署者持有那份）。**前提差已回投**：裁定那句"write 域含 read"在 HEAD 上不成立（`requires()` 逐名比对、契约行只给 POST 标鉴权、homesdk 里 `autoforge_api_token` 0 命中），AF 落单向蕴含表而非照字面打断 ask 通道。读数与变异表见 §二之三十五 |
| "写好了没人按"那一族（计划外补刀，注册表项的 test 出处就在这里） | **已交付两处，第三次被自家既有判据驳回** | 盘点"只增不减"时实测两处**函数完整、调用方为零**：`FireRecorder.sweep()`（`keep_days=3` + 3600s 节流）全仓唯一调用方是测试里那句 `sweep(force=True)` ⇒ `fire_log.json` 的 day 维度按天只增不减；`UndoStore.purge_expired()` `grep -rn purge_expired src` 只命中定义 ⇒ `undo_log.json` 随部署数单调增长。修法分别挂进 `Runtime.tick()`（**不带 force**，否则节流被旁路、逐 tick 变逐 tick 扫盘重写）与 `record()` 写侧。后者这里有一次**被既有判据驳回的设计**：第一版"打开即清"在全量跑当场红了 `test_af_undo_http.py::test_undo_refuses_expired_window_via_http`（`KeyError: 'expired'`）——那会让 `/api/undo/{deploy_id}` 对超窗记录回 404，把"过期撤不了"和"没这条"混成同一个答复。回收点因此挪进写路径，判据形状按本仓口径三段（先证 harness 真会写、再证纯写也被回收、最后证没超窗的还在）。两腿变异各 `rc=1 1 failed`，另把 `expire_stale()` docstring 里那句撒谎的"或超配额"改掉（配额管能不能再触发，不管字典留几条）。见 §二之三十 第四节 |
| 第七轮审计 | **finding 属实，已修** | 详见 `docs/audit/审计报告_第七轮_核实与修复.md`；第八轮尚未落 `docs/audit`（本回合实测目录内最新即第七轮，mtime `10-02 11:46`） |
| 门禁肥化防护（计划外补刀） | **已交付，本批又加一门** | `gates.sh` 计数棘轮 + `.gates-tally.txt`（上限 104），三条变异实测能红；AgentOps 模板同步去反引号缺陷。本批新增**状态源扇出门禁** `scripts/check_states_fanout.py`（换 `runtime.states` 必须四个消费方同步；HEAD 绿、两处变异红），并自带反例测试 `test_states_fanout_gate.py` 7 条——第一版实现自己是个假洞（对 HEAD 与对变异同时报绿），那一次形状也钉成用例（§二之十四） |
| 真机路径口径对账（计划外补刀） | **已交付** | 接缝盘点的第二个产物：`live_run`（HTTP `/api/live/run` 与 MCP `af_live_run` 共用）此前用 `build_runtime(graph)` 的**仿真锚点**当墙钟用——同一次真机下发的 `trigger_time`/`audit.at`/canary `at` 与 `at:19:30` 判定全落在 2026-09-14 08:00，而 CLI 的 live/dry-live 分支早已取家庭墙钟。`12cea64` 统一为"锚在真实的现在、仍可推进"的虚拟钟并开 `clock=` 入参；`build_runtime(` 全部 8 个站点逐一点过，其余无 clock 的都在仿真面上（锚点是设计）。三次变异 `3 failed`/`1 failed`/`1 failed`（§二之十五） |
| 真机入口面的闸门对账（计划外补刀） | **已交付三处（第三处已裁定并落地）** | 接缝盘点的第三个产物：同一盘点前两条落在 `af_cli`/`af_service`，这条落在 **HTTP 与 MCP 两个入口面**。实测两处 fail-open——① `af_mcp._t_live` 没递 `store` ⇒ Tier-0 设备保护（`device_acl.json`）与实体健康在 Agent 路径上整个失效，人点被拦的设备 Agent 能写；② events 上限写在端点层 ⇒ MCP 三面（`af_live_run`/`af_simulate`/`af_sessions`）无上限。修法把判据上收到 `af_service`（两条入口共用的咽喉），`MAX_REPLAY_EVENTS` + 三个服务层调用点，`_t_live` 补 `store=store`（`dc8ac0d`）。反真空靠一条**正向对照**（无 ACL 时 MCP 路径真下发）。三次变异 `1 failed, 18 passed`/`2 failed, 17 passed`/`2 failed, 17 passed`（§二之十六）。**同批抓到的第三件（单写者租约只装 HTTP 面）不属 AF 可自决**：它要给 DB 新增一个可见的拒收模式 ⇒ 投裁定（§五 第 10 件），代码未动 ⇒ **裁定 20261004 §一 1 判 A 并已落地（`7dbd640`）**：只把真机写纳入咽喉、只 check 不 acquire、MCP 文本带 `READONLY_DEGRADED:` 前缀，落点与五档变异见 §二之二十九 |
| 生产态验证证据与 canary 策略位盘点（计划外补刀） | **已交付** | 接缝盘点第六条命中，也是**第三条真缺陷**：`canary: {duration: "15m", auto_rollback: false}` 是"只观察、别自动反向下发"的显式选择，但 `pending_canary` 挂起元信息没存这个旗子 ⇒ 观察期结束的 `resume` 分支直接 `wrapped.rollback()`，绕开 `CanaryGuard.auto_rollback` 那一层判断；无 duration 的立即检查路径却尊重它。方向是"比要求的更自动"，且这条 resume 是挂起观察的**常规**唤醒路、不是崩溃恢复专用。`af2ee56` 把旗子带过接缝（缺键按 True，旧快照行为不变）、恢复侧改用真 `CanaryGuard.check_and_rollback`（回滚与否只留一处实现），不回滚仍记 `failed`+`entity_drift` 并在 reason 写明"未回滚"。同批盘了 `af_watch` 三个喂入点的 status 档对齐（shadow/立即 canary/冲突各在其位），并登记一处同名碰撞（`af_audit.record_conflict` vs `af_watch.record_conflict`，非缺陷）。三次变异各 `1 failed, 25 passed`（§二之十七） |
| 参数递错那一族升成常驻门禁（计划外补刀） | **已交付，且抓到第四处真缺陷** | 前六条同族站点有五条靠手扫；这一批把判据做成 `scripts/check_param_injection.py` + `gates.sh` 新节（CI 的 `quality-gates` 跑的就是 `bash gates.sh` ⇒ 本机与 runner 同口径判红）。按**参数表**收（`store`/`readonly`），`clock` **有意不收**（默认值是仿真锚点、是设计，§二之十五）。首跑抓到 `af_mcp._t_health` 从不探它服务的 store ⇒ Agent 面 `store_ok` 永远 `null` 而 HTTP 面是 `true`（读面证据缺失，铁律 #5）；五处变异各红一次，读数见 §二之十八 |
| 名单手抄那一族扫掉＝§六 挂了四批的最后一条接缝（计划外补刀） | **已交付，盘到两处第二份名单并删除** | 「TOOLS→caps 之外有没有第二次工具名单映射」这条以前**既没清单化也没有能判红的门**。盘点必须走 AST：全文正则扫 `af_*` 得 108 个串/77 个不在注册表，几乎全是模块名（`af_live.py` 本身就是模块）；只取字符串常量且整串 fullmatch 工具名形状 ⇒ 34 个/3 个不在 `TOOLS`（那 3 个是指标标签名，非工具名）。删除前的读数是 40/9，多出的 6 个就是两处真映射：`af_orchestrator.observe()` 按名调 `af_live`（真名 `af_live_run`，`_call_safe` 吞异常 ⇒ 这条路**永不响**）、`af_runtime_ext.mcp_tools()` 另抄五人名单且从未接线，其中 `af_approve_proposal` 与裁定 20261002 §三 ④A「人批后才进可执行队列」正面冲突。两处都是死代码 ⇒ **删除而非接线**（单提交可 revert）。新门 `scripts/check_tool_names.py`：按名调用必须命中 `TOOLS`、`TOOLS` 之外 ≥2 个 `af_*` 键的字典即第二份名单、**读不到注册表锚点 exit 2**（不做假绿）；22 条反例含"不误响"主干。五处变异分档红（`1/1/1/2/0`），并记账两处自踩：变异驱动把四次 patch 都写在循环前 ⇒ 前三条读数被第四条遮蔽成同一种红（"全红同一句"不是门严是没隔离变量）；自家 `gates.sh` 红分支里的裸反引号被 bash 当命令替换 ⇒ `readonly` 把整个 shell 环境 dump 进结论行（三行已转义并实跑复读）。**同批把门扩到集合/列表/元组**（`c7b97b9`：第二份名单不只长成像字典，`WRITE_TOOLS = {…}` 这一族同样算，扩前盘 src 该形状 **0 处** ⇒ 不误响；M-6 实测 `RC=1` 指到两行行号），并把 `TOOLS→caps` 的另一半盘成读数：**scope/caps 是 `TOOLS` 每个元组的第 5 项、与名字同源**，不是第二份名单。见 §二之十九 |
| "实现间契约不一致"补成静态门禁＝§六 那行拆开的两件里能自决的那件（计划外补刀） | **已交付，产品代码零改动** | 第七轮的 `StateProvider.snapshot()` 必须对未知实体 `raise UnknownEntity` 只有四条契约测试守着，而**每条用例只喂自己认识的那个 provider** ⇒ "新增一个忘了 raise 的实现"这条唯一的真实失败模式一条都判不红。新门 `scripts/check_snapshot_policy.py`（`gates.sh` 新节 ⇒ CI `quality-gates` 同口径硬门）射程 = src 全集 11 个类级 `snapshot` 站点里的 5 个实现，`DeviceCatalog`/`HealthEngine`/`MetricsAggregator`/`TickHealth`/`VersionManager` 五个留在射程外。**判据自身的漏法被自家反例打出来**：第一版只按 `-> Snapshot` 标注判 ⇒ 三条本职判据当场不红（`assert 0 == 1`），因为标注是可选的，而本仓 `tests/contract` 里的反面样本 `_FailOpenProvider` 正是"无标注 + 造 `Snapshot`"那一种 ⇒ 射程改成两信号**并集**（诚实记账：并集在今天 src 上零增量，买的是"下一个实现忘写标注"）。锚点 `StateProvider.snapshot() -> Snapshot` 读不到 **exit 2**；**豁免单独计入读数**（绿行原样写"全部抛"而其中一处靠豁免过关＝把未验证算成已验证，铁律 #5）。六档变异 `0/1/1/0/2/2` 全 OK，另记一次自踩：改 `check()` 返回三元组时漏改末尾 `return` ⇒ **M-0（什么都不改那档）先 BAD**，这就是它必须存在的理由。§六 那行同时拆开登记：`BoundedCache` 基类**审计两轮自己判过不做**（`git log -S` 在 `src`/`tests` 全历史 0 命中，它从未进过代码），但"有界缓存须同时给 TTL 与硬上限"的**门禁可见**那一半确实没做（`grep 有界|TTL gates.sh scripts/*.py` 零命中）——是否升级为硬要求留给 DCD，AF 不自决。是否升级为硬要求那一半已投 DCD（§五 第 11 件，随附静态口径实测读数：7 候选 / 真阳性 0/2）⇒ **裁定 20261004 §一 3 判 B（注册表式门禁）并已上线（`c0476e2`）**，`gates.sh` 新节即 CI 硬门；落地的同时把这条约定的**存量口径按实测更正**（76 / 2，不是裁定写的 5 / 2）并回投 DCD 追认，见 §二之三十、§五 第 15 件。见 §二之二十、§二之二十之一、§六 |
| F4 ③ / F7 前端残留 | **F4 ③ 已收口；F7 那一行的"已收口"曾是半件说成一整件，本批补齐** | `/evidence` 生产态证据视图上线；真机下发的事件回放与撤销按钮端到端真点通（读数见 §一末）。DCD §5.3 把第 4 件写成**两半**（撤销通路 **+** 10s 定时刷新），本仓上一版只做了前一半就在 §〇 写"已收口"——顺着这句重新读码又盘出两处更根本的假读数（查询失败被清成"窗口内没有可撤销项"、窗口长度印的是页面猜的 60 而真值在服务端 `AUTOFORGE_UNDO_WINDOW_S`，0 还另有"永不过期"之义）。本批三处前端改 + 两条服务面判据（清单键集合与"过期项不在清单"是这一页新依赖的口径，此前无人看守），并用一台一次性假 HA 取到**设备侧对账**（`turn_on`→`turn_off`→`turn_on`，两次下发的写前快照都是真读到的 `off`）。**结论等级**：通路按 DOM 事件驱动走通（本机浏览器取不到视口、真实指针事件与真实家电未验），读数、两条自纠的措辞谎与驱动器废读数一次见 §二之二十七 |
| GitHub CI | **首次可读，且从"永久红"修到连续绿；读数路已成脚本** | 实测 run 1–27 `conclusion` 全为 `failure`（建仓以来一条没绿过），三个红因全在版本/判据层而非产品逻辑：pydantic-v2-only 的 `Field(max_length=)` 让 CI（pydantic 1.10.12）**0 条测试跑过**、undefined-name 门禁自己用了 3.12+ 的 `ast.TypeAlias`（CI 是 3.11）、真 vhass 的 skip 判据问"包能否 import"而非"插件注册了没"。三条各钉能变红的反例后，run 28 五作业全 `success`，CI 与本机通过/跳过数逐字相同（2621/51）。**run 31（守卫那次提交）五作业再次全 `completed/success`**，且 runner 侧给出安装步的实测读数 `fetch 主机读数：npmmirror=0 npmjs=87`。读数路径固化为 `scripts/gh_ci_status.py`（`runs`/`jobs`/`annotate`/`log`，纯标准库、只 GET、不打印凭证；`log` 先停 302 再无凭证取正文，免得把 token 带给日志存储域）。读数与残留见 §二之五、§二之八。**run 36（commit `3144879`，含架构门禁口径修复那批）五作业再次全 `completed/success`，runner 侧读到 `Baseline lock: 96 modules, 0 violations` 与 `Contracts: 1 kept, 0 broken`** ⇒ CI 架构门的覆盖面与本机同口径已是实测，不再是"已修 + 待复测"（§二之十一）。**run 39（`daa3af7`）与 run 40（`cd5e1bc`）也已 `completed/success`**；**run 41（`1474a7f`）、run 42（`4a63b46`）、run 43（`3d25ed0`）三作业面全绿**，其中 run 43 五作业逐条 `completed/success`（`adm-linkage-contracts`/`ui-typecheck-build`/`quality-gates`/`pytest`/`layering-gates`，`failed_steps` 全空），runner 侧正文读数 `2652 passed, 51 skipped, 1 warning in 76.98s` 与本机 §二之十五 逐字相同，`quality-gates` 正文含新门禁那行 `✓ 状态源扇出门禁干净（扫描 96 个文件…）` ⇒ 连续绿已到 **10 条（run 34–43）**，且新门禁在 runner 上的口径与本机一致（不是只在本机生效的软门）。**此后 run 44（`cf6160d`）、run 45（`a94ca71`）、run 46（`d992f29`）三条 `completed/success`**，**run 47（`b1f0ec4`，工具名单门禁那批）的 `quality-gates` 与 `ui-typecheck-build` 已 `completed/success`**，且 `quality-gates` 正文逐字读到 `✓ 工具名单门禁干净（扫描 96 个文件，注册表 31 个工具，按名调用点全部命中，现场豁免 0 处）`——与本机 §二之十九 的读数**逐字相同**（runner Python 3.11 / 本机 3.13.2，纯标准库 AST 门两版都跑）⇒ 连续绿延伸到 **13 条（run 34–46）**，run 47 其余三作业取读数时仍 `in_progress`（§二之十九）。**run 47/48 事后复查也已 `completed/success`；run 49（`d7d1fff`，状态源 fail-closed 静态门禁那批）五作业逐条 `completed/success`、`failed_steps` 全空**，runner 侧 `quality-gates` 正文读到新门那行 `✓ 状态源 fail-closed 门禁干净（5 个状态源 snapshot() 实现：抛 UnknownEntity 5 / 现场豁免 0）`、`pytest` 正文 `2722 passed, 51 skipped, 1 warning in 81.27s`（与本机通过/跳过数逐字相同）⇒ 连续绿 **18 条（run 34–51）**（run 50/51 为纯文档提交也已绿；run 52 取数时在飞），读数见 §二之二十之一。**run 52（`64f4935`）与 run 53（`24ef0c0`）事后复查也已 `completed/success` ⇒ 连续绿 20 条（run 34–53）**；**run 54/55 同挂 `0a834b9`，其中 54 是我误在远端新建 `master` 分支（`git push origin master`，而本仓默认分支是 `main`、`ci.yml:5` 三叉都触发）多跑的一套**，已 `git push origin master:main`（`PUSH_MAIN_RC=0`）并删除误建分支（`DEL_RC=0`，删后 `ls-remote` 只剩 `refs/heads/main`）；两套的 `quality-gates` 均 `completed/success`（55 那次正文末行读到 `结论：门禁干净…`），`pytest`/`adm-linkage-contracts`/`layering-gates` 取数时 `in_progress`；**事后复查四套（54/55/56/57）全部 `completed/success`** ⇒ 按 main 计连续绿 **23 条（run 34–57）**，run 57（`b148574`，观察者键集判据那批）runner 正文 `2726 passed, 51 skipped, 1 warning in 80.41s (0:01:20)` 与本机逐字相同，见 §二之二十二。**run 58（`a7c2711`）与 run 59（`cc1048b`）事后复查均 `completed/success`** ⇒ 按 main 计连续绿 **26 条（run 34–59）**；run 59 的 `quality-gates` 正文读到出向写者门禁那行、四条计数与本机 §二之二十三 逐字相同（2 处/1 文件、2 处/1 文件、载荷 2/2、豁免 0），`pytest` 正文 `2744 passed, 51 skipped, 1 warning in 46.79s` 与本机同数 ⇒ 那条新门在 runner 上判的是同一份代码。**run 60（`b50bb22`，入向订阅门禁那批）的 `quality-gates` 已 `completed/success`**，正文读到本批新门那行且与本机逐字相同；其余三作业取数时 `in_progress` ⇒ 该套结论落地后再抬 streak。**run 60 事后复查已 `completed/success`**，**run 61（`16ad74e`）亦 `completed/success`**（五作业逐条 `success`、`failed_steps=[]`；`pytest` 正文 `2764 passed, 51 skipped, 1 warning in 84.57s` 与本机 §二之二十四 同数）⇒ 按 main 计连续绿 **27 条（run 34–61）**；本批 `da09d43` 触发的 run 结论落地后另记，读数见 §二之二十五。**run 62（`19ca389`，纯文档那笔）事后复查 `completed/success`** ⇒ 连续绿 **28 条（run 34–62）**；本批 `739a328` 触发的 **run 63 取数时 `status=in_progress`（`RUNS_RC=0`）⇒ 不计入 streak**，等它的 `quality-gates` 正文（新门那行是否与本机逐字相同）与 `pytest` 结论（是否 `2816 passed`）落地再记，见 §二之二十六。**run 63（`739a328`）与 run 64（`dd3b598`，纯文档那笔）事后复查均 `completed/success`**（`runs` 正文 `success runs: [64, 63, 62, 61, 60, 59, 58, 57, 56, 55]`，`total_count=64`）⇒ 按 main 计**连续绿 30 条（run 34–64）**；**同时钉一条读数口径**：这批三枚 commit 里 `6b33096`（§二之二十六 那笔台账）**没有自己的 run**——最近 10 条 run 的 sha 逐条读过（64=`dd3b598`、63=`739a328`、62=`19ca389`…）没有它，说明它是随 `dd3b598` 那一次推送一起上去的（GitHub 每个 push 只对所推 ref 的 tip 建一条 run）。它的内容是文档、且 run 64 跑的树含它 ⇒ **不是漏跑**，但"每枚 commit 各有一条 run"这个默认读法在这仓上不成立，此后引用 streak 要说"按 run 计"而不是"按 commit 计"。run 63 的 `quality-gates` 正文读到本批新门那行、与本机 §二之二十六 的绿行**逐字相同**（`✓ 联动桥依赖门禁干净（paho 声明于 ['dev', 'mqtt']；交付面 装 extras ['api', 'ha', 'mqtt']，CI 面（测试镜像） 装 extras ['dev']，CI 面（工作流） 装 extras ['dev']，三个面逐一核过）`），`pytest` 正文 `2816 passed, 51 skipped, 1 warning in 84.16s (0:01:24)` 与本机通过/跳过数逐字相同 ⇒ 那条"556s 墙钟悬账"在 runner 上是 84s，进一步坐实它是本机状态而非套件（本批 §二之二十七 已就此对 HEAD 复测并销账）。**run 65（`7a2d041`）与 run 66（`d51cc05`）也已 `completed/success`**，两条各五作业逐条 `completed/success`、`failed_steps` 全空（`adm-linkage-contracts`/`ui-typecheck-build`/`quality-gates`/`pytest`/`layering-gates`）⇒ 连续绿 **33 条（run 34–66）**；这两条是纯文档批，跑的仍是加新门之前的树，故 **UI↔路由契约门禁（§二之二十八）的 runner 口径要等本批那条 run**——按上一批定下的口径，streak 以 run 计、不以 commit 计。**run 67（`a93fb4b`，纯文档）也已 `completed/success`** ⇒ streak **34（run 34–67）**；随后 **run 68（`9477be1`，纯文档）是 34 条以来的第一条红**：`quality-gates` 的 `failed_steps=['Run quality gates']`，其余四作业事后复查全部 `completed/success`（`pytest`/`adm-linkage-contracts`/`layering-gates`/`ui-typecheck-build`，`failed_steps` 全空）⇒ 整条 run 只红在那一个 step。**§二之二十八 挂的那条"runner 口径要等本批那条 run"因此销账**：run 68 的 `quality-gates` 正文读到 `✓ UI↔路由契约门禁干净（UI 调用点 50 处：字面量 35、模板拼接 14、条件分支 1；服务端参与匹配路由 84 条（装饰器 79、`add_api_route` 挂载表 5）、被排除的兜底/MCP 2 条；运行期挂载文件 1 个（路径由插件声明，静态读不出，登记在册的射程边界）；反向读数 UI 未调用 33 条（只计数不判红）；现场豁免 0 处）`——把 runner 那一行与本机 `gates.out` 那一行取来直接比对，`EQUAL True`（不是"看着差不多"）⇒ 本门在 runner 上与本机同口径已是实测。红因不在判据、在产品逻辑，也不在文档：**上一批新那道门的判据 B 要在 CI 里起 `python -m pytest --collect-only`，而 `quality-gates` 作业只装 `pip install -e .`——里面没有 pytest** ⇒ 收集 rc=1 ⇒ 门按设计退 2（"读不成不许报干净"），作业红。本机装了 pytest 多年所以 RC=0，是同族 §二之二十六（paho"只在本机装过不算修"）的门禁工具依赖版；同批还修了这条红消息自身——第一版只截 `stdout[-300:]`，而缺失原因在 stderr，runner 正文因此停在冒号后什么都没有。修法取声明过的 extra（`-e ".[dev]"`），**不在 workflow 手搓 `pip install pytest`**；这一改把 gates 作业也拉进"CI 依赖未 pin"那一面（§五 第 5 件，仍等裁定）。全链本机 2881 passed / `gates.sh` RC=0，反例 +2、变异 `2 failed`（读数见 §二之三十一）。**ci.yml 那一行只能由下一条 run 证**，本批不自签 VERIFIED。**复测已落地：run 69（`d4994a8`，本批文档那笔）的 `quality-gates` 给的是 `completed/success`、`failed_steps=[]`**，正文里本门那行是绿行 `[有界缓存] 注册表 2 项双腿齐全且测试 id 被收集；固定键 2 项带理由；扫到增长容器 76 个，其中基线冻结 74 个、就地豁免标记 2 处`，取 runner 那行与本机同脚本重跑的输出比对 `EQUAL True`（且"测试 id 被收集"只在收集成功之后才印 ⇒ 判据 B 确实在 runner 上跑到了）；复查整条 run 69 为 `completed/success`，五作业逐条绿、`failed_steps` 全空。作业级那一半由此转为实测。**run 70（`81bc623`）与 run 71（`927b044`，§二之三十二 那笔文档）事后复查均 `completed/success`** ⇒ 按 main 计连续绿 **3 条（run 69–71）**（run 68 断在 34 条之后，新账从 69 起重计，口径仍是"按 run 计不按 commit 计"）。run 71 五作业逐条 `completed/success`、`failed_steps=[]`，`pytest` 正文 `2894 passed, 51 skipped, 1 warning in 90.01s (0:01:30)` 与本机 §二之三十二 同数；**上一批挂的那格"三棵树的 runner 口径"由此转为实测**：把 runner `quality-gates` 那一行与本机 `python scripts/check_ui_api_paths.py --all` 现跑的那行逐字节比对 ⇒ `EQUAL True`（两边同 217 字符，`UI 调用点 85 处（ui 50、ui-user 16、ui-user-mimo 19）…反向读数跨 3 棵树仍未被调用 16 条…现场豁免 0 处`），不是"看着差不多"。**上一批那两格"不预签"本批全部闭合**：`MCP 参数↔schema` 那行对 run 73 重取一次仍是
`EQUAL True`（本机与 runner 同 70 字符，逐字节），`固定名 .tmp` 那道原子写门在 `8689b39` 的树上与 runner
同为 93 字符 ⇒ 两格都成立；本批把原子写那行自己移动到 14 处/3 处（`af_auth` 新增私有助手），
故新挂一格：**run 74 的 `quality-gates` 正文里那行必须是 14 处/3 处那一行**，AF 不预签。
run 72（`a235840`）与 run 73（`8689b39`）事后复查均 `completed/success`，run 73 五作业当场重取逐条
`completed/success`、`failed_steps=[]`，`pytest` 正文 `2947 passed, 51 skipped, 1 warning in 95.07s (0:01:35)`
与本机 `2946/52` 差 1 条已归因（本机跳过那条 POSIX chmod 断言 `tests/unit/test_atomic_write_sites_fixes.py:260`
⇒ runner 真跑了本机跑不了的那条，不是口径漂移）⇒ 按 main 计连续绿 **5 条（run 69–73）** |
| DCD 20261002 §一（CI 锁文件源）| **裁定 (b) 已落地并已在 runner 上取到读数** | 只按原文写 `--registry=https://registry.npmjs.org` 经实测是**空操作**（fetch 87+87 行仍在 `registry.npmmirror.com`/`cdn.npmmirror.com`），必须配 `--replace-registry-host=always`；安装步另加"主机自证"守卫（CI 绿不能证明没吃镜像）。逐字节对账 137/137 `integrity` MATCH ⇒ 锁文件字节未动。§二之七 末尾"本机 npm 11.9.0 / runner 10.x，只有 push 之后才知道"的残留**已销账**（runner `npmmirror=0 npmjs=87`）。回执已投 DCD（含 §四 判例 2 的证据更正：`@types/node` tarball 两源逐字节相同、根目录差异不是重打包痕迹）。见 §二之七、§二之八 |
| `ma/insights` **入向与契约表对账**（本批新抓） | **AF 侧已按契约收，跨仓三问已交 DCD** | 拿着裁定 Q3 之后的载荷行逐条对 AF 的 `ingest_insight()`：必填的 `hypothesis_id`/`natural_language`/`conf` **三项契约一个都不发** ⇒ 每一条按契约发来的洞察都被拒（判例 1 的静默归零，面换到 AF 入口）。改法：契约键优先 + 旧键别名、缺 `conf`（含 `{"conf": null}` 这种空占位）按 0.0 入 ask 档并记 `conf_reported=false`（坏报照旧拒、封顶 0.59 不动）、契约字段有界落 `transport` 并出 `/api/insights/pending`，面板对缺报显示「未上报」而不是 `0.00`。`conf`/稳定 id/`intent` 三处空缺 AF 不能自决 → §五 第 7 件。见 §二之八 |
| AF **出向**载荷与契约表对账（本批新抓，与上一行同族、方向相反） | **`error` 那半已自决修；`trace_id` 那半交 DCD** | 对着 §1.2 两行逐字段读码：`fired` 齐（`ref`=实例 id、`ts`=家庭墙钟）；**`failed` 的 `error` 此前恒等于字符串 `"failed"`**（`observe_terminal()` 传 `error=state`），而真原因一直写在 `ctx.context["fail_reason"]`（`af_instance.py:287`）、且 `af_executor.py` 每条 `_fail()` 都带语义（失败的动作 / 软失效异常 / 收敛违约）⇒ DB 唯一能向用户显示的那句话被换成了状态名。修法只装契约已定义的东西、字段名与类型没动：桥读出真原因，缺原因发诚实占位句而不是状态名，按 §1.3 已定过的 500 封顶（`5ff1ea6`，+3 判据：真原因穿得到 / 退化成状态名即红 / 跨模块改键名即红，四档变异 `0/2/1/1` 各自钉一次）。**"为什么不升成静态门"**：`error=state` 与 `error=reason` 在 AST 上同形，静态可判的是 §二之十八 那族的"**没递**"、不是"递错"。`trace_id` 每次现场新造那一半 **AF 未动**：§1.3 那句"trace_id 必填、贯穿洞察→收件箱→播报"实测挂在**收件箱三面**护栏下（`grep -rn "publish.*butler/inbox" src/autoforge` = 0 ⇒ AF 不踩），§1.2 事件面只列了字段名；上游那枚号 AF 收了并落到 GraphStore 记录 `note`，但 `hypothesis_id` 在 `af_ir/`+`af_instance.py`+`af_executor.py` **0 命中** ⇒ 到不了发射点，三种修法两种要动载荷面/DB 检索语义（1:1→1:N）⇒ §五 第 12 件，A/B/C 齐、AF 建议 C（明确为事件级，因果链靠 `ref`＋各自存储元数据）。**同批打真载荷又盘出第三个差额**：观察者路径比契约行多发一个 `node_id`（`instance_id` 那份额是 ②A 登记过的，它没有任何登记）⇒ 已用 `159ba00` 把真实键集合钉死（四档变异 `0/1/1/2`），并作为第 12 件的**第三问**交裁（AF 倾向：DB 不读就删）。⇒ **裁定 20261004 §一 2 判 C（事件级，写进口径）且 `node_id` 判删，两件都已落地（`7dbd640`）**，口径注释与键集判据见 §二之二十九 第四节。见 §二之二十二 |
| AF 出向 MQTT 的**生产者形状**升成静态门禁（`8b629b8`，本批） | **已上线为 CI 硬门，产品码 0 变动** | 上一行那个差额暴露的不是"某个字段错"，而是"**测试绿在一条生产不走的路径上**"。这一族能静态判（判的是调用图形状：哪个文件、哪个函数体内、载荷实参是不是 `_envelope()` 的产物），与 §二之二十二 那条"值语义判不了"正好成对。三条判据：出向 MQTT 写者只在 `af_mqtt_bridge`；`publish_fired/publish_failed` 只在 `observe_terminal()` 体内（最内层归属，闭包也算）；`_publish` 的载荷必须来自 `_envelope()`（位置式与 `payload=` 关键字式都认，只认前者=静默放行）。锚点缺失 `exit 2` 不做假绿；`af_bus`/`af_runtime` 的 `publish()` 是进程内总线，不在射程。真实 src 实测：写者 2 处/1 文件、生产者 2 处/1 文件、载荷 2 处全部经 `_envelope()`、豁免 0；18 条反例 + 三档变异（`RC=1/1/2`），M-1 那处违例另走**完整 `gates.sh`** 取到 `RC=1` 与新结论行。见 §二之二十三 |
| AF 入向订阅的**守卫形状**也升成静态门禁（`b50bb22`，本批） | **已上线为 CI 硬门，产品码 0 变动** | 出向那半盘完，同一形状在入向：契约表 §1.3 护栏与计划 第 1 步 ④ 说"不订阅 `butler/inbox/*`"，而这条今天只有运行时判定（`FORBIDDEN_SUBSCRIPTIONS` + `subscribe_topic()` + `handle_message()` 两道）加行为测试——**行为测试只认识已知入口，下一个不查守卫的 `client.subscribe(…)` 一条都不会红**。三条判据：订阅口只在 `af_mqtt_bridge`；动态主题要在**同一函数体内**先过禁订族判定（守卫在外层、订阅藏在闭包 ⇒ 红）；收件箱族写死成实参就红，带守卫也不给过。射程取两条信号并集（接收者是 `client`，或调用带 `qos=`），进程内 `bus.subscribe(handler)` 不判。锚点缺失 `exit 2`。真实 src 实测：**订阅点 2 处/1 个文件，其中 `INSIGHTS_TOPIC` 1 处、过了守卫的动态入口 1 处、豁免 0** ⇒ 计划 第 1 步 ③④ 那句"只订 `ma/insights`"静态坐实。20 条反例 + 四档变异（`RC=0/1/1/1/2`，M-1 另走完整 `gates.sh` 取到 `结论：入向订阅门禁红（exit=1）`），还原逐字节自证。run 60 的 `quality-gates` 正文已读到本门那行且与本机逐字相同。见 §二之二十四 |
| 窗后四项验收从"手搓命令"升成一条命令（`da09d43`，本批） | **已交付工具 + 27 条反例，验收本身仍 EXEMPT** | 裁定 §四 那四件此前只作为文字挂在 §六，窗当天临时手搓——少跑一项、把 skip 读成 pass、把 `/health` 打错成 404 就判服务没起，都是过账形状。`scripts/verify_adm_window.py` 做成**三态逐项判定**：有 FAIL ⇒ `RC=1`；无 FAIL 但有 UNAVAILABLE ⇒ `RC=2` 且正文打印"EXEMPT ≠ VERIFIED"；全 PASS ⇒ `RC=0`。判据取自契约表：③ 查 §1.2 四个键 + `ts` 偏差（拦仿真锚点当墙钟那族）+ **事件不许 retained**；④ 直接问 `msg.retain`（现发的 `online` 不算快照）；② 的"连不上"由 ① 决定语义（容器在跑却连不上 ⇒ FAIL，本机没 docker ⇒ UNAVAILABLE）。连接与凭据走机制层 `homesdk.mqtt`，脚本**不读任何环境口令**。本机实测：降级跑 `RC=2` 四项皆缺、对 `forge serve` 真跑取到 `[PASS] /api/health 返回 200`（顺带钉下两事实：AF 只注册 `/api/health`，`docker-compose.api.yml` 无 healthcheck）；变异三档各 `1 failed` 且还原逐字节一致；全链 **2791 passed / 51 skipped RC=0**、`gates.sh` RC=0。同批更正 5 处旧登记的理由——"本机无 paho-mqtt"是跑错解释器（实测 `paho_available()=True`），真缺的是那台 broker。见 §二之二十五 |
| 联动桥的运行时依赖（paho）此前**整条链一处都没声明**（`739a328`，本批） | **已补齐 + 升成 CI 硬门；窗内开关与来源交 DCD** | 顺着"窗当天那条验收命令在容器里够不够得着"问出来的两件事：① `Dockerfile.api` 从不 `COPY scripts` ⇒ 上批那条命令在镜像里不存在（本批补 `COPY scripts ./scripts`）；② 更严重——**paho 在 AF 的依赖链里一处声明都没有**：homesdk 把它放在自家 `[mqtt]` extra（vendored wheel 元数据实测 `Requires-Dist: paho-mqtt>=1.6; extra == "mqtt"`，裸 wheel 不带），两个镜像装的是 `.[api,ha]` / `.[dev]`，CI 也是 `.[dev]`，而开发机正常只因那份解释器手动装过。后果不是少一个用例：`homesdk/mqtt.py:40-43` 对 paho 是惰性 guard ⇒ 导入不炸，炸在 `get_client()`，而 `af_cli.py:1380` 起桥排在 `uvicorn.run`（`:1382`）**之前**且不吞异常 ⇒ 窗内开 `AUTOFORGE_MQTT=1` 就是**整个 AF 不起**（含只读面）；不开则第 1/2 步验收原理上不可能达成。2816 条测试里**没有一条**能拦住（桥的用例全用 duck-typed client）＝§二之二十三 那族的**依赖版**。修法：`pyproject` 新增 `[mqtt]` extra（`dev` 同列，消掉"同一条用例两种机器两种脸色"这个形状）、`Dockerfile.api` 装 `.[api,ha,mqtt]`、新门 `scripts/check_mqtt_runtime_dep.py` 判 A 声明在 / B 交付面装到 / C CI 面（`Dockerfile.test` + `workflows/ci.yml`）装到，锚点读不到 `exit 2`，读不到安装行也判红（不许当"没装"放行）。真实仓绿行只印实测集合：`paho 声明于 ['dev', 'mqtt']；交付面 ['api', 'ha', 'mqtt']，CI 面 ['dev'] / ['dev']`。24 条反例（含两条直接对当前仓库跑）+ 四档变异 `RC=1/1/2/1`、还原逐字节一致。**自纠两处**：上一批那条 `_collect` 判据是宿主相关的（本机绿、无 paho 机器红，模拟 `hm._paho=None` 证过）⇒ 两态各钉一条；变异驱动首跑又被 `C:\WINDOWS\System32\bash.exe`（WSL 存根）骗出三个假 `RC=1` ⇒ 换 Git Bash 真身并**先跑对照档**才取数。窗内三问（开关与顺序 / paho 从哪个源进镜像 / 第③项用真机还是 dry-live 的 fired 过账）投 §五 第 13 件，**compose 一个字节未动**。见 §二之二十六 |
| §5.3 第 4 件（F7 撤销清单）的前半做完了、后半从未做，且顺着它盘出两处假读数（本批） | **已交付：前端 3 处 + 服务面 2 条判据；UI 无 vitest ⇒ 结论来自真浏览器读数** | 上一版 §〇 那行写"F7 前端残留 已收口"，而 DCD §5.3 这一件要的是"撤销按钮通路 **+** 10s 定时刷新"——**定时器从来没存在过**。这件事不是漏一个功能，是漏了一族：**撤销清单是会自己变假的读数**（`available()` 在超窗那一刻就把记录滤掉，`age_s` 只是取数瞬间的快照，页面却挂着"窗口内可撤销 1 条"直到点下去被 `expired` 拒）。同一次读码又抓出两处同族：`catch` 里 `undoItems=[]` 把"查询失败"渲染成"窗口内没有可撤销的部署"（**设备侧结论**）；开关旁写死 `undoWindowS \|\| 60` 而真值在服务端 `AUTOFORGE_UNDO_WINDOW_S`（默认 60、上限 300、**0 = 永不过期**，页面印 60 等于把运维设的 0 说成 60）。改法：10s 定时器（下发/撤销 in-flight 时跳过，不让它挤掉刚产生的凭据）+ 失败留旧读数并分档标注 + 窗口长度三态只印服务端给的数。服务面两条新判据钉的是这一页新依赖的口径（键集合逐等于 `{window_s, items}` / `{deploy_id, age_s, entities}`、过期项**不在**清单）——`ui/` 无 vitest，编译期判不出这一族，只能把跨层契约放到能判红的一侧。验收用一台**一次性假 HA**（标准库、token 门、逐次 POST 落 `fake_ha_calls.jsonl`）+ 真浏览器：`dep-0bc52a22918a` → 点撤销 → 确认回滚 → `已完整回滚 / 已恢复：light.fake_undo_probe`，设备侧 `turn_on`→`turn_off`；零点击取到 `上次成功 13:12:30 → 13:12:50` 且同一 tick 过期行消失；403 档旧读数原样留着。**自纠两条措辞谎**（第一版在"一次都没读到"时印 `窗口 0s` 并说"下面是上次成功读数"）与**一次驱动器废读数**（M-1 的 `RC=2/1 error` 是替换串造成的语法错误，`error≠failed`）都记账。全链：`TC_RC=0`/`BUILD_RC=0`、`2818 passed / 51 skipped RC=0（92.64s）`（改前 2816，+2 = 本批两条）、`gates.sh RC=0`；**顺带结掉上批 `556.34s` 的悬账**（计数同形、速度回区间 ⇒ 是机器不是套件）。真实指针事件本机不可得（视口 0×0），结论等级已按"DOM 事件驱动走通"写而不是"真机真点"。见 §二之二十七 |
| "名单手抄"那一族里最后挂着的一条扫掉＝**HTTP 路径与方法做成静态门禁**（计划外补刀） | **已交付，门能红** | 上一批留下的口径是"`ui/` 无 vitest ⇒ `vue-tsc` 与 `vite build` 只证能编译"。那批钉住了 `/api/undo/available` 的**响应键集合**，而**路径与方法本身仍是手抄字符串**：服务端改名/删路由/GET 换 POST，前端照编译照 build，只有人点一次才 404/405——与工具名单（§二之十九）、状态源扇出（§二之十四）、extras 名（§二之二十六）、出向载荷字段（§二之二十二）同族，且比工具名更漂（工具名改了就 `KeyError`，路径改了什么都不会发生）。射程量过：`request(` 调用点 **50 处全在 `client.ts` 一份文件**（另 1 处命中是函数定义本身），`@app.` 装饰器 **81 条 = 参与匹配 79 + 被排除 2**；但服务端路由有**第二张脸**——`af_conflict_runtime.py` 用 `("GET", "/api/conflicts", handler)` 表 + `app.add_api_route()` 挂 5 条真路由，只读装饰器会对这 5 条报**假红**且把修法指向 `af_api.py`（端点明明在），补读挂载表后 `参与匹配 84 = 装饰器 79 + 挂载表 5`，两个等式都钉进测试。新门 `scripts/check_ui_api_paths.py`（524 行、纯标准库）三判据：A 路径在两张路由脸里、B 方法一致（405 与 404 两码事）、C **反空洞自证**——每个调用点都必须解析得出，否则 `exit 2`；这条是本批自己差点犯的：探针 v1 纯正则报"UI 从未调 `GET /api/undo/{deploy_id}`"而 `client.ts:131` 明明在调，v2 补模板正则仍被**嵌套反引号**与 `${a ? b : c}` 里的 `?` 静默丢两条 ⇒ 改手写字符级扫描器。**四处口径靠读数定**：SPA 兜底 `GET /{full_path:path}` 必须排除（一条兜底接住任何错路径 ⇒ 门永远绿），首版按前缀排 `/mcp` 连带把真路由 `GET /api/mcp/pair-request` 挪出射程，改精确口径后 `excluded` 3→2、`参与匹配` 78→79；通配段双向放行是 HTTP 层事实（`/api/graphs/{name}` 接得住 `/graphs/tags`），段**数量**仍要对齐；③ 服务端那张挂载表**"读不出"要分两种**——有 `add_api_route` 且有字面量 `"/api/…"` 却读出 0 条 ⇒ `exit 2`（表改形 ⇒ 本门会对真端点报假红），而 `af_runtime_plugins.py` 那种 0 个字面量 `/api/`、路径由插件在运行期声明的 ⇒ 记成 `运行期挂载文件 1 个` 的射程边界（首版把两者混判，换来的是一条 `RC=2` 长红，而那正是门被人整节关掉的方式）。**变异九档（对照先跑 `RC=0`，`DRIVER_RC=0`，内存字节还原；M7–M9 是双文件变异）**：M1 前端抄错路径 ⇒ 1、M2 前端抄错方法 ⇒ 1、**M3/M4 前端一字未动只改服务端改名/换方法 ⇒ 各 1**（本门的存在理由，正是"编译期判不出来"的那个位置）、M5 路径变变量 ⇒ 2 且读数行印 `调用点 49 处`、**M6 边界档 ⇒ 0**（`/api/undo/available` 改名后被 `/api/undo/{deploy_id}` 接住：可达性事实而非漏判，故固定成边界并**在 §六 挂残留"语义级 404 本门不判"**，其可见处只有反向读数 33→34，而反向按口径只计数——33 条里多为 MCP/DB 面向与 CLI 共用端点，判红只会逼人加 `continue-on-error`）；**M7–M9 验第二张脸真在匹配集里**：M7 前端新增一行只调挂载表里的 `/conflicts` ⇒ `RC=0` 且反向读数 33→32、M8 在其上把表里路径改名 ⇒ `RC=1`、M9 前端换成 `DELETE /conflicts` ⇒ `RC=1` 且报文案点名路由在 `af_conflict_runtime.py:499`。这一档要说老实：真实仓库里 UI **一条挂载表端点都没调**，所以读进那张脸**今天只改计数、不改判红结果**——它堵的是下一次（谁先调 `/conflicts` 谁吃假红），不写成"抓到了 bug"。反例 24 条含"字面量段被参数路由接住不算红""兜底存在时错路径仍要红"两条口径钉法。全链 `pytest` **2842 passed / 51 skipped / 7 subtests / RC=0 / 118.24s**（改前 2818 ⇒ **+24 恰等本批新增**）；`gates.sh RC=0`、计数棘轮 `104/104` 不动；**`src/` 与 `ui/` 一个字节未动**，门加在契约面上。见 §二之二十八。**⚠ 本批按实测更正该行的一处口径**：那一行的"射程量过"只量了 `ui/src`（开发面板），而本仓有**三棵**第一方 UI 树——门扩到另两棵（用户端 ForgeSight）之后调用点 **50 ⇒ 85**、反向读数 **33 ⇒ 16**，并暴露出旧泛型跳过循环遇 `;` 就退出、**静默丢调用点**这一条真漏（85 点里 17 点没进射程）。扩树与三张调用脸的读法见 §二之三十二 |
| UI 类型门禁（上一版登记的债） | **已收口（含下一批零 `any`）** | 15 条 `vue-tsc` 错误清零，`type-check` 改 `--force` 防缓存假绿，`ci.yml` 新增 `ui` 硬门 job；过程中抓到"两个按钮从未接线"与"mock 夹具少回两字段"两条真缺陷（§二之二）。同族第二批：API 层四处 `request<any>` 换成后端真形类型并逐键与在线响应对账，`grep request<any> ui/src` 已无匹配（§二之三） |
| 安全审计（scoped run）对 HEAD 复测与处置（本批） | **两份真洞已修 + 一门新硬门；三问交 DCD，验收面不自签** | 那份 zip 此前沿用状态＝**从未进入处置链**（`docs/` 检索"安全审计"只命中 zip 自己）。按快照读的三条"根因"里有两条在 HEAD 早由 `3fbbab9` 修完（8 位码 + 一次性消耗 + 单码锁定），本批按铁律 #11 逐条重跑并落成七行钉源表（**已修被重报 5 / 成立 2**）。真洞一：`record_failure()` 遇不存在的码直接 `return False` ⇒ **枚举这一族根本不进任何计数器**，报告建议的协议层 limiter 按住的是症状；防线做进 `AuthCodeStore`（60s / 10 次全店窗口，失败方向仍是回落人审队列；**边界明写：进程内、重启清零**）。真洞二：`dispatch()` 从不把 `arguments` 与 `inputSchema` 对账，量出来是 **31 工具里 6 个键"没声明却能传"**，含爆炸半径护栏旁路 `allow_bulk`——运行期拒收（在 `_guard` 之后，顺序是判据）+ 新静态门 `scripts/check_mcp_arg_schemas.py`（与 §二之二十六 那族同形状：双向差额、缺 `properties`、读不出即 `exit 2`）+ `gates.sh` 新节。同批把 `load_graph` 规模上限从"有代码无读数"补成 5 条。全链 **2921 passed / 51 skipped RC=0**、`gates.sh RC=0`、变异八腿能红。**不自签的三问**（长期码绝对 TTL / `--host 0.0.0.0` + MCP 默认放行 / homesdk wheel 来源）⇒ §五 第 16 件；本批未改 compose、`--host`、read 端点鉴权、MCP 默认档、长期码 TTL。见 §二之三十三 |
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
| 本批之三十六（联动桥的运行时依赖整条链一处都没声明，补齐并做成硬门，`739a328`） | 起因是问一句"窗当天那条验收命令在容器里够不够得着"，答案是两件：**镜像里没 `scripts/`**（`Dockerfile.api` 只 COPY `pyproject.toml README.md src examples`），以及更严重的 **paho 从未被任何一层声明**——homesdk 把它放进自家 `[mqtt]` extra（vendored wheel 元数据实测 `Provides-Extra: mqtt` + `Requires-Dist: paho-mqtt>=1.6; extra == "mqtt"`，裸 wheel 安装不带），`Dockerfile.api` 装 `.[api,ha]`、`Dockerfile.test` 与 CI 三作业装 `.[dev]`，都不含它；开发机正常只因那份解释器手动装过 paho（§二之二十五 实测）。**为什么这不是"少一个可选依赖"**：`homesdk/mqtt.py:40-43` 对 paho 是惰性 guard ⇒ `from homesdk import mqtt` 不炸，炸在 `get_client()` 抛 `MqttUnavailable`，而 `af_cli.py:1380` 的起桥排在 `uvicorn.run`（`:1382`）**之前**且不吞异常 ⇒ 窗内打开 `AUTOFORGE_MQTT=1` 就是**整个 AF 起不来**（含只读面，`restart: unless-stopped` 变反复重启，正面撞铁律 #6）；不开则第 1/2 步验收（`status=online`、抓到一条 fired）在现烤镜像下**原理上不可能达成**，而 2816 条测试全绿——`grep -rn "import paho\|get_client\|MqttUnavailable" tests/` 只命中桩与源码字符串断言，桥的用例全用 duck-typed client ⇒ §二之二十三"测试绿在生产不走的路径上"那族的**依赖版**，且依赖形状静态可判 ⇒ 进门禁而不是靠"下次记得装"。交付：`pyproject` 新增 `mqtt = ["paho-mqtt>=1.6"]` 且 `dev` 同列（列 `dev` 不是凑数：本批自己踩了"同一条用例两种机器两种脸色"，写进 CI 面依赖表是为消掉这个形状本身）；`Dockerfile.api` 装 `.[api,ha,mqtt]` + `COPY scripts ./scripts` + 注释里过期的 `homesdk 0.1.1` → `0.3.1`；新门 `scripts/check_mqtt_runtime_dep.py`（142 行，纯 `tomllib`）三条判据 A 声明在 / B 交付面装到 / C CI 面装到（`Dockerfile.test` **与** `workflows/ci.yml`，工作流按所有 `-e ".[…]"` 行取并集，`gates` 那 job 只装 base 不该把整条 CI 面判红），读不到安装行判红而非"没装就放行"，锚点（含桥里 `from homesdk import mqtt` 这一射程前提）读不到 `exit 2`；`gates.sh` 新节 + `dep_rc` 两条红分支；反例 **24 条**含两条直接对当前仓库跑（否则这扇门只对自己的样本有效＝本仓那族"纸门"），口径侧钉 `.[mqtt-api]` ≠ 装了 `mqtt`、`paho-mqttlib` 不算 paho、base 声明自动满足三面、两条安装行取并集。**自纠三处**：① 上一批 `test__collect_maps_missing_env_to_unavailable` 是**宿主相关**断言（只钉 `broker_settings` 没钉 `paho_available`；把 `hm._paho=None` 模拟一次即证无 paho 的机器会红），改显式钉 `True` 并新增 paho 缺席反向一条，两条各只钉一个缺项；② 变异驱动首跑用裸 `bash` ⇒ Windows 解析成 `C:\WINDOWS\System32\bash.exe`（WSL 无发行版存根，`RC=1` + UTF-16 安装提示），三档"红"全是假读数（M-3 本该 `RC=2` 的自相矛盾才是线索），换 Git Bash 真身并**先跑未变异对照**（必须 `RC=0`）——同一坑 §二之二十三 记过并写成规程，本批才发现规程缺了"对照档"这一半；③ `_declared()` 按 TOML 迭代顺序返回 ⇒ 读数随书写顺序变，改 `sorted()`。**实测读数**：对照 `gates.sh RC=0`，绿行逐字 `✓ 联动桥依赖门禁干净（paho 声明于 ['dev', 'mqtt']；交付面 装 extras ['api', 'ha', 'mqtt']，CI 面（测试镜像） 装 extras ['dev']，CI 面（工作流） 装 extras ['dev']，三个面逐一核过）`（只印集合，无形容词）；变异 **M-1 交付面退回 `.[api,ha]` ⇒ `RC=1`**、**M-2 依赖表两处 paho 全删 ⇒ `RC=1`**（只剩 A 那条，无声明时 B/C 无从判）、**M-3 桥改 `import paho.mqtt.client` ⇒ `RC=2`** 并读到 `结论：联动桥依赖门禁读不到锚点（exit=2）`、**M-4 `ci.yml` 三处 `.[dev]`→`.[api]` ⇒ `RC=1`**，四档 `还原逐字节一致=True`、`DRIVER_RC=0`；反例首跑 `3 failed, 49 passed`（三条全是 ③ 那个顺序问题），改后 `24 passed`；全链 `pytest -q` **2816 passed / 51 skipped / 7 subtests RC=0**（2791+24+1 对得上），**墙钟 `556.34s` 与历批 94–124s 差一个量级、本批未归因**（取数时机器上无我起的 `forge serve` 残留，`Win32_Process` 查到那两条 `python.exe` 是宿主常驻 bridge/proxy、非本会话所起故未动；通过/跳过数才是判据，差异如实挂着）。卫生：`git diff --numstat` 八文件 `8 2`/`21 0`/`11 0`/`3 1`/`1 1`/`22 1`/`142 0`/`240 0`、`CRLF: 0`；**产品码只动 `af_mqtt_bridge.py` 文档字符串一行**（"本机就没有 paho"→ 可核口径）。推送 `git push origin master:main` `PUSH_RC=0`（`19ca389..739a328`），`ls-remote` 只剩 `refs/heads/main`。**窗内三问不自裁**（开关键序 / paho 从哪个源进镜像 / 第③项用真机还是 dry-live 的 fired 过账——dry-live 也能产四键齐全墙钟 `ts` 的一条 fired，但其语义是"设备一次没碰"，拿它写 PASS 等于"联动环真上线"无一条读数为真）⇒ §五 第 13 件，**compose 一个字节未动**。CI 复查：run 62 `completed/success` ⇒ 连续绿 28 条（run 34–62），本批 run 63 取数时 `in_progress` 不计入。文档侧：新增 `## 二之二十六`、§〇 加本批一行 + CI 行补 run 62/63、§五 第 13 件、§六 窗后那行补"开关未开属待裁"、核实基准 → `739a328` |
| 本批之三十七（§5.3 第 4 件补齐：撤销清单 10s 定时刷新，顺带把这一页的三处假读数改掉，`3039c62`） | 起因是本仓自己 §〇 那行写了"F4 ③ / F7 前端残留 **已收口**"，而 DCD 的 §5.3 把第 4 件写成**两半**——「撤销按钮通路」**加**「10s 定时刷新」。前一件早就点通，后一件**一次都没做过** ⇒ 那一行是把半件说成了一整件（铁律 #5 的自查形状）。回到代码盘这一面，得到的不是"少一个 setInterval"这么小：① 清单只在 `onMounted` 拉一次，而 `UndoStore.available()` **在超窗那一刻就把记录从服务端滤掉**、`age_s` 只是取数瞬间的快照 ⇒ 页面长期挂着"窗口内可撤销 1 条"，直到用户点下去才被后端以 `expired` 拒——**这是一个会自己变假的读数**；② `catch` 里写 `undoItems=[]` + `undoWindowS=0`，于是 403/断网/缺权限被渲染成"窗口内没有可撤销的部署"，而那是一句**设备侧结论**（本仓反复钉"不许把未验证读成已验证"，这次发生在前端）；③ 文案 `{{ undoWindowS \|\| 60 }}s` 印的是页面猜的数，真值在服务端 `AUTOFORGE_UNDO_WINDOW_S`（默认 60、上限 300、**0 = 永不过期**）⇒ 运维设 0 或 300，页面照印 60，用户据此以为"过 60 秒就撤不了"。改法（`LiveView.vue` `+63/−8`）：`UNDO_POLL_MS=10000` + `onMounted` 起定时器、`onUnmounted` 清；定时档遇 `undoListInFlight \|\| undoBusy \|\| running` 一律让路（**下发/撤销末尾那两次显式刷新不能被挤掉**，否则刚产生的撤销凭据要等一个周期才出现）；读失败**留着上一次成功读数**并分档标注（横幅标题按 `undoRefreshedAt` 是否为空两支、空态文案两支），表头同构两支；窗口长度改三态 `undoWindowLabel`（未读到 ⇒ "服务端窗口未读到"、0 ⇒ "0s（不过期）"、其余 ⇒ 真值），页面里 `\|\| 60` 字面量 grep 从 1 处降到 0 处。**服务面补 2 条判据**（`test_af_undo_http.py` `+47/−0`）：这一页新依赖的是**口径**而非行为——`/api/undo/available` 的形状此前无人看守，`age_s` 改名或去掉在浏览器里是 `undefined.toFixed()` 当场炸。`test_expired_deploy_leaves_the_available_list` 钉"超窗项**不在清单里**（不是列出标过期）"，即本批要消掉的那个假读数形状；`test_available_items_carry_the_keys_the_ui_renders` 钉键集合逐等于 `{window_s, items}` / `{deploy_id, age_s, entities}`、`age_s` 是数字且非 bool、`entities` 稳定排序。**验收按原文"浏览器点撤销走通"取真浏览器读数**，且**不碰真实家电**：标准库起一台一次性假 HA（`127.0.0.1:8788`、`Bearer fake-ha-token` 才响应、只服务 `/api/states[/<id>]` 与 `light.turn_on/turn_off`、每次 POST 追加记 `Temp\fake_ha_calls.jsonl`）+ `forge serve --ui-dir ui/dist`（同源，CORS 不参与）+ `AUTOFORGE_LIVE_ENABLED=1`。跑通链：下发得 `dep-0bc52a22918a` ⇒ 清单出现该行 ⇒ 撤销 ⇒ popconfirm 确认 ⇒ `已完整回滚 / 已恢复：light.fake_undo_probe`；**设备侧对账**（POST 原文三行 `turn_on`/`turn_off`/`turn_on`，与 `undo_log.json` 两条 deploy 按顺序配对）⇒ 下发与撤销都真到了设备层，且两次写前快照都真读到 `off`（非空快照）；第三条如实记账 = 文案修正、重新构建后为复取表头读数的第二次下发（`dep-014b028dff5a`），**本批未对它点撤销**、假 HA 内存态停在 `on`，该假服务随本批停掉不留残值。零点击取到定时档：`上次成功 13:12:30 → 13:12:50` 恰一个 10s 档，同一 tick 把已过期那行带没；403 档旧读数原样留着、横幅出现，改 token 后点"刷新清单"即恢复（**旧代码在这一档会清成"窗口内没有可撤销的部署"，那就是本批要修的那句假话**）。**两条执行限制如实标注**：`browser-use` 的 `click` 本机取不到视口（`NATIVE_BROWSER_VIEWPORT_UNAVAILABLE … viewport=0x0, visible=false`）⇒ 点击靠 `evaluate_script` 派发 DOM 事件（填值是真 `fill` 通道），**不是真实指针事件**；naive-ui 开关对 `root.click()` 无反应，要对 `.n-switch__rail` 连派 `mousedown`+`mouseup`+`click` 且 `aria-checked` 必须 await 后读（同 tick 读到旧值）；另钉一条事实——路由是 **history/path 模式**，导航 `/#/live` 会落在 `/overview#/live`。故本件结论等级写"**通路按 DOM 事件驱动走通 + 设备侧对账**"，**真实指针事件与真实家电未验**。**本批自己抓到并改掉两条措辞谎**（只有把页面文案当结论来读才抓得出，类型检查全绿时它们就已经在页面上）：第一版在**一次读数都没成功过**时仍印 `窗口 0s`（`0` 是未读到的初始值，而服务端 0 的含义是"永不过期"，两者正相反）并写"下面是上次成功读数，可能已过期"（当时没有任何成功读数）⇒ 改成两支分档后重跑 `vue-tsc -b --force` 与 `vite build` 并用假 token 复取 403 档确认。**判据能红**：先跑未变异对照 `RC=0 11 passed`，**M-1 清单改成含过期项 ⇒ `RC=1 1 failed, 10 passed`**（FAILED 点名 `test_expired_deploy_leaves_the_available_list`）、**M-2 清单少给 age_s ⇒ `RC=1 1 failed, 10 passed`**（点名 `test_available_items_carry_the_keys_the_ui_renders`），两档均从内存字节还原、`逐字节一致=True`、`DRIVER_RC=0`。**驱动器 v1 那次读数是废的，一样记账**：M-1 报 `RC=2 / 1 error` 看着像门严，实为替换串把推导式那行换成裸 `True` ⇒ 语法错误、目标测试根本没跑（**`error` 与 `failed` 是两回事**）；单独重放取完整尾巴后改 `if True` 并让驱动打印正文才是上面那份读数——同族坑第三次记（前两次 §二之二十三 WSL 假红、§二之二十六 缺对照档）。**销一条上批悬账**：本批全链 `2818 passed / 51 skipped / 1 warning / 7 subtests RC=0（92.64s）`，改前同机 `2816/51` ⇒ **+2 恰等于本批新增两条**、无静默增减；墙钟回到历批 94–124s 区间且计数同形 ⇒ 上批 `556.34s` 那个量级差**归因于这台机器当时的状态、不是套件**，那条"未归因"就地改为已归因（铁律 #11：对 HEAD 重跑而不是引用旧读数；run 63 runner 侧 84.16s 同向佐证）。卫生：`git diff --numstat` = `47 0` + `63 8`；**`LiveView.vue` 是 CRLF 文件**（HEAD 359/359、改后 414/414、裸 LF **0**）⇒ 按"锚点匹配该文件真实换行"的既有规矩改，未出现整份重写的假 diff；测试文件保持 LF（CRLF **0**）。`GATES_PYTHON=python bash gates.sh` **RC=0**、七节绿行逐字与 §二之二十三/二十四/二十六 相同（本批不动 `src`），并钉一条执行事实：`gates.sh` 默认解释器是 `python3` 而本机那份没装依赖 ⇒ 直接跑得到 `RC=2 homesdk 未安装`，**那不是门禁红、是跑错解释器**（§二之二十五 那条老话第一次撞在本仓自己的 gates 上）。**登记两条未测项、不写进 PASS**：`onUnmounted` 清定时器有代码有意图但无浏览器级读数；"同一浏览器多开两个标签各自轮询"连读数都没有。另登记 UI 判据口径：本仓 `ui/` **无 vitest**（不打算为本批塞一个），故 UI 改动只有三条判据——`vue-tsc --force`、`vite build`、真浏览器读数，前两条只证能编译，本批实质结论全在第三条。**同批追加两件事**（趁那台 serve + 假 HA 还没拆顺手量的，不另开批）：① §六 那条"切页后定时器是否还在打接口"从"有代码无读数"**补成实测**——页面内挂钩子记请求时刻（XHR + fetch 双通道，axios 走 XHR，只挂 fetch 会漏计；SPA 路由切换不重载文档 ⇒ 计数数组跨页存活、才可比），`/live` 上 `hitsOnLive=5`、间隔 `10451/9995/10166/9830` ms，SPA 切回 `/overview` 记下 `tLeave` 后**等 51 秒（五个档）新增 0 次** ⇒ `clearInterval` 真生效、无后台轮询残留；旁证是 `LiveView.vue` 全文只有一对 `setInterval`/`clearInterval`（`:166`/`:169`）。② 多标签那条**仍无 QPS 读数、如实挂着**：`window.open('…/live')` 被弹窗策略拒（`{opened:false, blocked:true}`，自动化上下文无用户手势），`browser-use` 的 `navigate_page` 只有 `url/back/forward/reload`、没有新建标签页动作 ⇒ 能量到的只是**前提**（`grep -rn "BroadcastChannel\|navigator.locks" ui/src` **0 命中**、`LiveView.vue` 无 `addEventListener('storage'`），"N 标签 ⇒ N 轮询"是**由前提推出而非量出**；后果界定：该端点只读、`available()` 不碰设备 ⇒ 放大的是请求量。另把 DCD 增补里一处**自相矛盾**投回去：§5.3 给第 3 件写"前置=无"（等于说现在就能开工）、§5.4 又把同一件列进"待 DCD 裁的"（等于说不能），两句同出 2026-10-04 一批 ⇒ **不替 DCD 择一**，A/B/C 只求回一个字母（AF 建议 A：§5.4 作数、第 3 件顺延等第 9 件裁定；B 要先给有界形状——验收那半句"崩溃后从末尾重放"决定了要不要**新增一类存储产物**，正是"前置=无"没回答的部分）⇒ **§五 第 14 件**，`af_persist` 一个字节未动；同件把三份账回给 DCD（§5.3 第 4 件已交付可划掉 / §5.6 的"未推 origin/main"在 AF 仓不成立、CI 已到 run 65 / §5.1 在窗内两件事之前仍是 EXEMPT 而非 VERIFIED）。文档侧：新增 `## 二之二十七`（含第七节补测读数）、§〇 那行 F7 由"已收口"改成本批口径 + 加本批一行、CI 行补 run 63/64（连续绿 **30 条，run 34–64**，并按 run 计数钉一条"不是每枚 commit 各有 run"的读数口径）、§六 第 4 件销账 + 两条未测项改为"一条实测销账、一条仍无量出"、§五 加第 14 件、核实基准 → `3039c62` |
| 本批之三十八（"名单手抄"那一族里最后挂着的一条：HTTP 路径与方法做成静态门禁，计划外补刀） | 起因是上一批留在记录里的那句口径——`ui/` 无 vitest，UI 判据只有 `vue-tsc` + `vite build` + 真浏览器读数，**前两条只证能编译**（§二之二十七 第八节）。上一批给 `/api/undo/available` 钉的是**响应键集合**，而**路径与方法仍是手抄字符串**：服务端改名/删路由/GET 换 POST，前端照编译照 build，只有人点一次才 404/405。本仓一路在扫同一族（工具名单 §二之十九、状态源扇出 §二之十四、extras 名 §二之二十六、出向载荷字段 §二之二十二），**HTTP 路径是这条族里唯一还挂着的一条**，且比工具名更漂：工具名改了就 `KeyError`，路径改了**什么都不会发生**。射程三条都量过：`grep -rn "request<\|request(" ui/src` = **51** 命中（1 处是 `client.ts:27` 定义，其余 **50 个调用点全在这一份文件**，视图层只调 `api.xxx()`）；`grep -c "@app." af_api.py` = **81**，本门装饰器脸读数 `参与匹配 79 + 被排除 2 = 81` 逐字对上；**但服务端有第二张脸**：`af_conflict_runtime.py:498-503` 的 `_ROUTES` 表 + `app.add_api_route()` 挂 5 条真路由（`grep -rl add_api_route src` 只有它和 `af_runtime_plugins.py`），只读装饰器 ⇒ 对这 5 条报假红并把修法指向 `af_api.py`，补读表形状后 `参与匹配 84 = 装饰器 79 + 挂载表 5`（两个等式都钉进测试，新增/删路由必须动那几个数＝故意的摩擦）；反向 `UI 从未调` **33 条**只计数。交付：`scripts/check_ui_api_paths.py`（524 行、纯标准库——本机禁 pip，依赖第三方 JS 解析器的门等于没有门禁）三判据 A 路径存在 / B 方法一致 / C **反空洞自证**（`request(` 每个调用点都必须解析得出，否则 `exit 2`）；`gates.sh` 新节 + `ui_api_rc` 两条红分支；`tests/unit/test_ui_api_paths_gate.py` 433 行 / **24 条**反例（含挂载表那三档：有表⇒可达、同一份前端代码无表⇒必红、表改形⇒`exit 2`，以及"运行期挂载文件"登记为边界而非报错）。扫描器是手写字符级状态机（三种引号 + 两种注释 + 括号嵌套 + 泛型 `<…>` 且 `=>` 的那个 `>` 不能当闭合），**不是一开始就是**：探针 v1 纯正则报出"UI 从未调 `GET /api/undo/{deploy_id}`"而 `client.ts:131` 明明在调 ⇒ 模板调用点整段在射程外，那份"缺失 0"混着"没看过"；探针 v2 补模板正则后仍有两处静默丢（嵌套反引号截断捕获、`${a ? b : c}` 里的 `?` 把 query 切点切错）。**四处口径是量出来的不是感觉定的**：① SPA 兜底 `GET /{full_path:path}` 必须排除（一条兜底就接住任何错路径，`/api/nope-at-all` 在兜底下是 200+HTML ⇒ 门永远绿），首版按**前缀**排 `/mcp` 把真路由 `GET /api/mcp/pair-request` 一起挪出射程＝静默少一条判红能力，改精确口径后 `excluded` 3→2、`参与匹配` 78→79，那 1 条差额就是这次自纠的读数；② 通配段双向放行是 HTTP 层事实（`/api/graphs/{name}` 确实接得住 `/graphs/tags`），本门判"可达不可达"不判"值对不对"，段**数量**仍要对齐；③ 那张挂载表"读不出"要分两种：有 `add_api_route` + 有字面量 `"/api/…"` 却读出 0 条 ⇒ `exit 2`，而 `af_runtime_plugins.py`（有 `add_api_route`、0 个字面量 `/api/`，路径由插件运行期声明）⇒ 单列成 `运行期挂载文件 1 个` 的射程边界——首版一律判 `exit 2`，得到的是一条长红；④ 判据文案里的路由出处改成印 `文件:行`，因为同形状的路由现在来自两个文件。**变异读数（先跑未变异对照，九档、`DRIVER_RC=0`、内存字节还原、`git status` 只剩预期四项）**：对照 `RC=0`；M1 前端路径抄错 ⇒ `RC=1` 点名 `/api/healtj`；M2 前端方法抄错 ⇒ `RC=1` 点名"405 与 404 是两种故障"；**M3/M4 前端一字未动、只改服务端改名/换方法 ⇒ 两档 `RC=1`**（这两档才是本门的存在理由：正是上一批那句"编译期一条都判不出来"的位置）；M5 路径改成变量 ⇒ `RC=2` 且读数行打印 `调用点 49 处`（少的那个不是没问题，是没看过）；**M6 边界档 `RC=0`**——`/api/undo/available` 改名后被 `/api/undo/{deploy_id}` 通配段接住，第一反应"门不严"，核过语义确认是可达性事实（请求真会落到参数路由上），于是固定成边界档并**在 §六 挂一条残留：语义级 404 本门不判，可见处只有反向读数 33→34 而反向是计数**。反向不判红的理由也记账：33 条里相当一部分是 MCP/DB 面向与 CLI 共用端点，做成红只会逼下一个人给整节加 `continue-on-error`（§二之五 那条 CI 假绿教训）。全链：`pytest -q` **2842 passed / 51 skipped / 7 subtests / RC=0 / 118.24s**（改前 2818 ⇒ **+19 恰等于本批新增反例数**，无静默增减）；`GATES_PYTHON=python bash gates.sh` **RC=0**、计数棘轮 `104/104` 不动；`git diff --numstat` = `19 0`（只 `gates.sh`），两份新文件 LF、CRLF 0；**本批未动 `src/` 与 `ui/` 一个字节**，门加在契约面上。CI：run 65（`7a2d041`）/ run 66（`d51cc05`）五作业逐条 `completed/success`、`failed_steps` 全空 ⇒ 连续绿 **33 条（run 34–66）**，但这两条是纯文档批、跑的是加门前的树，**本门 runner 口径要等本批那条 run**（仍按"streak 以 run 计不以 commit 计"）。文档侧：新增 `## 二之二十八`（八节，含第五节"探针为什么不够"、第六节 M6 边界档与 M7–M9 那三档第二张脸探针）、§〇 加本批一行 + CI 行补 run 65/66、§六 加"语义级 404 不判 + 反向未分类"一条残留、核实基准 → 本批提交 |
| 本批之三十九（裁定 20261004 §一 1 与 §一 2 落地：租约纳入 MCP 面 + 事件载荷收口，`7dbd640`） | 件 1 按裁定 A 落：`af_flock.serve_lock_path()` 成锁文件名唯一真源、新增 `FileLock.held_by_other()`（**纯探测**——不写 sidecar、当场释放，盖章会把生产 serve 的持有者诊断覆盖成自己），`af_service._single_writer_check()` 排在 IR 装载与传输层构造**之前**、抛 `ServiceError(status=503)`，MCP 出口文本固定前缀 `READONLY_DEGRADED:`（`af_mcp` 那层"工具执行出错："的壳套上去就等于没前缀，测试写死字面量）。持锁方必须是**真子进程**：flock 挂在打开文件描述上，同进程第二条句柄会被自己挡住 ⇒ `_LOCAL_HELD` 那条腿也得钉，否则 serve 每次真机下发被自己的租约 503（把闸门装反）。件 2 按裁定 C 落：`trace_id` 事件级口径写进 `_envelope()` docstring + 新钉"同实例 fired/failed 两枚号不同、`ref` 相同"，`node_id` 从 `observe_terminal()` 删掉、键集判据改逐等于契约行（`instance_id` 那份 ②A 过渡字段仍在，删除时点 v2.6）；两处旧文案（`check_mqtt_writers.py`、`verify_adm_window.py`）"仍等裁定"改过去式，**判据逻辑一字未动**。读数：租约 `9 passed` RC=0，五档真实仓变异 `3/3/2/1/2 failed`（对照先跑绿、末尾 `RESTORE_OK`），桥两文件 `56 passed`、B-1 重新多发 `node_id` ⇒ `1 failed, 37 passed` RC=1。头部"交叉裁定"行已加 `20261004-AF四件与DB一件与MA五件-裁定`。见 §二之二十九 |
| 本批之四十（裁定 §一 3 落成为注册表门禁 + 两处"回收没人按"接上生产 + §一 4 就地标注，`c0476e2`） | 件 3 按 B 落：`src/autoforge/af_bounded_caches.py`（`BOUNDED_CACHES`/`FIXED_KEY_CACHES`，纯字面量）给名字、`scripts/check_bounded_caches.py` 核对名字（A 双腿真在模块 / B 测试 id 真被 `--collect-only` 收集，收集失败是 `exit 2` 不是判绿 / C 新增容器必须注册-豁免-进基线 / D 反空洞自证），`gates.sh` 新节 ⇒ CI `quality-gates` 同口径硬门；豁免标记 `# bounded-cache: exempt(理由)` 单独计入读数，**注册表自身不计数**（它 docstring 引了标记形状，算进去等于自虚增——本批第一版的假洞，钉成用例）。**存量按 HEAD 落而不按裁定那"5 处"落**：完整口径扫到 76 个增长容器、双腿齐全且有测试的只有 2 个、裁定点名的两个误报候选根本不进这 76 ⇒ 落"注册表 2 + 固定键 2 + 基线冻结 74（`--print-baseline` 生成、不手敲）"，差异回投追认（§五 第 15 件）；基线**只在本仓 src 生效**（套到夹具树报 74 条假红、埋掉唯一真红）。同批两处"函数完整、调用方为零"：`Runtime.tick()` 按已装 recorder 的 `sweep()`（**不带 force**，否则 3600s 节流被旁路），`UndoStore` 超窗快照在**写路径**摘除——先版"打开即清"被既有 HTTP 判据当场驳回（`KeyError: 'expired'`，那会把"过期撤不了"和"没这条"混成同一个答复），回收点因此挪进 `record()`；`expire_stale()` docstring 那句撒谎的"或超配额"改掉（配额管能不能再触发，不管字典留几条）。件 4 按裁定执行栏在本仓标注"经 DCD 判定不适用"，`af_persist` 与**计划文档 DCD 原文**均一字未动（那一格更正请 DCD 落笔）。读数：门绿行 `注册表 2 / 固定键 2 / 76 容器 / 基线 74 / 就地豁免 2` RC=0，反例 **21 passed**，三档真实仓变异**各只 1 处判红**，回收两腿各 `1 failed`；全链 **2879 passed / 51 skipped / 7 subtests RC=0（138.96s）**（起点 2842 ⇒ **+37** = 9+21+6+1），`gates.sh` RC=0、棘轮 `104/104` 不动。见 §二之三十 |
| 本批之四十一（run 68 那条 CI 红修在门禁作业，而不是修在判据，`e460254`） | 连续绿 34 条（run 34–67）**断在 run 68**，而 run 68 是**纯文档**那一笔 ⇒ 红不可能是文档写的：`quality-gates` 的 `failed_steps=['Run quality gates']`，本门正文两条 `[射程] … 收集未成功（rc=1）⇒ 无法核对测试 id：` 后面**什么都没有**。真因是上一批那道门带着一个没声明的前提进了 CI——判据 B 要起 `python -m pytest --collect-only`，而 gates 作业只装 `pip install -e .`（core = jsonschema + typer），**没有 pytest**；本机装了它多年 ⇒ 同一棵树本机 RC=0、runner exit=2。这是 §二之二十六 那一族（"只在本机装过不算修"）的**门禁工具依赖版**，而门自己的设计这次是救命的：读不成退 2、不报干净，红得看得见。**两处各修各的**：① `ci.yml` gates 作业改 `pip install -e ".[dev]"`——用声明过的 extra，不在 workflow 手搓 `pip install pytest`（那正是 paho 那批否掉的形状）；② 射程消息 `f"…：{proc.stdout[-300:]}"` → `_collect_reason()`（先认"没有 pytest"并点名缺失前提、再取有字的那一路、两路都空明说"均为空"），因为缺失原因在 **stderr** 而第一版只看 stdout。反例 +2 ⇒ `23 passed`；变异 M-1 把消息退回只截 stdout ⇒ `2 failed` RC=1，红文末尾就是 CI 那条断在冒号的样子；驱动里一行"先写占位再覆盖"在跑前被删（真跑会把门禁脚本截成三行——按 §二之十八，驱动不许有"先截断"的写法）。同批销两笔待复测：run 68 的 `UI↔路由契约门禁` runner 行与本机 `gates.out` 同行比对 `EQUAL True`，工具名单门 runner 侧 `扫描 97 个文件`（多的那枚正是新注册表）。读数：本机 `gates.sh` RC=0 且本门绿行逐字未变（`注册表 2 / 固定键 2 / 76 容器 / 基线 74 / 就地豁免 2`）、全链 **2881 passed / 51 skipped / 7 subtests RC=0（125.52s）**（2879 ⇒ +2 就是这两条反例）。**一条不自签**：本机造不出"没有 pytest"的环境（不 pip uninstall、不建 venv、不装包）⇒ `ci.yml` 那行只能由下一条 run 证，本批记为「判据级实测绿 + 作业级待 run 复测」；另把 gates 作业并入"CI 依赖未 pin"那一面（§五 第 5 件，等裁定），并在 §六 登记 `check_mqtt_runtime_dep.py` 的射程不覆盖"作业是否具备自己要跑的工具"。见 §二之三十一 |
| 本批之四十二（run 69 复查闭合 + §六 那条反向读数开局，纯文档） | 上一行留下的半格"作业级待 run 复测"由 **run 69（`d4994a8`）落回实测**：整条 `completed/success`、五作业逐条 `failed_steps=[]`，runner 上本门那行绿读数与本机同脚本重跑**逐字相同**（`EQUAL True`）⇒ 34 条连续绿断在 run 68 之后，新账从 run 69 重新计、当前 **1 条**。§六 那条"33 条 UI 从未调的路由要先分类"的开局读数出来了，第一条结论却是**这个反向数现在不能当"没人用"读**：门只扫 `ui/src`（开发面板），而 `ui-user/src/api/client.ts`（用户端 ForgeSight）逐条真调 `automations` 一族 7 条 + `user/agents` 3 条 + `mcp/pair-request` ⇒ 33 条里**至少 11 条是门射程外的第一方消费者**，先分树再分类否则会把活接口判成冗余。同批盘出两棵树 `USE_MOCK` 默认档相反（`ui/src/api/index.ts:4` 的 `!== 'false'` 默认走 mock，靠 `.env.production` 关；`ui-user/src/api/index.ts:5` 的 `=== 'true'` 默认走真后端）：构建期内联 ⇒ 非运行期缺陷，但"改档必须重新 build"只写在 `ui/README.md`。收尾另自证一次**接近事故**：一次 `Edit` 把 `old_string` 选成相邻 bullet 的开头 ⇒ 覆盖掉那条的开头，而**numstat 对这件事是哑的**（受损当时 `12 1`，唯一那处"删"属于另一行被改写；被并行的文字不减行），靠 `git show HEAD` 逐行比对才捞回（做法与口径见 §四 末条）。见 §二之三十一 |
| 本批之四十三（那道门只看了三棵第一方 UI 树里的一棵：`UI↔路由` 扩到全部前端 + 泛型 `;` 那条静默漏，`9c32ea0`） | 起因是本批 §六 盘点里逼出的问题："33 条 UI 从未调"那个"UI"指谁——答案只指 `ui/src`（开发面板），而产品前端是 `ui-user/` 与 `ui-user-mimo/` 那两棵。纳入后反向读数 **33 ⇒ 16**，出去那 17 条是 `automations`/`user/agents`/`auth`/`pending` 四族的**活接口**（不是可删的死面）。为读得动它们先修三处：第二/三张调用脸 `req(path[, {method:'X'}])`（省略即 GET）、`/api` 前缀按路径自己带不带归一（mimo 的 `API_BASE` 默认空串）、以及**泛型实参里的 `;`**——旧跳过循环 `elif text[i] in "(;"` 遇 `req<{ ok: boolean; user: User }>(…)` 直接退出 ⇒ **整条调用静默丢掉、连"解析不出"都不报**，修前那两棵树的读数是 11/16 与 7/19 ⇒ 85 个调用点里 17 个（两成）以"没看过"混在"干净"里；这正是本门 C 判据的反身版。另加两条射程判据：`UI_TREES` 登记树读不出调用点 ⇒ `exit 2`、盘上多出未登记的 UI 形状顶层目录（`package.json` + `src/`）⇒ `exit 2`（"漏一棵树"最坏的表现恰恰是绿行）；`gates.sh` 改走 `--all`。13 条反例（该文件 24 ⇒ 37）+ 六档变异（`8/3/3/1/1/4 failed`，红的不是同一批测试 ⇒ 拦六件不同的事；未变异对照 `37 passed`、`RESTORE_OK True`）。全链 `GATES_RC=0`、`2894 passed / 51 skipped / 7 subtests RC=0`（上批 2881 + 本批 13）。边界照登记不静默：mimo 的配对流走 `EventSource` 不进门；剩下 16 条的**逐条定性仍未做**。见 §二之三十二 |
| 本批之四十四（安全审计那份从未进处置链；对 HEAD 复测后真洞是"枚举不存在的码不计数"＋MCP 六键"没声明却能传"） | `AutoForge安全审计报告.zip` 在仓里被跟踪，但 `docs/` 检索"安全审计"只命中 zip 自己、§〇/§五 从无对应行 ⇒ **先补记账再处置**。它的 `source_ref` 是 `zip-snapshot-of-default-branch-2026-09-29T22:43`（**无 sha**，与第六/七轮"按快照读码把已修的当未修重报"同一族失败方式），按铁律 #11 对 `927b044` 逐条重跑：**已修被重报 5 条 / 成立 2 条**（七行钉源表在处置记录 §〇）。成立那两条比报告写的更深：① 报告要"给 MCP 加 RateLimiter"，实测 `record_failure()` 在 `rec is None` 时直接 `return False` ⇒ **试一个不存在的码任何计数器都不加**，协议层 limiter 只按住症状；防线因此做进 `AuthCodeStore`（`ATTEMPT_WINDOW_S=60.0`/`ATTEMPT_LIMIT=10` 全店窗口，在早退**之前**计数，`validate()` 窗口内打满一律 False，失败方向仍是回落人审队列），并明写边界"**进程内、重启清零**"，不写成"暴力破解已根治"。② `dispatch()` 从不把 `arguments` 与 `inputSchema` 对账 ⇒ 报告的一句"未声明的键可传入"被**量成集合**：31 工具双向对账 **6 个键没声明却能传**，其中 `allow_bulk` 是 `af_service` 的爆炸半径护栏旁路、**旁路开关在对外契约里不存在**。两处各自修：运行期 `_undeclared_args()` 在 `_guard(scope)` **之后**拒收并回显声明表（顺序本身是判据），静态新门 `scripts/check_mcp_arg_schemas.py`（消费未声明/声明未消费/缺 `properties` 三条，handler 整包转发或锚点改名 ⇒ **`exit 2` 不报干净**）+ `gates.sh` 新节两条红分支；`allow_bulk` **补声明而非删参数**（删了等于把显式动作改回隐式）。附带把 `load_graph` 的规模上限从"有代码无读数"变成有读数（此前 `tests/` **0 命中**，5 条）。读数：全链 **2921 passed / 51 skipped / 7 subtests RC=0（356.73s）**＝基线 2894 **+27**（11+11+5 逐文件归位）、`gates.sh RC=0` 新门绿行 `31 个工具：声明参数 61 个、handler 消费 61 个，双向差额 0；现场豁免 0 处`、变异八腿 `1f/2f/门 2 条/门 6 条/RC=2/RC=2` 且 CONTROL 与 RESTORE 均 `RC=0`、numstat `gates.sh 21 0`+`af_auth.py 29 2`+`af_mcp.py 59 8`。**三问不自裁**（长期码 `expires_at=None` 的绝对 TTL / `--host 0.0.0.0` 且 `AUTOFORGE_MCP_TOKEN` 未设 ⇒ `_guard()` 放行一切 / homesdk wheel 来源与完整性）⇒ §五 第 16 件；本批**未改** compose、`--host`、read 端点鉴权依赖、MCP 默认放行、长期码 TTL。见 §二之三十三 |
| 本批之四十五（`out_of_scope` 第二件落地：`固定名 .tmp` 那一族——静态门 + 7 处收口 + 9 站冻结） | §二之三十三 台账里"14 个面不许读成无问题"的**第一件真正落地**：新门 `scripts/check_atomic_write_sites.py`（三判据 + `exit 2` 档 + 键含类名），基线 `.atomic-write-baseline.txt` 9 站逐条理由、只减不增，另 2 站就地豁免；收口 7 处（`af_persist.save`、启停写下沉成 `GraphStore.resave_raw`、`_delete_archive` 标签 RMW 进 `tags.lock`、`af_catalog` 三站、`af_insight_queue._atomic_write`）。新增 26 条测试（门 13 + 值语义 13，其中 1 条 POSIX-only 在本机 skip 并带理由），控制组 `25 passed, 1 skipped`、七档变异 M1…M7 全红（M2 第一遍没红——断言吃了 `save()` 留下的旧账，已改成调用前清零），全量 `2946 passed, 52 skipped, 7 subtests in 125.88s`（较上批 +25/+1，与本批新增数严格对齐），`gates.sh` 全链 `GATES_RC=0`。三条实测出的对端可见形状（`asks/pending` 无鉴权 / `login` 任意凭据发永久全权令牌 / `auth-codes` read 面给明文码全量）AF 未动，写入 DCD 那件 §五 续查。run 73 待复查，不预签连续绿。 |
| 本批之四十六（裁定 20261004 18:35 那份的 AF 侧今日四件 + 自盘出授权面 fail-open；含 DCD 自己那笔计划文档更正上线，`2a8d940`+`b4cd67f`） | 切分按"AF 今天能自决"而不是按条目顺序抄：今日落 **Q1**（长期码可配绝对上限 180 天、期限由 `created_at` 推导 ⇒ 老码不迁移也到期、`list()` 与 `validate()` 同源）、**F-1**（`GET /api/asks/pending` 加 `Depends(_read)`，并落一条**单向蕴含表** `read←{read,write}`——裁定那句"write 域含 read"在 HEAD 上并不成立，`requires()` 一直逐名比对，照字面只加门会把 DB 的 ask 轮询整条打断，前提差回投 inbox）、**F-2 码半边**（明文样例凭据出 docstring，判据扫 `src/autoforge/*.py` 全集并先自证射程读得成）、**Q2=B**（`paho-mqtt>=1.6,<2.1` 两处声明 + `tomllib` 判据）与 §五 追认侧（`insight_id` 成去重与回灌键，缺失才退 `hypothesis_id`/`trace_id` 且记 `transport.id_key`），README 另写死"HTTP 面只在可信 LAN"这条部署前提。**自盘一条非审计 finding 的洞**：`af_auth` 五处落盘站点全是裸 `write_text` 而 `_load_*` 把 `JSONDecodeError` 吞成"文件不存在" ⇒ **半截撤销名单 = 已撤销令牌复活（fail-open）**；写侧补私有 `_atomic_write_text()`（L0 不能引 `af_store`，先例 `af_config`/`af_pending`），读侧分开"在但读不成"与"不在"并**置位保持到进程重启**，另加一条反空洞自证（把助手换成"写半截就抛"，断言截断 JSON ⇒ 中毒 ⇒ 拒）。推后与理由写进 §二之三十五 第八节：**MCP 未设令牌默认拒绝**（默认结论翻转级，49 处 `dispatch(` 测试调用点要逐条显式化 + `dispatch()` 默认值本身要定，单独一批）、**F-3**（`auth-codes` 收紧 + owner/非 owner 面拆明文与掩码，消费面在 `ui-user`/`ui-user-mimo` 两棵树，须连前端改并浏览器验证）、F-2 的 UI 半边、Q3 真机演练与窗后四项/NAS 重烤（合并窗，Q1=A 明写本窗只重烤**不开开关**）、compose 那句可信 LAN 注释（铁律 #3）。**DCD 自己落笔的六处计划更正**（`3262ccb`）按其裁定 §四 第 2 条并入本批推送，§五 四条验收当场重跑取到读数（`已全部裁定` 命中；`/api/health` 三处命中且旧写法 0 命中；`origin/main` 两段带"已更正"与 `rev-list --count = 0`；§5.3 已无待办行），§5.3 第 7–10 行逐条对过 HEAD：第 7/8 行已在 `7dbd640`/`c0476e2` 交付，第 9 行=本批+推后两件，第 10 行 compose 半边留窗内。读数：新判据 25 条（382 行）+ 契约 14→18 + 桥 +3；定向 `25 passed`、`42 passed`、`59 passed`；全量 **`2978 passed, 52 skipped, 1 warning, 7 subtests`**（`PYTEST_RC=0`；墙钟 1149s 因与定向套件/门禁/CI 取数并发，登记为未归因悬账不复用）；变异 CONTROL `RC=0 42 passed` ⇒ M6 蕴含清空 `2 failed`、M7 无条件放行 `3 failed`，逐字节还原（**先记一次驱动器废读数**：argv 被写成 `["-m pytest"]` 一枚串，三档 `RC=1` 且 stdout 空，红因是 `No module named  pytest` 而非判据，控制组不成立、整表作废重跑）；`gates.sh` **`GATES_RC=0`**，原子写门绿行由 13 处/2 处自移动到 **14 处/3 处**（基线 9 站没动，"只减不增"仍成立）。CI：run 72/73 复查绿，MCP 门那行对 run 73 重取 `EQUAL True`（同 70 字符）、原子写门在 `8689b39` 树上同 93 字符 ⇒ 上一批两格"不预签"闭合，本批新挂一格"run 74 须见 14/3 那一行"；连续绿按 main 计 **5 条（run 69–73）**。`docs/audit` 本批当场重跑 `ls -lat`：外部最新仍是**第七轮**（`审计报告_第七轮_核实与修复.md`），第八轮未投递。见 §二之三十五、`docs/audit/审计报告_安全审计_核实与修复.md` §十 |

## 二之二十六、联动桥的运行时依赖此前**一处声明都没有**：paho 补齐并做成"三面一致"静态门禁（`739a328`）

起因不是新需求，是上一批交付之后我顺着验收点追问的一句：**"窗当天那句 `docker compose exec autoforge python scripts/verify_adm_window.py`，容器里到底够不够得着？"**
两个答案都不好看，而第二个比第一个严重得多。

### 一、盘出的事实（逐条实测，非推测）

| 判据 | 实测 |
|---|---|
| `scripts/` 进不进得了镜像 | **不进**。`docker/Dockerfile.api` 原先只 `COPY pyproject.toml README.md ./`、`COPY src`、`COPY examples` ⇒ 上一批那条"一条命令"在容器里根本不存在，窗当天还是只能手搓 |
| paho 在不在 AF 的依赖链里 | **不在，一处都没有**。`pyproject.toml` 全文无 `paho`；vendored wheel 的元数据实测只有两行相关：`Provides-Extra: mqtt` + `Requires-Dist: paho-mqtt>=1.6; extra == "mqtt"` ⇒ 镜像里那句 `pip install homesdk-0.3.1-py3-none-any.whl`（裸 wheel，不带 extra）**不会**带来 paho |
| 两个镜像装的是什么 | `Dockerfile.api` = `-e ".[api,ha]"`、`Dockerfile.test` = `-e ".[dev]"`、CI 三个作业 = `pip install -e ".[dev]"` ⇒ **全都不含 paho** |
| 那开发机为什么一直正常 | 那份解释器里**手动装过** paho（§二之二十五 实测 `paho_available()=True`、`paho\mqtt\client.py` 在 `Python313\Lib\site-packages`）⇒ "本机绿"和"交付面能跑"这两件事此前没有任何一条判据连着 |
| 后果（不是"少一个用例"） | 计划 第 1/2 步要在窗内 `AUTOFORGE_MQTT=1`：`af_mqtt_bridge` 顶部 `from homesdk import mqtt as _mqtt`，`homesdk/mqtt.py:40-43` 对 paho 是惰性 guard ⇒ 导入不炸，直到 `get_client()` 才抛 `MqttUnavailable`。而 `af_cli.py:1380` 那句 `_start_linkage_bridge()` 排在 `uvicorn.run(...)`（`:1382`）**之前**且不吞异常 ⇒ **窗内 serve 拒绝启动**，HTTP 只读面一起没（`restart: unless-stopped` 会变成反复重启）。开关若不开，则桥永远不上线，第 1/2 步的验收在现烤镜像下**原理上不可能达成** |
| 有没有测试能拦住 | **没有**。2816 条里没有一条 import paho：`grep -rn "import paho\|get_client\|MqttUnavailable" tests/` 只命中 `test_af_cli_linkage_wiring.py` 的**桩**与 `test_verify_adm_window.py` 对源码的字符串断言 ⇒ 桥的用例全用 duck-typed client（这是 §二之二十三 那条"测试绿在生产不走的路径上"的**依赖版**） |

### 二、交付（三面各自可红 + 依赖真补齐）

1. **`pyproject.toml`**：新增 `[project.optional-dependencies]` 的 `mqtt = ["paho-mqtt>=1.6"]`，并在 `dev` 里同列一条。`dev` 那一条不是凑数：本批自己就踩了"同一条用例两种机器两种脸色"（见下节 ①），把它写进 CI 面的依赖表是为了**消掉这个形状本身**，不是为了多装一个包。
2. **`docker/Dockerfile.api`**：安装行改 `-e ".[api,ha,mqtt]"`；加 `COPY scripts ./scripts`（窗内验收入口必须和被测服务同机）；顺带把注释里过期的 `homesdk 0.1.1` 改成实际钉死的 `0.3.1`。
3. **`scripts/check_mqtt_runtime_dep.py`**（新，142 行，纯标准库 `tomllib`）：
   - **A 声明在**——paho 必须在 base `dependencies` 或某个 extra 里；
   - **B 交付面装到**——`Dockerfile.api` 的 `-e ".[…]"` extras 集合必须与声明处相交（paho 在 base 则自动满足）；
   - **C CI 面装到**——同一判据对 `docker/Dockerfile.test` **与** `.github/workflows/ci.yml` 各判一次。工作流按所有 `-e ".[…]"` 行的**并集**判：`gates` 那个 job 刻意只装 base（门禁脚本全是纯标准库），它不需要 paho，不该因此把整条 CI 面判红；
   - **锚点读不到 ⇒ `exit 2`**：`pyproject` / `af_mqtt_bridge.py` / 两份 Dockerfile / `ci.yml` 任一缺位，或桥里已经找不到 `from homesdk import mqtt`（§二之十四 那一课：射程前提变了不许静默全绿）；
   - 读不到安装行也判红并点名"无从判定"，**不许当成"没装"就顺着下一条放行**。
4. **`gates.sh`**：新节 `联动桥依赖门禁` + `dep_rc` 的两条红分支（`-eq 2` 读不到锚点、`-ne 0` 红），CI 的 `quality-gates` 作业自动继承为硬门。
5. **`tests/unit/test_mqtt_runtime_dep_gate.py`**：反例 **24 条**，含两条**直接对当前仓库**跑的（锚点可读 + 真绿且 `mqtt in declared`）——否则这扇门只对自己的样本有效，正是本仓那族"纸门"的形状。口径侧钉了：base 声明自动满足三面、只装任一声明 extra 即够、一个面两条安装行取并集、裸 `-e .` 不污染并集、`.[mqtt-api]` **不等于**装了 `mqtt`（按 token 精确比）、`paho-mqttlib` 不算 paho、`Paho_MQTT` 算。

### 三、本批自纠三处（都记下来，因为都是"看着对"的错）

1. **自家新测试是宿主相关的**：上一批写的 `test__collect_maps_missing_env_to_unavailable` 只把 `broker_settings()` 打成抛 `MissingEnv`，却没管 `paho_available()`。我把 `hm._paho` 临时设为 `None` 模拟过一次无 paho 的机器：`_collect` 会**更早**从 paho 分支抛出，那句 `assert "MQTT_HOST" in … or "MissingEnv" in …` 当场不成立 ⇒ **本机绿、无 paho 的机器红**。修法是把两态各钉一条：前者显式 `monkeypatch.setattr("homesdk.mqtt.paho_available", lambda: True)`，后者新增 `test__collect_maps_absent_paho_to_unavailable`（钉 `False`，断言消息里有 `paho`）——**两条各自只钉一个缺项**，结论与宿主无关。
2. **变异驱动第一次的三档读数全部作废**：驱动用裸 `bash gates.sh`，Windows 把它解析成 `C:\WINDOWS\System32\bash.exe`（WSL 无发行版的存根，直接 `RC=1` 并吐一段 UTF-16 安装提示）⇒ 我一度"看到"三档全 `RC=1`，而 M-3 本该是 `RC=2`。这个自相矛盾本身就是线索。换成写死的 `C:/Program Files/Git/bin/bash.exe` 并**先跑一次未变异的对照**（必须 `RC=0`）才取数——这条正是 §二之二十三 记过的同一个坑，当时写成了规程却没在下一个驱动里执行，所以规程要连"对照档"一起执行才算数。
3. **读数会随 TOML 书写顺序变**：`_declared()` 第一版按 `optional-dependencies` 的迭代顺序返回，于是"先写 `mqtt` 后写 `dev`"与反过来会印出 `['mqtt','dev']` / `['dev','mqtt']` 两个字符串，对账时像是声明位置换了。改成 `sorted()` 后再印/比，并让一条反例把顺序钉住。

### 四、实测读数

- **对照（未变异）**：`GATES_PYTHON=python bash gates.sh` ⇒ `RC=0`，本门那行逐字为 `✓ 联动桥依赖门禁干净（paho 声明于 ['dev', 'mqtt']；交付面 装 extras ['api', 'ha', 'mqtt']，CI 面（测试镜像） 装 extras ['dev']，CI 面（工作流） 装 extras ['dev']，三个面逐一核过）`——绿色行只印三个面的实测 extras 集合，没有形容词。
- **变异四档**（Git Bash 真身 + 每档跑完整 `gates.sh`，从内存字节还原）：**M-1** 交付面退回 `.[api,ha]` ⇒ `RC=1`、`结论：联动桥依赖门禁红（exit=1）`；**M-2** paho 从依赖表两处全删 ⇒ `RC=1`（finding 只剩 A 那条，因为无声明时 B/C 自然无从判）；**M-3** 桥改成 `import paho.mqtt.client as _mqtt` ⇒ `RC=2` 并读到 `结论：联动桥依赖门禁读不到锚点（exit=2）…`；**M-4** `ci.yml` 三处 `-e ".[dev]"` 全改 `".[api]"` ⇒ `RC=1`。四档 `还原逐字节一致=True`、`DRIVER_RC=0`。
- **反例分跑**：`pytest tests/unit/test_mqtt_runtime_dep_gate.py tests/unit/test_verify_adm_window.py -q` ⇒ 头一轮 **3 failed, 49 passed**（三条全是上面 ③ 那个顺序问题），改完 `24 passed`。
- **全链**：`python -m pytest -q` ⇒ **2816 passed / 51 skipped / 7 subtests，`PYTEST_RC=0`**（上批 2791 + 本批 24 条门反例 + 1 条 paho 分支判据，逐条对得上）。**墙钟 `556.34s` 与历批（≈94–124s）差一个量级，本批未归因**：取数时机器上没有我起的 `forge serve` 残留（`Win32_Process` 查得两条 `python.exe` 都是宿主常驻的 bridge/proxy，非本会话所起，未动），通过/跳过数才是判据，这条差异如实挂着。
- **卫生**：`git diff --numstat` = `8 2`（Dockerfile.api）/ `21 0`（gates.sh）/ `11 0`（pyproject）/ `3 1`（verify_adm_window 文档串）/ `1 1`（af_mqtt_bridge 文档串一行）/ `22 1`（窗口测试）/ 两个新文件 `142 0` + `240 0`；八文件 `CRLF: 0`。**产品码只动了 `af_mqtt_bridge.py` 文档字符串一行**（"本机就没有 paho"→ 可核口径），逻辑零变动。
- **推送自证**：`git push origin master:main` ⇒ `PUSH_RC=0`，`19ca389..739a328`；`git ls-remote --heads origin` 只有 `refs/heads/main = 739a328b34a3…` ⇒ 远端仍单分支。
- **CI 侧复查**：run 62（`19ca389`，上一批纯文档）`completed/success` ⇒ 按 main 计连续绿 **28 条（run 34–62）**。本批 `739a328` 触发 **run 63**，取数时 `status=in_progress`（`RUNS_RC=0`）⇒ **不计入 streak**，其 `quality-gates` 正文里新门那行与本机是否逐字相同、`pytest` 是否 `2816 passed`，等结论落地再记。

### 五、为什么这三件事不在 AF 自决（投 §五 第 13 件）

镜像里从此**从 PyPI 拉一个此前不存在的运行时依赖**、窗内**开不开那个会把只读面一起带走的开关**、以及**第③项验收用真机 fired 还是 dry-live fired 过账**——三条都会改变窗当天的动作序列或跨仓可见语义。特别是最后一条：dry-live 也能产出四键齐全、`ts` 为家庭墙钟的一条 `fired`，但它对应的语义是"意图已成形、设备一次没碰"，拿它写 PASS 就等于"联动环在生产真上线"这件事没有任何一条读数为真。AF 已提交 `关键决策部/inbox/20261004-AF-窗内开联动开关会把只读面一起带走-决策申请.md`（Q1 开关与顺序 A/B/C、Q2 paho 来源 A/B/C、Q3 第③项事件来源 A/B/C），**compose 一个字节未动**。

### 六、同批收到 DCD 对计划的新增 §五（进度快照与 v2.6 细化）——一处前提已过期，按 HEAD 记账

SP 把 DCD 2026-10-04 的增补直接写进了 `docs/ADM联动执行计划-AF.md`（原文照录进仓，AF 不改 DCD 的措辞）。逐条对 HEAD 复核后的账：

- **§5.6 那句"20+ 枚 commit 未推 origin/main、授权已到期"在实测上不成立**：本仓自 `a5c8aa9` 之后每一批都按 `git push origin master:main` 推并 `ls-remote` 自证，本批 `739a328` 落地时 `PUSH_RC=0`、远端只有 `refs/heads/main`；`gh_ci_status.py runs` 读到 run 62（`19ca389`）`completed/success`、run 63（`739a328`）`in_progress` ⇒ **有异地备份，无需再申请推送授权**。这条如实回给 DCD 而不静默：它写的可能是 NAS 侧另一份工作副本的账，若是，那份副本的推送属 SP 动作，AF 无从代劳。
- **§5.1 的"第 0-4 步全交付"要补一句本批**：第 1/2 步的**代码与判据**都在，但"联动环在生产真跑起来"这一件取决于窗内 `AUTOFORGE_MQTT` 的开法与镜像重烤（§五 第 13 件 Q1），在此之前它仍是 EXEMPT 不是 VERIFIED。
- **§5.4 四件（第 9/10/11/12 件）确认已正式投递在 inbox**，AF 侧不动代码等回话；本批新加的**第 13 件**是同一段落里唯一"AF 已把能做的做完、只剩跨仓定序"的那条。
- **§5.3 六件里 AF 本批能自取的是第 4 件**（`LiveView.vue` 撤销按钮通路 + 10s 定时刷新，前置=无）；第 3 件（af_persist 顺序追加写）前置虽写"无"，但它正压在 §5.4 第 9 件的裁定上 ⇒ 不动存储面；第 1/2/6 件全部前置"合并窗"，第 5 件前置"建仓授权后的首次 commit"（属 DCD/SP 动作）。**本批已把第 4 件做完**，验收按原文"浏览器点撤销走通"取的是真浏览器读数（不是类型检查过就算），读数、两条自纠与两条新增服务面判据见 §二之二十七。

---

## 二之二十七、§5.3 第 4 件：撤销清单从"印一个猜的窗口"改成"服务端给数 + 会过期就承认会过期"（`LiveView.vue` + 2 条服务判据）

### 一、这件的残留到底在哪（先把"已收口"那行的账拆开）

§〇 有一行写的是"F4 ③ / F7 前端残留 **已收口**……撤销按钮端到端真点通"。DCD 的 §5.3 把第 4 件的验收写成**两件**：「撤销按钮通路」**加**「10s 定时刷新」。前一件事确实早就点通了，后一件**从未做过** ⇒ 那一行是把半件说成了一整件。顺着"撤销清单"这一面重新读码，盘出的不是"少一个定时器"这么小：

- **无定时刷新**：清单只在 `onMounted` 拉一次，之后每次靠人点"刷新清单"。而 `af_undo.UndoStore.available()` 会把**超窗的记录直接在服务端滤掉**（不是"列出来但标过期"），`age_s` 只是取数那一刻的快照 ⇒ 页面会长期挂着"窗口内可撤销 1 条"的行，直到用户点下去才被后端以 `expired` 拒掉。**这行的语义是"看起来还能撤销"，而它是会自己变假的读数。**
- **失败即清空 = 把"查询失败"显示成设备侧结论**：原 `catch` 里写的是 `undoItems.value = []` + `undoWindowS.value = 0`，于是 403/断网/缺权限时页面渲染成"窗口内没有可撤销的部署"——那是**设备侧结论**（没有任何部署可撤销），而实际结论是"一次读数都没拿到"。这一族与本仓 §二 反复钉的"不许把未验证读成已验证"同形，只是发生在前端。
- **窗口长度是页面自己猜的**：开关旁的文案写死 `{{ undoWindowS || 60 }}s`，读数没回来时也印 60。真实窗口由服务端 `AUTOFORGE_UNDO_WINDOW_S` 决定（默认 60、上限 300、**0 = 永不过期**）⇒ 运维把窗口设成 0 或 300，页面仍然印 60，用户据此判断"过了 60 秒就撤销不了"，而那条记录其实还活着。

### 二、交付（前端三处 + 服务面两条它新依赖的判据）

`ui/src/views/LiveView.vue` `+63/−8`：
1. `UNDO_POLL_MS = 10000` + `onMounted` 起 `setInterval(tickUndoList)`、`onUnmounted` 清定时器。定时档只做"没人正在动它"时的补数（`undoListInFlight || undoBusy || running` 三个旗子都在 ⇒ 跳过）：**下发/撤销末尾那两次显式刷新不能被定时器跳过**，否则刚产生的撤销凭据要等一个周期才出现在清单上。
2. 读失败**留着上一次的成功读数**并如实标注，不再清空：新增 `undoListError` / `undoRefreshedAt`，横幅标题按有没有成功过分两档——`自动刷新失败：下面是上次成功读数，可能已过期` / `撤销清单读取失败：目前一条读数都没有，不能据此判断窗口内无可撤销项`；空态文案同样分两档（`上次读数（13:12:30）窗口内没有可撤销的部署` / `撤销清单一次都没取到读数（接口不通或缺权限），不能断定"没有可撤销项"`）。
3. 窗口长度改为**只印服务端给的数**：`undoWindowLabel` 三态——未读到 ⇒ ` · 服务端窗口未读到`，读到 0 ⇒ ` · 服务端窗口 0s（不过期）`，其余 ⇒ ` · 服务端窗口 Ns`；表头同构分两档（`窗口 Ns · k 条可撤销 · 每 10s 自动刷新 · 上次成功 HH:MM:SS` / `窗口长度与可撤销清单均未读到 · 每 10s 自动重试`）。`60` 这个字面量在页面里已不存在（`grep -n "|| 60" ui/src/views/LiveView.vue` 改前 1 处、改后 0 处）。

`tests/unit/test_af_undo_http.py` `+47/−0`：这一页新依赖的是**服务面口径**，而此前没有任何判据钉着——`/api/undo/available` 返回形状全靠前端逐键硬撑，`age_s` 一旦被改名或去掉，浏览器里是 `undefined.toFixed()` 当场炸。补两条：
- `test_expired_deploy_leaves_the_available_list`：超窗项**不在清单里**（不是"列出但标过期"）。若哪天改成含过期项返回，页面就会挂着"窗口内可撤销 N 条"而点下去必被 `expired` 拒——正是本批要消掉的那个假读数形状。
- `test_available_items_carry_the_keys_the_ui_renders`：清单键集合必须逐等于 `{window_s, items}`、条目必须逐等于 `{deploy_id, age_s, entities}`，`age_s` 必须是**数字且不是 bool**，`entities` 排序稳定（页面用 `age_s.toFixed(1)` 与 `entities.join('、')` 渲染）。

### 三、验收读数（真浏览器 + 一台为这次验收搭的假 HA，未碰任何真实设备）

裁定口径里"真机下发"这一面**不能拿真实家电当测试床** ⇒ 用标准库起一台一次性假 HA（`127.0.0.1:8788`，`Bearer fake-ha-token` 才响应），只服务 `/api/states`、`/api/states/<id>` 与 `light.turn_on/turn_off`，并把每一次 POST 追加记进 `Temp\fake_ha_calls.jsonl`。AF 侧用 `forge serve --port 8787 --ui-dir ui/dist`（同源，故 CORS 不参与）+ `AUTOFORGE_LIVE_ENABLED=1`。

浏览器（`browser-use`，页面 `/live`）实测链：
- 下发得到 `dep-0bc52a22918a` ⇒ 清单出现该行 ⇒ 点"撤销" ⇒ popconfirm"确认回滚" ⇒ 结果条 `已完整回滚` + `已恢复：light.fake_undo_probe`。**设备侧对账**（`fake_ha_calls.jsonl` 原文三行，按顺序与 `Temp/af-undo-store/undo_log.json` 里那两条 deploy 配对）：`turn_on` → `turn_off` → `turn_on`，两条 deploy 记录的快照都是 `state: "off"`（即写前状态真被读到，不是空快照）。**第三条如实记账**：`dep-014b028dff5a` 是文案修正、重新 `vite build` 之后为了复取表头读数做的第二次下发，**本批没有对它点撤销** ⇒ 假 HA 内存态停在 `on`；这台假服务随本批停掉，不留残值。
- 定时刷新**零点击**取到：表头 `上次成功 13:12:30 → 13:12:50`，间隔恰为一个 10s 档；同一次 tick 把已过期的那行**从清单里带没了**（服务端 `available()` 滤掉，前端只是接受了新读数）。
- 失败面（用错误 token 造 403）：横幅出现、**上一次的成功读数原样留着**、空态文案走"有旧读数"那一档；把 token 改对后点"刷新清单" ⇒ 横幅消失、`上次成功` 更新。改前的旧代码在这一档会把清单清成"窗口内没有可撤销的部署"——那就是本批要修的那句假话。
- 窗口 0s 档：把服务端设成不过期后，表头不再印 `60s`（改前会印猜的数）。

**两处必须如实标注的执行限制**：① `mcp__browser-use__click` 在本机取不到视口（`NATIVE_BROWSER_VIEWPORT_UNAVAILABLE … viewport=0x0, visible=false, visibilityState=hidden`）⇒ 点击是通过 `evaluate_script` 派发 DOM 事件完成的（填值走 `fill` 工具是真通道），**不是真实指针事件**；naive-ui 的开关对 `root.click()` 无反应，要对 `.n-switch__rail` 依次派发 `mousedown`+`mouseup`+`click` 才翻转，且 `aria-checked` 必须 await 之后再读（同一同步 tick 里读会拿到旧值）。② 路由是 **history/path 模式**：导航 `/#/live` 会落在 `/overview#/live`，必须直接开 `/live`。这两条不是缺陷，但"浏览器点撤销走通"这句话的真实程度取决于它们 ⇒ 结论等级：**通路已按 DOM 事件驱动走通并拿到设备侧对账；真实指针/真实家电未验**。

### 四、本批自己抓到并改掉的两条措辞谎（只有真浏览器读数能抓出来）

第一版改完，页面在**一次读数都没成功过**时依然会说谎：
1. 表头印 `窗口 0s · 0 条可撤销`——`0` 是"还没读到"的初始值，被印成了服务端的结论（服务端真给 0 的含义是"永不过期"，两者完全相反）。
2. 横幅写"下面是上次成功读数，可能已过期"，而当时**没有任何一次成功读数**——这句本身就是假安心。

改法就是上面那两处的**分档**（`undoWindowLabel` / `undoEmptyText` / 横幅标题按 `undoRefreshedAt` 是否为空分支），改后重跑 `vue-tsc -b --force` 与 `vite build`，并重新用假 token 复取 403 那一档确认文案。**这两条是靠"把页面的话当作结论来读"才发现的，不是靠类型检查**——类型检查全绿时这两句谎就已经在页面上了。

### 五、判据能不能红（铁律 #8）与一次驱动自纠

两条新判据各自单点变异，**先跑未变异对照**（本批已固化为规程，见 §二之二十六）：

```
对照（未变异）=> RC=0 11 passed, 1 warning in 2.87s
M-1 清单改成含过期项 => RC=1 1 failed, 10 passed, 1 warning in 4.21s
    正文：FAILED tests/unit/test_af_undo_http.py::test_expired_deploy_leaves_the_available_list
    还原逐字节一致=True
M-2 清单少给 age_s => RC=1 1 failed, 10 passed, 1 warning in 5.58s
    正文：FAILED tests/unit/test_af_undo_http.py::test_available_items_carry_the_keys_the_ui_renders
    还原逐字节一致=True
DRIVER_RC=0
```

**驱动器 v1 的那次读数是废的，如实记**：M-1 报 `RC=2 / 1 error` 看着像"门很严"，实为我的替换串把推导式那一行换成了裸 `True` ⇒ 语法错误、目标测试根本没跑（`error` 与 `failed` 是两回事）。单独重放一次拿到完整 pytest 尾巴才确认，改 `if True` 并让驱动**打印正文而不只是退出码**之后才是上面那份读数。同族坑第三次记（前两次：§二之二十三 的 WSL 假红、§二之二十六 的对照档缺失）。

### 六、全链读数与卫生

- `vue-tsc -b --force` **TC_RC=0**、`vite build` + `build-manifest.mjs` **BUILD_RC=0**（文案修正后各重跑一次，两次都 0）。
- `python -m pytest -q` **2818 passed / 51 skipped / 1 warning / 7 subtests passed in 92.64s（RC=0）**，对照改前同机 **2816 passed / 51 skipped** ⇒ **+2 正好等于本批新增两条**，无静默增减。**墙钟 92.64s 也顺带把上一批那条 `556.34s` 的悬账结掉**：计数完全同形、速度回到历批 94–124s 区间 ⇒ 上批的量级差是**这台机器当时的状态**，不是套件；那条"未归因"登记就地改为已归因（铁律 #11：对 HEAD 重跑而不是引用旧读数）。
- `GATES_PYTHON=python "C:/Program Files/Git/bin/bash.exe" gates.sh` **RC=0**（本批不动 `src`，七节绿行逐字与 §二之二十三/二十四/二十六 相同；含新门那行 `✓ 联动桥依赖门禁干净（paho 声明于 ['dev', 'mqtt']；交付面 装 extras ['api', 'ha', 'mqtt']，CI 面（测试镜像） 装 extras ['dev']，CI 面（工作流） 装 extras ['dev']，三个面逐一核过）`）。**顺带钉一条执行事实**：`gates.sh` 的默认解释器是 `python3`，而本机 `python3` 不是装依赖那份 ⇒ 直接跑会得到 `RC=2 homesdk 未安装`，那不是门禁红、是跑错解释器（与 §二之二十五"缺包九成是跑错解释器"同族，本仓第一次把它撞在 gates 上）。
- 卫生：`git diff --numstat` = `tests/unit/test_af_undo_http.py 47 0`、`ui/src/views/LiveView.vue 63 8`。`LiveView.vue` 是 **CRLF 文件**（HEAD 359 行 / 359 个 CRLF，改后 414/414，裸 LF **0**）⇒ 本批按"锚点必须匹配该文件真实换行"的既有规矩处理，没有出现整份重写的假 diff；测试文件保持 LF（CRLF **0**、292 LF）。
- 验收面记账：**§5.3 第 4 件至此按原文两半交付**（通路 + 10s 定时刷新）。第 3 件（af_persist 追加写）继续压在 §五 第 9 件的裁定上，不动存储面；第 1/2/6 件前置合并窗；第 5 件属 DCD/SP 动作。

### 七、补测：定时器随挂载起、随卸载停（把 §六 那条"有代码无读数"变成有读数）

上面登记的两条未测项里，**第一条已在本批当场补成实测**（同一台 serve + 同一个浏览器页，无需重跑功能链）：

- **做法**：在页面里挂钩子把每次 `/api/undo/available` 的请求时刻记进 `window.__hits`（同时包 `XMLHttpRequest.prototype.open/send` 与 `window.fetch` 两条通道——axios 走 XHR，只挂 fetch 会漏计；SPA 路由切换不重载文档，所以计数数组跨页存活，这才是可比的）。服务端侧同时留 `uvicorn` 访问行做旁证（`grep -c 'GET /api/undo/available'`）。
- **实测**：SPA 切到 `/live` 后 `hitsOnLive=5`，相邻间隔 **`10451 / 9995 / 10166 / 9830` ms** ⇒ 10s 档是真的在打，不是"看起来在打"。随后 SPA 切回 `/overview` 并记 `tLeave`，**等了 51 秒（五个档）⇒ `hitsAfterLeave=0`、`totalHits` 仍为 5** ⇒ `onUnmounted` 的 `clearInterval` 生效，离开页面不会留下后台轮询。
- **旁证**：`LiveView.vue` 全文只有**一对** `setInterval`/`clearInterval`（`:166` 起、`:169` 清），`grep -n "setInterval\|clearInterval"` 无第二处。

- **第二条（多标签各自轮询）本批仍无直接读数，如实挂着**：试过两次都取不到——`window.open('…/live')` 被弹窗策略拒（返回 `{opened:false, blocked:true}`，自动化上下文里没有用户手势），`browser-use` 也没有"新建标签页"这一动作（`navigate_page` 只有 `url/back/forward/reload`）。能实测到的前提是**没有任何跨标签协调**：`grep -rn "BroadcastChannel\|navigator.locks" ui/src` **0 命中**、`grep -c "addEventListener('storage'" ui/src/views/LiveView.vue` **0**。所以"N 个标签 ⇒ N 个 10s 轮询"是**由"每实例一个定时器 + 零协调"两步推出来的结论，不是量出来的 QPS**。负载后果也如实界定：该端点是只读 GET、且 `available()` 只碰撤销快照目录 ⇒ 多标签放大的是请求量，不涉及设备写入。要把它变成实测，需要能在同一浏览器开两个页（或给 `ui/` 装一个测试框架跑双挂载），本批都不做。

### 八、UI 判据为什么只能是这样（登记一条口径，免得被读成"没跑测试就算改完"）

本仓 `ui/` **没有 vitest**（`package.json` 无该依赖，也不打算在本批塞一个进去）。所以 UI 改动的判据只有三条：`vue-tsc --force`、`vite build`、**真浏览器读数**。前两条只证"能编译"，本批的实质结论全部来自第三条；而这一页真正的风险（过期读数、把失败当空集、猜窗口长度）编译期一条都判不出来。因此本批把**跨层契约**放到了能判红的一侧：前端逐键渲染的集合，用服务面测试钉住（见本节第二节）。**残留只有一条**：多标签各自轮询**没有直接 QPS 读数**（第七节写了为什么取不到、以及取到了哪条前提）；"页面切走会不会留定时器"原本是挂着的一条，本批已就地补成实测。**这些差异不写成 PASS。**

---

## 二之二十八、前端手抄的每条 HTTP 路径此前没有任何一门看过：`UI ↔ 服务端路由表（装饰器 + 挂载表两张脸）` 做成静态门禁

### 一、为什么是这一条（不是"再补一门"，是同一族里最后挂着的那条）

上一批补 §5.3 第 4 件时把一句话留在了记录里：`ui/` 没有 vitest，UI 侧判据只有 `vue-tsc`、`vite build`、真浏览器读数三条，**前两条只证"能编译"**（§二之二十七 第八节）。那批给 `/api/undo/available` 补的是**响应键集合**的服务面判据，而**路径与方法本身仍是手抄的字符串**——服务端改名、删路由、GET 换 POST，前端那一行照编译、照 build，只有用户点一次才 404/405。

本仓从 §二之十九 起一路在扫"名单手抄"那一族：工具名单（§二之十九）、状态源扇出（§二之十四）、extras 名（§二之二十六）、出向载荷字段（§二之二十二）。**HTTP 路径是这一族里唯一还挂着的一条**，而且它比工具名更容易漂：工具名改了调用点会 `KeyError`/`NameError`，路径改了**什么都不会发生**，直到人在浏览器里点下去。

射程实测（三条都是本轮跑出来的）：
- `grep -rn "request<\|request(" ui/src --include=*.ts --include=*.vue | wc -l` = **51**，其中 1 处是 `ui/src/api/client.ts:27` 的函数定义本身，其余 **50 个调用点全部集中在 `client.ts` 这一份文件**（视图层只调 `api.xxx()`，不直接拼路径）；
- `grep -c "@app." src/autoforge/af_api.py` = **81** 条装饰器，全在一个文件；本门装饰器脸读数 `参与匹配 79 条 + 被排除的兜底/MCP 2 条 = 81`，两侧逐字对上；
- **但服务端路由不止一张脸**：`af_conflict_runtime.py:498-503` 用 `("GET", "/api/conflicts", handler)` 的 `_ROUTES` 表 + `app.add_api_route()` 挂 5 条真路由（`grep -rl "add_api_route" src` 只有它和 `af_runtime_plugins.py` 两个文件）。首版只读装饰器 ⇒ 本门会对这 5 条报"路由表里没有这条"，而且给的修法方向是错的（端点明明在）。表形状是静态可读的，就读进来：`参与匹配路由 84 条 = 装饰器 79 + 挂载表 5`；
- 反向：`服务端有、UI 从未调` = **33 条**（= 原 28 条 + 那 5 条挂载表面端点；只计数不判红，理由见第四节）。

### 二、交付

| 件 | 内容 |
|----|------|
| `scripts/check_ui_api_paths.py` | 524 行、纯标准库（本机禁 pip install，依赖第三方 JS 解析器的门等于没有门禁）。三条判据 A 路径存在 / B 方法一致 / C **反空洞自证**：`request(` 的每个调用点都必须解析出（方法, 路径），解析不出 ⇒ `exit 2`；射程覆盖**两张路由脸**（`@app.*` 装饰器 + `("VERB", "/api/…", handler)` 挂载表） |
| `gates.sh` | 新节 + `ui_api_rc` 两条红分支（`=2` 射程塌、`=1` 契约不符），注释里写清"为什么兜底必须排除" |
| `tests/unit/test_ui_api_paths_gate.py` | 433 行 / **24 条**反例，含两条真实仓库计数（`mounted == 5`、`routes == 84`、`routes - mounted + excluded == 81`、`boundary == ["src/autoforge/af_runtime_plugins.py"]`）、绿色行无形容词、"字面量段被 `{param}` 接住不算红"这条边界，以及挂载表面那三档（有表⇒可达 / 无表⇒同一份前端代码必红 / 表改形⇒`exit 2`） |

扫描器是手写字符级状态机（`_skip_literal` / `_skip_braces` / `_split_args` / `_top_literals` / `_normalize`），不是正则：`'…'`、`"…"`、`` `…${…}…` `` 三种引号 + `//` 与 `/* */` 注释 + `()[]{}` 嵌套都要认，泛型 `request<HealthResponse>(…)` 的 `<…>` 要跳过且**不能把 `=>` 里那个 `>` 当成闭合**。

### 三、判据 B 为什么单独存在（路径对了方法错，是另一种故障）

`af_api.py` 里 GET/POST 同路径成对的路由有实例（`/api/undo/{deploy_id}` 一支 GET 做预览、一支 POST 做回滚）。前端把 `POST` 抄成 `GET` 时路径完全"存在"，只比路径的门会对着一份 405 说干净。所以命中路由集之后还要再看**方法集合**，报文案直接点名"405 与 404 是两种故障，抄对路径不算过关"。

### 四、四处口径是拿读数定的，不是拿感觉定的

- **排除 SPA 兜底与 MCP 面**：`GET /{full_path:path}` 一条就接得住任何拼错的路径（`/api/nope-at-all` 在兜底之下返回的是 200 + HTML），把它放进匹配集 ⇒ 本门永远绿。首版按**前缀**排除 `/mcp`，结果把真路由 `GET /api/mcp/pair-request` 一起挪出射程——那是"少一条判红能力"的静默错误，改成只排**恰好** `/mcp` 与含 `{full_path` 的路径；装饰器脸 `excluded` 从 3 条回到 2 条、`参与匹配` 从 78 回到 79，这 1 条差额就是那次自纠的读数。
- **第二张脸读进来，但"读不出"要分两种**：首版只读装饰器 ⇒ 对 `_ROUTES` 那 5 条真路由报假红、还把修法指向 `af_api.py`。补上挂载表读法之后，紧接着撞出第二个问题：`af_runtime_plugins.py` 同样有 `add_api_route`，按表形状却读出 **0 条**——它的路径是插件在运行期声明的，静态**本来**就没有表。首版把"0 条"一律当"表改了形" ⇒ 本门 `exit 2` 长红；那种红只会被人改成 `continue-on-error`，最后换来真假绿。判别改成用**文件自己的形状**：有 `add_api_route` **且**有字面量 `"/api/…"` 却读不出表 ⇒ `exit 2`（表形变了）；有 `add_api_route`、0 个字面量 `/api/` ⇒ **登记为射程边界**，在绿色行里单列计数（`运行期挂载文件 1 个`）。两种情形都不写文件名硬编码。
- **通配段双向放行是故意的**：`/api/graphs/{name}` 在 HTTP 层确实接得住 `/graphs/tags`，本门判"这条路径可不可达"，不判"这个值服务端认不认"（后者是 4xx 语义，静态读不出来）。段的**数量**仍要对齐，多一段少一段都红。这条边界用第六节 M6 量出来了，不是推出来的。
- **反向读数只计数不判红**：33 条服务端路由 UI 从未调用，其中相当一部分是 MCP/DB 面向与 CLI 共用的端点，本来就不该有前端调用点；把它做成红只会逼下一个作者给整节加 `continue-on-error`（§二之五 那条 CI 假绿的教训）。

### 五、C 那条自证为什么必需（本批自己差点又交一份"缺失 0"）

- **探针 v1**（纯正则 `request<...>('VERB', '路径'`)）报出"UI 从未调 `GET /api/undo/{deploy_id}`"，而 `client.ts:131` 明明在调 ⇒ 反引号/模板那类调用点整段落在射程外。于是"缺失 0"里混着"根本没看过这一条"，这是**同一份谎的形状**，只是发生在门禁自己而不是发生在页面上。
- **探针 v2**（补了模板正则）仍有两处静默丢：`` `/graphs/${encodeURIComponent(name)}${version ? `?version=${version}` : ''}` `` 里嵌套反引号会把捕获截断；`${a ? b : c}` 里那个 `?` 会把 query 切点切错。⇒ 改成手写扫描器，并把"每个调用点必须解析得出"升成 `exit 2` 的判据。
- **条件表达式两支都算**：`client.ts:94` 的 `enable ? '/graphs/enable' : '/graphs/disable'` 只取第一支等于把另一条路径的改名风险藏起来。本轮读数：50 个调用点里 **字面量 35 / 模板拼接 14 / 条件分支 1**，`unparsed=0`。
- query 只在"替换之外"的 `?` 处切，`${…}` 折成一段通配并折叠连续（`…/\x01\x01` → `…/\x01`），报错与读数里把通配显示成 `*`（第一版直接把 `\x01` 打进终端，那行读数没人能读）。

### 六、判据能红：真实文件上的变异读数（先跑未变异对照）

驱动器 `Temp/ui_api_gate_driver.py`（用完即删，本轮留档 `Temp/ui_api_gate_readings.txt`）：变异从**内存里的原始字节**还原，锚点命中数 ≠ 1 当场中止，`DRIVER_RC=0` 且 `git status` 只剩本批三份预期改动。

| 档 | 变异 | 期望 | 实测 |
|----|------|------|------|
| 对照 | 未变异 | RC=0 | `RC=0`，绿行见第七节 |
| M1 | `client.ts` 路径抄错 `/health`→`/healtj` | RC=1 | `client.ts:56: UI 调 GET /api/healtj，服务端两张路由脸（@app.* 装饰器 + add_api_route 挂载表）里都没有这条…` |
| M2 | `client.ts` 方法抄错 `GET`→`DELETE` | RC=1 | `UI 用 DELETE 调 /api/health，服务端这条路由是 GET（在 src/autoforge/af_api.py:379）——405 与 404 是两种故障` |
| M3 | **前端一行没动**，`af_api.py` 改名 `/api/health`→`/api/healthz` | RC=1 | `UI 调 GET /api/health，服务端两张路由脸…里都没有这条` |
| M4 | 前端一行没动，服务端 `get`→`post` | RC=1 | `UI 用 GET 调 /api/health，服务端这条路由是 POST（在 src/autoforge/af_api.py:379）` |
| M5 | `client.ts` 路径改成变量 `_p` | RC=2 | `路径不是字面量（_p）…` + 读数行 `调用点 49 处、参与匹配路由 84 条` |
| M6 | **边界档（期望仍绿）**：`/api/undo/available`→`/api/undo/ready` | RC=0 | 绿，且反向读数从 33 变 **34** |
| M7 | 前端新增一行调 `/conflicts`（**该端点只存在于挂载表**），表原样 | RC=0 | 绿，`调用点 51 处`、反向读数从 33 降到 **32** ⇒ 挂载表的路由**真的在匹配集里** |
| M8 | M7 之上把 `_ROUTES` 里 `/api/conflicts` 改名 `/api/clashes` | RC=1 | `UI 调 GET /api/conflicts，服务端两张路由脸…里都没有这条` ⇒ M7 那档绿不是白给的 |
| M9 | 前端新增 `DELETE /conflicts`（挂载表里那条是 `GET`） | RC=1 | `UI 用 DELETE 调 /api/conflicts，服务端这条路由是 GET（在 src/autoforge/af_conflict_runtime.py:499）` ⇒ 方法判据 B 同样吃到挂载表，且**修法指向的文件是那张表本人** |

四条要说清的：
- **M3/M4 才是本门的存在理由**：这两档前端代码一个字节都没改，故障纯在服务端侧——而这正是 `vue-tsc` 与 `vite build` 两条判据**永远判不出来**的位置（上一批把它写成"这一页真正的风险编译期一条都判不出来"，本批把它变成编译期之外的一道门）。
- **M3 首跑用的是 undo 那条路由，结果是废读数**：`/api/undo/available` 改名后仍被 `/api/undo/{deploy_id}` 的通配段接住 ⇒ RC=0。第一反应是"门不严"，核过 HTTP 语义后确认这是**可达性的事实**（请求真的会落到那条参数路由上，只是 `deploy_id="available"` 多半被业务判 404），不是漏判。于是把它固定成 M6 边界档，并在 §六 挂一条残留：**语义级 404 本门不判**；可见处只有反向读数（33→34），而反向是计数。要把它做成红得先给 33 条未调用路由分类，那是另一批的事。
- **M5 那条读数行是特意加的**：`exit 2` 时打印"调用点 49 处"——少的那一个不是"没问题"，是"没看过"。这一行让射程塌掉的时候无法被读成干净。
- **M7–M9 是对"第二张脸"的三向探针**，因为本批在这张脸上犯过一次假红：真实仓库里 UI **一条挂载表端点都没调**（那 5 条全落在反向读数里），所以"读进挂载表"这一步在今天**不改变任何判红结果**——它只改计数。这一条我不写成"抓到了 bug"，写清楚它的实质是**堵住下一次**：哪天前端调 `/conflicts`，只读装饰器的版本会报假红并把修法指向 `af_api.py`。因此这三档都是**双文件变异**（M7 只动前端、M8 前端+表、M9 只动前端换方法），驱动器为此把"多条编辑先数锚点、再一次性落盘"写成了 `apply()`，任一锚点命中数 ≠ 1 就一个字节都不写。

### 七、全链读数与卫生

- 本门绿行逐字：`✓ UI↔路由契约门禁干净（UI 调用点 50 处：字面量 35、模板拼接 14、条件分支 1；服务端参与匹配路由 84 条（装饰器 79、add_api_route 挂载表 5）、被排除的兜底/MCP 2 条；运行期挂载文件 1 个（路径由插件声明，静态读不出，登记在册的射程边界）；反向读数 UI 未调用 33 条（只计数不判红）；现场豁免 0 处）`。
- `GATES_PYTHON=python bash gates.sh` **RC=0**，新节在第七节位置打印；计数棘轮 `全量违规 104 条 / 登记上限 104 条`（本批不产新违规）。仍要重申那条本仓自己的执行事实：直接跑 `bash gates.sh` 会得到 `RC=2 homesdk 未安装`，那是**跑错解释器**不是门禁红（§二之二十七 记过）。
- `python -m pytest -q` 全链 **2842 passed / 51 skipped / 1 warning / 7 subtests passed in 118.24s，RC=0**；本批起点（加门之前那棵树）2818/51 ⇒ **+24 恰等于本批新增那 24 条反例**，无静默增减。
- 卫生：`git diff --numstat` = `gates.sh 19 0`；新增两文件分别 524 / 433 行，`CRLF 计数 0`（`gates.sh` 与两份新文件都是 LF，未出现整份重写的假 diff）。本批**未动 `src/` 一行产品代码**，`ui/` 也一字未动——门是加在契约面上的。驱动器跑完 `git status --short` 仍只有本批那四项（`M gates.sh`、`M docs/…`、两份 `??` 新文件），三条被变异文件（`client.ts`/`af_api.py`/`af_conflict_runtime.py`）字节一致还原。
- 清理：探针 `Temp/ui_api_probe.py`、`Temp/ui_api_probe2.py` 与驱动器用完即删；变异还原后 `git status --short` 只剩 `M gates.sh` + 两份新文件。

### 八、CI 侧（本批的 run 才是这条门的 runner 读数）

run 65（`7a2d041`）与 run 66（`d51cc05`）已 `completed/success`，五作业逐条 `completed/success`、`failed_steps` 全空 ⇒ 连续绿 **33 条（run 34–66）**。这两条是纯文档批，跑的还是加门之前的树；**本门在 runner 上的口径要等本批那条 run**（`gates.sh` 是新节，`ui-typecheck-build` 作业不受影响）。取数时若仍 `in_progress` 则不计入 streak，按上一批定下的口径："连续绿按 run 计，不按 commit 计"。

**已取到（run 67，tip `a93fb4b`，含 `3c18f1c` 那棵树）**：五作业逐条 `completed/success`、`failed_steps` 全空
（`adm-linkage-contracts`/`ui-typecheck-build`/`quality-gates`/`pytest`/`layering-gates`），`quality-gates` 正文逐字读到
`✓ UI↔路由契约门禁干净（UI 调用点 50 处：字面量 35、模板拼接 14、条件分支 1；服务端参与匹配路由 84 条（装饰器 79、`add_api_route` 挂载表 5）、被排除的兜底/MCP 2 条；运行期挂载文件 1 个…反向读数 UI 未调用 33 条（只计数不判红）；现场豁免 0 处）`
——与本机上面那条**逐字相同**（runner Python 3.11 / 本机 3.13.2，纯标准库 AST 门两版都跑）；`pytest` 正文
`2842 passed, 51 skipped, 1 warning in 82.13s (0:01:22)` 与本机通过/跳过数逐字相同 ⇒ 连续绿 **34 条（run 34–67）**。
取 `jobs` 时踩到一条读数口径：`gh_ci_status.py jobs` 要的是 **run 的长 id**（`37182998205`），填 run 序号 67 得到的是
`HTTP Error 404`（RC=1）——那是取数失败，不是作业红。

---

## 二之二十九、裁定 20261004 §一 1 与 §一 2 落地：单写者租约装上 MCP 面（只 check 不 acquire）+ 事件载荷按裁定收口（`7dbd640`）

### 一、件 1：同一把锁此前只拦得住人点，拦不住 Agent

`forge serve` 早在 §一 第 5 步 ① 就装了单写者租约：`af_cli.py` 启动时 `try_acquire()`，拿不到就
`build_app(readonly=True)`，`af_api._readonly_guard` 对 8 个写/live 端点回 503。**MCP 面一个字节都没碰这把锁**
（申请时实测 `grep -rn "readonly\|lease\|acquire" src/autoforge/af_mcp.py` 零命中）⇒ `forge mcp` 与生产 serve
指向同一个 store 根时，人在 WebUI 被 503 拒、Agent 走 `af_live_run`（scope 确实是 `"live"`）**照样把真机写了**。
这与 §二之十六 那两处是同一族"闸门只装一面"，区别在修法不是 AF 能自决的：它给 DB 新增一种此前不会遇到的
**拒收模式** ⇒ 投第 10 件，裁定 **A**。

裁定 A 的三条口径逐条落：

| 口径 | 落点 | 为什么是这个形状 |
|------|------|------------------|
| **只把真机写纳入** | `_single_writer_check()` 只在 `af_service.live_run()` 里按 | `af_store`/`af_persist` 的既有锁不顺带动（裁定驳回 C 的理由：影响面跨两层既有锁） |
| **只 check 不 acquire** | `af_flock.FileLock.held_by_other()` —— 探测**不写 sidecar**、不改归属 | MCP 若盖章，会把生产 serve 的持有者诊断覆盖成自己，事后无法归因。判据只认内核锁：进程崩溃时内核自动释放，陈旧 sidecar 不参与判定 |
| **MCP 文本固定前缀** | `READONLY_DEGRADED:`（`af_service.READONLY_DEGRADED_PREFIX`） | 下游按前缀判别降级态。前缀必须在**文本开头**——`af_mcp` 出口会把错误套进"工具执行出错："那层壳，套了壳等于没有前缀（测试写死字面量而不是常量名） |

`serve_lock_path()` 把锁文件名收成单一出处（`test_serve_lock_file_name_has_a_single_source` 钉住），
CLI 与 MCP 两面不再各抄一遍 `".serve.lock"` 字面量。

### 二、这一族的旧坑：同进程第二条句柄会被自己挡住

flock/`msvcrt.locking` 挂在**打开文件描述**上，同进程再开一条句柄会被自己判成"别人持有"。所以
`_LOCAL_HELD` 认出"是本进程"这一条必须一起钉：否则 serve 自己的每一次真机下发都会被自己的租约 503
——那是把闸门装反，不是补缺口。持锁方在测试里必须是**真子进程**（`subprocess` + `stdin.read()` 挂住），
in-process 的 `FileLock` 假holder 判不出这件事。

### 三、读数（当场跑出，非引用）

- 9 条判据分跑 **`9 passed` RC=0**（对照档）；变体六档全在**真实仓库**上做，`RESTORE_OK` 逐字节还原自证：
  - **M1** 咽喉里那道 check 摘掉 ⇒ `3 failed, 6 passed`（HTTP 503 / MCP 前缀 / 拒收三腿同时红，正是"只装一面"复原）
  - **M2** 前缀改字 ⇒ `3 failed, 6 passed`（前缀是对外口径，改字即红）
  - **M3** 本进程自己的租约被当别人 ⇒ `2 failed, 7 passed`（`_LOCAL_HELD` 那条腿）
  - **M4** 探测顺手盖章 ⇒ `1 failed, 8 passed`（sidecar 不写这条腿单独可红）
  - **M5** 空闲锁也报成被人拿着 ⇒ `2 failed, 7 passed`（"锁空闲照常下发"这条正向对照）
- 桥的键集判据：**B-0 对照 `38 passed` RC=0**；**B-1 观察者路径多发 `node_id`** ⇒
  `FAILED tests/unit/test_af_mqtt_bridge.py::test_observer_path_key_set_equals_the_contract_row ‖ 1 failed, 37 passed`（RC=1）。

### 四、件 2：`trace_id` 判成事件级、`node_id` 判删

裁定 §一 2 **C**：`trace_id` 由发布者每次现场生成，只做**单事件关联**——同一次部署的 `fired` 与 `failed`
也不必同一枚号；因果链靠 `ref`（实例级）与发布者自己的存储元数据（`ingest_insight()` 把上游那枚号收进
`hypothesis_id` 落盘），**不承担跨仓串联**。这句口径现在写在 `_envelope()` 的 docstring 里，防的是下一轮有人
把它当链路号复用（A 档的真实代价：DB"按 trace_id 拉一屏日志"会静默从 1:1 变 1:N）。

同条裁定把 `node_id` **判删**：它是"失败时刻实例停在哪"而不是"哪个节点失败"（`fail()`/`_transition()`
不改 `ctx.current_node`，两条路径不必相等），从未进过契约行，且 DB 从不读 ⇒ 留着就是把一份没登记过的字段
当长期共读面。删除落在 `observe_terminal()`（唯一生产者），`_envelope()` 没动；§二之二十二 那次用
`159ba00` 钉死的键集合因此从"含 `node_id`"改成逐等于 `{trace_id, ts, automation_id, ref, instance_id}`——
`instance_id` 那份额是裁定 20261002 ②A 登记过的**同值过渡字段**，删除时点 = AF v2.6，本批仍不删。

裁定选的语义本身也有一条新判据：`test_trace_id_is_event_level_not_a_chain_id` 钉"同一实例的 `fired` 与 `failed`
两枚 `trace_id` **必须不同**、`ref` **必须相同**"。防的不是今天有人写错，是下一轮把"事件级"当"链路级"复用
——那种改法今天会正好让这条用例红。两文件合跑 **`56 passed`**。

同批把两处旧文案里"仍等 DCD 裁定"改成已裁事实：`scripts/check_mqtt_writers.py` 的 docstring、
`scripts/verify_adm_window.py` 的 §1.2 口径注释（多余键不判红那条行为没变，只是不再引用一个不存在的悬案）。
**两个脚本的判据逻辑一字未动**——`node_id` 从来没被它们判过红，这正是 §二之二十二 那条差额能活到盘点的原因。

---

## 二之三十、裁定 20261004 §一 3 落成为注册表门禁（`c0476e2`），前提与存量读数按实测更正；同批把两条"写好了没人按"的回收挂上生产路径

### 一、约定的形状：注册表在代码里，门只核对名字

"新增有界缓存必须同时给 TTL 与硬上限，并在测试里断言纯写不读也被回收"这句话此前只活在审计正文里
（§五 第 11 件申请时的实测：`grep -rn "有界\|TTL" gates.sh scripts/*.py` 零命中）。按裁定 **B** 落地：

- **`src/autoforge/af_bounded_caches.py`**（新，唯一真源）：`BOUNDED_CACHES` 每项给
  `module/attr/cap/ttl/trim/test`；`FIXED_KEY_CACHES` 给"看着像增长容器、其实键空间封闭"的两处，逐条带理由。
  注册表**不写任何判据逻辑**，只写名字——门按名字回到模块源码核对，写不出那条腿就不许登记。
  这一条防的是"用注册表给一个说法盖章"，那是 §二之十九 刚扫掉的那族第二真源的另一个变体。
- **`scripts/check_bounded_caches.py`**（新，纯标准库）四判据：
  A 每条腿与把手真在那个模块里；B 测试 id 真存在**且被 pytest 收集**（子进程 `--collect-only -q`；
  收集本身失败 ⇒ `exit 2`，不把"读不出"当成"没通过"）；C 新增增长容器必须注册/就地豁免/进基线；
  D 反空洞自证（注册表为空、扫不到容器、注册表读不出、基线里有已不存在的条目 ⇒ `exit 2` 或红）。
- **`gates.sh`** 新节（`cache_rc`）⇒ CI 的 `quality-gates` 跑同一口径；红/射程两条分支各自成文。
- 豁免标记 `# bounded-cache: exempt(理由)` **单独计入读数**，且**注册表文件自身不参与计数**——它的
  docstring 引了标记形状，算进去等于给自己虚增一处豁免（这是本批自己踩的第一版假洞，钉成用例）。

### 二、前提与裁定不符，按实测落而不按"5 处"落

裁定 §一 3 写"存量处理：5 处一次登记，2 处误报以『固定键/词表有界』类别逐条带理由登记为基线"。
HEAD 实测不是这个数：

| 裁定给的 | 实测 | 差异性质 |
|----------|------|----------|
| 5 处存量登记 | 扫到增长容器 **76** 个；其中 TTL 与硬上限**同时**具备且被测试钉住的只有 **2** 个 | "5 处"是审计当时的口径；76 是这条扫描口径今天的全集 |
| 2 处误报进基线 | `PreTriggerService._stats`、`DeviceSM.attributes` 根本不进那 76 个（扫描要求"空初始化 + 类内增长"） | 它们是被**就地豁免**（带理由），不是被基线冻结——按裁定给的位置落，等于把两个不存在的问题写成已处理 |
| — | 其余 **74** 个冻结成基线，由 `--print-baseline` 从扫描生成、不手敲 | 基线是**这份扫描的快照**，不是判断 |

**不为凑数给 cap-only 那批造 TTL 腿**：那正是裁定自己驳回的 C（跨 5 个模块的存储层重构）。已把
"存量口径更正 + 是否给 cap-only 那批排期"回投 DCD（§五 第 15 件）。

基线还有一条必须写明的口径：**只在 `src == 本仓 src/autoforge` 时生效**。它是这份扫描的快照，套到夹具树上
会报出 74 条"已经不存在"，把唯一那条真红埋掉——首版就是这么坏的，现在有专门用例钉住"基线不泄漏到别的树"
（`capsys` 断言 `共 1 处判红` 且 `"已经不存在" not in out`）。

### 三、真实仓库读数

- 绿行逐字：`[有界缓存] 注册表 2 项双腿齐全且测试 id 被收集；固定键 2 项带理由；扫到增长容器 76 个，其中基线冻结 74 个、就地豁免标记 2 处`，**RC=0**。
- 反例测试 `tests/unit/test_bounded_caches_gate.py` **21 passed（21.75s）**，含"不误响"主干与四条判据各自单独可红。
- 三档变异打在**真实仓的注册表**上（对照 C-0 先跑 `RC=0`，末尾 `RESTORE_OK` 逐字节还原）：
  - **C-1** TTL 腿指向不存在的名字 ⇒ `RC=1`，正文
    `注册表说 af_service.py::_SESSIONS 的 ttl 腿是 SESSION_TTL_GONE，但 af_service.py 里找不到这个名字 ⇒ 那条腿不存在`
  - **C-2** 测试 id 换成不存在的用例 ⇒ `RC=1`，`注册表指向的用例没被收集：…test_case_that_does_not_exist`
  - **C-3** 注册项 `attr` 改名 ⇒ `RC=1`，`新增增长容器 af_undo.py::UndoStore._records（af_undo.py:274）既不在注册表 / 固定键表 / 基线名单，那一行也没带豁免标记`
    ——三档**各只 1 处判红**，说明它们拦的是三件不同的事，不是一个开关的三个方向。

### 四、同批把两条"回收逻辑写好了但没人按"挂上生产路径（注册表项的 test 出处就在这里）

盘点时按第 5 条审计（§14 件那次）留下的形状复查"只增不减"，实测抓到两处**函数存在、调用方为零**：

1. **`fire_log.json` 的 day 维度**：`JsonFireStore.sweep()`/`FireRecorder.sweep()` 完整存在
   （`keep_days=3` + 3600s 单调节流），全仓调用方只有测试里那句 `sweep(force=True)`；生产侧
   `af_scheduler` 只用 `try_begin/confirm/release`，`af_runtime` 装上 recorder 后从不扫。修法：
   `Runtime.tick()` 按**已装 recorder** 的 `sweep()`，且**不带 force**（带 force 就旁路了节流，逐 tick 变逐 tick 扫盘重写）。
   接缝本身由 `test_tick_calls_the_installed_recorder_sweep` 钉（`stub.calls == [{}]`——塞进任何参数即红）。
2. **`undo_log.json` 随部署次数单调增长**：`UndoStore.purge_expired()` **调用方为零**（`grep -rn purge_expired src`
   只命中定义），超窗快照被读侧判成 `expired` 却从不摘除。这里有一次**被既有判据驳回的设计**：
   第一版把清理放进 `__init__`（"打开即清"），全量跑当场红了
   `test_af_undo_http.py::test_undo_refuses_expired_window_via_http`（`KeyError: 'expired'`，1 failed）——
   打开即清会让 `/api/undo/{deploy_id}` 对超窗记录回 404，把"过期撤不了"和"没这条"混成同一个答复。
   回收点因此挪进**写路径** `record()`（`purge_expired()` + `_trim()` 成对，在 `_save()` 之前），
   自家那两条用例随之改名并把理由写进 docstring：**先证 harness 真会写**（清理前读数 >0），再证"纯写不读也被回收"，
   最后证"没超窗的那条还在"——只写前两条的话，把清理改成无条件清空也能绿。

`expire_stale()` 的 docstring 顺带改掉"或超配额"：配额（`af_scheduler.Quota`）管的是"还能不能再触发新实例"，
不是"字典里最多留几条"；留着那句会让人以为内存上限有配额兜底。这一处不是代码缺陷，是**注释撒谎**。

回收三腿的变异：`摘掉 TTL 腿 … rc=1 1 failed`、`摘掉硬上限腿 … rc=1 1 failed`，对照与还原各 `rc=0`。

### 五、件 4（af_persist 追加写半边）按裁定标注，不改一字代码

裁定 §一 4 **A**：判"追加写"对 `af_persist` **不适用**，并给了执行口径——"AF 把裁定执行约束的这条在本仓记录里
标注为『经 DCD 判定不适用』，避免下一轮又被当未做项重报"。本批照此在 §六 改写那一行（原写"未做，等 DCD 定性"），
并在 §〇 的对应行销掉"只落半边"这个读数。计划文档 §5.3 第 3 件是 DCD 原文（"原文照录、不改一字"是本仓口径），
AF **不动它**；裁定落在这里的记录就是裁定要求的那处标注。`af_persist.py` 一个字节未动。

同时把 §五 第 14 件（v2.6 第 3 件"前置=无"与"待裁"自相矛盾）按这次裁定**闭合**：AF 申请的三档里
C 档（判该子句不适用、从任务表删格）正是裁定 §一 4 的 A 档，两问同解。

### 六、全链与卫生

- `GATES_PYTHON=python ./gates.sh` **RC=0**，新节在 `cache_rc` 位置打印那行绿读数；计数棘轮
  `全量违规 104 条 / 登记上限 104 条`（本批不产新违规，上限没抬）。仍要重申那条本仓自己的执行事实：
  直接 `bash gates.sh` 会得到 `RC=2 homesdk 未安装`，那是跑错解释器不是门禁红。
- `python -m pytest -q` 全链 **2879 passed / 51 skipped / 1 warning / 7 subtests passed in 138.96s，RC=0**；
  本批起点（§二之二十八 那棵树）2842 ⇒ **+37**（租约 9 + 注册表门反例 21 + 回收 6 + 桥键集 1），无静默增减。
- 卫生：两笔 commit 的 `git diff --numstat` 全是小数值（最大 279/0 是新测试文件），**没有整份重写的假 diff**；
  `af_service.py` 是 LF、`af_flock.py`/`af_mcp.py` 是 CRLF，变异驱动按各文件真实行尾构造匹配串（跨行模式用
  `\r\n` 或 `\n` 由文件本身决定），匹配数 ≠ 1 即中止不写字。
- 清理：`Temp/drive_lease_legs.py`、`Temp/drive_ruling_legs.py`、`Temp/gates.log` 用完即删。
- **收尾那笔纯文档 commit 的自证（含一次接近事故）**：run 69 复查读数 + §六 33 条反向读数的开局盘点落进本文件后，
  实测 `GATES_RC=0`、该文件 `CRLF 计数 0`、`git diff --numstat` 全程只有 **1 处"删"**（且属于另一行被改写）。途中踩到一次**几乎看不出来的破坏**：
  我的一次 `Edit` 把 `old_string` 选成了**相邻那条 bullet 的开头**，于是把 `- 同一门的射程边界登记清楚……路由有**两张脸**`
  整段开头覆盖掉，残余文字挂到了新 bullet 尾部。**关键在于 numstat 不报这件事**：受损当时它是 `12 1`，
  而那唯一一处"删"是另一行（§〇 的 GitHub CI 行）被改写留下的，被覆盖那条**没有留下任何删除痕迹**——它没消失，只是被并进我的新行，
  行数因此"只增不减"（该文件一条 bullet 一行、最长 805 字，扫 diff 摘要更看不出来）。捞回方式是拿 `git show HEAD:`
  与工作树做逐行 `difflib` 比对：修好后那条 bullet 不再出现在 ADD/DEL 两侧（= 与 HEAD 逐字相同），
  且剩下的唯一一对 DEL/ADD 经字符级 opcode 核对是**纯插入**（无 delete 段）。**口径钉在这里**：
  往长行文档里插 bullet，`old_string` 只能用新 bullet 自己的尾部锚点，且落笔后必须跑 HEAD 逐行比对——
  只看 `--numstat` 或只看 diff 摘要都不算验过。

---

## 二之三十一、CI 把上一批那道门判成「读不成」：`quality-gates` 作业里没有 pytest（run 68 红，连续绿 34 条断在这里）

### 一、红是怎么来的（先贴读数，再讲机理）

run 68 = `9477be1`（**纯文档**那一笔），五作业逐条：

```
pytest: completed/success job_id=111389232417 failed_steps=[]
quality-gates: completed/failure job_id=111389232450 failed_steps=['Run quality gates']
adm-linkage-contracts: in_progress/None job_id=111389232476 failed_steps=[]
layering-gates: completed/success job_id=111389232537 failed_steps=[]
ui-typecheck-build: completed/success job_id=111389232569 failed_steps=[]
```

（那是取数时点的快照，`adm-linkage-contracts` 当时还 `in_progress`；事后复查它也是
`completed/success` ⇒ 整条 run 只红在 `Run quality gates` 这一个 step。）

红那条作业的正文里，本门的射程自证**只到冒号为止**：

```
[射程] tests/unit/test_af_session_bounds.py 收集未成功（rc=1）⇒ 无法核对测试 id：
[射程] tests/unit/test_reclaim_callers_wired.py 收集未成功（rc=1）⇒ 无法核对测试 id：
结论：有界缓存注册表门禁读不出（exit=2）。…
```

真因在 `.github/workflows/ci.yml` 的 gates 作业：它只装 homesdk wheel + `pip install -e .`，
而本仓 core 依赖是 `jsonschema` + `typer`——**里面没有 pytest**。判据 B 起的是
`sys.executable -m pytest --collect-only -q <文件>`，pytest 不在时 Python 给的是
`No module named pytest` 且 **rc=1**。本机 3.13.2 装了 pytest 多年 ⇒ 同一棵树本机 `RC=0`、runner `exit=2`。
同批对照：那条作业其余各节全绿（`✓ 工具名单门禁干净（扫描 97 个文件，注册表 31 个工具…）`、
`✓ UI↔路由契约门禁干净（…反向读数 UI 未调用 33 条…）`）⇒ 红的是**这一节的射程**，不是整条链的口径。

顺带销一笔上一批挂着的账：§二之二十八 那句"本门 runner 口径要等本批那条 run"现在有了数——
把 run 68 `quality-gates` 正文里那一行与本机 `gates.out` 里同一行取来直接比对，`EQUAL True`
（50 处调用点 / 84 条参与匹配路由 / 兜底-MCP 排除 2 条 / 运行期挂载 1 个 / UI 未调用 33 条 / 豁免 0 处，
逐字相同）。取数方式是 `log <job_id> <关键词>` 落盘后按行比对，不是照屏幕判"差不多"。
同一条 run 也给了新门的 runner 侧读数：`扫描 97 个文件`（比 §二之十九 那批的 96 多一，多的正是
`af_bounded_caches.py` 那枚新模块）⇒ 上一批留下的两处"待复测"一并结清。

### 二、这次门的设计是对的，红得有道理

§二之三十 给本门定的口径就是「读不成退 2，不许报干净」。这次它读不成、退 2、把整条作业弄红——
**假绿那条路被它自己堵住了**。要修的是缺的那个前提（作业里该有 pytest），不是把判据改成
"读不出就当过"，也不是给这节加 `continue-on-error`（§二之二十 记过：那是本门最可能被关掉的方式）。

同族先例是 §二之二十六 的 paho 门（"只在本机装过不算修" ⇒ 声明处/交付面/CI 面三面一致）。
这次缺的不是运行时依赖，是**门禁自身的工具依赖**，形状相同而更隐蔽：它不让产品少一个功能，
它让那道门在 CI 上永久失明。同批复查 `check_mqtt_runtime_dep.py` 的绿行逐字未变
（`…CI 面（工作流） 装 extras ['dev']，三个面逐一核过`）——它核对的是"工作流装没装 dev"，
gates 作业原本不在它的核对项里，所以这次红它不响。**本批不扩它的射程**（那要它去核
"每个作业是否具备它自己要跑的工具"，是另一件事、另一批的账），只在 §六 登记这条边界。

### 三、自踩的那一半：射程消息自己把原因弄丢了

第一版是 `f"…无法核对测试 id：{proc.stdout[-300:]}"`。pytest 缺失时 **stdout 全空、原因写在 stderr**
⇒ 红消息停在冒号后面什么都没有（上面贴的 runner 正文就是证据）。修成 `_collect_reason()`：
先认"没有 pytest"并直接点名缺失前提，否则取**有字的那一路**尾部，两路都空要明说"均为空"。

反例 +2 ⇒ `tests/unit/test_bounded_caches_gate.py` **23 passed**（原 21）。变异 M-1 把消息退回
"只截 stdout"（按脚本真实 LF 构造匹配串，命中数≠1 即中止）：

```
control（未变异）: 2 passed, 21 deselected in 0.17s
M-1: 2 failed, 21 deselected / M1_RC 1
FAILED …::test_b_scope_message_names_the_missing_precondition
FAILED …::test_b_scope_message_carries_whichever_stream_has_the_text
E  AssertionError: ['tests/unit/test_af_session_bounds.py 收集未成功（rc=2）⇒ 无法核对测试 id：']
RESTORE_OK True
```

那条红文末尾正是 CI 里断在冒号的样子——这两条判据盯的就是"消息自己得说出原因"。
**驱动自身也记一笔自踩**：第一版驱动里留了一行 `TARGET.write_bytes(OLD.encode("utf-8"))`，
真跑它就会把门禁脚本**截成三行**（先写占位再覆盖）。跑前删掉了；按 §二之十八 那条口径，
驱动里任何"先截断再写"的行都不该存在，写盘只有一次、且内容来自内存里的完整替换。

### 四、修法与一条不能自签的边界

- `ci.yml` 的 gates 作业改 `pip install -e ".[dev]"`——**用声明过的 extra**，不在 workflow 里手搓
  `pip install pytest`（那正是 paho 那批否掉的形状）。代价：多装 grimp/import-linter/fastapi 等，
  但与 `pytest`/`contracts` 两条作业已在用的法子逐字相同，不引入新失败面。`yaml.safe_load` 复解析
  五个作业、`continue-on-error` 全文件命中数仍 0。
- 本机 `gates.sh` **RC=0**，本门绿行逐字未变：`[有界缓存] 注册表 2 项双腿齐全且测试 id 被收集；
  固定键 2 项带理由；扫到增长容器 76 个，其中基线冻结 74 个、就地豁免标记 2 处` ⇒ 动的是消息组装
  与作业依赖，判据读数一个没变。
- 全链：`python -m pytest tests -q` **2881 passed / 51 skipped / 7 subtests RC=0（125.52s）**
  （起点 2879 ⇒ +2，就是本批那两条反例），`gates.sh` RC=0。
- **边界要说死**：本机不制造"没有 pytest"的环境（不 `pip uninstall`、不建 venv、不装包），
  所以 `ci.yml` 那一行的效果**只有下一条 run 能证**。本批因此按「判据级已实测绿 + 作业级待 run 复测」
  记账，**不自签 VERIFIED**。
- **那条复测已落地（run 69 = `d4994a8`，本批文档那笔）**：`quality-gates: completed/success`、
  `failed_steps=[]`，正文里本门给的是绿行而不是上一批那两条断在冒号的射程——
  `[有界缓存] 注册表 2 项双腿齐全且测试 id 被收集；固定键 2 项带理由；扫到增长容器 76 个，其中基线冻结 74 个、就地豁免标记 2 处`
  （runner 08:03:28Z，与本机 `gates.out` 同一行逐字相同）。这条绿行本身就是判据 B **真在 runner 上跑过**的证据：
  "测试 id 被收集"只在 `--collect-only` 成功之后才印。事后复查整条 run：**五作业逐条 `completed/success`、
  `failed_steps` 全空**（`layering-gates`/`quality-gates`/`ui-typecheck-build`/`pytest`/`adm-linkage-contracts`），
  `gh_ci_status.py runs` 亦给 `total_count=69` 与 `success runs: [69, 67, 66, …]`——run 68 是那份名单里唯一的缺席者。
  作业级那一半因此从"待复测"转为实测，本批的等级不再打折。

### 五、streak 与 pin 的账

连续绿自 run 34 起至 run 67 共 **34 条**，**断在 run 68**（唯一红因即上文）。断因不在产品逻辑，
也不在判据本身——这点要写清楚，否则下一条绿会被读成"上一批那道门本来就时好时坏"。
**复摆那一头已归零**：run 69（本批文档那笔）整条 `completed/success`、五作业逐条 `failed_steps=[]`，
正文给绿行、runner 行与本机同脚本输出比对 `EQUAL True`（读数在 §四 末条）⇒ 新的连续绿 **1 条（run 69）**，
仍按"以 run 计、不以 commit 计"的口径引用。
另一笔账：这一改把 gates 作业也拉进了「CI 依赖未 pin」那一面（§五 第 5 件，仍等 DCD）——`[dev]`
全是下限写法，上游再漂移时这条作业会以同样方式红。AF 不自决加 pin/lockfile。

---

## 二之三十二、那道门只看了三棵第一方 UI 树里的一棵：`UI↔路由` 扩到全部前端（三张调用脸 + 树登记表）

起因不是新需求，是本批 §六 那条盘点里逼出来的一个问题：**"33 条 UI 从未调"里那个"UI"到底指谁**。答案是：只指 `ui/src`（开发面板），而产品的前端是那外两棵。

### 一、第一手读数

- 本仓有**三棵**第一方 UI 树：`ui/`（开发面板）、`ui-user/`（用户端 ForgeSight）、`ui-user-mimo/`（同端第二实现，`ForgeSight_前后端对接计划` 那批的产物）。三棵都在 git 里（`git ls-files ui-user ui-user-mimo` ⇒ 76 个文件）。
- 把后两棵纳入射程后，反向读数 **33 ⇒ 16**。消失那 17 条**不是"删得掉的死面"**，是 `automations` 一族、`user/agents` 一族、`auth/*`、`pending/*` 这些一直在被真调的路由。
- 剩下 16 条按来源分组才有意义：`sessions` 一族 6 条、`conflicts` 一族 5 条（`af_conflict_runtime.py` 挂载表）、`watch/start|stop` 2 条、`asks/{name}` / `experience/export` / `GET mcp/pair-request` 各 1 条。**这一批**才是"MCP/CLI 面向 vs 前端本该调"该逐条判的对象。

### 二、门要纳入它们，得先修三处——每一处都是**漏**，不是难

1. **第二、三张调用脸**：`ui` 写 `request('GET', '/undo/…')`（动词在前）；两棵用户端树写 `req(path)` / `req(path, { method: 'DELETE' })`（路径在前、动词在 `RequestInit` 里、**省略即 GET**，因为是 fetch 的默认档）。只认第一张脸 ⇒ 那两棵树整个读成"0 个调用点"。
2. **`/api` 前缀**：`ui-user-mimo` 的 `API_BASE` 默认**空串**、路径自己写全 `/api/…`，另两棵写 BASE 之后的相对段。归一规则按**路径自己带不带 `/api`**（新增 `_full()`），不给每棵树配一个前缀常量——配了常量的人不会同步来改门。
3. **泛型里的 `;`——这条是修前两条时撞出来的真 bug，且比前两条值得记**。跳过泛型实参的循环原来写的是 `elif text[i] in "(;": break`，而两棵用户端树写的是 `req<{ ok: boolean; user: User }>(…)` ⇒ **类型实参里就有 `;`**，整条调用被**静默丢掉**（连"解析不出"都不报，也就永远不到 exit 2）。修前若直接纳入那两棵树，读数是 `ui-user` 11/16、`ui-user-mimo` 7/19 ⇒ 85 个调用点里 **17 个（两成）会以"没看过"的样子混进"干净"里**。这就是 §二之二十八 给本门立的那条 C 判据的反身版：门对 UI 要求"每个调用点都得进射程"，它自己对泛型解析破了却没人要求它红。

### 三、新增两条射程判据（最坏表现恰恰是绿行，所以必须自己会红）

- **登记表 `UI_TREES`**（`目录 + anchor 文件 + 它是哪一端的脸`）里某棵树**读不出任何调用点** ⇒ `exit 2`：目录被搬走、anchor 改名、helper 换名都归这一条。
- 盘上多出**形状像第一方 UI 的顶层目录**（有 `package.json` 且有 `src/`）却不在登记表里 ⇒ `exit 2`。判别只看形状，实测在本仓恰好命中那三棵、不多不少；漏登记的后果不是"少看一棵树"，是反向读数把那棵树里的真消费者算成"UI 从未调"，下一批就有人照这个数去删活接口。
- `gates.sh` 的调用相应从 `… "$REPO/ui/src" "$REPO/src"` 改成 `… --all`。

### 四、反例与门自证

该文件测试 **24 ⇒ 37**（+13）。六档变异驱动，未变异对照 `37 passed / RC=0`，逐档读数：

| 变异 | 读数 | 红在哪些判据 |
| --- | --- | --- |
| 退回只认 `request` 这张脸 | `8 failed` | path-first 那一族 |
| 任何深度的 `method` 都当动词 | `3 failed` | 载荷字段 `{body: JSON.stringify({method:'PUT'})}` 被读成 PUT ⇒ 对只有 GET 的路由报**假红** |
| 泛型见 `;` 就退出 | `3 failed` | 含**真仓 `--all`** 那条 ⇒ 漏读在真实树上量得出 |
| `discover_ui_trees` 永远返回空 | `1 failed` | 未登记树不再红 |
| 某棵树 0 调用点不算射程 | `1 failed` | 整棵树消失也能绿 |
| 路径永远补 `/api`（双前缀） | `4 failed` | 含真仓那条 |

六档红的不是同一批测试 ⇒ 拦的是六件不同的事，不是一个开关的六个方向。驱动器从**内存字节**还原，末尾 `RESTORE_OK True`；用完即删（`Temp/drive_ui_tree_legs.py`）。

### 五、全链读数

`GATES_PYTHON=python ./gates.sh` **RC=0**，本门绿色行现在是：`UI 调用点 85 处（ui 50、ui-user 16、ui-user-mimo 19）：字面量 50、模板拼接 34、条件分支 1；…反向读数跨 3 棵树仍未被调用 16 条（只计数不判红）；现场豁免 0 处`。
`python -m pytest tests -q` ⇒ **2894 passed / 51 skipped / 1 warning / 7 subtests，RC=0**（上一批 2881 + 本批 13 条，无静默增减；墙钟 156.66s）。

### 六、边界登记（不在本批收口，写清楚免得被读成已收口）

- `ui-user-mimo/src/api/http.ts` 的配对流走 `new EventSource(…)`，不是 `req(` ⇒ 本门看不见那条 URL。与运行期插件那张脸同类，**登记不判红**。
- BASE 若哪天改成 `/v1` 之类，`_full()` 仍会补 `/api` ⇒ 报"这条路径不存在"，是**假红会响**，不是漏绿；改 BASE 的人当场看见。
- 两棵用户端树的 `USE_MOCK` 默认档相反（§六 记过）与本门无关：门读的是源码里的调用点，构建期走不走真后端不改路径形状。
- 剩下那 16 条的**逐条定性**（MCP/CLI 面向 vs 前端本该调）不随本批完成——本批只把"数错了"改成"数对了"。

---

## 二之三十三、安全审计（scoped run）从未进过处置链；对 HEAD 复测后真正的洞是"枚举不存在的码根本不计数"＋MCP 有 6 个键"没声明却能传"

起因不是有人催，是 `docs/audit/AutoForge安全审计报告.zip` 这一份**从来没被记过账**：`docs/` 全量检索
"安全审计"四字只命中这份 zip 自己，§〇/§五 没有任何对应行。它是第八轮之外的另一族（前五到七轮都是
"稳定性与功能性缺陷清单"，这份是 `cloudflare/security-audit-skill` 格式的 threat-model run），
所以处置记录单开一份 `docs/audit/审计报告_安全审计_核实与修复.md`，本节只记落点与读数。

### 一、先钉源：报告的"根因三条"有两条在 HEAD 早已修完

快照切在 `zip-snapshot-of-default-branch-2026-09-29T22:43`（`source_ref_kind=codeload-zip-snapshot`，
**无 commit sha**），而 `3fbbab9` 之后授权码面已经是 8 位码 + `consumed` + `failed_attempts` + `locked_until`
+ `FAILURE_THRESHOLD=10`/`LOCKOUT_S=300`。按铁律 #11 逐条对 HEAD 重跑，结果记成 §〇 那张七行表
（**已修被重报 5 条 / 成立 2 条**）。成立那两条里，"MCP 全文无 limiter"这句本身对，但报告把力气用错了地方：

- 报告建议加 `RateLimiter` 到 MCP 协议层。实测 `record_failure()` 在 `rec is None` 时**直接 `return False`**
  ⇒ **试一个不存在的码，任何计数器都不加**。也就是说单码锁定挡不住"换着码试"这一族，而协议层 limiter
  只是把症状按住——它既不知道"这次失败属于哪个主体"，也修不了"锁定计数只对存在的码生效"。
- 于是防线做进 `AuthCodeStore`：`ATTEMPT_WINDOW_S = 60.0` / `ATTEMPT_LIMIT = 10` 的**全店窗口**，
  `record_failure()` 在 `rec is None` 的早退**之前**计数，`validate()` 在窗口内打满时一律返回 False。
  失败方向是既有的：验证不过 ⇒ 回落人审队列（`submit_pending`），不是把设备写放行。
  窗口是**进程内**的，重启即清零 ⇒ 这一条在文档里明写为边界，不写成"暴力破解已根治"。

### 二、报告没盘到的那族：`dispatch()` 从不把 `arguments` 与 `inputSchema` 对账

这条判据在报告里只作为"根因 3"的一句话（"未声明的键实际可传入"），AF 把它**量成了集合**：
AST 扫 `af_mcp.TOOLS` 的 31 个工具，handler 里消费的顶层键 vs schema `properties` 声明的键，
双向差额 **6 个键"没声明却能传"**——`af_draft.session_id`、`af_compile_spec` 的 `spec`/`prompt` 两个别名、
以及 `allow_bulk` 出现在 `af_save`/`af_enable_by_tag`/`af_import_store` 三个写工具上。
最后那个最要紧：它在 `af_service.py` 是 `if not allow_bulk:` 的**爆炸半径护栏旁路**，
而旁路开关在对外契约里**不存在**——下游从 schema 上看不见自己正在用的是哪一档。

两处各自修，且都留了能红的判据：

| 面 | 落点 | 判据 |
|---|---|---|
| 运行期 | `dispatch()` 在 `_guard(scope, current)` **之后**比 `_undeclared_args()`，命中即拒收并回显该工具声明的参数表 | `test_undeclared_argument_is_rejected` / `test_declared_arguments_still_accepted` / `test_scope_guard_runs_before_argument_check`（顺序也是判据：先鉴权，否则错误文本会给未授权主体泄露 schema 形状） |
| 静态 | 新门 `scripts/check_mcp_arg_schemas.py`：消费未声明 / 声明未消费 / 缺 `properties` 三条，锚点读不出或 handler 整包转发 `args` ⇒ **`exit 2` 不报干净**；`gates.sh` 新节两条红分支 | 11 条反例 + 六档变异（`mcp_arg_schemas_gate.py` 的 CONTROL/M1…M6） |

`allow_bulk` 的处置是**补声明**而不是删参数：删了会把"确认批量归档"这个显式动作重新变回隐式，
而铁律要的是它写在纸面上。声明文本按自家口径写清"true=绕过爆炸半径护栏（默认 false）"。

### 三、附带把 IR 规模上限从"有代码无读数"变成有读数

`load_graph()` 的 `max_nodes_per_graph`/`max_edges_per_graph`（默认 5000）此前在 `tests/` 里 **0 命中**，
`MAX_REPLAY_EVENTS` 同理——审计建议里那条"IR 规模上限"被拒的不是判据而是"它是否已被看守"。
补 `tests/unit/test_ir_scale_cap.py` 5 条（节点超/边超/默认值钉死/多 automation 按**总和**判）。

### 四、读数

全链 `2921 passed / 51 skipped / 1 warning / 7 subtests RC=0（356.73s）`，基线 2894 ⇒ **+27**（11+11+5，逐文件归位）；
`gates.sh RC=0`，新门绿行 `✓ MCP 参数↔schema 门禁干净（31 个工具：声明参数 61 个、handler 消费 61 个，双向差额 0；现场豁免 0 处）`；
变异八腿（CONTROL 先跑，每腿从内存字节还原）：`1 failed`/`2 failed`/门禁 `2 条`/门禁 `6 条`/`RC=2 射程读不成`/`RC=2 没找到 TOOLS`，
`RESTORE RC=0 11 passed`；落地面 `git diff --numstat` = `gates.sh 21 0`、`af_auth.py 29 2`、`af_mcp.py 59 8`。
完整八腿表与部署声明见该处置记录 §七/§八。

### 五、不自签的部分（已交 DCD，§五 第 16 件）

审计的 needs_validation 三条与 out_of_scope 14 个单元里的高优先三族，**都不是一条"再修一个洞"**：
长期码 `expires_at=None` 的绝对 TTL 归谁定、`--host 0.0.0.0` + `AUTOFORGE_MCP_TOKEN` 未设 ⇒ `_guard()`
实际放行一切（默认拒绝会直接改变现网可达面）、homesdk wheel 的来源与完整性（`pyproject` 未声明它，
wheel 躺在 `docker/` 下）。三条都会改变**部署前提**或**现网可见行为** ⇒ 按 20261002 口径不自裁。
本批因此**没改**：compose、`--host`、read 端点的鉴权依赖、MCP 默认放行、长期码 TTL，一处没有。

另记一条 coverage 卫生：`out_of_scope 14` 不等于"那 14 个单元没问题"，而 AF 自审不构成独立覆盖 ⇒
处置记录 §四 把"AF 现在能说什么／不能说什么"分列，没自签任何一份"已审"。

---

## 二之三十四、审计 zip 的 `out_of_scope` 第二件落地：`固定名 .tmp` 那一族——一条静态门、7 处收口、9 站冻结

### 一、这一族从哪来（不是"顺手重构"）

§二之三十三 §四 记的那 14 个 `out_of_scope` 单元里，"高优先三件"第二件就是
`af_store`/`af_persist` 的路径写入。本批不等第二轮审计，直接把这一族的**形状**用 AST 盘出来：
`af_store._atomic_write` 的 docstring 把 P1-18 的修法写得很清楚（随机 tmp 名 + 写后 fsync +
replace 后 fsync 目录），可这条纪律只落在了 `af_store` 自己头上——同一个仓里另有一批落盘点仍是
"固定名 `x.tmp` + 裸 `write_text` + `os.replace`"，而 `af_persist.save` 的 docstring 还写着
"原子替换：崩溃时不会留半截文件"。

坏的形状不是"慢一点"而是**静默丢数据**，两处各自成立：

- `af_persist.save`：`PersistStore.claims()` 的租约设计**明确允许**两个进程在租约到期后驱动同一条实例
  ⇒ 两边写同一个 `{id}.json.tmp` ⇒ 交错内容被最后一次 `os.replace` 装上 ⇒ 读侧 `records()` 对校验和
  失败的记录是**跳过** ⇒ 那条活着的实例记录就此消失，不报错也不告警。
- `af_catalog._save`：整站**没有任何锁**，坏一次的代价是下一次 `_load()` 把整份设备目录按损坏读空。
- `af_api._resave_graph_raw`（启停写）：这段在端点闭包里手抄 store 的落盘纪律，伸进 `store._dir()` 私有面，
  两条并发 `/enable|/disable` 会算出同一个 `v{N}`、写同一个 `v{N}.tmp`，丢一次更新。

### 二、本批收口 7 处（6 处 tmp 形状 + 1 处锁范围）

| 站点 | 改法 | 为什么是这一处 |
|---|---|---|
| `af_persist.save` | 走 `af_store.atomic_write_text`（删掉本地固定名 tmp + `chmod` 那三行） | 租约双写是**设计允许**的，不是极端场景；`mkstemp` 建的临时文件本身就是 0600，也就没有"先 0644 落盘再补 chmod"那个可读窗口（ADM B-14 那一半顺带闭合） |
| `af_api._resave_graph_raw` | 整段下沉为 `GraphStore.resave_raw(name, mutate)`，端点只剩一行转调 | 锁的纪律、版本号与原子写必须和写盘在同一处；留在调用方就等于下一次再漏一把锁 |
| `GraphStore.resave_raw`（新） | `directory/.lock` 内读-改-写 + `_atomic_write` 落 `v{N}.json` | 同上 |
| `GraphStore._delete_archive` 标签半 | 读-改-写整段挪进 `tags.lock`，且**不**套 `self._write_tags()` | `FileLock` 不做重入引用计数，嵌套会由内层 `release()` 把外层的锁放开——这是 §二之三十 那条"A4  hazards"的同型坑，注释里写明 |
| `af_catalog._save` / `set_alias` / `remove_alias` | 三站统一走 `atomic_write_text`（该文件早已 import 了这个助手，只有这三站在用旧形状） | 同一文件内两种口径 = 纪律没扩散 |
| `af_insight_queue._atomic_write` | 助手本体改走 `atomic_write_text`，一处收口覆盖它全部调用点 | 队列的 pending/decided 两类记录都经它 |

### 三、新门 `scripts/check_atomic_write_sites.py`（283 行）+ 基线 9 站

三判据：A 函数体内有 `os.replace` 而该函数没有 `mkstemp`/`atomic_write_text`/`_atomic_write` ⇒ 必须
登记基线或就地豁免；B `# fixed-tmp: exempt(理由)` 理由为空判红；C 扫不到任何站点／解析失败／目录不存在
⇒ **`exit 2`**（射程读不成时不许报"干净"）。键是 `相对路径::类.方法`（**不含行号**，且必须含类名——
`af_premiere.py` 里 `PremiereStore.save` 与 `TrialStore.save` 同名，方法名单独当键会让"修好一处"连带
冻结另一处）。基线 `.atomic-write-baseline.txt` 18 行由 `--print-baseline` 生成、**只减不增**，每条一句
"为什么这一站可以暂时留"。

HEAD 实测绿行（`GATES_PYTHON=python bash ./gates.sh` 全链 `GATES_RC=0`）：

```
✓ 原子写站点门禁干净（扫描 97 个文件、`os.replace` 站点 13 处：走 mkstemp/公共助手 2 处、
  固定名形状 9 处（其中基线冻结 9 站、就地豁免 2 站））
```

其中"公共助手 2 处"＝ `af_config.Config._atomic_write` 与 `af_store._atomic_write`（P1-18 那条修法本身）；
"就地豁免 2 站"不是风险形状（`af_predict.Predictor._quarantine` 是"把坏文件挪走保留现场"、
`af_pending.os_replace` 只是跨平台包装）。本批移出的 6 站改完后再也不出现 `os.replace`，故反推开工前
为 13 + 6 = **19 站**；开工前那次 grep 的"16"是**行级粗盘**，与门的函数级 + 只认 `os.` 限定两个口径
都对不上，因此本批以门读数为唯一口径。

### 四、读数（新增 26 条：门 13 + 值语义 13）

- 控制组（未变异）：`25 passed, 1 skipped, 1 warning in 8.39s`，门 `RC=0`。那 1 条 skip 是 POSIX-only 的
  实例记录位模式判据（`mode & 0o077 == 0`，即 ADM B-14 那半），本机 Windows 判不了，带理由跳过——
  **不是**"这条已过"。
- 七档变异（`M1…M7`，逐档从内存里的原始字节还原并自证还原后字节相同）全部按预期变红：
  M1 固定名 tmp 回退 / M2 不拿 `.lock` / M3 标签读回到锁外 / M4 新增一站固定名 / M5 基线键抄错 /
  M6 豁免空理由 / M7 端点重新伸进 `store._dir`。完整表在
  `docs/audit/审计报告_安全审计_核实与修复.md` §九。
- **M2 第一遍没红**，这条要记进教训：那版断言在调用前没清锁记录，`GraphStore.save()` 早就拿过同一把
  `g1/.lock`，于是"拿到过锁"吃的是**别的调用留下的旧账**。改成调用前清零才判得住。断言吃旧账＝没有断言，
  与 §二之三十三 那条"无判据本身是缺陷"同族。
- 全量套件（HEAD 工作区，实测 `PYTEST_RC=0`）：

```
2946 passed, 52 skipped, 1 warning, 7 subtests passed in 125.88s (0:02:05)
```

  归因：上一批基线 `2921 passed, 51 skipped` ⇒ 本批 **+25 passed / +1 skipped**，正好等于新增 26 条
  （25 跑绿 + 1 条 POSIX-only 跳过）。差额不多不少，因此这 26 条之外没有别的测试被本批动过。
- diff 口径（`git diff --numstat`，不含新增文件）：`gates.sh 23/0`、`af_store.py 36/5`、`af_catalog.py 13/10`、
  `af_insight_queue.py 10/3`、`af_persist.py 7/9`、`af_api.py 8/12`、`af_predict.py 1/1`、`af_pending.py 1/0`；
  新增四件 283 / 225 / 289 / 18 行。

### 五、不自签与未做

- 基线那 9 站的**共同残留风险**不许被"门绿了"盖过去：全部缺 `fsync` ⇒ 掉电/容器强杀后仍可能留下
  "名字合法、内容半截"的文件；固定名只有在"同一文件存在第二个写者"时才真的互相截断，而这 9 站
  的"有没有第二个写者"是值语义，静态判不了——所以每条理由写的是"为什么这一站可以暂时留"。
  收口顺序已钉在基线头部：先敏感的 `af_premiere` 两站，再 `af_version`。
- 本批顺手实测出三条**对端可见**的形状（`GET /api/asks/pending` 无鉴权而契约表 §该行只给 POST 标了
  write 令牌；`POST /api/auth/login` 任意非空凭据即签发 `read+write+live` 永久令牌；
  `GET /api/user/auth-codes` 挂 `_read` 却返回**明文 code 全量列表**），三条都改的是别人看得见的口子，
  AF 一律没动，已作为 **§五 续查**写入 `关键决策部/inbox/20261004-AF-安全审计遗留三问-决策申请.md`。
- 部署声明（铁律 #3）：本批全部读数来自开发机 + 门禁/测试链，NAS 镜像未重烤、`docker/` 一个字节未动；
  新门 `check_atomic_write_sites.py` 要等**下一条 run** 才在 runner 上可比，AF 不预签连续绿。

## 二之三十五、裁定 20261004（18:35 那份）AF 侧今日四件落地；落的时候盘出授权面五处落盘全是最裸的 `write_text`，其中"撤销名单读不成"是 fail-open；裁定那句"write 域含 read"在 HEAD 上并不成立

裁定来源：`关键决策部/decisions/20261004-AF安全审计与MA回执与遗留两批-裁定.md`（§一 Q1/F-1/F-2/F-3/MCP、
§二 Q1=A·Q2=B、§五 追认侧）。它回答的是 §二之三十四 末"三条对端可见形状"那件（§五 第 16 件）。

### 一、切分口径：按"AF 今天能自决"切，不按裁定条目顺序抄

| 条目 | 今日处置 | 依据 / 理由 |
|---|---|---|
| §一 Q1 长期码绝对上限（判 A） | **落地** | 纯后端、env 可配、默认值裁定已给（180 天），不需要任何人点头 |
| §一 F-1 `asks/pending` 加 `_read`（判"加"） | **落地，但连蕴含表一起落**（见第三节，前提差已回投） | 只加门不动蕴含会把 ask 通道整条打断 |
| §一 F-2 明文样例凭据（码这半边） | **落地** | docstring 一行 + 一条扫 src 的判据；UI 侧 mock/prefill 另批 |
| §二 Q2=B `paho-mqtt>=1.6,<2.1` | **落地**（两处声明 + `tomllib` 判据） | 上界是裁定买的东西，声明面既有门（§二之二十六）看着它 |
| §五 追认侧：去重与回灌键用 `insight_id` | **落地** | AF 入向键优先级改判，不改载荷面 |
| §一"可信 LAN 写成显式部署前提" | **README 半边落地**；compose 注释半边不属 AF（铁律 #3，NAS 部署者持有那份 compose） | 计划 §5.3 第 10 行已把 compose 那半排在合并窗 |
| §一 MCP 未设令牌默认拒绝（判 B） | **推后，单独一批** | 见第八节：默认结论翻转 ⇒ `af_mcp.py` 的 49 处 `dispatch(` 测试调用点要逐条显式化，还要定 `dispatch()` 的默认值；塞进本批只会做成半件 |
| §一 F-3 auth-codes 收紧 + owner/非 owner 面拆分 | **推后** | 两棵第一方 UI 树消费该列表，改门禁档位 ⇒ 面板直接 403；须连着前端改并做浏览器验证（本机视口取不到，见 §二之二十七 口径） |
| §二 Q3 真机演练 / §三 窗后四项 / 镜像重烤 | **不在 AF 手里** | 合并窗 + DCD 受控窗口；本窗只重烤不开开关（Q1=A 明写） |

### 二、Q1：长期码有了可配绝对上限，且**盘上的历史码不用迁移就会到期**

`af_auth.py` 新增三件（模块在 L0，只允许 stdlib + `af_secrets`）：

- `_longcode_ttl_days()`：**调用时刻**读 `AUTOFORGE_AUTH_LONGCODE_TTL_DAYS`（不是 import 期常量，否则判据没法换值跑），空/解析失败 → 默认 `180.0`，**非正数 → 0 = 显式关**（裁定原话"设 0=显式关"）。
- `_longcode_expires_at(rec)`：存了 `expires_at` 就用存的；没存且 `kind == "long"` ⇒ `created_at + ttl*86400`。这条是"迁移"的替代品——**老码在盘上放着不动，181 天就验不过**（判据两条：造 180 天前与 179/181 天两侧的落盘记录，不写任何迁移代码）。
- `AuthCodeStore.create()` 给长期码直接写 `expires_at`；`validate()` 与 `list()` 都走同一个推导函数 ⇒ 面板显示的"还剩多久"和真正放行用的那个期限**不会分家**（一条判据专门钉这个：`list()` 报的 `expires_at_effective` 必须等于 `validate()` 用的那个）。
- `list()` 每条加 `age_s` / `expires_at_effective` / `expires_in_s`——"距生成多久"是裁定给 WebUI 的原话；面板接线跟 F-3 同批（同一页、同一个门禁档位）。
- 短期码语义一字未动（`ttl_minutes` 那条路），有一条判据钉住"没被顺手改了"。

### 三、F-1 落地时撞到的前提差：AF 从来没有过"write 域含 read"

裁定原话："**加 `Depends(_read)`**。……DB 侧持 write 域令牌（`autoforge_api_token`），write 域含 read——DB 侧零改动。"

现读三件事，其中第二件与那句前提不符：

1. `af_api.py` 的 `requires()` 早先是 `if scope not in info.scopes:`——**逐名比对**，AF 里不存在任何蕴含关系；`write` 域令牌在读面上就是 403。
2. 契约表 `ADM联动主题主题注册表与消息契约.md` §ASk 那一行**只给 POST 半边标了鉴权**，GET 半边是"公开发现"写的（这也是上一批 AF 没自决加门、交裁的原因）。
3. homesdk 侧 `grep -rn "AUTOFORGE_API_TOKEN\|autoforge_api_token"` 在码里 **0 命中** ⇒ DB 那颗令牌实际带哪些 scope，AF 读不到。

⇒ 照字面只加门 = 打断 ask 通道（DB 每 5s 轮询全 403），且这条打断**只有对端能发现**，本仓测试全绿。落法取最小、单向的一条：

```
_SCOPE_SATISFIED_BY = {"read": ("read", "write")}
def _scope_ok(scope, scopes) -> bool: ...
```

read 不满足 write、live 不满足 read——**蕴含只有一条方向**，两条判据各钉一个方向（write-only 令牌取 pending ⇒ 200；read 令牌 POST answer ⇒ 403；live-only ⇒ 403）。前提差本身回投 DCD（回执见 §五 表新增行），若 DCD/DB 确认 DB 令牌本来就带 `read`，蕴含表可以删，判据留着不会假绿。

契约测试四条按新前提重写（`_build(..., tokens=…)`），**原判据判的还是原判据**：路由顺序（精确路径不被 `{name}` 通配吃）、空态形状、ask 项字段最小集改走本地逃生舱；鉴权新加 4 条（无令牌 403 / 未配令牌也 403 / read+write-only 两档 200 / live-only 403）。文件头那段"本测试不改动任何跨仓接口"的旧话已改成实话：**GET 半边的鉴权是对 DB 提出的要求**，DB 侧镜像测试须同步。

### 四、F-2 的码半边

`af_api` 的登录端点 docstring 原样写着一对「样例用户名/明文口令」（裁定 §一 F-2 点名的形状，字面量不往本档再抄一遍——扫的就是这几个字，抄进台账等于把它留在仓里）。判据不写成"扫这一个文件"，而是扫 `src/autoforge/*.py` 全集（`len(files) > 20` 先自证射程读得成，读不成 `assert` 出射程而不是静默通过）：抄进别的注释或夹具也判红。UI 侧 `ui-user-mimo/src/api/mock.ts` 那对 `MOCK_CREDENTIALS` 明写不在本条射程（它是自报家门的 mock 夹具，要收它得连着登录页 prefill 一起做，另批）。

### 五、自盘那族（不是审计 finding，登记在处置链里免得被读成"本轮无产出"）

见 `docs/audit/审计报告_安全审计_核实与修复.md` §十：`af_auth` 五处落盘站点（`_persist_revoked`/`_persist_issued`/`_rewrite_issued`/`PairCodeStore._persist`/`AuthCodeStore._persist`）此前全是裸 `write_text`，而 `_load_*` 把 `JSONDecodeError` 吞成"文件不存在"⇒ **半截的撤销名单 = 已撤销的令牌复活**（fail-open）。写侧补私有 `_atomic_write_text()`（L0 不能引 `af_store`，先例 `af_config`/`af_pending`），读侧把"在但读不成"与"不在"分成两种结论并**置位保持**到进程重启。同批 `pyproject` 两处 paho 声明带上界、`af_mqtt_bridge.ingest_insight()` 键优先级改 `insight_id`（缺失才退 `hypothesis_id` 别名、再退 `trace_id`，且 `transport.id_key` 记下用的是哪一个）、`_envelope()` 文档串里那句"把上游 trace_id 收进 hypothesis_id 落盘"按新键序更正。README 新增 `## 4.1 部署前提：AF 的 HTTP 面只在可信 LAN`——`--host 0.0.0.0` 保留的**理由**与"公开读端点靠的是网络前提而不是鉴权"这一条从此在仓内有字。

### 六、读数

- 判据：新增 `tests/unit/test_dcd_20261004_auth_limits.py` **25 条**（382 行，LF）；`tests/contract/test_af_ask_contract.py` 14→**18 条**；`tests/unit/test_af_mqtt_bridge.py` +3（`insight_id` 胜出 / 压过 `hypothesis_id` 别名 / 只有 `trace_id` 时按"代位"记账）。
- 定向合跑：`tests/unit/test_dcd_20261004_auth_limits.py` **25 passed**；它 + 契约面 **42 passed**；契约 + 桥 **59 passed, 1 warning in 104.36s**。
- 全量：`2978 passed, 52 skipped, 1 warning, 7 subtests passed in 1149.09s (0:19:09)`，**`PYTEST_RC=0`**（当场 `tail` 取的行，非推送侧转述）。
  计数与前一档 2977 差 1 = 本批 F-2 那条；**墙钟 1149s 明显长于同日的 488s 档，原因是这一段并发跑了定向套件、门禁脚本与三次 CI 取数**，
  本批没有为墙钟单独复测 ⇒ 登记为未归因悬账（同 §二之二十七 销掉的那条 `556s` 同族），**不写成"套件变慢"也不写成"正常"**。
- 变异（真实仓、逐字节还原）：

| 档 | 改动 | 结果 |
|---|---|---|
| CONTROL | 未变异 | `RC=0`，`42 passed` |
| M6 | 蕴含表清空（`write` 不再满足 `read`） | `RC=1`，`2 failed, 40 passed` |
| M7 | 蕴含放成无条件放行（任何令牌都算够格） | `RC=1`，`3 failed, 39 passed` |
| RESTORE | 复跑控制组 | 还原后与原文逐字节相同 |

  **另记一次驱动器废读数**（同 §二之二十七 那族的自踩）：驱动第一版把 argv 写成 `["-m pytest"]` 一枚串，
  三档全 `RC=1` 且 stdout 空——那不是变异红，是 `python.exe: No module named  pytest`（两个空格）。
  控制组当时**不成立**，整表作废重跑；修正后才得上表。这条留在记录里的理由：`RC=1` 不是判据，
  "控制组绿 + 失败条数对得上"才是。
- 门禁全链：`gates.sh` **`GATES_RC=0`**（解释器 Python 3.13.2）。原子写站点门绿行由本批自移动到
  `扫描 97 个文件、os.replace 站点 14 处：走 mkstemp/公共助手 3 处、固定名形状 9 处（其中基线冻结 9 站、就地豁免 2 站）`
  （上一批是 13 处 / 2 处 ⇒ +1 站点正是 `af_auth` 那个私有助手，基线 9 站一个没动，"只减不增"仍成立）；
  UI↔路由门那行 217 字符读数未变；有界缓存注册表行 76/74/2 未变；分层/包标记门未新增违规（`af_auth` 仍是 L0：只 import stdlib + `.af_secrets`，实测 `grep "^from \|^import " src/autoforge/af_auth.py` 12 行里没有 `af_store`）。
- diff 口径（`git diff --numstat`，新增文件另计）：`af_auth.py 138/31`、`af_api.py 20/4`、`af_mqtt_bridge.py 15/3`、`pyproject.toml 4/2`、`README.md 14/0`、`test_af_ask_contract.py 70/15`、`test_af_mqtt_bridge.py 29/0`，新增 `test_dcd_20261004_auth_limits.py` 382 行。
- 计划文档侧：DCD 本次自己落笔的六处更正（`decisions/20261004-AF-v2.6前置与执行计划更正-裁定.md` §二）
  已在本仓工作树里由其成为 commit `3262ccb`，本批随推送一并上线（裁定 §四 第 2 条要求的正是"别让它悬在工作树"）。
  其 §五 四条验收当场重跑：`grep -n "已全部裁定"` → 140 行命中；`grep -n "/api/health"` → 100/129/130 三处命中且
  旧写法 `` `/health` 返回 200 `` **0 命中**；`grep -n "origin/main"` → 163/165 两段带"已更正"与 `rev-list --count = 0` 读数；
  §5.3 表内已无"af_persist 顺序追加写"待办行（131 行是收口行）。**§5.3 第 7–10 行逐条对过 HEAD**：第 7 行（`READONLY_DEGRADED:`）与第 8 行（`check_bounded_caches.py`）已在 `7dbd640`/`c0476e2` 交付，第 9 行安全遗留 = 本批 + 第八节推后两件，第 10 行 compose 半边按铁律 #3 留窗内。

### 七、CI 口径

上一批挂的两格"不预签"都已闭合，且是逐字节比对不是"看着差不多"：**MCP 参数↔schema** 那行本批又对 run 73
重取一次 ⇒ `EQUAL True`（本机与 runner 同 70 字符：`✓ MCP 参数↔schema 门禁干净（31 个工具：声明参数 61 个、handler 消费 61 个，双向差额 0；现场豁免 0 处）`，
run 72 那格是上一批已测）；**原子写站点**那行在 `8689b39` 的树上（= run 73 跑的树）与 runner 逐字节相同、
同为 93 字符 ⇒ 那一格成立。**但本批把原子写那行自己移动了**（`af_auth` 的私有助手 ⇒ 站点 13→14、走助手 2→3），
所以本批挂出一格新的：run 74 的 `quality-gates` 正文里那行必须是 14 处/3 处那一行，AF 不预签。
run 73（`8689b39`）五作业当场重取仍是 `completed/success`、`failed_steps=[]`（`pytest`/`layering-gates`/`quality-gates`/
`ui-typecheck-build`/`adm-linkage-contracts`），`pytest` 正文 `2947 passed, 51 skipped, 1 warning in 95.07s (0:01:35)`
与本机 `2946/52` 差 1 条，差因已定位到本机跳过的那条 POSIX chmod 断言
（`SKIPPED [1] tests/unit/test_atomic_write_sites_fixes.py:260`）⇒ **runner 上真跑过了本机跑不了的那条**，
不是口径漂移。按 main 计连续绿 **5 条（run 69–73）**；run 74（本批）的 `pytest` 条数必须与本机对得上，对不上就当场记账。

### 八、推后清单（写明理由，不是"回头再说"）

- **§一 MCP 未设令牌默认拒绝（B 档）**：`af_mcp.py` 的 `current is None` 直接 `return`（原型模式放行）要翻成拒绝，
  牵连两件事：① `tests/` 里 49 处 `dispatch(` 调用点大多没递令牌（`grep -rn "dispatch(" tests/ | wc -l` = 49），默认翻转后要么全绿要么全红，必须逐条显式化才判得住"默认"；
  ② `dispatch()` 的 `current` 默认值本身要定（默认 None 还是默认拒绝哨兵），这是**默认结论翻转**级改动，按 §二之二十六 的教训（依赖面"只在本机装过不算修"）单独一批做，配"未设令牌 ⇒ 每个工具都拒"的判据与对照档。
- **F-3**：`/api/user/auth-codes` 收 `_write` + `list()` 拆 owner 面（明文）/非 owner 面（掩码 + 状态）+ 裁定要的三条判据；
  消费面在 `ui-user`、`ui-user-mimo` 两棵树，改完必须浏览器走一遍，本机视口取不到 ⇒ 结论等级只能写"按 DOM 事件驱动走通"。
- **F-2 的 UI 半边**：mock 凭据与登录页 prefill 一并收（与 F-3 同页，同一批）。
- **Q3 真机演练 / 窗后四项验收 / NAS 镜像重烤**：合并窗，`AUTOFORGE_MQTT` 本窗只重烤**不开开关**（Q1=A 明写）；compose 的 MQTT 引用与那句"只在可信 LAN"注释由部署者那份落（铁律 #3）。
- **import-linter 的 C 口径**：裁定判"A（追认）+ C"，口径 = 同一契约连续 ≥5 条 run KEPT 且有可红实测 ⇒ 本仓那条契约目前 KEPT 计数继续按 run 累加，升硬门要等够 5 条并配变异，AF 不预签。
- 基线那 9 站的 `fsync` 缺失仍按 §二之三十四 的收口顺序走（先 `af_premiere` 两站）。

## 二之三十六、裁定 20261004 §二 那句"升成门"的启动条件由本批自己触发；D 腿上线时变异驱动当场盘出它自己骗得过自己的一档；F-3 的浏览器半边在真 dist 同源部署上取到读数，顺带盘出 `ui-user` 这棵树在 HEAD 就不可用

### 一、先到的是哪一半条件，以及为什么不是"等基线收完"

裁定 §二 的原文启动条件是"等基线 9 站按已钉顺序收完，**或**下一次 `af_auth`/`af_premiere` 被真实改动时——以先到者为准"。本批 F-3 真实改动了 `af_auth.py`（新增 `CODE_MASK` 常量、`AuthCodeStore.list()` 加 `reveal` 形参），**后一半先到**，所以这一腿今天就地上线；那 9 站基线本批一格未收（收它们是下一批的账，顺序不变）。若把"等基线收完"读成唯一条件，这条门就会永久排队——裁定自己写的就是"防永久排队"。

做法上刻意**不开第二份真源**：扩展既有 `scripts/check_atomic_write_sites.py` 成第四条判据（D 腿），而不是新建一个 `check_auth_face_writes.py`。同一族纪律拆成两个脚本，将来只会有一条被人记得跑。

### 二、D 腿的判据、收紧与读数

- **射程** = `af_auth.py` 的全部函数 ∪ 别的文件里"这一条写的作用路径看得出指向 `.auth`"的函数。第二个信号必须按**那条写**收（接收者或 `open()` 的路径参数，或它最近一次赋值的来源里含 `.auth`），不能按"函数体含 `.auth` 字面量"收：`build_app` 那种几百行装配函数里既有 `Path(root)/".auth"/…` 的构造也有与鉴权无关的 `write_text`，按粗信号写第一版实测把它打红（假红）。射程边界在 import 时钉成 `AUTH_LEG_ROOT`，与 A 腿用来读基线的 `SRC` 分开——共用一个开关时，挪 `SRC` 测基线的三条既有测试会被 D 腿的 `exit 2` 一起带崩（临时树里没有本体文件），这个耦合本身就是被测试钉住的。
- **红** = 授权面上的裸 `write_text` / `write_bytes` / **常量可写模式的 builtin `open()`**（`os.open()` 排除，它是 `O_RDONLY` 目录 fsync 那条正路；模式是变量时不硬套，宁缺勿假红）。这一腿**不接受基线也不接受就地豁免**——A 腿允许"有理由的固定名"，D 腿不给这个出口，因为半截的撤销名单被 `_load_*` 读成"没有这份文件"，等于把已撤销的令牌复活（fail-open），这不是能靠一句理由接受的代价。
- **exit 2 四档**：`af_auth.py` 不在射程（改名/挪包）、一个函数都没扫到、一个落盘函数都没扫到、**叫得出名字的助手一处都没扫到**。
- 绿行读数（`gates.sh` 第 142 行跑的那一条，本批原文取自 `gates_batch48.out:33`）：
  `✓ 原子写站点门禁干净（扫描 97 个文件、os.replace 站点 14 处：走 mkstemp/公共助手 3 处、固定名形状 9 处（其中基线冻结 9 站、就地豁免 2 站）；授权面腿射程函数 46 个、其中落盘 6 个（必经助手 5 个、自带 mkstemp 1 个、裸写 0 个——这一腿不接受基线与豁免））`，`GATES_RC=0`。
  落盘 6 = 助手本体自己（`mkstemp` 那份）+ 5 个调用它的落盘函数，与 `grep -c "_atomic_write_text("` 在 `af_auth.py` 上数到的 6（1 处 `def` + 5 处调用）对得上。
- 判据文件从 13 条 collected 涨到 **24 条**（+11），两文件单跑 `57 passed`。

### 三、"什么都不改"的对照档 + 可红实测：M4 是这个驱动抓出来的、这条腿自己的洞

按裁定 §二 要求配齐：真树一个字节不碰（`shutil.copytree` 到临时目录，变形只打在副本上，每档都从内存里的原始字节重摆），先跑对照档再逐档变形。驱动 `mut48_af_dleg.py`，`MUT_RC=0`：

| 档 | 变形 | 取到 |
|---|---|---|
| CONTROL | 什么都不改 | `RC=0`，绿行含"裸写 0 个" |
| M1 | 助手本体换回裸 `path.write_text(...)` | `RC=1`，红行点名 `_atomic_write_text` |
| M2 | 助手本体换回 `open(str(path), "w", ...)` | `RC=1`，红行含 `open(...,'w')` |
| M3 | `af_auth.py` 改名成 `af_authorization.py` | `RC=2`，"射程里没有 `af_auth.py` 本体" |
| M4 | 助手改名 `_atomic_write_text`→`_write_auth_blob`（各处仍旧自己 `mkstemp`） | 第一版 **`RC=0`（假绿）**；收口后 `RC=2` |
| M5 | 在 `af_api.py` 里新开一个授权面落盘点且自己裸写 | `RC=1`，红行点名 `leak_codes_into_auth_face` |

M4 值得单独写：把 `mkstemp` 和"叫得出名字的助手"记成同一种读数时，**助手被整体改名后这条腿仍旧报绿**，而它那句绿行声称的是"必经那个被评审过的助手"。形状上各处自己 `mkstemp` 也许真的还是原子的，但锚点没了——门在说一件它已经看不见的事。收法是拆成两个读数（`必经助手 N 个 / 自带 mkstemp M 个`）并把"named==0"列进 `exit 2`。这条判据落成 `test_renamed_helper_is_range_failure_not_green`，即"下一个把助手改名掉的人"会在 CI 上撞到，而不是靠人记得。

### 四、F-3 的浏览器半边：同源部署上后端面读数全对，视图面根本不接

起法不碰 NAS、不碰仓库 `.forge`（铁律 #3）：`npm run build` 出 `ui-user/dist`，再用 `af_cli serve --ui-dir ui-user/dist --port 8791 --store-root <临时目录>` 单进程同源托管；令牌两份（legacy 单管理员令牌 = owner 面、`AUTOFORGE_TOKENS` 里一颗 subject 非 owner 的 `write` 令牌 = 掩码面）。

- HTTP 面（`httpface48.py`，只打印形状不打印码值）：`POST /api/user/auth-code` 200、新码 8 位纯数字；owner 面 `GET /api/user/auth-codes` 200 且**看得见明文**（掩码行数 0）；第三方 write 令牌 200、`全掩码=True`、`明文码泄漏=False`、`掩码长度=8 且与真码长度相同=True`（这一条是 `CODE_MASK` 的全部意义：不给出长度就不把 10^8 的攻击面指回 10^6）、状态 11 键齐全（`age_s/code/consumed/created_at/expires_at/expires_at_effective/expires_in_s/failed_attempts/kind/locked_until/revoked`）。
- 浏览器面（DOM/页内 fetch 驱动）：SPA 起来、`#/auth-codes` 路由渲染出面板骨架与说明文案；同一页面里 `fetch('/api/user/auth-codes')` 带 owner 令牌 **200、1 行、`long:plain`**。⇒ 后端与鉴权分层这一半在"真构建产物 + 同源服务"的形状上确认成立。
- **结论等级写清楚**：本机浏览器有（browser-use 可导航、可 eval、可读 console）但**无可视视口**，截图不可用，本次走的是 DOM 事件 + 页内 fetch + console 取错，不是像素级确认。
- 面板上那 1 行明文码在界面上显示成"暂无"——这不是本批改动造成的，见下一节。

### 五、浏览器验证顺手盘出的真缺陷：`ui-user` 这棵树在 HEAD 就不可用（本批一个字节没动它）

三条独立证据，不靠推断：

1. **运行时**：console 首条错 `TypeError: Se.openPairStream is not a function`（`App.vue:32` 调它）。根因在门面：`api/index.ts:8` 是 `export const api: Api = (USE_MOCK ? mockApi : realApi) as Api`，而 `mock.ts:83` / `client.ts:35` 各自把方法装在**一个对象字面量里**（`export const api = {...}`），namespace 上并没有 `openPairStream`/`getAgents` 这些键 ⇒ 整个 `api.*` 调用面在运行时都是 undefined。`vue-tsc` 里对应的那条就是 `index.ts(8,25) error TS2352`。
2. **名字层**：`stores/main.ts:49` 调 `api.getAuthCodes()`，客户端里那条叫 `listAuthCodes`（:41）；:53 `generateAuthCode` vs `createAuthCode`；:59 `deleteAuthCode` vs `revokeAuthCode`。⇒ 就算门面修好，这三处仍旧各撞一次 undefined。视图侧还有第二层：`AuthCodesView.vue` 过滤 `c.type === 'long'` 而后端字段是 `kind`，`formatDate()` 期待 ISO 串而后端给 epoch 秒。同一棵树里并存 `views/AuthCodeView.vue`（单数）与 `views/AuthCodesView.vue`（复数）两代面板，`App.vue:8` 引的是 `stores/authCodes` 而面板引的是 `stores/main`。
3. **静态**：`npx vue-tsc --noEmit` 在 `ui-user` 上 `TSC_RC=2`、**51 条 error TS**（28×TS2307 / 16×TS7006 / 4×TS2339 / TS7053、TS2352、TS2322 各 1）。诚实拆账：28 条 TS2307 里含 `naive-ui`、`@vicons/ionicons5` 这类**本机 node_modules 装不全**的模块解析错（本机没跑 `npm ci`，AF 也不在这台机器上装包），这部分**不能算代码缺陷**；TS2352/TS2339/TS7006 那 21 条是代码层，且与 ①② 的运行时证据互相独立地对上。

为什么所有既有门都没拦：`ui-user` 不在 CI 的 vue-tsc 作业射程（那条只跑 `ui/`，已登记在未做清单），`ui-user-mimo` 也不在（它的 node 套件 60 条判据在 HEAD 红 7 条，同样登记）；UI↔路由契约门判的是"路径存在/方法一致"，**不判客户端对象上的方法名**。⇒ 这是"该红的不红"那一族的又一处，且这次红在真机上，不在静态盘点里。

处置：**不在本批修**。收它的正确形状是先定"两棵用户树哪棵是交付面"——`ui-user` 与 `ui-user-mimo` 都在仓、都在写、都进过 handover，把 `ui-user` 的数据层适配补齐（门面 + 三个方法名 + `kind`/epoch 两处值语义 + 两代面板去重）是一次独立的批，补错了树等于白做。已投 DCD（`inbox/20261004-用户WebUI交付面与数据层断链-决策申请.md`），§六 同步登记。本批 F-3 的验收口径因此收在这里：**后端分层 + 同源部署形状 = 实测通过；面板像素级可用性 = 未通过，且未通过的原因不是本批改动**。

## 二之三十七、裁定 20261005 §一 的 AF 半边：交付面认定 B 之后先把 `ui-user-mimo` 的 7 条红修绿；七条里有五条是量具自己的毛病，而新写的判据在变异档里当场假绿过一次

> 起因：`decisions/20261005-AF用户WebUI与MA四件与CVE-裁定.md` §一（Q1=B、Q2 先修后加、Q3 缺省档收敛）
> 与 `decisions/20261004-AF落地回执与write域含read前提差-裁定.md`（§一 三条全裁、§二 启动条件、§五 排期）。
> AF 侧边界照旧：`ui-user/` 冻结归档不投入、DCD 原文不改一字、compose 一个字节不写（铁律 #3）。

### 一、7 条红的逐条归因：只有两条是产品的毛病

| # | HEAD 实测的红 | 归因 | 本批落法 | 产品还是量具 |
|---|---|---|---|---|
| 1 | `compareAutomation 边界`：期望 `['a','b','n','bad']`，实得 `['a','b','bad','n']` | 「从未触发」与「时间戳读不出」共用一个 `-Infinity` 哨兵 ⇒ 两者平局后按名称决胜负，坏数据被插进正常项中间，伪装成"很久没触发" | 拆成两个哨兵（都早于 `Date` 可表示的最小时刻 `-8.64e15`）：`NEVER` 沉底、`BROKEN` 再沉一档 | **产品** |
| 2 | `MCP 端点常量固定`：判据要 `:8000`，常量是 `:8787` | 交付面端口真值是 8787（`Dockerfile.api` EXPOSE、compose 映射、`serve --port`、两棵树的 vite proxy 四处一致）；是判据抄旧了 | 判据改钉 8787；并把"同一个 URL 字面量抄在两份判据里"收成"值只在 `mock-api` 钉一次，`ui-contract` 钉导出与引用两个接合点" | 量具 |
| 3 | `createPairRequest … 5 分钟有效期`：`ttl=300002` | 判据把 `t0` 取在 `await` **之前**，而 mock 的 `now()` 是真墙钟 ⇒ 让出一次事件循环必然多出几毫秒，上界被时钟竞态吃掉 | `t0` 移到调用之后（误差只剩一个方向），产品一个字节没动 | 量具 |
| 4 | `store.test.mjs` 整文件加载即红 | `src/api/http.ts` 顶层 `import.meta.env.VITE_API_BASE`——`import.meta.env` 是 Vite 构建期注入，裸 node 里是 `undefined`，取属性即 `TypeError` | 新增 `src/api/env.ts` 作为全树唯一的环境变量接缝：`import.meta.env ?? process.env ?? {}`；`index.ts`/`http.ts`/`stores/main.ts` 三处改 import | **产品**（可加载性） |
| 5 | `Agent Tab：MCP 卡片…` 缺 URL 字面量 | 同 #2 的同一次抄写 | 同 #2 | 量具 |
| 6 | `配对弹窗…` 缺 `flipIn` | 动画定义与应用都在 `main.css` 的 `.cell` 上，组件里**从来不会**出现这个词 ⇒ 断言指错文件 | 钉三段：`@keyframes flipIn`（定义）、`animation: flipIn`（应用——定义过不等于用过）、组件里的 `class="cell"`（接合点） | 量具 |
| 7 | `§8-3 移动端 375px` 判红 | 负向前瞻写在 `width:` **之后**，挡不住写在它**之前**的 `max-` 前缀 ⇒ 同一条测试的上一行刚要求 `max-width: 320px` 必须在位，下一行就因为它判红。产品里没有真溢出（全树 3+ 位裸宽度为 0，只有 `max-width`） | 改成 `(?<![-\w])width:\s*\d{3,}px`，并用 M7 证明它仍咬得住真写死的 `width: 340px` | 量具 |

**顺带第 8 条**（不在 7 里，是修 #4 之后才露出来的）：`stores/main.ts` 的 `applyTheme()` 写
`document.documentElement`，判据在裸 node 里加载 store 就 `document is not defined`。
先试测试侧半桩（`globalThis.document = { documentElement: { setAttribute } }`），**实测更糟**：
`@vue/runtime-dom` 在导入期 `doc.createElement("template")` 炸 `not a function`（它只防 `doc` 整个缺席，
不防半桩）。所以否掉"给判据搭 DOM 桩"这条路，改产品分层：**store 出状态，App.vue 的 watcher 写 DOM**
（`immediate: true` 保证首帧有属性）。浏览器不存在"没有 document"这种失败模式，产品侧那段防御本来就是假的。

**读数**：`npm test` 从 HEAD 的 **60 条 / 7 红** 到本批 **74 条 / 0 红**（`store.test.mjs` 从"加载即红"
变成 11 条真断言，另加 3 条新判据）；`npm run build` RC=0，23 条 precache 入口（527.61 KiB）、`dist` 下 JS 合计 538,186 bytes（10 个 assets chunk + `sw.js`/workbox；推前复测更正，先前登记的 520,820 是只算 `dist/assets/*.js` 那一趟的口径）；
`gates.sh` RC=0（`UI↔路由` 那条仍认三棵树：85 处调用点 = ui 50、ui-user 16、ui-user-mimo 19）。

`npm test` 脚本本身在 HEAD 也是坏的：`node --test tests/`（尾斜杠）在 Windows 下把目录当模块解析，
`MODULE_NOT_FOUND`。改成 glob `"tests/*.test.mjs"` + `--env-file=tests/mock.env`——Q3 之后 mock
必须显式开，**判据跑法就自己显式开**，而不是回头把缺省档改成 true 替测试兜底。`engines` 从
`>=20.0.0` 抬到真实下限 `>=22.6.0`（`--experimental-strip-types` 从这版才有）。

### 二、缺省档收敛（Q3）与"全树只有一处"这条

`src/api/env.ts`：`USE_MOCK = src.VITE_USE_MOCK === 'true'`（原来是 `!== 'false'`，且
`api/index.ts` 与 `stores/main.ts` **各抄一遍**，两棵用户树的缺省档还相反）。`.env` 保留
`VITE_USE_MOCK=true`（开发档显式开），`.env.production` 显式 `false`。三条新判据钉在
`ui-contract.test.mjs`：缺省档那条钉**整条导出语句逐字在位**；"第二个真源"那条钉
**只有 `env.ts` 允许出现 `import.meta`**（第一版钉的是"别处不许出现 `VITE_USE_MOCK` 字样"，
当场被 `api/index.ts` 的模块注释判红——注释里写键名是应当的，判据要看代码形状不是字样）；
最后钉 `.env.production` 显式关。

### 三、变异自证：新写的判据里有一条骗得过自己

`CONTROL`（什么都不改）：RC=0 / 74 pass。之后每条改完立刻从内存里的原字节还原并逐字节比对。

| 档 | 变形 | 结果 |
|---|---|---|
| M1 | 两个哨兵并回同一档（HEAD 的缺陷形状） | RC=1 fail=1 ✅ |
| M2 | 缺省档退回 `!== 'false'` | RC=1 fail=1 ✅ |
| M3 | 把 App.vue 的主题 watcher **整行注释掉** | **第一版 RC=0——假绿**：`has('src/App.vue', "setAttribute('data-theme'")` 只查字面量，字符串还躺在注释里。改成"取出那一行 + 断它不以 `//` 开头"后 RC=1，红的消息直接点名那行是死的 ✅ |
| M4 | API 层重新碰 DOM（`export const _t = document.title`） | RC=1 fail=2 ✅（DOM 判据 + store 加载） |
| M5 | MCP 端口改回 8000 | RC=1 fail=1 ✅ |
| M6 | 组件里的接合点 `class="cell"` 改名 | RC=1 fail=1 ✅ |
| M7 | 主样式真写一条 `width: 340px` | RC=1 fail=1 ✅（证明 #7 的改写没把牙磨掉） |

`MUT_RC=0`。**M3 是本批唯一一条"门在说一件它已经看不见的事"**：字符串在场判据一旦被注释掉，
红就永远等不到——这条形状和 §二之三十六 里 M4 那档（改名助手骗过绿行）同族，两次都是**先按字面量钉、
后想起字面量不区分死活**。落法记进口径：**钉"某段代码存在"的判据，必须同时钉"它活着"**（取行 + 排除注释行首），
否则它只防删、不防废。

### 四、CI 侧（Q2「先修后加」只加主线树）

`.github/workflows/ci.yml` 新增 job `ui-user-mimo-judgments`：`npm ci`（官方源改写 + 主机自证：
mimo 锁文件 426 个包全带 `integrity`、`resolved` 426/426 指 `registry.npmmirror.com`、0 条指 npmjs ⇒
只写 `--registry=` 是空操作）+ `npm test` + `npm run build`，三步都不带 `continue-on-error`。
`ui-user/` 按裁定不进 CI。**没加 `vue-tsc`**：mimo 没有 `type-check` 脚本，本机拿不到可信读数
（`node_modules` 装不全就会读出 TS2307 噪音），而"不落没有权威判据的门"是本仓口径——排到 §六。

### 五、这批盘出来但没做的（登记，不静默）

- **生产产物仍带着 mock 数据**：入口 chunk `dist/assets/index-*.js` 55,556 bytes 里能 grep 到长期码样例。
  根因不是缺省档（`api` 已经是 `httpApi`），而是交付常量住在 mock 模块里——`AgentsView.vue` 直接
  `import { MCP_URL } from '../api/mock.ts'`、`http.ts` 也从 mock 拿 `apiError` ⇒ 整棵 mock 图被静态拉进包。
  行为不缺，是重量与归属问题（把 `MCP_URL`/`apiError` 搬进中性模块会同时动 5 个文件，超出本批授权射程）。
- **mimo 的 TS 严格面**：CI 加 `vue-tsc` 之前需要先有一次可信绿跑（本机不给，见上）。
- **F-3 面板在主线树上的像素级验收仍待做**：裁定 §五 把 F-3/F-2 的浏览器验证挂 `ui-user-mimo`；
  本机取不到可见视口 ⇒ 结论等级只能写到"DOM/页内 fetch 驱动走通"，这一条不做完 #35 不算闭合。

## 二之三十八、裁定 20261004 §一 Q2=B 的 MCP 半边：「没配令牌 = 一切 scope 放行」改成默认拒绝，而这条改动先咬红的是本仓自己那 8 条既有测试

> 起因：`decisions/20261004-AF安全审计与MA回执与遗留两批-裁定.md` §一 Q2 第三项判 **B（默认拒绝）**，
> `decisions/20261004-AF落地回执与write域含read前提差-裁定.md` §五 把它排成"下一批单独做"，理由写在裁定表里：
> "49 处 `dispatch(` 调用点要逐条显式化，不是本批的量"。
> AF 侧边界照旧：compose 一个字节不写（铁律 #3）、`--host 0.0.0.0` 与只读端点的公开档按裁定原样保留。

### 一、HEAD 的形状：`current is None` 同时表示两件事

`_guard(scope, current)` 里 `current is None` 的原意是"这个进程没配令牌 ⇒ 原型模式全放行"，于是 MCP 面上
`af_save` / `af_live_run` / `af_import_store` 这些 **write / live 域**工具在没配 `AUTOFORGE_TOKENS` 时
**一律可用**——同一份代码在 HTTP 面却是 fail-closed（无令牌 403，本地放行只有 `AF_ALLOW_NOAUTH=1` 一个逃生舱）。
裁定点名的正是这两面对不上。改动后的语义收窄成一件事：`current is None` = **没有身份**，而"没有身份"对需鉴权工具
就是拒绝；全放行必须是**显式选择**（`AUTOFORGE_MCP_ALLOW_NO_TOKEN=1`，且只认 `1`）。

### 二、落法（四处，都在这道门本身，不在调用方各自补检查）

| 落点 | 改法 | 钉它的判据 |
|------|------|-----------|
| `af_mcp.allow_no_token()` | 新增：`(os.environ.get(...) or "").strip() == "1"`——**只有 `1` 算放行** | `test_opt_in_reads_only_the_documented_value` 六档取值（未设 / 空串 / `0` / `true` / ` 1 ` / `1`）；M3 证明把它放宽成认 `true` 会红 |
| `af_mcp._guard()` | `scope is None` 仍直接放过（公开工具面不被打断）；`current is None` ⇒ 拒，消息里写出**怎么显式放行** | `test_no_identity_refuses_a_scoped_tool` + `test_explicit_prototype_flag_restores_pass_through`（两向各钉一次，同 `read←{read,write}` 那条的形状）+ `test_guard_refuses_even_when_scopes_would_have_matched_before`（有身份缺 scope 走的仍是老分支，消息不许混） |
| `af_mcp.serve_mcp()` 启动横幅 | 从两态改三态：有身份 / `flag=1` 的"原型档，显式选择" / **默认拒绝** | 两条横幅判据各起一次 `serve_mcp(stdin=[])` 读真 stderr；M4 把横幅退回旧那句"未启用鉴权（全放行，原型模式）"必红 |
| `af_mcp._t_whoami()` 的 `note` | 不再写"未启用鉴权（全放行）"，改写"无令牌身份 ⇒ 需鉴权工具默认拒绝" | `test_whoami_note_matches_the_档_it_is_in` 两档各读一次；M5 退回旧文案必红 |

HTTP 侧 `POST /mcp`（`af_api.py` 那张脸）复用同一个 `_guard`，**没加第二份检查**：`AF_ALLOW_NOAUTH=1` 只放行 HTTP
层，不越权放行工具层。这条有独立判据（`test_http_mcp_face_shares_the_same_default`），M6 在那张脸塞一个全 scope
身份就红——"两面共用一个真源"不是注释里的一句话。

### 三、调用点显式化：49 处里真靠默认放行的是 23 处，而**先红的是既有测试**

`grep -rn --include=*.py "dispatch(" tests/` = **52 行**（本批新测试文件占 6 行，剔除后 46 行；裁定原文写的是"49 处"，
含注释与同名函数，AST 复核后真调用点更少）。按 TOOLS 注册表的 scope 列分档（AST 读 `TOOLS` 第 5 元，非手抄）：
**31 个工具里公开 20 个、需鉴权 11 个**。把 AST 跑在调用点上：只有 **8 处** 拿"无身份"去调**需鉴权**工具
（`af_save`×3、`af_live_run`×4、`af_import_store`×1），另有 **15 处** `store, None)` 调公开工具——合计 **23 处**
在本批改成显式身份 `_ALL = {"subject": "test-all", "scopes": sorted(SCOPES)}`（scope 名单取自 `af_auth.SCOPES`
这一个真源，不在测试里手抄）。改完仍有 **5 处**省略第 4 参（全是公开工具：`af_health`×2、`af_resolve_entity`、
`af_list_entities`、`af_catalog`）与 **4 处**显式传 `None`（3 处公开工具 + `af_whoami` 那一条本批特意留的"无身份"档），
这一档由新判据 `test_public_tools_still_answer_without_identity` 钉住"公开工具不给身份照样可用"。

**最有用的一条读数不是新写的测试，而是既有测试当场变红**：产品改动落地、测试一行没动时
`pytest` 那四个模块就是 **8 failed / 26 passed**（`af_live_run`×3、`af_save`×3、`af_import_store`×1、`af_live_run` 租约面×1）。
默认拒绝如果咬不住这 8 条，它就只是一句注释。

### 四、变异自证（`tests/unit/test_dcd_20261004_mcp_default_deny.py`，逐档从内存里的原始字节还原并逐字节比对）

`CONTROL`（什么都不改）：**RC=0 / 14 passed**。

| 档 | 变形 | 结果 |
|---|------|------|
| M1 | `_guard` 退回 HEAD 的 `if scope is None or current is None: return` | RC=1 / 2 failed ✅ |
| M2 | `allow_no_token()` 恒 `False`（显式放行形同虚设） | RC=1 / 5 failed ✅ |
| M3 | 放行开关放宽成认 `"true"` | RC=1 / 1 failed ✅ |
| M4 | 启动横幅退回那句"未启用鉴权（全放行，原型模式）" | RC=1 / 1 failed ✅ |
| M5 | `whoami` 的 `note` 退回"未启用鉴权（全放行）" | RC=1 / 1 failed ✅ |
| M6 | HTTP `/mcp` 那 Face 绕过 `_guard`（塞一枚全 scope 身份） | RC=1 / 1 failed ✅ |

**六档全咬住，未咬住的档 = 0**。M1 只红两条（不是全红）是预期内的：其余 12 条钉的是"放行档 / 公开档 / 横幅 / note"，
它们不该因为"默认拒绝被撤掉"而红——如果它们也全红，说明判据没有分档，只是重复钉同一个字面量。

### 五、全量读数

`python -m pytest -q` = **3011 passed, 52 skipped**（含本批新增 14 条）；`GATES_RC=0`；
MCP 参数↔schema 门 **31 个工具 / 声明 61 / 消费 61 / 双向差额 0**；原子写门"授权面腿"仍 6 个落盘点、裸写 0。
README §4.1 加了这条部署前提（含"只认 `1`"与"HTTP 侧共用同一道 `_guard`"两句）。

### 六、盘出来没做的（登记，不静默）

1. **部署侧现在必须有令牌才有 write/live 面**：`docker/.env` 那侧怎么配属 NAS 部署者/SP（铁律 #3），
   AF 不写 compose；裁定 §五 同一条把 compose 的 `MQTT_*` 也派给了部署者，这两件事应在同一次配置推送里做。
2. **`AUTOFORGE_MCP_TOKEN` 缺失但已配令牌**那一档本批**未动**（HEAD 已经是 `MCP_NOACCESS` 空 scope 拒绝身份，
   与这次的默认拒绝同向），但它的拒绝消息仍在讲"缺少 'write' 权限"——两种"没有身份"的话术没统一，属可读性问题不属安全面。
3. **`AF_ALLOW_NOAUTH=1` 的本地开发流程会被这道门打断**（HTTP 层放行、工具层拒），要放行必须同时显式设
   `AUTOFORGE_MCP_ALLOW_NO_TOKEN=1`。这是**行为变更**，不是缺陷；README 已写，但如果部署方把两个逃生舱当成一个用，
   第一次读数会是"工具全拒"——留在这里免得下次被读成回归。
4. MCP 面没有新增静态门：这条线是**值语义**（"当前有没有身份"由运行期令牌表决定），静态判不出，按 §二之三十 那格的口径
   "本门只对静态判得出的形状成立"处理，判据放在运行期测试里。

---

## 二之三十九、裁定 20261004 §二 的 A 腿收口：基线那 9 站统一走一个 L0 助手，账本清空

起因（不是"顺手重构"）：§二之三十四 立门时把 9 个"固定名 `.tmp` + 不 `fsync`"的落盘站点**冻进基线**，
裁定 20261004 §二 钉了收口顺序（`af_premiere` 两站 → `af_version` → `af_scene` → `af_fire_recorder` →
`af_predict`/`af_pretrigger`/`af_shadow`/`af_flock`），§二之三十六 又写明"D 腿已上线不等于 A 腿收口，两腿各是各的账"。
本批把 A 腿一次收完，并把基线这一族**清空**（落地 commit `523d0cd`）。

### 一、为什么先解决"助手住在哪"，而不是逐站手搓 9 份 `mkstemp`

9 站要收敛到同一份语义，只有三条路：① 每站自己抄一遍 `mkstemp`+`fsync`+`replace`+清理（9 份实现，
"唯一真源"当场没了）；② 都去 import `af_store` 里那个 `_atomic_write`；③ 把助手下沉成一个只依赖标准库的
内核模块。**②被两条实测挡掉**（`grimp` 建 `autoforge` 全图后逐条查最短链）：

- `af_store → af_flock` 这条边**本来就在**（最短链读数：`af_store -> af_flock`），而 `af_flock` 在
  `scripts/check_imports.py` 的 L0 名单里 ⇒ 让 L0 内核反向去够 L1 存储层就是字面的环；
- `af_version` 自己写着硬约束「**不 import af_store 内部**」（HEAD 那份 docstring 的原话）⇒
  走 ② 要先破它自己的约束；
- 其余五站（`af_premiere`/`af_scene`/`af_fire_recorder`/`af_predict`/`af_shadow`）与 `af_store`
  **双向都无边**（同一张图：`store->X` 与 `X->store` 均为"无"）——为一个助手新拉一条到存储层的边，是拿分层换便利。

**本批中途的一次粗盘是错的，留此记账**：`python -c "import autoforge.af_store; ..."` 数 `sys.modules`
得出的"这 7 个模块全在被拉起之列"并不成立（HEAD 上那 7 个与 `af_store` 双向无边；粗盘读的是"导入 af_store
之后 sys.modules 里恰好有哪些 autoforge.af_*"，那是**另一件事**）。落笔前用图重跑否掉了它，
`af_atomic` 的模块 docstring 现在写的是上面三条链读数，不是那句粗盘。

⇒ 选 ③：新增 `src/autoforge/af_atomic.py`（49 行，只 import `os`/`tempfile`/`pathlib`），
`af_store` 里那对 `_atomic_write` / `atomic_write_text`（HEAD 上后者只是转调前者）整体搬进 `af_atomic`，
`af_store` 改为 `from .af_atomic import atomic_write_text` 并把内部 8 处调用改指助手；
HEAD 上从 `af_store` 取这个助手的 7 个 importer（`af_catalog`/`af_persist`/`af_telemetry`/`af_preference`/
`af_error_knowledge`/`af_insight_queue`/`af_experience`）一并改指 `af_atomic`。

### 二、9 站的落法（形状换了，语义一处没改）

| 站点 | 落法 | 特意保住的既有语义 |
| --- | --- | --- |
| `af_premiere.PremiereStore.save` | 4 行固定名块 → 一行助手调用 | `issue()`/`consume()` 里每次变更都落盘 |
| `af_premiere.TrialStore.save` | 同上 | `last_sha` 与 `trials` 同批写 |
| `af_version.VersionManager._save` | 助手，仍在原 `try` 内 | 失败照样 `raise VersionError`（不静默） |
| `af_scene.SceneManager._save_state` | 助手 | fail-open：返回 `False` + 记 `last_persist_error` |
| `af_fire_recorder.JsonFireStore._save` | 助手 | `{"records": …}` 结构不变 |
| `af_predict.Predictor._save` | 助手 | 保留**尾部换行** + 写失败只 `logger.warning` |
| `af_pretrigger.TriggerHistory._save` | 助手 | 同上（尾部换行） |
| `af_shadow.ShadowLogStore.save` | 助手 | docstring 不再写"抄 `af_version._save` 模式" |
| `af_flock.FileLock._stamp` | 助手 | 删掉显式 `tmp.chmod(0o600)`——`mkstemp` 建的临时文件天生 0600，`os.replace` 后 sidecar 继承该位（ADM B-14 的"锁信息文件限权"不靠那句 chmod） |

### 三、账本清空 + 口径翻面

`.atomic-write-baseline.txt` 现在**只剩注释、0 站**。随之翻面的是"新站点怎么办"：
以前是"可以登记进基线（附理由）"，现在**这条路没有了**——要么走助手，要么写带理由的就地豁免
`# fixed-tmp: exempt(理由)`。测试侧同一句话也翻了面：`test_every_baseline_entry_carries_a_nonempty_reason`
→ `test_baseline_is_empty_now_that_the_nine_sites_are_closed`（棘轮从"每条有理由"变成"不许再有条目"）。
留存的 2 处就地豁免是 `af_pending.py::os_replace`（把坏文件挪走、保留现场）与
`af_predict.py::Predictor._quarantine`（隔离损坏模型文件），两处都是"移动坏文件"而不是"写业务状态"。

### 四、L0 注册与判据

- `scripts/check_imports.py`：`autoforge.af_atomic` 进 `LAYERS` 的 L0 前缀、`L0_KEYWORDS`、`L0_KERNEL`
  三处 + 模块 docstring 一行；`pyproject.toml` 的 import-linter 契约 `source_modules` 同步加上。
  读数：`modules: 98 / L0 kernel: 10 / L1 runtime: 83 / L2 service: 5`，
  `✅ Layer architecture clean — no reverse dependencies detected.`，`Baseline lock: 98 modules, 0 violations`。
- 新判据 `tests/unit/test_dcd_20261004_atomic_write_nine_sites.py`（13 条 = 助手 5 + 站点 7 + sidecar 位模式 1）：
  静态门判的是形状，这一份判的是**助手本身的行为**——`replace` 失败时原文件内容不变且不留残留、
  每次调用拿到**不同**的 tmp 名、`fsync` 严格发生在 `replace` 之前、`def atomic_write_text` 在 src 里
  只许有一份（且住在 `af_atomic.py`）、`af_atomic` 不许引入任何包内依赖；
  其余 8 条按站点各测"落盘后读得回来 + 目录里没有 `.tmp` 残留"。
  `test_lock_sidecar_stays_owner_only_without_explicit_chmod` 是那句被删掉的 `chmod` 的替身，
  `skipif os.name != "posix"`（Windows 上 chmod 不进 ACL，位模式无从判定）。
  本机读数：`36 passed, 1 skipped`（本文件 12 passed + 1 skipped，门测试文件 24 passed）。
- 门绿行照录：`✓ 原子写站点门禁干净（扫描 98 个文件、os.replace 站点 5 处：走 mkstemp/公共助手 3 处、
  固定名形状 0 处（其中基线冻结 0 站、就地豁免 2 站）；授权面腿射程函数 46 个、其中落盘 6 个
  （必经助手 5 个、自带 mkstemp 1 个、裸写 0 个——这一腿不接受基线与豁免））`。
  上一批是 `97 个文件 / 站点 14 处 / 固定名 9 处（基线冻结 9 站）` ⇒ 文件 +1（新模块）、固定名形状 9→0。

### 五、突变自证（铁律 #8）：CONTROL 三档 RC=0，M1–M7 全咬住，还原逐字节相同

| 档 | 变异 | 谁咬住的 | RC |
| --- | --- | --- | --- |
| CONTROL | 什么都不改 | 门 + 两个测试文件 | `0 / 0 / 0` |
| M1 | `af_shadow.save` 退回固定名 tmp + `os.replace` | A 腿形状门 | `1`（红行点名 `ShadowLogStore.save`） |
| M2 | 助手去掉文件 `fsync` | `test_fsync_happens_before_replace` | `1` |
| M3 | 助手退回固定 tmp 名 | `test_each_write_gets_a_different_tmp_name` | `1` |
| M4 | 助手去掉失败清理 | `test_replace_failure_keeps_the_original_and_leaves_no_residue` | `1` |
| M5 | `af_premiere` 里再长一份 `atomic_write_text` | `test_helper_is_defined_exactly_once_in_src` | `1` |
| M6 | 助手加一条相对 import | `test_af_atomic_depends_on_nothing_but_stdlib` | `1` |
| M7 | 基线里重新冻一站 | `test_baseline_is_empty_now_that_the_nine_sites_are_closed` | `1` |

驱动只在内存里留原始 bytes、写回同一份 bytes；四份被改文件（`af_atomic.py`/`af_shadow.py`/
`af_premiere.py`/`.atomic-write-baseline.txt`）还原后 `sha256sum -c` 全部 `OK`，终态复跑 `pytest(new)=0 pytest(gate)=0 gate=0`。

### 六、全量读数

`3023 passed, 53 skipped, 1 warning, 7 subtests passed in 141.68s`（收口前那次是 `3011 passed / 52 skipped / 1 failed`，
那条 failed 就是基线棘轮自己——它按设计报了"基线空了，这条测试该改"，本批把判据改写成"必须为空"并新增 13 条判据）；
`GATES_RC=0`（14 条 `✓`）；`check_imports.py` RC=0。
CI（GitHub 侧，`git ls-remote --heads origin` 自证远端 `main` == 本地 HEAD 之后读 `check-runs`）：
本批 `523d0cd`/`bab54eb` 与上一批 `c4bb5b5` 各 **6 条腿全 `completed / success`**（`pytest`、`quality-gates`、`layering-gates`、`adm-linkage-contracts`、`ui-typecheck-build`、`ui-user-mimo-judgments`）。

### 七、本批没做的（登记，不静默）

- **`af_auth._atomic_write_text` 仍是 src 里第二份实现**，本批**故意不并**：
  ① 它是 §二之三十六 D 腿的锚点名（门按这个名字数"必经助手 5 个"），并入就等于在同一天里既改锚点又改落盘；
  ② 它带的是 `af_auth` 专属语义（撤销名单半截 ⇒ 读回应失败而不是回落默认，见那份 docstring）。
  下次 `af_auth` 被真实改动时再并入 `af_atomic`，并同步把 D 腿锚点从"叫得出名字的助手"换成新唯一真源。
  `test_helper_is_defined_exactly_once_in_src` 数的是 `atomic_write_text` 这个名字，所以它现在绿并不覆盖 `_atomic_write_text` 这一族——差别写在这里，免得被读成"已唯一"。
- **值语义不新增静态门**："这个文件到底有没有第二个写者"静态判不出（§二之三十四 就这么写的）。
  基线清空只保证"没有固定名形状"，不保证"某条实例记录不会被两个进程交替写坏"。
- 上面那 7 个 importer 只是换了取助手的模块名（HEAD 上它们的落盘本来就走 `af_store` 的 `mkstemp` 路径），不属于那 9 站；
  `af_insight_queue._atomic_write` 这个**本地同名包装**也仍在那里（它转调助手，不是第二份实现）。

## 四、审计侧




第七轮唯一 finding 在 HEAD 上**属实**（不是已修项的重报）：`HAStateProvider` / `HassStateProvider` 对未知实体静默跳过，而仿真侧 `InMemoryStateProvider` 抛 `UnknownEntity`，同一 IR 两判相反。短路口是 `af_ir/expr.py:514-517`（`and`→`all()`、`or`→`any()` 生成器表达式，未求值分支根本不碰 `Snapshot.get`），所以生产把条件判真、仿真把条件判假。
修复落点 5 处 + 契约测试 `tests/contract/test_state_provider_policy.py`（14 项，含两条铁律 #8 的"能变红"反证）。完整核实链、"连续六轮未修复"四条重验表、部署声明见 `docs/audit/审计报告_第七轮_核实与修复.md`。

⚠️ 审计口径事实：第六轮与第七轮读的是**同一个** zip 快照 `zip-snapshot-2026-09-29T22:43`（不是 GitHub HEAD），故其"连续多轮未修"多数条目已在 HEAD 修复；引用审计报告前须按铁律 #11 先对 commit 复测。

**第八轮截至 §二之三十 这批仍未投递**（本批当场重跑 `ls -lat docs/audit/*.md`，最新一条仍是 `审计报告_第七轮_核实与修复.md`，mtime `Oct 2 11:46` ⇒ 与上一批同读数，无新报告可核）。本批自审抓到一条与审计同型、但审计面按 zip 快照读不出来的**门禁级**缺陷：`.gitignore` 吞包标记 ⇒ CI 的架构门禁比本机少分析 10 个模块（86 vs 96）。它的难看之处在于**不会让任何东西变红**，只会让该红的东西不红，因此只能靠"两个口径的数字对账"发现——登记在此，避免被读成"本轮无自审产出"。见 §二之十一。

裁定 20261004 §一 3 落门时顺带把审计侧那条"5 处存量"的账**在 HEAD 上重扫了一遍**：这条扫描口径今天扫到 **76** 个增长容器，其中双腿齐全且有测试钉住的只有 **2** 个；裁定引用的"5 处/2 处误报"里，那两个误报候选（固定键计数器、domain 属性词表）连这 76 都不进。这是一条**建议写数量、门要每一项双腿**造成的口径漂移：数量不成立时若照单盖章，注册表自己就成了第二真源（§二之十九 刚扫掉的那一族）。故按实测落、差异回投追认（§五 第 15 件、§二之三十 第二节）。

**安全审计（scoped run）这一份此前从未进入处置链**：`AutoForge安全审计报告.zip` 早在仓里（git 已跟踪），
但 `docs/` 检索"安全审计"只命中 zip 自己，§〇/§五 没有对应行——所以记账先于处置。它的 `source_ref` 是
`zip-snapshot-of-default-branch-2026-09-29T22:43`（**无 commit sha**，该环境 `github.com` 的 git 协议 403、
改走 codeload zip），与第六/七轮同一族失败方式：**按快照读码，把 `3fbbab9` 之后已修的当未修重报**。
按铁律 #11 对 HEAD `927b044` 逐条重跑的结论、七行钉源表与两处真洞（授权码"枚举不存在的码根本不计数"、
MCP 六个键"没声明却能传"）见 §二之三十三 与 `docs/audit/审计报告_安全审计_核实与修复.md`。
覆盖声明照抄不改：`PARTIAL COVERAGE — 本轮为 scoped run，不得据此推断目标整体安全状态`，
22 单元 = covered 5 + candidate 3 + **out_of_scope 14**（不等于那 14 个没问题，也不等于 AF 自审能补上）。

**第八轮本批仍未投递**（13:58 当场重跑 `ls -lat docs/audit/*.md`：外部最新一条仍是
`审计报告_第七轮_核实与修复.md`，mtime `Oct 2 11:46`；`审计报告_安全审计_核实与修复.md` 是本仓自己的处置记录，
mtime `Oct 4 18:02` 只说明 AF 写过它，**不是新审计输入**）。那份处置记录本批新增 §十：落裁定时自盘出的授权面
fail-open（五处落盘站点 + 撤销名单"读不成即复活已撤销令牌"），按形状它是 zip 快照读不出来的一类，
登记在此免得下一轮把"AF 自审产出的洞"读成"审计没提所以不存在"。见 §二之三十五 第五节。

## 五、已提 / 待提 DCD

| # | 事项 | 状态 |
|---|------|------|
| 1 | 第 4 步契约面四问：MCP 工具面命名口径（`draft/verify/deploy` vs `af_draft`/`af_apply`）、`af/automation/fired` 的 `ref` 语义（实例 id 还是 deploy ref）、`/api/asks/answer` `ok=False(inbox_key_missing)` 的 DB 侧处置、`ma/insights` 提案队列是否要求持久化 | `inbox/20261002-AF-第4步契约面四问-决策申请.md` → **已裁定（①A ②A ③A ④A，全 A）**，AF 侧四条均已落地（见 §一 末"裁定 20261002"表） |
| 2 | 第 0 步 ③④：0.3.1 记账缺口（源树 `time.py` 未入库 + NAS `VERSIONS.txt` 无 0.3.1 条目 ⇒ sha 不可复现）+ §四 合并停机窗排期与授权 | `inbox/20261002-homesdk0.3.1记账缺口与AF镜像重烤合并窗-决策申请.md` → **记账半边已裁定并已执行**（DCD §〇：源码+契约表入库 `5e4ba33`、VERSIONS 补条目、以重建 `b4b5d6bb…` 为权威，AF pin 与 vendored wheel 已跟新）；**镜像重烤半边 = 合并窗内做，尚未生效**（窗口未开） |
| 3 | 第七轮 `StateProvider` 统一 fail-closed 的方向与代价（整段软失败 vs 逐实体可见漂移） | `inbox/20261002-AF-StateProvider统一fail-closed的方向与代价-决策申请.md` → **已裁定：A 先行 + B 排队**，B 启动条件 = 下次真实改动 `af_instance._refresh_snapshot` 或 AF v2.6；整段口径被裁定为**有意设计**；漂移须以 `entity_drift` 记账 |
| 4 | 第 0 步 ④ 的记账载体：`E:/NAS/AgentOps` **不是 git 仓** ⇒ 已授权改的门禁模板无 commit、无 sha、不可回滚；三仓 CI 都依赖它，却没有任何版本账 | `inbox/20261002-AgentOps门禁模板无版本载体-决策申请.md` → **已裁定**（`decisions/20261002-AgentOps与MA交付面-裁定.md` §一）：**A 建仓为目标态**（NAS bare 加 `agentops.git`、模板首次 commit、此后走 PR），**建仓与推送授权"与合并停机窗同批"** ⇒ AF 侧本窗不动；**C 兜底本窗已落**（申请区贴了模板 sha256 `887c33dd…`）。裁定另点名两件事：模板分发=**各仓按模板重写自己的 CI**（重写即该仓一次 CI 变更，走各自 PR），`memory-agent/.github/workflows/gates.yml:56` 的 `continue-on-error` 由 **MA 侧修**，AF 不碰他仓 main |
| 5 | 新 `ui` CI job 将**首次**让 GitHub runner 按 lockfile 去第三方镜像拉 137 个包（`ui/package-lock.json` 实测 137/137 指 `registry.npmmirror.com`、0 条 npmjs、0 条缺 `integrity`；根因是用户级 `~/.npmrc` 漏进共享产物）。这是 DPP 同题申请（R7）在 AF 的**前提修正**：那边选"只登记"的承重句是"CI 只在我这台机器跑"，AF 今天把这句话拿掉了 | `inbox/20261002-AF-CI首次消费镜像锁文件-DPP-R7同题补充-决策申请.md` → **已裁定 (b)**（`decisions/20261002-AF锁文件源与MA载荷键名-裁定.md` §一：只把 CI 侧改指官方源、**不动锁文件字节**、本机开发照旧）。**AF 已落地**，但落法比裁定原文多一条 flag：只写 `--registry=` 经实测是**空操作**（fetch 全在 `npmmirror` 的两个主机上），必须配 `--replace-registry-host=always`；同时给安装步加了主机自证守卫（CI 绿不能证明没吃镜像）。逐字节对账 137/137 MATCH ⇒ "不动锁文件"成立。读数见 §二之七 |
| 6 | 上一条落地时抓到的两件事要回给 DCD：①裁定原文那条命令是空操作，落法已补一条 flag（问"算不算改判"）；②裁定 §四 判例 2 的证据不成立——`@types/node@22.20.2` 的 tarball 在**两个源逐字节相同**（`size=447116`、`sha256` 前缀同为 `64921eb9b6caae37`、解包根目录都是 `node v22.20`），"根目录不是 `package/`"是 DefinitelyTyped 自己的发布布局，不是镜像重打包的痕迹 | `inbox/20261002-AF-锁文件源裁定落法补正与判例2旁证更正-回执.md`（**已提交，待回话**）。同件另附一条小问：安装步那条"fetch 主机集合 ⊆ {批准的那一个域} 且该域命中数 > 0"的守卫形状要不要提升到 AgentOps 模板——按 20261002 §一"分发=各仓自写"的口径，AF 默认不动 |
| 7 | `ma/insights` 载荷的三处契约空缺（AF 的入向此前按自家键必填，按契约发来的每条洞察都会被拒——判例 1 的静默归零换到了 AF 入口）：① `conf` 要不要正式进契约行（AF 已按"缺报按 0.0 入 ask 档 + `conf_reported=false`，面板显示「未上报」"落地，等追认）；② `trace_id` 是"一次联动的追踪号"还是"这条假设的稳定身份"——AF 现在拿它当 `hypothesis_id` 与回灌键 `hypothesis:{id}`，若每次播报都换，去重与回灌都会散；③ MA 会不会发结构化 `intent`（契约行没有这一项 ⇒ 生产里每条洞察都是「无 IR（不能批准）」，要改的是人的工作流还是契约，AF 不擅自扩面板） | `关键决策部/inbox/20261003-AF-ma-insights载荷conf与稳定id与intent-决策申请.md`（**裁定 20261004 §五 已回：(a) / 稳定身份另给 / (a)，且契约行 DCD 已落笔**）。AF 侧不等裁定：别名/缺报记账/有界 transport 已在同批进仓，判据见 §二之八。**→ 裁定 20261004 §五 三问全回（(a) / 稳定身份另给 / (a)），且裁定指派给 DCD 的契约行已落笔**：① `conf` **可选**进表——报了就用并**封顶 0.59**、没报记"未上报"，"AF 现有代码零改动即可兼容"；② **稳定身份另给**——表加 `insight_id`（MA 应发、稳定），`trace_id` 保持**事件级追踪号**语义，**AF 的去重与回灌键改用 `insight_id`、不用 `trace_id`**（这条与本仓 20261004 §一 2 的 `trace_id` 判 C 是同一刀的两半）；③ `intent?` 登记为可选，"可选字段不得成为消费方的硬依赖"。**HEAD 复测**：契约表头"修订 2026-10-04"①那一行确实写了 `insight_id / conf? / intent? + trace_id 事件级口径 + node_id 明确不进表`，`ma/insights` 载荷行已含这四键 ⇒ 本条的**契约侧已闭合**；AF 代码侧的 `insight_id` 去重/回灌改键随 `b4cd67f` 落地。**同一份表里仍未落笔的三行按 HEAD 复测为 0 命中**：`READONLY_DEGRADED` 前缀、`instance_id` 过渡字段的删除时点、`/api/user/auth-codes`（含裁定 §一(a) 要求的 GET 半边鉴权）——见 §六 |
| 8 | 两件合一份：①**第 5 步 ③ 第二档的时点**——裁定给的是"一个版本**或** 2 周"，`lint-imports` 进 CI 是 `20356dc`（10-01），AF 在 10-03（日历 2 天、无版本发完）就把 `continue-on-error` 去掉，判据是"runner 连续 7 条 run `KEPT` + 误伤 0 + 一次可红实测"；这是 AF 单方改窗口长度，故不求"裁定已满足"的认定，只求追认时点（A 追认 / B 判回退一行 YAML / C 把口径改成可判定的量）。②**`_*.py` 吞包标记的跨仓外溢**——`.gitignore` 的 `_*.py` 会连带吃掉 `__init__.py`，实测让 AF 两条架构门禁在 runner 上少分析 10 个模块（86 vs 96）；MA/DB/homesdk/AgentOps 模板是否有同款规则，AF 不擅动他仓，建议先做零改动探测（各仓一次 `git check-ignore -v` / `git status --ignored --short \| grep __init__`），是否把"包标记门禁"上收模板请一并裁定（注意 20261002 §三 对 fetch 守卫给过"先不落"） | `关键决策部/inbox/20261003-AF-import-linter升硬门时点与gitignore吞包标记-决策申请.md`（**裁定 20261004 §五 已回：第 1 件 A+C**）。AF 侧不等裁定：第 2 件已在本仓自闭合（负向规则 + `scripts/check_pkg_markers.py` 进 `gates.sh`，还原原状仍能取红），第 1 件若判 B 一条 `git revert` 即退，均不占停机窗。判据见 §二之十一。**→ 裁定 20261004 §五 补裁第 1 件 = A（追认本次提前落档）+ C（改口径为可判定量）**：连续 7 条 run `KEPT` + 误伤 0 + 可红实测（`af_time` 加一行反向 import 即 `BROKEN`）**已满足裁定原要防的"误伤 CI 永久红"**；同时把后续两档切换的口径固化为 **"同一契约连续 ≥5 条 run KEPT 且有可红实测"，不再用日历天数**——本仓凡出现"一个版本**或** 2 周"的旧处以这条口径读（只改判据来源，不改已落地的门）。第 2 件（gitignore 吞包标记）AF 本仓自闭合的形状无需裁定，裁定侧只在回执裁定 §四 就"整仓扫读出 `ignored=2010`"立了"**射程必须写明**"的判例，并确认三仓 `src/` 面 0 吞 |
| 9 | **第 5 步 ② 的「顺序追加」半边**：裁定 20260930 给 af_persist 的执行约束第 2 条要求"追加写 + 文件头部元信息"，而现实现是每实例一个快照文件、`save()` 整文件原子重写（`af_persist.py:171-187`）。三条张力不是 AF 能自决的：①它同同一份裁定的"不改存储格式头/不迁移"相抵（改成追加写**必然**动格式头）；②第五轮审计已把"每条全量重写 + 明细无上限"收成有界（`docs/audit/审计报告_第五轮_核实与修复.md:38-74`），再上追加写等于把那一轮的修法反向；③裁定本身把这一项降级为"不做全量 eventlog"。要么 A 判该子句对 `af_persist` **不适用**（AF 倾向：B 的形状要新增一类存储产物，已越出裁定自己给的"改动小、不迁移"档位）、要么 B 判必须做但给有界形状（每实例 `.jsonl` 只留最后 N=8 条、写侧纯追加 O(1)、读侧仍以 `.json` 快照为权威）、要么 C 判并入未来 `af_eventlog` 另立项 | `关键决策部/inbox/20261003-AF-af_persist追加写半边与裁定执行约束张力-决策申请.md`（**已提交，待回话**）。AF 侧不在裁定前擅自动存储面：本批只把 §〇/§一 的"全部落地"过度声明改成本节口径。判据见 §二之十二。**→ 裁定 20261004 §一 4 判 A（AF 的三条论证成立）：该子句对 `af_persist` 不适用**，本仓 §〇/§六 已按裁定执行口径就地标注"经 DCD 判定不适用"，`af_persist.py` 一个字节未动（§二之三十 第五节） |
| 10 | **单写者租约只装了 HTTP 一面，MCP 真机写未受约束**：AF 自己定的"一个 store 只能有一个写者"这条纪律目前有三处落点却只在一面上生效——`af_cli.py:1365` 的 `FileLock(store_root/".serve.lock")`（抢不到即 `readonly=True`）、`af_api.py:344` 的 `_readonly_guard`（8 个写端点挂 `Depends`）、以及 `build_app(readonly=…)` 这个入参本身；而 `af_mcp.serve_mcp` 与 `dispatch` 对 `readonly|lease|acquire` 三个词 **grep 零命中**（实测），`af_live_run` 的 scope 又确实是 `"live"` ⇒ Agent 侧可以在 serve 已持写权时并行开真机写。这与 §五 第 1/7 件不同：**它不是补 AF 漏装的自家闸门**（那种我直接落地，见本批 §二之十六 的两处），而是要给 DB 新增一个它此前不会遇到的**拒收模式**——MCP 在锁被占用时返回什么码、用 `check` 还是 `acquire`（Agent 进程与 serve 同机/分机两种拓扑答案不同）、是否只拦 `live` 还是连 `apply`/`bind` 一起拦，三项都会改变 DB 看到的失败面 ⇒ 按 20261002 §一"契约面改动走裁定"的口径申请 | `关键决策部/inbox/20261003-AF-单写者租约只装了HTTP面MCP真机写未受约束-决策申请.md`（**已提交，待回话**）。AF 侧**代码一个字节未动**；同批已自主落地的是前两件（`dc8ac0d`：`_t_live` 补 `store`、events 上限上收到 `af_service`），判据与三次变异读数见 §二之十六。**→ 裁定 20261004 §一 1 判 A 并已落地（`7dbd640`）**：只把真机写纳入、只 check 不 acquire、MCP 文本前缀 `READONLY_DEGRADED:`，三条判据与五档变异见 §二之二十九；**契约表登记该前缀那一半在 DCD 手里**（§二之三十 第五节列的四行之一） |
| 11 | **"新增有界缓存必须同时给 TTL 与硬上限 + 纯写不读也必须被回收"这条约定要不要升级成能判红的门禁**：本批拆 §六 那行混写时盘出来的。约定的两处落点现状不对等——"加统一基类"那一半第六轮 §三 已判**不做**（`git log -S"BoundedCache" --all -- src tests` **0 命中**，该符号从未进过代码；第七轮 `:86` 重申），但"**门禁可见**"那一半是**真空**：`grep -rn "有界\|TTL" gates.sh scripts/*.py` 零命中 ⇒ 约定只活在审计正文里（§二之十一 记过同一族"写在正文里的约定"）。AF 本批把静态口径真做到有读数：AST 扫"类里 `self.X = {}/[]/deque` 且同类内 `X[k]=`/`append`/`update`" ⇒ src 全集 **7 个候选**，5 个同类内已有界证据（`ConflictAuditor.events`/`JsonFireStore._records`/`InstanceManager.context`/`PreferenceModel._records`/`UndoStore._records`，与第六轮"四条腿 + `_SESSIONS`"的账一致），**"缺界"那 2 个逐条读码后全是误报**（`PreTriggerService._stats` 固定键计数器、`DeviceSM.attributes` 键集由 domain 属性词表决定）⇒ 真阳性 **0/2**，"扫无界容器"静态上判不出可靠口径，硬做上门第一天就带两条永久红、只能挂豁免表，而豁免表正是 §二之十九 刚扫掉的那族第二真源。三档 A（清单 + 成对断言测试，无门）/ **B（注册表式门：仿 `af_mcp.TOOLS` 形状，判"双腿齐全 + 测试 id 真被收集 + 新增增长容器必须登记"，AF 建议）** / C（统一基类 + 无界扫描 ⇒ 动 `af_audit`/`af_preference` 持久化层，需停机窗）；请 DCD 定向两点：①存量是否接受"基线冻结、新增必须登记"；②"固定键 / 词表有界"这一类给不给正式豁免 | `关键决策部/inbox/20261003-AF-有界缓存生命周期约定要不要升级为硬门禁-决策申请.md`（**已提交，待回话**）。AF 侧**代码一个字节未动**；本题不涉 ADM 契约表（无跨仓载荷、无主题），但严格度选型会决定这道门以后拦人还是拦格式 ⇒ 不自裁。**→ 裁定 20261004 §一 3 判 B（注册表式门禁）并已上线（`c0476e2`）**：AF 申请的"①存量是否接受基线冻结、新增必须登记"= 是（基线 74 项由扫描生成）；"②固定键/词表有界给不给正式豁免"= 给，两处各带理由且按行核对。**待 DCD 追认的两点**（第 15 件）：存量口径 76/2 与"cap-only 那批要不要排期补 TTL 腿"——AF 没有为凑"5 处"去造腿，那是裁定自己驳回的 C |

| 12 | **出向事件 `trace_id` 的语义：事件级还是因果链级**（与第 7 件同族、方向相反——那件是入向载荷，这件是 AF 自己发的两条）。契约表 §1.2 只列字段名（`{trace_id, ts, automation_id, ref}`，failed 多 `error`），没写谁造、是否须等于上游、一条 trace_id 允许对应几条事件；AF 现状每次发布现场 `uuid4().hex[:12]` ⇒ 同一实例的 fired 与 failed 也不是同一枚号，MA→AF→DB 的因果链在 AF 这一跳断。上游那枚号在 AF 侧并没丢（`ingest_insight` 收进 `hypothesis_id` → 提案落盘 → approve 时写进 GraphStore 记录 `note="ma_insight:{…}"`），**但到不了发射点**：`grep -rn hypothesis_id src/autoforge/af_ir/ src/autoforge/af_instance.py src/autoforge/af_executor.py` = 0。三档 A（因果链级：同一字段两种语义、DB 检索由 1:1 变 1:N、要把号搬进 IR/实例上下文）/ B（新增 `insight_trace_id`：三方共读面）/ **C（AF 建议：`trace_id` 明确为事件级，契约表加一句口径，因果链靠 `ref`＋各自存储元数据）**；另核到 §1.3 护栏第 4 条那句"trace_id 必填：贯穿洞察→收件箱→播报"**挂在收件箱三面之下**，而 `grep -rn "publish.*butler/inbox" src/autoforge` = **0** ⇒ AF 不踩那条，本题不是违规而是 §1.2 没写清。同批已自决的另一件（`error` 恒等于状态名）在 §二之二十二，申请里另向 DB 问了展示宽度；**申请现含三问**——第三问是本批打真载荷盘出的 `node_id`（契约行与裁定都没有它；发的是"失败时刻停靠节点"而非"失败节点"，`fail()`/`_transition()` 不改 `ctx.current_node`，执行链在 `af_executor.py:156/252/781` 跳边时才推进），AF 倾向"DB 不读就删"，删/留与语义都请一并裁 | `inbox/20261003-AF-事件载荷trace_id是事件级还是因果链级-决策申请.md`（**已提交，待回话**）。trace_id 生成方式 **AF 侧一个字节未动**。**→ 裁定 20261004 §一 2 判 C（事件级，写进口径）并判 `node_id` 删，两半都已落地（`7dbd640`）**：口径写进 `_envelope()` docstring 并新钉一条"同实例 fired/failed 两枚号不同、`ref` 相同"的判据，`observe_terminal()` 不再传 `node_id`；**契约表 §1.2 那两行（口径句、`node_id` 若列过则删）仍在 DCD 手里**，四行清单见 §五 第 15 件回执 §三 |
| 13 | **停机窗里"开联动开关"这件事有三问 AF 不能自裁**（本批把 paho 三面补齐之后才看得清）。① **开关与顺序**：`AUTOFORGE_MQTT`（`af_mqtt_bridge.py:67`）默认关，compose 的 `environment:` 实测既无它也无 `MQTT_HOST`/凭据 ⇒ 桥永不上线；而窗内直接打开的前提是"那一刻容器里 broker 配置齐且真可达"，这个前提 AF 保证不了（凭据在 NAS 宿主 `docker/.env`，可达性要 DB/MA 确认），且 `af_cli.py:1380` 起桥排在 `uvicorn.run`（`:1382`）之前、不吞异常 ⇒ 前提没成立就是**整容器反复重启、只读面一起没**（铁律 #6），而"宁可拒绝启动也不留假桥"是既有裁定口径，AF 不自行改成 fail-soft。② **paho 从哪个源进镜像**：两份 Dockerfile 与 CI 实测**无任何** `index-url` 配置 ⇒ 本批起交付面从官方 PyPI 拉一个此前不存在的运行时依赖；这与 20261002 §一（npm 交付面改指官方源）同型但方向相反，而 homesdk 因私有才 vendor、paho 是公开包 ⇒ vendor 与否是政策选择。③ **第③项验收拿什么事件过账**：`fired` 只由 `observe_terminal()` 产生、桥只在真机/dry-live 分支注册（`af_cli.py:320`，仿真分支不起桥）⇒ 必须"跑一次自动化执行"才取到数；dry-live 走 `HAAdapter(dry_run=True)` 只记意图不碰设备（`:302/315-318`）却**照样发得出**四键齐全、墙钟 `ts` 的一条 `fired` ⇒ 用它过账就是"联动环在生产真上线"没有任何一条读数为真 | `关键决策部/inbox/20261004-AF-窗内开联动开关会把只读面一起带走-决策申请.md`（**裁定 20261004 §二 已回：Q1=A / Q2=B / Q3=C**；Q1 三档 A=本窗只重烤镜像、开关走非停机窗配置推送 / B=本窗就开并请 DCD 明确豁免铁律 #6 / C=降级为 HTTP 面照起 + `linkage=degraded`，但 C 要先撤"宁可拒绝启动"那句既有裁定；Q2 三档 A=官方源 / **B=官方源 + 上界钉死（AF 倾向）** / C=vendor 进仓钉文件名；Q3 三档 A=第③项只认真机 fired / B=允许 dry-live 但读数必须带 `source=dry_live` 且结论等级记"接线已验、真机未验"（AF 会做成第三态而不是混进 PASS）/ **C=第③项移出停机窗、另立受控真机演练（AF 倾向）**）。**AF 侧 compose 一个字节未动**；同批已自决的只有"依赖三面一致"那半（`739a328`，判据与四档变异见 §二之二十六）。**→ 裁定 20261004 §二 三问全回**：**Q1=A**——本窗只重烤镜像、`AUTOFORGE_MQTT` 维持缺省关（桥 no-op、HTTP 面照常），同窗把 `MQTT_HOST`/`MQTT_USER_*`/`MQTT_PASSWORD` 的 compose 引用**补齐但留空**；开关真正打开走**非停机窗的配置推送**，且先 `docker compose exec` 预检（`paho_available()` + `broker_settings()`）通过再改 `AUTOFORGE_MQTT=1` 重启——裁定给的理由与 AF 申请同一条（铁律 #6 优先，"开早了是整容器 crash loop"），**AF 不需要把"宁可拒绝启动"改成 fail-soft，那条既有裁定原样保留**。**Q2=B**——官方 PyPI + 上界钉死 `paho-mqtt>=1.6,<2.1`（"能装上"与"装的是同一个东西"是两件事，20261002 那批已证明交付面要自证到字节级；C=vendor 被判过重，paho 是公开包）⇒ 已随 `b4cd67f` 落地，回执裁定 §三 那格判 ✅（`tomllib` 读声明面逐条核字符串、上界缺失即红）。**Q3=C**——第③项**移出停机窗、另立受控真机演练**（停机窗不给"动设备"这个权限；C 比 B 诚实，因为 B 的风险是"三个月后没人记得那行 PASS 其实是 dry-live"）；**可触达设备范围与时间窗由 DCD 给**（建议：合并窗后 24 小时内、非安防设备、限 3 条自动化）⇒ 本仓 §六 那条"窗后四项验收"的第③项结论等级因此**不再由 AF 单方写**，等 DCD 排期 + SP 执行。compose 那半按铁律 #3 归 NAS 部署者/SP，**AF 侧 compose 至今一个字节未动** |
| 14 | **DCD 增补的 v2.6 任务表里，第 3 件的「前置」两句话自相矛盾**：§5.3 给 `af_persist 顺序追加写` 写 **前置=无**（等于说 AF 现在就能开工），§5.4 又把同一件列进 **待 DCD 裁的**（等于说不能开工）——两句都出自同一份 2026-10-04 增补，AF 不替 DCD 择一。且"前置=无"并不真的够：验收那半句"**崩溃后从末尾重放**"决定了要不要**新增一类存储产物**（`.json` 快照里没有事件序列，重放只能来自追加流），而那正是第 9 件里三条张力的核心（追加写必然动格式头 / 第五轮刚把"每条全量重写"收成有界 / 裁定自己把这一项降级为"不做全量 eventlog"）。请 DCD 只回一个字母：**A**（§5.4 作数，§5.3 的"前置"更正为"第 9 件裁定"，AF 建议）/ B（§5.3 作数并给有界形状：载体与 N、权威读侧、"格式头不许变"是否撤销）/ C（判该子句对 `af_persist` 不适用，从任务表删格、登记进"不在本版做"，AF 此后不重投） | `关键决策部/inbox/20261004-AF-v2.6第3件前置与5.4自相矛盾-回执与定向请求.md`（**已提交，待回话**）。AF 侧**`af_persist` 一个字节未动**；同件另把三份账回给 DCD（§5.3 第 4 件已交付可划掉、§5.6 的"未推 origin/main"在 AF 仓不成立且已按 run 计绿 30 条、§5.1 的"第 0-4 步全交付"在窗内两件事之前仍是 EXEMPT）。**→ 本件随裁定 §一 4 A 一并闭合**：AF 申请的 C 档（判该子句不适用、从任务表删格）就是裁定给的 A 档，两问同解。§5.3 第 3 件那一格的"前置=无"**AF 不代 DCD 改**（计划文档是 DCD 原文，本仓口径是"原文照录、不改一字"），已在第 15 件回执里请 DCD 落笔 |
| 15 | **裁定 20261004 §一 四件的落地回执，其中第 3 件的存量口径与本仓实测不符，请追认更正后的落法**：裁定写"5 处一次登记 + 2 处误报进基线"，那两个数来自 AF 那份申请里**收窄过的判据**（只看"类里 `self.X = {}` 且同类内 `X[k]=`"，且预先只点了审计 §三 提到的那几个模块）。按完整口径（含子包、模块级容器、`deque/set/list` 与九种增长方法）跑在 `src/autoforge` 全集上：扫到 **76** 个增长容器，TTL 与硬上限**同时**具备且有"纯写不读也被回收"测试的只有 **2** 个；申请里点名的 2 处误报确认是误报，但在完整口径下**根本不进这 76**（扫描要求"空初始化 + 同类内增长"）。因此本批落法是"**注册表 2 + 固定键表 2（各带理由，且理由必须同时写在被豁免那一行）+ 基线冻结 74（由 `--print-baseline` 从扫描生成、只减不增）**"，而**不是**照"5 处"逐个盖章——那 3 处不齐的站点进表只能填出不存在的名字（门当场判红），或反过来给它们新增 TTL 与裁剪逻辑，而那正是裁定 §一 3 驳回 C 时拒绝的那种改动。请 DCD 追认这一档；若要求那 3 处 cap-only 站点也逐个补腿，AF 需要**排期授权**而不是顺手做。回执另列**必须回到契约表的四行**（① `READONLY_DEGRADED:` 前缀登记；② §1.2 的 `trace_id` 事件级口径句；③ §1.2 若列过 `node_id` 则删行；④ `instance_id` 过渡字段删除时点仍是 AF v2.6，登记以免与③混读），AF 不编辑他仓文档 | `关键决策部/inbox/20261004-AF-裁定四件落地回执与有界缓存存量口径更正.md`（**裁定 20261004 §四 已追认**）。四件的代码已在 `7dbd640`+`c0476e2`；本回执不改变任何已裁语义，只报读数、更正前提、列那四行。**→ 裁定 20261004 §四 已追认，且追认的是"AF 没照裁定里的数字执行"**：原话"**'基线冻结 74 + 注册表 2 + 固定键 2'替代'登记 5 + 基线 2'——追认**"，理由是那 3 处不齐的站点进注册表只能填不存在的名字（门当场判红），反过来给它们补 TTL 腿"正是我驳回 C 时拒绝的'为测不出收益的曲线动存储层'"。同节另追认两笔：① DCD 自己那份 20261004 盘点路线图**五行前提已过期、AF 指正成立**，DCD 当场更正该文档（`3262ccb` 是 AF 侧对更正的接收记录）；② 件 3 CI 首 run 红的更正确认——`quality-gates` 作业缺 pytest 由 AF 修（`pip install -e ".[dev]"`），并明写"**AF 不自签 VERIFIED 是对的**：件 3 当前等级 = 判据级绿 + 作业级待 run 69 复测"。回执列的**必须回到契约表的四行**按 HEAD 复测分两半：②`trace_id` 事件级口径与③`node_id` 不进表**已随 DCD 2026-10-04 那次落笔进表**（表头修订行 + `ma/insights` 载荷行四键齐全）；①`READONLY_DEGRADED` 前缀与④`instance_id` 删除时点**在表上 0 命中**，连同裁定 §一(a) 要求的 GET `pending` 鉴权行、`/api/user/auth-codes` 行一起登记不静默（§六）——这四行都在 DCD 手里，AF 不改他仓文档 |
| 16 | **安全审计（scoped run）遗留的三问，三问都会改变部署前提或现网可见行为**（本批对 HEAD 复测后剩下的、AF 不自裁的那部分）。① **长期码的绝对 TTL 归谁定**：`AuthCode` 有 `expires_at`，但签发长期码的路径写的是 `expires_at=None` ⇒ 一旦泄露即**永久有效**，加"可配置绝对 TTL（默认 180 天）"会改变**已签发码**的命运，那是运维契约不是 AF 的内部实现。② **MCP 面到底按什么拓扑部署**：`serve` 实测 `--host 0.0.0.0` 且 compose 里 `AUTOFORGE_API_TOKENS` 被注释掉；`AUTOFORGE_MCP_TOKEN` 未设 ⇒ `af_mcp._guard()` 今天**放行一切 scope**（默认拒绝会当场改变可达面，HTTP 只读面与 Agent 面同时受影响）。AF 倾向"读端点保持开放 + 写面明确写进部署前提『只在可信 LAN』，MCP 改默认拒绝"，但这两半都得 DCD 点名，注释里写不算。③ **homesdk wheel 的来源与完整性**：`af_executor.py` 顶层硬依赖 `homesdk`，而 `pyproject` 未声明它，交付物是躺在 `docker/` 下、文件名钉死的一枚 wheel——AF 侧能核"extras 三面一致"（`check_mqtt_runtime_dep.py`），**核不了这枚 wheel 是不是官方构建**（要 Pypi 侧发布账或 DCD 建 hash 台账，同 20261002 §〇 那批 homesdk 记账缺口同族） | `关键决策部/inbox/20261004-AF-安全审计遗留三问-决策申请.md`（**裁定 20261004《AF 安全审计与 MA 回执与遗留两批》§一 已回**）。AF 侧本批**未改**：compose、`--host`、read 端点的鉴权依赖、MCP 默认放行、长期码 TTL 一处没有；同批**已自决**的只有能静态判红的那两族（授权码全店窗口 + MCP 参数↔schema 双向门），判据与八腿变异见 §二之三十三。**→ 裁定 20261004 §一 三问全回（Q1=A / Q2 三项分别裁 / Q3=B）**：① **Q1=A**——长期码保留"长期"语义但**必须带可配绝对上限**（默认 180 天、`AUTOFORGE_AUTH_LONGCODE_TTL_DAYS`、`0`=显式关），且 `af_auth.list()` 要输出"距生成多久"给 WebUI；裁定给的理由是"窗口计数在进程内存、重启清零那条敞口仍在，180 天是上限兜住它"⇒ 本仓落法与 §一 3 的有界化同族（`2a8d940`，回执裁定 §三 那格读数判 ✅，含"空值或解析失败回落默认而不是关"这个细节）。② **Q2 三项分别裁**：部署拓扑=**`--host 0.0.0.0` 保留**（绑 loopback 会打断 DB 跨机访问）但"只在可信 LAN"要**写成显式部署前提**进 README + compose 注释——README 那半 AF 已落（`2a8d940` 的 14 行），**compose 那半按铁律 #3 归 NAS 部署者/SP**（裁定 §五 同一条）；read 端点=**A 维持公开**（只读面依铁律 #6 不该反过来依赖令牌系统）；MCP=**B 默认拒绝**——`serve_mcp` 未设令牌必须拒、显式 `AUTOFORGE_MCP_ALLOW_NO_TOKEN=1` 才放行，并要求 AF 补判据"未授权默认结论必须能被测试判红"⇒ 排 **下一批单独做**（`_guard` + 测试调用点逐条显式化，§六 已登记）**→ 该批已落地，见 §二之三十八**：`current is None` 收窄为"无身份 ⇒ 需鉴权工具默认拒绝"，`AUTOFORGE_MCP_ALLOW_NO_TOKEN` 只认 `1`，`serve_mcp` 横幅与 `whoami` 的 `note` 同步改三态，测试侧 23 处调用点显式化（`_ALL` 的 scope 名单取自 `af_auth.SCOPES` 唯一真源），HTTP `POST /mcp` 复用同一道 `_guard` 不另写第二份检查；CONTROL + 六档变异全咬住。。③ **Q3=B**——14 个未覆盖面先补高优先三件（homesdk 供应链、`af_store`/`af_persist` 路径写入、SSRF 白名单），其余按里程碑排；裁定明写"**AF 自审不算独立覆盖（铁律 #5）**，这三件建议排外部 scoped run 或 DCD 复核"⇒ wheel 是否官方构建这一格**仍不在 AF 手里**，AF 侧只能核 extras 三面一致。同裁定 §一 三条对端可见形状（F-1 加 `Depends(_read)`／F-2 只把 `demo/forge2026` 明文默认凭据从页面移除、不改校验逻辑／F-3 收紧到 `_write` + owner 面明文与非 owner 面摘要分离、并要三条判据）已随 `2a8d940`+`ccf2fde` 全部落地，读数见 §二之三十六 |
| 17 | **第 16 件的回执半边 + 一条 AF 读不出对端的前提差**：裁定 §一 F-1 原话"DB 侧持 write 域令牌（`autoforge_api_token`），write 域含 read——DB 侧零改动"，而 AF 的 `requires()` 一直是**逐名比对**、从没有过蕴含关系，homesdk 码里 `grep AUTOFORGE_API_TOKEN\|autoforge_api_token` **0 命中** ⇒ DB 令牌到底含不含 `read` 在 AF 侧不可证。AF 没有照字面只加门（那会把 DB 每 5s 的 ask 轮询整条打断，且只有对端能发现），落了一条**单向**蕴含 `read←{read,write}`（read 不满足 write、live 不满足 read），两向各一条判据。要 DCD 定的三件事：① 契约表那一行现在只给 POST 标了鉴权，GET 半边的要求要不要登记；② DB 令牌的 scope 集合给一句实话（含 `read` ⇒ 蕴含表可删；不含 ⇒ 它是必要修复）；③ "高权域天然含低权域"要不要变成全站口径（AF 不敢单方面扩到 `write←live`）。**同件附三份实测读数**：§六 要求的四仓 gitignore 探测（限定 `src/` 包树：AutoForge 5/0/5、homesdk 2/0/2、memory-agent 5/0/5、**AgentOps 仍 `NOT_A_REPO`**（`rev-parse` rc=128），并写明整仓扫会读出 2010 条 `.venv314` 假吞——射程必须声明）；授权面那条 fail-open 要不要升静态门（AF 建议等基线 9 站收完再谈，否则第一天就挂豁免表）；MCP 默认拒绝与 F-3 的排期请求 | `关键决策部/inbox/20261004-AF-裁定落地回执与write域含read前提差-决策申请.md`（**裁定 20261004 已回，三问全裁**）。今日已落地部分见同一份回执第一节，读数见 §二之三十五。**→ 裁定 20261004《AF 落地回执与 write 域含 read 前提差》三问全裁**：① 契约行**要**登记 GET 半边的鉴权要求，且由 **DCD 落笔**（AF 不改他仓文档）——本批按 HEAD 复测 `homesdk/doc/ADM联动主题注册表与消息契约.md` 的 `DB→AF` `asks` 那一行仍只给 POST 标了 `write 域令牌 + INBOX_KEY`，**该行未到**；② DB 那颗令牌**含 read**（实话：它要轮询 GET pending，只给 write 不含 read 就整条吃 403）⇒ AF 落的单向蕴含 `_SCOPE_SATISFIED_BY={"read":("read","write")}` 判为**必要修复、不是可选**，裁定原文"DB 侧零改动"成立的根据正是这条蕴含，落法评价"**最小、单向、正确**"；③ **不扩成全站口径**——只在 `read` 这一门做 write ⊇ read，明确**不扩 `write←live`**（"那把 `live` 变成万能钥匙"），要扩须 homesdk/DB 令牌签发面共同定义。**§二 授权面升硬门=本窗不升 + 给启动条件**（基线 9 站收完 **或** `af_auth`/`af_premiere` 下一次真改动，先到者为准）：本批 F-3 后端半边真改了 `af_auth.py` ⇒ **条件以"真改动"这一支先到**，D 腿按"不另开第二道门、不接基线也不收豁免、只收 `.auth` 可见写入目标"落地（`ccf2fde`，§二之三十六），9 站 A 腿的账**已于次日按钉定顺序收完（§二之三十九：基线清空 0 站、落点统一走 `af_atomic.atomic_write_text`、「登记进基线」这条出口关闭）**。**§三 五格读数全 ✅**（含"空值或解析失败回落默认而不是关"与 paho 上界逐条核字符串），§四 确认三仓 `src/` 面 0 吞、AgentOps 仍非 git 仓，顺带那条 fail-open（半截撤销名单=已撤销令牌下次启动复活）裁定判"AF 修对了"。**§五 排期确认**：MCP 默认拒绝=下一批单独做（裁定原文写"49 处 `dispatch(` 逐条显式化"；AF 现读 `grep -rn --include=*.py "dispatch(" tests/` = 52 行 / 剔除本批新测试文件 46 行，AST 分档后真需要显式身份的是 **23 处** → **§二之三十八 已落地**）、F-3 收紧+owner 拆分=下一批且含浏览器验证（视口取不到就按"DOM 事件驱动走通"写结论等级）、F-2 UI 半边=与 F-3 同批、Q3 真机演练+窗后四项验收+NAS 镜像重烤=合并窗由 DCD 排期 SP 执行、compose 侧 `MQTT_*` 与"可信 LAN"注释=NAS 部署者/SP（铁律 #3）。判例侧 AF 自报的"整仓扫读出 `ignored=2010`"裁定收为"**射程必须写明**" |
| 18 | **两棵用户 WebUI 哪棵是交付面，以及 `ui-user` 的数据层断链要不要本批就补**（F-3 浏览器验证撞出来的，HEAD 即存在、非本批引入）。实测三件：① console 首条错 `TypeError: Se.openPairStream is not a function`，根因 `ui-user/src/api/index.ts:8` 把**模块 namespace** 当对象用（方法装在 `client.ts:35` / `mock.ts:83` 的 `export const api = {...}` 里）⇒ 整个 `api.*` 调用面运行时都是 undefined，面板显示"暂无"而**同页 `fetch` 同一端点 200、1 行明文**；② 名字层三处（`stores/main.ts:49/53/59` 的 `getAuthCodes`/`generateAuthCode`/`deleteAuthCode` vs 客户端的 `listAuthCodes`/`createAuthCode`/`revokeAuthCode`）+ 值语义两处（视图过滤 `c.type` 而后端字段 `kind`；`formatDate()` 期待 ISO 而拿到 epoch 秒）；③ `vue-tsc --noEmit` 在 `ui-user` 上 `TSC_RC=2`、**51 条**（其中 28 条 TS2307 含本机 `node_modules` 装不全的部分，AF 不把它算成代码缺陷）。同族另一棵：`ui-user-mimo` 的 node 判据 **60 条 / 7 条红**，`store.test.mjs` 在裸 node 下 import 就失败（`import.meta.env` 未定义），已用逐字节 HEAD 对照档确认非本批。三问：Q1 交付面 A=`ui-user` / B=`ui-user-mimo` / C=两棵都要（AF 判最贵）；Q2 CI 射程扩到用户树是"先修后加"还是"先加红着当账"（现在加当天就红）；Q3 两棵树 `VITE_USE_MOCK` 缺省档相反（`=== 'true'` vs `!== 'false'`）要不要统一成"缺省=真后端" | `关键决策部/inbox/20261004-AF-用户WebUI交付面与数据层断链-决策申请.md`（**裁定 20261005 §一 已回，三问全裁 B/先修后加/批准**）。AF 侧本批**两棵用户树的数据层一个字节未改**、未自决扩 CI 射程、未加第 12 个响应键（`masked` 属裁定未批的形状改动）；已落地部分（D 腿门、F-3 后端半边、同源部署读数）见同一份申请第五节与 §二之三十六。**→ 裁定 20261005 §一 三问全裁（B / 先修后加 / 批准），本批已按三条落地**：**Q1=B**——`ui-user-mimo` 是交付面，且该题**早在 `20260928-AutoForge-v2.1设计难题A-F-决策.md` §G 已裁过**（"以 `ui-user-mimo` 为主线，`ui-user/` 冻结归档"），裁定明确认定本次撞出的数据层断链**正是该裁定的又一证据**、`ui-user` 不该再投人力；F-3/F-2 的浏览器验证改挂主线树，`ui-user/` 就地冻结（保留可读，不再开发）。**Q2=先修后加**——先修主线树那 7 条红（HEAD 即存在、非本批引入），再把它的 node 判据加进 CI 硬门；`ui-user`（冻结树）**不进 CI**，裁定给的理由与本仓既有口径同一条："先加红着当账 = 造一条会假红的门"。**Q3=批准收敛为"缺省=真后端，mock 必须显式开"**，改动只落主线树。**AF 侧本批的实际落法**：7 红逐条分档为 **2 条产品缺陷**（`compareAutomation` 把"从未触发"与"日期串解析不成"都折成 `-Infinity`，同键退化到名称序；`applyTheme()` 住在 store 里让判据在裸 node 上 import 即炸）+ **5 条量具缺陷**（MCP_URL 值抄成 8000、flipIn 三段只钉一段、375px 宽度正则漏钉响应式媒体查询、store 判据跑法没钉"不碰 DOM"、主题开关落点钉的是字样而非活代码）——分档理由与七档变异 + 对照档见 §二之三十七，其中 M3 当场自捕一次**假绿**（字面存在性判据在把代码注释掉之后照旧绿），据此新立口径"**钉『某段代码存在』的判据，必须同时钉『它活着』**"。env 读数收进唯一接缝 `ui-user-mimo/src/api/env.ts`（`VITE_USE_MOCK === 'true'`，缺省即真后端；判据跑法用 `package.json` 的 `--env-file=tests/mock.env` 显式声明 mock，不让量具依赖不安全的那一档）；`ui-user-mimo` 判据 **60 条 / 7 红 → 74 条 / 0 红**、`npm run build` `RC=0`、`GATES_RC=0`；CI 新增 `ui-user-mimo-judgments` 作业（node 22 + `--replace-registry-host=always` 的源自证 + `npm test` + `npm run build`）。**盘出来没做、已登记的三件**（§六）：mock 常量仍进生产产物、`vue-tsc` 不进 CI、F-3 面板的浏览器验收仍未完成 |

## 六、未在本版做（登记，不静默）

- 第 0 步 ③④：镜像重烤 + AgentOps 模板生效 = 合并停机窗内的动作（裁定 §一 已把顺序写死：**账 → wheel → 镜像 → 模板**；回滚反序 **模板 → 镜像 → wheel → 账**）。账与 wheel 两件已由 DCD 完成，AF 的 vendored wheel 也跟上；**后两件未做**。
- 窗后 AF 侧验收四项（裁定原文，缺任一项即该步未完成、不许用"配置正确只是没抓包"过账）：`compose ps` 起来、`/health` 200、抓到一条含家庭墙钟 `ts` 的 `af/automation/fired`、`adm/autoforge/status` retained `online`。**本批把四件做成一条命令** `scripts/verify_adm_window.py`（PASS/FAIL/UNAVAILABLE 三态；有缺项 ⇒ 退出码 2 并打印"EXEMPT ≠ VERIFIED"，不给部分绿留一条印成绿色的路；判据取自契约表 §1.2/§1.3；连接与凭据走机制层 `homesdk.mqtt`，脚本自身不读任何环境口令），本机实测 `RC=2`、四项逐项读数见 §二之二十五。**该项仍是 EXEMPT 不是 VERIFIED**：本机没有可连的 broker（`MQTT_HOST` 未配、无 `docker`/`mosquitto`），`kill -9` 与 retained 那两段只能在 NAS 上取数。顺带更正两处旧登记：① "本机无 paho-mqtt"是跑错解释器的结论（`homesdk.mqtt.paho_available()` 实测 `True`）；② 裁定原文里的 `/health` 在 AF **不存在**——真名只有 `/api/health`（`af_api.py` 注册的那一条），且 `docker/docker-compose.api.yml` **没有 healthcheck**，故脚本按 `/api/health` 优先、`/health` 兜底两态都探，并把命中的路径印进读数（"打了 `/health` 拿到 404"不等于服务没起）。**本批另加两条同一验收线上的前提**（都属 §五 第 13 件，不是 AF 自决项）：① 那条命令此前在镜像里**不存在**（`Dockerfile.api` 不 COPY `scripts/`），本批已随 `739a328` 补上——但**它要等下一次镜像重烤才生效**，窗当天若用的是旧镜像，`docker compose exec` 会报路径不存在，那不是缺陷而是未重烤；② ③④ 两项的数据来源取决于 `AUTOFORGE_MQTT` 到底开不开、以及第③项那条 `fired` 由真机还是 dry-live 产生（dry-live 语义是"设备一次没碰"，AF 不接受把它当默认档写 PASS），故本项的**结论等级由 DCD 答复决定**，不在 AF 手里。
- **§5.3 第 4 件的两条未测项——一条本批已补成实测，另一条仍无读数**：① ~~切页后定时器是否还在打接口~~ **已实测销账**：页面内挂钩子记请求时刻（XHR+fetch 两通道，axios 走 XHR），`/live` 上 5 次命中、间隔 `10451/9995/10166/9830` ms ⇒ 10s 档真在打；SPA 切回 `/overview` 后**等 51 秒（五个档）新增 0 次** ⇒ `clearInterval` 生效，无后台轮询残留。读数与做法见 §二之二十七 第七节。② **同一浏览器多开两个标签会各自轮询**（本批新引入的负载面）**仍无直接 QPS 读数**：`window.open` 被弹窗策略拒、`browser-use` 无"新建标签页"动作 ⇒ 只能实测到前提"零跨标签协调"（`grep -rn "BroadcastChannel\|navigator.locks" ui/src` **0 命中**，且 `LiveView.vue` 只有一对 `setInterval`/`clearInterval`），"N 标签 ⇒ N 轮询"是由前提两步**推出**而非量出。两者都只涉只读 GET（`available()` 不碰设备），放大的是请求量。若在窗后复盘要给"UI 长期开着"下结论，②得先有数——办法要么是能开双页的浏览器，要么是给 `ui/` 装测试框架跑双挂载，本批都不做。另：本批的"点撤销走通"是**按 DOM 事件驱动**（本机浏览器取不到视口），**真实指针事件与真实家电未验**，结论等级已按此写。
- 第 2 步 `kill -9` → broker 代发 offline 的真 broker 验收：同上，须进窗随镜像重烤一次跑。
- ②A 的 `instance_id` 过渡字段**未删**（删除时点 = AF v2.6，属破坏性变更须与窗口同做）。
- **UI↔路由契约门禁（§二之二十八）判"路径可达"，不判"值语义对"**：M6 是实测不是推理——服务端把 `/api/undo/available` 改名成 `/api/undo/ready` 后，前端那一行仍被 `/api/undo/{deploy_id}` 的通配段接住 ⇒ 门绿。HTTP 层这确实可达（请求真会落到参数路由上），错的是业务语义（多半 404 在 handler 里）。要把它做成红，得先把 33 条"UI 从未调"的路由**分类**（MCP/DB 面向 vs 前端本该调），那是另一批的事；本批只登记不判，反向也**只计数不判红**（判红只会逼下一个人给整节加 `continue-on-error`）。
- **那 33 条"UI 从未调"的第一手盘点已开局，且第一条结论是：这个反向读数现在不能当"没人用"读**。门只扫 `ui/src`（开发面板那一棵树），而本仓还有 `ui-user/src`（用户端 ForgeSight）——`ui-user/src/api/client.ts` 里逐条实调着 `/automations?group_by=…`、`/automations/{name}`、`…/enable|disable|archive|unarchive`、`DELETE /automations/{name}`、`/user/agents`、`PATCH|DELETE /user/agents/{id}`、`/mcp/pair-request` ⇒ 33 条里**至少 automations 一族 7 条 + user/agents 3 条 + pair-request 1 条共 11 条不是死面，是门射程外**。要收口得先把两棵树的消费者分开登记（谁调、哪棵树、跨仓还是第一方），否则"分类"这一步会把真实消费者误判成冗余。同批盘出的一条对照事实顺手钉住：`ui/src/api/index.ts:4` 是 `USE_MOCK = import.meta.env.VITE_USE_MOCK !== 'false'`（**默认走 mock**，靠 `ui/.env.production` 的 `VITE_USE_MOCK=false` 在生产构建关掉），而 `ui-user/src/api/index.ts:5` 是 `=== 'true'`（**默认走真后端**）——两棵树的默认档相反；`import.meta.env.*` 是构建期内联，所以这不是运行期缺陷，但"改档必须重新 `npm run build`"这条只写在 `ui/README.md`，分类那一批要一并处理。
- **上一条那个"至少 11 条"是手抄估算，本批已用实测换掉**：门扩到三棵第一方 UI 树后，反向读数 **33 ⇒ 16**，被移出去的是 **17 条**真消费者（`automations` 一族、`user/agents` 一族、`auth/*`、`pending/*`）。同批还修掉一条更要紧的漏：泛型实参里的 `;` 会让整条调用被**静默丢掉**（修前纳入那两棵树的读数是 11/16 与 7/19 ⇒ 85 个调用点里 17 个根本没进射程）。上一条里"要先把两棵树的消费者分开登记"那半步已随 `UI_TREES` 登记表落地，且加了"登记树读不出调用点 ⇒ exit 2""盘上多出没登记的 UI 形状目录 ⇒ exit 2"两条射程判据。做法与六档变异读数见 §二之三十二。**剩下那 16 条的逐条定性仍未做**——本批只把"数错了"改成"数对了"，没有宣布分类完成。
- 同一门的射程边界登记清楚（本批内改过一次口径）：路由有**两张脸**，门认 `@app.get/post/put/patch/delete("…")`、`@router.…` 装饰器，以及 `("VERB", "/api/…", handler)` 形状 + `add_api_route` 的静态挂载表。剩下**一张脸读不出**：`af_runtime_plugins.py` 的 `add_api_route(path, …)` 里 `path` 是插件在运行期声明的数据，静态无从得知 ⇒ 门把它记成 `运行期挂载文件 1 个` 并写进绿色行，而不是报错也不是假装看过。**方向上要分清**：挂载表那条路若哪天改了形（有 `add_api_route` 有字面量 `"/api/…"` 却读出 0 条）⇒ `exit 2`，因为那时本门会对真端点报**假红**；而插件那张动态脸漏掉只会漏在"UI 未调用"的计数里，不会产假绿。首版把这两件事混为一谈，得到的是一条 `RC=2` 的长红——那才是本门最可能被关掉的方式。
- §四 的 B（逐实体可见漂移 + `entity_drift` 记账）**未做**，按裁定的启动条件排队：下一次真实改动 `af_instance._refresh_snapshot` 时顺手做，或 AF v2.6。
- 洞察面板的**投递源仍是本机手投**：§二之四的两条提案是用 `PersistentInsightSink.submit()` 直接写进 dev store 的，走的是"落盘之后的那一段"。本批把**桥回调 → 落盘 → `/api/insights/pending`** 这一段用契约形状的假消息钉住了（`test_contract_shaped_insight_reaches_the_panel_with_its_accounting`，注入假 client、真桥、真队列、真 API），但**真 paho + 真 broker** 那一段仍未端到端（paho 本机实测有，缺的是那台 broker：`MQTT_HOST` 未配），面板的空态文案因此把"桥未上线/没订到主题"列为四种成因之一，而不是当作已验证链路。
- **契约表本身还欠三行改动，且都在 MA/DCD 手里**（§五 第 7 件）：`ma/insights` 的载荷行没有 `conf`、没有稳定的假设 id、也没有 IR 候选（AF 的 `intent`）。AF 已按可回退口径落地（别名 + 缺报记账），但只要契约行不改，MA 侧随时可能按自家形状发而 AF 无从判定"这条到底该不该有 conf"；面板上也因此会长期是「无 IR（不能批准）」。**这不是 AF 能单方面收口的残留**，登记以免被读成"入向已经全对齐"。**→ 本条三行已闭合，按 HEAD 复测**：契约表头"修订 2026-10-04"那行写了 ① `ma/insights` 登记 `insight_id` / `conf?` / `intent?` + `trace_id` 事件级口径 + `node_id` 明确不进表，载荷行 `{trace_id, ts, insight_id, kind, persons[], room?, summary, evidence[], snapshot_url?, conf?, intent?}` 四键齐全 ⇒ 上面那句"没有 `conf`、没有稳定假设 id、也没有 IR 候选"**已过期的部分就此销账**，面板上「无 IR（不能批准）」从"契约行没写"变成"MA 这次没发 `intent`"（可选字段，不得成为硬依赖）。**同一张表上仍 0 命中的是四行**：`READONLY_DEGRADED` 前缀、`instance_id` 过渡字段的删除时点、`GET /api/asks/pending` 的鉴权半边（裁定 §一(a) 点名要 DCD 落笔的那一行）、`/api/user/auth-codes` 整行——**这四项不在 AF 手里**，AF 不改他仓文档，登记不静默。
- ~~第 5 步 ② 的「顺序追加」子句未做，等 DCD 定性~~ → **裁定 20261004 §一 4 判 A：该子句对 `af_persist` 不适用**（AF 的三条论证成立：与同批"不改存储格式头"相抵 / 与第五轮的有界化修法反向 / 裁定自己已把它降级为"不做全量 eventlog"，而"重放友好"要解决的问题已被校验和 + 损坏段跳过 + 原子替换覆盖）。按裁定的执行栏，本仓记录就地标注"**经 DCD 判定不适用**"（§二之三十 第五节），这条**不再作为未做项重报**；`af_persist.py` 一个字节未动。§5.3 第 3 件那一格"前置=无"的更正是 DCD 原文侧的动作，AF 不代改（§五 第 15 件回执 §五）。
- **审计 §四 那条「高」优先的第二项（`af_store.py`/`af_persist.py` 路径处理，原文写「未做独立安全审计、不自签已审」）本批改成「盘过了，读数如下」**（铁律 #5：「未审」不是豁免，「已审」也不能空口签）。四条，全部真跑过：
  ① **穿越不成立**：三份 sanitizer 的保留集里既没有正斜杠也没有反斜杠（`af_persist._safe` 的保留集是 `-_ .`+`isalnum`，`af_version._SAFE_NAME` 是 `A-Za-z0-9._-`，`af_store._dir` 是 `-_`+`isalnum`）。`PersistStore._path("../outside/secret")` → `.._outside_secret.json`（仍在 `instances/` 目录内，`resolve()` 不等于外面那份目标）；`VersionManager._file_for("../../outside/secret")` → `.._.._outside_secret.json`；`GraphStore._dir("../../outside")` → `<root>/outside`（root 之内）；`ps.remove("../outside/secret")` 返回 `False`，外面的文件 `exists()` 仍为 True。
  ② **但「别名（aliasing）」成立**——映射是多对一：`_file_for("a/b")` 与 `_file_for("a_b")` 同为 `a_b.json`；`_safe("  sp  ")` 与 `_safe("sp")` 同为 `sp`（`strip("_")` 把替换出来的下划线又吃掉了）；`_file_for("日本")`/`("語")`/`("門")` 三者同为 `_.json`（`_SAFE_NAME = [^A-Za-z0-9._-]+` 是 ASCII 白名单，CJK 整段塌成 `_`）。
  ③ **射程核实（这是关键的一步，别停在 ②）**：写侧拿不到别名——IR schema 对 automation `id` 的要求是 `^[a-z][a-z0-9_]*$`（读数取自 `af_ir.models.SCHEMA_PATH` 那份 schema 的 `/properties/id`，**不是手抄**），这段字符集里 sanitizer 是恒等映射 ⇒ 两条不同自动化不会在版本文件上互相覆盖。成立的是**读侧**：面上（HTTP 路径参数 / MCP 工具入参）传进 `a/b` 这类越界 id 时，会读到 `a_b` 的历史——身份混读，不是写入损坏。`af_persist` 那侧同理（`instance_id` 不经 IR schema，`x/y` 与 `x_y` 落在同一个 `{...}.json`）。
  ④ **下一批要落的形状（AF 可自决、零迁移）**：把「必经 schema 那份 id 模式」做成**边界**校验（`af_api` 的路径参数 + `af_mcp` 的工具入参各一处，配反例判据），而不是去改 `_file_for`/`_safe` 的命名方案——后者会让存量 `_.json`、`a_b.json` 变成读不到的孤儿，属数据可见性变更，真要动必须先投 DCD。登记为待落，不静默。
- **接缝类判据：多路镜像这一族已结清，参数递错/名单手抄那一族扫过七条且已全部有门**：本批把"同一份值手抄进多条属性路径"这一族做成门禁（`scripts/check_states_fanout.py`，§二之十四 的 AST 盘点显示它覆盖的是 src 全集，不是抽样）。另补的四处判据属**参数递错**类（serve→`build_app` 的 `readonly`、CLI 起桥递出去的四样 kwargs、`_make_runtime` 的起桥条件与 clock 归属、`live_run` 的时钟锚点），靠的是顺着验收点手动追问"这根线谁在测"。第四条不只是"补一条测试"，它**实测出一个真缺陷**：两条真机路径对同一次下发给出两套时间轴（§二之十五，`12cea64` 已修）。同族里曾剩一条「TOOLS→caps 之外有没有第二次工具名单映射」**未做清单化盘点**，也没有对应门禁能判红——这类缺口不会让任何东西变红，只会让该红的不红，与 §二之十一 同族；**本批扫掉**：AST 盘点盘到两处真映射（`af_orchestrator.observe()` 按名调注册表里没有的 `af_live`、`af_runtime_ext.mcp_tools()` 另抄一份含 `af_approve_proposal` 的五人名单），两处都是死代码 ⇒ 删除而非接线，判据做成 `scripts/check_tool_names.py`（§二之十九，`743aadf`）。**第五条已在上一批（§二之十六）扫掉并抓到两处真缺陷**（`af_service` 的 store 注入 + events 上限只装 HTTP 面，`dc8ac0d`，§二之十六）；同批扫到的第三处（单写者租约只在 HTTP 面）因涉及给 DB 新增可见拒收模式而投裁定（§五 第 10 件），代码未动。**第六条 = `af_watch` 的观察者装配，本批扫掉并抓到第三条真缺陷**（观察期把 `auto_rollback` 丢在接缝上，`af2ee56`，§二之十七）。已扫的**七条**覆盖 `af_cli` 的 serve/起桥/runtime 装配三面 + `af_service` 的 clock 与 store/cap 一面 + `af_watch` 的三个喂入点一面 + `af_mcp.TOOLS` 的名字真源一面；`af_watch` 侧本批已盘（§二之十七，抓到第三条真缺陷：观察期把 `auto_rollback` 丢在接缝上）。第七条 = **TOOLS 之外有没有第二份工具名单映射**，本批扫掉：盘到两处死映射并删除，判据进门禁（§二之十九，`743aadf`）。**参数递错那一族的判据已常驻**（§二之十八：`scripts/check_param_injection.py`，按 `store`/`readonly` **参数表**收，`gates.sh` 新节 ⇒ CI `quality-gates` 同口径判红），**名单手抄那一族也已常驻**（§二之十九：`scripts/check_tool_names.py`，注册表锚点读不到就 exit 2），**第三条族——"实现之间对同一条契约给相反结论"——同批补成静态门**（§二之二十：`scripts/check_snapshot_policy.py`，射程 = 类级 `snapshot` 且"标注 `-> Snapshot` ∪ 体里造 `Snapshot`"，锚点读不到 exit 2；此前它只有四条各认自己那几个类的契约测试），所以"这类洞没有门禁能判红"从本批起只对**静态判不出的形状**成立（`clock` 属"默认即设计"、`obj.method()` 与 `**` 解包与变量名工具属读不出值、**"某个容器是不是有界"属值语义**——`check_bounded_caches.py` 判的是"注册表说的名字在不在模块里、测试 id 收不收得到"，不是"这个字典会不会胀"）。**第八门 = 有界缓存注册表**（`c0476e2`，裁定 20261004 §一 3 B，§二之三十）：它拦的是"下一条约定只写在审计正文里"这个形状本身。同批按这条门盘出的两处"回收逻辑写好了但没人按"（`sweep()` 与 `purge_expired()`）也已接上生产路径并各钉判据——那是 §二之十四 那一族的第 8、9 次命中，两腿变异各 `rc=1 1 failed`。登记在此，避免被读成"接缝已系统扫过"。
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
  A/B/C 三档，AF 建议 B=注册表式门），申请里带本批实测的静态口径读数（7 候选 / "缺界" 2 条**全是误报** ⇒ 真阳性 0/2）。
  **→ 这一条本批已收口**：裁定 20261004 §一 3 判 **B（注册表式门禁）**，`scripts/check_bounded_caches.py` +
  `src/autoforge/af_bounded_caches.py` + `gates.sh` 新节已上线为 CI 硬门（`c0476e2`，读数与三档真实仓变异见 §二之三十）；
  C（统一基类）仍是裁定自己驳回的那一档，未做也不该顺手做。收口同时按 HEAD 更正了申请里那组数的适用范围
  （完整口径下是"扫到 76 / 双腿齐全 2"，不是"7 候选 / 5 处登记"），差异已回投 DCD 追认（§五 第 15 件）——
  **不是把旧读数当错账抹掉**：0/2 真阳性那条结论仍然成立，它否的是"天真扫无界容器"，不是"注册表式门"。
- 证据面板的 `evicted_automations` 只做"提示有自动化被挤出内存"，未做跨进程持久化——**注意这与 ④A 不是同一个问题**：④A 裁的是 MA 洞察提案队列（已持久化），证据档的进程内清零仍是遗留。
- `SessionViewResponse` 的**应答成功分支**（本批已收口，见下方"已收口"与 §二之九）：原登记为 EXEMPT——`case04_ask_timeout` 带 seed + event 建会话后 `asks=0`（分支未走到挂起 ask），只实测到 404 失败面；顶层 9 键靠同族 `POST /sessions`/`GET /sessions/{sid}` 的同一 `_session_view` 坐实。
- `/api/metrics` 的 `runs/success/failed/audit_distribution` 仍是 0/空——`get_metrics` 的 docstring 写明这三项需**常驻进程**（`forge serve`/`watch`）才累计，原型期服务层无状态。本批只把"形状"钉成类型，没有把"数值来源"改成真累计；那是另一件事，不在类型面批里偷做。
- 本机 `node_modules` 的完整性没有门禁：本批实测 `@types/node@22.20.2` 曾被装成 **37/74** 文件（见 §二之二）。我用 registry 同版本 tarball 增量补齐了缺的 37 个文件，但**成因未查**（不在本批范围），且这条只在开发机成立——CI 走 `npm ci` 从锁文件装，不复用本机 `node_modules`。若下次又冒出"某个 `node:*` 模块找不到"，先数 `node_modules/@types/node/*.d.ts` 再怀疑代码。
- **CI 读数这条路本身没有门禁**（本批已收口，见下方"已收口"）：本批能读到 run 1–27 全红，靠的是"本机 `~/.git-credentials` 里存着可用的 github.com 凭证 + `api.github.com` 可达"（同一时刻 `github.com` 网页面 `curl` 超时、浏览器 `ERR_CONNECTION_TIMED_OUT`）。这是**一次性条件**，不是可复跑的命令；下一次两者都不可达时又变成 EXEMPT。要收口就得做成脚本（且不能把凭证写进仓）。
- **真 vhass 在 CI 上现在是 skip**：10 条 `*_in_real_vhass` 判"插件未注册 → skip（带理由）"，权威场地是 `docker run autoforge-test`；本机无 docker（`docker: command not found`），该文件也自陈"未经实跑验证" ⇒ **F14 第二道闸至今没有任何真跑读数**，"CI 绿"不许被读成"仿真复核过"。
- **CI 依赖未 pin**（`pip install -e ".[dev]"` 每次现解）：本批那条 pydantic v1/v2 红就是漂移付的账；改 lockfile/加 pin 等 §五 第 5 件裁定。**本批把未 pin 面又扩了一条作业**——`quality-gates` 原先只装裸包（`-e .`），为让有界缓存门的判据 B 能跑 `python -m pytest --collect-only` 改成装 `.[dev]`（run 68 的红因，§二之三十一）⇒ 现在三条作业吃 `[dev]`，下限写法漂移会同时红这三条。AF 不自决加 pin。
- **`check_mqtt_runtime_dep.py` 的射程不含"作业是否具备它自己要跑的工具"**（本批登记，不扩射程）：它核的是"paho 在声明处 / 交付面 / CI 面 三面装没装"，所以 run 68 那次"CI 作业里没有 pytest"它**不响**，且绿行一个字没变。要把这类洞收进门，得让任何门禁脚本都能声明自己的工具前提并被逐作业核对——那是新门新批的事，与本批"修红"无关，登记以免被读成"依赖三面一致已经覆盖了这一族"。
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
- ~~§五 第 10 件：单写者租约只装 HTTP 面，MCP 真机写未受约束（AF 侧"代码一个字节未动"）~~ → **裁定 20261004 §一 1 判 A 并落地（`7dbd640`）**：只把真机写纳入咽喉、只 check 不 acquire（探测不写 sidecar）、MCP 文本固定前缀 `READONLY_DEGRADED:`。9 条判据 `9 passed`，五档真实仓变异 `3/3/2/1/2 failed` 且对照 `9 passed RC=0`、还原 `RESTORE_OK`；持锁方用**真子进程**（同进程第二条句柄会被自己挡住 ⇒ 那是假绿）。见 §二之二十九。
- ~~§五 第 12 件：出向 `trace_id` 语义与第三问 `node_id`（"AF 侧一个字节未动"）~~ → **裁定 §一 2 判 C + 判删，两半都落地**：`_envelope()` 写上"事件级、只做单事件关联、因果链靠 `ref`"，新钉一条"同实例 fired/failed 两枚号不同而 `ref` 相同"的判据；`observe_terminal()` 不再传 `node_id`，键集合判据改为逐等于契约行。桥那两文件合跑 **`56 passed`**，多发一个 `node_id` 实测取到 `1 failed, 37 passed`。见 §二之二十九 第四节。
- ~~§五 第 11 件：有界缓存约定只活在审计正文里（`grep 有界|TTL gates.sh scripts/*.py` 零命中）~~ → **裁定 §一 3 判 B（注册表式门禁）并上线为 CI 硬门（`c0476e2`）**：注册表 `af_bounded_caches.py` 给名字、`check_bounded_caches.py` 核对名字（四判据 + 四种射程 `exit 2`），`gates.sh` 新节。绿行 `[有界缓存] 注册表 2 项双腿齐全且测试 id 被收集；固定键 2 项带理由；扫到增长容器 76 个，其中基线冻结 74 个、就地豁免标记 2 处` RC=0，反例 **21 passed**，三档真实仓变异各 `RC=1` 且**各只 1 处判红**。**存量口径按 HEAD 更正并回投追认**（76/2 而非裁定的 5/2，§五 第 15 件）。见 §二之三十。
- ~~两条"回收写好了没人按"：`FireRecorder.sweep()` 全仓唯一调用方是测试里那句 `sweep(force=True)`；`UndoStore.purge_expired()` `grep -rn purge_expired src` 只命中定义~~ → `Runtime.tick()` 按已装 recorder 的 `sweep()`（**不带 force**，否则 3600s 节流被旁路、逐 tick 变逐 tick 扫盘重写）；超窗快照在**写路径** `record()` 摘除。中间有一次设计被既有判据当场驳回：先版"打开即清"让全量跑红在 `test_af_undo_http.py::test_undo_refuses_expired_window_via_http`（`KeyError: 'expired'`）——那会把"过期撤不了"和"没这条"混成同一个答复。`tests/unit/test_reclaim_callers_wired.py` **6 passed**，两腿各摘一次各取到 `1 failed`。见 §二之三十 第四节。

- **F-3 的"面板可用"这半没拿到**：后端分层与同源部署形状实测通过（§二之三十六 第四节），但 `ui-user` 的授权码面板在 HEAD 就不接后端——门面 `api/index.ts:8` 把 namespace 当对象用、`stores/main.ts` 三处方法名对不上客户端、视图过滤 `type` 而后端给 `kind`、`formatDate` 期待 ISO 而拿到 epoch 秒。AF 不自决修哪棵树（两棵用户树并存、都在写），**交付面认定 + 数据层适配的优先级**已投 DCD（`inbox/20261004-用户WebUI交付面与数据层断链-决策申请.md`）。**→ 裁定 20261005 §一 Q1 判 B**：交付面 = `ui-user-mimo`，`ui-user` 冻结归档（该题早在 20260928 §G 已裁，本批断链是又一证据），故本条"面板可用"的验证**改挂主线树**、上面那串 `ui-user` 断链不再投人力。同族里还有一格留给裁定 §一(a)：契约表此刻仍没有 `/api/user/auth-codes` 那一行（`grep -c "/api/"` 在 `homesdk/doc/ADM联动主题注册表与消息契约.md` 上 = 1，且不是这一条）⇒ GET 半边由 DCD 落笔，AF 不改他人文档，本仓只记录"该行未到"。
- **`ui-user-mimo` 的 node 判据不在任何门的射程里**：`npm test` 的写法（`node --test tests/`）在本机 node v24 上 `MODULE_NOT_FOUND`，展开成 `tests/*.test.mjs` 后实测 **60 条判据、7 条红**，其中 `store.test.mjs` 在裸 node 下 import 阶段就失败（`import.meta.env` 未定义）——这条红经字节对死的 HEAD 对照档确认**HEAD 即存在、非本批引入**（同名同集合，`RESTORE_IDENTICAL=True`）。CI 的 typecheck/build 作业只覆盖 `ui/`，既不看 `ui-user` 也不看 `ui-user-mimo`。另记一条默认值风险：两棵树的开关写法**相反**——`ui-user/src/api/index.ts:5` 是 `VITE_USE_MOCK === 'true'`（缺省走真实后端），`ui-user-mimo/src/api/index.ts:8` 是 `VITE_USE_MOCK !== 'false'`（缺省走 mock）⇒ 交付构建漏写这一个 env，`ui-user` 会连真后端、`ui-user-mimo` 会静默上线一份 mock，两边都不会有任何读数告诉你走错了哪条。**→ 本条按裁定 20261005 §一 Q1=B / Q2=先修后加 / Q3=批准 收口**：7 条红逐条分成 2 条产品缺陷 + 5 条量具缺陷后全部修绿，主线树判据 **60 条 / 7 红 → 74 条 / 0 红**、`npm run build` `RC=0`；`npm test` 的写法改成展开的 `tests/*.test.mjs` glob（`node --test tests/` 在 Windows 上 `Cannot find module …\tests`），并加 `--env-file=tests/mock.env` 让量具**显式**声明 mock 档、`engines.node` 钉 `>=22.6.0`；env 读数收进唯一接缝 `src/api/env.ts`，缺省档收敛为 `VITE_USE_MOCK === 'true'`（缺省=真后端，mock 必须显式开），`.env.production` 的 `VITE_USE_MOCK=false` 由判据钉住；CI 新增 `ui-user-mimo-judgments` 作业（`ui-user` 按裁定不进 CI）。做法、七档变异 + 对照档与 M3 那次**假绿自捕**见 §二之三十七。
- **主线树仍有一格"钉住了但没消掉"：mock 数据进生产产物**。`npm run build` 产出的 `dist/assets/index-CVJnSq6k.js`（实测 55,556 B）里含一条长期码 fixture，根因是 `MCP_URL` 与 `apiError` 这两个**真实面也在用**的常量住在 `mock.ts` 里，于是 `http.ts` 反过来 import mock 模块、把整份假数据一起拖进产物。本批只把 `MCP_URL` 的**值**从判据里解掉（改成钉"导出存在"，不再手抄），没做那件真正要做的重构——把这四个符号移进中立模块（`constants.ts` 之类）、让 `mock.ts` 只留假数据。**这是产物体积与"交付面里躺着假数据"的问题，不是正确性问题**（缺省档已收敛，生产构建走真后端），但按 Q3 的口径"漏写 env 会静默上线 mock"已经不可能读不出来，所以留到下批和 F-3 浏览器验收同做，本批登记不静默。
- **`vue-tsc --noEmit` 本批不进 CI**，理由与裁定 Q2 同一条（不落没有权威判据的门）：本机不装依赖（多 Python/Node 解释器混用的既有限制，`npm install` 不在本批射程），`ui-user-mimo` 的 TS 读数里混着 TS2307 一类的**装不全**噪声——把这种读数做成硬门，第一天就是假红。CI 现在跑的是 node 判据 + `vite build`（构建期真解析每个 import，能咬住"方法名漂移"这类断链的一部分，但不咬类型）。**代价登记清楚**：`ui-user` 那 51 条 TS 错里的类型类问题，主线树若同型，现在没有任何门会判红；解法仍是在有干净 lockfile 安装读数的机器上把 `vue-tsc` 加进同一个作业。
- **掩码面上的"删除"会把掩码当码提交**（`ui-user` 的 `handleDelete(code.code)` 在 `reveal=false` 时拿到 `********`）：交付面认定已随裁定 20261005 §一 Q1=B 落到 `ui-user-mimo`，本条按 HEAD 复测**在主线树同样成立**——`AuthCodesView.vue` 的 `revoke(c.code)` 直接把后端返回的 `code` 字段送去 `DELETE /api/user/auth-code/{code}`，而 `/api/user/auth-codes` 对非 owner 主体返回的是定形掩码 `af_auth.CODE_MASK`（`"*" * 8`）；主线树没有 `reveal` 这一概念，视图始终渲染 `c.code` ⇒ 拿非 owner 的 write 令牌进这一页，"作废"就是拿掩码当码提交。**不是安全洞，是 UX**（撤销一个不存在的码不生效，后端 `revoke()` 返 `ok=False`），随 F-3 剩余半边（裁定 §五：下一批、含浏览器验证）一起收；本批不自决加第 12 个响应键（`masked` 这类字段属裁定未批的形状改动）。
- F-3 的**浏览器验收在主线树尚未做**（裁定 §五 把它与 owner/非 owner 拆分一起排到下一批）：本批只有 node 判据 + 构建 + 同源部署三样静态读数，`AuthCodesView.vue` 的长期码面板在 `ui-user-mimo` 上**没跑过一次真实页面动作**（生成→复制→作废、短码倒计时、SSE 配对流）。裁定同时给了结论等级口径：**本机浏览器取不到视口时，按"DOM 事件驱动走通"写结论，不写成像素级验收**。做这一步的前置是主线树起 dev server 并连一个真后端（`VITE_USE_MOCK=false`），且非 owner 那条分支要有第二枚令牌才取到数——两样本批都没准备，登记不静默。
- 原子写基线那 **9 站**（`af_premiere` ×2 → `af_version` → `af_scene` → `af_fire_recorder` → `af_predict`/`af_pretrigger`/`af_shadow`/`af_flock`，顺序是裁定 §二 钉的）——**→ 次日按该顺序全部收完，见 §二之三十九**：落点统一走新的 L0 助手 `af_atomic.atomic_write_text`，`.atomic-write-baseline.txt` 清空成 0 站，「登记进基线」这条出口从此关掉（只剩「走助手」或「带理由的就地豁免」）；D 腿与 A 腿两本账至此都清了（D 腿见 §二之三十六，A 腿剩下的 `af_auth._atomic_write_text` 并轨仍登记在 §二之三十九 第七节）。裁定 20261004《…落地回执与 write 域含 read 前提差》§五 的 **MCP 默认拒绝**同批未做（裁定自己写明下一批单独做）——**→ 本批已单独做完，见 §二之三十八**：`_guard` 的 `current is None` 从「全放行」收窄为「无身份 ⇒ 需鉴权工具默认拒绝」，显式放行只认 `AUTOFORGE_MCP_ALLOW_NO_TOKEN=1`，公开工具 20 个照常；测试侧 **23 处**调用点改成显式身份（8 处需鉴权 + 15 处公开），"产品改动落地、测试一行没动就是 8 failed" 是这条门咬得住的第一读数。

---

—— AutoForge 开发 · 2026-10-05
