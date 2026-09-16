"""G1 验收用例 7：适配器配置带 `retry=3` → 编译期拦截（违反纯执行层契约）。"""

from __future__ import annotations

from typer.testing import CliRunner

from autoforge.af_cli import app
from autoforge.af_ir import load_graph
from autoforge.af_scanner import StaticScanner


def test_case07_build_is_blocked(examples_dir):
    result = CliRunner().invoke(app, ["build", str(examples_dir / "invalid_case07_retry.json"), "--no-nl"])
    assert result.exit_code != 0, "适配器策略参数必须被拦下"
    assert "ADAPTER_POLICY_PARAM" in result.output


def test_case07_diagnostic_code(examples_dir):
    graph = load_graph(examples_dir / "invalid_case07_retry.json")
    scan = StaticScanner(graph).scan()
    assert not scan.ok
    assert "ADAPTER_POLICY_PARAM" in scan.codes()


def test_case07_whitelisted_params_are_allowed(examples_dir):
    """对照组：合法 IR（不带策略参数）不该被这项检查命中。"""
    graph = load_graph(examples_dir / "case01_day_light.json")
    scan = StaticScanner(graph).scan()
    assert "ADAPTER_POLICY_PARAM" not in scan.codes()
