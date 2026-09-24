"""DeviceSM —— 设备状态机，替代 FakeHA 的静态 SERVICE_STATE 表。

真机对齐要点：

* ``climate``：state = hvac_mode，实时动作在 ``attributes.hvac_action``
  （off/heating/cooling/idle），并随 ``_settle`` 收敛到 idle；
* ``cover``：必须经过 opening/closing 中间状态，行程结束（``_arrive``）才落位；
* ``light``：``transition`` 渐变期间状态不变，渐变结束（``_finish_*``）才落位；
* 一切状态变化都经 ``_transition()`` → ``FakeEventBus.publish()``（C7）；
* SM 不持有 FakeHA 的 states dict，内部 ``state/attributes`` 由 HighFidelityHA 镜像成快照。

SM 通过 out-box ``followups`` 申请延时后续动作（保持 ``apply() -> None`` 契约），
由 ActionQueue 排期执行。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping

from ..af_time import TimeSource
from .event_bus import FakeEventBus
from .fake import default_state_for, service_effect


def _to_int(value: Any, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class FollowUp:
    """状态机申请的延时后续动作（由 ActionQueue 排期执行）。"""

    delay: float
    service: str
    params: dict[str, Any] = field(default_factory=dict)


class DeviceSM(ABC):
    """设备状态机基类。"""

    domain: str = ""

    def __init__(
        self,
        entity_id: str,
        bus: FakeEventBus,
        clock: TimeSource,
        *,
        initial_state: str | None = None,
        initial_attributes: Mapping[str, Any] | None = None,
    ) -> None:
        self.entity_id = entity_id
        self.bus = bus
        self.clock = clock
        self.state: str = str(initial_state) if initial_state is not None else default_state_for(entity_id)
        self.attributes: dict[str, Any] = dict(initial_attributes or {})
        self.followups: list[FollowUp] = []

    @abstractmethod
    def apply(self, service: str, params: Mapping[str, Any]) -> None:
        """应用服务调用：执行状态转移并经 bus 发布事件。"""

    # ---- 延时后续动作 out-box -------------------------------------------------
    def drain_followups(self) -> list[FollowUp]:
        pending = list(self.followups)
        self.followups.clear()
        return pending

    def _defer(self, delay: float, service: str, params: Mapping[str, Any] | None = None) -> None:
        self.followups.append(FollowUp(delay=float(delay), service=service, params=dict(params or {})))

    # ---- 状态维护 -------------------------------------------------------------
    def reset(self, state: str, attributes: Mapping[str, Any] | None = None) -> None:
        """无事件覆写内部状态（seed/set 场景：初始状态不算"变化"）。"""
        self.state = str(state)
        if attributes is not None:
            self.attributes = dict(attributes)

    def _transition(self, new_state: str, new_attributes: dict[str, Any] | None = None) -> None:
        """状态转移辅助：更新内部状态并发布事件（唯一出口）。"""
        old_state = self.state
        old_attrs = dict(self.attributes)
        if new_attributes is not None:
            self.attributes.update(new_attributes)
        self.state = str(new_state)
        self.bus.publish(self.entity_id, old_state, self.state, old_attrs, dict(self.attributes))

    def _fallback(self, service: str, params: Mapping[str, Any]) -> None:
        """未建模服务：退回 SERVICE_STATE / service_effect 表（GenericSM 同款语义）。"""
        domain = self.domain or self._entity_domain()
        new_state, attrs = service_effect(domain, service, params, self.state)
        if new_state is None and not attrs:
            return
        self._transition(new_state if new_state is not None else self.state, attrs or None)

    def _entity_domain(self) -> str:
        return self.entity_id.split(".", 1)[0] if "." in self.entity_id else self.domain


class LightSM(DeviceSM):
    """灯：off/on，brightness(0-255)、transition(渐变秒数)。"""

    domain = "light"

    def apply(self, service: str, params: Mapping[str, Any]) -> None:
        if service == "_finish_turn_on":
            self._transition("on", dict(params.get("attributes") or {}))
            return
        if service == "_finish_turn_off":
            self._transition("off", dict(params.get("attributes") or {}))
            return
        if service == "turn_on":
            attrs: dict[str, Any] = {}
            brightness = _to_int(params.get("brightness"))
            if brightness is not None:
                attrs["brightness"] = max(0, min(255, brightness))
            elif "brightness" not in self.attributes:
                attrs["brightness"] = 255
            delay = max(0.0, _to_float(params.get("transition"), 0.0) or 0.0)
            if delay > 0:
                # 渐变：状态保持不动，渐变结束才落位（真机亮度爬升）
                self._defer(delay, "_finish_turn_on", {"attributes": attrs})
                return
            self._transition("on", attrs)
            return
        if service == "turn_off":
            delay = max(0.0, _to_float(params.get("transition"), 0.0) or 0.0)
            if delay > 0:
                self._defer(delay, "_finish_turn_off", {"attributes": {}})
                return
            self._transition("off")
            return
        if service == "toggle":
            self.apply("turn_off" if self.state == "on" else "turn_on", params)
            return
        self._fallback(service, params)


class ClimateSM(DeviceSM):
    """空调：state 是 hvac_mode，实时动作在 attributes.hvac_action。"""

    domain = "climate"
    settle_seconds: float = 5.0   # hvac_action 从 active 收敛一步的周期
    temp_step: float = 1.0        # 每个周期 current_temperature 向目标靠拢的步长

    _MODE_ACTION = {
        "cool": "cooling", "heat": "heating", "dry": "drying",
        "fan_only": "fan", "auto": "cooling", "heat_cool": "heating",
    }

    def _action_for(self, mode: str) -> str:
        if mode == "off":
            return "off"
        if mode in ("auto", "heat_cool"):
            target = _to_float(self.attributes.get("temperature"))
            current = _to_float(self.attributes.get("current_temperature"))
            if target is not None and current is not None:
                return "heating" if current < target else "cooling"
            return "cooling"
        return self._MODE_ACTION.get(mode, "idle")

    def _enter_mode(self, mode: str) -> None:
        mode = str(mode)
        self._transition(mode, {"hvac_action": self._action_for(mode)})
        self._arm_settle()

    def _arm_settle(self) -> None:
        if self.attributes.get("hvac_action") in ("cooling", "heating"):
            self._defer(self.settle_seconds, "_settle")

    def _settle(self) -> None:
        """温度逼近目标一步；到目标后 hvac_action: cooling/heating → idle。"""
        action = str(self.attributes.get("hvac_action") or "")
        if self.state == "off" or action not in ("cooling", "heating"):
            return
        target = _to_float(self.attributes.get("temperature"))
        current = _to_float(self.attributes.get("current_temperature"))
        updates: dict[str, Any] = {}
        if target is not None and current is not None:
            delta = target - current
            if abs(delta) <= self.temp_step:
                updates["current_temperature"] = target
                updates["hvac_action"] = "idle"
            else:
                updates["current_temperature"] = current + (self.temp_step if delta > 0 else -self.temp_step)
                self._defer(self.settle_seconds, "_settle")
        else:
            updates["hvac_action"] = "idle"
        self._transition(self.state, updates)

    def apply(self, service: str, params: Mapping[str, Any]) -> None:
        if service == "_settle":
            self._settle()
            return
        if service == "turn_on":
            mode = params.get("hvac_mode") or params.get("mode") or "cool"
            self._enter_mode(str(mode))
            return
        if service == "turn_off":
            self._transition("off", {"hvac_action": "off"})
            return
        if service == "set_hvac_mode":
            mode = params.get("hvac_mode") or params.get("state")
            if mode is None:
                self._fallback(service, params)
                return
            if str(mode) == "off":
                self._transition("off", {"hvac_action": "off"})
                return
            self._enter_mode(str(mode))
            return
        if service == "set_temperature":
            target = _to_float(params.get("temperature", params.get("target_temp")))
            if target is None:
                return
            updates: dict[str, Any] = {"temperature": target}
            if self.state != "off":
                updates["hvac_action"] = self._action_for(self.state)
            self._transition(self.state, updates)   # state 不变，仅属性变（仍发事件）
            self._arm_settle()
            return
        if service == "set_fan_mode":
            self._transition(self.state, {"fan_mode": params.get("fan_mode")})
            return
        if service == "set_preset_mode":
            self._transition(self.state, {"preset_mode": params.get("preset_mode")})
            return
        self._fallback(service, params)


class CoverSM(DeviceSM):
    """窗帘：closed/opening/open/closing/stopped + current_position(0-100)。"""

    domain = "cover"
    move_duration: float = 5.0    # 行程时长（可用 params["move_duration"] 覆盖）

    def _position(self) -> int:
        raw = _to_int(self.attributes.get("current_position"))
        if raw is None:
            return 0 if self.state == "closed" else 100
        return max(0, min(100, raw))

    def _duration(self, params: Mapping[str, Any]) -> float:
        value = _to_float(params.get("move_duration"), float(self.move_duration))
        return max(0.0, value if value is not None else float(self.move_duration))

    def _start_move(self, direction: str, target: int, params: Mapping[str, Any]) -> None:
        self._transition(direction)                                  # 中间状态立即可见
        self._defer(self._duration(params), "_arrive", {"position": int(target)})

    def apply(self, service: str, params: Mapping[str, Any]) -> None:
        if service == "_arrive":
            target = max(0, min(100, _to_int(params.get("position"), 100) or 0))
            self._transition("closed" if target <= 0 else "open", {"current_position": target})
            return
        if service == "open_cover":
            if self.state == "open" and self._position() >= 100:
                return
            self._start_move("opening", 100, params)
            return
        if service == "close_cover":
            if self.state == "closed" and self._position() <= 0:
                return
            self._start_move("closing", 0, params)
            return
        if service == "stop_cover":
            self._transition("stopped")
            return
        if service == "set_cover_position":
            raw = _to_int(params.get("position"))
            if raw is None:
                return
            target = max(0, min(100, raw))
            current = self._position()
            # 移动中：逻辑位置是移动目标（opening→100, closing→0），
            # 否则反向指令会被误判为"已到达"
            if self.state == "opening":
                current = 100
            elif self.state == "closing":
                current = 0
            if target == current:
                self._transition("closed" if target <= 0 else "open", {"current_position": target})
                return
            self._start_move("opening" if target > current else "closing", target, params)
            return
        self._fallback(service, params)


class SwitchSM(DeviceSM):
    """开关：off/on（简单二元，但仍走事件总线）。"""

    domain = "switch"

    def apply(self, service: str, params: Mapping[str, Any]) -> None:
        if service == "turn_on":
            self._transition("on")
            return
        if service == "turn_off":
            self._transition("off")
            return
        if service == "toggle":
            self._transition("off" if self.state == "on" else "on")
            return
        self._fallback(service, params)


class FanSM(DeviceSM):
    """风扇：off/on + percentage(0-100)。"""

    domain = "fan"

    def apply(self, service: str, params: Mapping[str, Any]) -> None:
        if service == "turn_on":
            attrs: dict[str, Any] = {}
            percentage = _to_int(params.get("percentage"))
            if percentage is not None:
                attrs["percentage"] = max(0, min(100, percentage))
            self._transition("on", attrs)
            return
        if service == "turn_off":
            self._transition("off")
            return
        if service == "toggle":
            self._transition("off" if self.state == "on" else "on")
            return
        if service == "set_percentage":
            percentage = _to_int(params.get("percentage"))
            if percentage is None:
                return
            percentage = max(0, min(100, percentage))
            self._transition("off" if percentage == 0 else "on", {"percentage": percentage})
            return
        self._fallback(service, params)


class MediaPlayerSM(DeviceSM):
    """媒体播放器：off/playing/paused/idle。"""

    domain = "media_player"

    _SERVICE_STATE = {
        "turn_on": "playing", "turn_off": "off", "media_play": "playing",
        "media_pause": "paused", "media_stop": "idle", "play_media": "playing",
    }

    def apply(self, service: str, params: Mapping[str, Any]) -> None:
        if service in self._SERVICE_STATE:
            attrs: dict[str, Any] = {}
            if service == "play_media" and params.get("media_content_id") is not None:
                attrs["media_content_id"] = params.get("media_content_id")
            self._transition(self._SERVICE_STATE[service], attrs)
            return
        if service == "volume_set":
            volume = _to_float(params.get("volume_level"))
            if volume is None:
                return
            self._transition(self.state, {"volume_level": max(0.0, min(1.0, volume))})
            return
        if service == "volume_mute":
            self._transition(self.state, {"is_volume_muted": bool(params.get("is_volume_muted"))})
            return
        self._fallback(service, params)


class LockSM(DeviceSM):
    """门锁：locked/unlocked。"""

    domain = "lock"

    def apply(self, service: str, params: Mapping[str, Any]) -> None:
        if service == "lock":
            self._transition("locked")
            return
        if service == "unlock":
            self._transition("unlocked")
            return
        self._fallback(service, params)


class GenericSM(DeviceSM):
    """未建模 domain 的回退：按 SERVICE_STATE 表翻状态，但仍发事件。"""

    domain = ""

    def apply(self, service: str, params: Mapping[str, Any]) -> None:
        self._fallback(service, params)


# 注册表：domain → SM 类
SM_REGISTRY: dict[str, type[DeviceSM]] = {
    "light": LightSM, "switch": SwitchSM, "fan": FanSM,
    "climate": ClimateSM, "cover": CoverSM, "media_player": MediaPlayerSM, "lock": LockSM,
}


def create_sm(
    entity_id: str,
    bus: FakeEventBus,
    clock: TimeSource,
    *,
    initial_state: str | None = None,
    initial_attributes: Mapping[str, Any] | None = None,
) -> DeviceSM:
    """按 entity_id 的 domain 创建状态机；未知 domain 回退 GenericSM。"""
    domain = entity_id.split(".", 1)[0] if "." in entity_id else ""
    cls = SM_REGISTRY.get(domain, GenericSM)
    return cls(entity_id, bus, clock, initial_state=initial_state, initial_attributes=initial_attributes)
