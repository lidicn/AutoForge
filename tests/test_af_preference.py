"""af_preference 用户偏好模型单测。"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime

from autoforge.af_preference import (
    PreferenceModel,
    PreferenceRecord,
    context_key,
    time_bucket,
    PREFERENCES_FILE,
)


class TestTimeBucket(unittest.TestCase):
    def test_morning(self):
        self.assertEqual(time_bucket(6), "morning")
        self.assertEqual(time_bucket(11), "morning")

    def test_afternoon(self):
        self.assertEqual(time_bucket(12), "afternoon")
        self.assertEqual(time_bucket(16), "afternoon")

    def test_evening(self):
        self.assertEqual(time_bucket(17), "evening")
        self.assertEqual(time_bucket(21), "evening")

    def test_night(self):
        self.assertEqual(time_bucket(0), "night")
        self.assertEqual(time_bucket(4), "night")
        self.assertEqual(time_bucket(22), "night")


class TestContextKey(unittest.TestCase):
    def test_full_context(self):
        key = context_key(hour=20, weekday=5, scene="movie", entity_id="light.living")
        self.assertIn("time=evening", key)
        self.assertIn("weekday=weekend", key)
        self.assertIn("scene=movie", key)
        self.assertIn("entity=light.living", key)

    def test_workday(self):
        key = context_key(hour=9, weekday=2)
        self.assertIn("weekday=workday", key)

    def test_wildcard(self):
        key = context_key()
        self.assertEqual(key, "time=*|weekday=*|scene=*|entity=*")


class TestPreferenceRecord(unittest.TestCase):
    def test_to_json(self):
        rec = PreferenceRecord(
            record_id="abc",
            automation_id="auto1",
            action="turn_on",
            params={"entity_id": "light.living"},
            context="time=evening|weekday=*|scene=*|entity=*",
            accepted=True,
            timestamp=1234.5,
        )
        d = rec.to_json()
        self.assertEqual(d["action"], "turn_on")
        self.assertTrue(d["accepted"])
        self.assertEqual(d["params"]["entity_id"], "light.living")


class TestPreferenceModel(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.model = PreferenceModel(persist_dir=self.tmpdir, min_samples=2)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_record_accept(self):
        rec = self.model.record_accept(
            "turn_on",
            {"entity_id": "light.living", "brightness": 80},
            context={"hour": 20, "weekday": 5, "scene": "movie"},
            automation_id="auto1",
        )
        self.assertIsInstance(rec, PreferenceRecord)
        self.assertTrue(rec.accepted)
        self.assertEqual(rec.action, "turn_on")

    def test_record_reject(self):
        rec = self.model.record_reject(
            "turn_on",
            {"entity_id": "light.living", "brightness": 100},
            context={"hour": 20, "weekday": 5, "scene": "movie"},
            automation_id="auto1",
        )
        self.assertFalse(rec.accepted)

    def test_preference_best_action(self):
        ctx = {"hour": 20, "weekday": 5, "scene": "movie"}
        # 接受 turn_on 3 次，拒绝 turn_off 2 次
        for _ in range(3):
            self.model.record_accept("turn_on", {"entity_id": "light.living"}, context=ctx, automation_id="auto1")
        for _ in range(2):
            self.model.record_reject("turn_off", {"entity_id": "light.living"}, context=ctx, automation_id="auto1")

        prefs = self.model.preference(ctx)
        self.assertEqual(prefs["best_action"], "turn_on")
        self.assertGreater(prefs["confidence"], 0.6)

    def test_preference_cold_start(self):
        prefs = self.model.preference({"hour": 3, "weekday": 0})
        self.assertIsNone(prefs["best_action"])
        self.assertEqual(prefs["confidence"], 0.0)

    def test_preference_min_samples(self):
        ctx = {"hour": 10, "weekday": 1}
        self.model.record_accept("turn_on", {"entity_id": "light.a"}, context=ctx)
        # 只有 1 个样本，低于 min_samples=2
        prefs = self.model.preference(ctx)
        self.assertIsNone(prefs["best_action"])

    def test_preference_params(self):
        ctx = {"hour": 20, "weekday": 5, "scene": "movie"}
        # brightness=80 接受 3 次，brightness=100 接受 1 次拒绝 2 次
        for _ in range(3):
            self.model.record_accept("turn_on", {"entity_id": "light.living", "brightness": 80}, context=ctx)
        self.model.record_accept("turn_on", {"entity_id": "light.living", "brightness": 100}, context=ctx)
        for _ in range(2):
            self.model.record_reject("turn_on", {"entity_id": "light.living", "brightness": 100}, context=ctx)

        prefs = self.model.preference(ctx)
        self.assertEqual(prefs["best_action"], "turn_on")
        self.assertEqual(prefs["best_params"].get("brightness"), 80)

    def test_preference_filter_by_action(self):
        ctx = {"hour": 10, "weekday": 1}
        for _ in range(3):
            self.model.record_accept("turn_on", {"entity_id": "light.a"}, context=ctx)
        for _ in range(3):
            self.model.record_accept("set_temperature", {"entity_id": "climate.a", "temperature": 24}, context=ctx)

        prefs = self.model.preference(ctx, action="set_temperature")
        self.assertEqual(prefs["best_action"], "set_temperature")
        self.assertNotIn("turn_on", prefs["actions"])

    def test_suggest_low_accept_rate(self):
        # brightness=100 被拒绝多次
        for _ in range(2):
            self.model.record_accept("turn_on", {"entity_id": "light.a", "brightness": 100}, automation_id="auto1")
        for _ in range(4):
            self.model.record_reject("turn_on", {"entity_id": "light.a", "brightness": 100}, automation_id="auto1")
        # brightness=50 接受率高
        for _ in range(3):
            self.model.record_accept("turn_on", {"entity_id": "light.a", "brightness": 50}, automation_id="auto1")

        suggestions = self.model.suggest("auto1")
        self.assertTrue(len(suggestions) > 0)
        self.assertEqual(suggestions[0]["action"], "turn_on")
        self.assertEqual(suggestions[0]["current_params"]["brightness"], 100)

    def test_suggest_no_automation(self):
        suggestions = self.model.suggest("nonexistent")
        self.assertEqual(suggestions, [])

    def test_suggest_high_accept_rate_no_suggestion(self):
        for _ in range(5):
            self.model.record_accept("turn_on", {"entity_id": "light.a"}, automation_id="auto1")
        suggestions = self.model.suggest("auto1")
        self.assertEqual(suggestions, [])

    def test_stats(self):
        self.model.record_accept("turn_on", {"entity_id": "light.a"}, automation_id="auto1")
        self.model.record_reject("turn_off", {"entity_id": "light.a"}, automation_id="auto1")
        stats = self.model.stats()
        self.assertEqual(stats["total_records"], 2)
        self.assertEqual(stats["accepted"], 1)
        self.assertEqual(stats["rejected"], 1)
        self.assertEqual(stats["accept_rate"], 0.5)

    def test_records_for(self):
        self.model.record_accept("turn_on", {"entity_id": "light.a"}, automation_id="auto1")
        self.model.record_accept("turn_off", {"entity_id": "light.a"}, automation_id="auto2")
        self.assertEqual(len(self.model.records_for("auto1")), 1)
        self.assertEqual(len(self.model.records_for(action="turn_on")), 1)
        self.assertEqual(len(self.model.records_for()), 2)

    def test_clear_all(self):
        self.model.record_accept("turn_on", {"entity_id": "light.a"}, automation_id="auto1")
        n = self.model.clear()
        self.assertEqual(n, 1)
        self.assertEqual(self.model.stats()["total_records"], 0)

    def test_clear_by_automation(self):
        self.model.record_accept("turn_on", {"entity_id": "light.a"}, automation_id="auto1")
        self.model.record_accept("turn_on", {"entity_id": "light.b"}, automation_id="auto2")
        n = self.model.clear("auto1")
        self.assertEqual(n, 1)
        self.assertEqual(len(self.model.records_for("auto2")), 1)

    def test_persistence(self):
        self.model.record_accept(
            "turn_on",
            {"entity_id": "light.living", "brightness": 80},
            context={"hour": 20, "weekday": 5},
            automation_id="auto1",
        )
        # 新建模型从同目录加载
        model2 = PreferenceModel(persist_dir=self.tmpdir, min_samples=2)
        self.assertEqual(model2.stats()["total_records"], 1)
        prefs = model2.preference({"hour": 20, "weekday": 5})
        # 只有 1 个样本，min_samples=2，所以 best_action 为 None
        self.assertIsNone(prefs["best_action"])

    def test_persistence_file_format(self):
        """第五轮审计修复后为 append-only JSONL：一条记录一行，不再整档重写。"""
        self.model.record_accept("turn_on", {"entity_id": "light.a"}, automation_id="auto1")
        self.model.record_accept("turn_off", {"entity_id": "light.a"}, automation_id="auto2")
        path = os.path.join(self.tmpdir, PREFERENCES_FILE)
        self.assertTrue(os.path.exists(path))
        with open(path, "r", encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
        self.assertEqual([r["automation_id"] for r in rows], ["auto1", "auto2"])

    def test_wildcard_context_match(self):
        # 记录时只有 time 维度
        self.model.record_accept("turn_on", {"entity_id": "light.a"}, context={"hour": 20})
        # 查询时带更多维度，应该通配匹配到
        prefs = self.model.preference({"hour": 20, "weekday": 5, "scene": "movie"})
        # 只有 1 个样本，min_samples=2，所以 best_action 为 None
        self.assertIsNone(prefs["best_action"])
        # 但 actions 应该有数据
        self.assertIn("turn_on", prefs["actions"])

    def test_record_with_details(self):
        rec = self.model.record_accept(
            "set_temperature",
            {"entity_id": "climate.a", "temperature": 24},
            context={"hour": 14, "weekday": 2},
            automation_id="auto1",
            details={"user_adjusted_to": 23},
        )
        self.assertEqual(rec.details["user_adjusted_to"], 23)

    def test_multiple_automations_same_context(self):
        ctx = {"hour": 8, "weekday": 1}
        for _ in range(3):
            self.model.record_accept("turn_on", {"entity_id": "light.kitchen"}, context=ctx, automation_id="auto1")
        for _ in range(3):
            self.model.record_reject("turn_on", {"entity_id": "light.bedroom"}, context=ctx, automation_id="auto2")

        prefs = self.model.preference(ctx)
        # 同 context 下 turn_on 的总体接受率
        self.assertIn("turn_on", prefs["actions"])

    def test_coerce_param_value(self):
        from autoforge.af_preference import PreferenceModel
        self.assertEqual(PreferenceModel._coerce_param_value("80"), 80)
        self.assertEqual(PreferenceModel._coerce_param_value("0.5"), 0.5)
        self.assertEqual(PreferenceModel._coerce_param_value("True"), True)
        self.assertEqual(PreferenceModel._coerce_param_value("False"), False)
        self.assertEqual(PreferenceModel._coerce_param_value("None"), None)
        self.assertEqual(PreferenceModel._coerce_param_value("hello"), "hello")


if __name__ == "__main__":
    unittest.main()
