"""conf 回灌 MA 的结构化反馈（IR §10「失败类型影响权重不同」）+ 分级模块共用垫片。

职责
----
1. FeedbackRecorder —— 所有会改动 ConfidenceStore 的事件的唯一写入口，按
   FeedbackWeights 分型加权，并同步写 samples，保证 conf 变更可追溯、可回灌。
2. FeedbackExporter —— 只读聚合多源反馈，输出 MA 需要的
   ``{automation_id, events:[{type, timestamp, details, conf_before, conf_after}]}``。
3. 公共垫片 —— clock_now / audit_write / read_state / conf_read / LaterFn，
   让新模块不必假设 Clock / AuditLog / StateProvider 的具体方法名。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Sequence
from uuid import uuid4

from autoforge.af_conf import NEGATIVE_DROP, POSITIVE_LIFT, ConfidenceStore

__all__ = [
    "FeedbackKind", "NEGATIVE_KINDS", "FeedbackWeights", "FeedbackEvent",
    "FeedbackRecorder", "FeedbackFilter", "FeedbackExporter", "FeedbackSource",
    "LaterFn", "new_id", "clamp_conf", "norm_state", "state_eq",
    "clock_now", "audit_write", "read_state", "conf_read",
]


# --------------------------------------------------------------------------- #
# 公共垫片
# --------------------------------------------------------------------------- #

LaterFn = Callable[[float, Callable[[], Any]], Any]
"""延迟调度函数：``later(seconds, callback)``，由 Runtime 的 Scheduler 适配而来。"""

FeedbackSource = Callable[[], Iterable["FeedbackEvent"]]


def new_id(prefix: str) -> str:
    """生成短随机 ID。"""
    return f"{prefix}-{uuid4().hex[:12]}"


def clamp_conf(value: float, floor: float = 0.0, ceiling: float = 1.0) -> float:
    """把 conf 夹到 [floor, ceiling]。"""
    return min(ceiling, max(floor, float(value)))


def norm_state(value: Any) -> str | None:
    """状态归一化：None 保持 None，其余转小写字符串。"""
    return None if value is None else str(value).strip().lower()


def state_eq(left: Any, right: Any) -> bool:
    """比较两个实体状态是否等价。"""
    return norm_state(left) == norm_state(right)


def clock_now(clock: Any) -> float:
    """取当前时间戳（秒）。兼容 ``Clock.now()`` 返回 float 或 datetime。"""
    raw = clock.now() if hasattr(clock, "now") else clock()
    return raw.timestamp() if isinstance(raw, datetime) else float(raw)


def audit_write(audit: Any, **fields: Any) -> Any:
    """写审计日志。兼容 ``AuditLog.append / .record / .add``。"""
    entry = dict(fields)
    if audit is None:
        return entry
    for name in ("append", "record", "add"):
        fn = getattr(audit, name, None)
        if callable(fn):
            return fn(entry)
    return entry


def read_state(states: Any, entity_id: str) -> str | None:
    """读单个实体状态。兼容 ``StateProvider.get / .state_of / .state``。"""
    if states is None:
        return None
    for name in ("get", "state_of", "state"):
        fn = getattr(states, name, None)
        if callable(fn):
            return norm_state(fn(entity_id))
    try:
        return norm_state(states[entity_id])
    except Exception:
        return None


def conf_read(conf: ConfidenceStore, automation_id: str, default: float = 0.0) -> float:
    """读 conf，容忍未初始化的 automation_id。"""
    values = getattr(conf, "values", None)
    if isinstance(values, dict) and automation_id in values:
        return float(values[automation_id])
    try:
        return float(conf.get(automation_id))
    except Exception:
        return default


# --------------------------------------------------------------------------- #
# 反馈事件
# --------------------------------------------------------------------------- #

class FeedbackKind(str, Enum):
    """反馈类型。IR §10：失败类型影响权重不同，回灌 MA 时必须区分。"""

    SEED = "seed"                    # 初始化 conf（不加权）
    POSITIVE = "positive"            # 自动执行无误 / shadow 比对命中 / 人工批准
    USER_OVERRIDE = "user_override"  # 用户手动覆盖自动化给出的状态
    USER_REJECT = "user_reject"      # ask 被拒绝 / 提案被否决
    DEVICE_ERROR = "device_error"    # 设备异常（动作迟迟未落地等）
    DRIFT = "drift"                  # canary 漂移
    SHADOW_MISS = "shadow_miss"      # shadow 比对未命中（弱负样本）
    PROMOTED = "promoted"            # 晋升（审计事件，权重 0）
    DEMOTED = "demoted"              # 降级（审计事件，权重 0）


NEGATIVE_KINDS: frozenset[FeedbackKind] = frozenset({
    FeedbackKind.USER_OVERRIDE, FeedbackKind.USER_REJECT,
    FeedbackKind.DEVICE_ERROR, FeedbackKind.DRIFT, FeedbackKind.SHADOW_MISS,
})


@dataclass
class FeedbackWeights:
    """分型加权。IR §10：设备异常 ≠ 用户拒绝，回灌 MA 时区分。

    取值理由：用户意图冲突（override/reject）是 conf 的直接反证，取满额
    ``NEGATIVE_DROP``；canary 漂移说明行为不符合世界反馈，取 0.6 倍；
    设备异常不该由自动化背锅，取 0.2 倍；shadow 未命中只是「没被印证」
    而非反证，取小值以免把还能救的提案打到 ask 档。
    """

    seed: float = 0.0
    positive: float = POSITIVE_LIFT               # +0.03
    user_override: float = -NEGATIVE_DROP         # -0.25
    user_reject: float = -NEGATIVE_DROP           # -0.25
    drift: float = -NEGATIVE_DROP * 0.6           # -0.15
    device_error: float = -NEGATIVE_DROP * 0.2    # -0.05
    shadow_miss: float = -0.02
    promoted: float = 0.0
    demoted: float = 0.0

    def delta(self, kind: "FeedbackKind | str") -> float:
        """返回某类型反馈对应的 conf 增量。"""
        key = kind.value if isinstance(kind, FeedbackKind) else str(kind)
        return float(getattr(self, key, 0.0))


@dataclass
class FeedbackEvent:
    """一条结构化反馈。字段与 /api/feedback 的响应体一一对应。"""

    automation_id: str
    kind: FeedbackKind
    timestamp: float
    conf_before: float
    conf_after: float
    details: dict[str, Any] = field(default_factory=dict)
    source: str = ""
    event_id: str = field(default_factory=lambda: new_id("fb"))

    @property
    def is_negative(self) -> bool:
        """是否为负样本。"""
        return self.kind in NEGATIVE_KINDS

    def to_json(self) -> dict[str, Any]:
        """序列化为 MA 需要的结构。"""
        return {
            "type": self.kind.value,
            "timestamp": self.timestamp,
            "details": dict(self.details),
            "conf_before": self.conf_before,
            "conf_after": self.conf_after,
            "source": self.source,
            "event_id": self.event_id,
        }


@dataclass
class FeedbackRecorder:
    """conf 变更的唯一写入口。

    所有模块（shadow / intervention / canary / proposal）都必须经由 emit()
    改 conf，这样 ConfidenceStore.samples 与本事件流天然同源，回灌不丢信息。
    """

    conf: ConfidenceStore
    clock: Any
    audit: Any = None
    states: Any = None
    weights: FeedbackWeights = field(default_factory=FeedbackWeights)
    floor: float = 0.0
    ceiling: float = 1.0
    events: list[FeedbackEvent] = field(default_factory=list)
    max_events: int = 20000

    # ---- 写入 ----------------------------------------------------------- #

    def seed(self, automation_id: str, value: float, **details: Any) -> FeedbackEvent:
        """初始化 conf（不加权），用于提案部署时写入起始置信度。"""
        return self.emit(
            automation_id, FeedbackKind.SEED,
            details={"value": value, **details}, source="seed", override=value,
        )

    def emit(
        self,
        automation_id: str,
        kind: "FeedbackKind | str",
        *,
        details: Mapping[str, Any] | None = None,
        source: str = "",
        at: float | None = None,
        override: float | None = None,
    ) -> FeedbackEvent:
        """记录一条反馈并按权重更新 conf。

        ``override`` 用于跳过加权、直接写入目标值（如 seed / promote）。
        """
        kind = kind if isinstance(kind, FeedbackKind) else FeedbackKind(kind)
        now = clock_now(self.clock) if at is None else float(at)
        before = conf_read(self.conf, automation_id)
        after = before if override is not None else before + self.weights.delta(kind)
        if override is not None:
            after = float(override)
        after = clamp_conf(after, self.floor, self.ceiling)

        self.conf.values[automation_id] = after
        self.conf.samples.setdefault(automation_id, []).append((kind.value, after))

        event = FeedbackEvent(
            automation_id=automation_id, kind=kind, timestamp=now,
            conf_before=before, conf_after=after,
            details=dict(details or {}), source=source,
        )
        self.events.append(event)
        if len(self.events) > self.max_events:
            del self.events[: len(self.events) - self.max_events]
        audit_write(
            self.audit, at=now, kind="conf_feedback", source=source,
            automation_id=automation_id, feedback=kind.value,
            conf_before=before, conf_after=after, details=event.details,
        )
        return event

    # ---- 持久化 --------------------------------------------------------- #

    def dump(self) -> list[dict[str, Any]]:
        """导出为 JSON 兼容结构。"""
        return [e.to_json() | {"automation_id": e.automation_id} for e in self.events]

    def load(self, rows: Iterable[Mapping[str, Any]]) -> int:
        """从 JSON 结构恢复事件流（不重放 conf 变更）。"""
        count = 0
        for row in rows:
            self.events.append(FeedbackEvent(
                automation_id=str(row["automation_id"]),
                kind=FeedbackKind(row["type"]),
                timestamp=float(row["timestamp"]),
                conf_before=float(row.get("conf_before", 0.0)),
                conf_after=float(row.get("conf_after", 0.0)),
                details=dict(row.get("details") or {}),
                source=str(row.get("source", "")),
                event_id=str(row.get("event_id") or new_id("fb")),
            ))
            count += 1
        return count


# --------------------------------------------------------------------------- #
# 导出（供 MA 拉取）
# --------------------------------------------------------------------------- #

@dataclass
class FeedbackFilter:
    """查询过滤器。"""

    from_ts: float | None = None
    to_ts: float | None = None
    automation_id: str | None = None
    kinds: set["FeedbackKind | str"] | None = None
    negatives_only: bool = False

    def match(self, event: FeedbackEvent) -> bool:
        """判断事件是否命中过滤器。"""
        if self.from_ts is not None and event.timestamp < self.from_ts:
            return False
        if self.to_ts is not None and event.timestamp > self.to_ts:
            return False
        if self.automation_id is not None and event.automation_id != self.automation_id:
            return False
        if self.kinds is not None:
            wanted = {k.value if isinstance(k, FeedbackKind) else str(k) for k in self.kinds}
            if event.kind.value not in wanted:
                return False
        if self.negatives_only and not event.is_negative:
            return False
        return True


def store_source(conf: ConfidenceStore) -> FeedbackSource:
    """把 ``ConfidenceStore.samples`` 里的历史条目转成合成事件源。

    正常情况下 samples 由 FeedbackRecorder 统一写入、天然同源；本函数用于
    迁移过来的老数据或外部直接写过 samples 的场景，合成事件带
    ``synthetic=True`` 标记且无时间戳（timestamp=0）。
    """

    def _collect() -> Iterable[FeedbackEvent]:
        samples = getattr(conf, "samples", {}) or {}
        values = getattr(conf, "values", {}) or {}
        # 为每个 automation_id 合成 seed 事件（如果 samples 里没有 seed 记录）
        for aid, val in values.items():
            rows = samples.get(aid, [])
            has_seed = any(
                (isinstance(r, (tuple, list)) and len(r) >= 1 and r[0] == "seed")
                for r in rows
            )
            if not has_seed:
                yield FeedbackEvent(
                    automation_id=aid, kind=FeedbackKind.SEED, timestamp=0.0,
                    conf_before=0.0, conf_after=float(val),
                    details={"synthetic": True, "origin": "confidence_store.values"},
                    source="confidence_store",
                )
        for aid, rows in samples.items():
            prev: float | None = None
            for kind_name, value in rows:
                try:
                    kind = FeedbackKind(kind_name)
                except ValueError:
                    kind = FeedbackKind.SEED
                yield FeedbackEvent(
                    automation_id=aid, kind=kind, timestamp=0.0,
                    conf_before=0.0 if prev is None else prev, conf_after=float(value),
                    details={"synthetic": True, "origin": "confidence_store.samples"},
                    source="confidence_store",
                )
                prev = float(value)

    return _collect


@dataclass
class FeedbackExporter:
    """聚合多源反馈，供 MA 按时间范围 / 自动化 / 类型拉取。"""

    recorder: FeedbackRecorder
    extra_sources: Sequence[FeedbackSource] = field(default_factory=tuple)

    def events(self, filt: "FeedbackFilter | None" = None) -> list[FeedbackEvent]:
        """返回命中的事件（按时间升序）。"""
        filt = filt or FeedbackFilter()
        merged: list[FeedbackEvent] = list(self.recorder.events)
        for src in self.extra_sources:
            merged.extend(src())
        hits = [e for e in merged if filt.match(e)]
        hits.sort(key=lambda e: (e.timestamp, e.event_id))
        return hits

    def query(self, filt: "FeedbackFilter | None" = None) -> dict[str, Any]:
        """聚合视图：``{"automations": [{automation_id, events: [...]}]}``。"""
        grouped: dict[str, list[FeedbackEvent]] = {}
        for event in self.events(filt):
            grouped.setdefault(event.automation_id, []).append(event)
        return {
            "automations": [
                {
                    "automation_id": aid,
                    "events": [e.to_json() for e in rows],
                }
                for aid, rows in sorted(grouped.items())
            ],
        }

    def to_json(self, filt: "FeedbackFilter | None" = None) -> list[dict[str, Any]]:
        """逐自动化输出 ``{automation_id, events: [...]}``，与 4.2 契约一致。"""
        return [
            {"automation_id": block["automation_id"], "events": block["events"]}
            for block in self.query(filt)["automations"]
        ]
