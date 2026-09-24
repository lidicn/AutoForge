"""Canary 观察期与自动晋升（IR §10：auto 档 = build → canary → 自动部署）。

现有 CanaryGuard 只做**单次动作**的漂移检测 + 回滚。本模块在其上层加一层
Supervisor，把 canary 变成一个**阶段**：

* 新部署（或 shadow 转正）的 auto 自动化进入观察期（默认 24h），每次动作都
  走 CanaryGuard 的漂移检测；
* 观察期满且漂移次数 ≤ max_drift → 自动晋升为全量：把 IR 的 canary 字段置
  null 并持久化（``strip_canary`` 钩子）；
* 出现漂移 → 回滚 + ``drift`` 负样本 + conf 降到 < 0.85 时自动降级为 shadow。

**不修改 af_canary.py**，只调用其公开的 CanaryGuard / CanaryResult。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from autoforge.af_conf import AUTO_MIN, ConfidenceStore
from autoforge.af_feedback import (
    FeedbackKind, FeedbackRecorder,
    audit_write, clock_now, conf_read,
)

__all__ = [
    "CanaryRecord", "CanaryPolicy", "CanarySupervisor",
    "graph_canary_stripper", "graph_canary_applier", "DEFAULT_CANARY_SPEC",
]

DEFAULT_CANARY_SPEC: dict[str, Any] = {"duration": "24h", "auto_rollback": True}


@dataclass
class CanaryRecord:
    """一个自动化的 canary 观察状态。"""

    automation_id: str
    since: float
    observation_seconds: float
    status: str = "observing"          # observing | promoted | demoted
    actions: int = 0
    drift_count: int = 0
    last_drift_at: float | None = None
    promoted_at: float | None = None
    demoted_at: float | None = None
    note: str = ""

    def to_json(self) -> dict[str, Any]:
        """序列化。"""
        return {
            "automation_id": self.automation_id, "since": self.since,
            "observation_seconds": self.observation_seconds, "status": self.status,
            "actions": self.actions, "drift_count": self.drift_count,
            "last_drift_at": self.last_drift_at,
            "promoted_at": self.promoted_at, "demoted_at": self.demoted_at,
            "note": self.note,
        }


@dataclass
class CanaryPolicy:
    """全部阈值可配。"""

    observation_hours: float = 24.0     # 观察期 N 小时
    max_drift: int = 0                  # 观察期内允许的漂移次数
    min_actions: int = 0                # 观察期内至少执行 N 次动作（0 = 按 IR §10 字面）
    demote_below: float = AUTO_MIN      # conf < 0.85 → 降级 shadow
    recheck_conf: bool = True           # check() 复核已晋升自动化的 conf


def _iter_automations(graph: Any):
    """duck typing 取 Graph 里的自动化集合。"""
    autos = getattr(graph, "automations", None)
    if autos is None:
        return []
    return list(autos.values()) if isinstance(autos, Mapping) else list(autos)


def _automation(graph: Any, automation_id: str) -> Any:
    autos = getattr(graph, "automations", None)
    if isinstance(autos, Mapping):
        return autos.get(automation_id)
    for auto in _iter_automations(graph):
        if str(getattr(auto, "id", "")) == automation_id:
            return auto
    return None


def _do_nodes(auto: Any):
    nodes = getattr(auto, "nodes", {}) or {}
    items = nodes.values() if isinstance(nodes, Mapping) else nodes
    return [n for n in items if getattr(n, "kind", "") == "do"]


def graph_canary_stripper(graph: Any) -> Callable[[str], int]:
    """返回 ``strip(automation_id) -> 变更节点数``：把 IR 的 canary 字段置 None。"""

    def _strip(automation_id: str) -> int:
        auto = _automation(graph, automation_id)
        if auto is None:
            return 0
        changed = 0
        for node in _do_nodes(auto):
            if getattr(node, "canary", None) is not None:
                node.canary = None
                changed += 1
        return changed

    return _strip


def graph_canary_applier(
    graph: Any, spec: Mapping[str, Any] | None = None,
) -> Callable[[str], int]:
    """返回 ``apply(automation_id) -> 变更节点数``：给 IR 的 do 节点挂 canary。"""
    payload = dict(spec or DEFAULT_CANARY_SPEC)

    def _apply(automation_id: str) -> int:
        auto = _automation(graph, automation_id)
        if auto is None:
            return 0
        changed = 0
        for node in _do_nodes(auto):
            if getattr(node, "canary", None) is None:
                node.canary = dict(payload)
                changed += 1
        return changed

    return _apply


@dataclass
class CanarySupervisor:
    """包装 CanaryGuard，把单次漂移检测升级为「观察期 → 晋升/降级」阶段机。"""

    conf: ConfidenceStore
    recorder: FeedbackRecorder
    clock: Any
    audit: Any = None
    policy: CanaryPolicy = field(default_factory=CanaryPolicy)
    strip_canary: Callable[[str], int] | None = None
    on_promote: Callable[[str], None] | None = None
    on_demote: Callable[[str], None] | None = None
    records: dict[str, CanaryRecord] = field(default_factory=dict)

    # ---- 登记 ----------------------------------------------------------- #

    def begin(self, automation_id: str, at: float | None = None) -> CanaryRecord:
        """进入 canary 观察期（幂等：观察中则返回现有记录）。"""
        existing = self.records.get(automation_id)
        if existing is not None and existing.status == "observing":
            return existing
        now = clock_now(self.clock) if at is None else float(at)
        record = CanaryRecord(
            automation_id=automation_id, since=now,
            observation_seconds=self.policy.observation_hours * 3600.0,
        )
        self.records[automation_id] = record
        audit_write(
            self.audit, at=now, kind="canary_started", automation_id=automation_id,
            observation_hours=self.policy.observation_hours,
        )
        return record

    def record(self, automation_id: str) -> CanaryRecord | None:
        """查观察状态。"""
        return self.records.get(automation_id)

    # ---- 动作回报 ------------------------------------------------------- #

    def on_canary_result(
        self, automation_id: str, result: Any, *,
        guard: Any = None, adapter: Any = None, at: float | None = None,
    ) -> CanaryRecord:
        """每次 CanaryGuard 动作结束后回报（自动登记未入册的自动化）。"""
        now = clock_now(self.clock) if at is None else float(at)
        record = self.records.get(automation_id) or self.begin(automation_id, at=now)
        record.actions += 1
        drift = bool(result.has_drift())
        details: dict[str, Any] = {
            "basis": "canary_result", "action": getattr(result, "action", ""),
            "params": dict(getattr(result, "params", None) or {}),
            "drift": drift,
        }
        if not drift:
            audit_write(
                self.audit, at=now, kind="canary_action", automation_id=automation_id,
                drift=False, actions=record.actions,
            )
            return record

        record.drift_count += 1
        record.last_drift_at = now
        if guard is not None and adapter is not None:
            rolled = guard.check_and_rollback(adapter, result) or []
            details["rollback"] = [getattr(c, "action", None) for c in rolled]
        self.recorder.emit(
            automation_id, FeedbackKind.DRIFT, source="canary", at=now, details=details,
        )
        audit_write(
            self.audit, at=now, kind="canary_drift", automation_id=automation_id,
            drift_count=record.drift_count, **details,
        )
        self._maybe_demote(automation_id, reason="drift", at=now)
        return record

    # ---- 定时巡检 ------------------------------------------------------- #

    def check(self, at: float | None = None) -> list[str]:
        """巡检：观察期满无漂移 → 晋升；已晋升但 conf 掉档 → 降级。

        返回本次发生状态迁移的 automation_id 列表。
        """
        now = clock_now(self.clock) if at is None else float(at)
        moved: list[str] = []
        for automation_id, record in list(self.records.items()):
            if record.status == "observing":
                elapsed = now - record.since
                if (
                    elapsed >= record.observation_seconds
                    and record.drift_count <= self.policy.max_drift
                    and record.actions >= self.policy.min_actions
                ):
                    self._promote(automation_id, record, now)
                    moved.append(automation_id)
            elif record.status == "promoted" and self.policy.recheck_conf:
                if conf_read(self.conf, automation_id) < self.policy.demote_below:
                    self._demote(automation_id, record, reason="conf_below_threshold", at=now)
                    moved.append(automation_id)
        return moved

    # ---- 持久化 --------------------------------------------------------- #

    def dump(self) -> dict[str, Any]:
        """导出 JSON。"""
        return {"records": {k: v.to_json() for k, v in self.records.items()}}

    def load(self, data: Mapping[str, Any]) -> int:
        """从 JSON 恢复。"""
        rows = data.get("records", {}) or {}
        for aid, row in rows.items():
            self.records[str(aid)] = CanaryRecord(
                automation_id=str(aid), since=float(row["since"]),
                observation_seconds=float(row["observation_seconds"]),
                status=str(row.get("status", "observing")),
                actions=int(row.get("actions", 0)),
                drift_count=int(row.get("drift_count", 0)),
                last_drift_at=row.get("last_drift_at"),
                promoted_at=row.get("promoted_at"),
                demoted_at=row.get("demoted_at"),
                note=str(row.get("note", "")),
            )
        return len(rows)

    # ---- 内部 ----------------------------------------------------------- #

    def _promote(self, automation_id: str, record: CanaryRecord, now: float) -> None:
        removed = self.strip_canary(automation_id) if self.strip_canary else 0
        record.status = "promoted"
        record.promoted_at = now
        record.note = f"移除 canary 保护节点数={removed}"
        self.conf.promote(automation_id)
        self.recorder.emit(
            automation_id, FeedbackKind.PROMOTED, source="canary", at=now,
            details={"basis": "observation_clear", "actions": record.actions,
                     "drift_count": record.drift_count, "canary_nodes_removed": removed},
        )
        audit_write(
            self.audit, at=now, kind="canary_promoted", automation_id=automation_id,
            actions=record.actions, drift_count=record.drift_count,
            canary_nodes_removed=removed,
        )
        if self.on_promote is not None:
            self.on_promote(automation_id)

    def _maybe_demote(self, automation_id: str, reason: str, at: float) -> None:
        record = self.records.get(automation_id)
        if record is None or record.status != "observing":
            return
        if conf_read(self.conf, automation_id) < self.policy.demote_below:
            self._demote(automation_id, record, reason=reason, at=at)

    def _demote(self, automation_id: str, record: CanaryRecord, reason: str, at: float) -> None:
        record.status = "demoted"
        record.demoted_at = at
        record.note = f"降级原因={reason}"
        self.recorder.emit(
            automation_id, FeedbackKind.DEMOTED, source="canary", at=at,
            details={"basis": reason, "drift_count": record.drift_count,
                     "conf": conf_read(self.conf, automation_id)},
        )
        audit_write(
            self.audit, at=at, kind="canary_demoted", automation_id=automation_id,
            reason=reason, conf=conf_read(self.conf, automation_id),
        )
        if self.on_demote is not None:
            self.on_demote(automation_id)

