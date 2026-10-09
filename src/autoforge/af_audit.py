"""结构化审计 —— 供 G4 回灌 MA 与人工排障。

G1 记录四类必填事件（IR §9.4 的失败类型）加上总线/配额类系统事件：
- `entity_drift`  实体漂移（引用了不存在的实体）→ 走 on_error 软失效
- `action_failed` 适配器调用失败（设备异常 / 5xx）
- `breaker_open` / `breaker_recover`  实体变更频率熔断
- `quota_exceeded` 并发配额超限
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

__all__ = ["AuditEvent", "AuditLog", "DEPLOY_AUDIT", "WRITE_CONFLICT", "record_conflict"]

ENTITY_DRIFT = "entity_drift"
ACTION_FAILED = "action_failed"
#: v0.3.0 跨自动化事件·发布侧（`emit` 发布的自定义事件）
EVENT_EMITTED = "event_emitted"
BREAKER_OPEN = "breaker_open"
BREAKER_RECOVER = "breaker_recover"
QUOTA_EXCEEDED = "quota_exceeded"
INSTANCE_REJECTED = "instance_rejected"
#: P2-4：触发被去抖（debounce）抑制，此前无任何观测记录
INSTANCE_DEBOUNCED = "instance_debounced"
INSTANCE_EXPIRED = "instance_expired"
#: P1 实例持久化：崩溃恢复时成功挂回 / 因图变更被丢弃
INSTANCE_RESTORED = "instance_restored"
INSTANCE_RESTORE_DROPPED = "instance_restore_dropped"
#: B.14：恢复出的挂起实例**重建不出一条可应答的会话**（挂起节点已不在当前图）。
#: 不静默跳过——这条实例会一直 suspended 且在任何问句面上都看不见。
INSTANCE_SESSION_LOST = "instance_session_lost"
#: v0.9.0 跨进程：写入版本冲突（expect_version 不匹配）与恢复时租约仍属其他进程
WRITE_CONFLICT = "write_conflict"
INSTANCE_LEASE_HELD = "instance_lease_held"
#: P1-3：事件总线订阅回调异常隔离（单个 handler 抛错不影响其他订阅者）
HANDLER_FAILED = "handler_failed"
#: v2 M1 首演码仪式：签发 / 消费 / 试演期开始 / 试演期暂停 / 试演期断言失败
PREMIERE_ISSUED = "premiere_issued"
PREMIERE_CONSUMED = "premiere_consumed"
PREMIERE_TRIAL_STARTED = "premiere_trial_started"
PREMIERE_TRIAL_PAUSED = "premiere_trial_paused"
TRIAL_ASSERT_FAILED = "trial_assert_failed"
#: 裁定 20261009 §三 硬前置：`requires_confirm` 的运行期确认。一次确认只放行一次下发，
#: 所以"通过"与"未通过"都要各自留一条能对上节点与动作的账（拒绝侧不许只落在会被截断的 trace 里）。
CONFIRM_GRANTED = "confirm_granted"
CONFIRM_DENIED = "confirm_denied"

ALL_EVENT_TYPES = (
    ENTITY_DRIFT,
    ACTION_FAILED,
    EVENT_EMITTED,
    BREAKER_OPEN,
    BREAKER_RECOVER,
    QUOTA_EXCEEDED,
    INSTANCE_REJECTED,
    INSTANCE_EXPIRED,
    INSTANCE_DEBOUNCED,
    INSTANCE_RESTORED,
    INSTANCE_RESTORE_DROPPED,
    INSTANCE_SESSION_LOST,
    WRITE_CONFLICT,
    INSTANCE_LEASE_HELD,
    HANDLER_FAILED,
    PREMIERE_ISSUED,
    PREMIERE_CONSUMED,
    PREMIERE_TRIAL_STARTED,
    PREMIERE_TRIAL_PAUSED,
    TRIAL_ASSERT_FAILED,
    CONFIRM_GRANTED,
    CONFIRM_DENIED,
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
    """内存审计日志（G1 不落盘；结构保持不变，G2 可直接换持久化实现）。

    P1-1 修复：用有界 deque（保留最近 5000 条）防止常驻进程无限增长——
    审计数组随运行时间线性膨胀，且 `Runtime.stats()` 每次全量序列化，会成为性能杀手。
    """

    events: deque[AuditEvent] = field(default_factory=lambda: deque(maxlen=5000))

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


#: 部署链（`af_apply.issue_premiere` / `apply`）的进程级审计日志。
#: 它**不是** `runtime.audit`：apply 这条链上没有 runtime 实例可挂载。过去四处都写成
#: `AuditLog.add(event)`——把实例方法当类方法调，`event` 落到了 `self` 上，运行即
#: `TypeError: AuditLog.add() missing 1 required positional argument`，于是
#: "入队成功之后崩在记账上"，部署仪式整条路走不通。
DEPLOY_AUDIT = AuditLog()


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
