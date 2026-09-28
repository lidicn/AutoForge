"""F9 决策门数据采集方案：compose 会话多意图占比统计。

- 同一 session_id 多次 draft_intent = 一次用户 compose 请求产出多条自动化。
- 占比 = 多意图会话数 / 总会话数；<5% 则 v2.3 group 节点整体推迟（决策 D）。
- 不传 session_id 时零采样（默认无侵入）。
"""

from autoforge.af_draft import (
    draft_intent,
    compose_metrics_summary,
    reset_compose_metrics,
)

INTENT = {
    "name": "开门亮灯",
    "when": {"type": "state", "entity": "前门", "to": "on"},
    "do": {"action": "开灯", "target": "客厅灯"},
}


def setup_function(_):
    reset_compose_metrics()


def test_no_session_id_no_sampling():
    draft_intent(INTENT)
    draft_intent(INTENT)
    assert compose_metrics_summary()["sessions"] == 0


def test_single_intent_session():
    draft_intent(INTENT, session_id="s1")
    s = compose_metrics_summary()
    assert s["sessions"] == 1
    assert s["multi_intent_sessions"] == 0
    assert s["single_intent_sessions"] == 1
    assert s["multi_intent_ratio"] == 0.0


def test_multi_intent_session_ratio():
    draft_intent(INTENT, session_id="s1")
    draft_intent(INTENT, session_id="s1")
    draft_intent(INTENT, session_id="s2")  # 单意图会话
    s = compose_metrics_summary()
    assert s["sessions"] == 2
    assert s["multi_intent_sessions"] == 1
    assert s["single_intent_sessions"] == 1
    assert s["total_automations"] == 3
    assert abs(s["multi_intent_ratio"] - 0.5) < 1e-9


def test_session_distinct_counts():
    draft_intent(INTENT, session_id="a")
    draft_intent(INTENT, session_id="a")
    draft_intent(INTENT, session_id="a")  # a 产出 3 条
    draft_intent(INTENT, session_id="b")
    draft_intent(INTENT, session_id="c")
    s = compose_metrics_summary()
    assert s["sessions"] == 3
    assert s["multi_intent_sessions"] == 1
    assert s["total_automations"] == 5
