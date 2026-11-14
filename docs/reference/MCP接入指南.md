# AutoForge MCP 接入指南（让 Agent 连入 AutoForge）

> 适用版本：v1.0.1（MCP stdio server 已交付）起，含 v1.1.0–v1.6.0 全部能力。
> 目标读者：想用 Claude Desktop / CodeBuddy / Cursor / Cline 等 Agent 直接驱动 AutoForge 的用户。

---

## 1. 它是什么

`forge mcp` 以 **stdio** 模式启动一个零依赖的 MCP server，把 AutoForge 的全部能力以 **39 个工具**暴露给任意兼容 MCP 的 Agent。
Agent 端无需了解 REST：直接「说人话 → 查设备拿真 ID → 写 AF-Spec → `af_build` 过安全闸 → `af_simulate` 验证逻辑 → `af_save` 进待批队列」，由人在 WebUI/CLI 审批后落盘。

> 注：`README.md` 旧写「21 个工具」，经多版扩展（v1.1.0 设备目录工具、v1.5.0 `af_experience`/`af_telemetry`、v1.29 执行授权/任务单/场景/配对族），现读 **39 个**。全集以 `af_mcp.TOOLS` 注册表与 `tools/list` 现读为准，本文件 §六 逐条列出（v1.30.10 前本表只列 24 条——MCP-13 缺口已补）。

## 2. 前置条件

- 已安装 CLI：`pip install -e ".[dev]"`，确保 `forge` 在 PATH 上。
  Windows 若 PATH 不便，可改用 `python -m autoforge.af_cli mcp` 作为 `command`。
- 一个 **store 根目录**（默认 `./.forge`，即当前工作目录下的 `.forge`）。你的自动化归档、设备目录缓存都在这里。
  若放在别处，启动时用 `--root /abs/path/to/.forge`（或环境变量 `AUTOFORGE_STORE_ROOT`）。

## 3. 最小接入（只读，无需令牌）

Agent 配置文件里加一段 `mcpServers`：

- **Claude Desktop**
  - macOS：`~/Library/Application Support/Claude/claude_desktop_config.json`
  - Windows：`%APPDATA%\Claude\claude_desktop_config.json`
- **CodeBuddy / Cursor / Cline**：在各自的 MCP 配置里填同样的 `command` / `args`。

```json
{
  "mcpServers": {
    "autoforge": { "command": "forge", "args": ["mcp"] }
  }
}
```

> 未设任何令牌时，server 处于**全放行原型模式**，Agent 可调用全部**只读/校验**工具
> （`af_build` / `af_simulate` / `af_resolve_entity` / `af_list_graphs` …）。写 / live 工具会被 scope 门拒绝——这是预期行为，
> 逼你在明确授权下才写真机。

**改完配置重启 Agent 应用**（让它重新拉起 MCP 子进程）。之后对 Agent 说「帮我看下书房吊灯现在怎么控制」，
它应自动调 `af_resolve_entity` / `af_list_entities` 等工具。

## 4. 授权写 / 真机下发（需要令牌 + scope）

想让 Agent 能存档（`af_save`→进待批）、刷新目录、真机下发，需给它一个带 `write` / `live` scope 的令牌：

```json
{
  "mcpServers": {
    "autoforge": {
      "command": "forge",
      "args": ["mcp", "--root", "/abs/path/to/.forge"],
      "env": {
        "AUTOFORGE_TOKENS": "{\"sk-your-agent-token\": {\"subject\": \"agent\", \"scopes\": [\"read\", \"write\", \"live\"]}}"
      }
    }
  }
}
```

- `AUTOFORGE_TOKENS` 是 JSON 对象：`{ "<token串>": { "subject": "...", "scopes": ["read","write","live"] } }`。
- 旧式 `AUTOFORGE_API_TOKEN=<token>` 仍可用，等价于拥有全部 scope（平滑升级）。
- MCP server 只会用**第一条**令牌的身份运行；需要不同主体时调整 JSON 顺序。
- scope 门在**工具层**生效：`af_save`(write) / `af_enable_by_tag`(write) / `af_import_store`(write) / `af_live_run`(live) 缺对应 scope 会直接报错「拒绝：缺少 xxx 权限」。

## 5. 验证接入

1. 让 Agent 调 `af_whoami` → 返回当前 `subject` + `scopes`，确认授权生效。
2. 只读链路：`af_resolve_entity("书房吊灯")` → 拿到候选 `entity_id` + 可能状态 → 写 AF-Spec →
   `af_build` → `af_simulate`（看 `expect` 是否 `fully_verified`）→ `af_save`（**进入待批队列，不会自己落盘**）。
3. 人在 **WebUI / CLI**（`forge pending approve <id>`）里审批后才真正落盘——**Agent 不能自批**（安全铁律，见 `docs/ROADMAP.md` v1.4.0）。
4. 真机下发：`af_live_run` 需服务端 `AUTOFORGE_LIVE_ENABLED=1` + `AUTOFORGE_HA_TOKEN` + 请求 `confirm=true`
   + 非空 `live_allow` 白名单且 IR 写目标 ⊆ 白名单（三重闸）。

## 6. 39 个工具速查

| 工具 | 能力 | scope |
|---|---|---|
| `af_draft` | 黄金路径第 1 步：传意图 JSON，自动生成 IR 并返回 ref | None |
| `af_apply` | 黄金路径第 2 步：ref 一次走完校验→仿真→入队 | write |
| `af_test_submit` | 测试通道：批量提交 draft→build→simulate（落 `/data/test/`） | write |
| `af_test_report` | 测试通道：获取测试报告 | None |
| `af_test_clear` | 测试通道：清空测试区（test_root 由服务端定，Agent 递不进路径） | write |
| `af_health` | 服务健康自检（版本/契约/里程碑/联动/写闸） | None |
| `af_request_pair` | 配对第 1 步：发起配对请求，8 位单次短时效码经 SSE 推用户弹窗 | write |
| `af_pair` | 配对第 2 步：用口述配对码兑换 Bearer 令牌 | write |
| `af_refresh_catalog` | 拉 HA 全屋设备目录进本地缓存（首连/大增减后调一次） | None |
| `af_resolve_entity` | 设备名→entity_id 唯一正路 | None |
| `af_remember_entity` | 设备名→entity_id 人工/agent 选型写入（v1.6.0 决策智能） | write |
| `af_list_entities` | 全屋实体目录过滤浏览（读本地缓存，不触真实 HA） | None |
| `af_get_entity_state` | 查某实体当前状态 | None |
| `af_catalog` | 设备目录摘要（按域统计＋区域，防数千实体撑爆上下文） | None |
| `af_build` | 第一道闸：校验自动化 IR | None |
| `af_compile_spec` | AF-Spec 文本语法编译成 IR（同一份真相） | None |
| `af_simulate` | 第二道闸：内存 FakeHA 回放验证逻辑 | None |
| `af_list_graphs` | 列出已归档自动化（版本/模式/标签） | None |
| `af_quarantine` | 隔离区清单（`.deleted/` 可人工恢复） | read |
| `af_delete_graph` | 删除归档：移入 `.deleted/`（可恢复，⛔ 物理删） | write |
| `af_get_graph` | 取某归档指定版本图（ir/nl/diagnostics） | None |
| `af_graphs_by_tag` | 按标签筛选归档 | None |
| `af_conf` | 查看归档置信度分级（G4 MA） | None |
| `af_set_tags` | 设置归档标签（覆盖式） | write |
| `af_enable_by_tag` | 批量启停某标签下所有自动化（需授权码闸） | write |
| `af_export_store` | 导出 store 为可携带 bundle（含 tags＋校验和） | None |
| `af_import_store` | 导入 bundle | write |
| `af_save` | IR 归档为新版本（先过第一道闸） | write |
| `af_diff` | 版本 diff（G6）：结构化差异＋可读 render | None |
| `af_live_run` | 真机下发（四重闸＋**单写者租约**：serve 持锁时 503 `READONLY_DEGRADED`——设计口径非故障，真机下发请走 HTTP 面） | live |
| `af_grant` | 执行授权 v1.29：码管部署、grant 管执行（issue/list/revoke/renew/receipts） | write |
| `af_order` | 一次性任务单 v1.29（create/list/get/cancel） | write |
| `af_scene` | 场景对象 v1.29（save/list/get/delete/run；**run 需 live 档＋单写者租约**） | write |
| `af_whoami` | 自检当前 MCP 会话鉴权身份（subject/scopes） | None |
| `af_guide` | 起草前必读：AF 自动化起草指引全文 | None |
| `af_experience` | 实体共现经验摘要（成功落盘后采集，失败样本不污染） | None |
| `af_telemetry` | token/结果遥测＋错误类别分布 | None |
| `af_deploy` | 发起部署（写更新标记给宿主脚本，与 WebUI 升级按钮同枚 marker） | admin |
| `af_deploy_status` | 部署进度（pending/requested） | admin |

> MCP 面**不注册** `approve` 工具（批准只在 HTTP/CLI 服务层），且比 HTTP 面窄：
> 无 `af_bind` / `af_intervene` / 会话类工具——MCP 只暴露 Agent 安全可用的子集。

## 7. 排错

- **Agent 看不到工具**：确认 `forge` 在 PATH；Windows 改用绝对路径或 `python -m autoforge.af_cli`。
- **写工具被拒**：检查 env 里 `AUTOFORGE_TOKENS` 的 JSON 是否合法、scope 是否含 `write` / `live`；用 `af_whoami` 自查。
- **设备解析为空**：先 `af_refresh_catalog`（或 CLI `forge entities refresh`）拉一次 HA 全屋目录。
- **真机下发 403**：服务端未开 `AUTOFORGE_LIVE_ENABLED=1` 或缺失 `AUTOFORGE_HA_TOKEN`。
- **store 找不到归档**：确认 `--root` / `AUTOFORGE_STORE_ROOT` 指向你实际存放 `.forge` 的目录。
