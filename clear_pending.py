import json
from pathlib import Path

pending_dir = Path("/data/pending")
if pending_dir.exists():
    for f in pending_dir.glob("*.json"):
        f.unlink()
        print(f"deleted {f.name}")
print("done")
