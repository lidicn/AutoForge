"""AutoForge · 自动化健康度引擎（af_health，§0 契约）。

聚合六维信号 → 0-100 健康分，支持自动降级（转 shadow）与告警::

    成功率 25% / 用户干预率 20% / 冲突率 15% / canary 漂移 15% /
    conf 趋势 15% / 触发频率 10%

公开接口（与提单契约逐字对齐）::

    health_score(automation_id) -> int
    health_report() -> list[dict]
    auto_demote(threshold=30) -> list[str]
    alert(threshold=50) -> list[dict]
    history(automation_id, days=7) -> list[dict]

设计要点
--------
* **零内部依赖**：五个数据源 ``executor_stats / conflict_audit /
  intervention_detector / canary_supervisor / conf_store`` 全部由装配层注入，
  本模块只用 duck typing 读它们的公开只读接口（见 ``_read_*``），不 import 任何
  autoforge 内部实现，也不 import 第三方库（只用标准库）。
* **可复算**：``compute_health(inputs, policy)`` 是纯函数；``health_score`` /
  ``health_report`` / ``alert`` / ``history`` 对引擎状态**只读**。全模块只有两个
  写点：``snapshot()``（显式记历史）与 ``auto_demote()``（显式降级）。
* **失败语义（fail-open 计分 / fail-safe 落库）**：数据源抛异常、字段缺失、口径
  不明 → 该维度按 ``unknown_score``（默认 50，中性）计分并在 ``warnings`` 点名，
  绝不向上抛，避免健康度自身成为单点。``auto_demote`` 的降级副作用失败只进
  ``demote_errors``，返回值照常给出。
* **无 IO**：不读写任何文件（"不碰现网配置"），历史仅驻内存环形缓冲。

⚠️ 兼容边界（详见 §7）：af_conflict 的事件时间戳来自 ``TimeSource.monotonic()``，
与本模块的墙钟不在同一时钟域，因此**默认不做事件时间过滤**（``filter_events_by_time``
默认 False），"窗口"由审计器自身的内存环承担。
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "AUTO_MIN", "SHADOW_LOW", "WEIGHTS", "DIM_NAMES",
    "HealthPolicy", "HealthInputs", "DimScore", "HealthResult", "HealthEngine",
    "compute_health", "band_of", "round_half_up",
    "configure", "set_engine", "get_engine",
    "health_score", "health_report", "auto_demote", "alert", "history",
]


# ======================================================================
#  阈值镜像（与 af_conf.decision_for / G2 编译期闸门同一套口径）
# ======================================================================

#: af_conf.AUTO_MIN 的镜像常量（§1 已内联可见）。改 af_conf 必须同步这里。
AUTO_MIN = 0.85
#: af_conf.SHADOW_LOW 的镜像常量。
SHADOW_LOW = 0.60

#: 六维权重（§0 契约，和必须为 1.00）
WEIGHTS: dict[str, float] = {
    "success_rate": 0.25,
    "intervention_rate": 0.20,
    "conflict_rate": 0.15,
    "canary_drift": 0.15,
    "conf_trend": 0.15,
    "trigger_frequency": 0.10,
}

#: 维度顺序（report / history 里稳定输出）
DIM_NAMES: tuple[str, ...] = (
    "success_rate",
    "intervention_rate",
    "conflict_rate",
    "canary_drift",
    "conf_trend",
    "trigger_frequency",
)


# ======================================================================
#  小工具
# ======================================================================

_MISSING: Any = object()


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else (hi if x > hi else x)


def _clamp01(x: float) -> float:
    return _clamp(float(x), 0.0, 1.0)


def _linear(value: float, worst: float, best: float) -> float:
    """把 ``value`` 线性映射到 0..1：``worst`` → 0，``best`` → 1，两端外截断。

    ``worst > best`` 表示「越小越好」（率类指标）。``worst == best`` 时退化为阈值判断。
    """
    if worst == best:
        return 1.0 if value == best else 0.0
    return _clamp01((value - worst) / (best - worst))


def round_half_up(x: float) -> int:
    """四舍五入（0.5 向上），不用内置 round（那是 banker's rounding）。"""
    return int(math.floor(x + 0.5)) if x >= 0 else -int(math.floor(-x + 0.5))


def _field(obj: Any, name: str, default: Any = None) -> Any:
    """Mapping / 对象统一取字段。"""
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _pick(entry: Any, aliases: tuple[str, ...]) -> int | None:
    for name in aliases:
        value = _field(entry, name, _MISSING)
        if value is not _MISSING and value is not None:
            n = _int(value)
            if n is not None:
                return n
    return None


def _int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def band_of(conf: float | None) -> str:
    """af_conf.decision_for 的镜像（§1 内联可见的三级自主口径）。"""
    if conf is None:
        return "unknown"
    if conf >= AUTO_MIN:
        return "auto"
    if conf >= SHADOW_LOW:
        return "shadow"
    return "ask"


def _event_involves(event: Any, automation_id: str) -> bool:
    """事件是否与某自动化相关（请求者或被抢占的持有者）。

    与 af_conflict_audit.ConflictAuditor._involved 的口径对齐（用户动作的
    requester_id 固定为 "user"，天然不会误算进自动化）。
    """
    return (
        _field(event, "requester_id") == automation_id
        or _field(event, "holder_id") == automation_id
    )


# executor_stats 的字段别名表（af_executor 未内联，见 §7 回读清单）
_EXEC_ALIASES: dict[str, tuple[str, ...]] = {
    "attempts": ("attempts", "runs", "total", "dispatched", "calls", "executions"),
    "successes": ("successes", "success_count", "success", "ok", "succeeded"),
    "failures": ("failures", "failure_count", "failed", "errors", "error_count"),
    "triggers": ("triggers", "trigger_count", "triggered", "fired"),
}


# ======================================================================
#  策略与数据载体
# ======================================================================

@dataclass(frozen=True)
class HealthPolicy:
    """全部阈值可配（默认值即 §0/§1 口径的原型期取值）。"""

    window_days: float = 7.0              # 统计/归一化窗口
    unknown_score: float = 50.0           # 缺数据维度的中性分
    success_worst: float = 0.50           # 成功率 <= 0.50 → 0 分
    intervention_worst: float = 0.20      # 干预率 >= 20% → 0 分
    conflict_worst: float = 0.20          # 冲突率 >= 20% → 0 分
    canary_drift_penalty: float = 25.0    # 每次漂移扣分
    canary_demoted_score: float = 0.0     # canary 被降级 → 该维 0 分
    conf_trend_gain: float = 2.0          # 趋势增益
    conf_trend_clip: float = 0.10         # 单步趋势截断
    trigger_ideal_lo: float = 0.25        # 健康带下沿（次/天）
    trigger_ideal_hi: float = 24.0        # 健康带上沿（次/天）
    trigger_runaway_hi: float = 72.0      # 达到即 0 分（次/天）
    trigger_stale_score: float = 40.0     # 完全不触发时的分
    critical_below: float = 30.0          # alert 严重度分界（与 auto_demote 阈值同源）
    dim_warn_below: float = 60.0          # 单维告警线
    conflict_limit: int = 1000            # 单次读取的事件上限
    conflict_kinds: frozenset[str] = frozenset({
        "rejected", "preempted", "throttled", "flicker",
        "circuit_open", "circuit_half_open", "starved",
    })                                    # 空集合 = 不过滤
    filter_events_by_time: bool = False   # 默认关：事件时间戳可能是 monotonic
    demote_epsilon: float = 0.01          # 降到 AUTO_MIN - eps → 进入 shadow 带


@dataclass(frozen=True)
class HealthInputs:
    """一个自动化的归一化输入（``compute_health`` 的唯一入口）。"""

    automation_id: str
    window_days: float = 7.0
    attempts: int | None = None
    successes: int | None = None
    failures: int | None = None
    triggers: int | None = None
    overrides: int | None = None          # manual_override 条数
    attributed: int | None = None         # 可归因条数（af_caused + manual_override）
    conflicts: int | None = None
    canary_drift: int | None = None
    canary_actions: int | None = None
    canary_status: str | None = None
    conf: float | None = None
    conf_prev: float | None = None        # 上一次快照的 conf（趋势项）
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class DimScore:
    """单维打分结果。"""

    name: str
    weight: float
    raw: float | None
    score: float
    known: bool
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "weight": self.weight,
            "raw": self.raw,
            "score": self.score,
            "known": self.known,
            "note": self.note,
        }


@dataclass(frozen=True)
class HealthResult:
    """健康分计算结果（JSON 兼容，``to_dict`` 可直接进 WebUI）。"""

    automation_id: str
    score: int
    dims: Mapping[str, DimScore]
    conf: float | None
    inputs: HealthInputs
    warnings: tuple[str, ...] = ()

    def failing(self, policy: HealthPolicy | None = None) -> list[str]:
        policy = policy or HealthPolicy()
        return [n for n in DIM_NAMES if self.dims[n].known and self.dims[n].score < policy.dim_warn_below]

    def unknown_dims(self) -> list[str]:
        return [n for n in DIM_NAMES if not self.dims[n].known]

    def to_dict(self, policy: HealthPolicy | None = None) -> dict[str, Any]:
        policy = policy or HealthPolicy()
        return {
            "automation_id": self.automation_id,
            "score": self.score,
            "conf": self.conf,
            "dims": {n: self.dims[n].to_dict() for n in DIM_NAMES},
            "failing": self.failing(policy),
            "unknown_dims": self.unknown_dims(),
            "inputs": inputs_to_dict(self.inputs),
            "warnings": list(self.warnings),
        }


def inputs_to_dict(inputs: HealthInputs) -> dict[str, Any]:
    return {
        "automation_id": inputs.automation_id,
        "window_days": inputs.window_days,
        "attempts": inputs.attempts,
        "successes": inputs.successes,
        "failures": inputs.failures,
        "triggers": inputs.triggers,
        "overrides": inputs.overrides,
        "attributed": inputs.attributed,
        "conflicts": inputs.conflicts,
        "canary_drift": inputs.canary_drift,
        "canary_actions": inputs.canary_actions,
        "canary_status": inputs.canary_status,
        "conf": inputs.conf,
        "conf_prev": inputs.conf_prev,
        "warnings": list(inputs.warnings),
    }


# ======================================================================
#  六维打分（纯函数，固定输入 → 固定输出）
# ======================================================================

def _unknown(name: str, policy: HealthPolicy, note: str) -> DimScore:
    return DimScore(name, WEIGHTS[name], None, policy.unknown_score, False, note)


def score_success(inputs: HealthInputs, policy: HealthPolicy) -> DimScore:
    att, suc = inputs.attempts, inputs.successes
    if att is None or suc is None or att <= 0:
        return _unknown("success_rate", policy, "no attempts in window")
    ratio = _clamp01(suc / att)
    return DimScore(
        "success_rate", WEIGHTS["success_rate"], ratio,
        100.0 * _linear(ratio, policy.success_worst, 1.0), True, "",
    )


def score_intervention(inputs: HealthInputs, policy: HealthPolicy) -> DimScore:
    ov = inputs.overrides
    den = inputs.attributed if (inputs.attributed or 0) > 0 else inputs.attempts
    if ov is None or den is None or den <= 0:
        note = "no attributed actions" if ov is not None else "intervention_detector unavailable"
        return _unknown("intervention_rate", policy, note)
    rate = _clamp01(ov / den)
    note = "" if (inputs.attributed or 0) > 0 else "denominator falls back to attempts"
    return DimScore(
        "intervention_rate", WEIGHTS["intervention_rate"], rate,
        100.0 * _linear(rate, policy.intervention_worst, 0.0), True, note,
    )


def _ops(inputs: HealthInputs) -> int | None:
    """冲突率分母：优先取动作次数，退化取触发次数。"""
    if (inputs.attempts or 0) > 0:
        return inputs.attempts
    return inputs.triggers


def score_conflict(inputs: HealthInputs, policy: HealthPolicy) -> DimScore:
    cf = inputs.conflicts
    if cf is None:
        return _unknown("conflict_rate", policy, "conflict_audit unavailable")
    ops = _ops(inputs)
    if ops is None or ops <= 0:
        if cf <= 0:
            return DimScore("conflict_rate", WEIGHTS["conflict_rate"], 0.0, 100.0,
                            True, "zero conflicts; no denominator needed")
        return _unknown("conflict_rate", policy, "no denominator for conflict rate")
    rate = _clamp01(cf / ops)
    return DimScore(
        "conflict_rate", WEIGHTS["conflict_rate"], rate,
        100.0 * _linear(rate, policy.conflict_worst, 0.0), True, "",
    )


def score_canary(inputs: HealthInputs, policy: HealthPolicy) -> DimScore:
    drift = inputs.canary_drift
    if drift is None:
        return _unknown("canary_drift", policy, "no canary record")
    drift = max(0, drift)
    score = _clamp(100.0 - drift * policy.canary_drift_penalty, 0.0, 100.0)
    status = (inputs.canary_status or "").strip().lower()
    note = f"status={status}" if status else ""
    if status == "demoted":
        score = policy.canary_demoted_score
        note = "canary demoted"
    raw = drift / max(1, inputs.canary_actions or 0)   # 漂移/动作，仅供观察
    return DimScore("canary_drift", WEIGHTS["canary_drift"], raw, score, True, note)


def score_conf(inputs: HealthInputs, policy: HealthPolicy) -> DimScore:
    conf = inputs.conf
    if conf is None:
        return _unknown("conf_trend", policy, "conf_store unavailable")
    level = _clamp01(conf)
    adjusted = level
    note = "level only"
    if inputs.conf_prev is not None:
        delta = _clamp(level - _clamp01(inputs.conf_prev),
                       -policy.conf_trend_clip, policy.conf_trend_clip)
        adjusted = _clamp01(level + policy.conf_trend_gain * delta)
        note = "trend gain applied"
    return DimScore("conf_trend", WEIGHTS["conf_trend"], adjusted,
                    100.0 * adjusted, True, note)


def score_trigger(inputs: HealthInputs, policy: HealthPolicy) -> DimScore:
    triggers = inputs.triggers
    if triggers is None:
        return _unknown("trigger_frequency", policy, "executor_stats has no trigger counter")
    days = policy.window_days if policy.window_days > 0 else 1.0
    rate = max(0, triggers) / days
    lo, hi, run = policy.trigger_ideal_lo, policy.trigger_ideal_hi, policy.trigger_runaway_hi
    if rate <= 0.0:
        score, note = policy.trigger_stale_score, "idle"
    elif rate < lo:
        score = policy.trigger_stale_score + (100.0 - policy.trigger_stale_score) * (rate / lo)
        note = "below ideal band"
    elif rate <= hi:
        score, note = 100.0, "in ideal band"
    elif rate < run:
        score, note = 100.0 * _linear(rate, run, hi), "above ideal band"
    else:
        score, note = 0.0, "runaway triggering"
    return DimScore("trigger_frequency", WEIGHTS["trigger_frequency"], rate, score, True, note)


def compute_health(inputs: HealthInputs, policy: HealthPolicy | None = None) -> HealthResult:
    """纯函数：给定输入算健康分。固定输入 → 固定输出，可离线复算。"""
    policy = policy or HealthPolicy()
    dims = (
        score_success(inputs, policy),
        score_intervention(inputs, policy),
        score_conflict(inputs, policy),
        score_canary(inputs, policy),
        score_conf(inputs, policy),
        score_trigger(inputs, policy),
    )
    total_w = sum(d.weight for d in dims) or 1.0
    total = sum(d.score * d.weight for d in dims) / total_w
    score = round_half_up(_clamp(total, 0.0, 100.0))
    return HealthResult(
        automation_id=inputs.automation_id,
        score=score,
        dims={d.name: d for d in dims},
        conf=inputs.conf,
        inputs=inputs,
        warnings=inputs.warnings,
    )


# ======================================================================
#  引擎：采集（duck typing，只读）+ 打分 + 降级/告警/历史
# ======================================================================

@dataclass
class HealthEngine:
    """健康度引擎。五个数据源任意缺失都能跑（该维度计中性分）。"""

    executor_stats: Any = None
    conflict_audit: Any = None
    intervention_detector: Any = None
    canary_supervisor: Any = None
    conf_store: Any = None
    policy: HealthPolicy = field(default_factory=HealthPolicy)
    clock: Callable[[], float] = time.time
    demoter: Callable[[str], Any] | None = None   # 注入降级动作（覆盖缺省 conf 压档）
    extra_ids: set[str] = field(default_factory=set)
    history_max: int = 4096
    warnings: list[str] = field(default_factory=list)
    demote_errors: list[dict] = field(default_factory=list)
    _history: dict[str, list[dict]] = field(default_factory=dict)
    _last_conf: dict[str, float] = field(default_factory=dict)
    _demotions: dict[str, dict] = field(default_factory=dict)

    # ---------------------------------------------------------- 基础
    def _now(self) -> float:
        return float(self.clock())

    def _warn(self, msg: str) -> None:
        self.warnings.append(msg)
        if len(self.warnings) > 200:
            del self.warnings[:-200]

    def register(self, *automation_ids: str) -> None:
        """显式登记 automation_id（例如：已编译但还没跑过的自动化）。"""
        for aid in automation_ids:
            if isinstance(aid, str) and aid:
                self.extra_ids.add(aid)

    def automation_ids(self) -> list[str]:
        """全集 = 五个数据源里出现过的 id ∪ register()，排序保证确定性。"""
        ids: set[str] = set(self.extra_ids)

        def _add(value: Any) -> None:
            if isinstance(value, str) and value:
                ids.add(value)

        def _add_many(values: Any) -> None:
            for v in values or ():
                _add(v)

        # conf_store
        try:
            for name in ("values", "samples"):
                mapping = getattr(self.conf_store, name, None)
                if isinstance(mapping, Mapping):
                    _add_many(mapping.keys())
        except Exception as exc:                       # fail-open：只记诊断
            self._warn(f"ids/conf_store: {exc!r}")
        # conflict_audit
        try:
            fn = getattr(self.conflict_audit, "automation_ids", None)
            if callable(fn):
                _add_many(fn())
            else:
                for ev in getattr(self.conflict_audit, "events", None) or ():
                    _add(_field(ev, "requester_id"))
                    _add(_field(ev, "holder_id"))
        except Exception as exc:
            self._warn(f"ids/conflict_audit: {exc!r}")
        # intervention_detector
        try:
            for rec in getattr(self.intervention_detector, "records", None) or ():
                _add(_field(rec, "automation_id"))
        except Exception as exc:
            self._warn(f"ids/intervention_detector: {exc!r}")
        # canary_supervisor
        try:
            records = getattr(self.canary_supervisor, "records", None)
            if isinstance(records, Mapping):
                for key, rec in records.items():
                    _add(key)
                    _add(_field(rec, "automation_id"))
        except Exception as exc:
            self._warn(f"ids/canary_supervisor: {exc!r}")
        # executor_stats
        try:
            if isinstance(self.executor_stats, Mapping):
                _add_many(self.executor_stats.keys())
            else:
                fn = getattr(self.executor_stats, "automation_ids", None) \
                    or getattr(self.executor_stats, "ids", None)
                if callable(fn):
                    _add_many(fn())
        except Exception as exc:
            self._warn(f"ids/executor_stats: {exc!r}")
        return sorted(ids)

    # ---------------------------------------------------------- 采集器
    def _read_exec(self, automation_id: str, warnings: list[str]) -> dict[str, int | None]:
        empty = {"attempts": None, "successes": None, "failures": None, "triggers": None}
        src = self.executor_stats
        if src is None:
            return dict(empty)
        try:
            entry: Any = None
            for name in ("stats", "get", "for_automation", "stat_for", "get_stats"):
                fn = getattr(src, name, None)
                if callable(fn):
                    entry = fn(automation_id)
                    if entry is not None:
                        break
            if entry is None and isinstance(src, Mapping):
                entry = src.get(automation_id)
            if entry is None:
                return dict(empty)
            out = {k: _pick(entry, aliases) for k, aliases in _EXEC_ALIASES.items()}
            if out["attempts"] is None and out["successes"] is not None and out["failures"] is not None:
                out["attempts"] = out["successes"] + out["failures"]
            if out["successes"] is None and out["attempts"] is not None and out["failures"] is not None:
                out["successes"] = max(0, out["attempts"] - out["failures"])
            return out
        except Exception as exc:
            warnings.append(f"executor_stats[{automation_id}]: {exc!r}")
            return dict(empty)

    def _read_conflicts(self, automation_id: str, since: float, warnings: list[str]) -> int | None:
        src = self.conflict_audit
        if src is None:
            return None
        try:
            getter = getattr(src, "history", None)
            if callable(getter):
                events = list(getter(automation_id=automation_id, limit=self.policy.conflict_limit) or ())
            else:
                events = [e for e in (getattr(src, "events", None) or ()) if _event_involves(e, automation_id)]
            count = 0
            kinds = self.policy.conflict_kinds
            for ev in events:
                kind = str(_field(ev, "kind", "") or "")
                if kinds and kind and kind not in kinds:
                    continue
                if self.policy.filter_events_by_time:
                    ts = _float(_field(ev, "timestamp"))
                    if ts is not None and ts < since:
                        continue
                count += 1
            return count
        except Exception as exc:
            warnings.append(f"conflict_audit[{automation_id}]: {exc!r}")
            return None

    def _read_interventions(self, automation_id: str, warnings: list[str]) -> tuple[int | None, int | None]:
        src = self.intervention_detector
        if src is None:
            return None, None
        try:
            records = list(getattr(src, "records", None) or ())
            if records:
                overrides = attributed = 0
                for rec in records:
                    if str(_field(rec, "automation_id") or "") != automation_id:
                        continue
                    verdict = _field(rec, "verdict", "")
                    verdict = str(getattr(verdict, "value", verdict) or "")
                    if verdict == "manual_override":
                        overrides += 1
                        attributed += 1
                    elif verdict == "af_caused":
                        attributed += 1
                    # untracked / 其它 verdict 不进分母（不被任何自动化管理）
                return overrides, attributed
            # 兜底：只有 overrides() 可用 → 分子可知、分母不可知
            fn = getattr(src, "overrides", None)
            if callable(fn):
                n = sum(1 for r in (fn() or ())
                        if str(_field(r, "automation_id") or "") == automation_id)
                return n, None
            return 0, 0
        except Exception as exc:
            warnings.append(f"intervention_detector[{automation_id}]: {exc!r}")
            return None, None

    def _read_canary(self, automation_id: str, warnings: list[str]
                     ) -> tuple[int | None, int | None, str | None]:
        src = self.canary_supervisor
        if src is None:
            return None, None, None
        try:
            rec: Any = None
            fn = getattr(src, "record", None)
            if callable(fn):
                rec = fn(automation_id)
            if rec is None:
                records = getattr(src, "records", None)
                if isinstance(records, Mapping):
                    rec = records.get(automation_id)
            if rec is None:
                return None, None, None
            status = _field(rec, "status")
            return (_int(_field(rec, "drift_count")),
                    _int(_field(rec, "actions")),
                    str(status) if status is not None else None)
        except Exception as exc:
            warnings.append(f"canary_supervisor[{automation_id}]: {exc!r}")
            return None, None, None

    def _read_conf(self, automation_id: str, warnings: list[str]) -> float | None:
        src = self.conf_store
        if src is None:
            return None
        try:
            fn = getattr(src, "get", None)
            if callable(fn):
                return _float(fn(automation_id))
            if isinstance(src, Mapping):
                return _float(src.get(automation_id))
            return None
        except Exception as exc:
            warnings.append(f"conf_store[{automation_id}]: {exc!r}")
            return None

    def inputs(self, automation_id: str, at: float | None = None) -> HealthInputs:
        """把五个数据源归一化成 ``HealthInputs``（只读）。"""
        now = self._now() if at is None else float(at)
        window = float(self.policy.window_days)
        since = now - window * 86400.0
        warnings: list[str] = []
        ex = self._read_exec(automation_id, warnings)
        overrides, attributed = self._read_interventions(automation_id, warnings)
        drift, actions, status = self._read_canary(automation_id, warnings)
        return HealthInputs(
            automation_id=automation_id,
            window_days=window,
            attempts=ex["attempts"],
            successes=ex["successes"],
            failures=ex["failures"],
            triggers=ex["triggers"],
            overrides=overrides,
            attributed=attributed,
            conflicts=self._read_conflicts(automation_id, since, warnings),
            canary_drift=drift,
            canary_actions=actions,
            canary_status=status,
            conf=self._read_conf(automation_id, warnings),
            conf_prev=self._last_conf.get(automation_id),
            warnings=tuple(warnings),
        )

    # ---------------------------------------------------------- 契约接口
    def health_score(self, automation_id: str) -> int:
        """健康分 0-100（只读、无副作用、确定性）。"""
        return compute_health(self.inputs(automation_id), self.policy).score

    def _band(self, automation_id: str, inputs: HealthInputs) -> str:
        fn = getattr(self.conf_store, "band", None)
        if callable(fn):
            try:
                return str(fn(automation_id))
            except Exception:
                pass
        return band_of(inputs.conf)

    def _entry(self, automation_id: str, now: float) -> dict[str, Any]:
        inputs = self.inputs(automation_id, at=now)
        entry = compute_health(inputs, self.policy).to_dict(self.policy)
        entry["band"] = self._band(automation_id, inputs)
        entry["demoted"] = automation_id in self._demotions
        entry["at"] = now
        return entry

    def health_report(self) -> list[dict]:
        """全量健康报告，按 (score, automation_id) 升序（最差在前）。"""
        now = self._now()
        out = [self._entry(aid, now) for aid in self.automation_ids()]
        out.sort(key=lambda e: (e["score"], e["automation_id"]))
        return out

    def auto_demote(self, threshold: float = 30) -> list[str]:
        """健康分 < threshold 的自动化自动转 shadow。

        返回值 = 当前低于阈值的 automation_id 列表（排序、幂等、可重复调用）。
        降级副作用：优先调用注入的 ``demoter(automation_id)``；缺省实现把
        conf_store.values 里的 conf 压到 ``AUTO_MIN - demote_epsilon``（= 0.84，
        正好落进 shadow 带），已在 shadow/ask 的不动（幂等）。
        副作用失败只进 ``demote_errors``，不影响返回值（fail-open 上报）。
        """
        now = self._now()
        below = [e for e in self.health_report() if e["score"] < threshold]
        ids = [e["automation_id"] for e in below]
        for entry in below:
            aid = entry["automation_id"]
            already = aid in self._demotions
            previous_conf = self._read_conf(aid, [])
            try:
                if self.demoter is not None:
                    self.demoter(aid)
                else:
                    self._fallback_demote(aid)
            except Exception as exc:
                self.demote_errors.append(
                    {"automation_id": aid, "at": now, "error": repr(exc)})
            if not already:
                self._demotions[aid] = {
                    "at": now, "score": entry["score"],
                    "previous_conf": previous_conf,
                    "target_band": "shadow",
                }
        return ids

    def _fallback_demote(self, automation_id: str) -> bool:
        values = getattr(self.conf_store, "values", None)
        if not isinstance(values, MutableMapping):
            raise RuntimeError("no demoter and conf_store.values is not a mutable mapping")
        target = AUTO_MIN - self.policy.demote_epsilon
        current = values.get(automation_id)
        if current is None:
            values[automation_id] = target
            return True
        if float(current) >= AUTO_MIN:
            values[automation_id] = target
            return True
        return False          # 已在 shadow/ask：不再下压（幂等）

    def alert(self, threshold: float = 50) -> list[dict]:
        """健康分 < threshold 的告警事件列表（只读、确定性、无 IO）。"""
        now = self._now()
        events: list[dict] = []
        for entry in self.health_report():
            score = entry["score"]
            if score >= threshold:
                continue
            severity = "critical" if score < self.policy.critical_below else "warning"
            events.append({
                "kind": "health_alert",
                "automation_id": entry["automation_id"],
                "score": score,
                "threshold": threshold,
                "severity": severity,
                "band": entry["band"],
                "demoted": entry["demoted"],
                "demote_recommended": score < self.policy.critical_below,
                "failing": entry["failing"],
                "unknown_dims": entry["unknown_dims"],
                "dims": entry["dims"],
                "message": f"健康分 {score} 低于阈值 {threshold}：{entry['automation_id']}",
                "at": now,
            })
        return events

    # ---------------------------------------------------------- 历史
    def snapshot(self, at: float | None = None,
                 automation_ids: list[str] | None = None) -> list[dict]:
        """显式记一条健康分历史（唯一的记账入口，可复算点）。"""
        now = self._now() if at is None else float(at)
        ids = sorted(automation_ids) if automation_ids is not None else self.automation_ids()
        out: list[dict] = []
        for aid in ids:
            inputs = self.inputs(aid, at=now)
            result = compute_health(inputs, self.policy)
            entry = {
                "automation_id": aid,
                "at": now,
                "seq": len(self._history.get(aid, ())),
                "score": result.score,
                "conf": inputs.conf,
                "dims": {n: result.dims[n].score for n in DIM_NAMES},
            }
            rows = self._history.setdefault(aid, [])
            rows.append(entry)
            if len(rows) > self.history_max:
                del rows[: len(rows) - self.history_max]
            if inputs.conf is not None:
                self._last_conf[aid] = inputs.conf
            out.append(dict(entry))
        return out

    def history(self, automation_id: str, days: float = 7,
                desc: bool = False) -> list[dict]:
        """健康分历史，按时间升序（``desc=True`` 反转），窗口 ``[now-days, now]``。"""
        now = self._now()
        since = now - float(days) * 86400.0
        rows = [dict(r) for r in self._history.get(automation_id, ()) if r["at"] >= since]
        rows.sort(key=lambda r: (r["at"], r["seq"]))
        return list(reversed(rows)) if desc else rows

    def clear_demotion(self, automation_id: str | None = None) -> None:
        """人工复位降级标记（恢复路径本单未定义，见 §7-6）。"""
        if automation_id is None:
            self._demotions.clear()
        else:
            self._demotions.pop(automation_id, None)


# ======================================================================
#  模块级 facade（§0 的函数签名形态；默认引擎可 configure()）
# ======================================================================

_ENGINE: HealthEngine | None = None


def set_engine(engine: HealthEngine) -> HealthEngine:
    global _ENGINE
    _ENGINE = engine
    return _ENGINE


def configure(**kwargs: Any) -> HealthEngine:
    """装配默认引擎（数据源、policy、clock、demoter 全部从这里注入）。"""
    return set_engine(HealthEngine(**kwargs))


def get_engine() -> HealthEngine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = HealthEngine()
    return _ENGINE


def health_score(automation_id: str) -> int:
    return get_engine().health_score(automation_id)


def health_report() -> list[dict]:
    return get_engine().health_report()


def auto_demote(threshold: float = 30) -> list[str]:
    return get_engine().auto_demote(threshold)


def alert(threshold: float = 50) -> list[dict]:
    return get_engine().alert(threshold)


def history(automation_id: str, days: float = 7) -> list[dict]:
    return get_engine().history(automation_id, days)