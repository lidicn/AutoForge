# AutoForge 版本开发计划 · DCD 执行细案

> 出品：关键决策部（Directorate of Critical Decisions · DCD）
> 文档日期：2026-09-28
> 性质：对 `plan/版本开发计划.md` §四"待决策部交付物"的交付——PR 拆分、工时、依赖序、决策门采集、质量门
> 上游文档：`docs/roadmap/版本路线图.md`（唯一路线图）、`decisions/20260928-AutoForge-v2.1设计难题A-F-决策.md`

---

## §0 对开发者两份产出的审查结论

**结论：采纳，三处修正。**

1. **F9 表述方向反了**（路线图 :54、计划 :45）：写的是"前置 ir_version 枚举化 + `additionalProperties:false`"。正确表述：ir.schema.json 顶层当前**已是** `additionalProperties:false`，问题恰恰是它会导致旧校验器硬拒新图；前置工作是**对新增容器字段做前向兼容放宽**（新增字段白名单或改 `additionalProperties: true` + 未知字段忽略策略），不是保持 false。
2. **路径引用核实**：文中 `roadmap/ADM-路线图_v2.0投产.md`、`roadmap/演进路线图_AF_v2.1+.md` 与仓内实际位置（`doc/ADM-路线图-AF.md`、`docs/design/v2/演进路线图_AF_v2.1+.md`）不一致，请统一——唯一权威路线图自身引用的路径必须是真实路径。
3. **F6 落盘归属**：premiere 落盘我写的是 v2.1（作为自动暂停前置），开发者并入 v2.2 F6。**接受开发者的归并**（内聚性更好），但 F6 的 PR 序必须保证"落盘 PR 先于接闸 PR"合入。

---

## §1 PR 拆分总原则

1. **一个 PR 一个验收点**：每个 PR 独立可合入、有独立测试、revert 不牵连他人。
2. **测试随 PR 走**：无专项测试网的模块（shadow/canary/premiere/orchestrator），首个触碰它的 PR 必须补建该模块测试文件。
3. **schema 先行**：凡涉 IR/schema 变更，schema PR 单独先合（铁律 1），代码投影 PR 依赖它。
4. **行为开关后置**：改变默认行为的 PR（如冲突仲裁默认开）与功能实现 PR 分开，默认开启永远单独一个 PR。

---

## §2 v2.0.1 投产收口（PR 序，总 2.5-3 天）

| PR | 内容 | 工时 | 依赖 | 可并行 |
|----|------|------|------|--------|
| 0.1-a | 103 个未提交文件清点：分"提交/删除/入 gitignore"三类，逐批提交 | 1 天 | 无 | — |
| 0.1-b | token 移出 env_file 走 secret 管理 | 0.5 天 | 无 | 与 0.1-a 并行 |
| 0.1-c | P0-9 鉴权 fail-closed 修复 + 专项测试 | 0.5-1 天 | 0.1-a | 否 |
| 0.1-d | api 镜像烘入 + 两容器基线分叉收口验证 | 0.5 天 | 0.1-a/b/c | 否 |
| 0.2 | 文档鲜度：README/KICKOFF 529 passed、v1.7.1、ADM 剩余项同步 | 0.5 天 | 无 | 任意时点 |
| 0.3 | ui-user 冻结归档（README 标注 + 交接文档更新） | 0.5 天 | 无 | 任意时点 |

**出口**：gates.sh 退出码 0；`git status` 干净；两容器基线一致；投产+回滚文档齐备。

## §3 v2.1（总 6 天，9 个 PR）

| PR | 内容 | 工时 | 依赖 | 可并行 |
|----|------|------|------|--------|
| 1.1 | scene/script 间接触发展开 + notify 标"副作用不可观测" | 1 天 | v2.0.1 | 与 1.2/1.3 并行 |
| 1.2 | 双轨对拍测试（FakeHA vs HiFi 一致性断言，sun 触发器白名单豁免） | 1 天 | 无 | 与 1.1 并行 |
| 1.3 | shadow 推导扩表（toggle 取反 / set_temperature / set_cover_position / volume_set，约 20 行）**+ 补 af_shadow 首个专项测试文件** | 1 天 | 无 | 并行 |
| 1.4 | EXEMPT 豁免通道（verdict + streak 语义 + 人审入口 + 审计 + 诚实报告 exempted 分区） | 1 天 | 1.3 | 否 |
| 1.5 | shadow_log 落盘 + 重启回放 | 0.5 天 | 1.3 | 与 1.4 并行 |
| 1.6 | 契约冻结：build/simulate 返回 `schema:"af-stage/1"` + issues_from 严格模式（一版过渡期容错） | 1 天 | 无 | 并行，但须早于 1.8 |
| 1.7 | af_watch 聚合层 + 诚实报告 `verified_in_prod` 回灌 | 1.5 天 | 1.4、1.6 | 否 |
| 1.8 | 全量回归 + 采信台真跑 + 评审窗 | 0.5 天 | 全部 | — |

关键路径：1.3 → 1.4 → 1.7 → 1.8（约 4 天）；1.1/1.2/1.6 为并行支线。

## §4 v2.2（总 4.5 天，8 个 PR，过 G2 门）

| PR | 内容 | 工时 | 依赖 | 可并行 |
|----|------|------|------|--------|
| 2.1 | Premiere/Trial Store JSON 写透 + 启动 load **+ 补 af_premiere 专项测试** | 1 天 | v2.1 | 与 2.3 并行 |
| 2.2 | paused 接 af_apply 执行闸 + 分级策略（低风险二次失败暂停/高风险首次即暂停）+ 人工恢复入口 | 0.5 天 | 2.1 | 否 |
| 2.3 | pre_snapshot 扩属性（状态+attributes 快照）**+ 补 af_canary 专项测试** | 1 天 | v2.1 | 与 2.1 并行 |
| 2.4 | DOMAIN_SETTER 参数化回放 + fail-closed + af_undo.py | 1 天 | 2.3 | 否 |
| 2.5 | CLI `forge undo` + ForgeSight 撤销按钮 | 0.5 天 | 2.4 | 否 |
| 2.6 | band 归一术语对照表（纯文档 PR，先评审） | 0.5 天 | 无 | 任意时点 |
| 2.7 | band 归一代码统一 | 1 天 | 2.6 | 否 |
| 2.8 | 冲突仲裁默认开启（独立 PR，行为变更开关） | 0.5 天 | 2.7 | 否 |

**undo 验收硬门**：light.turn_on（含亮度）、climate.set_temperature、cover.set_cover_position 三域，下发后 60s 内 undo 恢复前态含属性；不可映射动作 fail-closed 告警不误滚。

## §5 v2.3（仅登记骨架，主体等需求门）

| PR | 内容 | 工时 | 依赖 |
|----|------|------|------|
| 3.0 | **IR 版本机制修复（无条件做，可提前到 v2.2 顺带）**：`const "0.2.1"` → 枚举 `["0.2.1","0.3.0"]`，schema/`af_ir/models.py:45`/orchestrator 三处同步 + 前向兼容放宽（§0 修正 1） | 0.5 天 | 无 |
| 3.1 | group 节点 schema（`mode: sequence|parallel`、`children` 递归），ir_version 升 0.3.0 | 1 天 | 3.0 + 需求门 |
| 3.2 | 编译/仿真对 group 支持 | 1.5 天 | 3.1 |
| 3.3 | NL 渲染 + 诚实报告对 group 支持 | 1 天 | 3.1 |
| 3.4 | 复合部署原子性（依赖 undo）+ 跨自动化一致性预检 | 1.5 天 | 3.2 + v2.2 undo |

验收硬门：IR→NL→IR 往返保真测试；两条有依赖的自动化编排为单图，simulate 正确演化、按依赖序下发。

## §6 v2.4 / v2.5 预登记（不拆 PR，过门后再拆）

- v2.4 过门问题：experience 库有效记录数是否 ≥100 条；
- v2.5 方向已定（IR 唯一真源、NL 仅视图、歧义必须落 Ask 追问）；过门问题：真实 NL 输入超 `_ACTION_VERBS` 覆盖的比例。

---

## §7 v2.3 需求决策门 · 采集方案补全

### 7.1 指标定义（回答开发者留下的两个待补问题）

- **分子**：含 ≥2 条**独立意图**的 compose 调用数。"独立意图"判定：IntentParser 解析出 ≥2 个 action 目标，且**目标实体集不相交**。
- **弱信号处理**："同时提两个房间自动化"（实体不相交、无依赖）**计入分子但单独打标** `independent_multi=true`——这类其实批量下发即可解决，不需要 group。
- **真复合依赖**（决策门真正关心的）：多意图且实体集相交或存在显式时序词（"然后/之后/等…再"）。打标 `dependent_multi=true`。
- **门槛修正**：`dependent_multi` 占比 ≥5% → v2.3 启动；若仅 `independent_multi` 高而 dependent 低 → 不做 group，改做"批量下发"小功能（成本 1 天，ROI 远高于复合编排）。**这是草案没看到的分叉：占比高也不一定要做 v2.3。**

### 7.2 采集代码（骨架，落点 `af_orchestrator.compose()`）

```python
# compose 入口，IntentParser 解析完成后打点（约 :2057 后）
intents = parse_result.intents  # 现有解析结果
entity_sets = [set(i.entities) for i in intents]
overlapping = any(a & b for i, a in enumerate(entity_sets) for b in entity_sets[i+1:])
temporal = any(kw in raw_text for kw in ("然后", "之后", "等", "再", "then"))
af_metrics.emit("compose_intent", {
    "n_intents": len(intents),
    "independent_multi": len(intents) >= 2 and not overlapping,
    "dependent_multi": len(intents) >= 2 and (overlapping or temporal),
})
```

### 7.3 看板与告警

- 复用 af_metrics 按日聚合，30 天滚动窗；
- 看板三项：`compose_total` / `independent_multi%` / `dependent_multi%`；
- 告警（反向用）：`dependent_multi%` 连续两周 ≥5% → 提醒 DCD 启动 G3 评审；**不设下限告警**（需求不足是常态，不需要打扰）。

---

## §8 各版本质量门（采信台真跑标准）

| 版本 | 新增单测下限 | 专项测试补建 | 硬门 |
|------|------------|------------|------|
| v2.0.1 | +5（鉴权 fail-closed） | — | gates.sh 退出码 0；git 干净 |
| v2.1 | +20 | af_shadow、af_premiere 首建 | 双轨对拍绿；含 toggle 自动化 streak=3 转正；诚实报告 non_simulable 区收缩 |
| v2.2 | +15 | af_canary 首建 | undo 三域恢复验收；试演失败被闸拦截 |
| v2.3 | +15（含往返保真） | orchestrator group 路径 | IR→NL→IR 往返 diff 为空 |
| 全版本 | 每次发版 README/KICKOFF 数字同步 | — | 文档鲜度纳入验收（DCD 纪律 §5） |

---

## §9 并行度与总工期

- v2.0.1：2.5-3 天（0.1-a 与 0.1-b 可并行）；
- v2.1：关键路径 4 天，并行充分时 **5-6 天**（单人按 6 天排）；
- v2.2：**4.5 天**；
- 累计到 v2.2 出口：约 12-14 天（单人串行口径）。

—— 关键决策部 · Directorate of Critical Decisions (DCD)
