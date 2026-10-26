"""第四批 · 批 3：治理与审计面看得见（D9）。

腿：
1. GET /api/auth/subjects 每条含 principal_kind + expires_at，无令牌字段；
2. agent 族令牌打 GET /api/auth/me ⇒ role == "agent"；
3. owner 登录令牌打同一端点 ⇒ role == "admin"（不回归）；
4. owner 面按 subject 撤销（新增能力），非 owner 面按主体撤销 403。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from autoforge.af_api import build_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AF_ACCESS_TOKENS", json.dumps({
        "wtest-token": {"subject": "svc", "scopes": ["read", "write", "live"]},
    }))
    return TestClient(build_app(str(tmp_path / "store")))


def test_1_subjects_has_kind_expiry_no_token(client):
    r = client.get("/api/auth/subjects", headers={"Authorization": "Bearer wtest-token"})
    assert r.status_code == 200, r.text
    subjects = r.json()["subjects"]
    assert subjects, "env 令牌应被列出"
    for row in subjects:
        assert "principal_kind" in row
        assert "expires_at" in row
        # ⛔ 不回令牌明文/摘要
        assert "token" not in row and "digest" not in row
    assert any(row["subject"] == "svc" for row in subjects)


def test_2_agent_token_me_role_is_agent(client):
    # env 访问密钥走默认 principal_kind=agent（A 类操作员键，非 WebUI 登录）。
    r = client.get("/api/auth/me", headers={"Authorization": "Bearer wtest-token"})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["role"] == "agent"


def test_3_owner_login_me_role_is_admin(client):
    # 先注册管理员，再登录拿到 kind=login 的 owner 令牌。
    reg = client.post("/api/auth/register", json={"username": "root", "password": "secret123"})
    assert reg.status_code in (200, 409), reg.text
    login = client.post("/api/auth/login", json={"username": "root", "password": "secret123"})
    assert login.status_code == 200, login.text
    tok = login.json()["token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"})
    assert me.status_code == 200, me.text
    assert me.json()["user"]["role"] == "admin"


def test_4_non_owner_cannot_revoke_by_subject(client):
    # 普通 write 令牌（svc，非 owner 面）按主体撤销 ⇒ 403。
    r = client.post("/api/auth/revoke", json={"subject": "someone"},
                    headers={"Authorization": "Bearer wtest-token"})
    assert r.status_code == 403, r.text
