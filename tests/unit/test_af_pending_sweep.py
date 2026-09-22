"""审计 Medium：af_pending 僵尸过期清理。"""
from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta

from autoforge.af_pending import PendingStore


def test_sweep_removes_stale(tmp_path):
    store = PendingStore(tmp_path)
    op = store.submit("do", {"a": 1}, summary="x", blast_radius={}, submitted_by="u")
    # 人为把提交时间改到 8 天前
    p = store._path(op.op_id)
    import json
    d = json.loads(p.read_text(encoding="utf-8"))
    d["submitted_at"] = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    p.write_text(json.dumps(d), encoding="utf-8")
    removed = store.sweep(max_age_s=24 * 3600)
    assert removed == 1
    assert store.load(op.op_id) is None


def test_sweep_keeps_fresh(tmp_path):
    store = PendingStore(tmp_path)
    op = store.submit("do", {"a": 1}, summary="x", blast_radius={}, submitted_by="u")
    assert store.sweep(max_age_s=24 * 3600) == 0
    assert store.load(op.op_id) is not None
