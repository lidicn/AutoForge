# -*- coding: utf-8 -*-
"""AF-808 判据 · af_apply 通道的提交主体透传（MA 联调窗口现场发现，2026-11-17）。

**缺陷形状**：`af_mcp._t_apply` 既不在 `_TOOLS_WITH_CONTEXT` 里（`dispatch` 于是走
`fn(store, args)`，拿不到 `current`），调 `af_apply.apply` 时也没递 `authenticated_subject`
⇒ `apply` 把入队 `submitted_by` 写死成**工具名** `"af_apply"`（`af_apply.py:314`），
而服务层只认 `effective_submitter = authenticated_subject or submitted_by`
（`af_service.py:1363`）⇒ pending 记录的提交人不等于任何令牌主体 ⇒
AF-734「agent 仅撤回自己提交的」（`af_api.py:841-849`）对 af_apply 通道**永久 403**：
**提交人自己也撤不回自己刚提交的 op**。同一条路径把归档 `owner` 也写成空串
（`af_apply.py:308`）。

**修法（AF-PM 裁的单点修）**：`_t_apply` 收 `current`、进 `_TOOLS_WITH_CONTEXT`、
把令牌 `subject` 当 `authenticated_subject` 递给 `apply` —— 与 `_t_save`（af_save 通道）
同口径；⛔ 不动 AF-734 判定本身、⛔ 不动 insight/approve 链、⛔ 不新造码。

判据（AF-PM 卡面四条，逐条落腿）：

- A1 `af_apply(stage=save)` 提交的 pending op 的 `submitted_by` ＝ 调用方主体（真队列读回）
- A2 该主体撤回自己的 op ⇒ **200**（修前 403）
- A3 他人提交的 op，非 owner 撤回 ⇒ 仍 **403**（⛔ 不放开越权；owner 面仍 200）
- A4 回归：af_save 通道仍带 subject；无令牌档仍回落工具名（旧行为一字不变）
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from autoforge import af_mcp, af_service
from autoforge.af_api import build_app
from autoforge.af_draft import _staging
from autoforge.af_mcp import dispatch
from autoforge.af_ir import load_graph
from autoforge.af_store import GraphStore

NON_OWNER = "dsh-pm"
OWNER_SUBJECT = "owner"
OWNER_OF_ALL = "dsh"
TOKENS_ENV = "AF_ACCESS_TOKENS"
AGENT = {"subject": NON_OWNER, "scopes": ["read", "write"]}


def _ir(name: str = "af808_target") -> dict:
    """带入口节点的最小 IR：入队要过静态扫描（⛔ 不是"随便一条"）。"""
    return {
        "ir_version": "0.2.1", "id": name, "name": name, "version": 1, "mode": "single",
        "nodes": [
            {"id": "t1", "kind": "on", "name": "t",
             "trigger": {"type": "state", "entity_id": "light.x", "to": "on"}},
            {"id": "p1", "kind": "pass", "name": "p"},
        ],
        "edges": [{"from": "t1", "to": "p1", "kind": "then"}],
    }


@pytest.fixture()
def ref() -> str:
    """走真 staging 管线拿一枚 ref（⛔ 不手搓 ref 字符串）。"""
    return _staging.put(load_graph({"automations": [_ir()]}), "af808_target", {})


@pytest.fixture()
def short_circuit(monkeypatch):
    """短路 build/simulate（不连 catalog/HA），让 submit_pending 走真实现。"""
    monkeypatch.setattr(af_service, "build",
                        lambda ir, store=None, use_catalog=True: {"ok": True})
    monkeypatch.setattr(af_service, "simulate",
                        lambda ir, seed=None, events=None, store=None:
                        {"ok": True, "instances": [{"state": "done", "current_node": "p1"}]})


def _apply_via_mcp(store: GraphStore, ref: str, current: dict | None) -> dict:
    content, is_error = dispatch("af_apply", {"ref": ref, "stage": "save"}, store, current)
    body = json.loads(content[0]["text"])
    assert is_error is False, body
    assert body.get("ok") is True, body
    return body["save"]


def _pending_row(store: GraphStore, op_id: str) -> dict:
    items = af_service.list_pending(store, None).get("items", [])
    row = next((r for r in items if str(r.get("op_id") or r.get("id") or "") == op_id), None)
    assert row is not None, f"队列里找不到 {op_id}：{[r.get('op_id') for r in items]}"
    return row


def _client(monkeypatch, store: GraphStore, tokens: dict) -> TestClient:
    monkeypatch.setenv(TOKENS_ENV, json.dumps(tokens))
    monkeypatch.delenv("AF_ALLOW_NOAUTH", raising=False)
    return TestClient(build_app(store_root=str(store.root)))


# ── A1：入队主体＝调用方主体 ────────────────────────────────────────────

def test_a1_af_apply_pending_records_the_callers_subject(tmp_path, short_circuit, ref):
    store = GraphStore(str(tmp_path / "store"))
    save = _apply_via_mcp(store, ref, AGENT)

    # 回执自报 + 真队列读回两段都核（⛔ 不信回执一面）
    assert save["submitted_by"] == NON_OWNER, save
    row = _pending_row(store, save["pending"])
    assert row["submitted_by"] == NON_OWNER, row
    assert row["submitted_by"] != "af_apply", "修前那条形状：提交人落成工具名"


def test_a1b_owner_subject_also_lands_in_the_pending_record(tmp_path, short_circuit, ref):
    """同一枚 subject 也写进 `save_payload["owner"]`（归档归属不再是空串）。"""
    store = GraphStore(str(tmp_path / "store"))
    save = _apply_via_mcp(store, ref, {"subject": OWNER_OF_ALL, "scopes": ["read", "write"]})
    assert save["submitted_by"] == OWNER_OF_ALL
    from autoforge.af_pending import PendingStore

    op = PendingStore(store.root).load(save["pending"])
    payload = op.get("payload") or {}
    assert payload.get("owner") == OWNER_OF_ALL, payload


# ── A2：自己的 op 撤得回（修前 403） ────────────────────────────────────

def test_a2_caller_can_reject_its_own_af_apply_op(tmp_path, monkeypatch, short_circuit, ref):
    store = GraphStore(str(tmp_path / "store"))
    save = _apply_via_mcp(store, ref, AGENT)
    client = _client(monkeypatch, store, {"tok-agent": AGENT})

    r = client.post("/api/pending/reject",
                    headers={"Authorization": "Bearer tok-agent"},
                    json={"op_id": save["pending"], "reason": "撤回自己的"})
    assert r.status_code == 200, f"agent 撤回 af_apply 提交的自己的 op ⇒ 200，实得 {r.status_code}：{r.text[:200]}"
    assert not any(str(row.get("op_id")) == save["pending"]
                   for row in af_service.list_pending(store, None).get("items", []))


# ── A3：越权仍然 403（⛔ 不放开） ──────────────────────────────────────

def test_a3_foreign_submitted_op_still_403_for_non_owner(tmp_path, monkeypatch, short_circuit, ref):
    from autoforge.af_pending import PendingStore

    store = GraphStore(str(tmp_path / "store"))
    own = _apply_via_mcp(store, ref, AGENT)["pending"]           # 自己提交的
    foreign = PendingStore(store.root).submit(
        "af_save",
        payload={"ir": _ir("foreign"), "name": "foreign", "note": "", "tags": None,
                 "expect_version": None, "owner": ""},
        summary="他人提交", blast_radius={"total": 1},
        submitted_by=OWNER_OF_ALL, agent_key=OWNER_OF_ALL,
    )
    foreign_id = foreign.op_id if hasattr(foreign, "op_id") else foreign["op_id"]

    client = _client(monkeypatch, store, {"tok-agent": AGENT})
    hdr = {"Authorization": "Bearer tok-agent"}

    r_other = client.post("/api/pending/reject", headers=hdr,
                          json={"op_id": foreign_id, "reason": "x"})
    assert r_other.status_code == 403, f"agent 驳回他人提交 ⇒ 403，实得 {r_other.status_code}"
    assert "无权" in r_other.json()["detail"]

    # 反向自证：同一条腿里自己的 op 仍 200（不然"403"可能只是端点坏了）
    r_own = client.post("/api/pending/reject", headers=hdr,
                        json={"op_id": own, "reason": "撤回"})
    assert r_own.status_code == 200, f"自己提交的仍应 200，实得 {r_own.status_code}"


def test_a3b_owner_face_can_still_reject_anyones_op(tmp_path, monkeypatch, short_circuit, ref):
    """owner 面语义未动：驳回＝owner 全部（AF-734 原文）。"""
    from autoforge.af_pending import PendingStore

    store = GraphStore(str(tmp_path / "store"))
    foreign = PendingStore(store.root).submit(
        "af_save",
        payload={"ir": _ir("foreign2"), "name": "foreign2", "note": "", "tags": None,
                 "expect_version": None, "owner": ""},
        summary="他人提交", blast_radius={"total": 1},
        submitted_by=NON_OWNER, agent_key=NON_OWNER,
    )
    foreign_id = foreign.op_id if hasattr(foreign, "op_id") else foreign["op_id"]
    client = _client(monkeypatch, store, {
        "tok-owner": {"subject": OWNER_SUBJECT, "scopes": ["read", "write", "live"]}})
    r = client.post("/api/pending/reject",
                    headers={"Authorization": "Bearer tok-owner"},
                    json={"op_id": foreign_id, "reason": "owner 驳回"})
    assert r.status_code == 200, f"owner 驳回他人 op ⇒ 200，实得 {r.status_code}"


# ── A4：回归（af_save 通道 ＋ 无令牌原型档） ────────────────────────────

def test_a4a_af_save_channel_still_carries_the_subject(tmp_path):
    """af_save 是本笔的对照面：口径一字未改（subject 直传）。"""
    store = GraphStore(str(tmp_path / "store"))
    content, is_error = dispatch("af_save", {"ir": _ir("af808_save"), "name": "af808_save"},
                                 store, AGENT)
    body = json.loads(content[0]["text"])
    assert is_error is False, body
    assert body["submitted_by"] == NON_OWNER, body


def test_a4b_no_token_prototype_keeps_the_tool_name_fallback(tmp_path, short_circuit, ref):
    """`current is None`（本地原型档）传空串 ⇒ 服务层回落 `submitted_by`，旧行为保留。"""
    store = GraphStore(str(tmp_path / "store"))
    res = af_mcp._t_apply(store, {"ref": ref, "stage": "save"})
    assert res["save"]["submitted_by"] == "af_apply", res["save"]


def test_a4c_apply_and_save_agree_on_the_owner_key(tmp_path, short_circuit, ref):
    """两条通道写进 payload 的归属键必须是同一枚（`owner`），⛔ 不许各写一套。"""
    from autoforge.af_pending import PendingStore

    store = GraphStore(str(tmp_path / "store"))
    save = _apply_via_mcp(store, ref, AGENT)
    ps = PendingStore(store.root)
    op = ps.load(save["pending"])
    payload = op.get("payload") or {}
    assert payload.get("owner") == NON_OWNER, payload
    assert "owner_subject" not in payload and "owned_by" not in payload


def test_a4d_af_apply_is_registered_as_a_context_tool():
    """结构钉：漏了 `_TOOLS_WITH_CONTEXT` 这一格，subject 就永远递不进来（本缺陷的成因）。"""
    assert af_mcp._t_apply in af_mcp._TOOLS_WITH_CONTEXT
