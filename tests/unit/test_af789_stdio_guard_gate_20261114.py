# -*- coding: utf-8 -*-
"""AF-789 判据 · stdio 协议循环的两道守卫（元宝第五期 MCP-03，2026-11-14）。

**缺陷形状**：`serve_mcp` 的循环里 `req = json.loads(line)` 之后直接 `req.get("method")`。
一条 JSON-RPC **batch 数组**（`[{...}]`）或顶层标量（`42`／`"x"`／`null`）解析得出来、
却不是 dict ⇒ `AttributeError: 'list' object has no attribute 'get'` **穿出整条 stdio 循环**，
server 进程当场死掉：客户端只看到 EOF，后续 `ping` 也没有了。同一族还有第二个缺口：
方法分发段里任何一枚工具异常都会同样穿出（`dispatch` 之外的 `params.get`／`registry`
调用都在裸奔）。

**修法（AF-PM 已裁，两刀都不扩语义）**：

1. `json.loads` 后 `isinstance(req, dict)` 不满足 ⇒ 回 `-32600` 并 `continue`。
   ⛔ **不实现真 batch**（MCP 2024-11-05 起已移除 batch）；⛔ 不静默 continue（客户端会挂到超时）。
2. 方法分发整段包一层循环级 `try`：异常回 `-32603`，⛔ 不穿出，并往 stderr 留一行
   （stdout 专供 JSON-RPC，⛔ 不能把 traceback 写进去）。

⛔ 本笔**不碰**：超大行判定（MCP-06／批三）、notifications 分支语义（MCP-11／批三）、
协议版本回显（MCP-16／批三）。

**判据**：

- C1 batch 数组 ⇒ 该行回 `-32600`，进程存活，随后 `ping` 正常
- C2 顶层标量（`42`／`"str"`／`null`）⇒ 同上
- C3 分发段抛异常 ⇒ 回 `-32603`，进程存活，stderr 有一行痕，随后 `ping` 正常
- C4 真进程端到端：把 C1 的形状喂给 `python -m autoforge.af_mcp`，进程自己退出（stdin EOF）而不是崩
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from autoforge import af_mcp

REPO = Path(__file__).resolve().parents[2]
PING = json.dumps({"jsonrpc": "2.0", "id": 9, "method": "ping"})


def _drive(monkeypatch, tmp_path, lines: list[str]) -> tuple[list[dict], str]:
    """在进程内跑一遍 `serve_mcp`：喂 `lines`，返回（stdout 解析出来的 JSON 行, stderr 文本）。"""
    monkeypatch.delenv("AUTOFORGE_TOKENS", raising=False)
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n".join(lines) + "\n"))
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    af_mcp.serve_mcp(str(tmp_path / "store"))  # 不抛 = 循环没被单条请求弄死
    parsed = [json.loads(ln) for ln in out.getvalue().splitlines() if ln.strip()]
    return parsed, err.getvalue()


def _codes(parsed: list[dict]) -> list[int]:
    return [p["error"]["code"] for p in parsed if "error" in p]


# ── C1 batch 数组 ─────────────────────────────────────────────────────
def test_c1_batch_array_gets_minus_32600_and_process_survives(monkeypatch, tmp_path):
    batch = json.dumps([{"jsonrpc": "2.0", "id": 1, "method": "ping"}])
    parsed, _err = _drive(monkeypatch, tmp_path, [batch, PING])

    assert -32600 in _codes(parsed), parsed
    assert any(p.get("id") is None and p.get("error", {}).get("code") == -32600 for p in parsed), (
        f"batch 那行必须回 -32600 且 id=null：{parsed}"
    )
    # 进程存活：后面那条 ping 必须照常答
    assert any(p.get("id") == 9 and p.get("result") == {} for p in parsed), parsed
    assert "batch" in json.dumps(parsed, ensure_ascii=False), "文案要点明不支持 batch（⛔ 不实现真 batch）"


# ── C2 顶层标量 ───────────────────────────────────────────────────────
@pytest.mark.parametrize("scalar", ["42", '"just a string"', "null", "3.14"])
def test_c2_top_level_scalar_gets_minus_32600(monkeypatch, tmp_path, scalar):
    parsed, _err = _drive(monkeypatch, tmp_path, [scalar, PING])
    assert -32600 in _codes(parsed), parsed
    assert any(p.get("id") == 9 and p.get("result") == {} for p in parsed), parsed


# ── C3 分发段异常 ⇒ -32603，不穿出 ─────────────────────────────────────
def test_c3_dispatch_exception_becomes_minus_32603(monkeypatch, tmp_path):
    def boom(*_a, **_k):
        raise RuntimeError("工具里自己炸了")

    monkeypatch.setattr(af_mcp, "dispatch", boom)
    call = json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                       "params": {"name": "af_status", "arguments": {}}})
    parsed, err = _drive(monkeypatch, tmp_path, [call, PING])

    assert -32603 in _codes(parsed), parsed
    assert any(p.get("id") == 3 for p in parsed if "error" in p), parsed
    assert any(p.get("id") == 9 and p.get("result") == {} for p in parsed), "异常之后 ping 仍要答"
    assert "request failed" in err and "RuntimeError" in err, f"stderr 要留一行痕：{err!r}"
    assert "Traceback" not in json.dumps(parsed), "⛔ 不许把 traceback 塞进 stdout（客户端 JSON 解析器先炸）"


# ── C4 真进程端到端：batch 之后进程照常退出 ─────────────────────────────
def test_c4_real_process_survives_a_batch_line(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = str(REPO / "src")
    payload = (
        json.dumps([{"jsonrpc": "2.0", "id": 1, "method": "ping"}]) + "\n" + PING + "\n"
    )
    proc = subprocess.run(
        [sys.executable, "-m", "autoforge.af_mcp"],
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(tmp_path),
        env=env,
        timeout=120,
    )
    assert proc.returncode == 0, f"进程应正常退（stdin EOF），实得 {proc.returncode}：{proc.stderr[-800:]}"
    assert "-32600" in proc.stdout, proc.stdout[-400:]
    assert '"result": {}' in proc.stdout or '"result":{}' in proc.stdout, proc.stdout[-400:]
