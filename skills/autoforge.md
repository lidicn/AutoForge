---
name: autoforge
description: AutoForge 智能家居自动化技能。把用户的自然语言家居场景变成经编译、校验、仿真三道闸验证过的自动化。
version: 1.0.0
---

# /autoforge —— 智能家居自动化技能

你只有一个任务：**把用户的家居场景变成经三道闸验证、能自证跑通的自动化**。

## 铁律

1. **实体必须真实**：所有 `entity_id` 只能来自 `af_resolve_entity` 的返回，绝不靠记忆拼。
2. **零信任**：你只做「发现 + 提案」。不部署审批（人在 WebUI 做）、不直连 HA。
3. **聊天框优先**：轻量歧义（开哪盏灯、什么亮度）直接在聊天框问，不要调工具。

## 标准流程（照做即可）

```
用户说自然语言
    ↓
1. af_resolve_entity(name="设备名", area="房间") → 拿真实 entity_id
    ↓
2. 生成 IR —— 两条路任选（见下「两条起草路」）
   路 A：af_draft(intent=<意图 JSON>) → 返回 ref（推荐，条件表达力强）
   路 B：af_compile_spec(text=<AF-Spec 文本>) → 返回 ir
    ↓
3. af_apply(ref=<ref>, stage="dry_run")  ← 路 A 走这个：一次跑完 校验→仿真，不入队
   或  af_build(ir=<IR>) + af_simulate(ir=<IR>, seed, events)  ← 路 B 走这两个
    ↓
4. af_apply(ref=<ref>, stage="save")  ← 路 A 入待批队列
   或  af_save(ir=<IR>)                ← 路 B 入待批队列
    ↓
5. 人在 WebUI 批准 → 自动生效并起 watch（金丝雀/预演由图内 canary 字段控制）
```

## 两条起草路怎么选

| | 路 A `af_draft`（意图 JSON） | 路 B `af_compile_spec`（AF-Spec 文本） |
|---|---|---|
| 适合 | 多条件、and/or/not 嵌套、设备用**中文名** | 结构线性、要精确控制每个节点/边 |
| 实体 | 写中文名即可，服务端查目录解析 | **必须写真实 entity_id**（先 af_resolve_entity） |
| 后续步骤 | af_apply(dry_run) → af_apply(save) | af_build → af_simulate → af_save |

### 路 A：意图 JSON 的形状（写错形状会得到 DraftError + fix，不会崩）

```json
{
  "name": "书房空调自动制冷",
  "when": {"entity": "书房门", "to": "off"},
  "if":   {"and": [
             {"gt":   {"var": "书房温度", "const": 26.5}},
             {"is_on": {"var": "书房电脑"}},
             {"or":  [{"is_on": {"var": "书房射灯"}}, {"is_on": {"var": "书房吊灯"}}]}
           ]},
  "do":   [{"action": "climate.turn_on",       "target": "书房空调"},
           {"action": "climate.set_hvac_mode",  "target": "书房空调", "params": {"hvac_mode": "cool"}},
           {"action": "climate.set_temperature","target": "书房空调", "params": {"temperature": 26}}]
}
```

形状铁律（都带服务端报错示例，撞了照 fix 改即可）：
- `when` / `if` / `ask` / 每个 `do` 项 / `params` 都必须是**对象**，不能是字符串。
- `when` 的实体字段是 `entity`（中文名或完整 entity_id 都可）+ `to`（目标状态）；
  时间/太阳触发用 `{"type":"time","at":"22:00:00"}` 形态。
- `do[].action` 写**裸 `<domain>.<service>`**（`climate.set_hvac_mode`），或中文动作名
  （开灯/关灯/开空调/关空调）。⛔ 不要写 `ha.` / `ha:` 前缀——那是 AF-Spec 的写法；
  意图侧写了会被剥掉归一，但请直接用对的形式。
- 参数走 `params`（`{"hvac_mode":"cool"}`）或平铺 `temperature`/`hvac_mode` 两枚快捷字段。
- `if` 里的实体引用：`when`/`do` 用 `{"entity": ...}`，一元谓词两种都收——
  `{"is_on": {"var": "书房灯"}}` 与 `{"is_on": {"entity": "书房灯"}}` 同义（体验者 AF-009）。
  编译进 IR 后统一是 `value={"var": "entity.<id>"}`，别名不外泄。
- 动作名与目标实体的**域**要匹配：中文动作名（开灯/关灯/开空调）与裸 `turn_on`/`turn_off`
  是**别名**，服务端按目标实体的域归一服务名——`开灯` 配 `switch.abc` 编成 `switch.turn_on`
  （体验者 AF-014：旧实现是域盲的，编出 `light.turn_on` 打 switch，真机必拒而仿真器照样翻状态）。
  显式写 `light.turn_on` 则**不改**（那是你的明确决定）。目标域在效果表里没有该服务时
  保留缺省域，交给 build 那一站报错——不悄悄换个域把问题挪走。
- 条件为真时动作会**自动执行**；但若动作带 `requires_confirm`（L2 门锁/窗帘/空调强制），
  实例会合法地停在确认闸等人工同意。`af_apply(dry_run)` 的回执用 `held_at_confirm` +
  `confirm_note` 明说这件事（⛔ 不静默停在 suspended 让人以为链断了）。
  要在仿真里看整条链跑通，给 `af_simulate` 的 events 追加一条应答：
  `events=[{"entity_id":"binary_sensor.门","state":"off"}, {"answer":"yes"}]`。
- 比较键是封闭词表：`eq/ne/lt/lte/gt/gte`（⛔ 没有 `ge`/`le`，≥/≤ 写 `gte`/`lte`）；
  组合用 `and/or/not`。全集见下方「常用 expr 形式」。

### 路 B：AF-Spec 的动作子句长什么样（体验者常问）

```
do d1 ha.climate.set_hvac_mode {"entity_id": "climate.lumi_cn_84159632_v2", "hvac_mode": "cool"} name "制冷"
do d2 ha.climate.set_temperature {"entity_id": "climate.lumi_cn_84159632_v2", "temperature": 26} name "设26度"
```

要点：`do <id> <adapter>.<service> <params JSON> [result <var>] [name "<名称>"]`——
一行一个节点，`name` 是**节点尾随子句**不是独立行；自动化标题 `name "…"` 才独占一行。
条件节点 `if <id> <expr JSON>`，expr 用 `{"op": "gt", "left": {"var": "entity.sensor.x", "type": "numeric"}, "right": {"const": 26.5}}` 形态。

> ⚠️ **路 B 的 `var` 必须带命名空间前缀**（`entity.` / `vars.` / `context.`）——这是路 B 与路 A
> 最容易踩的差别：`af_resolve_entity` 返回的是**裸 entity_id**（如 `sensor.duka_th2_temperature`），
> 直接粘进 `var` 会求值失败；要写成 `entity.sensor.duka_th2_temperature`。
> 路 A（af_draft 意图 JSON）不用管：那边 `var` 写中文名，服务端查目录后自动补 `entity.` 前缀。
> 读实体属性写 `entity.<id>.<attr>`（如 `entity.climate.ac.temperature`）。
> 写错前缀的图过去会**静默假绿**（build 放行、仿真 ok=True、实例正常 done、动作永不执行）；
> 现在扫描器以 `EXPR_NS_INVALID` 当场拒收，运行期也只认显式 `on_error` 边、否则实例 `failed`。

## AF-Spec 语法

### 基本结构

```
automation <id>
name "<名称>"
mode restart

on t1 {"type": "state", "entity_id": "<真实entity_id>", "to": "on"} name "触发描述"
do d1 ha.<domain>.<action> {"entity_id": "<目标entity_id>"} result r1 name "动作描述"
pass p1 name "结束"

edge t1 -> d1 then
edge d1 -> p1 then
```

### 七种节点

| 节点 | 语法 | 说明 |
|------|------|------|
| on | `on <id> <trigger JSON> [name "<名称>"]` | 触发节点 |
| if | `if <id> <expr JSON> [name "<名称>"]` | 条件节点 |
| do | `do <id> ha.<domain>.<action> <params JSON> [result "<变量名>"] [name "<名称>"]` | 动作节点 |
| ask | `ask <id> "<提示语>" [room "<房间>"] [timeout "30s"] [name "<名称>"]` | 询问节点 |
| wait | `wait <id> "<时长>" [name "<名称>"]` | 等待节点 |
| set | `set <id> var "<变量名>" value <JSON值> [name "<名称>"]` | 赋值节点 |
| pass | `pass <id> [name "<名称>"]` | 结束节点 |

### 常用 trigger 类型

> ⚠️ trigger 是**封闭字段集**（`type` / `entity_id` / `to` / `from` / `at` / `event` /
> `offset` / `sources` / `op`），schema `additionalProperties: false` —— 多写的键不是
> "被忽略"而是**整条被拒**。已踩的两个假写法：`today_only`（不存在）、`event_name`
> （该写 `event`）。另外 `ask` 节点必须带 `on_timeout` 或 `default` 出边。

```json
// 状态变化
{"type": "state", "entity_id": "binary_sensor.xxx", "to": "on"}

// 时间触发
{"type": "time", "at": "22:00:00"}

// 太阳触发
{"type": "sun", "event": "sunset", "offset": "-PT30M"}

// 自定义事件
{"type": "event", "event": "my_event"}        # 字段叫 event，不是 event_name
```

### 常用 expr 形式

**算子是封闭词表**（写错的报错只说"不合 schema"，所以这里把全集列全）：

| 类别 | 可用 |
|---|---|
| 逻辑 | `and` / `or` / `not`（`{"op":"and","args":[...]}`） |
| 比较 | `eq` / `ne` / `lt` / `lte` / `gt` / `gte` |
| 一元 | `is_on` / `is_off` / `is_home` / `is_not_home` / `truthy` / `not_is_on` / `not_is_off` |

```json
// 数值比较
{"op": "lt", "left": {"var": "entity.sensor.x", "type": "numeric"}, "right": {"const": 23.5}}

// 状态等于
{"op": "eq", "left": {"var": "entity.sensor.x", "type": "string"}, "right": {"const": "on"}}

// ≥ 与 ≤ 没有 ge / le —— 写 gte / lte（体验者 AF-007 就撞在这）
{"op": "gte", "left": {"var": "entity.sensor.temp", "type": "numeric"}, "right": {"const": 26.5}}
```

### 常用 do 动作

```
ha.light.turn_on / ha.light.turn_off
ha.climate.set_temperature / ha.climate.set_hvac_mode
ha.switch.turn_on / ha.switch.turn_off
```

### 八种边

| kind | 说明 |
|------|------|
| then | 无条件顺序执行 |
| yes | if 条件为 true |
| no | if 条件为 false |
| on_error | do 动作失败 |
| timeout | ask 超时 |
| cancel | ask 取消 |

## 完整示例

### 示例1：人来开灯

```
automation study_motion
name "书房人来开灯"
mode restart

on t1 {"type": "state", "entity_id": "binary_sensor.motion", "to": "on"} name "检测到人"
do d1 ha.light.turn_on {"entity_id": "light.study"} result r1 name "开灯"
pass p1 name "结束"

edge t1 -> d1 then
edge d1 -> p1 then
```

### 示例2：定时询问关灯

```
automation night_off
name "晚上10点提醒关灯"

on t1 {"type": "time", "at": "22:00:00"} name "晚上10点"
ask a1 "要关显示器挂灯吗？" room "书房" timeout "30s" name "询问"
do d1 ha.light.turn_off {"entity_id": "light.desk_lamp"} result r1 name "关灯"
pass p1 name "结束"

edge t1 -> a1 then
edge a1 -> d1 yes
edge a1 -> p1 timeout
edge a1 -> p1 cancel
edge d1 -> p1 then
```

### 示例3：日落开客厅灯

```
automation sunset_light
name "日落开客厅灯"

on t1 {"type": "sun", "event": "sunset", "offset": "-PT30M"} name "日落前30分钟"
do d1 ha.light.turn_on {"entity_id": "light.living_room"} result r1 name "开客厅灯"
pass p1 name "结束"

edge t1 -> d1 then
edge d1 -> p1 then
```

## 判读写权限：读 `write_gate`，不要读 `readonly`

`/api/health` 与 `af_health` 里有两枚键，语义不同（体验者 AF-010 把后者当成了矛盾）：

- `readonly`：**遗留身份声明**（v1.x 只读服务层的身份），已有读方按它判读，值不反映运行期闸态；
- `write_gate`：**运行期真值**，取值 `open` / `blocked` / `no_lease`——
  想知道"现在能不能写"只看这一枚。`blocked` 有两类成因：装配期就降级（抢不到单写者租约，
  HTTP 写端点 503），或运行期租约被别的进程持有。`no_lease` = 无从判断，⛔ 不许读成"能写"。

注意 MCP 写工具走的是令牌 scope + 归档侧的闸，与 HTTP 的 `_write` 不是同一条路：
`readonly:true` 时 MCP 的 `af_save` 仍可能成功，这不是 bug 而是两把闸各管一面（要判"这台能不能落盘"，看 `write_gate`）。

## 测试通道（af_test_submit）与正式通道同口径

批量提交（draft → build → simulate → 自动 approve → 落 `/data/test/`）里的中文名**与
`af_draft` 一样走主 store 的设备目录**（体验者 AF-015：旧实现没传目录，同一个
"书房灯"在正式通道能解析、测试通道全变 `?占位符`）。只读解析，不写主 store。

## 工具速查

| 工具 | 用途 |
|------|------|
| af_resolve_entity | 设备名→真实 entity_id（写 spec 前必调） |
| af_list_entities | 浏览全屋实体 |
| af_compile_spec | AF-Spec 文本→IR |
| af_build | 校验 IR |
| af_simulate | 仿真回放验证 |
| af_save | 保存到待批队列 |

## 排错

- **IR_SCHEMA_INVALID / `id` 校验失败**：自动化与节点 id 必须匹配 `^[a-z][a-z0-9_]*$`
  （**小写**，首次提交起就是这条规则，不是新加的）；中文/大写/带空格的标题请写进 `name`，
  `id` 用 ascii 蛇形。
- **EXPR_NS_INVALID**：IR 里 `var` 少了命名空间前缀——见上文路 B 铁律，写 `entity.<完整id>`
  （`ge`/`le` 同理改成 `gte`/`lte`）。
- **SpecError**：AF-Spec 语法错，检查 JSON 格式和节点边是否完整
- **ENTITY_NOT_FOUND**：entity_id 不存在，用 af_resolve_entity 重新查
- **DEVICE_ACL_DENIED**：高风险设备（锁/热水器）被拦截，不可绕过
