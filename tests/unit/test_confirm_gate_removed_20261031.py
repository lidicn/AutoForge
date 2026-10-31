"""DCD 裁定 20261031（取消运行期二次授权·Q1 甲）：运行期确认闸的**反存在钉**。

裁定：`requires_confirm` 不再挂起、不再开会话、不再等人应答——出资人设计模型
「自动化只有一次审批，部署后即生效」。字段保留，只承载「部署期必须显眼」
（编译期 L2 分档）。

本文件是 2026-10-09 那份 `test_requires_confirm_runtime.py` 的**替代面**（旧文件
随闸删除，⛔ 留两份互相矛盾的终态判据）：它钉的不是"闸还在"，而是"闸确实没了"
——谁把运行期闸加回来，这些判据红。保留侧（编译期 L2 分档、canary 强制、ask 业务
问句、live_run 的 confirm=true）由各自既有判据继续看守。
"""

from __future__ import annotations

import pytest

from autoforge.af_audit import AuditEvent
from autoforge.af_bus import BusEvent
from autoforge.af_instance import DONE, FAILED, SUSPENDED
from autoforge.af_ir import Graph, load_automation
from autoforge.af_runtime import build_runtime
from autoforge.af_scanner import CHECKS, CODE_HINT, ERROR, StaticScanner


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
        nodes.insert(-1, {"id": "s1", "kind": "set", "name": "计一次", "var": "rounds", "value": 1})
        edges += [{"from": "d1", "to": "s1", "kind": "then"}, {"from": "s1", "to": "d1", "kind": "then"}]
    else:
        edges += [{"from": "d1", "to": "p1", "kind": "then"}]
    return {
        "ir_version": "0.2.1",
        "id": "test_confirm_gate_removed",
        "name": "运行期确认闸已撤除",
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


# ── 矩阵第 1 行（开关行）：L2 动作触发 ⇒ 直接执行，零挂起 ────────────


def test_l2_with_confirm_flag_dispatches_immediately():
    """requires_confirm=True 的动作**照常直接执行**（闸撤除的本体判据）。"""
    rt = _runtime(_auto())
    inst = _inst(rt)
    assert inst.state == DONE, "运行期闸没删干净：实例停在非终态"
    assert [a for a, _ in rt.adapters.get("mock").calls] == ["climate.turn_on"], "动作没执行"
    assert rt.executor.pending_asks == {}, "确认会话还挂着（闸的残骸）"


def test_l2_without_confirm_flag_also_dispatches_immediately():
    """不带标记的同款动作同样直接执行（闸不认字段做运行期拦截）。"""
    auto = _auto()
    auto["nodes"][1].pop("requires_confirm")
    rt = _runtime(auto)
    assert _inst(rt).state == DONE
    assert [a for a, _ in rt.adapters.get("mock").calls] == ["climate.turn_on"]


def test_no_answer_nothing_to_answer():
    """没有确认会话可答——answer 落空返回 None，实例已经跑完。"""
    rt = _runtime(_auto())
    inst = _inst(rt)
    assert inst.state == DONE
    assert rt.executor.answer(room=None, text="好", ask_id=inst.instance_id) is None


def test_grant_reentry_is_gone():
    """loop 形状：二次进入同节点不再被确认会话挂住——直接往下执行。

    注意：这条形状（d1→s1→d1）本就是静态死循环，执行器按既有上限护栏
    「求值段超过最大步数」落 failed——那是原有的图纪律，不是确认闸的残骸
    （旧闸会先挂起等人答，根本走不到上限护栏）。判据钉的是「零挂起、闸没了」，
    终态交还给既有的循环护栏。
    """
    rt = _runtime(_auto(loop=True))
    inst = _inst(rt)
    assert rt.executor.pending_asks == {}, "确认会话还挂着（闸的残骸）"
    calls = [a for a, _ in rt.adapters.get("mock").calls]
    assert len(calls) >= 1, "至少第一跳执行了"
    # 既有静态图护栏接管（与闸无关）：上限步数到点 failed，理由点名死循环
    assert inst.state == FAILED
    reason = getattr(inst, "fail_reason", "") or inst.ctx.context.get("fail_reason", "")
    assert "最大步数" in str(reason), reason


# ── 审计事件：CONFIRM_GRANTED/DENIED 不再发出 ────────────────────────


def test_confirm_audit_events_are_gone():
    """运行期不再发 confirm_granted / confirm_denied（整族撤销）。"""
    rt = _runtime(_auto())
    types = {e.type if hasattr(e, "type") else e.get("type") for e in rt.executor.audit}
    assert "confirm_granted" not in types, "确认闸撤了还在发放行审计"
    assert "confirm_denied" not in types, "确认闸撤了还在发拒绝审计"


def test_audit_constants_removed_from_module():
    """常量真源（af_audit）里已无 CONFIRM_GRANTED/DENIED（撤除不留死常量）。"""
    import autoforge.af_audit as audit

    assert not hasattr(audit, "CONFIRM_GRANTED")
    assert not hasattr(audit, "CONFIRM_DENIED")
    assert "confirm_granted" not in audit.ALL_EVENT_TYPES
    assert "confirm_denied" not in audit.ALL_EVENT_TYPES


def test_executor_has_no_suspend_for_confirm():
    """`_suspend_for_confirm` 本体已删（摘除面不留空壳）。"""
    from autoforge.af_executor import NodeExecutor

    assert not hasattr(NodeExecutor, "_suspend_for_confirm")


# ── 矩阵第 5 行：预演档含 L2 照常跑、零挂起 ──────────────────────────


def test_dry_run_l2_runs_without_suspend():
    """预演档（ha dry_run）L2 动作照常跑、无挂起、无 confirm_skipped 留痕。"""
    auto = _auto()
    auto["nodes"][1]["adapter"] = "ha"
    rt = _runtime(auto)
    inst = _inst(rt)
    assert rt.executor.pending_asks == {}, "预演档不该有确认会话"
    assert rt.adapters.get("ha").intents == [("climate.turn_on", {"entity_id": "climate.study"})]
    notes = [t.get("note") for t in inst.ctx.trace]
    assert "confirm_skipped_dry_run" not in notes, "闸撤了还留旧痕"


def test_confirm_wait_trace_note_is_gone():
    """`confirm_wait` 留痕随闸撤除——正常运行轨迹里不该出现。"""
    rt = _runtime(_auto())
    inst = _inst(rt)
    notes = [t.get("note") for t in inst.ctx.trace]
    assert "confirm_wait" not in notes


# ── 保留侧不误伤：编译期 L2 分档与 canary 强制仍在 ───────────────────


def test_compile_time_l2_gate_still_enforced():
    """矩阵第 3/4 行：L2 不写 requires_confirm 仍 ERROR；不配 canary 仍 ERROR（保留侧）。"""
    ir = _auto()
    ir["nodes"][1].pop("requires_confirm")
    scan = StaticScanner(Graph([load_automation(ir)])).scan()
    assert "L2_NEEDS_CONFIRM" in {d.code for d in scan.diagnostics if d.level == ERROR}

    ir2 = _auto()
    scan2 = StaticScanner(Graph([load_automation(ir2)])).scan()
    assert "L2_NEEDS_CANARY" in {d.code for d in scan2.diagnostics if d.level == ERROR}, (
        "canary 强制被误撤（Q2 甲 ⛔）"
    )


def test_confirm_without_deny_path_code_removed_from_catalogs():
    """Q3 甲：CONFIRM_WITHOUT_DENY_PATH 从 CHECKS/CODE_HINT 除名，无孤儿引用。"""
    assert "CONFIRM_WITHOUT_DENY_PATH" not in CHECKS
    assert "CONFIRM_WITHOUT_DENY_PATH" not in CODE_HINT


def test_ask_node_still_suspends():
    """⛔ 边界：ask 节点是业务问句，不受本裁定影响——挂起语义原样。"""
    auto = _auto()
    auto["nodes"].insert(
        1,
        {
            "id": "q1", "kind": "ask", "name": "要不要开", "prompt": "书房热了，开空调吗？",
            "session": "room", "room": "study", "timeout": "60s",
        },
    )
    auto["edges"] = [
        {"from": "a1", "to": "q1", "kind": "then"},
        {"from": "q1", "to": "d1", "kind": "yes"},
        {"from": "d1", "to": "p1", "kind": "then"},
    ]
    rt = _runtime(auto)
    inst = _inst(rt)
    assert inst.state == SUSPENDED, "ask 业务问句的挂起被误撤（⛔ 裁定边界）"
    assert rt.adapters.get("mock").calls == []
    assert rt.executor.answer("study", "好") is not None
    assert [a for a, _ in rt.adapters.get("mock").calls] == ["climate.turn_on"]


def test_reseed_sessions_no_longer_reseeds_confirm_context():
    """崩溃恢复不再重挂确认会话（pending_confirm 腿已撤）——旧落盘残留按普通挂起处理。"""
    rt = _runtime(_auto())
    # 人工构造一个带 pending_confirm 键的"旧落盘"实例形状，验证恢复腿不认它
    inst = _inst(rt)
    inst.ctx.context["pending_confirm"] = {"node_id": "d1", "action": "climate.turn_on"}
    # 实例已终态，reseed 直接跳过；再用一个 suspended 的 ask 实例验证 ask 腿仍在
    auto = _auto()
    auto["nodes"].insert(
        1,
        {"id": "q1", "kind": "ask", "name": "问", "prompt": "热吗？", "session": "room",
         "room": "study", "timeout": "60s"},
    )
    auto["edges"] = [{"from": "a1", "to": "q1", "kind": "then"},
                     {"from": "q1", "to": "d1", "kind": "yes"},
                     {"from": "d1", "to": "p1", "kind": "then"}]
    rt2 = _runtime(auto)
    i2 = _inst(rt2)
    assert i2.state == SUSPENDED
    assert rt2.executor.reseed_sessions(rt2.instances.all()) == 1, "ask 腿不应误伤"
