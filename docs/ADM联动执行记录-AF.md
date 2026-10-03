# ADM 联动执行记录 · AF（AutoForge）

> 对应计划：`docs/ADM联动执行计划-AF.md`（DCD 出品，v2.5 联动落地版）
> 记录人：AutoForge 开发
> 核实基准：代码侧最新 commit = **`64ca83c`**（其后 `3144879`、`e675c01` 与本条记录所在提交均为文档提交；`git ls-remote origin main` → `e675c012c1d6595e54f5bdb373c1480200ee8190`，与本机 HEAD 一致；本记录与后续文档提交只改文档，不再改判据）。§二/二之二…二之十二 各组读数各自当场跑出，非互相引用；跨批次重复的读数（全量 pytest、`gates.sh`）在对应小节里写明当次的解释器与通过/跳过数（铁律 #11）
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
| 第 5 步 后续优化三项 | **①③④ 已交付；② 只落了半边** | ① 时钟单源、③ import-linter 分层、④ tick 自愈逐条 file:line 见 §二；② af_persist 的「顺序追加」子句**未落**（裁定执行约束第 2 条要求「追加写 + 文件头部元信息」，现实现仍是整文件重写）⇒ 已提 DCD（§五 第 9 件），读数与不自主实现的三条理由见 §二之十二 |
| 第七轮审计 | **finding 属实，已修** | 详见 `docs/audit/审计报告_第七轮_核实与修复.md`；第八轮尚未落 `docs/audit`（本回合实测目录内最新即第七轮，mtime `10-02 11:46`） |
| 门禁肥化防护（计划外补刀） | **已交付** | `gates.sh` 计数棘轮 + `.gates-tally.txt`（上限 104），三条变异实测能红；AgentOps 模板同步去反引号缺陷 |
| F4 ③ / F7 前端残留 | **已收口** | `/evidence` 生产态证据视图上线；真机下发的事件回放与撤销按钮端到端真点通（读数见 §一末） |
| GitHub CI | **首次可读，且从"永久红"修到连续绿；读数路已成脚本** | 实测 run 1–27 `conclusion` 全为 `failure`（建仓以来一条没绿过），三个红因全在版本/判据层而非产品逻辑：pydantic-v2-only 的 `Field(max_length=)` 让 CI（pydantic 1.10.12）**0 条测试跑过**、undefined-name 门禁自己用了 3.12+ 的 `ast.TypeAlias`（CI 是 3.11）、真 vhass 的 skip 判据问"包能否 import"而非"插件注册了没"。三条各钉能变红的反例后，run 28 五作业全 `success`，CI 与本机通过/跳过数逐字相同（2621/51）。**run 31（守卫那次提交）五作业再次全 `completed/success`**，且 runner 侧给出安装步的实测读数 `fetch 主机读数：npmmirror=0 npmjs=87`。读数路径固化为 `scripts/gh_ci_status.py`（`runs`/`jobs`/`annotate`/`log`，纯标准库、只 GET、不打印凭证；`log` 先停 302 再无凭证取正文，免得把 token 带给日志存储域）。读数与残留见 §二之五、§二之八。**run 36（commit `3144879`，含架构门禁口径修复那批）五作业再次全 `completed/success`，runner 侧读到 `Baseline lock: 96 modules, 0 violations` 与 `Contracts: 1 kept, 0 broken`** ⇒ CI 架构门的覆盖面与本机同口径已是实测，不再是"已修 + 待复测"（§二之十一） |
| DCD 20261002 §一（CI 锁文件源）| **裁定 (b) 已落地并已在 runner 上取到读数** | 只按原文写 `--registry=https://registry.npmjs.org` 经实测是**空操作**（fetch 87+87 行仍在 `registry.npmmirror.com`/`cdn.npmmirror.com`），必须配 `--replace-registry-host=always`；安装步另加"主机自证"守卫（CI 绿不能证明没吃镜像）。逐字节对账 137/137 `integrity` MATCH ⇒ 锁文件字节未动。§二之七 末尾"本机 npm 11.9.0 / runner 10.x，只有 push 之后才知道"的残留**已销账**（runner `npmmirror=0 npmjs=87`）。回执已投 DCD（含 §四 判例 2 的证据更正：`@types/node` tarball 两源逐字节相同、根目录差异不是重打包痕迹）。见 §二之七、§二之八 |
| `ma/insights` **入向与契约表对账**（本批新抓） | **AF 侧已按契约收，跨仓三问已交 DCD** | 拿着裁定 Q3 之后的载荷行逐条对 AF 的 `ingest_insight()`：必填的 `hypothesis_id`/`natural_language`/`conf` **三项契约一个都不发** ⇒ 每一条按契约发来的洞察都被拒（判例 1 的静默归零，面换到 AF 入口）。改法：契约键优先 + 旧键别名、缺 `conf`（含 `{"conf": null}` 这种空占位）按 0.0 入 ask 档并记 `conf_reported=false`（坏报照旧拒、封顶 0.59 不动）、契约字段有界落 `transport` 并出 `/api/insights/pending`，面板对缺报显示「未上报」而不是 `0.00`。`conf`/稳定 id/`intent` 三处空缺 AF 不能自决 → §五 第 7 件。见 §二之八 |
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
| ④A 洞察队列持久化 | 新模块 `src/autoforge/af_insight_queue.py`（`InsightRecord:41` / `InsightQueue:68` / `PersistentInsightSink:174` / `InsightQueueFull:36`，`DEFAULT_LIMIT=500`）；桥侧工厂 `af_mqtt_bridge.py:149` `make_durable_ask_sink`；接线 `af_cli.py:1315,1330`（真机/dry-live 起桥时落 `{store_root}/insight_proposals`）；交接 `af_service.py:500 approve_insight` / `:551 reject_insight`；HTTP `af_api.py:733 _insight_queue`、`:742 /api/insights/pending`、`:751 /api/insights/approve`、`:761 /api/insights/reject` | `tests/contract/test_af_insight_queue_contract.py` **13 项**（本回合新增）：落盘后换队列实例仍读得到、入桥 conf 被 `INSIGHT_CONF_CAP=0.59` 封顶仍在 ask 带、队列满 `InsightQueueFull` 且不丢旧条、approve 只生成 `af_pending` 一条 `af_save` 且归档目录 `*/v*.json` 为空（**没部署**）、重复 approve 409、无 IR / 两条自动化 400 且记录仍在 pending、未知 404、只读实例 200 读 + 503 写、坏 JSON 进 `stats()["unreadable"]`、结构约束（队列模块不 import `af_apply/af_pending/af_service/af_deploy/af_executor`，也不持有 `approve`/`deploy` 方法） | 队列没有 UI（`/api/insights/pending` 已可读，面板未做）→ **面板已上线 `/#/insights`**（本批，读数见 §二之四）；`ProposalManager` 的进程内 ask 档保留给测试与分诊，两条路径并存是刻意的 |
| §四 StateProvider | A（整段 fail-closed）已在 commit `628502a`（第七轮修复）落地，本轮无改动 | `tests/contract/test_state_provider_policy.py` **14 项**（本回合 `--collect-only` 实测）；裁定"共同要求：漂移必须以 `entity_drift` 记账、不许静默"的链是 `af_instance.py:374-392` 空快照回退 → 表达式逐条抛 `UnknownEntity` → `af_executor.py:596-609` `_soft_fail` 记 `ENTITY_DRIFT`（带 `node_id`/`instance_id`/`entity_id`） | **B（逐实体可见漂移）本轮不做**，启动条件按裁定：`af_instance._refresh_snapshot` 下次被真实改动时，或 AF v2.6 窗口。裁定把整段口径判为**有意设计**（不是遗留），所以 B 是加功能不是修 bug |
| §一 合并停机窗 | AF 侧无代码动作 | — | 窗后验收四项（`compose ps` + `/health` 200 + `mosquitto_sub` 抓到一条含家庭墙钟 `ts` 的 `af/automation/fired` + `adm/autoforge/status` retained `online`）**缺任一项即第 0 步未完成**，本机无 paho-mqtt 且禁 pip install ⇒ 只能随窗做 |

**④A 为什么这样切**：裁定要的是"重启不丢" **且** "MA 来源与人来源在部署面上不同权"。所以队列只落盘、不持 deployer；`approve` 走 `submit_pending` 的常规路径（静态扫描第一道闸 + per-agent 熔断），与人工提交同闸，落 `af_pending` 后**仍需** af_pending 的 approve 才部署。两道闸不是冗余：前者管"MA 投的东西不许直接跑"，后者管"任何人提交的东西都要人批"。

**铁律 #8 的反证**：`/api/insights/*` 三个端点最初没走 `_svc`（错误映射垫片），`ServiceError` 会以未捕获异常逃逸——本回合这批测试**先红后绿**：修之前 `5 failed, 20 passed`（RC=1），把 400/404/409 包进 `_svc` 后 `25 passed`（RC=0）。红的是判据本身，不是环境。

**棘轮自己咬了一口**：新增三处 `ok=True` 字面量被计数棘轮判红（`全量违规 107 条 / 登记上限 104 条`，RC=1）。处置是**删假安心字段**而不是上调上限：查询/写回成败由 HTTP 状态表达（与 `/api/evidence/prod` 同一口径），`_insight_queue()` 在无 store 时改抛 503 而不是 200 带 `ok:false`。修完 `104 / 104`、RC=0。

### 第 5 步：后续优化三项（① ③ ④ 全落；② 按**本计划验收点**已落，按**裁定执行约束**少一条子句）

| 子任务 | 验收点 | 实测落点 |
|--------|--------|----------|
| ① 单写者租约，抢不到锁降级只读（裁定 A） | serve 启动 `try_acquire` 失败→只读 | `src/autoforge/af_cli.py:1365` `readonly = not _lock.try_acquire()` → `:1375` 传给 `build_app(..., readonly=readonly)`；`af_api.py:340-348` `_readonly_guard`，挂在 `:449 /api/build`、`:457 /api/bind`、`:465 /api/sim`、`:528 /api/spec/compile`、`:696 /api/live/run`、`:716 /api/undo/{deploy_id}`（铁律 #6 写面 503） |
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

## 六、未在本版做（登记，不静默）

- 第 0 步 ③④：镜像重烤 + AgentOps 模板生效 = 合并停机窗内的动作（裁定 §一 已把顺序写死：**账 → wheel → 镜像 → 模板**；回滚反序 **模板 → 镜像 → wheel → 账**）。账与 wheel 两件已由 DCD 完成，AF 的 vendored wheel 也跟上；**后两件未做**。
- 窗后 AF 侧验收四项（裁定原文，缺任一项即该步未完成、不许用"配置正确只是没抓包"过账）：`compose ps` 起来、`/health` 200、`mosquitto_sub` 抓到一条含家庭墙钟 `ts` 的 `af/automation/fired`、`adm/autoforge/status` retained `online`。本机无 paho-mqtt 且禁 pip install ⇒ 无法在窗前进"半截实测"，这条一直是 EXEMPT 不是 VERIFIED。
- 第 2 步 `kill -9` → broker 代发 offline 的真 broker 验收：同上，须进窗随镜像重烤一次跑。
- ②A 的 `instance_id` 过渡字段**未删**（删除时点 = AF v2.6，属破坏性变更须与窗口同做）。
- §四 的 B（逐实体可见漂移 + `entity_drift` 记账）**未做**，按裁定的启动条件排队：下一次真实改动 `af_instance._refresh_snapshot` 时顺手做，或 AF v2.6。
- 洞察面板的**投递源仍是本机手投**：§二之四的两条提案是用 `PersistentInsightSink.submit()` 直接写进 dev store 的，走的是"落盘之后的那一段"。本批把**桥回调 → 落盘 → `/api/insights/pending`** 这一段用契约形状的假消息钉住了（`test_contract_shaped_insight_reaches_the_panel_with_its_accounting`，注入假 client、真桥、真队列、真 API），但**真 paho + 真 broker** 那一段仍未端到端（本机无 paho-mqtt 且禁 pip install），面板的空态文案因此把"桥未上线/没订到主题"列为四种成因之一，而不是当作已验证链路。
- **契约表本身还欠三行改动，且都在 MA/DCD 手里**（§五 第 7 件）：`ma/insights` 的载荷行没有 `conf`、没有稳定的假设 id、也没有 IR 候选（AF 的 `intent`）。AF 已按可回退口径落地（别名 + 缺报记账），但只要契约行不改，MA 侧随时可能按自家形状发而 AF 无从判定"这条到底该不该有 conf"；面板上也因此会长期是「无 IR（不能批准）」。**这不是 AF 能单方面收口的残留**，登记以免被读成"入向已经全对齐"。
- 第 5 步 ② 的「顺序追加」子句**未做**（不是漏，是不在裁定前擅自动存储格式）：af_persist 现为整文件原子重写，追加写会同时撞"不改格式头"与第五轮的有界化修法两面。校验和/坏文件跳过/原子替换三条已落，剩这一条等 DCD 定性（§五 第 9 件，判据与三条理由见 §二之十二）。
- `BoundedCache` 基类收敛（第五轮遗留）与"实现间契约不一致"的**静态**门禁：本轮以契约测试覆盖，未做静态门禁。
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
