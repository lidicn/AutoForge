"""v2 M4 诚实报告分层单测。"""

from __future__ import annotations

from autoforge.af_service import honest_report, simulate


def test_honest_report_three_tiers():
    out = {
        "expect": {
            "ok": False,
            "failed": 1,
            "items": [
                {"index": 0, "target": "light.a", "status": "pass"},
                {"index": 1, "target": "light.b", "status": "fail", "reason": "x"},
                {"index": 2, "target": "light.c", "status": "unverified", "reason": "no state"},
            ],
        },
        "final_states": {"light.a": "on", "light.b": "off", "light.c": "on", "light.d": "on"},
    }
    rep = honest_report(out)
    assert len(rep["verified"]) == 2  # pass + fail 都算"跑过的断言"
    assert len(rep["non_simulable"]) == 1
    assert rep["non_simulable"][0]["flag"] == "yellow"  # 不可仿真显式标黄
    # light.d 未被任何 expect target 覆盖 → inferred
    assert any(i["entity"] == "light.d" for i in rep["inferred"])
    assert rep["fully_verified"] is False  # 存在 unverified


def test_honest_report_no_unverified_fully_verified():
    out = {
        "expect": {"ok": True, "failed": 0, "items": [{"target": "light.a", "status": "pass"}]},
        "final_states": {"light.a": "on"},
    }
    rep = honest_report(out)
    assert rep["fully_verified"] is True
    assert rep["non_simulable"] == []
    assert rep["inferred"] == []  # light.a 已被断言覆盖


def test_honest_report_no_do_python_escape():
    # 内核零 LLM 红线：报告结构里不得出现 do:python 逃生舱
    out = {"expect": {"items": []}, "final_states": {}}
    rep = honest_report(out)
    assert "do:python" not in str(rep)


def test_honest_report_unmodeled_actions_flagged():
    # 仿真底座未建模动作 → 同样归入 non_simulable 并标黄（不可仿真显式降级）
    out = {
        "expect": {
            "failed": 0,
            "items": [{"target": "light.a", "status": "pass"}],
            "unmodeled_actions": ["service.unknown_action"],
        },
        "final_states": {"light.a": "on"},
    }
    rep = honest_report(out)
    acts = [x for x in rep["non_simulable"] if x.get("action") == "service.unknown_action"]
    assert acts and acts[0]["flag"] == "yellow"
    # 存在不可仿真项 → 不应判 fully_verified
    assert rep["fully_verified"] is False


def test_honest_report_tiers_are_lists():
    # 验收门：三栏恒为可枚举 list，即使为空也不伪造占位
    out = {"expect": {"items": []}, "final_states": {}}
    rep = honest_report(out)
    assert isinstance(rep["verified"], list)
    assert isinstance(rep["inferred"], list)
    assert isinstance(rep["non_simulable"], list)


def test_honest_report_exempted_partition():
    # 决策 B 真豁免通道：副作用不可观测的自动化单列 exempted 档，
    # 不冒充 verified，也不混入 non_simulable（那是「想验但验不到」）。
    out = {
        "expect": {"ok": True, "failed": 0, "items": [{"target": "light.a", "status": "pass"}]},
        "final_states": {"light.a": "on"},
        "exempted": [
            {"automation_id": "a2", "action": "notify", "reason": "副作用不可观测，转人审"},
        ],
    }
    rep = honest_report(out)
    assert isinstance(rep["exempted"], list)
    assert len(rep["exempted"]) == 1
    assert rep["exempted"][0]["automation_id"] == "a2"
    # exempted 不影响 fully_verified（无 expect 的豁免自动化不拉低验证率）
    assert rep["fully_verified"] is True
    # 没喂入时分区为空 list，不伪造占位
    assert honest_report({"expect": {"items": []}, "final_states": {}})["exempted"] == []


def test_honest_report_verified_in_prod_partition():
    # v2.1 F4：生产态真实验证证据（af_watch 聚合）单列 verified_in_prod 分区
    out = {
        "expect": {"ok": True, "failed": 0, "items": [{"target": "light.a", "status": "pass"}]},
        "final_states": {"light.a": "on"},
        "verified_in_prod": [
            {"automation_id": "a1", "verified_in_prod": 3, "last_verified_at": 100.0, "shadow": 2, "canary": 1, "conflict": 0},
        ],
    }
    rep = honest_report(out)
    assert isinstance(rep["verified_in_prod"], list)
    assert rep["verified_in_prod"][0]["automation_id"] == "a1"
    assert rep["verified_in_prod"][0]["verified_in_prod"] == 3
    # 没喂入时回退为 af_watch 默认分区（dict，含 automations，不伪造占位）
    fallback = honest_report({"expect": {"items": []}, "final_states": {}})["verified_in_prod"]
    assert isinstance(fallback, dict) and "automations" in fallback


def test_simulate_report_has_three_tiers():
    # 验收门：sim 端点返回结构含 verified / inferred / non_simulable 三分区且可枚举
    import json
    import os

    here = os.path.dirname(__file__)
    p = os.path.join(here, "..", "..", "examples", "ir", "case11_expect.json")
    with open(p, encoding="utf-8") as f:
        ir = json.load(f)
    out = simulate(ir)
    assert "report" in out
    rep = out["report"]
    assert isinstance(rep, dict)
    assert isinstance(rep["verified"], list)
    assert isinstance(rep["inferred"], list)
    assert isinstance(rep["non_simulable"], list)
    # 向后兼容：旧字段仍在
    assert "expect" in out and "final_states" in out


# ---- PR 1.6：契约冻结（build/simulate 返回带 stage schema 标记） ----


def _load_example_ir(name: str):
    import json
    import os

    here = os.path.dirname(__file__)
    p = os.path.join(here, "..", "..", "examples", "ir", name)
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def test_build_returns_stage_schema_marker():
    # 决策 C 契约冻结：build 返回须带 "schema": "af-stage/1"
    from autoforge.af_service import STAGE_SCHEMA, build
    ir = _load_example_ir("case11_expect.json")
    out = build(ir)
    assert out.get("schema") == STAGE_SCHEMA


def test_simulate_returns_stage_schema_marker():
    from autoforge.af_service import STAGE_SCHEMA, simulate
    ir = _load_example_ir("case11_expect.json")
    out = simulate(ir)
    assert out.get("schema") == STAGE_SCHEMA


# ---- PR 1.1：补域 P1（scene/script 间接触发展开 + notify 标副作用不可观测） ----


def _make_ir(action, *, effects=None, expect=None, seed=None):
    do = {"id": "do", "kind": "do", "adapter": "ha", "action": action, "params": {}}
    if effects is not None:
        do["effects"] = effects
    return {
        "ir_version": "0.2.1",
        "id": "a1",
        "name": "t",
        "version": 1,
        "mode": "restart",
        "nodes": [
            {"id": "on", "kind": "on", "trigger": {"type": "state", "entity_id": "light.a", "to": "on"}},
            do,
        ],
        "edges": [{"from": "on", "to": "do", "kind": "then"}],
        "expect": expect or [],
    }


def test_notify_action_routed_to_exempted_not_non_simulable():
    # 决策 A：notify 副作用不可观测 → 真豁免（exempted 档），不冒充 verified、不进 non_simulable
    from autoforge.af_service import simulate
    ir = _make_ir("notify.send", expect=[{"entity_id": "light.a", "state": "on"}])
    out = simulate(ir, seed={"light.a": "off"})  # 未验证（unverified）但属 notify → 豁免
    rep = out["report"]
    # 真豁免清单含该自动化
    assert any(e.get("automation_id") == "a1" and e.get("action") == "notify.send"
               for e in out.get("exempted", []))
    assert any(e.get("automation_id") == "a1" for e in rep["exempted"])
    # 不计入 non_simulable（unverified 的 notify expect 被路由走）
    assert rep["non_simulable"] == []


def test_scene_effects_expanded_and_verified():
    # 决策 A：scene/script 间接触发展开 → 声明 effects 进入仿真状态 → expect 可验证
    from autoforge.af_service import simulate
    ir = _make_ir(
        "scene.activate",
        effects=[{"entity_id": "light.b", "state": "on"}],
        expect=[{"entity_id": "light.b", "state": "on"}],
    )
    out = simulate(ir, seed={"light.b": "off"})  # 声明展开前为 off
    rep = out["report"]
    # effects 展开后 light.b == on → expect 通过（verified，离开 non_simulable）
    assert any(it.get("target") == "light.b" and it.get("status") == "pass"
               for it in rep["verified"])
    assert rep["non_simulable"] == []


def test_scene_without_declared_effects_is_verifiable_not_exempt():
    # 反向：scene 未声明 effects → 仿真按默认状态判定（此处 fail），诚实可验证；
    # 且 scene 走「展开后验证」而非豁免（exempted 为空）。
    from autoforge.af_service import simulate
    ir = _make_ir("scene.activate", expect=[{"entity_id": "light.b", "state": "on"}])
    out = simulate(ir)  # light.b 默认 off → expect 判 fail
    rep = out["report"]
    assert any(it.get("target") == "light.b" and it.get("status") == "fail"
               for it in rep["verified"])
    assert rep["exempted"] == []  # scene 不豁免（仅 notify/tts 等副作用不可观测动作豁免）

