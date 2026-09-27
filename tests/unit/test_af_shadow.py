"""ShadowRunner 单测。"""
from autoforge.af_shadow import Verdict
from conftest import (
    FakeAdapter, FakeAutomation, FakeExecutor, FakeInstance, FakeNode, FakeStates,
    make_conf, make_recorder, make_shadow,
)


def _setup(conf_value=0.75, **kw):
    from conftest import FakeClock
    clock = FakeClock()
    conf = make_conf({"a1": conf_value})
    states = FakeStates()
    rec = make_recorder(conf, clock)
    shadow = make_shadow(conf, clock, rec, states, **kw)
    auto = FakeAutomation(id="a1")
    inst = FakeInstance(automation=auto)
    node = FakeNode(action="turn_on", entities=["light.study"], expected={"light.study": "on"})
    return clock, conf, states, rec, shadow, inst, node


def test_shadow_do_never_calls_adapter():
    clock, conf, states, rec, shadow, inst, node = _setup()
    adapter = FakeAdapter()
    shadow.run_do(inst, node)
    assert adapter.calls == []


def test_shadow_log_records_action_and_expected_state():
    clock, conf, states, rec, shadow, inst, node = _setup()
    record = shadow.run_do(inst, node)
    assert record.action == "turn_on"
    assert record.expected_state == {"light.study": "on"}
    assert record.verdict is Verdict.PENDING
    assert shadow.log.by_automation("a1")[0].record_id == record.record_id


def test_compare_hit_is_positive():
    clock, conf, states, rec, shadow, inst, node = _setup()
    record = shadow.run_do(inst, node)
    states.set("light.study", "on")
    clock.advance(301)
    judged = shadow.compare(record.record_id)
    assert judged.verdict is Verdict.MATCHED
    assert conf.values["a1"] > 0.75


def test_compare_miss_is_weak_negative():
    clock, conf, states, rec, shadow, inst, node = _setup()
    record = shadow.run_do(inst, node)
    states.set("light.study", "off")
    clock.advance(301)
    judged = shadow.compare(record.record_id)
    assert judged.verdict is Verdict.MISSED
    assert conf.values["a1"] < 0.75
    assert shadow.log.streak("a1") == 0


def test_three_hits_promote_to_auto():
    clock, conf, states, rec, shadow, inst, node = _setup()
    states.set("light.study", "on")
    promoted = []
    shadow.on_promote = promoted.append
    for _ in range(3):
        record = shadow.run_do(inst, node)
        clock.advance(301)
        shadow.compare(record.record_id)
    assert conf.values["a1"] == 1.0
    assert conf.band("a1") == "auto"
    assert promoted == ["a1"]


def test_unverifiable_action_does_not_count_toward_streak():
    clock, conf, states, rec, shadow, inst, node = _setup()
    node.expected = None
    node.action = "toggle"
    record = shadow.run_do(inst, node)
    assert record.verdict is Verdict.UNVERIFIABLE
    assert shadow.log.streak("a1") == 0
    assert rec.events == []


def test_decorator_passes_through_in_auto_band():
    clock, conf, states, rec, shadow, inst, node = _setup(conf_value=0.95)
    executor = FakeExecutor()
    binding = shadow.install(executor)
    assert executor._do(inst, node) == "executed:turn_on"
    binding.restore()
    assert executor.calls == [("a1", "turn_on")]


def test_decorator_blocks_in_shadow_band_and_open_ask_in_ask_band():
    clock, conf, states, rec, shadow, inst, node = _setup(conf_value=0.75)
    executor = FakeExecutor()
    asks = []
    shadow.ask_handler = lambda **kw: asks.append(kw) or "ask-1"
    shadow.install(executor)
    assert executor._do(inst, node) is None
    assert executor.calls == []
    conf.values["a1"] = 0.30
    assert executor._do(inst, node) is None
    assert executor.calls == []
    assert asks and asks[0]["automation_id"] == "a1"


# ---- PR 1.3：期望态推导扩表（toggle / set_temperature / set_cover_position / volume_set） ----


def test_derives_set_temperature_from_params():
    clock, conf, states, rec, shadow, inst, node = _setup()
    node.expected = None
    node.action = "set_temperature"
    node.params = {"temperature": 22}
    node.entities = ["climate.living"]
    record = shadow.run_do(inst, node)
    assert record.expected_state == {"climate.living": "22"}
    assert record.verdict is Verdict.PENDING


def test_derives_set_cover_position_from_params():
    clock, conf, states, rec, shadow, inst, node = _setup()
    node.expected = None
    node.action = "set_cover_position"
    node.params = {"position": 50}
    node.entities = ["cover.garage"]
    record = shadow.run_do(inst, node)
    assert record.expected_state == {"cover.garage": "50"}
    assert record.verdict is Verdict.PENDING


def test_derives_volume_set_from_params():
    clock, conf, states, rec, shadow, inst, node = _setup()
    node.expected = None
    node.action = "volume_set"
    node.params = {"volume_level": 0.4}
    node.entities = ["media_player.speaker"]
    record = shadow.run_do(inst, node)
    assert record.expected_state == {"media_player.speaker": "0.4"}
    assert record.verdict is Verdict.PENDING


def test_toggle_derives_inverted_current_state_and_matches():
    clock, conf, states, rec, shadow, inst, node = _setup()
    node.expected = None
    states.set("light.study", "on")
    node.action = "toggle"
    node.entities = ["light.study"]
    record = shadow.run_do(inst, node)
    assert record.expected_state == {"light.study": "off"}
    # 把真实态翻成 off 后比对应命中（可计入转正证据）
    states.set("light.study", "off")
    clock.advance(301)
    judged = shadow.compare(record.record_id)
    assert judged.verdict is Verdict.MATCHED


def test_toggle_unverifiable_for_non_binary_state():
    clock, conf, states, rec, shadow, inst, node = _setup()
    # 当前态非二元（取反映射无覆盖）→ 推导不出 → 不可验证
    node.expected = None
    states.set("light.study", "unavailable")
    node.action = "toggle"
    node.entities = ["light.study"]
    record = shadow.run_do(inst, node)
    assert record.expected_state == {}
    assert record.verdict is Verdict.UNVERIFIABLE
    assert shadow.log.streak("a1") == 0
