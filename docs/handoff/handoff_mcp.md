# 交接卡 v1.0.1 — Agent 接入（MCP stdio server）

## 背景

路线图 v0.2.0–v1.0.0 十个版本全部交付，但 v0.6–v1.0 五个版本仅经单测 + 容器回归，无真实 agent 端到端验证。
用户决策：**先配置 MCP 用 agent 实测，再决定是否继续迭代**。本交付是实测基础设施（非功能迭代）。

## 改动清单（1 新增 + CLI + 测试 + 文档）

| 文件 | 变更 |
|---|---|
| `src/autoforge/af_mcp.py` | **新增**：零依赖 MCP stdio server（兼容 MCP 2024-11-05）。16 个工具 + scope 门 + 协议循环 + `dispatch()` 测试入口。 |
| `src/autoforge/af_service.py` | 新增 `save_graph()`（先过静态扫描再归档 + 置信度种子，与 MCP/HTTP/CLI 同源）——补齐 agent 归档闭环。 |
| `src/autoforge/af_cli.py` | 新增 `forge mcp [--root]` 子命令启动 stdio server。 |
| `tests/unit/test_af_mcp.py` | **新增** 14 项单测（dispatch 功能 + scope 门 + 子进程协议整轮 initialize→tools/list→tools/call）。 |
| `README.md` / `docs/ROADMAP.md` | 补 `forge mcp` 启动与 agent 接入说明；路线图加 v1.0.1（规模 S）✅。 |

## 设计要点

- **零依赖**：`mcp` 包在本机/NAS 双环境装不稳（外部网络受限）。MCP 的 stdio 本质是「逐行 JSON-RPC 2.0」，
  自实现最稳、双环境可跑、测试可纯子进程驱动。agent 端只认协议不认框架。
- **进程内直调 `af_service`**：与 REST 同源逻辑，**不绕任何安全闸**（静态扫描/ACL/live 三重闸全生效）。
- **scope 门（复用 v0.8.0）**：工具层 `_guard(scope, current)` 用启动时环境令牌主体（读 `AUTOFORGE_API_TOKEN`/`AUTOFORGE_TOKENS`）。
  无令牌 → 全放行（原型兼容）；只读令牌 → `af_set_tags`/`af_enable_by_tag`/`af_import_store`/`af_save` 拒、只读令牌调 `af_live_run` 拒；
  含对应 scope 的令牌 → 放行。让 agent 实测能真实踩到 v0.8.0 的越权语义。
- **16 个工具**：`af_health` / `af_build` / `af_compile_spec` / `af_simulate` / `af_list_graphs` / `af_get_graph` /
  `af_graphs_by_tag` / `af_conf` / `af_set_tags`(写) / `af_enable_by_tag`(写) / `af_export_store` /
  `af_import_store`(写) / `af_save`(写) / `af_diff` / `af_live_run`(live) / `af_whoami`。
  每个中文 docstring 含参数示例与坑（如 live 需确认/白名单），提升 agent 自诊断。
- **归档闭环（`af_save`）**：把编译/构建通过的 IR 归档为新版本，**先过第一道闸**（静态扫描未过则拒绝，避免坏自动化落盘），
  并同时写置信度种子快照——与 `forge store save` 语义一致。至此 agent 完整链路 = 自然语言→spec→build→sim→**save**→标签→导出导入→live。
- **协议**：stdin 逐行读请求，stdout 逐行回响应；通知（无 id，如 `notifications/initialized`）不回。

## 接入方式

agent 侧 mcpServers 配置：`{"autoforge": {"command": "forge", "args": ["mcp"]}}`。
需写/live 能力时，把令牌注入 MCP server 启动环境的 `AUTOFORGE_TOKENS`
（如 `{"<token>": {"subject": "agent", "scopes": ["read", "write"]}}`）。

## 验证

- 本机：`python -m pytest tests/unit/test_af_mcp.py -q` → **14 passed**。
  含子进程整轮：`initialize` 返回 protocolVersion=`2024-11-05`、16 工具齐全且 inputSchema 完整、call `af_health` 成功。
- 全量：`python -m pytest -q` → **337 passed / 10 skipped / 0 failed**（v1.0.0 基线 323 + 14）。
- 真实 `forge mcp` 端到端冒烟（临时脚本 `tmp/mcp_e2e_smoke.py`，跑完即删）：
  AF-Spec 文本→`af_compile_spec`→`af_build`(ok)→`af_simulate`→`af_save`(v1)→`af_list_graphs`→`af_export_store`，
  以及 `af_save` 拒绝坏 IR（isError=True）——`E2E_OK: True`。
- NAS 待同步验证（双环境）。

## 已知风险 / 注意

- `mcp` 外部包装不上，故零依赖实现；若日后要切 FastMCP，工具定义可平滑迁移（签名一致）。
- MCP server 为内存态（同 `forge serve` 的服务层假设）：`af_*_session` 系列会话未暴露为工具（原型期多进程会话不一致）。
- `af_live_run` 仍受服务端三重闸限制（需 `AUTOFORGE_LIVE_ENABLED=1` + HA 令牌），未启用时返回明确 403 信息——正是 agent 应学会自我纠错的场景。
- stdio 模式单次只服务一个 agent 连接（launch 即一个进程），多 agent 需各自启动或走 HTTP（本期未实现）。
