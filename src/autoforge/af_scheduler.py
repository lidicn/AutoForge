"""调度器 —— 4 种 mode + 配额 + 触发匹配（KICKOFF §4.3）。

顺序铁律：**事件到达 → 调度器先做 mode 判断与配额决策 → 实例管理器再执行状态迁移**。

两种 Timer 严格隔离（IR §11）：
- `for=10m`：静态图的**持续条件**，边沿触发 + 持续时长，**条件破坏即取消**（对齐 HA `for:`）
- `wait`：实例级**延时**定时器，到点走 `then` 正常继续（语义拍板 A）
- `ask`：实例级询问定时器，无人应答到点走 `on_timeout` 兜底
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterable

from .af_audit import INSTANCE_REJECTED, QUOTA_EXCEEDED, AuditEvent, AuditLog
from .af_bus import EVENT_ENTITY_PREFIX, BusEvent
from .af_executor import EMIT_TIMER_KIND, NodeExecutor
from .af_instance import Instance, InstanceManager
from .af_ir import Automation, Graph, Node, Trigger
from .af_state import StateProvider
from .af_time import TimeSource, SystemTimeSource, parse_duration

__all__ = ["Quota", "Scheduler", "SINGLE", "RESTART", "QUEUED", "PARALLEL", "normalize_trigger"]

SINGLE = "single"
RESTART = "restart"
QUEUED = "queued"
PARALLEL = "parallel"


@dataclass(frozen=True)
class Quota:
    """并发配额（IR §12-6）。超限拒绝 + 审计，不静默丢弃。"""

    global_limit: int = 100
    per_automation: int = 10
    queue_limit: int = 10


@dataclass
class _PendingFor:
    """一个尚未满足时长要求的持续条件（`for`）。"""

    automation_id: str
    node_id: str
    entity_id: str
    to: str | None
    started_at: float
    duration: float
    event: BusEvent | None = None


@dataclass
class Scheduler:
    graph: Graph
    instances: InstanceManager
    executor: NodeExecutor
    states: StateProvider
    clock: TimeSource = field(default_factory=SystemTimeSource)
    audit: AuditLog = field(default_factory=AuditLog)
    quota: Quota = field(default_factory=Quota)

    def __post_init__(self) -> None:
        self._pending: dict[tuple[str, str, str], _PendingFor] = {}
        self._queues: dict[str, deque[BusEvent]] = {}
        self._debounce: dict[tuple[str, str], float] = {}
        self._time_fired: set[tuple[str, str, str]] = set()
        self.rejections: list[str] = []

    # ─────────────────────────────────────────────────────────────────
    # 事件入口
    # ─────────────────────────────────────────────────────────────────
    def handle_event(self, event: BusEvent) -> list[Instance]:
        """分发一个状态变更事件，返回本次触发的实例。"""
        fired: list[Instance] = []
        for auto in self.graph:
            if not auto.enabled:
                continue  # v0.6.0 启停：禁用的自动化不注册触发、不派发实例
            for node in auto.entry_nodes():
                trig = node.trigger
                if trig is None:
                    continue
                satisfied = self._satisfied(trig, event)
                if node.for_:
                    self._update_pending(auto, node, event, satisfied)
                elif satisfied:
                    instance = self._try_fire(auto, node, event)
                    if instance is not None:
                        fired.append(instance)
        return fired

    def tick(self) -> list[Instance]:
        """时间推进后调用：处理到点的 `for`、定时触发、实例超时与排队。"""
        fired: list[Instance] = []

        # 1) 到点的实例级定时器，按 kind 分派：
        #    - `emit`：延迟发布，到点按 then 继续（不是超时）
        #    - `wait`：语义拍板 A——时间到了走 `then` 正常继续（wait 是"延时"，不是超时）
        #    - `ask`（或未知 kind）：无人应答到点 → `on_timeout` 兜底
        for instance in self.instances.due_timers():
            kind = instance.timer.kind if instance.timer is not None else "timeout"
            if kind == EMIT_TIMER_KIND:
                fired.append(self.executor.emit_due(instance))
            elif kind == "wait":
                fired.append(self.executor.resume_then(instance))
            else:
                fired.append(self.executor.timeout(instance))

        # 2) 到点的持续条件 for
        now = self.clock.monotonic()
        for key in list(self._pending):
            pending = self._pending[key]
            if now < pending.started_at + pending.duration:
                continue
            del self._pending[key]
            if not self._still_holds(pending):
                continue  # 条件在到期前已被破坏
            auto = self.graph.get(pending.automation_id)
            instance = self._try_fire(auto, auto.node(pending.node_id), pending.event)
            if instance is not None:
                fired.append(instance)

        # 3) 定时触发（time）
        fired += self._fire_time_triggers()

        # 4) 排队队列出队
        fired += self._drain_queues()
        return fired

    def trigger(self, automation_id: str, event: BusEvent | None = None) -> Instance | None:
        """手动触发（测试 / 外部调用）：跳过触发匹配，直接走 mode 与配额。"""
        auto = self.graph.get(automation_id)
        entry = auto.entry_nodes()[0]
        return self._try_fire(auto, entry, event)

    # ─────────────────────────────────────────────────────────────────
    # mode 与配额（先决策，再动实例）
    # ─────────────────────────────────────────────────────────────────
    def _try_fire(self, auto: Automation, node: Node, event: BusEvent | None) -> Instance | None:
        key = (auto.id, node.id)
        if node.debounce:
            now = self.clock.monotonic()
            last = self._debounce.get(key)
            if last is not None and (now - last) < parse_duration(node.debounce):
                return None
            self._debounce[key] = now

        active = self.instances.active_of(auto.id)

        if not self._within_quota(auto):
            return None

        mode = auto.mode
        if mode == SINGLE:
            if active:
                self._reject(auto, "single 模式已有实例在跑")
                return None
        elif mode == RESTART:
            # ⚠️ 与 HA 的有意偏离：restart 会触发旧实例 on_cancel（见 docs/HA_SEMANTIC_DIFF.md）
            for old in active:
                self.executor.cancel(old, reason=f"{auto.id} restart")
        elif mode == QUEUED:
            if len(active) >= self.quota.per_automation:
                queue = self._queues.setdefault(auto.id, deque())
                if len(queue) >= self.quota.queue_limit:
                    self._reject(auto, "queued 队列已满")
                    return None
                queue.append(event or BusEvent.of(node.id, "manual", source="manual"))
                return None
        elif mode == PARALLEL:
            if len(active) >= self.quota.per_automation:
                self._reject(auto, "parallel 达到单条并发上限")
                return None

        instance = self.instances.spawn(auto, trigger_event=event)
        instance.ctx.current_node = node.id
        self.executor.run(instance)
        return instance

    def _within_quota(self, auto: Automation) -> bool:
        if self.instances.count_active() >= self.quota.global_limit:
            self.audit.add(
                AuditEvent(
                    type=QUOTA_EXCEEDED,
                    at=self.clock.now(),
                    message=f"全局并发达到上限 {self.quota.global_limit}，拒绝触发 {auto.id}",
                    automation_id=auto.id,
                )
            )
            self.rejections.append(f"global_quota:{auto.id}")
            return False
        return True

    def _reject(self, auto: Automation, reason: str) -> None:
        self.rejections.append(f"{reason}:{auto.id}")
        import sys

        print(f"[SCHED-REJECT] {auto.id}: {reason}", file=sys.stderr, flush=True)
        self.audit.add(
            AuditEvent(
                type=INSTANCE_REJECTED,
                at=self.clock.now(),
                message=f"拒绝触发 {auto.id}：{reason}",
                automation_id=auto.id,
            )
        )

    def _drain_queues(self) -> list[Instance]:
        fired: list[Instance] = []
        for auto_id, queue in list(self._queues.items()):
            if not queue:
                continue
            auto = self.graph.get(auto_id)
            if auto is None or not auto.enabled:
                continue  # v0.6.0 启停：禁用项队列不排空
            if len(self.instances.active_of(auto_id)) >= self.quota.per_automation:
                continue
            event = queue.popleft()
            instance = self._try_fire(auto, auto.entry_nodes()[0], event)
            if instance is not None:
                fired.append(instance)
        return fired

    # ─────────────────────────────────────────────────────────────────
    # 触发匹配
    # ─────────────────────────────────────────────────────────────────
    def _satisfied(self, trig: Trigger, event: BusEvent) -> bool:
        trig = _normalize(trig)
        if trig.type == "group":
            results = [self._satisfied(sub, event) for sub in trig.sources]
            # group 的 and/or 按**当前状态**判定（不只依赖触发事件），避免多源组合永远不成立
            if trig.op == "and":
                return all(results)
            return any(results)
        if trig.type == "event":
            # v0.4.0 订阅侧：只按事件名**精确**匹配（不支持通配符，KICKOFF §4.1）
            if not trig.event:
                return False
            return event.entity_id == f"{EVENT_ENTITY_PREFIX}{trig.event}"
        if trig.type != "state":
            return False
        if trig.entity_id and trig.entity_id != event.entity_id:
            return False
        if trig.to is not None and event.state != trig.to:
            return False
        if trig.from_ is not None and event.payload.get("old_state") != trig.from_:
            return False
        return True

    def _still_holds(self, pending: _PendingFor) -> bool:
        """到期时复查持续条件是否仍成立（条件破坏即取消）。"""
        if pending.to is None:
            return True
        snapshot = self.states.snapshot([pending.entity_id])
        try:
            return snapshot.get(pending.entity_id) == pending.to
        except KeyError:
            return False

    def _update_pending(self, auto: Automation, node: Node, event: BusEvent, satisfied: bool) -> None:
        key = (auto.id, node.id, event.entity_id)
        if not satisfied:
            self._pending.pop(key, None)  # 条件破坏 → 取消
            return
        if key in self._pending:
            return
        self._pending[key] = _PendingFor(
            automation_id=auto.id,
            node_id=node.id,
            entity_id=event.entity_id,
            to=node.trigger.to if node.trigger else None,  # type: ignore[union-attr]
            started_at=self.clock.monotonic(),
            duration=parse_duration(node.for_ or "0s"),
            event=event,
        )

    def _fire_time_triggers(self) -> list[Instance]:
        fired: list[Instance] = []
        now = self.clock.now()
        today = now.date().isoformat()
        hhmm = f"{now.hour:02d}:{now.minute:02d}"
        for auto in self.graph:
            for node in auto.entry_nodes():
                trig = node.trigger
                if trig is None or trig.type != "time":
                    continue
                if trig.at != hhmm:
                    continue
                mark = (auto.id, node.id, today)
                if mark in self._time_fired:
                    continue
                self._time_fired.add(mark)
                instance = self._try_fire(auto, node, None)
                if instance is not None:
                    fired.append(instance)
        return fired

    # ─────────────────────────────────────────────────────────────────
    def pending_for(self) -> list[dict[str, Any]]:
        """当前挂起的持续条件（便于测试与观测）。"""
        now = self.clock.monotonic()
        return [
            {
                "automation_id": p.automation_id,
                "node_id": p.node_id,
                "entity_id": p.entity_id,
                "elapsed_s": round(now - p.started_at, 3),
                "required_s": p.duration,
            }
            for p in self._pending.values()
        ]


def normalize_trigger(trig: Trigger) -> Trigger:
    """把 `sun` 触发归一化为 `sun.sun` 的状态触发。

    语义等价（日落 = sun.sun 变为 below_horizon），好处是不用为太阳历单独写一套分支，
    真 vhass 下 `sun.sun` 由 HA 原生太阳历组件驱动。
    """
    if trig.type != "sun":
        return trig
    return Trigger(
        type="state",
        entity_id="sun.sun",
        to="below_horizon" if trig.event == "sunset" else "above_horizon",
    )


# 内部别名（保持调用点简短）
_normalize = normalize_trigger
