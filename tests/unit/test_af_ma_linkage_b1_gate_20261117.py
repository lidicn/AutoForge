# -*- coding: utf-8 -*-
"""AF×MA 深度联动批一（读）判据（2026-11-17 · DCD 裁定 A＋Q1–Q5＋H1 全甲）。

卡面（AF-PM 2026-11-17）：桥加两条事件族订阅＋ingest 分支；`ma.py` 扩三枚只读门面客户端，
新鲜度闸单点强制、数据不足与陈旧分开、错误分类五态双形状；AF 对外工具面 **39 不漂**。
全 mock、离线零网络、不连 MA 进程／broker。

依据：契约 `MA-AF联动契约-v1草稿-20261117.md` §2.1（三族错误语义）／§2.4（min_days=3、
`insufficient_data`）／§2.5（新鲜度）／§三（事件族＋公共信封）；预案 §三（五态双形状、⛔ 混判）。

分组：
- **A 桥**：主题／kind／计数三张表；两条事件族落盘与信封闸；现网两条回归不动。
- **B 门面客户端**：三枚动作与参数面；新鲜度闸（含"对端更严就听、更宽不放宽"、覆盖只许收紧、
  `as_of` 读不出即陈旧）；数据不足三形状（`insufficient_data` ≠ `data:[]`＋`sufficient:true`）。
- **C 错误分类五态双形状**：结构化优先于文本前缀；业务 `DENIED` 与分发 `DENIED:` 分流；
  两条分发级各自计数与具名留痕。
- **D 不漂断言**：AF 对外 MCP 工具面 39。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from autoforge import af_mcp
from autoforge.af_adapters.base import AdapterError, CallResult
from autoforge.af_adapters.ma import (
    ADM_ERR_AUTH_REQUIRED,
    ADM_ERR_INTERNAL,
    ADM_ERR_PAYLOAD_INVALID,
    DATA_INSUFFICIENT,
    DEFAULT_MA_TRANSPORT_TIMEOUT,
    FRESHNESS_BY_READ,
    FRESHNESS_HABITS_S,
    FRESHNESS_PREDICTION_S,
    FRESHNESS_REALTIME_S,
    MA_ACTIONS,
    MA_BUSINESS_ERROR_TO_ADM,
    MA_ERR_CONTRACT_DRIFT,
    MA_ERR_SCOPE_DENIED,
    MaAdapter,
    assess_freshness,
    dispatch_failure,
    insufficient_reason,
    parse_as_of,
    structured_error_code,
)
from autoforge.af_linkage_feed import (
    ENVELOPE_KEYS,
    KIND_BEHAVIOR_STATE,
    KIND_DEVICE_HEALTH,
    KIND_PRESENCE,
    KIND_PREDICTION,
    TRIGGER_NAME,
    LinkageRecord,
    behavior_data_of,
    prediction_data_of,
)
from autoforge.af_mqtt_bridge import (
    BEHAVIOR_STATE_TOPIC,
    DEVICE_HEALTH_TOPIC,
    IN_COUNT_BY_KIND,
    INSIGHTS_TOPIC,
    LINKAGE_KIND_BY_TOPIC,
    LINKAGE_TOPICS,
    PAYLOAD_VERSION_MAJOR,
    PREDICTION_TOPIC,
    PRESENCE_TOPIC,
    make_linkage_feed,
)

_ENDPOINT = "http://ma.invalid:8086/mcp"
_NOW = datetime(2026, 11, 17, 12, 0, 0, tzinfo=timezone.utc)


# ══════════════════════════════════════════════════════════════════════════
# A. 桥：三张表 + 两条事件族
# ══════════════════════════════════════════════════════════════════════════
def test_a_topic_tables_are_the_only_entry_and_keep_the_existing_three():
    assert LINKAGE_TOPICS == (
        PRESENCE_TOPIC,
        DEVICE_HEALTH_TOPIC,
        BEHAVIOR_STATE_TOPIC,
        PREDICTION_TOPIC,
    ), "两条新主题按裁定逐字追加，⛔ 不动现网三条的顺序与名字"
    assert INSIGHTS_TOPIC not in LINKAGE_TOPICS, "洞察线走自己的订阅口，⛔ 不混进联动表"
    assert LINKAGE_KIND_BY_TOPIC[BEHAVIOR_STATE_TOPIC] == KIND_BEHAVIOR_STATE
    assert LINKAGE_KIND_BY_TOPIC[PREDICTION_TOPIC] == KIND_PREDICTION
    assert set(IN_COUNT_BY_KIND) == {
        KIND_PRESENCE, KIND_DEVICE_HEALTH, KIND_BEHAVIOR_STATE, KIND_PREDICTION,
    }
    assert TRIGGER_NAME[KIND_BEHAVIOR_STATE] == "ma_behavior_state"
    assert TRIGGER_NAME[KIND_PREDICTION] == "ma_prediction"
    assert PAYLOAD_VERSION_MAJOR == "1"


def _bridge(tmp_path):
    from autoforge.af_mqtt_bridge import AfMqttBridge

    client = _FakeClient()
    feed = make_linkage_feed(store_root=tmp_path / "store", clock=lambda: 1_700_000_000.0)
    bridge = AfMqttBridge(client, linkage_sink=feed, clock=lambda: 1_700_000_000.0)
    return bridge, client, feed


class _FakeClient:
    def __init__(self) -> None:
        self.subscribed: list[str] = []
        self.published: list[tuple[str, str]] = []

    def subscribe(self, topic: str, qos: int = 0) -> None:
        self.subscribed.append(topic)

    def publish(self, topic: str, payload: str, qos: int = 0, retain: bool = False) -> None:
        self.published.append((topic, payload))


def _behavior_payload(**over):
    payload = {
        "type": "behavior_state",
        "source": "ma",
        "payload_version": "1.0",
        "event_id": "ma-evt-1",
        "trace_id": "trace-ma-1",
        "ts": "2026-11-17T20:03:00+08:00",
        "emitted_at": "2026-11-17T12:03:00Z",
        "as_of": "2026-11-17T12:02:58Z",
        "state": "watching_tv",
        "confidence": 0.91,
        "member_id": "m1",
        "room": "living_room",
        "duration": 3600.0,
    }
    payload.update(over)
    return payload


def _prediction_payload(**over):
    payload = {
        "type": "prediction",
        "source": "ma",
        "payload_version": "1.0",
        "event_id": "ma-evt-2",
        "trace_id": "trace-ma-2",
        "ts": "2026-11-17T20:10:00+08:00",
        "emitted_at": "2026-11-17T12:10:00Z",
        "as_of": "2026-11-17T12:09:30Z",
        "next_arrival": "2026-11-17T21:10:00+08:00",
        "upcoming_device_hints": ["climate.study_ac"],
        "room": "study",
    }
    payload.update(over)
    return payload


def test_a_behavior_state_lands_with_envelope_and_counts(tmp_path):
    bridge, client, feed = _bridge(tmp_path)
    bridge.start()
    out = bridge.ingest_linkage(BEHAVIOR_STATE_TOPIC, _behavior_payload())

    assert out["handled"] is True and out["kind"] == KIND_BEHAVIOR_STATE
    assert out["event_id"] == "ma-evt-1", "契约信封的 event_id 就是落盘身份（跨仓对齐同一次事件）"
    assert out["subject"] == "m1"
    assert bridge.counts["behavior_state_in"] == 1
    assert bridge.counts["prediction_in"] == 0
    records = feed.poll_new(max_age_s=10 ** 9)
    assert len(records) == 1 and records[0].kind == KIND_BEHAVIOR_STATE
    data = records[0].data
    assert data["state"] == "watching_tv" and data["duration"] == 3600.0
    assert data["as_of"] == "2026-11-17T12:02:58Z"
    for key in ("payload_version", "event_id", "ts", "emitted_at"):
        assert key in data, f"公共信封 {key} 必须入库（契约 §三）"


def test_a_prediction_lands_and_keeps_hints_list(tmp_path):
    bridge, _client, feed = _bridge(tmp_path)
    bridge.start()
    out = bridge.ingest_linkage(PREDICTION_TOPIC, _prediction_payload())

    assert out["kind"] == KIND_PREDICTION and out["subject"] == "study"
    assert bridge.counts["prediction_in"] == 1
    data = feed.poll_new(max_age_s=10 ** 9)[0].data
    assert data["next_arrival"].startswith("2026-11-17T21:10")
    assert data["upcoming_device_hints"] == ["climate.study_ac"]


@pytest.mark.parametrize("version", ["1.0", "1.7", ""])
def test_a_payload_version_minor_or_absent_is_accepted(tmp_path, version):
    bridge, _c, _f = _bridge(tmp_path)
    bridge.start()
    payload = _behavior_payload()
    if version:
        payload["payload_version"] = version
    else:
        payload.pop("payload_version")
    assert bridge.ingest_linkage(BEHAVIOR_STATE_TOPIC, payload)["handled"] is True


@pytest.mark.parametrize("topic,payload", [
    (BEHAVIOR_STATE_TOPIC, _behavior_payload(payload_version="2.0")),
    (PREDICTION_TOPIC, _prediction_payload(payload_version="2.0")),
])
def test_a_unsupported_major_version_is_rejected_not_guessed(tmp_path, topic, payload):
    bridge, _c, _f = _bridge(tmp_path)
    bridge.start()
    out = bridge.ingest_linkage(topic, payload)
    assert out["handled"] is False
    assert out["reason"] == "payload_version_unsupported"
    assert bridge.counts["linkage_rejected"] == 1


def test_a_missing_trace_id_is_rejected_on_the_new_topics_too(tmp_path):
    bridge, _c, _f = _bridge(tmp_path)
    bridge.start()
    payload = _behavior_payload()
    payload.pop("trace_id")
    out = bridge.ingest_linkage(BEHAVIOR_STATE_TOPIC, payload)
    assert out["handled"] is False and out["reason"] == "missing_trace_id"


def test_a_event_id_falls_back_to_generated_when_absent(tmp_path):
    bridge, _c, _f = _bridge(tmp_path)
    bridge.start()
    payload = _behavior_payload()
    payload.pop("event_id")
    out = bridge.ingest_linkage(BEHAVIOR_STATE_TOPIC, payload)
    assert out["handled"] is True and len(out["event_id"]) == 12


def test_a_existing_two_topics_are_unchanged(tmp_path):
    """⛔ 本批不动现网两条：在场仍要 members 数组、设备健康仍要稳定 id。"""
    bridge, _c, _f = _bridge(tmp_path)
    bridge.start()
    assert bridge.ingest_linkage(PRESENCE_TOPIC, {"trace_id": "t", "members": []})["handled"] is True
    bad = bridge.ingest_linkage(PRESENCE_TOPIC, {"trace_id": "t", "members": "not-a-list"})
    assert bad["handled"] is False and bad["reason"] == "members_not_list"
    assert bridge.ingest_linkage(
        DEVICE_HEALTH_TOPIC, {"trace_id": "t", "stable_id": "", "device_id": "d"}
    )["reason"] == "missing_stable_id"
    assert bridge.counts["presence_in"] == 1 and bridge.counts["device_health_in"] == 0


def test_a_trigger_projection_brings_duration_to_the_dsl():
    record = LinkageRecord(
        event_id="e", kind=KIND_BEHAVIOR_STATE, topic=BEHAVIOR_STATE_TOPIC,
        trace_id="t", received_at=1.0, subject="m1",
        data=behavior_data_of(_behavior_payload()),
    )
    assert record.trigger_name == "ma_behavior_state"
    trigger = record.as_trigger_data()
    assert trigger["kind"] == KIND_BEHAVIOR_STATE and trigger["duration"] == 3600.0
    assert trigger["subject"] == "m1"


def test_a_trim_keeps_only_contract_keys():
    data = behavior_data_of({**_behavior_payload(), "secret": "nope", "face_feature": [1, 2]})
    assert "secret" not in data and "face_feature" not in data, "白名单外的键一律不带"
    assert set(data) <= set(ENVELOPE_KEYS) | {
        "as_of", "state", "confidence", "since", "duration", "member", "member_id", "room", "trigger",
    }
    assert set(prediction_data_of(_prediction_payload())) <= set(ENVELOPE_KEYS) | {
        "as_of", "next_arrival", "upcoming_device_hints", "confidence", "room",
    }


# ══════════════════════════════════════════════════════════════════════════
# B. 门面客户端：动作面 + 新鲜度闸 + 数据不足
# ══════════════════════════════════════════════════════════════════════════
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


def _context_payload(*, age_s: float = 0.0, **over):
    payload = {
        "as_of": (_NOW - timedelta(seconds=age_s)).isoformat().replace("+00:00", "Z"),
        "behavior_state": {"state": "sleeping", "confidence": 0.8, "since": "2026-11-17T21:30:00+08:00"},
        "presence": {"in_home": ["m1"], "last_change": "2026-11-17T21:40:00+08:00"},
        "prediction": {"next_arrival": "2026-11-18T07:30:00+08:00", "upcoming_device_hints": []},
        "intent": {"name": "sleep", "confidence": 0.7, "factors": []},
        "data_quality": {"coverage": 0.9, "sufficient": True},
    }
    payload.update(over)
    return payload


def _habits_payload(**over):
    payload = {
        "as_of": _NOW.isoformat().replace("+00:00", "Z"),
        "habits": [{"pattern": "weekday_ac_on", "entity": "climate.study_ac",
                    "typical_time_window": "21:00-23:00", "frequency": 0.8,
                    "stability": 0.7, "sample_weeks": 4, "evidence_refs": []}],
    }
    payload.update(over)
    return payload


def _opportunities_payload(**over):
    payload = {
        "as_of": _NOW.isoformat().replace("+00:00", "Z"),
        "gaps": [{"kind": "missing_rule", "description": "门口无人提醒", "evidence": [],
                  "sample_count": 9, "suggested_trigger": "binary_sensor.door"}],
        "candidates": [{"rule_id": "r1", "description": "d", "confidence": 0.5,
                        "evidence": [], "confirm_state": "needs_confirm"}],
        "data_gate": {"coverage": 0.8, "blocked_reasons": []},
    }
    payload.update(over)
    return payload


def test_b_facade_actions_and_params_face():
    assert MA_ACTIONS["get_automation_context"] == ("room", "member_id", "window_minutes")
    assert MA_ACTIONS["get_automation_opportunities"] == ("room", "top_k", "min_samples")
    assert MA_ACTIONS["get_member_habits"] == (
        "member_id", "room", "entity", "granularity", "weeks",
    )
    adapter = _live()
    _stub(adapter, _context_payload())
    assert adapter.call("get_automation_context", {"room": "zhu_wo_shi"}).success is True
    assert adapter.call("ma.get_automation_context", {"window_minutes": 60}).success is True

    bad = adapter.call("get_automation_context", {"channel": 21})
    assert bad.success is False and bad.data["code"] == ADM_ERR_PAYLOAD_INVALID
    with pytest.raises(AdapterError):
        adapter.call("get_room_snapshot", {})


def test_b_fresh_read_is_not_stale_and_carries_the_gate_fields():
    adapter = _live()
    _stub(adapter, _context_payload(age_s=5))
    result = adapter.call("get_automation_context", {})
    assert result.success is True
    assert result.data["freshness"]["stale"] is False
    assert result.data["freshness"]["age_s"] == pytest.approx(5.0, abs=0.01)
    assert result.data["freshness"]["stale_after_s"] == FRESHNESS_REALTIME_S
    assert "degraded" not in result.data
    assert adapter.stale_read_count == 0


def test_b_stale_realtime_read_is_a_fact_with_留痕(caplog):
    adapter = _live()
    _stub(adapter, _context_payload(age_s=300))
    with caplog.at_level("WARNING", logger="autoforge.af_adapters.ma"):
        result = adapter.call("get_automation_context", {})
    assert result.success is True, "陈旧是事实不是 error：自动化要能看见并降级"
    assert result.data["freshness"]["stale"] is True
    assert result.data["degraded"] is True
    assert any(r.startswith("stale:") for r in result.data["degraded_reasons"])
    assert adapter.stale_read_count == 1
    assert any("MA_READ_STALE" in r.getMessage() for r in caplog.records)


def test_b_peer_tighter_limit_wins_and_looser_is_not_widened():
    tighter = _live()
    _stub(tighter, _context_payload(age_s=30, stale_after_s=10))
    assert tighter.call("get_automation_context", {}).data["freshness"]["stale"] is True

    looser = _live()
    _stub(looser, _context_payload(age_s=300, stale_after_s=7200))
    gate = looser.call("get_automation_context", {}).data["freshness"]
    assert gate["stale_after_s"] == FRESHNESS_REALTIME_S, "对端更宽也⛔ 不放宽"
    assert gate["stale"] is True


def test_b_override_may_tighten_never_widen():
    tight = _live(freshness={"get_automation_context": 30.0})
    _stub(tight, _context_payload(age_s=60))
    assert tight.call("get_automation_context", {}).data["freshness"]["stale"] is True

    with pytest.raises(AdapterError) as exc:
        _live(freshness={"get_automation_context": FRESHNESS_REALTIME_S + 1})
    assert ADM_ERR_PAYLOAD_INVALID in str(exc.value)
    with pytest.raises(AdapterError):
        _live(freshness={"get_unknown_read": 10.0})


def test_b_unreadable_as_of_is_stale_fail_closed():
    for bad in (None, "", "not-a-time"):
        adapter = _live()
        payload = _context_payload()
        if bad is None:
            payload.pop("as_of")
        else:
            payload["as_of"] = bad
        _stub(adapter, payload)
        gate = adapter.call("get_automation_context", {}).data["freshness"]
        assert gate["stale"] is True and gate["reason"] == "as_of_unreadable", bad


def test_b_parse_as_of_handles_offsets_z_epoch_and_naive():
    assert parse_as_of("2026-11-17T12:00:00Z") == _NOW
    assert parse_as_of("2026-11-17T20:00:00+08:00") == _NOW
    assert parse_as_of(1_700_000_000) == datetime.fromtimestamp(1_700_000_000, tz=timezone.utc)
    naive = parse_as_of("2026-11-17T20:00:00")
    assert naive is not None and naive.tzinfo is not None, "裸时刻按家庭墙钟解释，⛔ 不留 naive"
    assert parse_as_of("garbage") is None and parse_as_of(True) is None


def test_b_freshness_thresholds_are_the_contract_defaults():
    assert FRESHNESS_REALTIME_S == 120.0
    assert FRESHNESS_PREDICTION_S == 900.0
    assert FRESHNESS_HABITS_S == 3600.0
    assert FRESHNESS_BY_READ["get_automation_context"] == FRESHNESS_REALTIME_S
    assert FRESHNESS_BY_READ["get_automation_opportunities"] == FRESHNESS_HABITS_S
    assert FRESHNESS_BY_READ["get_member_habits"] == FRESHNESS_HABITS_S


def test_b_insufficient_data_is_a_fact_not_an_error(caplog):
    adapter = _live()
    _stub(adapter, {"ok": False, "error": {"code": DATA_INSUFFICIENT, "message": "样本不足"}})
    with caplog.at_level("INFO", logger="autoforge.af_adapters.ma"):
        result = adapter.call("get_member_habits", {"member_id": "m1"})
    assert result.success is True, "DATA_INSUFFICIENT（200＋flag）＝事实降级，⛔ 不当 error"
    assert result.data["data_insufficient"] is True
    assert adapter.insufficient_read_count == 1
    assert any("MA_DATA_INSUFFICIENT" in r.getMessage() for r in caplog.records)


def test_b_data_quality_sufficient_false_is_the_same_fact():
    adapter = _live()
    _stub(adapter, _context_payload(data_quality={"coverage": 0.2, "sufficient": False}))
    result = adapter.call("get_automation_context", {})
    assert result.success is True
    assert result.data["data_insufficient"] is True
    assert result.data["data_insufficient_shape"] == "data_quality"
    assert result.data["freshness"]["stale"] is False, "陈旧与不足是两条独立断言"


def test_b_member_habits_three_shapes_are_distinguishable():
    # ① 样本不足：insufficient_data（裁定 §2.4 min_days=3）
    a = _live()
    _stub(a, {"as_of": _NOW.isoformat(), "insufficient_data": True, "data": []})
    r1 = a.call("get_member_habits", {})
    assert r1.success is True and r1.data["data_insufficient"] is True
    assert r1.data["data_insufficient_shape"] == "insufficient_data"

    # ② 样本够但真无规律：data:[] + sufficient:true ⇒ **不是**不足
    b = _live()
    _stub(b, {"as_of": _NOW.isoformat(), "data": [], "sufficient": True})
    r2 = b.call("get_member_habits", {})
    assert r2.success is True and "data_insufficient" not in r2.data
    assert b.insufficient_read_count == 0

    # ③ 正常规律
    c = _live()
    _stub(c, _habits_payload())
    r3 = c.call("get_member_habits", {})
    assert r3.success is True and r3.data["habits"][0]["pattern"] == "weekday_ac_on"


def test_b_only_as_of_empty_shell_is_refused():
    adapter = _live()
    _stub(adapter, {"as_of": _NOW.isoformat()})
    with pytest.raises(AdapterError) as exc:
        adapter.call("get_automation_context", {})
    assert ADM_ERR_PAYLOAD_INVALID in str(exc.value)


def test_b_dry_run_records_intent_without_network():
    adapter = MaAdapter(dry_run=True)
    result = adapter.call("get_member_habits", {"member_id": "m1"})
    assert result.success is True and result.data["dry_run"] is True
    assert adapter.intents[-1]["action"] == "get_member_habits"


# ══════════════════════════════════════════════════════════════════════════
# C. 错误分类：五态双形状
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("code,adm", sorted(MA_BUSINESS_ERROR_TO_ADM.items()))
def test_c_structured_business_errors_map_to_the_adm_family(code, adm):
    adapter = _live()
    _stub(adapter, {"ok": False, "error": {"code": code, "message": "x"}})
    result = adapter.call("get_automation_context", {})
    assert result.success is False
    assert result.data["code"] == adm
    assert result.data["ma_error_code"] == code
    assert result.data["business_error"] is True
    assert adapter.unregistered_tool_count == 0 and adapter.scope_denied_count == 0


def test_c_business_denied_is_not_scope_denied():
    adapter = _live()
    _stub(adapter, {"ok": False, "error": {"code": "DENIED", "message": "成员标签开关未开"}})
    result = adapter.call("get_automation_context", {})
    assert result.data["code"] == ADM_ERR_AUTH_REQUIRED
    assert result.data["business_error"] is True
    assert "defect" not in result.data, "业务 DENIED 不是分发级缺陷，⛔ 混判"
    assert adapter.scope_denied_count == 0


def test_c_dispatch_not_found_is_contract_drift_with_counter_and_alert(caplog):
    adapter = _live()
    _stub(adapter, envelope=_envelope(
        is_error=True, text="NOT_FOUND: 工具 'get_automation_context' 未登记",
    ))
    with caplog.at_level("ERROR", logger="autoforge.af_adapters.ma"):
        result = adapter.call("get_automation_context", {})
    assert result.success is False
    assert result.data["code"] == MA_ERR_CONTRACT_DRIFT
    assert result.data["defect"] == "unregistered_tool" and result.data["retryable"] is False
    assert adapter.unregistered_tool_count == 1 and adapter.scope_denied_count == 0
    assert any("MA_TOOL_UNREGISTERED" in r.getMessage() for r in caplog.records)


def test_c_dispatch_denied_is_scope_problem_with_its_own_alert(caplog):
    adapter = _live()
    _stub(adapter, envelope=_envelope(
        is_error=True, text="DENIED: 令牌无 write 权限",
    ))
    with caplog.at_level("ERROR", logger="autoforge.af_adapters.ma"):
        result = adapter.call("get_automation_context", {})
    assert result.data["code"] == MA_ERR_SCOPE_DENIED
    assert result.data["defect"] == "write_scope" and result.data["retryable"] is False
    assert adapter.scope_denied_count == 1 and adapter.unregistered_tool_count == 0
    assert any("MA_WRITE_SCOPE_DENIED" in r.getMessage() for r in caplog.records), "⛔ 不合并告警"


def test_c_two_dispatch_failures_never_share_one_alert(caplog):
    first, second = _live(), _live()
    _stub(first, envelope=_envelope(is_error=True, text="NOT_FOUND: 工具 'x' 未登记"))
    _stub(second, envelope=_envelope(is_error=True, text="DENIED: 令牌无 write 权限"))
    with caplog.at_level("ERROR", logger="autoforge.af_adapters.ma"):
        a = first.call("get_automation_context", {})
        b = second.call("get_automation_context", {})
    assert a.data["code"] != b.data["code"]
    alerts = [r.getMessage().split()[0] for r in caplog.records]
    assert alerts.count("MA_TOOL_UNREGISTERED") == 1
    assert alerts.count("MA_WRITE_SCOPE_DENIED") == 1


def test_c_structured_wins_over_text_prefix():
    """分类顺序钉死：结构化 `error.code` 优先 ⇒ 带结构化载荷时不按文本前缀判分发级。"""
    adapter = _live()
    _stub(adapter, envelope=_envelope(
        is_error=True,
        text="DENIED: 令牌无 write 权限",
        structured={"ok": False, "error": {"code": "DENIED", "message": "服务端开关未开"}},
    ))
    result = adapter.call("get_automation_context", {})
    assert result.data["business_error"] is True, "结构化优先（契约 §2.1）"
    assert result.data["ma_error_code"] == "DENIED"
    assert adapter.scope_denied_count == 0, "⛔ 不许把业务 DENIED 记成作用域告警"


def test_c_helpers_classify_independently():
    assert structured_error_code({"error": {"code": "rate_limited"}}) == "RATE_LIMITED"
    assert structured_error_code({"error": "not-a-mapping"}) is None
    assert insufficient_reason("get_member_habits", {"sufficient": False}) == "sufficient_false"
    assert insufficient_reason("get_automation_context", {"data_quality": {"sufficient": False}}) == "data_quality"
    assert insufficient_reason("get_member_habits", {"data": [], "sufficient": True}) is None
    assert dispatch_failure("NOT_FOUND: x").code == MA_ERR_CONTRACT_DRIFT
    assert dispatch_failure("DENIED: y").code == MA_ERR_SCOPE_DENIED
    assert dispatch_failure("正文里提到 DENIED 但行首不是") is None


def test_c_transport_failure_keeps_its_adm_code(monkeypatch):
    """传输层超时仍映射成既有 ADM 码（本批不动那条腿，只是回归腿）。"""
    import socket

    from autoforge.af_adapters import ma as ma_mod

    adapter = _live()

    def boom(*_a, **_kw):
        raise socket.timeout("timed out")

    monkeypatch.setattr(ma_mod, "guarded_open", boom)
    result = adapter.call("get_automation_context", {})
    assert result.success is False
    assert result.data["code"] == "ADM_ERR_UPSTREAM_TIMEOUT"
    assert DEFAULT_MA_TRANSPORT_TIMEOUT == 10.0


# ══════════════════════════════════════════════════════════════════════════
# D. 不漂断言：AF 对外 MCP 工具面 39
# ══════════════════════════════════════════════════════════════════════════
def test_d_af_outbound_tool_face_is_still_39():
    """契约 §四 第 7 行：门面客户端是**出向**，AF 对外工具面不动（批一批二各自断言）。"""
    assert len(af_mcp.TOOLS) == 39
    names = {row[0] for row in af_mcp.TOOLS}
    for facade in ("get_automation_context", "get_automation_opportunities", "get_member_habits"):
        assert facade not in names, f"{facade} 是 AF 出向调用，⛔ 不许进 AF 的对外工具面"
    assert len(MA_ACTIONS) == 7
