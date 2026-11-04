"""AF-718（v1.15.1）：except:pass 门基线键符号化判据（`file:line` ⇒ `file::外层限定名`）。

改键纪律（PM 派卡 2026-11-01，`plans/AF-Core卡_v1151_AF718与BOM_2026-11-01.md` §二）：
- ⛔ 手抄符号名：外层限定名按 AST 现读（嵌套链全名，与 check_atomic_write_sites 的
  `rel::qual`／homesdk AST 门 `.gates-baseline.txt` 的 `#函数` 指纹同口径）；
- ⛔ 机械替换：改键前后站点集合必须逐条相等（本文件 ② 腿即自证）；
- 条数与改前相等按**实例级**（15 枚）钉——`af_version.py::_now` 同函数 3 枚聚一键计 3，
  键级 13；"只减不增"改在实例级判定（同键枚数变多＝新增，仍要判红）；
- ⛔ 断言按符号/键形状钉，⛔ 不钉行号（行号漂移不再逼人重冻结）。
"""

from __future__ import annotations

import ast
import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "check_except_pass.py"

_spec = importlib.util.spec_from_file_location("check_except_pass", SCRIPT)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

#: 改前的 file:line 基线（15 枚，逐字摘自改前 BASELINE——站点集合相等性的对照面）
OLD_BASELINE = {
    "af_closedloop/deepfix.py:52", "af_conflict.py:680", "af_conflict_audit.py:150",
    "af_conflict_runtime.py:269", "af_conflict_runtime.py:279", "af_expect.py:141",
    "af_health.py:702", "af_orchestrator.py:893", "af_orchestrator.py:1098",
    "af_runtime_plugins.py:262", "af_time.py:202", "af_version.py:440",
    "af_version.py:447", "af_version.py:452", "af_version.py:465",
}


def test_baseline_keys_are_symbolic_with_counts():
    """①⑤ 键形状：`file::外层限定名 → 枚数`；⛔ 行号键；_now 聚一键计 3。"""
    assert mod.BASELINE, "基线不得为空"
    for key, n in mod.BASELINE.items():
        assert "::" in key, f"键缺符号段：{key}"
        sym = key.split("::", 1)[1]
        assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", sym), f"符号形状不对：{key}"
        assert not re.search(r":\d+$", key), f"键还带着行号：{key}"
        assert isinstance(n, int) and n >= 1, f"枚数必须是正整数：{key}"
    assert mod.BASELINE["af_version.py::_now"] == 3, "同函数 3 枚聚一键、枚数保实例级"
    assert mod.BASELINE["af_conflict_runtime.py::ConflictService.attach._uninstall"] == 1
    assert mod.BASELINE["af_runtime_plugins.py::install._tick_conflict"] == 1, "嵌套链全名（AST 现读）"
    assert len(mod.BASELINE) == 13, "13 键"
    assert sum(mod.BASELINE.values()) == len(OLD_BASELINE) == 15, "实例总数与改前基线相等"


def test_site_set_identical_to_old_file_line_baseline():
    """② 非机械替换自证：新扫描投影回 (文件, 行号) 与改前基线逐条相等。"""
    found, errors = mod.scan(REPO / "src" / "autoforge")
    assert not errors, errors
    projection = {f"{key.split('::', 1)[0]}:{ln}"
                  for key, lines in found.items() for ln in lines}
    assert projection == OLD_BASELINE, (
        f"站点集合漂移：仅新 {sorted(projection - OLD_BASELINE)} / 仅旧 {sorted(OLD_BASELINE - projection)}")
    # 符号是真值不是行号推的：同文件两枚不同符号各归各键
    assert "af_orchestrator.py::IntentParser.parse" in found
    assert "af_orchestrator.py::_extract_json" in found


def test_gate_exit_zero_with_new_key_format():
    """③ 新键格式实跑：真仓 exit=0，读数带键/枚两口径。"""
    r = subprocess.run([sys.executable, str(SCRIPT)], cwd=str(REPO),
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "except:pass 门禁干净" in r.stdout
    assert "13 键 / 15 枚" in r.stdout, r.stdout


def _copy_src(dst: Path) -> Path:
    shutil.copytree(REPO / "src" / "autoforge", dst / "autoforge")
    return dst


def test_new_site_in_fresh_function_still_red(tmp_path):
    """④-a 真新增（全新函数）⇒ 判红：A 腿没有因键形状改松。"""
    root = _copy_src(tmp_path / "src")
    target = root / "autoforge" / "af_time.py"
    inject = ("\n\ndef _af718_new_broad():\n"
              "    try:\n"
              "        1 / 0\n"
              "    except Exception:\n"
              "        pass\n")
    target.write_text(target.read_text(encoding="utf-8") + inject, encoding="utf-8")
    r = subprocess.run([sys.executable, str(SCRIPT), str(root)], cwd=str(REPO),
                       capture_output=True, text=True)
    assert r.returncode == 1, r.stdout
    assert "A 新增严格吞掉型站点" in r.stdout
    assert "af_time.py::_af718_new_broad" in r.stdout, r.stdout


def test_new_site_inside_baselined_symbol_still_red(tmp_path):
    """④-b 既有基线符号内新增一枚（_now 3→4）⇒ 仍红：实例级判定的核心腿。"""
    root = _copy_src(tmp_path / "src")
    target = root / "autoforge" / "af_version.py"
    text = target.read_text(encoding="utf-8")
    tree = ast.parse(text)
    now = next(n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_now")
    lines = text.splitlines(keepends=True)
    inject = "    try:\n        1 / 0\n    except Exception:\n        pass\n"
    lines.insert(now.end_lineno, inject + "\n")
    target.write_text("".join(lines), encoding="utf-8")
    r = subprocess.run([sys.executable, str(SCRIPT), str(root)], cwd=str(REPO),
                       capture_output=True, text=True)
    assert r.returncode == 1, r.stdout
    assert "af_version.py::_now" in r.stdout, "键内枚数 3→4 必须判红（⛔ 平键集合放走同函数新增）"
