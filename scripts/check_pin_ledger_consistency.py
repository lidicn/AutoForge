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

#: 进程模型那一对（AF-PM 2026-11-14 批 4）：**独立表名**，⛔ 不跟可观测性共用一个名字。
PM_LEDGER_REL = "docs/进程模型清单.md"
PM_PINS_REL = "tests/unit/test_process_model_gate.py"
PM_PINS_NAME = "PINS_PROCESS_MODEL"

AUTO_BEGIN = "<!-- AUTO-BEGIN 由 scripts/check_observability.py --write 生成，勿手改 -->"
AUTO_END = "<!-- AUTO-END -->"
#: 进程模型的自动段标记文案不同（生成器不同），所以按「同时出现 AUTO-BEGIN／AUTO-END」判。
PM_AUTO_BEGIN = "<!-- AUTO-BEGIN 由 scripts/check_process_model.py --write 生成，勿手改 -->"

LEVELS = ("debug", "info", "warning", "error", "exception", "critical")
REQUIRED = ("sites", "files", "levels", "codes", "coded", "dup", "uncoded")
#: 进程模型侧收的五个读数块（**没有** dup／无码格——那两格是日志面的东西，该清单里不存在，
#: 所以条目按实际格数收，⛔ 不硬造 N/A 形状的假键）。
PM_REQUIRED = (
    "lifecycle_counts",
    "primitives",
    "crossproc_by_file",
    "shared",
    "shared_locks",
)

TOTAL_RE = re.compile(r"现读计数：(\d+) 站点／(\d+) 个文件 · (.+)$", re.M)
CODED_RE = re.compile(r"现读计数：具名码 (\d+) 枚／(\d+) 站点 · 无码站点 (\d+) 枚（(.+)）$", re.M)
LEVEL_PAIR_RE = re.compile(r"([A-Z]+)=(\d+)")
ROW_RE = re.compile(r"^\| `(?P<code>[A-Z][A-Z0-9_]+)` \| (?P<level>[A-Z]+) \| `[^`]+` \| `[^`]+` \|$")

#: 进程模型 2.1 计数行：`atexit.register=1 · multiprocessing.Process=0 · …`
PM_KIND_PAIR_RE = re.compile(r"([\w.]+)=(\d+)")
#: 进程模型 2.2 计数行：`进程内 21 站点／14 个文件 · 跨进程 19 站点／8 个文件`
PM_PRIM_RE = re.compile(r"进程内 (\d+) 站点／(\d+) 个文件 · 跨进程 (\d+) 站点／(\d+) 个文件")
#: 进程模型 2.3 计数行：`9 枚名／6 个文件；其中函数体内没有任何 `with` 的 6 枚`
PM_SHARED_RE = re.compile(r"(\d+) 枚名／(\d+) 个文件；其中函数体内没有任何 `with` 的 (\d+) 枚")
#: 进程模型 2.2 表行：`| FileLock | `path:line` | `fn` | `bind` |`
PM_PRIM_ROW_RE = re.compile(r"^\| (?P<prim>[\w.]+) \| `(?P<site>[^`]+)` \| `[^`]+` \| `[^`]*` \|$")
#: 进程模型 2.3 表行：`| `_name` | `path` | fns | 59 | 是/否 | 1 |`
PM_SHARED_ROW_RE = re.compile(
    r"^\| `(?P<name>\w+)` \| `(?P<path>[^`]+)` \| [^|]+ \| [^|]+ \| (?P<with>是|否) \| (?P<locks>\d+) \|$"
)
#: 进程内／跨进程的分族口径：**threading.\* 是进程内，其余是跨进程**。
#: 这不是第二份真源——2.2 计数行的两个总数就是本门的自检器：分族聚合对不上它 ⇒ `exit 2`。
INPROC_PREFIX = "threading."


class RangeError(RuntimeError):
    """射程塌：此刻本门无从判定，⛔ 不许用「没有发现」冒充「没有问题」。"""


def _auto_body(doc: str, *, begin: str = AUTO_BEGIN, rel: str = LEDGER_REL) -> str:
    if begin not in doc or AUTO_END not in doc:
        raise RangeError(f"`{rel}` 的自动段标记缺失或被改写（`AUTO-BEGIN`／`AUTO-END`）")
    return doc.split(begin, 1)[1].split(AUTO_END, 1)[0]


def _levels(raw: str) -> dict[str, int]:
    out = {k.lower(): int(v) for k, v in LEVEL_PAIR_RE.findall(raw)}
    if set(out) != set(LEVELS):
        raise RangeError(f"级别分布读不全（得到 {sorted(out)}，需要 {list(LEVELS)}）：`{raw[:60]}`")
    return out


def parse_ledger(body: str) -> dict:
    """可观测性清单自动段 → 六个读数（⛔ 不碰磁盘：IO 在 `_read_auto_body`）。"""
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


def load_pin_table(root: Path, rel: str, name: str) -> dict:
    """AST 读某份 pin 文件里的模块级 `name = {…}`（纯字面量）。"""
    p = root / rel
    if not p.is_file():
        raise RangeError(f"`{rel}` 不在盘上——pin 表是本门的另一半取数面")
    try:
        tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError, ValueError) as exc:
        raise RangeError(f"`{rel}` 解析不了（pin 表所在文件坏了，本门无从判定）：{exc}") from exc
    node: ast.expr | None = None
    for n in tree.body:  # 只认模块级：局部同名表会让本门读到假值
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == name:
            node = n.value
        elif isinstance(n, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in n.targets
        ):
            node = n.value
    if node is None:
        raise RangeError(f"`{rel}` 里读不出模块级 `{name} = {{…}}`——pin 表被改名或挪进函数了")
    try:
        data = ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError) as exc:
        raise RangeError(f"`{name}` 不是纯字面量（对账门只认字面量，⛔ 不执行代码）：{exc}") from exc
    if not isinstance(data, dict):
        raise RangeError(f"`{name}` 不是 dict（现读 {type(data).__name__}）")
    return data


def parse_pins(root: Path) -> dict:
    data = load_pin_table(root, PINS_REL, PINS_NAME)
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


# ── 进程模型清单（批 4）：独立解析器，⛔ 不为对齐去改清单历史形状 ──────────────────────

def parse_process_model(body: str) -> dict:
    """`docs/进程模型清单.md` 自动段 → 五个读数块。

    三张表的形状与可观测性那张**不同构**（2.1 是 kind 计数、2.2 是「进程内／跨进程」两条腿、
    2.3 是「共享名 × 有没有 with × 同模块锁数」），所以这里单独解析，⛔ 不去把清单改成同构。
    每个块都与它自己的计数行**自检**：分族聚合／逐行求和与计数行不符 ⇒ 射程塌（`exit 2`），
    ⛔ 不许把「解析器读歪了」当成「pin 漂了」报红。
    """
    # 只认 2.1 那一行：它形如 `atexit.register=1 · multiprocessing.Process=0 · …`，
    # 与 2.3 表里的 `| … | 59 |` 之类不会混淆（本正则要求带 `=`）。
    kind_line = next((ln for ln in body.splitlines() if ln.startswith("现读计数：") and "·" in ln
                      and "=" in ln and "枚" not in ln), "")
    if not kind_line:
        raise RangeError("2.1 的计数行读不出（`现读计数：atexit.register=1 · …` 形状变了）")
    counts = {k: int(v) for k, v in PM_KIND_PAIR_RE.findall(kind_line)}
    if not counts:
        raise RangeError("2.1 的计数行一枚 `kind=N` 都读不出")

    m2 = PM_PRIM_RE.search(body)
    if not m2:
        raise RangeError("2.2 的计数行读不出（`进程内 X 站点／Y 个文件 · 跨进程 Z 站点／W 个文件` 形状变了）")
    inproc_sites, inproc_files, cross_sites, cross_files = (int(g) for g in m2.groups())

    prim_rows = [r for r in (PM_PRIM_ROW_RE.match(ln) for ln in body.splitlines()) if r]
    if not prim_rows:
        raise RangeError("2.2 的原语站点表一行都读不出（表格列形状变了，或表被清空）")
    inproc, cross = [], []
    for r in prim_rows:
        (inproc if r.group("prim").startswith(INPROC_PREFIX) else cross).append(r.group("site"))

    # 自检：分族聚合必须与计数行逐项相等，否则是解析口径断了（⛔ 不是 pin 漂）
    got = (len(inproc), len({s.split(":")[0] for s in inproc}),
           len(cross), len({s.split(":")[0] for s in cross}))
    if got != (inproc_sites, inproc_files, cross_sites, cross_files):
        raise RangeError(
            f"2.2 表按族聚合 {got} 与计数行 {(inproc_sites, inproc_files, cross_sites, cross_files)} 不符"
            f"——分族口径（`{INPROC_PREFIX}*`＝进程内）与清单不再一致，先对齐口径再谈 pin"
        )
    cross_by_file = Counter(s.split(":")[0] for s in cross)

    m3 = PM_SHARED_RE.search(body)
    if not m3:
        raise RangeError("2.3 的计数行读不出（`N 枚名／M 个文件；其中函数体内没有任何 \\`with\\` 的 K 枚` 形状变了）")
    shared_names, shared_files, shared_no_with = (int(g) for g in m3.groups())

    shared_rows = [r for r in (PM_SHARED_ROW_RE.match(ln) for ln in body.splitlines()) if r]
    if not shared_rows:
        raise RangeError("2.3 的共享名表一行都读不出（表格列形状变了，或表被清空）")
    if len(shared_rows) != shared_names or len({r.group("path") for r in shared_rows}) != shared_files:
        raise RangeError(
            f"2.3 表 {len(shared_rows)} 行／{len({r.group('path') for r in shared_rows})} 文件"
            f" 与计数行 {shared_names} 枚名／{shared_files} 个文件 不符——要么表少了行，要么计数行过期"
        )
    no_with = sum(1 for r in shared_rows if r.group("with") == "否")
    if no_with != shared_no_with:
        raise RangeError(f"2.3 表里「函数体内无 with」的 {no_with} 枚 与计数行 {shared_no_with} 枚 不符")

    return {
        "lifecycle_counts": counts,
        "primitives": {
            "inproc_sites": inproc_sites,
            "inproc_files": inproc_files,
            "crossproc_sites": cross_sites,
            "crossproc_files": cross_files,
        },
        "crossproc_by_file": dict(cross_by_file),
        "shared": {"names": shared_names, "files": shared_files, "no_with": shared_no_with},
        "shared_locks": {f"{r.group('path')}::{r.group('name')}": int(r.group("locks"))
                         for r in shared_rows},
    }


def parse_pins_process_model(root: Path) -> dict:
    data = load_pin_table(root, PM_PINS_REL, PM_PINS_NAME)
    missing = [k for k in PM_REQUIRED if k not in data]
    if missing:
        raise RangeError(f"`{PM_PINS_NAME}` 缺读数块：{missing}（本门认 {list(PM_REQUIRED)}）")
    for key in PM_REQUIRED:
        if not isinstance(data[key], dict):
            raise RangeError(f"`{PM_PINS_NAME}[{key!r}]` 必须是 dict（逐格对撞）")
    return data


def compare_process_model(ledger: dict, pins: dict) -> list[str]:
    """进程模型侧：五个读数块逐格对撞（表格形状不同构，所以单独一版，⛔ 不硬凑同一套措辞）。"""
    out: list[str] = []

    def fixme(key: str, sub: str = "") -> str:
        where = f"{PM_PINS_NAME}[{key!r}]" + (f"[{sub!r}]" if sub else "")
        return (
            f"——清单是 `--write` 生成的现状，`{where}` 是人写过并认过的数。"
            f"改清单就把 `{PM_PINS_NAME}` 一起改（⛔ 别只改一处）。"
        )

    life, life_pin = ledger["lifecycle_counts"], pins["lifecycle_counts"]
    for kind in sorted(set(life_pin)):
        if kind not in life:
            out.append(
                f"A：`lifecycle_counts` 里 pin 的 `{kind}` 在清单 2.1 计数行读不出"
                f"（清单现在只有 {sorted(life)}）——要么这枚 kind 整族消失了，要么 pin 写过时了"
            )
            continue
        if life[kind] != life_pin[kind]:
            out.append(
                f"A：`lifecycle_counts[{kind!r}]` 清单自动段＝{life[kind]}，"
                f"pin＝{life_pin[kind]}{fixme('lifecycle_counts', kind)}"
            )

    for sub, want in ledger["primitives"].items():
        got = pins["primitives"].get(sub)
        if got != want:
            out.append(f"A：`primitives[{sub!r}]` 清单自动段＝{want}，pin＝{got}{fixme('primitives', sub)}")

    cross_pin = pins["crossproc_by_file"]
    for path, want_n in sorted(cross_pin.items()):
        got_n = ledger["crossproc_by_file"].get(path, 0)
        if got_n != want_n:
            out.append(
                f"A：`crossproc_by_file[{path!r}]` 清单 2.2 表按文件聚合＝{got_n}，"
                f"pin＝{want_n}{fixme('crossproc_by_file', path)}"
            )

    for sub, want in ledger["shared"].items():
        got = pins["shared"].get(sub)
        if got != want:
            out.append(f"A：`shared[{sub!r}]` 清单自动段＝{want}，pin＝{got}{fixme('shared', sub)}")

    locks, locks_pin = ledger["shared_locks"], pins["shared_locks"]
    for key in sorted(set(locks_pin)):
        if key not in locks:
            out.append(
                f"A：`shared_locks` 里 pin 的 `{key}` 在清单 2.3 表读不出"
                f"（那一行被删或改名了）——过期 pin 要跟着动"
            )
            continue
        if locks[key] != locks_pin[key]:
            out.append(
                f"A：`shared_locks[{key!r}]` 清单 2.3 表＝{locks[key]}，pin＝{locks_pin[key]}"
                f"{fixme('shared_locks', key)}"
            )
    return out


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


#: 台账注册表：一个条目＝一份清单 ↔ 它的 pin 文件。新增台账就在这里加一行（⛔ 不共用一个 pin 表名）。
LEDGERS: tuple[dict, ...] = (
    {
        "key": "observability",
        "label": "可观测性清单",
        "ledger_rel": LEDGER_REL,
        "auto_begin": AUTO_BEGIN,
        "pins_rel": PINS_REL,
        "pins_name": PINS_NAME,
        "readings": "6 读数（sites／files／levels／具名码枚数＋站点数／重码集／无码存量）",
        "parse": parse_ledger,
        "pins_parse": parse_pins,
        "compare": compare,
    },
    {
        "key": "process_model",
        "label": "进程模型清单",
        "ledger_rel": PM_LEDGER_REL,
        "auto_begin": PM_AUTO_BEGIN,
        "pins_rel": PM_PINS_REL,
        "pins_name": PM_PINS_NAME,
        "readings": (
            "5 读数块（2.1 生命周期各类计数／2.2 进程内·跨进程站点与文件／2.2 跨进程按文件／"
            "2.3 共享名·文件·无 with 枚数／2.3 逐名同模块锁数；dup 与无码格该清单没有 ⇒ N/A）"
        ),
        "parse": parse_process_model,
        "pins_parse": parse_pins_process_model,
        "compare": compare_process_model,
    },
)


def _summary(entry: dict, ledger: dict) -> str:
    if entry["key"] == "observability":
        return (
            f"站点 {ledger['sites']}／文件 {ledger['files']}／{_fmt_levels(ledger['levels'])}"
            f"／具名码 {ledger['codes']} 枚·{ledger['coded']} 站点／重码 {len(ledger['dup'])} 枚"
            f"／无码 {ledger['uncoded_total']} 枚"
        )
    prim = ledger["primitives"]
    shared = ledger["shared"]
    return (
        f"生命周期 {sum(ledger['lifecycle_counts'].values())} 站点／{len(ledger['lifecycle_counts'])} 类"
        f"／进程内 {prim['inproc_sites']} 站点·{prim['inproc_files']} 文件"
        f"·跨进程 {prim['crossproc_sites']} 站点·{prim['crossproc_files']} 文件"
        f"／共享名 {shared['names']} 枚·{shared['files']} 文件（无 with {shared['no_with']} 枚）"
        f"／逐名锁数 {len(ledger['shared_locks'])} 格"
    )


def run_entry(entry: dict, root: Path) -> tuple[list[str], str]:
    ledger_p = root / entry["ledger_rel"]
    if not ledger_p.is_file():
        raise RangeError(f"`{entry['ledger_rel']}` 不在盘上——清单本身就是本门的取数面")
    body = _auto_body(
        ledger_p.read_text(encoding="utf-8", errors="replace"),
        begin=entry["auto_begin"],
        rel=entry["ledger_rel"],
    )
    ledger = entry["parse"](body)
    pins = entry["pins_parse"](root)
    findings = [f"[{entry['label']}] {f}" for f in entry["compare"](ledger, pins)]
    return findings, _summary(entry, ledger)


def check(root: Path, only: str | None = None) -> tuple[list[dict], list[str]]:
    """返回 (每个条目的读数摘要, 全部 findings)。"""
    summaries: list[dict] = []
    findings: list[str] = []
    for entry in LEDGERS:
        if only and entry["key"] != only:
            continue
        one, summary = run_entry(entry, root)
        findings.extend(one)
        summaries.append({"entry": entry, "summary": summary, "findings": one})
    if not summaries:
        raise RangeError(
            f"`--only {only}` 不是已注册的台账条目（本门认 {[e['key'] for e in LEDGERS]}）"
        )
    return summaries, findings


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parent.parent
    rest = [a for a in argv[1:] if not a.startswith("--")]
    only: str | None = None
    if "--only" in argv:
        i = argv.index("--only")
        only = argv[i + 1] if i + 1 < len(argv) else ""
        rest = [a for a in rest if a != only]
    if rest:
        root = Path(rest[0]).resolve()
    try:
        summaries, findings = check(root, only)
    except RangeError as exc:
        print(f"✗ 台账读数 pin 对账门读不出射程：{exc}")
        print(
            "  修法：射程断不是「没问题」。确认清单的两条计数行与表格（`--write` 生成的原文形状）在，"
            "且对应 pin 文件里模块级表（`PINS`／`PINS_PROCESS_MODEL`）是纯字面量 dict、读数键齐。"
        )
        return 2
    if findings:
        print(f"✗ 台账读数 pin 对账门发现 {len(findings)} 处读数对不上：")
        for f in findings:
            print(f"  · {f}")
        print(
            "  修法：① 刚跑过 `--write` ⇒ 把对应 pin 表里被点名的字面改成清单里的新值（这是人手认过的数，"
            "改它就是一次「我确认这个新读数是对的」）；② 没跑过 `--write` 而清单变了 ⇒ 先查是谁改了清单"
            "（自动段是生成物，手改即漂），别用改 pin 的方式让它闭嘴。"
        )
        return 1
    for s in summaries:
        e = s["entry"]
        print(
            f"✓ 台账读数 pin 对账门干净·{e['label']}（{e['readings']}逐项相等：{s['summary']}；"
            f"取数面＝`{e['ledger_rel']}` 自动段 ↔ `{e['pins_rel']}` 的 `{e['pins_name']}`）"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
