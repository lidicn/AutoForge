"""v0.3.0 跨自动化事件 · **发布侧** `emit`（IR §4.3）。

范围：只验"发布"——订阅侧 `on event` 属 v0.4.0。
契约要点：
- `emit` 是**节点字段**（非第 8 种 kind），语义"发出事件后继续"
- 走总线**独立通道**（主体 `event.<name>`、`source="emit"`），与实体状态事件隔离
- 被限流/熔断**不视为失败**，只落审计、不进 `on_error`
- `delay` 走实例定时器 + `TimeSource`，时间旅行可测
"""

from __future__ import annotations

from typing import Any

from autoforge.af_audit import BREAKER_OPEN, EVENT_EMITTED
from autoforge.af_bus import EVENT_ENTITY_PREFIX
from autoforge.af_instance import FAILED
from autoforge.af_ir import Graph, load_automation, load_graph
from autoforge.af_nl import render_graph
from autoforge.af_runtime import build_runtime
from autoforge.af_scanner import StaticScanner
from autoforge.af_spec import compile_spec, graph_to_raw, render_spec
from autoforge.af_vhass import FakeHAAdapter, seed_from_graph


def _emit_ir(event: str = "evt", data: dict[str, Any] | None = None, delay: str | None = None) -> dict:
    """最小 emit 图：`on` → `pass`（带 emit）。"""
    emit: dict[str, Any] = {"event": event}
    if data is not None:
        emit["data"] = data
    if delay is not None:
        emit["delay"] = delay
    return {
        "ir_version": "0.2.1",
        "id": "emit_demo",
        "name": "emit_demo",
        "version": 1,
        "mode": "single",
        "nodes": [
            {
                "id": "o",
                "kind": "on",
                "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"},
            },
            {"id": "n", "kind": "pass", "emit": emit},
        ],
        "edges": [{"from": "o", "to": "n", "kind": "then"}],
    }


def _make(graph_dict: dict, seed: dict[str, str]):
    graph = load_graph(graph_dict)
    runtime = build_runtime(graph)
    states = seed_from_graph(graph, seed, clock=runtime.clock)
    runtime.states = states
    runtime.instances.states = states
    runtime.scheduler.states = states
    runtime.executor.states = states
    runtime.adapters.register(FakeHAAdapter(states))
    return runtime, states


# ─────────────────────────────────────────────────────────────────────
# 发布
# ─────────────────────────────────────────────────────────────────────


def test_emit_publishes_custom_event_with_payload():
    """执行到 emit 节点 → 总线收到一条独立通道的自定义事件。"""
    runtime, _ = _make(_emit_ir("hello", {"room": "study"}), {"binary_sensor.m": "off"})

    runtime.emit("binary_sensor.m", "on")

    emitted = runtime.bus.emitted
    assert [e.event for e in emitted] == ["hello"], f"应恰好发布一条 hello，实际 {[e.event for e in emitted]}"
    assert emitted[0].payload == {"room": "study"}
    assert emitted[0].source == "emit"
    assert emitted[0].entity_id == f"{EVENT_ENTITY_PREFIX}hello"


def test_emit_records_audit_event():
    runtime, _ = _make(_emit_ir("hello"), {"binary_sensor.m": "off"})

    runtime.emit("binary_sensor.m", "on")

    hits = [e for e in runtime.audit if e.type == EVENT_EMITTED]
    assert hits, "应记录 EVENT_EMITTED 审计"
    assert hits[0].data["event"] == "hello"
    assert hits[0].data["result"] == "accepted"


def test_emit_does_not_pollute_entity_state_channel():
    """自定义事件不应被当成实体状态事件（独立通道）。"""
    runtime, _ = _make(_emit_ir("hello"), {"binary_sensor.m": "off"})

    runtime.emit("binary_sensor.m", "on")

    for event in runtime.bus.emitted:
        assert event.event is not None and event.last_changed is None
        assert event.entity_id.startswith(EVENT_ENTITY_PREFIX)


def test_emit_same_event_twice_is_not_deduped():
    """连续两次 emit 同一事件名都应发布（去重键含 event_id，不是实体三元组）。"""
    runtime, _ = _make(_emit_ir("dup"), {"binary_sensor.m": "off"})

    runtime.emit("binary_sensor.m", "on", last_changed="t1")
    runtime.clock.advance(0.25)  # 推进以绕过总线 200ms 节流（节流与去重是两回事）
    runtime.emit("binary_sensor.m", "on", last_changed="t2")

    assert len(runtime.bus.emitted) == 2, "同事件名重复发布不应被去重"


# ─────────────────────────────────────────────────────────────────────
# 延迟发布
# ─────────────────────────────────────────────────────────────────────


def test_emit_delay_uses_instance_timer_and_time_travel():
    """`delay` 走实例定时器：未到点不发布，`advance()` 后发布。"""
    runtime, _ = _make(_emit_ir("later", None, "30s"), {"binary_sensor.m": "off"})

    runtime.emit("binary_sensor.m", "on")
    assert runtime.bus.emitted == [], "延迟未到点不应发布"

    runtime.advance(31)
    assert [e.event for e in runtime.bus.emitted] == ["later"], "时间旅行到点后应发布"


# ─────────────────────────────────────────────────────────────────────
# 事件风暴：熔断而非失败
# ─────────────────────────────────────────────────────────────────────


def test_emit_storm_opens_breaker_without_failing_instance():
    """风暴 → 总线熔断，只落审计；**不进 on_error**，实例不 failed。"""
    runtime, _ = _make(_emit_ir("boom"), {"binary_sensor.m": "off"})

    for i in range(15):
        runtime.clock.advance(0.25)  # 推进以绕过 200ms 节流，使每次变更都计入熔断计数
        runtime.emit("binary_sensor.m", "on", last_changed=f"t{i}")

    assert any(e.type == BREAKER_OPEN for e in runtime.audit), "高频 emit 应触发熔断"
    assert all(i.state != FAILED for i in runtime.instances.all()), "熔断是系统保护，不应让实例 failed"


# ─────────────────────────────────────────────────────────────────────
# 扫描器
# ─────────────────────────────────────────────────────────────────────


def test_scanner_detects_emit_self_loop():
    """A emit `x` 且 A 自己由 `event.x` 触发 → 自环（编译期拦截）。"""
    auto = {
        "ir_version": "0.2.1",
        "id": "a",
        "name": "a",
        "version": 1,
        "mode": "single",
        "nodes": [
            {
                "id": "o",
                "kind": "on",
                "trigger": {"type": "state", "entity_id": "event.x", "to": "on"},
            },
            {"id": "n", "kind": "pass", "emit": {"event": "x"}},
        ],
        "edges": [{"from": "o", "to": "n", "kind": "then"}],
    }
    scan = StaticScanner(Graph([load_automation(auto)])).scan()
    assert "EMIT_SELF_LOOP" in scan.codes(), f"应检出 emit 自环，实际 {sorted(scan.codes())}"


def test_scanner_warns_when_same_event_emitted_repeatedly():
    auto = {
        "ir_version": "0.2.1",
        "id": "a",
        "name": "a",
        "version": 1,
        "mode": "single",
        "vars": {"v1": {"type": "numeric"}, "v2": {"type": "numeric"}},
        "nodes": [
            {
                "id": "o",
                "kind": "on",
                "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"},
            },
            {"id": "s1", "kind": "set", "var": "v1", "value": 1, "emit": {"event": "same"}},
            {"id": "s2", "kind": "set", "var": "v2", "value": 2, "emit": {"event": "same"}},
            {"id": "p", "kind": "pass"},
        ],
        "edges": [
            {"from": "o", "to": "s1", "kind": "then"},
            {"from": "s1", "to": "s2", "kind": "then"},
            {"from": "s2", "to": "p", "kind": "then"},
        ],
    }
    scan = StaticScanner(Graph([load_automation(auto)])).scan()
    assert "EMIT_STORM_LIMIT" in scan.codes(), f"重复 emit 同名事件应告警，实际 {sorted(scan.codes())}"
    # 只是告警，不拦截
    assert scan.ok


def test_scanner_allows_emit_and_case09_passes_build(examples_dir):
    """`emit` 已实现不再报保留位；case09 通过第一道闸。"""
    graph = load_graph(examples_dir / "case09_emit.json")
    scan = StaticScanner(graph).scan()
    assert "RESERVED_NOT_IMPLEMENTED" not in scan.codes(), "emit 已实现，不应再报保留位"
    assert scan.ok, scan.render()


# ─────────────────────────────────────────────────────────────────────
# NL / AF-Spec
# ─────────────────────────────────────────────────────────────────────


def test_nl_mentions_emitted_event():
    """NL 必须写出事件名——"批准的与跑的必须一致"。"""
    result = render_graph(load_graph(_emit_ir("hello", {"room": "study"})))
    assert "hello" in result.text, f"NL 应提到事件名：{result.text}"
    assert result.ok, f"NL 覆盖率失败：{result.missing}"


def test_spec_emit_roundtrip_is_lossless():
    """AF-Spec：`emit` 字段无损往返。"""
    graph = load_graph(_emit_ir("hello", {"room": "study"}))
    text = render_spec(graph)
    assert "emit" in text, f"渲染应包含 emit：{text}"

    assert graph_to_raw(compile_spec(text)) == graph_to_raw(graph)
