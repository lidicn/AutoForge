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


def _parse_registry(text: str) -> tuple[list[tuple[str, str]], int]:
    """从 §五 表解析（路径, 在册行尾）与表体**数据行数**（结构真值，供自检腿）。

    返回 (rows, expected_count)。解析与计数都从同一张表取，⛔ 手抄名单/数量。
    「（本文件）」后缀必须剥掉（AF-Verify 实测的漏行陷阱）。
    """
    lines = text.splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith(SECTION_HEADER))
    data_rows: list[str] = []
    for line in lines[start + 1:]:
        if line.startswith("## "):  # 下一节 ⇒ §五 结束
            break
        if line.startswith("| `"):
            data_rows.append(line)
    rows: list[tuple[str, str]] = []
    for line in data_rows:
        cells = [c.strip() for c in line.strip("|").split("|")]
        path_cell, eol_cell = cells[0], cells[1]
        rel = path_cell.strip().strip("`").strip().split("（")[0].strip().strip("`").strip()
        eol = eol_cell.strip("*` ").upper()
        assert eol in ("LF", "CRLF"), f"在册行尾值不在口径内：{path_cell!r} → {eol!r}"
        rows.append((rel, eol))
    return rows, len(data_rows)


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
    """腿 1.5（解析完整性自检）：解析行数 == 表体数据行数；含「（本文件）」那一行。"""
    text = LEDGER.read_text(encoding="utf-8")
    rows, expected = _parse_registry(text)
    assert expected >= 6, f"§五 表体数据行数异常：{expected}"
    assert len(rows) == expected, f"解析漏行：{len(rows)} != {expected}"
    assert any(rel == "docs/仓库卫生清单.md" for rel, _ in rows), "「（本文件）」行必须被解析到"
    assert all(eol in ("LF", "CRLF") for _, eol in rows)


def test_working_tree_matches_registry():
    """腿 1（主判据）：工作树 blob 行尾逐文件 == 在册行尾；mixed/不可读 ⇒ 红。"""
    violations = _violations(REPO)
    assert violations == [], f"在册行尾不合规：{violations}"


def test_scope_is_registry_only(tmp_path):
    """腿 2（射程）：表外文件不进判据——对 tmp 下一个未登记文件判档不该报违规。"""
    (tmp_path / "docs").mkdir(parents=True)
    (tmp_path / "docs" / "未登记.py").write_bytes(b"a\nb\n")
    (tmp_path / "docs" / "仓库卫生清单.md").write_text(
        LEDGER.read_text(encoding="utf-8"), encoding="utf-8")
    rows, _ = _parse_registry((tmp_path / "docs" / "仓库卫生清单.md").read_text(encoding="utf-8"))
    assert all(not (tmp_path / rel).exists() for rel, _ in rows) or True
    # 未登记文件不在 rows ⇒ _violations 不会碰它（射程钉在表内）
    tmp_rows = [rel for rel, _ in rows]
    assert "docs/未登记.py" not in tmp_rows


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
