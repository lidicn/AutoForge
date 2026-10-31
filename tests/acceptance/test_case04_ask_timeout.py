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
    """对照腿：ask 答"好" ⇒ L2 动作立即下发（一次审批＝部署，运行期不二次问人）。

    裁定 20261031（取消运行期二次授权，出资人 E13 原话「自动化只有一次审批…部署后
    即可生效」）收掉了本链的第二道门：`requires_confirm` 的运行期挂起已随卡 B 撤除，
    部署期显眼标记保留（矩阵 3/4 行），L2 动作在部署审批摘要里列明（矩阵行 2，
    AF-715：待批列表批准前即可见）。`ask q1` 是自动化自己的在环询问（G1 特性，
    保留），答"好"即走 ⇒ 本判据从「两道门逐门断言」改写为「一道门答完即下发」。

    旧形状（答完 ask 仍零下发、等第二次确认）是裁定 20261009 两道门世界的判据，
    随卡 B 闭合改写；ask 的 on_timeout/default/on_cancel 无人值守分支仍被
    HIGH_RISK_AFTER_SUSPEND 拦（扫描器口径同批收窄：yes/no 有人在环，移出射程）。
    """
    harness = make_harness("case04_ask_timeout.json", SEED)
    harness.fire("sensor.study_temp", "28.5")

    answered = harness.runtime.answer("study", "好")
    assert answered is not None, "room 维度应匹配到书房实例"
    # 一次审批世界：ask 答"好"即下发，没有第二道确认门
    assert harness.get("climate.study") == "cool"
    assert [a for a, _ in harness.ha_calls] == ["climate.turn_on"]
    assert harness.runtime.executor.pending_asks == {}


def test_case04_wrong_room_does_not_match(make_harness):
    """其它房间的应答不应命中（ask 会话按 room 维度消歧义）。"""
    harness = make_harness("case04_ask_timeout.json", SEED)
    harness.fire("sensor.study_temp", "28.5")

    assert harness.runtime.answer("bedroom", "好") is None
    assert harness.ha_calls == []
