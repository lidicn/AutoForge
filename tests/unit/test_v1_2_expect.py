"""v1.2.0 断言闭环（IR `expect` + 诊断可行动）单测。

覆盖：
1. Schema 接受实体形态 / 变量形态，拒绝非法形态；
2. `Automation.expects()` / `expect_entities()`；
3. `evaluate_expects` 三态：pass / fail / unverified（**未验到 ≠ 通过**）；
4. 变量形态：走实例 `vars`（含 `result_var` 的嵌套路径）；
5. `fully_verified` 比 `ok` 更严（有 unverified 时不算完全验证）；
6. 扫描器：`EXPECT_MISSING`（warning）/ `EXPECT_UNREACHABLE`（error）；
7. `Diagnostic.hint` 自动补 + 进入 `af_build` 输出；
8. `af_simulate` 端到端：断言通过 / 断言失败；
9. NL 渲染写进「预期」段；
10. AF-Spec 往返保 `expect`；
11. FakeHA 未建模动作留痕（`unmodeled`）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoforge import af_service as svc
from autoforge.af_expect import MISSING, evaluate_expects, evaluate_graph_expects, resolve_var
from autoforge.af_ir import IRValidationError, load_graph
from autoforge.af_nl import render_automation
from autoforge.af_scanner import CODE_HINT, StaticScanner
from autoforge.af_spec import compile_spec, render_spec
from autoforge.af_vhass import FakeHAAdapter, seed_from_graph

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "ir"


def _raw(**overrides) -> dict:
    base = {
        "ir_version": "0.2.1",
        "id": "demo",
        "name": "demo",
        "version": 1,
        "mode": "single",
        "nodes": [
            {
                "id": "o",
                "kind": "on",
                "trigger": {"type": "state", "entity_id": "binary_sensor.study_motion", "to": "on"},
            },
            {
                "id": "d",
                "kind": "do",
                "adapter": "ha",
                "action": "light.turn_on",
                "params": {"entity_id": "light.study_main"},
                "result_var": "r",
            },
            {"id": "p", "kind": "pass"},
        ],
        "edges": [
            {"from": "o", "to": "d", "kind": "then"},
            {"from": "d", "to": "p", "kind": "then"},
        ],
    }
    base.update(overrides)
    return base


# ─────────────────────────────────────────────────────────────────────
# 1. Schema
# ─────────────────────────────────────────────────────────────────────


def test_schema_accepts_entity_and_var_forms():
    graph = load_graph(
        _raw(
            expect=[
                {"entity_id": "light.study_main", "state": "on"},
                {"entity_id": "light.study_main", "state": ["on", "off"]},
                {"var": "r.success", "op": "eq", "value": True, "note": "下发应成功"},
            ]
        )
    )
    assert len(graph.get("demo").expects()) == 3


def test_schema_rejects_bad_expect():
    with pytest.raises(IRValidationError):
        load_graph(_raw(expect=[{"entity_id": "light.study_main"}]))  # 缺 state
    with pytest.raises(IRValidationError):
        load_graph(_raw(expect=[{"var": "r.success"}]))  # 缺 value
    with pytest.raises(IRValidationError):
        load_graph(_raw(expect=[{"entity_id": "light.x", "state": "on", "bogus": 1}]))


# ─────────────────────────────────────────────────────────────────────
# 2/3/4/5. 求值
# ─────────────────────────────────────────────────────────────────────


class _States:
    def __init__(self, values: dict[str, str | None]):
        self.values = values

    def get(self, entity_id: str):
        return self.values.get(entity_id)


def test_evaluate_three_states():
    auto = load_graph(
        _raw(
            expect=[
                {"entity_id": "light.a", "state": "on"},
                {"entity_id": "light.b", "state": "off"},
                {"entity_id": "light.c", "state": "on"},
            ]
        )
    ).get("demo")
    states = _States({"light.a": "on", "light.b": "off"})  # light.c 缺失 → unverified
    report = evaluate_expects(auto, states, [{}])
    assert report["passed"] == 2 and report["failed"] == 0 and report["unverified"] == 1
    assert report["ok"] is True
    # 关键：有未验证项 → 不算"完全验证过"
    assert report["fully_verified"] is False


def test_evaluate_fail_reports_actual():
    auto = load_graph(_raw(expect=[{"entity_id": "light.a", "state": "on"}])).get("demo")
    report = evaluate_expects(auto, _States({"light.a": "off"}), [{}])
    assert report["ok"] is False and report["failed"] == 1
    assert "实际 'off'" in report["items"][0]["reason"]


def test_evaluate_state_list_any_of():
    auto = load_graph(_raw(expect=[{"entity_id": "light.a", "state": ["on", "off"]}])).get("demo")
    assert evaluate_expects(auto, _States({"light.a": "off"}), [{}])["passed"] == 1


def test_resolve_var_and_var_form():
    assert resolve_var("r.success", {"r": {"success": True}}) is True
    assert resolve_var("vars.flag", {"flag": 1}) == 1
    assert resolve_var("nope", {}) is MISSING

    auto = load_graph(_raw(expect=[{"var": "r.success", "op": "eq", "value": True}])).get("demo")
    # 任一实例满足即通过
    assert evaluate_expects(auto, _States({}), [{"r": {"success": False}}, {"r": {"success": True}}])["passed"] == 1
    # 没有活跃实例 → unverified（不是 fail）
    assert evaluate_expects(auto, _States({}), [])["unverified"] == 1


def test_evaluate_graph_skips_undeclared():
    graph = load_graph({"automations": [_raw(id="a"), _raw(id="b", expect=[{"entity_id": "light.a", "state": "on"}])]})
    out = evaluate_graph_expects(graph, _States({"light.a": "on"}), [{}])
    assert set(out["automations"]) == {"b"}  # 未声明的 a 不进入结果
    assert out["fully_verified"] is True


def test_unmodeled_actions_break_fully_verified():
    graph = load_graph(_raw(expect=[{"entity_id": "light.study_main", "state": "on"}]))
    out = evaluate_graph_expects(graph, _States({"light.study_main": "on"}), [{}], unmodeled_actions=["light.turn_on"])
    assert out["ok"] is True
    assert out["fully_verified"] is False  # 动作没建模 → 后果根本没验过


# ─────────────────────────────────────────────────────────────────────
# 6/7. 扫描器 + hint
# ─────────────────────────────────────────────────────────────────────


def test_scanner_expect_missing_is_warning():
    scan = StaticScanner(load_graph(_raw())).scan()
    codes = {d.code for d in scan.warnings}
    assert "EXPECT_MISSING" in codes
    assert scan.ok  # warning 不拦截（不破坏既有 IR）


def test_scanner_no_expect_missing_when_declared():
    graph = load_graph(_raw(expect=[{"entity_id": "light.study_main", "state": "on"}]))
    scan = StaticScanner(graph).scan()
    assert "EXPECT_MISSING" not in {d.code for d in scan.warnings}


def test_scanner_expect_unreachable_is_error():
    graph = load_graph(_raw(expect=[{"entity_id": "light.other_room", "state": "on"}]))
    scan = StaticScanner(graph).scan()
    assert "EXPECT_UNREACHABLE" in {d.code for d in scan.errors}
    assert scan.ok is False


def test_scanner_rejects_state_outside_domain_contract():
    """复用 v1.1.0 的域契约表：灯断言成 cover 的 open → 告警。"""
    graph = load_graph(_raw(expect=[{"entity_id": "light.study_main", "state": "open"}]))
    scan = StaticScanner(graph).scan()
    assert "EXPECT_STATE_INVALID" in {d.code for d in scan.warnings}


def test_scanner_allows_unavailable_and_placeholder_domains():
    """`unavailable` 全域合法；`select` 等占位符域不做静态判定（不误报）。"""
    graph = load_graph(
        _raw(expect=[{"entity_id": "light.study_main", "state": "unavailable"}])
    )
    assert "EXPECT_STATE_INVALID" not in {d.code for d in StaticScanner(graph).scan().warnings}

    select_graph = load_graph(
        _raw(expect=[{"entity_id": "select.tv_source", "state": "HDMI 3"}])
    )
    assert "EXPECT_STATE_INVALID" not in {d.code for d in StaticScanner(select_graph).scan().warnings}


def test_diagnostic_hint_autofilled():
    scan = StaticScanner(load_graph(_raw())).scan()
    diag = next(d for d in scan.warnings if d.code == "EXPECT_MISSING")
    assert diag.hint == CODE_HINT["EXPECT_MISSING"]
    assert "建议：" in str(diag)  # hint 渲染进诊断文本


def test_build_output_carries_hint():
    out = svc.build(_raw())
    item = next(d for d in out["diagnostics"] if d["code"] == "EXPECT_MISSING")
    assert item["hint"]


# ─────────────────────────────────────────────────────────────────────
# 8. af_simulate 端到端
# ─────────────────────────────────────────────────────────────────────


#: 触发「人来」的事件（`simulate` 无事件时不会有任何实例，断言只会是 unverified）
MOTION_ON = [{"entity_id": "binary_sensor.study_motion", "state": "on"}]


def test_simulate_expect_passes_on_case11():
    raw = (EXAMPLES / "case11_expect.json").read_text(encoding="utf-8")
    import json

    out = svc.simulate(json.loads(raw), events=MOTION_ON)
    assert out["expect"]["ok"] is True
    assert out["expect"]["declared"] == 2
    assert out["expect"]["fully_verified"] is True, out["expect"]
    assert out["final_states"]["light.study_main"] == "on"


def test_simulate_expect_fails_when_wrong():
    raw = _raw(expect=[{"entity_id": "light.study_main", "state": "off"}])  # 实际应为 on
    out = svc.simulate(raw, events=MOTION_ON)
    assert out["expect"]["ok"] is False
    assert out["expect"]["failed"] == 1
    assert out["final_states"]["light.study_main"] == "on"


def test_simulate_seeds_expect_entities_so_they_are_verifiable():
    """`expect` 里断言的实体即使不在图里读写，也会被播种 → 断言**可验证**（而不是 unverified）。

    这是刻意的：不播种就只能报「无法验证」，等于把断言架空。
    （注：静态扫描仍会把这种「断言不在 reads/writes 里」的实体报为 `EXPECT_UNREACHABLE` error。）
    """
    out = svc.simulate(_raw(expect=[{"entity_id": "light.ghost", "state": "off"}]), events=MOTION_ON)
    assert out["expect"]["unverified"] == 0
    assert out["expect"]["passed"] == 1
    assert out["expect"]["fully_verified"] is True


def test_simulate_var_assertion_unverified_without_instances():
    """变量形态：没有活跃实例 → unverified（不谎报通过，也不误判失败）。"""
    out = svc.simulate(_raw(expect=[{"var": "r.success", "op": "eq", "value": True}]))
    assert out["expect"]["ok"] is True
    assert out["expect"]["unverified"] == 1
    assert out["expect"]["fully_verified"] is False


def test_simulate_without_events_still_fails_assertion():
    """无事件 → 自动化没跑 → 断言「灯应当 on」而实际 off → **判失败**（不是"没验到就放行"）。

    这是刻意的：无事件时实体已被播种（默认 off），断言与事实不符就该报失败，
    而不是用 unverified 掩盖"自动化根本没执行"。
    """
    out = svc.simulate(_raw(expect=[{"entity_id": "light.study_main", "state": "on"}]))
    assert out["expect"]["ok"] is False
    assert out["expect"]["failed"] == 1
    assert out["final_states"]["light.study_main"] == "off"


# ─────────────────────────────────────────────────────────────────────
# 9/10. NL + AF-Spec
# ─────────────────────────────────────────────────────────────────────


def test_nl_renders_expect_section():
    auto = load_graph(_raw(expect=[{"entity_id": "light.study_main", "state": "on", "note": "灯必须亮"}])).get("demo")
    text = render_automation(auto).text
    assert "预期（跑完之后应当如此）" in text
    assert "light.study_main 应当为 “on”" in text
    assert "灯必须亮" in text


def test_spec_roundtrip_preserves_expect():
    graph = load_graph(_raw(expect=[{"entity_id": "light.study_main", "state": "on"}, {"var": "r.success", "op": "eq", "value": True}]))
    rebuilt = compile_spec(render_spec(graph))
    assert rebuilt.get("demo").expects() == graph.get("demo").expects()


# ─────────────────────────────────────────────────────────────────────
# 11. FakeHA 未建模动作留痕
# ─────────────────────────────────────────────────────────────────────


def test_fakeha_tracks_unmodeled_actions():
    states = seed_from_graph(load_graph(_raw()))
    adapter = FakeHAAdapter(states)
    adapter.call("light.turn_on", {"entity_id": "light.study_main"})  # 已建模
    adapter.call("mythical.domain_x", {"entity_id": "mythical.thing"})  # 未建模（从未登记）
    assert adapter.unmodeled == ["mythical.domain_x"]
    assert states.get("light.study_main") == "on"
    # 未建模的不伪造状态
    assert states.get("mythical.thing") is None


def test_seed_from_graph_includes_expect_entities():
    graph = load_graph(_raw(expect=[{"entity_id": "light.extra", "state": "on"}]))
    graph.get("demo").writes  # 触发 reads/writes 计算
    # 断言实体不在 writes 里（否则本测试无意义）
    assert "light.extra" not in graph.get("demo").writes()
    states = seed_from_graph(graph)
    assert states.get("light.extra") is not None  # 但被播种了 → 断言可验证
