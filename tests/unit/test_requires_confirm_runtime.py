"""裁定 20261009 §三 硬前置：`requires_confirm` 的**运行期**消费者。

判据要钉住的六件事（每件都对应一种"看起来拦了、其实没拦"的形状）：
- 未确认 ⇒ 一条下发都没有（`requires_confirm` 不再是编译期独占的装饰）。
- yes ⇒ 只放行**这一次**下发；授权是一次性的，同实例再次走到同一节点还要再问。
- no / 超时 / 非法 timeout ⇒ 一律不下发（fail-closed），且异常不穿出求值段。
- 确认会话复用 ask 那条接缝 ⇒ `pending_asks` 里的形状不变（前端与管家侧照旧能答）。
- 预演档（dry_run 适配器）不挂起，但必须留痕 ⇒ 不拿"没拦"冒充"拦了"。
- 放行与拒绝各落一条**具名审计** ⇒ 拒绝不能只活在会被截断的 trace 里。
"""

from __future__ import annotations

from autoforge.af_audit import CONFIRM_DENIED, CONFIRM_GRANTED
from autoforge.af_bus import BusEvent
from autoforge.af_instance import DONE, FAILED, SUSPENDED
from autoforge.af_ir import Graph, load_automation
from autoforge.af_runtime import build_runtime

ROOM = "study"


def _auto(node_extra: dict | None = None, *, loop: bool = False) -> dict:
    d1 = {
        "id": "d1",
        "kind": "do",
        "name": "开空调",
        "adapter": "mock",
        "action": "climate.turn_on",
        "params": {"entity_id": "climate.study"},
        "requires_confirm": True,
        **(node_extra or {}),
    }
    nodes = [
        {
            "id": "a1",
            "kind": "on",
            "name": "有人进书房",
            "trigger": {"type": "state", "entity_id": "binary_sensor.study", "to": "on"},
        },
        d1,
        {"id": "p1", "kind": "pass", "name": "结束"},
    ]
    edges = [{"from": "a1", "to": "d1", "kind": "then"}]
    if loop:
        # d1 → set s1 → d1：同实例二次进入同一个受确认节点
        nodes.insert(-1, {"id": "s1", "kind": "set", "name": "计一次", "var": "rounds", "value": 1})
        edges += [{"from": "d1", "to": "s1", "kind": "then"}, {"from": "s1", "to": "d1", "kind": "then"}]
    else:
        edges += [{"from": "d1", "to": "p1", "kind": "then"}]
    return {
        "ir_version": "0.2.1",
        "id": "test_requires_confirm",
        "name": "requires_confirm 运行期用例",
        "version": 1,
        "mode": "restart",
        "nodes": nodes,
        "edges": edges,
    }


def _runtime(auto: dict):
    rt = build_runtime(Graph([load_automation(auto)]))
    rt.states.set_state("binary_sensor.study", "off")
    rt.publish(BusEvent.of("binary_sensor.study", "on"))
    return rt


def _inst(rt):
    return next(iter(rt.instances.all()))


# ── 未确认 ⇒ 零下发 ──

def test_requires_confirm_suspends_before_any_dispatch():
    rt = _runtime(_auto())
    inst = _inst(rt)
    assert inst.state == SUSPENDED
    assert len(rt.executor.pending_asks) == 1
    sess = next(iter(rt.executor.pending_asks.values()))
    assert sess.node_id == "d1"
    assert "climate.turn_on" in sess.prompt, "问句必须说清要下发的动作，否则确认是空的"
    assert sess.ask_spec is None, "确认会话是自由文本应答，不新造控件词汇"
    assert rt.adapters.get("mock").calls == []


def test_yes_dispatches_once_and_grant_is_consumed():
    rt = _runtime(_auto())
    inst = _inst(rt)
    answered = rt.executor.answer(room=None, text="好", ask_id=inst.instance_id)
    assert answered is not None
    assert [a for a, _ in rt.adapters.get("mock").calls] == ["climate.turn_on"]
    assert rt.executor.pending_asks == {}
    # 一次性授权：放行之后把手必须收走，不能留在上下文里等下次白拿
    assert "confirm_granted" not in inst.ctx.context
    assert "pending_confirm" not in inst.ctx.context
    # 具名审计：放行的这一次必须能对上节点与动作
    granted = rt.executor.audit.of_type(CONFIRM_GRANTED)
    assert [e.node_id for e in granted] == ["d1"]
    assert "climate.turn_on" in granted[0].message
    assert rt.executor.audit.of_type(CONFIRM_DENIED) == []


def test_grant_is_one_shot_so_second_entry_asks_again():
    rt = _runtime(_auto(loop=True))
    inst = _inst(rt)
    rt.executor.answer(room=None, text="好", ask_id=inst.instance_id)
    assert [a for a, _ in rt.adapters.get("mock").calls] == ["climate.turn_on"]
    # 绕回 d1：上一轮的授权已经用掉，这一轮必须重新问，第二次下发不能白拿
    assert inst.state == SUSPENDED
    assert len(rt.executor.pending_asks) == 1
    assert len(rt.adapters.get("mock").calls) == 1


def test_no_answer_never_dispatches():
    rt = _runtime(_auto())
    inst = _inst(rt)
    rt.executor.answer(room=None, text="不要", ask_id=inst.instance_id)
    assert rt.adapters.get("mock").calls == []
    assert rt.executor.pending_asks == {}
    assert inst.state == DONE
    denied = rt.executor.audit.of_type(CONFIRM_DENIED)
    assert [e.node_id for e in denied] == ["d1"]
    assert "climate.turn_on" in denied[0].message
    assert rt.executor.audit.of_type(CONFIRM_GRANTED) == []


def test_confirm_timeout_never_dispatches():
    rt = _runtime(_auto({"timeout": "30s"}))
    inst = _inst(rt)
    rt.advance(31)
    assert rt.adapters.get("mock").calls == []
    assert inst.state == DONE
    assert rt.executor.pending_asks == {}
    # 超时也是"未通过"，审计里必须点名是哪一种
    denied = rt.executor.audit.of_type(CONFIRM_DENIED)
    assert len(denied) == 1
    assert "on_timeout" in denied[0].message


def test_illegal_confirm_timeout_soft_fails_without_escaping():
    """timeout 写坏 ⇒ 走软失效，不许抛穿求值段，也不许"解析失败=不拦"地下发。"""
    rt = _runtime(_auto({"timeout": "abch"}))
    inst = _inst(rt)
    assert rt.adapters.get("mock").calls == []
    # 既有 `_soft_fail` 口径：图里没有 on_error/default 边 ⇒ failed（不是静默 done）
    assert inst.state == FAILED
    assert rt.executor.pending_asks == {}


# ── 会话形状与房间可达性 ──

def test_confirm_session_inherits_last_ask_room():
    """链式 IR（ask room=study → do 受确认）：第二道门必须还在同一个房间够得到。"""
    auto = _auto()
    auto["nodes"].insert(
        1,
        {
            "id": "q1",
            "kind": "ask",
            "name": "要不要开",
            "prompt": "书房热了，开空调吗？",
            "session": "room",
            "room": ROOM,
            "timeout": "60s",
        },
    )
    auto["edges"] = [
        {"from": "a1", "to": "q1", "kind": "then"},
        {"from": "q1", "to": "d1", "kind": "yes"},
        {"from": "q1", "to": "p1", "kind": "no"},
        {"from": "q1", "to": "p1", "kind": "default"},
        {"from": "d1", "to": "p1", "kind": "then"},
    ]
    rt = _runtime(auto)

    assert rt.executor.answer(ROOM, "好") is not None, "第一道门：按房间应答"
    assert rt.adapters.get("mock").calls == [], "第二道门没答，一条都不能发"
    sess = next(iter(rt.executor.pending_asks.values()))
    assert sess.node_id == "d1"
    assert sess.room == ROOM, "确认会话沿用本实例最近一次 ask 的 room"
    assert rt.executor.answer(ROOM, "好") is not None, "第二道门：同房间可达"
    assert [a for a, _ in rt.adapters.get("mock").calls] == ["climate.turn_on"]


# ── 预演档：不挂起，但留痕 ──

def test_dry_run_adapter_skips_gate_but_records_trace():
    """默认 `build_runtime` 的 ha 适配器是 dry_run：本来就不上线，不拿确认冒充拦得住。"""
    auto = _auto()
    auto["nodes"][1]["adapter"] = "ha"
    rt = _runtime(auto)
    inst = _inst(rt)
    assert rt.executor.pending_asks == {}, "预演档不挂起，否则仿真/预演跑不动"
    assert rt.adapters.get("ha").intents == [("climate.turn_on", {"entity_id": "climate.study"})]
    notes = [t["note"] for t in inst.ctx.trace]
    assert "confirm_skipped_dry_run" in notes, "跳过必须留痕，不能与'拦了'长得一样"


# ── 非受确认节点不受影响（门的边界）──

def test_node_without_requires_confirm_dispatches_immediately():
    auto = _auto()
    auto["nodes"][1].pop("requires_confirm")
    rt = _runtime(auto)
    assert [a for a, _ in rt.adapters.get("mock").calls] == ["climate.turn_on"]
    assert rt.executor.pending_asks == {}
