# AutoForge 完整架构与运行时说明

> 本文档基于 2026-09-24 实测验证，不是理论设计。

## 一、一句话总结

**AF 是一个智能家居自动化的"设计+运行"一体化平台**：Agent 通过 MCP 接口设计自动化（自然语言→IR→安全闸→仿真→入队），approve 后由 `forge watch` 常驻运行时引擎订阅 HA 事件流，实时驱动自动化执行。

---

## 二、核心组件

### 2.1 两个进程

| 进程 | 命令 | 职责 | 碰真机吗 |
|------|------|------|---------|
| **HTTP/MCP 服务** | `forge serve --host 0.0.0.0 --port 8787 --store-root /data` | 设计/编译/仿真/MCP 接口 | ❌ 不碰真机 |
| **运行时 daemon** | `forge watch <ir.json> --confirm --ha-url ... --live-allow ...` | 常驻订阅 HA 事件流，实时驱动自动化 | ✅ 真操作 HA |

### 2.2 关键路径

```
E:\NAS\AutoForge\src\autoforge\
├── af_draft.py          # 意图 JSON → IR（新，MiMo 方案）
├── af_apply.py          # 合并 build+simulate+save（新，MiMo 方案）
├── af_mcp.py            # MCP 接口层（HTTP）
├── af_api.py            # HTTP REST API
├── af_cli.py            # 命令行工具
├── af_service.py        # 服务层（build/simulate/submit_pending/approve）
├── af_runtime.py        # Runtime 引擎（事件驱动）
├── af_store.py          # GraphStore 版本化存储
├── af_pending.py        # 待批队列
├── af_scanner.py        # 静态安全闸（StaticScanner）
├── af_ir/               # IR 模型与节点定义
└── af_adapters.py       # HA 适配器（HAAdapter/HTTPAdapter/MockAdapter）
```

---

## 三、完整链路：从自然语言到真机执行

### 3.1 设计阶段（forge serve）

```
用户自然语言
    ↓
af_draft(intent JSON)     # 意图 → IR，返回 ref（不回传 IR 全文）
    ↓
af_apply(ref, stage="check")   # 只过安全闸（dry run）
    ↓
af_apply(ref, stage="simulate") # 安全闸 + 仿真回放（假事件）
    ↓
af_apply(ref, stage="save")    # 安全闸 + 仿真 + 入待批队列
    ↓
待批队列（/data/pending/*.json）
    ↓
人工 approve（CLI: pending approve --root /data <op_id>）
    ↓
保存到 GraphStore（版本化归档）
```

### 3.2 运行阶段（forge watch）

```
HA 设备状态变化
    ↓
HA SSE 事件流（/api/stream）
    ↓
forge watch 订阅事件
    ↓
Runtime 评估触发条件
    ↓
匹配到自动化 → 执行 do 节点
    ↓
HAAdapter 调用 HA 服务（light.turn_on 等）
    ↓
真机动作执行 ✅
```

---

## 四、三种模式对比

| 模式 | 命令 | 做了什么 | 碰真机吗 | 用途 |
|------|------|---------|---------|------|
| **check** | `af_apply(ref, stage="check")` | 只过 build 安全闸 | ❌ | 快速验证语法/安全 |
| **simulate** | `af_apply(ref, stage="simulate")` | 安全闸 + 假事件仿真 | ❌ | 逻辑验证 |
| **save** | `af_apply(ref, stage="save")` | 安全闸 + 仿真 + 入待批队列 | ⏳ 待 approve | 提交审核 |
| **watch** | `forge watch <ir.json> --confirm` | 常驻监听 HA 事件，实时驱动 | ✅ 真操作 | 生产运行 |

---

## 五、MCP 工具列表（当前已注册）

| 工具 | 用途 |
|------|------|
| `af_draft` | 意图 JSON → IR，返回 ref |
| `af_apply` | 合并 build+simulate+save |
| `af_resolve_entity` | 中文实体名 → entity_id |
| `af_remember_entity` | 记住实体映射 |
| `af_list_entities` | 列出实体 |
| `af_catalog` | 实体目录 |
| `af_build` | 编译安全闸（旧接口） |
| `af_compile_spec` | AF-Spec 文本 → IR（旧接口） |
| `af_simulate` | 仿真回放（旧接口） |
| `af_list_graphs` | 列出已保存的自动化 |
| `af_get_graph` | 获取自动化详情 |
| `af_health` | 健康检查 |
| `af_live_run` | 一次性真机下发 |

---

## 六、关键配置

### 6.1 NAS 部署

- **docker 容器**：autoforge-api
- **源码路径**：/vol1/1000/docker/autoforge/src:/app/src（挂卷）
- **数据路径**：/data（GraphStore + pending + 持久化）
- **git 分支**：master
- **MCP 地址**：http://192.168.2.200:8787/mcp
- **HA 地址**：http://192.168.2.200:8123

### 6.2 环境变量

| 变量 | 值 | 说明 |
|------|-----|------|
| AUTOFORGE_HA_URL | http://192.168.2.200:8123 | HA 地址 |
| AUTOFORGE_HA_TOKEN | eyJhbGci... | HA 长期令牌 |
| AUTOFORGE_LIVE_ENABLED | 1 | live mode 开关 |
| AUTOFORGE_RATE_LIMIT_PER_MIN | 100000 | 限流（已放开） |

---

## 七、实测验证（2026-09-24）

### 7.1 测试用例

- **自动化**：防盗门打开 → 开客厅灯
- **触发实体**：binary_sensor.0x00158d0001f34db6_contact
- **动作实体**：light.mijia_cn_group_1861372413196005378_group4_s_2_light

### 7.2 测试步骤

1. `af_draft(intent)` → 返回 ref `af:xxx`
2. `af_apply(ref, stage="save")` → 返回 pending id `b5021eed87b24a44`
3. CLI approve → 保存到 GraphStore
4. 导出 IR JSON → `/tmp/test_ir.json`
5. 启动 `forge watch /tmp/test_ir.json --confirm`
6. 手动设置防盗门状态为 on
7. watch 收到事件 → 触发自动化 → 客厅灯真的开了 ✅

### 7.3 实测结果

```
[FIRE] binary_sensor.0x00158d0001f34db6_contact=on → 实例 31b1a314111b
  · light.mijia_cn_group_1861372413196005378_group4_s_2_light = on
```

**客厅灯真的从 off 变成了 on。端到端链路完全打通。**

---

## 八、已知问题与待办

### 8.1 架构缺口

1. **没有自动启动 watch daemon**：docker 里只跑了 `forge serve`，watch 需要手动启动
2. **IR 导出格式坑**：`graph_to_raw()` 返回数组，但 `forge watch` 期望 `{"automations": [...]}` 对象
3. **协调锁**：同一时刻只能一个 watcher 运行，锁文件在 `/app/.forge/watch.lock`
4. **白名单**：`--live-allow` 显式列出可写实体，不在白名单的实体不会被操作

### 8.2 FFL 测试相关

1. **PowerShell JSON 序列化问题**：嵌套 JSON 用 PowerShell 调 MCP 会出错，必须用 Python
2. **中英文动作映射**：af_draft 支持中文（开灯/关灯）和英文（turn_on/turn_off）
3. **entity 解析是占位**：af_draft 的 `_resolve_entity()` 目前直接透传 entity_name，没有接 af_catalog 做真实中文→entity_id 解析

### 8.3 审计遗留问题

1. P0-9：无令牌时整站开放（fail-closed 未完全收口）
2. P1-4：跨自动化成环漏检（deps+emit_deps union 已修）
3. 没有 CI（.github/workflows 不存在）
4. device_id/area_id 不展开

---

## 九、快速操作命令

### 9.1 MCP 调用

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
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"af_apply","arguments":{"ref":"af:xxx","stage":"save"}}}'
```

### 9.2 CLI 操作

```bash
# 列出待批
docker exec autoforge-api python -m autoforge.af_cli pending list --root /data

# 批准
docker exec autoforge-api python -m autoforge.af_cli pending approve --root /data <op_id>

# 列出已保存
docker exec autoforge-api python -m autoforge.af_cli store log --root /data

# 启动 watch
docker exec -d autoforge-api python -m autoforge.af_cli watch /tmp/test_ir.json --confirm --ha-url http://192.168.2.200:8123 --live-allow light.xxx
```

### 9.3 HA API

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

---

## 十、总结

**AF 已经跑通了完整的端到端链路**：
- ✅ 自然语言 → 意图 JSON → IR
- ✅ IR → 安全闸校验
- ✅ 安全闸 → 仿真回放
- ✅ 仿真 → 入待批队列
- ✅ 人工 approve → 保存到 GraphStore
- ✅ watch daemon → 订阅 HA 事件 → 真机执行
- ✅ 实测：防盗门触发 → 客厅灯真的开了

**下一步要做的**：
1. 把 watch daemon 加入 docker compose 自动启动
2. 修复 af_draft 的 entity 解析（接 af_catalog）
3. 补全 MCP 工具注册（af_draft/af_apply 已注册）
4. FFL 200 题正式跑一轮
