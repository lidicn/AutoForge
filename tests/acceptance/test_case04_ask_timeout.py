"""G1 验收用例 4：温度>27 + 门关 → 询问，60s 无应答 → `on_timeout` 静默，**实例不挂起**。"""

from __future__ import annotations

from autoforge.af_instance import DONE, SUSPENDED

SEED = {"sensor.study_temp": "28", "binary_sensor.study_door": "off", "climate.study": "off"}


def test_case04_ask_suspends_first(make_harness):
    harness = make_harness("case04_ask_timeout.json", SEED)
    harness.fire("sensor.study_temp", "28.5")

    instance = harness.instances[0]
    assert instance.state == SUSPENDED
    assert harness.runtime.executor.pending_asks, "应有一个挂起的询问会话"


def test_case04_timeout_goes_silent_and_instance_not_suspended(make_harness):
    harness = make_harness("case04_ask_timeout.json", SEED)
    harness.fire("sensor.study_temp", "28.5")
    instance = harness.instances[0]

    harness.advance(61)  # 超过 60s 询问超时

    assert instance.state == DONE, "超时后走 on_timeout → 静默结束，实例不能继续挂起"
    assert harness.ha_calls == [], "静默 = 不开空调"
    assert harness.get("climate.study") == "off"
    assert harness.runtime.executor.pending_asks == {}


def test_case04_user_answers_yes_turns_on_ac(make_harness):
    """对照组：两道门都答"好"才开空调。

    裁定 20261009 §三 硬前置把 `requires_confirm` 从"只有编译期看得见"改成运行期真拦，
    所以这条链今天有**两道**人工门：`ask q1`（要不要开）与 `do d1` 的确认（真的要下发）。
    判据因此逐门断言——第一道门答完必须仍是"零下发"，否则"接口 200、设备没动"那类假绿
    就会被写进验收标准。
    """
    harness = make_harness("case04_ask_timeout.json", SEED)
    harness.fire("sensor.study_temp", "28.5")

    answered = harness.runtime.answer("study", "好")
    assert answered is not None, "room 维度应匹配到书房实例"
    # 第一道门已过、第二道门（requires_confirm）挂起中：一条下发都没有
    assert harness.get("climate.study") == "off"
    assert harness.ha_calls == []
    assert harness.runtime.executor.pending_asks, "应剩人工确认这一道门"

    again = harness.runtime.answer("study", "好")
    assert again is not None, "确认会话必须同房间可达（沿用本实例最近一次 ask 的 room）"
    assert harness.get("climate.study") == "cool"
    assert [a for a, _ in harness.ha_calls] == ["climate.turn_on"]
    assert harness.runtime.executor.pending_asks == {}


def test_case04_wrong_room_does_not_match(make_harness):
    """其它房间的应答不应命中（ask 会话按 room 维度消歧义）。"""
    harness = make_harness("case04_ask_timeout.json", SEED)
    harness.fire("sensor.study_temp", "28.5")

    assert harness.runtime.answer("bedroom", "好") is None
    assert harness.ha_calls == []
