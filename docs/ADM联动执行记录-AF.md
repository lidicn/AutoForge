# ADM 联动执行记录 · AF（AutoForge）

> 对应计划：`docs/ADM联动执行计划-AF.md`（DCD 出品，v2.5 联动落地版）
> 记录人：AutoForge 开发
> 核实基准：**HEAD = `c5b13a9`**（已推 origin/main；本轮实测 `git rev-list --count origin/main..master` = 0、`master..origin/main` = 0，即本地与远端同点），另加**本回合 working-tree 改动**（`af_insight_queue` 队列 + `/api/insights/*` + `channel_error` 字段 + vendored wheel 换版）——这些与本条记录同批提交，故下文读数都在"上述改动已生效"的工作树上当场跑出（铁律 #11）
> 契约真源：`E:\NAS\homesdk\doc\ADM联动主题注册表与消息契约.md`
> 交叉裁定：`20260929-ADM三仓联动七问`、`20261001-AF-homesdk接入四问`、`20260930-AutoForge后续优化三项`、`20261001-DB目标模式与AF三题` §H、**`20261002-homesdk记账与AF-DB-DPP六件-裁定`**（本轮落地依据，§一 末）

---

## 〇、结论先说

| 步骤 | 状态 | 说明 |
|------|------|------|
| 第 0 步 homesdk 0.3.1 接入 | **①② 已交付 / ③④ 不在本回合** | ③ NAS 镜像重烤、④ AgentOps 模板是他仓 main + 生产动作，AF 单方面有代码无权生效 |
| 第 1 步 AF MQTT 桥 | **①②③④ 已交付** | ④ 为"不订阅 `butler/inbox/*`"，实测白名单门禁只有 7 处 topic 且全在契约表内 |
| 第 2 步 presence 发布 | **①② 已交付** | LWT + 优雅下线双路；`kill -9` 分支需真 broker，本机无数 |
| 第 3 步 F9 group 节点 | **①②③④ 已交付** | schema 先行（铁律 #1），原子回滚把手修在 `0dd57b2`、其回归测试在 `34a540c` |
| 第 4 步 MCP 工具面 | **①②③ 已交付** | ① 命名口径按裁定 ①A 以 AF 现名为准（计划文档已改口径，正式重发由 DCD 出）；② 加 `channel_error` 判别字段（③A）；③ 契约测试随批绿 |
| 裁定 20261002 · AF 侧六件 | **①A ②A(部分) ③A ④A 已落地；StateProvider B 排队；合并窗后验收未做** | ②A 的 `instance_id` 删除时点 = AF v2.6，本轮不删；窗口后验收需真 broker，本机无 paho-mqtt 且禁 pip install |
| 第 5 步 后续优化三项 | **①②③④ 已交付** | 逐条 file:line 见 §二 |
| 第七轮审计 | **finding 属实，已修** | 详见 `docs/audit/审计报告_第七轮_核实与修复.md`；第八轮尚未落 `docs/audit`（本回合实测目录内最新即第七轮，mtime `10-02 11:46`） |
| 门禁肥化防护（计划外补刀） | **已交付** | `gates.sh` 计数棘轮 + `.gates-tally.txt`（上限 104），三条变异实测能红；AgentOps 模板同步去反引号缺陷 |
| F4 ③ / F7 前端残留 | **已收口** | `/evidence` 生产态证据视图上线；真机下发的事件回放与撤销按钮端到端真点通（读数见 §一末） |
| 部署 | **未部署** | 铁律 #3：文档/本地阶段不部署，镜像重烤须进合并停机窗 |

---

## 一、逐步落点与验收证据

### 第 0 步 ①②：vendor 升级 + 时区接入机制层

| 项 | 实测证据 |
|----|----------|
| `pyproject.toml` pin | `pyproject.toml:48` → `homesdk>=0.3.1`（不是 `==`，允许库侧前向修复） |
| 机制层主路径 | `src/autoforge/af_time.py:110-116` `homesdk_time()` 取 `homesdk.time`；`:149-153` 主路径调机制层，缺席才走 AF fallback |
| 键名口径（裁定 §五 的 AF 半边） | `af_time.py:90-103`：规范键 `HOMESDK_TZ`，`HOMESDK_` 前缀优先于裸键，与 `homesdk.config` 同一约定；`:84-85` `AF_TZ` 降为过渡别名 |
| 时区例 | `tests/unit/test_af_house_tz.py` 在本轮 2602 项全绿内含 |

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
| ② 发 `af/automation/fired|failed`，**不 retained** | `:51-52` 主题常量、`:258-262` `publish_fired/publish_failed`；`:240-252` 信封 `{trace_id, ts, automation_id, ref}` |
| ③ 只读 `ma/insights` → 编译候选 → 进审批，不自动部署 | `:53` `INSIGHTS_TOPIC`、`:311` 回调只认该主题、`:340-378` reject 缺 `hypothesis_id`/`conf`、`conf` 被 `INSIGHT_CONF_CAP` 封顶后 `proposal_sink.submit(..., source="ma")` |
| ④ 不订阅 `butler/inbox/*` | 白名单门禁实测：`✓ 主题白名单门禁干净（7 处 topic 字面量全部在契约表内）`（`scripts/check_topic_whitelist.py`，本轮 gates 内） |

`ts` 字段口径：跨仓时间戳走家庭墙钟（契约表 §四），本轮 commit `d602e93` 把信封对齐到 `homesdk.time` 的墙钟口径。

### 第 2 步：presence

| 子任务 | 落点 |
|--------|------|
| ① `advertise` 发 retained `adm/autoforge/status` + `caps` | `af_mqtt_bridge.py:218-220`；`:148` caps 载荷注释——**桥在 L1，不许 import `af_mcp`**，`tools` 由入口层（L2）传入 |
| ② LWT 离线 + 优雅下线双路 | `:220`（`presence.advertise` 带 LWT，覆盖异常断连）+ `:297-298`（`offline=True` 显式 retained offline，覆盖正常退出） |

⛔ 未实测部分（诚实标注，铁律 #5：EXEMPT ≠ VERIFIED）：`kill -9` 后由 broker 代发 `offline` 需要真 MQTT broker；本机无 paho-mqtt 且**禁 pip install**，桥的测试用 duck-typed client 覆盖到"调用形态正确"，未覆盖 broker 行为。

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
| ④A 洞察队列持久化 | 新模块 `src/autoforge/af_insight_queue.py`（`InsightRecord:41` / `InsightQueue:68` / `PersistentInsightSink:174` / `InsightQueueFull:36`，`DEFAULT_LIMIT=500`）；桥侧工厂 `af_mqtt_bridge.py:149` `make_durable_ask_sink`；接线 `af_cli.py:1315,1330`（真机/dry-live 起桥时落 `{store_root}/insight_proposals`）；交接 `af_service.py:500 approve_insight` / `:551 reject_insight`；HTTP `af_api.py:733 _insight_queue`、`:742 /api/insights/pending`、`:751 /api/insights/approve`、`:761 /api/insights/reject` | `tests/contract/test_af_insight_queue_contract.py` **13 项**（本回合新增）：落盘后换队列实例仍读得到、入桥 conf 被 `INSIGHT_CONF_CAP=0.59` 封顶仍在 ask 带、队列满 `InsightQueueFull` 且不丢旧条、approve 只生成 `af_pending` 一条 `af_save` 且归档目录 `*/v*.json` 为空（**没部署**）、重复 approve 409、无 IR / 两条自动化 400 且记录仍在 pending、未知 404、只读实例 200 读 + 503 写、坏 JSON 进 `stats()["unreadable"]`、结构约束（队列模块不 import `af_apply/af_pending/af_service/af_deploy/af_executor`，也不持有 `approve`/`deploy` 方法） | 队列没有 UI（`/api/insights/pending` 已可读，面板未做）；`ProposalManager` 的进程内 ask 档保留给测试与分诊，两条路径并存是刻意的 |
| §四 StateProvider | A（整段 fail-closed）已在 commit `628502a`（第七轮修复）落地，本轮无改动 | `tests/contract/test_state_provider_policy.py` **14 项**（本回合 `--collect-only` 实测）；裁定"共同要求：漂移必须以 `entity_drift` 记账、不许静默"的链是 `af_instance.py:374-392` 空快照回退 → 表达式逐条抛 `UnknownEntity` → `af_executor.py:596-609` `_soft_fail` 记 `ENTITY_DRIFT`（带 `node_id`/`instance_id`/`entity_id`） | **B（逐实体可见漂移）本轮不做**，启动条件按裁定：`af_instance._refresh_snapshot` 下次被真实改动时，或 AF v2.6 窗口。裁定把整段口径判为**有意设计**（不是遗留），所以 B 是加功能不是修 bug |
| §一 合并停机窗 | AF 侧无代码动作 | — | 窗后验收四项（`compose ps` + `/health` 200 + `mosquitto_sub` 抓到一条含家庭墙钟 `ts` 的 `af/automation/fired` + `adm/autoforge/status` retained `online`）**缺任一项即第 0 步未完成**，本机无 paho-mqtt 且禁 pip install ⇒ 只能随窗做 |

**④A 为什么这样切**：裁定要的是"重启不丢" **且** "MA 来源与人来源在部署面上不同权"。所以队列只落盘、不持 deployer；`approve` 走 `submit_pending` 的常规路径（静态扫描第一道闸 + per-agent 熔断），与人工提交同闸，落 `af_pending` 后**仍需** af_pending 的 approve 才部署。两道闸不是冗余：前者管"MA 投的东西不许直接跑"，后者管"任何人提交的东西都要人批"。

**铁律 #8 的反证**：`/api/insights/*` 三个端点最初没走 `_svc`（错误映射垫片），`ServiceError` 会以未捕获异常逃逸——本回合这批测试**先红后绿**：修之前 `5 failed, 20 passed`（RC=1），把 400/404/409 包进 `_svc` 后 `25 passed`（RC=0）。红的是判据本身，不是环境。

**棘轮自己咬了一口**：新增三处 `ok=True` 字面量被计数棘轮判红（`全量违规 107 条 / 登记上限 104 条`，RC=1）。处置是**删假安心字段**而不是上调上限：查询/写回成败由 HTTP 状态表达（与 `/api/evidence/prod` 同一口径），`_insight_queue()` 在无 store 时改抛 503 而不是 200 带 `ok:false`。修完 `104 / 104`、RC=0。

### 第 5 步：后续优化三项（裁定全部落地）

| 子任务 | 验收点 | 实测落点 |
|--------|--------|----------|
| ① 单写者租约，抢不到锁降级只读（裁定 A） | serve 启动 `try_acquire` 失败→只读 | `src/autoforge/af_cli.py:1365` `readonly = not _lock.try_acquire()` → `:1375` 传给 `build_app(..., readonly=readonly)`；`af_api.py:340-348` `_readonly_guard`，挂在 `:449 /api/build`、`:457 /api/bind`、`:465 /api/sim`、`:528 /api/spec/compile`、`:696 /api/live/run`、`:716 /api/undo/{deploy_id}`（铁律 #6 写面 503） |
| ② `af_persist` 加校验和（裁定 B，不做全量 eventlog） | 补 SHA256，对齐 `af_store` | `src/autoforge/af_persist.py:40` `_SHA256_KEY = "_sha256"`、`:78` 计算、`:84` 校验（无字段=旧格式视为通过，向后兼容） |
| ③ import-linter 分层（裁定 B→A 渐进：先告警） | 配置 + 违规清单 ≈0 | `pyproject.toml:79-95` `[tool.importlinter]` 契约 "Service boundary never imported by kernel"；实测 `lint-imports` → **Contracts: 1 kept, 0 broken**；`.github/workflows/ci.yml:91-92` 的 `continue-on-error: true` 是**观察窗**（DCD 观察裁定要求保留，不是假绿），真正的架构门禁是 `scripts/check_imports.py` → `Baseline lock: 96 modules, 0 violations`（本回合 +1 = 新增 `af_insight_queue`；括号里的模块数是读数，判红的是 `violations != 0`） |
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

⚠️ 一条口径事实（登记，不等于已解决）：AF 的 **UI 类型检查今天不是门禁**。`npx vue-tsc --force` 有 16 条既有错误（`MetricsView`/`OverviewView`/`SpecEditorView`/`VersionsView`/`vite.config.ts`），`npx vue-tsc -b` 增量模式还会因缓存**假报 rc=0**。本回合只修掉了我改动的那个文件里的真错（`ui/src/api/client.ts` 用了 `WatchListResponse` 却没 import）。把 `vue-tsc` 升为 CI 硬门要先清这 16 条，属独立工作量，未塞进本批（见 §六）。


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

## 四、审计侧

第七轮唯一 finding 在 HEAD 上**属实**（不是已修项的重报）：`HAStateProvider` / `HassStateProvider` 对未知实体静默跳过，而仿真侧 `InMemoryStateProvider` 抛 `UnknownEntity`，同一 IR 两判相反。短路口是 `af_ir/expr.py:514-517`（`and`→`all()`、`or`→`any()` 生成器表达式，未求值分支根本不碰 `Snapshot.get`），所以生产把条件判真、仿真把条件判假。
修复落点 5 处 + 契约测试 `tests/contract/test_state_provider_policy.py`（14 项，含两条铁律 #8 的"能变红"反证）。完整核实链、"连续六轮未修复"四条重验表、部署声明见 `docs/audit/审计报告_第七轮_核实与修复.md`。

⚠️ 审计口径事实：第六轮与第七轮读的是**同一个** zip 快照 `zip-snapshot-2026-09-29T22:43`（不是 GitHub HEAD），故其"连续多轮未修"多数条目已在 HEAD 修复；引用审计报告前须按铁律 #11 先对 commit 复测。

## 五、已提 / 待提 DCD

| # | 事项 | 状态 |
|---|------|------|
| 1 | 第 4 步契约面四问：MCP 工具面命名口径（`draft/verify/deploy` vs `af_draft`/`af_apply`）、`af/automation/fired` 的 `ref` 语义（实例 id 还是 deploy ref）、`/api/asks/answer` `ok=False(inbox_key_missing)` 的 DB 侧处置、`ma/insights` 提案队列是否要求持久化 | `inbox/20261002-AF-第4步契约面四问-决策申请.md` → **已裁定（①A ②A ③A ④A，全 A）**，AF 侧四条均已落地（见 §一 末"裁定 20261002"表） |
| 2 | 第 0 步 ③④：0.3.1 记账缺口（源树 `time.py` 未入库 + NAS `VERSIONS.txt` 无 0.3.1 条目 ⇒ sha 不可复现）+ §四 合并停机窗排期与授权 | `inbox/20261002-homesdk0.3.1记账缺口与AF镜像重烤合并窗-决策申请.md` → **记账半边已裁定并已执行**（DCD §〇：源码+契约表入库 `5e4ba33`、VERSIONS 补条目、以重建 `b4b5d6bb…` 为权威，AF pin 与 vendored wheel 已跟新）；**镜像重烤半边 = 合并窗内做，尚未生效**（窗口未开） |
| 3 | 第七轮 `StateProvider` 统一 fail-closed 的方向与代价（整段软失败 vs 逐实体可见漂移） | `inbox/20261002-AF-StateProvider统一fail-closed的方向与代价-决策申请.md` → **已裁定：A 先行 + B 排队**，B 启动条件 = 下次真实改动 `af_instance._refresh_snapshot` 或 AF v2.6；整段口径被裁定为**有意设计**；漂移须以 `entity_drift` 记账 |
| 4 | 第 0 步 ④ 的记账载体：`E:/NAS/AgentOps` **不是 git 仓** ⇒ 已授权改的门禁模板无 commit、无 sha、不可回滚；三仓 CI 都依赖它，却没有任何版本账 | `inbox/20261002-AgentOps门禁模板无版本载体-决策申请.md`（**已提交，尚未裁定**）——本批 20261002 裁定的申请来源六件里**不含这一件**，实测裁定文首"申请来源"清单逐项对得上，唯独缺它。缺陷仍在：模板 sha `887c33dd…` 无版本账，AF 侧不动他仓 |

## 六、未在本版做（登记，不静默）

- 第 0 步 ③④：镜像重烤 + AgentOps 模板生效 = 合并停机窗内的动作（裁定 §一 已把顺序写死：**账 → wheel → 镜像 → 模板**；回滚反序 **模板 → 镜像 → wheel → 账**）。账与 wheel 两件已由 DCD 完成，AF 的 vendored wheel 也跟上；**后两件未做**。
- 窗后 AF 侧验收四项（裁定原文，缺任一项即该步未完成、不许用"配置正确只是没抓包"过账）：`compose ps` 起来、`/health` 200、`mosquitto_sub` 抓到一条含家庭墙钟 `ts` 的 `af/automation/fired`、`adm/autoforge/status` retained `online`。本机无 paho-mqtt 且禁 pip install ⇒ 无法在窗前进"半截实测"，这条一直是 EXEMPT 不是 VERIFIED。
- 第 2 步 `kill -9` → broker 代发 offline 的真 broker 验收：同上，须进窗随镜像重烤一次跑。
- ②A 的 `instance_id` 过渡字段**未删**（删除时点 = AF v2.6，属破坏性变更须与窗口同做）。
- §四 的 B（逐实体可见漂移 + `entity_drift` 记账）**未做**，按裁定的启动条件排队：下一次真实改动 `af_instance._refresh_snapshot` 时顺手做，或 AF v2.6。
- ④A 的队列**只有 API，没有面板**：`/api/insights/pending` 可读、approve/reject 可写，UI 上还没有"MA 洞察"这一屏（下一步做面板时，空态与"approve ≠ 部署"的措辞必须照 `EvidenceView` 的口径写，不许写"点了就上线"）。
- `BoundedCache` 基类收敛（第五轮遗留）与"实现间契约不一致"的**静态**门禁：本轮以契约测试覆盖，未做静态门禁。
- `vue-tsc` 未进 CI：既有 16 条类型错误要先清（§二 口径事实）。**当前 AF 的 UI 没有类型门禁**，本批未触碰 UI，这条区别不变。
- 证据面板的 `evicted_automations` 只做"提示有自动化被挤出内存"，未做跨进程持久化——**注意这与 ④A 不是同一个问题**：④A 裁的是 MA 洞察提案队列（已持久化），证据档的进程内清零仍是遗留。

已收口（上一版登记、本版实测销账）：
- ~~`ui/src/views/LiveView.vue` `events=undefined` ⇒ `_replay_live` 空转~~ → 补事件脚本输入 + 回放读数，F7 端到端真点通（`dep-28bf327fe8d7`，假 HA `turn_on`→`turn_off`，设备回 `off`）。
- ~~F4 ③ 监护视图缺失~~ → `/#/evidence` 上线，三档并列 + 诚实空态，读数与 `/api/evidence/prod` JSON 逐项一致。
- ~~`/api/asks/answer` 的失败语义未裁（通道失败还是业务失败）~~ → 裁定 ③A：`channel_error` 字段，两个方向都有断言钉住。
- ~~`ma/insights` 提案重启即清零~~ → 裁定 ④A：`af_insight_queue` 落盘队列，approve 只交接不部署。
- ~~0.3.1 wheel sha 不可复现~~ → 裁定 §〇：DCD 补账 + 重建产物 `b4b5d6bb…` 为权威，AF vendored/pin 跟新，且 wheel 内 `time.py` 与源树逐字节相同（实测见 §一）。

---

—— AutoForge 开发 · 2026-10-02
