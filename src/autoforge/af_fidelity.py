"""F14 P1 — Fidelity Verifier（往返保真校验器）。

判据（DCD 20261001《AF 三题》·F / 方案 C）：
- **核心字段 L0 完全相等**：automation id、node id 集合、node kind、trigger、
  action(domain.service)、edges、schema/ir_version、mode；group 容器还递归比节点级
  `mode`（sequence|parallel）与整棵 `children` 子树（顺序即编排语义）。
- **params 语义相等**：键序无关、值必等。
- **condition（`expr`）L1 结构等价**：经 `condition_norm.normalize_condition` 归一化（CNF）后相等
  ——允许布尔等价变形，如 `(A and B) or C ≡ (A or C) and (B or C)`。

P1 范围约束（见裁定 §三.3）：P1 的比较面**不含自由文本解析器**——`IR→NL→IR` 由**确定性 NL 渲染**
（`af_nl.render_automation`：同图必得同文 + 全节点覆盖）承担 IR→NL 半程，用**结构化投影回写**
（本模块 `project_automation`）承担 NL→IR 半程，再按上述分层规则比较。交付物 = 校验器 + 归一化 +
30 样本全绿。自由文本解析属 **P2**，已在 `af_nl_build.build_ir_from_nl` 落地（受限文法、零 LLM，
超文法即 `ValueError`；判据 `tests/f14/test_nl_build.py`）——P1 用投影、P2 用解析器，两条半程**互不替换**。
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Mapping

from .af_ir import Automation, ParamDepthError, Trigger, check_param_depth, check_trigger_depth
from .af_ir.condition_norm import LeafUnserializable, normalize_condition
from .af_ir.models import IRDepthError
from .af_nl import render_automation

__all__ = [
    "FidelityReport",
    "FidelityNotComparable",
    "verify_roundtrip",
    "fidelity_equal",
    "project_automation",
]

CANON_ERR_UNSUPPORTED = "FIDELITY_CANON_UNSUPPORTED"


class FidelityNotComparable(ValueError):
    """某一格的值/键型规范化不了——**这不是「相等」，也不是「不等」，是没比成**。

    第十三轮 `_leaf_key` 那一族的另一半：`json.dumps` 遇到 date/set 抛 `TypeError`、遇到自引用抛
    `ValueError`、遇到混合键型抛 `TypeError`，三种都绕过全部遍历闸门直接抛穿 `verify_roundtrip`；
    而更糟的是它把 `("x","y")` 与 `["x","y"]`、`{1: "a"}` 与 `{"1": "a"}` canon 成**同一个串**，
    于是「类型不同/键型不同」在保真校验器里被判成保真——校验器的本职恰恰是拒绝这一格。
    """

    def __init__(self, where: str, detail: str) -> None:
        self.code = CANON_ERR_UNSUPPORTED
        self.where = where
        self.detail = detail
        super().__init__(f"{CANON_ERR_UNSUPPORTED}: 位置 {where} 无法规范化（{detail}）")


def _canon(value: Any, *, where: str = "params", _depth: int = 0) -> str:
    """把值编码成**带类型标记**的规范串：键序无关，但 tuple≠list、int 键≠str 键。

    词汇表就是 JSON 那一套（null/bool/int/有限 float/str/list/dict）；表外的形状一律
    `FidelityNotComparable` 带上位置与原因，绝不静默强转。深度预算取 `MAX_PARAM_DEPTH`
    那份单一真值源（`check_param_depth`），环与超深都抛具名错而不是 RecursionError。
    """
    check_param_depth(_depth, f"保真规范化 {where}")
    if value is None:
        return "null"
    if isinstance(value, bool):  # bool 是 int 的子类，必须排在 int 之前判
        return "bool:true" if value else "bool:false"
    if isinstance(value, int):
        return f"int:{value}"
    if isinstance(value, float):
        if not math.isfinite(value):
            raise FidelityNotComparable(where, f"非有限 float {value!r}（JSON 里没有这一档，两侧无从对齐）")
        return f"float:{value!r}"
    if isinstance(value, str):
        return "str:" + json.dumps(value, ensure_ascii=False)
    if isinstance(value, Mapping):
        for key in value:
            if not isinstance(key, str):
                raise FidelityNotComparable(
                    where, f"键 {key!r} 是 {type(key).__name__} 而非 str（json 会把键强转成字符串，"
                    f"`{key!r}` 与 `{str(key)!r}` 就塌成一格）"
                )
        items = ",".join(
            f"{json.dumps(key, ensure_ascii=False)}:"
            f"{_canon(value[key], where=f'{where}[{key}]', _depth=_depth + 1)}"
            for key in sorted(value)
        )
        return "{" + items + "}"
    if isinstance(value, (list, tuple)):
        tag = "l" if isinstance(value, list) else "t"
        inner = ",".join(
            _canon(item, where=f"{where}[{i}]", _depth=_depth + 1) for i, item in enumerate(value)
        )
        return f"[{tag}:{inner}]"
    raise FidelityNotComparable(where, f"值型 {type(value).__name__} 不在 JSON 词汇表内")


def _trigger_to_dict(t: Trigger, _depth: int = 0) -> dict[str, Any]:
    """从结构化 Trigger 回写 IR dict（不读 raw）——投影即真值检验。

    F6：group 递归走者之一（第十七轮实测的 cyclic_crash 站点）。预算取 `MAX_TRIGGER_DEPTH`
    那份单一真值源，超限抛 `TriggerDepthError` 而不是把 RecursionError 漏给调用方。
    """
    check_trigger_depth(_depth, "fidelity._trigger_to_dict")
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
        out["sources"] = [_trigger_to_dict(s, _depth + 1) for s in t.sources]
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
    nid = node.id
    return {
        "kind": node.kind,
        "trigger": None if node.trigger is None else _canon(_trigger_to_dict(node.trigger), where=f"nodes[{nid}].trigger"),
        "action": node.action,
        "params": _canon(node.params or {}, where=f"nodes[{nid}].params"),
        "condition": normalize_condition(node.expr),
        "targets": tuple(sorted(node.target_entities())),
        # P3：ask 结构元数据跨层一致（§2.1 nodes[].ask 结构相等）——含控件规格与挂起字段
        "ask": _canon({
            "prompt": node.prompt,
            "room": node.room,
            "session": node.session,
            "timeout": node.timeout,
            "spec": None if node.ask is None else node.ask.to_dict(),
        }, where=f"nodes[{nid}].ask"),
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
        "edges": {_canon((e.from_, e.to, e.kind), where="edges") for e in auto.edges},
    }


def fidelity_equal(a: Automation, b: Automation) -> bool:
    """分层等价：核心 L0 完全相等、params 键序无关、condition 结构等价。

    某一格落在 JSON 词汇表外（date/set/自引用/非 str 键/非有限 float）时抛
    `FidelityNotComparable` —— 返回 `False` 会被读成"两条不等"，而实情是"这条没比成"。
    `verify_roundtrip` 捕这一枚并如实落成 `not_comparable` 档。
    """
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
    #: 某一格规范化不了（词汇表外/超预算/归一化不了）——**这不是不等，是没比成**，
    #: 所以 `ok` 与 `projection_fidelity` 都必须是 False，绝不报成绿。
    not_comparable: bool = False


def verify_roundtrip(ir: Mapping[str, Any]) -> FidelityReport:
    """对一条 IR 执行 IR→NL→IR 往返保真校验，返回分项报告。

    词汇表外的形状不再抛穿：落成 `not_comparable` 一档，`ok=False`，`detail` 带位置与原因。
    """
    detail: list[str] = []
    try:
        auto = Automation.from_dict(dict(ir))
    except (IRDepthError, ParamDepthError) as exc:
        # 环/超深在 schema 之前就被 AF1 那道闸拦掉，轮不到下面的遍历——这里接住它，
        # 否则「自引用容器」那一格又以另一种具名错抛穿校验器（本函数的本职是拒绝这一格）。
        return FidelityReport(
            ok=False,
            nl_deterministic=False,
            nl_full_coverage=False,
            projection_fidelity=False,
            detail=[f"无法比较（不是不等）：入口解析即抛 {type(exc).__name__}：{exc}"],
            not_comparable=True,
        )

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
    not_comparable = False
    projection_fidelity = False
    try:
        rebuilt = Automation.from_dict(project_automation(auto))
        projection_fidelity = fidelity_equal(auto, rebuilt)
    except (FidelityNotComparable, ParamDepthError, LeafUnserializable) as exc:
        not_comparable = True
        detail.append(f"无法比较（不是不等）：{exc}")
    if not not_comparable and not projection_fidelity:
        detail.append("投影回写与原文不满足分层保真判据")
        # 定位差异供调试
        ca, cb = _automation_core(auto), _automation_core(rebuilt)
        if set(ca["nodes"]) != set(cb["nodes"]):
            detail.append(f"节点集差异：{set(ca['nodes']) ^ set(cb['nodes'])}")

    ok = nl_deterministic and nl_full_coverage and projection_fidelity and not not_comparable
    return FidelityReport(
        ok=ok,
        nl_deterministic=nl_deterministic,
        nl_full_coverage=nl_full_coverage,
        projection_fidelity=projection_fidelity,
        detail=detail,
        not_comparable=not_comparable,
    )
