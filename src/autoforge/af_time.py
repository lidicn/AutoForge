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
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol, runtime_checkable

__all__ = [
    "TimeSource",
    "SystemTimeSource",
    "VirtualTimeSource",
    "parse_duration",
    "format_duration",
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


class SystemTimeSource:
    """生产用时间源：真实墙钟 + 真实单调时钟。"""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def monotonic(self) -> float:
        return time.monotonic()

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return "SystemTimeSource()"


@dataclass
class VirtualTimeSource:
    """仿真用虚拟时间源：时间完全由测试推进，支持时间旅行。

    两个时钟**同步推进**（`advance` 同时推进墙钟与单调时钟），
    这样依赖 `monotonic()` 的计时器在仿真里也能被 `async_fire_time_changed` 驱动。
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
    IR 里 `for=10m`、`wait 60s` 都走这里，保证两种 Timer 的单位口径一致。
    """
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower()
    if not text:
        raise ValueError("时长不能为空")
    if text[-1] in _UNIT_SECONDS:
        number, unit = text[:-1], text[-1]
    else:
        number, unit = text, "s"
    try:
        return float(number) * _UNIT_SECONDS[unit]
    except ValueError as exc:
        raise ValueError(f"无法解析时长字面量：{value!r}") from exc


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
