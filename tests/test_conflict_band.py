"""F8：冲突仲裁 band 单一真值源 + 与 G4 auto/shadow/ask 联动。

- af_conf.BAND_PRIORITY / PASSIVE_BANDS / CONFIRM_REQUIRED_BANDS 为唯一真值源。
- shadow：只读比对，不参与冲突仲裁（F8 ① 归一）。
- ask：只出提案须人工确认，冲突仲裁拒绝自动下发（F8 ③ 与 G4 联动 + 防御纵深）。
- auto：正常走仲裁（ALLOW 才下发）。

零网络：全部用 fake executor / adapter / conf。
"""

import importlib
import importlib.util
import pathlib
import sys
from dataclasses import dataclass, field
from types import SimpleNamespace

from autoforge.af_conf import (
    BAND_PRIORITY,
    CONFIRM_REQUIRED_BANDS,
    PASSIVE_BANDS,
    is_passive_band,
    requires_confirm_band,
)


def _load(name: str):
    for dotted in (f"autoforge.{name}", f"af.{name}", name):
        try:
            return importlib.import_module(dotted)
        except ImportError:
            continue
    root = pathlib.Path(__file__).resolve().parents[1]
    candidates = sorted(root.rglob(f"{name}.py"))
    for path in candidates:
        if "tests" in path.parts:
            continue
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    raise ImportError(f"cannot locate {name}.py")


af_conflict = _load("af_conflict")
af_conflict_audit = _load("af_conflict_audit")
af_conflict_runtime = _load("af_conflict_runtime")

RequestDecision = af_conflict.RequestDecision
ConflictAuditor = af_conflict_audit.ConflictAuditor
ConflictService = af_conflict_runtime.ConflictService
ConflictSettings = af_conflict_runtime.ConflictSettings
ConflictBlocked = af_conflict_runtime.ConflictBlocked


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = float(start)

    def monotonic(self) -> float:
        return self.t


class FakeConf:
    def __init__(self, values=None, band: str = "auto") -> None:
        self.values = dict(values or {})
        self.default_band = band

    def get(self, automation_id: str) -> float:
        return float(self.values.get(automation_id, 0.5))

    def band(self, automation_id: str) -> str:
        return self.default_band


class CallResult:
    success: bool = True
    data: dict = field(default_factory=dict)
    error = None


class FakeAdapter:
    dry_run = False

    def __init__(self, log):
        self.log = log

    def call(self, action, params):
        self.log.append(("call", action))
        return CallResult()


class FakeExecutor:
    def __init__(self, log):
        self.log = log
        self.adapters = {"light": FakeAdapter(log)}
        self.soft_fail = None

    def _do(self, instance, node):
        self.log.append(("do", node.action))
        adapter = self.adapters.get(node.adapter)
        result = adapter.call(node.action or "", dict(node.params))
        return {"then"} if result.success else {"on_error"}

    def _soft_fail(self, instance, node, exc):
        self.soft_fail = exc
        self.log.append(("soft_fail", getattr(exc, "reason", "")))
        return {"on_error"}


def make_node(action="light.turn_on", entity="light.study"):
    return SimpleNamespace(adapter="light", action=action, params={"entity_id": entity}, canary=None)


def make_instance(automation_id="A", instance_id="i-1"):
    return SimpleNamespace(automation=SimpleNamespace(id=automation_id), instance_id=instance_id)


def make_service(mode="enforce", conf=None, **settings):
    conf = conf or FakeConf({"A": 0.9})
    cfg = ConflictSettings(mode=mode, persist_dir=None, **settings)
    return ConflictService(
        conf,
        FakeClock(),
        cfg,
        auditor=ConflictAuditor(persist_dir=None),
    )


# ── F8 ① band 单一真值源 ────────────────────────────────────────────────
def test_band_source_constants():
    assert BAND_PRIORITY == {"ask": 0, "shadow": 1, "auto": 2}
    assert is_passive_band("shadow") is True
    assert is_passive_band("auto") is False
    assert requires_confirm_band("ask") is True
    assert requires_confirm_band("auto") is False
    assert "shadow" in PASSIVE_BANDS
    assert "ask" in CONFIRM_REQUIRED_BANDS


# ── F8 ③ 与 G4 auto/shadow/ask 联动 ──────────────────────────────────────
def test_shadow_band_bypasses_arbitration():
    conf = FakeConf({"S": 0.9}, band="shadow")
    service = make_service(conf=conf)
    executor = FakeExecutor([])
    service.attach(executor)
    calls = []
    original = service.arbiter.request
    service.arbiter.request = lambda *a, **k: calls.append(1) or original(*a, **k)
    edges = executor._do(make_instance("S", "i-1"), make_node())
    assert edges == {"then"}
    assert calls == []                       # shadow 不参与冲突
    assert service.arbiter.locks() == {}


def test_ask_band_blocked_by_conflict_arbiter():
    conf = FakeConf({"K": 0.5}, band="ask")  # ask：只出提案，须人工确认
    service = make_service(conf=conf)
    executor = FakeExecutor([])
    service.attach(executor)
    edges = executor._do(make_instance("K", "i-1"), make_node())
    assert edges == {"on_error"}             # 冲突仲裁拒绝自动下发
    assert isinstance(executor.soft_fail, ConflictBlocked)
    assert executor.soft_fail.reason == "ask_band_requires_confirmation"
    assert not any(item[0] == "call" for item in executor.log)   # adapter.call 未发生


def test_ask_band_observe_mode_does_not_block():
    conf = FakeConf({"K": 0.5}, band="ask")
    service = make_service(mode="observe", conf=conf)
    executor = FakeExecutor([])
    service.attach(executor)
    edges = executor._do(make_instance("K", "i-1"), make_node())
    assert edges == {"then"}                 # observe 只观测不拦截
    assert any(item[0] == "call" for item in executor.log)


def test_auto_band_arbitrated_normally():
    conf = FakeConf({"A": 0.9}, band="auto")
    service = make_service(conf=conf)
    executor = FakeExecutor([])
    service.attach(executor)
    edges = executor._do(make_instance("A", "i-1"), make_node())
    assert edges == {"then"}                 # auto：仲裁 ALLOW 后正常下发
    assert any(item[0] == "call" for item in executor.log)
