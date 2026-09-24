"""ActionQueue 单测（C10：≥5 个用例）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from autoforge.af_vhass.action_queue import ActionQueue, PendingAction
from autoforge.af_vhass.event_bus import FakeEventBus


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


def _queue(**kwargs):
    clock = StubClock()
    bus = FakeEventBus(clock=clock)
    return clock, bus, ActionQueue(bus=bus, clock=clock, **kwargs)


def test_call_只入队不改状态():
    clock, bus, queue = _queue()
    queue.call("light.turn_on", {"entity_id": "light.study"})

    assert len(queue.pending()) == 1
    assert queue.get_sm("light.study").state == "off"
    assert bus.history() == []


def test_flush_到期动作生效并发布事件():
    clock, bus, queue = _queue()
    queue.call("light.turn_on", {"entity_id": "light.study", "brightness": 128})

    assert queue.flush() == 0            # 未到期
    clock.advance(0.2)
    assert queue.flush() == 1

    sm = queue.get_sm("light.study")
    assert sm.state == "on"
    assert sm.attributes["brightness"] == 128
    assert len(bus.history()) == 1
    assert queue.pending() == []


def test_同实体新指令取消旧pending():
    clock, bus, queue = _queue()
    queue.call("light.turn_on", {"entity_id": "light.study"})
    queue.call("light.turn_off", {"entity_id": "light.study"})

    pendings = queue.pending()
    assert pendings[0].cancelled is True        # 旧的被抢占
    assert pendings[1].cancelled is False

    clock.advance(0.2)
    queue.flush()
    assert queue.get_sm("light.study").state == "off"
    assert bus.history() == []                  # turn_on 从未执行


def test_多实体拆成独立PendingAction():
    clock, bus, queue = _queue()
    created = queue.call("light.turn_on", {"entity_id": ["light.a", "light.b"]})

    assert [p.entity_id for p in created] == ["light.a", "light.b"]
    assert all(isinstance(p, PendingAction) for p in created)
    assert len(queue.pending()) == 2


def test_零延迟_call后flush立即执行():
    clock, bus, queue = _queue(device_latency=0.0)
    queue.call("light.turn_on", {"entity_id": "light.a"})

    assert queue.flush() == 1
    assert queue.get_sm("light.a").state == "on"


def test_cancel_all_取消全部pending():
    clock, bus, queue = _queue()
    queue.call("light.turn_on", {"entity_id": "light.a"})
    queue.call("switch.turn_on", {"entity_id": "switch.b"})

    assert queue.cancel_all() == 2
    assert all(p.cancelled for p in queue.pending())
    clock.advance(1)
    assert queue.flush() == 0
    assert bus.history() == []


def test_cancel_all_可按实体过滤():
    clock, bus, queue = _queue()
    queue.call("light.turn_on", {"entity_id": "light.a"})
    queue.call("switch.turn_on", {"entity_id": "switch.b"})

    assert queue.cancel_all("light.a") == 1
    clock.advance(0.2)
    assert queue.flush() == 1
    assert queue.get_sm("switch.b").state == "on"


def test_tick_只执行到期动作():
    clock, bus, queue = _queue(device_latency=1.0)
    queue.call("light.turn_on", {"entity_id": "light.a"})

    assert queue.tick() == 0
    clock.advance(1.0)
    assert queue.tick() == 1
    assert queue.get_sm("light.a").state == "on"


def test_flush_all_忽略排期立即执行():
    clock, bus, queue = _queue(device_latency=10.0)
    queue.call("light.turn_on", {"entity_id": "light.a"})

    assert queue.flush_all() == 1
    assert queue.get_sm("light.a").state == "on"


def test_cover_followup_中间状态到达到():
    clock, bus, queue = _queue(device_latency=0.0)
    queue.call("cover.open_cover", {"entity_id": "cover.curtain"})
    queue.flush()

    sm = queue.get_sm("cover.curtain")
    assert sm.state == "opening"
    (pending,) = queue.pending()
    assert pending.action == "cover._arrive"

    clock.advance(5)
    assert queue.tick() == 1
    assert sm.state == "open"
    assert [e.new_state for e in bus.history()] == ["opening", "open"]
