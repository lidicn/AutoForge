"""§十八 残余 B.2：保真校验器的 `_canon` 不再吃裸 `json.dumps`。

两格各自的塌法（都在 HEAD `bf8c563` 上真跑出来过，见执行记录 §二之八十七 §一）：
- 抛穿：词汇表外的值（date/set/自引用/混合键型）让 `verify_roundtrip` 直接抛 `TypeError`/`ValueError`，
  调用方拿到崩而不是报告；
- 塌陷：`("x","y")` 与 `["x","y"]`、`{1:"a"}` 与 `{"1":"a"}` canon 成**同一个串**，
  于是「类型不同」被判成「保真」——校验器的本职恰恰是拒绝这一格。

修法口径：`_canon` 改成带类型标记的递归编码，词汇表就是 JSON 那一套；表外形状抛
`FidelityNotComparable`（具名、带位置），深度预算取 `MAX_PARAM_DEPTH` 那份单一真值源。
"""
from __future__ import annotations

import ast
import datetime
import math
from dataclasses import fields
from pathlib import Path

import pytest

from autoforge import af_fidelity
from autoforge.af_fidelity import (
    CANON_ERR_UNSUPPORTED,
    FidelityNotComparable,
    _canon,
    fidelity_equal,
    verify_roundtrip,
)
from autoforge.af_ir import Automation
from autoforge.af_ir.models import MAX_PARAM_DEPTH, ParamDepthError

SRC = Path(__file__).resolve().parents[2] / "src" / "autoforge" / "af_fidelity.py"


def _ir(params):
    """最小连通链 on → do → pass；params 是要验的那一格载荷。"""
    return {
        "ir_version": "0.2.1",
        "id": "a1",
        "name": "a1",
        "version": 1,
        "mode": "single",
        "nodes": [
            {"id": "n_on", "kind": "on",
             "trigger": {"type": "state", "entity_id": "binary_sensor.presence", "to": "on"}},
            {"id": "n_do", "kind": "do", "adapter": "ha", "action": "light.turn_on", "params": params},
            {"id": "n_pass", "kind": "pass"},
        ],
        "edges": [
            {"from": "n_on", "to": "n_do", "kind": "then"},
            {"from": "n_do", "to": "n_pass", "kind": "then"},
        ],
    }


def _auto(params, aid="a1"):
    ir = _ir(params)
    ir["id"] = aid
    ir["name"] = aid
    return Automation.from_dict(ir)


def _tree() -> ast.Module:
    return ast.parse(SRC.read_text(encoding="utf-8"))


def _func(name: str) -> ast.FunctionDef:
    for node in _tree().body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"函数 {name} 不在 {SRC.name} 顶层")


# ── ① 抛穿的四格：现在必须是报告，不是崩 ──────────────────────────────────
@pytest.mark.parametrize("params,fragment", [
    ({"when": datetime.date(2026, 1, 1)}, "date"),
    ({"tags": {"a", "b"}}, "set"),
])
def test_unserializable_value_becomes_a_report_not_a_crash(params, fragment):
    report = verify_roundtrip(_ir(params))
    assert report.not_comparable is True
    assert report.ok is False
    assert report.projection_fidelity is False
    assert any(fragment in d for d in report.detail)


def test_self_referential_container_reports_instead_of_recursion_error():
    loop: list = []
    loop.append(loop)
    # 反证的另一面：环不再靠解释器栈去撞，而是走同一份深度预算抛具名错
    with pytest.raises(ParamDepthError):
        _canon(loop, where="params")
    report = verify_roundtrip(_ir({"loop": loop}))
    assert report.not_comparable is True
    assert report.ok is False


def test_mixed_key_types_report_not_comparable():
    report = verify_roundtrip(_ir({1: "a", "b": 2}))
    assert report.not_comparable is True
    assert report.ok is False


def test_detail_carries_the_position_and_the_named_code():
    report = verify_roundtrip(_ir({"when": datetime.date(2026, 1, 1)}))
    joined = " ".join(report.detail)
    assert CANON_ERR_UNSUPPORTED in joined
    assert "nodes[n_do].params[when]" in joined


def test_exception_shape_carries_code_where_detail_and_is_a_valueerror():
    with pytest.raises(FidelityNotComparable) as caught:
        _canon({"k": datetime.date(2026, 1, 1)}, where="nodes[n_x].params")
    err = caught.value
    assert err.code == CANON_ERR_UNSUPPORTED
    assert err.where == "nodes[n_x].params[k]"
    assert isinstance(err, ValueError)  # 调用方按既有 ValueError 口径接得住


# ── ② 塌陷的格子：不同型不许再判成相等 ────────────────────────────────────
def test_tuple_and_list_params_are_no_longer_equal():
    assert _canon(("x", "y"), where="v") != _canon(["x", "y"], where="v")
    assert fidelity_equal(_auto({"v": ["x", "y"]}), _auto({"v": ("x", "y")})) is False


def test_int_key_versus_str_key_is_refused_not_collapsed():
    with pytest.raises(FidelityNotComparable) as caught:
        _canon({1: 1}, where="v")
    assert caught.value.where == "v"
    assert "1" in caught.value.detail
    # 嵌套层的 int 键同样拒，并且位置一路带出来（不是只在顶层设防）
    with pytest.raises(FidelityNotComparable) as nested:
        _canon({"outer": {2: "x"}}, where="v")
    assert nested.value.where == "v[outer]"


def test_non_finite_floats_are_refused():
    for value in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(FidelityNotComparable):
            _canon(value, where="v")
    assert math.isfinite(math.pi)  # 有限浮点仍走正常档


def test_int_and_bool_stay_distinct():
    assert _canon(1, where="v") != _canon(True, where="v")
    assert _canon(0, where="v") != _canon(False, where="v")


def test_string_delimiters_do_not_collapse_across_items():
    assert _canon(["a,b"], where="v") != _canon(["a", "b"], where="v")
    assert _canon({"k]:": 1}, where="v") != _canon({"k": 1}, where="v")
    # 字符串叶子不会伪造出类型标记：带引号的原文与真 int 的编码必须分开
    assert _canon('str:"int:1"') != _canon(1, where="v")


# ── ③ 不许误伤：合法形状照常保真 ──────────────────────────────────────────
def test_normal_ir_still_passes_the_roundtrip():
    report = verify_roundtrip(_ir({"entity_id": "light.living", "rgb": [255, 0, 0], "brightness": 0.5}))
    assert report.ok is True
    assert report.not_comparable is False
    assert report.detail == []


def test_key_order_is_still_ignored():
    assert _canon({"b": 1, "a": 2}, where="v") == _canon({"a": 2, "b": 1}, where="v")


def test_same_automation_is_always_equal_to_itself():
    auto = _auto({"entity_id": "light.l", "nested": {"a": [1, {"b": None}]}})
    assert fidelity_equal(auto, auto) is True


def test_nesting_inside_the_shared_budget_still_canonicalises():
    value = 1
    for _ in range(MAX_PARAM_DEPTH - 1):
        value = {"a": value}
    assert isinstance(_canon(value, where="v"), str)


def test_nesting_over_budget_raises_the_named_depth_error():
    value = 1
    for _ in range(MAX_PARAM_DEPTH + 16):
        value = {"a": value}
    with pytest.raises(ParamDepthError):
        _canon(value, where="v")


# ── ④ 静态面：形状被钉住，防"改回旧写法"──────────────────────────────────
def test_bare_dumps_of_the_whole_value_is_gone_from_canon():
    body = ast.dump(_func("_canon"))
    assert "sort_keys" not in body, "_canon 又拿整值 sort_keys dumps＝旧形状回来"
    calls = [n for n in ast.walk(_func("_canon"))
             if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "dumps"]
    assert calls, "_canon 仍应借 json.dumps 编码 str 叶子（转义由库负责）"
    for call in calls:
        arg = call.args[0]
        assert isinstance(arg, ast.Name) and arg.id in {"value", "key", "item"}, \
            "json.dumps 只准编码单枚字符串叶子，不许再吃整格容器"


def test_depth_budget_comes_from_the_shared_constant_not_a_second_copy():
    src = SRC.read_text(encoding="utf-8")
    canon_src = ast.get_source_segment(src, _func("_canon")) or ""
    assert "check_param_depth(" in canon_src, "_canon 没走共享深度入口＝自己再造一份深度口径"
    numbers = {n.value for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Constant) and isinstance(n.value, int)}
    assert MAX_PARAM_DEPTH not in numbers, "模块里出现了第二份深度数字＝手抄"


def test_verify_roundtrip_catches_named_errors_only():
    handlers = [n for n in ast.walk(_func("verify_roundtrip")) if isinstance(n, ast.ExceptHandler)]
    assert handlers
    for handler in handlers:
        names = [ast.dump(t) for t in ([handler.type] if isinstance(handler.type, ast.Name)
                                       else handler.type.elts)]
        assert all("Name" in n for n in names), "except 里出现 Exception/BaseException＝宽捕"
        assert not any("Exception" in n or "BaseException" in n for n in names)


def test_report_has_the_not_comparable_tier_and_it_is_exported():
    names = [f.name for f in fields(af_fidelity.FidelityReport)]
    assert "not_comparable" in names
    assert "FidelityNotComparable" in af_fidelity.__all__


def test_ok_is_derived_never_a_literal_true():
    report = verify_roundtrip(_ir({"entity_id": "light.l"}))
    assert report.ok is (report.nl_deterministic and report.nl_full_coverage
                         and report.projection_fidelity and not report.not_comparable)
