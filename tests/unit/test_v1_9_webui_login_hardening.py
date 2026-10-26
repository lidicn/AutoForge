"""WebUI 登录面加固回归：登录/注册限速 + TTL 可调（P2 批次，20261021）。

覆盖：
- 登录/注册两枚限速器的数值口径（10 次/分钟、超限锁 300s），与 bootstrap 同一纪律。
- `_login_limit` 桶形签名：同一桶 11 发第 11 发即 429 形状（RateLimitExceeded）。
- HTTP 接线腿：`/api/auth/login` 与 `/api/auth/register` 真的挂了限速——
  monkeypatch 换新鲜实例再打满，不污染其他测试共享的模块级桶。
- `AF_LOGIN_TOKEN_TTL_S` 三条腿：缺省 86400、显式覆盖、坏值回退缺省；
  `issue_for_agent(ttl_s=None)` 在调用时刻读 env（显式 ttl_s 仍优先）。

限速器单元腿沿用 test_dcd_20261006 的裁定：跑在 RateLimiter 单元上而不是
HTTP 上不等真 60 秒；HTTP 腿则换桶再打满——模块级桶全进程共享，直接打满
会把 test_dcd_20261004 的 `_login_token` 顶进 429（顺序性联坐）。
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

import autoforge.af_api as af_api  # noqa: E402
from autoforge.af_api import build_app  # noqa: E402
from autoforge.af_auth import (  # noqa: E402
    LOGIN_LOCK_S,
    LOGIN_PER_MIN,
    RateLimitExceeded,
    RateLimiter,
    TokenRegistry,
    _digest,
    _login_token_ttl_s,
)


# ── 数值口径 ──────────────────────────────────────────────────────────


def test_login_limit_constants_are_the_signed_ones():
    """登录/注册限速数值：10 次/分钟，超限锁 300s（与 bootstrap 锁同时长）。"""
    assert LOGIN_PER_MIN == 10
    assert LOGIN_LOCK_S == 300


# ── RateLimiter 单元腿 ────────────────────────────────────────────────


def test_login_limiter_locks_at_11th_hit():
    """同桶打满 10 发后第 11 发抛 RateLimitExceeded，且锁定期间旧命中即刻作废。"""
    limiter = RateLimiter(per_minute=LOGIN_PER_MIN, lock_s=LOGIN_LOCK_S)
    for _ in range(LOGIN_PER_MIN):
        limiter.check("ip:1.2.3.4")
    with pytest.raises(RateLimitExceeded):
        limiter.check("ip:1.2.3.4")
    assert limiter._hits.get("ip:1.2.3.4", []) == []  # 锁定时窗口里的旧命中即刻作废
    with pytest.raises(RateLimitExceeded):
        limiter.check("ip:1.2.3.4")  # 窗口滚过也没用，挡它的必须是那把锁

    limiter._blocked["ip:1.2.3.4"] = time.monotonic() - 1
    limiter.check("ip:1.2.3.4")  # 解锁后放行


def test_login_and_register_buckets_are_independent():
    """登录桶打满不得连坐注册桶（按端点分桶，镜像 bootstrap 的 ip+端点键位）。"""
    login_bucket = RateLimiter(per_minute=LOGIN_PER_MIN, lock_s=LOGIN_LOCK_S)
    register_bucket = RateLimiter(per_minute=LOGIN_PER_MIN, lock_s=LOGIN_LOCK_S)
    for _ in range(LOGIN_PER_MIN):
        login_bucket.check("ip:1.2.3.4")
    with pytest.raises(RateLimitExceeded):
        login_bucket.check("ip:1.2.3.4")  # 登录桶打满即锁
    register_bucket.check("ip:1.2.3.4")  # 注册桶不受登录桶锁定影响


# ── HTTP 接线腿 ───────────────────────────────────────────────────────


def _fresh_client(tmp_path: Path, monkeypatch) -> TestClient:
    """build_app 内部自造 login/register 两枚新鲜桶：每个 app 独立计数，
    测试间不共享状态（限速器不是模块级单例，无需 monkeypatch 换桶）。"""
    for k in ("AUTOFORGE_API_TOKEN", "AUTOFORGE_TOKENS", "AF_ALLOW_NOAUTH"):
        monkeypatch.delenv(k, raising=False)
    return TestClient(build_app(str(tmp_path / "store")))


def test_login_endpoint_rate_limited_at_11th(tmp_path, monkeypatch):
    """/api/auth/login：先注册出管理员，再用错密码打满——10 发 401 后第 11 发 429
    （限速先于凭据校验）。"""
    client = _fresh_client(tmp_path, monkeypatch)
    assert client.post(
        "/api/auth/register", json={"username": "boss", "password": "secret123"}
    ).status_code == 200
    codes = [
        client.post("/api/auth/login", json={"username": "boss", "password": "wrong"}).status_code
        for _ in range(LOGIN_PER_MIN + 1)
    ]
    assert all(c == 401 for c in codes[:LOGIN_PER_MIN]), codes
    assert codes[-1] == 429, codes


def test_register_endpoint_rate_limited_at_11th(tmp_path, monkeypatch):
    """/api/auth/register：同桶纪律，第 11 发 429（前 10 发是 200/409 的业务形状）。"""
    client = _fresh_client(tmp_path, monkeypatch)
    codes = [
        client.post("/api/auth/register", json={"username": f"u{i}", "password": "secret123"}).status_code
        for i in range(LOGIN_PER_MIN + 1)
    ]
    assert codes[-1] == 429, codes


# ── TTL 三条腿 ────────────────────────────────────────────────────────


def test_login_token_ttl_default_override_and_bad_value(monkeypatch):
    """缺省 86400；env 覆盖生效；坏值/零/负数一律回退缺省（不得把 TTL 打成 0/负）。"""
    monkeypatch.delenv("AF_LOGIN_TOKEN_TTL_S", raising=False)
    assert _login_token_ttl_s() == 86400.0

    monkeypatch.setenv("AF_LOGIN_TOKEN_TTL_S", "3600")
    assert _login_token_ttl_s() == 3600.0

    for bad in ("not-a-number", "", "0", "-5"):
        monkeypatch.setenv("AF_LOGIN_TOKEN_TTL_S", bad)
        assert _login_token_ttl_s() == 86400.0, bad


def test_issue_for_agent_reads_env_ttl_at_call_time(tmp_path, monkeypatch):
    """issue_for_agent(ttl_s=None) 在调用时刻读 env：覆盖后 expires_at ≈ now+覆盖值；
    显式 ttl_s 仍优先于 env。"""
    reg = TokenRegistry(tmp_path / "revoked.json", issued_path=tmp_path / "issued.json")

    monkeypatch.setenv("AF_LOGIN_TOKEN_TTL_S", "3600")
    before = time.time()
    reg.issue_for_agent("owner", token="af_ttl_env_1")
    info = reg._issued[_digest("af_ttl_env_1")]  # 批 2：签发令牌落摘要索引
    assert info.expires_at is not None
    assert (before + 3600) <= info.expires_at <= time.time() + 3600 + 5, info.expires_at

    reg.issue_for_agent("owner", token="af_ttl_explicit", ttl_s=60)
    assert reg._issued[_digest("af_ttl_explicit")].expires_at <= time.time() + 60 + 5

    # 顶层常量 ISSUED_TTL_S 保留为旧缺省口径（类属性不再参与运行期缺省，防口径漂移）
    assert TokenRegistry.ISSUED_TTL_S == 86400.0
