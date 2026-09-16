"""G1 验收用例 5：询问中"人离开" → `on_cancel` 取消，**已执行动作不回滚**。"""

from __future__ import annotations

from autoforge.af_instance import CANCELLED, SUSPENDED


def test_case05_cancel_does_not_rollback(make_harness):
    harness = make_harness(
        "case05_ask_cancel.json",
        {"binary_sensor.study_motion": "off", "light.study_main": "off"},
    )

    harness.fire("binary_sensor.study_motion", "on")
    instance = harness.instances[0]

    # 先开的灯已经生效
    assert harness.get("light.study_main") == "on"
    assert instance.state == SUSPENDED, "应挂在询问节点"

    harness.runtime.cancel(instance, reason="人离开")

    assert instance.state == CANCELLED, "走 on_cancel → 取消"
    assert harness.get("light.study_main") == "on", "取消不回滚：灯必须保持开着"
    assert [a for a, _ in harness.ha_calls] == ["light.turn_on"], "取消分支里没有额外动作"


def test_case05_cancel_reason_is_recorded(make_harness):
    harness = make_harness(
        "case05_ask_cancel.json",
        {"binary_sensor.study_motion": "off", "light.study_main": "off"},
    )
    harness.fire("binary_sensor.study_motion", "on")
    instance = harness.instances[0]
    harness.runtime.cancel(instance, reason="人离开")

    assert instance.ctx.context["cancel_reason"] == "人离开"
