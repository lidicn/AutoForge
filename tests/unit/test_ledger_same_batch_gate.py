"""台账同批门禁（`scripts/check_ledger_same_batch.py`）的判据腿。

收裁定 20261014 §一 Q1（路线图 rev 14 ①）：同族第三次复发（20261012 三格 → #135 →
2026-10-14 的 71+22 处）⇒ 立机械前置——凡改动会移动 `file:line` 锚点的提交，与它对应的
清单必须在**同一笔**提交里重生成；分两笔 ⇒ 红，红因逐字「清单与源码不同批」。

两族腿都在（⛔ 只写一族）：
- **真仓腿**：门在现仓 HEAD 上跑出 0 或 1（⛔ 2＝射程塌），ROSTER 每枚 doc/gate 都在 HEAD、
  锚点数 > 0（anchors_optional 那枚除外），HEAD 里带 AUTO-BEGIN 标记的 docs/*.md 全在 ROSTER。
- **合成 git 树腿**：临时目录真 `git init` + 逐笔提交，把 A／假红／B／D／R／C／窄判据／Q2 负控
  ／D 腿 exit 2 五种各杀一次。Q2 负控是本批的核心主张：工作树脏不脏与台账无关。
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_ledger_same_batch.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_ledger_same_batch", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git(cwd: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    assert p.returncode == 0, f"git {' '.join(args)} 失败：\n{p.stdout}\n{p.stderr}"
    return p.stdout


# ── 合成树：一棵最小的「清单↔锚点文件」对应面 ─────────────────────────────

SRC_REL = "src/autoforge/foo.py"
DOC_REL = "docs/foo清单.md"
GATE_REL = "scripts/check_foo.py"
#: 清单里的锚点指向 SRC_REL 第 10 行；测试在第 5 行前加行 ⇒ 锚点必漂。
DOC_BODY = "# foo 清单\n\n登记面：\n- `src/autoforge/foo.py:10` 站点 A\n"


def _write_initial(repo: Path) -> None:
    (repo / "src" / "autoforge").mkdir(parents=True)
    lines = [f"line{i}" for i in range(1, 16)]  # 15 行；锚点指第 10 行
    (repo / SRC_REL).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    (repo / "docs").mkdir(exist_ok=True)
    (repo / DOC_REL).write_text(DOC_BODY, encoding="utf-8", newline="\n")
    (repo / "scripts").mkdir(exist_ok=True)
    (repo / GATE_REL).write_text("# placeholder\n", encoding="utf-8", newline="\n")


def _commit(repo: Path, msg: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", msg)


def _mini_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "tree"
    repo.mkdir()
    _write_initial(repo)
    _git(repo, "init", "-q")
    _commit(repo, "V1")
    # 再补一笔空提交：根提交没有父、门按设计退 2（同批性对撞没有对照面），
    # 所有判据腿都必须挂在「有父的提交」上。
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
         "--allow-empty", "-m", "V2 baseline with parent")
    return repo


def _patch_roster(mod, entries):
    """临时替换模块级 ROSTER（合成树里没有真仓那 5 份清单）。"""
    old = mod.ROSTER
    mod.ROSTER = entries
    return old


def _run(mod, repo: Path, *extra: str) -> tuple[int, str]:
    argv = ["--repo", str(repo), *extra]
    p = subprocess.run(
        [sys.executable, "-c",
         "import importlib.util,sys;"
         f"spec=importlib.util.spec_from_file_location('m', r'{SCRIPT}');"
         "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);"
         f"m.ROSTER={entries!r};"
         "sys.exit(m.main(sys.argv[1:]))",
         *argv],
        capture_output=True, text=True,
    )
    return p.returncode, p.stdout + p.stderr


#: 合成树 ROSTER：一份清单 ↔ 一份门 ↔ 一枚锚点文件。
MINI_ROSTER = (
    {
        "doc": DOC_REL,
        "gate": GATE_REL,
        "anchor_re": re.compile(r"`(?P<path>[\w./\-]+\.py):(?P<lineno>\d+)`"),
        "none_re": re.compile(r"^- 无$|^\*\*无\*\*|^无站点"),
    },
)


def _mini_run(repo: Path, roster=MINI_ROSTER, *extra: str) -> tuple[int, str]:
    """在合成树上跑门，临时把 ROSTER 换成 `roster`。"""
    mod = _load()
    old = mod.ROSTER
    mod.ROSTER = roster
    try:
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = mod.main(["--repo", str(repo), *extra])
        return rc, buf.getvalue()
    finally:
        mod.ROSTER = old


# ── M0：什么都不改 ⇒ RC=0（门必须先有绿的可能） ───────────────────────────

def test_m0_no_change_is_green(tmp_path):
    repo = _mini_repo(tmp_path)
    rc, out = _mini_run(repo)
    assert rc == 0, f"基线干净形状被判红：{out}"


# ── A 腿：会漂而清单不同笔 ⇒ RC=1；同笔带清单 ⇒ RC=0 ──────────────────────

def test_a_drift_without_ledger_is_red(tmp_path):
    repo = _mini_repo(tmp_path)
    # 在第 5 行前插一行（行数变化在锚点 10 之前 ⇒ 后面的锚点整体下移）。
    p = repo / SRC_REL
    lines = p.read_text(encoding="utf-8").splitlines()
    lines.insert(4, "# new line before anchor")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    _commit(repo, "drift without ledger")
    rc, out = _mini_run(repo)
    assert rc == 1, out
    assert "清单与源码不同批" in out, out


def test_a_drift_with_ledger_same_commit_is_green(tmp_path):
    repo = _mini_repo(tmp_path)
    p = repo / SRC_REL
    lines = p.read_text(encoding="utf-8").splitlines()
    lines.insert(4, "# new line before anchor")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    # 同笔把清单里的锚点行号也改了（10 → 11）。
    doc = repo / DOC_REL
    doc.write_text(DOC_BODY.replace(":10`", ":11`"), encoding="utf-8", newline="\n")
    _commit(repo, "drift with ledger same batch")
    rc, out = _mini_run(repo)
    assert rc == 0, f"同笔带清单仍被判红：{out}"


# ── 假红腿：内容改了但行数不变 ⇒ RC=0（本门刻意不管这一类） ────────────────

def test_content_change_with_same_line_count_is_green(tmp_path):
    repo = _mini_repo(tmp_path)
    p = repo / SRC_REL
    lines = p.read_text(encoding="utf-8").splitlines()
    lines[9] = "line10-touched-but-same-line"  # 第 10 行内容变了，行数不变
    p.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    _commit(repo, "content only, same line count")
    rc, out = _mini_run(repo)
    assert rc == 0, f"行数不变的改动被假红：{out}"


# ── B 腿：笔尖只动清单、上一笔让同一枚清单漂过 ⇒ RC=1 且消息带 `B：` ──────

def test_b_two_commit_split_is_red_on_the_tip(tmp_path):
    repo = _mini_repo(tmp_path)
    # 笔1：源码漂（行数变化），不带清单。
    p = repo / SRC_REL
    lines = p.read_text(encoding="utf-8").splitlines()
    lines.insert(4, "# drift commit 1")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    _commit(repo, "source drifts, ledger left behind")
    # 笔2（笔尖）：只动清单。
    doc = repo / DOC_REL
    doc.write_text(DOC_BODY.replace(":10`", ":11`"), encoding="utf-8", newline="\n")
    _commit(repo, "tip only touches the ledger")
    rc, out = _mini_run(repo)
    assert rc == 1, out
    assert "B：" in out and "清单与源码不同批" in out, out


# ── D/R 腿：删掉／改名在册锚点所在文件 ⇒ RC=1 ─────────────────────────────

def test_d_deleting_anchor_file_is_red(tmp_path):
    repo = _mini_repo(tmp_path)
    _git(repo, "rm", "-q", SRC_REL)
    _commit(repo, "delete anchor file")
    rc, out = _mini_run(repo)
    assert rc == 1, out
    assert "删除" in out, out


def test_r_renaming_anchor_file_is_red(tmp_path):
    repo = _mini_repo(tmp_path)
    _git(repo, "mv", SRC_REL, "src/autoforge/bar.py")
    _commit(repo, "rename anchor file")
    rc, out = _mini_run(repo)
    assert rc == 1, out
    # diff-tree 默认不开 -M，rename 在门里读作 D+A；foo.py 没了＝在册锚点悬空。
    assert "foo.py" in out and ("删除" in out or "改名" in out), out


# ── C 腿：新加一份带 AUTO-BEGIN 标记的清单而不进 ROSTER ⇒ RC=1 ─────────────

def test_c_unregistered_auto_doc_is_red(tmp_path):
    repo = _mini_repo(tmp_path)
    new_doc = repo / "docs" / "新清单.md"
    new_doc.write_text(
        "# 新清单\n\n<!-- AUTO-BEGIN: scripts/check_sneaky.py --write 生成 -->\n"
        "这里没有登记过\n<!-- AUTO-END -->\n",
        encoding="utf-8", newline="\n",
    )
    _commit(repo, "add an unregistered ledger")
    rc, out = _mini_run(repo)
    assert rc == 1, out
    assert "C：" in out and "ROSTER" in out, out


# ── 窄判据腿：toplevel_py 名册项在根目录新增入库 .py ⇒ RC=1 ───────────────

def test_narrow_toplevel_new_py_is_red(tmp_path):
    repo = _mini_repo(tmp_path)
    roster = MINI_ROSTER + (
        {
            "doc": "docs/仓库卫生清单.md",
            "gate": "scripts/check_oneshot_layout.py",
            "anchor_re": re.compile(r"`(?P<path>[\w./\-]+\.(py|sh|conf)):(?P<lineno>\d+)`"),
            "none_re": re.compile(r"无"),
            "anchors_optional": True,
            "toplevel_py": True,
        },
    )
    # 先把第二份清单及其门摆进 HEAD（否则 D 腿先塌）。
    (repo / "docs" / "仓库卫生清单.md").write_text("# 卫生清单\n无\n", encoding="utf-8", newline="\n")
    (repo / "scripts" / "check_oneshot_layout.py").write_text("# ph\n", encoding="utf-8", newline="\n")
    _commit(repo, "add hygiene ledger")
    # 再在仓库根新增一枚入库 .py。
    (repo / "probe_once.py").write_text("print(1)\n", encoding="utf-8", newline="\n")
    _commit(repo, "add root-level .py")
    rc, out = _mini_run(repo, roster)
    assert rc == 1, out
    assert "根目录新增" in out, out


# ── Q2 负控：工作树脏不脏与台账无关（本批核心主张） ────────────────────────

def test_q2_dirty_worktree_does_not_move_the_needle(tmp_path):
    repo = _mini_repo(tmp_path)
    rc_before, _ = _mini_run(repo)
    assert rc_before == 0

    # 在工作树里改源码文件（行数变化）但**不提交**。
    p = repo / SRC_REL
    lines = p.read_text(encoding="utf-8").splitlines()
    lines.insert(4, "# uncommitted worktree drift")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    rc_dirty, out_dirty = _mini_run(repo)
    assert rc_dirty == rc_before, f"工作树脏了却影响了读数：{out_dirty}"

    # git add 暂存而不提交 ⇒ 仍相同。
    _git(repo, "add", SRC_REL)
    rc_staged, out_staged = _mini_run(repo)
    assert rc_staged == rc_before, f"暂存未提交却影响了读数：{out_staged}"


# ── D 腿 exit 2 五种 ─────────────────────────────────────────────────────

def test_d_no_git_repo_is_two(tmp_path):
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    (plain / "docs").mkdir()
    rc, out = _mini_run(plain)
    assert rc == 2, out
    assert "不是 git 仓库" in out, out


def test_d_roster_doc_missing_in_head_is_two(tmp_path):
    repo = _mini_repo(tmp_path)
    bad = (
        {
            "doc": "docs/nope.md",
            "gate": GATE_REL,
            "anchor_re": re.compile(r"`(?P<path>[\w./\-]+\.py):(?P<lineno>\d+)`"),
            "none_re": re.compile(r"无"),
        },
    )
    rc, out = _mini_run(repo, bad)
    assert rc == 2, out
    assert "docs/nope.md" in out and "不在 HEAD" in out, out


def test_d_roster_gate_missing_in_head_is_two(tmp_path):
    repo = _mini_repo(tmp_path)
    bad = (
        {
            "doc": DOC_REL,
            "gate": "scripts/check_nope.py",
            "anchor_re": re.compile(r"`(?P<path>[\w./\-]+\.py):(?P<lineno>\d+)`"),
            "none_re": re.compile(r"无"),
        },
    )
    rc, out = _mini_run(repo, bad)
    assert rc == 2, out
    assert "scripts/check_nope.py" in out and "不在 HEAD" in out, out


def test_d_zero_anchors_without_marker_is_two(tmp_path):
    repo = _mini_repo(tmp_path)
    # 清单里把锚点整行删掉、也不写「无」。
    (repo / DOC_REL).write_text("# foo 清单\n\n登记面：\n（空）\n", encoding="utf-8", newline="\n")
    _commit(repo, "empty ledger with no marker")
    rc, out = _mini_run(repo)
    assert rc == 2, out
    assert "锚点 0" in out or "空集" in out, out


def test_d_bad_rev_is_two(tmp_path):
    repo = _mini_repo(tmp_path)
    rc, out = _mini_run(repo, MINI_ROSTER, "--rev", "does-not-exist")
    assert rc == 2, out
    assert "读不出提交" in out, out


# ── 反空集腿：把修前形状种回去必须被扫出 ──────────────────────────────────

def test_injection_guard_drift_is_actually_detected(tmp_path):
    """护栏：上面 A 腿那套「插一行」若注入没生效（比如行号算错），测试会假绿。"""
    repo = _mini_repo(tmp_path)
    p = repo / SRC_REL
    before = len(p.read_text(encoding="utf-8").splitlines())
    lines = p.read_text(encoding="utf-8").splitlines()
    lines.insert(4, "# injected")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    after = len(p.read_text(encoding="utf-8").splitlines())
    assert after == before + 1, f"注入未生效（{before} → {after}）"


# ── 真仓腿（对现仓，⛔ 写死计数） ────────────────────────────────────────

def test_real_repo_rc_is_zero_or_one_never_two():
    mod = _load()
    rc = mod.main(["--repo", str(ROOT)])
    assert rc in (0, 1), f"真仓腿跑出 {rc}：射程塌了"


def test_real_roster_every_doc_and_gate_present_in_head():
    mod = _load()
    tree = set(mod.head_paths(ROOT))
    for entry in mod.ROSTER:
        assert entry["doc"] in tree, f"名册里的清单 {entry['doc']} 不在 HEAD 树"
        assert entry["gate"] in tree, f"名册里的门 {entry['gate']} 不在 HEAD 树"
        if entry.get("anchors_optional"):
            continue
        anchors = mod.anchors_in_doc(ROOT, entry["doc"], entry["anchor_re"])
        assert sum(len(v) for v in anchors.values()) > 0, \
            f"{entry['doc']} 现读锚点 0 枚（且无 anchors_optional）"


def test_real_every_auto_doc_is_in_roster():
    """C 腿真仓面：HEAD 里带 AUTO-BEGIN 标记的 docs/*.md 全部在 ROSTER。"""
    mod = _load()
    tree = mod.head_paths(ROOT)
    roster_docs = {e["doc"] for e in mod.ROSTER}
    leaked: list[str] = []
    for path in tree:
        if not (path.startswith("docs/") and path.endswith(".md")):
            continue
        if path in roster_docs:
            continue
        text = mod._git(ROOT, ["git", "show", f"HEAD:{path}"])
        if mod.AUTO_MARKER.search(text):
            leaked.append(path)
    assert leaked == [], f"带 AUTO-BEGIN 标记却没进 ROSTER：{leaked}"


def test_real_red_cause_is_exact_phrase():
    """红因逐字含「清单与源码不同批」——这是裁定 §一 Q1 钉死的措辞。"""
    mod = _load()
    # CI（actions/checkout）默认浅克隆 ⇒ 历史里的 779b9ed 读不出来（rev-parse 128）。
    # 这条腿量的是**那笔历史违规的文案**，浅克隆上无从量——跳过不算放水：
    # rc 永不为 2 与真实红形状由合成树腿覆盖，本地全克隆上本腿照跑。
    probe = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "--verify", "779b9ed^{commit}"],
        capture_output=True, text=True,
    )
    if probe.returncode != 0:
        import pytest
        pytest.skip(f"浅克隆读不出 779b9ed（CI 默认 fetch-depth=1）——文案腿由本地全克隆覆盖")
    # 779b9ed 是本门上线后咬到的第一枚真违规（交接单 §2 永久坐在那笔上）。
    p = subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(ROOT), "--rev", "779b9ed"],
        capture_output=True, text=True,
    )
    assert p.returncode == 1, p.stdout + p.stderr
    assert "清单与源码不同批" in p.stdout, p.stdout


def test_gate_is_wired_into_gates_sh():
    """独立成档的门必须真在链上跑（与 test_gates_coverage_gate 同族）。"""
    body = (ROOT / "gates.sh").read_text(encoding="utf-8")
    assert "check_ledger_same_batch.py" in body, "门没接进 gates.sh"
    assert "ledger_same_batch_rc=" in body, "接线后没捕 rc"
