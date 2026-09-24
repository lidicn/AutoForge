"""ActionQueue —— 动作延迟队列（网络 + 设备响应延迟仿真）。

``call()`` 只入队绝不改状态；``flush()/tick()`` 是执行动作的唯一入口，执行经
``DeviceSM.apply()`` 生效（自动发布事件）。同一实体的新指令取消旧的未生效动作
（最新指令优先）；SM 申请的后续动作（``cover._arrive`` 等）同样参与抢占，而
``climate._settle`` 这类收敛动作由"每次指令执行时重新武装"保证不丢失。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from ..af_time import TimeSource
from .device_sm import DeviceSM, GenericSM, SM_REGISTRY
from .event_bus import FakeEventBus


@dataclass
class PendingAction:
    action: str                    # "light.turn_on" / 内部动作 "cover._arrive"
    params: dict[str, Any]
    entity_id: str                 # 目标实体（多目标拆成多条）
    scheduled_at: float            # 计划执行时间（monotonic）
    cancelled: bool = False


@dataclass
class ActionQueue:
    bus: FakeEventBus
    clock: TimeSource
    sm_registry: dict[str, type[DeviceSM]] = field(default_factory=lambda: dict(SM_REGISTRY))
    device_latency: float = 0.1
    _pending: list[PendingAction] = field(default_factory=list, init=False)
    _sms: dict[str, DeviceSM] = field(default_factory=dict, init=False)

    # ---- 入队 -----------------------------------------------------------------
    @staticmethod
    def _targets(params: Mapping[str, Any]) -> list[str]:
        raw = params.get("entity_id")
        if raw is None:
            return []
        if isinstance(raw, str):
            return [raw] if raw else []
        if isinstance(raw, (list, tuple, set, frozenset)):
            return [str(item) for item in raw if str(item)]
        return [str(raw)]

    def call(self, action: str, params: Mapping[str, Any]) -> list[PendingAction]:
        """入队一个动作（多 entity_id 拆成多条）。不执行，只排期。"""
        created: list[PendingAction] = []
        for entity_id in self._targets(params):
            self._cancel_entity(entity_id)          # 最新指令优先
            pending = PendingAction(
                action=action,
                params=dict(params),
                entity_id=entity_id,
                scheduled_at=self.clock.monotonic() + float(self.device_latency),
            )
            self._pending.append(pending)
            created.append(pending)
        return created

    def _enqueue_followup(self, entity_id: str, action: str, params: Mapping[str, Any], delay: float) -> PendingAction:
        pending = PendingAction(
            action=action,
            params=dict(params),
            entity_id=entity_id,
            scheduled_at=self.clock.monotonic() + max(0.0, float(delay)),
        )
        self._pending.append(pending)
        return pending

    def _cancel_entity(self, entity_id: str) -> int:
        count = 0
        for pending in self._pending:
            if pending.entity_id == entity_id and not pending.cancelled:
                pending.cancelled = True
                count += 1
        return count

    # ---- 执行 -----------------------------------------------------------------
    def flush(self) -> int:
        """执行所有**到期**的 pending（scheduled_at <= clock.monotonic()）。"""
        return self._run(due_only=True)

    def flush_all(self) -> int:
        """忽略排期，立即执行全部未取消的 pending。"""
        return self._run(due_only=False)

    def tick(self) -> int:
        """时间推进时调用，执行到期动作（等价 flush()）。"""
        return self.flush()

    def _run(self, due_only: bool) -> int:
        executed = 0
        for pending in list(self._pending):
            if pending.cancelled:
                continue
            if due_only and pending.scheduled_at > self.clock.monotonic():
                continue
            pending.cancelled = True                 # 标记已消费，统一清理
            self._execute(pending)
            executed += 1
        self._pending = [item for item in self._pending if not item.cancelled]
        return executed

    def _execute(self, pending: PendingAction) -> None:
        domain = pending.entity_id.split(".", 1)[0] if "." in pending.entity_id else ""
        service = pending.action.partition(".")[2] or pending.action
        sm = self.get_sm(pending.entity_id)
        sm.apply(service, pending.params)            # 状态机生效并发布事件
        for follow in sm.drain_followups():          # SM 申请的延时后续动作
            self._enqueue_followup(pending.entity_id, f"{domain}.{follow.service}", follow.params, follow.delay)

    # ---- 查询 / 维护 -----------------------------------------------------------
    def pending(self, entity_id: str | None = None) -> list[PendingAction]:
        """查看待执行队列（含已 cancelled 的条目，便于断言抢占）。"""
        if entity_id is None:
            return list(self._pending)
        return [item for item in self._pending if item.entity_id == entity_id]

    def cancel_all(self, entity_id: str | None = None) -> int:
        count = 0
        for pending in self._pending:
            if pending.cancelled:
                continue
            if entity_id is not None and pending.entity_id != entity_id:
                continue
            pending.cancelled = True
            count += 1
        return count

    def get_sm(self, entity_id: str) -> DeviceSM:
        """获取或创建实体的状态机（未知 domain 回退 GenericSM）。"""
        sm = self._sms.get(entity_id)
        if sm is None:
            domain = entity_id.split(".", 1)[0] if "." in entity_id else ""
            cls = self.sm_registry.get(domain) or GenericSM
            sm = cls(entity_id, self.bus, self.clock)
            self._sms[entity_id] = sm
        return sm
