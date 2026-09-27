"""v2 M3 结构化 Ask · 模型层 + AF-Spec 单测。

契约：IR/DSL 字段名统一为 `ask`（`$defs` 定义名仍是 ask_spec），属性 `min/max/unit`
不带下划线；`prompt` 不进 ask 对象，由节点级字段投影，避免两处真相。
"""

from __future__ import annotations

import pytest

from autoforge.af_ir.models import (
    AskSpec,
    Automation,
    IRValidationError,
    Node,
    collect_asks,
)
from autoforge.af_spec import compile_spec, graph_to_raw, render_spec


# ── 构造期校验（带病规格不许进运行时）──

def test_ask_spec_bad_kind_rejected():
    with pytest.raises(IRValidationError):
        AskSpec.from_dict({"kind": "bogus"})


def test_ask_spec_choice_requires_options():
    with pytest.raises(IRValidationError):
        AskSpec.from_dict({"kind": "choice"})


def test_ask_spec_choice_requires_at_least_two_options():
    # 1 个选项不构成"选择"
    with pytest.raises(IRValidationError):
        AskSpec.from_dict({"kind": "choice", "options": ["only"]})
    assert AskSpec.from_dict({"kind": "choice", "options": ["a", "b"]}).kind == "choice"


def test_ask_spec_range_requires_min_and_max():
    for kind in ("threshold", "time_range"):
        with pytest.raises(IRValidationError):
            AskSpec.from_dict({"kind": kind})  # 缺 min/max
        with pytest.raises(IRValidationError):
            AskSpec.from_dict({"kind": kind, "min": 0})  # 只给一半


def test_ask_spec_min_gt_max_rejected():
    with pytest.raises(IRValidationError):
        AskSpec.from_dict({"kind": "threshold", "min": 5, "max": 1})


def test_ask_spec_entity_requires_domain():
    with pytest.raises(IRValidationError):
        AskSpec.from_dict({"kind": "entity"})


def test_ask_spec_valid_threshold():
    s = AskSpec.from_dict({"kind": "threshold", "min": 0, "max": 100, "unit": "%"})
    assert s.kind == "threshold" and s.min_ == 0 and s.max_ == 100 and s.unit_ == "%"


# ── prompt 投影（不进 ask 对象）──

def test_prompt_projected_from_node_not_from_ask_object():
    # ask 对象里即便写了 prompt 也不生效——唯一真相是节点级 prompt
    s = AskSpec.from_dict({"kind": "text", "prompt": "来自 ask 对象"}, prompt="来自节点")
    assert s.prompt == "来自节点"


# ── control()：后端是控件映射的唯一真相 ──

@pytest.mark.parametrize(
    "data,widget",
    [
        ({"kind": "choice", "options": ["开", "关"]}, "select"),
        ({"kind": "threshold", "min": 16, "max": 30, "unit": "°C"}, "slider"),
        ({"kind": "time_range", "min": 0, "max": 1439}, "time_range"),
        ({"kind": "entity", "entity_domain": "light"}, "entity_picker"),
        ({"kind": "text"}, "input"),
    ],
)
def test_control_widget_per_kind(data, widget):
    assert AskSpec.from_dict(data).control()["widget"] == widget


def test_control_carries_bounds_and_options():
    c = AskSpec.from_dict({"kind": "threshold", "min": 16, "max": 30, "unit": "°C"}).control()
    assert c["min"] == 16 and c["max"] == 30 and c["unit"] == "°C"
    c2 = AskSpec.from_dict({"kind": "choice", "options": ["a", "b"]}).control()
    assert c2["options"] == ["a", "b"]


def test_control_time_range_is_minute_domain():
    # 时间窗内部一律分钟 [0,1439]，前端仅在呈现层转 HH:MM
    c = AskSpec.from_dict({"kind": "time_range", "min": 0, "max": 1439}).control()
    assert c["unit"] == "min" and c["max"] == 1439


# ── Node / collect_asks ──

def test_node_from_dict_wires_ask():
    n = Node.from_dict(
        {
            "id": "a1",
            "kind": "ask",
            "prompt": "选一个？",
            "ask": {"kind": "choice", "options": ["x", "y"]},
        }
    )
    assert n.ask is not None
    assert n.ask.kind == "choice"
    assert list(n.ask.options) == ["x", "y"]
    assert n.ask.prompt == "选一个？"  # 由节点级 prompt 投影


def test_collect_asks_only_structured():
    auto = Automation.from_dict(
        {
            "id": "a",
            "ir_version": "0.2.1",
            "name": "a",
            "version": 1,
            "mode": "restart",
            "nodes": [
                {"id": "a1", "kind": "ask", "prompt": "?", "ask": {"kind": "choice", "options": ["x", "y"]}},
                {"id": "a2", "kind": "ask", "prompt": "?"},  # 自由文本 ask，不计入
            ],
            "edges": [{"from": "a1", "to": "a2", "kind": "then"}],
        }
    )
    asks = collect_asks(auto)
    assert len(asks) == 1
    assert asks[0]["node_id"] == "a1"
    assert asks[0]["spec"]["kind"] == "choice"
    assert asks[0]["control"]["widget"] == "select"


# ── AF-Spec DSL ──

def test_af_spec_ask_node_parses_ask():
    spec = (
        'automation a\nname "a"\n'
        'on o1 {"type":"state","entity_id":"x","to":"on"}\n'
        'ask a1 "执行吗？" ask {"kind":"choice","options":["开","关"]}\n'
        'edge o1 -> a1 then\n'
    )
    graph = compile_spec(spec)
    raw = graph_to_raw(graph)
    node = [n for n in raw[0]["nodes"] if n["id"] == "a1"][0]
    assert node["ask"]["kind"] == "choice"
    assert node["ask"]["options"] == ["开", "关"]


def test_af_spec_ask_node_roundtrip():
    spec = (
        'automation a\nname "a"\n'
        'on o1 {"type":"state","entity_id":"x","to":"on"}\n'
        'ask a1 "执行吗？" ask {"kind":"choice","options":["开","关"]}\n'
        'edge o1 -> a1 then\n'
    )
    graph = compile_spec(spec)
    rendered = render_spec(graph)
    graph2 = compile_spec(rendered)
    raw2 = graph_to_raw(graph2)
    node2 = [n for n in raw2[0]["nodes"] if n["id"] == "a1"][0]
    assert node2["ask"]["kind"] == "choice"
