"""两条"清理逻辑写好了但没人按"的回收路径：跨日记账清理与撤销快照清理。

起因（裁定 20261004 §一 3 存量核账时实测出来的形状）：
- `JsonFireStore.sweep()` / `FireRecorder.sweep()` 完整存在（`keep_days=3` + 3600s 单调节流），
  全仓调用方只有 `tests/unit/test_fire_recorder.py` 里那句显式 `sweep(force=True)`。生产侧
  `af_scheduler` 只用 `try_begin/confirm/release`，`af_runtime` 装上 recorder 后从不扫 ⇒
  `fire_log.json` 的 day 维度按天只增不减。
- `UndoStore.purge_expired()` 调用方为零（`grep -rn purge_expired src` 只命中定义）。超窗快照被
  读侧判成 `expired` 却从不摘除 ⇒ `undo_log.json` 随**部署次数**单调增长，且每次都全量重写。

这一族第五轮已经审过一次（明细只增不减 + 每条全量重写），修法是"回收挂在写路径或统一 tick 上"。
判据形状照本仓口径：**先证 harness 真会写**（无清理前读数必须 >0），再证"纯写不读也被回收"，
最后证"没超窗的那条还在"——只写前两条的话，把清理改成无条件清空也能绿。
"""

from __future__ import annotations

from datetime import datetime, timezone

from autoforge.af_fire_recorder import JsonFireStore
from autoforge.af_ir import Graph, load_automation
from autoforge.af_runtime import build_runtime
from autoforge.af_state import InMemoryStateProvider
from autoforge.af_time import VirtualTimeSource
from autoforge.af_undo import UndoStore

DEMO = {
    "ir_version": "0.2.1", "id": "demo", "name": "示例", "version": 1, "mode": "single",
    "nodes": [
        {"id": "a1", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
        {"id": "p1", "kind": "pass"},
    ],
    "edges": [{"from": "a1", "to": "p1", "kind": "then"}],
}

ANCHOR = datetime(2026, 10, 4, 12, 0, 0, tzinfo=timezone.utc)


def _runtime(tmp_path):
    return build_runtime(
        Graph([load_automation(DEMO)]),
        states=InMemoryStateProvider(states={"binary_sensor.m": "off"}),
        persist_dir=str(tmp_path),
    )


class _CountingRecorder:
    def __init__(self):
        self.calls: list[dict] = []

    def sweep(self, **kwargs):
        self.calls.append(kwargs)
        return 0


# ── ① 跨日记账清理：调用方从"只有测试"变成"统一 tick" ──────────────────


def test_tick_calls_the_installed_recorder_sweep(tmp_path):
    """接缝本身：`Runtime.tick()` 必须按 recorder 的 `sweep()`，且**不带 force**。

    带 force 会把 3600s 节流旁路掉——逐 tick 调用就变成逐 tick 扫盘重写。
    """
    rt = _runtime(tmp_path)
    stub = _CountingRecorder()
    rt.scheduler.fire_recorder = stub

    rt.tick()

    assert stub.calls == [{}], f"tick 没调用 sweep，或调用时塞了参数：{stub.calls}"


def test_tick_reclaims_old_day_fire_records_from_disk(tmp_path):
    """端到端：真 recorder 记一条，跨 5 天后一次 tick 就要把它从 `fire_log.json` 摘掉。"""
    rt = _runtime(tmp_path)
    recorder = rt.scheduler.fire_recorder
    assert recorder is not None, "harness 没装上 recorder ⇒ 这条用例等于没跑"

    lease = recorder.try_begin("rule1")
    assert lease is not None
    lease.confirm("inst-1")
    assert JsonFireStore(tmp_path).count() == 1, "对照组：清理前必须真写过一条"

    rt.clock.advance(86400 * 5)
    rt.tick()

    assert JsonFireStore(tmp_path).count() == 0, "跨日记录仍在盘上：sweep 没被生产路径按到"


# ── ② 撤销快照：写侧摘除（下一次记录时清），不是"打开即清"──────────────


def _undo(root, clock, deploy_id, entity_id):
    store = UndoStore(str(root), window_s=60, clock=clock)
    store.record(deploy_id, {entity_id: {"state": "on", "attributes": {"brightness": 10}}})
    return store


def test_expired_snapshot_is_removed_by_the_next_record(tmp_path):
    """超窗快照在下一次记录时被摘除并回写，而不是留在文件里等读侧判 expired。

    回收点为什么在 `record()` 而不是 `__init__`：本仓另有一条 HTTP 判据
    （`test_af_undo_http.py::test_undo_refuses_expired_window_via_http`）要求 `/api/undo/{id}`
    对超窗记录回 `expired=true` 而不是"没这条"。打开即清会让重启把两者混成同一个答复——
    那条判据实测拦下了这个落法，所以这里钉的是写路径。
    """
    clock = VirtualTimeSource(ANCHOR)
    _undo(tmp_path, clock, "deploy-one", "light.study")
    clock.advance(61)
    _undo(tmp_path, clock, "deploy-two", "light.hall")

    reopened = UndoStore(str(tmp_path), window_s=60, clock=VirtualTimeSource(clock.now()))

    assert reopened.get("deploy-one") is None
    assert reopened.get("deploy-two") is not None
    # 落盘也要真的少了：直接读文件本体，不经 `_records`
    text = (tmp_path / "undo_log.json").read_text(encoding="utf-8")
    assert "deploy-one" not in text, "内存摘除但文件没回写 ⇒ 下次启动又是一样的量"


def test_snapshot_within_the_window_survives_reopen(tmp_path):
    """对照组：窗口内的快照一份都不许少——否则"回收"会退化成"随手清空"，F7 的撤销把手自己被拆。"""
    clock = VirtualTimeSource(ANCHOR)
    _undo(tmp_path, clock, "deploy-one", "light.study")

    reopened = UndoStore(str(tmp_path), window_s=60, clock=VirtualTimeSource(clock.now()))

    assert reopened.get("deploy-one") is not None


# ── ③ 撤销快照的硬上限腿（注册表项 `af_undo:UndoStore._records` 的测试出处）──


def _fill(tmp_path, count, cap):
    clock = VirtualTimeSource(ANCHOR)
    store = UndoStore(str(tmp_path), window_s=60, clock=clock, max_deploys=cap)
    for i in range(count):
        store.record(f"deploy-{i}", {"light.study": {"state": "on", "attributes": {}}})
        clock.advance(1)  # 全部留在窗口内：被摘只能归因于上限，不是 TTL
    return store, clock


def _kept(store, count):
    return [i for i in range(count) if store.get(f"deploy-{i}") is not None]


def test_undo_snapshot_count_is_capped_without_reads(tmp_path):
    """连写 7 份、一次都不读：只剩 `max_deploys` 份，且丢的是最旧的那两份。"""
    store, clock = _fill(tmp_path, 7, 5)

    assert _kept(store, 7) == [2, 3, 4, 5, 6]

    reopened = UndoStore(str(tmp_path), window_s=60, clock=VirtualTimeSource(clock.now()))
    assert _kept(reopened, 7) == [2, 3, 4, 5, 6], "内存裁了但文件没回写 ⇒ 下次启动又是 7 份"


def test_undo_snapshots_under_the_cap_are_untouched(tmp_path):
    """对照组：没超上限时一份都不许少——否则"上限"会退化成"随手清空"。"""
    store, _clock = _fill(tmp_path, 5, 5)

    assert _kept(store, 5) == [0, 1, 2, 3, 4]
