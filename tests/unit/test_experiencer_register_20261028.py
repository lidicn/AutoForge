"""体验者缺陷登记册 2026-10-28 的四刀判据（AF-008 / AF-011 / AF-012 / AF-013）。

登记册出处：`E:/NAS/QA/AF_Tester/AF_bug_register.md`（DSH 连接器实测，v1.14.10）。
每条腿都对应一条登记项，改法与判级理由写在源码注释里，这里只钉行为：

- AF-008 🔴：金色路径 `simulate(ir)` 不带 seed/events ⇒ `instances=[]` 却 `ok:true`
  （预演/测试通道由此成了橡皮图章）。现在合成触发；合成过仍零实例 ⇒ `ok:false` +
  `reason=simulate_noop`。⛔ 纯时间/太阳触发不许被误判成空跑（那是判据过头）。
- AF-011 🟡：单自动化"写回自己的触发源"被报 error 级「**跨自动化**成环」并阻断 build。
  现在降 warning、文案如实，且按效果表给出"是否真自激"的判断。
- AF-012 🟡：`wait "PT5S"`（ISO-8601，且 `sun.offset` 的文档写法就是这一族）抛
  ValueError 冒成 ADM_ERR_INTERNAL。现在解析 ISO；空壳/日历型明确拒绝。
- AF-013 🔴：`?占位符` 一路过闸，只在依赖环那站被动露出、报的还是误导性文案。
  现在 `UNRESOLVED_ENTITY`（error）当场拦，测试通道也带回失败理由。
"""

from __future__ import annotations

import json

import pytest

from autoforge.af_apply import _MAX_SYNTH_EVENTS, _synth_triggers, apply
from autoforge.af_draft import draft_intent
from autoforge.af_ir import Graph, load_automation
from autoforge.af_scanner import CHECKS, CODE_HINT, StaticScanner
from autoforge.af_store import GraphStore
from autoforge.af_time import parse_duration

PRE = "__af_pre_state__"


def _state_auto(eid="binary_sensor.d", to="off", action="switch.turn_off", target="switch.pc"):
    return {"ir_version": "0.3.0", "id": "probe", "name": "自读写", "version": 1, "mode": "single",
            "nodes": [
                {"id": "t1", "kind": "on", "name": "触发",
                 "trigger": {"type": "state", "entity_id": eid, "to": to}},
                {"id": "d1", "kind": "do", "name": "动作", "adapter": "ha",
                 "action": action, "params": {"entity_id": target}}],
            "edges": [{"from": "t1", "to": "d1", "kind": "then"}]}


def _codes(auto: dict):
    """返回 (诊断码→级别, 完整 ScanResult)。"""
    res = StaticScanner(Graph([load_automation(auto)])).scan()
    return {d.code: d.level for d in res.diagnostics}, res


# ── AF-012 · ISO-8601 时长 ────────────────────────────────────────────

@pytest.mark.parametrize("text,seconds", [
    ("PT5S", 5.0), ("PT30M", 1800.0), ("P1DT2H", 93600.0), ("PT0.5S", 0.5), ("P2W", 1209600.0),
    ("5s", 5.0), ("10m", 600.0), ("01:30:00", 5400.0), ("90", 90.0),
])
def test_duration_parses_both_families(text, seconds):
    """朴素后缀、HH:MM:SS、ISO-8601 三种写法同源（体验者按文档写 PT5S 不该撞 INTERNAL）。"""
    assert parse_duration(text) == seconds


@pytest.mark.parametrize("bad", ["P", "PT", "P1DT", "P1Y", "P3M", "abc", "-PT30M"])
def test_duration_rejects_with_readable_error(bad):
    """三条 fail-closed 边界都要**具名报错**，⛔ 不许静默当 0 秒。"""
    with pytest.raises(ValueError) as ei:
        parse_duration(bad)
    assert str(bad) in str(ei.value) or bad in str(ei.value)


def test_wait_node_accepts_iso_duration():
    """端到端：IR 里写 `PT5S` 的 wait 节点现在能挂起计时（旧实现整次仿真 INTERNAL 崩）。"""
    from autoforge.af_service import simulate
    ir = {"ir_version": "0.3.0", "id": "w", "name": "等待", "version": 1, "mode": "single",
          "nodes": [
              {"id": "t1", "kind": "on", "name": "触发",
               "trigger": {"type": "state", "entity_id": "binary_sensor.d", "to": "off"}},
              {"id": "w1", "kind": "wait", "name": "等", "duration": "PT5S"},
              {"id": "p1", "kind": "pass", "name": "结束"}],
          "edges": [{"from": "t1", "to": "w1", "kind": "then"}, {"from": "w1", "to": "p1", "kind": "then"}]}
    r = simulate(ir, seed={"binary_sensor.d": "on"},
                 events=[{"entity_id": "binary_sensor.d", "state": "off"}])
    inst = (r.get("instances") or [{}])[0]
    assert inst.get("state") == "suspended" and inst.get("current_node") == "w1"


# ── AF-011 · 同自动化自环不再误判为跨自动化 ──────────────────────────

def test_self_write_back_is_warning_not_cross_automation_error():
    """「开关 X 打开时把它关掉」是合法写法：降 warning、文案如实写"同自动化"。

    自环形状要求**触发源实体 == 被写实体**（switch.pc 触发 → 写 switch.pc），
    上一版腿里我拿 binary_sensor.d 触发去写 switch.pc，那压根不是自环——红得对。
    """
    codes, res = _codes(_state_auto(eid="switch.pc", to="on", target="switch.pc"))
    assert "ENTITY_DEP_CYCLE" not in codes, "同自动化不该再吃跨自动化 error"
    assert codes.get("SELF_TRIGGER_LOOP") == "warning"
    assert res.ok, "误报级别一降，build 不再阻断合法逻辑"


def test_self_loop_message_says_same_not_cross():
    codes, res = _codes(_state_auto(eid="switch.pc", to="on", target="switch.pc"))
    diag = next(d for d in res.diagnostics if d.code == "SELF_TRIGGER_LOOP")
    assert "同自动化" in diag.message and "非跨自动化" in diag.message
    assert "probe → probe" in diag.message


def test_self_loop_detects_oscillation_shape_by_effect_table():
    """触发 to=on + 动作 turn_on（写 on）＝与触发目标一致——要照实说，不含糊。"""
    codes, res = _codes(_state_auto(eid="switch.pc", to="on",
                                    action="switch.turn_on", target="switch.pc"))
    diag = next(d for d in res.diagnostics if d.code == "SELF_TRIGGER_LOOP")
    assert "自激形态" in diag.message or "无法判定" in diag.message


def test_cross_automation_cycle_still_error():
    """对照：真跨自动化互写（A 写 B 的触发源、B 写 A 的触发源）仍是 error 级
    ENTITY_DEP_CYCLE——本次降级只针对同自动化，不许把这条防线一起拆掉。"""
    from autoforge.af_ir import Graph as G
    a = _state_auto(eid="switch.b", to="on", action="switch.turn_on", target="switch.a")
    a["id"] = "a"
    b = _state_auto(eid="switch.a", to="on", action="switch.turn_on", target="switch.b")
    b["id"] = "b"
    res = StaticScanner(G([load_automation(a), load_automation(b)])).scan()
    cyc = [d for d in res.diagnostics if d.code == "ENTITY_DEP_CYCLE"]
    assert cyc and cyc[0].level == "error", "跨自动化互写的保守拦截必须还在"


def test_new_codes_registered_in_both_catalogs():
    for code in ("SELF_TRIGGER_LOOP", "UNRESOLVED_ENTITY", "EXPR_NS_INVALID"):
        assert code in CHECKS and CODE_HINT.get(code, "").strip(), f"{code} 半张脸"


# ── AF-013 · 未解析占位符当场拦 ───────────────────────────────────────

def test_unresolved_placeholder_is_named_error():
    auto = _state_auto(eid="?书房不存在的实体xyz")
    codes, res = _codes(auto)
    assert codes.get("UNRESOLVED_ENTITY") == "error", "占位符必须有具名 error（过去只被动露在环里）"
    assert "ENTITY_DEP_CYCLE" not in codes
    assert not res.ok


def test_unresolved_gate_covers_action_params_and_expr():
    """三处都能抓：触发源、动作 params.entity_id、条件 var（`entity.?名`）。"""
    auto = _state_auto()
    auto["nodes"][1]["params"]["entity_id"] = "?书房空调本体"
    auto["nodes"][0]["trigger"]["entity_id"] = "binary_sensor.ok"
    codes, _ = _codes(auto)
    assert codes.get("UNRESOLVED_ENTITY") == "error"

    auto2 = _state_auto()
    auto2["nodes"].insert(1, {"id": "c1", "kind": "if", "name": "条件",
                              "expr": {"op": "is_on", "value": {"var": "entity.?查不到的名字"}}})
    auto2["edges"] = [{"from": "t1", "to": "c1", "kind": "then"}, {"from": "c1", "to": "d1", "kind": "then"}]
    codes2, _ = _codes(auto2)
    assert codes2.get("UNRESOLVED_ENTITY") == "error"


def test_real_entity_id_not_flagged():
    codes, _ = _codes(_state_auto())
    assert "UNRESOLVED_ENTITY" not in codes, "⛔ 不许误伤正常 entity_id"
    assert "SELF_TRIGGER_LOOP" not in codes, "触发源与被写实体不同名，压根不该出自环码"


# ── AF-008 · 金色路径合成触发，空跑不算通过 ──────────────────────────

def test_synth_from_state_trigger_makes_a_real_transition():
    ir = {"automations": [_state_auto(eid="binary_sensor.d", to="off",
                                      action="climate.turn_on", target="climate.ac")]}
    seed, events, time_based, targets = _synth_triggers(ir)
    assert seed["binary_sensor.d"] == PRE, "先把源置成别的值，否则默认态等于目标态会被去重吞掉"
    assert events == [{"entity_id": "binary_sensor.d", "state": "off"}]
    assert targets == 1 and time_based == []


def test_synth_covers_group_and_nested_triggers():
    ir = {"automations": [{
        "ir_version": "0.3.0", "id": "g", "name": "分组", "version": 1, "mode": "single",
        "nodes": [{"id": "t1", "kind": "on", "name": "触发", "trigger": {
            "type": "group", "op": "or",
            "sources": [{"type": "state", "entity_id": "binary_sensor.a", "to": "on"},
                        {"type": "state", "entity_id": "binary_sensor.b", "to": "off"}]}}],
        "edges": []}]}
    seed, events, _, targets = _synth_triggers(ir)
    assert targets == 2 and {e["entity_id"] for e in events} == {"binary_sensor.a", "binary_sensor.b"}
    assert set(seed.values()) == {PRE}


def test_synth_caps_event_count():
    autos = [_state_auto(eid=f"binary_sensor.x{i}") for i in range(_MAX_SYNTH_EVENTS + 20)]
    _, events, _, targets = _synth_triggers({"automations": autos})
    assert targets <= _MAX_SYNTH_EVENTS and len(events) <= _MAX_SYNTH_EVENTS


def test_time_only_graph_is_not_judged_noop(tmp_path):
    """纯时间触发的图**不许**被判 simulate_noop——合成不了它就属于"这一闸没覆盖"，不是"验不过"。"""
    ir = {"automations": [{
        "ir_version": "0.3.0", "id": "night", "name": "定时", "version": 1, "mode": "single",
        "nodes": [{"id": "t1", "kind": "on", "name": "触发",
                   "trigger": {"type": "time", "at": "22:00:00"}},
                  {"id": "p1", "kind": "pass", "name": "结束"}],
        "edges": [{"from": "t1", "to": "p1", "kind": "then"}]}]}
    _, events, time_based, targets = _synth_triggers(ir)
    assert targets == 0 and time_based == ["night"] and events, "时间触发要给出时钟推进并进名单"


def test_golden_path_spawned_an_instance_and_reports_coverage(tmp_path):
    """端到端（体验者被挡的那条路）：预演真的跑出了实例，回执带 coverage 数字。"""
    store = GraphStore(str(tmp_path / "store"))
    ref = draft_intent({"name": "书房空调", "when": {"entity": "binary_sensor.d", "to": "off"},
                        "do": {"action": "开空调", "target": "climate.ac",
                               "requires_confirm": True, "canary": {"duration": "10m"}}})["ref"]
    r = apply(ref, stage="dry_run", store=store)
    assert r.get("ok") is True, str(r)[:240]
    cov = r.get("sim_coverage") or {}
    assert cov.get("state_triggers") == 1 and cov.get("instances") == cov["instances"] > 0, cov
    assert (r.get("simulate") or {}).get("instances"), "金色路径必须真跑出实例"


def test_noop_is_reported_not_passed(tmp_path, monkeypatch):
    """合成过触发仍零实例 ⇒ ok:false + reason=simulate_noop（橡皮图章的正面对照）。"""
    import autoforge.af_apply as af_apply
    store = GraphStore(str(tmp_path / "store"))
    ref = draft_intent({"name": "空调2", "when": {"entity": "binary_sensor.d", "to": "off"},
                        "do": {"action": "开空调", "target": "climate.ac",
                               "requires_confirm": True, "canary": {"duration": "10m"}}})["ref"]
    monkeypatch.setattr(af_apply.af_service, "simulate",
                        lambda ir, seed=None, events=None, store=None: {"ok": True, "instances": []})
    r = apply(ref, stage="dry_run", store=store)
    assert r.get("ok") is False and r.get("reason") == "simulate_noop", str(r)[:200]
    assert "实例" in str(r.get("error"))


def test_staging_ir_shape_reaches_synthesis(tmp_path):
    """staging 里的 IR 形状（graph_to_raw）与手抄形状一致：合成器读的 nodes/trigger 真有值。"""
    store = GraphStore(str(tmp_path / "store"))
    ref = draft_intent({"name": "空调3", "when": {"entity": "binary_sensor.d", "to": "off"},
                        "do": {"action": "关灯", "target": "light.x"}})["ref"]
    from autoforge.af_draft import get_staged
    from autoforge.af_spec import graph_to_raw
    payload = {"automations": graph_to_raw(get_staged(ref)["graph"])}
    _, _, _, targets = _synth_triggers(payload)
    assert targets >= 1, f"起草出的图合成不到触发：{json.dumps(payload)[:200]}"


# ── AF-009 · 意图里 `entity` 与 `var` 两套字段名的收口 ────────────────

def test_unary_accepts_entity_alias_and_compiles_identical_ir():
    """`{"is_on": {"entity": ...}}` 与 `{"is_on": {"var": ...}}` 必须编译出同一份 IR。

    体验者 AF-009：`when`/`do` 用 `entity`、一元谓词只认 `var`，同一份意图两套字段名。
    别名只活在起草入口——⛔ IR 词汇表不扩（编译器仍只见 `value={"var": ...}`）。
    """
    from autoforge.af_draft import draft_intent, get_staged

    refs = []
    for key in ("var", "entity"):
        ref = draft_intent({
            "name": f"别名-{key}",
            "when": {"entity": "binary_sensor.d", "to": "off"},
            "if": {"is_on": {key: "switch.pc"}},
            "do": {"action": "开灯", "target": "light.x"},
        })["ref"]
        refs.append(ref)
    irs = [get_staged(r)["graph"].automations[0].raw for r in refs]
    exprs = [next(n for n in a["nodes"] if n["kind"] == "if")["expr"] for a in irs]
    assert exprs[0] == exprs[1], f"两种写法编出了不同 IR：{exprs}"
    assert exprs[0]["value"]["var"].startswith("entity."), "IR 侧仍是规范的 entity. 前缀形态"
    assert "entity" not in exprs[0]["value"], "别名不许渗进 IR"


def test_unary_operand_still_rejects_shapeless_form():
    """别名不放宽形状：既无 var 又无 entity 时照旧 E_INVALID_EXPR，且文案提到两种写法。"""
    from autoforge.af_draft import DraftError, draft_intent
    with pytest.raises(DraftError) as ei:
        draft_intent({"name": "坏", "when": {"entity": "binary_sensor.d", "to": "off"},
                      "if": {"is_on": {"value": "switch.pc"}},
                      "do": {"action": "开灯", "target": "light.x"}})
    assert ei.value.code == "E_INVALID_EXPR"
    assert "entity" in str(ei.value), "报错要告知两种可接受写法"
