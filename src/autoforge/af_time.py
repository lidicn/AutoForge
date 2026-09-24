"""时间源抽象 —— 开工第一件事（KICKOFF §4.2）。

契约（强制）：
    业务代码**禁止直接调用 `time.time()` / `datetime.now()`**。
    一切时间读取必须走 `TimeSource`，否则仿真无法做时间旅行，
    生产与仿真会出现「看起来一样、跑起来不一样」的漂移。

两个时钟不能混用：
    - `now()`      墙钟：仅用于事件时间戳（生产环境取 HA 的 `last_changed`，不自己造）
    - `monotonic()` 单调时钟：计时器（`wait`/`ask`、节流、熔断、配额）一律用它，
                    不受系统时间调整影响

仿真时由 vhass 注入 `VirtualTimeSource`，通过 `async_fire_time_changed` 时间旅行。

mimo Clock 设计增量（2026-09-22）：
    - ensure_aware / load_tz / assert_clock_consistent / matches_at 辅助函数
    - SystemTimeSource / VirtualTimeSource 增加 today() / utc_now() / wall_after()
    - VirtualTimeSource 增加 jump()（只动墙钟不动单调钟，测 NTP 校时场景）
"""

from __future__ import annotations

import math

import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Protocol, runtime_checkable

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

__all__ = [
    "TimeSource",
    "SystemTimeSource",
    "VirtualTimeSource",
    "parse_duration",
    "format_duration",
    "ensure_aware",
    "load_tz",
    "assert_clock_consistent",
    "matches_at",
]


@runtime_checkable
class TimeSource(Protocol):
    """时间源接口。生产与仿真共用，业务代码只依赖这个协议。"""

    def now(self) -> datetime:
        """墙钟（带时区）。事件时间戳用；生产环境实际应取 HA 的 last_changed。"""
        ...

    def monotonic(self) -> float:
        """单调时钟（秒）。计时器/节流/熔断/配额一律用它。"""
        ...

    def local_now(self) -> datetime:
        """本地墙钟（带系统时区）。time 触发 hhmm 判定用。"""
        ...

    def today(self) -> date:
        """本地日历日。"""
        ...

    def utc_now(self) -> datetime:
        """UTC 墙钟。"""
        ...

    def wall_after(self, seconds: float) -> datetime:
        """定时器持久化用的到期墙钟。"""
        ...


# ── 辅助函数（mimo Clock 设计增量）───────────────────────────────────
def ensure_aware(dt: datetime, *, who: str = "dt") -> datetime:
    """naive 时间一律拒绝。B3-AF-04 根因就是 naive UTC 与 aware local 混算。"""
    if dt.tzinfo is None:
        raise ValueError(f"{who} must be timezone-aware, got naive {dt!r}")
    return dt


def load_tz(tz_name: str = "Asia/Shanghai"):
    """容器缺 tzdata 时退化为固定 +08:00（深圳无夏令时，语义等价）。"""
    if ZoneInfo is not None:
        try:
            return ZoneInfo(tz_name)
        except Exception:
            pass
    return timezone(timedelta(hours=8), "CST")


def assert_clock_consistent(c) -> None:
    """单测共用不变量：tz-aware、today 与 local_now 同日、utc_now 为 UTC。"""
    n, l = c.now(), c.local_now()
    assert n.tzinfo is not None and l.tzinfo is not None, "clock must be tz-aware"
    assert c.today() == l.date(), "today() must equal local_now().date()"
    assert c.utc_now().tzinfo is timezone.utc


def matches_at(local_now: datetime, at: str) -> bool:
    """time 触发判定：只接受本地时间。B3-AF-04 回归测试点。"""
    ensure_aware(local_now, who="matches_at.local_now")
    hh, mm = at.split(":")
    return (local_now.hour, local_now.minute) == (int(hh), int(mm))


class SystemTimeSource:
    """生产用时间源：真实墙钟 + 真实单调时钟。

    now() 返回 UTC（事件时间戳用），local_now() 返回本地（time 触发判定用）。
    两者不统一是历史原因；mimo Clock 设计建议未来统一 now()=本地，但需迁移 67 处引用。
    """

    def __init__(self, tz_name: str = "Asia/Shanghai"):
        self._tz = load_tz(tz_name)

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def local_now(self) -> datetime:
        """B3-AF-04：本地墙钟（带系统时区）。time 触发 hhmm 判定用——用户写 at:07:00 指本地时间。"""
        return datetime.now(self._tz)

    def utc_now(self) -> datetime:
        return datetime.now(timezone.utc)

    def today(self) -> date:
        return self.local_now().date()

    def wall_after(self, seconds: float) -> datetime:
        return self.local_now() + timedelta(seconds=seconds)

    def monotonic(self) -> float:
        return time.monotonic()

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return "SystemTimeSource()"


@dataclass
class VirtualTimeSource:
    """仿真用虚拟时间源：时间完全由测试推进，支持时间旅行。

    两个时钟**同步推进**（`advance` 同时推进墙钟与单调时钟），
    这样依赖 `monotonic()` 的计时器在仿真里也能被 `async_fire_time_changed` 驱动。

    mimo Clock 增量：`jump()` 只动墙钟不动单调钟，用于测 NTP 校时/人为改时钟场景。
    """

    start: datetime = field(default_factory=lambda: datetime(2026, 9, 14, 8, 0, 0, tzinfo=timezone.utc))
    _current: datetime = field(init=False)
    _mono: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        if self.start.tzinfo is None:
            self.start = self.start.replace(tzinfo=timezone.utc)
        self._current = self.start

    # ── TimeSource 实现 ──────────────────────────────────────────────
    def now(self) -> datetime:
        return self._current

    def local_now(self) -> datetime:
        """B3-AF-04：仿真时间即本地时间（仿真测试断言基于仿真时间本身）。"""
        return self._current

    def utc_now(self) -> datetime:
        return self._current.astimezone(timezone.utc)

    def today(self) -> date:
        return self._current.date()

    def wall_after(self, seconds: float) -> datetime:
        return self._current + timedelta(seconds=seconds)

    def monotonic(self) -> float:
        return self._mono

    # ── 仿真控制 ────────────────────────────────────────────────────
    def advance(self, seconds: float) -> datetime:
        """向前推进虚拟时间（秒），两个时钟同步。返回推进后的墙钟。"""
        if seconds < 0:
            raise ValueError(f"虚拟时间只能前进，收到 {seconds}s")
        self._current = self._current + timedelta(seconds=seconds)
        self._mono += float(seconds)
        return self._current

    def jump(self, seconds: float) -> datetime:
        """只动墙钟不动单调钟（NTP 校时/人为改时钟/夏令时切换）。

        mimo Clock 设计：墙钟与单调钟解耦是必须的——af_persist 的定时器墙钟换算
        恰恰要在时钟跳变后仍正确，绑死的虚拟时钟测不出这类 bug。
        """
        self._current = self._current + timedelta(seconds=seconds)
        return self._current

    def advance_to(self, target: datetime) -> datetime:
        """推进到指定墙钟时刻（不得回退）。"""
        delta = (target - self._current).total_seconds()
        return self.advance(delta)

    def reset(self) -> None:
        self._current = self.start
        self._mono = 0.0

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return f"VirtualTimeSource(now={self._current.isoformat()}, mono={self._mono:.3f})"


# ── 时长字面量：`10m` / `90s` / `2h` / `1d` ─────────────────────────
_UNIT_SECONDS = {
    "s": 1.0,
    "m": 60.0,
    "h": 3600.0,
    "d": 86400.0,
}


def parse_duration(value: str | int | float) -> float:
    """解析时长字面量为秒。

    支持 `'10m'` / `'90s'` / `'2h'` / `'1d'`，也接受纯数字（按秒）。
    支持 HA 原生 `'HH:MM:SS'` 格式（P1-9）。
    IR 里 `for=10m`、`wait 60s` 都走这里，保证两种 Timer 的单位口径一致。

    P1-9 修复：拒绝 inf/nan/负值（fail-closed），避免挂起项永不过期或定时器静默不触发。
    """
    if isinstance(value, (int, float)):
        seconds = float(value)
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError(f"时长必须是非负有限值：{value!r}")
        return seconds
    text = str(value).strip().lower()
    if not text:
        raise ValueError("时长不能为空")
    # P1-9：支持 HH:MM:SS 格式
    if ":" in text:
        parts = text.split(":")
        if len(parts) != 3:
            raise ValueError(f"HH:MM:SS 格式需要三个部分：{value!r}")
        try:
            h, m, s = float(parts[0]), float(parts[1]), float(parts[2])
        except ValueError as exc:
            raise ValueError(f"无法解析 HH:MM:SS：{value!r}") from exc
        seconds = h * 3600 + m * 60 + s
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError(f"时长必须是非负有限值：{value!r}")
        return seconds
    if text[-1] in _UNIT_SECONDS:
        number, unit = text[:-1], text[-1]
    else:
        number, unit = text, "s"
    try:
        seconds = float(number) * _UNIT_SECONDS[unit]
    except ValueError as exc:
        raise ValueError(f"无法解析时长字面量：{value!r}") from exc
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError(f"时长必须是非负有限值：{value!r}")
    return seconds


def format_duration(seconds: float) -> str:
    """秒 → 人类可读时长（用于 NL 渲染与审计）。"""
    seconds = float(seconds)
    if seconds < 60:
        return f"{seconds:g} 秒"
    if seconds < 3600:
        return f"{seconds / 60:g} 分钟"
    if seconds < 86400:
        return f"{seconds / 3600:g} 小时"
    return f"{seconds / 86400:g} 天"
