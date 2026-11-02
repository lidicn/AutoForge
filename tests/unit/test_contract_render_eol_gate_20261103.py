# -*- coding: utf-8 -*-
"""契约生成器 `--eol` 行尾根治判据（AF卡_契约生成器eol根治_v120，2026-11-03）。

病灶（v1.19 发版实测）：`render_api_contract.py` 写死 LF 输出，而仓内契约是 CRLF ⇒
重生成＝整文件假 diff（现树复现 **numstat 274/274**；加 `--eol auto` 后同动作降到
**3/3 真差异**）⇒ ⇒ 从此发版要人工记着转行尾（靠人记得＝下次必忘，与契约「靠人记得
更新」同族病根）。

判据（E2–E4 用**桩件源树**跑，⛔ 不碰仓内 `docs/reference/API_CONTRACT.md`——
漂移门规定文档由发布窗口重生成；⛔ 不跑 `*watch*.sh`；E1 主判据的读数在报告里给）：
  E2 目标文件是 LF ⇒ auto 生成后**仍是 LF**（⛔ 不许强翻 CRLF）
  E3 目标文件不存在 ⇒ 生成 LF，不报错
  E4 `--eol crlf`／`--eol lf` 显式各自生效（字节级计数，⛔ 不许报「看起来像」）
  E4b 非法值（如 mixed）⇒ 退出码 2（⛔ 不许静默拿默认跑）
  E5 行尾只影响行尾：**三种模式内容行逐行相等**（⛔ 生成器不许改正文/计数）

被测生成器＝仓内 `scripts/render_api_contract.py` 现读**拷进桩树**（桩树里跑的是
真文件，⛔ 不是测试里复制的第二份实现）。桩源最小但形状真（装饰器＋def＋docstring、
ALL_EVENT_TYPES 常量名表、CHECKS 字典），解析口径与生成器一致。
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
GENERATOR = REPO / "scripts" / "render_api_contract.py"

STUB_API = '''"""桩件：最小 af_api（判据用）。"""
app = None


def _read():
    pass


@app.get("/api/probe", dependencies=[Depends(_read)])
def api_probe() -> dict[str, object]:
    """探针端点（判据桩件）。"""
    return {"ok": True}
'''

STUB_AUDIT = '''A = "alpha_event"
B = "beta_event"
ALL_EVENT_TYPES = (A, B)
'''

STUB_SCANNER = '''CHECKS = {"PROBE_CODE": "探针码（判据桩件）"}
'''


def _make_stub_tree(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """搭桩源树（scripts/ 里放**仓内真生成器的副本**），返回（树根, 契约目标）。"""
    root = tmp_path / "tree"
    (root / "scripts").mkdir(parents=True)
    (root / "src" / "autoforge").mkdir(parents=True)
    (root / "docs" / "reference").mkdir(parents=True)
    shutil.copyfile(GENERATOR, root / "scripts" / "render_api_contract.py")
    src = root / "src" / "autoforge"
    (src / "__init__.py").write_text('__version__ = "0.0.0-stub"\n', encoding="utf-8", newline="\n")
    (src / "af_api.py").write_text(STUB_API, encoding="utf-8", newline="\n")
    (src / "af_audit.py").write_text(STUB_AUDIT, encoding="utf-8", newline="\n")
    (src / "af_scanner.py").write_text(STUB_SCANNER, encoding="utf-8", newline="\n")
    return root, root / "docs" / "reference" / "API_CONTRACT.md"


def _render(root: pathlib.Path, target: pathlib.Path, *extra: str) -> subprocess.CompletedProcess:
    args = [sys.executable, str(root / "scripts" / "render_api_contract.py"),
            "--offline", str(root), *extra, str(target)]
    return subprocess.run(args, capture_output=True, text=True, timeout=120)


def _seed_target(target: pathlib.Path, eol: str) -> bytes:
    """造一个既有目标文件：先跑一次默认生成，再按 eol 归一成目标行尾形状。"""
    root = target.parents[2]
    proc = _render(root, target)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    body = target.read_bytes().replace(b"\r\n", b"\n")
    if eol == "crlf":
        body = body.replace(b"\n", b"\r\n")
    target.write_bytes(body)
    return body


def _counts(data: bytes) -> tuple[int, int]:
    """返回（CRLF 对数, 裸 LF 数）——字节级，⛔ 不许用「看起来像」。"""
    crlf = data.count(b"\r\n")
    lf_total = data.count(b"\n")
    return crlf, lf_total - crlf


def test_e2_auto_keeps_lf_target_lf(tmp_path):
    """E2：目标是 LF ⇒ auto 生成后仍纯 LF（⛔ 不强翻 CRLF）。"""
    root, target = _make_stub_tree(tmp_path)
    _seed_target(target, "lf")
    proc = _render(root, target)  # 默认 --eol auto
    assert proc.returncode == 0, proc.stdout + proc.stderr
    crlf, lf = _counts(target.read_bytes())
    assert crlf == 0 and lf > 0, (crlf, lf)
    assert "eol=lf" in proc.stdout, proc.stdout


def test_e2b_auto_keeps_crlf_target_crlf(tmp_path):
    """E2 反腿：目标是 CRLF ⇒ auto 生成后仍 CRLF（根治 274/274 假 diff 的那一面）。"""
    root, target = _make_stub_tree(tmp_path)
    _seed_target(target, "crlf")
    proc = _render(root, target)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    crlf, bare_lf = _counts(target.read_bytes())
    assert crlf > 0 and bare_lf == 0, (crlf, bare_lf)
    assert "eol=crlf(mode=auto)" in proc.stdout, proc.stdout


def test_e3_absent_target_creates_lf(tmp_path):
    """E3：目标不存在 ⇒ 生成 LF、不报错。"""
    root, target = _make_stub_tree(tmp_path)
    assert not target.exists()
    proc = _render(root, target)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    crlf, lf = _counts(target.read_bytes())
    assert crlf == 0 and lf > 0, (crlf, lf)


def test_e4_explicit_flags_each_take_effect(tmp_path):
    """E4：`--eol crlf`／`--eol lf` 显式各自生效（字节级读数）。"""
    root, target = _make_stub_tree(tmp_path)
    _seed_target(target, "lf")
    proc = _render(root, target, "--eol", "crlf")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    crlf, bare_lf = _counts(target.read_bytes())
    assert crlf > 0 and bare_lf == 0, (crlf, bare_lf)
    assert "eol=crlf(mode=crlf)" in proc.stdout

    proc = _render(root, target, "--eol", "lf")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    crlf, lf = _counts(target.read_bytes())
    assert crlf == 0 and lf > 0, (crlf, lf)
    assert "eol=lf(mode=lf)" in proc.stdout


def test_e5_eol_only_moves_endings_not_content(tmp_path):
    """E5：三种模式**内容行逐行相等**（行尾只影响行尾；⛔ 生成器不改正文）。"""
    root, target = _make_stub_tree(tmp_path)
    texts = {}
    for mode in ("auto", "crlf", "lf"):
        if mode == "crlf":
            _seed_target(target, "lf")
        elif mode == "auto":
            _seed_target(target, "crlf")
        proc = _render(root, target, *(["--eol", mode] if mode != "auto" else []))
        assert proc.returncode == 0, proc.stdout + proc.stderr
        texts[mode] = target.read_bytes().replace(b"\r\n", b"\n")
    assert texts["auto"] == texts["crlf"] == texts["lf"]


def test_e4b_bad_eol_value_exits_two(tmp_path):
    """E4b：非法 `--eol` 值 ⇒ 退出码 2（⛔ 静默拿默认跑＝把工具说 X 当真相）。"""
    root, target = _make_stub_tree(tmp_path)
    _seed_target(target, "lf")
    proc = _render(root, target, "--eol", "mixed")
    assert proc.returncode == 2, proc.returncode
    assert "--eol 只认 auto|crlf|lf" in proc.stdout
