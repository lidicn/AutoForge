"""预测性触发（AutoForge）—— 从历史触发模式预测未来概率，高概率时提前触发。

本模块是**纯新增**模块（不改任何现有文件），与现有实现的边界：

* **时间**：只依赖 `af_time` 的 TimeSource 契约（`SystemTimeSource` / `ensure_aware` /
  `load_tz`），业务代码不直接读墙钟（KICKOFF §4.2）。`predict()` 由调用方传 `now`；
  `learn()` / `pre_trigger()` / 持久化时间戳一律走注入的 clock。
* **执行**：不 import `af_executor`（禁改清单内、且接口未内联），对 `executor` 做鸭子类型
  调用（`execute_predicted` / `execute` / `trigger` / `run` / `fire` / 可调用对象）。
* **置信度**：不 import `af_conf`。`pre_trigger(threshold=...)` 是**概率**阈值，与 G4 的
  auto/shadow/ask 分级是两套口径；需要联动时由调用方先查 `ConfidenceStore.band()`。

模型（每个 automation 一份）：
    1. 时间直方图：24 个小时段触发计数（`hourly()` / `peak_hour()`）
    2. 分钟带日覆盖率：主估计器 —— 「过去 N 个完整观察日里触发落在查询时段的比例」
    3. 状态模式：触发时的状态组合计数（`state_counts()` / `state_pattern()`），
       预测时按「重叠键一致、未知键不否决」做条件化
    4. 滑动窗口：只保留最近 `window_days`（默认 7）天的触发记录

概率口径（`explain()` 暴露全部中间量）：
    p = (hits + k * p_hour) / (trials + k)，k = `prior_strength`（默认 2.0）
    hits   = 完整观察日里，有触发落在 [now, now+window_minutes)（本地时间、环形跨零点）的日数
    trials = 完整观察日数（昨天往前数；当天不计入，见交付单 §7 决策 2）
    p_hour = 小时直方图的日命中率按带内小时重叠分钟加权（先验平滑）

冷启动：`len(events) < min_events`（默认 3）或 `trials < min_days`（默认 2）→ `predict()` 返回 0.0。

持久化：`persist_dir/predictions.json`（原子写 + 损坏隔离为 `.corrupt`），重启后模型与
`predicted` 标记都在；`predicted` 采用 **at-most-once**（先落盘再触发），避免重复写设备。
"""

from __future__ import annotations

import inspect
import json
import logging
import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .af_atomic import atomic_write_text
from .af_time import SystemTimeSource, ensure_aware, load_tz

__all__ = [
    "Predictor",
    "PREDICTIONS_FILE",
    "MODEL_VERSION",
    "DEFAULT_WINDOW_DAYS",
    "DEFAULT_MIN_EVENTS",
    "DEFAULT_MIN_DAYS",
    "DEFAULT_PRIOR_STRENGTH",
    "EXECUTOR_METHODS",
]

logger = logging.getLogger(__name__)

PREDICTIONS_FILE = "predictions.json"
MODEL_VERSION = 1

DEFAULT_WINDOW_DAYS = 7
DEFAULT_MIN_EVENTS = 3
DEFAULT_MIN_DAYS = 2
DEFAULT_PRIOR_STRENGTH = 2.0
DEFAULT_MAX_EVENTS = 5000
DEFAULT_PRE_WINDOW_MINUTES = 5.0

DAY_MINUTES = 1440.0

#: executor 鸭子类型探测顺序（af_executor 接口未内联，见 §7 决策 6）
EXECUTOR_METHODS = ("execute_predicted", "execute", "trigger", "run", "fire")
#: history_store 只读探测顺序（见 §7 未决项 1）
HISTORY_METHODS = ("events_for", "events", "history_for", "get_events", "history")
STATE_METHODS = ("current_state", "get_state", "state_for")
#: 事件字典字段名探测（见 §7 未决项 6）
EVENT_TIME_KEYS = ("at", "timestamp", "ts", "time", "last_changed", "when")
STATE_KEYS = ("state", "states", "context_state", "context")


# ── 小工具 ──────────────────────────────────────────────────────────
def _require_id(automation_id: Any) -> str:
    if not isinstance(automation_id, str) or not automation_id.strip():
        raise ValueError(f"automation_id 必须是非空字符串：{automation_id!r}")
    return automation_id


def _require_window(window_minutes: Any) -> float:
    try:
        window = float(window_minutes)
    except (TypeError, ValueError) as exc:  # pragma: no cover - 防御
        raise ValueError(f"window_minutes 必须是正有限值：{window_minutes!r}") from exc
    if not math.isfinite(window) or window <= 0:
        raise ValueError(f"window_minutes 必须是正有限值：{window_minutes!r}")
    return window


def _require_threshold(threshold: Any) -> float:
    try:
        value = float(threshold)
    except (TypeError, ValueError) as exc:  # pragma: no cover - 防御
        raise ValueError(f"threshold 必须落在 [0, 1]：{threshold!r}") from exc
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"threshold 必须落在 [0, 1]：{threshold!r}")
    return value


def _call_flexible(fn, *args, **kwargs):
    """按签名裁剪参数后调用。

    不用「试 TypeError 再换形态」的做法——那会把被调用方自己抛的 TypeError 吞掉。
    """
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):  # pragma: no cover - C 函数/内建
        return fn(*args)
    params = list(sig.parameters.values())
    if any(p.kind is p.VAR_POSITIONAL for p in params):
        call_args = args
    else:
        positional = [
            p for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        call_args = args[: len(positional)]
    if any(p.kind is p.VAR_KEYWORD for p in params):
        call_kwargs = dict(kwargs)
    else:
        call_kwargs = {k: v for k, v in kwargs.items() if k in sig.parameters}
    return fn(*call_args, **call_kwargs)


def _event_datetime(value: Any) -> datetime:
    """事件时间戳 → tz-aware datetime。naive 一律拒绝（B3-AF-04 口径）。"""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, bool):
        raise ValueError(f"事件时间戳不是合法时间：{value!r}")
    elif isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            raise ValueError(f"事件时间戳不是有限值：{value!r}")
        dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("事件时间戳为空字符串")
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"无法解析事件时间戳：{value!r}") from exc
    else:
        raise ValueError(f"不支持的事件时间戳类型：{type(value).__name__}")
    return ensure_aware(dt, who="trigger_event timestamp")


def _event_time_value(event: Mapping[str, Any]) -> Any:
    for key in EVENT_TIME_KEYS:
        if event.get(key) is not None:
            return event[key]
    raise ValueError(f"触发事件缺少时间戳字段（{EVENT_TIME_KEYS}）：{event!r}")


def _norm_state(raw: Any) -> dict[str, str] | None:
    if isinstance(raw, Mapping):
        items = {str(k): str(v) for k, v in raw.items()}
        return items or None
    return None


def _event_state(event: Mapping[str, Any]) -> dict[str, str] | None:
    for key in STATE_KEYS:
        if event.get(key) is None:
            continue
        raw = event[key]
        if isinstance(raw, Mapping):
            return _norm_state(raw)
        if key == "state":  # HA 常见：state 直接是 "on"/"off" 标量
            return {"state": str(raw)}
    return None


def _state_match(event_state: Mapping[str, str] | None,
                 query_state: Mapping[str, str] | None) -> bool:
    """「重叠键一致、未知键不否决」——见 §7 决策 8。"""
    if not query_state:
        return True
    if not event_state:
        return True  # 历史事件没记状态 = 无信息，不否决
    for key, value in event_state.items():
        if key in query_state and str(query_state[key]) != str(value):
            return False
    return True


def _canon_state_key(state: Mapping[str, str] | None) -> str:
    if not state:
        return ""
    return ",".join(f"{k}={v}" for k, v in sorted(state.items()))


def _minute_of_day(dt: datetime) -> float:
    return dt.hour * 60.0 + dt.minute + dt.second / 60.0 + dt.microsecond / 60_000_000.0


def _band_segments(start: float, length: float) -> list[tuple[float, float]]:
    """查询时段 → [0, 1440) 上的线性区间（环形，跨零点自动切成两段）。"""
    length = max(0.0, min(float(length), DAY_MINUTES))
    if length <= 0:
        return []
    begin = start % DAY_MINUTES
    end = begin + length
    if end <= DAY_MINUTES:
        return [(begin, end)]
    return [(begin, DAY_MINUTES), (0.0, end - DAY_MINUTES)]


def _in_band(minute: float, segments: list[tuple[float, float]]) -> bool:
    return any(lo <= minute < hi for lo, hi in segments)


def _overlap(a_lo: float, a_hi: float, b_lo: float, b_hi: float) -> float:
    return max(0.0, min(a_hi, b_hi) - max(a_lo, b_lo))


def _parse_iso(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _ev_key(record: Mapping[str, Any]) -> tuple[str, str]:
    return (str(record["at"]), _canon_state_key(record.get("state")))


@dataclass
class _Model:
    """一个 automation 的触发历史 + 提前触发标记。

    `events` 是唯一真相，索引（`_Index`）随时可重建。
    """

    automation_id: str
    events: list[dict] = field(default_factory=list)
    predicted: dict | None = None
    learned: int = 0
    dropped: int = 0
    pruned: int = 0
    skipped_predicted: int = 0
    seen: set = field(default_factory=set)


@dataclass
class _Index:
    """派生索引：按日分桶（分钟 + 状态）+ 24 桶时间直方图 + 状态组合计数。"""

    by_day: dict = field(default_factory=dict)      # date -> [(minute, state)]
    hour_hist: list = field(default_factory=lambda: [0] * 24)
    state_counts: dict = field(default_factory=dict)
    count: int = 0


class Predictor:
    """预测性触发器：学历史 → 算概率 → 高概率提前触发（带去重标记）。

    构造契约：`Predictor(history_store, executor, persist_dir)`；
    其余参数全部 keyword-only 且有默认值，不改变既有调用形态。
    """

    def __init__(
        self,
        history_store,
        executor,
        persist_dir,
        *,
        window_days: int = DEFAULT_WINDOW_DAYS,
        min_events: int = DEFAULT_MIN_EVENTS,
        min_days: int = DEFAULT_MIN_DAYS,
        prior_strength: float = DEFAULT_PRIOR_STRENGTH,
        max_events: int = DEFAULT_MAX_EVENTS,
        pre_window_minutes: float = DEFAULT_PRE_WINDOW_MINUTES,
        tz_name: str | None = None,
        clock=None,
        filename: str = PREDICTIONS_FILE,
    ) -> None:
        if int(window_days) < 1:
            raise ValueError(f"window_days 必须 >= 1：{window_days!r}")
        if int(min_events) < 1:
            raise ValueError(f"min_events 必须 >= 1：{min_events!r}")
        if int(min_days) < 1:
            raise ValueError(f"min_days 必须 >= 1：{min_days!r}")
        if int(max_events) < 1:
            raise ValueError(f"max_events 必须 >= 1：{max_events!r}")
        if not math.isfinite(float(prior_strength)) or float(prior_strength) < 0:
            raise ValueError(f"prior_strength 必须是非负有限值：{prior_strength!r}")
        self._pre_window = _require_window(pre_window_minutes)

        self._history_store = history_store
        self._executor = executor
        self._persist_dir = os.fspath(persist_dir)
        self._filename = str(filename)
        self._window_days = int(window_days)
        self._min_events = int(min_events)
        self._min_days = int(min_days)
        self._prior_strength = float(prior_strength)
        self._max_events = int(max_events)
        self._tz = load_tz(tz_name)
        # 时间一律走 TimeSource（KICKOFF §4.2）：显式 clock 优先，其次 history_store.clock
        self._clock = clock or getattr(history_store, "clock", None) or SystemTimeSource(tz_name)

        self._models: dict[str, _Model] = {}
        self._index: dict[str, _Index] = {}
        self._load()

    # ── 只读属性 ───────────────────────────────────────────────────
    @property
    def predictions_path(self) -> str:
        return os.path.join(self._persist_dir, self._filename)

    @property
    def persist_dir(self) -> str:
        return self._persist_dir

    @property
    def window_days(self) -> int:
        return self._window_days

    # ── 学习 ───────────────────────────────────────────────────────
    def learn(
        self,
        automation_id: str,
        trigger_events: Sequence[Mapping[str, Any]] | None = None,
        *,
        include_predicted: bool = False,
    ) -> None:
        """从历史触发记录学习时间 / 状态模式。

        * 时间戳缺失、无法解析、naive → 丢弃该事件并计数（`stats()["dropped"]`），不猜时区；
        * `predicted=True` 的自生成事件默认**不进训练集**（防自我强化，§7 决策 5）；
        * 同一 (时间戳, 状态) 的重复记录只算一次（幂等）；
        * 事件按滑动窗口裁剪到最近 `window_days` 天。

        `trigger_events` 省略时，从 `history_store` 拉（§7 未决项 1）。
        """
        aid = _require_id(automation_id)
        if trigger_events is None:
            trigger_events = self._fetch_history(aid)
        if trigger_events is None:
            trigger_events = []
        if isinstance(trigger_events, (str, bytes, Mapping)) or not isinstance(
            trigger_events, Iterable_abc
        ):  # pragma: no cover - 防御
            raise TypeError(f"trigger_events 必须是事件序列：{type(trigger_events).__name__}")

        model = self._models.setdefault(aid, _Model(aid))
        added = dropped = skipped = 0
        for raw in trigger_events:
            if not isinstance(raw, Mapping):
                dropped += 1
                continue
            try:
                at = _event_datetime(_event_time_value(raw))
            except (TypeError, ValueError) as exc:
                dropped += 1
                logger.warning("丢弃脏触发事件（%s）：%r", exc, raw)
                continue
            is_predicted = bool(raw.get("predicted", False))
            if is_predicted and not include_predicted:
                skipped += 1
                continue
            record = {
                "at": at.isoformat(),
                "state": _event_state(raw),
                "predicted": is_predicted,
            }
            key = _ev_key(record)
            if key in model.seen:
                continue
            model.seen.add(key)
            model.events.append(record)
            added += 1

        model.learned += 1
        model.dropped += dropped
        model.skipped_predicted += skipped
        model.events.sort(key=lambda e: _parse_iso(e["at"]))
        self._prune(aid, self._ref_now(model))
        self._reindex(aid)
        self._save()
        logger.info(
            "learn %s：+%d 条（窗口内 %d），丢弃 %d，跳过自生成 %d",
            aid, added, len(model.events), dropped, skipped,
        )

    # ── 预测 ───────────────────────────────────────────────────────
    def predict(
        self,
        automation_id: str,
        now,
        window_minutes: float = 5,
        *,
        state: Mapping[str, Any] | None = None,
        prior: float | None = None,
        prior_weight: float | None = None,
    ) -> float:
        """预测未来 `window_minutes` 分钟内触发概率（0-1）。冷启动返回 0.0。

        `now` 必须 tz-aware（naive 直接 ValueError，B3-AF-04 口径）。
        `prior` / `prior_weight` 为可选的**经验派生先验**（F11②，来自 `af_experience`
        `empirical_prior`）：贝叶斯合成多一项证据；两者任一缺失或权重<=0 → 行为与接入前一致。
        """
        return float(
            self.explain(
                automation_id, now, window_minutes, state=state,
                prior=prior, prior_weight=prior_weight,
            )["p"]
        )

    def explain(
        self,
        automation_id: str,
        now,
        window_minutes: float = 5,
        *,
        state: Mapping[str, Any] | None = None,
        prior: float | None = None,
        prior_weight: float | None = None,
    ) -> dict:
        """概率的全部中间量（可解释性 / 单测断言用）。"""
        aid = _require_id(automation_id)
        now = ensure_aware(now, who="predict.now")
        window = _require_window(window_minutes)
        query_state = _norm_state(state)

        info: dict[str, Any] = {
            "automation_id": aid,
            "now": now.isoformat(),
            "window_minutes": window,
            "state": query_state,
            "cold_start": True,
            "reason": "",
            "events": 0,
            "sample_events": 0,
            "hits": 0,
            "trials": 0,
            "p_minute": 0.0,
            "p_hour_prior": 0.0,
            "prior_strength": self._prior_strength,
            "experience_prior_mean": None,
            "experience_prior_weight": 0.0,
            "p": 0.0,
        }

        model = self._models.get(aid)
        if model is None or not model.events:
            info["reason"] = "无历史触发记录"
            return info

        window_events = self._window_events(model, now)
        info["events"] = len(window_events)
        if len(window_events) < self._min_events:
            info["reason"] = (
                f"历史不足（events={len(window_events)} < min_events={self._min_events}）"
            )
            return info

        now_local = now.astimezone(self._tz)
        first_local = min(_parse_iso(e["at"]) for e in window_events).astimezone(self._tz)
        last_full_day = now_local.date() - timedelta(days=1)   # 当天不计入（§7 决策 2）
        if first_local.date() > last_full_day:
            info["reason"] = "没有完整观察日（历史全部发生在当天）"
            return info

        trials = (last_full_day - first_local.date()).days + 1
        trials = max(1, min(trials, self._window_days))
        info["trials"] = trials
        if trials < self._min_days:
            info["reason"] = f"观察天数不足（trials={trials} < min_days={self._min_days}）"
            return info

        day_list = [last_full_day - timedelta(days=i) for i in range(trials)]
        day_set = set(day_list)
        by_day: dict[date, list[tuple[float, dict | None]]] = {}
        for record in window_events:
            dt = _parse_iso(record["at"]).astimezone(self._tz)
            if dt.date() not in day_set:
                continue
            by_day.setdefault(dt.date(), []).append(
                (_minute_of_day(dt), record.get("state"))
            )
        info["sample_events"] = sum(len(v) for v in by_day.values())

        start = _minute_of_day(now_local)
        segments = _band_segments(start, window)

        hits = 0
        hour_days = [0] * 24
        for day in day_list:
            matched = [
                (minute, st)
                for (minute, st) in by_day.get(day, ())
                if _state_match(st, query_state)
            ]
            if not matched:
                continue
            if any(_in_band(minute, segments) for minute, _ in matched):
                hits += 1
            for hour in {int(minute // 60) % 24 for minute, _ in matched}:
                hour_days[hour] += 1

        p_minute = hits / trials

        acc = 0.0
        total = 0.0
        for lo, hi in segments:
            for hour in range(24):
                share = _overlap(lo, hi, hour * 60.0, hour * 60.0 + 60.0)
                if share <= 0:
                    continue
                acc += share * (hour_days[hour] / trials)
                total += share
        p_hour = acc / total if total > 0 else 0.0

        k = self._prior_strength
        # 经验先验（v2.4/F11②）：调用方可注入 (prior_mean, prior_weight) 作为经验派生先验，
        # 与既有 k·p_hour 平滑同构（贝叶斯合成多一项证据）。先验缺失或权重<=0 → 公式与接入前
        # 完全一致（零回归）。经验库当前在 src 无调用方，消费闭环待 F12 预触发调用方接入。
        pw = float(prior_weight) if (prior_weight is not None and float(prior_weight) > 0) else 0.0
        if pw > 0 and prior is not None and 0.0 <= float(prior) <= 1.0:
            pm = float(prior)
            p = (hits + k * p_hour + pw * pm) / (trials + k + pw) if (trials + k + pw) > 0 else 0.0
        else:
            p = (hits + k * p_hour) / (trials + k) if (trials + k) > 0 else 0.0
        p = min(1.0, max(0.0, p))

        info.update(
            cold_start=False,
            reason="ok",
            hits=hits,
            p_minute=p_minute,
            p_hour_prior=p_hour,
            experience_prior_mean=(float(prior) if (prior is not None and pw > 0) else None),
            experience_prior_weight=pw,
            band_minutes=[start, start + window],
            p=p,
        )
        return info

    # ── 提前触发 ───────────────────────────────────────────────────
    def pre_trigger(
        self,
        automation_id: str,
        threshold: float = 0.8,
        *,
        state: Mapping[str, Any] | None = None,
        window_minutes: float | None = None,
        prior: float | None = None,
        prior_weight: float | None = None,
    ) -> bool:
        """概率 **>** 阈值时提前触发，并标记 `predicted=True` 防重复。

        返回语义：
            True  = 本次发起了提前触发，且 executor 调用成功（已标记）
            False = 未发起（概率不足 / 已标记过 / executor 失败 / 先验注入无效）

        `prior` / `prior_weight` 为可选的**经验派生先验**（F11②，来自 `af_experience`
        `empirical_prior`）：由调用方（F12 预触发服务）注入，本模块**不 import af_conf /
        af_experience**，保持纯新增契约。`prior` 缺省或 `prior_weight<=0` → 行为与旧版一致。
        """
        """概率 **>** 阈值时提前触发，并标记 `predicted=True` 防重复。

        返回语义：
            True  = 本次发起了提前触发，且 executor 调用成功（已标记）
            False = 未发起（概率不足 / 已标记过 / executor 失败）

        at-most-once：标记**先落盘再触发**；executor 抛异常时标记保留（不会自动重试），
        需要重来请显式 `clear_prediction(automation_id)`。
        """
        aid = _require_id(automation_id)
        thr = _require_threshold(threshold)

        model = self._models.setdefault(aid, _Model(aid))
        if model.predicted and model.predicted.get("predicted"):
            logger.debug("%s 已有 predicted 标记，跳过重复提前触发", aid)
            return False

        query_state = _norm_state(state) if state is not None else self._current_state(aid)
        now = ensure_aware(self._clock.local_now(), who="clock.local_now")
        window = self._pre_window if window_minutes is None else _require_window(window_minutes)

        info = self.explain(
            aid, now, window, state=query_state,
            prior=prior, prior_weight=prior_weight,
        )
        probability = float(info["p"])
        if probability <= thr:
            logger.debug("%s 概率 %.3f 未超过阈值 %.3f", aid, probability, thr)
            return False

        mark = {
            "predicted": True,
            "at": now.isoformat(),
            "probability": round(probability, 9),
            "threshold": thr,
            "window_minutes": window,
            "state": query_state,
            "ok": None,
        }
        model.predicted = mark
        self._save()  # 先落盘再触发：崩溃安全，重启也不会重复写设备

        try:
            result = self._invoke_executor(aid, probability)
        except Exception as exc:  # noqa: BLE001 - 执行器异常不外抛，标记保留
            mark["ok"] = False
            mark["error"] = f"{type(exc).__name__}: {exc}"
            self._save()
            logger.warning(
                "提前触发执行失败（%s），predicted 标记保留以防重复：%s", aid, mark["error"]
            )
            return False

        mark["ok"] = True
        mark["result"] = repr(result)[:200]
        self._save()
        logger.info("提前触发 %s（p=%.3f > %.3f，window=%gmin）", aid, probability, thr, window)
        return True

    def clear_prediction(self, automation_id: str) -> bool:
        """人工解除 `predicted` 标记（重新武装）。返回是否确实有标记被清除。"""
        aid = _require_id(automation_id)
        model = self._models.get(aid)
        if model is None or not model.predicted:
            return False
        model.predicted = None
        self._save()
        return True

    def reset(self, automation_id: str) -> None:
        """丢弃该 automation 的全部模型数据（学习记录 + 标记）并落盘。"""
        aid = _require_id(automation_id)
        self._models.pop(aid, None)
        self._index.pop(aid, None)
        self._save()

    def flush(self) -> None:
        """手动落盘（等价于内部保存动作）。"""
        self._save()

    # ── 内省 ───────────────────────────────────────────────────────
    def hourly(self, automation_id: str) -> list[int]:
        """时间直方图：24 个小时段的触发计数。"""
        idx = self._index.get(_require_id(automation_id))
        return list(idx.hour_hist) if idx else [0] * 24

    def peak_hour(self, automation_id: str) -> int | None:
        """触发最密集的小时段；无数据返回 None。"""
        hist = self.hourly(automation_id)
        if sum(hist) == 0:
            return None
        return max(range(24), key=lambda h: (hist[h], -h))

    def state_counts(self, automation_id: str) -> dict[str, int]:
        """状态组合计数（键为 `k=v,k=v` 规范串）。"""
        idx = self._index.get(_require_id(automation_id))
        return dict(idx.state_counts) if idx else {}

    def state_pattern(self, automation_id: str, state: Mapping[str, Any] | None = None) -> float:
        """给定状态组合下的触发占比 P(state | trigger)。

        查询为空 → 1.0（不条件化）；无历史 → 0.0。
        """
        aid = _require_id(automation_id)
        model = self._models.get(aid)
        if model is None or not model.events:
            return 0.0
        query_state = _norm_state(state)
        if not query_state:
            return 1.0
        matched = sum(1 for e in model.events if _state_match(e.get("state"), query_state))
        return matched / len(model.events)

    def is_predicted(self, automation_id: str) -> bool:
        """是否已带 `predicted=True` 标记。"""
        model = self._models.get(_require_id(automation_id))
        return bool(model and model.predicted and model.predicted.get("predicted"))

    def stats(self, automation_id: str) -> dict:
        """模型体检信息（含派生索引与标记），全部 JSON 可序列化。"""
        aid = _require_id(automation_id)
        model = self._models.get(aid)
        idx = self._index.get(aid)
        if model is None or idx is None:
            return {
                "automation_id": aid,
                "count": 0,
                "dropped": 0,
                "pruned": 0,
                "learned": 0,
                "skipped_predicted": 0,
                "hourly": [0] * 24,
                "peak_hour": None,
                "state_counts": {},
                "predicted": None,
                "first": None,
                "last": None,
            }
        events = model.events
        return {
            "automation_id": aid,
            "count": len(events),
            "dropped": model.dropped,
            "pruned": model.pruned,
            "learned": model.learned,
            "skipped_predicted": model.skipped_predicted,
            "hourly": list(idx.hour_hist),
            "peak_hour": self.peak_hour(aid),
            "state_counts": dict(idx.state_counts),
            "predicted": dict(model.predicted) if model.predicted else None,
            "first": events[0]["at"] if events else None,
            "last": events[-1]["at"] if events else None,
        }

    # ── 私有：依赖适配 ─────────────────────────────────────────────
    def _fetch_history(self, automation_id: str) -> list:
        """从 history_store 拉历史触发事件（可选读侧依赖，拿不到就当空）。"""
        store = self._history_store
        if store is None:
            return []
        for name in HISTORY_METHODS:
            fn = getattr(store, name, None)
            if callable(fn):
                try:
                    raw = _call_flexible(fn, automation_id)
                except Exception as exc:  # noqa: BLE001 - 降级为无历史
                    logger.warning("history_store.%s() 读取失败：%s", name, exc)
                    return []
                return list(raw) if raw else []
            raw = getattr(store, name, None)
            if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
                return list(raw)
        if isinstance(store, Mapping):
            raw = store.get(automation_id)
            return list(raw) if raw else []
        return []

    def _current_state(self, automation_id: str) -> dict[str, str] | None:
        """当前状态快照（pre_trigger 用；拿不到就不条件化）。"""
        store = self._history_store
        if store is None:
            return None
        for name in STATE_METHODS:
            fn = getattr(store, name, None)
            if callable(fn):
                try:
                    return _norm_state(_call_flexible(fn, automation_id))
                except Exception as exc:  # noqa: BLE001 - 降级为不条件化
                    logger.warning("history_store.%s() 读取失败：%s", name, exc)
                    return None
        for name in ("state", "states", "current_state"):
            raw = getattr(store, name, None)
            if isinstance(raw, Mapping):
                return _norm_state(raw)
        return None

    def _invoke_executor(self, automation_id: str, probability: float):
        """按鸭子类型调用 executor，并附带 predicted=True 标记。"""
        target = None
        for name in EXECUTOR_METHODS:
            fn = getattr(self._executor, name, None)
            if callable(fn):
                target = fn
                break
        if target is None:
            if callable(self._executor):
                target = self._executor
            else:
                raise TypeError(f"executor 不支持的接口：{self._executor!r}")
        return _call_flexible(
            target, automation_id, predicted=True, probability=probability
        )

    # ── 私有：模型维护 ─────────────────────────────────────────────
    def _window_events(self, model: _Model, now: datetime) -> list[dict]:
        low = now - timedelta(days=self._window_days)
        return [e for e in model.events if low <= _parse_iso(e["at"]) <= now]

    def _ref_now(self, model: _Model) -> datetime:
        """滑动窗口裁剪基准：max(clock 现在, 最新事件)，避免仿真/回灌时误删数据。"""
        ref = ensure_aware(self._clock.local_now(), who="clock.local_now")
        if model.events:
            newest = max(_parse_iso(e["at"]) for e in model.events)
            if newest > ref:
                ref = newest
        return ref

    def _prune(self, automation_id: str, ref: datetime) -> None:
        model = self._models[automation_id]
        cutoff = ref - timedelta(days=self._window_days)
        keep = [e for e in model.events if _parse_iso(e["at"]) >= cutoff]
        pruned = len(model.events) - len(keep)
        if len(keep) > self._max_events:
            pruned += len(keep) - self._max_events
            keep = keep[-self._max_events:]
        if pruned:
            model.pruned += pruned
            model.events = keep
            model.seen = {_ev_key(e) for e in keep}

    def _reindex(self, automation_id: str) -> None:
        model = self._models[automation_id]
        idx = _Index()
        for record in model.events:
            dt = _parse_iso(record["at"]).astimezone(self._tz)
            idx.by_day.setdefault(dt.date(), []).append(
                (_minute_of_day(dt), record.get("state"))
            )
            idx.hour_hist[dt.hour] += 1
            key = _canon_state_key(record.get("state"))
            if key:
                idx.state_counts[key] = idx.state_counts.get(key, 0) + 1
            idx.count += 1
        model.seen = {_ev_key(e) for e in model.events}
        self._index[automation_id] = idx

    # ── 私有：持久化 ───────────────────────────────────────────────
    def _save(self) -> None:
        payload = {
            "version": MODEL_VERSION,
            "saved_at": ensure_aware(self._clock.local_now(), who="clock.local_now").isoformat(),
            "automations": {},
        }
        for aid, model in self._models.items():
            idx = self._index.get(aid)
            payload["automations"][aid] = {
                "events": model.events,
                "predicted": model.predicted,
                "learned": model.learned,
                "dropped": model.dropped,
                "pruned": model.pruned,
                "skipped_predicted": model.skipped_predicted,
                "hour_hist": list(idx.hour_hist) if idx else [0] * 24,
                "state_counts": dict(idx.state_counts) if idx else {},
            }
        try:
            os.makedirs(self._persist_dir, exist_ok=True)
            atomic_write_text(
                self.predictions_path,
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            )
        except OSError as exc:  # fail-open：写盘失败不打断运行，内存模型继续生效
            logger.warning("预测模型持久化失败（%s），继续使用内存模型", exc)

    def _load(self) -> None:
        path = self.predictions_path
        if not os.path.exists(path):
            return
        try:
            with open(path, encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, ValueError) as exc:
            logger.warning("预测模型文件损坏（%s）→ 隔离并冷启动", exc)
            self._quarantine(path)
            return
        if not isinstance(payload, Mapping) or payload.get("version") != MODEL_VERSION:
            logger.warning("预测模型版本不兼容 → 隔离并冷启动")
            self._quarantine(path)
            return

        automations = payload.get("automations")
        if not isinstance(automations, Mapping):
            return
        for aid, blob in automations.items():
            if not isinstance(blob, Mapping):
                logger.warning("预测模型条目损坏（%r）→ 跳过", aid)
                continue
            model = _Model(str(aid))
            for raw in blob.get("events") or []:
                if not isinstance(raw, Mapping):
                    model.dropped += 1
                    continue
                try:
                    at = _event_datetime(_event_time_value(raw))
                except (TypeError, ValueError):
                    model.dropped += 1
                    continue
                model.events.append(
                    {
                        "at": at.isoformat(),
                        "state": _event_state(raw),
                        "predicted": bool(raw.get("predicted", False)),
                    }
                )
            model.events.sort(key=lambda e: _parse_iso(e["at"]))
            mark = blob.get("predicted")
            model.predicted = dict(mark) if isinstance(mark, Mapping) else None
            try:
                model.learned = int(blob.get("learned", 0))
                model.dropped += int(blob.get("dropped", 0))
                model.pruned = int(blob.get("pruned", 0))
                model.skipped_predicted = int(blob.get("skipped_predicted", 0))
            except (TypeError, ValueError):  # pragma: no cover - 防御
                pass
            self._models[model.automation_id] = model
            self._prune(model.automation_id, self._ref_now(model))
            self._reindex(model.automation_id)

    def _quarantine(self, path: str) -> None:
        """把坏文件挪走（保留现场），下一次保存重建。"""
        try:
            os.replace(path, path + ".corrupt")  # fixed-tmp: exempt(语义是把坏文件挪走保留现场，不是临时文件写；目标名 .corrupt 唯一)
        except OSError as exc:  # pragma: no cover - 只读目录等
            logger.warning("无法隔离损坏的预测模型文件：%s", exc)


# `learn()` 的 isinstance 防御用（避免 typing/collections.abc 混用）
from collections.abc import Iterable as Iterable_abc  # noqa: E402