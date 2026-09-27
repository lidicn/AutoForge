"""Secret 管理：敏感凭据走挂载的 secret 文件，env 仅作兼容回退。

v2.0.1 / PR 0.1-b：把 HA/API 令牌从明文 `env_file: .env` 移出，改由 docker
secrets（或任意挂载的 secret 文件目录）提供。读取优先级：

    credentials.json（af_config 内的文件存储） > secret 文件 > 环境变量

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
