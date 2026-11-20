# -*- coding: utf-8 -*-
"""AF-822/823 判据 · tv.py 会话复位＋信封回退撤销（墨鉴 N-15／N-16，2026-11-20）。

**AF-822（N-15）**：`TvMcpHttpClient._session_id` 握上后永不清除、404 归 ADM_ERR_INTERNAL
不触发重新握手 ⇒ 适配器单例一张死会话污染进程后续所有 tv 动作直到 serve 重启。
修法：404（带旧 `Mcp-Session-Id`）⇒ 私有 `_SessionExpired` ⇒ `call()` 置 `_session_id=None`
重握手一次（**限一次防循环**），二次仍失败才具名报错（"会话失效已重试"）；握手（无会话头）
404 仍是端点错 INTERNAL。

**AF-823（N-16）**：`_decode_envelope` id 不匹配回退 `candidates[-1]` ⇒ 最坏把对端通知当
本次答复。修法：通知帧（无 id）显式跳过；id 不匹配一律具名失败 ⛔ 回退。

全部离线（假 `guarded_open`）。opener 的应答**回显请求 id**——MCP 客户端按 id 匹配，
硬编码 id 会与 `_next_id()` 计数错位。
"""

from __future__ import annotations

import json

import pytest
from urllib.error import HTTPError

from autoforge.af_adapters import tv as tv_module
from autoforge.af_adapters.tv import (
    ADM_ERR_INTERNAL,
    ADM_ERR_PAYLOAD_INVALID,
    TvAdapter,
    TvMcpHttpClient,
)


class _FakeResponse:
    def __init__(self, body, *, content_type: str = "application/json", session_id: str | None = None):
        self._body = body if isinstance(body, bytes) else str(body).encode("utf-8")
        self.headers = {"content-type": content_type}
        if session_id:
            self.headers["mcp-session-id"] = session_id

    def read(self, n: int = -1):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _terminal(result: str = "iptv_ok") -> dict:
    return {"result": result, "stage": "done", "gap": None, "detail": None,
            "af_speech": None, "s3_s4": {}, "screen_on_after": None,
            "steps": [], "total_ms": 1.0}


def _body_of(req) -> dict:
    return json.loads(bytes(req.data).decode("utf-8"))


class _FakeOpener:
    """假出站。条目："init"/sid、"notify"（202 空体）、"err404"、"ok"/payload。

    `init` 与 `ok` 都**回显请求 id**（按 id 匹配是客户端契约）。
    """

    def __init__(self, items):
        self.items = list(items)
        self.requests = []

    def __call__(self, req, *, allowed_hosts, timeout=None):
        self.requests.append(req)
        kind, *rest = self.items.pop(0)
        body = _body_of(req)
        if kind == "err404":
            raise HTTPError("http://tvpilot.invalid:8090/mcp", 404, "err", {}, None)
        if kind == "notify":
            return _FakeResponse(b"")
        if kind == "init":
            return _FakeResponse(
                json.dumps({"jsonrpc": "2.0", "id": body.get("id"), "result": {}}),
                session_id=rest[0] if rest else None,
            )
        payload = rest[0] if rest else _terminal()
        frame = "data: " + json.dumps(
            {"jsonrpc": "2.0", "id": body.get("id"),
             "result": {"structuredContent": payload, "content": []}}
        ) + "\n\n"
        return _FakeResponse(frame, content_type="text/event-stream")


def _methods_of(opener) -> list:
    return [_body_of(r).get("method") for r in opener.requests]


def _hdrs(req) -> dict:
    return {str(k).lower(): str(v) for k, v in dict(getattr(req, "headers", {})).items()}


# ══ AF-822：会话复位 ════════════════════════════════════════════════════

def test_822_404_with_session_resets_and_rehandshakes_once(monkeypatch):
    """tools/call 收 404（死会话）⇒ 清会话重握手一次 ⇒ 重试成功且握上新会话。"""
    opener = _FakeOpener([
        ("init", "stale"),
        ("notify",),
        ("err404",),            # tools/call：对端已丢弃该会话
        ("init", "fresh"),      # 复位后重握手
        ("notify",),
        ("ok", _terminal()),    # 重试成功
    ])
    monkeypatch.setattr(tv_module, "guarded_open", opener)
    adapter = TvAdapter(endpoint="http://tvpilot.invalid:8090", dry_run=False)
    result = adapter.call("morning_news", {"request_id": "r1"})
    assert result.success is True, f"会话复位后重试必须成功：{result.error}"
    assert adapter._http._session_id == "fresh", "复位后应握上新会话"
    assert _methods_of(opener) == [
        "initialize", "notifications/initialized", "tools/call",
        "initialize", "notifications/initialized", "tools/call",
    ], _methods_of(opener)
    assert _hdrs(opener.requests[2]).get("mcp-session-id") == "stale"
    assert _hdrs(opener.requests[5]).get("mcp-session-id") == "fresh"


def test_822_second_404_is_named_not_a_loop(monkeypatch):
    """二次仍 404 ⇒ 具名失败（"会话失效已重试"），⛔ 无限循环重握手。"""
    opener = _FakeOpener([
        ("init", "s1"),
        ("notify",),
        ("err404",),            # tools/call #1
        ("init", "s2"),         # 重握手
        ("notify",),
        ("err404",),            # 重试仍 404
    ])
    monkeypatch.setattr(tv_module, "guarded_open", opener)
    adapter = TvAdapter(endpoint="http://tvpilot.invalid:8090", dry_run=False)
    result = adapter.call("morning_news", {})
    assert result.success is False
    assert result.data["code"] == ADM_ERR_INTERNAL
    assert "会话失效已重试" in (result.error or ""), f"二次失败必须具名：{result.error}"
    assert len(opener.requests) == 6, f"⛔ 循环重握手：发了 {len(opener.requests)} 个请求"
    assert _methods_of(opener) == [
        "initialize", "notifications/initialized", "tools/call",
        "initialize", "notifications/initialized", "tools/call",
    ]


def test_822_handshake_404_without_session_stays_internal(monkeypatch):
    """无会话头的 404（握手/端点错）≠ 会话失效：仍归 INTERNAL，不触发复位循环。"""
    opener = _FakeOpener([("err404",)])
    monkeypatch.setattr(tv_module, "guarded_open", opener)
    client = TvMcpHttpClient("http://tvpilot.invalid:8090/mcp")
    assert client._session_id is None
    with pytest.raises(tv_module._UpstreamFailure) as ei:
        client._post({"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert ei.value.code == ADM_ERR_INTERNAL
    assert not isinstance(ei.value, tv_module._SessionExpired)


# ══ AF-823：信封回退撤销 ════════════════════════════════════════════════

def test_823_id_mismatch_is_named_failure_no_fallback():
    """id 不匹配 + 后面还有别的帧 ⇒ ⛔ 回退最后一条，一律具名失败。"""
    raw = (
        "data: " + json.dumps({"jsonrpc": "2.0", "id": 99, "result": {}}) + "\n\n"
        + "data: " + json.dumps({"jsonrpc": "2.0", "id": 77,
                                 "result": {"structuredContent": _terminal(), "content": []}}) + "\n\n"
    )
    with pytest.raises(tv_module._UpstreamFailure) as ei:
        tv_module._decode_envelope(raw.encode("utf-8"), "text/event-stream", 2)
    assert ei.value.code == ADM_ERR_PAYLOAD_INVALID
    assert "id=2" in str(ei.value), f"具名失败要点名找哪个 id：{ei.value}"


def test_823_notification_frames_are_skipped_not_matched():
    """对端通知帧（无 id）⇒ 显式跳过，⛔ 被当成本次答复。"""
    raw = (
        "data: " + json.dumps({"jsonrpc": "2.0", "method": "notifications/message",
                               "params": {"hello": 1}}) + "\n\n"
        + "data: " + json.dumps({"jsonrpc": "2.0", "id": 3,
                                 "result": {"structuredContent": _terminal(), "content": []}}) + "\n\n"
    )
    out = tv_module._decode_envelope(raw.encode("utf-8"), "text/event-stream", 3)
    assert out.get("id") == 3, "必须匹配到带 id 的那帧"


def test_823_notification_only_stream_is_named_failure():
    """整条流只有通知帧 ⇒ 具名失败（⛔ 旧回退会把通知当答复收下）。"""
    raw = (
        "data: " + json.dumps({"jsonrpc": "2.0", "method": "notifications/message",
                               "params": {"result": "sneaky"}}) + "\n\n"
    )
    with pytest.raises(tv_module._UpstreamFailure) as ei:
        tv_module._decode_envelope(raw.encode("utf-8"), "text/event-stream", 1)
    assert ei.value.code == ADM_ERR_PAYLOAD_INVALID


def test_823_plain_json_leg_still_matches_by_id():
    """application/json 腿（单帧）按 id 匹配不变。"""
    body = json.dumps({"jsonrpc": "2.0", "id": 7,
                       "result": {"structuredContent": _terminal(), "content": []}})
    out = tv_module._decode_envelope(body.encode("utf-8"), "application/json", 7)
    assert out["id"] == 7
    with pytest.raises(tv_module._UpstreamFailure):
        tv_module._decode_envelope(body.encode("utf-8"), "application/json", 8)


# ══ 端到端 ═════════════════════════════════════════════════════════════

def test_adapter_call_survives_session_reset_end_to_end(monkeypatch):
    opener = _FakeOpener([("init", "a1"), ("notify",), ("ok", _terminal())])
    monkeypatch.setattr(tv_module, "guarded_open", opener)
    adapter = TvAdapter(endpoint="http://tvpilot.invalid:8090", dry_run=False)
    result = adapter.call("morning_news", {"request_id": "e2e"})
    assert result.success is True
    assert result.data["result"] == "iptv_ok"