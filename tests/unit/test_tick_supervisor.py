"""TickSupervisor 单测 — 验证 mimo 增量的异常分类/退避/SAFE HALT。"""
import threading
import time

import pytest

from autoforge.af_tick_supervisor import (
    ExponentialBackoff,
    FaultClass,
    TickStatus,
    TickSupervisor,
    default_fault_policy,
)
from autoforge.af_time import VirtualTimeSource


class FakeClock:
    """测试用时钟：monotonic 可手动推进。"""
    def __init__(self):
        self._mono = 0.0
        self._wall = 0.0

    def now(self):
        from datetime import datetime, timezone
        return datetime.fromtimestamp(self._wall, tz=timezone.utc)

    def monotonic(self):
        return self._mono

    def advance(self, s):
        self._mono += s
        self._wall += s


def test_ok_tick():
    """正常 tick：status=OK，计数正确。"""
    clock = FakeClock()
    sup = TickSupervisor(clock)
    outcome = sup.run_once(lambda: None)
    assert outcome.status is TickStatus.OK
    assert sup.health().ticks_total == 1
    assert sup.health().ok_total == 1
    assert sup.health().consecutive_failures == 0


def test_transient_fault_backoff():
    """TRANSIENT 异常：退避重试，status=RETRY。"""
    clock = FakeClock()
    sup = TickSupervisor(clock)
    calls = [0]

    def body():
        calls[0] += 1
        raise ConnectionError("network down")

    outcome = sup.run_once(body)
    assert outcome.status is TickStatus.RETRY
    assert outcome.fault is FaultClass.TRANSIENT
    assert outcome.delay > 0
    assert sup.health().consecutive_failures == 1


def test_degraded_fault_no_backoff():
    """DEGRADED 异常：不退避，status=DEGRADED。"""
    clock = FakeClock()
    sup = TickSupervisor(clock)

    def body():
        raise ValueError("bad data")

    outcome = sup.run_once(body)
    assert outcome.status is TickStatus.DEGRADED
    assert outcome.fault is FaultClass.DEGRADED
    assert outcome.delay == 0
    assert sup.health().degraded_total == 1
    assert sup.health().consecutive_failures == 0  # degraded 不计连续失败


def test_safe_halt_after_escalation():
    """连续 TRANSIENT 失败 20 次后 SAFE HALT。"""
    clock = FakeClock()
    policy = default_fault_policy()
    policy.escalate_after = 3  # 缩短到 3 次方便测试
    sup = TickSupervisor(clock, policy=policy)

    def body():
        raise ConnectionError("network down")

    # 前 2 次 RETRY
    for _ in range(2):
        outcome = sup.run_once(body)
        assert outcome.status is TickStatus.RETRY
    # 第 3 次 HALTED
    outcome = sup.run_once(body)
    assert outcome.status is TickStatus.HALTED
    assert sup.health().halted is True
    assert "escalated" in sup.health().halted_reason


def test_reset_after_halt():
    """SAFE HALT 后 reset 可恢复。"""
    clock = FakeClock()
    policy = default_fault_policy()
    policy.escalate_after = 2
    sup = TickSupervisor(clock, policy=policy)

    def body():
        raise ConnectionError("down")

    sup.run_once(body)
    sup.run_once(body)  # HALTED
    assert sup.health().halted

    sup.reset()
    assert not sup.health().halted
    assert sup.health().consecutive_failures == 0
    # reset 后正常 tick 可执行
    outcome = sup.run_once(lambda: None)
    assert outcome.status is TickStatus.OK


def test_health_state():
    """health_state 返回正确状态。"""
    clock = FakeClock()
    sup = TickSupervisor(clock)
    assert sup.health_state() == "ok"

    sup.run_once(lambda: (_ for _ in ()).throw(ConnectionError()))
    assert sup.health_state() == "backoff"

    sup.run_once(lambda: None)  # 成功一次
    assert sup.health_state() == "ok"


def test_exponential_backoff_increases():
    """退避延迟递增，有上限。"""
    back = ExponentialBackoff(base=0.5, factor=2.0, cap=10.0, jitter=0.0)
    d1 = back.next_delay()
    d2 = back.next_delay()
    d3 = back.next_delay()
    assert d1 < d2 < d3
    # 多次后达到 cap
    for _ in range(20):
        back.next_delay()
    assert back.next_delay() <= 10.0


def test_run_forever_stop():
    """run_forever 收到 stop 信号退出。"""
    clock = FakeClock()
    sup = TickSupervisor(clock, tick_interval_s=0.01)
    stop = threading.Event()
    calls = [0]

    def body():
        calls[0] += 1
        if calls[0] >= 3:
            stop.set()

    health = sup.run_forever(body, stop=stop)
    assert calls[0] >= 3
    assert health.ticks_total >= 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
