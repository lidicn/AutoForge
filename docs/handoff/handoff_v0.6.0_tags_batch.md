# 交接单：v0.6.0 标签体系与批量启停

- 日期：2026-09-15
- 范围：AutoForge 标签治理（GraphStore tags + 自动化 `enabled` 开关 + CLI/API 批量启停）
- 状态：🟢 已交付，本机 278 passed / 10 skipped 全绿

---

## 1. 背景

此前自动化只能逐条管理。v0.6.0 引入「按组治理」：给归档打标签，再按标签一次性启用/禁用其下全部自动化。

## 2. 改动清单（files touched）

| 文件 | 改动 |
|---|---|
| `src/autoforge/af_ir/schema/ir.schema.json` | `automation` 增加 `enabled` 属性（boolean，默认 true） |
| `src/autoforge/af_ir/models.py` | `Automation` 加 `enabled: bool = True`；`from_dict` 读取并显式回写 `raw["enabled"]`（保证 round-trip 一致） |
| `src/autoforge/af_scheduler.py` | `handle_event` 遍历自动化时 `if not auto.enabled: continue`；`_drain_queues` 同样跳过禁用项（静默跳过，非拒绝） |
| `src/autoforge/af_store.py` | 侧车索引 `{root}/tags.json`：`set_tags`/`get_tags`/`all_tags`；`save` 支持 `tags=` 参数 |
| `src/autoforge/af_service.py` | `list_graphs` 每项带 `tags`；新增 `set_graph_tags` / `graphs_by_tag` / `enable_by_tag` |
| `src/autoforge/af_cli.py` | `forge store tag <name> --tag x` / `forge store tags` / `forge store enable --tag x` / `forge store disable --tag x` |
| `src/autoforge/af_api.py` | `GET /api/graphs?tag=` 过滤；`POST /api/graphs/tags`、`/enable`、`/disable`；模块 docstring 同步契约 |
| `tests/unit/test_v0_6_tags.py` | 新增 9 条单测（模型 / 调度器跳过 / store / 服务层 / API） |

## 3. 行为变化（behavior delta）

- **新增 `enabled` 开关**：每个自动化可 `enabled: false`，运行时完全忽略（不注册触发、不派发实例、不进队列）。默认 `true`，对存量 IR 透明。
- **标签元数据**：归档名 ↔ 标签列表（侧车 `tags.json`，覆盖式设置，空列表=清空）。
- **按标签批量启停**：`enable_by_tag`/`disable` 对标签下每个归档的最新版本，翻转其中全部自动化的 `enabled` 并保存**新版本**（留痕，可回滚到旧版本）。
- **API 过滤**：`GET /api/graphs?tag=lighting` 仅返回带该标签的归档；列表项含 `tags` 字段。
- 写端点（`/tags`、`/enable`、`/disable`）与既有写端点一致：仅当服务端设置 `AUTOFORGE_API_TOKEN` 才强制 Bearer 鉴权（v0.8.0 才做完整鉴权升级）。

## 4. 如何验证（how to verify）

```bash
# 本机单测
python -m pytest tests/unit/test_v0_6_tags.py -q
# 期望：9 passed

# CLI 冒烟
python -m autoforge.af_cli store save examples/ir/case_lamp_sync.json --name demo
python -m autoforge.af_cli store tag demo --tag lighting
python -m autoforge.af_cli store disable --tag lighting   # demo 下全部自动化转 disabled
python -m autoforge.af_cli store enable  --tag lighting   # 恢复

# API 冒烟（需 fastapi）
# POST /api/graphs/disable {"tag":"lighting"} → 该标签归档最新版本自动化全部 enabled=false
# GET  /api/graphs?tag=lighting → 仅返回 demo
```

NAS 双环境：源码 scp 至 `/vol1/1000/docker/autoforge/src/autoforge/` 后，容器内
`python -m pytest tests/unit/test_v0_6_tags.py -q`；或 `docker restart autoforge-api` 让运行服务加载新端点。

## 5. 已知风险（known risks）

1. **前端未随本版**：标签列/筛选/批量操作 UI 在 `Autoforge-UI` 独立仓库（不在本仓库），需随 UI 迭代；后端契约（`GET /api/graphs?tag=` + `tags`/`enable`/`disable` 三端点）已就绪。
2. **禁用不热生效于运行中的 `watch`**：`enabled` 在加载期生效。已运行的 `forge watch` 进程需重载（docker restart / 重新 `run`）才会应用新版本里的 `enabled` 位。这是 v0.6.0 预期范围（不做热重载）。
3. **批量启停落新版本**：每次 enable/disable 产生一个归档新版本。高频切换会累积版本号（无害，归档按版本可追溯）。
4. **`enabled` 与 `mode` 正交**：禁用优先于 mode/配额；禁用项既不触发也不占配额。

## 6. 合并影响（merge-impact）

- `ir.schema.json` 加属性（additionalProperties 已显式允许 `enabled`），旧 IR 文件无需改（默认 true）。
- `Automation.raw` 现显式含 `enabled`，任何 re-save 的归档都会带该字段（向后兼容）。
- 无破坏既有 API 契约；仅新增可选 `tag` 查询参数与 3 个写端点。
- 回归门：本仓库 `python -m pytest tests/ -q` 须全绿（278 passed / 10 skipped，含本版 9 条）；任何改 `af_store`/`af_service`/`af_api` 的 PR 须跑 `test_v0_6_tags.py`。

## 7. 附：并行修复的缺陷（v0.5.x 补丁）

迭代期间修复 EventBus 200ms 节流误杀真实状态跳变（`on↔off` 被当抖动丢弃），改为状态感知节流。属独立缺陷修复，详见 `docs/handoff_lamp_sync_throttle.md`。该修复已并入本机基线，不属 v0.6.0 功能范畴，但同批验证。
