"""DeviceStateMachine 单测（C10：≥5 个用例）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from autoforge.af_vhass.action_queue import ActionQueue
from autoforge.af_vhass.device_sm import (
    ClimateSM, CoverSM, DeviceSM, FanSM, GenericSM, LightSM, LockSM,
    MediaPlayerSM, SM_REGISTRY, SwitchSM, create_sm,
)
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


def _ctx():
    clock = StubClock()
    bus = FakeEventBus(clock=clock)
    return clock, bus


def test_light_turn_on_写入亮度并发事件():
    clock, bus = _ctx()
    sm = LightSM("light.study", bus, clock)

    sm.apply("turn_on", {"entity_id": "light.study", "brightness": 300})

    assert sm.state == "on"
    assert sm.attributes["brightness"] == 255        # 0-255 钳制
    (event,) = bus.history()
    assert (event.old_state, event.new_state) == ("off", "on")
    assert event.new_attributes["brightness"] == 255


def test_light_transition_渐变结束才落位():
    clock, bus = _ctx()
    sm = LightSM("light.study", bus, clock)

    sm.apply("turn_on", {"transition": 2, "brightness": 128})

    assert sm.state == "off"                          # 渐变期间不变
    assert bus.history() == []
    (follow,) = sm.drain_followups()
    assert (follow.service, follow.delay) == ("_finish_turn_on", 2.0)

    sm.apply(follow.service, follow.params)
    assert sm.state == "on"
    assert sm.attributes["brightness"] == 128
    assert len(bus.history()) == 1


def test_light_toggle_翻转():
    clock, bus = _ctx()
    sm = LightSM("light.study", bus, clock)

    sm.apply("toggle", {})
    assert sm.state == "on"
    clock.advance(1)
    sm.apply("toggle", {})
    assert sm.state == "off"
    assert [e.new_state for e in bus.history()] == ["on", "off"]


def test_climate_turn_on_state是hvac_mode且hvac_action为cooling():
    clock, bus = _ctx()
    sm = ClimateSM("climate.ac", bus, clock)

    sm.apply("turn_on", {"entity_id": "climate.ac"})

    assert sm.state == "cool"
    assert sm.attributes["hvac_action"] == "cooling"
    (event,) = bus.history()
    assert event.new_attributes["hvac_action"] == "cooling"


def test_climate_settle_到目标温度后收敛为idle():
    clock, bus = _ctx()
    sm = ClimateSM("climate.ac", bus, clock)
    sm.apply("set_temperature", {"temperature": 26})
    sm.apply("turn_on", {})
    sm.attributes["current_temperature"] = 30

    sm.apply("_settle", {})                           # 30 → 29
    assert sm.attributes["current_temperature"] == 29
    assert sm.attributes["hvac_action"] == "cooling"
    for _ in range(4):
        clock.advance(5)
        sm.apply("_settle", {})

    assert sm.attributes["current_temperature"] == 26
    assert sm.attributes["hvac_action"] == "idle"


def test_climate_set_temperature_state不变只更新属性():
    clock, bus = _ctx()
    sm = ClimateSM("climate.ac", bus, clock)
    sm.apply("turn_on", {})
    clock.advance(1)

    sm.apply("set_temperature", {"temperature": 24})

    assert sm.state == "cool"                          # state 不变
    assert sm.attributes["temperature"] == 24
    (event,) = bus.history()[1:]
    assert (event.old_state, event.new_state) == ("cool", "cool")
    assert event.new_attributes["temperature"] == 24


def test_climate_set_hvac_mode_heat_与_turn_off():
    clock, bus = _ctx()
    sm = ClimateSM("climate.ac", bus, clock)

    sm.apply("set_hvac_mode", {"hvac_mode": "heat"})
    assert (sm.state, sm.attributes["hvac_action"]) == ("heat", "heating")
    clock.advance(1)
    sm.apply("turn_off", {})
    assert (sm.state, sm.attributes["hvac_action"]) == ("off", "off")


def test_cover_open_cover_中间状态_opening_行程结束才open():
    clock, bus = _ctx()
    queue = ActionQueue(bus=bus, clock=clock, device_latency=0.0)

    queue.call("cover.open_cover", {"entity_id": "cover.living_room"})
    queue.flush()
    sm = queue.get_sm("cover.living_room")
    assert sm.state == "opening"                      # 中间状态（不能直接 open）

    (pending,) = queue.pending()
    assert pending.action == "cover._arrive"
    clock.advance(5)
    queue.tick()
    assert sm.state == "open"
    assert sm.attributes["current_position"] == 100
    assert [e.new_state for e in bus.history()] == ["opening", "open"]


def test_cover_set_cover_position_按方向选中间状态():
    clock, bus = _ctx()
    sm = CoverSM("cover.curtain", bus, clock)
    sm.reset("closed", {"current_position": 0})

    sm.apply("set_cover_position", {"position": 60})
    assert sm.state == "opening"
    clock.advance(1)
    sm.apply("set_cover_position", {"position": 0})
    assert sm.state == "closing"
    clock.advance(1)
    sm.apply("stop_cover", {})
    assert sm.state == "stopped"


def test_cover_close_cover_中间状态_closing():
    clock, bus = _ctx()
    sm = CoverSM("cover.curtain", bus, clock)
    sm.reset("open", {"current_position": 100})

    sm.apply("close_cover", {})
    assert sm.state == "closing"
    (follow,) = sm.drain_followups()
    sm.apply(follow.service, follow.params)
    assert sm.state == "closed"
    assert sm.attributes["current_position"] == 0


def test_switch_toggle_翻转并发事件():
    clock, bus = _ctx()
    sm = SwitchSM("switch.kettle", bus, clock)

    sm.apply("toggle", {})
    assert sm.state == "on"
    clock.advance(1)
    sm.apply("toggle", {})
    assert sm.state == "off"
    assert len(bus.history()) == 2


def test_fan_百分比与media_player_服务映射():
    clock, bus = _ctx()
    fan = FanSM("fan.bedroom", bus, clock)
    fan.apply("turn_on", {"percentage": 40})
    assert (fan.state, fan.attributes["percentage"]) == ("on", 40)

    player = MediaPlayerSM("media_player.tv", bus, clock)
    player.apply("media_pause", {})
    assert player.state == "paused"
    clock.advance(1)
    player.apply("volume_set", {"volume_level": 0.5})
    assert player.state == "paused"
    assert player.attributes["volume_level"] == 0.5


def test_lock_解锁与上锁():
    clock, bus = _ctx()
    sm = LockSM("lock.door", bus, clock)
    sm.apply("unlock", {})
    assert sm.state == "unlocked"
    clock.advance(1)
    sm.apply("lock", {})
    assert sm.state == "locked"


def test_未知domain回退GenericSM按SERVICE_STATE翻状态仍发事件():
    clock, bus = _ctx()
    sm = create_sm("input_boolean.guest", bus, clock)

    assert isinstance(sm, GenericSM)
    sm.apply("turn_on", {})
    assert sm.state == "on"
    clock.advance(1)
    sm.apply("toggle", {})
    assert sm.state == "off"
    assert [e.new_state for e in bus.history()] == ["on", "off"]


def test_注册表覆盖七大domain():
    assert set(SM_REGISTRY) == {"light", "switch", "fan", "climate", "cover", "media_player", "lock"}
    assert all(issubclass(cls, DeviceSM) for cls in SM_REGISTRY.values())
