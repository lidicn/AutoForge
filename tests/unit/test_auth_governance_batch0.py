"""第四批 · 批 0：堵四把"今天就能被利用"的刀（D1/D2/D3/D4）。

判据腿（路线图 §1.3）：
- D1 授权码铸造/撤销收 owner 面（非 owner write 令牌 403）；
- D2 未注册管理员时登录 fail-closed（401 + need_register，不再发 owner 全权限令牌）；
- D3 HTTP 导入面传 owner（非归属人 overwrite 他人归档 403）；
- D4 标签/启停加归属判定（非归属人 403；空 owner 仅 owner 面可改；owner 面 bypass）。

纪律：一律用 monkeypatch.setenv（§2.5 顺序污染），不裸写 os.environ。
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from autoforge.af_api import build_app  # noqa: E402
from autoforge.af_store import GraphStore  # noqa: E402


def _make_client(tmp_path, monkeypatch, *, tokens: dict | None = None):
    """装配一个带若干令牌的 app 客户端。

    tokens: {bearer: {subject, scopes}}；不传则无令牌（fail-closed）。
    返回 (client, store_dir)，store_dir 供测试直接造盘上归档。
    """
    if tokens:
        monkeypatch.setenv("AUTOFORGE_TOKENS", json.dumps(tokens))
    else:
        monkeypatch.delenv("AUTOFORGE_TOKENS", raising=False)
    monkeypatch.delenv("AF_ALLOW_NOAUTH", raising=False)
    store_dir = str(tmp_path / "store")
    return TestClient(build_app(store_dir)), store_dir


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ── D1：授权码铸造/撤销收 owner 面 ─────────────────────────────────────


def test_d1_auth_code_create_blocked_for_non_owner_write(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch, tokens={
        "tok-bot": {"subject": "alice", "scopes": ["read", "write", "live"]},
    })
    r = client.post("/api/user/auth-code", json={"kind": "long"}, headers=_bearer("tok-bot"))
    assert r.status_code == 403, r.text


def test_d1_auth_code_create_allowed_for_owner(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch, tokens={
        "tok-owner": {"subject": "owner", "scopes": ["read", "write", "live", "admin"]},
    })
    r = client.post("/api/user/auth-code", json={"kind": "long"}, headers=_bearer("tok-owner"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["code"]  # 明文码仍只在响应里出现一次


def test_d1_auth_code_delete_blocked_for_non_owner_write(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch, tokens={
        "tok-owner": {"subject": "owner", "scopes": ["read", "write", "live", "admin"]},
        "tok-bot": {"subject": "alice", "scopes": ["read", "write", "live"]},
    })
    created = client.post("/api/user/auth-code", json={"kind": "long"}, headers=_bearer("tok-owner")).json()
    code = created["code"]
    r = client.delete(f"/api/user/auth-code/{code}", headers=_bearer("tok-bot"))
    assert r.status_code == 403, r.text


# ── D2：未注册管理员时登录 fail-closed ────────────────────────────────


def test_d2_login_unregistered_is_fail_closed(tmp_path, monkeypatch):
    # 全新 store_dir：没有 admin.json ⇒ has_admin=False
    client, _ = _make_client(tmp_path, monkeypatch)
    r = client.post("/api/auth/login", json={"username": "x", "password": "y"})
    assert r.status_code == 401, r.text
    body = r.json()
    assert body.get("need_register") is True
    assert "token" not in body  # 绝不返回 token


def test_d2_login_wrong_password_after_register_not_regression(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)
    reg = client.post("/api/auth/register", json={"username": "admin", "password": "hunter2!"})
    assert reg.status_code in (200, 201), reg.text
    # 已注册部署：错误密码仍 401（不回归）
    r = client.post("/api/auth/login", json={"username": "admin", "password": "WRONG"})
    assert r.status_code == 401, r.text


# ── D3：HTTP 导入面传 owner ──────────────────────────────────────────


def _minimal_ir(name: str) -> dict:
    return {
        "ir_version": "0.2.1",
        "id": name,
        "name": name,
        "version": 1,
        "mode": "single",
        "nodes": [
            {"id": "o", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
            {"id": "d", "kind": "do", "adapter": "mock", "action": "light.turn_on", "params": {"entity_id": "light.x"}},
        ],
        "edges": [{"from": "o", "to": "d", "kind": "then"}],
    }


def _export_bundle(name: str, owner: str = "") -> dict:
    """用一个源 store 导出真实形状的 bundle（与 /api/store/export 同形）。"""
    import tempfile
    from pathlib import Path
    from autoforge.af_ir import load_graph
    src = GraphStore(str(Path(tempfile.mkdtemp()) / "src"))
    src.save(load_graph(_minimal_ir(name)), name, owner=owner)
    return src.export_bundle()


def test_d3_import_overwrite_others_archive_blocked(tmp_path, monkeypatch):
    client, store_dir = _make_client(tmp_path, monkeypatch, tokens={
        "tok-owner": {"subject": "owner", "scopes": ["read", "write", "live", "admin"]},
        "tok-bot": {"subject": "alice", "scopes": ["read", "write", "live"]},
    })
    store = GraphStore(store_dir)
    store.save_version_raw(
        "bob_arch", _minimal_ir("bob_arch"), 1, "", "", owner="bob",
    )
    r = client.post(
        "/api/store/import",
        json={"bundle": _export_bundle("bob_arch"), "strategy": "overwrite"},
        headers=_bearer("tok-bot"),
    )
    assert r.status_code == 403, r.text


def test_d3_import_new_archive_as_owner_ok(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch, tokens={
        "tok-owner": {"subject": "owner", "scopes": ["read", "write", "live", "admin"]},
    })
    r = client.post(
        "/api/store/import",
        json={"bundle": _export_bundle("brand_new_arch"), "strategy": "overwrite"},
        headers=_bearer("tok-owner"),
    )
    assert r.status_code == 200, r.text
    assert r.json().get("ok") is True


# ── D4：标签/启停归属判定 ────────────────────────────────────────────


def _seed_owned(store_dir: str, name: str, owner: str, tags: list[str]) -> None:
    store = GraphStore(store_dir)
    store.save_version_raw(
        name, {"automations": [{"id": f"{name}-a1", "nodes": []}]}, 1, "", "", owner=owner,
    )
    store.set_tags(name, tags)


def test_d4_tags_blocked_for_non_owner(tmp_path, monkeypatch):
    client, store_dir = _make_client(tmp_path, monkeypatch, tokens={
        "tok-owner": {"subject": "owner", "scopes": ["read", "write", "live", "admin"]},
        "tok-bot": {"subject": "alice", "scopes": ["read", "write", "live"]},
    })
    _seed_owned(store_dir, "bob_arch", "bob", ["work"])
    r = client.post(
        "/api/graphs/tags",
        json={"name": "bob_arch", "tags": ["pwned"]},
        headers=_bearer("tok-bot"),
    )
    assert r.status_code == 403, r.text


def test_d4_enable_by_tag_blocked_for_non_owner(tmp_path, monkeypatch):
    client, store_dir = _make_client(tmp_path, monkeypatch, tokens={
        "tok-owner": {"subject": "owner", "scopes": ["read", "write", "live", "admin"]},
        "tok-bot": {"subject": "alice", "scopes": ["read", "write", "live"]},
    })
    _seed_owned(store_dir, "bob_arch", "bob", ["work"])
    r = client.post(
        "/api/graphs/enable",
        json={"tag": "work"},
        headers=_bearer("tok-bot"),
    )
    assert r.status_code == 403, r.text


def test_d4_tags_and_enable_owner_bypass(tmp_path, monkeypatch):
    client, store_dir = _make_client(tmp_path, monkeypatch, tokens={
        "tok-owner": {"subject": "owner", "scopes": ["read", "write", "live", "admin"]},
    })
    _seed_owned(store_dir, "bob_arch", "bob", ["work"])
    r1 = client.post(
        "/api/graphs/tags",
        json={"name": "bob_arch", "tags": ["work", "x"]},
        headers=_bearer("tok-owner"),
    )
    assert r1.status_code == 200, r1.text
    # AF-401 后启用过服务层码闸——owner 的**归属 bypass**只免归属判定，
    # 不免码（那是出资人给的凭据，不是身份自带）。腿补一枚真码量 bypass 本体。
    from pathlib import Path as _P
    from autoforge.af_auth import AuthCodeStore
    _rec = AuthCodeStore(_P(store_dir) / ".auth" / "auth_codes.json").create("short", ttl_minutes=30)
    _code = getattr(_rec, "code", None) or _rec["code"]
    r2 = client.post(
        "/api/graphs/enable",
        json={"tag": "work", "auth_code": _code},
        headers=_bearer("tok-owner"),
    )
    assert r2.status_code == 200, r2.text


def test_d4_empty_owner_archive_non_owner_cannot_modify(tmp_path, monkeypatch):
    client, store_dir = _make_client(tmp_path, monkeypatch, tokens={
        "tok-owner": {"subject": "owner", "scopes": ["read", "write", "live", "admin"]},
        "tok-bot": {"subject": "alice", "scopes": ["read", "write", "live"]},
    })
    # 空 owner 归档（D5 未修时的既有形状）：非 owner 面改 ⇒ 403（fail-closed）
    _seed_owned(store_dir, "orphan", "", ["work"])
    r = client.post(
        "/api/graphs/tags",
        json={"name": "orphan", "tags": ["hijack"]},
        headers=_bearer("tok-bot"),
    )
    assert r.status_code == 403, r.text


def test_d2_login_docstring_does_not_promise_old_compat_branch():
    """DB-08·a：docstring 不得再描述「任意非空凭据可登录」这把已堵死的刀。"""
    from pathlib import Path
    here = Path(__file__).resolve()
    af_api = here.parents[2] / "src" / "autoforge" / "af_api.py"
    text = af_api.read_text(encoding="utf-8")
    # 旧 docstring 关键句必须 0 命中；注释里允许引用历史（说明那把刀已堵死）。
    assert "向后兼容：如果还没有管理员用户" not in text
    assert "保持旧的「任意非空凭据可登录」行为" not in text
