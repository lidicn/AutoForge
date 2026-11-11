"""regen() 行尾归一判据（2026-10-31 卡，v1.15.0 前置）。

`scripts/sync_ledger_anchors.py` 的 regen() 必须按被写文件的现状行尾写盘：现状 LF
仍 LF、现状 CRLF 仍 CRLF、首次生成按生成器 LF 落盘并在输出写明；真实锚点位移只动
锚点那一行、行尾一个字节都不动。旧实现无条件转 CRLF，把两份 LF 台账整文件翻成假
diff（953 行假 diff 三次复发）。本文件与 `scripts/check_ledger_regen_eol.py`
（gates.sh 门位）互为两向：这里从 pytest 面钉行为，那里从门禁链钉射程。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SYNC = REPO / "scripts" / "sync_ledger_anchors.py"

STUB_GENERATOR = (
    "def write_doc(root):\n"
    "    doc = root / 'docs' / '可观测性清单.md'\n"
    "    doc.parent.mkdir(parents=True, exist_ok=True)\n"
    "    doc.write_bytes('# 台账\\n自动行 A\\n自动行 B\\n'.encode('utf-8'))\n"
)


def _load(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("sync_ledger_regen_eol_ut", SYNC)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    (tmp_path / "scripts").mkdir(exist_ok=True)
    (tmp_path / "docs").mkdir(exist_ok=True)
    (tmp_path / "scripts" / "check_observability.py").write_bytes(STUB_GENERATOR.encode("utf-8"))
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    return mod


def _doc(tmp_path):
    return tmp_path / "docs" / "可观测性清单.md"


def test_regen_keeps_existing_lf(tmp_path, monkeypatch):
    mod = _load(tmp_path, monkeypatch)
    doc = _doc(tmp_path)
    doc.write_bytes("旧内容甲\n旧内容乙\n".encode("utf-8"))
    mod.regen("observability")
    out = doc.read_bytes()
    assert out.count(b"\r\n") == 0
    assert "自动行 A".encode("utf-8") in out


def test_regen_restores_existing_crlf(tmp_path, monkeypatch):
    mod = _load(tmp_path, monkeypatch)
    doc = _doc(tmp_path)
    doc.write_bytes("旧内容甲\r\n旧内容乙\r\n".encode("utf-8"))
    mod.regen("observability")
    out = doc.read_bytes()
    assert out.count(b"\n") == out.count(b"\r\n")
    assert out.count(b"\r\n") > 0
    assert "自动行 A".encode("utf-8") + b"\r\n" in out


def test_regen_first_generation_uses_lf_and_says_so(tmp_path, monkeypatch, capsys):
    mod = _load(tmp_path, monkeypatch)
    doc = _doc(tmp_path)
    assert not doc.exists()
    mod.regen("observability")
    out = doc.read_bytes()
    assert out.count(b"\r\n") == 0
    assert "自动行 A".encode("utf-8") in out
    assert "首次生成" in capsys.readouterr().out


def test_regen_second_generation_does_not_say_first(tmp_path, monkeypatch, capsys):
    mod = _load(tmp_path, monkeypatch)
    doc = _doc(tmp_path)
    doc.write_bytes("旧内容甲\n".encode("utf-8"))
    mod.regen("observability")
    assert "首次生成" not in capsys.readouterr().out


def test_realign_anchor_shift_touches_only_that_line_lf(tmp_path, monkeypatch):
    mod = _load(tmp_path, monkeypatch)
    monkeypatch.setattr(
        mod, "_logger_codes",
        lambda: {"DEMOCODE": {"src/autoforge/af_demo.py": [42]}})
    doc = _doc(tmp_path)
    body = ("# 台账\n\n## 三 手工段\n"
            "- `src/autoforge/af_demo.py:10` · code=DEMOCODE 拉起示例\n"
            "旁注行\n")
    doc.write_bytes(body.encode("utf-8"))
    before = doc.read_bytes().split(b"\n")
    fixed = mod.realign("observability")
    after = doc.read_bytes().split(b"\n")
    assert fixed == 1
    assert len(before) == len(after)
    assert [i for i, (x, y) in enumerate(zip(before, after)) if x != y] == [3]
    out = doc.read_bytes()
    assert b":42`" in out and b":10`" not in out
    assert out.count(b"\r\n") == 0


def test_realign_anchor_shift_preserves_crlf(tmp_path, monkeypatch):
    mod = _load(tmp_path, monkeypatch)
    monkeypatch.setattr(
        mod, "_logger_codes",
        lambda: {"DEMOCODE": {"src/autoforge/af_demo.py": [42]}})
    doc = _doc(tmp_path)
    body = ("# 台账\r\n\r\n## 三 手工段\r\n"
            "- `src/autoforge/af_demo.py:10` · code=DEMOCODE 拉起示例\r\n"
            "旁注行\r\n")
    doc.write_bytes(body.encode("utf-8"))
    fixed = mod.realign("observability")
    out = doc.read_bytes()
    assert fixed == 1
    assert out.count(b"\r\n") == body.count("\r\n")
    assert out.count(b"\n") - out.count(b"\r\n") == 0


def test_regen_rejects_mixed_eol_fail_closed(tmp_path, monkeypatch):
    """混合行尾＝非法输入：regen 直接拒（fail-closed），文件字节一个都不动（复核补尺）。"""
    mod = _load(tmp_path, monkeypatch)
    doc = _doc(tmp_path)
    mixed = "甲\r\n乙\n丙\r\n".encode("utf-8")
    doc.write_bytes(mixed)
    before = doc.read_bytes()
    raised = ""
    try:
        mod.regen("observability")
    except mod.MixedEolError as exc:
        raised = str(exc)
    assert "混合" in raised and "拒绝写" in raised
    assert doc.read_bytes() == before


def test_realign_rejects_mixed_eol_fail_closed(tmp_path, monkeypatch):
    """realign 同口径：混合行尾拒写，⛔ 不自动归一到任何一侧。"""
    mod = _load(tmp_path, monkeypatch)
    monkeypatch.setattr(
        mod, "_logger_codes",
        lambda: {"DEMOCODE": {"src/autoforge/af_demo.py": [42]}})
    doc = _doc(tmp_path)
    mixed = ("# 台账\r\n自动行\n- `src/autoforge/af_demo.py:10` · code=DEMOCODE 拉起示例\r\n").encode("utf-8")
    doc.write_bytes(mixed)
    before = doc.read_bytes()
    raised = ""
    try:
        mod.realign("observability")
    except mod.MixedEolError as exc:
        raised = str(exc)
    assert "混合" in raised and "拒绝写" in raised
    assert doc.read_bytes() == before


def test_gate_is_wired_into_gates_sh():
    """独立成档的门必须真在链上跑（与 test_ledger_same_batch_gate 同族）。"""
    body = (REPO / "gates.sh").read_text(encoding="utf-8")
    assert "check_ledger_regen_eol.py" in body, "门没接进 gates.sh"
    assert "ledger_eol_rc=" in body, "接线后没捕 rc"
