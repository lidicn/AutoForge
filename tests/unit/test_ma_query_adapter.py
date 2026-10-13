"""`ma` 取数适配器判据腿（路线图 G-AF-01／计划 §七 卡2，链主 R3 定的静默空值判据）。

两族都要：
- **差异用例**：静默空值两种形状必须抛；通道四类故障必须带不同码。
- **一致用例（防拦太宽）**：`templates: []` 是"确实没有模板"的真实答案，⛔ 不许被形状塌判据误杀。
- **图级**：`ma_query` 在 IR 里落形为 `do`+`adapter: ma`，走编译（L0 ⇒ 不强制 canary）与仿真
  （`result_var` 真绑上 `{"success","data","error"}`），锁住"适配器单测绿、四张脸却接不到"这一族。
"""

from __future__ import annotations

import json
import socket
from urllib.error import HTTPError, URLError

import pytest

from autoforge.af_adapters import classify_action
from autoforge.af_adapters.base import AdapterError, CallResult
from autoforge.af_adapters.ma import (
    ADM_ERR_AUTH_REQUIRED,
    ADM_ERR_PAYLOAD_INVALID,
    ADM_ERR_PEER_OFFLINE,
    ADM_ERR_UPSTREAM_TIMEOUT,
    MAX_RESPONSE_BYTES,
    MaAdapter,
    action_of,
    select_entity,
)
from autoforge.af_bus import BusEvent
from autoforge.af_ir import Graph, load_automation
from autoforge.af_runtime import build_runtime
from autoforge.af_scanner import StaticScanner

_ENDPOINT = "http://ma.invalid:8086/mcp"


def _live(**overrides) -> MaAdapter:
    return MaAdapter(endpoint=_ENDPOINT, token="mcp_testtoken", dry_run=False, **overrides)


def _envelope(payload: dict) -> dict:
    """按 MA 的 streamable-http 形状包一层：`result.content[0].text` 放业务 JSON。"""
    return {"result": {"content": [{"type": "text", "text": json.dumps(payload)}], "isError": False}}


def _stub(adapter: MaAdapter, payload: dict | None = None, *, envelope: dict | None = None) -> list[dict]:
    """把 `_post` 换成"按 JSON-RPC method 回答"的桩，记下每次出网的 body。"""
    bodies: list[dict] = []

    def fake_post(endpoint, host, token, body):
        bodies.append(dict(body))
        if body.get("method") == "initialize":
            return {"result": {"protocolVersion": "2024-11-05", "capabilities": {}}}
        if body.get("method") == "notifications/initialized":
            return {}
        return envelope if envelope is not None else _envelope(payload or {})

    adapter._post = fake_post  # type: ignore[method-assign]
    adapter._session_id = "sess-1"
    return bodies


# ── 静默空值判据（链主 R3 定、DCD 追认：⛔ 不许放宽成放行）────────────────
def test_export_insight_missing_insight_block_raises_not_silent_empty():
    adapter = _live()
    _stub(adapter, {"ok": True})
    with pytest.raises(AdapterError) as exc:
        adapter.call("export_insight", {"template_id": "ac_runtime_daily"})
    assert ADM_ERR_PAYLOAD_INVALID in str(exc.value)


def test_export_insight_all_three_blocks_empty_raises_not_silent_empty():
    adapter = _live()
    _stub(adapter, {"ok": True, "insight": {"insight": {}, "queries": {}, "implementation": {}}})
    with pytest.raises(AdapterError) as exc:
        adapter.call("export_insight", {"template_id": "ac_runtime_daily"})
    assert ADM_ERR_PAYLOAD_INVALID in str(exc.value)


def test_export_insight_carries_the_three_blocks_under_insight_key():
    """定档甲：三块真身在 `parsed["insight"]` 之下，AF 按既有形状读、MA 零代码。"""
    adapter = _live()
    _stub(
        adapter,
        {
            "ok": True,
            "insight": {
                "insight": {"name": "卧室空调昨晚开机时长", "confidence": 0.82},
                "queries": [{"entity_id": "climate.bedroom"}],
                "implementation": {"nr_flow": "…"},
            },
        },
    )
    result = adapter.call("export_insight", {"template_id": "ac_runtime_daily"})
    assert result.success is True
    assert result.data["insight"]["insight"]["confidence"] == 0.82
    assert result.data["insight"]["queries"] == [{"entity_id": "climate.bedroom"}]


def test_run_template_without_body_raises_but_full_shape_passes():
    missing = _live()
    _stub(missing, {"ok": True})
    with pytest.raises(AdapterError):
        missing.call("run_analysis_template", {"template_id": "ac_runtime_daily"})

    full = _live()
    _stub(
        full,
        {
            "ok": True,
            "template": {"id": "ac_runtime_daily", "name": "空调时长"},
            "window": {"start": "2026-10-12", "end": "2026-10-13"},
            "entities": [{"entity_id": "climate.bedroom", "total_seconds": 7200}],
            "summary_text": "卧室空调昨晚开机 2 小时",
        },
    )
    result = full.call("run_analysis_template", {"template_id": "ac_runtime_daily"})
    assert result.success is True
    assert result.data["summary_text"] == "卧室空调昨晚开机 2 小时"


def test_ok_false_is_a_business_answer_not_a_shape_collapse():
    """MA 明确回答"没成"＝可兜底的业务结果 ⇒ 走 `CallResult.fail`（IR 的 `on_error`），不抛。"""
    adapter = _live()
    _stub(adapter, {"ok": False, "error": "模板不存在: nope"})
    result = adapter.call("export_insight", {"template_id": "nope"})
    assert isinstance(result, CallResult)
    assert result.success is False


def test_empty_template_list_is_a_true_answer_not_blocked():
    """一致用例：`templates: []` 是"确实没有模板"，形状塌判据不该把它拦成红。"""
    adapter = _live()
    _stub(adapter, {"ok": True, "total": 0, "templates": []})
    result = adapter.call("list_analysis_templates", {})
    assert result.success is True
    assert result.data["templates"] == []


# ── 出参绑定：entities[] 只能在适配器内选一次 ────────────────────────────
def test_entity_id_lifts_only_scalars_into_a_mapping_for_dsl_binding():
    adapter = _live()
    _stub(
        adapter,
        {
            "ok": True,
            "template": {"id": "t"},
            "window": {"start": "a"},
            "entities": [
                {"entity_id": "climate.other", "total_seconds": 1, "timeline": [1, 2]},
                {"entity_id": "climate.bedroom", "total_seconds": 7200, "timeline": [1, 2], "meta": {"x": 1}},
            ],
            "summary_text": "s",
        },
    )
    result = adapter.call("run_analysis_template", {"template_id": "t", "entity_id": "climate.bedroom"})
    assert result.data["entity"] == {"entity_id": "climate.bedroom", "total_seconds": 7200}
    assert "timeline" not in result.data["entity"]
    assert result.data["payload"]["entities"][1]["timeline"] == [1, 2]  # 原载荷不被裁剪


def test_entity_id_that_is_not_present_fails_closed():
    adapter = _live()
    _stub(
        adapter,
        {
            "ok": True,
            "template": {"id": "t"},
            "window": {"start": "a"},
            "entities": [{"entity_id": "climate.bedroom", "total_seconds": 1}],
            "summary_text": "s",
        },
    )
    result = adapter.call("run_analysis_template", {"template_id": "t", "entity_id": "climate.living"})
    assert result.success is False
    assert result.data["code"] == ADM_ERR_PAYLOAD_INVALID


def test_select_entity_matches_logical_id_and_ignores_blank():
    rows = {"entities": [{"logical_id": "卧室空调", "v": 3}]}
    assert select_entity(rows, "卧室空调") == {"logical_id": "卧室空调", "v": 3}
    assert select_entity(rows, "") is None
    assert select_entity({"entities": ["不是对象"]}, "x") is None


# ── 入参面：名单外不透传、必填缺失不猜、动作名两种写法 ──────────────────
def test_policy_param_is_refused_instead_of_reaching_the_transport():
    adapter = _live()
    bodies = _stub(adapter, {"ok": True, "insight": {"insight": {"a": 1}}})
    result = adapter.call("export_insight", {"template_id": "t", "timeout": 5})
    assert result.success is False
    assert result.data["code"] == ADM_ERR_PAYLOAD_INVALID
    assert len(bodies) == 0  # 一条都不出网


def test_missing_required_param_refuses():
    adapter = _live()
    bodies = _stub(adapter, {"ok": True, "insight": {"insight": {"a": 1}}})
    assert adapter.call("export_insight", {}).success is False
    assert adapter.call("route_question", {"question": "  "}).success is False
    assert len(bodies) == 0


def test_unknown_action_raises_adapter_error():
    with pytest.raises(AdapterError):
        _live().call("delete_analysis_template", {"template_id": "t"})


def test_qualified_and_bare_action_names_are_the_same_action():
    assert action_of("ma.route_question") == "route_question"
    assert action_of("route_question") == "route_question"


def test_ma_actions_classify_as_l0_readonly():
    """四条 action 全只读：留在未知档会被保守判 L2 ⇒ 每条取数都要配 canary，连编译都过不去。"""
    for name in ("route_question", "list_analysis_templates", "export_insight", "run_analysis_template"):
        assert classify_action("ma", name) == "L0"
    assert classify_action("ma", f"ma.{name}") == "L0"


# ── dry_run 缺省档：一个字节都不出网、不伪造读数 ─────────────────────────
def test_dry_run_records_intent_and_invents_no_payload_values():
    adapter = MaAdapter(endpoint=_ENDPOINT, token="mcp_testtoken")
    result = adapter.call("route_question", {"question": "昨晚卧室空调开机时长", "days": 7})
    assert result.success is True
    assert result.data == {
        "dry_run": True,
        "action": "route_question",
        "arguments": {"question": "昨晚卧室空调开机时长", "days": 7},
    }
    assert adapter.intents == [{"action": "route_question", "arguments": {"question": "昨晚卧室空调开机时长", "days": 7}}]
    assert "summary_text" not in str(adapter.intents)  # ⛔ 不伪造读数


def test_intent_ring_is_bounded():
    adapter = MaAdapter(endpoint=_ENDPOINT, token="t")
    for i in range(260):
        adapter.call("list_analysis_templates", {"category": f"c{i}"})
    assert len(adapter.intents) == 200


def test_missing_credentials_refuse_to_send():
    """缺凭据/缺端点 ⇒ fail-closed + `ADM_ERR_AUTH_REQUIRED`（与卡1 的桥面同一口径）。"""
    no_token = MaAdapter(endpoint=_ENDPOINT, dry_run=False)
    no_token._token = ""
    result = no_token.call("route_question", {"question": "x"})
    assert result.success is False
    assert result.data["code"] == ADM_ERR_AUTH_REQUIRED

    no_endpoint = MaAdapter(token="mcp_t", dry_run=False)
    result2 = no_endpoint.call("route_question", {"question": "x"})
    assert result2.success is False
    assert result2.data["code"] == ADM_ERR_AUTH_REQUIRED


# ── 传输层故障：四类下场各带自己的码（⛔ 不共一枚码）───────────────────────
@pytest.mark.parametrize(
    "exc, expected_code",
    [
        (socket.timeout("timed out"), ADM_ERR_UPSTREAM_TIMEOUT),
        (URLError(reason=TimeoutError("slow")), ADM_ERR_UPSTREAM_TIMEOUT),
        (URLError(reason=ConnectionRefusedError("refused")), ADM_ERR_PEER_OFFLINE),
        (HTTPError(_ENDPOINT, 401, "Unauthorized", {}, None), ADM_ERR_AUTH_REQUIRED),
        (HTTPError(_ENDPOINT, 408, "Timeout", {}, None), ADM_ERR_UPSTREAM_TIMEOUT),
        (HTTPError(_ENDPOINT, 503, "Unavailable", {}, None), ADM_ERR_PEER_OFFLINE),
    ],
)
def test_transport_failures_carry_their_own_code(monkeypatch, exc, expected_code):
    """故障映射测在 `_post` 那一层：桩替掉 `guarded_open`（真正会抛的是它），⛔ 不替掉 `_post` 本身。"""
    import autoforge.af_adapters.ma as ma_mod
    from autoforge.af_adapters.ma import _UpstreamFailure

    adapter = _live()
    adapter._session_id = "sess-1"
    monkeypatch.setattr(ma_mod, "guarded_open", _raise(exc))
    with pytest.raises(_UpstreamFailure) as raised:
        adapter._post(_ENDPOINT, "ma.invalid", "t", {"jsonrpc": "2.0", "id": 1, "method": "x"})
    assert raised.value.code == expected_code

    # 同一场故障走完整条调用链 ⇒ 结果是带码的 fail（由 IR 的 `on_error` 兜，不炸执行器）
    adapter2 = _live()
    adapter2._session_id = "sess-1"
    monkeypatch.setattr(ma_mod, "guarded_open", _raise(exc))
    result = adapter2.call("route_question", {"question": "x"})
    assert result.success is False
    assert result.data["code"] == expected_code


def _raise(exc: Exception):
    def _boom(req, *, allowed_hosts, timeout):
        raise exc

    return _boom


def test_outbound_host_not_in_allowlist_refuses_before_sending(monkeypatch):
    """白名单由 `guarded_open` 把：这里断言"拒发"落成一条带码的 fail，而不是把凭据发出去。"""
    import autoforge.af_adapters.ma as ma_mod

    adapter = MaAdapter(endpoint=_ENDPOINT, token="mcp_t", dry_run=False, allowed_hosts=("other.invalid",))
    monkeypatch.setattr(ma_mod, "guarded_open", _raise(URLError("不在白名单内")))
    result = adapter.call("route_question", {"question": "x"})
    assert result.success is False
    assert result.data["code"] == ADM_ERR_PEER_OFFLINE


def test_response_over_the_byte_cap_is_refused_not_swallowed(monkeypatch):
    import autoforge.af_adapters.ma as ma_mod
    from autoforge.af_adapters.ma import _UpstreamFailure

    adapter = _live()
    adapter._session_id = "sess-1"

    class _Resp:
        headers = {"content-type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, _n):
            return b"x" * (MAX_RESPONSE_BYTES + 1)

    monkeypatch.setattr(ma_mod, "guarded_open", lambda req, **kw: _Resp())
    with pytest.raises(_UpstreamFailure) as exc:
        adapter._post(_ENDPOINT, "ma.invalid", "t", {"jsonrpc": "2.0", "id": 1, "method": "x"})
    assert exc.value.code == ADM_ERR_PAYLOAD_INVALID


def test_sse_framed_response_decodes_to_the_matching_id():
    from autoforge.af_adapters.ma import _decode_envelope

    raw = (
        b"event: message\r\ndata: {\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{\"ok\":false}}\r\n\r\n"
        b"event: message\r\ndata: {\"jsonrpc\":\"2.0\",\"id\":7,\"result\":{\"ok\":true}}\r\n\r\n"
    )
    envelope = _decode_envelope(raw, "text/event-stream", 7)
    assert envelope["id"] == 7


def test_structured_content_is_preferred_over_text_leg():
    adapter = _live()
    _stub(
        adapter,
        envelope={
            "result": {
                "structuredContent": {"ok": True, "insight": {"insight": {"confidence": 0.7}}},
                "content": [{"type": "text", "text": "not json at all"}],
                "isError": False,
            }
        },
    )
    result = adapter.call("export_insight", {"template_id": "t"})
    assert result.success is True
    assert result.data["insight"]["insight"]["confidence"] == 0.7


def test_jsonrpc_error_object_is_reported_not_treated_as_empty_answer():
    adapter = _live()
    _stub(adapter, envelope={"error": {"code": -32601, "message": "Method not found"}, "id": 3})
    result = adapter.call("route_question", {"question": "x"})
    assert result.success is False
    assert "Method not found" in (result.error or "")


def test_tools_call_without_result_is_payload_invalid():
    adapter = _live()
    _stub(adapter, envelope={"result": {"content": [], "isError": False}})
    result = adapter.call("export_insight", {"template_id": "t"})
    assert result.success is False
    assert result.data["code"] == ADM_ERR_PAYLOAD_INVALID


# ── 图级：编译面 + 仿真绑定（"适配器单测绿但四张脸接不到 MA"这一族的锁）────
_MA_IR = {
    "ir_version": "0.2.1",
    "id": "ac_ma_pull",
    "name": "向 MA 取一次路由答案",
    "version": 1,
    "mode": "single",
    "nodes": [
        {
            "id": "a1",
            "kind": "on",
            "trigger": {"type": "state", "entity_id": "binary_sensor.hall_motion", "to": "on"},
        },
        {
            "id": "d1",
            "kind": "do",
            "adapter": "ma",
            "action": "route_question",
            "params": {"question": "昨晚卧室空调开机时长"},
            "result_var": "ma_answer",
        },
        {"id": "p1", "kind": "pass"},
    ],
    "edges": [
        {"from": "a1", "to": "d1", "kind": "then"},
        {"from": "d1", "to": "p1", "kind": "then"},
    ],
}


def test_ma_query_node_compiles_without_canary_or_confirm_diagnostic():
    """落 L2 保守档 ⇒ 取数节点被要求确认/canary（现读诊断码 `L2_NEEDS_CONFIRM`），G-AF-01 永远点不亮。

    反例自证在 `E:\\tmp\\ma_negcontrol.py`：把 `ma` 从 `_L0_DOMAINS` 摘掉，这条腿与
    `test_ma_actions_classify_as_l0_readonly` 一起变红；种回去即绿。
    """
    scan = StaticScanner(Graph([load_automation(_MA_IR)])).scan()
    codes = {d.code for d in scan.diagnostics}

    assert "L2_NEEDS_CANARY" not in codes, scan.diagnostics
    assert "L2_NEEDS_CONFIRM" not in codes, scan.diagnostics
    assert scan.ok is True, scan.diagnostics


def test_ma_query_is_registered_on_the_shared_runtime_builder():
    """`build_runtime` 是四面（CLI / HTTP / MCP / 仿真）唯一的适配器收口点，缺省档必须是 dry_run。"""
    runtime = build_runtime(Graph([load_automation(_MA_IR)]))
    adapter = runtime.adapters.get("ma")

    assert "ma" in runtime.adapters
    assert adapter.dry_run is True


def test_simulation_binds_result_var_to_call_result_shape():
    """仿真跑通：`vars.ma_answer` 是 `{success,data,error}` 三键，且 dry_run 只留意图、不伪造读数。"""
    runtime = build_runtime(Graph([load_automation(_MA_IR)]))
    instances = runtime.publish(BusEvent.of("binary_sensor.hall_motion", "on"))

    assert [i.state for i in instances] == ["done"], instances
    bound = instances[0].ctx.vars["ma_answer"]
    assert set(bound) == {"success", "data", "error"}
    assert bound["success"] is True
    assert bound["data"]["dry_run"] is True
    assert bound["data"]["arguments"] == {"question": "昨晚卧室空调开机时长"}
    assert "summary_text" not in json.dumps(bound, ensure_ascii=False)  # ⛔ 不伪造读数
    assert runtime.adapters.get("ma").intents == [
        {"action": "route_question", "arguments": {"question": "昨晚卧室空调开机时长"}}
    ]
