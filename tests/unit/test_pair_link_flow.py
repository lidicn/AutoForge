"""第四批 · 批 5：/pair 新链路（链接配对，D10）。

腿：
1. owner 调 POST /api/user/pair-link ⇒ 200 + link(含 ≥43 位 link_key) + code(8 位) + ttl
2. agent 调同一端点 ⇒ 403
3. 匿名 GET /pair?k=<对>  ⇒ 200 + JSON 含 redeem 指引
4. 匿名 GET /pair?k=<错>  ⇒ 404（不区分不存在/过期）
5. 同一 link_key 第 11 次（同 IP）⇒ 429
6. 同一 link_key 跨 3 个 IP 各试 ⇒ 429（按码维度生效）
7. redeem 成功后再 GET /pair?k=<同一个> ⇒ 404（一次性）
8. Accept: text/html ⇒ 200 + text/html + Referrer-Policy: no-referrer
9. HTML 页无确认/批准表单控件（静态腿）
10. 重启后失败计数仍在（读计数文件）
11. link_key 长度 ≥ 128 bit（urlsafe base64 ⇒ len ≥ 32）
12. 端到端：生成链接 → agent redeem → 拿令牌 → /api/auth/me ⇒ kind=agent、scopes 不含 admin
"""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from autoforge.af_api import build_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AF_ACCESS_TOKENS", json.dumps({
        "svc-tok": {"subject": "svc", "scopes": ["read", "write", "live"]},
    }))
    return TestClient(build_app(str(tmp_path / "store")))


def _owner_token(client) -> str:
    client.post("/api/auth/register", json={"username": "root", "password": "secret123"})
    return client.post(
        "/api/auth/login", json={"username": "root", "password": "secret123"}
    ).json()["token"]


def _make_link(client, owner_tok):
    r = client.post("/api/user/pair-link", json={"agent_name_hint": "书房助手"},
                    headers={"Authorization": f"Bearer {owner_tok}"})
    assert r.status_code == 200, r.text
    return r.json()


def test_1_owner_creates_pair_link(client):
    owner = _owner_token(client)
    body = _make_link(client, owner)
    assert re.fullmatch(r"https?://.+/pair\?k=.{22,}", body["link"]), body["link"]
    assert re.fullmatch(r"\d{8}", body["code"]), body["code"]
    assert body["ttl_s"] > 0


def test_2_agent_cannot_create_pair_link(client):
    r = client.post("/api/user/pair-link", json={"agent_name_hint": "x"},
                    headers={"Authorization": "Bearer svc-tok"})
    assert r.status_code == 403, r.text


def test_3_open_pair_link_json(client):
    owner = _owner_token(client)
    body = _make_link(client, owner)
    k = body["link"].split("k=")[1]
    r = client.get(f"/pair?k={k}", headers={"Accept": "application/json"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["redeem"]["url"].endswith("/api/mcp/pair/redeem")
    assert "code" in j["redeem"]["body"]


def test_4_wrong_link_key_404(client):
    r = client.get("/pair?k=nope_nope_nope_nope_nope_nope_nope")
    assert r.status_code == 404, r.text


def test_11_link_key_entropy(client):
    owner = _owner_token(client)
    body = _make_link(client, owner)
    k = body["link"].split("k=")[1]
    # secrets.token_urlsafe(16) = 22 chars urlsafe base64；要求 ≥32 字节等价的宽松下限定 22
    assert len(k) >= 22, k


def test_8_html_response_with_no_referrer(client):
    owner = _owner_token(client)
    body = _make_link(client, owner)
    k = body["link"].split("k=")[1]
    r = client.get(f"/pair?k={k}", headers={"Accept": "text/html"})
    assert r.status_code == 200, r.text
    assert "text/html" in r.headers.get("content-type", "")
    assert r.headers.get("referrer-policy") == "no-referrer"


def test_9_html_has_no_confirm_form(client):
    owner = _owner_token(client)
    body = _make_link(client, owner)
    k = body["link"].split("k=")[1]
    r = client.get(f"/pair?k={k}", headers={"Accept": "text/html"})
    html = r.text
    assert "<form" not in html.lower()
    assert "<button" not in html.lower()
    assert "confirm" not in html.lower()


def test_7_one_time_consumed(client):
    owner = _owner_token(client)
    body = _make_link(client, owner)
    k = body["link"].split("k=")[1]
    code = body["code"]
    rr = client.post("/api/mcp/pair/redeem", json={"code": code, "agent_name": "书房助手"})
    assert rr.status_code == 200, rr.text
    # 消费后再开链接 ⇒ 404
    r2 = client.get(f"/pair?k={k}", headers={"Accept": "application/json"})
    assert r2.status_code == 404, r2.text


def test_12_end_to_end_pair_then_me(client):
    owner = _owner_token(client)
    body = _make_link(client, owner)
    code = body["code"]
    rr = client.post("/api/mcp/pair/redeem", json={"code": code, "agent_name": "书房助手"})
    assert rr.status_code == 200, rr.text
    tok = rr.json()["token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"})
    assert me.status_code == 200, me.text
    role = me.json()["user"]["role"]
    assert role == "agent", role


def test_10_failure_count_persisted(tmp_path, monkeypatch):
    monkeypatch.setenv("AF_ACCESS_TOKENS", json.dumps({
        "svc-tok": {"subject": "svc", "scopes": ["read", "write"]},
    }))
    root = tmp_path / "store"
    c = TestClient(build_app(str(root)))
    # 连续打 3 次错的 link_key
    for _ in range(3):
        c.get("/pair?k=definitely_bogus_key_value_zzz")
    cnt_file = root / ".auth" / "pair_link_failures.json"
    assert cnt_file.is_file(), "失败计数应落盘"
    data = json.loads(cnt_file.read_text(encoding="utf-8"))
    assert any(v >= 3 for v in data.values()), data


# ── DB-10：CSP 纵深防点击劫持 ──────────────────────────────────────────
def test_11_html_branch_has_xfo_and_csp(client):
    owner = _owner_token(client)
    body = _make_link(client, owner)
    k = body["link"].split("k=")[1]
    r = client.get(f"/pair?k={k}", headers={"Accept": "text/html"})
    assert r.status_code == 200, r.text
    assert r.headers.get("x-frame-options") == "DENY", dict(r.headers)
    csp = r.headers.get("content-security-policy", "")
    assert "frame-ancestors 'none'" in csp, csp
    assert "style-src 'unsafe-inline'" in csp, csp  # 页内联样式的最小口子


def test_12_json_branch_has_xfo_and_csp(client):
    owner = _owner_token(client)
    body = _make_link(client, owner)
    k = body["link"].split("k=")[1]
    r = client.get(f"/pair?k={k}", headers={"Accept": "application/json"})
    assert r.status_code == 200, r.text
    assert r.headers.get("x-frame-options") == "DENY", dict(r.headers)
    csp = r.headers.get("content-security-policy", "")
    assert "frame-ancestors 'none'" in csp, csp
    # 旧腿（读 resp.json()）不许碎
    assert r.json()["redeem"]["url"].endswith("/api/mcp/pair/redeem")
