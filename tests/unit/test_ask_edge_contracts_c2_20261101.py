"""C-2 §G.2 判据：ask 边契约两枚新码（PM 裁落点＝af_scanner，⛔ 不动 AskSpec.__post_init__）。

- ASK_BINARY_NO_DENY_PATH（G.2①）：二元 ask 必须有 `no` 边，或 `default` 分支
  可达集不含执行类节点（do）——D.2「听不清不执行」的静态面：unknown 收紧为 no
  后经 {kind,"default"} 选路，default 直达 do ＝ 无人值守执行缺口。
- ASK_STRUCT_NO_THEN（G.2③）：结构化 ask 必须有 `then` 边（answer_structured 的
  恢复通道），且不得带 yes/no 出边（二元语义死边，运行期不可达）。
- G.2②（带 timeout 须有 on_timeout/default）由既有 MISSING_TIMEOUT_OR_DEFAULT
  覆盖（它对所有 ask 无差别要求）⇒ 本文件钉「不双写」：该场景只发既有码。
"""

from __future__ import annotations

from autoforge.af_ir import load_graph
from autoforge.af_scanner import StaticScanner


def _ir(nodes: list[dict], edges: list[dict], ask_id: str = "c2g2") -> dict:
    return {
        "ir_version": "0.2.1", "id": ask_id, "name": ask_id, "version": 1, "mode": "single",
        "nodes": nodes,
        "edges": edges,
    }


def _binary_nodes(timeout: str | None = "60s") -> list[dict]:
    ask = {"id": "q1", "kind": "ask", "name": "q", "prompt": "开吗？", "room": "study"}
    if timeout:
        ask["timeout"] = timeout
    return [
        {"id": "t1", "kind": "on", "name": "t", "trigger": {"type": "state", "entity_id": "light.x", "to": "on"}},
        ask,
        {"id": "d1", "kind": "do", "name": "d1", "adapter": "mock", "action": "light.turn_on",
         "params": {"entity_id": "light.y"}},
        {"id": "p1", "kind": "pass", "name": "p"},
    ]


def _structured_nodes() -> list[dict]:
    return [
        {"id": "t1", "kind": "on", "name": "t", "trigger": {"type": "state", "entity_id": "light.x", "to": "on"}},
        {"id": "q1", "kind": "ask", "name": "q", "prompt": "多少度？", "room": "study",
         "timeout": "60s", "ask": {"kind": "threshold", "min": 16, "max": 30}},
        {"id": "d1", "kind": "set", "name": "d1", "var": "target", "value": 22},
        {"id": "p1", "kind": "pass", "name": "p"},
    ]


def _errors(ir: dict) -> set[tuple[str, str]]:
    scan = StaticScanner(load_graph(ir)).scan()
    return {(d.code, d.node_id) for d in scan.diagnostics if d.level == "error"}


# ── G.2① 二元 ask 的拒绝出口 ─────────────────────────────────────────

def test_g2_binary_with_no_edge_is_clean():
    ir = _ir(_binary_nodes(), [
        {"from": "t1", "to": "q1", "kind": "then"},
        {"from": "q1", "to": "d1", "kind": "yes"},
        {"from": "q1", "to": "p1", "kind": "no"},
        {"from": "q1", "to": "p1", "kind": "on_timeout"},
        {"from": "d1", "to": "p1", "kind": "then"},
    ])
    errs = _errors(ir)
    assert not any(c.startswith("ASK_") for c, _ in errs), errs


def test_g2_binary_no_edge_default_to_do_is_red():
    """无 no 边且 default 直达 do ⇒ ASK_BINARY_NO_DENY_PATH（D.2 缺口）。"""
    ir = _ir(_binary_nodes(), [
        {"from": "t1", "to": "q1", "kind": "then"},
        {"from": "q1", "to": "d1", "kind": "yes"},
        {"from": "q1", "to": "d1", "kind": "default"},
        {"from": "q1", "to": "p1", "kind": "on_timeout"},
        {"from": "d1", "to": "p1", "kind": "then"},
    ])
    assert ("ASK_BINARY_NO_DENY_PATH", "q1") in _errors(ir)


def test_g2_binary_no_edge_default_to_pass_is_clean():
    """无 no 边但 default 指向非执行下游 ⇒ 合法（守"听不清不执行"的替代形）。"""
    ir = _ir(_binary_nodes(), [
        {"from": "t1", "to": "q1", "kind": "then"},
        {"from": "q1", "to": "d1", "kind": "yes"},
        {"from": "q1", "to": "p1", "kind": "default"},
        {"from": "q1", "to": "p1", "kind": "on_timeout"},
        {"from": "d1", "to": "p1", "kind": "then"},
    ])
    errs = _errors(ir)
    assert not any(c == "ASK_BINARY_NO_DENY_PATH" for c, _ in errs), errs


def test_g2_binary_neither_no_nor_default_is_red():
    ir = _ir(_binary_nodes(timeout=None), [
        {"from": "t1", "to": "q1", "kind": "then"},
        {"from": "q1", "to": "d1", "kind": "yes"},
        {"from": "d1", "to": "p1", "kind": "then"},
    ])
    errs = _errors(ir)
    assert ("ASK_BINARY_NO_DENY_PATH", "q1") in errs, errs


# ── G.2③ 结构化 ask 的恢复通道 ───────────────────────────────────────

def test_g2_structured_with_then_is_clean():
    ir = _ir(_structured_nodes(), [
        {"from": "t1", "to": "q1", "kind": "then"},
        {"from": "q1", "to": "d1", "kind": "then"},
        {"from": "q1", "to": "p1", "kind": "on_timeout"},
        {"from": "d1", "to": "p1", "kind": "then"},
    ])
    errs = _errors(ir)
    assert not any(c == "ASK_STRUCT_NO_THEN" for c, _ in errs), errs


def test_g2_structured_without_then_is_red():
    ir = _ir(_structured_nodes(), [
        {"from": "t1", "to": "q1", "kind": "then"},
        {"from": "q1", "to": "p1", "kind": "on_timeout"},
    ])
    assert ("ASK_STRUCT_NO_THEN", "q1") in _errors(ir)


def test_g2_structured_yes_edge_is_dead_edge_red():
    """结构化 ask 带 yes 出边 ⇒ 死边（answer_structured 只走 then）⇒ 同码。"""
    ir = _ir(_structured_nodes(), [
        {"from": "t1", "to": "q1", "kind": "then"},
        {"from": "q1", "to": "d1", "kind": "then"},
        {"from": "q1", "to": "d1", "kind": "yes"},
        {"from": "q1", "to": "p1", "kind": "on_timeout"},
        {"from": "d1", "to": "p1", "kind": "then"},
    ])
    assert ("ASK_STRUCT_NO_THEN", "q1") in _errors(ir)


# ── G.2② 不双写：既有码覆盖 ──────────────────────────────────────────

def test_g2_timeout_escape_covered_by_existing_code_only():
    """带 timeout 无 on_timeout/default ⇒ 只发既有 MISSING_TIMEOUT_OR_DEFAULT，
    ⛔ 不另发新码（G.2② 已被既有判定覆盖，双写＝两套真源）。"""
    ir = _ir(_binary_nodes(timeout="60s"), [
        {"from": "t1", "to": "q1", "kind": "then"},
        {"from": "q1", "to": "d1", "kind": "yes"},
        {"from": "q1", "to": "p1", "kind": "no"},
        {"from": "d1", "to": "p1", "kind": "then"},
    ])
    errs = _errors(ir)
    assert ("MISSING_TIMEOUT_OR_DEFAULT", "q1") in errs, errs
    assert not any(c.startswith("ASK_BINARY") or c == "ASK_STRUCT_NO_THEN"
                   for c, _ in errs), errs


# ── 在册正例不误伤 ───────────────────────────────────────────────────

def test_examples_case04_and_lamp_sync_no_new_codes():
    """case04（ask 带 no/on_timeout/default）与 case_lamp_sync（无 ask）零新码。"""
    import json
    from pathlib import Path
    ex = Path(__file__).resolve().parents[2] / "examples" / "ir"
    for name in ("case04_ask_timeout.json", "case_lamp_sync.json"):
        d = json.loads((ex / name).read_text(encoding="utf-8"))
        errs = _errors(d)
        assert not any(c in ("ASK_BINARY_NO_DENY_PATH", "ASK_STRUCT_NO_THEN")
                       for c, _ in errs), (name, errs)


def test_catalog_pins_updated():
    """三 pin 之一：目录计数 52 ＋ 两新码在 CHECKS/CODE_HINT（细节由
    test_diagnostic_code_catalog 钉，这里防漏登记）。"""
    from autoforge.af_scanner import CHECKS, CODE_HINT
    for code in ("ASK_BINARY_NO_DENY_PATH", "ASK_STRUCT_NO_THEN"):
        assert code in CHECKS and code in CODE_HINT, code
