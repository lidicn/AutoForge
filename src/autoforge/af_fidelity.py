"""F14 P1 — Fidelity Verifier（往返保真校验器）。

判据（DCD 20261001《AF 三题》·F / 方案 C）：
- **核心字段 L0 完全相等**：automation id、node id 集合、node kind、trigger、
  action(domain.service)、edges、schema/ir_version、mode；group 容器还递归比节点级
  `mode`（sequence|parallel）与整棵 `children` 子树（顺序即编排语义）。
- **params 语义相等**：键序无关、值必等。
- **condition（`expr`）L1 结构等价**：经 `condition_norm.normalize_condition` 归一化（CNF）后相等
  ——允许布尔等价变形，如 `(A and B) or C ≡ (A or C) and (B or C)`。

P1 范围约束（见裁定 §三.3）：NL→IR 自由文本解析属 **P2**（`build_ir_from_nl` 未实现）。
P1 的 `IR→NL→IR` 由**确定性 NL 渲染**（`af_nl.render_automation`：同图必得同文 + 全节点覆盖）
承担 IR→NL 半程，用**结构化投影回写**（本模块 `project_automation`）承担 NL→IR 半程，
再按上述分层规则比较。交付物 = 校验器 + 归一化 + 30 样本全绿，不含自由文本解析器。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping

from .af_ir import Automation, Trigger
from .af_ir.condition_norm import normalize_condition
from .af_nl import render_automation

__all__ = ["FidelityReport", "verify_roundtrip", "fidelity_equal", "project_automation"]


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _trigger_to_dict(t: Trigger) -> dict[str, Any]:
    """从结构化 Trigger 回写 IR dict（不读 raw）——投影即真值检验。"""
    out: dict[str, Any] = {"type": t.type}
    if t.entity_id is not None:
        out["entity_id"] = t.entity_id
    if t.from_ is not None:
        out["from"] = t.from_
    if t.to is not None:
        out["to"] = t.to
    if t.event is not None:
        out["event"] = t.event
    if t.offset is not None:
        out["offset"] = t.offset
    if t.at is not None:
        out["at"] = t.at
    if t.op is not None:
        out["op"] = t.op
    if t.sources:
        out["sources"] = [_trigger_to_dict(s) for s in t.sources]
    return out


def _node_projection(node) -> dict[str, Any]:
    d: dict[str, Any] = {"id": node.id, "kind": node.kind}
    if node.name:
        d["name"] = node.name
    if node.trigger is not None:
        d["trigger"] = _trigger_to_dict(node.trigger)
    if node.for_ is not None:
        d["for"] = node.for_
    if node.expr is not None:
        d["expr"] = node.expr
    if node.adapter is not None:
        d["adapter"] = node.adapter
    if node.action is not None:
        d["action"] = node.action
    if node.params:
        d["params"] = node.params
    if node.prompt:
        d["prompt"] = node.prompt
    if node.room is not None:
        d["room"] = node.room
    if node.session and node.session != "room":
        d["session"] = node.session
    if node.timeout is not None:
        d["timeout"] = node.timeout
    if node.duration is not None:
        d["duration"] = node.duration
    if node.var is not None:
        d["var"] = node.var
    if node.ask is not None:
        d["ask"] = node.ask.to_dict()
    # group 容器的两项必须进投影：`mode` 是编排语义，`children` 是整棵子树。
    # 过去投影把 children 丢了也照样"保真通过"——那是假绿：schema 现在会直接拒收
    # （kind=group 而 children 缺失），但判据不该靠 schema 兜出来才发现。
    if node.mode is not None:
        d["mode"] = node.mode
    if node.children:
        d["children"] = [project_automation(child) for child in node.children]
    return d


def project_automation(auto: Automation) -> dict[str, Any]:
    """IR 的结构化投影：从 Automation 的**结构化字段**重建 IR dict（不碰 raw）。

    若某核心字段只存在于 raw 而未进入 Python 投影，重建结果会与原文不同——校验器据此报警。
    """
    return {
        "ir_version": auto.raw.get("ir_version"),
        "id": auto.id,
        "name": auto.name,
        "version": auto.version,
        "mode": auto.mode,
        "nodes": [_node_projection(auto.nodes[nid]) for nid in _ordered_node_ids(auto)],
        "edges": [{"from": e.from_, "to": e.to, "kind": e.kind} for e in auto.edges],
    }


def _ordered_node_ids(auto: Automation) -> list[str]:
    return sorted(auto.nodes)


def _node_core(node) -> dict[str, Any]:
    return {
        "kind": node.kind,
        "trigger": None if node.trigger is None else _canon(_trigger_to_dict(node.trigger)),
        "action": node.action,
        "params": _canon(node.params or {}),
        "condition": normalize_condition(node.expr),
        "targets": tuple(sorted(node.target_entities())),
        # P3：ask 结构元数据跨层一致（§2.1 nodes[].ask 结构相等）——含控件规格与挂起字段
        "ask": _canon({
            "prompt": node.prompt,
            "room": node.room,
            "session": node.session,
            "timeout": node.timeout,
            "spec": None if node.ask is None else node.ask.to_dict(),
        }),
        # group 容器：编排语义与整棵子树都是核心字段。只比 kind 的话，投影丢掉一个
        # child 也算"保真通过"——那是假绿，且比 children 缺失更危险（schema 拦不住）。
        "mode": node.mode,
        "children": node.children,
    }


def _automation_core(auto: Automation) -> dict[str, Any]:
    return {
        "id": auto.id,
        "mode": auto.mode,
        "ir_version": auto.raw.get("ir_version"),
        "nodes": {nid: _node_core(auto.nodes[nid]) for nid in auto.nodes},
        "edges": {_canon((e.from_, e.to, e.kind)) for e in auto.edges},
    }


def fidelity_equal(a: Automation, b: Automation) -> bool:
    """分层等价：核心 L0 完全相等、params 键序无关、condition 结构等价。"""
    ca, cb = _automation_core(a), _automation_core(b)
    if ca["id"] != cb["id"] or ca["mode"] != cb["mode"]:
        return False
    if ca["ir_version"] != cb["ir_version"]:
        return False
    if set(ca["nodes"]) != set(cb["nodes"]):  # node id 集合完全相等
        return False
    for nid in ca["nodes"]:
        na, nb = ca["nodes"][nid], cb["nodes"][nid]
        if na["kind"] != nb["kind"]:
            return False
        if na["action"] != nb["action"]:  # domain.service 不可变（L0）
            return False
        if na["trigger"] != nb["trigger"]:
            return False
        if na["params"] != nb["params"]:  # 语义相等（键序无关已 sort）
            return False
        if na["targets"] != nb["targets"]:
            return False
        if na["ask"] != nb["ask"]:  # P3：ask 控件元数据结构相等
            return False
        if na["mode"] != nb["mode"]:  # group 编排语义（sequence|parallel）
            return False
        if not _children_equal(na["children"], nb["children"]):
            return False
        if na["condition"] != nb["condition"]:  # L1 结构等价
            return False
    return ca["edges"] == cb["edges"]  # 拓扑完全相等（L0）


def _children_equal(a: tuple[Automation, ...], b: tuple[Automation, ...]) -> bool:
    """group 子树递归保真：顺序即编排语义（sequence 下换序 = 换了执行次序），故按位比较。"""
    if [c.id for c in a] != [c.id for c in b]:
        return False
    return all(fidelity_equal(x, y) for x, y in zip(a, b))


@dataclass
class FidelityReport:
    ok: bool
    nl_deterministic: bool
    nl_full_coverage: bool
    projection_fidelity: bool
    detail: list[str] = field(default_factory=list)


def verify_roundtrip(ir: Mapping[str, Any]) -> FidelityReport:
    """对一条 IR 执行 IR→NL→IR 往返保真校验，返回分项报告。"""
    detail: list[str] = []
    auto = Automation.from_dict(dict(ir))

    # IR→NL：确定性（同图必得同文）+ 全节点覆盖
    nl1 = render_automation(auto)
    nl2 = render_automation(auto)
    nl_deterministic = nl1.text == nl2.text
    nl_full_coverage = not nl1.missing
    if not nl_deterministic:
        detail.append("NL 渲染非确定性")
    if not nl_full_coverage:
        detail.append(f"NL 覆盖率缺失节点：{sorted(nl1.missing)}")

    # NL→IR（结构化投影）保真：投影重建的 Automation 与原 Automation 分层等价
    rebuilt = Automation.from_dict(project_automation(auto))
    projection_fidelity = fidelity_equal(auto, rebuilt)
    if not projection_fidelity:
        detail.append("投影回写与原文不满足分层保真判据")
        # 定位差异供调试
        ca, cb = _automation_core(auto), _automation_core(rebuilt)
        if set(ca["nodes"]) != set(cb["nodes"]):
            detail.append(f"节点集差异：{set(ca['nodes']) ^ set(cb['nodes'])}")

    ok = nl_deterministic and nl_full_coverage and projection_fidelity
    return FidelityReport(
        ok=ok,
        nl_deterministic=nl_deterministic,
        nl_full_coverage=nl_full_coverage,
        projection_fidelity=projection_fidelity,
        detail=detail,
    )
