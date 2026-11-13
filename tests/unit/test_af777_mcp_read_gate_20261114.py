# -*- coding: utf-8 -*-
"""AF-777 判据 · MCP 读面收口到单源闸（`_t_get_graph`，2026-11-14）。

**缺陷**：`_t_get_graph` 自己内联了一份归属判定（`caller and not _owner_face(current)`
＋ `_existing_owner(...) != caller`），而服务层 `svc._assert_archive_visible` 是同一件事的
**第二份实现**（同一个 `decide` 判定表、同一句 `visible_error` 文案）。两套就有分叉：
- 残缺身份（持令牌缺 subject）在工具层被 `caller` 空串提前放行 ⇒ 服务层那档 BROKEN 403
  在这条路径上**根本没机会说话**；
- 归属通过但归档/版本不存在时，`FileNotFoundError` 冒到 dispatcher，回执是
  「没找到该归档/版本：<路径>」——与「他人的」那句**不同形** ⇒ 拿两种回执一比就知存在性。

**修法（§1.216 裁定）**：删内联分支，只调 `svc._assert_archive_visible`；`get_graph` 的
`FileNotFoundError` 翻成**同一份** 404 载荷（文案走 `visible_error` 单源）。⛔ 不动 HTTP 面、
⛔ 不动 `_guard` 的 `scope=None` 放行结构。

**判据**：

- T1 非 owner 读他人归档 vs 读不存在 ⇒ **同一份载荷**（模板逐字同形、键集/子码/fix 全等）
- T2 残缺身份（持令牌缺 subject）⇒ 403 拒（`ADM_ERR_AUTH_REQUIRED` + `FORBIDDEN`），⛔ 不回 IR
- T3 无令牌面（`current=None`，且 `AUTOFORGE_MCP_ALLOW_NO_TOKEN=1`）⇒ 行为不变：能读他人归档
- T4 现网形状（`subject=dsh` 读自己名下）⇒ 不变：ok=True 且拿到 IR
- T5 单源钉：`_t_get_graph` 里⛔ 不许再出现内联归属判定（`_existing_owner`／`_owner_face` 判定）
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from autoforge.af_ir import Graph, load_automation
from autoforge.af_mcp import _t_get_graph, dispatch
from autoforge.af_ownership import broken_identity_error, visible_error
from autoforge.af_service import ServiceError as SvcError  # noqa: F401  （形状对照用）
from autoforge.af_store import GraphStore
from homesdk.adm.errors import ADM_ERR_AUTH_REQUIRED, ADM_ERR_PAYLOAD_INVALID

IR: dict[str, Any] = {
    "ir_version": "0.2.1", "id": "af777_ir", "name": "AF777 归档",
    "version": 1, "mode": "single",
    "nodes": [
        {"id": "t1", "kind": "on", "name": "人",
         "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
        {"id": "p1", "kind": "pass", "name": "结束"},
    ],
    "edges": [{"from": "t1", "to": "p1", "kind": "then"}],
}


def _store(tmp_path) -> GraphStore:
    store = GraphStore(str(tmp_path))
    store.save(Graph([load_automation(IR)]), "alice_ir", owner="alice")
    store.save(Graph([load_automation(IR)]), "bob_ir", owner="bob")
    store.save(Graph([load_automation(IR)]), "dsh_ir", owner="dsh")
    return store


ALICE = {"subject": "alice", "scopes": ["read", "write", "live", "admin"]}
DSH = {"subject": "dsh", "scopes": ["read", "write", "live"]}
BROKEN = {"scopes": ["read", "write"]}  # 持令牌却缺 subject（MCP 面残缺身份）


def _shape(payload: dict[str, Any]) -> tuple:
    """载荷形状指纹：键集 + 子码 + fix（文案另行按模板比对）。"""
    err = payload.get("error") or {}
    return (tuple(sorted(payload)), payload.get("code"), err.get("code"),
            json.dumps(err.get("fix") or {}, sort_keys=True, ensure_ascii=False))


# ── T1 他人归档 vs 不存在：同一份载荷 ─────────────────────────────────
def test_t1_other_owner_and_missing_are_verbatim_identical(tmp_path):
    """判据：同一个调用方，读「他人的」与读「不存在的」必须回**同一份**载荷。

    ⛔ 只要两者有一处不同（文案模板／子码／fix／甚至键顺序），拿两次回执一比就能
    判断"这条归档到底存不存在"——归属隔离就漏了。
    """
    store = _store(tmp_path)

    other = _t_get_graph(store, {"name": "bob_ir"}, ALICE)
    missing = _t_get_graph(store, {"name": "no_such_archive"}, ALICE)

    assert other["ok"] is False and missing["ok"] is False
    assert _shape(other) == _shape(missing), (
        "他人归档与不存在必须是同一份载荷形状：%r vs %r" % (_shape(other), _shape(missing))
    )
    assert "ir" not in other and "ir" not in missing, "⛔ 拒的时候不许回 IR"

    # 文案逐字同形：把名字替换成同一占位后必须完全相等（模板单源）
    m_other = other["error"]["message"].replace("bob_ir", "{NAME}")
    m_missing = missing["error"]["message"].replace("no_such_archive", "{NAME}")
    assert m_other == m_missing, "文案模板必须逐字同形：%r vs %r" % (m_other, m_missing)
    # 且文案就是 HTTP 面那条单源（同一句 visible_error）
    assert other["error"]["message"] == visible_error("bob_ir")
    assert missing["error"]["message"] == visible_error("no_such_archive")


# ── T2 残缺身份：403 拒 ──────────────────────────────────────────────
def test_t2_broken_identity_is_rejected_403(tmp_path):
    """判据：持令牌缺 subject ⇒ 必须 403 拒，⛔ 不许像修前那样被当成"未携带身份"放行。"""
    store = _store(tmp_path)

    out = _t_get_graph(store, {"name": "alice_ir"}, BROKEN)

    assert out["ok"] is False, "残缺身份⛔ 不许读到任何归档"
    assert out["code"] == ADM_ERR_AUTH_REQUIRED, "403 档载荷（与写面同码）"
    assert (out.get("error") or {}).get("code") == "FORBIDDEN"
    assert out["error"]["message"] == broken_identity_error(), "文案单源"
    assert "ir" not in out


# ── T3 无令牌面：行为不变（设计内逃生舱）─────────────────────────────
def test_t3_no_token_face_unchanged(tmp_path, monkeypatch):
    """判据：没有令牌身份（`current=None`）⇒ 与修前一致，能读他人归档。

    修前那条内联分支的条件是 `caller and ...`——`caller` 空串时整段跳过；
    收口后走 `Subject.NONE` 豁免档，结论相同。本腿钉住「收口没有顺手把逃生舱关掉」。
    """
    monkeypatch.setenv("AUTOFORGE_MCP_ALLOW_NO_TOKEN", "1")
    store = _store(tmp_path)

    out = _t_get_graph(store, {"name": "alice_ir"}, None)

    assert out.get("ok") is True, "无令牌面必须仍能读（设计内豁免，⛔ 修 bug 顺手关掉）"
    assert out.get("name") == "alice_ir" and out.get("ir")

    # 走完整 dispatch 也一样（registry.enabled=False 的部署形状＝没有 current）
    content, is_err = dispatch("af_get_graph", {"name": "bob_ir"}, store, None)
    body = json.loads(content[0]["text"])
    assert is_err is False and body.get("ok") is True, "无令牌部署的 dispatch 面同样不受影响"


# ── T4 现网形状：自己的归档照常读 ─────────────────────────────────────
def test_t4_own_archive_still_readable(tmp_path):
    """判据：`subject=dsh` 读自己名下 ⇒ 不变（ok=True、拿到 IR 与版本）。"""
    store = _store(tmp_path)

    out = _t_get_graph(store, {"name": "dsh_ir"}, DSH)

    assert out.get("ok") is True
    assert out.get("name") == "dsh_ir" and out.get("version") == 1
    assert out.get("ir"), "自己的归档必须拿到内容"
    # 相邻反证：同一身份读他人的，仍是 T1 那份 404
    other = _t_get_graph(store, {"name": "alice_ir"}, DSH)
    assert other["ok"] is False and other["error"]["code"] == "ARCHIVE_NOT_FOUND"


# ── T5 单源钉：工具层⛔ 不许再内联归属判定 ────────────────────────────
def test_t5_source_pin_single_gate():
    """判据：`_t_get_graph` 只许调服务层唯一入口，⛔ 不许再出现第二份判定。

    ⚠️ 用 AST 取**代码里的名字**，不扫源码文本：本函数的 docstring 里就要提到
    `_existing_owner`（讲清根因），文本钉会把"说明"误判成"实现"。
    """
    import ast
    import inspect

    from autoforge import af_mcp

    fn = next(
        n for n in ast.walk(ast.parse(inspect.getsource(af_mcp).lstrip()))
        if isinstance(n, ast.FunctionDef) and n.name == "_t_get_graph"
    )
    used: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            used.add(node.attr)
    assert "_assert_archive_visible" in used, "读面必须调服务层单源闸"
    assert "_existing_owner" not in used, "⛔ 工具层不许自己读归属（AF-777 根因）"
    assert "_owner_face" in used, "身份旗仍要透传（判定在服务层做）"

    payload_fn = next(
        n for n in ast.walk(ast.parse(inspect.getsource(af_mcp).lstrip()))
        if isinstance(n, ast.FunctionDef) and n.name == "_archive_not_found_payload"
    )
    assert "visible_error" in inspect.getsource(af_mcp._archive_not_found_payload), (
        "404 文案必须来自 visible_error 单源，⛔ 手写第二份"
    )
    assert payload_fn is not None
