#!/usr/bin/env python3
"""第 0 步：核对关键事实"""
import json
from pathlib import Path

root = Path("/data")

# 1. Automation.raw 的键结构
print("=== 1. Automation.raw 结构 ===")
from autoforge.af_store import GraphStore
s = GraphStore(str(root))
for r in s.history()[:1]:
    g = s.load(r["name"], r["version"])
    a = g.automations[0]
    raw = getattr(a, "raw", None)
    if raw:
        print("raw keys:", list(raw.keys()))
        print("raw 顶层有没有 automations 键:", "automations" in raw)
        print("raw 顶层有没有 nodes 键:", "nodes" in raw)
        print("raw 有没有 meta:", "meta" in raw, raw.get("meta"))

# 2. history record 里有没有 tags / note
print("\n=== 2. history record 字段 ===")
for r in s.history()[:3]:
    print(f"  name={r.get('name')!r} note={r.get('note')!r} tags={r.get('tags')!r}")

# 3. priority 读侧字段名
print("\n=== 3. conflict 读侧字段 ===")
import autoforge.af_conflict as ac
import inspect
src = inspect.getsource(ac)
for line in src.split("\n"):
    if "priority" in line.lower():
        print(" ", line.strip())

# 4. watch-ir-manual 目录内容
print("\n=== 4. watch-ir-manual 目录 ===")
wd = root / "watch-ir-manual"
if wd.exists():
    for f in wd.iterdir():
        print(f"  {f.name}")
