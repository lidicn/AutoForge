"""FakeHA —— 降级用的内存 HA（**不是**首选，只是不阻塞 G1 的兜底）。

只在 `AUTOFORGE_VHASS=fake` 或装不上 `pytest-homeassistant` 时使用。
接口与 vhass 桥接层完全一致：`snapshot(entity_ids) -> Snapshot`。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping

from ..af_adapters import CallResult
from ..af_adapters.base import FaultQueue
from ..af_ir import Graph
from ..af_state import Snapshot, StateProvider

__all__ = ["FakeHA", "FakeHAAdapter", "SERVICE_STATE", "default_state_for", "seed_from_graph"]

#: 按 domain 推导默认状态（口径参考 HA 常见初值）
_DOMAIN_DEFAULT: dict[str, str] = {
    "light": "off",
    "switch": "off",
    "fan": "off",
    "input_boolean": "off",
    "cover": "closed",
    "lock": "locked",
    "climate": "off",
    "sensor": "0",
    "binary_sensor": "off",
    "device_tracker": "not_home",
    "media_player": "off",
    "vacuum": "docked",
    "alarm_control_panel": "disarmed",
    "valve": "closed",
    "water_heater": "off",
    "number": "0",
    "select": "unknown",
    "sun": "above_horizon",
}

#: FakeHA 的太阳历桩：日落 18:00 → 次日 06:00 视为 below_horizon
SUNSET_HOUR = 18
SUNRISE_HOUR = 6


def default_state_for(entity_id: str) -> str:
    domain = entity_id.split(".", 1)[0] if "." in entity_id else ""
    return _DOMAIN_DEFAULT.get(domain, "unknown")


def sun_state(now: datetime) -> str:
    """本地太阳历桩。真 vhass 用 HA 原生 `sun` 组件，不走这里。"""
    return "below_horizon" if (now.hour >= SUNSET_HOUR or now.hour < SUNRISE_HOUR) else "above_horizon"


@dataclass
class FakeHA:
    """内存 HA：可播种、可断言。"""

    states: dict[str, str] = field(default_factory=dict)
    attributes: dict[str, dict[str, object]] = field(default_factory=dict)
    clock: object | None = None  # 可选的 VirtualTimeSource，用于驱动 sun 桩

    # ── StateProvider ────────────────────────────────────────────────
    def snapshot(self, entity_ids: Iterable[str]) -> Snapshot:
        wanted = list(entity_ids)
        values = {}
        for entity_id in wanted:
            if entity_id == "sun.sun" and self.clock is not None:
                values[entity_id] = sun_state(self.clock.now())  # type: ignore[attr-defined]
                continue
            values[entity_id] = self.states.get(entity_id, default_state_for(entity_id))
        return Snapshot(
            values=values,
            attributes={e: dict(self.attributes.get(e, {})) for e in wanted},
        )

    # ── 场景构造 ─────────────────────────────────────────────────────
    def seed(self, states: Mapping[str, str]) -> "FakeHA":
        self.states.update({k: str(v) for k, v in states.items()})
        return self

    def set(self, entity_id: str, state: str, attributes: Mapping[str, object] | None = None) -> "FakeHA":
        self.states[entity_id] = str(state)
        if attributes is not None:
            self.attributes[entity_id] = dict(attributes)
        return self

    def get(self, entity_id: str) -> str | None:
        if entity_id == "sun.sun" and self.clock is not None:
            return sun_state(self.clock.now())  # type: ignore[attr-defined]
        return self.states.get(entity_id)


#: (domain, service) → 新状态（与 HA 常见服务语义对齐）
#: vhass 与 FakeHA **共用这张表**，保证两条仿真路径的动作语义完全一致
SERVICE_STATE: dict[tuple[str, str], str] = {
    ("light", "turn_on"): "on",
    ("light", "turn_off"): "off",
    ("switch", "turn_on"): "on",
    ("switch", "turn_off"): "off",
    ("fan", "turn_on"): "on",
    ("fan", "turn_off"): "off",
    ("input_boolean", "turn_on"): "on",
    ("input_boolean", "turn_off"): "off",
    ("climate", "turn_on"): "cool",
    ("climate", "turn_off"): "off",
    ("media_player", "turn_on"): "playing",
    ("media_player", "turn_off"): "off",
    ("lock", "lock"): "locked",
    ("lock", "unlock"): "unlocked",
    ("cover", "open_cover"): "open",
    ("cover", "close_cover"): "closed",
}


@dataclass
class FakeHAAdapter:
    """绑定到 FakeHA 的 HA 适配器（仿真里的"设备真的会动"）。

    与生产 `HAAdapter` 实现同一个 `Adapter` 协议：单次下发，无重试无降级。
    """

    states: FakeHA
    name: str = "ha"

    def __post_init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.faults = FaultQueue()
        #: v1.2.0 诚实性：FakeHA 未建模的动作（翻转不了状态）→ 其后果**无法验证**，
        #: 登记于此（而非静默跳过），让 `expect` 断言不被"根本没执行"骗过。
        self.unmodeled: list[str] = []

    @staticmethod
    def _modeled(domain: str, service: str) -> bool:
        """该 (domain, service) 是否能在 FakeHA 里翻出状态。"""
        return (domain, service) in SERVICE_STATE or (domain == "climate" and service == "set_temperature")

    # ── 故障注入（G5，IR §9.5）────────────────────────────────────────
    def fail_next(self, error: str = "fakeha 注入的失败") -> None:
        self.faults.push("fail", error)

    def timeout_next(self, error: str = "fakeha 注入的传输层超时") -> None:
        self.faults.push("timeout", error)

    def drop_next(self, error: str = "fakeha 注入的消息丢包") -> None:
        self.faults.push("drop", error)

    def unavailable_next(self, error: str = "fakeha 注入的实体不可用") -> None:
        self.faults.push("unavailable", error)

    def call(self, action: str, params: Mapping[str, object]) -> CallResult:
        self.calls.append((action, dict(params)))
        fault = self.faults.pop()
        if fault is not None:
            kind, error = fault
            return CallResult.fail(error, fault=kind, action=action, params=dict(params))
        domain, _, service = action.partition(".")
        targets = params.get("entity_id")
        if isinstance(targets, str):
            targets = [targets]
        modeled = self._modeled(domain, service)
        if not modeled:
            # 未建模：**不留假状态**，只登记 → 上层据此把相关断言视为"未验证"
            self.unmodeled.append(action)
        for entity_id in targets or []:
            new_state = SERVICE_STATE.get((domain, service))
            if new_state is not None:
                self.states.set(str(entity_id), new_state)
            elif domain == "climate" and service == "set_temperature":
                self.states.set(str(entity_id), "cool", {"temperature": params.get("temperature")})
        data: dict[str, Any] = {"action": action, "params": dict(params)}
        if not modeled:
            data["unmodeled"] = True
        return CallResult.ok(data)


def seed_from_graph(graph: Graph, overrides: Mapping[str, str] | None = None, clock: object | None = None) -> FakeHA:
    """按 Graph 引用的实体播种默认状态，便于快速起一个可跑的场景。"""
    ha = FakeHA(clock=clock)
    for auto in graph:
        # v1.2.0：`expect` 里断言的实体也必须播种，否则断言会被判「无法验证」而非「验过」
        for entity_id in sorted(auto.reads() | auto.writes() | auto.expect_entities()):
            ha.states.setdefault(entity_id, default_state_for(entity_id))
    if overrides:
        ha.seed(overrides)
    return ha
