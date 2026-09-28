"""F9 决策门生产接线：af_mcp._t_draft 把 session_id 透传给 af_draft.ComposeMetrics。

- 同一 session_id 多次 draft = 一次用户 compose 请求产出多条自动化（多意图）。
- 不传 session_id 时零采样（默认无侵入）。
"""

from autoforge.af_draft import compose_metrics_summary, reset_compose_metrics
from autoforge.af_mcp import _t_draft

INTENT = {
    "name": "开门亮灯",
    "when": {"type": "state", "entity": "前门", "to": "on"},
    "do": {"action": "开灯", "target": "客厅灯"},
}


def setup_function(_):
    reset_compose_metrics()


def test_mcp_draft_forwards_session_id():
    _t_draft(None, {"intent": INTENT, "session_id": "s1"})
    _t_draft(None, {"intent": INTENT, "session_id": "s1"})
    _t_draft(None, {"intent": INTENT, "session_id": "s2"})
    s = compose_metrics_summary()
    assert s["sessions"] == 2
    assert s["multi_intent_sessions"] == 1
    assert s["total_automations"] == 3


def test_mcp_draft_no_session_no_sampling():
    _t_draft(None, {"intent": INTENT})
    _t_draft(None, {"intent": INTENT})
    assert compose_metrics_summary()["sessions"] == 0
