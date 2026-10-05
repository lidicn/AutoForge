"""v1.5.0 接线单测：MCP 工具 + 异常回执归因 + HTTP 端点 + CLI。"""

from __future__ import annotations

import json

import pytest

from autoforge.af_mcp import TOOLS, dispatch
from autoforge.af_auth import SCOPES as _ALL_SCOPES

# 裁定 20261004 §一 Q2=B：MCP 面「无身份」改成默认拒绝，本文件的调用点因此逐条显式给身份；
# 「不给身份」那一档只由 test_dcd_20261004_mcp_default_deny.py 钉成"拒绝"。
_ALL = {"subject": "test-all", "scopes": sorted(_ALL_SCOPES)}
from autoforge.af_store import GraphStore

#: http 出站 → L3（首错 L3_ACTION）
BAD_IR = {
    "ir_version": "0.2.1", "id": "bad", "name": "bad", "version": 1, "mode": "single",
    "nodes": [
        {"id": "a", "kind": "on", "trigger": {"type": "state", "entity_id": "light.x", "to": "on"}},
        {"id": "d", "kind": "do", "adapter": "http", "action": "get", "params": {"url": "https://x"}},
        {"id": "p", "kind": "pass"},
    ],
    "edges": [
        {"from": "a", "to": "d", "kind": "then"},
        {"from": "d", "to": "p", "kind": "then"},
    ],
}


def test_new_tools_registered():
    names = {t[0] for t in TOOLS}
    assert {"af_experience", "af_telemetry"} <= names


def test_dispatch_failure_carries_attribution(tmp_path):
    """MCP 异常回执附归因 + 建议（v1.5.0 写→读闭环）。"""
    store = GraphStore(str(tmp_path))
    content, is_error = dispatch("af_save", {"name": "x", "ir": BAD_IR}, store, _ALL)
    assert is_error is True
    text = content[0]["text"]
    assert "拒绝归档" in text          # 原错误保留
    assert "↳ 归因：" in text          # 归因附加
    assert "IR" in text or "静态扫描" in text


def test_dispatch_experience_and_telemetry(tmp_path):
    store = GraphStore(str(tmp_path))
    content, is_error = dispatch("af_experience", {}, store, _ALL)
    assert is_error is False
    assert json.loads(content[0]["text"])["ok"] is True

    content, is_error = dispatch("af_telemetry", {}, store, _ALL)
    assert is_error is False
    assert json.loads(content[0]["text"])["ok"] is True


def test_http_endpoints(tmp_path, monkeypatch):
    monkeypatch.setenv("AF_ALLOW_NOAUTH", "1")
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from autoforge.af_api import build_app

    client = TestClient(build_app(str(tmp_path / "store")))
    for path in ("/api/experience", "/api/telemetry", "/api/catalog/resolve-metrics",
                 "/api/experience/export"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert resp.json()["ok"] is True, path


def test_cli_experience_and_telemetry(tmp_path):
    from typer.testing import CliRunner

    from autoforge.af_cli import app

    runner = CliRunner()
    assert runner.invoke(app, ["experience", "show", "--root", str(tmp_path)]).exit_code == 0
    assert runner.invoke(app, ["telemetry", "show", "--root", str(tmp_path)]).exit_code == 0
