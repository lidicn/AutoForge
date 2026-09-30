# AutoForge 项目交接单（换 Agent / 换对话接手 · 2026-09-30）

> 用途：**新 Agent 接手 AutoForge 的唯一总入口**。读完本文 + `KICKOFF.md` + `docs/architecture/IR_AND_RUNTIME.md` 即可安全开工。
> 编写背景：原 Agent 完成 v0.2.0 → v2.4（F11–F13）全部交付 + 2026-09-30 三项运行时加固后，需换 Agent / 新对话继续。
> 本文件取代旧 `docs/archive/交接总览_换Agent继续本项目.md`（v1.7.1 时代，已归档）。
> 配套权威文档（引用、不重复）：`KICKOFF.md`（冻结决策/红线）、`docs/roadmap/版本路线图.md`（**唯一路线图**）、`docs/plan/版本开发计划.md`（**唯一计划**）、`docs/architecture/IR_AND_RUNTIME.md`（IR 语义唯一真相）、`docs/architecture/NAMING.md`、`docs/architecture/HA_SEMANTIC_DIFF.md`、`docs/reference/G1_ACCEPTANCE.md`、`docs/reference/API_CONTRACT.md`、`README.md`。

---

## 0. 30 秒冷启动

- **这是什么**：`AutoForge` = Agent 为中心的智能家居自动化平台。让 Agent 写自动化（JSON IR / AF-Spec），机器验证正确性（两道闸），人只看自然语言。
- **代码位置**：`E:\NAS\AutoForge`（工作副本在 `e:/NAS/AutoForge`）。包名 `autoforge`，CLI 命令 `forge`，内部模块前缀 `af_*`。
- **直连 Home Assistant**，不碰 Node-RED，不自研仿真器。
- **当前最新版本语义**：v2.4（经验闭环 + 预测，F11–F13 已交付并部署 NAS）。下一里程碑 **v2.5（F14 NL→IR 可逆编译器，未启动）**。`pyproject.toml` 的 `version` 仍是包版本（非里程碑），勿被误导。
- **git 远程（重要变更）**：`git@github.com:lidicn/AutoForge.git` 的 **`main`** 分支。**本地 `master` 跟踪 `origin/main`**；提交后 `git push` 即推 `origin/main`。旧交接单写的"无远程、绝不 push"已**作废**。
- **回归基线（v2.4）**：本机全量 `python -m pytest tests -q`（Windows + FakeHA）应全绿（除 10 项真 vhass acceptance 因 Windows 跳过）；NAS 容器 `autoforge-api` 已部署同款代码（image-bake），`/api/health` 200。
- **开工三句**：`forge build <ir>`（安全闸）→ `forge sim <ir> --vhass fake --seed <s> --events <e>`（逻辑闸 + expect 断言）→ `forge run`（内存 Runtime）。

---

## 1. 当前进度（已完成 / 进行中 / 计划中）

### 1.1 已交付（全部 commit 入仓，部分已 push GitHub + 部署 NAS）

| 阶段 | 主题 | 状态 |
|---|---|---|
| G1–G7 + 真机接线 + UI 服务层 + 真机常驻监听 + P1 持久化 | 内核与治理面全栈 | ✅ |
| v0.2.0–v1.0.1 | 11 个小版本：基线对齐→…→MCP 接入 | ✅ |
| v1.1.0–v1.6.0-a | 实体事实内建 / 断言闭环 / 变更可信 / 治理面 / 经验闭环 / 实体解析决策智能 | ✅ |
| v1.7.0 / v1.7.1 | WebUI 全功能接入（UI 已并入主仓 `ui/`）/ NL 实测复验修复 | ✅ |
| **v2.0 / v2.0.1** | 业务设计 M1–M4 + 投产收口（决策 H） | ✅ 已投产 |
| **v2.1** | F1 补域 / F2 对拍 / F3 Shadow / F4 监护 `af_watch` / F5 阶段契约 | ✅ 已交付 09-28 |
| **v2.2** | F6 Premiere 封禁 / F7 通用撤销 / F8 band 归一 | ✅ 已交付 09-28 |
| **v2.3** | F9 IR `group` 容器节点 / F10 复合部署 | ✅ 已交付 09-28 |
| **v2.4** | F11 experience 消费 / F12 predict 联动 / F13 evo 真 IR | ✅ 已交付 09-29（代码+测试+NAS image-bake 部署） |
| **后续优化三项（DCD 20260930）** | 单写者租约 / WAL-B / import-linter | 🟡 两项已落地、一项 CI 待补（详见 §9） |

### 1.2 进行中 / 待决策 / 计划中

| # | 事项 | 状态与建议 |
|---|---|---|
| ① | **`wait` 语义二选一**（平台级决策，旧交接单 §1.2① 遗留） | 仍需拍板：A 改运行时让 wait 到期走 `then`；B 保留现状、修文档。`af_scheduler.tick()` 把所有非 emit 定时器派发为 `on_timeout`。建议走 DCD 申请裁定（见 §8）。 |
| ② | v2.5 F14 NL→IR 可逆编译器 | 📋 仅设计层（演进路线图 §2 v2.5），无代码；过需求门后拆。 |
| ③ | import-linter CI 门禁 | 🟡 契约 `.importlinter` 已写；待 `pip install importlinter && lint-imports` 出违规清单，再接入 CI（观察期 `continue-on-error` → 升失败门禁）。 |
| ④ | F11②经验先验注入 | TODO（非阻塞）：`af_runtime_ext.install` 当前传 `experience=None` 未真正接线，不影响正确性。 |
| ⑤ | 路线图"不排期项" | `fn` 节点 CEL/Lua/Wasm、多成员/多租户、多语言 NL、隐私脱敏、Runtime 高可用、多 agent 任务池、自动化判重——待真实需求驱动。 |

---

## 2. 当前架构

### 2.1 模块地图（`src/autoforge/`）

```
af_ir/           IR 数据模型 + JSON Schema（唯一真相，已冻结 v0.2.1）
af_time.py       TimeSource 抽象（生产/仿真两套；业务代码禁止直接 time.time()）
af_state.py      StateProvider + 求值段快照（Snapshot.get 支持「实体.属性」）
af_bus.py        EventBus：去重/节流/熔断/精确订阅
af_instance.py   InstanceManager：状态机/上下文/快照/vars
af_adapters/     HA(dry-run + HATransport)/HTTP/Mock——纯执行层（不重试/不降级）
af_executor.py   NodeExecutor：求值段、边优先级、挂起、canary、ask/timeout
af_scheduler.py  Scheduler：4 mode + 配额；⚠️ 非 emit 定时器派发为 on_timeout（见 §1.2①）
af_scanner.py    StaticScanner（安全闸全项）+ live_preflight（真机预检）
af_nl.py         NL 确定性渲染 + 覆盖率检查
af_audit.py      结构化审计
af_conf.py       G4 置信度分级自主（衰减 + 样本回灌）
af_canary.py     G4 灰度保护（漂移检测 + 回滚）——⚠️ 本地工作副本是 WIP 重构版，见 §6 部署警告
af_fault.py      G5 故障注入（五类故障 + 四类失败映射）
af_store.py      G6 版本化存储 + 置信度持久化 + diff
af_spec.py       G7 AF-Spec（文本语法 ⇄ JSON IR）
af_catalog.py    v1.1.0 设备目录 + 解析（多级兜底 resolve、决策智能、别名、遥测）
af_affordance.py v1.1.0 域状态契约表
af_registry.py   v1.6.0-a 可选 ws 四注册表抓取
af_pending.py    v1.4.0 待批队列
af_device_acl.py / af_scanner 设备保护分级
af_experience/   v1.5.0 经验闭环（失败归因/错误知识库/共现/遥测）→ F11 消费
af_watch.py      v2.1 F4 运行时监护聚合（回灌 M4 诚实报告）
af_intent.py / af_orchestrator.py  意图解析（LLM + HeuristicIntentParser 兜底；目录相关性预筛默认开）
af_evo.py        v2.4 F13 evo 提案真 IR 内联（require_shadow_band 默认开启）
af_predict.py    v2.4 F12 预测性触发（与 G4 分级联动）
af_runtime_ext.py v2.4 F12 装配扩展（⚠️ 部署见 §6 警告）
af_runtime.py    Runtime 组装（依赖注入）
af_persist.py    P1 实例持久化与崩溃恢复（🟡 2026-09-30 加 SHA256 校验和 + 损坏段跳过，向后兼容）
af_service.py    服务层纯逻辑（只读 API 能力，无 Web 依赖）
af_api.py        HTTP 路由层（FastAPI，`forge serve`）；🟡 2026-09-30 加 `readonly=` 守卫
af_cli.py        forge build/sim/run/conf/diff/store/spec/serve/entities/pending/mcp …
af_vhass/        仿真夹具、FakeHA 降级、故障注入底座
af_flock.py      文件锁（FileLock.try_acquire / holder）——🟡 2026-09-30 被单写者租约复用
```

### 2.2 数据流（两道闸铁律，顺序不可调换）
```
Agent 写 JSON IR / AF-Spec
   │  forge build  （第一道闸：静态扫描 StaticScanner；拦截即出局）
   ▼  ok=true
   │  forge sim   （第二道闸：vhass 仿真 + 时间旅行 + expect 断言；exit=0 只代表没崩，expect 全过才是 fully_verified）
   ▼
   │  forge run   （内存态 Runtime；--live 真机下发到 HA，白名单+二次确认）
   ▼
   NL 渲染（确定性，"看到即跑的"）
```

### 2.3 仿真底座（vhass）
- **FakeHA**：内置降级实现，Windows 默认走它（pytest-homeassistant 因 `fcntl` 在 Windows 崩溃）。
- **真 vhass**（pytest-homeassistant）：Linux/WSL/Docker（`docker/Dockerfile.test`）；8 条验收有真 vhass 复核。
- `af_vhass/__init__.py` 暴露 `is_modeled(domain, service)` / `service_effect(domain, service)`。

### 2.4 实体解析（v1.1.0 + v1.6.0）
`forge entities resolve <自然语言>` / `af_resolve_entity`（MCP）/ `GET /api/entities/resolve`。多级兜底；陷阱条保护：不存在的设备必须 0 候选，绝不臆造 entity_id。目录落 `{store_root}/.catalog/catalog.json`。

### 2.5 断言闭环（v1.2.0 + v1.7.1 属性形态）
IR 顶层 `expect` 三态 `pass`/`fail`/`unverified`；属性形态 `{entity_id, attribute, value, op}` 才是真军令状；属性缺失判 `unverified` 不判 fail。

---

## 3. 项目历史（里程碑时间线，续至 2026-09-30）

| 日期 | 事件 |
|---|---|
| 2026-09-14~18 | G1–G7、v0.2.0–v1.7.1 全交付；UI 合并主仓；NL 实测复验修复 |
| 2026-09-27 | v2.0.1 收口 + image-bake 重建 `autoforge-api`/`autoforge-test` 镜像部署 NAS |
| 2026-09-28 | v2.1 F1–F5、v2.2 F6–F8、v2.3 F9–F10 交付（代码+测试） |
| 2026-09-29 | v2.4 F11–F13 交付；F12/F13 经 image-bake 部署 NAS；GitHub 仓库初始化并 push `main` |
| 2026-09-30 | 三项运行时加固（单写者租约 / WAL-B / import-linter 契约）落地；文档归一（本交接单） |

---

## 4. 生态分工（避免串台）

| 代号 | 项目 | 角色 | 路径 |
|---|---|---|---|
| **AF** | AutoForge | 自动化工厂——HA 自动化的**唯一写入方** | `E:\NAS\AutoForge` |
| **MA** | memory-agent | 创造力源泉，提供带 conf 的假设；设备长期健康度归它 | `E:\NAS\memory-agent`（独立仓库） |
| **DB** | doubao-butler | 连接与执行，`ask` 话术与对话承接 | `E:\NAS\doubao-butler`（独立仓库） |
| **AutoFlow** | `E:\NAS\autoflow` | 另一条对话维护；AF 不读写其代码 | 独立 |

**铁律**：AF 管「执行事实」（ID/域/状态），MA 管「语义身份」。AF 的 `af_catalog` 切断对 MA 取 entity_id 的硬依赖。

---

## 5. 工程环境

- **语言/运行时**：Python 3.11+（以 `pyproject.toml` `requires-python` 为准）；`af_*` 为包内模块。
- **依赖**：`pip install -e .`（或按 `pyproject.toml` extras，如 `[api]` 含 FastAPI/uvicorn、`[algo]` 含 pm4py/river 可选）。
- **测试**：
  - Windows 本机：**FakeHA**（`pytest` 默认 `-p no:homeassistant` 防整轮崩溃）；真 vhass 的 10 项 acceptance 因 Windows 跳过。
  - 真 vhass 复核：Linux/WSL/Docker（`docker/Dockerfile.test`）。
  - 双环境全绿 = DoD。
- **Lint/契约**：`import-linter` 契约 `.importlinter` 已就位（L0→L1→L2 禁反向）；接 CI 前先本地 `pip install import-linter && lint-imports` 看违规清单。

---

## 6. 部署与工程纪律（红线，已修正）

### 6.1 NAS 部署流程（image-bake，2026-09-27 起生效）
> ⚠️ **旧交接单写的"源码 bind mount、改完 restart 即生效"已作废**。当前 `autoforge-api` 是 **image-bake**：改后端代码必须重建镜像。

```bash
# 1) 先备份远端基线（从挂载目录移出，不污染 /app/src）
ssh <nas> 'cp -r /vol1/1000/docker/autoforge/src/autoforge /vol1/1000/docker/autoforge/backups/autoforge.bak.<tag>'

# 2) 同步改动的 src 文件到 NAS
scp -i <key> -o StrictHostKeyChecking=no \
    src/autoforge/<file> lidicn@192.168.2.200:/vol1/1000/docker/autoforge/src/autoforge/<file>

# 3) 重建镜像 + 起容器（后端必须 build）
ssh lidicn@192.168.2.200 'cd /vol1/1000/docker/autoforge && docker compose -f docker/docker-compose.api.yml build autoforge-api && docker compose -f docker/docker-compose.api.yml up -d'

# 4) UI 产物是 bind-mount，无需重建镜像，直接 scp dist
scp -r ui/dist/* lidicn@192.168.2.200:/vol1/1000/docker/autoforge/ui/dist/

# 5) 校验
ssh lidicn@192.168.2.200 'curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8787/api/health'
```
- SSH key：`C:\Users\lidicn\.ssh\id_ed25519`；SSH 客户端：`C:\Users\lidicn\.ssh\openssh\OpenSSH-Win64/ssh.exe`（PowerShell 远程命令**单引号包裹**，否则 `$(...)` 被本地展开）。
- 容器：`autoforge-api`（端口 8787）；store 卷挂 `/vol1/1000/docker/autoforge-store`；UI 卷挂 `/vol1/1000/docker/autoforge/ui/dist`。
- **⚠️ WIP 分叉警告（极重要）**：本地 `e:/NAS/AutoForge/src/autoforge/af_canary.py` 是 **WIP 重构版**（无 `CanarySupervisor`/`CanaryPolicy`），与 NAS v2.1+ 基线**分叉**。从本地部署 F12/F13 等**只 scp 对应文件**（如 `af_evo.py`/`af_predict.py`/`af_runtime_ext.py`/`tests/test_af_pretrigger.py`），**切勿把本地 WIP 的 `af_canary.py`/`af_runtime.py` 整文件覆盖到 NAS**，否则引入分叉致运行异常。
- 回滚：用备份覆盖 `/vol1/1000/docker/autoforge/src/autoforge/` 后 `docker compose ... build autoforge-api && up -d`。

### 6.2 GitHub 推送（2026-09-29 起生效）
```bash
git status                                   # 提交前扫一眼，确认无 .env/token/*.db
git add -A
git commit -m "feat: ..."                    # 中文 + 范围前缀（fix:/feat:/docs:/chore:）
git push                                      # 推 origin/main（本地 master 跟踪 origin/main）
```
- 远程：`git@github.com:lidicn/AutoForge.git`（`main`）。旧交接单"绝不 push"已作废。
- **NAS 不直接读 GitHub**：NAS 跑的是 image-bake 副本，靠 §6.1 的 scp+build 更新；GitHub 是源码真源。

### 6.3 代码纪律（红线）
- **不提交敏感文件**：`.env`/`credentials.json`/`*.db` 已在 `.gitignore`。
- **测试环境红线**：Windows 默认 FakeHA；真 vhass 走 Docker。双环境全绿才合。
- **不碰的红线**（KICKOFF §3）：不碰 Node-RED；不自研仿真器；不上原生 Python 沙箱；业务代码禁 `time.time()`（走 TimeSource）；G1 不写真实 HA（HA 适配器默认 `dry_run=True`）。
- **IR 冻结（v0.2.1）**：节点 7 种、边 6 种已冻结；改 Schema 走 `af_ir/schema/ir.schema.json` 并同步 `af_ir/models.py` 与 Schema 测试（`scripts/gen_schema_check.py`）。
- **两文档唯一真相**：`docs/architecture/IR_AND_RUNTIME.md`（IR 语义）+ `KICKOFF.md`（冻结决策/红线）；代码/文档冲突以这俩为准。
- **单写者租约（2026-09-30）**：`forge serve` 抢不到 `{store_root}/.serve.lock` 即降级只读，写操作由 API 层统一拒绝（503）。

---

## 7. 测试与回归
```bash
python -m pytest tests -q                                   # 全量（Windows + FakeHA）
python -m pytest tests/unit/test_af_api.py -q                # 单写者租约单测
python -m pytest tests/unit/test_af_persist.py -q            # WAL-B 校验和单测
docker run --rm -v /vol1/1000/docker/autoforge:/app autoforge-test   # 真 vhass 复核
```
> 本环境 `pytest` 整文件收集偶发空闲挂起；单文件/小范围运行稳定，CI 容器（autoforge-test）跑全量。

---

## 8. 决策与设计双管流程（DCD + Lever-Hub MiMo，新 Agent 必读）

本项目有两条"超出本仓库可推导"的协作通道，开发者只写申请/投喂，**不代裁定/不代设计**：

### 8.1 DCD（关键决策部）—— 路线图标前提可能错误时移交裁定
- 路径：`E:/NAS/关键决策部/`
- 流程：开发者写**决策申请表** → 投 `inbox/`（文件名 `YYYYMMDD-事项-决策申请.md`，含背景/一句话问题/候选方案表/证据表(须 `file:line` 或标【假设】)/倾向性意见/**不裁定**）→ 在对话里 `@` 该文件。
- DCD 核实源码后出《决策意见书》落 `decisions/`，路线图更新落到 AutoForge 自己的 `docs/`。
- 例：2026-09-30 三项优化即先投 `inbox/20260930-AutoForge后续优化-并发安全与持久化与导入分层-决策申请.md`，由 DCD 裁定后开发者承接（见 §9）。

### 8.2 Lever-Hub MiMo 长稿 —— 从大仓库提炼全新架构方案
- 路径：`E:/NAS/Lever-Hub/`；技能：`use_skill lever-hub`。
- 流程：写**申请单**（`申请/`，一单一事）→ `intake.py` 生成需求稿（内联契约闭包源码，`--attach` 喂 `<100kb`）→ `send.py --auto`（机器代按回车取 hex）→ `harvest.py --hex` 回收设计件。
- 纪律：设计件只落盘 `out-alt/` **待评审、不自动合入**；硬档（内网 IP/密钥形态）触发即拒单。
- 例：2026-09-30 投喂 `af_eventlog` 全量设计（hex `8475001d17f9a55e6053d8031529677d`）——但因 DCD 裁定本轮做 B（增强现有）而非 A（全量），该长稿按纪律留作**未来种子、未合入**。

---

## 9. 后续优化三项（DCD 20260930 裁定，已落地）

裁定：`E:/NAS/关键决策部/decisions/20260930-AutoForge后续优化三项-裁定.md`；落地：`docs/roadmap/后续优化三项落地-20260930.md`。

| 子项 | 落地 | 产物 / 验证 |
|---|---|---|
| 单写者租约（A 降级只读） | ✅ | `af_cli.serve` 注入 `af_flock.try_acquire()`；`af_api.build_app(readonly=)` 写端点 503（含无 `_write` 守卫的 `/api/build`/`/bind`/`/sim`/`/spec/compile`）；单测 `tests/unit/test_af_api.py` 三项（只读 503 / 非只读 ≠503 / `/api/build` 只读 503） |
| WAL-B（增强现有） | ✅ | `af_persist` 落盘加 SHA256 校验和（对齐 `af_store`）+ 读时校验、损坏段跳过；不改格式头、旧记录向后兼容；单测 `tests/unit/test_af_persist.py` 三项（校验和往返 / 篡改拒收 / 旧格式兼容） |
| import-linter（B→A 渐进） | 🟡 | 契约 `.importlinter`（L0 内核 → L1 运行时 → L2 服务，禁反向依赖）；待 `lint-imports` 出清单 + CI 门禁（观察期 `continue-on-error` → 升失败门禁） |

> MiMo 长稿产物（A 全量 `af_eventlog`）与裁定 B 不一致，留作未来种子、未合入。

---

## 10. 给新 Agent 的"下一步"清单（按价值/依赖）

1. **【最高优先·需 DCD 拍板】** §1.2① `wait` 语义二选一（A 改运行时 / B 改文档）——影响所有 Agent 生成的 `wait` 节点，建议走 DCD 申请（§8.1）。
2. **v2.5 F14 NL→IR 可逆编译器**：走演进路线图 §2 v2.5 设计，过需求门后拆 PR。
3. **import-linter CI 门禁**：`pip install importlinter && lint-imports` 出清单 → 接 CI（先 `continue-on-error`）。
4. **F11②经验先验注入**：把 `af_runtime_ext.install` 的 `experience=None` 接成真实先验（非阻塞）。
5. **文档同步**：本文 + `版本路线图.md` + `版本开发计划.md` 已是 2026-09-30 口径；若后续改版本，先更这两份唯一真源，再顺手修 `README.md`/`KICKOFF.md` 的版本段。
6. **勿做**：不要为"统一版本号"改 `pyproject.toml` 的 `version`；不要动 Node-RED/自研仿真器/原生沙箱红线；部署后端**必须 image-bake build**（勿迷信旧 bind-mount 说法）；**切勿**把本地 WIP 的 `af_canary.py`/`af_runtime.py` 整文件覆盖 NAS（§6.1 警告）。

---

## 11. 一页速查表

| 想做… | 入口 |
|---|---|
| 写/编译 IR | `forge build <ir>`（安全闸） |
| 逻辑仿真 + 断言 | `forge sim <ir> --vhass fake --seed <s> --events <e>` |
| 真机下发 | `forge run <ir> --live --confirm --ha-token …`（白名单 + 二次确认） |
| 常驻监听 | `forge watch --persist-dir …`（订阅 HA SSE） |
| 查设备/拿真 ID | `forge entities resolve <自然语言>` / MCP `af_resolve_entity` |
| 服务层 API | `GET /api/health`、`/api/graphs`、`/api/build`、`/api/sim`、`/api/catalog`、`/api/entities/resolve`、`/api/pending`、`/api/metrics`、`/api/experience`、`/api/telemetry`、`/api/faults`（权威：`GET /openapi.json`） |
| MCP 接入 | `forge mcp`（21 工具） |
| 服务启动 | `forge serve --host 0.0.0.0 --port 8787 --store-root /data --examples /app/examples/ir --ui-dir /ui`（NAS 容器命令；抢不到锁降级只读） |
| 待批队列 | `forge pending list\|approve\|reject` / `POST /api/pending/*` |
| 部署 NAS | 见 §6.1（scp src → `docker compose build autoforge-api` → `up -d`；先备份） |
| 推 GitHub | 见 §6.2（`git add -A` → `commit` → `push` 到 `origin/main`） |
| 提决策 | 写 `E:/NAS/关键决策部/inbox/YYYYMMDD-事项-决策申请.md` 并 `@` |
| 要设计稿 | 写 `E:/NAS/Lever-Hub/申请/` 申请单 → `intake.py` → `send.py --auto` → `harvest.py --hex` |

---

*本文件为换 Agent / 换对话交接单（2026-09-30）。权威细节以 `KICKOFF.md` / `docs/roadmap/版本路线图.md` / `docs/plan/版本开发计划.md` / `docs/architecture/IR_AND_RUNTIME.md` 为准；如发现冲突，以被引用的权威文档为准，并顺手修本文。旧版见 `docs/archive/交接总览_换Agent继续本项目.md`（v1.7.1 时代，已作废）。*
