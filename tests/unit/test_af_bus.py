"""af_bus 单测：去重 / 节流 / 熔断 / 精确订阅。"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from autoforge.af_audit import BREAKER_OPEN, BREAKER_RECOVER
from autoforge.af_bus import (
    ACCEPTED,
    BREAKER_BLOCKED,
    DUPLICATE,
    THROTTLED,
    BusEvent,
    EventBus,
)
from autoforge.af_time import VirtualTimeSource


def _bus(**kw) -> EventBus:
    clock = VirtualTimeSource(datetime(2026, 9, 14, 8, 0, tzinfo=timezone.utc))
    bus = EventBus(clock, **kw)
    bus._clock = clock  # 测试里要手动推进时间
    return bus


def _event(entity="binary_sensor.x", state="on", last_changed="t1") -> BusEvent:
    return BusEvent.of(entity, state, last_changed=last_changed)


# ── 去重 ──────────────────────────────────────────────────────────────


def test_duplicate_is_dropped():
    bus = _bus()
    assert bus.publish(_event(state="on", last_changed="t1")) == ACCEPTED
    assert bus.publish(_event(state="on", last_changed="t1")) == DUPLICATE
    assert bus.counts[ACCEPTED] == 1


def test_same_state_different_timestamp_is_not_duplicate():
    bus = _bus()
    bus.publish(_event(state="on", last_changed="t1"))
    bus.clock.advance(1.0)
    # 节流窗口 200ms，推进 1s 后放行
    assert bus.publish(_event(state="on", last_changed="t2")) == ACCEPTED


# ── 节流 ──────────────────────────────────────────────────────────────


def test_throttle_merges_burst():
    bus = _bus(throttle_ms=200)
    # 同状态突发（抖动/双报）：窗口内重复 on → 节流合并
    assert bus.publish(_event(state="on", last_changed="t1")) == ACCEPTED
    assert bus.publish(_event(state="on", last_changed="t2")) == THROTTLED
    bus.clock.advance(0.3)
    assert bus.publish(_event(state="on", last_changed="t3")) == ACCEPTED
    # 真实状态跳变（on→off）即便在窗口内也必须放行，不得误杀触发事件
    assert bus.publish(_event(state="off", last_changed="t4")) == ACCEPTED


# ── 熔断 ──────────────────────────────────────────────────────────────


def test_breaker_opens_after_burst_and_recovers():
    bus = _bus(breaker_threshold=5, breaker_window_s=10, breaker_cooldown_s=30)
    for i in range(5):
        bus.clock.advance(0.5)
        bus.publish(_event(state="on" if i % 2 == 0 else "off", last_changed=f"t{i}"))
    assert "binary_sensor.x" in bus.open_entities()
    assert bus.counts[BREAKER_BLOCKED] >= 1

    events = [e for e in bus.audit if e.type == BREAKER_OPEN]
    assert len(events) == 1 and events[0].entity_id == "binary_sensor.x"

    bus.clock.advance(31)
    # P2-7：is_open 现已改为纯查询（不再顺带治愈），恢复是显式副作用，
    # 由 publish 路径的 _maybe_recover 或显式调用触发。
    bus._maybe_recover("binary_sensor.x")
    assert bus.is_open("binary_sensor.x") is False
    assert any(e.type == BREAKER_RECOVER for e in bus.audit)


def test_breaker_is_per_entity():
    bus = _bus(breaker_threshold=5)
    for i in range(5):
        bus.clock.advance(0.5)
        bus.publish(_event(entity="binary_sensor.noisy", last_changed=f"t{i}"))
    bus.clock.advance(1.0)
    # 另一个实体不受影响
    assert bus.publish(_event(entity="binary_sensor.calm", last_changed="t9")) == ACCEPTED


# ── 订阅 ──────────────────────────────────────────────────────────────


def test_subscribe_by_entity_and_by_source():
    bus = _bus()
    seen: list[BusEvent] = []
    bus.subscribe("light.a", seen.append)
    bus.subscribe("manual", seen.append)

    bus.publish(BusEvent.of("light.a", "on", source="ha", last_changed="t1"))
    bus.publish(BusEvent.of("light.b", "on", source="manual", last_changed="t2"))
    bus.publish(BusEvent.of("light.c", "on", source="ha", last_changed="t3"))  # 无人订阅

    assert len(seen) == 2
    assert {e.entity_id for e in seen} == {"light.a", "light.b"}


def test_no_wildcard_subscription():
    """精确订阅：订阅 `light.*` 不会匹配任何实体。"""
    bus = _bus()
    seen: list[BusEvent] = []
    bus.subscribe("light.*", seen.append)
    bus.publish(BusEvent.of("light.a", "on", last_changed="t1"))
    assert seen == []


# ── 自定义事件（emit 广播）豁免熔断 / 节流 ──────────────────────────────


def test_custom_event_bypasses_breaker_and_throttle():
    """emit 广播是内部信号（event_id 去重、速率受外部事件上限），不参与熔断/节流。

    否则自动化联动风暴（A 开→emit→B 跟→B 状态变更→…）会在用户连按 / 继电器抖动时
    触发熔断 / 节流**自伤**，阻断合法同步（2026-09-15 真机实测修复）。
    """
    bus = _bus(breaker_threshold=3, breaker_window_s=10, breaker_cooldown_s=30, throttle_ms=200)
    # 连续 10 次 emit 同一事件（虚拟时间不推进，状态事件会被熔断/节流）
    evs = [BusEvent.custom(f"ceiling_on_{i}", {"i": i}) for i in range(10)]
    results = [bus.publish(e) for e in evs]
    assert all(r == ACCEPTED for r in results), results
    assert bus.counts[BREAKER_BLOCKED] == 0
    assert bus.counts[THROTTLED] == 0
    assert bus.counts[ACCEPTED] == 10
    # 熔断仍对实体状态事件生效（护栏本身没坏）
    for i in range(3):
        bus.clock.advance(0.5)
        bus.publish(_event(entity="switch.noisy", state="on" if i % 2 == 0 else "off", last_changed=f"s{i}"))
    assert "switch.noisy" in bus.open_entities()
