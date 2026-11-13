# -*- coding: utf-8 -*-
"""AF-774 判据 · gates.sh 解释器误诊与 winpid 平台守卫（2026-11-14，§1.201/§1.204）。

**缺陷一**：`PYTHON="${GATES_PYTHON:-python3}"` 之后直接 `"$PYTHON" -c "import homesdk.gates"`。
解释器**根本不存在**时 bash 报 127，脚本照样打印「homesdk 未安装。先执行 pip install …」——
把人往"重装依赖"上带。Git for Windows 的 bash 里 `python3` 通常不存在（只有 `python`/`py`），
所以这条误诊在现场是**必然复现**的，而且把门禁自己的 RC=2 混进"依赖问题"。

**缺陷二**：`/proc/$$/winpid` 是 MSYS 专属文件。CI 是 `ubuntu-latest`，那里没有它。旧写法靠
`|| echo "$$"` 兜底"能用"，但那是**靠失败命令的后备**，不是按平台选语义。

**修法**：先 `command -v "$PYTHON"` 探存在性（缺了就说缺 + 给 `GATES_PYTHON=` 用法，RC=2）；
winpid 那行加显式 `[ -r ... ]` 平台守卫（MSYS 给 winpid，其它给 `$$`）。⛔ 未动任何门禁判据。

**判据**（本文件是**行为钉**：真起一个 bash 跑脚本，不看源码文本）：

- W1 `GATES_PYTHON=python3`（本机无此解释器）⇒ RC=2 ＋「不在 PATH」＋「用 GATES_PYTHON=」
- W2 文案里⛔ 不许再出现「homesdk 未安装」这类依赖误诊
- W3 winpid 守卫在位：源码里有 `[ -r "/proc/$$/winpid" ]` 分支，且两分支各给一行
- W4 `bash -n` 语法自检通过（门禁脚本改坏=全员红，这条是入口）
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GATES = REPO / "gates.sh"


def _bash() -> str | None:
    for cand in (r"C:\Program Files\Git\bin\bash.exe", "/bin/bash", "/usr/bin/bash"):
        if Path(cand).is_file() or (not cand.startswith("C:") and Path("/bin").is_dir()):
            return cand
    return None


BASH = _bash()
pytestmark = pytest.mark.skipif(BASH is None, reason="本机没有 bash，跳过（CI 上 bash 恒有）")


# ── W1 解释器缺失 ⇒ 新文案 ＋ RC=2 ──────────────────────────────────
def test_w1_missing_interpreter_reports_clearly():
    """判据 a：解释器不在 PATH 时必须说"解释器缺失"，并给出 GATES_PYTHON= 的用法。"""
    r = subprocess.run(
        [BASH, "-c", f'GATES_PYTHON=definitely_no_such_python_zzz "{GATES}"'],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        cwd=str(REPO),
    )
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 2, f"解释器缺失必须是 RC=2（环境错误），实得 {r.returncode}：{out[:300]}"
    assert "不在 PATH" in out, f"必须明说解释器不在 PATH：{out[:300]}"
    assert "GATES_PYTHON=" in out, "必须给出指定解释器的用法"


# ── W2 ⛔ 不许再误诊成依赖没装 ───────────────────────────────────────
def test_w2_no_dependency_misdiagnosis():
    """判据 b（反证腿）：解释器缺失时⛔ 不许把话说到"homesdk 未安装"上。"""
    r = subprocess.run(
        [BASH, "-c", f'GATES_PYTHON=definitely_no_such_python_zzz "{GATES}"'],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        cwd=str(REPO),
    )
    out = (r.stdout or "") + (r.stderr or "")
    assert "homesdk 未安装" not in out, f"⛔ 这是依赖误诊（真凶是解释器不存在）：{out[:300]}"


# ── W3 winpid 平台守卫在位 ──────────────────────────────────────────
def test_w3_winpid_platform_guard_present():
    """判据 c：winpid 那一行必须按平台分支，⛔ 只靠 `||` 兜底。"""
    src = GATES.read_text(encoding="utf-8", errors="replace")
    assert '[ -r "/proc/$$/winpid" ]' in src, "必须有显式平台守卫（MSYS 才有 winpid）"
    assert src.count("printf 'winpid=%s\\n'") == 2, "两个分支各须给一行 winpid（都查得到活 pid）"


# ── W4 语法自检 ─────────────────────────────────────────────────────
def test_w4_bash_syntax_ok():
    """判据（入口守卫）：门禁脚本本身语法必须过——改坏一句就是全员红。"""
    r = subprocess.run([BASH, "-n", str(GATES)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
    assert r.returncode == 0, f"bash -n 失败：{(r.stderr or '')[:400]}"
