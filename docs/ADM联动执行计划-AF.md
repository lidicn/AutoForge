# ADM 联动执行计划 · AF（AutoForge）

> 出品：关键决策部（DCD）
> 版本：v2.5 联动落地版（基于 v3_DCD 路线图 + 全部裁定链）
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
| ② af_persist 加校验和 + 追加写（裁定 B：不做全量 af_eventlog） | 给 `af_persist` 补 SHA256（对齐 `af_store`） |
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
① `compose ps` 服务在；② `/health` 返回 200；③ `mosquitto_sub` 抓到一条 `af/automation/fired`，且其 `ts` 是**家庭墙钟**口径；④ `adm/autoforge/status` retained 值为 `online`。

---

—— 关键决策部 · DCD