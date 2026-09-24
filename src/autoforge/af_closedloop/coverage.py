# -*- coding: utf-8 -*-
"""coverage — 覆盖率报告。

trace 模式：仿真引擎回传 reached / trace 时按实测统计；
spec 模式：引擎不回传 trace 时，按 SimCase.expect_reach 的声明估算（明示为估算）。
修复命中率（各 ErrorCode 的自动修复成功率）由 history.stats() 提供。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from .graphops import edges_of, nodes_of


@dataclass
class CoverageReport:
    mode: str = "spec"
    nodes_total: int = 0
    nodes_hit: int = 0
    edges_total: int = 0
    edges_hit: int = 0
    branches_total: int = 0
    branches_hit: int = 0
    cases_total: int = 0
    cases_ok: int = 0
    missing_nodes: list = field(default_factory=list)
    missing_edges: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    def node_ratio(self) -> float:
        return _ratio(self.nodes_hit, self.nodes_total)

    def edge_ratio(self) -> float:
        return _ratio(self.edges_hit, self.edges_total)

    def branch_ratio(self) -> float:
        return _ratio(self.branches_hit, self.branches_total)

    def to_message(self) -> dict:
        return {
            "mode": self.mode,
            "nodes": {"hit": self.nodes_hit, "total": self.nodes_total,
                      "ratio": round(self.node_ratio(), 3)},
            "edges": {"hit": self.edges_hit, "total": self.edges_total,
                      "ratio": round(self.edge_ratio(), 3)},
            "branches": {"hit": self.branches_hit, "total": self.branches_total,
                         "ratio": round(self.branch_ratio(), 3)},
            "cases": {"ok": self.cases_ok, "total": self.cases_total},
            "missing_nodes": list(self.missing_nodes),
            "missing_edges": list(self.missing_edges),
            "notes": list(self.notes),
        }


def _ratio(hit, total) -> float:
    return round(hit / total, 4) if total else 1.0


def _match_node(entry, nodes):
    if isinstance(entry, Mapping):
        entry = entry.get("node") or entry.get("id") or entry.get("name") or ""
    s = str(entry or "").strip()
    if not s:
        return None
    for n in nodes:
        if str(n.get("id")) == s:
            return str(n.get("id"))
    for n in nodes:
        name = str(n.get("name") or "")
        if name and (name == s or s in name or name in s):
            return str(n.get("id"))
    return None


def _case_ok(c) -> bool:
    if "ok" in c:
        return bool(c.get("ok"))
    if "passed" in c:
        return bool(c.get("passed"))
    if "failed" in c:
        return not bool(c.get("failed"))
    return True


def _cases_of(sim_out) -> list:
    if not isinstance(sim_out, Mapping):
        return []
    for key in ("cases", "results", "runs", "simulations"):
        v = sim_out.get(key)
        if isinstance(v, list):
            return [x for x in v if isinstance(x, Mapping)]
    out = []
    for k, v in sim_out.items():
        if isinstance(v, Mapping) and ("reached" in v or "trace" in v or "visited" in v):
            d = dict(v)
            d.setdefault("name", k)
            out.append(d)
    return out


def _entries(c, *keys) -> list:
    out = []
    for k in keys:
        v = c.get(k)
        if isinstance(v, list):
            out.extend(v)
        elif isinstance(v, Mapping):
            for kk in ("nodes", "edges", "visited", "path"):
                vv = v.get(kk)
                if isinstance(vv, list):
                    out.extend(vv)
    return out


def _parse_edge_entry(entry, triples):
    if isinstance(entry, Mapping):
        frm, to, when = entry.get("from"), entry.get("to"), entry.get("when")
    elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
        frm, to, when = entry[0], entry[1], (entry[2] if len(entry) > 2 else None)
    else:
        s = str(entry or "")
        if "->" not in s:
            return set()
        left, _, right = s.partition("->")
        frm, _, when = right.partition(":")
        to = frm if when else right
        frm, to, when = left.strip(), (frm if when else right).strip(), (when or "").strip() or None
    hit = set()
    for (f, t, w) in triples:
        if str(frm) == f and str(to) == t and (when is None or str(when) == w):
            hit.add((f, t, w))
    return hit


def build_report(ir, sim_out=None, cases: Sequence = ()) -> CoverageReport:
    nodes, edges = nodes_of(ir), edges_of(ir)
    node_ids = [str(n.get("id")) for n in nodes]
    triples = [(str(e.get("from")), str(e.get("to")), str(e.get("when"))) for e in edges]
    branches = {(f, w) for (f, _t, w) in triples}

    sim_cases = _cases_of(sim_out)
    if sim_cases:
        mode = "trace"
        hit_nodes, hit_triples, total, ok = set(), set(), 0, 0
        for c in sim_cases:
            total += 1
            ok += 1 if _case_ok(c) else 0
            for entry in _entries(c, "reached", "visited", "trace"):
                nid = _match_node(entry, nodes)
                if nid:
                    hit_nodes.add(nid)
            for entry in _entries(c, "edges", "trace"):
                if isinstance(entry, str) and "->" not in entry:
                    continue
                hit_triples |= _parse_edge_entry(entry, triples)
        notes = []
    else:
        mode = "spec"
        hit_nodes, hit_triples, total, ok = set(), set(), 0, 0
        for case in cases or ():
            total += 1
            reach = list(getattr(case, "expect_reach", None)
                         or (case.get("expect_reach") if isinstance(case, Mapping) else [])
                         or [])
            matched = {m for m in (_match_node(x, nodes) for x in reach) if m}
            hit_nodes |= matched
            for (f, t, w) in triples:
                if f in matched and t in matched:
                    hit_triples.add((f, t, w))
            ok += 1 if matched else 0
        notes = ["spec 模式：仿真引擎未回传 trace，覆盖率按用例 expect_reach 声明估算"]

    missing_nodes = [i for i in node_ids if i not in hit_nodes]
    missing_edges = [f"{f}->{t}:{w}" for (f, t, w) in triples if (f, t, w) not in hit_triples]
    hit_branches = {(f, w) for (f, _t, w) in triples if (f, _t, w) in hit_triples}

    return CoverageReport(
        mode=mode,
        nodes_total=len(node_ids), nodes_hit=len([i for i in node_ids if i in hit_nodes]),
        edges_total=len(triples), edges_hit=len([t for t in triples if t in hit_triples]),
        branches_total=len(branches), branches_hit=len(hit_branches & branches),
        cases_total=total, cases_ok=ok,
        missing_nodes=missing_nodes, missing_edges=missing_edges, notes=notes,
    )
