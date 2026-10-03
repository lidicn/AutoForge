#!/usr/bin/env python3
"""包标记门禁：`src/` 下每个含 `.py` 的包目录，必须在 git 索引里有 `__init__.py`。

为什么判"索引里有没有"而不是"磁盘上有没有"：本仓 `.gitignore` 的 `_*.py` 会连带吃掉
`__init__.py`（`_` + `*` = `__init__` + `.py`），实测 `src/autoforge/af_closedloop/__init__.py`
在盘上躺了很久、从未入库。磁盘 walk 检不出这个（本地文件在），只有 CI 的 checkout 才缺；
而 grimp 对"没有 `__init__.py` 的目录"不递归 ⇒ 两条架构门禁在 runner 上只分析 86 个模块、
本机 96 个。**CI 门比本机门弱**，正是本仓 fight 的假绿类。判据换成索引，两边口径才一致。

`tests/` 不在管辖范围：pytest 靠 rootdir 收集，测试目录刻意不放 `__init__.py`。

纯标准库 + git CLI：本机禁 pip install，要装包的门禁等于没有门禁。
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
    root = Path(argv[1]).resolve() if len(argv) > 1 else REPO / "src"
    if not root.is_relative_to(REPO):
        print(f"包标记门禁：{root} 不在仓库 {REPO} 内，索引口径无从建立")
        return 2
    if not root.is_dir():
        print(f"包标记门禁：目录不存在 {root}")
        return 2
    findings, n_pkg, n_py = check(root)
    if not findings and tracked_files(root) is None:
        print("包标记门禁：拿不到 git 索引（git 不可用或这里不是仓库）——本门判的是索引，环境不对就是 RC=2。")
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
