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

__all__ = [
    "FakeHA",
    "FakeHAAdapter",
    "SERVICE_STATE",
    "DYNAMIC_SERVICES",
    "default_state_for",
    "is_modeled",
    "seed_from_graph",
    "service_effect",
]

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
        # P1-12：对未知实体 raise UnknownEntity（fail-closed，不编造默认状态）
        from ..af_state import UnknownEntity
        wanted = list(entity_ids)
        values = {}
        for entity_id in wanted:
            if entity_id == "sun.sun" and self.clock is not None:
                values[entity_id] = sun_state(self.clock.now())  # type: ignore[attr-defined]
                continue
            if entity_id not in self.states:
                raise UnknownEntity(entity_id)
            values[entity_id] = self.states[entity_id]
        return Snapshot(
            values=values,
            attributes={e: dict(self.attributes.get(e, {})) for e in wanted},
        )

    # ── 场景构造 ─────────────────────────────────────────────────────
    def seed(self, states: Mapping[str, Any]) -> "FakeHA":
        """播种初始状态。两种写法都支持：

        ```jsonc
        {"light.study": "off"}                                      // 只给状态
        {"climate.study_ac": {"state": "cool",                       // 状态 + 属性
                              "attributes": {"temperature": 26}}}
        ```

        v1.7.1：支持属性是修 NL 实测 #3 的 `entity_drift` 误报——原实现只能播种 state，
        `climate` 实体没有 `temperature` 属性，导致 `entity.…temperature` 声明为 numeric
        却读到字符串 `'off'`，仿真里报「实体漂移」把 seed 缺口伪装成 IR 错误。
        """
        for key, value in states.items():
            entity_id = str(key)
            if isinstance(value, Mapping):
                payload = dict(value)
                state = payload.pop("state", None)
                extra = dict(payload.pop("attributes", {}) or {})
                extra.update(payload)  # 顶层其余键一并当作属性（宽松写法）
                self.set(
                    entity_id,
                    str(state) if state is not None else default_state_for(entity_id),
                    extra or None,
                )
            else:
                self.states[entity_id] = str(value)
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
    # ── v1.7.1：补齐 NL 实测暴露的高频缺口（原先 media_pause 被判 unmodeled，
    #    导致「夜间电视暂停」这类 IR 正确却无法在仿真里验证状态翻转）─────────
    ("media_player", "media_pause"): "paused",
    ("media_player", "media_play"): "playing",
    ("media_player", "media_stop"): "idle",
    ("media_player", "play_media"): "playing",
    ("lock", "lock"): "locked",
    ("lock", "unlock"): "unlocked",
    ("cover", "open_cover"): "open",
    ("cover", "close_cover"): "closed",
    ("cover", "stop_cover"): "stopped",
    # ── F1 P1：决策 A 仿真补域（场景/脚本间接触发 + 通知副作用域）─────────────
    ("scene", "turn_on"): "on",           # scene 触发：FakeHA 标记 active，不展开到 light 调用
    ("script", "turn_on"): "on",          # script 运行：FakeHA 标记 active，不展开到真实脚本链
    ("script", "turn_off"): "off",        # script 停止
    ("notify", "notify"): "on",           # notify.notify：副作用不可观测（发手机），
    ("persistent_notification", "create"): "on",  #    但 FakeHA 给一个"已触发"状态让仿真不跳过
    # ── F1 P2/P3：高频设备域补全（vacuum 扫地机 / valve 阀门 / water_heater 热水器）─────
    ("vacuum", "start"): "cleaning",
    ("vacuum", "pause"): "paused",
    ("vacuum", "stop"): "idle",
    ("vacuum", "return_to_base"): "docked",
    ("vacuum", "locate"): "on",
    ("valve", "open_valve"): "open",
    ("valve", "close_valve"): "closed",
    ("water_heater", "turn_on"): "on",
    ("water_heater", "turn_off"): "off",
}

#: 已建模、但**新状态由参数或当前状态决定**的服务（不能在上表里给死值）。
DYNAMIC_SERVICES: frozenset[tuple[str, str]] = frozenset(
    {
        ("climate", "set_temperature"),
        ("climate", "set_hvac_mode"),
        ("climate", "set_fan_mode"),
        ("climate", "set_preset_mode"),
        ("media_player", "volume_set"),
        ("media_player", "volume_mute"),
        ("light", "toggle"),
        ("switch", "toggle"),
        ("fan", "toggle"),
        ("input_boolean", "toggle"),
        ("water_heater", "set_temperature"),
    }
)


def is_modeled(domain: str, service: str) -> bool:
    """该 `(domain, service)` 是否能在仿真里产生**可观测效果**。

    vhass 与 FakeHA 共用此判据，避免两条仿真路径对「未建模」的判断不一致。
    """
    return (domain, service) in SERVICE_STATE or (domain, service) in DYNAMIC_SERVICES


def service_effect(
    domain: str,
    service: str,
    params: Mapping[str, Any],
    current: str | None,
) -> tuple[str | None, dict[str, Any]]:
    """计算一次服务调用的效果：`(新状态 | None=状态不变, 属性更新)`。

    vhass 与 FakeHA **共用**此函数，保证两条仿真路径动作语义一致。
    """
    if (domain, service) == ("climate", "set_temperature"):
        # 与 HiFi ClimateSM 对齐：设定温度不改变开关机状态（off 保持 off，cool 保持 cool），
        # 仅写 temperature 属性。此前强制返回 "cool" 与高仿真底座不一致，是双轨对拍暴露的真实漂移源。
        return current, {"temperature": params.get("temperature")}
    if (domain, service) == ("climate", "set_hvac_mode"):
        mode = params.get("hvac_mode") or params.get("state")
        return (str(mode) if mode else current), {}
    if (domain, service) == ("climate", "set_fan_mode"):
        return current, {"fan_mode": params.get("fan_mode")}
    if (domain, service) == ("climate", "set_preset_mode"):
        return current, {"preset_mode": params.get("preset_mode")}
    if (domain, service) == ("media_player", "volume_set"):
        return current, {"volume_level": params.get("volume_level")}
    if (domain, service) == ("media_player", "volume_mute"):
        return current, {"is_volume_muted": params.get("is_volume_muted")}
    # ── F1 P1：notify 域副作用不可观测（发手机/推送），
    #    状态保持不变（None），但把 message/title 写进 attributes，
    #    与 af_shadow 的 EXEMPT verdict 路径对齐（notify 已在 shadow exempt_actions 里）
    if domain in ("notify", "persistent_notification"):
        attrs = {k: v for k, v in params.items() if k in ("message", "title", "entity_id")}
        return None, attrs
    if service == "toggle":
        return ("off" if current == "on" else "on"), {}
    return SERVICE_STATE.get((domain, service)), {}


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
        """该 (domain, service) 是否能在 FakeHA 里翻出状态（与 vhass 共用同一判据）。"""
        return is_modeled(domain, service)

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
            key = str(entity_id)
            new_state, attrs = service_effect(domain, service, params, self.states.get(key))
            if new_state is None and not attrs:
                continue  # 已建模但状态/属性均无变化（如 set_fan_mode 缺参）
            self.states.set(
                key,
                new_state if new_state is not None else (self.states.get(key) or default_state_for(key)),
                attrs or None,
            )
        data: dict[str, Any] = {"action": action, "params": dict(params)}
        if not modeled:
            data["unmodeled"] = True
        return CallResult.ok(data)


def seed_from_graph(graph: Graph, overrides: Mapping[str, Any] | None = None, clock: object | None = None) -> FakeHA:
    """按 Graph 引用的实体播种默认状态，便于快速起一个可跑的场景。

    `overrides` 支持 v1.7.1 的扩展写法（状态 + 属性），见 `FakeHA.seed`。
    """
    ha = FakeHA(clock=clock)
    for auto in graph:
        # v1.2.0：`expect` 里断言的实体也必须播种，否则断言会被判「无法验证」而非「验过」
        for entity_id in sorted(auto.reads() | auto.writes() | auto.expect_entities()):
            ha.states.setdefault(entity_id, default_state_for(entity_id))
    if overrides:
        ha.seed(overrides)
    return ha
