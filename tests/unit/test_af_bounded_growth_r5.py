"""第五轮审计（写放大 / 只增不减）修复的守护测试。

对应缺陷：
- P1 `PreferenceModel.record()` 每次全量重写整档 + 明细无上限 → O(N²)
- 回归族 `EventBus` 实体维度缓存键永不摘除

铁律 #8：字节守护配了「把回归塞回去必须报红」的反例。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import pytest

from autoforge import af_preference
from autoforge.af_audit import BREAKER_OPEN, BREAKER_RECOVER
from autoforge.af_bus import ACCEPTED, BREAKER_BLOCKED, BusEvent, EventBus
from autoforge.af_preference import (
    LEGACY_PREFERENCES_FILE,
    PREFERENCES_FILE,
    PreferenceModel,
)
from autoforge.af_time import VirtualTimeSource


def _pref(tmp_path, **kw) -> PreferenceModel:
    return PreferenceModel(persist_dir=str(tmp_path), **kw)


def _rows(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# ── 明细有上限 ────────────────────────────────────────────────────────

def test_records_are_capped_and_oldest_dropped(tmp_path):
    m = _pref(tmp_path, max_records=10)
    for i in range(25):
        m.record("turn_on", {"entity_id": f"light.l{i}"}, accepted=True, automation_id=f"a{i}")
    retained = m.records_for()
    assert len(retained) == 10
    got = m.stats()
    assert got["total_records"] == 10
    assert got["evicted_records"] == 15
    assert got["max_records"] == 10
    assert retained[0].automation_id == "a15"  # 留的是最新的 10 条


def test_cap_is_honoured_on_reload(tmp_path):
    path = os.path.join(str(tmp_path), PREFERENCES_FILE)
    m = _pref(tmp_path, max_records=100)
    for _ in range(60):
        m.record("turn_on", {"entity_id": "light.a"}, accepted=True)
    m._rewrite_all()
    assert len(_rows(path)) == 60

    small = _pref(tmp_path, max_records=10)
    assert small.stats()["total_records"] == 10
    assert small.stats()["evicted_records"] == 50
    # 超限的老档一加载就被压回上限，不会一直拖着 60 行
    assert len(_rows(path)) == 10


# ── 聚合态与留存明细一致（淘汰必须回补）──────────────────────────────

def test_aggregates_match_retained_details_after_eviction(tmp_path):
    m = _pref(tmp_path, min_samples=1, max_records=12)
    # 同一 (context, action, params)：先 10 次接受，再 10 次拒绝 → 最旧的接受被淘汰
    for _ in range(10):
        m.record_accept("turn_on", {"entity_id": "light.a"}, context={"hour": 20})
    for _ in range(10):
        m.record_reject("turn_on", {"entity_id": "light.a"}, context={"hour": 20})

    retained = m.records_for(action="turn_on")
    assert len(retained) == 12
    accepted = sum(1 for r in retained if r.accepted)
    stats = m.preference({"hour": 20})["actions"]["turn_on"]
    # 聚合只反映留存明细：不回补就会显示 10/20 接受率，而留存里只有 2 条接受
    assert stats["total"] == len(retained)
    assert stats["accepted"] == accepted == 2
    assert stats["rejected"] == 10


def test_zero_sample_stat_keys_are_removed(tmp_path):
    """淘汰到 0 样本的组合必须整键摘掉，否则键本身又是一族"只增不减"。"""
    m = _pref(tmp_path, max_records=4)
    for i in range(10):
        m.record("turn_on", {"entity_id": f"light.l{i}"}, accepted=True)
    live_keys = {
        (ctx, act, pk)
        for ctx, acts in m._stats.items()
        for act, params in acts.items()
        for pk in params
    }
    derived = {(r.context, r.action, m._freeze_params(r.params)) for r in m.records_for()}
    assert live_keys == derived
    assert len(live_keys) == 4


# ── 写放大：每条只追加一行 ────────────────────────────────────────────

@pytest.fixture()
def write_spy(monkeypatch):
    """统计整档重写的字节量（追加行走 append_jsonl，不计入重写）。"""
    calls: dict[str, list[int]] = {"append": [], "rewrite": []}
    real_append = af_preference.append_jsonl
    real_rewrite = af_preference.atomic_write_text

    def spy_append(path, obj):
        calls["append"].append(len(json.dumps(obj)))
        return real_append(path, obj)

    def spy_rewrite(path, text):
        calls["rewrite"].append(len(text))
        return real_rewrite(path, text)

    monkeypatch.setattr(af_preference, "append_jsonl", spy_append)
    monkeypatch.setattr(af_preference, "atomic_write_text", spy_rewrite)
    return calls


def test_record_writes_one_line_not_the_whole_store(tmp_path, write_spy):
    m = _pref(tmp_path, max_records=5000)
    for i in range(300):
        m.record("turn_on", {"entity_id": "light.a", "brightness": i % 100}, accepted=True)

    assert len(write_spy["append"]) == 300
    # slack = max(64, 5000 // 8) = 625 → 300 条内一次整档重写都不该发生
    assert write_spy["rewrite"] == []
    total_bytes = sum(write_spy["append"])
    # 尺子：300 条 × ~120 字节/行；旧实现每条重写整档 → MB 级
    assert total_bytes < 300_000, total_bytes


def test_guard_reports_red_when_full_rewrite_is_reintroduced(tmp_path, monkeypatch, write_spy):
    """铁律 #8 反例：把「每条重写整档」塞回去，同一把字节尺子必须报红。"""

    def _append_by_rewriting(self: PreferenceModel, rec) -> None:
        self._rewrite_all()

    monkeypatch.setattr(PreferenceModel, "_append_record", _append_by_rewriting)
    m = _pref(tmp_path, max_records=5000)
    for _ in range(300):
        m.record("turn_on", {"entity_id": "light.a"}, accepted=True)
    assert write_spy["append"] == []
    assert len(write_spy["rewrite"]) == 300
    assert sum(write_spy["rewrite"]) > 300_000  # test_record_writes_one_line_not_the_whole_store 的阈值在此情形下会被击穿


def test_compaction_happens_once_per_slack(tmp_path, write_spy):
    m = _pref(tmp_path, max_records=200)  # slack = max(64, 25) = 64
    for i in range(200):
        m.record("turn_on", {"entity_id": "light.a"}, accepted=True)
    # 200 条最多 4 次整档压缩，而不是 200 次
    assert len(write_spy["rewrite"]) <= 4, len(write_spy["rewrite"])
    path = os.path.join(str(tmp_path), PREFERENCES_FILE)
    assert len(_rows(path)) <= 200


# ── 持久化格式与迁移 ──────────────────────────────────────────────────

def test_reload_roundtrip_keeps_aggregates(tmp_path):
    m = _pref(tmp_path, min_samples=2)
    for _ in range(3):
        m.record_accept("turn_on", {"entity_id": "light.a", "brightness": 80},
                        context={"hour": 20, "weekday": 5}, automation_id="auto1")
    reloaded = _pref(tmp_path, min_samples=2)
    assert reloaded.stats()["total_records"] == 3
    prefs = reloaded.preference({"hour": 20, "weekday": 5})
    assert prefs["best_action"] == "turn_on"
    assert prefs["actions"]["turn_on"]["total"] == 3
    assert len(reloaded.suggest("auto1")) == len(m.suggest("auto1"))


def test_legacy_full_store_is_migrated_and_left_intact(tmp_path):
    legacy = {
        "records": [
            {
                "record_id": "r1", "automation_id": "auto1", "action": "turn_on",
                "params": {"entity_id": "light.a"},
                "context": "time=morning|weekday=*|scene=*|entity=*",
                "accepted": True, "timestamp": 1700000000.0, "details": {},
            }
        ],
        "min_samples": 7,
        "confidence_threshold": 0.9,
    }
    legacy_path = os.path.join(str(tmp_path), LEGACY_PREFERENCES_FILE)
    with open(legacy_path, "w", encoding="utf-8") as f:
        json.dump(legacy, f, indent=2)
    legacy_bytes = open(legacy_path, "rb").read()

    m = _pref(tmp_path)
    assert m.stats()["total_records"] == 1
    assert m._min_samples == 7
    assert m._confidence_threshold == 0.9
    # 迁移只读：旧文件原封不动，新格式另建
    assert open(legacy_path, "rb").read() == legacy_bytes
    new_path = os.path.join(str(tmp_path), PREFERENCES_FILE)
    assert os.path.exists(new_path)
    m.record_accept("turn_on", {"entity_id": "light.a"}, context={"hour": 8})
    assert len(_rows(new_path)) == 2


def test_corrupt_line_is_skipped_without_losing_the_rest(tmp_path):
    m = _pref(tmp_path)
    for _ in range(3):
        m.record_accept("turn_on", {"entity_id": "light.a"})
    path = os.path.join(str(tmp_path), PREFERENCES_FILE)
    with open(path, "a", encoding="utf-8") as f:
        f.write("{ 这是一行被截断的垃圾\n")
    assert _pref(tmp_path).stats()["total_records"] == 3


def test_unknown_extra_keys_are_ignored(tmp_path):
    m = _pref(tmp_path)
    m.record_accept("turn_on", {"entity_id": "light.a"})
    path = os.path.join(str(tmp_path), PREFERENCES_FILE)
    rows = _rows(path)
    rows[0]["from_the_future"] = True
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    assert _pref(tmp_path).stats()["total_records"] == 1


def test_in_memory_model_writes_nothing(tmp_path):
    m = PreferenceModel(persist_dir=None, max_records=3)
    for _ in range(5):
        m.record_accept("turn_on", {"entity_id": "light.a"})
    assert m.stats()["total_records"] == 3
    assert os.listdir(str(tmp_path)) == []


def test_clear_rewrites_the_store(tmp_path):
    m = _pref(tmp_path, max_records=100)
    for i in range(20):
        m.record_accept("turn_on", {"entity_id": "light.a"}, automation_id="auto1" if i % 2 else "auto2")
    path = os.path.join(str(tmp_path), PREFERENCES_FILE)
    assert m.clear("auto1") == 10
    assert len(_rows(path)) == 10
    assert m.stats()["total_records"] == 10
    assert m.clear() == 10
    assert _rows(path) == []


# ── EventBus：实体维度缓存不再只增不减 ───────────────────────────────

def _bus(**kw) -> EventBus:
    clock = VirtualTimeSource(datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc))
    return EventBus(clock, **kw)


def _event(entity: str, state: str = "on", last_changed: str = "t1") -> BusEvent:
    return BusEvent.of(entity, state, last_changed=last_changed)


def test_retired_entity_keys_are_pruned():
    bus = _bus()
    for i in range(200):
        bus.publish(_event(f"light.old{i}", last_changed=f"o{i}"))
    assert bus.stats()["entity_cache"]["last_accepted"] == 200

    bus.clock.advance(70.0)  # 跨过修剪周期与节流窗口
    for i in range(200):
        bus.publish(_event(f"light.new{i}", last_changed=f"n{i}"))
    cache = bus.stats()["entity_cache"]
    # 回归（第五轮审计实测）：旧键全部残留 → 200→400
    assert cache["last_accepted"] == 200, cache
    assert cache["last_state"] == 200, cache
    assert cache["pruned_total"] >= 200, cache
    assert not any(k.startswith("light.old") for k in bus._last_state)
    assert not any(k.startswith("light.old") for k in bus._changes)


def test_change_windows_for_silent_entities_are_pruned():
    bus = _bus(breaker_window_s=10, breaker_threshold=12)
    for i in range(50):
        bus.publish(_event(f"binary_sensor.s{i}", last_changed=f"s{i}"))
    assert len(bus._changes) == 50
    bus.clock.advance(70.0)
    bus.publish(_event("light.keep", last_changed="k1"))
    assert len(bus._changes) == 1
    assert "light.keep" in bus._changes


def test_pruning_does_not_change_throttle_or_breaker_verdicts():
    bus = _bus(breaker_window_s=10, breaker_threshold=5, throttle_ms=200)
    # 修剪只针对"已超过节流窗口"的实体 —— 那种情况下本来必然放行
    bus.publish(_event("light.a", last_changed="t1"))
    bus.clock.advance(70.0)
    bus._prune_stale_caches(bus.clock.monotonic())
    assert bus.publish(_event("light.a", state="off", last_changed="t2")) == ACCEPTED
    # 熔断不受修剪影响：同一实体窗口内高频变更仍然开路
    for i in range(6):
        bus.clock.advance(0.25)
        bus.publish(_event("light.a", state="on" if i % 2 else "off", last_changed=f"b{i}"))
    assert bus.publish(_event("light.a", state="off", last_changed="b9")) == BREAKER_BLOCKED


def test_open_breaker_key_survives_pruning():
    """冷却中的熔断键不修剪：`_maybe_recover` 要靠它记 BREAKER_RECOVER 审计。"""
    bus = _bus(breaker_window_s=10, breaker_threshold=5, breaker_cooldown_s=30)
    for i in range(6):
        bus.clock.advance(0.25)
        bus.publish(_event("light.a", state="on" if i % 2 else "off", last_changed=f"b{i}"))
    assert bus.is_open("light.a")
    assert bus.audit.of_type(BREAKER_OPEN)

    bus.clock.advance(70.0)
    bus._prune_stale_caches(bus.clock.monotonic())
    assert "light.a" in bus._open_until
    # 该实体再次发布 → 正常走恢复路径并留下审计
    bus.publish(_event("light.a", state="off", last_changed="later"))
    assert bus.audit.of_type(BREAKER_RECOVER)
    assert not bus.is_open("light.a")
