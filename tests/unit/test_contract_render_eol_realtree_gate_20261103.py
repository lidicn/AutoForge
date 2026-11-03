# -*- coding: utf-8 -*-
"""契约生成器**真树差分**判据（§1.127 遗留补腿 · AF-Verify 出）。

背景：EOL 根治判据（`test_contract_render_eol_gate_20261103.py`）的 E2–E5 用**桩件源树**跑，
「**真树重生成 ⇒ 逐字节幂等（numstat 0/0）**」这条只写在票里、没进判据 ⇒ 下任看不到（§1.127）。
本文件把它补成带 `gate` 的判据：在**真树**上跑**真生成器**，断言重生成结果与仓内契约
**逐字节相同**（⛔ 不是「看起来像」）。

为什么必须真树：桩树只证明「生成器逻辑能判行尾」，不证明「仓内契约与源码同步、重生成幂等」。
v1.19 的病灶正是「重生成＝整文件假 diff（274/274）」——那只在真树上才看得见。

**空转守卫（⛔ 恒 0 不是负控）**：E1 把输出先种子成契约内容再跑生成器 ⇒ 若生成器根本不
重写文件，`after == before` 会**假绿**。所以：
- E1 断言生成器 stdout 确实报了 `wrote=` 与 `eol=crlf(mode=auto)`（证明它跑过、且 auto 沿用了目标 CRLF）；
- E1c 显式 `--eol lf` ⇒ 产物**必须变成纯 LF**（生成器若不重写，这条必红）。

⛔ 不碰仓内 `docs/reference/API_CONTRACT.md`（输出写 `tmp_path`）；⛔ 不跑 `*watch*.sh`。
"""
from __future__ import annotations

import hashlib
import pathlib
import shutil
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
GENERATOR = REPO / "scripts" / "render_api_contract.py"
CONTRACT = REPO / "docs" / "reference" / "API_CONTRACT.md"


def _render_to(out: pathlib.Path, *extra: str) -> subprocess.CompletedProcess:
    """在真树 REPO 上跑真生成器，产物写 out（⛔ 不碰仓内契约）。"""
    args = [sys.executable, str(GENERATOR), "--offline", str(REPO), *extra, str(out)]
    return subprocess.run(args, capture_output=True, text=True, timeout=600)


def _seed(out: pathlib.Path) -> bytes:
    """把仓内契约拷到 out（种子：沿用既有行尾形状＝auto 的输入）。"""
    shutil.copyfile(CONTRACT, out)
    return CONTRACT.read_bytes()


def test_e1_realtree_regeneration_is_byte_identical(tmp_path):
    """E1（真树主判据）：真树重生成 ⇒ 与仓内契约逐字节相同（numstat 空）。"""
    assert GENERATOR.is_file(), GENERATOR
    assert CONTRACT.is_file(), CONTRACT
    before = CONTRACT.read_bytes()
    assert before, "仓内契约为空"

    out = tmp_path / "API_CONTRACT.md"
    _seed(out)
    proc = _render_to(out)

    # 空转守卫：生成器必须真的跑过、写盘过、且 auto 沿用了目标 CRLF
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "wrote=" in proc.stdout, f"生成器没报 wrote=（可能没重写）: {proc.stdout!r}"
    assert "eol=crlf(mode=auto)" in proc.stdout, (
        f"auto 未沿用目标 CRLF: {proc.stdout!r}"
    )

    after = out.read_bytes()
    assert after == before, (
        "真树重生成不是逐字节幂等（§1.127 病灶复现）："
        f"before={len(before)}B/{hashlib.sha256(before).hexdigest()[:8]} "
        f"after={len(after)}B/{hashlib.sha256(after).hexdigest()[:8]}"
    )


def test_e1b_realtree_regeneration_keeps_crlf(tmp_path):
    """E1b：真树契约是 CRLF ⇒ 重生成后仍 CRLF、无裸 LF（行尾不被翻）。"""
    before = CONTRACT.read_bytes()
    crlf_before = before.count(b"\r\n")
    assert crlf_before > 0, "仓内契约应带 CRLF（EOL 根治的对象）"

    out = tmp_path / "API_CONTRACT.md"
    _seed(out)
    proc = _render_to(out)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    after = out.read_bytes()
    crlf_after = after.count(b"\r\n")
    bare_lf = after.count(b"\n") - crlf_after
    assert crlf_after == crlf_before and bare_lf == 0, (crlf_before, crlf_after, bare_lf)


def test_e1c_realtree_explicit_lf_flips_to_lf(tmp_path):
    """E1c（防「生成器是 no-op」的空转守卫）：显式 `--eol lf` ⇒ 产物必须变成纯 LF。

    若生成器根本不重写文件，产物会保持 CRLF ⇒ 这条必红 ⇒ 证明 E1 的幂等断言不是空转绿。
    """
    out = tmp_path / "API_CONTRACT.md"
    _seed(out)
    proc = _render_to(out, "--eol", "lf")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "eol=lf(mode=lf)" in proc.stdout, proc.stdout

    after = out.read_bytes()
    crlf = after.count(b"\r\n")
    bare = after.count(b"\n") - crlf
    assert crlf == 0 and bare > 0, (crlf, bare)
