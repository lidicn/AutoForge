"""AF-748-B（2026-11-09 真机实证）：同一容器的两路 AF 进程不许共用 broker 身份。

现场：`forge serve` 与它拉起的 `forge watch` 各自建桥、client_id 同取 `MQTT_CLIENT_ID`
⇒ broker 每 5 秒按 client_id 踢旧连接（`/mosquitto/log` 现读累计 28 万+ 次），
发布撞在"刚被踢"的窗口就回 `rc=4`（`MQTT_ERR_NO_CONN`）⇒ `butler/inbox/speak` 出不去（TTS 无声）。

裁定（PM）：B 案——watch 侧 client_id 加 `-watch` 后缀、`presence=False`（不设 LWT、不发
retained status/caps）、不订入向主题；身份 owner 只留 serve。
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from autoforge import af_cli, af_mqtt_bridge
from autoforge.af_mqtt_bridge import STATUS_TOPIC, AfMqttBridge
from autoforge.af_time import VirtualTimeSource


class FakeClient:
    """鸭子类型 paho client：只记录，不联网。"""

    def __init__(self) -> None:
        self.published: list[dict] = []
        self.subscribed: list[str] = []
        self.will: dict | None = None
        self.on_message = None
        self.connected: tuple | None = None
        self.loop_started = False

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append({"topic": topic, "payload": payload, "qos": qos, "retain": retain})
        return SimpleNamespace(rc=0)

    def subscribe(self, topic, qos=0):
        self.subscribed.append(topic)
        return (0, [1])

    def will_set(self, topic, payload, qos=0, retain=False):
        self.will = {"topic": topic, "payload": payload, "qos": qos, "retain": retain}

    def connect(self, host, port, keepalive):
        self.connected = (host, port, keepalive)

    def loop_start(self):
        self.loop_started = True

    def loop_stop(self):
        self.loop_started = False

    def disconnect(self):
        self.connected = None


def _bridge(client, **kw) -> AfMqttBridge:
    return AfMqttBridge(
        client,
        clock=VirtualTimeSource(start=datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc)),
        **kw,
    )


# ── presence=False：不设 LWT、不发 retained status ────────────────
def test_presence_false_sets_neither_will_nor_status():
    client = FakeClient()
    bridge = _bridge(client, presence=False)
    bridge.advertise(caps={"version": "9.9"})
    assert client.will is None, "非 owner 进程不许设 LWT：两路都设会让对端按最后一次写入读在线态"
    assert client.published == [], f"不许发 retained status，实发 {client.published}"

    bridge.start()  # 也不许在 start() 里补发
    assert not [p for p in client.published if p["topic"] == STATUS_TOPIC]


def test_presence_true_still_advertises():
    client = FakeClient()
    bridge = _bridge(client, presence=True)
    bridge.advertise(caps={"version": "9.9"})
    assert client.will is not None and client.will["retain"] is True
    assert [p for p in client.published if p["topic"] == STATUS_TOPIC], "serve 侧必须照旧广播在线态"


def test_degraded_snapshot_not_published_without_presence():
    client = FakeClient()
    bridge = _bridge(client, presence=False)
    bridge.mark_degraded("ADM_ERR_BROKER_UNREACHABLE")
    out = bridge.publish_degraded()
    assert out == {"published": False, "topic": STATUS_TOPIC, "reason": "presence_disabled"}
    assert client.published == []


# ── client_id 后缀：两条连接在 broker 眼里必须是两个客户端 ────────
def test_make_client_appends_suffix(monkeypatch):
    captured: list[str] = []
    fake = FakeClient()

    monkeypatch.setenv("MQTT_CLIENT_ID", "homesdk-autoforge-af")
    monkeypatch.setattr(af_mqtt_bridge, "preflight", lambda **kw: ("h", 1883, 60))
    monkeypatch.setattr(
        af_mqtt_bridge._mqtt,
        "get_client",
        lambda cid, **kw: (captured.append(cid), fake)[1],
    )

    af_mqtt_bridge.make_client(client_id_suffix="watch")
    af_mqtt_bridge.make_client()
    assert captured == ["homesdk-autoforge-af-watch", "homesdk-autoforge-af"]
    assert fake.connected == ("h", 1883, 60) and fake.loop_started


# ── CLI 接线：watch 一路必须带 presence=False + 后缀 + 不订入向 ────
def _wire(monkeypatch, tmp_path):
    seen: dict = {}

    monkeypatch.setattr(af_mqtt_bridge, "env_enabled", lambda: True)
    monkeypatch.setattr(af_mqtt_bridge, "make_linkage_feed", lambda **kw: SimpleNamespace(root=tmp_path))
    monkeypatch.setattr(af_mqtt_bridge, "make_durable_ask_sink", lambda **kw: object())
    monkeypatch.setattr(af_mqtt_bridge, "attach", lambda b: None)

    def fake_start(**kw):
        seen.update(kw)
        return SimpleNamespace(root=tmp_path)

    monkeypatch.setattr(af_mqtt_bridge, "start_from_env", fake_start)
    return seen


def test_cli_watch_role_is_not_identity_owner(monkeypatch, tmp_path):
    seen = _wire(monkeypatch, tmp_path)
    af_cli._start_linkage_bridge(store_root=str(tmp_path), role="watch")
    assert seen["presence"] is False, "watch 不是身份 owner"
    assert seen["client_id_suffix"] == "watch", "两路进程在 broker 眼里必须是两个客户端"
    assert seen["subscribe_insights"] is False and seen["subscribe_linkage"] is False, (
        "入向主题归 serve：watch 再订一份 ⇒ 洞察提案/联动事件双写"
    )


def test_cli_serve_role_keeps_presence(monkeypatch, tmp_path):
    seen = _wire(monkeypatch, tmp_path)
    af_cli._start_linkage_bridge(store_root=str(tmp_path))
    assert seen["presence"] is True
    assert seen["client_id_suffix"] is None
    assert seen["subscribe_insights"] is True and seen["subscribe_linkage"] is True
