"""G1 验收用例 3：夜晚（太阳历）电脑开 → 挂灯开。

真 vhass 下 `sun.sun` 由 HA 原生太阳历提供；FakeHA 降级时用内置太阳历桩
（`af_vhass.fake.sun_state`：18:00–06:00 视为 below_horizon）。
"""

from __future__ import annotations


def test_case03_night_pc_on_turns_on_desk_lamp(make_harness):
    harness = make_harness(
        "case03_night_sun.json",
        {"switch.desk_pc": "off", "light.desk_lamp": "off"},
    )

    harness.fire("switch.desk_pc", "on", advance_s=12 * 3600)  # 08:00 → 20:00（夜晚）
    assert harness.get("light.desk_lamp") == "on"


def test_case03_daytime_pc_on_does_nothing(make_harness):
    """白天开电脑不该开灯（验证太阳历真的参与判定）。"""
    harness = make_harness(
        "case03_night_sun.json",
        {"switch.desk_pc": "off", "light.desk_lamp": "off"},
    )
    harness.fire("switch.desk_pc", "on", advance_s=60)  # 08:01（白天）
    assert harness.get("light.desk_lamp") == "off"
    assert harness.ha_calls == []
