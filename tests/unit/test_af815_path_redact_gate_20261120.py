# -*- coding: utf-8 -*-
"""AF-815 判据 · 路径泄露三出口脱敏（墨鉴 BUG-006＋BUG-007＋PM 自抓 tv.py:596，2026-11-20）。

缺陷形状（三条，缺一不可）：
1. **上游根因（BUG-007）** `af_store.py` `InvalidArchiveName` docstring 承诺「message 不含路径」，
   实现 `{name!r}` 却把**原始归档名**整串拼进 message —— 归档名本身可能携带路径／`..`／NUL／
   `user:pass@` 凭据片段，agent 拿到的回执就把这些原文外泄。
2. **回执＋持久化双污染（BUG-006）** `af_mcp.py` dispatch 的 `except InvalidArchiveName` 直接
   `str(exc)` 传给 `_annotate_failure`，而后者内部首行就把 message 落遥测/知识库 history（洗不掉）——
   同函数其余四出口都过了 `_redact_paths`，唯独这一支漏网。
3. **端点回显（PM 自抓 tv.py:596）** `_post` 端点校验报错原样回显完整 endpoint，可能带内嵌凭据。

修法（AF-PM 卡面）：① af_store 异常 message 改「reason＋长度/首字符摘要」不拼 name 原文；
② af_mcp:1956 补 `_redact_paths`；③ tv.py:596 不回显 URL 只报「缺主机名或含 @ 凭证注入」。

判据（每类原文都不许出现在回执）：
- A1 三种原始 name（含路径/超长/`@` 凭据）都不进 `InvalidArchiveName.message`（self.name 仍保留）
- A2 MCP dispatch 的 InvalidArchiveName 回执不出现绝对路径/原 name，且 `_annotate_failure` 落库前已脱敏
- A3 tv `_post` 端点缺主机名时，回执不出现完整 endpoint（含 `user:pass@`），reason 仍报
"""

from __future__ import annotations

import json

import pytest

from autoforge import af_mcp
from autoforge.af_adapters.http import host_of
from autoforge.af_adapters.tv import ADM_ERR_AUTH_REQUIRED, TvMcpHttpClient, _UpstreamFailure
from autoforge.af_mcp import dispatch
from autoforge.af_store import GraphStore, InvalidArchiveName


# ── A1：InvalidArchiveName.message 不拼原名原文 ──────────────────────────

@pytest.mark.parametrize(
    "name, reason",
    [
        ("../../etc/passwd", "含路径字符"),
        ("user:pass@evil.example:8123/api", "含路径字符"),
        ("a" * 200, "超过 120 字符"),
        ("/etc/shadow", "含路径字符"),
    ],
)
def test_a1_message_never_contains_the_raw_name(name, reason):
    exc = InvalidArchiveName(name, reason)
    assert exc.name == name, "self.name 必须保留原始值供服务层定位"
    assert name not in str(exc), f"message 不许拼原名原文：{str(exc)}"


def test_a1_message_reports_reason_length_and_head():
    exc = InvalidArchiveName("../../etc/passwd", "含路径字符")
    msg = str(exc)
    assert "含路径字符" in msg, "reason 要在"
    assert "长 16 字符" in msg, "长度摘要要在"
    assert "首 '" in msg and ".." in msg, "首字符摘要要在（⛔ 不是整串）"


# ── A2：MCP dispatch 的 InvalidArchiveName 回执已脱敏且持久化前脱敏 ──────

def test_a2_mcp_reply_and_annotate_are_both_redacted(tmp_path, monkeypatch):
    """`af_diff` 喂一个带路径的名字 ⇒ 回执 + 传给 `_annotate_failure` 的 message 都不带原文。"""
    captured: dict[str, str] = {}
    real_annotate = af_mcp._annotate_failure

    def spy_annotate(store, name, message, hint_code=None):
        captured["message"] = message
        return real_annotate(store, name, message, hint_code=hint_code)

    monkeypatch.setattr(af_mcp, "_annotate_failure", spy_annotate)
    store = GraphStore(str(tmp_path / "store"))
    content, is_error = dispatch(
        "af_diff", {"name": "../../etc/passwd", "old": 1, "new": 2}, store, None
    )
    assert is_error is True
    blob = json.dumps(json.loads(content[0]["text"]), ensure_ascii=False)
    assert "etc/passwd" not in blob, f"原路径外泄进回执：{blob}"
    assert "etc/passwd" not in captured["message"], (
        f"持久化 message 未脱敏（落遥测/知识库洗不掉）：{captured['message']}"
    )


def test_a2_reason_still_surfaces_for_recoverable_input():
    """脱敏≠消音：agent 仍要能看到「归档名非法」的 reason，⛔ 不许抹成'没有错'。"""
    store = GraphStore(str("/tmp/nonexistent-af815-store"))
    content, is_error = dispatch(
        "af_diff", {"name": "../../etc/passwd", "old": 1, "new": 2}, store, None
    )
    assert is_error is True
    blob = json.dumps(json.loads(content[0]["text"]), ensure_ascii=False)
    assert "归档名非法" in blob, "reason 必须可读（对端可自纠）"
    assert "etc/passwd" not in blob


# ── A3：tv `_post` 端点校验不回显完整 endpoint ──────────────────────────

def test_a3_tv_missing_host_does_not_echo_endpoint():
    """无 host（`user:pass@` 注入）⇒ 具名失败，⛔ 不回显 endpoint / 内嵌凭据。"""
    client = TvMcpHttpClient("user:pass@only")
    assert host_of("user:pass@only") == "", "前置：该 endpoint 的 host 必须为空（才会进 _post 首行守卫）"
    with pytest.raises(_UpstreamFailure) as ei:
        client._post({"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert ei.value.code == ADM_ERR_AUTH_REQUIRED
    assert "user:pass@" not in str(ei.value), f"endpoint 内嵌凭据外泄：{ei.value}"
    assert "缺主机名或含 @ 凭证注入" in str(ei.value), "reason 仍要报"