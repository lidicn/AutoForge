"""F2（第二十轮确证）：condition/参数/group 遍历的**每一根递归腿**都必须接上同一份预算。

判据形状照 `test_ir_trigger_depth_budget.py`（F6 那份），因为坑是同一个：
预算常量一直都在，缺的是"遍历路径没接上"。实测（recursionlimit=1000，Python 3.13）：
`json.loads` 能稳定送达 ≥1000 层嵌套，而这些走者在 496–997 层才 RecursionError
——中间那段是真实可达面，不是理论面。

改造前逐站实测（`af_depth_probe3.py` / `af_draft_expr_probe.py` 的 .out 留档）：
    expr._walk 992 / condition_norm._nnf 496 / _expr_text 996 / _iter_expr_nodes 992
    describe_condition 497 / detectors.expr_nodes 990~1200 之间
    version._jsonable 997 / extract_entity_ids 994 / draft._resolve_expr 997
    nl_parse._build 992 / _assert_no_runtime_fields 994 / _iter_nodes 997 / _expr_atom 995
    orchestrator.iter_strings 993 / walk_dicts 992 / substitute_refs 993 / normalize_var_paths 993
    evo._canon 498

反例族：CONTROL（预算内输出与 HEAD 逐字节相同，另见 golden 档）、边界（== 预算通过、
+1 拒）、每根腿超限都抛**本模块声明的**异常而不是 RecursionError、反 Hollow 自证
（摘掉守卫 ⇒ 自引用容器必须重新崩）、单一真值源（预算只定义一次、消费者引而不抄）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from autoforge import af_conflict_runtime, af_draft, af_evo, af_nl, af_nl_parse, af_orchestrator, af_version
from autoforge.af_closedloop import detectors
from autoforge.af_ir import (
    MAX_EXPR_DEPTH,
    MAX_PARAM_DEPTH,
    Graph,
    assert_param_budget,
    check_expr_depth,
    check_param_depth,
)
from autoforge.af_ir.condition_norm import CNFBudgetExceeded, normalize_condition
from autoforge.af_ir.expr import ExprError, collect_entity_refs, collect_var_refs
from autoforge.af_ir.models import MAX_TRIGGER_DEPTH, ParamDepthError
from autoforge.af_nl_parse import ParseError, _Stmt
from autoforge import af_scanner as af_scan_mod
from autoforge.af_scanner import StaticScanner
from autoforge.af_ir import load_automation

LEAF_EXPR = {"op": "gt", "left": {"var": "entity.a.b", "type": "numeric"}, "right": {"const": 1}}
GROUP_TEXT = "编组 并行"
DO_TEXT = "执行 打开 灯：light.study"


def deep_expr(depth: int) -> dict:
    node = dict(LEAF_EXPR)
    for _ in range(depth):
        node = {"op": "and", "args": [node, dict(LEAF_EXPR)]}
    return node


def draft_expr(depth: int) -> dict:
    """af_draft 吃的是 draft 方言 `{"and": [...]}`，不是 IR 的 `{"op": "and"}`。
    形状喂错的话 `_resolve_expr` 根本不递归——探针就因此给过一次假读数。
    """
    leaf = {"lt": {"var": "temp_living", "const": 20}}
    node = leaf
    for _ in range(depth):
        node = {"and": [node, leaf]}
    return node


def deep_container(levels: int) -> dict:
    """恰好 `levels` 层容器（实测口径：`levels == MAX_PARAM_DEPTH` 放行、`+1` 拒）。

    写这份夹具时先按"wrap 次数"定义过，边界因此差一层——预算的含义必须是它说的那个数。
    """
    node: dict = {"entity_id": "light.a"}
    for _ in range(levels - 1):
        node = {"target": node}
    return node


def leaf_at(index: int) -> dict:
    return {"op": "gt", "left": {"var": f"entity.a{index}", "type": "numeric"}, "right": {"const": 1}}


def deep_distinct(depth: int) -> dict:
    """和 `deep_expr` 同形状，但每片叶子不同——CNF 的结构判据必须用它。

    同一份叶子会被 frozenset 折叠成 1 个子句，拿它验"子句数"会得到假红（本轮真踩过）。
    """
    node = leaf_at(0)
    for i in range(1, depth + 1):
        node = {"op": "and", "args": [node, leaf_at(i)]}
    return node


def cyclic_container() -> dict:
    node: dict = {"entity_id": "light.a"}
    node["self"] = node
    return node


def _silently(call) -> str:
    """把"抛/不抛"读成两个字面档，用来对比两条腿的边界是否同档。"""
    try:
        call()
    except Exception as exc:  # noqa: BLE001 - 这里要的正是"有没有异常"，类型由调用方断言
        return f"拒:{type(exc).__name__}"
    return "放行"


def deep_stmts(depth: int) -> list[_Stmt]:
    stmts = [_Stmt(indent=2 * k, edge=None, text=GROUP_TEXT, line_no=k + 1) for k in range(depth)]
    stmts.append(_Stmt(indent=2 * depth, edge=None, text=DO_TEXT, line_no=depth + 1))
    return stmts


def deep_group_nodes(depth: int) -> list[dict]:
    inner = [{"id": "n0", "kind": "do"}]
    for _ in range(depth):
        inner = [{"id": "g", "kind": "group", "children": [{"nodes": inner}]}]
    return inner


# ── 0. 取样形状自证：喂错形状 ⇒ 读数测的是空气（本轮真踩过） ──────────────────


def test_fixtures_really_reach_the_recursive_branch():
    assert len(collect_var_refs(deep_expr(3))) == 1
    # draft 方言取样必须真的走 and 分支（返回 {"op": "and"}），否则 _resolve_expr 不递归
    assert af_draft._resolve_expr(draft_expr(3), {}, None)["op"] == "and"
    # _build 取样必须真的嵌套：depth=3 ⇒ 3 层 group
    nodes, _edges = af_nl_parse._build(deep_stmts(3))

    def _nest(ns):
        best = 0
        for n in ns or ():
            if n.get("kind") != "group":
                continue
            sub = max((_nest(c.get("nodes")) for c in n.get("children") or ()), default=0)
            best = max(best, 1 + sub)
        return best

    assert _nest(nodes) == 3
    # 否定链取样必须真的产出多层 not
    atom = af_nl_parse._expr_atom("非" * 3 + "entity.temp>20")
    assert atom is not None and atom["op"] == "not"


# ── 1. 真值源：预算只许一处定义，消费者引而不抄 ─────────────────────────────


def test_depth_budgets_have_a_single_definition_point():
    expr_src = Path(af_orchestrator.__file__).parent / "af_ir" / "expr.py"
    models_src = Path(af_orchestrator.__file__).parent / "af_ir" / "models.py"
    assert expr_src.read_text(encoding="utf-8").count("MAX_EXPR_DEPTH = ") == 1
    assert models_src.read_text(encoding="utf-8").count("MAX_PARAM_DEPTH = ") == 1

    for mod in (af_orchestrator, af_nl, af_version, af_conflict_runtime, af_evo, af_draft, af_scan_mod):
        text = Path(mod.__file__).read_text(encoding="utf-8")
        assert "MAX_EXPR_DEPTH = " not in text, f"{mod.__name__} 重新定义了表达式深度预算"
        assert "MAX_PARAM_DEPTH = " not in text, f"{mod.__name__} 重新定义了参数深度预算"

    # 扫描器是**闸门腿**：它只调同一份守卫，不许自己数深度、也不许抄预算数字
    scanner_text = Path(af_scan_mod.__file__).read_text(encoding="utf-8")
    assert "assert_param_budget" in scanner_text, "参数预算闸门没接真值源"
    assert "MAX_PARAM_DEPTH" not in scanner_text, "扫描器抄了参数预算数字"


def test_expr_and_param_budgets_are_the_declared_numbers():
    assert MAX_EXPR_DEPTH == 32 and MAX_PARAM_DEPTH == 64
    assert issubclass(ParamDepthError, ValueError), "参数预算必须留在 ValueError 族里"
    with pytest.raises(ExprError):
        check_expr_depth(MAX_EXPR_DEPTH + 1, "legacy")
    with pytest.raises(ParamDepthError):
        check_param_depth(MAX_PARAM_DEPTH + 1, "legacy")


# ── 2. CONTROL：预算内输出照常（护栏不许静默换语义） ─────────────────────────


def test_control_in_budget_results_are_unchanged():
    shallow = deep_expr(5)
    assert collect_var_refs(shallow) == {"entity.a.b"}
    assert collect_entity_refs(shallow) == {"a.b"}
    assert af_nl._expr_text(shallow)  # 渲染照常有内容
    assert [n.get("op") for n, _p in af_orchestrator._iter_expr_nodes(shallow)]

    cont = {"entity_id": "light.a", "params": {"target": {"entity_id": "climate.b"}}}
    assert af_version._jsonable(cont) == cont
    # `_walk` 只下钻 entity_id/entity_ids/entityId/target/targets 这五个键，
    # "params" 不是其中一根——这是既有口径，护栏不许悄悄改它。
    assert af_conflict_runtime.extract_entity_ids(cont) == ["light.a"]
    assert af_conflict_runtime.extract_entity_ids(
        {"target": {"entity_id": "climate.b"}}
    ) == ["climate.b"]
    assert af_evo._canon(cont)
    assert list(af_orchestrator.iter_strings(cont))

    # CNF 结构判据要用**不同叶子**（同一叶子会被 frozenset 折叠，见 `deep_distinct` 注释）
    cnf = normalize_condition(deep_distinct(5))
    assert len(cnf) == 6 and all(len(c) == 1 for c in cnf), "浅档 CNF 应当仍是 6 个单文字子句"
    assert {t.lstrip("!") for c in cnf for t in c} == {
        json.dumps(leaf_at(i), sort_keys=True, ensure_ascii=False) for i in range(6)
    }

    assert af_draft._resolve_expr(draft_expr(5), {}, None)["op"] == "and"
    assert af_nl_parse._expr_atom("非" * 30 + "entity.temp>20")["op"] == "not"
    assert len(list(af_nl_parse._iter_nodes(deep_group_nodes(5)))) == 6


def test_walk_never_refuses_what_the_compile_gate_accepted():
    """护栏必须不严于闸门：合法存量 IR 不许在读路径被新加的预算拒掉。

    实测（`af_gate_vs_walk2.py`）：`check_expr` 在 and/not 链 depth=31 首拒
    （"超过上限 32（operand）"），`_walk` 在 depth=32 首拒——遍历腿比闸门**宽一档**，
    所以"闸门放行 ⇒ 遍历放行"成立。这条判据把该关系钉住，比手挑一个边界数强。
    """
    from autoforge.af_ir.expr import check_expr

    for depth in range(0, MAX_EXPR_DEPTH + 2):
        for build in (deep_expr, lambda d: {"op": "not", "args": [deep_expr(d)]}):
            tree = build(depth)
            try:
                check_expr(tree)
            except ExprError:
                continue  # 闸门本就拒的形状不归遍历腿管
            collect_var_refs(tree)  # 闸门放行 ⇒ 这里必须不抛
            af_nl._expr_text(tree)
            normalize_condition(tree)


def test_over_budget_is_refused_on_both_families():
    with pytest.raises(ExprError):
        collect_var_refs(deep_expr(MAX_EXPR_DEPTH + 8))
    with pytest.raises(ParamDepthError):
        af_version._jsonable(deep_container(MAX_PARAM_DEPTH + 8))
    # 边界：预算含义就是它说的那个层数——`== MAX_PARAM_DEPTH` 层容器放行、`+1` 拒
    af_version._jsonable(deep_container(MAX_PARAM_DEPTH))
    with pytest.raises(ParamDepthError):
        af_version._jsonable(deep_container(MAX_PARAM_DEPTH + 1))


def test_param_gate_and_param_walkers_refuse_at_the_same_level():
    """闸门（`assert_param_budget`）与遍历腿（`_jsonable`）必须同档，不许闸门更严。

    护栏严于闸门 = 把合法存量 IR 拒在门外（本轮专门量过一次，结论是"遍历腿比编译闸宽一档"
    只发生在表达式侧；参数侧新加的闸门口径必须与遍历腿逐层对齐，所以这里按层实测）。
    """
    from autoforge.af_ir import assert_param_budget

    for levels in (1, 9, MAX_PARAM_DEPTH - 1, MAX_PARAM_DEPTH, MAX_PARAM_DEPTH + 1, MAX_PARAM_DEPTH + 40):
        container = deep_container(levels)
        gate = _silently(lambda: assert_param_budget(container, "test"))
        walk = _silently(lambda: af_version._jsonable(container))
        assert gate == walk, f"容器 {levels} 层：闸门={gate} 遍历腿={walk} 不一致"
    cyclic = _silently(lambda: assert_param_budget(cyclic_container(), "test"))
    assert cyclic.startswith("拒"), "自引用容器必须被闸门拦住"


# ── 3. 每根腿：超限都必须抛本模块声明的异常，而不是把 RecursionError 漏出去 ──

LEGS: dict[str, tuple] = {
    "expr.collect_var_refs": (lambda: collect_var_refs(deep_expr(200)), ExprError),
    "expr.collect_entity_refs": (lambda: collect_entity_refs(deep_expr(200)), ExprError),
    "condition_norm.normalize_condition": (lambda: normalize_condition(deep_expr(200)), CNFBudgetExceeded),
    "af_nl._expr_text": (lambda: af_nl._expr_text(deep_expr(200)), ExprError),
    "orchestrator.describe_condition": (
        lambda: af_orchestrator.describe_condition(deep_expr(200), {"entity.a.b": "温度"}),
        ExprError,
    ),
    "orchestrator._iter_expr_nodes": (lambda: list(af_orchestrator._iter_expr_nodes(deep_expr(200))), ExprError),
    "detectors.expr_nodes": (lambda: list(detectors.expr_nodes(deep_expr(200))), ExprError),
    "draft._resolve_expr": (lambda: af_draft._resolve_expr(draft_expr(200), {}, None), af_draft.DraftError),
    "nl_parse._expr_atom": (lambda: af_nl_parse._expr_atom("非" * 200 + "entity.temp>20"), ParseError),
    "version._jsonable": (lambda: af_version._jsonable(deep_container(200)), ParamDepthError),
    "conflict_runtime.extract_entity_ids": (
        lambda: af_conflict_runtime.extract_entity_ids(deep_container(200)),
        ParamDepthError,
    ),
    "orchestrator.iter_strings": (lambda: list(af_orchestrator.iter_strings(deep_container(200))), ParamDepthError),
    "orchestrator.walk_dicts": (lambda: list(af_orchestrator.walk_dicts(deep_container(200))), ParamDepthError),
    "orchestrator.substitute_refs": (
        lambda: af_orchestrator.substitute_refs(deep_container(200), {"@k": "light.z"}),
        ParamDepthError,
    ),
    "orchestrator.normalize_var_paths": (
        lambda: af_orchestrator.normalize_var_paths(deep_container(200)),
        ParamDepthError,
    ),
    "evo._canon": (lambda: af_evo._canon(deep_container(200)), ParamDepthError),
    "nl_parse._assert_no_runtime_fields": (
        lambda: af_nl_parse._assert_no_runtime_fields(deep_container(200)),
        ParseError,
    ),
    "nl_parse._build": (lambda: af_nl_parse._build(deep_stmts(200)), ParseError),
    "nl_parse._iter_nodes": (lambda: list(af_nl_parse._iter_nodes(deep_group_nodes(200))), ParseError),
    # 闸门侧新加的那根腿（扫描器 `_check_param_budget` 走的就是它）
    "models.assert_param_budget": (
        lambda: assert_param_budget(deep_container(200), "test"),
        ParamDepthError,
    ),
}


@pytest.mark.parametrize("leg_name", sorted(LEGS))
def test_over_depth_is_a_declared_error_not_a_crash(leg_name):
    call, expected = LEGS[leg_name]
    try:
        with pytest.raises(expected):
            call()
    except RecursionError:  # pragma: no cover - 守卫失效时的现场
        pytest.fail(f"{leg_name} 仍会 RecursionError（预算没接上这根腿）")


def test_guarded_walker_inventory_covers_every_measured_site():
    """第二十轮列出的站点一根不许漏：这张表本身就是回归面。"""
    expected_sites = {
        "expr.collect_var_refs", "expr.collect_entity_refs", "condition_norm.normalize_condition",
        "af_nl._expr_text", "orchestrator.describe_condition", "orchestrator._iter_expr_nodes",
        "detectors.expr_nodes", "draft._resolve_expr", "nl_parse._expr_atom", "version._jsonable",
        "conflict_runtime.extract_entity_ids", "orchestrator.iter_strings", "orchestrator.walk_dicts",
        "orchestrator.substitute_refs", "orchestrator.normalize_var_paths", "evo._canon",
        "nl_parse._assert_no_runtime_fields", "nl_parse._build", "nl_parse._iter_nodes",
        "models.assert_param_budget",
    }
    assert set(LEGS) == expected_sites, "腿清单与第二十轮实测站点不一致"


# ── 4. 扫描器：坏 IR 必须落成诊断，`scan()` 不许把具名异常换成整次崩溃 ─────────
#
# F6（第十七轮）的判据是"每根腿抛具名异常而不是 RecursionError"，没盖住的是外面那一层：
# `scan()` 对调用方承诺的是 `ScanResult`。改造前实测（`af_scan_probe_f2.py`）四张形状里
# 有两张直接把整次扫描换成异常——表达式侧 `ExprError（expr._walk）`、触发源侧
# `TriggerDepthError（entity_ids）`，因为 `_check_entities`→`auto.reads()` 会再走一遍递归。

BASE_IR = {
    "ir_version": "0.2.1",
    "id": "depth_probe",
    "name": "深嵌套",
    "version": 1,
    "mode": "single",
    "nodes": [
        {"id": "a1", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
        {"id": "i1", "kind": "if", "expr": dict(LEAF_EXPR)},
        {"id": "d1", "kind": "do", "adapter": "homeassistant", "action": "call_service",
         "params": {"entity_id": "light.typo"}},
        {"id": "p1", "kind": "pass"},
    ],
    "edges": [
        {"from": "a1", "to": "i1", "kind": "then"},
        {"from": "i1", "to": "d1", "kind": "yes"},
        {"from": "d1", "to": "p1", "kind": "then"},
    ],
}
KNOWN = {"binary_sensor.m", "light.a"}  # `light.typo` 故意不在里面：写侧检查必须还能红


def _deep_trigger(depth: int):
    from autoforge.af_ir.models import Trigger

    node = Trigger(type="state", entity_id="binary_sensor.m", to="on")
    for _ in range(depth):
        node = Trigger(type="group", op="or", sources=(node,))
    return node


def _cyclic_trigger():
    from autoforge.af_ir.models import Trigger

    node = Trigger(type="group", op="or")
    object.__setattr__(node, "sources", (node,))
    return node


#: 形状名 → (注入器, 期望诊断码)。注入都在 `load_automation` 之后：闸门本就拒收这些 IR，
#: 这里模拟的是"存量库里的坏 IR 被读出来"（`Node` 是 frozen dataclass，只能绕过去造现场）。
SHAPES = {
    "deep_expr": (lambda a: object.__setattr__(a.nodes["i1"], "expr", deep_expr(MAX_EXPR_DEPTH + 40)), "EXPR_INVALID"),
    "deep_trigger": (
        lambda a: object.__setattr__(a.nodes["a1"], "trigger", _deep_trigger(MAX_TRIGGER_DEPTH + 40)),
        "TRIGGER_INVALID",
    ),
    "cyclic_trigger": (lambda a: object.__setattr__(a.nodes["a1"], "trigger", _cyclic_trigger()), "TRIGGER_INVALID"),
    "deep_params": (
        lambda a: object.__setattr__(a.nodes["d1"], "params", deep_container(MAX_PARAM_DEPTH + 40)),
        "PARAMS_TOO_DEEP",
    ),
    "cyclic_params": (lambda a: object.__setattr__(a.nodes["d1"], "params", cyclic_container()), "PARAMS_TOO_DEEP"),
}


def _scan(mutate=None):
    auto = load_automation(BASE_IR)
    if mutate:
        mutate(auto)
    return StaticScanner(Graph([auto]), known_entities=KNOWN).scan()


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_scanner_lands_a_diagnostic_instead_of_raising(shape):
    mutate, code = SHAPES[shape]
    result = _scan(mutate)
    assert code in result.codes(), f"{shape} 必须落成 {code} 诊断"
    assert not result.ok, f"{shape} 是坏 IR，扫描结果必须红"


def test_control_clean_ir_carries_no_budget_diagnostic():
    """CONTROL：预算内的 IR 一个超限码都不许多报（护栏不许把自己变成噪声源）。"""
    codes = _scan().codes()
    assert {"EXPR_INVALID", "TRIGGER_INVALID", "PARAMS_TOO_DEEP"}.isdisjoint(codes), codes


@pytest.mark.parametrize("shape", ["deep_expr", "deep_trigger", "cyclic_trigger"])
def test_the_same_ir_still_breaks_the_raw_accessor(shape):
    """反 Hollow：同一份 IR 上直接 `auto.reads()` 必须抛。

    没有这条，"scan() 不再抛"可能只是因为取样其实没超预算（本轮已经第四次踩这类坑）。
    容错的是**扫描器**，不是 IR 访问器——访问器照旧拒绝分析不动的东西。
    """
    from autoforge.af_ir.models import TriggerDepthError

    auto = load_automation(BASE_IR)
    SHAPES[shape][0](auto)
    with pytest.raises((ExprError, TriggerDepthError)):
        auto.reads()


def test_write_side_checks_survive_a_refused_read_side():
    """跳过读侧不许把写侧一起问哑：do 节点拼错的实体仍要报 `ENTITY_NOT_FOUND`。"""
    codes = _scan(SHAPES["deep_expr"][0]).codes()
    assert "ENTITY_NOT_FOUND" in codes, "读侧超限不该让写侧检查消失"


# ── 5. 反 Hollow 自证：摘掉守卫，自引用容器必须重新崩 ────────────────────────


def test_removing_the_guard_brings_the_crash_back(monkeypatch):
    """没有这一条，"每根腿都拒了"可能只是因为取样根本没进递归。"""
    cyclic: dict = {}
    cyclic["target"] = cyclic

    monkeypatch.setattr(af_version, "check_param_depth", lambda depth, where: None)
    old = sys.getrecursionlimit()
    sys.setrecursionlimit(400)
    try:
        with pytest.raises(RecursionError):
            af_version._jsonable(cyclic)
    finally:
        sys.setrecursionlimit(old)


def test_condition_norm_refusal_comes_from_the_budget_itself(monkeypatch):
    """把 `condition_norm.MAX_EXPR_DEPTH` 调大 ⇒ 自引用 condition 必须重新 RecursionError。

    否则"CNFBudgetExceeded"可能来自别的原因（例如子句宽度上限先命中），判据就是空的。
    """
    from autoforge.af_ir import condition_norm

    monkeypatch.setattr(condition_norm, "MAX_EXPR_DEPTH", 10**6)
    node: dict = {"op": "and", "args": []}
    node["args"].append(node)
    old = sys.getrecursionlimit()
    sys.setrecursionlimit(400)
    try:
        with pytest.raises(RecursionError):
            condition_norm.normalize_condition(node)
    finally:
        sys.setrecursionlimit(old)


def test_condition_norm_rejects_a_self_referential_expr():
    """内存里的自引用 condition 同样必须被预算拦住（护栏比"改迭代"强的地方）。"""
    node: dict = {"op": "and", "args": []}
    node["args"].append(node)
    with pytest.raises(CNFBudgetExceeded):
        normalize_condition(node)
    with pytest.raises(ExprError):
        collect_var_refs(node)
