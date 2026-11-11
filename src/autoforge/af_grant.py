# src/autoforge/af_grant.py · 执行授权（grant）模型——v1.29 对话操控面第一批
"""执行授权（grant）：某个 agent 在一段时间内、在给定范围内**直接执行**设备动作的活契约。

缘起与依据（⛔ 本模块不自行发明产品语义，全部抄自裁定）：
- DCD `decisions/20261111-AF-对话操控执行授权模型-裁定.md`（六问全甲）：MVP 六字段
  `agent`／`rooms`／`device_scope`／`action_risk`／`expires_at`／`revoke`；`time_window`／
  `rate_limit` 进 v1.29.1（本模块**故意不预留**这两个字段的读写位，加了就是偷偷落档）。
- 设计稿 v5 §4.1–§4.3：资助模型是「双轨授权」——**码管部署（原体系不动）、grant 管执行**。
  grant 解决的是「用户不该每开一盏灯找一次码」，它**不**替代自动化部署的授权码体系。

四条不可越的边界（裁定 §五 ⛔ 清单，逐条落在这里，可以被判据点名）：

1. **⛔ agent 自声明范围**（`20261009` 判例：等于自己授权自己）。⇒ `create()` 强制要求
   `issued_by` 非空，且该值来自**人那一侧**（owner 令牌／长期码）；agent 自己的普通令牌
   签不出 grant。模块只认"签发者是谁"这一个事实，通道校验在服务层（`af_service.grant_*`）。
2. **⛔ 确认即升级**。越界只发**单次放行票**：`issue_confirm()` 出票 → `consume_confirm()`
   验票并**当场销毁**；`grant` 本体一个字段都不动。要更大范围，只能重新走签发。
3. **⛔ 高危动作进 grant**。`_L3_DOMAINS`（shell/python/hassio…）一律**硬拒**；`_L2_DOMAINS`
   里 `lock`／`valve`／`water_heater`／`alarm_control_panel` 一律**硬拒**（`GRANT_BANNED_DOMAINS`）。
   档位判定**只**走 `af_adapters.base.classify_action` —— ⛔ 本模块不另造风险表（判例②）。
   越界确认**不能**放行这两类：确认只对"范围越界"和"档位超授但 ≤ L2"生效。
4. **⛔ 第二套审计面**。留痕一律走既有 `af_audit.record_deploy`（`deploy_audit.jsonl`），
   事件类型 `GRANT_*` 与查询面的 `QUERY_READ` 同形（who/what/when/which agent）——
   本模块**不建**任何新的持久化审计文件。

有效期（Q1）：`expires_at` 默认 90 天，可续期（`renew()`）；长期码不过期 ≠ 授权不过期。
撤销（Q3）：「完成当前步、取消未执行步」——`revoke()` 立即生效，此后**任何新执行请求**即拒；
第一批只有【指令】面（同步单发，一次一步），已进入 `live_run` 的当前步允许自然完成，
"其后步全部取消"要等第二批 TaskOrder 有"多步"可取消时才落地（⛔ 本批不假装已有该形状）。
"""
from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from .af_adapters.base import (
    L0_READONLY,
    L1_IDEMPOTENT,
    L2_RISKY,
    L3_DANGEROUS,
    classify_action,
)
from .af_atomic import atomic_write_text

logger = logging.getLogger(__name__)

__all__ = [
    "ALL_ROOMS",
    "CONFIRM_TTL_S",
    "GRANT_BANNED_DOMAINS",
    "GRANT_FILE_REL",
    "GRANT_RISK_LEVELS",
    "GRANT_TTL_DAYS_DEFAULT",
    "GRANT_TTL_DAYS_MAX",
    "GrantCheck",
    "GrantError",
    "GrantStore",
    "collect_actions",
]

#: 盘上位置（与 `af_auth` 的 `.auth/` 同目录：凭据与授权同一处，别开第二处根）。
GRANT_FILE_REL = (".auth", "grants.json")
CONFIRM_FILE_REL = (".auth", "grant_confirms.json")

GRANT_VERSION = 1
#: 有效期默认 90 天（裁定 Q1：长期码不过期 ≠ 授权不过期）。上限两年，防手滑写个十年。
GRANT_TTL_DAYS_DEFAULT = 90.0
GRANT_TTL_DAYS_MAX = 730.0
#: 越界确认票的存活窗口：够 agent 把"要不要放行"问出口并拿到答，又不至于挂成长期后门。
CONFIRM_TTL_S = 300.0

#: grant 可声明的动作档上限集合。L3 永不入册（§三.1），所以这里只有三档。
GRANT_RISK_LEVELS = (L0_READONLY, L1_IDEMPOTENT, L2_RISKY)
_RISK_ORDER = {L0_READONLY: 0, L1_IDEMPOTENT: 1, L2_RISKY: 2, L3_DANGEROUS: 3}

#: `_L2_DOMAINS` 中**禁入 grant** 的四个域（裁定 §三.1 逐个点名）。
#: 只声明"哪几个 L2 域不许进"，⛔ 不复制 L2 表——档位仍由 `classify_action` 判。
#: `tests/unit/test_v129_grant_model_gate_20261111.py` 钉住 `GRANT_BANNED_DOMAINS ⊆ _L2_DOMAINS`，
#: 防止这张四元集与上游分级表漂成两套口径。
GRANT_BANNED_DOMAINS = frozenset({"lock", "valve", "water_heater", "alarm_control_panel"})

#: `rooms` 里的通配：全屋授权。⛔ 不写成空列表——空表示"没给范围"，那是拒签条件。
ALL_ROOMS = "all"

#: 不能当 `agent` 的主体名：owner/shared/unknow 是**人**那一侧的身份，不是被授权 agent。
_RESERVED_SUBJECTS = frozenset({"owner", "shared", "unknown", ""})

#: 账本写锁：同一进程内（serve 的线程池）把「读-改-写」串起来。
#: 为什么必须有：create／revoke／renew／bump_use／放行票都是**整份覆盖写**，两笔并发时
#: 后写者按自己那份旧快照覆盖，前一笔就凭空消失——对撤销而言，丢一笔等于「撤了但没撤」。
#: 本模块的实例是**按请求新建**的（`_grant_store()` 每次都 new），所以锁必须是模块级的。
_GRANT_LOCK = threading.Lock()

# ── 拒绝/确认码（判据与被拒文案共用一份，⛔ 各面自造字符串）─────────────────
GRANT_OK = "ok"
GRANT_NOT_FOUND = "GRANT_NOT_FOUND"
GRANT_REVOKED = "GRANT_REVOKED"
GRANT_EXPIRED = "GRANT_EXPIRED"
GRANT_AGENT_MISMATCH = "GRANT_AGENT_MISMATCH"
GRANT_ISSUER_REQUIRED = "GRANT_ISSUER_REQUIRED"
GRANT_AGENT_INVALID = "GRANT_AGENT_INVALID"
GRANT_RISK_INVALID = "GRANT_RISK_INVALID"
GRANT_RISK_L3 = "GRANT_RISK_L3"
GRANT_DOMAIN_BANNED = "GRANT_DOMAIN_BANNED"
GRANT_SCOPE_EMPTY = "GRANT_SCOPE_EMPTY"
GRANT_SCOPE_SHAPE = "GRANT_SCOPE_SHAPE"
GRANT_TTL_INVALID = "GRANT_TTL_INVALID"
GRANT_DEVICE_EXCLUDED = "GRANT_DEVICE_EXCLUDED"
GRANT_RISK_EXCEEDS = "GRANT_RISK_EXCEEDS"
GRANT_OUT_OF_SCOPE = "GRANT_OUT_OF_SCOPE"
GRANT_CONFIRM_REQUIRED = "GRANT_CONFIRM_REQUIRED"
GRANT_CONFIRM_INVALID = "GRANT_CONFIRM_INVALID"
GRANT_STORE_CORRUPT = "GRANT_STORE_CORRUPT"
GRANT_STORE_VERSION_MISMATCH = "GRANT_STORE_VERSION_MISMATCH"

#: 硬拒码：出现即整条请求拒，⛔ 不给"确认放行"这条路（矩阵第 4/5 行的形状）。
HARD_DENY_CODES = frozenset(
    {
        GRANT_NOT_FOUND,
        GRANT_REVOKED,
        GRANT_EXPIRED,
        GRANT_AGENT_MISMATCH,
        GRANT_RISK_L3,
        GRANT_DOMAIN_BANNED,
        GRANT_DEVICE_EXCLUDED,
        GRANT_STORE_CORRUPT,
        GRANT_STORE_VERSION_MISMATCH,
    }
)
#: 软拒码：请求本身合规但缺一次"本次放行"——只在越界/超授时出现（矩阵第 2/3 行）。
SOFT_DENY_CODES = frozenset({GRANT_OUT_OF_SCOPE, GRANT_RISK_EXCEEDS})


class GrantError(Exception):
    """授权面拒绝。`status` 供 HTTP/MCP 层映射（403＝要凭据/授权，400＝请求形状，500＝盘坏了）。"""

    def __init__(self, message: str, code: str = GRANT_NOT_FOUND, status: int = 403):
        super().__init__(message)
        self.code = code
        self.status = status


def _now_iso(now: float | None = None) -> str:
    return datetime.fromtimestamp(float(time.time() if now is None else now), timezone.utc) \
        .replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _norm_room(value: Any) -> str:
    """区域名归一：去空白、小写。`all` 单独保留。

    ⚠️ 只做**形状归一**，不做同义词映射（"主卧室"↔`zhu_wo_shi` 的归一归 DB 侧
    `butler/areas` 与本仓 `DeviceCatalog.area_of`）——两层各管一段，混起来就是第二份归一词典。
    """
    text = str(value or "").strip().lower()
    return ALL_ROOMS if text == ALL_ROOMS else text


def _domain_of(adapter: str, action: str) -> str:
    """取动作域，**与 `classify_action` 同一条切法**（`<domain>.<service>` 取前缀，否则退回适配器名）。

    这不是第二份风险表：它只把同一个字符串再切一次，用来查"这个域在不在禁入四元集里"。
    一致性由判据钉（禁入集里每个域经 `classify_action` 都必须是 L2）。
    """
    lowered = str(action or "").lower()
    return lowered.split(".", 1)[0] if "." in lowered else str(adapter or "").lower()


def collect_actions(automations: Iterable[Any]) -> list[tuple[str, str, Mapping[str, Any]]]:
    """把 IR 里的 `do` 节点摊成 `(adapter, action, params)` 列表（含 group 子图）。

    只收**真会写设备**的节点：`adapter` 与 `action` 都有值。⛔ 不看 `writes()`（那是实体集合，
    丢掉了"哪个动作"这一维），风险分级要的是动作本身。
    """
    out: list[tuple[str, str, Mapping[str, Any]]] = []
    stack = list(automations)
    while stack:
        auto = stack.pop()
        for node in getattr(auto, "nodes", {}).values():
            children = getattr(node, "children", ()) or ()
            if children:
                stack.extend(children)
            adapter = getattr(node, "adapter", None)
            action = getattr(node, "action", None)
            if adapter and action:
                out.append((str(adapter), str(action), dict(getattr(node, "params", {}) or {})))
    return out


@dataclass(frozen=True)
class GrantCheck:
    """一次执行请求的判定结果。⛔ 调用方不得只看 `ok` 就执行——必须看 `needs_confirm`。"""

    ok: bool
    grant_id: str
    agent: str
    risk: str
    denied: tuple[dict[str, str], ...] = ()
    out_of_scope: tuple[str, ...] = ()
    needs_confirm: bool = False

    @property
    def code(self) -> str:
        """单码视图：硬拒优先，其次确认，最后 ok（回执/日志用它，别自己再拼）。"""
        for item in self.denied:
            if item.get("code") in HARD_DENY_CODES:
                return str(item["code"])
        if self.needs_confirm:
            return GRANT_CONFIRM_REQUIRED
        return GRANT_OK

    @property
    def hard_denied(self) -> bool:
        return any(item.get("code") in HARD_DENY_CODES for item in self.denied)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "code": self.code,
            "grant_id": self.grant_id,
            "agent": self.agent,
            "risk": self.risk,
            "needs_confirm": self.needs_confirm,
            "out_of_scope": list(self.out_of_scope),
            "denied": [dict(x) for x in self.denied],
        }


class GrantStore:
    """grant 账本（`{store}/.auth/grants.json`）＋ 越界放行票（`grant_confirms.json`）。

    两个文件刻意分开：签发/撤销是低频写，放行票是执行期高频写；混在一个文件里，
    每次放行都会重写整份授权账本（一次磁盘抖动就能把授权面写坏）。
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.confirm_path = self.path.parent / CONFIRM_FILE_REL[1]

    # ── 读盘/落盘 ────────────────────────────────────────────────────
    def _load(self) -> dict[str, Any]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {"version": GRANT_VERSION, "grants": {}}
        except OSError as exc:
            # 读不出 ≠ 没有 grant：按最保守一侧拒（否则一次 IO 抖动就等于全场放行）。
            logger.error("GRANT_STORE_CORRUPT path=%s err=%s —— 授权账本读不出，本次一律拒", self.path, exc)
            raise GrantError("授权账本读不出（fail-closed）：请修复或隔离 grants.json",
                             GRANT_STORE_CORRUPT, 500) from exc
        try:
            parsed = json.loads(raw)
        except ValueError as exc:
            logger.error("GRANT_STORE_CORRUPT path=%s err=%s —— JSON 坏形，本次一律拒", self.path, exc)
            raise GrantError("授权账本已损坏（fail-closed）：请修复或隔离 grants.json",
                             GRANT_STORE_CORRUPT, 500) from exc
        if not isinstance(parsed, dict) or not isinstance(parsed.get("grants"), dict):
            logger.error("GRANT_STORE_CORRUPT path=%s shape=%s —— 形状不是 {grants:{}}",
                         self.path, type(parsed).__name__)
            raise GrantError("授权账本形状不对（fail-closed）：期望 {\"grants\": {...}}",
                             GRANT_STORE_CORRUPT, 500)
        version = parsed.get("version")
        if version is not None and version != GRANT_VERSION:
            # 不猜：可能是更新/更旧的程序写的。读面继续（不把服务打死），写面由调用方拒。
            logger.warning("GRANT_STORE_VERSION_MISMATCH path=%s on_disk=%r current=%r —— 请人工核对后再签",
                           self.path, version, GRANT_VERSION)
            parsed["_mismatch"] = True
        parsed.setdefault("version", GRANT_VERSION)
        return parsed

    def _save(self, data: dict[str, Any]) -> None:
        payload = {k: v for k, v in data.items() if not str(k).startswith("_")}
        payload["version"] = GRANT_VERSION
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))

    def _refuse_write_if_mismatch(self, data: Mapping[str, Any]) -> None:
        if data.get("_mismatch"):
            raise GrantError(
                "授权账本的版本号与当前程序不一致：拒写（fail-closed），请人工核对 grants.json",
                GRANT_STORE_VERSION_MISMATCH, 500,
            )

    # ── 签发 / 读 / 撤销 / 续期 ──────────────────────────────────────
    def create(
        self,
        *,
        agent: str,
        issued_by: str,
        rooms: Sequence[str] = (),
        device_scope: Mapping[str, Any] | None = None,
        action_risk: str = L2_RISKY,
        ttl_days: float | None = None,
        issued_via: str = "",
        now: float | None = None,
    ) -> dict[str, Any]:
        """签一张 grant。⛔ `issued_by` 必填＝"范围必须由人给"的代码级护栏。"""
        with _GRANT_LOCK:
            agent_text = str(agent or "").strip()
            if not agent_text:
                raise GrantError("签发 grant 需要 agent（被授权主体）", GRANT_AGENT_INVALID, 400)
            if agent_text in _RESERVED_SUBJECTS:
                raise GrantError(f"{agent_text!r} 是人那一侧的身份，不是被授权 agent", GRANT_AGENT_INVALID, 400)
            if not str(issued_by or "").strip():
                raise GrantError(
                    "签发 grant 必须记明签发者（owner 令牌或长期码）——"
                    "⛔ agent 不能自己给自己扩范围（20261009 判例）",
                    GRANT_ISSUER_REQUIRED, 403,
                )
            risk = str(action_risk or "").strip().upper()
            if risk == L3_DANGEROUS:
                raise GrantError(
                    "L3（shell/python/hassio 等）动作一律禁入 grant——"
                    "它不作用于设备状态，而是任意执行（裁定 §三.1）",
                    GRANT_RISK_L3, 400,
                )
            if risk not in GRANT_RISK_LEVELS:
                raise GrantError(
                    f"action_risk 只能是 {'/'.join(GRANT_RISK_LEVELS)}，收到 {action_risk!r}",
                    GRANT_RISK_INVALID, 400,
                )
            room_list = [_norm_room(r) for r in (rooms or []) if str(r or "").strip()]
            scope = self._normalize_scope(device_scope)
            if not room_list and not scope["include"]:
                raise GrantError(
                    "grant 范围为空：至少要给一个区域（rooms，含 'all'＝全屋）或设备白名单（include）",
                    GRANT_SCOPE_EMPTY, 400,
                )
            days = GRANT_TTL_DAYS_DEFAULT if ttl_days is None else float(ttl_days)
            if not (0 < days <= GRANT_TTL_DAYS_MAX):
                raise GrantError(f"有效期必须是 (0, {GRANT_TTL_DAYS_MAX:g}] 天，收到 {ttl_days!r}",
                                 GRANT_TTL_INVALID, 400)
            stamp = float(time.time() if now is None else now)
            data = self._load()
            self._refuse_write_if_mismatch(data)
            grant_id = "g-" + secrets.token_hex(6)
            while grant_id in data["grants"]:
                grant_id = "g-" + secrets.token_hex(6)
            record = {
                "grant_id": grant_id,
                "agent": agent_text,
                "rooms": room_list,
                "device_scope": scope,
                "action_risk": risk,
                "expires_at": stamp + days * 86400.0,
                "revoke": {"revoked": False, "reason": "", "at": ""},
                "issued_at": _now_iso(stamp),
                "issued_by": str(issued_by).strip(),
                "issued_via": str(issued_via or "").strip(),
                "ttl_days": days,
                "uses": 0,
            }
            data["grants"][grant_id] = record
            self._save(data)
            return record

    def get(self, grant_id: str) -> dict[str, Any] | None:
        return self._load()["grants"].get(str(grant_id or "").strip())

    def list(self, *, agent: str = "", now: float | None = None) -> list[dict[str, Any]]:
        """列 grant（可按 agent 收窄）。`status` 是现算的，⛔ 不落盘（落盘就会过期）。"""
        stamp = float(time.time() if now is None else now)
        wanted = str(agent or "").strip()
        out: list[dict[str, Any]] = []
        for rec in self._load()["grants"].values():
            if wanted and str(rec.get("agent") or "") != wanted:
                continue
            item = dict(rec)
            item["status"] = self._status_of(rec, stamp)
            out.append(item)
        out.sort(key=lambda r: str(r.get("issued_at") or ""), reverse=True)
        return out

    def revoke(self, grant_id: str, *, reason: str = "", by: str = "",
               now: float | None = None) -> dict[str, Any]:
        """撤销立即生效：此后任何**新**执行请求即拒（Q3 上半）。幂等。"""
        with _GRANT_LOCK:
            data = self._load()
            self._refuse_write_if_mismatch(data)
            gid = str(grant_id or "").strip()
            rec = data["grants"].get(gid)
            if rec is None:
                raise GrantError(f"没有这张 grant：{gid}", GRANT_NOT_FOUND, 404)
            if not rec["revoke"]["revoked"]:
                rec["revoke"] = {"revoked": True, "reason": str(reason or "").strip(),
                                 "at": _now_iso(now), "by": str(by or "").strip()}
                self._save(data)
            return rec

    def renew(self, grant_id: str, *, ttl_days: float | None = None, by: str = "",
              now: float | None = None) -> dict[str, Any]:
        """续期（从**现在**起算，不是从旧到期时间叠加——旧值可能早已过去）。"""
        with _GRANT_LOCK:
            data = self._load()
            self._refuse_write_if_mismatch(data)
            rec = data["grants"].get(str(grant_id or "").strip())
            if rec is None:
                raise GrantError(f"没有这张 grant：{grant_id}", GRANT_NOT_FOUND, 404)
            if rec["revoke"]["revoked"]:
                raise GrantError("已撤销的 grant 不能续期：要恢复执行授权，请重新签发", GRANT_REVOKED, 403)
            days = GRANT_TTL_DAYS_DEFAULT if ttl_days is None else float(ttl_days)
            if not (0 < days <= GRANT_TTL_DAYS_MAX):
                raise GrantError(f"有效期必须是 (0, {GRANT_TTL_DAYS_MAX:g}] 天，收到 {ttl_days!r}",
                                 GRANT_TTL_INVALID, 400)
            stamp = float(time.time() if now is None else now)
            rec["expires_at"] = stamp + days * 86400.0
            rec["ttl_days"] = days
            rec["renewed_at"] = _now_iso(stamp)
            rec["renewed_by"] = str(by or "").strip()
            self._save(data)
            return rec

    def resolve(self, grant_id: str, *, agent: str, now: float | None = None) -> dict[str, Any]:
        """取一张"当前有效且归属正确"的 grant；任一条件不满足即抛（fail-closed）。"""
        stamp = float(time.time() if now is None else now)
        rec = self.get(grant_id)
        if rec is None:
            raise GrantError(f"没有这张 grant：{grant_id}——执行授权须先经用户签发",
                             GRANT_NOT_FOUND, 403)
        if rec["revoke"]["revoked"]:
            raise GrantError(
                f"grant 已被撤销（{rec['revoke'].get('at') or '时间不明'}）：执行拒绝",
                GRANT_REVOKED, 403,
            )
        if stamp > float(rec.get("expires_at") or 0):
            raise GrantError(
                f"grant 已过期（{_now_iso(float(rec.get('expires_at') or 0))}）：请续期或重新签发",
                GRANT_EXPIRED, 403,
            )
        if str(rec.get("agent") or "") != str(agent or "").strip():
            # ⛔ 不点名"这张 grant 属于谁"：那是枚举面。
            raise GrantError("grant 与调用主体不匹配：每张 grant 只对被授权 agent 生效",
                             GRANT_AGENT_MISMATCH, 403)
        return rec

    def bump_use(self, grant_id: str) -> int:
        """记一次执行（只计数，不消耗授权码——Q1：日常执行不再耗码）。"""
        with _GRANT_LOCK:
            data = self._load()
            self._refuse_write_if_mismatch(data)
            rec = data["grants"].get(str(grant_id or "").strip())
            if rec is None:
                raise GrantError(f"没有这张 grant：{grant_id}", GRANT_NOT_FOUND, 404)
            rec["uses"] = int(rec.get("uses") or 0) + 1
            rec["last_used_at"] = _now_iso()
            self._save(data)
            return int(rec["uses"])

    # ── 判定（唯一判定真源）──────────────────────────────────────────
    def check(
        self,
        grant: Mapping[str, Any],
        *,
        agent: str,
        actions: Iterable[tuple[str, str, Mapping[str, Any]]],
        targets: Iterable[str],
        room_of: Callable[[str], str] | None = None,
        now: float | None = None,
    ) -> GrantCheck:
        """一次执行请求合不合规：硬拒 → 范围/档位越界（可确认）→ 放行。

        `room_of` 是**注入**的（实体 → 区域名）——与 `af_ownership.decide(owner_of=…)` 同一种
        形状：判定在这里、取值在调用方。⛔ 本模块不自己打开设备目录（那会把授权面绑到网络上）。
        """
        stamp = float(time.time() if now is None else now)
        denied: list[dict[str, str]] = []
        if grant.get("revoke", {}).get("revoked"):
            denied.append({"code": GRANT_REVOKED, "detail": "grant 已被撤销"})
        if stamp > float(grant.get("expires_at") or 0):
            denied.append({"code": GRANT_EXPIRED, "detail": _now_iso(float(grant.get("expires_at") or 0))})
        if str(grant.get("agent") or "") != str(agent or "").strip():
            denied.append({"code": GRANT_AGENT_MISMATCH, "detail": "grant 与调用主体不匹配"})

        granted = str(grant.get("action_risk") or L0_READONLY).upper()
        granted_rank = _RISK_ORDER.get(granted, -1)
        risk = L0_READONLY
        for adapter, action, params in actions:
            level = classify_action(adapter, action, params or {})
            if _RISK_ORDER.get(level, 3) > _RISK_ORDER[risk]:
                risk = level
            domain = _domain_of(adapter, action)
            if level == L3_DANGEROUS:
                denied.append({"code": GRANT_RISK_L3, "detail": f"{action}（L3 一律禁入）"})
            elif domain in GRANT_BANNED_DOMAINS:
                denied.append({"code": GRANT_DOMAIN_BANNED,
                               "detail": f"{action}：{domain} 域禁入 grant（门锁/燃气阀/热水器/安防面板）"})

        scope = self._normalize_scope(grant.get("device_scope"))
        includes = set(scope["include"])
        excludes = set(scope["exclude"])
        rooms = {_norm_room(r) for r in (grant.get("rooms") or [])}
        out_of_scope: list[str] = []
        for entity in sorted({str(t) for t in targets if str(t)}):
            if entity in excludes:
                denied.append({"code": GRANT_DEVICE_EXCLUDED, "detail": entity})
                continue
            if entity in includes:
                continue
            room = _norm_room(room_of(entity) if room_of is not None else "")
            if ALL_ROOMS in rooms or (room and room in rooms):
                continue
            out_of_scope.append(entity)

        risk_exceeds = _RISK_ORDER.get(risk, 3) > granted_rank >= 0
        if risk_exceeds and _RISK_ORDER.get(risk, 3) <= _RISK_ORDER[L2_RISKY]:
            # 档位超授但仍在 L2 以内 ＝ 越界确认的合法对象（确认⛔ 不升级 grant，只放行这一次）。
            denied.append({"code": GRANT_RISK_EXCEEDS,
                           "detail": f"请求档 {risk} > 授予档 {granted}"})
        if out_of_scope:
            denied.append({"code": GRANT_OUT_OF_SCOPE, "detail": ",".join(out_of_scope)})

        hard = any(item["code"] in HARD_DENY_CODES for item in denied)
        soft = any(item["code"] in SOFT_DENY_CODES for item in denied)
        needs_confirm = bool(soft) and not hard
        return GrantCheck(
            ok=not hard and not soft,
            grant_id=str(grant.get("grant_id") or ""),
            agent=str(grant.get("agent") or ""),
            risk=risk,
            denied=tuple(denied),
            out_of_scope=tuple(out_of_scope),
            needs_confirm=needs_confirm,
        )

    # ── 越界确认：单次放行票（确认⛔ 不升级 grant）────────────────────
    def issue_confirm(self, check: GrantCheck, *, now: float | None = None) -> dict[str, Any]:
        """为一次越界请求出票。票 ＝ 一次性、绑定 (grant, agent, 实体集合, 档位)、限时。"""
        with _GRANT_LOCK:
            if not check.needs_confirm:
                raise GrantError("这次请求没有越界，不需要放行票", GRANT_CONFIRM_INVALID, 400)
            stamp = float(time.time() if now is None else now)
            data = self._load_confirms()
            tickets = data["tickets"]
            self._purge_confirms(tickets, stamp)
            ticket = secrets.token_hex(16)
            record = {
                "ticket": ticket,
                "grant_id": check.grant_id,
                "agent": check.agent,
                "targets": list(check.out_of_scope),
                "risk": check.risk,
                "created_at": _now_iso(stamp),
                "expires_at": stamp + CONFIRM_TTL_S,
            }
            tickets[ticket] = record
            self._save_confirms(data)
            return record

    def consume_confirm(
        self,
        ticket: str,
        *,
        agent: str,
        targets: Iterable[str] | None = None,
        grant_id: str = "",
        now: float | None = None,
    ) -> bool:
        """验票并**当场销毁**（单次有效）。验不上就 False，且留痕。"""
        with _GRANT_LOCK:
            stamp = float(time.time() if now is None else now)
            data = self._load_confirms()
            tickets = data["tickets"]
            self._purge_confirms(tickets, stamp)
            key = str(ticket or "").strip()
            rec = tickets.get(key)
            reason = ""
            if rec is None:
                reason = "票不存在／已用过／已过期"
            elif str(rec.get("agent") or "") != str(agent or "").strip():
                reason = "票与调用主体不匹配"
            elif grant_id and str(rec.get("grant_id") or "") != str(grant_id):
                reason = "票与 grant 不匹配"
            elif targets is not None and sorted(rec.get("targets") or []) != sorted({str(t) for t in targets if str(t)}):
                reason = "票与本次实体集合不匹配"
            if reason:
                logger.warning("GRANT_CONFIRM_REJECTED ticket=%s reason=%s", key[:6] + "…" if key else "", reason)
                self._save_confirms(data)  # 过期清理也要落盘
                return False
            tickets.pop(key, None)
            self._save_confirms(data)
            return True

    # ── 内部：范围形状 / 状态 / 放行票盘 ─────────────────────────────
    @staticmethod
    def _normalize_scope(device_scope: Mapping[str, Any] | None) -> dict[str, list[str]]:
        """`device_scope` 形状归一。⛔ 不接受未声明的键（静默忽略＝配置写错了也不知道）。"""
        if device_scope is None:
            return {"include": [], "exclude": []}
        if not isinstance(device_scope, Mapping):
            raise GrantError("device_scope 必须是对象 {include: [...], exclude: [...]}",
                             GRANT_SCOPE_SHAPE, 400)
        unknown = set(device_scope) - {"include", "exclude"}
        if unknown:
            raise GrantError(f"device_scope 有未声明的键：{sorted(unknown)}（只认 include/exclude）",
                             GRANT_SCOPE_SHAPE, 400)
        out: dict[str, list[str]] = {}
        for key in ("include", "exclude"):
            value = device_scope.get(key) or []
            if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
                raise GrantError(f"device_scope.{key} 必须是列表", GRANT_SCOPE_SHAPE, 400)
            out[key] = [str(x) for x in value if str(x or "").strip()]
        return out

    @staticmethod
    def _status_of(rec: Mapping[str, Any], now: float) -> str:
        if rec.get("revoke", {}).get("revoked"):
            return "revoked"
        if now > float(rec.get("expires_at") or 0):
            return "expired"
        return "active"

    def _load_confirms(self) -> dict[str, Any]:
        try:
            raw = self.confirm_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {"version": GRANT_VERSION, "tickets": {}}
        except OSError as exc:
            raise GrantError(f"放行票文件读不出（fail-closed）：{exc}", GRANT_STORE_CORRUPT, 500) from exc
        try:
            parsed = json.loads(raw)
        except ValueError as exc:
            raise GrantError("放行票文件已损坏（fail-closed）：请隔离 grant_confirms.json",
                             GRANT_STORE_CORRUPT, 500) from exc
        if not isinstance(parsed, dict) or not isinstance(parsed.get("tickets"), dict):
            raise GrantError("放行票文件形状不对（fail-closed）：期望 {\"tickets\": {...}}",
                             GRANT_STORE_CORRUPT, 500)
        return parsed

    def _save_confirms(self, data: Mapping[str, Any]) -> None:
        self.confirm_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": GRANT_VERSION, "tickets": dict(data.get("tickets") or {})}
        atomic_write_text(self.confirm_path,
                          json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))

    @staticmethod
    def _purge_confirms(tickets: dict[str, Any], now: float) -> int:
        """清过期票（票是短命的，盘上不许长出无界的"曾经放行过"清单）。"""
        dead = [k for k, v in tickets.items() if now > float(v.get("expires_at") or 0)]
        for key in dead:
            tickets.pop(key, None)
        return len(dead)
