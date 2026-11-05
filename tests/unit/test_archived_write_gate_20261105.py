# -*- coding: utf-8 -*-
"""判据 D3 · archived 标签＝写闸（2026-11-05 实证缺陷）· 卡名 AF卡_archived_write_gate。

今晨实证：归档件被 MCP 直接覆盖部署（v4 就落在一份归档档上），底下持续真跑（74+19 次触发）
——「界面上死了、底下活着」。根因：`archived` 标签此前**只有展示层认**（`af_service.py` 的
`archived`/`status` 两格），三类写/装载面一个都没拦。

三面都要拒（解档唯一路径＝显式 `set_tags` 摘标签）：
  D1 `save_graph`（覆盖部署）⇒ 403
  D2 `enable_by_tag`（按标签批量启停）⇒ 403
  D3 `_build_merged_ir`（watch 装载）⇒ 该档不进合并 IR
  D4 反证牙：把 archived 标签摘掉后，三面全部放行（证明闸认的是标签，不是"这档坏了"）
  D5 牙齿（源码钉）：三处都有 archived 判定
"""

from __future__ import annotations

import pytest

from autoforge.af_ir import load_graph  # noqa: F401  (保持与生产同源的装载路径)
from autoforge.af_service import ServiceError, _build_merged_ir, enable_by_tag, save_graph
from autoforge.af_store import GraphStore

IR = {
    "ir_version": "0.2.1", "id": "g_arch", "name": "g_arch", "version": 1, "mode": "single",
    "nodes": [
        {"id": "t1", "kind": "on", "name": "t",
         "trigger": {"type": "state", "entity_id": "binary_sensor.x", "to": "on"}},
        {"id": "p1", "kind": "pass", "name": "p"},
    ],
    "edges": [{"from": "t1", "to": "p1", "kind": "then"}],
}


@pytest.fixture()
def env(tmp_path):
    store = GraphStore(str(tmp_path / "store"))
    save_graph(store, {"automations": [dict(IR)]}, "g_arch", owner="dsh")
    store.set_tags("g_arch", ["archived", "ac"])
    return store


def _save(store):
    return save_graph(store, {"automations": [dict(IR, version=2)]}, "g_arch", owner="dsh")


# ── D1 覆盖部署被拒 ──────────────────────────────────────────────
def test_d1_save_refused_on_archived(env):
    with pytest.raises(ServiceError) as ei:
        _save(env)
    assert ei.value.status == 403
    assert "archived" in str(ei.value)


# ── D2 按标签批量启停被拒 ────────────────────────────────────────
def test_d2_enable_by_tag_refused_on_archived(env):
    with pytest.raises(ServiceError) as ei:
        enable_by_tag(env, "ac", False, allow_bulk=True, caller="dsh", caller_is_owner=True)
    assert ei.value.status == 403
    assert "archived" in str(ei.value)


# ── D3 watch 装载跳过归档档 ─────────────────────────────────────
def test_d3_merged_ir_skips_archived(env):
    merged = _build_merged_ir(env)
    ids = [a.get("id") for a in (merged or {}).get("automations", [])]
    assert "g_arch" not in ids, f"归档档⛔ 不该进 watch 合并 IR，实得 {ids}"


# ── D4 反证牙：摘标签后三面放行 ─────────────────────────────────
def test_d4_all_three_open_after_unarchive(env):
    env.set_tags("g_arch", ["ac"])          # 解档＝摘标签
    out_save = _save(env)
    assert out_save, "解档后应能覆盖部署"
    merged = _build_merged_ir(env)
    ids = [a.get("id") for a in (merged or {}).get("automations", [])]
    assert "g_arch" in ids, f"解档后应进 watch 合并 IR，实得 {ids}"
    # 启停面：启用路径要授权码（既有硬口径），所以「放行」在这里表现为
    # **报错不再是 archived**，而是走到下一道闸（授权码）——差分牙同样成立。
    with pytest.raises(ServiceError) as ei:
        enable_by_tag(env, "ac", True, allow_bulk=True, caller="dsh", caller_is_owner=True)
    assert "archived" not in str(ei.value), f"解档后不该再被 archived 拦：{ei.value}"
    assert "授权码" in str(ei.value), f"应走到授权码那道闸，实得：{ei.value}"


# ── D5 源码钉 ───────────────────────────────────────────────────
def test_d5_source_gates_present():
    import inspect

    from autoforge import af_service

    src = inspect.getsource(af_service)
    assert src.count('"archived"') >= 3, "save/enable/merged 三处都要有 archived 判定"
    assert "不许覆盖部署" in src and "不许按标签批量启停" in src
