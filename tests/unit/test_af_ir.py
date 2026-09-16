"""af_ir 单测：Schema 校验、图查询、边优先级、表达式类型系统。"""

from __future__ import annotations

import json

import pytest

from autoforge.af_ir import (
    EDGE_PRIORITY,
    IRValidationError,
    collect_entity_refs,
    evaluate,
    load_automation,
    load_graph,
)
from autoforge.af_ir.expr import ExprError

MINIMAL = {
    "ir_version": "0.2.1",
    "id": "demo",
    "name": "示例",
    "version": 1,
    "mode": "single",
    "nodes": [{"id": "a1", "kind": "on", "trigger": {"type": "state", "entity_id": "sun.sun"}}],
    "edges": [],
}


# ── Schema 校验 ───────────────────────────────────────────────────────


def test_minimal_ir_passes_schema():
    auto = load_automation(MINIMAL)
    assert auto.id == "demo"
    assert auto.mode == "single"
    assert auto.snapshot is True


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda d: d.pop("mode"), id="缺 mode"),
        pytest.param(lambda d: d.update(mode="turbo"), id="非法 mode"),
        pytest.param(lambda d: d["nodes"][0].update(kind="teleport"), id="非法节点类型"),
        pytest.param(lambda d: d["nodes"][0].pop("trigger"), id="on 节点缺 trigger"),
        pytest.param(
            lambda d: d["edges"].append({"from": "a1", "to": "ghost", "kind": "then"}), id="边指向不存在的节点"
        ),
    ],
)
def test_invalid_ir_rejected(mutate):
    data = json.loads(json.dumps(MINIMAL))
    mutate(data)
    with pytest.raises(IRValidationError):
        load_automation(data)


def test_all_examples_load(examples_dir):
    files = sorted(examples_dir.glob("*.json"))
    assert len(files) >= 8
    for path in files:
        graph = load_graph(path)
        assert len(graph) >= 1
        for auto in graph:
            assert auto.nodes, f"{path.name} 无节点"


# ── 图查询与边优先级 ──────────────────────────────────────────────────


def test_pick_edge_follows_priority_not_definition_order():
    """优先级从高到低匹配，**不允许按定义顺序 fallback**。"""
    data = json.loads(json.dumps(MINIMAL))
    data["nodes"] += [
        {"id": "q1", "kind": "ask", "prompt": "要关灯吗", "timeout": "60s"},
        {"id": "p1", "kind": "pass"},
        {"id": "p2", "kind": "pass"},
    ]
    # 故意把低优先级的 default 写在最前面
    data["edges"] = [
        {"from": "q1", "to": "p1", "kind": "default"},
        {"from": "q1", "to": "p2", "kind": "on_timeout"},
        {"from": "a1", "to": "q1", "kind": "then"},
    ]
    auto = load_automation(data)
    # 同时可用时，on_timeout 胜过 default
    edge = auto.pick_edge("q1", {"default", "on_timeout"})
    assert edge.kind == "on_timeout" and edge.to == "p2"
    # 只有 default 可用时才走 default
    assert auto.pick_edge("q1", {"default"}).to == "p1"


def test_edge_priority_constant_order():
    assert EDGE_PRIORITY.index("on_cancel") < EDGE_PRIORITY.index("on_error")
    assert EDGE_PRIORITY.index("on_error") < EDGE_PRIORITY.index("on_timeout")
    assert EDGE_PRIORITY.index("then") < EDGE_PRIORITY.index("default")


def test_reads_and_writes(examples_dir):
    auto = load_graph(examples_dir / "case01_day_light.json").get("study_day_light")
    assert "sensor.study_illum" in auto.reads()
    assert auto.writes() == {"light.study_main"}


# ── 表达式 ────────────────────────────────────────────────────────────


def _resolver(values: dict):
    return lambda name, declared=None: values[name]


def test_numeric_must_be_declared():
    expr = {
        "op": "gt",
        "left": {"var": "entity.sensor.temp", "type": "numeric"},
        "right": {"const": 27},
    }
    assert evaluate(expr, _resolver({"entity.sensor.temp": "27.4"})) is True
    assert evaluate(expr, _resolver({"entity.sensor.temp": "20"})) is False


def test_undeclared_string_is_not_silently_converted():
    """IR §14-10：数值显式 typed。未声明的字符串与数字比较必须**报错**而不是隐式转换。"""
    expr = {
        "op": "gt",
        "left": {"var": "entity.sensor.temp"},
        "right": {"const": 27},
    }
    with pytest.raises(ExprError, match="类型不一致"):
        evaluate(expr, _resolver({"entity.sensor.temp": "27.4"}))


def test_logic_and_unary_ops():
    expr = {
        "op": "and",
        "args": [
            {"op": "is_off", "value": {"var": "entity.light.x"}},
            {"op": "not", "args": [{"op": "is_on", "value": {"var": "entity.switch.y"}}]},
        ],
    }
    assert evaluate(expr, _resolver({"entity.light.x": "off", "entity.switch.y": "off"})) is True
    assert evaluate(expr, _resolver({"entity.light.x": "on", "entity.switch.y": "off"})) is False


def test_collect_entity_refs():
    expr = {
        "op": "and",
        "args": [
            {"op": "lt", "left": {"var": "entity.sensor.a", "type": "numeric"}, "right": {"const": 1}},
            {"op": "is_off", "value": {"var": "entity.light.b"}},
            {"op": "truthy", "value": {"var": "vars.flag"}},
        ],
    }
    assert collect_entity_refs(expr) == {"sensor.a", "light.b"}
