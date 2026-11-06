"""v1.28 R1「ask 双向桥」判据（卡名 `AF卡_ask_bridge`，2026-11-06）。

**权威出处**：DCD 裁定 2026-11-04 §2.3（C1–C6/C8/C9）＋ PM v1.28 批卡 §二 三条补钉
（超时默认/持久化复用/变异自证）。端点面：
- 出向 `af/ask/pending`（AF→DB），载荷 C3 逐字 `{ask_id, room, prompt, source, priority}`＋C6 `timeout_remaining`；
- 入向 `af/ask/answer`（DB→AF），载荷 C4 逐字 `{ask_id, answer:"yes"/"no", raw_text}`；
- 消费面仍是 `answer_inbox/`（`af_live.read_answer_inbox`，与 WebUI 腿同一条）；
- 持久化面 `<archive>.runtime.json#ask_answers`（复用运行期事实设施，⛔ 不新建持久化面）。

**每格都带反证腿（群规⑧有牙）**：正例走通后，用"这条腿真断掉"的输入把同一格判红。
其中 D5 那条最要紧——它证明"AF 不再对 `raw_text` 二次分类"（拿一句 homesdk 会判 `no`
的原文＋DB 判 `yes` 的投票，实现若回退成再分类就会走 `no` 边）。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from autoforge import af_mqtt_bridge as mqtt
from autoforge.af_ask_bridge import (
    ASK_ANSWER_TOPIC,
    ASK_PENDING_TOPIC,
    ANSWERED_VIA_WEBUI,
    ANSWERED_VIA_XIAOMI,
    AnswerRejected,
    build_pending_payload,
    normalize_answered_via,
    parse_answer_payload,
    write_answer_inbox_file,
)
from autoforge.af_bus import BusEvent
from autoforge.af_executor import classify_answer
from autoforge.af_ir import Graph, load_automation
from autoforge.af_live import read_answer_inbox
from autoforge.af_runtime import build_runtime
from autoforge.af_runtime_facts import RuntimeFacts, recent_ask_answers

ROOM = "study"

#: 二元会话（yes/no 边），**不写 timeout** ⇒ 走 v1.28 的 300s 默认兜底（超时腿 D6）
AUTO_BINARY: dict[str, Any] = {
    "ir_version": "0.2.1",
    "id": "gate_ask_binary",
    "name": "R1 判据：二元 ask",
    "version": 1,
    "mode": "restart",
    "nodes": [
        {"id": "a1", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.motion", "to": "on"}},
        {"id": "q1", "kind": "ask", "prompt": "现在开空调吗？", "session": "room", "room": ROOM},
        {"id": "d1", "kind": "set", "var": "decided", "value": "yes"},
        {"id": "d2", "kind": "set", "var": "refused", "value": "yes"},
        {"id": "p1", "kind": "pass"},
    ],
    "edges": [
        {"from": "a1", "to": "q1", "kind": "then"},
        {"from": "q1", "to": "d1", "kind": "yes"},
        {"from": "q1", "to": "d2", "kind": "no"},
        {"from": "q1", "to": "p1", "kind": "on_timeout"},
        {"from": "q1", "to": "p1", "kind": "default"},
        {"from": "d1", "to": "p1", "kind": "then"},
        {"from": "d2", "to": "p1", "kind": "then"},
    ],
}

#: 带 `timeout` 的同形图（对照腿：图作者写了多少就用多少，⛔ 不被默认值顶掉）
AUTO_BINARY_60S = json.loads(json.dumps(AUTO_BINARY))
AUTO_BINARY_60S["id"] = "gate_ask_binary_60s"
for _n in AUTO_BINARY_60S["nodes"]:
    if _n["id"] == "q1":
        _n["timeout"] = "60s"


class _FakeBridge:
    """只记发布调用：本文件不测 broker，测的是"谁在什么时候发了哪条载荷"。"""

    def __init__(self) -> None:
        self.pending: list[dict[str, Any]] = []
        self.speaks: list[dict[str, Any]] = []

    def publish_ask_pending(self, **kw: Any) -> dict[str, Any]:
        self.pending.append(kw)
        return {"published": True, "topic": ASK_PENDING_TOPIC, "payload": kw}

    def publish_speak(self, **kw: Any) -> dict[str, Any]:
        self.speaks.append(kw)
        return {"published": True}


def _suspend(monkeypatch: pytest.MonkeyPatch, auto: dict[str, Any], *, area: str | None = "shu_fang",
             store_root: str | None = None):
    """装配 Runtime（桥＝假桥、房间解析＝钉死）并触发到挂起态。"""
    bridge = _FakeBridge()
    monkeypatch.setattr(mqtt, "current_bridge", lambda: bridge)
    rt = build_runtime(Graph([load_automation(auto)]), store_root=store_root)
    monkeypatch.setattr(rt, "resolve_room", lambda room_text, *, source, entity_id="": area)
    rt.publish(BusEvent.of("binary_sensor.motion", "on"))
    return rt, bridge


# ── D1 出向载荷：C3 逐字 ＋ C6 timeout_remaining ────────────────────────

def test_d1_pending_payload_is_c3_verbatim():
    payload = build_pending_payload(
        ask_id="inst-1", room="shu_fang", prompt="开空调吗？", timeout_remaining=300.0
    )
    assert set(payload) == {"ask_id", "room", "prompt", "source", "priority", "timeout_remaining"}
    assert payload["source"] == "autoforge" and payload["priority"] == 1
    assert payload["timeout_remaining"] == 300.0
    # 反证腿：身份不合 C3 约束（空/超 64）⇒ 当场拒，⛔ 不发一条 DB 认不出身份的挂起
    with pytest.raises(AnswerRejected):
        build_pending_payload(ask_id="", room="shu_fang", prompt="x")
    with pytest.raises(AnswerRejected):
        build_pending_payload(ask_id="a" * 65, room="shu_fang", prompt="x")


def test_d1_suspend_publishes_pending_without_touching_ask_core(monkeypatch):
    rt, bridge = _suspend(monkeypatch, AUTO_BINARY)
    assert len(bridge.pending) == 1, "挂起时应发一条 af/ask/pending"
    call = bridge.pending[0]
    ask_id = next(iter(rt.executor.pending_asks))
    assert call["ask_id"] == ask_id
    assert call["room"] == "shu_fang" and call["prompt"] == "现在开空调吗？"
    assert call["timeout_remaining"] == 300.0, "未写 timeout ⇒ 默认兜底值随载荷发出（C6）"
    # ask 核心不被旁路改动：仍挂起、不误派发
    assert ask_id in rt.executor.pending_asks
    inst = rt.instances.get(ask_id)
    assert inst.ctx.vars.get("decided") is None


def test_d1_unresolved_room_refuses_to_publish(monkeypatch):
    """解析不到区域 ⇒ 拒投递（与 speak 腿同一道 fail-closed 闸），但 ask 仍正常挂起。"""
    rt, bridge = _suspend(monkeypatch, AUTO_BINARY, area=None)
    assert bridge.pending == []
    assert len(rt.executor.pending_asks) == 1


# ── D2 入向腿：MQTT `af/ask/answer` → inbox → 消费 ─────────────────────

def test_d2_mqtt_leg_accepts_verdict_and_writes_signed_inbox(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOFORGE_INBOX_KEY", "gate-key")
    bridge = mqtt.AfMqttBridge(object(), ask_answer_root=str(tmp_path))
    out = bridge.ingest_ask_answer({"ask_id": "inst-9", "answer": "yes", "raw_text": "好"})
    assert out["handled"] is True and out["via"] == ANSWERED_VIA_XIAOMI
    assert bridge.counts["ask_answer_in"] == 1
    files = list((tmp_path / "answer_inbox").glob("*.json"))
    assert len(files) == 1
    body = json.loads(files[0].read_text(encoding="utf-8"))
    assert body["via"] == ANSWERED_VIA_XIAOMI
    # C4/C5：投票落进被签名的 `answer` 格（与原文同一份签名材料）
    assert body["answer"] == {"verdict": "yes"} and body["text"] == "好"
    assert body["sig"], "有密钥时必须签名（缺签名读侧必拒）"


def test_d2_mqtt_leg_rejects_non_mvp_and_missing_key(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOFORGE_INBOX_KEY", "gate-key")
    bridge = mqtt.AfMqttBridge(object(), ask_answer_root=str(tmp_path))
    # 反证腿①：C4 只两值 ⇒ 带参数回答整条拒（MVP 不得项）
    bad = bridge.ingest_ask_answer({"ask_id": "inst-9", "answer": "设到25度", "raw_text": "设到25度"})
    assert bad["handled"] is False and bad["reason"] == "answer_not_verdict"
    # 反证腿②：信道优先——自称别的来源整条拒（枚举不自行扩）
    other = bridge.ingest_ask_answer({"ask_id": "inst-9", "answer": "yes", "via": "alexa"})
    assert other["handled"] is False and other["reason"] == "via_not_allowed_on_channel"
    assert bridge.counts["ask_answer_rejected"] == 2
    assert list((tmp_path / "answer_inbox").glob("*.json")) == [], "被拒的应答⛔ 不落盘"
    # 反证腿③：没密钥 ⇒ 当场拒（⛔ 不写一份读侧注定被丢的文件）
    monkeypatch.delenv("AUTOFORGE_INBOX_KEY", raising=False)
    nokey = bridge.ingest_ask_answer({"ask_id": "inst-9", "answer": "no"})
    assert nokey["handled"] is False and nokey["reason"] == "inbox_key_missing"
    assert list((tmp_path / "answer_inbox").glob("*.json")) == []


def test_d2_topic_names_are_ruled_verbatim():
    """C1/C2 主题名逐字——裁定写死的名字，⛔ 不许出现同义变体。"""
    assert ASK_PENDING_TOPIC == "af/ask/pending"
    assert ASK_ANSWER_TOPIC == "af/ask/answer"
    assert mqtt.ASK_PENDING_TOPIC == ASK_PENDING_TOPIC
    assert mqtt.ASK_ANSWER_TOPIC == ASK_ANSWER_TOPIC


# ── D3 消费腿：xiaomi / webui 两条来源标 ＋ 审计留痕 ─────────────────────

def test_d3_xiaomi_answer_resumes_and_marks_answered_via(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOFORGE_INBOX_KEY", "gate-key")
    root = tmp_path
    rt, _ = _suspend(monkeypatch, AUTO_BINARY, store_root=str(root))
    ask_id = next(iter(rt.executor.pending_asks))

    write_answer_inbox_file(
        root, ask_id=ask_id, text="好", room=ROOM, verdict="yes", via=ANSWERED_VIA_XIAOMI
    )
    read_answer_inbox(rt, inbox_dir=str(root / "answer_inbox"))

    assert ask_id not in rt.executor.pending_asks, "应答应解挂"
    inst = rt.instances.get(ask_id)
    assert inst.ctx.vars.get("decided") == "yes", "yes 边应被走到"
    resolved = [e for e in rt.audit if e.type == "ask_resolved"]
    assert resolved and resolved[-1].data["answered_via"] == ANSWERED_VIA_XIAOMI
    # 持久化腿（复用运行期事实设施）：已应答历史落 `<archive>.runtime.json`
    facts = RuntimeFacts(str(root), AUTO_BINARY["id"]).ask_answers()
    assert facts and facts[0]["answered_via"] == ANSWERED_VIA_XIAOMI
    assert facts[0]["edge"] == "yes" and facts[0]["ask_id"] == ask_id


def test_d3_webui_leg_defaults_to_webui(tmp_path, monkeypatch):
    """文件回注/WebUI 腿：载荷没有 via ⇒ 按回退口径记 webui（⛔ 不猜第三个值）。"""
    import hashlib
    import hmac
    import time as _time

    monkeypatch.setenv("AUTOFORGE_INBOX_KEY", "gate-key")
    root = tmp_path
    rt, _ = _suspend(monkeypatch, AUTO_BINARY, store_root=str(root))
    ask_id = next(iter(rt.executor.pending_asks))
    inbox = root / "answer_inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    # 手写一份"历史遗留"文件：没有 via 字段（签名覆盖 text/room/answer，⛔ 与 via 无关）
    ts = int(_time.time())
    answer_json = "{}"
    sig = hmac.new(
        b"gate-key", f"{ask_id}|不要|{ROOM}|{answer_json}|{ts}".encode(), hashlib.sha256
    ).hexdigest()
    (inbox / "legacy.json").write_text(
        json.dumps({"ask_id": ask_id, "text": "不要", "room": ROOM, "ts": ts, "sig": sig}),
        encoding="utf-8",
    )
    read_answer_inbox(rt, inbox_dir=str(inbox))
    assert ask_id not in rt.executor.pending_asks
    inst = rt.instances.get(ask_id)
    assert inst.ctx.vars.get("refused") == "yes", "自由文本腿仍按本仓分类走 no 边"
    resolved = [e for e in rt.audit if e.type == "ask_resolved"]
    assert resolved and resolved[-1].data["answered_via"] == "webui"


def test_d3_via_enum_is_closed():
    assert normalize_answered_via("xiaomi") == ANSWERED_VIA_XIAOMI
    assert normalize_answered_via(None) == ANSWERED_VIA_WEBUI
    assert normalize_answered_via("alexa") == ANSWERED_VIA_WEBUI  # 未知回落，⛔ 不扩枚举
    with pytest.raises(AnswerRejected):
        parse_answer_payload({"ask_id": "a", "answer": "yes", "via": "alexa"})


# ── D4 在途条的形状（PM 契约：仅"已答未消费"带值）────────────────────

def test_d4_pending_ask_carries_source_only_after_a_rejected_attempt(monkeypatch):
    rt, _ = _suspend(monkeypatch, AUTO_BINARY)
    ask_id = next(iter(rt.executor.pending_asks))
    assert rt.executor.pending_asks[ask_id].answered_via is None, "没答过 ⇒ None（UI 不显示标签）"
    # 交叉通道误用：结构化会话被喂自由文本 ⇒ 拒收、保持挂起，但"答过一次"留下
    session = rt.executor.pending_asks[ask_id]
    session.ask_spec = object()  # 造出一个结构化会话（本判据只测"拒收但留标"这一格）
    assert rt.executor.answer(room=ROOM, text="开", ask_id=ask_id, answered_via=ANSWERED_VIA_XIAOMI) is None
    assert ask_id in rt.executor.pending_asks
    assert rt.executor.pending_asks[ask_id].answered_via == ANSWERED_VIA_XIAOMI


# ── D5 分类归 DB：⛔ 不对 raw_text 二次分类（本批最要紧的一格）──────────

def test_d5_db_verdict_wins_over_local_reclassification(tmp_path, monkeypatch):
    """DB 判 yes、原文却是 homesdk 会判 no 的一句 ⇒ 必须走 yes 边。

    若实现回退成"拿 raw_text 再分类一次"，本用例走 `no` 边（`refused=yes`、`decided` 空）⇒ 判红。
    """
    monkeypatch.setenv("AUTOFORGE_INBOX_KEY", "gate-key")
    raw = "不要这个"
    assert classify_answer(raw) == "no", "原文必须是本地会判 no 的差分输入，否则本判据没有牙"
    root = tmp_path
    rt, _ = _suspend(monkeypatch, AUTO_BINARY, store_root=str(root))
    ask_id = next(iter(rt.executor.pending_asks))
    write_answer_inbox_file(root, ask_id=ask_id, text=raw, room=ROOM, verdict="yes", via=ANSWERED_VIA_XIAOMI)
    read_answer_inbox(rt, inbox_dir=str(root / "answer_inbox"))
    inst = rt.instances.get(ask_id)
    assert inst.ctx.vars.get("decided") == "yes", "必须采信 DB 的 yes（C5），⛔ 不许被本地分类翻掉"
    assert inst.ctx.vars.get("refused") is None


def test_d5_bad_verdict_in_file_is_refused(tmp_path, monkeypatch):
    """文件里的 verdict 非法（篡改/坏写）⇒ 拒收、保留证据文件、⛔ 不消费。"""
    monkeypatch.setenv("AUTOFORGE_INBOX_KEY", "gate-key")
    root = tmp_path
    rt, _ = _suspend(monkeypatch, AUTO_BINARY)
    ask_id = next(iter(rt.executor.pending_asks))
    write_answer_inbox_file(root, ask_id=ask_id, text="好", room=ROOM)
    f = next((root / "answer_inbox").glob("*.json"))
    body = json.loads(f.read_text(encoding="utf-8"))
    body["answer"] = {"verdict": "maybe"}
    # 重签成"签名有效但投票非法"，证明拒收来自投票枚举而不是签名
    import hashlib
    import hmac
    import time as _time

    ts = int(_time.time())
    answer_json = json.dumps(body["answer"], sort_keys=True, ensure_ascii=False)
    body["ts"] = ts
    body["sig"] = hmac.new(b"gate-key", f"{ask_id}||{ROOM}|{answer_json}|{ts}".encode(), hashlib.sha256).hexdigest()
    f.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    read_answer_inbox(rt, inbox_dir=str(root / "answer_inbox"))
    assert ask_id in rt.executor.pending_asks, "非法投票不得消费 ask"
    assert f.exists(), "证据文件保留"


# ── D6 超时腿：默认 300s 兜底 ＋ 到点收尾 ───────────────────────────────

def test_d6_ask_without_timeout_gets_default_deadline(monkeypatch):
    rt, _ = _suspend(monkeypatch, AUTO_BINARY)
    ask_id = next(iter(rt.executor.pending_asks))
    inst = rt.instances.get(ask_id)
    assert inst.timer is not None, "未写 timeout 也必须有个到点（否则永久挂起）"
    assert inst.timer.kind == "ask"
    remaining = inst.timer.due_at - rt.clock.monotonic()
    assert 295.0 < remaining <= 300.5, f"默认兜底应为 300s 量级，实得 {remaining}"


def test_d6_ir_timeout_still_wins_and_timeout_reaches_edge(monkeypatch):
    rt, _ = _suspend(monkeypatch, AUTO_BINARY_60S)
    ask_id = next(iter(rt.executor.pending_asks))
    inst = rt.instances.get(ask_id)
    assert 55.0 < inst.timer.due_at - rt.clock.monotonic() <= 60.5, "图作者写 60s ⇒ 就用 60s"
    # 到点：走 on_timeout 收尾，不留死挂起、不误判 yes/no
    rt.executor.timeout(inst)
    assert ask_id not in rt.executor.pending_asks
    assert inst.state != "suspended"
    assert inst.ctx.vars.get("decided") is None and inst.ctx.vars.get("refused") is None
    expired = [e for e in rt.audit if e.type == "ask_expired"]
    assert expired, "未经应答消亡必须留 ASK_EXPIRED（C-2 §F.1）"


# ── D7 持久化面：环形 ＋ 跨实例读回 ＋ UI 载荷形状 ─────────────────────

def test_d7_recent_ask_answers_ring_cap_and_restart_readback(tmp_path):
    root = tmp_path
    for i in range(7):
        RuntimeFacts(str(root), "auto1").record_ask_answer(
            ask_id=f"i{i}",
            node_id="q1",
            room=ROOM,
            edge="yes",
            answered_via=ANSWERED_VIA_XIAOMI if i % 2 else ANSWERED_VIA_WEBUI,
            payload_kind="text",
        )
    got = recent_ask_answers(str(root), 5)
    assert [e["ask_id"] for e in got] == ["i6", "i5", "i4", "i3", "i2"], "新→旧、最多 5 条"
    assert got[0]["answered_via"] == ANSWERED_VIA_WEBUI  # i6：偶数 ⇒ webui（逐条来源标未被抹平）
    # "重启"＝换一个 RuntimeFacts 实例再读（正源在盘上，不在内存）
    assert RuntimeFacts(str(root), "auto1").ask_answers(3)[0]["ask_id"] == "i6"
    assert recent_ask_answers(None, 5) == [], "没有 store 时如实空表，⛔ 不伪造"


def test_d7_asks_payload_shape_for_ui(tmp_path):
    from autoforge import af_service as svc

    RuntimeFacts(str(tmp_path), "auto1").record_ask_answer(
        ask_id="i9", room=ROOM, edge="yes", answered_via=ANSWERED_VIA_XIAOMI
    )
    out = svc.asks_pending(str(tmp_path))
    assert set(out) >= {"ok", "total", "asks", "answered"}
    assert len(out["answered"]) == 1
    item = out["answered"][0]
    assert {"ask_id", "answered_via", "answered_at"} <= set(item), "UI 渲染三件套必须齐"
