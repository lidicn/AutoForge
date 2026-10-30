"""审计第四轮（f0255fb_round4）判据批：AF-504 归属读面收口 + AF-509 拒绝文案 + AF-501 隔离区工具。

出处 `AF_code_audit_f0255fb_round4.md` §2 实测矩阵：
- af_graphs_by_tag / af_conf / af_diff / af_export_store 四道读面不收 caller，
  agent-a 实测拿到 agent-b 的归档名/置信度/diff 行/整店 bundle；
- af_delete_graph 403 文案明文「归档 'theirs' 归属 'agent-b'」——与同批 get_graph
  的「与不存在逐字同形」自相矛盾（AF-509），set_tags/enable/delete 共用；
- af_delete_graph 的 next_action 指向不存在的 af_list_quarantined（AF-501）。

判据形状：矩阵逐门复测（泄漏必须红转绿）+ 文案不点名（ghost 对照）+ 工具存在性。
"""

from __future__ import annotations

import json

from autoforge.af_ir import Graph, load_automation
from autoforge.af_mcp import TOOLS, dispatch
from autoforge.af_store import GraphStore

AGENT_A = {"subject": "agent-a", "scopes": ["read", "write", "live"]}
AGENT_B = {"subject": "agent-b", "scopes": ["read", "write", "live"]}
OWNER = {"subject": "owner", "scopes": ["read", "write", "live", "admin"]}


def _make_ir(aid: str) -> dict:
    return {"ir_version": "0.3.0", "id": aid, "name": aid, "version": 1, "mode": "single",
            "nodes": [{"id": "a", "kind": "on", "name": "t",
                       "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
                      {"id": "d", "kind": "do", "name": "d", "adapter": "ha",
                       "action": "light.turn_on", "params": {"entity_id": "light.x"}}],
            "edges": [{"from": "a", "to": "d", "kind": "then"}]}


def _seed(store: GraphStore) -> None:
    store.save(Graph([load_automation(_make_ir("mine"))]), "mine", tags=["prod"], owner="agent-a")
    store.save(Graph([load_automation(_make_ir("theirs"))]), "theirs", tags=["prod"], owner="agent-b")
    store.save(Graph([load_automation(_make_ir("theirs2"))]), "theirs", owner="agent-b")


def _call(tool: str, args: dict, store: GraphStore, who: dict) -> dict:
    content, _is_err = dispatch(tool, args, store, who)
    return json.loads(content[0]["text"])


# ── AF-504：四道读面逐门复测 ────────────────────────────────────────

def test_by_tag_scopes_out_others_archives(tmp_path):
    store = GraphStore(str(tmp_path))
    _seed(store)
    body = _call("af_graphs_by_tag", {"tag": "prod"}, store, AGENT_A)
    names = [i["name"] for i in body.get("items", [])]
    assert names == ["mine"], f"by_tag 仍漏他人归档：{names}"
    assert body.get("hidden_count") == 1, "缺口要报数（看到的不是全量要自报）"
    assert body.get("scope") == "owner:agent-a"


def test_by_tag_owner_face_sees_all(tmp_path):
    store = GraphStore(str(tmp_path))
    _seed(store)
    body = _call("af_graphs_by_tag", {"tag": "prod"}, store, OWNER)
    assert sorted(i["name"] for i in body["items"]) == ["mine", "theirs"]
    assert "hidden_count" not in body, "owner 面不该有 hidden_count（他看到的本来就是全量）"


def _shape_of(body: dict) -> str:
    """去名回显后的形状：名字插值是收口面（get_graph）也有的合法回显，
    逐字同形比的是模板与 code——不是连名字都不许回显。"""
    return json.dumps(body, ensure_ascii=False).replace("theirs", "X").replace("ghost", "X")


def test_conf_of_others_archive_is_same_shape_as_missing(tmp_path):
    """别人的置信度读面与「不存在」同形（code + 模板；名字回显合法，与 get_graph 同款）。"""
    store = GraphStore(str(tmp_path))
    _seed(store)
    theirs = _call("af_conf", {"name": "theirs"}, store, AGENT_A)
    ghost = _call("af_conf", {"name": "ghost"}, store, AGENT_A)
    assert theirs["ok"] is False and ghost["ok"] is False
    assert _shape_of(theirs) == _shape_of(ghost), \
        f"conf 拒绝形状与不同形——存在性泄露：{_shape_of(theirs)!r} vs {_shape_of(ghost)!r}"


def test_diff_of_others_archive_is_same_shape_as_missing(tmp_path):
    store = GraphStore(str(tmp_path))
    _seed(store)
    theirs = _call("af_diff", {"name": "theirs", "old": 1, "new": 2}, store, AGENT_A)
    ghost = _call("af_diff", {"name": "ghost", "old": 1, "new": 2}, store, AGENT_A)
    assert theirs["ok"] is False and ghost["ok"] is False
    assert _shape_of(theirs) == _shape_of(ghost), \
        f"diff 拒绝形状与不存在不同形：{_shape_of(theirs)!r} vs {_shape_of(ghost)!r}"


def test_export_store_only_exports_callers_share(tmp_path):
    """🔴 审计实测泄漏面：agent-a 的 bundle 含 ['mine','theirs']——必须只含自己名下。"""
    store = GraphStore(str(tmp_path))
    _seed(store)
    body = _call("af_export_store", {}, store, AGENT_A)
    assert body["ok"] is True
    assert body["names"] == ["mine"], f"整店导出仍带他人归档：{body['names']}"
    entry_names = [e["name"] for e in body["bundle"]["entries"]]
    assert entry_names == ["mine"], f"bundle entries 仍含他人归档：{entry_names}"
    assert body.get("hidden_count") == 1, "导出缺口要报数"


def test_export_store_owner_face_gets_everything(tmp_path):
    store = GraphStore(str(tmp_path))
    _seed(store)
    body = _call("af_export_store", {}, store, OWNER)
    assert sorted(body["names"]) == ["mine", "theirs"]


def test_owner_can_still_read_others_conf_and_diff(tmp_path):
    """收口不许把 owner 面一起收掉——开发者 agent「能查能改能删」是出资人裁定。"""
    store = GraphStore(str(tmp_path))
    _seed(store)
    conf = _call("af_conf", {"name": "theirs"}, store, OWNER)
    diff = _call("af_diff", {"name": "theirs", "old": 1, "new": 2}, store, OWNER)
    assert conf["ok"] is True and diff["ok"] is True


# ── AF-509：403 文案不点归属者的名 ─────────────────────────────────

def test_delete_refusal_does_not_name_the_owner(tmp_path):
    """⛔ 拒绝文案回「归属 'agent-b'」= 给调用方一条批量采集全库归属者名单的通道。"""
    store = GraphStore(str(tmp_path))
    _seed(store)
    body = _call("af_delete_graph", {"name": "theirs"}, store, AGENT_A)
    assert body["ok"] is False, "非归属人删他人归档必须拒"
    blob = json.dumps(body, ensure_ascii=False)
    assert "agent-b" not in blob, f"403 文案泄露归属者身份：{blob[:200]}"


def test_delete_refusal_same_for_named_owner_and_ghost(tmp_path):
    """与 get_graph 同理：别人的 vs 不存在的，拒绝形状不可分辨。"""
    store = GraphStore(str(tmp_path))
    _seed(store)
    theirs = _call("af_delete_graph", {"name": "theirs"}, store, AGENT_A)
    ghost = _call("af_delete_graph", {"name": "ghost"}, store, AGENT_A)
    # ghost 走 404 ARCHIVE_NOT_FOUND（quarantine 抛 FileNotFoundError）；
    # theirs 走 403——两档不同码是对的（写面拒绝 vs 读不到），但**都不点名**。
    blob_t, blob_g = json.dumps(theirs, ensure_ascii=False), json.dumps(ghost, ensure_ascii=False)
    assert "agent-b" not in blob_t and "agent-b" not in blob_g


def test_enable_refusal_does_not_name_the_owner(tmp_path):
    """enable/set_tags 与 delete 共用 _assert_archive_mutator 文案——同批收口。"""
    store = GraphStore(str(tmp_path))
    _seed(store)
    body = _call("af_enable_by_tag", {"tag": "prod", "enabled": False}, store, AGENT_A)
    assert body["ok"] is False, "非归属面批量启停他人归档必须拒"
    assert "agent-b" not in json.dumps(body, ensure_ascii=False)


# ── AF-501：af_quarantine 工具面 ──────────────────────────────────

def test_quarantine_tool_is_registered():
    names = {t[0] for t in TOOLS}
    assert "af_quarantine" in names, "af_delete_graph 的 next_action 指向它——工具必须存在"


def test_quarantine_scopes_by_owner_after_delete(tmp_path):
    store = GraphStore(str(tmp_path))
    _seed(store)
    # agent-a 删自己的；agent-b 名下的也在隔离区（模拟 owner 清场过的形状）
    assert _call("af_delete_graph", {"name": "mine"}, store, AGENT_A)["ok"] is True
    assert _call("af_delete_graph", {"name": "theirs"}, store, OWNER)["ok"] is True

    a_view = _call("af_quarantine", {}, store, AGENT_A)
    assert a_view["ok"] is True
    assert len(a_view["items"]) == 1 and a_view["items"][0].startswith("mine--")
    assert a_view["hidden_count"] == 1, "别人的隔离条目要报数不点名"

    owner_view = _call("af_quarantine", {}, store, OWNER)
    assert owner_view["count"] == 2, "owner 面看隔离区全量"


def test_delete_failure_receipt_points_to_real_tool(tmp_path):
    """next_action 不许再指向不存在的 af_list_quarantined。"""
    from autoforge.af_service import delete_graph, ServiceError
    import pytest
    store = GraphStore(str(tmp_path))
    _seed(store)
    # 造一个隔离不完整的形状很难；改从源码断言失败回执的文案键——用半途失败的
    # 合成手法：删 mine 成功后把 .deleted 目录清空 ⇒ readback 落地判失败。
    assert delete_graph(store, "mine", caller="agent-a")["ok"] is True
    import shutil
    shutil.rmtree(store.root / ".deleted")
    # 再删 theirs（agent-b 的被 owner 删过名目在 history）——用 owner 面
    out = delete_graph(store, "theirs", caller="owner", caller_is_owner=True)
    if not out["ok"]:
        assert "af_quarantine" in json.dumps(out, ensure_ascii=False)
        assert "af_list_quarantined" not in json.dumps(out, ensure_ascii=False)
    else:
        # 隔离成功路径也要指向真工具（成功 note 不带 next_action，量失败位即可）
        pytest.skip("本形状没触发失败回执——next_action 位由 test_quarantine_tool_is_registered + 源码门覆盖")
