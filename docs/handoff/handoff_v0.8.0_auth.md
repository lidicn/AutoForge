# 交接卡 v0.8.0 — 服务层鉴权升级

## 改动清单（4 文件新增/修改 + 测试 + 文档）

| 文件 | 变更 |
|---|---|
| `src/autoforge/af_auth.py` | **新增**：鉴权引擎。`TokenRegistry`（env 多令牌加载 / 认证 / 撤销黑名单 + 落盘）、`RateLimiter`（IP + 主体双维度固定窗口）、`TokenInfo`、`RateLimitExceeded`。纯逻辑，不 import fastapi。 |
| `src/autoforge/af_api.py` | 移除旧 `_require_auth`（单密钥等值比较）→ `requires(scope)` / `authenticated()` 依赖工厂 + 全局 `_rate_limit_dep`；全部受保护端点迁移到 scope 依赖；新增 `/api/auth/whoami`、`/api/auth/subjects`、`/api/auth/revoke`；模块 docstring 补 v0.8.0 契约。 |
| `src/autoforge/af_cli.py` | 新增 `forge auth list`（主体摘要，不含明文令牌）、`forge auth revoke`（撤销 + 落盘）。 |
| `tests/unit/test_v0_8_auth.py` | **新增** 10 项单测（见下）。 |

## 行为变化

- **多令牌主体模型**：`AUTOFORGE_TOKENS`（JSON 对象 `{token: {subject, scopes[]}}`；值也兼容字符串=subject、数组=scopes、非法 scope 回退 read）。旧 `AUTOFORGE_API_TOKEN` 平滑映射为 subject=shared、scopes={read,write,live}。两者都未设置 → 全站公开（与 v0.7.0 前行为一致）。
- **端点 scope 分级**：
  - `read`（公开，无需令牌）：health / graphs / conf / metrics / diff / spec / faults / store/export / sessions 列表与详情 / build / sim / spec/compile。
  - `write`：graphs/tags·enable·disable、store/import、conf/intervene（**新纳入鉴权**）、sessions 写、auth 管理。
  - `live`：live/status、live/run。
- **撤销即时生效**：`POST /api/auth/revoke {token}`（需 write scope）→ 内存黑名单 + 落盘 `{store_root}/.auth/revoked.json`；重启自动恢复；另支持启动期 `AUTOFORGE_REVOKED_TOKENS`（逗号分隔）。
- **限速**：全局依赖，IP + 主体双维度固定窗口（`AUTOFORGE_RATE_LIMIT_PER_MIN`，默认 1000/min），超限 429（读端点也受限）。
- 越权响应统一 403（`令牌缺少 'X' 权限（当前 scopes：…）`），无效/已撤销令牌 403，未携带 403，whoami 未认证 401。

## 已知风险 / 注意

- `conf/intervene` 此前无鉴权，本次纳入 write —— 已有 3 个 intervene 测试用的 `client` fixture 未设令牌（鉴权关闭）故不受影响；生产若已设 `AUTOFORGE_API_TOKEN`，前端干预按钮需带令牌。
- 撤销黑名单持久化在 `{store_root}/.auth/revoked.json`；多进程部署（v0.9.0 主题）需注意该文件为本地文件、非共享。
- CLI `forge auth revoke` 只写盘，运行中服务**重启后才生效**；需即时撤销走 HTTP 端点（设计取舍，见测试注释）。
- 限速为进程内固定窗口，重启清零；TestClient 下 client IP 恒为 unknown（测试即借此触发）。
- 令牌在 env 中为明文（原型期取舍）；jti 字段已预留（`TokenInfo.jti`），当前以令牌字符串本身为黑名单键。

## 验证

- 本机：`python -m pytest -q` → **297 passed / 10 skipped / 0 failed**（v0.7.0 基线 + 10）。
- NAS（`autoforge-test` 容器，Python 3.14，全量同步后）：**297 passed / 0 failed**；另 10 errors 全部为 `test_vhass_native.py` 的 `fixture 'hass' not found`（容器缺 pytest-homeassistant 夹具的**既有环境差异**，本机这些为 skip，与本次改动无关）。
- 新增测试覆盖：meta 三形态解析与非法 scope 回退、撤销落盘 + 重启恢复、限速器纯函数、未配置全开放、旧单密钥兼容（403/200/whoami）、多令牌越权矩阵（reporter read-only / bot 无 live）、HTTP 撤销即时 403 + 落盘 + 重启仍拒、429。

## 合并影响

- 前端（Autoforge-UI）若使用干预/启停/导入等写端点：服务端设令牌后必须携带对应 scope 的 Bearer；只读浏览不受影响。
- `openapi.json` 自动更新（新端点 + 依赖），`/docs` 可直接联调。
