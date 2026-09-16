"""v1.5.0 经验/共现单测（调研 §2.12：无向去序 + 仅成功更新 + 导出）。"""

from autoforge.af_experience import ExperienceStore
from autoforge.af_ir import load_graph

IR = {
    "ir_version": "0.2.1", "id": "e", "name": "e", "version": 1, "mode": "single",
    "nodes": [
        {"id": "a", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
        {"id": "d", "kind": "do", "adapter": "ha", "action": "light.turn_on", "params": {"entity_id": "light.x"}},
        {"id": "p", "kind": "pass"},
    ],
    "edges": [
        {"from": "a", "to": "d", "kind": "then"},
        {"from": "d", "to": "p", "kind": "then"},
    ],
}


def test_pair_undirected(tmp_path):
    store = ExperienceStore(tmp_path)
    store.observe(load_graph(IR), ok=True)
    pairs = store.top_pairs()
    assert len(pairs) == 1
    assert pairs[0]["pair"] == "binary_sensor.m|light.x"
    assert pairs[0]["count"] == 1


def test_observe_accumulates_without_splitting(tmp_path):
    """`a|b` 与 `b|a` 合并（无向去序），重复观测累加计数。"""
    store = ExperienceStore(tmp_path)
    g = load_graph(IR)
    store.observe(g, ok=True)
    store.observe(g, ok=True)
    pairs = store.top_pairs()
    assert len(pairs) == 1
    assert pairs[0]["count"] == 2


def test_failed_run_not_recorded(tmp_path):
    """失败样本完全不进经验。"""
    store = ExperienceStore(tmp_path)
    res = store.observe(load_graph(IR), ok=False)
    assert res["updated"] is False
    assert store.top_pairs() == []
    assert store.top_entities() == []


def test_export_shape(tmp_path):
    store = ExperienceStore(tmp_path)
    store.observe(load_graph(IR), ok=True)
    exp = store.export()
    assert set(exp) >= {"ok", "generated_at", "observed", "pairs", "entities", "patterns"}
    assert exp["observed"] == 1
    assert exp["patterns"]["kinds"].get("do") == 1
    assert {e["entity_id"] for e in exp["entities"]} == {"binary_sensor.m", "light.x"}


def test_service_get_experience(tmp_path):
    """service 出口：成功落盘后自动采集共现。"""
    from autoforge import af_service as svc
    from autoforge.af_store import GraphStore

    store = GraphStore(str(tmp_path))
    svc.save_graph(store, IR, "exp_demo")
    summary = svc.get_experience(store)
    assert summary["observed"] >= 1
    assert any(p["pair"] == "binary_sensor.m|light.x" for p in summary["top_pairs"])
