"""G1 验收用例 1：白天人在 + 光照<200 + 灯灭 → 开灯。

覆盖：边沿触发、多条件 AND、段内原子快照、`do` 单次下发。
"""

from __future__ import annotations

from typer.testing import CliRunner

from autoforge.af_cli import app


def test_case01_turns_on_light(make_harness, examples_dir):
    harness = make_harness(
        "case01_day_light.json",
        {"sensor.study_illum": "80", "light.study_main": "off", "binary_sensor.study_motion": "off"},
    )

    harness.fire("binary_sensor.study_motion", "on")

    assert harness.get("light.study_main") == "on"
    assert [a for a, _ in harness.ha_calls] == ["light.turn_on"]


def test_case01_condition_not_met_does_nothing(make_harness):
    """光照足够时不该开灯（验证条件真的参与判定）。"""
    harness = make_harness(
        "case01_day_light.json",
        {"sensor.study_illum": "500", "light.study_main": "off", "binary_sensor.study_motion": "off"},
    )
    harness.fire("binary_sensor.study_motion", "on")
    assert harness.get("light.study_main") == "off"
    assert harness.ha_calls == []


def test_case01_passes_build_gate(examples_dir):
    """合法 IR 必须能通过安全闸（退出码 0）。"""
    result = CliRunner().invoke(app, ["build", str(examples_dir / "case01_day_light.json"), "--no-nl"])
    assert result.exit_code == 0, result.output
