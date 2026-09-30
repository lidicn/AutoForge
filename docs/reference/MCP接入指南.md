# AutoForge MCP 接入指南（让 Agent 连入 AutoForge）

> 适用版本：v1.0.1（MCP stdio server 已交付）起，含 v1.1.0–v1.6.0 全部能力。
> 目标读者：想用 Claude Desktop / CodeBuddy / Cursor / Cline 等 Agent 直接驱动 AutoForge 的用户。

---

## 1. 它是什么

`forge mcp` 以 **stdio** 模式启动一个零依赖的 MCP server，把 AutoForge 的全部能力以 **24 个工具**暴露给任意兼容 MCP 的 Agent。
Agent 端无需了解 REST：直接「说人话 → 查设备拿真 ID → 写 AF-Spec → `af_build` 过安全闸 → `af_simulate` 验证逻辑 → `af_save` 进待批队列」，由人在 WebUI/CLI 审批后落盘。

> 注：`README.md` 旧写「21 个工具」，代码已扩展到 **24 个**（v1.1.0 新增 5 个设备目录工具、v1.5.0 新增 `af_experience`/`af_telemetry`）。以本文件与 `docs/ROADMAP.md` 为准。

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

## 6. 24 个工具速查

| 工具 | 能力 | scope |
|---|---|---|
| `af_health` | 服务健康自检（版本/契约/里程碑/readonly） | 公开 |
| `af_refresh_catalog` | 拉 HA 全屋设备目录进本地缓存 | 公开 |
| `af_resolve_entity` | 设备名→候选 entity_id（写 IR 前必调） | 公开 |
| `af_remember_entity` | 沉淀「设备名→entity_id」别名（下次直中） | 写 |
| `af_list_entities` | 全屋实体过滤浏览（分页） | 公开 |
| `af_get_entity_state` | 查某实体当前状态 | 公开 |
| `af_catalog` | 设备目录摘要（域/区域/freshness） | 公开 |
| `af_build` | 第一道闸：校验自动化 IR | 公开 |
| `af_compile_spec` | AF-Spec 文本→IR | 公开 |
| `af_simulate` | 第二道闸：FakeHA 回放 + expect 断言 | 公开 |
| `af_list_graphs` | 列出已归档自动化 | 公开 |
| `af_get_graph` | 获取某归档指定版本图 | 公开 |
| `af_graphs_by_tag` | 按标签筛选归档 | 公开 |
| `af_conf` | 查看置信度分级（G4） | 公开 |
| `af_set_tags` | 设置归档标签 | 写 |
| `af_enable_by_tag` | 批量启停某标签下自动化 | 写 |
| `af_export_store` | 导出整个 store 为 bundle | 公开 |
| `af_import_store` | 导入 bundle（skip/overwrite/rename） | 写 |
| `af_save` | 归档为新版本（**先入待批队列**） | 写 |
| `af_diff` | 版本 diff（G6） | 公开 |
| `af_live_run` | 真机下发（三重闸） | live |
| `af_whoami` | 自检当前 MCP 会话鉴权身份 | 公开 |
| `af_experience` | 实体共现经验摘要 | 公开 |
| `af_telemetry` | token/结果遥测 + 错误类别分布 | 公开 |

> MCP 面**不注册** `approve` 工具（批准只在 HTTP/CLI 服务层），且比 HTTP 面窄：
> 无 `af_bind` / `af_intervene` / 会话类工具——MCP 只暴露 Agent 安全可用的子集。

## 7. 排错

- **Agent 看不到工具**：确认 `forge` 在 PATH；Windows 改用绝对路径或 `python -m autoforge.af_cli`。
- **写工具被拒**：检查 env 里 `AUTOFORGE_TOKENS` 的 JSON 是否合法、scope 是否含 `write` / `live`；用 `af_whoami` 自查。
- **设备解析为空**：先 `af_refresh_catalog`（或 CLI `forge entities refresh`）拉一次 HA 全屋目录。
- **真机下发 403**：服务端未开 `AUTOFORGE_LIVE_ENABLED=1` 或缺失 `AUTOFORGE_HA_TOKEN`。
- **store 找不到归档**：确认 `--root` / `AUTOFORGE_STORE_ROOT` 指向你实际存放 `.forge` 的目录。
