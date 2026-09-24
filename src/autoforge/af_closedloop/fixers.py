# -*- coding: utf-8 -*-
"""fixers — 确定性 auto-fix：ErrorCode → fixer 列表，产出契约 FixOutcome。

铁律（I3）：设备类问题一律转人工，绝不猜 entity_id。
"""
from __future__ import annotations

from typing import Callable, Mapping, Optional

from .graphops import (add_edge, edges_signature, kind_of, node_by_id, pass_ids,
                       relink_chain, remove_edge)
from .runtime import load_module


def _outcome(applied, description, touched_ir=True, needs_user=False):
    A = load_module()
    return A.FixOutcome(applied=applied, description=description,
                        touched_ir=touched_ir, needs_user=needs_user)


def _alive(ctx) -> bool:
    """该 issue 描述的问题是否仍然存在（同轮其他修复可能已经顺手解决）。"""
    for i in load_module() and __import__("autoforge.af_closedloop.detectors", fromlist=["lint"]).lint(ctx.ir):
        if i.code == ctx.issue.code and i.node == ctx.issue.node:
            return True
    return False


# ---- 各类 fixer -------------------------------------------------------
def fix_missing_timeout(ctx):
    n = node_by_id(ctx.ir, ctx.issue.node)
    if not n or n.get("timeout"):
        return _outcome(False, "同轮其他修复已解决", touched_ir=False)
    A = load_module()
    n["timeout"] = A.DEFAULT_TIMEOUT
    return _outcome(True, f"ask 超时补默认 {A.DEFAULT_TIMEOUT}")


def fix_missing_on_error(ctx):
    n = node_by_id(ctx.ir, ctx.issue.node)
    if not n:
        return _outcome(False, "节点不存在", touched_ir=False)
    ps = pass_ids(ctx.ir) or [""]
    if add_edge(ctx.ir, n.get("id"), ps[0], "on_error", "失败兜底"):
        return _outcome(True, f"补失败兜底边 {n.get('id')}->{ps[0]} on_error")
    return _outcome(False, "兜底边已存在", touched_ir=False)


def fix_missing_canary(ctx):
    n = node_by_id(ctx.ir, ctx.issue.node)
    if not n:
        return _outcome(False, "节点不存在", touched_ir=False)
    n["requires_confirm"] = True           # 只允许调严（I4）
    n.setdefault("canary", {"percent": 10, "window": "24h"})
    meta = ctx.ir.setdefault("meta", {})
    meta.setdefault("canary", {"percent": 10, "window": "24h"})
    return _outcome(True, "高风险动作补 requires_confirm + canary(10%/24h)")


def fix_missing_numeric_type(ctx):
    n = node_by_id(ctx.ir, ctx.issue.node)
    root = (n or {}).get("condition")
    path = tuple((ctx.issue.raw or {}).get("path") or ())
    if not isinstance(root, dict):
        return _outcome(False, "条件节点不可用", touched_ir=False)
    try:
        load_module().set_path(root, path + ("left", "type"), "numeric")
    except Exception:
        return _outcome(False, "无法定位比较表达式", touched_ir=False)
    return _outcome(True, "数值比较补 type=numeric")


def fix_disconnected(ctx):
    before = edges_signature(ctx.ir)
    relink_chain(ctx.ir)
    if edges_signature(ctx.ir) != before:
        return _outcome(True, "重链接 ask/动作链路，补齐入边与出边")
    n = node_by_id(ctx.ir, ctx.issue.node)
    if not n:
        return _outcome(False, "节点不存在", touched_ir=False)
    nid = str(n.get("id"))
    edges = ctx.ir.get("edges") or []
    has_in = any(str(e.get("to")) == nid for e in edges)
    has_out = any(str(e.get("from")) == nid for e in edges)
    if (n.get("kind") == "on" or has_in) and (n.get("kind") == "pass" or has_out):
        return _outcome(False, "同轮其他修复已解决，无需重复处理", touched_ir=False)
    reason = str((ctx.issue.raw or {}).get("reason") or "")
    ps = pass_ids(ctx.ir)
    if reason == "dead_end" and ps and add_edge(ctx.ir, nid, ps[0], "then"):
        return _outcome(True, f"补出边 {nid}->{ps[0]}")
    return _outcome(False, "拓扑关系无法自动判定，需人工确认连接", touched_ir=False, needs_user=True)


def fix_cycle(ctx):
    edge = (ctx.issue.raw or {}).get("edge") or {}
    if edge and remove_edge(ctx.ir, edge.get("from"), edge.get("to"), edge.get("when")):
        relink_chain(ctx.ir)
        return _outcome(True, f"断环：删除回边 {edge.get('from')}->{edge.get('to')}")
    return _outcome(False, "环无法自动定位，需人工确认", touched_ir=False, needs_user=True)


def fix_wait_edge_misuse(ctx):
    edge = dict((ctx.issue.raw or {}).get("edge") or {})
    if not edge:
        return _outcome(False, "缺少边信息", touched_ir=False)
    frm = edge.get("from")
    want = "yes" if kind_of(ctx.ir, frm) == "ask" else "then"
    for e in ctx.ir.get("edges") or []:
        if e.get("from") == edge.get("from") and e.get("to") == edge.get("to") \
                and e.get("when") == edge.get("when"):
            e["when"] = want
            return _outcome(True, f"边条件纠正为 {want}")
    return _outcome(False, "边已不存在", touched_ir=False)


def fix_sim_branch(ctx):
    raw = ctx.issue.raw or {}
    frm, to, when = raw.get("from") or ctx.issue.node, raw.get("to"), raw.get("when")
    case = f"（用例 {ctx.issue.case}）" if ctx.issue.case else ""
    if to and when and add_edge(ctx.ir, frm, to, when):
        return _outcome(True, f"补齐仿真缺失分支 {frm}->{to} {when}{case}")
    ps = pass_ids(ctx.ir)
    if when and ps and add_edge(ctx.ir, frm, ps[-1], when):
        return _outcome(True, f"补齐仿真缺失分支 {frm}->{ps[-1]} {when}{case}")
    return _outcome(False, f"分支走向需人工确认{case}", touched_ir=False, needs_user=True)


def fix_never_guess_entity(ctx):
    refs = (ctx.issue.raw or {}).get("refs") or [ctx.issue.node]
    return _outcome(False, f"设备引用必须由人确认，禁止猜测（{', '.join(map(str, refs))}）",
                    touched_ir=False, needs_user=True)


def fix_expect_failed(ctx):
    case = f"用例 {ctx.issue.case}" if ctx.issue.case else "验收断言"
    return _outcome(False, f"{case} 未通过，需人工确认（或调整用例/期望）",
                    touched_ir=False, needs_user=True)


def fix_escalate(ctx):
    return _outcome(False, "交给 LLM 深度修复或人工处理", touched_ir=False)


REGISTRY: dict[str, list[Callable]] = {
    "MISSING_TIMEOUT_OR_DEFAULT": [fix_missing_timeout],
    "MISSING_ON_ERROR": [fix_missing_on_error],
    "MISSING_CANARY": [fix_missing_canary],
    "MISSING_NUMERIC_TYPE": [fix_missing_numeric_type],
    "DISCONNECTED_NODE": [fix_disconnected],
    "CYCLE": [fix_cycle],
    "WAIT_EDGE_MISUSE": [fix_wait_edge_misuse],
    "SIM_BRANCH_MISMATCH": [fix_sim_branch],
    "ENTITY_NOT_FOUND": [fix_never_guess_entity],
    "STALE_ENTITY": [fix_never_guess_entity],
    "EXPECT_FAILED": [fix_expect_failed],
    "SPEC_PARSE_ERROR": [fix_escalate],
    "UNKNOWN": [fix_escalate],
}


def code_of(issue) -> str:
    return str(getattr(issue.code, "value", issue.code))


def apply_fix(ctx, registry=None) -> tuple:
    """返回 (FixOutcome, action)；action = 命中 fixer 的函数名，用于历史聚合。"""
    reg = REGISTRY if registry is None else registry
    for fn in reg.get(code_of(ctx.issue), ()):
        out = fn(ctx)
        if out is not None:
            return out, fn.__name__
    return _outcome(False, "无可用自动修复", touched_ir=False, needs_user=True), "escalate"
