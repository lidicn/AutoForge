#!/usr/bin/env python3
"""IR 运行时扩展键白名单硬门（DCD 裁定 20261005-AF-ir_non_reversible是否升schema）。

裁定：B（豁免 + schema 白名单注释）+ 一条硬约束。
- `_non_reversible / stage / diff_sha / simulate_track / honest_report` 属运行时标注，
  铁律#1 豁免，不进可逆核心；写在 ir.schema.json 的 node 段 `$comment` 白名单里。
- 不在此白名单的运行时键写入 IR 节点，必须先申请 DCD 裁定。
- 白名单是注释不是校验，故本脚本是 B 能成立的前提：代码侧运行时键集合
  （af_irreversible.RUNTIME_ONLY_FIELDS ∪ {NON_REVERSIBLE_KEY}）必须与 schema 白名单一致；
  且写入点源码里不得出现未登记的运行时键字面量。

退出码：0=通过；1=发现未登记键（CI 红）；2=脚本自身环境错误（如 schema 缺失）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCHEMA = REPO / "src" / "autoforge" / "af_ir" / "schema" / "ir.schema.json"

# 代码侧写入 IR 节点的运行时键，集中声明在 af_irreversible 这一处（单一真值源）。
WRITER_SOURCES = [
    REPO / "src" / "autoforge" / "af_irreversible.py",
    REPO / "src" / "autoforge" / "af_evo.py",
    REPO / "src" / "autoforge" / "af_apply.py",
    REPO / "src" / "autoforge" / "af_draft.py",
    REPO / "src" / "autoforge" / "af_ir.py",
]

# 源码里出现的下划线键，但不是"写进 IR 节点的运行时标注"，扫描时豁免：
# - `_evo`：af_evo 写进 IR `meta`/`extra` 的 evol 溯源键（meta 子对象，非节点级运行时标注）。
#   若将来它（或同类键）被移到节点级，必须从本豁免集移除并登记进白名单 + RUNTIME_ONLY_FIELDS。
SAFE_INTERNAL_RUNTIME_KEYS = {"_evo"}


def load_whitelist(schema: Path) -> set[str]:
    """从 node 段 `$comment` 的机器可读尾缀解析白名单键。"""
    doc = json.loads(schema.read_text(encoding="utf-8"))
    # IR 节点定义位于 $defs.node（顶层 properties.nodes 的 item 经 #/$defs/node 引用）。
    comment = doc.get("$defs", {}).get("node", {}).get("$comment", "")
    if not comment:
        raise SystemExit(
            f"[{schema}] node 段缺少 $comment 运行时扩展键白名单（DCD 裁定要求写死）"
        )
    tail = re.split(r"[:：]", comment)[-1]
    keys = set()
    for tok in re.split(r"[/\s]+", tail):
        tok = tok.strip().strip("/")
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", tok):
            keys.add(tok)
    if not keys:
        raise SystemExit(f"[{schema}] $comment 未解析出任何白名单键")
    return keys


def load_code_runtime_keys() -> set[str]:
    """通过 import 拿到单一真值源，避免字符串解析漂移。"""
    sys.path.insert(0, str(REPO / "src"))
    try:
        import autoforge.af_irreversible as mod  # noqa: E402
    finally:
        sys.path.pop(0)
    return set(mod.RUNTIME_ONLY_FIELDS) | {mod.NON_REVERSIBLE_KEY}


def scan_source_literal_keys(paths) -> set[str]:
    """扫描写入点源码里下划线前缀的 dict 键字面量（运行时键约定用 `_xxx`）。"""
    pat = re.compile(r'["\'](_[a-z][a-z0-9_]*)["\']\s*[:=]')
    found = set()
    for p in paths:
        if not p.exists():
            continue
        for m in pat.finditer(p.read_text(encoding="utf-8")):
            found.add(m.group(1))
    return found


def check(whitelist: set[str], code_keys: set[str], source_keys: set[str]) -> list[str]:
    problems: list[str] = []
    # 方向一：代码写了但白名单没登记（真违规）。
    for k in sorted(code_keys - whitelist):
        problems.append(
            f"代码写入未登记运行时键 {k!r}（不在 ir.schema.json 白名单，须先申请 DCD 裁定）"
        )
    # 方向二：白名单列了但代码没用到（漂移，应同步清理或说明）。
    for k in sorted(whitelist - code_keys):
        problems.append(
            f"白名单含代码未声明的键 {k!r}（RUNTIME_ONLY_FIELDS/NON_REVERSIBLE_KEY 缺此键）"
        )
    # 方向三：源码字面量里出现未登记的下划线运行时键。
    for k in sorted(source_keys - whitelist - SAFE_INTERNAL_RUNTIME_KEYS):
        problems.append(
            f"源码出现未登记运行时键字面量 {k!r}（须加入白名单并登记到 RUNTIME_ONLY_FIELDS）"
        )
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--self-test",
        action="store_true",
        help="负控：注入一个未登记键，断言脚本判红后退出 0",
    )
    args = ap.parse_args()

    whitelist = load_whitelist(SCHEMA)
    code_keys = load_code_runtime_keys()
    source_keys = scan_source_literal_keys(WRITER_SOURCES)

    if args.self_test:
        # 证明检测器真能抓到未登记键：故意加一个并断言被检出。
        bogus = "_bogus_marker"
        problems = check(whitelist, code_keys | {bogus}, source_keys | {bogus})
        if any(bogus in p for p in problems):
            print(f"[self-test] OK：未登记键 {bogus!r} 被检出（共 {len(problems)} 条问题）")
            return 0
        print(f"[self-test] FAIL：未登记键 {bogus!r} 未被检出，检测器失效")
        return 1

    problems = check(whitelist, code_keys, source_keys)
    if problems:
        print("IR 运行时扩展键白名单校验未通过：")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(
        f"IR 运行时扩展键白名单校验通过（白名单 {sorted(whitelist)}，代码 {sorted(code_keys)}）"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
