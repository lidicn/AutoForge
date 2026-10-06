#!/usr/bin/env python3
"""出站护栏门禁：第一方出站 HTTP 必须走 `af_adapters.http.guarded_open`，不许直连 `urlopen`。

缘起（第十四轮审计 F15 / OUTB-01）：本仓的出站白名单护栏本身写得很认真——`host_of()` 连
netloc 里的 `@` 凭证注入都判（`http://evil@whitelisted.com` 返回空串），`_WhitelistRedirector`
还会在跟随 3xx 之前对 `Location` **重过一遍白名单**。问题是**有代码绕开了它**：
`af_catalog` / `af_live` / `af_metrics` / `af_registry` 四处各自直连 `urlopen`，用的是**默认
opener**——默认 opener 跟 3xx 且不对 `Location` 重校验，于是"守得住首跳、守不住重定向目标"
这条 HI-03 的修法对那四条路完全没生效。`af_metrics` 那一处尤其实在：它带着 `Bearer` 凭据，
一次 3xx 就能把凭据转发到白名单外的主机。

四条腿（全部静态可判，锚点只认 AST 的 Name/Attribute ⇒ 注释与文档串里出现 `urlopen` 不算命中，
这是 §二之五十二 那条"散文踩名字哨兵"教训的正面用法）：

  A. 裸出站腿：被调者解析出 `urlopen`（`urlopen(...)`、`x.urlopen(...)`、以及审计点名 bandit
     漏掉的那一种——`(opener or urllib.request.urlopen)(...)`，**布尔表达式作被调者**）⇒ 一律判红。
     同族另一半也收在 A 腿：**把 `urlopen` 当函数对象交出去**（`self._opener = opener or urlopen`），
     之后在别处 `self._opener(req)` 调用——那种"绕一圈"的写法 bandit 同样漏，且本门按被调者
     名字追不到它，所以判红点前移到赋值那一行。
     确实要留这一站的，就地写 `# outbound-guard: exempt(理由)`，理由为空判红。
  B. 重定向腿：每个 `build_opener(` 站点必须在**同一条语句或同一个函数**里挂上两种重定向守卫
     之一（`_WhitelistRedirector` 重校验 / `_NoRedirectHandler` 直接拒绝跟随）——新建一个不挂
     守卫的 opener 就是重新挖一条旁路。
  C. 白名单腿：每个 `guarded_open(` 调用必须带 `allowed_hosts=` 关键字。收口点存在却被忘了传
     白名单，读代码的人看不出来。
  D. 反空洞腿：A∪B∪C 的出站站点数为 0 ⇒ `exit 2`。"一个出站站点都没扫到"不是干净，是本门
     失去了射程（出站换了库、或 `guarded_open` 改了名）。

本门判的是**形状**：走没走收口点、有没有挂重定向守卫。白名单内容对不对（该放哪台主机）是配置
与裁定问题，不归静态门。
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"

#: 就地豁免标记（与原子写门 `# fixed-tmp: exempt(理由)` 同一语法形状）
EXEMPT = re.compile(r"outbound-guard:\s*exempt\(([^)]*)\)")
GUARD_CALL = "guarded_open"
REDIRECT_HANDLERS = ("_WhitelistRedirector", "_NoRedirectHandler")


def _name_of(func: ast.expr) -> str:
    """被调者的末端名字：`urlopen(...)` / `x.urlopen(...)` / `a.b.urlopen(...)` 都取到 `urlopen`。"""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _unwrap(func: ast.expr) -> list[ast.expr]:
    """把"表达式作被调者"的形状拆平：`(opener or urllib.request.urlopen)(req)` 的被调者是 BoolOp。

    bandit 的 B310 只认直接调用形态，这一拆就是审计里"自建规则补漏"的那一半。
    """
    out: list[ast.expr] = [func]
    stack = [func]
    while stack:
        node = stack.pop()
        if isinstance(node, ast.BoolOp):
            children = list(node.values)
        elif isinstance(node, ast.IfExp):
            children = [node.body, node.orelse]
        else:
            children = []
        for child in children:
            out.append(child)
            stack.append(child)
    return out


class Visitor(ast.NodeVisitor):
    def __init__(self, path: Path, lines: list[str], root: Path):
        self.path = path
        self.root = root
        self.lines = lines
        self.raw: list[tuple[int, str]] = []      # A 腿：裸 urlopen 站点
        self.exempt: list[tuple[int, str]] = []
        self.guarded: list[tuple[int, str]] = []  # C 腿：guarded_open 调用
        self.openers: list[tuple[int, str]] = []  # B 腿：build_opener 站点
        self.findings: list[str] = []
        self._stack: list[ast.AST] = []

    # -- 作用域 helpers ---------------------------------------------------- #

    def _scope(self) -> ast.AST | None:
        for node in reversed(self._stack):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return node
        return None

    @staticmethod
    def _calls_in(scope: ast.AST) -> set[str]:
        return {
            _name_of(call.func)
            for call in ast.walk(scope)
            if isinstance(call, ast.Call)
        }

    @staticmethod
    def _names_in(node: ast.AST) -> set[str]:
        found: set[str] = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name):
                found.add(sub.id)
            elif isinstance(sub, ast.Attribute):
                found.add(sub.attr)
        return found

    def _exempt_reason(self, lineno: int) -> str | None:
        """命中行或其上一行写了豁免标记 ⇒ 返回理由（可能是空串）。"""
        for candidate in (lineno, lineno - 1):
            if 1 <= candidate <= len(self.lines):
                m = EXEMPT.search(self.lines[candidate - 1])
                if m:
                    return m.group(1).strip()
        return None

    def _flag_raw(self, lineno: int, scope_name: str, shape: str) -> None:
        self.raw.append((lineno, scope_name))
        reason = self._exempt_reason(lineno)
        if reason is None:
            self.findings.append(
                f"[裸出站] {self.path.relative_to(self.root).as_posix()}:{lineno} 函数 "
                f"{scope_name}() {shape} ⇒ 走默认 opener：跟 3xx 且不对 Location 重校验。"
                "改走 guarded_open(req, allowed_hosts=(host_of(自己的 base_url),), …)"
            )
        elif not reason:
            self.findings.append(
                f"[豁免空转] {self.path.relative_to(self.root).as_posix()}:{lineno} "
                "`# outbound-guard: exempt(理由)` 的理由是空的——空着等于没豁免"
            )
        else:
            self.exempt.append((lineno, scope_name))

    # -- 访问 -------------------------------------------------------------- #

    def _visit_scope(self, node: ast.AST) -> None:
        """维护函数作用域栈：A/B 两条腿都按『所在函数』而不是『所在文件』判定。

        文件级判断是本门刻意不要的形状——审计 W38 记过一次：第一版按文件排除护栏符号，
        结果真实仓库 4 处全被误判为"已走护栏"（这些文件的**别处**提到过护栏符号）。
        """
        self._stack.append(node)
        self.generic_visit(node)
        self._stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._visit_scope(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._visit_scope(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        scope = self._scope()
        scope_name = getattr(scope, "name", "<module>")
        callee_names = {_name_of(f) for f in _unwrap(node.func)}

        if GUARD_CALL in callee_names:
            self.guarded.append((node.lineno, scope_name))
            if not any(kw.arg == "allowed_hosts" for kw in node.keywords):
                self.findings.append(
                    f"[白名单腿] {self.path.relative_to(self.root).as_posix()}:{node.lineno} 函数 "
                    f"{scope_name}() 调用 guarded_open 却没传 allowed_hosts= ⇒ 收口点在，白名单不在"
                )
            self.generic_visit(node)
            return

        if "build_opener" in callee_names:
            self.openers.append((node.lineno, scope_name))
            holders = {n for n in self._names_in(node) if n in REDIRECT_HANDLERS}
            if not holders and scope is not None:
                holders = {n for n in self._names_in(scope) if n in REDIRECT_HANDLERS}
            if not holders:
                self.findings.append(
                    f"[重定向腿] {self.path.relative_to(self.root).as_posix()}:{node.lineno} 函数 "
                    f"{scope_name}() 新建 opener 却没挂重定向守卫"
                    f"（{' 或 '.join(REDIRECT_HANDLERS)} 二选一）⇒ 3xx 的 Location 不再校验"
                )

        if "urlopen" in callee_names:
            self._flag_raw(node.lineno, scope_name, "直连 urlopen")

        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        """`self._opener = opener or urllib.request.urlopen`：把默认出站函数当值交出去。

        这一形状的危险在于**判红点会漂走**——真正的请求发生在别处的 `self._opener(req)`，
        按被调者名字追不到（`_opener` 不含 `urlopen` 三个字）。所以红点前移到赋值这一行。
        """
        scope = self._scope()
        scope_name = getattr(scope, "name", "<module>")
        if "urlopen" in {_name_of(f) for f in _unwrap(node.value)}:
            self._flag_raw(node.lineno, scope_name, "把 urlopen 当可调用对象交出")
        self.generic_visit(node)


def scan(root: Path) -> tuple[list[str], dict[str, int], list[str]]:
    findings: list[str] = []
    counts = {"files": 0, "raw": 0, "exempt": 0, "guarded": 0, "openers": 0}
    broken: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        counts["files"] += 1
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            broken.append(f"{path.relative_to(REPO).as_posix()}: {exc}")
            continue
        v = Visitor(path, path.read_text(encoding="utf-8").splitlines(), root)
        v.visit(tree)
        findings += v.findings
        counts["raw"] += len(v.raw)
        counts["exempt"] += len(v.exempt)
        counts["guarded"] += len(v.guarded)
        counts["openers"] += len(v.openers)
    return findings, counts, broken


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="出站护栏门禁")
    ap.add_argument("root", nargs="?", default=str(SRC))
    args = ap.parse_args(argv[1:])
    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"出站护栏门禁：扫描目录不存在 {root}")
        return 2

    findings, counts, broken = scan(root)
    if broken:
        print("出站护栏门禁：有 .py 解析失败 ⇒ 射程不完整，不下结论：" + "；".join(broken[:5]))
        return 2
    in_range = counts["raw"] + counts["guarded"] + counts["openers"]
    if in_range == 0:
        print(
            f"出站护栏门禁：扫描 {counts['files']} 个文件，裸出站 / guarded_open / build_opener "
            "三类站点一个都没扫到 ⇒ 出站形状整体换了名字或换了库，本门失去射程（这不是干净）"
        )
        return 2

    for line in findings:
        print(f"  ✗ {line}")
    if findings:
        print(
            f"\n出站护栏门禁报红：{len(findings)} 条。射程 {in_range} 处"
            f"（裸 urlopen {counts['raw']}、guarded_open {counts['guarded']}、"
            f"build_opener {counts['openers']}）。"
        )
        return 1
    print(
        f"✓ 出站护栏门禁干净（扫描 {counts['files']} 个文件、出站站点 {in_range} 处："
        f"走 guarded_open 收口 {counts['guarded']} 处、自建 opener {counts['openers']} 处"
        f"（都挂了重定向守卫）、裸 urlopen {counts['raw'] - counts['exempt']} 处"
        f"（另有就地豁免 {counts['exempt']} 处））"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
