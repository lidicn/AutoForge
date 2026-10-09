"""收件箱投递（计划 §七 卡1，契约 §1.3/§1.5）——上线语义与三道 fail-closed 的锁。

本机没有 paho、没有 broker，所以照 `test_af_mqtt_bridge.py` 的做法注入鸭子类型 client：
锁的是**送达语义**（主题、QoS、retain、trace_id 口径、失败必带码、失败必留痕），
不是"能不能连上某个 IP"。`mosquitto_sub` 那一半属 NAS 合并窗（见执行记录 未收 项）。

本文件全部用显式 `bridge_provider=` 取桥，不靠 `current_bridge()`：`_BRIDGES` 是模块级全局，
同一次 pytest 会话里别的测试 attach 过的桥会让"没桥"这一档变得不确定。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Mapping

import pytest
from homesdk.adm.errors import (
    ADM_ERR_AUTH_REQUIRED,
    ADM_ERR_BROKER_UNREACHABLE,
    ADM_ERR_PAYLOAD_INVALID,
)
from homesdk.adm.status import STATE_DEGRADED, STATE_ONLINE, decode_status
from homesdk.presence import INBOX_MAX_BODY, INBOX_MAX_TEXT, INBOX_MAX_TITLE

from autoforge.af_adapters.inbox import INTENTS_MAX, InboxAdapter, kind_of
from autoforge.af_mqtt_bridge import (
    INBOX_PREFIX,
    PublishRefused,
    AfMqttBridge,
    caps_payload,
    inbox_publish,
)
from autoforge.af_time import VirtualTimeSource

STATUS_TOPIC = "adm/autoforge/status"
CAPS_TOPIC = "adm/autoforge/caps"


class Wire:
    """鸭子类型的 paho client：只记录**真上线**的字节。

    `rc≠0` 时不记录——这是 paho 的真实形状（未连接时 `publish()` 既不排队也不抛，
    只回 `MQTT_ERR_NO_CONN`）。本批修掉的 bug 正是"把这个返回值丢了"，所以这里
    必须让"没上线"和"回了非零 rc"在假件里也是同一件事，否则测试会跟着假绿。
    """

    def __init__(
        self,
        *,
        rc_for: Mapping[str, int] | None = None,
        default_rc: int = 0,
        raise_on: tuple[str, ...] = (),
    ) -> None:
        self.rc_for = dict(rc_for or {})
        self.default_rc = default_rc
        self.raise_on = tuple(raise_on)
        self.published: list[dict[str, Any]] = []
        self.subscribed: list[str] = []
        self.will: dict | None = None

    def publish(self, topic, payload, qos=0, retain=False):
        rc = self.rc_for.get(topic, self.default_rc)
        if topic in self.raise_on:
            raise OSError("broker 掉了")
        if rc:
            return SimpleNamespace(rc=rc)
        self.published.append({"topic": topic, "payload": payload, "qos": qos, "retain": retain})
        return SimpleNamespace(rc=0)

    def subscribe(self, topic, qos=0):
        self.subscribed.append(topic)
        return (0, [1])

    def will_set(self, topic, payload, qos=0, retain=False):
        self.will = {"topic": topic, "payload": payload, "qos": qos, "retain": retain}

    def topics(self) -> list[str]:
        return [p["topic"] for p in self.published]


def _bridge(client: Wire | None = None, **kw) -> AfMqttBridge:
    return AfMqttBridge(
        client or Wire(),
        clock=VirtualTimeSource(start=datetime(2026, 10, 2, 8, 0, tzinfo=timezone.utc)),
        **kw,
    )


def _adapter(client: Wire | None = None, *, dry_run: bool = False, with_bridge: bool = True) -> InboxAdapter:
    wire = client or Wire()
    bridge = _bridge(wire) if with_bridge else None
    return InboxAdapter(dry_run=dry_run, bridge_provider=lambda: bridge)


# ── dry_run：有证据价值的预演，一个字节都不上线 ────────────────────────
def test_dry_run_records_the_intent_and_writes_nothing_to_the_wire():
    """dry_run 即使手里有桥也必须不上线。

    这条判据的形状是"注入桥 + dry_run"：如果实现按"有桥就发"分支，
    预演会真的让音箱播一句话——用户视角的"只是看看效果"变成副作用。
    """
    wire = Wire()
    adapter = InboxAdapter(dry_run=True, bridge_provider=lambda: _bridge(wire))

    result = adapter.call("inbox.speak", {"text": "昨晚卧室空调开机 3.2 小时"})

    assert result.success is True, result.error
    assert result.data["dry_run"] is True and result.data["topic"] == "butler/inbox/speak"
    assert result.data["payload"]["text"] == "昨晚卧室空调开机 3.2 小时"
    assert wire.published == []
    assert adapter.intents == [
        {"topic": "butler/inbox/speak", "trace_id": result.data["trace_id"], "payload": result.data["payload"]}
    ]


def test_dry_run_needs_no_bridge():
    """通道侧那道门只管真发：预演走记录代理，没桥也能看到载荷。"""
    out = inbox_publish("notify", fields={"title": "水浸", "body": "厨房"}, bridge=None, dry_run=True)
    assert out["dry_run"] is True and out["published"] is False
    assert out["payload"] == {
        "trace_id": out["trace_id"],
        "ts": out["payload"]["ts"],
        "title": "水浸",
        "body": "厨房",
    }


def test_dry_run_shows_the_exact_bytes_that_would_go_on_the_wire():
    """预演载荷必须**来自上线那份 body**，不是 AF 重拼的第二份。

    两档各跑一次同样的字段：键集合与非 `trace_id`/`ts` 的值必须一致。做不到这一点
    （比如展示侧自己 `{"text": ...}` 手搓）就是"预演说播这句、音箱收到空字符串"的形状。
    """
    fields = {"text": "早安", "priority": 5, "role": "butler"}
    dry = inbox_publish("speak", fields=fields, dry_run=True)
    real = inbox_publish("speak", fields=fields, bridge=_bridge(Wire()))

    assert json.loads(dry["body"]) == dry["payload"], dry["body"]
    assert set(dry["payload"]) == set(real["payload"]) == {"trace_id", "ts", "text", "role", "priority"}
    assert {k: v for k, v in dry["payload"].items() if k not in ("trace_id", "ts")} == {
        k: v for k, v in real["payload"].items() if k not in ("trace_id", "ts")
    }
    # 契约 §四 的登记例外：inbox 的 ts 是 epoch 整数（库侧定的），不是家庭墙钟 ISO
    assert isinstance(real["payload"]["ts"], int)


# ── 上线形态：主题 / QoS / retain ──────────────────────────────────────
def test_real_send_lands_on_contract_topic_qos_and_no_retain():
    wire = Wire()
    out = _bridge(wire).publish_inbox("speak", fields={"text": "早安", "priority": 5})

    assert out["published"] is True and out["topic"] == INBOX_PREFIX + "speak"
    assert wire.published == [
        {
            "topic": "butler/inbox/speak",
            "payload": wire.published[0]["payload"],
            "qos": 1,  # 契约 §1.3 钉 QoS 1
            "retain": False,  # 播报不能重放：新订阅者补一个 retained 的"早安"是故障不是功能
        }
    ]
    assert json.loads(wire.published[0]["payload"])["priority"] == 5


def _fields_for(kind: str) -> dict[str, Any]:
    return {
        "speak": {"text": "该喝水了"},
        "notify": {"title": "水浸", "body": "厨房"},
        "tv": {"content": "门铃"},
    }[kind]


@pytest.mark.parametrize(
    "action,expected_topic",
    [("inbox.speak", "butler/inbox/speak"), ("notify", "butler/inbox/notify"), ("INBOX.TV", "butler/inbox/tv")],
)
def test_three_kinds_and_two_spellings_reach_their_own_topic(action, expected_topic):
    """`inbox.speak` 与 `speak` 两种写法归一；三种动作各走自己的主题。"""
    wire = Wire()
    adapter = _adapter(wire)
    result = adapter.call(action, _fields_for(kind_of(action)))
    assert result.success is True, result.error
    assert result.data["topic"] == expected_topic
    assert wire.topics().count(expected_topic) == 1


# ── fail-closed 第 1 道：载荷侧 ────────────────────────────────────────
@pytest.mark.parametrize(
    "kind,fields,needle",
    [
        ("shout", {"text": "x"}, "未知收件箱动作"),
        ("speak", {}, "缺必填字段"),
        ("notify", {"title": "只有标题"}, "缺必填字段"),
        ("speak", {"tet": "拼错的键"}, "不接受这些键"),
        # 契约 §1.3 明确 inbox 载荷**没有 source 字段**；多给一个就必须拒，不能静默丢掉
        ("speak", {"text": "x", "source": "autoforge"}, "不接受这些键"),
        # 机制层入参不开放给 IR 作者：trace_id 由 AF 每次现生成，qos 由契约钉死
        ("speak", {"text": "x", "trace_id": "伪造一枚"}, "不接受这些键"),
        ("speak", {"text": "x", "qos": 0}, "不接受这些键"),
        # 非字符串 / 超长都由库侧先校验后 publish，所以线上一字节都没出
        ("speak", {"text": 123}, "TypeError"),
        ("speak", {"text": "长" * (INBOX_MAX_TEXT + 1)}, "ValueError"),
        ("notify", {"title": "长" * (INBOX_MAX_TITLE + 1), "body": "ok"}, "ValueError"),
        ("notify", {"title": "ok", "body": "长" * (INBOX_MAX_BODY + 1)}, "ValueError"),
    ],
)
def test_payload_side_refusal_carries_a_code_and_sends_nothing(kind, fields, needle):
    wire = Wire()
    out = inbox_publish(kind, fields=fields, bridge=_bridge(wire))

    assert out["published"] is False and out["code"] == ADM_ERR_PAYLOAD_INVALID, out
    assert needle in out["error"], out["error"]
    assert wire.published == []
    # 载荷侧拒绝不是传输故障：不该把 retained status 翻成 degraded，也不该计 publish_errors
    assert wire.topics() == []


def test_adapter_maps_payload_refusal_to_failed_callresult():
    """适配器不许把"没送出去"报成 done——失败要走 IR 的 `on_error`，无则实例 failed。"""
    result = _adapter(Wire()).call("inbox.shout", {"text": "x"})
    assert result.success is False
    assert result.data["code"] == ADM_ERR_PAYLOAD_INVALID
    assert result.data["topic"] == "butler/inbox/shout"
    assert result.error


# ── fail-closed 第 2 道：通道侧（计划验收点名的"缺凭据拒发"）───────────
def test_missing_bridge_refuses_with_auth_required():
    wire = Wire()
    out = inbox_publish("speak", fields={"text": "早安"}, bridge=None, dry_run=False)
    assert out["published"] is False and out["code"] == ADM_ERR_AUTH_REQUIRED
    assert "AUTOFORGE_MQTT" in out["error"]
    assert re.fullmatch(r"[0-9a-f]{12}", out["trace_id"]), out

    adapter = InboxAdapter(dry_run=False, bridge_provider=lambda: None)
    result = adapter.call("inbox.speak", {"text": "早安"})
    assert result.success is False and result.data["code"] == ADM_ERR_AUTH_REQUIRED
    assert wire.published == []


# ── fail-closed 第 3 道：传输侧（本批修掉的"rc 被丢弃"）────────────────
def test_publish_rc_nonzero_is_not_reported_as_sent():
    """paho 未连接时不抛异常，只回 `rc≠0`。

    修法前这条会被报成"已投递"：`published=True`、degraded 不亮、`publish_errors` 不加，
    而音箱一句都没播。这正是契约 §7.3 要点名消灭的静默失败，所以三处读数都得变。
    """
    wire = Wire(rc_for={"butler/inbox/speak": 2})
    bridge = _bridge(wire)

    out = bridge.publish_inbox("speak", fields={"text": "早安"})

    assert out["published"] is False and out["code"] == ADM_ERR_BROKER_UNREACHABLE, out
    assert "rc=2" in out["error"], out["error"]
    assert bridge.degraded == [ADM_ERR_BROKER_UNREACHABLE]
    assert bridge.counts["publish_errors"] == 1
    # 线上只剩那张降级快照：事件本身一个字节都没出去（paho 未连接时不排队）
    assert wire.topics() == [STATUS_TOPIC], wire.topics()


def test_degraded_status_carries_the_code_when_inbox_is_refused():
    """投递被拒必须让对端**看得见**：retained status 转 degraded + reasons 带码（契约 §7.1/§7.2）。"""
    wire = Wire(rc_for={"butler/inbox/tv": 4})
    _bridge(wire).publish_inbox("tv", fields={"content": "门铃"})

    statuses = [p for p in wire.published if p["topic"] == STATUS_TOPIC]
    assert len(statuses) == 1, wire.topics()
    st = decode_status(statuses[0]["payload"])
    assert st["state"] == STATE_DEGRADED, st
    assert ADM_ERR_BROKER_UNREACHABLE in (st.get("reasons") or []), st
    assert statuses[0]["retain"] is True and statuses[0]["qos"] == 1


def test_recovered_delivery_clears_degraded_and_readvertises_caps():
    """传输回来之后：清降级位 + 重发同一份 caps，否则对端读到"在线但 caps 空了"。"""
    wire = Wire(rc_for={"butler/inbox/speak": 2})
    bridge = _bridge(wire)
    bridge.start(caps=caps_payload(tools=["af_inbox"], version="2.6"))

    assert bridge.publish_inbox("speak", fields={"text": "早安"})["published"] is False
    assert bridge.degraded == [ADM_ERR_BROKER_UNREACHABLE]

    wire.rc_for = {}  # 传输恢复
    out = bridge.publish_inbox("speak", fields={"text": "早安"})

    assert out["published"] is True
    assert bridge.degraded == []
    last_status = [p for p in wire.published if p["topic"] == STATUS_TOPIC][-1]
    assert decode_status(last_status["payload"])["state"] == STATE_ONLINE, last_status
    caps = [p for p in wire.published if p["topic"] == CAPS_TOPIC]
    assert json.loads(caps[-1]["payload"])["tools"] == ["af_inbox"], caps[-1]


def test_transport_exception_and_refused_share_one_accounting():
    """抛异常与 rc≠0 是同一类故障，记账必须同码同计数（分两套就会各自决定要不要标降级）。"""
    raised = _bridge(Wire(raise_on=("butler/inbox/speak",)))
    refused = _bridge(Wire(rc_for={"butler/inbox/speak": 14}))

    a = raised.publish_inbox("speak", fields={"text": "x"})
    b = refused.publish_inbox("speak", fields={"text": "x"})

    assert a["code"] == b["code"] == ADM_ERR_BROKER_UNREACHABLE
    assert raised.degraded == refused.degraded == [ADM_ERR_BROKER_UNREACHABLE]
    assert raised.counts["publish_errors"] == refused.counts["publish_errors"] == 1
    assert PublishRefused.__name__ in b["error"] or "rc=14" in b["error"], b


# ── trace_id：事件级、每次现造（裁定 20261004 §一 2）───────────────────
def test_trace_id_is_minted_per_event_not_per_deployment():
    seen = [inbox_publish("speak", fields={"text": f"第 {i} 句"}, dry_run=True)["trace_id"] for i in range(3)]
    assert len(set(seen)) == 3, seen
    for tid in seen:
        assert re.fullmatch(r"[0-9a-f]{12}", tid), tid


def test_success_and_failure_both_return_the_trace_id():
    """失败也带 trace_id：对端拿它去查那条审计，缺了就只能按时间猜。"""
    ok = inbox_publish("speak", fields={"text": "x"}, bridge=_bridge(Wire()))
    bad = inbox_publish("speak", fields={"text": "x"}, bridge=_bridge(Wire(rc_for={"butler/inbox/speak": 2})))
    assert ok["trace_id"] and bad["trace_id"]


# ── 适配器自身的两条纪律 ──────────────────────────────────────────────
def test_intents_ring_is_capped():
    """常驻服务里每次 dry_run 都记一条，不封顶就是只增不减（稳定性审计 BUG-01 同族）。"""
    adapter = InboxAdapter(dry_run=True, bridge_provider=lambda: None)
    for i in range(INTENTS_MAX + 25):
        adapter.call("inbox.speak", {"text": f"第 {i} 句"})

    assert len(adapter.intents) == INTENTS_MAX
    assert adapter.intents[-1]["payload"]["text"] == f"第 {INTENTS_MAX + 24} 句"
    assert adapter.intents[0]["payload"]["text"] == "第 25 句"  # 裁头不裁尾


def test_bridge_is_reread_on_every_call():
    """`serve` 的顺序是 build_app → 起桥：装配期抓引用会永远是 None。"""
    taken: list[Any] = [None]
    adapter = InboxAdapter(dry_run=False, bridge_provider=lambda: taken[0])

    assert adapter.call("inbox.speak", {"text": "早安"}).data["code"] == ADM_ERR_AUTH_REQUIRED

    wire = Wire()
    taken[0] = _bridge(wire)
    assert adapter.call("inbox.speak", {"text": "早安"}).success is True
    assert wire.topics().count("butler/inbox/speak") == 1
