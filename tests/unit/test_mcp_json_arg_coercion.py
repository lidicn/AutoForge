"""MCP 边界的 JSON 参数兜底（体验者 2026-10-28 现场归因）。

体验者用对照实验把病灶钉死：DSH 桥接层把「可选+嵌套复杂」参数（object/含 object
的 array）json.dumps 成字符串转发——必填复杂参数（ir）与纯字符串数组
（known_entities/tags）都原生。seed/events 恰好是前者 ⇒ AF 端 dict() 炸成
ADM_ERR_INTERNAL。修法选 AF 侧兜底（体验者列的二选一：DSH 是第三方桥接层，
AF 管不了别人发什么，但能把"发错形状"变容错/可读错误）。

钉四条：字符串化解一层 / 原生不回归 / 坏字符串可读错误（⛔ 不 INTERNAL 崩）/
af_simulate 端到端（DSH 形状能跑完整图）。
"""

from __future__ import annotations

import json

import pytest

from autoforge.af_mcp import _coerce_json_arg, _t_simulate
from autoforge.af_store import GraphStore

_IR = {
    "ir_version": "0.2.1", "id": "sim_repro", "name": "repro", "version": 1, "mode": "single",
    "nodes": [
        {"id": "t1", "kind": "on", "name": "t",
         "trigger": {"type": "state", "entity_id": "binary_sensor.d", "to": "off"}},
        {"id": "d1", "kind": "do", "name": "d", "adapter": "ha",
         "action": "climate.turn_on", "params": {"entity_id": "climate.a"}},
    ],
    "edges": [{"from": "t1", "to": "d1", "kind": "then"}],
}


def test_coerce_parses_dsh_stringified_shapes():
    """DSH 现场形状：seed/events 被 json.dumps 成字符串——解一层后照常工作。"""
    seed = _coerce_json_arg(json.dumps({"binary_sensor.d": "on"}), "seed")
    events = _coerce_json_arg(json.dumps([{"entity_id": "binary_sensor.d", "state": "off"}]), "events")
    assert seed == {"binary_sensor.d": "on"}
    assert events == [{"entity_id": "binary_sensor.d", "state": "off"}]


def test_coerce_passes_native_shapes_through():
    assert _coerce_json_arg({"a": "b"}, "seed") == {"a": "b"}
    assert _coerce_json_arg([{"x": 1}], "events") == [{"x": 1}]
    assert _coerce_json_arg(None, "seed") is None


def test_coerce_bad_string_is_readable_error():
    """真坏字符串：可读 ValueError 指明桥接层问题——⛔ 不是裸 dict() 的 INTERNAL 崩。"""
    with pytest.raises(ValueError) as ei:
        _coerce_json_arg("这不是json", "seed")
    assert "桥接层" in str(ei.value) and "seed" in str(ei.value)


def test_t_simulate_end_to_end_with_dsh_shapes():
    """端到端：DSH 字符串化形状跑完整图（体验者被挡住的那一步）。"""
    with __import__("tempfile").TemporaryDirectory() as td:
        store = GraphStore(td)
        r = _t_simulate(store, {
            "ir": _IR,
            "seed": json.dumps({"binary_sensor.d": "on"}),
            "events": json.dumps([{"entity_id": "binary_sensor.d", "state": "off"}]),
        })
        assert r.get("ok") is True, str(r)[:200]
        # 效果真值表：climate.turn_on 在空调上的落位是 cool（hvac_mode 默认档）
        assert (r.get("final_states") or {}).get("climate.a") == "cool"


def test_t_simulate_bad_string_reports_readable_not_internal():
    with __import__("tempfile").TemporaryDirectory() as td:
        store = GraphStore(td)
        with pytest.raises(ValueError) as ei:
            _t_simulate(store, {"ir": _IR, "seed": "{bad json"})
        assert "seed" in str(ei.value)
