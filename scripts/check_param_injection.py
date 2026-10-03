#!/usr/bin/env python3
"""参数注入门禁：调用了"签名里带某个关键参数且有默认值"的函数，就必须把那个参数递过去。

针对的形状（本仓 §二之十六 / §二之十八 各抓到一处真缺陷）：
- `af_mcp._t_live` 调 `svc.live_run(...)` 漏了 `store=store` ⇒ Tier-0 设备保护
  （`device_acl.json`）与实体健康视图都从 store 根目录读，人点 WebUI 会被拦下的设备，
  Agent 说同一句话直接打到真机上；
- `af_mcp._t_health` 写成 `svc.health()` ⇒ Agent 面的健康读数**从不探**它服务的那个 store，
  `store_ok` 永远是 `null`，"这一栏我没看"被下游读成"这一栏没问题"（铁律 #5）；
- `serve` 起 `build_app(..., readonly=…)` 漏 `readonly` 是同一形状的最早一例。

共同点：**带默认值的关键参数少递一个，不报错、不崩，只是那条路径上的闸门静默退化成
"没有这道闸门"**。所以判据放在调用边界上静态判，不靠人记得"每个入口面都要递"。

口径：
- 只扫 `src/`（测试里"故意不传"是合法形状，不在本门射程）；
- 目标参数表 `TARGET_PARAMS`：**只收"漏传即 fail-open"的那些**。今天两条——
  `store`（设备 ACL / 健康探测 / 存储根）与 `readonly`（写面闸门）。
  **`clock` 有意不收**：`build_runtime(graph)` 的默认时钟是仿真锚点、那是设计而非缺口
  （§二之十五），把设计判红会让门禁变成噪音，噪音会淹掉真漏传。真机时间轴那条判据在
  `tests/unit/test_af_live_run_clock.py`，它问的是"live 路径锚在真实的现在"，不是"处处递 clock"。
- 目标函数 = 模块级函数（首参不是 `self`/`cls`）、且形参表里带某个目标参数；
  **按 (模块, 函数名) 解析**，不按裸名——上一版按裸名把 `store.snapshot([...])`、
  `_direction(snapshot, ...)` 这类"变量恰好和函数同名"的正常调用判成红（28 处误报），
  一堵会自己响的墙必须先做到不误响。
- 调用点认三种写法：
  1. `别名.X(...)`：别名由 `from . import af_service as svc` / `import x as y` 解析成模块名；
  2. `X(...)`：只按**同模块**的模块级函数解析，或 `from .af_apply import apply` 这类显式导入；
  3. 转发 `_svc(svc.X, ...)`：第一实参必须是 `别名.X` **函数引用**（不是调用），
     此时关键参数必须出现在这次转发的关键字里。
- 位置传参按形参顺序数（`def f(store, *rest)` 数得出来：位置下标在 `*rest` 之前仍是确定的）；
  调用实参出现 `*`/`**` 解包时无法证明 ⇒ 放过（宁少判不误判）。
- `obj.method(...)` 一律不认：接收者静态判不出来。

已知局限：不做控制流分析；`from .mod import *` 不解析；跨模块 `importlib` 动态取函数不在射程。
判据要能指路，所以宁可漏判（下一批补）也不误判（误判会把门禁的红当噪音）。

现场豁免的形状：`# param-injection: exempt(理由)`，写在那一行的调用行或紧邻上一行。
理由不能空——空理由的豁免和没有门禁没区别，且会随时间变成无人敢删的注释。

纯标准库 AST：本机禁 pip install，依赖第三方解析器的门等于没有门禁。
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TARGET_PARAMS = ("store", "readonly")

EXEMPT_PREFIX = "param-injection"
_EXEMPT = re.compile(rf"{EXEMPT_PREFIX}:\s*exempt\(\s*(\S[^)]*)\s*\)")

Key = tuple[str, str]  # (模块名, 函数名)
#: 每个目标参数的形状：(参数名, 位置下标 or None, 是否 keyword-only)
Sig = tuple[str, int | None, bool]


def _params(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[list[str], list[str]]:
    a = fn.args
    positional = [p.arg for p in list(a.posonlyargs) + list(a.args)]
    return positional, [p.arg for p in a.kwonlyargs]


def _stem(module: str) -> str:
    return module.rsplit(".", 1)[-1]


def collect_signatures(root: Path) -> dict[Key, list[Sig]]:
    sigs: dict[Key, list[Sig]] = {}
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        mod = path.stem
        for fn in tree.body:
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            positional, kwonly = _params(fn)
            if not positional or positional[0] in ("self", "cls"):
                continue
            hits = [
                (p, positional.index(p) if p in positional else None, p in kwonly)
                for p in TARGET_PARAMS
                if p in positional or p in kwonly
            ]
            if hits:
                sigs[(mod, fn.name)] = hits
    return sigs


def _imports(tree: ast.Module, own_stem: str) -> tuple[dict[str, str], dict[str, Key]]:
    """返回 (模块别名 -> 模块名, 裸名 -> (模块名, 函数名))。

    `from . import af_service as svc`      → aliases["svc"] = "af_service"
    `from .af_apply import apply as go`    → bare["go"]    = ("af_apply", "apply")
    `import autoforge.af_mcp as m`         → aliases["m"]  = "af_mcp"
    """
    aliases: dict[str, str] = {}
    bare: dict[str, Key] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            src = _stem(node.module or "")
            if not node.module:  # `from . import x [as y]`：x 本身就是模块名
                for alias in node.names:
                    aliases[alias.asname or alias.name] = alias.name
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                bare[alias.asname or alias.name] = (src, alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                head = alias.name.split(".")[-1]
                aliases[alias.asname or alias.name.split(".")[0]] = head
    aliases.setdefault(own_stem, own_stem)
    return aliases, bare


def _call_name(node: ast.Call) -> tuple[str | None, ast.expr | None]:
    """返回 (被调名, 属性链的接收者表达式)；非 Name/Attribute 调用返回 (None, None)。"""
    f = node.func
    if isinstance(f, ast.Name):
        return f.id, None
    if isinstance(f, ast.Attribute):
        return f.attr, f.value
    return None, None


def _resolve(node: ast.Call, aliases: dict[str, str], bare: dict[str, Key], own: str) -> Key | None:
    name, receiver = _call_name(node)
    if name is None:
        return None
    if receiver is None:  # 裸名
        if name in bare:
            return bare[name]
        return (own, name)
    if isinstance(receiver, ast.Name) and receiver.id in aliases:
        return (aliases[receiver.id], name)
    return None  # obj.method(...)：接收者判不出来


def _forwarded_ref(call: ast.Call, aliases: dict[str, str]) -> Key | None:
    """`_svc(svc.live_run, ...)`：第一实参是 `别名.名` 形式的**函数引用**时解析成 (模块, 名)。"""
    if not call.args:
        return None
    first = call.args[0]
    if not isinstance(first, ast.Attribute) or not isinstance(first.value, ast.Name):
        return None
    if first.value.id not in aliases:
        return None
    return (aliases[first.value.id], first.attr)


def _passed(param: str, args: list[ast.expr], keywords: list[ast.keyword], idx: int | None, kwonly: bool) -> bool:
    if any(k.arg == param for k in keywords):
        return True
    # `f(**opts)`：解包出来的字典静态读不出键名 ⇒ 无法证明，和位置解包同档放过。
    if any(k.arg is None for k in keywords):
        return True
    if kwonly or idx is None:
        return False
    positional_given = 0
    for a in args:
        if isinstance(a, ast.Starred):
            return True
        positional_given += 1
    return positional_given > idx


def _exempted(lines: list[str], lineno: int) -> bool:
    """调用行本身或紧邻上一行有带理由的豁免标记。"""
    for idx in (lineno, lineno - 1):  # ast 是 1 基，list 是 0 基
        if 1 <= idx <= len(lines) and _EXEMPT.search(lines[idx - 1]):
            return True
    return False


def check(root: Path) -> tuple[list[str], int, int]:
    sigs = collect_signatures(root)
    findings: list[str] = []
    files = 0
    exempted = 0
    for path in sorted(root.rglob("*.py")):
        files += 1
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        lines = source.splitlines()
        own = path.stem
        aliases, bare = _imports(tree, own)
        try:
            rel = path.relative_to(REPO).as_posix()
        except ValueError:  # 测试用的临时目录树不在仓内
            rel = path.as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            key = _resolve(node, aliases, bare, own)
            offset = 0
            if key not in sigs:
                target = _forwarded_ref(node, aliases)  # 转发：目标函数是第一实参，不在实参数里
                if target not in sigs:
                    continue
                key, offset = target, 1
            call_args = node.args[offset:]
            for param, idx, kwonly in sigs[key]:
                if _passed(param, call_args, node.keywords, idx, kwonly):
                    continue
                if _exempted(lines, node.lineno):
                    exempted += 1
                    continue
                findings.append(
                    f"{rel}:{node.lineno}: `{key[0]}.{key[1]}` 的签名里有 `{param}` 形参，这一处没递"
                    "（带默认值的关键参数少递一个不报错、不崩，只会让那条路径的闸门静默少装一面）"
                )
    return findings, files, exempted


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else REPO / "src"
    if not root.is_absolute():
        root = REPO / root
    findings, files, exempted = check(root)
    if findings:
        print(f"✗ 参数注入门禁发现 {len(findings)} 处漏传（扫描 {files} 个文件）：")
        for f in findings:
            print(f"  {f}")
        print(f"  修法：把该参数递过去；确实不需要（如纯仿真面、或默认值就是设计）就地写 "
              f"`# {EXEMPT_PREFIX}: exempt(理由)`。")
        return 1
    print(
        f"✓ 参数注入门禁干净（扫描 {files} 个文件，"
        f"{len(collect_signatures(root))} 个带 {'/'.join(TARGET_PARAMS)} 的模块级函数，"
        f"现场豁免 {exempted} 处）"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
