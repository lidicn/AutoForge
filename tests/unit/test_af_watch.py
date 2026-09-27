"""af_watch 聚合层单测（v2.1 F4 初稿）。"""
from autoforge import af_watch
from autoforge.af_watch import WatchAggregator


def setup_function(_):
    af_watch.reset()


def test_aggregator_partitions_by_kind_and_status():
    agg = WatchAggregator()
    agg.record("shadow", "a1", "verified", 100.0, {"x": 1})
    agg.record("shadow", "a1", "verified", 200.0)
    agg.record("shadow", "a1", "failed", 150.0)
    agg.record("canary", "a1", "verified", 120.0)
    agg.record("conflict", "a2", "conflict", 130.0)

    part = agg.verified_in_prod()
    autos = {a["automation_id"]: a for a in part["automations"]}
    assert autos["a1"]["verified_in_prod"] == 3           # 2 shadow verified + 1 canary verified
    assert autos["a1"]["shadow"] == 3
    assert autos["a1"]["canary"] == 1
    assert autos["a1"]["last_verified_at"] == 200.0       # 最近一次 verified
    assert autos["a2"]["conflict"] == 1
    assert part["summary"]["total_verified_in_prod"] == 3
    assert part["summary"]["total_conflict"] == 1


def test_empty_partition_is_list_not_placeholder():
    agg = WatchAggregator()
    part = agg.verified_in_prod()
    assert isinstance(part["automations"], list)
    assert part["automations"] == []
    assert part["summary"]["total_verified_in_prod"] == 0


def test_record_shadow_feeds_default_aggregator():
    af_watch.reset()
    af_watch.record_shadow("a9", "verified", 999.0)
    part = af_watch.verified_in_prod_partition()
    assert part["summary"]["total_verified_in_prod"] == 1
    assert part["automations"][0]["automation_id"] == "a9"
