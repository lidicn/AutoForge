"""FeedbackRecorder / FeedbackExporter 单测。"""
from autoforge.af_feedback import (
    FeedbackFilter, FeedbackKind, FeedbackRecorder, FeedbackWeights, store_source,
)
from conftest import FakeAudit, FakeClock, make_conf, make_recorder


def test_positive_lifts_conf():
    conf = make_conf({"a1": 0.70})
    rec = make_recorder(conf, FakeClock())
    event = rec.emit("a1", FeedbackKind.POSITIVE)
    assert event.conf_before == 0.70
    assert abs(event.conf_after - 0.73) < 1e-9
    assert conf.values["a1"] == event.conf_after


def test_negative_kinds_have_distinct_weights():
    w = FeedbackWeights()
    assert w.user_reject < w.drift < w.device_error < 0
    conf = make_conf({"a1": 0.90})
    rec = make_recorder(conf, FakeClock())
    rec.emit("a1", FeedbackKind.DEVICE_ERROR)
    after_device = conf.values["a1"]
    rec.emit("a1", FeedbackKind.USER_REJECT)
    after_reject = conf.values["a1"]
    assert (0.90 - after_device) < (after_device - after_reject)


def test_samples_record_kind_and_value():
    conf = make_conf({"a1": 0.70})
    rec = make_recorder(conf, FakeClock())
    rec.emit("a1", FeedbackKind.USER_OVERRIDE)
    assert conf.samples["a1"][-1][0] == "user_override"
    assert abs(conf.samples["a1"][-1][1] - 0.45) < 1e-9


def test_filter_by_time_kind_and_automation():
    clock = FakeClock()
    conf = make_conf({"a1": 0.9, "a2": 0.9})
    rec = make_recorder(conf, clock)
    rec.emit("a1", FeedbackKind.POSITIVE, at=100.0)
    clock.advance(10)
    rec.emit("a2", FeedbackKind.USER_REJECT, at=200.0)
    from autoforge.af_feedback import FeedbackExporter
    exp = FeedbackExporter(recorder=rec)
    filt = FeedbackFilter(from_ts=150.0, kinds={FeedbackKind.USER_REJECT})
    assert [e.automation_id for e in exp.events(filt)] == ["a2"]


def test_exporter_merges_store_samples():
    conf = make_conf({"a1": 0.70})
    rec = make_recorder(conf, FakeClock())
    rec.emit("a1", FeedbackKind.SHADOW_MISS)
    from autoforge.af_feedback import FeedbackExporter
    exp = FeedbackExporter(recorder=rec, extra_sources=[store_source(conf)])
    blocks = exp.to_json()
    assert blocks[0]["automation_id"] == "a1"
    assert {e["type"] for e in blocks[0]["events"]} == {"shadow_miss", "seed"}


def test_clamp_prevents_out_of_range():
    conf = make_conf({"a1": 0.99})
    rec = make_recorder(conf, FakeClock())
    for _ in range(5):
        rec.emit("a1", FeedbackKind.POSITIVE)
    assert conf.values["a1"] == 1.0
    conf.values["a1"] = 0.10
    for _ in range(5):
        rec.emit("a1", FeedbackKind.USER_REJECT)
    assert conf.values["a1"] == 0.0


def test_seed_writes_without_weight():
    conf = make_conf({})
    rec = make_recorder(conf, FakeClock())
    event = rec.seed("a1", 0.75)
    assert conf.values["a1"] == 0.75
    assert event.kind is FeedbackKind.SEED
