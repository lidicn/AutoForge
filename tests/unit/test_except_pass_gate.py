"""check_except_pass.py 门（v1.13.3，autoflow「静默失效」死因的落地）的判据腿。

合成树单红复刻 check_bounded_caches.py 测试模式：每条判据（A 新增/B 豁免两半/
C 基线只减/D 反空洞）用最小合成树钉住"真的会红"，绿腿钉住"现状判绿"。

射程说明：门按 `--src` 指向的树判；合成树没有基线（BASELINE 只在指向真仓
`src/autoforge` 时生效），所以合成树判据 A 的"新增"= 全部站点。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_except_pass.py"
sys.path.insert(0, str(SCRIPT.parent))

import check_except_pass  # noqa: E402


def _run(src: Path, argv: list[str] | None = None) -> int:
    return check_except_pass.main([str(src), *(argv or [])])


def _mk(src: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return src


BROAD_PASS = "try:\n    x = 1\nexcept Exception:\n    pass\n"
NARROW_PASS = "try:\n    x = 1\nexcept KeyError:\n    pass\n"
BARE_PASS = "try:\n    x = 1\nexcept:\n    pass\n"
LOGGED = "try:\n    x = 1\nexcept Exception:\n    logger.debug('skipped')\n"


def test_green_on_clean_synthetic_tree(tmp_path):
    """干净合成树（0 站点）按判据 D 退 2——反空洞对任何树都生效，合成树也不例外。

    真仓的"绿"由 test_real_repo_gate_is_green 钉（基线非空 ⇒ 有站点可判）。
    合成树的绿只能长在"有站点且全部豁免"上（见豁免腿）。
    """
    src = _mk(tmp_path / "src", {"a.py": NARROW_PASS + LOGGED})
    assert _run(src) == 2  # 0 站点 = 判据 D 反空洞，不是干净


def test_A_broad_pass_is_red(tmp_path):
    """判据 A：宽 except+pass 在合成树（无基线）里就是新增 ⇒ 红。"""
    src = _mk(tmp_path / "src", {"a.py": NARROW_PASS + BROAD_PASS})
    assert _run(src) == 1


def test_A_bare_except_and_tuple_with_exception_are_red(tmp_path):
    """裸 except / 元组混 Exception 的宽 handler 同样计入。"""
    src = _mk(tmp_path / "src", {"a.py": BARE_PASS})
    assert _run(src) == 1
    src2 = _mk(tmp_path / "src2", {"a.py": "try:\n    x = 1\nexcept (KeyError, Exception):\n    pass\n"})
    assert _run(src2) == 1


def test_A_exempt_marker_suppresses_when_reason_present(tmp_path):
    """豁免腿：被扫到的那一行带 `# except-pass: exempt(理由)` ⇒ 不红。"""
    marked = "try:\n    x = 1\nexcept Exception:  # except-pass: exempt(旁路容错，失败不拖垮回执)\n    pass\n"
    src = _mk(tmp_path / "src", {"a.py": marked})
    assert _run(src) == 0


def test_B_exempt_with_empty_reason_is_red(tmp_path):
    """判据 B：豁免理由为空 ⇒ 红（理由为空的豁免就是无豁免）。"""
    marked = "try:\n    x = 1\nexcept Exception:  # except-pass: exempt()\n    pass\n"
    src = _mk(tmp_path / "src", {"a.py": marked})
    assert _run(src) == 1


def test_B_exempt_on_unscanned_line_is_red(tmp_path):
    """判据 B：豁免写在扫不到的行（窄 except 行）上 ⇒ 第二份口径 ⇒ 红。"""
    marked = "try:\n    x = 1\nexcept KeyError:  # except-pass: exempt(写错位置)\n    pass\nexcept Exception:\n    pass\n"
    src = _mk(tmp_path / "src", {"a.py": marked})
    assert _run(src) == 1


def test_D_zero_sites_is_exit_2(tmp_path):
    """判据 D 反空洞：0 站点=扫描器塌了，退 2 不许报干净。"""
    src = _mk(tmp_path / "src", {"a.py": "x = 1\n"})
    assert _run(src) == 2


def test_scope_missing_dir_is_exit_2(tmp_path):
    """射程：src 目录不存在 ⇒ 2。"""
    assert _run(tmp_path / "nope") == 2


def test_syntax_error_is_exit_2(tmp_path):
    """射程：任一 .py 解析不了 ⇒ 2（不是 1——那是扫描器的病不是代码的病）。"""
    src = _mk(tmp_path / "src", {"a.py": "def broken(:\n"})
    assert _run(src) == 2


def test_subdirectory_keys_are_relative(tmp_path):
    """键用相对路径：子目录文件不被同名顶层文件吞掉（deepfix.py 首轮踩的形状）。"""
    src = _mk(tmp_path / "src", {
        "a.py": NARROW_PASS,
        "af_closedloop/a.py": BROAD_PASS,  # 子目录里的宽吞——单独成键
    })
    rc = _run(src)
    assert rc == 1
    # print-baseline 能看到相对路径键
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        check_except_pass.main([str(src), "--print-baseline"])
    assert "af_closedloop/a.py:" in buf.getvalue()


def test_real_repo_gate_is_green():
    """真仓现状判绿（基线 16 枚 = 现读 16 枚）。行号漂移后先 --print-baseline 重冻结。
    17 → 16（2026-10-24 AST 门禁收账）：af_api.py:1209 那枚真缺陷修掉（具名捕获+
    UPDATE_PREVIEW_READ_FAILED 留痕），1334 行漂 6 → 1340 重冻结。"""
    repo_src = SCRIPT.parents[1] / "src" / "autoforge"
    if not repo_src.is_dir():
        pytest.skip("real repo src not present")
    assert _run(repo_src) == 0


def test_baseline_frozen_at_15():
    """基线规模钉：15 枚。增减都要走"重冻结+核对同一批"的流程。
    17 → 16（2026-10-24）：-1 是修掉的真缺陷，非静默缩水——见 BASELINE 旁注释。
    16 → 15（2026-10-31 AF-710 留痕半 9ead4f0）：`_pair_event_gen` 的 `except Exception: pass`
    已收窄成 `logger.warning(..., exc_info=True)`＋具名码 PAIR_SSE_EVENT_FAILED，不再是严格吞掉
    形状 ⇒ 该枚从 BASELINE 拿掉（本门明写「真删了就把它们从 BASELINE 拿掉（只减不增）」）。
    -1 仍是修掉的真缺陷，非静默缩水。"""
    assert len(check_except_pass.BASELINE) == 15
    repo_src = SCRIPT.parents[1] / "src" / "autoforge"
    if not repo_src.is_dir():
        pytest.skip("real repo src not present")
    assert _run(repo_src) == 0
