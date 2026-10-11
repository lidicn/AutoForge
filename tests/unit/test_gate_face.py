#!/usr/bin/env python3
"""取数面判据腿：门读的必须是**入库面**，退回磁盘面时必须被读出来。

起因（#118）：三份清单的自动段与真仓腿的期望值都曾在**并发登录线的在途工作树**上量，
CI 跑 committed HEAD ⇒ 同一句话两边读不出同一个数（`assert 89 == 91`／自动段各漂 2／4／4 行）。
本文件钉的是"面"本身，不是某一枚读数——面错了，下面所有读数都会两边各自成立。

两族腿都在：
- **合成树腿**（`E:\tmp` 式的临时 git 仓）逐条杀：未暂存的改动必须**看不见**、暂存后必须**看得见**、
  未入库的新文件必须**不在射程**、祖先里躺着仓库时**不许**把别人的索引当自己的（那是本模块能造出的最坏假绿）、
  空索引必须抛 `FaceError` 而不是读成「干净」。
- **真仓腿**：门的结论行必须自报取数面；快照内容必须逐字节等于 `git show :路径` 的索引 blob。
"""
from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from af_face import DISK, INDEX, Face, FaceError  # noqa: E402

AF_API_REL = "src/autoforge/af_api.py"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git(cwd: pathlib.Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    assert p.returncode == 0, f"git {' '.join(args)} 失败：{p.stdout}{p.stderr}"
    return p.stdout


def _mini_repo(tmp: pathlib.Path) -> pathlib.Path:
    """一棵最小的**已提交** git 树：`src/autoforge/a.py` 内容＝`V1`。"""
    repo = tmp / "tree"
    (repo / "src" / "autoforge").mkdir(parents=True)
    (repo / "src" / "autoforge" / "a.py").write_text("V1\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "src")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "V1")
    return repo


# ---------------------------------------------------------------- 合成树腿
def test_staged_change_is_visible_and_unstaged_is_not(tmp_path):
    """判据 A 的正反两面：改了没 `git add` ⇒ 门**读不到**；`git add` 之后 ⇒ 立刻读到。

    这就是入库面相对磁盘面的**全部**语义差别，所以两面都要有腿：只测「读不到」会放过
    「门根本没换面」，只测「读得到」会放过「门还在读工作树」。
    """
    repo = _mini_repo(tmp_path)
    target = repo / "src" / "autoforge" / "a.py"
    face = Face.open(repo)
    assert face.mode == INDEX, face.label

    target.write_text("V2-在途未暂存\n", encoding="utf-8")
    with face.snapshot("src") as snap:
        assert snap.text(snap.base / "src/autoforge/a.py") == "V1\n", "未暂存的改动漏进了射程"

    _git(repo, "add", "src")
    fresh = Face.open(repo)
    with fresh.snapshot("src") as snap:
        assert snap.text(snap.base / "src/autoforge/a.py") == "V2-在途未暂存\n", "暂存后的改动没被读到"


def test_untracked_new_file_is_out_of_range(tmp_path):
    """新文件没入库 ⇒ 不在射程。这是入库面口径的**已知代价**，明写在 `af_face.py` 的 docstring 里。"""
    repo = _mini_repo(tmp_path)
    (repo / "src" / "autoforge" / "b.py").write_text("V1\n", encoding="utf-8")
    face = Face.open(repo)
    with face.snapshot("src") as snap:
        names = [snap.rel(p) for p in snap.files("src", (".py",))]
    assert names == ["src/autoforge/a.py"], names


def test_ancestor_repo_is_not_mistaken_for_own_index(tmp_path):
    """祖先里躺着仓库时，子目录必须退回**磁盘面**——否则「假想的小树」读到别人的索引。

    反例腿：这里若用 `rev-parse --is-inside-work-tree` 判定，模式会读成 `index`、
    名单会是父仓那份，合成树判据于是拿到一套完全不同的数（本模块能造出的最坏假绿）。
    """
    repo = _mini_repo(tmp_path)
    child = repo / "child"
    (child / "src" / "autoforge").mkdir(parents=True)
    (child / "src" / "autoforge" / "own.py").write_text("ONLY-IN-CHILD\n", encoding="utf-8")
    face = Face.open(child)
    assert face.mode == DISK, face.label
    with face.snapshot("src") as snap:
        assert snap.text(snap.base / "src/autoforge/own.py") == "ONLY-IN-CHILD\n"


def test_empty_dir_range_raises_instead_of_reading_clean(tmp_path):
    """索引里那一层是空的 ⇒ `FaceError`，⛔ 不返回空名单让调用方读成「没有违规」。"""
    repo = _mini_repo(tmp_path)
    face = Face.open(repo)
    try:
        face.snapshot("docs")
    except FaceError as exc:
        assert "0 份" in str(exc), str(exc)
    else:
        raise AssertionError("索引里读不出 `docs/` 却静默成功——射程塌被冒充成了干净")


# ---------------------------------------------------------------- 真仓腿
def test_real_repo_is_read_from_the_index_face():
    face = Face.open(ROOT)
    assert face.mode == INDEX, face.label
    assert "入库面" in face.label


def test_snapshot_bytes_equal_index_blob():
    """快照落出的**字节** == `git show :路径`。这条断了就说明快照悄悄退回了工作树，或导出跑了过滤器。

    ⚠️ 比的是 `read_bytes` 而不是 `text()`：本仓的 blob 自带 CRLF，而 `read_text` 走 universal-newline
    会把 `\r\n` 归一成 `\n`（同一份 72781 字节的 blob 读成 59135 字符）。拿 `text()` 去比 `git show`
    的原始输出，读出来的差是**行尾归一的差**，会被误读成"两边不同源"——本批真的这样误判过一次
    （先写成"checkout-index 跑了 smudge"，现读 `cmp` 才证伪：那份导出与 blob 逐字节相同）。
    所以这条腿同时钉两件事：字节面等于索引，且 `text()` 只许是它的行尾归一形态。
    """
    expected = subprocess.run(
        ["git", "-C", str(ROOT), "show", f":{AF_API_REL}"], capture_output=True
    ).stdout
    face = Face.open(ROOT)
    with face.snapshot("src") as snap:
        got = (snap.base / AF_API_REL).read_bytes()
        chars = snap.text(snap.base / AF_API_REL)
    assert got == expected, f"索引 blob {len(expected)} 字节／快照落出 {len(got)} 字节"
    assert chars == expected.decode("utf-8", "replace").replace("\r\n", "\n"), (
        f"`text()` 不是索引 blob 的行尾归一形态：字符 {len(chars)}／blob 解码 {len(expected.decode('utf-8', 'replace'))}"
    )
    # 反向自证：工作树此刻与索引不同面（登录线在途），所以「等于磁盘」才是真出了缺陷
    disk = (ROOT / AF_API_REL).read_bytes()
    if disk != expected:
        assert got != disk, "快照读到了工作树内容——取数面失效"


def test_three_gates_announce_their_face():
    """结论行必须自报取数面：「读磁盘」不许伪装成「读索引」（降级档只有这一处能被看见）。"""
    for script, needle in (
        ("check_process_model.py", "进程模型门禁干净"),
        ("check_observability.py", "可观测性门禁干净"),
        ("check_trust_boundary.py", "信任边界门禁干净"),
    ):
        p = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / script), str(ROOT)],
            capture_output=True, text=True,
        )
        out = p.stdout + p.stderr
        assert needle in out, f"{script} 结论行没出现「{needle}」：{out[:300]}"
        assert "取数面" in out or "入库面" in out, f"{script} 的结论行没自报取数面：{out[:300]}"
