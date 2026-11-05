# -*- coding: utf-8 -*-
"""判据 · 短期授权码被 consume 后，前端要能区分「已消耗/已过期/活跃」（2026-11-04）。

出资人原话：「使用了就没必要继续倒计时了，在 webui 里显示已经被使用过了」。
根因：`/api/user/auth-codes` 只给 `consumed` 布尔，前端无法据此停倒计时＋灰化＋标「已消耗于<时刻>」。

本卡**只加展示派生字段**（`status` 三态 + `consumed_at` 时刻），**⛔ 不改消耗/校验逻辑本身**
（短期码一次性是安全设计）。字段由存储层 `AuthCodeStore.list()` 派生，API 面透传。

D 编号带卡名（群规⑭）：
  D1 新建短期码 ⇒ status=active，consumed_at=None
  D2 consume 后 ⇒ status=consumed 且 consumed_at≈现在（前端据此停倒计时）
  D2b 长期码 consume 后 ⇒ status 仍 active（长期码不消耗，use_count+1）
  D3 到期未消耗 ⇒ status=expired
  D4 revoked 优先 ⇒ 已撤销码 status=revoked（即便 expired/consumed）
  D5 API 透传：`/api/user/auth-codes` owner 面返回的每枚码都带 status + consumed_at
  D6 牙齿（源码钉）：list() 里按 revoked→consumed→expired→active 顺序派生 status，防删。
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from autoforge.af_api import build_app
from autoforge.af_auth import AuthCodeStore
from autoforge.af_store import GraphStore

OWNER_OF_ALL = "dsh"


@pytest.fixture()
def acs(tmp_path):
    return AuthCodeStore(tmp_path / "store" / ".auth" / "auth_codes.json")


def _by_code(codes, code):
    for c in codes:
        if c["code"] == code:
            return c
    raise AssertionError(f"码 {code} 不在 list() 里")


# ── D1 新建短期码 ⇒ active ────────────────────────────────────────
def test_d1_new_short_is_active(acs):
    ac = acs.create("short", ttl_minutes=10)
    row = _by_code(acs.list(), ac.code)
    assert row["status"] == "active", f"新建短期码应 active，实得 {row['status']}"
    assert row["consumed"] is False
    assert row["consumed_at"] is None, "未消耗码 consumed_at 应为 None"


# ── D2 consume 后 ⇒ consumed + consumed_at ────────────────────────
def test_d2_consumed_short_status_and_time(acs):
    ac = acs.create("short", ttl_minutes=10)
    before = time.time()
    assert acs.consume(ac.code) is True
    after = time.time()
    row = _by_code(acs.list(), ac.code)
    assert row["status"] == "consumed", f"已消耗短期码应 consumed，实得 {row['status']}"
    assert row["consumed"] is True
    assert row["consumed_at"] is not None, "消耗码必须记 consumed_at（前端显示「已消耗于<时刻>」）"
    assert before <= row["consumed_at"] <= after, "consumed_at 必须落在本次 consume 的时间窗内"


# ── D2b 长期码 consume 后仍 active ────────────────────────────────
def test_d2b_long_code_stays_active_after_use(acs):
    ac = acs.create("long")
    assert acs.consume(ac.code) is True
    row = _by_code(acs.list(), ac.code)
    assert row["status"] == "active", f"长期码不消耗，status 应仍 active，实得 {row['status']}"
    assert row["consumed"] is False
    assert row["use_count"] == 1


# ── D3 到期未消耗 ⇒ expired（防御分支：直测派生逻辑）─────────────────
def test_d3_expired_status(tmp_path, monkeypatch):
    """expired 是派生分支；线上到期码会被 `_load` 的过期过滤挡在 list 外（既有行为），
    故这里直接喂内存快照、旁路 `_load`，专测 status 派生逻辑本身。"""
    store = AuthCodeStore(tmp_path / "store" / ".auth" / "auth_codes.json")
    ac = store.create("short", ttl_minutes=10)
    # 把内存快照的 expires_at 改到过去，并旁路 _load（否则会被过期过滤覆盖/剔除）
    store._codes[ac.code]["expires_at"] = time.time() - 60
    monkeypatch.setattr(store, "_load", lambda: None)
    row = _by_code(store.list(), ac.code)
    assert row["status"] == "expired", f"到期码应 expired，实得 {row['status']}"


# ── D4 revoked 优先于 expired/consumed ────────────────────────────
def test_d4_revoked_takes_precedence(acs):
    ac = acs.create("short", ttl_minutes=10)
    acs.consume(ac.code)
    assert acs.revoke(ac.code) is True
    row = _by_code(acs.list(), ac.code)
    assert row["status"] == "revoked", f"已撤销码 status 应 revoked（优先级最高），实得 {row['status']}"


# ── D5 API 透传 status + consumed_at ──────────────────────────────
def test_d5_api_list_passes_status(tmp_path, monkeypatch):
    store = GraphStore(str(tmp_path / "store"))
    tokens = {"tok-owner": {"subject": "owner", "scopes": ["read", "write"]}}
    monkeypatch.setenv("AF_ACCESS_TOKENS", json.dumps(tokens))
    monkeypatch.delenv("AF_ALLOW_NOAUTH", raising=False)
    client = TestClient(build_app(store_root=str(store.root)))
    headers = {"Authorization": "Bearer tok-owner"}
    acs = AuthCodeStore(store.root / ".auth" / "auth_codes.json")
    ac = acs.create("short", ttl_minutes=10)
    acs.consume(ac.code)
    r = client.get("/api/user/auth-codes", headers=headers)
    assert r.status_code == 200, f"owner 面列表 ⇒ 200，实得 {r.status_code}: {r.text}"
    codes = r.json()["codes"]
    row = _by_code(codes, ac.code)
    assert row["status"] == "consumed", "API 列表须透传 status=consumed"
    assert row["consumed_at"] is not None, "API 列表须透传 consumed_at"


# ── D6 牙齿（源码钉）：status 派生顺序 ────────────────────────────
def test_d6_source_derivation_order():
    """list() 必须按 revoked→consumed→expired→active 派生（防后人删/调乱优先级）。"""
    import inspect

    from autoforge import af_auth

    src = inspect.getsource(af_auth.AuthCodeStore.list)
    assert '"status": status' in src, "list() 必须产出 status 字段"
    # 优先级顺序：revoked 判定在 consumed 之前、consumed 在 expired 之前
    i_rev = src.find('status = "revoked"')
    i_con = src.find('status = "consumed"')
    i_exp = src.find('status = "expired"')
    assert -1 < i_rev < i_con < i_exp, "status 派生优先级必须 revoked < consumed < expired，顺序被改了"
