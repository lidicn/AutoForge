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
import os
import re

import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Protocol, runtime_checkable

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
    "house_tz_name",
    "house_tz_status",
    "TZ_ENV_KEY",
    "TZ_FALLBACK_NAME",
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
#: 家庭时区的过渡别名键（DCD 20261001·homesdk 接入四问 问题 2 甲：`AF_TZ` 降为别名）。
TZ_ENV_KEY = "AF_TZ"

#: 未配置时的 fallback。DCD 20261001·§五：本仓 +8 从「写死」降级为「具名 fallback」。
TZ_FALLBACK_NAME = "Asia/Shanghai"

#: 规范键（机制层 `homesdk.time` 的第一优先键）。
HOMESDK_TZ_ENV_KEY = "HOMESDK_TZ"

#: 前缀约定（与 `homesdk.config` / `homesdk.time` 同一约定：`HOMESDK_` 优先）。
HOMESDK_TZ_PREFIX = "HOMESDK_"

#: 基键（与 `homesdk.time.TZ_ENV_KEYS` 同一份清单）。
_TZ_BASE_KEYS: tuple[str, ...] = ("TZ", TZ_ENV_KEY, "TZ_OFFSET_HOURS")

#: 有效读取顺序——照机制层约定：**每个基键先试 `HOMESDK_` 前缀，再试裸键**。
#: 写成扁平清单而不是循环里拼前缀，是为了让 fallback 与 `homesdk.time` 的优先级逐字相等；
#: 两条路径给出两个优先级，就等于同一份 compose 文件有两个家庭墙钟。
TZ_ENV_KEYS: tuple[str, ...] = tuple(
    key for base in _TZ_BASE_KEYS for key in (f"{HOMESDK_TZ_PREFIX}{base}", base)
)

#: 值形态是小时偏移而非 IANA 名的键（单独处理，越界值拒收）。
_OFFSET_KEY_SUFFIX = "TZ_OFFSET_HOURS"


def homesdk_time():
    """机制层 `homesdk.time` 模块；不可用时返回 None（vendor 的 wheel 里没有该模块）。

    缺席是**可观测**的，不是静默的：`house_tz_status()["mechanism"]` 会写 `"af_local"`。
    """
    try:
        from homesdk import time as _module
    except (ImportError, AttributeError):
        return None
    return _module


def _first_tz_value() -> tuple[str | None, str | None]:
    """按 `TZ_ENV_KEYS` 顺序取第一个有效配置，返回 `(键名, 值)`；都没有则 `(None, None)`。"""
    for key in TZ_ENV_KEYS:
        raw = os.environ.get(key)
        if raw is None:
            continue
        value = raw.strip()
        if not value:  # 空串等同未配置（`AF_TZ=` 是"没配"，不是"配了个空时区"）
            continue
        if key.endswith(_OFFSET_KEY_SUFFIX):
            try:
                hours = float(value)
            except (TypeError, ValueError):
                continue
            if not -24 < hours < 24:  # "80" 这类笔误不留 fallback 幻觉
                continue
            sign = "+" if hours >= 0 else "-"
            whole = int(abs(hours))
            minutes = int(round((abs(hours) - whole) * 60))
            return key, f"UTC{sign}{whole:02d}:{minutes:02d}"
        return key, value
    return None, None


def house_tz_name() -> str:
    """当前生效的家庭时区标识（IANA 名，或兜底的 `UTC+HH:MM` 形态）。

    主路径走机制层 `homesdk.time`（DCD 20261001·homesdk 接入四问 问题 1/2）；机制层缺席时
    用本仓同口径的读取链做 fallback。每次调用现读环境变量，不做模块级缓存——否则注入后
    毫无反应（MA 在同批裁定执行回填里专门点名过那种 import 时绑定快照的写法）。
    """
    module = homesdk_time()
    if module is not None:
        return module.house_tz_name()
    _, value = _first_tz_value()
    return value or TZ_FALLBACK_NAME


def to_house_iso(moment: datetime | float | int | None = None) -> str:
    """家庭墙钟口径的 ISO8601（ADM 主题契约 §四：跨仓时间戳不靠"大家都是 +8"的默契）。

    主路径走机制层 `homesdk.time.to_house_iso`；缺席时用本仓同语义的镜像——
    naive datetime **按家庭墙钟解释**，不交给机器时区（B3-AF-04 的根因）。
    """
    module = homesdk_time()
    if module is not None:
        return module.to_house_iso(moment)
    tz = load_tz()
    if moment is None:
        target = datetime.now(tz)
    elif isinstance(moment, (int, float)):
        target = datetime.fromtimestamp(float(moment), tz=tz)
    elif isinstance(moment, datetime):
        target = moment.replace(tzinfo=tz) if moment.tzinfo is None else moment.astimezone(tz)
    else:
        raise TypeError(f"to_house_iso 不支持的入参类型：{type(moment).__name__}")
    return target.isoformat()


def ensure_aware(dt: datetime, *, who: str = "dt") -> datetime:
    """naive 时间一律拒绝。B3-AF-04 根因就是 naive UTC 与 aware local 混算。"""
    if dt.tzinfo is None:
        raise ValueError(f"{who} must be timezone-aware, got naive {dt!r}")
    return dt


def load_tz(tz_name: str | None = None):
    """家庭时区对象。

    `tz_name=None`（生产默认）走机制层 `homesdk.time.house_tz()`——IANA 名口径，能表达 DST；
    机制层缺席时退本仓解析。显式传参（`af_predict` / `af_pretrigger` 的按图覆盖）保持原语义。
    """
    if tz_name is None:
        module = homesdk_time()
        if module is not None:
            return module.house_tz()
        tz_name = house_tz_name()
    if ZoneInfo is not None:
        try:
            return ZoneInfo(tz_name)
        except Exception:
            pass
    if tz_name.startswith("UTC") and len(tz_name) > 3:
        try:
            sign = 1 if tz_name[3] == "+" else -1
            hours, minutes = tz_name[4:].split(":")
            return timezone(sign * timedelta(hours=int(hours), minutes=int(minutes)))
        except (ValueError, IndexError):
            pass
    # 容器缺 tzdata（或名字写错）时退化为固定 +08:00：深圳无夏令时，与 fallback 名语义等价。
    # 这件事必须能被外部看到，所以配套 `house_tz_status()` 供 /api/health 暴露。
    return timezone(timedelta(hours=8), "CST")


def house_tz_status(tz_name: str | None = None) -> dict:
    """时区口径的可观测判据：请求名 + 是否真的按名字解析成功 + 谁在供数。

    静默退化到 +8 就是 MA 在 20261001 批里踩过的假绿同类问题，故把"退化了"这件事
    本身做成一个能读到的字段，而不是只写在注释里。`mechanism` 区分两种供数路径：
    `homesdk.time`（机制层）与 `af_local`（本仓 fallback）——两条路径给出同一个名字
    才算迁移完成，这条字段就是用来发现它们分叉的。
    """
    key, _ = _first_tz_value()
    module = homesdk_time()
    if tz_name is not None:
        source = "param"
    elif key is not None:
        source = f"env:{key}"
    else:
        source = "fallback"
    name = tz_name or house_tz_name()
    tz = load_tz(tz_name)
    offset = tz.utcoffset(datetime.now(timezone.utc))
    return {
        "tz_name": name,
        "source": source,
        "mechanism": "homesdk.time" if module is not None else "af_local",
        "resolved_by_name": getattr(tz, "key", None) == name,
        "utc_offset": None if offset is None else offset.total_seconds() / 3600,
    }


def assert_clock_consistent(c) -> None:
    """单测共用不变量：tz-aware、today 与 local_now 同日、utc_now 为 UTC。"""
    n, l = c.now(), c.local_now()
    assert n.tzinfo is not None and l.tzinfo is not None, "clock must be tz-aware"
    assert c.today() == l.date(), "today() must equal local_now().date()"
    assert c.utc_now().tzinfo is timezone.utc


def matches_at(local_now: datetime, at: str) -> bool:
    """time 触发判定：只接受本地时间。B3-AF-04 回归测试点。

    N-P2-2 修复：严格校验 HH:MM 格式（正则 + 范围），非法输入直接 ValueError，
    让调用点（IR 静态闸）能在建图期拦下，而非运行时静默不匹配。
    """
    ensure_aware(local_now, who="matches_at.local_now")
    if not isinstance(at, str) or not re.fullmatch(r"\d{2}:\d{2}", at):
        raise ValueError(f"at 必须是 'HH:MM' 格式，收到 {at!r}")
    hh, mm = at.split(":")
    h, m = int(hh), int(mm)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"at 必须合法 HH:MM（00-23:00-59），收到 {at!r}")
    return (local_now.hour, local_now.minute) == (h, m)


class SystemTimeSource:
    """生产用时间源：真实墙钟 + 真实单调时钟。

    now() 返回 UTC（事件时间戳用），local_now() 返回本地（time 触发判定用）。
    两者不统一是历史原因；mimo Clock 设计建议未来统一 now()=本地，但需迁移 67 处引用。
    """

    def __init__(self, tz_name: str | None = None):
        self._tz_name = tz_name or house_tz_name()
        self._tz = load_tz(self._tz_name)

    @property
    def tz_name(self) -> str:
        """实际生效的时区名（构造时解析，供健康面与日志复核）。"""
        return self._tz_name

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
        return f"SystemTimeSource(tz_name={self._tz_name!r})"


@dataclass
class VirtualTimeSource:
    """仿真用虚拟时间源：时间完全由测试推进，支持时间旅行。

    两个时钟**同步推进**（`advance` 同时推进墙钟与单调时钟），
    这样依赖 `monotonic()` 的计时器在仿真里也能被 `async_fire_time_changed` 驱动。

    mimo Clock 增量：`jump()` 只动墙钟不动单调钟，用于测 NTP 校时/人为改时钟场景。

    v2.5 修复（N-P2-1）：增加独立 `_tz`，`local_now()` 返回 `_current.astimezone(_tz)`，
    与生产 `SystemTimeSource.local_now()` 语义对齐；否则仿真 time 触发的 hhmm 判定会因
    UTC/本地时区差导致假绿灯（+08 机器上 UTC 07:00 ≠ 本地 07:00）。
    """

    start: datetime = field(default_factory=lambda: datetime(2026, 9, 14, 8, 0, 0, tzinfo=timezone.utc))
    _current: datetime = field(init=False)
    _mono: float = field(default=0.0, init=False)
    _tz: Any = field(default_factory=load_tz, init=False)

    def __post_init__(self) -> None:
        if self.start.tzinfo is None:
            self.start = self.start.replace(tzinfo=timezone.utc)
        self._current = self.start

    # ── TimeSource 实现 ──────────────────────────────────────────────
    def now(self) -> datetime:
        return self._current

    def local_now(self) -> datetime:
        """仿真时间的本地投影：把 _current 归一到仿真时区（默认 Asia/Shanghai / +08:00）。

        生产对应：SystemTimeSource.local_now() = datetime.now(self._tz)。
        仿真中 start 可能是 UTC（self._current.tzinfo=UTC），需要 astimezone(_tz) 后再取 hhmm。
        """
        return self._current.astimezone(self._tz)

    def utc_now(self) -> datetime:
        return self._current.astimezone(timezone.utc)

    def today(self) -> date:
        return self.local_now().date()

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
