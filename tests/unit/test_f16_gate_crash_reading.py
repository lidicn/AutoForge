"""第十五轮 F16 的 AF 侧半边：`homesdk.gates` 崩掉时，本仓的门禁不许把『崩』读成『违规』或『干净』。

缺陷本体在依赖里（`homesdk/gates/scan.py` 的 `_numeric_literal` / `_dotted` / `_literal_secret`
三个自递归函数没有深度预算），AF 改不了别人仓的源码。AF 能做、也该自决的是**自己那三处调用**的读数口径：
崩溃时 Python 退 1，与"判出违规"同形，而下一个读红的人的常规动作是补基线或调棘轮上限——
**崩掉的门没有产出任何计数，那一按下去留在门禁上的记录是"它绿了"。**

所以这里钉三件事：分类器三档读数、`gates.sh` 真的走它、以及一条**真依赖真崩**的端到端腿。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CLASSIFIER = REPO / "scripts" / "classify_homesdk_run.py"
GATES = REPO / "gates.sh"

sys.path.insert(0, str(CLASSIFIER.parent))
from classify_homesdk_run import classify  # noqa: E402

CRASH_TRANSCRIPT = (
    "Traceback (most recent call last):\n"
    '  File "E:\\NAS\\homesdk\\src\\homesdk\\gates\\scan.py", line 375, in _numeric_literal\n'
    "    return _numeric_literal(node.operand, ...)\n"
    "RecursionError: maximum recursion depth exceeded\n"
)
VIOLATION_TRANSCRIPT = "✗ src/autoforge/af_x.py:12 [AST-07] 裸 except 吞掉了 CancelledError\n新增/未获批 1 条\n"
CLEAN_TRANSCRIPT = "✓ AST 门禁：0 条新增，存量在基线内\n新增/未获批 0 条\n"


def run(text: str, rc: int) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(CLASSIFIER), "--rc", str(rc)],
        input=text, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return proc.returncode, (proc.stdout + proc.stderr).strip()


# ── 三档读数 ──────────────────────────────────────────────────────
def test_crash_with_rc1_is_range_failure_not_a_violation():
    code, message = classify(CRASH_TRANSCRIPT, 1)
    assert code == 2
    assert "不许" in message


def test_crash_is_caught_even_when_the_runner_reports_zero():
    """签名优先于退出码：崩在子线程／被外层 try 吞掉时 rc 可能是 0，读数不能因此变干净。"""
    assert classify(CRASH_TRANSCRIPT, 0)[0] == 2


def test_real_violation_still_reads_red():
    assert classify(VIOLATION_TRANSCRIPT, 1) [0] == 1


def test_clean_run_reads_green():
    assert classify(CLEAN_TRANSCRIPT, 0)[0] == 0


def test_empty_output_is_not_clean():
    """反空转：什么都没读到不是"没违规"，是"没读数"。"""
    assert classify("", 0)[0] == 2
    assert classify("   \n", 0)[0] == 2


def test_gate_own_verdict_format_is_not_mistaken_for_a_crash():
    """`✗` 是依赖门禁自己的判红格式；把它当崩溃签名就会把真红读成无从判定（另一种假绿）。"""
    assert classify(VIOLATION_TRANSCRIPT, 1)[1].startswith("依赖门禁判红")


# ── CLI 口径（gates.sh 走的是这条）────────────────────────────────
def test_cli_exit_codes_match_the_three_tiers():
    assert run(CRASH_TRANSCRIPT, 1)[0] == 2
    assert run(VIOLATION_TRANSCRIPT, 1)[0] == 1
    assert run(CLEAN_TRANSCRIPT, 0)[0] == 0
    assert run("", 0)[0] == 2


def test_cli_prints_the_reading_for_the_human():
    code, out = run(CRASH_TRANSCRIPT, 1)
    assert f"RC={code}" in out, out


# ── 接线：gates.sh 必须真的经过分类器 ─────────────────────────────
def test_gates_sh_routes_the_ast_leg_through_the_classifier():
    text = GATES.read_text(encoding="utf-8")
    assert "classify_homesdk_run.py" in text, "AST 腿又退回裸 $? 了"
    assert "ast_raw_rc" in text and "ast_class_rc" in text


def test_gates_sh_has_a_distinct_conclusion_for_the_crash_reading():
    """补上分类却不解释，读红的人照样去补基线。"""
    text = GATES.read_text(encoding="utf-8")
    assert "[ $ast_rc -eq 2 ]" in text
    assert "update-baseline" in text, "结论里必须点名那件不许做的事"


def _find_bash() -> str | None:
    """本机 `bash` 的第一候选可能是 WSL 占位程序（rc=1 + UTF-16 提示），以自报 GNU bash 为准。"""
    for cand in (os.environ.get("BASH"), shutil.which("bash"),
                 "/bin/bash", r"C:/Program Files/Git/bin/bash.exe"):
        if not cand:
            continue
        try:
            out = subprocess.run([cand, "--version"], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=20)
        except (OSError, subprocess.SubprocessError):
            continue
        if out.returncode == 0 and "GNU bash" in out.stdout:
            return cand
    return None


def test_gates_sh_still_parses_as_bash():
    """找不到 bash 就红，不 skip——skip 等于这条证据不存在（与 §二之五十三 同一口径）。"""
    bash = _find_bash()
    assert bash, "本机取不到 GNU bash，这一腿没有证据"
    proc = subprocess.run([bash, "-n", str(GATES)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ── 端到端：真依赖、真崩、真分类 ──────────────────────────────────
def test_real_homesdk_crash_on_deep_nesting_reads_as_range_failure(tmp_path):
    """F16 的可复现证据：不是照抄审计的说法，是在装好的那份 homesdk 上再崩一次。"""
    pytest.importorskip("homesdk.gates")
    (tmp_path / ".gates.toml").write_text("[gates]\n", encoding="utf-8")
    pkg = tmp_path / "src"
    pkg.mkdir()
    (pkg / "deep.py").write_text("x = " + "-" * 500 + "1\n", encoding="utf-8")
    (pkg / "dotted.py").write_text("y = a" + ".b" * 500 + "\n", encoding="utf-8")

    run_proc = subprocess.run(
        [sys.executable, "-m", "homesdk.gates", str(tmp_path), "--no-baseline", "--no-smoke"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    transcript = run_proc.stdout + run_proc.stderr
    assert run_proc.returncode != 0, "依赖门禁这次没崩——F16 的口径要重新对（可能被库侧修掉了）"
    code, message = run(transcript, run_proc.returncode)
    assert code == 2, f"真崩却被读成 {code}：{message}"
