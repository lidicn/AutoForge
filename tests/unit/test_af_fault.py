"""G5 故障注入单测：五类故障描述 + 注入原语 + 四类失败映射。"""

from __future__ import annotations

import pytest

from autoforge.af_adapters import CallResult, HAAdapter, MockAdapter
from autoforge.af_fault import (
    FOUR_FAILURES,
    FaultInjectionError,
    FaultKind,
    FaultPlan,
    FaultSpec,
    drop,
    drift,
    inject_adapter_fault,
    inject_drift,
    inject_unavailable,
    reorder,
    reorder_events,
    set_entity_state,
    timeout,
    unavailable,
)
from autoforge.af_state import InMemoryStateProvider
from autoforge.af_vhass.fake import FakeHA, FakeHAAdapter


def test_fault_kinds_are_exactly_five():
    assert {k.value for k in FaultKind} == {"unavailable", "timeout", "reorder", "drop", "drift"}


def test_constructors_set_kind_and_fields():
    assert unavailable("light.x").kind is FaultKind.UNAVAILABLE
    assert timeout("t").kind is FaultKind.TIMEOUT and timeout("t").error == "t"
    assert drop().kind is FaultKind.DROP
    spec = drift("light.x", "off")
    assert spec.kind is FaultKind.DRIFT and spec.entity == "light.x" and spec.wrong_state == "off"
    assert reorder(7).seed == 7


def test_spec_validation_requires_targets():
    with pytest.raises(FaultInjectionError):
        FaultSpec(FaultKind.UNAVAILABLE)
    with pytest.raises(FaultInjectionError):
        FaultSpec(FaultKind.DRIFT, entity="light.x")  # 缺 wrong_state


def test_set_entity_state_dispatches_across_backends():
    ha = FakeHA().seed({"light.x": "off"})
    set_entity_state(ha, "light.x", "unavailable")
    assert ha.get("light.x") == "unavailable"

    mem = InMemoryStateProvider()
    mem.set_state("light.x", "off")
    set_entity_state(mem, "light.x", "unavailable")
    assert mem.snapshot(["light.x"]).get("light.x") == "unavailable"

    with pytest.raises(FaultInjectionError):
        set_entity_state(object(), "light.x", "on")


def test_inject_unavailable_and_drift():
    ha = FakeHA().seed({"sensor.x": "10"})
    inject_unavailable(ha, "sensor.x")
    assert ha.get("sensor.x") == "unavailable"
    inject_drift(ha, "light.x", "on")
    assert ha.get("light.x") == "on"


def test_reorder_events_deterministic_and_lossless():
    events = [{"entity_id": f"e{i}", "state": "on"} for i in range(8)]
    first = reorder_events(events, seed=3)
    second = reorder_events(events, seed=3)
    assert first == second, "同种子必须得到同一顺序"
    assert sorted(e["entity_id"] for e in first) == sorted(e["entity_id"] for e in events), "只重排不增删"
    assert first != events or len(events) < 3, "种子 3 应真的打乱了顺序"


def test_mock_adapter_fault_kinds_carry_marker():
    adapter = MockAdapter()
    adapter.timeout_next("t")
    r = adapter.call("light.turn_on", {"entity_id": "light.x"})
    assert not r.success and r.error == "t" and r.data["fault"] == "timeout"

    adapter.drop_next("d")
    r = adapter.call("light.turn_on", {"entity_id": "light.x"})
    assert r.data["fault"] == "drop"

    adapter.unavailable_next("u")
    r = adapter.call("light.turn_on", {"entity_id": "light.x"})
    assert r.data["fault"] == "unavailable"


def test_fakeha_and_ha_adapter_fault_injection():
    ha = FakeHA().seed({"light.x": "off"})
    fake = FakeHAAdapter(ha)
    fake.timeout_next()
    res = fake.call("light.turn_on", {"entity_id": "light.x"})
    assert not res.success and res.data["fault"] == "timeout"
    assert ha.get("light.x") == "off", "故障时不得下发，状态保持原样"

    haadapter = HAAdapter(dry_run=False, transport=lambda a, p: CallResult.ok())
    haadapter.drop_next()
    assert not haadapter.call("light.turn_on", {}).success


def test_inject_adapter_fault_dispatch_and_errors():
    adapter = MockAdapter()
    assert inject_adapter_fault(adapter, timeout("t")) is True
    assert inject_adapter_fault(adapter, drop("d")) is True
    assert adapter.call("a", {}).data["fault"] == "timeout"
    assert adapter.call("a", {}).data["fault"] == "drop"
    assert inject_adapter_fault(adapter, reorder()) is False, "非适配器类故障不注入"
    with pytest.raises(FaultInjectionError):
        inject_adapter_fault(object(), timeout())


def test_fault_plan_applies_to_states_adapter_and_events():
    ha = FakeHA().seed({"sensor.x": "1"})
    adapter = MockAdapter()
    plan = FaultPlan().add(unavailable("sensor.x")).add(timeout("t")).add(reorder(1))

    assert len(plan.apply_to_states(ha)) == 1
    assert ha.get("sensor.x") == "unavailable"
    assert len(plan.apply_to_adapter(adapter)) == 1
    assert sorted(plan.reorder([1, 2, 3])) == [1, 2, 3]


def test_four_failures_matrix():
    assert set(FOUR_FAILURES) == {"device", "timeout", "cancel", "reject"}
    assert FOUR_FAILURES["device"]["edge"] == "on_error"
    assert FOUR_FAILURES["timeout"]["edge"] == "on_timeout"
    assert FOUR_FAILURES["cancel"]["edge"] == "on_cancel"
    assert FOUR_FAILURES["device"]["recoverable"] == "no"
