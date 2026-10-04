""""固定名 .tmp + 裸 write_text"这一族修完之后，必须能证明**残留下不来、锁真的拿、tmp 名真的不撞**。

起因：安全审计那份 zip 的 out_of_scope 单元里盘出的写原子性族（`af_store._atomic_write` 的
P1-18 修法没有扩散）。本批修掉 4 处形状最坏的：
  - `af_persist.save`：`PersistStore.claims()` 明确允许两个进程在租约到期后驱动同一条实例，
    两边写同一个 `{id}.json.tmp` ⇒ 交错内容被 `os.replace` 装上 ⇒ 读侧对校验和失败的记录**跳过**
    ⇒ 那条活着的实例记录静默消失。
  - `af_api._resave_graph_raw`（启停写）：整段在端点闭包里手抄 store 的落盘纪律，既不拿 `.lock`
    也用固定名 tmp ⇒ 下沉成 `GraphStore.resave_raw`。
  - `GraphStore._delete_archive` 的标签半：读-改-写在锁外 ⇒ 挪进 `tags.lock`（且**不能**套
    `_write_tags`——`FileLock` 无重入计数，内层 release 会把外层的锁放开）。
  - `af_catalog` 三站 + `af_insight_queue._atomic_write`：同一族的其余成员。
静态那半由 `scripts/check_atomic_write_sites.py` 钉（见 `test_atomic_write_gate.py`）；这里钉值语义。
"""
from __future__ import annotations

import ast
import json
import os
import pathlib

import pytest

from autoforge.af_api import build_app
from autoforge.af_insight_queue import _atomic_write
from autoforge.af_ir import Graph, load_automation, load_graph
from autoforge.af_persist import PersistStore
from autoforge.af_store import GraphStore

ROOT = pathlib.Path(__file__).resolve().parents[2]

GRAPH = {
    "automations": [
        {
            "ir_version": "0.2.1",
            "id": "g1",
            "name": "g1",
            "version": 1,
            "mode": "restart",
            "nodes": [
                {"id": "a1", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
                {"id": "p1", "kind": "pass"},
            ],
            "edges": [{"from": "a1", "to": "p1", "kind": "then"}],
        }
    ]
}

WAIT_IR = {
    "ir_version": "0.2.1",
    "id": "demo",
    "name": "示例",
    "version": 1,
    "mode": "single",
    "persist": True,
    "nodes": [
        {"id": "a1", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
        {"id": "w1", "kind": "wait", "duration": "10m"},
        {"id": "p1", "kind": "pass"},
    ],
    "edges": [
        {"from": "a1", "to": "w1", "kind": "then"},
        {"from": "w1", "to": "p1", "kind": "then"},
    ],
}


class _LockSpy:
    """记录每次构造时拿的锁路径，行为委托给真 `FileLock`。"""

    acquired: list[str] = []

    def __init__(self, path, timeout: float = 10.0):
        self._path = str(path)
        from autoforge.af_flock import FileLock

        self._real = FileLock(path, timeout=timeout)

    def __enter__(self):
        _LockSpy.acquired.append(self._path)
        return self._real.__enter__()

    def __exit__(self, *exc):
        return self._real.__exit__(*exc)


@pytest.fixture(autouse=True)
def _reset_lock_spy(monkeypatch):
    _LockSpy.acquired = []
    import autoforge.af_store as af_store

    monkeypatch.setattr(af_store, "FileLock", _LockSpy)
    yield


def _store(tmp_path) -> GraphStore:
    store = GraphStore(tmp_path)
    store.save(load_graph(GRAPH), "g1")
    return store


# ── GraphStore.resave_raw：锁 + 版本 + 无残留 ──────────────────────────

def test_resave_raw_takes_the_per_graph_lock(tmp_path):
    store = _store(tmp_path)
    _LockSpy.acquired.clear()  # `save()` 自己拿过同一把锁，不清零就等于拿旧账通过
    v = store.resave_raw("g1", lambda g: g.__setitem__("enabled", False))
    assert v == 2
    assert any(p.endswith(".lock") and "g1" in p for p in _LockSpy.acquired), _LockSpy.acquired
    assert store.load_record("g1")["graph"]["enabled"] is False


def test_resave_raw_bumps_monotonically_and_keeps_the_record(tmp_path):
    store = _store(tmp_path)
    for expected in (2, 3, 4):
        assert store.resave_raw("g1", lambda g: g.__setitem__("enabled", True)) == expected
    rec = store.load_record("g1")
    assert rec["version"] == 4
    assert rec["name"] == "g1" and isinstance(rec["graph"], dict) and rec["graph"], rec.keys()


def test_resave_raw_leaves_no_tmp_residue(tmp_path):
    store = _store(tmp_path)
    store.resave_raw("g1", lambda g: g.__setitem__("enabled", True))
    d = pathlib.Path(store.root) / "g1"
    assert not list(d.glob("*.tmp")), list(d.iterdir())


def test_enable_endpoint_round_trips_through_the_store(tmp_path, monkeypatch):
    monkeypatch.setenv("AF_ALLOW_NOAUTH", "1")
    store = _store(tmp_path)
    from fastapi.testclient import TestClient

    client = TestClient(build_app(store_root=str(tmp_path)))
    r = client.post("/api/automations/g1/enable")
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 2
    assert store.load_record("g1")["graph"]["enabled"] is True


def test_api_layer_no_longer_reaches_into_store_privates():
    """`_resave_graph_raw` 必须只是转调：手抄落盘纪律 = 下一次又漏一把锁。"""
    tree = ast.parse((ROOT / "src" / "autoforge" / "af_api.py").read_text(encoding="utf-8"))
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_resave_graph_raw"
    )
    names = {getattr(node.func, "attr", "") for node in ast.walk(fn) if isinstance(node, ast.Call)}
    attrs = {node.attr for node in ast.walk(fn) if isinstance(node, ast.Attribute)}
    assert "resave_raw" in names, names
    assert "_dir" not in attrs and "replace" not in names, attrs


# ── _delete_archive：标签侧车整段在 tags.lock 内读-改-写 ───────────────

def test_delete_archive_locks_the_tags_sidecar(tmp_path):
    store = _store(tmp_path)
    store.set_tags("g1", ["night"])
    _LockSpy.acquired.clear()
    store._delete_archive("g1")
    assert any(p.endswith("tags.lock") for p in _LockSpy.acquired), _LockSpy.acquired
    assert store.get_tags("g1") == []


def test_delete_archive_does_not_unlock_itself_early(tmp_path, monkeypatch):
    """嵌套拿同一把锁会被内层 release 放开——这里必须只拿一次 `tags.lock`。"""
    store = _store(tmp_path)
    store.set_tags("g1", ["night"])
    _LockSpy.acquired.clear()
    store._delete_archive("g1")
    assert [p for p in _LockSpy.acquired if p.endswith("tags.lock")].__len__() == 1


def test_delete_archive_reads_tags_only_after_taking_the_lock(tmp_path, monkeypatch):
    """读在锁外＝拿的是一份可能过期的全量字典，覆盖别人刚写进去的标签（丢失更新）。

    这条钉的是**顺序**而不是"有没有拿锁"：旧形状也拿 `tags.lock`（在 `_write_tags` 里），
    但读发生在锁之前，所以只数锁次数的测试放得过它。
    """
    import autoforge.af_store as af_store

    events: list[str] = []

    class _OrderedLockSpy:
        def __init__(self, path, timeout: float = 10.0):
            self._path = str(path)
            from autoforge.af_flock import FileLock

            self._real = FileLock(path, timeout=timeout)

        def __enter__(self):
            events.append(f"lock:{self._path}")
            return self._real.__enter__()

        def __exit__(self, *exc):
            return self._real.__exit__(*exc)

    real_read = GraphStore._read_tags

    def spy_read(self):
        events.append("read")
        return real_read(self)

    monkeypatch.setattr(af_store, "FileLock", _OrderedLockSpy)
    monkeypatch.setattr(GraphStore, "_read_tags", spy_read)

    store = _store(tmp_path)
    store.set_tags("g1", ["night"])
    events.clear()
    store._delete_archive("g1")
    locks = [i for i, e in enumerate(events) if e.endswith("tags.lock")]
    reads = [i for i, e in enumerate(events) if e == "read"]
    assert locks and reads, events
    assert locks[-1] < reads[-1], events


# ── PersistStore.save：实例记录的 tmp 名不撞 ──────────────────────────

def _suspended_instance(tmp_path):
    from autoforge.af_runtime import build_runtime
    from autoforge.af_state import InMemoryStateProvider

    rt = build_runtime(
        Graph([load_automation(WAIT_IR)]),
        states=InMemoryStateProvider(states={"binary_sensor.m": "off"}),
        persist_dir=str(tmp_path / "p"),
    )
    rt.emit("binary_sensor.m", "on", last_changed="t1")
    return rt, rt.instances.all()[0]


def test_persist_save_writes_unique_tmp_names(tmp_path, monkeypatch):
    """两次落盘的临时文件必须不同名：同名 = 第二个写者截断第一个，读侧只会静默跳过。"""
    rt, instance = _suspended_instance(tmp_path)
    seen: list[str] = []
    real = os.replace

    def spy(src, dst, *args, **kwargs):
        seen.append(str(src))
        return real(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", spy)
    store = PersistStore(tmp_path / "p", owner="proc-A")
    store.save(instance, rt.clock)
    store2 = PersistStore(tmp_path / "p", owner="proc-B")
    store2.save(instance, rt.clock)
    assert len(seen) >= 2, seen
    assert len({os.path.basename(s) for s in seen}) == len(seen), seen
    assert all(s.endswith(".tmp") for s in seen), seen


def test_persist_save_leaves_no_tmp_residue(tmp_path):
    rt, instance = _suspended_instance(tmp_path)
    store = PersistStore(tmp_path / "p")
    store.save(instance, rt.clock)
    assert not list(store.directory.glob("*.tmp")), list(store.directory.iterdir())


@pytest.mark.skipif(os.name != "posix", reason="Windows 上 chmod 不进 ACL，位模式无从判定（ADM B-14 只在 POSIX 侧可验）")
def test_persist_record_is_owner_only_on_posix(tmp_path):
    rt, instance = _suspended_instance(tmp_path)
    path = PersistStore(tmp_path / "p").save(instance, rt.clock)
    assert path.stat().st_mode & 0o077 == 0, oct(path.stat().st_mode)


# ── 其余两族成员：目录/别名 与 洞察队列助手 ────────────────────────────

def test_insight_queue_helper_writes_full_json_without_residue(tmp_path):
    path = tmp_path / "pending" / "ins-1.json"
    _atomic_write(path, {"b": 2, "a": 1})
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1, "b": 2}
    assert not list(path.parent.glob("*.tmp")), list(path.parent.iterdir())


def test_catalog_save_and_alias_leave_no_fixed_name_tmp(tmp_path):
    from autoforge.af_catalog import DeviceCatalog

    cat_path = tmp_path / "store" / "catalog.json"
    alias_path = tmp_path / "store" / "aliases.json"
    cat = DeviceCatalog(tmp_path / "store")
    cat._save({"schema": 1, "entities": {"light.x": {"area": "study"}}, "freshness": ""})
    assert cat._load()["entities"]["light.x"]["area"] == "study"
    assert cat.set_alias("书桌灯", "light.x")["ok"] is True
    assert cat._load_aliases() == {"书桌灯": "light.x"}
    residue = list(cat_path.parent.glob("*.tmp")) + list(alias_path.parent.glob("*.tmp"))
    assert not residue, residue
    assert cat.remove_alias("书桌灯")["ok"] is True
    assert cat._load_aliases() == {}
