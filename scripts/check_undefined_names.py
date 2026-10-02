#!/usr/bin/env python3
"""undefined-name 门禁：抓"这个名字整份文件里就没定义过"这一类崩溃。

为什么要有它（不是偏好，是复现过的事故）：
- 外部审计 REG-3 指出第 1-2 轮那批缺陷（未定义名之类）没有任何门禁能报红；
- 2026-10-02 本轮自己踩到一次：`af_time.py` 引用了尚未定义的常量，
  `import autoforge.af_service` 当场 NameError，整套测试连收集都跑不起来。

只依赖标准库——这台机器禁 pip install，任何要装包的门禁等于没有门禁。

**判据刻意保守**：只有"某名字以 Load 出现，而它在整份文件里从未以任何绑定形态出现
（赋值/形参/import/def/class/except/comprehension/match/global）"才报。
跨作用域误用（在 A 函数里定义、B 函数里裸用）不在本门禁射程内——那是 mypy/pyright 的活，
需要装包与变更窗口。宁可少抓一类，也不做一条天天误报、两天后被 `continue-on-error` 掉的红。
"""
from __future__ import annotations

import ast
import builtins
import sys
from pathlib import Path

BUILTIN_NAMES = frozenset(dir(builtins))

#: 解释器运行期注入、AST 里看不到定义的名字。
IMPLICIT_NAMES = frozenset({
    "__name__", "__file__", "__doc__", "__package__", "__loader__", "__spec__",
    "__path__", "__builtins__", "__debug__", "__class__", "__annotations__",
    "__all__", "__dict__", "__module__", "__qualname__", "__firstlineno__",
    "__static_attributes__", "typing",
})

#: 名字全集一旦不可静态确定，整份文件让路（承认看不见，而不是硬猜一个红）。
#: 判到**调用点**（`ast.Call` 的函数名真是 `exec`/`eval`），不是判字符串——
#: 否则一句 `executed_at = ...` 就能让一个文件永久脱离门禁（本仓 `af_health.py` 就是这种形状）。
def _has_dynamic_scope(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("exec", "eval"):
            return True
        if isinstance(node, (ast.ImportFrom,)) and any(a.name == "*" for a in node.names):
            return True   # `from x import *`：名字全集不可知
    return False


def _bound_names(tree: ast.AST) -> set[str]:
    """一份文件里所有"曾经绑定过"的名字。"""
    names: set[str] = set()

    def add_target(target: ast.AST) -> None:
        for sub in ast.walk(target):
            if isinstance(sub, ast.Name):
                names.add(sub.id)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = node.args
                for param in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
                    names.add(param.arg)
                if args.vararg:
                    names.add(args.vararg.arg)
                if args.kwarg:
                    names.add(args.kwarg.arg)
        elif isinstance(node, ast.Lambda):
            args = node.args
            for param in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
                names.add(param.arg)
            if args.vararg:
                names.add(args.vararg.arg)
            if args.kwarg:
                names.add(args.kwarg.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                add_target(target)
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            add_target(node.target)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    add_target(item.optional_vars)
        elif isinstance(node, ast.ExceptHandler):
            if node.name:
                names.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
        elif isinstance(node, ast.MatchAs):
            if node.name:
                names.add(node.name)
        elif isinstance(node, ast.MatchStar):
            names.add(node.name)
        elif isinstance(node, ast.MatchMapping):
            if node.rest:
                names.add(node.rest)
        elif isinstance(node, ast.TypeAlias):
            add_target(node.name)
    return names


def check_source(text: str, path: Path) -> list[str]:
    """这份源码里"用了但从没定义过"的名字清单（空 = 干净）。"""
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return [f"{path}: 语法错误：{exc}"]
    if _has_dynamic_scope(tree):
        return []

    bound = _bound_names(tree) | IMPLICIT_NAMES | BUILTIN_NAMES
    findings = [
        f"{path}:{node.lineno} 未定义的名字 `{node.id}`"
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id not in bound
    ]
    return findings


def check_file(path: Path) -> list[str]:
    return check_source(path.read_text(encoding="utf-8-sig"), path)  # utf-8-sig：带 BOM 的文件不是语法错误


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path("src")
    files = [p for p in sorted(root.rglob("*.py")) if ".egg-info" not in p.parts]
    offenders: list[str] = []
    for path in files:
        offenders.extend(check_file(path))
    if offenders:
        print(f"✗ undefined-name 门禁：{len(offenders)} 处")
        for line in offenders:
            print(f"  {line}")
        return 1
    print(f"✓ undefined-name 门禁干净（扫描 {len(files)} 个文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
