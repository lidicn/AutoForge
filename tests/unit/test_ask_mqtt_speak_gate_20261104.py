# -*- coding: utf-8 -*-
"""ask 触发 MQTT 播报判据 · **AF-776 后语义反转**（原卡 AF卡_ask_mqtt_speak，DCD 2026-11-04 阶段1 乙）。

**这次为什么翻面**：AF-776（2026-11-13 出资人体感「一次询问念两遍」）撤掉了 ask 的 speak 腿。
原 D3「ask 触发即发一条 speak」、原 D4「Runtime 已注入 on_ask_speak 闭包」、原 D7「_suspend 必须含
播报钩子」——这三条钉的正是**被撤掉的那个行为**，留着就是钉死缺陷。

⛔ 不是把断言删掉了事，而是**如实翻转**：现在要钉的是「ask ⛔ 不许再发 speak」，
以及「⛔ 不许留一个能一键挂回去的空钩子」（空钩子会被读成"撤了没生效"）。

覆盖（编号沿用，语义按 AF-776 重写）：
  D1 ask 核心不变（开关行）：ask 仍挂起、pending_asks 建齐、prompt/room/spec 与改前逐字一致
  D2 speak 载荷逐字按 DB §3：trace_id/text/source=autoforge/room/priority=1，qos=1 retain=False
     （桥方法本身没动——非 ask 的 do／emit／fail 三条腿还在用它）
  D3 ask 触发 ⛔ 不发 speak（AF-776 反转项；原钉「必发 1 条」）
  D4 无桥 ⇒ 零副作用、ask 照常挂起；且 executor ⛔ 不再有 `on_ask_speak` 属性
  D5 就算有人手工把 `on_ask_speak` 塞回 executor，⛔ 也不会被调用（防"挂回去"）
  D6 room 空也发、但不伪造 room 键（桥方法面，未变）
  D7 源码钉：`_suspend` ⛔ 不含 `self.on_ask_speak(...)` 调用；`af/ask/pending` 仍在写待答之后
"""

from __future__ import annotations

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
        self.pending = []

    def publish_speak(self, *, trace_id, text, room="", source="autoforge", priority=1):
        self.speaks.append({"trace_id": trace_id, "text": text, "room": room,
                            "source": source, "priority": priority})
        return {"published": True, "topic": "butler/inbox/speak",
                "payload": {"trace_id": trace_id, "text": text, "room": room,
                            "source": source, "priority": priority}}

    def publish_ask_pending(self, **kw):
        self.pending.append(kw)
        return {"published": True, "topic": "af/ask/pending", "payload": kw}


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


# ── D3 ask 触发 ⛔ 不发 speak（AF-776 反转项）──────────────────────────
def test_d3_ask_does_not_publish_speak(monkeypatch):
    """AF-776：问句只走 `af/ask/pending`，⛔ 不许再经 `butler/inbox/speak` 念第二遍。

    修前形状（原 D3）：`len(fb.speaks) == 1`——那条断言就是缺陷本身，已按 AF-PM 派卡翻转。
    ⛔ 不许把它改回「等于 1」：18:49 trace 实测 DB 因此 TTS 两遍。
    """
    from autoforge import af_mqtt_bridge as mqtt

    rt = _runtime()
    fb = _FakeBridge()
    monkeypatch.setattr(mqtt, "current_bridge", lambda: fb)
    monkeypatch.setattr(rt, "resolve_room", lambda room_text, *, source, entity_id="": "shu_fang")
    _fire(rt)

    assert fb.speaks == [], "ask 触发⛔ 不许发 speak（AF-776：DB 收到 pending 后自己念）"
    assert len(fb.pending) == 1, "唯一那条出声腿是 af/ask/pending"


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


# ── D4 无桥 ⇒ 零副作用；且 executor ⛔ 不再有 on_ask_speak ──────────────
def test_d4_no_bridge_no_effect_and_hook_is_gone():
    rt = _runtime()
    # AF-776：钩子连属性都不留（原钉是 `is not None`——那正是被撤掉的东西）
    assert not hasattr(rt.executor, "on_ask_speak"), (
        "⛔ executor 不许再留 ask 的 speak 钩子（空钩子会被读成「撤了没生效」）"
    )
    _fire(rt)  # 无桥：⛔ 不许抛
    assert len(rt.executor.pending_asks) == 1


# ── D5 手工塞回钩子也⛔ 不会被调用（防挂回去）──────────────────────────
def test_d5_reinjected_hook_is_never_called():
    """反证腿：有人在 executor 上手工塞 `on_ask_speak`，`_suspend` 里⛔ 也不许再读它。

    没有这条，撤腿可能变成"只删了 Runtime 的注入、executor 里那段调用还留着"——
    在 serve 里看着一样（没注入就 no-op），但只要有人一注入就复发。
    """
    rt = _runtime()
    calls: list[tuple] = []
    rt.executor.on_ask_speak = lambda *a, **k: calls.append((a, k))  # 手工塞回去
    _fire(rt)
    assert calls == [], "⛔ _suspend 不许再调用 on_ask_speak（撤腿必须撤干净）"
    assert len(rt.executor.pending_asks) == 1


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


# ── D7 源码钉：_suspend 里⛔ 没有 ask 播报调用；pending 仍在写待答之后 ──
def test_d7_source_pin_no_ask_speak_in_suspend():
    import inspect

    from autoforge.af_executor import NodeExecutor

    src = inspect.getsource(NodeExecutor._suspend)
    assert "self.on_ask_speak(" not in src, (
        "⛔ `_suspend` 不许再调用 on_ask_speak（AF-776 撤腿；注释里点名历史是允许的）"
    )
    # 时序钉不变：pending 仍在写待答之后（DB 答回来要对得上 pending_asks）
    assert src.index("self.pending_asks[instance.instance_id] = AskSession") < src.index("on_ask_pending"), \
        "af/ask/pending 必须在写待答之后发（开关行：ask 核心时序不变）"
