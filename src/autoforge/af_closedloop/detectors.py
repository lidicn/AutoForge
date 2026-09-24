# -*- coding: utf-8 -*-
"""detectors — IR 静态体检，产出编排器契约里的 BuildIssue（ErrorCode 复用，不新增枚举）。"""
from __future__ import annotations

from collections import Counter
from typing import Mapping

from .graphops import (ASK_WHENS, DO_WHENS, EDGE_WHENS, IF_WHENS, WAIT_WHENS,
                       edges_of, nodes_of)
from .runtime import load_module


def expr_nodes(expr, path=()):
    """与原 _iter_expr_nodes 同一 path 约定：args 走 ("args", i)，left/right/value 走 (k,)。"""
    if isinstance(expr, Mapping):
        yield path, expr
        for i, a in enumerate(expr.get("args") or []):
            yield from expr_nodes(a, path + ("args", i))
        for k in ("left", "right", "value"):
            if isinstance(expr.get(k), Mapping):
                yield from expr_nodes(expr[k], path + (k,))


def _issue(code, node, message, **kw):
    A = load_module()
    return A.BuildIssue(code=code, node=node, message=message, **kw)


def lint(ir) -> list:
    A = load_module()
    E = A.ErrorCode
    if not isinstance(ir, Mapping):
        return [_issue(E.SPEC_PARSE_ERROR, None, "IR 不是对象")]
    nodes, edges = nodes_of(ir), edges_of(ir)
    if not nodes:
        return [_issue(E.SPEC_PARSE_ERROR, None, "IR 缺少 nodes")]
    out = []
    ids = [str(n.get("id")) for n in nodes]
    if any(not i for i in ids) or len(set(ids)) != len(ids):
        out.append(_issue(E.SPEC_PARSE_ERROR, None, "节点 id 缺失或重复"))
    known = set(ids)

    for e in edges:
        frm, to, when = e.get("from"), e.get("to"), e.get("when")
        if frm not in known or to not in known:
            out.append(_issue(E.DISCONNECTED_NODE, frm if frm not in known else to,
                              f"边 {frm}->{to} 指向不存在的节点", raw=dict(e)))
            continue
        kind = str((next(n for n in nodes if str(n.get("id")) == frm)).get("kind") or "")
        allowed = {"ask": ASK_WHENS, "do": DO_WHENS, "if": IF_WHENS, "wait": WAIT_WHENS}.get(kind, EDGE_WHENS)
        if when not in EDGE_WHENS or when not in allowed:
            out.append(_issue(E.WAIT_EDGE_MISUSE, frm,
                              f"{kind or '?'} 节点不允许 {when} 分支", raw=dict(e)))

    indeg = Counter(str(e.get("to")) for e in edges if e.get("to") in known)
    outdeg = Counter(str(e.get("from")) for e in edges if e.get("from") in known)
    for n in nodes:
        nid, kind = str(n.get("id")), str(n.get("kind"))
        if kind != "on" and indeg[nid] == 0:
            out.append(_issue(E.DISCONNECTED_NODE, nid, "节点不可达（无入边）",
                              raw={"reason": "unreachable"}))
        if kind != "pass" and outdeg[nid] == 0:
            out.append(_issue(E.DISCONNECTED_NODE, nid, "节点是死胡同（无出边）",
                              raw={"reason": "dead_end"}))

    for back in _back_edges(nodes, edges):
        out.append(_issue(E.CYCLE, back.get("from"),
                          f"存在环: {back.get('from')}->{back.get('to')} ({back.get('when')})",
                          raw={"edge": dict(back)}))

    for n in nodes:
        nid, kind = str(n.get("id")), str(n.get("kind"))
        if kind == "ask" and not n.get("timeout"):
            out.append(_issue(E.MISSING_TIMEOUT_OR_DEFAULT, nid, "ask 缺 timeout"))
        if kind == "do":
            risky = A.risk_of(str(n.get("action") or "")) != A.Risk.L1
            if risky and not (n.get("requires_confirm") and n.get("canary")):
                out.append(_issue(E.MISSING_CANARY, nid,
                                  "高风险动作缺少 requires_confirm / canary"))
        if kind == "if":
            for path, node in expr_nodes(n.get("condition")):
                if node.get("op") in ("gt", "lt", "ge", "le"):
                    left = node.get("left") or {}
                    if left.get("var") and left.get("type") != "numeric":
                        out.append(_issue(E.MISSING_NUMERIC_TYPE, nid,
                                          f"「{left.get('var')}」数值比较缺少 type=numeric",
                                          raw={"path": path}))

    left = A.find_refs(ir)
    if left:
        out.append(_issue(E.ENTITY_NOT_FOUND, left[0],
                          f"未解析实体引用: {', '.join(left)}", raw={"refs": left}))
    return out


def _back_edges(nodes, edges) -> list:
    adj = {}
    for e in edges:
        adj.setdefault(str(e.get("from")), []).append(e)
    color, found = {}, []

    def dfs(nid):
        color[nid] = 1
        for e in adj.get(nid, ()):
            to = str(e.get("to"))
            if color.get(to) == 1:
                found.append(e)
            elif color.get(to, 0) == 0 and to in {str(n.get("id")) for n in nodes}:
                dfs(to)
        color[nid] = 2

    for n in nodes:
        nid = str(n.get("id"))
        if color.get(nid, 0) == 0:
            dfs(nid)
    return found
