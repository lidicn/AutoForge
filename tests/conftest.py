"""pytest 公共夹具。

vhass 与 FakeHA 的切换开关放这里：`AUTOFORGE_VHASS=ha|fake`（默认 `ha`）。
见 docs/G1_ACCEPTANCE.md §4。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# 注意：v1.10.0 默认 fail-closed。独立测试若需要无令牌可达 API，
# 请自行 monkeypatch.setenv("AF_ALLOW_NOAUTH", "1")；不要全局 setdefault——
# 会污染那些故意测 fail-closed 行为的测试（如 test_v0_8_auth.py）。

from autoforge.af_ir import load_graph  # noqa: E402
from autoforge.af_runtime import Runtime, build_runtime  # noqa: E402
from autoforge.af_vhass import FakeHA, FakeHAAdapter, seed_from_graph  # noqa: E402

EXAMPLES = ROOT / "examples" / "ir"


@dataclass
class Harness:
    """验收用例的驾驶台：改状态、发事件、推进时间、断言。"""

    runtime: Runtime
    states: FakeHA
    _seq: int = field(default=0, repr=False)

    def fire(self, entity_id: str, state: str, advance_s: float = 0.0):
        """改变实体状态并发布事件（先改状态再发，保证快照读得到）。"""
        if advance_s:
            self.runtime.advance(advance_s)
        self._seq += 1
        self.states.set(entity_id, state)
        return self.runtime.emit(entity_id, state, last_changed=f"t{self._seq}")

    def advance(self, seconds: float):
        return self.runtime.advance(seconds)

    def get(self, entity_id: str):
        return self.states.get(entity_id)

    @property
    def instances(self):
        return self.runtime.instances.all()

    @property
    def ha_calls(self) -> list[tuple[str, dict]]:
        """FakeHA 适配器收到的调用（用于断言"动作真的下发了吗"）。"""
        return list(self.runtime.adapters.get("ha").calls)


@pytest.fixture
def examples_dir() -> Path:
    return EXAMPLES


def vhass_plugin_loaded(config) -> bool:
    """真 vhass 的 `hass` 夹具由 pytest-homeassistant **插件**提供，插件没注册就没有夹具。

    模块级 skipif 原先只问"包装得上吗"（`find_spec('homeassistant')`），可本仓
    `pyproject.toml:65` 默认 `-p no:homeassistant`（Windows 上 HA runner 要 `fcntl`，
    插件在加载阶段就崩）。于是在 GitHub CI 上：**包在、插件被禁** ⇒ 夹具不存在 ⇒
    收集期 `fixture 'hass' not found` **报错**而不是 skip，CI 从建仓第一条 run 起就红
    （2026-10-02 实测：run 1-26 全 failed，其中 pytest 作业 10 errors）。
    真 vhass 的权威跑法是 `docker run autoforge-test`——`Dockerfile.test` 显式
    `-p pytest_homeassistant_custom_component.plugins` 把插件加载回来。
    """
    return any(
        getattr(mod, "__name__", "").startswith("pytest_homeassistant_custom_component")
        for mod in config.pluginmanager.get_plugins()
    )


def pytest_collection_modifyitems(config, items):
    """离线环境自动 skip 需要真实 HA 的集成测试。

    vhass 插件（pytest-homeassistant）在离线容器里封锁一切 socket，
    真连 HA 的测试会因此 HASocketBlockedError。这些测试统一打
    `@pytest.mark.integration`；仅当 AUTOFORGE_LIVE_HA=1 时才运行。
    """
    if not vhass_plugin_loaded(config):
        reason = (
            "真 vhass 需要 pytest-homeassistant 插件处于启用状态；本仓默认 `-p no:homeassistant`"
            "（Windows 装不上 HA runner）。权威跑法 = docker run autoforge-test"
            "（Dockerfile.test 显式 -p 加载插件）"
        )
        for item in items:
            if "vhass" in item.keywords:
                item.add_marker(pytest.mark.skip(reason=reason))
    live_ha = os.environ.get("AUTOFORGE_LIVE_HA", "").lower() in ("1", "true", "yes")
    if live_ha:
        return
    skip_reason = "integration test needs live HA; set AUTOFORGE_LIVE_HA=1 to run"
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(pytest.mark.skip(reason=skip_reason))


@pytest.fixture
def vhass_mode() -> str:
    """`ha` = 真 vhass（pytest-homeassistant）；`fake` = 内置 FakeHA 降级。"""
    return os.environ.get("AUTOFORGE_VHASS", "ha").lower()


@pytest.fixture
def make_harness(examples_dir):
    """构造一个可跑的 Runtime：FakeHA 状态源 + 会真的改状态的 ha 适配器。"""

    def _make(name: str, seed: dict[str, str] | None = None) -> Harness:
        graph = load_graph(examples_dir / name)
        runtime = build_runtime(graph)
        states = seed_from_graph(graph, seed or {}, clock=runtime.clock)
        runtime.states = states
        runtime.instances.states = states
        runtime.scheduler.states = states
        runtime.executor.states = states  # canary 漂移检测必须读同一份状态源
        runtime.adapters.register(FakeHAAdapter(states))
        return Harness(runtime, states)

    return _make


# =====================================================================
# conf 分级引擎单测 Fake（mimo 输出）
# =====================================================================
@dataclass
class FakeClock:
    t: float = 1_700_000_000.0
    def now(self) -> float: return self.t
    def advance(self, seconds: float) -> None: self.t += seconds

@dataclass
class FakeAudit:
    entries: list = field(default_factory=list)
    def append(self, entry): self.entries.append(dict(entry)); return entry
    def kinds(self): return [e.get("kind", "") for e in self.entries]

@dataclass
class FakeStates:
    data: dict = field(default_factory=dict)
    def get(self, entity_id): return self.data.get(entity_id)
    def set(self, entity_id, value): self.data[entity_id] = value

@dataclass
class FakeCallResult:
    action: str
    params: dict
    ok: bool = True

@dataclass
class FakeAdapter:
    calls: list = field(default_factory=list)
    dry_run: bool = False
    def call(self, action, params):
        self.calls.append((action, dict(params)))
        return FakeCallResult(action, dict(params))

@dataclass
class FakeNode:
    id: str = "n1"
    kind: str = "do"
    action: str = "turn_on"
    params: dict = field(default_factory=dict)
    entities: list = field(default_factory=lambda: ["light.study"])
    adapter: str = "ha"
    canary: dict = None
    expected: dict = None
    def target_entities(self): return list(self.entities)

@dataclass
class FakeAutomation:
    id: str = "a1"
    nodes: dict = field(default_factory=dict)
    confidence: float = None

@dataclass
class FakeInstance:
    automation: FakeAutomation = None
    id: str = "i1"

@dataclass
class FakeGraph:
    automations: dict = field(default_factory=dict)

@dataclass
class FakeExecutor:
    calls: list = field(default_factory=list)
    def _do(self, instance, node):
        self.calls.append((instance.automation.id, node.action))
        return f"executed:{node.action}"

@dataclass
class FakeCanaryResult:
    action: str = "turn_on"
    params: dict = field(default_factory=dict)
    drift: bool = False
    rolled_back: list = field(default_factory=list)
    def has_drift(self): return self.drift

@dataclass
class FakeGuard:
    def check_and_rollback(self, adapter, res):
        res.rolled_back.append(res.action)
        return [FakeCallResult("rollback", {})]

def make_conf(pairs=None):
    from autoforge.af_conf import ConfidenceStore
    values = dict(pairs or {})
    return ConfidenceStore(values=values, samples={k: [] for k in values})

def make_recorder(conf, clock, audit=None, states=None):
    from autoforge.af_feedback import FeedbackRecorder
    return FeedbackRecorder(conf=conf, clock=clock, audit=audit or FakeAudit(), states=states)

def make_shadow(conf, clock, recorder, states, policy=None, later=None, audit=None, exempt_actions=None, log_path=None):
    from autoforge.af_shadow import ShadowPolicy, ShadowRunner
    return ShadowRunner(conf=conf, states=states, recorder=recorder, clock=clock,
                        audit=audit or FakeAudit(), policy=policy or ShadowPolicy(),
                        later=later, exempt_actions=exempt_actions or frozenset(),
                        log_path=log_path)

def make_intervention(conf, clock, recorder, states, policy=None, later=None, audit=None):
    from autoforge.af_intervention import InterventionDetector
    # states 通过 recorder.states 传递（InterventionDetector._observed 读 recorder.states）
    if recorder is not None and getattr(recorder, 'states', None) is None:
        recorder.states = states
    return InterventionDetector(conf=conf, recorder=recorder, clock=clock,
                                audit=audit or FakeAudit(), policy=policy, later=later)
