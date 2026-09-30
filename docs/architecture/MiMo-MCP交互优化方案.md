# AutoForge MCP 交互优化方案（MiMo 设计）

> 来源：mimo V2.6-Pro，2026-09-24
> 一句话总结：让 LLM 只输出"意图 JSON"，把 AF-Spec 从"Agent 的输出"降级为"系统的导出格式"；IR 不回传、用 ref 流转；校验+仿真+入队合并成一次 af_apply；工具面 24→8（常驻 5）；Skill 三层化，正文 ≤120 行。

## 预计效果对比

| 环节 | 现状 | 方案后 |
|------|------|--------|
| 工具定义常驻上下文 | 24 个 ≈ 2.5-3k tokens/轮 | 5 个 ≈ 0.6k/轮 |
| 实体解析 | 2-4 次调用 | 0-1 次（draft 内部解析） |
| 规则表达 | AF-Spec 文本 300-600 tokens | 意图 JSON 60-150 tokens |
| IR 传输 | compile/build/save 各带全量 IR，3 次 | 0 次（服务端暂存 + ref） |
| 校验/仿真/保存 | 3 次调用 | 1 次调用 |
| 仿真用例 | Agent 编写 150-300 tokens | 服务端派生 0 |
| **合计** | **6-9 次调用 / 2-4k tokens** | **1-2 次调用 / 0.3-0.8k tokens** |

## 一、根本问题：DSL 是"纯负债"

真正的浪费在四处：
1. 让 LLM 写 DSL 语法是贵且易错的：节点 id、edge 都是机器本该自己生成的样板
2. 同一份 IR 在三次调用里反复传输，纯浪费
3. 仿真用例还要 Agent 自己编，而触发条件里其实已经蕴含了测试用例
4. resolve 与 draft 分离，Agent 先查 id 再写规则，两轮往返

**优化方向**：AF-Spec 不再是 API 契约，只是给人看的导出格式。Agent 提交的是带 JSON Schema 的意图对象，服务端负责起 id、建节点、连边、编译、派生测试、安全闸、入队。

## 二、新的工具面：24 → 8（常驻 5）

| 工具 | 合并了 | 用途 |
|------|--------|------|
| af_draft | 部分 af_compile_spec | 意图 JSON → IR，返回 ref |
| af_apply | af_build + af_simulate + af_save | 一次走完 校验→仿真→入队 |
| af_entities | af_resolve_entity / af_remember_entity / af_list_entities / af_catalog | op=search\|resolve\|remember |
| af_state | af_get_entity_state | 批量查状态 |
| af_graphs | af_list_graphs / af_get_graph / af_graphs_by_tag / af_diff / af_export_store | op=list\|get\|by_tag\|diff\|export |
| af_tags | af_set_tags / af_enable_by_tag | 打标、按标启停 |
| af_admin | af_health / af_conf / af_whoami / af_telemetry / af_experience / af_refresh_catalog | 诊断类，单独一个 MCP server |
| af_live_run | 原样保留 | 真机执行，独立 + 强制 confirm 令牌 |

**要点**：
- af_admin 拆成第二个 MCP server，主 server 每轮省约 2k tokens
- af_live_run 永远独立，参数要求 confirm: "<graph_id>@<version>" 不可误触
- 工具描述压到 3 行以内，细节放 inputSchema 字段描述里
- 带 MCP annotations（readOnlyHint / destructiveHint / idempotentHint）

## 三、三个省 token 的关键机制

### 1. ref 化：IR 永不回传模型

```json
// af_draft 返回
{"ok": true, "ref": "af:7c31", "summary": "开门 → 若照度<200 → 开客厅灯",
 "resolved": {"灯": "light.living_room", "门": "binary_sensor.front_door"}}
```

IR 存在服务端 staging 区（会话级、带 TTL），后续全部用 ref 引用。需要看全文时显式要：`{"op":"export","ref":"af:7c31","format":"spec"}`。

### 2. patch 化：修错不重发全文

校验失败返回结构化错误 + 可直接回填的 patch：
```json
{"ok": false, "stage": "build", "error": {"code": "E_ENTITY_AMBIGUOUS",
 "candidates": [...], "fix": {"op": "replace", "path": "$.flow.do[0].target", "value": "light.living_room"}}}
```

Agent 回传 `{"op":"patch","ref":"af:7c31","patches":[...]}` 即可，不重发整个 draft。

### 3. preset 化：常用场景固化

```json
{"preset": "door_light", "params": {"door": "前门", "light": "客厅灯", "lux_below": 200}}
```

Agent 只传参数，服务端展开成完整 flow。非 preset 需求用 flow 结构（嵌套 JSON，不是文本 DSL）。

## 四、意图 JSON 结构（flow）

```json
{
  "name": "开门亮灯",
  "mode": "restart",
  "when": {
    "type": "state",
    "entity": "前门",
    "to": "on"
  },
  "if": {
    "lt": {"var": "照度", "const": 200}
  },
  "do": {
    "action": "开灯",
    "target": "客厅灯"
  }
}
```

实体写中文名/别名，服务端内部解析成 entity_id。7 种节点、8 种边全部映射到嵌套结构。

## 五、af_apply 合并

一次调用走完校验→仿真→入队：
- 静态校验：实体存在性 / ACL / 环检测
- 仿真：服务端从 when 派生 3-5 个回放用例（触发 true/false、超时、on_error）
- 保存：intent_id 幂等键，Agent 重试不会产生重复待批项

## 六、Skill 文档：三层递进披露

```
skills/autoforge/
  SKILL.md              # ≤120 行，唯一常驻，约 1.2k tokens
  references/
    triggers.md         # when 的全部形态（any/all/多触发）
    actions.md         # do/ask/wait/set/if 字段细则
    presets.md         # preset 清单与参数
    errors.md          # 错误码 → 修复动作
    afspec.md          # 旧 AF-Spec 语法，仅维护旧图时读
  examples/
    motion_light.json   # 每个 ≤ 8 行，共 6-8 个
```

### SKILL.md 实稿

```markdown
---
name: autoforge
description: 创建/修改 AutoForge 智能家居自动化。当用户说"xx 时做 xx""自动开灯""传感器联动""定时""场景"等需求时使用。
---

# AutoForge 快速上手

## 黄金路径（一次调用搞定）
af_draft 传意图 JSON → 用返回的 ref 调 af_apply(stages="save")。

    {"preset": "door_light", "params": {"door": "前门", "light": "客厅灯", "lux_below": 200}}
    → {"ref": "af:7c31"}
    → af_apply({"ref": "af:7c31", "stage": "save"})

实体写中文名/别名即可，服务端解析；不要自己写 entity_id。
非 preset 需求用 flow 结构，见 examples/ 里最接近的一份照抄改参数。

## 三条铁律
1. 只传 ref，不传 IR；要看全文用 af_graphs(op="export")。
2. 校验失败按返回的 fix 做 patch 回传，不要重发整个 draft。
3. af_live_run 是真机执行，未经用户明确同意不得调用。

## 出错先查这里
| 错误码 | 动作 |
|---|---|
| E_ENTITY_AMBIGUOUS | 从 candidates 选一个，patch 回传 |
| E_ACL_DENIED | 该实体无权限，换实体或让用户授权，不要绕过 |
| E_CYCLE | flow 分支成环，检查 on_error/wait 分支的回跳 |
| 其他 | 读 references/errors.md |

## 什么时候读 references
改触发 → triggers.md；改动作/询问/等待 → actions.md；
不确定用哪个 preset → presets.md；维护旧 AF-Spec 图 → afspec.md
```

## 七、mcp_server 瘦身

```
autoforge/
  mcp/
    server.py       # 100-150 行：挂载 registry，无业务分支
    registry.py     # 声明式工具表 + @tool 装饰器
  core/
    draft.py        # 意图 JSON → IR（含 id 生成、边合成）
    presets.py      # preset 表 = 文档生成源
    build.py        # 安全闸：实体存在性 / ACL / 环检测
    simulate.py     # 用例派生 + 虚拟设备回放
    catalog.py      # 实体目录 / 解析 / 别名
    store.py        # 图存储 / diff / 导入导出
    errors.py       # 错误码表 = hint + fix + errors.md 生成源
```

**四条红线**：
1. mcp/ 里禁止出现 if/else 业务分支，只有解析→调用→序列化
2. 所有 HA / 存储 I/O 走 core 后面的 port 接口，core 可单测
3. schema 单一来源（pydantic 模型），文档由表生成
4. 新增能力优先扩 op enum 或 preset，而不是新增工具

## 八、迁移与度量

- 旧的 24 个工具保留两个版本周期，标记 deprecated
- af_compile_spec 保留为 af_draft(op="from_spec")
- 用 af_telemetry 对比：工具调用数、输入 token、首次成功率、语法错误失败率
- preset 覆盖率单独看：非 preset flow 形状反复出现就固化成新 preset
