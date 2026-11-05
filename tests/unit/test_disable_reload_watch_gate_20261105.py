# -*- coding: utf-8 -*-
"""判据 D4 · 禁用后 watch 必须重装（2026-11-05 实证 + AF-PM 拍板「先停后起」）· 卡名 AF卡_disable_reload_watch。

实证：`enable_by_tag(enabled=False)` 后 watch 内存仍咬旧快照，被禁用的档继续触发（08:39-08:40 三次）。
根因：`_ensure_watcher(store, enabled=False)` **直接 return None** —— 而「启用面已有 watch reconcile」
那条并不成立：`ir_reconcile_once` 只**报告**漂移、不重装。

修法（PM 拍板）：禁用后「停旧 → 按新 merged IR 起新」，没有可跑的就只停不起。
在途实例的「不留无限 suspended」由既有链路保证（定时器随实例落盘、恢复后 `due_timers()` 立即命中，
再由 D8 的恢复腿落终态）——本判据只钉禁用面的停起行为。

D 编号带卡名（群规⑭）：
  D1 禁用 + 有活 watch + 仍有可跑项 ⇒ 先 stop 再 start
  D2 禁用 + 全部禁完（merged 为空）⇒ 只 stop 不起
  D3 禁用 + 本来没 watch ⇒ none_running（不空转）
  D4 stop 抛错 ⇒ 如实回报（⛔ 不许假装已停）
  D5 源码钉：enabled=False 分支不许早退成 None
"""

from __future__ import annotations

import pytest

from autoforge import af_service
from autoforge.af_store import GraphStore


@pytest.fixture()
def store(tmp_path):
    return GraphStore(str(tmp_path / "store"))


def _wire(monkeypatch, *, alive=True, merged=None, stop_exc=None):
    calls = {"stop": 0, "start": 0}

    monkeypatch.setattr(
        af_service, "list_watches",
        lambda root=None: {"watches": [{"alive": True}] if alive else []},
    )

    def _stop(**kw):
        calls["stop"] += 1
        if stop_exc is not None:
            raise stop_exc
        return {"ok": True, "signal": "delivered"}

    def _start(**kw):
        calls["start"] += 1
        return {"ok": True, "pid": 4242}

    monkeypatch.setattr(af_service, "stop_watch", _stop)
    monkeypatch.setattr(af_service, "_start_watch_impl", _start)
    monkeypatch.setattr(af_service, "_build_merged_ir", lambda s: merged)
    return calls


AUTO = {"ir_version": "0.2.1", "id": "a", "name": "a", "version": 1, "mode": "single",
        "nodes": [{"id": "t1", "kind": "on", "name": "t",
                   "trigger": {"type": "state", "entity_id": "binary_sensor.x", "to": "on"}},
                  {"id": "p1", "kind": "pass", "name": "p"}],
        "edges": [{"from": "t1", "to": "p1", "kind": "then"}]}


# ── D1 先停后起 ─────────────────────────────────────────────────
def test_d1_disable_stops_then_starts(store, monkeypatch):
    calls = _wire(monkeypatch, merged={"automations": [dict(AUTO)]})
    out = af_service._ensure_watcher(store, enabled=False)
    assert calls["stop"] == 1, "禁用必须先停旧 watch"
    assert calls["start"] == 1, "仍有可跑项时应按新 IR 起新 watch"
    assert out["action"] == "restarted"


# ── D2 只停不起 ─────────────────────────────────────────────────
def test_d2_disable_all_off_stops_only(store, monkeypatch):
    calls = _wire(monkeypatch, merged=None)
    out = af_service._ensure_watcher(store, enabled=False)
    assert calls["stop"] == 1
    assert calls["start"] == 0, "没有可跑项时⛔ 不许再起 watch"
    assert out["action"] == "stopped" and out["restarted"] is False


# ── D3 本来没 watch ─────────────────────────────────────────────
def test_d3_disable_without_watch_is_noop(store, monkeypatch):
    calls = _wire(monkeypatch, alive=False, merged={"automations": [dict(AUTO)]})
    out = af_service._ensure_watcher(store, enabled=False)
    assert calls["stop"] == 0 and calls["start"] == 0
    assert out["action"] == "none_running"


# ── D4 stop 抛错如实报 ──────────────────────────────────────────
def test_d4_stop_failure_is_reported(store, monkeypatch):
    _wire(monkeypatch, stop_exc=RuntimeError("锁被占"))
    out = af_service._ensure_watcher(store, enabled=False)
    assert out["action"] == "stop_failed", f"停不动必须如实回，实得 {out}"
    assert "锁被占" in out["reason"]


# ── D5 源码钉 ───────────────────────────────────────────────────
def test_d5_source_no_early_return():
    import inspect

    src = inspect.getsource(af_service._ensure_watcher)
    assert "if not enabled:\n        return None" not in src, "禁用面⛔ 不许再早退成 None"
    assert "stop_watch(" in src and "restarted" in src
