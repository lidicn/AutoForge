# -*- coding: utf-8 -*-
"""`tv` 真接线待命件（批T）判据腿 —— AF-PM 卡 2026-11-18｜契约 v3 §2.2／§二-1.1。

卡面四件事逐条落判据：

1. `base.classify_action("tv", …)` **显式**落 L2（⛔ 靠"未知 domain ⇒ 保守 L2"的缺省档）；
2. `TvAdapter` 从 `af_adapters` 导出、`build_runtime` 按 `MaAdapter` 先例注册 `kind=tv`；
3. client 注入：端点注入（缺省空串＋显式报错）／不写死地址与凭据／LAN 无鉴权时不发 Authorization；
4. result 六值消费＋话术逐字（v3 §2.2）／休眠两值＝缺陷／240s＋60s 兜底「电视联动执行超时」／
   call 失败不冒执行链。

全部离线：MCP 传输用假 `guarded_open`（⛔ 一个字节都不出网），播报腿是桩函数（⛔ 不碰 bridge）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from autoforge.af_adapters import TvAdapter as ExportedTvAdapter
from autoforge.af_adapters.base import (
    L2_RISKY,
    KNOWN_SERVICE_DOMAINS,
    CallResult,
    classify_action,
    unrecognized_service,
)
from autoforge.af_adapters.base import _L2_DOMAINS
from autoforge.af_adapters.tv import (
    ACTIVE_RESULTS,
    ADM_ERR_AUTH_REQUIRED,
    ADM_ERR_INTERNAL,
    ADM_ERR_PAYLOAD_INVALID,
    DEFAULT_TV_TRANSPORT_TIMEOUT,
    DEFECT_DORMANT_RESULT,
    DORMANT_RESULTS,
    GRACE_S,
    PARAMS_ALLOWED,
    RESULTS,
    RUN_BUDGET_S,
    SPEECH_BY_RESULT,
    TERMINAL_DEADLINE_S,
    TERMINAL_KEYS,
    TIMEOUT_SPEECH,
    TOOL_MORNING_NEWS,
    TV_ACTIONS,
    TV_ENDPOINT_ENV,
    TvAdapter,
    TvAnnouncement,
    TvMcpHttpClient,
    TvOutcomeSpeaker,
    defect_of_result,
    speech_of,
)

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src" / "autoforge"


# ── 工具面 ────────────────────────────────────────────────────────────────


class _StubClient:
    """MCP 客户端桩：记下每次 `call(tool, **kwargs)`，按需返回回执或抛异常。"""

    def __init__(self, payload=None, *, raises: BaseException | None = None) -> None:
        self.payload = payload
        self.raises = raises
        self.calls: list[tuple[str, dict]] = []

    def call(self, tool: str, **kwargs):  # noqa: ANN003, ANN201 - 与 Protocol 同形
        self.calls.append((tool, dict(kwargs)))
        if self.raises is not None:
            raise self.raises
        return self.payload


def _terminal(result: str = "iptv_ok", **overrides):
    """对端 `_terminal` ＋ `_finish` 的九键形状（只换 result 那一枚）。"""
    stage = "done" if result in ("iptv_ok", "adb_off_tv_on") else "failed"
    record = {
        "result": result,
        "stage": stage,
        "gap": None,
        "detail": None,
        "af_speech": SPEECH_BY_RESULT.get(result),
        "s3_s4": {},
        "screen_on_after": None,
        "steps": [{"step": "wake", "status": "ok", "evidence": {}, "at": 1.0}],
        "total_ms": 12.5,
    }
    record.update(overrides)
    return record


class _FakeResponse:
    """`guarded_open` 的替身响应：够 `.read()`／`.headers.get()`／上下文管理器协议即可。"""

    def __init__(self, body, *, content_type: str = "application/json", session_id: str | None = None):
        self._body = body if isinstance(body, bytes) else str(body).encode("utf-8")
        self.headers = {"content-type": content_type}
        if session_id:
            self.headers["mcp-session-id"] = session_id

    def read(self, n: int = -1):  # noqa: ANN201 - 与 http.client 同形
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):  # noqa: ANN002, ANN003
        return False


class _FakeOpener:
    """假出站：按序吐预设响应，并把每个 Request 留下供断言。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, req, *, allowed_hosts, timeout=None):  # noqa: ANN001, ANN201
        self.requests.append((req, tuple(allowed_hosts), timeout))
        return self.responses.pop(0)


def _headers(req) -> dict[str, str]:
    """urllib 把 header 名 `capitalize()` 过 ⇒ 统一小写再比。"""
    return {str(k).lower(): str(v) for k, v in dict(getattr(req, "headers", {})).items()}


def _body_of(req) -> dict:
    return json.loads(bytes(req.data).decode("utf-8"))


def _envelope(msg_id: int, payload: dict) -> str:
    """MCP `tools/call` 的响应信封：结构化内容走 `structuredContent`。"""
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"structuredContent": payload, "content": []},
        }
    )


# ── L1：`classify_action("tv", …)` 显式落 L2 ───────────────────────────────


def test_tv_is_explicitly_registered_as_l2_domain():
    """卡面第 1 项：**显式**归类——`tv` 必须真的在 L2 域表里，⛔ 靠缺省档。

    这条是本项的真判据：`classify_action` 的返回值在"登记"与"落缺省"两种情况下**一模一样**
    （都是 L2），所以只断言返回值等于 L2 是**没牙的**——它证不了显式化。
    """
    assert "tv" in _L2_DOMAINS, "tv 必须在 L2 域表里显式登记（⛔ 靠『未知 domain ⇒ 保守 L2』）"
    assert "tv" in KNOWN_SERVICE_DOMAINS


@pytest.mark.parametrize("action", ["morning_news", "tv.morning_news", "Morning_News"])
def test_classify_action_tv_is_l2(action):
    assert classify_action("tv", action) == L2_RISKY
    assert classify_action("tv", action) == "L2"


def test_classification_result_is_unchanged_versus_unknown_domain_default():
    """行为结果**不变**（卡面原话）：显式登记前后都落 L2，与未知 domain 的缺省档同值。"""
    assert classify_action("tv", "morning_news") == classify_action("frobnicate", "do_it") == L2_RISKY


def test_tv_no_longer_misreported_as_unknown_domain():
    """显式登记的可见收益：`unrecognized_service` 不再把 `tv.morning_news` 报成"域不在表内"。"""
    assert unrecognized_service("tv.morning_news") is None
    # 负控：真正没登记的名字仍然要报（否则这条判据就是恒 None 的覆盖洞）。
    assert unrecognized_service("frobnicate.turn_on") is not None


# ── L2：导出与注册 ─────────────────────────────────────────────────────────


def test_tv_adapter_is_exported_from_package():
    from autoforge import af_adapters as pkg
    from autoforge.af_adapters import tv as tv_module

    assert ExportedTvAdapter is tv_module.TvAdapter
    assert "TvAdapter" in pkg.__all__


def test_build_runtime_registers_tv_adapter_like_ma():
    """照 `MaAdapter` 先例：`kind=tv` 进 `build_runtime` 这个唯一收口点。"""
    from autoforge.af_ir import Graph
    from autoforge.af_runtime import build_runtime

    runtime = build_runtime(Graph([]))
    assert "tv" in runtime.adapters
    adapter = runtime.adapters.get("tv")
    assert isinstance(adapter, TvAdapter)
    assert adapter.name == "tv"
    assert adapter.dry_run is True, "缺省仍是 dry_run（真机写入只在接线窗）"


def test_build_runtime_passes_endpoint_through():
    from autoforge.af_ir import Graph
    from autoforge.af_runtime import build_runtime

    runtime = build_runtime(
        Graph([]), tv_endpoint="http://tvpilot.invalid:8090", dry_run=False
    )
    adapter = runtime.adapters.get("tv")
    assert adapter.dry_run is False
    assert adapter._resolve_endpoint() == "http://tvpilot.invalid:8090/mcp"


# ── L3：端点注入／显式报错／零出网 ─────────────────────────────────────────


def test_missing_endpoint_is_an_explicit_refusal_not_a_guess(monkeypatch):
    """缺省端点空串 ⇒ 显式拒发（卡面第 3 项），⛔ 不猜地址、⛔ 不静默当 dry_run。"""
    monkeypatch.delenv(TV_ENDPOINT_ENV, raising=False)
    adapter = TvAdapter(dry_run=False)
    result = adapter.call("morning_news", {})
    assert result.success is False
    assert TV_ENDPOINT_ENV in (result.error or "")
    assert result.data["code"] == ADM_ERR_AUTH_REQUIRED
    assert adapter._http is None, "⛔ 没有端点就⛔ 建客户端（零出网）"


def test_endpoint_normalisation_matches_ma():
    assert TvAdapter(endpoint="http://host:8090")._resolve_endpoint() == "http://host:8090/mcp"
    assert TvAdapter(endpoint="http://host:8090/")._resolve_endpoint() == "http://host:8090/mcp"
    assert TvAdapter(endpoint="http://host:8090/mcp")._resolve_endpoint() == "http://host:8090/mcp"


def test_endpoint_falls_back_to_env(monkeypatch):
    monkeypatch.setenv(TV_ENDPOINT_ENV, "http://env.invalid:8090")
    assert TvAdapter()._resolve_endpoint() == "http://env.invalid:8090/mcp"


def test_injected_client_wins_over_endpoint():
    client = _StubClient(_terminal("iptv_ok"))
    adapter = TvAdapter(client, endpoint="http://unused.invalid:8090", dry_run=False)
    adapter.call("morning_news", {})
    assert adapter._http is None, "注入了 client 就⛔ 再建 HTTP 客户端"
    assert client.calls[0][0] == TOOL_MORNING_NEWS


def test_no_hardcoded_endpoint_or_credentials_in_source():
    """红线：源码里⛔ 出现联调窗那台机器的地址／任何 bearer 字面量。"""
    src = (SRC / "af_adapters" / "tv.py").read_text(encoding="utf-8")
    assert "192.168." not in src, "⛔ 不写死联调窗地址"
    assert not re.search(r"Bearer\s+[A-Za-z0-9_\-]{8,}", src), "⛔ 不内置任何令牌字面量"


# ── L4：MCP 传输（假出站，零网络）────────────────────────────────────────


def test_http_client_handshake_and_tool_call_offline(monkeypatch):
    """initialize → notifications/initialized（202 空体）→ tools/call 三跳形状。"""
    from autoforge.af_adapters import tv as tv_module

    opener = _FakeOpener(
        [
            _FakeResponse(json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}), session_id="sess-1"),
            _FakeResponse(b"", content_type="text/plain"),  # 通知的 202 空体
            _FakeResponse(
                "data: " + _envelope(2, _terminal("iptv_ok")) + "\n\n",
                content_type="text/event-stream",
            ),
        ]
    )
    monkeypatch.setattr(tv_module, "guarded_open", opener)

    adapter = TvAdapter(endpoint="http://tvpilot.invalid:8090", dry_run=False)
    result = adapter.call("morning_news", {"request_id": "req-1"})
    assert result.success is True
    assert set(result.data) == set(TERMINAL_KEYS) | {"action", "request_id"}

    assert len(opener.requests) == 3
    reqs = [entry[0] for entry in opener.requests]
    init_req, hosts, timeout = opener.requests[0]
    assert _body_of(init_req)["method"] == "initialize"
    assert hosts == ("tvpilot.invalid",)
    assert timeout == DEFAULT_TV_TRANSPORT_TIMEOUT
    assert "authorization" not in _headers(init_req), "LAN 无鉴权 ⇒ ⛔ 发 Authorization"

    notify = _body_of(reqs[1])
    assert notify["method"] == "notifications/initialized" and "id" not in notify

    call_req = reqs[2]
    body = _body_of(call_req)
    assert body["method"] == "tools/call"
    assert body["params"]["name"] == TOOL_MORNING_NEWS
    assert body["params"]["arguments"] == {"request_id": "req-1"}
    assert _headers(call_req)["mcp-session-id"] == "sess-1", "会话头必须回带给对端"


def test_http_client_decodes_plain_json_leg(stub_note=None):
    """`application/json` 腿（非 SSE）同样要能解——对端按 Accept 可能回两种。"""
    from autoforge.af_adapters import tv as tv_module

    payload = _terminal("adb_off_tv_on")
    client = TvMcpHttpClient("http://tvpilot.invalid:8090/mcp")
    raw = {
        "jsonrpc": "2.0",
        # AF-823：响应 id 必须回显 tools/call 的请求 id（init 占 1，tools/call 为 2）；
        # 旧实现 id 不匹配会回退 candidates[-1]，这条 id=1 的旧形状正是被回退蒙过的。
        "id": 2,
        "result": {"content": [{"type": "text", "text": json.dumps(payload)}]},
    }
    original = tv_module.guarded_open
    try:
        tv_module.guarded_open = _FakeOpener(
            [
                _FakeResponse(json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}})),
                _FakeResponse(b""),
                _FakeResponse(json.dumps(raw)),
            ]
        )
        got = client.call(TOOL_MORNING_NEWS, request_id="r")
    finally:
        tv_module.guarded_open = original
    assert got["result"] == "adb_off_tv_on"


def test_http_client_maps_upstream_error_to_named_code(monkeypatch):
    """`isError` ⇒ 具名失败（⛔ 当成业务回执吞掉）。"""
    from autoforge.af_adapters import tv as tv_module

    opener = _FakeOpener(
        [
            _FakeResponse(json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}})),
            _FakeResponse(b""),
            _FakeResponse(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "result": {"isError": True, "content": [{"type": "text", "text": "boom"}]},
                    }
                )
            ),
        ]
    )
    monkeypatch.setattr(tv_module, "guarded_open", opener)
    adapter = TvAdapter(endpoint="http://tvpilot.invalid:8090", dry_run=False)
    result = adapter.call("morning_news", {})
    assert result.success is False
    assert result.data["code"] == ADM_ERR_INTERNAL
    assert "boom" in (result.error or "")


# ── L5：话术六值逐字（v3 §2.2）────────────────────────────────────────────


def test_speech_table_matches_contract_verbatim():
    """逐字对契约 v3 §2.2 表 —— 这里是一份**人手抄的**字面量（⛔ 从被测常量派生）。"""
    assert SPEECH_BY_RESULT == {
        "iptv_ok": "新闻已打开",
        "adb_off_tv_on": "电视已打开；ADB 未开启，新闻请打开 tvdesk 手动观看",
        "wake_fail": "电视唤醒失败",
        "iptv_fail": "电视已打开，新闻没能自动打开，请手动观看",
        "faceid_timeout": None,
        "arcface_fail": None,
    }
    assert set(SPEECH_BY_RESULT) == set(RESULTS), "六值一个不多一个不少"
    assert TIMEOUT_SPEECH == "电视联动执行超时"


@pytest.mark.parametrize("result", ACTIVE_RESULTS)
def test_speech_of_active_values_is_verbatim(result):
    assert speech_of(result) == SPEECH_BY_RESULT[result]
    assert speech_of(result)


@pytest.mark.parametrize("result", DORMANT_RESULTS)
def test_speech_of_dormant_values_is_silent(result):
    assert speech_of(result) is None, "休眠两值＝静默（⛔ 改成空串骗过 speak 腿的 empty_text）"


def test_speech_of_unknown_and_none_are_silent():
    assert speech_of("nope") is None
    assert speech_of(None) is None


@pytest.mark.parametrize("result", ACTIVE_RESULTS)
def test_speaker_publishes_verbatim_speech_once(result):
    sent: list[dict] = []
    speaker = TvOutcomeSpeaker(lambda **kw: sent.append(kw) or {"published": True})
    adapter = TvAdapter(_StubClient(_terminal(result)), dry_run=False)
    call_result = adapter.call("morning_news", {"request_id": "t-1"})

    announced = speaker.consume(call_result, trace_id="trace-1", room="living_room")

    assert isinstance(announced, TvAnnouncement)
    assert announced.spoken is True
    assert announced.result == result
    assert announced.text == SPEECH_BY_RESULT[result], "话术必须逐字来自 v3 §2.2"
    assert sent == [
        {"trace_id": "trace-1", "text": SPEECH_BY_RESULT[result], "room": "living_room"}
    ], "一次终态⛔ 念两遍"
    assert speaker.counters["spoken"] == 1


def test_speaker_ignores_peer_af_speech_field():
    """话术真源是本表，⛔ 抄回执里的 `af_speech`（对端改一个字就改掉了家里的播报）。"""
    payload = _terminal("iptv_ok", af_speech="对端自己改的话术")
    adapter = TvAdapter(_StubClient(payload), dry_run=False)
    result = adapter.call("morning_news", {})
    assert result.data["af_speech"] == "对端自己改的话术", "回执原样留痕"

    sent: list[dict] = []
    speaker = TvOutcomeSpeaker(lambda **kw: sent.append(kw) or {"published": True})
    speaker.consume(result, trace_id="t")
    assert sent[0]["text"] == "新闻已打开"


# ── L6：休眠两值＝缺陷（⛔ 当业务失败收下、⛔ 出声）──────────────────────


@pytest.mark.parametrize("result", DORMANT_RESULTS)
def test_dormant_result_is_defect_in_adapter(result):
    adapter = TvAdapter(_StubClient(_terminal(result)), dry_run=False)
    call_result = adapter.call("morning_news", {"request_id": "d-1"})
    assert call_result.success is False
    assert call_result.data["code"] == ADM_ERR_INTERNAL
    assert call_result.data["defect"] == DEFECT_DORMANT_RESULT
    assert defect_of_result(result) == DEFECT_DORMANT_RESULT


@pytest.mark.parametrize("result", DORMANT_RESULTS)
def test_dormant_result_is_silent_and_counted_as_defect(result):
    sent: list[dict] = []
    speaker = TvOutcomeSpeaker(lambda **kw: sent.append(kw) or {"published": True})
    # 双形状：adapter 转出的 fail 形态，以及"对端越界且蒙过适配器"的成功形态。
    failed = TvAdapter(_StubClient(_terminal(result)), dry_run=False).call("morning_news", {})
    announced = speaker.consume(failed, trace_id="d")
    assert announced.spoken is False and announced.reason == "dormant_defect"
    assert announced.defect == DEFECT_DORMANT_RESULT
    assert speaker.counters["defect"] == 1

    from autoforge.af_adapters.base import CallResult

    sneaky = CallResult.ok({"result": result, "stage": "failed"})
    announced2 = speaker.consume(sneaky, trace_id="d2")
    assert announced2.spoken is False and announced2.reason == "dormant_defect"
    assert speaker.counters["defect"] == 2
    assert sent == [], "休眠两值⛔ 出声（一期零 faceid，出资人 2026-11-14 裁）"


# ── L7：幂等答复与失败态（⛔ 出声、⛔ 冒执行链）──────────────────────────


def test_already_running_reply_is_silent_not_failure():
    sent: list[dict] = []
    speaker = TvOutcomeSpeaker(lambda **kw: sent.append(kw) or {"published": True})
    adapter = TvAdapter(
        _StubClient({"accepted": False, "reason": "already_running", "request_id": "x", "last": {}}),
        dry_run=False,
    )
    result = adapter.call("morning_news", {"request_id": "x"})
    announced = speaker.consume(result, trace_id="a")
    assert result.success is True and result.data["accepted"] is False
    assert announced.spoken is False and announced.reason == "already_running"
    assert speaker.counters["silent"] == 1
    assert sent == []


def test_call_failure_is_silent_and_counted_never_raised():
    sent: list[dict] = []
    speaker = TvOutcomeSpeaker(lambda **kw: sent.append(kw) or {"published": True})
    adapter = TvAdapter(_StubClient(raises=TimeoutError("slow")), dry_run=False)
    result = adapter.call("morning_news", {})
    assert result.success is False
    assert result.data["code"] == "ADM_ERR_UPSTREAM_TIMEOUT" or result.data["code"]

    announced = speaker.consume(result, trace_id="f")  # ⛔ 不许抛
    assert announced.spoken is False and announced.reason == "call_failed"
    assert speaker.counters["failed"] == 1
    assert sent == []


def test_publish_leg_failure_does_not_escape():
    def boom(**kwargs):
        raise RuntimeError("broker on fire")

    speaker = TvOutcomeSpeaker(boom)
    adapter = TvAdapter(_StubClient(_terminal("iptv_ok")), dry_run=False)
    result = adapter.call("morning_news", {})
    announced = speaker.consume(result, trace_id="p")  # ⛔ 不冒执行链
    assert announced.spoken is False
    assert announced.reason.endswith("publish_raised")
    assert "RuntimeError" in (announced.error or "")
    assert speaker.counters["publish_failed"] == 1


def test_publish_leg_not_published_is_not_spoken():
    speaker = TvOutcomeSpeaker(lambda **kw: {"published": False, "code": "MQTT_DOWN"})
    adapter = TvAdapter(_StubClient(_terminal("wake_fail")), dry_run=False)
    announced = speaker.consume(adapter.call("morning_news", {}), trace_id="n")
    assert announced.spoken is False and announced.reason.endswith("not_published")
    assert speaker.counters["publish_failed"] == 1


def test_no_publish_leg_is_fail_closed():
    speaker = TvOutcomeSpeaker(None)
    adapter = TvAdapter(_StubClient(_terminal("iptv_ok")), dry_run=False)
    announced = speaker.consume(adapter.call("morning_news", {}), trace_id="z")
    assert announced.spoken is False, "没有播报腿⛔ 记成已播（那会骗过验收读数）"
    assert announced.reason.endswith("no_leg")


@pytest.mark.parametrize("junk", [None, "not a result", 42, object()])
def test_consume_never_raises_on_junk(junk):
    speaker = TvOutcomeSpeaker(lambda **kw: {"published": True})
    announced = speaker.consume(junk, trace_id="j")
    assert announced.spoken is False and announced.reason == "not_a_result"


# ── L8：240s＋60s 兜底 ────────────────────────────────────────────────────


def test_deadline_constants_follow_contract():
    assert RUN_BUDGET_S == 240.0, "对端全链预算"
    assert GRACE_S == 60.0, "宽限"
    assert TERMINAL_DEADLINE_S == 300.0


@pytest.mark.parametrize(
    ("elapsed", "expected"),
    [(None, False), (0.0, False), (299.9, False), (300.0, False), (300.1, True), (3600.0, True)],
)
def test_expired_boundary(elapsed, expected):
    """边界取值：`>` 而不是 `>=` —— 300.0 整还没过点，⛔ 自己造提前量。"""
    assert TvOutcomeSpeaker(lambda **kw: {}).expired(elapsed) is expected


def test_timeout_fallback_speaks_verbatim_once():
    sent: list[dict] = []
    speaker = TvOutcomeSpeaker(lambda **kw: sent.append(kw) or {"published": True})
    announced = speaker.consume(
        CallResult.ok({}), trace_id="late", room="living_room", elapsed_s=301.0
    )
    assert announced.spoken is True
    assert announced.text == TIMEOUT_SPEECH == "电视联动执行超时"
    assert announced.reason == "timeout"
    assert sent == [{"trace_id": "late", "text": "电视联动执行超时", "room": "living_room"}]
    assert speaker.counters["timeout"] == 1


def test_announce_timeout_is_directly_callable_and_counted():
    sent: list[dict] = []
    speaker = TvOutcomeSpeaker(lambda **kw: sent.append(kw) or {"published": True})
    announced = speaker.announce_timeout("t-9")
    assert announced.text == TIMEOUT_SPEECH
    assert speaker.counters["timeout"] == 1
    assert sent == [{"trace_id": "t-9", "text": TIMEOUT_SPEECH, "room": ""}]


# ── L9：不动别人的面（39 不漂＋kind 词表＋工具入参）─────────────────────────


def test_tool_surface_did_not_drift():
    """39 不漂：本批只接适配器，⛔ 新增 AF 对外工具。"""
    from autoforge.af_mcp import TOOLS

    assert len(TOOLS) == 39


def test_ir_kind_vocabulary_unchanged():
    """AF 的 kind 词表封闭 8 枚：tv 走 `do` + `adapter: tv`，⛔ 新增节点 kind。"""
    from autoforge.af_ir import NODE_KINDS

    assert NODE_KINDS == ("on", "if", "do", "ask", "wait", "set", "pass", "group")


def test_tv_action_table_and_params_unchanged():
    assert TV_ACTIONS == {"morning_news": "af_morning_news"}
    assert PARAMS_ALLOWED == frozenset({"request_id"})


def test_orchestrator_and_bridge_untouched():
    """卡面红线：⛔ 动编排器／桥（站点 pin 会连带漂移）。"""
    for name in ("af_orchestrator.py", "af_mqtt_bridge.py"):
        source = (SRC / name).read_text(encoding="utf-8")
        assert "TvAdapter" not in source, f"{name} 不该知道 tv 适配器（⛔ 本卡接线）"


def test_homesdk_pin_untouched():
    """⛔ 动 homesdk pin（0.3.5 是 AF×MA 线的事，TV 线不需要）。"""
    wheels = sorted(p.name for p in (REPO / "docker" / "homesdk").glob("homesdk-*.whl"))
    assert wheels == ["homesdk-0.3.3-py3-none-any.whl"], f"homesdk pin 漂了：{wheels}"
