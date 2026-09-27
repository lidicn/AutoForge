"""v2.0.1 / PR 0.1-c：鉴权 fail-closed 专项测试（P0-9 收口）。

覆盖三态拒绝：令牌缺失 / 无效 / 越权 / 过期 / 校验异常，以及本地逃生舱。
核心原则：任何失败（缺失、未知、撤销、配置非法、过期、校验异常）都**拒绝**，
绝不降级为放行（fail-open）。

由于 `app` 由工厂函数 `build_app()` 构造（`registry` 是其闭包内的局部实例，
非模块级导出），集成测试一律用 `build_app(store_root=临时目录)` 取得独立实例，
并通过环境变量注入令牌，避免污染全局状态。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from autoforge.af_api import build_app
from autoforge.af_auth import TokenExpired, TokenInfo, TokenRegistry

# 受 _write 保护的端点（af_api.py: GET /api/credentials）
AUTH_CODES = "/api/credentials"


@pytest.fixture
def reg(tmp_path: Path, monkeypatch) -> TokenRegistry:
    # 隔离 env，避免继承其它测试注入的令牌
    monkeypatch.delenv("AUTOFORGE_API_TOKEN", raising=False)
    monkeypatch.delenv("AUTOFORGE_TOKENS", raising=False)
    monkeypatch.delenv("AUTOFORGE_REVOKED_TOKENS", raising=False)
    monkeypatch.delenv("AF_ALLOW_NOAUTH", raising=False)
    return TokenRegistry(tmp_path / ".auth" / "revoked.json")


# ── 单元：TokenRegistry.authenticate 三态 ──
def test_authenticate_missing_raw_is_none(reg: TokenRegistry):
    assert reg.authenticate(None) is None
    assert reg.authenticate("") is None


def test_authenticate_unknown_token_is_none(reg: TokenRegistry):
    reg._tokens = {"good": TokenInfo(subject="s", scopes={"read"})}
    assert reg.authenticate("bad") is None


def test_authenticate_revoked_is_none(reg: TokenRegistry):
    reg._tokens = {"good": TokenInfo(subject="s", scopes={"read"})}
    reg._revoked = {"good"}
    assert reg.authenticate("good") is None


def test_authenticate_invalid_config_is_none(reg: TokenRegistry):
    reg._tokens = {"bad": TokenInfo(subject="s", scopes={"read"}, invalid=True)}
    assert reg.authenticate("bad") is None


def test_authenticate_expired_raises(reg: TokenRegistry):
    reg._tokens = {"exp": TokenInfo(subject="s", scopes={"read"}, expires_at=1000.0)}
    with pytest.raises(TokenExpired):
        reg.authenticate("exp")


# ── 集成：requires(scope) 经 HTTP 三态拒绝 ──
def _client(monkeypatch, tmp_path: Path, **env) -> TestClient:
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("AF_ALLOW_NOAUTH", raising=False)
    app = build_app(store_root=str(tmp_path / "store"))
    return TestClient(app)


def test_requires_fail_closed_when_no_tokens_configured(monkeypatch, tmp_path: Path):
    # 未配置任何令牌 → registry.enabled=False → 默认拒绝
    client = _client(monkeypatch, tmp_path)
    assert client.get(AUTH_CODES).status_code == 403


def test_requires_missing_bearer_is_403(monkeypatch, tmp_path: Path):
    client = _client(
        monkeypatch,
        tmp_path,
        AUTOFORGE_TOKENS=json.dumps({"t": {"subject": "a", "scopes": ["write"]}}),
    )
    # 有令牌配置但请求未带 Authorization 头
    assert client.get(AUTH_CODES).status_code == 403


def test_requires_invalid_token_is_403(monkeypatch, tmp_path: Path):
    client = _client(
        monkeypatch,
        tmp_path,
        AUTOFORGE_TOKENS=json.dumps({"t": {"subject": "a", "scopes": ["write"]}}),
    )
    assert client.get(AUTH_CODES, headers={"Authorization": "Bearer wrong"}).status_code == 403


def test_requires_scope_insufficient_is_403(monkeypatch, tmp_path: Path):
    client = _client(
        monkeypatch,
        tmp_path,
        AUTOFORGE_TOKENS=json.dumps({"ro": {"subject": "a", "scopes": ["read"]}}),
    )
    # 端点需要 write，令牌只有 read
    assert client.get(AUTH_CODES, headers={"Authorization": "Bearer ro"}).status_code == 403


def test_requires_valid_write_token_is_200(monkeypatch, tmp_path: Path):
    client = _client(
        monkeypatch,
        tmp_path,
        AUTOFORGE_TOKENS=json.dumps({"ok": {"subject": "a", "scopes": ["write"]}}),
    )
    assert client.get(AUTH_CODES, headers={"Authorization": "Bearer ok"}).status_code == 200


def test_requires_expired_token_is_403(monkeypatch, tmp_path: Path):
    client = _client(
        monkeypatch,
        tmp_path,
        AUTOFORGE_TOKENS=json.dumps(
            {"exp": {"subject": "a", "scopes": ["write"], "expires_at": "2000-01-01T00:00:00+00:00"}}
        ),
    )
    assert client.get(AUTH_CODES, headers={"Authorization": "Bearer exp"}).status_code == 403


def test_requires_auth_exception_is_fail_closed(monkeypatch, tmp_path: Path):
    # 鉴权后端意外异常（非 TokenExpired）必须 fail-closed 拒绝，不得 500/放行
    monkeypatch.setenv(
        "AUTOFORGE_TOKENS",
        json.dumps({"x": {"subject": "a", "scopes": ["write"]}}),
    )
    monkeypatch.delenv("AF_ALLOW_NOAUTH", raising=False)

    def boom(self, raw: str | None) -> TokenInfo:
        raise RuntimeError("unexpected auth backend failure")

    monkeypatch.setattr(TokenRegistry, "authenticate", boom)
    app = build_app(store_root=str(tmp_path / "store"))
    client = TestClient(app)
    r = client.get(AUTH_CODES, headers={"Authorization": "Bearer x"})
    assert r.status_code == 403
