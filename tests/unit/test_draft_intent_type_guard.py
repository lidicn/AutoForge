"""G5 判据腿：draft_intent 收到非 dict 时必须抛 E_INTENT_NOT_OBJECT。

背景（2026-10-24 实测复现）：`draft_intent('打开客厅灯')` 不抛"你传了字符串"，
而是把字符串逐字符迭代，每个汉字成"未知域"，抛 `E_UNKNOWN_DOMAIN`。
这把真实协议错误（该传 dict 却传了 str）伪装成"域名单错了"——agent 拿到的
`fix.allowed` 是字段表，会去改字段名，而真正的修法是包一层 dict。误导性归因。

本文件钉五条：str / list / None 三种非对象 → 新码；正常 dict 不回归；
真未知域仍走 E_UNKNOWN_DOMAIN（原有行为不动）。
"""

from __future__ import annotations

import pytest

from autoforge.af_draft import (
    E_INTENT_NOT_OBJECT,
    E_UNKNOWN_DOMAIN,
    DraftError,
    draft_intent,
)


def _when() -> dict:
    return {"type": "state", "entity": "前门", "to": "on"}


def test_str_intent_raises_not_object():
    with pytest.raises(DraftError) as ei:
        draft_intent("打开客厅灯")
    assert ei.value.code == E_INTENT_NOT_OBJECT
    assert "str" in ei.value.args[0]
    # 不要回灌字段名表——真正的修法是包 dict，不是改字段名
    assert ei.value.fix is not None
    assert ei.value.fix.get("expected") == "object"


def test_list_intent_raises_not_object():
    with pytest.raises(DraftError) as ei:
        draft_intent(["name", "开门"])
    assert ei.value.code == E_INTENT_NOT_OBJECT
    assert "list" in ei.value.args[0]


def test_none_intent_raises_not_object():
    with pytest.raises(DraftError) as ei:
        draft_intent(None)
    assert ei.value.code == E_INTENT_NOT_OBJECT
    assert "NoneType" in ei.value.args[0]


def test_dict_path_unaffected():
    """正常 dict 路径照旧走通（不回归）。"""
    res = draft_intent(
        {"name": "开灯", "when": _when(), "do": {"action": "开灯", "target": "客厅灯"}}
    )
    assert res["ok"] is True
    assert "ref" in res


def test_unknown_domain_still_unknown_domain():
    """真·未知域行为不动：合法 dict 但多一个未登记顶层键 → 仍 E_UNKNOWN_DOMAIN。"""
    with pytest.raises(DraftError) as ei:
        draft_intent({"name": "x", "when": _when(), "do": {"action": "开灯"}, "bogus": 1})
    assert ei.value.code == E_UNKNOWN_DOMAIN
    assert "bogus" in ei.value.fix["unknown"]


# ── 字段级形状闸（体验者崩溃 2026-10-27）────────────────────────────
# G5 修的是"顶层 intent 是 str"；体验者撞的是字段级：when/if/do/ask/params
# 写成字符串时，下游成员运算崩 AttributeError → ADM_ERR_INTERNAL。
# 本族钉：四形状各出一条带 fix 的 DraftError，且正常形状（含 params 透传）不回归。

def _base_do() -> dict:
    return {"action": "开空调", "target": "书房空调"}


@pytest.mark.parametrize("broken,code", [
    ({"name": "t", "when": "书房门关了", "do": _base_do()}, "E_INVALID_WHEN"),
    ({"name": "t", "when": _when(), "if": "温度>26.5", "do": _base_do()}, "E_INVALID_EXPR"),
    ({"name": "t", "when": _when(), "do": "开空调"}, "E_INVALID_DO"),
    ({"name": "t", "when": _when(), "do": {"action": "开空调", "target": "书房空调",
                                            "params": "cool"}}, "E_INVALID_DO"),
    ({"name": "t", "when": _when(), "ask": "要开吗？", "do": _base_do()}, "E_INVALID_ASK"),
])
def test_field_shape_raises_draft_error_with_fix(broken, code):
    with pytest.raises(DraftError) as ei:
        draft_intent(broken)
    assert ei.value.code == code
    assert getattr(ei.value, "fix", None), "fix 必须可机读（否则 agent 只能猜）"


def test_valid_nested_intent_and_params_passthrough_compiles():
    """护栏：正常嵌套形状 + 显式 params 透传照旧编译（闸不误伤正路）。"""
    r = draft_intent({
        "name": "t", "when": _when(),
        "do": [{"action": "ha:climate.set_hvac_mode", "target": "书房空调",
                "params": {"hvac_mode": "cool"}}],
    }, catalog=None)
    assert r["ok"] is True
    # params 合并：entity_id 由 target 解析补上，hvac_mode 从 params 透传
    from autoforge.af_draft import _staging
    nodes = _staging.get(r["ref"])["graph"].automations[0].nodes
    do_node = [n for n in nodes.values() if n.kind == "do"][0]
    assert do_node.params["hvac_mode"] == "cool"
    assert "entity_id" in do_node.params


def test_tester_study_scene_intent_compiles_end_to_end():
    """体验者书房原案（真 entity_id、and/or/is_on 组合、三动作）必须整条编译过。"""
    r = draft_intent({
        "name": "书房空调自动制冷",
        "when": {"entity": "binary_sensor.door_contact", "to": "off"},
        "if": {"and": [
            {"gt": {"var": "sensor.study_temp", "const": 26.5}},
            {"is_on": {"var": "switch.pc"}},
            {"or": [{"is_on": {"var": "switch.spot"}}, {"is_on": {"var": "switch.ceiling"}}]},
        ]},
        "do": [
            {"action": "climate.turn_on", "target": "climate.lumi_cn_84159632_v2"},
            {"action": "ha:climate.set_hvac_mode", "target": "climate.lumi_cn_84159632_v2",
             "params": {"hvac_mode": "cool"}},
            {"action": "ha:climate.set_temperature", "target": "climate.lumi_cn_84159632_v2",
             "params": {"temperature": 26}},
        ],
    }, catalog=None)
    assert r["ok"] is True, r


def test_action_prefix_normalized_not_double_prefixed():
    """体验者按 AF-Spec 习惯给意图写 `ha.`/`ha:` 前缀——兜底透传会产出
    `adapter=ha, action=ha.climate.turn_on` 的双前缀坏服务名（编译静默过、运行时才炸）。
    现在三种写法归一到裸服务名，映射名照旧优先。"""
    from autoforge.af_draft import _staging

    def _do_node(action: str):
        r = draft_intent({"name": "t", "when": _when(),
                          "do": {"action": action, "target": "书房空调",
                                 "params": {"hvac_mode": "cool"}}})
        n = [x for x in _staging.get(r["ref"])["graph"].automations[0].nodes.values() if x.kind == "do"][0]
        return n

    for written in ("ha:climate.set_hvac_mode", "ha.climate.set_hvac_mode", "climate.set_hvac_mode"):
        n = _do_node(written)
        assert n.adapter == "ha" and n.action == "climate.set_hvac_mode", f"{written} → {n.adapter}/{n.action}"
    assert _do_node("ha:climate.set_hvac_mode").action == _do_node("climate.set_hvac_mode").action
