"""G5 故障注入验收：五类故障 + 四类失败落执行器断言（FakeHA 路径，本机可跑）。

对照 `docs/G1_ACCEPTANCE.md` 与 `af_fault.FOUR_FAILURES`：
- 五类故障：unavailable / timeout / drop / reorder / drift
- 四类失败（IR §9.4）：设备故障 / 超时 / 中断取消 / 业务拒绝
"""

from __future__ import annotations

import pytest

from autoforge.af_adapters import CallResult
from autoforge.af_audit import ACTION_FAILED, ENTITY_DRIFT
from autoforge.af_fault import inject_drift, inject_unavailable, reorder_events
from autoforge.af_instance import CANCELLED, DONE
from autoforge.af_ir import load_graph
from autoforge.af_runtime import build_runtime
from autoforge.af_vhass import FakeHAAdapter, seed_from_graph


def _make(graph_dict: dict, seed: dict[str, str]):
    """按 Graph dict 装配 Runtime（FakeHA 状态源 + 会真改状态的 ha 适配器）。"""
    graph = load_graph(graph_dict)
    runtime = build_runtime(graph)
    states = seed_from_graph(graph, seed, clock=runtime.clock)
    runtime.states = states
    runtime.instances.states = states
    runtime.scheduler.states = states
    runtime.executor.states = states  # canary 漂移检测必须读同一份状态源
    runtime.adapters.register(FakeHAAdapter(states))
    return runtime, states


# ─────────────────────────────────────────────────────────────────────
# 五类故障
# ─────────────────────────────────────────────────────────────────────


def _unavailable_graph() -> dict:
    return {
        "ir_version": "0.2.1",
        "id": "g5_unavail",
        "name": "g5_unavail",
        "version": 1,
        "mode": "single",
        "nodes": [
            {"id": "a1", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
            {
                "id": "i1",
                "kind": "if",
                "expr": {
                    "op": "lt",
                    "left": {"var": "entity.sensor.illum", "type": "numeric"},
                    "right": {"const": 200},
                },
            },
            {"id": "d1", "kind": "do", "adapter": "ha", "action": "light.turn_on", "params": {"entity_id": "light.x"}},
            {"id": "p1", "kind": "pass"},
            {"id": "pe", "kind": "pass"},
        ],
        "edges": [
            {"from": "a1", "to": "i1", "kind": "then"},
            {"from": "i1", "to": "d1", "kind": "then"},
            {"from": "i1", "to": "p1", "kind": "no"},
            {"from": "i1", "to": "pe", "kind": "on_error"},
            {"from": "d1", "to": "p1", "kind": "then"},
            {"from": "d1", "to": "p1", "kind": "on_error"},
        ],
    }


def test_fault_unavailable_goes_on_error_and_does_not_act():
    """实体掉线（unavailable）→ 表达式求值失败 → on_error 软失效，动作不下发。"""
    runtime, states = _make(
        _unavailable_graph(), {"binary_sensor.m": "off", "sensor.illum": "100", "light.x": "off"}
    )
    inject_unavailable(states, "sensor.illum")  # G5：传感器掉线

    runtime.emit("binary_sensor.m", "on")

    assert states.get("light.x") == "off", "不可用导致条件无法判定，不能开灯"
    assert any(ev.type == ENTITY_DRIFT for ev in runtime.audit), "应记录漂移/类型错误审计"


def _mock_do_graph() -> dict:
    return {
        "ir_version": "0.2.1",
        "id": "g5_mock_do",
        "name": "g5_mock_do",
        "version": 1,
        "mode": "single",
        "nodes": [
            {"id": "a1", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
            {"id": "d1", "kind": "do", "adapter": "mock", "action": "light.turn_on", "params": {"entity_id": "light.x"}},
            {"id": "p1", "kind": "pass"},
            {"id": "pe", "kind": "pass"},
        ],
        "edges": [
            {"from": "a1", "to": "d1", "kind": "then"},
            {"from": "d1", "to": "p1", "kind": "then"},
            {"from": "d1", "to": "pe", "kind": "on_error"},
        ],
    }


@pytest.mark.parametrize("fault_method", ["timeout_next", "drop_next", "fail_next"])
def test_fault_adapter_timeout_drop_device_error_go_to_on_error(fault_method):
    """传输层超时 / 消息丢包 / 设备报错 → 单次失败 → on_error（不重试不降级）。"""
    runtime, _ = _make(_mock_do_graph(), {"binary_sensor.m": "off", "light.x": "off"})
    getattr(runtime.adapters.get("mock"), fault_method)("注入")

    runtime.emit("binary_sensor.m", "on")

    assert any(ev.type == ACTION_FAILED for ev in runtime.audit), "设备故障应落 action_failed 审计"
    instance = runtime.instances.all()[0]
    assert instance.state == DONE, "有 on_error 兜底 → 软失效而非 failed"


def _two_auto_graph() -> dict:
    def auto(i: str) -> dict:
        return {
            "ir_version": "0.2.1",
            "id": f"auto_{i}",
            "name": f"auto_{i}",
            "version": 1,
            "mode": "single",
            "nodes": [
                {"id": "o", "kind": "on", "trigger": {"type": "state", "entity_id": f"binary_sensor.{i}", "to": "on"}},
                {"id": "d", "kind": "do", "adapter": "ha", "action": "light.turn_on", "params": {"entity_id": f"light.{i}"}},
                {"id": "p", "kind": "pass"},
            ],
            "edges": [
                {"from": "o", "to": "d", "kind": "then"},
                {"from": "d", "to": "p", "kind": "then"},
                {"from": "d", "to": "p", "kind": "on_error"},
            ],
        }

    return {"automations": [auto("a"), auto("b")]}


def test_fault_reorder_events_converges_to_same_state():
    """事件乱序重放：互不依赖的触发最终状态与顺序无关（去重/熔断兜底）。"""
    seed = {"binary_sensor.a": "off", "binary_sensor.b": "off", "light.a": "off", "light.b": "off"}
    events = [("binary_sensor.a", "on"), ("binary_sensor.b", "on")]

    for order_seed in (0, 1, 2, 99):
        runtime, states = _make(_two_auto_graph(), dict(seed))
        for entity, state in reorder_events(events, seed=order_seed):
            states.set(entity, state)
            runtime.emit(entity, state, last_changed=f"{entity}-{order_seed}")
        assert states.get("light.a") == "on" and states.get("light.b") == "on", "乱序下仍应全部生效"


def _canary_graph() -> dict:
    return {
        "ir_version": "0.2.1",
        "id": "g5_drift",
        "name": "g5_drift",
        "version": 1,
        "mode": "single",
        "confidence": 0.9,  # auto 带
        "nodes": [
            {"id": "o", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
            {
                "id": "d",
                "kind": "do",
                "adapter": "mock",  # 返回成功但不改状态 → 制造漂移
                "action": "light.turn_on",
                "params": {"entity_id": "light.x"},
                "canary": {"duration": "15m", "auto_rollback": True},
            },
            {"id": "e", "kind": "pass"},
            {"id": "err", "kind": "pass"},
        ],
        "edges": [
            {"from": "o", "to": "d", "kind": "then"},
            {"from": "d", "to": "e", "kind": "then"},
            {"from": "d", "to": "err", "kind": "on_error"},
        ],
    }


def test_fault_drift_triggers_canary_rollback():
    """状态漂移：动作后未达预期 → canary 检测 + 自动回滚（反向下发）。"""
    runtime, states = _make(_canary_graph(), {"binary_sensor.m": "off", "light.x": "off"})
    inject_drift(states, "light.x", "off")  # 目标停在非预期值

    runtime.emit("binary_sensor.m", "on")
    # P1-11：canary.duration 生效——推进时钟超过观察期
    runtime.advance(15 * 60 + 1)

    assert any(ev.type == ENTITY_DRIFT for ev in runtime.audit), "应记录漂移审计"
    calls = [(a, p) for a, p in runtime.adapters.get("mock").calls]
    assert ("light.turn_off", {"entity_id": "light.x"}) in calls, "应下发反向动作回滚"


# ─────────────────────────────────────────────────────────────────────
# 四类失败（IR §9.4）落执行器断言
# ─────────────────────────────────────────────────────────────────────


def test_failure_device_error_lands_on_error(make_harness):
    """设备故障 → on_error（对照 FOUR_FAILURES['device']）。"""
    harness = make_harness("case01_day_light.json", {"sensor.study_illum": "80", "light.study_main": "off"})
    harness.runtime.adapters.get("ha").fail_next("设备离线")

    harness.fire("binary_sensor.study_motion", "on")

    assert harness.get("light.study_main") == "off"
    assert any(ev.type == ACTION_FAILED for ev in harness.runtime.audit)


def test_failure_timeout_lands_on_timeout(make_harness):
    """超时（ask 60s 无应答）→ on_timeout 静默，实例不挂起。"""
    harness = make_harness(
        "case04_ask_timeout.json",
        {"sensor.study_temp": "28", "binary_sensor.study_door": "off", "climate.study": "off"},
    )
    harness.fire("sensor.study_temp", "28.5")
    harness.advance(61)

    assert harness.instances[0].state == DONE
    assert harness.ha_calls == [] and harness.get("climate.study") == "off"


def test_failure_cancel_lands_on_cancel_without_rollback(make_harness):
    """中断取消 → on_cancel，**已执行动作不回滚**（对照 FOUR_FAILURES['cancel']）。"""
    harness = make_harness("case05_ask_cancel.json", {"binary_sensor.study_motion": "off", "light.study_main": "off"})
    harness.fire("binary_sensor.study_motion", "on")
    instance = harness.instances[0]
    assert harness.get("light.study_main") == "on"

    harness.runtime.cancel(instance, reason="人离开")

    assert instance.state == CANCELLED
    assert harness.get("light.study_main") == "on", "取消不回滚：灯保持开着"
    assert [a for a, _ in harness.ha_calls] == ["light.turn_on"]


def test_failure_reject_lands_on_no_branch(make_harness):
    """业务拒绝（用户"不用了"）→ no 分支（非失败）。"""
    harness = make_harness(
        "case04_ask_timeout.json",
        {"sensor.study_temp": "28", "binary_sensor.study_door": "off", "climate.study": "off"},
    )
    harness.fire("sensor.study_temp", "28.5")

    answered = harness.runtime.answer("study", "不用了")

    assert answered is not None
    assert harness.get("climate.study") == "off" and harness.ha_calls == []


def test_drift_fault_result_carries_no_fault_marker():
    """对照：正常成功结果不带 fault 标记（确保断言用的标记只在故障时出现）。"""
    from autoforge.af_adapters import MockAdapter

    ok = MockAdapter().call("light.turn_on", {"entity_id": "light.x"})
    assert ok.success and "fault" not in ok.data
    assert isinstance(CallResult.ok(), CallResult)
