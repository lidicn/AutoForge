#!/usr/bin/env python3
"""台账锚点对账器 v2：按「真值为键」重定位 §三 手工段的行号。

为什么重写（2026-10-29 实证）：v1 从门报里取"过期行→新行"配对，再用"同文件队列取第一个"
分配——同文件多个站点时会**级联错配**（把 A 码的行号写到 B 码那行，下一轮再把 B 挪去 A，
来回震荡）。今天真实发生了，而且是靠门报里"同一行已不是同一件事"这种奇怪的措辞才发现。

v2 的算法不依赖门报的措辞，直接从 AST 取真值，按 `(码, 文件)` / `(kind, 函数, 文件)` 为键
对位重写：一行改错不会影响另一行。

用法：
    PYTHONPATH=src python scripts/sync_ledger_anchors.py [observability|process_model ...]
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
CODE_RE = re.compile(r"[A-Z][A-Z0-9_]{4,}")


def _logger_codes() -> dict[str, dict[str, list[int]]]:
    """`码 → {相对路径: [行号...]}`（与可观测性门同口径：只认 `logger.<lvl>("CODE…")` 首参常量）。"""
    out: dict[str, dict[str, list[int]]] = {}
    for f in sorted((SRC / "autoforge").rglob("*.py")):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        rel = f.relative_to(ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not (isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name)
                    and fn.value.id == "logger"):
                continue
            if not node.args or not isinstance(node.args[0], ast.Constant):
                continue
            val = node.args[0].value
            if not isinstance(val, str):
                continue
            code = val.split(" ", 1)[0].split("{", 1)[0].strip()
            if CODE_RE.fullmatch(code):
                out.setdefault(code, {}).setdefault(rel, []).append(node.lineno)
    return out


def _lifecycle_sites() -> dict[tuple[str, str], dict[str, list[int]]]:
    """`(kind, 文件) → {函数: [行号...]}`：进程模型 §三 的行没有码，靠 kind+函数对位。"""
    # 键必须与台账 §三 的 `kind=` 读数同名（点名，如 os.kill）；v2 第一版在这里
    # 用了简称（kill/Popen），结果真值查不到、对账静默返回 0 —— 修不动比修错更难发现。
    want = {"subprocess.Popen": "subprocess.Popen", "os.kill": "os.kill",
            "signal.signal": "signal.signal", "atexit.register": "atexit.register"}
    out: dict[tuple[str, str], dict[str, list[int]]] = {}
    for f in sorted((SRC / "autoforge").rglob("*.py")):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        rel = f.relative_to(ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            parts: list[str] = []
            cur = node.func
            while isinstance(cur, ast.Attribute):
                parts.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name):
                parts.append(cur.id)
            dotted = ".".join(reversed(parts))
            kind = want.get(dotted)
            if kind is None:
                continue
            owner = ""
            for enc in reversed(list(getattr(tree, "body", []))):
                pass
            fn_name = _enclosing_func(tree, node.lineno)
            out.setdefault((kind, rel), {}).setdefault(fn_name, []).append(node.lineno)
    return out


def _enclosing_func(tree: ast.AST, lineno: int) -> str:
    """返回**真正包住这一行**的最内层函数名。

    v2 第一版取"起始行 ≤ 目标行的最后一个函数"——遇到嵌套函数就错：`start_watch` 里
    定义的 `_collect_trigger_entities` 起始行更晚，于是外层 `Popen` 那一站被记到内层
    函数名下，对账永远配不上（台账写的是 `start_watch`，门报也是，真值却是内层名）。
    正确做法是按区间 `[lineno, end_lineno]` 取包含目标行的最内层定义。
    """
    best = ""
    best_span = 10 ** 9
    for n in ast.walk(tree):
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        lo, hi = n.lineno, getattr(n, "end_lineno", n.lineno)
        if lo <= lineno <= hi and (hi - lo) < best_span:
            best, best_span = n.name, hi - lo
    return best


def realign(name: str) -> int:
    if name == "observability":
        truth = _logger_codes()
        row = re.compile(r"^- `((?:src|scripts)/[^`:]+):(\d+)` · code=([A-Z][A-Z0-9_]{4,})")
    else:
        truth = {(k, p): {fn: sorted(v) for fn, v in d.items()}
                 for (k, p), d in _lifecycle_sites().items()}
        # ⛔ 字符类别漏大写：`kind=subprocess.Popen` 的 P 是大写，[a-z_.]+ 会把它截成
        # "subprocess."，于是真值永远查不到、对账静默返回 0——"修不动"比"修错"更难发现。
        row = re.compile(r"^- `((?:src|scripts)/[^`:]+):(\d+)` · kind=([A-Za-z_.]+)")

    led = ROOT / ("docs/可观测性清单.md" if name == "observability" else "docs/进程模型清单.md")
    b = led.read_bytes()
    crlf = b.count(b"\r\n") > 0 and b.count(b"\r\n") == b.count(b"\n")
    lines = b.decode("utf-8").replace("\r\n", "\n").split("\n")

    groups: dict[tuple, list[int]] = {}
    for i, line in enumerate(lines):
        m = row.match(line)
        if m:
            groups.setdefault((m.group(3), m.group(1)), []).append(i)

    fixed = 0
    for (key, path), rows in groups.items():
        book = truth.get(key, {}).get(path) if name == "observability" else \
            truth.get((key, path), {})
        # 观测性：book 是行号列表；进程模型：book 是 {函数: 行号列表}
        seq: list[int] = []
        if name == "observability":
            seq = sorted(book or [])
        else:
            fnames = [_func_of(lines[i]) for i in rows]
            for fn in fnames:
                got = book.get(fn) or []
                if len(got) == 1:
                    seq.append(got[0])
        if not seq:
            continue
        for i, line_idx in enumerate(rows):
            if i >= len(seq):
                break
            m = row.match(lines[line_idx])
            old = int(m.group(2))
            if old != seq[i]:
                lines[line_idx] = lines[line_idx].replace(f"`{path}:{old}`", f"`{path}:{seq[i]}`", 1)
                fixed += 1
    led.write_bytes(("\n".join(lines)).replace("\n", "\r\n").encode("utf-8") if crlf
                    else "\n".join(lines).encode("utf-8"))
    print(f"[{name}] 按真值重写 {fixed} 行")
    return fixed


def _func_of(line: str) -> str:
    """进程模型 §三 的函数名写在「拉起：`函数名`」里（不是 `函数=` 字段）。"""
    m = re.search(r"拉起：\s*`([A-Za-z_][A-Za-z0-9_]*)`", line)
    return m.group(1) if m else ""


def run_gate(name: str) -> tuple[int, str]:
    gate = {"observability": "check_observability", "process_model": "check_process_model"}[name]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / f"{gate}.py")],
                       capture_output=True, text=True, cwd=ROOT, env=env)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def regen(name: str) -> None:
    gate = {"observability": "check_observability", "process_model": "check_process_model"}[name]
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib.util
    spec = importlib.util.spec_from_file_location(gate, ROOT / "scripts" / f"{gate}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.write_doc(ROOT)
    led = ROOT / ("docs/可观测性清单.md" if name == "observability" else "docs/进程模型清单.md")
    b = led.read_bytes()
    led.write_bytes(b.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    print(f"[{name}] 自动段已重生成 + CRLF 复原")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="按 AST 真值重定位台账手工段行号")
    ap.add_argument("targets", nargs="*", choices=["observability", "process_model"], default=None)
    ap.add_argument("--no-regen", action="store_true")
    a = ap.parse_args(argv)
    rc_all = 0
    for name in (a.targets or ["observability", "process_model"]):
        if not a.no_regen:
            regen(name)
        for attempt in range(1, 4):
            fixed = realign(name)
            rc, out = run_gate(name)
            if rc == 0:
                print(f"[{name}] 第 {attempt} 轮：门绿 ✅")
                break
            if fixed == 0:
                print(f"[{name}] 第 {attempt} 轮：无可自动修正项，剩腿需人工判断")
                for ln in [l for l in out.splitlines() if "· " in l][:5]:
                    print("   ", ln.strip()[:130])
                rc_all = 1
                break
        else:
            print(f"[{name}] 未收敛")
            rc_all = 1
    return rc_all


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
