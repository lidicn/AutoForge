"""af_instance 单测：状态机 / 快照 / vars / TTL。"""

from __future__ import annotations

import json

import pytest

from autoforge.af_bus import BusEvent
from autoforge.af_instance import (
    ACTIVE,
    CANCELLED,
    DONE,
    SUSPENDED,
    TRIGGER_FLAT_KEYS,
    IllegalTransition,
    InstanceManager,
)
from autoforge.af_ir import load_automation
from autoforge.af_state import InMemoryStateProvider

AUTO = {
    "ir_version": "0.2.1",
    "id": "demo",
    "name": "示例",
    "version": 1,
    "mode": "single",
    "vars": {"counter": {"type": "numeric", "value": 0}},
    "nodes": [
        {
            "id": "a1",
            "kind": "on",
            "trigger": {"type": "state", "entity_id": "binary_sensor.motion", "to": "on"},
        },
        {
            "id": "i1",
            "kind": "if",
            "expr": {
                "op": "lt",
                "left": {"var": "entity.sensor.illum", "type": "numeric"},
                "right": {"const": 200},
            },
        },
        {
            "id": "d1",
            "kind": "do",
            "adapter": "ha",
            "action": "light.turn_on",
            "params": {"entity_id": "light.main"},
        },
        {"id": "p1", "kind": "pass"},
    ],
    "edges": [
        {"from": "a1", "to": "i1", "kind": "then"},
        {"from": "i1", "to": "d1", "kind": "then"},
        {"from": "i1", "to": "p1", "kind": "no"},
        {"from": "d1", "to": "p1", "kind": "then"},
    ],
}


@pytest.fixture
def manager():
    states = InMemoryStateProvider()
    states.set_state("binary_sensor.motion", "on")
    states.set_state("sensor.illum", "80")
    states.set_state("light.main", "off")
    return InstanceManager(states)


def test_spawn_captures_snapshot(manager):
    auto = load_automation(AUTO)
    inst = manager.spawn(auto)
    assert inst.state == ACTIVE
    assert inst.ctx.current_node == "a1"
    assert inst.snapshot is not None and inst.snapshot.get("sensor.illum") == "80"
    assert inst.ctx.vars["counter"] == 0.0


def test_context_is_json_serializable(manager):
    inst = manager.spawn(load_automation(AUTO))
    payload = inst.to_dict()
    assert isinstance(payload, dict)
    json.dumps(payload)  # 不可序列化对象会在这里炸出来
    assert set(payload) >= {"instance_id", "automation_id", "state", "snapshot", "vars"}


def test_resume_creates_new_snapshot(manager):
    """恢复时**必须生成新快照并丢弃旧快照**（IR §7.1）。"""
    auto = load_automation(AUTO)
    inst = manager.spawn(auto)
    first = inst.snapshot
    manager.suspend(inst, "i1", duration_s=60)
    assert inst.state == SUSPENDED
    manager.resume(inst, next_node="d1")
    assert inst.state == ACTIVE
    assert inst.snapshot is not first  # 新对象
    assert inst.ctx.current_node == "d1"


def test_illegal_transition_raises(manager):
    inst = manager.spawn(load_automation(AUTO))
    manager.done(inst)
    assert inst.state == DONE
    with pytest.raises(IllegalTransition):
        manager.resume(inst)  # 终态不可恢复


def test_cancel_does_not_rollback(manager):
    """取消只停止流转，不回滚已执行动作（IR §13-1）。这里只验证状态语义。"""
    inst = manager.spawn(load_automation(AUTO))
    manager.cancel(inst, reason="人离开")
    assert inst.state == CANCELLED
    assert inst.ctx.context["cancel_reason"] == "人离开"


def test_suspended_instance_creates_timer(manager):
    inst = manager.spawn(load_automation(AUTO))
    manager.suspend(inst, "i1", duration_s=5)
    assert inst.ctx.timers[0]["node"] == "i1"
    assert inst.ctx.timers[0]["remaining_s"] == pytest.approx(5.0, abs=0.01)


def test_expire_stale(manager):
    manager.ttl_seconds = 0  # 立即可过期
    inst = manager.spawn(load_automation(AUTO))
    stale = manager.expire_stale()
    assert inst in stale
    assert inst.is_terminal


def test_vars_type_is_enforced(manager):
    inst = manager.spawn(load_automation(AUTO))
    manager.set_var(inst, "counter", "3")  # 声明 numeric，字符串数字可转
    assert inst.ctx.vars["counter"] == 3.0
    with pytest.raises(TypeError):
        manager.set_var(inst, "counter", "abc")


# ── 裁定 20261009 §四 甲：`context.trigger_*` 三枚平键 ──────────────────


def test_three_flat_keys_are_always_present(manager):
    """手动触发（不给事件）也要在场：`make_resolver` 对缺失的 `context.*` 抛「未知系统变量」，
    少一枚键就等于一条用了新词汇的自动化在手动那一档整段失败。"""
    ctx = manager.spawn(load_automation(AUTO)).ctx.context
    assert set(ctx) >= set(TRIGGER_FLAT_KEYS)
    assert [ctx[key] for key in TRIGGER_FLAT_KEYS] == ["", "", ""]


def test_state_event_fills_the_entity_and_leaves_the_old_shape(manager):
    inst = manager.spawn(load_automation(AUTO), trigger_event=BusEvent.of("binary_sensor.motion", "on"))
    ctx = inst.ctx.context
    assert ctx["trigger_entity_id"] == "binary_sensor.motion"
    assert ctx["trigger_subject"] == "binary_sensor.motion"
    assert ctx["trigger_kind"] == ""
    assert ctx["trigger"] == {"entity_id": "binary_sensor.motion", "state": "on"}


def test_custom_event_takes_the_contract_entity_id_not_the_bus_subject(manager):
    """`event.<名>` 是总线主体、不是设备，不许漏进平键；主标识按裁定落 `entity_id`。"""
    event = BusEvent.custom(
        "ma_device_health",
        {"kind": "device_health", "subject": "light.desk", "entity_id": "sensor.plug"},
    )
    ctx = manager.spawn(load_automation(AUTO), trigger_event=event).ctx.context
    assert ctx["trigger_entity_id"] == "sensor.plug"
    assert ctx["trigger_subject"] == "sensor.plug"
    assert ctx["trigger_kind"] == "device_health"


def test_presence_snapshot_has_no_device_entity(manager):
    event = BusEvent.custom("ma_presence", {"kind": "presence", "subject": "dad，mom"})
    ctx = manager.spawn(load_automation(AUTO), trigger_event=event).ctx.context
    assert ctx["trigger_entity_id"] == ""
    assert ctx["trigger_subject"] == "dad，mom"
    assert ctx["trigger_kind"] == "presence"
