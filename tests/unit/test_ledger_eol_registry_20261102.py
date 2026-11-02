"""「在册行尾合规判据」（AF-502 第四次复发防复发机制，PM 卡 v2）。

解析 `docs/仓库卫生清单.md` §五「仓内在册行尾表」⇒ 逐文件断言「当前工作树 blob 的
行尾 == 在册行尾列」。⛔ 判别只读**在册行尾**列（那一列是判据的锚；同行的「现读」
列是历史快照，读它就等于把判据做成「跟着漂」的形状）。

腿：①逐行合规 ②**解析完整性自检**（「（本文件）」后缀会让常规正则漏行——漏行还
显示绿正是要防的形状）③射程＝表内文件（表外⛔不判）④反向腿（tmp 副本翻行尾必须红，
⛔ 不碰仓内文件）。取数面＝**工作树**（PM 卡 §四）；工作树不可读 ⇒ **判红**（跳过＝漏检）。
⛔ 本文件 LF。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LEDGER = REPO / "docs" / "仓库卫生清单.md"
SECTION_HEADER = "## 五、仓内在册行尾表"
#: 机制 B 的数据行形状（路径 + **LF/CRLF**）——与机制 A 的 split 解析不同源（⛔ 同源即恒真）。
EXPECTED_ROW_RE = re.compile(r"^\|\s*`[^`]+`[^|]*\|\s*\*\*(LF|CRLF)\*\*\s*\|", re.M)


def _parse_registry(text: str) -> tuple[list[tuple[str, str]], int]:
    """从 §五 表解析（路径, 在册行尾）与**应解析行数**（从表结构独立数出，供自检腿）。

    返回 (rows, expected_count)。expected＝§五 表体**所有** `|` 开头的行数 − 表头行 −
    分隔行（⛔ 与 rows 同源会恒真——PM 复核指出的「假牙」；两处必须各算各的）。
    ⛔ 手抄名单/数量都是同族根因；「（本文件）」后缀必须剥净（含尾部反引号，
    AF-Verify 实测的漏行陷阱）。
    """
    lines = text.splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith(SECTION_HEADER))
    sec_lines: list[str] = []
    for line in lines[start + 1:]:
        if line.startswith("## "):  # 分节线是 `## ` 开头，⛔ 不是 `|` 行 ⇒ 窗口在此停
            break
        sec_lines.append(line)
    # 变异⑤ 缺口的堵法（PM 验过）：§五 里「以 | 开头的连续行块」必须恰好 1 个——
    # 表被从中截断 ⇒ 解析与计数**同时**少一行 ⇒ 两路仍一致 ⇒ 完整性腿变瞎还显示绿。
    blocks: list[list[str]] = []
    cur: list[str] = []
    for line in sec_lines:
        if line.startswith("|"):
            cur.append(line)
        elif cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    assert len(blocks) == 1, (
        f"§五 必须恰好一个表格块（实际 {len(blocks)}）——表被从中截断 ⇒ 解析会静默漏行")
    # 「−2」不靠猜：块首格＝文件表头、第 2 行＝分隔行（结构断言，⛔ 不许手写数字）
    first_cells = [c.strip() for c in blocks[0][0].strip("|").split("|")]
    assert first_cells and first_cells[0] == "文件", f"块首行不是表头：{blocks[0][0]!r}"
    assert set(blocks[0][1].replace("|", "").replace("-", "").strip()) == set(),         f"块第 2 行不是分隔行：{blocks[0][1]!r}"
    section_text = "\n".join(sec_lines)
    # 机制 B（计数）：整节正则只认「数据行形状」（路径 + **LF/CRLF**）——与机制 A（split
    # 解析）不是同一套过滤器 ⇒ 恒真不可能（PM 复核口径；⛔ 不许改成 len(数据行) 同源计数）。
    expected = len(EXPECTED_ROW_RE.findall(section_text))
    rows: list[tuple[str, str]] = []
    for line in sec_lines:
        if not line.startswith("|") or line.startswith("| 文件") or set(line.replace("|", "").strip()) <= {"-", " ", ":"}:
            continue  # 表头行 / 分隔行
        cells = [c.strip() for c in line.strip("|").split("|")]
        m = re.search(r"`([^`]+)`", cells[0])
        assert m is not None, f"路径格解析不出：{cells[0]!r}"
        rel = m.group(1)  # 「（本文件）」后缀天然在引号段外 ⇒ 不留尾反引号（陷阱①）
        eol = cells[1].strip("*` ").upper()
        assert eol in ("LF", "CRLF"), f"在册行尾值不在口径内：{cells[0]!r} → {eol!r}"
        rows.append((rel, eol))
    return rows, expected


def _judge(data: bytes) -> str:
    """blob 行尾判档：`LF`/`CRLF`/`mixed`。"""
    crlf = data.count(b"\r\n")
    lf_only = data.count(b"\n") - crlf
    if crlf and lf_only:
        return "mixed"
    return "CRLF" if crlf else "LF"


def _violations(root: Path) -> list[str]:
    """对 root 下每条在册记录判档，返回违规描述（空＝全合规）。"""
    rows, expected = _parse_registry((root / "docs/仓库卫生清单.md").read_text(encoding="utf-8"))
    if len(rows) != expected:
        return [f"§五 解析完整性：表体数据行 {expected} 行，只解析出 {len(rows)} 行（漏行＝判据变瞎）"]
    out: list[str] = []
    for rel, eol in rows:
        p = root / rel
        if not p.is_file():
            out.append(f"{rel}: 工作树不可读（判红——跳过＝漏检）")
            continue
        style = _judge(p.read_bytes())
        if style == "mixed":
            out.append(f"{rel}: mixed 行尾（零混合是卫生底线）")
        elif eol == "LF" and style != "LF":
            out.append(f"{rel}: 在册 LF，现读 {style}")
        elif eol == "CRLF" and style != "CRLF":
            out.append(f"{rel}: 在册 CRLF，现读 {style}")
    return out


def test_registry_parses_six_rows_with_self_file():
    """腿 1.5（解析完整性自检）：解析行数 == 从表结构独立数出的应解析行数；含「（本文件）」行。"""
    text = LEDGER.read_text(encoding="utf-8")
    rows, expected = _parse_registry(text)
    assert expected >= 6, f"§五 表体应解析行数异常：{expected}"
    assert len(rows) == expected, f"解析漏行：{len(rows)} != {expected}"
    assert any(rel == "docs/仓库卫生清单.md" for rel, _ in rows), "「（本文件）」行必须被解析到"
    assert all(eol in ("LF", "CRLF") for _, eol in rows)


def test_registry_paths_resolve_to_real_files():
    """腿 1.6（AF-PM 建议采纳）：每条在册路径必须解析成仓内真实文件——解析缺陷
    （尾部反引号残留一类）会伪装成「工作树不可读」，与真行尾不合规分不开。"""
    rows, _ = _parse_registry(LEDGER.read_text(encoding="utf-8"))
    bad = [rel for rel, _ in rows if not (REPO / rel).is_file()]
    assert bad == [], f"在册路径解析失败（解析缺陷，非行尾问题）：{bad}"


def test_working_tree_matches_registry():
    """腿 1（主判据）：工作树 blob 行尾逐文件 == 在册行尾；mixed/不可读 ⇒ 红。"""
    violations = _violations(REPO)
    assert violations == [], f"在册行尾不合规：{violations}"


def test_scope_is_registry_only(tmp_path):
    """腿 2（射程）：表外文件不进判据——tmp 里一个「未登记」的坏行尾文件，_violations 不得提名它。"""
    (tmp_path / "docs").mkdir(parents=True)
    (tmp_path / "docs" / "未登记.py").write_bytes(b"a\r\nb\r\n")  # 混合行尾，若被纳入必红
    (tmp_path / "docs" / "仓库卫生清单.md").write_text(
        LEDGER.read_text(encoding="utf-8"), encoding="utf-8")
    rows, expected = _parse_registry(
        (tmp_path / "docs" / "仓库卫生清单.md").read_text(encoding="utf-8"))
    assert len(rows) == expected
    # tmp 副本里所有在册文件都缺（不可读 ⇒ 会红）；但违规名单里⛔ 不许出现未登记文件
    violations = _violations(tmp_path)
    assert all("未登记" not in v for v in violations), violations


def test_reverse_leg_flipped_copy_is_caught(tmp_path):
    """腿 3（反向腿）：tmp 副本把某个在册 LF 文件翻成 CRLF ⇒ 判定必须红（⛔ 不碰仓）。"""
    text = LEDGER.read_text(encoding="utf-8")
    (tmp_path / "docs").mkdir(parents=True)
    (tmp_path / "docs" / "仓库卫生清单.md").write_text(text, encoding="utf-8")
    rows, _ = _parse_registry(text)
    lf_rows = [(rel, e) for rel, e in rows if e == "LF"]
    victim = lf_rows[0][0]
    src = REPO / victim
    flipped = src.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    (tmp_path / victim).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / victim).write_bytes(flipped)
    violations = _violations(tmp_path)
    assert any(victim in v for v in violations), f"反向腿必须红：{violations}"
    # 未翻转的其余在册文件在 tmp 副本缺文件 ⇒ 也会红（不可读＝判红）；只断 victim 命中即可
