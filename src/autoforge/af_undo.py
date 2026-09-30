"""下发后通用撤销 / 设备态回滚（Roadmap v2.2 F7，决策 E）。

设计要点（对齐决策 E「常规安全能力，非红线」）：
- **属性感知恢复**：除 on/off 外，light 的 brightness/color_temp、cover 的
  position、climate 的 temperature/hvac_mode、fan 的 percentage 都按动作前真实
  快照参数化回放（`DOMAIN_SETTER`），而不是只会"关灯/开灯"。
- **fail-closed**：任何无法映射成恢复动作的域（binary_sensor、sensor、scene…
  以及快照缺失的关键属性）一律**跳过并告警**，绝不瞎猜（沿用 canary 铁律）。
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
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .af_adapters import CallResult
from .af_time import SystemTimeSource, TimeSource

__all__ = [
    "DOMAIN_SETTER",
    "restore_call",
    "RISK_DOMAINS",
    "UndoStore",
    "deploy_id",
]

logger = logging.getLogger("autoforge.undo")

DEFAULT_WINDOW_S = float(os.getenv("AUTOFORGE_UNDO_WINDOW_S", "60"))
MAX_WINDOW_S = 300.0

#: 风险域：撤销需调用方显式 confirm（与下发同级审批带）
RISK_DOMAINS = frozenset({"climate", "cover", "lock", "fan", "vacuum"})

_ON_STATES = frozenset({"on", "open", "locked", "active", "home", "true", "1"})
_OFF_STATES = frozenset(
    {"off", "closed", "unlocked", "idle", "away", "false", "0", "unavailable", "unknown"}
)


def _as_state(snapshot: Mapping[str, Any]) -> str | None:
    s = snapshot.get("state")
    return str(s).strip().lower() if s is not None else None


def _attrs(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    a = snapshot.get("attributes")
    return dict(a) if isinstance(a, Mapping) else {}


def _light(snapshot: Mapping[str, Any], entity_id: str) -> tuple[str, dict[str, Any]] | None:
    st = _as_state(snapshot)
    if st in _OFF_STATES:
        return ("light.turn_off", {"entity_id": entity_id})
    params: dict[str, Any] = {"entity_id": entity_id}
    attrs = _attrs(snapshot)
    if (b := attrs.get("brightness")) is not None:
        try:
            params["brightness"] = int(b)
        except (TypeError, ValueError):
            pass
    if (ct := attrs.get("color_temp")) is not None:
        try:
            params["color_temp"] = int(ct)
        except (TypeError, ValueError):
            pass
    return ("light.turn_on", params)


def _switch(snapshot: Mapping[str, Any], entity_id: str) -> tuple[str, dict[str, Any]] | None:
    st = _as_state(snapshot)
    action = "switch.turn_off" if st in _OFF_STATES else "switch.turn_on"
    return (action, {"entity_id": entity_id})


def _fan(snapshot: Mapping[str, Any], entity_id: str) -> tuple[str, dict[str, Any]] | None:
    st = _as_state(snapshot)
    if st in _OFF_STATES:
        return ("fan.turn_off", {"entity_id": entity_id})
    params: dict[str, Any] = {"entity_id": entity_id}
    attrs = _attrs(snapshot)
    if (p := attrs.get("percentage")) is not None:
        try:
            params["percentage"] = int(p)
        except (TypeError, ValueError):
            pass
    return ("fan.turn_on", params)


def _cover(snapshot: Mapping[str, Any], entity_id: str) -> tuple[str, dict[str, Any]] | None:
    attrs = _attrs(snapshot)
    pos = attrs.get("current_position")
    if pos is None:
        return None  # 无位置信息：fail-closed，不猜
    try:
        pos = int(pos)
    except (TypeError, ValueError):
        return None
    return ("cover.set_cover_position", {"entity_id": entity_id, "position": pos})


def _climate(snapshot: Mapping[str, Any], entity_id: str) -> tuple[str, dict[str, Any]] | None:
    attrs = _attrs(snapshot)
    temp = attrs.get("temperature")
    hvac = attrs.get("hvac_mode")
    if temp is None and hvac is None:
        return None  # 无可恢复设定：fail-closed
    # 有 temperature → climate.set_temperature（可附带 hvac_mode）
    if temp is not None:
        try:
            params: dict[str, Any] = {"entity_id": entity_id, "temperature": float(temp)}
        except (TypeError, ValueError):
            return None
        if hvac is not None:
            params["hvac_mode"] = str(hvac)
        return ("climate.set_temperature", params)
    # 仅 hvac_mode：单独调 climate.set_hvac_mode，避免缺 temperature 必填参数
    return ("climate.set_hvac_mode", {"entity_id": entity_id, "hvac_mode": str(hvac)})


def _lock(snapshot: Mapping[str, Any], entity_id: str) -> tuple[str, dict[str, Any]] | None:
    st = _as_state(snapshot)
    action = "lock.lock" if st in ("locked", "true", "1") else "lock.unlock"
    return (action, {"entity_id": entity_id})


#: 域 → 属性感知恢复动作生成器（决策 E ② 的 DOMAIN_SETTER 映射）
DOMAIN_SETTER: dict[str, Callable[[Mapping[str, Any], str], tuple[str, dict[str, Any]] | None]] = {
    "light": _light,
    "switch": _switch,
    "fan": _fan,
    "cover": _cover,
    "climate": _climate,
    "lock": _lock,
}


def restore_call(entity_id: str, snapshot: Mapping[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """依据动作前快照推导恢复动作（属性感知）。无法映射 → None（fail-closed）。"""
    if not entity_id or snapshot is None:
        return None
    # 状态缺失：无法判定 on/off，绝不猜（fail-closed）
    if _as_state(snapshot) is None:
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

    def __init__(self, store_root: str = ".forge", window_s: float | None = None, clock: TimeSource | None = None):
        self.root = Path(store_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "undo_log.json"
        self.window_s = min(MAX_WINDOW_S, float(window_s if window_s is not None else DEFAULT_WINDOW_S))
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
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._records, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)  # 原子替换，避免半写

    def record(self, deploy_id: str, entities: Mapping[str, Mapping[str, Any]]) -> None:
        """记录本次部署的"动作前快照"（entity_id → {state, attributes}）。"""
        self._records[deploy_id] = {
            "ts": self._clock.now().timestamp(),
            "entities": {
                e: {"state": s.get("state"), "attributes": s.get("attributes", {})}
                for e, s in entities.items()
            },
        }
        self._save()

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
        """
        rec = self._records.get(deploy_id)
        if rec is None:
            return {"ok": False, "reason": "unknown_deploy_id", "deploy_id": deploy_id}
        if self.window_s > 0 and (self._clock.now().timestamp() - float(rec.get("ts", 0))) > self.window_s:
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
        results: list[CallResult] = []
        for entity_id, snapshot in entities.items():
            call = restore_call(entity_id, snapshot)
            if call is None:
                skipped.append(entity_id)
                continue
            action, params = call
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
            "results": results,
        }

    def purge_expired(self, window_s: float | None = None) -> int:
        """清理超时间窗的记录（避免无限增长）。返回清理条数。"""
        window = self.window_s if window_s is None else float(window_s)
        now = self._clock.now().timestamp()
        before = len(self._records)
        self._records = {
            did: rec for did, rec in self._records.items()
            if window <= 0 or (now - float(rec.get("ts", 0))) <= window
        }
        removed = before - len(self._records)
        if removed:
            self._save()
        return removed
