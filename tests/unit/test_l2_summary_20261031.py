"""v1.15.0（DCD 裁定 20261031·Q2 甲）：部署审批摘要机提取 L2 列表判据。

★ 硬判据：摘要列表由 `l2_summary_of` 从 IR 提取（⛔ 人肉手写），L2 命中逐条列出
（动作名＋中文别名＋归属自动化）；非 L2 不列；列表为空返回空数组（前端渲染占位）。
"""

from __future__ import annotations

from autoforge.af_service import l2_summary_of


def _ir(action: str, name: str = "空调", *, adapter: str = "ha") -> dict:
    return {
        "automations": [{
            "ir_version": "0.2.1", "id": "a1", "name": name, "version": 1, "mode": "single",
            "nodes": [{"id": "d1", "kind": "do", "name": "d", "adapter": adapter,
                       "action": action, "params": {"entity_id": "x"}}],
            "edges": [],
        }],
    }


def test_l2_action_is_listed_with_alias():
    out = l2_summary_of(_ir("climate.turn_on"))
    assert len(out) == 1, out
    assert out[0]["action"] == "climate.turn_on"
    assert out[0]["alias"] == "打开空调", out[0]
    assert out[0]["automation"] == "空调"
    assert out[0]["node"] == "d1"


def test_l1_actions_are_not_listed():
    assert l2_summary_of(_ir("light.turn_on")) == []
    assert l2_summary_of(_ir("switch.turn_off")) == []


def test_lock_and_cover_are_listed():
    for act in ("lock.lock", "cover.close_cover"):
        out = l2_summary_of(_ir(act))
        assert len(out) == 1 and out[0]["action"] == act, (act, out)


def test_unrecognized_action_not_listed():
    """认不出的动作名不进摘要（它由 L2_NEEDS_CONFIRM 的另一支文案负责）。"""
    assert l2_summary_of(_ir("light.turn_onnn")) == []


def test_empty_ir_returns_empty_list_not_none():
    assert l2_summary_of({"automations": []}) == []
    assert l2_summary_of({}) == []


def test_multiple_l2_across_automations_all_listed():
    ir = _ir("climate.turn_on")
    ir["automations"].append({
        "ir_version": "0.2.1", "id": "a2", "name": "门锁", "version": 1, "mode": "single",
        "nodes": [{"id": "d2", "kind": "do", "name": "d", "adapter": "ha",
                   "action": "lock.lock", "params": {"entity_id": "x"}}],
        "edges": [],
    })
    out = l2_summary_of(ir)
    assert {o["action"] for o in out} == {"climate.turn_on", "lock.lock"}


# ── list_pending 每行附 l2_summary（UI-1 补格配套，PM 复核裁决 2026-11-01）──────
# DCD 矩阵行 2 的「审批时」＝批准前那一刻：待批列表就是审批面，⛔ 只在批准后的
# 回执上给＝人点下去的时候面前是空的。单一真源仍是 l2_summary_of，⛔ 前端不自算。


def _seed_catalog(root, entities):
    """在 store 根铺设备目录快照（submit_pending 实体闸的读盘形状，同 AF-704 判据文件）。"""
    import json
    cat = root / ".catalog" / "catalog.json"
    cat.parent.mkdir(parents=True, exist_ok=True)
    cat.write_text(json.dumps({"version": 1, "freshness": "", "entities": {
        eid: {"state": "off", "domain": eid.split(".", 1)[0]} for eid in entities
    }}, ensure_ascii=False), encoding="utf-8")


def _pending_ir() -> dict:
    """两条自动化（过静态闸现行语义）：a1 含 L2（climate.turn_on，带部署期显眼标记＋
    canary——卡 B 保留的矩阵 3/4 行强制），a2 纯 L1（light.turn_on）。"""
    return {
        "automations": [
            {"ir_version": "0.2.1", "id": "a1", "name": "空调", "version": 1, "mode": "single",
             "nodes": [
                 {"id": "o1", "kind": "on", "name": "o", "trigger": {"type": "state", "entity_id": "climate.x", "to": "on"}},
                 {"id": "d1", "kind": "do", "name": "d", "adapter": "ha",
                  "action": "climate.turn_on", "params": {"entity_id": "climate.x"},
                  "requires_confirm": True, "canary": {"duration": "15m", "auto_rollback": True}}],
             "edges": [{"from": "o1", "to": "d1", "kind": "then"}]},
            {"ir_version": "0.2.1", "id": "a2", "name": "灯", "version": 1, "mode": "single",
             "nodes": [
                 {"id": "o2", "kind": "on", "name": "o", "trigger": {"type": "state", "entity_id": "light.x", "to": "on"}},
                 {"id": "d2", "kind": "do", "name": "d", "adapter": "ha",
                  "action": "light.turn_on", "params": {"entity_id": "light.x"}}],
             "edges": [{"from": "o2", "to": "d2", "kind": "then"}]},
        ],
    }


def test_list_pending_items_carry_l2_summary(tmp_path):
    from autoforge.af_service import list_pending, submit_pending
    from autoforge.af_store import GraphStore

    root = tmp_path / "store"
    root.mkdir()
    _seed_catalog(root, {"climate.x", "light.x"})
    store = GraphStore(str(root))
    res = submit_pending(store, "af_save", {"ir": _pending_ir(), "name": "n"})
    out = list_pending(store)
    assert out["ok"] is True and len(out["items"]) == 1
    item = out["items"][0]
    assert item["l2_summary"] == res["l2_summary"], (item.get("l2_summary"), res.get("l2_summary"))
    assert [e["action"] for e in item["l2_summary"]] == ["climate.turn_on"]
    assert item["l2_summary"][0]["automation"] == "空调"
    assert item["l2_summary"][0]["alias"] == "打开空调"


def test_list_pending_l1_only_item_has_explicit_empty_list(tmp_path):
    """纯 L1 的待批条目：l2_summary 显式空数组（字段必须在，⛔ 缺字段＝没读出来）。"""
    from autoforge.af_service import list_pending, submit_pending
    from autoforge.af_store import GraphStore

    root = tmp_path / "store"
    root.mkdir()
    _seed_catalog(root, {"light.x"})
    store = GraphStore(str(root))
    ir = {"automations": [
        {"ir_version": "0.2.1", "id": "a2", "name": "灯", "version": 1, "mode": "single",
         "nodes": [
             {"id": "o2", "kind": "on", "name": "o", "trigger": {"type": "state", "entity_id": "light.x", "to": "on"}},
             {"id": "d2", "kind": "do", "name": "d", "adapter": "ha",
              "action": "light.turn_on", "params": {"entity_id": "light.x"}}],
         "edges": [{"from": "o2", "to": "d2", "kind": "then"}]},
    ]}
    submit_pending(store, "af_save", {"ir": ir, "name": "n"})
    item = list_pending(store)["items"][0]
    assert "l2_summary" in item and item["l2_summary"] == []
