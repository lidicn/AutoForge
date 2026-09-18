"""P1 实例持久化与崩溃恢复单测。

覆盖：扫描器放开 persist / 存储原子写 / 记录往返 / 定时器墙钟换算 /
崩溃期间错过的超时 / Runtime 端到端恢复 / 终态清除 / 缺自动化丢弃 / 非 persist 不落盘。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from typer.testing import CliRunner

from autoforge.af_audit import INSTANCE_RESTORE_DROPPED, INSTANCE_RESTORED
from autoforge.af_cli import app
from autoforge.af_instance import DONE, SUSPENDED, InstanceManager
from autoforge.af_ir import Graph, load_automation
from autoforge.af_persist import PersistStore, record_instance, restore_instance
from autoforge.af_runtime import build_runtime
from autoforge.af_scanner import StaticScanner
from autoforge.af_state import InMemoryStateProvider
from autoforge.af_time import VirtualTimeSource

ON_M = {"id": "a1", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}}
P1 = {"id": "p1", "kind": "pass"}


def _wait_ir(auto_id: str = "demo", persist: bool = True) -> dict:
    """入口 → `wait 10m` → pass（`wait` 制造可持久化的挂起实例）。"""
    return {
        "ir_version": "0.2.1",
        "id": auto_id,
        "name": "示例",
        "version": 1,
        "mode": "single",
        "persist": persist,
        "nodes": [ON_M, {"id": "w1", "kind": "wait", "duration": "10m"}, P1],
        "edges": [
            {"from": "a1", "to": "w1", "kind": "then"},
            {"from": "w1", "to": "p1", "kind": "then"},
        ],
    }


def _states() -> InMemoryStateProvider:
    return InMemoryStateProvider(states={"binary_sensor.m": "off"})


def _runtime(data: dict, persist_dir=None, states=None):
    graph = Graph([load_automation(data)])
    provider = states if states is not None else _states()
    return build_runtime(
        graph, states=provider, persist_dir=str(persist_dir) if persist_dir else None
    )


def _suspend(rt) -> object:
    rt.emit("binary_sensor.m", "on", last_changed="t1")
    instance = rt.instances.all()[0]
    assert instance.state == SUSPENDED
    return instance


# ── 扫描器：persist 已放开 ─────────────────────────────────────────────


def test_scanner_allows_persist_now():
    scan = StaticScanner(Graph([load_automation(_wait_ir(persist=True))])).scan()
    assert "RESERVED_NOT_IMPLEMENTED" not in scan.codes()
    assert scan.ok, scan.render()


def test_scanner_rejects_fn_reserved_but_allows_emit():
    """v0.3.0：`emit` 已实现（不再拦截）；仅 `fn` 保留位仍报未实现。"""
    data = _wait_ir()
    data["nodes"].append({"id": "f1", "kind": "set", "var": "x", "value": 1, "fn": {"lang": "cel"}})
    data["edges"].append({"from": "w1", "to": "f1", "kind": "then"})
    scan = StaticScanner(Graph([load_automation(data)])).scan()
    assert "RESERVED_NOT_IMPLEMENTED" in scan.codes(), "`fn` 仍是保留位，应报未实现"


# ── 存储 ──────────────────────────────────────────────────────────────


def test_store_save_load_remove_atomic(tmp_path):
    rt = _runtime(_wait_ir(), tmp_path / "p")
    instance = _suspend(rt)
    store = PersistStore(tmp_path / "p")

    saved = store.save(instance, rt.clock)
    assert saved.is_file()
    assert not list(store.directory.glob("*.tmp")), "原子写不应残留临时文件"

    loaded = store.load(instance.instance_id)
    assert loaded is not None
    assert loaded["instance_id"] == instance.instance_id
    assert loaded["automation_id"] == "demo"
    assert loaded["due_at_wall"]

    assert store.remove(instance.instance_id) is True
    assert store.remove(instance.instance_id) is False  # 二次删除返回 False，不抛
    assert store.records() == []


def test_records_skips_corrupt_file(tmp_path):
    store = PersistStore(tmp_path / "p")
    store.directory.mkdir(parents=True)
    (store.directory / "bad.json").write_text("{ not json", encoding="utf-8")
    assert store.records() == []


# ── 记录往返 / 定时器墙钟换算 ──────────────────────────────────────────


def test_record_roundtrip_preserves_state_and_timer():
    clock = VirtualTimeSource(datetime(2026, 9, 14, 8, 0, tzinfo=timezone.utc))
    auto = load_automation(_wait_ir())
    manager = InstanceManager(_states(), clock)
    instance = manager.spawn(auto)
    manager.suspend(instance, "w1", 600.0, kind="wait")

    record = record_instance(instance, clock)
    json.dumps(record)  # 必须可 JSON 化（红线）

    restored = restore_instance(record, auto, clock)
    assert restored.instance_id == instance.instance_id
    assert restored.state == instance.state
    assert restored.ctx.current_node == "w1"
    assert restored.timer is not None
    assert restored.timer.remaining(clock.monotonic()) == pytest.approx(600.0)


def test_overdue_timer_after_restore_fires():
    """崩溃期间已错过的超时：恢复后定时器到期时刻落在过去 → 立即命中。"""
    clock = VirtualTimeSource(datetime(2026, 9, 14, 8, 0, tzinfo=timezone.utc))
    auto = load_automation(_wait_ir())
    manager = InstanceManager(_states(), clock)
    instance = manager.spawn(auto)
    manager.suspend(instance, "w1", 600.0, kind="wait")

    record = record_instance(instance, clock)
    clock.advance(3600)  # 崩溃/停机 1h，远超 10m 超时
    restored = restore_instance(record, auto, clock)

    assert restored.timer is not None
    assert restored.timer.due_at <= clock.monotonic()
    manager.attach(restored)
    assert restored in manager.due_timers()


# ── Runtime 端到端 ────────────────────────────────────────────────────


def test_runtime_persists_and_restores_active_instance(tmp_path):
    persist_dir = tmp_path / "p"

    rt1 = _runtime(_wait_ir(), persist_dir)
    instance = _suspend(rt1)
    store = PersistStore(persist_dir)
    records = store.records()
    assert len(records) == 1
    assert records[0]["state"] == SUSPENDED
    assert records[0]["automation_id"] == "demo"

    # 进程重启：新 Runtime 同目录 → 恢复该实例
    rt2 = _runtime(_wait_ir(), persist_dir, states=_states())
    assert len(rt2.restored) == 1
    restored = rt2.restored[0]
    assert restored.instance_id == instance.instance_id
    assert restored.state == SUSPENDED
    assert restored.timer is not None
    assert any(e.type == INSTANCE_RESTORED for e in rt2.audit)

    # 推进到期 → then（方案 A）→ pass → done → 终态清除落盘
    rt2.advance(601)
    assert restored.state == DONE
    assert store.records() == [], "终态实例必须从落盘清除"


def test_non_persist_automation_is_not_written(tmp_path):
    persist_dir = tmp_path / "p"
    rt = _runtime(_wait_ir(persist=False), persist_dir)
    _suspend(rt)
    assert PersistStore(persist_dir).records() == []
    assert rt.restored == []


def test_missing_automation_is_dropped_with_audit(tmp_path):
    persist_dir = tmp_path / "p"
    rt1 = _runtime(_wait_ir(auto_id="demo"), persist_dir)
    _suspend(rt1)
    assert len(PersistStore(persist_dir).records()) == 1

    # 用一张不含 demo 的图恢复 → 丢弃 + 审计，不静默保留
    other = Graph([load_automation(_wait_ir(auto_id="other"))])
    rt2 = build_runtime(other, states=_states(), persist_dir=str(persist_dir))
    assert rt2.restored == []
    assert PersistStore(persist_dir).records() == []
    assert any(e.type == INSTANCE_RESTORE_DROPPED for e in rt2.audit)


def test_runtime_without_persist_dir_keeps_memory_behaviour(tmp_path):
    rt = _runtime(_wait_ir(), None)
    _suspend(rt)
    assert rt.persist is None
    assert rt.restored == []


# ── CLI ───────────────────────────────────────────────────────────────


def test_cli_run_accepts_persist_dir(tmp_path):
    ir = tmp_path / "a.json"
    ir.write_text(json.dumps(_wait_ir()), encoding="utf-8")
    persist_dir = tmp_path / "p"
    result = CliRunner().invoke(app, ["run", str(ir), "--persist-dir", str(persist_dir)])
    assert result.exit_code == 0, result.output
