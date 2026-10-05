"""F14 P4 — 不可逆字段处理（L2/L3 标注 + `[运行时]` 占位符）。

设计依据 F14 §2.2 / §2.3 / §3.3：
- **运行时产物**（仿真注入的 `stage`/`diff_sha` 等）不属于可逆核心字段：
  NL→IR **不写入**，IR→NL→IR **原样保留**（留在节点 `raw`，不进核心比较）。
- **L2/L3 不可逆字段**必须在 IR 输出里显式标注 `_non_reversible: true`，
  让 NL 渲染用 `[运行时]` 占位符承认其存在、但不假装能还原它。

本模块只操作 dict 层的 `raw`/标记，**不改动 `ir.schema.json`**（节点 schema
`additionalProperties: true`，扩展键合法且被 `Node.from_dict` 原样保留），
因此不触碰铁律 #1「字段增删先改 schema」——这里没有任何核心 schema 字段变化。
"""
from __future__ import annotations

from typing import Any, Mapping

__all__ = [
    "RUNTIME_ONLY_FIELDS",
    "NON_REVERSIBLE_KEY",
    "NL_RUNTIME_PLACEHOLDER",
    "runtime_fields_of",
    "is_node_non_reversible",
    "annotate_non_reversible",
    "nl_runtime_note",
]

#: 仿真/运行时注入的字段——不是 NL 可逆核心，往返只保留、不重建。
#: 此集合须与 ir.schema.json node 段 $comment 白名单逐字一致（DCD 裁定
#: 20261005-AF-ir_non_reversible是否升schema + scripts/check_ir_runtime_keys.py 硬门）。
#: 注：审计载荷用的 `store_diff_sha256` 是返回值字段、非 IR 节点键，不在此集合。
RUNTIME_ONLY_FIELDS: frozenset[str] = frozenset({
    "stage", "diff_sha", "simulate_track", "honest_report",
})

#: L2/L3 不可逆标记键（写在节点上）。
NON_REVERSIBLE_KEY = "_non_reversible"

#: NL 渲染里代表"此字段属运行时、不可逆"的占位符。
NL_RUNTIME_PLACEHOLDER = "[运行时]"


def runtime_fields_of(node: Mapping[str, Any]) -> list[str]:
    """节点上出现的运行时字段名（不含核心字段），排序稳定。"""
    return sorted(k for k in node if k in RUNTIME_ONLY_FIELDS)


def is_node_non_reversible(node: Mapping[str, Any]) -> bool:
    """节点是否被判定为不可逆：显式标了 `_non_reversible` 或携带运行时字段。"""
    if node.get(NON_REVERSIBLE_KEY) is True:
        return True
    return bool(runtime_fields_of(node))


def annotate_non_reversible(node: dict[str, Any]) -> dict[str, Any]:
    """携带运行时字段但未标注时补 `_non_reversible: true`（原地返回新 dict）。

    NL→IR 侧绝不调用它写入运行时字段；仅用于把"已有运行时内容"的 IR 规范化标注。
    """
    if runtime_fields_of(node) and node.get(NON_REVERSIBLE_KEY) is not True:
        marked = dict(node)
        marked[NON_REVERSIBLE_KEY] = True
        return marked
    return node


def nl_runtime_note(node: Mapping[str, Any]) -> str:
    """给 NL 渲染的运行时占位说明；无可标注内容时返回空串。"""
    fields = runtime_fields_of(node)
    if not fields and node.get(NON_REVERSIBLE_KEY) is not True:
        return ""
    names = "、".join(fields) if fields else "不可逆字段"
    return f"（{NL_RUNTIME_PLACEHOLDER} 字段：{names}，非 NL 可逆核心）"
