"""下发后通用撤销 / 设备态回滚（Roadmap v2.2 F7，决策 E）。

设计要点（对齐决策 E「常规安全能力，非红线」）：
- **属性感知恢复**：除 on/off 外，light 的 brightness/color_temp、cover 的
  position、climate 的 temperature/hvac_mode、fan 的 percentage 都按动作前真实
  快照参数化回放（`DOMAIN_SETTER`），而不是只会"关灯/开灯"。
- **单一恢复策略（第四轮审计 缺陷 2）**：六个域共用同一条规则——快照里**读得出的目标一定回放，
  读不出的字段一定如实上报，既不猜值也不假装恢复完整**：
  - 无法映射成恢复动作的域（binary_sensor、sensor、scene…）→ 跳过并告警（沿用 canary 铁律）；
  - 状态本身不可判读（缺失 / `unknown` / `unavailable`）→ **跳过整个实体**，绝不据此写设备
    （旧实现把 `unknown` 当"关"，会对状态未知的锁下发 `lock.unlock`）；
  - 恢复目标本身不可判读（cover 的位置）→ 跳过（HA 的 `open` 含 1%–99%，转不成"全开"）；
  - 方向读得出、附加属性读不出（brightness / color_temp / percentage / temperature）→
    回放方向并把缺失字段记进 `gaps`，结果里以 `partial` 呈现 + warn 日志（旧实现静默丢字段后
    仍报 `restored`，用户以为完全回滚了）。
- **时间窗**：每次部署记录带时间戳，超过 `window_s`（默认 60s，可配 0–300s）
  的撤销请求直接拒绝——过期意味着需要重新走审批闸门，不能"静默无限期可撤"。
- **风险域二次确认**：climate/cover/lock 等风险域的撤销需调用方显式 `confirm`，
  与下发同级的审批带一致。
- **零 LLM、零硬编码**：纯映射表 + 文件持久化，不引入新依赖。

落盘：`{store_root}/undo_log.json`（与 shadow_log / conflict_audit 同目录），
记录 `{deploy_id: {ts, entities: {entity_id: {state, attributes}}}}`。
"""

from __future__ import annotations

import json
import logging
import math
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, NamedTuple

from .af_adapters import CallResult
from .af_atomic import atomic_write_text, refuse_when_shape_unreadable
from .af_env import env_int, env_number
from .af_time import SystemTimeSource, TimeSource

__all__ = [
    "DOMAIN_SETTER",
    "RestoreCall",
    "restore_call",
    "RISK_DOMAINS",
    "UndoStore",
    "deploy_id",
]

logger = logging.getLogger("autoforge.undo")

# 数值型 env 走公共 fail-safe 路径：模块级求值若裸写 float()/int()，配置写错会让
# `import autoforge.af_undo` 直接抛 ValueError ⇒ 整个包起不来（新增审计 BUG-10）。
MAX_WINDOW_S = 300.0
DEFAULT_WINDOW_S = env_number("AUTOFORGE_UNDO_WINDOW_S", 60.0, lo=0.0, hi=MAX_WINDOW_S)

#: 快照条数硬上限。时间窗只管"一份快照活了多久"，这一条管"窗口内同时堆了多少份"：
#: 一次部署一份，批量下发下 `undo_log.json` 仍会随部署数涨。与 `af_service._SESSIONS` 同口径。
MAX_DEPLOYS = env_int("AUTOFORGE_UNDO_MAX_DEPLOYS", 200, lo=1)

#: 时钟回拨容差（秒）。撤销窗口用墙上时钟算 age，若 age 为负（记录时间在未来），
#: `age > window` 恒为 False ⇒ 记录永不过期、revert 永远放行。超过此容差即判时钟偏移，
#: fail-closed 拒绝撤销（新增审计 BUG-09）。
CLOCK_SKEW_TOLERANCE_S = 5.0

#: 风险域：撤销需调用方显式 confirm（与下发同级审批带）
RISK_DOMAINS = frozenset({"climate", "cover", "lock", "fan", "vacuum"})

#: 快照状态 → "off" 的读法。`unknown`/`unavailable` 是"读不出"，见 `_ILLEGIBLE_STATES`，
#: 不放进来——旧实现把它们当"关"，会对状态未知的锁下发 `lock.unlock`。
_OFF_STATES = frozenset({"off", "closed", "unlocked", "idle", "away", "false", "0"})
_ON_STATES = frozenset({"on", "open", "locked", "active", "home", "true", "1"})

#: HA 的"我不知道"哨兵。它们**不是**关/合——状态读不出方向，据其下发就是猜。
_ILLEGIBLE_STATES = frozenset({"unknown", "unavailable", "none", ""})


class RestoreCall(NamedTuple):
    """恢复动作 + 快照里存在却没能回放的字段。

    `gaps` 非空即"部分恢复"：方向回放了、某个属性丢了。调用方必须把它呈现出来，
    不能只报 `restored`（第四轮审计 缺陷 2 的正解——旧实现静默丢字段仍算成功）。
    """

    action: str
    params: dict[str, Any]
    gaps: tuple[str, ...] = ()


def _as_state(snapshot: Mapping[str, Any]) -> str | None:
    s = snapshot.get("state")
    return str(s).strip().lower() if s is not None else None


def _attrs(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    a = snapshot.get("attributes")
    return dict(a) if isinstance(a, Mapping) else {}


def _direction(snapshot: Mapping[str, Any]) -> str | None:
    """快照状态 → "on" / "off"；读不出方向返回 None（调用方必须跳过，不写设备）。

    两边都是**白名单**：`_OFF_STATES` / `_ON_STATES` 之外的状态（如 lock 的 `jammed`、
    `locking`）含义不明，落到任何一边都是猜——默认"开"更是会把"卡住的锁"当成"已上锁"。
    """
    st = _as_state(snapshot)
    if st is None or st in _ILLEGIBLE_STATES:
        return None
    if st in _OFF_STATES:
        return "off"
    if st in _ON_STATES:
        return "on"
    return None


def _num_attr(
    attrs: Mapping[str, Any], key: str, entity_id: str, *, cast: Callable[[Any], Any] = int
) -> tuple[Any, bool]:
    """读快照里的数值属性。

    返回 `(值, 是否算 gap)`：键不存在 → `(None, False)`（快照本来就没这个字段，不算丢失）；
    键存在但读不出（HA 常见的 `unknown`/`unavailable`/字符串/`nan`）→ `(None, True)` + 告警，
    调用方必须把它记进 `gaps`。
    """
    if key not in attrs:
        return None, False
    raw = attrs[key]
    try:
        value = cast(raw)
    except (TypeError, ValueError, OverflowError):
        logger.warning("undo：%s 的 %s=%r 读不出数值，本次恢复不含该字段", entity_id, key, raw)
        return None, True
    if isinstance(value, float) and not math.isfinite(value):
        logger.warning("undo：%s 的 %s=%r 非有限数值，本次恢复不含该字段", entity_id, key, raw)
        return None, True
    return value, False


def _light(snapshot: Mapping[str, Any], entity_id: str) -> RestoreCall | None:
    direction = _direction(snapshot)
    if direction is None:
        return None
    if direction == "off":
        return RestoreCall("light.turn_off", {"entity_id": entity_id})
    params: dict[str, Any] = {"entity_id": entity_id}
    attrs = _attrs(snapshot)
    gaps: list[str] = []
    brightness, lost = _num_attr(attrs, "brightness", entity_id)
    if brightness is not None:
        params["brightness"] = brightness
    gaps += ["brightness"] if lost else []
    color_temp, lost = _num_attr(attrs, "color_temp", entity_id)
    if color_temp is not None:
        params["color_temp"] = color_temp
    gaps += ["color_temp"] if lost else []
    return RestoreCall("light.turn_on", params, tuple(gaps))


def _switch(snapshot: Mapping[str, Any], entity_id: str) -> RestoreCall | None:
    direction = _direction(snapshot)
    if direction is None:
        return None
    action = "switch.turn_off" if direction == "off" else "switch.turn_on"
    return RestoreCall(action, {"entity_id": entity_id})


def _fan(snapshot: Mapping[str, Any], entity_id: str) -> RestoreCall | None:
    direction = _direction(snapshot)
    if direction is None:
        return None
    if direction == "off":
        return RestoreCall("fan.turn_off", {"entity_id": entity_id})
    params: dict[str, Any] = {"entity_id": entity_id}
    percentage, lost = _num_attr(_attrs(snapshot), "percentage", entity_id)
    if percentage is not None:
        params["percentage"] = percentage
    return RestoreCall("fan.turn_on", params, ("percentage",) if lost else ())


def _cover(snapshot: Mapping[str, Any], entity_id: str) -> RestoreCall | None:
    # HA 的 cover state 只有 open/closed 两档，而 open 覆盖 1%–99%，读不出位置就转不成
    # "开到原处"——位置是唯一恢复目标，它读不出就整体 fail-closed（绝不下发 open_cover 猜值）。
    pos, _ = _num_attr(_attrs(snapshot), "current_position", entity_id)
    if pos is None:
        return None
    return RestoreCall("cover.set_cover_position", {"entity_id": entity_id, "position": pos})


def _climate(snapshot: Mapping[str, Any], entity_id: str) -> RestoreCall | None:
    attrs = _attrs(snapshot)
    temp, temp_lost = _num_attr(attrs, "temperature", entity_id, cast=float)
    hvac = attrs.get("hvac_mode")
    if temp is not None:
        params: dict[str, Any] = {"entity_id": entity_id, "temperature": temp}
        if hvac is not None and str(hvac).strip().lower() not in _ILLEGIBLE_STATES:
            params["hvac_mode"] = str(hvac)
            return RestoreCall("climate.set_temperature", params)
        return RestoreCall(
            "climate.set_temperature", params,
            () if hvac is None else ("hvac_mode",),
        )
    # 温度读不出/没有：hvac_mode 读得出就只回方向，并把温度记进 gap（与 light 同策略）
    if hvac is not None and str(hvac).strip().lower() not in _ILLEGIBLE_STATES:
        return RestoreCall(
            "climate.set_hvac_mode", {"entity_id": entity_id, "hvac_mode": str(hvac)},
            ("temperature",) if temp_lost else (),
        )
    return None  # 读不出任何设定：fail-closed


def _lock(snapshot: Mapping[str, Any], entity_id: str) -> RestoreCall | None:
    direction = _direction(snapshot)
    if direction is None:
        # 状态未知时旧实现下发 lock.unlock——对读不出的锁做解锁是不可接受的猜测。
        return None
    action = "lock.lock" if direction == "on" else "lock.unlock"
    return RestoreCall(action, {"entity_id": entity_id})


#: 域 → 属性感知恢复动作生成器（决策 E ② 的 DOMAIN_SETTER 映射）
DOMAIN_SETTER: dict[str, Callable[[Mapping[str, Any], str], RestoreCall | None]] = {
    "light": _light,
    "switch": _switch,
    "fan": _fan,
    "cover": _cover,
    "climate": _climate,
    "lock": _lock,
}


def restore_call(entity_id: str, snapshot: Mapping[str, Any]) -> RestoreCall | None:
    """依据动作前快照推导恢复动作（属性感知）。无法映射 → None（fail-closed）。

    读得出的目标一定回放；读不出的字段进 `RestoreCall.gaps` 由调用方如实上报。
    """
    if not entity_id or snapshot is None:
        return None
    # 状态缺失：无法判定 on/off，绝不猜（fail-closed）
    st = _as_state(snapshot)
    if st is None:
        return None
    if st in _ILLEGIBLE_STATES:
        logger.warning("undo 跳过 %s：快照状态 %r 读不出方向（fail-closed）", entity_id, st)
        return None
    domain = entity_id.split(".", 1)[0]
    setter = DOMAIN_SETTER.get(domain)
    if setter is None:
        logger.warning("undo 跳过 %s：域 %r 无恢复映射（fail-closed）", entity_id, domain)
        return None
    try:
        return setter(snapshot, entity_id)
    except Exception:  # 防御：映射函数异常也按 fail-closed 处理
        logger.exception("undo 恢复映射异常（fail-closed）：%s", entity_id)
        return None


def deploy_id() -> str:
    """生成本次部署的唯一撤销 ID（CLI/API 在部署开始时调用）。"""
    return f"dep-{uuid.uuid4().hex[:12]}"


class UndoStore:
    """下发前快照的持久化 + 撤销执行（决策 E ①④⑤）。

    默认落盘 `{store_root}/undo_log.json`；`window_s` 控制可撤销时间窗。
    """

    def __init__(
        self,
        store_root: str = ".forge",
        window_s: float | None = None,
        clock: TimeSource | None = None,
        max_deploys: int = MAX_DEPLOYS,
    ):
        self.root = Path(store_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "undo_log.json"
        self.window_s = min(MAX_WINDOW_S, float(window_s if window_s is not None else DEFAULT_WINDOW_S))
        self.max_deploys = max(1, int(max_deploys))
        self._clock = clock or SystemTimeSource()  # B 增强：注入 TimeSource，生产缺省时用 SystemTimeSource
        self._records: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                self._records = json.loads(self.path.read_text(encoding="utf-8")) or {}
            except (ValueError, OSError):
                logger.warning("undo_log 读取失败，重置为空", exc_info=True)
                self._records = {}

    def _save(self) -> None:
        # AF3（第二期第二轮确证）：`_load` 有日志但**照样重置成空**，而这里是整档覆盖——
        # undo_log.json 一旦损坏，下一次 record() 就把全部撤销历史抹掉，撤销功能直接失效。
        refuse_when_shape_unreadable(self.path, "撤销记录")
        atomic_write_text(self.path, json.dumps(self._records, ensure_ascii=False, indent=2))

    @staticmethod
    def _age_of(rec: Mapping[str, Any], now_ts: float) -> tuple[float, bool]:
        """返回 `(age_seconds, skewed)`。

        `skewed=True` 表示 age 为负且超过容差——即记录时间落在「未来」，通常来自
        墙上时钟回拨或 NTP 校正。此类记录不能当作"还很新"：否则撤销窗口永不过期。
        （新增审计 BUG-09）
        """
        age = now_ts - float(rec.get("ts", 0))
        return age, age < -CLOCK_SKEW_TOLERANCE_S

    def record(self, deploy_id: str, entities: Mapping[str, Mapping[str, Any]]) -> None:
        """记录本次部署的"动作前快照"（entity_id → {state, attributes}）。"""
        self._records[deploy_id] = {
            "ts": self._clock.now().timestamp(),
            "entities": {
                e: {"state": s.get("state"), "attributes": s.get("attributes", {})}
                for e, s in entities.items()
            },
        }
        # 回收挂在**写路径**而不是"打开即清"：`purge_expired()` 原先零调用方，超窗快照只被读侧
        # 判 expired、从不摘除 ⇒ `undo_log.json` 随部署数单调增长。放在 `__init__` 里实测会让
        # `/api/undo/{deploy_id}` 对超窗记录回 404（"这条不存在"），把"过期撤不了"和"没这条"
        # 混成一个答复——那是既有 HTTP 判据（`test_af_undo_http.py`）当场拦住的红。
        self.purge_expired()
        self._trim()
        self._save()

    def _trim(self) -> None:
        """超硬上限时按时间戳丢最旧：窗口内的最新部署才是在操作员手里可能要撤的那一份。"""
        overflow = len(self._records) - self.max_deploys
        if overflow <= 0:
            return
        oldest = sorted(
            self._records.items(), key=lambda kv: float(kv[1].get("ts", 0.0))
        )[:overflow]
        for deploy_id, _rec in oldest:
            self._records.pop(deploy_id, None)

    def record_merge(self, deploy_id: str, entities: Mapping[str, Mapping[str, Any]]) -> None:
        """累积同一次部署的多个动作前快照（F7 CLI 逐动作落盘用）。

        同一实体只保留**首次**出现的快照——即"整次部署开始前"的原始状态，
        撤销时能正确还原到部署前，而非某次动作之间的中间态。
        """
        rec = self._records.get(deploy_id)
        if rec is None:
            self.record(deploy_id, entities)
            return
        for e, s in entities.items():
            if e not in rec["entities"]:
                rec["entities"][e] = {
                    "state": s.get("state"),
                    "attributes": s.get("attributes", {}),
                }
        self._save()

    def get(self, deploy_id: str) -> dict[str, Any] | None:
        return self._records.get(deploy_id)

    def exists(self, deploy_id: str) -> bool:
        return deploy_id in self._records

    def inspect(self, deploy_id: str) -> dict[str, Any]:
        """只读盘点一条撤销记录（WebUI/MCP 渲染用）。

        判定口径与 `revert` **同源**（同一 `window_s`、同一 `RISK_DOMAINS`），
        避免前端自己算窗口/风险域而与实际拒绝结果不一致。
        不调用 `restore_call`——那会为每个不可映射域刷 warn 日志。
        """
        rec = self._records.get(deploy_id)
        if rec is None:
            return {"exists": False, "deploy_id": deploy_id, "reason": "unknown_deploy_id"}
        entities = rec.get("entities", {})
        age, skewed = self._age_of(rec, self._clock.now().timestamp())
        expired = skewed or (self.window_s > 0 and age > self.window_s)
        risk = sorted(e for e in entities if e.split(".", 1)[0] in RISK_DOMAINS)
        mapped = sorted(e for e in entities if e.split(".", 1)[0] in DOMAIN_SETTER)
        return {
            "exists": True,
            "deploy_id": deploy_id,
            "age_s": round(age, 3),
            "window_s": self.window_s,
            "expired": expired,
            "clock_skew": skewed,
            "undoable": not expired,
            "entities": sorted(entities),
            "risk_entities": risk,
            "confirm_required": bool(risk),
            # 只说"这个域有恢复映射"，不说"一定能恢复"——属性是否读得出来要等 revert 才知道
            "domain_mapped": mapped,
            "domain_unmapped": sorted(set(entities) - set(mapped)),
        }

    def available(self) -> list[dict[str, Any]]:
        """窗口内仍可撤销的部署清单（按时间倒序），供 UI 展示可撤销项。"""
        now = self._clock.now().timestamp()
        out = [
            {"deploy_id": did, "age_s": round(self._age_of(rec, now)[0], 3),
             "entities": sorted(rec.get("entities", {}))}
            for did, rec in self._records.items()
            if not self._age_of(rec, now)[1]
            and (self.window_s <= 0 or (now - float(rec.get("ts", 0))) <= self.window_s)
        ]
        out.sort(key=lambda r: r["age_s"])
        return out

    def revert(
        self,
        deploy_id: str,
        adapter: Any,
        *,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """回放动作前快照恢复设备态（fail-closed）。

        - 未知 deploy_id → 拒绝
        - 超时间窗 → 拒绝（需重新审批）
        - 风险域（climate/cover/lock…）且未 confirm → 拒绝
        - 不可映射实体 → 跳过 + 告警（不阻断其余可恢复实体）
        - 方向回放了但有属性读不出 → 照常下发，实体进 `partial` 并列出 `not_restored`，
          绝不只报 `restored`（第四轮审计 缺陷 2）
        """
        rec = self._records.get(deploy_id)
        if rec is None:
            return {"ok": False, "reason": "unknown_deploy_id", "deploy_id": deploy_id}
        now_ts = self._clock.now().timestamp()
        age, skewed = self._age_of(rec, now_ts)
        if skewed:
            # fail-closed：时间戳落在未来，窗口判定不可信 ⇒ 拒绝撤销而不是放行
            logger.warning(
                "撤销记录时间戳落在未来（age=%.3fs），判时钟偏移后拒绝：deploy_id=%s", age, deploy_id
            )
            return {
                "ok": False,
                "reason": "clock_skew",
                "deploy_id": deploy_id,
                "age_s": round(age, 3),
                "message": "部署记录时间戳异常（系统时钟回拨），撤销已拒绝；请校准系统时钟后重试",
            }
        if self.window_s > 0 and age > self.window_s:
            return {
                "ok": False,
                "reason": "expired",
                "deploy_id": deploy_id,
                "window_s": self.window_s,
                "message": "撤销窗口已过期，需重新走审批闸门下发",
            }
        entities = rec.get("entities", {})
        # 风险域二次确认
        risk_hit = [e for e in entities if e.split(".", 1)[0] in RISK_DOMAINS]
        if risk_hit and not confirm:
            return {
                "ok": False,
                "reason": "risk_domain_requires_confirm",
                "deploy_id": deploy_id,
                "risk_entities": risk_hit,
                "message": "含风险域（climate/cover/lock 等），请加 --confirm 二次确认",
            }
        restored: list[str] = []
        failed: list[str] = []
        skipped: list[str] = []
        partial: list[dict[str, Any]] = []
        results: list[CallResult] = []
        for entity_id, snapshot in entities.items():
            call = restore_call(entity_id, snapshot)
            if call is None:
                skipped.append(entity_id)
                continue
            action, params, gaps = call
            if gaps:
                partial.append({
                    "entity_id": entity_id,
                    "action": action,
                    "not_restored": list(gaps),
                    "message": "方向已恢复，但快照里这些属性读不出、未回放",
                })
            try:
                res = adapter.call(action, params)
            except Exception as exc:  # 适配器异常不阻断其余回滚
                logger.exception("undo 恢复调用失败：%s %s", action, params)
                results.append(CallResult.fail(f"undo 调用异常：{exc}", action=action, params=params))
                failed.append(entity_id)
                continue
            results.append(res)
            if getattr(res, "success", True):
                restored.append(entity_id)
            else:
                failed.append(entity_id)
        all_ok = not failed
        return {
            "ok": all_ok,
            "deploy_id": deploy_id,
            "restored": restored,
            "failed": failed,
            "skipped": skipped,
            "partial": partial,
            "fully_restored": all_ok and not partial and not skipped,
            "results": results,
        }

    def purge_expired(self, window_s: float | None = None) -> int:
        """清理超时间窗的记录（避免无限增长）。返回清理条数。"""
        window = self.window_s if window_s is None else float(window_s)
        now = self._clock.now().timestamp()
        before = len(self._records)
        self._records = {
            did: rec for did, rec in self._records.items()
            # skewed（时间戳落在未来）视为无效记录一并清掉，否则它们永远清不掉
            if not self._age_of(rec, now)[1]
            and (window <= 0 or (now - float(rec.get("ts", 0))) <= window)
        }
        removed = before - len(self._records)
        if removed:
            self._save()
        return removed
