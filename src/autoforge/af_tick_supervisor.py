"""TickSupervisor —— 把「tick 不许死」升级成「tick 不许假活」。

源自 mimo 旗舰大模型设计（E:\\NAS\\FFL\\mimo_af_output\\07_tick_supervisor.py），
增量吸收：异常三分类 + 指数退避 + 健康快照 + SAFE HALT。

与 B3-AF-01 的关系：B3-AF-01 只修了"tick 异常不崩线程"（简单 try/except），
TickSupervisor 在此基础上增加：
- 异常分类（TRANSIENT 退避 / DEGRADED 隔离 / FATAL 停摆）
- 指数退避 + 抖动（防持续打设备）
- 连续失败升级（20 次后 SAFE HALT）
- 健康快照（供 /health 与外部 watchdog）
"""
from __future__ import annotations

import enum
import logging
import random
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Sequence

log = logging.getLogger("autoforge.tick")


class FaultClass(str, enum.Enum):
    TRANSIENT = "transient"    # 退避重试
    DEGRADED = "degraded"      # 隔离该部分，tick 继续
    FATAL = "fatal"            # 停摆 + 降级写端口


class TickStatus(str, enum.Enum):
    OK = "ok"
    DEGRADED = "degraded"
    RETRY = "retry"
    HALTED = "halted"


@dataclass(frozen=True)
class FaultRule:
    pred: Callable[[BaseException], bool]
    cls: FaultClass


class FaultPolicy:
    """异常 → FaultClass。默认未知异常算 TRANSIENT，靠 escalate_after 升级。

    理由：未知异常既不能直接 FATAL（一个偶发 bug 就让智能家居停摆，代价不对称），
    也不能无限重试（永久性 bug 会持续打设备）。退避 + 连续失败升级是折中点。
    """

    def __init__(
        self,
        rules: Sequence[FaultRule] = (),
        *,
        default: FaultClass = FaultClass.TRANSIENT,
        escalate_after: int = 20,
    ):
        self.rules = list(rules)
        self.default = default
        self.escalate_after = escalate_after

    def classify(self, exc: BaseException) -> FaultClass:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            return FaultClass.FATAL
        for r in self.rules:
            if r.pred(exc):
                return r.cls
        return self.default


def default_fault_policy(extra_transient: Sequence[type[BaseException]] = ()) -> FaultPolicy:
    transient_types = (OSError, TimeoutError, ConnectionError, *extra_transient)
    return FaultPolicy(
        rules=[
            FaultRule(lambda e: isinstance(e, transient_types), FaultClass.TRANSIENT),
            FaultRule(
                lambda e: isinstance(e, (ValueError, KeyError, TypeError, AttributeError)),
                FaultClass.DEGRADED,
            ),  # 数据 / IR 局部坏 → 隔离
        ],
        default=FaultClass.TRANSIENT,
        escalate_after=20,
    )


@dataclass
class ExponentialBackoff:
    base: float = 0.5
    factor: float = 2.0
    cap: float = 30.0
    jitter: float = 0.2  # ±20%，防多容器/多线程同步重试
    rng: Callable[[], float] = random.random
    attempt: int = 0

    def next_delay(self) -> float:
        d = min(self.cap, self.base * (self.factor ** self.attempt))
        self.attempt += 1
        span = d * self.jitter
        return max(0.0, d - span + 2 * span * self.rng())

    def reset(self) -> None:
        self.attempt = 0


@dataclass
class TickHealth:
    started_wall: datetime | None = None
    ticks_total: int = 0
    ok_total: int = 0
    degraded_total: int = 0
    fail_total: int = 0
    consecutive_failures: int = 0
    consecutive_oks: int = 0
    last_ok_mono: float = 0.0
    last_error: str = ""
    last_fault: FaultClass | None = None
    faults: dict[str, int] = field(default_factory=dict)
    ewma_tick_ms: float = 0.0
    halted: bool = False
    halted_reason: str = ""

    def snapshot(self, clock) -> dict[str, Any]:
        return {
            "state": "safe_halt" if self.halted else "ok",
            "ticks_total": self.ticks_total,
            "ok": self.ok_total,
            "degraded": self.degraded_total,
            "failed": self.fail_total,
            "consecutive_failures": self.consecutive_failures,
            "last_ok_age_s": round(clock.monotonic() - self.last_ok_mono, 3)
            if self.last_ok_mono
            else None,
            "ewma_tick_ms": round(self.ewma_tick_ms, 2),
            "faults": dict(self.faults),
            "last_error": self.last_error[-300:],
            "halted_reason": self.halted_reason,
        }


@dataclass(frozen=True)
class TickOutcome:
    status: TickStatus
    fault: FaultClass | None
    exc: BaseException | None
    duration_s: float
    delay: float = 0.0


class TickSupervisor:
    """把「tick 不许死」（B3-AF-01）升级成「tick 不许假活」。

    职责：分类 → 退避 → 计数/健康快照 → 必要时 SAFE HALT。
    本身不碰业务；body() 是现有 _tick()。
    """

    def __init__(
        self,
        clock,
        *,
        policy: FaultPolicy | None = None,
        backoff: ExponentialBackoff | None = None,
        health: TickHealth | None = None,
        tick_interval_s: float = 1.0,
        on_fault: Callable[[BaseException, FaultClass, float], None] | None = None,
        on_degrade: Callable[[BaseException], None] | None = None,
        on_halt: Callable[[BaseException], None] | None = None,
    ):
        self._clock = clock
        self._policy = policy or default_fault_policy()
        self._back = backoff or ExponentialBackoff()
        self._health = health or TickHealth(started_wall=clock.now())
        self._interval = tick_interval_s
        self._on_fault = on_fault or (
            lambda e, c, d: log.warning("tick %s, retry in %.2fs: %r", c, d, e)
        )
        self._on_degrade = on_degrade or (lambda e: log.exception("tick degraded: %r", e))
        self._on_halt = on_halt or (lambda e: log.error("SAFE HALT: %r", e))
        self._lock = threading.RLock()

    # ---- 单次 tick ----
    def run_once(self, body: Callable[[], None]) -> TickOutcome:
        with self._lock:
            if self._health.halted:
                return TickOutcome(TickStatus.HALTED, FaultClass.FATAL, None, 0.0, 0.0)
        t0 = self._clock.monotonic()
        self._health.ticks_total += 1
        try:
            body()
        except Exception as exc:  # 刻意不捕获 BaseException：让 CancelledError/退出信号穿透
            dur = max(0.0, self._clock.monotonic() - t0)
            return self._on_failure(exc, self._policy.classify(exc), dur)
        dur = max(0.0, self._clock.monotonic() - t0)
        self._on_success(dur)
        return TickOutcome(TickStatus.OK, None, None, dur, 0.0)

    def run_forever(
        self,
        body: Callable[[], None],
        *,
        stop: threading.Event | None = None,
        wait: Callable[[float], bool] | None = None,
    ) -> TickHealth:
        """wait(interval) -> True 表示被要求停止。可注入假 wait 做单测（不真 sleep）。"""
        _wait = wait or (
            lambda s: (stop.wait(s) if stop else (_ for _ in ()).throw(RuntimeError("no wait")))
        )
        while True:
            if stop is not None and stop.is_set():
                break
            outcome = self.run_once(body)
            if outcome.status is TickStatus.HALTED:
                break
            interval = outcome.delay if outcome.status is TickStatus.RETRY else self._interval
            if _wait(interval):
                break
        return self._health

    # ---- 健康 ----
    def health_state(self, stall_after_s: float = 30.0) -> str:
        """供 /health 与外部 watchdog 调用（watchdog 在主线程，能重启本线程）。"""
        if self._health.halted:
            return "safe_halt"
        if self._health.consecutive_failures:
            return "backoff"
        if self._health.degraded_total and not self._health.consecutive_oks:
            return "degraded"
        if self._health.last_ok_mono and self._clock.monotonic() - self._health.last_ok_mono > stall_after_s:
            return "stalled"
        return "ok"

    def health(self) -> TickHealth:
        return self._health

    def reset(self) -> None:
        """运维手动恢复 SAFE HALT（修复根因后才允许）。"""
        with self._lock:
            self._health.halted = False
            self._health.halted_reason = ""
            self._health.consecutive_failures = 0
            self._back.reset()

    # ---- 内部 ----
    def _on_success(self, dur: float) -> None:
        h = self._health
        h.ok_total += 1
        h.consecutive_oks += 1
        h.consecutive_failures = 0
        h.last_ok_mono = self._clock.monotonic()
        h.ewma_tick_ms = (
            dur * 1000 if not h.ewma_tick_ms else 0.8 * h.ewma_tick_ms + 0.2 * dur * 1000
        )
        self._back.reset()

    def _on_failure(self, exc: BaseException, cls: FaultClass, dur: float) -> TickOutcome:
        h = self._health
        h.fail_total += 1
        h.last_error = repr(exc)
        h.last_fault = cls
        h.faults[cls.value] = h.faults.get(cls.value, 0) + 1
        h.consecutive_oks = 0

        if cls is FaultClass.DEGRADED:
            h.degraded_total += 1
            self._on_degrade(exc)  # 隔离单实例，不退避
            return TickOutcome(TickStatus.DEGRADED, cls, exc, dur, 0.0)

        h.consecutive_failures += 1
        if cls is FaultClass.FATAL or h.consecutive_failures >= self._policy.escalate_after:
            with self._lock:
                h.halted = True
                h.halted_reason = (
                    f"escalated after {h.consecutive_failures} failures"
                    if cls is not FaultClass.FATAL
                    else repr(exc)
                )
            self._on_halt(exc)  # 回调负责把写端口降级 dry
            return TickOutcome(TickStatus.HALTED, cls, exc, dur, 0.0)

        delay = self._back.next_delay()
        self._on_fault(exc, cls, delay)
        return TickOutcome(TickStatus.RETRY, cls, exc, dur, delay)


# ---------------- SSE 重连（mimo 增量） ----------------
@dataclass(frozen=True)
class ReconnectPlan:
    attempt: int
    delay: float
    escalate: bool       # 超过 escalate_after: 告警 / 降级
    give_up: bool        # 超过 max_attempts (0 = 永不放弃)


class SseReconnector:
    """指数退避 + 稳定窗口清零 + 可选放弃。

    默认永不放弃 (max_attempts=0): 断流 = 自动化永久失效，比继续重试更糟；
    但超过 escalate_after 触发 escalate 回调（告警 + 写端口降级 dry），避免无声失效。
    """

    def __init__(
        self,
        clock,
        *,
        backoff: ExponentialBackoff | None = None,
        max_attempts: int = 0,
        escalate_after: int = 10,
        stable_reset_s: float = 60.0,
    ):
        self._clock = clock
        self._back = backoff or ExponentialBackoff(base=1.0, cap=60.0)
        self._max = max_attempts
        self._escalate_after = escalate_after
        self._stable_s = stable_reset_s
        self._attempt = 0
        self._opened_mono: float | None = None
        self.dropped_total = 0
        self.last_drop: str = ""

    def on_open(self) -> None:
        self._opened_mono = self._clock.monotonic()
        # 刻意不清零 attempt: 要连接稳定一段时间才算恢复

    def poll_stable(self) -> bool:
        """由 tick 调用: 连接已稳定超过窗口 -> 退避等级归零。"""
        if self._opened_mono is None:
            return False
        if self._clock.monotonic() - self._opened_mono >= self._stable_s and self._attempt:
            self._attempt = 0
            self._back.reset()
            return True
        return False

    def on_drop(self, exc: BaseException) -> ReconnectPlan:
        self.dropped_total += 1
        self.last_drop = repr(exc)
        self._opened_mono = None
        self._attempt += 1
        delay = self._back.next_delay()
        return ReconnectPlan(
            attempt=self._attempt,
            delay=delay,
            escalate=self._attempt >= self._escalate_after,
            give_up=bool(self._max) and self._attempt >= self._max,
        )

    @property
    def attempt(self) -> int:
        return self._attempt

    def health(self) -> dict[str, Any]:
        return {
            "attempt": self._attempt,
            "dropped_total": self.dropped_total,
            "last_drop": self.last_drop[-200:] if self.last_drop else "",
            "stable": self._opened_mono is not None,
        }
