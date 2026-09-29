"""F12 预触发服务单测（band 策略 A + 真实触发历史 + 经验先验注入点）。

覆盖：auto 档命中即提前触发 / shadow·ask 档命中也不触发（跳过计数） /
概率不足不触发 / on_spawn 钩子记录真实触发 / at-most-once 不重复触发。
零新依赖；不修改现有文件。
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(REPO_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from autoforge.af_conf import ConfidenceStore  # noqa: E402
from autoforge.af_pretrigger import (  # noqa: E402
    PreTriggerBandPolicy,
    PreTriggerService,
    TriggerHistory,
)
from autoforge.af_predict import Predictor  # noqa: E402
from autoforge.af_time import load_tz  # noqa: E402

SH = load_tz("Asia/Shanghai")
BASE = datetime(2026, 9, 14, 0, 0, tzinfo=SH)
FIRE_NOW = BASE + timedelta(days=7, hours=6, minutes=58)  # 高峰前 2 分钟


class FixedClock:
    """测试用固定时钟：now() == local_now() == 设定时刻（tz-aware）。"""

    def __init__(self, t: datetime):
        self._t = t

    def now(self) -> datetime:
        return self._t

    def local_now(self) -> datetime:
        return self._t


class StubExecutor:
    def __init__(self):
        self.calls = []

    def execute(self, automation_id, predicted=False):
        self.calls.append((automation_id, predicted))
        return {"ok": True, "id": automation_id}


class Auto:
    def __init__(self, aid: str):
        self.id = aid


class Graph:
    def __init__(self, autos):
        self._autos = list(autos)

    def __iter__(self):
        return iter(self._autos)

    def get(self, aid: str):
        for a in self._autos:
            if a.id == aid:
                return a
        raise KeyError(aid)


class RuntimeStub:
    def __init__(self, conf, executor, graph, clock, pdir):
        self.conf = conf
        self.executor = executor
        self.graph = graph
        self.clock = clock
        self.persist_dir = pdir
        self.instances = _Instances()


class _Instances:
    def __init__(self):
        self.on_spawn = None


def _daily(hour: int, days: int, aid: str, history: TriggerHistory) -> None:
    for d in range(days):
        at = BASE + timedelta(days=d, hours=hour)
        history.record(aid, at.isoformat())


def _build(conf_value: float, history_events_hour=7, history_days=7):
    pdir = tempfile.mkdtemp()
    history = TriggerHistory(persist_dir=pdir)
    aid = "kettle"
    _daily(history_events_hour, history_days, aid, history)

    conf = ConfidenceStore()
    conf.values[aid] = conf_value

    clock = FixedClock(FIRE_NOW)
    executor = StubExecutor()
    graph = Graph([Auto(aid)])
    runtime = RuntimeStub(conf, executor, graph, clock, pdir)

    predictor = Predictor(history, executor, pdir, clock=clock, tz_name="Asia/Shanghai")
    service = PreTriggerService(
        runtime, predictor=predictor, conf=conf, experience=None,
        threshold=0.8, window_minutes=5.0, interval_seconds=90.0, persist_dir=pdir,
    )
    return service, executor, history, aid


class PreTriggerTest(unittest.TestCase):
    def test_auto_fires_on_high_probability(self):
        svc, executor, _, aid = _build(0.9)  # auto 档
        stats = svc.scan()
        self.assertEqual(executor.calls, [(aid, True)], "auto 档命中应提前触发")
        self.assertEqual(stats["fired"], 1)
        self.assertEqual(stats["skipped_band"], 0)

    def test_shadow_does_not_fire(self):
        svc, executor, _, aid = _build(0.70)  # shadow 档
        stats = svc.scan()
        self.assertEqual(executor.calls, [], "shadow 档命中也不得写设备")
        self.assertEqual(stats["skipped_band"], 1)
        self.assertEqual(stats["fired"], 0)

    def test_ask_does_not_fire(self):
        svc, executor, _, aid = _build(0.30)  # ask 档
        stats = svc.scan()
        self.assertEqual(executor.calls, [], "ask 档命中也不得写设备")
        self.assertEqual(stats["skipped_band"], 1)

    def test_below_threshold_not_fired(self):
        # 历史事件在 12:00，查询在 06:58 → 带内无命中，p≈0
        svc, executor, _, aid = _build(0.9, history_events_hour=12)
        stats = svc.scan()
        self.assertEqual(executor.calls, [], "概率不足不应触发")
        self.assertEqual(stats["below_threshold"], 1)
        self.assertEqual(stats["fired"], 0)

    def test_no_double_fire_at_most_once(self):
        svc, executor, _, aid = _build(0.9)
        svc.scan()
        svc.scan()  # 第二次：predicted 标记已存在
        self.assertEqual(len(executor.calls), 1, "at-most-once：不应重复写设备")

    def test_on_spawn_hook_records_history(self):
        svc, executor, history, aid = _build(0.9)
        fake_instance = type("I", (), {"automation_id": aid, "created_at": FIRE_NOW.isoformat()})()
        svc.record_fire(fake_instance)
        self.assertTrue(history.events_for(aid), "on_spawn 应把真实触发写进历史")

    def test_band_policy_allows_only_auto(self):
        policy = PreTriggerBandPolicy()  # 默认决策 A
        self.assertTrue(policy.allows("auto"))
        self.assertFalse(policy.allows("shadow"))
        self.assertFalse(policy.allows("ask"))


if __name__ == "__main__":
    unittest.main()
