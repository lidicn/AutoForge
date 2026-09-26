"""P1-4 跨自动化环：放行 emit 解耦混合环 + 拦截纯实体环。

- 混合环（含 emit 事件解耦边，如 case_lamp_sync 浓缩版 invalid_case_cross_ring.json）：
  union 检测仍标 CROSS_DEP_CYCLE，但降级为 WARNING，不阻断 forge build（放行可见）。
- 纯实体环（无 emit 解耦的直接互写，invalid_case_entity_cycle.json）：
  必须被 ENTITY_DEP_CYCLE 拦截（ERROR）。
"""

from pathlib import Path

from autoforge.af_ir import load_graph
from autoforge.af_scanner import StaticScanner


def test_cross_ring_released_not_blocked(examples_dir: Path):
    """混合环（emit 解耦）union 检测可见但不拦截：WARNING，放行。"""
    graph = load_graph(examples_dir / "invalid_case_cross_ring.json")
    assert len(graph) == 2

    scan = StaticScanner(graph).scan()
    assert scan.ok, "emit 解耦混合环应放行（WARNING 不阻断编译）"
    assert "CROSS_DEP_CYCLE" in scan.codes()
    assert "CROSS_DEP_CYCLE" not in {d.code for d in scan.errors}
    assert "CROSS_DEP_CYCLE" in {d.code for d in scan.warnings}


def test_entity_cycle_blocked(examples_dir: Path):
    """纯实体环（无 emit 解耦，直接互写）必须被 ENTITY_DEP_CYCLE 拦截。"""
    graph = load_graph(examples_dir / "invalid_case_entity_cycle.json")
    assert len(graph) == 2

    scan = StaticScanner(graph).scan()
    assert not scan.ok, "纯实体环必须被拦截"
    assert "ENTITY_DEP_CYCLE" in scan.codes()


def test_cross_ring_not_false_positive_on_benign(examples_dir: Path):
    """对照：正常非环 IR（case01 自读自写但触发源是人体传感器）不应被误判为环。"""
    graph = load_graph(examples_dir / "case01_day_light.json")
    scan = StaticScanner(graph).scan()
    assert "CROSS_DEP_CYCLE" not in scan.codes()
    assert "ENTITY_DEP_CYCLE" not in scan.codes()
    assert "EMIT_SELF_LOOP" not in scan.codes()
