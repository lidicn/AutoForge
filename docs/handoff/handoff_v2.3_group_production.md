# 交接卡：v2.3/F9 group 复合自动化 —— 生产 `apply(ref)` 接入与 NAS 部署

日期：2026-09-28　提交：`3a4faf5`（生产路径接入）→ `3c4f482`（版本语义修正）

## 1. 变更清单（文件级）

| 文件 | 改动 |
|---|---|
| `src/autoforge/af_apply.py` | `apply()` 在 premiere 闸门之后检测到 `mode='group'` 即**短路**到 `apply_group`（原子部署），不再走单自动化的 build/simulate/submit 分支 |
| `src/autoforge/af_draft.py` | 新增 `stage_group()`：group 以**单自动化 Graph** 形式进入既有 staging 管线（零模型改动） |
| `src/autoforge/af_orchestrator.py` | `compose_group()` wrapper 的 `mode` 由 `single` **修正为 `group`**；默认 `ir_version` 改用 `GROUP_IR_VERSION`（0.3.0） |
| `src/autoforge/af_ir/models.py` | 新增单一真值源常量 `GROUP_IR_VERSION = "0.3.0"`（与 `SUPPORTED_IR_VERSIONS` / schema 的 `ir_version.enum` 同源），加入 `__all__` |
| `src/autoforge/af_ir/__init__.py` | 导出 `GROUP_IR_VERSION` |
| `src/autoforge/af_ir/schema/ir.schema.json` | 顶层 `automation.mode` 枚举新增 `"group"`（仅顶层；`child_automation` 未放开，避免静默启用未支持的嵌套 group） |
| `tests/test_af_ir_group_apply.py` | +3 项：生产路径 `apply(ref)` 原子部署 / 冲突预检告警随结果返回 / group IR 版本语义（`mode='group'` 且 `ir_version=='0.3.0'`） |

## 2. 行为变化（delta）

- **生产路径**：`apply(ref)` 对 group IR 走原子部署 —— 全量仿真，任一子自动化失败即整体 `ok=False` 且**不入队**（无半部署）；全量通过才依次入待批队列，统一打 `group_ref`（单 ref 回滚单位）。
- **IR 自描述**：group 容器节点的 `mode` 此前被误标为 `single`，导致 IR 不自描述、`apply` 无法识别、序列化不自洽 —— 已修正为 `group`。
- **版本号诚实**：group IR 此前打 `0.2.1`，而 group 按决策 D 落地于 `0.3.0`（用旧版本号承载新能力，旧消费者无法理解 `mode='group'`）—— 已改为 `0.3.0`。
- **冲突语义**：跨自动化冲突预检是**告警**（随 `apply` 结果返回），**不是硬闸**（DCD 裁定 §7.3）。冲突组仍会原子部署。

## 3. 如何验证

- 本地全量离线门：`python -m pytest tests -q` → **1247 passed / 51 skipped / EXIT=0**。
- group 专项：`pytest tests/test_af_ir_group_apply.py tests/test_af_ir_group.py -q` → **14 passed**。
- NAS 生产真机冒烟（`docker exec` 内跑，用 `stage="simulate"` **不写待批队列**、不污染生产库）：
  ```
  GROUP_MODE: group          STAMPED_IR_VERSION: 0.3.0
  ROUTED_TO_APPLY_GROUP: True   OK: True
  CHILDREN: ['smoke_off','smoke_lock']   DEPLOYED: []   ← simulate 不入队
  CONFLICT_REPORTED: 1  （light.smoke 相反操作 off/on）
  SMOKE_OK: True
  ```
- 服务健康：`GET /api/health` → `{"ok":true,"store_ok":true,"readonly":true}`。

## 4. 部署步骤（NAS，image-bake）

```
cd /vol1/1000/docker/autoforge
git fetch origin && git checkout -f -B master origin/master   # → 3c4f482
docker tag autoforge-api:latest autoforge-api:pre-group-backup  # 回滚点（已保留）
docker compose -f docker/docker-compose.api.yml build
docker compose -f docker/docker-compose.api.yml up -d
```

**回滚**：`docker tag autoforge-api:pre-group-backup autoforge-api:latest && docker compose -f docker/docker-compose.api.yml up -d`

## 5. 已知风险与注意事项

1. **冲突是告警非阻断**：若需要「冲突即禁止部署」的更强护栏，属另一次语义变更，需单独决策（当前按 DCD §7.3 为告警）。
2. **子自动化必须带 `ir_version`**：`apply_group` 的仿真把每个子 IR 当**顶层 IR** 校验（走 `load_graph` → `Automation.from_dict`，要求 `ir_version`）。单测里 `af_service.simulate` 被 mock，**未暴露**此约束，真机冒烟才发现。调用方传入的 children 必须是合法完整 IR。
3. **`compose_group` 不补全 children 的 `ir_version`**：若传入手写 dict 缺该字段，会在仿真期报错（已在冒烟验证脚本中确认补上即可通过）。是否要 `compose_group` 自动补全，待定。
4. **部署为 image-bake**：compose 已注释掉宿主源码挂载（`v2.0.1` 基线收口），**改后端必须重建镜像**，改代码后仅 `docker restart` 不生效。
5. **真机 submit 路径仅单测覆盖**：生产冒烟为免污染待批队列只跑到 `simulate`；真实入队由单测（mock `submit_pending`）保证。

## 6. 合并影响（重要修正）

**此前基于旧记忆的「WIP↔v2.1 分叉风险」是误报**：`git merge-base --is-ancestor 4271dec master` 为真 —— `master` 是 v2.1 的**干净延伸**（4271dec 为其祖先），并非分叉。真正的分叉只存在于旧分支 `wip/af-draft-apply-20260927`，而该分支的工作早已并入 `master`。因此**无需额外调和即可部署**，已验证。
