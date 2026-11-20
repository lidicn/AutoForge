# -*- coding: utf-8 -*-
"""AF-819 判据 · af_export_store 面收口：无身份拒绝导出（2026-11-20，出资人拍板）。

缺陷本体：`af_mcp.py` 的 `af_export_store` 注册项 **scope=None**（公开面）⇒ `_guard`
对 scope=None 直接放行 ⇒ 无令牌也能经 MCP 拿到**全量 bundle**（svc.export_store 的
NONE 档）——AF-504 份额化只收了"带身份"的面，无身份那条通道漏了。

修法（AF-PM 卡面，三条全落）：① 注册项 scope None→"read"（`_guard` 默认拒绝生效）＋
工具描述改份额语义；② `svc.export_store` docstring 现势改写（⛔ 函数体不动）；
③ 本文件六腿。⛔ AF_ALLOW_NOAUTH 逃生舱语义不动（两枚既有测试钉着 noauth=200）；
⛔ CLI 直调 `store.export_bundle()` 不碰（本地操作者路径）。

判据：
- E1 MCP 无令牌 dispatch af_export_store ⇒ isError True＋「默认拒绝」＋回执含
  AUTOFORGE_MCP_ALLOW_NO_TOKEN 指引（修前=全量 bundle）
- E2 HTTP /mcp 面（AF_ALLOW_NOAUTH=1 但工具层无身份）同拒——镜像
  test_http_mcp_face_shares_the_same_default 形状：「HTTP 面拒、MCP 面放」不再存在
- E3 read/write 令牌非 owner ⇒ 份额＋hidden_count 报缺口
- E4 owner 面 ⇒ 全量
- E5 REST 无令牌且未设 AF_ALLOW_NOAUTH ⇒ 403（export 端点本身的钉）
- E6 REST AF_ALLOW_NOAUTH=1 ⇒ 200（逃生舱保留）
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from autoforge.af_api import build_app
from autoforge.af_ir import Graph, load_automation
from autoforge.af_mcp import TOOLS, dispatch
from autoforge.af_store import GraphStore


def _ir(name: str) -> dict:
    return {
        "ir_version": "0.2.1", "id": name, "name": name, "version": 1, "mode": "single",
        "nodes": [
            {"id": "t1", "kind": "on", "name": "t",
             "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
            {"id": "p1", "kind": "pass", "name": "p"},
        ],
        "edges": [{"from": "t1", "to": "p1", "kind": "then"}],
    }


def _store(tmp_path) -> GraphStore:
    store = GraphStore(str(tmp_path))
    store.save(Graph([load_automation(_ir("af819_a"))]), "af819_a", tags=["t"], owner="agent-a")
    store.save(Graph([load_automation(_ir("af819_b"))]), "af819_b", tags=["t"], owner="agent-b")
    return store


def _scope_of(name: str):
    return next((scope for (n, _d, _s, _f, scope) in TOOLS if n == name), "missing")


# ── 前提钉：scope 行为本身就是修复的一半 ──────────────────────────────

def test_e0_af_export_store_scope_is_read_not_none():
    """修前这格是 None（公开面直通）；修复＝注册项 scope None→"read"。"""
    assert _scope_of("af_export_store") == "read", (
        "af_export_store 的 scope 必须是 'read'（AF-819：无身份拒绝导出）"
    )
    assert _scope_of("af_import_store") == "write", "对照：导入面仍是 write（本卡不动）"


# ── E1：MCP 无令牌 ⇒ 默认拒绝 ────────────────────────────────────────

def test_e1_mcp_no_token_is_default_denied(tmp_path):
    store = _store(tmp_path)
    content, is_error = dispatch("af_export_store", {}, store, None)
    assert is_error is True
    body = json.loads(content[0]["text"])
    assert "默认拒绝" in body["message"], body
    assert "AUTOFORGE_MCP_ALLOW_NO_TOKEN" in body["message"], "拒绝话必须说出怎么显式放行"
    assert body["code"] == "ADM_ERR_AUTH_REQUIRED"
    assert "bundle" not in json.dumps(body), "⛔ 不许仍回全量 bundle（修前形状）"


# ── E2：HTTP /mcp 面同拒（镜像 default-deny 形状）────────────────────

def test_e2_http_mcp_face_shares_the_same_default(tmp_path, monkeypatch):
    """AF_ALLOW_NOAUTH=1 只放行 HTTP 层；工具层无身份照样被拒——两道门不许对着干。"""
    monkeypatch.delenv("AUTOFORGE_MCP_ALLOW_NO_TOKEN", raising=False)
    monkeypatch.setenv("AF_ALLOW_NOAUTH", "1")
    store = _store(tmp_path)
    client = TestClient(build_app(store_root=str(store.root)))
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "af_export_store", "arguments": {}}}
    out = client.post("/mcp", json=body).json()
    result = out["result"]
    assert result["isError"] is True, f"HTTP 面也必须拒（⛔ 'HTTP 拒、MCP 放'的分裂）：{result}"
    assert "默认拒绝" in result["content"][0]["text"]
    assert "bundle" not in result["content"][0]["text"]


# ── E3：非 owner 令牌 ⇒ 份额 + hidden_count ──────────────────────────

def test_e3_non_owner_token_gets_own_share_with_hidden_count(tmp_path):
    store = _store(tmp_path)
    current = {"subject": "agent-a", "scopes": ["read"]}
    content, is_error = dispatch("af_export_store", {}, store, current)
    assert is_error is False, content
    body = json.loads(content[0]["text"])
    assert body["names"] == ["af819_a"], f"只能导自己名下：{body['names']}"
    assert body["hidden_count"] == 1, "缺口必须报数（⛔ 静默少给）"
    assert body["scope"] == "owner:agent-a"


# ── E4：owner 面令牌 ⇒ 全量 ──────────────────────────────────────────

def test_e4_owner_face_token_gets_everything(tmp_path):
    store = _store(tmp_path)
    current = {"subject": "owner", "scopes": ["read", "write", "live"]}
    content, is_error = dispatch("af_export_store", {}, store, current)
    assert is_error is False
    body = json.loads(content[0]["text"])
    assert sorted(body["names"]) == ["af819_a", "af819_b"], "owner 面=全量（AF-504 原语义保持）"
    assert "hidden_count" not in body


# ── E5：REST 无令牌 ⇒ 403（export 端点本身）──────────────────────────

def test_e5_rest_export_without_token_and_without_noauth_is_403(tmp_path, monkeypatch):
    monkeypatch.delenv("AF_ALLOW_NOAUTH", raising=False)
    store = _store(tmp_path)
    client = TestClient(build_app(store_root=str(store.root)))
    r = client.get("/api/store/export")
    assert r.status_code == 403, f"REST 无令牌导出必须拒：{r.status_code}"


# ── E6：REST 逃生舱保留 ──────────────────────────────────────────────

def test_e6_rest_noauth_escape_hatch_still_200(tmp_path, monkeypatch):
    """AF_ALLOW_NOAUTH=1 ⇒ 200（操作者显式本地开发选择；既有两枚测试钉的形状，本卡不动）。"""
    monkeypatch.setenv("AF_ALLOW_NOAUTH", "1")
    store = _store(tmp_path)
    client = TestClient(build_app(store_root=str(store.root)))
    r = client.get("/api/store/export")
    assert r.status_code == 200, f"逃生舱必须保留：{r.status_code}"
