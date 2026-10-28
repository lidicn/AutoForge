"""第四批 · 批 1：D5 af_apply 补 owner。

`apply()` 此前入队的 save_payload 只有 {ir, name}——审批落盘后归档 record["owner"] 是空串，
与 MCP 面 `af_mcp._t_save`（写 "owner": current.subject）不对称。本批给 apply 加
`authenticated_subject` 形参并把 owner 写进 save_payload；approve_pending 回放时经
`save_graph(..., owner=payload["owner"])` 落盘。

判据腿：
1. apply 传 authenticated_subject="alice" ⇒ 入队 payload["owner"] == "alice"；
2. apply 不传 ⇒ payload["owner"] == ""（向后兼容，旧调用方不回归）；
3. authenticated_subject 同时下传 submit_pending.submitted_by；
4. 真审批回放：payload["owner"] 落进归档 record["owner"]；
5. payload 的 owner 键名与 MCP 面一致（"owner"，不是 owner_subject/owned_by 等异写）。
"""

from __future__ import annotations

import pytest

from autoforge import af_service
from autoforge.af_apply import apply
from autoforge.af_draft import _staging
from autoforge.af_ir import load_graph
from autoforge.af_store import GraphStore


def _minimal_ir(name: str = "alice_arch") -> dict:
    return {
        "ir_version": "0.2.1",
        "id": name,
        "name": name,
        "version": 1,
        "mode": "single",
        "nodes": [
            {"id": "o", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
            {"id": "d", "kind": "do", "adapter": "mock", "action": "light.turn_on", "params": {"entity_id": "light.x"}},
        ],
        "edges": [{"from": "o", "to": "d", "kind": "then"}],
    }


@pytest.fixture
def fake_pipeline(monkeypatch):
    """短路 build/simulate（不连 catalog/HA），捕获 submit_pending 收到的 payload。"""
    captured: dict = {}
    called: dict = {}

    monkeypatch.setattr(af_service, "build", lambda ir, store=None, use_catalog=True: {"ok": True})
    monkeypatch.setattr(af_service, "simulate", lambda ir, seed=None, events=None, store=None: {"ok": True, "instances": [{"state": "done", "current_node": "p1"}]})

    def fake_submit(store, source, payload, submitted_by="", authenticated_subject=None):
        captured.update(payload)
        called["submitted_by"] = submitted_by
        called["authenticated_subject"] = authenticated_subject
        return {"ok": True, "pending": "op-test", "summary": payload.get("name", "")}

    monkeypatch.setattr(af_service, "submit_pending", fake_submit)
    return captured, called


def _ref() -> str:
    graph = load_graph({"automations": [_minimal_ir()]})
    return _staging.put(graph, "alice_arch", {})


def test_apply_payload_carries_owner(tmp_path, monkeypatch, fake_pipeline):
    captured, called = fake_pipeline
    store = GraphStore(str(tmp_path / "store"))
    res = apply(_ref(), stage="save", store=store, authenticated_subject="alice")
    assert res["ok"] is True
    assert captured.get("owner") == "alice"


def test_apply_payload_defaults_empty_owner(tmp_path, monkeypatch, fake_pipeline):
    captured, called = fake_pipeline
    store = GraphStore(str(tmp_path / "store"))
    res = apply(_ref(), stage="save", store=store)  # 不传 authenticated_subject
    assert res["ok"] is True
    assert captured.get("owner") == ""


def test_apply_submitted_by_uses_authenticated_subject(tmp_path, monkeypatch, fake_pipeline):
    captured, called = fake_pipeline
    store = GraphStore(str(tmp_path / "store"))
    apply(_ref(), stage="save", store=store, authenticated_subject="alice")
    # authenticated_subject 必须下传 submit_pending（自批检查需要真主体）
    assert called["authenticated_subject"] == "alice"


def test_payload_owner_key_matches_mcp_t_save(fake_pipeline):
    captured, called = fake_pipeline
    store = GraphStore(str(__import__("tempfile").mkdtemp()))
    apply(_ref(), stage="save", store=store, authenticated_subject="alice")
    # 键名必须是 "owner"——与 af_mcp._t_save 写的同一枚键（路线图 D5）
    assert "owner" in captured
    assert "owner_subject" not in captured and "owned_by" not in captured


def test_approved_archive_owner_matches_subject(tmp_path, monkeypatch):
    """端到端：apply 写出的 payload 经真 approve_pending 回放，归档 record['owner'] == alice。"""
    monkeypatch.setattr(af_service, "build", lambda ir, store=None, use_catalog=True: {"ok": True})
    monkeypatch.setattr(af_service, "simulate", lambda ir, seed=None, events=None, store=None: {"ok": True, "instances": [{"state": "done", "current_node": "p1"}]})
    store = GraphStore(str(tmp_path / "store"))

    payload_seen: dict = {}

    real_submit = af_service.submit_pending

    def spy_submit(s, source, payload, submitted_by="", authenticated_subject=None):
        payload_seen.update(payload)
        return real_submit(s, source, payload, submitted_by=submitted_by,
                           authenticated_subject=authenticated_subject)

    monkeypatch.setattr(af_service, "submit_pending", spy_submit)
    res = apply(_ref(), stage="save", store=store, authenticated_subject="alice")
    assert res["ok"] is True

    op_id = res["save"]["pending"]
    svc_res = af_service.approve_pending(store, op_id, reviewer="human")
    assert svc_res["ok"] is True
    assert store._record_owner("alice_arch") == "alice"
