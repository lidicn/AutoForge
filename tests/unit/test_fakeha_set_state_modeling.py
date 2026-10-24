"""G3 方向①：FakeHA/vhass 对显式 set_state 动作的仿真建模。

缺口（缺口册 G3）：af_shadow.py:201 已有 set_state → @params.state 的期望态映射，
但仿真底座 SERVICE_STATE/DYNAMIC_SERVICES 无 set_state 条目 ⇒ 底座 unmodeled 不落态，
两侧口径不一致（影子算得出期望、底座给不出）。

PM 默认裁决：按字面建模，不发明映射（不偷偷改成 set_hvac_mode）。set_state 的新
状态取 params.state。
"""

from __future__ import annotations

from autoforge.af_vhass import fake


def test_set_state_cool():
    """腿1：调 (climate, set_state) params {state: cool} → 落 cool。"""
    new_state, _attrs = fake.service_effect("climate", "set_state", {"state": "cool"}, "off")
    assert new_state == "cool"


def test_set_state_off():
    """腿2：同调 params {state: off} → 落 off。"""
    new_state, _attrs = fake.service_effect("climate", "set_state", {"state": "off"}, "cool")
    assert new_state == "off"


def test_set_state_no_param_honest():
    """腿3：不带 state 参数不崩，按现状诚实处理（不假绿）。"""
    new_state, _attrs = fake.service_effect("climate", "set_state", {}, "cool")
    # 没有 state 就不动状态（保持 current），或明确 None——不许伪造一个状态
    assert new_state in ("cool", None)


def test_set_state_is_modeled():
    """腿4：登记后 is_modeled((climate, set_state)) 为 True。"""
    assert fake.is_modeled("climate", "set_state") is True


def test_existing_climate_services_no_regress():
    """腿5（护栏）：既有 set_temperature / turn_on 行为不回归。"""
    # turn_on → SERVICE_STATE 静态建模 cool
    assert fake.is_modeled("climate", "turn_on")
    s, _ = fake.service_effect("climate", "turn_on", {}, "off")
    assert s == "cool"
    # set_temperature 只写 temperature 属性，不改开关机态
    s, attrs = fake.service_effect("climate", "set_temperature", {"temperature": 24}, "cool")
    assert s == "cool" and attrs == {"temperature": 24}


def test_shadow_and_fake_state_align():
    """腿6：影子侧 set_state→@params.state 与底座落态口径一致。

    af_shadow.py:201 用 "@params.state" 作 set_state 的期望态表达式；底座
    service_effect 也取 params.state。两侧对同一个 set_state 调用应得出同一状态。
    """
    for state in ("heat", "cool", "off"):
        new_state, _ = fake.service_effect("climate", "set_state", {"state": state}, "on")
        assert new_state == state, f"底座落态应等于 params.state={state}"
