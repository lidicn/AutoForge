"""F6（第十七轮首次自动实测确证）：trigger group 嵌套的**每一根递归腿**都必修到预算上。

第十七轮的实测形状：
- 自引用 Trigger ⇒ `af_fidelity._trigger_to_dict` / `af_nl._trigger_text` **RecursionError**；
- 深度阶梯 50 / 200 通过，**1000 / 3000 崩**；
- `af_scanner._triggers_node` 运行期路径实测崩溃；
- `af_scheduler._satisfied` 是**每个事件都走一次**的那条腿，暴露面比保存时校验更宽。

同一份 F6 早先只修在 `entity_ids` / `leaf_triggers` 两条腿上，而且是**手抄的字面量 32**、
零测试。那一族"预算只装在一根腿"的缺口正是本轮要钉死的：判据不是"某个走者会拦"，
而是"每一个走者都引同一个真值源"。

反例族（铁律 #8）：CONTROL（浅嵌套逐个走者结果照常正确）、边界（== 预算通过、+1 拒）、
六根腿各自超限都抛具名异常而**不是** RecursionError、反 Hollow 自证（把守卫摘掉一根腿，
那一条腿确实变回 RecursionError）、单一真值源（预算常数只定义一次、消费者引而不抄）。
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from autoforge.af_fidelity import _trigger_to_dict
from autoforge.af_ir import Graph
from autoforge.af_ir.models import (
    MAX_TRIGGER_DEPTH,
    Trigger,
    TriggerDepthError,
    check_trigger_depth,
)
from autoforge.af_nl import _trigger_text
from autoforge.af_scheduler import Scheduler
from autoforge.af_scanner import ScanResult, StaticScanner

LEAF_ENTITY = "binary_sensor.motion"
LEAF_TEXT = f"{LEAF_ENTITY} 变为「on」"


def _nested_group(depth: int) -> Trigger:
    """构造 `depth` 层 group 嵌套，最里层是一个 state 叶子。"""
    node = Trigger(type="state", entity_id=LEAF_ENTITY, to="on")
    for _ in range(depth):
        node = Trigger(type="group", op="or", sources=(node,))
    return node


def _cyclic_trigger() -> Trigger:
    """自引用 group（frozen dataclass，只能绕过去 setattr 造现场）。

    这正是第十七轮探针构造不出、只能靠 `_NestStandin` 降级替身去触发的那个形状。
    """
    node = Trigger(type="group", op="or")
    object.__setattr__(node, "sources", (node,))
    return node


def _nested_dict(depth: int) -> dict:
    data: dict = {"type": "state", "entity_id": LEAF_ENTITY, "to": "on"}
    for _ in range(depth):
        data = {"type": "group", "op": "or", "sources": [data]}
    return data


# ── 0. 真值源：预算只有一个定义点，消费者不许手抄数字 ────────────────────────


def test_budget_is_a_single_source_of_truth():
    from pathlib import Path

    import autoforge.af_fidelity as m_fid
    import autoforge.af_nl as m_nl
    import autoforge.af_scheduler as m_sched
    import autoforge.af_scanner as m_scan
    import autoforge.af_ir.models as m_models

    src = Path(m_models.__file__).read_text(encoding="utf-8")
    assert src.count("MAX_TRIGGER_DEPTH = ") == 1, "预算定义只许一处"

    for mod in (m_fid, m_nl, m_sched, m_scan):
        text = Path(mod.__file__).read_text(encoding="utf-8")
        assert "check_trigger_depth" in text, f"{mod.__name__} 必须走同一根守卫"
        # 手抄字面量的形状（`> 32`）在 trigger 走者上不允许复活
        assert "> 32" not in text, f"{mod.__name__} 手抄了 trigger 深度字面量"


def test_budget_matches_the_condition_side_and_stays_a_valueerror():
    """口径与 `expr.MAX_EXPR_DEPTH` 对齐；基类仍是 ValueError（换具名异常不许漏接旧兜底）。"""
    from autoforge.af_ir.expr import MAX_EXPR_DEPTH

    assert MAX_TRIGGER_DEPTH == MAX_EXPR_DEPTH == 32
    assert issubclass(TriggerDepthError, ValueError)
    with pytest.raises(ValueError):
        check_trigger_depth(MAX_TRIGGER_DEPTH + 1, "legacy")


# ── 1. CONTROL：浅嵌套逐个走者结果照常正确（这条必须"什么都不改"也绿）────────


def test_control_shallow_group_results_are_correct_on_every_walker():
    trig = _nested_group(5)
    assert trig.entity_ids() == {LEAF_ENTITY}
    assert [t.entity_id for t in trig.leaf_triggers()] == [LEAF_ENTITY]
    projected = _trigger_to_dict(trig)
    assert projected["type"] == "group" and projected["op"] == "or"
    assert _trigger_text(trig) == LEAF_TEXT
    assert Trigger.from_dict(_nested_dict(5)).entity_ids() == {LEAF_ENTITY}

    sched = _scheduler()
    assert sched._satisfied(trig, _event(LEAF_ENTITY)) is True


def _scheduler() -> Scheduler:
    return Scheduler(
        graph=Graph([]),
        instances=SimpleNamespace(),
        executor=SimpleNamespace(),
        states=SimpleNamespace(snapshot=lambda ids: {i: "on" for i in ids}),
    )


def _event(entity_id: str) -> SimpleNamespace:
    return SimpleNamespace(entity_id=entity_id, state="on", payload={})


def test_control_budget_boundary_is_exactly_the_numbered_depth():
    """`== MAX_TRIGGER_DEPTH` 通过、`+1` 拒绝：预算含义必须是它说的那个数，不能差一。"""
    ok = _nested_group(MAX_TRIGGER_DEPTH)
    assert ok.entity_ids() == {LEAF_ENTITY}
    assert _trigger_text(ok) == LEAF_TEXT
    assert Trigger.from_dict(_nested_dict(MAX_TRIGGER_DEPTH)).entity_ids() == {LEAF_ENTITY}

    over = _nested_group(MAX_TRIGGER_DEPTH + 1)
    with pytest.raises(TriggerDepthError):
        over.entity_ids()
    with pytest.raises(TriggerDepthError):
        _trigger_text(over)
    with pytest.raises(TriggerDepthError):
        Trigger.from_dict(_nested_dict(MAX_TRIGGER_DEPTH + 1))


# ── 2. 六根腿：超限都必须抛具名异常，而不是把 RecursionError 漏出去 ──────────


def _legs(trig: Trigger) -> dict:
    sched = _scheduler()
    scanner = StaticScanner(Graph([]), trigger_stale_after_s=3600)
    cyclic_auto = SimpleNamespace(entry_nodes=lambda: [SimpleNamespace(trigger=trig)])
    return {
        "entity_ids": lambda: trig.entity_ids(),
        "leaf_triggers": lambda: list(trig.leaf_triggers()),
        "trigger_to_dict": lambda: _trigger_to_dict(trig),
        "trigger_text": lambda: _trigger_text(trig),
        "scheduler._satisfied": lambda: sched._satisfied(trig, _event(LEAF_ENTITY)),
        "scanner._check_trigger_stale": lambda: scanner._check_trigger_stale(cyclic_auto, ScanResult()),
    }


@pytest.mark.parametrize("leg_name", sorted(_legs(_nested_group(3))))
def test_deep_group_is_refused_by_every_walker_not_recursion_crash(leg_name):
    """第十八轮之前这些腿的名字与 `unavailable` 长得很像：能跑 ≠ 跑得完。"""
    trig = _nested_group(MAX_TRIGGER_DEPTH + 68)  # 覆盖审计里 depth=1000 的那一档形状
    with pytest.raises(TriggerDepthError):
        _legs(trig)[leg_name]()


def test_self_referential_trigger_is_refused_by_every_walker():
    trig = _cyclic_trigger()
    for name, leg in sorted(_legs(trig).items()):
        old_limit = sys.getrecursionlimit()
        try:
            with pytest.raises(TriggerDepthError):
                leg()
        except RecursionError:  # pragma: no cover - 守卫失效时的现场
            pytest.fail(f"{name} 仍会 RecursionError（预算没装在这根腿上）")
        finally:
            sys.setrecursionlimit(old_limit)


def test_from_dict_boundary_refuses_before_the_object_tree_exists():
    """反序列化边界是第一站：深 dict 在这里就该拒，不能让每根下游腿各崩一次。"""
    with pytest.raises(TriggerDepthError):
        Trigger.from_dict(_nested_dict(MAX_TRIGGER_DEPTH + 5))

    cyclic: dict = {"type": "group", "op": "or", "sources": []}
    cyclic["sources"].append(cyclic)
    with pytest.raises(TriggerDepthError):
        Trigger.from_dict(cyclic)


# ── 3. 反 Hollow 自证：摘掉守卫，那条腿必须真的变回崩（否则上面的判据是空的）──


def test_guard_removal_reverts_the_leg_to_recursion_crash(monkeypatch):
    """把 `check_trigger_depth` 换成空操作 ⇒ 自引用必须重新 RecursionError。

    没有这一条，"每个走者都拒了"可能只是因为替身太保守、根本没进递归（第十七轮记过第四次
    踩同一类坑：替身过于保守 ⇒ 被测代码根本不进递归 ⇒ 判 safe）。
    """
    import autoforge.af_fidelity as m_fid

    monkeypatch.setattr(m_fid, "check_trigger_depth", lambda depth, where: None)
    sys.setrecursionlimit(400)
    try:
        with pytest.raises(RecursionError):
            _trigger_to_dict(_cyclic_trigger())
    finally:
        sys.setrecursionlimit(1000)
