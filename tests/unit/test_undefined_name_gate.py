"""新门禁必须自带"已实测能变红"的反例（铁律 §五 #8：能变红的门禁才是门禁）。

`scripts/check_undefined_names.py` 是本轮为审计 REG-3 补的那一层。它第一次跑就抓到两处
真实缺陷（`af_scheduler` 少了 `INSTANCE_DEBOUNCED` 的 import、`af_time` 用了没 import 的 `Any`），
但"第一次跑出东西"不等于"以后也能报红"——所以这里把报红路径本身钉成测试。
"""
from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_undefined_names.py"


def _module():
    spec = importlib.util.spec_from_file_location("check_undefined_names", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write(tmp_path: pathlib.Path, name: str, body: str) -> pathlib.Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def test_flags_a_name_that_is_never_bound(tmp_path):
    bad = _write(tmp_path, "bad.py", "def f():\n    return logger.warning('x')\n")
    findings = _module().check_file(bad)
    assert len(findings) == 1 and "logger" in findings[0]


def test_flags_the_real_incident_shape(tmp_path):
    """本轮自己踩到的形态：模块顶层引用了尚未定义的常量 → import 期就 NameError。"""
    bad = _write(
        tmp_path, "const.py",
        "PREFIX = 'HOMESDK_'\nKEYS = tuple(f'{PREFIX_MISSING}{b}' for b in ('TZ',))\n",
    )
    findings = _module().check_file(bad)
    assert any("PREFIX_MISSING" in f for f in findings), findings


def test_bound_names_across_every_binding_form_are_silent(tmp_path):
    good = _write(tmp_path, "good.py", """
import os as environment
from typing import Any
class C:
    attr: Any = 1
def f(a, *rest, **kw) -> str:
    for i, (x, y) in enumerate(rest):
        with open('p') as fh:
            try:
                value = environment.getenv('V')
            except OSError as exc:
                value = str(exc)
            [q for q in value if q]
            match a:
                case {'k': kk, **others}:
                    return kk
    return f"{i}{x}{y}{value}{fh}{C.attr}"
lambda z: z
""")
    assert _module().check_file(good) == []


def test_class_body_forward_reference_is_not_reported(tmp_path):
    """类体内互相引用的名字整份文件里都绑过，不该被这条门禁误报。"""
    body = _write(tmp_path, "fwd.py", "class A:\n    X = 1\n    Y = X + 1\n")
    assert _module().check_file(body) == []


def test_gate_exits_nonzero_when_something_is_undefined(tmp_path):
    """门禁的价值在退出码：只会打印不会红的门禁等于没有门禁。"""
    _write(tmp_path, "bad.py", "def f():\n    return nope()\n")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_path)], capture_output=True, text=True, encoding="utf-8"
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "nope" in result.stdout


def test_gate_is_clean_over_the_real_source_tree():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "src"], capture_output=True, text=True, encoding="utf-8", cwd=ROOT
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_gate_runs_on_an_interpreter_without_ast_typealias(tmp_path):
    """门禁自己必须能在 3.11 上跑——CI 用的就是 3.11。

    `ast.TypeAlias` 是 3.12 才有的节点：裸引用它的门禁在 3.11 上 AttributeError 直接崩，
    而 gates.sh 把"脚本崩了"报成"undefined-name 门禁红（src=1 / tests=1）"——
    红因写错地方，且 CI 从此永久红（2026-10-02 CI 实测：run 13–26 全 failed）。
    本机是 3.13，所以这里真把那个属性删掉，模拟 3.11 的 ast 面。
    """
    _write(tmp_path, "good.py", "x = 1\nprint(x)\n")
    runner = tmp_path / "runner.py"
    runner.write_text(
        "import ast, sys, importlib.util\n"
        "if hasattr(ast, 'TypeAlias'):\n"
        "    del ast.TypeAlias  # 3.11：这个属性从来不存在\n"
        "spec = importlib.util.spec_from_file_location('gate', sys.argv[1])\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(mod)\n"
        "sys.exit(mod.main(['gate', sys.argv[2]]))\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(runner), str(SCRIPT), str(tmp_path)],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "AttributeError" not in result.stderr
