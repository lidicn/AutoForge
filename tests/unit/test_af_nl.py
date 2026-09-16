"""af_nl 单测：确定性渲染 + 覆盖率检查。"""

from __future__ import annotations

from autoforge.af_ir import Graph, load_automation, load_graph
from autoforge.af_nl import render_automation, render_graph


def _auto(**over) -> dict:
    data = {
        "ir_version": "0.2.1",
        "id": "demo",
        "name": "书房白天补光",
        "version": 1,
        "mode": "single",
        "nodes": [
            {"id": "a1", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
            {
                "id": "i1",
                "kind": "if",
                "expr": {
                    "op": "lt",
                    "left": {"var": "entity.sensor.illum", "type": "numeric"},
                    "right": {"const": 200},
                },
            },
            {
                "id": "d1",
                "kind": "do",
                "adapter": "ha",
                "action": "light.turn_on",
                "params": {"entity_id": "light.main"},
            },
            {"id": "p1", "kind": "pass"},
        ],
        "edges": [
            {"from": "a1", "to": "i1", "kind": "then"},
            {"from": "i1", "to": "d1", "kind": "then"},
            {"from": "i1", "to": "p1", "kind": "no"},
            {"from": "d1", "to": "p1", "kind": "then"},
        ],
    }
    data.update(over)
    return data


def test_render_covers_all_nodes():
    result = render_automation(load_automation(_auto()))
    assert result.ok, result.warnings
    assert result.coverage == 1.0
    assert {"a1", "i1", "d1", "p1"} <= result.covered


def test_render_is_deterministic():
    first = render_automation(load_automation(_auto())).text
    second = render_automation(load_automation(_auto())).text
    assert first == second


def test_render_mentions_key_facts():
    text = render_automation(load_automation(_auto())).text
    assert "binary_sensor.m" in text
    assert "sensor.illum" in text and "200" in text
    assert "打开 light.main" in text


def test_render_restart_mode_reveals_ha_difference():
    """与 HA 的有意偏离必须在 NL 里可见（docs/HA_SEMANTIC_DIFF.md）。"""
    text = render_automation(load_automation(_auto(mode="restart"))).text
    assert "取消" in text and "HA" in text


def test_orphan_node_is_reported_as_missing():
    data = _auto(nodes=_auto()["nodes"] + [{"id": "orphan", "kind": "pass"}])
    result = render_automation(load_automation(data))
    assert "orphan" in result.missing
    assert result.coverage < 1.0


def test_render_graph(examples_dir):
    result = render_graph(load_graph(examples_dir / "invalid_case08_cycle.json"))
    assert len(result.covered) == 6  # 两条自动化各 3 个节点
    assert result.ok
