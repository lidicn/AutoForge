"""Secret 管理：敏感凭据走挂载的 secret 文件，env 仅作兼容回退。

v2.0.1 / PR 0.1-b：把 HA/API 令牌从明文 `env_file: .env` 移出，改由 docker
secrets（或任意挂载的 secret 文件目录）提供。

**本模块自己实现的两段**：`secret 文件 > 环境变量`。

第三段 `credentials.json` **不在本层**，它由 `af_config` 接在前面，而且只接在
`af_config.get_ha_token()` / `get_api_token()` 这两个键上（先取 `credentials.json`，
取不到才落到 `load_secret("HA_LONG_LIVED_TOKEN" / "AF_ACCESS_TOKEN")`，旧名
`AUTOFORGE_HA_TOKEN` / `AUTOFORGE_API_TOKEN` 回退）。
所以「`credentials.json` > secret 文件 > 环境变量」这条三段链的**射程只有那两个键**：
其余名字（`AF_ACCESS_TOKENS`、`AF_REVOKED_TOKENS`，旧 `AUTOFORGE_TOKENS`、
`AUTOFORGE_REVOKED_TOKENS`，以及 `af_auth.py` 里 legacy 单密钥回退）由 `af_auth`
直接调 `load_secret`，**不经** `credentials.json`。旧措辞把三段链写成了全局事实——
安全审计 F-11 对撞后按现读订正射程，行为一行未改。

- secret 文件目录：默认 `/run/secrets`（docker secrets 挂载点），可用
  `AUTOFORGE_SECRET_DIR` 环境变量覆盖（本地开发/非 docker 场景）。
- 文件名为大写环境变量名，如 `/run/secrets/HA_LONG_LIVED_TOKEN`（旧 `AUTOFORGE_HA_TOKEN`）。
- 缺失或为空时回退到同名环境变量，保证与旧 `env_file` 行为兼容。
"""

from __future__ import annotations

import os
from pathlib import Path


def secret_path(name: str) -> Path:
    """返回某个 secret 的挂载路径。"""
    base = os.environ.get("AUTOFORGE_SECRET_DIR", "/run/secrets")
    return Path(base) / name


def load_secret(name: str, default: str = "") -> str:
    """读取 secret：优先 secret 文件，缺失/空则回退环境变量。

    返回脱尾空白的文本；文件不存在/为空时返回 default。

    AF55：文件**在**却读不出来（权限/解码失败）时**抛错**，不再静默回退 env。
    旧实现把这一档与"文件不存在"归并——而对鉴权面（AUTOFORGE_API_TOKEN 等）而言，
    env 多半也没配：读不出 ⇒ 静默落到空 ⇒ `TokenRegistry.enabled=False` ⇒ 整个 API
    从"要求令牌"静默退化成"全站公开"，且与"真的没配"逐字不可区分、零日志。
    现在这一档必须炸出来，让运维立刻看到现场。
    """
    p = secret_path(name)
    try:
        val = p.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        # 文件不存在 = 没配，正常回退 env
        return os.environ.get(name, default)
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            f"secret 文件 {p} 存在却读不出来（{type(exc).__name__}）；"
            "拒绝静默回退环境变量，以免鉴权状态被静默降级。请修复文件权限/内容。"
        ) from exc
    if val:
        return val
    # 空文件 = 没配，回退 env
    return os.environ.get(name, default)


def load_secret_or_none(name: str) -> str | None:
    """同上，但缺失时返回 None（便于区分"未配置"与"空字符串"）。"""
    val = load_secret(name, "")
    return val or None


def load_secret_chain(*names: str, default: str = "") -> str:
    """按顺序尝试多个 secret/env 名，返回第一个非空值（新名优先、旧名回退）。

    用于 20261017 角色法重命名：新名先读，读不到再回退旧名，平滑切换不断服。
    """
    for name in names:
        val = load_secret(name, "")
        if val:
            return val
    return default
