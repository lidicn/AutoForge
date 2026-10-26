"""v0.8.0 服务层鉴权引擎：多令牌主体模型 + 撤销（jti 黑名单）+ 限速 + 端点 scope 分级。

设计目标：
- **命名（角色法 20261017）**：A 类「访问密钥」= 谁能登录/调用 AF（与 B 类 AF→HA 出站令牌严格区分）。
  新名 `AF_ACCESS_TOKENS`（多密钥 JSON）/ `AF_ACCESS_TOKEN`（旧式单密钥）；
  旧名 `AUTOFORGE_TOKENS` / `AUTOFORGE_API_TOKEN` / `AUTOFORGE_REVOKED_TOKENS` 作为回退兼容。
- **向后兼容**：仅设 `AF_ACCESS_TOKEN`（或旧 `AUTOFORGE_API_TOKEN`，单密钥）→ 视为
  subject=shared、scopes={read,write,live} 的令牌。
- **多令牌主体模型**：`AF_ACCESS_TOKENS`（或旧 `AUTOFORGE_TOKENS`）为 JSON 对象，每条令牌自报
  `subject` + `scopes`（`read`/`write`/`live` 子集）。主体（subject）用于审计与限速归因。
- **撤销**：内存黑名单 + 落盘 `{store_root}/.auth/revoked.json`；`revoke(token)` 即时生效，
  重启后从盘恢复；另支持启动期紧急封禁 `AF_REVOKED_TOKENS`（旧 `AUTOFORGE_REVOKED_TOKENS`，逗号分隔）。
- **过期三重 fail-closed（v1.4.0 附，调研 §2.8）**：`AF_ACCESS_TOKENS` 每条令牌可选
  `expires_at`（ISO8601 **必须带时区**）。解析失败 / 无时区 → **拒绝**该令牌（不是放过）；
  已过期 → `TokenExpired`（由 `af_api` 转 HTTP 403）。未配 `expires_at` → 永不过期（向后兼容）。
- **限速**：IP + 主体双维度固定窗口（默认 1000/min，可配 `AUTOFORGE_RATE_LIMIT_PER_MIN`）。
- **端点分级**：`read`（公开）/ `write` / `live` / `admin` 四档，由 `af_api.requires(scope)` 落地。
- **三级 agent 身份制（v1.13.0 起，2026-10-23 autoflow 照搬；v1.13.3 文档化收尾）**：
  三档是 **scope 预设约定**，不是独立机制——
  `normal = {read}`（只读，不能写 IR 不能下发）；
  `expert = {read, write, live}`（默认档：配对签发 `issue_for_agent` 的缺省就是它，能写能仿真能真机下发）；
  `developer = {read, write, live, admin}`（运维档：加了 admin——系统升级、watch 启停、删除自动化、
  `af_deploy`/`af_deploy_status` MCP 工具都在这档后面）。
  三条铁律：① admin 蕴含 read/write/live，反向不蕴含（`_SCOPE_SATISFIED_BY`）；
  ② **admin 永不经配对自动获得**——配对只签 expert 预设，developer 档必须出资人在
  `AF_ACCESS_TOKENS` 显式配（"身份码由人签发"，autoflow ARCHITECTURE §5）；
  ③ WebUI 登录令牌（subject=owner/shared）经 `af_api.requires` 的 owner 通道过 admin 门，
  与 scope 无关——那是"这台部署自己的手"，不是 agent 身份制的一员。
  agent 端自检用 `af_whoami`（实时返回 subject + scopes，即当前在哪一档）。

本模块为纯逻辑（不依赖 FastAPI 具体请求对象），`af_api` 负责把它接线成路由依赖。
"""

from __future__ import annotations

import hmac
import hashlib
import json
import os
import tempfile
import threading
import time
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .af_secrets import load_secret

#: 端点 scope 档（v1.13.0 新增 admin 运维档，2026-10-23 autoflow 三级身份制照搬）：
#: normal={read} / expert={read,write} / developer={read,write,live,admin}。
#: admin 蕴含 read/write/live（_SCOPE_SATISFIED_BY），反向不蕴含；owner subject 名单
#: 通道（af_api.requires）另对 WebUI 登录令牌放行。admin 不经配对自动获得——
#: 只能显式配在 AF_ACCESS_TOKENS（身份由人签发的铁律，autoflow ARCHITECTURE §5）。
SCOPES = ("read", "write", "live", "admin")


def _longcode_ttl_days() -> float:
    """长期码的绝对上限（天）。DCD 裁定 20261004 §一 Q1=A：默认 180，`0` = 显式关。

    读在调用时刻而非 import 时刻，测试与部署都能改而不必重导模块。
    解析不了的字符串回落默认值（配错键不该把上限悄悄关掉）；非正数按"关"处理，
    因为 `0` 是裁定给的显式关，负数只是同一意思的另一种写法。
    """
    raw = (os.getenv("AUTOFORGE_AUTH_LONGCODE_TTL_DAYS") or "").strip()
    if not raw:
        return LONGCODE_TTL_DAYS_DEFAULT
    try:
        days = float(raw)
    except ValueError:
        return LONGCODE_TTL_DAYS_DEFAULT
    return days if days > 0 else 0.0


#: 长期码绝对上限的默认天数（`AUTOFORGE_AUTH_LONGCODE_TTL_DAYS` 未设时生效）。
LONGCODE_TTL_DAYS_DEFAULT = 180.0


def _login_token_ttl_s() -> float:
    """WebUI 登录令牌的默认存活期（秒）。env `AF_LOGIN_TOKEN_TTL_S` 可覆盖，默认 86400（24h）。

    读在调用时刻而非 import 时刻（与 `_longcode_ttl_days` 同一口径）。
    解析失败或非正数回落默认——配错键不该把会话悄悄变成永不过期。
    """
    raw = (os.getenv("AF_LOGIN_TOKEN_TTL_S") or "").strip()
    if not raw:
        return 86400.0
    try:
        return float(raw) if float(raw) > 0 else 86400.0
    except ValueError:
        return 86400.0


def _longcode_expires_at(rec: dict[str, Any]) -> float | None:
    """该码的到期时刻（epoch 秒），`None` = 不过期。

    记 `expires_at` 的码按记录走；长期码若记的是 `None`（这条裁定之前落盘的老码），
    按"生成时刻 + 上限"补出来——不迁移数据也能让老码有界，且 `list()` 与 `validate()`
    读到的到期点是同一个，管理面不会显示"永不"而服务面却判它过期。
    """
    stored = rec.get("expires_at")
    if stored is not None:
        return float(stored)
    if rec.get("kind") != "long":
        return None
    ttl_days = _longcode_ttl_days()
    if ttl_days <= 0:
        return None
    return float(rec.get("created_at", 0.0)) + ttl_days * 86400


def _atomic_write_text(path: Path, text: str) -> None:
    """授权面的三个落盘文件都必须整份换：本模块在 L0，`af_store.atomic_write_text` 在 L1，
    向上依赖会被分层门禁判红，所以按 `af_store._atomic_write` 的同一形状自带一份
    （随机 tmp + fsync + replace + 尽力目录 fsync）。

    为什么这一族非走不可：`_load_*` 把解析失败一律吞成"当没有这份文件"。半截 JSON 因此
    不是"读回上一版"而是**读回空**——撤销黑名单读回空 = 已撤销的令牌重新有效（fail-open），
    授权码读回空 = owner 的长期码整店消失。写侧不给原子性，崩溃一次就足以造成。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:
            pass
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    try:
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass


def _digest(raw: str) -> str:
    """明文令牌 → SHA-256 十六进制摘要（第四批批 2：落盘键不再存明文）。

    内存侧 `authenticate` 仍拿"调用方手里那枚明文"比对——先把明文摘要一次，再查
    摘要索引，命中即返回，不再 O(n) 逐钥 compare_digest。revoked.json / issued_tokens.json
    上的键一律是这枚摘要值；迁移脚本 `scripts/migrate_issued_tokens_digest.py` 负责把
    存量明文键改写成摘要。⛔ 摘要值本身不是给人看的秘密，但也绝不写进审计/日志。
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _looks_digest(s: str) -> bool:
    """64 位小写十六进制 = SHA-256 摘要。用于装载时兼容存量明文键（迁移窗口容错）。"""
    return len(s) == 64 and all(c in "0123456789abcdef" for c in s)


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
    #: 仅用于界面显示的人类可读名（如 WebUI 登录用户名）。**不参与任何授权判定**——
    #: owner 面只看 `subject` 是否落在 `_OWNER_SUBJECTS`；WebUI 登录令牌的 subject 固定是
    #: `owner`（见 af_api 登录/注册签发），登录用户名靠这一格带到 `/api/auth/me` 显示。
    label: str | None = None
    #: 身份族（第四批批 2 / D9）：`login`=WebUI 登录注册、`agent`=配对兑换、
    #: `developer`=批 4 开发者签发端点。⛔ 不叫 `kind`（AuthCode 已占用，取值 long/short）。
    #: 默认落在最窄档 `agent`——只有签发点能把它抬到 login/developer，调用方不能从
    #: 请求体自助提权（见 issue_for_agent 的 principal_kind 形参校验）。
    principal_kind: str = "agent"


class TokenRegistry:
    """令牌注册表：env 静态配置 + 运行时撤销黑名单 + 落盘。"""

    #: 运行时签发令牌的默认存活期（秒）。此前运行期签发的令牌**不带** `expires_at`，
    #: 结构上永远无法过期，而 `/api/auth/login` 无鉴权依赖、每调一次追加一条 ⇒
    #: 凭证库只增不减（新增审计 BUG-19）。给默认 TTL，令牌到期即失效并被清理。
    #: env `AF_LOGIN_TOKEN_TTL_S` 可覆盖默认值（部署侧调长/调短，不用改码）。
    ISSUED_TTL_S = 86400.0
    #: 已签发令牌条数硬上限（兜底）：与同文件 `RateLimiter._MAX_KEYS` 同一纪律。
    MAX_ISSUED = 10000

    def __init__(
        self,
        revoked_path: str | Path | None = None,
        issued_path: str | Path | None = None,
        audit: Any = None,
    ) -> None:
        # G8/G9（2026-10-24）：审计日志可注入（测试传内存 AuditLog 断言）；
        # 不传则惰性建一个进程内 AuditLog——启动期模块不顶层 import af_audit（函数内建，防环）。
        if audit is None:
            from .af_audit import AuditLog

            audit = AuditLog()
        self._audit = audit
        # env 访问密钥：明文键（不落盘，AF_ACCESS_TOKENS 那族，第四批批 2 维持明文内存键）。
        self._tokens: dict[str, TokenInfo] = {}
        # 运行期签发令牌：**摘要键**（第四批批 2，D6）。落盘 issued_tokens.json 的键即摘要。
        self._issued: dict[str, TokenInfo] = {}
        self._revoked: set[str] = set()
        #: 撤销黑名单文件在但读不出来 ⇒ 置位并保持，直到进程重启（见 `authenticate`）。
        self._revoked_poisoned = False
        self._revoked_path = Path(revoked_path) if revoked_path else None
        self._issued_path = Path(issued_path) if issued_path else None
        self._lock = threading.Lock()
        self._load_env()
        self._load_revoked_file()
        self._load_issued_file()

    # ── 配置加载 ──
    # 命名（角色法 20261017）：A 类「访问密钥」= 谁能登录/调用 AF。
    #   新名 AF_ACCESS_TOKENS（多密钥 JSON）/ AF_ACCESS_TOKEN（旧式单密钥）；
    #   旧名 AUTOFORGE_TOKENS / AUTOFORGE_API_TOKEN 作为回退（新名优先，旧名兜底，平滑切换）。
    def _load_env(self) -> None:
        # 单密钥（新名优先，旧名回退）：拥有全部 scope，便于平滑升级
        new_single = (load_secret("AF_ACCESS_TOKEN") or "").strip()
        old_single = (load_secret("AUTOFORGE_API_TOKEN") or "").strip()
        if old_single:
            # G9（2026-10-24）：旧名被读到（无论新名是否也在）→ 落 legacy 告警，data 不带值。
            self._record_legacy_env("AUTOFORGE_API_TOKEN", "AF_ACCESS_TOKEN", both=bool(new_single))
        legacy = (new_single or old_single).strip()
        if legacy:
            self._tokens[legacy] = TokenInfo(subject="shared", scopes={"read", "write", "live"})
        # 多密钥 JSON（新名优先，旧名回退）
        new_multi = (load_secret("AF_ACCESS_TOKENS") or "").strip()
        old_multi = (load_secret("AUTOFORGE_TOKENS") or "").strip()
        if old_multi:
            self._record_legacy_env("AUTOFORGE_TOKENS", "AF_ACCESS_TOKENS", both=bool(new_multi))
        raw = (new_multi or old_multi).strip()
        if raw:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"访问密钥 AF_ACCESS_TOKENS 不是合法 JSON：{exc}") from exc
            if not isinstance(data, dict):
                raise ValueError("访问密钥 AF_ACCESS_TOKENS 必须是对象 {token: {subject, scopes}}")
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
        # 启动期紧急封禁（运维封禁已泄露令牌）——批 2：revoked 集合改存摘要，env 给的是明文。
        for t in (load_secret("AF_REVOKED_TOKENS") or load_secret("AUTOFORGE_REVOKED_TOKENS") or "").split(","):
            t = t.strip()
            if t:
                self._revoked.add(_digest(t))

    def _record_legacy_env(self, old_name: str, new_name: str, *, both: bool) -> None:
        """G9：A 类访问密钥走旧名回退 → 落一条 TOKEN_ENV_LEGACY_NAME（启动期一次性，不去重）。

        data 只放名字对与 both 标志，**绝不带令牌值**。
        """
        from .af_audit import AuditEvent, TOKEN_ENV_LEGACY_NAME

        self._audit.add(
            AuditEvent(
                type=TOKEN_ENV_LEGACY_NAME,
                at=datetime.now(timezone.utc),
                message=f"访问密钥走旧名回退：{old_name}",
                data={"legacy_name": old_name, "new_name": new_name, "both_names_set": both},
            )
        )

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
        self._revoked_poisoned = False
        if not self._revoked_path or not self._revoked_path.is_file():
            return
        try:
            data = json.loads(self._revoked_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # 撤销黑名单读不出来 ≠ 名单为空。此处若按"空名单"继续跑，被撤销过的令牌
            # 会全部复活（fail-open）。置位后 `authenticate()` 对任何令牌都返回 None，
            # 运维把文件修好（或删掉重建）即恢复。
            self._revoked_poisoned = True
            return
        if isinstance(data, list):
            # 批 2：文件里的条目本应已是摘要；迁移窗口内若残留明文键，就地补摘要，
            # 否则一枚"升级前已撤销"的令牌会因集合里躺着明文而被放行（fail-open）。
            self._revoked.update(
                (str(t) if _looks_digest(str(t)) else _digest(str(t))) for t in data
            )
        else:
            # AF66：能解析但顶层形状不对（如 {"a":1}、标量）≠ 空名单。旧实现落到这里
            # 什么都不做，self._revoked 只剩 env 侧那点东西 ⇒ 盘上已撤销过的令牌全部复活
            # （fail-open）。与"读不出来"同口径：置 poisoned，authenticate() 一律拒绝。
            self._revoked_poisoned = True

    def _persist_revoked(self) -> None:
        if not self._revoked_path:
            return
        _atomic_write_text(
            self._revoked_path,
            json.dumps(sorted(self._revoked), ensure_ascii=False, indent=2),
        )

    @property
    def enabled(self) -> bool:
        """是否启用了鉴权（配置了任意令牌）。未启用时全站公开（读 + 写 + live）。"""
        return bool(self._tokens or self._issued)

    def authenticate(self, raw_token: str | None) -> TokenInfo | None:
        """返回令牌主体信息；未知 / 已撤销 / 配置非法（`expires_at` 无法解析）返回 None。

        v1.4.0 附（B §2.8）：令牌**已过期**抛 `TokenExpired`（由 af_api 转 403），
        与「未知令牌」（None）区分。

        撤销黑名单文件存在却解析不出来时（`_revoked_poisoned`）**一律拒绝**：这时无法
        证明 handed token 不在名单里，按"名单为空"放行等于把撤销当作没发生过。
        """
        if not raw_token:
            return None
        if self._revoked_poisoned:
            return None
        with self._lock:
            # 批 2：内存先把明文摘要一次，revoked 集合与 issued 索引都是摘要键——
            # 一次哈希代替 O(n) 逐钥 compare_digest（顺带修掉原线性扫）。
            dg = _digest(raw_token)
            if dg in self._revoked:
                return None
            # 运行期签发令牌走摘要索引；env 访问密钥仍是明文键，原样查。
            info = self._issued.get(dg)
            if info is None:
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
        """撤销令牌；返回是否确实发生变更（False=本就不在注册表或已撤销）。即时生效并落盘。

        批 2：入参仍是调用方手里那枚明文（/api/auth/revoke 请求体契约不变），
        内存与落盘黑名单只存摘要。
        """
        with self._lock:
            dg = _digest(token)
            if dg in self._revoked:
                return False
            self._revoked.add(dg)
            self._persist_revoked()
            return True

    def subjects(self) -> list[dict[str, Any]]:
        """已注册令牌的主体摘要（不含明文令牌，供 `forge auth list` / 审计用）。"""
        seen: dict[str, set[str]] = {}
        for info in list(self._tokens.values()) + list(self._issued.values()):
            seen.setdefault(info.subject, set()).update(info.scopes)
        return [{"subject": s, "scopes": sorted(sc)} for s, sc in sorted(seen.items())]


    # ── v1.9.0 用户 WebUI：运行时签发 agent 令牌（配对成功后调用）──
    def issue_for_agent(
        self,
        agent_name: str,
        scopes: Iterable[str] = ("read", "write", "live"),
        token: str | None = None,
        ttl_s: float | None = None,
        label: str | None = None,
        principal_kind: str = "agent",
    ) -> str:
        """运行时为 agent 签发 Bearer 令牌（配对成功后调用）。返回明文令牌。

        `label` 仅作界面显示名，不参与授权；WebUI 登录面以 `agent_name="owner"` 签发、
        把登录用户名放进 `label`，使 owner 判定（subject 落在 `_OWNER_SUBJECTS`）不依赖
        用户名恰好叫什么，同时 `/api/auth/me` 仍能显示真实登录用户名。

        `principal_kind`（批 2 / D9）：身份族。⛔ **只有服务端签发点能定它**——登录/注册
        端点硬编码 `"login"`、配对兑换走默认 `"agent"`；调用方传 `"developer"` 视为自助
        提权，直接拒绝（developer 档只能由批 4 专用端点落盘，不经此入口）。
        """
        if principal_kind == "developer":
            # 腿 11：签发点是唯一能定身份的地方；从这里递 developer = 自己给自己发身份码。
            from .af_audit import AuditEvent, TOKEN_ISSUED_REJECTED

            self._audit.add(
                AuditEvent(
                    type=TOKEN_ISSUED_REJECTED,
                    at=datetime.now(timezone.utc),
                    message="拒绝自助提权：issue_for_agent 不接受 principal_kind=developer",
                    data={"subject": agent_name},
                )
            )
            raise ValueError("签发点不接受 principal_kind=developer（developer 档须由专用端点签发）")
        if principal_kind not in ("login", "agent"):
            principal_kind = "agent"
        token = token or ("af_" + secrets.token_hex(20))
        scopes_set = {s for s in scopes if s in SCOPES} or {"read"}
        # 运行期签发的令牌此前**不带 expires_at** ⇒ 结构上永远无法过期，
        # 而 `/api/auth/login` 无鉴权依赖、每次调用都追加一条 ⇒ 凭证库单调增长
        # （新增审计 BUG-19）。给一个默认 TTL，并加条数硬上限兜底。
        # TTL 默认值在调用时刻读 env（`AF_LOGIN_TOKEN_TTL_S`），部署侧可调；显式 ttl_s 优先。
        ttl = float(ttl_s) if ttl_s is not None else _login_token_ttl_s()
        expires_at = (time.time() + ttl) if ttl > 0 else None
        with self._lock:
            # 批 2：运行期签发令牌进摘要索引（不再混进 env 的明文 self._tokens）。
            self._issued[_digest(token)] = TokenInfo(
                subject=agent_name, scopes=scopes_set, expires_at=expires_at,
                label=label, principal_kind=principal_kind,
            )
            self._purge_expired_locked()
            if len(self._issued) > self.MAX_ISSUED:
                self._evict_oldest_locked()
            if self._issued_path:
                self._persist_issued(token, agent_name, sorted(scopes_set), expires_at, label,
                                    principal_kind=principal_kind)
        return token

    def _purge_expired_locked(self) -> None:
        """清掉已过期的已签发令牌（须持锁）。

        G8（2026-10-24）：此前剔除是静默的——审计里查不到"某枚登录令牌何时到期被清"，
        与"从未签发过"无从区分。dead 非空时落一条 TOKEN_EXPIRED_PURGED 事件（只记
        count/subjects/at，**绝不带令牌明文**）；dead 为空不落（空事件是噪声）。
        """
        now = time.time()
        # 批 2：过期剔除作用于运行期签发索引（摘要键）；env 静态令牌一并照此判定。
        pools = (self._issued, self._tokens)
        dead = [
            (pool, t, info)
            for pool in pools
            for t, info in pool.items()
            if info.expires_at is not None and info.expires_at <= now
        ]
        if not dead:
            return
        subjects = sorted({info.subject for _, _, info in dead})
        for pool, t, _info in dead:
            pool.pop(t, None)
        from .af_audit import AuditEvent, TOKEN_EXPIRED_PURGED

        self._audit.add(
            AuditEvent(
                type=TOKEN_EXPIRED_PURGED,
                at=datetime.now(timezone.utc),
                message=f"过期令牌剔除 {len(dead)} 枚",
                data={"count": len(dead), "subjects": subjects, "at": now},
            )
        )

    def _evict_oldest_locked(self) -> None:
        """超过 `MAX_ISSUED` 时按过期时间淘汰最旧的（须持锁）。

        淘汰顺序：先过期时间早的，没有过期时间的（永不过期）最后动。
        """
        overflow = len(self._issued) - self.MAX_ISSUED
        if overflow <= 0:
            return
        ordered = sorted(
            self._issued.items(),
            key=lambda kv: (kv[1].expires_at is None, kv[1].expires_at or 0.0),
        )
        for tok, _info in ordered[:overflow]:
            self._issued.pop(tok, None)

    def revoke_by_subject(self, subject: str, *, except_token: str | None = None) -> int:
        """撤销某主体名下的全部已签发令牌；返回撤销数量。

        `except_token`：排除某个 token 不撤销（用于删除 agent 时保留当前用户的登录态）。
        """
        n = 0
        except_dg = _digest(except_token) if except_token else None
        with self._lock:
            # env 池：明文键，撤销前先摘要；issued 池：已是摘要键，直接用。
            for tok, info in list(self._tokens.items()):
                if info.subject == subject:
                    dg = _digest(tok)
                    if except_dg and dg == except_dg:
                        continue
                    self._revoked.add(dg)
                    n += 1
            for dg, info in list(self._issued.items()):
                if info.subject == subject:
                    if except_dg and dg == except_dg:
                        continue
                    self._revoked.add(dg)
                    n += 1
            if self._issued_path:
                self._persist_revoked()
                self._rewrite_issued(subject, drop=True, except_token=except_token)
        return n

    def rename_subject(self, old: str, new: str) -> int:
        """给 agent 改名：更新其名下全部令牌的 subject；返回受影响数量。"""
        n = 0
        with self._lock:
            for pool in (self._tokens, self._issued):
                for info in pool.values():
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
        p = Path(self._issued_path)
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # AF67：与相隔约 150 行的撤销侧处置相反——旧实现读不出就静默 return，
            # 全部已签发令牌重启后从内存消失（用户侧 = 整个 API 锁死）且零日志。
            # 现在 fail-fast 启动即报错，与 _load_env 对坏 AUTOFORGE_TOKENS 的口径一致：
            # 配置/名单读不出来就不许带病启动，运维一眼看到现场。
            raise ValueError(
                f"{p.name} 已损坏，拒绝启动以保护已签发令牌名单；"
                f"请修复或隔离该文件后重试（{type(exc).__name__}）"
            ) from exc
        if not isinstance(data, dict):
            # AF67：能解析但形状不对（如数组）同样不能静默当空——旧实现
            # `if isinstance(data, dict)` 不成立就什么都不做，症状同上。
            raise ValueError(f"{p.name} 形状不是对象，拒绝启动以保护已签发令牌名单")
        now = time.time()
        for key, meta in data.items():
            # 批 2：盘上键本应是摘要；迁移窗口内若仍是明文键，就地补摘要（同 revoked 口径）。
            dg = key if _looks_digest(key) else _digest(key)
            if dg in self._revoked:
                continue
            if isinstance(meta, dict) and "subject" in meta:
                # 过期条目直接不载入：否则重启后满盘都是过期令牌（新增审计 BUG-19）
                exp = meta.get("expires_at")
                if isinstance(exp, (int, float)) and exp <= now:
                    continue
                self._issued[dg] = TokenInfo(
                    subject=meta["subject"],
                    scopes=set(meta.get("scopes", ["read"])),
                    expires_at=float(exp) if isinstance(exp, (int, float)) else None,
                    label=meta.get("label"),
                    principal_kind=self._infer_principal_kind(meta),
                )

    @staticmethod
    def _infer_principal_kind(meta: dict[str, Any]) -> str:
        """存量记录（无 principal_kind 字段）装载时推断身份族（批 2 迁移规则）。

        subject ∈ {owner, shared} ⇒ `login`（WebUI 登录面签发）；其余 ⇒ `agent`（配对）。
        ⛔ `developer` 只新发、不回溯——给存量记录回溯成 developer = 间接提权。
        """
        pk = meta.get("principal_kind")
        if isinstance(pk, str) and pk:
            return pk
        subject = str(meta.get("subject", ""))
        return "login" if subject in ("owner", "shared") else "agent"

    def _persist_issued(
        self, token: str, subject: str, scopes: list[str], expires_at: float | None = None,
        label: str | None = None, principal_kind: str = "agent",
    ) -> None:
        if not self._issued_path:
            return
        p = Path(self._issued_path)
        data: dict[str, Any] = {}
        if p.is_file():
            # 第十轮 F10①：文件存在却读不出来时**拒写**，不退回空字典。此前 `data = {}`
            # 会让这次写入只落"内存里那一条"，等于一次签发把已签发令牌名单全部抹掉。
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ValueError(
                    f"{p.name} 已损坏，拒绝写入以保护已签发令牌名单；"
                    f"请修复或隔离该文件后重试（{type(exc).__name__}）"
                ) from exc
        if not isinstance(data, dict):
            raise ValueError(f"{p.name} 形状不是对象，拒绝写入以保护已签发令牌名单")
        # 批 2：存量文件可能还残留明文键（迁移窗口），先就地补摘要键，避免后面
        # `data[_digest(token)] = entry` 与明文旧键并存导致一枚令牌两条记录。
        migrated: dict[str, Any] = {}
        for k, v in data.items():
            migrated[k if _looks_digest(str(k)) else _digest(str(k))] = v
        data = migrated
        # 顺带把已过期的条目从落盘文件里剔掉：否则重启后 `_load_issued_file`
        # 读回来的全是过期令牌，文件单调增长（新增审计 BUG-19）。
        now = time.time()
        for tok in list(data):
            meta = data.get(tok)
            exp = meta.get("expires_at") if isinstance(meta, dict) else None
            if isinstance(exp, (int, float)) and exp <= now:
                data.pop(tok, None)
        entry: dict[str, Any] = {"subject": subject, "scopes": scopes, "principal_kind": principal_kind}
        if expires_at is not None:
            entry["expires_at"] = expires_at
        if label is not None:
            entry["label"] = label
        # 批 2：落盘键 = 摘要，绝不写明文令牌。
        data[_digest(token)] = entry
        _atomic_write_text(p, json.dumps(data, ensure_ascii=False, indent=2))

    def _rewrite_issued(self, subject: str, new_name: str | None = None, drop: bool = False, except_token: str | None = None) -> None:
        if not self._issued_path or not Path(self._issued_path).is_file():
            return
        p = Path(self._issued_path)
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # AF50c-R：旧实现静默 return —— 调用方（revoke_by_subject/rename_subject）
            # 内存侧已经改完并上报成功，盘上文件却原样未动 ⇒ 重启后被"删除/改名"的令牌
            # 原样复活。与 _persist_issued 同口径：文件损坏即拒写并抛错，让 API 面明确失败，
            # 不许"看似成功、重启复活"。
            raise ValueError(
                f"{p.name} 已损坏，拒绝改写已签发令牌名单；"
                f"请修复或隔离该文件后重试（{type(exc).__name__}）"
            ) from exc
        if not isinstance(data, dict):
            # AF50c-R：旧实现 `list(data.items())` 在 try 之外——形状不对时 AttributeError
            # 直接穿出调用方。统一在形状校验处抛可读错误。
            raise ValueError(f"{p.name} 形状不是对象，拒绝改写已签发令牌名单")
        changed = False
        # 批 2：盘上键是摘要；except_token 来自调用方（明文），先摘要再比对。
        except_dg = _digest(except_token) if except_token else None
        for tok, meta in list(data.items()):
            if except_dg is not None and tok == except_dg:
                continue
            if isinstance(meta, dict) and meta.get("subject") == subject:
                if drop:
                    data.pop(tok, None)
                    changed = True
                elif new_name:
                    meta["subject"] = new_name
                    changed = True
        if changed:
            _atomic_write_text(
                Path(self._issued_path),
                json.dumps(data, ensure_ascii=False, indent=2),
            )


# ─────────────────────────────────────────────────────────────────────
# v1.9.0 用户 WebUI：配对码 / 授权码 文件存储（与 .auth/revoked.json 同目录）
# ─────────────────────────────────────────────────────────────────────


def _rand6() -> str:
    """8 位配对/授权码（纯数字，熵 10^8，不可离线枚举；10^6 空间 0.5s 已被证实可达）。

    N-P0-sec（安全审计 fp-authcode-bruteforce）：原 6 位被证实可 0.5s 穷举。
    8 位仍为纯数字（便于口述/匹配已有 UX），但空间扩大 100x，单进程枚举约 50s。
    调用点零 ^[0-9]{6}$ 正则约束——与 6 位老码向后兼容（老码仍可 validate/consume）。
    """
    return "".join(secrets.choice("0123456789") for _ in range(8))


@dataclass
class PairCode:
    code: str
    agent_name_hint: str
    created_at: float
    expires_at: float
    consumed: bool = False
    pushed: bool = False  # SSE 是否已推送给前端


def _purge_expired_codes(codes: dict[str, Any]) -> int:
    """从「码 → 记录」字典里剔除已过期条目，返回剔除条数。

    两族码仓（`PairCodeStore` / `AuthCodeStore`）此前只在 `consume` 里判断过期并
    `return None`，**从不删除**——过期码永远留在内存字典与落盘文件里，配合每次
    `create` 都全量重写，文件单调增长（新增审计 BUG-19）。调用方须持锁。
    """
    now = time.time()
    dead = [
        code
        for code, rec in codes.items()
        if isinstance(rec, dict)
        and isinstance(rec.get("expires_at"), (int, float))
        and rec["expires_at"] <= now
    ]
    for code in dead:
        codes.pop(code, None)
    return len(dead)


def _refuse_when_list_file_unreadable(path: Path, label: str) -> None:
    """落盘前的护栏：`path` 存在却读不出数组形状时**拒写**（fail-closed）。

    第十轮 F10①②③ 那一族：两族码仓的 `_load` 在文件损坏时只会静默得到空字典，而
    `_persist` 是**整档由内存快照重写**——一次 `create` 就把盘上全部待发码抹掉，
    现场也没了。护栏必须紧邻落盘调用（第十轮 W30：装在调用方不走的方法上等于没装）。

    文件不存在是正常首写场景，不拒；0 字节/纯空白文件也不拒——那是**空名单**，不是损坏
    （AF53：旧实现把空文件当"已损坏"拒写，`create()` 必经此护栏 ⇒ 配对码/授权码签发锁死）。
    放行刻意做窄：只有"0 字节或纯空白"放行，"有内容却读不出来"仍拒（AF76 的教训）。
    """
    if not path.is_file():
        return
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        raise ValueError(
            f"{path.name} 已损坏，拒绝写入以保护已有{label}；"
            f"请修复或隔离该文件后重试（{type(exc).__name__}）"
        ) from exc
    if not raw.strip():
        # 空/纯空白文件按空名单处理，放行整档重写（见 docstring AF53）
        return
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ValueError(
            f"{path.name} 已损坏，拒绝写入以保护已有{label}；"
            f"请修复或隔离该文件后重试（{type(exc).__name__}）"
        ) from exc
    if not isinstance(data, list):
        raise ValueError(f"{path.name} 形状不是数组，拒绝写入以保护已有{label}")


#: ── 配对 bootstrap 匿名端点的参数（裁定 20261006 §一 钉死，AF 不自签）──
#: 数值来自 `关键决策部/decisions/20261006-AF配对与段间封顶与DPP四件与MA三件-裁定.md` §一 追问两答；
#: 要改这三个数，先把裁定改掉（测试里有钉值的那一条会红）。
BOOTSTRAP_REQUEST_PER_MIN = 6    # 每 IP 每分钟 ≤6 次配对请求
BOOTSTRAP_REDEEM_PER_MIN = 10    # redeem 放宽到 10 次：裁定明写"要容忍用户口述打错一次"
BOOTSTRAP_LOCK_S = 300           # 超限即锁该 IP 于**该端点** 5 分钟
LOGIN_PER_MIN = 10               # 登录/注册每 IP 每分钟 ≤10 次（手输错几次够用，爆破被锁）
LOGIN_LOCK_S = 300               # 超限即锁该 IP 于该端点 5 分钟（与 bootstrap 同档）


class PairCodeStore:
    """配对码存储：落盘 `.auth/pair_codes.json`，进程内读缓存 + 写时落盘。

    单次短时效（默认 5 分钟）。agent 经 MCP `af_request_pair` 触发生成，
    后端经 SSE `/api/mcp/pair-request` 推送给前端弹窗；agent 再用
    `af_pair(code)` 兑换运行时签发的 Bearer 令牌。
    """

    def __init__(self, path: str | Path, *, pause_path: str | Path | None = None) -> None:
        self._path = Path(path)
        #: owner 侧"暂停接受配对请求"开关的落盘位置（裁定 20261006 §一）。默认与码文件同目录，
        #: 但**独立成文件**：`pair_codes.json` 现在是"活的配对码"的集合，往里塞一个标量会把
        #: 那份 list 的形状改成 dict，牵连 `_load` 与 BUG-19 的过期剔除口径。
        self._pause_path = (
            Path(pause_path)
            if pause_path is not None
            else self._path.parent / "pair_accepting.json"
        )
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
            now = time.time()
            for c in data:
                if isinstance(c, dict) and "code" in c:
                    # 过期码不载入：否则 pair_codes.json 只增不减，重启后满盘都是
                    # 过期码（新增审计 BUG-19）
                    exp = c.get("expires_at")
                    if isinstance(exp, (int, float)) and exp <= now:
                        continue
                    self._codes[c["code"]] = c

    def _purge_expired(self) -> int:
        return _purge_expired_codes(self._codes)

    def _persist(self) -> None:
        # 第十轮 F10②：整档由内存快照重写，所以落盘前必须确认盘上那份还读得出来。
        # `_load` 在文件损坏时只会得到空字典，若此处照写，一枚新配对码就把全部待发配对码抹掉。
        _refuse_when_list_file_unreadable(self._path, "配对码")
        _atomic_write_text(
            self._path,
            json.dumps(list(self._codes.values()), ensure_ascii=False, indent=2),
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
        # 跨进程同步：**写前必须重读**。`_persist` 是全量覆盖写，不重读就会用陈旧快照
        # 把别的进程刚写入的码整片抹掉——用户看到弹窗里的 6 位码随即判无效，
        # 配对流程走不通且日志无痕（新增审计 BUG-20，与 consume/get 的读侧对称）。
        self._load()
        with self._lock:
            self._purge_expired()  # 顺带清掉历史过期码，文件不再单调增长（BUG-19）
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
        self._load()  # 写前重读，同 create（BUG-20）
        with self._lock:
            rec = self._codes.get(code)
            if rec is not None:
                rec["pushed"] = True
                self._persist()

    # ── owner 侧"暂停接受配对请求"开关（裁定 20261006 §一）────────────
    def pause(self) -> None:
        self._write_paused(True)

    def resume(self) -> None:
        self._write_paused(False)

    def _write_paused(self, paused: bool) -> None:
        _atomic_write_text(
            self._pause_path,
            json.dumps({"accepting": not paused, "updated_at": time.time()}),
        )

    def is_accepting(self) -> bool:
        """每次现读磁盘：开关由 HTTP 面写、由 MCP 面与匿名 bootstrap 端点读，跨进程不许拿进程缓存。

        三档读数，**缺一档就会出事**：
        - 文件不存在 ⇒ True。这是出厂默认（用户从没按过暂停）。把它也读成"暂停"，
          新装的 AF 会连一次配对都走不通——功能被自己的 fail-closed 口径打死；
        - 文件在但读不出/形状不对 ⇒ False（fail-closed）。这道开关是用户对"弹窗骚扰"唯一的
          止血手段，一份损坏的开关文件若被读成"仍在接受"，用户按下暂停却照旧被打扰，且无处留痕；
        - 其余按 `accepting` 字段，**只有 `True` 才算接受**（缺键/`null`/字符串都不算）。
        """
        if not self._pause_path.is_file():
            return True
        try:
            data = json.loads(self._pause_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if not isinstance(data, dict):
            return False
        return data.get("accepting") is True


def request_pair_code(pair_store: PairCodeStore, agent_name_hint: str) -> PairCode:
    """配对第 1 步的**唯一实现**：MCP 工具 `af_request_pair` 与匿名 HTTP bootstrap 端点共用。

    写两份的原因不会缺席这里——两份各自漂移之后，"码只显示给用户"这条不变量就会在其中一份里失效。
    """
    hint = (agent_name_hint or "").strip() or "未知 agent"
    return pair_store.create(hint)


def redeem_pair_code(
    pair_store: PairCodeStore,
    registry: TokenRegistry | None,
    code: str,
    agent_name: str = "",
) -> tuple[str, str | None, str | None]:
    """配对第 2 步的唯一实现：消费配对码并签发 Bearer。

    返回 `(状态, 令牌, 主体)`；状态 ∈ {`ok`,`no_code`,`invalid`,`not_ready`}——调用方决定
    把它翻成 MCP 的 `{ok:false,error}` 还是 HTTP 的 4xx，语义分层留在各自的面里，
    但"consume 之后才签发"这一条顺序只有一份。

    **配对令牌不设过期（2026-10-23 出资人裁定）**：配对一次长期有效，只有撤销
    （`revoke_by_subject` / 单令牌 revoke）才失效——与长期授权码同语义（gap #9 的
    分型口径）。此前 BUG-19 修复给所有 `issue_for_agent` 签发统一打了 24h TTL，
    把「登录令牌该有 TTL」误套到配对令牌上 ⇒ agent 每天被迫重新配对。BUG-19 的
    凭证库增长担忧对配对不成立：低频操作 + `MAX_ISSUED` 上限 + 撤销机制兜底。

    `not_ready` 只在**注册表对象都没有**时给（`registry is None`）。不要在"没配置任何令牌"时
    也拒绝：那档下全站本来就是公开的（`TokenRegistry.enabled` 的口径），此时拒发配对令牌
    只会把开发档的配对流程打断，换不到任何安全收益。
    """
    trimmed = (code or "").strip()
    if not trimmed:
        return "no_code", None, None
    pc = pair_store.consume(trimmed)
    if pc is None:
        return "invalid", None, None
    subject = (agent_name or "").strip() or pc.agent_name_hint or "agent"
    if registry is None:
        return "not_ready", None, None
    # ttl_s=0 ⇒ `expires_at=None`（`issue_for_agent` 的口径：ttl<=0 不过期）——
    # 与 WebUI 登录令牌（走默认 24h）在同一签发点上分型。
    return "ok", registry.issue_for_agent(subject, ttl_s=0), subject


@dataclass
class AuthCode:
    code: str
    kind: str  # "long" | "short"
    created_at: float
    expires_at: float | None  # None = 未记到期；长期码另按绝对上限补出（见 `list`/`validate`）
    revoked: bool = False
    consumed: bool = False
    failed_attempts: int = 0
    locked_until: float | None = None
    # v2.6 语义修复（gap #9）：长期码不消耗，只累计使用次数（consume 时 +1）。
    use_count: int = 0


#: 非 owner 面看到的定形掩码（裁定 20261004 §一 F-3："非 owner 面只给掩码 + 状态"）。
#: 一个真实字符都不给，长度也不跟着码长变——授权码是纯数字（新码 10^8、兼容的老码 10^6），
#: "泄漏长度"就等于把攻击面从 10^8 指到 10^6。同理不给截断哈希指纹：这点熵离线穷举
#: 一遍就能反查，指纹在低熵值上不是单向的。
CODE_MASK = "*" * 8


class AuthCodeStore:
    """部署授权码存储：落盘 `.auth/auth_codes.json`。

    长期码（kind="long"，可撤销，另有 `AUTOFORGE_AUTH_LONGCODE_TTL_DAYS` 的绝对上限，
    默认 180 天、`0` 为显式关）与短期码（kind="short"，ttl 5–30 分钟）。
    agent 部署（af_save）持有效码走路径 A 直部署，否则路径 B 入待批队列。
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._codes: dict[str, dict[str, Any]] = {}
        # 存储级失败尝试窗口（进程内存量，不落盘）：单码计数挡不住"试不存在的码"这一族。
        # AF38-R：窗口基准必须用单调钟——墙钟被 NTP 向前阶跃一次，窗口就被判"已过期"
        # 清零，防爆破 fail-open。落盘字段（expires_at/locked_until）仍是墙钟（跨重启要可解析）。
        self._window_start = 0.0
        self._window_hits = 0
        # 单码软锁定的进程内单调钟副本：盘上 locked_until 是墙钟（跨重启恢复用），
        # 单调钟副本抗 NTP 阶跃；validate 取两者更严格者（AF38-R 双判据）。
        self._lockout_until_mono: dict[str, float] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.is_file():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(data, list):
            now = time.time()
            for c in data:
                if isinstance(c, dict) and "code" in c:
                    # 过期码不载入：同 PairCodeStore，auth_codes.json 也不能只增不减
                    # （新增审计 BUG-19）
                    exp = c.get("expires_at")
                    if isinstance(exp, (int, float)) and exp <= now:
                        continue
                    self._codes[c["code"]] = c

    def _purge_expired(self) -> int:
        return _purge_expired_codes(self._codes)

    def _persist(self) -> None:
        # 第十轮 F10③：与配对码同形态——`_load` 遇损坏静默留空，这里整档重写。
        # 一次 `create` 就把盘上全部已签发授权码抹掉，且现场一同消失。
        _refuse_when_list_file_unreadable(self._path, "授权码")
        _atomic_write_text(
            self._path,
            json.dumps(list(self._codes.values()), ensure_ascii=False, indent=2),
        )

    #: 失败计数阈值：超过后软锁定 LOCKOUT_S 秒
    FAILURE_THRESHOLD = 10
    #: 锁定时长（秒）
    LOCKOUT_S = 300
    #: 存储级失败尝试窗口（秒）——单位是"整个码库", 不是单枚码
    ATTEMPT_WINDOW_S = 60.0
    #: 窗口内允许的失败尝试次数；超限后 `validate()` 对任何码都返回 False
    ATTEMPT_LIMIT = 10
    #: `_lockout_until_mono` 的硬上限（AF38-R：单调钟软锁定副本是进程内字典，
    #: 键空间随"被锁过的码数"开，给一条封顶腿 + `_prune_lockout_mono` 写侧回收，
    #: 与 `RateLimiter._blocked` 的登记形状一致；TTL 腿就是 `LOCKOUT_S`）。
    LOCKOUT_MONO_MAX = 256

    def _prune_lockout_mono(self, now_mono: float | None = None) -> None:
        """写侧回收 `_lockout_until_mono`：先丢已到期的；仍超硬上限再按到期时间最早先丢。

        到期条目本就在 `validate()` 里懒清，但判据 E 不许"纯写不读也不回收"——
        一枚被锁后再也没人来 validate 的码，它的副本要在后续 record_failure 的写入口自己退出去。
        """
        if not self._lockout_until_mono:
            return
        now_mono = time.monotonic() if now_mono is None else now_mono
        expired = [c for c, until in self._lockout_until_mono.items() if now_mono >= until]
        for c in expired:
            self._lockout_until_mono.pop(c, None)
        # 为即将写入的新条目留位：prune 在 insert 之前跑，故以 >= 为准，写完不超硬上限。
        while len(self._lockout_until_mono) >= self.LOCKOUT_MONO_MAX:
            oldest = min(self._lockout_until_mono, key=self._lockout_until_mono.get)
            self._lockout_until_mono.pop(oldest, None)

    def create(self, kind: str, ttl_minutes: int | None = None) -> AuthCode:
        now = time.time()
        if kind == "long":
            # DCD 裁定 20261004 §一 Q1=A：长期码保留"长期"语义，但必须带可配绝对上限
            # （默认 180 天，`AUTOFORGE_AUTH_LONGCODE_TTL_DAYS=0` 显式关）。
            # 无限寿命把"枚举到一个还活着的码"的收益钉在未重启的窗口上；上限是给它的兜底。
            ttl_days = _longcode_ttl_days()
            expires_at = now + ttl_days * 86400 if ttl_days > 0 else None
        else:
            expires_at = now + (ttl_minutes or 5) * 60
        rec = {
            "code": _rand6(),
            "kind": kind,
            "created_at": now,
            "expires_at": expires_at,
            "revoked": False,
            "consumed": False,
            "failed_attempts": 0,
            "locked_until": None,
            "use_count": 0,
        }
        # 写前重读：`_persist` 是全量覆盖写，不重读会用陈旧快照抹掉别的进程写入的码
        # （新增审计 BUG-20——用户看到弹窗里的码随即被判无效，配对走不通且日志无痕）
        self._load()
        with self._lock:
            self._purge_expired()  # 同 PairCodeStore：清历史过期码（BUG-19）
            self._codes[rec["code"]] = rec
            self._persist()
        return AuthCode(**rec)

    def list(self, *, reveal: bool = True) -> list[dict[str, Any]]:
        """返回全部授权码摘要；`reveal=False` 时明文换成定形掩码（`CODE_MASK`）。

        `age_s`（距生成多久）与按绝对上限补出的 `expires_at` 是 DCD 裁定 20261004 §一 Q1
        要的两项：owner 面必须看得见"这枚长期码还活多久"，否则 180 天上限只是后台数字。

        掩码只挡"看得见"，不挡"改得动"：状态字段（kind/created/expires/revoked/consumed/
        use_count/failed_attempts）两档都给，撤销一枚码仍然要求手里真有那枚码。谁可以 `reveal=True`
        由 API 面判（`/api/user/auth-codes` 的 owner 面），存储层不自备身份概念。
        """
        # FFL 20261008 发现：list() 不调用 _load()，内存快照从不刷新，
        # 已消费码的 consumed 字段永远显示 false。与 revoke()/validate()/consume() 同口径，读前重载。
        self._load()
        now = time.time()
        with self._lock:
            out: list[dict[str, Any]] = []
            for c in self._codes.values():
                exp = _longcode_expires_at(c)
                out.append(
                    {
                        "code": c["code"] if reveal else CODE_MASK,
                        "kind": c["kind"],
                        "created_at": c["created_at"],
                        "expires_at": c.get("expires_at"),
                        "age_s": round(now - float(c.get("created_at", now)), 1),
                        "expires_at_effective": exp,
                        "expires_in_s": round(exp - now, 1) if exp is not None else None,
                        "revoked": c.get("revoked", False),
                        "consumed": c.get("consumed", False),
                        "use_count": c.get("use_count", 0),
                        "failed_attempts": c.get("failed_attempts", 0),
                        "locked_until": c.get("locked_until"),
                    }
                )
            return out

    def revoke(self, code: str) -> bool:
        self._load()  # 写前重读：全量覆盖写不重读会抹掉别的进程写入的码（BUG-20）
        with self._lock:
            rec = self._codes.get(code)
            if rec is None or rec.get("revoked"):
                return False
            rec["revoked"] = True
            self._persist()
            return True

    def validate(self, code: str) -> bool:
        """有效（存在、未撤销、未过期、未锁定、未消耗）返回 True。

        N-P0-sec 修复：validate 现在**仅查询**，不修改状态（消耗/失败计数由调用方显式调 consume/record_failure）。
        "仅查询"不含窗口阈值读数：超限后一律 False（含真实有效码），失败方向是回落人审队列，
        不是放开快速通道。调用方须在同一条失败分支上 `record_failure`，否则该面不受窗口保护
        ——两面（MCP / HTTP）的接线由 `tests/unit/test_v0_8_auth.py` 钉住。

        R17-01：读前重载盘——长驻单例（MCP 进程）必须能看到其他进程（API/管理员）的撤销/消耗，
        否则已撤销的码在重启前持续有效。

        v2.6（gap #9）：`consumed` 只会咬短期码（长期码 consume 不再置 consumed，见 `consume`）。
        历史上已被 v2.5 语义消耗掉（盘上 consumed=true）的长期码仍按已消耗处理，**不自动复活**——
        无法与真烧掉的码区分，部署快速通道上自动复活凭据是错误默认；作废重生成即可。
        """
        self._load()
        with self._lock:
            # AF38-R：进程内窗口基准用单调钟（NTP 阶跃不动它）
            now_mono = time.monotonic()
            if (
                now_mono - self._window_start < self.ATTEMPT_WINDOW_S
                and self._window_hits >= self.ATTEMPT_LIMIT
            ):
                return False
            rec = self._codes.get(code)
            if rec is None or rec.get("revoked") or rec.get("consumed"):
                return False
            # 软锁定双判据（AF38-R）：单调钟副本抗阶跃 + 盘上墙钟跨重启恢复，取更严格者
            # （任一仍在锁定期内就拒）。单调钟侧到期顺手清掉，防 _lockout_until_mono 无界增长。
            mono_until = self._lockout_until_mono.get(code)
            if mono_until is not None:
                if now_mono < mono_until:
                    return False
                self._lockout_until_mono.pop(code, None)
            locked_until = rec.get("locked_until")
            if locked_until is not None and time.time() < locked_until:
                return False
            exp = _longcode_expires_at(rec)
            if exp is not None and time.time() > exp:
                return False
            return True

    def consume(self, code: str) -> bool:
        """消耗一次授权码使用额度：短期码一次性置 consumed；长期码不消耗，只累计 use_count。

        v2.6 语义修复（gap #9，2026-10-22）：设计意图是「长期码…不撤销一直可用」
        （docs/design/咨询_用户WebUI后端确认_20260924.md:37）；v2.5 的 fp-authcode-bruteforce
        修复把一次性 consume 无差别套到两种码上，长期码用一次就废。本处按 kind 分型：
        长期码的抗重放改由 高熵（10^8）+ 存储级失败窗口（ATTEMPT_LIMIT/ATTEMPT_WINDOW_S）
        + 单码软锁（record_failure）+ 撤销 + 180 天绝对上限共同承担，不再依赖一次性消耗
        （对 docs/audit/归档/AutoForge_内核安全与架构深度审计报告.md HIGH-1 与 R17-01
        「一次性消耗」口径的有意修订）。
        kind 缺失的历史记录按短期码处理（保守缺省 = 维持 v2.5 一次性语义）。
        """
        self._load()  # 写前重读，同 create（BUG-20）
        with self._lock:
            rec = self._codes.get(code)
            if rec is None:
                return False
            if rec.get("consumed") or rec.get("revoked"):
                return False
            if rec.get("kind") == "long":
                # 长期码：记录使用、不清码（.get 兼容 v2.5 时期没有 use_count 的记录）
                rec["use_count"] = rec.get("use_count", 0) + 1
            else:
                # 短期码（及缺 kind 的历史记录）：一次性消耗，防重放语义不变
                rec["consumed"] = True
            self._persist()
            return True

    def record_failure(self, code: str) -> bool:
        """记录一次错误授权码尝试。

        单码计数仅对**已存在**的码有效（未知码不落盘，避免服务端暴露有效码集合）。
        超过 FAILURE_THRESHOLD 次后进入 LOCKOUT_S 秒软锁定。

        **未知码走存储级窗口**（`ATTEMPT_LIMIT` 次/`ATTEMPT_WINDOW_S` 秒）：单码那层挡的是
        "反复试同一枚真实码"，而 10^8 码空间的实际枚举路径是"试不存在的码"——它在单码层
        根本落不到任何记录上，审计侧实测吞吐 195 万次/秒（fp-authcode-bruteforce 证据 [5]），
        8 位熵只把穷举从 0.51 秒推到约 51 秒，量级不够。窗口计数在进程内存，重启即清零，
        这是登记在册的边界（主防线是高熵 + 失败窗口/软锁 + 撤销；长期码可重复使用，
        短期码一次性——v2.6 分型语义，gap #9）。

        返回：True = 本次失败使**该码**进入锁定（调用方应提示用户稍后再试）。
        """
        now_wall = time.time()      # 盘上 locked_until 落盘字段（跨重启可解析），保留墙钟
        now_mono = time.monotonic()  # 进程内窗口/单调钟锁定副本（AF38-R）
        self._load()  # 写前重读，同 create（BUG-20）
        with self._lock:
            if now_mono - self._window_start >= self.ATTEMPT_WINDOW_S:
                self._window_start = now_mono
                self._window_hits = 0
            self._window_hits += 1
            rec = self._codes.get(code)
            if rec is None:
                return False  # 未知码不落盘，无侧信道
            if rec.get("consumed") or rec.get("revoked"):
                return False
            rec["failed_attempts"] = rec.get("failed_attempts", 0) + 1
            locked = False
            if rec["failed_attempts"] >= self.FAILURE_THRESHOLD:
                if rec.get("locked_until") is None:
                    # 盘上仍写墙钟（重启后能解析），同时记一份单调钟到期（本进程内抗阶跃）。
                    # 写前先回收：纯写不读也不许这张表跟着"被锁过的码数"无界长。
                    self._prune_lockout_mono(now_mono)
                    rec["locked_until"] = now_wall + self.LOCKOUT_S
                    self._lockout_until_mono[code] = now_mono + self.LOCKOUT_S
                    locked = True
            self._persist()
            return locked


class RateLimiter:
    """IP + 主体双维度固定窗口限速。

    用法：对每个请求分别 `check("ip:<client>")` 与（若已认证）`check("subj:<subject>")`。
    任一维度超限即抛 `RateLimitExceeded`。
    """

    #: 最大保留的 key 数量（P0-10：防止 _hits 无界增长导致内存泄漏）
    _MAX_KEYS = 10000
    #: `_blocked` 的硬上限（与 `_MAX_KEYS` 同量级、独立命名：两棵树的生命周期不同，
    #: 一个键可以从 `_hits` 被清掉却仍在 `_blocked` 里锁着）。
    LOCK_MAX_KEYS = 10000

    def __init__(self, per_minute: int = 1000, window_s: int = 60, lock_s: int = 0) -> None:
        self.per_minute = per_minute
        self.window_s = window_s
        #: 超限后的**锁定**时长（0 = 不锁定，只按窗口滑动）。裁定 20261006 §一 给配对 bootstrap
        #: 要的是"超限即锁该 IP 于**该端点** 5 分钟"——窗口限速单独存在时，攻击者等窗口滚过
        #: 就能继续按 6 次/分钟的速度刷，那不是"锁"。这一枚同时是 `_blocked` 的 TTL 腿。
        self.lock_s = lock_s
        self._hits: dict[str, list[float]] = {}
        self._blocked: dict[str, float] = {}
        self._lock = threading.Lock()

    def _prune_blocked(self, now: float) -> int:
        """回收 `_blocked`：先按 TTL 清到期项，再按硬上限淘汰"最早解锁"的项。返回清掉的到期条数。

        为什么不能只靠 `check()`：那里只删**被挡到的那一条**。攻击者每请求换一个源 IP 时，
        旧 IP 的锁定项没人再去碰，就永远留在表里——"纯写不读"的那一半（第六轮审计 §三）。
        调用点在超限时那条罕见路径上（每个被锁的 IP 至多一次），不在正常请求路径上。
        """
        dead = [k for k, until in self._blocked.items() if until <= now]
        for k in dead:
            del self._blocked[k]
        while len(self._blocked) > self.LOCK_MAX_KEYS:
            self._blocked.pop(min(self._blocked, key=self._blocked.get), None)
        return len(dead)

    def _cleanup_expired(self, now: float) -> None:
        """清理所有已过期的 key（P0-10：防止 _hits 无界增长）。"""
        cutoff = now - self.window_s
        expired = [k for k, v in self._hits.items() if not v or v[-1] <= cutoff]
        for k in expired:
            del self._hits[k]
        self._prune_blocked(now)

    def check(self, key: str) -> None:
        # AF38-R：_hits/_blocked 全是进程内存量（不落盘、不跨重启），基准必须用单调钟——
        # 墙钟被 NTP 向前阶跃一次，已记录的命中全部被判过期 ⇒ 限速 fail-open。
        now = time.monotonic()
        with self._lock:
            until = self._blocked.get(key)
            if until is not None:
                if until > now:
                    raise RateLimitExceeded(
                        f"该维度已被锁定 {int(until - now)} 秒（维度 {key!r}）"
                    )
                del self._blocked[key]
            if self.per_minute <= 0:
                return
            # P0-10：超过最大 key 数时触发全量清理
            if len(self._hits) >= self._MAX_KEYS:
                self._cleanup_expired(now)
            # R7-02：清理后若仍超限（所有 key 都未过期），淘汰最旧的 key（FIFO），
            # 把软上限变成硬上限，防止 _hits 涨至 3×。
            if len(self._hits) >= self._MAX_KEYS:
                oldest = min(self._hits, key=lambda k: self._hits[k][0] if self._hits[k] else 0)
                del self._hits[oldest]
            hits = self._hits.setdefault(key, [])
            cutoff = now - self.window_s
            if hits and hits[0] <= cutoff:
                hits[:] = [t for t in hits if t > cutoff]
            if len(hits) >= self.per_minute:
                if self.lock_s > 0:
                    # 先回收再挂号：写侧自己负责让表缩回去，不等那个 IP 再来一次
                    self._prune_blocked(now)
                    self._blocked[key] = now + self.lock_s
                    # 锁定即刻生效：窗口里的旧命中不再有意义，清掉免得解锁后再攒一次瞬时红
                    self._hits.pop(key, None)
                raise RateLimitExceeded(
                    f"请求过于频繁（限速 {self.per_minute}/min，维度 {key!r}"
                    + (f"，已锁定 {self.lock_s} 秒）" if self.lock_s > 0 else "）")
                )
            hits.append(now)


# ── 管理员用户存储（登录正规化：首次设定账号密码）──

class AdminUserStore:
    """单管理员用户存储：用户名 + PBKDF2 密码哈希，落盘原子写。

    设计：
    - 单用户系统，只有一个管理员账号
    - 首次启动无用户 → 允许注册（/api/auth/register）
    - 已有用户 → 注册接口拒绝，必须登录
    - 密码用 PBKDF2-HMAC-SHA256，100000 次迭代，随机盐
    - 落盘到 {store_root}/.auth/admin.json，权限 0600
    """

    PBKDF2_ITERATIONS = 100000
    SALT_BYTES = 16

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path else None
        self._lock = threading.Lock()
        self._username: str | None = None
        self._salt: str | None = None
        self._hash: str | None = None
        # AF13-R：admin.json 在却读不出/形状不对时置位——此时无法证明"到底有没有管理员"，
        # 必须按"已有管理员但无法核验"fail-closed（登录一律拒、注册一律拒），绝不能当
        # "没有管理员"fall-through：旧口径下文件一坏，/api/auth/login 的未注册兼容模式就给
        # 任意非空凭据发 read+write+live 全权限令牌（原管理员反被 401），register 端点
        # 也能让任何人注册成管理员并落盘（AF13b-R）。
        self._poisoned = False
        self._load()

    def _load(self) -> None:
        if not self._path or not self._path.is_file():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # 文件损坏 ≠ 没有用户。置位 poisoned，由 has_admin/verify/register 统一 fail-closed。
            self._poisoned = True
            return
        # AF13c-R：顶层不是对象（如 `[1,2,3]`）时 `data.get` 会抛 AttributeError 穿出
        # build_app() 让服务起不来。形状不对按损坏处理（置 poisoned），不再裸奔。
        if not isinstance(data, dict):
            self._poisoned = True
            return
        self._username = data.get("username")
        self._salt = data.get("salt")
        self._hash = data.get("hash")

    def _persist(self) -> None:
        if not self._path:
            return
        data = {
            "username": self._username,
            "salt": self._salt,
            "hash": self._hash,
            "iterations": self.PBKDF2_ITERATIONS,
            "algorithm": "pbkdf2_sha256",
        }
        _atomic_write_text(self._path, json.dumps(data, ensure_ascii=False, indent=2))

    @property
    def has_admin(self) -> bool:
        """是否已有管理员用户。

        文件在却读不出/形状不对时（`_poisoned`）返回 True：无法证明"没有管理员"时
        按"已有管理员"处理（AF13-R fail-closed）——拒绝一切登录与注册，直到运维修好/删掉
        admin.json。这与旧口径（损坏=没用户=放行注册+兼容登录）相反，是有意为之。
        """
        if self._poisoned:
            return True
        return bool(self._username and self._salt and self._hash)

    @property
    def username(self) -> str | None:
        return self._username

    @staticmethod
    def _hash_password(password: str, salt: str) -> str:
        dk = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt),
            AdminUserStore.PBKDF2_ITERATIONS,
        )
        return dk.hex()

    def register(self, username: str, password: str) -> bool:
        """注册管理员。只有当没有管理员时才允许。

        返回 True 表示注册成功，False 表示已有管理员（拒绝注册）。
        """
        if not username or not password:
            raise ValueError("用户名和密码不能为空")
        if len(username) > 64 or len(password) > 256:
            raise ValueError("用户名或密码过长")
        with self._lock:
            if self.has_admin:
                return False
            salt = secrets.token_hex(self.SALT_BYTES)
            self._username = username
            self._salt = salt
            self._hash = self._hash_password(password, salt)
            self._persist()
            return True

    def verify(self, username: str, password: str) -> bool:
        """验证用户名和密码。恒定时间比较。"""
        # AF13-R：poisoned 时 has_admin 虽为 True，凭据字段全空——显式 fail-closed 返回，
        # 不走到下面的哈希比较分支（语义更清晰，也防未来 has_admin 口径再变）。
        if self._poisoned or not self.has_admin:
            return False
        if not username or not password:
            return False
        with self._lock:
            expected_hash = self._hash or ""
            salt = self._salt or ""
            stored_user = self._username or ""
        # 恒定时间比较用户名
        if not hmac.compare_digest(username, stored_user):
            # 即使用户名不对也跑一次哈希，防止时序攻击推断用户名
            self._hash_password(password, salt or "00" * self.SALT_BYTES)
            return False
        actual_hash = self._hash_password(password, salt)
        return hmac.compare_digest(actual_hash, expected_hash)
