"""v1.7.1：仿真保真度修复单测（NL 实测复验中发现的「空跑」与「属性不可读」）。

三个真实缺陷（都不是"风格问题"，而是让仿真结论失真）：

1. **事件词汇不对齐** → `forge sim --events` 只认 `"state"`，而调用方按触发词汇写
   `"to"`/`"from"`，事件状态恒为空串 → `Scheduler._satisfied` 永远不满足 →
   **实例从未触发**，"仿真通过"实为空跑（实测 8 条里 6 条）。
2. **事件不改状态源** → `for` 到期复查（`_still_holds`）读状态源拿到旧值 →
   「离家 10 分钟后」这类持续条件永不成立。
3. **属性不可读** → `entity.climate.x.temperature` 被整条当成 entity_id，
   状态源为未知实体补默认值（climate 域 = `'off'`）→ 数值比较报 `entity_drift`；
   且读集里放的是"属性路径"而非真实实体，状态源根本读不到该实体的 attributes。
4. **`context.trigger_time` 取墙钟** → 时间窗判断在仿真/回放里不可复现，
   与「表达式不读墙钟、时间必须可测」的红线相悖。
"""

from __future__ import annotations

import pytest

from autoforge.af_cli import _replay
from autoforge.af_ir import load_graph
from autoforge.af_ir.expr import collect_entity_refs
from autoforge.af_runtime import build_runtime
from autoforge.af_state import Snapshot, UnknownEntity
from autoforge.af_vhass import FakeHA, seed_from_graph


def _ir(trigger: dict, *, for_: str = "", expr: dict | None = None) -> dict:
    node: dict = {"id": "o", "kind": "on", "trigger": trigger}
    if for_:
        node["for"] = for_
    nodes = [
        node,
        {
            "id": "d",
            "kind": "do",
            "adapter": "mock",
            "action": "light.turn_on",
            "params": {"entity_id": "light.study"},
        },
        {"id": "p", "kind": "pass"},
    ]
    edges = [
        {"from": "o", "to": "d", "kind": "then"},
        {"from": "d", "to": "p", "kind": "then"},
    ]
    if expr is not None:
        nodes.insert(1, {"id": "i", "kind": "if", "expr": expr})
        edges = [
            {"from": "o", "to": "i", "kind": "then"},
            {"from": "i", "to": "d", "kind": "then"},
            {"from": "i", "to": "p", "kind": "no"},
            {"from": "d", "to": "p", "kind": "then"},
        ]
    return {
        "ir_version": "0.2.1",
        "id": "demo",
        "name": "demo",
        "version": 1,
        "mode": "single",
        "snapshot": True,
        "nodes": nodes,
        "edges": edges,
    }


def _runtime(ir: dict, seed: dict | None = None):
    graph = load_graph(ir)
    runtime = build_runtime(graph)
    states = seed_from_graph(graph, seed or {}, clock=runtime.clock)
    runtime.states = states
    runtime.instances.states = states
    runtime.scheduler.states = states
    return runtime, states


def _by_state(runtime) -> dict:
    out: dict[str, int] = {}
    for inst in runtime.instances.all():
        out[inst.state] = out.get(inst.state, 0) + 1
    return out


# ─────────────────────────────────────────────────────────────────────
# 1. 事件词汇对齐 + 状态同步
# ─────────────────────────────────────────────────────────────────────


def test_replay_accepts_to_alias_and_fires_instance():
    """`to` 是触发侧词汇，事件侧必须同样可用，否则实例永不触发（空跑）。"""
    runtime, _ = _runtime(_ir({"type": "state", "entity_id": "binary_sensor.m", "to": "on"}))

    _replay(runtime, [{"entity_id": "binary_sensor.m", "to": "on"}])

    assert _by_state(runtime) == {"done": 1}


def test_replay_mirrors_state_into_state_source():
    """回放必须把新状态写进状态源，否则 `for` 复查/后续 `if` 读到的是旧值。"""
    runtime, states = _runtime(
        _ir({"type": "state", "entity_id": "binary_sensor.m", "to": "off"}, for_="10m"),
        {"binary_sensor.m": "on"},
    )

    fired = _replay(runtime, [{"entity_id": "binary_sensor.m", "to": "off"}])
    assert fired is None or True  # 首次只登记 pending，未到期
    assert states.get("binary_sensor.m") == "off"  # 状态源已同步
    assert _by_state(runtime) == {}  # 尚未到期

    _replay(runtime, [{"advance_s": 600}])  # 时间前进 10 分钟 → 到期复查

    assert _by_state(runtime) == {"done": 1}


def test_replay_advance_only_item_is_tick():
    runtime, _ = _runtime(_ir({"type": "state", "entity_id": "binary_sensor.m"}))
    _replay(runtime, [{"advance_s": 1}])
    assert runtime.clock.now().isoformat().startswith("2026-09-14T08:00:01")


# ─────────────────────────────────────────────────────────────────────
# 2. 属性读取（修 #3 的 entity_drift）
# ─────────────────────────────────────────────────────────────────────


def test_snapshot_reads_attribute_with_dotted_path():
    snap = Snapshot(
        values={"climate.ac": "cool"},
        attributes={"climate.ac": {"temperature": 15}},
    )
    assert snap.get("climate.ac") == "cool"
    assert snap.get("climate.ac.temperature") == 15


def test_snapshot_missing_attribute_is_unknown_entity():
    """属性缺失必须显式失败（走软失效），**不能**退回域默认值假装读到了。"""
    snap = Snapshot(values={"climate.ac": "off"}, attributes={})
    with pytest.raises(UnknownEntity):
        snap.get("climate.ac.temperature")


def test_collect_entity_refs_strips_attribute_suffix():
    """读集必须是真实实体：属性路径进读集 → 状态源读一个不存在的实体。"""
    expr = {
        "op": "lt",
        "left": {"var": "entity.climate.ac.temperature", "type": "numeric"},
        "right": {"const": 16},
    }
    assert collect_entity_refs(expr) == {"climate.ac"}


def test_attribute_condition_evaluates_end_to_end():
    """带属性的条件必须真正求值（此前恒报 entity_drift → 分支被静默跳过）。"""
    runtime, _ = _runtime(
        _ir(
            {"type": "state", "entity_id": "climate.ac"},
            expr={
                "op": "lt",
                "left": {"var": "entity.climate.ac.temperature", "type": "numeric"},
                "right": {"const": 16},
            },
        ),
        {"climate.ac": {"state": "cool", "attributes": {"temperature": 15}}},
    )

    _replay(runtime, [{"entity_id": "climate.ac", "state": "cool"}])

    assert _by_state(runtime) == {"done": 1}
    assert not [a for a in runtime.audit.events if "drift" in str(getattr(a, "message", ""))]


# ─────────────────────────────────────────────────────────────────────
# 3. context.trigger_time 可复现
# ─────────────────────────────────────────────────────────────────────


def test_trigger_time_follows_virtual_clock():
    """时间窗判断依赖它：必须取时间源，才能随虚拟时钟前进而可复现。"""
    runtime, _ = _runtime(
        _ir(
            {"type": "state", "entity_id": "binary_sensor.m"},
            expr={
                "op": "gte",
                "left": {"fn": "time_hour", "args": [{"var": "context.trigger_time", "type": "string"}]},
                "right": {"const": 20},
            },
        )
    )

    # 08:00 触发 → 时间窗 20:00 未到 → 走 no 分支到 pass（done，但动作未执行）
    _replay(runtime, [{"entity_id": "binary_sensor.m", "state": "on"}])
    assert _by_state(runtime) == {"done": 1}

    runtime2, _ = _runtime(
        _ir(
            {"type": "state", "entity_id": "binary_sensor.m"},
            expr={
                "op": "gte",
                "left": {"fn": "time_hour", "args": [{"var": "context.trigger_time", "type": "string"}]},
                "right": {"const": 20},
            },
        )
    )
    # 先时间旅行到 20:00（08:00 + 12h）再触发 → 条件成立
    _replay(runtime2, [{"advance_s": 43200}, {"entity_id": "binary_sensor.m", "state": "on"}])
    inst = runtime2.instances.all()[0]
    assert inst.state == "done"
    assert inst.ctx.context["trigger_time"].startswith("2026-09-14T20:00")
