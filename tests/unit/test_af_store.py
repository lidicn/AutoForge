"""G6 版本审计与 diff 单测：GraphStore / 置信度持久化 / 可寻址 / diff / CLI。"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from autoforge.af_cli import app
from autoforge.af_conf import ConfidenceStore
from autoforge.af_ir import load_graph
from autoforge.af_runtime import build_runtime
from autoforge.af_state import InMemoryStateProvider
from autoforge.af_store import (
    GraphStore,
    diff_graphs,
    dump_confidence,
    find_node,
    instance_record,
    load_confidence,
    restore_context,
)


def _graph(target: str = "light.x", confidence: float | None = None):
    data = {
        "ir_version": "0.2.1",
        "id": "demo",
        "name": "demo",
        "version": 1,
        "mode": "single",
        "nodes": [
            {"id": "o", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
            {"id": "d", "kind": "do", "adapter": "mock", "action": "light.turn_on", "params": {"entity_id": target}},
            {"id": "p", "kind": "pass"},
        ],
        "edges": [
            {"from": "o", "to": "d", "kind": "then"},
            {"from": "d", "to": "p", "kind": "then"},
            {"from": "d", "to": "p", "kind": "on_error"},
        ],
    }
    if confidence is not None:
        data["confidence"] = confidence
    return load_graph(data)


# ─────────────────────────────────────────────────────────────────────
# 版本化存储
# ─────────────────────────────────────────────────────────────────────


def test_store_save_versions_and_load_roundtrip(tmp_path):
    store = GraphStore(tmp_path)
    assert store.versions("demo") == [] and store.latest("demo") is None

    v1 = store.save(_graph("light.a"), "demo", note="初版")
    v2 = store.save(_graph("light.b"), "demo", note="改目标")

    assert (v1, v2) == (1, 2)
    assert store.versions("demo") == [1, 2]
    assert store.latest("demo") == 2
    assert store.load("demo", 1).get("demo").node("d").params["entity_id"] == "light.a"
    assert store.load("demo").get("demo").node("d").params["entity_id"] == "light.b"

    record = store.load_record("demo", 1)
    assert record["note"] == "初版" and record["saved_at"]


def test_store_history(tmp_path):
    store = GraphStore(tmp_path)
    store.save(_graph(), "a", note="x")
    store.save(_graph(), "b", note="y")
    history = store.history()
    assert {h["name"] for h in history} == {"a", "b"}
    assert all(h["version"] == 1 for h in history)


def test_store_load_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        GraphStore(tmp_path).load("nope")


# ─────────────────────────────────────────────────────────────────────
# 置信度持久化
# ─────────────────────────────────────────────────────────────────────


def test_confidence_dump_load_roundtrip():
    store = ConfidenceStore(values={"a": 0.9, "b": 0.4})
    store.record_negative("a")
    payload = json.loads(json.dumps(dump_confidence(store)))  # 必须可 JSON 化
    restored = load_confidence(payload)
    assert restored.values == store.values
    assert restored.samples == store.samples


def test_store_save_and_load_conf(tmp_path):
    store = GraphStore(tmp_path)
    conf = ConfidenceStore().seed(_graph())
    conf.record_negative("demo")
    path = store.save_conf(conf, "demo", note="干预后")
    assert path.exists()

    restored = store.load_conf("demo")
    assert restored.get("demo") == conf.get("demo")
    assert restored.samples["demo"] == conf.samples["demo"]


# ─────────────────────────────────────────────────────────────────────
# 实例快照可回放
# ─────────────────────────────────────────────────────────────────────


def test_instance_record_and_restore_context():
    graph = _graph()
    states = InMemoryStateProvider()
    states.set_state("binary_sensor.m", "off")
    runtime = build_runtime(graph, states=states)
    runtime.emit("binary_sensor.m", "on")

    instance = runtime.instances.all()[0]
    record = instance_record(instance)
    assert record["automation_id"] == "demo"

    ctx = restore_context(record)
    assert ctx.automation_id == "demo"
    assert ctx.state == instance.state
    assert ctx.instance_id == instance.instance_id
    assert ctx.snapshot == instance.ctx.snapshot


# ─────────────────────────────────────────────────────────────────────
# 可寻址
# ─────────────────────────────────────────────────────────────────────


def test_find_node_by_full_address_and_unique_id():
    graph = _graph()
    assert find_node(graph, "demo:d").kind == "do"
    assert find_node(graph, "d").kind == "do"  # 全局唯一时可省 automation_id


def test_find_node_unknown_raises():
    with pytest.raises(KeyError):
        find_node(_graph(), "demo:nope")


# ─────────────────────────────────────────────────────────────────────
# diff
# ─────────────────────────────────────────────────────────────────────


def test_diff_identical_is_empty():
    assert diff_graphs(_graph(), _graph()).empty


def test_diff_detects_param_change_and_meta():
    old = _graph("light.a", confidence=0.9)
    new = _graph("light.b", confidence=0.7)
    diff = diff_graphs(old, new)

    assert not diff.empty
    changed = dict(diff.changed_nodes)
    assert "demo:d" in changed and "params" in changed["demo:d"]
    assert ("demo", "confidence", 0.9, 0.7) in diff.meta_changes
    text = diff.render()
    assert "~ 节点 demo:d" in text and "confidence" in text


def test_diff_detects_added_removed_node_and_edge():
    old = _graph()
    new_data = {
        "ir_version": "0.2.1",
        "id": "demo",
        "name": "demo",
        "version": 2,
        "mode": "single",
        "nodes": [
            {"id": "o", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
            {"id": "d", "kind": "do", "adapter": "mock", "action": "light.turn_on", "params": {"entity_id": "light.x"}},
            {"id": "p", "kind": "pass"},
            {"id": "extra", "kind": "set", "var": "flag", "value": True},
        ],
        "vars": {"flag": {"type": "boolean", "value": False}},
        "edges": [
            {"from": "o", "to": "d", "kind": "then"},
            {"from": "d", "to": "extra", "kind": "then"},
            {"from": "extra", "to": "p", "kind": "then"},
            {"from": "d", "to": "p", "kind": "on_error"},
        ],
    }
    diff = diff_graphs(old, load_graph(new_data))

    assert "demo:extra" in diff.added_nodes
    assert "demo:d->extra:then" in diff.added_edges
    assert "demo:d->p:then" in diff.removed_edges


def test_diff_detects_added_automation():
    old = _graph()
    new = load_graph(
        {
            "automations": [
                {
                    "ir_version": "0.2.1",
                    "id": "demo",
                    "name": "demo",
                    "version": 1,
                    "mode": "single",
                    "nodes": [
                        {"id": "o", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
                        {"id": "p", "kind": "pass"},
                    ],
                    "edges": [{"from": "o", "to": "p", "kind": "then"}],
                },
                {
                    "ir_version": "0.2.1",
                    "id": "brand_new",
                    "name": "new",
                    "version": 1,
                    "mode": "single",
                    "nodes": [
                        {"id": "o", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.n", "to": "on"}},
                        {"id": "p", "kind": "pass"},
                    ],
                    "edges": [{"from": "o", "to": "p", "kind": "then"}],
                },
            ]
        }
    )
    diff = diff_graphs(old, new)
    assert "brand_new" in diff.added_automations
    assert "demo" not in diff.added_automations


# ─────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────


def test_cli_diff_reports_changes(tmp_path):
    old = tmp_path / "old.json"
    new = tmp_path / "new.json"
    old.write_text(json.dumps(_graph("light.a").get("demo").raw), encoding="utf-8")
    new.write_text(json.dumps(_graph("light.b").get("demo").raw), encoding="utf-8")

    result = CliRunner().invoke(app, ["diff", str(old), str(new)])
    assert result.exit_code == 0, result.output
    assert "版本 diff" in result.output and "~ 节点 demo:d" in result.output


def test_cli_store_save_and_log(tmp_path, examples_dir):
    root = str(tmp_path / "forge")
    save = CliRunner().invoke(
        app, ["store", "save", str(examples_dir / "case01_day_light.json"), "--name", "study", "--root", root]
    )
    assert save.exit_code == 0, save.output
    assert "v1" in save.output

    log = CliRunner().invoke(app, ["store", "log", "--root", root])
    assert log.exit_code == 0, log.output
    assert "study v1" in log.output
