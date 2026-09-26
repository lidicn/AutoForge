"""af_scanner 单测：十项检查的拦截效果。"""

from __future__ import annotations

from pathlib import Path

from autoforge.af_ir import Graph, load_automation, load_graph
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


def test_p1_4_cross_ring_union_detected():
    """P1-4 union 检测：跨自动化混合环（实体边 + 事件边各贡献一半）。

    deps 图（B 写 X、A 触发 X → B→A）与 emit_deps 图（A emit E、B 触发 E → A→B）
    各自都不是环，但 union 后是环 A→B→A。因环上含 emit 事件解耦边，属良性
    双向同步模式（case_lamp_sync），P1-4 union 检测仍标 CROSS_DEP_CYCLE，但降级为
    WARNING，不阻断 forge build（放行可见，运行时靠状态收敛 + 熔断避免死循环）。
    """
    a = _base(
        id="a_emit",
        name="A：开关变化→广播",
        nodes=[
            {"id": "on1", "kind": "on", "trigger": {"type": "state", "entity_id": "switch.lamp_x", "to": "on"}},
            {"id": "e1", "kind": "pass", "name": "广播", "emit": {"event": "lamp_x_changed"}},
            {"id": "p1", "kind": "pass"},
        ],
        edges=[
            {"from": "on1", "to": "e1", "kind": "then"},
            {"from": "e1", "to": "p1", "kind": "then"},
        ],
    )
    b = _base(
        id="b_write",
        name="B：收到事件→写开关",
        nodes=[
            {"id": "onA", "kind": "on", "trigger": {"type": "event", "event": "lamp_x_changed"}},
            {"id": "dA", "kind": "do", "name": "写开关", "adapter": "ha", "action": "switch.turn_on", "params": {"entity_id": "switch.lamp_x"}, "result_var": "r_x"},
            {"id": "p2", "kind": "pass"},
        ],
        edges=[
            {"from": "onA", "to": "dA", "kind": "then"},
            {"from": "dA", "to": "p2", "kind": "then"},
        ],
    )
    scan = StaticScanner(Graph([load_automation(a), load_automation(b)])).scan()
    assert scan.ok, "emit 解耦混合环应放行（WARNING 不阻断编译）"
    assert "CROSS_DEP_CYCLE" in scan.codes()
    assert "CROSS_DEP_CYCLE" not in {d.code for d in scan.errors}


def test_p1_4_entity_cycle_blocked():
    """纯实体环（无 emit 解耦的直接互写）必须被 ENTITY_DEP_CYCLE 拦截。"""
    a = _base(
        id="a_write",
        name="A：X 变化→写 Y",
        nodes=[
            {"id": "on1", "kind": "on", "trigger": {"type": "state", "entity_id": "switch.lamp_x", "to": "on"}},
            {"id": "d1", "kind": "do", "name": "写 Y", "adapter": "ha", "action": "switch.turn_on", "params": {"entity_id": "switch.lamp_y"}, "result_var": "r_y"},
            {"id": "p1", "kind": "pass"},
        ],
        edges=[
            {"from": "on1", "to": "d1", "kind": "then"},
            {"from": "d1", "to": "p1", "kind": "then"},
        ],
    )
    b = _base(
        id="b_write",
        name="B：Y 变化→写 X",
        nodes=[
            {"id": "on2", "kind": "on", "trigger": {"type": "state", "entity_id": "switch.lamp_y", "to": "on"}},
            {"id": "d2", "kind": "do", "name": "写 X", "adapter": "ha", "action": "switch.turn_on", "params": {"entity_id": "switch.lamp_x"}, "result_var": "r_x"},
            {"id": "p2", "kind": "pass"},
        ],
        edges=[
            {"from": "on2", "to": "d2", "kind": "then"},
            {"from": "d2", "to": "p2", "kind": "then"},
        ],
    )
    scan = StaticScanner(Graph([load_automation(a), load_automation(b)])).scan()
    assert not scan.ok, "纯实体环必须被拦截"
    assert "ENTITY_DEP_CYCLE" in scan.codes()


def test_p1_4_normal_graph_no_false_cycle():
    """对照：正常非环 IR 不应被误判为跨环（防 P1-4 修复过度拦截）。

    用 case01（自读自写但触发源是人体传感器，而非被写的实体）→ 不构成环。
    注意：case_lamp_sync 这类 emit 解耦双向同步本质确为跨环，union 后会标记
    CROSS_DEP_CYCLE，但已是放行告警（不阻断编译）。
    """
    graph = load_graph(Path(__file__).parent.parent.parent / "examples" / "ir" / "case01_day_light.json")
    scan = StaticScanner(graph).scan()
    assert "CROSS_DEP_CYCLE" not in scan.codes()
    assert "ENTITY_DEP_CYCLE" not in scan.codes()
    assert "EMIT_SELF_LOOP" not in scan.codes()
