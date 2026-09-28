"""AutoForge · 冲突仲裁器集成层（装配层，不修改任何既有模块）。

职责：
* 用 ConflictSettings.from_env() 从环境变量读配置（默认关闭）。
* 包装 executor._do：adapter.call 之前 arbiter.request()，之后 note_af_action + release。
* 转发 InterventionDetector 的 user_override / manual_override → arbiter.on_user_override()。
* WAIT 挂起 + Scheduler.call_later(0, retry) 续跑；被抢占的在飞实例走 on_error 边。
* 提供 WebUI 用的 5 个端点装配函数（FastAPI 兼容，零新依赖）。

F8 band 归一（决策 A/C 协同）：冲突仲裁的"放行/被动/须确认"判定统一走 `af_conf` 的
`BAND_PRIORITY / PASSIVE_BANDS / CONFIRM_REQUIRED_BANDS` 单一真值源，与 G4 的
auto/shadow/ask 三级自主同口径——`shadow` 只读比对不参与冲突，`ask` 只出提案、
禁止自动下发（防御纵深，G2 编译期已拦截写设备，此处兜底）。

时序：
    request() -> ALLOW  -> adapter.call -> note_af_action -> release()
    request() -> REJECT / CIRCUIT_OPEN -> _soft_fail(...)（on_error / default 边）
    request() -> WAIT   -> 挂起，锁释放后 call_later(0, retry)
"""

from __future__ import annotations

import functools
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Iterable, Mapping, Sequence

try:  # 包内加载 / 单文件加载两种方式都可用
    from .af_conflict import ConflictArbiter, RequestDecision, SystemTimeSource
    from .af_conflict_audit import ConflictAuditor, event_to_dict
    from .af_conf import PASSIVE_BANDS, CONFIRM_REQUIRED_BANDS
except ImportError:  # pragma: no cover
    from af_conflict import ConflictArbiter, RequestDecision, SystemTimeSource
    from af_conflict_audit import ConflictAuditor, event_to_dict
    from af_conf import PASSIVE_BANDS, CONFIRM_REQUIRED_BANDS

if TYPE_CHECKING:  # 仅类型标注，运行时零依赖
    from .af_conf import ConfidenceStore
    from .af_intervention import InterventionDetector
    from .af_time import TimeSource

ENV_FLAG = "AUTOFORGE_CONFLICT_ARBITER"

_ENV_FLOATS = {
    "lock_ttl": "AUTOFORGE_CONFLICT_LOCK_TTL",
    "cooldown_after_user": "AUTOFORGE_CONFLICT_COOLDOWN",
    "flicker_window": "AUTOFORGE_CONFLICT_FLICKER_WINDOW",
    "flicker_pause": "AUTOFORGE_CONFLICT_FLICKER_PAUSE",
    "circuit_cooldown": "AUTOFORGE_CONFLICT_CIRCUIT_COOLDOWN",
    "aging_rate": "AUTOFORGE_CONFLICT_AGING_RATE",
    "aging_cap": "AUTOFORGE_CONFLICT_AGING_CAP",
    "max_pending_wait": "AUTOFORGE_CONFLICT_MAX_PENDING_WAIT",
}
_ENV_INTS = {
    "flicker_threshold": "AUTOFORGE_CONFLICT_FLICKER_THRESHOLD",
    "circuit_threshold": "AUTOFORGE_CONFLICT_CIRCUIT_THRESHOLD",
    "circuit_half_open_success": "AUTOFORGE_CONFLICT_CIRCUIT_HALF_OPEN_SUCCESS",
}
_ENV_BOOLS = {
    "preempt_enabled": "AUTOFORGE_CONFLICT_PREEMPT",
    "wait_enabled": "AUTOFORGE_CONFLICT_WAIT",
}
_ENV_DIR = "AUTOFORGE_CONFLICT_AUDIT_DIR"

_TRUTHY = {"1", "true", "on", "yes", "enforce"}
_OBSERVE = {"observe", "audit", "log", "watch"}


class ConflictBlocked(Exception):
    """do 节点被仲裁器拦截（用于 _soft_fail 走 on_error/default 边）。"""

    def __init__(self, decision: RequestDecision, reason: str = "") -> None:
        self.decision = decision
        self.reason = reason
        super().__init__(f"conflict arbiter blocked: {decision.value} {reason}".strip())


@dataclass
class ConflictSettings:
    """仲裁器配置（全部可由环境变量覆盖）。"""

    mode: str = "off"                    # off / observe / enforce
    lock_ttl: float = 10.0
    preempt_enabled: bool = True
    wait_enabled: bool = False
    cooldown_after_user: float = 30.0
    flicker_threshold: int = 4
    flicker_window: float = 10.0
    flicker_pause: float = 60.0
    circuit_threshold: int = 5
    circuit_cooldown: float = 300.0
    circuit_half_open_success: int = 2
    aging_rate: float = 0.01
    aging_cap: float = 0.20
    max_pending_wait: float = 60.0
    persist_dir: str | None = ".forge"
    on_error_edges: tuple[str, ...] = ("on_error",)
    wait_edges: tuple[str, ...] = ()

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "ConflictSettings":
        src = os.environ if env is None else env
        raw = str(src.get(ENV_FLAG, "")).strip().lower()
        if raw in _TRUTHY:
            mode = "enforce"
        elif raw in _OBSERVE:
            mode = "observe"
        else:
            mode = "off"
        settings = cls(mode=mode)
        for attr, key in _ENV_FLOATS.items():
            if key in src:
                try:
                    setattr(settings, attr, float(src[key]))
                except (TypeError, ValueError):
                    pass
        for attr, key in _ENV_INTS.items():
            if key in src:
                try:
                    setattr(settings, attr, int(float(src[key])))
                except (TypeError, ValueError):
                    pass
        for attr, key in _ENV_BOOLS.items():
            if key in src:
                setattr(settings, attr, str(src[key]).strip().lower() in _TRUTHY)
        if _ENV_DIR in src:
            settings.persist_dir = src[_ENV_DIR] or None
        return settings


# --------------------------------------------------------------------------- #
# 参数解析工具
# --------------------------------------------------------------------------- #
def extract_entity_ids(params: Mapping[str, Any] | None) -> list[str]:
    """从 node.params 提取 entity_id（str / list[str] / target 嵌套均可）。"""
    out: list[str] = []

    def _walk(container: Any) -> None:
        if not isinstance(container, Mapping):
            return
        for key in ("entity_id", "entity_ids", "entityId", "target", "targets"):
            val = container.get(key)
            if val is None:
                continue
            if isinstance(val, str):
                out.append(val)
            elif isinstance(val, Mapping):
                _walk(val)
            elif isinstance(val, (list, tuple, set)):
                for item in val:
                    if isinstance(item, str):
                        out.append(item)
                    elif isinstance(item, Mapping):
                        _walk(item)

    _walk(params or {})
    seen: list[str] = []
    for eid in out:
        if eid and eid not in seen:
            seen.append(eid)
    return sorted(seen)


def expected_state_for(action: str, params: Mapping[str, Any] | None) -> str | None:
    """推断 AF 动作的期望状态（供 InterventionDetector.note_af_action 使用）。"""
    name = (action or "").lower()
    if name.endswith("turn_on") or name.endswith("_on") or name == "on":
        return "on"
    if name.endswith("turn_off") or name.endswith("_off") or name == "off":
        return "off"
    if name.endswith("toggle"):
        return "toggle"
    for key in ("state", "value", "mode"):
        if params and key in params and params[key] is not None:
            return str(params[key])
    return None


def _automation_id(instance: Any) -> str:
    automation = getattr(instance, "automation", None)
    return str(getattr(automation, "id", "") or "")


def _instance_id(instance: Any) -> str:
    return str(getattr(instance, "instance_id", "") or "")


# --------------------------------------------------------------------------- #
# 集成层
# --------------------------------------------------------------------------- #
class ConflictService:
    """把 ConflictArbiter 装进现有 Runtime 的装配层服务对象。"""

    def __init__(
        self,
        conf: "ConfidenceStore",
        clock: "TimeSource" | None = None,
        settings: ConflictSettings | None = None,
        *,
        scheduler: Any = None,
        intervention: "InterventionDetector" | None = None,
        on_resume: Callable[[Any, Any, Any], None] | None = None,
        auditor: ConflictAuditor | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.settings = settings or ConflictSettings.from_env(env)
        self.conf = conf
        self.clock = clock or SystemTimeSource()
        self.scheduler = scheduler
        self.intervention = intervention
        self.on_resume = on_resume
        # 事件不可跳过：审计器始终存在（observe 模式只观测不拦截）
        self.auditor = auditor or ConflictAuditor(persist_dir=self.settings.persist_dir)
        self.arbiter = ConflictArbiter(
            conf,
            self.clock,
            lock_ttl=self.settings.lock_ttl,
            preempt_enabled=self.settings.preempt_enabled,
            cooldown_after_user=self.settings.cooldown_after_user,
            flicker_threshold=self.settings.flicker_threshold,
            flicker_window=self.settings.flicker_window,
            flicker_pause=self.settings.flicker_pause,
            circuit_threshold=self.settings.circuit_threshold,
            circuit_cooldown=self.settings.circuit_cooldown,
            circuit_half_open_success=self.settings.circuit_half_open_success,
            aging_rate=self.settings.aging_rate,
            aging_cap=self.settings.aging_cap,
            wait_enabled=self.settings.wait_enabled,
            max_pending_wait=self.settings.max_pending_wait,
            on_event=self.auditor.record,
            on_preempted=self._on_preempted,
            on_pending_ready=self._on_pending_ready,
        )
        self.auditor.lock_provider = lambda: self.arbiter.locks().values()
        self._aborted: set[tuple[str, str]] = set()
        self._waiters: dict[tuple[str, str], tuple[Any, Any, Any, Any]] = {}
        self._uninstallers: list[Callable[[], None]] = []

    # --------------------------- 生命周期 --------------------------- #
    @property
    def enabled(self) -> bool:
        return self.settings.mode != "off"

    def attach(self, executor: Any) -> Callable[[], None]:
        """包装 executor._do（装配层注入，不修改 af_executor.py）。返回卸载器。"""
        original = getattr(executor, "_do")

        def _do(instance: Any, node: Any) -> Any:
            return self.dispatch(executor, original, instance, node)

        executor._do = _do

        def _uninstall() -> None:
            try:
                if getattr(executor, "_do", None) is _do:
                    executor._do = original
            except Exception:
                pass

        self._uninstallers.append(_uninstall)
        return _uninstall

    def detach(self) -> None:
        for fn in list(self._uninstallers):
            try:
                fn()
            except Exception:
                pass
        self._uninstallers.clear()

    # --------------------------- do 节点拦截 --------------------------- #
    def dispatch(self, executor: Any, original: Callable[..., Any], instance: Any, node: Any) -> Any:
        if self.settings.mode == "off":
            return original(instance, node)
        try:
            automation_id = _automation_id(instance)
            instance_id = _instance_id(instance)
            params = dict(getattr(node, "params", None) or {})
            action = str(getattr(node, "action", "") or "")
            entity_ids = extract_entity_ids(params)
        except Exception as exc:
            self._audit_degraded("introspect", exc, "", "")
            return original(instance, node)

        if not entity_ids or not automation_id:
            return original(instance, node)          # 只读/无实体节点不参与锁
        band = self._safe_band(automation_id)
        if band in PASSIVE_BANDS:                      # shadow：只读比对，不执行真实动作（F8 ① 单一真值源）
            return original(instance, node)
        if band in CONFIRM_REQUIRED_BANDS and self.settings.mode != "observe":
            # ask：只出提案须人工确认，禁止自动下发（F8 ③ 与 G4 auto/shadow/ask 联动 + 防御纵深）
            return self._abort(executor, instance, node, RequestDecision.REJECT, "ask_band_requires_confirmation")
        if self._adapter_is_dry(executor, node):
            return original(instance, node)          # dry_run 不参与冲突

        observe = self.settings.mode == "observe"
        try:
            decision = self.arbiter.request(
                entity_ids, automation_id, instance_id, action, params, observe=observe
            )
        except Exception as exc:                     # 故障优先：降级 ALLOW
            self._audit_degraded("request", exc, entity_ids[0], automation_id)
            decision = RequestDecision.ALLOW

        if decision is RequestDecision.WAIT and not observe:
            self._park(executor, original, instance, node, automation_id, instance_id)
            return tuple(self.settings.wait_edges)
        if decision is not RequestDecision.ALLOW and not observe:
            return self._abort(executor, instance, node, decision, "denied")

        key = (automation_id, instance_id)
        try:
            edges = original(instance, node)
        except BaseException:
            self._safe_release(entity_ids, automation_id, success=False)
            raise

        success = bool(edges) and "then" in set(edges)
        aborted = key in self._aborted
        self._aborted.discard(key)
        if success and not aborted:
            self._note_af_actions(automation_id, action, params, entity_ids)
        self._safe_release(entity_ids, automation_id, success=success and not aborted)
        if aborted:                                   # 在飞期间被抢占 → 走 on_error 边
            return self._abort(executor, instance, node, RequestDecision.REJECT, "preempted")
        return edges

    # --------------------------- 事件联动 --------------------------- #
    def handle_state_change(self, entity_id: str, new_state: Any, old_state: Any = None, timestamp: Any = None) -> Any:
        """订阅总线状态变化：转发给 InterventionDetector，user_override 触发冷却。"""
        event = None
        if self.intervention is not None:
            try:
                event = self.intervention.handle_state_change(entity_id, new_state, old_state, timestamp)
            except Exception as exc:
                self._audit_degraded("intervention", exc, entity_id, "")
                event = None
        self.handle_intervention(event, entity_id=entity_id)
        return event

    def handle_intervention(self, event: Any, *, entity_id: str = "") -> None:
        kind = getattr(event, "kind", None)
        target = getattr(event, "entity_id", None) or entity_id
        if kind in ("user_override", "manual_override") and target:
            try:
                self.arbiter.on_user_override(str(target))
            except Exception as exc:
                self._audit_degraded("user_override", exc, str(target), "")

    # --------------------------- 内部回调 --------------------------- #
    def _on_preempted(self, lock: Any) -> None:
        self._aborted.add((lock.automation_id, lock.instance_id))

    def _on_pending_ready(self, pending: Any) -> None:
        key = (pending.automation_id, pending.instance_id)
        if key not in self._waiters:
            return
        waiter = self._waiters.pop(key)

        def _retry() -> None:
            executor, original, instance, node = waiter
            try:
                edges = self.dispatch(executor, original, instance, node)
            except Exception as exc:
                self._audit_degraded("retry", exc, pending.entity_id, pending.automation_id)
                edges = None
            if self.on_resume is not None:
                try:
                    self.on_resume(instance, node, edges)
                except Exception as exc:
                    self._audit_degraded("resume", exc, pending.entity_id, pending.automation_id)

        if self.scheduler is not None:
            try:
                self.scheduler.call_later(0.0, _retry)   # 锁释放就立即重试，无延迟重试
                return
            except Exception as exc:
                self._audit_degraded("schedule", exc, pending.entity_id, pending.automation_id)
        _retry()

    def _park(self, executor: Any, original: Any, instance: Any, node: Any, automation_id: str, instance_id: str) -> None:
        self._waiters[(automation_id, instance_id)] = (executor, original, instance, node)

    # --------------------------- 工具 --------------------------- #
    def _abort(self, executor: Any, instance: Any, node: Any, decision: RequestDecision, reason: str) -> Any:
        exc = ConflictBlocked(decision, reason)
        soft = getattr(executor, "_soft_fail", None)
        if callable(soft):
            try:
                return soft(instance, node, exc)
            except Exception as err:
                self._audit_degraded("soft_fail", err, "", "")
        return set(self.settings.on_error_edges)

    def _safe_release(self, entity_ids: Sequence[str], automation_id: str, *, success: bool) -> None:
        try:
            self.arbiter.release(list(entity_ids), automation_id, success=success)
        except Exception as exc:
            self._audit_degraded("release", exc, entity_ids[0] if entity_ids else "", automation_id)

    def _note_af_actions(self, automation_id: str, action: str, params: Mapping[str, Any], entity_ids: Sequence[str]) -> None:
        if self.intervention is None:
            return
        expected = expected_state_for(action, params)
        for eid in entity_ids:
            try:
                self.intervention.note_af_action(automation_id, eid, action, expected)
            except Exception as exc:
                self._audit_degraded("note_af_action", exc, eid, automation_id)

    def _safe_band(self, automation_id: str) -> str:
        """统一 band 真值源读取（故障优先：缺省 auto，绝不因 band 查询异常而阻断下发）。"""
        try:
            return str(self.conf.band(automation_id))
        except Exception:
            return "auto"

    def _adapter_is_dry(self, executor: Any, node: Any) -> bool:
        try:
            adapters = getattr(executor, "adapters", None)
            adapter = adapters.get(getattr(node, "adapter", "") or "") if adapters is not None else None
            return bool(getattr(adapter, "dry_run", False))
        except Exception:
            return False

    def _audit_degraded(self, phase: str, exc: Exception, entity_id: str, automation_id: str) -> None:
        try:
            from .af_conflict import KIND_DEGRADED, ConflictEvent
        except ImportError:  # pragma: no cover
            from af_conflict import KIND_DEGRADED, ConflictEvent
        import uuid as _uuid

        self.auditor.record(
            ConflictEvent(
                event_id=_uuid.uuid4().hex[:12],
                entity_id=entity_id,
                kind=KIND_DEGRADED,
                requester_id=automation_id,
                holder_id=None,
                timestamp=float(self.clock.monotonic()),
                details={"phase": phase, "error": repr(exc), "fail_open": True},
            )
        )


# --------------------------------------------------------------------------- #
# WebUI 端点（FastAPI 兼容；零新依赖，普通函数可直接单测）
# --------------------------------------------------------------------------- #
def api_conflicts(service: ConflictService, entity_id: str | None = None, automation_id: str | None = None, limit: int = 100) -> dict:
    events = service.auditor.history(entity_id=entity_id, automation_id=automation_id, limit=int(limit))
    return {"count": len(events), "events": [event_to_dict(e) for e in events]}


def api_conflicts_summary(service: ConflictService, automation_id: str | None = None) -> dict:
    if automation_id:
        return {"automation_id": automation_id, "summary": service.auditor.summary(automation_id)}
    return {"summaries": {aid: service.auditor.summary(aid) for aid in service.auditor.automation_ids()}}


def api_conflicts_locks(service: ConflictService) -> dict:
    return {"locks": service.auditor.locks()}


def api_conflicts_reset(service: ConflictService, automation_id: str) -> dict:
    service.arbiter.reset_circuit(automation_id)
    return {"ok": True, "automation_id": automation_id, "circuit": service.arbiter.circuit_state(automation_id)}


def api_conflicts_unlock(service: ConflictService, entity_id: str) -> dict:
    released = service.arbiter.unlock(entity_id, reason="api")
    return {"ok": True, "entity_id": entity_id, "released": bool(released)}


_ROUTES: tuple[tuple[str, str, Callable[..., Any]], ...] = (
    ("GET", "/api/conflicts", api_conflicts),
    ("GET", "/api/conflicts/summary", api_conflicts_summary),
    ("GET", "/api/conflicts/locks", api_conflicts_locks),
    ("POST", "/api/conflicts/{automation_id}/reset", api_conflicts_reset),
    ("DELETE", "/api/conflicts/locks/{entity_id}", api_conflicts_unlock),
)


def install_api(app: Any, service: ConflictService) -> bool:
    """把 5 个端点挂到 FastAPI 风格的 app 上（app/router/add_api_route 任一即可）。"""
    installed = False
    for method, path, handler in _ROUTES:
        fn = handler.__get__(service, type(service))   # 绑定 service，保留可检查签名
        ok = False
        for adder in (getattr(app, "add_api_route", None), getattr(getattr(app, "router", None), "add_api_route", None)):
            if callable(adder):
                try:
                    adder(path, fn, methods=[method])
                    ok = True
                    break
                except TypeError:
                    continue
        if not ok:
            decorate = getattr(app, method.lower(), None)
            if callable(decorate):
                try:
                    decorate(path)(fn)
                    ok = True
                except Exception:
                    ok = False
        installed = installed or ok
    return installed


def install(
    executor: Any,
    *,
    conf: "ConfidenceStore",
    clock: "TimeSource" | None = None,
    env: Mapping[str, str] | None = None,
    scheduler: Any = None,
    intervention: "InterventionDetector" | None = None,
    on_resume: Callable[[Any, Any, Any], None] | None = None,
    auditor: ConflictAuditor | None = None,
) -> ConflictService | None:
    """装配层唯一入口：未启用返回 None（对现有 774 个测试零影响）。"""
    settings = ConflictSettings.from_env(env)
    if settings.mode == "off":
        return None
    service = ConflictService(
        conf,
        clock,
        settings,
        scheduler=scheduler,
        intervention=intervention,
        on_resume=on_resume,
        auditor=auditor,
    )
    service.attach(executor)
    return service
