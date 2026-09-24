"""FakeSSEStream —— 把总线事件转成 HA ``/api/stream`` 的 SSE 行流。

格式与真实 HA 逐字节对齐（HA 不发 ``event:`` 行，只发 ``id:`` + ``data:``，空行分隔；
心跳是注释行 ``: ping`` + 空行），可直接喂给 ``af_live.iter_sse_blocks``。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from ..af_time import TimeSource
from .event_bus import FakeEventBus, StateChangeEvent


@dataclass
class FakeSSEStream:
    bus: FakeEventBus
    clock: TimeSource
    heartbeat_interval: float = 30.0    # 秒；0 表示不发心跳
    _buffer: list[str] = field(default_factory=list, init=False)
    _connected: bool = field(default=True, init=False)
    _last_heartbeat: float = field(default=0.0, init=False)
    _subscriber_token: Callable[[], None] | None = field(default=None, init=False)
    _emitted_ids: set[str] = field(default_factory=set, init=False)
    _start_index: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self._last_heartbeat = self.clock.monotonic()
        self._start_index = len(self.bus.history())
        self._subscriber_token = self.bus.subscribe(self._on_event)

    # ---- 事件 → SSE -----------------------------------------------------------
    def _on_event(self, event: StateChangeEvent) -> None:
        self._emit(event)

    def _emit(self, event: StateChangeEvent) -> None:
        if not self._connected:
            return                      # 断流期间不写 buffer，重连时按 history 补发
        if event.event_id in self._emitted_ids:
            return                      # 补发去重（对应 SSE id: 跨重连去重）
        self._emitted_ids.add(event.event_id)
        self._buffer.extend(self._format_event(event))

    def _format_event(self, event: StateChangeEvent) -> list[str]:
        """HA SSE 块：``id:`` / ``data:`` / 空行。old/new_state 含
        entity_id、state、attributes、last_changed。"""

        def state_dict(state: str | None, attributes: dict[str, Any]) -> dict[str, Any] | None:
            if state is None:
                return None
            return {
                "entity_id": event.entity_id,
                "state": state,
                "attributes": dict(attributes or {}),
                "last_changed": event.last_changed,
            }

        payload = {
            "event_type": "state_changed",
            "data": {
                "entity_id": event.entity_id,
                "old_state": state_dict(event.old_state, event.old_attributes),
                "new_state": state_dict(event.new_state, event.new_attributes),
            },
            "origin": "LOCAL",
            "time_fired": event.last_changed,
        }
        return [f"id: {event.event_id}", f"data: {json.dumps(payload, default=str)}", ""]

    # ---- 读取 -----------------------------------------------------------------
    def iter_lines(self) -> Iterator[str]:
        """产出 SSE 行流（每次调用先补到期心跳，再排空 buffer）。

        断流时**立即** raise ConnectionError（不是静默返回空流）。
        """
        if not self._connected:
            raise ConnectionError("fake SSE 流已断开（模拟断流）")
        return self._drain()

    def _drain(self) -> Iterator[str]:
        self._flush_heartbeat()
        lines = list(self._buffer)
        self._buffer.clear()
        yield from lines

    def _flush_heartbeat(self) -> None:
        if not self.heartbeat_interval or self.heartbeat_interval <= 0:
            return
        now = self.clock.monotonic()
        if now - self._last_heartbeat >= self.heartbeat_interval:
            self._buffer.append(": ping")
            self._buffer.append("")
            self._last_heartbeat = now

    # ---- 断流 / 重连 -----------------------------------------------------------
    def break_connection(self) -> None:
        """模拟断流：后续 iter_lines() raise ConnectionError。"""
        self._connected = False
        self._buffer.clear()

    def reconnect(self, replay: bool = True) -> None:
        """模拟重连：清空 buffer、恢复连接，并（可选）补发断流期间的事件。

        补发来源是 ``bus.history()``（订阅起点之后），按发布顺序、按 ``event_id`` 去重，
        与 ``af_live.parse_ha_event`` 的 SSE id 去重语义一致。
        """
        self._connected = True
        self._buffer.clear()
        for event in self.bus.history()[self._start_index:]:
            if event.event_id in self._emitted_ids:
                continue
            self._emitted_ids.add(event.event_id)     # replay=False 时同样标记为已消费
            if replay:
                self._buffer.extend(self._format_event(event))
        self._last_heartbeat = self.clock.monotonic()

    @property
    def connected(self) -> bool:
        return self._connected

    def close(self) -> None:
        """退订并释放资源。"""
        if self._subscriber_token is not None:
            self._subscriber_token()
            self._subscriber_token = None
