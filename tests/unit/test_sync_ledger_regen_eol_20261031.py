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


# ── 2026-11-11 PM 卡：另一个 `--write` 生成器（信任边界清单）同一条纪律 ──────────
#
# 病根（发版前实跑红）：`scripts/check_trust_boundary.py` 的 `write_doc` 旧实现是
# `open(doc_p, "w", encoding="utf-8", newline="\n")`——**无条件按 LF 写盘**，于是每跑
# 一次 `--write` 就把这份 CRLF 台账整份翻面。v1.29 批 1 `15f7d2c` 的
# `docs/信任边界清单.md` 就是这么造出 196 行假 diff 的（`eac7152` 上还是 CRLF 191、在册）。
# 下面四条腿把这个生成器钉到与 `regen()`／`realign()` 同一口径上。

TB = REPO / "scripts" / "check_trust_boundary.py"


def _load_trust_boundary(tmp_path, monkeypatch):
    """加载信任边界门，路由表与渲染都换成桩（本文件只钉行尾，不钉路由）。"""
    spec = importlib.util.spec_from_file_location("check_trust_boundary_eol_ut", TB)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    (tmp_path / "docs").mkdir(exist_ok=True)
    monkeypatch.setattr(mod, "derive_rows", lambda root: ([("GET /x", "read")], []))
    monkeypatch.setattr(mod, "render_table", lambda rows: ["| `GET /x` | read |"])
    return mod


def _tb_doc(tmp_path):
    return tmp_path / "docs" / "信任边界清单.md"


def _tb_bytes(mod, manual_head="手工甲", manual_tail="手工乙", eol=b"\r\n"):
    body = (
        manual_head.encode("utf-8") + eol
        + mod.AUTO_BEGIN.encode("utf-8") + eol
        + "旧表行".encode("utf-8") + eol
        + mod.AUTO_END.encode("utf-8") + eol
        + manual_tail.encode("utf-8") + eol
    )
    return body


def test_trust_boundary_write_keeps_existing_crlf(tmp_path, monkeypatch):
    """CRLF 台账跑 `--write` 后仍是 CRLF，且只动自动段那几行（手工段一个字节不碰）。"""
    mod = _load_trust_boundary(tmp_path, monkeypatch)
    doc = _tb_doc(tmp_path)
    doc.write_bytes(_tb_bytes(mod))
    assert mod.write_doc(tmp_path) == 0
    out = doc.read_bytes()
    assert out.count(b"\r\n") > 0, "整份被翻成 LF 了（假 diff 复发）"
    assert out.count(b"\n") == out.count(b"\r\n"), "CRLF 台账里混进了裸 LF"
    assert "手工甲".encode("utf-8") + b"\r\n" in out
    assert "手工乙".encode("utf-8") + b"\r\n" in out
    assert b"| `GET /x` | read |" in out
    assert "旧表行".encode("utf-8") not in out, "自动段没被重生成"


def test_trust_boundary_write_keeps_existing_lf(tmp_path, monkeypatch):
    """LF 台账保持 LF（⛔ 别为了修 CRLF 那一侧把 LF 这侧翻过去）。"""
    mod = _load_trust_boundary(tmp_path, monkeypatch)
    doc = _tb_doc(tmp_path)
    doc.write_bytes(_tb_bytes(mod, eol=b"\n"))
    assert mod.write_doc(tmp_path) == 0
    out = doc.read_bytes()
    assert out.count(b"\r\n") == 0
    assert b"| `GET /x` | read |" in out


def test_trust_boundary_write_rejects_mixed_eol_fail_closed(tmp_path, monkeypatch):
    """混合行尾＝非法输入，拒绝写且**一个字节都不落盘**（⛔ 不自动归一）。"""
    mod = _load_trust_boundary(tmp_path, monkeypatch)
    doc = _tb_doc(tmp_path)
    mixed = (
        "手工甲\r\n".encode("utf-8")
        + mod.AUTO_BEGIN.encode("utf-8") + b"\n"
        + "旧表行\n".encode("utf-8")
        + mod.AUTO_END.encode("utf-8") + b"\r\n"
    )
    doc.write_bytes(mixed)
    raised = ""
    try:
        mod.write_doc(tmp_path)
    except mod.MixedEolError as exc:
        raised = str(exc)
    assert "混合" in raised and "拒绝写" in raised
    assert doc.read_bytes() == mixed, "拒绝写却动了盘"


def test_trust_boundary_write_round_trips_real_repo_doc():
    """真仓那份台账必须是纯一种行尾（混合就说明又有人手改过），⛔ 不是混合。"""
    doc = REPO / "docs" / "信任边界清单.md"
    raw = doc.read_bytes()
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n")
    assert crlf == 0 or crlf == lf, (
        f"`docs/信任边界清单.md` 行尾混合（CRLF {crlf} 行／换行 {lf} 个）——"
        "先人工归一行尾，⛔ 靠生成器自动归一")

