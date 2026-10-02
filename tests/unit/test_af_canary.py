"""G4 canary 执行保护单测。"""

from __future__ import annotations

from autoforge.af_adapters import CallResult
from autoforge.af_adapters.mock import MockAdapter
from autoforge.af_audit import ENTITY_DRIFT
from autoforge.af_bus import BusEvent
from autoforge.af_canary import CanaryGuard, inverse_action
from autoforge.af_ir import load_graph
from autoforge.af_runtime import build_runtime
from autoforge.af_state import InMemoryStateProvider
from autoforge.af_vhass.fake import FakeHA, FakeHAAdapter


def test_inverse_action_mapping():
    assert inverse_action("light.turn_on") == "light.turn_off"
    assert inverse_action("light.turn_off") == "light.turn_on"
    assert inverse_action("switch.turn_on") == "switch.turn_off"
    assert inverse_action("lock.lock") == "lock.unlock"
    assert inverse_action("cover.open_cover") == "cover.close_cover"


def test_perform_captures_pre_state():
    ha = FakeHA().seed({"light.x": "off"})
    adapter = FakeHAAdapter(ha)
    guard = CanaryGuard(ha)
    res = guard.perform(adapter, "light.turn_on", {"entity_id": "light.x"})
    assert res.pre_states["light.x"] == "off"
    assert ha.get("light.x") == "on"  # 动作生效
    assert not res.has_drift()        # 符合预期 → 无漂移


def test_drift_detected_when_state_unchanged():
    states = InMemoryStateProvider()
    states.set_state("light.x", "off")
    # 适配器返回成功，但**不改状态**（模拟设备没反应）
    adapter = MockAdapter()
    guard = CanaryGuard(states)
    res = guard.perform(adapter, "light.turn_on", {"entity_id": "light.x"})
    assert res.has_drift()            # 预期 on，实际仍 off
    rolled = res.rollback(adapter)
    assert len(rolled) == 1
    # 回滚动作是反向 light.turn_off
    assert adapter.calls[-1][0] == "light.turn_off"


def test_auto_rollback_issues_inverse():
    states = InMemoryStateProvider()
    states.set_state("light.x", "off")
    adapter = MockAdapter()
    guard = CanaryGuard(states, auto_rollback=True)
    res = guard.perform(adapter, "light.turn_on", {"entity_id": "light.x"})
    rolled = guard.check_and_rollback(adapter, res)
    assert len(rolled) == 1
    assert adapter.calls[-1][0] == "light.turn_off"


def test_no_rollback_when_nothing_drifts():
    states = InMemoryStateProvider()
    states.set_state("light.x", "off")
    adapter = MockAdapter()
    guard = CanaryGuard(states)
    res = guard.perform(adapter, "light.turn_on", {"entity_id": "light.x"})
    # 即便预期与实际不符，显式关闭 auto_rollback 也不回滚
    guard.auto_rollback = False
    assert guard.check_and_rollback(adapter, res) == []


def test_executor_canary_rolls_back_on_drift():
    """集成：conf 处于 auto 带、节点标 canary，设备没反应 → 触发漂移回滚。"""
    graph = load_graph(
        {
            "ir_version": "0.2.1",
            "id": "drive",
            "name": "drive",
            "version": 1,
            "mode": "single",
            "confidence": 0.9,  # auto 带
            "nodes": [
                {"id": "o", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
                {
                    "id": "d",
                    "kind": "do",
                    "adapter": "mock",  # MockAdapter 返回成功但不改状态 → 必漂移
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
    )
    states = InMemoryStateProvider()
    states.set_state("binary_sensor.m", "off")
    states.set_state("light.x", "off")
    runtime = build_runtime(graph, states=states)
    runtime.publish(BusEvent.of("binary_sensor.m", "on"))  # 触发 → do 节点挂起观察 15m
    # P1-11：canary.duration 生效——推进时钟超过观察期，触发漂移检查
    runtime.advance(15 * 60 + 1)

    # 漂移被检测到并审计
    assert any(ev.type == ENTITY_DRIFT for ev in runtime.audit)
    # MockAdapter 收到了反向动作 light.turn_off
    assert ("light.turn_off", {"entity_id": "light.x"}) in [
        (c[0], c[1]) for c in runtime.adapters.get("mock").calls
    ]


def test_executor_skips_canary_when_low_confidence():
    """对照：conf < 0.6 (ask 带) 时即便标了 canary，也不走灰度保护。"""
    graph = load_graph(
        {
            "ir_version": "0.2.1",
            "id": "drive",
            "name": "drive",
            "version": 1,
            "mode": "single",
            "confidence": 0.4,  # ask 带
            "nodes": [
                {"id": "o", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
                {
                    "id": "d",
                    "kind": "do",
                    "adapter": "mock",
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
    )
    states = InMemoryStateProvider()
    states.set_state("binary_sensor.m", "off")
    states.set_state("light.x", "off")
    runtime = build_runtime(graph, states=states)
    runtime.publish(BusEvent.of("binary_sensor.m", "on"))

    # ask 带：不进入 canary 保护，因此无漂移审计
    assert not any(ev.type == ENTITY_DRIFT for ev in runtime.audit)


# ── 第七轮审计：StateProvider 统一 fail-closed 后，canary 两处读取的落点 ──────

def test_has_drift_reads_unreadable_target_as_drift():
    """动作后目标实体读不到（漂移/改名）→ 判**有漂移**，不是抛穿、更不是"没漂"。

    铁律 #5：EXEMPT ≠ VERIFIED。读不到实际态就没有"符合预期"的证据。
    """
    states = InMemoryStateProvider()
    states.set_state("light.x", "off")
    adapter = MockAdapter()
    guard = CanaryGuard(states)
    res = guard.perform(adapter, "light.turn_on", {"entity_id": "light.x"})
    states.states.pop("light.x")  # 设备在动作之后从 HA 消失
    assert res.has_drift() is True   # 旧行为：抛 UnknownEntity 穿出 step()


def test_executor_soft_fails_when_pre_snapshot_missing():
    """取不到动作前快照 → 一条动作都不许下发，走软失效记 entity_drift。

    `perform` 在**下发之前**取快照，所以这里 mock 适配器必须一次都没被调用；
    异常也不许穿出 `publish`（穿出就是整次 tick 陪葬，第二轮审计那一类）。
    """
    graph = load_graph(
        {
            "ir_version": "0.2.1",
            "id": "drive",
            "name": "drive",
            "version": 1,
            "mode": "single",
            "confidence": 0.9,  # auto 带 → 进 canary 保护
            "nodes": [
                {"id": "o", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
                {
                    "id": "d",
                    "kind": "do",
                    "adapter": "mock",
                    "action": "light.turn_on",
                    "params": {"entity_id": "light.ghost"},  # 状态源里没有这个实体
                    "canary": {"auto_rollback": True},
                    "on_error": {"default": "err"},
                },
                {"id": "err", "kind": "pass"},
            ],
            "edges": [{"from": "o", "to": "d", "kind": "then"}],
        }
    )
    states = InMemoryStateProvider()
    states.set_state("binary_sensor.m", "off")
    runtime = build_runtime(graph, states=states)
    runtime.publish(BusEvent.of("binary_sensor.m", "on"))  # 不许抛

    assert any(ev.type == ENTITY_DRIFT for ev in runtime.audit)
    assert runtime.adapters.get("mock").calls == [], "取不到回滚把手就不该下发"
