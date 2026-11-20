# -*- coding: utf-8 -*-
"""AF-830 判据 · ma.py 握手空体守卫（MA 读面联调窗实抓，2026-11-20）。

MCP streamable-http 规范对 JSON-RPC **通知**（`notifications/initialized`，请求不带 `id`）回
`202` 空体，content-type 仍是 `application/json`。`ma.py._post` 缺这道守卫 ⇒ `json.loads('')`
抛 JSONDecodeError ⇒ `_UpstreamFailure(ADM_ERR_PAYLOAD_INVALID)` ⇒ **每次握手都中止**：
AF→MA 任何真实 tools/call 在握手阶段就崩（联调窗 J1-J4 全「MA 响应不是合法 JSON」）。

修法：与 `af_adapters/tv.py` 的 `:681-685` 逐字同形的守卫——空体**且**请求不带 `id` ⇒ 返回 `{}`。
⛔ 不许解成静默空答复：**有 `id` 的空体仍然判形状塌**（两个方向各钉一次，⛔ 只钉一侧）。

全部离线（假 `guarded_open`）。opener 的应答**回显请求 id**——MCP 客户端按 id 匹配，
硬编码 id 会与 `_next_id()` 计数错位。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoforge.af_adapters import ma as ma_module
from autoforge.af_adapters.ma import (
    ADM_ERR_PAYLOAD_INVALID,
    MAX_RESPONSE_BYTES,
    MaAdapter,
    _UpstreamFailure,
)

_ENDPOINT = "http://ma.invalid:8086/mcp"


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


class _FakeOpener:
    """假出站。条目："init"/sid、"notify"（202 空体，content-type 仍是 application/json）、
    "empty"（空体，任意请求）、"ok"/payload。

    `init` 与 `ok` 都**回显请求 id**（按 id 匹配是客户端契约）。
    """

    def __init__(self, items):
        self.items = list(items)
        self.requests = []

    def __call__(self, req, *, allowed_hosts, timeout=None):
        self.requests.append(req)
        kind, *rest = self.items.pop(0)
        body = _body_of(req)
        if kind in ("notify", "empty"):
            # MCP 规范：通知回 202 空体；content-type 仍是 application/json
            return _FakeResponse(b"")
        if kind == "init":
            return _FakeResponse(
                json.dumps({"jsonrpc": "2.0", "id": body.get("id"), "result": {}}),
                session_id=rest[0] if rest else None,
            )
        payload = rest[0] if rest else {}
        frame = "data: " + json.dumps(
            {"jsonrpc": "2.0", "id": body.get("id"),
             "result": {"structuredContent": payload, "content": []}}
        ) + "\n\n"
        return _FakeResponse(frame, content_type="text/event-stream")


def _body_of(req) -> dict:
    return json.loads(bytes(req.data).decode("utf-8"))


def _methods_of(opener) -> list:
    return [_body_of(r).get("method") for r in opener.requests]


def _live(**overrides) -> MaAdapter:
    return MaAdapter(endpoint=_ENDPOINT, token="mcp_testtoken", dry_run=False, **overrides)


_TEMPLATES_EMPTY = {"ok": True, "total": 0, "templates": []}


# ══ ① 通知腿：202 空体不中止握手，随后 tools/call 正常 ═════════════════════

def test_830_notification_empty_202_does_not_abort_handshake(monkeypatch):
    """联调窗 J1-J4 的现场形状：initialize 成功 → 通知回 202 空体 → tools/call 应正常。"""
    opener = _FakeOpener([("init", "sess-af830"), ("notify",), ("ok", _TEMPLATES_EMPTY)])
    monkeypatch.setattr(ma_module, "guarded_open", opener)

    adapter = _live()
    result = adapter.call("list_analysis_templates", {})

    assert result.success is True, result
    assert result.data["templates"] == []
    assert _methods_of(opener) == [
        "initialize",
        "notifications/initialized",
        "tools/call",
    ], _methods_of(opener)
    # 握手成功后世话真握上了
    assert adapter._session_id == "sess-af830"


def test_830_notification_empty_body_returns_empty_envelope_not_failure(monkeypatch):
    """守卫正向腿：通知（无 id）收空体 ⇒ `_post` 直接返回 `{}`，⛔ 不抛。"""
    opener = _FakeOpener([("notify",)])
    monkeypatch.setattr(ma_module, "guarded_open", opener)

    adapter = _live()
    adapter._session_id = "sess-1"
    envelope = adapter._post(
        _ENDPOINT,
        "ma.invalid",
        "t",
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
    )

    assert envelope == {}


# ══ ② 有 id 的空体：仍然判形状塌（另一方向，⛔ 只钉一侧）═════════════════════

def test_830_id_carrying_empty_body_still_collapses_to_payload_invalid(monkeypatch):
    """⛔ 守卫不许把「有 id 的空体」也放成空答复——那是伪装成成功的形状塌。"""
    opener = _FakeOpener([("empty",)])
    monkeypatch.setattr(ma_module, "guarded_open", opener)

    adapter = _live()
    adapter._session_id = "sess-1"
    with pytest.raises(_UpstreamFailure) as raised:
        adapter._post(
            _ENDPOINT,
            "ma.invalid",
            "t",
            {"jsonrpc": "2.0", "id": 7, "method": "tools/call"},
        )

    assert raised.value.code == ADM_ERR_PAYLOAD_INVALID


def test_830_initialize_leg_with_empty_body_collapses(monkeypatch):
    """握手本体（有 id）收空体也必须失败：不能靠握手「成功」把半截状态传下去。"""
    opener = _FakeOpener([("empty",)])
    monkeypatch.setattr(ma_module, "guarded_open", opener)

    adapter = _live()
    with pytest.raises(_UpstreamFailure) as raised:
        adapter._ensure_session(_ENDPOINT, "ma.invalid", "t")

    assert raised.value.code == ADM_ERR_PAYLOAD_INVALID
    assert adapter._session_id is None  # ⛔ 没有会话被当成可用


# ══ ③ 字节上限优先：守卫不吃掉 MAX_RESPONSE_BYTES 的拒收腿 ═════════════════

def test_830_guard_does_not_swallow_the_byte_cap_refusal(monkeypatch):
    """超限检查在守卫之前：即使请求不带 id，超大空体也不能靠守卫躲过上限。"""

    class _HugeResp(_FakeResponse):
        def read(self, n: int = -1):
            return b"x" * (MAX_RESPONSE_BYTES + 1)

    monkeypatch.setattr(ma_module, "guarded_open", lambda req, **kw: _HugeResp(b""))
    adapter = _live()
    adapter._session_id = "sess-1"
    with pytest.raises(_UpstreamFailure) as raised:
        adapter._post(
            _ENDPOINT,
            "ma.invalid",
            "t",
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
        )

    assert raised.value.code == ADM_ERR_PAYLOAD_INVALID


# ══ ④ 源码钉：与 tv.py 逐字同形、位置在上限检查之后 / 解码之前 ══════════════

_GUARD = 'if not raw.strip() and "id" not in body:'
_MA_SRC = Path(__file__).resolve().parents[2] / "src" / "autoforge" / "af_adapters" / "ma.py"
_TV_SRC = Path(__file__).resolve().parents[2] / "src" / "autoforge" / "af_adapters" / "tv.py"


def test_830_guard_shape_matches_tv_verbatim():
    """同族两个适配器共用同一道守卫：tv.py 有、ma.py 也得有且**逐字同形**（防只修一半）。"""
    ma_src = _MA_SRC.read_text(encoding="utf-8")
    tv_src = _TV_SRC.read_text(encoding="utf-8")

    assert _GUARD in tv_src, "tv.py 的同款守卫不见了（AF-830 的参照物）"
    assert _GUARD in ma_src, "ma.py 缺空体守卫（AF-830 本缺陷）"
    assert ma_src.count(_GUARD) == 1
    assert tv_src.count(_GUARD) == 1


def test_830_guard_sits_after_byte_cap_and_before_decode():
    """位置钉：上限检查之后、`_decode_envelope` 之前——放错地方等于没修。"""
    ma_src = _MA_SRC.read_text(encoding="utf-8")

    i_cap = ma_src.index("if len(raw) > MAX_RESPONSE_BYTES:")
    i_guard = ma_src.index(_GUARD)
    i_decode = ma_src.index('return _decode_envelope(raw, content_type, body.get("id"))')

    assert i_cap < i_guard < i_decode, (i_cap, i_guard, i_decode)
    # 守卫必须在 `_post` 体内（`_ensure_session` 之前），别落进别的函数
    i_post = ma_src.index("    def _post(self, endpoint: str, host: str, token: str, body: Mapping[str, Any])")
    i_ensure = ma_src.index("    def _ensure_session(self, endpoint: str, host: str, token: str) -> None:")
    assert i_post < i_guard < i_ensure