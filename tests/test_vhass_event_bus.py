"""FakeEventBus 单测（C10：≥5 个用例）。"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from autoforge.af_adapters import CallResult
from autoforge.af_adapters.base import FaultQueue
from autoforge.af_time import TimeSource
from autoforge.af_vhass.event_bus import FakeEventBus, StateChangeEvent


class StubClock:
    """确定性时钟（可推进），杜绝 datetime.now()/time.time()（C6）。"""

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

    def today(self):
        return self._now.date()

    def utc_now(self):
        return self._now

    def wall_after(self, seconds: float):
        return self._now + timedelta(seconds=seconds)


def test_stub_clock_满足TimeSource协议():
    # TimeSource 是 Protocol（无 runtime_checkable），用 hasattr 验证关键方法
    clock = StubClock()
    for method in ("now", "monotonic", "local_now", "today", "utc_now", "wall_after"):
        assert hasattr(clock, method), f"StubClock 缺少 {method}"
    assert callable(clock.now) and callable(clock.monotonic)


def test_publish_通知订阅者且事件字段正确():
    clock = StubClock()
    bus = FakeEventBus(clock=clock)
    seen: list[StateChangeEvent] = []
    bus.subscribe(seen.append)

    event = bus.publish("light.study", "off", "on", {"brightness": 0}, {"brightness": 200})

    assert event is not None
    assert seen == [event]
    assert event.entity_id == "light.study"
    assert event.old_state == "off"
    assert event.new_state == "on"
    assert event.old_attributes == {"brightness": 0}
    assert event.new_attributes == {"brightness": 200}
    assert event.last_changed == clock.now().isoformat()
    assert bus.history() == [event]


def test_相同状态且属性无变化不发布():
    clock = StubClock()
    bus = FakeEventBus(clock=clock)
    seen: list[StateChangeEvent] = []
    bus.subscribe(seen.append)

    assert bus.publish("light.study", "on", "on", {"a": 1}, {"a": 1}) is None
    assert bus.publish("light.study", "off", "off", None, None) is None
    assert seen == []
    assert bus.history() == []


def test_dedup_同实体同状态同时间戳不重复():
    clock = StubClock()
    bus = FakeEventBus(clock=clock, dedup=True)

    first = bus.publish("light.study", "off", "on", {}, {"brightness": 10})
    assert first is not None
    # 时钟未推进：state+last_changed 与上一条相同 → 视为重复投递（重连补发的形态）
    assert bus.publish("light.study", "on", "on", {"brightness": 10}, {"brightness": 20}) is None
    clock.advance(1)
    assert bus.publish("light.study", "on", "on", {"brightness": 10}, {"brightness": 20}) is not None
    assert len(bus.history()) == 2


def test_dedup_关闭时同刻重复也发布():
    clock = StubClock()
    bus = FakeEventBus(clock=clock, dedup=False)

    assert bus.publish("light.study", "off", "on") is not None
    assert bus.publish("light.study", "on", "on", {}, {"x": 1}) is not None
    assert len(bus.history()) == 2


def test_history_按实体过滤():
    clock = StubClock()
    bus = FakeEventBus(clock=clock)
    bus.publish("light.a", None, "on")
    clock.advance(1)
    bus.publish("switch.b", None, "on")
    clock.advance(1)
    bus.publish("light.a", "on", "off")

    assert [e.new_state for e in bus.history("light.a")] == ["on", "off"]
    assert [e.new_state for e in bus.history("switch.b")] == ["on"]
    assert len(bus.history()) == 3


def test_subscribe_返回取消函数_取消后不再接收():
    clock = StubClock()
    bus = FakeEventBus(clock=clock)
    seen: list[StateChangeEvent] = []
    unsubscribe = bus.subscribe(seen.append)

    bus.publish("light.a", None, "on")
    unsubscribe()
    clock.advance(1)
    bus.publish("light.a", "on", "off")

    assert len(seen) == 1
    unsubscribe()  # 幂等，不抛异常


def test_event_id_格式为_timestamp_seq():
    clock = StubClock()
    bus = FakeEventBus(clock=clock)

    first = bus.publish("light.a", None, "on")
    second = bus.publish("light.b", None, "on")

    assert first.event_id == f"{int(clock.now().timestamp())}.001"
    assert second.event_id.endswith(".002")
    assert re.fullmatch(r"\d+\.\d{3}", first.event_id)


def test_仅属性变化也发布事件():
    clock = StubClock()
    bus = FakeEventBus(clock=clock)
    clock.advance(1)

    event = bus.publish("climate.ac", "cool", "cool", {"hvac_action": "cooling"}, {"hvac_action": "idle"})

    assert event is not None
    assert event.old_state == event.new_state == "cool"
    assert event.new_attributes["hvac_action"] == "idle"


def test_clear_清空历史与订阅者():
    clock = StubClock()
    bus = FakeEventBus(clock=clock)
    seen: list[StateChangeEvent] = []
    bus.subscribe(seen.append)
    bus.publish("light.a", None, "on")

    bus.clear()
    clock.advance(1)
    bus.publish("light.a", "on", "off")

    # clear 清空了 bus 内部的 history 和 subscribers；
    # seen 列表仍保留 clear 前收到的 1 个事件，clear 后因订阅者已清空不再收到
    assert len(bus.history()) == 1
    assert len(seen) == 1


def test_事件与CallResult同属基础契约():
    # 防回归：确认高仿真层不绕开 af_adapters / af_bus 的既有类型
    assert CallResult.ok({"x": 1}).success is True
    assert FaultQueue().empty() is True
    assert StateChangeEvent("a", None, "on", {}, {}, "t", "1").dedup_key() == ("a", "on", "t")
