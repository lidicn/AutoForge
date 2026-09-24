"""AutoForge · 冲突审计与持久化。

职责：承接 ConflictArbiter 产出的 ConflictEvent，做内存留存 + JSONL 落盘，
并导出给 MA / WebUI 分析（history / summary / locks）。

持久化：`.forge/conflict_audit.json`（与 shadow_log / interventions 同目录），
JSONL 追加写，每行一个 ConflictEvent；启动时自动回放恢复（持久化恢复）。
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Iterable

try:  # 包内加载 / 单文件加载两种方式都可用
    from .af_conflict import ConflictEvent, ResourceLock
except ImportError:  # pragma: no cover
    from af_conflict import ConflictEvent, ResourceLock

AUDIT_FILENAME = "conflict_audit.json"

_FLICKER_KINDS = {"flicker", "throttled"}


def event_to_dict(event: ConflictEvent) -> dict:
    return {
        "event_id": event.event_id,
        "entity_id": event.entity_id,
        "kind": event.kind,
        "requester_id": event.requester_id,
        "holder_id": event.holder_id,
        "timestamp": event.timestamp,
        "details": dict(event.details or {}),
    }


def event_from_dict(data: dict) -> ConflictEvent:
    return ConflictEvent(
        event_id=str(data.get("event_id", "")),
        entity_id=str(data.get("entity_id", "")),
        kind=str(data.get("kind", "")),
        requester_id=str(data.get("requester_id", "")),
        holder_id=data.get("holder_id"),
        timestamp=float(data.get("timestamp", 0.0)),
        details=dict(data.get("details") or {}),
    )


def lock_to_dict(lock: ResourceLock) -> dict:
    return {
        "entity_id": lock.entity_id,
        "automation_id": lock.automation_id,
        "instance_id": lock.instance_id,
        "action": lock.action,
        "acquired_at": lock.acquired_at,
        "ttl": lock.ttl,
        "priority": lock.priority,
    }


def lock_from_dict(data: dict) -> ResourceLock:
    return ResourceLock(
        entity_id=str(data.get("entity_id", "")),
        automation_id=str(data.get("automation_id", "")),
        instance_id=str(data.get("instance_id", "")),
        action=str(data.get("action", "")),
        acquired_at=float(data.get("acquired_at", 0.0)),
        ttl=float(data.get("ttl", 0.0)),
        priority=float(data.get("priority", 0.0)),
    )


class ConflictAuditor:
    """冲突审计器：内存环形缓冲 + JSONL 追加写。"""

    def __init__(
        self,
        persist_dir: str | None = None,
        *,
        max_memory: int = 2000,
        lock_provider: Callable[[], Iterable[ResourceLock]] | None = None,
        autoload: bool = True,
    ) -> None:
        self.persist_dir = persist_dir
        self.max_memory = int(max_memory)
        self.lock_provider = lock_provider
        self.events: list[ConflictEvent] = []
        self._lock_snapshot: list[ResourceLock] = []
        if persist_dir and autoload:
            self.reload()

    # ------------------------------------------------------------------ #
    def record(self, event: ConflictEvent) -> None:
        """记录一条冲突事件（不可跳过：任何已产生事件都必须落盘）。"""
        self.events.append(event)
        if len(self.events) > self.max_memory:
            self.events = self.events[-self.max_memory:]
        self._persist(event)

    def history(
        self,
        entity_id: str | None = None,
        automation_id: str | None = None,
        limit: int = 100,
        kind: str | None = None,
    ) -> list[ConflictEvent]:
        out: list[ConflictEvent] = []
        for event in reversed(self.events):     # 最新在前
            if entity_id is not None and event.entity_id != entity_id:
                continue
            if kind is not None and event.kind != kind:
                continue
            if automation_id is not None and not self._involved(event, automation_id):
                continue
            out.append(event)
            if len(out) >= max(0, int(limit)):
                break
        return out

    def summary(self, automation_id: str) -> dict:
        involved = [e for e in self.events if self._involved(e, automation_id)]

        def count(kinds: set[str]) -> int:
            return sum(1 for e in involved if e.kind in kinds)

        return {
            "total": len(involved),
            "rejected": count({"rejected"}),
            "preempted": count({"preempted"}),
            "circuit_open": count({"circuit_open"}),
            "flicker": count(_FLICKER_KINDS),
        }

    def automation_ids(self) -> list[str]:
        ids = {e.requester_id for e in self.events if e.requester_id} | {
            e.holder_id for e in self.events if e.holder_id
        }
        ids.discard("user")
        return sorted(ids)

    def locks(self) -> list[dict]:
        """当前所有锁的快照（优先取 live provider）。"""
        if self.lock_provider is not None:
            try:
                return [lock_to_dict(l) for l in self.lock_provider()]
            except Exception:
                pass
        return [lock_to_dict(l) for l in self._lock_snapshot]

    def snapshot_locks(self, locks: Iterable[ResourceLock]) -> None:
        self._lock_snapshot = list(locks)

    # ------------------------------------------------------------------ #
    @property
    def file_path(self) -> str | None:
        if not self.persist_dir:
            return None
        return os.path.join(self.persist_dir, AUDIT_FILENAME)

    def reload(self) -> int:
        """从 JSONL 回放恢复（持久化恢复）。"""
        path = self.file_path
        if not path or not os.path.exists(path):
            return 0
        loaded = 0
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    self.events.append(event_from_dict(json.loads(line)))
                    loaded += 1
                except Exception:
                    continue
        if len(self.events) > self.max_memory:
            self.events = self.events[-self.max_memory:]
        return loaded

    def clear(self) -> None:
        self.events = []
        self._lock_snapshot = []

    # ------------------------------------------------------------------ #
    @staticmethod
    def _involved(event: ConflictEvent, automation_id: str) -> bool:
        return event.requester_id == automation_id or event.holder_id == automation_id

    def _persist(self, event: ConflictEvent) -> None:
        path = self.file_path
        if not path:
            return
        try:
            directory = os.path.dirname(path) or "."
            os.makedirs(directory, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event_to_dict(event), ensure_ascii=False, sort_keys=True))
                fh.write("\n")
        except Exception:
            pass    # 落盘失败不影响内存审计
