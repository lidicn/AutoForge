# -*- coding: utf-8 -*-
"""AF-776 判据 · ask 的 speak 腿全撤（2026-11-13，出资人体感「一次询问念两遍」）。

**现场**：18:49 trace 里 DB 收到 `INBOX_DONE(speak, source=autoforge)` 两条——一问句一超时作废；
其中「问句」那条与 `af/ask/pending` 是同一句话的两条腿：

```
ask 挂起 ─┬─ af/ask/pending ──▶ DB butler/bus/af_ask.py ──▶ TTS 一次   ← 保留（唯一出声腿）
          └─ butler/inbox/speak ──▶ DB 收件箱播报 ──▶ TTS 再一次      ← AF-776 撤掉
```

DB 侧现读（`E:\\NAS\\doubao-butler\\butler\\bus\\af_ask.py`，只读）确认两件事：
1. 收到 `af/ask/pending` 后 `_speak()` 自己念题 ⇒ **AF 再发 speak 就是第二遍**；
2. 超时无回答时 DB **什么都不发**（源码原话：「用户没回答：什么都不发，AF 侧超时兜底，本条问句作废」）
   ⇒ 那声「本次询问作废」在 AF 侧必须留着。

**授权例外**（AF-PM 2026-11-13 派卡 §①）：DB 无自有超时播报 ⇒ 仅保留 AF 的「超时作废」一条 speak。
它的载体是 IR 的 `pto.announce`，走 **emit 腿**（数据，不是代码）——所以本笔撤代码里的问句腿，
emit 腿一行不动。

**判据**（每格都带反证腿）：

- A1 一次 ask ⇒ MQTT 记录器**只有 1 条**，topic = `af/ask/pending`，⛔ 无 `butler/inbox/speak`
- A2 ask 核心不变：仍挂起、`pending_asks` 建齐、pending 载荷逐字（C3＋C6）
- B1 例外腿在：超时到点后「本次询问作废」**必须仍播**（IR 的 `pto.announce` → emit 腿）
- C1 `af_db_ask_test` v4 真实形状回归：派发 1 条、pending 1 条、speak 0 条
- D1 源码钉：⛔ 不许把 ask 的 speak 腿挂回去（含"留个空钩子"）
"""

from __future__ import annotations

from typing import Any

import pytest

from autoforge import af_mqtt_bridge as mqtt
from autoforge.af_ask_bridge import ASK_PENDING_TOPIC
from autoforge.af_bus import BusEvent
from autoforge.af_ir import Graph, load_automation
from autoforge.af_runtime import build_runtime

ROOM = "zhu_wo_shi"
SPEAK_TOPIC = "butler/inbox/speak"
OCC = "binary_sensor.occupancy_main_bedroom"
LIGHT = "switch.light_main_bedroom"
PURIFIER = "switch.purifier_main_bedroom"
ASK_TIMEOUT_S = 60


#: 与线上 `af_ask_purifier` 同形：`on(人在) → if(灯开∧净化器关) → ask(60s) → …`；
#: `pto` 上就是那句「没有收到回答，本次询问作废」（线上 IR 现读，逐字）。
AUTO: dict[str, Any] = {
    "ir_version": "0.2.1", "id": "af776_ask_speak", "name": "AF776 问句只走 pending",
    "version": 1, "mode": "single",
    "nodes": [
        {"id": "t1", "kind": "on", "name": "人在",
         "trigger": {"type": "state", "entity_id": OCC, "to": "on"}},
        {"id": "i1", "kind": "if", "name": "灯开且净化器关",
         "expr": {"op": "and", "args": [
             {"op": "is_on", "value": {"var": f"entity.{LIGHT}"}},
             {"op": "is_off", "value": {"var": f"entity.{PURIFIER}"}},
         ]}},
        {"id": "q1", "kind": "ask", "name": "问",
         "prompt": "要打开主卧室净化器吗？", "session": "room", "room": ROOM,
         "timeout": f"{ASK_TIMEOUT_S}s"},
        {"id": "pyes", "kind": "pass", "name": "已开", "announce": "好的，净化器已打开"},
        {"id": "pno", "kind": "pass", "name": "不要", "announce": "好的，不打开净化器"},
        {"id": "pto", "kind": "pass", "name": "超时兜底",
         "emit": {"event": "af776_ask_timeout", "data": {"room": ROOM, "reason": "timeout"}},
         "announce": "没有收到回答，本次询问作废"},
    ],
    "edges": [
        {"from": "t1", "to": "i1", "kind": "then"},
        {"from": "i1", "to": "q1", "kind": "then"},
        {"from": "i1", "to": "pno", "kind": "no"},
        {"from": "q1", "to": "pyes", "kind": "yes"},
        {"from": "q1", "to": "pno", "kind": "no"},
        {"from": "q1", "to": "pto", "kind": "on_timeout"},
        {"from": "q1", "to": "pto", "kind": "default"},
    ],
}


class _Recorder:
    """MQTT 记录器：把每条出向发布都记成 `(topic, payload)`。

    ⛔ 不用"数 speak 调用次数"这种口径——判据 a 要的是「一次 ask 总共只出 1 条 MQTT」，
    所以两条腿（speak／pending）都记，靠 topic 分辨谁在发。
    """

    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any]]] = []

    def publish_speak(self, **kw: Any) -> dict[str, Any]:
        self.published.append((SPEAK_TOPIC, kw))
        return {"published": True, "topic": SPEAK_TOPIC, "payload": kw}

    def publish_ask_pending(self, **kw: Any) -> dict[str, Any]:
        self.published.append((ASK_PENDING_TOPIC, kw))
        return {"published": True, "topic": ASK_PENDING_TOPIC, "payload": kw}

    def topics(self) -> list[str]:
        return [t for t, _ in self.published]

    def texts(self) -> list[str]:
        return [str(p.get("text") or p.get("prompt") or "") for _, p in self.published]


def _rt(monkeypatch, auto: dict[str, Any] | None = None):
    bridge = _Recorder()
    monkeypatch.setattr(mqtt, "current_bridge", lambda: bridge)
    rt = build_runtime(Graph([load_automation(auto or AUTO)]))
    monkeypatch.setattr(rt, "resolve_room", lambda room_text, *, source, entity_id="": "zhu_wo_shi")
    rt.states.set_state(OCC, "off")
    rt.states.set_state(LIGHT, "on")
    rt.states.set_state(PURIFIER, "off")
    return rt, bridge


def _enter(rt):
    rt.states.set_state(OCC, "on")
    return rt.publish(BusEvent.of(OCC, "on"))


# ── A1：一次 ask 只出 1 条 MQTT（判据 a）──────────────────────────────
def test_a1_one_ask_publishes_exactly_one_mqtt(monkeypatch):
    """判据 a（本卡主判据）：问句阶段 ⛔ 不许再发 `butler/inbox/speak`。

    修前形状：`af/ask/pending` ＋ `butler/inbox/speak` 各一条 ⇒ DB 侧 TTS 两遍
    （18:49 trace 的两条 `INBOX_DONE(speak, source=autoforge)` 里就有这一条）。
    """
    rt, bridge = _rt(monkeypatch)
    fired = _enter(rt)
    assert len(fired) == 1, "前置：ask 必须照常挂起"

    assert len(bridge.published) == 1, (
        "一次 ask 只许出 1 条 MQTT，实得 %r" % (bridge.published,)
    )
    assert bridge.topics() == [ASK_PENDING_TOPIC], (
        "唯一那条必须是 af/ask/pending；⛔ 不许出现 butler/inbox/speak（那是第二遍）"
    )
    assert SPEAK_TOPIC not in bridge.topics()


# ── A2：ask 核心不变 ＋ pending 载荷逐字 ───────────────────────────────
def test_a2_ask_core_unchanged_and_pending_payload(monkeypatch):
    """反证腿：撤 speak ⛔ 不许动 ask 核心（挂起、会话、载荷、时序都不许变）。"""
    rt, bridge = _rt(monkeypatch)
    _enter(rt)

    assert len(rt.executor.pending_asks) == 1, "ask 必须仍挂起（撤播报⛔ 不许连挂起一起弄没）"
    sess = next(iter(rt.executor.pending_asks.values()))
    assert sess.prompt == "要打开主卧室净化器吗？"
    assert sess.room == ROOM
    assert sess.node_id == "q1"

    topic, payload = bridge.published[0]
    assert topic == ASK_PENDING_TOPIC
    assert payload["ask_id"] == sess.instance_id, "ask_id 必须等于 instance_id（DB C3）"
    assert payload["room"] == "zhu_wo_shi"
    assert payload["prompt"] == "要打开主卧室净化器吗？"
    assert payload["timeout_remaining"] == float(ASK_TIMEOUT_S), "C6：图作者写 60s 就发 60s"
    # 线缆载荷（`source=autoforge` / `priority=1`）由桥方法 `build_pending_payload` 补齐，
    # 逐字钉在 `test_ask_bridge_gate_20261106.py::test_d1_pending_payload_is_c3_verbatim`
    # ——本腿只钉 runtime→bridge 这一层的四个键，⛔ 不在这里复制一份 C3 断言（两份口径必分叉）。


# ── B1：例外腿在——超时作废那句必须仍播（emit 腿）──────────────────────
def test_b1_timeout_void_announce_still_speaks(monkeypatch):
    """判据 b／授权例外：无人应答走到 `on_timeout` ⇒ 「本次询问作废」**必须仍播一次**。

    载体是 IR 的 `pto.announce`，走 emit 腿（AF-PM 2026-11-13 派卡 §① 已授权例外：
    DB 侧超时**什么都不发**，撤了这句用户就再也听不到"作废"）。
    ⛔ 若有人把 emit 腿也一起撤了，本腿红。
    """
    rt, bridge = _rt(monkeypatch)
    _enter(rt)
    assert len(bridge.published) == 1, "前置：问句阶段只有 pending 一条"

    rt.advance(ASK_TIMEOUT_S)  # 无人应答到点 ⇒ on_timeout ⇒ pto(announce)

    assert len(bridge.published) == 2, (
        "超时作废必须仍播 1 条（问句 1 条 pending ＋ 作废 1 条 speak），实得 %r"
        % (bridge.published,)
    )
    topic, payload = bridge.published[1]
    assert topic == SPEAK_TOPIC, "作废那句走 emit 腿（butler/inbox/speak）"
    assert payload["text"] == "没有收到回答，本次询问作废"


# ── C1：af_db_ask_test v4 真实形状回归（判据 c）────────────────────────
#: 线上 `af_db_ask_test` v4 的同形：入口 or 两个 source 都带 `to=on from=off`
DB_OCC = "binary_sensor.xiaomi_cn_blt_3_1hsett9ug4k01_03_occupancy_status_p_2_1078"
DB_PC = "switch.lemesh_cn_1088333045_sw0a04_on_p_2_1"
DB_LUX = "sensor.0x00158d0001a2520d_illuminance"
DB_LAMP = "light.yeelink_cn_555003624_lamp22_s_2"

AUTO_DB: dict[str, Any] = {
    "ir_version": "0.2.1", "id": "af_db_ask_test", "name": "AF776 书房 ask 同形",
    "version": 4, "mode": "single", "cooldown": 120,
    "nodes": [
        {"id": "t1", "kind": "on", "name": "电脑开或人在",
         "trigger": {"type": "group", "op": "or", "sources": [
             {"type": "state", "entity_id": DB_OCC, "to": "on", "from": "off"},
             {"type": "state", "entity_id": DB_PC, "to": "on", "from": "off"},
         ]}},
        {"id": "i_start", "kind": "if", "name": "三条件",
         "expr": {"op": "and", "args": [
             {"op": "is_on", "value": {"var": f"entity.{DB_PC}"}},
             {"op": "lt", "left": {"var": f"entity.{DB_LUX}", "type": "numeric"},
              "right": {"const": 50}},
             {"op": "is_on", "value": {"var": f"entity.{DB_OCC}"}},
             {"op": "is_off", "value": {"var": f"entity.{DB_LAMP}"}},
         ]}},
        {"id": "q1", "kind": "ask", "name": "询问挂灯",
         "prompt": "书房光线较暗，要打开显示器挂灯吗？", "session": "room",
         "room": "shu_fang", "timeout": "60s"},
        {"id": "pto", "kind": "pass", "name": "超时",
         "emit": {"event": "af_db_ask_timeout", "data": {"room": "shu_fang", "reason": "timeout"}},
         "announce": "没有收到回答，本次询问作废"},
        {"id": "p1", "kind": "pass", "name": "结束"},
    ],
    "edges": [
        {"from": "t1", "to": "i_start", "kind": "then"},
        {"from": "i_start", "to": "q1", "kind": "then"},
        {"from": "i_start", "to": "p1", "kind": "no"},
        {"from": "q1", "to": "p1", "kind": "yes"},
        {"from": "q1", "to": "p1", "kind": "no"},
        {"from": "q1", "to": "pto", "kind": "on_timeout"},
    ],
}


def test_c1_db_ask_shape_still_dispatches_with_pending_only(monkeypatch):
    """判据 c：`af_db_ask_test` 形状在那条在跑的自动化上⛔ 不许被撤腿弄坏。

    前置＝AF-771 修好的「带 `from` 的 or 入口仍能派发」；本腿只加一维：
    派发后**只**发 pending，⛔ 不发 speak。
    """
    bridge = _Recorder()
    monkeypatch.setattr(mqtt, "current_bridge", lambda: bridge)
    rt = build_runtime(Graph([load_automation(AUTO_DB)]))
    monkeypatch.setattr(rt, "resolve_room", lambda room_text, *, source, entity_id="": "shu_fang")
    for k, v in {DB_OCC: "off", DB_PC: "on", DB_LUX: "30", DB_LAMP: "off"}.items():
        rt.states.set_state(k, v)

    rt.states.set_state(DB_OCC, "on")
    fired = rt.scheduler.handle_event(BusEvent.of(DB_OCC, "on", old_state="off"))
    assert len(fired) == 1, "前置：af_db_ask_test 形状仍必须派发 1 条（AF-771 的成果不许回退）"
    assert bridge.topics() == [ASK_PENDING_TOPIC], (
        "派发后只许发 af/ask/pending，实得 %r" % (bridge.published,)
    )

    rt.advance(60)
    assert bridge.topics()[-1] == SPEAK_TOPIC, "超时作废那条仍须留着（已授权例外）"
    assert bridge.texts()[-1] == "没有收到回答，本次询问作废"
    assert SPEAK_TOPIC not in bridge.topics()[:1], "问句阶段⛔ 不许出现 speak（那才是重复念）"


# ── D1：源码钉——⛔ 不许把 ask 的 speak 腿挂回去 ───────────────────────
def test_d1_source_pin_ask_speak_leg_is_gone():
    """判据 d 的代码面：撤就撤干净，⛔ 不留空钩子（空钩子会被读成"撤了没生效"）。"""
    import inspect

    from autoforge import af_executor, af_runtime

    ex_src = inspect.getsource(af_executor)
    rt_src = inspect.getsource(af_runtime)

    assert "on_ask_speak" not in ex_src.replace('`on_ask_speak`', ""), (
        "⛔ executor 里不许再有 `on_ask_speak` 字段或调用（只允许注释里点名历史）"
    )
    assert "self.executor.on_ask_speak" not in rt_src, "⛔ Runtime 不许再注入 ask 播报闭包"
    assert "_speak_ask" not in rt_src.replace("`_speak_ask`", ""), "⛔ ask 播报闭包必须删掉"

    # 时序钉不变：pending 仍在 pending_asks 写入**之后**发（撤腿不许挪动这条）
    susp = inspect.getsource(af_executor.NodeExecutor._suspend)
    assert susp.index("self.pending_asks[instance.instance_id] = AskSession") < susp.index(
        "on_ask_pending"
    ), "pending 必须在写待答之后发（DB 答回来要对得上 pending_asks）"

    # 三条非 ask 腿一条不少
    assert "on_do_speak" in ex_src and "on_emit_speak" in ex_src and "on_fail_speak" in ex_src
