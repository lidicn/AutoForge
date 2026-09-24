"""HighFidelity —— 高仿真组装层：事件总线 + 设备状态机 + 动作延迟 + SSE 流。

对外实现 StateProvider / Adapter 协议，与 FakeHA / FakeHAAdapter 接口兼容（C4），
Runtime 侧零改动；默认关闭（C5）：``AUTOFORGE_VHASS_HIFI=1`` 或 ``create_ha(hifi=True)``。

额外补齐需求"核心痛点"里的 sun 缺口：``sun.sun`` 用真实太阳几何（默认深圳经纬度），
夏至日落 ≈19:11、冬至 ≈17:42，而不是 FakeHA 的固定 18:00/06:00。
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from ..af_adapters import CallResult
from ..af_adapters.base import FaultQueue
from ..af_ir import Graph
from ..af_state import Snapshot, UnknownEntity
from ..af_time import TimeSource
from .action_queue import ActionQueue
from .device_sm import SM_REGISTRY
from .event_bus import FakeEventBus, StateChangeEvent
from .fake import FakeHA, FakeHAAdapter, default_state_for, is_modeled, seed_from_graph
from .sse_stream import FakeSSEStream

# ---- 太阳几何（标准库实现，NOAA 简化日出日落方程）--------------------------------

DEFAULT_LATITUDE = 22.5431      # 深圳
DEFAULT_LONGITUDE = 114.0579
_SUN_ALTITUDE = -0.833          # 官方日出/日落：太阳中心在地平线下 0.833°
_OBLIQUITY = 23.44
_EPOCH_DAY = date(2000, 1, 1)


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _to_julian(moment: datetime) -> float:
    return _as_utc(moment).timestamp() / 86400.0 + 2440587.5


def _from_julian(julian: float) -> datetime:
    return datetime.fromtimestamp((julian - 2440587.5) * 86400.0, tz=timezone.utc)


def _solar_geometry(day: datetime, latitude: float, longitude: float) -> tuple[float, float, float]:
    """返回 (中天儒略日, 半日弧角度°, 赤纬 rad)。``day`` 的日历日视为目标日。"""
    n = (day.date() - _EPOCH_DAY).days
    mean_solar = n - longitude / 360.0
    anomaly = math.radians((357.5291 + 0.98560028 * mean_solar) % 360.0)
    center = (1.9148 * math.sin(anomaly) + 0.0200 * math.sin(2 * anomaly) + 0.0003 * math.sin(3 * anomaly))
    ecliptic = math.radians((math.degrees(anomaly) + center + 282.9372) % 360.0)
    declination = math.asin(math.sin(ecliptic) * math.sin(math.radians(_OBLIQUITY)))
    lat = math.radians(latitude)
    cos_omega = (math.sin(math.radians(_SUN_ALTITUDE)) - math.sin(lat) * math.sin(declination)) / (
        math.cos(lat) * math.cos(declination)
    )
    omega = math.degrees(math.acos(max(-1.0, min(1.0, cos_omega))))
    transit = 2451545.0 + mean_solar + 0.0053 * math.sin(anomaly) - 0.0069 * math.sin(2 * ecliptic)
    return transit, omega, declination


def solar_events(day: datetime, latitude: float = DEFAULT_LATITUDE, longitude: float = DEFAULT_LONGITUDE) -> tuple[datetime, datetime]:
    """``day`` 所在日历日的 (sunrise, sunset)，返回 UTC 时间。"""
    transit, omega, _ = _solar_geometry(day, latitude, longitude)
    return (_from_julian(transit - omega / 360.0), _from_julian(transit + omega / 360.0))


def sun_state_at(now: datetime, latitude: float = DEFAULT_LATITUDE, longitude: float = DEFAULT_LONGITUDE) -> str:
    rise, setting = solar_events(now, latitude, longitude)
    moment = _as_utc(now)
    return "above_horizon" if rise <= moment <= setting else "below_horizon"


def _elevation(moment: datetime, latitude: float, longitude: float, transit: float, declination: float) -> float:
    hour_angle = math.radians((_to_julian(moment) - transit) * 360.0)
    lat = math.radians(latitude)
    sin_alt = math.sin(lat) * math.sin(declination) + math.cos(lat) * math.cos(declination) * math.cos(hour_angle)
    return math.degrees(math.asin(max(-1.0, min(1.0, sin_alt))))


def sun_attributes(now: datetime, latitude: float = DEFAULT_LATITUDE, longitude: float = DEFAULT_LONGITUDE) -> dict[str, Any]:
    """HA 风格的 sun.sun 属性（需要时用 ``ha.set("sun.sun", ..., ha.sun_info())``）。"""
    moment = _as_utc(now)
    transit, omega, declination = _solar_geometry(now, latitude, longitude)
    rise, setting = _from_julian(transit - omega / 360.0), _from_julian(transit + omega / 360.0)
    if moment < rise:
        next_rising, next_setting = rise, setting
    elif moment < setting:
        next_rising, next_setting = solar_events(now + timedelta(days=1), latitude, longitude)[0], setting
    else:
        next_rising, next_setting = solar_events(now + timedelta(days=1), latitude, longitude)
    return {
        "elevation": round(_elevation(moment, latitude, longitude, transit, declination), 3),
        "rising": bool(rise <= moment <= _from_julian(transit)),
        "next_rising": next_rising.isoformat(),
        "next_setting": next_setting.isoformat(),
    }


# ---- 组装层 --------------------------------------------------------------------


@dataclass
class HighFidelityHA:
    """高仿真 HA：事件总线 + 设备状态机 + 动作延迟 + SSE 流（StateProvider）。"""

    clock: TimeSource
    bus: FakeEventBus = field(init=False)
    queue: ActionQueue = field(init=False)
    sse: FakeSSEStream = field(init=False)
    device_latency: float = 0.1
    heartbeat_interval: float = 30.0
    dedup: bool = True
    sun_events: bool = True
    latitude: float = DEFAULT_LATITUDE
    longitude: float = DEFAULT_LONGITUDE
    sun_override: str | None = None
    _states: dict[str, str] = field(default_factory=dict, init=False)
    _attributes: dict[str, dict] = field(default_factory=dict, init=False)
    _sun_state: str | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.bus = FakeEventBus(clock=self.clock, dedup=self.dedup)
        self.queue = ActionQueue(
            bus=self.bus, clock=self.clock,
            sm_registry=dict(SM_REGISTRY), device_latency=self.device_latency,
        )
        self.sse = FakeSSEStream(bus=self.bus, clock=self.clock, heartbeat_interval=self.heartbeat_interval)
        self.bus.subscribe(self._mirror)

    # ---- 事件镜像 -------------------------------------------------------------
    def _mirror(self, event: StateChangeEvent) -> None:
        self._states[event.entity_id] = event.new_state
        self._attributes[event.entity_id] = dict(event.new_attributes)

    @property
    def states(self) -> dict[str, str]:
        return self._states

    @property
    def attributes(self) -> dict[str, dict]:
        return self._attributes

    # ---- StateProvider --------------------------------------------------------
    def snapshot(self, entity_ids: Iterable[str]) -> Snapshot:
        """与 FakeHA.snapshot 行为一致：未知实体 raise UnknownEntity。"""
        wanted = list(entity_ids)
        values: dict[str, str] = {}
        for entity_id in wanted:
            if entity_id == "sun.sun":
                values[entity_id] = self._sun_value()
                continue
            if entity_id not in self._states:
                raise UnknownEntity(entity_id)
            values[entity_id] = self._states[entity_id]
        return Snapshot(
            values=values,
            attributes={entity_id: dict(self._attributes.get(entity_id, {})) for entity_id in wanted},
        )

    def get(self, entity_id: str) -> str | None:
        if entity_id == "sun.sun":
            return self._sun_value()
        return self._states.get(entity_id)

    def seed(self, states: Mapping[str, Any]) -> "HighFidelityHA":
        """播种初始状态：不发布事件（初始状态不是"变化"），并创建对应 DeviceSM。"""
        for key, value in states.items():
            entity_id = str(key)
            if isinstance(value, Mapping):
                payload = dict(value)
                state = payload.pop("state", None)
                extra = dict(payload.pop("attributes", {}) or {})
                extra.update(payload)
                self.set(entity_id, str(state) if state is not None else default_state_for(entity_id), extra or None)
            else:
                self.set(entity_id, str(value), None)
        return self

    def set(self, entity_id: str, state: str, attributes: Mapping[str, object] | None = None) -> "HighFidelityHA":
        """直接设置状态（测试构造场景）：不发布事件，同步状态机内部状态。"""
        self._states[entity_id] = str(state)
        if attributes is not None:
            self._attributes[entity_id] = dict(attributes)
        self.queue.get_sm(entity_id).reset(self._states[entity_id], self._attributes.get(entity_id))
        return self

    def materialize(self, entity_id: str) -> None:
        """把目标实体补进状态镜像（不发事件）：与 FakeHA.call 的"实体即建"语义对齐。"""
        if entity_id not in self._states:
            self._states[entity_id] = default_state_for(entity_id)
            self._attributes.setdefault(entity_id, {})
        self.queue.get_sm(entity_id)

    # ---- 时间推进 -------------------------------------------------------------
    def tick(self) -> int:
        """时间推进：同步太阳状态并执行到期动作。"""
        self._sync_sun()
        return self.queue.tick()

    def flush(self) -> int:
        """立即执行所有待执行动作（忽略排期）。"""
        self._sync_sun()
        return self.queue.flush_all()

    # ---- sun ------------------------------------------------------------------
    def _sun_value(self) -> str:
        if self.sun_override is not None:
            return self.sun_override
        return sun_state_at(self.clock.now(), self.latitude, self.longitude)

    def _sync_sun(self) -> None:
        if not self.sun_events:
            return
        current = self._sun_value()
        if self._sun_state is None:
            self._sun_state = current       # 首次只记录，不产生事件
            return
        if current == self._sun_state:
            return
        previous, self._sun_state = self._sun_state, current
        self.bus.publish("sun.sun", previous, current, {}, {})

    def sun_info(self) -> dict[str, Any]:
        return sun_attributes(self.clock.now(), self.latitude, self.longitude)


@dataclass
class HighFidelityAdapter:
    """高仿真适配器：call() 入队不立即执行；与 FakeHAAdapter 接口兼容。"""

    ha: HighFidelityHA
    name: str = "ha"

    def __post_init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.faults = FaultQueue()
        self.unmodeled: list[str] = []

    def call(self, action: str, params: Mapping[str, Any]) -> CallResult:
        self.calls.append((action, dict(params)))
        fault = self.faults.pop()                       # C8：故障优先于入队
        if fault is not None:
            kind, error = fault
            return CallResult.fail(error, fault=kind, action=action, params=dict(params))
        domain, _, service = action.partition(".")
        if not is_modeled(domain, service):
            self.unmodeled.append(action)
        for pending in self.ha.queue.call(action, params):
            self.ha.materialize(pending.entity_id)
        return CallResult.ok({"action": action, "params": dict(params), "queued": True})

    def fail_next(self, error: str = "fakeha 注入的失败") -> None:
        self.faults.push("fail", error)

    def timeout_next(self, error: str = "fakeha 注入的传输层超时") -> None:
        self.faults.push("timeout", error)

    def drop_next(self, error: str = "fakeha 注入的消息丢包") -> None:
        self.faults.push("drop", error)

    def unavailable_next(self, error: str = "fakeha 注入的实体不可用") -> None:
        self.faults.push("unavailable", error)


# ---- 可切换工厂（C5：默认关闭，不影响现有测试）------------------------------------


def hifi_enabled(env: Mapping[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    return str(source.get("AUTOFORGE_VHASS_HIFI", "")).strip().lower() in {"1", "true", "yes", "on"}


def create_ha(clock: TimeSource | None = None, *, hifi: bool | None = None, **kwargs: Any) -> FakeHA | HighFidelityHA:
    """按开关选择 FakeHA / HighFidelityHA（hifi=None 时看 AUTOFORGE_VHASS_HIFI）。"""
    use_hifi = hifi_enabled() if hifi is None else bool(hifi)
    if not use_hifi:
        return FakeHA(clock=clock)
    if clock is None:
        raise ValueError("高仿真模式必须注入 TimeSource（约束 C6）")
    return HighFidelityHA(clock=clock, **kwargs)


def create_adapter(ha: FakeHA | HighFidelityHA, name: str = "ha") -> FakeHAAdapter | HighFidelityAdapter:
    if isinstance(ha, HighFidelityHA):
        return HighFidelityAdapter(ha=ha, name=name)
    return FakeHAAdapter(states=ha, name=name)


def create_ha_from_graph(
    graph: Graph,
    overrides: Mapping[str, Any] | None = None,
    clock: TimeSource | None = None,
    *,
    hifi: bool | None = None,
    **kwargs: Any,
) -> FakeHA | HighFidelityHA:
    """与 fake.seed_from_graph 同构：按 IR 图覆盖实体建初始状态，按开关选择实现。"""
    use_hifi = hifi_enabled() if hifi is None else bool(hifi)
    if not use_hifi:
        return seed_from_graph(graph, overrides, clock=clock)
    ha = HighFidelityHA(clock=clock, **kwargs) if clock is not None else create_ha(clock, hifi=True, **kwargs)
    covered: dict[str, str] = {}
    for auto in graph:
        for entity_id in sorted(auto.reads() | auto.writes() | auto.expect_entities()):
            covered.setdefault(entity_id, default_state_for(entity_id))
    ha.seed(covered)
    if overrides:
        ha.seed(overrides)
    return ha
