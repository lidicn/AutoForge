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
from homesdk.adm.errors import (
    ADM_ERR_BROKER_UNREACHABLE,
    ADM_ERR_INTERNAL,
    ADM_ERR_PAYLOAD_INVALID,
)
from homesdk.adm.status import STATE_DEGRADED, STATE_OFFLINE, STATE_ONLINE, decode_status

from autoforge import af_mqtt_bridge
from autoforge.af_audit import AuditLog
from autoforge.af_conf import ConfidenceStore
from autoforge.af_mqtt_bridge import (
    DEVICE_HEALTH_TOPIC,
    FAILED_TOPIC,
    FIRED_TOPIC,
    INBOX_PREFIX,
    INSIGHT_CONF_CAP,
    INSIGHTS_TOPIC,
    MAX_ERROR_CHARS,
    NO_FAILURE_REASON,
    PRESENCE_CAPS_VERSION,
    PRESENCE_TOPIC,
    STATUS_TOPIC,
    AfMqttBridge,
    BridgeUnavailable,
    TRANSPORT_EVIDENCE_PREVIEW,
    TRANSPORT_PERSON_LIMIT,
    TRANSPORT_TEXT_LIMIT,
    attach,
    caps_payload,
    current_observers,
    detach,
    env_enabled,
    linkage_status,
    preflight,
)
from autoforge.af_instance import InstanceManager
from autoforge.af_ir import load_automation
from autoforge.af_runtime import Runtime
from autoforge.af_state import InMemoryStateProvider
from autoforge.af_time import VirtualTimeSource, load_tz


class FakeClient:
    """鸭子类型的 paho client：只记录调用，不联网。"""

    def __init__(self, *, explode_on_publish: bool = False, fail_on: tuple[str, ...] = ()) -> None:
        self.published: list[dict] = []
        self.subscribed: list[str] = []
        self.will: dict | None = None
        self.on_message = None
        self.explode_on_publish = explode_on_publish
        #: 只让某些主题失败——降级播报必须能在"事件发不出去、status 还发得出去"的窗口里被读到。
        self.fail_on = tuple(fail_on)

    def publish(self, topic, payload, qos=0, retain=False):
        if self.explode_on_publish or topic in self.fail_on:
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
    """homesdk 0.3.2 起 `adm/*/status` 是 JSON 文档，不再是裸字面量。

    字段表从 `homesdk.adm.status` 读，不在这里抄第二份；本条只锁 AF 真正负责的三件事：
    retained 标志、AF 传进去的 `caps.version` 有没有 round-trip 进 status 文档、
    LWT 与显式 offline 是不是同一个编码器产的。
    """
    client = FakeClient()
    _bridge(client).start(caps=caps_payload(tools=["af_draft"], version="2.5"))

    status = [p for p in client.published if p["topic"] == "adm/autoforge/status"]
    assert len(status) == 1 and status[0]["retain"] is True
    # 反空洞：decode_status 认 legacy 字面量，所以光"解得出 online"不足以证明升级发生——载荷必须真是文档
    assert json.loads(status[0]["payload"]), status[0]["payload"]
    st = decode_status(status[0]["payload"])
    assert st["state"] == STATE_ONLINE and st["version"] == "2.5", st
    # LWT：进程被 kill -9 时由 broker 代发 offline，否则对端永远以为 AF 在线
    will = client.will
    assert will["topic"] == "adm/autoforge/status" and will["qos"] == 1 and will["retain"] is True
    assert json.loads(will["payload"]), will["payload"]
    assert decode_status(will["payload"])["state"] == STATE_OFFLINE, will
    caps = json.loads([p for p in client.published if p["topic"] == "adm/autoforge/caps"][0]["payload"])
    assert caps == {"mcp": True, "tools": ["af_draft"], "version": "2.5"}


def test_stop_publishes_retained_offline():
    client = FakeClient()
    bridge = _bridge(client)
    bridge.start()
    bridge.stop()
    last = [p for p in client.published if p["topic"] == "adm/autoforge/status"][-1]
    assert last["retain"] is True
    assert json.loads(last["payload"]), last["payload"]
    assert decode_status(last["payload"])["state"] == STATE_OFFLINE
    assert bridge.started is False


# ── 收尾（第六轮审计 BUG-07）：只连不停 = 每次重连泄漏一条线程 ──────────

class LoopClient(FakeClient):
    """带上 paho 那两张脸的假客户端：`loop_start` 是 `make_client()` 真会起的线程。"""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.loop_started = 0
        self.loop_stopped = 0
        self.disconnected = 0

    def loop_start(self):
        self.loop_started += 1

    def loop_stop(self):
        self.loop_stopped += 1

    def disconnect(self):
        self.disconnected += 1


def test_stop_tears_down_the_paho_network_loop_it_started():
    """`make_client()` 起了 `loop_start()`，收尾就是调用方的责任（homesdk 的 `get_client()` 明确不连、
    不起循环，也不会有库来替我们停）。不回收 = 线程与套接字单调增长，NAS 常驻形态下一周后才显形。"""
    client = LoopClient()
    bridge = _bridge(client)
    bridge.start()
    bridge.stop()
    assert client.loop_stopped == 1, client.loop_stopped
    assert client.disconnected == 1, client.disconnected
    assert client.on_message is None, "已下线的桥不许再挂回调消费消息"


def test_stop_is_idempotent_and_a_second_one_does_nothing():
    client = LoopClient()
    bridge = _bridge(client)
    bridge.start()
    bridge.stop()
    before = len(client.published)
    bridge.stop()
    assert client.loop_stopped == 1 and client.disconnected == 1, client
    assert len(client.published) == before, "重复 stop 不许再发一份 retained offline"


def test_double_start_is_refused_loudly():
    """重复上线会让对端把同一条自动化看到两次在线，而桥这侧的计数分不清——直接拒。"""
    client = LoopClient()
    bridge = _bridge(client)
    bridge.start()
    with pytest.raises(BridgeUnavailable) as exc:
        bridge.start()
    assert "先 `stop()`" in str(exc.value), str(exc.value)
    # 拒了之后不能把桥留在"半新半旧"的形状：状态还是那一个 started，订阅表也没多一份
    assert bridge.started is True
    assert client.subscribed.count(INSIGHTS_TOPIC) == 1, client.subscribed


def test_callback_detach_happens_before_the_loop_is_stopped():
    """摘回调必须排在 `loop_stop()` 之前：断开是异步的，先停循环会留一个还能被投递的窗口。"""
    order: list[str] = []

    class OrderedClient(LoopClient):
        def __setattr__(self, name, value):
            if name == "on_message":
                order.append("detach")
            super().__setattr__(name, value)

        def loop_stop(self):
            order.append("loop_stop")
            super().loop_stop()

        def disconnect(self):
            order.append("disconnect")
            super().disconnect()

    client = OrderedClient()
    bridge = _bridge(client)
    bridge.start()
    assert order[:1] == ["detach"], f"start() 挂回调也算一次赋值，形状要看得见：{order}"
    order.clear()
    bridge.stop()
    assert order == ["detach", "loop_stop", "disconnect"], order



def test_plan_caps_version_is_the_number_the_contract_assigns_to_af():
    """契约 v2.0 §7.1（计划 §六 逐字快照第 201 行）写死「AF 2.6 / MA 1.4 / DB 2.7」。

    这条钉的是**常量的值**：`caps.version` 与 `status.version` 都由它供给（后者由
    `presence.advertise` 从 caps 回退取值），值写错不会有任何其它测试变红——只有对端查账时才看得见。
    生产路径把常量带进 retained 载荷那一跳由 `test_start_from_env_publishes_the_plan_caps_version` 钉。
    """
    assert PRESENCE_CAPS_VERSION == "2.6"


def _status_docs(client):
    return [decode_status(p["payload"]) for p in client.published if p["topic"] == STATUS_TOPIC]


def test_event_publish_failure_flips_retained_status_to_degraded_with_a_code():
    """计划 §六 第 3 项：发不出去事件 ⇒ retained status 转 `degraded` 且 `reasons` 带 `ADM_ERR_*`。

    窗口是"事件这条腿掉了、status 还发得出去"（broker 半死 / 主题被 ACL 拒）。此时最坏的
    读法是 status 仍写着 online——对端会照着"AF 一切正常"去等它不会发出来的 fired。
    """
    client = FakeClient(fail_on=(FIRED_TOPIC,))
    bridge = _bridge(client)
    bridge.start(caps=caps_payload(tools=["af_draft"]))
    assert _status_docs(client)[-1]["state"] == STATE_ONLINE

    out = bridge.publish_fired(automation_id="a-1", instance_id="i-1")
    assert out["published"] is False
    st = _status_docs(client)[-1]
    assert st["state"] == STATE_DEGRADED and st["degraded"] is True, st
    assert st["reasons"] == [ADM_ERR_BROKER_UNREACHABLE], st
    assert st["version"] == "2.6", st
    last = [p for p in client.published if p["topic"] == STATUS_TOPIC][-1]
    assert last["retain"] is True and last["qos"] == 1, last


def test_degraded_status_clears_when_the_transport_returns():
    """降级位不复位＝让对端永远读着一张旧病历。"""
    client = FakeClient(fail_on=(FIRED_TOPIC,))
    bridge = _bridge(client)
    bridge.start(caps=caps_payload(tools=["af_draft"]))
    bridge.publish_fired(automation_id="a-1", instance_id="i-1")
    assert _status_docs(client)[-1]["state"] == STATE_DEGRADED

    client.fail_on = ()
    assert bridge.publish_fired(automation_id="a-1", instance_id="i-1")["published"] is True
    st = _status_docs(client)[-1]
    assert st["state"] == STATE_ONLINE and not st.get("reasons"), st
    assert bridge.degraded == []
    # 复位时 caps 不能丢：对端读到的应当是同一份能力清单，不是"在线了但工具没了"
    caps = json.loads([p for p in client.published if p["topic"] == "adm/autoforge/caps"][-1]["payload"])
    assert caps["tools"] == ["af_draft"], caps


def test_degraded_announcement_that_also_fails_does_not_reach_the_execution_chain():
    """broker 全掉时连降级播报都发不出去——这条必须只计数、不抛（否则自动化跟着陪葬）。"""
    client = FakeClient(explode_on_publish=True)
    bridge = _bridge(client)
    out = bridge.publish_failed(automation_id="a-1", instance_id="i-1", error="boom")
    assert out["published"] is False
    assert bridge.degraded == [ADM_ERR_BROKER_UNREACHABLE]  # 位仍然记着，等传输回来再播报
    assert bridge.counts["publish_errors"] == 2, bridge.counts  # 事件 + 降级播报各一次


def test_inbound_reject_carries_an_adm_err_code():
    """契约 §7.2 纪律：联动失败必须带码，禁止"只记一句不带码的日志"。"""
    bridge = _bridge(FakeClient(), proposal_sink=RecordingSink())
    msg = SimpleNamespace(topic=INSIGHTS_TOPIC, payload=b"{not json")
    out = bridge.handle_message(None, None, msg)
    assert out["handled"] is False and out["code"] == ADM_ERR_PAYLOAD_INVALID, out
    assert bridge.rejected[-1]["code"] == ADM_ERR_PAYLOAD_INVALID, bridge.rejected


def test_af_side_wiring_gap_is_not_blamed_on_the_peer():
    """`no_proposal_sink_wired` 是 AF 自己没接落点，用兜底码——发件方（MA）不该被读成"载荷有问题"。"""
    bridge = _bridge(FakeClient())  # proposal_sink=None
    msg = SimpleNamespace(topic=INSIGHTS_TOPIC, payload=json.dumps({"insight_id": "i-1", "summary": "x"}).encode())
    out = bridge.handle_message(None, None, msg)
    assert out["code"] == ADM_ERR_INTERNAL, out


def test_start_from_env_publishes_the_plan_caps_version(monkeypatch):
    """`caps` 是 retained，`caps.version` 按裁定 20261002 Q7 = 计划号。

    入口工厂的 `version` 默认值以前是空串：`af_cli` 那条生产调用点显式传了号，所以今天是对的，
    但"默认发一个没有版本号的 caps"留在签名里——retained 意味着这条空值会一直挂在 broker 上，
    只有对端查账时才看得见。默认值改成计划号本身，漏传就不可能变成静默空值。
    """
    client = FakeClient()
    monkeypatch.setenv("AUTOFORGE_MQTT", "1")
    monkeypatch.setattr("autoforge.af_mqtt_bridge.make_client", lambda **kw: client)

    bridge = af_mqtt_bridge.start_from_env(tools=["af_draft"])
    assert bridge is not None, "AUTOFORGE_MQTT=1 时必须真起桥"

    record = [p for p in client.published if p["topic"] == "adm/autoforge/caps"][0]
    assert record["retain"] is True
    caps = json.loads(record["payload"]) if isinstance(record["payload"], str) else record["payload"]
    assert caps == {"mcp": True, "tools": ["af_draft"], "version": PRESENCE_CAPS_VERSION}


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
    assert body["trace_id"], "事件级关联锚点（不承担跨仓串联）"


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


def test_observer_path_key_set_equals_the_contract_row():
    """生产唯一发事件的路径是 `observe_terminal()`，它的键集合必须**就是**契约表 §1.2 那一行。

    这条原先钉的是"比直接调用多发一个 `node_id`"：那个键没进过契约行、DB 也从不读，且它是
    "失败时刻实例停在哪"而不是"哪个节点失败"（done 与 failed 两条路径不必相等）——
    裁定 20261004 §一 2 判**删**。用真状态机产出的实例（它带着 `current_node_id`）把键集合钉死：
    多发的字段要显式记账，不许留成"恰好发出去了"。
    """
    client = FakeClient()
    bridge = _bridge(client)
    inst = _failed_inst("d2 失败且无 on_error/default 兜底")
    assert inst.current_node_id, "对照组：实例确实带着一个节点号，才证得住桥没把它多发出去"
    bridge.observe_terminal(inst, "done")
    bridge.observe_terminal(inst, "failed")
    fired, failed = (_body(p) for p in client.published)

    assert set(fired) == {"trace_id", "ts", "automation_id", "ref", "instance_id"}
    assert set(failed) == set(fired) | {"error"}


def test_trace_id_is_event_level_not_a_chain_id():
    """`trace_id` 是事件级（裁定 20261004 §一 2 C）：同一次部署的 fired 与 failed 不同号。

    把它"修"成一条链路共用一枚号，下游"按 trace_id 拉一屏日志"的语义会从 1:1 静默变 1:N；
    因果链的把手是 `ref`（实例级）与发布者自己的存储元数据。
    """
    client = FakeClient()
    bridge = _bridge(client)
    inst = _failed_inst()
    bridge.observe_terminal(inst, "done")
    bridge.observe_terminal(inst, "failed")
    fired, failed = (_body(p) for p in client.published)

    assert fired["trace_id"] != failed["trace_id"]
    assert fired["ref"] == failed["ref"] == inst.instance_id


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


MINIMAL_AUTO = {
    "ir_version": "0.2.1", "id": "demo", "name": "示例", "version": 1, "mode": "single",
    "nodes": [
        {"id": "a1", "kind": "on",
         "trigger": {"type": "state", "entity_id": "binary_sensor.motion", "to": "on"}},
        {"id": "p1", "kind": "pass"},
    ],
    "edges": [{"from": "a1", "to": "p1", "kind": "then"}],
}


def _failed_inst(reason: str = ""):
    """真状态机产出的失败实例：`fail_reason` 由 `InstanceManager.fail()` 写，桥只负责读出来。"""
    manager = InstanceManager(InMemoryStateProvider())
    inst = manager.spawn(load_automation(MINIMAL_AUTO))
    manager.fail(inst, reason)
    return inst


def test_failed_event_carries_the_reason_the_state_machine_recorded():
    """契约表 §1.2 把 `error` 定为失败原因，DB 拿它向用户解释"为什么失败"。

    锁的是**跨模块字段名**：桥读的是 `ctx.context["fail_reason"]`，写它的是 `af_instance.InstanceManager.fail()`。
    两边任何一侧改键名，这条就红——而不是让线上每条失败事件静默退化成占位句。
    """
    client = FakeClient()
    bridge = _bridge(client)
    bridge.observe_terminal(_failed_inst("light.turn_on 失败且无兜底：HA 返回 502"), "failed")

    assert _body(client.published[-1])["error"] == "light.turn_on 失败且无兜底：HA 返回 502"


def test_failed_event_error_is_never_the_state_name():
    """执行链没留原因时诚实说"没记录"。恒等于 `"failed"` 的字段携带零信息，而它正是 DB 唯一能显示的东西。"""
    for inst in (
        _failed_inst(),                                                      # 真状态机、空原因
        _failed_inst("   "),                                                 # 键在、只有空白
        SimpleNamespace(automation=SimpleNamespace(id="a"), instance_id="i"),  # 没有 ctx 的鸭子类型
    ):
        client = FakeClient()
        _bridge(client).observe_terminal(inst, "failed")
        error = _body(client.published[-1])["error"]
        assert error == NO_FAILURE_REASON
        assert error != "failed"


def test_failed_event_error_is_bounded():
    """原因里会拼异常 repr 与真机回执；封顶用契约表给展示类文本定过的同一个数（§1.3 = 500）。"""
    client = FakeClient()
    _bridge(client).observe_terminal(_failed_inst("很长的回执" * 400), "failed")
    assert len(_body(client.published[-1])["error"]) <= MAX_ERROR_CHARS


def test_publish_error_is_recorded_not_raised():
    """broker 抖动不许把自动化执行链带崩，但必须留下可见的账。

    一次事件失败会顺带触发 degraded 快照播报（契约 §7.3 degrade-flag 档），broker 全掉时那条也发不出去
    ⇒ 同一次故障记 2 次写失败。计数从 1 抬到 2 是这次改动的**读数变化**，不是把判据放宽。
    """
    bridge = _bridge(FakeClient(explode_on_publish=True))
    result = bridge.publish_fired(automation_id="auto_a", instance_id="i1")
    assert result["published"] is False
    assert bridge.counts["publish_errors"] == 2
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
    ({"natural_language": "缺了假设号", "conf": 0.9}, "missing_insight_id"),
    ({"trace_id": "   ", "summary": "两边都是空串"}, "missing_insight_id"),
    ({"hypothesis_id": "h", "conf": 0.9}, "missing_natural_language_and_intent"),
    ({"hypothesis_id": "h", "natural_language": "x", "conf": 1.7}, "conf_out_of_range"),
    ({"hypothesis_id": "h", "natural_language": "x", "conf": -0.1}, "conf_out_of_range"),
    ({"hypothesis_id": "h", "natural_language": "x", "conf": True}, "conf_out_of_range"),
    ({"hypothesis_id": "h", "natural_language": "x", "conf": "0.9"}, "conf_out_of_range"),
    ({"trace_id": "t", "summary": "契约形状", "confidence": 2}, "conf_out_of_range"),
])
def test_malformed_insight_is_rejected_not_invented(payload, reason):
    sink = RecordingSink()
    bridge = _bridge(proposal_sink=sink)
    result = bridge.handle_message(None, None, SimpleNamespace(topic=INSIGHTS_TOPIC, payload=json.dumps(payload).encode()))
    assert result["handled"] is False and result["reason"] == reason
    assert sink.calls == []
    assert bridge.counts["insights_rejected"] == 1


#: 契约表 `ma/insights` 那一行的**逐字形状**（裁定 20261002 Q3）。AF 早年自造的键不在这里——
#: 如果桥只认自家键，每一条按契约发来的洞察都会被丢掉，而且 broker 两侧都不报错（判例 1：静默归零）。
CONTRACT_INSIGHT = {
    "trace_id": "t-20261002-0001",
    "ts": "2026-10-02T17:36:27",
    "kind": "behavior.insight",
    "persons": ["爸爸", "妈妈"],
    "room": "客厅",
    "summary": "两人每晚 21:40 一起在客厅开大灯",
    "evidence": [{"at": "21:40", "entity": "light.living_room"}, {"at": "21:41", "entity": "media_player.tv"}],
    "snapshot_url": "http://ma.internal/snap/1.jpg",
}


def _ingest(sink: RecordingSink, payload: dict) -> dict:
    bridge = _bridge(proposal_sink=sink)
    return bridge.handle_message(None, None, SimpleNamespace(
        topic=INSIGHTS_TOPIC, payload=json.dumps(payload).encode()))


def test_contract_shaped_insight_lands_instead_of_being_dropped():
    """按契约表原样发来的洞察必须进审批队列，不是被"缺 hypothesis_id"拒掉。"""
    sink = RecordingSink()
    result = _ingest(sink, CONTRACT_INSIGHT)
    assert result["handled"] is True, result
    assert len(sink.calls) == 1
    call = sink.calls[0]
    assert call["hypothesis_id"] == "t-20261002-0001"      # 没给 hypothesis_id 时用 trace_id
    assert call["natural_language"] == CONTRACT_INSIGHT["summary"]
    assert call["source"] == "ma"
    transport = call["transport"]
    assert transport["id_key"] == "trace_id"
    assert transport["kind"] == "behavior.insight"
    assert transport["persons"] == ["爸爸", "妈妈"]         # 多人同框是现实，单数装不下
    assert transport["room"] == "客厅"
    assert transport["evidence_count"] == 2
    assert transport["ts"] == "2026-10-02T17:36:27"        # 家庭墙钟 ISO，原样留住
    assert transport["has_snapshot"] is True
    assert "snapshot_url" not in transport                 # 留住"有截图"，不留住 URL


def test_insight_id_is_the_stable_identity_when_present():
    """裁定 20261004 §五 Q2：`insight_id` 才是稳定身份，`trace_id` 只做追踪号。

    MA 一旦补发 `insight_id`，去重与回灌键必须切过去；而代位（只有 trace_id 时）这件事
    要留在 `transport.id_key` 上——否则"用追踪号去重"这条临时口径会看不见了。
    """
    sink = RecordingSink()
    result = _ingest(sink, {**CONTRACT_INSIGHT, "insight_id": "ins-0001"})
    assert result["handled"] is True, result
    call = sink.calls[0]
    assert call["hypothesis_id"] == "ins-0001"
    assert call["transport"]["id_key"] == "insight_id"


def test_insight_id_beats_the_legacy_alias():
    sink = RecordingSink()
    _ingest(sink, {**CONTRACT_INSIGHT, "insight_id": "ins-0002", "hypothesis_id": "hyp-old"})
    assert sink.calls[0]["hypothesis_id"] == "ins-0002"
    assert sink.calls[0]["transport"]["id_key"] == "insight_id"


def test_trace_id_only_still_enters_but_is_marked_as_proxy():
    """对照组：代位这条路不能因为新键上线就被关掉（MA 补发之前它是唯一入口）。"""
    sink = RecordingSink()
    result = _ingest(sink, CONTRACT_INSIGHT)
    assert result["handled"] is True
    assert sink.calls[0]["transport"]["id_key"] == "trace_id"


def test_absent_conf_is_accounted_as_unreported_not_as_zero_confidence():
    """契约里**没有 conf 这一项**。缺报要按 ask 档收（0.0），但必须记成"MA 没报"，
    不能让批的人在面板上把对端的沉默读成对端的否定。"""
    without = RecordingSink()
    result = _ingest(without, CONTRACT_INSIGHT)
    assert result["conf_reported"] is False
    assert without.calls[0]["conf"] == 0.0
    assert without.calls[0]["transport"]["conf_reported"] is False

    with_conf = RecordingSink()
    payload = {**CONTRACT_INSIGHT, "conf": 0.93}
    result = _ingest(with_conf, payload)
    assert result["conf_reported"] is True
    assert with_conf.calls[0]["conf"] == INSIGHT_CONF_CAP  # 报了也照旧封顶
    assert with_conf.calls[0]["transport"]["conf_reported"] is True


def test_explicit_null_conf_is_the_same_absence_as_a_missing_key():
    """`{"conf": null}` 与根本不发 `conf` 是同一件事：MA 明说"没有这个数"。

    把它当坏报拒收，等于让"用 null 占位"的实现把每一条洞察丢掉——还是判例 1 的静默归零，
    只是这回动手的是 AF。同时 `conf` 为空时 `confidence` 别名要能顶上来（不是被 null 遮住）。
    """
    null_only = RecordingSink()
    result = _ingest(null_only, {**CONTRACT_INSIGHT, "conf": None})
    assert result["handled"] is True and result["conf_reported"] is False
    assert null_only.calls[0]["conf"] == 0.0
    assert null_only.calls[0]["transport"]["conf_reported"] is False

    aliased = RecordingSink()
    result = _ingest(aliased, {"trace_id": "t-alias", "summary": "x", "conf": None, "confidence": 0.8})
    assert result["conf_reported"] is True
    assert aliased.calls[0]["conf"] == INSIGHT_CONF_CAP      # 报了 0.8 就按 0.8 走，只是照样封顶

    bad_type = RecordingSink()
    assert _ingest(bad_type, {"trace_id": "t", "summary": "x", "conf": [0.5]})["reason"] == "conf_out_of_range"
    assert bad_type.calls == []                               # 报了却不是数：坏报，不是缺报


def test_legacy_af_keys_remain_a_supported_alias():
    """MA 承诺旧键保留到 DB/AF 迁完；两边都能进来，用的是哪个键记在 transport 里。"""
    sink = RecordingSink()
    result = _ingest(sink, {"hypothesis_id": "h-legacy", "natural_language": "天黑关廊灯", "conf": 0.4})
    assert result["handled"] is True
    assert sink.calls[0]["hypothesis_id"] == "h-legacy"
    assert sink.calls[0]["transport"]["id_key"] == "hypothesis_id"


def test_transport_accounting_is_bounded_against_a_loud_peer():
    """载荷来自对端：persons/evidence/文本长度都有上界，不然一条消息就能撑爆落盘记录。"""
    sink = RecordingSink()
    payload = {
        "trace_id": "t-fat",
        "summary": "x",
        "persons": [f"person-{i}" * 200 for i in range(500)],
        "evidence": [{"n": i} for i in range(500)],
        "room": "r" * 5000,
    }
    assert _ingest(sink, payload)["handled"] is True
    transport = sink.calls[0]["transport"]
    assert len(transport["persons"]) == TRANSPORT_PERSON_LIMIT
    assert all(len(p) <= TRANSPORT_TEXT_LIMIT for p in transport["persons"])
    assert transport["evidence_count"] == 500               # 计数不封顶，封顶的是预览
    assert len(transport["evidence_preview"]) == TRANSPORT_EVIDENCE_PREVIEW
    assert len(transport["room"]) <= TRANSPORT_TEXT_LIMIT


def test_undecodable_payload_and_foreign_topics_are_dropped():
    bridge = _bridge(proposal_sink=RecordingSink())
    bad = bridge.handle_message(None, None, SimpleNamespace(topic=INSIGHTS_TOPIC, payload=b"{not json"))
    assert bad["handled"] is False and bad["reason"] == "undecodable_payload"

    other = bridge.handle_message(None, None, SimpleNamespace(topic="butler/trigger/living", payload=b"{}"))
    assert other["handled"] is False and other["reason"] == "topic_not_mine"


def test_inbox_topics_are_never_subscribed_and_never_handled():
    """收件箱归 DB。AF 既不去订，也不替 DB 处理。

    卡3 起订阅表是三条（洞察 + 在场 + 设备健康），所以这里锁的不再是"只订了一条"，
    而是**inbox 族一条都不许出现**——那才是角色边界。族外主题的数量另行钉死，
    少订/多订都会红。
    """
    client = FakeClient()
    bridge = _bridge(client, proposal_sink=RecordingSink())
    bridge.start()
    assert client.subscribed == [INSIGHTS_TOPIC, PRESENCE_TOPIC, DEVICE_HEALTH_TOPIC]

    for topic in ("butler/inbox/speak", "butler/inbox/notify", "butler/inbox/tv"):
        assert bridge.subscribe_topic(topic) is False
    assert client.subscribed == [INSIGHTS_TOPIC, PRESENCE_TOPIC, DEVICE_HEALTH_TOPIC]
    assert not [t for t in client.subscribed if t.startswith(INBOX_PREFIX)], client.subscribed
    dropped = bridge.handle_message(None, None, SimpleNamespace(topic="butler/inbox/speak", payload=b"{}"))
    assert dropped["handled"] is False
    assert bridge.counts["forbidden_seen"] == 4


def test_linkage_subscriptions_can_be_turned_off():
    """`subscribe_linkage=False` 那条腿也要有人按：整条入向线一次都不订。"""
    client = FakeClient()
    bridge = _bridge(client, subscribe_linkage=False)
    bridge.start()
    assert client.subscribed == [INSIGHTS_TOPIC]
    assert linkage_status(bridge)["inbound"]["subscribed"] == []


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

