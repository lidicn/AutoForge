"""condition 归一化单测 —— F14 P1 分层判据的 L1 结构等价基元。"""
import ast
import datetime
import json
import pathlib

import pytest

from autoforge.af_ir.condition_norm import (
    CNFBudgetExceeded,
    LeafUnserializable,
    condition_equivalent,
    normalize_condition,
)


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


# ── 第十三轮 §三：`_leaf_key` 的第二条失败路径（序列化不经遍历，预算挡不住） ──


def _cyclic_leaf():
    leaf = {"op": "is_on", "value": {"self": None}}
    leaf["value"]["self"] = leaf
    return leaf


def _deep_leaf(levels):
    cur = root = {"k": None}
    for _ in range(levels):
        nxt = {"k": None}
        cur["k"] = nxt
        cur = nxt
    return {"op": "is_on", "value": root}


def test_the_raw_failure_really_exists_before_any_wrapper():
    """反空洞：先证明 stdlib 在这两个输入上**确实**抛——否则下面的具名异常腿
    可能只是把一次本来就会成功的调用换了个名字。"""
    with pytest.raises(ValueError):
        json.dumps(_cyclic_leaf(), sort_keys=True)
    with pytest.raises(TypeError):
        json.dumps(datetime.datetime(2026, 1, 1))


def test_cyclic_leaf_raises_named_error_and_names_the_site():
    with pytest.raises(LeafUnserializable) as got:
        normalize_condition(_cyclic_leaf())
    text = str(got.value)
    assert "condition 叶子" in text                      # 说清是哪一层拒的
    assert "Circular reference" in text                  # 底层原因不被吞掉


@pytest.mark.parametrize("value", [__import__("datetime").datetime(2026, 1, 1), {1, 2}, b"bytes"])
def test_non_json_leaf_value_raises_named_error(value):
    """自引用只是其中一种：叶子含任何不可 JSON 序列化的值，都该收到同一个具名失败。"""
    with pytest.raises(LeafUnserializable):
        normalize_condition({"op": "is_on", "value": value})


def test_unserializable_leaf_is_read_as_not_equivalent_not_as_a_crash():
    """`condition_equivalent` 的既有口径：无法证明等价 ≠ 判为等价，必须落 False 交人工看。
    这条腿同时钉住继承关系——具名异常不是新语义，是复用同一档"证明不了"。"""
    assert issubclass(LeafUnserializable, CNFBudgetExceeded)
    leaf = _cyclic_leaf()
    assert condition_equivalent(leaf, leaf) is False


def test_serializable_deep_leaf_still_normalizes():
    """CONTROL：护栏不能严于必要——深嵌套但可序列化的叶子本就不该被拒（第十三轮实测
    这一档在原仓库是 safe，只有自引用/非 JSON 值才炸）。"""
    assert normalize_condition(_deep_leaf(300)) == normalize_condition(_deep_leaf(300))
    assert condition_equivalent(_deep_leaf(300), _deep_leaf(300)) is True


def test_guard_sits_on_the_leg_that_actually_serializes():
    """结构腿（第十/十一轮的教训：护栏装在不会被执行到的路径上等于没装）：
    `_leaf_key` 的函数体必须**整体**包在 try 里，且 except 同时收 TypeError 与 ValueError，
    处理器抛出 LeafUnserializable——预算挡不住这条腿，只有这里能挡。"""
    src = (
        pathlib.Path(__file__).resolve().parents[2]
        / "src" / "autoforge" / "af_ir" / "condition_norm.py"
    )
    fn = next(
        n for n in ast.walk(ast.parse(src.read_text(encoding="utf-8")))
        if isinstance(n, ast.FunctionDef) and n.name == "_leaf_key"
    )
    (body,) = fn.body
    assert isinstance(body, ast.Try) and len(body.handlers) == 1
    caught = {ast.unparse(t) for t in body.handlers[0].type.elts}
    assert caught == {"TypeError", "ValueError"}
    assert any(
        isinstance(s, ast.Raise) and ast.unparse(s.exc).startswith("LeafUnserializable(")
        for s in body.handlers[0].body
    )
