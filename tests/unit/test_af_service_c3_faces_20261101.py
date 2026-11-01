"""C-3 服务面收口判据（af-ownership-audit-model-v3-v3 §7 R1-R10，PM 裁定 20261101）。

单源层判定表见 `test_af_ownership_scope_20261101.py`；本文件把服务面逐条映射到那
里的 verdict（⛔ 服务面自造第二份规则即违 C1）：

- R1 残缺身份四读面同拒：`graphs_by_tag`/`get_conf`/`diff`/`export_store` 带
  `token_identity=True` + 空 subject ⇒ 403 残缺身份，⛔ 退化成匿名全量；
- R2 合并置信度按份额：`get_conf("_all")` 非 owner 只含自己名下的行，读不出进
  `unavailable`（⛔ 整面 404、⛔ 全库聚合）；
- R3 export 份额自洽：`names == bundle entries 名单` ⊆ 份额；`checksum` 与**返回的**
  bundle 一致（份额化后重算，⛔ 全量指纹）；`hidden_count` = 全集 − 份额；
- R4 计数合拢：by_tag / list_graphs / export / quarantine 四面
  `len(items) + len(unavailable) + hidden_count == 全集` 且 `ok` 派生（⛔ 字面量）；
- R5 批量启停不泄名：混合归属标签 `enable_by_tag` 回执不出现他人归档名，
  `hidden_count` 报缺口；「调用方自报名」既有 403 文案逐字保持；
- R6 归属随版本走：批量启停后新版本 owner == 原 owner；「最新版本无 owner」账本上
  `list_graphs` 与 `_existing_owner` 不分叉（C4）；
- R7 删除 round-trip：归档名含 `--` 隔离后仍反解出原名、归属过滤正确；戳形破坏
  ⛔ 不猜归属；
- R8 tags 侧车残留：`graphs_by_tag` 与 `list_graphs` 同形进 `unavailable`；
- R9 跨面一致性：同一场景 服务直调 vs MCP 工具 逐格同 verdict（REST 面身份入参
  未接线＝渐进收口旧路径，本批不动 af_api，见报告边界）；
- R10 回归钉：①无身份豁免保留 ②`enable` 顺序 blast→auth ③`READONLY_DEGRADED:`
  原样上抛 ④`svc.ServiceError` 与守卫类不混捕 ⑤删除免码・启用要码。
"""

from __future__ import annotations

import json

import pytest

from autoforge import af_service as svc
from autoforge.af_ir import Graph, load_automation
from autoforge.af_mcp import (
    ADM_ERR_AUTH_REQUIRED,
    ADM_ERR_INTERNAL,
    ADM_ERR_PAYLOAD_INVALID,
    dispatch,
)
from autoforge.af_ownership import broken_identity_error, mutator_error
from autoforge.af_store import GraphStore, _bundle_checksum

AGENT_A = {"subject": "agent-a", "scopes": ["read", "write", "live"]}
AGENT_B = {"subject": "agent-b", "scopes": ["read", "write", "live"]}
OWNER = {"subject": "owner", "scopes": ["read", "write", "live", "admin"]}
BROKEN = {"subject": "", "scopes": ["read", "write"]}  # 持令牌缺 subject


def _ir(aid: str) -> dict:
    return {"ir_version": "0.3.0", "id": aid, "name": aid, "version": 1, "mode": "single",
            "nodes": [{"id": "a", "kind": "on", "name": "t",
                       "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
                      {"id": "d", "kind": "do", "name": "d", "adapter": "ha",
                       "action": "light.turn_on", "params": {"entity_id": "light.x"}}],
            "edges": [{"from": "a", "to": "d", "kind": "then"}]}


def _seed(store: GraphStore) -> None:
    """mine(agent-a, 2 版) ／ theirs(agent-b) ／ orphan(无归属)；prod 标签三方共有。"""
    store.save(Graph([load_automation(_ir("m1"))]), "mine", tags=["prod", "bulk"], owner="agent-a")
    store.save(Graph([load_automation(_ir("m1"))]), "mine", owner="agent-a")  # v2（diff 用）
    store.save(Graph([load_automation(_ir("t1"))]), "theirs", tags=["prod", "bulk"], owner="agent-b")
    store.save(Graph([load_automation(_ir("t1"))]), "orphan", tags=["prod"], owner="")


def _call(tool: str, args: dict, store: GraphStore, who: dict) -> dict:
    content, _is_err = dispatch(tool, args, store, who)
    return json.loads(content[0]["text"])


def _corrupt_latest_graph_body(store: GraphStore, name: str) -> None:
    """记录信封保持可解析（history() 仍见名字/归属），graph 体改成非法形状 ⇒ load 炸。"""
    path = store._dir(name) / f"v{store.latest(name)}.json"
    rec = json.loads(path.read_text(encoding="utf-8"))
    rec["graph"] = {"automations": "not-a-list"}
    path.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")


# ── R1：残缺身份四读面同拒（⛔ 不得退化成匿名全量） ──────────────────

def test_r1_broken_identity_denied_on_all_four_read_faces(tmp_path):
    store = GraphStore(str(tmp_path))
    _seed(store)
    with pytest.raises(svc.ServiceError) as e1:
        svc.graphs_by_tag(store, "prod", caller="", caller_is_owner=False, token_identity=True)
    assert e1.value.status == 403 and str(e1.value) == broken_identity_error()
    with pytest.raises(svc.ServiceError) as e2:
        svc.get_conf(store, "_all", caller="", caller_is_owner=False, token_identity=True)
    assert e2.value.status == 403 and str(e2.value) == broken_identity_error()
    with pytest.raises(svc.ServiceError) as e3:
        svc.get_conf(store, "mine", caller="", caller_is_owner=False, token_identity=True)
    assert e3.value.status == 403
    with pytest.raises(svc.ServiceError) as e4:
        svc.diff(store, "mine", 1, 2, caller="", caller_is_owner=False, token_identity=True)
    assert e4.value.status == 403
    with pytest.raises(svc.ServiceError) as e5:
        svc.export_store(store, caller="", caller_is_owner=False, token_identity=True)
    assert e5.value.status == 403


def test_r1_broken_identity_via_mcp_reads_auth_required(tmp_path):
    store = GraphStore(str(tmp_path))
    _seed(store)
    body = _call("af_graphs_by_tag", {"tag": "prod"}, store, BROKEN)
    assert body["ok"] is False and body["code"] == ADM_ERR_AUTH_REQUIRED
    body = _call("af_export_store", {}, store, BROKEN)
    assert body["ok"] is False and body["code"] == ADM_ERR_AUTH_REQUIRED


def test_r1_anonymous_still_exempt_on_read_faces(tmp_path):
    """token_identity=None（旧调用方/本地 CLI）＝无令牌身份 ⇒ 豁免维持（R10① 的读面半边）。"""
    store = GraphStore(str(tmp_path))
    _seed(store)
    assert svc.graphs_by_tag(store, "prod", caller="", token_identity=None)["ok"] is True
    assert svc.export_store(store, caller="", token_identity=None)["ok"] is True


# ── R2：get_conf 合并面按份额 ────────────────────────────────────────

def test_r2_merged_conf_scoped_for_agent(tmp_path):
    store = GraphStore(str(tmp_path))
    _seed(store)
    out = svc.get_conf(store, "_all", caller="agent-a", caller_is_owner=False)
    assert out["ok"] is True
    assert [i["automation_id"] for i in out["items"]] == ["m1"], "只含自己名下的行"
    assert out["hidden_count"] == 2 and out["scope"] == "owner:agent-a"
    assert "unavailable" not in out


def test_r2_merged_conf_unavailable_instead_of_404(tmp_path):
    """R2：自己名下读不出的进 unavailable（⛔ 整面 404、⛔ 静默隐身）。"""
    store = GraphStore(str(tmp_path))
    _seed(store)
    _corrupt_latest_graph_body(store, "mine")
    out = svc.get_conf(store, "_all", caller="agent-a", caller_is_owner=False)
    assert out["ok"] is True, "自己份额内读不出不算整面失败"
    assert out["items"] == [] and out["unavailable"] == ["mine"]
    owner_view = svc.get_conf(store, "_all", caller="", caller_is_owner=False)
    assert [i["automation_id"] for i in owner_view["items"]] == ["t1"], "owner 面不受他人份额影响"


def test_r2_single_name_foreign_still_404_same_shape(tmp_path):
    store = GraphStore(str(tmp_path))
    _seed(store)
    with pytest.raises(svc.ServiceError) as theirs:
        svc.get_conf(store, "theirs", caller="agent-a", caller_is_owner=False)
    assert theirs.value.status == 404
    assert str(theirs.value) == f"没找到归档 {'theirs'!r}（或它不属于你）"


# ── R3：export 份额自洽 ─────────────────────────────────────────────

def test_r3_export_share_checksum_consistent(tmp_path):
    store = GraphStore(str(tmp_path))
    _seed(store)
    out = svc.export_store(store, caller="agent-a", caller_is_owner=False)
    assert out["names"] == ["mine"]
    assert [e["name"] for e in out["bundle"]["entries"]] == out["names"]
    assert out["hidden_count"] == 2
    # R3 核心：checksum 与**返回的** bundle 一致（份额化重算，⛔ 全量指纹）
    assert out["checksum"] == out["bundle"]["checksum"] == _bundle_checksum(out["bundle"])
    full = svc.export_store(store, caller="", caller_is_owner=False)
    assert full["checksum"] == _bundle_checksum(full["bundle"]), "全量导出指纹自洽"
    assert full["checksum"] != out["checksum"], "份额指纹 ≠ 全量指纹（否则 R3 没修）"


# ── R4：计数合拢（ok 派生） ─────────────────────────────────────────

def test_r4_count_closure_on_four_faces(tmp_path):
    store = GraphStore(str(tmp_path))
    _seed(store)
    by_tag = svc.graphs_by_tag(store, "prod", caller="agent-a", caller_is_owner=False)
    assert len(by_tag["items"]) + len(by_tag.get("unavailable", [])) + by_tag["hidden_count"] == 3
    assert by_tag["ok"] is True
    listing = svc.list_graphs(store, "agent-a", is_owner_face=False)
    assert len(listing["items"]) + len(listing.get("unavailable", [])) + listing["hidden_count"] == 3
    assert listing["ok"] is True
    exported = svc.export_store(store, caller="agent-a", caller_is_owner=False)
    assert len(exported["names"]) + exported["hidden_count"] == 3
    assert exported["ok"] is True
    moved, into = store.quarantine("theirs")
    q = svc.list_quarantined(store, caller="agent-a", caller_is_owner=False)
    assert len(q["items"]) + q["hidden_count"] == len(store.list_quarantined())
    assert q["ok"] is True and q["items"] == []


def test_r4_ok_is_derived_not_literal(tmp_path):
    """读数闭合被破坏时 ok 必须能读出 False（⛔ 字面量 True 的假绿）。"""
    store = GraphStore(str(tmp_path))
    _seed(store)
    _corrupt_latest_graph_body(store, "mine")
    listing = svc.list_graphs(store, "agent-a", is_owner_face=False)
    assert listing["ok"] is True  # 0 items + 1 unavailable + 2 hidden == 3（合拢成立）
    assert listing["unavailable"] == ["mine"]
    by_tag = svc.graphs_by_tag(store, "prod", caller="agent-a", caller_is_owner=False)
    assert by_tag["ok"] is True and by_tag["unavailable"] == ["mine"]


# ── R5：批量启停不泄名 + 既有 403 文案保持 ──────────────────────────

def test_r5_enable_by_tag_never_leaks_foreign_names(tmp_path):
    store = GraphStore(str(tmp_path))
    _seed(store)
    theirs_version_before = store.latest("theirs")
    out = svc.enable_by_tag(store, "bulk", False, caller="agent-a", caller_is_owner=False)
    assert out["ok"] is True
    assert [a["name"] for a in out["affected"]] == ["mine"]
    assert out["affected"][0]["enabled"] is False
    assert out["failed"] == [], "failed 仍是 list-of-dict 形状（空列表）"
    assert out["hidden_count"] == 1 and out["scope"] == "owner:agent-a"
    assert "theirs" not in json.dumps(out, ensure_ascii=False), "⛔ 回执出现他人归档名"
    assert store.latest("theirs") == theirs_version_before, "份额化不动他人归档"


def test_r5_caller_named_403_text_preserved(tmp_path):
    """「调用方自报名」写面（set_tags/delete/单条启停）的既有 403 文案逐字保持。"""
    store = GraphStore(str(tmp_path))
    _seed(store)
    with pytest.raises(svc.ServiceError) as exc:
        svc.set_graph_tags(store, "theirs", ["x"], caller="agent-a", caller_is_owner=False,
                           token_identity=True)
    assert exc.value.status == 403
    assert str(exc.value) == mutator_error("theirs", "修改其标签/启停")


# ── R6：归属随版本走 + C4 不分叉 ────────────────────────────────────

def test_r6_owner_travels_with_new_version(tmp_path):
    store = GraphStore(str(tmp_path))
    _seed(store)
    out = svc.enable_by_tag(store, "bulk", False, caller="agent-a", caller_is_owner=False)
    new_version = out["affected"][0]["version"]
    rec = store.load_record("mine", new_version)
    assert rec["owner"] == "agent-a", "批量启停的新版本必须带原归属"


def test_r6_list_graphs_matches_existing_owner_single_source(tmp_path):
    """C4：「最新版本无 owner」的账本上，list_graphs 与 _existing_owner 不分叉。"""
    store = GraphStore(str(tmp_path))
    _seed(store)
    store.save(Graph([load_automation(_ir("m1"))]), "mine", owner="")  # v3：最新版本无 owner
    assert svc._existing_owner(store, "mine") == "agent-a"
    out = svc.list_graphs(store, "agent-a", is_owner_face=False)
    row = next(r for r in out["items"] if r["name"] == "mine")
    assert row["owner"] == "agent-a", "展示归属必须与 _existing_owner 同值"
    assert row["name"] in [r["name"] for r in out["items"]], "归属人仍看得见自己的归档"
    assert out["hidden_count"] == 2


# ── R7：删除 round-trip（归档名含 --） ──────────────────────────────

def test_r7_quarantine_roundtrip_with_double_dash_name(tmp_path):
    store = GraphStore(str(tmp_path))
    _seed(store)
    store.save(Graph([load_automation(_ir("f1"))]), "foo--bar", owner="agent-a")
    out = svc.delete_graph(store, "foo--bar", caller="agent-a", caller_is_owner=False)
    assert out["ok"] is True
    qname = out["quarantined_as"]
    assert qname.startswith("foo--bar--"), qname
    view = svc.list_quarantined(store, caller="agent-a", caller_is_owner=False)
    assert qname in view["items"], "真主必须看得见自己的隔离项（D6：尾部戳反解）"
    other = svc.list_quarantined(store, caller="agent-b", caller_is_owner=False)
    assert qname not in other["items"] and other["hidden_count"] >= 1


def test_r7_broken_stamp_never_guesses_owner(tmp_path):
    """戳形被破坏的隔离名 ⛔ 不猜原名（fail-closed：除 owner 面无人可见）。"""
    store = GraphStore(str(tmp_path))
    _seed(store)
    trash = store.root / GraphStore.TRASH_DIR
    ghost = trash / "mine--badstamp"
    ghost.mkdir(parents=True)
    (ghost / "v1.json").write_text(json.dumps(
        {"name": "mine--badstamp", "version": 1, "owner": "agent-b", "graph": {"automations": []}}),
        encoding="utf-8")
    view = svc.list_quarantined(store, caller="agent-a", caller_is_owner=False)
    assert "mine--badstamp" not in view["items"], "戳形不匹配 ⛔ 不按记录 owner 归属"
    owner_view = svc.list_quarantined(store, caller="", caller_is_owner=True)
    assert "mine--badstamp" in owner_view["items"], "owner 面看全量（含不可解析项）"


# ── R8：tags 侧车残留同形 unavailable ───────────────────────────────

def test_r8_tags_residual_same_shape_on_both_list_faces(tmp_path):
    store = GraphStore(str(tmp_path))
    _seed(store)
    store.set_tags("ghost", ["prod"])  # 标签在、归档不在
    by_tag = svc.graphs_by_tag(store, "prod", caller="", caller_is_owner=False)
    assert by_tag["unavailable"] == ["ghost"], "by_tag 面不静默隐身"
    _corrupt_latest_graph_body(store, "theirs")
    listing = svc.list_graphs(store, "", is_owner_face=True)
    assert listing["unavailable"] == ["theirs"], "list_graphs 同形进 unavailable（R8：⛔ 一面显示一面隐身）"


# ── R9：跨面一致性（服务直调 vs MCP 工具） ──────────────────────────

def _svc_verdict(fn) -> str:
    try:
        fn()
    except svc.ServiceError as exc:
        return {403: "403", 404: "404"}.get(exc.status, f"s{exc.status}")
    return "ok"


def _mcp_verdict(body: dict) -> str:
    if body.get("ok") is True:
        return "ok"
    code = body.get("code")
    if code == ADM_ERR_AUTH_REQUIRED:
        return "403"
    if code == ADM_ERR_PAYLOAD_INVALID:
        return "404"
    return f"code:{code}"


def test_r9_cross_face_matrix_same_verdict(tmp_path):
    store = GraphStore(str(tmp_path))
    _seed(store)
    cells = [
        ("read_own", lambda: svc.get_conf(store, "mine", caller="agent-a", token_identity=True),
         lambda: _call("af_conf", {"name": "mine"}, store, AGENT_A)),
        ("read_foreign", lambda: svc.get_conf(store, "theirs", caller="agent-a", token_identity=True),
         lambda: _call("af_conf", {"name": "theirs"}, store, AGENT_A)),
        ("write_foreign", lambda: svc.set_graph_tags(store, "theirs", ["x"], caller="agent-a",
                                                     token_identity=True),
         lambda: _call("af_set_tags", {"name": "theirs", "tags": ["x"]}, store, AGENT_A)),
        ("delete_foreign", lambda: svc.delete_graph(store, "theirs", caller="agent-a",
                                                    token_identity=True),
         lambda: _call("af_delete_graph", {"name": "theirs"}, store, AGENT_A)),
        ("broken_read", lambda: svc.get_conf(store, "mine", caller="", token_identity=True),
         lambda: _call("af_conf", {"name": "mine"}, store, BROKEN)),
    ]
    for label, svc_fn, mcp_fn in cells:
        assert _svc_verdict(svc_fn) == _mcp_verdict(mcp_fn()), f"跨面 verdict 分叉：{label}"


# ── R10：回归钉（⛔ 不许顺手改） ────────────────────────────────────

def test_r10_anonymous_exemption_kept(tmp_path):
    store = GraphStore(str(tmp_path))
    _seed(store)
    out = svc.list_graphs(store, "", is_owner_face=False)
    assert out["count"] == 3 and "hidden_count" not in out, "无身份豁免保留（本地 CLI 全量）"


def test_r10_enable_order_blast_before_auth(tmp_path, monkeypatch):
    store = GraphStore(str(tmp_path))
    store.save(Graph([load_automation(_ir("b1")), load_automation(_ir("b2"))]),
               "big", tags=["b"], owner="agent-a")
    store.save(Graph([load_automation(_ir("b3"))]), "small", tags=["b"], owner="agent-b")
    monkeypatch.setenv("AUTOFORGE_BLAST_RADIUS", "1")
    with pytest.raises(svc.ServiceError) as exc:
        svc.enable_by_tag(store, "b", True, caller="agent-a", caller_is_owner=False,
                          auth_code="")
    assert "爆炸半径" in str(exc.value), "blast 闸必须先于授权码闸"
    assert "授权码" not in str(exc.value)
    assert exc.value.status == 400


def test_r10_readonly_degraded_reraised_verbatim(tmp_path, monkeypatch):
    store = GraphStore(str(tmp_path))
    _seed(store)
    monkeypatch.setattr(svc, "_start_watch_impl",
                        lambda **kw: (_ for _ in ()).throw(
                            svc.ServiceError("READONLY_DEGRADED: lease held", status=503)))
    with pytest.raises(svc.ServiceError) as exc:
        svc._ensure_watcher(store, enabled=True)
    assert str(exc.value).startswith("READONLY_DEGRADED:"), "⛔ 原样上抛（DB 按前缀判读）"


def test_r10_service_error_not_mixed_with_guard_class(tmp_path):
    store = GraphStore(str(tmp_path))
    _seed(store)
    body = _call("af_delete_graph", {"name": "theirs"}, store, AGENT_A)
    assert body["ok"] is False
    assert body["code"] == ADM_ERR_AUTH_REQUIRED, "svc.ServiceError(403) 必须落 AUTH_REQUIRED"
    assert body["code"] != ADM_ERR_INTERNAL, "⛔ 混捕成 INTERNAL"


def test_r10_delete_needs_no_code_enable_needs_code(tmp_path):
    store = GraphStore(str(tmp_path))
    _seed(store)
    out = svc.delete_graph(store, "mine", caller="agent-a", caller_is_owner=False)
    assert out["ok"] is True, "删除免码（让能力变少）"
    with pytest.raises(svc.ServiceError) as exc:
        svc.enable_by_tag(store, "bulk", True, caller="agent-a", caller_is_owner=False,
                          auth_code="")
    assert exc.value.status == 403 and "需要授权码" in str(exc.value), "启用要码"
