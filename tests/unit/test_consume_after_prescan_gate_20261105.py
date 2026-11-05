# -*- coding: utf-8 -*-
"""判据 D3 · 授权码消耗必须排在「提交能过闸」之后（2026-11-05）· 卡名 AF卡_consume_after_prescan。

实证：`af_mcp._t_save` 的快速通道**先 consume 再 submit_pending（扫描在里面）**⇒ 结构非法/扫描不过的
IR 会先把码烧掉再报错（`32892011` 白烧一枚）。DCD/Chief 口径：validate → **只读预扫描** → 通过才 consume。

D 编号带卡名（群规⑭）：
  D1 结构非法 IR + 有效短期码 ⇒ 抛错且**码未被消耗**（可继续用）
  D2 扫描不过（结构合法但静态错误）⇒ 同样不烧码
  D3 正常 IR + 有效码 ⇒ 照常部署，且码被消耗（正例，防「干脆不消耗」的假修）
  D4 预扫描与入队闸同一份实现（源码钉：submit_pending 调 precheck_save_ir）
"""

from __future__ import annotations

import json

import pytest

from autoforge import af_mcp
from autoforge.af_auth import AuthCodeStore
from autoforge.af_service import ServiceError, precheck_save_ir
from autoforge.af_store import GraphStore

GOOD_IR = {
    "ir_version": "0.2.1", "id": "ok_case", "name": "ok_case", "version": 1, "mode": "single",
    "nodes": [
        {"id": "t1", "kind": "on", "name": "t",
         "trigger": {"type": "state", "entity_id": "binary_sensor.x", "to": "on"}},
        {"id": "p1", "kind": "pass", "name": "p"},
    ],
    "edges": [{"from": "t1", "to": "p1", "kind": "then"}],
}

BAD_STRUCT = {"ir_version": "0.2.1", "id": "bad", "name": "bad", "nodes": "not-a-list"}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    store = GraphStore(str(tmp_path / "store"))
    acs = AuthCodeStore(Path_ := store.root / ".auth" / "auth_codes.json")  # noqa: F841
    monkeypatch.setattr(af_mcp, "_MCP_AUTH_STORE", acs, raising=False)
    return {"store": store, "acs": acs, "current": {"subject": "dsh", "scopes": ["read", "write"]}}


def _call_save(env, ir, code):
    return af_mcp._t_save(
        env["store"],
        {"ir": ir, "name": "case", "note": "", "auth_code": code},
        env["current"],
    )


# ── D1 结构非法 ⇒ 不烧码 ────────────────────────────────────────
def test_d1_bad_structure_does_not_burn_code(env):
    ac = env["acs"].create("short", ttl_minutes=10)
    with pytest.raises(ServiceError):
        _call_save(env, BAD_STRUCT, ac.code)
    assert env["acs"].validate(ac.code) is True, "结构非法时⛔ 不许消耗授权码"


# ── D2 扫描不过 ⇒ 不烧码 ────────────────────────────────────────
def test_d2_scan_failure_does_not_burn_code(env):
    bad = json.loads(json.dumps(GOOD_IR))
    bad["id"] = "bad_scan"
    bad["nodes"][0]["trigger"] = {"type": "state"}      # 缺 entity_id ⇒ 扫描错误
    ac = env["acs"].create("short", ttl_minutes=10)
    with pytest.raises(ServiceError):
        _call_save(env, bad, ac.code)
    assert env["acs"].validate(ac.code) is True, "扫描不过时⛔ 不许消耗授权码"


# ── D3 正例：正常 IR 仍要消耗 ────────────────────────────────────
def test_d3_good_ir_still_consumes(env):
    ac = env["acs"].create("short", ttl_minutes=10)
    out = _call_save(env, GOOD_IR, ac.code)
    assert out.get("deployed_via") == "auth_code"
    assert env["acs"].validate(ac.code) is False, "正常部署路径必须照旧消耗短期码（一次性）"


# ── D4 单一实现钉 ───────────────────────────────────────────────
def test_d4_single_scan_implementation():
    import inspect

    from autoforge import af_service

    src = inspect.getsource(af_service.submit_pending)
    assert "precheck_save_ir" in src, "入队闸必须复用预扫描（⛔ 两份扫描实现会判出两种结果）"
    mcp_src = inspect.getsource(af_mcp._t_save)
    assert "precheck_save_ir" in mcp_src
    assert mcp_src.index("precheck_save_ir") < mcp_src.index("acs.consume("), \
        "预扫描必须在 consume 之前"
