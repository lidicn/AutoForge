"""FireRecorder 单测 — 验证当日一次记账落盘 + 防重启重放。"""
import json
import tempfile
from pathlib import Path

import pytest

from autoforge.af_fire_recorder import FireRecorder, JsonFireStore
from autoforge.af_time import VirtualTimeSource


class FakeClock:
    def __init__(self):
        self._mono = 1000.0
        self._wall = 0.0

    def now(self):
        from datetime import datetime, timezone, timedelta
        return datetime(2026, 9, 22, 10, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=self._wall)

    def monotonic(self):
        return self._mono

    def advance(self, s):
        self._mono += s
        self._wall += s


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def store(tmp_dir):
    return JsonFireStore(tmp_dir)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def recorder(store, clock):
    return FireRecorder(store, clock, max_attempts_per_day=3, lease_s=5.0, keep_days=1)


def test_claim_and_confirm(recorder):
    """正常触发：claim → confirm → 当天不再触发。"""
    lease = recorder.try_begin("rule1")
    assert lease is not None
    lease.confirm("inst-1")
    # 当天已触发，再 claim 返回 None
    assert recorder.try_begin("rule1") is None
    assert recorder.fired_today("rule1")


def test_failed_claim_can_retry(recorder):
    """触发失败（release）：当天可重试，attempts 计数。"""
    # 第一次失败
    lease1 = recorder.try_begin("rule1")
    assert lease1 is not None
    lease1.release()
    # 第二次可以重试
    lease2 = recorder.try_begin("rule1")
    assert lease2 is not None
    lease2.confirm("inst-2")
    # confirm 后不再触发
    assert recorder.try_begin("rule1") is None


def test_exhausted_after_max_attempts(recorder):
    """达到 max_attempts 后不再触发。"""
    for i in range(3):
        lease = recorder.try_begin("rule1")
        assert lease is not None, f"attempt {i+1} should succeed"
        lease.release()  # 每次都失败
    # 第 4 次应该 exhausted
    assert recorder.try_begin("rule1") is None


def test_persistence_across_reload(tmp_dir, clock):
    """落盘后重新加载，fired 状态保留（防重启重放）。"""
    store1 = JsonFireStore(tmp_dir)
    rec1 = FireRecorder(store1, clock, max_attempts_per_day=3)
    lease = rec1.try_begin("rule1")
    assert lease is not None
    lease.confirm("inst-1")

    # 模拟重启：新建 store，从文件加载
    store2 = JsonFireStore(tmp_dir)
    rec2 = FireRecorder(store2, clock, max_attempts_per_day=3)
    assert rec2.fired_today("rule1")
    assert rec2.try_begin("rule1") is None


def test_crash_recovery_release(tmp_dir, clock):
    """崩溃在 confirm 之前（state=claimed）：重启后默认可重试。"""
    store1 = JsonFireStore(tmp_dir)
    rec1 = FireRecorder(store1, clock, max_attempts_per_day=3, lease_s=5.0)
    lease = rec1.try_begin("rule1")
    assert lease is not None
    # 不 confirm，模拟崩溃

    # 重启后：claimed 状态，lease 已过期 → 可重新 claim
    clock.advance(10)  # 超过 lease_s
    store2 = JsonFireStore(tmp_dir)
    rec2 = FireRecorder(store2, clock, max_attempts_per_day=3)
    lease2 = rec2.try_begin("rule1")
    assert lease2 is not None  # 可以重试


def test_sweep_old_days(recorder, store, clock):
    """跨日清理：保留 keep_days 天。"""
    # 触发一条
    lease = recorder.try_begin("rule1")
    lease.confirm("inst-1")
    assert store.count() == 1

    # 推进 2 天，sweep 应该清掉
    clock.advance(86400 * 2)
    removed = recorder.sweep(force=True)
    assert removed >= 1
    assert store.count() == 0


def test_fire_exactly_once(recorder):
    """fire_exactly_once 便捷方法。"""
    calls = []
    def do():
        calls.append(1)
        return "inst-x"

    assert recorder.fire_exactly_once("rule1", do) is True
    assert len(calls) == 1
    # 第二次跳过
    assert recorder.fire_exactly_once("rule1", do) is False
    assert len(calls) == 1


def test_fire_exactly_once_failure_retryable(recorder):
    """fire_exactly_once 中 do() 抛异常 → release，可重试。"""
    call_count = [0]
    def failing_do():
        call_count[0] += 1
        raise RuntimeError("device offline")

    with pytest.raises(RuntimeError):
        recorder.fire_exactly_once("rule1", failing_do)
    assert call_count[0] == 1

    # 可以重试
    def success_do():
        call_count[0] += 1
        return "inst-y"
    assert recorder.fire_exactly_once("rule1", success_do) is True
    assert call_count[0] == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


def test_day_bucket_uses_local_calendar_day():
    """审计 2026-10-28：旧实现按 `now().date()`（UTC）分桶，调度器判"当日"用本地日期。

    UTC+8 下本地 01:00 = UTC 前一日 17:00 ⇒ 落盘记录进**前一天**的桶；该窗口内重启时
    `recover()` 认不到当天记录，已执行过的触发可能被重放。这里钉两把尺子同口径。
    """
    from datetime import date, datetime, timedelta, timezone

    class OffsetClock:
        """now()=UTC、local_now()=本地（UTC+8）：与 SystemTimeSource 同形状。"""
        def __init__(self):
            self._utc = datetime(2026, 9, 22, 17, 0, tzinfo=timezone.utc)  # 本地已是 09-23 01:00
        def now(self): return self._utc
        def local_now(self): return self._utc + timedelta(hours=8)
        def today(self): return self.local_now().date()
        def monotonic(self): return 1000.0
        def advance(self, s): pass

    with tempfile.TemporaryDirectory() as d:
        store = JsonFireStore(Path(d))
        rec = FireRecorder(store, OffsetClock(), max_attempts_per_day=3)
        lease = rec.try_begin("rule-x")
        assert lease is not None
        assert lease.day == "2026-09-23", f"当日桶该是本地日（UTC+8 的 01:00），实得 {lease.day}"
        lease.confirm(inst_id="i1")
        # 同一本地日内再 claim 必须被拒：UTC 日界（09-22）落在桶名之外，
        # 若仍用 UTC 日期，这里会被放行为"新的一天"＝重放已执行过的触发。
        assert rec.try_begin("rule-x") is None, "确认过的当日再触发被放行＝日桶尺子不一致"
        assert rec.fired_today("rule-x") is True
