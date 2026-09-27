"""结构化审计 —— 供 G4 回灌 MA 与人工排障。

G1 记录四类必填事件（IR §9.4 的失败类型）加上总线/配额类系统事件：
- `entity_drift`  实体漂移（引用了不存在的实体）→ 走 on_error 软失效
- `action_failed` 适配器调用失败（设备异常 / 5xx）
- `breaker_open` / `breaker_recover`  实体变更频率熔断
- `quota_exceeded` 并发配额超限
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

__all__ = ["AuditEvent", "AuditLog", "WRITE_CONFLICT", "record_conflict"]

ENTITY_DRIFT = "entity_drift"
ACTION_FAILED = "action_failed"
#: v0.3.0 跨自动化事件·发布侧（`emit` 发布的自定义事件）
EVENT_EMITTED = "event_emitted"
BREAKER_OPEN = "breaker_open"
BREAKER_RECOVER = "breaker_recover"
QUOTA_EXCEEDED = "quota_exceeded"
INSTANCE_REJECTED = "instance_rejected"
INSTANCE_EXPIRED = "instance_expired"
#: P1 实例持久化：崩溃恢复时成功挂回 / 因图变更被丢弃
INSTANCE_RESTORED = "instance_restored"
INSTANCE_RESTORE_DROPPED = "instance_restore_dropped"
#: v0.9.0 跨进程：写入版本冲突（expect_version 不匹配）与恢复时租约仍属其他进程
WRITE_CONFLICT = "write_conflict"
INSTANCE_LEASE_HELD = "instance_lease_held"
#: v2 M1 首演码仪式：签发 / 消费 / 试演期开始 / 试演期暂停 / 试演期断言失败
PREMIERE_ISSUED = "premiere_issued"
PREMIERE_CONSUMED = "premiere_consumed"
PREMIERE_TRIAL_STARTED = "premiere_trial_started"
PREMIERE_TRIAL_PAUSED = "premiere_trial_paused"
TRIAL_ASSERT_FAILED = "trial_assert_failed"

ALL_EVENT_TYPES = (
    ENTITY_DRIFT,
    ACTION_FAILED,
    EVENT_EMITTED,
    BREAKER_OPEN,
    BREAKER_RECOVER,
    QUOTA_EXCEEDED,
    INSTANCE_REJECTED,
    INSTANCE_EXPIRED,
    INSTANCE_RESTORED,
    INSTANCE_RESTORE_DROPPED,
    WRITE_CONFLICT,
    INSTANCE_LEASE_HELD,
    PREMIERE_ISSUED,
    PREMIERE_CONSUMED,
    PREMIERE_TRIAL_STARTED,
    PREMIERE_TRIAL_PAUSED,
    TRIAL_ASSERT_FAILED,
)


@dataclass(frozen=True)
class AuditEvent:
    """一条审计记录。字段保持扁平可 JSON 化。"""

    type: str
    at: datetime
    message: str
    automation_id: str = ""
    instance_id: str = ""
    node_id: str = ""
    entity_id: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "at": self.at.isoformat(),
            "message": self.message,
            "automation_id": self.automation_id,
            "instance_id": self.instance_id,
            "node_id": self.node_id,
            "entity_id": self.entity_id,
            "data": dict(self.data),
        }


@dataclass
class AuditLog:
    """内存审计日志（G1 不落盘；结构保持不变，G2 可直接换持久化实现）。"""

    events: list[AuditEvent] = field(default_factory=list)

    def add(self, event: AuditEvent) -> AuditEvent:
        self.events.append(event)
        return event

    def of_type(self, *types: str) -> list[AuditEvent]:
        return [e for e in self.events if e.type in types]

    def __iter__(self) -> Iterator[AuditEvent]:
        return iter(self.events)

    def __len__(self) -> int:
        return len(self.events)

    def clear(self) -> None:
        self.events.clear()


def record_conflict(
    journal: str | Path,
    *,
    name: str,
    expected: int | None,
    actual: int | None,
    writer: str = "",
    note: str = "",
) -> dict[str, Any]:
    """把一条 `write_conflict` 追加进跨进程共享的 JSONL 冲突日志（可审计）。

    追加单行（O_APPEND 语义，单行 <4KB）在无锁并发下也不会撕裂；
    返回写入的条目（供测试/调用方断言）。
    """
    entry = {
        "type": WRITE_CONFLICT,
        "at": datetime.now(timezone.utc).isoformat(),
        "name": name,
        "expected_version": expected,
        "actual_version": actual,
        "writer": writer,
        "note": note,
    }
    path = Path(journal)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry
