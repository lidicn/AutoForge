"""F4 收敛守护：生产态验证证据必须**可见**（verified / failed / unmodeled 三档），
且聚合器自身的键空间不许只增不减。

对应三条真实缺陷：
1. `af_shadow` 早就在喂 `status="failed"`，但分区只统计 verified ⇒ 「生产验过 10 次全 MISS」
   与「一次没跑过」读数相同（铁律 #5 假安心）。
2. canary / conflict 的 `record_*` 在全仓**没有任何消费点**。
3. 动作没有 SERVICE_STATE 映射时 `has_drift()` 恒 False ⇒ 被当成"验过了"。
"""

from __future__ import annotations

import pytest

from autoforge import af_watch
from autoforge.af_audit import ENTITY_DRIFT
from autoforge.af_bus import BusEvent
from autoforge.af_ir import load_graph
from autoforge.af_runtime import build_runtime
from autoforge.af_state import InMemoryStateProvider
from autoforge.af_vhass.fake import SERVICE_STATE


def _graph(action="light.turn_on", canary=None, confidence=0.9):
    return load_graph(
        {
            "ir_version": "0.2.1",
            "id": "drive",
            "name": "drive",
            "version": 1,
            "mode": "single",
            "confidence": confidence,
            "nodes": [
                {"id": "o", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
                {
                    "id": "d",
                    "kind": "do",
                    "adapter": "mock",   # 只记意图、不改状态：漂移与否完全由预置状态决定
                    "action": action,
                    "params": {"entity_id": "light.x"},
                    "canary": canary if canary is not None else {"auto_rollback": True},
                },
                {"id": "e", "kind": "pass"},
                {"id": "err", "kind": "pass"},
            ],
            "edges": [
                {"from": "o", "to": "d", "kind": "then"},
                {"from": "d", "to": "e", "kind": "then"},
                {"from": "d", "to": "err", "kind": "on_error"},
            ],
        }
    )


def _runtime(graph, initial_states):
    states = InMemoryStateProvider()
    for entity, state in initial_states.items():
        states.set_state(entity, state)
    return build_runtime(graph, states=states)


def _partition_of(automation_id="drive"):
    part = af_watch.verified_in_prod_partition()
    for row in part["automations"]:
        if row["automation_id"] == automation_id:
            return row
    raise AssertionError(f"分区里没有 {automation_id}：{part['automations']}")


@pytest.fixture(autouse=True)
def _clean_watch():
    af_watch.reset()
    yield
    af_watch.reset()


# ── 执行器：canary 三种结局各归各档 ─────────────────────────────────────


def test_canary_drift_feeds_failed_not_verified():
    runtime = _runtime(_graph(), {"binary_sensor.m": "off", "light.x": "off"})
    runtime.publish(BusEvent.of("binary_sensor.m", "on"))

    row = _partition_of()
    assert row["canary"] == 1
    assert row["failed_in_prod"] == 1
    assert row["verified_in_prod"] == 0          # 漂移回滚过，绝不是"验过了"
    assert row["last_failed_at"] is not None
    assert any(ev.type == ENTITY_DRIFT for ev in runtime.audit)


def test_canary_clean_feeds_verified():
    # 预置成预期态 → MockAdapter 不改状态也不构成漂移 = 干净通过
    runtime = _runtime(_graph(), {"binary_sensor.m": "off", "light.x": "on"})
    runtime.publish(BusEvent.of("binary_sensor.m", "on"))

    row = _partition_of()
    assert row["verified_in_prod"] == 1
    assert row["failed_in_prod"] == 0
    assert row["unmodeled_in_prod"] == 0


def test_canary_without_state_mapping_is_unmodeled_not_verified():
    """铁律 #5 反例锁：`expected_state()` 为 None ⇒ `has_drift()` 恒 False。

    修复前这条走"干净通过"，白拿一条 verified 证据；现在单列 unmodeled，
    且 verified 必须为 0——否则本守护变红。
    """
    graph = _graph(action="notify.send_message")
    # 前提要钉住：该动作确实没有 SERVICE_STATE 映射，否则本守护测的不是这条路径
    assert ("notify", "send_message") not in SERVICE_STATE
    runtime = _runtime(graph, {"binary_sensor.m": "off", "light.x": "off"})
    runtime.publish(BusEvent.of("binary_sensor.m", "on"))

    row = _partition_of()
    assert row["unmodeled_in_prod"] == 1
    assert row["verified_in_prod"] == 0
    assert row["failed_in_prod"] == 0


def test_canary_observe_window_drift_feeds_failed():
    """挂起观察期（duration）分支：漂移证据同样要落到分区，不能只在审计里。"""
    graph = _graph(canary={"duration": "15m", "auto_rollback": True})
    runtime = _runtime(graph, {"binary_sensor.m": "off", "light.x": "off"})
    runtime.publish(BusEvent.of("binary_sensor.m", "on"))      # 挂起
    assert af_watch.verified_in_prod_partition()["automations"] == []   # 未出结论前不落证据
    runtime.advance(15 * 60 + 1)

    row = _partition_of()
    assert row["failed_in_prod"] == 1
    assert row["verified_in_prod"] == 0


def test_canary_observe_window_clean_feeds_verified():
    graph = _graph(canary={"duration": "15m", "auto_rollback": True})
    runtime = _runtime(graph, {"binary_sensor.m": "off", "light.x": "on"})
    runtime.publish(BusEvent.of("binary_sensor.m", "on"))
    runtime.advance(15 * 60 + 1)

    row = _partition_of()
    assert row["verified_in_prod"] == 1
    assert row["failed_in_prod"] == 0


def test_watch_feed_failure_does_not_break_dispatch(caplog):
    """聚合器抛错不得影响下发，但必须留痕——静默咽下等于"一条没记"看起来像"记好了"。"""

    def boom(*args, **kwargs):
        raise RuntimeError("聚合器炸了")

    monkey = pytest.MonkeyPatch()
    monkey.setattr(af_watch, "record_canary", boom)
    try:
        runtime = _runtime(_graph(), {"binary_sensor.m": "off", "light.x": "on"})
        with caplog.at_level("WARNING", logger="autoforge.executor"):
            runtime.publish(BusEvent.of("binary_sensor.m", "on"))
    finally:
        monkey.undo()

    assert runtime.adapters.get("mock").calls, "适配器仍应收到下发"
    assert any("af_watch" in rec.message for rec in caplog.records)
    assert af_watch.verified_in_prod_partition()["automations"] == []


# ── 聚合器：失败与淘汰都要可见 ───────────────────────────────────────────


def test_summary_counts_failed_and_flags_automations_with_failed():
    agg = af_watch.WatchAggregator()
    agg.record("shadow", "a1", "verified", 100.0)
    agg.record("shadow", "a1", "failed", 110.0)
    agg.record("canary", "a2", "failed", 120.0)
    agg.record("canary", "a3", "unmodeled", 130.0)

    part = agg.verified_in_prod()
    s = part["summary"]
    assert s["total_verified_in_prod"] == 1
    assert s["total_failed_in_prod"] == 2
    assert s["total_unmodeled_in_prod"] == 1
    assert s["automations_with_failed"] == 2
    assert s["tracked_automations"] == 3


def test_failed_only_automation_is_not_empty_in_the_partition():
    """旧版行为：只有失败证据的自动化在分区里只剩 kind 计数，看不出"验出过问题"。"""
    agg = af_watch.WatchAggregator()
    agg.record("shadow", "a1", "failed", 100.0)
    row = agg.verified_in_prod()["automations"][0]
    assert row["verified_in_prod"] == 0
    assert row["failed_in_prod"] == 1
    assert row["last_failed_at"] == 100.0


def test_automation_key_space_is_bounded_with_lru():
    agg = af_watch.WatchAggregator()
    monkey = pytest.MonkeyPatch()
    monkey.setattr(type(agg), "MAX_AUTOMATIONS", 3, raising=False)
    try:
        for i in range(1, 6):                        # a1..a5，各一条
            agg.record("shadow", f"a{i}", "verified", float(i))
        rows = agg.verified_in_prod()
        assert rows["summary"]["tracked_automations"] == 3
        assert rows["summary"]["evicted_automations"] == 2
        assert {a["automation_id"] for a in rows["automations"]} == {"a3", "a4", "a5"}

        agg.record("shadow", "a1", "verified", 9.0)  # 老的被重新点亮 → 挤掉最久没动的 a3
        rows = agg.verified_in_prod()
        assert {a["automation_id"] for a in rows["automations"]} == {"a1", "a4", "a5"}
        assert rows["summary"]["evicted_automations"] == 3
    finally:
        monkey.undo()


def test_eviction_can_only_shrink_claimed_evidence():
    """淘汰的方向性：丢证据可以，绝不能凭空造 verified 计数。"""
    agg = af_watch.WatchAggregator()
    monkey = pytest.MonkeyPatch()
    monkey.setattr(type(agg), "MAX_AUTOMATIONS", 2, raising=False)
    try:
        for i in range(1, 8):
            agg.record("shadow", f"a{i}", "verified", float(i))
        rows = agg.verified_in_prod()
        assert rows["summary"]["evicted_automations"] == 5   # 7 个 id、上限 2 ⇒ 摘掉 5 个
        assert all(a["verified_in_prod"] == 1 for a in rows["automations"])
        assert rows["summary"]["total_verified_in_prod"] == len(rows["automations"])
    finally:
        monkey.undo()


def test_events_per_automation_stay_bounded():
    agg = af_watch.WatchAggregator()
    monkey = pytest.MonkeyPatch()
    monkey.setattr(type(agg), "MAX_EVENTS_PER_AUTO", 10, raising=False)
    try:
        for i in range(50):
            agg.record("shadow", "a1", "verified", float(i))
        row = agg.verified_in_prod()["automations"][0]
        assert row["shadow"] == 10                   # 只留最近 10 条
    finally:
        monkey.undo()


def test_mixed_time_axes_are_normalized_before_max():
    """shadow 传 epoch 秒、executor/仲裁传 datetime（AuditEvent.at 就是 datetime）。

    两条时间轴混在一栏里 `max()`：要么 TypeError，要么把"最近一次"排成乱序
    （1.7e9 vs 1e-3 级 monotonic）。收口在 `WatchAggregator.ingest`。
    """
    from datetime import datetime, timezone

    agg = af_watch.WatchAggregator()
    later = datetime(2026, 9, 15, tzinfo=timezone.utc)
    agg.record("shadow", "a1", "verified", 1_700_000_000.0)
    agg.record("canary", "a1", "verified", later)
    row = agg.verified_in_prod()["automations"][0]
    assert isinstance(row["last_verified_at"], float)
    assert row["last_verified_at"] == later.timestamp()


def test_naive_pre_epoch_datetime_does_not_raise_on_windows():
    """本轮实测踩到的平台坑：朴素 datetime 的 `.timestamp()` 走 `mktime`，
    UTC+8 下 1970-01-01 01:16 换算是负 epoch → Windows 抛 `OSError(22, Invalid argument)`，
    冲突证据被异常隔离咽掉（有日志、但一条都没记）。按 UTC 解释后必须落库。
    """
    from datetime import datetime, timezone

    agg = af_watch.WatchAggregator()
    naive = datetime(1970, 1, 1, 0, 16, 40)          # 无 tzinfo，且本地换算会到 1969
    agg.record("conflict", "a1", "conflict", naive)
    row = agg.verified_in_prod()["automations"][0]
    assert row["conflict"] == 1
    assert row["last_failed_at"] is None
    assert naive.replace(tzinfo=timezone.utc).timestamp() >= 0


def test_reset_clears_eviction_counter():
    agg = af_watch.WatchAggregator()
    agg._evicted_automations = 7
    af_watch.reset()
    assert af_watch.verified_in_prod_partition()["summary"]["evicted_automations"] == 0


# ── HTTP 只读面（监护视图的数据源）───────────────────────────────────────


def _client(tmp_path, **kw):
    from fastapi.testclient import TestClient

    from autoforge.af_api import build_app

    return TestClient(build_app(store_root=str(tmp_path), **kw))


@pytest.fixture()
def noauth(monkeypatch):
    """鉴权逃生舱：让这两条测的是证据面本身，而不是 403。"""
    monkeypatch.setenv("AF_ALLOW_NOAUTH", "1")


def test_evidence_endpoint_reports_all_three_tiers(tmp_path, noauth):
    af_watch.record_shadow("a1", "verified", 100.0)
    af_watch.record_shadow("a1", "failed", 110.0)
    af_watch.record_canary("a2", "unmodeled", 120.0)

    body = _client(tmp_path).get("/api/evidence/prod").json()
    rows = {r["automation_id"]: r for r in body["automations"]}
    assert rows["a1"]["verified_in_prod"] == 1 and rows["a1"]["failed_in_prod"] == 1
    assert rows["a2"]["unmodeled_in_prod"] == 1
    assert body["summary"]["total_failed_in_prod"] == 1
    # 只读盘点不许带字面量 ok（AST 门禁 fake-ok-const）
    assert "ok" not in body


def test_evidence_endpoint_still_serves_reads_when_readonly(tmp_path, noauth):
    """铁律 #6：单写者降级成只读的实例，读证据面必须照供——监护是只读活动。"""
    af_watch.record_shadow("a1", "failed", 100.0)
    resp = _client(tmp_path, readonly=True).get("/api/evidence/prod")
    assert resp.status_code == 200
    assert resp.json()["summary"]["automations_with_failed"] == 1
