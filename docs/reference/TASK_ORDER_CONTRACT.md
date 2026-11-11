# AF TaskOrder 契约（v1.29.0 第二批产出）

> 卡号：AF-V129-G2-20261111｜作者：AF-Core｜状态：**随批落地，终稿待 AF-PM 复核后转 TVPilot 席**
> 依据：DCD `20261111-AF-对话操控执行授权模型-裁定.md` Q5 甲／Q6／§2.3 甲；设计稿 v5 §二.场景2＋§四.2-6b
> 本文是**跨仓接口契约**：TVPilot 的 `PlanStep` 退役/降级后按本契约对接（⛔ 双轨并存）。

## 一、对象定义

`TaskOrder`＝**一次性**的定时任务单：到点按序执行一组设备动作，完成即销。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `order_id` | string | 出 | `o-<12 hex>`，AF 生成 |
| `agent` | string | 入 | 执行主体（按该 agent 的 `grant` 逐动作放行） |
| `created_at` | ISO8601 | 出 | 入册时刻 |
| `created_by` | string | 出 | 谁挂的单（令牌主体；owner 代挂时与 `agent` 可能不同） |
| `due_at` | number | 入 | 目标时间点（epoch 秒）。`at` 与 `delay_s` **二选一**，入册即折算成绝对时间 |
| `due_kind` | `"at"｜"delay"` | 出 | 入册用的是哪种写法（只为可读，⛔ 不参与判定） |
| `steps` | array | 入 | **有序**动作序列，见 §二 |
| `output` | object | 入 | 输出通道，见 §三 |
| `grant_id` | string | 入 | 执行授权引用。**每步执行前都要过它**（⛔ 整单放行） |
| `status` | string | 出 | `pending｜running｜done｜failed｜cancelled｜expired` |
| `receipt` | object | 出 | `{steps:[…], output:[…]}` 逐动作/逐通道回执 |
| `cancel` | object | 出 | `{at, by, reason}`（仅 cancelled） |

**⛔ 不存在的字段**（红线①：任务不是自动化）：`every`、`repeat`、`interval`、`cron`、`schedule`、
`recurring`、`trigger`、`on`、`events`。出现即被 `af_task_order.FORBIDDEN_KEYS` **代码级拒收**
（`TASK_ORDER_NO_TRIGGER_FIELD`）——要重复就写成自动化（Q6 判例：一次性 vs 重复）。

## 二、`steps[]` 元素

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `adapter` | string | 否 | 缺省 `ha` |
| `action` | string | 是 | `<domain>.<service>`，如 `light.turn_on` |
| `params` | object | 否 | 直接透传给适配器；`params.entity_id` 是执行授权校验的目标实体 |
| `requires_confirm` | bool | 否 | L2 动作在静态扫描里需要它（同 IR 语义） |
| `canary` | object | 否 | 同上 |

执行语义：**逐步、串行、失败即停**（一步失败 ⇒ 整单 `failed`，后续步不发起）。
每一步都是一次完整的真机单发（四重闸＋grant＋回读＋审计），回执逐条记在 `receipt.steps[]`。

## 三、`output` 三通道

```json
{"channels": ["tts", "bark", "mimo"], "room": "zhu_wo_shi", "text": "事做完了", "kind": "control"}
```

| 字段 | 说明 |
|---|---|
| `channels` | 缺省 `["tts","bark"]`；只认 `tts`／`bark`／`mimo`，未知通道**拒收** |
| `room` | TTS 的房间（DB 侧按它绑定音箱） |
| `text` | 播报文本；缺省由 AF 生成一句（"你订的 N 步任务已经按顺序做完了"） |
| `kind` | `control`（默认）／`query`。**查询型**必须至少一条**已接线**通道，否则拒建（红线③哑任务） |

**现读接线状态**（`OUTPUT_WIRED`，⛔ 没接线就如实说）：

| 通道 | 状态 | 机制 |
|---|---|---|
| `tts` | **wired** | `AfMqttBridge.publish_speak`（与自动化播报同一条桥方法） |
| `mimo` | **read** | 读面：`GET /api/orders/{id}`／`/api/orders/receipts` 可查即达成（⛔ 假装推送） |
| `bark` | **unwired** | 仓内无 bark 设施（全仓 grep 0 命中）。它是设计稿里的**兜底**通道 ⇒ TTS 失败时回执带 `fallback_unavailable` |

> ⚠️ 拍板项：`bark` 要从"占位"变"接线"，需要外部推送设施（bark 服务端地址/token）——归出资人/PM 定，
> ⛔ 本批自行发明外部集成。

## 四、状态机

```
pending ──claim（到点）──▶ running ──全部步成功──▶ done        （移出活动池）
   │                          │ └──任一步失败──▶ failed       （移出活动池）
   │                          └──撤销/取消──▶ cancelled        （其后未执行步不发起）
   └──人工取消──▶ cancelled
   └──错过太久（>DUE_GRACE_S=1h）──▶ expired
```

- **终态即销**：`done/failed/cancelled/expired` 移出活动池（`.orders/active.json`），
  留痕进 `deploy_audit.jsonl` 的 `TASK_ORDER_CREATED｜FINISHED｜CANCELLED｜EXPIRED`（⛔ 第二套审计面）。
- **撤销（DCD §2.3 甲）**：每一步之前重读 grant；撤销后**当前步允许自然完成、其后所有未执行步取消**，
  状态翻 `cancelled`，`result.cancelled_from_step` 记下断点。
- **崩溃续跑**：`running` 且 `started_at` 超过 `RUN_STALE_S=300s` ⇒ 引擎从 `receipt.steps` 的断点续跑，
  ⛔ 不重跑已回执的步。

## 五、错误语义（机器可读，码名在 message 开头）

| 码 | HTTP | 含义 |
|---|---|---|
| `TASK_ORDER_NOT_FOUND` | 404 | 单号不存在（已销的单子查 `order_receipts`） |
| `TASK_ORDER_STATE` | 409 | 状态不允许该操作（如对终态再取消） |
| `TASK_ORDER_NO_STEPS`／`_TOO_MANY_STEPS`／`_BAD_STEP`／`_BAD_DUE` | 400 | 入参形状 |
| `TASK_ORDER_NO_TRIGGER_FIELD` | 400 | 出现循环/触发字段（红线①） |
| `TASK_ORDER_NEEDS_OUTPUT` | 400 | 查询型任务无已接线通道（红线③） |
| `TASK_ORDER_OUTPUT_UNWIRED` | — | 保留码（通道未接线在回执里以 `unwired` 表达） |
| `TASK_ORDER_LIMIT` | 429 | 单 agent 活动单上限 20 |
| `TASK_ORDER_STORE_CORRUPT`／`_STORE_VERSION_MISMATCH` | 500 | 账本坏形/版本不一致（fail-closed） |
| `GRANT_*`（来自批 1） | 403 | 入册期逐动作过 grant 的拒绝；执行期同码进 `receipt.steps[].error` |

**越界步的处理（重要，与批 1 的差异）**：任务到点无人可确认，所以越界/高危步**在入册时就拒**
（`GRANT_OUT_OF_SCOPE`／`GRANT_DOMAIN_BANNED`／`GRANT_RISK_EXCEEDS` 等随 message 返回）。
批 1 的「单次放行票」TTL 300s，挂单几小时后必然过期 ⇒ **不用于任务单**（见第二批报告 §三）。

## 六、接口面

| 面 | 形态 |
|---|---|
| MCP | `af_order`（`action`：`create｜list｜cancel｜receipts`），scope `write` |
| REST | `POST /api/orders`／`GET /api/orders`／`POST /api/orders/{order_id}/cancel`／`GET /api/orders/receipts` |
| 引擎 | `serve` 进程内的任务单引擎（5s 轮询）。⛔ 不进 watch 装载池、⛔ 在 MCP/CLI 进程里起（那里没有单写者租约） |

## 七、TVPilot 对接注意（给对接席）

1. **一个语义一个实现**：AF 侧已落 TaskOrder；TVPilot `PlanStep` 按 DCD Q5 甲退役或降级为**执行内部分解**，
   ⛔ 两套并存。
2. 对接只需要三个动作：`create`（挂单）、`list`（看活动池）、`cancel`（取消）；
   历史查询走 `receipts`。
3. 时间只有一个形状：**一次性时间点**（`at` 或 `delay_s`）。周期性需求请走自动化面（`af_save`＋授权码）。
4. 每一步仍然是**真机单发**：工作量的账在 AF 侧（四重闸＋grant＋回读），TVPilot 只需给结构化 steps。
