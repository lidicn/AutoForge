#!/usr/bin/env python3
"""MCP 面「参数 ⇔ inputSchema」双向一致门禁：声明的参数必须真被消费，消费的参数必须已声明。

背景（安全审计包 `docs/audit/AutoForge安全审计报告.zip`，`fp-authcode-bruteforce` 的**加重情节 3**）：
`dispatch()` 早先只做工具名查找 + scope 门，`arguments` 从不与 `inputSchema` 对账。于是
"schema 未声明却可用"这一族在 HEAD 上是**活的**——按 AST 实测（本门首跑口径）31 个工具里有
6 个键被 handler 消费却没出现在 schema 里，其中 `allow_bulk` 是**爆炸半径护栏的绕过位**，
同时出现在 `af_save` / `af_enable_by_tag` / `af_import_store` 三个写面上：`tools/list` 展示给
agent 的参数集里看不见它，调用时它却真的生效。这属于"看起来不可用、实际可用"的认知盲区，
对写面护栏来说就是绕过路径。

现在 `dispatch()` 会拒掉未声明的顶层键（声明即契约），本门钉住的是**这条新契约会烂掉的方式**：

1. **消费未声明**（handler 读了 `args["k"]` / `args.get("k")` 而 schema 没写 `k`）⇒ 判红。
   在 `dispatch()` 拒绝之后，这一族的后果从"静默生效"变成"**静默失效**"——调用方传了、
   被拒了、代码里那行读取永远拿不到值。比原来更隐蔽，所以必须有静态判据。
2. **声明未消费**（schema 写了 `k`，handler 一次都不读）⇒ 判红。这是镜像方向：`tools/list`
   告诉 agent"可以传"，实现里没人接，等于对调用方撒谎。
3. **schema 缺 `properties`** ⇒ 判红。`dispatch()` 的拒绝逻辑对读不出声明形状的 schema
   是**不置防**（`_undeclared_args` 返回空），缺 properties 就等于那道拒绝在该工具上静默关闭。
4. **参数读取解析不出**（`args` 被整包传给别的函数、`args[k]` 的键名是变量、handler 定义不
   在本文件里）⇒ **exit 2，不是"这条跳过"**。判据一旦能靠"读不出"通过，绿行就毫无意义
   （§二之二十七 那条正则版谎报"缺失 0"是同型事故）。

口径与边界：
- 唯一真源 = `src/autoforge/af_mcp.py` 的 `TOOLS` 列表字面量：每个元组的
  `(名字, 描述, inputSchema, handler, scope)`。
- 只统计 handler **自身函数体**里的直接读取；`args` 转发给 helper 的写法本门会 exit 2
  （今天 31 个工具 0 处，所以这条边界是"要改形状得先过门"，不是豁免通道）。
- 现场豁免 `# mcp-args: exempt(理由)`，写在同一行或紧邻上一行，理由不能空。

纯标准库 AST：本机禁 pip install，依赖第三方解析器的门等于没有门禁。
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REGISTRY = REPO / "src" / "autoforge" / "af_mcp.py"
_ARGS = "args"
_EXEMPT = re.compile(r"mcp-args:\s*exempt\(\s*(\S[^)]*)\s*\)")


def _prop_keys(schema: ast.Dict) -> tuple[list[str], bool]:
    """返回 (properties 的键名列表, 是否读到 properties)。非字符串常量键忽略。"""
    for key, value in zip(schema.keys, schema.values):
        if isinstance(key, ast.Constant) and key.value == "properties":
            if not isinstance(value, ast.Dict):
                return [], True
            return [k.value for k in value.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)], True
    return [], False


def _arg_reads(fn: ast.FunctionDef) -> tuple[set[str], list[str]]:
    """返回 (handler 体内直接消费的 args 顶层键, 解析不出的读取描述)。"""
    reads: set[str] = set()
    unresolved: list[str] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == _ARGS:
            sl = node.slice
            if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                reads.add(sl.value)
            else:
                unresolved.append(f"{fn.name}:{node.lineno} args[…] 键名不是字面量")
        elif isinstance(node, ast.Call):
            f = node.func
            is_get = (
                isinstance(f, ast.Attribute)
                and f.attr in ("get", "pop", "setdefault")
                and isinstance(f.value, ast.Name)
                and f.value.id == _ARGS
            )
            if is_get:
                first = node.args[0] if node.args else None
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    reads.add(first.value)
                else:
                    unresolved.append(f"{fn.name}:{node.lineno} args.{f.attr}() 键名不是字面量")
                continue
            for a in list(node.args) + [kw.value for kw in node.keywords]:
                if isinstance(a, ast.Name) and a.id == _ARGS:
                    unresolved.append(f"{fn.name}:{node.lineno} args 整包转发给 {ast.dump(a)}")
        elif isinstance(node, ast.Starred):
            if isinstance(node.value, ast.Name) and node.value.id == _ARGS:
                unresolved.append(f"{fn.name}:{node.lineno} **args 展开")
    return reads, unresolved


def _tools_entries(tree: ast.Module) -> tuple[list[tuple[str, ast.expr, int]], str]:
    """返回 TOOLS 列表项 `(工具名, schema 节点, 行号)`；读不出时给出原因。"""
    for node in tree.body:
        if isinstance(node, ast.AnnAssign):
            targets: list[ast.expr] = [node.target]
        elif isinstance(node, ast.Assign):
            targets = list(node.targets)
        else:
            continue
        if not any(getattr(t, "id", "") == "TOOLS" for t in targets) or not isinstance(node.value, ast.List):
            continue
        out: list[tuple[str, ast.expr, int]] = []
        for el in node.value.elts:
            if not isinstance(el, (ast.Tuple, ast.List)) or len(el.elts) < 4:
                return [], f"TOOLS 第 {getattr(el, 'lineno', '?')} 项不是 (name, desc, schema, handler, …) 五元组形状"
            name, schema, handler = el.elts[0], el.elts[2], el.elts[3]
            if not isinstance(name, ast.Constant) or not isinstance(name.value, str):
                return [], f"TOOLS 第 {el.lineno} 项的工具名不是字符串常量"
            if not isinstance(handler, ast.Name):
                return [], f"TOOLS {name.value} 的 handler 不是本文件内的具名函数（{type(handler).__name__}）"
            out.append((name.value, schema, handler.id))
        if not out:
            return [], "TOOLS 找到了但一个条目都没解析出来（形状变了？见脚本 docstring）"
        return out, ""
    return [], "没找到 `TOOLS: list[...]` 的列表字面量（改名/挪走会让本门静默全绿）"


def collect(path: Path) -> tuple[list[dict], list[str], str]:
    """返回 (每个工具的 {name, declared, consumed} 记录, 解析不出的读取, 致命错误)。"""
    if not path.is_file():
        return [], [], f"注册表文件不在盘上：{path.as_posix()}"
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    tree = ast.parse(text, filename=str(path))
    fns = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    entries, err = _tools_entries(tree)
    if err:
        return [], [], err
    rows: list[dict] = []
    unresolved: list[str] = []
    for name, schema_node, handler_name in entries:  # type: ignore[assignment]
        if not isinstance(schema_node, ast.Dict):
            return [], [], f"{name} 的 inputSchema 不是字典字面量（无法核对参数）"
        declared, has_props = _prop_keys(schema_node)
        fn = fns.get(handler_name)
        if fn is None:
            return [], [], f"{name} 的 handler `{handler_name}` 不在 {path.name} 顶层（读不出它消费哪些参数）"
        consumed, unres = _arg_reads(fn)
        unresolved.extend(unres)
        start = max(0, fn.lineno - 2)
        end = getattr(fn, "end_lineno", None) or (fn.lineno + len(fn.body) + 1)
        exempt = any(_EXEMPT.search(l) for l in lines[start:end])
        rows.append({
            "name": name,
            "declared": set(declared),
            "has_props": has_props,
            "consumed": consumed,
            "exempt": exempt,
        })
    return rows, unresolved, ""


def check(rows: list[dict]) -> list[str]:
    findings: list[str] = []
    for r in rows:
        if r["exempt"]:
            continue
        if not r["has_props"]:
            findings.append(f"{r['name']}：inputSchema 没有 `properties` ⇒ dispatch 的未声明键拒绝在该工具上静默关闭")
            continue
        undeclared = sorted(r["consumed"] - r["declared"])
        unconsumed = sorted(r["declared"] - r["consumed"])
        if undeclared:
            findings.append(f"{r['name']}：消费未声明的参数 {', '.join(undeclared)}（tools/list 看不见却生效／被拒后静默失效）")
        if unconsumed:
            findings.append(f"{r['name']}：声明未消费的参数 {', '.join(unconsumed)}（对调用方撒谎）")
    return findings


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else REGISTRY
    rows, unresolved, err = collect(path)
    if err:
        print(f"✗ 射程读不成：{err}")
        print("结论：读不出参数形状时不报『干净』（exit 2）。")
        return 2
    if unresolved:
        print("✗ 射程读不成：以下参数读取静态判不出来——")
        for u in unresolved:
            print(f"  - {u}")
        print("结论：本门的判据要求「每个消费点都能对上声明」，读不出的那条不许当跳过（exit 2）。")
        return 2
    findings = check(rows)
    exempt = sum(1 for r in rows if r["exempt"])
    declared_total = sum(len(r["declared"]) for r in rows)
    consumed_total = sum(len(r["consumed"]) for r in rows)
    if findings:
        print(f"✗ MCP 参数↔schema 门禁发现 {len(findings)} 条：")
        for f in findings:
            print(f"  - {f}")
        print(f"（扫描 {len(rows)} 个工具，现场豁免 {exempt} 处）")
        return 1
    print(
        f"✓ MCP 参数↔schema 门禁干净（{len(rows)} 个工具：声明参数 {declared_total} 个、"
        f"handler 消费 {consumed_total} 个，双向差额 0；现场豁免 {exempt} 处）"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
