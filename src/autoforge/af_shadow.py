"""Shadow 运行时执行器（IR §10 Shadow 档位的运行时闭环）。

ShadowRunner 装饰 ``NodeExecutor._do``（Python 实例属性优先于类属性，故可不改
af_executor.py 就地拦截），按 ConfidenceStore 的**运行时 band** 路由：

* ``auto``   → 透传原 _do（真实执行），并向 InterventionDetector 报到；
* ``shadow`` → **绝不调用 adapter.call**，只记 shadow_log：动作 + 参数 +
  期望状态，延迟 ``compare_after`` 秒后只读比对目标实体是否真的变成了期望态；
* ``ask``    → 不执行，生成 ask 提案挂起等人确认。

达标转正：连续 ``streak_to_promote`` 次命中（或 conf 自行爬到 AUTO_MIN）
→ ``conf.promote()`` 拉满，并回调 ``on_promote``（重新挂 canary 保护 +
启动 CanarySupervisor 观察期）。

Shadow 日志独立存放（``shadow_log.json``），与正常运行日志隔离。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Sequence
from uuid import uuid4

from autoforge.af_conf import AUTO_MIN, Band, ConfidenceStore
from autoforge.af_feedback import (
    FeedbackKind, FeedbackRecorder, LaterFn,
    audit_write, clock_now, conf_read, norm_state,
)

__all__ = [
    "Verdict", "ShadowRecord", "ShadowLogStore", "ShadowPolicy",
    "ExpectedStateResolver", "DefaultExpectedStateResolver", "DEFAULT_EFFECTS",
    "ShadowRunner", "ShadowBinding",
]


class Verdict(str, Enum):
    """影子比对结论。"""

    PENDING = "pending"
    MATCHED = "matched"
    MISSED = "missed"
    UNVERIFIABLE = "unverifiable"
    EXPIRED = "expired"


@dataclass
class ShadowRecord:
    """一条影子执行记录：「如果执行会做什么」+ 比对结果。"""

    record_id: str
    automation_id: str
    instance_id: str
    node_id: str
    action: str
    params: dict[str, Any]
    expected_state: dict[str, str | None]
    created_at: float
    compare_after: float
    checked_at: float | None = None
    observed_state: dict[str, str | None] | None = None
    verdict: Verdict = Verdict.PENDING
    note: str = ""

    def to_json(self) -> dict[str, Any]:
        """序列化。"""
        return {
            "record_id": self.record_id, "automation_id": self.automation_id,
            "instance_id": self.instance_id, "node_id": self.node_id,
            "action": self.action, "params": dict(self.params),
            "expected_state": dict(self.expected_state),
            "observed_state": dict(self.observed_state or {}),
            "timestamp": self.created_at, "created_at": self.created_at,
            "checked_at": self.checked_at,
            "compare_after": self.compare_after,
            "verdict": self.verdict.value, "note": self.note,
        }


@dataclass
class ShadowLogStore:
    """shadow_log 独立存储（JSON 文件），与正常运行日志隔离。"""

    records: list[ShadowRecord] = field(default_factory=list)

    def append(self, record: ShadowRecord) -> ShadowRecord:
        """追加记录。"""
        self.records.append(record)
        return record

    def get(self, record_id: str) -> ShadowRecord | None:
        """按记录 ID 取。"""
        for record in reversed(self.records):
            if record.record_id == record_id:
                return record
        return None

    def by_automation(self, automation_id: str) -> list[ShadowRecord]:
        """取某自动化的全部影子记录。"""
        return [r for r in self.records if r.automation_id == automation_id]

    def due(self, now: float) -> list[ShadowRecord]:
        """已到比对时间且仍未判定的记录。"""
        return [
            r for r in self.records
            if r.verdict is Verdict.PENDING and r.created_at + r.compare_after <= now
        ]

    def streak(self, automation_id: str) -> int:
        """连续命中次数（只计已判定记录，UNVERIFIABLE/PENDING 不打断也不累加）。"""
        count = 0
        for record in reversed(self.records):
            if record.automation_id != automation_id:
                continue
            if record.verdict is Verdict.MATCHED:
                count += 1
            elif record.verdict in (Verdict.MISSED, Verdict.EXPIRED):
                break
        return count

    def trim(self, max_records: int) -> None:
        """限制内存占用。"""
        if max_records > 0 and len(self.records) > max_records:
            del self.records[: len(self.records) - max_records]

    def dump(self) -> list[dict[str, Any]]:
        """导出 JSON。"""
        return [r.to_json() for r in self.records]

    def load(self, rows: Sequence[Mapping[str, Any]]) -> int:
        """从 JSON 恢复。"""
        for row in rows:
            self.records.append(ShadowRecord(
                record_id=str(row["record_id"]),
                automation_id=str(row["automation_id"]),
                instance_id=str(row.get("instance_id", "")),
                node_id=str(row.get("node_id", "")),
                action=str(row.get("action", "")),
                params=dict(row.get("params") or {}),
                expected_state=dict(row.get("expected_state") or {}),
                created_at=float(row.get("created_at", row.get("timestamp", 0.0))),
                compare_after=float(row.get("compare_after", 0.0)),
                checked_at=row.get("checked_at"),
                observed_state=row.get("observed_state"),
                verdict=Verdict(row.get("verdict", "pending")),
                note=str(row.get("note", "")),
            ))
        return len(rows)

    def save(self, path: str) -> None:
        """落盘 shadow_log.json。"""
        import json
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.dump(), fh, ensure_ascii=False, indent=2)

    def load_file(self, path: str) -> int:
        """从 shadow_log.json 恢复。"""
        import json
        with open(path, "r", encoding="utf-8") as fh:
            return self.load(json.load(fh))


# --------------------------------------------------------------------------- #
# 期望状态推导
# --------------------------------------------------------------------------- #

DEFAULT_EFFECTS: dict[str, str] = {
    "turn_on": "on",
    "turn_off": "off",
    "open_cover": "open",
    "close_cover": "closed",
    "open": "open",
    "close": "closed",
    "lock": "locked",
    "unlock": "unlocked",
    "set_hvac_mode": "@params.hvac_mode",
    "set_state": "@params.state",
    # toggle / set_cover_position 等无法静态推导期望态的动作不在表内 → 不可验证
}

ExpectedStateResolver = Callable[[Any, Sequence[str]], Mapping[str, Any]]


@dataclass
class DefaultExpectedStateResolver:
    """推导 ``do`` 节点执行后目标实体应有的状态。

    优先级：``node.expected``（IR 显式声明） > ``params.expected_state``
    > DEFAULT_EFFECTS 动作效果表。推导不出 → 返回空表（记录为不可验证，
    不计入转正证据）。
    """

    effects: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_EFFECTS))
    attr: str = "expected"

    def __call__(self, node: Any, entities: Sequence[str]) -> dict[str, str | None]:
        """返回 ``{entity_id: expected_state}``。"""
        entities = [str(e) for e in entities]
        explicit = getattr(node, self.attr, None)
        if isinstance(explicit, Mapping) and explicit:
            if entities:
                return {e: norm_state(explicit.get(e)) for e in entities}
            return {str(k): norm_state(v) for k, v in explicit.items()}

        params = getattr(node, "params", None) or {}
        if isinstance(params, Mapping) and params.get("expected_state") is not None:
            value = norm_state(params.get("expected_state"))
            return {e: value for e in entities}

        template = self.effects.get(str(getattr(node, "action", "") or ""))
        if not template:
            return {}
        if template.startswith("@params."):
            key = template.split(".", 1)[1]
            if not isinstance(params, Mapping) or key not in params:
                return {}
            value = norm_state(params.get(key))
        else:
            value = norm_state(template)
        return {e: value for e in entities}


@dataclass
class ShadowPolicy:
    """全部阈值可配。"""

    compare_after: float = 300.0            # 延迟比对秒数 T
    streak_to_promote: int = 3              # 连续命中 M 次转正
    intercept_all_do: bool = True           # shadow/ask 档拦截一切 do（含不写设备的）
    max_records: int = 5000
    ask_prompt_template: str = (
        "自动化「{automation_id}」请求执行 {action}（参数 {params}），请确认是否放行。"
    )


@dataclass
class ShadowBinding:
    """``install()`` 的还原句柄。"""

    executor: Any
    original: Any
    had_own_attr: bool

    def restore(self) -> None:
        """还原被装饰的 ``_do``。"""
        if self.had_own_attr:
            setattr(self.executor, "_do", self.original)
        elif hasattr(self.executor, "_do"):
            delattr(self.executor, "_do")


def _automation_id(instance: Any) -> str:
    auto = getattr(instance, "automation", None)
    return str(getattr(auto, "id", None) or getattr(instance, "automation_id", "") or "")


def _instance_id(instance: Any) -> str:
    return str(getattr(instance, "id", None) or getattr(instance, "instance_id", "") or "")


@dataclass
class ShadowRunner:
    """Executor 装饰器：按运行时 band 路由 do 节点（难点 1 + ask 档）。"""

    conf: ConfidenceStore
    states: Any
    recorder: FeedbackRecorder
    clock: Any
    audit: Any = None
    log: ShadowLogStore = field(default_factory=ShadowLogStore)
    policy: ShadowPolicy = field(default_factory=ShadowPolicy)
    resolver: ExpectedStateResolver = field(default_factory=DefaultExpectedStateResolver)
    later: LaterFn | None = None
    ask_handler: Callable[..., Any] | None = None
    on_promote: Callable[[str], None] | None = None
    intervention: Any = None

    # ---- 装饰 ----------------------------------------------------------- #

    def install(self, executor: Any) -> ShadowBinding:
        """实例级装饰 ``executor._do``，返回可还原的句柄。"""
        original = getattr(executor, "_do", None)
        had_own = "_do" in getattr(executor, "__dict__", {})
        runner = self

        def _do(instance: Any, node: Any, _orig: Any = original) -> Any:
            aid = _automation_id(instance)
            band = runner.band_of(aid)
            if band == "shadow":
                runner.run_do(instance, node)
                return None
            if band == "ask":
                runner.open_ask(instance, node)
                return None
            if runner.intervention is not None and getattr(node, "kind", "") == "do":
                runner.intervention.note_node(
                    aid, _instance_id(instance), node,
                    runner.resolver(node, node.target_entities() or ()),
                )
            return _orig(instance, node)

        setattr(executor, "_do", _do)
        return ShadowBinding(executor=executor, original=original, had_own_attr=had_own)

    def band_of(self, automation_id: str) -> Band:
        """读运行时 band（来自 ConfidenceStore，不是 IR 静态字段）。"""
        return self.conf.band(automation_id)

    # ---- shadow 拦截 ---------------------------------------------------- #

    def run_do(self, instance: Any, node: Any) -> ShadowRecord:
        """影子执行：记录「如果执行会做什么」，**不调用 adapter.call**。"""
        aid = _automation_id(instance)
        entities = [str(e) for e in (node.target_entities() or ())]
        expected = dict(self.resolver(node, entities) or {})
        now = clock_now(self.clock)
        record = ShadowRecord(
            record_id=uuid4().hex[:12], automation_id=aid,
            instance_id=_instance_id(instance), node_id=str(getattr(node, "id", "")),
            action=str(getattr(node, "action", "")),
            params=dict(getattr(node, "params", None) or {}),
            expected_state=expected, created_at=now,
            compare_after=self.policy.compare_after,
        )
        self.log.append(record)
        if not expected:
            record.verdict = Verdict.UNVERIFIABLE
            record.note = "无法推导期望状态，不计入转正证据"
        else:
            self._schedule(record)
        self.log.trim(self.policy.max_records)
        audit_write(
            self.audit, at=now, kind="shadow_intercepted", automation_id=aid,
            instance_id=record.instance_id, node_id=record.node_id,
            action=record.action, params=record.params,
            expected_state=record.expected_state, record_id=record.record_id,
            adapter_called=False,
        )
        return record

    def _schedule(self, record: ShadowRecord) -> None:
        if self.later is None:
            return

        def _fire() -> None:
            self.compare(record.record_id)

        self.later(record.compare_after, _fire)

    # ---- 只读比对 ------------------------------------------------------- #

    def compare(self, record_id: str, at: float | None = None) -> ShadowRecord | None:
        """延迟 T 秒后比对目标实体状态是否 == expected_state。"""
        from autoforge.af_feedback import read_state

        record = self.log.get(record_id)
        if record is None or record.verdict is not Verdict.PENDING:
            return record
        now = clock_now(self.clock) if at is None else float(at)
        observed = {e: read_state(self.states, e) for e in record.expected_state}
        record.observed_state = observed
        record.checked_at = now

        hit = all(
            norm_state(record.expected_state[e]) == norm_state(observed[e])
            for e in record.expected_state
        )
        if hit:
            record.verdict = Verdict.MATCHED
            self.recorder.emit(
                record.automation_id, FeedbackKind.POSITIVE, source="shadow", at=now,
                details={"basis": "shadow_match", "record_id": record.record_id,
                         "action": record.action,
                         "expected_state": record.expected_state},
            )
            self.promote_if_ready(record.automation_id, at=now)
        else:
            record.verdict = Verdict.MISSED
            self.recorder.emit(
                record.automation_id, FeedbackKind.SHADOW_MISS, source="shadow", at=now,
                details={"basis": "shadow_miss", "record_id": record.record_id,
                         "action": record.action,
                         "expected_state": record.expected_state,
                         "observed_state": observed},
            )
        audit_write(
            self.audit, at=now, kind="shadow_compared", automation_id=record.automation_id,
            record_id=record.record_id, verdict=record.verdict.value,
            expected_state=record.expected_state, observed_state=observed,
        )
        return record

    def compare_due(self, at: float | None = None) -> list[ShadowRecord]:
        """批量比对到期记录（供 Scheduler 定时器调用，重启后兜底）。"""
        now = clock_now(self.clock) if at is None else float(at)
        out: list[ShadowRecord] = []
        for record in self.log.due(now):
            judged = self.compare(record.record_id, at=now)
            if judged is not None:
                out.append(judged)
        return out

    # ---- 达标转正 ------------------------------------------------------- #

    def promote_if_ready(self, automation_id: str, at: float | None = None) -> bool:
        """连续 M 次命中或 conf 自行爬到 AUTO_MIN → 拉满并回调 on_promote。"""
        now = clock_now(self.clock) if at is None else float(at)
        streak = self.log.streak(automation_id)
        current = conf_read(self.conf, automation_id)
        if streak < self.policy.streak_to_promote and current < AUTO_MIN:
            return False
        self.conf.promote(automation_id)
        basis = "streak" if streak >= self.policy.streak_to_promote else "conf_threshold"
        audit_write(
            self.audit, at=now, kind="shadow_promoted", automation_id=automation_id,
            streak=streak, conf_before=current, conf_after=conf_read(self.conf, automation_id),
            basis=basis,
        )
        self.recorder.emit(
            automation_id, FeedbackKind.PROMOTED, source="shadow", at=now,
            details={"basis": basis, "streak": streak, "conf_before": current},
        )
        if self.on_promote is not None:
            self.on_promote(automation_id)
        return True

    # ---- ask 挂起 ------------------------------------------------------- #

    def open_ask(self, instance: Any, node: Any) -> Any:
        """conf < 0.60：不执行 do，生成 ask 提案挂起等人确认。"""
        aid = _automation_id(instance)
        iid = _instance_id(instance)
        entities = [str(e) for e in (node.target_entities() or ())]
        expected = dict(self.resolver(node, entities) or {})
        action = str(getattr(node, "action", ""))
        params = dict(getattr(node, "params", None) or {})
        prompt = self.policy.ask_prompt_template.format(
            automation_id=aid, action=action, params=params,
        )
        ticket = None
        if self.ask_handler is not None:
            ticket = self.ask_handler(
                automation_id=aid, instance_id=iid, node=node,
                expected_state=expected, prompt=prompt,
            )
        audit_write(
            self.audit, at=clock_now(self.clock), kind="ask_opened",
            automation_id=aid, instance_id=iid, node_id=str(getattr(node, "id", "")),
            action=action, params=params, expected_state=expected, prompt=prompt,
            proposal_id=getattr(ticket, "proposal_id", None),
        )
        return ticket

