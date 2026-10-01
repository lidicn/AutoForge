#!/usr/bin/env python3
"""第五轮审计（写放大 / 只增不减）的复现脚本 —— 处置记录里的表由此产生。

    python scripts/audit_r5_repro.py            # 快速自检（1000 条）
    python scripts/audit_r5_repro.py --n 4000   # 复现 docs/audit/审计报告_第五轮_核实与修复.md 的表

输出三段，同一进程内跑，消除跨时段负载差：
  A `record()` 修复后（append-only + 上限）
  B `record()` 回归复刻（每插一条重写整档）
  C `EventBus` 实体维度缓存键：修剪关闭（= 修复前）vs 修剪开启
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from autoforge.af_bus import BusEvent, EventBus  # noqa: E402
from autoforge.af_preference import PREFERENCES_FILE, PreferenceModel  # noqa: E402
from autoforge.af_time import VirtualTimeSource  # noqa: E402

MARK_EVERY = (500, 1000, 2000, 4000)


def bench_record(store: str, *, full_rewrite_each_time: bool, max_records: int, n: int) -> None:
    """逐条计时 `record()`：修复后 vs 每插一条重写整档。"""
    label = "B 回归复刻（每条全量重写整档）" if full_rewrite_each_time else "A 修复后（append-only + 上限）"
    if full_rewrite_each_time:
        def _full_rewrite(self, rec):  # 复刻审计快照里的 _save()：每次整档重写
            self._rewrite_all()
        PreferenceModel._append_record = _full_rewrite  # type: ignore[method-assign]
    shutil.rmtree(store, ignore_errors=True)
    os.makedirs(store, exist_ok=True)
    model = PreferenceModel(persist_dir=store, max_records=max_records)
    print(f"\n[{label}]  insert={n}  max_records={max_records}")
    total_ms = 0.0
    for i in range(1, n + 1):
        t0 = time.perf_counter()
        model.record(
            "turn_on",
            {"entity_id": f"light.l{i % 20}", "brightness": i % 100},
            context={"hour": i % 24, "weekday": i % 7, "scene": "home"},
            accepted=(i % 3 != 0),
        )
        dt_ms = (time.perf_counter() - t0) * 1000
        total_ms += dt_ms
        if i in MARK_EVERY or i == n:
            path = os.path.join(store, PREFERENCES_FILE)
            size_kb = os.path.getsize(path) / 1024 if os.path.exists(path) else 0
            lines = sum(1 for _ in open(path, encoding="utf-8")) if os.path.exists(path) else 0
            print(f"    n={i}\tsingle={dt_ms:.2f}ms\tcumulative={total_ms / 1000:.2f}s\t"
                  f"file={size_kb:.0f}KB\tlines={lines}")


def bench_bus_keys() -> None:
    """实体维度缓存：200 个旧实体下线 → 200 个新实体接入，看旧键是否残留。"""
    print("\n[C] EventBus 实体维度缓存键（200 旧实体下线 + 200 新实体接入）")
    for prune in (False, True):
        clock = VirtualTimeSource(datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc))
        bus = EventBus(clock)
        if not prune:
            bus._prune_stale_caches = lambda now: 0  # 修复前：键永不摘除
        for i in range(200):
            bus.publish(BusEvent.of(f"light.old{i}", "on", last_changed=f"o{i}"))
        clock.advance(70.0)
        for i in range(200):
            bus.publish(BusEvent.of(f"light.new{i}", "on", last_changed=f"n{i}"))
        cache = bus.stats()["entity_cache"]
        print(f"    修剪={'开' if prune else '关（= 修复前）'}\t"
              f"_last_accepted={cache['last_accepted']}\t_last_state={cache['last_state']}\t"
              f"_changes={cache['changes']}\tpruned_total={cache['pruned_total']}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=1000, help="插入条数（默认 1000，复现审计表用 4000）")
    ap.add_argument("--max-records", type=int, default=5000)
    args = ap.parse_args()

    root = tempfile.mkdtemp(prefix="af_r5_repro_")
    try:
        bench_record(os.path.join(root, "after"), full_rewrite_each_time=False,
                     max_records=args.max_records, n=args.n)
        bench_record(os.path.join(root, "before"), full_rewrite_each_time=True,
                     max_records=args.max_records, n=args.n)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    bench_bus_keys()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
