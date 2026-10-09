"""联动入向队列 `af_linkage_feed`（计划 §七 卡3 的落盘半边）。

这里锁的四件事，都是"读代码看不出来、出事要靠现场对账"的那类：

- 满/超龄时**裁最旧**且**每类各自封顶**（共享上限=一条线刷得多就把另一条线挤哑）；
- 水位线跨目录可排序（键若是 `{kind}/{文件名}`，后来那条设备健康会被当成已消费而**静默不触发**）；
- 超龄条目只推进水位线、不补触发（"重启不丢"说的是记录，不是把旧掉线快照当成当下事实）；
- 纯写不读也必须被回收（判据 E：回收腿挂在 `append` 上，不挂在读侧）。
"""
from __future__ import annotations

import json
from pathlib import Path

from autoforge.af_linkage_feed import (
    DEFAULT_LIMIT,
    KIND_DEVICE_HEALTH,
    KIND_PRESENCE,
    MEMBERS_LIMIT,
    TRIGGER_MAX_AGE_S,
    TRIGGER_NAME,
    TTL_S,
    UNREADABLE_MAX,
    LinkageFeed,
    LinkageRecord,
    device_data_of,
    members_of,
)


class _Clock:
    def __init__(self, t: float = 1.0) -> None:
        self.t = t

    def now(self) -> float:
        return self.t


def _record(kind: str = KIND_PRESENCE, *, t: float = 1.0, event_id: str = "evt1", **kw) -> LinkageRecord:
    return LinkageRecord(
        event_id=event_id,
        kind=kind,
        topic="ma/presence" if kind == KIND_PRESENCE else "ma/device-health",
        trace_id=kw.pop("trace_id", f"trace-{event_id}"),
        received_at=t,
        subject=kw.pop("subject", "客厅"),
        data=kw.pop("data", {"members": [{"member_id": "dad"}]}),
    )


def _feed(tmp_path: Path, clock: _Clock | None = None, **kw) -> LinkageFeed:
    return LinkageFeed(tmp_path / "linkage_events", clock=clock or _Clock(), **kw)


# ── 落盘形状与读回 ────────────────────────────────────────────────────
def test_append_lands_one_file_per_kind_and_reads_back(tmp_path):
    clock = _Clock()
    feed = _feed(tmp_path, clock)
    feed.append(_record(t=1.0))
    feed.append(_record(KIND_DEVICE_HEALTH, t=2.0, event_id="evt2", data={"device_id": "lamp"}))

    assert sorted(p.name for p in (feed.root / KIND_PRESENCE).glob("*.json")) == ["0000000001000-evt1.json"]
    assert sorted(p.name for p in (feed.root / KIND_DEVICE_HEALTH).glob("*.json")) == ["0000000002000-evt2.json"]
    assert feed.stats()["per_kind"] == {KIND_PRESENCE: 1, KIND_DEVICE_HEALTH: 1}

    back = feed.list_recent()
    assert [r.kind for r in back] == [KIND_DEVICE_HEALTH, KIND_PRESENCE]   # 新的在前
    assert back[1].trace_id == "trace-evt1" and back[1].data == {"members": [{"member_id": "dad"}]}


def test_restart_does_not_lose_records(tmp_path):
    """重启不丢：换一个 feed 实例指向同一目录，记录还在（水位线是新进程的，从空开始）。"""
    first = _feed(tmp_path, _Clock())
    first.append(_record())
    assert len(LinkageFeed(tmp_path / "linkage_events", clock=_Clock()).list_recent()) == 1


def test_trigger_projection_uses_existing_event_names():
    record = _record(KIND_DEVICE_HEALTH, data={"device_id": "lamp", "status": "unavailable"})
    assert record.trigger_name == "ma_device_health"
    data = record.as_trigger_data()
    assert data["topic"] == "ma/device-health" and data["device_id"] == "lamp"
    assert data["trace_id"] == "trace-evt1"
    assert set(TRIGGER_NAME) == {KIND_PRESENCE, KIND_DEVICE_HEALTH}


def test_unregistered_kind_projects_no_trigger_name():
    """没登记事件名的一类**不猜**名字：消费侧据此跳过，调度器不会收到一个凭空造的 `event.x`。"""
    record = _record(t=1.0)
    object.__setattr__(record, "kind", "something_new")
    assert record.trigger_name == ""


# ── 回收：每类各自封顶 + TTL ──────────────────────────────────────────
def test_cap_is_per_kind_and_evicts_oldest_without_any_read(tmp_path):
    """判据 E：只写不读也必须回收（回收腿挂在 `append` 上，读侧一条都不参与）。"""
    feed = _feed(tmp_path, _Clock(), limit=3)
    for i in range(6):
        feed.append(_record(t=1.0 + i, event_id=f"p{i}"))

    names = [p.name for p in sorted((feed.root / KIND_PRESENCE).glob("*.json"))]
    assert len(names) == 3 and names[0].endswith("-p3.json")        # 最旧的三条已被裁掉
    assert feed.stats()["per_kind"] == {KIND_PRESENCE: 3, KIND_DEVICE_HEALTH: 0}


def test_default_limit_is_not_shared_across_kinds(tmp_path):
    """把上限读成"两类合计"是这条队列最容易被改错的一处：合计=一条线刷得多就把另一条挤哑。"""
    feed = _feed(tmp_path, _Clock(), limit=2)
    assert LinkageFeed(tmp_path / "other").limit == DEFAULT_LIMIT    # 缺省档仍是 500
    for i in range(4):
        feed.append(_record(t=1.0 + i, event_id=f"p{i}"))
        feed.append(_record(KIND_DEVICE_HEALTH, t=5.0 + i, event_id=f"d{i}"))
    assert feed.stats()["per_kind"] == {KIND_PRESENCE: 2, KIND_DEVICE_HEALTH: 2}


def test_ttl_drops_over_age_records(tmp_path):
    clock = _Clock(1.0)
    feed = _feed(tmp_path, clock)
    feed.append(_record(event_id="old"))
    clock.t = 1.0 + TTL_S + 1.0
    feed.append(_record(t=clock.t, event_id="new"))

    assert [p.name for p in (feed.root / KIND_PRESENCE).glob("*.json")] == [f"{int(clock.t * 1000):013d}-new.json"]


def test_unparsable_filename_is_not_deleted(tmp_path):
    """文件名没有毫秒前缀的文件不属于本队列，回收腿不许把它删了。"""
    feed = _feed(tmp_path, _Clock())
    foreign = feed.root / KIND_PRESENCE / "not-mine.json"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("{}", encoding="utf-8")
    feed.append(_record())
    assert foreign.exists()


# ── 水位线：跨目录排序 + 超龄只留档 ────────────────────────────────────
def test_watermark_orders_across_kind_directories(tmp_path):
    """这条是本次改动里最容易写错、也最静默的一处：

    旧在场 → 消费 → 再收一条**更晚**的设备健康。按 `{kind}/{文件名}` 排时后者键更小
    （`d` < `p`），会被水位线吃掉，于是"设备掉线"永远不触发。
    """
    clock = _Clock(1.0)
    feed = _feed(tmp_path, clock)
    feed.append(_record(event_id="p1"))
    assert [r.event_id for r in feed.poll_new()] == ["p1"]

    clock.t = 2.0
    feed.append(_record(KIND_DEVICE_HEALTH, t=clock.t, event_id="d1", data={"device_id": "lamp"}))
    fresh = feed.poll_new()
    assert [r.event_id for r in fresh] == ["d1"], fresh


def test_poll_advances_watermark_and_consumes_once(tmp_path):
    feed = _feed(tmp_path, _Clock())
    feed.append(_record())
    assert len(feed.poll_new()) == 1
    assert feed.poll_new() == []
    assert feed.stats()["watermark"].startswith("0000000001000|")


def test_over_age_record_is_archived_not_replayed(tmp_path):
    """重启不回放：超龄条目推进水位线（所以不会在下一个 tick 又被翻出来）但不返回。"""
    feed = _feed(tmp_path, _Clock(1.0))
    feed.append(_record(event_id="stale"))
    feed.clock.t = 1.0 + TRIGGER_MAX_AGE_S + 60.0
    assert feed.poll_new() == []
    assert feed.stats()["watermark"] != ""
    assert len(feed.list_recent()) == 1                     # 记录仍在，只是不再当触发

    feed.clock.t = 1.0 + TRIGGER_MAX_AGE_S + 61.0
    feed.append(_record(t=feed.clock.t, event_id="fresh"))
    assert [r.event_id for r in feed.poll_new()] == ["fresh"]


def test_unreadable_file_counts_but_does_not_block(tmp_path):
    """坏文件是一条读不出的记录，不是整条队列的故障——记账后继续扫后面的。"""
    feed = _feed(tmp_path, _Clock())
    feed.append(_record(event_id="good"))
    (feed.root / KIND_PRESENCE / "0000000000999-bad.json").write_text("{not json", encoding="utf-8")

    fresh = feed.poll_new()
    assert [r.event_id for r in fresh] == ["good"]
    assert len(feed.stats()["unreadable"]) == 1
    assert "bad.json" in feed.unreadable[0] and "JSONDecodeError" in feed.unreadable[0]


def test_unreadable_ring_is_bounded(tmp_path):
    """"只增不减"家族：诊断清单本身必须有顶。"""
    feed = _feed(tmp_path, _Clock())
    directory = feed.root / KIND_PRESENCE
    directory.mkdir(parents=True)
    for i in range(UNREADABLE_MAX + 10):
        (directory / f"{i:013d}-b{i}.json").write_text("{oops", encoding="utf-8")

    feed.poll_new()
    assert len(feed.unreadable) == UNREADABLE_MAX


# ── 载荷裁剪（契约 §1.2）──────────────────────────────────────────────
def test_members_whitelist_drops_private_keys():
    members = members_of([
        {
            "name": "爸爸",
            "member_id": "dad",
            "room": "书房",
            "via": "vlm",
            "via_raw": "一整段内部推理原文",
            "confidence": 0.8,
            "last_seen": "2026-10-09T09:00:00+08:00",
            "trigger": True,
            "ma_private": {"prompt": "…"},
        }
    ])
    assert members == [{
        "name": "爸爸", "member_id": "dad", "room": "书房", "via": "vlm",
        "confidence": 0.8, "last_seen": "2026-10-09T09:00:00+08:00", "trigger": True,
    }]


def test_members_of_tolerates_garbage_and_caps_length():
    assert members_of("dad") == [] and members_of(None) == [] and members_of([None, 3, {"member_id": "x"}]) == [{"member_id": "x"}]
    assert len(members_of([{"member_id": f"m{i}"} for i in range(MEMBERS_LIMIT + 20)])) == MEMBERS_LIMIT


def test_device_data_carries_contract_keys_only():
    data = device_data_of({
        "device_id": "light.desk", "status": "unavailable", "entity_id": "sensor.plug",
        "from": "on", "to": "unavailable", "stable_id": "dh-1", "ts": 123, "raw_snapshot_url": "http://x",
    })
    assert data == {
        "device_id": "light.desk", "status": "unavailable", "entity_id": "sensor.plug",
        "from_state": "on", "to": "unavailable", "stable_id": "dh-1",
    }


def test_disk_shape_is_the_record_itself(tmp_path):
    """盘上那份是别人（面板、事后查账）会读的那份：字段表必须就是 `LinkageRecord`，不多不少。

    白名单生效在裁剪侧（`members_of`），这里锁的是"裁剪结果原样落盘、读回不漂移"——
    队列不参与载荷加工，才不会变成"第二个写者"。
    """
    feed = _feed(tmp_path, _Clock())
    feed.append(_record(data={"members": members_of([{"member_id": "dad", "via_raw": "内部原文"}])}))
    on_disk = json.loads((feed.root / KIND_PRESENCE / "0000000001000-evt1.json").read_text(encoding="utf-8"))
    assert on_disk == _record(data={"members": [{"member_id": "dad"}]}).to_dict()
    assert "via_raw" not in json.dumps(on_disk)
