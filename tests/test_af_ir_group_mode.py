"""F9 group 节点第 3 步：`mode: sequence|parallel` 的 schema↔模型同步 + 原子性验收。

铁律 §五 #1：字段先加在 `ir.schema.json`，`models.py` 只是它的投影。
本文件把两处枚举的同步、空 group 的拒收、以及「模拟中途失败不留半部署」钉住。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoforge import af_pending, af_service
from autoforge.af_apply import apply_group
from autoforge.af_ir import GROUP_MODES, GROUP_MODE_SEQUENCE, NODE_KINDS, SCHEMA_PATH
from autoforge.af_ir.models import IRValidationError
from autoforge.af_nl import render_automation
from autoforge.af_orchestrator import compose_group, render_node


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _node_props() -> dict:
    return _schema()["$defs"]["node"]["properties"]


# ───────────────────────────────────────────────────────────────── 同步（铁律 #1）

def test_node_mode_enum_syncs_group_modes_constant():
    """schema 的 node.mode.enum 必须等于 af_ir.GROUP_MODES。

    两处枚举靠人记就会漂移——`af_spec._NODE_KINDS` 至今少一个 group 就是先例。
    """
    assert _node_props()["mode"]["enum"] == list(GROUP_MODES)


def test_node_kind_enum_syncs_node_kinds_constant():
    assert _node_props()["kind"]["enum"] == list(NODE_KINDS)


def test_ir_version_enum_syncs_supported_versions():
    from autoforge.af_ir import SUPPORTED_IR_VERSIONS

    assert _schema()["properties"]["ir_version"]["enum"] == list(SUPPORTED_IR_VERSIONS)


# ───────────────────────────────────────────────────────────────── 子自动化非空

def _child(cid: str, entity: str, action_entity: str | None = None) -> dict:
    return {
        "ir_version": "0.3.0",
        "id": cid,
        "name": f"子自动化 {cid}",
        "version": 1,
        "mode": "single",
        "nodes": [
            {"id": "on1", "kind": "on",
             "trigger": {"type": "state", "entity_id": entity, "to": "on"}},
            {"id": "do1", "kind": "do", "adapter": "ha", "action": "light.turn_on",
             "params": {"entity_id": action_entity or f"light.{cid}"}},
        ],
        "edges": [{"from": "on1", "to": "do1", "kind": "then"}],
    }


def test_group_node_without_children_is_rejected():
    """空 group 会「一条都没部署」却报 ok=True——schema 直接拒收（minItems 1）。"""
    from autoforge.af_ir import Automation

    with pytest.raises(IRValidationError):
        Automation.from_dict({
            "ir_version": "0.3.0", "id": "grp_empty", "name": "空组", "version": 1,
            "mode": "group",
            "nodes": [{"id": "g1", "kind": "group", "children": []}],
            "edges": [],
        })


def test_group_node_missing_children_is_rejected():
    from autoforge.af_ir import Automation

    with pytest.raises(IRValidationError):
        Automation.from_dict({
            "ir_version": "0.3.0", "id": "grp_no_children", "name": "缺字段组", "version": 1,
            "mode": "group",
            "nodes": [{"id": "g1", "kind": "group"}],
            "edges": [],
        })


def test_compose_group_rejects_empty_children():
    with pytest.raises(ValueError, match="没有子自动化"):
        compose_group("晚安", [])


# ───────────────────────────────────────────────────────────────── mode 往返

def test_compose_group_defaults_to_sequence():
    g = compose_group("晚安", [_child("a_lamp", "input_boolean.night")])
    gnode = next(n for n in g.nodes.values() if n.kind == "group")
    assert gnode.mode == GROUP_MODE_SEQUENCE
    assert gnode.raw["mode"] == "sequence"


def test_compose_group_parallel_roundtrips_through_ir():
    g = compose_group("晚安", [_child("a_lamp", "input_boolean.night"),
                              _child("b_lamp", "input_boolean.night")], mode="parallel")
    gnode = next(n for n in g.nodes.values() if n.kind == "group")
    assert gnode.mode == "parallel"
    # 重新从 raw 载入（落盘→读盘的同一条路）后 mode 仍在
    from autoforge.af_ir import Automation

    again = Automation.from_dict(g.raw)
    assert next(n for n in again.nodes.values() if n.kind == "group").mode == "parallel"


def test_compose_group_rejects_unknown_mode():
    with pytest.raises(ValueError, match="sequence"):
        compose_group("晚安", [_child("a_lamp", "input_boolean.night")], mode="random")


def test_group_mode_is_rejected_at_schema_level():
    from autoforge.af_ir import Automation

    with pytest.raises(IRValidationError):
        Automation.from_dict({
            "ir_version": "0.3.0", "id": "grp_bad", "name": "坏 mode", "version": 1,
            "mode": "group",
            "nodes": [{"id": "g1", "kind": "group", "mode": "sideways",
                       "children": [_child("a_lamp", "input_boolean.night")]}],
            "edges": [],
        })


# ───────────────────────────────────────────────────────────────── NL 渲染

def test_nl_renders_group_mode_wording():
    g = compose_group("晚安", [_child("a_lamp", "input_boolean.night"),
                              _child("b_lamp", "input_boolean.night")])
    text = render_automation(g).text
    assert "依次按序下发" in text
    assert "未知节点" not in text

    parallel = compose_group("晚安", [_child("a_lamp", "input_boolean.night"),
                                     _child("b_lamp", "input_boolean.night")], mode="parallel")
    assert "可并行下发" in render_automation(parallel).text


def test_spec_text_render_refuses_group_instead_of_dropping_children():
    """`render_node` 过去把 group 渲染成 `pass g1`——文本"看起来合法"，整棵子树没了。"""
    node = {"id": "g1", "kind": "group", "name": "晚安",
            "mode": "sequence", "children": [_child("a_lamp", "input_boolean.night")]}
    with pytest.raises(ValueError, match="静默丢弃"):
        render_node(node)


# ───────────────────────────────────────────────────────────────── 失败时机

def _patch_sim(monkeypatch, fail_ids):
    calls: list[str] = []

    def fake_sim(ir, store=None):
        calls.append(ir.get("id", ""))
        if ir.get("id") in fail_ids:
            return {"ok": False, "reason": "sim boom"}
        return {"ok": True}

    monkeypatch.setattr(af_service, "simulate", fake_sim)
    return calls


def _patch_submit_all_ok(monkeypatch):
    monkeypatch.setattr(
        af_service, "submit_pending",
        lambda store, source, payload, submitted_by: {"ok": True, "pending": f"op-{payload['ir']['id']}"},
    )


def test_sequence_stops_simulating_at_first_failure(monkeypatch):
    children = [_child("c1", "input_boolean.night"), _child("c2", "input_boolean.night"),
                _child("c3", "input_boolean.night")]
    _patch_submit_all_ok(monkeypatch)
    calls = _patch_sim(monkeypatch, {"c2"})
    res = apply_group(compose_group("晚安", children, mode="sequence"), store=object(), stage="apply")
    assert res["ok"] is False
    assert res["group_mode"] == "sequence"
    assert calls == ["c1", "c2"]                 # c3 未仿真：到第一个失败即停
    assert res["error"]["failures"] == ["c2"]


def test_parallel_simulates_every_child_before_failing(monkeypatch):
    children = [_child("c1", "input_boolean.night"), _child("c2", "input_boolean.night"),
                _child("c3", "input_boolean.night")]
    _patch_submit_all_ok(monkeypatch)
    calls = _patch_sim(monkeypatch, {"c2", "c3"})
    res = apply_group(compose_group("晚安", children, mode="parallel"), store=object(), stage="apply")
    assert res["ok"] is False
    assert res["group_mode"] == "parallel"
    assert calls == ["c1", "c2", "c3"]            # 无依赖 → 全部试完再判定
    assert res["error"]["failures"] == ["c2", "c3"]


def test_parallel_stops_enqueuing_after_first_submit_failure(monkeypatch):
    """入队阶段的失败仍然整体回滚：mode 只改变"要不要继续试后续"，不改变原子性。"""
    children = [_child("c1", "input_boolean.night"), _child("c2", "input_boolean.night"),
                _child("c3", "input_boolean.night")]
    _patch_sim(monkeypatch, set())
    seen: list[str] = []

    def fake_submit(store, source, payload, submitted_by):
        cid = payload["ir"]["id"]
        if cid == "c2":
            raise af_service.ServiceError("待批熔断", status=400)
        seen.append(cid)
        return {"ok": True, "pending": f"op-{cid}"}

    monkeypatch.setattr(af_service, "submit_pending", fake_submit)
    rolled: list[str] = []
    monkeypatch.setattr(af_service, "reject_pending",
                        lambda store, op_id, reason="": rolled.append(op_id) or {"ok": True})

    res = apply_group(compose_group("晚安", children, mode="parallel"), store=object(), stage="apply")
    assert res["ok"] is False
    assert seen == ["c1", "c3"]                   # parallel：c2 失败仍试了 c3
    assert sorted(rolled) == ["op-c1", "op-c3"]   # 但两条都被摘掉，队列不留半个
    assert res["deployed"] == []


# ───────────────────────────────────────────────────────────────── 原子性验收（真store）

def test_real_store_leaves_no_residue_when_third_child_fails(tmp_path, monkeypatch):
    """第 3 步④ 的验收：前两条**真的**进了待批队列，第三条失败后队列必须为空。

    这里不打桩队列：submit / reject 都走真实 `PendingStore`（磁盘上的 json），
    因为原实现的回滚调的是 `store.rollback_pending(...)`——真实 GraphStore 上并没有
    这个方法，且传的是 child.id 而不是 pending op_id。桩测永远看不到这个洞。
    """
    from autoforge.af_store import GraphStore

    store = GraphStore(tmp_path)
    children = [_child("c1", "input_boolean.night"), _child("c2", "input_boolean.night"),
                _child("c3", "input_boolean.night")]
    _patch_sim(monkeypatch, set())

    real_submit = af_service.submit_pending
    attempts: list[str] = []

    def submit_third_fails(st, tool, payload, **kw):
        cid = payload["ir"]["id"]
        if cid == "c3":
            raise af_service.ServiceError("拒绝归档：IR 未通过静态扫描（1 个错误）", status=400)
        attempts.append(cid)
        return real_submit(st, tool, payload, **kw)

    monkeypatch.setattr(af_service, "submit_pending", submit_third_fails)

    res = apply_group(compose_group("晚安", children, mode="sequence"), store=store, stage="apply")

    assert res["ok"] is False
    assert res["error"]["code"] == "SUBMIT_FAILED"
    assert attempts == ["c1", "c2"]               # 前两条确实入了队（不是桩）
    assert res["rolled_back"] == ["c1", "c2"]
    assert res["deployed"] == []
    assert res["rollback_failed"] == []
    # 队列本体：真实落盘的待批条目必须一条不剩
    ps = af_pending.PendingStore(store.root)
    assert ps.list() == []
    assert list(ps.pending_dir.glob("*.json")) == []


def test_real_store_reports_residue_when_rollback_cannot_delete(tmp_path, monkeypatch):
    """回滚把手被抽掉（队列文件删不掉）→ 必须报出残留，不许谎称 deployed 为空（铁律 #5）。"""
    from autoforge.af_store import GraphStore

    store = GraphStore(tmp_path)
    children = [_child("c1", "input_boolean.night"), _child("c2", "input_boolean.night")]
    _patch_sim(monkeypatch, set())

    real_submit = af_service.submit_pending

    def submit_second_fails(st, tool, payload, **kw):
        if payload["ir"]["id"] == "c2":
            raise af_service.ServiceError("待批熔断", status=400)
        return real_submit(st, tool, payload, **kw)

    monkeypatch.setattr(af_service, "submit_pending", submit_second_fails)
    monkeypatch.setattr(af_pending.PendingStore, "delete", lambda self, op_id: False)

    res = apply_group(compose_group("晚安", children), store=store, stage="apply")
    assert res["ok"] is False
    # delete 返回 False 意味着条目还在队列里，reject_pending 据此抛错，残留必须被报出来
    assert res["deployed"] == ["c1"]
    assert [f["child"] for f in res["rollback_failed"]] == ["c1"]
