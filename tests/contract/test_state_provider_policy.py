"""StateProvider 跨实现契约：未知实体一律 fail-closed（第七轮审计 key_finding）。

审计实锤：四个实现分两派——仿真底座（`InMemoryStateProvider` / `FakeHA`）抛
`UnknownEntity`，生产实现（`HAStateProvider` / vhass `HassStateProvider`）静默省略。
`and`/`or` 在 `af_ir/expr.py:514-517` 走 `all()`/`any()` **短路**，未求值的分支不会去
`Snapshot.get`，所以"缺的实体运行时自然会报"在生产侧根本不成立：同一条 IR
`or(is_on(motion), is_on(ghost))`（ghost 不存在）在仿真软失效**不执行**、在生产
motion 命中即**执行**——"看到即跑的"当场失效。

本文件把四个实现钉在同一口径上，并留下反证（末两测）说明这套断言真的能判红。
"""

from __future__ import annotations

import pytest

from autoforge.af_adapters.ha import HAStateProvider
from autoforge.af_ir.expr import evaluate
from autoforge.af_state import InMemoryStateProvider, Snapshot, UnknownEntity, make_resolver
from autoforge.af_vhass.bridge import HassStateProvider
from autoforge.af_vhass.fake import FakeHA

MOTION = "binary_sensor.hall_motion"
GHOST = "binary_sensor.this_entity_does_not_exist"
READS = sorted([MOTION, GHOST])

#: 审计给出的原始复现形态
OR_EXPR = {
    "op": "or",
    "args": [
        {"op": "is_on", "value": {"var": f"entity.{MOTION}"}},
        {"op": "is_on", "value": {"var": f"entity.{GHOST}"}},
    ],
}


# ── 生产侧两个实现的下层桩（本机无 paho-mqtt / 不连真实 HA）───────────────
class _Transport:
    """`HATransport.all_states()` 的桩：entity_id → (state, attributes)。"""

    def __init__(self, states):
        self._states = dict(states)

    def all_states(self):
        return dict(self._states)


class _State:
    def __init__(self, state, attributes=None):
        self.state = state
        self.attributes = dict(attributes or {})


class _States:
    def __init__(self, states):
        self._states = dict(states)

    def get(self, entity_id):
        return self._states.get(entity_id)


class _Hass:
    """`hass.states.get(...)` 的桩。"""

    def __init__(self, states):
        self.states = _States(states)


#: 四个实现 = 契约的全部适用面（新增实现必须同时进这张表）
MAKERS = {
    "InMemoryStateProvider": lambda: InMemoryStateProvider(states={MOTION: "on"}),
    "FakeHA": lambda: FakeHA(states={MOTION: "on"}),
    "HAStateProvider": lambda: HAStateProvider(transport=_Transport({MOTION: ("on", {})})),
    "HassStateProvider": lambda: HassStateProvider(_Hass({MOTION: _State("on")})),
}


def _policy(provider) -> str:
    """取快照这一刻的失效模式：抛 = fail_closed，返回 = fail_open。"""
    try:
        provider.snapshot(READS)
    except UnknownEntity:
        return "fail_closed"
    return "fail_open"


@pytest.mark.parametrize("name", sorted(MAKERS))
def test_unknown_entity_raises_at_snapshot_time(name):
    """四实现同口径：缺实体在 `snapshot()` 就抛，不留给"运行时自己发现"。"""
    assert _policy(MAKERS[name]()) == "fail_closed", (
        f"{name} 对未知实体静默省略 → 与仿真底座结论相反（第七轮审计）"
    )


@pytest.mark.parametrize("name", sorted(MAKERS))
def test_known_entities_snapshot_ok(name):
    """反向不误伤：实体齐全时正常出快照，值可读。"""
    snap = MAKERS[name]().snapshot([MOTION])
    assert snap.get(MOTION) == "on"


# ── 审计的复现表达式：短路是"为什么必须在取快照时就拦"的理由 ──────────────

@pytest.mark.parametrize("name", sorted(MAKERS))
def test_or_shortcircuit_cannot_hide_drift(name):
    """真实调用链先按 `automation.reads()` 整体取快照（含 ghost）再求值。

    抛点在 `snapshot()`，与分支是否被短路无关——四实现结论必须一致。
    """
    provider = MAKERS[name]()
    with pytest.raises(UnknownEntity):
        snap = provider.snapshot(READS)
        evaluate(OR_EXPR, make_resolver(snap, {}, {}))


def test_fail_open_snapshot_would_have_executed():
    """反证短路机制：只要快照少了 ghost，`or` 首支为真就**永不**读到它。

    这正是生产侧旧行为判不出漂移的原因——所以口径只能定在取快照那一刻。
    """
    torn = Snapshot.of({MOTION: "on"})
    assert evaluate(OR_EXPR, make_resolver(torn, {}, {})) is True


class _FailOpenProvider:
    """反面样本（不是产品代码）：证明上面的契约测试真的能判红（铁律 #8）。"""

    def snapshot(self, entity_ids):
        values = {e: "on" for e in entity_ids if e == MOTION}
        return Snapshot(values=values, attributes={})


def test_gate_turns_red_against_fail_open_provider():
    assert _policy(_FailOpenProvider()) == "fail_open"
