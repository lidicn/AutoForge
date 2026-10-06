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
  → 重建 wheel（✅ 已执行，权威 sha b4b5d6bbe424…；首投 36fdf77a… 作废）
  → 重烤 AF 镜像（待窗）
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
| v2.5 NL 双向保真 | ✅ 本地全绿，**NAS 待烤** |

**联动计划**：第 0-4 步**代码全交付**；第 5 步（后续优化三项）①③④ 交付，**② af_persist 顺序追加写经 DCD 判定"不适用"**（裁定 `20261004-AF四件与DB一件与MA五件-裁定.md` §一.4——本仓按裁定要求记为"经 DCD 判定不适用"，**不是"未做"**，避免下一轮重报）。基线：2500 passed / 0 failed。

> ⚠️ **验收等级更正（AF 2026-10-04 指正，DCD 采纳）**：第 0-4 步仍差**窗内两件**——NAS 镜像重烤、`AUTOFORGE_MQTT` 的开法（本文件 §四 与 18:35 裁定 §二 Q1）。在那两件完成前，第 1/2 步（MQTT 桥、presence）的验收等级是 **EXEMPT，不是 VERIFIED**；且**不得与 §5.2"唯一的硬阻塞"分开过账**（两者一致，但不能分开记功）。

### 5.2 唯一的硬阻塞：NAS 镜像重烤（合并窗）

AF 代码全绿但镜像未烤——**这是 AF 唯一的硬阻塞**，且与 DB/MA/AgentOps 同一个窗（§四 已写死顺序）。

### 5.3 下一版 v2.6 细化任务

| # | 任务 | 验收 | 前置 |
|---|------|------|------|
| 1 | NAS 镜像重烤 + 窗后四项验收 | §四 四条硬读数（**`/api/health`**，不是 `/health`） | 合并窗 |
| 2 | v2.5 部署到 NAS | 容器起 + `/api/health` 200 | 1 |
| 3 | ~~af_persist 顺序追加写~~ → **已收口：经 DCD 判定不适用**（裁定 `20261004-AF四件…` §一.4）。本仓记录须标注"经 DCD 判定不适用"，**不是"未做"** | — | — |
| 4 | F7 撤销前端残留（`LiveView.vue` 撤销按钮通路 + 10s 定时补数） | **已交付**（commit `3039c62`：10s 定时补数、读失败不清空、窗口长度只印服务端给的数）。残留：真机指针事件未验 | — |
| 5 | AgentOps 建仓后模板首次 commit | `gates.yml` 无 `continue-on-error` | 建仓授权（裁定已给） |
| 6 | LWT `kill -9` 离线分支 + 镜像内 vendor 自检第 7 条 | 真 broker 上验 | 1 |
| 7 | 单写者租约补 **MCP 真机写面**（只 check 不 acquire；被持锁时 `ServiceError(503)`；MCP 侧文本带固定前缀 `READONLY_DEGRADED:` 并登记进契约表） | 三条判据：HTTP 503 / MCP 拒收且不构造传输层 / 锁空闲时照常下发 | 无（裁定 `20261004-AF四件…` §一.1 A） |
| 8 | 有界缓存 **注册表式门禁**（容器属性名 + TTL 来源 + 上限常量 + 测试 id；三判据；5 处一次登记 + 2 处误报带理由入基线） | 门可判红：新增增长容器未登记即红 | 无（同上 §一.3 B） |
| 9 | 安全遗留（裁定 18:35 §一 / §五）：长期码加可配绝对上限（默认 180 天，`AUTOFORGE_AUTH_LONGCODE_TTL_DAYS`，0=显式关）+ `af_auth.list()` 输出"距生成多久"；**MCP 未设令牌默认拒绝**（`AUTOFORGE_MCP_ALLOW_NO_TOKEN=1` 才放行）；`GET /api/asks/pending` 加 `Depends(_read)`；`/api/user/auth-codes` 收紧到 `_write` 且 owner 面给明文、非 owner 面只给掩码+状态；`docstring` 里的 `demo/forge2026` 明文默认凭据移除；"只在可信 LAN"写成显式部署前提进 README + compose 注释 | 未授权默认结论必须能被测试判红；read 令牌取 auth-codes 列表 403；明文只在 write 面；掩码不可还原 | 无 |
| 10 | 窗内开关（裁定 18:35 §二）：compose 补齐 `MQTT_HOST`/`MQTT_USER_*`/`MQTT_PASSWORD` 引用**但留空**，`AUTOFORGE_MQTT` 维持缺省关；`paho-mqtt` 钉上界 `>=1.6,<2.1` | **仓侧半边已交付**（`729343b`，§二之五十一）：compose 补齐 `MQTT_HOST`/`MQTT_PORT`/`MQTT_KEEPALIVE`/`MQTT_USER`/`MQTT_PASSWORD` 五条引用且值一律留空、`AUTOFORGE_MQTT` 缺省 `0`；键名不手抄——唯一真源是 `Dockerfile.api:26` 钉死的那枚 vendored wheel 的 `homesdk/mqtt.py`（`MQTT_USER_*` 那种带作用域的形式与 `MQTT_USERNAME`/`MQTT_PASS`/`MQTT_PASSWD` 别名在注释里点名但本仓不用），"留空是真 no-op"由真模块当场判（空 host 抛 `MissingEnv`、空 port 落回 `DEFAULT_PORT`、空账密抛 `MqttCredentialsMissing`）。`paho-mqtt>=1.6,<2.1` 早已钉（`pyproject.toml:42` 与 `:54`，裁定 Q2=B）。**窗内半边仍待第 1 件**：重烤后服务照常起、桥 no-op；开关真打开走**非停机窗**配置推送（先 `compose exec` 预检 `paho_available()` + `broker_settings()` 通过） | 1 |

| 11 | **落地 DCD 20261006 §二**：段间累计封顶两档（S=1000 段 / T=20000 步越档告警，2× 才 `_fail`，落 `AuditLog` + 监护视图常驻指示）+ trace 截断（N=1000 + `trace_dropped`，不进 IR schema） | **已交付**（commit `e876678`，13 条腿）。残留：真机 HA 上未量（要等第 1 件那个窗） | — |
| 12 | **落地 DCD 20261006 §一**：配对 bootstrap 走**两个匿名端点**（裁定 B）+ 限速 `request` 6/min、`redeem` 10/min、超限锁该 IP 于该端点 5 分钟 + 维持 8 位/300s/单次 + owner 侧"暂停接受配对请求"开关 | 匿名射程只开这两条（整张工具表仍 default-deny）；限速与锁定可被测试判红；owner 开关关掉后配对请求进不来 | 无（裁定已给） |

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

—— 关键决策部 · DCD