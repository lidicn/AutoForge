"""第二期第一轮～第五轮审计的修复判据（AF1 深度闸／AF2·AF3·AF4 落盘护栏）。

口径两条，都是从这两轮的教训里来的：
- **「拒绝写入」与「丢数据」在「盘上内容变了」这一观察下无法区分**——判定必须靠异常类型，
  且 try 要套在**公开写入口**上（`record()`／`try_claim()` 内部自己会落盘）。
- **护栏必须紧邻落盘调用**（第一期 W30：装在调用方不走到的方法上等于没装）——所以除了行为腿，
  还有一条 AST 腿钉住"同一个函数体里既有护栏又有 `atomic_write_text`"。
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autoforge.af_atomic import refuse_when_shape_unreadable  # noqa: E402
from autoforge.af_fire_recorder import JsonFireStore  # noqa: E402
from autoforge.af_ir import expr as expr_mod  # noqa: E402
from autoforge.af_ir.models import (  # noqa: E402
    IRDepthError,
    IRValidationError,
    MAX_IR_DEPTH,
    _exceeds_container_depth,
    validate_automation,
)
from autoforge.af_pretrigger import TriggerHistory  # noqa: E402
from autoforge.af_undo import UndoStore  # noqa: E402

# ── AF1：schema 之前的整篇深度闸 ────────────────────────────────────


def _deep_expr(n: int):
    node = {"op": "truthy", "value": {"var": "sensor.x"}}
    for _ in range(n):
        node = {"op": "not", "args": [node]}
    return node


def _ir(expr_node):
    return {
        "ir_version": "1.0",
        "id": "probe",
        "nodes": [
            {"id": "on1", "kind": "on",
             "trigger": {"type": "state", "entity_id": "sensor.x"}},
            {"id": "c1", "kind": "condition", "expr": expr_node},
            {"id": "a1", "kind": "action", "adapter": "light.turn_on",
             "params": {"entity_id": "light.a"}},
        ],
        "edges": [{"from": "on1", "to": "c1"}, {"from": "c1", "to": "a1"}],
    }


def _doc_depth(obj) -> int:
    best, stack = 0, [(obj, 1)]
    while stack:
        node, d = stack.pop()
        best = max(best, d)
        if isinstance(node, dict):
            stack += [(v, d + 1) for v in node.values()]
        elif isinstance(node, (list, tuple)):
            stack += [(v, d + 1) for v in node]
    return best


def test_af1_legit_extreme_is_not_rejected_by_depth_gate():
    """expr 打满 `MAX_EXPR_DEPTH=32` ⇒ 整篇深度 70，必须**不被**深度闸拒。

    审计副本里的 `_MAX_SCHEMA_DEPTH=64` 会把这张合法 IR 直接拒掉（64 < 70）——那不是"错误信息
    变粗略"，是把预算内合法的输入判死。本腿钉住 128 这个数不与三条域预算抢跑道。
    """
    doc = _ir(_deep_expr(expr_mod.MAX_EXPR_DEPTH))
    assert _doc_depth(doc) == 70, "合法上界实测值变了，MAX_IR_DEPTH 的推导要重算"
    assert 70 < MAX_IR_DEPTH
    with pytest.raises(IRValidationError) as caught:
        validate_automation(doc)
    assert not isinstance(caught.value, IRDepthError)


def test_af1_schema_crash_point_is_above_the_gate():
    """修前实测爆栈点（expr=122 ⇒ 整篇 250）必须在闸之后，且现在给的是 400 那一族异常。"""
    doc = _ir(_deep_expr(122))
    assert _doc_depth(doc) == 250
    assert 250 > MAX_IR_DEPTH
    with pytest.raises(IRDepthError) as caught:
        validate_automation(doc)
    assert isinstance(caught.value, IRValidationError)
    assert str(MAX_IR_DEPTH) in str(caught.value)


@pytest.mark.parametrize("n", [150, 200, 300])
def test_af1_no_recursion_error_on_deeper_inputs(n):
    with pytest.raises(IRDepthError):
        validate_automation(_ir(_deep_expr(n)))


def test_af1_gate_walk_is_iterative_on_pathological_depth():
    """深度探针自己绝不能递归：5 万层列表要答得出来，而不是先把它自己压垮。"""
    pathological = ["x"] * 1
    for _ in range(50_000):
        pathological = [pathological]
    assert _exceeds_container_depth(pathological, MAX_IR_DEPTH) is True
    assert _exceeds_container_depth({"a": [1, 2, {"b": 3}]}, MAX_IR_DEPTH) is False


def test_af1_depth_gate_fires_before_schema_recursion_not_after():
    """闸在 `iter_errors` 之前：证据是消息里带 `MAX_IR_DEPTH`，而不是 schema 的 `<root>: …`。"""
    with pytest.raises(IRDepthError) as caught:
        validate_automation(_ir(_deep_expr(400)))
    assert "JSON Schema 校验之前" in str(caught.value)


# ── AF2／AF3／AF4：读失败 + 整档覆盖 = 丢数据 ────────────────────────

CORRUPT = "{这不是合法 JSON"


def _seed_and_corrupt(path: Path, n_units) -> None:
    """先正常写出若干条，再把盘上那份换成坏字节。"""
    assert path.is_file(), "播种没落盘，对照组无效"
    path.write_text(CORRUPT, encoding="utf-8")


def test_af2_fire_recorder_refuses_to_overwrite_corrupt_log(tmp_path):
    store = JsonFireStore(tmp_path)
    for i in range(3):
        store.try_claim(f"rule{i}", "2026-10-11", "2026-10-11T00:00:00", "2026-10-11T00:05:00", 2)
    log = tmp_path / "fire_log.json"
    _seed_and_corrupt(log, 3)

    reopened = JsonFireStore(tmp_path)  # 读不出 ⇒ 冷启动为空（允许），但盘上坏字节还在
    with pytest.raises(ValueError):
        reopened.try_claim("ruleX", "2026-10-11", "2026-10-11T00:00:00", "2026-10-11T00:05:00", 2)
    assert log.read_text(encoding="utf-8") == CORRUPT, "现场被抹掉了：护栏没生效"


def test_af3_undo_refuses_to_overwrite_corrupt_log(tmp_path):
    store = UndoStore(str(tmp_path))
    for i in range(3):
        store.record(f"deploy{i}", {"light.a": {"state": "on"}})
    log = tmp_path / "undo_log.json"
    _seed_and_corrupt(log, 3)

    reopened = UndoStore(str(tmp_path))
    with pytest.raises(ValueError):
        reopened.record("deployX", {"light.a": {"state": "on"}})
    assert log.read_text(encoding="utf-8") == CORRUPT


def test_af4_pretrigger_refuses_to_overwrite_corrupt_history(tmp_path):
    history = TriggerHistory(persist_dir=str(tmp_path))
    for i in range(3):
        history.record(f"auto{i}", "2026-10-11T00:00:00")
    log = Path(history.path)
    _seed_and_corrupt(log, 3)

    reopened = TriggerHistory(persist_dir=str(tmp_path))
    with pytest.raises(ValueError):
        reopened.record("autoX", "2026-10-11T00:00:00")
    assert log.read_text(encoding="utf-8") == CORRUPT


def test_guards_do_not_block_the_healthy_write_path(tmp_path):
    """反例腿：护栏不能把正常首写与正常覆写一起挡掉（否则修一个缺陷造出一个更差的）。"""
    store = JsonFireStore(tmp_path)
    store.try_claim("rule", "2026-10-11", "2026-10-11T00:00:00", "2026-10-11T00:05:00", 2)
    before = (tmp_path / "fire_log.json").read_text(encoding="utf-8")
    store.try_claim("rule2", "2026-10-11", "2026-10-11T00:00:00", "2026-10-11T00:05:00", 2)
    assert "rule2" in (tmp_path / "fire_log.json").read_text(encoding="utf-8")
    assert before != (tmp_path / "fire_log.json").read_text(encoding="utf-8")


def test_missing_file_is_a_normal_first_write_not_a_refusal(tmp_path):
    refuse_when_shape_unreadable(tmp_path / "never_written.json", "对照组")  # 不抛 = 放行首写


def test_helper_rejects_wrong_top_level_shape(tmp_path):
    p = tmp_path / "as_list.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError, match="形状不是"):
        refuse_when_shape_unreadable(p, "形状对照")
    refuse_when_shape_unreadable(p, "形状对照", expect="list")  # 换成期望形状就放行


# ── 护栏位置判据（W30：装在调用方不走到的方法上等于没装）────────────

GUARDED = {
    "af_fire_recorder.py": "JsonFireStore",
    "af_undo.py": "UndoStore",
    "af_pretrigger.py": "TriggerHistory",
}


@pytest.mark.parametrize("fname", sorted(GUARDED))
def test_guard_sits_in_the_same_function_that_does_the_atomic_rewrite(fname):
    tree = ast.parse((SRC / "autoforge" / fname).read_text(encoding="utf-8"))
    hits = []
    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        for fn in [n for n in cls.body if isinstance(n, ast.FunctionDef)]:
            names = set()
            for call in ast.walk(fn):
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Name):
                    names.add(call.func.id)
            if "refuse_when_shape_unreadable" in names and "atomic_write_text" in names:
                hits.append(f"{cls.name}.{fn.name}")
    assert hits, f"{fname}：护栏没有和整档重写待在同一个函数体里——这一站又装错了阶段"
