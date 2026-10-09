"""卡3 的订阅/入向半边：broker 回调 → 落盘队列 → `on event` 自动化，一条链真跑。

本机没有 broker、也没有 paho，所以照旧注入鸭子类型的假 client——锁的是**送达与角色**：
订了哪两条、什么形状会被拒、拒收有没有带契约码、事件最终落到哪个自动化上。

四条判据对应四条腿：
- 能投 ≠ 能订：`butler/inbox/*` 一条都不订（另一份文件里另锁）；
- 回调只落盘，不碰总线（`EventBus` 没有锁，线程交接靠队列）；
- 拒收必须带 `ADM_ERR_*` 且单独计数（契约 §7.2「禁止静默丢弃」）；
- 超龄不补触发：队列里有记录 ≠ 自动化被旧事实驱动。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

from homesdk.adm.errors import ADM_ERR_INTERNAL, ADM_ERR_PAYLOAD_INVALID

from autoforge import af_live, af_mqtt_bridge
from autoforge.af_adapters import MockAdapter
from autoforge.af_bus import BusEvent
from autoforge.af_ir import load_graph
from autoforge.af_linkage_feed import KIND_DEVICE_HEALTH, KIND_PRESENCE, TRIGGER_MAX_AGE_S
from autoforge.af_mqtt_bridge import (
    DEVICE_HEALTH_TOPIC,
    INSIGHTS_TOPIC,
    LINKAGE_TOPICS,
    PRESENCE_TOPIC,
    AfMqttBridge,
    make_linkage_feed,
)
from autoforge.af_runtime import build_runtime
from autoforge.af_time import VirtualTimeSource


class FakeClient:
    def __init__(self) -> None:
        self.published: list[dict] = []
        self.subscribed: list[str] = []
        self.on_message = None

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append({"topic": topic, "payload": payload, "qos": qos, "retain": retain})
        return SimpleNamespace(rc=0)

    def subscribe(self, topic, qos=0):
        self.subscribed.append(topic)
        return (0, [1])

    def will_set(self, topic, payload, qos=0, retain=False):
        pass


def _clock() -> VirtualTimeSource:
    return VirtualTimeSource(start=datetime(2026, 10, 9, 1, 0, tzinfo=timezone.utc))


def _bridge(tmp_path, *, client=None, with_sink: bool = True, clock=None):
    client = client or FakeClient()
    clock = clock or _clock()
    sink = make_linkage_feed(store_root=tmp_path / "store", clock=clock) if with_sink else None
    return AfMqttBridge(client, linkage_sink=sink, clock=clock), client


def _msg(topic: str, payload) -> SimpleNamespace:
    raw = payload if isinstance(payload, (bytes, bytearray)) else (
        payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    )
    return SimpleNamespace(topic=topic, payload=raw.encode("utf-8") if isinstance(raw, str) else raw)


PRESENCE_OK = {
    "trace_id": "t-1",
    "ts": "2026-10-09T09:00:00+08:00",
    "total": 2,
    "members": [
        {"name": "爸爸", "member_id": "dad", "room": "书房", "via": "vlm", "confidence": 0.82},
        {"name": "妈妈", "member_id": "mom", "room": "客厅", "via": "face", "confidence": 0.7},
    ],
}

HEALTH_OK = {
    "trace_id": "t-2",
    "device_id": "light.desk",
    "entity_id": "sensor.plug",
    "status": "unavailable",
    "from": "on",
    "to": "unavailable",
    "stable_id": "dh-77",
}


# ── 收：落盘 ──────────────────────────────────────────────────────────
def test_presence_lands_in_its_own_directory(tmp_path):
    bridge, _ = _bridge(tmp_path)
    result = bridge.handle_message(None, None, _msg(PRESENCE_TOPIC, PRESENCE_OK))

    assert result["handled"] is True and result["kind"] == KIND_PRESENCE
    assert bridge.counts["presence_in"] == 1 and bridge.counts["linkage_rejected"] == 0
    files = list((bridge.linkage_sink.root / KIND_PRESENCE).glob("*.json"))
    assert len(files) == 1
    on_disk = json.loads(files[0].read_text(encoding="utf-8"))
    assert on_disk["trace_id"] == "t-1" and on_disk["subject"] == "dad，mom"
    assert on_disk["data"]["total"] == 2 and len(on_disk["data"]["members"]) == 2


def test_device_health_lands_in_its_own_directory(tmp_path):
    bridge, _ = _bridge(tmp_path)
    result = bridge.handle_message(None, None, _msg(DEVICE_HEALTH_TOPIC, HEALTH_OK))

    assert result["handled"] is True and result["kind"] == KIND_DEVICE_HEALTH
    on_disk = json.loads(list((bridge.linkage_sink.root / KIND_DEVICE_HEALTH).glob("*.json"))[0]
                         .read_text(encoding="utf-8"))
    assert on_disk["data"]["from_state"] == "on" and on_disk["data"]["stable_id"] == "dh-77"
    assert on_disk["subject"] == "light.desk"


def test_empty_house_is_a_legal_presence_snapshot(tmp_path):
    """`members: []` 是"真的没人"，不是"对端漏了字段"——拒收它反而会让自动化拿着假事实动作。"""
    bridge, _ = _bridge(tmp_path)
    result = bridge.handle_message(None, None, _msg(PRESENCE_TOPIC, {"trace_id": "t-3", "members": []}))
    assert result["handled"] is True
    assert result["subject"] == ""


def test_private_keys_never_reach_disk(tmp_path):
    """契约 §1.2 的 member 子键表里没有 `via_raw`；多余键不许经 AF 的归档扩散。"""
    payload = {
        "trace_id": "t-4",
        "members": [{"member_id": "dad", "via_raw": "整段内部推理原文", "ma_debug": {"prompt": "x"}}],
    }
    bridge, _ = _bridge(tmp_path)
    bridge.handle_message(None, None, _msg(PRESENCE_TOPIC, payload))
    dumped = json.dumps([r.to_dict() for r in bridge.linkage_sink.list_recent()], ensure_ascii=False)
    assert "via_raw" not in dumped and "ma_debug" not in dumped


def test_total_survives_only_when_it_is_a_real_int(tmp_path):
    """`total` 是展示项：坏了就丢这一键、照常收，不许因为它把整条在场快照挡掉。"""
    for bad, want in (( "2", False), (True, False), (2.5, False), (7, True)):
        bridge, _ = _bridge(tmp_path / f"store-{want}-{bad}")
        got = bridge.handle_message(
            None, None, _msg(PRESENCE_TOPIC, {"trace_id": "t", "members": [], "total": bad}))
        assert got["handled"] is True, got
        rec = bridge.linkage_sink.poll_new()[0]
        assert ("total" in rec.data) is want, rec.data


# ── 拒：每一条都要有码、有计数、不落盘 ────────────────────────────────
def test_rejections_carry_a_contract_code(tmp_path):
    cases = [
        (PRESENCE_TOPIC, {"members": []}, "missing_trace_id"),
        (PRESENCE_TOPIC, {"trace_id": " ", "members": []}, "missing_trace_id"),
        (PRESENCE_TOPIC, {"trace_id": "t", "members": {"dad": 1}}, "members_not_list"),
        (PRESENCE_TOPIC, {"trace_id": "t"}, "members_not_list"),
        (DEVICE_HEALTH_TOPIC, {"trace_id": "t"}, "missing_stable_id"),
        (DEVICE_HEALTH_TOPIC, {"trace_id": "t", "stable_id": ""}, "missing_stable_id"),
        (PRESENCE_TOPIC, "[1,2]", "payload_not_object"),
        (DEVICE_HEALTH_TOPIC, "{not json", "undecodable_payload"),
    ]
    bridge, _ = _bridge(tmp_path)
    for topic, payload, reason in cases:
        result = bridge.handle_message(None, None, _msg(topic, payload))
        assert result["handled"] is False and result["reason"] == reason, result
        assert result["code"] in (ADM_ERR_PAYLOAD_INVALID, ADM_ERR_INTERNAL), result
        assert result["topic"] == topic
    assert bridge.counts["linkage_rejected"] == len(cases)
    assert bridge.counts["presence_in"] == 0 and bridge.counts["device_health_in"] == 0
    assert bridge.linkage_sink.list_recent() == []
    assert len(bridge.stats()["recent_rejected"]) == len(cases)


def test_without_a_sink_the_bridge_says_so(tmp_path):
    """没给落盘队列就说"没接"（`ADM_ERR_INTERNAL`），不许把入向事件默默收下还报 handled。"""
    bridge, _ = _bridge(tmp_path, with_sink=False)
    result = bridge.handle_message(None, None, _msg(PRESENCE_TOPIC, PRESENCE_OK))
    assert result["handled"] is False
    assert result["reason"] == "no_linkage_sink_wired"
    assert result["code"] == ADM_ERR_INTERNAL
    assert bridge.counts["linkage_rejected"] == 1
    assert bridge.linkage_stats() == {"wired": False}


def test_insights_and_linkage_have_separate_reject_counters(tmp_path):
    """两条队列两个对端契约：混进同一枚计数就分不清"MA 发坏了"和"入向线整条没接"。"""
    bridge, _ = _bridge(tmp_path)
    bridge.handle_message(None, None, _msg(INSIGHTS_TOPIC, {"natural_language": "x", "conf": 0.5}))
    bridge.handle_message(None, None, _msg(PRESENCE_TOPIC, {"members": []}))

    assert bridge.counts["insights_rejected"] == 1          # 洞察缺稳定身份
    assert bridge.counts["linkage_rejected"] == 1           # 在场缺 trace_id
    assert bridge.counts["insights_in"] == 1


# ── 消费：队列 → 总线 → `on event` 自动化 ────────────────────────────
def _presence_runtime():
    graph = load_graph({"automations": [{
        "ir_version": "0.2.1",
        "id": "who_is_home",
        "name": "谁在家就报一声",
        "version": 1,
        "mode": "single",
        "nodes": [
            {"id": "o", "kind": "on", "trigger": {"type": "event", "event": "ma_presence"}},
            {"id": "d", "kind": "do", "adapter": "mock", "action": "light.turn_on",
             "params": {"entity_id": "light.b"}},
            {"id": "p", "kind": "pass"},
        ],
        "edges": [{"from": "o", "to": "d", "kind": "then"}, {"from": "d", "to": "p", "kind": "then"}],
    }]})
    runtime = build_runtime(graph)
    runtime.adapters.register(MockAdapter())
    return runtime


def test_pump_fires_the_existing_on_event_channel(tmp_path):
    """卡3 不新立触发类型：在场快照就是 `on event.ma_presence` 上的一次事件。

    `bus.emitted` 是发布侧（`emit` 节点）那本账，`runtime.publish()` 这条不进它——
    所以载荷读数用一条精确订阅（键 `event.ma_presence`）自己取，不去借别人的账本。
    """
    bridge, _ = _bridge(tmp_path)
    bridge.handle_message(None, None, _msg(PRESENCE_TOPIC, PRESENCE_OK))
    runtime = _presence_runtime()
    seen: list = []
    runtime.bus.subscribe(BusEvent.event_name("ma_presence"), seen.append)

    assert af_live.pump_linkage(runtime, bridge) == 1
    assert [a for a, _ in runtime.adapters.get("mock").calls] == ["light.turn_on"]
    assert [e.event for e in seen] == ["ma_presence"]
    assert seen[0].source == "emit" and seen[0].state == ""
    assert seen[0].payload["total"] == 2 and seen[0].payload["trace_id"] == "t-1"


def test_pump_consumes_once(tmp_path):
    """同一个 tick 之后再泵不得重复触发（水位线就是那条消费边）。"""
    bridge, _ = _bridge(tmp_path)
    bridge.handle_message(None, None, _msg(PRESENCE_TOPIC, PRESENCE_OK))
    runtime = _presence_runtime()

    assert af_live.pump_linkage(runtime, bridge) == 1
    assert af_live.pump_linkage(runtime, bridge) == 0
    assert len(runtime.adapters.get("mock").calls) == 1


def test_over_age_event_is_not_replayed(tmp_path):
    """重启不回放：一条超龄的在场记录留在盘上，但不许在开机瞬间触发一次真实下发。"""
    clock = _clock()
    bridge, _ = _bridge(tmp_path, clock=clock)
    bridge.handle_message(None, None, _msg(PRESENCE_TOPIC, PRESENCE_OK))
    clock.advance(TRIGGER_MAX_AGE_S + 60.0)
    runtime = _presence_runtime()

    assert af_live.pump_linkage(runtime, bridge) == 0
    assert runtime.adapters.get("mock").calls == []
    assert len(bridge.linkage_sink.list_recent()) == 1        # 记录仍在，只是不再当触发


def test_pump_without_a_bridge_is_a_noop(tmp_path):
    """没开 `AUTOFORGE_MQTT` 时行为与接桥前逐字相同：零动作、不抛。"""
    assert af_live.pump_linkage(_presence_runtime(), None) == 0


# ── 健康面读数（判据 E：写进去 must 读得出来）────────────────────────
def test_inbound_readings_are_surfaced(tmp_path):
    client = FakeClient()
    bridge, _ = _bridge(tmp_path, client=client)
    bridge.start()
    bridge.handle_message(None, None, _msg(PRESENCE_TOPIC, PRESENCE_OK))
    bridge.handle_message(None, None, _msg(DEVICE_HEALTH_TOPIC, HEALTH_OK))
    bridge.handle_message(None, None, _msg(PRESENCE_TOPIC, {"members": []}))

    inbound = af_mqtt_bridge.linkage_status(bridge)["inbound"]
    assert inbound["subscribed"] == list(LINKAGE_TOPICS)
    assert client.subscribed == [INSIGHTS_TOPIC, *LINKAGE_TOPICS]
    assert inbound["presence_in"] == 1 and inbound["device_health_in"] == 1
    assert inbound["rejected"] == 1
    assert inbound["feed"]["wired"] is True
    assert inbound["feed"]["per_kind"] == {KIND_PRESENCE: 1, KIND_DEVICE_HEALTH: 1}
    assert inbound["feed"]["root"].endswith("linkage_events")


def test_make_linkage_feed_keeps_two_separate_directories(tmp_path):
    """洞察队列与联动队列分开：一条满了要拒收，一条满了要裁旧，共用目录就只剩一种回收策略。"""
    feed = make_linkage_feed(store_root=tmp_path / "store")
    assert feed.root == tmp_path / "store" / "linkage_events"
    assert feed.root != (tmp_path / "store" / "insight_proposals")


# ── 裁定 20261009 §四 甲：平键走完真链路（桥 → 队列 → 总线 → DSL 分支）──
def _branch_runtime(event_name: str, want: str):
    """`on event.<name>` 之后按 `context.trigger_entity_id` 分岔——裁定举的那个用例本身。"""
    graph = load_graph({"automations": [{
        "ir_version": "0.2.1",
        "id": "pick_backup",
        "name": "掉的若是这台就走备用那条",
        "version": 1,
        "mode": "single",
        "nodes": [
            {"id": "o", "kind": "on", "trigger": {"type": "event", "event": event_name}},
            {"id": "i", "kind": "if", "expr": {
                "op": "eq",
                "left": {"var": "context.trigger_entity_id", "type": "string"},
                "right": {"const": want},
            }},
            {"id": "d_main", "kind": "do", "adapter": "mock", "action": "light.turn_on",
             "params": {"entity_id": "light.b"}},
            {"id": "d_backup", "kind": "do", "adapter": "mock", "action": "light.turn_off",
             "params": {"entity_id": "light.b"}},
            {"id": "p", "kind": "pass"},
        ],
        "edges": [
            {"from": "o", "to": "i", "kind": "then"},
            {"from": "i", "to": "d_main", "kind": "then"},
            {"from": "i", "to": "d_backup", "kind": "no"},
            {"from": "d_main", "to": "p", "kind": "then"},
            {"from": "d_backup", "to": "p", "kind": "then"},
        ],
    }]})
    runtime = build_runtime(graph)
    runtime.adapters.register(MockAdapter())
    return runtime


def test_device_health_flat_entity_decides_the_dsl_branch(tmp_path):
    bridge, _ = _bridge(tmp_path)
    bridge.handle_message(None, None, _msg(DEVICE_HEALTH_TOPIC, HEALTH_OK))
    runtime = _branch_runtime("ma_device_health", "sensor.plug")

    assert af_live.pump_linkage(runtime, bridge) == 1
    assert [a for a, _ in runtime.adapters.get("mock").calls] == ["light.turn_on"]
    ctx = runtime.instances.all()[0].ctx.context
    assert ctx["trigger_entity_id"] == "sensor.plug"
    assert ctx["trigger_subject"] == "sensor.plug"      # 裁定：device-health 的主标识取 entity_id
    assert ctx["trigger_kind"] == KIND_DEVICE_HEALTH


def test_a_different_device_takes_the_other_branch(tmp_path):
    """判据不是"读得出"，是"读出的值真会改分支"——否则平键等于一件摆设。"""
    bridge, _ = _bridge(tmp_path)
    bridge.handle_message(None, None, _msg(DEVICE_HEALTH_TOPIC, {**HEALTH_OK, "entity_id": "sensor.fridge"}))
    runtime = _branch_runtime("ma_device_health", "sensor.plug")

    assert af_live.pump_linkage(runtime, bridge) == 1
    assert [a for a, _ in runtime.adapters.get("mock").calls] == ["light.turn_off"]


def test_presence_flat_keys_reach_the_instance(tmp_path):
    bridge, _ = _bridge(tmp_path)
    bridge.handle_message(None, None, _msg(PRESENCE_TOPIC, PRESENCE_OK))
    runtime = _presence_runtime()

    assert af_live.pump_linkage(runtime, bridge) == 1
    ctx = runtime.instances.all()[0].ctx.context
    assert ctx["trigger_entity_id"] == ""               # 契约 §1.2 的 presence 行没有 entity_id
    assert ctx["trigger_subject"] == "dad，mom"          # 主标识是 member_id 串
    assert ctx["trigger_kind"] == KIND_PRESENCE
