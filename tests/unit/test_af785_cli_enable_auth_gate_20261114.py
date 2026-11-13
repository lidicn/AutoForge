# -*- coding: utf-8 -*-
"""AF-785 判据 · CLI 批量启用补凭据闸（2026-11-14，AF-401「启用要码、禁用免码」）。

**缺口**：`forge store enable --tag …` 走 `svc_enable_disable`，这条 CLI 面**完全无闸**——
MCP/REST 侧 `af_enable_by_tag` 要授权码（AF-778 还补了 consume），本机侧却一句码都不要，
等于把「启用要凭据」这条纪律从 CLI 绕开。

**修法**：闸装**CLI 入口层**（`_require_enable_auth`），⛔ 不动 `svc_enable_disable` 本体
（那是纯函数，库形调用不该被焊死）。顺序与 AF-778 同形：爆炸半径预检 → validate →
consume（fail-closed）；缺省读 env `AUTOFORGE_AUTH_CODE`（AF-714 口径）。disable 免码。

**判据**：

- N1 无码 enable ⇒ 退出码 2 ＋「需要授权码」文案（与 `_auth_code_check` 同形）
- N2 短期码 enable 成功后被 consume，**重放**再跑一次被拒
- N3 长期码 enable 后 `use_count` +1 且仍可复用
- N4 disable 免码照通（关安全阀不该被钥匙挡住）
- N5 爆炸半径超额 ⇒ 拒绝且**不烧码**
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from typer.testing import CliRunner

from autoforge.af_auth import AuthCodeStore
from autoforge.af_cli import app
from autoforge.af_ir import Graph, load_automation
from autoforge.af_store import GraphStore

IR: dict[str, Any] = {
    "ir_version": "0.2.1", "id": "af785_ir", "name": "AF785",
    "version": 1, "mode": "single", "enabled": False,
    "nodes": [
        {"id": "t1", "kind": "on", "name": "人",
         "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
        {"id": "p1", "kind": "pass", "name": "结束"},
    ],
    "edges": [{"from": "t1", "to": "p1", "kind": "then"}],
}


def _store(tmp_path) -> GraphStore:
    store = GraphStore(str(tmp_path / "store"))
    store.save(Graph([load_automation(IR)]), "af785_case", tags=["night"])
    return store


def _acs(store: GraphStore) -> AuthCodeStore:
    return AuthCodeStore(store.root / ".auth" / "auth_codes.json")


def _run(*args: str):
    return CliRunner().invoke(app, list(args))


def _rec(acs: AuthCodeStore, code: str) -> dict[str, Any]:
    return next(r for r in acs.list(reveal=True) if r.get("code") == code)


# ── N1 无码 ⇒ 拒 ────────────────────────────────────────────────────
def test_n1_enable_without_code_is_rejected(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTOFORGE_AUTH_CODE", raising=False)
    store = _store(tmp_path)

    r = _run("store", "enable", "--tag", "night", "--root", str(store.root))

    assert r.exit_code == 2, f"无码必须非零退出，实得 {r.exit_code}：{r.output}"
    assert "需要授权码" in r.output, "文案要与 _auth_code_check 同形：%r" % (r.output,)
    # ⛔ 被拒时不许动盘：自动化仍是 disabled
    graph = store.load("af785_case", store.latest("af785_case"))
    assert list(graph)[0].enabled is False


# ── N2 短期码：一次性，重放被拒 ──────────────────────────────────────
def test_n2_short_code_consumed_and_replay_rejected(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTOFORGE_AUTH_CODE", raising=False)
    store = _store(tmp_path)
    acs = _acs(store)
    code = acs.create("short").code

    r1 = _run("store", "enable", "--tag", "night", "--root", str(store.root), "--auth-code", code)
    assert r1.exit_code == 0, f"带有效短期码必须成功：{r1.output}"
    assert _rec(acs, code).get("consumed") is True, "过闸即 consume（短期码一次性）"

    r2 = _run("store", "enable", "--tag", "night", "--root", str(store.root), "--auth-code", code)
    assert r2.exit_code == 2, "同一枚短期码重放必须被拒（修前 CLI 无闸 ⇒ 永远成功）"
    assert "授权码无效" in r2.output or "需要授权码" in r2.output


# ── N3 长期码：计次且可复用 ──────────────────────────────────────────
def test_n3_long_code_counts_and_reusable(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTOFORGE_AUTH_CODE", raising=False)
    store = _store(tmp_path)
    acs = _acs(store)
    code = acs.create("long").code

    assert _run("store", "enable", "--tag", "night", "--root", str(store.root),
                "--auth-code", code).exit_code == 0
    assert _rec(acs, code).get("use_count") == 1
    assert _run("store", "enable", "--tag", "night", "--root", str(store.root),
                "--auth-code", code).exit_code == 0, "长期码可复用"
    assert _rec(acs, code).get("use_count") == 2


# ── N4 disable 免码 ─────────────────────────────────────────────────
def test_n4_disable_needs_no_code(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTOFORGE_AUTH_CODE", raising=False)
    store = _store(tmp_path)

    r = _run("store", "disable", "--tag", "night", "--root", str(store.root))

    assert r.exit_code == 0, f"禁用⛔ 不许要码（关安全阀不该被钥匙挡住）：{r.output}"
    assert "已禁用" in r.output


# ── N5 爆炸半径超额 ⇒ 不烧码 ─────────────────────────────────────────
def test_n5_blast_rejection_does_not_burn_code(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTOFORGE_AUTH_CODE", raising=False)
    store = _store(tmp_path)
    store.save(Graph([load_automation({**IR, "id": "af785_ir2"})]), "af785_case2", tags=["night"])
    acs = _acs(store)
    code = acs.create("short").code

    import autoforge.af_service as svc

    monkeypatch.setattr(svc, "blast_radius_limit", lambda: 1)  # 影响 2 > 上限 1

    r = _run("store", "enable", "--tag", "night", "--root", str(store.root), "--auth-code", code)

    assert r.exit_code == 2 and "爆炸半径" in r.output, r.output
    rec = _rec(acs, code)
    assert rec.get("consumed") is False and rec.get("use_count") == 0, (
        "被爆炸半径拒绝的那次⛔ 不许扣额度（码必须完好）"
    )
    assert acs.validate(code) is True
