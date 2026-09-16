"""G1 验收用例 2：人离 10 分钟关灯，第 5 分钟人回 → **关灯被取消**。

验证 `for=10m` 是"边沿触发 + 持续时长"而不是电平触发：条件中途被破坏即取消。
"""

from __future__ import annotations


def test_case02_for_is_cancelled_when_condition_breaks(make_harness):
    harness = make_harness(
        "case02_leave_for_10m.json",
        {"binary_sensor.study_motion": "on", "light.study_main": "on"},
    )

    harness.fire("binary_sensor.study_motion", "off")  # t0：人走
    assert len(harness.runtime.scheduler.pending_for()) == 1

    harness.fire("binary_sensor.study_motion", "on", advance_s=300)  # t+5min：人回来
    assert harness.runtime.scheduler.pending_for() == [], "条件被破坏，持续条件应取消"

    harness.advance(601)  # 早就过了 10 分钟
    assert harness.get("light.study_main") == "on", "关灯必须被取消"
    assert harness.ha_calls == []


def test_case02_for_fires_when_condition_holds(make_harness):
    """对照组：人一直没回来 → 10 分钟后真的关灯。"""
    harness = make_harness(
        "case02_leave_for_10m.json",
        {"binary_sensor.study_motion": "on", "light.study_main": "on"},
    )

    harness.fire("binary_sensor.study_motion", "off")
    harness.advance(601)

    assert harness.get("light.study_main") == "off"
    assert [a for a, _ in harness.ha_calls] == ["light.turn_off"]
