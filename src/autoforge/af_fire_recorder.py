"""FireRecorder —— 当日一次记账落盘，防重启重放。

源自 mimo 旗舰大模型设计（E:\\NAS\\FFL\\mimo_af_output\\13_fire_recorder.py），
增量吸收：文件级 JSON 持久化（不引入 sqlite，与 af_persist 架构一致）。

与 B3-AF-03 的关系：B3-AF-03 修了"先试后记"顺序（内存态），
FireRecorder 在此基础上把记账落盘，重启后不丢失。

recovery 语义：重启时发现 state='claimed'（崩溃在 confirm 之前）的行：
  - 默认 release（当作没触发过，当天还能再试一次）—— 与 attempts 上限配合
  - 设备动作宁可少做一次（漏）也别做两次（重复开关灯更扰民），
    但漏做要能当天补上 —— 所以默认是 release + attempts 上限。
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from .af_atomic import atomic_write_text, refuse_when_shape_unreadable

logger = logging.getLogger(__name__)


@dataclass
class FireRecord:
    rule_key: str
    fire_day: str  # YYYY-MM-DD
    state: str  # "claimed" | "fired"
    attempts: int = 0
    lease_until: str | None = None
    fired_at: str | None = None
    inst_id: str | None = None


class JsonFireStore:
    """文件级 JSON 持久化的 FireStore。

    数据文件：{store_dir}/fire_log.json
    格式：{"records": {rule_key: {fire_day: FireRecord_dict}}}
    """

    def __init__(self, store_dir: str | Path):
        self._path = Path(store_dir) / "fire_log.json"
        self._lock = threading.RLock()
        self._records: dict[str, dict[str, dict[str, Any]]] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                # AF2：冷启动成空是允许的，但**盘上那份读不出来**这件事必须让写侧知道，
                # 否则一次 claim 就把全部当日记账抹平。护栏见 `_save`。
                logger.warning("FIRE_LOG_UNREADABLE fire_log.json 读不出，冷启动为空：%s", exc)
                self._records = {}
                return
            # AF69：json 合法但顶层不是 dict（如 `[1,2,3]`）时，下一行 `data.get` 会抛
            # AttributeError 穿出 __init__ —— 一个坏文件让整个 Runtime 起不来，且报错不点名是
            # 哪份文件。按"读不出"处理：留痕 + 冷启动为空；`_save` 的整档护栏会拒写，避免把这份
            # 错形状文件抹掉（fail-closed，不静默当空库后覆盖）。
            if not isinstance(data, dict):
                logger.warning(
                    "FIRE_LOG_BAD_SHAPE fire_log.json 顶层是 %s 而非 dict，冷启动为空：%s",
                    type(data).__name__, self._path,
                )
                self._records = {}
                return
            records = data.get("records", {})
            self._records = records if isinstance(records, dict) else {}

    def _save(self) -> None:
        # AF2（第二期第二轮确证）：整档由内存快照重写，落盘前先确认盘上那份还读得出来。
        refuse_when_shape_unreadable(self._path, "当日触发记账")
        atomic_write_text(
            self._path,
            json.dumps({"records": self._records}, ensure_ascii=False, indent=2),
        )

    def _get(self, rule_key: str, day: str) -> dict[str, Any] | None:
        return self._records.get(rule_key, {}).get(day)

    def _set(self, rule_key: str, day: str, record: dict[str, Any]) -> None:
        if rule_key not in self._records:
            self._records[rule_key] = {}
        self._records[rule_key][day] = record

    def try_claim(
        self, rule_key: str, day: str, now_iso: str, lease_iso: str, max_attempts: int
    ) -> str:
        with self._lock:
            existing = self._get(rule_key, day)
            if existing is None:
                self._set(rule_key, day, {
                    "rule_key": rule_key, "fire_day": day,
                    "state": "claimed", "attempts": 1,
                    "lease_until": lease_iso, "fired_at": None, "inst_id": None,
                })
                self._save()
                return "claimed"
            if existing["state"] == "fired":
                return "already_fired"
            if existing["attempts"] >= max_attempts:
                return "exhausted"
            lease = existing.get("lease_until")
            if lease is not None and lease >= now_iso:
                return "leased"
            existing["state"] = "claimed"
            existing["lease_until"] = lease_iso
            existing["attempts"] = existing.get("attempts", 0) + 1
            self._save()
            return "claimed"

    def confirm(self, rule_key: str, day: str, fired_iso: str, inst_id: str | None) -> None:
        with self._lock:
            rec = self._get(rule_key, day)
            if rec:
                rec["state"] = "fired"
                rec["fired_at"] = fired_iso
                rec["inst_id"] = inst_id
                rec["lease_until"] = None
                self._save()

    def release(self, rule_key: str, day: str) -> None:
        with self._lock:
            rec = self._get(rule_key, day)
            if rec and rec["state"] == "claimed":
                rec["state"] = "claimed"  # 保持 claimed，attempts 已计数
                rec["lease_until"] = None
                self._save()

    def sweep(self, before_day: str) -> int:
        with self._lock:
            removed = 0
            for rule_key in list(self._records.keys()):
                days = self._records[rule_key]
                for day in list(days.keys()):
                    if day < before_day:
                        del days[day]
                        removed += 1
                if not days:
                    del self._records[rule_key]
            if removed:
                self._save()
            return removed

    def count(self, day: str | None = None) -> int:
        with self._lock:
            if day is None:
                return sum(len(d) for d in self._records.values())
            return sum(1 for d in self._records.values() if day in d)

    def claimed_keys(self, day: str) -> list[str]:
        with self._lock:
            return [
                rule_key for rule_key, days in self._records.items()
                if days.get(day, {}).get("state") == "claimed"
            ]


def _local_day(clock: Any) -> date:
    """本地日历日（审计 2026-10-28：落盘防重放的"当日"桶要用哪把尺子）。

    旧实现用 `clock.now().date()`，而 `SystemTimeSource.now()` 返回 **UTC**
    （`af_time.py:284`）；调度器判"今天是否已触发"用的是 `local_now().date()`
    （`af_scheduler.py:441`）⇒ UTC+8 部署下本地 00:00–08:00 落进**前一个 UTC 日**桶，
    该窗口内重启时 `recover()` 认不到当天既有记录，已执行过的触发可能被重放。
    两把尺子必须同一口径，这里向调度器对齐。

    取法容错三档（与 `af_scheduler.py:441` 同姿势）：`today()` >
    `local_now().date()` > `now().date()`——测试注入的轻量假时钟常只实现 `now`。
    返回 `date` 而不是字符串：调用点有 `- timedelta(keep_days)` 的日期算术，
    字符串会把那一步变成运行时崩（我第一版就踩在这）。
    """
    for getter in ("today", "local_now"):
        fn = getattr(clock, getter, None)
        if not callable(fn):
            continue
        val = fn()
        if isinstance(val, datetime):
            return val.date()
        if isinstance(val, date):
            return val
        if isinstance(val, str):  # 假时钟直接给 ISO 串
            return date.fromisoformat(val)
    return clock.now().date()


class FireLease:
    """一次「当天一次」记账的租约。with 块异常退出自动 release。"""

    def __init__(self, store: JsonFireStore, rule_key: str, day: str, clock):
        self._store = store
        self.rule_key = rule_key
        self.day = day
        self._clock = clock
        self._done = False
        self.confirmed = False

    def confirm(self, inst_id: str | None = None) -> None:
        if self._done:
            return
        self._store.confirm(self.rule_key, self.day, self._clock.now().isoformat(), inst_id)
        self._done = self.confirmed = True

    def release(self) -> None:
        if self._done:
            return
        self._store.release(self.rule_key, self.day)
        self._done = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if not self._done:
            if exc_type is None:
                self.confirm(None)
            else:
                self.release()
        return False


class FireRecorder:
    """当日一次记账。跨日清理 + 重启不重放 + 失败可重试（有界）。"""

    def __init__(
        self,
        store: JsonFireStore,
        clock,
        *,
        max_attempts_per_day: int = 3,
        lease_s: float = 30.0,
        keep_days: int = 3,
    ):
        self._store = store
        self._clock = clock
        self._max_attempts = max_attempts_per_day
        self._lease_s = lease_s
        self._keep_days = keep_days
        self._last_sweep_mono = 0.0

    def try_begin(self, rule_key: str) -> FireLease | None:
        day = _local_day(self._clock).isoformat()
        now = self._clock.now()
        res = self._store.try_claim(
            rule_key, day, now.isoformat(),
            (now + timedelta(seconds=self._lease_s)).isoformat(),
            self._max_attempts,
        )
        return FireLease(self._store, rule_key, day, self._clock) if res == "claimed" else None

    def fire_exactly_once(
        self, rule_key: str, do: Callable[[], str | None],
        *, on_skip: Callable[[str], None] | None = None,
    ) -> bool:
        lease = self.try_begin(rule_key)
        if lease is None:
            if on_skip:
                on_skip(rule_key)
            return False
        with lease:
            inst_id = do()
        if not lease.confirmed:
            lease.confirm(inst_id)
        return True

    def fired_today(self, rule_key: str) -> bool:
        return self.try_begin(rule_key) is None

    def sweep(self, *, force: bool = False) -> int:
        now_m = self._clock.monotonic()
        if not force and now_m - self._last_sweep_mono < 3600:
            return 0
        self._last_sweep_mono = now_m
        before = (_local_day(self._clock) - timedelta(days=self._keep_days)).isoformat()
        return self._store.sweep(before)

    def recover(self, *, stale_as_fired: bool = False) -> int:
        if not stale_as_fired:
            return 0
        day = _local_day(self._clock).isoformat()
        n = 0
        for key in self._store.claimed_keys(day):
            self._store.confirm(key, day, self._clock.now().isoformat(), None)
            n += 1
        return n
