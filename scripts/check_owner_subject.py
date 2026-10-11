#!/usr/bin/env python3
"""owner 主体单一真源 ＋ `/api/user/agents/*` 主体校验门禁。

依据：裁定 `20261011-AF稳定性第三轮残余八问-裁定.md` §3 Q1 丙。
本门**只判不写**——⛔ 不动 `af_api.py` 一行码（那格归登录线，#79 窗）。

两条判据：
  A「主体名单单一真源」：拿字符串字面量当"是不是 owner 面"的判定，就是 `_OWNER_SUBJECTS`
    之外的第二份名单。字面量名单在两边各改一次都不会有人报错，而 F-02 撞的恰恰是这个：
    签发侧把主体改名，判定侧还拿着旧字面量比。
  B「`/api/user/agents/{…}` 必带主体校验」：路径里带着别人主体名的端点，处理器必须把
    **调用方的主体**与**路径参数**做一次绑定，否则一枚 write 令牌就能撤销／改名任何人的
    agent（审计 §六 I，HEAD 上今天就成立）。

退出码就是结论：0 干净 / 1 有未获批违规 / 2 射程塌（锚点读不出＝本门无从判定，报"干净"就是假绿）。
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
API_REL = "src/autoforge/af_api.py"
SUBJECT_SYMBOL = "_OWNER_SUBJECTS"
AGENTS_PREFIX = "/api/user/agents"
EXEMPT_MARKER = "# owner-subject: exempt("
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
PRINCIPAL_ANCHORS = {"authenticated"}
_SUBJECT_WORD = re.compile(r"subject", re.I)
_PATH_PARAM = re.compile(r"\{(\w+)(?::[^}]*)?\}")


def _refs_subject(node: ast.AST) -> bool:
    """这个表达式是不是"某个主体的主体名"——`info.subject`／`current_subject`／`s["subject"]`。"""
    if isinstance(node, ast.Name):
        return bool(_SUBJECT_WORD.search(node.id))
    if isinstance(node, ast.Attribute):
        return bool(_SUBJECT_WORD.search(node.attr)) or _refs_subject(node.value)
    if isinstance(node, ast.Subscript):
        sl = node.slice
        if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
            if _SUBJECT_WORD.search(sl.value):
                return True
        return _refs_subject(sl) or _refs_subject(node.value)
    if isinstance(node, ast.Call):
        for arg in [*node.args, *(kw.value for kw in node.keywords if kw.value)]:
            if _refs_subject(arg):
                return True
    return False


def _literal_subject_values(node: ast.AST) -> list[str] | None:
    """字面量主体名单：单个字符串常量，或元素全是字符串常量的 Tuple／Set／List。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, (ast.Tuple, ast.Set, ast.List)):
        vals = [e.value for e in node.elts]
        if vals and all(isinstance(v, str) for v in vals):
            return list(vals)
    return None


def subject_literal_compares(tree: ast.AST) -> list[dict[str, object]]:
    """判据 A 的站点：一侧引用主体、另一侧是字符串字面量（或字面量名单）的比较。"""
    sites: list[dict[str, object]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        operands = [node.left, *node.comparators]
        has_subject = any(_refs_subject(o) for o in operands)
        if not has_subject:
            continue
        for op, other in zip(node.ops, node.comparators):
            if not isinstance(op, (ast.In, ast.NotIn, ast.Eq, ast.NotEq)):
                continue
            values = _literal_subject_values(other)
            if values:
                sites.append({"lineno": node.lineno, "values": values, "src": ast.unparse(node)})
    return sites


def _owner_symbol_line(tree: ast.AST) -> int | None:
    """`_OWNER_SUBJECTS = …` 那一行——它是名单的唯一合法定义处。"""
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name) and t.id == SUBJECT_SYMBOL:
                    return node.lineno
    return None


def agents_endpoints(tree: ast.AST) -> list[dict[str, object]]:
    """`/api/user/agents*` 的端点及其处理器形状。"""
    out: list[dict[str, object]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
                continue
            if dec.func.attr not in HTTP_METHODS:
                continue
            if not dec.args or not isinstance(dec.args[0], ast.Constant):
                continue
            path = dec.args[0].value
            if not (isinstance(path, str) and path.startswith(AGENTS_PREFIX)):
                continue
            out.append({
                "name": node.name,
                "method": dec.func.attr.upper(),
                "path": path,
                "lineno": node.lineno,
                "params": sorted(set(_PATH_PARAM.findall(path))),
                "has_principal": _takes_principal(node),
                "binds": _binds_subject_to_param(node),
                "body": node,
            })
    return out


def _takes_principal(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """处理器是否把调用方身份递进来了（`Depends(authenticated())` 那一族）。"""
    defaults = [*fn.args.defaults, *(fn.args.kw_defaults or [])]
    for d in defaults:
        for call in _calls(d):
            func = call.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name in PRINCIPAL_ANCHORS:
                return True
            for arg in call.args:
                for inner in _calls(arg):
                    ifc = inner.func
                    nm = ifc.id if isinstance(ifc, ast.Name) else getattr(ifc, "attr", None)
                    if nm in PRINCIPAL_ANCHORS:
                        return True
    return False


def _calls(node: ast.AST | None):
    if node is None:
        return
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            yield sub


def _binds_subject_to_param(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """体内是否存在"调用方主体 ↔ 路径主体"的一次绑定。

    两种合法形状：① 直接比较（`agent_id != info.subject`／`agent_id in _MY_SUBJECTS`）；
    ② 走 helper，且 helper 名字里带 subject、参数里同时出现路径主体与主体引用
       （`assert_agent_subject(agent_id, info)`）。名字要求是为了本门能机械判定，
       不是为了让人迁就写法——一个不带 subject 字样的调用，本门无从知道它校验了什么。
    """
    names = {a.arg for a in [*fn.args.args, *fn.args.kwonlyargs]}
    for node in ast.walk(fn):
        if isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            if any(_refs_subject(o) for o in operands) and any(
                _names_subject_param(o, names) for o in operands
            ):
                return True
        if isinstance(node, ast.Call):
            fname = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            if not fname or not _SUBJECT_WORD.search(fname):
                continue
            args = [*(node.args), *(kw.value for kw in node.keywords if kw.value is not None)]
            if any(_names_subject_param(a, names) for a in args) and any(_refs_subject(a) for a in args):
                return True
    return False


def _names_subject_param(node: ast.AST, param_names: set[str]) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id in param_names:
            return True
    return False


def _exemptions(src: Path) -> dict[str, str]:
    hits: dict[str, str] = {}
    for i, line in enumerate(src.read_text(encoding="utf-8").splitlines(), 1):
        if EXEMPT_MARKER in line:
            reason = line.split(EXEMPT_MARKER, 1)[1].rstrip().rstrip(")").strip()
            hits[str(i)] = reason
    return hits


def check(repo: Path) -> tuple[list[str], list[str], dict[str, object]]:
    """返回 `(红, 射程塌, 读数)`。"""
    api = repo / API_REL
    if not api.is_file():
        return [], [f"{API_REL} 读不到：本门的射程前提已经不成立（文件挪走/改名），报『干净』没有依据"], {}
    try:
        tree = ast.parse(api.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        return [], [f"{API_REL} 解析不了（{exc.lineno}:{exc.msg}）⇒ 本门无从判定"], {}

    exemptions = _exemptions(api)
    red: list[str] = []
    errs: list[str] = []

    def exempted(lineno: int) -> bool:
        reason = exemptions.get(str(lineno))
        if reason is None:
            return False
        if not reason:
            red.append(f"{API_REL}:{lineno} 写了豁免标记却没写理由（理由为空＝没认领，与不豁免同判红）")
            return True
        return True

    def_line = _owner_symbol_line(tree)
    if def_line is None:
        errs.append(
            f"在 {API_REL} 里找不到 `{SUBJECT_SYMBOL} = …` 那一行：owner 面的定义被挪走或改名了，"
            "判据 A 就此失去对照物——此刻报『干净』是假绿"
        )

    compares = subject_literal_compares(tree)
    for site in compares:
        if exempted(int(site["lineno"])):
            continue
        red.append(
            f"A：{API_REL}:{site['lineno']} 用字面量名单 {site['values']} 判定主体"
            f"（`{site['src']}`）——owner 面只有 `{SUBJECT_SYMBOL}`（:{def_line}）一枚真源，"
            "再抄一份就是第二真源：签发侧改主体名时判定侧不会跟着红（F-02 撞的就是这一格）"
        )

    endpoints = agents_endpoints(tree)
    if not errs and not endpoints:
        errs.append(
            f"在 {API_REL} 里数不出任何 `{AGENTS_PREFIX}*` 端点：那面被删了或换了路径，"
            "本门已盯不住任何东西——报『干净』是假绿，要当场决定本门去留"
        )
    for ep in endpoints:
        params = ep["params"]
        if not params:
            continue
        if exempted(int(ep["lineno"])):
            continue
        missing = []
        if not ep["has_principal"]:
            missing.append("没把调用方身份递进来（缺 `Depends(authenticated()))` 那一类主参）")
        if not ep["binds"]:
            missing.append(f"体内没有『调用方主体 ↔ 路径主体（{', '.join(params)}）』的那一次绑定")
        if missing:
            red.append(
                f"B：{ep['method']} `{ep['path']}`（`{ep['name']}`，:{ep['lineno']}）"
                + "；".join(missing)
                + " ⇒ 一枚 write 令牌可撤销／改名任何主体的 agent（审计 §六 I）"
            )

    info = {
        "literal_sites": len(compares),
        "endpoints": len(endpoints),
        "with_path_param": sum(1 for e in endpoints if e["params"]),
        "symbol_line": def_line,
    }
    return red, errs, info


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("repo", nargs="?", default=str(REPO), help="仓根（默认脚本所在仓的上一级）")
    args = ap.parse_args(argv)
    red, errs, info = check(Path(args.repo))
    if errs:
        for e in errs:
            print(f"射程自证：{e}")
        return 2
    print(
        f"读数：主体字面量判定 {info['literal_sites']} 处／`{AGENTS_PREFIX}` 端点 "
        f"{info['endpoints']} 枚（带路径参数 {info['with_path_param']} 枚）／单一真源定义行 :{info['symbol_line']}"
    )
    if red:
        for r in red:
            print(f"红：{r}")
        return 1
    print("✓ 主体名单只有一枚真源，`/api/user/agents/*` 每枚带路径参数的端点都做了主体绑定")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
