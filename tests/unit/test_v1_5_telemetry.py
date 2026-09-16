"""v1.5.0 遥测单测（调研 §2.10/§2.16：`_telemetry` 零侵入 + token 估算 + 有界聚合）。"""

from autoforge import af_service as svc
from autoforge.af_store import GraphStore
from autoforge.af_telemetry import estimate_tokens

#: L3 动作（http 出站）→ 首错 L3_ACTION
BAD_IR = {
    "ir_version": "0.2.1", "id": "bad", "name": "bad", "version": 1, "mode": "single",
    "nodes": [
        {"id": "a", "kind": "on", "trigger": {"type": "state", "entity_id": "light.x", "to": "on"}},
        {"id": "d", "kind": "do", "adapter": "http", "action": "get", "params": {"url": "https://x"}},
        {"id": "p", "kind": "pass"},
    ],
    "edges": [
        {"from": "a", "to": "d", "kind": "then"},
        {"from": "d", "to": "p", "kind": "then"},
    ],
}

GOOD_IR = {
    "ir_version": "0.2.1", "id": "g", "name": "g", "version": 1, "mode": "single",
    "nodes": [
        {"id": "a", "kind": "on", "trigger": {"type": "state", "entity_id": "light.x", "to": "on"}},
        {"id": "p", "kind": "pass"},
    ],
    "edges": [{"from": "a", "to": "p", "kind": "then"}],
}


def test_estimate_tokens():
    assert estimate_tokens("a" * 400) == 100
    assert estimate_tokens("abc") == 1
    assert estimate_tokens("") == 0


def test_build_failure_carries_telemetry(tmp_path):
    """失败回执 `_telemetry` 含归因 + token；二次失败附历史同类（写→读闭环）。"""
    store = GraphStore(str(tmp_path))
    res = svc.build(BAD_IR, store=store)
    assert res["ok"] is False
    tel = res["_telemetry"]
    assert tel["ok"] is False
    assert tel["category"] == "L3_ACTION"
    assert tel["advice"]
    assert isinstance(tel["history"], list)

    res2 = svc.build(BAD_IR, store=store)
    assert res2["_telemetry"]["history"], "第二次相同失败应带历史同类"


def test_build_success_telemetry_ok(tmp_path):
    """零侵入：成功结果既有字段不变，仅多一个 `_telemetry`。"""
    store = GraphStore(str(tmp_path))
    res = svc.build(GOOD_IR, store=store)
    assert res["ok"] is True
    assert res["_telemetry"]["ok"] is True
    assert res["_telemetry"]["tokens"] > 0


def test_usage_aggregates(tmp_path):
    store = GraphStore(str(tmp_path))
    svc.build(GOOD_IR, store=store)
    svc.build(BAD_IR, store=store)
    usage = svc.get_telemetry(store)
    assert usage["total"] >= 2
    assert usage["by_tool"].get("build", 0) >= 2
    assert usage["by_ok"]["failed"] >= 1
    assert usage["knowledge"]


def test_simulate_telemetry(tmp_path):
    """sim 的 `ok` 恒 True；`_telemetry.ok` 反映断言是否通过。"""
    store = GraphStore(str(tmp_path))
    res = svc.simulate(GOOD_IR, store=store)
    assert res["ok"] is True
    assert res["_telemetry"]["ok"] is True
