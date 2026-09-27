"""PR 1.2 双轨对拍测试：同一组自动化在 FakeHA / HiFi 下仿真结果一致性断言。

决策 A：两轨共享 fake.py 唯一效果真值表，对建模动作结果应当一致；
唯一已知分歧点是 `sun` 触发器（fake.py:52 固定 18:00/06:00 vs HiFi 真实太阳几何），
对拍时白名单豁免。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from autoforge.af_service import simulate_track
from autoforge.af_vhass.dual_track import compare_dual_track, sun_trigger_automation_ids
from autoforge.af_vhass.fake import sun_state
from autoforge.af_vhass.high_fidelity import sun_state_at


def _ir(action, target, *, effects=None, expect=None, seed=None, trigger_entity="light.a"):
    """构造最小合法 IR：state 触发器 → do（含 entity_id 参数）。"""
    do = {"id": "do", "kind": "do", "adapter": "ha", "action": action, "params": {"entity_id": target}}
    if effects is not None:
        do["effects"] = effects
    return {
        "ir_version": "0.2.1",
        "id": "a1",
        "name": "t",
        "version": 1,
        "mode": "restart",
        "nodes": [
            {"id": "on", "kind": "on", "trigger": {"type": "state", "entity_id": trigger_entity, "to": "on"}},
            do,
        ],
        "edges": [{"from": "on", "to": "do", "kind": "then"}],
        "expect": expect or [],
    }


def _fire(trigger_entity="light.a"):
    return [{"entity_id": trigger_entity, "state": "on"}]


# ---- 决策 A 核心：建模动作两轨一致 ----------------------------------------


def test_light_turn_on_两轨一致():
    seed = {"light.b": "off", "light.a": "off"}
    ir = _ir("light.turn_on", "light.b", expect=[{"entity_id": "light.b", "state": "on"}], seed=seed)
    out = compare_dual_track(ir, seed=seed, events=_fire())
    assert out["divergences"] == []
    assert out["fake"]["final_states"]["light.b"] == "on"
    assert out["hifi"]["final_states"]["light.b"] == "on"


def test_switch_turn_off_两轨一致():
    seed = {"switch.kettle": "on", "light.a": "off"}
    ir = _ir("switch.turn_off", "switch.kettle", expect=[{"entity_id": "switch.kettle", "state": "off"}], seed=seed)
    out = compare_dual_track(ir, seed=seed, events=_fire())
    assert out["divergences"] == []
    assert out["hifi"]["final_states"]["switch.kettle"] == "off"


def test_climate_set_temperature_两轨一致():
    # 决策 A 真值表一致性：set_temperature 不改变开关机状态（off 保持 off，
    # 两轨均如此），且 temperature 属性在两轨都按参数翻到 26。
    seed = {"climate.study": {"state": "off", "attributes": {"temperature": 16}}, "light.a": "off"}
    ir = {
        "ir_version": "0.2.1", "id": "a1", "name": "t", "version": 1, "mode": "restart",
        "nodes": [
            {"id": "on", "kind": "on", "trigger": {"type": "state", "entity_id": "light.a", "to": "on"}},
            {"id": "do", "kind": "do", "adapter": "ha", "action": "climate.set_temperature",
             "params": {"entity_id": "climate.study", "temperature": 26}},
        ],
        "edges": [{"from": "on", "to": "do", "kind": "then"}],
        "expect": [{"entity_id": "climate.study", "attribute": "temperature", "value": 26}],
    }
    out = compare_dual_track(ir, seed=seed, events=_fire())
    assert out["divergences"] == []
    assert out["fake"]["final_states"]["climate.study"] == "off"
    assert out["hifi"]["final_states"]["climate.study"] == "off"
    for track in ("fake", "hifi"):
        ok = any("temperature" in str(it.get("target", "")) and it.get("status") == "pass"
                 for it in out[track]["report"]["verified"])
        assert ok, f"{track} 轨未验证 climate.study.temperature 属性断言"


def test_media_player_play_media_两轨一致():
    seed = {"media_player.tv": "off", "light.a": "off"}
    ir = _ir("media_player.play_media", "media_player.tv",
             expect=[{"entity_id": "media_player.tv", "state": "playing"}], seed=seed)
    out = compare_dual_track(ir, seed=seed, events=_fire())
    assert out["divergences"] == []
    assert out["hifi"]["final_states"]["media_player.tv"] == "playing"


def test_scene_effects_两轨一致():
    # 决策 A 清单：scene 间接触发展开 → 两轨都应把 light.b 置 on 并验证 expect
    seed = {"light.b": "off", "light.a": "off"}
    ir = _ir(
        "scene.activate", "light.b",
        effects=[{"entity_id": "light.b", "state": "on"}],
        expect=[{"entity_id": "light.b", "state": "on"}],
        seed=seed,
    )
    out = compare_dual_track(ir, seed=seed, events=_fire())
    assert out["divergences"] == []
    # 两轨的 scene 自动化 expect 都应判定为 pass（verified，离开 non_simulable）
    fverified = any(it.get("target") == "light.b" and it.get("status") == "pass"
                    for it in out["fake"]["report"]["verified"])
    hverified = any(it.get("target") == "light.b" and it.get("status") == "pass"
                    for it in out["hifi"]["report"]["verified"])
    assert fverified and hverified


def test_多自动化_两轨一致():
    # 两条独立自动化（灯 + 开关）同跑，两轨 final_states 与判定都应一致
    events = [{"entity_id": "light.a", "state": "on"}, {"entity_id": "binary_sensor.m", "state": "on"}]
    multi = {
        "ir_version": "0.2.1", "id": "root", "name": "t", "version": 1, "mode": "restart",
        "automations": [
            {
                "ir_version": "0.2.1", "id": "a1", "name": "l", "version": 1, "mode": "restart",
                "nodes": [
                    {"id": "on", "kind": "on", "trigger": {"type": "state", "entity_id": "light.a", "to": "on"}},
                    {"id": "do", "kind": "do", "adapter": "ha", "action": "light.turn_on", "params": {"entity_id": "light.b"}},
                ],
                "edges": [{"from": "on", "to": "do", "kind": "then"}],
                "expect": [{"entity_id": "light.b", "state": "on"}],
            },
            {
                "ir_version": "0.2.1", "id": "a2", "name": "s", "version": 1, "mode": "restart",
                "nodes": [
                    {"id": "on", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
                    {"id": "do", "kind": "do", "adapter": "ha", "action": "switch.turn_on", "params": {"entity_id": "switch.k"}},
                ],
                "edges": [{"from": "on", "to": "do", "kind": "then"}],
                "expect": [{"entity_id": "switch.k", "state": "on"}],
            },
        ],
    }
    out = compare_dual_track(multi, seed={"light.b": "off", "light.a": "off", "switch.k": "off"},
                             events=events)
    assert out["divergences"] == []
    assert out["fake"]["final_states"]["light.b"] == "on" == out["hifi"]["final_states"]["light.b"]
    assert out["fake"]["final_states"]["switch.k"] == "on" == out["hifi"]["final_states"]["switch.k"]


# ---- sun 触发器白名单（已知分歧点） ---------------------------------------


def test_sun_triggered_automation_被白名单():
    ir = {
        "ir_version": "0.2.1", "id": "a1", "name": "t", "version": 1, "mode": "restart",
        "nodes": [
            {"id": "on", "kind": "on", "trigger": {"type": "sun", "event": "sunset"}},
            {"id": "do", "kind": "do", "adapter": "ha", "action": "light.turn_on",
             "params": {"entity_id": "light.sun_lamp"}},
        ],
        "edges": [{"from": "on", "to": "do", "kind": "then"}],
        "expect": [{"entity_id": "light.sun_lamp", "state": "on"}],
    }
    ids = sun_trigger_automation_ids(ir)
    assert ids == ["a1"]
    # 白名单内的分歧不计入 divergences（两轨一致判定通过）
    out = compare_dual_track(ir)
    assert out["whitelisted"] == ["a1"]
    assert out["divergences"] == []


def _make_sun_ir():
    return {
        "ir_version": "0.2.1", "id": "a1", "name": "t", "version": 1, "mode": "restart",
        "nodes": [
            {"id": "on", "kind": "on", "trigger": {"type": "sun", "event": "sunset"}},
            {"id": "do", "kind": "do", "adapter": "ha", "action": "light.turn_on",
             "params": {"entity_id": "light.sun_lamp"}},
        ],
        "edges": [{"from": "on", "to": "do", "kind": "then"}],
        "expect": [{"entity_id": "light.sun_lamp", "state": "on"}],
    }


def test_sun_分歧点真实存在_fakeha固定历vs_hifi真实几何():
    # 决策 A 已知分歧：盛夏 18:30，FakeHA 固定日落 18:00 → below_horizon；
    # HiFi 深圳真实日落 ≈19:11 → above_horizon。两者对 sun.sun 的计算确实不同，
    # 这正是把 sun 触发器列入白名单的硬依据（直接用两套太阳历函数对账，不依赖整链 simulate）。
    cst = timezone(timedelta(hours=8))
    moment = datetime(2026, 6, 21, 18, 30, tzinfo=cst)
    fake_sun = sun_state(moment)                       # fake.py:52 固定桩
    hifi_sun = sun_state_at(moment)                    # 真实 NOAA 几何
    assert fake_sun == "below_horizon"
    assert hifi_sun == "above_horizon"
    assert fake_sun != hifi_sun                         # 分歧确实存在 → 白名单必要


def test_simulate_track_hifi_返回_track标记():
    ir = _ir("light.turn_on", "light.b", seed={"light.b": "off", "light.a": "off"})
    out = simulate_track("hifi", ir, seed={"light.b": "off", "light.a": "off"}, events=_fire())
    assert out["track"] == "hifi"
    out_fake = simulate_track("fake", ir, seed={"light.b": "off", "light.a": "off"}, events=_fire())
    assert out_fake["track"] == "fake"


def test_simulate_track_未知轨报错():
    import pytest

    ir = _ir("light.turn_on", "light.b")
    with pytest.raises(ValueError):
        simulate_track("bogus", ir)
