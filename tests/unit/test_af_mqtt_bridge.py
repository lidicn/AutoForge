"""AF MQTT 桥（ADM 联动执行计划·第 1/2 步）—— 不需要 broker、不需要 paho 的契约锁。

本机没有 paho-mqtt、也没有 broker，所以全部用注入的假 client 真跑：
锁的是**送达语义**（主题名、retained 与否、LWT、禁订族、conf 封顶），
不是"能不能连上某个 IP"。连上 broker 的验收属 NAS 变更窗口（见执行记录）。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from autoforge.af_audit import AuditLog
from autoforge.af_conf import ConfidenceStore
from autoforge.af_mqtt_bridge import (
    FAILED_TOPIC,
    FIRED_TOPIC,
    INSIGHT_CONF_CAP,
    INSIGHTS_TOPIC,
    AfMqttBridge,
    BridgeUnavailable,
    attach,
    caps_payload,
    current_observers,
    detach,
    env_enabled,
    preflight,
)
from autoforge.af_runtime import Runtime
from autoforge.af_time import VirtualTimeSource, load_tz


class FakeClient:
    """鸭子类型的 paho client：只记录调用，不联网。"""

    def __init__(self, *, explode_on_publish: bool = False) -> None:
        self.published: list[dict] = []
        self.subscribed: list[str] = []
        self.will: dict | None = None
        self.on_message = None
        self.explode_on_publish = explode_on_publish

    def publish(self, topic, payload, qos=0, retain=False):
        if self.explode_on_publish:
            raise OSError("broker 掉了")
        self.published.append({"topic": topic, "payload": payload, "qos": qos, "retain": retain})
        return SimpleNamespace(rc=0)

    def subscribe(self, topic, qos=0):
        self.subscribed.append(topic)
        return (0, [1])

    def will_set(self, topic, payload, qos=0, retain=False):
        self.will = {"topic": topic, "payload": payload, "qos": qos, "retain": retain}


def _bridge(client=None, **kw):
    return AfMqttBridge(client or FakeClient(), clock=VirtualTimeSource(
        start=datetime(2026, 10, 2, 8, 0, tzinfo=timezone.utc)), **kw)


class RecordingSink:
    """假审批队列：记下 submit 收到的 conf，永不部署。"""

    def __init__(self):
        self.calls: list[dict] = []

    def submit(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(proposal_id="prop-1")


# ── 出向：presence（第 2 步）────────────────────────────────────────────
def test_start_publishes_retained_online_and_sets_lwt():
    client = FakeClient()
    _bridge(client).start(caps=caps_payload(tools=["af_draft"], version="2.5"))

    status = [p for p in client.published if p["topic"] == "adm/autoforge/status"]
    assert len(status) == 1 and status[0]["retain"] is True and status[0]["payload"] == "online"
    # LWT：进程被 kill -9 时由 broker 代发 offline，否则对端永远以为 AF 在线
    assert client.will == {"topic": "adm/autoforge/status", "payload": "offline", "qos": 1, "retain": True}
    caps = json.loads([p for p in client.published if p["topic"] == "adm/autoforge/caps"][0]["payload"])
    assert caps == {"mcp": True, "tools": ["af_draft"], "version": "2.5"}


def test_stop_publishes_retained_offline():
    client = FakeClient()
    bridge = _bridge(client)
    bridge.start()
    bridge.stop()
    last = [p for p in client.published if p["topic"] == "adm/autoforge/status"][-1]
    assert last["payload"] == "offline" and last["retain"] is True
    assert bridge.started is False


# ── 出向：事件流（第 1 步②）────────────────────────────────────────────
def test_fired_and_failed_are_not_retained():
    client = FakeClient()
    bridge = _bridge(client)
    assert bridge.publish_fired(automation_id="auto_a", instance_id="i1")["published"] is True
    bridge.publish_failed(automation_id="auto_a", instance_id="i2", error="boom")

    topics = [p["topic"] for p in client.published]
    assert topics == [FIRED_TOPIC, FAILED_TOPIC]
    assert all(p["retain"] is False for p in client.published), "事件流不回放状态"
    body = json.loads(client.published[1]["payload"]) if isinstance(client.published[1]["payload"], str) else client.published[1]["payload"]
    assert body["error"] == "boom" and body["automation_id"] == "auto_a"
    assert body["trace_id"], "跨仓排障锚点"


def _body(record: dict) -> dict:
    payload = record["payload"]
    return json.loads(payload) if isinstance(payload, str) else payload


def test_event_envelope_matches_adm_contract_table():
    """契约表 §1.2 载荷字段 + §四 时间口径：fired={trace_id,ts,automation_id,ref}，failed 多 error。

    `ts` 断言的是**口径**而不是某个固定偏移：必须带偏移、且偏移等于家庭时区当前偏移。
    写死 "+08:00" 会让这条测试在 TZ 环境变量被改动时假红，也放过了"naive UTC"这类真红。
    """
    client = FakeClient()
    bridge = _bridge(client)
    bridge.publish_fired(automation_id="auto_a", instance_id="inst-1")
    bridge.publish_failed(automation_id="auto_a", instance_id="inst-2", error="boom")
    fired, failed = (_body(p) for p in client.published)

    assert set(fired) == {"trace_id", "ts", "automation_id", "ref", "instance_id"}
    assert fired["ref"] == fired["instance_id"] == "inst-1"
    assert failed["ref"] == "inst-2" and failed["error"] == "boom"

    ts = datetime.fromisoformat(fired["ts"])
    assert ts.tzinfo is not None and ts.utcoffset() is not None, "ts 不许是 naive 或 Z（契约 §四）"
    assert ts.utcoffset() == datetime.now(load_tz()).utcoffset()


def test_observe_terminal_uses_instance_id_and_ignores_other_states():
    client = FakeClient()
    bridge = _bridge(client)
    inst = SimpleNamespace(automation=SimpleNamespace(id="auto_a"), id="WRONG",
                           instance_id="inst-9", current_node_id="n2")

    bridge.observe_terminal(inst, "done")
    bridge.observe_terminal(inst, "failed")
    assert bridge.observe_terminal(inst, "cancelled") is None

    fired = json.loads(client.published[0]["payload"]) if isinstance(client.published[0]["payload"], str) else client.published[0]["payload"]
    assert fired["instance_id"] == "inst-9"          # 审计第一轮的 P0：id 不是标识
    assert fired["instance_id"] != "WRONG"
    assert [p["topic"] for p in client.published] == [FIRED_TOPIC, FAILED_TOPIC]
    assert bridge.counts["fired"] == 1 and bridge.counts["failed"] == 1


def test_publish_error_is_recorded_not_raised():
    """broker 抖动不许把自动化执行链带崩，但必须留下可见的账。"""
    bridge = _bridge(FakeClient(explode_on_publish=True))
    result = bridge.publish_fired(automation_id="auto_a", instance_id="i1")
    assert result["published"] is False
    assert bridge.counts["publish_errors"] == 1
    assert bridge.stats()["recent_errors"]


def test_history_buckets_are_bounded():
    client = FakeClient()
    bridge = _bridge(client)
    for i in range(120):
        bridge.publish_fired(automation_id=f"auto_{i}", instance_id=f"i{i}")
    assert len(bridge.published) <= 50


# ── Runtime 接线（旁观者注册表）──────────────────────────────────────────
def test_runtime_terminal_hook_fans_out_to_registered_bridge():
    client = FakeClient()
    bridge = _bridge(client)
    attach(bridge)
    try:
        assert bridge.observe_terminal in current_observers()
        stub = SimpleNamespace(_exec_stats={})
        Runtime._on_terminal(stub, SimpleNamespace(
            automation=SimpleNamespace(id="auto_a"), instance_id="i7"), "done")
    finally:
        detach(bridge)
    assert [p["topic"] for p in client.published] == [FIRED_TOPIC]
    assert current_observers() == ()


def test_detach_is_idempotent():
    bridge = _bridge(FakeClient())
    detach(bridge)          # 没 attach 过也不炸
    attach(bridge)
    attach(bridge)          # 重复 attach 只注册一次
    try:
        assert list(current_observers()).count(bridge.observe_terminal) == 1
    finally:
        detach(bridge)


# ── fail-closed：配置不齐就不启动 ───────────────────────────────────────
def test_preflight_requires_broker_host(monkeypatch):
    monkeypatch.delenv("MQTT_HOST", raising=False)
    with pytest.raises(BridgeUnavailable, match="MQTT"):
        preflight()


def test_preflight_requires_credentials(monkeypatch):
    """有地址、没凭据 → 抛。匿名接入能指挥全屋，所以不是默认。"""
    monkeypatch.setenv("MQTT_HOST", "broker.invalid")
    for key in ("MQTT_USER", "MQTT_USERNAME", "MQTT_PASSWORD", "MQTT_PASS", "MQTT_PASSWD",
                "MQTT_USER_AUTOFORGE", "MQTT_PASSWORD_AUTOFORGE"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(BridgeUnavailable):
        preflight()
    assert preflight(allow_anonymous=True)[0] == "broker.invalid"


def test_bridge_off_unless_env_says_so(monkeypatch):
    monkeypatch.delenv("AUTOFORGE_MQTT", raising=False)
    assert env_enabled() is False
    monkeypatch.setenv("AUTOFORGE_MQTT", "0")
    assert env_enabled() is False
    monkeypatch.setenv("AUTOFORGE_MQTT", "1")
    assert env_enabled() is True


# ── 入向：ma/insights（第 1 步③）────────────────────────────────────────
def test_insight_conf_is_capped_into_ask_band():
    sink = RecordingSink()
    bridge = _bridge(proposal_sink=sink)
    result = bridge.handle_message(None, None, SimpleNamespace(
        topic=INSIGHTS_TOPIC,
        payload=json.dumps({"hypothesis_id": "h1", "natural_language": "天黑关廊灯", "conf": 0.97}).encode()))
    assert result["handled"] is True
    assert sink.calls[0]["conf"] == INSIGHT_CONF_CAP          # 高置信也不许自动部署
    assert sink.calls[0]["source"] == "ma"


@pytest.mark.parametrize("payload,reason", [
    ({"natural_language": "缺了假设号", "conf": 0.9}, "missing_hypothesis_id"),
    ({"hypothesis_id": "h", "conf": 0.9}, "missing_natural_language_and_intent"),
    ({"hypothesis_id": "h", "natural_language": "x"}, "conf_missing_or_out_of_range"),
    ({"hypothesis_id": "h", "natural_language": "x", "conf": 1.7}, "conf_missing_or_out_of_range"),
    ({"hypothesis_id": "h", "natural_language": "x", "conf": True}, "conf_missing_or_out_of_range"),
])
def test_malformed_insight_is_rejected_not_invented(payload, reason):
    sink = RecordingSink()
    bridge = _bridge(proposal_sink=sink)
    result = bridge.handle_message(None, None, SimpleNamespace(topic=INSIGHTS_TOPIC, payload=json.dumps(payload).encode()))
    assert result["handled"] is False and result["reason"] == reason
    assert sink.calls == []
    assert bridge.counts["insights_rejected"] == 1


def test_undecodable_payload_and_foreign_topics_are_dropped():
    bridge = _bridge(proposal_sink=RecordingSink())
    bad = bridge.handle_message(None, None, SimpleNamespace(topic=INSIGHTS_TOPIC, payload=b"{not json"))
    assert bad["handled"] is False and bad["reason"] == "undecodable_payload"

    other = bridge.handle_message(None, None, SimpleNamespace(topic="butler/trigger/living", payload=b"{}"))
    assert other["handled"] is False and other["reason"] == "topic_not_mine"


def test_inbox_topics_are_never_subscribed_and_never_handled():
    """收件箱归 DB。AF 既不去订，也不替 DB 处理。"""
    client = FakeClient()
    bridge = _bridge(client, proposal_sink=RecordingSink())
    bridge.start()
    assert client.subscribed == [INSIGHTS_TOPIC]

    for topic in ("butler/inbox/speak", "butler/inbox/notify", "butler/inbox/tv"):
        assert bridge.subscribe_topic(topic) is False
    assert client.subscribed == [INSIGHTS_TOPIC]
    dropped = bridge.handle_message(None, None, SimpleNamespace(topic="butler/inbox/speak", payload=b"{}"))
    assert dropped["handled"] is False
    assert bridge.counts["forbidden_seen"] == 4


def test_insight_without_sink_is_refused_loudly():
    """没接审批队列就说"没接"——不许把洞察吞了还报 handled。"""
    bridge = _bridge()
    result = bridge.handle_message(None, None, SimpleNamespace(
        topic=INSIGHTS_TOPIC, payload=json.dumps({"hypothesis_id": "h", "natural_language": "x", "conf": .5}).encode()))
    assert result["reason"] == "no_proposal_sink_wired"


# ── 端到端（第 1 步③ 判据）：真 ProposalManager + 真 af_draft，假 client ──
def _real_manager():
    from autoforge.af_proposal import ProposalManager

    def _never_deploy(plan):
        raise AssertionError(f"MA 洞察不得自动部署：{plan}")

    return ProposalManager(
        conf=ConfidenceStore(), recorder=None, clock=VirtualTimeSource(
            start=datetime(2026, 10, 2, 8, 0, tzinfo=timezone.utc)),
        audit=AuditLog(), deployer=_never_deploy,
    )


def test_insight_with_intent_lands_in_approval_queue_with_compiled_ir():
    manager = _real_manager()
    bridge = _bridge(proposal_sink=manager)
    result = bridge.handle_message(None, None, SimpleNamespace(topic=INSIGHTS_TOPIC, payload=json.dumps({
        "hypothesis_id": "h-e2e",
        "natural_language": "玄关有人且照度低时开鞋柜灯",
        "conf": 0.91,
        "intent": {
            "name": "玄关补光",
            "when": {"type": "state", "entity": "binary_sensor.entry_presence", "to": "on"},
            "do": {"action": "turn_on", "target": "light.shoe_cabinet"},
        },
    }).encode()))

    assert result["handled"] is True and result["had_ir"] is True
    proposal = manager.proposals[result["proposal_id"]]
    assert proposal.conf == INSIGHT_CONF_CAP                  # 进队即被封顶，不由对端说了算
    assert manager.band_for(proposal.conf) == "ask"
    assert proposal.suggested_ir is not None
    assert "automations" in json.dumps(proposal.suggested_ir, default=str)
    # ask 档只出提案：deployer 一旦被调用就直接 AssertionError，这里能过就是没部署
    assert proposal.status.name in ("PENDING", "WAITING_APPROVAL", "RECEIVED") or proposal.automation_id == ""


def test_insight_without_intent_still_proposes_but_carries_no_ir():
    manager = _real_manager()
    bridge = _bridge(proposal_sink=manager)
    result = bridge.handle_message(None, None, SimpleNamespace(topic=INSIGHTS_TOPIC, payload=json.dumps({
        "hypothesis_id": "h-nl", "natural_language": "周末早上晚十分钟拉帘", "conf": 0.42,
    }).encode()))
    assert result["handled"] is True and result["had_ir"] is False
    assert manager.proposals[result["proposal_id"]].conf == 0.42   # 未触顶时不改值


def test_uncompilable_intent_degrades_to_plain_proposal_with_trace():
    manager = _real_manager()
    bridge = _bridge(proposal_sink=manager)
    result = bridge.handle_message(None, None, SimpleNamespace(topic=INSIGHTS_TOPIC, payload=json.dumps({
        "hypothesis_id": "h-bad", "natural_language": "x", "conf": 0.5,
        "intent": {"name": "缺触发", "do": {"action": "turn_on", "target": "light.a"}},
    }).encode()))
    assert result["handled"] is True and result["had_ir"] is False
    assert bridge.stats()["recent_errors"], "编译失败必须留痕，不能静默当成没有 intent"

