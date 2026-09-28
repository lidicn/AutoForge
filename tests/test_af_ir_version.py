"""F9 前置（决策 D）：ir_version 枚举化 + 单一真值源校验。

- schema 的 ir_version.enum 必须与 af_ir.SUPPORTED_IR_VERSIONS 同步（灰度兼容）。
- 非法 ir_version 必须被 JSON Schema 拒绝（防止旧运行时误读新 IR）。
"""

import json

from autoforge.af_ir import (
    IRValidationError,
    SUPPORTED_IR_VERSIONS,
    SCHEMA_PATH,
    is_supported_ir_version,
    load_automation,
)


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
