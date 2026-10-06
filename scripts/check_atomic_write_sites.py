#!/usr/bin/env python3
"""原子写站点门禁：把临时文件挪成正名的那张"替换脸"不能配固定临时名，除非同一函数里有 `mkstemp`/公共助手。

起因（2026-10-04，安全审计那份 zip 的 out_of_scope 14 个单元里优先级最高的两族）：
`af_atomic.atomic_write_text`（P1-18 那次的修法原本写在 `af_store._atomic_write` 的 docstring 里，
本批下沉成独立助手）把纪律写得很清楚——"用 `tempfile.mkstemp`
生成随机 tmp 名（避免并发写同名 .tmp 互相截断）+ 写后 fsync + replace 后 fsync 目录"。
可这条修好的纪律只落在了 `af_store` 自己头上：AST 盘 src 全集，`os.replace(` 的站点里
**一半以上**仍是"固定名 tmp + 不 fsync"的旧形状（`with_suffix(".tmp")` / `path + ".tmp"` /
`f"{path}.tmp"`），而 `af_persist.save` 的 docstring 还写着"原子替换：崩溃时不会留半截文件"。
坏的形状不是"慢一点"而是**静默丢数据**：`PersistStore` 的租约设计明确允许两个进程在租约到期后
驱动同一条实例，两边写同一个 `{id}.json.tmp` ⇒ 交错内容被最后一次 `os.replace` 装上 ⇒
读侧对校验和失败的记录是**跳过**（`af_persist.records()`），于是那条活着的实例记录直接消失，
不报错也不告警。`af_api._resave_graph_raw` 同理：两条并发启停算出同一个版本号、写同一个
`v{N}.tmp`，丢一次更新。

判据（五条）：
  A. 函数体内出现"把临时文件挪成正名"的替换脸——`os.replace`/`os.rename`，或单位置参数的
     `X.replace(Y)`／`X.rename(Y)`（`Path` 那一张），而该函数体内**没有** `mkstemp` /
     `atomic_write_text` / `_atomic_write` ⇒ 该站点必须出现在基线里，否则判红。
     第二张脸是 2026-10-06 补进射程的：`af_undo.UndoStore._save` 写的是固定名
     `with_suffix(".json.tmp")` + `tmp.replace(self.path)`，当时门只认 `os.replace`，
     于是它根本不进站点集合、照旧报"固定名形状 0 处"（§二之五十四）。
  B. 就地豁免标记 `# fixed-tmp: exempt(理由)` 的括号里必须有非空理由；空理由判红。
     标记要写在替换脸那一行或函数体内任意一行（与基线二选一）。**理由必须写在同一行**——
     收集时是逐行扫的，跨行的理由匹配不上（今天就踩了这个坑）。
  C. 反空洞自证：扫不到任何 src 文件、或全仓替换脸站点数为 0 ⇒ **`exit 2`**。
     锚点被挪走时本门无从判定射程，报"干净"就是假绿。
  D. 授权面腿（DCD 裁定 20261004 §二，本批启动条件已到）：`.auth/` 那一族的落盘函数必须走
     原子助手。射程 = `af_auth.py` 全部函数 ∪ 别的文件里**这一条写的作用路径**看得出指向 `.auth`
     的函数（按"函数体含 `.auth` 字面量"判会把 `build_app` 那种几百行装配函数的无关裸写一起打红）。
     裸 `write_text`/`write_bytes`/常量可写模式的 `open()` 判红，且**不接受基线与豁免**。
     这一腿自己读不成时同样 `exit 2`（`af_auth.py` 本体不在射程、一个落盘函数都扫不到、
     或叫得出名字的原子助手一处都扫不到 ⇒ 无从判定，不报干净）。
  E. 状态落盘腿（新增审计 BUG-05 补的第三条腿）：A/D 都只审"已经在做原子替换"或
     "`.auth` 那一族"，**第三种形状它们都看不见——压根没走原子写**。BUG-03/BUG-04 当年
     就是这么活下来的：门禁报绿，而它们就躺在 `src/` 里。E 问的是另一个问题：
     「这个状态文件**该不该**走原子写」。射程 = 整份覆盖写（`write_text`/`write_bytes`/
     常量 w/x/+ 模式的 builtin `open`）且路径指向 `.forge/` 或 `*.json`/`*.jsonl`；
     追加写（模式 `a`，如 `af_store.append_jsonl`）刻意不在射程——单行追加本就不撕裂，
     塞进原子助手是错配。判红条件 = 该函数内既无 `mkstemp` 也无任何原子助手。
     射程失效（一个状态落盘点都扫不到）同样 `exit 2`。
     **锚点与红行解耦**：锚点只要求"文件里有状态提示 + 函数里有落盘动作"，不要求路径
     字面量（仓库里多数落盘传的是 `self._tags_path()` 这类造路径表达式）；红行仍要求
     路径字面量指认，否则装配函数里的无关裸写会被一起打红。

基线（`.atomic-write-baseline.txt`）由 `--print-baseline` 从扫描生成、不手敲，**只减不增**：
每修好一处，重跑一次生成器，条目数往下走。基线只对本仓 `src/` 生效（`argv[1]` 另给目录时
不读基线，也不写）。**当前基线为空**（立门时冻结的 9 站已按裁定 20261004 §二 的顺序收完），
所以"新站点登记进基线"这条路实际上已经没有了——要么走助手，要么写带理由的就地豁免。

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
            replaces = _replacement_faces(func)
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


def _replacement_faces(func: ast.AST) -> list[int]:
    """函数体内每一张"把临时文件挪成正名"的替换脸的行号（判据 A 的射程）。

    原本是**一张**：只有 `os.replace(...)`，而 `_is_os` 的注释自己写着"`shutil.move`／`path.replace`
    不在这族的形状里"。代价今天量出来了：`af_undo.UndoStore._save` 写的是
    `tmp = self.path.with_suffix(".json.tmp")` + `tmp.replace(self.path)`——固定名 + 第二张脸，
    于是它**根本不进站点集合**，门照旧报"固定名形状 0 处"，而审计点名的正是这一站
    （§二之五十四：一条"该红的不红"的门，红的前提是它在射程里）。

    现在是**两张**：`os.replace`／`os.rename`（模块属性那一张），以及 `X.replace(Y)`／`X.rename(Y)`
    这种**恰好一个位置参数、无关键字**的方法调用（`Path` 那一张）。按"单个位置参数"划是因为
    `str.replace(old, new)` 必然给两个位置参数、`datetime.replace(tzinfo=…)` 带关键字——
    `af_persist.py:51` 那种时区归一不会被算成替换脸，`af_draft.py:402` 的 `action_name.replace('.', '_')`
    同理。真出现两参数不带关键字的 `X.replace(a)` 之外的形状时，本门会漏判而不是误伤：漏判由
    "站点数为 0 ⇒ `exit 2`"那条反空洞档兜住口径漂移。
    """
    faces: set[int] = set()
    for node in ast.walk(func):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in ("replace", "rename"):
            continue
        receiver_is_os = (isinstance(node.func.value, ast.Name) and node.func.value.id == "os")
        if receiver_is_os:
            faces.add(node.lineno)
        elif len(node.args) == 1 and not node.keywords:
            faces.add(node.lineno)
    return sorted(faces)


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
        shape = "固定名 tmp（并发写会互相截断）" if site["fixed"] else "裸替换脸（无 mkstemp、无 fsync）"
        findings.append(
            f"[原子写] {site['file']}:{lines} 函数 `{site['key'].split('::', 1)[1]}` 里的替换脸"
            f"（`os.replace` 那一张，或 `tmp.replace(正名)`／`tmp.rename(正名)` 那一张）是{shape}，"
            f"且既没登记进基线也没就地豁免：走 `af_atomic.atomic_write_text`"
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
#: E 腿（状态落盘）的射程边界**独立**成一个开关，不挂在 `AUTH_LEG_ROOT` 上。
#: 门禁单测测 D 腿时会故意把 `AUTH_LEG_ROOT` 指到临时树；E 若跟着被启动，就会在
#: 「临时树里一个状态落盘点都没有」上 exit 2，把 D 腿的读数一起带崩——与 `SRC` /
#: `AUTH_LEG_ROOT` 那个耦合同构，今天已经踩过一次（三个开关必须各自独立）。
STATE_LEG_ROOT = (Path(__file__).resolve().parent.parent / "src").resolve()
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


# ── 判据 E：状态落盘点必须走原子助手（新增审计 BUG-05 补的那条腿）─────
#
# A/D 两条腿都只审「已经在做原子替换」或「`.auth` 那一族」。第三种形状它们都看不见：
# **压根没走原子写**——既没有 `os.replace`，也没有 mkstemp+rename。
# BUG-03（`af_runtime_ext.persist()` 裸 `open("w")+json.dump`）与
# BUG-04（`af_metrics.flush_buffer()` 裸 `write_text`）当时就是这么活下来的：
# 门禁报绿，而它们就在 `src/` 里。
#
# E 腿与 A/D 的差别是**问的问题**不同：
#   A 问「走了原子写的，写得对不对」；D 问「授权面有没有偷跑」；
#   E 问「这个状态文件**该不该**走原子写」——没走的直接判红。
#
# 射程刻意收窄，遵循本门一贯的「宁缺勿假红」：
#   · 只认**整份覆盖**的写（`write_text`/`write_bytes`/常量模式的 builtin `open`）；
#     追加写（模式 `a`，如 `af_store.append_jsonl`）是另一种纪律——单行追加本就不撕裂，
#     把它塞进原子助手反而是错配。
#   · 路径要能看出指向 `.forge/` 或 `*.json`/`*.jsonl` 才算状态文件。
#   · 助手实现本身（`af_atomic.py`）当然豁免——它就是那条路。
STATE_HINTS = (".forge", ".json", ".jsonl")
#: 判据 E 自己认的「已经修过的那条路」，与 A 腿的 SAFE_CALLS 同一组名字。
E_SAFE_CALLS = {"mkstemp", "atomic_write_text", "_atomic_write", "_atomic_write_text"}
#: 走助手的那条路本身也要能当锚点被看见（否则"全都改走助手"会让射程归零）。
E_HELPER_WRITE_CALLS = {"atomic_write_text", "_atomic_write", "_atomic_write_text"}
E_HELPER_MODULES = {"af_atomic.py"}
#: 只认这些常量模式的整份覆盖写；`a`（追加）刻意不在其中。
E_WHOLE_WRITE_MODES = "wx+"


def _writes_state_path(node: ast.Call, text: str, func: ast.AST) -> bool:
    """这条写用的路径看得出是状态文件吗（`.forge/` 或 `*.json`/`*.jsonl`）。"""
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
    blob = " ".join(segments).lower()
    return any(h in blob for h in STATE_HINTS)


def _is_whole_file_write(node: ast.Call) -> bool:
    """整份覆盖写：`write_text`/`write_bytes`，或 builtin `open` 的 w/x/+ 常量模式。"""
    name = _call_name(node)
    if name in PLAIN_WRITE:
        return True
    if name != "open" or not isinstance(node.func, ast.Name):
        return False
    mode: object = None
    if len(node.args) >= 2:
        second = node.args[1]
        if not isinstance(second, ast.Constant):
            return False  # 模式是变量 ⇒ 判不出来就不硬套
        mode = second.value
    for kw in node.keywords:
        if kw.arg == "mode":
            if not isinstance(kw.value, ast.Constant):
                return False
            mode = kw.value.value
    return isinstance(mode, str) and any(ch in mode for ch in E_WHOLE_WRITE_MODES)


def state_write_functions(root: Path) -> tuple[list[dict], int, list[str]]:
    """扫出「整份覆盖写状态文件」的函数，含是否走原子助手的记账。

    返回 (站点列表, 射程内整份覆盖写站点总数, 解析失败的文件)。**站点列表只含违规的**
    （没走助手那些），但「总数」含走助手的——反空洞自证要看的是"射程还看不看得见"，
    而不是"有没有违规"：修干净之后违规数为 0 是绿，不是射程失效。
    """
    out: list[dict] = []
    broken: list[str] = []
    in_scope = 0
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts or path.name in E_HELPER_MODULES:
            continue
        rel = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text)
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            broken.append(f"{rel}: {type(exc).__name__}")
            continue
        if not any(h in text for h in STATE_HINTS):
            continue
        for qual, func in _functions(tree):
            # 锚点与红行**解耦**（各自解决一个不同问题）：
            # · 锚点问「还看不看得见状态落盘」——所以裸写与走助手的都算，且**不要求**
            #   路径字面量里出现 .json：仓库里大多数落盘传的是 `self._tags_path()` 这类
            #   造路径的表达式，字面量判据看不见它们（今天就因此把 in_scope 判成 0，
            #   把「全都改走助手了」误当成射程失效）。
            # · 红行问「这一条裸写是不是在写状态文件」——这里保留精确的路径字面量判据，
            #   否则 `build_app` 那种几百行装配函数里与状态无关的裸写会被一起打红。
            plain_all: list[int] = []
            helper_all: list[int] = []
            for node in ast.walk(func):
                if not isinstance(node, ast.Call):
                    continue
                if _is_whole_file_write(node):
                    plain_all.append(node.lineno)
                elif _call_name(node) in E_HELPER_WRITE_CALLS:
                    helper_all.append(node.lineno)
            if not plain_all and not helper_all:
                continue
            in_scope += len(plain_all) + len(helper_all)
            names = [_call_name(n) for n in ast.walk(func) if isinstance(n, ast.Call)]
            if any(n in E_SAFE_CALLS for n in names):
                continue  # 走了原子助手（或自己 mkstemp）——这一站是绿的
            red = [
                node.lineno
                for node in ast.walk(func)
                if isinstance(node, ast.Call)
                and _is_whole_file_write(node)
                and _writes_state_path(node, text, func)
            ]
            if red:
                out.append({"key": f"{rel}::{qual}", "file": rel, "lines": sorted(red)})
    return out, in_scope, broken


def check_state_writes(root: Path) -> tuple[list[str], dict[str, int], list[str]]:
    """判据 E 的判定。返回 (红行列表, 读数, 射程失效原因)；原因非空 ⇒ 调用方 `exit 2`。"""
    sites, in_scope, broken = state_write_functions(root)
    # 嵌套函数里的写会同时算到外层 qualname 上（`_qualnames` 把内层挂到外层之下），
    # 同一个 (文件, 行) 会被报两次。按 (文件, 行号) 去重，红行才对着实际站点。
    seen: set[tuple[str, int]] = set()
    deduped: list[dict] = []
    for s in sites:
        if any((s["file"], ln) in seen for ln in s["lines"]):
            continue
        for ln in s["lines"]:
            seen.add((s["file"], ln))
        deduped.append(s)
    sites = deduped
    plain = sum(len(s["lines"]) for s in sites)
    counts = {
        "in_scope": in_scope,
        "state_sites": in_scope,
        "safe": max(0, in_scope - plain),
        "plain": plain,
    }
    findings = [
        f"[状态落盘] {s['file']}:{'/'.join(str(n) for n in s['lines'])} 函数 "
        f"`{s['key'].split('::', 1)[1]}` 整份覆盖写状态文件（`.forge/` 或 `*.json`/`*.jsonl`）"
        f"却没走 `af_atomic.atomic_write_text`——崩在半截会让读侧把「损坏」读成「没有这份文件」，"
        f"状态静默回空。走公共助手，或就地说明为何这条路径不可能被并发写/撕裂"
        for s in sites
    ]
    reasons: list[str] = []
    if broken:
        reasons.append(f"{len(broken)} 个文件读不成（解析/编码失败）：{'; '.join(broken[:5])}")
    if in_scope == 0:
        reasons.append(
            "状态落盘射程里一个整份覆盖写站点都没扫到 ⇒ 落盘形状整体换了名字，"
            "这一腿此刻判不了任何东西（不许报干净）"
        )
    return findings, counts, reasons


def render(
    counts: dict[str, int],
    stale: list[str],
    auth: dict[str, int] | None = None,
    state: dict[str, int] | None = None,
) -> str:
    line = (
        f"✓ 原子写站点门禁干净（扫描 {counts['files']} 个文件、替换脸站点 {counts['replace_sites']} 处："
        f"走 mkstemp/公共助手 {counts['safe']} 处、固定名形状 {counts['fixed_name']} 处"
        f"（其中基线冻结 {counts['baselined']} 站、就地豁免 {counts['exempt']} 站）"
    )
    if auth is not None:
        line += (
            f"；授权面腿射程函数 {auth['in_scope']} 个、其中落盘 {auth['writers']} 个"
            f"（必经助手 {auth['named']} 个、自带 mkstemp {auth['mkstemp']} 个、"
            f"裸写 0 个——这一腿不接受基线与豁免）"
        )
    if state is not None:
        line += (
            f"；状态落盘腿整份覆盖写站点 {state['in_scope']} 处"
            f"（走助手 {state['safe']} 处、裸写 {state['plain']} 处）"
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
            "[射程] 一个替换脸站点都没扫到 ⇒ 锚点形状变了（改名？挪包？装饰方式换了？），"
            "本门此刻无从判定射程——报『干净』就是假绿",
            file=sys.stderr,
        )
        return 2
    baseline_keys = load_baseline(BASELINE) if root.resolve() == SRC.resolve() else []
    if root.resolve() != SRC.resolve():
        baseline_keys = []
    findings, counts, stale = check(root, baseline_keys)
    auth: dict[str, int] | None = None
    state: dict[str, int] | None = None
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
    if root.resolve() == STATE_LEG_ROOT:
        # 判据 E（状态落盘必须走原子助手）——新增审计 BUG-05 补的那条腿。
        # 独立开关：只在本仓 src/ 上判定，且同样带反空洞自证。
        state_findings, state_counts, state_reasons = check_state_writes(root)
        findings += state_findings
        if state_reasons:
            for line in findings[:80]:
                print(line)
            print("[状态落盘腿] 射程读不成 ⇒ 不许报干净：" + "；".join(state_reasons), file=sys.stderr)
            return 2
        state = state_counts
    if findings:
        for line in findings[:80]:
            print(line)
        print(f"\n共 {len(findings)} 条。修法是走公共助手，不是给这一站加豁免。", file=sys.stderr)
        return 1
    print(render(counts, stale, auth, state))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
