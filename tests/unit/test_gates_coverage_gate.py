"""门禁装配覆盖门必须**能变红**（铁律 #8），且红的必须是"下一条没接上的链"。

起因（§二之四十五）：裁定 20261005 要求的那条 IR 运行时键硬门只写在 `ci.yml` 的 `quality-gates` 作业
（`bash gates.sh` 的**下一步**、前置完全相同），`gates.sh` 里没有 ⇒ 本机绿、推上去 CI 红。那种不对称
不会让任何东西变红，只会让"该红的不红"。本门把"盘上的门 = 某条链真跑过的门"钉成静态判据。

五条判据各自单独可红：① 漏跑（盘上有、两条链都不跑）② 远端有本机没有（工作流引用而 `gates.sh` 没跑、
又没豁免）③ 豁免过期（登记了却没工作流引用）④ 豁免空理由 ⑤ 豁免理由的锚点核对不住（点名的作业没在引用
这个脚本 / 没有路径锚点 / 路径是编的）。反空洞档同样单独可红：读不到 `gates.sh`／读不到 `workflows/`／
盘上 0 个脚本／`gates.sh` 里 0 条调用／引用了盘上不存在的脚本／工作流数不出任何一个 job ⇒ 一律 `exit 2`，
"没有发现"不等于"没有问题"。

本文件最要害的一族是**注释不算覆盖**：往工作流里加一行 `# 见 check_x.py`、或把 `gates.sh` 的调用行
改成任何非 `"$REPO/scripts/…"` 形状，都等于把那道门从链上摘掉——所以 ⑥⑦⑧ 三条腿专门验"只有注释／
只有非引号形状"时门必须红，而不是把那句注释读成覆盖。
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import pathlib
import re
import sys
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_gates_coverage.py"

GATES_A = '''#!/usr/bin/env bash
set -e
"$PYTHON" "$REPO/scripts/check_a.py"
'''

CI_ONLY_B = '''name: ci
on: push
jobs:
  layering:
    steps:
      # check_b.py 只在注释里出现，不算覆盖
      - name: gate
        run: python scripts/check_b.py
'''

#: 合成树用的最小工作流：不含任何 `check_*.py` 引用，也不缺目录（缺目录会先撞射程塌那档）。
WF_MIN = "name: ci\non: push\njobs:\n  q:\n    steps:\n      - run: bash gates.sh\n"

#: 判据⑤ 合格理由的形状：反引号点名的作业**本体真引用了这个脚本**（`layering` 作业里有 `check_b.py`），
#: 外加一个**盘上真存在**的路径锚点（`_tree` 会在沙箱根写 `anchor.txt`，并把 `PATH_ROOT` 指过去）。
GOOD_REASON = "跑在 `layering` 独立作业：前置差见 `anchor.txt`"

#: 哨兵：这一档要的是「workflows/ 目录根本不存在」，不能和 None（= 用默认最小工作流）混用。
NO_DIR = object()


def _module():
    spec = importlib.util.spec_from_file_location("check_gates_coverage", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(mod, argv: tuple[str, ...] = ()) -> tuple[int, str]:
    """`main()` 读 `sys.argv`（argparse），跑之前把 pytest 的那份 argv 换掉。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), mock.patch.object(
        sys, "argv", ["check_gates_coverage.py", *argv]
    ):
        rc = mod.main()
    return rc, buf.getvalue()


def _tree(
    tmp_path: pathlib.Path,
    monkeypatch,
    *,
    scripts: tuple[str, ...] = ("check_a.py",),
    gates_sh: str | None = GATES_A,
    workflows: dict[str, str] | None = None,
    exempt: dict[str, str] | None = None,
) -> None:
    """搭一棵假仓：scripts/ + gates.sh + .github/workflows/，然后把模块的射程指过去。"""
    if workflows is None:
        workflows = {"ci.yml": WF_MIN}
    if exempt is None:
        exempt = {}
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    for name in scripts:
        (scripts_dir / name).write_text("# gate\n", encoding="utf-8")
    gates = tmp_path / "gates.sh"
    if gates_sh is not None:
        gates.write_text(gates_sh, encoding="utf-8")
    wf_dir = tmp_path / ".github" / "workflows"
    if workflows is not NO_DIR:
        wf_dir.mkdir(parents=True, exist_ok=True)
        for name, text in workflows.items():
            (wf_dir / name).write_text(text, encoding="utf-8")
    mod = _module()
    # 判据⑤ 的路径锚点按 PATH_ROOT 核对：沙箱树里写一个真文件，并把根指到沙箱。
    (tmp_path / "anchor.txt").write_text("前置差锚点（合成）\n", encoding="utf-8")
    for attr, value in (
        ("GATES_SH", gates),
        ("WORKFLOWS", wf_dir),
        ("SCRIPTS", scripts_dir),
        ("PATH_ROOT", tmp_path),
    ):
        monkeypatch.setattr(mod, attr, value)
    if exempt is not None:
        monkeypatch.setattr(mod, "CI_ONLY_EXEMPT", exempt)
    # 让 _run 用的就是这个被改过射程的模块实例。
    _tree.mod = mod


def _green_tree(tmp_path, monkeypatch):
    """check_a 在 gates.sh、check_b 只在工作流且有豁免理由 ⇒ 应当干净。"""
    _tree(
        tmp_path,
        monkeypatch,
        scripts=("check_a.py", "check_b.py"),
        gates_sh=GATES_A,
        workflows={"ci.yml": CI_ONLY_B},
        exempt={"check_b.py": GOOD_REASON},
    )
    return _tree.mod


# ── 判据 ①：漏跑 ────────────────────────────────────────────────────

def test_unwired_script_on_disk_goes_red(tmp_path, monkeypatch):
    _tree(tmp_path, monkeypatch, scripts=("check_a.py", "check_orphan.py"))
    rc, out = _run(_tree.mod)
    assert rc == 1
    assert "漏跑" in out and "check_orphan.py" in out


def test_green_tree_with_exempt_ci_only_script_passes(tmp_path, monkeypatch):
    mod = _green_tree(tmp_path, monkeypatch)
    rc, out = _run(mod)
    assert rc == 0, out
    assert "覆盖门干净" in out


# ── 判据 ②：远端有、本机没有 ───────────────────────────────────────

def test_workflow_only_ref_without_exempt_goes_red(tmp_path, monkeypatch):
    _tree(
        tmp_path,
        monkeypatch,
        scripts=("check_a.py", "check_b.py"),
        gates_sh=GATES_A,
        workflows={"ci.yml": CI_ONLY_B},
        exempt={},
    )
    rc, out = _run(_tree.mod)
    assert rc == 1
    assert "远端有、本机没有" in out and "check_b.py" in out


# ── 判据 ③④：豁免过期 / 空理由 ────────────────────────────────────

def test_stale_exempt_goes_red(tmp_path, monkeypatch):
    mod = _green_tree(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "CI_ONLY_EXEMPT", {"check_b.py": "跑在独立作业：前置不同", "check_gone.py": "已并入 gates.sh"})
    rc, out = _run(mod)
    assert rc == 1
    assert "豁免过期" in out and "check_gone.py" in out


def test_exempt_with_empty_reason_goes_red(tmp_path, monkeypatch):
    mod = _green_tree(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "CI_ONLY_EXEMPT", {"check_b.py": "   "})
    rc, out = _run(mod)
    assert rc == 1
    assert "豁免没理由" in out


# ── 要害档：注释不算覆盖 ──────────────────────────────────────────

def test_comment_only_mention_in_workflow_is_not_coverage(tmp_path, monkeypatch):
    """工作流只在注释里提到 check_b.py ⇒ 那条门其实没接，必须判红而不是算成"远端覆盖"。"""
    yml = """name: ci
on: push
jobs:
  q:
    steps:
      - name: gates
        run: bash gates.sh
      # 下一版要接 python scripts/check_b.py
"""
    _tree(
        tmp_path,
        monkeypatch,
        scripts=("check_a.py", "check_b.py"),
        gates_sh=GATES_A,
        workflows={"ci.yml": yml},
        exempt={},
    )
    rc, out = _run(_tree.mod)
    assert rc == 1
    assert "漏跑" in out and "check_b.py" in out


def test_comment_only_mention_does_not_become_exit2(tmp_path, monkeypatch):
    """注释里的名字不进 `unknown`：否则"只在注释里出现的脚本"会把整门变成射程塌（exit 2）而掩盖真红。"""
    yml = """name: ci
on: push
jobs:
  q:
    steps:
      # 见 check_a.py 与本门 check_gates_coverage.py
      - name: gates
        run: bash gates.sh
"""
    _tree(
        tmp_path,
        monkeypatch,
        scripts=("check_a.py",),
        gates_sh=GATES_A,
        workflows={"ci.yml": yml},
        exempt={},
    )
    rc, out = _run(_tree.mod)
    assert rc == 0, out


def test_unquoted_call_shape_in_gates_sh_is_not_coverage(tmp_path, monkeypatch):
    gates = '''"$PYTHON" "$REPO/scripts/check_a.py"
$PYTHON $REPO/scripts/check_b.py
'''
    _tree(
        tmp_path,
        monkeypatch,
        scripts=("check_a.py", "check_b.py"),
        gates_sh=gates,
        workflows={"ci.yml": WF_MIN},
        exempt={},
    )
    rc, out = _run(_tree.mod)
    assert rc == 1
    assert "check_b.py" in out


def test_real_ci_yml_comment_naming_a_script_does_not_count_as_ci_coverage():
    """本仓真读数的反空洞档：`ci.yml` 的注释里确实写了 `check_gates_coverage.py`，
    而它同时在 `gates.sh` 有真调用行 ⇒ 覆盖来源必须是 `gates.sh`，不能是那句注释。"""
    mod = _module()
    on_disk, gates_refs, ci_refs = mod.collect()
    assert "check_gates_coverage.py" in gates_refs
    assert "check_gates_coverage.py" not in ci_refs


# ── 反空洞：射程塌了 ⇒ exit 2 ─────────────────────────────────────

def test_missing_gates_sh_exits_2(tmp_path, monkeypatch):
    _tree(tmp_path, monkeypatch, gates_sh=None)
    rc, out = _run(_tree.mod)
    assert rc == 2 and "读不到" in out


def test_missing_workflows_dir_exits_2(tmp_path, monkeypatch):
    _tree(tmp_path, monkeypatch, workflows=NO_DIR)
    rc, out = _run(_tree.mod)
    assert rc == 2 and "读不到" in out


def test_zero_scripts_on_disk_exits_2(tmp_path, monkeypatch):
    _tree(tmp_path, monkeypatch, scripts=())
    rc, out = _run(_tree.mod)
    assert rc == 2 and "都没数到" in out


def test_gates_sh_with_no_call_shape_exits_2(tmp_path, monkeypatch):
    _tree(tmp_path, monkeypatch, scripts=("check_a.py",), gates_sh="# 装配口径改了\n")
    rc, out = _run(_tree.mod)
    assert rc == 2 and "都没数到" in out


def test_ref_to_absent_script_exits_2(tmp_path, monkeypatch):
    """引用了盘上不存在的门 = 那一行是死步骤，报"干净"没有依据。"""
    _tree(
        tmp_path,
        monkeypatch,
        scripts=("check_a.py",),
        gates_sh=GATES_A + '"$PYTHON" "$REPO/scripts/check_ghost.py"\n',
        workflows={"ci.yml": CI_ONLY_B},
    )
    rc, out = _run(_tree.mod)
    assert rc == 2 and "check_ghost.py" in out


# ── 真仓读数 ──────────────────────────────────────────────────────

def test_self_test_passes_on_the_real_detector(tmp_path, monkeypatch):
    mod = _green_tree(tmp_path, monkeypatch)
    rc, out = _run(mod, ("--self-test",))
    assert rc == 0 and "全部被检出" in out, out


def test_self_test_fails_when_detector_is_blind(tmp_path, monkeypatch):
    """反空洞的反空洞：`--self-test` 必须能验出一个永远返回干净的检测器本体。"""
    mod = _green_tree(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "check", lambda *a, **k: [])
    rc, out = _run(mod, ("--self-test",))
    assert rc == 1 and "检测器失效" in out, out


def test_real_repo_reading_is_clean_and_nontrivial():
    mod = _module()
    on_disk, gates_refs, ci_refs = mod.collect()
    jobs = mod.collect_jobs()
    assert mod.check(on_disk, gates_refs, ci_refs, jobs) == []
    # 反空洞：真仓的门数量必须像样，否则"干净"是空集给的干净。
    assert len(on_disk) >= 15
    assert len(on_disk & gates_refs) >= 15
    assert "check_ir_runtime_keys.py" in gates_refs  # 裁定 20261005 那条硬门，本机链上也得在
    assert jobs, "工作流数不出任何 job：判据⑤ 没有射程，本门的『干净』包括豁免语义核对在内都没依据"


def test_ci_only_exempt_entries_all_have_reasons_and_are_referenced():
    """每一格豁免：有理由、被工作流引用、在盘上，且**理由的两个锚点当场核对得住**（判据⑤）。

    本门原先只能判"有没有写字"，那句"跑在别的作业里"当年就混得过；现在锚点是现取的：
    作业名来自 YAML（不建第二份名单），且那个作业本体必须真引用了这个脚本；路径要在盘上。
    """
    mod = _module()
    on_disk, _gates_refs, ci_refs = mod.collect()
    jobs = mod.collect_jobs()
    assert mod.CI_ONLY_EXEMPT  # 空表就等于没有豁免口径，本门只验形状不验语义
    for name, reason in mod.CI_ONLY_EXEMPT.items():
        assert reason.strip(), name
        assert name in ci_refs, name
        assert name in on_disk, name
        assert mod._reason_problems(name, reason, jobs) == [], name


# ── 判据 ⑤：豁免理由的锚点必须核对得住 ─────────────────────────────

def test_reason_naming_an_unrelated_job_goes_red(tmp_path, monkeypatch):
    """真存在、但不引用这个脚本的作业名——拉个不相干的作业当掩护，过不了。"""
    mod = _green_tree(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "CI_ONLY_EXEMPT", {"check_b.py": "跑在 `q` 独立作业：前置差见 `anchor.txt`"})
    rc, out = _run(mod)
    assert rc == 1
    assert "没点名一个**真的在引用它**的作业" in out, out


def test_reason_naming_a_fabricated_job_goes_red(tmp_path, monkeypatch):
    mod = _green_tree(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "CI_ONLY_EXEMPT", {"check_b.py": "跑在 `ghost-job` 独立作业：前置差见 `anchor.txt`"})
    rc, out = _run(mod)
    assert rc == 1
    assert "没点名一个**真的在引用它**的作业" in out, out


def test_reason_citing_an_absent_path_goes_red(tmp_path, monkeypatch):
    """本批的真实猎物：旧理由里的 `.gates-imports-baseline.txt` 盘上从来没有过。"""
    mod = _green_tree(tmp_path, monkeypatch)
    monkeypatch.setattr(
        mod, "CI_ONLY_EXEMPT",
        {"check_b.py": "跑在 `layering` 独立作业：判据是 grimp 的包图 + `.gates-imports-baseline.txt`"},
    )
    rc, out = _run(mod)
    assert rc == 1
    assert "锚点是编的" in out and ".gates-imports-baseline.txt" in out, out


def test_reason_without_any_path_anchor_goes_red(tmp_path, monkeypatch):
    mod = _green_tree(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "CI_ONLY_EXEMPT", {"check_b.py": "跑在 `layering` 独立作业：前置不同"})
    rc, out = _run(mod)
    assert rc == 1
    assert "缺前置差锚点" in out, out


def test_reason_may_cite_the_job_display_name(tmp_path, monkeypatch):
    """`name:` 与 job id 同源同权：写显示名也算数（本仓 `ci.yml` 的实际书写习惯）。"""
    yml = """name: ci
on: push
jobs:
  layering:
    name: layering-gates
    steps:
      - name: gate
        run: python scripts/check_b.py
"""
    mod = _tree(
        tmp_path,
        monkeypatch,
        scripts=("check_a.py", "check_b.py"),
        gates_sh=GATES_A,
        workflows={"ci.yml": yml},
        exempt={"check_b.py": "跑在 `layering-gates` 独立作业：前置差见 `anchor.txt`"},
    ) or _tree.mod
    rc, out = _run(mod)
    assert rc == 0, out


def test_zero_jobs_in_workflows_collapses_the_range(tmp_path, monkeypatch):
    """工作流里没有 `jobs:` 段 ⇒ 判据⑤ 没有射程，一律 exit 2 而不是报干净。"""
    yml = "name: ci\non: push\nsteps:\n  - run: python scripts/check_a.py\n"
    _tree(
        tmp_path,
        monkeypatch,
        scripts=("check_a.py",),
        gates_sh=GATES_A,
        workflows={"ci.yml": yml},
    )
    rc, out = _run(_tree.mod)
    assert rc == 2, out
    assert "数不出任何一个 job" in out, out


def test_collect_jobs_is_taken_from_yaml_not_a_hand_list():
    """锚点的唯一真源是工作流本身：作业数与 `ci.yml` 里 `jobs:` 的条目数一致。"""
    mod = _module()
    jobs = mod.collect_jobs()
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    declared = re.findall(r"^  ([A-Za-z0-9_.-]+):\s*$", text.split("jobs:", 1)[1], re.M)
    assert declared and set(declared) <= set(jobs), (declared, sorted(jobs))
    # 反向也核：只有 `architecture` 真引用 check_imports.py，别的服务作业不许被算成它的覆盖。
    runners = sorted(jid for jid, (_n, refs) in jobs.items() if "check_imports.py" in refs)
    assert runners == ["architecture"], runners
