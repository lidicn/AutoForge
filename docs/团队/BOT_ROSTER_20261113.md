# AF 双编排平面 · 排查与处置记录

> 2026-11-13 13:40 落盘。**本文档取代同日早先的「归档前备份」版本**——那份基于「解散群 + 归档席位」的假设，实际处置是**保留 ds-bot 群与席位、关闭实验性 agent-team 插件**。
> 行尾口径：新建文档，按 docs 主流 LF。

## 一、事故

2026-11-13 12:32，`origin/master` 上出现 `fdaab06 chore(release): 1.30.2 契约重生成`，并有人在其上打 tag `v1.30.2`。事发后无法唯一归因——查不出是谁推的。

## 二、根因

同一台机器上**同时启用了两套多智能体编排平面**：

| 平面 | 插件 | 版本 | 状态 | 实体 |
|---|---|---|---|---|
| A · 群 | `ds-bot` | 0.3.2 | enabled | 常驻 Bot：Chief、AF-PM、AF-Core、AF-UI、AF-QA-Code、AF-QA-MCP、AF-Verify |
| B · 团队 | `@deepseek-ai/dsh-experimental-agent-team-profile` | 0.2.2-rc.1 | enabled | 临时成员：`AF-Core`、`AF-Verify`（**同名不同实体**） |

两边同名，模型路由不同：平面 A 的 AF-Core 跑 `magpie/group/af-core`，平面 B 的 `AF-Core` 继承 PM 自己的 `magpie/group/af-pm`。

**两条流水线都持有 `git push` 权限** ⇒ 归因缺失。这不是技术故障，是**两个平面同时开工**。

## 三、关键发现

`@deepseek-ai/dsh-experimental-agent-team-profile` 的 bundle 定义里有：

```json
"overrides": ["tool-subagent-control", "tool-subagent", "tool-subagent-fork"]
```

**它静默替换了全 profile 的 subagent 工具组。** 这个副作用与「有没有在用 team」无关——只要它启用着，所有 Bot 的 subagent 调用走的就是另一套实现。今天两套 roster 能同时存在而无人察觉，原因就在这行 override。

附带查清：本机还装了**第二套** agent-teams 实现 `@nanmicoder/dsh-agent-teams@0.1.29`（`enabled: false`）。两套互不兼容的实现同装，靠一个开关区分，本身就是隐患。

## 四、处置

```
plugin_manager set_bundle \
  target  = @deepseek-ai/dsh-experimental-agent-team-profile \
  enabled = false

→ changed: true
→ application: restart-required
→ restartReason: unmanaged-plugin
```

外挂载插件，**改动不热加载，须重启 DSH 生效**。

**保留**：群「AutoForge」及其全部席位（常驻记忆与交接班历史都在这里）。
**不归档**：先前讨论过的「归档 5 个 Bot」方案作废——问题不在席位，在第二套平面。

## 五、改后铁律

1. **单一编排平面**：只有 ds-bot 群能干活；`agent-team` 插件保持关闭
2. **`git push` / `git tag` 只由 AF-PM 执行**；任何其他席位、任何临时成员都不许碰 `.git` 操作
3. **发版唯一出口**：三处版本同值 bump → `chore(release)` → `git push origin HEAD:master` → `git tag vX.Y.Z && git push origin vX.Y.Z`
4. **席位名唯一**：如果哪天真要再开 team，成员名必须带批次后缀（`core-r1`），与常驻席位区分
5. **重启后自查**：确认 `agent_teams_*` 工具已从工具列表消失

## 六、席位快照（存档，不作归档用）

| 席位 | 角色 | 模型 | 文件域 |
|---|---|---|---|
| AF-PM | PM（Main Bot） | `magpie/group/af-pm` | 全部；发版唯一出口 |
| AF-Core | 内核 | `magpie/group/af-core` | `src/autoforge/` + `tests/` |
| AF-UI | 界面 | `magpie/group/deepseek-flash4.1` | `ui/`、`ui-user/`、`ui-user-mimo/` |
| AF-QA-Code | 代码QA | `magpie/auto/hy3` | 审计报告（①④⑤类） |
| AF-QA-MCP | MCPQA | `magpie/auto/hy4free` | 拨测报告（②③类） |
| AF-Verify | 验收 | `magpie/group/sensenova-6.8` | `E:\NAS\QA\AF_Tester\reports\` |
| AF-Verify-停用 | 停用 | `magpie/auto/hy4free` | 无（2026-11-08 已停用，待出资人删除） |

**文件域纪律**：Core 写 `src/autoforge` + `tests/`；UI 写三个前端目录；Verify 只写验收报告；docs/ 与发版归 AF-PM。
