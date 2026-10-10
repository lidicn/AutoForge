"""F9 前置（决策 D）：ir_version 枚举化 + 单一真值源校验。

- schema 的 ir_version.enum 必须与 af_ir.SUPPORTED_IR_VERSIONS 同步（灰度兼容）。
- 非法 ir_version 必须被 JSON Schema 拒绝（防止旧运行时误读新 IR）。
- IR 版本字面量在整棵 src 树里只许写一遍（第六轮审计 ARCH-02 的机械那半边）。
"""

import ast
import json
import pathlib

from autoforge.af_ir import (
    IRValidationError,
    IR_VERSION,
    SUPPORTED_IR_VERSIONS,
    SCHEMA_PATH,
    is_supported_ir_version,
    load_automation,
)

SRC_ROOT = pathlib.Path(__file__).resolve().parents[1] / "src" / "autoforge"
#: 只许在这一个文件里被"写"成字面量，别处一律 import。
IR_VERSION_SOURCES = {"models.py"}
IR_VERSION_NAMES = {"IR_VERSION", "GROUP_IR_VERSION"}


MIN_IR = {
    "ir_version": "0.2.1",
    "id": "case_x",
    "name": "测试自动化",
    "version": 1,
    "mode": "single",
    "nodes": [
        {"id": "t1", "kind": "on", "trigger": {"type": "state", "entity_id": "light.x", "to": "on"}},
        {"id": "p1", "kind": "pass"},
    ],
    "edges": [{"from": "t1", "to": "p1", "kind": "then"}],
}


def test_schema_ir_version_enum_matches_single_source():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["properties"]["ir_version"]["enum"] == list(SUPPORTED_IR_VERSIONS)


def test_supported_ir_version_helper():
    assert is_supported_ir_version("0.2.1") is True
    assert is_supported_ir_version("9.9.9") is False


def test_valid_ir_version_loads():
    auto = load_automation(MIN_IR)  # 不应抛
    assert auto.id == "case_x"


def test_unknown_ir_version_rejected():
    bad = dict(MIN_IR, ir_version="9.9.9")
    try:
        load_automation(bad)
    except IRValidationError as exc:
        assert any("ir_version" in str(e.path) for e in exc.errors) or "ir_version" in exc.args[0]
    else:
        raise AssertionError("非法 ir_version 应被 JSON Schema 拒绝")


def _hand_copied_ir_version_literals():
    """整棵 src/autoforge 里把 IR 版本号重新写成字面量的位置（file:line，含嵌套作用域）。"""
    hits = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        if path.name in IR_VERSION_SOURCES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if not any(
                isinstance(t, ast.Name) and t.id in IR_VERSION_NAMES for t in node.targets
            ):
                continue
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                hits.append(f"{path.relative_to(SRC_ROOT.parent)}:{node.lineno}")
    return hits


def test_ir_version_literal_written_only_once():
    hits = _hand_copied_ir_version_literals()
    assert not hits, (
        "IR 版本号在 af_ir/models.py 之外又被写成字面量（手抄=改一处忘一处的静默格式漂移）："
        + "、".join(hits)
    )


def test_orchestrator_exposes_the_canonical_ir_version():
    # af_closedloop 经 load_module().IR_VERSION 读它，真源收敛后这个模块属性不能消失。
    from autoforge import af_orchestrator

    assert af_orchestrator.IR_VERSION == IR_VERSION
