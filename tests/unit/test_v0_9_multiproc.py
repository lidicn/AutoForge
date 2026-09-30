"""v0.9.0 跨进程与多写者单测。

覆盖：跨进程文件锁互斥（真子进程）/ 并发保存同图无损坏（真子进程）/
乐观版本冲突审计 / tags 并发读改写 / 实例归属 owner 往返 / 租约仲裁 /
Runtime 恢复尊重租约 / watch 协调锁。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import pytest

import autoforge.af_persist as af_persist_mod
from autoforge.af_audit import (
    INSTANCE_LEASE_HELD,
    WRITE_CONFLICT,
)
from autoforge.af_instance import SUSPENDED, InstanceContext
from autoforge.af_ir import Graph, load_automation
from autoforge.af_live import WatchCoordinator
from autoforge.af_persist import PersistStore, record_instance
from autoforge.af_runtime import build_runtime
from autoforge.af_store import GraphStore, WriteConflictError, restore_context
from autoforge.af_time import VirtualTimeSource

# 供子进程 import autoforge：把包所在 src 目录注入 PYTHONPATH
_SRC = str(Path(af_persist_mod.__file__).resolve().parents[1])


def _run_py(code: str, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = _SRC + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


ON_M = {"id": "a1", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}}
P1 = {"id": "p1", "kind": "pass"}


def _wait_ir(auto_id: str = "demo", persist: bool = True) -> dict:
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


def _states():
    from autoforge.af_state import InMemoryStateProvider

    return InMemoryStateProvider(states={"binary_sensor.m": "off"})


def _runtime(data: dict, persist_dir=None):
    return build_runtime(
        Graph([load_automation(data)]),
        states=_states(),
        persist_dir=str(persist_dir) if persist_dir else None,
    )


# ── af_flock：跨进程互斥（真子进程）────────────────────────────────────


def test_file_lock_mutual_exclusion_between_processes(tmp_path):
    lock_path = tmp_path / "x.lock"

    busy = _run_py(
        """
        import sys
        from autoforge.af_flock import FileLock
        print("ACQUIRED" if FileLock(sys.argv[1], timeout=1.0).try_acquire() else "BUSY")
        """,
        str(lock_path),
    )
    assert busy.returncode == 0, busy.stderr
    assert busy.stdout.strip() == "ACQUIRED"  # 无人持锁 → 可拿

    lock = __import__("autoforge.af_flock", fromlist=["FileLock"]).FileLock(lock_path, timeout=1.0)
    assert lock.try_acquire() is True
    assert lock.holder().get("owner"), "锁文件应记录持有者身份"

    child = _run_py(
        """
        import sys
        from autoforge.af_flock import FileLock
        lk = FileLock(sys.argv[1], timeout=0.5)
        if lk.try_acquire():
            print("ACQUIRED")
        else:
            print("BUSY:" + lk.holder().get("owner", "?"))
        """,
        str(lock_path),
    )
    assert child.returncode == 0, child.stderr
    assert child.stdout.strip().startswith("BUSY:"), "父进程持锁期间子进程必须被拒"

    lock.release()
    after = _run_py(
        """
        import sys
        from autoforge.af_flock import FileLock
        print("ACQUIRED" if FileLock(sys.argv[1], timeout=1.0).try_acquire() else "BUSY")
        """,
        str(lock_path),
    )
    assert after.stdout.strip() == "ACQUIRED", "释放后子进程应可拿锁"


# ── GraphStore：跨进程并发保存（真子进程）────────────────────────────


_SAVING_WORKER = """
import sys
from autoforge.af_ir import load_graph
from autoforge.af_store import GraphStore

IR = {
    "ir_version": "0.2.1", "id": "demo", "name": "并发", "version": 1,
    "mode": "single",
    "nodes": [
        {"id": "a1", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
        {"id": "p1", "kind": "pass"},
    ],
    "edges": [{"from": "a1", "to": "p1", "kind": "then"}],
}
store = GraphStore(sys.argv[1])
for i in range(5):
    store.save(load_graph(IR), sys.argv[2], note=f"worker-{sys.argv[3]}-{i}")
print("OK")
"""


def test_concurrent_saves_from_processes_no_corruption(tmp_path):
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", textwrap.dedent(_SAVING_WORKER), str(tmp_path), "demo", str(w)],
            env={**os.environ, "PYTHONPATH": _SRC + os.pathsep + os.environ.get("PYTHONPATH", "")},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for w in range(3)
    ]
    for p in procs:
        out, err = p.communicate(timeout=120)
        assert p.returncode == 0, f"worker 失败：{err}"

    store = GraphStore(tmp_path)
    versions = store.versions("demo")
    assert len(versions) == 15, "3 进程 × 5 次保存 → 版本号必须一个不少"
    assert versions == list(range(1, 16)), "锁内自增 → 版本号连续无空洞"
    for v in versions:
        record = store.load_record("demo", v)  # 任一版本都能完整解析
        assert record["name"] == "demo" and record["writer"]


def test_save_expect_version_conflict_audited(tmp_path):
    store = GraphStore(tmp_path)
    graph = Graph([load_automation(_wait_ir(auto_id="demo"))])

    v1 = store.save(graph, "demo")
    v2 = store.save(graph, "demo", expect_version=v1)
    assert v2 == v1 + 1

    with pytest.raises(WriteConflictError):
        store.save(graph, "demo", expect_version=v1)  # 已被并发推进到 v2

    journal = tmp_path / "write_conflicts.jsonl"
    lines = journal.read_text(encoding="utf-8").strip().splitlines()
    entry = json.loads(lines[-1])
    assert entry["type"] == WRITE_CONFLICT
    assert entry["name"] == "demo"
    assert entry["expected_version"] == v1
    assert entry["actual_version"] == v2
    assert entry["writer"]


def test_tags_read_modify_write_survives_concurrent_processes(tmp_path):
    """两个进程并发对同一 tags.json 各自 read-modify-write → 双方键都保留。"""
    code = """
import sys
from autoforge.af_store import GraphStore
store = GraphStore(sys.argv[1])
for i in range(10):
    store.set_tags(sys.argv[2], [sys.argv[2], f"iter{i}"])
print("OK")
"""
    env = {**os.environ, "PYTHONPATH": _SRC + os.pathsep + os.environ.get("PYTHONPATH", "")}
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", textwrap.dedent(code), str(tmp_path), name],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        for name in ("alpha", "beta")
    ]
    for p in procs:
        out, err = p.communicate(timeout=120)
        assert p.returncode == 0, err

    tags = GraphStore(tmp_path).all_tags()
    assert "alpha" in tags and "beta" in tags, "锁内读改写 → 并发双方都不丢"


# ── 实例归属（owner）与租约仲裁 ───────────────────────────────────────


def test_instance_context_owner_roundtrip():
    ctx = InstanceContext(instance_id="i1", automation_id="demo", owner="worker-a")
    assert ctx.to_dict()["owner"] == "worker-a"
    restored = restore_context(ctx.to_dict())
    assert restored.owner == "worker-a"


def test_persist_store_stamps_owner_and_lease(tmp_path):
    clock = VirtualTimeSource(datetime(2026, 9, 14, 8, 0, tzinfo=timezone.utc))
    auto = load_automation(_wait_ir())
    store_a = PersistStore(tmp_path, owner="worker-a", lease_s=60.0)
    inst = af_persist_mod.restore_instance(
        record_instance(_spawn_suspended(clock, auto), clock), auto, clock
    )
    path = store_a.save(inst, clock)
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["owner"] == "worker-a"
    assert record["lease_until_wall"] == "2026-09-14T08:01:00+00:00"  # 虚拟钟 08:00 + 60s 租约


def _spawn_suspended(clock, auto):
    from autoforge.af_instance import InstanceManager

    manager = InstanceManager(_states(), clock)
    instance = manager.spawn(auto)
    manager.suspend(instance, "w1", 600.0, kind="wait")
    return instance


def test_lease_arbitration(tmp_path):
    clock = VirtualTimeSource(datetime(2026, 9, 14, 8, 0, tzinfo=timezone.utc))
    auto = load_automation(_wait_ir())
    store_a = PersistStore(tmp_path, owner="worker-a", lease_s=600.0)
    store_b = PersistStore(tmp_path, owner="worker-b", lease_s=600.0)

    inst = af_persist_mod.restore_instance(
        record_instance(_spawn_suspended(clock, auto), clock), auto, clock
    )
    store_a.save(inst, clock)
    record = store_a.load(inst.instance_id)

    assert store_a.claims(record, clock) is True  # 本进程所有
    assert store_b.claims(record, clock) is False  # 租约仍属 worker-a
    record["lease_until_wall"] = "2026-09-14T07:00:00+00:00"  # 租约已过期
    assert store_b.claims(record, clock) is True  # 过期 → 可接管
    legacy = {k: v for k, v in record.items() if k != "owner"}
    assert store_b.claims(legacy, clock) is True  # 旧版无主记录 → 可接管


def test_runtime_restore_honors_lease(tmp_path):
    persist_dir = tmp_path / "p"
    rt1 = _runtime(_wait_ir(), persist_dir)
    instance = rt1.emit("binary_sensor.m", "on", last_changed="t1")[0]
    assert instance.state == SUSPENDED
    record_path = next((persist_dir / "instances").glob("*.json"))

    # 伪造租约仍属其他进程（远期到期）
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["owner"] = "other-proc"
    record["lease_until_wall"] = "2027-01-01T00:00:00+00:00"
    # WAL-B：修改后需刷新 _sha256（先剥离旧 checksum 再算）
    _sha_key = af_persist_mod._SHA256_KEY
    record[_sha_key] = af_persist_mod._record_checksum({k: v for k, v in record.items() if k != _sha_key})
    record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    rt2 = _runtime(_wait_ir(), persist_dir)
    assert rt2.restored == [], "租约未到期 → 不恢复、不双跑"
    assert any(e.type == INSTANCE_LEASE_HELD for e in rt2.audit)
    assert record_path.is_file(), "跳过恢复时不得删除落盘文件"

    # 租约过期（崩溃进程）→ 可接管恢复
    record["lease_until_wall"] = "2026-01-01T00:00:00+00:00"
    record[_sha_key] = af_persist_mod._record_checksum({k: v for k, v in record.items() if k != _sha_key})
    record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    rt3 = _runtime(_wait_ir(), persist_dir)
    assert len(rt3.restored) == 1
    assert rt3.restored[0].instance_id == instance.instance_id


# ── watch 多实例协调 ──────────────────────────────────────────────────


def test_watch_coordinator_single_active(tmp_path):
    lock_path = tmp_path / "watch.lock"
    coord_a = WatchCoordinator(lock_path, holder_info={"graph": "demo.json"})
    coord_b = WatchCoordinator(lock_path)

    assert coord_a.try_acquire() is True
    assert coord_a.info["graph"] == "demo.json"

    assert coord_b.try_acquire() is False, "已有活跃 watcher → 第二实例被拒"
    assert coord_b.holder.get("watcher"), "拒绝方可读到持有者身份做诊断"

    coord_a.release()
    assert coord_b.try_acquire() is True, "释放后可接管"
    coord_b.release()
