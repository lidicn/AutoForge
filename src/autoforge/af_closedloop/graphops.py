# -*- coding: utf-8 -*-
"""graphops — IR 拓扑工具：稳定排序、去重加边、ask 链路确定性重链接。"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Mapping

EDGE_WHENS = {"then", "no", "yes", "default", "on_timeout", "on_cancel", "on_error"}
ASK_WHENS = {"yes", "no", "default", "on_timeout", "on_cancel"}
DO_WHENS = {"then", "on_error"}
IF_WHENS = {"then", "no"}
WAIT_WHENS = {"then", "on_timeout"}
CHAIN_KINDS = ("if", "wait", "ask", "set", "do")
_KIND_ORDER = {"on": 0, "if": 1, "wait": 2, "ask": 3, "set": 4, "do": 5, "pass": 6}
_ID_RE = re.compile(r"^([a-z]+)(\d+)$")


def nodes_of(graph) -> list:
    return [n for n in (graph.get("nodes") or []) if isinstance(n, Mapping)]


def edges_of(graph) -> list:
    return [e for e in (graph.get("edges") or []) if isinstance(e, Mapping)]


def node_by_id(graph, nid):
    for n in nodes_of(graph):
        if n.get("id") == nid:
            return n
    return None


def kind_of(graph, nid) -> str:
    return str((node_by_id(graph, nid) or {}).get("kind") or "")


def pass_ids(graph) -> list:
    ps = [str(n.get("id")) for n in nodes_of(graph) if n.get("kind") == "pass"]
    return sorted(ps, key=lambda s: int(_ID_RE.match(s).group(2)) if _ID_RE.match(s) else 0)


def _sort_key(n):
    m = _ID_RE.match(str(n.get("id") or ""))
    return (_KIND_ORDER.get(str(n.get("kind")), 9), int(m.group(2)) if m else 0, str(n.get("id")))


def _used_ids(graph) -> set:
    return {str(e.get("id")) for e in edges_of(graph) if e.get("id")}


def edges_signature(graph) -> tuple:
    return tuple(sorted((str(e.get("from")), str(e.get("to")), str(e.get("when")))
                        for e in edges_of(graph)))


def add_edge(graph, frm, to, when, label=None) -> bool:
    if not frm or not to or frm == to:
        return False
    for e in edges_of(graph):
        if e.get("from") == frm and e.get("to") == to and e.get("when") == when:
            return False
    used = _used_ids(graph)
    i = 1
    while f"e{i}" in used:
        i += 1
    edge = {"from": frm, "to": to, "when": when, "id": f"e{i}"}
    if label:
        edge["label"] = label
    graph.setdefault("edges", []).append(edge)
    return True


def remove_edge(graph, frm, to, when=None) -> bool:
    keep, removed = [], False
    for e in edges_of(graph):
        if e.get("from") == frm and e.get("to") == to and (when is None or e.get("when") == when):
            removed = True
            continue
        keep.append(e)
    graph["edges"] = keep
    return removed


def relink_chain(graph) -> dict:
    """B2: 多 ask 拓扑重链接。

    原实现 ask 的 yes 固定跳第一个 do，链上后续 ask 永远没有入边（build 报
    DISCONNECTED_NODE）。这里按节点 id 重建 ask 相关的全部边，并补齐链上缺失的
    入/出边；非 ask 相关的边原样保留。
    链序: cond → ask → do；ask 的 yes 进入链上下一个节点，no / on_cancel 进「未执行」，
    on_timeout / default 进「结束」。
    """
    nodes = nodes_of(graph)
    if not nodes or not any(n.get("kind") == "ask" for n in nodes):
        return graph
    by_kind = defaultdict(list)
    for n in sorted(nodes, key=_sort_key):
        by_kind[str(n.get("kind"))].append(n)
    ps = pass_ids(graph)
    p1 = ps[0] if ps else ""
    p2 = ps[1] if len(ps) > 1 else p1
    seq = [str(n.get("id")) for k in CHAIN_KINDS for n in by_kind.get(k, [])]
    ask_ids = {str(n.get("id")) for n in by_kind.get("ask")}

    keep = [dict(e) for e in edges_of(graph)
            if str(e.get("from")) not in ask_ids and str(e.get("to")) not in ask_ids]
    graph["edges"] = keep
    seen = {(str(e.get("from")), str(e.get("to")), str(e.get("when"))) for e in keep}

    def add(frm, to, when, label=None):
        if (frm, to, when) in seen:
            return
        seen.add((frm, to, when))
        add_edge(graph, frm, to, when, label)

    head = seq[0] if seq else p1
    for n in by_kind.get("on") or []:
        nid = str(n.get("id"))
        if not any(str(e.get("from")) == nid for e in graph["edges"]):
            add(nid, head, "then")

    for i, nid in enumerate(seq):
        kind = kind_of(graph, nid)
        nxt = seq[i + 1] if i + 1 < len(seq) else p1
        if kind == "if":
            add(nid, nxt, "then")
            add(nid, p2, "no")
        elif kind == "ask":
            add(nid, nxt, "yes")
            add(nid, p2, "no")
            add(nid, p2, "on_cancel", "用户拒绝")
            add(nid, p1, "on_timeout")
            add(nid, p1, "default")
        elif kind == "do":
            add(nid, nxt, "then")
            add(nid, p1, "on_error", "失败兜底")
        else:
            add(nid, nxt, "then")
    return graph
