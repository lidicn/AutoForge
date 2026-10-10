# AutoForge

> **AutoForge = Agent 为中心的智能家居自动化平台**：让 Agent 撰写自动化，机器验证正确性，人只看自然语言。
> 命名约定见 [`docs/reference/NAMING.md`](docs/reference/NAMING.md)：产品名 **AutoForge**｜CLI **`forge`**｜Python 包 **`autoforge`**｜模块前缀 **`af_*`**。

---

## 1. 它解决什么问题

传统智能家居自动化要人写 YAML、拉连线、猜实体 ID；Agent 时代则反过来——**让 Agent 写，让机器验，让人只读一句话**。

```
Agent 撰写 AF-Spec / JSON IR ──▶ forge build（安全闸：静态扫描）
                            │ 拦截即出局，不进仿真
                            ▼
                       forge sim（逻辑闸：vhass 仿真 + 时间旅行）
                            ▼
                       forge run （内存态 Runtime）
                            │  --live：真机下发到 HA（白名单 + 二次确认）
                            ▼
                   自然语言渲染（确定性，"看到即跑的"）
```

**铁律**：Graph（JSON IR）是唯一真相，自然语言由它**确定性渲染**——你批准的那句话与真正跑的逻辑不可能漂移。

---

## 1.1 里程碑状态

> **版本双轨**（DCD 20261001《AF 三题》·G 裁定，方案 A）：**包/API 版本 `0.1.0`**（`pyproject.toml` / `af_service.API_VERSION`，`/api/health.version` 返回它，属机器契约，不随里程碑动）↔ **对外里程碑 `v2.x`**（人读叙事）。三列映射表见 [`docs/plan/版本开发计划.md`](docs/plan/版本开发计划.md) §〇——两套口径并行且都正确，不是"版本号漂移"。
> **当前里程碑**：`v2.5`（F14 NL→IR / F15 词表真值源）——**本地已交付，NAS 部署走变更窗口**；v0.2.0–v2.4 已全部交付。剩余增量收口与后续见 [`docs/roadmap/演进路线图_AF_v2.1+.md`](docs/roadmap/演进路线图_AF_v2.1+.md)。
> 各版本主题与交接卡见 [`docs/roadmap/版本路线图.md`](docs/roadmap/版本路线图.md) 与 [`docs/roadmap/`](docs/roadmap/)。
> **回归读数**：README 不钉死测试计数——钉一份计数就多一份会过期的副本（此处曾长期挂 1300 passed / 51 skipped，而整树现读已 3000+ 条）。整树口径 `PYTHONPATH=src python -m pytest tests -q`，最新读数记在 [`docs/ADM联动执行记录-AF.md`](docs/ADM联动执行记录-AF.md)；门禁读数由 `gates.sh` 给出。

| 阶段 | 主题 | 状态 |
|---|---|---|
| G1 | 最小可运行闭环 | ✅ |
| G2 | 安全模型与静态扫描完整化（§8.2 全项） | ✅ |
| G3 | IR 完备性：7 节点 / 6 边 / 两种 Timer | ✅ |
| G4 | MA 置信度分级自主 + canary 灰度 | ✅ |
| G5 | 故障注入（五类故障 + 四类失败落执行器） | ✅ |
| 真机接线 | HA `dry_run=False`（`forge run --live`），解锁 G4 canary | ✅ |
| G6 | 版本审计与 diff（`forge diff` / `forge store`） | ✅ |
| G7 | AF-Spec（文本语法 ⇄ 同一份 JSON IR） | ✅ |
| UI 服务层 | 只读 HTTP API（FastAPI，供前端控制台） | ✅ |
| UI 服务层 R2 | ask 审批会话 + 真机下发三重闸 + 可选鉴权 | ✅ |
| 真机常驻监听 | 订阅 HA SSE 事件流，实时驱动自动化（`forge watch`） | ✅ |
| P1 实例持久化 | `persist=true`：实例跨进程 / 崩溃恢复（`--persist-dir`） | ✅ |
| v0.2.0–v1.0.1 | 十一个小版本：基线对齐 → `emit` → `on event` → 回灌 MA → 标签 → 导出 → 鉴权 → 跨进程 → 表达力 → MCP 接入 | ✅ |
| **v1.1.0** | **实体事实内建**（设备目录 + 解析，切断对 MA 的硬依赖） | ✅ |
| **v1.2.0** | **断言闭环**（IR `expect` + sim 断言 + `Diagnostic.hint`） | ✅ |
| **v1.3.0** | **变更可信**（diff 签名兜底 + 爆炸半径 + 归档归属） | ✅ |
| **v1.4.0** | **治理面**（待批队列 + 设备保护分级 + 凭据热重载 + 令牌过期 fail-closed） | ✅ |
| **v1.5.0** | **经验闭环**（失败归因 + 错误知识库 + 实体共现 + 解析遥测/消歧） | ✅ |
| **v1.6.0-a** | **实体解析 P0「数据源升级」**（可选 ws 四注册表 + `device_id`/`integration`/area 解析链） | ✅ |
| **v1.6.0** | **实体解析决策智能**（device 归并 + 集成优选 + 弱信号降权 + 别名沉淀 + 联动验证闸 + 可选 binding） | ✅ |
| **v1.7.0** | **WebUI 全功能接入**（前端由独立仓库并入主仓 `ui/`，A/B 批次已交付；C 批次经验闭环待做） | ✅ |
| **v1.7.1** | **WebUI 全功能接入收口 + 回归基线对齐** | ✅ |
| **v2.0.1** | **投产收口**（工程卫生与可靠性） | ✅ （核心修复：鉴权 fail-closed / 审计12项 / 安全加固） |
| **v2.1** | **仿真保真与真实闭环强化**（Shadow EXEMPT / Watch / Stage schema） | 🟡 大部分交付，剩余增量收口中 |
| **v2.2** | **意图生命周期与安全红线**（撤销 F7 ✅ / canary band 归一 F8 部分） | ✅ |
| **v2.3** | **复合编排**（F9 group 容器 + F10 原子部署 + 跨自动化冲突预检） | ✅ |
| **v2.4** | **经验闭环与预测**（F11 经验→catalog→predict / F12 预触发 G4 联动 / F13 evo 真 IR 内联） | ✅ |
| **v2.5** | **NL→IR 与词表真值源**（F14 往返保真 + NL Builder + AskSpec 跨层 / F15 `af_actions.KNOWN_ACTIONS` 统一） | ✅ 本地交付，NAS 部署走变更窗口 |

### v1.0 发布说明（2026-09-15）

- **内置表达式函数库**（operand 级 `{"fn": ..., "args": [...]}`，纯函数白名单四族）：
  math（`abs/floor/ceil/round/clamp/min/max/sum/avg`）、string（`lower/upper/trim/length/contains/starts_with/ends_with/replace`）、
  time（`time_hour/time_minute/time_weekday/time_between`——作用于**显式 ISO 时间戳**，表达式自身不读时钟，跨午夜窗口原生支持）、
  list（`length/contains/first/last`，与 string/math 共享多态实现）。
- **资源上限（防 DoS）**：求值深度 `MAX_EXPR_DEPTH=32`、单次节点数 `MAX_EXPR_NODES=256` 双硬顶；
  编译期 `check_expr`（安全闸 `EXPR_INVALID`）与运行期 `evaluate` 同一套上限，未知函数/参数个数错误编译期即拦。
- **`fn` 节点（自定义代码）保持保留位**：结论与论证见 [`docs/architecture/fn_节点设计评估.md`](docs/architecture/fn_节点设计评估.md)——
  CEL/Lua 均不引入，声明式白名单是 1.x 的边界。
- 其余 0.6–0.9 版本（标签/导出备份/鉴权/跨进程）见路线图与各交接卡。

详见 [`docs/archive/ROADMAP.md`](docs/archive/ROADMAP.md) 与 `docs/handoff/`（各里程碑交接卡）。

**CLI 总览**：`forge build（--acl/--guard/--bind）｜sim｜run（--live / --persist-dir）｜watch（--persist-dir）｜conf｜diff｜store｜spec｜serve｜auth｜mcp｜pending｜credentials｜experience｜telemetry｜entities`。

### Agent 接入（MCP，v1.0.1）

`forge mcp` 以 stdio 模式启动一个零依赖 MCP server，把现有功能以 16 个工具暴露给 Claude Desktop / CodeBuddy 等 agent
（进程内直调服务层，与 REST 同源逻辑；写/live 工具复用 v0.8.0 的 scope 凭证门）。

agent 侧配置示例：

```json
{
  "mcpServers": {
    "autoforge": { "command": "forge", "args": ["mcp"] }
  }
}
```

工具清单（**21 个**）：`af_health` / `af_build` / `af_compile_spec` / `af_simulate` / `af_list_graphs` / `af_get_graph` /
`af_graphs_by_tag` / `af_conf` / `af_set_tags`(写) / `af_enable_by_tag`(写) / `af_export_store` /
`af_import_store`(写) / `af_save`(写，先过静态扫描再归档) / `af_diff` / `af_live_run`(live) / `af_whoami`
+ **v1.1.0 新增 5 个实体工具** `af_refresh_catalog` / `af_resolve_entity` / `af_list_entities` / `af_get_entity_state` / `af_catalog`。

用于让 agent 端到端实测现有功能（自然语言→**查设备拿真 ID**→spec→build→sim→store→标签→导出导入→live 下发），
并验证安全边界（只读令牌调写工具被拒）。

### 实体事实内建（v1.1.0）

`af_resolve_entity` 让 agent **不必再绕道 memory-agent 取 entity_id**：

```
用户说人话 → Agent 调 af_resolve_entity("书房吊灯") → 拿到真实 entity_id + 可能状态 + 可调服务
          → 写 IR → af_build → af_simulate → af_save      （全在同一个 MCP 里闭环）
```

- 目录来自 HA 全量状态快照，落本地缓存（`{store}/.catalog/catalog.json`），解析毫秒级返回；
- **不过滤域**：「书房吊灯」可能对应 `light.x` 也可能对应 `switch.y`，全返回让 agent 自己判断；
- **绝不静默猜域**：`resolve_best()` 只在无歧义时自动采纳，有歧义返回 `None` 逼 agent 显式选；
- 列表强制分页（上限 200）+ 透明回报 `truncated` / `next_offset`。
- 与 MA 分工不重叠：**AF 管执行事实**（ID/域/状态），**MA 管语义身份**（谁在家/习惯/历史）。MA 挂了照样能写自动化。

**可选的注册表增强**（v1.6.0-a）：`pip install -e ".[ha]"` 后，`af_refresh_catalog` 会额外抓 HA
websocket 四注册表（`entity`/`device`/`area`/`config_entry`），为候选补上
`device_id` / `platform` / `integration` / 精确 `area`。
**未安装时优雅降级**（这些字段为空、解析照常工作）——降级语义由 12 条契约测试钉住，核心能力不受损。

**决策智能层**（v1.6.0）在检索层之上补「选哪个 / 别用哪个 / 记住哪个」：
- **集成优选** `connectivity_tier`（local > cloud > polling > unknown）：排序插在**置信度之后**，未知档位**中性**、只影响排序不过滤（fail-closed 纪律不变）；
- **device 归并**：按 `device_id` 把同物理设备的多 `entity_id` 合成一条**设备卡**（`entity_id` 仍是唯一键，归并**只影响展示**）；
- **弱信号降权**：`offline_now` 候选降权并显式回传「设备 Y 不可用，建议优先 X」；验证侧 `build`/`save_graph` 报 `ENTITY_OFFLINE_NOW` **防假绿**（只告警不拦截）；
- **别名沉淀**：`{root}/.catalog/aliases.json`（`af_remember_entity` / `forge entities remember` / `POST /api/catalog/alias`），同名查询下次**直中**；
- **可选 binding**：`forge build --bind` / `POST /api/bind` 把 `?书房吊灯`、`?light:书房吊灯` 占位符回填为真实 entity_id（歧义 / 无候选 **不回填**，fail-closed）。

> 两个易踩的坑（已实现）：① `platform ≠ integration`，真集成名走 `config_entry_id → domain`；
> ② area 解析链必须是 `entity.area_id → device.area_id`，区域常挂在 device 上。

### 断言闭环（v1.2.0）

IR 顶层可声明 `expect`（自动化自己立的军令状），`forge sim` 逐条断言：

```json
"expect": [
  { "entity_id": "light.study_main", "state": "on" },
  { "var": "turn_on_result.success", "op": "eq", "value": true }
]
```

返回三态：`pass` / `fail` / **`unverified`**——「没验到」与「验过了」严格分开；
`fully_verified` 比 `ok` 更严（前者要求全部验过，后者只代表没抓到反例）。
诊断带 `hint`（怎么改），agent 一次往返即可自修正。

---

## 2. 快速开始

```powershell
# 1) 建虚拟环境（⚠️ 需要 Python 3.14+：pytest-homeassistant 要求 python_requires>=3.14）
& "<python3.14>" -m venv .venv314
.\.venv314\Scripts\python.exe -m pip install -e ".[dev]"

# 2) 冒烟：编译 + 静态扫描 + NL 渲染
forge build examples/ir/case01_day_light.json

# 3) 仿真（时间旅行、太阳历）
forge sim examples/ir/case01_day_light.json

# 4) 跑全量测试（含 8 条 G1 验收）
.\.venv314\Scripts\python.exe -m pytest tests/ -q
```

> **只做内核开发、不碰仿真**时 Python 3.11+ 即可（`pip install jsonschema typer pytest pytest-asyncio`），
> 无需装 HA 那一大坨依赖。

### vhass（真 HA 仿真）在 Windows 上的限制

`pytest-homeassistant-custom-component` 的插件会经 `homeassistant.runner` 引入 `fcntl`（POSIX 专有），
**Windows 上 pytest 无法加载它**，因此：

| 环境 | 行为 |
|---|---|
| Windows | 默认用内置 **FakeHA**（三接口一致，Runtime 零改动；太阳历用本地桩）。`pyproject.toml` 里已默认 `-p no:homeassistant` 防止整轮 pytest 崩溃 |
| Linux / WSL / Docker | `tests/acceptance/test_vhass_native.py` 自动启用真 vhass：`pytest tests -q -p pytest_homeassistant_custom_component.plugins`；或用 `docker/Dockerfile.test` |

详见 `docs/reference/G1_ACCEPTANCE.md` §4。

---

## 3. 与 Home Assistant 的关系

- **直连 HA**，不经过 Node-RED。
- 并发 mode（`single`/`restart`/`queued`/`parallel`）、`for:` 边沿+持续语义、太阳历/日历均与 HA 对齐。
- 有 8 处**有意偏离**（最重要的是 `restart` 会触发旧实例 `on_cancel`），全部记录在
  [`docs/architecture/HA_SEMANTIC_DIFF.md`](docs/architecture/HA_SEMANTIC_DIFF.md)，且必须在 NL 文本中可见。

---

## 4. G1 范围与红线

**纳入**：IR 数据模型 + JSON Schema｜TimeSource｜EventBus｜InstanceManager｜AdapterLayer｜NodeExecutor｜Scheduler｜StaticScanner（全项）｜NL 渲染器与覆盖率检查｜`build`/`run`/`sim`/`conf`/`diff`/`store`/`spec` 子命令｜8 条验收用例。

**已交付的 P1**：✅ 实例持久化与崩溃恢复 `persist`（`forge run/watch --persist-dir`）。

**后续迭代（原 P1/P2 剩余项已全部拆版排期）**：跨自动化事件 `emit`/`on event` → **v0.3.0 / v0.4.0**｜指标回灌 MA → **v0.5.0**｜标签体系与批量启停 → **v0.6.0**｜模板导出备份 → **v0.7.0**｜服务层鉴权升级 → **v0.8.0**｜跨进程 / 多写者 → **v0.9.0**｜`fn` 节点评估 → **v1.0.0**。详见 [`docs/archive/ROADMAP.md`](docs/archive/ROADMAP.md) §「版本路线图（v0.2.0–v1.6.0）」。

**暂不排期**（理由见 ROADMAP §「不排期项」）：`fn` 实装（CEL/Lua/Wasm）、多成员 / 多租户、多语言 NL、隐私脱敏与保留期。

**并发/存储现状**：原型期为**单进程内存态** Runtime；G6 的 `GraphStore` 为文件级存储（无跨进程锁）→ 目标 **v0.9.0**。

**红线**：
- 不碰 Node-RED
- 不自研仿真器（vhass 必须基于 `pytest-homeassistant`）
- 不上原生 Python 沙箱
- 业务代码禁止直接 `time.time()`（走 `TimeSource`）
- G1 不写真实 HA（HA 适配器默认 `dry_run=True`）

---

## 4.1 部署前提：AF 的 HTTP 面只在可信 LAN

这是 DCD 裁定 20261004 §一 Q2 要求"写成显式前提"的那一条，不是注释里的口头约定：

- **`--host 0.0.0.0` 保留**：DB（homesdk）跨机器调 AF，绑 loopback 会直接打断联动环。
- **因此这一面默认不出可信 LAN**：`/api/*` 的公开读端点（`/api/health`、`/api/metrics` 一类
  `scope=None` 的门）按裁定维持公开，判据是铁律 #6——只读面不能反过来依赖令牌系统。
  它们的"公开"边界靠的是这条网络前提，而不是鉴权。把它挪出可信 LAN 之前，先回来读这一段。
- 写面 / live 面仍然 fail-closed：无令牌即 403，本地放行只有 `AF_ALLOW_NOAUTH=1` 一个逃生舱。
- **MCP 面同样默认拒绝（裁定 20261004 §一 Q2=B）**：`forge mcp` 未配 `AUTOFORGE_TOKENS` 时，需鉴权工具
  （write / live 域，注册表里 11 条）一律拒；公开工具（20 条，含 `af_health`/`af_draft`/`af_whoami`）照常可用。
  本地或原型确需全放行，只有 `AUTOFORGE_MCP_ALLOW_NO_TOKEN=1` 一个显式开关——**只认 `1`**，写 `true` 不算。
  HTTP 侧 `POST /mcp` 复用同一道 `_guard`：`AF_ALLOW_NOAUTH=1` 只放行 HTTP 层，不越权放行工具层。
- compose 侧的同一条注释由部署方在 NAS 上落（本仓不改 compose，见交接记录铁律 #3）；
  本文件这句是仓库内唯一真源，容器那侧的注释与它冲突时以裁定为准并回来更正这里。

---

## 5. 文档索引

| 文档 | 内容 |
|---|---|
| [`KICKOFF.md`](KICKOFF.md) | 冷启动唯一入口：定位、13 条冻结决策、红线、G1 目标、开发顺序、8 条验收 |
| [`docs/architecture/IR_AND_RUNTIME.md`](docs/architecture/IR_AND_RUNTIME.md) | IR v0.2.1：7 节点 / 6 边、实例生命周期、快照、安全模型、vhass |
| [`docs/reference/NAMING.md`](docs/reference/NAMING.md) | 命名约定与历史改名对照 |
| [`docs/architecture/HA_SEMANTIC_DIFF.md`](docs/architecture/HA_SEMANTIC_DIFF.md) | 与 HA 的有意偏离清单 |
| [`docs/reference/G1_ACCEPTANCE.md`](docs/reference/G1_ACCEPTANCE.md) | 8 条验收用例 ↔ 实现点 ↔ 测试落位（含 G4 置信度/canary 映射） |
| [`docs/archive/ROADMAP.md`](docs/archive/ROADMAP.md) | ① 已发布里程碑 G1–G7 + 真机接线归档；② **版本路线图 v0.2.0–v1.7.1** 与开发计划 |
| [`docs/roadmap/ADM-路线图-AF.md`](docs/roadmap/ADM-路线图-AF.md) | 架构决策记录（ADM）路线图：已决策项与剩余项 |
| [`docs/交接卡_模板.md`](docs/交接卡_模板.md) | 里程碑交接卡模板（文件清单/行为增量/验证/风险/合并影响） |
| [`docs/reference/API_CONTRACT.md`](docs/reference/API_CONTRACT.md) | 服务层只读 API 契约（Round 1，权威形态 `/openapi.json`） |

---

## 6. 目录结构

```
src/autoforge/
├── af_ir/          IR 数据模型 + JSON Schema（唯一真相）
├── af_time.py      TimeSource 抽象（生产/仿真两套实现）
├── af_state.py     StateProvider + 求值段快照
├── af_bus.py       EventBus：去重/节流/熔断/精确订阅
├── af_instance.py  InstanceManager：状态机/上下文/快照/vars
├── af_adapters/    HA(dry-run + HATransport/HAStateProvider)/HTTP/Mock —— 纯执行层
├── af_executor.py  NodeExecutor：求值段、边优先级、挂起、canary
├── af_scheduler.py Scheduler：4 mode + 配额
├── af_scanner.py   StaticScanner（安全闸全项）+ live_preflight（真机预检）
├── af_nl.py        NL 确定性渲染 + 覆盖率检查
├── af_audit.py     结构化审计
├── af_conf.py      G4 置信度分级自主（衰减 + 样本回灌）
├── af_canary.py    G4 灰度保护（漂移检测 + 回滚）
├── af_fault.py     G5 故障注入（五类故障 + 四类失败映射）
├── af_store.py     G6 版本化存储 + 置信度持久化 + diff
├── af_spec.py      G7 AF-Spec（文本语法 ⇄ JSON IR）
├── af_service.py   服务层纯逻辑（只读 API 能力，无 Web 依赖）
├── af_api.py       只读 HTTP 路由层（FastAPI，forge serve）
├── af_runtime.py   Runtime 组装（依赖注入）
├── af_persist.py   P1 实例持久化与崩溃恢复（persist=true）
├── af_cli.py       forge build / sim / run(--live) / conf / diff / store / spec
└── af_vhass/       仿真夹具、FakeHA 降级、故障注入底座
```

遵循 `E:\NAS\开发规范.md`：本地写码、git 管、手动 SSH 推送才部署；G1 无部署需求。
