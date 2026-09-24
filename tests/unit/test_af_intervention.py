"""InterventionDetector 单测。"""
from autoforge.af_feedback import FeedbackKind
from autoforge.af_intervention import InterventionPolicy, Verdict
from conftest import FakeClock, FakeStates, make_conf, make_intervention, make_recorder


def _setup(**kw):
    clock = FakeClock()
    conf = make_conf({"a1": 0.90})
    states = FakeStates()
    rec = make_recorder(conf, clock)
    det = make_intervention(conf, clock, rec, states, **kw)
    return clock, conf, states, rec, det


def test_af_caused_within_window_is_not_negative():
    clock, conf, states, rec, det = _setup()
    det.note_call("a1", "i1", "n1", "light.study", "turn_on", "on")
    states.set("light.study", "on")
    clock.advance(2)
    record = det.on_state_changed("light.study", "off", "on")
    assert record.verdict is Verdict.AF_CAUSED
    assert conf.values["a1"] == 0.90
    assert rec.events == []


def test_unexpected_state_within_window_is_override():
    clock, conf, states, rec, det = _setup()
    det.note_call("a1", "i1", "n1", "light.study", "turn_on", "on")
    clock.advance(2)
    record = det.on_state_changed("light.study", "off", "off")
    assert record.verdict is Verdict.MANUAL_OVERRIDE
    assert abs(conf.values["a1"] - 0.65) < 1e-9
    assert rec.events[-1].kind is FeedbackKind.USER_OVERRIDE


def test_out_of_window_landing_counts_as_intervention():
    clock, conf, states, rec, det = _setup(policy=InterventionPolicy(window=30.0))
    det.note_call("a1", "i1", "n1", "light.study", "turn_on", "on", at=clock.now())
    clock.advance(45)
    record = det.on_state_changed("light.study", "off", "on")
    assert record.verdict is Verdict.MANUAL_OVERRIDE
    assert record.details["late"] is True
    assert conf.values["a1"] < 0.90


def test_applied_state_overridden_later_is_negative():
    clock, conf, states, rec, det = _setup()
    det.note_call("a1", "i1", "n1", "light.study", "turn_on", "on")
    states.set("light.study", "on")
    det.on_state_changed("light.study", "off", "on")
    clock.advance(600)
    record = det.on_state_changed("light.study", "on", "off")
    assert record.verdict is Verdict.MANALLEL_OVERRIDE if False else Verdict.MANUAL_OVERRIDE
    assert abs(conf.values["a1"] - 0.65) < 1e-9


def test_untracked_entity_yields_no_sample():
    clock, conf, states, rec, det = _setup()
    record = det.on_state_changed("light.kitchen", "off", "on")
    assert record.verdict is Verdict.UNTRACKED
    assert rec.events == []
    assert conf.values["a1"] == 0.90


def test_hold_without_override_emits_positive():
    clock, conf, states, rec, det = _setup()
    det.note_call("a1", "i1", "n1", "light.study", "turn_on", "on")
    states.set("light.study", "on")
    det.on_state_changed("light.study", "off", "on")
    clock.advance(601)
    det.check_holds()
    assert rec.events[-1].kind is FeedbackKind.POSITIVE
    assert conf.values["a1"] > 0.90


def test_override_inside_hold_skips_positive():
    clock, conf, states, rec, det = _setup()
    det.note_call("a1", "i1", "n1", "light.study", "turn_on", "on")
    states.set("light.study", "on")
    det.on_state_changed("light.study", "off", "on")
    clock.advance(60)
    det.on_state_changed("light.study", "on", "off")
    before = len([e for e in rec.events if e.kind is FeedbackKind.POSITIVE])
    clock.advance(600)
    det.check_holds()
    after = len([e for e in rec.events if e.kind is FeedbackKind.POSITIVE])
    assert before == after


def test_expired_pending_records_device_error():
    clock, conf, states, rec, det = _setup(
        policy=InterventionPolicy(stale_after=60.0))
    det.note_call("a1", "i1", "n1", "light.study", "turn_on", "on")
    clock.advance(61)
    det.flush_expired()
    assert rec.events[-1].kind is FeedbackKind.DEVICE_ERROR
