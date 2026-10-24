"""G10：canary + duration 的链，观察期超时恢复后下一个节点必须被接上。

缺陷（缺口册 G10）：d3(do, canary.duration) 观察期超时恢复后，执行器按
`{on_timeout, default}` 选边；若 d3→d4 只有 then 边，pick_edge 落空 → _terminate，
链后半段永不执行。

修法（PM 裁决，commit 在 af_draft.py）：af_draft 给带 canary.duration 的 do 节点
回填一条 on_timeout 边指向下一个节点。执行器侧未改。

判据腿：
- 腿1（已翻转正向）：带 duration、图带 on_timeout 边（=draft 修复后输出形状）时，
  advance 过观察期后 d4 真被执行，且 d4 的适配器调用真发生（不只是终态对）。
- 腿3：同图下 d3 的 canary 证据仍落 verified/unmodeled——改边不能让漂移检查被跳过。
- 腿2（对照）：不带 duration 时 then 链行为不变。
- 腿4：af_draft 对带 canary.duration 的 do 节点，确实输出了 on_timeout 边。
"""

from __future__ import annotations

from autoforge.af_instance import DONE, SUSPENDED
from autoforge.af_runtime import build_runtime
from autoforge.af_ir import Graph, load_automation


ON_M = {"id": "a1", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}}


def _d3(canary: dict | None) -> dict:
    node = {
        "id": "d3",
        "kind": "do",
        "adapter": "mock",
        "action": "light.turn_on",
        "params": {"entity_id": "light.a"},
    }
    if canary is not None:
        node["canary"] = canary
    return node


# d4 也是 do（mock 关灯），便于断言它的适配器调用真发生
D4 = {"id": "d4", "kind": "do", "adapter": "mock", "action": "light.turn_off", "params": {"entity_id": "light.a"}}


def _ir(canary: dict | None, *, with_timeout_edge: bool) -> dict:
    edges = [
        {"from": "a1", "to": "d3", "kind": "then"},
        {"from": "d3", "to": "d4", "kind": "then"},
    ]
    if with_timeout_edge:
        # = af_draft 修复后回填的 on_timeout 边
        edges.append({"from": "d3", "to": "d4", "kind": "on_timeout"})
    return {
        "ir_version": "0.2.1",
        "id": "g10",
        "name": "g10",
        "version": 1,
        "mode": "single",
        "nodes": [ON_M, _d3(canary), D4],
        "edges": edges,
    }


def _trace_nodes(instance) -> list[str]:
    return [t["node"] for t in instance.ctx.trace]


def _build(canary: dict | None, *, with_timeout_edge: bool):
    runtime = build_runtime(Graph([load_automation(_ir(canary, with_timeout_edge=with_timeout_edge))]))
    runtime.states.set_state("light.a", "off")  # canary 取 pre_snapshot 需要已知实体状态
    return runtime


def test_canary_with_duration_d4_runs_after_observe_window():
    """腿1（正向，G10 修复后）：观察期超时恢复 → on_timeout 边命中 d4，d4 真执行。"""
    runtime = _build({"duration": "0.2s"}, with_timeout_edge=True)
    runtime.emit("binary_sensor.m", "on")

    inst = runtime.instances.all()[0]
    assert inst.state == SUSPENDED  # d3 已下发并挂起观察
    assert "d3" in _trace_nodes(inst)

    runtime.advance(1.0)  # 过观察期 → on_timeout 恢复

    nodes = _trace_nodes(inst)
    assert "d4" in nodes, f"G10：恢复后应经 on_timeout 边到 d4，实际 trace={nodes}"
    assert inst.state == DONE
    # PM §1.5 A：d4 的适配器调用真发生（不只是终态对）
    assert "light.turn_off" in runtime.adapters.get("mock").actions, (
        f"d4 动作没真下发，actions={runtime.adapters.get('mock').actions}"
    )


def test_canary_evidence_still_recorded_after_fix():
    """腿3：修复后漂移检查仍跑（证据落 verified/unmodeled），改边没跳过检查。"""
    from autoforge import af_watch

    af_watch._default._by_auto.clear()
    runtime = _build({"duration": "0.2s"}, with_timeout_edge=True)
    runtime.emit("binary_sensor.m", "on")
    assert runtime.instances.all()[0].state == SUSPENDED

    runtime.advance(1.0)

    events = [e for e in af_watch._default._by_auto.get("g10", []) if e.kind == "canary"]
    assert events, "canary 证据没落——漂移检查被跳过了"
    assert events[-1].status in ("verified", "unmodeled", "failed")


def test_without_canary_duration_d4_runs_normally():
    """腿2（对照）：不带 canary.duration，then 链行为不变。"""
    runtime = build_runtime(Graph([load_automation(_ir(None, with_timeout_edge=True))]))
    runtime.emit("binary_sensor.m", "on")

    inst = runtime.instances.all()[0]
    assert inst.state == DONE
    assert _trace_nodes(inst)[-1] == "d4"


def test_draft_backfills_on_timeout_edge_for_canary_duration_do():
    """腿4：af_draft 对带 canary.duration 的 do，输出 on_timeout 边指向下一节点。"""
    from autoforge.af_draft import draft_intent, get_staged

    intent = {
        "name": "g10",
        "when": {"type": "state", "entity": "binary_sensor.m", "to": "on"},
        "do_list": [
            {"action": "turn_on", "entity_id": "light.a", "canary": {"duration": "0.2s"}},
            {"action": "turn_off", "entity_id": "light.a"},
        ],
    }
    out = draft_intent(intent, catalog={"entities": {}})
    staged = get_staged(out["ref"])
    auto = staged["graph"].automations[0]
    edges = list(auto.edges)
    # 找两个 do 节点（id 由 gen_id("d") 生成）
    d_ids = [nid for nid in auto.nodes if str(nid).startswith("d")]
    assert len(d_ids) >= 2, f"应有两个 do 节点，nodes={list(auto.nodes)}"
    timeout_edges = [
        e for e in edges
        if e.kind == "on_timeout" and e.from_ == d_ids[0]
    ]
    assert timeout_edges, f"带 duration 的 do 应回填 on_timeout 边，实际 edges={[str(e) for e in edges]}"
    assert timeout_edges[0].to == d_ids[1], "on_timeout 边应指向下一个 do"
