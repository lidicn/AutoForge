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
