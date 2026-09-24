"""P1-13：CLI sim 命令 expect 失败时非 0 退出（之前恒 0）。"""

import json
from pathlib import Path

from typer.testing import CliRunner

from autoforge.af_cli import app, EXIT_OK, EXIT_SCAN_ERROR


runner = CliRunner()

CASE01 = Path(__file__).resolve().parents[2] / "examples" / "ir" / "case01_day_light.json"


def _copy_with_expect(tmp_path: Path, expect_state: str) -> Path:
    ir = json.loads(CASE01.read_text(encoding="utf-8"))
    ir["expect"] = [{"entity_id": "light.study_main", "state": expect_state}]
    p = tmp_path / f"expect_{expect_state}.json"
    p.write_text(json.dumps(ir, ensure_ascii=False), encoding="utf-8")
    return p


class TestSimExitCode:
    def test_sim_no_expect_returns_ok(self):
        result = runner.invoke(app, ["sim", str(CASE01)])
        assert result.exit_code == EXIT_OK, result.output

    def test_sim_expect_fail_returns_nonzero(self, tmp_path: Path):
        ir = _copy_with_expect(tmp_path, "on")
        result = runner.invoke(app, ["sim", str(ir)])
        assert result.exit_code == EXIT_SCAN_ERROR, result.output

    def test_sim_expect_pass_returns_ok(self, tmp_path: Path):
        ir = _copy_with_expect(tmp_path, "off")
        result = runner.invoke(app, ["sim", str(ir)])
        assert result.exit_code == EXIT_OK, result.output
