"""第四批 · 批 4：RBAC 收口 + rename 三校验 + 令牌寿命（D7/D8/D11）。

腿 1-11 走 HTTP（build_app + env 注入令牌）；腿 12-13 直接打 TokenRegistry
（空闲续期只落盘一次 / 7 天绝对上限）。
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from autoforge.af_api import build_app
from autoforge.af_auth import TokenExpired, TokenRegistry


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AF_ACCESS_TOKENS", json.dumps({
        "svc-tok": {"subject": "svc", "scopes": ["read", "write", "live"]},
        "paired-tok": {"subject": "paired_agent", "scopes": ["read", "write"]},
        "paired2-tok": {"subject": "paired_agent2", "scopes": ["read", "write"]},
        "agentA-tok": {"subject": "agentA", "scopes": ["read", "write"]},
    }))
    return TestClient(build_app(str(tmp_path / "store")))


def _owner_token(client) -> str:
    client.post("/api/auth/register", json={"username": "root", "password": "secret123"})
    r = client.post("/api/auth/login", json={"username": "root", "password": "secret123"})
    return r.json()["token"]


def _developer_token(client, owner_tok, name="dev1") -> str:
    r = client.post("/api/user/agent-token", json={"agent_name": name},
                    headers={"Authorization": f"Bearer {owner_tok}"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


# ── DELETE /api/user/agents/{id}：治理面收口 ──
def test_1_agent_cannot_delete_other_agent(client):
    r = client.delete("/api/user/agents/paired_agent",
                      headers={"Authorization": "Bearer svc-tok"})
    assert r.status_code == 403, r.text


def test_2_developer_can_delete_agent(client):
    owner = _owner_token(client)
    dev = _developer_token(client, owner)
    r = client.delete("/api/user/agents/paired_agent",
                      headers={"Authorization": f"Bearer {dev}"})
    assert r.status_code == 200, r.text


def test_3_owner_can_delete_agent_no_regression(client):
    owner = _owner_token(client)
    r = client.delete("/api/user/agents/paired_agent2",
                      headers={"Authorization": f"Bearer {owner}"})
    assert r.status_code == 200, r.text


# ── PATCH rename：治理面 + 服务层三校验 ──
def test_4_agent_cannot_rename_to_owner(client):
    r = client.patch("/api/user/agents/paired_agent2", json={"name": "owner"},
                     headers={"Authorization": "Bearer svc-tok"})
    assert r.status_code == 403, r.text


def test_5_owner_rename_to_owner_rejected_by_service(client):
    owner = _owner_token(client)
    r = client.patch("/api/user/agents/paired_agent2", json={"name": "owner"},
                     headers={"Authorization": f"Bearer {owner}"})
    assert r.status_code == 403, r.text  # 服务层保留名拦，不只端点


def test_6_owner_rename_to_existing_subject_rejected(client):
    owner = _owner_token(client)
    r = client.patch("/api/user/agents/paired_agent2", json={"name": "svc"},
                     headers={"Authorization": f"Bearer {owner}"})
    assert r.status_code == 403, r.text  # 重名碰撞拦


# ── /api/auth/revoke：非 owner 只能撤自己 ──
def test_8_agent_cannot_revoke_other_first(client):
    r = client.post("/api/auth/revoke", json={"token": "svc-tok"},
                    headers={"Authorization": "Bearer agentA-tok"})
    assert r.status_code == 403, r.text


def test_7_agent_can_revoke_self(client):
    r = client.post("/api/auth/revoke", json={"token": "agentA-tok"},
                    headers={"Authorization": "Bearer agentA-tok"})
    assert r.status_code == 200, r.text


# ── POST /api/user/agent-token：developer 档唯一铸造口，只准 owner ──
def test_9_agent_cannot_issue_agent_token(client):
    r = client.post("/api/user/agent-token", json={"agent_name": "x"},
                    headers={"Authorization": "Bearer svc-tok"})
    assert r.status_code == 403, r.text


def test_10_developer_cannot_self_amplify(client):
    owner = _owner_token(client)
    dev = _developer_token(client, owner)
    r = client.post("/api/user/agent-token", json={"agent_name": "y"},
                    headers={"Authorization": f"Bearer {dev}"})
    assert r.status_code == 403, r.text


def test_11_owner_issues_developer_token_with_admin_scope(client):
    owner = _owner_token(client)
    r = client.post("/api/user/agent-token", json={"agent_name": "ops_bot"},
                    headers={"Authorization": f"Bearer {owner}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["principal_kind"] == "developer"
    assert "admin" in body["scopes"]


# ── 令牌寿命（直接打注册表）──
def _reg_with_issued(tmp_path) -> TokenRegistry:
    return TokenRegistry(
        issued_path=str(tmp_path / "issued.json"),
        revoked_path=str(tmp_path / "revoked.json"),
    )


def test_12_login_renewal_persists_only_once(tmp_path):
    reg = _reg_with_issued(tmp_path)
    tok = reg.issue_for_agent("owner", scopes={"read", "write"},
                              principal_kind="login", ttl_s=100)
    info = reg.authenticate(tok)
    assert info is not None
    # 把剩余压到 < 50%（40 < 50）⇒ 第一次 authenticate 触发续期 + 落盘。
    info.expires_at = time.time() + 40
    calls = []
    reg._persist_renewal_locked = lambda dg, exp: calls.append((dg, exp))  # type: ignore
    reg.authenticate(tok)
    assert len(calls) == 1, f"第一次应续期一次，实际 {len(calls)}"
    # 续期后剩余≈100s > 50% ⇒ 第二次不再续、不再落盘。
    reg.authenticate(tok)
    assert len(calls) == 1, f"连续两次请求只落盘一次，实际 {len(calls)}"


def test_13_login_token_hits_absolute_cap(tmp_path):
    reg = _reg_with_issued(tmp_path)
    tok = reg.issue_for_agent("owner", scopes={"read", "write"},
                             principal_kind="login", ttl_s=100)
    info = reg.authenticate(tok)
    assert info is not None and info.absolute_deadline is not None
    # 把绝对上限拨到过去 ⇒ 即使 expires_at 还没到，也必须过期（fail-closed）。
    info.absolute_deadline = time.time() - 1
    info.expires_at = time.time() + 1000
    with pytest.raises(TokenExpired):
        reg.authenticate(tok)
