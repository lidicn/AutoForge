"""FakeSSEStream 单测（C10：≥5 个用例），并对接 af_live 的解析器。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from autoforge.af_live import iter_sse_blocks, parse_ha_event
from autoforge.af_vhass.event_bus import FakeEventBus
from autoforge.af_vhass.sse_stream import FakeSSEStream


class StubClock:
    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 9, 23, 2, 0, 0, tzinfo=timezone.utc)
        self._mono = 1000.0

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._mono

    def local_now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)
        self._mono += float(seconds)


def _ctx(**kwargs):
    clock = StubClock()
    bus = FakeEventBus(clock=clock)
    stream = FakeSSEStream(bus=bus, clock=clock, **kwargs)
    return clock, bus, stream


def test_事件_格式化为HA_SSE块():
    clock, bus, stream = _ctx()
    event = bus.publish("light.study", "off", "on", {}, {"brightness": 255})

    lines = list(stream.iter_lines())

    assert lines[0] == f"id: {event.event_id}"          # HA 不发 event: 行
    assert lines[1].startswith("data: ")
    assert lines[2] == ""

    payload = json.loads(lines[1][len("data: "):])
    assert payload["event_type"] == "state_changed"
    assert payload["origin"] == "LOCAL"
    assert payload["time_fired"] == event.last_changed
    data = payload["data"]
    assert data["entity_id"] == "light.study"
    assert data["old_state"] == {
        "entity_id": "light.study", "state": "off", "attributes": {}, "last_changed": event.last_changed,
    }
    assert data["new_state"]["state"] == "on"
    assert data["new_state"]["attributes"] == {"brightness": 255}
    assert data["new_state"]["last_changed"] == event.last_changed


def test_SSE块可被af_live解析为BusEvent():
    clock, bus, stream = _ctx()
    bus.publish("light.study", "off", "on", {}, {"brightness": 255})

    blocks = list(iter_sse_blocks(stream.iter_lines()))
    assert len(blocks) == 1
    event_type, data_str, sse_id = blocks[0]

    bus_event = parse_ha_event(event_type, data_str, sse_id)
    assert bus_event is not None
    assert bus_event.entity_id == "light.study"
    assert bus_event.state == "on"
    assert bus_event.source == "ha"
    assert bus_event.payload.get("ha_event_id") == sse_id
    assert bus_event.payload.get("attributes") == {"brightness": 255}


def test_心跳为注释行加空行():
    clock, bus, stream = _ctx(heartbeat_interval=1.0)
    clock.advance(2.0)

    assert list(stream.iter_lines()) == [": ping", ""]
    assert list(stream.iter_lines()) == []       # 同一周期内不重复心跳


def test_心跳关闭时不产出任何行():
    clock, bus, stream = _ctx(heartbeat_interval=0.0)
    clock.advance(100.0)
    assert list(stream.iter_lines()) == []


def test_break_connection_后iter_lines抛ConnectionError():
    clock, bus, stream = _ctx()
    stream.break_connection()

    assert stream.connected is False
    with pytest.raises(ConnectionError):
        list(stream.iter_lines())


def test_reconnect_补发断流期间事件():
    clock, bus, stream = _ctx()
    stream.break_connection()
    clock.advance(1)
    bus.publish("light.study", "off", "on")
    clock.advance(1)
    bus.publish("light.study", "on", "off")

    stream.reconnect()
    blocks = list(iter_sse_blocks(stream.iter_lines()))
    states = [parse_ha_event(t, d, i).state for t, d, i in blocks]
    assert states == ["on", "off"]
    assert stream.connected is True


def test_reconnect_replayFalse_丢弃断流期间事件():
    clock, bus, stream = _ctx()
    stream.break_connection()
    bus.publish("light.study", "off", "on")

    stream.reconnect(replay=False)
    assert list(stream.iter_lines()) == []
    stream.break_connection()
    stream.reconnect()                       # 也不再补发（已标记为消费）
    assert list(stream.iter_lines()) == []


def test_多事件按发布顺序产出():
    clock, bus, stream = _ctx()
    for state in ("on", "off", "on"):
        bus.publish("light.study", None, state)
        clock.advance(1)

    blocks = list(iter_sse_blocks(stream.iter_lines()))
    assert [parse_ha_event(t, d, i).state for t, d, i in blocks] == ["on", "off", "on"]
    ids = [sse_id for _, _, sse_id in blocks]
    assert len(set(ids)) == 3                 # 每条事件唯一 id
    assert list(stream.iter_lines()) == []    # buffer 已排空


def test_close_后不再接收事件():
    clock, bus, stream = _ctx()
    stream.close()
    bus.publish("light.study", "off", "on")
    assert list(stream.iter_lines()) == []
