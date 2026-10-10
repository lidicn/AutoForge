"""门禁装配覆盖门必须**能变红**（铁律 #8），且红的必须是"下一条没接上的链"。

起因（§二之四十五）：裁定 20261005 要求的那条 IR 运行时键硬门只写在 `ci.yml` 的 `quality-gates` 作业
（`bash gates.sh` 的**下一步**、前置完全相同），`gates.sh` 里没有 ⇒ 本机绿、推上去 CI 红。那种不对称
不会让任何东西变红，只会让"该红的不红"。本门把"盘上的门 = 某条链真跑过的门"钉成静态判据。

七条判据各自单独可红：① 漏跑（盘上有、两条链都不跑）② 远端有本机没有（工作流引用而 `gates.sh` 没跑、
又没豁免）③ 豁免过期（登记了却没工作流引用）④ 豁免空理由 ⑤ 豁免理由的锚点核对不住（点名的作业没在引用
这个脚本 / 没有路径锚点 / 路径是编的）⑥ `gates.sh` 的 `echo "…"` 文案里有**未转义反引号**（bash 会把那段
当命令替换执行一遍，打印的不是作者写的那句话，而退出码照旧对——⑤ 抓到过真猎物，见 §二之五十二）
⑦ 基线里有关键模块的硬错误条目（`.gates.toml` 的名单与 `.gates-baseline.txt` 的欠债台账互相矛盾，§二之九十五）。
反空洞档同样单独可红：读不到 `gates.sh`／读不到 `workflows/`／盘上 0 个脚本／`gates.sh` 里 0 条调用／
引用了盘上不存在的脚本／工作流数不出任何一个 job／`gates.sh` 里 `echo "` 行数掉到下限以下／
`.gates.toml` 解析不了／`.gates-baseline.txt` 不在盘上／`critical_globs` 数不出任何一条 ⇒ 一律 `exit 2`，
"没有发现"不等于"没有问题"。

本文件最要害的一族是**注释不算覆盖**：往工作流里加一行 `# 见 check_x.py`、或把 `gates.sh` 的调用行
改成任何非 `"$REPO/scripts/…"` 形状，都等于把那道门从链上摘掉——所以"注释／非引号形状"那几条腿
专门验门必须红，而不是把那句注释读成覆盖。判据 ⑥ 的要害相反：**转义形状与注释行都不许红**（红了就等于
逼文案少写信息），所以除了"注进去必红"还有一档"仓里现有写法零误伤"。
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import pathlib
import re
import shutil
import subprocess
import sys
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_gates_coverage.py"


def _echo_fill(n: int = 8) -> str:
    r"""合成 `gates.sh` 的文案行：满足判据 ⑥ 的射程下限，且**全是仓里在用的合法形状**。

    第一行带一对已转义的 `\``（现仓二十多条结论文案都这么写）——它同时是本文件的"不误伤"反例：
    这些行存在而门不红，才说明下限那一档不是靠"没有 echo 行"蒙过去的。
    """
    lines = ['echo "已转义的形状：\\`gates.sh\\` 与 \\`check_a.py\\` 不判红"\n']
    lines += [f'echo "结论文案 {i}（$rc）"\n' for i in range(2, n + 1)]
    return "".join(lines)


GATES_A = '''#!/usr/bin/env bash
set -e
"$PYTHON" "$REPO/scripts/check_a.py"
''' + _echo_fill()

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

#: 哨兵：判据 ⑦ 的台账「文件根本不在盘上」（≠ 空字符串=有文件但零条目）。
NO_LEDGER = object()

#: 沙箱名单：只有一格，且那一格对应的模块沙箱里并不存在——⑦ 判的是"基线条目的路径命中名单"，
#: 它读的是台账本身，不需要源文件在场（这正是它能当"两本台账对账"用的原因）。
TOML_MIN = 'critical_globs = [\n    "src/pkg/critical.py",\n]\n'
#: 沙箱基线默认条目：同一条规则，但模块**不在名单里** ⇒ 不该红（红了就等于逼着把名单抄成"全部模块"）。
BASELINE_MIN = "src/pkg/innocent.py#except-pass-broad#f\n"


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
    gates_toml: str = TOML_MIN,
    gates_baseline: str = BASELINE_MIN,
) -> None:
    """搭一棵假仓：scripts/ + gates.sh + .github/workflows/，然后把模块的射程指过去。

    判据 ⑦ 需要两份台账才有射程，缺文件会被判 `exit 2`（"读不到"≠"没问题"），所以沙箱默认就带上：
    名单里只有 `src/pkg/critical.py`，默认基线里那条是**名单外**模块的 ⇒ 既有各档测试照旧走它们自己的判红路径。
    """
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
    toml_p = tmp_path / ".gates.toml"
    if gates_toml is not NO_LEDGER:
        toml_p.write_text(gates_toml, encoding="utf-8")
    base_p = tmp_path / ".gates-baseline.txt"
    if gates_baseline is not NO_LEDGER:
        base_p.write_text(gates_baseline, encoding="utf-8")
    for attr, value in (
        ("GATES_SH", gates),
        ("WORKFLOWS", wf_dir),
        ("SCRIPTS", scripts_dir),
        ("PATH_ROOT", tmp_path),
        ("GATES_TOML", toml_p),
        ("GATES_BASELINE", base_p),
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
''' + _echo_fill()
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


# ── 判据 ⑥：`echo "…"` 文案里的未转义反引号＝命令替换 ─────────────────

#: §二之五十二 抓到的那条真缺陷的**原文形状**（本门自己接线时写的一句标题）。
LOOSE = 'echo "══ 计划表口径门（`docs/plan` 那份表）══"\n'


def test_loose_backtick_in_echo_text_goes_red(tmp_path, monkeypatch):
    mod = _green_tree(tmp_path, monkeypatch)
    gates = mod.GATES_SH.read_text(encoding="utf-8") + LOOSE
    mod.GATES_SH.write_text(gates, encoding="utf-8")
    rc, out = _run(mod)
    assert rc == 1, out
    assert "未转义反引号" in out and "计划表口径门" in out, out


def test_escaped_backticks_and_comment_lines_are_not_red(tmp_path, monkeypatch):
    r"""反例档：仓里在用的 `\`` 转义形状 + 整行注释里的裸反引号都不许红。

    红了就会把人逼成"文案里少写信息"（不写路径、不写脚本名），那比原缺陷更坏。
    """
    mod = _green_tree(tmp_path, monkeypatch)
    gates = (mod.GATES_SH.read_text(encoding="utf-8")
             + LOOSE.replace('echo "', '# echo "')  # 注释不执行 ⇒ 不在射程
             + 'echo "已转义：\\`docs/plan\\` 与 \\`check_a.py\\`"\n')
    mod.GATES_SH.write_text(gates, encoding="utf-8")
    rc, out = _run(mod)
    assert rc == 0, out


def test_few_echo_lines_collapse_the_range(tmp_path, monkeypatch):
    """文案口径改了（printf／heredoc／变量）⇒ 数不出下限那么多 `echo "` 行，本门宁红不装干净。"""
    mod = _green_tree(tmp_path, monkeypatch)
    gates = ('"$PYTHON" "$REPO/scripts/check_a.py"\n'
             'echo "one"\necho "two"\necho "three"\n')
    mod.GATES_SH.write_text(gates, encoding="utf-8")
    rc, out = _run(mod)
    assert rc == 2, out
    assert "没有射程" in out and "echo" in out, out


def test_real_repo_gates_sh_echo_text_is_clean_and_has_range():
    """真读数：本仓 `gates.sh` 的 echo 文案零未转义反引号，且行数值得住（下限不是空集给的干净）。"""
    mod = _module()
    text = (ROOT / "gates.sh").read_text(encoding="utf-8")
    problems, n_echo = mod.echo_quoting_problems(text, "`gates.sh`")
    assert problems == [], problems
    assert n_echo >= mod.ECHO_LINE_FLOOR, n_echo


def test_self_test_fails_when_echo_detector_is_blind(tmp_path, monkeypatch):
    """判据 ⑥ 的注入腿必须真的能失效：检测器被致盲时 `--self-test` 必须红。"""
    mod = _green_tree(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "echo_quoting_problems", lambda *a, **k: ([], 99))
    rc, out = _run(mod, ("--self-test",))
    assert rc == 1 and "检测器失效" in out, out


def _gnu_bash() -> str | None:
    """找一个**真会做命令替换**的 GNU bash。

    本机 `bash` 的第一候选是 WSL 那个占位程序（它连 WSL 都没装，只会回一段 UTF-16 的提示并以 rc=1 结束），
    拿它跑这一腿会得到一个与引号语义毫无关系的红。所以逐个试、以 `--version` 自报 GNU bash 为准；
    全都找不到就让腿红（不是 skip——skip 等于这条证据不存在）。
    """
    for cand in (os.environ.get("BASH"), shutil.which("bash"),
                 "/bin/bash", r"C:/Program Files/Git/bin/bash.exe"):
        if not cand:
            continue
        try:
            out = subprocess.run([cand, "--version"], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=20)
        except (OSError, subprocess.SubprocessError):
            continue
        if out.returncode == 0 and "GNU bash" in out.stdout:
            return cand
    return None


def test_bash_really_runs_the_loose_backticks_and_prints_something_else():
    """判据 ⑥ 不是排版洁癖：这一段用 bash 自己的语义证明"未转义 ⇒ 那句文案没被打出来"。

    这条腿跑的是**真实命令替换**：`docs/plan` 当命令执行会失败，替换成空串——退出码照旧 0。
    转义那一行是配对反例（同一段文字，加了反斜杠就照原样打出来）。
    """
    exe = _gnu_bash()
    assert exe, "找不到 GNU bash（WSL 占位程序不算）：这一族证据只能靠真解释器给，本腿不能默认成立"

    def run(payload: str):
        return subprocess.run([exe, "-c", payload], cwd=str(ROOT), capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=30)

    loose = run('echo "══ 计划表口径门（`docs/plan` 那份表）══"')
    assert loose.returncode == 0, loose.stderr
    assert "docs/plan" not in loose.stdout, loose.stdout
    assert loose.stderr.strip(), "命令替换没被执行过——这一族形状变了，本腿失去意义"

    escaped = run('echo "══ 计划表口径门（\\`docs/plan\\` 那份表）══"')
    assert escaped.returncode == 0, escaped.stderr
    assert "docs/plan" in escaped.stdout, escaped.stdout
    assert escaped.stderr == "", escaped.stderr


# ── 判据 ⑦：基线里有硬错误条目（§二之九十五 / 第六轮审计 ARCH-01）──────────
#
# 起因是两本台账的直接对账：`.gates.toml` 的 `critical_globs` 段开头写着"这里的
# `except Exception: pass` 是硬错误，不许进基线"，而基线里就躺着一条
# `src/autoforge/af_service.py#except-pass-broad#health`。扫描器按名单把它升成 error，
# 基线又按指纹把它吸收掉 ⇒ 门禁照绿、口径照说"不许"。下面这几条腿钉的是"下一次别再靠人翻台账发现"。

def test_baseline_entry_inside_critical_list_goes_red(tmp_path, monkeypatch):
    """名单里有这个模块、基线里也有它的 `except-pass-broad` ⇒ 两本台账互相矛盾，必须红。"""
    _tree(tmp_path, monkeypatch,
          gates_baseline="src/pkg/critical.py#except-pass-broad#health\n")
    rc, out = _run(_tree.mod)
    assert rc == 1 and "硬错误条目" in out, out
    assert "src/pkg/critical.py#except-pass-broad#health" in out, out


def test_other_rule_in_critical_module_is_not_red(tmp_path, monkeypatch):
    """同模块、别的规则 ⇒ 不许红：名单今天只对 `except-pass-broad` 升 error（`scan.py:177`）。
    红了就等于把整张名单读成"关键模块一律不许进基线"，那会一次红掉一堆合法存量，
    逼出来的动作是**抄小名单**——正好是 ARCH-09 点名要防的方向。"""
    _tree(tmp_path, monkeypatch,
          gates_baseline="src/pkg/critical.py#fake-ok-const#save\n")
    rc, out = _run(_tree.mod)
    assert rc == 0, out


def test_fingerprint_without_function_suffix_goes_red(tmp_path, monkeypatch):
    """指纹第三段（函数限定名）可缺 ⇒ 只按前两段判。按"必须三段"判会漏掉一整族写法。"""
    _tree(tmp_path, monkeypatch, gates_baseline="src/pkg/critical.py#except-pass-broad\n")
    rc, out = _run(_tree.mod)
    assert rc == 1 and "硬错误条目" in out, out


def test_missing_gates_baseline_exits_2(tmp_path, monkeypatch):
    """没有欠债台账 ⇒ 没有可对账的东西，`exit 2` 而不是"一条都没命中"的假干净。"""
    _tree(tmp_path, monkeypatch, gates_baseline=NO_LEDGER)
    rc, out = _run(_tree.mod)
    assert rc == 2 and "欠债台账" in out, out


def test_empty_critical_globs_collapses_the_range(tmp_path, monkeypatch):
    """名单空着 ⇒ 基线一条都不会命中，那是空集给的干净，`exit 2`。"""
    _tree(tmp_path, monkeypatch, gates_toml="critical_globs = [\n]\n", gates_baseline="")
    rc, out = _run(_tree.mod)
    assert rc == 2 and "没有射程" in out, out


def test_unparsable_gates_toml_exits_2(tmp_path, monkeypatch):
    """配置文件读得出字节但解析不了 ⇒ 也算射程塌，不能退回"名单为空所以全绿"。"""
    _tree(tmp_path, monkeypatch, gates_toml="critical_globs = [oops\n")
    rc, out = _run(_tree.mod)
    assert rc == 2 and "解析不了" in out, out


def test_self_test_fails_when_baseline_detector_is_blind(tmp_path, monkeypatch):
    """`--self-test` 也必须验得出 ⑦ 这一档瞎了（七档注入少一档就是少一档）。"""
    mod = _green_tree(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "baseline_critical_problems", lambda cfg: [])
    rc, out = _run(mod, ("--self-test",))
    assert rc == 1 and "基线命中关键模块" in out, out


def test_real_repo_baseline_is_clean_and_has_range():
    """真仓读数：两本台账都非空，且现在一条都不命中——"干净"必须是数出来的，不是空集给的。"""
    mod = _module()
    cfg = mod._load_gate_config(mod.GATES_TOML, mod.GATES_BASELINE)
    assert cfg.critical_globs and cfg.baseline, (len(cfg.critical_globs), len(cfg.baseline))
    assert any("af_service.py" in g for g in cfg.critical_globs), cfg.critical_globs
    assert mod.baseline_critical_problems(cfg) == []


def test_the_historical_arch01_fingerprint_is_red_against_the_real_list(tmp_path):
    """把 ARCH-01 当时那条**真指纹**注回沙箱、名单取真仓那份 ⇒ 必须红。

    这一条打在"名单来自真仓"上：合成名单验的是形状，真名单验的是 glob 语义对得上
    （`src/autoforge/af_service.py` 这种整路径、`src/autoforge/af_ir/*` 这种带星号的两档）。
    """
    mod = _module()
    shutil.copyfile(ROOT / ".gates.toml", tmp_path / ".gates.toml")
    baseline = tmp_path / ".gates-baseline.txt"
    baseline.write_text("src/autoforge/af_service.py#except-pass-broad#health\n", encoding="utf-8")
    cfg = mod._load_gate_config(tmp_path / ".gates.toml", baseline)
    problems = mod.baseline_critical_problems(cfg)
    assert len(problems) == 1 and "af_service.py" in problems[0], problems
