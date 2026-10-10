"""ShadowRunner 单测。"""
import os

from autoforge import af_watch
from autoforge.af_shadow import ShadowLogStore, Verdict
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
    # AF21：影子档拦下适配器，但返回"前进"边而不是 None——驱动把 None 读成"实例已终止"，
    # 原先多动作自动化只回放得到第一个 do，而 shadow_log 正是 conf grading 的转正证据。
    assert executor._do(inst, node) == {"then"}
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


# ---- PR 1.4：EXEMPT 豁免通道（verdict 枚举 + streak 语义 + 人审入口 + 审计） ----


def test_exempt_verdict_for_side_effect_action():
    clock, conf, states, rec, shadow, inst, node = _setup(exempt_actions={"notify"})
    node.expected = None
    node.action = "notify"
    node.entities = ["notify.living"]
    record = shadow.run_do(inst, node)
    assert record.expected_state == {}
    assert record.verdict is Verdict.EXEMPT
    assert "豁免" in record.note
    # EXEMPT 不计入转正证据（streak 中性、=0）
    assert shadow.log.streak("a1") == 0
    # 审计事件落了 shadow_exempted
    assert "shadow_exempted" in shadow.audit.kinds()


def test_exempt_via_node_flag():
    clock, conf, states, rec, shadow, inst, node = _setup()
    node.expected = None
    node.action = "some_unmodeled_service"
    node.exempt = True  # IR 显式声明该节点副作用不可观测
    node.entities = ["light.study"]
    record = shadow.run_do(inst, node)
    assert record.verdict is Verdict.EXEMPT
    assert "shadow_exempted" in shadow.audit.kinds()


def test_unverifiable_still_default_when_action_not_exempt():
    clock, conf, states, rec, shadow, inst, node = _setup(exempt_actions={"notify"})
    node.expected = None
    node.action = "vacuum_start"  # 未列入豁免清单、又无期望态 → 维持 UNVERIFIABLE
    node.entities = ["vacuum.living"]
    record = shadow.run_do(inst, node)
    assert record.verdict is Verdict.UNVERIFIABLE
    assert record.verdict is not Verdict.EXEMPT


def test_exempt_is_transparent_in_streak():
    clock, conf, states, rec, shadow, inst, node = _setup(exempt_actions={"notify"})
    # MATCHED → EXEMPT → MATCHED：EXEMPT 中位数中性，连续命中仍计 2
    node.expected = None
    node.action = "turn_on"
    node.entities = ["light.study"]
    states.set("light.study", "on")
    r1 = shadow.run_do(inst, node)
    r1.verdict = Verdict.MATCHED

    node.action = "notify"
    node.entities = ["notify.living"]
    r2 = shadow.run_do(inst, node)
    assert r2.verdict is Verdict.EXEMPT

    node.action = "turn_on"
    node.entities = ["light.study"]
    r3 = shadow.run_do(inst, node)
    r3.verdict = Verdict.MATCHED

    assert shadow.log.streak("a1") == 2


def test_exempted_review_queue_filters_by_automation():
    clock, conf, states, rec, shadow, inst, node = _setup(exempt_actions={"notify"})
    node.expected = None
    node.action = "notify"
    node.entities = ["notify.living"]
    shadow.run_do(inst, node)
    # 另一条自动化（a2）也产生一条 EXEMPT
    inst2 = FakeInstance(automation=FakeAutomation(id="a2"), id="i2")
    node2 = _FakeNodeExempt()
    shadow.run_do(inst2, node2)

    all_exempt = shadow.log.exempted()
    assert len(all_exempt) == 2
    only_a1 = shadow.log.exempted("a1")
    assert len(only_a1) == 1
    assert only_a1[0].automation_id == "a1"


class _FakeNodeExempt:
    """另一条自动化（a2）的副作用不可观测节点。"""
    id = "n2"
    kind = "do"
    action = "notify"
    params = {}
    exempt = True

    def target_entities(self):
        return ["notify.other"]


# ---- PR 1.5：shadow_log 落盘 + 重启回放 ----


def _shadow_with_log(tmp_path, clock=None, later=None, log_path=None):
    from conftest import FakeClock
    clock = clock or FakeClock()
    conf = make_conf({"a1": 0.75})
    states = FakeStates()
    rec = make_recorder(conf, clock)
    path = log_path or str(tmp_path / "shadow_log.json")
    shadow = make_shadow(conf, clock, rec, states, log_path=path, later=later)
    inst = FakeInstance(automation=FakeAutomation(id="a1"))
    return shadow, inst, path


def test_shadow_log_persisted_to_disk(tmp_path):
    shadow, inst, path = _shadow_with_log(tmp_path)
    node = FakeNode(action="turn_on", entities=["light.study"], expected={"light.study": "on"})
    shadow.run_do(inst, node)
    assert os.path.exists(path)
    # 重新读回，记录数 == 1
    store = ShadowLogStore()
    assert store.load_file(path) == 1


def test_shadow_log_save_atomic_leaves_no_tmp(tmp_path):
    shadow, inst, path = _shadow_with_log(tmp_path)
    node = FakeNode(action="turn_on", entities=["light.study"], expected={"light.study": "on"})
    shadow.run_do(inst, node)
    assert os.path.exists(path)
    assert not os.path.exists(path + ".tmp")  # 原子替换不留半截文件


def test_shadow_log_replay_after_restart(tmp_path):
    # 第一次运行：shadow 拦截，期望 on（states 已是 on），PENDING（later=None 不调度）
    shadow1, inst1, path = _shadow_with_log(tmp_path, later=None)
    shadow1.states.set("light.study", "on")
    node = FakeNode(action="turn_on", entities=["light.study"], expected={"light.study": "on"})
    rec1 = shadow1.run_do(inst1, node)
    assert rec1.verdict is Verdict.PENDING
    assert os.path.exists(path)

    # 模拟重启：时钟已推进过 compare_after，但新 runner 不主动 compare
    from conftest import FakeClock
    clock2 = FakeClock()
    clock2.advance(1000)
    shadow2, inst2, _ = _shadow_with_log(tmp_path, clock=clock2, later=None, log_path=path)
    shadow2.states.set("light.study", "on")
    # 重启回放：install 时恢复 shadow_log 并补判到期记录
    shadow2.install(FakeExecutor())

    loaded = shadow2.log.get(rec1.record_id)
    assert loaded is not None
    assert loaded.verdict is Verdict.MATCHED  # states 仍为 on → 命中


def test_shadow_log_replay_misses_when_state_changed(tmp_path):
    shadow1, inst1, path = _shadow_with_log(tmp_path, later=None)
    shadow1.states.set("light.study", "on")
    node = FakeNode(action="turn_on", entities=["light.study"], expected={"light.study": "on"})
    rec1 = shadow1.run_do(inst1, node)

    from conftest import FakeClock
    clock2 = FakeClock()
    clock2.advance(1000)
    shadow2, inst2, _ = _shadow_with_log(tmp_path, clock=clock2, later=None, log_path=path)
    shadow2.states.set("light.study", "off")  # 重启后状态变了 → 应判 MISSED
    shadow2.install(FakeExecutor())

    loaded = shadow2.log.get(rec1.record_id)
    assert loaded.verdict is Verdict.MISSED


def test_shadow_compare_feeds_af_watch(tmp_path):
    # PR 1.8：影子比对结论（MATCHED/MISSED）订阅进 af_watch 聚合层
    af_watch.reset()
    shadow, inst, _ = _shadow_with_log(tmp_path, later=None)
    shadow.states.set("light.study", "on")
    node = FakeNode(action="turn_on", entities=["light.study"], expected={"light.study": "on"})
    rec = shadow.run_do(inst, node)
    shadow.clock.advance(301)        # 让比对到期
    judged = shadow.compare(rec.record_id)  # 落点：record_shadow(verified)
    assert judged.verdict is Verdict.MATCHED

    part = af_watch.verified_in_prod_partition()
    assert part["summary"]["total_verified_in_prod"] >= 1
    assert any(a["automation_id"] == "a1" and a["verified_in_prod"] >= 1
               for a in part["automations"])


