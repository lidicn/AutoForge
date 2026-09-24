# AF-Spec 完整语法参考

> AF-Spec 是 AutoForge 的 agent 撰写面，编译到 JSON IR。一行一个节点/边，结构化子对象用内联 JSON。

---

## 1. 基本结构

```
automation <id>
name "<中文名称>"
mode restart
confidence 0.9

on t1 {"type": "state", "entity_id": "binary_sensor.xxx", "to": "on"} name "触发"
do d1 ha.light.turn_on {"entity_id": "light.yyy"} result r1 name "开灯"
pass p1 name "结束"

edge t1 -> d1 then
edge d1 -> p1 then
```

## 2. 顶层选项行

在 `automation <id>` 之后、节点之前，每行一个选项：

| 语法 | 说明 | 示例 |
|------|------|------|
| `name "<名称>"` | 自动化名称 | `name "书房人来灯亮"` |
| `version <整数>` | 版本号，默认 1 | `version 1` |
| `mode <模式>` | 触发模式：single/restart/queued/parallel | `mode restart` |
| `confidence <浮点>` | 置信度 0-1 | `confidence 0.9` |
| `snapshot <true/false>` | 是否快照 | `snapshot true` |
| `persist <true/false>` | 是否持久化 | `persist true` |
| `ir_version "<版本>"` | IR 版本，默认当前 | `ir_version "0.2.1"` |
| `meta {JSON}` | 元数据 | `meta {"acceptance": "G1"}` |
| `var "<名>" {JSON}` | 声明变量 | `var "flag" {"type": "boolean", "value": false}` |
| `expect {JSON}` | 后置断言，可重复 | `expect {"entity_id": "light.x", "state": "on"}` |
| `# 注释` | 注释行 | `# 这是注释` |

## 3. 七种节点

### 3.1 on（触发节点）

```
on <id> <trigger JSON> [for "<时长>"] [debounce "<时长>"] [name "<名称>"]
```

**trigger JSON 常用类型：**

```json
// 状态变化触发
{"type": "state", "entity_id": "binary_sensor.xxx", "to": "on"}

// 时间触发（cron）
{"type": "time", "at": "22:00:00", "today_only": true}

// 太阳触发
{"type": "sun", "event": "sunset", "offset": "-PT30M"}

// 自定义事件触发
{"type": "event", "event_name": "my_custom_event"}
```

**示例：**
```
on t1 {"type": "state", "entity_id": "binary_sensor.0x00158d0001f34db6_contact", "to": "on"} name "防盗门打开"
on t2 {"type": "time", "at": "22:00:00", "today_only": true} name "晚上10点"
on t3 {"type": "sun", "event": "sunset", "offset": "-PT30M"} name "日落前30分钟"
on t4 {"type": "event", "event_name": "custom_event"} name "收到自定义事件"
```

### 3.2 if（条件节点）

```
if <id> <expr JSON> [name "<名称>"]
```

**expr JSON 常用形式：**

```json
// 数值比较
{"op": "lt", "left": {"var": "entity.sensor.x", "type": "numeric"}, "right": {"const": 23.5}}

// 状态等于
{"op": "eq", "left": {"var": "entity.sensor.x", "type": "string"}, "right": {"const": "on"}}

// 逻辑与
{"op": "and", "left": {...}, "right": {...}}

// 逻辑或
{"op": "or", "left": {...}, "right": {...}}

// 否定
{"op": "not", "left": {...}}
```

**比较运算符：** `eq` `ne` `lt` `lte` `gt` `gte`

**变量引用：**
- `entity.<entity_id>` — 实体状态
- `entity.<entity_id>.<attribute>` — 实体属性（如 `entity.climate.x.temperature`）
- `<变量名>` — 已声明的变量

**示例：**
```
if i1 {"op": "lt", "left": {"var": "entity.sensor.illum", "type": "numeric"}, "right": {"const": 200}} name "亮度低于200"
if i2 {"op": "eq", "left": {"var": "entity.sensor.temp", "type": "string"}, "right": {"const": "off"}} name "温度传感器off"
```

### 3.3 do（动作节点）

```
do <id> <adapter>.<action> [params JSON] [result "<变量名>"] [confirm] [atomic] [name "<名称>"]
```

**常用 adapter.action：**
- `ha.light.turn_on` / `ha.light.turn_off`
- `ha.climate.set_hvac_mode` / `ha.climate.set_temperature`
- `ha.switch.turn_on` / `ha.switch.turn_off`
- `ha.media_player.turn_on` / `ha.media_player.turn_off`
- `ha.cover.open_cover` / `ha.cover.close_cover`

**params JSON 常用字段：**
```json
{"entity_id": "light.xxx"}
{"entity_id": "climate.xxx", "temperature": 24}
{"entity_id": "climate.xxx", "hvac_mode": "cool"}
```

**示例：**
```
do d1 ha.light.turn_on {"entity_id": "light.mijia_cn_group_1861372413196005378_group4_s_2_light"} result r1 name "开客厅灯"
do d2 ha.climate.set_temperature {"entity_id": "climate.lumi_cn_84159632_v2", "temperature": 23.5} name "设温度23.5度"
do d3 ha.climate.set_hvac_mode {"entity_id": "climate.lumi_cn_84159632_v2", "hvac_mode": "cool"} name "开制冷"
```

### 3.4 ask（询问节点）

```
ask <id> "<提示语>" [room "<房间>"] [timeout "<时长>"] [session "<会话>"] [name "<名称>"]
```

**示例：**
```
ask a1 "要关显示器挂灯吗？" room "书房" timeout "30s" name "询问是否关灯"
```

### 3.5 wait（等待节点）

```
wait <id> "<时长>" [name "<名称>"]
```

**时长格式：** `"30s"` `"5m"` `"1h"`

**示例：**
```
wait w1 "5m" name "等待5分钟"
```

### 3.6 set（变量赋值节点）

```
set <id> var "<变量名>" value <JSON值> | from "<来源>" [name "<名称>"]
```

**示例：**
```
set s1 var "flag" value true name "设置flag为true"
set s2 var "temp" from "entity.sensor.temperature" name "从传感器读温度"
```

### 3.7 pass（结束节点）

```
pass <id> [name "<名称>"]
```

**示例：**
```
pass p1 name "结束"
```

## 4. 边（edge）

```
edge <from节点> -> <to节点> <kind> [id "<边id>"] [label "<标签>"]
```

**edge kind 常用值：**

| kind | 说明 |
|------|------|
| `then` | 无条件顺序执行 |
| `yes` | if 条件为 true 时走 |
| `no` | if 条件为 false 时走 |
| `on_error` | do 动作失败时走 |
| `default` | ask 默认分支 |
| `timeout` | ask 超时分支 |
| `cancel` | ask 取消分支 |

**示例：**
```
edge t1 -> i1 then
edge i1 -> d1 yes
edge i1 -> p1 no
edge d1 -> p1 on_error id e2 label "动作失败兜底"
edge a1 -> d1 yes
edge a1 -> p1 timeout
edge a1 -> p1 cancel
```

## 5. 完整示例

### 示例1：人来开灯

```
automation study_day_light
name "书房白天人来补光"
mode restart

on t1 {"type": "state", "entity_id": "binary_sensor.lumi_cn_lumi_158d0001a2520d_aq2_motion_state_p_2_1", "to": "on"} name "书房检测到人"
if i1 {"op": "lt", "left": {"var": "entity.sensor.illum", "type": "numeric"}, "right": {"const": 200}} name "亮度低于200"
do d1 ha.light.turn_on {"entity_id": "light.yeelink_cn_555003624_lamp22_s_2"} result r1 name "开挂灯"
pass p1 name "结束"

edge t1 -> i1 then
edge i1 -> d1 yes
edge i1 -> p1 no
edge d1 -> p1 then
```

### 示例2：定时关灯+询问

```
automation night_off
name "晚上10点提醒关灯"

on t1 {"type": "time", "at": "22:00:00", "today_only": true} name "晚上10点"
ask a1 "要关显示器挂灯吗？" room "书房" timeout "30s" name "询问是否关灯"
do d1 ha.light.turn_off {"entity_id": "light.yeelink_cn_555003624_lamp22_s_2"} result r1 name "关挂灯"
pass p1 name "结束"

edge t1 -> a1 then
edge a1 -> d1 yes
edge a1 -> p1 timeout
edge a1 -> p1 cancel
edge d1 -> p1 then
```

### 示例3：太阳触发

```
automation sunset_light
name "日落开客厅灯"

on t1 {"type": "sun", "event": "sunset", "offset": "-PT30M"} name "日落前30分钟"
do d1 ha.light.turn_on {"entity_id": "light.mijia_cn_group_1861372413196005378_group4_s_2_light"} result r1 name "开客厅灯"
pass p1 name "结束"

edge t1 -> d1 then
edge d1 -> p1 then
```

### 示例4：跨自动化事件

```
automation motion_event
name "人来发事件"

on t1 {"type": "state", "entity_id": "binary_sensor.motion", "to": "on"} name "检测到人"
do d1 ha.light.turn_on {"entity_id": "light.study"} result r1 emit {"event_name": "motion_detected", "payload": {"room": "study"}}
pass p1 name "结束"

edge t1 -> d1 then
edge d1 -> p1 then

automation event_listen
name "收到事件关灯"

on t2 {"type": "event", "event_name": "motion_detected"} name "收到人来事件"
do d2 ha.light.turn_off {"entity_id": "light.study"} result r2 name "关灯"
pass p2 name "结束"

edge t2 -> d2 then
edge d2 -> p2 then
```

## 6. 注意事项

1. **必须先有 `automation <id>` 行**，后面才能写节点和选项
2. **节点 id 在同一 automation 内唯一**
3. **edge 的 from/to 必须指向已定义的节点 id**
4. **JSON 对象必须用双引号**，不能用单引号
5. **字符串值在 JSON 内用双引号，在 name/room 等裸词位置可以不加引号**（建议都加）
6. **注释用 `#` 开头**，空行自动忽略
7. **每个 automation 至少要有一个 on 节点和一个 pass/do 节点**
