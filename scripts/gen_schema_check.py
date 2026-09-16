#!/usr/bin/env python3
"""样例 IR 体检脚本（补 G1 遗留 R5）。

约定（靠文件名区分预期）：
- `examples/ir/invalid_*.json` → **必须**被安全闸拦下（至少一条 error）
- 其余 `examples/ir/*.json`    → **必须**通过安全闸（可以有 warning）

同时输出每条样例的诊断码，便于一眼看出"扫描器到底拦了什么"。

用法：
    python scripts/gen_schema_check.py
    python scripts/gen_schema_check.py --dir examples/ir --verbose
退出码：0 全符合预期；1 有不符合预期的样例。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from autoforge.af_ir import IRValidationError, load_graph  # noqa: E402
from autoforge.af_scanner import StaticScanner  # noqa: E402

INVALID_PREFIX = "invalid_"


def check_one(path: Path, verbose: bool = False) -> tuple[bool, str]:
    """返回 (是否符合预期, 摘要文本)。"""
    name = path.name
    expect_invalid = name.startswith(INVALID_PREFIX)

    try:
        graph = load_graph(path)
    except IRValidationError as exc:
        # Schema 就没过：对 invalid_* 也算"被拦下"，对正常样例则是失败
        return expect_invalid, f"Schema 拒绝：{str(exc)[:80]}"

    scan = StaticScanner(graph).scan()
    codes = sorted(scan.codes())

    if expect_invalid:
        ok = not scan.ok
        reason = "必须被拦下" if ok else "预期被拦下却通过了"
    else:
        ok = scan.ok
        reason = "通过" if ok else f"不该有 error：{sorted({d.code for d in scan.errors})}"

    summary = f"{name:<38} {'✅' if ok else '❌'} {reason}"
    if codes:
        summary += f"\n{'':<40}诊断码：{', '.join(codes)}"
    if verbose and scan.warnings:
        for w in scan.warnings:
            summary += f"\n{'':<40}· {w.code}: {w.message}"
    return ok, summary


def main() -> int:
    parser = argparse.ArgumentParser(description="校验 examples/ir 下所有样例的通过/拒绝是否符合预期")
    parser.add_argument("--dir", default=str(ROOT / "examples" / "ir"), help="样例目录")
    parser.add_argument("--verbose", action="store_true", help="同时打印 warning 明细")
    args = parser.parse_args()

    directory = Path(args.dir)
    files = sorted(directory.glob("*.json"))
    if not files:
        print(f"目录里没有样例：{directory}")
        return 1

    print(f"样例体检：{directory}（共 {len(files)} 份）\n")
    results = [check_one(p, args.verbose) for p in files]
    for _, text in results:
        print(text)

    failed = [p.name for (ok, _), p in zip(results, files) if not ok]
    print(f"\n—— {len(files) - len(failed)}/{len(files)} 符合预期")
    if failed:
        print(f"不符合预期：{failed}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
