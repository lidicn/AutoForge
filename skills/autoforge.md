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
2. 写 AF-Spec 文本（语法见下方）
    ↓
3. af_compile_spec(text=<AF-Spec>) → 编译成 IR
    ↓
4. af_build(ir=<IR>) → 安全闸校验
    ↓
5. af_simulate(ir=<IR>, seed={...}, events=[...]) → 仿真验证
    ↓
6. af_save(ir=<IR>) → 进待批队列，等人审批
```

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
