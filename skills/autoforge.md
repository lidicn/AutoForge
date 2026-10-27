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

### 路 B：AF-Spec 的动作子句长什么样（体验者常问）

```
do d1 ha.climate.set_hvac_mode {"entity_id": "climate.lumi_cn_84159632_v2", "hvac_mode": "cool"} name "制冷"
do d2 ha.climate.set_temperature {"entity_id": "climate.lumi_cn_84159632_v2", "temperature": 26} name "设26度"
```

要点：`do <id> <adapter>.<service> <params JSON> [result <var>] [name "<名称>"]`——
一行一个节点，`name` 是**节点尾随子句**不是独立行；自动化标题 `name "…"` 才独占一行。
条件节点 `if <id> <expr JSON>`，expr 用 `{"op": "gt", "left": {"var": "entity.sensor.x", "type": "numeric"}, "right": {"const": 26.5}}` 形态。

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

```json
// 状态变化
{"type": "state", "entity_id": "binary_sensor.xxx", "to": "on"}

// 时间触发
{"type": "time", "at": "22:00:00", "today_only": true}

// 太阳触发
{"type": "sun", "event": "sunset", "offset": "-PT30M"}

// 自定义事件
{"type": "event", "event_name": "my_event"}
```

### 常用 expr 形式

```json
// 数值比较
{"op": "lt", "left": {"var": "entity.sensor.x", "type": "numeric"}, "right": {"const": 23.5}}

// 状态等于
{"op": "eq", "left": {"var": "entity.sensor.x", "type": "string"}, "right": {"const": "on"}}
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

on t1 {"type": "time", "at": "22:00:00", "today_only": true} name "晚上10点"
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

- **SpecError**：AF-Spec 语法错，检查 JSON 格式和节点边是否完整
- **ENTITY_NOT_FOUND**：entity_id 不存在，用 af_resolve_entity 重新查
- **DEVICE_ACL_DENIED**：高风险设备（锁/热水器）被拦截，不可绕过
