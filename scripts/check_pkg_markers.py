#!/usr/bin/env python3
"""包标记门禁：`src/` 下每个含 `.py` 的包目录，必须在 git 索引里有 `__init__.py`。

为什么判"索引里有没有"而不是"磁盘上有没有"：本仓 `.gitignore` 的 `_*.py` 会连带吃掉
`__init__.py`（`_` + `*` = `__init__` + `.py`），实测 `src/autoforge/af_closedloop/__init__.py`
在盘上躺了很久、从未入库。磁盘 walk 检不出这个（本地文件在），只有 CI 的 checkout 才缺；
而 grimp 对"没有 `__init__.py` 的目录"不递归 ⇒ 两条架构门禁在 runner 上只分析 86 个模块、
本机 96 个。**CI 门比本机门弱**，正是本仓 fight 的假绿类。判据换成索引，两边口径才一致。

`tests/` 不在管辖范围：pytest 靠 rootdir 收集，测试目录刻意不放 `__init__.py`。

纯标准库 + git CLI：本机禁 pip install，要装包的门禁等于没有门禁。

**降级档（稳定性审计 §六 P3）**：`--allow-degraded` 时，git 索引读不出就用**磁盘口径**给一个结论——
盘上有模块却没有 `__init__.py` 的目录，在任何环境都会让 grimp 不递归，这一半判得了；
"盘上有、索引里没有"那一半（本仓真正踩过的那个）在无 git 的环境里**判不了**，所以降级档的读数
必须自称 `DEGRADED / 索引半边未验`，不许打成本门正常的那句"干净"。默认（不带旗标）**照旧 RC=2**：
`gates.sh` 与 CI 都不带旗标 ⇒ 任何环境想用弱口径都得显式承认它弱。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def tracked_files(root: Path) -> list[str] | None:
    """返回索引里的仓库相对路径；git 不可用/非仓库时返回 None（由调用方判环境红）。"""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--", str(root.relative_to(REPO))],
            cwd=str(REPO), capture_output=True, text=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return [p for p in out.split("\0") if p]


def _disk_py_files(root: Path) -> list[Path]:
    return [p for p in sorted(root.rglob("*.py")) if "__pycache__" not in p.parts]


def check_degraded(root: Path) -> tuple[list[str], int, int]:
    """磁盘口径的同一件事：有 `.py` 的目录里必须也有 `__init__.py`。返回 (违规, 包目录数, 盘上 .py 数)。"""
    files = _disk_py_files(root)
    pkg_dirs = {p.parent for p in files if p.parent != root}
    findings = [f"{d.relative_to(root).as_posix()}/__init__.py 不在盘上（该目录有模块，包标记缺失）"
                for d in sorted(pkg_dirs) if not (d / "__init__.py").is_file()]
    return findings, len(pkg_dirs), len(files)


def check(root: Path) -> tuple[list[str], int, int]:
    """返回 (违规清单, 包目录数, 索引内 .py 数)。"""
    files = tracked_files(root)
    if files is None:
        return [], 0, 0
    tracked = set(files)
    rel_root = root.relative_to(REPO).as_posix()
    pkg_dirs: set[str] = set()
    for p in tracked:
        if not p.endswith(".py") or "__pycache__" in p:
            continue
        d = p.rsplit("/", 1)[0]
        if d.startswith(rel_root):
            pkg_dirs.add(d)
    findings = [f"{d}/__init__.py 不在 git 索引里（该目录有模块，但包标记未入库）"
                for d in sorted(pkg_dirs) if f"{d}/__init__.py" not in tracked]
    n_py = sum(1 for p in tracked if p.endswith(".py"))
    return findings, len(pkg_dirs), n_py


REPO = Path(__file__).resolve().parent.parent


def main(argv: list[str]) -> int:
    allow_degraded = "--allow-degraded" in argv
    positional = [a for a in argv[1:] if not a.startswith("--")]
    root = Path(positional[0]).resolve() if positional else REPO / "src"
    if not root.is_relative_to(REPO):
        print(f"包标记门禁：{root} 不在仓库 {REPO} 内，索引口径无从建立")
        return 2
    if not root.is_dir():
        print(f"包标记门禁：目录不存在 {root}")
        return 2
    if tracked_files(root) is None:
        findings, n_pkg, n_py = check_degraded(root)
        if not allow_degraded:
            print("包标记门禁：拿不到 git 索引（git 不可用或这里不是仓库）——本门判的是索引，环境不对就是 RC=2。")
            print(f"（同一棵树按磁盘口径可数到 {n_py} 个 .py、{n_pkg} 个包目录、{len(findings)} 处缺盘上标记；"
                  "要用这个弱口径当结论请显式加 --allow-degraded，读数会自称 DEGRADED 而不是『干净』）")
            return 2
        print(f"[降级档] 无 git 索引 ⇒ 磁盘口径（盘上 {n_py} 个 .py / {n_pkg} 个包目录）："
              "只判『盘上有模块却没标记』；『盘上有、索引里没有』那一半本档判不了")
        if findings:
            for line in findings:
                print(f"  ✗ {line}")
            print("\n包标记门禁报红（DEGRADED）：磁盘口径就有包目录缺 __init__.py——这一处不需要 git 也已经是缺陷。")
            return 1
        print("包标记门禁：磁盘口径无缺项，但**结论等级 = DEGRADED / 索引半边未验**"
              "（本环境没有 git，历史那个『盘上有但未入库』的形状无从判定）")
        return 0
    findings, n_pkg, n_py = check(root)
    disk_n = len(_disk_py_files(root))
    if n_py == 0 and disk_n > 0:
        # 索引读得出却一个 .py 都没有 ⇒ 不是"都入库了"，是本门射程为空（路径口径不符 / 索引未初始化）。
        print(f"[射程] git 索引读得出，但本 root 下 0 个入库 .py 而盘上有 {disk_n} 个：本门没有射程")
        return 2
    if findings:
        for line in findings:
            print(f"  ✗ {line}")
        print(f"\n包标记门禁报红：{len(findings)} 个包目录缺入库的 __init__.py"
              "——grimp 不会递归进去，CI 上的架构门禁会比本机少分析模块。")
        return 1
    print(f"✓ 包标记门禁干净（{n_pkg} 个包目录都有入库的 __init__.py，索引内 {n_py} 个 .py）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
