"""v1.0.0 表达力收口单测：内置函数库（math/string/time/list）/ 资源上限 / 编译期校验。

fn *节点*（自定义代码）仍是保留位（见 docs/fn_节点设计评估.md），本文件只覆盖
operand 级 `{"fn": ...}` 白名单函数调用。
"""

from __future__ import annotations

import pytest

from autoforge.af_ir import IRValidationError, Graph, load_automation, load_graph
from autoforge.af_ir.expr import (
    MAX_EXPR_DEPTH,
    MAX_EXPR_NODES,
    FUNCTIONS,
    ExprError,
    check_expr,
    collect_entity_refs,
    collect_var_refs,
    evaluate,
)
from autoforge.af_runtime import build_runtime
from autoforge.af_scanner import StaticScanner
from autoforge.af_state import InMemoryStateProvider


def _resolve_factory(env: dict[str, str]):
    def resolve(name: str, declared: str | None = None):
        if name not in env:
            raise KeyError(name)
        return env[name]

    return resolve


def _num(value, declared=None):
    return float(value)


def _eq(left, right) -> dict:
    return {"op": "eq", "left": left, "right": {"const": right}}


def _call(fn: str, *args) -> dict:
    return {"fn": fn, "args": list(args)}


# ── 函数库：math ─────────────────────────────────────────────────────


def test_math_functions():
    env = {"entity.temp": "25.6"}
    r = _resolve_factory(env)
    assert evaluate(_eq(_call("round", {"var": "entity.temp", "type": "numeric"}), 26.0), r)
    assert evaluate(_eq(_call("floor", {"var": "entity.temp", "type": "numeric"}), 25.0), r)
    assert evaluate(_eq(_call("ceil", {"var": "entity.temp", "type": "numeric"}), 26.0), r)
    assert evaluate(_eq(_call("abs", {"const": -3}), 3.0), r)
    assert evaluate(_eq(_call("clamp", {"const": 30}, {"const": 18}, {"const": 26}), 26.0), r)
    assert evaluate(_eq(_call("min", {"const": 3}, {"const": 1}, {"const": 2}), 1.0), r)
    assert evaluate(_eq(_call("max", {"const": [3, 1, 2]}), 3.0), r)
    assert evaluate(_eq(_call("sum", {"const": [1, 2, 3]}), 6.0), r)
    assert evaluate(_eq(_call("avg", {"const": [1, 2, 3]}), 2.0), r)


def test_nested_function_calls():
    """round(avg([...])) 嵌套求值。"""
    expr = _eq(
        _call("round", _call("avg", {"const": [21, 22, 26]})),
        23.0,
    )
    assert evaluate(expr, _resolve_factory({}))


def test_string_functions():
    r = _resolve_factory({"entity.mode": "Cooling", "entity.name": "客厅空调 "})
    assert evaluate({"op": "eq", "left": _call("lower", {"var": "entity.mode"}), "right": {"const": "cooling"}}, r)
    assert evaluate(_eq(_call("upper", {"const": "abc"}), "ABC"), _resolve_factory({}))
    assert evaluate(_eq(_call("trim", {"var": "entity.name"}), "客厅空调"), r)
    assert evaluate({"op": "truthy", "value": _call("contains", {"var": "entity.mode"}, {"const": "ool"})}, r)
    assert evaluate({"op": "truthy", "value": _call("starts_with", {"var": "entity.mode"}, {"const": "Cool"})}, r)
    assert evaluate({"op": "truthy", "value": _call("ends_with", {"const": "abc"}, {"const": "bc"})}, _resolve_factory({}))
    assert evaluate(_eq(_call("length", {"const": "客厅"}), 2), _resolve_factory({}))
    assert evaluate(_eq(_call("replace", {"const": "a-b"}, {"const": "-"}, {"const": "+"}), "a+b"), _resolve_factory({}))


def test_contains_polymorphic_list_and_string():
    r = _resolve_factory({})
    assert evaluate({"op": "truthy", "value": _call("contains", {"const": [1, 2, 3]}, {"const": 2})}, r)
    assert evaluate({"op": "truthy", "value": _call("contains", {"const": "hello"}, {"const": "ell"})}, r)


def test_first_last_and_list_length():
    r = _resolve_factory({})
    assert evaluate(_eq(_call("first", {"const": [9, 8, 7]}), 9), r)
    assert evaluate(_eq(_call("last", {"const": [9, 8, 7]}), 7), r)
    assert evaluate(_eq(_call("length", {"const": [1, 2, 3]}), 3), r)
    with pytest.raises(ExprError):
        evaluate(_eq(_call("first", {"const": []}), 0), r)


# ── 函数库：time（作用于显式 ISO 串，不读时钟）───────────────────────


def test_time_functions_explicit_timestamps():
    r = _resolve_factory({"vars.last_motion": "2026-09-15T08:30:00+00:00"})  # 周二
    iso = {"var": "vars.last_motion"}
    assert evaluate(_eq(_call("time_hour", iso), 8), r)
    assert evaluate(_eq(_call("time_minute", iso), 30), r)
    assert evaluate(_eq(_call("time_weekday", iso), 1), r)  # 0=周一，周二=1


def test_time_between_normal_and_cross_midnight_windows():
    r = _resolve_factory({})
    # 使用 naive ISO（无时区后缀）表示"本地墙钟时间"，避免 astimezone() 转换受运行机时区影响。
    # 带时区这条腿由 `test_time_between_aware_*` 覆盖（AF-757：此前只靠生产场景覆盖 ⇒
    # 容器 UTC 下错了 16 小时仍 CI 全绿）。
    def at(hhmm: str) -> dict:
        return {"const": f"2026-09-15T{hhmm}:00"}

    # 普通窗口 [07:00, 09:00)
    assert evaluate({"op": "truthy", "value": _call("time_between", at("08:30"), {"const": "07:00"}, {"const": "09:00"})}, r)
    assert not evaluate({"op": "truthy", "value": _call("time_between", at("09:00"), {"const": "07:00"}, {"const": "09:00"})}, r)
    # 跨午夜窗口 [22:00, 07:00)
    assert evaluate({"op": "truthy", "value": _call("time_between", at("23:30"), {"const": "22:00"}, {"const": "07:00"})}, r)
    assert evaluate({"op": "truthy", "value": _call("time_between", at("06:00"), {"const": "22:00"}, {"const": "07:00"})}, r)
    assert not evaluate({"op": "truthy", "value": _call("time_between", at("12:00"), {"const": "22:00"}, {"const": "07:00"})}, r)


#: 家庭时区的全部环境键（读取顺序见 `af_time.TZ_ENV_KEYS`：`HOMESDK_` 前缀优先）。
#: 逐枚清空再写 `AF_TZ`，否则跑测试的机器上只要有一个 `TZ` 就把断言判成另一个墙钟。
_TZ_ENV_KEYS = (
    "HOMESDK_TZ", "TZ", "HOMESDK_AF_TZ", "AF_TZ",
    "HOMESDK_TZ_OFFSET_HOURS", "TZ_OFFSET_HOURS",
)


def _house_tz(monkeypatch, name: str) -> None:
    for key in _TZ_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AF_TZ", name)


def _between(iso: str, start: str, end: str) -> bool:
    r = _resolve_factory({})
    return bool(evaluate(
        {"op": "truthy", "value": _call(
            "time_between", {"const": iso}, {"const": start}, {"const": end})}, r))


def test_time_between_aware_iso_uses_house_tz(monkeypatch):
    """T1／T2：带时区的 ISO 按**家庭时区**归一，不认容器时区（AF-757）。

    缺陷现场（2026-11-12 主卧）：容器 `TZ` 为空 ⇒ 系统时区 `Etc/UTC`，无参
    `astimezone()` 把规格 21:00–23:45 挪到本地 05:00–07:45（错位 16 小时）——
    晚上怎么走动都不问、凌晨 5 点才问。
    """
    _house_tz(monkeypatch, "Asia/Shanghai")

    # T1：本地 21:25 在窗口内；本地 05:30 不在（左闭右开，边界逐枚核）
    assert _between("2026-11-12T21:25:00+08:00", "21:00", "23:45") is True
    assert _between("2026-11-12T05:30:00+08:00", "21:00", "23:45") is False
    assert _between("2026-11-12T21:00:00+08:00", "21:00", "23:45") is True
    assert _between("2026-11-12T23:45:00+08:00", "21:00", "23:45") is False

    # T2：同一瞬间的不同写法必须同结果（`Z` / `+00:00` / `+08:00`）
    assert _between("2026-11-12T13:25:00+00:00", "21:00", "23:45") is True
    assert _between("2026-11-12T13:25:00Z", "21:00", "23:45") is True
    assert _between("2026-11-11T21:30:00+00:00", "21:00", "23:45") is False  # ＝本地次日 05:30


def test_time_between_aware_cross_midnight(monkeypatch):
    """T4：跨午夜窗口 `[22:00, 07:00)` 在带时区形态下不回归。"""
    _house_tz(monkeypatch, "Asia/Shanghai")

    assert _between("2026-11-12T23:30:00+08:00", "22:00", "07:00") is True
    assert _between("2026-11-12T06:00:00+08:00", "22:00", "07:00") is True
    assert _between("2026-11-12T22:00:00+08:00", "22:00", "07:00") is True
    assert _between("2026-11-12T07:00:00+08:00", "22:00", "07:00") is False  # 右开
    assert _between("2026-11-12T12:00:00+08:00", "22:00", "07:00") is False


def test_time_between_ignores_container_tz(monkeypatch):
    """T3 负控：容器 `TZ=`（**空串**，缺陷现场形态）下仍按家庭墙钟 `Asia/Shanghai` 判。

    `TZ=` 空串＝「没配」而不是「配了个 UTC 时区」：`af_time._first_tz_value()` 跳过后
    落到 `Asia/Shanghai` fallback。旧代码走无参 `astimezone()`＝**系统时区**（容器里
    就是 `Etc/UTC`），与这里读到的家庭墙钟是两套，故整条链错 16 小时。
    """
    for key in _TZ_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TZ", "")  # 容器现读形态：`TZ=` 空串

    assert _between("2026-11-12T21:25:00+08:00", "21:00", "23:45") is True
    assert _between("2026-11-12T05:30:00+08:00", "21:00", "23:45") is False


def test_time_between_calls_load_tz_not_system_tz(monkeypatch):
    """T3 结构腿：归一目的一律取 `load_tz()`，⛔ 不许退回无参 `astimezone()`。

    把 `load_tz` 换成一个**本机系统时区不可能等于**的哨兵时区（`UTC-05:00`）：
    只要实现里还留着无参 `astimezone()`，这条断言就必然红——它不是数据腿的重复。
    """
    from datetime import timedelta, timezone as _timezone

    from autoforge.af_ir import expr as expr_mod

    monkeypatch.setattr(expr_mod, "load_tz", lambda: _timezone(timedelta(hours=-5)))

    # 2026-11-12T21:25+08:00 ＝ UTC 13:25 ＝ 哨兵时区 08:25 ⇒ 不在 [21:00, 23:45)
    assert _between("2026-11-12T21:25:00+08:00", "21:00", "23:45") is False
    # 同一瞬间的 UTC 写法结果一致（时刻等价腿在哨兵时区下同样成立）
    assert _between("2026-11-12T13:25:00+00:00", "21:00", "23:45") is False
    # 哨兵时区 08:25+5h... 换一个真落在窗口内的瞬间：UTC-05:00 的 21:30
    assert _between("2026-11-13T02:30:00+00:00", "21:00", "23:45") is True


def test_time_between_follows_af_tz(monkeypatch):
    """T3 配置腿：`AF_TZ=UTC` 时窗口按 UTC 判（⛔ 别写死 +8）。"""
    _house_tz(monkeypatch, "UTC")

    # 本地 21:25 ＝ UTC 13:25 ⇒ 落在 [21:00, 23:45) 之外
    assert _between("2026-11-12T21:25:00+08:00", "21:00", "23:45") is False
    assert _between("2026-11-12T13:25:00+00:00", "21:00", "23:45") is False
    # UTC 21:25 才是窗口内
    assert _between("2026-11-12T21:25:00+00:00", "21:00", "23:45") is True


# ── 白名单与参数校验 ─────────────────────────────────────────────────


def test_unknown_function_rejected():
    r = _resolve_factory({})
    with pytest.raises(ExprError, match="未知函数"):
        evaluate({"op": "truthy", "value": _call("eval", {"const": "1+1"})}, r)
    with pytest.raises(ExprError, match="未知函数"):
        check_expr({"op": "truthy", "value": _call("os.system", {"const": "ls"})})


def test_wrong_arity_rejected():
    r = _resolve_factory({})
    with pytest.raises(ExprError, match="参数个数"):
        evaluate({"op": "truthy", "value": _call("clamp", {"const": 5})}, r)
    with pytest.raises(ExprError, match="参数个数"):
        check_expr({"op": "truthy", "value": _call("time_between", {"const": "2026-01-01T00:00:00+00:00"})})


def test_type_errors_are_explicit():
    r = _resolve_factory({})
    with pytest.raises(ExprError, match="需要数字"):
        evaluate(_eq(_call("abs", {"const": "abc"}), 0), r)
    with pytest.raises(ExprError, match="无法解析时间戳"):
        evaluate(_eq(_call("time_hour", {"const": "not-a-time"}), 0), r)


# ── 资源上限（防 DoS）────────────────────────────────────────────────


def _nested_not(depth: int) -> dict:
    expr: dict = {"op": "truthy", "value": {"const": True}}
    for _ in range(depth):
        expr = {"op": "not", "args": [expr]}
    return expr


def test_depth_limit():
    deep = _nested_not(MAX_EXPR_DEPTH + 10)
    with pytest.raises(ExprError, match="深度"):
        evaluate(deep, _resolve_factory({}))
    with pytest.raises(ExprError, match="深度"):
        check_expr(deep)


def test_node_count_limit():
    wide = {"op": "or", "args": [{"op": "truthy", "value": {"const": False}} for _ in range(MAX_EXPR_NODES + 50)]}
    with pytest.raises(ExprError, match="节点数"):
        evaluate(wide, _resolve_factory({}))
    with pytest.raises(ExprError, match="节点数"):
        check_expr(wide)


# ── 引用收集深入 fn 参数 ─────────────────────────────────────────────


def test_collect_refs_traverse_function_args():
    expr = {
        "op": "gt",
        "left": _call("round", {"var": "entity.temp", "type": "numeric"}, {"const": 1}),
        "right": _call("avg", {"const": [1, 2]}),
    }
    assert collect_var_refs(expr) == {"entity.temp"}
    assert collect_entity_refs(expr) == {"temp"}


# ── Schema / 扫描器 / 运行时端到端 ───────────────────────────────────


def _ir_with_expr(expr: dict, auto_id: str = "demo") -> dict:
    return {
        "ir_version": "0.2.1",
        "id": auto_id,
        "name": "表达力",
        "version": 1,
        "mode": "single",
        "nodes": [
            {"id": "a1", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
            {"id": "c1", "kind": "if", "expr": expr},
            {"id": "d1", "kind": "do", "adapter": "mock", "action": "light.turn_on", "params": {"entity_id": "light.x"}},
            {"id": "p_yes", "kind": "pass"},
            {"id": "p_no", "kind": "pass"},
        ],
        "edges": [
            {"from": "a1", "to": "c1", "kind": "then"},
            {"from": "c1", "to": "d1", "kind": "then"},  # if 真分支走 then
            {"from": "d1", "to": "p_yes", "kind": "then"},
            {"from": "c1", "to": "p_no", "kind": "no"},
        ],
    }


def test_schema_accepts_fn_operand():
    graph = load_graph(_ir_with_expr(_eq(_call("round", {"var": "entity.temp", "type": "numeric"}), 26.0)))
    assert list(graph)[0].id == "demo"

    with pytest.raises(IRValidationError):
        # operand 不允许未知字段（additionalProperties=false 仍然生效）
        load_graph(
            _ir_with_expr(
                {"op": "truthy", "value": {"fn": "round", "args": [{"const": 1}], "evil": True}}
            )
        )


def test_scanner_flags_invalid_expression_and_accepts_valid():
    bad = Graph([load_automation(_ir_with_expr({"op": "truthy", "value": _call("nope", {"const": 1})}))])
    scan = StaticScanner(bad).scan()
    assert not scan.ok
    assert "EXPR_INVALID" in scan.codes()

    good = Graph(
        [load_automation(_ir_with_expr(_eq(_call("round", {"var": "entity.temp", "type": "numeric"}), 26.0)))]
    )
    assert StaticScanner(good).scan().ok


def _run_ir(expr: dict, temp: str):
    rt = build_runtime(
        Graph([load_automation(_ir_with_expr(expr))]),
        states=InMemoryStateProvider(states={"temp": temp, "binary_sensor.m": "off"}),
    )
    instances = rt.emit("binary_sensor.m", "on", last_changed="t1")
    rt.tick()
    return instances[0]


def test_runtime_evaluates_fn_expression_end_to_end():
    expr = {"op": "gt", "left": _call("round", {"var": "entity.temp", "type": "numeric"}), "right": {"const": 25}}
    # round(25.6)=26 > 25 → 真分支，执行 do 到达 p_yes
    yes = _run_ir(expr, "25.6")
    assert yes.state == "done" and yes.ctx.current_node == "p_yes"
    # round(25.3)=25 不 > 25 → no 分支
    no = _run_ir(expr, "25.3")
    assert no.state == "done" and no.ctx.current_node == "p_no"


def test_runtime_fn_true_branch_executes_action():
    expr = {"op": "gt", "left": _call("round", {"var": "entity.temp", "type": "numeric"}), "right": {"const": 25}}
    rt = build_runtime(
        Graph([load_automation(_ir_with_expr(expr))]),
        states=InMemoryStateProvider(states={"temp": "25.6", "binary_sensor.m": "off"}),
    )
    rt.emit("binary_sensor.m", "on", last_changed="t1")
    rt.tick()
    mock = rt.adapters.get("mock")
    assert ("light.turn_on", {"entity_id": "light.x"}) in mock.calls
