# -*- coding: utf-8 -*-
"""判据 D5③ · 真机单发只认短期码（2026-11-05）· 卡名 AF卡_live_short_code_only。

AF-PM 裁定 D5 三门之③：**单发只认短期码**，长期码只准用于部署。
边界（Chief 口径）：本条只管单发通道，⛔ 不碰 watch 常驻执行/部署主路。

D 编号带卡名（群规⑭）：
  D1 长期码 + 单发 ⇒ 403 且文案指明「单发只认短期码」
  D2 短期码 + 单发 ⇒ ⛔ 不被这条闸拦（错误里不带 D5③ 文案）
  D3 短期码 + 单发**第二次** ⇒ 403（AF-753 收口：短期码一次性，validate 通过≠可复用）
  D4 源码钉：live_run 的短期码通道必须真的 `acs.consume`（防后人删回去）
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


def test_d3_short_code_is_one_shot(env):
    """AF-753（v1.29 收口）：短期码是**一次性**凭据——同一枚码第二次单发必须被拒。

    修前：`validate` 只校验不消费，同一枚短期码在 TTL 窗口内可反复单发（60s 只是把窗口
    压短，不是闭环）。修后：闸内 `acs.consume` 把码烧掉，第二次落到「授权码无效」。
    """
    ac = env["acs"].create("short", ttl_minutes=30)
    _run(env, ac.code)  # 第一次：放行并核销
    with pytest.raises(ServiceError) as ei:
        _run(env, ac.code)
    assert "授权码无效" in str(ei.value), f"短期码复用必须拒，实得：{ei.value}"
    assert ei.value.status == 403


def test_d4_consume_site_pinned():
    """源码钉（AF-753）：`live_run` 的短期码通道必须真的核销，⛔ 只 validate。

    为什么钉源码：这条闸的语义就是"一次性"，删掉那一行不会有任何报错，只会在线上
    悄悄退回"短期码可复用"——正是 AF-753 的现场。
    """
    import inspect

    src = inspect.getsource(svc.live_run)
    assert "acs.consume(auth_code)" in src, "live_run 的短期码通道未核销（AF-753 回退）"
