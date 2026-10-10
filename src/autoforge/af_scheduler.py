"""调度器 —— 4 种 mode + 配额 + 触发匹配（KICKOFF §4.3）。

顺序铁律：**事件到达 → 调度器先做 mode 判断与配额决策 → 实例管理器再执行状态迁移**。

两种 Timer 严格隔离（IR §11）：
- `for=10m`：静态图的**持续条件**，边沿触发 + 持续时长，**条件破坏即取消**（对齐 HA `for:`）
- `wait`：实例级**延时**定时器，到点走 `then` 正常继续（语义拍板 A）
- `ask`：实例级询问定时器，无人应答到点走 `on_timeout` 兜底
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterable

logger = logging.getLogger(__name__)

from .af_audit import INSTANCE_DEBOUNCED, INSTANCE_REJECTED, QUOTA_EXCEEDED, AuditEvent, AuditLog
from .af_bus import EVENT_ENTITY_PREFIX, BusEvent
from .af_executor import EMIT_TIMER_KIND, NodeExecutor
from .af_instance import Instance, InstanceManager
from .af_ir import Automation, Graph, Node, Trigger, check_trigger_depth
from .af_state import StateProvider
from .af_time import TimeSource, SystemTimeSource, parse_duration
from .af_fire_recorder import FireRecorder

__all__ = ["Quota", "Scheduler", "SINGLE", "RESTART", "QUEUED", "PARALLEL", "normalize_trigger"]

SINGLE = "single"
RESTART = "restart"
QUEUED = "queued"
PARALLEL = "parallel"

#: `Scheduler.rejections` 的条数上限。这份是拒绝清单的内存镜像（每次拒绝都同时落审计），
#: 常驻服务里若只增不减就是稳定性审计 BUG-01 那一族的无界增长。
REJECTIONS_MAX = 200


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
    fire_recorder: FireRecorder | None = None  # mimo: 当日一次记账落盘，防重启重放

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
        due = self.instances.due_timers()
        logger.debug("[TICK] due_timers=%d instances", len(due))
        for instance in due:
            kind = instance.timer.kind if instance.timer is not None else "timeout"
            logger.debug("[TICK] inst=%s kind=%s node=%s", instance.instance_id, kind, instance.ctx.current_node)
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
            auto = self._find(pending.automation_id)
            if auto is None:
                # 挂起期间该自动化被从 graph 删掉：`graph.get` 是 `self._by_id[id]`，直接
                # KeyError 会冒泡到 tick 的调用方（af_cli 五处／af_live 一处），整个 tick 循环停摆。
                logger.warning(
                    "SCHED_PENDING_AUTO_GONE [TICK] 持续条件到期，但自动化 %s 已不在图里，丢弃这条挂起",
                    pending.automation_id,
                )
                continue
            if not getattr(auto, "enabled", True):
                continue  # 三条触发路径都守 enabled：handle_event／_fire_time_triggers／这里
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
    def _find(self, automation_id: str) -> Automation | None:
        """按 id 找自动化，找不到给 None 而不是 KeyError。

        `Graph.get` 走的是 `self._by_id[id]`，而调度器里两处（tick 到期／排空队列）拿着一个
        "注册时还在、现在未必在"的 id——`_drain_queues` 原来写 `graph.get(...)` 之后
        还判 `auto is None`，那道守卫永远不可能为真，是假绿的一种。
        手动触发 `trigger()` 保持抛 KeyError：调用方给错 id 是调用方的错，不该静默。
        """
        try:
            return self.graph.get(automation_id)
        except KeyError:
            return None

    def _try_fire(self, auto: Automation, node: Node, event: BusEvent | None) -> Instance | None:
        key = (auto.id, node.id)
        if node.debounce:
            now = self.clock.monotonic()
            last = self._debounce.get(key)
            if last is not None and (now - last) < parse_duration(node.debounce):
                # P2-4 修复：去抖抑制此前静默 return None，无任何观测记录。
                self.audit.add(
                    AuditEvent(
                        type=INSTANCE_DEBOUNCED,
                        at=self.clock.now(),
                        message=f"自动化 {auto.id} 节点 {node.id} 去抖抑制（{parse_duration(node.debounce)}s 内重复触发）",
                        automation_id=auto.id,
                        node_id=node.id,
                    )
                )
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
            self._record_rejection(f"global_quota:{auto.id}")
            return False
        return True

    def _record_rejection(self, text: str) -> None:
        """把拒绝原因记进环形清单：超过 `REJECTIONS_MAX` 时从头部丢最旧的。"""
        if len(self.rejections) >= REJECTIONS_MAX:
            del self.rejections[0]
        self.rejections.append(text)

    def _reject(self, auto: Automation, reason: str) -> None:
        self._record_rejection(f"{reason}:{auto.id}")
        logger.info("[SCHED-REJECT] %s: %s", auto.id, reason)
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
            auto = self._find(auto_id)
            if auto is None or not auto.enabled:
                continue  # v0.6.0 启停：禁用项队列不排空；已删除的同样不排空
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
    def _satisfied(self, trig: Trigger, event: BusEvent, _depth: int = 0) -> bool:
        # F6：这是**每个事件都走一次**的运行期路径，暴露面比保存时校验更宽（第十七轮实测）。
        check_trigger_depth(_depth, "scheduler._satisfied")
        trig = _normalize(trig)
        if trig.type == "group":
            # P1-6：group and 基于状态快照判定（窗口默认 0，严格同时）。
            # 旧实现用同一个 event 匹配所有子 trigger，一个事件只能匹配一个
            # entity_id，导致 and 永远不成立。修复后：对 state 类型子 trigger
            # 查当前状态快照；event 类型仍按事件匹配。
            if trig.op == "and":
                return all(
                    self._group_sub_satisfied(sub, event, _depth + 1) for sub in trig.sources
                )
            # or 保持原逻辑：任一子 trigger 匹配当前事件即成立
            results = [self._satisfied(sub, event, _depth + 1) for sub in trig.sources]
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

    def _group_sub_satisfied(self, sub: Trigger, event: BusEvent, _depth: int = 0) -> bool:
        """P1-6：group and 的子 trigger 判定——state 类型查状态快照，event 类型按事件匹配。"""
        sub = _normalize(sub)
        if sub.type == "group":
            # 嵌套 group 交回 `_satisfied`：那里同时装着深度预算与 and/or 两种复合口径。
            # 不认这一档就会落到本函数末尾那句"非事件驱动类型 ⇒ False"，于是 `and` 里套
            # `or`（合法 IR，扫描器只查预算不查形状）**条件永远不成立**——静默 False 比报错难查得多。
            return self._satisfied(sub, event, _depth + 1)
        if sub.type == "state":
            if not sub.entity_id:
                return False
            try:
                snap = self.states.snapshot([sub.entity_id])
                current = snap.get(sub.entity_id)
            except Exception:
                return False
            if sub.to is not None and current != sub.to:
                return False
            if sub.from_ is not None:
                old_state = event.payload.get("old_state") if event.entity_id == sub.entity_id else None
                if old_state is not None and old_state != sub.from_:
                    return False
            return True
        if sub.type == "event":
            if not sub.event:
                return False
            return event.entity_id == f"{EVENT_ENTITY_PREFIX}{sub.event}"
        # time/sun 等非事件驱动类型：在 group and 里无法由事件触发，返回 False
        return False

    def _still_holds(self, pending: _PendingFor) -> bool:
        """到期时复查持续条件是否仍成立（条件破坏即取消）。

        P1-7 修复：`to is None` 时保守返回 False（fail-closed），
        不再 fail-open 当作「仍然成立」。无明确目标态的 for 条件不可靠，
        用户应指定 `to` 才能用持续条件。

        P1-2 修复：`snapshot()` 本身在 `try` 之外会抛 `UnknownEntity`
        （实体被删除/重命名时），冒泡会拖垮整次 tick。此处把快照获取
        一并纳入异常捕获，实体不可见时保守判为「不成立」。
        """
        if pending.to is None:
            return False
        try:
            snapshot = self.states.snapshot([pending.entity_id])
            return snapshot.get(pending.entity_id) == pending.to
        except Exception:
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
        # B3-AF-04: local time for hhmm (user writes at:07:00 meaning local)
        now = getattr(self.clock, 'local_now', self.clock.now)()
        today = now.date().isoformat()
        hhmm = f"{now.hour:02d}:{now.minute:02d}"
        # P1-1 修复：_time_fired 按 (auto,node,date) 累加，跨天不清理会无限增长。
        # 每次进入时剔除非今日的桶（集合很小，开销可忽略）。
        if self._time_fired:
            self._time_fired = {m for m in self._time_fired if m[2] == today}
        for auto in self.graph:
            # P1-5 修复：停用的自动化不触发时间事件
            if not getattr(auto, "enabled", True):
                continue
            for node in auto.entry_nodes():
                trig = node.trigger
                if trig is None or trig.type != "time":
                    continue
                if trig.at != hhmm:
                    continue
                rule_key = f"{auto.id}:{node.id}"
                # mimo FireRecorder: 落盘记账防重启重放；无 recorder 时回退内存态
                if self.fire_recorder is not None:
                    lease = self.fire_recorder.try_begin(rule_key)
                    if lease is None:
                        continue
                    instance = self._try_fire(auto, node, None)
                    if instance is not None:
                        lease.confirm(instance.instance_id)
                        fired.append(instance)
                    else:
                        lease.release()  # B3-AF-03: 失败释放，当天可重试
                else:
                    mark = (auto.id, node.id, today)
                    if mark in self._time_fired:
                        continue
                    instance = self._try_fire(auto, node, None)
                    if instance is not None:
                        # B3-AF-03: mark after successful fire (was mark-then-fire)
                        self._time_fired.add(mark)
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
    # P1-8 修复：offset 字段透传，不丢弃（state 类型暂不消费，但信息保留）
    return Trigger(
        type="state",
        entity_id="sun.sun",
        to="below_horizon" if trig.event == "sunset" else "above_horizon",
        offset=trig.offset,
    )


# 内部别名（保持调用点简短）
_normalize = normalize_trigger
