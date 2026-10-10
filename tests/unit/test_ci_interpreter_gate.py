"""CI 解释器口径门必须"能变红"（铁律 #8），红的是**下一处手抄漂移或无人认领的差**，不是已经对上的那些。

背景（第六轮审计 ARCH-03 / §二之九十七）：`ci.yml` 的四个 Python 作业把解释器手抄了四遍，包声明是
`requires-python = ">=3.11"`，两份镜像 base 是 3.14，README 快速开始明写「需要 Python 3.14+」。
其中两对关系必须成立（四枚手抄彼此相等、CI 钉值被包声明允许），一对当前不成立且**不由本门拍板**
（CI 3.11 vs 镜像 3.14——那是交付/验证口径，已列待裁）。所以本门只判形状：A 手抄一致 / B 满足包声明 /
C 漂移必须挂着锚点核对得住的显式登记。

反面样本一律用 tmp 树，不动仓内真文件；`test_real_repo_*` 三条是对当前仓库的实测（真绿），它们保证
本门不是"只对自己的样本有效"的纸门，也保证登记里那两格的理由在**真仓**上真的能过锚点核对。
"""
from __future__ import annotations

import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_ci_interpreter.py"

WORKFLOW = '''name: CI

on:
  push:
    branches: [main, master, dev]
  pull_request:

jobs:
  test:
    name: pytest
    runs-on: ubuntu-latest
    steps:
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
  contracts:
    name: adm-linkage-contracts
    runs-on: ubuntu-latest
    steps:
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
  ui:
    name: ui-typecheck-build
    runs-on: ubuntu-latest
    steps:
      - uses: actions/setup-node@v4
'''

PYPROJECT = '''[project]
name = "autoforge"
requires-python = ">=3.11"
dependencies = ["jsonschema>=4.20"]
'''

API = 'FROM python:3.14-slim\nRUN pip install --no-cache-dir -e ".[api]"\n'
TEST_IMAGE = 'FROM python:3.14-slim\nRUN pip install --no-cache-dir -e ".[dev]"\n'

# tmp 树用的登记：理由里点一个带钉值的作业（`test` / 显示名 `pytest`）+ 一个盘上真在的路径。
DRIFT = {
    "docker/Dockerfile.api": "`test` 钉 3.11，镜像 base 是 3.14（`docker/Dockerfile.api`），口径差已列待裁。",
    "docker/Dockerfile.test": "`pytest` 钉 3.11，测试镜像 base 是 3.14（`docker/Dockerfile.test`），同上一条待裁。",
}

PATHS = {
    "pyproject.toml": PYPROJECT,
    "docker/Dockerfile.api": API,
    "docker/Dockerfile.test": TEST_IMAGE,
    ".github/workflows/ci.yml": WORKFLOW,
}


def _module():
    spec = importlib.util.spec_from_file_location("check_ci_interpreter", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tree(tmp_path: pathlib.Path, **over: str | None) -> pathlib.Path:
    files = dict(PATHS)
    files.update(over)
    for name, text in files.items():
        if text is None:
            continue
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def _check(tmp_path: pathlib.Path, drift: dict[str, str] | None = DRIFT, **over: str | None):
    mod = _module()
    if drift is not None:
        mod.INTERPRETER_DRIFT = drift
    return mod.check(_tree(tmp_path, **over))


def _anchor(tmp_path: pathlib.Path, **over: str | None):
    return _module().anchor_ok(_tree(tmp_path, **over))


# ── 真仓实测：本门对当前仓库有射程，且此刻是真绿 ─────────────────────────

def test_real_repo_anchor_readable():
    assert _module().anchor_ok(ROOT) is None


def test_real_repo_is_actually_green():
    mod = _module()
    findings, info = mod.check(ROOT)
    assert findings == [], f"真仓读数不应为空却给出：{findings}"
    assert info["jobs"] == 6, f"ci.yml 作业数现读为 {info['jobs']}"
    assert len(info["pins"]) == 4, f"`python-version:` 钉值现读 {info['pins']}"
    assert info["values"] == ["3.11"], f"四枚手抄的现读取值 {info['values']}"
    assert info["requires_python"] == ">=3.11"
    assert info["images"] == {
        "docker/Dockerfile.api": "3.14",
        "docker/Dockerfile.test": "3.14",
    }, f"两份镜像 base 现读 {info['images']}"


def test_real_repo_drift_register_matches_drift():
    """登记必须**恰好**覆盖当前在漂移的那两面——多一格是过期，少一格是无人认领。"""
    mod = _module()
    _findings, info = mod.check(ROOT)
    drifted = {rel for rel, base in info["images"].items() if base != info["values"][0]}
    assert drifted == set(mod.INTERPRETER_DRIFT), f"漂移面 {drifted} 与登记表 {set(mod.INTERPRETER_DRIFT)} 不等"
    jobs, _pins = mod._read_jobs(ROOT / ".github" / "workflows" / "ci.yml")
    for rel, reason in mod.INTERPRETER_DRIFT.items():
        err, job_hits, path_hits = mod._anchors_ok(ROOT, reason, jobs)
        assert err is None, f"`{rel}` 的理由不合格：{err}"
        assert job_hits and path_hits


# ── 判据 A：手抄一致 ─────────────────────────────────────────────────

def test_jobs_section_is_the_only_job_source(tmp_path):
    """`on:` 下的 `  push:`／`  pull_request:` 也是两格缩进加冒号，不能被数成作业。

    样本里有 3 个作业（其中只有 2 个带钉值）；把触发器算进来会得到 5——本批写第一版时就把真仓
    的 6 个作业数成了 8，那个错读数会直接印进结论里当证据用。
    """
    _findings, info = _check(tmp_path)
    assert info["jobs"] == 3, f"作业数读数 {info['jobs']}（样本里是 test/contracts/ui 三个作业）"
    assert len(info["pins"]) == 2


def test_single_pin_drift_goes_red(tmp_path):
    """改一处忘三处：第二个作业的钉值被抬走，本门必须当场红。"""
    changed = WORKFLOW.replace('          python-version: "3.11"\n  ui:', '          python-version: "3.12"\n  ui:')
    assert changed != WORKFLOW, "锚点没命中，样本写法变了"
    findings, info = _check(tmp_path, drift=DRIFT, **{".github/workflows/ci.yml": changed})
    assert len(findings) == 1, f"只该咬到判据 A，实际 {findings}"
    assert "不一致" in findings[0]
    assert info["values"] == ["3.11", "3.12"]


# ── 判据 B：CI 钉值满足包声明 ────────────────────────────────────────

def test_raised_floor_goes_red(tmp_path):
    """把包下限抬到 3.14（例如与镜像对齐）而 CI 钉值没跟上 ⇒ 红。"""
    findings, _info = _check(tmp_path, **{"pyproject.toml": '[project]\nrequires-python = ">=3.14"\n'})
    assert len(findings) == 1 and "不满足包声明" in findings[0], findings


def test_unparseable_specifier_is_reported_not_skipped(tmp_path):
    findings, _info = _check(tmp_path, **{"pyproject.toml": '[project]\nrequires-python = "== 3.*"\n'})
    assert len(findings) == 1 and "认不出" in findings[0], findings


def test_compatible_release_operator_supported():
    mod = _module()
    assert mod._satisfied("3.11", "~=3.11") is True
    assert mod._satisfied("3.13", "~=3.11") is True
    assert mod._satisfied("4.0", "~=3.11") is False
    assert mod._satisfied("3.11", ">=3.11,<4.0") is True
    assert mod._satisfied("3.11", ">=3.12") is False


# ── 判据 C：漂移必须显式登记，且理由核对得住 ─────────────────────────

def test_unregistered_drift_goes_red(tmp_path):
    findings, _info = _check(tmp_path, drift={})
    assert len(findings) == 2, f"两份镜像都在漂移却没登记，该各咬一条：{findings}"
    assert all("没人认领" in f for f in findings), findings


def test_stale_register_entry_goes_red(tmp_path):
    """CI 与镜像已经对齐了却还挂着登记 ⇒ 豁免过期。"""
    findings, _info = _check(
        tmp_path,
        drift=DRIFT,
        **{"docker/Dockerfile.api": 'FROM python:3.11-slim\nRUN pip install -e ".[api]"\n'},
    )
    assert len(findings) == 1 and "已过期" in findings[0], findings


def test_empty_reason_goes_red(tmp_path):
    findings, _info = _check(tmp_path, drift={**DRIFT, "docker/Dockerfile.api": "   "})
    assert len(findings) == 1 and "理由为空" in findings[0], findings


def test_reason_naming_a_python_version_free_job_goes_red(tmp_path):
    """拉一个不相干的真作业当掩护（Node 作业没有钉值）过不了。"""
    reason = "`ui-typecheck-build` 钉的是 3.11，见 `docker/Dockerfile.api`。"
    findings, _info = _check(tmp_path, drift={**DRIFT, "docker/Dockerfile.api": reason})
    assert len(findings) == 1 and "本体带" in findings[0], findings


def test_reason_with_made_up_path_goes_red(tmp_path):
    reason = "`test` 钉 3.11，口径差落在 `docker/Dockerfile.never-existed`。"
    findings, _info = _check(tmp_path, drift={**DRIFT, "docker/Dockerfile.api": reason})
    assert len(findings) == 1 and "盘上真实存在" in findings[0], findings


def test_line_anchor_beyond_eof_goes_red(tmp_path):
    """写成 `路径:行号` 时那一行必须真存在——行号是编的就判红。"""
    reason = "`test` 钉 3.11，见 `docker/Dockerfile.api:9999`。"
    findings, _info = _check(tmp_path, drift={**DRIFT, "docker/Dockerfile.api": reason})
    assert len(findings) == 1 and "盘上真实存在" in findings[0], findings


def test_rewording_the_reason_does_not_move_the_reading(tmp_path):
    """同义改写（锚点都在）不该让门变红——本门判形状，不判措辞。"""
    reason = "`pytest` 这条链跑 3.11，镜像 base 却是 3.14（`docker/Dockerfile.api`），差由 ARCH-03 待裁。"
    findings, _info = _check(tmp_path, drift={**DRIFT, "docker/Dockerfile.api": reason})
    assert findings == [], findings


# ── 射程塌了不许报干净 ───────────────────────────────────────────────

def test_no_pins_at_all_is_exit_two_not_green(tmp_path):
    workflow = WORKFLOW.replace('          python-version: "3.11"\n', "")
    assert _anchor(tmp_path, **{".github/workflows/ci.yml": workflow}) is not None


def test_missing_jobs_section_is_exit_two(tmp_path):
    workflow = WORKFLOW.replace("jobs:\n", "jobz:\n")
    assert _anchor(tmp_path, **{".github/workflows/ci.yml": workflow}) is not None


def test_image_base_without_python_from_is_exit_two(tmp_path):
    assert _anchor(tmp_path, **{"docker/Dockerfile.api": 'FROM node:20-slim\n'}) is not None


def test_missing_pyproject_floor_is_exit_two(tmp_path):
    assert _anchor(tmp_path, **{"pyproject.toml": '[project]\nname = "autoforge"\n'}) is not None
