"""真机下发有**两个入口面**：HTTP `/api/live/run` 与 MCP `af_live_run`（Agent/DB 实际调的那条）。
两者都落到 `af_service.live_run`，所以闸门的判据必须挂在服务层，而不是挂在某个入口的处理函数里。

本批盘出来的两处"只装了一个入口"：

1. `af_mcp._t_live(store, args)` 收下了 `store` 参数却**没有递进** `live_run`。
   Tier-0 设备保护（`{store_root}/device_acl.json`，"写入须经人工审批"）与实体健康视图都是从
   store 根目录读盘装配的，于是同一份 ACL 下：人在 WebUI 点同一个按钮被 400 拦下，
   Agent 走 MCP 直接把保护设备写了。
2. events 条数上限（`8e8c725` 的内存回放 DoS 收敛）原先是 `af_api._check_event_cap`，
   只在 HTTP 请求边界上判。MCP 的 `af_live_run`/`af_sim` 调的是同一批函数，却不经过那条边界。

判据形状：先证明"这个 harness 真的会下发"（无 ACL 的对照组必须有 dispatched），再证明
"设了 ACL 就一次都不许下发"。只写后一条的话，传输层工厂压根没被调用也能让测试变绿——
那是假绿，不是闸门。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from autoforge import af_service as svc
from autoforge.af_adapters import CallResult
from autoforge.af_auth import SCOPES as _ALL_SCOPES

# 裁定 20261004 §一 Q2=B：MCP 面「无身份」改成默认拒绝，本文件的调用点因此逐条显式给身份；
# 「不给身份」那一档只由 test_dcd_20261004_mcp_default_deny.py 钉成"拒绝"。
_ALL = {"subject": "test-all", "scopes": sorted(_ALL_SCOPES)}
from autoforge.af_api import build_app
from autoforge.af_mcp import dispatch
from autoforge.af_store import GraphStore
from fastapi.testclient import TestClient

LIVE_IR: dict = {
    "automations": [
        {
            "ir_version": "0.2.1",
            "id": "a_guard",
            "name": "a_guard",
            "version": 1,
            "mode": "restart",
            "nodes": [
                {
                    "id": "t1",
                    "kind": "on",
                    "trigger": {"type": "state", "entity_id": "binary_sensor.hall", "to": "on"},
                },
                {
                    "id": "d1",
                    "kind": "do",
                    "adapter": "ha",
                    "action": "light.turn_off",
                    "params": {"entity_id": "light.study"},
                    "on_error": {"default": "pass"},
                    "expect": {"entity_id": "light.study", "state": "off"},
                },
            ],
            "edges": [{"from": "t1", "to": "d1", "kind": "then"}],
        }
    ]
}

ARGS: dict[str, Any] = {
    "ir": LIVE_IR,
    "live_allow": ["light.study"],
    "events": [{"entity_id": "binary_sensor.hall", "state": "on"}],
    "confirm": True,
}


class FakeTransport:
    def __init__(self):
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
    """开真机闸门 + 记录每一次实际构造的传输层（= 真的碰过设备）。"""
    monkeypatch.setenv("AF_ALLOW_NOAUTH", "1")
    monkeypatch.setenv("AUTOFORGE_LIVE_ENABLED", "1")
    monkeypatch.setenv("AUTOFORGE_HA_TOKEN", "srv-side-token")
    monkeypatch.delenv("AUTOFORGE_UNDO", raising=False)
    transports: list[FakeTransport] = []

    def factory(ha_url, token):
        transports.append(FakeTransport())
        return transports[-1]

    monkeypatch.setattr(svc, "LIVE_TRANSPORT_FACTORY", factory)
    return transports


def _protect(root: Path, entity_id: str) -> None:
    """Tier-0 = 必须人审：scanner 侧是 ERROR，`live_run` 的第一道闸就该拒收。"""
    rules = [{"match": {"type": "entity", "value": entity_id}, "tier": 0, "perm": "-"}]
    Path(root, "device_acl.json").write_text(json.dumps(rules), encoding="utf-8")


def test_control_without_acl_the_mcp_path_really_dispatches(tmp_path, gate):
    """对照组：不设 ACL 时 MCP 这条路确实会下发，否则下面的"被拦住"可以是 harness 坏了。"""
    store = GraphStore(tmp_path)
    content, is_error = dispatch("af_live_run", ARGS, store, _ALL)
    assert is_error is False, content[0]["text"]
    assert [a for t in gate for a, _ in t.dispatched] == ["light.turn_off"]


def test_mcp_live_run_honours_the_store_device_guard(tmp_path, gate):
    """本批修的那根线：`_t_live` 必须把 `store` 递给 `live_run`，Tier-0 保护才装得上 Agent 面。"""
    store = GraphStore(tmp_path)
    _protect(tmp_path, "light.study")

    content, is_error = dispatch("af_live_run", ARGS, store, _ALL)

    assert is_error is True, "受保护设备被 Agent 面放行 ⇒ 闸门只装了 HTTP 一面"
    assert "ENTITY_GUARD_TIER0" in content[0]["text"]
    assert gate == [], "静态扫描就应拒收：连传输层都不许构造"


def test_http_live_run_honours_the_same_guard(tmp_path, gate):
    """同一份 ACL、同一次下发，人点的 WebUI 按钮判据必须与 Agent 面一致。"""
    _protect(tmp_path, "light.study")
    client = TestClient(build_app(store_root=str(tmp_path)))

    r = client.post("/api/live/run", json=ARGS)

    assert r.status_code == 400, r.text
    assert "ENTITY_GUARD_TIER0" in r.json()["detail"]
    assert gate == []


def test_mcp_live_run_refuses_oversized_events(tmp_path, gate):
    """上限挪进服务层后，MCP 面也拦得住整场回放（HTTP 侧的 422 判据由 test_af_api 钉）。"""
    store = GraphStore(tmp_path)
    args = {**ARGS, "events": [{"advance_s": 1}] * (svc.MAX_REPLAY_EVENTS + 1)}

    content, is_error = dispatch("af_live_run", args, store, _ALL)

    assert is_error is True
    assert "超过上限" in content[0]["text"]
    assert gate == []


def test_service_layer_itself_rejects_oversized_events():
    """判据的家在服务层：不经过任何 HTTP 入口，直接调 `simulate` 也要拿到 422 口径的拒收。"""
    with pytest.raises(svc.ServiceError) as ei:
        svc.simulate(LIVE_IR, {}, [{"advance_s": 1}] * (svc.MAX_REPLAY_EVENTS + 1))
    assert ei.value.status == 422
    assert "超过上限" in str(ei.value)
