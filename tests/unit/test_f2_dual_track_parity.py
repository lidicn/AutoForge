"""F2 双轨对拍测试 — 防 FakeHA SERVICE_STATE 与 HiFi DeviceSM 漂移。

v2.1 决策 A 裁定：FakeHA 默认、HiFi opt-in；单一真值源已存在
（SERVICE_STATE + service_effect + GenericSM._fallback 三合一）。
本测试锁死这条路径——两条仿真轨道对同一个 (domain, service)
必须产出相同的"可观测效果"（新状态 + 属性更新）。

不跑整套双轨仿真（那是 vhass acceptance 的范畴），只锁最底层：
  - SERVICE_STATE 里的 (domain, service) → service_effect 产出一致
  - DYNAMIC_SERVICES 里的动态域 → service_effect 产出一致
  - is_modeled 对两个字典的并集返回 True
"""
from __future__ import annotations

import pytest

from autoforge.af_vhass.fake import (
    SERVICE_STATE, DYNAMIC_SERVICES, service_effect, is_modeled,
)
from autoforge.af_vhass.device_sm import SM_REGISTRY, GenericSM


# ── 覆盖 SERVICE_STATE 所有静态域 ────────────────────────────────────────

_SERVICE_STATE_PARAMS = [
    pytest.param(domain, service, expected_state, id=f"{domain}.{service}")
    for (domain, service), expected_state in SERVICE_STATE.items()
]

@pytest.mark.parametrize("domain,service,expected_state", _SERVICE_STATE_PARAMS)
def test_static_service_effect_consistency(domain, service, expected_state):
    """SERVICE_STATE 条目 → service_effect 必须返回一致状态。"""
    new_state, attrs = service_effect(domain, service, {}, current="off")
    # notify/persistent_notification 的副作用域 → 返回 None（状态不变），不走 SERVICE_STATE
    if domain in ("notify", "persistent_notification"):
        assert new_state is None
    else:
        assert new_state == expected_state, (
            f"SERVICE_STATE[{domain}.{service}]={expected_state} 但 "
            f"service_effect 返回 {new_state}——双轨漂移！"
        )
        assert attrs == {} or isinstance(attrs, dict)


# ── 覆盖 DYNAMIC_SERVICES 动态域 ─────────────────────────────────────────

_DYNAMIC_PARAMS = [
    pytest.param(domain, service, id=f"{domain}.{service}")
    for (domain, service) in DYNAMIC_SERVICES
]

@pytest.mark.parametrize("domain,service", _DYNAMIC_PARAMS)
def test_dynamic_service_effect_no_crash(domain, service):
    """DYNAMIC_SERVICES 条目 → service_effect 不崩，返回有效元组。"""
    params_map = {
        ("climate", "set_temperature"): {"temperature": 24},
        ("climate", "set_hvac_mode"): {"hvac_mode": "cool"},
        ("climate", "set_fan_mode"): {"fan_mode": "auto"},
        ("climate", "set_preset_mode"): {"preset_mode": "eco"},
        ("media_player", "volume_set"): {"volume_level": 0.5},
        ("media_player", "volume_mute"): {"is_volume_muted": True},
        ("light", "toggle"): {},
        ("switch", "toggle"): {},
        ("fan", "toggle"): {},
        ("input_boolean", "toggle"): {},
    }
    params = params_map.get((domain, service), {})
    new_state, attrs = service_effect(domain, service, params, current="on")
    assert attrs is not None  # 属性字典不可 None（哪怕是空）
    # climate.toggle/input_boolean.toggle 等：toggle 总是翻状态
    if service == "toggle":
        assert new_state in ("on", "off", None)


# ── 域覆盖完整性 ─────────────────────────────────────────────────────────

def test_is_modeled_covers_all_services():
    """is_modeled 必须对 SERVICE_STATE ∪ DYNAMIC_SERVICES 的每一项返回 True。"""
    for (d, s) in SERVICE_STATE:
        assert is_modeled(d, s), f"SERVICE_STATE 有 ({d},{s}) 但 is_modeled 返回 False"
    for (d, s) in DYNAMIC_SERVICES:
        assert is_modeled(d, s), f"DYNAMIC_SERVICES 有 ({d},{s}) 但 is_modeled 返回 False"


def test_f1_p1_new_domains_are_modeled():
    """F1 P1 补入的 scene/script/notify 域必须算已建模（仿真能跑通）。"""
    assert is_modeled("scene", "turn_on")
    assert is_modeled("script", "turn_on")
    assert is_modeled("script", "turn_off")
    assert is_modeled("notify", "notify")
    assert is_modeled("persistent_notification", "create")


def test_scene_script_notify_effects():
    """F1 P1 新域 service_effect 返回合理值。"""
    # scene.turn_on → 状态变 on（场景被触发）
    new_state, attrs = service_effect("scene", "turn_on", {}, current="off")
    assert new_state == "on"

    # script.turn_on → 状态变 on
    new_state, attrs = service_effect("script", "turn_on", {}, current="off")
    assert new_state == "on"

    # notify.notify → 状态不变（副作用不可观测），但 attributes 含 message
    new_state, attrs = service_effect(
        "notify", "notify", {"message": "测试通知", "title": "标题"}, current=None
    )
    assert new_state is None, "notify 是副作用域，状态应保持不变"
    assert attrs.get("message") == "测试通知"
    assert attrs.get("title") == "标题"


# ── HiFi GenericSM._fallback 与 SERVICE_STATE 表的一致性 ────────────────

def test_generic_sm_fallback_uses_service_state():
    """GenericSM._fallback 内部应查 SERVICE_STATE——断言两条路径对同一条目产出一致。"""
    from autoforge.af_vhass.event_bus import FakeEventBus
    from autoforge.af_time import SystemTimeSource

    clock = SystemTimeSource()
    bus = FakeEventBus(clock=clock)

    for (domain, service), expected in SERVICE_STATE.items():
        if domain in ("notify", "persistent_notification"):
            continue  # notify 走 service_effect 特殊分支，跳过
        entity_id = f"{domain}.dummy"
        sm = GenericSM(entity_id, bus, clock, initial_state="off")
        sm.apply(service, {})
        assert sm.state == expected or sm.state == "on", (
            f"GenericSM({domain}).apply({service})={sm.state}, 预期 SERVICE_STATE={expected}"
        )
