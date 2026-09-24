"""HighFidelityHA / HighFidelityAdapter 单测（C10：≥5 个用例）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from autoforge.af_adapters import CallResult
from autoforge.af_adapters.base import Adapter
from autoforge.af_live import iter_sse_blocks, parse_ha_event
from autoforge.af_state import Snapshot, StateProvider, UnknownEntity
from autoforge.af_vhass.fake import FakeHA
from autoforge.af_vhass.high_fidelity import (
    HighFidelityAdapter, HighFidelityHA, create_adapter, create_ha,
    create_ha_from_graph, hifi_enabled, solar_events, sun_state_at,
)


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
    ha = HighFidelityHA(clock=clock, **kwargs)
    adapter = HighFidelityAdapter(ha=ha)
    return clock, ha, adapter


def test_seed_可读快照且不发布事件():
    clock, ha, adapter = _ctx()
    ha.seed({
        "light.study": "off",
        "climate.ac": {"state": "cool", "attributes": {"temperature": 26}},
    })

    snap = ha.snapshot(["light.study", "climate.ac"])
    assert isinstance(snap, Snapshot)
    assert snap.values == {"light.study": "off", "climate.ac": "cool"}
    assert snap.get("climate.ac.temperature") == 26     # entity.attribute 形态
    assert ha.bus.history() == []                        # 初始状态不算变化

    ha.set("light.study", "on", {"brightness": 200})     # 测试构造用，同样不发事件
    assert ha.get("light.study") == "on"
    assert ha.bus.history() == []


def test_未知实体快照抛UnknownEntity():
    clock, ha, adapter = _ctx()
    ha.seed({"light.study": "off"})
    with pytest.raises(UnknownEntity):
        ha.snapshot(["light.missing"])


def test_call入队未生效_tick后生效():
    clock, ha, adapter = _ctx(device_latency=0.1)
    ha.seed({"light.study": "off"})

    result = adapter.call("light.turn_on", {"entity_id": "light.study", "brightness": 128})
    assert result.success is True
    assert result.data["queued"] is True
    assert ha.get("light.study") == "off"       # 延迟未到
    assert ha.tick() == 0

    clock.advance(0.2)
    assert ha.tick() == 1
    assert ha.get("light.study") == "on"
    assert ha.snapshot(["light.study"]).get("light.study.brightness") == 128


def test_完整链路_call_tick_snapshot_SSE_parse():
    clock, ha, adapter = _ctx(device_latency=0.1)
    ha.seed({"light.study": "off"})

    adapter.call("light.turn_on", {"entity_id": "light.study", "brightness": 200})
    clock.advance(0.2)
    ha.tick()

    assert ha.snapshot(["light.study"]).values["light.study"] == "on"

    blocks = list(iter_sse_blocks(ha.sse.iter_lines()))
    assert len(blocks) == 1
    bus_event = parse_ha_event(*blocks[0])
    assert bus_event is not None
    assert (bus_event.entity_id, bus_event.state) == ("light.study", "on")
    assert bus_event.payload["attributes"] == {"brightness": 200}


def test_故障优先于入队():
    clock, ha, adapter = _ctx()
    ha.seed({"light.study": "off"})

    adapter.timeout_next()
    result = adapter.call("light.turn_on", {"entity_id": "light.study"})

    assert result.success is False
    assert result.data.get("fault") == "timeout"
    assert ha.queue.pending() == []
    assert ha.get("light.study") == "off"
    assert len(adapter.calls) == 1      # 调用仍被记录（与 FakeHAAdapter 一致）


def test_unmodeled动作被记录但仍入队():
    clock, ha, adapter = _ctx(device_latency=0.0)
    result = adapter.call("homeassistant.reload_config", {"entity_id": "light.study"})

    assert result.success is True
    assert adapter.unmodeled == ["homeassistant.reload_config"]
    ha.flush()
    assert ha.queue.pending() == []


def test_断流重连链路():
    clock, ha, adapter = _ctx(device_latency=0.1)
    ha.seed({"light.study": "off"})
    ha.sse.break_connection()

    with pytest.raises(ConnectionError):
        list(ha.sse.iter_lines())

    adapter.call("light.turn_on", {"entity_id": "light.study"})
    clock.advance(0.2)
    ha.tick()

    ha.sse.reconnect()
    blocks = list(iter_sse_blocks(ha.sse.iter_lines()))
    bus_event = parse_ha_event(*blocks[0])
    assert bus_event is not None and bus_event.state == "on"


def test_cover中间状态链路():
    clock, ha, adapter = _ctx(device_latency=0.0)
    ha.seed({"cover.living_room": "closed"})

    adapter.call("cover.open_cover", {"entity_id": "cover.living_room"})
    ha.tick()
    assert ha.get("cover.living_room") == "opening"

    clock.advance(5)
    ha.tick()
    assert ha.get("cover.living_room") == "open"


def test_sun_按真实日出日落判定并发布事件():
    cst = timezone(timedelta(hours=8))
    clock = StubClock(start=datetime(2026, 6, 21, 12, 0, tzinfo=cst))
    ha = HighFidelityHA(clock=clock)

    assert ha.get("sun.sun") == "above_horizon"
    ha.tick()                          # 首次同步只记录
    clock.advance(9 * 3600)            # 21:00，已过日落
    ha.tick()

    assert ha.get("sun.sun") == "below_horizon"
    events = ha.bus.history("sun.sun")
    assert [(e.old_state, e.new_state) for e in events] == [("above_horizon", "below_horizon")]
    assert "next_setting" in ha.sun_info()


def test_sun_深圳夏至冬至日落时间():
    cst = timezone(timedelta(hours=8))
    _, summer_set = solar_events(datetime(2026, 6, 21, 12, 0, tzinfo=cst))
    _, winter_set = solar_events(datetime(2026, 12, 21, 12, 0, tzinfo=cst))
    summer_rise, _ = solar_events(datetime(2026, 6, 21, 12, 0, tzinfo=cst))

    local = lambda dt: dt.astimezone(cst)            # noqa: E731
    assert (18, 50) <= (local(summer_set).hour, local(summer_set).minute) <= (19, 30)   # ≈19:11
    assert (17, 20) <= (local(winter_set).hour, local(winter_set).minute) <= (18, 0)    # ≈17:42
    assert (5, 20) <= (local(summer_rise).hour, local(summer_rise).minute) <= (6, 10)
    assert sun_state_at(datetime(2026, 6, 21, 23, 0, tzinfo=cst)) == "below_horizon"


def test_协议兼容_运行时零改动():
    clock, ha, adapter = _ctx()
    assert isinstance(ha, StateProvider)
    assert isinstance(adapter, Adapter)
    assert adapter.name == "ha"
    assert isinstance(adapter.call("light.turn_on", {"entity_id": "light.x"}), CallResult)


def test_开关切换_默认关闭():
    assert hifi_enabled({}) is False
    assert hifi_enabled({"AUTOFORGE_VHASS_HIFI": "1"}) is True
    assert hifi_enabled({"AUTOFORGE_VHASS_HIFI": "on"}) is True

    clock = StubClock()
    assert isinstance(create_ha(clock=clock, hifi=False), FakeHA)
    assert isinstance(create_ha(clock=clock, hifi=True), HighFidelityHA)
    assert isinstance(create_adapter(FakeHA()), object)


class _StubAuto:
    def __init__(self, reads, writes=(), expects=()):
        self._reads, self._writes, self._expects = set(reads), set(writes), set(expects)

    def reads(self):
        return self._reads

    def writes(self):
        return self._writes

    def expect_entities(self):
        return self._expects


def test_create_ha_from_graph_按图覆盖实体():
    clock = StubClock()
    graph = [_StubAuto(["light.study"], writes=["climate.ac"], expects=["switch.kettle"])]

    ha = create_ha_from_graph(graph, {"light.study": "on"}, clock=clock, hifi=True, device_latency=0.0)
    snap = ha.snapshot(["light.study", "climate.ac", "switch.kettle"])
    assert snap.values == {"light.study": "on", "climate.ac": "off", "switch.kettle": "off"}
    assert ha.bus.history() == []

    fake = create_ha_from_graph(graph, clock=clock, hifi=False)
    assert isinstance(fake, FakeHA)

