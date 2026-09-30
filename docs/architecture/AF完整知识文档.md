# AutoForge 完整知识文档

> 更新时间：2026-09-24
> 基于实测验证，非理论设计

---

## 一、项目概述

**AutoForge（AF）** 是一个智能家居自动化的"设计+运行"一体化平台。

核心能力：
- 自然语言 → 意图 JSON → IR（中间表示）
- 安全闸校验（StaticScanner）
- 仿真回放（simulate）
- 待批队列（pending）
- 人工 approve → 版本化存储（GraphStore）
- 常驻运行时（forge watch）订阅 HA 事件流，实时驱动自动化

**设计哲学**：
- fail-closed（默认拒绝）
- 安全闸优先
- 版本化可追溯
- 真机操作需白名单 + 二次确认

---

## 二、架构总览

### 2.1 两个进程

| 进程 | 命令 | 职责 | 碰真机 |
|------|------|------|--------|
| **HTTP/MCP 服务** | `forge serve --host 0.0.0.0 --port 8787 --store-root /data` | 设计/编译/仿真/MCP 接口 | ❌ |
| **运行时 daemon** | `forge watch <ir.json> --confirm --ha-url ... --live-allow ...` | 常驻订阅 HA 事件，实时驱动 | ✅ |

### 2.2 数据流

```
用户自然语言
    ↓
af_draft(intent JSON) → IR → staging 区 → 返回 ref
    ↓
af_apply(ref, stage)
    ├── check: 只过 build 安全闸
    ├── simulate: build + 仿真回放
    └── save: build + simulate + 入待批队列
    ↓
待批队列（/data/pending/*.json）
    ↓
人工 approve（CLI: pending approve）
    ↓
GraphStore 版本化存储（/data/）
    ↓
导出 IR JSON
    ↓
forge watch 常驻运行
    ↓
HA 事件流（SSE /api/stream）
    ↓
Runtime 评估触发 → 执行 do 节点
    ↓
HAAdapter 调用 HA 服务 → 真机动作
```

---

## 三、核心模块

### 3.1 源码结构

```
E:\NAS\AutoForge\src\autoforge\
├── af_draft.py          # 意图 JSON → IR（新，MiMo 方案）
├── af_apply.py          # 合并 build+simulate+save（新，MiMo 方案）
├── af_mcp.py            # MCP 接口层（HTTP JSON-RPC）
├── af_api.py            # HTTP REST API（FastAPI）
├── af_cli.py            # 命令行工具（Typer）
├── af_service.py        # 服务层（build/simulate/submit_pending/approve）
├── af_runtime.py        # Runtime 引擎（事件驱动）
├── af_store.py          # GraphStore 版本化存储
├── af_pending.py        # 待批队列
├── af_scanner.py        # 静态安全闸（StaticScanner）
├── af_live.py           # 真机运行（watch/run）
├── af_spec.py           # AF-Spec 文本解析
├── af_adapters.py       # HA 适配器（HAAdapter/HTTPAdapter/MockAdapter）
├── af_ir/               # IR 模型与节点定义
│   ├── models.py        # IR 数据模型
│   └── nodes.py         # 节点类型（on/if/do/ask/wait/pass/emit）
├── af_vhass/            # vhass 虚拟 HA 仿真（5 个模块）
├── af_health.py         # 健康度评估
├── af_evo.py            # 自进化提案生成器
├── af_conflict.py       # 跨自动化冲突仲裁器
├── af_self_repair.py    # 自修正闭环
├── af_runtime_plugins.py # 运行时插件总装配
└── ...
```

### 3.2 IR 节点类型

| 节点 | kind | 说明 |
|------|------|------|
| 触发 | `on` | state/time/sun/event 触发 |
| 条件 | `if` | 表达式判断（gt/lt/eq/ne/and/or/not） |
| 动作 | `do` | 调用 HA 服务（light.turn_on 等） |
| 询问 | `ask` | 人工确认（超时兜底） |
| 等待 | `wait` | 延时等待 |
| 结束 | `pass` | 终止节点 |
| 发事件 | `emit` | 发自定义事件 |

### 3.3 边类型

| 边 | kind | 说明 |
|----|------|------|
| 顺序 | `then` | 正常执行流 |
| 条件真 | `true` | if 条件为真 |
| 条件假 | `false` | if 条件为假 |
| 错误 | `on_error` | do 节点执行失败 |
| 超时 | `on_timeout` | ask/wait 超时 |
| 同意 | `yes` | ask 用户同意 |
| 拒绝 | `no` | ask 用户拒绝 |

---

## 四、MCP 工具列表

### 4.1 核心工具（黄金路径）

| 工具 | 用途 | 参数 |
|------|------|------|
| `af_draft` | 意图 JSON → IR，返回 ref | `intent` (object, 必填) |
| `af_apply` | 合并 build+simulate+save | `ref` (string), `stage` (check/simulate/save) |
| `af_resolve_entity` | 中文实体名 → entity_id | `name` (string) |

### 4.2 辅助工具

| 工具 | 用途 |
|------|------|
| `af_catalog` | 实体目录（列出所有设备） |
| `af_list_entities` | 列出实体 |
| `af_get_entity_state` | 获取实体状态 |
| `af_remember_entity` | 记住实体映射 |
| `af_build` | 编译安全闸（旧接口） |
| `af_compile_spec` | AF-Spec 文本 → IR（旧接口） |
| `af_simulate` | 仿真回放（旧接口） |
| `af_list_graphs` | 列出已保存的自动化 |
| `af_get_graph` | 获取自动化详情 |
| `af_health` | 健康检查 |
| `af_live_run` | 一次性真机下发 |

### 4.3 af_draft intent JSON 格式

```json
{
  "name": "自动化名称",
  "mode": "restart",
  "when": {
    "type": "state",
    "entity": "binary_sensor.xxx",
    "to": "on"
  },
  "do": {
    "action": "开灯",
    "target": "light.xxx"
  },
  "if": {
    "gt": {"var": "sensor.xxx", "const": 26}
  },
  "ask": {
    "prompt": "要关灯吗？",
    "timeout": "30s"
  },
  "wait": "5m"
}
```

### 4.4 触发类型

| type | 说明 | 例子 |
|------|------|------|
| `state` | 状态变化 | `{"type": "state", "entity": "binary_sensor.xxx", "to": "on"}` |
| `time` | 定时 | `{"type": "time", "at": "08:00"}` |
| `sun` | 日出日落 | `{"type": "sun", "event": "sunset", "offset": -1800}` |
| `event` | 自定义事件 | `{"type": "event", "event_type": "my_event"}` |

### 4.5 动作映射

| 中文 | 英文 | HA 服务 |
|------|------|---------|
| 开灯 | turn_on | light.turn_on |
| 关灯 | turn_off | light.turn_off |
| 开空调 | - | climate.turn_on |
| 关空调 | - | climate.turn_off |

---

## 五、部署配置

### 5.1 NAS 部署

| 项 | 值 |
|----|-----|
| docker 容器 | autoforge-api |
| 镜像 | autoforge-api:nonroot |
| 源码挂卷 | /vol1/1000/docker/autoforge/src:/app/src |
| 数据路径 | /data（GraphStore + pending + 持久化） |
| git 分支 | master |
| git remote | nas → ssh://lidicn@192.168.2.200/vol1/1000/git/autoforge.git |

### 5.2 网络

| 服务 | 地址 |
|------|------|
| AF MCP | http://192.168.2.200:8787/mcp |
| AF API | http://192.168.2.200:8787/api |
| AF Swagger | http://192.168.2.200:8787/docs |
| HA | http://192.168.2.200:8123 |

### 5.3 环境变量

| 变量 | 值 | 说明 |
|------|-----|------|
| AUTOFORGE_HA_URL | http://192.168.2.200:8123 | HA 地址 |
| AUTOFORGE_HA_TOKEN | eyJhbGci... | HA 长期令牌 |
| AUTOFORGE_LIVE_ENABLED | 1 | live mode 开关 |
| AUTOFORGE_RATE_LIMIT_PER_MIN | 100000 | 限流（已放开） |
| AF_REQUIRE_AUTH | 1 | 强制鉴权（P0-9 修复） |

### 5.4 MCP Token

```
af_X1nLv_NiiuD80yM0wrKJiYLDEJs6NKAT
```

---

## 六、关键实体映射

| 中文名 | entity_id |
|-------|-----------|
| 防盗门 | binary_sensor.0x00158d0001f34db6_contact |
| 木门 | binary_sensor.0x00158d0000d6de14_contact |
| 房间门 | binary_sensor.0x00158d0001a2237a_contact |
| 书房人体 | binary_sensor.0x00158d0001a2520d_motion |
| 卫生间存在 | binary_sensor.649e314cdeeb_occupancy |
| 客厅存在 | binary_sensor.649e3151e45f_occupancy |
| 显示器挂灯 | light.yeelink_cn_555003624_lamp22_s_2 |
| 客厅灯 | light.mijia_cn_group_1861372413196005378_group4_s_2_light |
| 书房温度 | sensor.duka_cn_blt_3_1orsfvt24cc01_th2_temperature_p_2_1001 |
| 书房空调 | climate.lumi_cn_84159632_v2 |
| 门锁 | lock.smart_lock |
| 客厅电视 | media_player.xiaomi_rmh1_6103_play_control |
| 客厅人数 | sensor.xiaomi_cn_820783783_p1_people_num_p_3_12 |

---

## 七、三种模式对比

| 模式 | 命令/参数 | 做了什么 | 碰真机 | 入队 |
|------|----------|---------|--------|------|
| **check** | `af_apply(ref, stage="check")` | 只过 build 安全闸 | ❌ | ❌ |
| **simulate** | `af_apply(ref, stage="simulate")` | build + 仿真回放 | ❌ | ❌ |
| **save** | `af_apply(ref, stage="save")` | build + simulate + 入待批队列 | ❌ | ✅ |
| **watch** | `forge watch <ir.json> --confirm` | 常驻监听 HA 事件，实时驱动 | ✅ | - |

---

## 八、安全闸（StaticScanner）

### 8.1 检查项

| 检查 | 说明 |
|------|------|
| IR_SCHEMA | IR 格式校验 |
| NO_ACTION | 无 do 节点 |
| NO_TRIGGER | 无 on 节点 |
| ENTITY_NOT_FOUND | 引用不存在的实体 |
| TARGET_UNRESOLVED | target 无法解析 |
| DEPENDENCY_CYCLE | 依赖环检测（deps + emit_deps union） |
| L2_NEEDS_CONFIRM | 高风险操作需确认 |
| DEVICE_ACL_DENIED | 设备 ACL 拒绝 |
| HIGH_RISK_BLOCKED | 高风险设备拦截 |
| ENTITY_DEP_CYCLE | 实体依赖成环 |

### 8.2 高风险设备

- lock（门锁）
- water_heater（热水器）
- climate（空调）— L2 需确认

---

## 九、实测验证（2026-09-24）

### 9.1 端到端实测

**用例**：防盗门打开 → 开客厅灯

**步骤**：
1. `af_draft(intent)` → ref
2. `af_apply(ref, stage="save")` → pending id
3. CLI approve → 保存到 GraphStore
4. 导出 IR JSON → /tmp/test_ir.json
5. 启动 `forge watch /tmp/test_ir.json --confirm`
6. 手动设置防盗门状态为 on
7. watch 收到事件 → 触发自动化 → 客厅灯真的开了 ✅

**日志**：
```
[FIRE] binary_sensor.0x00158d0001f34db6_contact=on → 实例 31b1a314111b
  · light.mijia_cn_group_1861372413196005378_group4_s_2_light = on
```

**结论**：端到端链路完全打通。

### 9.2 全量回归基线

```
1107 passed, 10 skipped, 0 failed
```

---

## 十、已知问题与待办

### 10.1 架构缺口

1. **没有自动启动 watch daemon**：docker 里只跑了 `forge serve`，watch 需要手动启动
2. **IR 导出格式坑**：`graph_to_raw()` 返回数组，但 `forge watch` 期望 `{"automations": [...]}` 对象
3. **协调锁**：同一时刻只能一个 watcher，锁文件在 `/app/.forge/watch.lock`
4. **白名单**：`--live-allow` 显式列出可写实体
5. **webui 前端未构建**：dist 目录不存在，访问根路径 404

### 10.2 af_draft 限制

1. **entity 解析是占位**：`_resolve_entity()` 直接透传 entity_name，没接 af_catalog 做真实中文→entity_id 解析
2. **动作映射有限**：只有开灯/关灯/开空调/关空调 + 英文等价物
3. **复杂意图不支持**：多动作、多条件、嵌套逻辑

### 10.3 审计遗留问题

1. P0-9：无令牌时整站开放（fail-closed 未完全收口，AF_REQUIRE_AUTH 已加但需验证）
2. device_id/area_id 不展开
3. 没有 CI（.github/workflows 不存在）
4. P1-12：三套 StateProvider 语义矛盾
5. P1-21：docker 配置（已改 nonroot，但 token 仍在 env_file）

### 10.4 FFL 测试相关

1. **PowerShell JSON 序列化问题**：嵌套 JSON 用 PowerShell 调 MCP 会出错，必须用 Python
2. **待批队列上限 20 条**：跑 200 题用 stage="save" 会熔断，必须用 stage="simulate"
3. **FFL 翻译能力**：简单意图翻译正确，复杂意图（多条件/多动作）容易出错

---

## 十一、快速操作命令

### 11.1 MCP 调用

```bash
# draft
curl -X POST http://192.168.2.200:8787/mcp \
  -H "Authorization: Bearer af_X1nLv_NiiuD80yM0wrKJiYLDEJs6NKAT" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"af_draft","arguments":{"intent":{...}}}}'

# apply
curl -X POST http://192.168.2.200:8787/mcp \
  -H "Authorization: Bearer af_X1nLv_NiiuD80yM0wrKJiYLDEJs6NKAT" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"af_apply","arguments":{"ref":"af:xxx","stage":"simulate"}}}'
```

### 11.2 CLI 操作

```bash
# 列出待批
docker exec autoforge-api python -m autoforge.af_cli pending list --root /data

# 批准
docker exec autoforge-api python -m autoforge.af_cli pending approve --root /data <op_id>

# 拒绝
docker exec autoforge-api python -m autoforge.af_cli pending reject --root /data <op_id>

# 列出已保存
docker exec autoforge-api python -m autoforge.af_cli store log --root /data

# 启动 watch
docker exec -d autoforge-api python -m autoforge.af_cli watch /tmp/test_ir.json \
  --confirm --ha-url http://192.168.2.200:8123 \
  --live-allow light.mijia_cn_group_1861372413196005378_group4_s_2_light

# 全量测试
docker exec autoforge-api python -m pytest /app/tests -q
```

### 11.3 HA API

```bash
# 设置实体状态
curl -X POST http://192.168.2.200:8123/api/states/binary_sensor.xxx \
  -H "Authorization: Bearer <HA_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"state": "on"}'

# 开关灯
curl -X POST http://192.168.2.200:8123/api/services/light/turn_on \
  -H "Authorization: Bearer <HA_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"entity_id": "light.xxx"}'
```

### 11.4 Docker 操作

```bash
# 重启容器（注意：docker compose restart 不生效，用 docker restart）
docker restart autoforge-api

# 查看日志
docker logs autoforge-api --tail 50

# 进入容器
docker exec -it autoforge-api sh

# 删协调锁
docker exec autoforge-api rm -f /app/.forge/watch.lock
```

---

## 十二、开发规范

### 12.1 红线（KICKOFF.md §3）

- 不连 NR（Node-RED）
- 直连 HA
- 只留 vhass（虚拟 HA 仿真）
- fail-closed 默认拒绝
- 安全闸优先

### 12.2 验收标准

- py_compile 全过
- 单测通过（报 total/collectable/ran 三个数）
- 不修改"不许改"清单
- 零新依赖（只用标准库 + 现有 autoforge 模块）

### 12.3 工单追踪

- WO-AF-xxx：整改工单
- R-xx：回归测试
- ✅/⏳/🔄：可视状态

---

## 十三、MiMo 设计方案（MCP 交互优化）

### 13.1 核心思路

1. **不让 Agent 写 AF-Spec 文本**——Agent 只传意图 JSON（中文实体名+触发+动作）
2. **IR 永不回传**——用 ref 流转（staging 区 + TTL）
3. **工具面 24→8**（常驻 5），诊断类拆到第二个 MCP server
4. **校验+仿真+入队合并成一次 af_apply**
5. **Skill 三层化**（正文≤120行 + references/ + examples/）

### 13.2 效果

从 6-9 次调用/2-4k tokens 降到 1-2 次/0.3-0.8k tokens。

---

## 十四、文档索引

| 文档 | 路径 |
|------|------|
| 本文档 | docs/AF完整知识文档.md |
| 架构与运行时 | docs/AF完整架构与运行时说明.md |
| AF-Spec 语法 | docs/AF-Spec语法参考.md |
| MiMo MCP 优化方案 | docs/MiMo-MCP交互优化方案.md |
| FFL 测试提示词 | docs/FFL-200题测试提示词.md |
| 蓝图 | KICKOFF.md |
| 代码审计 | docs/代码审计报告_20260918.md |

---

## 十五、总结

**AF 已经跑通了完整的端到端链路**：
- ✅ 自然语言 → 意图 JSON → IR
- ✅ IR → 安全闸校验
- ✅ 安全闸 → 仿真回放
- ✅ 仿真 → 入待批队列
- ✅ 人工 approve → 保存到 GraphStore
- ✅ watch daemon → 订阅 HA 事件 → 真机执行
- ✅ 实测：防盗门触发 → 客厅灯真的开了
- ✅ 全量回归 1107 passed

**下一步**：
1. af_draft 实体解析接 af_catalog
2. watch daemon 加入 docker compose 自动启动
3. FFL 200 题用 stage="simulate" 正式跑
4. 补 CI
5. 构建 webui 前端
