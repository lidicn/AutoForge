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
from typing import Any, Callable, Iterable, Mapping, NamedTuple

logger = logging.getLogger(__name__)

from .af_audit import INSTANCE_DEBOUNCED, INSTANCE_EDGE_HELD, INSTANCE_COOLDOWNED, INSTANCE_REJECTED, QUOTA_EXCEEDED, AuditEvent, AuditLog
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

#: `Scheduler._delayed` 的长度上限（裁定 20261011 §3 Q2 乙的延迟回调队列）。
#: 满了不静默淘汰：排队满 ⇒ 抛，调用方（`af_conflict_runtime._on_pending_ready`）本来就带
#: `except` 兜底——它会留 `schedule` 降级痕并退回同步重试，比"重试悄悄消失"诚实。
DELAYED_MAX = 256

#: D10（2026-11-06）：派发冷却期默认 10 分钟。IR 元数据可配（`cooldown`，auto 级覆盖），
#: 配置缺失取此默认。冷却只拦"派发"（spawn），⛔ 不拦触发计数（卡名 AF卡_dispatch_cooldown）。
DEFAULT_COOLDOWN_S = 600.0


@dataclass(frozen=True)
class Quota:
    """并发配额（IR §12-6）。超限拒绝 + 审计，不静默丢弃。"""

    global_limit: int = 100
    per_automation: int = 10
    queue_limit: int = 10


#: 排队事件的归属：`node_id` 是**入队那一刻匹配上的入口节点**。
#: 用 NamedTuple 而非裸元组：本模块刚修掉的缺陷就是"位置索引代替归属"，
#: 元组 `(a, b)` 读起来谁都不知道谁是谁，具名字段才拦得住下一次重犯。
class QueuedTrigger(NamedTuple):
    node_id: str
    event: BusEvent


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
    #: 运行期事实落盘（PM 卡 2026-11-04）：触发计数单一出口记账。可调用对象
    #: `(automation_id, node_id, entity_id, via) -> None`；未注入时零副作用（旧行为）。
    #: 这里用可调用对象而非 RuntimeFacts 实例，避免 scheduler 依赖 store 具体类型、也便于单测。
    runtime_facts: Any | None = None
    #: D10（2026-11-06）：边沿触发的**事实 IO**——`read_last_met(auto_id)->bool|None`（None=无历史）＋
    #: `record_met(auto_id, met: bool)`。由 `af_runtime` 注成 RuntimeFacts 的闭包（落盘持久化）；
    #: 未注入 ⇒ 调度器退回内存 `_last_met`（可单测、重启即空——持久化只在 serve 装配时给）。
    met_io: Any | None = None

    def __post_init__(self) -> None:
        self._pending: dict[tuple[str, str, str], _PendingFor] = {}
        self._queues: dict[str, deque[QueuedTrigger]] = {}
        self._debounce: dict[tuple[str, str], float] = {}
        self._time_fired: set[tuple[str, str, str]] = set()
        self.rejections: list[str] = []
        self._delayed: list[tuple[float, Callable[[], Any]]] = []  # bounded-cache: exempt(调度器的延迟回调队列，不是缓存：每次 tick 由 `_run_delayed()` 按 deadline 排空，读点就在同方法；上限 `DELAYED_MAX` 走"满了即抛"、由调用方退回同步重试，不静默淘汰待重试项。没有 TTL 腿——到点前把它抹掉等于让一次重试悄悄消失，所以不进 BOUNDED_CACHES 给一条不存在的腿盖章)
        #: D10（2026-11-06）：派发冷却——每个自动化上次成功派发的单调钟时刻。冷却期内再命中
        #: ⇒ 计数照记＋`INSTANCE_COOLDOWNED` 审计、不 spawn（卡名 AF卡_dispatch_cooldown）。
        #: ⛔ 这是内存态（重启即空）：`last_met` 落盘由 `runtime_facts.record_met` 管，
        #: 冷却本身不落盘——重启后首轮无冷却（可接受：冷却只是高频念叨的兜底，边沿触发是主闸）。
        # bounded-cache: exempt(冷却起点：键＝自动化 id，成员数上界＝图里自动化总数（有界），每次派发覆盖写、同 id 不新增键；无 TTL 腿——到点前抹掉会让自动化立刻重派，等于把冷却关了，所以不进 BOUNDED_CACHES 给一条不存在的腿盖章)
        self._cooldown: dict[str, float] = {}
        #: D10：内存版 `last_met`（上轮全条件布尔）。`met_io` 未注入（单测/无 store）时用这个；
        #: 注入了 `met_io` ⇒ 读写都走它（`af_runtime` 落盘），本 dict 不参与。
        # bounded-cache: exempt(边沿触发内存 last_met：键＝自动化 id，成员数上界＝图里自动化总数（有界），覆盖写不增长；无 TTL 腿——它的落盘持久化由 `met_io` 管，内存版只是 serve 未接线时的单测退回，不配 TTL 语义，所以不进 BOUNDED_CACHES 给一条不存在的腿盖章)
        self._last_met: dict[str, bool] = {}

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
                else:
                    # D10（2026-11-06）：条件**不满**也要推进 `last_met=False`——否则
                    # "条件打破"没被记下，下一次满足就不算跳变沿（先断后立失效）。
                    # 只对 group-and 自动化生效（无该条件层 ⇒ 无"条件维持"语义，不记）。
                    self._record_met_if_applicable(auto, met=False)
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

        # 5) 到点的延迟回调（裁定 20261011 §3 Q2 乙：`call_later` 的落地腿）
        self._run_delayed()
        return fired

    def call_later(self, seconds: float, callback: Callable[[], Any]) -> None:
        """把 `callback` 排到 `now + seconds` 之后的那一次 `tick()` 上执行。

        这枚接口早就有人调：`af_conflict_runtime.py:421` 的 `scheduler.call_later(0.0, _retry)`，
        而 `af_runtime_ext.make_later` 按 `("call_later","schedule","add_timer","later")` 四个名字探测
        ——过去四个全不落，于是 `make_later` 恒返 `None`、冲突等待的 `_retry` 在事件栈里同步重入
        （稳定性第三轮 F-07／F-08）。这里刻意**不**用 `asyncio` 的 `loop.call_later`：本仓的 tick 由
        守护线程驱动（`af_live.py`），线程里没有 running loop，用真环就是把"量不出"写成契约。
        """
        if not callable(callback):
            raise TypeError("call_later 的 callback 必须可调用")
        if len(self._delayed) >= DELAYED_MAX:
            raise RuntimeError(
                f"SCHED_DELAYED_QUEUE_FULL 延迟回调队列已达上限 {DELAYED_MAX}，拒绝再排（不静默淘汰已排项）"
            )
        self._delayed.append((self.clock.monotonic() + max(0.0, float(seconds)), callback))

    def _run_delayed(self) -> None:
        """执行到点的延迟回调；单个回调抛穿只留痕，不拖垮本次 tick 与其余回调。"""
        if not self._delayed:
            return
        now = self.clock.monotonic()
        due = [item for item in self._delayed if item[0] <= now]
        if not due:
            return
        # 先摘再跑：回调里重新 `call_later` 不会在同一轮被再次执行（那正是 F-07 的重入形状）。
        self._delayed = [item for item in self._delayed if item[0] > now]
        for _, callback in due:
            try:
                callback()
            except Exception as exc:  # noqa: BLE001 —— 一条回调的失败不该停掉整次 tick
                logger.error(
                    "SCHED_DELAYED_CALLBACK_FAILED 延迟回调抛穿（本次 tick 其余任务继续）：%s", exc
                )

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
                # 归属节点必须随事件一起排队：queued 模式的语义是"并发满了先排队、
                # 稍后**照原样**执行"，而"原样"包含"是哪个入口点亮的它"。旧实现只存
                # 事件、出队时 `entry_nodes()[0]` 硬取第一个 ⇒ 双入口以上自动化排在
                # 第二个入口的事件会被派到第一个分支（审计 2026-10-28 实测复现：
                # binary_sensor.n 触发的实例起点成了 q1）。
                queue.append(QueuedTrigger(node.id, event or BusEvent.of(node.id, "manual", source="manual")))
                return None
        elif mode == PARALLEL:
            if len(active) >= self.quota.per_automation:
                self._reject(auto, "parallel 达到单条并发上限")
                return None

        # ── D10 第四层闸：边沿触发（主）＋冷却兜底 ──
        # 命中抑制 ⇒ **计数照记**（调 runtime_facts 记账，与 spawn 出口同单源）、不 spawn、审计在案。
        # 分层铁律：计数是事实、派发是决策——抑制只拦派发，⛔ 不拦触发计数（卡名 AF卡_dispatch_cooldown）。
        # ⛔ 只在这里加"派发"闸，不碰装载(archived)/触发(enabled)三层语义（台账 §1.173）。
        _allowed, _why = self._dispatch_allowed(auto)
        if not _allowed:
            # 计数照记（即使不 spawn）：抑制也是"条件又命中了"的事实，别让它从计数里消失。
            self._record_trigger_facts(auto, node, event)
            _ev = INSTANCE_COOLDOWNED if _why == "cooldown" else INSTANCE_EDGE_HELD
            self.audit.add(
                AuditEvent(
                    type=_ev,
                    at=self.clock.now(),
                    message=(f"自动化 {auto.id} 冷却期内命中，不派发（计数照记）" if _why == "cooldown"
                             else f"自动化 {auto.id} 条件持续满足（电平维持），不重复派发（计数照记）"),
                    automation_id=auto.id,
                    node_id=node.id,
                )
            )
            return None

        instance = self.instances.spawn(auto, trigger_event=event)
        instance.ctx.current_node = node.id
        # 运行期事实：真触发成功 spawn 后记账（单一出口，group/state/time/队列排空都汇此处）。
        # 记账失败⛔ 不影响执行：可调用对象内部自吞异常，这里再兜一层防御。
        self._record_trigger_facts(auto, node, event)
        self.executor.run(instance)
        # D10：成功派发 ⇒ 记冷却起点（下一次冷却期的计时基准）。
        self._cooldown[auto.id] = self.clock.monotonic()
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
            node_id, event = queue.popleft()
            # ⛔ 不许退回 `entry_nodes()[0]`：那正是本缺陷的原始形状。节点查不到
            # （图被换过/手工构造的坏 id）时**可见地丢弃**，而不是静默走错分支。
            node = auto.nodes.get(node_id)
            if node is None or node.kind != "on":
                self._reject(auto, f"排队事件的入口节点 {node_id!r} 已不存在或非 on 节点，丢弃该事件")
                continue
            instance = self._try_fire(auto, node, event)
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

    def _auto_currently_met(self, auto: Automation) -> bool | None:
        """D10（2026-11-06）：求自动化**入口触发**的"全条件布尔"——不依赖单个触发事件。

        边沿触发的承重前提：判断"条件从不满→满足的跳变沿"必须用**当前状态**求整组条件，
        而不能只看"这次来的那个事件匹配了没有"。对 AC 这类「六实体任一变化→重查五条件」的
        形状，入口 `on` 节点的 trigger 是 `group and` 五条状态条件——这里对每条子条件
        取当前状态快照判定，返回全布尔。

        返回 `None` = 该自动化**没有 group-and 条件层**（入口是单条状态/event/time/group-or/
        无触发）⇒ 无"条件维持"语义，边沿触发与冷却都不适用，**每次事件命中即派发**（旧行为）。
        返回 `bool` = 有 group-and 条件层，`True`=当前全条件成立。

        ⛔ 这是**决策**（是否在跳变沿派发），与触发计数（`runtime_facts.record_trigger`）分层——
        计数是事实、派发是决策，两边独立（D10 卡 §一）。
        """
        has_group_and = False
        for node in auto.entry_nodes():
            trig = node.trigger
            if trig is None:
                continue
            trig = _normalize(trig)
            if trig.type != "group" or trig.op != "and":
                continue
            has_group_and = True
            try:
                # 每条子条件按当前状态快照判定（state 型查快照；event 型无法由状态求值 ⇒ 保守 False）
                for sub in trig.sources:
                    sub = _normalize(sub)
                    if sub.type == "state":
                        if not sub.entity_id:
                            return False
                        snap = self.states.snapshot([sub.entity_id])
                        current = snap.get(sub.entity_id)
                        if sub.to is not None and current != sub.to:
                            return False
                        if sub.from_ is not None:
                            # from_ 的"前一态"只对事件求值有意义，状态快照没有旧态 ⇒ 保守 False
                            return False
                    elif sub.type == "group":
                        # 嵌套 group：递归求布尔（`_satisfied` 对 group 走快照，这里同口径）
                        if not self._group_currently_met(sub):
                            return False
                    else:
                        # event/time/sun：非状态驱动，无法由当前状态求"持续满足" ⇒ 保守 False
                        return False
                return True
            except Exception:  # noqa: BLE001 —— 求值失败保守判"不满足"（fail-closed，⛔ 不猜）
                return False
        # 没有任何 group-and 条件层 ⇒ 无"条件维持"语义，返回 None（不适用边沿/冷却）
        return None if not has_group_and else True

    def _group_currently_met(self, trig: Trigger, _depth: int = 0) -> bool:
        """D10：嵌套 `group` 的当前状态求值（供 `_auto_currently_met` 递归）。"""
        check_trigger_depth(_depth, "scheduler._group_currently_met")
        trig = _normalize(trig)
        if trig.type != "group":
            # 单条状态子：按快照判
            if trig.type == "state" and trig.entity_id:
                if trig.from_ is not None:
                    return False
                try:
                    snap = self.states.snapshot([trig.entity_id])
                    current = snap.get(trig.entity_id)
                    return trig.to is None or current == trig.to
                except Exception:
                    return False
            return False
        if trig.op == "and":
            return all(self._group_currently_met(sub, _depth + 1) for sub in trig.sources)
        return any(self._group_currently_met(sub, _depth + 1) for sub in trig.sources)

    # ─────────────────────────────────────────────────────────────────
    # D10：边沿触发的 last_met 事实存取（met_io 落盘 / 内存退回）
    # ─────────────────────────────────────────────────────────────────
    def _read_last_met(self, auto_id: str) -> bool | None:
        """读上轮全条件布尔。`met_io` 注入（serve）⇒ 走落盘；否则内存 dict。"""
        if self.met_io is not None:
            try:
                read = getattr(self.met_io, "read_last_met", None)
                return read(auto_id) if callable(read) else None
            except Exception:  # noqa: BLE001 —— 读失败保守"无历史"（None），不阻塞派发
                return None
        return self._last_met.get(auto_id)

    def _record_met(self, auto_id: str, met: bool) -> None:
        """记本轮全条件布尔。`met_io` 注入 ⇒ 走落盘；否则内存 dict。"""
        if self.met_io is not None:
            try:
                rec = getattr(self.met_io, "record_met", None)
                if callable(rec):
                    rec(auto_id, met)
                return
            except Exception:  # noqa: BLE001 —— 记失败不影响派发（落盘是决策的留痕，不是闸）
                return
        self._last_met[auto_id] = met

    def _record_met_if_applicable(self, auto: Automation, *, met: bool) -> None:
        """D10：只有**带 group-and 条件层**的自动化才记 `last_met`（其余无"条件维持"语义）。

        `handle_event` 在条件不满（`satisfied=False`）时调这里把 `last_met` 推进成 False，
        保证"条件打破"被记下 ⇒ 下一次满足才算跳变沿（先断后立成立）。⛔ 不给单条 state/
        event/time 自动化记——它们不走边沿语义，记了反而会误伤旧行为。
        """
        if self._auto_currently_met(auto) is None:
            return
        self._record_met(auto.id, met)

    def _record_trigger_facts(self, auto: Automation, node: Node, event: BusEvent | None) -> None:
        """D10：触发计数记账的**唯一实现点**（spawn 出口与抑制腿共用，⛔ 不各写一份）。

        计数是事实、派发是决策（D10 卡 §一）：抑制（边沿维持/冷却）也要照记，
        所以两处都调这里。记账失败⛔ 不影响调度：吞成一行 warning（与 approve 审计同口径）。
        """
        if self.runtime_facts is None:
            return
        try:
            trig = getattr(node, "trigger", None)
            via = getattr(trig, "type", "") or ("queued" if event is None else "")
            self.runtime_facts(
                auto.id, node.id,
                (event.entity_id if event is not None else ""),
                via,
            )
        except Exception:  # noqa: BLE001 —— 记账绝不许弄死调度（吞但不静默）
            logger.warning(
                "RUNTIME_FACTS_TRIGGER_FAILED 触发记账失败（不影响执行） auto=%s node=%s",
                auto.id, node.id, exc_info=True,
            )

    def _cooldown_seconds(self, auto: Automation) -> float:
        """D10：冷却期解析——IR 元数据可配（`cooldown`），缺失取默认 10 分钟。"""
        meta = getattr(auto, "raw", {}) or {}
        raw = meta.get("cooldown") if isinstance(meta, Mapping) else None
        if raw is not None:
            try:
                val = float(raw)
                if val >= 0:
                    return val
            except (TypeError, ValueError):
                pass
        return DEFAULT_COOLDOWN_S

    def _in_cooldown(self, auto_id: str, now: float, cooldown: float) -> bool:
        """D10：该自动化是否仍在冷却期（距上次成功派发 < cooldown）。"""
        last = self._cooldown.get(auto_id)
        return last is not None and (now - last) < cooldown

    def _dispatch_allowed(self, auto: Automation) -> tuple[bool, str]:
        """D10 主闸：边沿触发（条件不满→满足才派发）＋冷却兜底。

        返回 `(allowed, why)`：`allowed=False` 时 `why` 是抑制原因
        （`edge_held`＝条件持续满足电平维持／`cooldown`＝冷却期内）。
        触发计数照记（调用方在 spawn 出口记）——**计数是事实、派发是决策**。

        ⛔ 仅对**有 group-and 条件层**的自动化生效（AC 这类"条件维持"形状）。
        入口是单条状态/event/time/group-or/无触发 ⇒ `_auto_currently_met` 返 None ⇒
        无"条件维持"语义，**每次事件命中即派发**（旧行为，不施加边沿/冷却）。

        ⚠️ 这是**第四层闸**（装载=archived／触发=enabled／派发=本闸／执行）。
        只在本函数加"派发"闸，⛔ 不碰 watch 装载/触发三层闸语义（台账 §1.173）。
        """
        # 边沿主路（优先）：求当前全条件布尔，只有 不满→满足 的跳变沿才派发。
        # 条件仍全满足（上轮已 True）⇒ 电平维持，最直接的主语义抑制。
        met = self._auto_currently_met(auto)
        if met is None:
            # 无 group-and 条件层 ⇒ 不适用边沿/冷却（无"条件维持"语义），直接放行
            return True, ""
        prev = self._read_last_met(auto.id)
        self._record_met(auto.id, met)
        if met and prev:
            # 条件持续满足（上轮已 True）：电平维持 ⇒ 不重派（判据①边沿主路，优先级最高）
            return False, "edge_held"
        # 冷却兜底（只对 group-and 自动化）：新回合来得太快 ⇒ 仍不派发
        cooldown = self._cooldown_seconds(auto)
        now = self.clock.monotonic()
        if self._in_cooldown(auto.id, now, cooldown):
            return False, "cooldown"
        return True, ""

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
