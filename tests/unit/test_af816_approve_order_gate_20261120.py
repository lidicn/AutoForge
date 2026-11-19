# -*- coding: utf-8 -*-
"""AF-816 判据 · approve_pending 先删后落盘修复（墨鉴 N-01，2026-11-20）。

缺陷形状：`af_service.approve_pending` 旧实现**先 `ps.delete(op_id)` 再 `save_graph`**——
`save_graph` 的爆炸半径>8 未传 allow_bulk／archived 写闸／owner 变更／expect_version 冲突
任一抛错 ⇒ 人已批准、授权码已核销（af_mcp.py:492 在 submit 前烧码）、待批条目却已消失
⇒ 不可重试孤儿（「批准了但没落盘、待批也没了」）。

修法（AF-PM 卡面）：save_graph 成功后再 ps.delete（失败保留待批＋具名错误；防重批用 op
幂等键，⛔ 不回「先删」旧语义）。

判据：
- A1 save_graph 失败 ⇒ 待批条目**仍在**盘上（可重试），且抛具名 ServiceError
- A2 save_graph 成功 ⇒ 待批条目删除、归档落盘（既有语义不回归）
- A3 防重批：同一 op 第二次 approve ⇒ 404（op 幂等键）
- A4 源码钉：`ps.delete` 必须排在 `save_graph` 调用之后（防回退「先删」）
"""

from __future__ import annotations

import inspect

import pytest

from autoforge import af_service as svc
from autoforge.af_ir import load_graph
from autoforge.af_store import GraphStore


#: 最小合法 IR（入口 + pass；进 save_graph 静态扫描要过）。
RAW_IR = {
    "ir_version": "0.2.1",
    "id": "af816_demo",
    "name": "af816_demo",
    "version": 1,
    "mode": "single",
    "nodes": [
        {"id": "o", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
        {"id": "p", "kind": "pass"},
    ],
    "edges": [{"from": "o", "to": "p", "kind": "then"}],
}


def _payload():
    return {
        "ir": RAW_IR,
        "name": "af816_demo",
        "note": "",
        "tags": None,
        "expect_version": None,
        "owner": "",
        "allow_bulk": False,
    }


def _submit(store, submitted_by="alice"):
    return svc.submit_pending(store, "af_save", _payload(),
                              submitted_by=submitted_by, agent_key=submitted_by)["pending"]


def _pending_exists(store, op_id) -> bool:
    return (store.root / "pending" / f"{op_id}.json").is_file()


# ── A1：save_graph 失败 ⇒ 待批保留＋具名错误 ─────────────────────────────

def test_a1_save_failure_keeps_the_pending_op(tmp_path, monkeypatch):
    store = GraphStore(str(tmp_path / "store"))
    op_id = _submit(store)

    def boom(*args, **kwargs):
        raise svc.ServiceError("IR 未通过静态扫描", status=400)

    monkeypatch.setattr(svc, "save_graph", boom)
    with pytest.raises(svc.ServiceError) as ei:
        svc.approve_pending(store, op_id, reviewer="bob")
    assert ei.value.status == 400
    assert _pending_exists(store, op_id), "save 失败 ⇒ 待批必须仍在（可重试，⛔ 孤儿化）"


def test_a1_save_archived_conflict_keeps_pending(tmp_path):
    """archived 写闸路径：`save_graph` 自己抛 403 ⇒ 待批仍在（不靠外部桩）。"""
    store = GraphStore(str(tmp_path / "store"))
    store.save(load_graph(RAW_IR), "af816_demo", owner="")
    store.set_tags("af816_demo", ["archived"])
    op_id = _submit(store)
    with pytest.raises(svc.ServiceError) as ei:
        svc.approve_pending(store, op_id, reviewer="bob")
    assert ei.value.status == 403
    assert _pending_exists(store, op_id), "archived 写闸拒绝 ⇒ 待批仍在"


# ── A2：save 成功 ⇒ 待批删除＋归档落盘（既有语义） ──────────────────────

def test_a2_success_removes_pending_and_deploys(tmp_path):
    store = GraphStore(str(tmp_path / "store"))
    op_id = _submit(store)
    approved = svc.approve_pending(store, op_id, reviewer="bob")
    assert approved["ok"] is True and approved["approved_by"] == "bob"
    assert not _pending_exists(store, op_id), "save 成功 ⇒ 待批应删除"
    assert store.latest("af816_demo") is not None, "归档应落盘"


# ── A3：防重批（op 幂等键） ─────────────────────────────────────────────

def test_a3_second_approve_is_404(tmp_path):
    store = GraphStore(str(tmp_path / "store"))
    op_id = _submit(store)
    svc.approve_pending(store, op_id, reviewer="bob")
    with pytest.raises(svc.ServiceError) as ei:
        svc.approve_pending(store, op_id, reviewer="carol")
    assert ei.value.status == 404, "第二次批准同一 op ⇒ 未找到（op 幂等键）"


# ── A4：源码钉 ─────────────────────────────────────────────────────────

def test_a4_delete_statement_comes_after_save_graph_call():
    """⛔ 防回退「先删」：`ps.delete` 源码位置必须在 `save_graph(` 之后。"""
    src = inspect.getsource(svc.approve_pending)
    lines = src.splitlines()
    save_abs = next(i for i, line in enumerate(lines, 1) if "save_graph(" in line)
    del_abs = next(i for i, line in enumerate(lines, 1) if "if not ps.delete(op_id)" in line)
    assert save_abs < del_abs, (
        f"save_graph(行 {save_abs}) 必须先于 ps.delete(行 {del_abs})（⛔ 回退先删）"
    )