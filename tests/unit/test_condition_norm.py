"""condition 归一化单测 —— F14 P1 分层判据的 L1 结构等价基元。"""
from autoforge.af_ir.condition_norm import condition_equivalent, normalize_condition


def _cmp(op, var, const):
    return {"op": op, "left": {"var": var}, "right": {"const": const}}


def _and(*args):
    return {"op": "and", "args": list(args)}


def _or(*args):
    return {"op": "or", "args": list(args)}


def test_simple_comparison_normalizes_to_single_clause():
    cnf = normalize_condition(_cmp("gt", "entity.temp", 30))
    assert len(cnf) == 1  # 单个叶子 → 单原子句
    assert len(next(iter(cnf))) == 1  # 子句含一个文字


def test_de_morgan_distribution_equivalent():
    """(A and B) or C ≡ (A or C) and (B or C) —— L1 结构等价（分配律）必须判等。"""
    A, B, C = _cmp("eq", "a", 1), _cmp("eq", "b", 2), _cmp("eq", "c", 3)
    left = _or(_and(A, B), C)
    right = _and(_or(A, C), _or(B, C))
    assert condition_equivalent(left, right) is True


def test_operand_order_irrelevant():
    A, B = _cmp("eq", "a", 1), _cmp("eq", "b", 2)
    assert condition_equivalent(_and(A, B), _and(B, A)) is True


def test_double_negation_eliminated():
    A = _cmp("eq", "a", 1)
    single = A
    double = {"op": "not", "args": [{"op": "not", "args": [A]}]}
    assert condition_equivalent(single, double) is True


def test_semantically_different_not_equivalent():
    A, B = _cmp("eq", "a", 1), _cmp("eq", "b", 2)
    assert condition_equivalent(_and(A, B), _or(A, B)) is False


def test_tautology_clause_dropped():
    """A ∨ ¬A 恒真 → 该子句应被丢弃（不约束整体）。"""
    A = _cmp("eq", "a", 1)
    B = _cmp("eq", "b", 2)
    nota = {"op": "not", "args": [A]}
    # (A ∨ ¬A) ∧ B  → 等价于 B
    expr = _and(_or(A, nota), B)
    assert normalize_condition(expr) == normalize_condition(B)


def test_empty_condition_is_true():
    assert normalize_condition(None) == frozenset()
    assert condition_equivalent(None, None) is True
