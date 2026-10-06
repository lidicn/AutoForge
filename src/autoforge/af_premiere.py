"""af_premiere — 首演码仪式试演期（AutoForge v2 M1）。

部署前一次性 6 位首演码绑定当前 store diff 的 SHA256（防验码后掉包）+ 5min 过期
+ 原子消费防重放；部署落地后自动进入试演期（默认 24h）只统计不封禁，
assert 失败 / 抖动自动暂停并推送（pusher 为可注入回调，不执行真实封禁）。

依赖仅标准库（secrets / hashlib / json / threading / time），零现有模块耦合。
审计事件由集成层（af_apply）发射，本模块保持纯净、可独立单测。
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from .af_atomic import atomic_write_text

__all__ = [
    "PremiereCode",
    "ConsumeResult",
    "PremiereStore",
    "Trial",
    "TrialStore",
    "issue",
    "consume",
    "enter_trial",
    "report_assert_failure",
    "pause_and_notify",
    "is_paused",
    "is_paused_for_automation",
    "resume_trial",
    "issue_premiere_for",
    "set_persistence",
]

TimeSource = Callable[[], float]
Pusher = Callable[[dict], None]

_CODE_SPACE = 10 ** 6  # 6 位十进制码空间


def _default_now() -> float:
    return time.time()


class PremiereCode:
    """一条首演码记录。"""

    __slots__ = ("code", "store_diff_sha256", "issued_at", "expires_at", "consumed_at")

    def __init__(
        self,
        code: str,
        store_diff_sha256: str,
        issued_at: float,
        expires_at: float,
        consumed_at: Optional[float] = None,
    ) -> None:
        self.code = code
        self.store_diff_sha256 = store_diff_sha256
        self.issued_at = issued_at
        self.expires_at = expires_at
        self.consumed_at = consumed_at

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "store_diff_sha256": self.store_diff_sha256,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "consumed_at": self.consumed_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PremiereCode":
        return cls(
            code=d["code"],
            store_diff_sha256=d["store_diff_sha256"],
            issued_at=d["issued_at"],
            expires_at=d["expires_at"],
            consumed_at=d.get("consumed_at"),
        )


class ConsumeResult(dict):
    """consume 的返回：dict 子类，``__bool__ == ok`` 以便同时兼容
    ``assert result`` 与 ``result["ok"]`` 两种断言风格（验收台不猜其偏好）。"""

    def __init__(
        self,
        ok: bool,
        reason: str = "",
        *,
        code: str = "",
        store_diff_sha256: str = "",
        consumed_at: Optional[float] = None,
    ) -> None:
        super().__init__(
            ok=ok,
            reason=reason,
            code=code,
            store_diff_sha256=store_diff_sha256,
            consumed_at=consumed_at,
        )

    def __bool__(self) -> bool:  # type: ignore[override]
        return bool(self["ok"])


class PremiereStore:
    """首演码仓库：内存字典 + 锁（与 af_audit 内存-only 一致，持久化后可换）。

    时间源 ``now`` 可注入，支持时间旅行测试。
    """

    def __init__(self, now: TimeSource = _default_now, ttl_s: int = 300, path: Optional[str] = None) -> None:
        self._now = now
        self._ttl_s = ttl_s
        self._lock = threading.Lock()
        self._by_code: dict[str, PremiereCode] = {}
        self._path = path
        if path and os.path.exists(path):
            self.load()

    def issue(self, store_diff_sha256: str, ttl_s: Optional[int] = None) -> str:
        ttl = self._ttl_s if ttl_s is None else ttl_s
        with self._lock:
            code = self._gen_unique_code_locked()
            rec = PremiereCode(
                code=code,
                store_diff_sha256=store_diff_sha256,
                issued_at=self._now(),
                expires_at=self._now() + ttl,
            )
            self._by_code[code] = rec
            self.save()
            return code

    def _gen_unique_code_locked(self) -> str:
        for _ in range(32):
            code = f"{secrets.randbelow(_CODE_SPACE):06d}"
            if code not in self._by_code:
                return code
        # 码空间被占满（极端），兜底线性探测
        i = 0
        while f"{i:06d}" in self._by_code:
            i += 1
        return f"{i:06d}"

    def consume(self, code: str, store_diff_sha256: str) -> ConsumeResult:
        with self._lock:
            rec = self._by_code.get(code)
            if rec is None:
                return ConsumeResult(
                    False, "unknown_code", code=code, store_diff_sha256=store_diff_sha256
                )
            if rec.consumed_at is not None:
                return ConsumeResult(
                    False, "already_consumed", code=code, store_diff_sha256=store_diff_sha256
                )
            if self._now() > rec.expires_at:
                return ConsumeResult(
                    False, "expired", code=code, store_diff_sha256=store_diff_sha256
                )
            if rec.store_diff_sha256 != store_diff_sha256:
                # 防验码后掉包：码绑定的 diff 哈希与本次不一致
                return ConsumeResult(
                    False, "sha_mismatch", code=code, store_diff_sha256=store_diff_sha256
                )
            rec.consumed_at = self._now()
            self.save()
            return ConsumeResult(
                True,
                "ok",
                code=code,
                store_diff_sha256=store_diff_sha256,
                consumed_at=rec.consumed_at,
            )

    def peek(self, code: str) -> Optional[PremiereCode]:
        return self._by_code.get(code)

    # ---- JSON 落盘（走 `af_atomic.atomic_write_text`：随机 tmp + fsync）----

    def save(self, path: Optional[str] = None) -> None:
        """写透当前首演码仓库（原子替换，崩溃不留半截文件）。"""
        path = path or self._path
        if not path:
            return
        payload = {
            "version": 1,
            "ttl_s": self._ttl_s,
            "codes": [c.to_dict() for c in self._by_code.values()],
        }
        atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))

    def load(self, path: Optional[str] = None) -> int:
        """从磁盘恢复首演码仓库（启动期调用）。返回加载条数。"""
        path = path or self._path
        if not path or not os.path.exists(path):
            return 0
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        # 顶层未必是 dict（截断写成 null、被别的工具写成数组、手工编辑）。启动期调用，
        # 抛出去就是整个服务起不来 —— 形状不符当"没有这份文件"（新增审计 BUG-17）。
        if not isinstance(data, dict):
            return 0
        n = 0
        for c in data.get("codes", []):
            rec = PremiereCode.from_dict(c)
            self._by_code[rec.code] = rec
            n += 1
        return n


class Trial:
    """试演期状态。

    决策 F：失败即暂停（分级）。
    - low 风险 band：首次 assert 失败仅 notify；24h 内二次失败才 pause。
    - high 风险 band：首次 assert 失败立即 pause + 通知。
    - 恢复必须人工（resume()），不许自动恢复。
    """

    __slots__ = ("store_diff_sha256", "started_at", "ends_at", "paused",
                 "assert_failures", "failure_times", "risk_level", "automation_ids")

    def __init__(self, store_diff_sha256: str, started_at: float, ends_at: float,
                 risk_level: str = "low", automation_ids: list[str] | None = None) -> None:
        self.store_diff_sha256 = store_diff_sha256
        self.started_at = started_at
        self.ends_at = ends_at
        self.paused = False
        self.assert_failures = 0
        self.failure_times: list[float] = []
        self.risk_level = risk_level  # "low" | "high"
        self.automation_ids = list(automation_ids or [])

    def to_dict(self) -> dict:
        return {
            "store_diff_sha256": self.store_diff_sha256,
            "started_at": self.started_at,
            "ends_at": self.ends_at,
            "paused": self.paused,
            "assert_failures": self.assert_failures,
            "failure_times": list(self.failure_times),
            "risk_level": self.risk_level,
            "automation_ids": list(self.automation_ids),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Trial":
        t = cls(d["store_diff_sha256"], d["started_at"], d["ends_at"])
        t.paused = d.get("paused", False)
        t.assert_failures = d.get("assert_failures", 0)
        t.failure_times = list(d.get("failure_times") or [])
        t.risk_level = d.get("risk_level", "low")
        t.automation_ids = list(d.get("automation_ids") or [])
        return t


class TrialStore:
    """试演期仓库：内存字典 + 锁 + JSON 原子落盘。

    决策 F：失败即暂停（分级）。
    - low 风险 band：首次 assert 失败仅 notify；24h 内二次失败才 pause。
    - high 风险 band：首次 assert 失败立即 pause + notify。
    - 恢复必须人工（resume()），不许自动恢复。
    - paused 状态会被 af_apply 执行闸拦截（apply 同一个 store 或 automation_id 时拒绝）。
    """

    # low 风险 band 24h 内二次失败才 pause
    LOW_RISK_SECOND_FAILURE_WINDOW_S = 24 * 3600

    def __init__(self, now: TimeSource = _default_now, path: Optional[str] = None) -> None:
        self._now = now
        self._lock = threading.Lock()
        self._trials: dict[str, Trial] = {}
        self._last_sha: Optional[str] = None
        self._path = path
        if path and os.path.exists(path):
            self.load()

    def enter_trial(self, store_diff_sha256: str, hours: int = 24,
                    risk_level: str = "low", automation_ids: list[str] | None = None) -> dict:
        with self._lock:
            t = Trial(
                store_diff_sha256=store_diff_sha256,
                started_at=self._now(),
                ends_at=self._now() + hours * 3600,
                risk_level=risk_level,
                automation_ids=automation_ids,
            )
            self._trials[store_diff_sha256] = t
            self._last_sha = store_diff_sha256
            self.save()
            return {
                "ok": True,
                "store_diff_sha256": store_diff_sha256,
                "started_at": t.started_at,
                "ends_at": t.ends_at,
                "risk_level": risk_level,
            }

    def get_trial(self, store_diff_sha256: str) -> Optional[Trial]:
        return self._trials.get(store_diff_sha256)

    def is_in_trial(self, store_diff_sha256: str) -> bool:
        with self._lock:
            t = self._trials.get(store_diff_sha256)
            if t is None:
                return False
            return self._now() < t.ends_at

    def is_paused(self, store_diff_sha256: str) -> bool:
        """指定 store 的试演期是否已被暂停。"""
        with self._lock:
            t = self._trials.get(store_diff_sha256)
            if t is None:
                return False
            return t.paused and self._now() < t.ends_at

    def is_paused_for_automation(self, automation_id: str) -> bool:
        """指定 automation 是否在任何 paused trial 里。"""
        with self._lock:
            for t in self._trials.values():
                if t.paused and self._now() < t.ends_at and automation_id in t.automation_ids:
                    return True
            return False

    def resume(self, store_diff_sha256: str) -> dict:
        """人工恢复：清除 paused 标志（不可逆操作，必须显式调用）。"""
        with self._lock:
            t = self._trials.get(store_diff_sha256)
            if t is None:
                return {"ok": False, "reason": "not_in_trial"}
            if not t.paused:
                return {"ok": False, "reason": "not_paused"}
            t.paused = False
            self.save()
            resumed = self._trials.get(store_diff_sha256) is t and not t.paused
            return {"ok": resumed, "store_diff_sha256": store_diff_sha256, "resumed": resumed}

    def report_assert_failure(
        self,
        store_diff_sha256: str,
        pusher: Optional[Pusher] = None,
        *,
        reason: str = "assert_failed",
    ) -> dict:
        """assert 失败处理（决策 F 分级暂停）。

        high 风险 band → 立即 pause + notify
        low 风险 band  → 首次仅 notify；24h 内二次失败才 pause
          - 24h 窗口：清理 > 24h 前的旧失败时间戳
          - 窗口内失败数 >= 2 → pause
        """
        with self._lock:
            t = self._trials.get(store_diff_sha256)
            if t is None:
                return {"ok": False, "reason": "not_in_trial"}
            if t.paused:
                return {"ok": False, "reason": "already_paused"}

            now = self._now()
            t.assert_failures += 1
            t.failure_times.append(now)
            # 清理 24h 前的旧失败时间戳（窗口滚动）
            cutoff = now - self.LOW_RISK_SECOND_FAILURE_WINDOW_S
            t.failure_times = [ft for ft in t.failure_times if ft >= cutoff]
            failures_in_window = len(t.failure_times)

            should_pause = False
            if t.risk_level == "high":
                # high 风险 band：首次失败即 pause
                should_pause = True
            elif t.risk_level == "low":
                # low 风险 band：24h 窗口内失败 >= 2 才 pause
                should_pause = failures_in_window >= 2

            if should_pause:
                result = self._pause_locked(t, pusher, reason=reason)
            else:
                # 首次失败 low risk：仅 notify，不 pause
                payload = {"store_diff_sha256": store_diff_sha256,
                           "reason": reason, "assert_failures": t.assert_failures,
                           "failures_in_window": failures_in_window,
                           "paused": False, "action": "notified_only",
                           "risk_level": t.risk_level}
                notified, notify_error = self._deliver(pusher, payload)
                # ok = 失败确实被计入窗口且试演期确实没被暂停（分级判定的两条真实后置条件），
                # 推送是否送达另有 notified/notify_error，不混进 ok。
                result = {"ok": (not t.paused) and failures_in_window >= 1,
                          **payload,
                          "notified": notified,
                          "notify_error": notify_error}

            self.save()
            return result

    def pause_and_notify(
        self,
        pusher: Optional[Pusher] = None,
        store_diff_sha256: Optional[str] = None,
        *,
        reason: str = "trial_paused",
    ) -> dict:
        with self._lock:
            sha = store_diff_sha256 or self._last_sha
            if sha is None:
                return {"ok": False, "reason": "no_trial"}
            t = self._trials.get(sha)
            if t is None:
                return {"ok": False, "reason": "not_in_trial"}
            result = self._pause_locked(t, pusher, reason=reason)
            self.save()
            return result

    @staticmethod
    def _deliver(pusher: Optional[Pusher], payload: dict) -> tuple[bool, Optional[str]]:
        """推送并如实回报是否送达。异常不外抛，但转成 (False, 原因)——绝不咽了还算成功。"""
        if pusher is None:
            return False, "no_pusher"
        try:
            pusher(payload)
        except Exception as exc:  # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}"
        return True, None

    def _pause_locked(self, t: Trial, pusher: Optional[Pusher], *, reason: str) -> dict:
        """设置 paused=True（调用方须持锁 + 调用 save）。"""
        t.paused = True
        payload = {
            "store_diff_sha256": t.store_diff_sha256,
            "reason": reason,
            "paused": True,
            "assert_failures": t.assert_failures,
            "risk_level": t.risk_level,
        }
        notified, notify_error = self._deliver(pusher, payload)
        applied = self._trials.get(t.store_diff_sha256) is t and t.paused
        return {"ok": applied, **payload, "notified": notified, "notify_error": notify_error}

    # ---- JSON 落盘（原子替换）----

    def save(self, path: Optional[str] = None) -> None:
        """写透当前试演期仓库（原子替换，崩溃不留半截文件）。"""
        path = path or self._path
        if not path:
            return
        payload = {
            "version": 1,
            "last_sha": self._last_sha,
            "trials": [t.to_dict() for t in self._trials.values()],
        }
        atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))

    def load(self, path: Optional[str] = None) -> int:
        """从磁盘恢复试演期仓库（启动期调用）。返回加载条数。"""
        path = path or self._path
        if not path or not os.path.exists(path):
            return 0
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):  # 同上：形状不符当"没有这份文件"（新增审计 BUG-17）
            return 0
        self._last_sha = data.get("last_sha")
        n = 0
        for t in data.get("trials", []):
            rec = Trial.from_dict(t)
            self._trials[rec.store_diff_sha256] = rec
            n += 1
        return n


# ---- 模块级默认实例（af_apply 直接调用下列函数）----

_default_premiere = PremiereStore()
_default_trial = TrialStore()


def issue(store_diff_sha256: str, ttl_s: int = 300) -> str:
    return _default_premiere.issue(store_diff_sha256, ttl_s=ttl_s)


def consume(code: str, store_diff_sha256: str) -> ConsumeResult:
    return _default_premiere.consume(code, store_diff_sha256)


def enter_trial(store_diff_sha256: str, hours: int = 24,
                risk_level: str = "low", automation_ids: list[str] | None = None) -> dict:
    return _default_trial.enter_trial(store_diff_sha256, hours=hours,
                                      risk_level=risk_level, automation_ids=automation_ids)


def report_assert_failure(
    store_diff_sha256: str,
    pusher: Optional[Pusher] = None,
    *,
    reason: str = "assert_failed",
) -> dict:
    return _default_trial.report_assert_failure(store_diff_sha256, pusher, reason=reason)


def pause_and_notify(
    pusher: Optional[Pusher] = None,
    store_diff_sha256: Optional[str] = None,
    *,
    reason: str = "trial_paused",
) -> dict:
    return _default_trial.pause_and_notify(pusher, store_diff_sha256, reason=reason)


def is_paused(store_diff_sha256: str) -> bool:
    """指定 store 的试演期是否已被暂停（F6 执行闸查询接口）。

    无 trial 记录 → False（没有暂停的 trial = 不拦截）。
    """
    return _default_trial.is_paused(store_diff_sha256)


def is_paused_for_automation(automation_id: str) -> bool:
    """指定 automation 是否在任何 paused trial 里。"""
    return _default_trial.is_paused_for_automation(automation_id)


def resume_trial(store_diff_sha256: str) -> dict:
    """人工恢复被暂停的试演期（决策 F：恢复必须人工，不许自动）。"""
    return _default_trial.resume(store_diff_sha256)


def issue_premiere_for(store_diff_sha256: str, ttl_s: int = 300) -> str:
    """便捷别名：运维取码用（af_apply.issue_premiere 内部调用）。"""
    return issue(store_diff_sha256, ttl_s=ttl_s)


def set_persistence(premiere_path: Optional[str] = None, trial_path: Optional[str] = None) -> None:
    """启动期配置默认仓库落盘路径并加载既有数据（决策 F 前置：premiere 落盘）。

    仅当传入路径时启用持久化；默认内存实例保持向后兼容。
    """
    global _default_premiere, _default_trial
    if premiere_path is not None:
        _default_premiere._path = premiere_path
        if os.path.exists(premiere_path):
            _default_premiere.load()
    if trial_path is not None:
        _default_trial._path = trial_path
        if os.path.exists(trial_path):
            _default_trial.load()
