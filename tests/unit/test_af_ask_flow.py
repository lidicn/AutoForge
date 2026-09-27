"""v2 M3 Ask 端到端：ask 挂起 → 结构化应答 → then 边恢复；inbox / 会话 / 收敛纪律。

契约要点（与设计稿对齐后）：
- IR 字段名为 `ask`，控件映射由后端 `AskSpec.control()` 给出。
- 结构化应答校验通过 → 值落 `ctx.vars["ask_answer"]` + **then 边**恢复（不是 yes）。
- 结构化应答校验失败 → 拒绝并**保持挂起**（不是走 no 边），可改值重答；会话端点映射 422。
- 收敛纪律两维：同实例连问 ≤3 轮（成功 do 清零）、同房间同时挂起 ≤2。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoforge import af_service as svc
from autoforge.af_bus import BusEvent
from autoforge.af_executor import MAX_ASK_ROUNDS, MAX_ASKS_PER_ROOM, AskSession
from autoforge.af_ir import Graph, load_automation
from autoforge.af_ir.models import AskAnswer
from autoforge.af_live import read_answer_inbox
from autoforge.af_runtime import build_runtime
from autoforge.af_service import ServiceError

ROOM = "study"

AUTO: dict = {
    "ir_version": "0.2.1",
    "id": "test_ask_choice",
    "name": "结构化 ask 用例",
    "version": 1,
    "mode": "restart",
    "nodes": [
        {
            "id": "a1",
            "kind": "on",
            "name": "检测到人",
            "trigger": {"type": "state", "entity_id": "binary_sensor.motion", "to": "on"},
        },
        {
            "id": "q1",
            "kind": "ask",
            "name": "询问是否开灯",
            "prompt": "开灯吗？",
            "session": "room",
            "room": ROOM,
            "timeout": "60s",
            "ask": {"kind": "choice", "options": ["开", "关"]},
        },
        {
            "id": "d1",
            "kind": "set",
            "name": "记录已拍板",
            "var": "decided",
            "value": "yes",
        },
        {"id": "p1", "kind": "pass", "name": "结束"},
    ],
    "edges": [
        {"from": "a1", "to": "q1", "kind": "then"},
        {"from": "q1", "to": "d1", "kind": "then"},  # 结构化应答走 then（不是 yes）
        {"from": "q1", "to": "p1", "kind": "no"},  # 自由文本"不要"
        {"from": "q1", "to": "p1", "kind": "on_timeout"},
        {"from": "q1", "to": "p1", "kind": "default"},
        {"from": "d1", "to": "p1", "kind": "then"},
    ],
}


def _runtime():
    """装配带 ask 节点的 Runtime，并触发到挂起态。"""
    rt = build_runtime(Graph([load_automation(AUTO)]))
    rt.states.set_state("binary_sensor.motion", "off")
    rt.states.set_state("light.x", "off")
    rt.publish(BusEvent.of("binary_sensor.motion", "on"))
    return rt


# ── 挂起与控件元数据 ──

def test_ask_suspends_with_spec():
    rt = _runtime()
    assert len(rt.executor.pending_asks) == 1
    sess = next(iter(rt.executor.pending_asks.values()))
    assert sess.ask_spec is not None
    assert sess.ask_spec.kind == "choice"
    assert list(sess.ask_spec.options) == ["开", "关"]  # options 为 tuple
    assert sess.prompt == "开灯吗？"
    assert sess.room == ROOM


# ── 结构化应答：合法 → then 边 ──

def test_structured_answer_resumes_via_then_edge():
    """结构化值是参数收集，没有 yes/no 语义 → 走 then 边（不是 yes）。"""
    rt = _runtime()
    ask_id = next(iter(rt.executor.pending_asks))
    sess = rt.executor.pending_asks[ask_id]
    inst_id = sess.instance_id

    res = rt.executor.answer_structured(room=ROOM, payload={"kind": "choice", "value": "开"}, ask_id=ask_id)
    assert res is not None
    assert ask_id not in rt.executor.pending_asks

    inst = rt.instances.get(inst_id)
    # 值落业务变量区（下游 if/set 可引用），且是可序列化 dict
    assert inst.ctx.vars.get("ask_answer") == {"kind": "choice", "value": "开"}
    assert AskAnswer.from_dict(inst.ctx.vars["ask_answer"]).value == "开"
    # 走了 then → 执行 d1（set decided=yes），而非 no/default 直奔 p1
    assert inst.ctx.vars.get("decided") == "yes"


# ── 结构化应答：不合法 → 拒绝并保持挂起 ──

def test_structured_answer_invalid_keeps_pending_for_retry():
    """缺省即拒：不合规应答被拒绝，ask **保持挂起**让人改值重答，不消费、不走 no 边。"""
    rt = _runtime()
    ask_id = next(iter(rt.executor.pending_asks))
    res = rt.executor.answer_structured(room=ROOM, payload={"kind": "choice", "value": "炸掉"}, ask_id=ask_id)
    assert res is None  # 被拒，未恢复
    assert ask_id in rt.executor.pending_asks  # 仍挂起


def test_validate_answer_distinguishes_reject_from_missing():
    """预检：ask 存在但不合规 → False（供 service 映射 422）。"""
    rt = _runtime()
    ask_id = next(iter(rt.executor.pending_asks))
    assert rt.executor.validate_answer(room=ROOM, payload={"kind": "choice", "value": "开"}, ask_id=ask_id) is True
    assert rt.executor.validate_answer(room=ROOM, payload={"kind": "choice", "value": "炸掉"}, ask_id=ask_id) is False
    assert rt.executor.validate_answer(room=ROOM, payload={"kind": "choice", "value": "开"}, ask_id="不存在") is False


# ── watch inbox 链路 ──

def test_read_inbox_consumes_structured_answer(tmp_path, monkeypatch):
    key = "test-inbox-key"
    monkeypatch.setenv("AUTOFORGE_INBOX_KEY", key)

    rt = _runtime()
    ask_id = next(iter(rt.executor.pending_asks))
    rt.store = SimpleNamespace(root=str(tmp_path))
    inbox = tmp_path / "answer_inbox"
    inbox.mkdir()

    payload = {"kind": "choice", "value": "开"}
    answer_json = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    sig = hmac.new(
        key.encode("utf-8"),
        f"{ask_id}||{ROOM}|{answer_json}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    (inbox / "1.json").write_text(
        json.dumps({"ask_id": ask_id, "text": "", "room": ROOM, "answer": payload, "sig": sig}, ensure_ascii=False),
        encoding="utf-8",
    )

    read_answer_inbox(rt)

    assert ask_id not in rt.executor.pending_asks
    assert list(inbox.glob("*.json")) == []  # 成功消费后清理


def test_read_inbox_rejects_bad_signature(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOFORGE_INBOX_KEY", "real-key")
    rt = _runtime()
    ask_id = next(iter(rt.executor.pending_asks))
    rt.store = SimpleNamespace(root=str(tmp_path))
    inbox = tmp_path / "answer_inbox"
    inbox.mkdir()
    (inbox / "1.json").write_text(
        json.dumps(
            {"ask_id": ask_id, "text": "", "room": ROOM, "answer": {"kind": "choice", "value": "开"}, "sig": "tampered"},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    read_answer_inbox(rt)

    assert ask_id in rt.executor.pending_asks
    assert len(list(inbox.glob("*.json"))) == 1  # 证据文件保留


def test_read_inbox_keeps_evidence_when_answer_rejected(tmp_path, monkeypatch):
    """不合规应答经 inbox 注入同样被拒 → 文件保留、ask 仍挂起（可重答）。"""
    key = "k"
    monkeypatch.setenv("AUTOFORGE_INBOX_KEY", key)
    rt = _runtime()
    ask_id = next(iter(rt.executor.pending_asks))
    rt.store = SimpleNamespace(root=str(tmp_path))
    inbox = tmp_path / "answer_inbox"
    inbox.mkdir()
    payload = {"kind": "choice", "value": "越界值"}
    aj = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    sig = hmac.new(key.encode("utf-8"), f"{ask_id}||{ROOM}|{aj}".encode("utf-8"), hashlib.sha256).hexdigest()
    (inbox / "1.json").write_text(
        json.dumps({"ask_id": ask_id, "text": "", "room": ROOM, "answer": payload, "sig": sig}, ensure_ascii=False),
        encoding="utf-8",
    )

    read_answer_inbox(rt)

    assert ask_id in rt.executor.pending_asks
    assert len(list(inbox.glob("*.json"))) == 1


# ── 会话：控件元数据 + 结构化应答 + 422 ──

def test_session_asks_expose_control_and_structured_answer_consumes():
    view = svc.create_session(
        {"automations": [AUTO]},
        seed={"binary_sensor.motion": "off", "light.x": "off"},
        events=[{"entity_id": "binary_sensor.motion", "state": "on"}],
    )
    asks = view["asks"]
    assert len(asks) == 1
    assert asks[0]["spec"]["kind"] == "choice"
    assert asks[0]["control"]["widget"] == "select"
    assert asks[0]["control"]["options"] == ["开", "关"]

    sid = view["session_id"]
    out = svc.answer_session(sid, answer={"kind": "choice", "value": "开"}, ask_id=asks[0]["ask_id"], room=ROOM)
    assert out["asks"] == []


def test_session_invalid_answer_returns_422_and_keeps_ask():
    """不合规 → 422，会话保持挂起可重答。"""
    view = svc.create_session(
        {"automations": [AUTO]},
        seed={"binary_sensor.motion": "off", "light.x": "off"},
        events=[{"entity_id": "binary_sensor.motion", "state": "on"}],
    )
    sid = view["session_id"]
    ask_id = view["asks"][0]["ask_id"]
    with pytest.raises(ServiceError) as ei:
        svc.answer_session(sid, answer={"kind": "choice", "value": "炸掉"}, ask_id=ask_id, room=ROOM)
    assert ei.value.status == 422
    # 仍在挂起
    assert len(svc.get_session(sid)["asks"]) == 1


def test_asks_pending_aggregates_across_sessions():
    """跨会话聚合端点的数据源：仿真会话下的挂起 ask 也能被发现。"""
    svc.create_session(
        {"automations": [AUTO]},
        seed={"binary_sensor.motion": "off", "light.x": "off"},
        events=[{"entity_id": "binary_sensor.motion", "state": "on"}],
    )
    out = svc.asks_pending()
    assert out["ok"] is True
    assert out["total"] >= 1
    item = out["asks"][0]
    assert item["control"]["widget"] == "select"
    assert item["session_id"]  # 前端据此走会话应答端点


# ── 收敛纪律（两维）──

def test_ask_budget_blocks_when_rounds_exhausted():
    rt = _runtime()
    inst = next(iter(rt.instances.all()))
    node = rt.graph[0].node("q1") if hasattr(rt.graph, "__getitem__") else None
    auto = list(rt.graph)[0]
    node = auto.node("q1")
    inst.ctx.context["_ask_rounds"] = MAX_ASK_ROUNDS  # 已问满 3 轮
    assert rt.executor._ask_budget_ok(inst, node) is False


def test_ask_budget_blocks_when_room_saturated():
    rt = _runtime()
    inst = next(iter(rt.instances.all()))
    auto = list(rt.graph)[0]
    node = auto.node("q1")
    # 同房间已有 MAX_ASKS_PER_ROOM 个挂起（含刚挂起的这一个）
    while len([s for s in rt.executor.pending_asks.values() if s.room == ROOM]) < MAX_ASKS_PER_ROOM:
        rt.executor.pending_asks[f"fake-{len(rt.executor.pending_asks)}"] = AskSession(
            instance_id=f"fake-{len(rt.executor.pending_asks)}", node_id="q1", room=ROOM, created_at=0.0
        )
    assert rt.executor._ask_budget_ok(inst, node) is False


def test_ask_budget_ok_when_room_has_headroom():
    rt = _runtime()
    inst = next(iter(rt.instances.all()))
    auto = list(rt.graph)[0]
    node = auto.node("q1")
    assert rt.executor._ask_budget_ok(inst, node) is True
