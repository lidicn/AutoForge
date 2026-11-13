#!/usr/bin/env python3
"""台账读数 pin 对账门：清单自动段 ↔ pytest 里的 `PINS` 字面，漏改在**本地** gates 就红。

**病根（§1.226）**：新增/移动一枚留痕站点，仓里是**三套**在同步——

1. 台账自动段（`scripts/check_observability.py --write` 重生成）；
2. §三 手工登记（重钉 `file:line` 锚点）；
3. pytest 里的字面 pin（`tests/unit/test_observability_gate.py` 的 `== 263` 那一批，人手改）。

前两套有门看着（A／B／F 三腿 + `check_ledger_same_batch.py`），**第三套此前只有 CI 全量
pytest 兜底**：v1.30.7 推上去才红（`sites` 259 vs 263、重码白名单缺
`SCHED_PREMISE_EVAL_FAILED`），本地 gates 全绿、我自己的窄面也没含那两个文件。
本门把「清单自动段 vs PINS」逐项对撞 ⇒ 漏改在本地就红，不再等 CI。

**为什么 pin 不改成从自动段派生**（AF-PM 2026-11-14 裁定，方案 B）：pin 的价值是
「**有人在这个提交里认过这个数**」。派生会让它与 A 腿同源，任何一次 `--write` 都把两边
一起带平——那是第二次「全绿但没人认过」。所以：数字仍人手写在 `PINS` 里，本门只保证
「人写的那个数**此刻**与清单一致，不一致当场点名改哪一个」。

**射程**（本批＝最小 6 读数，AF-PM 卡面）：

| 读数键 | 清单侧来源 | 说明 |
| --- | --- | --- |
| `sites` | 2.1 计数行 | 全部留痕站点数 |
| `files` | 2.1 计数行 | 站点所在文件数 |
| `levels` | 2.1 计数行 | 六档分布（含 CRITICAL=0） |
| `codes` | 2.2 计数行 | 具名码**枚数** |
| `coded` | 2.2 计数行 | 具名码**站点数**（与 `codes` 同一条计数行，⛔ 别只对一半） |
| `dup` | 2.2 表 | 重码集（`Counter(code) > 1`，逐枚点名的白名单） |
| `uncoded` | 2.2 计数行 | 无码站点按级别（§四 上限就是照这一组钉的） |

`dup`／`uncoded` 是 7 个键里的两格——「6 读数」按卡面口径数：sites／files／levels／
具名码（枚数＋站点数）／重码集／无码存量。HTTP 面与关联 id 面（§2.3／§2.4 那些低漂移读数）
本批**不在射程**，留后一批。

**⛔ 本门不判这些数对不对**——「清单有没有如实反映代码」是 `check_observability.py` 的 A 腿；
- 「§三 登记行的函数归属」是它的 F 腿；「清单与源码是不是同一笔提交」是同批门。
本门只判**第三套有没有跟上**。

退出码就是结论：0 干净 / 1 有对不上的读数 / 2 射程塌（读不出清单、读不出 PINS、清单计数行
被改写）。纯标准库，只读。
"""
from __future__ import annotations

import ast
import re
import sys
from collections import Counter
from pathlib import Path

LEDGER_REL = "docs/可观测性清单.md"
PINS_REL = "tests/unit/test_observability_gate.py"
PINS_NAME = "PINS"
AUTO_BEGIN = "<!-- AUTO-BEGIN 由 scripts/check_observability.py --write 生成，勿手改 -->"
AUTO_END = "<!-- AUTO-END -->"

LEVELS = ("debug", "info", "warning", "error", "exception", "critical")
REQUIRED = ("sites", "files", "levels", "codes", "coded", "dup", "uncoded")

TOTAL_RE = re.compile(r"现读计数：(\d+) 站点／(\d+) 个文件 · (.+)$", re.M)
CODED_RE = re.compile(r"现读计数：具名码 (\d+) 枚／(\d+) 站点 · 无码站点 (\d+) 枚（(.+)）$", re.M)
LEVEL_PAIR_RE = re.compile(r"([A-Z]+)=(\d+)")
ROW_RE = re.compile(r"^\| `(?P<code>[A-Z][A-Z0-9_]+)` \| (?P<level>[A-Z]+) \| `[^`]+` \| `[^`]+` \|$")


class RangeError(RuntimeError):
    """射程塌：此刻本门无从判定，⛔ 不许用「没有发现」冒充「没有问题」。"""


def _auto_body(doc: str) -> str:
    if AUTO_BEGIN not in doc or AUTO_END not in doc:
        raise RangeError(f"`{LEDGER_REL}` 的自动段标记缺失或被改写（`AUTO-BEGIN`／`AUTO-END`）")
    return doc.split(AUTO_BEGIN, 1)[1].split(AUTO_END, 1)[0]


def _levels(raw: str) -> dict[str, int]:
    out = {k.lower(): int(v) for k, v in LEVEL_PAIR_RE.findall(raw)}
    if set(out) != set(LEVELS):
        raise RangeError(f"级别分布读不全（得到 {sorted(out)}，需要 {list(LEVELS)}）：`{raw[:60]}`")
    return out


def parse_ledger(root: Path) -> dict:
    p = root / LEDGER_REL
    if not p.is_file():
        raise RangeError(f"`{LEDGER_REL}` 不在盘上——清单本身就是本门的取数面")
    body = _auto_body(p.read_text(encoding="utf-8", errors="replace"))

    m = TOTAL_RE.search(body)
    if not m:
        raise RangeError("2.1 的计数行读不出（`现读计数：N 站点／M 个文件 · DEBUG=…` 形状变了）")
    sites, files, lv_raw = int(m.group(1)), int(m.group(2)), m.group(3)

    m2 = CODED_RE.search(body)
    if not m2:
        raise RangeError(
            "2.2 的计数行读不出（`现读计数：具名码 X 枚／Y 站点 · 无码站点 N 枚（…）` 形状变了）"
        )
    codes, coded, uncoded_total, unc_raw = (
        int(m2.group(1)), int(m2.group(2)), int(m2.group(3)), m2.group(4)
    )

    rows = [r for r in (ROW_RE.match(line) for line in body.splitlines()) if r]
    if not rows:
        raise RangeError("2.2 的具名码站点表一行都读不出（表格列形状变了，或表被清空）")
    dup = {code for code, n in Counter(r.group("code") for r in rows).items() if n > 1}

    return {
        "sites": sites,
        "files": files,
        "levels": _levels(lv_raw),
        "codes": codes,
        "coded": coded,
        "uncoded_total": uncoded_total,
        "uncoded": _levels(unc_raw),
        "dup": dup,
    }


def parse_pins(root: Path) -> dict:
    p = root / PINS_REL
    if not p.is_file():
        raise RangeError(f"`{PINS_REL}` 不在盘上——pin 表是本门的另一半取数面")
    try:
        tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError, ValueError) as exc:
        raise RangeError(f"`{PINS_REL}` 解析不了（pin 表所在文件坏了，本门无从判定）：{exc}") from exc
    node: ast.expr | None = None
    for n in tree.body:  # 只认模块级：局部同名表会让本门读到假值
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == PINS_NAME:
            node = n.value
        elif isinstance(n, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == PINS_NAME for t in n.targets
        ):
            node = n.value
    if node is None:
        raise RangeError(f"`{PINS_REL}` 里读不出模块级 `{PINS_NAME} = {{…}}`——pin 表被改名或挪进函数了")
    try:
        data = ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError) as exc:
        raise RangeError(f"`{PINS_NAME}` 不是纯字面量（对账门只认字面量，⛔ 不执行代码）：{exc}") from exc
    if not isinstance(data, dict):
        raise RangeError(f"`{PINS_NAME}` 不是 dict（现读 {type(data).__name__}）")
    missing = [k for k in REQUIRED if k not in data]
    if missing:
        raise RangeError(f"`{PINS_NAME}` 缺读数键：{missing}（本门认 {list(REQUIRED)}）")
    for key in ("levels", "uncoded"):
        if not isinstance(data[key], dict):
            raise RangeError(f"`{PINS_NAME}[{key!r}]` 必须是 dict（按级别逐档对撞）")
    if not isinstance(data["dup"], (set, frozenset, list, tuple)):
        raise RangeError(f"`{PINS_NAME}['dup']` 必须是集合／列表（重码逐枚对撞）")
    data["dup"] = set(data["dup"])
    data["uncoded"]["_total"] = None  # 占位：总数由 by-level 求和，不单独手抄（少一处会漂的字面）
    return data


def _fmt_levels(d: dict) -> str:
    return " ".join(f"{k.upper()}={d.get(k, 0)}" for k in LEVELS)


def compare(ledger: dict, pins: dict) -> list[str]:
    out: list[str] = []

    def fixme(key: str) -> str:
        return (
            f"——两边必须逐项相等：清单是 `--write` 生成的现状，`{PINS_NAME}[{key!r}]` 是人写过并认过的数。"
            f"改清单就把 `{PINS_NAME}` 一起改（⛔ 别只改一处），改 pin 就得说清为什么那个人写的数现在才对。"
        )

    for key in ("sites", "files", "codes", "coded"):
        if ledger[key] != pins[key]:
            out.append(f"A：`{key}` 清单自动段＝{ledger[key]}，`{PINS_NAME}`＝{pins[key]}{fixme(key)}")

    for key in ("levels", "uncoded"):
        want, got = ledger[key], pins[key]
        for lv in LEVELS:
            if int(got.get(lv, 0)) != want[lv]:
                out.append(
                    f"A：`{key}` 的 `{lv.upper()}` 清单自动段＝{want[lv]}，"
                    f"`{PINS_NAME}[{key!r}]`＝{int(got.get(lv, 0))}{fixme(key)}"
                )
        if key == "uncoded":
            total = sum(want.values())
            if total != ledger["uncoded_total"]:
                out.append(
                    f"A：2.2 计数行的无码总数 {ledger['uncoded_total']} ≠ 分档求和 {total}"
                    "——清单自己就不自洽（先跑 `--write` 重生成，别在 pin 上凑）"
                )

    if ledger["dup"] != pins["dup"]:
        only_ledger = sorted(ledger["dup"] - pins["dup"])
        only_pins = sorted(pins["dup"] - ledger["dup"])
        bits = []
        if only_ledger:
            bits.append(f"清单有而 `{PINS_NAME}` 没有：{only_ledger}")
        if only_pins:
            bits.append(f"`{PINS_NAME}` 有而清单没有：{only_pins}")
        out.append(
            "A：重码集不一致（" + "；".join(bits) + "）——重码是**逐枚认过的白名单**（每条都要在 "
            "§三 写清几条腿、为什么共用一枚码），所以清单与 pin 的集合必须相等" + fixme("dup")
        )
    return out


def check(root: Path) -> tuple[list[str], str]:
    ledger = parse_ledger(root)
    pins = parse_pins(root)
    findings = compare(ledger, pins)
    summary = (
        f"站点 {ledger['sites']}／文件 {ledger['files']}／{_fmt_levels(ledger['levels'])}"
        f"／具名码 {ledger['codes']} 枚·{ledger['coded']} 站点／重码 {len(ledger['dup'])} 枚"
        f"／无码 {ledger['uncoded_total']} 枚"
    )
    return findings, summary


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parent.parent
    rest = [a for a in argv[1:] if not a.startswith("--")]
    if rest:
        root = Path(rest[0]).resolve()
    try:
        findings, summary = check(root)
    except RangeError as exc:
        print(f"✗ 台账读数 pin 对账门读不出射程：{exc}")
        print(
            "  修法：射程断不是「没问题」。确认 `docs/可观测性清单.md` 的两条计数行与 2.2 表"
            f"（`--write` 生成的原文形状）在，且 `{PINS_REL}` 里模块级 `{PINS_NAME}` 是纯字面量 dict、"
            f"七个读数键齐（{list(REQUIRED)}）。"
        )
        return 2
    if findings:
        print(f"✗ 台账读数 pin 对账门发现 {len(findings)} 处读数对不上：")
        for f in findings:
            print(f"  · {f}")
        print(
            "  修法：① 刚跑过 `--write` ⇒ 把 `PINS` 里对应的字面改成清单里的新值（这是人手认过的数，"
            "改它就是一次「我确认这个新读数是对的」）；② 没跑过 `--write` 而清单变了 ⇒ 先查是谁改了清单"
            "（自动段是生成物，手改即漂），别用改 pin 的方式让它闭嘴。"
        )
        return 1
    print(
        f"✓ 台账读数 pin 对账门干净（6 读数逐项相等：{summary}；取数面＝清单自动段 ↔ `{PINS_REL}` 的 `{PINS_NAME}`）"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
