"""condition（IR `expr`）归一化 —— F14 P1 Fidelity Verifier 的 L1 结构等价基元。

判据（DCD 20261001《AF 三题》·F）：核心字段 L0 完全相等；**`condition` 允许 L1 结构等价**——
布尔表达式经归一化（德摩根下推 not、双重否定消除、分配律转 CNF、子句/文字去重）后比较。
`(A and B) or C` 与 `(A or C) and (B or C)` 归一化后同为 CNF `((A ∨ C) ∧ (B ∨ C))`，判**等价**。

表达式文法（见 `af_nl._expr_text` / `af_ir.expr`）：
    and/or     {"op": "and"|"or", "args": [...]}
    not        {"op": "not",      "args": [x]}
    比较       {"op": eq|ne|lt|lte|gt|gte, "left": operand, "right": operand}
    一元谓词   {"op": is_on|is_off|is_home|is_not_home|truthy, "value": operand}
比较 / 一元谓词为叶子（literal）。
"""
from __future__ import annotations

import json
from typing import Any, Mapping

__all__ = ["normalize_condition", "condition_equivalent"]

_BINARY = ("and", "or")
_FLIP = {"and": "or", "or": "and"}


def _leaf_key(expr: Mapping[str, Any]) -> str:
    return json.dumps(expr, sort_keys=True, ensure_ascii=False)


def _nnf(expr: Mapping[str, Any], neg: bool) -> tuple:
    """否定范式：not 下推到叶子、双重否定抵消。返回 ("lit"|"not"|"and"|"or"|"empty", ...)。"""
    op = expr.get("op")
    if op == "not":
        args = expr.get("args", ())
        return _nnf(args[0], not neg) if args else ("lit", _leaf_key(expr))
    if op in _BINARY:
        eff = _FLIP[op] if neg else op
        children = tuple(_nnf(a, neg) for a in expr.get("args", ()))
        if not children:
            return ("empty",)
        return (eff, children)
    return ("not" if neg else "lit", _leaf_key(expr))


def _to_cnf(node: tuple) -> list[frozenset[str]]:
    """NNF -> CNF（子句=文字 frozenset；结果=子句列表，合取语义）。文字带 `!` 前缀表否定。"""
    tag = node[0]
    if tag == "lit":
        return [frozenset({node[1]})]
    if tag == "not":
        return [frozenset({"!" + node[1]})]
    if tag == "empty":
        return []
    if tag == "and":
        out: list[frozenset[str]] = []
        for child in node[1]:
            out.extend(_to_cnf(child))
        return out
    # or：分配律——各子 CNF 各取一子句并集，笛卡尔积
    merged: list[frozenset[str]] = [frozenset()]
    for child in node[1]:
        child_clauses = _to_cnf(child)
        merged = [base | cc for base in merged for cc in child_clauses]
    # 丢弃永真子句（同含 A 与 ¬A）——它对合取无约束
    return [c for c in merged if not _is_tautology(c)]


def _is_tautology(clause: frozenset[str]) -> bool:
    for lit in clause:
        if lit.startswith("!") and lit[1:] in clause:
            return True
    return False


def normalize_condition(expr: Mapping[str, Any] | None) -> frozenset[frozenset[str]]:
    """condition 归一为 CNF 规范形（子句集合的集合，天然与括号/顺序无关）。

    `None`/空 → `frozenset()`（空合取 = 恒真 = "无条件"）。
    """
    if not expr:
        return frozenset()
    return frozenset(_to_cnf(_nnf(expr, neg=False)))


def condition_equivalent(a: Mapping[str, Any] | None, b: Mapping[str, Any] | None) -> bool:
    """两个 condition 是否 L1 结构等价（归一化后相等）。"""
    return normalize_condition(a) == normalize_condition(b)
