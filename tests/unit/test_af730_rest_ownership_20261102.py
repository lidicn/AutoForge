# -*- coding: utf-8 -*-
"""AF-730＋AF-731（2026-11-02，并批）：REST 读端点归属收口判据（6 端点 · 端到端）。

范围（卡 §七/§十一/§十二，⛔ 只许这 6 个，其余出批）：
  GET /api/graphs          ① 非 owner 只见自己＋hidden_count＋scope（与 MCP 同形）
  GET /api/graphs/{name}   ② 他人名 404 与「不存在」逐字同形（存在性 oracle 闭）
  GET /api/conf/{name}     ⑭ 服务层本就带身份，REST 补传即可（他人 404 同形）
  GET /api/asks/{name}     ⑬ 同 ②（旧文案「未找到自动化」⇒ 撤，同 `visible_error` 单源）
  GET /api/automations     ⑦/⑧ 薄入口 `list_automation_cards`：份额化＋缺口必报
  GET /api/automations/{name} ⑨/⑩ 归属闸先过，404 同形
  （⑬/本地 ask 构造腿：PM 裁定 ④「线上构造不出 ⇒ 本地树覆盖」）

单源纪律（⛔ 不新写判定）：身份 `Subject.from_caller`／归属 `_existing_owner`（C4）/
范围 `scope_names`／读拒文案 `visible_error`——判据同时钉「收口后 owner 面/腿 A
（归属者·非 owner 面）行为不变」（AF-716 反向同族：⛔ 误伤 owner＝失败）。

变异自证（卡 ⑥/⑧：探针必须有牙）：把 REST 侧 caller 改回空 ⇒ ①②⑦/⑧ 必红。
⛔ 恒绿＝覆盖洞。三坐标随提交面出票（sha／tree clean／autoforge.__file__）。
"""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from autoforge.af_api import build_app
from autoforge.af_auth import TokenRegistry
from autoforge.af_ir import load_graph
from autoforge.af_store import GraphStore

NON_OWNER = "dsh-pm"      # 纯非 owner 主体（名下无归档）
OWNER_SUBJECT = "owner"   # 真 owner 面（_OWNER_SUBJECTS）
OWNER_OF_ALL = "dsh"      # 腿 A：归属者（全部归档挂他名下）· 非 owner 面

#: AF-735：拒绝文案的**字面量模板**（独立期望，⛔ 不引用实现函数）。
#: 真源是 `af_ownership.visible_error` 的 f-string：`没找到归档 {name!r}（或它不属于你）`。
#: 实现若改模板 ⇒ 这里红 ⇒ 有意照面（判据必须独立于被检对象，台账 1.28）；
#: ⛔ 禁止改回「拿 `visible_error()` 当期望值」——那等于同源自证（AF-735 病灶①）。
VISIBLE_TEMPLATE = "没找到归档 '«NAME»'（或它不属于你）"


def _denamed_exact(detail: str, name: str) -> str:
    """AF-735 D3 底座：**只**把请求名替换成占位符（⛔ 不做正则兜底）。
    ⚠️ 主体名泄露检查必须跑在这一层——PM 复核指出的假绿角落：若实现把主体名
    放进名字位置（如 detail 里出现 'dsh'），正则兜底会把它一起占位化 ⇒
    D2/D3 双盲；先只去请求名，主体名留在文本里给 D3 抓。"""
    return detail.replace(f"'{name}'", "'«NAME»'")


def _denamed(detail: str, name: str) -> str:
    """AF-735 D2 同形用：先只去请求名，再以正则兜底归一**其余**引号形状
    （兜底只服务同形比较；⛔ 主体名泄露检查跑 `_denamed_exact`）。"""
    return re.sub(r"'[^']*'", "'«NAME»'", _denamed_exact(detail, name))


def _assert_no_subject_leak(denamed: str) -> None:
    """AF-735 D3：去名后的文案不得出现 owner／主体／归属者名字
    （存在性 oracle 泄露 ⇒ 红）。⚠️ 必须对**去名后**的文本断言——
    请求名本身（如 dsh_own）含主体子串，先去名再查才不误伤。"""
    for subject in (NON_OWNER, OWNER_SUBJECT, OWNER_OF_ALL):
        assert subject not in denamed, (
            f"文案泄露归属者（存在性 oracle）: {denamed!r} 含 {subject!r}"
        )


def _ir(name: str, ask: bool = False) -> dict:
    nodes: list[dict] = [{"id": "p1", "kind": "pass", "name": "p"}]
    edges: list[dict] = []
    if ask:
        nodes.insert(0, {
            "id": "q1", "kind": "ask", "name": "q",
            "prompt": "测试问句",
            "ask": {"kind": "choice", "options": ["是", "否"]},
        })
        edges.append({"from": "q1", "to": "p1", "kind": "then"})
    return {
        "ir_version": "0.2.1", "id": name, "name": name, "version": 1,
        "mode": "single", "nodes": nodes, "edges": edges,
    }


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """临时 store（mine=非owner自己／其余挂 dsh 名下）＋ 多令牌 env。

    形状复刻线上：`dsh`＝腿 A（归属者·非 owner 面，名下 2 条）；`dsh-pm`＝腿 B
    （名下 1 条）；`owner`＝腿 O（真 owner 面）。归档三条 ⇒ 腿 A hidden=0、
    腿 B items=1/hidden=2、腿 O 全量无缺口键。
    """
    store = GraphStore(str(tmp_path / "store"))
    store.save(load_graph(_ir("mine")), "mine", note="v1", owner=NON_OWNER)
    store.save(load_graph(_ir("theirs")), "theirs", note="v1", owner=OWNER_OF_ALL)
    store.save(load_graph(_ir("dsh_own", ask=True)), "dsh_own", note="v1", owner=OWNER_OF_ALL)
    tokens = {
        "tok-agent": {"subject": NON_OWNER, "scopes": ["read", "write"]},
        "tok-owner": {"subject": OWNER_SUBJECT, "scopes": ["read", "write", "live"]},
        "tok-dsh": {"subject": OWNER_OF_ALL, "scopes": ["read", "write"]},
        "tok-empty-subject": {"subject": "", "scopes": ["read"]},
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


# ── ① GET /api/graphs：三面三形状 ────────────────────────────────────

def test_graphs_non_owner_scoped_with_gap(env):
    r = env["client"].get("/api/graphs", headers=env["agent"])
    assert r.status_code == 200
    body = r.json()
    assert [it["name"] for it in body["items"]] == ["mine"]
    assert body["count"] == 1
    assert body["hidden_count"] == 2, body.get("hidden_count")
    assert body["scope"] == f"owner:{NON_OWNER}"


def test_graphs_owner_of_all_zero_gap_but_gap_fields_present(env):
    """腿 A（本 fixture 变体）：dsh 名下 2/3 ⇒ hidden_count==1、items=2（纯接线自证：
    caller 下传后 scope 必报；hidden=全集−份额，与 MCP 同算术）。线上「dsh 拥有全部
    ⇒ hidden==0」形状由本地复验 seed（规约②）与 dedup 测试覆盖。"""
    r = env["client"].get("/api/graphs", headers=env["dsh"])
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    assert body["hidden_count"] == 1
    assert body["scope"] == f"owner:{OWNER_OF_ALL}"


def test_graphs_owner_face_no_gap_fields(env):
    r = env["client"].get("/api/graphs", headers=env["owner"])
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 3
    assert "hidden_count" not in body and "scope" not in body


def test_graphs_broken_identity_403(env):
    """R1：持令牌缺 subject ⇒ 403 残缺身份，⛔ 退化成匿名全量。"""
    r = env["client"].get("/api/graphs", headers=env["empty"])
    assert r.status_code == 403
    assert "subject" in r.json()["detail"]


def test_automations_broken_identity_403(env):
    """AF-735 D5：残缺身份腿补第二端点（原覆盖只有 /api/graphs 一条腿）——
    /api/automations 同样 403，⛔ 退化成匿名全量。"""
    r = env["client"].get("/api/automations", headers=env["empty"])
    assert r.status_code == 403
    assert "subject" in r.json()["detail"]


def test_graphs_non_owner_with_no_archives_shape(env):
    """腿 B 复刻（线上 dsh-PM＝名下 0 条的形状）由 leg1 的 mine 份额腿等效覆盖；
    此处只钉「非 owner 名下条数 0 ⇒ items=0、hidden=全集」的算术（tmp 直调服务层）。"""
    from autoforge import af_service as svc
    store = GraphStore(str(env["store"].root))  # 同一 store 复用
    out = svc.list_graphs(store, caller="no-such-subject", is_owner_face=False)
    assert out["count"] == 0
    assert out["hidden_count"] == 3
    assert out["scope"] == "owner:no-such-subject"


def test_graphs_noauth_escape_hatch_still_full(tmp_path, monkeypatch):
    """④ 免令牌逃生舱（AF_ALLOW_NOAUTH）⇒ caller 空＋token_identity None ⇒ 全量豁免不动。"""
    store = GraphStore(str(tmp_path / "store2"))
    store.save(load_graph(_ir("solo")), "solo", note="v1", owner=OWNER_OF_ALL)
    monkeypatch.setenv("AF_ALLOW_NOAUTH", "1")
    client = TestClient(build_app(store_root=str(store.root)))
    r = client.get("/api/graphs")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1 and "hidden_count" not in body


# ── ② GET /api/graphs/{name}：存在性 oracle ─────────────────────────

def test_graph_detail_foreign_and_missing_verbatim_same(env):
    """oracle（AF-735 重钉）：他人名与不存在名同一路径同一枚文案单源——状态同 404，
    detail 各自**把归档名替换成同一占位符后**逐字等于**字面量模板**（D2；⛔ 不引用
    `visible_error()` 当期望——期望与被检对象同源＝判红能力为零，AF-735 病灶①）；
    且去名后不得出现归属者名字（D3，存在性 oracle 泄露 ⇒ 红）。"""
    headers = env["agent"]
    r_foreign = env["client"].get("/api/graphs/theirs", headers=headers)
    r_missing = env["client"].get("/api/graphs/no-such-archive", headers=headers)
    assert r_foreign.status_code == 404 and r_missing.status_code == 404
    assert list(r_foreign.json().keys()) == list(r_missing.json().keys()) == ["detail"]
    f_tmpl = _denamed(r_foreign.json()["detail"], "theirs")
    m_tmpl = _denamed(r_missing.json()["detail"], "no-such-archive")
    assert f_tmpl == VISIBLE_TEMPLATE, f_tmpl
    assert m_tmpl == VISIBLE_TEMPLATE, m_tmpl
    assert f_tmpl == m_tmpl, "D2：两条响应去名后必须逐字同形"
    # D3（PM 复核修正）：跑在**只去请求名**的中间层——主体名占名字位的泄露
    # 不会被正则兜底吃掉
    _assert_no_subject_leak(_denamed_exact(r_foreign.json()["detail"], "theirs"))
    _assert_no_subject_leak(_denamed_exact(r_missing.json()["detail"], "no-such-archive"))


def test_graph_detail_own_ok_owner_of_all_reads_foreign(env):
    assert env["client"].get("/api/graphs/mine", headers=env["agent"]).status_code == 200
    # 腿 O：真 owner 面能读他人归档（保持）＋ 腿 A 归属者读自己名下（保持）
    assert env["client"].get("/api/graphs/dsh_own", headers=env["owner"]).status_code == 200
    assert env["client"].get("/api/graphs/theirs", headers=env["dsh"]).status_code == 200


# ── ⑭ GET /api/conf/{name} ──────────────────────────────────────────

def test_conf_foreign_same_shape_as_missing(env):
    """AF-735 重钉（同 ②）：期望＝字面量模板＋占位符同形＋无主体名泄露。"""
    r_foreign = env["client"].get("/api/conf/theirs", headers=env["agent"])
    r_missing = env["client"].get("/api/conf/no-such", headers=env["agent"])
    assert r_foreign.status_code == 404 and r_missing.status_code == 404
    f_tmpl = _denamed(r_foreign.json()["detail"], "theirs")
    m_tmpl = _denamed(r_missing.json()["detail"], "no-such")
    assert f_tmpl == VISIBLE_TEMPLATE, f_tmpl
    assert m_tmpl == VISIBLE_TEMPLATE, m_tmpl
    assert f_tmpl == m_tmpl, "D2：两条响应去名后必须逐字同形"
    # D3（PM 复核修正）：跑在**只去请求名**的中间层
    _assert_no_subject_leak(_denamed_exact(r_foreign.json()["detail"], "theirs"))
    _assert_no_subject_leak(_denamed_exact(r_missing.json()["detail"], "no-such"))


def test_conf_own_ok(env):
    r = env["client"].get("/api/conf/mine", headers=env["agent"])
    assert r.status_code == 200 and r.json().get("ok") is True


# ── ⑬ GET /api/asks/{name}（本地构造 ask 腿）────────────────────────

def test_asks_name_foreign_and_missing_verbatim_same(env):
    """AF-735 重钉（同 ②）：期望＝字面量模板＋占位符同形＋无主体名泄露
    （⚠️ 请求名 dsh_own 含主体子串 ⇒ D3 必须对**去名后**文本断言）。"""
    r_foreign = env["client"].get("/api/asks/dsh_own", headers=env["agent"])
    r_missing = env["client"].get("/api/asks/no-such", headers=env["agent"])
    assert r_foreign.status_code == 404 and r_missing.status_code == 404
    f_tmpl = _denamed(r_foreign.json()["detail"], "dsh_own")
    m_tmpl = _denamed(r_missing.json()["detail"], "no-such")
    assert f_tmpl == VISIBLE_TEMPLATE, f_tmpl
    assert m_tmpl == VISIBLE_TEMPLATE, m_tmpl
    assert f_tmpl == m_tmpl, "D2：两条响应去名后必须逐字同形"
    # D3（PM 复核修正）：跑在**只去请求名**的中间层
    _assert_no_subject_leak(_denamed_exact(r_foreign.json()["detail"], "dsh_own"))
    _assert_no_subject_leak(_denamed_exact(r_missing.json()["detail"], "no-such"))
    assert "未找到自动化" not in r_foreign.text, "旧文案必须撤（存在性 oracle）"


def test_asks_name_own_lists_ask_nodes(env):
    r = env["client"].get("/api/asks/dsh_own", headers=env["dsh"])
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and len(body["asks"]) == 1


# ── ⑦/⑧ GET /api/automations：份额化＋缺口必报 ─────────────────────

def test_automations_non_owner_scoped_with_gap(env):
    r = env["client"].get("/api/automations", headers=env["agent"])
    assert r.status_code == 200
    body = r.json()
    names = [c["id"] for g in body["groups"] for c in g["items"]]
    assert names == ["mine"]
    assert body["total"] == 1
    assert body["hidden_count"] == 2 and body["scope"] == f"owner:{NON_OWNER}"


def test_automations_owner_face_full_no_gap_fields(env):
    r = env["client"].get("/api/automations", headers=env["owner"])
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert "hidden_count" not in body and "scope" not in body


def test_automations_dedup_and_owner_legs(tmp_path, monkeypatch):
    """必修②：多版本归档只出一张卡（total == 唯一归档数，⛔ 字面量锚）＋ ok 派生。"""
    store = GraphStore(str(tmp_path / "store3"))
    g = load_graph(_ir("dupme"))
    store.save(g, "dupme", note="v1", owner=OWNER_OF_ALL)
    store.save(g, "dupme", note="v2", owner=OWNER_OF_ALL)
    monkeypatch.setenv("AF_ACCESS_TOKENS", json.dumps(
        {"tok-dsh": {"subject": OWNER_OF_ALL, "scopes": ["read"]}}))
    client = TestClient(build_app(store_root=str(store.root)))
    r = client.get("/api/automations", headers={"Authorization": "Bearer tok-dsh"})
    assert r.status_code == 200
    body = r.json()
    # AF-735 D4：期望改**独立字面量**（夹具只建 dupme 一名两版本 ⇒ 唯一归档数=1）；
    # ⛔ 不许从 store.history() 反推期望（从被检对象的读面反推＝同源，AF-735 病灶③）。
    assert body["total"] == 1, "total 必须等于唯一归档数（去重；字面量锚=1：dupme 两版本）"
    assert r.json()["ok"] is True
    # graphs 面同样去重口径（list_graphs 走 latest map，天然唯一）：
    r2 = client.get("/api/graphs", headers={"Authorization": "Bearer tok-dsh"})
    assert r2.json()["count"] == 1


def test_automations_group_by_other_shape_scoped(env):
    r = env["client"].get("/api/automations", headers=env["agent"], params={"group_by": "none"})
    assert r.status_code == 200
    body = r.json()
    assert [c["id"] for c in body["items"]] == ["mine"]
    assert body["hidden_count"] == 2


# ── ⑨/⑩ GET /api/automations/{name} ────────────────────────────────

def test_automation_detail_foreign_and_missing_verbatim_same(env):
    """AF-735 重钉（同 ②）：期望＝字面量模板＋占位符同形＋无主体名泄露。"""
    r_foreign = env["client"].get("/api/automations/theirs", headers=env["agent"])
    r_missing = env["client"].get("/api/automations/no-such", headers=env["agent"])
    assert r_foreign.status_code == 404 and r_missing.status_code == 404
    f_tmpl = _denamed(r_foreign.json()["detail"], "theirs")
    m_tmpl = _denamed(r_missing.json()["detail"], "no-such")
    assert f_tmpl == VISIBLE_TEMPLATE, f_tmpl
    assert m_tmpl == VISIBLE_TEMPLATE, m_tmpl
    assert f_tmpl == m_tmpl, "D2：两条响应去名后必须逐字同形"
    # D3（PM 复核修正）：跑在**只去请求名**的中间层
    _assert_no_subject_leak(_denamed_exact(r_foreign.json()["detail"], "theirs"))
    _assert_no_subject_leak(_denamed_exact(r_missing.json()["detail"], "no-such"))
    assert "未找到自动化" not in r_foreign.text


def test_automation_detail_own_ok(env):
    r = env["client"].get("/api/automations/mine", headers=env["agent"])
    assert r.status_code == 200 and r.json()["automation"]["id"] == "mine"


# ── ⑤ MCP 面零变化（服务层旧调用形状回归）──────────────────────────

def test_service_mcp_shape_unchanged(env):
    """MCP `_t_list_graphs` 不传 token_identity ⇒ None ⇒ 行为与收口前逐字一致。"""
    from autoforge import af_service as svc
    out = svc.list_graphs(env["store"], caller=NON_OWNER, is_owner_face=False)
    assert out["count"] == 1 and out["hidden_count"] == 2


# ── ⑥ 变异自证（探针必须有牙）──────────────────────────────────────

def test_mutation_caller_reset_reverts_bypass(env, monkeypatch):
    """把 REST 侧 caller 改回空 ⇒ ①⑦/⑧ 必红（旁路复原）——证明判据真有牙。"""
    from autoforge import af_api as api_mod

    real_list = api_mod.svc.list_graphs
    monkeypatch.setattr(
        api_mod.svc, "list_graphs",
        lambda store, **kw: real_list(store),  # 丢弃 caller（= 修前形状）
    )
    r = env["client"].get("/api/graphs", headers=env["agent"])
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 3, "变异后必须回到全量（判据①失效＝有牙）"
    assert "hidden_count" not in body
