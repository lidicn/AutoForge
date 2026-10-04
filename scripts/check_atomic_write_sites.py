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


def render(counts: dict[str, int], stale: list[str]) -> str:
    line = (
        f"✓ 原子写站点门禁干净（扫描 {counts['files']} 个文件、`os.replace` 站点 {counts['replace_sites']} 处："
        f"走 mkstemp/公共助手 {counts['safe']} 处、固定名形状 {counts['fixed_name']} 处"
        f"（其中基线冻结 {counts['baselined']} 站、就地豁免 {counts['exempt']} 站））"
    )
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
    if findings:
        for line in findings[:80]:
            print(line)
        print(f"\n共 {len(findings)} 条。修法是走公共助手，不是给这一站加豁免。", file=sys.stderr)
        return 1
    print(render(counts, stale))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
