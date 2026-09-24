"""FakeEventBus —— 高仿真层的内存事件总线。

* ``publish()`` 是事件的唯一入口（约束 C7），DeviceSM / ActionQueue 都必须经它发布；
* ``last_changed`` / ``event_id`` 一律取自注入的 ``TimeSource``（约束 C6）；
* 通知同步完成（测试可控），subscriber 在 ``publish()`` 返回前一定被调用；
* 去重键 ``(entity_id, new_state, last_changed)``，用于 SSE 断流重连补发时抑制重复投递。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..af_time import TimeSource

Subscriber = Callable[["StateChangeEvent"], None]
Unsubscribe = Callable[[], None]


@dataclass
class StateChangeEvent:
    """一次状态变化，字段与 HA ``state_changed`` 事件的 ``data`` 部分对齐。"""

    entity_id: str
    old_state: str | None
    new_state: str
    old_attributes: dict[str, Any]
    new_attributes: dict[str, Any]
    last_changed: str        # ISO 8601 带时区，来自 TimeSource
    event_id: str            # "{unix_ts}.{seq:03d}"，与 HA 的 SSE id 形态一致

    def dedup_key(self) -> tuple[str, str, str]:
        return (self.entity_id, self.new_state, self.last_changed)


@dataclass
class FakeEventBus:
    clock: TimeSource
    dedup: bool = True
    _subscribers: list[Subscriber] = field(default_factory=list, init=False)
    _history: list[StateChangeEvent] = field(default_factory=list, init=False)
    _last_event_id: int = field(default=0, init=False)

    def subscribe(self, callback: Subscriber) -> Unsubscribe:
        """订阅事件，返回取消订阅函数（幂等）。"""
        self._subscribers.append(callback)

        def _unsubscribe() -> None:
            try:
                self._subscribers.remove(callback)
            except ValueError:  # 已取消
                pass

        return _unsubscribe

    def publish(
        self,
        entity_id: str,
        old_state: str | None,
        new_state: str,
        old_attributes: dict[str, Any] | None = None,
        new_attributes: dict[str, Any] | None = None,
    ) -> StateChangeEvent | None:
        """发布状态变化。

        - ``old_state == new_state`` 且属性无变化 → 返回 ``None``（不发无变化事件）；
        - ``dedup=True`` 且最近一条同实体事件的 ``state+last_changed`` 相同 → 返回 ``None``
          （SSE 重连重复投递的形态；注意去重键不含属性，时钟未推进时同状态的二次属性
          更新会被抑制，仿真时请推进时钟）；
        - 否则生成事件、入 history、同步通知所有 subscriber 并返回事件。
        """
        old_attrs = dict(old_attributes or {})
        new_attrs = dict(new_attributes or {})
        if old_state == new_state and old_attrs == new_attrs:
            return None

        now = self.clock.now()
        last_changed = now.isoformat()

        if self.dedup:
            for previous in reversed(self._history):
                if previous.entity_id == entity_id:
                    if previous.new_state == new_state and previous.last_changed == last_changed:
                        return None
                    break

        self._last_event_id += 1
        event = StateChangeEvent(
            entity_id=entity_id,
            old_state=None if old_state is None else str(old_state),
            new_state=str(new_state),
            old_attributes=old_attrs,
            new_attributes=new_attrs,
            last_changed=last_changed,
            event_id=f"{int(now.timestamp())}.{self._last_event_id:03d}",
        )
        self._history.append(event)
        for callback in list(self._subscribers):
            callback(event)
        return event

    def history(self, entity_id: str | None = None) -> list[StateChangeEvent]:
        """事件历史（副本），可按 entity_id 过滤。"""
        if entity_id is None:
            return list(self._history)
        return [event for event in self._history if event.entity_id == entity_id]

    def clear(self) -> None:
        """清空历史与订阅者。"""
        self._history.clear()
        self._subscribers.clear()
