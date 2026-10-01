"""第三轮审计核实：表达式求值层的**异常收口不变量**。

不变量：`evaluate()` / `call_function()` 只允许抛 `ExprError` / `UnknownEntity`。
executor 的 `if` 节点捕获列表按这两类写死（`af_executor.py`），任何 `TypeError`/
`OverflowError` 逃逸都会让 `_soft_fail`（on_error 降级 + ENTITY_DRIFT 审计 + 重试）
整条被跳过——外层 `except Exception` 只保证进程不挂，不保证降级发生。

这里既做**全量类型扫描**（新增函数无法再悄悄漏类型校验），也做**端到端 soft-fail**
反例锁（表达式层与捕获列表的耦合一旦再次断裂，测试必须变红）。
"""

from __future__ import annotations

import pytest

from autoforge.af_audit import ENTITY_DRIFT
from autoforge.af_instance import DONE
from autoforge.af_ir import Graph, load_automation
from autoforge.af_ir.expr import FUNCTIONS, ExprError, call_function, evaluate
from autoforge.af_runtime import build_runtime
from autoforge.af_state import UnknownEntity

CONTROLLED = (ExprError, UnknownEntity)

#: 求值层能见到的全部值形态（HA 实体状态原生是字符串；inf/nan 经 type=numeric 会成功）
SHAPES: list[tuple[str, object]] = [
    ("str-num", "21.5"),
    ("str-word", "on"),
    ("str-unavail", "unavailable"),
    ("str-empty", ""),
    ("int", 20),
    ("float", 21.5),
    ("zero", 0),
    ("neg", -3.7),
    ("bool", True),
    ("none", None),
    ("inf", float("inf")),
    ("nan", float("nan")),
    ("huge", 1e308),
    ("list", [1, 2]),
    ("list-str", ["a", "b"]),
    ("list-mix", ["a", 1]),
    ("iso", "2026-01-02T03:04:05"),
    ("iso-garbage", "not-a-date"),
    ("hhmm", "22:00"),
    ("hhmm-bad", "99:99"),
]


def _sweep():
    """展开 (函数, 参数位, 值标签) 用例。"""
    for name in sorted(FUNCTIONS):
        low, high, _impl = FUNCTIONS[name]
        arities = [1, 2, 3] if high is None else list(range(low, min(high, 3) + 1))
        for arity in arities or [low]:
            for i in range(arity):
                for tag, val in SHAPES:
                    yield name, arity, i, tag, val


@pytest.mark.parametrize("name,arity,idx,tag,val", list(_sweep()), ids=lambda v: str(v))
def test_每个函数的每个参数位都不得抛受控异常之外(name, arity, idx, tag, val):
    """求值层的全量类型扫描：新增函数再也不能悄悄漏掉类型校验。

    合法返回当然允许（如 `abs(21.5)`）；这里只钉住**抛出的异常必须是哪一类**。
    """
    args = [0.0] * arity
    args[idx] = val
    try:
        call_function(name, args)
    except CONTROLLED:
        pass


@pytest.mark.parametrize("op", ["eq", "ne", "lt", "lte", "gt", "gte"])
def test_比较算子跨类型矩阵不逃逸(op):
    """任意两侧类型组合都不得抛出 ExprError/UnknownEntity 之外的异常。"""
    for _lt, lv in SHAPES:
        for _rt, rv in SHAPES:
            try:
                evaluate({"op": op, "left": {"const": lv}, "right": {"const": rv}}, lambda n, t: None)
            except CONTROLLED:
                continue


# ── 缺陷本体：min/max 混类型（审计缺陷 1）────────────────────────────


def _min_max_expr(fn, left, right):
    return {
        "op": "gt",
        "left": {"fn": fn, "args": [left, right]},
        "right": {"const": 10},
    }


@pytest.mark.parametrize("fn", ["min", "max"])
@pytest.mark.parametrize("state", ["21.5", "on", "unavailable", "inf", "nan", "", "21.5x"])
def test_min_max_混类型必须抛_ExprError_而非_TypeError(fn, state):
    """`min(sensor_temp, 20) > 10`，实体状态是 HA 原生字符串。

    修复前：Python 的 `'<' not supported between 'int' and 'str'` 直接逃逸。
    """
    expr = _min_max_expr(fn, {"var": "entity.temp"}, {"const": 20})
    with pytest.raises(ExprError) as exc:
        evaluate(expr, lambda n, t: state)
    # 消息要能指路：告诉作者该声明 type=numeric，而不是只说类型混用
    assert "type=numeric" in str(exc.value)


@pytest.mark.parametrize("fn", ["min", "max"])
def test_min_max_同类字符串仍然可用(fn):
    """收紧类型校验不能顺手禁掉合法用法（同型字符串按字典序）。"""
    expr = {
        "op": "eq",
        "left": {"fn": fn, "args": [{"const": "beta"}, {"const": "alpha"}]},
        "right": {"const": "alpha" if fn == "min" else "beta"},
    }
    assert evaluate(expr, lambda n, t: None) is True


@pytest.mark.parametrize("fn", ["min", "max"])
def test_min_max_数字与数组形态仍按原语义(fn):
    assert evaluate({"op": "eq", "left": {"fn": fn, "args": [{"const": [3, 1, 2]}]},
                     "right": {"const": 1 if fn == "min" else 3}}, lambda n, t: None) is True
    assert evaluate({"op": "eq", "left": {"fn": fn, "args": [{"const": 5}, {"const": 2}]},
                     "right": {"const": 2 if fn == "min" else 5}}, lambda n, t: None) is True


@pytest.mark.parametrize("fn", ["min", "max"])
def test_min_max_布尔不参与数值最值(fn):
    """bool 是 int 子类，混进来会得到 `min(True, 20)=1` 这种静默错答。"""
    with pytest.raises(ExprError):
        call_function(fn, [True, 20])


def test_min_max_混合数组同样收口():
    with pytest.raises(ExprError):
        call_function("min", [["a", 1]])


# ── 审计未报出的第二条逃逸：round 的 OverflowError ───────────────────


@pytest.mark.parametrize("nd", [float("inf"), float("nan")])
def test_round_位数非有限数字不得逃逸(nd):
    """`round(x, n)` 的 n 若为 inf/nan → `OverflowError`（捕获列表里没有）。

    第三轮审计只记了 `floor/ceil` 的 ValueError（在捕获列表内，soft-fail 仍生效），
    漏了这条真正绕过 soft-fail 的路径。
    """
    with pytest.raises(ExprError):
        call_function("round", [21.5, nd])


@pytest.mark.parametrize("fn", ["floor", "ceil"])
@pytest.mark.parametrize("value", [float("inf"), float("nan")])
def test_floor_ceil_非有限数字给可读错误(fn, value):
    """修复前抛 `ValueError: cannot convert float NaN to integer`——消息与真因无关。"""
    with pytest.raises(ExprError) as exc:
        call_function(fn, [value])
    assert "有限" in str(exc.value)


def test_declared_numeric_放行_inf_nan_所以求值层必须自己挡():
    """审计的 hardening note 判定「inf/nan 无法经 HA 状态字符串进入」，实测不成立。

    `type: numeric` 的强制转换是 `float(value)`，而 `float('inf')` / `float('nan')`
    / `float('1e400')` 全部成功——非有限数字照样进得来。
    """
    for state in ("inf", "nan", "1e400"):
        with pytest.raises(ExprError):
            evaluate(
                {"op": "gt", "left": {"fn": "floor", "args": [{"var": "e", "type": "numeric"}]},
                 "right": {"const": 10}},
                lambda n, t: state,
            )


def test_未声明类型的_nan_不再静默判假():
    """`nan > 10` 恒 False 是**静默错答**，比抛错更危险（条件永不成立且无人知晓）。"""
    with pytest.raises(ExprError):
        evaluate({"op": "gt", "left": {"var": "e"}, "right": {"const": 10}}, lambda n, t: float("nan"))


# ── 端到端反例锁：soft-fail 降级必须真的发生 ─────────────────────────

ON_M = {"id": "a1", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}}


def _guard_ir(expr):
    return {
        "ir_version": "0.2.1",
        "id": "demo",
        "name": "示例",
        "version": 1,
        "mode": "single",
        "nodes": [
            ON_M,
            {"id": "i1", "kind": "if", "expr": expr},
            {"id": "p1", "kind": "pass"},
            {"id": "p2", "kind": "pass"},
        ],
        "edges": [
            {"from": "a1", "to": "i1", "kind": "then"},
            {"from": "i1", "to": "p1", "kind": "then"},
            {"from": "i1", "to": "p2", "kind": "on_error"},
            {"from": "i1", "to": "p2", "kind": "default"},
        ],
    }


def _run(expr, states):
    runtime = build_runtime(Graph([load_automation(_guard_ir(expr))]))
    for k, v in states.items():
        runtime.states.set_state(k, v)
    runtime.emit("binary_sensor.m", "on", last_changed="t1")
    return runtime


def test_混类型表达式走_on_error_降级并留下审计():
    """修复前：TypeError 穿透捕获列表 → 实例走未预期路径，on_error/审计全部不发生。"""
    expr = {
        "op": "gt",
        "left": {"fn": "min", "args": [{"var": "entity.sensor.sensor_temp"}, {"const": 20}]},
        "right": {"const": 10},
    }
    runtime = _run(expr, {"binary_sensor.m": "on", "sensor.sensor_temp": "21.5"})

    instance = runtime.instances.all()[0]
    assert instance.state == DONE
    assert instance.ctx.trace[-1]["node"] == "p2", "必须经 on_error 落到兜底节点"
    drift = [e for e in runtime.audit if e.type == ENTITY_DRIFT]
    assert drift and "type=numeric" in drift[0].message


def test_正常数值表达式仍走_then():
    expr = {
        "op": "gt",
        "left": {"fn": "min", "args": [{"var": "entity.sensor.sensor_temp", "type": "numeric"}, {"const": 20}]},
        "right": {"const": 10},
    }
    runtime = _run(expr, {"binary_sensor.m": "on", "sensor.sensor_temp": "21.5"})
    instance = runtime.instances.all()[0]
    assert instance.state == DONE
    assert instance.ctx.trace[-1]["node"] == "p1"
    assert not [e for e in runtime.audit if e.type == ENTITY_DRIFT]
