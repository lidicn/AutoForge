# -*- coding: utf-8 -*-
"""v1.21.0 治理面＋写面批判据（AF卡_v1.19_治理面与写面批_20261103，批号已改 v1.21.0）。

覆盖四件（D 编号**带卡名**，群规⑭）：
  ① AF-733 干预/删除限自己名下 —— 干预/删除端点先过读面单源闸
  ② AF-734 审批矩阵（agent 不能批待部署／agent 仅撤回自己提交／自审自批拒）
  ③ AF-732 sessions/asks/export 非 owner 看不到（读面＋写面同一道门）
  ④ AF-402 授权码 6~8 位三处声明一致 ＋ dispatch 真拦 5/9 位/含字母

角色（卡 §共同角色定义）：owner 面＝`{owner, shared}`；agent＝非 owner 且持 write。
⛔ 不改数据模型；⛔ 拒绝文案一律复用既有单源；⛔ 不碰真机三道闸。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from autoforge.af_api import build_app
from autoforge.af_ir import load_graph
from autoforge.af_store import GraphStore

NON_OWNER = "dsh-pm"
OWNER_SUBJECT = "owner"
OWNER_OF_ALL = "dsh"


def _ir(name: str) -> dict:
    return {
        "ir_version": "0.2.1", "id": name, "name": name, "version": 1, "mode": "single",
        "nodes": [{"id": "p1", "kind": "pass", "name": "p"}], "edges": [],
    }


@pytest.fixture()
def env(tmp_path, monkeypatch):
    store = GraphStore(str(tmp_path / "store"))
    store.save(load_graph(_ir("mine")), "mine", note="v1", owner=NON_OWNER)
    store.save(load_graph(_ir("theirs")), "theirs", note="v1", owner=OWNER_OF_ALL)
    tokens = {
        "tok-agent": {"subject": NON_OWNER, "scopes": ["read", "write"]},
        "tok-owner": {"subject": OWNER_SUBJECT, "scopes": ["read", "write", "live"]},
        "tok-dsh": {"subject": OWNER_OF_ALL, "scopes": ["read", "write"]},
    }
    monkeypatch.setenv("AF_ACCESS_TOKENS", json.dumps(tokens))
    monkeypatch.delenv("AF_ALLOW_NOAUTH", raising=False)
    client = TestClient(build_app(store_root=str(store.root)))
    yield {
        "client": client, "store": store,
        "agent": {"Authorization": "Bearer tok-agent"},
        "owner": {"Authorization": "Bearer tok-owner"},
        "dsh": {"Authorization": "Bearer tok-dsh"},
    }


# ── ① AF-733 干预权／删归档权（限自己名下） ──────────────────────────
# ⚠️ `svc.intervene` 的 automation_id 遍历的是 `a.id for a in graph`；桩里图 id＝归档名
# 即命中（判据只需 200/404，不关心节点种类）。

def test_af733_agent_intervene_foreign_404_own_200(env):
    r = env["client"].post(
        "/api/conf/theirs/intervene", headers=env["agent"], json={"automation_id": "theirs"}
    )
    assert r.status_code == 404, f"agent 干预他人归档 ⇒ 拒（与不存在同形），实得 {r.status_code}"
    assert "不属于你" in r.json()["detail"], "文案必须走 visible_error 单源（同形）"
    r2 = env["client"].post(
        "/api/conf/mine/intervene", headers=env["agent"], json={"automation_id": "mine"}
    )
    assert r2.status_code == 200, f"agent 干预自己归档 ⇒ 200，实得 {r2.status_code}"


def test_af733_owner_intervene_anyone_200(env):
    r = env["client"].post(
        "/api/conf/theirs/intervene", headers=env["owner"], json={"automation_id": "theirs"}
    )
    assert r.status_code == 200, "owner 干预任何人 ⇒ 200"


def test_af733_agent_delete_foreign_rejected_no_disk_change(env):
    before = env["store"].get_tags("theirs")
    r = env["client"].delete("/api/automations/theirs", headers=env["agent"])
    assert r.status_code in (403, 404), f"agent 删除他人归档 ⇒ 拒，实得 {r.status_code}"
    assert env["store"].get_tags("theirs") == before, "⛔ 被拒后盘上零变化"


# ── ② AF-734 审批矩阵 ─────────────────────────────────────────────

def _ir_savable(name: str) -> dict:
    """审批落盘会过静态扫描 ⇒ 必须带入口节点（否则 NO_ENTRY_NODE 400，与审批权无关）。"""
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
    from autoforge.af_pending import PendingStore

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


def test_af734_agent_cannot_approve_pending(env):
    """D1：agent 批准**他人提交**的待部署 ⇒ 拒（⚠️ 必须外人提交的；自批会被服务层
    P0-13 先拒，那证的是自批门不是 owner 专属门）。"""
    op_id = _submit_pending(env["store"], "otherapp", OWNER_OF_ALL)
    r = env["client"].post(
        "/api/pending/approve", headers=env["agent"], json={"op_id": op_id}
    )
    assert r.status_code == 403, f"agent 批准他人提交的 ⇒ 拒，实得 {r.status_code}"
    assert "禁止自批" not in r.json()["detail"], "此腿必须命中 owner 专属门，不是自批门"


def test_af734_agent_reject_own_ok_but_foreign_403(env):
    own_id = _submit_pending(env["store"], "ownop", NON_OWNER)
    r_own = env["client"].post(
        "/api/pending/reject", headers=env["agent"], json={"op_id": own_id, "reason": "撤销"}
    )
    assert r_own.status_code == 200, f"agent 撤回自己提交的 ⇒ 200，实得 {r_own.status_code}"
    other_id = _submit_pending(env["store"], "otherop", OWNER_OF_ALL)
    r_other = env["client"].post(
        "/api/pending/reject", headers=env["agent"], json={"op_id": other_id, "reason": "x"}
    )
    assert r_other.status_code == 403, f"agent 驳回他人提交 ⇒ 拒，实得 {r_other.status_code}"


def test_af734_owner_approve_self_submitted_rejected(env):
    """D5：自审自批（提交者=审批者）⇒ 拒（服务层 P0-13；owner 也拦）。"""
    op_id = _submit_pending(env["store"], "selfop", OWNER_SUBJECT)
    r = env["client"].post(
        "/api/pending/approve", headers=env["owner"], json={"op_id": op_id}
    )
    assert r.status_code == 403, "自审自批 ⇒ 拒"


def test_af734_owner_can_approve_others(env):
    """D3 正腿：owner 批准他人提交 ⇒ 200。"""
    op_id = _submit_pending(env["store"], "otherapprove", NON_OWNER)
    r = env["client"].post(
        "/api/pending/approve", headers=env["owner"], json={"op_id": op_id}
    )
    assert r.status_code == 200, f"owner 批他人提交 ⇒ 200，实得 {r.status_code}: {r.text[:120]}"


# ── ③ AF-732 sessions／asks／export 非 owner 看不到 ─────────────────

def test_af732_agent_cannot_read_sessions_or_export(env):
    assert env["client"].get("/api/sessions", headers=env["agent"]).status_code == 404
    assert env["client"].get("/api/store/export", headers=env["agent"]).status_code == 404


def test_af732_agent_cannot_write_sessions(env):
    r = env["client"].post(
        "/api/sessions", headers=env["agent"], json={"ir": _ir("s"), "seed": None, "events": None}
    )
    assert r.status_code == 404, "非 owner 写 sessions ⇒ 拒"


def test_af732_owner_full_access(env):
    assert env["client"].get("/api/sessions", headers=env["owner"]).status_code == 200
    assert env["client"].get("/api/store/export", headers=env["owner"]).status_code == 200


def test_af732_noauth_keeps_full_access(tmp_path, monkeypatch):
    store = GraphStore(str(tmp_path / "store9"))
    store.save(load_graph(_ir("solo")), "solo", note="v1", owner=OWNER_OF_ALL)
    monkeypatch.setenv("AF_ALLOW_NOAUTH", "1")
    client = TestClient(build_app(store_root=str(store.root)))
    assert client.get("/api/sessions").status_code == 200
    assert client.get("/api/store/export").status_code == 200


# ── ④ AF-402 授权码格式：三处声明一致 ＋ dispatch 真拦 ─────────────

def test_af402_declarations_unified():
    """D3：三处声明逐字 `^[0-9]{6,8}$`（⛔ 一处漏改就红）。"""
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "src" / "autoforge" / "af_mcp.py").read_text(
        encoding="utf-8"
    )
    pats = re.findall(r'"auth_code":\s*\{"type": "string", "pattern": "([^"]+)"', src)
    assert pats and all(p == "^[0-9]{6,8}$" for p in pats), pats


def test_af402_dispatch_rejects_bad_format_accepts_6_and_8():
    """D1/D2：5 位／9 位／含字母 ⇒ 被拒；6 位／8 位 ⇒ 过格式闸。"""
    from autoforge import af_mcp

    schema = {"properties": {"auth_code": {"type": "string", "pattern": "^[0-9]{6,8}$"}}}
    for bad in ("12345", "123456789", "abc123"):
        assert af_mcp._pattern_violations(schema, {"auth_code": bad}) == ["auth_code"], bad
    for good in ("123456", "12345678"):
        assert af_mcp._pattern_violations(schema, {"auth_code": good}) == [], good
