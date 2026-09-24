---
name: autoforge
description: 创建/修改 AutoForge 智能家居自动化。当用户说"xx 时做 xx""自动开灯""传感器联动""定时""场景"等需求时使用。
---

# AutoForge 快速上手

## 黄金路径（两次调用搞定）

```
af_draft(intent) → af_apply(ref, stage="save")
```

### 第 1 步：af_draft — 传意图 JSON

实体写中文名/别名，服务端自动解析成 entity_id。

```json
{
  "name": "开门亮灯",
  "when": {"type": "state", "entity": "前门", "to": "on"},
  "do": {"action": "开灯", "target": "客厅灯"}
}
```

返回 `{"ok": true, "ref": "af:7c31", "summary": "前门 on → 开灯 客厅灯"}`

### 第 2 步：af_apply — 校验+仿真+入队

```json
{"ref": "af:7c31", "stage": "save"}
```

- `stage="check"`：只校验，不入队
- `stage="simulate"`：校验+仿真
- `stage="save"`：校验+仿真+入待批队列（默认）

## 意图 JSON 格式

```
{
  "name": "自动化名称",
  "mode": "restart | single | queue",
  "when": {触发条件},
  "if": {可选条件},
  "ask": {可选询问},
  "wait": {可选等待},
  "do": {动作}
}
```

### when（触发）

```json
// 状态变化
{"type": "state", "entity": "前门", "to": "on"}

// 定时
{"type": "time", "at": "22:00"}

// 日出日落
{"type": "sun", "event": "sunset", "offset": "-1800"}
```

### if（条件）

```json
{"lt": {"var": "照度", "const": 200}}
{"and": [{"lt": {"var": "温度", "const": 26}}, {"eq": {"var": "湿度", "const": 60}}]}
```

### do（动作）

```json
{"action": "开灯", "target": "客厅灯"}
{"action": "开空调", "target": "书房空调", "temperature": 24, "hvac_mode": "cool"}
```

### ask（询问）

```json
{"prompt": "要关灯吗？", "room": "书房", "timeout": "30s"}
```

## 三条铁律

1. **只传 ref，不传 IR**——af_draft 返回的 ref 就是唯一引用
2. **校验失败按 fix 做 patch**——不要重发整个 draft
3. **af_live_run 是真机执行**——未经用户明确同意不得调用

## 出错先查这里

| 错误码 | 动作 |
|--------|------|
| E_MISSING_WHEN | 补 when 字段（触发条件） |
| E_MISSING_DO | 补 do 字段（动作） |
| E_ENTITY_AMBIGUOUS | 从 candidates 选一个，重传 draft |
| E_ACL_DENIED | 该实体无权限，换实体，不要绕过 |
| E_CYCLE | 分支成环，检查 on_error/wait 分支 |
| E_REF_NOT_FOUND | ref 过期了，重新 draft |

## 什么时候读 references

改触发 → references/triggers.md
改动作/询问/等待 → references/actions.md
维护旧 AF-Spec 图 → references/afspec.md
