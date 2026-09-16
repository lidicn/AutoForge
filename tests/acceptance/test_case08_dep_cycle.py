"""G1 验收用例 8：A 开灯 → B 关灯 → A → **实体依赖图环检测报错**。

注意依赖矩阵只看**触发源**实体（不是条件里读到的实体），
否则"灯是关的 → 开灯"这类正常自动化会被误判成环。
"""

from __future__ import annotations

from typer.testing import CliRunner

from autoforge.af_cli import app
from autoforge.af_ir import load_graph
from autoforge.af_scanner import StaticScanner


def test_case08_build_is_blocked(examples_dir):
    result = CliRunner().invoke(app, ["build", str(examples_dir / "invalid_case08_cycle.json"), "--no-nl"])
    assert result.exit_code != 0
    assert "ENTITY_DEP_CYCLE" in result.output


def test_case08_cycle_detected(examples_dir):
    graph = load_graph(examples_dir / "invalid_case08_cycle.json")
    assert len(graph) == 2

    scan = StaticScanner(graph).scan()
    assert not scan.ok
    assert "ENTITY_DEP_CYCLE" in scan.codes()


def test_case08_normal_self_read_write_is_not_a_cycle(examples_dir):
    """对照组：case01 条件里读 light.study_main 又写它，但触发源是人体传感器 → 不成环。"""
    graph = load_graph(examples_dir / "case01_day_light.json")
    scan = StaticScanner(graph).scan()
    assert "ENTITY_DEP_CYCLE" not in scan.codes()
    assert scan.ok
