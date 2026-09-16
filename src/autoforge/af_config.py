"""v1.4.0 凭据热重载：凭据落盘 + 代数自愈。

设计来源：调研 §2.9（connection_revision 代数）。

- 凭据存 `{root}/credentials.json`（gitignore + `0600`），**绝不**进版本库、绝不回明文；
- `connection_revision` 全局代数：改凭据 → `bump_revision` → 各连接层 client 比对代数
  变化即丢弃缓存重建，免重启进程（对齐「多个 Gateway 共享单例」的自愈语义）；
- `describe()` 只回**掩码 + 长度**（连末 4 位都不露），防 WebUI 截图 / 投屏泄露。

`credentials.json` 与 `AUTOFORGE_HA_TOKEN` / `AUTOFORGE_API_TOKEN` 环境变量并存：
文件优先，缺省回退环境变量（向后兼容原型期 `export` 启动方式）。
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Mapping

__all__ = ["Config", "get_config"]


def _mask(token: str) -> str:
    """只回掩码 + 长度，绝不泄露任何明文片段。"""
    if not token:
        return ""
    return f"****len={len(token)}"


class Config:
    """某 store 根的凭据 + 连接代数单例（按 root 缓存，见 `get_config`）。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self._lock = threading.Lock()
        self._creds = self._load_credentials()
        self.connection_revision = self._load_revision()

    # ── 路径 ─────────────────────────────────────────────────────────
    def _creds_path(self) -> Path:
        return self.root / "credentials.json"

    def _revision_path(self) -> Path:
        return self.root / "revision.json"

    # ── 读取 ────────────────────────────────────────────────────────
    def _load_credentials(self) -> dict[str, Any]:
        try:
            return json.loads(self._creds_path().read_text(encoding="utf-8")) or {}
        except (OSError, ValueError):
            return {}

    def _load_revision(self) -> int:
        try:
            return int(json.loads(self._revision_path().read_text(encoding="utf-8")).get("revision", 0) or 0)
        except (OSError, ValueError):
            return 0

    def get_ha_token(self) -> str:
        """HA 长期访问令牌：文件优先，缺省回退环境变量。"""
        return str(self._creds.get("ha_token") or os.environ.get("AUTOFORGE_HA_TOKEN", ""))

    def get_api_token(self) -> str:
        """AutoForge API 令牌：文件优先，缺省回退环境变量。"""
        return str(self._creds.get("api_token") or os.environ.get("AUTOFORGE_API_TOKEN", ""))

    # ── 原子写（mkstemp + os.replace + chmod 600）────────────────────
    def _atomic_write(self, path: Path, data: Mapping[str, Any]) -> None:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:  # Windows 无 0600 语义，尽力而为
            pass

    # ── 更新凭据 + 代数自增 ──────────────────────────────────────────
    def update_credentials(
        self, *, ha_token: str | None = None, api_token: str | None = None
    ) -> dict[str, Any]:
        """原子落盘新凭据 + 自增 connection_revision，返回 describe（只掩码）。"""
        with self._lock:
            creds = dict(self._creds)
            if ha_token is not None:
                creds["ha_token"] = ha_token
            if api_token is not None:
                creds["api_token"] = api_token
            self._atomic_write(self._creds_path(), creds)
            self._creds = creds
            self._bump_revision_locked()
        return self.describe()

    def bump_revision(self) -> int:
        """仅自增代数（不更凭据），返回新代数。"""
        with self._lock:
            return self._bump_revision_locked()

    def _bump_revision_locked(self) -> int:
        self.connection_revision += 1
        self._atomic_write(self._revision_path(), {"revision": self.connection_revision})
        return self.connection_revision

    # ── 自检（只掩码 + 长度）────────────────────────────────────────
    def describe(self) -> dict[str, Any]:
        return {
            "ha_token": _mask(self.get_ha_token()),
            "api_token": _mask(self.get_api_token()),
            "connection_revision": self.connection_revision,
        }


#: 进程内单例缓存（按 root 字符串键）：同一 store 根共享一个 Config，
#: 使 af_adapters/ha.py 的 HATransport/HAStateProvider 与 af_cli watch 引用同一代数。
_CONFIGS: dict[str, Config] = {}
_CONFIGS_LOCK = threading.Lock()


def get_config(root: str | Path) -> Config:
    key = str(Path(root))
    with _CONFIGS_LOCK:
        cfg = _CONFIGS.get(key)
        if cfg is None:
            cfg = Config(root)
            _CONFIGS[key] = cfg
        return cfg
