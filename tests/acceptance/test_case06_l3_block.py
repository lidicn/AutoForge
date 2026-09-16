"""G1 验收用例 6：`http.post delete_all` → **编译期拦截**，不进仿真。

断言要求（docs/G1_ACCEPTANCE.md §2）：**不能只断言报错**，
必须同时证明 IR 没有进入 Runtime / 仿真——即 `forge build` 退出码非 0。
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from autoforge.af_adapters import HTTPAdapter
from autoforge.af_cli import app
from autoforge.af_ir import load_graph
from autoforge.af_scanner import StaticScanner


def test_case06_build_is_blocked(examples_dir):
    result = CliRunner().invoke(app, ["build", str(examples_dir / "invalid_case06_delete_all.json"), "--no-nl"])
    assert result.exit_code != 0, "L3 动作必须被安全闸拦下"
    assert "L3_ACTION" in result.output


def test_case06_diagnostic_code(examples_dir):
    graph = load_graph(examples_dir / "invalid_case06_delete_all.json")
    scan = StaticScanner(graph).scan()

    assert not scan.ok
    codes = scan.codes()
    assert "L3_ACTION" in codes
    assert "HTTP_NOT_WHITELISTED" in codes


def test_case06_runtime_second_line_of_defense():
    """即使绕过扫描，适配器运行期仍拒绝非白名单主机（纵深防御）。"""
    adapter = HTTPAdapter(allowed_hosts=(), dry_run=False)
    result = adapter.call("http.post", {"url": "https://api.example.com/delete_all"})
    assert result.success is False
    assert "白名单" in (result.error or "")
