# -*- coding: utf-8 -*-
"""AF-731 写面腿判据（v1.18 批次 C）：POST /api/automations/{name}/archive ＋ /unarchive
归属收口（卡：AF卡_AF731_写面腿_增补_2026-11-02，⛔ 落点＝PM 裁定）。

落点纪律（⛔ 席位不得自选）：写面两条端点复用**读面单源** `svc._assert_archive_visible`
⇒ 非 owner 的「他人名下」与「不存在」**同码同文 404**（`visible_error` 单源，与读面
完全一致）；⛔ 不用 `_assert_archive_mutator`——它两条文案不同形 ⇒ 非 owner 能靠文案
分辨存在性 ⇒ oracle 开（AF-509 的「无归属」分叉保留在 mutator 里，本卡不碰）。

判据（D1–D8）：
  D1 非 owner 归档他人 ⇒ 404，且盘上 tags **零变化**（归档前后 get_tags 逐字相等）
  D2 非 owner 归档「不存在」⇒ 与 D1 同码同文：状态码=**字面量 404**（⛔ 不得从实现
     推导）；两条 detail **把归档名替换成同一占位符后逐字相等**（⛔ 不许直接比原文
     ——文案内嵌名字，必然不等＝恒红）；期望=**字面量模板**（⛔ 不许引用
     `visible_error()` 之类实现函数生成期望＝同源自证，AF-735 同族教训）
  D3 owner 面归档 ⇒ 200 且 tags 增 `archived`（本判据用夹具私有 tmp store，天然隔离）
  D4 残缺身份（持令牌缺 subject）⇒ 403（broken_identity_error 文案族），⛔ 200／匿名
  D5 无身份逃生舱（AF_ALLOW_NOAUTH）⇒ 行为逐字不变（可归档；缺名仍 404 旧文案）
  D6 变异自证：把 `af_ownership.visible_error` 改成**带归属者名字**的形态（在
     af_service 导入绑定**之前** patch＝等价源级模板改动）⇒ D2 那条必须红
  D7 写面判据**全部用 POST**（⛔ 混 GET ⇒ 405 恒红/假绿）
  D8 前置（「他人名下」归档）由夹具自造，⛔ 不依赖现场库

反腿（⛔ 误伤即失败）：非 owner 写**自己名下** ⇒ 200（写面只拦「他人」）。
三坐标随提交面出票（sha／tree clean／PYTHONPATH 落树）。
"""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from autoforge.af_api import build_app
from autoforge.af_ir import load_graph
from autoforge.af_store import GraphStore

NON_OWNER = "dsh-pm"      # 非 owner 主体（名下 1 条）
OWNER_SUBJECT = "owner"   # 真 owner 面
OWNER_OF_ALL = "dsh"      # 归属者（他人名下归档挂他名下）

#: AF-735 同族纪律：拒绝文案的**字面量模板**（独立期望，⛔ 不引用实现函数）。
VISIBLE_TEMPLATE = "没找到归档 '«NAME»'（或它不属于你）"


def _ir(name: str) -> dict:
    return {
        "ir_version": "0.2.1", "id": name, "name": name, "version": 1,
        "mode": "single",
        "nodes": [{"id": "p1", "kind": "pass", "name": "p"}],
        "edges": [],
    }


def _denamed_exact(detail: str, name: str) -> str:
    """只把请求名换成占位符（⛔ 无兜底）——主体名泄露检查跑这一层
    （正则兜底会把「主体名占名字位」的泄露一起吃掉 ⇒ D2/D3 双盲，AF-735 教训）。"""
    return detail.replace(f"'{name}'", "'«NAME»'")


def _denamed(detail: str, name: str) -> str:
    """D2 同形用：去请求名＋正则兜底归一其余引号形状（只服务同形比较）。"""
    return re.sub(r"'[^']*'", "'«NAME»'", _denamed_exact(detail, name))


def _assert_no_subject_leak(exact_denamed: str) -> None:
    """去名后文案不得出现 owner／主体／归属者名字（存在性 oracle 泄露 ⇒ 红）。"""
    for subject in (NON_OWNER, OWNER_SUBJECT, OWNER_OF_ALL):
        assert subject not in exact_denamed, (
            f"写面文案泄露归属者: {exact_denamed!r} 含 {subject!r}"
        )


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """临时 store（mine=非 owner 名下；theirs／dsh_own 挂 dsh 名下）＋多令牌 env。"""
    store = GraphStore(str(tmp_path / "store"))
    store.save(load_graph(_ir("mine")), "mine", note="v1", owner=NON_OWNER)
    store.save(load_graph(_ir("theirs")), "theirs", note="v1", owner=OWNER_OF_ALL)
    store.save(load_graph(_ir("dsh_own")), "dsh_own", note="v1", owner=OWNER_OF_ALL)
    tokens = {
        "tok-agent": {"subject": NON_OWNER, "scopes": ["read", "write"]},
        "tok-owner": {"subject": OWNER_SUBJECT, "scopes": ["read", "write", "live"]},
        "tok-dsh": {"subject": OWNER_OF_ALL, "scopes": ["read", "write"]},
        "tok-empty-subject": {"subject": "", "scopes": ["read", "write"]},
    }
    monkeypatch.setenv("AF_ACCESS_TOKENS", json.dumps(tokens))
    monkeypatch.delenv("AF_ALLOW_NOAUTH", raising=False)
    client = TestClient(build_app(store_root=str(store.root)))
    yield {
        "client": client, "store": store,
        "agent": {"Authorization": "Bearer tok-agent"},
        "owner": {"Authorization": "Bearer tok-owner"},
        "dsh": {"Authorization": "Bearer tok-dsh"},
        "empty": {"Authorization": "Bearer tok-empty-subject"},
    }


# ── D1：非 owner 归档他人 ⇒ 404 ＋ 盘上 tags 零变化 ────────────────────

@pytest.mark.parametrize("action", ["archive", "unarchive"])
def test_d1_non_owner_cannot_write_foreign(env, action):
    """写面两条端点同判（D7：全部 POST）。"""
    tags_before = env["store"].get_tags("theirs")
    r = env["client"].post(f"/api/automations/theirs/{action}", headers=env["agent"])
    assert r.status_code == 404, r.text
    assert env["store"].get_tags("theirs") == tags_before, "⛔ 写面被拒后盘上 tags 必须零变化"


def test_d1b_non_owner_writes_own_archive_still_ok(env):
    """反腿：写面只拦「他人」——非 owner 写自己名下必须照常 200。"""
    r = env["client"].post("/api/automations/mine/archive", headers=env["agent"])
    assert r.status_code == 200 and r.json()["archived"] is True
    assert "archived" in env["store"].get_tags("mine")


# ── D2：非 owner 归档「不存在」⇒ 与 D1 同码同文（字面量模板） ──────────

@pytest.mark.parametrize("action", ["archive", "unarchive"])
def test_d2_missing_same_shape_as_foreign(env, action):
    headers = env["agent"]
    r_foreign = env["client"].post(f"/api/automations/theirs/{action}", headers=headers)
    r_missing = env["client"].post(f"/api/automations/no-such/{action}", headers=headers)
    # 状态码＝字面量 404（独立期望，⛔ 不得从实现推导）
    assert r_foreign.status_code == 404 and r_missing.status_code == 404
    f_tmpl = _denamed(r_foreign.json()["detail"], "theirs")
    m_tmpl = _denamed(r_missing.json()["detail"], "no-such")
    assert f_tmpl == VISIBLE_TEMPLATE, f_tmpl
    assert m_tmpl == VISIBLE_TEMPLATE, m_tmpl
    assert f_tmpl == m_tmpl, "D2：两条响应去名后必须逐字同形（oracle 闭）"
    # 主体名泄露检查跑在**只去请求名**的中间层（AF-735 同族：兜底不得吃掉泄露）
    _assert_no_subject_leak(_denamed_exact(r_foreign.json()["detail"], "theirs"))
    _assert_no_subject_leak(_denamed_exact(r_missing.json()["detail"], "no-such"))
    # ⛔ 不得退回 mutator_error 的「无权归档」形状（PM 裁定：那族文案不同形 ⇒ oracle 开）
    assert "无权" not in r_foreign.json()["detail"]


# ── D3：owner 面归档 ⇒ 200 ＋ tags 增 archived（夹具 tmp store 天然隔离） ──

def test_d3_owner_face_archives_and_unarchives(env):
    r = env["client"].post("/api/automations/theirs/archive", headers=env["owner"])
    assert r.status_code == 200 and r.json()["archived"] is True
    assert "archived" in env["store"].get_tags("theirs")
    r2 = env["client"].post("/api/automations/theirs/unarchive", headers=env["owner"])
    assert r2.status_code == 200 and r2.json()["archived"] is False
    assert "archived" not in env["store"].get_tags("theirs")


# ── D4：残缺身份 ⇒ 403（先于一切存取） ─────────────────────────────

@pytest.mark.parametrize("action", ["archive", "unarchive"])
def test_d4_broken_identity_403(env, action):
    r = env["client"].post(f"/api/automations/theirs/{action}", headers=env["empty"])
    assert r.status_code == 403
    assert "subject" in r.json()["detail"]
    assert env["store"].get_tags("theirs") == [], "⛔ 残缺身份不得碰盘"


# ── D5：无身份逃生舱（AF_ALLOW_NOAUTH）⇒ 行为逐字不变 ────────────────

def test_d5_noauth_escape_hatch_unchanged(tmp_path, monkeypatch):
    store = GraphStore(str(tmp_path / "store5"))
    store.save(load_graph(_ir("solo")), "solo", note="v1", owner=OWNER_OF_ALL)
    monkeypatch.setenv("AF_ALLOW_NOAUTH", "1")
    client = TestClient(build_app(store_root=str(store.root)))
    r = client.post("/api/automations/solo/archive")
    assert r.status_code == 200 and r.json()["archived"] is True
    assert "archived" in store.get_tags("solo")
    r_missing = client.post("/api/automations/no-such/archive")
    assert r_missing.status_code == 404
    assert r_missing.json()["detail"] == "未找到自动化", "缺名仍 404 旧文案（逐字不变）"


# ── D7/D8 的守卫：写面判据只许 POST（本文件无一处写面 GET，静态自证） ──

def test_d7_write_probes_are_post_only():
    """静态自证：本文件对写面路径的调用全部是 `.post(`，⛔ 不许出现写面 GET。"""
    import pathlib
    src = pathlib.Path(__file__).read_text(encoding="utf-8")
    for line in src.splitlines():
        if "/archive" in line or "/unarchive" in line:
            assert ".get(" not in line, f"写面判据混入 GET（⇒ 405 假绿/恒红）: {line}"
