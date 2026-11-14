# -*- coding: utf-8 -*-
"""AF-787 判据 · 配对主体由服务端派生（元宝第五期 MCP-01，2026-11-14）。

**缺陷形状**：`redeem_pair_code` 里 `subject = caller 自报的 agent_name` ⇒ 一枚还没拿到任何
令牌的 agent，只要在 `af_pair(code, agent_name="owner")` 里自称 `owner`，就拿到的 subject 正好
落在 `_OWNER_SUBJECTS` 里 ⇒ **owner 面**（全量自动化、签发/撤销、部署通道）。主体是**授权**，
⛔ 不能让被授权方自己填。

**修法（AF-PM 已裁）**：

1. `subject = f"{hint}-{secrets.token_hex(3)}"`——hint 取配对码登记时那枚（:1186 口径
   strip／缺省），每次兑换都是新的、与服务端登记面绑定的身份；
2. caller 的 `agent_name` **降级成 label**（界面显示名，不参与任何鉴权）；
3. caller 名或 hint 落 `af_api._OWNER_SUBJECTS` ⇒ 拒发（HTTP 400），并落一条
   `TOKEN_ISSUED_REJECTED`（`data.reason=reserved_subject`，**复用**既有事件，不新开）。
   源头同闸：`request_pair_code` 是三条配对准入径的唯一实现，hint 保留名在这里就拒；
4. `ttl_s=0`（配对令牌不过期）与 `principal_kind` 分型**一字未动**；既有已签发令牌不动。

**判据**：

- A1 自报名 `owner` ⇒ 400、**不签发**、且那枚码**没被消费**（拒发不许顺手烧掉用户的码）
- A2 审计恰好 1 条 `TOKEN_ISSUED_REJECTED`，`data.reason == "reserved_subject"`
- A3 正常名 ⇒ `subject` 形如 `<hint>-<6 hex>`、`label` 是自报名、令牌可用、`principal_kind=agent`
- A4 源头闸：hint `owner`／`shared` ⇒ `/api/mcp/pair/request` 400 且**不落码**；pair-link 同
- A5 MCP `af_pair` 径同判（与 HTTP 面共用 `redeem_pair_code`）
- A6 历史/毒化记录（hint 落保留名的码）⇒ 兑换也拒（hint 侧闸）
- A7 确认流：兑换后 `/api/user/pair/{code}/confirm` 能按**真正签发的 subject** 找到人
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from autoforge import af_mcp
from autoforge.af_api import build_app
from autoforge.af_audit import AuditLog, TOKEN_ISSUED_REJECTED
from autoforge.af_auth import PairCodeStore, TokenRegistry, redeem_pair_code

QA = {"subject": "qa", "scopes": ["read", "write"]}
AGENT_TOKENS = {
    "svc-tok": {"subject": "svc", "scopes": ["read", "write", "live"]},
}
OWNER_TOKENS = {
    "owner-tok": {"subject": "owner", "scopes": ["read", "write", "live"]},
}
HINT = "书房助手"


def _client(tmp_path, monkeypatch, tokens=None) -> TestClient:
    for key in ("AUTOFORGE_TOKENS", "AF_ALLOW_NOAUTH", "AF_REQUIRE_AUTH"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AUTOFORGE_TOKENS", json.dumps(tokens or AGENT_TOKENS))
    return TestClient(build_app(str(tmp_path / "store")))


def _pair_store(tmp_path) -> PairCodeStore:
    return PairCodeStore(tmp_path / "store" / ".auth" / "pair_codes.json")


def _issued(tmp_path) -> Path:
    return tmp_path / "store" / ".auth" / "issued_tokens.json"


# ── A1 自报名 owner ⇒ 400 ＋ 不签发 ＋ 码没被消费 ───────────────────────
def test_a1_self_reported_owner_is_rejected_without_burning_the_code(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    pc = _pair_store(tmp_path).create(HINT)

    r = client.post("/api/mcp/pair/redeem", json={"code": pc.code, "agent_name": "owner"})

    assert r.status_code == 400, r.text
    assert "保留名" in r.text
    assert not _issued(tmp_path).is_file() or json.loads(_issued(tmp_path).read_text("utf-8")) == {}, (
        "被拒的兑换⛔ 不许在已签发名单里留东西"
    )
    rec = _pair_store(tmp_path).get(pc.code)
    assert rec is not None and rec["consumed"] is False, (
        "拒发必须在 peek 阶段返回：一次恶意自称不许顺手烧掉用户手里那枚码"
    )


def test_a1b_self_reported_shared_is_rejected_too(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    pc = _pair_store(tmp_path).create(HINT)
    r = client.post("/api/mcp/pair/redeem", json={"code": pc.code, "agent_name": "shared"})
    assert r.status_code == 400, r.text


# ── A2 审计恰好 1 条（复用既有事件，不新开码） ──────────────────────────
def test_a2_rejection_leaves_exactly_one_audit_event(tmp_path):
    audit = AuditLog()
    registry = TokenRegistry(
        revoked_path=tmp_path / "revoked.json",
        issued_path=tmp_path / "issued.json",
        audit=audit,
    )
    store = PairCodeStore(tmp_path / "pair_codes.json")
    pc = store.create(HINT)

    status, token, subject = redeem_pair_code(store, registry, pc.code, "owner")

    assert (status, token, subject) == ("reserved_subject", None, None)
    events = audit.of_type(TOKEN_ISSUED_REJECTED)
    assert len(events) == 1, [e.type for e in audit]
    assert events[0].data.get("reason") == "reserved_subject"
    assert events[0].data.get("caller") == "owner"
    assert "保留名" in events[0].message
    assert store.get(pc.code)["consumed"] is False


# ── A3 正常名：subject 派生、label 自报、令牌可用、分型未动 ────────────────
def test_a3_normal_redeem_derives_subject_and_keeps_label(tmp_path):
    registry = TokenRegistry(
        revoked_path=tmp_path / "revoked.json", issued_path=tmp_path / "issued.json"
    )
    store = PairCodeStore(tmp_path / "pair_codes.json")
    pc = store.create(HINT)

    status, token, subject = redeem_pair_code(store, registry, pc.code, "小书童")

    assert status == "ok"
    assert re.fullmatch(rf"{re.escape(HINT)}-[0-9a-f]{{6}}", subject or ""), subject
    info = registry.authenticate(token)
    assert info is not None and info.subject == subject
    assert info.label == "小书童", "自报名只该进 label（显示名）"
    assert info.principal_kind == "agent", "配对兑换的 principal_kind 分型⛔ 不许被本笔改动"
    assert info.expires_at is None, "ttl_s=0 ⇒ 配对令牌不过期（出资人 2026-10-23 裁定的语义）"
    # 两次兑换同一 hint ⇒ 主体不同（不再是"同名即同身份"的可碰撞形状）
    pc2 = store.create(HINT)
    status2, _t2, subject2 = redeem_pair_code(store, registry, pc2.code, "小书童")
    assert status2 == "ok" and subject2 != subject


# ── A4 源头同闸：hint 落保留名 ⇒ 400 且不落码 ──────────────────────────
@pytest.mark.parametrize("reserved", ["owner", "shared"])
def test_a4a_reserved_hint_is_refused_at_the_pair_request_source(tmp_path, monkeypatch, reserved):
    client = _client(tmp_path, monkeypatch)
    r = client.post("/api/mcp/pair/request", json={"agent_name_hint": reserved})
    assert r.status_code == 400, r.text
    assert "保留名" in r.text
    assert not (tmp_path / "store" / ".auth" / "pair_codes.json").is_file() or (
        _pair_store(tmp_path).pending_events() == []
    ), "被拒的 hint ⛔ 不许在盘上留一枚待推的码（它一进盘就会被推给用户弹窗）"


def test_a4b_reserved_hint_is_refused_on_the_pair_link_path(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, OWNER_TOKENS)
    r = client.post(
        "/api/user/pair-link",
        json={"agent_name_hint": "owner"},
        headers={"Authorization": "Bearer owner-tok"},
    )
    assert r.status_code == 400, r.text


# ── A5 MCP `af_pair` 径同判 ────────────────────────────────────────────
def test_a5_mcp_af_pair_path_uses_the_same_gate(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)  # 让 store 根先建出来
    assert client is not None
    from autoforge.af_store import GraphStore

    store = GraphStore(str(tmp_path / "store"))
    registry = TokenRegistry(
        revoked_path=tmp_path / "store" / ".auth" / "revoked.json",
        issued_path=tmp_path / "store" / ".auth" / "issued_tokens.json",
    )
    monkeypatch.setattr(af_mcp, "_MCP_REGISTRY", registry, raising=False)
    monkeypatch.setattr(af_mcp, "_MCP_PAIR_STORE", _pair_store(tmp_path), raising=False)
    pc = _pair_store(tmp_path).create(HINT)

    ok_args = {"code": pc.code, "agent_name": "agent-x"}
    out = af_mcp._t_pair(store, ok_args)
    assert out["ok"] is True and re.fullmatch(rf"{re.escape(HINT)}-[0-9a-f]{{6}}", out["subject"]), out

    pc2 = _pair_store(tmp_path).create(HINT)
    out2 = af_mcp._t_pair(store, {"code": pc2.code, "agent_name": "owner"})
    assert out2["ok"] is False and "保留名" in out2["error"], out2


def test_a5b_mcp_af_request_pair_refuses_reserved_hint(tmp_path, monkeypatch):
    from autoforge.af_store import GraphStore

    store = GraphStore(str(tmp_path / "store"))
    monkeypatch.setattr(af_mcp, "_MCP_PAIR_STORE", _pair_store(tmp_path), raising=False)
    out = af_mcp._t_request_pair(store, {"agent_name_hint": "owner"})
    assert out["ok"] is False, out
    assert out["code"] == "ADM_ERR_PAYLOAD_INVALID", out
    assert "保留名" in out["error"], out


# ── A6 hint 侧闸：历史/毒化记录也拒 ───────────────────────────────────
def test_a6_poisoned_code_with_reserved_hint_is_refused_at_redeem(tmp_path):
    """绕过源头闸直接落一枚 hint=`owner` 的码（历史数据／别的进程写进来）⇒ 兑换仍拒。"""
    registry = TokenRegistry(
        revoked_path=tmp_path / "revoked.json", issued_path=tmp_path / "issued.json"
    )
    store = PairCodeStore(tmp_path / "pair_codes.json")
    pc = store.create("owner")

    status, token, _subject = redeem_pair_code(store, registry, pc.code, "小书童")

    assert (status, token) == ("reserved_subject", None)
    assert store.get(pc.code)["consumed"] is False


# ── A7 确认流按真正签发的 subject 找人 ────────────────────────────────
def test_a7_confirm_flow_finds_the_issued_subject(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, OWNER_TOKENS)
    auth = {"Authorization": "Bearer owner-tok"}
    pc = _pair_store(tmp_path).create(HINT)

    early = client.post(f"/api/user/pair/{pc.code}/confirm", headers=auth)
    assert early.status_code == 409, "还没兑换就该 409（既有语义不变）"

    redeemed = client.post("/api/mcp/pair/redeem", json={"code": pc.code, "agent_name": "小书童"})
    assert redeemed.status_code == 200, redeemed.text
    subject = redeemed.json()["subject"]

    done = client.post(f"/api/user/pair/{pc.code}/confirm", headers=auth)
    assert done.status_code == 200, done.text
    assert done.json()["agent"]["agent_id"] == subject, (
        "确认流必须按**真正签发的 subject** 找人——hint 已经不再等于 subject"
    )
