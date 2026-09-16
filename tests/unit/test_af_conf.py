"""G4 置信度分级自主单测。"""

from __future__ import annotations

from autoforge.af_conf import (
    AUTO_MIN,
    SHADOW_LOW,
    ConfidenceStore,
    decision_for,
    decay,
)
from autoforge.af_ir import load_graph

_GRAPH = {
    "ir_version": "0.2.1",
    "id": "a",
    "name": "a",
    "version": 1,
    "mode": "single",
    "confidence": 0.9,
    "nodes": [{"id": "o", "kind": "on", "trigger": {"type": "state", "entity_id": "x", "to": "on"}}, {"id": "p", "kind": "pass"}],
    "edges": [{"from": "o", "to": "p", "kind": "then"}],
}

_BAND = {
    "ir_version": "0.2.1",
    "id": "b",
    "name": "b",
    "version": 1,
    "mode": "single",
    "confidence": 0.5,
    "nodes": [{"id": "o", "kind": "on", "trigger": {"type": "state", "entity_id": "x", "to": "on"}}, {"id": "p", "kind": "pass"}],
    "edges": [{"from": "o", "to": "p", "kind": "then"}],
}


def test_decision_thresholds():
    assert decision_for(0.99) == "auto"
    assert decision_for(AUTO_MIN) == "auto"
    assert decision_for(0.84) == "shadow"
    assert decision_for(SHADOW_LOW) == "shadow"
    assert decision_for(0.59) == "ask"
    assert decision_for(0.0) == "ask"


def test_decay_is_monotonic_and_bounded():
    assert decay(1.0, 0) == 1.0
    d1 = decay(0.9, 24)
    assert d1 < 0.9
    assert d1 > 0.86
    # 事件越多衰减越多
    assert decay(0.9, 24, events=10) < decay(0.9, 24, events=0)
    # 钳制 [0,1]
    assert 0.0 <= decay(0.1, 100000) <= 1.0


def test_store_seeds_from_declared_confidence():
    graph = load_graph(_GRAPH)
    store = ConfidenceStore().seed(graph)
    assert store.get("a") == 0.9
    assert store.band("a") == "auto"


def test_store_defaults_undeclared_to_full():
    graph = load_graph({"ir_version": "0.2.1", "id": "c", "name": "c", "version": 1, "mode": "single",
                        "nodes": [{"id": "o", "kind": "on", "trigger": {"type": "state", "entity_id": "x", "to": "on"}}, {"id": "p", "kind": "pass"}],
                        "edges": [{"from": "o", "to": "p", "kind": "then"}]})
    store = ConfidenceStore().seed(graph)
    assert store.get("c") == 1.0
    assert store.band("c") == "auto"


def test_sample_replay_changes_band():
    graph = load_graph(_BAND)
    store = ConfidenceStore().seed(graph)
    assert store.band("b") == "ask"              # 0.5 < 0.60
    for _ in range(5):
        store.record_positive("b")              # 0.5 + 0.15 = 0.65
    assert store.band("b") == "shadow"
    for _ in range(8):
        store.record_positive("b")              # 0.65 + 0.24 = 0.89
    assert store.band("b") == "auto"

    store.record_negative("b")                   # 0.89 - 0.25 = 0.64
    assert store.band("b") == "shadow"
    store.record_negative("b")                    # 0.39
    store.record_negative("b")                    # 0.14
    assert store.band("b") == "ask"


def test_promote_to_full():
    graph = load_graph(_BAND)
    store = ConfidenceStore().seed(graph)
    store.promote("b")
    assert store.get("b") == 1.0
    assert store.band("b") == "auto"
