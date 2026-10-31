#!/usr/bin/env python3
"""台账行尾门禁：`sync_ledger_anchors.py` 的 regen() 必须按被写文件的现状行尾写盘。

2026-10-31 卡（AF-PM，v1.15.0 前置）：regen() 旧实现无条件把重生成结果转 CRLF，而
docs/可观测性清单.md 与 docs/进程模型清单.md 都是 LF、两个 --write 生成器输出的也是
LF ⇒ 跑一次 regen 就整文件翻成 CRLF＝整文件假 diff（953 行假 diff 三次复发），还会把
「移动锚点的提交必须自带重生成清单」的真实锚点位移淹在行尾噪声里。

本门在临时夹具里真跑 regen()/realign()，五条腿都是行为判据（不靠注释自证）：
  A 现状 LF 的台账，regen 后仍是 LF（旧实现必红：它会把这一腿写成 CRLF）；
  B 现状 CRLF 的台账，regen 后仍是 CRLF（按现状风格还原，堵「一刀切全 LF」的反向过修）；
  C 写前不存在的台账（首次生成），按生成器的 LF 落盘；
  D 真实锚点位移（手工段行号漂了）：realign 只改锚点那一行，行尾一个字节都不动
    （LF 与 CRLF 两种台账各跑一遍）；
  E 混合行尾（CRLF 与裸 LF 并存）＝非法输入：regen/realign 直接拒（fail-closed，
    报「该文件行尾混合、拒绝写」），文件字节一个都不动——⛔ 不自动归一到任何一侧
    （复核补尺：AF-Verify 三 fixture ＋ Chief 定口径，自动归一＝本卡病灶）。

退出码：0 过 / 1 判据红 / 2 射程塌（被测脚本不在、夹具建不出来、真值加载不了）。
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYNC = ROOT / "scripts" / "sync_ledger_anchors.py"

# 夹具生成器：模拟 check_observability 的 --write 面——整份按 LF 重写台账。
STUB_GENERATOR = '''\
def write_doc(root):
    doc = root / "docs" / "可观测性清单.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_bytes(
        "# 台账\\n\\n## 一 自动段\\n自动行 A\\n自动行 B\\n\\n## 三 手工段\\n".encode("utf-8")
    )
'''

MANUAL_ROW = "- `src/autoforge/af_demo.py:10` · code=DEMOCODE 拉起示例\n"


def _load_sync(tmp: Path):
    """把仓里那份 sync_ledger_anchors.py 装进独立模块实例，ROOT 指向夹具。"""
    spec = importlib.util.spec_from_file_location("sync_ledger_under_gate", SYNC)
    if spec is None or spec.loader is None:
        raise RuntimeError("spec_from_file_location 读不出加载器")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    scripts = tmp / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (tmp / "docs").mkdir(parents=True, exist_ok=True)
    (scripts / "check_observability.py").write_bytes(STUB_GENERATOR.encode("utf-8"))
    mod.ROOT = tmp
    return mod


def _doc(tmp: Path) -> Path:
    return tmp / "docs" / "可观测性清单.md"


def _crlf_count(raw: bytes) -> int:
    return raw.count(b"\r\n")


def _lone_lf_count(raw: bytes) -> int:
    return raw.count(b"\n") - raw.count(b"\r\n")


def leg_a_lf_stays_lf(tmp: Path) -> None:
    mod = _load_sync(tmp)
    doc = _doc(tmp)
    doc.write_bytes("旧内容甲\n旧内容乙\n".encode("utf-8"))
    mod.regen("observability")
    out = doc.read_bytes()
    assert "自动行 A".encode("utf-8") in out, "regen 后自动段不在——夹具生成器没被跑到，本腿无从判定"
    assert _crlf_count(out) == 0 and _lone_lf_count(out) > 0, (
        "现状 LF 的台账被 regen 翻出 CRLF＝整文件假 diff（旧病灶复发）")


def leg_b_crlf_stays_crlf(tmp: Path) -> None:
    mod = _load_sync(tmp)
    doc = _doc(tmp)
    doc.write_bytes("旧内容甲\r\n旧内容乙\r\n".encode("utf-8"))
    mod.regen("observability")
    out = doc.read_bytes()
    assert _lone_lf_count(out) == 0 and _crlf_count(out) > 0, (
        "现状 CRLF 的台账被 regen 改成 LF——『按被写文件现状行尾还原』未落地")
    assert "自动行 A".encode("utf-8") + b"\r\n" in out, "CRLF 还原丢了内容"


def leg_c_first_generation_lf(tmp: Path) -> None:
    mod = _load_sync(tmp)
    doc = _doc(tmp)
    assert not doc.exists(), "夹具里台账不应预存（首次生成腿的前提）"
    mod.regen("observability")
    out = doc.read_bytes()
    assert _crlf_count(out) == 0, "首次生成应按生成器的 LF 落盘"
    assert "自动行 A".encode("utf-8") in out


def leg_d_anchor_shift_touches_one_line(tmp: Path) -> None:
    mod = _load_sync(tmp)
    doc = _doc(tmp)
    body = "# 台账\n\n## 三 手工段\n" + MANUAL_ROW + "旁注行\n"
    truth = {"DEMOCODE": {"src/autoforge/af_demo.py": [42]}}
    mod._logger_codes = lambda: truth  # 真值源换夹具，⛔ 不扫真仓

    # LF 腿：锚点行号漂了（10 → 真值 42），realign 只许动这一行、行尾零翻转。
    doc.write_bytes(body.encode("utf-8"))
    before = doc.read_bytes()
    fixed = mod.realign("observability")
    after = doc.read_bytes()
    assert fixed == 1, f"应只修 1 行，实修 {fixed}"
    before_lines, after_lines = before.split(b"\n"), after.split(b"\n")
    assert len(before_lines) == len(after_lines), "行数变了＝不是单行位移"
    diffs = [i for i, (x, y) in enumerate(zip(before_lines, after_lines)) if x != y]
    assert diffs == [3], f"应只动锚点行（第 4 行），实际动了 {diffs}"
    assert b":42`" in after and b":10`" not in after, "锚点没有指到真值行号"
    assert _crlf_count(after) == 0, "锚点位移顺带翻转了行尾＝假 diff 淹掉真位移"

    # CRLF 腿：同样的位移在 CRLF 台账上也不许翻行尾。
    doc.write_bytes(body.replace("\n", "\r\n").encode("utf-8"))
    before = doc.read_bytes()
    fixed = mod.realign("observability")
    after = doc.read_bytes()
    assert fixed == 1, f"CRLF 腿：应只修 1 行，实修 {fixed}"
    assert _crlf_count(after) == _crlf_count(before) and _lone_lf_count(after) == 0, (
        "CRLF 台账的锚点位移把整文件行尾翻了")


def leg_e_mixed_rejected(tmp: Path) -> None:
    mod = _load_sync(tmp)
    doc = _doc(tmp)
    mixed = "甲\r\n乙\n丙\r\n".encode("utf-8")
    doc.write_bytes(mixed)
    before = doc.read_bytes()
    raised = ""
    try:
        mod.regen("observability")
    except mod.MixedEolError as exc:
        raised = str(exc)
    assert "混合" in raised and "拒绝写" in raised, (
        f"混合行尾未被拒（fail-closed 失守）：{raised!r}")
    assert doc.read_bytes() == before, "regen 拒写后文件字节被改了"
    mod._logger_codes = lambda: {}  # 真值源换夹具（同 leg D），⛔ 不扫真仓
    try:
        mod.realign("observability")
    except mod.MixedEolError as exc:
        raised = str(exc)
    assert "混合" in raised and "拒绝写" in raised, (
        f"realign 对混合行尾未拒（fail-closed 失守）：{raised!r}")
    assert doc.read_bytes() == before, "realign 拒写后文件字节被改了"


LEGS = [
    ("A 现状 LF 仍 LF", leg_a_lf_stays_lf),
    ("B 现状 CRLF 仍 CRLF", leg_b_crlf_stays_crlf),
    ("C 首次生成按 LF", leg_c_first_generation_lf),
    ("D 锚点位移只动一行（LF+CRLF）", leg_d_anchor_shift_touches_one_line),
    ("E 混合行尾拒写（fail-closed）", leg_e_mixed_rejected),
]


def main(argv: list[str]) -> int:
    if not SYNC.exists():
        print(f"[ledger-eol] 读不到被测脚本：{SYNC}——射程塌了，不许报『干净』")
        return 2
    failed: list[str] = []
    for label, leg in LEGS:
        with tempfile.TemporaryDirectory(prefix="af_ledger_eol_") as td:
            try:
                leg(Path(td))
            except AssertionError as exc:
                failed.append(label)
                print(f"[ledger-eol] 腿 {label} 红：{exc}")
            except (OSError, RuntimeError, SyntaxError) as exc:
                print(f"[ledger-eol] 腿 {label} 无从判定（射程塌）：{exc!r}")
                return 2
    if failed:
        print(f"[ledger-eol] 结论：红（红腿：{'、'.join(failed)}）。"
              "regen() 必须按被写文件现状行尾写盘，修 scripts/sync_ledger_anchors.py，"
              "⛔ 不许在门禁夹具上放水。")
        return 1
    print(f"[ledger-eol] 结论：干净（{len(LEGS)} 条腿全绿：LF 仍 LF／CRLF 仍 CRLF／"
          "首次生成 LF／锚点位移只动一行／混合行尾拒写）。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
