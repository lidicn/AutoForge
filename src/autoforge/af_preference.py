"""af_preference —— 用户偏好模型：学习用户在不同场景下对自动化动作的接受/拒绝。

核心：
- record(action, context, accepted)：记录用户接受/拒绝
- preference(context) -> dict：返回该场景下用户偏好的动作/参数及接受率
- suggest(automation_id) -> list：基于偏好建议调整自动化参数

上下文维度：小时段（morning/afternoon/evening/night）、工作日/周末、场景标签。
零新依赖，持久化到 persist_dir/preferences.json。
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

__all__ = [
    "PreferenceRecord",
    "PreferenceModel",
    "context_key",
    "time_bucket",
]

PREFERENCES_FILE = "preferences.json"
DEFAULT_MIN_SAMPLES = 3
DEFAULT_CONFIDENCE_THRESHOLD = 0.6


def time_bucket(hour: int) -> str:
    """将小时映射到时段标签。"""
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 22:
        return "evening"
    return "night"


def context_key(
    *,
    hour: int | None = None,
    weekday: int | None = None,
    scene: str | None = None,
    entity_id: str | None = None,
) -> str:
    """生成上下文键。维度缺失时用 '*' 通配。

    键格式：time=morning|weekday=workday|scene=home|entity=light.living
    """
    parts = []
    if hour is not None:
        parts.append(f"time={time_bucket(hour)}")
    else:
        parts.append("time=*")
    if weekday is not None:
        parts.append(f"weekday={'weekend' if weekday >= 5 else 'workday'}")
    else:
        parts.append("weekday=*")
    parts.append(f"scene={scene or '*'}")
    parts.append(f"entity={entity_id or '*'}")
    return "|".join(parts)


@dataclass
class PreferenceRecord:
    """单条偏好记录。"""
    record_id: str
    automation_id: str | None
    action: str
    params: dict[str, Any]
    context: str
    accepted: bool
    timestamp: float
    details: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict:
        return asdict(self)


@dataclass
class _Stat:
    """某个 (context, action, params_key) 的统计。"""
    accepted: int = 0
    rejected: int = 0
    last_seen: float = 0.0

    @property
    def total(self) -> int:
        return self.accepted + self.rejected

    @property
    def rate(self) -> float:
        if self.total == 0:
            return 0.0
        return self.accepted / self.total


class PreferenceModel:
    """用户偏好模型。

    用法::

        model = PreferenceModel(persist_dir="/tmp")
        model.record("turn_on", {"entity_id": "light.living", "brightness": 80},
                     context={"hour": 20, "weekday": 5, "scene": "movie"}, accepted=True)
        prefs = model.preference({"hour": 20, "weekday": 5, "scene": "movie"})
        suggestions = model.suggest("auto_123")
    """

    def __init__(
        self,
        persist_dir: str | None = None,
        *,
        min_samples: int = DEFAULT_MIN_SAMPLES,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
        clock: Callable[[], float] = time.time,
        tz: Any = None,
    ) -> None:
        self._persist_dir = persist_dir
        self._min_samples = min_samples
        self._confidence_threshold = confidence_threshold
        self._clock = clock
        self._tz = tz
        self._records: list[PreferenceRecord] = []
        # stats[context][action][params_frozen] = _Stat
        self._stats: dict[str, dict[str, dict[str, _Stat]]] = {}
        self._load()

    # ── 记录 ────────────────────────────────────────────────────────
    def record(
        self,
        action: str,
        params: Mapping[str, Any] | None = None,
        *,
        context: Mapping[str, Any] | None = None,
        accepted: bool,
        automation_id: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> PreferenceRecord:
        """记录用户接受/拒绝一个自动化动作。

        Args:
            action: 动作名（如 turn_on / set_temperature）
            params: 动作参数（如 {"entity_id": "light.living", "brightness": 80}）
            context: 上下文（hour/weekday/scene/entity_id）
            accepted: 用户是否接受
            automation_id: 关联的自动化 ID
            details: 额外信息（如用户手动调整后的值）
        """
        ctx = self._normalize_context(context)
        params_dict = dict(params) if params else {}
        rec = PreferenceRecord(
            record_id=uuid.uuid4().hex,
            automation_id=automation_id,
            action=action,
            params=params_dict,
            context=ctx,
            accepted=bool(accepted),
            timestamp=self._clock(),
            details=dict(details) if details else {},
        )
        self._records.append(rec)
        self._update_stats(ctx, action, params_dict, rec.accepted, rec.timestamp)
        self._save()
        return rec

    def record_accept(self, action: str, params=None, *, context=None, automation_id=None, details=None) -> PreferenceRecord:
        """便捷方法：记录用户接受。"""
        return self.record(action, params, context=context, accepted=True, automation_id=automation_id, details=details)

    def record_reject(self, action: str, params=None, *, context=None, automation_id=None, details=None) -> PreferenceRecord:
        """便捷方法：记录用户拒绝（含手动覆盖）。"""
        return self.record(action, params, context=context, accepted=False, automation_id=automation_id, details=details)

    # ── 查询 ────────────────────────────────────────────────────────
    def preference(
        self,
        context: Mapping[str, Any] | None = None,
        *,
        action: str | None = None,
        min_samples: int | None = None,
    ) -> dict[str, Any]:
        """返回指定上下文下用户偏好的动作/参数及接受率。

        返回结构::

            {
                "context": "...",
                "actions": {
                    "turn_on": {
                        "rate": 0.8,
                        "total": 10,
                        "params": {"brightness": {"80": 0.75, "100": 0.5}},
                    }
                },
                "best_action": "turn_on",
                "best_params": {"brightness": 80},
                "confidence": 0.75,
            }
        """
        ctx = self._normalize_context(context)
        min_s = min_samples if min_samples is not None else self._min_samples

        result: dict[str, Any] = {
            "context": ctx,
            "actions": {},
            "best_action": None,
            "best_params": {},
            "confidence": 0.0,
        }

        ctx_stats = self._stats.get(ctx, {})
        if not ctx_stats:
            # 尝试通配匹配
            ctx_stats = self._wildcard_match(context)

        best_rate = -1.0
        best_action = None
        best_params: dict[str, Any] = {}

        for act, param_stats in ctx_stats.items():
            if action is not None and act != action:
                continue
            total_accepted = 0
            total_rejected = 0
            param_rates: dict[str, dict[str, float]] = {}

            for params_key, stat in param_stats.items():
                total_accepted += stat.accepted
                total_rejected += stat.rejected
                # 解析参数键
                params_dict = self._parse_params_key(params_key)
                for pk, pv in params_dict.items():
                    param_rates.setdefault(pk, {})
                    param_rates[pk][str(pv)] = stat.rate

            total = total_accepted + total_rejected
            if total == 0:
                continue
            rate = total_accepted / total

            result["actions"][act] = {
                "rate": round(rate, 4),
                "total": total,
                "accepted": total_accepted,
                "rejected": total_rejected,
                "params": param_rates,
            }

            if total >= min_s and rate > best_rate:
                best_rate = rate
                best_action = act
                # 选每个参数中接受率最高的值
                for pk, pv_dict in param_rates.items():
                    best_pv = max(pv_dict, key=pv_dict.get)
                    best_params[pk] = self._coerce_param_value(best_pv)

        if best_action is not None and best_rate >= self._confidence_threshold:
            result["best_action"] = best_action
            result["best_params"] = best_params
            result["confidence"] = round(best_rate, 4)

        return result

    def suggest(
        self,
        automation_id: str,
        *,
        context: Mapping[str, Any] | None = None,
        min_samples: int | None = None,
    ) -> list[dict[str, Any]]:
        """基于偏好建议调整自动化参数。

        扫描该自动化的历史记录，找出被拒绝率高的参数组合，建议调整。
        返回建议列表，每条包含 reason / current / suggested / confidence。
        """
        min_s = min_samples if min_samples is not None else self._min_samples
        suggestions: list[dict[str, Any]] = []

        # 找该自动化的所有记录
        auto_records = [r for r in self._records if r.automation_id == automation_id]
        if not auto_records:
            return suggestions

        # 按 action 分组统计
        action_stats: dict[str, dict[str, _Stat]] = {}
        for rec in auto_records:
            pk = self._freeze_params(rec.params)
            action_stats.setdefault(rec.action, {})
            stat = action_stats[rec.action].setdefault(pk, _Stat())
            if rec.accepted:
                stat.accepted += 1
            else:
                stat.rejected += 1
            stat.last_seen = rec.timestamp

        for act, param_stats in action_stats.items():
            for params_key, stat in param_stats.items():
                if stat.total < min_s:
                    continue
                if stat.rate >= self._confidence_threshold:
                    continue  # 接受率够高，不需要调整

                params_dict = self._parse_params_key(params_key)
                # 找同 action 下接受率最高的参数组合
                best_key = max(
                    (k for k in param_stats if param_stats[k].total >= min_s),
                    key=lambda k: param_stats[k].rate,
                    default=None,
                )
                suggested_params = self._parse_params_key(best_key) if best_key else {}

                suggestions.append({
                    "automation_id": automation_id,
                    "action": act,
                    "reason": f"当前参数接受率 {stat.rate:.0%}（{stat.accepted}/{stat.total}），低于阈值 {self._confidence_threshold:.0%}",
                    "current_params": params_dict,
                    "suggested_params": suggested_params,
                    "confidence": round(param_stats[best_key].rate, 4) if best_key else 0.0,
                    "samples": stat.total,
                })

        return sorted(suggestions, key=lambda s: s["confidence"], reverse=True)

    # ── 统计信息 ────────────────────────────────────────────────────
    def stats(self) -> dict[str, Any]:
        """返回模型统计概览。"""
        total_records = len(self._records)
        accepted = sum(1 for r in self._records if r.accepted)
        rejected = total_records - accepted
        contexts = len(self._stats)
        actions = sum(len(v) for v in self._stats.values())
        return {
            "total_records": total_records,
            "accepted": accepted,
            "rejected": rejected,
            "accept_rate": round(accepted / total_records, 4) if total_records else 0.0,
            "contexts": contexts,
            "actions": actions,
            "persist_dir": self._persist_dir,
        }

    def records_for(self, automation_id: str | None = None, action: str | None = None) -> list[PreferenceRecord]:
        """返回过滤后的记录列表。"""
        result = self._records
        if automation_id is not None:
            result = [r for r in result if r.automation_id == automation_id]
        if action is not None:
            result = [r for r in result if r.action == action]
        return list(result)

    def clear(self, automation_id: str | None = None) -> int:
        """清除记录（可按自动化过滤）。返回清除条数。"""
        if automation_id is None:
            n = len(self._records)
            self._records.clear()
            self._stats.clear()
        else:
            before = len(self._records)
            self._records = [r for r in self._records if r.automation_id != automation_id]
            n = before - len(self._records)
            self._rebuild_stats()
        self._save()
        return n

    # ── 内部 ────────────────────────────────────────────────────────
    def _normalize_context(self, context: Mapping[str, Any] | None) -> str:
        if not context:
            return context_key()
        hour = context.get("hour")
        weekday = context.get("weekday")
        scene = context.get("scene")
        entity_id = context.get("entity_id")
        return context_key(hour=hour, weekday=weekday, scene=scene, entity_id=entity_id)

    def _freeze_params(self, params: Mapping[str, Any]) -> str:
        """将参数字典转为可哈希的字符串键。只保留可比较的标量值。"""
        items = []
        for k in sorted(params.keys()):
            v = params[k]
            if isinstance(v, (str, int, float, bool)) or v is None:
                items.append(f"{k}={v}")
        return ";".join(items) if items else "_"

    def _parse_params_key(self, key: str) -> dict[str, Any]:
        """从冻结键还原参数字典。"""
        if key == "_" or not key:
            return {}
        result = {}
        for pair in key.split(";"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                result[k] = self._coerce_param_value(v)
        return result

    @staticmethod
    def _coerce_param_value(v: str) -> Any:
        """尝试将字符串参数值转为合适的类型。"""
        if v == "True":
            return True
        if v == "False":
            return False
        if v == "None":
            return None
        try:
            return int(v)
        except (ValueError, TypeError):
            pass
        try:
            return float(v)
        except (ValueError, TypeError):
            pass
        return v

    def _update_stats(
        self,
        ctx: str,
        action: str,
        params: dict[str, Any],
        accepted: bool,
        timestamp: float,
    ) -> None:
        pk = self._freeze_params(params)
        ctx_stat = self._stats.setdefault(ctx, {})
        act_stat = ctx_stat.setdefault(action, {})
        stat = act_stat.setdefault(pk, _Stat())
        if accepted:
            stat.accepted += 1
        else:
            stat.rejected += 1
        stat.last_seen = timestamp

    def _rebuild_stats(self) -> None:
        """从记录重建统计。"""
        self._stats.clear()
        for rec in self._records:
            self._update_stats(rec.context, rec.action, rec.params, rec.accepted, rec.timestamp)

    def _wildcard_match(self, context: Mapping[str, Any] | None) -> dict[str, dict[str, _Stat]]:
        """尝试通配上下文匹配（放宽维度）。"""
        if not context:
            return {}
        # 逐步放宽：先去掉 entity，再去掉 scene，再去掉 weekday
        dims = [
            {"hour": context.get("hour"), "weekday": context.get("weekday"), "scene": context.get("scene")},
            {"hour": context.get("hour"), "weekday": context.get("weekday")},
            {"hour": context.get("hour")},
            {},
        ]
        for dim in dims:
            ctx = self._normalize_context(dim)
            if ctx in self._stats:
                return self._stats[ctx]
        return {}

    def _save(self) -> None:
        if not self._persist_dir:
            return
        path = os.path.join(self._persist_dir, PREFERENCES_FILE)
        os.makedirs(self._persist_dir, exist_ok=True)
        data = {
            "records": [r.to_json() for r in self._records],
            "min_samples": self._min_samples,
            "confidence_threshold": self._confidence_threshold,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _load(self) -> None:
        if not self._persist_dir:
            return
        path = os.path.join(self._persist_dir, PREFERENCES_FILE)
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for r in data.get("records", []):
                rec = PreferenceRecord(**r)
                self._records.append(rec)
                self._update_stats(rec.context, rec.action, rec.params, rec.accepted, rec.timestamp)
            self._min_samples = data.get("min_samples", self._min_samples)
            self._confidence_threshold = data.get("confidence_threshold", self._confidence_threshold)
        except (json.JSONDecodeError, KeyError, TypeError):
            # 损坏的持久化文件不阻塞启动
            self._records.clear()
            self._stats.clear()
