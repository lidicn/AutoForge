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
import time
from pathlib import Path
from typing import Any, Mapping

from .af_secrets import load_secret

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
        """HA 长期访问令牌：credentials.json 文件优先，缺省回退 secret 文件/环境变量。

        v2.0.1/0.1-b：原仅回退 `os.environ`，现经 `load_secret` 把 secret 文件
        （docker secrets 挂载点）作为凭据主路径之一，env 仅作兼容回退。
        """
        return str(self._creds.get("ha_token") or load_secret("AUTOFORGE_HA_TOKEN"))

    def get_api_token(self) -> str:
        """AutoForge API 令牌：credentials.json 文件优先，缺省回退 secret 文件/环境变量。"""
        return str(self._creds.get("api_token") or load_secret("AUTOFORGE_API_TOKEN"))

    # ── 原子写（mkstemp + fsync + os.replace，权限在 tmp 上先设）────────────────────
    def _atomic_write(self, path: Path, data: Mapping[str, Any]) -> None:
        # P1-22 修复：
        # - 用 tempfile.mkstemp 生成随机 tmp 名（避免并发写同名 .tmp）
        # - 权限在 tmp 上先设 0o600（避免 rename 后、chmod 前的窗口期令牌可读）
        # - 写后 fsync（掉电数据落盘）
        import tempfile
        fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            # 先设权限再写内容（窗口期最小化）
            try:
                os.chmod(tmp_path, 0o600)
            except OSError:
                pass  # Windows 无 0600 语义
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(data, ensure_ascii=False, indent=2))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
            # 目录 fsync
            try:
                dir_fd = os.open(str(path.parent), os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            except OSError:
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

    # ── 外部改盘后自愈（R-54）──────────────────────────────────────
    def refresh(self) -> None:
        """从磁盘重读凭据与代数：外部进程改了 credentials.json/revision.json
        后，本进程缓存的 Config 不再永久过期。"""
        with self._lock:
            self._creds = self._load_credentials()
            self.connection_revision = self._load_revision()

    # ── 自检（只掩码 + 长度）────────────────────────────────────────
    def describe(self) -> dict[str, Any]:
        return {
            "ha_token": _mask(self.get_ha_token()),
            "api_token": _mask(self.get_api_token()),
            "connection_revision": self.connection_revision,
        }


#: 进程内单例缓存（按 root 字符串键）：同一 store 根共享一个 Config。
#: R-54：不再永久缓存——按 TTL 重读磁盘（默认 60s，env AUTOFORGE_CONFIG_TTL_S 可调，0=不过期）。
_CONFIGS: dict[str, tuple[Config, float]] = {}
_CONFIGS_LOCK = threading.Lock()
_CONFIG_TTL_S = float(os.getenv("AUTOFORGE_CONFIG_TTL_S", "60"))


def get_config(root: str | Path) -> Config:
    key = str(Path(root))
    now = time.monotonic()
    with _CONFIGS_LOCK:
        entry = _CONFIGS.get(key)
        if entry is None:
            cfg = Config(root)
            _CONFIGS[key] = (cfg, now)
            return cfg
        cfg, loaded_at = entry
        if _CONFIG_TTL_S > 0 and now - loaded_at > _CONFIG_TTL_S:
            cfg.refresh()
            _CONFIGS[key] = (cfg, now)
        return cfg
