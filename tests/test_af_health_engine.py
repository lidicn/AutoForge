"""af_health 单测（不覆盖、不修改 tests/test_af_health.py）。

覆盖：六维打分边界、缺数据中性分、确定性、id 归集、五源采集口径、
auto_demote 阈值/幂等/注入 demoter、alert 事件、history 排序与窗口、
故障 fail-open、模块级 facade。
"""

from __future__ import annotations

import json
from types import SimpleNamespace as NS

from autoforge import af_health

import pytest
from pytest import approx


# ----------------------------------------------------------------------
#  假数据源（只实现 §1 内联可见的公开接口）
# ----------------------------------------------------------------------

class FakeExec:
    def __init__(self, stats=None):
        self._stats = dict(stats or {})

    def stats(self, automation_id):
        return self._stats.get(automation_id)

    def automation_ids(self):
        return list(self._stats)


class BrokenExec:
    def stats(self, automation_id):
        raise RuntimeError("boom")

    def automation_ids(self):
        raise RuntimeError("boom")


def ev(event_id, entity_id, kind, requester_id, holder_id=None, timestamp=0.0):
    return NS(event_id=event_id, entity_id=entity_id, kind=kind,
              requester_id=requester_id, holder_id=holder_id,
              timestamp=timestamp, details={})


class FakeAudit:
    def __init__(self, events=None, ids=()):
        self.events = list(events or [])
        self._ids = list(ids)

    def history(self, entity_id=None, automation_id=None, limit=100, kind=None):
        out = []
        for e in reversed(self.events):
            if automation_id is not None and not (
                    e.requester_id == automation_id or e.holder_id == automation_id):
                continue
            if kind is not None and e.kind != kind:
                continue
            out.append(e)
            if len(out) >= max(0, int(limit)):
                break
        return out

    def automation_ids(self):
        return list(self._ids)


class RawAudit:            # 只有 events 字段：走 duck typing 兜底路径
    def __init__(self, events):
        self.events = list(events)


class FakeDetector:
    def __init__(self, records=None):
        self.records = list(records or [])

    def overrides(self):
        return [r for r in self.records if getattr(r, "verdict", "") == "manual_override"]


class OverridesOnly:       # 只有 overrides()：分母不可知
    def __init__(self, records):
        self._records = list(records)

    def overrides(self):
        return list(self._records)


class FakeCanary:
    def __init__(self, records=None):
        self.records = dict(records or {})

    def record(self, automation_id):
        return self.records.get(automation_id)


class NoRecordCanary:      # 没有 record()：走 records 映射
    def __init__(self, records):
        self.records = dict(records)


class FakeConf:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.samples = {}

    def get(self, automation_id):
        return self.values.get(automation_id, 1.0)   # 与 af_conf 一致：缺省 1.0

    def band(self, automation_id):
        v = self.get(automation_id)
        return "auto" if v >= 0.85 else ("shadow" if v >= 0.60 else "ask")


def rec(automation_id, verdict, ts=0.0):
    return NS(record_id="r", verdict=verdict, entity_id="light.x",
              automation_id=automation_id, old_state=None, new_state=None,
              timestamp=ts, details={})


def green(aid="ok"):
    return af_health.HealthInputs(
        automation_id=aid, attempts=10, successes=10, triggers=10,
        overrides=0, attributed=6, conflicts=0,
        canary_drift=0, canary_actions=4, canary_status="promoted", conf=1.0)


def red(aid="bad"):
    return af_health.HealthInputs(
        automation_id=aid, attempts=10, successes=5, triggers=1000,
        overrides=4, attributed=5, conflicts=10,
        canary_drift=5, canary_actions=5, canary_status="demoted", conf=0.20)


def make_engine(conf_values=None, demoter=None, clock=None, policy=None):
    """装配一个 ok=100 / bad≈3 的引擎。"""
    conf_values = {"ok": 1.0, "bad": 0.20} if conf_values is None else conf_values
    events = [ev(f"e{i}", "light.x", "rejected", requester_id="bad") for i in range(10)]
    return af_health.HealthEngine(
        executor_stats=FakeExec({
            "ok": {"attempts": 10, "successes": 10, "triggers": 10},
            "bad": {"attempts": 10, "successes": 5, "triggers": 1000},
        }),
        conflict_audit=FakeAudit(events, ids=["ok", "bad"]),
        intervention_detector=FakeDetector(
            [rec("bad", "manual_override")] * 4 + [rec("bad", "af_caused")]
            + [rec("ok", "af_caused")] * 6),
        canary_supervisor=FakeCanary({
            "ok": NS(drift_count=0, actions=4, status="promoted"),
            "bad": NS(drift_count=5, actions=5, status="demoted"),
        }),
        conf_store=FakeConf(conf_values),
        policy=policy or af_health.HealthPolicy(),
        clock=clock or (lambda: 1_000_000.0),
        demoter=demoter,
    )


P = af_health.HealthPolicy


# ----------------------------------------------------------------------
#  一、纯打分与确定性
# ----------------------------------------------------------------------

def test_health_weights_match_contract():
    assert af_health.WEIGHTS == {
        "success_rate": 0.25, "intervention_rate": 0.20, "conflict_rate": 0.15,
        "canary_drift": 0.15, "conf_trend": 0.15, "trigger_frequency": 0.10}
    assert abs(sum(af_health.WEIGHTS.values()) - 1.0) < 1e-9
    assert af_health.DIM_NAMES == (
        "success_rate", "intervention_rate", "conflict_rate",
        "canary_drift", "conf_trend", "trigger_frequency")


def test_health_round_half_up():
    assert af_health.round_half_up(0.5) == 1
    assert af_health.round_half_up(1.5) == 2
    assert af_health.round_half_up(2.4) == 2
    assert af_health.round_half_up(-0.5) == -1


def test_health_linear_maps_and_clamps():
    lin = af_health._linear
    assert lin(0.5, 0.5, 1.0) == 0.0
    assert lin(1.0, 0.5, 1.0) == 1.0
    assert lin(0.75, 0.5, 1.0) == 0.5
    assert lin(0.2, 0.5, 1.0) == 0.0          # 下界截断
    assert lin(0.0, 0.2, 0.0) == 1.0          # 反向（越小越好）


def test_health_score_success_perfect_floor_unknown():
    p = P()
    assert abs(af_health.score_success(green(), p).score - 100.0) < 0.01 + 0.001  # float tolerance
    assert af_health.score_success(
        af_health.HealthInputs("a", attempts=10, successes=7), p).score == approx(40.0)  # exact
    assert af_health.score_success(
        af_health.HealthInputs("a", attempts=10, successes=5), p).score == approx(0.0)  # exact
    d = af_health.score_success(af_health.HealthInputs("a"), p)
    assert d.known is False and d.score == p.unknown_score


def test_health_score_intervention_zero_worst_and_fallback():
    p = P()
    assert af_health.score_intervention(green(), p).score == approx(100.0)
    assert af_health.score_intervention(red(), p).score == approx(0.0)     # 4/5 = 80%
    d = af_health.score_intervention(
        af_health.HealthInputs("a", overrides=1, attributed=None, attempts=10), p)
    assert d.known and d.raw == 0.1 and "falls back" in d.note
    d = af_health.score_intervention(af_health.HealthInputs("a", overrides=1), p)
    assert d.known is False


def test_health_score_conflict_zero_worst_no_denominator():
    p = P()
    assert af_health.score_conflict(green(), p).score == approx(100.0)
    assert af_health.score_conflict(red(), p).score == approx(0.0)
    d = af_health.score_conflict(af_health.HealthInputs("a", conflicts=0), p)
    assert d.known and d.score == approx(100.0)
    d = af_health.score_conflict(af_health.HealthInputs("a", conflicts=3), p)
    assert d.known is False and d.score == p.unknown_score


def test_health_score_canary_drift_penalty_and_demoted():
    p = P()
    assert af_health.score_canary(green(), p).score == approx(100.0)
    assert af_health.score_canary(
        af_health.HealthInputs("a", canary_drift=1, canary_actions=3), p).score == approx(75.0)
    assert af_health.score_canary(
        af_health.HealthInputs("a", canary_drift=9, canary_actions=9), p).score == approx(0.0)
    assert af_health.score_canary(
        af_health.HealthInputs("a", canary_drift=0, canary_status="demoted"), p).score == approx(0.0)


def test_health_score_conf_level_and_trend():
    p = P()
    up = af_health.score_conf(
        af_health.HealthInputs("a", conf=0.80, conf_prev=0.70), p)
    down = af_health.score_conf(
        af_health.HealthInputs("a", conf=0.80, conf_prev=0.90), p)
    flat = af_health.score_conf(af_health.HealthInputs("a", conf=0.80), p)
    assert up.score > flat.score > down.score
    assert flat.score == approx(80.0)
    assert down.score == approx(60.0)                    # Δ=-0.10 截断 ×2.0


def test_health_score_trigger_band_boundaries():
    p = P()
    f = lambda t: af_health.score_trigger(
        af_health.HealthInputs("a", triggers=t, window_days=7.0), p).score
    assert f(0) == p.trigger_stale_score         # 完全不触发
    assert f(2) == 100.0                         # 2/7 ≈ 0.29/天 在带内
    assert f(7 * 24) == 100.0                    # 24/天 = 带上沿
    assert f(7 * 48) == 50.0                     # 48/天 → 带上沿到 runaway 的中点
    assert f(7 * 72) == 0.0                      # runaway
    d = af_health.score_trigger(af_health.HealthInputs("a"), p)
    assert d.known is False


def test_health_unknown_dims_neutral_fill_gives_50():
    result = af_health.compute_health(af_health.HealthInputs("a"), P())
    assert result.score == approx(50)
    assert result.unknown_dims() == list(af_health.DIM_NAMES)
    assert all(result.dims[n].score == P().unknown_score for n in af_health.DIM_NAMES)


def test_health_compute_is_deterministic():
    a = af_health.compute_health(red("x"), P()).to_dict(P())
    b = af_health.compute_health(red("x"), P()).to_dict(P())
    assert a == b
    assert a == af_health.compute_health(red("x"), P()).to_dict(P())


def test_health_compute_all_green_is_100_all_red_is_low():
    assert af_health.compute_health(green(), P()).score == approx(100)
    assert af_health.compute_health(red(), P()).score < 20


def test_health_result_is_json_serializable():
    payload = af_health.compute_health(red(), P()).to_dict(P())
    assert json.loads(json.dumps(payload))["automation_id"] == "bad"
    assert set(payload["dims"]) == set(af_health.DIM_NAMES)


def test_health_band_of_mirrors_af_conf():
    assert af_health.band_of(0.90) == "auto"
    assert af_health.band_of(0.70) == "shadow"
    assert af_health.band_of(0.30) == "ask"
    assert af_health.band_of(None) == "unknown"


# ----------------------------------------------------------------------
#  二、采集口径
# ----------------------------------------------------------------------

def test_health_automation_ids_union_and_register():
    eng = make_engine()
    eng.canary_supervisor = FakeCanary({"ghost": NS(drift_count=0, actions=0, status="observing")})
    eng.register("manual")
    assert eng.automation_ids() == ["bad", "ghost", "manual", "ok"]


def test_health_exec_alias_normalization_and_derivation():
    eng = make_engine()
    eng.executor_stats = FakeExec({
        "a": {"runs": 10, "ok": 8},                 # 别名
        "b": {"attempts": 10, "errors": 3},         # successes 由 failures 推出
    })
    a, b = eng.inputs("a"), eng.inputs("b")
    assert (a.attempts, a.successes) == (10, 8)
    assert (b.attempts, b.successes, b.failures) == (10, 7, 3)


def test_health_success_unknown_when_no_attempts():
    eng = make_engine()
    eng.executor_stats = FakeExec({"a": {"attempts": 0, "successes": 0, "triggers": 0}})
    inputs = eng.inputs("a")
    d = af_health.score_success(inputs, P())
    assert d.known is False and d.score == P().unknown_score


def test_health_conflict_kinds_and_involvement():
    policy = P(conflict_kinds=frozenset({"rejected", "preempted"}))
    eng = make_engine(policy=policy)
    eng.conflict_audit = RawAudit([
        ev("1", "e1", "rejected", "a", None),          # 计入（请求者）
        ev("2", "e2", "preempted", "b", "a"),          # 计入（被抢占持有者）
        ev("3", "e3", "user_cooldown", "user", "a"),   # kind 过滤掉
        ev("4", "e4", "rejected", "b", "b"),           # 与 a 无关
    ])
    eng.executor_stats = FakeExec({"a": {"attempts": 10, "successes": 10}})
    assert eng.inputs("a").conflicts == 2


def test_health_conflict_time_filter_is_opt_in():
    events = [ev("1", "e1", "rejected", "a", None, timestamp=0.0)]
    eng = make_engine(clock=lambda: 1_000_000.0)
    eng.conflict_audit = RawAudit(events)
    eng.executor_stats = FakeExec({"a": {"attempts": 10, "successes": 10}})
    assert eng.inputs("a").conflicts == 1                 # 默认不过滤（时钟域未知）
    eng.policy = P(filter_events_by_time=True)
    assert eng.inputs("a").conflicts == 0                 # 开了才按窗口截断


def test_health_intervention_counts_exclude_untracked():
    eng = make_engine()
    eng.intervention_detector = FakeDetector([
        rec("a", "manual_override"), rec("a", "manual_override"),
        rec("a", "af_caused"), rec("a", "af_caused"),
        rec("a", "untracked"), rec("b", "manual_override"),
    ])
    assert eng.inputs("a").overrides == 2
    assert eng.inputs("a").attributed == 4


def test_health_intervention_overrides_only_has_no_denominator():
    eng = make_engine()
    eng.intervention_detector = OverridesOnly([rec("a", "manual_override")])
    inputs = eng.inputs("a")
    assert (inputs.overrides, inputs.attributed) == (1, None)
    assert af_health.score_intervention(inputs, P()).known is False


def test_health_canary_read_paths():
    eng = make_engine()
    eng.canary_supervisor = FakeCanary(
        {"a": NS(drift_count=2, actions=4, status="observing")})
    assert eng._read_canary("a", []) == (2, 4, "observing")
    eng.canary_supervisor = NoRecordCanary(
        {"a": {"drift_count": 1, "actions": 2, "status": "promoted"}})
    assert eng._read_canary("a", []) == (1, 2, "promoted")
    assert eng._read_canary("zz", []) == (None, None, None)


def test_health_conf_unseen_follows_conf_store_default():
    eng = make_engine()
    eng.conf_store = FakeConf({})            # get() 缺省 1.0（§1 af_conf 口径）
    assert eng.inputs("ghost").conf == 1.0
    assert af_health.band_of(1.0) == "auto"


# ----------------------------------------------------------------------
#  三、契约接口
# ----------------------------------------------------------------------

def test_health_score_int_and_matches_compute_health():
    eng = make_engine()
    for aid in ("ok", "bad"):
        expected = af_health.compute_health(eng.inputs(aid), eng.policy).score
        score = eng.health_score(aid)
        assert isinstance(score, int) and score == expected and 0 <= score <= 100


def test_health_score_and_report_are_read_only():
    eng = make_engine()
    before = dict(eng.conf_store.values)
    eng.health_score("bad")
    eng.health_report()
    eng.alert(50)
    eng.history("bad")
    assert eng.conf_store.values == before
    assert eng._history == {} and eng._demotions == {}


def test_health_report_sorted_worst_first_and_covers_all_ids():
    eng = make_engine()
    report = eng.health_report()
    assert [e["automation_id"] for e in report] == ["bad", "ok"]
    assert report[0]["score"] < report[1]["score"]
    assert report[0]["band"] == "ask" and report[1]["band"] == "auto"
    assert set(report[0]["dims"]) == set(af_health.DIM_NAMES)
    assert report[0]["failing"] and "canary_drift" in report[0]["failing"]


def test_health_auto_demote_returns_only_below_threshold():
    eng = make_engine()
    assert eng.auto_demote(30) == ["bad"]
    assert eng.auto_demote(0) == []
    assert eng.auto_demote(100.1) == ["bad", "ok"]


def test_health_auto_demote_clamps_conf_into_shadow_band():
    eng = make_engine(conf_values={"ok": 1.0, "bad": 0.95})   # 仅 conf 高，其余全差
    assert eng.health_score("bad") < 30
    assert eng.auto_demote(30) == ["bad"]
    value = eng.conf_store.values["bad"]
    assert af_health.SHADOW_LOW <= value < af_health.AUTO_MIN
    assert eng.conf_store.band("bad") == "shadow"
    assert eng._demotions["bad"]["previous_conf"] == 0.95


def test_health_auto_demote_is_idempotent():
    eng = make_engine(conf_values={"ok": 1.0, "bad": 0.95})
    assert eng.auto_demote(30) == ["bad"]
    clamped = eng.conf_store.values["bad"]
    assert eng.auto_demote(30) == ["bad"]
    assert eng.conf_store.values["bad"] == clamped          # 不再二次下压
    assert len(eng._demotions) == 1
    assert eng.health_report()[0]["demoted"] is True


def test_health_auto_demote_calls_injected_demoter():
    calls = []
    eng = make_engine(demoter=lambda aid: calls.append(aid))
    assert eng.auto_demote(30) == ["bad"]
    assert calls == ["bad"]


def test_health_auto_demote_records_demoter_errors():
    def boom(aid):
        raise RuntimeError("nope")

    eng = make_engine(demoter=boom)
    assert eng.auto_demote(30) == ["bad"]                  # 不抛、返回值照常
    assert eng.demote_errors[0]["automation_id"] == "bad"
    assert "nope" in eng.demote_errors[0]["error"]


def test_health_alert_events_below_threshold():
    eng = make_engine()
    events = eng.alert(50)
    assert [e["automation_id"] for e in events] == ["bad"]
    e = events[0]
    assert e["kind"] == "health_alert" and e["score"] < 50 == e["threshold"]
    assert e["severity"] == "critical"                      # 3 < 30
    assert e["demote_recommended"] is True and "canary_drift" in e["failing"]
    assert json.loads(json.dumps(e))["automation_id"] == "bad"


def test_health_alert_empty_or_warning_band():
    eng = make_engine(conf_values={"ok": 1.0, "bad": 0.95})
    # bad = 0.15*95 ≈ 14 → 仍 < 50；把阈值压到 5 才为空
    assert [e["automation_id"] for e in eng.alert(50)] == ["bad"]
    assert eng.alert(5) == []
    eng2 = make_engine()
    eng2.executor_stats = FakeExec(
        {"mid": {"attempts": 10, "successes": 10, "triggers": 10}})
    eng2.conflict_audit = FakeAudit(ids=["mid"])
    eng2.intervention_detector = FakeDetector([rec("mid", "af_caused")] * 6)
    eng2.canary_supervisor = FakeCanary()
    eng2.conf_store = FakeConf({"mid": 0.80})               # 仅 conf 80 → 总分 62
    events = eng2.alert(100)  # mid=90, 需>90才触发
    assert events[0]["severity"] == "warning"                # 62 >= 30
    assert events[0]["failing"] == []  # mid 各维度均健康，无 failing 维度


def test_health_history_empty_then_sorted_ascending():
    eng = make_engine(clock=lambda: 1_000_000.0)
    assert eng.history("bad") == []
    eng.snapshot(at=200.0)
    eng.snapshot(at=100.0)                                   # 乱序写入
    rows = eng.history("bad", days=1e9)
    assert [r["at"] for r in rows] == [100.0, 200.0]         # 验收 8：按时间排序
    assert [r["score"] for r in rows] == [eng.health_score("bad")] * 2


def test_health_history_days_filter_and_desc():
    eng = make_engine(clock=lambda: 1_000_000.0)
    eng.snapshot(at=0.0)
    eng.snapshot(at=1_000_000.0)
    rows = eng.history("bad", days=7)                        # 窗口 = [1_000_000-7d, now]
    assert [r["at"] for r in rows] == [1_000_000.0]
    rows = eng.history("bad", days=1e9, desc=True)
    assert [r["at"] for r in rows] == [1_000_000.0, 0.0]
    assert rows[0]["dims"].keys() == set(af_health.DIM_NAMES).__class__(
        af_health.DIM_NAMES).keys() if False else set(rows[0]["dims"]) == set(af_health.DIM_NAMES)


def test_health_history_tracks_conf_trend():
    eng = make_engine(clock=lambda: 1_000_000.0, conf_values={"ok": 0.90, "bad": 0.90})
    eng.snapshot(at=100.0)                                   # 记录 conf_prev = 0.90
    eng.conf_store.values["bad"] = 0.70
    inputs = eng.inputs("bad", at=200.0)
    assert inputs.conf == 0.70 and inputs.conf_prev == 0.90
    assert abs(af_health.score_conf(inputs, P()).score - 50.0) < 0.01   # 70 + 2×(-10 截断→-0.10)


def test_health_broken_source_fails_open_with_warning():
    eng = make_engine()
    eng.executor_stats = BrokenExec()
    eng.conflict_audit = None
    eng.intervention_detector = None
    eng.canary_supervisor = None
    eng.conf_store = None
    score = eng.health_score("bad")
    assert score == 50                                       # 全部中性分
    entry = eng._entry("bad", 1_000_000.0)
    assert entry["warnings"] and "executor_stats[bad]" in entry["warnings"][0]
    assert entry["band"] == "unknown"


def test_health_facade_functions_delegate():
    eng = af_health.configure(
        executor_stats=FakeExec({"bad": {"attempts": 10, "successes": 5, "triggers": 1000}}),
        conflict_audit=FakeAudit([ev(f"e{i}", "x", "rejected", "bad") for i in range(10)]),
        intervention_detector=FakeDetector(
            [rec("bad", "manual_override")] * 4 + [rec("bad", "af_caused")]),
        canary_supervisor=FakeCanary({"bad": NS(drift_count=5, actions=5, status="demoted")}),
        conf_store=FakeConf({"bad": 0.20}),
        clock=lambda: 1_000_000.0,
    )
    assert af_health.get_engine() is eng
    assert af_health.health_score("bad") == eng.health_score("bad")
    assert [e["automation_id"] for e in af_health.health_report()] == ["bad"]
    assert af_health.auto_demote(30) == ["bad"]
    assert [e["automation_id"] for e in af_health.alert(50)] == ["bad"]
    eng.snapshot(at=5.0)
    assert [r["at"] for r in af_health.history("bad", days=1e9)] == [5.0]