"""af_predict 单测（交付单 §4 / §8 验收）。

纯 stdlib + unittest（pytest 亦可收集），零新依赖；不覆盖、不修改现有
tests/test_af_predict.py。覆盖验收项：5 时间模式学习 / 6 高峰高概率 / 7 低峰低概率 /
8 冷启动 / 9 提前触发+标记 / 10 不重复触发 / 11 状态模式影响 / 12 持久化重启。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(REPO_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from autoforge.af_predict import PREDICTIONS_FILE, Predictor  # noqa: E402
from autoforge.af_time import VirtualTimeSource, load_tz  # noqa: E402

SH = load_tz("Asia/Shanghai")
BASE = datetime(2026, 9, 14, 0, 0, tzinfo=SH)               # day0
PEAK_NOW = BASE + timedelta(days=7, hours=6, minutes=58)    # 2026-09-21 06:58
EARLY_NOW = BASE + timedelta(days=7, hours=6, minutes=55)   # 06:55
OFFPEAK_NOW = BASE + timedelta(days=7, hours=13, minutes=2)  # 13:02

# 概率口径的解析值（见 af_predict 模块 docstring）
PEAK_P = 41 / 45        # (7 + 2*(3/5)) / 9
EDGE_P = 22 / 27        # (7 + 2*(1/6)) / 9
HALF_P = 41 / 63        # (5 + 2*(3/7)) / 9
SINGLE_DAY_P = 1.0      # (1 + 2*1) / (1+2) 单天数据 p_hour=1.0


def ev(day: int, hour: int, minute: int = 0, state=None, predicted: bool = False) -> dict:
    at = BASE + timedelta(days=day, hours=hour, minutes=minute)
    rec = {"at": at.isoformat()}
    if state is not None:
        rec["state"] = dict(state)
    if predicted:
        rec["predicted"] = True
    return rec


def daily(hour: int = 7, days: int = 7, minute: int = 0, state=None, predicted: bool = False) -> list:
    return [ev(d, hour, minute, state, predicted) for d in range(days)]


class StubExecutor:
    def __init__(self, fail: bool = False):
        self.calls = []
        self.fail = fail

    def execute(self, automation_id, predicted=False):
        self.calls.append((automation_id, predicted))
        if self.fail:
            raise RuntimeError("boom")
        return {"ok": True, "id": automation_id}


class MinimalExecutor:
    """只有一个 run(automation_id) 的极简执行器。"""

    def __init__(self):
        self.calls = []

    def run(self, automation_id):
        self.calls.append(automation_id)


class CallableExecutor:
    def __init__(self):
        self.calls = []

    def __call__(self, automation_id, predicted=False):
        self.calls.append((automation_id, predicted))


class StubHistory:
    def __init__(self, events=None, state=None):
        self._events = events or {}
        self._state = state

    def events_for(self, automation_id):
        return list(self._events.get(automation_id, []))

    def current_state(self, automation_id):
        return dict(self._state) if self._state else None


class PredictCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="af_predict_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.clock = VirtualTimeSource(start=PEAK_NOW)
        self.executor = StubExecutor()
        self._paths = []

    def make(self, history=None, *, executor=None, clock=None, path=None, **kwargs):
        if path is None:
            path = os.path.join(self.tmp, f"p{len(self._paths)}")
            self._paths.append(path)
        return Predictor(
            history if history is not None else StubHistory(),
            executor if executor is not None else self.executor,
            path,
            clock=clock or self.clock,
            **kwargs,
        )

    def path_of(self, index: int = 0) -> str:
        return self._paths[index]


# ── 契约 / 构造 ─────────────────────────────────────────────────────
class TestContract(PredictCase):
    def test_contract_signatures(self):
        p = self.make()
        sig_learn = type(p).learn.__annotations__ and __import__("inspect").signature(type(p).learn)
        sig_pred = __import__("inspect").signature(type(p).predict)
        sig_pre = __import__("inspect").signature(type(p).pre_trigger)
        sig_init = __import__("inspect").signature(type(p).__init__)
        # 契约调用形态必须能绑定
        sig_init.bind(None, None, None, "/tmp")
        sig_learn.bind(p, "a", [])
        sig_pred.bind(p, "a", PEAK_NOW)
        sig_pred.bind(p, "a", PEAK_NOW, 5)
        sig_pre.bind(p, "a")
        sig_pre.bind(p, "a", 0.8)
        self.assertEqual(sig_pred.parameters["window_minutes"].default, 5)
        self.assertEqual(sig_pre.parameters["threshold"].default, 0.8)

    def test_constructor_defaults(self):
        p = self.make()
        self.assertEqual(p.window_days, 7)
        self.assertTrue(p.predictions_path.endswith(PREDICTIONS_FILE))
        self.assertEqual(p.hourly("ghost"), [0] * 24)
        self.assertIsNone(p.peak_hour("ghost"))


# ── learn：时间/状态模式学习（验收 5）───────────────────────────────
class TestLearn(PredictCase):
    def test_learn_builds_time_histogram(self):
        p = self.make()
        p.learn("a", daily(7) + [ev(d, 21) for d in (0, 1, 2)])
        hist = p.hourly("a")
        self.assertEqual(hist[7], 7)
        self.assertEqual(hist[21], 3)
        self.assertEqual(sum(hist), 10)
        self.assertEqual(hist[3], 0)
        self.assertEqual(p.peak_hour("a"), 7)
        self.assertEqual(p.stats("a")["count"], 10)

    def test_learn_dedupes_identical_events(self):
        p = self.make()
        p.learn("a", daily(7))
        p.learn("a", daily(7))
        self.assertEqual(p.stats("a")["count"], 7)
        self.assertEqual(p.hourly("a")[7], 7)

    def test_learn_drops_bad_timestamps(self):
        p = self.make()
        p.learn(
            "a",
            [
                {"at": "not-a-time"},
                {"at": datetime(2026, 9, 14, 7, 0)},   # naive：B3-AF-04 一律拒绝
                {"nope": 1},
                {"at": "2026-09-14T07:00:00Z"},        # UTC → 本地 15:00
                ev(0, 7),
            ],
        )
        stats = p.stats("a")
        self.assertEqual(stats["count"], 2)
        self.assertEqual(stats["dropped"], 3)
        self.assertEqual(p.hourly("a")[7], 1)
        self.assertEqual(p.hourly("a")[15], 1)

    def test_learn_skips_self_generated_predicted_events(self):
        p = self.make()
        p.learn("a", daily(7, days=7, predicted=True) + [ev(0, 21), ev(1, 21)])
        stats = p.stats("a")
        self.assertEqual(stats["count"], 2)
        self.assertEqual(stats["skipped_predicted"], 7)
        self.assertEqual(p.hourly("a")[7], 0)

    def test_learn_include_predicted_flag(self):
        p = self.make()
        p.learn("a", daily(7, days=7, predicted=True), include_predicted=True)
        self.assertEqual(p.stats("a")["count"], 7)
        self.assertEqual(p.stats("a")["skipped_predicted"], 0)

    def test_learn_reads_history_store_when_events_omitted(self):
        history = StubHistory({"a": daily(7)})
        p = self.make(history)
        p.learn("a")
        self.assertEqual(p.stats("a")["count"], 7)
        self.assertAlmostEqual(p.predict("a", PEAK_NOW, 5), PEAK_P, places=9)


# ── 滑动窗口（最近 N 天）───────────────────────────────────────────
class TestSlidingWindow(PredictCase):
    def test_sliding_window_prunes_old_events(self):
        clock = VirtualTimeSource(start=BASE + timedelta(days=20, hours=12))
        p = self.make(clock=clock, window_days=7)
        p.learn("a", [ev(d, 7) for d in range(20)])
        stats = p.stats("a")
        # 裁剪基准 = 2026-10-04 12:00；7 天前 = 09-27 12:00 → 只剩 09-28..10-03 的 6 条
        self.assertEqual(stats["count"], 6)
        self.assertEqual(stats["pruned"], 14)
        self.assertEqual(p.hourly("a")[7], 6)

    def test_sliding_window_respects_window_days(self):
        clock = VirtualTimeSource(start=BASE + timedelta(days=10, hours=12))
        p = self.make(clock=clock, window_days=3)
        p.learn("a", [ev(d, 7) for d in range(10)])
        stats = p.stats("a")
        self.assertEqual(stats["count"], 2)   # day8 / day9
        self.assertEqual(stats["pruned"], 8)


# ── predict（验收 6/7/8）───────────────────────────────────────────
class TestPredict(PredictCase):
    def test_predict_high_at_peak_hour(self):
        p = self.make()
        p.learn("a", daily(7))
        prob = p.predict("a", PEAK_NOW, 5)
        self.assertAlmostEqual(prob, PEAK_P, places=9)
        self.assertGreater(prob, 0.85)
        self.assertLessEqual(prob, 1.0)

    def test_predict_low_at_off_peak_hour(self):
        p = self.make()
        p.learn("a", daily(7))
        prob = p.predict("a", OFFPEAK_NOW, 5)
        self.assertEqual(prob, 0.0)
        self.assertLess(prob, 0.2)

    def test_predict_peak_dominates_off_peak(self):
        p = self.make()
        p.learn("a", daily(7))
        peak = p.predict("a", PEAK_NOW, 5)
        low = p.predict("a", OFFPEAK_NOW, 5)
        self.assertGreater(peak - low, 0.8)

    def test_predict_cold_start_without_history(self):
        p = self.make()
        info = p.explain("ghost", PEAK_NOW, 5)
        self.assertEqual(info["p"], 0.0)
        self.assertTrue(info["cold_start"])
        self.assertIn("无历史", info["reason"])

    def test_predict_cold_start_single_event(self):
        p = self.make()
        p.learn("a", [ev(6, 7)])
        info = p.explain("a", PEAK_NOW, 5)
        self.assertEqual(info["p"], 0.0)
        self.assertIn("历史不足", info["reason"])

    def test_predict_cold_start_requires_min_days(self):
        p = self.make()
        p.learn("a", [ev(6, 6), ev(6, 7), ev(6, 8)])   # 事件数够，但只有一天
        info = p.explain("a", PEAK_NOW, 5)
        self.assertEqual(info["p"], 0.0)
        self.assertIn("观察天数不足", info["reason"])

    def test_min_days_one_allows_single_day_history(self):
        p = self.make(min_days=1, min_events=1)
        p.learn("a", [ev(6, 6), ev(6, 7), ev(6, 8)])
        self.assertAlmostEqual(p.predict("a", PEAK_NOW, 5), SINGLE_DAY_P, places=9)

    def test_predict_window_edge_excludes_event(self):
        p = self.make()
        p.learn("a", daily(7))
        # 06:55 + 5min = [06:55, 07:00) 不含 07:00 的触发
        self.assertEqual(p.predict("a", EARLY_NOW, 5), 0.0)

    def test_predict_window_edge_includes_event(self):
        p = self.make()
        p.learn("a", daily(7))
        # 06:55 + 6min = [06:55, 07:01) 含 07:00
        self.assertAlmostEqual(p.predict("a", EARLY_NOW, 6), EDGE_P, places=9)
        self.assertGreater(p.predict("a", EARLY_NOW, 6), 0.8)

    def test_predict_partial_week_pattern_value(self):
        p = self.make()
        p.learn("a", [ev(d, 7) for d in range(5)])   # 7 个观察日里命中 5 天
        self.assertAlmostEqual(p.predict("a", PEAK_NOW, 5), HALF_P, places=9)

    def test_predict_ignores_events_after_now(self):
        p = self.make()
        p.learn("a", [ev(8, 7), ev(9, 7), ev(10, 7)])
        info = p.explain("a", PEAK_NOW, 5)
        self.assertEqual(info["events"], 0)
        self.assertEqual(info["p"], 0.0)

    def test_predict_rejects_naive_now(self):
        p = self.make()
        p.learn("a", daily(7))
        with self.assertRaises(ValueError):
            p.predict("a", datetime(2026, 9, 21, 6, 58))

    def test_predict_rejects_invalid_window(self):
        p = self.make()
        p.learn("a", daily(7))
        for bad in (0, -1, float("nan"), float("inf")):
            with self.subTest(window=bad):
                with self.assertRaises(ValueError):
                    p.predict("a", PEAK_NOW, bad)


# ── 状态模式（验收 11）─────────────────────────────────────────────
class TestStatePattern(PredictCase):
    def test_state_match_keeps_probability(self):
        p = self.make()
        p.learn("a", daily(7, state={"mode": "home"}))
        self.assertAlmostEqual(
            p.predict("a", PEAK_NOW, 5, state={"mode": "home"}), PEAK_P, places=9
        )

    def test_state_conflict_zeroes_probability(self):
        p = self.make()
        p.learn("a", daily(7, state={"mode": "home"}))
        self.assertEqual(p.predict("a", PEAK_NOW, 5, state={"mode": "away"}), 0.0)

    def test_state_unknown_keys_do_not_veto(self):
        p = self.make()
        p.learn("a", daily(7, state={"mode": "home", "light": "on"}))
        self.assertAlmostEqual(
            p.predict("a", PEAK_NOW, 5, state={"mode": "home", "extra": "x"}),
            PEAK_P, places=9,
        )
        self.assertAlmostEqual(
            p.predict("a", PEAK_NOW, 5, state={"extra": "x"}), PEAK_P, places=9
        )
        self.assertEqual(p.predict("a", PEAK_NOW, 5, state={"light": "off"}), 0.0)

    def test_state_pattern_share_and_counts(self):
        p = self.make()
        p.learn(
            "a",
            [ev(d, 7, state={"mode": "home"}) for d in range(4)]
            + [ev(4, 7, state={"mode": "away"})],
        )
        self.assertAlmostEqual(p.state_pattern("a", {"mode": "home"}), 0.8, places=9)
        self.assertAlmostEqual(p.state_pattern("a", {"mode": "away"}), 0.2, places=9)
        self.assertEqual(p.state_counts("a"), {"mode=home": 4, "mode=away": 1})

    def test_state_pattern_empty_query_is_one(self):
        p = self.make()
        p.learn("a", daily(7, state={"mode": "home"}))
        self.assertEqual(p.state_pattern("a", {}), 1.0)
        self.assertEqual(p.state_pattern("a"), 1.0)
        self.assertEqual(p.state_pattern("ghost", {"mode": "home"}), 0.0)


# ── 可解释性 ───────────────────────────────────────────────────────
class TestExplain(PredictCase):
    def test_explain_breakdown_is_consistent(self):
        p = self.make()
        p.learn("a", daily(7))
        info = p.explain("a", PEAK_NOW, 5)
        self.assertFalse(info["cold_start"])
        self.assertEqual(info["hits"], 7)
        self.assertEqual(info["trials"], 7)
        self.assertEqual(info["events"], 7)
        self.assertAlmostEqual(info["p_minute"], 1.0, places=9)
        self.assertAlmostEqual(info["p_hour_prior"], 0.6, places=9)
        self.assertAlmostEqual(info["p"], PEAK_P, places=9)
        self.assertGreaterEqual(info["p_hour_prior"], 0.0)
        self.assertLessEqual(info["p_hour_prior"], 1.0)


# ── pre_trigger（验收 9/10）────────────────────────────────────────
class TestPreTrigger(PredictCase):
    def test_pre_trigger_fires_and_marks(self):
        p = self.make()
        p.learn("a", daily(7))
        self.assertTrue(p.pre_trigger("a", threshold=0.7))
        self.assertEqual(self.executor.calls, [("a", True)])
        self.assertTrue(p.is_predicted("a"))
        mark = p.stats("a")["predicted"]
        self.assertTrue(mark["predicted"])
        self.assertTrue(mark["ok"])
        self.assertAlmostEqual(mark["probability"], PEAK_P, places=6)
        self.assertGreater(mark["probability"], 0.8)

    def test_pre_trigger_mark_blocks_second_call(self):
        p = self.make()
        p.learn("a", daily(7))
        self.assertTrue(p.pre_trigger("a", threshold=0.7))
        self.assertFalse(p.pre_trigger("a"))
        self.assertFalse(p.pre_trigger("a"))
        self.assertEqual(len(self.executor.calls), 1)

    def test_pre_trigger_below_threshold_does_not_fire(self):
        clock = VirtualTimeSource(start=OFFPEAK_NOW)
        p = self.make(clock=clock)
        p.learn("a", daily(7))
        self.assertFalse(p.pre_trigger("a"))
        self.assertEqual(self.executor.calls, [])
        self.assertFalse(p.is_predicted("a"))

    def test_pre_trigger_needs_strictly_greater(self):
        p = self.make()
        p.learn("a", daily(7))
        prob = p.predict("a", PEAK_NOW, 5)
        self.assertFalse(p.pre_trigger("a", threshold=prob))          # 相等不触发
        self.assertFalse(p.is_predicted("a"))
        self.assertTrue(p.pre_trigger("a", threshold=prob - 0.05))
        self.assertEqual(len(self.executor.calls), 1)

    def test_pre_trigger_executor_failure_keeps_mark(self):
        executor = StubExecutor(fail=True)
        p = self.make(executor=executor)
        p.learn("a", daily(7))
        self.assertFalse(p.pre_trigger("a"))
        mark = p.stats("a")["predicted"]
        self.assertTrue(mark["predicted"])
        self.assertFalse(mark["ok"])
        self.assertIn("RuntimeError", mark["error"])
        self.assertFalse(p.pre_trigger("a"))          # 不重复触发
        self.assertEqual(len(executor.calls), 1)

    def test_pre_trigger_clear_prediction_rearms(self):
        p = self.make()
        p.learn("a", daily(7))
        self.assertTrue(p.pre_trigger("a", threshold=0.7))
        self.assertTrue(p.clear_prediction("a"))
        self.assertFalse(p.is_predicted("a"))
        self.assertTrue(p.pre_trigger("a", threshold=0.7))
        self.assertEqual(len(self.executor.calls), 2)

    def test_pre_trigger_uses_history_store_state_match(self):
        history = StubHistory(state={"mode": "home"})
        p = self.make(history)
        p.learn("a", daily(7, state={"mode": "home"}))
        self.assertTrue(p.pre_trigger("a", threshold=0.7))
        self.assertEqual(self.executor.calls, [("a", True)])

    def test_pre_trigger_uses_history_store_state_conflict(self):
        history = StubHistory(state={"mode": "away"})
        p = self.make(history)
        p.learn("a", daily(7, state={"mode": "home"}))
        self.assertFalse(p.pre_trigger("a"))
        self.assertEqual(self.executor.calls, [])

    def test_pre_trigger_uses_injected_clock(self):
        clock = VirtualTimeSource(start=OFFPEAK_NOW)   # 系统墙钟与仿真时间无关
        p = self.make(clock=clock)
        p.learn("a", daily(7))
        self.assertFalse(p.pre_trigger("a"))
        clock.advance_to(PEAK_NOW + timedelta(days=1))   # 时间旅行到次日高峰
        self.assertTrue(p.pre_trigger("a", threshold=0.7))
        self.assertEqual(len(self.executor.calls), 1)

    def test_pre_trigger_rejects_invalid_threshold(self):
        p = self.make()
        p.learn("a", daily(7))
        for bad in (-0.01, 1.01, float("nan")):
            with self.subTest(threshold=bad):
                with self.assertRaises(ValueError):
                    p.pre_trigger("a", threshold=bad)


# ── executor 鸭子类型（§7 决策 6）──────────────────────────────────
class TestExecutorAdapters(PredictCase):
    def test_executor_callable_object(self):
        executor = CallableExecutor()
        p = self.make(executor=executor)
        p.learn("a", daily(7))
        self.assertTrue(p.pre_trigger("a", threshold=0.7))
        self.assertEqual(executor.calls, [("a", True)])

    def test_executor_minimal_signature_gets_no_kwargs(self):
        executor = MinimalExecutor()
        p = self.make(executor=executor)
        p.learn("a", daily(7))
        self.assertTrue(p.pre_trigger("a", threshold=0.7))
        self.assertEqual(executor.calls, ["a"])

    def test_executor_unsupported_interface_marks_and_fails(self):
        p = self.make(executor=object())
        p.learn("a", daily(7))
        self.assertFalse(p.pre_trigger("a"))
        self.assertTrue(p.is_predicted("a"))          # at-most-once：标记保留


# ── 持久化（验收 12）───────────────────────────────────────────────
class TestPersistence(PredictCase):
    def test_persistence_round_trip_model(self):
        path = os.path.join(self.tmp, "keep")
        p1 = self.make(path=path)
        p1.learn("a", daily(7) + [ev(0, 21), ev(1, 21)])
        p2 = self.make(path=path, executor=StubExecutor())
        self.assertEqual(p2.hourly("a"), p1.hourly("a"))
        self.assertEqual(p2.state_counts("a"), p1.state_counts("a"))
        self.assertEqual(p2.stats("a")["learned"], 1)
        self.assertEqual(p2.stats("a")["count"], 9)
        self.assertAlmostEqual(p2.predict("a", PEAK_NOW, 5), PEAK_P, places=9)

    def test_persistence_mark_survives_restart(self):
        path = os.path.join(self.tmp, "mark")
        p1 = self.make(path=path)
        p1.learn("a", daily(7))
        self.assertTrue(p1.pre_trigger("a"))
        fresh = StubExecutor()
        p2 = self.make(path=path, executor=fresh)
        self.assertTrue(p2.is_predicted("a"))
        self.assertFalse(p2.pre_trigger("a"))
        self.assertEqual(fresh.calls, [])

    def test_persistence_creates_directory_and_version(self):
        path = os.path.join(self.tmp, "x", "y", "z")
        p = self.make(path=path)
        p.learn("a", daily(7))
        file_path = os.path.join(path, PREDICTIONS_FILE)
        self.assertTrue(os.path.isfile(file_path))
        with open(file_path, encoding="utf-8") as fh:
            payload = json.load(fh)
        self.assertEqual(payload["version"], 1)
        self.assertIn("a", payload["automations"])
        self.assertEqual(len(payload["automations"]["a"]["events"]), 7)

    def test_persistence_quarantines_corrupt_file(self):
        path = os.path.join(self.tmp, "bad")
        os.makedirs(path)
        file_path = os.path.join(path, PREDICTIONS_FILE)
        with open(file_path, "w", encoding="utf-8") as fh:
            fh.write("{ 这不是 json")
        p = self.make(path=path)
        self.assertEqual(p.predict("a", PEAK_NOW, 5), 0.0)   # fail-open 冷启动
        self.assertTrue(os.path.exists(file_path + ".corrupt"))
        p.learn("a", daily(7))                                # 还能继续工作
        with open(file_path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["version"], 1)

    def test_persistence_quarantines_unknown_version(self):
        path = os.path.join(self.tmp, "ver")
        os.makedirs(path)
        file_path = os.path.join(path, PREDICTIONS_FILE)
        with open(file_path, "w", encoding="utf-8") as fh:
            json.dump({"version": 99, "automations": {"a": {"events": [ev(0, 7)]}}}, fh)
        p = self.make(path=path)
        self.assertEqual(p.stats("a")["count"], 0)
        self.assertTrue(os.path.exists(file_path + ".corrupt"))

    def test_reset_clears_model_and_flush_keeps_file(self):
        path = os.path.join(self.tmp, "reset")
        p = self.make(path=path)
        p.learn("a", daily(7))
        p.reset("a")
        self.assertEqual(p.hourly("a"), [0] * 24)
        self.assertEqual(p.predict("a", PEAK_NOW, 5), 0.0)
        p.flush()
        with open(os.path.join(path, PREDICTIONS_FILE), encoding="utf-8") as fh:
            payload = json.load(fh)
        self.assertEqual(payload["automations"], {})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()