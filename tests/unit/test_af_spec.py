"""G7 AF-Spec 单测：编译 / 渲染 / 往返一致 / NL 对齐 / 错误 / CLI。"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from autoforge.af_cli import app
from autoforge.af_ir import load_graph
from autoforge.af_nl import render_graph
from autoforge.af_spec import SpecError, compile_spec, graph_to_raw, render_spec


def _spec() -> str:
    return """
    automation study_day_light
    name "书房白天人来补光"
    ir_version "0.2.1"
    version 1
    mode restart
    confidence 0.9
    snapshot true
    meta {"acceptance": "G1 用例 1"}
    var flag {"type": "boolean", "value": false}

    on a1 {"type": "state", "entity_id": "binary_sensor.study_motion", "to": "on"} name "书房检测到人"
    if i1 {"op": "lt", "left": {"var": "entity.sensor.study_illum", "type": "numeric"}, "right": {"const": 200}}
    do d1 ha.light.turn_on {"entity_id": "light.study_main"} result turn_on_result
    ask q1 "书房 27 度了，要开空调吗？" room study timeout 60s
    wait w1 10m
    set s1 var flag value true
    pass p1 name "结束"

    edge a1 -> i1 then
    edge i1 -> d1 then
    edge i1 -> p1 no
    edge d1 -> p1 on_error id e2 label "失败兜底"
    edge q1 -> p1 on_timeout
    edge w1 -> p1 on_timeout
    edge s1 -> p1 then
    """


def test_compile_spec_basic_structure():
    graph = compile_spec(_spec())
    auto = graph.get("study_day_light")

    assert auto.name == "书房白天人来补光"
    assert auto.mode == "restart" and auto.confidence == 0.9
    assert auto.vars["flag"].type == "boolean"
    assert auto.node("d1").adapter == "ha" and auto.node("d1").action == "light.turn_on"
    assert auto.node("d1").result_var == "turn_on_result"
    assert auto.node("q1").room == "study" and auto.node("q1").timeout == "60s"
    assert auto.node("w1").duration == "10m"
    assert auto.node("s1").var == "flag" and auto.node("s1").value is True
    edge = auto.edge_of("i1", "no")
    assert edge is not None and edge.to == "p1"
    e2 = auto.edge_of("d1", "on_error")
    assert e2 is not None and e2.id == "e2" and e2.label == "失败兜底"


def test_compiled_graph_passes_schema_and_build_gate(tmp_path):
    out = tmp_path / "study.json"
    result = CliRunner().invoke(app, ["spec", "compile", "--out", str(out), _write(tmp_path, _spec())])
    assert result.exit_code == 0, result.output
    # 生成的 IR 必须能被 build 闸（Schema + 扫描）接受
    build = CliRunner().invoke(app, ["build", str(out), "--no-nl"])
    assert build.exit_code == 0, build.output


def _write(tmp_path, text: str) -> str:
    path = tmp_path / "spec.forge"
    path.write_text(text, encoding="utf-8")
    return str(path)


# ─────────────────────────────────────────────────────────────────────
# 往返一致（对所有 IR 样例）
# ─────────────────────────────────────────────────────────────────────


def test_render_compile_roundtrip_all_examples(examples_dir):
    for path in sorted(examples_dir.glob("*.json")):
        graph = load_graph(path)
        text = render_spec(graph)
        rebuilt = compile_spec(text)
        assert graph_to_raw(rebuilt) == graph_to_raw(graph), f"往返不一致：{path.name}"
        assert render_spec(rebuilt) == text, f"渲染不幂等：{path.name}"


def test_nl_alignment_with_compiled_graph(examples_dir):
    """同一 Graph，AF-Spec 往返后 NL 渲染必须逐字一致（防止"批准的≠跑的"）。"""
    for path in sorted(examples_dir.glob("*.json")):
        graph = load_graph(path)
        rebuilt = compile_spec(render_spec(graph))
        assert render_graph(rebuilt).text == render_graph(graph).text, f"NL 不一致：{path.name}"


def test_roundtrip_preserves_set_from_and_do_flags():
    spec = """
    automation t
    name "t"
    if i1 {"op": "is_on", "value": {"var": "entity.light.x"}}
    set s1 var n from vars.last
    do d1 ha.climate.turn_on {"entity_id": "climate.x"} atomic confirm canary {"duration": "15m", "auto_rollback": true}
    pass p1
    edge i1 -> d1 then
    edge d1 -> s1 then
    edge s1 -> p1 then
    """
    graph = compile_spec(spec)
    rebuilt = compile_spec(render_spec(graph))
    assert graph_to_raw(rebuilt) == graph_to_raw(graph)
    node = graph.get("t").node("d1")
    assert node.atomic and node.requires_confirm and node.canary["duration"] == "15m"
    assert graph.get("t").node("s1").from_ == "vars.last"


# ─────────────────────────────────────────────────────────────────────
# 错误
# ─────────────────────────────────────────────────────────────────────


def test_error_empty_spec():
    with pytest.raises(SpecError):
        compile_spec("\n# 只有注释\n")


def test_error_node_before_automation():
    with pytest.raises(SpecError):
        compile_spec('pass p1\n')


def test_error_unknown_keyword():
    with pytest.raises(SpecError):
        compile_spec('automation a\nname "a"\nbogus 1\n')


def test_error_bad_edge():
    with pytest.raises(SpecError):
        compile_spec('automation a\nname "a"\nedge a1 > b1 then\n')


def test_render_rejects_reserved_fields():
    graph = load_graph(
        {
            "ir_version": "0.2.1",
            "id": "r",
            "name": "r",
            "version": 1,
            "mode": "single",
            "nodes": [
                {"id": "o", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
                {"id": "e", "kind": "set", "var": "x", "value": 1, "fn": {"lang": "cel"}},
                {"id": "p", "kind": "pass"},
            ],
            "edges": [{"from": "o", "to": "e", "kind": "then"}, {"from": "e", "to": "p", "kind": "then"}],
        }
    )
    with pytest.raises(SpecError):
        render_spec(graph)


# ─────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────


def test_cli_spec_compile_and_render(tmp_path, examples_dir):
    spec_path = tmp_path / "s.forge"
    spec_path.write_text(_spec(), encoding="utf-8")
    out = tmp_path / "out.json"

    compile_result = CliRunner().invoke(app, ["spec", "compile", str(spec_path), "--out", str(out)])
    assert compile_result.exit_code == 0, compile_result.output
    assert out.exists()

    render_result = CliRunner().invoke(app, ["spec", "render", str(examples_dir / "case01_day_light.json")])
    assert render_result.exit_code == 0, render_result.output
    assert "automation study_day_light" in render_result.output
