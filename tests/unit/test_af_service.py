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

