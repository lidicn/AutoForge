#!/usr/bin/env python3
"""工具名单唯一真源门禁：按名字调 MCP 工具的地方，那个名字必须在 `af_mcp.TOOLS` 里注册。

背景（§二之十八 之后盘"TOOLS→caps 之外有没有第二次工具名单映射"抓到的两处）：
- `af_orchestrator.observe()` 调 `self._call_safe("af_live", …)`，而注册名是 **`af_live_run`**；
  `_call_safe` 把异常吞成 `{"ok": False}` ⇒ 这条"观察真机"的路径**永远不会响**。
- `af_runtime_ext.mcp_tools()` 另立了一份 5 个 `af_*` 名字的字典，**从未被任何调用方接线**，
  其中 `af_approve_proposal` 是"Agent 自己批准提案"的写面——与裁定 20261002 §三 ④A
  「只落盘、人批后才进可执行队列」和 `af_api.py` 里"approve 只在服务层，MCP 面绝不注册"直接冲突。

两处都不是"运行期崩了"的缺陷，而是**名单手抄第二份、一边改名另一边不知道**：不报错、不崩，
只让那条路静默不通，或者让人日后把一份没审过的名单接线上去。所以判据静态判，且两向都判：

1. **按名调用的字面量**必须是 TOOLS 里的名字：
   `_call("af_x", …)` / `_call_safe("af_x", …)` / `X.call("af_x", …)` / `submit_pending(store, "af_x", …)`
   / `dispatch(tool="af_x", …)` / 形参默认值 `build_tool="af_x"`、`tool="af_x"`。只认 `af_` 开头
   且整个串就是工具名形状的字面量，因此 `adapter.call("light.turn_on", …)`（另一套命名）和
   `logger.warning("af_persist: 校验和不匹配…")`（日志文案）都不在射程。
2. **第二份名单本身**判红：非 `af_mcp.py` 里的字典字面量出现 ≥2 个 `af_*` 字符串键
   ⇒ 这就是又一份手抄的工具表（`mcp_tools()` 当年的形状）。

口径与边界：
- 唯一真源 = `src/autoforge/af_mcp.py` 里 `TOOLS: list[...]` 每个元组的第一个字符串常量。
  **读不到 TOOLS 就 exit 2**：锚点消失时报"0 处发现"是假绿（§二之十四 那次门自己是假洞，同型）。
- 变量名（`self._call(tool, …)`）不判：静态不知道值。
- 现场豁免 `# tool-name: exempt(理由)`，写在同一行或紧邻上一行，理由不能空。

纯标准库 AST：本机禁 pip install，依赖第三方解析器的门等于没有门禁。
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REGISTRY = REPO / "src" / "autoforge" / "af_mcp.py"
#: 工具名的严格形状：日志文案里的 "af_persist: 校验和不匹配…" 不该被当成工具名
_TOOL_RE = re.compile(r"af_[a-z][a-z0-9_]*")
#: 形参名里带工具语义的（`tool`、`build_tool`、`mcp_tool`）才允许手抄默认值
_TOOL_PARAM_RE = re.compile(r"(?:[a-z0-9]+_)?tool")
#: 按名调用工具的函数/方法名（第一实参是工具名）
CALL_FUNCS = {"_call", "_call_safe", "call", "dispatch"}
#: `submit_pending(store, tool, payload)`：工具名在第二个位置实参
TOOL_ARG_INDEX = {"submit_pending": 1}
_EXEMPT = re.compile(r"tool-name:\s*exempt\(\s*(\S[^)]*)\s*\)")


def registry_names() -> tuple[set[str], str]:
    """返回 (TOOLS 里注册的工具名, 错误说明)。读不到时名字集合为空、说明非空。"""
    if not REGISTRY.is_file():
        return set(), f"注册表文件不在盘上：{REGISTRY.as_posix()}"
    tree = ast.parse(REGISTRY.read_text(encoding="utf-8"), filename=str(REGISTRY))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign):
            targets: list[ast.expr] = [node.target]
        elif isinstance(node, ast.Assign):
            targets = list(node.targets)
        else:
            continue
        if not any(getattr(t, "id", "") == "TOOLS" for t in targets) or not isinstance(node.value, ast.List):
            continue
        out: set[str] = set()
        for el in node.value.elts:
            if isinstance(el, (ast.Tuple, ast.List)) and el.elts and isinstance(el.elts[0], ast.Constant) \
                    and isinstance(el.elts[0].value, str):
                out.add(el.elts[0].value)
        if not out:
            return set(), "TOOLS 找到了但一个工具名都没解析出来（形状变了？见脚本 docstring）"
        return out, ""
    return set(), "没找到 `TOOLS: list[...]` 的列表字面量（改名/挪走会让本门静默全绿）"


def _func_name(f: ast.expr) -> str | None:
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _named_defaults(args: ast.arguments) -> list[tuple[str, ast.expr | None]]:
    """把默认值和**形参名**对齐：`defaults` 只对应位置参数尾部，`kw_defaults` 与 `kwonlyargs` 一一对应。"""
    pos = list(getattr(args, "posonlyargs", [])) + list(args.args)
    pairs: list[tuple[str, ast.expr | None]] = []
    offset = len(pos) - len(args.defaults)
    for i, d in enumerate(args.defaults):
        pairs.append((pos[offset + i].arg, d))
    for a, d in zip(args.kwonlyargs, args.kw_defaults):
        pairs.append((a.arg, d))
    return pairs


def _is_exempt(lines: list[str], lineno: int) -> bool:
    for idx in (lineno, lineno - 1):
        if 1 <= idx <= len(lines) and _EXEMPT.search(lines[idx - 1]):
            return True
    return False


def _literal_tool(node: ast.Call) -> ast.Constant | None:
    """从按名调用里取出工具名字面量；不是字面量、或调用面不在名单里就返回 None（不判）。"""
    fname = _func_name(node.func)
    if fname is None or (fname not in CALL_FUNCS and fname not in TOOL_ARG_INDEX):
        return None
    cands: list[ast.expr] = []
    idx = TOOL_ARG_INDEX.get(fname, 0)
    if len(node.args) > idx:
        cands.append(node.args[idx])
    for kw in node.keywords or []:
        if kw.arg == "tool":
            cands.append(kw.value)
    for a in cands:
        if isinstance(a, ast.Constant) and isinstance(a.value, str) and _TOOL_RE.fullmatch(a.value):
            return a
    return None


def check(root: Path, tools: set[str]) -> tuple[list[str], int, int]:
    findings: list[str] = []
    files = 0
    exempted = 0
    for path in sorted(root.rglob("*.py")):
        files += 1
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        lines = source.splitlines()
        try:
            rel = path.relative_to(REPO).as_posix()
        except ValueError:
            rel = path.as_posix()
        own_registry = path.resolve() == REGISTRY.resolve()

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                lit = _literal_tool(node)
                if lit is None or lit.value in tools:
                    continue
                if _is_exempt(lines, lit.lineno):
                    exempted += 1
                    continue
                findings.append(
                    f"{rel}:{lit.lineno}: 按名调用 `{lit.value}`，但 `af_mcp.TOOLS` 里没有这个名字"
                    f"（注册表 {len(tools)} 个名字；改名不报错、不崩，只让这条路径永远走不通）"
                )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # 形参默认值 `build_tool="af_build"`：也是一种手抄的工具名。只认工具名形状的形参，
                # 免得把 `topic="af_x"` 这类别的字符串默认值一并误伤。
                for name, d in _named_defaults(node.args):
                    if not _TOOL_PARAM_RE.fullmatch(name):
                        continue
                    if not (isinstance(d, ast.Constant) and isinstance(d.value, str)
                            and _TOOL_RE.fullmatch(d.value) and d.value not in tools):
                        continue
                    if _is_exempt(lines, d.lineno):
                        exempted += 1
                        continue
                    findings.append(
                        f"{rel}:{d.lineno}: `{node.name}` 的默认工具名 `{d.value}` 不在 `af_mcp.TOOLS` 里"
                    )
            elif isinstance(node, ast.Dict) and not own_registry:
                # 第二份工具名单：≥2 个 af_* 字符串键的字典字面量
                keys = [k for k in node.keys if isinstance(k, ast.Constant)
                        and isinstance(k.value, str) and _TOOL_RE.fullmatch(k.value)]
                if len(keys) < 2:
                    continue
                if _is_exempt(lines, node.lineno):
                    exempted += 1
                    continue
                names = "、".join(sorted({k.value for k in keys}))
                findings.append(
                    f"{rel}:{node.lineno}: 这里是第二份工具名单（{names}）——"
                    "`TOOLS` 之外再抄一份，改名时一边不知道，且从未接线的名单随时可能被人接上写面"
                )
    return findings, files, exempted


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else REPO / "src"
    if not root.is_absolute():
        root = REPO / root
    tools, err = registry_names()
    if err:
        print(f"✗ 工具名单门禁读不到注册表：{err}")
        return 2
    findings, files, exempted = check(root, tools)
    if findings:
        print(f"✗ 工具名单门禁发现 {len(findings)} 处（扫描 {files} 个文件，注册表 {len(tools)} 个工具）：")
        for f in findings:
            print(f"  {f}")
        print("  修法：用 `TOOLS` 里注册的名字；确实不是 MCP 工具名（HA 服务名/其他前缀）就地写 "
              "`# tool-name: exempt(理由)`。")
        return 1
    print(f"✓ 工具名单门禁干净（扫描 {files} 个文件，注册表 {len(tools)} 个工具，"
          f"按名调用点全部命中，现场豁免 {exempted} 处）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
