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


def test_readonly_mode_rejects_write_endpoints(tmp_path):
    """单写者租约降级只读（DCD 裁定一 A）：写端点应 503，而非走到鉴权 403。"""
    client = TestClient(build_app(store_root=str(tmp_path), readonly=True))
    r = client.post("/api/graphs/enable", json={"tag": "t", "enable": True})
    assert r.status_code == 503


def test_readonly_mode_blocks_unauth_build_endpoint(tmp_path):
    """建自动化（/api/build，无 _write 守卫）在只读模式同样被拒。"""
    client = TestClient(build_app(store_root=str(tmp_path), readonly=True))
    r = client.post("/api/build", json={"ir": {"name": "x"}})
    assert r.status_code == 503


def test_writable_mode_write_endpoints_not_503(tmp_path):
    """非只读：写端点走正常鉴权链路（fail-closed 403），不触发只读拦截。"""
    client = TestClient(build_app(store_root=str(tmp_path), readonly=False))
    r = client.post("/api/graphs/enable", json={"tag": "t", "enable": True})
    assert r.status_code != 503


def test_api_sim_rejects_oversized_event_list(tmp_path, monkeypatch):
    """/api/sim 的 events 上限是 DoS 面（整场仿真在内存里跑），必须在边界上真生效。

    它原先写成 pydantic v2 的 `Field(max_length=…)`：CI 解析到 pydantic 1.10 时
    **import 期就 ValueError**（"constraints set but not enforced"），而 v1 里这条
    约束根本不存在——一个只在半套环境里生效、另半套环境让整仓崩掉的写法不是防线。
    """
    monkeypatch.setenv("AF_ALLOW_NOAUTH", "1")
    client = TestClient(build_app(store_root=str(tmp_path)))
    r = client.post("/api/sim", json={"ir": {"name": "x"}, "events": [{"e": 1}] * 10001})
    assert r.status_code == 422, r.text
    assert "10000" in r.json()["detail"]


def test_api_sim_accepts_events_exactly_at_the_cap(tmp_path, monkeypatch):
    """上限是"超过才拒"：恰好 10000 条必须放行到服务层（证明拒的是条数，不是整条路由）。"""
    monkeypatch.setenv("AF_ALLOW_NOAUTH", "1")
    client = TestClient(build_app(store_root=str(tmp_path)))
    r = client.post("/api/sim", json={"ir": {"name": "x"}, "events": [{"e": 1}] * 10000})
    assert r.status_code != 422, r.text
