"""admin 运维档（v1.13.0，2026-10-23 autoflow 三级身份制照搬）的判定腿。

`SCOPES` 扩了 "admin"、`_SCOPE_SATISFIED_BY` 让 admin 蕴含 read/write/live（反向不蕴含）、
`requires("admin")` 另有 owner subject 名单通道（WebUI 登录令牌 subject=owner 视同持 admin）。
gap #6 的四端点（watch stop/start、system/update、DELETE automations）从 `_write` 换 `_admin`。

失败形状（都会静默不报错，必须钉住）：
① SCOPES 白名单漏了 admin ⇒ AF_ACCESS_TOKENS 里配的 admin 被静默过滤回落 read
  （`_parse_meta` 的白名单语义），四端点全部 403 且无日志；
② 蕴含表忘改 ⇒ admin 令牌调普通 write 端点反而 403（比不加还糟的方向）；
③ owner 名单通道缺失 ⇒ WebUI 登录（issue_for_agent("owner") 默认不带 admin）被
  升级页/watch 治理全数拒之门外——部署面失联；
④ 配对令牌若能自动拿到 admin ⇒ agent 自部署不设防（违背"身份由人签发"铁律）。
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient  # noqa: E402

from autoforge.af_api import build_app
from autoforge.af_auth import SCOPES


def _client(tmp_path, monkeypatch, tokens: dict):
    monkeypatch.setenv("AF_ACCESS_TOKENS", json.dumps(tokens))
    return TestClient(build_app(str(tmp_path / "store")))


def _tok(subject: str, scopes: list[str]) -> dict:
    return {"subject": subject, "scopes": scopes}


ADMIN = "af_admin_token_0000000000"
WRITER = "af_writer_token_00000000"
PAIRLIKE = "af_pairlike_token_0000000"
OWNER = "af_owner_token_0000000000"
TOKENS = {
    ADMIN: _tok("dev-agent", ["read", "write", "live", "admin"]),
    WRITER: _tok("worker", ["read", "write", "live"]),
    PAIRLIKE: _tok("paired-agent", ["read", "write", "live"]),  # 配对签发预设=expert 档
    # WebUI 登录签发口径：subject=owner、默认预设不含 admin（issue_for_agent 默认值）
    OWNER: _tok("owner", ["read", "write"]),
}


def test_scopes_constant_carries_admin():
    assert "admin" in SCOPES, (
        SCOPES, "SCOPES 白名单没有 admin ⇒ AF_ACCESS_TOKENS 配的 admin 会被静默过滤回落 read"
    )


def test_admin_implies_read_write_live_but_not_conversely():
    from autoforge.af_api import _SCOPE_SATISFIED_BY, _scope_ok

    assert _scope_ok("read", {"admin"}) and _scope_ok("write", {"admin"}) and _scope_ok("live", {"admin"})
    assert _scope_ok("admin", {"admin"})
    # 反向不蕴含：write/live/read 令牌碰 admin 门必须 403（在 HTTP 层测，见下）
    assert not _scope_ok("admin", {"write", "live", "read"})


def test_write_token_gets_403_on_all_four_gap6_endpoints(tmp_path, monkeypatch):
    """gap #6 收口的判定腿：write 令牌（无 admin）打四端点一律 403。"""
    client = _client(tmp_path, monkeypatch, TOKENS)
    h = {"Authorization": f"Bearer {WRITER}"}
    assert client.post("/api/watch/stop", headers=h).status_code == 403
    assert client.post("/api/watch/start", json={}, headers=h).status_code == 403
    assert client.post("/api/system/update", json={"branch": "master"}, headers=h).status_code == 403
    assert client.delete("/api/automations/whatever", headers=h).status_code == 403


def test_admin_token_passes_the_gate(tmp_path, monkeypatch):
    """admin 令牌过门（探针以状态码区分：过鉴权后吃业务 400/404/409，不再 403）。"""
    client = _client(tmp_path, monkeypatch, TOKENS)
    h = {"Authorization": f"Bearer {ADMIN}"}
    assert client.post("/api/system/update", json={"branch": "master"}, headers=h).status_code != 403
    # watch/start 带 ir 才过业务校验；403 与 4xx-非403 都说明门已过——这里钉"不是 403"
    assert client.post("/api/watch/start", json={"ir": {"ir_version": "0.2.1", "version": 1, "automations": []}}, headers=h).status_code != 403
    assert client.delete("/api/automations/不存在", headers=h).status_code != 403


def test_ref_whitelist_rejects_arbitrary_branch(tmp_path, monkeypatch):
    """发布线清单 §一.4 翻格：branch 取值域 = master | vX.Y.Z。任意字符串 400。"""
    client = _client(tmp_path, monkeypatch, TOKENS)
    h = {"Authorization": f"Bearer {ADMIN}"}
    assert client.post("/api/system/update", json={"branch": "main"}, headers=h).status_code == 400
    assert client.post("/api/system/update", json={"branch": "dev; rm -rf"}, headers=h).status_code == 400
    assert client.post("/api/system/update", json={"branch": "v1.13.0"}, headers=h).status_code != 403
    assert client.post("/api/system/update", json={"branch": "master"}, headers=h).status_code != 403


def test_paired_agent_preset_never_carries_admin(tmp_path, monkeypatch):
    """配对签发预设（read/write/live）打 admin 门必须 403——admin 永不经配对自动获得。"""
    assert SCOPES.index("admin") > SCOPES.index("live")  # 常量序：admin 是后加的运维档
    client = _client(tmp_path, monkeypatch, TOKENS)
    h = {"Authorization": f"Bearer {PAIRLIKE}"}
    assert client.post("/api/system/update", json={"branch": "master"}, headers=h).status_code == 403
    assert client.delete("/api/automations/whatever", headers=h).status_code == 403


def test_owner_subject_channel_lets_webui_login_through(tmp_path, monkeypatch):
    """owner 名单通道：WebUI 登录令牌（subject=owner、默认预设不含 admin）过 admin 门。

    requires("admin") 对 subject ∈ _OWNER_SUBJECTS 的令牌直接放行——否则 WebUI 登录
    签发的 owner 令牌被升级页/watch 治理全数拒之门外，部署面失联。
    """
    client = _client(tmp_path, monkeypatch, TOKENS)
    h = {"Authorization": f"Bearer {OWNER}"}
    assert client.post("/api/system/update", json={"branch": "master"}, headers=h).status_code != 403
