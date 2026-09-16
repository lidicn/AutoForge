"""v0.8.0 服务层鉴权引擎：多令牌主体模型 + 撤销（jti 黑名单）+ 限速 + 端点 scope 分级。

设计目标：
- **向后兼容**：仅设 `AUTOFORGE_API_TOKEN`（旧单密钥）→ 视为 subject=shared、scopes={read,write,live} 的令牌。
- **多令牌主体模型**：`AUTOFORGE_TOKENS` 为 JSON 对象，每条令牌自报 `subject` + `scopes`
  （`read`/`write`/`live` 子集）。主体（subject）用于审计与限速归因。
- **撤销**：内存黑名单 + 落盘 `{store_root}/.auth/revoked.json`；`revoke(token)` 即时生效，
  重启后从盘恢复；另支持启动期紧急封禁 `AUTOFORGE_REVOKED_TOKENS`（逗号分隔）。
- **过期三重 fail-closed（v1.4.0 附，调研 §2.8）**：`AUTOFORGE_TOKENS` 每条令牌可选
  `expires_at`（ISO8601 **必须带时区**）。解析失败 / 无时区 → **拒绝**该令牌（不是放过）；
  已过期 → `TokenExpired`（由 `af_api` 转 HTTP 403）。未配 `expires_at` → 永不过期（向后兼容）。
- **限速**：IP + 主体双维度固定窗口（默认 1000/min，可配 `AUTOFORGE_RATE_LIMIT_PER_MIN`）。
- **端点分级**：`read`（公开）/ `write` / `live` 三档，由 `af_api.requires(scope)` 落地。

本模块为纯逻辑（不依赖 FastAPI 具体请求对象），`af_api` 负责把它接线成路由依赖。
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

#: 端点 scope 三档
SCOPES = ("read", "write", "live")


class RateLimitExceeded(Exception):
    """限速触发，由 af_api 转换为 HTTP 429。"""


class TokenExpired(Exception):
    """令牌已过期（v1.4.0 附 / 调研 §2.8）：由 af_api 转换为 HTTP 403。

    与「未知/已撤销令牌」（`authenticate` 返回 None → 403）区分：过期是**已知但失效**。
    """


@dataclass
class TokenInfo:
    subject: str
    scopes: set[str]
    jti: str | None = None
    #: v1.4.0 附（B §2.8）：过期时间（epoch 秒，UTC）；None = 永不过期。
    expires_at: float | None = None
    #: `expires_at` 解析失败 / 无时区 → fail-closed 禁用该令牌（`authenticate` 返回 None）。
    invalid: bool = False
    invalid_reason: str = ""


class TokenRegistry:
    """令牌注册表：env 静态配置 + 运行时撤销黑名单 + 落盘。"""

    def __init__(self, revoked_path: str | Path | None = None) -> None:
        self._tokens: dict[str, TokenInfo] = {}
        self._revoked: set[str] = set()
        self._revoked_path = Path(revoked_path) if revoked_path else None
        self._lock = threading.Lock()
        self._load_env()
        self._load_revoked_file()

    # ── 配置加载 ──
    def _load_env(self) -> None:
        legacy = (os.getenv("AUTOFORGE_API_TOKEN") or "").strip()
        if legacy:
            # 旧单密钥：拥有全部 scope，便于平滑升级
            self._tokens[legacy] = TokenInfo(subject="shared", scopes={"read", "write", "live"})
        raw = (os.getenv("AUTOFORGE_TOKENS") or "").strip()
        if raw:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"AUTOFORGE_TOKENS 不是合法 JSON：{exc}") from exc
            if not isinstance(data, dict):
                raise ValueError("AUTOFORGE_TOKENS 必须是对象 {token: {subject, scopes}}")
            for token, meta in data.items():
                subject, scopes = self._parse_meta(meta)
                expires_at, invalid_reason = self._parse_expiry(meta)
                self._tokens[token] = TokenInfo(
                    subject=subject,
                    scopes=set(scopes),
                    expires_at=expires_at,
                    invalid=bool(invalid_reason),
                    invalid_reason=invalid_reason,
                )
        # 启动期紧急封禁（运维封禁已泄露令牌）
        for t in (os.getenv("AUTOFORGE_REVOKED_TOKENS") or "").split(","):
            t = t.strip()
            if t:
                self._revoked.add(t)

    @staticmethod
    def _parse_meta(meta: Any) -> tuple[str, list[str]]:
        if isinstance(meta, dict):
            subject = str(meta.get("subject", "unknown"))
            scopes = [s for s in meta.get("scopes", ["read"]) if s in SCOPES]
        elif isinstance(meta, (list, tuple)):
            scopes = [s for s in meta if s in SCOPES]
            subject = "unknown"
        elif isinstance(meta, str):
            subject, scopes = meta, ["read"]
        else:
            subject, scopes = "unknown", ["read"]
        if not scopes:
            scopes = ["read"]
        return subject, scopes

    @staticmethod
    def _parse_expiry(meta: Any) -> tuple[float | None, str]:
        """解析 `expires_at`（v1.4.0 附 / 调研 §2.8 三重 fail-closed）。

        返回 `(epoch_秒 | None, 失败原因)`：

        - 缺省 → `(None, "")`：永不过期（向后兼容，未配 `expires_at` 的令牌不受影响）；
        - **无法解析**（ValueError/TypeError）→ `(None, "…")`：**拒绝**该令牌（不是放过）；
        - **naive datetime（无时区）** → `(None, "…")`：**拒绝**（无法与 aware 时间可靠比较）；
        - 合法 → `(dt.timestamp(), "")`。

        为什么反着来：很多系统「解析失败就放过」，会制造「永不过期」的幽灵凭据。
        """
        if not isinstance(meta, dict):
            return None, ""
        raw = meta.get("expires_at")
        if raw is None or raw == "":
            return None, ""
        try:
            dt = datetime.fromisoformat(str(raw))
        except (ValueError, TypeError):
            return None, f"expires_at 无法解析：{raw!r}"
        if dt.tzinfo is None:
            return None, f"expires_at 缺时区（naive）：{raw!r}"
        return dt.timestamp(), ""

    # ── 撤销（jti 黑名单）──
    def _load_revoked_file(self) -> None:
        if not self._revoked_path or not self._revoked_path.is_file():
            return
        try:
            data = json.loads(self._revoked_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(data, list):
            self._revoked.update(str(t) for t in data)

    def _persist_revoked(self) -> None:
        if not self._revoked_path:
            return
        self._revoked_path.parent.mkdir(parents=True, exist_ok=True)
        self._revoked_path.write_text(
            json.dumps(sorted(self._revoked), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @property
    def enabled(self) -> bool:
        """是否启用了鉴权（配置了任意令牌）。未启用时全站公开（读 + 写 + live）。"""
        return bool(self._tokens)

    def authenticate(self, raw_token: str | None) -> TokenInfo | None:
        """返回令牌主体信息；未知 / 已撤销 / 配置非法（`expires_at` 无法解析）返回 None。

        v1.4.0 附（B §2.8）：令牌**已过期**抛 `TokenExpired`（由 af_api 转 403），
        与「未知令牌」（None）区分。
        """
        if not raw_token:
            return None
        with self._lock:
            if raw_token in self._revoked:
                return None
            info = self._tokens.get(raw_token)
            if info is None:
                return None
            if info.invalid:
                return None  # 配置非法（expires_at 无法解析/naive）→ fail-closed 拒绝
            if info.expires_at is not None and time.time() > info.expires_at:
                raise TokenExpired(
                    f"令牌已过期（subject={info.subject}）"
                )
            return info

    def revoke(self, token: str) -> bool:
        """撤销令牌；返回是否确实发生变更（False=本就不在注册表或已撤销）。即时生效并落盘。"""
        with self._lock:
            if token in self._revoked:
                return False
            self._revoked.add(token)
            self._persist_revoked()
            return True

    def subjects(self) -> list[dict[str, Any]]:
        """已注册令牌的主体摘要（不含明文令牌，供 `forge auth list` / 审计用）。"""
        seen: dict[str, set[str]] = {}
        for info in self._tokens.values():
            seen.setdefault(info.subject, set()).update(info.scopes)
        return [{"subject": s, "scopes": sorted(sc)} for s, sc in sorted(seen.items())]


class RateLimiter:
    """IP + 主体双维度固定窗口限速。

    用法：对每个请求分别 `check("ip:<client>")` 与（若已认证）`check("subj:<subject>")`。
    任一维度超限即抛 `RateLimitExceeded`。
    """

    def __init__(self, per_minute: int = 1000, window_s: int = 60) -> None:
        self.per_minute = per_minute
        self.window_s = window_s
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        if self.per_minute <= 0:
            return
        now = time.time()
        with self._lock:
            hits = self._hits.setdefault(key, [])
            cutoff = now - self.window_s
            if hits and hits[0] <= cutoff:
                hits[:] = [t for t in hits if t > cutoff]
            if len(hits) >= self.per_minute:
                raise RateLimitExceeded(
                    f"请求过于频繁（限速 {self.per_minute}/min，维度 {key!r}）"
                )
            hits.append(now)
