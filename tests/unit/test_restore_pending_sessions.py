"""§十八 B.14：崩溃恢复后，挂起的 `ask` / 人工确认会话必须**重挂**（看得见、能应答、绝不自动放行）。

判据要钉住的形状：
- 重启后 `pending_asks`（`/api/asks`、sidecar、inbox 三面共同的读源）里必须还挂着那条问题。
- **恢复过程本身零下发**：重启不等于人已批过。
- 答 yes ⇒ 恰一次下发；答 no ⇒ 零下发（沿用 §三 硬前置那套一次性授权与边纪律）。
- 确认会话的问句按**当前图**渲染 ⇒ 拿旧动作名问人 = 说谎。
- `wait` / `canary_observe` 不重挂（无人在场语义，不是问题）。
- 挂起节点已不在当前图 ⇒ 具名审计，不许静默跳过。
"""

from __future__ import annotations

from autoforge.af_audit import CONFIRM_DENIED, CONFIRM_GRANTED, INSTANCE_SESSION_LOST
from autoforge.af_instance import DONE, SUSPENDED
from autoforge.af_ir import Graph, load_automation
from autoforge.af_runtime import build_runtime

AUTO_ID = "demo_sessions"
ROOM = "study"


def _nodes(tail: list[dict]) -> list[dict]:
    return [
        {
            "id": "a1",
            "kind": "on",
            "name": "有人进书房",
            "trigger": {"type": "state", "entity_id": "binary_sensor.study", "to": "on"},
        },
        *tail,
    ]


def _ask_ir(*, room: bool = False, timeout: str | None = None) -> dict:
    """入口 → `ask` → （yes）`do` → pass。`do` 不带确认，专测 ask 那一格。"""
    q: dict = {"id": "q1", "kind": "ask", "name": "要不要开", "prompt": "书房热了，开空调吗？"}
    if room:
        q.update({"session": "room", "room": ROOM})
    if timeout:
        q["timeout"] = timeout
    return {
        "ir_version": "0.2.1",
        "id": AUTO_ID,
        "name": "恢复会话用例（ask）",
        "version": 1,
        "mode": "restart",
        "persist": True,
        "nodes": _nodes(
            [
                q,
                {
                    "id": "d1",
                    "kind": "do",
                    "name": "开空调",
                    "adapter": "mock",
                    "action": "climate.turn_on",
                    "params": {"entity_id": "climate.study"},
                },
                {"id": "p1", "kind": "pass", "name": "结束"},
            ]
        ),
        "edges": [
            {"from": "a1", "to": "q1", "kind": "then"},
            {"from": "q1", "to": "d1", "kind": "yes"},
            {"from": "q1", "to": "p1", "kind": "no"},
            {"from": "q1", "to": "p1", "kind": "default"},
            {"from": "d1", "to": "p1", "kind": "then"},
        ],
    }


def _confirm_ir(*, room: bool = False, action: str = "climate.turn_on") -> dict:
    """入口 →（可选 ask 定房间）→ 受确认 `do`。链路与 §三 硬前置那批判据同形。"""
    head: list[dict] = []
    edges = [{"from": "a1", "to": "d1", "kind": "then"}]
    if room:
        head = [
            {
                "id": "q1",
                "kind": "ask",
                "name": "要不要开",
                "prompt": "书房热了，开空调吗？",
                "session": "room",
                "room": ROOM,
                "timeout": "60s",
            }
        ]
        edges = [
            {"from": "a1", "to": "q1", "kind": "then"},
            {"from": "q1", "to": "d1", "kind": "yes"},
            {"from": "q1", "to": "p1", "kind": "no"},
            {"from": "q1", "to": "p1", "kind": "default"},
        ]
    edges.append({"from": "d1", "to": "p1", "kind": "then"})
    return {
        "ir_version": "0.2.1",
        "id": AUTO_ID,
        "name": "恢复会话用例（确认）",
        "version": 1,
        "mode": "restart",
        "persist": True,
        "nodes": _nodes(
            [
                *head,
                {
                    "id": "d1",
                    "kind": "do",
                    "name": "开空调",
                    "adapter": "mock",
                    "action": action,
                    "params": {"entity_id": "climate.study"},
                    "requires_confirm": True,
                },
                {"id": "p1", "kind": "pass", "name": "结束"},
            ]
        ),
        "edges": edges,
    }


def _wait_ir() -> dict:
    return {
        "ir_version": "0.2.1",
        "id": AUTO_ID,
        "name": "恢复会话用例（wait）",
        "version": 1,
        "mode": "single",
        "persist": True,
        "nodes": _nodes(
            [
                {"id": "w1", "kind": "wait", "duration": "10m"},
                {"id": "p1", "kind": "pass", "name": "结束"},
            ]
        ),
        "edges": [
            {"from": "a1", "to": "w1", "kind": "then"},
            {"from": "w1", "to": "p1", "kind": "then"},
        ],
    }


def _runtime(ir: dict, persist_dir=None):
    return build_runtime(
        Graph([load_automation(ir)]),
        states=None,
        persist_dir=str(persist_dir) if persist_dir else None,
    )


def _suspend_first(rt) -> str:
    """触发一次并确认已挂起，返回实例 id。"""
    rt.states.set_state("binary_sensor.study", "off")
    rt.emit("binary_sensor.study", "on", last_changed="t1")
    inst = rt.instances.all()[0]
    assert inst.state == SUSPENDED
    return inst.instance_id


# ── ask 会话 ──


def test_ask_session_is_visible_after_restart(tmp_path):
    """重启后 `pending_asks` 必须还挂着那条问题——它是 `/api/asks`、sidecar、inbox 共同的读源。"""
    persist_dir = tmp_path / "p"
    rt1 = _runtime(_ask_ir(), persist_dir)
    instance_id = _suspend_first(rt1)
    assert len(rt1.executor.pending_asks) == 1

    rt2 = _runtime(_ask_ir(), persist_dir)
    assert len(rt2.restored) == 1
    assert instance_id in rt2.executor.pending_asks, "恢复后问题必须看得见，否则实例永远挂在那里"
    sess = rt2.executor.pending_asks[instance_id]
    assert sess.node_id == "q1"
    assert "开空调" in sess.prompt
    assert sess.ask_spec is None
    assert rt2.adapters.get("mock").calls == [], "恢复过程本身零下发"


def test_answer_yes_after_restart_dispatches_exactly_once(tmp_path):
    persist_dir = tmp_path / "p"
    rt1 = _runtime(_ask_ir(), persist_dir)
    instance_id = _suspend_first(rt1)

    rt2 = _runtime(_ask_ir(), persist_dir)
    answered = rt2.executor.answer(room=None, text="好", ask_id=instance_id)
    assert answered is not None
    assert [a for a, _ in rt2.adapters.get("mock").calls] == ["climate.turn_on"]
    assert rt2.executor.pending_asks == {}


def test_answer_no_after_restart_never_dispatches(tmp_path):
    persist_dir = tmp_path / "p"
    rt1 = _runtime(_ask_ir(), persist_dir)
    instance_id = _suspend_first(rt1)

    rt2 = _runtime(_ask_ir(), persist_dir)
    inst = rt2.executor.answer(room=None, text="不要", ask_id=instance_id)
    assert inst is not None
    assert rt2.adapters.get("mock").calls == []
    assert inst.state == DONE
    assert rt2.executor.pending_asks == {}


def test_room_scoped_ask_answerable_after_restart(tmp_path):
    """按房间挂的问题，重启后仍要能被同房间应答够到（room 来自当前图节点，不是内存残留）。"""
    persist_dir = tmp_path / "p"
    rt1 = _runtime(_ask_ir(room=True), persist_dir)
    _suspend_first(rt1)

    rt2 = _runtime(_ask_ir(room=True), persist_dir)
    sess = next(iter(rt2.executor.pending_asks.values()))
    assert sess.room == ROOM
    assert rt2.executor.answer(ROOM, "好") is not None
    assert [a for a, _ in rt2.adapters.get("mock").calls] == ["climate.turn_on"]


def test_expired_ask_timeout_clears_reseeded_session(tmp_path):
    """崩溃期间已错过的 ask 超时：恢复后 `tick()` 走 on_timeout ⇒ 会话随之消失，不留幽灵。"""
    persist_dir = tmp_path / "p"
    rt1 = _runtime(_ask_ir(timeout="30s"), persist_dir)
    instance_id = _suspend_first(rt1)

    rt2 = _runtime(_ask_ir(timeout="30s"), persist_dir)
    assert instance_id in rt2.executor.pending_asks
    rt2.advance(31)
    assert rt2.executor.pending_asks == {}
    assert rt2.adapters.get("mock").calls == []
    assert rt2.instances.get(instance_id).state == DONE


# ── 确认会话 ──


def test_confirm_session_reseeds_with_action_in_prompt(tmp_path):
    persist_dir = tmp_path / "p"
    rt1 = _runtime(_confirm_ir(), persist_dir)
    instance_id = _suspend_first(rt1)
    assert rt1.adapters.get("mock").calls == []

    rt2 = _runtime(_confirm_ir(), persist_dir)
    sess = rt2.executor.pending_asks[instance_id]
    assert sess.node_id == "d1"
    assert "climate.turn_on" in sess.prompt, "问句必须说清要下发的动作"
    assert rt2.adapters.get("mock").calls == []

    inst = rt2.executor.answer(room=None, text="好", ask_id=instance_id)
    assert inst is not None
    assert [a for a, _ in rt2.adapters.get("mock").calls] == ["climate.turn_on"]
    assert [e.node_id for e in rt2.audit.of_type(CONFIRM_GRANTED)] == ["d1"]


def test_confirm_denial_after_restart_still_lands_on_edge_discipline(tmp_path):
    persist_dir = tmp_path / "p"
    rt1 = _runtime(_confirm_ir(), persist_dir)
    instance_id = _suspend_first(rt1)

    rt2 = _runtime(_confirm_ir(), persist_dir)
    inst = rt2.executor.answer(room=None, text="不要", ask_id=instance_id)
    assert inst is not None
    assert rt2.adapters.get("mock").calls == []
    assert inst.state == DONE
    assert len(rt2.audit.of_type(CONFIRM_DENIED)) == 1


def test_confirm_session_inherits_persisted_ask_room(tmp_path):
    """链式 IR（ask room → 受确认 do）：`_ask_room` 随上下文落盘，重启后确认会话还在同房间。"""
    persist_dir = tmp_path / "p"
    rt1 = _runtime(_confirm_ir(room=True), persist_dir)
    _suspend_first(rt1)
    assert rt1.executor.answer(ROOM, "好") is not None
    assert rt1.adapters.get("mock").calls == [], "第一道门放行、第二道门仍挂着"

    rt2 = _runtime(_confirm_ir(room=True), persist_dir)
    sess = next(iter(rt2.executor.pending_asks.values()))
    assert sess.node_id == "d1"
    assert sess.room == ROOM, "确认会话的房间要能跟着实例跨重启"
    assert rt2.executor.answer(ROOM, "好") is not None
    assert [a for a, _ in rt2.adapters.get("mock").calls] == ["climate.turn_on"]


def test_confirm_prompt_follows_current_graph_not_the_stale_action(tmp_path):
    """停机期间 IR 改了动作：问句必须按**当前图**渲染，拿落盘的旧动作名问人等于说谎。"""
    persist_dir = tmp_path / "p"
    rt1 = _runtime(_confirm_ir(action="climate.turn_on"), persist_dir)
    instance_id = _suspend_first(rt1)

    rt2 = _runtime(_confirm_ir(action="climate.turn_off"), persist_dir)
    sess = rt2.executor.pending_asks[instance_id]
    assert "climate.turn_off" in sess.prompt
    assert "climate.turn_on" not in sess.prompt
    rt2.executor.answer(room=None, text="好", ask_id=instance_id)
    assert [a for a, _ in rt2.adapters.get("mock").calls] == ["climate.turn_off"]


# ── 不该重挂的 & 恢复不出会话的 ──


def test_wait_suspension_is_not_reseeded(tmp_path):
    """`wait` 无人在场语义：重挂只会造出一条谁也答不了的假问题。"""
    persist_dir = tmp_path / "p"
    rt1 = _runtime(_wait_ir(), persist_dir)
    instance_id = _suspend_first(rt1)

    rt2 = _runtime(_wait_ir(), persist_dir)
    assert rt2.executor.pending_asks == {}
    assert rt2.restored[0].instance_id == instance_id
    rt2.advance(601)
    assert rt2.restored[0].state == DONE


def test_unresolvable_suspended_node_audits_inst_of_silently_skipping(tmp_path):
    """挂起节点已不在当前图 ⇒ 建不出一条可应答会话，必须落具名审计而不是静默跳过。"""
    persist_dir = tmp_path / "p"
    rt1 = _runtime(_ask_ir(), persist_dir)
    instance_id = _suspend_first(rt1)

    # 停机期间把 q1 改名成 q2（自动化还在，挂起点却查不到节点）
    renamed = _ask_ir()
    renamed["nodes"][1]["id"] = "q2"
    renamed["edges"] = [
        {"from": "a1", "to": "q2", "kind": "then"},
        {"from": "q2", "to": "d1", "kind": "yes"},
        {"from": "q2", "to": "p1", "kind": "no"},
        {"from": "q2", "to": "p1", "kind": "default"},
        {"from": "d1", "to": "p1", "kind": "then"},
    ]
    rt2 = _runtime(renamed, persist_dir)

    assert rt2.executor.pending_asks == {}
    assert rt2.adapters.get("mock").calls == []
    lost = rt2.audit.of_type(INSTANCE_SESSION_LOST)
    assert len(lost) == 1
    assert lost[0].instance_id == instance_id
    assert lost[0].node_id == "q1"
    assert rt2.instances.get(instance_id).state == SUSPENDED, "恢复不出会话也不许顺手放行"
