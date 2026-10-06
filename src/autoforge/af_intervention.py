"""人工干预检测器（IR §10「用户手动干预作为负样本压低 conf」）。

归因规则
--------
* AF 派发动作后写入 pending_actions；时间窗 W 内目标实体落到 expected_state
  → ``af_caused``：只做归因 + 登记 AppliedState，**不直接改 conf**；
* 窗内落到其它状态，或窗口外才落地 → ``manual_override`` → 负样本；
* 已无 pending、但实体停在 AF 给出的状态上被改走 → 人接管了 → 负样本；
* 从未被任何自动化管理的实体 → 只记审计，不产生样本。

为什么 af_caused 不立刻给正样本？
--------------------------------
AF 自己写下的状态必然「符合预期」，立即计正样本会形成自我确认的抬升回路，
任何自动化都会自动爬到 1.0。正样本延迟到 ``hold_seconds`` 结算：期间没有人
推翻（实体仍停在 AF 给的状态）才算「自动执行无误」，这才是对用户意图的真实
证据。hold 内被推翻的，覆盖路径已计负样本，hold 结算自动跳过，不会对冲。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping
from uuid import uuid4

from autoforge.af_conf import ConfidenceStore
from autoforge.af_feedback import (
    FeedbackKind, FeedbackRecorder, LaterFn,
    audit_write, clock_now, conf_read, norm_state,
)

__all__ = [
    "Verdict", "PendingAction", "AppliedState", "InterventionRecord",
    "InterventionPolicy", "InterventionDetector", "collect_managed_entities",
]


class Verdict(str, Enum):
    """归因结论。"""

    AF_CAUSED = "af_caused"
    MANUAL_OVERRIDE = "manual_override"
    UNTRACKED = "untracked"


@dataclass
class PendingAction:
    """AF 刚派发、等待状态落地的动作。"""

    automation_id: str
    instance_id: str
    node_id: str
    entity_id: str
    action: str
    expected_state: str | None
    issued_at: float
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class AppliedState:
    """AF 最近一次成功落地的状态，用于后续识别「人接管」。"""

    automation_id: str
    entity_id: str
    state: str | None
    applied_at: float
    hold_due_at: float
    hold_checked: bool = False


@dataclass
class InterventionRecord:
    """一条归因记录。"""

    record_id: str
    verdict: Verdict
    entity_id: str
    timestamp: float
    automation_id: str | None = None
    old_state: str | None = None
    new_state: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        """序列化。"""
        return {
            "record_id": self.record_id, "verdict": self.verdict.value,
            "entity_id": self.entity_id, "automation_id": self.automation_id,
            "old_state": self.old_state, "new_state": self.new_state,
            "timestamp": self.timestamp, "details": dict(self.details),
        }


@dataclass
class InterventionPolicy:
    """全部阈值可配。"""

    window: float = 30.0                 # AF 动作落地归因窗 W
    stale_after: float = 300.0           # 超过此时仍没落地 → 记 device_error
    hold_seconds: float = 600.0          # 正样本延迟确认期
    positive_after_hold: bool = True     # hold 期满无人推翻 → 计正样本
    stale_as_override: bool = True       # 窗口外落地是否计负样本（IR §10 保守归因）
    expired_feedback: FeedbackKind | None = FeedbackKind.DEVICE_ERROR
    negative_kind: FeedbackKind = FeedbackKind.USER_OVERRIDE


def collect_managed_entities(graph: Any) -> set[str]:
    """从 Graph 收集所有被 ``do`` 节点写到的实体（duck typing）。"""
    managed: set[str] = set()
    autos = getattr(graph, "automations", None)
    if autos is None:
        return managed
    items = autos.values() if isinstance(autos, Mapping) else autos
    for auto in items:
        nodes = getattr(auto, "nodes", {}) or {}
        node_iter = nodes.values() if isinstance(nodes, Mapping) else nodes
        for node in node_iter:
            if getattr(node, "kind", "") != "do":
                continue
            getter = getattr(node, "target_entities", None)
            if callable(getter):
                managed.update(str(e) for e in (getter() or ()))
    return managed


class FakeAuditLike:
    """最小审计日志，audit=None 时兜底。"""
    def append(self, entry): return entry

@dataclass
class InterventionDetector:
    """监听 HA 事件流，区分「自动化执行的动作」与「用户手动操作」。"""

    conf: ConfidenceStore
    recorder: FeedbackRecorder
    clock: Any
    audit: Any = None
    policy: InterventionPolicy = field(default_factory=InterventionPolicy)
    later: LaterFn | None = None
    pending: dict[str, list[PendingAction]] = field(default_factory=dict)
    applied: dict[str, AppliedState] = field(default_factory=dict)
    records: list[InterventionRecord] = field(default_factory=list)
    #: `records` 的条数上限。检测器随 `af_runtime_ext` 长驻、每次干预 append 一条，
    #: 不裁剪就是随进程寿命单调上涨（新增审计 BUG-18）。真值走 audit 落盘，
    #: 内存里这份只是最近若干条的窗口。
    max_records: int = 1000
    managed: set[str] = field(default_factory=set)

    def __post_init__(self):
        if self.policy is None:
            self.policy = InterventionPolicy()
        if self.audit is None:
            self.audit = FakeAuditLike()

    # ---- 受管实体 ------------------------------------------------------- #

    def mark_managed(self, entity_ids: Iterable[str]) -> None:
        """登记受自动化管理的实体。"""
        self.managed.update(str(e) for e in entity_ids)

    def mark_managed_from_graph(self, graph: Any) -> None:
        """从 Graph 自动收集受管实体。"""
        self.mark_managed(collect_managed_entities(graph))

    # ---- AF 派发报到 ---------------------------------------------------- #

    def note_call(
        self, automation_id: str, instance_id: str, node_id: str,
        entity_id: str, action: str, expected_state: str | None,
        *, params: Mapping[str, Any] | None = None, at: float | None = None,
    ) -> PendingAction:
        """AF 真实调用 adapter 前报到（每个目标实体一条）。"""
        now = clock_now(self.clock) if at is None else float(at)
        entry = PendingAction(
            automation_id=automation_id, instance_id=instance_id, node_id=node_id,
            entity_id=str(entity_id), action=action,
            expected_state=norm_state(expected_state), issued_at=now,
            params=dict(params or {}),
        )
        self.pending.setdefault(entry.entity_id, []).append(entry)
        self.managed.add(entry.entity_id)
        audit_write(
            self.audit, at=now, kind="af_action_dispatched", automation_id=automation_id,
            instance_id=instance_id, node_id=node_id, entity_id=entry.entity_id,
            action=action, expected_state=entry.expected_state,
        )
        return entry

    def note_node(
        self, automation_id: str, instance_id: str, node: Any,
        expected_state: Mapping[str, Any],
    ) -> list[PendingAction]:
        """node 级报到（由 ShadowRunner 的 do 派发咽喉调用）。"""
        entities = [str(e) for e in (node.target_entities() or ())]
        out: list[PendingAction] = []
        for entity_id in entities:
            if entity_id not in expected_state and len(expected_state) == 1:
                expected = norm_state(next(iter(expected_state.values())))
            else:
                expected = norm_state(expected_state.get(entity_id))
            out.append(self.note_call(
                automation_id, instance_id, str(getattr(node, "id", "")),
                entity_id, str(getattr(node, "action", "")), expected,
                params=getattr(node, "params", None),
            ))
        return out

    # ---- 事件流入口 ----------------------------------------------------- #

    def on_state_changed(
        self, entity_id: str, old_state: Any, new_state: Any, at: float | None = None,
    ) -> InterventionRecord:
        """收到实体状态变化事件（SSE 订阅回调）。"""
        now = clock_now(self.clock) if at is None else float(at)
        entity_id = str(entity_id)
        old = norm_state(old_state)
        new = norm_state(new_state)

        queue = self.pending.get(entity_id)
        if queue:
            pend = queue.pop(0)
            if not queue:
                self.pending.pop(entity_id, None)
            within = (now - pend.issued_at) <= self.policy.window
            landed = pend.expected_state == new

            if within and landed:
                record = self._record(
                    Verdict.AF_CAUSED, entity_id, pend.automation_id, old, new, now,
                    {"action": pend.action, "node_id": pend.node_id,
                     "instance_id": pend.instance_id},
                )
                hold_due = now + self.policy.hold_seconds
                self.applied[entity_id] = AppliedState(
                    automation_id=pend.automation_id, entity_id=entity_id,
                    state=new, applied_at=now, hold_due_at=hold_due,
                    hold_checked=not self.policy.positive_after_hold,
                )
                self._schedule_hold(entity_id, pend.automation_id, new, hold_due)
                return record

            late = not within
            details = {
                "action": pend.action, "node_id": pend.node_id,
                "instance_id": pend.instance_id, "late": late,
                "expected": pend.expected_state, "observed": new,
                "basis": "pending_not_satisfied",
            }
            if late and not self.policy.stale_as_override:
                self.applied.pop(entity_id, None)
                return self._record(
                    Verdict.UNTRACKED, entity_id, pend.automation_id, old, new, now,
                    details | {"note": "窗口外落地，策略设为不计负样本"},
                )
            record = self._record(
                Verdict.MANUAL_OVERRIDE, entity_id, pend.automation_id, old, new, now, details,
            )
            self.applied.pop(entity_id, None)
            self._penalize(pend.automation_id, details, now)
            return record

        applied = self.applied.get(entity_id)
        if applied is not None and applied.state != new:
            self.applied.pop(entity_id, None)
            details = {
                "basis": "applied_state_overridden",
                "expected": applied.state, "observed": new,
                "applied_at": applied.applied_at,
            }
            record = self._record(
                Verdict.MANUAL_OVERRIDE, entity_id, applied.automation_id, old, new, now, details,
            )
            self._penalize(applied.automation_id, details, now)
            return record

        return self._record(
            Verdict.UNTRACKED, entity_id, None, old, new, now,
            {"managed": entity_id in self.managed, "basis": "no_pending"},
        )

    # ---- 定时器回调 ----------------------------------------------------- #

    def check_holds(self, at: float | None = None) -> list[InterventionRecord]:
        """结算到期的 hold：无人推翻 → 正样本。"""
        now = clock_now(self.clock) if at is None else float(at)
        out: list[InterventionRecord] = []
        for entity_id, applied in list(self.applied.items()):
            if applied.hold_checked or now < applied.hold_due_at:
                continue
            applied.hold_checked = True
            if applied.state != self._observed(entity_id):
                continue                      # 已被推翻，覆盖路径已计负样本
            details = {"basis": "uncontested_hold", "entity_id": entity_id,
                       "state": applied.state, "hold_seconds": self.policy.hold_seconds}
            self.recorder.emit(
                applied.automation_id, FeedbackKind.POSITIVE,
                details=details, source="intervention", at=now,
            )
            out.append(self._record(
                Verdict.AF_CAUSED, entity_id, applied.automation_id,
                applied.state, applied.state, now, details | {"feedback": "positive"},
            ))
        return out

    def flush_expired(self, at: float | None = None) -> list[InterventionRecord]:
        """清理超期未落地的 pending（计 device_error 弱负样本）。"""
        now = clock_now(self.clock) if at is None else float(at)
        out: list[InterventionRecord] = []
        for entity_id in list(self.pending):
            queue = self.pending[entity_id]
            keep: list[PendingAction] = []
            for pend in queue:
                if now - pend.issued_at <= self.policy.stale_after:
                    keep.append(pend)
                    continue
                details = {"basis": "action_never_landed", "action": pend.action,
                           "node_id": pend.node_id, "expected": pend.expected_state}
                out.append(self._record(
                    Verdict.UNTRACKED, entity_id, pend.automation_id,
                    None, self._observed(entity_id), now, details | {"expired": True},
                ))
                if self.policy.expired_feedback is not None:
                    self.recorder.emit(
                        pend.automation_id, self.policy.expired_feedback,
                        details=details, source="intervention", at=now,
                    )
            if keep:
                self.pending[entity_id] = keep
            else:
                self.pending.pop(entity_id, None)
        return out

    # ---- 查询 / 持久化 --------------------------------------------------- #

    def overrides(self) -> list[InterventionRecord]:
        """返回全部人工干预记录。"""
        return [r for r in self.records if r.verdict is Verdict.MANUAL_OVERRIDE]

    def dump(self) -> dict[str, Any]:
        """导出为 JSON 兼容结构。"""
        return {"records": [r.to_json() for r in self.records]}

    # ---- 内部 ----------------------------------------------------------- #

    def _observed(self, entity_id: str) -> str | None:
        from autoforge.af_feedback import read_state
        return read_state(getattr(self.recorder, "states", None), entity_id)

    def _schedule_hold(
        self, entity_id: str, automation_id: str, state: str | None, due_at: float,
    ) -> None:
        if self.later is None or not self.policy.positive_after_hold:
            return

        def _fire() -> None:
            self.check_holds(at=due_at)

        self.later(max(0.0, due_at - clock_now(self.clock)), _fire)

    def _penalize(self, automation_id: str, details: Mapping[str, Any], now: float) -> None:
        self.recorder.emit(
            automation_id, self.policy.negative_kind,
            details=dict(details), source="intervention", at=now,
        )

    def _record(
        self, verdict: Verdict, entity_id: str, automation_id: str | None,
        old_state: str | None, new_state: str | None, now: float,
        details: Mapping[str, Any],
    ) -> InterventionRecord:
        record = InterventionRecord(
            record_id=uuid4().hex[:12], verdict=verdict, entity_id=entity_id,
            automation_id=automation_id, old_state=old_state, new_state=new_state,
            timestamp=now, details=dict(details),
        )
        self.records.append(record)
        if len(self.records) > self.max_records:
            del self.records[: len(self.records) - self.max_records]
        audit_write(
            self.audit, at=now, kind="intervention", verdict=verdict.value,
            entity_id=entity_id, automation_id=automation_id,
            old_state=old_state, new_state=new_state, details=record.details,
        )
        return record

