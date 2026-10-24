"""G10 判据腿：canary + duration 的链，观察期超时恢复后下一个节点不应被掐掉。

实测缺口（docs/缺口登记_实测自动化_20261023.md G10）：
a1(on) → d3(do, canary.duration) → d4(do/pass)。d3 下发后挂起观察 duration，
到点 on_timeout 恢复做漂移检查；但恢复分支选边用的是 `{on_timeout, default}`，
而 d3→d4 是 `then` 边 → pick_edge 落空 → _terminate，d4 永远不被执行。

本文件只钉行为证据（不改 src——修 d3→then 边是 PM 的活）：
- 腿1：带 canary.duration 时，advance 过观察期后 d4 从未进 trace（当前=缺陷，断言红是对的）。
- 腿2：不带 canary.duration 时，同一条 then 链 d4 正常执行（对照，证明不是图本身搭错）。
"""

from __future__ import annotations

from autoforge.af_instance import DONE, SUSPENDED
from autoforge.af_runtime import build_runtime
from autoforge.af_ir import Graph, load_automation


ON_M = {"id": "a1", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}}
D4 = {"id": "d4", "kind": "pass"}


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


def _ir(canary: dict | None) -> dict:
    return {
        "ir_version": "0.2.1",
        "id": "g10",
        "name": "g10",
        "version": 1,
        "mode": "single",
        "nodes": [ON_M, _d3(canary), D4],
        "edges": [
            {"from": "a1", "to": "d3", "kind": "then"},
            {"from": "d3", "to": "d4", "kind": "then"},
        ],
    }


def _trace_nodes(instance) -> list[str]:
    return [t["node"] for t in instance.ctx.trace]


def _build(canary: dict | None):
    runtime = build_runtime(Graph([load_automation(_ir(canary))]))
    runtime.states.set_state("light.a", "off")  # canary 取 pre_snapshot 需要已知实体状态
    return runtime


def test_canary_with_duration_chain_break_d4_never_runs():
    """腿1（钉当前缺陷）：观察期超时恢复后，d4 从未被调用。"""
    runtime = _build({"duration": "0.2s"})
    runtime.emit("binary_sensor.m", "on")

    inst = runtime.instances.all()[0]
    # d3 已下发并挂起观察
    assert inst.state == SUSPENDED
    assert "d3" in _trace_nodes(inst)

    runtime.advance(1.0)  # 过观察期 → on_timeout 恢复

    nodes = _trace_nodes(inst)
    # G10 缺陷证据：恢复后选边用 on_timeout/default，d3→d4(then) 落空 → 链断在 d3
    assert "d4" not in nodes, (
        f"G10 已修？d4 现在跑了（trace={nodes}）——若如此请把本腿改为正向断言；"
        f"在此之前本测试钉住的是'链断在 d3'这个现状。"
    )


def test_without_canary_duration_d4_runs_normally():
    """腿2（对照）：不带 canary.duration，同一条 then 链 d4 正常走完。"""
    runtime = build_runtime(Graph([load_automation(_ir(None))]))
    runtime.emit("binary_sensor.m", "on")

    inst = runtime.instances.all()[0]
    assert inst.state == DONE
    assert _trace_nodes(inst)[-1] == "d4"
