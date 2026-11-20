# -*- coding: utf-8 -*-
"""AF-828 判据 · enable_by_tag 归属/旁车两缺陷（墨鉴 N-58＋N-59，2026-11-20）。

**① N-58（归属先于 archived）**：`names` 取全量、`archived_names` 从全量挑、raise 把
前 5 个原文拼进 detail，归属过滤 `scope_names` 在其后 ⇒ 非 owner 调一次就被**他人**归档名
连带整批 403＋错误文案泄露他人归档名（:875-876 注释正以「逐名 403 会泄露他人归档名」为由
拒绝逐名 _assert，一处写对、相邻一处违反它）。
修法：先 `scope_names` 过滤，archived 判定与报错只对**可见集**。

**② N-59（touched_ids 先收集后 save）**：`touched_ids` 在 try 前 append、save 失败
continue 未撤回 ⇒ 旁车对**保存失败**的 id 也写 enabled（「旁车说已启用、store 还是旧值」；
停用场景＝安全阀关不上）。
修法：touched_ids 在 save **成功之后**收集。

全部离线（tmp_path 真 GraphStore，⛔ 出网）。
"""

from __future__ import annotations

from typing import Any

import pytest

from autoforge.af_auth import AuthCodeStore
from autoforge.af_ir import Graph, load_automation
from autoforge.af_service import ServiceError, enable_by_tag
from autoforge.af_store import GraphStore
from autoforge.af_watch_toggle import read_overrides


def _ir(auto_id: str) -> dict[str, Any]:
    return {
        "ir_version": "0.2.1", "id": auto_id, "name": auto_id, "version": 1, "mode": "single",
        "nodes": [
            {"id": "t1", "kind": "on", "name": "人",
             "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
            {"id": "p1", "kind": "pass", "name": "结束"},
        ],
        "edges": [{"from": "t1", "to": "p1", "kind": "then"}],
    }


def _store_with(tmp_path, specs: list[tuple[str, list[str], str]]) -> tuple[GraphStore, AuthCodeStore]:
    """specs: [(归档名, 标签, owner)]。返回 (store, acs)——启用腿要真码（af778 先例）。"""
    store = GraphStore(str(tmp_path))
    for name, tags, owner in specs:
        store.save(Graph([load_automation(_ir(name))]), name, tags=tags, owner=owner)
    acs = AuthCodeStore(tmp_path / ".auth" / "auth_codes.json")
    return store, acs


# ══ ① N-58：归属先于 archived，不泄露他人归档名 ══════════════════════════

def test_n58_non_owner_403_does_not_leak_others_archived_name(tmp_path):
    """alice 与 bob 各有 archived 归档：alice 调 ⇒ 403 只点 alice 的，⛔ 出现 bob 的名字。"""
    store, acs = _store_with(tmp_path, [
        ("af828_alice", ["night", "archived"], "alice"),
        ("af828_bob", ["night", "archived"], "bob"),
    ])
    with pytest.raises(ServiceError) as ei:
        enable_by_tag(store, "night", True, allow_bulk=True,
                      caller="alice", token_identity=True)
    assert ei.value.status == 403
    detail = str(ei.value)
    assert "af828_alice" in detail, "自己的归档名可以点（他自己看得见）"
    assert "af828_bob" not in detail, f"⛔ 泄露他人归档名：{detail}"


def test_n58_others_archived_no_longer_rejects_whole_batch(tmp_path):
    """bob 的归档带 archived、alice 的是普通标签：alice 调 ⇒ 修前整批 403，修后成功翻自己的。"""
    store, acs = _store_with(tmp_path, [
        ("af828_bob", ["night", "archived"], "bob"),
        ("af828_alice", ["night"], "alice"),
    ])
    code = acs.create("short").code
    out = enable_by_tag(store, "night", True, allow_bulk=True,
                        caller="alice", token_identity=True, auth_code=code)
    assert out.get("ok") is True, f"⛔ 他人归档连带整批拒（修前形状）：{out}"
    names = [a["name"] for a in out.get("affected", [])]
    assert names == ["af828_alice"], f"只能翻自己的：{names}"
    # bob 的归档一字未动（仍带 archived、enabled 未翻）
    assert "archived" in (store.all_tags().get("af828_bob") or ())


def test_n58_owner_face_still_rejects_archived_batch(tmp_path):
    """owner 面行为保持：archived 在场 ⇒ 403 且点归档名（写闸不被削弱）。"""
    store, acs = _store_with(tmp_path, [("af828_arch", ["night", "archived"], "bob")])
    with pytest.raises(ServiceError) as ei:
        enable_by_tag(store, "night", True, allow_bulk=True,
                      caller="dsh", caller_is_owner=True)
    assert ei.value.status == 403
    assert "af828_arch" in str(ei.value)


# ══ ② N-59：旁车 id 只在 save 成功后收集 ════════════════════════════════

def test_n59_override_excludes_ids_whose_save_failed(tmp_path, monkeypatch):
    """一条 save 失败 ⇒ 旁车里 ⛔ 出现它的 id（修前会写 enabled=True）。"""
    store, acs = _store_with(tmp_path, [
        ("af828_ok", ["night"], "alice"),
        ("af828_bad", ["night"], "alice"),
    ])
    code = acs.create("short").code
    orig_save = GraphStore.save

    def flaky_save(self, graph, name, **kw):
        if name == "af828_bad":
            raise OSError("disk full (simulated)")
        return orig_save(self, graph, name, **kw)

    monkeypatch.setattr(GraphStore, "save", flaky_save)
    out = enable_by_tag(store, "night", True, allow_bulk=True,
                        caller="alice", token_identity=True, auth_code=code)
    monkeypatch.undo()

    failed = out.get("failed") or []
    assert any(f.get("name") == "af828_bad" for f in failed), "前置：失败项必须如实记"
    overrides = read_overrides(store.root)
    # 两个自动化的 id 都叫 af828_ok/af828_bad（IR id 即自动化 id）
    assert overrides.get("af828_ok") is True, "成功那条要写旁车"
    assert "af828_bad" not in overrides, (
        f"⛔ 保存失败条目的 id 也写了旁车（旁车说已启用、store 还是旧值）：{overrides}"
    )


def test_n59_override_has_all_ids_when_all_saves_succeed(tmp_path):
    """全成功路径行为保持：旁车收到全部成功条目的 id。"""
    store, acs = _store_with(tmp_path, [
        ("af828_a", ["night"], "alice"),
        ("af828_b", ["night"], "alice"),
    ])
    code = acs.create("short").code
    out = enable_by_tag(store, "night", True, allow_bulk=True,
                        caller="alice", token_identity=True, auth_code=code)
    assert out.get("ok") is True
    overrides = read_overrides(store.root)
    assert overrides.get("af828_a") is True
    assert overrides.get("af828_b") is True