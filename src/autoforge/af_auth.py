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

import hmac
import json
import os
import threading
import time
import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .af_secrets import load_secret

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

    def __init__(
        self,
        revoked_path: str | Path | None = None,
        issued_path: str | Path | None = None,
    ) -> None:
        self._tokens: dict[str, TokenInfo] = {}
        self._revoked: set[str] = set()
        self._revoked_path = Path(revoked_path) if revoked_path else None
        self._issued_path = Path(issued_path) if issued_path else None
        self._lock = threading.Lock()
        self._load_env()
        self._load_revoked_file()
        self._load_issued_file()

    # ── 配置加载 ──
    def _load_env(self) -> None:
        legacy = (load_secret("AUTOFORGE_API_TOKEN") or "").strip()
        if legacy:
            # 旧单密钥：拥有全部 scope，便于平滑升级
            self._tokens[legacy] = TokenInfo(subject="shared", scopes={"read", "write", "live"})
        raw = (load_secret("AUTOFORGE_TOKENS") or "").strip()
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
        for t in (load_secret("AUTOFORGE_REVOKED_TOKENS") or "").split(","):
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
            # P0-9 修复：恒定时间比较，防止时序攻击推断有效令牌
            for revoked in self._revoked:
                if hmac.compare_digest(raw_token, revoked):
                    return None
            info = None
            for key, val in self._tokens.items():
                if hmac.compare_digest(raw_token, key):
                    info = val
                    break
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


    # ── v1.9.0 用户 WebUI：运行时签发 agent 令牌（配对成功后调用）──
    def issue_for_agent(
        self,
        agent_name: str,
        scopes: Iterable[str] = ("read", "write", "live"),
        token: str | None = None,
    ) -> str:
        """运行时为 agent 签发 Bearer 令牌（配对成功后调用）。返回明文令牌。"""
        token = token or ("af_" + secrets.token_hex(20))
        scopes_set = {s for s in scopes if s in SCOPES} or {"read"}
        with self._lock:
            self._tokens[token] = TokenInfo(subject=agent_name, scopes=scopes_set)
            if self._issued_path:
                self._persist_issued(token, agent_name, sorted(scopes_set))
        return token

    def revoke_by_subject(self, subject: str) -> int:
        """撤销某主体名下的全部已签发令牌；返回撤销数量。"""
        n = 0
        with self._lock:
            for tok, info in list(self._tokens.items()):
                if info.subject == subject:
                    self._revoked.add(tok)
                    n += 1
            if self._issued_path:
                self._persist_revoked()
                self._rewrite_issued(subject, drop=True)
        return n

    def rename_subject(self, old: str, new: str) -> int:
        """给 agent 改名：更新其名下全部令牌的 subject；返回受影响数量。"""
        n = 0
        with self._lock:
            for info in self._tokens.values():
                if info.subject == old:
                    info.subject = new
                    n += 1
            if self._issued_path:
                self._rewrite_issued(old, new_name=new)
        return n

    # ── 已签发令牌落盘（重启后恢复，owner 可删除/改名 agent）──
    def _load_issued_file(self) -> None:
        if not self._issued_path or not Path(self._issued_path).is_file():
            return
        try:
            data = json.loads(Path(self._issued_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(data, dict):
            for tok, meta in data.items():
                if tok in self._revoked:
                    continue
                if isinstance(meta, dict) and "subject" in meta:
                    self._tokens[tok] = TokenInfo(
                        subject=meta["subject"],
                        scopes=set(meta.get("scopes", ["read"])),
                    )

    def _persist_issued(self, token: str, subject: str, scopes: list[str]) -> None:
        if not self._issued_path:
            return
        p = Path(self._issued_path)
        data: dict[str, Any] = {}
        if p.is_file():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
        data[token] = {"subject": subject, "scopes": scopes}
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _rewrite_issued(self, subject: str, new_name: str | None = None, drop: bool = False) -> None:
        if not self._issued_path or not Path(self._issued_path).is_file():
            return
        try:
            data = json.loads(Path(self._issued_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        changed = False
        for tok, meta in list(data.items()):
            if isinstance(meta, dict) and meta.get("subject") == subject:
                if drop:
                    data.pop(tok, None)
                    changed = True
                elif new_name:
                    meta["subject"] = new_name
                    changed = True
        if changed:
            Path(self._issued_path).write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )


# ─────────────────────────────────────────────────────────────────────
# v1.9.0 用户 WebUI：配对码 / 授权码 文件存储（与 .auth/revoked.json 同目录）
# ─────────────────────────────────────────────────────────────────────


def _rand6() -> str:
    """6 位配对/授权码（纯数字，便于口述转告 agent）。"""
    return "".join(secrets.choice("0123456789") for _ in range(6))


@dataclass
class PairCode:
    code: str
    agent_name_hint: str
    created_at: float
    expires_at: float
    consumed: bool = False
    pushed: bool = False  # SSE 是否已推送给前端


class PairCodeStore:
    """配对码存储：落盘 `.auth/pair_codes.json`，进程内读缓存 + 写时落盘。

    单次短时效（默认 5 分钟）。agent 经 MCP `af_request_pair` 触发生成，
    后端经 SSE `/api/mcp/pair-request` 推送给前端弹窗；agent 再用
    `af_pair(code)` 兑换运行时签发的 Bearer 令牌。
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._codes: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.is_file():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(data, list):
            for c in data:
                if isinstance(c, dict) and "code" in c:
                    self._codes[c["code"]] = c

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(list(self._codes.values()), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def create(self, agent_name_hint: str, ttl_s: int = 300) -> PairCode:
        now = time.time()
        code = _rand6()
        rec = {
            "code": code,
            "agent_name_hint": agent_name_hint,
            "created_at": now,
            "expires_at": now + ttl_s,
            "consumed": False,
            "pushed": False,
        }
        with self._lock:
            self._codes[code] = rec
            self._persist()
        return PairCode(**rec)

    def get(self, code: str) -> dict[str, Any] | None:
        """只读按码取记录（不消费）；跨进程同步读（MCP 写、API 读）。"""
        self._load()
        return self._codes.get(code)

    def consume(self, code: str) -> PairCode | None:
        """校验并消费配对码；无效（不存在/已消费/过期）返回 None。"""
        self._load()  # 跨进程同步：MCP 写入、API 读取
        with self._lock:
            rec = self._codes.get(code)
            if rec is None or rec.get("consumed"):
                return None
            if time.time() > rec.get("expires_at", 0):
                return None
            rec["consumed"] = True
            self._persist()
        return PairCode(**rec)

    def pending_events(self) -> list[PairCode]:
        """尚未消费、尚未推送、未过期的配对码（供 SSE 推送）。"""
        self._load()  # 跨进程同步：MCP 写入、API 读取
        now = time.time()
        out: list[PairCode] = []
        with self._lock:
            for rec in self._codes.values():
                if (
                    not rec.get("consumed")
                    and not rec.get("pushed")
                    and now <= rec.get("expires_at", 0)
                ):
                    out.append(PairCode(**rec))
        return out

    def mark_pushed(self, code: str) -> None:
        with self._lock:
            rec = self._codes.get(code)
            if rec is not None:
                rec["pushed"] = True
                self._persist()


@dataclass
class AuthCode:
    code: str
    kind: str  # "long" | "short"
    created_at: float
    expires_at: float | None  # None = 长期（可撤销）
    revoked: bool = False


class AuthCodeStore:
    """部署授权码存储：落盘 `.auth/auth_codes.json`。

    长期码（kind="long"，expires_at=None，可撤销）与短期码（kind="short"，ttl 5–30 分钟）。
    agent 部署（af_save）持有效码走路径 A 直部署，否则路径 B 入待批队列。
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._codes: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.is_file():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(data, list):
            for c in data:
                if isinstance(c, dict) and "code" in c:
                    self._codes[c["code"]] = c

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(list(self._codes.values()), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def create(self, kind: str, ttl_minutes: int | None = None) -> AuthCode:
        now = time.time()
        expires_at = None if kind == "long" else now + (ttl_minutes or 5) * 60
        rec = {
            "code": _rand6(),
            "kind": kind,
            "created_at": now,
            "expires_at": expires_at,
            "revoked": False,
        }
        with self._lock:
            self._codes[rec["code"]] = rec
            self._persist()
        return AuthCode(**rec)

    def list(self) -> list[dict[str, Any]]:
        """返回全部授权码摘要（含明文 code，单 owner 下仅供 owner 查看管理）。"""
        with self._lock:
            return [
                {
                    "code": c["code"],
                    "kind": c["kind"],
                    "created_at": c["created_at"],
                    "expires_at": c.get("expires_at"),
                    "revoked": c.get("revoked", False),
                }
                for c in self._codes.values()
            ]

    def revoke(self, code: str) -> bool:
        with self._lock:
            rec = self._codes.get(code)
            if rec is None or rec.get("revoked"):
                return False
            rec["revoked"] = True
            self._persist()
            return True

    def validate(self, code: str) -> bool:
        """有效（存在、未撤销、未过期）返回 True。"""
        with self._lock:
            rec = self._codes.get(code)
            if rec is None or rec.get("revoked"):
                return False
            exp = rec.get("expires_at")
            if exp is not None and time.time() > exp:
                return False
            return True


class RateLimiter:
    """IP + 主体双维度固定窗口限速。

    用法：对每个请求分别 `check("ip:<client>")` 与（若已认证）`check("subj:<subject>")`。
    任一维度超限即抛 `RateLimitExceeded`。
    """

    #: 最大保留的 key 数量（P0-10：防止 _hits 无界增长导致内存泄漏）
    _MAX_KEYS = 10000

    def __init__(self, per_minute: int = 1000, window_s: int = 60) -> None:
        self.per_minute = per_minute
        self.window_s = window_s
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _cleanup_expired(self, now: float) -> None:
        """清理所有已过期的 key（P0-10：防止 _hits 无界增长）。"""
        cutoff = now - self.window_s
        expired = [k for k, v in self._hits.items() if not v or v[-1] <= cutoff]
        for k in expired:
            del self._hits[k]

    def check(self, key: str) -> None:
        if self.per_minute <= 0:
            return
        now = time.time()
        with self._lock:
            # P0-10：超过最大 key 数时触发全量清理
            if len(self._hits) >= self._MAX_KEYS:
                self._cleanup_expired(now)
            hits = self._hits.setdefault(key, [])
            cutoff = now - self.window_s
            if hits and hits[0] <= cutoff:
                hits[:] = [t for t in hits if t > cutoff]
            if len(hits) >= self.per_minute:
                raise RateLimitExceeded(
                    f"请求过于频繁（限速 {self.per_minute}/min，维度 {key!r}）"
                )
            hits.append(now)
