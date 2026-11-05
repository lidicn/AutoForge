# -*- coding: utf-8 -*-
"""判据 D5③ · 真机单发只认短期码（2026-11-05）· 卡名 AF卡_live_short_code_only。

AF-PM 裁定 D5 三门之③：**单发只认短期码**，长期码只准用于部署。
边界（Chief 口径）：本条只管单发通道，⛔ 不碰 watch 常驻执行/部署主路。

D 编号带卡名（群规⑭）：
  D1 长期码 + 单发 ⇒ 403 且文案指明「单发只认短期码」
  D2 短期码 + 单发 ⇒ ⛔ 不被这条闸拦（错误里不带 D5③ 文案）
"""

from __future__ import annotations

import pytest

from autoforge import af_service as svc
from autoforge.af_auth import AuthCodeStore
from autoforge.af_service import ServiceError
from autoforge.af_store import GraphStore

IR = {
    "ir_version": "0.2.1", "id": "live_code", "name": "live_code", "version": 1, "mode": "single",
    "nodes": [
        {"id": "t1", "kind": "on", "name": "t",
         "trigger": {"type": "state", "entity_id": "light.x", "to": "on"}},
        {"id": "d1", "kind": "do", "name": "d", "adapter": "ha", "action": "light.turn_on",
         "params": {"entity_id": "light.x"}},
        {"id": "p1", "kind": "pass", "name": "p"},
    ],
    "edges": [{"from": "t1", "to": "d1", "kind": "then"},
              {"from": "d1", "to": "p1", "kind": "then"}],
}


class _NoopTransport:
    def __call__(self, action, params):
        from autoforge.af_adapters.base import CallResult
        return CallResult(success=True, data={}, error=None)

    def all_states(self):
        return {}

    def get_state(self, entity_id):
        return None


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOFORGE_LIVE_ENABLED", "1")
    monkeypatch.setenv("AUTOFORGE_HA_TOKEN", "srv-side-token")
    monkeypatch.delenv("HA_LONG_LIVED_TOKEN", raising=False)
    monkeypatch.delenv("AUTOFORGE_UNDO", raising=False)
    monkeypatch.setattr(svc, "LIVE_TRANSPORT_FACTORY", lambda ha_url, token: _NoopTransport())
    store = GraphStore(str(tmp_path / "store"))
    return {"store": store, "acs": AuthCodeStore(store.root / ".auth" / "auth_codes.json")}


def _run(env, code):
    return svc.live_run(
        IR, ["light.x"], None, confirm=True, store=env["store"], auth_code=code,
    )


def test_d1_long_code_rejected_for_single_shot(env):
    ac = env["acs"].create("long")
    with pytest.raises(ServiceError) as ei:
        _run(env, ac.code)
    assert "单发只认短期码" in str(ei.value), f"长期码必须被 D5③ 拦下，实得：{ei.value}"
    assert ei.value.status == 403


def test_d2_short_code_not_blocked_by_this_gate(env):
    ac = env["acs"].create("short", ttl_minutes=30)
    try:
        _run(env, ac.code)
    except ServiceError as exc:
        assert "单发只认短期码" not in str(exc), f"短期码⛔ 不该被 D5③ 拦，实得：{exc}"
