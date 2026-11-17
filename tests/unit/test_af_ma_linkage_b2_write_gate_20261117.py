# -*- coding: utf-8 -*-
"""AF×MA 深度联动批二（写）判据（2026-11-17 · DCD 裁定 Q2＝甲：本版交付、读先写后）。

卡面（AF-PM 2026-11-17）：`ma.py` 扩第四枚门面客户端 `report_automation_outcome`（写）；
载荷逐字按契约 2.3；staging 语义（AF ⛔ 直写 live）；错误处理五态同批一（⛔ 不简化）；
**写向失败纪律**：自动化本体已完成 ⇒ 写失败⛔ 不判自动化失败（留痕＋计数、⛔ 重试）；
39 不漂再跑。全 mock、离线零网络、⛔ 不接线。

依赖：本分支接在批一 tip 上（错误分类／新鲜度／`CallResult` 面全部复用批一实现）。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from autoforge import af_mcp
from autoforge.af_adapters.base import AdapterError, CallResult
from autoforge.af_adapters.ma import (
    ADM_ERR_PAYLOAD_INVALID,
    ADM_ERR_UPSTREAM_TIMEOUT,
    MA_ACTIONS,
    MA_ERR_CONTRACT_DRIFT,
    MA_ERR_SCOPE_DENIED,
    OUTCOME_VALUES,
    USER_RESPONSE_CHOICES,
    USER_RESPONSE_VIA,
    WRITE_ACTIONS,
    MaAdapter,
    write_payload_defect,
)

_ENDPOINT = "http://ma.invalid:8086/mcp"
_NOW = datetime(2026, 11, 17, 12, 0, 0, tzinfo=timezone.utc)
_WRITE = "report_automation_outcome"


def _live(**kw) -> MaAdapter:
    kw.setdefault("clock", lambda: _NOW)
    return MaAdapter(endpoint=_ENDPOINT, token="mcp_testtoken", dry_run=False, **kw)


def _envelope(payload: dict | None = None, *, is_error: bool = False,
              text: str | None = None, structured: dict | None = None) -> dict:
    if is_error:
        return {"result": {
            "isError": True,
            "content": [{"type": "text", "text": text or ""}],
            **({"structuredContent": structured} if structured is not None else {}),
        }}
    return {"result": {"content": [{"type": "text", "text": json.dumps(payload or {})}],
                       "isError": False}}


def _stub(adapter: MaAdapter, payload: dict | None = None, **kw) -> list[dict]:
    bodies: list[dict] = []
    envelope = kw.pop("envelope", None)

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


def _payload(**over):
    """契约 2.3 的最小合法载荷（用户答了"接受"，走语音）。"""
    payload = {
        "rule_ref": "rule.study_ac_morning",
        "outcome": "executed",
        "at": "2026-11-17T21:05:00+08:00",
        "automation_decision": "turn_on_ac",
        "user_response": {
            "choice": "accepted",
            "value": "26",
            "at": "2026-11-17T21:04:30+08:00",
            "via": "voice",
        },
        "context_ref": "ctx:2026-11-17T21:00:00Z",
        "evidence": {"scene": "study_ac"},
        "note": "周末早晨第一条",
    }
    payload.update(over)
    return payload


# ── ① 动作面与载荷逐字段校验（契约 2.3）────────────────────────────────
def test_action_face_and_whitelist():
    assert _WRITE in MA_ACTIONS and WRITE_ACTIONS == frozenset({_WRITE})
    assert MA_ACTIONS[_WRITE] == (
        "rule_ref", "outcome", "at", "automation_decision",
        "user_response", "context_ref", "evidence", "note",
    )
    adapter = _live()
    _stub(adapter, {"memory_id": "m-1", "staging": True})
    assert adapter.call(_WRITE, _payload()).success is True
    bad = adapter.call(_WRITE, {**_payload(), "channel": 21})
    assert bad.success is False and bad.data["code"] == ADM_ERR_PAYLOAD_INVALID


@pytest.mark.parametrize("outcome", OUTCOME_VALUES)
def test_every_outcome_value_is_accepted_including_asked(outcome):
    """`asked`＝问了没答是**合法态**，⛔ 不许当缺字段/当失败。"""
    adapter = _live()
    _stub(adapter, {"memory_id": "m-1", "staging": True})
    result = adapter.call(_WRITE, _payload(outcome=outcome))
    assert result.success is True
    assert result.data["outcome"] == outcome


@pytest.mark.parametrize("choice", USER_RESPONSE_CHOICES)
def test_every_user_response_choice_is_accepted(choice):
    adapter = _live()
    _stub(adapter, {"memory_id": "m-1", "staging": True})
    result = adapter.call(_WRITE, _payload(user_response={"choice": choice}))
    assert result.success is True


@pytest.mark.parametrize("via", USER_RESPONSE_VIA)
def test_every_via_value_is_accepted(via):
    adapter = _live()
    _stub(adapter, {"memory_id": "m-1", "staging": True})
    result = adapter.call(_WRITE, _payload(user_response={"choice": "accepted", "via": via}))
    assert result.success is True


@pytest.mark.parametrize("broken,expect", [
    ({"outcome": "succeeded"}, "outcome"),
    ({"user_response": {"choice": "ok"}}, "choice"),
    ({"user_response": {"choice": "accepted", "via": "sms"}}, "via"),
    ({"user_response": {"choice": "accepted", "at": "  "}}, "at"),
    ({"user_response": "accepted"}, "user_response"),
    ({"evidence": ["a"]}, "evidence"),
])
def test_bad_values_are_refused_before_going_out(broken, expect):
    adapter = _live()
    bodies = _stub(adapter, {"memory_id": "m-1", "staging": True})
    result = adapter.call(_WRITE, _payload(**broken))
    assert result.success is False
    assert result.data["code"] == ADM_ERR_PAYLOAD_INVALID
    assert expect in result.data["write_payload_defect"]
    assert bodies == [], "载荷不合契约时⛔ 一个字节都不出网"


@pytest.mark.parametrize("missing", ["rule_ref", "outcome", "automation_decision", "user_response"])
def test_missing_core_fields_are_refused(missing):
    payload = _payload()
    payload.pop(missing)
    adapter = _live()
    _stub(adapter, {"memory_id": "m-1", "staging": True})
    result = adapter.call(_WRITE, payload)
    assert result.success is False and missing in result.data["write_payload_defect"]


def test_helper_reports_none_for_a_good_payload():
    assert write_payload_defect(_WRITE, _payload()) is None
    assert write_payload_defect("get_automation_context", {}) is None, "只读动作不走写向校验"


# ── ② staging 语义（AF ⛔ 直写 live）────────────────────────────────────
def test_staging_true_is_the_only_accepted_receipt(caplog):
    adapter = _live()
    _stub(adapter, {"memory_id": "m-9", "staging": True})
    with caplog.at_level("ERROR", logger="autoforge.af_adapters.ma"):
        result = adapter.call(_WRITE, _payload())
    assert result.success is True
    assert result.data["memory_id"] == "m-9" and result.data["staging"] is True
    assert adapter.staging_violation_count == 0
    assert not [r for r in caplog.records if "MA_STAGING_VIOLATION" in r.getMessage()]


@pytest.mark.parametrize("receipt", [
    {"memory_id": "m-9", "staging": False},
    {"memory_id": "m-9"},
])
def test_non_staging_receipt_is_a_contract_violation(caplog, receipt):
    adapter = _live()
    _stub(adapter, receipt)
    with caplog.at_level("ERROR", logger="autoforge.af_adapters.ma"):
        result = adapter.call(_WRITE, _payload())
    assert result.success is False, "AF ⛔ 直写 live：对端没落 staging 就不许当成功"
    assert result.data["defect"] == "staging_not_true"
    assert adapter.staging_violation_count == 1
    assert any("MA_STAGING_VIOLATION" in r.getMessage() for r in caplog.records)


# ── ③ 错误分类五态（同批一，⛔ 不简化）＋写失败纪律 ─────────────────────
def test_scope_denied_is_refused_without_retry_and_with_alert(caplog):
    adapter = _live()
    _stub(adapter, envelope=_envelope(is_error=True, text="DENIED: 令牌无 write 权限"))
    with caplog.at_level("ERROR", logger="autoforge.af_adapters.ma"):
        result = adapter.call(_WRITE, _payload())
    assert result.success is False
    assert result.data["code"] == MA_ERR_SCOPE_DENIED
    assert result.data["retryable"] is False, "⛔ 重试——重试解不了授权面"
    assert adapter.scope_denied_count == 1
    assert any("MA_WRITE_SCOPE_DENIED" in r.getMessage() for r in caplog.records)


def test_unregistered_tool_is_contract_drift(caplog):
    adapter = _live()
    _stub(adapter, envelope=_envelope(is_error=True, text="NOT_FOUND: 工具 'report_automation_outcome' 未登记"))
    with caplog.at_level("ERROR", logger="autoforge.af_adapters.ma"):
        result = adapter.call(_WRITE, _payload())
    assert result.data["code"] == MA_ERR_CONTRACT_DRIFT
    assert adapter.unregistered_tool_count == 1


def test_structured_business_error_keeps_the_adm_family():
    adapter = _live()
    _stub(adapter, {"ok": False, "error": {"code": "INTERNAL", "message": "staging 面写不进"}})
    result = adapter.call(_WRITE, _payload())
    assert result.success is False
    assert result.data["business_error"] is True and result.data["ma_error_code"] == "INTERNAL"


def test_timeout_maps_to_the_adm_code(monkeypatch):
    import socket

    from autoforge.af_adapters import ma as ma_mod

    adapter = _live()

    def boom(*_a, **_kw):
        raise socket.timeout("timed out")

    monkeypatch.setattr(ma_mod, "guarded_open", boom)
    result = adapter.call(_WRITE, _payload())
    assert result.success is False and result.data["code"] == ADM_ERR_UPSTREAM_TIMEOUT


@pytest.mark.parametrize("receipt_kw", [
    {"envelope": _envelope(is_error=True, text="DENIED: 令牌无 write 权限")},
    {"envelope": _envelope(is_error=True, text="NOT_FOUND: 工具 'x' 未登记")},
    {"payload": {"ok": False, "error": {"code": "INTERNAL", "message": "x"}}},
    {"payload": {"memory_id": "m", "staging": False}},
])
def test_write_failure_never_blames_the_automation(caplog, receipt_kw):
    """卡面红线：自动化本体已完成 ⇒ 写失败**⛔ 不判自动化失败**，只留痕＋计数、⛔ 不重试。"""
    adapter = _live()
    _stub(adapter, **receipt_kw)
    with caplog.at_level("WARNING", logger="autoforge.af_adapters.ma"):
        result = adapter.call(_WRITE, _payload())
    assert result.success is False
    assert result.data["automation_impact"] == "none", "本体不受影响必须显式标出"
    assert result.data["retryable"] is False, "⛔ 重试"
    assert adapter.write_failure_count == 1
    assert any("MA_OUTCOME_REPORT_FAILED" in r.getMessage() for r in caplog.records)


def test_read_failures_do_not_take_the_write_discipline():
    """⛔ 读向失败不走写向纪律：那条是"本次取数没成"，该由 IR 的 `on_error` 兜。"""
    adapter = _live()
    _stub(adapter, {"ok": False, "error": {"code": "INTERNAL", "message": "boom"}})
    result = adapter.call("get_automation_context", {})
    assert result.success is False
    assert "automation_impact" not in result.data
    assert adapter.write_failure_count == 0


# ── ④ dry_run 与 39 不漂 ────────────────────────────────────────────────
def test_dry_run_records_the_report_without_network():
    adapter = MaAdapter(dry_run=True)
    result = adapter.call(_WRITE, _payload())
    assert result.success is True and result.data["dry_run"] is True
    assert adapter.intents[-1]["action"] == _WRITE
    assert adapter.intents[-1]["arguments"]["outcome"] == "executed"


def test_af_outbound_tool_face_is_still_39():
    """契约 §四 第 7 行：批二同样⛔ 不许漂 AF 对外工具面。"""
    assert len(af_mcp.TOOLS) == 39
    assert _WRITE not in {row[0] for row in af_mcp.TOOLS}, "写向客户端是出向调用，⛔ 不进 AF 对外工具面"
    assert len(MA_ACTIONS) == 8
