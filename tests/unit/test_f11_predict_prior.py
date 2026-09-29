"""v2.4 / F11②：经验先验注入 af_predict 概率合成。

路线图原文写「接进 af_predict 作为先验」，但源码核实 `af_predict.Predictor` 在 `src`
**无任何实例化/调用方**（纯新增独立模块，符合其设计 doc），故本项落地为：
- `af_experience.empirical_prior(entities, adapters)`：经验频次 → 贝叶斯先验 `(mean, weight)`；
- `Predictor.predict/explain` 接受可选 `prior` / `prior_weight`，在概率合成处做**同构贝叶斯
  合成**（与既有 `k·p_hour` 平滑同构，多一项证据）。先验缺失或权重<=0 → 公式与接入前完全一致
  （零回归）。消费闭环（调用方把经验先验喂给 Predictor）待 F12 预触发调用方接入。

语义：经验先验是「活跃度/确立度」先验（经验库只记成功频次、不记触发时刻），不是触发率估计。
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta

from autoforge.af_predict import Predictor
from autoforge.af_time import VirtualTimeSource, load_tz

SH = load_tz("Asia/Shanghai")
BASE = datetime(2026, 9, 14, 0, 0, tzinfo=SH)


class _StubHistory:
    def events_for(self, automation_id):
        return []


class _StubExecutor:
    def run(self, automation_id):
        return {"ok": True}


def _ev(day: int, hour: int, minute: int = 0) -> dict:
    return {"at": (BASE + timedelta(days=day, hours=hour, minutes=minute)).isoformat()}


def _make(tmp: str) -> Predictor:
    return Predictor(
        _StubHistory(), _StubExecutor(), tmp,
        clock=VirtualTimeSource(start=BASE),
        min_events=3, min_days=2,
    )


def _events() -> list:
    # 6 个完整观察日：3 天命中（7:58）、3 天不命中（7:00，落在窗口外）
    hit = [_ev(d, 7, 58) for d in (0, 2, 4)]
    miss = [_ev(d, 7, 0) for d in (1, 3, 5)]
    return hit + miss


NOW = BASE + timedelta(days=6, hours=7, minutes=58)


def test_prior_absent_is_baseline(tmp_path):
    """不传先验 → 概率与接入前完全一致（零回归）。"""
    p = _make(str(tmp_path))
    p.learn("a", _events())
    base = p.predict("a", NOW)
    assert abs(p.predict("a", NOW, prior=0.9) - base) < 1e-9
    assert abs(p.predict("a", NOW, prior=0.9, prior_weight=0.0) - base) < 1e-9


def test_prior_pulls_probability_toward_mean(tmp_path):
    """经验先验把概率拉向先验均值：prior=0.9 抬高、prior=0.0 压低，且介于二者之间。"""
    p = _make(str(tmp_path))
    p.learn("a", _events())
    base = p.predict("a", NOW)
    low = p.predict("a", NOW, prior=0.0, prior_weight=10.0)
    high = p.predict("a", NOW, prior=0.9, prior_weight=10.0)
    assert low < base < high
    # 显式验证合成公式方向：high 应明显高于 base
    assert high - base > 0.1


def test_prior_exposed_in_explain(tmp_path):
    p = _make(str(tmp_path))
    p.learn("a", _events())
    info = p.explain("a", NOW, prior=0.9, prior_weight=10.0)
    assert info["experience_prior_mean"] == 0.9
    assert info["experience_prior_weight"] == 10.0
    assert "p" in info
    # 不传先验 → 字段退化为 None / 0.0
    info0 = p.explain("a", NOW)
    assert info0["experience_prior_mean"] is None
    assert info0["experience_prior_weight"] == 0.0


def test_prior_ignored_in_cold_start(tmp_path):
    """冷启动（无历史）→ 先验被忽略，仍返回 0.0 且不崩溃。"""
    p = _make(str(tmp_path))
    assert p.predict("a", NOW, prior=0.9, prior_weight=10.0) == 0.0
    assert p.explain("a", NOW, prior=0.9, prior_weight=10.0)["p"] == 0.0
