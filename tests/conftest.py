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
