"""af_preference —— 用户偏好模型：学习用户在不同场景下对自动化动作的接受/拒绝。

核心：
- record(action, context, accepted)：记录用户接受/拒绝
- preference(context) -> dict：返回该场景下用户偏好的动作/参数及接受率
- suggest(automation_id) -> list：基于偏好建议调整自动化参数

上下文维度：小时段（morning/afternoon/evening/night）、工作日/周末、场景标签。
零新依赖，持久化为 append-only JSONL（`persist_dir/preferences.jsonl`），
记录数有上限（`max_records`），超限丢弃最旧并同步回补聚合统计。
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from collections import deque
from dataclasses import dataclass, field, asdict, fields as dataclasses_fields
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .af_atomic import atomic_write_text
from .af_store import append_jsonl, read_jsonl_bounded

__all__ = [
    "PreferenceRecord",
    "PreferenceModel",
    "context_key",
    "time_bucket",
]

PREFERENCES_FILE = "preferences.jsonl"
#: 第五轮审计前的整档格式（每次 record 全量重写 → O(N²)）。只读迁移，不覆盖。
LEGACY_PREFERENCES_FILE = "preferences.json"
DEFAULT_MIN_SAMPLES = 3
DEFAULT_CONFIDENCE_THRESHOLD = 0.6
#: 明细留存上限：偏好明细是抽样证据，聚合态 `_stats` 才服务于决策。
DEFAULT_MAX_RECORDS = 5000

_logger = logging.getLogger(__name__)


def _jsonl_has_content(path: Path) -> bool:
    """盘上这份 JSONL 有没有**非空白内容**（用来区分"空库"与"整档读不出来"）。"""
    try:
        return any(line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    except OSError:
        return False


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
        max_records: int = DEFAULT_MAX_RECORDS,
        clock: Callable[[], float] = time.time,
        tz: Any = None,
    ) -> None:
        self._persist_dir = persist_dir
        self._min_samples = min_samples
        self._confidence_threshold = confidence_threshold
        self._max_records = max(1, int(max_records))
        self._clock = clock
        self._tz = tz
        self._records: deque[PreferenceRecord] = deque()
        # stats[context][action][params_frozen] = _Stat
        self._stats: dict[str, dict[str, dict[str, _Stat]]] = {}
        #: 被上限丢弃的明细条数（诚实观测：聚合态可能比明细更宽）
        self._evicted = 0
        #: 距上次整档压缩已追加的行数
        self._since_compact = 0
        #: F13：`preferences.jsonl` 有内容却一行 JSON 对象都读不出来 ⇒ 置位后整档压缩拒写。
        #: 追加式写入不受影响（它只加一行，不重写整档，抹不掉盘上已有的东西）。
        self._records_unreadable = False
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
        self._trim()
        self._append_record(rec)
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
            "max_records": self._max_records,
            "evicted_records": self._evicted,
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
            self._records = deque(r for r in self._records if r.automation_id != automation_id)
            n = before - len(self._records)
            self._rebuild_stats()
        # 全量清空是修复现场的动作，允许越过拒写护栏；按 automation 过滤的部分清空**不**越过
        # ——它同样是整档重写，读不出来时写下去会把别的记录一起抹掉。
        self._rewrite_all(force=automation_id is None)
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

    def _revert_stats(self, rec: PreferenceRecord) -> None:
        """明细被上限淘汰时回补聚合，使 `_stats` 恒等于「留存明细的派生态」。

        键摘除是必要的：否则 `(context, action, params)` 组合本身又是一族"只增不减"。
        `last_seen` 由最新记录写入，淘汰的是最旧记录，故计数未归零时它仍然有效。
        """
        pk = self._freeze_params(rec.params)
        act_stat = self._stats.get(rec.context, {}).get(rec.action)
        if not act_stat:
            return
        stat = act_stat.get(pk)
        if stat is None:
            return
        if rec.accepted:
            stat.accepted = max(0, stat.accepted - 1)
        else:
            stat.rejected = max(0, stat.rejected - 1)
        if stat.total == 0:
            del act_stat[pk]
            ctx_stat = self._stats[rec.context]
            if not act_stat:
                del ctx_stat[rec.action]
                if not ctx_stat:
                    del self._stats[rec.context]

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

    def _trim(self) -> None:
        """明细超上限时丢最旧，并同步回补聚合。"""
        while len(self._records) > self._max_records:
            self._revert_stats(self._records.popleft())
            self._evicted += 1

    def _path(self, name: str) -> str:
        return os.path.join(str(self._persist_dir), name)

    def _append_record(self, rec: PreferenceRecord) -> None:
        """O(1) 落盘：只追加本次新增的那一行，不重写整档。"""
        if not self._persist_dir:
            return
        os.makedirs(self._persist_dir, exist_ok=True)
        append_jsonl(Path(self._path(PREFERENCES_FILE)), rec.to_json())
        self._since_compact += 1
        if self._since_compact >= self._compact_slack():
            self._rewrite_all()

    def _compact_slack(self) -> int:
        """允许文件比内存留存多出这么多行再压缩（把整档重写摊薄到 O(1)/条）。"""
        return max(64, self._max_records // 8)

    def _rewrite_all(self, *, force: bool = False) -> None:
        """整档压缩（原子写）：留存明细有多少写多少。"""
        if not self._persist_dir:
            return
        if self._records_unreadable and not force:
            # 护栏紧邻落盘调用（第十轮 W30）：这份文件本进程一行都没读出来，内存明细是空的，
            # 整档重写等于把盘上明细抹平。拒写（fail-closed），直到文件被修复或隔离；
            # `clear()` 的显式清空是唯一例外（那是修复现场的动作，不是累积写入）。
            return
        os.makedirs(self._persist_dir, exist_ok=True)
        lines = "\n".join(
            json.dumps(r.to_json(), ensure_ascii=False, default=str) for r in self._records
        )
        atomic_write_text(Path(self._path(PREFERENCES_FILE)), (lines + "\n") if lines else "")
        self._since_compact = 0
        self._records_unreadable = False  # 盘上现在就是内存这份快照，现场已一致

    def _migrate_legacy(self) -> list[PreferenceRecord]:
        """旧整档格式（preferences.json）只读迁移：读入后由 `_rewrite_all` 落成 JSONL。

        不删除旧文件、不覆盖旧文件——迁移可逆。
        """
        path = self._path(LEGACY_PREFERENCES_FILE)
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return []
        # 旧档顶层未必是 dict（截断写成 null、被别的工具写成数组、手工编辑等）。
        # try 只护住了读+解析，结构使用在 try 之外 ⇒ 必须显式校验形状（新增审计 BUG-15）。
        if not isinstance(data, dict):
            return []
        recs = [r for r in (self._record_from_row(row) for row in data.get("records", []) if isinstance(row, dict)) if r is not None]
        self._min_samples = data.get("min_samples", self._min_samples)
        self._confidence_threshold = data.get("confidence_threshold", self._confidence_threshold)
        return recs

    def _load(self) -> None:
        if not self._persist_dir:
            return
        path = Path(self._path(PREFERENCES_FILE))
        if path.is_file():
            # 读入量受上限约束：整档只可能比留存多出 _compact_slack 行
            rows = read_jsonl_bounded(path, self._max_records + self._compact_slack())
            if not rows and _jsonl_has_content(path):
                # F13（第九轮实测 data_lost 的那处）：盘上有内容却一行都读不出来。
                # `_records` 这时是空的，任何整档压缩都会把明细抹平且连坏字节的现场一起没。
                self._records_unreadable = True
                _logger.error(
                    "PREFERENCES_UNREADABLE path=%s —— 整档压缩已拒写，坏文件原样保留", path
                )
            else:
                self._records_unreadable = False
            raw = [r for r in (self._record_from_row(row) for row in rows) if r is not None]
        else:
            raw = self._migrate_legacy()
        dropped = max(0, len(raw) - self._max_records)
        self._evicted += dropped
        for rec in raw[dropped:]:
            self._records.append(rec)
            self._update_stats(rec.context, rec.action, rec.params, rec.accepted, rec.timestamp)
        if raw and (dropped or not path.is_file()):
            # 超上限的老档 / 刚迁移完的整档 → 立即压成新格式；空库不建文件
            self._rewrite_all()

    #: 未知键（老版本残留 / 手工编辑）一律忽略，缺关键字段则整行丢弃
    _RECORD_FIELDS = frozenset({f.name for f in dataclasses_fields(PreferenceRecord)})

    @classmethod
    def _record_from_row(cls, row: Mapping[str, Any]) -> PreferenceRecord | None:
        try:
            kwargs = {k: v for k, v in row.items() if k in cls._RECORD_FIELDS}
            rec = PreferenceRecord(**kwargs)
        except (TypeError, ValueError):
            return None
        if not isinstance(rec.params, dict):
            return None
        return rec
