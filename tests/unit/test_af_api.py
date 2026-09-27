"""v2 M3 结构化 Ask · /api/asks 路由冒烟测试。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from autoforge.af_api import build_app
from autoforge.af_ir import load_graph
from autoforge.af_store import GraphStore


AUTO_WITH_ASK: dict = {
    "ir_version": "0.2.1",
    "id": "g1",
    "name": "g1",
    "version": 1,
    "mode": "restart",
    "nodes": [
        {
            "id": "a1",
            "kind": "on",
            "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"},
        },
        {
            "id": "q1",
            "kind": "ask",
            "prompt": "开灯吗？",
            "room": "study",
            "timeout": "60s",
            "ask": {"kind": "choice", "options": ["开", "关"]},
        },
        {"id": "p1", "kind": "pass"},
    ],
    "edges": [
        {"from": "a1", "to": "q1", "kind": "then"},
        {"from": "q1", "to": "p1", "kind": "then"},
        {"from": "q1", "to": "p1", "kind": "default"},
    ],
}


def test_api_asks_unknown_returns_404(tmp_path, monkeypatch):
    # 本地无令牌逃生舱，否则 _read 依赖会 403
    monkeypatch.setenv("AF_ALLOW_NOAUTH", "1")
    client = TestClient(build_app(store_root=str(tmp_path)))
    r = client.get("/api/asks/does_not_exist")
    assert r.status_code == 404
    assert r.json()["detail"] == "未找到自动化"


def test_api_asks_by_name_exposes_control(tmp_path, monkeypatch):
    """IR 字段名为 `ask`：读节点时不能用 `ask_spec`（改名后曾漏改导致恒返回空）。"""
    monkeypatch.setenv("AF_ALLOW_NOAUTH", "1")
    root = str(tmp_path)
    store = GraphStore(root)
    store.save(load_graph({"automations": [AUTO_WITH_ASK]}), "g1")

    client = TestClient(build_app(store_root=root))
    r = client.get("/api/asks/g1")
    assert r.status_code == 200
    asks = r.json()["asks"]
    assert len(asks) == 1, f"应返回 1 条 ask，实际 {asks}"
    assert asks[0]["control"]["widget"] == "select"
    assert asks[0]["spec"]["kind"] == "choice"
    assert asks[0]["prompt"] == "开灯吗？"


def test_api_asks_aggregate_endpoint_registered(tmp_path, monkeypatch):
    """跨会话聚合端点存在且需鉴权（挂 _read，与 session 读端点同级）。"""
    monkeypatch.setenv("AF_ALLOW_NOAUTH", "1")
    client = TestClient(build_app(store_root=str(tmp_path)))
    r = client.get("/api/asks")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["total"] == 0 and body["asks"] == []


def test_api_asks_pending_not_shadowed_by_name_route(tmp_path, monkeypatch):
    """精确路径必须先于 `/api/asks/{name}` 通配，否则 pending 会被 {name} 吃掉。"""
    monkeypatch.setenv("AF_ALLOW_NOAUTH", "1")
    client = TestClient(build_app(store_root=str(tmp_path)))
    r = client.get("/api/asks/pending")
    # 若被 {name} 通配捕获会走 get_graph("pending") → 404；200 说明路由顺序正确
    assert r.status_code == 200
    assert r.json()["ok"] is True
