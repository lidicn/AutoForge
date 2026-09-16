"""v0.4.0 跨自动化事件 · **订阅侧** `on event`（IR §4.3）。

与 v0.3.0（发布侧 `emit`）合起来构成闭环：
    A 执行到 emit 节点 → 总线独立通道 `event.<name>` → B 的 `on event` 匹配 → B 触发

契约要点：
- `on event` 是 `on` 节点的一种 **trigger 类型**：`{"type": "event", "event": "<name>"}`
- 匹配方式：按 `event.<name>` **精确匹配，不支持通配符**（KICKOFF §4.1）
- 事件边纳入依赖矩阵 → `EMIT_SELF_LOOP` 能在编译期拦下自触发环
"""

from __future__ import annotations

from typing import Any

from autoforge.af_adapters import MockAdapter
from autoforge.af_bus import EVENT_ENTITY_PREFIX
from autoforge.af_ir import Graph, load_automation, load_graph
from autoforge.af_nl import render_graph
from autoforge.af_runtime import build_runtime
from autoforge.af_scanner import StaticScanner
from autoforge.af_spec import compile_spec, graph_to_raw, render_spec
from autoforge.af_vhass import FakeHAAdapter, seed_from_graph


def _publisher(emit_event: str = "ping") -> dict:
    """A：`on`(状态) → `pass`(带 emit)。"""
    return {
        "ir_version": "0.2.1",
        "id": "a",
        "name": "a",
        "version": 1,
        "mode": "single",
        "nodes": [
            {
                "id": "o",
                "kind": "on",
                "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"},
            },
            {"id": "n", "kind": "pass", "emit": {"event": emit_event}},
        ],
        "edges": [{"from": "o", "to": "n", "kind": "then"}],
    }


def _subscriber(sub_event: str = "ping") -> dict:
    """B：`on`(event) → `do`(mock) → `pass`。"""
    return {
        "ir_version": "0.2.1",
        "id": "b",
        "name": "b",
        "version": 1,
        "mode": "single",
        "nodes": [
            {"id": "o", "kind": "on", "trigger": {"type": "event", "event": sub_event}},
            {
                "id": "d",
                "kind": "do",
                "adapter": "mock",
                "action": "light.turn_on",
                "params": {"entity_id": "light.b"},
            },
            {"id": "p", "kind": "pass"},
        ],
        "edges": [
            {"from": "o", "to": "d", "kind": "then"},
            {"from": "d", "to": "p", "kind": "then"},
        ],
    }


def _make(publisher: dict, subscriber: dict, seed: dict[str, str]):
    graph = load_graph({"automations": [publisher, subscriber]})
    runtime = build_runtime(graph)
    states = seed_from_graph(graph, seed, clock=runtime.clock)
    runtime.states = states
    runtime.instances.states = states
    runtime.scheduler.states = states
    runtime.executor.states = states
    runtime.adapters.register(MockAdapter())
    return runtime, states


# ─────────────────────────────────────────────────────────────────────
# 端到端闭环
# ─────────────────────────────────────────────────────────────────────


def test_emit_drives_on_event_subscriber_end_to_end():
    """A emit `ping` → B（`on event ping`）被驱动并执行动作。"""
    runtime, _ = _make(_publisher("ping"), _subscriber("ping"), {"binary_sensor.m": "off", "light.b": "off"})

    runtime.emit("binary_sensor.m", "on")

    # 1) A 确实发布了事件
    assert [e.event for e in runtime.bus.emitted] == ["ping"]
    # 2) B 被驱动：执行了 light.turn_on
    assert [a for a, _ in runtime.adapters.get("mock").calls] == ["light.turn_on"]


def test_on_event_ignores_unsubscribed_event_names():
    """订阅 `ping` 的自动化不应被 `pong` 触发（精确匹配，无通配符）。"""
    runtime, _ = _make(_publisher("pong"), _subscriber("ping"), {"binary_sensor.m": "off", "light.b": "off"})

    runtime.emit("binary_sensor.m", "on")

    assert [e.event for e in runtime.bus.emitted] == ["pong"], "事件本身应被发布"
    assert runtime.adapters.get("mock").calls == [], "事件名不匹配，B 不应触发"


def test_on_event_subscriber_runs_to_completion():
    """被事件驱动的实例应正常走到终态（不是挂起）。"""
    from autoforge.af_instance import DONE

    runtime, _ = _make(_publisher("ping"), _subscriber("ping"), {"binary_sensor.m": "off", "light.b": "off"})

    runtime.emit("binary_sensor.m", "on")

    states = sorted(i.state for i in runtime.instances.all())
    assert states == [DONE, DONE], f"A 与 B 都应正常结束，实际 {states}"


# ─────────────────────────────────────────────────────────────────────
# 依赖图与环检测
# ─────────────────────────────────────────────────────────────────────


def test_trigger_entities_include_event_channel():
    """`on event` 的触发源实体 = 总线主体 `event.<name>`（事件边进依赖矩阵）。"""
    auto = load_automation(_subscriber("ping"))
    assert auto.trigger_entities() == {f"{EVENT_ENTITY_PREFIX}ping"}


def test_scanner_detects_emit_to_own_on_event_loop():
    """同一条自动化 emit `ping` 且又订阅 `ping` → 自触发环（编译期拦截）。"""
    auto = {
        "ir_version": "0.2.1",
        "id": "a",
        "name": "a",
        "version": 1,
        "mode": "single",
        "nodes": [
            {"id": "o", "kind": "on", "trigger": {"type": "event", "event": "ping"}},
            {"id": "n", "kind": "pass", "emit": {"event": "ping"}},
        ],
        "edges": [{"from": "o", "to": "n", "kind": "then"}],
    }
    scan = StaticScanner(Graph([load_automation(auto)])).scan()
    assert "EMIT_SELF_LOOP" in scan.codes(), f"应检出自触发环，实际 {sorted(scan.codes())}"


# ─────────────────────────────────────────────────────────────────────
# NL / AF-Spec
# ─────────────────────────────────────────────────────────────────────


def test_nl_renders_on_event_trigger():
    graph = load_graph({"automations": [_publisher("ping"), _subscriber("ping")]})
    result = render_graph(graph)
    assert "收到事件「ping」" in result.text, f"NL 应写出订阅的事件名：{result.text}"
    assert result.ok, f"NL 覆盖率失败：{result.missing}"


def test_spec_on_event_roundtrip_is_lossless():
    graph = load_graph({"automations": [_publisher("ping"), _subscriber("ping")]})
    text = render_spec(graph)
    assert '"type": "event"' in text, f"渲染应包含 event 触发：{text}"

    assert graph_to_raw(compile_spec(text)) == graph_to_raw(graph)


def test_event_trigger_requires_event_name():
    """`{"type": "event"}` 缺 `event` 字段 → Schema 拒绝（IR 唯一真相）。"""
    import pytest

    from autoforge.af_ir import IRValidationError

    bad = _subscriber("ping")
    bad["nodes"][0]["trigger"] = {"type": "event"}
    with pytest.raises(IRValidationError):
        load_automation(bad)


def test_case10_emit_on_event_closes_the_loop(examples_dir):
    """用归档样例验证 emit → on event 端到端闭环（ROADMAP v0.4.0 退出标准）。"""
    graph = load_graph(examples_dir / "case10_emit_on_event.json")

    # 第一道闸：闭环样例应通过静态扫描（含 emit 环检测）
    scan = StaticScanner(graph).scan()
    assert scan.ok, scan.render()

    runtime = build_runtime(graph)
    states = seed_from_graph(
        graph,
        {
            "binary_sensor.study_motion": "off",
            "sensor.study_illum": "80",
            "light.study_main": "off",
            "light.study_ambient": "off",
        },
        clock=runtime.clock,
    )
    runtime.states = states
    runtime.instances.states = states
    runtime.scheduler.states = states
    runtime.executor.states = states
    runtime.adapters.register(FakeHAAdapter(states))

    runtime.emit("binary_sensor.study_motion", "on")

    # 发布侧：发出了 study_light_on
    assert [e.event for e in runtime.bus.emitted] == ["study_light_on"]
    # 订阅侧联动：氛围灯被打开（闭环生效）
    assert states.get("light.study_ambient") == "on"
