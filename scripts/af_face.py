#!/usr/bin/env python3
"""门禁取数面：把「门读哪一棵树」从各门口头的一句话收成一个可命名、可读出降级的对象。

起因（AF 任务 #118，裁定 `20261011-AF第六轮与第二期审计攒批十三问-裁定.md` §四／§五）：三份清单的
自动段是在**并发登录线未提交的工作树**上生成的，而 CI 跑 committed HEAD ⇒ 同一句话两边读不出同一个数
（`assert 89 == 91`／三份自动段各漂 2／4／4 行／进程模型 5 处）。那份裁定明写
「要把这族一次拔掉，得选 ①（测试腿与门禁一起改跑干净副本树）」，并留了一句
「本件不自决『清单文档该跟哪棵树』」。本模块落的是**同一目标（本地读数＝CI 读数）的另一种取数面**：
不导出 HEAD 整棵树，而是把**入库面（`git ls-files -s -z` 的名单 × `git cat-file --batch` 的裸 blob）**
快照一份再扫——
CI 上索引就是 HEAD，于是两边天然同源；而门仍然看得见「已经 `git add` 还没提交」的东西（①那种修法看不见）。

口径的代价（⛔ 不是免费的，必须写明）：索引面读不出「写了却还没 `git add`」的改动 ⇒ 一份新违规文件
在暂存之前不会被扫到。这一格与本仓既有先例同源——`scripts/check_oneshot_layout.py` 与
`scripts/check_pkg_markers.py` 的判据口径同为**入库面**，理由写在那两份的 docstring 里：
要防的是「进了库的东西」，不是「手边草稿」。

为什么取内容用 `cat-file --batch` 而不是 `git checkout-index --prefix`：后者会跑**过滤器**，
本机把 `core.autocrlf` 改成 `true`（或将来加一条 `.gitattributes`）之后，本地导出面变成 CRLF、
CI 那份 checkout 仍是 LF ⇒ 在本模块内部又造出一族「两边不同源」，恰恰是要消灭的东西。
`cat-file` 直读对象库，不过滤器，与 `git show :<路径>` 逐字节相同（本仓现状：两份都 72781 字节／
1442 枚 CRLF，腿 `test_snapshot_bytes_equal_index_blob` 钉住）。代价是慢一点：一次快照 `src/` 下
103 份 **2.52s**（`checkout-index` 只要 0.35s），但逐份 `git show` 要 **11.50s**（Windows 起进程
≈110ms/次），链上跑 20 多枚门，所以仍是"一次子进程"这一档。
另有一条行尾陷阱（本模块自己踩过并写死在这儿）：仓内 blob **本身就是 CRLF**，而 `Snapshot.text()`
走 `read_text` 会做 universal-newline 归一（`\r\n`→`\n`，72781 字节读成 59135 字符）。
行号与逐行锚点不受影响，但**任何"两边同源"的逐字节对撞必须比 `read_bytes` 与 git 的原始输出，
不许拿 `text()` 去比 `git show`**——那样比出来的差是行尾归一的差，不是取数面的差。

不是 git 仓库（`tests/unit` 的合成树、`E:\\tmp` 的副本树）时**退回磁盘面且不复制**，
但退回这件事必须被读出来：`label` 进各门的结论行，「读磁盘」不许伪装成「读索引」。
另有一条防呆：只有 `git rev-parse --show-toplevel` **恰好等于**取数根才走索引面——`--is-inside-work-tree`
会向上找父目录的 `.git`，而合成树落在 `AppData\\Local\\Temp\\pytest-*`，祖先里只要躺着一枚仓库就会把
「假想的小树」读成别人的索引，那才是本模块能造出的最坏假绿。

纯标准库。只读；`snapshot()` 只往系统临时目录落一份索引面副本，由调用方 `close()` 删除。
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

INDEX = "index"  #: 入库面：名单与内容都来自 git 索引
DISK = "disk"    #: 磁盘面：读不出 git 索引时的降级档，与 CI 口径不同源

LABEL = {
    INDEX: "入库面（git 索引；CI 上索引即 HEAD，两边同源）",
    DISK: "磁盘面（⚠️ 读不出 git 索引的降级档，与 CI 口径不同源）",
}


class FaceError(Exception):
    """索引面读不出（git 不可用／名单为空／导出失败）。

    调用方决定怎么处理：射程该塌的门 ⇒ `exit 2`。⛔「读不出」冒充「没有」是本仓判死的那一族。
    """


def _run(args: list[str], cwd: Path) -> bytes:
    try:
        p = subprocess.run(args, cwd=str(cwd), capture_output=True)
    except OSError as exc:  # git 不在 PATH
        raise FaceError(f"`{args[0]}` 无法执行：{type(exc).__name__}: {exc}") from exc
    if p.returncode != 0:
        detail = (p.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        raise FaceError(f"`{' '.join(args[:3])}…` 退出码 {p.returncode}：" + (detail[0] if detail else "无 stderr"))
    return p.stdout


def _specs(root: Path, rel_dir: str) -> tuple[list[tuple[str, str, str]], list[str]]:
    """`git ls-files -s -z` → `([（模式, 对象名, 路径）], [冲突条目])`。

    要对象名而不是内容：内容交给 `cat-file --batch` 一次取回，全程**不过过滤器**，
    拿到的字节与 `git show :<路径>` 逐字节相同。
    """
    out = _run(["git", "ls-files", "-s", "-z", "--", rel_dir], root)
    specs: list[tuple[str, str, str]] = []
    unmerged: list[str] = []
    for rec in out.split(b"\0"):
        if not rec:
            continue
        head, sep, path = rec.partition(b"\t")
        if not sep:
            raise FaceError(f"`git ls-files -s` 的条目形状变了（没有 TAB）：{rec[:80]!r}")
        parts = head.split(b" ")
        if len(parts) != 3:
            raise FaceError(f"`git ls-files -s` 的头部形状变了（期望 `<模式> <对象名> <阶段>`）：{head[:80]!r}")
        mode, oid, stage = (x.decode("ascii", "replace") for x in parts)
        name = path.decode("utf-8", "replace")
        if stage != "0":
            unmerged.append(name)
            continue
        specs.append((mode, oid, name))
    return specs, unmerged


def _batch(root: Path, specs: list[tuple[str, str, str]], tmp: Path) -> list[str]:
    """一次 `git cat-file --batch` 把所有裸 blob 落进 `tmp`，返回**没落上**的路径。"""
    stdin = "\n".join(oid for _mode, oid, _name in specs).encode() + b"\n"
    try:
        p = subprocess.run(
            ["git", "cat-file", "--batch"], cwd=str(root), input=stdin, capture_output=True
        )
    except OSError as exc:
        raise FaceError(f"`git cat-file` 无法执行：{type(exc).__name__}: {exc}") from exc
    if p.returncode != 0:
        detail = (p.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        raise FaceError(f"`git cat-file --batch` 退出码 {p.returncode}：" + (detail[0] if detail else "无 stderr"))
    buf = p.stdout
    pos = 0
    missing: list[str] = []
    for _mode, oid, name in specs:
        nl = buf.find(b"\n", pos)
        if nl < 0:
            raise FaceError(f"`cat-file --batch` 的输出在 `{name}` 的头部处截断")
        header = buf[pos:nl].decode("ascii", "replace")
        pos = nl + 1
        fields = header.split(" ")
        if len(fields) != 3 or fields[0] != oid:
            raise FaceError(f"`cat-file --batch` 的头部形状变了：{header!r}（期望 `{oid} blob <长度>`）")
        kind, size_raw = fields[1], fields[2]
        if kind == "missing":
            missing.append(name)
            continue
        if kind != "blob":
            raise FaceError(f"`{name}` 的索引对象不是 blob 而是 `{kind}`——本模块只会落 blob")
        try:
            size = int(size_raw)
        except ValueError as exc:
            raise FaceError(f"`cat-file --batch` 给出的长度不是十进制：{size_raw!r}") from exc
        data = buf[pos:pos + size]
        if len(data) != size:
            raise FaceError(f"`{name}` 声称 {size} 字节，输出里只剩 {len(data)} 字节——整体截断")
        pos += size
        if buf[pos:pos + 1] == b"\n":
            pos += 1
        dest = tmp / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        # 符号链接的 blob 内容＝目标路径字符串，这里按普通文件落（射程内是 *.py，不受影响）。
        dest.write_bytes(data)
    return missing


class Snapshot:
    """一棵可以直接按 Path 扫的树。索引面＝临时目录里的索引副本；磁盘面＝原树（不复制）。"""

    def __init__(self, base: Path, mode: str, tmp: Path | None) -> None:
        self.base = base
        self.mode = mode
        self._tmp = tmp

    @property
    def label(self) -> str:
        return LABEL[self.mode]

    def close(self) -> None:
        if self._tmp is not None:
            shutil.rmtree(self._tmp, ignore_errors=True)
            self._tmp = None

    def __enter__(self) -> "Snapshot":
        return self

    def __exit__(self, *_exc: object) -> bool:
        self.close()
        return False

    def rel(self, p: Path) -> str:
        """快照内路径 → 仓内相对路径（两面同形，所以自动段的字符串不因取数面而变）。"""
        try:
            return Path(p).relative_to(self.base).as_posix()
        except ValueError:
            return Path(p).as_posix()

    def files(self, rel_dir: str, suffixes: tuple[str, ...] = (), recursive: bool = True) -> list[Path]:
        base = self.base / rel_dir
        if not base.is_dir():
            raise FaceError(f"取数根下没有 `{rel_dir}/` 这一层（快照根 {self.base}）")
        it = base.rglob("*") if recursive else base.glob("*")
        out = [
            p for p in it
            if p.is_file() and "__pycache__" not in p.parts and (not suffixes or p.name.endswith(suffixes))
        ]
        return sorted(out)

    def text(self, p: Path) -> str:
        try:
            return Path(p).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise FaceError(f"读不出 `{self.rel(p)}`：{type(exc).__name__}: {exc}") from exc

    def exists(self, rel: str) -> bool:
        return (self.base / rel).is_file()


class Face:
    """取数面的构造者：先定「读哪一面」，再把选中的树快照出来。"""

    def __init__(self, root: Path, mode: str, tracked: tuple[str, ...] = ()):
        self.root = Path(root).resolve()
        self.mode = mode
        self.tracked = tracked

    @classmethod
    def open(cls, root: Path) -> "Face":
        root = Path(root).resolve()
        if not root.is_dir():
            raise FaceError(f"取数根不存在：{root}")
        try:
            top = Path(_run(["git", "rev-parse", "--show-toplevel"], root)
                       .decode("utf-8", "replace").strip()).resolve()
            mode = INDEX if top == root else DISK
        except FaceError:
            mode = DISK
        if mode == DISK:
            return cls(root, DISK)
        raw = _run(["git", "ls-files", "-z"], root)
        tracked = tuple(n for n in raw.decode("utf-8", "replace").split("\0") if n)
        if not tracked:
            raise FaceError("git 索引读出 0 份文件——本门无从判定，报「干净」就是假绿")
        return cls(root, INDEX, tracked)

    @property
    def label(self) -> str:
        return LABEL[self.mode]

    def snapshot(self, rel_dir: str) -> Snapshot:
        """把 `rel_dir` 下的**入库面**内容逐字节落到临时目录；磁盘面直接指向原树（零复制）。

        ⚠️ 刻意**不用** `git checkout-index --prefix`：那一条会跑过滤器，本机 `core.autocrlf=true`
        或将来加 `.gitattributes` 之后，本地副本与 CI 的 checkout 就在行尾上分家——本模块的存在理由
        正是消灭「两边读不出同一个数」，不能自己留这一道缝。`cat-file --batch` 直读对象库，
        落出来的字节与 `git show :<路径>` 相同（判据腿逐字节对撞）。
        """
        if self.mode == DISK:
            return Snapshot(self.root, DISK, None)
        tmp = Path(tempfile.mkdtemp(prefix="af-face-"))
        try:
            specs, unmerged = _specs(self.root, rel_dir)
            if unmerged:
                raise FaceError(
                    f"`{rel_dir}/` 下有 {len(unmerged)} 份未合并（stage≠0）条目，索引面读不出唯一内容："
                    + "、".join(unmerged[:5])
                )
            if not specs:
                raise FaceError(f"索引里 `{rel_dir}/` 下读出 0 份文件——射程塌，不许读成「没有违规」")
            missing = _batch(self.root, specs, tmp)
            if missing:
                raise FaceError(
                    f"索引点名 {len(specs)} 份，`cat-file` 说库里缺失 {len(missing)} 份：" + "、".join(missing[:5])
                )
            absent = [name for _m, _o, name in specs if not (tmp / name).is_file()]
            if absent:
                raise FaceError(
                    f"落盘后仍缺 {len(absent)} 份（导出未生效）：" + "、".join(absent[:5])
                )
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        return Snapshot(tmp, INDEX, tmp)
