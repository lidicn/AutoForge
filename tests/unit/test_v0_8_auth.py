"""v0.8.0 服务层鉴权升级单测：多令牌主体模型 / 撤销即时生效 / 限速 / 端点 scope 分级。

装了 fastapi 才跑；未装则整文件 skip（保持内核测试可在无 Web 依赖下运行）。
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from autoforge.af_api import build_app  # noqa: E402
from autoforge.af_auth import (  # noqa: E402
    RateLimitExceeded,
    RateLimiter,
    TokenRegistry,
)


def _client(tmp_path, monkeypatch, env: dict[str, str]) -> TestClient:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return TestClient(build_app(str(tmp_path / "store")))


def _probe_write(client: TestClient, headers: dict[str, str] | None = None):
    """探针：POST /api/sessions（write scope）。鉴权通过 → 400（IR 校验失败）；被拒 → 403。"""
    return client.post("/api/sessions", json={"ir": {}}, headers=headers or {})


# ── 纯引擎单测（不依赖 HTTP）────────────────────────────────────────


def test_registry_parse_meta_forms(monkeypatch):
    monkeypatch.setenv(
        "AUTOFORGE_TOKENS",
        json.dumps(
            {
                "tok-dict": {"subject": "ci-bot", "scopes": ["read", "write"]},
                "tok-list": ["read"],
                "tok-str": "reporter",
                "tok-badscope": {"subject": "x", "scopes": ["root"]},
            }
        ),
    )
    registry = TokenRegistry()

    assert registry.authenticate("tok-dict").subject == "ci-bot"
    assert registry.authenticate("tok-dict").scopes == {"read", "write"}
    assert registry.authenticate("tok-list").scopes == {"read"}
    assert registry.authenticate("tok-str").subject == "reporter"
    # 非法 scope 过滤后回退 read，绝不放权
    assert registry.authenticate("tok-badscope").scopes == {"read"}


def test_registry_revoke_persists_and_survives_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOFORGE_API_TOKEN", "secret")
    revoked_path = tmp_path / ".auth" / "revoked.json"

    first = TokenRegistry(revoked_path)
    assert first.enabled is True
    assert first.authenticate("secret") is not None
    assert first.revoke("secret") is True
    assert first.authenticate("secret") is None  # 即时生效
    assert first.revoke("secret") is False  # 重复撤销无变更
    assert revoked_path.is_file() and json.loads(revoked_path.read_text()) == ["secret"]

    # 重启后从盘恢复，撤销不失效
    second = TokenRegistry(revoked_path)
    assert second.authenticate("secret") is None


def test_rate_limiter_fixed_window():
    limiter = RateLimiter(per_minute=2, window_s=60)
    limiter.check("ip:x")
    limiter.check("ip:x")
    with pytest.raises(RateLimitExceeded):
        limiter.check("ip:x")
    limiter.check("ip:other")  # 其他维度不受影响


# ── HTTP 端到端 ──────────────────────────────────────────────────────


def test_no_tokens_fail_closed(tmp_path, monkeypatch):
    """未配置任何令牌：默认 fail-closed，受保护写/真机端点 403（读/健康仍公开）。"""
    monkeypatch.delenv("AF_ALLOW_NOAUTH", raising=False)  # 显式清逃生舱，确保测的是 fail-closed 路径
    client = _client(tmp_path, monkeypatch, {})
    assert client.get("/api/health").status_code == 200
    assert _probe_write(client).status_code == 403
    assert client.get("/api/live/status").status_code == 403


def test_no_tokens_allow_noauth(tmp_path, monkeypatch):
    """AF_ALLOW_NOAUTH=1 逃生舱：本地开发/原型仍可无令牌开放。"""
    client = _client(tmp_path, monkeypatch, {"AF_ALLOW_NOAUTH": "1"})
    assert client.get("/api/health").status_code == 200
    assert _probe_write(client).status_code != 403
    assert client.get("/api/live/status").status_code == 200


def test_legacy_single_token_backward_compat(tmp_path, monkeypatch):
    """旧单密钥 AUTOFORGE_API_TOKEN：等价 subject=shared、全 scope。"""
    client = _client(tmp_path, monkeypatch, {"AUTOFORGE_API_TOKEN": "secret"})

    # 无令牌 → 写与真机均 403；读端点公开
    assert _probe_write(client).status_code == 403
    assert client.get("/api/live/status").status_code == 403
    assert client.get("/api/health").status_code == 200

    headers = {"Authorization": "Bearer secret"}
    assert _probe_write(client, headers).status_code == 400  # 400=过鉴权，卡在 IR 校验
    assert client.get("/api/live/status", headers=headers).status_code == 200

    # whoami 自检
    whoami = client.get("/api/auth/whoami", headers=headers).json()
    assert whoami["subject"] == "shared"
    assert set(whoami["scopes"]) == {"read", "write", "live"}


def test_multi_token_scope_grading(tmp_path, monkeypatch):
    """多令牌主体模型：read-only 令牌无法写/真机；write 令牌无法真机（越权 403）。"""
    env = {
        "AUTOFORGE_TOKENS": json.dumps(
            {
                "tok-reporter": {"subject": "reporter", "scopes": ["read"]},
                "tok-bot": {"subject": "bot", "scopes": ["read", "write"]},
            }
        )
    }
    client = _client(tmp_path, monkeypatch, env)

    reporter = {"Authorization": "Bearer tok-reporter"}
    bot = {"Authorization": "Bearer tok-bot"}

    # read 端点始终公开（无需令牌）
    assert client.get("/api/health").status_code == 200

    # reporter：read-only，越权 403
    assert _probe_write(client, reporter).status_code == 403
    assert client.get("/api/live/status", headers=reporter).status_code == 403
    assert client.get("/api/auth/whoami", headers=reporter).json()["subject"] == "reporter"

    # bot：可写，不可真机
    assert _probe_write(client, bot).status_code == 400
    assert client.get("/api/live/status", headers=bot).status_code == 403

    # 未知令牌 403；subjects 需 write scope
    assert _probe_write(client, {"Authorization": "Bearer nope"}).status_code == 403
    assert client.get("/api/auth/subjects", headers=reporter).status_code == 403
    subjects = client.get("/api/auth/subjects", headers=bot).json()["subjects"]
    by_subject = {s["subject"]: s["scopes"] for s in subjects}
    assert by_subject["reporter"] == ["read"]
    assert by_subject["bot"] == ["read", "write"]


def test_revocation_immediate_over_http(tmp_path, monkeypatch):
    """撤销即时生效：撤销前放行 → 撤销 → 同一令牌立即 403，且黑名单落盘。"""
    client = _client(tmp_path, monkeypatch, {"AUTOFORGE_API_TOKEN": "secret"})
    headers = {"Authorization": "Bearer secret"}

    assert _probe_write(client, headers).status_code == 400

    body = client.post("/api/auth/revoke", json={"token": "secret"}, headers=headers)
    assert body.status_code == 200 and body.json()["revoked"] is True

    assert _probe_write(client, headers).status_code == 403
    assert client.get("/api/auth/whoami", headers=headers).status_code == 401

    # 黑名单已落盘，重启（新 registry 实例）后仍生效
    persisted = json.loads((tmp_path / "store" / ".auth" / "revoked.json").read_text())
    assert persisted == ["secret"]
    restarted = _client(tmp_path, monkeypatch, {"AUTOFORGE_API_TOKEN": "secret"})
    assert _probe_write(restarted, headers).status_code == 403


def test_rate_limit_429_over_http(tmp_path, monkeypatch):
    """限速：超过 per-minute 阈值 → 429（读端点也受限）。"""
    client = _client(tmp_path, monkeypatch, {"AUTOFORGE_RATE_LIMIT_PER_MIN": "3"})
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/health").status_code == 429
