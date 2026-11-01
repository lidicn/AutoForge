"""C-2 运行期 ask 语义收口判据（设计稿 af-runtime-ask-semantics，2026-11-01）。

钉死的契约（本文件只覆盖执行器半；G.2 构造期四拒的落点与卡面"不动构造期校验"
存在冲突，已报 PM 待裁，不在本文件）：
- §B.3 交叉通道守卫：结构化会话被 `answer()` 喂自由文本 ⇒ 拒、保持挂起、落
  ASK_VIOLATION(reason=cross_channel)；二元会话被 `answer_structured()` ⇒ 同拒
  （对称半）；`validate_answer` 与守卫同形（二元 + 结构化 ⇒ False ⇒ 422）。
- §F.1 事件面：ASK_REJECTED 从 ASK_VIOLATION 拆出（「填错值」≠「收敛纪律违反」，
  两件事在审计里可数分）；ASK_RESOLVED 一次会话至多一条（出队即消费）；ASK_EXPIRED
  三径可分（timeout/terminal/sweep）。
- §F.3 脱敏：审计消息里 spec/answer 只记 kind 与值域摘要——自由文本长度+前 2 字符、
  entity 只记 domain，⛔ 全文直印。
- 二元路径不误伤：ask_spec 为 None 的会话经 `answer()` 正常选边（case04 新语义）。
"""

from __future__ import annotations

import pytest

from autoforge import af_executor as afx
from autoforge.af_bus import BusEvent
from autoforge.af_ir import Graph, load_automation
from autoforge.af_ir.models import AskAnswer
from autoforge.af_runtime import build_runtime

ROOM = "study"

STRUCTURED_AUTO: dict = {
    "ir_version": "0.2.1", "id": "c2_structured", "name": "结构化 ask", "version": 1,
    "mode": "restart",
    "nodes": [
        {"id": "a1", "kind": "on", "name": "t",
         "trigger": {"type": "state", "entity_id": "binary_sensor.motion", "to": "on"}},
        {"id": "q1", "kind": "ask", "name": "q", "prompt": "多少度？", "session": "room",
         "room": ROOM, "timeout": "60s",
         "ask": {"kind": "threshold", "min": 16, "max": 30}},
        {"id": "d1", "kind": "set", "name": "d", "var": "decided", "value": "yes"},
        {"id": "p1", "kind": "pass", "name": "p"},
    ],
    "edges": [
        {"from": "a1", "to": "q1", "kind": "then"},
        {"from": "q1", "to": "d1", "kind": "then"},
        {"from": "q1", "to": "p1", "kind": "no"},
        {"from": "q1", "to": "p1", "kind": "on_timeout"},
        {"from": "q1", "to": "p1", "kind": "default"},
        {"from": "d1", "to": "p1", "kind": "then"},
    ],
}

BINARY_AUTO: dict = {
    "ir_version": "0.2.1", "id": "c2_binary", "name": "二元 ask", "version": 1,
    "mode": "restart",
    "nodes": [
        {"id": "a1", "kind": "on", "name": "t",
         "trigger": {"type": "state", "entity_id": "binary_sensor.motion", "to": "on"}},
        {"id": "q1", "kind": "ask", "name": "q", "prompt": "开灯吗？", "session": "room",
         "room": ROOM, "timeout": "60s"},
        {"id": "d1", "kind": "set", "name": "d", "var": "decided", "value": "yes"},
        {"id": "p1", "kind": "pass", "name": "p"},
    ],
    "edges": [
        {"from": "a1", "to": "q1", "kind": "then"},
        {"from": "q1", "to": "d1", "kind": "yes"},
        {"from": "q1", "to": "p1", "kind": "no"},
        {"from": "q1", "to": "p1", "kind": "on_timeout"},
        {"from": "d1", "to": "p1", "kind": "then"},
    ],
}


def _runtime(auto: dict):
    rt = build_runtime(Graph([load_automation(auto)]))
    rt.states.set_state("binary_sensor.motion", "off")
    rt.publish(BusEvent.of("binary_sensor.motion", "on"))
    assert len(rt.executor.pending_asks) == 1
    return rt


def _audit_types(rt):
    return [e.type for e in rt.audit.events]


def _audit_of(rt, type_: str):
    return [e for e in rt.audit.events if e.type == type_]


# ── §B.3 交叉通道守卫 ────────────────────────────────────────────────

def test_b3_structured_session_rejects_free_text():
    rt = _runtime(STRUCTURED_AUTO)
    inst = next(iter(rt.instances.all())) if hasattr(rt, "instances") else None
    out = rt.executor.answer(ROOM, "随便答一句", ask_id=None)
    assert out is None, "结构化会话被自由文本喂 ⇒ 必须拒"
    assert len(rt.executor.pending_asks) == 1, "拒后必须保持挂起（可走 answer_structured 重答）"
    v = _audit_of(rt, afx.ASK_VIOLATION)
    assert v and v[-1].data.get("reason") == "cross_channel", v[-1].data


def test_b3_binary_session_rejects_structured():
    rt = _runtime(BINARY_AUTO)
    out = rt.executor.answer_structured(
        ROOM, {"kind": "threshold", "value": 22}, ask_id=None)
    assert out is None, "二元会话被结构化喂 ⇒ 必须拒"
    assert len(rt.executor.pending_asks) == 1, "拒后必须保持挂起"
    v = _audit_of(rt, afx.ASK_VIOLATION)
    assert v and v[-1].data.get("reason") == "cross_channel", v[-1].data


def test_b3_validate_answer_binary_is_false():
    rt = _runtime(BINARY_AUTO)
    assert rt.executor.validate_answer(ROOM, {"kind": "threshold", "value": 22}) is False


def test_binary_text_path_not_blocked_by_guard():
    """守卫不误伤：二元会话经 answer() 正常选边（case04 新语义：答"好"即走）。"""
    rt = _runtime(BINARY_AUTO)
    out = rt.executor.answer(ROOM, "好", ask_id=None)
    assert out is not None, "二元路径被守卫误伤"
    assert len(rt.executor.pending_asks) == 0, "消费后应出队"


# ── §F.1 事件面 ──────────────────────────────────────────────────────

def test_f1_structured_success_emits_one_resolved():
    rt = _runtime(STRUCTURED_AUTO)
    out = rt.executor.answer_structured(ROOM, {"kind": "threshold", "value": 22}, ask_id=None)
    assert out is not None
    resolved = _audit_of(rt, afx.ASK_RESOLVED)
    assert len(resolved) == 1, "一次会话至多一条 ASK_RESOLVED"
    assert resolved[0].data["edge"] == "then" and resolved[0].data["channel"] == "structured"
    assert resolved[0].data["payload_kind"] == "threshold"


def test_f1_repeat_answer_no_second_resolved():
    rt = _runtime(STRUCTURED_AUTO)
    assert rt.executor.answer_structured(ROOM, {"kind": "threshold", "value": 22}) is not None
    assert rt.executor.answer_structured(ROOM, {"kind": "threshold", "value": 23}) is None
    assert len(_audit_of(rt, afx.ASK_RESOLVED)) == 1, "重复应答不得产生第二条 RESOLVED"


def test_f1_rejected_uses_ask_rejected_not_violation():
    rt = _runtime(STRUCTURED_AUTO)
    assert rt.executor.answer_structured(ROOM, {"kind": "threshold", "value": 999}) is None
    rejected = _audit_of(rt, afx.ASK_REJECTED)
    assert rejected, "校验被拒必须落 ASK_REJECTED（从 VIOLATION 拆出）"
    assert len(_audit_of(rt, afx.ASK_VIOLATION)) == 0, "「填错值」≠「收敛纪律违反」"


def test_f1_expired_three_paths_distinguishable():
    # terminal 腿：终态实例的迟到应答 ⇒ 幽灵出队 + ASK_EXPIRED(reason=terminal)
    # （用二元会话：结构化会话会先被 B.3 守卫拦下，走不到 terminal 分支）
    rt = _runtime(BINARY_AUTO)
    inst = rt.instances.get(next(iter(rt.executor.pending_asks)))
    rt.instances.fail(inst, "c2 test")  # 打成终态（is_terminal）
    assert rt.executor.answer(ROOM, "好") is None
    term = [e for e in _audit_of(rt, afx.ASK_EXPIRED) if e.data.get("reason") == "terminal"]
    assert term, "terminal 腿缺"
    # sweep 腿：sweep_orphans 清幽灵 ⇒ reason=sweep
    rt2 = _runtime(STRUCTURED_AUTO)
    i2 = rt2.instances.get(next(iter(rt2.executor.pending_asks)))
    rt2.instances.fail(i2, "c2 test")
    assert rt2.executor.sweep_orphans() == 1
    sweep = [e for e in _audit_of(rt2, afx.ASK_EXPIRED) if e.data.get("reason") == "sweep"]
    assert sweep, "sweep 腿缺"
    # timeout 腿：ask 的 on_timeout resume ⇒ reason=timeout（canary 不入会话表不误报）
    rt3 = _runtime(STRUCTURED_AUTO)
    i3 = rt3.instances.get(next(iter(rt3.executor.pending_asks)))
    rt3.executor.resume(i3, "on_timeout")
    to = [e for e in _audit_of(rt3, afx.ASK_EXPIRED) if e.data.get("reason") == "timeout"]
    assert to, "timeout 腿缺"


# ── §F.3 脱敏 ────────────────────────────────────────────────────────

def test_f3_redaction_entity_only_domain():
    rt = _runtime(STRUCTURED_AUTO)
    # 非法 kind（越界）触发拒；entity 值只记 domain
    rt.executor.answer_structured(ROOM, {"kind": "entity", "value": "light.study_desk_lamp"})
    msg = _audit_of(rt, afx.ASK_REJECTED)[-1].message
    assert "domain=light" in msg and "study_desk_lamp" not in msg, msg


def test_f3_redaction_free_text_len_head_only():
    rt = _runtime(STRUCTURED_AUTO)
    rt.executor.answer(ROOM, "这是一段很长的自由文本不应全文进审计")
    msg = _audit_of(rt, afx.ASK_VIOLATION)[-1].message
    assert "text_len=" in msg and "text_head=" in msg, msg
    assert "不应全文进审计" not in msg, "⛔ 自由文本全文不得进审计"


def test_f3_redaction_spec_kind_only():
    rt = _runtime(STRUCTURED_AUTO)
    rt.executor.answer_structured(ROOM, {"kind": "threshold", "value": 999})
    msg = _audit_of(rt, afx.ASK_REJECTED)[-1].message
    assert "spec_kind=threshold" in msg, msg
    assert "min" not in msg and "max" not in msg, "⛔ spec 全文（min/max/options）不得直印"
