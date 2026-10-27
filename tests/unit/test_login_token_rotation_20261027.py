"""登录态换代与治理页按主体撤销（出资人 2026-10-27 现场：登录态堆 11 行、删不动、一点就把自己登出）。

三条铁律各钉一族腿：
1. 登录/注册即换代——一次登录只留一个活登录态，表不堆；
2. 治理页撤 owner 只清登录态——保住调用者自己手里那枚（不误登出）、⛔ 不碰 env 部署密钥
   与 agent/developer 令牌（登录态是会话，部署凭据不是）；
3. 撤其他主体（agent）仍全撤——那是「删 agent」语义，except 当前 bearer 只是防自杀式登出。
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from autoforge.af_api import build_app  # noqa: E402
from autoforge.af_auth import TokenRegistry  # noqa: E402

PW = "hunter2-pass"


def _fresh_client(tmp_path, monkeypatch):
    for k in ("AF_ACCESS_TOKENS", "AUTOFORGE_TOKENS", "AF_ALLOW_NOAUTH"):
        monkeypatch.delenv(k, raising=False)
    return TestClient(build_app(str(tmp_path / "store")))


def _register(client, username="boss"):
    r = client.post("/api/auth/register", json={"username": username, "password": PW})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _login(client, username="boss"):
    r = client.post("/api/auth/login", json={"username": username, "password": PW})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _who(client, token):
    return client.get("/api/auth/whoami", headers={"Authorization": f"Bearer {token}"})


# ── 1 · 登录即换代 ─────────────────────────────────────────────────────

def test_second_login_revokes_the_first_session(tmp_path, monkeypatch):
    client = _fresh_client(tmp_path, monkeypatch)
    t1 = _register(client)
    t2 = _login(client)
    assert _who(client, t2).status_code == 200, "新会话必须活"
    assert _who(client, t1).status_code == 401, "旧登录态必须随换代失效（堆行的根源）"


def test_rotate_keeps_agent_and_other_subjects(tmp_path):
    """单元层：login/agent 两族混布 + 别的主体，rotate("owner") 只动 owner 的 login 档。"""
    reg = TokenRegistry()
    tok_login = reg.issue_for_agent("owner", ("read",), principal_kind="login")
    tok_agent = reg.issue_for_agent("owner", ("read",), principal_kind="agent")   # 同名不同族
    tok_other = reg.issue_for_agent("dsh", ("read",), principal_kind="login")    # 别的主体
    assert reg.rotate_login_tokens("owner") == 1
    assert reg.authenticate(tok_login) is None, "owner 的 login 档必须被清"
    assert reg.authenticate(tok_agent) is not None, "agent 档不是会话，不许被登录换代误杀"
    assert reg.authenticate(tok_other) is not None, "别的主体不在本次换代射程内"


def test_env_owner_key_survives_subject_revoke(tmp_path, monkeypatch):
    """env 里人配的 owner 部署密钥不是会话：治理页按主体撤销 owner 不得吊销它。

    registry 在**构造期**读 env ⇒ 先 setenv 再构造，不借 client 内部那份（上一版
    先 set 后被 fixture 的 delenv 删掉，测了个寂寞——它红得对）。
    """
    monkeypatch.setenv("AF_ACCESS_TOKENS",
                       '{"env-owner-key": {"subject": "owner", "scopes": ["read","write","live"]}}')
    reg = TokenRegistry(revoked_path=tmp_path / "revoked.json", issued_path=tmp_path / "issued.json")
    assert reg.authenticate("env-owner-key") is not None, "前置：env 密钥本应可用"
    t_login = reg.issue_for_agent("owner", ("read",), principal_kind="login")
    # 治理页撤 owner＝rotate(keep=当前 bearer)：只动 login 档
    assert reg.rotate_login_tokens("owner", keep_token=t_login) == 0
    assert reg.authenticate("env-owner-key") is not None, \
        "env owner 部署密钥被按主体撤销误杀 = 把部署凭据当会话"
    assert reg.rotate_login_tokens("owner") == 1, "对照组：rotate 不是空操作，login 档该清"
    assert reg.authenticate(t_login) is None
    assert reg.authenticate("env-owner-key") is not None, "清完登录态，env 密钥仍活"


# ── 2 · 治理页撤销不误伤自己 ────────────────────────────────────────────

def test_owner_revoke_keeps_caller_session(tmp_path, monkeypatch):
    client = _fresh_client(tmp_path, monkeypatch)
    t_keep = _register(client)
    t_old = _login(client)      # 换代后 t_old 已是唯一活着的旧会话（t_keep 已被换掉）
    assert _who(client, t_old).status_code == 200
    r = client.post("/api/auth/revoke", json={"subject": "owner"},
                    headers={"Authorization": f"Bearer {t_old}"})
    assert r.status_code == 200, r.text
    assert _who(client, t_old).status_code == 200, "撤销把调用者自己登出＝现场「删不动/一点就踢自己」"


def test_agent_subject_revoke_still_kills_all_its_tokens(tmp_path):
    """撤 agent 主体＝「删这个 agent」语义：名下全部令牌清光——这是 revoke_by_subject
    的既有行为，换代修复不许顺手把它收窄（owner 走 rotate 是**分族**，不是全局改语义）。"""
    reg = TokenRegistry()
    t1 = reg.issue_for_agent("dsh", ("read",))
    t2 = reg.issue_for_agent("dsh", ("read",))
    keeper = reg.issue_for_agent("owner", ("read",), principal_kind="login")
    n = reg.revoke_by_subject("dsh", except_token=keeper)
    assert n == 2, f"dsh 名下两枚都该撤，实撤 {n}"
    assert reg.authenticate(t1) is None and reg.authenticate(t2) is None
    assert reg.authenticate(keeper) is not None, "except_token 保的不是 dsh 的令牌，不受牵连"
