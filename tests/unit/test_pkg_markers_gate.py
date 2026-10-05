"""包标记门禁的三件自证：默认口径不许被弱化、降级档必须自称弱、索引读得出却是空的不能算绿。

这扇门判的是 **git 索引**（稳定性审计的祖先缺陷就长这样：`__init__.py` 在盘上躺了很久、从未入库，
磁盘检查看不见，grimp 在 runner 上少递归一个包 ⇒ CI 比本机弱）。§六 P3 要的是"无 git 环境也有结论"，
于是加了磁盘口径的降级档——但降级档一旦默认生效，就等于把本门唯一能判的那一半悄悄换掉。
所以这里钉的是**边界**，不是"两种口径都能跑"。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_pkg_markers.py"
REPO = Path(__file__).resolve().parents[2]


def _sandbox(tmp_path: Path) -> Path:
    """把脚本放进一棵独立树：脚本按自身位置推 REPO，因此 root 必须在它"下面"才不被拒。"""
    root = tmp_path
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    shutil.copy(SCRIPT, root / "scripts" / "check_pkg_markers.py")
    return root


def _pkg(root: Path, name: str, *, marker: bool, tracked_name: str = "mod.py") -> Path:
    d = root / "src" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / tracked_name).write_text("VALUE = 1\n", encoding="utf-8")
    if marker:
        (d / "__init__.py").write_text("", encoding="utf-8")
    return d


def _run(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "check_pkg_markers.py"), *args],
        capture_output=True, text=True, cwd=str(root),
    )


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)


def _git_init_and_commit(root: Path) -> None:
    _git(root, "init", "-q", ".")
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-qm", "sandbox")


def test_default_verdict_without_git_is_range_collapse_not_a_fallback(tmp_path):
    """不带旗标 ⇒ 无 git 就是 RC=2。默认走弱口径等于把"未入库"这一族永久放过。"""
    root = _sandbox(tmp_path)
    _pkg(root, "pkg_a", marker=True)
    _pkg(root, "pkg_b", marker=False)
    proc = _run(root, str(root / "src"))
    assert proc.returncode == 2, proc.stdout
    assert "拿不到 git 索引" in proc.stdout
    assert "[降级档]" not in proc.stdout  # 提示语里会出现"DEGRADED"这个词，判的是有没有走弱口径的**结论行**
    assert "✓ 包标记门禁干净" not in proc.stdout


def test_degraded_catches_a_missing_marker_on_disk(tmp_path):
    """降级档不是装饰：盘上有模块却没标记，不需要 git 也是缺陷，必须判红。"""
    root = _sandbox(tmp_path)
    _pkg(root, "pkg_a", marker=True)
    _pkg(root, "pkg_b", marker=False)
    proc = _run(root, "--allow-degraded", str(root / "src"))
    assert proc.returncode == 1, proc.stdout
    assert "pkg_b/__init__.py 不在盘上" in proc.stdout
    assert "降级档" in proc.stdout


def test_degraded_green_declares_itself_degraded(tmp_path):
    """降级档给绿必须同时说出"索引半边未验"，且不许复用正常口径那句"干净"。"""
    root = _sandbox(tmp_path)
    _pkg(root, "pkg_a", marker=True)
    _pkg(root, "pkg_b", marker=True)
    proc = _run(root, "--allow-degraded", str(root / "src"))
    assert proc.returncode == 0, proc.stdout
    assert "DEGRADED" in proc.stdout and "索引半边未验" in proc.stdout
    assert "✓ 包标记门禁干净" not in proc.stdout


def test_readable_but_empty_index_collapses_the_range(tmp_path):
    """反空洞腿：git 读得出、索引里却一个 `.py` 都没有（盘上有）⇒ 是射程塌了，不是"都入库了"。

    本批之前这条路径返回 RC=0 并打印"0 个包目录都有入库的 `__init__.py`"——一条什么都不判的绿。
    """
    root = _sandbox(tmp_path)
    _pkg(root, "pkg_c", marker=True)
    _git(root, "init", "-q", ".")
    _git(root, "add", "scripts/check_pkg_markers.py")
    _git(root, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-qm", "only scripts")
    proc = _run(root, str(root / "src"))
    assert proc.returncode == 2, proc.stdout
    assert "没有射程" in proc.stdout


def test_index_mode_still_catches_the_untracked_marker(tmp_path):
    """加降级档不许把主口径换软：入库的 `mod.py` + 从未入库的目录 ⇒ 仍按索引判红。"""
    root = _sandbox(tmp_path)
    _pkg(root, "pkg_c", marker=False)
    _git_init_and_commit(root)
    proc = _run(root, str(root / "src"))
    assert proc.returncode == 1, proc.stdout
    assert "不在 git 索引里" in proc.stdout


def test_index_mode_is_green_when_every_pkg_dir_is_tracked(tmp_path):
    """CONTROL：同一棵树把标记补上并入库 ⇒ RC=0，且用的是索引口径那句读数（不是 DEGRADED）。"""
    root = _sandbox(tmp_path)
    _pkg(root, "pkg_c", marker=True)
    _git_init_and_commit(root)
    proc = _run(root, str(root / "src"))
    assert proc.returncode == 0, proc.stdout
    assert "✓ 包标记门禁干净" in proc.stdout
    assert "DEGRADED" not in proc.stdout


def test_real_repo_is_green_on_the_index_reading():
    """真仓跑一遍：读数不能只活在沙箱里。"""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(REPO / "src")],
        capture_output=True, text=True, cwd=str(REPO),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "✓ 包标记门禁干净" in proc.stdout
