#!/usr/bin/env python3
"""一次性迁移：issued_tokens.json / revoked.json 的明文令牌键 → SHA-256 摘要 + principal_kind。

第四批批 2（D6/D9）配套脚本。只跑一次；跑完服务按新格式读写。

用法：
    PYTHONPATH=src python scripts/migrate_issued_tokens_digest.py --store-root <store>            # dry-run（默认）
    PYTHONPATH=src python scripts/migrate_issued_tokens_digest.py --store-root <store> --apply     # 真正写

纪律：
- ⛔ 脚本内绝不打印任何令牌明文或摘要值，只打印计数；
- 写前先备份原文件到 `.auth/backup-<时间戳>/`；
- 文件损坏/形状不对 ⇒ 拒写并退出非零，原文件不动；
- 默认 dry-run，必须显式 --apply 才落盘。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from autoforge.af_auth import _atomic_write_text, _digest, _looks_digest


def _infer_kind(subject: str) -> str:
    return "login" if subject in ("owner", "shared") else "agent"


def _load_json_dict(p: Path) -> dict:
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{p.name} 形状不是对象（{type(data).__name__}），拒写")
    return data


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="issued_tokens/revoked 明文键 → 摘要迁移")
    ap.add_argument("--store-root", required=True, help="store 根目录（.auth 在其下）")
    ap.add_argument("--apply", action="store_true", help="真正写盘；默认 dry-run")
    args = ap.parse_args(argv)

    auth_dir = Path(args.store_root) / ".auth"
    issued_path = auth_dir / "issued_tokens.json"
    revoked_path = auth_dir / "revoked.json"

    # ── 预演：全程只读，算清计数，任何形状不对就立刻拒 ──
    issued_count = 0
    login_count = 0
    agent_count = 0
    revoked_count = 0

    if issued_path.is_file():
        data = _load_json_dict(issued_path)
        for key, meta in data.items():
            issued_count += 1
            if isinstance(meta, dict):
                kind = meta.get("principal_kind") or _infer_kind(str(meta.get("subject", "")))
            else:
                kind = "agent"
            if kind == "login":
                login_count += 1
            else:
                agent_count += 1

    if revoked_path.is_file():
        rdata = json.loads(revoked_path.read_text(encoding="utf-8"))
        if not isinstance(rdata, list):
            print(f"✗ {revoked_path.name} 形状不是数组（{type(rdata).__name__}），拒写", file=sys.stderr)
            return 2
        revoked_count = len(rdata)

    mode = "APPLY（将写盘）" if args.apply else "dry-run（不写盘）"
    print(f"[{mode}] .auth = {auth_dir}")
    print(f"  将改写 issued_tokens.json：{issued_count} 条（login {login_count} / agent {agent_count}）")
    print(f"  将改写 revoked.json：{revoked_count} 条")
    if not args.apply:
        print("  dry-run：未写任何文件。确认无误后加 --apply。")
        return 0

    # ── 真写：先备份，再原子换 ──
    backup_dir = auth_dir / f"backup-{int(time.time())}"
    backup_dir.mkdir(parents=True, exist_ok=False)

    if issued_path.is_file():
        data = _load_json_dict(issued_path)
        (backup_dir / "issued_tokens.json").write_text(
            issued_path.read_text(encoding="utf-8"), encoding="utf-8"
        )
        new_data: dict[str, object] = {}
        for key, meta in data.items():
            new_key = key if _looks_digest(str(key)) else _digest(str(key))
            if isinstance(meta, dict):
                meta = dict(meta)
                meta.setdefault("principal_kind", _infer_kind(str(meta.get("subject", ""))))
            new_data[new_key] = meta
        _atomic_write_text(issued_path, json.dumps(new_data, ensure_ascii=False, indent=2))

    if revoked_path.is_file():
        rdata = json.loads(revoked_path.read_text(encoding="utf-8"))
        if not isinstance(rdata, list):
            print(f"✗ {revoked_path.name} 形状不是数组，拒写", file=sys.stderr)
            return 2
        (backup_dir / "revoked.json").write_text(
            revoked_path.read_text(encoding="utf-8"), encoding="utf-8"
        )
        new_rev = [t if _looks_digest(str(t)) else _digest(str(t)) for t in rdata]
        _atomic_write_text(revoked_path, json.dumps(new_rev, ensure_ascii=False, indent=2))

    print(f"  完成。备份在 {backup_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
