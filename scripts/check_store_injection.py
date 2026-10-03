#!/usr/bin/env python3
"""store 注入门禁：调用了"签名里有 `store` 参数"的函数，就必须把 `store` 递过去。

背景（本仓 §二之十六 的第一处真缺陷）：`af_mcp._t_live` 调 `svc.live_run(...)` 时漏了
`store=store`，而 `live_run` 的 `store` 形参带默认值 ⇒ **少递一个关键字参数不会报错、不会崩，
只会让那条路径上的闸门整个退化成"没有 store"**：Tier-0 设备保护（`device_acl.json`）与实体
健康视图都从 store 根目录读，人点 WebUI 会被拦下的设备，Agent 说同一句话直接打到真机上。
同族的另一条是 events 上限只装在 HTTP 端点层、MCP 面没有。

这类形状的共同点是**默认值把漏传变成静默降级**，所以判据放在调用边界上静态判。

口径：
- 只扫 `src/`（测试里"故意不传"是合法形状，不在本门射程）；
- 目标函数 = 模块级函数（首参不是 `self`/`cls`）、且形参表里有叫 `store` 的参数；
  **按 (模块, 函数名) 解析**，不按裸名——上一版按裸名把 `_direction(snapshot, ...)`、
  `bool(snapshot, ...)` 这类"变量恰好和函数同名"的正常调用判成红（28 处误报），
  一堵会自己响的墙必须先做到不误响。
- 调用点认三种写法：
  1. `别名.X(...)`：别名由 `from . import af_service as svc` / `import x as y` 解析成模块名；
  2. `X(...)`：只按**同模块**的模块级函数解析，或 `from .af_apply import apply` 这类显式导入；
  3. 转发 `_svc(svc.X, ...)`：第一实参必须是 `别名.X` **函数引用**（不是调用），
     此时 `store=` 必须出现在这次转发的关键字里。
- 位置传参按形参顺序数（`def f(store, *rest)` 数得出来：位置下标在 `*rest` 之前，仍是确定的）；
  调用实参出现 `*`/`**` 解包时无法证明 ⇒ 放过（宁少判不误判）。
- `obj.method(...)` 一律不认：接收者静态判不出来。

已知局限：不做控制流分析；`from .mod import *` 不解析；跨模块 `importlib` 动态取函数不在射程。
判据要能指路，所以宁可漏判（下一批补）也不误判（会把门禁的红当噪音）。

纯标准库 AST：本机禁 pip install，依赖第三方解析器的门等于没有门禁。
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TARGET_PARAM = "store"

#: 现场豁免的形状：`# store-injection: exempt(理由)`，写在那一行的调用行或紧邻上一行。
#: 理由不能空——空理由的豁免和没有门禁没区别，且会随时间变成无人敢删的注释。
_EXEMPT = re.compile(r"store-injection:\s*exempt\(\s*(\S[^)]*)\s*\)")

Key = tuple[str, str]  # (模块名, 函数名)
Sig = tuple[int | None, bool]  # (store 位置下标 or None, 是否 keyword-only)


def _params(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[list[str], list[str]]:
    a = fn.args
    positional = [p.arg for p in list(a.posonlyargs) + list(a.args)]
    return positional, [p.arg for p in a.kwonlyargs]


def _stem(module: str) -> str:
    return module.rsplit(".", 1)[-1]


def collect_signatures(root: Path) -> dict[Key, Sig]:
    sigs: dict[Key, Sig] = {}
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        mod = path.stem
        for fn in tree.body:
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            positional, kwonly = _params(fn)
            if not positional or positional[0] in ("self", "cls"):
                continue
            if TARGET_PARAM not in positional and TARGET_PARAM not in kwonly:
                continue
            sigs[(mod, fn.name)] = (
                positional.index(TARGET_PARAM) if TARGET_PARAM in positional else None,
                TARGET_PARAM in kwonly,
            )
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


def _passed_store(args: list[ast.expr], keywords: list[ast.keyword], sig: Sig) -> bool:
    positional_idx, kwonly = sig
    if any(k.arg == TARGET_PARAM for k in keywords):
        return True
    # `f(**opts)`：解包出来的字典静态读不出键名 ⇒ 无法证明，和位置解包同档放过。
    # 判据宁可漏判也不误判：误判会把门禁的红当噪音，噪音会把真漏传（§二之十六 那一处）也一并淹掉。
    if any(k.arg is None for k in keywords):
        return True
    if kwonly or positional_idx is None:
        return False
    positional_given = 0
    for a in args:
        if isinstance(a, ast.Starred):
            return True  # 解包：无法证明，放过
        positional_given += 1
    return positional_given > positional_idx


def _exempted(lines: list[str], lineno: int) -> bool:
    """调用行本身或紧邻上一行有带理由的豁免标记。"""
    for idx in (lineno, lineno - 1):  # ast 是 1 基，list 是 0 基
        if 1 <= idx <= len(lines):
            m = _EXEMPT.search(lines[idx - 1])
            if m:
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
            missing = key in sigs and not _passed_store(node.args, node.keywords, sigs[key])
            if not missing and key not in sigs:
                target = _forwarded_ref(node, aliases)
                if target in sigs:
                    missing = not _passed_store(node.args[1:], node.keywords, sigs[target])
                    key = target
            if not missing:
                continue
            if _exempted(lines, node.lineno):
                exempted += 1
                continue
            label = f"`{key[0]}.{key[1]}`" if key else "调用"
            findings.append(
                f"{rel}:{node.lineno}: {label} 的签名里有 `store` 形参，这一处没递"
                "（漏传不会报错，只会静默退化成默认值——闸门因此少装一面）"
            )
    return findings, files, exempted


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else REPO / "src"
    if not root.is_absolute():
        root = REPO / root
    findings, files, exempted = check(root)
    if findings:
        print(f"✗ store 注入门禁发现 {len(findings)} 处漏传（扫描 {files} 个文件）：")
        for f in findings:
            print(f"  {f}")
        print("  修法：把 `store` 递过去；确实不需要（如纯仿真面）就地写 "
              "`# store-injection: exempt(理由)`。")
        return 1
    print(
        f"✓ store 注入门禁干净（扫描 {files} 个文件，"
        f"{len(collect_signatures(root))} 个带 store 的模块级函数，现场豁免 {exempted} 处）"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
