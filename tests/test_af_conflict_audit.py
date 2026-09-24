import importlib
import importlib.util
import json
import os
import pathlib
import sys
import tempfile


def _load(name: str):
    for dotted in (f"autoforge.{name}", f"af.{name}", name):
        try:
            return importlib.import_module(dotted)
        except ImportError:
            continue
    root = pathlib.Path(__file__).resolve().parents[1]
    anchors = sorted(root.rglob("af_executor.py")) or [root / "_none.py"]
    candidates = [anchors[0].parent / f"{name}.py"] + [
        p for p in sorted(root.rglob(f"{name}.py")) if "tests" not in p.parts
    ]
    for path in candidates:
        if not path.exists():
            continue
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    raise ImportError(f"cannot locate {name}.py")


af_conflict = _load("af_conflict")
af_conflict_audit = _load("af_conflict_audit")

ConflictEvent = af_conflict.ConflictEvent
ResourceLock = af_conflict.ResourceLock
ConflictAuditor = af_conflict_audit.ConflictAuditor
event_to_dict = af_conflict_audit.event_to_dict


def make_event(kind, entity="light.study", requester="A", holder=None, ts=1.0, **details):
    return ConflictEvent(
        event_id=f"{kind}-{entity}-{requester}-{ts}",
        entity_id=entity,
        kind=kind,
        requester_id=requester,
        holder_id=holder,
        timestamp=ts,
        details=details,
    )


def test_record_then_history_returns_latest_first():
    auditor = ConflictAuditor()
    auditor.record(make_event("rejected", ts=1.0))
    auditor.record(make_event("preempted", ts=2.0))
    history = auditor.history()
    assert [e.timestamp for e in history] == [2.0, 1.0]


def test_history_filters_by_entity_id():
    auditor = ConflictAuditor()
    auditor.record(make_event("rejected", entity="light.study", ts=1.0))
    auditor.record(make_event("rejected", entity="light.living", ts=2.0))
    assert [e.entity_id for e in auditor.history(entity_id="light.study")] == ["light.study"]


def test_history_filters_by_automation_id():
    auditor = ConflictAuditor()
    auditor.record(make_event("preempted", requester="B", holder="A", ts=1.0))
    auditor.record(make_event("rejected", requester="C", holder=None, ts=2.0))
    assert [e.requester_id for e in auditor.history(automation_id="A")] == ["B"]
    assert len(auditor.history(automation_id="C")) == 1


def test_summary_counts_kinds():
    auditor = ConflictAuditor()
    auditor.record(make_event("rejected", requester="A", ts=1.0))
    auditor.record(make_event("preempted", requester="B", holder="A", ts=2.0))
    auditor.record(make_event("circuit_open", requester="A", ts=3.0))
    auditor.record(make_event("flicker", requester="A", ts=4.0))
    auditor.record(make_event("throttled", requester="A", ts=5.0))
    auditor.record(make_event("rejected", requester="Z", holder=None, ts=6.0))
    summary = auditor.summary("A")
    assert summary == {
        "total": 5,
        "rejected": 1,
        "preempted": 1,
        "circuit_open": 1,
        "flicker": 2,
    }


def test_persistence_append_and_reload():
    with tempfile.TemporaryDirectory() as tmp:
        first = ConflictAuditor(persist_dir=tmp)
        first.record(make_event("rejected", ts=1.0))
        first.record(make_event("flicker", ts=2.0))
        path = os.path.join(tmp, af_conflict_audit.AUDIT_FILENAME)
        assert os.path.exists(path)
        with open(path, "r", encoding="utf-8") as fh:
            lines = [json.loads(x) for x in fh.read().splitlines() if x.strip()]
        assert [x["kind"] for x in lines] == ["rejected", "flicker"]

        restored = ConflictAuditor(persist_dir=tmp)     # 持久化恢复
        assert len(restored.events) == 2
        assert restored.events[0].kind == "rejected"
        restored.record(make_event("starved", ts=3.0))  # JSONL 追加写
        assert sum(1 for _ in open(path, encoding="utf-8")) == 3


def test_locks_snapshot_and_live_provider():
    auditor = ConflictAuditor()
    lock = ResourceLock("light.study", "A", "i-1", "light.turn_on", 10.0, 10.0, 0.9)
    auditor.snapshot_locks([lock])
    assert auditor.locks() == [
        {
            "entity_id": "light.study",
            "automation_id": "A",
            "instance_id": "i-1",
            "action": "light.turn_on",
            "acquired_at": 10.0,
            "ttl": 10.0,
            "priority": 0.9,
        }
    ]
    auditor.lock_provider = lambda: []
    assert auditor.locks() == []
