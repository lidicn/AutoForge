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

__all__ = [
    "normalize_condition",
    "condition_equivalent",
    "MAX_CNF_CLAUSES",
    "CNFBudgetExceeded",
]

#: CNF 子句数硬上限。分配律展开是笛卡尔积：`or` 的子句数是各子 CNF 之积，
#: k 个二选一子句就会炸成 2^k（实测 k=18 即 OOM / exit 137）。归一化只是
#: 「证明两个条件等价」的手段，不该把进程撑爆——超预算必须显式失败而不是
#: 无限膨胀（新增审计 BUG-06）。
MAX_CNF_CLAUSES = 1024


class CNFBudgetExceeded(ValueError):
    """CNF 展开超出 `MAX_CNF_CLAUSES` 预算（表达式过于复杂，无法归一化）。"""


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


def _to_cnf(node: tuple, budget: list[int]) -> list[frozenset[str]]:
    """NNF -> CNF（子句=文字 frozenset；结果=子句列表，合取语义）。文字带 `!` 前缀表否定。

    `budget` 是单元素列表形式的**剩余额度**计数器（递归共享）。之所以在乘起来
    *之前* 判额度：笛卡尔积一旦分配出去，OOM 发生了再报已经太晚
    （新增审计 BUG-06）。
    """
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
            out.extend(_to_cnf(child, budget))
        budget[0] -= len(out)
        if budget[0] < 0:
            raise CNFBudgetExceeded(
                f"condition 归一化超预算：合取展开后子句数超过 {MAX_CNF_CLAUSES}"
            )
        return out
    # or：分配律——各子 CNF 各取一子句并集，笛卡尔积
    merged: list[frozenset[str]] = [frozenset()]
    for child in node[1]:
        child_clauses = _to_cnf(child, budget)
        # 先判乘积规模，再决定要不要真的分配
        if merged and child_clauses and len(merged) * len(child_clauses) > budget[0]:
            raise CNFBudgetExceeded(
                f"condition 归一化超预算：分配律展开将产生 "
                f"{len(merged) * len(child_clauses)} 个子句，剩余额度仅 {budget[0]}"
                f"（上限 {MAX_CNF_CLAUSES}）"
            )
        merged = [base | cc for base in merged for cc in child_clauses]
    # 丢弃永真子句（同含 A 与 ¬A）——它对合取无约束
    kept = [c for c in merged if not _is_tautology(c)]
    budget[0] -= len(kept)
    if budget[0] < 0:
        raise CNFBudgetExceeded(
            f"condition 归一化超预算：展开后子句数超过 {MAX_CNF_CLAUSES}"
        )
    return kept


def _is_tautology(clause: frozenset[str]) -> bool:
    for lit in clause:
        if lit.startswith("!") and lit[1:] in clause:
            return True
    return False


def normalize_condition(
    expr: Mapping[str, Any] | None, *, max_clauses: int = MAX_CNF_CLAUSES
) -> frozenset[frozenset[str]]:
    """condition 归一为 CNF 规范形（子句集合的集合，天然与括号/顺序无关）。

    `None`/空 → `frozenset()`（空合取 = 恒真 = "无条件"）。

    超出 `max_clauses` 预算时抛 `CNFBudgetExceeded`——**不静默降级**：静默返回一个
    截断结果会让「证明等价」变成「谎报等价」（新增审计 BUG-06）。
    """
    if not expr:
        return frozenset()
    return frozenset(_to_cnf(_nnf(expr, neg=False), [int(max_clauses)]))


def condition_equivalent(a: Mapping[str, Any] | None, b: Mapping[str, Any] | None) -> bool:
    """两个 condition 是否 L1 结构等价（归一化后相等）。

    超预算时返回 **False**：无法证明等价 ≠ 判为等价。让它落到「疑似有差异、
    交人工看」这一侧，而不是把危险的改动放过去（新增审计 BUG-06）。
    """
    try:
        return normalize_condition(a) == normalize_condition(b)
    except CNFBudgetExceeded:
        return False
