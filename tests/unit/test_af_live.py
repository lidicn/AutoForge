"""真机常驻监听（af_live）单测：SSE 解析 / 事件投递 / 重连，零网络。"""

from __future__ import annotations

import threading

from autoforge.af_bus import BusEvent
from autoforge.af_live import (
    HAEventStream,
    get_tick_supervisor,
    iter_sse_blocks,
    parse_ha_event,
    run_watch,
    start_ticker,
)


# ── iter_sse_blocks ────────────────────────────────────────────────────
def test_iter_sse_blocks_parses_event_and_data():
    lines = [
        "event: state_changed",
        'data: {"entity_id":"light.x","state":"on"}',
        "",
        ": ping",  # 注释行忽略
        "event: other",
        'data: {"k":1}',
        "",
    ]
    blocks = list(iter_sse_blocks(lines))
    assert blocks == [
        ("state_changed", '{"entity_id":"light.x","state":"on"}', ""),
        ("other", '{"k":1}', ""),
    ]


def test_iter_sse_blocks_multiline_data_joined():
    lines = [
        "event: state_changed",
        'data: {"a":',
        'data: 1}',
        "",
    ]
    assert list(iter_sse_blocks(lines)) == [("state_changed", '{"a":\n1}', "")]


def test_iter_sse_blocks_missing_blank_line_flushes():
    # 流在中途结束（无尾随空行）
    assert list(iter_sse_blocks(["event: x", 'data: y'])) == [("x", "y", "")]


# ── parse_ha_event ─────────────────────────────────────────────────────
def test_parse_ha_event_state_changed():
    ev = parse_ha_event(
        "state_changed",
        '{"data":{"entity_id":"light.x","new_state":{"state":"on",'
        '"last_changed":"2026-09-14T08:00:00+00:00",'
        '"attributes":{"friendly_name":"X"}}}}',
    )
    assert isinstance(ev, BusEvent)
    assert ev.entity_id == "light.x"
    assert ev.state == "on"
    assert ev.last_changed == "2026-09-14T08:00:00+00:00"
    assert ev.payload.get("attributes") == {"friendly_name": "X"}


def test_parse_ha_event_ignores_non_state_changed():
    assert parse_ha_event("other", "{}") is None


def test_parse_ha_event_bad_json_returns_none():
    assert parse_ha_event("state_changed", "not-json") is None


def test_parse_ha_event_without_sse_event_line():
    """HA 的 `/api/stream` **不发** SSE `event:` 行，event_type 在 payload JSON 内。

    真机常驻（`forge watch`）的关键：早期实现只认 SSE `event:` 行，
    会让真实 HA 事件被整体静默丢弃（2026-09-15 真机实测修复）。
    """
    data = (
        '{"event_type":"state_changed","data":{"entity_id":"switch.x",'
        '"new_state":{"state":"on","last_changed":"t1","attributes":{}}}}'
    )
    ev = parse_ha_event("", data)
    assert isinstance(ev, BusEvent)
    assert ev.entity_id == "switch.x"
    assert ev.state == "on"


def test_parse_ha_event_ping_without_event_line_returns_none():
    """HA 心跳 `data: ping`（无 `event:` 行）不应被当成事件。"""
    assert parse_ha_event("", "ping") is None


def test_parse_ha_event_missing_new_state_returns_none():
    assert parse_ha_event("state_changed", '{"data":{"entity_id":"x"}}') is None


# ── run_watch ──────────────────────────────────────────────────────────
class _FakeRuntime:
    def __init__(self):
        self.published: list[BusEvent] = []
        self.ticks = 0

    def publish(self, ev: BusEvent) -> list:
        self.published.append(ev)
        return []

    def tick(self) -> list:
        self.ticks += 1
        return []


def _ev(entity_id: str, state: str) -> BusEvent:
    return BusEvent.of(entity_id, state, source="ha", last_changed="t")


def test_run_watch_publishes_each_event():
    rt = _FakeRuntime()
    events = [_ev("a", "1"), _ev("b", "2"), _ev("a", "3")]
    res = run_watch(rt, events, tick_each=0)
    assert [e.entity_id for e in rt.published] == ["a", "b", "a"]
    assert res["published"] == 3
    assert rt.ticks == 0


def test_run_watch_ticks_every_n():
    rt = _FakeRuntime()
    events = [_ev(f"e{i}", str(i)) for i in range(5)]
    run_watch(rt, events, tick_each=2)
    # i+1 ∈ {1,2,3,4,5}，被 2 整除的是 2,4 → 对应 i=1,3，共 2 次 tick
    assert rt.ticks == 2


def test_run_watch_stops_on_stop_event():
    rt = _FakeRuntime()
    stop = threading.Event()
    events = [_ev("a", "1"), _ev("b", "2"), _ev("c", "3")]

    # 发布 b 之后再置位停止，验证 b 已发布、c 被拦下
    def _gen():
        for e in events:
            yield e
            if e.entity_id == "b":
                stop.set()

    res = run_watch(rt, _gen(), stop=stop)
    assert [e.entity_id for e in rt.published] == ["a", "b"]
    assert res["stopped"] is True


def test_run_watch_max_events_truncates():
    rt = _FakeRuntime()
    events = [_ev(f"e{i}", str(i)) for i in range(10)]
    res = run_watch(rt, events, tick_each=0, max_events=2)
    assert res["published"] == 2


# ── HAEventStream（注入 opener，零网络）────────────────────────────────
def test_ha_event_stream_from_canned_lines():
    sse = (
        "event: state_changed\n"
        'data: {"data":{"entity_id":"light.x","new_state":{"state":"on",'
        '"last_changed":"T1"}}}\n'
        "\n"
        "event: state_changed\n"
        'data: {"data":{"entity_id":"light.y","new_state":{"state":"off",'
        '"last_changed":"T2"}}}\n'
        "\n"
    ).splitlines(keepends=True)

    calls: dict = {}

    def opener(req, timeout=None):
        calls["url"] = req.full_url
        calls["auth"] = req.headers.get("Authorization")
        return sse

    stream = HAEventStream(base_url="http://ha:8123", token="SECRET", opener=opener, max_retries=0)
    evs = list(stream.events())
    assert len(evs) == 2
    assert evs[0].entity_id == "light.x" and evs[0].state == "on"
    assert evs[1].entity_id == "light.y" and evs[1].state == "off"
    assert calls["url"].endswith("/api/stream")
    assert calls["auth"] == "Bearer SECRET"


def test_ha_event_stream_reconnects_on_error():
    # 第一次打开抛异常（模拟断流），第二次返回事件
    attempts = {"n": 0}

    def opener(req, timeout=None):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ConnectionError("boom")
        return [
            "event: state_changed\n",
            'data: {"data":{"entity_id":"light.z","new_state":{"state":"on"}}}\n',
            "\n",
        ]

    stream = HAEventStream(base_url="http://ha:8123", token="t", opener=opener, max_retries=1, backoff_s=0)
    evs = list(stream.events())
    assert len(evs) == 1
    assert evs[0].entity_id == "light.z"
    assert attempts["n"] == 2  # 失败一次后重连成功


def test_ha_event_stream_gives_up_after_max_retries():
    def opener(req, timeout=None):
        raise ConnectionError("down")

    stream = HAEventStream(base_url="http://ha:8123", token="t", opener=opener, max_retries=2, backoff_s=0)
    # 无限迭代器本应常驻，但达到 max_retries 后会结束
    evs = list(stream.events())
    assert evs == []


# ── start_ticker ───────────────────────────────────────────────────────
def test_start_ticker_calls_tick_until_stopped():
    rt = _FakeRuntime()
    stop = threading.Event()
    t = start_ticker(rt, 0.01, stop)
    # 给线程一点时间跑几轮
    import time as _t

    _t.sleep(0.05)
    stop.set()
    t.join(timeout=1.0)
    assert rt.ticks >= 1


# ── 审计 AF-第二轮 接缝回归（缺陷1 + 缺陷2）──────────────────────────────
def test_start_ticker_safe_halt_no_uncaught_exception(monkeypatch):
    """SAFE HALT 消费侧应走 break（logger 已定义），不应抛未捕获异常静默杀死线程。

    复现审计场景：tick 持续抛异常 → TickSupervisor 进入 SAFE HALT → 消费侧
    `logger.error(...)` + break。若模块级 logger 未定义（旧缺陷1），此处会抛
    NameError 并被 threading.excepthook 捕获，caught 非空 → 测试失败。
    """
    caught = []

    def _hook(args):
        caught.append(args.exc_type)

    monkeypatch.setattr(threading, "excepthook", _hook)

    class _BoomRt(_FakeRuntime):
        def tick(self):
            self.ticks += 1
            raise RuntimeError("模拟致命 tick 故障")

    rt = _BoomRt()
    stop = threading.Event()
    t = start_ticker(rt, 0.01, stop)
    t.join(timeout=3.0)
    assert not t.is_alive(), "SAFE HALT 后线程应干净退出"
    assert get_tick_supervisor().health().halted is True, "supervisor 应进入 SAFE HALT"
    stop.set()
    assert caught == [], f"tick 线程不应有未捕获异常，实际: {caught}"


def test_start_ticker_survives_unexpected_exception(monkeypatch):
    """消费侧意外异常（非 SAFE HALT）不应杀死 daemon 线程（审计缺陷2 韧性修复）。

    注入 read_answer_inbox 抛 RuntimeError：修复后整轮 try/except 捕获并下一轮
    重试，线程保持存活且继续 tick；修复前 _loop 无 try/except，线程会静默死亡。
    """
    import autoforge.af_live as _live

    def _boom_read(runtime, inbox_dir=None):
        raise RuntimeError("注入的意外异常")

    monkeypatch.setattr(_live, "read_answer_inbox", _boom_read)

    rt = _FakeRuntime()
    stop = threading.Event()
    t = start_ticker(rt, 0.01, stop, sidecar_dir=None)
    import time as _t

    _t.sleep(0.08)
    alive_after_exception = t.is_alive()
    stop.set()
    t.join(timeout=1.0)
    assert alive_after_exception is True, "消费侧意外异常不应杀死 tick 线程"
    assert rt.ticks >= 1, "线程保活期间应继续 tick"


# ── D1（DCD 20261001·H，方案 C）：tick watchdog 区分退出原因 ──────────────
class _FakeDeadThread:
    def __init__(self, alive: bool = False) -> None:
        self._alive = alive

    def is_alive(self) -> bool:
        return self._alive


def _set_watchdog_state(monkeypatch, *, thread, reason):
    import autoforge.af_live as al

    monkeypatch.setattr(al, "_ticker_thread", thread, raising=False)
    monkeypatch.setattr(al, "_tick_exit_reason", reason, raising=False)
    return al


def test_watchdog_does_not_restart_after_safe_halt(monkeypatch):
    """硬验收：SAFE HALT 后 watchdog **绝不**重启（否则会抵消安全闸，退化成方案 B）。"""
    calls = []
    al = _set_watchdog_state(monkeypatch, thread=_FakeDeadThread(alive=False), reason="safe_halt")
    result = al.tick_watchdog_pass(restart=lambda: calls.append(1))
    assert result == "held_safe_halt"
    assert calls == [], "SAFE HALT 停机不得被 watchdog 重启"


def test_watchdog_restarts_on_unexpected_death(monkeypatch):
    """意外终止 → watchdog 自愈重启（方案 C 唯一可重启分支）。"""
    calls = []
    al = _set_watchdog_state(monkeypatch, thread=_FakeDeadThread(alive=False), reason="unexpected")
    result = al.tick_watchdog_pass(restart=lambda: calls.append(1))
    assert result == "restarted"
    assert calls == [1]
    assert al._tick_exit_reason == "running"


def test_watchdog_no_action_when_thread_alive(monkeypatch):
    al = _set_watchdog_state(monkeypatch, thread=_FakeDeadThread(alive=True), reason="unexpected")
    result = al.tick_watchdog_pass(restart=lambda: (_ for _ in ()).throw(AssertionError("不应重启")))
    assert result == "alive"


def test_watchdog_honors_manual_stop(monkeypatch):
    calls = []
    al = _set_watchdog_state(monkeypatch, thread=_FakeDeadThread(alive=False), reason="stop")
    result = al.tick_watchdog_pass(restart=lambda: calls.append(1))
    assert result == "stopped"
    assert calls == []
