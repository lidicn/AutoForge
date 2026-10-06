"""Runtime 总装配层 —— 把所有 mimo 设计的扩展模块统一装进 Runtime。

职责：
* 统一装配 conf 分级引擎 + 冲突仲裁器（各自独立开关，总开关一键启用）
* 模块联动：冲突仲裁器共享 conf 分级引擎的 ConfidenceStore 和 InterventionDetector
* 统一生命周期：tick() 驱动所有定时任务，observe() 统一 SSE 入口
* 统一持久化：所有状态落到 .forge/ 目录
* 统一 API：合并 conf + conflict 端点，由 af_api.py 挂载

不修改任何现有文件，只新增本模块。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

__all__ = ["RuntimeExtensions", "install", "api_handlers", "install_api"]

logger = logging.getLogger(__name__)

_ENV_MASTER = "AUTOFORGE_RUNTIME_EXT"
_ENV_CONF_GRADING = "AUTOFORGE_CONF_GRADING"
_ENV_CONFLICT = "AUTOFORGE_CONFLICT_ARBITER"

_TRUTHY = {"1", "true", "on", "yes", "enforce"}
_OBSERVE_MODES = {"observe", "audit", "log", "watch"}
_FALSY = {"0", "false", "off", "no", "disable", "disabled"}


def _env_flag(name: str, env: Mapping[str, str] | None = None) -> bool | None:
    """读环境变量开关。返回 True/False/None（未设置）。"""
    src = os.environ if env is None else env
    raw = str(src.get(name, "")).strip().lower()
    if raw in _TRUTHY:
        return True
    if raw in _FALSY:
        return False
    return None


def _should_enable(sub_flag: str, master: bool | None, env: Mapping[str, str] | None) -> bool:
    """子模块启用判定：子开关优先，其次总开关。"""
    sub = _env_flag(sub_flag, env)
    if sub is not None:
        return sub
    if master is not None:
        return master
    return False


@dataclass
class RuntimeExtensions:
    """所有扩展模块的统一句柄。"""

    runtime: Any
    grading: Any = None          # ConfGrading | None
    conflict: Any = None         # ConflictService | None
    persist_dir: str | None = None
    _tick_hooks: list[Callable[[], None]] = field(default_factory=list)
    _observe_hooks: list[Callable[[str, Any, Any, float | None], None]] = field(default_factory=list)
    # 生命周期失败记账：persist/restore 失败绝不能静默——restore 静默失败会让服务
    # 以空状态启动却以为已恢复（新增审计 BUG-07）。
    lifecycle_errors: dict[str, str] = field(default_factory=dict)

    def _record_lifecycle_error(self, op: str, exc: BaseException) -> None:
        """记录 persist/restore 失败：日志 + 记账 + 审计条目，三处都留痕。"""
        detail = f"{type(exc).__name__}: {exc}"
        self.lifecycle_errors[op] = detail
        logger.error("扩展模块 %s 失败：%s", op, detail)
        audit = getattr(self.runtime, "audit", None)
        append = getattr(audit, "append", None)
        if callable(append):
            try:
                clock_now = getattr(getattr(self.runtime, "clock", None), "now", None)
                append({
                    "kind": f"runtime_ext_{op}_failed",
                    "error": detail,
                    "at": clock_now() if callable(clock_now) else None,
                })
            except Exception:  # 记账本身失败不得反过来打断调用方
                logger.debug("扩展模块 %s 的审计记账也失败了", op, exc_info=True)

    # ── 生命周期 ──────────────────────────────────────────────────────
    def tick(self) -> None:
        """驱动所有扩展模块的定时任务。"""
        for hook in self._tick_hooks:
            try:
                hook()
            except Exception:
                logger.debug("扩展模块 tick 钩子失败（不阻塞其他模块）", exc_info=True)

    def observe(
        self,
        entity_id: str,
        old_state: Any,
        new_state: Any,
        at: float | None = None,
    ) -> None:
        """SSE 状态变化统一入口：同时喂给 conf 分级引擎和冲突仲裁器。"""
        for hook in self._observe_hooks:
            try:
                hook(entity_id, old_state, new_state, at)
            except Exception:
                logger.debug("扩展模块 observe 钩子失败（不阻塞其他模块）", exc_info=True)

    def persist(self) -> None:
        """统一持久化所有扩展模块状态到 .forge/。"""
        if self.grading is not None:
            try:
                self.grading.persist()
            except Exception as exc:
                self._record_lifecycle_error("persist", exc)
        # ConflictAuditor 是 append-only JSONL，无需显式 persist

    def restore(self) -> None:
        """从 .forge/ 恢复所有扩展模块状态。"""
        if self.grading is not None:
            try:
                self.grading.restore()
            except Exception as exc:
                # 静默恢复失败 = 以空状态起服务却以为已恢复（新增审计 BUG-07）
                self._record_lifecycle_error("restore", exc)

    # ── API ───────────────────────────────────────────────────────────
    def api_handlers(self) -> dict[tuple[str, str], Callable[..., Any]]:
        """合并 conf + conflict 的 API 端点。"""
        handlers: dict[tuple[str, str], Callable[..., Any]] = {}
        if self.grading is not None:
            try:
                from autoforge.af_runtime_ext import api_handlers as _conf_handlers
                handlers.update(_conf_handlers(self.grading))
            except Exception:
                logger.warning("conf 分级引擎 API 端点注册失败（其余端点不受影响）", exc_info=True)
        if self.conflict is not None:
            try:
                from autoforge.af_conflict_runtime import install_api as _conflict_install
                # conflict 的端点通过 install_api(app, service) 挂载，
                # 这里返回一个标记，由 install_api() 统一处理
                handlers[("__conflict__", "__conflict__")] = lambda: self.conflict
            except Exception:
                logger.warning("冲突仲裁器 API 端点注册失败（其余端点不受影响）", exc_info=True)
        return handlers

    @property
    def enabled(self) -> bool:
        return self.grading is not None or self.conflict is not None


def install(
    runtime: Any,
    *,
    enable_conf_grading: bool | None = None,
    enable_conflict: bool | None = None,
    conflict_mode: str | None = None,
    env: Mapping[str, str] | None = None,
) -> RuntimeExtensions:
    """在 Runtime 上装配所有扩展模块。

    装配顺序：
    1. conf 分级引擎（如果启用）— 先装，冲突仲裁器要共享它的 intervention
    2. 冲突仲裁器（如果启用）— 共享 conf、intervention、scheduler
    3. 统一挂到 runtime.extensions

    环境变量优先级：显式参数 > 子模块环境变量 > 总开关环境变量
    """
    src = os.environ if env is None else env
    master = _env_flag(_ENV_MASTER, src)

    # 判定各模块是否启用
    if enable_conf_grading is None:
        enable_conf_grading = _should_enable(_ENV_CONF_GRADING, master, src)
    if enable_conflict is None:
        # 冲突仲裁器特殊：observe/enforce 也是启用信号
        raw_conflict = str(src.get(_ENV_CONFLICT, "")).strip().lower()
        if raw_conflict in _OBSERVE_MODES:
            enable_conflict = True
            if conflict_mode is None:
                conflict_mode = "observe"
        elif raw_conflict in _TRUTHY:
            enable_conflict = True
        elif raw_conflict in _FALSY:
            enable_conflict = False
        else:
            enable_conflict = _should_enable(_ENV_CONFLICT, master, src)

    persist_dir = getattr(runtime, "persist_dir", None)
    ext = RuntimeExtensions(runtime=runtime, persist_dir=persist_dir)

    # ── 1. conf 分级引擎 ──────────────────────────────────────────────
    grading = None
    if enable_conf_grading:
        try:
            from autoforge.af_runtime_ext import install as _install_grading
            grading = _install_grading(runtime)
            ext.grading = grading
            runtime.audit.append({"kind": "runtime_ext_conf_grading_enabled", "at": runtime.clock.now()})
        except Exception as e:
            runtime.audit.append({
                "kind": "runtime_ext_conf_grading_failed",
                "error": str(e),
                "at": runtime.clock.now(),
            })
            grading = None

    # ── 2. 冲突仲裁器 ─────────────────────────────────────────────────
    conflict = None
    if enable_conflict:
        try:
            from autoforge.af_conflict_runtime import ConflictService, ConflictSettings

            # 共享 conf 分级引擎的 intervention（如果有）
            shared_intervention = getattr(grading, "intervention", None) if grading else None

            # 冲突仲裁器配置
            if conflict_mode is not None:
                settings = ConflictSettings(mode=conflict_mode, persist_dir=persist_dir)
            else:
                settings = ConflictSettings.from_env(src)
                if settings.mode == "off" and master:
                    # 总开关开了但冲突仲裁器没设 mode，默认 enforce
                    settings = ConflictSettings(mode="enforce", persist_dir=persist_dir)

            conflict = ConflictService(
                conf=runtime.conf,
                clock=runtime.clock,
                settings=settings,
                scheduler=getattr(runtime, "scheduler", None),
                intervention=shared_intervention,
                auditor=None,  # 用默认 ConflictAuditor
            )
            conflict.attach(runtime.executor)
            ext.conflict = conflict
            runtime.audit.append({
                "kind": "runtime_ext_conflict_enabled",
                "mode": settings.mode,
                "at": runtime.clock.now(),
            })
        except Exception as e:
            runtime.audit.append({
                "kind": "runtime_ext_conflict_failed",
                "error": str(e),
                "at": runtime.clock.now(),
            })
            conflict = None

    # ── 3. 注册 tick hooks ────────────────────────────────────────────
    def _tick_grading() -> None:
        if grading is not None:
            grading.tick(hours=1.0)

    def _tick_conflict() -> None:
        if conflict is not None:
            # 主动清理过期锁（arbiter._expire 是 lazy 的，tick 时调用）
            arbiter = getattr(conflict, "arbiter", None)
            if arbiter is not None:
                now = getattr(runtime.clock, "monotonic", lambda: 0.0)()
                try:
                    arbiter._expire(now)
                except Exception:
                    pass

    def _tick_persist() -> None:
        ext.persist()

    ext._tick_hooks = [_tick_grading, _tick_conflict, _tick_persist]

    # ── 4. 注册 observe hooks ─────────────────────────────────────────
    def _observe_grading(entity_id: str, old_state: Any, new_state: Any, at: float | None) -> None:
        if grading is not None:
            grading.observe(entity_id, old_state, new_state, at=at)

    def _observe_conflict(entity_id: str, old_state: Any, new_state: Any, at: float | None) -> None:
        if conflict is not None:
            conflict.handle_state_change(entity_id, new_state, old_state=old_state)

    ext._observe_hooks = [_observe_grading, _observe_conflict]

    # ── 5. 挂到 runtime ───────────────────────────────────────────────
    runtime.extensions = ext

    # ── 6. 恢复持久化状态 ─────────────────────────────────────────────
    ext.restore()

    return ext


def api_handlers(ext: RuntimeExtensions) -> dict[tuple[str, str], Callable[..., Any]]:
    """合并所有扩展模块的 API 端点。"""
    return ext.api_handlers()


def install_api(app: Any, ext: RuntimeExtensions) -> bool:
    """把所有扩展模块的 API 端点挂到 FastAPI app。

    返回 True 表示至少挂载了一个端点。
    """
    mounted = False

    # conf 分级引擎端点
    if ext.grading is not None:
        try:
            from autoforge.af_runtime_ext import api_handlers as _conf_handlers
            from fastapi import Request

            handlers = _conf_handlers(ext.grading)
            for (method, path), handler in handlers.items():
                if method == "__conflict__":
                    continue

                def _make_endpoint(h: Callable, m: str, p: str):
                    async def _endpoint(request: Request):
                        body = {}
                        if m in ("POST", "PUT", "PATCH"):
                            try:
                                body = await request.json()
                            except Exception:
                                body = {}
                        params = dict(request.query_params)
                        # 路径参数
                        for key, value in request.path_params.items():
                            params[key] = value
                        return h(**{**body, **params})
                    return _endpoint

                endpoint = _make_endpoint(handler, method, path)
                app.add_api_route(path, endpoint, methods=[method])
                mounted = True
        except Exception:
            pass

    # 冲突仲裁器端点
    if ext.conflict is not None:
        try:
            from autoforge.af_conflict_runtime import install_api as _conflict_install
            _conflict_install(app, ext.conflict)
            mounted = True
        except Exception:
            pass

    return mounted
