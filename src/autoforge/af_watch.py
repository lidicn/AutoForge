"""af_watch — 可信闭环聚合层（v2.1 F4 初稿）。

订阅 shadow / canary / conflict 的「生产态验证」事件，聚合成诚实报告的
``verified_in_prod`` 分区：回答「这个自动化到底在真实环境里被影子比对 / 金丝雀 /
冲突仲裁验证过多少次、最近一次何时」。

v2.1 初稿只做**内存聚合 + 诚实报告分区**（决策：单进程内存，与 af_audit /
af_premiere 一致）；跨进程 / 持久化是后续。订阅即「生产态模块在产生验证结论时
调用 ``record_*`` 喂入本聚合器」，由调用方保证（shadow 已在 ``compare`` 落点）。

依赖仅标准库，零现有模块耦合（避免与 af_shadow 形成循环 import）。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

__all__ = [
    "WatchEvent",
    "WatchAggregator",
    "record_shadow",
    "record_canary",
    "record_conflict",
    "verified_in_prod_partition",
    "reset",
]


def _as_epoch(value: Any) -> float:
    """把 `at` 收到同一条时间轴（epoch 秒）。

    调用方本来就不一致：af_shadow 传 `clock_now()` 的浮点秒，executor / 冲突仲裁传
    `clock.now()` 的 datetime（AuditEvent.at 就是 datetime）。两种值混在同一栏里做
    `max()` 要么 TypeError，要么把"最近一次"排成乱序——与第三轮 min/max 混类型同族。

    朴素 datetime 按 UTC 解释：Windows 上 `.timestamp()` 走 `mktime`，1970 年之前的
    本地时间直接 `OSError(22, 'Invalid argument')`（本轮实测踩到）。
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc).timestamp()
        return value.timestamp()
    return float(value)

KIND_SHADOW = "shadow"
KIND_CANARY = "canary"
KIND_CONFLICT = "conflict"

STATUS_VERIFIED = "verified"
STATUS_FAILED = "failed"
#: 生产侧判不了的动作（canary 无 SERVICE_STATE 映射 → 无从判漂移）。
#: 单列一档：它既不是"验过了"，也不是"验出问题"，而是"这条根本没有证据"（铁律 #5）。
STATUS_UNMODELED = "unmodeled"


@dataclass
class WatchEvent:
    """一条生产态验证事件。"""

    kind: str
    automation_id: str
    status: str            # verified / failed / conflict / ...
    at: float
    detail: dict[str, Any] = field(default_factory=dict)


class WatchAggregator:
    """生产态验证事件聚合器（内存）。

    N-P2-7 修复：环形缓冲（最多保留 MAX_EVENTS_PER_AUTO 条 / 自动化），避免常驻进程内存单调增长。
    第五轮同族补齐：桶数（automation_id 键空间）也要有上限——每个桶裁到 500 条，
    但被删除/改名的自动化留下的空桶原本永不摘除，仍是只增不减。
    """

    # 每个自动化保留最近 N 条事件——超过丢弃最旧。实测 shadow compare 每次都落点，
    # 长跑不裁剪必然线性膨胀。
    MAX_EVENTS_PER_AUTO = 500
    # 最多跟踪 N 个自动化，超出按"最久没有新事件"摘除。家庭部署的自动化数量远小于此，
    # 上限只拦反复改名/删除重建留下的空桶。
    MAX_AUTOMATIONS = 1000

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_auto: dict[str, list[WatchEvent]] = {}
        self._evicted_automations = 0

    def ingest(self, ev: WatchEvent) -> None:
        ev.at = _as_epoch(ev.at)          # 单一时间轴收口：含调用方直接构造 WatchEvent 的路径
        with self._lock:
            bucket = self._by_auto.pop(ev.automation_id, None) or []
            bucket.append(ev)
            if len(bucket) > self.MAX_EVENTS_PER_AUTO:
                del bucket[: len(bucket) - self.MAX_EVENTS_PER_AUTO]
            # 重新插回末尾：dict 的插入序在此即"最近有新事件"的顺序，摘除只看队首
            self._by_auto[ev.automation_id] = bucket
            while len(self._by_auto) > self.MAX_AUTOMATIONS:
                oldest = next(iter(self._by_auto))
                del self._by_auto[oldest]
                self._evicted_automations += 1

    def record(
        self,
        kind: str,
        automation_id: str,
        status: str,
        at: float,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.ingest(WatchEvent(kind=kind, automation_id=automation_id, status=status, at=at, detail=detail or {}))

    def verified_in_prod(self) -> dict[str, Any]:
        """诚实报告 ``verified_in_prod`` 分区。

        每个自动化给出：生产态验证次数（shadow/canary/conflict 分列）、
        最近一次 verified 时间；顶层 summary 汇总全局。

        **失败与"无从验证"必须同栏出现**：只报 verified 计数会让「影子比对 10 次全 MISSED」
        和「一次都没跑过」长得一模一样——那是 铁律 #5 的假安心。
        """
        with self._lock:
            autos: list[dict[str, Any]] = []
            total_verified = 0
            total_failed = 0
            total_unmodeled = 0
            total_conflict = 0
            for aid, evs in self._by_auto.items():
                verified = [e for e in evs if e.status == STATUS_VERIFIED]
                failed = [e for e in evs if e.status == STATUS_FAILED]
                unmodeled = [e for e in evs if e.status == STATUS_UNMODELED]
                last = max((e.at for e in verified), default=None)
                conflict_count = sum(1 for e in evs if e.kind == KIND_CONFLICT)  # N-P2-6 修复：逐事件计数
                autos.append({
                    "automation_id": aid,
                    "verified_in_prod": len(verified),
                    "last_verified_at": last,
                    "failed_in_prod": len(failed),
                    "last_failed_at": max((e.at for e in failed), default=None),
                    "unmodeled_in_prod": len(unmodeled),
                    "shadow": sum(1 for e in evs if e.kind == KIND_SHADOW),
                    "canary": sum(1 for e in evs if e.kind == KIND_CANARY),
                    "conflict": conflict_count,
                })
                total_verified += len(verified)
                total_failed += len(failed)
                total_unmodeled += len(unmodeled)
                total_conflict += conflict_count  # N-P2-6 修复：累加逐事件冲突数
            autos.sort(key=lambda a: a["automation_id"])
            return {
                "automations": autos,
                "summary": {
                    "total_verified_in_prod": total_verified,
                    "total_failed_in_prod": total_failed,
                    "total_unmodeled_in_prod": total_unmodeled,
                    "total_conflict": total_conflict,
                    # 有失败证据的自动化单独计数：报告里"有 N 个自动化在生产验过"必须能同时说"其中 M 个验出过问题"
                    "automations_with_failed": sum(1 for a in autos if a["failed_in_prod"]),
                    "tracked_automations": len(autos),
                    "evicted_automations": self._evicted_automations,
                },
            }


# ---- 模块级默认实例（生产态模块直接调用下列函数喂入）----

_default = WatchAggregator()


def record_shadow(automation_id: str, status: str, at: float, detail: dict[str, Any] | None = None) -> None:
    _default.record(KIND_SHADOW, automation_id, status, at, detail)


def record_canary(automation_id: str, status: str, at: float, detail: dict[str, Any] | None = None) -> None:
    _default.record(KIND_CANARY, automation_id, status, at, detail)


def record_conflict(automation_id: str, status: str, at: float, detail: dict[str, Any] | None = None) -> None:
    _default.record(KIND_CONFLICT, automation_id, status, at, detail)


def verified_in_prod_partition() -> dict[str, Any]:
    """默认聚合器的 ``verified_in_prod`` 分区（运行时回灌诚实报告用）。"""
    return _default.verified_in_prod()


def reset() -> None:
    """清空默认聚合器（测试隔离用；进程内线程安全）。

    N-P3-1 修复：在锁内 clear() 现有实例，而非无锁替换 _default 引用——
    避免并发 record_* 期间写入旧实例、读新实例导致事件丢失。
    """
    with _default._lock:
        _default._by_auto.clear()
        _default._evicted_automations = 0
