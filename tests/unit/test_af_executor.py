"""v2 M3 结构化 Ask · 运行时单测（收敛纪律 + 结构化应答校验）。"""

from __future__ import annotations

from autoforge.af_executor import (
    AskSession,
    MAX_ASK_ROUNDS,
    MAX_ASKS_PER_ROOM,
    NodeExecutor,
)
from autoforge.af_ir.models import AskAnswer, AskSpec


def _ans(kind, value):
    return AskAnswer(kind=kind, value=value)


def test_convergence_limits_are_two_dimensions():
    # 收敛纪律拆两维（不再是"3轮×2问=6"的合并计数）：
    # 同一实例连问 ≤3 轮（成功的 do 清零）；同一房间同一时刻挂起 ≤2（跨实例合计）
    assert MAX_ASK_ROUNDS == 3
    assert MAX_ASKS_PER_ROOM == 2


def test_ask_session_default_ask_spec_none():
    s = AskSession(instance_id="i", node_id="n", room=None, created_at=0.0)
    assert s.ask_spec is None


def test_validate_structured_choice_ok():
    spec = AskSpec.from_dict({"kind": "choice", "options": ["a", "b"]})
    assert NodeExecutor._validate_structured(_ans("choice", "a"), spec) is True
    assert NodeExecutor._validate_structured(_ans("choice", "z"), spec) is False  # 不在选项
    assert NodeExecutor._validate_structured(_ans("threshold", 1), spec) is False  # kind 不一致


def test_validate_structured_threshold_bounds():
    spec = AskSpec.from_dict({"kind": "threshold", "min": 0, "max": 10})
    assert NodeExecutor._validate_structured(_ans("threshold", 5), spec) is True
    assert NodeExecutor._validate_structured(_ans("threshold", 11), spec) is False  # 越上界
    assert NodeExecutor._validate_structured(_ans("threshold", None), spec) is False  # 缺省即拒


def test_validate_structured_entity_and_text():
    ent = AskSpec.from_dict({"kind": "entity", "entity_domain": "light"})
    assert NodeExecutor._validate_structured(_ans("entity", "light.x"), ent) is True
    assert NodeExecutor._validate_structured(_ans("entity", ""), ent) is False
    txt = AskSpec.from_dict({"kind": "text"})
    assert NodeExecutor._validate_structured(_ans("text", "hi"), txt) is True
    assert NodeExecutor._validate_structured(_ans("text", ""), txt) is False


def test_validate_structured_time_range():
    spec = AskSpec.from_dict({"kind": "time_range", "min": 0, "max": 1439})
    assert NodeExecutor._validate_structured(_ans("time_range", [60, 120]), spec) is True
    assert NodeExecutor._validate_structured(_ans("time_range", [100, 50]), spec) is False  # s>e
    assert NodeExecutor._validate_structured(_ans("time_range", [1, 2, 3]), spec) is False  # 长度错


def test_validate_structured_time_range_bounds():
    """min/max 是**应答值域约束**（可选窗口），与 threshold 同语义，不只是控件刻度。"""
    spec = AskSpec.from_dict({"kind": "time_range", "min": 480, "max": 1320})  # 08:00–22:00
    assert NodeExecutor._validate_structured(_ans("time_range", [600, 900]), spec) is True
    assert NodeExecutor._validate_structured(_ans("time_range", [300, 900]), spec) is False  # 起点越下界
    assert NodeExecutor._validate_structured(_ans("time_range", [600, 1400]), spec) is False  # 终点越上界
    assert NodeExecutor._validate_structured(_ans("time_range", [480, 1320]), spec) is True  # 边界含端点


def test_validate_structured_free_text_when_no_spec():
    # 自由文本 ask：非空即合规，空即不合规
    assert NodeExecutor._validate_structured(_ans("text", "anything"), None) is True
    assert NodeExecutor._validate_structured(_ans("text", ""), None) is False
