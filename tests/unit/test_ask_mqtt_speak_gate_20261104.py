# -*- coding: utf-8 -*-
"""ask 触发额外 MQTT 播报判据（DCD 裁定 2026-11-04 阶段1 乙）· 卡名 AF卡_ask_mqtt_speak。

覆盖（编号带卡名）：
  D1 ask 核心不变（开关行）：ask 仍挂起、pending_asks 建齐、prompt/room/spec 与改前逐字一致
  D2 speak 载荷逐字按 DB §3：trace_id/text/source=autoforge/room/priority=1，qos=1 retain=False
  D3 ask 触发即触发播报回调（trace_id=instance_id、text=prompt、room=node.room）
  D4 无桥 / 未注入 on_ask_speak ⇒ 零副作用、ask 照常挂起（不改核心）
  D5 播报旁路抛异常 ⇒ ask 核心不受影响（仍挂起）
  D6 room 从 IR node.room 取（room 空也发，但不伪造 room 键）
  D7 源码钉：_suspend 里在 pending_asks 写入之后发、⛔ 不在 ask 核心之前
"""

from __future__ import annotations

import pytest

from autoforge.af_bus import BusEvent
from autoforge.af_ir import Graph, load_automation
from autoforge.af_runtime import build_runtime

ROOM = "书房"

AUTO = {
    "ir_version": "0.2.1", "id": "ask_speak_case", "name": "ask播报用例", "version": 1,
    "mode": "restart",
    "nodes": [
        {"id": "a1", "kind": "on", "name": "人", "trigger": {"type": "state", "entity_id": "binary_sensor.motion", "to": "on"}},
        {"id": "q1", "kind": "ask", "name": "问", "prompt": "书房27度了，要开空调吗？", "session": "room", "room": ROOM, "timeout": "60s"},
        {"id": "p1", "kind": "pass", "name": "结束"},
    ],
    "edges": [
        {"from": "a1", "to": "q1", "kind": "then"},
        {"from": "q1", "to": "p1", "kind": "yes"},
        {"from": "q1", "to": "p1", "kind": "no"},
        {"from": "q1", "to": "p1", "kind": "on_timeout"},
        {"from": "q1", "to": "p1", "kind": "default"},
    ],
}


class _FakeBridge:
    def __init__(self):
        self.speaks = []

    def publish_speak(self, *, trace_id, text, room="", source="autoforge", priority=1):
        self.speaks.append({"trace_id": trace_id, "text": text, "room": room,
                            "source": source, "priority": priority})
        return {"published": True, "topic": "butler/inbox/speak",
                "payload": {"trace_id": trace_id, "text": text, "room": room,
                            "source": source, "priority": priority}}


def _runtime():
    rt = build_runtime(Graph([load_automation(AUTO)]))
    return rt


def _fire(rt):
    rt.states.set_state("binary_sensor.motion", "off")
    rt.publish(BusEvent.of("binary_sensor.motion", "on"))


# ── D1 ask 核心不变（开关行）─────────────────────────────────────────
def test_d1_ask_core_unchanged_without_bridge():
    rt = _runtime()
    _fire(rt)
    # ask 仍挂起、会话逐字建齐（与改前一致）
    assert len(rt.executor.pending_asks) == 1
    sess = next(iter(rt.executor.pending_asks.values()))
    assert sess.prompt == "书房27度了，要开空调吗？"
    assert sess.room == ROOM


# ── D3 ask 触发即记 speak（trace_id=instance_id, text=prompt, room）─────
def test_d3_ask_triggers_speak_with_instance_as_trace_id(monkeypatch):
    rt = _runtime()
    fb = _FakeBridge()
    rt.executor.on_ask_speak = lambda tid, text, room: fb.publish_speak(trace_id=tid, text=text, room=room)
    _fire(rt)
    assert len(fb.speaks) == 1, "ask 触发应额外发一条 speak"
    sp = fb.speaks[0]
    sess = next(iter(rt.executor.pending_asks.values()))
    assert sp["trace_id"] == sess.instance_id, "trace_id 必须是 instance_id（DB §2）"
    assert sp["text"] == "书房27度了，要开空调吗？"
    assert sp["room"] == ROOM


# ── D2 publish_speak 载荷逐字按 DB §3 + qos1 retainFalse ─────────────
def test_d2_publish_speak_payload_and_qos():
    from autoforge import af_mqtt_bridge as mb

    captured = {}

    class _FakeClient:
        pass

    bridge = object.__new__(mb.AfMqttBridge)
    bridge.client = _FakeClient()
    bridge.published = []
    bridge.counts = {"publish_errors": 0}
    bridge.degraded = set()

    # 拦 _mqtt.publish 记录参数
    def _fake_publish(client, topic, payload, *, qos=None, retain=False):
        captured.update(topic=topic, payload=payload, qos=qos, retain=retain)
        return type("R", (), {"rc": 0, "is_published": lambda self: True})()

    import autoforge.af_mqtt_bridge as mod
    monkeypatch_target = mod._mqtt.publish
    mod._mqtt.publish = _fake_publish
    try:
        r = bridge.publish_speak(trace_id="abc123", text="书房27度了要开空调吗", room="书房", priority=1)
    finally:
        mod._mqtt.publish = monkeypatch_target

    assert captured["topic"] == "butler/inbox/speak", "主题逐字（⛔ 变体）"
    assert captured["qos"] == 1, "QoS=1"
    assert captured["retain"] is False, "retain=False（⛔ retained 重连重复念）"
    p = captured["payload"]
    assert p["trace_id"] == "abc123"
    assert p["text"] == "书房27度了要开空调吗"
    assert p["source"] == "autoforge"
    assert p["room"] == "书房"
    assert p["priority"] == 1
    assert r["published"] is True


# ── D4 无桥/未注入 ⇒ 零副作用 ────────────────────────────────────────
def test_d4_no_callable_no_effect():
    rt = _runtime()
    assert rt.executor.on_ask_speak is not None  # Runtime 已注入闭包（惰性取桥）
    # 但当前无桥 ⇒ 闭包内 current_bridge() None ⇒ no-op，不抛
    _fire(rt)
    assert len(rt.executor.pending_asks) == 1


# ── D5 播报旁路抛异常 ⇒ ask 核心不受影响 ─────────────────────────────
def test_d5_speak_exception_does_not_break_ask():
    rt = _runtime()

    def _boom(*a, **k):
        raise RuntimeError("mqtt down")

    rt.executor.on_ask_speak = _boom
    _fire(rt)  # 不应抛
    assert len(rt.executor.pending_asks) == 1, "播报失败必须不影响 ask 挂起"


# ── D6 room 空 ⇒ 载荷不带 room 键（不伪造）────────────────────────────
def test_d6_empty_room_omits_room_key():
    from autoforge import af_mqtt_bridge as mb

    captured = {}
    bridge = object.__new__(mb.AfMqttBridge)
    bridge.client = object()
    bridge.published = []
    bridge.counts = {"publish_errors": 0}
    bridge.degraded = set()

    def _fake_publish(client, topic, payload, *, qos=None, retain=False):
        captured.update(payload=payload)
        return type("R", (), {"rc": 0, "is_published": lambda self: True})()

    import autoforge.af_mqtt_bridge as mod
    orig = mod._mqtt.publish
    mod._mqtt.publish = _fake_publish
    try:
        bridge.publish_speak(trace_id="t1", text="x", room="", priority=1)
    finally:
        mod._mqtt.publish = orig
    assert "room" not in captured["payload"], "room 空时不伪造 room 键"


# ── D7 源码钉：播报在 pending_asks 写入之后（不改 ask 核心时序）─────────
def test_d7_speak_after_pending_session_created():
    import inspect

    from autoforge.af_executor import NodeExecutor

    src = inspect.getsource(NodeExecutor._suspend)
    assert "on_ask_speak" in src, "_suspend 必须含播报钩子"
    # 播报必须在 AskSession 落 pending_asks 之后（不改 ask 核心挂起时序）
    assert src.index("self.pending_asks[instance.instance_id] = AskSession") < src.index("on_ask_speak"), \
        "播报必须在写待答之后（开关行：ask 核心时序不变）"
