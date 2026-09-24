"""CanarySupervisor 单测。"""
from autoforge.af_canary_supervisor import CanaryPolicy, CanarySupervisor
from autoforge.af_feedback import FeedbackKind
from conftest import (
    FakeAutomation, FakeCanaryResult, FakeClock, FakeGuard, FakeNode,
    make_conf, make_recorder,
)


def _setup(conf_value=0.95, **kw):
    clock = FakeClock()
    conf = make_conf({"a1": conf_value})
    rec = make_recorder(conf, clock)
    node = FakeNode(canary={"duration": "24h", "auto_rollback": True})
    auto = FakeAutomation(id="a1", nodes={"n1": node})
    stripped = []
    sup = CanarySupervisor(
        conf=conf, recorder=rec, clock=clock,
        policy=kw.pop("policy", CanaryPolicy()),
        strip_canary=lambda aid: stripped.append(aid) or 1, **kw)
    return clock, conf, rec, node, sup, stripped


def test_no_drift_within_observation_promotes():
    clock, conf, rec, node, sup, stripped = _setup()
    sup.begin("a1")
    sup.on_canary_result("a1", FakeCanaryResult(drift=False))
    clock.advance(24 * 3600 + 1)
    moved = sup.check()
    assert moved == ["a1"]
    assert stripped == ["a1"]
    assert node.canary is None or stripped == ["a1"]
    assert sup.record("a1").status == "promoted"


def test_observation_not_elapsed_does_not_promote():
    clock, conf, rec, node, sup, stripped = _setup()
    sup.begin("a1")
    clock.advance(23 * 3600)
    assert sup.check() == []
    assert sup.record("a1").status == "observing"


def test_drift_rolls_back_penalizes_and_demotes():
    clock, conf, rec, node, sup, stripped = _setup(conf_value=0.86)
    sup.begin("a1")
    result = FakeCanaryResult(drift=True)
    sup.on_canary_result("a1", result, guard=FakeGuard(), adapter=object())
    assert result.rolled_back == ["turn_on"]
    assert any(e.kind is FeedbackKind.DRIFT for e in rec.events)
    assert conf.values["a1"] < 0.85
    assert sup.record("a1").status == "demoted"


def test_drift_with_high_conf_demotes_after_drift():
    clock, conf, rec, node, sup, stripped = _setup(conf_value=0.98)
    sup.begin("a1")
    sup.on_canary_result("a1", FakeCanaryResult(drift=True))
    assert conf.values["a1"] < 0.98
    assert sup.record("a1").status == "demoted"


def test_on_canary_result_auto_enrolls():
    clock, conf, rec, node, sup, stripped = _setup()
    sup.on_canary_result("a1", FakeCanaryResult(drift=False))
    assert sup.record("a1").actions == 1
    assert sup.record("a1").status == "observing"


def test_promoted_then_conf_drops_demotes():
    clock, conf, rec, node, sup, stripped = _setup()
    sup.begin("a1")
    clock.advance(24 * 3600 + 1)
    sup.check()
    assert sup.record("a1").status == "promoted"
    conf.values["a1"] = 0.70
    assert sup.check() == ["a1"]
    assert sup.record("a1").status == "demoted"


def test_dump_load_roundtrip():
    clock, conf, rec, node, sup, stripped = _setup()
    sup.begin("a1")
    sup.on_canary_result("a1", FakeCanaryResult(drift=False))
    payload = sup.dump()
    other = CanarySupervisor(conf=conf, recorder=rec, clock=clock)
    assert other.load(payload) == 1
    assert other.record("a1").actions == 1
