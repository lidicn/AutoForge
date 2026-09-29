"""事件总线 —— 去重 / 节流 / 熔断 / 精确订阅（KICKOFF §4.1）。

职责：
- **去重**：`(entity_id, state, last_changed)` 三元组，同三元组只分发一次
- **节流**：单实体 >1 次/200ms 合并
- **熔断**：单实体 10s 内状态变更 ≥5 次 → 熔断该实体**所有**触发分发，30s 后恢复 + 审计
  （静态图查不出家庭里 90% 的隐式循环，熔断是第二道防线，见 IR §8.3）
- **订阅**：按 `entity_id` 或事件类型（`source`）**精确订阅，不支持通配符**
"""

from __future__ import annotations

import logging
import os
from collections import deque
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterable, Mapping

from .af_audit import BREAKER_OPEN, BREAKER_RECOVER, AuditEvent, AuditLog, HANDLER_FAILED
from .af_time import TimeSource, SystemTimeSource

logger = logging.getLogger(__name__)


def _env_number(
    name: str, default: float, *, lo: float | None = None, hi: float | None = None
) -> float:
    """读取数值型环境变量，解析失败 / 越界时回落默认（fail-safe 而非 fail-crash）。

    P1-7 修复：原实现直接 `int(os.getenv(...))`，运维写错（如 "12s"、空串、超范围）
    会让 EventBus 构造即抛 ValueError，服务起不来。
    """
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        val = float(raw)
    except (ValueError, TypeError):
        logger.warning("%s 解析失败（值=%r），回落默认 %s", name, raw, default)
        return default
    if lo is not None and val < lo:
        logger.warning("%s=%s 低于下限 %s，回落默认 %s", name, val, lo, default)
        return default
    if hi is not None and val > hi:
        logger.warning("%s=%s 高于上限 %s，回落默认 %s", name, val, hi, default)
        return default
    return val

__all__ = [
    "BusEvent",
    "EventBus",
    "ACCEPTED",
    "DUPLICATE",
    "THROTTLED",
    "BREAKER_BLOCKED",
    "EVENT_SOURCE",
    "EVENT_ENTITY_PREFIX",
]

ACCEPTED = "accepted"
DUPLICATE = "duplicate"
THROTTLED = "throttled"
BREAKER_BLOCKED = "breaker_open"

#: v0.3.0 跨自动化事件·发布侧（IR §4.3）：自定义事件走独立通道
EVENT_SOURCE = "emit"
EVENT_ENTITY_PREFIX = "event."


@dataclass(frozen=True)
class BusEvent:
    """总线事件。时间戳取 HA 的 `last_changed`，**不用本地 datetime.now()**。"""

    entity_id: str
    state: str
    source: str = "ha"
    event_id: str = ""
    last_changed: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    #: v0.3.0：自定义事件名（实体状态事件为 `None`）
    event: str | None = None

    @classmethod
    def of(
        cls,
        entity_id: str,
        state: str,
        *,
        source: str = "ha",
        last_changed: str | None = None,
        **payload: Any,
    ) -> "BusEvent":
        import uuid

        return cls(
            entity_id=entity_id,
            state=str(state),
            source=source,
            event_id=uuid.uuid4().hex[:12],
            last_changed=last_changed,
            payload=dict(payload),
        )

    @classmethod
    def custom(cls, event: str, data: Mapping[str, Any] | None = None) -> "BusEvent":
        """构造一条**非实体自定义事件**（v0.3.0 发布侧，IR §4.3）。

        与实体状态事件隔离：主体为 `event.<name>`、`source="emit"`、无 `last_changed`。
        """
        import uuid

        return cls(
            entity_id=f"{EVENT_ENTITY_PREFIX}{event}",
            state="",
            source=EVENT_SOURCE,
            event_id=uuid.uuid4().hex[:12],
            last_changed=None,
            payload=dict(data or {}),
            event=event,
        )

    @classmethod
    def event_name(cls, event: str) -> str:
        """自定义事件在总线里的订阅键（v0.4.0 `on event` 用它精确订阅，**无通配符**）。"""
        return f"{EVENT_ENTITY_PREFIX}{event}"

    @property
    def dedup_key(self) -> tuple[str, str, str | None]:
        # 自定义事件没有 `last_changed`，若沿用实体三元组会把"连续两次 emit 同一事件名"
        # 误判为重复；改用 `event_id` 保证每次发布都算新事件（风暴由熔断兜底）。
        if self.event is not None:
            return (self.entity_id, self.state, self.event_id)
        return (self.entity_id, self.state, self.last_changed)


class EventBus:
    """单进程内存事件总线（G1 无跨进程）。"""

    def __init__(
        self,
        time_source: TimeSource | None = None,
        audit: AuditLog | None = None,
        *,
        dedup_size: int = 4096,
        throttle_ms: float = 200.0,
        breaker_threshold: int | None = None,
        breaker_window_s: float | None = None,
        breaker_cooldown_s: float | None = None,
    ):
        self.clock = time_source or SystemTimeSource()
        self.audit = audit if audit is not None else AuditLog()

        self.dedup_size = dedup_size
        self.throttle_window = throttle_ms / 1000.0
        #: 熔断阈值：默认偏宽松，避免误伤合法快速输入（物理开关抖动 / 用户连按）。
        # 真正的隐式循环由 IR 结构（emit 解耦 + `if` 护栏）在编译/运行期杜绝，
        # 此处熔断仅作最后兜底；阈值需显著高于单次物理操作的抖动次数。
        # R-53：熔断参数可经 env 覆盖（AUTOFORGE_BREAKER_*），未显式传时读 env，缺省与原硬编码一致。
        self.breaker_threshold = (
            breaker_threshold if breaker_threshold is not None
            else int(_env_number("AUTOFORGE_BREAKER_THRESHOLD", 12, lo=1, hi=1000))
        )
        self.breaker_window = (
            breaker_window_s if breaker_window_s is not None
            else _env_number("AUTOFORGE_BREAKER_WINDOW_S", 10, lo=0.1)
        )
        self.breaker_cooldown = (
            breaker_cooldown_s if breaker_cooldown_s is not None
            else _env_number("AUTOFORGE_BREAKER_COOLDOWN_S", 15, lo=0.1)
        )

        self._seen: set[tuple[str, str, str | None]] = set()
        self._seen_order: deque[tuple[str, str, str | None]] = deque()
        self._last_accepted: dict[str, float] = {}
        self._last_state: dict[str, str] = {}
        self._changes: dict[str, deque[float]] = {}
        self._open_until: dict[str, float] = {}

        self._subs: dict[str, list[Callable[[BusEvent], None]]] = {}
        self.counts: dict[str, int] = {ACCEPTED: 0, DUPLICATE: 0, THROTTLED: 0, BREAKER_BLOCKED: 0}
        #: v0.3.0：已成功发布的自定义事件（只读观测，供验证与后续服务层使用）
        # P1-1 修复：有界 deque，避免常驻进程无限增长。
        self.emitted: deque[BusEvent] = deque(maxlen=1000)

    # ── 订阅 ──────────────────────────────────────────────────────────
    def subscribe(self, key: str, handler: Callable[[BusEvent], None]) -> None:
        """按 `entity_id` 或事件类型（`source`）精确订阅。**不支持通配符**。"""
        self._subs.setdefault(key, []).append(handler)

    def unsubscribe_all(self) -> None:
        self._subs.clear()

    # ── 发布 ──────────────────────────────────────────────────────────
    def publish(self, event: BusEvent) -> str:
        """发布事件，返回分发结果（accepted / duplicate / throttled / breaker_open）。

        ⚠️ 自定义事件（`event.event is not None`，即 `emit` 广播）是**内部信号**：
        去重键已含 `event_id`（每次都是新事件），且产生速率受外部状态变更上限约束，
        **不参与熔断/节流统计**——否则自动化联动风暴（A 开→emit→B 跟→B 状态变更→…）
        会触发熔断/节流**自伤**，在用户连按/继电器抖动时阻断合法同步。
        """
        if self._is_duplicate(event):
            self.counts[DUPLICATE] += 1
            return DUPLICATE

        now = self.clock.monotonic()

        if event.event is None:
            # 实体状态事件：去重后做熔断 + 节流（防自动化隐式循环 / 抖动）
            self._maybe_recover(event.entity_id)
            self._record_change(event.entity_id, now)
            if self.is_open(event.entity_id):
                self.counts[BREAKER_BLOCKED] += 1
                self._log_drop(BREAKER_BLOCKED, event)
                return BREAKER_BLOCKED
            if not self._pass_throttle(event.entity_id, now, event.state):
                self.counts[THROTTLED] += 1
                self._log_drop(THROTTLED, event)
                return THROTTLED
        else:
            # P1-5 修复：自定义事件此前完全跳过熔断，与 publish_custom 注释
            # "事件风暴由熔断兜底"自相矛盾。改为按事件名维度单独熔断
            # （key = event.<name>），既保留风暴兜底，又不污染实体维度的熔断计数
            # （避免自动化联动风暴自伤实体维度）。阈值与实体一致，但维度隔离。
            name_key = f"{EVENT_ENTITY_PREFIX}{event.event}"
            self._maybe_recover(name_key)
            self._record_change(name_key, now)
            if self.is_open(name_key):
                self.counts[BREAKER_BLOCKED] += 1
                self._log_drop(BREAKER_BLOCKED, event)
                return BREAKER_BLOCKED

        self._last_accepted[event.entity_id] = now
        self._last_state[event.entity_id] = event.state
        self.counts[ACCEPTED] += 1
        self._dispatch(event)
        return ACCEPTED

    def _log_drop(self, result: str, event: "BusEvent") -> None:
        logger.info("[BUS-DROP] %s %s=%r src=%s", result, event.entity_id, event.state, event.source)

    def publish_all(self, events: Iterable[BusEvent]) -> list[str]:
        return [self.publish(e) for e in events]

    def publish_custom(self, event: str, data: Mapping[str, Any] | None = None) -> str:
        """发布一条**非实体自定义事件**（v0.3.0 跨自动化事件·发布侧，IR §4.3）。

        走独立通道：主体 `event.<name>`，与实体状态事件共享去重/节流/熔断结构——
        `dedup_key` 含 `event_id` 因此每次发布都算新事件；**事件风暴由熔断兜底**
        （同一事件名在窗口内过频 → 熔断，返回 `breaker_open`）。
        """
        bus_event = BusEvent.custom(event, data)
        result = self.publish(bus_event)
        if result == ACCEPTED:
            self.emitted.append(bus_event)
        return result

    # ── 熔断状态 ──────────────────────────────────────────────────────
    def is_open(self, entity_id: str) -> bool:
        """纯查询：该实体当前是否处于熔断态。

        P2-7 修复：原本在此处做"冷却到期→删除状态+记恢复审计"的副作用，
        导致 `stats()` 调用 `open_entities()`（逐个 `is_open`）会顺带"治愈"熔断，
        且观测接口与语义判断耦合。现改为纯查询，副作用由 `_maybe_recover` 承担。
        """
        until = self._open_until.get(entity_id)
        if until is None:
            return False
        return self.clock.monotonic() < until

    def _maybe_recover(self, entity_id: str) -> None:
        """副作用：若熔断冷却已结束，清除状态并记恢复审计。仅在发布路径显式调用。"""
        until = self._open_until.get(entity_id)
        if until is None:
            return
        if self.clock.monotonic() >= until:
            del self._open_until[entity_id]
            self._changes.pop(entity_id, None)
            self.audit.add(
                AuditEvent(
                    type=BREAKER_RECOVER,
                    at=self.clock.now(),
                    message=f"实体 {entity_id} 熔断恢复，重新接受触发",
                    entity_id=entity_id,
                )
            )

    def open_entities(self) -> list[str]:
        return [e for e in list(self._open_until) if self.is_open(e)]

    # ── 内部 ──────────────────────────────────────────────────────────
    def _is_duplicate(self, event: BusEvent) -> bool:
        # P1-6 修复：自定义事件 dedup_key 含唯一 event_id，永远不会被判重，却会占用
        # 共享 4096 LRU 槽位、把真实实体事件的去重窗口挤掉。自定义事件本就不去重，
        # 直接放行且不污染共享缓存。
        if event.event is not None:
            return False
        key = event.dedup_key
        if key in self._seen:
            return True
        self._seen.add(key)
        self._seen_order.append(key)
        if len(self._seen_order) > self.dedup_size:
            self._seen.discard(self._seen_order.popleft())
        return False

    def _record_change(self, entity_id: str, now: float) -> None:
        window = self._changes.setdefault(entity_id, deque())
        window.append(now)
        cutoff = now - self.breaker_window
        while window and window[0] < cutoff:
            window.popleft()
        if len(window) >= self.breaker_threshold and entity_id not in self._open_until:
            self._open_until[entity_id] = now + self.breaker_cooldown
            self.audit.add(
                AuditEvent(
                    type=BREAKER_OPEN,
                    at=self.clock.now(),
                    message=(
                        f"实体 {entity_id} 在 {self.breaker_window:g}s 内变更 "
                        f"{len(window)} 次，触发熔断（{self.breaker_cooldown:g}s 后恢复）"
                    ),
                    entity_id=entity_id,
                    data={"changes": len(window)},
                )
            )

    def _pass_throttle(self, entity_id: str, now: float, state: str) -> bool:
        """窗口内仍放行「真实状态跳变」（on↔off），只抑制「同状态重复上报」（抖动/双报）。

        原实现不区分状态，会把合法开关跳变也一并节流丢弃，导致依赖该事件的
        自动化偶发不触发（时序相关、逐轮抖动）。
        """
        last = self._last_accepted.get(entity_id)
        if last is None:
            return True
        if (now - last) >= self.throttle_window:
            return True
        # 窗口内但状态已变化 → 是真实跳变，必须放行
        return self._last_state.get(entity_id) != state

    def _dispatch(self, event: BusEvent) -> None:
        # 精确订阅：实体维度 + 事件类型维度，无通配符
        # P1-3 修复：逐个 handler 隔离异常，任一订阅者抛错不影响其他订阅者
        # （与「自动化之间隔离」的设计意图一致），并记审计事件。
        for key in (event.entity_id, event.source):
            for handler in list(self._subs.get(key, ())):
                try:
                    handler(event)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("事件总线回调异常（已隔离，不影响其他订阅者）: key=%s", key)
                    self.audit.add(
                        AuditEvent(
                            type=HANDLER_FAILED,
                            at=self.clock.now(),
                            message=(
                                f"订阅回调 {getattr(handler, '__qualname__', repr(handler))} "
                                f"抛出异常：{exc!r}"
                            ),
                            entity_id=event.entity_id,
                        )
                    )

    def stats(self) -> dict[str, Any]:
        return {
            "counts": dict(self.counts),
            "open_entities": self.open_entities(),
            "subscriptions": {k: len(v) for k, v in self._subs.items()},
        }


def replay(events: Iterable[BusEvent], bus: EventBus) -> list[str]:
    """把一批事件灌进总线（仿真/回放用）。"""
    return bus.publish_all(events)
