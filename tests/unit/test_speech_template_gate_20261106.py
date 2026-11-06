# -*- coding: utf-8 -*-
"""判据 D11 · 播报文案模板化（2026-11-06）· 卡名 AF卡_speech_template。

来源：AF-PM 派单卡 `plans/AF卡_v127_D10D11_派单卡_20261105.md` §二；
出资人原话「当前书房温度是 {实时温度} 度，已经帮你设置空调为制冷模式26摄氏度」。

**契约**：
- IR 字段 `speech_template`（do/emit 节点级，模板字符串）＋ `speech_entity`（读数实体源，
  缺省用节点目标实体）；DSL 关键字同名，`_NODE_FIELDS["do"]` 白名单已开。
- 占位符白名单 = `SPEECH_TEMPLATE_PLACEHOLDERS`（初始 `{temperature}`），这是 UI 编辑器校验的唯一真源。
- 语义：触发时读传感器实时值填入；**实体不可读 ⇒ 回退静态文案**。
- fail-closed 三禁：⛔ 不假装读数、⛔ 不播占位符原文、⛔ 不播「unknown」。

覆盖（编号带卡名）：
  D1 正例：可读传感器 ⇒ 播报文本含实时读数（do 腿）
  D2 反例：实体不可读 ⇒ 回退 announce 静态文案，正文**无占位符残留**、无 unknown
  D3 反例：白名单外占位符 ⇒ 回退静态（⛔ 不播占位符原文）
  D4 DSL：`speech_template`/`speech_entity` 能被 spec 编译并 render 回去；未登记字段仍被拒（有牙）
  D5 emit 腿同源：emit 节点的模板同样生效（三腿同一闸的既有边界不动）
  D6 变异：改模板文本 ⇒ 播报随之改变（证判据钳住真实模板，不是恒值）
  D7 源码钉：渲染走单一 `render_speech_template`＋白名单常量
"""

from __future__ import annotations

import pytest

from autoforge.af_bus import BusEvent
from autoforge.af_executor import SPEECH_TEMPLATE_PLACEHOLDERS, render_speech_template
from autoforge.af_ir import Graph, load_automation
from autoforge.af_runtime import build_runtime
from autoforge.af_spec import SpecError, compile_spec, render_spec

TEMPLATE = "当前书房温度是 {temperature} 度，已经帮你设置空调为制冷模式26摄氏度"
STATIC = "书房有点热，我把空调打开"
SENSOR = "sensor.duke_th2"


def _auto(template: str | None = TEMPLATE, entity: str | None = SENSOR, **node_extra) -> dict:
    d1 = {
        "id": "d1", "kind": "do", "name": "开空调", "adapter": "climate",
        "action": "set_hvac_mode",
        "params": {"entity_id": "climate.x", "hvac_mode": "cool"},
        "room": "书房",
    }
    if template is not None:
        d1["speech_template"] = template
    if entity is not None:
        d1["speech_entity"] = entity
    d1.update(node_extra)
    return {
        "ir_version": "0.2.1", "id": "d11", "name": "播报模板用例", "version": 1,
        "mode": "restart",
        "nodes": [
            {"id": "a1", "kind": "on", "name": "人",
             "trigger": {"type": "state", "entity_id": "binary_sensor.motion", "to": "on"}},
            d1,
            {"id": "p1", "kind": "pass", "name": "结束"},
        ],
        "edges": [
            {"from": "a1", "to": "d1", "kind": "then"},
            {"from": "d1", "to": "p1", "kind": "then"},
        ],
    }


class _FakeAdapter:
    name = "climate"
    dry_run = False

    def __init__(self):
        self.calls = []

    def call(self, action, params):
        self.calls.append((action, dict(params)))
        from autoforge.af_adapters.base import CallResult
        return CallResult(success=True, data={}, error=None)


def _runtime(auto, states=None):
    rt = build_runtime(Graph([load_automation(auto)]))
    rt.adapters.register(_FakeAdapter())
    for k, v in (states or {}).items():
        rt.states.set_state(k, v)
    return rt


def _fire(rt):
    rt.states.set_state("binary_sensor.motion", "off")
    rt.publish(BusEvent.of("binary_sensor.motion", "on"))


def _capture(rt) -> list[str]:
    speaks: list[str] = []
    rt.executor.on_do_speak = lambda tid, text, room, entity_id="": speaks.append(text)
    return speaks


# ── D1 正例：实时读数进文案 ────────────────────────────────────────────
def test_d1_template_fills_live_reading():
    rt = _runtime(_auto(), {SENSOR: "28.6"})
    speaks = _capture(rt)
    _fire(rt)
    assert len(speaks) == 1, f"应播报一次，实得 {speaks}"
    assert "28.6" in speaks[0], f"播报必须含实时读数 28.6，实得 {speaks[0]!r}"
    assert "{temperature}" not in speaks[0], "⛔ 不许把占位符原文播出去"
    assert "unknown" not in speaks[0].lower()


# ── D2 反例：实体不可读 ⇒ 回退静态文案 ────────────────────────────────
def test_d2_unreadable_entity_falls_back_to_static():
    # 节点带 announce 静态文案；传感器没注册 ⇒ 读不到 ⇒ 必须回退 announce
    rt = _runtime(_auto(announce=STATIC))  # 不设 sensor 状态
    speaks = _capture(rt)
    _fire(rt)
    assert speaks == [STATIC], f"实体不可读必须回退静态文案，实得 {speaks}"
    assert "{temperature}" not in speaks[0]
    assert "unknown" not in speaks[0].lower()


# ── D3 反例：白名单外占位符 ⇒ 回退静态（不播占位符原文）──────────────────
def test_d3_unknown_placeholder_falls_back():
    rt = _runtime(_auto(template="湿度 {humidity} 度", announce=STATIC), {SENSOR: "28.6"})
    speaks = _capture(rt)
    _fire(rt)
    assert speaks == [STATIC], f"白名单外占位符必须回退静态，实得 {speaks}"
    assert "{humidity}" not in speaks[0]


# ── D3b v1.27.1 收口：渲不出且无 announce ⇒ 静默（不机拼、不播占位符）──────
def test_d3b_render_fail_without_announce_is_silent():
    """PM 卡 §修法 2：模板渲染失败且无 announce ⇒ **静默**（fail-closed）。

    反证腿：v1.27.0 上这一格会机拼「自动化名：节点名」出声；实现若回退旧兜底，本腿红。
    """
    rt = _runtime(_auto())  # 有模板、无 announce、传感器没注册 ⇒ 渲染失败
    speaks = _capture(rt)
    _fire(rt)
    assert speaks == [], f"渲不出且无 announce 必须静默，实得 {speaks}"


# ── D3c 卡 §修法 5：读数渲染口径（整数不拖 .0，非整数原样）────────────────
def test_d3c_integral_reading_drops_trailing_dot_zero():
    rt = _runtime(_auto(), {SENSOR: "28.0"})
    speaks = _capture(rt)
    _fire(rt)
    assert speaks and "28 度" in speaks[0], f"整数读数应念「28 度」，实得 {speaks[0]!r}"
    assert "28.0" not in speaks[0], "⛔ 不拖 .0（出资人听到的是「28 度」）"
    rt2 = _runtime(_auto(), {SENSOR: "28.6"})
    speaks2 = _capture(rt2)
    _fire(rt2)
    assert speaks2 and "28.6" in speaks2[0], f"非整数读数照实念，实得 {speaks2[0]!r}"


# ── D4 DSL：编译 + render 往返；未登记字段仍被拒 ────────────────────────
def test_d4_dsl_accepts_and_roundtrips():
    spec = f"""
    automation dsl_speech
    name "DSL 模板用例"
    ir_version "0.2.1"
    version 1
    mode parallel

    on a1 {{"type": "state", "entity_id": "binary_sensor.pc", "to": "on"}}
    do d1 ha.climate.set_hvac_mode {{"entity_id": "climate.x", "hvac_mode": "cool"}} speech_template "{TEMPLATE}" speech_entity "{SENSOR}"
    pass p1
    edge a1 -> d1 then
    edge d1 -> p1 then
    """
    graph = compile_spec(spec)
    node = graph.get("dsl_speech").nodes["d1"]
    assert node.raw.get("speech_template") == TEMPLATE, "DSL 必须把 speech_template 编进 raw"
    assert node.raw.get("speech_entity") == SENSOR
    out = render_spec(graph)
    assert "speech_template" in out and "speech_entity" in out, "render 必须把两个字段写回去（round-trip）"
    again = compile_spec(out).get("dsl_speech").nodes["d1"]
    assert again.raw.get("speech_template") == TEMPLATE, "round-trip 后模板文本必须一致"


def test_d4b_unregistered_field_still_rejected():
    """反证牙：白名单不是「什么都放行」——没登记的关键字照旧被拒。"""
    spec = """
    automation bad_speech
    name "坏字段用例"
    ir_version "0.2.1"
    version 1
    mode parallel
    on a1 {"type": "state", "entity_id": "binary_sensor.pc", "to": "on"}
    do d1 ha.climate.set_hvac_mode {"entity_id": "climate.x"} speech_volume "loud"
    pass p1
    edge a1 -> d1 then
    edge d1 -> p1 then
    """
    with pytest.raises(SpecError):
        compile_spec(spec)


# ── D5 emit 腿同源 ───────────────────────────────────────────────────
def test_d5_emit_leg_uses_template():
    auto = {
        "ir_version": "0.2.1", "id": "emit_speech", "name": "emit 模板", "version": 1,
        "mode": "parallel",
        "nodes": [
            {"id": "a1", "kind": "on",
             "trigger": {"type": "state", "entity_id": "binary_sensor.motion", "to": "on"}},
            {"id": "e1", "kind": "pass", "name": "广播",
             "emit": {"event": "study_ac_started", "data": {"room": "书房", "entity_id": SENSOR}},
             "speech_template": TEMPLATE, "speech_entity": SENSOR},
        ],
        "edges": [{"from": "a1", "to": "e1", "kind": "then"}],
    }
    rt = _runtime(auto, {SENSOR: "27.3"})
    speaks: list[str] = []
    rt.executor.on_emit_speak = lambda tid, text, room, entity_id="": speaks.append(text)
    _fire(rt)
    assert speaks, "emit 腿必须播报"
    assert "27.3" in speaks[0], f"emit 腿也要填实时读数，实得 {speaks[0]!r}"


# ── D6 变异：改模板 ⇒ 播报随变 ────────────────────────────────────────
@pytest.mark.parametrize("reading", ["28.6", "25.1"])
def test_d6_template_change_changes_text(reading):
    rt = _runtime(_auto(), {SENSOR: reading})
    speaks = _capture(rt)
    _fire(rt)
    assert speaks and reading in speaks[0], f"读数 {reading} 必须出现在播报里（变异：输入变则输出变）"


# ── D7 源码钉：单一渲染实现＋白名单真源 ────────────────────────────────
def test_d7_source_pins():
    import inspect

    from autoforge.af_executor import NodeExecutor

    assert SPEECH_TEMPLATE_PLACEHOLDERS == frozenset({"temperature"}), "白名单初始只有 temperature"
    assert render_speech_template("温度 {temperature}", {"temperature": "1"}) == "温度 1"
    assert render_speech_template("温度 {temperature}", {}) is None, "缺值必须 None（回退）"
    src = inspect.getsource(NodeExecutor)
    assert "_speech_template_text" in src, "渲染必须收敛到一个方法"
    assert "_speech_template_text" in inspect.getsource(NodeExecutor._speak_for_do)
    assert "_speech_template_text" in inspect.getsource(NodeExecutor._speak_for_emit)
