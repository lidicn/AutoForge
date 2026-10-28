"""MCP 边界的 JSON 参数兜底（体验者 2026-10-28 现场归因）。

体验者用对照实验把病灶钉死：DSH 桥接层把「可选+嵌套复杂」参数（object/含 object
的 array）json.dumps 成字符串转发——必填复杂参数（ir）与纯字符串数组
（known_entities/tags）都原生。seed/events 恰好是前者 ⇒ AF 端 dict() 炸成
ADM_ERR_INTERNAL。修法选 AF 侧兜底（体验者列的二选一：DSH 是第三方桥接层，
AF 管不了别人发什么，但能把"发错形状"变容错/可读错误）。

钉四条：字符串化解一层 / 原生不回归 / 坏字符串可读错误（⛔ 不 INTERNAL 崩）/
af_simulate 端到端（DSH 形状能跑完整图）。
"""

from __future__ import annotations

import json

import pytest

from autoforge.af_mcp import _coerce_json_arg

from homesdk.adm.errors import ADM_ERR_PAYLOAD_INVALID
from autoforge.af_store import GraphStore

_IR = {
    "ir_version": "0.2.1", "id": "sim_repro", "name": "repro", "version": 1, "mode": "single",
    "nodes": [
        {"id": "t1", "kind": "on", "name": "t",
         "trigger": {"type": "state", "entity_id": "binary_sensor.d", "to": "off"}},
        {"id": "d1", "kind": "do", "name": "d", "adapter": "ha",
         "action": "climate.turn_on", "params": {"entity_id": "climate.a"}},
    ],
    "edges": [{"from": "t1", "to": "d1", "kind": "then"}],
}


def test_coerce_parses_dsh_stringified_shapes():
    """DSH 现场形状：seed/events 被 json.dumps 成字符串——解一层后照常工作。"""
    seed = _coerce_json_arg(json.dumps({"binary_sensor.d": "on"}), "seed")
    events = _coerce_json_arg(json.dumps([{"entity_id": "binary_sensor.d", "state": "off"}]), "events")
    assert seed == {"binary_sensor.d": "on"}
    assert events == [{"entity_id": "binary_sensor.d", "state": "off"}]


def test_coerce_passes_native_shapes_through():
    assert _coerce_json_arg({"a": "b"}, "seed") == {"a": "b"}
    assert _coerce_json_arg([{"x": 1}], "events") == [{"x": 1}]
    assert _coerce_json_arg(None, "seed") is None


def test_coerce_bad_string_is_readable_error():
    """真坏字符串：可读 ValueError 指明桥接层问题——⛔ 不是裸 dict() 的 INTERNAL 崩。"""
    with pytest.raises(ValueError) as ei:
        _coerce_json_arg("这不是json", "seed")
    assert "桥接层" in str(ei.value) and "seed" in str(ei.value)


# ── 真实边界：兜底收在 dispatch（按 inputSchema 归一），覆盖**所有**工具，
#    所以判据必须打 dispatch 而不是直调 _t_simulate（直调不是生产路径）。

def _dispatch(name, args):
    import tempfile

    from autoforge.af_mcp import dispatch
    with tempfile.TemporaryDirectory() as td:
        content, is_err = dispatch(name, args, GraphStore(td),
                                   {"subject": "owner", "scopes": ["read", "write", "live", "admin"]})
    return json.loads(content[0]["text"]), is_err


def test_simulate_end_to_end_with_dsh_stringified_shapes():
    """端到端：DSH 字符串化形状跑完整图（体验者被挡住的那一步）。"""
    inner, is_err = _dispatch("af_simulate", {
        "ir": _IR,
        "seed": json.dumps({"binary_sensor.d": "on"}),
        "events": json.dumps([{"entity_id": "binary_sensor.d", "state": "off"}]),
    })
    assert is_err is False and inner.get("ok") is True, str(inner)[:200]
    # 效果真值表：climate.turn_on 在空调上的落位是 cool（hvac_mode 默认档）
    assert (inner.get("final_states") or {}).get("climate.a") == "cool"


def test_stringified_ir_is_the_actual_tester_crash():
    """体验者那句 `dictionary update sequence element #0 has length 1` 的真因：
    被字符串化的是 **ir**（`_load_ir` 的 dict(字符串)），不是 seed/events——
    形状相关性是假象，报错形状随崩点而异。现在必须可读拒收或解好。"""
    inner, is_err = _dispatch("af_simulate", {
        "ir": json.dumps(_IR),
        "seed": json.dumps({"binary_sensor.d": "on"}),
        "events": json.dumps([{"entity_id": "binary_sensor.d", "state": "off"}]),
    })
    assert is_err is False, str(inner)[:200]
    assert inner.get("ok") is True


def test_bad_json_string_reports_payload_invalid_not_internal():
    inner, is_err = _dispatch("af_simulate", {"ir": "{坏 json"})
    assert is_err is True
    assert inner.get("code") == ADM_ERR_PAYLOAD_INVALID, "必须是可读拒收，不是 ADM_ERR_INTERNAL"
    assert "桥接层" in str(inner.get("message"))


def test_string_params_not_touched_by_normalization():
    """⛔ 不误伤：schema 声明 string 的参数（af_guide.section）值即使是合法 JSON 文本
    也不许被解成别的类型——归一只对 object/array 声明生效。"""
    inner, is_err = _dispatch("af_guide", {"section": "7"})
    assert is_err is False
    assert inner.get("ok") is True
