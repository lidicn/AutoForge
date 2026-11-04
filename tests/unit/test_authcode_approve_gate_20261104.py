# -*- coding: utf-8 -*-
"""P0 判据 · auth_code 部署路径被 AF-734 owner gate 堵死 → approve 双通道修复（2026-11-04）。

出资人原始设计：部署审批双路径——① owner 在 WebUI 点批准 ② **agent 持授权码直接部署**
（授权码＝用户批准；短期码一次性、长期码可复用）。v1.21.0 的 AF-734 在 `/api/pending/approve`
加了 owner 专属门，**没考虑授权码路径** ⇒ agent 持有效码也被 403。本卡修双通道：
  - 令牌主体 ∈ owner 面 ⇒ 放行（路径 1 不变）
  - 非 owner 但 body 带**有效 auth_code**（validate 通过、未过期、未消耗）⇒ 放行并 **consume**，
    审计记 `authorized_by=auth_code` / `deployed_via=auth_code`
  - 两个都不过 ⇒ 403

D 编号带卡名（群规⑭）：
  D1 owner 令牌 + 无码 ⇒ 200
  D2 agent 令牌 + 有效短期码 ⇒ 200 且码被 consume（一次性；复用再批 ⇒ 拒）
  D3 agent 令牌 + 无码 ⇒ 403
  D4 agent 令牌 + 过期码 ⇒ 403
  D5 agent 令牌 + 已消耗码 ⇒ 403
  D6 变异自证（见文件尾注释 + 报告；桩外在本文件用「去掉 auth_code 通道」的临时树副本跑，另附）
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from autoforge.af_api import build_app
from autoforge.af_auth import AuthCodeStore
from autoforge.af_ir import load_graph
from autoforge.af_pending import PendingStore
from autoforge.af_store import GraphStore

NON_OWNER = "dsh-pm"
OWNER_SUBJECT = "owner"
OWNER_OF_ALL = "dsh"


def _ir_savable(name: str) -> dict:
    """审批落盘过静态扫描 ⇒ 必须带入口节点（否则 NO_ENTRY_NODE 400，与审批权无关）。"""
    return {
        "ir_version": "0.2.1", "id": name, "name": name, "version": 1, "mode": "single",
        "nodes": [
            {"id": "t1", "kind": "on", "name": "t",
             "trigger": {"type": "state", "entity_id": "light.x", "to": "on"}},
            {"id": "p1", "kind": "pass", "name": "p"},
        ],
        "edges": [{"from": "t1", "to": "p1", "kind": "then"}],
    }


def _submit_pending(store, name: str, submitted_by: str) -> str:
    ps = PendingStore(store.root)
    op = ps.submit(
        "af_save",
        payload={"ir": _ir_savable(name), "name": name, "note": "", "tags": None,
                 "expect_version": None, "owner": ""},
        summary=f"测试待批 {name}",
        blast_radius={"total": 1},
        submitted_by=submitted_by,
        agent_key=submitted_by,
    )
    return op.op_id if hasattr(op, "op_id") else op["op_id"]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    store = GraphStore(str(tmp_path / "store"))
    tokens = {
        "tok-agent": {"subject": NON_OWNER, "scopes": ["read", "write"]},
        "tok-owner": {"subject": OWNER_SUBJECT, "scopes": ["read", "write", "live"]},
        "tok-dsh": {"subject": OWNER_OF_ALL, "scopes": ["read", "write"]},
    }
    monkeypatch.setenv("AF_ACCESS_TOKENS", json.dumps(tokens))
    monkeypatch.delenv("AF_ALLOW_NOAUTH", raising=False)
    client = TestClient(build_app(store_root=str(store.root)))
    return {
        "client": client, "store": store,
        "agent": {"Authorization": "Bearer tok-agent"},
        "owner": {"Authorization": "Bearer tok-owner"},
        "dsh": {"Authorization": "Bearer tok-dsh"},
        "acs": AuthCodeStore(store.root / ".auth" / "auth_codes.json"),
    }


# ── D1 owner 令牌 + 无码 ⇒ 200（路径 1 不变）─────────────────────────
def test_d1_owner_no_code_approves(env):
    # 他人提交（避免 P0-13 自批）。owner 批他人 ⇒ 200。
    op = _submit_pending(env["store"], "g_d1", submitted_by="someone_else")
    r = env["client"].post("/api/pending/approve", headers=env["owner"], json={"op_id": op})
    assert r.status_code == 200, f"owner 无码批准他人待批 ⇒ 200，实得 {r.status_code}: {r.text}"
    assert env["store"].load("g_d1") is not None, "归档应已落盘"


# ── D2 agent + 有效短期码 ⇒ 200 且 consume（一次性）──────────────────
def test_d2_agent_valid_short_code_approves_and_consumes(env):
    ac = env["acs"].create("short", ttl_minutes=10)
    op = _submit_pending(env["store"], "g_d2", submitted_by="someone_else")
    r = env["client"].post(
        "/api/pending/approve", headers=env["agent"],
        json={"op_id": op, "auth_code": ac.code},
    )
    assert r.status_code == 200, f"agent 持有效码批准 ⇒ 200，实得 {r.status_code}: {r.text}"
    body = r.json()
    assert body.get("deployed_via") == "auth_code", "审计须记 deployed_via=auth_code"
    assert body.get("authorized_by") == "auth_code", "审计须记 authorized_by=auth_code"
    assert env["store"].load("g_d2") is not None, "归档应已落盘"
    # 短期码一次性：已消耗
    assert env["acs"].validate(ac.code) is False, "短期码批准后必须被 consume（一次性）"


# ── D3 agent + 无码 ⇒ 403 ──────────────────────────────────────────
def test_d3_agent_no_code_403(env):
    op = _submit_pending(env["store"], "g_d3", submitted_by="someone_else")
    r = env["client"].post("/api/pending/approve", headers=env["agent"], json={"op_id": op})
    assert r.status_code == 403, f"agent 无码批准 ⇒ 403，实得 {r.status_code}"
    # 待批未被吃掉（仍可被人审批）
    assert env["store"].latest("g_d3") is None, "被拒后⛔ 不得落盘"


# ── D4 agent + 过期码 ⇒ 403 ────────────────────────────────────────
def test_d4_agent_expired_code_403(env):
    ac = env["acs"].create("short", ttl_minutes=10)
    # 直接把盘上 expires_at 改到过去并让新实例重载（模拟过期）
    path = env["store"].root / ".auth" / "auth_codes.json"
    recs = json.loads(path.read_text(encoding="utf-8"))
    for rec in recs:
        if rec["code"] == ac.code:
            rec["expires_at"] = time.time() - 60
    path.write_text(json.dumps(recs), encoding="utf-8")
    env["acs"]._load()  # 让服务侧新 AuthCodeStore 也读到过期
    op = _submit_pending(env["store"], "g_d4", submitted_by="someone_else")
    r = env["client"].post(
        "/api/pending/approve", headers=env["agent"],
        json={"op_id": op, "auth_code": ac.code},
    )
    assert r.status_code == 403, f"过期码 ⇒ 403，实得 {r.status_code}: {r.text}"
    assert env["store"].latest("g_d4") is None, "过期码被拒后⛔ 不得落盘"


# ── D5 agent + 已消耗码 ⇒ 403 ──────────────────────────────────────
def test_d5_agent_consumed_code_403(env):
    ac = env["acs"].create("short", ttl_minutes=10)
    assert env["acs"].consume(ac.code) is True, "前置：先消耗该码"
    op = _submit_pending(env["store"], "g_d5", submitted_by="someone_else")
    r = env["client"].post(
        "/api/pending/approve", headers=env["agent"],
        json={"op_id": op, "auth_code": ac.code},
    )
    assert r.status_code == 403, f"已消耗码 ⇒ 403，实得 {r.status_code}: {r.text}"
    assert env["store"].latest("g_d5") is None, "已消耗码被拒后⛔ 不得落盘"


# ── D2b 长期码可复用（区别于短期码一次性）────────────────────────────
def test_d2b_agent_long_code_reusable(env):
    ac = env["acs"].create("long")
    op1 = _submit_pending(env["store"], "g_l1", submitted_by="someone_else")
    r1 = env["client"].post("/api/pending/approve", headers=env["agent"],
                            json={"op_id": op1, "auth_code": ac.code})
    assert r1.status_code == 200, f"长期码第 1 次批准 ⇒ 200，实得 {r1.status_code}: {r1.text}"
    # 长期码不消耗 ⇒ 仍有效、可第 2 次用
    assert env["acs"].validate(ac.code) is True, "长期码不得被一次性消耗"
    op2 = _submit_pending(env["store"], "g_l2", submitted_by="someone_else")
    r2 = env["client"].post("/api/pending/approve", headers=env["agent"],
                            json={"op_id": op2, "auth_code": ac.code})
    assert r2.status_code == 200, f"长期码第 2 次批准（复用）⇒ 200，实得 {r2.status_code}: {r2.text}"


# ── D6 牙齿（源码钉）：approve 端点确有 auth_code 双通道分支 ──────────
def test_d6_source_has_dual_channel():
    """approve 处理器源码必须含 auth_code 通道（validate/consume/authorized_by），防后人删回去。"""
    import inspect

    from autoforge import af_api

    src = inspect.getsource(af_api.build_app)
    # 定位 api_pending_approve 函数体（build_app 是内嵌定义）
    assert "authorized_by" in src and 'result["deployed_via"] = "auth_code"' in src, \
        "approve 端点缺 auth_code 双通道标记（authorized_by / deployed_via）"
    assert "acs.consume(code)" in src or "acs.consume(" in src, "approve 端点未经 consume 消耗码"
