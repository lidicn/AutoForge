"""AutoForge · 跨自动化冲突仲裁器（核心）。

在 do 节点的 adapter.call 之前调用 request()，决定 ALLOW / REJECT / WAIT / CIRCUIT_OPEN。

架构要点
--------
* 锁粒度 = entity_id（light.study 与 light.living 不互斥）；多实体按字典序获取，
  任一失败整体放弃 -> 天然无死锁、无部分持有。
* request() 两阶段：先计划（续期 / 抢占 / 冲突分类），再一次性获取，避免抢占到一半回滚。
* TTL 只是兜底（do 崩溃防占用），正常路径必须显式 release()；到期下次 request 时惰性回收。
* 抢占条件严格大于（effective > holder.priority），同优先级不抢占 -> 后来者 REJECT。
* 有效优先级 = conf.get(automation_id) + aging（每等待 1 秒 +0.01，上限 +0.20）。
* 抖动：同一 entity 在 flicker_window 内 release >= flicker_threshold 次 -> 暂停 flicker_pause。
* 熔断：被拒 / 被抢占 +1，成功 -1；达阈值熔断 circuit_cooldown 秒 -> 半开试探，
  连续成功 circuit_half_open_success 次才完全恢复。
* 用户冷却优先于熔断：on_user_override 释放锁并冷却 cooldown_after_user 秒。
* 故障优先（fail-open）：仲裁器内部异常一律降级 ALLOW，并写 kind="degraded" 审计事件。
* 时间全部取注入的 TimeSource.monotonic()（单调时钟），保证测试确定性。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Iterable, Mapping, Sequence

if TYPE_CHECKING:  # 仅类型标注，运行时零依赖
    from .af_conf import ConfidenceStore
    from .af_time import TimeSource


# --------------------------------------------------------------------------
# 基础类型
# --------------------------------------------------------------------------
class RequestDecision(Enum):
    ALLOW = "allow"
    REJECT = "reject"
    WAIT = "wait"
    CIRCUIT_OPEN = "circuit_open"


@dataclass
class ResourceLock:
    entity_id: str
    automation_id: str
    instance_id: str
    action: str
    acquired_at: float       # monotonic
    ttl: float               # 锁持有时长（秒）
    priority: float          # 持有者的 conf 值


@dataclass
class ConflictEvent:
    event_id: str
    entity_id: str
    kind: str                 # rejected / preempted / throttled / flicker / circuit_* /
                              # user_cooldown / starved / degraded / forced_unlock
    requester_id: str         # 请求者 automation_id（用户动作记 "user"）
    holder_id: str | None     # 锁持有者 automation_id（如有）
    timestamp: float          # monotonic（由 TimeSource 提供）
    details: dict


@dataclass
class PendingRequest:
    entity_id: str
    automation_id: str
    instance_id: str
    action: str
    params: dict
    requested_at: float       # monotonic（aging 起点）
    base_priority: float      # 请求时的 conf 值


# 事件 kind 常量（审计汇总依赖这些名字）
KIND_REJECTED = "rejected"
KIND_PREEMPTED = "preempted"
KIND_THROTTLED = "throttled"
KIND_FLICKER = "flicker"
KIND_CIRCUIT_OPEN = "circuit_open"
KIND_CIRCUIT_HALF_OPEN = "circuit_half_open"
KIND_CIRCUIT_RECOVERED = "circuit_recovered"
KIND_USER_COOLDOWN = "user_cooldown"
KIND_STARVED = "starved"
KIND_DEGRADED = "degraded"
KIND_FORCED_UNLOCK = "forced_unlock"


@dataclass
class SystemTimeSource:
    """缺省时钟（仅供装配层兜底；测试一律注入假时钟）。"""

    def now(self) -> datetime:
        # naive 时钟一旦被接上 ensure_aware 的路径就是 TypeError；与 af_time.now() 同口径。
        return datetime.now(timezone.utc)

    def monotonic(self) -> float:
        return time.monotonic()


@dataclass
class _Circuit:
    """单个 automation 的熔断状态。"""

    conflict_count: int = 0
    status: str = "closed"          # closed / open / half_open
    open_until: float = 0.0
    half_open_successes: int = 0
    trial_inflight: bool = False


def _normalize_ids(entity_ids: Any) -> list[str]:
    """去重 + 字典序排序（多实体死锁预防的第一步）。"""
    if isinstance(entity_ids, str):
        raw: Iterable[Any] = [entity_ids]
    else:
        raw = list(entity_ids or [])
    seen: list[str] = []
    for item in raw:
        if isinstance(item, str) and item and item not in seen:
            seen.append(item)
    return sorted(seen)


# --------------------------------------------------------------------------
# 仲裁器
# --------------------------------------------------------------------------
class ConflictArbiter:
    """跨自动化冲突仲裁器：do 节点执行前的准入控制。"""

    def __init__(
        self,
        conf: "ConfidenceStore",
        clock: "TimeSource",
        *,
        lock_ttl: float = 10.0,
        preempt_enabled: bool = True,
        cooldown_after_user: float = 30.0,
        flicker_threshold: int = 4,
        flicker_window: float = 10.0,
        flicker_pause: float = 60.0,
        circuit_threshold: int = 5,
        circuit_cooldown: float = 300.0,
        circuit_half_open_success: int = 2,
        aging_rate: float = 0.01,
        aging_cap: float = 0.20,
        wait_enabled: bool = False,
        max_pending_wait: float = 60.0,
        on_event: Callable[[ConflictEvent], None] | None = None,
        on_preempted: Callable[[ResourceLock], None] | None = None,
        on_pending_ready: Callable[[PendingRequest], None] | None = None,
    ) -> None:
        self.conf = conf
        self.clock = clock
        self.lock_ttl = float(lock_ttl)
        self.preempt_enabled = bool(preempt_enabled)
        self.cooldown_after_user = float(cooldown_after_user)
        self.flicker_threshold = int(flicker_threshold)
        self.flicker_window = float(flicker_window)
        self.flicker_pause = float(flicker_pause)
        self.circuit_threshold = int(circuit_threshold)
        self.circuit_cooldown = float(circuit_cooldown)
        self.circuit_half_open_success = int(circuit_half_open_success)
        self.aging_rate = float(aging_rate)
        self.aging_cap = float(aging_cap)
        self.wait_enabled = bool(wait_enabled)
        self.max_pending_wait = float(max_pending_wait)
        self.on_event = on_event
        self.on_preempted = on_preempted
        self.on_pending_ready = on_pending_ready

        self._locks: dict[str, ResourceLock] = {}
        self._pending: dict[tuple[str, str, str], PendingRequest] = {}
        self._release_log: dict[str, list[float]] = {}
        self._flicker_until: dict[str, float] = {}
        self._cooldown_until: dict[str, float] = {}
        self._circuits: dict[str, _Circuit] = {}

    # ------------------------------------------------------------------ #
    # 公开 API
    # ------------------------------------------------------------------ #
    def request(
        self,
        entity_ids: list[str],
        automation_id: str,
        instance_id: str,
        action: str,
        params: dict,
        *,
        observe: bool = False,
    ) -> RequestDecision:
        """do 节点执行前调用。多实体按字典序排序获取锁。

        observe=True：只观测不拦截（事件照记，最终返回 ALLOW 并强制登记锁），
        用于「审计不缺席」的旁路模式。
        """
        ids = _normalize_ids(entity_ids)
        try:
            decision = self._arbitrate(ids, automation_id, instance_id, action, dict(params or {}))
            if observe and decision is not RequestDecision.ALLOW:
                now = self._now()
                self._acquire(ids, automation_id, instance_id, action, now, forced=True)
                return RequestDecision.ALLOW
            return decision
        except Exception as exc:  # 故障优先：绝不阻塞 do 节点
            self._emit(
                KIND_DEGRADED,
                ids[0] if ids else "",
                automation_id,
                None,
                {"phase": "request", "error": repr(exc)},
            )
            return RequestDecision.ALLOW

    def release(self, entity_ids: list[str], automation_id: str, *, success: bool = True) -> None:
        """do 节点完成后释放锁（success 用于熔断的成功/失败衰减）。"""
        try:
            now = self._now()
            self._expire(now)
            freed: list[str] = []
            for eid in _normalize_ids(entity_ids):
                lock = self._locks.get(eid)
                if lock is None or lock.automation_id != automation_id:
                    continue
                self._locks.pop(eid, None)
                freed.append(eid)
                self._note_release(eid, automation_id, now)
            self._note_result(automation_id, success, now)
            for eid in freed:
                self._wake(eid, now)
        except Exception as exc:
            self._emit(KIND_DEGRADED, "", automation_id, None, {"phase": "release", "error": repr(exc)})

    def on_user_override(self, entity_id: str) -> None:
        """InterventionDetector 检测到 user_override 时调用：释放锁 + 冷却期。"""
        try:
            now = self._now()
            lock = self._locks.pop(entity_id, None)
            self._cooldown_until[entity_id] = now + self.cooldown_after_user
            for key in [k for k in self._pending if k[2] == entity_id]:
                self._pending.pop(key, None)
            self._emit(
                KIND_USER_COOLDOWN,
                entity_id,
                "user",
                lock.automation_id if lock else None,
                {"phase": "start", "cooldown": self.cooldown_after_user, "released": lock is not None},
            )
        except Exception as exc:
            self._emit(KIND_DEGRADED, entity_id, "user", None, {"phase": "user_override", "error": repr(exc)})

    def locks(self) -> dict[str, ResourceLock]:
        """当前所有锁（可观测性）。"""
        return {eid: replace(lock) for eid, lock in self._locks.items()}

    def reset_circuit(self, automation_id: str) -> None:
        """手动重置熔断（WebUI 用）。"""
        prev = self._circuits.get(automation_id)
        self._circuits[automation_id] = _Circuit()
        self._emit(
            KIND_CIRCUIT_RECOVERED,
            "",
            automation_id,
            None,
            {
                "reason": "manual_reset",
                "previous_status": prev.status if prev else "closed",
                "previous_conflicts": prev.conflict_count if prev else 0,
            },
        )

    # ---- 扩展可观测 / 运维 API（装配层与 WebUI 用） ----
    def unlock(self, entity_id: str, *, reason: str = "forced") -> bool:
        """强制释放单个实体的锁（DELETE /api/conflicts/locks/{entity_id}）。"""
        try:
            now = self._now()
            self._expire(now)
            lock = self._locks.pop(entity_id, None)
            self._emit(
                KIND_FORCED_UNLOCK,
                entity_id,
                "user",
                lock.automation_id if lock else None,
                {"reason": reason, "released": lock is not None},
            )
            self._wake(entity_id, now)
            return lock is not None
        except Exception as exc:
            self._emit(KIND_DEGRADED, entity_id, "user", None, {"phase": "unlock", "error": repr(exc)})
            return False

    def pending(self) -> list[PendingRequest]:
        return [replace(p) for p in self._pending.values()]

    def circuit_state(self, automation_id: str) -> dict:
        cd = self._circuits.get(automation_id, _Circuit())
        return {
            "status": cd.status,
            "conflict_count": cd.conflict_count,
            "open_until": cd.open_until,
            "half_open_successes": cd.half_open_successes,
            "trial_inflight": cd.trial_inflight,
        }

    def sweep(self) -> None:
        """显式做一次惰性回收（TTL / 过期等待者 / 冷却）。"""
        self._expire(self._now())

    # ------------------------------------------------------------------ #
    # 仲裁主流程
    # ------------------------------------------------------------------ #
    def _arbitrate(self, ids: list[str], automation_id: str, instance_id: str, action: str, params: dict) -> RequestDecision:
        now = self._now()
        self._expire(now)
        if not ids:
            return RequestDecision.ALLOW
        base = self._base_priority(automation_id)

        # 1) 用户冷却优先于熔断（用户意图 > 系统自愈）
        for eid in ids:
            until = self._cooldown_until.get(eid, 0.0)
            if now < until:
                self._emit(
                    KIND_USER_COOLDOWN, eid, automation_id, self._holder_of(eid),
                    {"phase": "block", "remaining": round(until - now, 3)},
                )
                return RequestDecision.REJECT

        # 2) 抖动暂停：设备层面问题，该实体所有写一律拒绝
        for eid in ids:
            until = self._flicker_until.get(eid, 0.0)
            if now < until:
                self._emit(
                    KIND_THROTTLED, eid, automation_id, self._holder_of(eid),
                    {"reason": "flicker_pause", "remaining": round(until - now, 3)},
                )
                return RequestDecision.REJECT

        # 3) 熔断闸门
        blocked = self._circuit_gate(automation_id, ids[0], now)
        if blocked is not None:
            return blocked

        # 4) 计划：找出第一个拿不到的实体（无锁 / 自己续期 / 可抢占 都算可得）
        conflict: tuple[str, ResourceLock, float] | None = None
        for eid in ids:
            lock = self._locks.get(eid)
            if lock is None or lock.automation_id == automation_id:
                continue
            mine = self._effective_priority(automation_id, instance_id, eid, now)
            if not (self.preempt_enabled and mine > lock.priority):
                conflict = (eid, lock, mine)
                break

        if conflict is not None:
            eid, lock, mine = conflict
            decision = RequestDecision.WAIT if self.wait_enabled else RequestDecision.REJECT
            self._enqueue(ids, automation_id, instance_id, action, params, base, now)
            self._emit(
                KIND_REJECTED, eid, automation_id, lock.automation_id,
                {
                    "decision": decision.value,
                    "action": action,
                    "base_priority": base,
                    "effective_priority": mine,
                    "holder_priority": lock.priority,
                    "preempt_enabled": self.preempt_enabled,
                },
            )
            self._count_conflict(automation_id, now)
            self._record_negative(automation_id)
            return decision

        # 5) 获取（含续期 / 抢占）
        self._acquire(ids, automation_id, instance_id, action, now)
        return RequestDecision.ALLOW

    def _acquire(
        self,
        ids: list[str],
        automation_id: str,
        instance_id: str,
        action: str,
        now: float,
        *,
        forced: bool = False,
    ) -> None:
        created: list[str] = []
        try:
            for eid in ids:
                lock = self._locks.get(eid)
                if lock is not None and lock.automation_id == automation_id:
                    # 同一 automation 重复请求 → 续期
                    lock.instance_id = instance_id
                    lock.action = action
                    lock.acquired_at = now
                    lock.ttl = self.lock_ttl
                    lock.priority = self._base_priority(automation_id)
                    continue
                if lock is not None:
                    mine = self._effective_priority(automation_id, instance_id, eid, now)
                    if not forced:
                        self._emit(
                            KIND_PREEMPTED, eid, automation_id, lock.automation_id,
                            {
                                "evicted_instance_id": lock.instance_id,
                                "evicted_action": lock.action,
                                "action": action,
                                "holder_priority": lock.priority,
                                "effective_priority": mine,
                                "forced": forced,
                            },
                        )
                        self._count_conflict(lock.automation_id, now)   # 被抢占 +1
                        self._record_negative(lock.automation_id)
                        self._notify_preempted(lock)
                self._locks[eid] = ResourceLock(
                    entity_id=eid,
                    automation_id=automation_id,
                    instance_id=instance_id,
                    action=action,
                    acquired_at=now,
                    ttl=self.lock_ttl,
                    priority=self._base_priority(automation_id),
                )
                created.append(eid)
            for eid in ids:
                self._pending.pop((automation_id, instance_id, eid), None)
        except Exception:
            for eid in created:  # 保险回滚（正常路径走两阶段计划，不会触发）
                lock = self._locks.get(eid)
                if lock is not None and lock.automation_id == automation_id:
                    self._locks.pop(eid, None)
            raise

    # ------------------------------------------------------------------ #
    # 熔断
    # ------------------------------------------------------------------ #
    def _circuit_gate(self, automation_id: str, entity_id: str, now: float) -> RequestDecision | None:
        cd = self._circuits.setdefault(automation_id, _Circuit())
        if cd.status == "open":
            if now < cd.open_until:
                self._emit(
                    KIND_CIRCUIT_OPEN, entity_id, automation_id, None,
                    {"phase": "block", "remaining": round(cd.open_until - now, 3), "conflicts": cd.conflict_count},
                )
                return RequestDecision.CIRCUIT_OPEN
            # 到点 → 半开，放一次试探
            cd.status = "half_open"
            cd.half_open_successes = 0
            cd.trial_inflight = True
            self._emit(KIND_CIRCUIT_HALF_OPEN, entity_id, automation_id, None, {"trial": True})
            return None
        if cd.status == "half_open":
            if cd.trial_inflight:
                self._emit(KIND_CIRCUIT_OPEN, entity_id, automation_id, None, {"phase": "half_open_busy"})
                return RequestDecision.CIRCUIT_OPEN
            cd.trial_inflight = True
        return None

    def _count_conflict(self, automation_id: str, now: float) -> None:
        cd = self._circuits.setdefault(automation_id, _Circuit())
        cd.conflict_count += 1
        if cd.status == "half_open":
            self._reopen(automation_id, cd, now, "half_open_conflict")
        elif cd.status == "closed" and cd.conflict_count >= self.circuit_threshold:
            self._reopen(automation_id, cd, now, "threshold")

    def _reopen(self, automation_id: str, cd: _Circuit, now: float, reason: str) -> None:
        cd.status = "open"
        cd.open_until = now + self.circuit_cooldown
        cd.trial_inflight = False
        cd.half_open_successes = 0
        self._emit(
            KIND_CIRCUIT_OPEN, "", automation_id, None,
            {"phase": "open", "reason": reason, "conflicts": cd.conflict_count, "cooldown": self.circuit_cooldown},
        )

    def _note_result(self, automation_id: str, success: bool, now: float) -> None:
        if success:
            self._note_success(automation_id)
        else:
            self._note_trial_failure(automation_id, now)

    def _note_success(self, automation_id: str) -> None:
        cd = self._circuits.get(automation_id)
        if cd is None:
            return
        cd.trial_inflight = False
        cd.conflict_count = max(0, cd.conflict_count - 1)     # 成功衰减
        if cd.status == "half_open":
            cd.half_open_successes += 1
            if cd.half_open_successes >= self.circuit_half_open_success:
                cd.status = "closed"
                cd.conflict_count = 0
                cd.half_open_successes = 0
                self._emit(KIND_CIRCUIT_RECOVERED, "", automation_id, None, {"reason": "half_open_success"})

    def _note_trial_failure(self, automation_id: str, now: float) -> None:
        cd = self._circuits.get(automation_id)
        if cd is None:
            return
        cd.trial_inflight = False
        if cd.status == "half_open":      # 单次侥幸成功不算，失败立刻继续熔断
            self._reopen(automation_id, cd, now, "half_open_failure")

    def reset_conflicts(self, automation_id: str) -> None:
        """清零冲突计数但保留状态（WebUI 辅助）。"""
        cd = self._circuits.setdefault(automation_id, _Circuit())
        cd.conflict_count = 0

    # ------------------------------------------------------------------ #
    # 抖动 / 队列 / 优先级
    # ------------------------------------------------------------------ #
    def _note_release(self, entity_id: str, automation_id: str, now: float) -> None:
        log = self._release_log.setdefault(entity_id, [])
        log.append(now)
        cutoff = now - self.flicker_window
        log = [t for t in log if t >= cutoff]
        self._release_log[entity_id] = log
        if len(log) >= self.flicker_threshold:
            self._flicker_until[entity_id] = now + self.flicker_pause
            self._release_log[entity_id] = []
            self._emit(
                KIND_FLICKER, entity_id, automation_id, self._holder_of(entity_id),
                {
                    "count": len(log),
                    "window": self.flicker_window,
                    "pause": self.flicker_pause,
                    "threshold": self.flicker_threshold,
                },
            )

    def _enqueue(self, ids: Sequence[str], automation_id: str, instance_id: str, action: str, params: dict, base: float, now: float) -> None:
        for eid in ids:
            key = (automation_id, instance_id, eid)
            if key not in self._pending:
                self._pending[key] = PendingRequest(
                    entity_id=eid,
                    automation_id=automation_id,
                    instance_id=instance_id,
                    action=action,
                    params=dict(params),
                    requested_at=now,
                    base_priority=base,
                )

    def _effective_priority(self, automation_id: str, instance_id: str, entity_id: str, now: float) -> float:
        base = self._base_priority(automation_id)
        pending = self._pending.get((automation_id, instance_id, entity_id))
        if pending is None:
            return base
        waited = max(0.0, now - pending.requested_at)
        return base + min(self.aging_rate * waited, self.aging_cap)

    def _wake(self, entity_id: str, now: float) -> None:
        if self.on_pending_ready is None:
            return
        candidates = [p for p in self._pending.values() if p.entity_id == entity_id]
        if not candidates:
            return
        best = max(candidates, key=lambda p: self._effective_priority(p.automation_id, p.instance_id, p.entity_id, now))
        try:
            self.on_pending_ready(replace(best))
        except Exception as exc:
            self._emit(KIND_DEGRADED, entity_id, best.automation_id, None, {"phase": "wake", "error": repr(exc)})

    def _expire(self, now: float) -> None:
        for eid in [e for e, l in self._locks.items() if now - l.acquired_at >= l.ttl]:
            self._locks.pop(eid, None)          # TTL 兜底释放
            self._wake(eid, now)
        for key, pending in list(self._pending.items()):
            if now - pending.requested_at >= self.max_pending_wait:
                self._pending.pop(key, None)
                self._emit(
                    KIND_STARVED, pending.entity_id, pending.automation_id, self._holder_of(pending.entity_id),
                    {"waited": round(now - pending.requested_at, 3), "max_wait": self.max_pending_wait},
                )
        self._flicker_until = {e: t for e, t in self._flicker_until.items() if t > now}
        self._cooldown_until = {e: t for e, t in self._cooldown_until.items() if t > now}

    # ------------------------------------------------------------------ #
    # 小工具
    # ------------------------------------------------------------------ #
    def _now(self) -> float:
        return float(self.clock.monotonic())

    def _base_priority(self, automation_id: str) -> float:
        return float(self.conf.get(automation_id))

    def _holder_of(self, entity_id: str) -> str | None:
        lock = self._locks.get(entity_id)
        return lock.automation_id if lock else None

    def _record_negative(self, automation_id: str) -> None:
        try:
            self.conf.record_negative(automation_id)
        except Exception as exc:
            self._emit(KIND_DEGRADED, "", automation_id, None, {"phase": "record_negative", "error": repr(exc)})

    def _notify_preempted(self, lock: ResourceLock) -> None:
        if self.on_preempted is None:
            return
        try:
            self.on_preempted(replace(lock))
        except Exception as exc:
            self._emit(KIND_DEGRADED, lock.entity_id, lock.automation_id, None, {"phase": "preempt_notify", "error": repr(exc)})

    def _emit(self, kind: str, entity_id: str, requester_id: str, holder_id: str | None, details: dict | None) -> None:
        if self.on_event is None:
            return
        try:
            event = ConflictEvent(
                event_id=uuid.uuid4().hex[:12],
                entity_id=entity_id or "",
                kind=kind,
                requester_id=requester_id or "",
                holder_id=holder_id,
                timestamp=self._now(),
                details=dict(details or {}),
            )
            self.on_event(event)
        except Exception:
            pass    # 审计失败也不能影响主流程
