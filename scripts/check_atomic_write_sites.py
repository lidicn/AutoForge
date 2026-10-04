#!/usr/bin/env python3
"""原子写站点门禁：`os.replace` 的临时名不能是固定名，除非同一函数里有 `mkstemp`/公共助手。

起因（2026-10-04，安全审计那份 zip 的 out_of_scope 14 个单元里优先级最高的两族）：
`af_store._atomic_write` 的 docstring 把 P1-18 那次修复写得很清楚——"用 `tempfile.mkstemp`
生成随机 tmp 名（避免并发写同名 .tmp 互相截断）+ 写后 fsync + replace 后 fsync 目录"。
可这条修好的纪律只落在了 `af_store` 自己头上：AST 盘 src 全集，`os.replace(` 的站点里
**一半以上**仍是"固定名 tmp + 不 fsync"的旧形状（`with_suffix(".tmp")` / `path + ".tmp"` /
`f"{path}.tmp"`），而 `af_persist.save` 的 docstring 还写着"原子替换：崩溃时不会留半截文件"。
坏的形状不是"慢一点"而是**静默丢数据**：`PersistStore` 的租约设计明确允许两个进程在租约到期后
驱动同一条实例，两边写同一个 `{id}.json.tmp` ⇒ 交错内容被最后一次 `os.replace` 装上 ⇒
读侧对校验和失败的记录是**跳过**（`af_persist.records()`），于是那条活着的实例记录直接消失，
不报错也不告警。`af_api._resave_graph_raw` 同理：两条并发启停算出同一个版本号、写同一个
`v{N}.tmp`，丢一次更新。

判据（三条）：
  A. 函数体内出现 `os.replace(`，而该函数体内**没有** `mkstemp` / `atomic_write_text` /
     `_atomic_write` ⇒ 该站点必须出现在基线里，否则判红。
  B. 就地豁免标记 `# fixed-tmp: exempt(理由)` 的括号里必须有非空理由；空理由判红。
     标记要写在 `os.replace` 那一行或函数体内任意一行（与基线二选一）。
  C. 反空洞自证：扫不到任何 src 文件、或全仓 `os.replace` 站点数为 0 ⇒ **`exit 2`**。
     锚点被挪走时本门无从判定射程，报"干净"就是假绿。
  D. 授权面腿（DCD 裁定 20261004 §二，本批启动条件已到）：`.auth/` 那一族的落盘函数必须走
     原子助手。射程 = `af_auth.py` 全部函数 ∪ 别的文件里**这一条写的作用路径**看得出指向 `.auth`
     的函数（按"函数体含 `.auth` 字面量"判会把 `build_app` 那种几百行装配函数的无关裸写一起打红）。
     裸 `write_text`/`write_bytes`/常量可写模式的 `open()` 判红，且**不接受基线与豁免**。
     这一腿自己读不成时同样 `exit 2`（`af_auth.py` 本体不在射程、一个落盘函数都扫不到、
     或叫得出名字的原子助手一处都扫不到 ⇒ 无从判定，不报干净）。

基线（`.atomic-write-baseline.txt`）由 `--print-baseline` 从扫描生成、不手敲，**只减不增**：
每修好一处，重跑一次生成器，条目数往下走。基线只对本仓 `src/` 生效（`argv[1]` 另给目录时
不读基线，也不写）。

豁免/基线**不覆盖**的事：本门判的是"临时文件名唯不唯一、有没有走已经修过的那条路"，
是纯调用图形状。至于"这个文件到底有没有第二个写者"是值语义，静态判不出——
那正是基线里每一条都要写一句理由的原因（理由写的是"为什么这里可以暂时不修"）。
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
BASELINE = REPO / ".atomic-write-baseline.txt"
EXEMPT = re.compile(r"fixed-tmp:\s*exempt\(([^)]*)\)")
#: "已经修过的那条路"的三个名字：随机 tmp 与两个公共助手。
SAFE_CALLS = {"mkstemp", "atomic_write_text", "_atomic_write"}


def _call_name(node: ast.Call) -> str:
    """取调用名的最后一段（`tempfile.mkstemp` → `mkstemp`）。"""
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _qualnames(tree: ast.AST) -> dict[int, str]:
    """id(node) → `类.方法` 全名。方法名单独当键会撞车（两处同名 `save` 会共用一条基线）。"""
    out: dict[int, str] = {}

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}{child.name}"
                out[id(child)] = name
                walk(child, f"{name}.")
            else:
                walk(child, prefix)

    walk(tree, "")
    return out


def _functions(tree: ast.AST) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    quals = _qualnames(tree)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append((quals.get(id(node), node.name), node))
    return out


def _key(rel: str, qual: str, func: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    return f"{rel}::{qual}"


def collect(root: Path) -> tuple[list[dict], list[str], list[tuple[str, int]]]:
    """扫描 `root` 下的 .py。

    返回 (站点列表, 解析失败的文件, 就地豁免标记)。每个站点：
    `{"key", "file", "lines": [replace 的行号...], "safe": bool, "fixed": bool}`
    """
    sites: list[dict] = []
    broken: list[str] = []
    exempts: list[tuple[str, int]] = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text)
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            broken.append(f"{path.relative_to(root)}: {type(exc).__name__}")
            continue
        rel = path.relative_to(root).as_posix()
        for lineno, line in enumerate(text.splitlines(), 1):
            if EXEMPT.search(line):
                exempts.append((rel, lineno))
        for qual, func in _functions(tree):
            replaces = [
                node.lineno
                for node in ast.walk(func)
                if isinstance(node, ast.Call) and _call_name(node) == "replace"
                and _is_os(node)
            ]
            if not replaces:
                continue
            safe = any(
                isinstance(node, ast.Call) and _call_name(node) in SAFE_CALLS
                for node in ast.walk(func)
            )
            fixed = _has_fixed_tmp_name(func)
            sites.append(
                {
                    "key": _key(rel, qual, func),
                    "file": rel,
                    "lines": replaces,
                    "safe": safe,
                    "fixed": fixed,
                    "body": (func.lineno, func.end_lineno or func.lineno),
                    "text": text,
                }
            )
    return sites, broken, exempts


def _is_os(node: ast.Call) -> bool:
    """只认 `os.replace(...)`（`shutil.move`/`path.replace` 不在这族的形状里）。"""
    func = node.func
    return isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "os"


def _has_fixed_tmp_name(func: ast.AST) -> bool:
    """函数体里是否存在"固定名 tmp"的构造：`.with_suffix(".tmp")`、`+ ".tmp"`、`f"...{x}.tmp"`。"""
    for node in ast.walk(func):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "with_suffix" and any(
                isinstance(a, ast.Constant) and isinstance(a.value, str) and a.value.endswith(".tmp")
                for a in node.args
            ):
                return True
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            for side in (node.left, node.right):
                if isinstance(side, ast.Constant) and isinstance(side.value, str) and side.value.endswith(".tmp"):
                    return True
        if isinstance(node, ast.JoinedStr):
            for value in node.values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str) and value.value.endswith(".tmp"):
                    return True
    return False


def load_baseline(path: Path) -> list[str]:
    if not path.is_file():
        return []
    keys = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        keys.append(stripped.split("#", 1)[0].strip())
    return keys


def check(root: Path, baseline_keys: list[str]) -> tuple[list[str], dict[str, int], list[str]]:
    """返回 (红行列表, 计数, 基线中已不再命中的键)。"""
    sites, broken, exempts = collect(root)
    findings: list[str] = []
    counts = {"files": 0, "replace_sites": 0, "safe": 0, "baselined": 0, "exempt": 0, "fixed_name": 0}
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" not in path.parts:
            counts["files"] += 1
    for site in sites:
        counts["replace_sites"] += len(site["lines"])
        if site["safe"]:
            counts["safe"] += 1
            continue
        if site["fixed"]:
            counts["fixed_name"] += 1
        marked = _has_exempt_mark(site)
        if marked:
            counts["exempt"] += 1
            empty = [ln for ln, reason in marked if not reason.strip()]
            for lineno, reason in marked:
                if not reason.strip():
                    findings.append(
                        f"[原子写] {site['file']}:{lineno} 豁免标记没写理由——"
                        f"`# fixed-tmp: exempt(理由)` 的理由是这站为什么可以留固定名，空着等于没豁免"
                    )
            continue
        if site["key"] in baseline_keys:
            counts["baselined"] += 1
            continue
        lines = "/".join(str(n) for n in site["lines"])
        shape = "固定名 tmp（并发写会互相截断）" if site["fixed"] else "裸 os.replace（无 mkstemp、无 fsync）"
        findings.append(
            f"[原子写] {site['file']}:{lines} 函数 `{site['key'].split('::', 1)[1]}` 里的 `os.replace` "
            f"是{shape}，且既没登记进基线也没就地豁免：走 `af_store.atomic_write_text`"
            f"（随机 tmp + fsync + 目录 fsync），或把这一站写进基线并给理由"
        )
    stale = [k for k in baseline_keys if k not in {s["key"] for s in sites}]
    if broken:
        counts["broken"] = len(broken)
    else:
        counts["broken"] = 0
    return findings, counts, stale


def _has_exempt_mark(site: dict) -> list[tuple[int, str]]:
    start, end = site["body"]
    out: list[tuple[int, str]] = []
    for lineno, line in enumerate(site["text"].splitlines(), 1):
        if not (start - 2 <= lineno <= end + 2):
            continue
        match = EXEMPT.search(line)
        if match:
            out.append((lineno, match.group(1)))
    return out


# ── 判据 D：授权面（`.auth/` 下那几份文件）落盘必经原子助手 ────────────
#
# DCD 裁定 `20261004-AF落地回执与write域含read前提差-裁定.md` §二给的启动条件是
# "基线 9 站收完 **或** 下一次 `af_auth`/`af_premiere` 被真实改动，以先到者为准"；本批真实改动了
# `af_auth.py`（授权码列表的 owner/掩码分层），所以这一腿今天就地上线，不等基线收口。
#
# 它与 A 腿的差别是**政策**不是形状：A 腿允许"有理由的固定名"进基线，D 腿不吃基线也不吃豁免。
# 理由写在 `af_auth._atomic_write_text` 的 docstring 里——`_load_*` 把解析失败吞成"当没有这份文件"，
# 半截的撤销名单因此不是"读回上一版"而是"读回空"，等于把已撤销的令牌复活（fail-open）。
AUTH_FACE_BASENAME = "af_auth.py"
#: D 腿的射程边界在 import 时钉死成仓库 `src/`，而不是复用 A 腿的 `SRC`。
#: 原因：A 腿的基线行为要靠挪 `SRC` 来测，两个开关共用会让 D 腿在临时目录里被误启动
#: （临时树里没有 `af_auth.py` 本体 ⇒ 每次都是 exit 2），把 A 腿的测试全带崩。
AUTH_LEG_ROOT = (Path(__file__).resolve().parent.parent / "src").resolve()
#: 裸写整份文件的调用名——这一族的坏形状本身。
PLAIN_WRITE = {"write_text", "write_bytes"}
#: `open()` 只有常量可写模式（`w`/`a`/`x`/`+`）算落盘；模式是变量时不在今天的形状里，
#: 宁缺勿假红——这一腿的红必须是"下一个写错的落盘点"，不能是既有正确读法的噪音。
OPEN_WRITE_MODES = "wax+"
#: 落盘正路分两种记账：叫得出名字的**助手**（本模块私有助手 L0 不能引 `af_store` + 公共助手），
#: 和函数自己 `mkstemp` 现搭的一份。两者都不判红（形状都是原子的），但只有前者撑得起这一腿的锚点
#: ——助手被改名或删掉、各处改成自己手搓 tmp 时，这一腿要说「看不见那条被评审过的路了」，
#: 而不是继续报绿（M4 变形实测过：混记成一种时改名能骗过绿行）。
NAMED_HELPERS = {"_atomic_write_text", "atomic_write_text"}
MKSTEMP = "mkstemp"


def _mentions_auth_literal(func: ast.AST) -> bool:
    """函数体里出现 `.auth` 字符串常量（别处新开的授权面落盘点靠这一个信号进射程）。"""
    return any(
        isinstance(node, ast.Constant) and isinstance(node.value, str) and ".auth" in node.value
        for node in ast.walk(func)
    )


def _write_path_arg(node: ast.Call) -> ast.expr | None:
    """这条写作用的「路径表达式」：`p.write_text(...)` 取接收者，`open(p, "w")` 取路径参数。"""
    if isinstance(node.func, ast.Attribute):
        return node.func.value
    if isinstance(node.func, ast.Name) and node.func.id == "open":
        if node.args:
            return node.args[0]
        for kw in node.keywords:
            if kw.arg in {"file", "path"}:
                return kw.value
    return None


def _writable_open_mode(node: ast.Call) -> bool:
    """`open()` 的模式是不是写：只认常量模式，变量模式判不出来就不硬套（宁缺勿假红）。"""
    mode: object = None
    if len(node.args) >= 2:
        second = node.args[1]
        if not isinstance(second, ast.Constant):
            return False
        mode = second.value
    for kw in node.keywords:
        if kw.arg == "mode":
            if not isinstance(kw.value, ast.Constant):
                return False
            mode = kw.value.value
    return isinstance(mode, str) and any(ch in mode for ch in OPEN_WRITE_MODES)


def _is_plain_write(node: ast.Call) -> bool:
    """整份裸写的三种名字：`write_text` / `write_bytes` / 常量可写模式的 builtin `open`。

    `os.open()` 要排除——它是 `O_RDONLY` 目录 fsync 那条正路，`_call_name` 会把两段都叫成 `open`。
    """
    name = _call_name(node)
    if name in PLAIN_WRITE:
        return True
    return name == "open" and isinstance(node.func, ast.Name) and _writable_open_mode(node)


def _writes_auth_face_path(node: ast.Call, text: str, func: ast.AST) -> bool:
    """第二个信号的收紧：`af_auth.py` **之外**的裸写，只有写的对象看得出指向 `.auth` 才算授权面。

    不收紧会打错：`build_app` 那种几百行的装配函数里既有 `Path(root)/".auth"/…` 的构造，也有
    与鉴权无关的 `write_text`——按"函数体含 `.auth` 字面量"判，它每一条裸写都会被说成授权面落盘。
    这里看的是**那条写**的路径（接收者或 `open` 的路径参数，或它最近一次赋值的来源）里有没有 `.auth`。
    """
    target = _write_path_arg(node)
    if target is None:
        return False
    segments = [ast.get_source_segment(text, target) or ""]
    if isinstance(target, ast.Name):
        for assign in ast.walk(func):
            if not isinstance(assign, ast.Assign):
                continue
            names = [t.id for t in assign.targets if isinstance(t, ast.Name)]
            if target.id in names:
                segments.append(ast.get_source_segment(text, assign.value) or "")
    return any(".auth" in s for s in segments)


def auth_face_functions(root: Path) -> tuple[list[dict], list[str], bool]:
    """两信号**并集**定射程：`af_auth.py` 里的每个函数 ∪ 别的文件里"写向 `.auth` 路径"的裸写函数。

    返回 (函数站点, 解析失败的文件, 有没有扫到 `af_auth.py` 本体)。诚实记账：第二个信号在今天的
    src 上零命中（授权面落盘全在 `af_auth` 内），它买的是"下一个授权面落盘点开在 `af_auth` 之外、
    且自己裸写"——写在 `af_api`/`af_mcp` 里直接落 `.auth/x.json` 那种。
    """
    out: list[dict] = []
    broken: list[str] = []
    saw_auth_file = False
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(root).as_posix()
        is_auth_file = path.name == AUTH_FACE_BASENAME
        saw_auth_file = saw_auth_file or is_auth_file
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            broken.append(f"{rel}: {type(exc).__name__}")
            continue
        if not is_auth_file and ".auth" not in text:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            broken.append(f"{rel}: {type(exc).__name__}")
            continue
        for qual, func in _functions(tree):
            names = [_call_name(n) for n in ast.walk(func) if isinstance(n, ast.Call)]
            lines = []
            for node in ast.walk(func):
                if not isinstance(node, ast.Call) or not _is_plain_write(node):
                    continue
                if is_auth_file or _writes_auth_face_path(node, text, func):
                    lines.append(node.lineno)
            if not lines and not (is_auth_file or _mentions_auth_literal(func)):
                continue
            uses_named = any(name in NAMED_HELPERS for name in names)
            uses_mkstemp = MKSTEMP in names
            out.append(
                {
                    "key": f"{rel}::{qual}",
                    "file": rel,
                    "lines": sorted(lines),
                    "uses_named": uses_named,
                    "uses_mkstemp": uses_mkstemp,
                    "writes": bool(lines) or uses_named or uses_mkstemp,
                }
            )
    return out, broken, saw_auth_file


def check_auth_face(root: Path) -> tuple[list[str], dict[str, int], list[str]]:
    """返回 (红行列表, 读数, 射程失效原因)。射程失效原因非空时调用方必须 `exit 2`。"""
    funcs, broken, saw_auth_file = auth_face_functions(root)
    writers = [s for s in funcs if s["writes"]]
    counts = {
        "in_scope": len(funcs),
        "writers": len(writers),
        "named": sum(1 for s in writers if s["uses_named"]),
        "mkstemp": sum(1 for s in writers if s["uses_mkstemp"] and not s["uses_named"]),
        "plain": 0,
    }
    findings: list[str] = []
    for site in funcs:
        if not site["lines"]:
            continue
        counts["plain"] += 1
        lines = "/".join(str(n) for n in site["lines"])
        findings.append(
            f"[授权面落盘] {site['file']}:{lines} 函数 `{site['key'].split('::', 1)[1]}` 用裸 "
            f"`write_text`/`write_bytes`/`open(...,'w')` 写 `.auth/` 面的文件。这一腿**不吃基线、不吃豁免**："
            f"半截的撤销名单会被读成「没有这份文件」，等于把已撤销的令牌复活。走本模块的 `_atomic_write_text`"
        )
    reasons: list[str] = []
    if broken:
        reasons.append(f"{len(broken)} 个文件读不成（解析/编码失败）：{'; '.join(broken[:5])}")
    if not saw_auth_file:
        reasons.append(f"射程里没有 `{AUTH_FACE_BASENAME}` 本体——文件被改名或挪包，这一腿此刻判不了任何东西")
    if counts["in_scope"] == 0:
        reasons.append("授权面射程里一个函数都没扫到")
    if counts["writers"] == 0:
        reasons.append("射程里一个落盘函数都没扫到 ⇒ 落盘形状整体换了名字，绿行无从成立")
    if counts["named"] == 0:
        reasons.append(
            "叫得出名字的原子助手一处都没扫到 ⇒ 助手被改名或删掉了；"
            "各处自己 `mkstemp` 也许仍旧原子，但这一腿的锚点（必经那个被评审过的助手）已经不成立"
        )
    return findings, counts, reasons


def render(counts: dict[str, int], stale: list[str], auth: dict[str, int] | None = None) -> str:
    line = (
        f"✓ 原子写站点门禁干净（扫描 {counts['files']} 个文件、`os.replace` 站点 {counts['replace_sites']} 处："
        f"走 mkstemp/公共助手 {counts['safe']} 处、固定名形状 {counts['fixed_name']} 处"
        f"（其中基线冻结 {counts['baselined']} 站、就地豁免 {counts['exempt']} 站）"
    )
    if auth is not None:
        line += (
            f"；授权面腿射程函数 {auth['in_scope']} 个、其中落盘 {auth['writers']} 个"
            f"（必经助手 {auth['named']} 个、自带 mkstemp {auth['mkstemp']} 个、"
            f"裸写 0 个——这一腿不接受基线与豁免）"
        )
    line += "）"
    if stale:
        line += f"\n  [基线] {len(stale)} 条已不再命中（修好了就该用 --print-baseline 收掉，只减不增）：" + "、".join(stale[:5])
    return line


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="原子写站点门禁")
    parser.add_argument("target", nargs="?", default=str(SRC), help="扫描目录（默认 src/）")
    parser.add_argument("--print-baseline", action="store_true", help="打印当前扫描出的基线条目")
    args = parser.parse_args(argv[1:])
    root = Path(args.target)
    if not root.is_dir():
        print(f"[射程] 目录不存在：{root}", file=sys.stderr)
        return 2
    sites, broken, _exempts = collect(root)
    if broken:
        print(f"[射程] {len(broken)} 个文件解析失败 ⇒ 本门此刻看不见它们：{'; '.join(broken[:5])}", file=sys.stderr)
        return 2
    if args.print_baseline:
        todo = [s["key"] for s in sites if not s["safe"] and not _has_exempt_mark(s)]
        for key in sorted(set(todo)):
            print(f"{key}  # 待给理由")
        return 0
    replace_sites = sum(len(s["lines"]) for s in sites)
    if not sites or replace_sites == 0:
        print(
            "[射程] 一个 `os.replace` 站点都没扫到 ⇒ 锚点形状变了（改名？挪包？装饰方式换了？），"
            "本门此刻无从判定射程——报『干净』就是假绿",
            file=sys.stderr,
        )
        return 2
    baseline_keys = load_baseline(BASELINE) if root.resolve() == SRC.resolve() else []
    if root.resolve() != SRC.resolve():
        baseline_keys = []
    findings, counts, stale = check(root, baseline_keys)
    auth: dict[str, int] | None = None
    if root.resolve() == AUTH_LEG_ROOT:
        # 授权面腿只在扫本仓 `src/` 时判定：临时目录里没有 `af_auth.py` 本体，让它把 A 腿的
        # 读数一起打成"射程读不成"就丢了这条门真正要看的东西（射程边界写在绿行里）。
        auth_findings, auth_counts, auth_reasons = check_auth_face(root)
        findings += auth_findings
        if auth_reasons:
            for line in findings[:80]:
                print(line)
            print("[授权面腿] 射程读不成 ⇒ 不许报干净：" + "；".join(auth_reasons), file=sys.stderr)
            return 2
        auth = auth_counts
    if findings:
        for line in findings[:80]:
            print(line)
        print(f"\n共 {len(findings)} 条。修法是走公共助手，不是给这一站加豁免。", file=sys.stderr)
        return 1
    print(render(counts, stale, auth))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
