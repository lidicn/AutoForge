"""Secret 管理：敏感凭据走挂载的 secret 文件，env 仅作兼容回退。

v2.0.1 / PR 0.1-b：把 HA/API 令牌从明文 `env_file: .env` 移出，改由 docker
secrets（或任意挂载的 secret 文件目录）提供。

**本模块自己实现的两段**：`secret 文件 > 环境变量`。

第三段 `credentials.json` **不在本层**，它由 `af_config` 接在前面，而且只接在
`af_config.get_ha_token()` / `get_api_token()` 这两个键上（先取 `credentials.json`，
取不到才落到 `load_secret("AUTOFORGE_HA_TOKEN" / "AUTOFORGE_API_TOKEN")`）。
所以「`credentials.json` > secret 文件 > 环境变量」这条三段链的**射程只有那两个键**：
其余名字（`AUTOFORGE_TOKENS`、`AUTOFORGE_REVOKED_TOKENS`，以及 `af_auth.py` 里
legacy 的 `AUTOFORGE_API_TOKEN` 回退）由 `af_auth` 直接调 `load_secret`，**不经**
`credentials.json`。旧措辞把三段链写成了全局事实——安全审计 F-11 对撞后按现读订正射程，
行为一行未改。

- secret 文件目录：默认 `/run/secrets`（docker secrets 挂载点），可用
  `AUTOFORGE_SECRET_DIR` 环境变量覆盖（本地开发/非 docker 场景）。
- 文件名为大写环境变量名，如 `/run/secrets/AUTOFORGE_HA_TOKEN`。
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

    返回脱尾空白的文本；文件不存在/无权限/为空时返回 default。
    """
    p = secret_path(name)
    try:
        val = p.read_text(encoding="utf-8").strip()
        if val:
            return val
    except (OSError, ValueError):
        # 文件不存在、无权限、或解码失败：交给 env 回退
        pass
    return os.environ.get(name, default)


def load_secret_or_none(name: str) -> str | None:
    """同上，但缺失时返回 None（便于区分"未配置"与"空字符串"）。"""
    val = load_secret(name, "")
    return val or None
