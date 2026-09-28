"""F9/v2.3 group 容器节点（决策 D 裁定方案 B）：IR 基础能力。

覆盖 DCD 裁定验收 §7.1/§7.4/§5：
- group 节点可加载、含 children
- ir_version 含 "0.3.0"，旧 0.2.1 仍可共存读
- group 可渲染回 NL（覆盖率检查通过）
- additionalProperties 前向兼容（未知顶层字段不拒）
"""

from autoforge.af_ir import (
    NODE_KINDS,
    is_supported_ir_version,
    load_automation,
    validate_automation,
)
from autoforge.af_ir.models import IRValidationError
from autoforge.af_nl import render_automation

GOOD_NIGHT = {
    "ir_version": "0.3.0",
    "id": "good_night",
    "name": "晚安模式",
    "version": 1,
    "mode": "single",
    "nodes": [
        {
            "id": "g1",
            "kind": "group",
            "name": "晚安组合",
            "children": [
                {
                    "id": "off_lights", "name": "关所有灯", "version": 1, "mode": "single",
                    "nodes": [
                        {"id": "on1", "kind": "on", "trigger": {"type": "state", "entity_id": "input_boolean.good_night", "to": "on"}},
                        {"id": "d1", "kind": "do", "adapter": "ha", "action": "light.turn_off", "params": {"entity_id": "light.all"}},
                    ],
                    "edges": [{"from": "on1", "to": "d1", "kind": "then"}],
                },
                {
                    "id": "lock_door", "name": "锁前门", "version": 1, "mode": "single",
                    "nodes": [
                        {"id": "on2", "kind": "on", "trigger": {"type": "state", "entity_id": "input_boolean.good_night", "to": "on"}},
                        {"id": "d2", "kind": "do", "adapter": "ha", "action": "lock.lock", "params": {"entity_id": "lock.front_door"}},
                    ],
                    "edges": [{"from": "on2", "to": "d2", "kind": "then"}],
                },
                {
                    "id": "ac_off", "name": "关空调", "version": 1, "mode": "single",
                    "nodes": [
                        {"id": "on3", "kind": "on", "trigger": {"type": "state", "entity_id": "input_boolean.good_night", "to": "on"}},
                        {"id": "d3", "kind": "do", "adapter": "ha", "action": "climate.turn_off", "params": {"entity_id": "climate.bedroom"}},
                    ],
                    "edges": [{"from": "on3", "to": "d3", "kind": "then"}],
                },
            ],
        }
    ],
    "edges": [],
}


def test_group_kind_in_enum():
    assert "group" in NODE_KINDS


def test_ir_version_0_3_0_supported_and_old_coexists():
    assert is_supported_ir_version("0.3.0")
    assert is_supported_ir_version("0.2.1")
    assert not is_supported_ir_version("0.4.0")


def test_group_ir_validates_and_loads():
    validate_automation(GOOD_NIGHT)  # 不抛
    auto = load_automation(GOOD_NIGHT)
    gnode = auto.nodes["g1"]
    assert gnode.kind == "group"
    assert len(gnode.children) == 3
    assert {c.id for c in gnode.children} == {"off_lights", "lock_door", "ac_off"}


def test_group_renders_to_nl_with_full_coverage():
    auto = load_automation(GOOD_NIGHT)
    res = render_automation(auto)
    assert res.ok, f"NL 覆盖率失败：{res.warnings}"
    assert "晚安组合" in res.text
    assert "关所有灯" in res.text and "锁前门" in res.text and "关空调" in res.text


def test_legacy_0_2_1_still_valid():
    legacy = {
        "ir_version": "0.2.1",
        "id": "legacy1", "name": " legacy", "version": 1, "mode": "single",
        "nodes": [
            {"id": "on1", "kind": "on", "trigger": {"type": "state", "entity_id": "light.x", "to": "on"}},
            {"id": "d1", "kind": "do", "adapter": "ha", "action": "light.turn_on", "params": {"entity_id": "light.x"}},
        ],
        "edges": [{"from": "on1", "to": "d1", "kind": "then"}],
    }
    validate_automation(legacy)  # 旧图仍可校验


def test_additional_properties_forward_compat():
    # 顶层未知字段（未来扩展）不应被硬拒（前向兼容策略）
    future = dict(GOOD_NIGHT)
    future["x_future_ext"] = {"note": "前向兼容字段"}
    validate_automation(future)  # 不抛


def test_unknown_ir_version_rejected_by_enum():
    bad = dict(GOOD_NIGHT)
    bad["ir_version"] = "0.4.0"
    try:
        validate_automation(bad)
    except IRValidationError:
        pass
    else:
        raise AssertionError("ir_version 0.4.0 应被 enum 拒")
