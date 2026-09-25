"""P1-4 跨自动化混合环：CLI 构建期拦截 + 扫描检测（与 test_case08_dep_cycle 同款）。

盲区拓扑：A 由实体状态变化触发并 emit 事件，B 由该事件触发并写同一实体。
deps 图与 emit_deps 图各自都不是环，但 union 后是环，必须被 CROSS_DEP_CYCLE 拦截。
"""

from pathlib import Path

from click.testing import CliRunner

from autoforge.af_cli import app
from autoforge.af_ir import load_graph
from autoforge.af_scanner import StaticScanner


def test_cross_ring_detected(examples_dir: Path):
    graph = load_graph(examples_dir / "invalid_case_cross_ring.json")
    assert len(graph) == 2

    scan = StaticScanner(graph).scan()
    assert not scan.ok
    assert "CROSS_DEP_CYCLE" in scan.codes()


def test_cross_ring_not_false_positive_on_benign(examples_dir: Path):
    """对照：正常非环 IR（case01 自读自写但触发源是人体传感器）不应被误判为环。

    注意：case_lamp_sync 这类 emit 解耦双向同步本质确为跨环，union 后会标记
    CROSS_DEP_CYCLE（P1-4 盲区补全的预期副作用），故此处用 case01 做非环对照。
    """
    graph = load_graph(examples_dir / "case01_day_light.json")
    scan = StaticScanner(graph).scan()
    assert "CROSS_DEP_CYCLE" not in scan.codes()
    assert "ENTITY_DEP_CYCLE" not in scan.codes()
    assert "EMIT_SELF_LOOP" not in scan.codes()
