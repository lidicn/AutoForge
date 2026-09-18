# AutoForge 项目交接总览（换 Agent 继续）

> 用途：**新 Agent 接手 AutoForge 的唯一总入口**。读完本文 + `KICKOFF.md` + `docs/IR_AND_RUNTIME.md` 即可安全开工。
> 编写日期：2026-09-18　编写背景：原 Agent 完成 v0.2.0–v1.7.1 的全部交付后，需换 Agent 继续。
> 配套权威文档（本文件引用、不重复）：`KICKOFF.md`（冷启动+冻结决策+红线）、`docs/ROADMAP.md`（里程碑逐版）、`docs/IR_AND_RUNTIME.md`（IR 模型唯一真相）、`docs/NAMING.md`、`docs/HA_SEMANTIC_DIFF.md`、`docs/G1_ACCEPTANCE.md`、`docs/API_CONTRACT.md`、`README.md`。

---

## 0. 30 秒冷启动

- **这是什么**：`AutoForge` = Agent 为中心的智能家居自动化平台。让 Agent 写自动化（JSON IR / AF-Spec），机器验证正确性（两道闸），人只看自然语言。
- **代码位置**：`E:\NAS\AutoForge`（工作副本在 `e:/NAS/AutoForge`）。包名 `autoforge`，CLI 命令 `forge`，内部模块前缀 `af_*`。
- **直连 Home Assistant**，不碰 Node-RED，不自研仿真器。
- **当前最新提交**：`3a509c6`（v1.7.1，NL 实测复验与修复，已 commit、已部署 NAS）。**仓库无远程**，只在本地 + NAS 副本。
- **当前最新版本号语义**：v1.6.0（实体解析决策智能，路线图基线）→ v1.7.0（UI 合并进主仓，A/B 批次已交）→ v1.7.1（NL 实测暴露的缺陷修复）。**`pyproject.toml` 的 `version` 仍是 `0.1.0`**——版本号是里程碑标签，不是包版本，勿被误导。
- **回归基线（v1.7.1）**：本机 **529 passed / 0 failed / 0 errors**（10 项 vhass acceptance 因 Windows 环境跳过）；NAS 容器 `autoforge-api` 已部署同款代码，`/api/health` 200。
- **开工三句**：`forge build <ir>`（安全闸）→ `forge sim <ir> --vhass fake --seed <seed> --events <events>`（逻辑闸 + expect 断言）→ `forge run`（内存 Runtime）。

---

## 1. 当前进度（已完成 / 进行中 / 计划中）

### 1.1 已交付（全部 commit 入仓）

| 阶段 | 主题 | 状态 |
|---|---|---|
| G1–G7 + 真机接线 + UI 服务层 + 真机常驻监听 + P1 持久化 | 内核与治理面全栈 | ✅ |
| v0.2.0–v1.0.1 | 11 个小版本：基线对齐→emit→on event→回灌 MA→标签→导出→鉴权→跨进程→表达力→MCP 接入 | ✅ |
| v1.1.0 | 实体事实内建（设备目录 + 解析，切断对 MA 硬依赖） | ✅ |
| v1.2.0 | 断言闭环（IR `expect` + sim 断言 + `Diagnostic.hint`） | ✅ |
| v1.3.0 | 变更可信（diff 签名兜底 + 爆炸半径 + 归档归属） | ✅ |
| v1.4.0 | 治理面（待批队列 + 设备保护分级 + 凭据热重载） | ✅ |
| v1.5.0 | 经验闭环（失败归因 + 错误知识库 + 实体共现 + 解析遥测） | ✅ |
| v1.6.0 / v1.6.0-a | 实体解析决策智能（归并/优选/别名/弱信号 + 可选 ws 注册表数据源） | ✅ |
| v1.7.0 | WebUI 全功能接入：前端仓库 `autoforge-ui` 已合并进主仓 `ui/`，批次 **A（核心闭环）/ B（治理与实时）已交付**；批次 **C（经验闭环面板 + 故障注入面板）待做** | 🔨 A/B 完 |
| v1.7.1 | **NL 实测（10 条提示词）PM 侧独立复验 + 按报告 P0 修复 + 8 条 IR 补 expect 断言 + 部署 NAS**（详见 §5 / §6） | ✅ 已部署 |

### 1.2 进行中 / 待决策

| # | 事项 | 状态与建议 |
|---|---|---|
| ① | **`wait` 语义二选一（平台级决策）** | **最该拍板**。文档自相矛盾：`docs/IR_AND_RUNTIME.md` §6/§11 写「wait 计时到点 → `on_timeout`、then 边永不生效」，但 §13.2 写「轮询用 `wait`+`then`」。`af_scheduler.tick()` 把**所有非 emit 实例定时器**派发为 `on_timeout`（见 `src/autoforge/af_scheduler.py`）。v1.7.1 按现状（wait→on_timeout）修正了 `ir_06`，并在该 IR 的 `meta` 注明「若平台改判则须回滚」。选 **A**：改运行时让 wait 到期走 `then`、`on_timeout` 仅给 `ask` 超时（更符合直觉，但需同步改 `tests/unit/test_af_executor_scheduler.py::test_wait_timeout` 与 `tests/unit/test_af_persist.py` 相关用例）；选 **B**：保留现状、修 §13.2、并在 Agent 工具文档显式声明「wait 必须接 `on_timeout`」。 |
| ② | v1.7.0 批次 C | 经验闭环面板（指标/共现/遥测/错误知识库，沿用 `/api/metrics`、`/api/experience`、`/api/telemetry`、`/api/catalog/resolve-metrics`）+ 仿真故障注入面板（消费 `/api/faults`）。后端 47 端点已齐备，纯前端工作。 |
| ③ | 路线图「不排期项」 | `fn` 节点 CEL/Lua/Wasm 实装、多成员/多租户、多语言 NL、隐私脱敏、Runtime 高可用、多 agent 任务池、自动化判重——均待真实需求驱动（详见 `docs/ROADMAP.md` §不排期项）。 |

### 1.3 文档鲜度提示

`README.md` / `KICKOFF.md` / `docs/ROADMAP.md` 的"当前版本"与"下一阶段"段落**仍停留在 v1.6.0 / v1.7.0 计划中**，未更新到 v1.7.1。新 Agent 若改这些文档请同步刷新版本表；若不动则无影响（权威语义以 `IR_AND_RUNTIME.md` 与各 `交接卡_*.md` 为准）。

---

## 2. 当前架构

### 2.1 模块地图（`src/autoforge/`）

```
af_ir/           IR 数据模型 + JSON Schema（唯一真相，已冻结 v0.2.1）
af_time.py       TimeSource 抽象（生产/仿真两套；业务代码禁止直接 time.time()）
af_state.py      StateProvider + 求值段快照（Snapshot.get 支持「实体.属性」）
af_bus.py        EventBus：去重/节流/熔断/精确订阅
af_instance.py   InstanceManager：状态机/上下文/快照/vars；trigger_time 取时间源（v1.7.1 修正）
af_adapters/     HA(dry-run + HATransport)/HTTP/Mock——纯执行层（不重试/不降级）
af_executor.py   NodeExecutor：求值段、边优先级、挂起、canary、ask/timeout
af_scheduler.py  Scheduler：4 mode + 配额；⚠️ 所有非 emit 实例定时器派发为 on_timeout（见 §1.2①）
af_scanner.py    StaticScanner（安全闸全项）+ live_preflight（真机预检）
af_nl.py         NL 确定性渲染 + 覆盖率检查
af_audit.py      结构化审计
af_conf.py       G4 置信度分级自主（衰减 + 样本回灌）
af_canary.py     G4 灰度保护（漂移检测 + 回滚）
af_fault.py      G5 故障注入（五类故障 + 四类失败映射）
af_store.py      G6 版本化存储 + 置信度持久化 + diff
af_spec.py       G7 AF-Spec（文本语法 ⇄ JSON IR）
af_catalog.py    v1.1.0 设备目录 + 解析（多级兜底 resolve、决策智能、别名、遥测）
af_affordance.py v1.1.0 域状态契约表
af_registry.py   v1.6.0-a 可选 ws 四注册表抓取（websockets optional extra）
af_pending.py    v1.4.0 待批队列
af_device_acl.py / af_scanner 设备保护分级
af_experience/telemetry/error_knowledge.py  v1.5.0 经验闭环
af_service.py    服务层纯逻辑（只读 API 能力，无 Web 依赖）
af_api.py        只读 HTTP 路由层（FastAPI，forge serve）
af_runtime.py    Runtime 组装（依赖注入）
af_persist.py    P1 实例持久化与崩溃恢复
af_cli.py        forge build / sim / run(--live) / conf / diff / store / spec / serve / entities / pending …
af_vhass/        仿真夹具、FakeHA 降级、故障注入底座（is_modeled/service_effect 被 vhass 与 FakeHA 共用，v1.7.1 抽出）
```

### 2.2 数据流（两道闸铁律，顺序不可调换）

```
Agent 写 JSON IR / AF-Spec
        │
        ▼
   forge build （第一道闸：静态扫描 StaticScanner；拦截即出局，不进仿真）
        │  ok=true
        ▼
   forge sim  （第二道闸：vhass 仿真 + 时间旅行 + expect 断言）
        │  exit=0 只代表「没崩」；expect 全过才是 fully_verified
        ▼
   forge run  （内存态 Runtime；--live 真机下发到 HA，白名单+二次确认）
        │
        ▼
   NL 渲染（确定性，"看到即跑的"）
```

**v1.7.1 关键修复的链路断点**：`forge sim` 的 fake 底座之前只注册 `HAAdapter(dry_run=True)`（只记意图不翻状态），且 CLI **从不渲染 expect 报告**——导致「跑完了」被当成「跑对了」。现已改为注册 `FakeHAAdapter` 并渲染三态报告（`pass`/`fail`/`unverified`）。这两处是 §5 里 #7/#3 假绿的根因。

### 2.3 仿真底座（vhass）：两套实现

- **FakeHA**：内置降级实现，三接口（`get`/`call`/`set`）与 vhass 同形，Runtime 零改动；太阳历用本地桩。**Windows 默认走它**（pytest-homeassistant 因 `fcntl` 在 Windows 崩溃）。
- **真 vhass**（pytest-homeassistant）：Linux/WSL/Docker 用 `docker/Dockerfile.test`；8 条验收有真 vhass 复核。
- `af_vhass/__init__.py` 暴露 `is_modeled(domain, service)` / `service_effect(domain, service)`（v1.7.1 抽出，vhass 与 FakeHA 共用同一套语义）。

### 2.4 实体解析（v1.1.0 + v1.6.0）

`forge entities resolve <自然语言>` / `af_resolve_entity`（MCP）/ `GET /api/entities/resolve`。多级兜底：`direct → area_relaxed → area_split → suffix_stripped → readonly_upgrade`，派生命中一律降级置信度并标注 `match_stage`；排序插入「可动作域优先」（仅 direct/area_relaxed 启用）与「能力面大者优先」（readonly_upgrade 专用）。**陷阱条保护**：不存在的设备必须 0 候选，绝不臆造 entity_id。目录落 `{store_root}/.catalog/catalog.json`，可选 ws 注册表增强（未装则优雅降级）。

### 2.5 断言闭环（v1.2.0 + v1.7.1 属性形态）

IR 顶层 `expect` 三种形态（v1.7.1 新增属性形态）：
```json
{ "entity_id": "light.x", "state": "on" }
{ "entity_id": "climate.x", "attribute": "temperature", "value": 18, "op": "eq" }   // v1.7.1
{ "var": "turn_on_result.success", "op": "eq", "value": true }
```
三态 `pass`/`fail`/`unverified` 严格分离；`fully_verified` 比 `ok` 更严（有 unverified 或未建模动作即为 False）。属性缺失判 `unverified` **不判 fail**；未建模动作后果无法验证（诚实性）。

---

## 3. 项目历史（里程碑时间线）

> 权威逐版细节见 `docs/ROADMAP.md` 与各 `docs/交接卡_*.md`。此处只给时间线与关键转折点。

| 日期 | 事件 |
|---|---|
| 2026-09-14 | G1–G7、真机接线、UI 服务层（R1/R2+鉴权）、真机常驻监听、P1 持久化 一次性全交付；8 条验收 + NAS 真 vhass 双环境全绿 |
| 2026-09-15 | v0.2.0–v1.0.1 十一个小版本全交付（基线对齐/emit/on event/回灌 MA/标签/导出/鉴权/跨进程/表达力/MCP） |
| 2026-09-16 | v1.1.0–v1.6.0 串行主链 + v1.6.0-a 数据源薄片 全交付；实体解析决策智能落地；ALIGN 窗口（与 AutoFlow v2.3.0 同源对齐） |
| 2026-09-17 | **FFL 团队 NL 实测**（PROJECT-20260917-AF-NL，10 条提示词，Agent 生成 IR）；前端 `autoforge-ui` 合并进主仓 `ui/`，v1.7.0 A/B 批次交付（commit `663f505`） |
| 2026-09-17~18 | **v1.7.1**：PM 侧独立复验团队报告 → 按报告 P0 修复（resolve/仿真底座/expect）→ 补 8 条 IR 的 expect（抓出 #6 假绿）→ 提交 `3a509c6` → 部署 NAS（commit 后工作树干净） |

**git 提交链**（最新在上）：`3a509c6`（v1.7.1）→ `663f505`（UI 合并 v1.7.0-b）→ `32fe4f8`（MCP 指南）→ `0b3c624`（首次提交 v0.2.0–v1.6.0）。**无远程，未推送**。

---

## 4. 生态分工（避免串台）

| 代号 | 项目 | 角色 |
|---|---|---|
| **AF** | AutoForge（`E:\NAS\AutoForge`） | 自动化工厂——HA 自动化的**唯一写入方** |
| **MA** | memory-agent | 创造力源泉，提供带 conf 的假设（语义/身份/历史）；设备长期健康度归它 |
| **DB** | doubao-butler | 连接与执行，`ask` 的话术与对话由它承接 |
| **AutoFlow v1** | `E:\NAS\autoflow` | **另一条对话维护**；AutoForge 不读写其代码（但 v1.6.0 与其共享设备绑定语义，对齐点见 ROADMAP v1.6.0 三拍板） |

**铁律**：AF 管「执行事实」（ID/域/状态），MA 管「语义身份」。AF 的 `af_catalog` 切断了对 MA 取 entity_id 的硬依赖（v1.1.0 动机）。

---

## 5. FFL 团队的使用（NL 实测，必读）

### 5.1 背景

FFL 团队（本项目使用者）用 **10 条自然语言需求**驱动 Agent 生成 IR，验证 AutoForge 是否真能"人话→自动化"。项目代号 **PROJECT-20260917-AF-NL**（2026-09-17）。

- **8 条有效 IR**：`ir_01` 书房人体感应补光、`ir_02` 日落关书房灯、`ir_03` 空调温控保护、`ir_04` 离家断电、`ir_05` 电视播放联动书房灯、`ir_06` 洗澡完关卫生间灯、`ir_07` 夜间电视暂停、`ir_08` 做饭开油烟机灯光。
- **2 条判为非智能可自动化**：#9（卫生间人体存在传感器缺失）、#10（老家电非智能，无实体）。

### 5.2 测试工件位置

> ⚠️ **这些文件不在仓库内**，在用户工作区 `D:/Documents/WorkSpace/Test/`：
> - IR + seed + events：`D:/Documents/WorkSpace/Test/results/nl_test/ir_0X_*.json`、`seed_0X.json`、`events_0X.json`
> - 复验与修复报告：`D:\Documents\WorkSpace\Test\AutoForge_NL复验与修复报告_v1.7.1.md`（PM 侧独立复验 + P0 修复记录，含修复前后对照、**#6 假绿根因**、待决策项）
> 新 Agent 若要重跑，路径按上述；**若文件已转移，以报告里的 IR 内容为准**（报告内嵌了每条 IR 的关键结构）。

### 5.3 复跑命令（Windows 工作副本）

```powershell
$dir = 'D:/Documents/WorkSpace/Test/results/nl_test'
1..8 | ForEach-Object {
  $n = $_
  forge sim (Get-Item "$dir/ir_0${n}_*.json").FullName `
       --vhass fake `
       --seed (Get-Item "$dir/seed_0${n}.json").FullName `
       --events (Get-Item "$dir/events_0${n}.json").FullName
}
# 判定看输出里的「后置条件（expect）」与「判定：fully_verified / 断言失败」
```

### 5.4 v1.7.1 复验结论（新 Agent 必须知道）

1. **团队报告可信**：8 条 IR 用到的 14 个 distinct entity_id 全部命中真实 catalog（2888 实体）；build/sim 复跑 8/8 ok + 8/8 exit=0，与团队一致。
2. **但「exit=0」是假绿温床**：此前 CLI 不渲染 expect，只能看"跑完了"。补完 8 条 IR 的 `expect` 后，**#6 立刻失败**——`wait 5m` 到期走了 `on_timeout`，关灯动作 `d1` 从未执行（详见 §1.2① 的 wait 语义坑）。
3. **两个报告未识别的更深根因**（v1.7.1 已修）：
   - CLI fake 链路从未注册 `FakeHAAdapter`（只记意图不翻状态）→ `do` 看着成功、实体原地不动；
   - CLI 从不渲染 expect 报告 → 无法回答"跑对了吗"。
4. **#3 的断言空洞**：seed 已把状态播成 `cool`，原 `state ∈ {off,cool,...}` 断言恒真。v1.7.1 给 expect 加了**属性形态**（`temperature==18`），才是真军令状。
5. **#7 边界已消除**：`media_pause/play/stop` 等已在 FakeHA/vhass 建模（`playing→paused` 可验证）。

**最终态**：8/8 `build ok + sim exit=0 + fully_verified`。

---

## 6. 部署与工程纪律（红线）

### 6.1 NAS 部署流程（已验证可复现）

`e:/NAS/AutoForge` 是 NAS 的**断开副本**；改动须 `scp` 到 NAS 才对运行容器生效（容器通过卷挂载源码 `/app/src`）。

```bash
# 1) 同步前先比对远端基线，防止覆盖 NAS 上未回传的改动
#    （diff 远端 md5 vs 本地 git HEAD；若远端落后则安全 scp）
# 2) scp 改动的 src 文件
scp -i <key> -o StrictHostKeyChecking=no \
    src/autoforge/<file> lidicn@192.168.2.200:/vol1/1000/docker/autoforge/src/autoforge/<file>
# 3) 重启容器（源码是 bind mount，无需重建镜像）
ssh lidicn@192.168.2.200 'docker restart autoforge-api'
# 4) 校验
ssh lidicn@192.168.2.200 'curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8787/api/health'
```

- SSH key：`C:\Users\lidicn\.ssh\id_ed25519`；SSH 客户端：`C:\Users\lidicn\.ssh\openssh\OpenSSH-Win64/ssh.exe`（PowerShell 远程命令**单引号包裹**，否则 `$(...)` 被本地展开）。
- 容器：`autoforge-api`（端口 8787，归档挂 `/vol1/1000/docker/autoforge-store`）；UI 产物挂 `/vol1/1000/docker/autoforge/ui/dist`。
- **备份习惯**：部署前 `cp -r src/autoforge /vol1/1000/docker/autoforge/backups/autoforge.bak.<tag>`，并从挂载目录移出（不污染 `/app/src`）。
- 回滚：用备份覆盖 `/vol1/1000/docker/autoforge/src/autoforge/` 后 `docker restart`。

### 6.2 代码纪律

- **git：只在本地 commit，绝不 push**（无远程）；提交信息用中文 + 范围前缀（`fix:`/`feat:`/`docs:`/`chore:`），参考 `3a509c6`。
- **不提交敏感文件**：`.env`/`credentials.json`/`*.db` 已在 `.gitignore`；提交前 `git status` 扫一眼确认无 `.env`/token。
- **测试环境红线**：
  - Windows 本机：**FakeHA** 仿真（pytest 默认 `-p no:homeassistant` 防整轮崩溃）；10 项真 vhass acceptance 因此跳过。
  - 真 vhass 复核：Linux/WSL/Docker（`docker/Dockerfile.test`）。
  - 双环境全绿 = DoD 第 4 条。
- **绝不触碰的红线**（KICKOFF §3）：不碰 Node-RED；不自研仿真器（vhass 必须基于 pytest-homeassistant）；不上原生 Python 沙箱；业务代码禁止直接 `time.time()`（走 TimeSource）；G1 不写真实 HA（HA 适配器默认 `dry_run=True`）。
- **IR 冻结（v0.2.1）**：节点 7 种、边 6 种已冻结；改 IR Schema 走 `af_ir/schema/ir.schema.json`，且必须同步 `af_ir/models.py` 与 Schema 测试（`scripts/gen_schema_check.py`）。
- **两文档唯一真相**：`IR_AND_RUNTIME.md`（IR 语义）+ `KICKOFF.md`（冻结决策/红线）。代码/文档冲突时以这俩为准。

### 6.3 测试与回归

```bash
# 全量（Windows，FakeHA）
python -m pytest tests -q
# 真 vhass 复核（需 Linux/WSL/Docker）
docker run --rm -v /vol1/1000/docker/autoforge:/app autoforge-test
# 单文件
python -m pytest tests/unit/test_v1_7_nl_resolve_and_sim.py -q
```
新增能力**必须带单测**（v1.7.1 新增 `tests/unit/test_v1_7_nl_resolve_and_sim.py` 23 项：多级兜底/陷阱保护/服务建模/属性 seed/属性 expect/CLI 适配器）。

---

## 7. 给新 Agent 的"下一步"清单

按价值与依赖排序：

1. **【最高优先·需你拍板】决断 §1.2① 的 `wait` 语义**（A 改运行时 / B 改文档）。这影响所有 Agent 生成的 `wait` 节点，且 §13.2 与 §6/§11 的矛盾不消除会持续坑人。建议选 A（更符合直觉，已验证改动范围可控）。
2. **v1.7.0 批次 C**：前端经验闭环面板 + 故障注入面板（后端端点已齐备，纯 UI 工作）。
3. **若接新需求**：走 `docs/ROADMAP.md` §不排期项（fn 节点 / 多租户 / 多语言 NL 等），每个独立成版、双环境全绿再合。
4. **文档同步**（可选）：把 `README.md`/`KICKOFF.md`/`ROADMAP.md` 的"当前版本"刷新到 v1.7.1，消除 §1.3 的鲜度偏差。
5. **勿做**：不要为了"统一版本号"去改 `pyproject.toml` 的 `version`（它是包版本，里程碑用 commit message 标签表达）；不要动 Node-RED/自研仿真器/原生沙箱红线。

---

## 8. 一页速查表

| 想做… | 入口 |
|---|---|
| 写/编译 IR | `forge build <ir>`（安全闸） |
| 逻辑仿真 + 断言 | `forge sim <ir> --vhass fake --seed <s> --events <e>` |
| 真机下发 | `forge run <ir> --live --confirm --ha-token …`（白名单 + 二次确认） |
| 常驻监听 | `forge watch --persist-dir …`（订阅 HA SSE） |
| 查设备/拿真 ID | `forge entities resolve <自然语言>` / MCP `af_resolve_entity` |
| 服务层 API | `GET /api/health`、`/api/graphs`、`/api/build`、`/api/sim`、`/api/catalog`、`/api/entities/resolve`、`/api/pending`、`/api/metrics`、`/api/experience`、`/api/telemetry`、`/api/faults`（权威：`GET /openapi.json`） |
| MCP 接入 | `forge mcp`（21 工具） |
| 服务启动 | `forge serve --host 0.0.0.0 --port 8787 --store-root /data --examples /app/examples/ir --ui-dir /ui`（NAS 容器命令） |
| 待批队列 | `forge pending list\|approve\|reject` / `POST /api/pending/*`（MCP 不注册 approve，执行/批准物理分离） |
| 部署 NAS | 见 §6.1（scp + docker restart，先备份） |

---
*本文件为换 Agent 交接总览，权威细节以 `KICKOFF.md` / `docs/ROADMAP.md` / 各 `交接卡_*.md` / `docs/IR_AND_RUNTIME.md` 为准。如发现本文与那些文档冲突，以被引用的权威文档为准，并顺手修本文。*
