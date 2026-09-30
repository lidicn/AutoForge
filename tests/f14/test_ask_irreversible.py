"""F14 P3 + P4 验收 —— AskSpec 跨层复用 / 不可逆字段标注。

P3（设计 §3.4）：NL→IR 生成的 AskSpec 与 IR 模型是**同一个 hashable 类**，
控件类型（kind→widget）匹配；ask 节点过 `load_automation` + P1 `verify_roundtrip`。

P4（设计 §2.2/§2.3/§3.3）：运行时字段（`stage`/`diff_sha` 等）不进可逆核心——
NL Builder 绝不写入；IR→NL→IR 原样保留于 raw；NL 渲染用 `[运行时]` 占位符；
L2/L3 字段显式标注 `_non_reversible: true`。
"""
from __future__ import annotations

import pytest

from autoforge import af_nl
from autoforge.af_fidelity import verify_roundtrip
from autoforge.af_ir import AskSpec, Automation, load_automation
from autoforge.af_irreversible import (
    NON_REVERSIBLE_KEY,
    NL_RUNTIME_PLACEHOLDER,
    RUNTIME_ONLY_FIELDS,
    annotate_non_reversible,
    is_node_non_reversible,
    nl_runtime_note,
    runtime_fields_of,
)
from autoforge.af_nl_build import build_ir_from_nl


# ════════════════════════════ P3 · AskSpec 跨层复用 ════════════════════════════

def test_nl_confirm_builds_ask_node_with_shared_ask_spec():
    ir = build_ir_from_nl("当 binary_sensor.motion 变为 on 时，确认 \"要开灯吗？\"")
    ask = next(n for n in ir["nodes"] if n["kind"] == "ask")
    # 装载后模型层的 ask 必须是同一个 af_ir.models.AskSpec 类（跨层单对象，非复制）
    auto = Automation.from_dict(ir)
    node = next(n for n in auto.nodes.values() if n.kind == "ask")
    assert isinstance(node.ask, AskSpec)
    assert node.ask.kind == "choice"
    assert tuple(node.ask.options) == ("是", "否")
    assert node.prompt == "要开灯吗？"


def test_nl_ask_with_options_builds_choice_spec():
    ir = build_ir_from_nl("日落时，询问 \"选亮度\" 选项 低 / 中 / 高")
    auto = Automation.from_dict(ir)
    node = next(n for n in auto.nodes.values() if n.kind == "ask")
    assert node.ask.kind == "choice"
    assert tuple(node.ask.options) == ("低", "中", "高")


def test_nl_ask_without_options_is_text_spec():
    ir = build_ir_from_nl("日落时，询问 \"输入目标温度\"")
    node = next(n for n in Automation.from_dict(ir).nodes.values() if n.kind == "ask")
    assert node.ask.kind == "text"


@pytest.mark.parametrize("text", [
    "当 binary_sensor.motion 变为 on 时，确认 \"要开灯吗？\"",
    "日落时，询问 \"选亮度\" 选项 低 / 中 / 高",
    "每天 08:00，询问 \"输入昵称\"",
])
def test_ask_nodes_pass_schema_and_roundtrip(text):
    ir = build_ir_from_nl(text)
    load_automation(ir)
    report = verify_roundtrip(ir)
    assert report.ok, f"{text!r} 往返保真失败：{report.detail}"


def test_ask_spec_is_hashable_and_equal_specs_dedupe():
    a = AskSpec(kind="choice", options=("是", "否"), prompt="p")
    b = AskSpec(kind="choice", options=("是", "否"), prompt="p")
    assert hash(a) == hash(b)
    assert len({a, b}) == 1
    assert {a: "v"}[b] == "v"  # 可作 dict 键


@pytest.mark.parametrize("kind,widget", [
    ("choice", "select"),
    ("threshold", "slider"),
    ("time_range", "time_range"),
    ("entity", "entity_picker"),
    ("text", "input"),
])
def test_ask_spec_control_widget_matches_kind(kind, widget):
    kwargs = {"kind": kind}
    if kind == "choice":
        kwargs["options"] = ("a", "b")
    elif kind in ("threshold", "time_range"):
        kwargs["min_"], kwargs["max_"] = (0, 10)
    elif kind == "entity":
        kwargs["entity_domain"] = "light"
    spec = AskSpec(**kwargs)
    assert spec.control()["widget"] == widget


def test_control_mapping_covers_all_kinds():
    assert set(AskSpec.KINDS) == {"choice", "entity", "time_range", "threshold", "text"}


# ════════════════════════════ P4 · 不可逆字段处理 ════════════════════════════

def _runtime_ir():
    return {
        "ir_version": "0.2.1", "id": "rt1", "name": "运行时样本", "version": 1, "mode": "single",
        "nodes": [
            {"id": "n_on", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.x", "to": "on"}},
            {"id": "n_do", "kind": "do", "adapter": "ha", "action": "light.turn_on",
             "params": {"entity_id": "light.a"}, "stage": "shadow", "diff_sha": "deadbeef"},
            {"id": "n_pass", "kind": "pass"},
        ],
        "edges": [{"from": "n_on", "to": "n_do", "kind": "then"}, {"from": "n_do", "to": "n_pass", "kind": "then"}],
    }


def test_nl_builder_never_writes_runtime_or_nonreversible_fields():
    for text in ["当 binary_sensor.s 变为 on 时，打开 light.a", "日落时，确认 \"要开灯吗？\"", "每天 07:00，把湿度设为 humidifier.n"]:
        ir = build_ir_from_nl(text)
        for n in ir["nodes"]:
            assert not (set(n) & RUNTIME_ONLY_FIELDS), n
            assert NON_REVERSIBLE_KEY not in n, n


def test_runtime_fields_preserved_in_raw_and_core_fidelity_unaffected():
    ir = _runtime_ir()
    auto = load_automation(ir)
    do = auto.nodes["n_do"]
    assert do.raw["stage"] == "shadow" and do.raw["diff_sha"] == "deadbeef"
    # 运行时字段不进可逆核心：verify_roundtrip 仍判保真
    assert verify_roundtrip(ir).ok is True


def test_render_uses_runtime_placeholder():
    ir = _runtime_ir()
    result = af_nl.render_automation(Automation.from_dict(ir))
    assert NL_RUNTIME_PLACEHOLDER in result.text
    assert "stage" in result.text and "diff_sha" in result.text


def test_runtime_fields_of_sorted_and_detection():
    assert runtime_fields_of({"kind": "do", "diff_sha": "x", "stage": "y"}) == ["diff_sha", "stage"]
    assert is_node_non_reversible({"kind": "do", "stage": "s"}) is True
    assert is_node_non_reversible({"kind": "do", NON_REVERSIBLE_KEY: True}) is True
    assert is_node_non_reversible({"kind": "do", "action": "light.turn_on"}) is False


def test_annotate_non_reversible_marks_and_idempotent():
    clean = {"id": "n", "kind": "do", "action": "light.turn_on"}
    assert annotate_non_reversible(clean) is clean  # 无运行时字段：原样返回
    dirty = {"id": "n", "kind": "do", "action": "light.turn_on", "stage": "shadow"}
    marked = annotate_non_reversible(dirty)
    assert marked[NON_REVERSIBLE_KEY] is True
    assert annotate_non_reversible(marked)[NON_REVERSIBLE_KEY] is True  # 幂等
    assert dirty.get(NON_REVERSIBLE_KEY) is None  # 原 dict 未被就地修改


def test_nl_runtime_note_shape():
    assert nl_runtime_note({"kind": "do"}) == ""
    note = nl_runtime_note({"kind": "do", "diff_sha": "z"})
    assert NL_RUNTIME_PLACEHOLDER in note and "diff_sha" in note
