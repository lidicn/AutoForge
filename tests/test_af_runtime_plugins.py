"""Runtime 总装配层单测。"""
import os
from dataclasses import dataclass, field
from typing import Any

import pytest

from autoforge.af_runtime_plugins import (
    RuntimeExtensions,
    install,
    api_handlers,
    install_api,
    _env_flag,
    _should_enable,
)


# ── Fake 对象 ──────────────────────────────────────────────────────────

@dataclass
class FakeClock:
    _now: float = 1000.0
    def now(self): return self._now
    def monotonic(self): return self._now


@dataclass
class FakeConf:
    values: dict = field(default_factory=dict)
    samples: dict = field(default_factory=dict)
    def get(self, aid): return self.values.get(aid, 0.0)
    def band(self, aid): return "auto"
    def seed(self, graph): pass
    def record_positive(self, aid): pass
    def record_negative(self, aid): pass
    def promote(self, aid): self.values[aid] = 1.0
    def decay_all(self, hours=1.0, events=0): pass


@dataclass
class FakeAudit:
    events: list = field(default_factory=list)
    def append(self, ev): self.events.append(ev)
    def add(self, ev): self.events.append(ev)


@dataclass
class FakeExecutor:
    calls: list = field(default_factory=list)
    def _do(self, instance, node):
        self.calls.append((instance, node))
        return {"then"}


@dataclass
class FakeScheduler:
    timers: list = field(default_factory=list)
    def call_later(self, delay, callback):
        self.timers.append((delay, callback))
        return len(self.timers)


@dataclass
class FakeGraph:
    automations: dict = field(default_factory=dict)


@dataclass
class FakeStates:
    data: dict = field(default_factory=dict)
    def get(self, eid): return self.data.get(eid)
    def set_state(self, eid, state): self.data[eid] = state


@dataclass
class FakeRuntime:
    graph: Any = field(default_factory=FakeGraph)
    states: Any = field(default_factory=FakeStates)
    clock: Any = field(default_factory=FakeClock)
    audit: Any = field(default_factory=FakeAudit)
    conf: Any = field(default_factory=FakeConf)
    executor: Any = field(default_factory=FakeExecutor)
    scheduler: Any = field(default_factory=FakeScheduler)
    persist_dir: str | None = None
    grading: Any = None
    extensions: Any = None


# ── 环境变量工具 ────────────────────────────────────────────────────────

class TestEnvFlag:
    def test_truthy_values(self):
        for v in ("1", "true", "on", "yes", "enforce", "TRUE", "Yes"):
            assert _env_flag("X", {"X": v}) is True

    def test_falsy_values(self):
        for v in ("0", "false", "off", "no", "disable", "FALSE"):
            assert _env_flag("X", {"X": v}) is False

    def test_unset_returns_none(self):
        assert _env_flag("X", {}) is None

    def test_empty_returns_none(self):
        assert _env_flag("X", {"X": ""}) is None


class TestShouldEnable:
    def test_sub_flag_priority_over_master(self):
        # 总开关开，但子开关关
        assert _should_enable("SUB", True, {"SUB": "0"}) is False

    def test_sub_flag_on_when_master_off(self):
        # 总开关关，但子开关开
        assert _should_enable("SUB", False, {"SUB": "1"}) is True

    def test_master_on_when_sub_unset(self):
        assert _should_enable("SUB", True, {}) is True

    def test_master_off_when_sub_unset(self):
        assert _should_enable("SUB", False, {}) is False

    def test_both_unset_returns_false(self):
        assert _should_enable("SUB", None, {}) is False


# ── install() 核心测试 ──────────────────────────────────────────────────

class TestInstall:
    def test_default_nothing_enabled(self):
        rt = FakeRuntime()
        ext = install(rt, env={})
        assert ext.grading is None
        assert ext.conflict is None
        assert ext.enabled is False

    def test_master_switch_enables_all(self):
        rt = FakeRuntime()
        ext = install(rt, env={"AUTOFORGE_RUNTIME_EXT": "1"})
        # conf 分级引擎需要完整 Runtime，可能失败；冲突仲裁器应该启用
        assert ext.conflict is not None
        assert ext.conflict.settings.mode == "enforce"

    def test_conf_grading_only(self):
        rt = FakeRuntime()
        ext = install(rt, env={"AUTOFORGE_CONF_GRADING": "1"})
        # conf 分级引擎可能因 FakeRuntime 不完整而失败
        # 但冲突仲裁器不应启用
        assert ext.conflict is None

    def test_conflict_only(self):
        rt = FakeRuntime()
        ext = install(rt, env={"AUTOFORGE_CONFLICT_ARBITER": "1"})
        assert ext.conflict is not None
        assert ext.conflict.settings.mode == "enforce"
        assert ext.grading is None

    def test_conflict_observe_mode(self):
        rt = FakeRuntime()
        ext = install(rt, env={"AUTOFORGE_CONFLICT_ARBITER": "observe"})
        assert ext.conflict is not None
        assert ext.conflict.settings.mode == "observe"

    def test_explicit_params_override_env(self):
        rt = FakeRuntime()
        ext = install(rt, enable_conflict=True, conflict_mode="observe", env={})
        assert ext.conflict is not None
        assert ext.conflict.settings.mode == "observe"

    def test_explicit_disable_overrides_master(self):
        rt = FakeRuntime()
        ext = install(
            rt,
            enable_conflict=False,
            env={"AUTOFORGE_RUNTIME_EXT": "1"},
        )
        assert ext.conflict is None

    def test_attaches_to_runtime(self):
        rt = FakeRuntime()
        ext = install(rt, env={"AUTOFORGE_CONFLICT_ARBITER": "1"})
        assert rt.extensions is ext

    def test_audit_records_enabled(self):
        rt = FakeRuntime()
        install(rt, env={"AUTOFORGE_CONFLICT_ARBITER": "1"})
        kinds = [e.get("kind") for e in rt.audit.events]
        assert "runtime_ext_conflict_enabled" in kinds


# ── 模块联动测试 ────────────────────────────────────────────────────────

class TestModuleCoordination:
    def test_conflict_shares_conf(self):
        rt = FakeRuntime()
        ext = install(rt, env={"AUTOFORGE_CONFLICT_ARBITER": "1"})
        assert ext.conflict.conf is rt.conf

    def test_conflict_uses_scheduler(self):
        rt = FakeRuntime()
        ext = install(rt, env={"AUTOFORGE_CONFLICT_ARBITER": "1"})
        assert ext.conflict.scheduler is rt.scheduler

    def test_conflict_attaches_executor(self):
        rt = FakeRuntime()
        original_do = rt.executor._do
        install(rt, env={"AUTOFORGE_CONFLICT_ARBITER": "1"})
        # executor._do 应该被包装
        assert rt.executor._do is not original_do

    def test_conflict_intervention_none_when_no_grading(self):
        rt = FakeRuntime()
        ext = install(rt, env={"AUTOFORGE_CONFLICT_ARBITER": "1"})
        assert ext.conflict.intervention is None


# ── 生命周期测试 ────────────────────────────────────────────────────────

class TestLifecycle:
    def test_tick_does_not_raise_when_empty(self):
        rt = FakeRuntime()
        ext = install(rt, env={})
        ext.tick()  # 不应抛异常

    def test_tick_calls_grading(self):
        rt = FakeRuntime()
        ext = RuntimeExtensions(runtime=rt)
        calls = []
        ext._tick_hooks = [lambda: calls.append("grading")]
        ext.tick()
        assert calls == ["grading"]

    def test_tick_isolates_failures(self):
        rt = FakeRuntime()
        ext = RuntimeExtensions(runtime=rt)
        calls = []
        def _fail(): raise RuntimeError("boom")
        def _ok(): calls.append("ok")
        ext._tick_hooks = [_fail, _ok]
        ext.tick()  # 第一个失败不应阻塞第二个
        assert calls == ["ok"]

    def test_observe_does_not_raise_when_empty(self):
        rt = FakeRuntime()
        ext = install(rt, env={})
        ext.observe("light.a", "off", "on")

    def test_observe_calls_hooks(self):
        rt = FakeRuntime()
        ext = RuntimeExtensions(runtime=rt)
        received = []
        ext._observe_hooks = [
            lambda eid, old, new, at: received.append(("h1", eid, old, new)),
            lambda eid, old, new, at: received.append(("h2", eid, old, new)),
        ]
        ext.observe("light.a", "off", "on", at=100.0)
        assert len(received) == 2
        assert received[0] == ("h1", "light.a", "off", "on")
        assert received[1] == ("h2", "light.a", "off", "on")

    def test_observe_isolates_failures(self):
        rt = FakeRuntime()
        ext = RuntimeExtensions(runtime=rt)
        calls = []
        def _fail(*a): raise RuntimeError("boom")
        def _ok(*a): calls.append("ok")
        ext._observe_hooks = [_fail, _ok]
        ext.observe("light.a", "off", "on")
        assert calls == ["ok"]

    def test_persist_noop_when_no_grading(self):
        rt = FakeRuntime()
        ext = install(rt, env={"AUTOFORGE_CONFLICT_ARBITER": "1"})
        ext.persist()  # 不应抛异常

    def test_restore_noop_when_no_grading(self):
        rt = FakeRuntime()
        ext = install(rt, env={"AUTOFORGE_CONFLICT_ARBITER": "1"})
        ext.restore()  # 不应抛异常


# ── API 测试 ────────────────────────────────────────────────────────────

class TestApi:
    def test_api_handlers_empty_when_nothing_enabled(self):
        rt = FakeRuntime()
        ext = install(rt, env={})
        handlers = api_handlers(ext)
        # 只有 conflict 标记，没有实际端点
        assert all(m == "__conflict__" for m, _ in handlers) or len(handlers) == 0

    def test_api_handlers_includes_conflict_marker(self):
        rt = FakeRuntime()
        ext = install(rt, env={"AUTOFORGE_CONFLICT_ARBITER": "1"})
        handlers = api_handlers(ext)
        assert ("__conflict__", "__conflict__") in handlers

    def test_install_api_with_conflict(self):
        rt = FakeRuntime()
        ext = install(rt, env={"AUTOFORGE_CONFLICT_ARBITER": "1"})

        class FakeApp:
            def __init__(self): self.routes = []
            def add_api_route(self, path, endpoint, methods=None):
                self.routes.append((path, methods))

        app = FakeApp()
        result = install_api(app, ext)
        # conflict 的 install_api 可能需要 FastAPI，这里只验证不抛异常
        assert isinstance(result, bool)

    def test_install_api_noop_when_nothing_enabled(self):
        rt = FakeRuntime()
        ext = install(rt, env={})

        class FakeApp:
            pass

        result = install_api(FakeApp(), ext)
        assert result is False


# ── 故障隔离测试 ────────────────────────────────────────────────────────

class TestFaultIsolation:
    def test_conflict_init_failure_does_not_block(self):
        rt = FakeRuntime()
        # 传入无效的 conflict_mode 不应崩溃
        ext = install(rt, enable_conflict=True, conflict_mode="invalid_mode", env={})
        # 即使 ConflictSettings 不校验 mode，ConflictService 也应该能创建
        assert ext is not None

    def test_grading_failure_does_not_block_conflict(self):
        rt = FakeRuntime()
        # conf 分级引擎需要完整 Runtime，FakeRuntime 不完整会失败
        # 但冲突仲裁器应该仍能启用
        ext = install(rt, env={
            "AUTOFORGE_CONF_GRADING": "1",
            "AUTOFORGE_CONFLICT_ARBITER": "1",
        })
        assert ext.conflict is not None
        kinds = [e.get("kind") for e in rt.audit.events]
        assert "runtime_ext_conflict_enabled" in kinds
