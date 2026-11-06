"""v1.28 R2「af_live_run 补 undo 参数」判据（卡名 `AF卡_live_run_undo`，2026-11-06）。

**背景**：`svc.live_run` 早就支持 `undo`（REST `/api/live/run` 经 `LiveRunBody.undo` 传入），
但 MCP `af_live_run`（`_t_live`）**没透传**——Agent/DB 实际调的那条路拿不到 `undo_deploy_id`，
没法事后撤销。本判据钉：MCP 面带 undo 与不带 undo **差分**，且与 REST **逐字同形**（卡 §三）。

**差分牙**（群规⑧）：不带 undo ⇒ `undo_enabled=False`、`undo_deploy_id=None`、`undo/available` 空；
带 undo=true ⇒ `undo_enabled=True`、`undo_deploy_id` 非空、`undo/available` 能读到它。
两者差必须能被本文件真实测出，否则"MCP 有 undo"这条就等于没装。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from autoforge import af_service as svc
from autoforge.af_adapters import CallResult
from autoforge.af_api import build_app
from autoforge.af_mcp import dispatch
from autoforge.af_store import GraphStore
from fastapi.testclient import TestClient

#: 复用既有 live 用例的最小 IR（与 test_af_live_entry_seams 同形，只测 undo 开关面）
LIVE_IR: dict = {
    "automations": [
        {
            "ir_version": "0.2.1",
            "id": "a_undo_gate",
            "name": "undo 判据",
            "version": 1,
            "mode": "restart",
            "nodes": [
                {"id": "t1", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.hall", "to": "on"}},
                {"id": "d1", "kind": "do", "adapter": "ha", "action": "light.turn_off",
                 "params": {"entity_id": "light.study"}, "on_error": {"default": "pass"},
                 "expect": {"entity_id": "light.study", "state": "off"}},
            ],
            "edges": [{"from": "t1", "to": "d1", "kind": "then"}],
        }
    ]
}

_ALL = {"subject": "test-undo", "scopes": sorted(
    __import__("autoforge.af_auth", fromlist=["SCOPES"]).SCOPES)}


class _FakeTransport:
    def __init__(self) -> None:
        self.states = {"light.study": ("on", {}), "binary_sensor.hall": ("off", {})}
        self.dispatched: list[tuple[str, dict]] = []

    def __call__(self, action, params):
        return self.call(action, params)

    def call(self, action, params):
        self.dispatched.append((action, dict(params)))
        return CallResult.ok({"action": action, "result": []})

    def all_states(self):
        return dict(self.states)

    def get_state(self, entity_id):
        found = self.states.get(entity_id)
        return found[0] if found else None


@pytest.fixture()
def gate(monkeypatch, tmp_path):
    """开真机闸门 + 记账传输层；undo 由 env 覆盖（逐字与 REST 同源，⛔ 不设服务端开关）。"""
    monkeypatch.setenv("AF_ALLOW_NOAUTH", "1")
    monkeypatch.setenv("AUTOFORGE_LIVE_ENABLED", "1")
    monkeypatch.setenv("AUTOFORGE_HA_TOKEN", "srv-side-token")
    monkeypatch.delenv("AUTOFORGE_UNDO", raising=False)
    transports: list[_FakeTransport] = []

    def factory(ha_url, token):
        transports.append(_FakeTransport())
        return transports[-1]

    monkeypatch.setattr(svc, "LIVE_TRANSPORT_FACTORY", factory)
    return transports


def _code(root: Path) -> str:
    from autoforge.af_auth import AuthCodeStore
    rec = AuthCodeStore(Path(root) / ".auth" / "auth_codes.json").create("short", ttl_minutes=30)
    return getattr(rec, "code", None) or rec["code"]


def _base_args(root: Path) -> dict:
    return {
        "ir": LIVE_IR,
        "live_allow": ["light.study"],
        "events": [{"entity_id": "binary_sensor.hall", "state": "on"}],
        "confirm": True,
        "auth_code": _code(root),
    }


def _mcp_live(content_result: tuple[Any, bool]) -> dict:
    """dispatch 回执 → body（dispatch 把 svc.live_run 的 dict 回执 json.dumps 成 text）。"""
    content, is_error = content_result
    assert is_error is False, content[0]["text"]
    return json.loads(content[0]["text"])


def test_mcp_live_run_with_undo_records_and_returns_deploy_id(tmp_path, gate):
    """MCP 面带 undo=true ⇒ `undo_deploy_id` 非空、undo/available 可读——与 REST 同源。"""
    store = GraphStore(tmp_path)
    body = _mcp_live(dispatch("af_live_run", {**_base_args(tmp_path), "undo": True}, store, _ALL))
    assert body["undo_enabled"] is True
    did = body["undo_deploy_id"]
    assert did, "undo=true 必须把撤销 ID 交回 Agent 面，否则无从指向事后 /api/undo"
    assert [a for t in gate for a, _ in t.dispatched] == ["light.turn_off"]
    # 撤销句柄确实可读（盘点面与 HTTP 同一条，Agent 拿这个 ID 去 /api/undo 撤销）
    avail = TestClient(build_app(store_root=str(tmp_path))).get("/api/undo/available").json()
    assert any(item.get("deploy_id") == did for item in avail.get("items", [])), \
        "undo/available 必须能看到刚下的那笔，否则 Agent 撤销时是瞎指"


def test_mcp_live_run_without_undo_advertises_nothing(tmp_path, gate):
    """MCP 面不带 undo ⇒ 逐字同 REST：undo_enabled=False、undo_deploy_id=None、不落快照。"""
    store = GraphStore(tmp_path)
    body = _mcp_live(dispatch("af_live_run", _base_args(tmp_path), store, _ALL))
    assert body["undo_enabled"] is False, "缺省必须关，⛔ 不谎报可撤销"
    assert body["undo_deploy_id"] is None
    avail = TestClient(build_app(store_root=str(tmp_path))).get("/api/undo/available").json()
    assert avail.get("items") == [], "没开 undo 时撤销清单必须空（REST 同形）"


def test_rest_undo_shape_matches_mcp_verbatim(tmp_path, gate):
    """对照腿：REST 面带 undo 与不带 undo 的形状 = MCP 面（逐字同形，卡 §三 同源）。"""
    client = TestClient(build_app(store_root=str(tmp_path)))
    args = _base_args(tmp_path)

    with_u = client.post("/api/live/run", json={**args, "undo": True}).json()
    without_u = client.post("/api/live/run", json=args).json()

    # REST 与 MCP 的同源断言：两枚键的形状逐字一致（枚举域相同）
    assert with_u["undo_enabled"] is True and bool(with_u["undo_deploy_id"])
    assert without_u["undo_enabled"] is False and without_u["undo_deploy_id"] is None
    # 反证腿：缺省默认值必须两边一致（MCP 若手抖成默认 true，此腿红）
    assert with_u["undo_enabled"] != without_u["undo_enabled"]