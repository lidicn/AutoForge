# 交接卡：v2.4/F13 af_evo 提案真 IR 内联与 band 口径

日期：2026-09-29　本地改动：`src/autoforge/af_evo.py` + 测试（未提交，待窗口部署）

## 1. 变更清单（文件级）

| 文件 | 改动 |
|---|---|
| `src/autoforge/af_evo.py` | ① `EvoPolicy.require_shadow_band` 默认 `False`→`True`（`af_evo.py:481`），`_detect_promote` 在 `_band_of(aid) != "shadow"` 时跳过（对齐 G4 band 闸门口径）；② 新增 `_clean_node_id` / `_clean_trigger` / `_ir_from_automation_ir` helper；③ `default_ir_builder` 重写，直接产出合规 IR 文档（v0.3.0），不再包 graph-delta 信封 |
| `tests/test_af_evo_strategies.py` | 既有 43 项同步：导入 `EvoPolicy`；9 处 envelope 断言改为真 IR 提取（helper `ir_meta/ir_do/ir_on/ir_children`）；id 断言 `auto.a`→`auto_a`、split children `auto.big`/`auto.big#part2`→`auto_big`/`auto_big_part2`；4 处 promote 测试显式 `EvoPolicy(require_shadow_band=False)` 保留原逻辑验证 |
| `tests/test_af_evo_f13.py` | 新增 4 项：require_shadow_band 默认 True / merge 合规 IR / adjust+fallback+promote 合规 IR / split group IR（均经 `validate_automation` 校验 + 节点 id 正则） |

## 2. 行为变化（delta）

- **F13②（升档闸门）**：`af_evo` 的 `promote_shadow` 策略默认要求目标自动化处于 `shadow` band（由注入的 `executor_stats.band()` 判定，与 G4 `af_conf.ConfidenceStore` 同一档位口径）才允许升档提案。此前默认 `False`，evo 升档可绕过人审档位。装配层须确保给 `EvoScanner` 注入带 `band()` 口径的 `executor_stats`，否则所有 promote 提案被静默跳过（进 `warnings`）。
- **F13①（提案即真 IR）**：`ProposalManager.build_ir` 收到的 `suggested_ir` 不再是 `{strategy, graph, revision_of, remove, ...}` 信封，而是：
  - 单自动化 → 完整 IR 文档（`ir_version:"0.3.0"` + `nodes/edges` + `meta._evo` 审计）；
  - 多自动化（split）→ 顶层 `mode:"single"` + 单个 `kind:"group"` 节点，其 `children` 为各 part 合规 IR（`root_key="child_automation"` 可校验）。
  - `on` 节点带 `trigger`（字段经白名单裁剪、`kind`→`type` 映射）；`do` 节点补 `adapter:"homeassistant"`（schema 必需）。所有节点 id 经 `_clean_node_id` 清洗匹配 `^[a-z][a-z0-9_]*$`。
  - 消费方改为 `return dict(suggested_ir)` 即可，无需再解信封。
  - **附带加固**：`_ir_from_automation_ir` 容忍 `trigger`/`do` 为单条 dict（不仅 list），使 `default_ir_builder` 可脱离 `EvoScanner` 单独调用（`EvoScanner` 经 `view.automation_ir` 已归一化为 list，此为防御性兼容）。

## 3. 如何验证

- evo 专项：`python -m pytest tests/test_af_evo_strategies.py tests/test_af_evo_f13.py -q` → **48 passed**。
- ir/evo 广域：`python -m pytest tests/ -q -k "ir or evo"` → **217 passed / 2 skipped / EXIT=0**（含 `validate_automation` 对真 IR 与 group 子节点的 schema 校验）。
- NAS 生产真机冒烟（2026-09-29 部署后，`docker exec autoforge-api python3 /tmp/f13_smoke.py` 驱动 EvoScanner + 直接 `default_ir_builder`，确认 `suggested_ir` 为合规 IR、`validate_automation` 通过、节点 id 清洗、promote 受 shadow band 约束）：
  ```
  F13_2_REQUIRE_SHADOW_DEFAULT_TRUE
  MERGE_COMPLIANT_IR_OK auto_a ['auto_a__on1', 'n1', 'n2']
  SPLIT_GROUP_IR_OK 2
  BUILDER_DIRECT_OK
  EVO_SMOKE_OK
  ```
- 服务健康：`GET /api/health` → `{"ok":true,"readonly":true,"store_ok":true}`。

## 4. 部署步骤（NAS，image-bake）

```
cd /vol1/1000/docker/autoforge
docker tag autoforge-api:latest autoforge-api:pre-f13-backup   # 回滚点
# 仅后端改动，image-bake：把本地 src/autoforge/af_evo.py 与两测试文件 scp 到 /src 后重建
docker compose -f docker/docker-compose.api.yml build autoforge-api
docker compose -f docker/docker-compose.api.yml up -d
```

**回滚**：`docker tag autoforge-api:pre-f13-backup autoforge-api:latest && docker compose -f docker/docker-compose.api.yml up -d`

## 5. 已知风险与注意事项

1. **装配层必须注入 band 口径**：F13② 使 promote 默认依赖 `executor_stats.band()`。若现网 `executor_stats` 未实现 `band/status/mode` callable，`_band_of` 恒返回 `None` → 所有 `promote_shadow` 提案被跳过（fail-open，不报错但功能静默失效）。需确认 G4 `ConfidenceStore` 的 band 出口已接到 evo 的 `executor_stats`。
2. **消费方解信封代码需同步**：任何从 `EvoProposal.suggested_ir` 读取 `graph/revision_of/remove` 信封字段的代码都必须改为读 `meta._evo`。当前仓库内 `ProposalManager.build_ir` 与测试已同步；若另有下游脚本直接解析旧信封，会静默拿不到数据。
3. **节点 id 形态变更**：真 IR 节点 id 已清洗（`auto.a`→`auto_a`、含 `#`/`#part2` 的拆分子节点→`auto_big_part2`）。若下游按旧 id 形态做匹配/索引，需同步。
4. **部署为 image-bake**：compose 已注释掉宿主源码挂载（v2.0.1 基线收口），改后端必须重建镜像，`docker restart` 不生效。
5. **本地 WIP 分叉警示**：本机 `e:/NAS/AutoForge/src/autoforge/af_canary.py` 是 WIP 重构版（无 `CanarySupervisor`/`CanaryPolicy`），与 NAS 上的 v2.1 基线（`af_canary` 含这些符号）分叉。**从本地部署 F13 只 scp `af_evo.py` + 测试到 NAS `/src`，切勿把本地 WIP 的 `af_canary.py`/`af_runtime.py` 整文件覆盖到 NAS**，否则会引入分叉。

## 6. 合并影响

- 纯新增 + 单文件行为变更（`af_evo.py`），未触及 `af_apply`/`af_draft`/`af_orchestrator` 等生产下发路径；IR 消费闭环仍由 `ProposalManager` 承接，与 F9 group 生产路径正交。
- 测试同步更新保证零回归（48 evo + 217 ir/evo 全过）。
- 未 git commit（用户本轮仅说"继续推进"，未要求提交）。
