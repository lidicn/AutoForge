"""af_scanner 单测：十项检查的拦截效果。"""

from __future__ import annotations

from autoforge.af_ir import Graph, load_automation
from autoforge.af_scanner import StaticScanner


def _scan(data: dict) -> StaticScanner:
    """对单条 IR 跑扫描（扫描器本身面向 Graph）。"""
    return StaticScanner(Graph([load_automation(data)])).scan()


def _base(**over) -> dict:
    data = {
        "ir_version": "0.2.1",
        "id": "demo",
        "name": "示例",
        "version": 1,
        "mode": "single",
        "nodes": [
            {"id": "a1", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
            {"id": "p1", "kind": "pass"},
        ],
        "edges": [{"from": "a1", "to": "p1", "kind": "then"}],
    }
    data.update(over)
    return data


def test_clean_ir_has_no_errors():
    assert _scan(_base()).ok


def test_02_ask_without_timeout_or_default():
    scan = _scan(
        _base(
            nodes=[
                {"id": "a1", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
                {"id": "q1", "kind": "ask", "prompt": "要关灯吗"},
                {"id": "p1", "kind": "pass"},
            ],
            edges=[
                {"from": "a1", "to": "q1", "kind": "then"},
                {"from": "q1", "to": "p1", "kind": "yes"},
            ],
        )
    )
    assert "MISSING_TIMEOUT_OR_DEFAULT" in scan.codes()


def test_09_adapter_policy_param():
    scan = _scan(
        _base(
            nodes=[
                {"id": "a1", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
                {
                    "id": "d1",
                    "kind": "do",
                    "adapter": "ha",
                    "action": "light.turn_on",
                    "params": {"entity_id": "light.a", "retry": 3},
                },
                {"id": "p1", "kind": "pass"},
            ],
            edges=[
                {"from": "a1", "to": "d1", "kind": "then"},
                {"from": "d1", "to": "p1", "kind": "then"},
            ],
        )
    )
    assert "ADAPTER_POLICY_PARAM" in scan.codes()


def test_07_shadow_mode_must_not_write_device():
    scan = _scan(
        _base(
            confidence=0.7,
            nodes=[
                {"id": "a1", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
                {
                    "id": "d1",
                    "kind": "do",
                    "adapter": "ha",
                    "action": "light.turn_on",
                    "params": {"entity_id": "light.a"},
                },
                {"id": "p1", "kind": "pass"},
            ],
            edges=[
                {"from": "a1", "to": "d1", "kind": "then"},
                {"from": "d1", "to": "p1", "kind": "then"},
            ],
        )
    )
    assert "SHADOW_WRITES_DEVICE" in scan.codes()


def test_14_duplicate_edge_priority():
    """同节点同优先级（同一种 kind）重复定义 → 报错。

    注意 `then` 与 `yes` 优先级不同，**不算**重复（这是有意设计：分支可共存）。
    """
    scan = _scan(
        _base(
            nodes=_base()["nodes"] + [{"id": "p2", "kind": "pass"}],
            edges=[
                {"from": "a1", "to": "p1", "kind": "then"},
                {"from": "a1", "to": "p2", "kind": "then"},
            ],
        )
    )
    assert "DUPLICATE_EDGE_PRIORITY" in scan.codes()

    # 对照组：不同优先级的边可以共存
    ok = _scan(
        _base(
            nodes=_base()["nodes"] + [{"id": "p2", "kind": "pass"}],
            edges=[
                {"from": "a1", "to": "p1", "kind": "then"},
                {"from": "a1", "to": "p2", "kind": "yes"},
            ],
        )
    )
    assert "DUPLICATE_EDGE_PRIORITY" not in ok.codes()


def test_04_static_loop_without_termination():
    scan = _scan(
        _base(
            nodes=[
                {"id": "a1", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
                {"id": "s1", "kind": "set", "var": "x", "value": 1},
                {"id": "p1", "kind": "pass"},
            ],
            edges=[
                {"from": "a1", "to": "s1", "kind": "then"},
                {"from": "s1", "to": "a1", "kind": "then"},
            ],
        )
    )
    assert "STATIC_LOOP" in scan.codes()


def test_reserved_field_is_rejected():
    scan = _scan(
        _base(
            nodes=[
                {"id": "a1", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
                {"id": "f1", "kind": "set", "var": "x", "value": 1, "fn": {"lang": "lua", "body": "return 1"}},
                {"id": "p1", "kind": "pass"},
            ],
            edges=[
                {"from": "a1", "to": "f1", "kind": "then"},
                {"from": "f1", "to": "p1", "kind": "then"},
            ],
        )
    )
    assert "RESERVED_NOT_IMPLEMENTED" in scan.codes()


def test_10_snapshot_false_with_multi_and_is_warning():
    scan = _scan(
        _base(
            snapshot=False,
            nodes=[
                {"id": "a1", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
                {
                    "id": "i1",
                    "kind": "if",
                    "expr": {
                        "op": "and",
                        "args": [
                            {"op": "is_off", "value": {"var": "entity.light.a"}},
                            {"op": "is_on", "value": {"var": "entity.switch.b"}},
                        ],
                    },
                },
                {"id": "p1", "kind": "pass"},
            ],
            edges=[
                {"from": "a1", "to": "i1", "kind": "then"},
                {"from": "i1", "to": "p1", "kind": "then"},
            ],
        )
    )
    assert any(d.code == "SNAPSHOT_FALSE_MULTI_AND" for d in scan.warnings)
    assert scan.ok, "告警不拦截"


def test_13_nl_coverage_missing_node():
    """有节点没被自然语言描述覆盖 → 告警（防止"批准的与跑的不一致"）。"""
    scan = _scan(
        _base(
            nodes=[
                {"id": "a1", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
                {"id": "p1", "kind": "pass"},
                {"id": "orphan", "kind": "pass"},  # 没有任何边指向它
            ],
        )
    )
    assert any(d.code == "NL_COVERAGE" for d in scan.warnings)
