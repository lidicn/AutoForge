"""v1.2.0 后置条件断言求值（`expect`）—— 让 `forge sim` 能回答「跑**对**了吗」。

**为什么需要它**：在 v1.2.0 之前，`forge sim` 只返回 `final_states`——**跑完了，但没人说「应该是什么」**。
于是 `forge build` 拦住了「不安全」，`forge sim` **没拦住「逻辑对但语义错」**。

本模块把 IR 顶层的 `expect` 逐条求值，产出可机读也可人读的三态结果：

| 状态 | 含义 |
|---|---|
| `pass` | 断言成立 |
| `fail` | 断言不成立（**自动化没跑对**） |
| `unverified` | **无法验证**（实体不在状态源 / 变量没被任何实例写过 / 无法比较） |

> **`unverified` 必须与 `pass` 区分开**——把「没验到」当「验过了」，断言就成了自欺。
> 这与 `af_vhass` 对「未建模服务」的处理是同一条诚实性主线（不伪造、只如实降级）。

`expect` 两种形态：
- **实体形态** `{"entity_id": "light.study_main", "state": "on"}`（`state` 可为候选数组，命中任一即过）；
- **变量形态** `{"var": "turn_on_result.success", "op": "eq", "value": true}`（`op` 缺省 `eq`）。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

__all__ = ["ExpectReport", "evaluate_expects", "evaluate_graph_expects", "resolve_var"]

#: 哨兵：变量不存在（与「变量存在但值为 None」区分）
MISSING: Any = object()

#: 变量形态支持的前缀（与 `af_ir.expr` 的命名空间一致）
_VAR_PREFIXES = ("vars.", "context.", "entity.")


def resolve_var(name: str, namespace: Mapping[str, Any]) -> Any:
    """按点号路径解析变量：`turn_on_result.success` / `vars.flag`。不存在返回 `MISSING`。"""
    path = str(name or "")
    for prefix in _VAR_PREFIXES:
        if path.startswith(prefix):
            path = path[len(prefix) :]
            break
    current: Any = namespace
    for part in path.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        else:
            return MISSING
    return current


def _apply_op(op: str, actual: Any, expected: Any) -> bool | None:
    """比较运算。返回 `None` 表示「无法比较」（→ unverified，而非判失败）。"""
    if op == "eq":
        if isinstance(actual, str) or isinstance(expected, str):
            return str(actual) == str(expected)
        return actual == expected
    if op == "ne":
        inner = _apply_op("eq", actual, expected)
        return None if inner is None else (not inner)
    try:
        left, right = float(actual), float(expected)
    except (TypeError, ValueError):
        return None
    if op == "lt":
        return left < right
    if op == "lte":
        return left <= right
    if op == "gt":
        return left > right
    if op == "gte":
        return left >= right
    return None


def _state_matches(actual: Any, expected: Any) -> bool:
    """实体形态：`state` 可为字符串或候选数组（命中任一即通过）。"""
    if isinstance(expected, (list, tuple)):
        return any(str(actual) == str(x) for x in expected)
    return str(actual) == str(expected)


class ExpectReport(dict):
    """断言报告：可直接 JSON 化的 dict（`ok` / `checked` / `passed` / `failed` / `unverified` / `items`）。"""


def evaluate_expects(
    auto: Any,
    states: Any,
    var_sources: Sequence[Mapping[str, Any]] | None = None,
) -> ExpectReport:
    """对单条自动化的 `expect` 逐条求值。

    - `states`：状态源（实现 `.get(entity_id) -> str | None`，与 `af_service.simulate` 同源）。
    - `var_sources`：若干「变量命名空间」（通常每个实例一份 `ctx.vars`）；
      变量形态断言**在任一命名空间中成立即通过**。为空表示没有活跃实例 → `unverified`。
    """
    namespaces = list(var_sources or ())
    items: list[dict[str, Any]] = []
    passed = failed = unverified = 0

    for index, raw in enumerate(auto.expects()):
        item: dict[str, Any] = {"index": index, "note": raw.get("note", "")}
        if raw.get("entity_id"):
            entity_id = str(raw["entity_id"])
            expected = raw.get("state")
            actual = states.get(entity_id) if hasattr(states, "get") else None
            item.update({"kind": "entity", "target": entity_id, "expected": expected, "actual": actual})
            if actual is None:
                item.update(
                    status="unverified",
                    reason="实体不在状态源中（未被播种或不可达），本次仿真无法验证该断言",
                )
                unverified += 1
            elif _state_matches(actual, expected):
                item["status"] = "pass"
                passed += 1
            else:
                item["status"] = "fail"
                item["reason"] = f"期望 {expected!r}，实际 {actual!r}"
                failed += 1
        else:
            var_name = str(raw.get("var", ""))
            op = str(raw.get("op") or "eq")
            expected = raw.get("value")
            values = [resolve_var(var_name, ns) for ns in namespaces]
            present = [v for v in values if v is not MISSING]
            item.update({"kind": "var", "target": var_name, "op": op, "expected": expected})

            if not namespaces:
                item.update(status="unverified", reason="没有活跃实例（仿真未产生任何实例），无法验证变量断言")
                unverified += 1
                continue
            if not present:
                item.update(status="unverified", reason=f"没有任何实例写过变量 {var_name!r}")
                unverified += 1
                continue

            outcomes = [_apply_op(op, v, expected) for v in present]
            item["actual"] = values[0] if len(values) == 1 else present
            if any(o is True for o in outcomes):
                item["status"] = "pass"
                passed += 1
            elif all(o is None for o in outcomes):
                item.update(status="unverified", reason="无法比较（类型不可比）")
                unverified += 1
            else:
                item["status"] = "fail"
                item["reason"] = f"期望 {op} {expected!r}，实际 {present!r}"
                failed += 1

        items.append(item)

    return ExpectReport(
        ok=failed == 0,
        #: `fully_verified` 比 `ok` 更严：声明过断言、且**全部验过**（无 fail 且无 unverified）。
        #: `ok=True` 只代表「没抓到反例」——把「没验到」当「验过了」就是自欺（对齐 autoflow 的 fully_verified 语义）。
        fully_verified=bool(items) and failed == 0 and unverified == 0,
        declared=len(items),
        checked=passed + failed,
        passed=passed,
        failed=failed,
        unverified=unverified,
        items=items,
    )


def evaluate_graph_expects(
    graph: Any,
    states: Any,
    var_sources: Sequence[Mapping[str, Any]] | None = None,
    unmodeled_actions: Iterable[str] | None = None,
) -> dict[str, Any]:
    """对整个 Graph 求值：按自动化聚合。**未声明 `expect` 的自动化不进入结果**。

    `unmodeled_actions`：仿真底座未建模的动作（如 vhass/FakeHA 不认识的服务）——
    有则 `fully_verified=False`（那些动作的后果根本没被验证过）。
    """
    per_auto: dict[str, Any] = {}
    total = {"declared": 0, "passed": 0, "failed": 0, "unverified": 0}
    for auto in graph:
        if not auto.expects():
            continue
        report = evaluate_expects(auto, states, var_sources)
        per_auto[auto.id] = report
        for key in total:
            total[key] += report.get(key, 0)
    unmodeled = sorted(set(unmodeled_actions or ()))
    return {
        "ok": total["failed"] == 0,
        "fully_verified": (
            total["declared"] > 0
            and total["failed"] == 0
            and total["unverified"] == 0
            and not unmodeled
        ),
        "declared": total["declared"],
        "passed": total["passed"],
        "failed": total["failed"],
        "unverified": total["unverified"],
        "unmodeled_actions": unmodeled,
        "automations": per_auto,
    }
