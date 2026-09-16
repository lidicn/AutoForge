# 交接卡 —— 服务层（Round 1 只读 API）

> 模板见 `docs/交接卡_模板.md`。契约见 `docs/API_CONTRACT.md`（权威形态：运行中的 `/openapi.json`）。
> 本交付对应《AutoForge-UI 开工令》（`D:\Documents\WorkSpace\Test\AutoForge-UI开工令.md`）附录 A。

## 0. 元信息

| 项 | 值 |
|---|---|
| 范围 | AutoForge 只读服务层（FastAPI，11 个端点）+ 容器化部署 |
| 完成日期 | 2026-09-14 |
| 状态 | ✅ 已交付并 NAS 实测 |
| 验收 | 本机 **202 passed / 5 skipped**；NAS 容器内端点自测 **13/13 PASS**；局域网 `http://192.168.2.200:8787/api/health` 可达 |

## 1. 文件清单

| 文件 | 类型 | 说明 |
|---|---|---|
| `src/autoforge/af_service.py` | [NEW] | 服务层纯逻辑（无 Web 依赖）：health/graphs/build/sim/conf/intervene/diff/spec/faults/bootstrap |
| `src/autoforge/af_api.py` | [NEW] | FastAPI 路由层（CORS、错误码映射、Pydantic 请求体） |
| `src/autoforge/af_cli.py` | [MODIFY] | 新增 `forge serve`（懒加载 uvicorn/fastapi） |
| `src/autoforge/af_fault.py` | [MODIFY] | 新增 `FAULT_META`（五类故障的展示元数据，供 `/api/faults`） |
| `pyproject.toml` | [MODIFY] | 新增可选依赖 `api = [fastapi, uvicorn]`；dev 增 fastapi/uvicorn/httpx |
| `docker/Dockerfile.api` | [NEW] | 服务层镜像（仅 COPY pyproject/src/examples，暴露 8787） |
| `docker/docker-compose.api.yml` | [NEW] | NAS 部署：`8787:8787`，归档挂 `/vol1/1000/docker/autoforge-store:/data` |
| `docs/API_CONTRACT.md` | [NEW] | 契约速查表（权威形态为 `/openapi.json`） |
| `tests/unit/test_af_api.py` | [NEW] | 12 条 TestClient 用例（未装 fastapi 时整文件 skip） |

## 2. 行为增量

- **新增能力**：`forge serve` 启动**只读** HTTP 服务；11 个端点覆盖 UI 附录 A 契约。
- **数据来源**：G6 归档存储（`GraphStore`，`--store-root`）；启动时把 `examples/ir` **幂等灌入**归档，控制台一开即有数据。
- **复用内核**：`af_scanner`（扫描/诊断码）、`af_runtime`+`af_vhass`（本地 FakeHA 仿真回放）、`af_nl`（确定性 NL）、`af_conf`（分级与阈值）、`af_store`（版本/diff/置信度持久化）、`af_spec`（AF-Spec）、`af_fault`（故障图鉴）。
- **对外契约**：新增 `serve` 子命令与可选依赖 `.[api]`；新增容器 `autoforge-api` 与端口 **8787**。无 IR Schema 改动。
- **只读保证**：无 HA 连接、无令牌、无设备写入；`/sim` 只在本地 FakeHA 内运行。

### 契约增量（2026-09-14，前端联调修正）

Round 1 前端（FFL 交付）联调时发现 4 处契约可用性问题，已按"后端向前端友好形状收敛"修复并重新部署：

| 端点 | 增量 |
|---|---|
| `GET /api/graphs` | `items[]` 增 `mode`（最新版本；多自动化=`multi`）与 `automation_ids[]`（归档名 → 自动化 id 映射） |
| `GET /api/conf/{name}` / `POST .../intervene` | 支持 `_all` / `all` 聚合视图（intervene 自动定位归档） |
| `GET /api/diff` | `structured` 由字符串改**对象数组**（`{node_id,address,automation_id,node}` / `{from,to,kind,...}` / `meta_changes{key,old_value,new_value}`） |
| `GET /api/faults` | `failures[].recoverable` 由 `"yes"/"no"` 改**布尔** |

> 契约权威仍为 `GET /openapi.json`；速查表 `docs/API_CONTRACT.md` 已同步。

### Round 2 增量（2026-09-14）：ask 审批会话 + 真机下发闸门

| 端点组 | 内容 |
|---|---|
| `/api/sessions`（POST/GET）、`/api/sessions/{sid}`（GET/DELETE）、`.../answer`、`.../tick`、`.../cancel` | 进程内**会话**：`ask` 的"挂起 → 人工应答 → 继续"跨请求状态；`tick` 推进虚拟时钟演示 `on_timeout`；TTL 默认 3600s |
| `/api/live/status`、`/api/live/run` | **真机下发**（三重闸：服务端开关 + 服务端令牌 + 请求体 confirm/白名单，IR 写目标 ⊆ 白名单） |

- 新增 `af_service.ServiceError(message, status)`，HTTP 层统一映射状态码。
- `LIVE_TRANSPORT_FACTORY` 为测试注入点（默认用真实 `HATransport`）。
- NAS 实测：`asks=1(room=study)` → 应答"好"→ `climate.study=cool`；`live/run` 未启用 → 403；`live/status` 列出两条闸门原因。
- ⚠️ 会话为内存态（重启丢失）；服务层无鉴权（原型期，仅局域网）。

## 3. 验证方式

```powershell
# 本机
.\.venv314\Scripts\python.exe -m pip install -e ".[api]"
.\.venv314\Scripts\python.exe -m pytest tests/unit/test_af_api.py -q    # 12 passed
.\.venv314\Scripts\python.exe -m pytest tests/ -q                        # 202 passed / 5 skipped
forge serve --port 8787 --store-root .forge --examples examples/ir       # /docs 可开
```

```bash
# NAS
cd /vol1/1000/docker/autoforge
docker compose -f docker/docker-compose.api.yml up -d --build
docker exec autoforge-api python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8787/api/health').read().decode())"
# 局域网：curl http://192.168.2.200:8787/api/health
```

**实测结果**（容器内 13/13 PASS）：health / graphs(n=8) / graph.detail / build(ok) / build(L3 拒绝) / sim(light 变 on) / conf(auto, 阈值 0.85-0.60) / intervene(1.0→0.75) / diff / spec.render / spec.compile / faults(5+4) / openapi.json。

## 4. 已知风险 / 残留项

- [ ] **CORS 全开**（`allow_origins=["*"]`）——原型期为本地前端联调方便；上线前应收敛为具体来源。
- [ ] **归档无并发锁**：`GraphStore` 为文件级存储（同 G6）；多写者需加锁（P1）。
- [ ] `fastapi/uvicorn` 为**可选依赖**：未安装时 `test_af_api.py` 整文件 skip、`forge serve` 给出安装提示——内核测试不受影响。
- [ ] **Round 2 未实现**：ask 审批、真机下发（`/api/live/run`）——前端先做 disabled 占位。真机下发必须沿用 `live_preflight`（白名单 + 二次确认）。
- [ ] 部署验证过程会在 `/data` 留下**演示数据**（case01 的 v2 与 conf 干预记录 0.75）——属演示态，可按需清空 `/vol1/1000/docker/autoforge-store`。

## 5. 合并影响

- 既有 CLI 与测试不受影响（新增 `serve` 与可选依赖）。
- 同步更新：`docs/ROADMAP.md`、`README.md`（CLI 总览、目录、文档索引）。
- 部署：新增容器 `autoforge-api`（端口 8787）与持久目录 `/vol1/1000/docker/autoforge-store`。
- 下游：前端 `AutoForge-UI`（FFL）可对 `http://192.168.2.200:8787` 或本地 Mock 开发；契约变更须同步 `docs/API_CONTRACT.md` 与前端 Mock。

## 6. 回归基线（本里程碑起算）

- 本机：202 passed / 5 skipped / 0 failed
- NAS 真 vhass（测试镜像）：195 passed（G7 时基线，本次未改动内核测试路径）
- NAS 服务层容器：13/13 端点自测 PASS
