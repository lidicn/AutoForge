"""v1.4.0 附 · 令牌过期三重 fail-closed（调研 §2.8）。

覆盖：`expires_at` 合法放行 / 已过期拒绝（TokenExpired → 403）/ 无法解析拒绝（fail-closed）
/ naive（无时区）拒绝 / 未配 `expires_at` 向后兼容。
"""

from __future__ import annotations

import json

import pytest

from autoforge.af_auth import TokenExpired, TokenRegistry


def _registry(monkeypatch, tokens: dict) -> TokenRegistry:
    monkeypatch.setenv("AUTOFORGE_TOKENS", json.dumps(tokens))
    return TokenRegistry()


# ── 纯引擎单测（不依赖 HTTP）──────────────────────────────────────────


def test_valid_future_expiry_accepted(monkeypatch):
    reg = _registry(
        monkeypatch,
        {"tok": {"subject": "alice", "scopes": ["read"], "expires_at": "2999-01-01T00:00:00+00:00"}},
    )
    info = reg.authenticate("tok")
    assert info is not None and info.subject == "alice"


def test_expired_token_raises(monkeypatch):
    reg = _registry(
        monkeypatch,
        {"tok": {"subject": "old", "scopes": ["read"], "expires_at": "2000-01-01T00:00:00+00:00"}},
    )
    with pytest.raises(TokenExpired):
        reg.authenticate("tok")


def test_unparseable_expiry_is_rejected(monkeypatch):
    """解析失败 → 拒绝（不是放过）：authenticate 返回 None，且 info 标记 invalid。"""
    reg = _registry(
        monkeypatch,
        {"tok": {"subject": "broken", "scopes": ["read"], "expires_at": "not-a-date"}},
    )
    assert reg.authenticate("tok") is None
    assert reg._tokens["tok"].invalid is True
    assert "无法解析" in reg._tokens["tok"].invalid_reason


def test_naive_expiry_is_rejected(monkeypatch):
    """无时区（naive）→ 拒绝：无法与 aware 时间可靠比较。"""
    reg = _registry(
        monkeypatch,
        {"tok": {"subject": "naive", "scopes": ["read"], "expires_at": "2999-01-01T00:00:00"}},
    )
    assert reg.authenticate("tok") is None
    assert "时区" in reg._tokens["tok"].invalid_reason


def test_no_expiry_is_backward_compatible(monkeypatch):
    """未配 `expires_at` → 永不过期（向后兼容既有令牌）。"""
    reg = _registry(monkeypatch, {"tok": {"subject": "forever", "scopes": ["read"]}})
    info = reg.authenticate("tok")
    assert info is not None and info.expires_at is None and info.invalid is False


# ── HTTP 端到端 ──────────────────────────────────────────────────────


def _client(tmp_path, monkeypatch, env: dict[str, str]):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from autoforge.af_api import build_app

    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return TestClient(build_app(str(tmp_path / "store")))


def _probe_write(client, headers):
    """write scope 探针：过鉴权 → 400（IR 校验失败）；被拒 → 403。"""
    return client.post("/api/sessions", json={"ir": {}}, headers=headers)


def test_expired_token_is_403_over_http(tmp_path, monkeypatch):
    env = {
        "AUTOFORGE_TOKENS": json.dumps(
            {
                "tok-expired": {
                    "subject": "old", "scopes": ["read", "write"],
                    "expires_at": "2000-01-01T00:00:00+00:00",
                },
                "tok-live": {
                    "subject": "current", "scopes": ["read", "write"],
                    "expires_at": "2999-01-01T00:00:00+00:00",
                },
                "tok-bad": {
                    "subject": "broken", "scopes": ["read", "write"],
                    "expires_at": "not-a-date",
                },
            }
        )
    }
    client = _client(tmp_path, monkeypatch, env)

    expired = {"Authorization": "Bearer tok-expired"}
    resp = _probe_write(client, expired)
    assert resp.status_code == 403 and "过期" in resp.json()["detail"]
    # 过期令牌视为未认证：whoami → 401
    assert client.get("/api/auth/whoami", headers=expired).status_code == 401

    # 合法过期时间在未来的令牌：过鉴权，卡在 IR 校验（400）
    assert _probe_write(client, {"Authorization": "Bearer tok-live"}).status_code == 400

    # 配置非法（无法解析）→ fail-closed 拒绝（403）
    assert _probe_write(client, {"Authorization": "Bearer tok-bad"}).status_code == 403
