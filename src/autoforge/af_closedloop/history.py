# -*- coding: utf-8 -*-
"""history — 修复历史 JSONL 持久化：追加写、断点续读、指纹聚合（闭环防死循环 + 修复命中率）。"""
from __future__ import annotations

import json
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Iterator

SCHEMA = "af-fixlog/1"


class FixHistoryStore:
    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.skipped = 0

    def append(self, **fields) -> dict:
        rec = {"schema": SCHEMA, "ts": time.time()}
        rec.update({k: v for k, v in fields.items() if v is not None})
        line = json.dumps(rec, ensure_ascii=False, default=str)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        return rec

    def iter(self, **filters) -> Iterator[dict]:
        if not self.path.exists():
            return
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    self.skipped += 1
                    continue
                if not isinstance(rec, dict):
                    self.skipped += 1
                    continue
                if all(rec.get(k) == v for k, v in filters.items()):
                    yield rec

    def records(self, **filters) -> list:
        return list(self.iter(**filters))

    def count(self, fingerprint: str) -> int:
        return sum(1 for _ in self.iter(fingerprint=fingerprint))

    def stats(self) -> dict:
        """修复覆盖率报告：哪些 ErrorCode 能闭环自愈、哪些必须转人工。"""
        recs = self.records()
        by_code = Counter(str(r.get("code")) for r in recs)
        applied = sum(1 for r in recs if r.get("applied"))
        needs_user = sum(1 for r in recs if r.get("needs_user"))
        auto_handled = len(recs) - needs_user
        return {
            "total": len(recs),
            "applied": applied,
            "needs_user": needs_user,
            "by_code": dict(sorted(by_code.items())),
            "auto_fix_rate": round(auto_handled / len(recs), 3) if recs else 0.0,
            "skipped_lines": self.skipped,
        }
