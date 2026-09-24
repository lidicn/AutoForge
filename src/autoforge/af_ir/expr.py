"""内置表达式求值（IR §12.7 语言阶梯；v1.0.0 表达力收口）。

设计要点：
- **数值必须显式 typed**（IR §14-10）：HA 的实体状态一律是字符串，若拿 `"27.4"` 直接和
  数字 `27` 比较，要么静默出错要么全军覆没（AutoFlow v1 血泪 F-R6.5）。
  因此 operand 必须声明 `"type": "numeric"`，否则**报错而不是隐式转换**。
- 表达式是**纯函数**：无副作用、不读时钟、不调适配器，可在编译期与仿真期重复求值。
  时间函数（`time_*`）作用于**显式传入的 ISO 时间戳**（如 `vars.last_motion`、快照的
  `last_changed`），"何时算"由调用方注入，表达式本身不取墙钟——红线不破。
- **函数白名单**（v1.0.0）：operand 第三形态 `{"fn": "<名>", "args": [...]}`，只能调用
  `FUNCTIONS` 注册表内的纯函数（math / string / time / list 四族），不支持任意代码。
- **资源上限**（防 DoS）：求值深度 `MAX_EXPR_DEPTH`、单次求值节点数 `MAX_EXPR_NODES`
  双硬顶，超限即 `ExprError`——编译期 `check_expr` 与运行期 `evaluate` 同一套上限。
"""

from __future__ import annotations

from datetime import datetime

from ..af_state import UnknownEntity
from typing import Any, Callable, Mapping

__all__ = [
    "ExprError",
    "ON_STATES",
    "OFF_STATES",
    "MAX_EXPR_DEPTH",
    "MAX_EXPR_NODES",
    "FUNCTIONS",
    "evaluate",
    "check_expr",
    "collect_entity_refs",
    "collect_var_refs",
]

# HA 常见"开/关"字面量（多 domain 归一）
ON_STATES = frozenset({"on", "true", "home", "open", "unlocked", "playing", "cleaning", "above_horizon"})
OFF_STATES = frozenset({"off", "false", "not_home", "closed", "locked", "idle", "paused", "below_horizon"})

_CMP_OPS = ("eq", "ne", "lt", "lte", "gt", "gte")
_UNARY_OPS = ("is_on", "is_off", "is_home", "is_not_home", "truthy", "not_is_on", "not_is_off")
_LOGIC_OPS = ("and", "or", "not")

#: 求值深度上限（嵌套 op / fn 层数）
MAX_EXPR_DEPTH = 32
#: 单次求值节点数上限（op + operand 全部计 1）
MAX_EXPR_NODES = 256

#: resolve(name, declared_type) -> value
Resolver = Callable[[str, str | None], Any]


class ExprError(Exception):
    """表达式求值/校验失败（类型不匹配、缺声明、未知算子/函数、超限等）。"""


# ─────────────────────────────────────────────────────────────────────
# 资源预算（防 DoS：编译期与运行期同一套上限）
# ─────────────────────────────────────────────────────────────────────


class _Budget:
    __slots__ = ("nodes", "depth")

    def __init__(self) -> None:
        self.nodes = 0
        self.depth = 0

    def enter(self, what: str) -> None:
        self.nodes += 1
        self.depth += 1
        if self.depth > MAX_EXPR_DEPTH:
            raise ExprError(f"表达式嵌套深度超过上限 {MAX_EXPR_DEPTH}（{what}）")
        if self.nodes > MAX_EXPR_NODES:
            raise ExprError(f"表达式节点数超过上限 {MAX_EXPR_NODES}（{what}）")

    def leave(self) -> None:
        self.depth -= 1


# ─────────────────────────────────────────────────────────────────────
# 内置函数库（v1.0.0：math / string / time / list，全纯函数）
# ─────────────────────────────────────────────────────────────────────


def _as_num(value: Any, fn: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExprError(f"{fn}() 需要数字，收到 {value!r}（实体状态请声明 type=numeric）")
    return float(value)


def _as_str(value: Any, fn: str) -> str:
    if not isinstance(value, str):
        raise ExprError(f"{fn}() 需要字符串，收到 {value!r}")
    return value


def _as_list(value: Any, fn: str) -> list[Any]:
    if not isinstance(value, (list, tuple)):
        raise ExprError(f"{fn}() 需要数组，收到 {value!r}")
    return list(value)


def _as_iso(value: Any, fn: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ExprError(f"{fn}() 无法解析时间戳 {value!r}（需 ISO 8601）") from exc
    return parsed


def _parse_hhmm(text: Any, fn: str) -> int:
    raw = str(text).strip()
    parts = raw.split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise ExprError(f"{fn}() 时间必须是 HH:MM，收到 {text!r}")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ExprError(f"{fn}() 时间越界：{text!r}")
    return hour * 60 + minute


def _min_max(args: list[Any], fn: str, pick: Callable[[list], Any]) -> Any:
    """min/max：接受多个标量或单个数组。"""
    if len(args) == 1:
        values = _as_list(args[0], fn)
    else:
        values = list(args)
    if not values:
        raise ExprError(f"{fn}() 空序列无最值")
    return pick(values)


def _fn_round(args: list[Any]) -> Any:
    x = _as_num(args[0], "round")
    nd = int(_as_num(args[1], "round")) if len(args) > 1 else 0
    result = round(x, nd)
    return result if nd > 0 else float(result)


def _fn_sum(args: list[Any]) -> Any:
    return float(sum(_as_num(v, "sum") for v in _as_list(args[0], "sum")))


def _fn_avg(args: list[Any]) -> Any:
    values = [_as_num(v, "avg") for v in _as_list(args[0], "avg")]
    if not values:
        raise ExprError("avg() 空数组无均值")
    return sum(values) / len(values)


def _fn_contains(args: list[Any]) -> bool:
    """多态：字符串含子串 / 数组含元素。"""
    hay, needle = args[0], args[1]
    if isinstance(hay, str):
        return str(needle) in hay
    if isinstance(hay, (list, tuple)):
        return needle in list(hay)
    raise ExprError(f"contains() 需要字符串或数组，收到 {hay!r}")


def _fn_time_between(args: list[Any]) -> bool:
    """ISO 时间戳是否落在 [start, end) 窗口内；end<=start 视为跨午夜窗口。"""
    moment = _as_iso(args[0], "time_between")
    start = _parse_hhmm(args[1], "time_between")
    end = _parse_hhmm(args[2], "time_between")
    minutes = moment.hour * 60 + moment.minute
    if start <= end:
        return start <= minutes < end
    return minutes >= start or minutes < end  # 跨午夜（如 22:00-07:00）


#: name -> (min_args, max_args(None=不限), 实现)
FUNCTIONS: dict[str, tuple[int, int | None, Callable[[list[Any]], Any]]] = {
    # math
    "abs": (1, 1, lambda a: abs(_as_num(a[0], "abs"))),
    "floor": (1, 1, lambda a: float(int(_as_num(a[0], "floor") // 1))),
    "ceil": (1, 1, lambda a: float(-int(-_as_num(a[0], "ceil") // 1))),
    "round": (1, 2, _fn_round),
    "clamp": (3, 3, lambda a: min(max(_as_num(a[0], "clamp"), _as_num(a[1], "clamp")), _as_num(a[2], "clamp"))),
    "min": (1, None, lambda a: _min_max(a, "min", min)),
    "max": (1, None, lambda a: _min_max(a, "max", max)),
    "sum": (1, 1, _fn_sum),
    "avg": (1, 1, _fn_avg),
    # string
    "lower": (1, 1, lambda a: _as_str(a[0], "lower").lower()),
    "upper": (1, 1, lambda a: _as_str(a[0], "upper").upper()),
    "trim": (1, 1, lambda a: _as_str(a[0], "trim").strip()),
    "length": (1, 1, lambda a: len(a[0]) if isinstance(a[0], (str, list, tuple)) else _raise(f"length() 需要字符串或数组，收到 {a[0]!r}")),
    "contains": (2, 2, _fn_contains),
    "starts_with": (2, 2, lambda a: _as_str(a[0], "starts_with").startswith(_as_str(a[1], "starts_with"))),
    "ends_with": (2, 2, lambda a: _as_str(a[0], "ends_with").endswith(_as_str(a[1], "ends_with"))),
    "replace": (3, 3, lambda a: _as_str(a[0], "replace").replace(_as_str(a[1], "replace"), _as_str(a[2], "replace"))),
    # time（作用于显式 ISO 时间戳；表达式自身不读时钟）
    "time_hour": (1, 1, lambda a: _as_iso(a[0], "time_hour").hour),
    "time_minute": (1, 1, lambda a: _as_iso(a[0], "time_minute").minute),
    "time_weekday": (1, 1, lambda a: _as_iso(a[0], "time_weekday").weekday()),  # 0=周一 … 6=周日
    "time_between": (3, 3, _fn_time_between),
    # list
    "first": (1, 1, lambda a: (_as_list(a[0], "first") or _raise("first() 空数组"))[0]),
    "last": (1, 1, lambda a: (_as_list(a[0], "last") or _raise("last() 空数组"))[-1]),
}


def _raise(message: str) -> Any:
    raise ExprError(message)


def call_function(name: str, args: list[Any]) -> Any:
    """按白名单调用内置函数（参数个数校验 + 实现）。"""
    spec = FUNCTIONS.get(name)
    if spec is None:
        known = ", ".join(sorted(FUNCTIONS))
        raise ExprError(f"未知函数：{name!r}（可用：{known}）")
    low, high, impl = spec
    if len(args) < low or (high is not None and len(args) > high):
        bound = str(low) if high == low else f"{low}.." + ("∞" if high is None else str(high))
        raise ExprError(f"{name}() 参数个数应为 {bound}，收到 {len(args)} 个")
    return impl(args)


# ─────────────────────────────────────────────────────────────────────
# 引用收集（静态扫描 / 快照预取用）
# ─────────────────────────────────────────────────────────────────────


def _walk(node: Mapping[str, Any]):
    """遍历表达式树，yield 所有 operand（含 var/const/fn 调用）。"""
    if not isinstance(node, Mapping):
        return
    op = node.get("op")
    if op in _LOGIC_OPS:
        for arg in node.get("args", ()):
            yield from _walk(arg)
    elif op in _CMP_OPS:
        yield from _walk_operand(node.get("left"))
        yield from _walk_operand(node.get("right"))
    elif op in _UNARY_OPS:
        yield from _walk_operand(node.get("value"))


def _walk_operand(operand: Any):
    """遍历 operand（fn 形态要深入 args，函数参数里的 var 引用不漏）。"""
    if not isinstance(operand, Mapping):
        return
    yield operand
    if "fn" in operand:
        for arg in operand.get("args", ()):
            yield from _walk_operand(arg)


def collect_var_refs(expr: Mapping[str, Any]) -> set[str]:
    """收集表达式引用的全部变量名（含 `entity.` / `vars.` / `context.` 前缀）。"""
    return {op["var"] for op in _walk(expr) if isinstance(op, Mapping) and "var" in op}


def collect_entity_refs(expr: Mapping[str, Any]) -> set[str]:
    """收集表达式引用的实体（`entity.<entity_id>` → `<entity_id>`）。

    v1.7.1：支持 `entity.<entity_id>.<attr>` 形态（如
    `entity.climate.study_ac.temperature`）——**返回的必须是真实实体**
    `climate.study_ac`，而不是整条带属性的路径：
    读集决定状态源去读哪些实体，若把属性路径当实体名（HA 里不存在这个实体），
    就会读到默认值/404，属性比较永远失败（NL 实测 #3 的 `entity_drift` 真因之一）。
    判据：HA entity_id 形如 `domain.object_id` 只含 1 个点，多余的点是属性名。
    """
    out: set[str] = set()
    for name in collect_var_refs(expr):
        if not name.startswith("entity."):
            continue
        path = name[len("entity.") :]
        parts = path.split(".")
        if len(parts) > 2:  # domain.object_id.attr... → 取真实实体
            path = ".".join(parts[:2])
        out.add(path)
    return out


# ─────────────────────────────────────────────────────────────────────
# 编译期校验（不解析变量值：形态 / 函数白名单 / 参数个数 / 上限）
# ─────────────────────────────────────────────────────────────────────


def check_expr(expr: Mapping[str, Any]) -> None:
    """静态校验表达式；失败抛 `ExprError`（供安全闸在编译期拦截）。"""
    budget = _Budget()
    _check_expr(expr, budget)


def _check_expr(node: Any, budget: _Budget) -> None:
    if not isinstance(node, Mapping):
        raise ExprError(f"表达式必须是对象，收到 {type(node).__name__}")
    budget.enter("op")
    try:
        op = node.get("op")
        if op in _LOGIC_OPS:
            args = node.get("args", ())
            if op == "not" and len(args) != 1:
                raise ExprError(f"`not` 只能有 1 个参数，收到 {len(args)} 个")
            for arg in args:
                _check_expr(arg, budget)
        elif op in _CMP_OPS:
            _check_operand(node.get("left"), budget)
            _check_operand(node.get("right"), budget)
        elif op in _UNARY_OPS:
            _check_operand(node.get("value"), budget)
        else:
            raise ExprError(f"未知算子：{op!r}")
    finally:
        budget.leave()


def _check_operand(operand: Any, budget: _Budget) -> None:
    if not isinstance(operand, Mapping):
        raise ExprError(f"非法 operand：{operand!r}")
    budget.enter("operand")
    try:
        if "const" in operand:
            return
        if "var" in operand:
            if operand.get("type") not in (None, "numeric", "boolean", "string", "enum"):
                raise ExprError(f"未知 operand 类型声明：{operand.get('type')!r}")
            return
        if "fn" in operand:
            name = str(operand.get("fn", ""))
            spec = FUNCTIONS.get(name)
            if spec is None:
                known = ", ".join(sorted(FUNCTIONS))
                raise ExprError(f"未知函数：{name!r}（可用：{known}）")
            low, high, _impl = spec
            args = operand.get("args", [])
            if len(args) < low or (high is not None and len(args) > high):
                bound = str(low) if high == low else f"{low}.." + ("∞" if high is None else str(high))
                raise ExprError(f"{name}() 参数个数应为 {bound}，收到 {len(args)} 个")
            for arg in args:
                _check_operand(arg, budget)
            return
        raise ExprError(f"非法 operand：{operand!r}")
    finally:
        budget.leave()


# ─────────────────────────────────────────────────────────────────────
# 求值
# ─────────────────────────────────────────────────────────────────────


def _coerce(value: Any, declared: str | None, ref: str) -> Any:
    """按声明类型显式转换。**未声明时不猜**——字符串就是字符串。"""
    if declared == "numeric":
        if isinstance(value, bool):
            raise ExprError(f"`{ref}` 声明为 numeric，但拿到布尔值 {value!r}")
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ExprError(f"`{ref}` 声明为 numeric，但值 {value!r} 无法转成数字") from exc
    if declared == "boolean":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in ON_STATES:
            return True
        if text in OFF_STATES:
            return False
        raise ExprError(f"`{ref}` 声明为 boolean，但值 {value!r} 无法解析为布尔")
    if declared == "string":
        return str(value)
    if declared == "enum":
        return str(value)
    # 未声明类型：按 Python 原生类型推断，字符串保持字符串（禁止隐式转数字）
    return value


def _operand_value(operand: Any, resolve: Resolver, budget: _Budget) -> Any:
    if not isinstance(operand, Mapping):
        raise ExprError(f"非法 operand：{operand!r}")
    budget.enter("operand")
    try:
        if "const" in operand:
            return operand["const"]
        if "var" in operand:
            name = operand["var"]
            return _coerce(resolve(name, operand.get("type")), operand.get("type"), name)
        if "fn" in operand:
            args = [_operand_value(arg, resolve, budget) for arg in operand.get("args", [])]
            return call_function(str(operand["fn"]), args)
        raise ExprError(f"非法 operand：{operand!r}")
    finally:
        budget.leave()


def _compare(op: str, left: Any, right: Any) -> bool:
    if op in ("eq", "ne"):
        # P1-15 修复：eq/ne 加类型守卫，与 lt/gt 一致（防止数值/字符串间漂移）
        # None 可与任何类型比较（HA 状态可能为 None）；同类型可比较；bool 不与 int 混比
        if left is not None and right is not None:
            if isinstance(left, bool) != isinstance(right, bool):
                raise ExprError(f"eq/ne 不能跨 bool 与非 bool 比较：{left!r} vs {right!r}")
            if not isinstance(left, bool) and type(left) is not type(right):
                # 允许 int vs float（同为数值）
                if not (isinstance(left, (int, float)) and isinstance(right, (int, float))):
                    raise ExprError(
                        f"eq/ne 操作数类型不一致（{type(left).__name__} vs {type(right).__name__}）："
                        f"{left!r} vs {right!r}；请检查 operand 是否声明了正确的 type"
                    )
        equal = left == right
        return equal if op == "eq" else not equal
    # 有序比较：两侧必须同为数字，或同为字符串（显式类型系统会挡住大部分误用）
    if isinstance(left, bool) or isinstance(right, bool):
        raise ExprError(f"布尔值不能参与 {op} 比较：{left!r} vs {right!r}")
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        pass
    elif isinstance(left, str) and isinstance(right, str):
        pass
    else:
        raise ExprError(
            f"操作数类型不一致（{type(left).__name__} vs {type(right).__name__}），"
            f"无法比较 {left!r} {op} {right!r}；请检查 operand 是否声明了正确的 type"
        )
    if op == "lt":
        return left < right
    if op == "lte":
        return left <= right
    if op == "gt":
        return left > right
    if op == "gte":
        return left >= right
    raise ExprError(f"未知比较算子：{op}")


def _unary(op: str, value: Any) -> bool:
    if op == "truthy":
        # P1-14 修复：显式 HA 语义真值表，不再用 bool(value)（对 off/unavailable 恒真）
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        if isinstance(value, (int, float)):
            return value != 0
        text = str(value).strip().lower()
        if text in ON_STATES:
            return True
        # OFF_STATES / unknown / unavailable / 空串 全部保守 False（fail-closed）
        return False
    if op in ("not_is_on", "not_is_off"):
        # 实体状态为「开/关」之外的任意值（含 unknown/unavailable）都视为需要动作：
        # not_is_on → 非 on 即动作；not_is_off → 非 off 即动作。
        if isinstance(value, bool):
            return not value if op == "not_is_on" else value
        text = str(value).strip().lower()
        if op == "not_is_on":
            return text not in ON_STATES
        return text not in OFF_STATES
    if isinstance(value, bool):
        return value if op in ("is_on", "is_home") else not value
    text = str(value).strip().lower()
    if op in ("is_on", "is_home"):
        return text in ON_STATES
    # is_off / is_not_home
    return text in OFF_STATES


def evaluate(
    expr: Mapping[str, Any], resolve: Resolver, _budget: _Budget | None = None
) -> bool:
    """求值表达式，返回布尔。

    `resolve(name, declared_type)` 由调用方提供：
    运行时传快照 + vars，仿真传 vhass 状态，编译期可传桩做可达性分析。
    深度 / 节点数超 `MAX_EXPR_DEPTH` / `MAX_EXPR_NODES` 即 `ExprError`（防 DoS）。
    """
    budget = _budget or _Budget()
    if not isinstance(expr, Mapping):
        raise ExprError(f"表达式必须是对象，收到 {type(expr).__name__}")
    budget.enter("op")
    try:
        op = expr.get("op")
        if op in _LOGIC_OPS:
            args = expr.get("args", ())
            if op == "and":
                return all(evaluate(a, resolve, budget) for a in args)
            if op == "or":
                return any(evaluate(a, resolve, budget) for a in args)
            if len(args) != 1:
                raise ExprError(f"`not` 只能有 1 个参数，收到 {len(args)} 个")
            return not evaluate(args[0], resolve, budget)
        if op in _CMP_OPS:
            left = _operand_value(expr["left"], resolve, budget)
            right = _operand_value(expr["right"], resolve, budget)
            return _compare(op, left, right)
        if op in _UNARY_OPS:
            if op in ("not_is_on", "not_is_off"):
                # P1-16 修复：实体未知（UnknownEntity）时保守返回 False（fail-closed），
                # 避免把「实体掉线/名字写错」当成「条件成立」触发不该动的写。
                try:
                    value = _operand_value(expr["value"], resolve, budget)
                except UnknownEntity:
                    return False
                return _unary(op, value)
            return _unary(op, _operand_value(expr["value"], resolve, budget))
        raise ExprError(f"未知算子：{op!r}")
    finally:
        budget.leave()
