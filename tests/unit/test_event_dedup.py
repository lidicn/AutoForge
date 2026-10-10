"""SSE 事件去重单测 — 验证 mimo 增量的 epoch:ha_event_id 去重。"""
import json

import pytest

from autoforge.af_live import iter_sse_blocks, parse_ha_event, HAEventStream


def test_iter_sse_blocks_captures_id():
    """iter_sse_blocks 现在返回 (event_type, data, sse_id) 三元组。"""
    sse = (
        "id: 12345\n"
        "event: state_changed\n"
        'data: {"event_type":"state_changed","data":{"entity_id":"light.x","new_state":{"state":"on"}}}\n'
        "\n"
        "id: 12346\n"
        "event: state_changed\n"
        'data: {"event_type":"state_changed","data":{"entity_id":"light.y","new_state":{"state":"off"}}}\n'
        "\n"
    )
    blocks = list(iter_sse_blocks(sse.splitlines(keepends=True)))
    assert len(blocks) == 2
    assert blocks[0][2] == "12345"  # sse_id
    assert blocks[1][2] == "12346"


def test_parse_ha_event_stores_ha_event_id():
    """parse_ha_event 把 sse_id 存进 BusEvent.payload['ha_event_id']。"""
    data = '{"event_type":"state_changed","data":{"entity_id":"light.x","new_state":{"state":"on","last_changed":"T1"}}}'
    ev = parse_ha_event("state_changed", data, sse_id="12345")
    assert ev is not None
    assert ev.payload.get("ha_event_id") == "12345"


def test_ha_event_stream_dedup_across_reconnect():
    """跨 SSE 重连后相同 ha_event_id 但 epoch 不同 → 不被去重（epoch 防回绕）。"""
    # 模拟两次连接都返回同一个 event_id=1 的事件
    call_count = [0]
    def opener(req, timeout=None):
        call_count[0] += 1
        if call_count[0] == 1:
            # 第一次连接：返回事件 id=1，然后流结束
            return iter([
                b"id: 1\n",
                b"event: state_changed\n",
                b'data: {"event_type":"state_changed","data":{"entity_id":"light.x","new_state":{"state":"on","last_changed":"T1"}}}\n',
                b"\n",
            ])
        else:
            # 第二次连接：返回同一个事件 id=1（HA 重连后 event_id 回绕），然后流结束
            return iter([
                b"id: 1\n",
                b"event: state_changed\n",
                b'data: {"event_type":"state_changed","data":{"entity_id":"light.x","new_state":{"state":"on","last_changed":"T1"}}}\n',
                b"\n",
            ])

    stream = HAEventStream(
        base_url="http://ha:8123", token="t", opener=opener,
        max_retries=1, backoff_s=0,
    )
    evs = list(stream.events())
    # 两次连接各产出一个事件（epoch 不同，不被去重）
    assert len(evs) == 2
    assert evs[0].entity_id == "light.x"
    assert evs[1].entity_id == "light.x"


def test_ha_event_stream_dedup_same_epoch():
    """同一连接内相同 ha_event_id 重复出现 → 被去重。"""
    def opener(req, timeout=None):
        # 同一连接内返回两个相同 id=1 的事件
        return iter([
            b"id: 1\n",
            b"event: state_changed\n",
            b'data: {"event_type":"state_changed","data":{"entity_id":"light.x","new_state":{"state":"on","last_changed":"T1"}}}\n',
            b"\n",
            b"id: 1\n",  # 重复
            b"event: state_changed\n",
            b'data: {"event_type":"state_changed","data":{"entity_id":"light.x","new_state":{"state":"on","last_changed":"T1"}}}\n',
            b"\n",
            b"id: 2\n",
            b"event: state_changed\n",
            b'data: {"event_type":"state_changed","data":{"entity_id":"light.y","new_state":{"state":"off","last_changed":"T2"}}}\n',
            b"\n",
        ])

    stream = HAEventStream(
        base_url="http://ha:8123", token="t", opener=opener,
        max_retries=0, backoff_s=0,
    )
    evs = list(stream.events())
    # id=1 重复被过滤，只剩 id=1 和 id=2
    assert len(evs) == 2
    assert evs[0].entity_id == "light.x"
    assert evs[1].entity_id == "light.y"


def test_dedup_table_evicts_least_recently_seen_not_first_seen():
    """BUG-13：容量淘汰过去是"按首次插入顺序砍前一半"，命中不重排——注释里的 LRU 是假的。

    同一个 id 在重连窗口里最容易被再送一次，所以它必须活得最久；而真正该被淘汰的旧 id
    要确实被丢掉，否则这条上限只是名义上的（表会一直肥下去）。两头都要量到。
    """
    ids = (
        ["hot"]
        + [f"c{i}" for i in range(1, 4096)]  # 填到 4096 枚，尚未触发淘汰
        + ["hot"]                              # 命中 → 挪到队尾（旧形状留在队首）
        + ["c4096"]                            # 第 4097 枚 → 淘汰队首 2048 枚
        + ["hot"]                              # 再命中：真 LRU 下仍被去重
        + ["c1"]                               # 已被淘汰 → 这一次应当放行
    )

    def opener(req, timeout=None):
        blocks = []
        for n, ev_id in enumerate(ids):
            data = json.dumps(
                {
                    "event_type": "state_changed",
                    "data": {
                        "entity_id": f"light.{n}",
                        "new_state": {"state": "on", "last_changed": f"T{n}"},
                    },
                }
            )
            blocks += [
                f"id: {ev_id}\n".encode(),
                b"event: state_changed\n",
                f"data: {data}\n".encode(),
                b"\n",
            ]
        return iter(blocks)

    stream = HAEventStream(
        base_url="http://ha:8123", token="t", opener=opener, max_retries=0, backoff_s=0
    )
    seen = [ev.payload.get("ha_event_id") for ev in stream.events()]

    assert seen.count("hot") == 1, "命中过三次的 id 被半量淘汰带走 ⇒ 淘汰看的还是首次顺序"
    assert seen.count("c1") == 2, "队首旧 id 没被淘汰 ⇒ 上限形同虚设，表会无限肥"
    assert len(seen) == len(ids) - 2, (len(seen), len(ids))


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
