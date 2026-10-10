"""诊断码目录（`CHECKS` / `CODE_HINT`）的通用判据——收《AF完整架构与运行时说明》§十八 B.8。

缺口形状（现读，本批前）：`af_scanner.py` 以 **ERROR** 级发出 `L2_NEEDS_CANARY`，但两份目录里都没有这枚键。
后果有两层，都不靠"人眼盯"能发现：

1. `Diagnostic.__post_init__`（`af_scanner.py:174-176`）在 `hint` 为空时回退 `CODE_HINT.get(code, "")`
   ⇒ 这条 ERROR 到 Agent 手上时 `hint=""`，v1.2.0 立"一次往返自修正"的那张脸对它**静默**；
2. 目录是"扫描器会报哪些检查"的对外读数 ⇒ 枚举 `CHECKS` 的消费面少一格硬门。

先前没有任何判据管这事：`test_p1_2_confirm_policy.py` 用 `"CANARY" in d.code` 这种**模糊谓词**验行为，
键没登记照样绿。本批把判据换成"发得出的码必在目录里"，并按名字钉住这枚键。

方向只查一遍：`emitted_literal ⊆ CHECKS`。**不查反向双射**——有 7 枚键是经 `code = "X"` 与
`LIVE_*` 模块常量发出的，字面量不在 `Diagnostic("X")` 的第一个位置上（那 7 枚的发出点见
`_NON_LITERAL_KEYS` 注释）。反向那一遍用 `_NON_LITERAL_KEYS` 这枚**点名名单**来当棘轮：
目录里凭空多一枚谁也发不出的键 ⇒ 红，作者必须要么发出它、要么把发出点补进名单。
"""

from __future__ import annotations

import ast
from pathlib import Path

from autoforge.af_ir import Graph, load_automation
from autoforge.af_scanner import CHECKS, CODE_HINT, ERROR, StaticScanner

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "autoforge"
SCANNER = SRC / "af_scanner.py"

#: 目录里有、但不以 `Diagnostic("X")` 字面量发出的键（发出点为现读位置，行号随本批插键后重钉）：
#: - `code = "CROSS_DEP_CYCLE"` `af_scanner.py:1147`
#: - `code = "EMIT_SELF_LOOP"`   `:1152`
#: - `code = "ENTITY_DEP_CYCLE"` `:1158`
#: - `LIVE_TOKEN_REQUIRED` 常量 `:1287`，用于 `:1313`
#: - `LIVE_CONFIRM_REQUIRED` 常量 `:1288`，用于 `:1321`
#: - `LIVE_WHITELIST_REQUIRED` 常量 `:1289`，用于 `:1329`
#: - `LIVE_ENTITY_NOT_WHITELISTED` 常量 `:1290`，用于 `:1347`
_NON_LITERAL_KEYS = {
    "CROSS_DEP_CYCLE",
    "EMIT_SELF_LOOP",
    "ENTITY_DEP_CYCLE",
    "LIVE_CONFIRM_REQUIRED",
    "LIVE_ENTITY_NOT_WHITELISTED",
    "LIVE_TOKEN_REQUIRED",
    "LIVE_WHITELIST_REQUIRED",
}


def _emitted_literal_codes() -> set[str]:
    """全仓（`src/autoforge/**/*.py`）以字面量发出的诊断码集合。

    用 `ast.walk` 而不是按 `tree.body` 逐条看：`Diagnostic(...)` 大多嵌在嵌套函数与
    `if` 分支里，只扫顶层会静默得到空集（判据就假绿了）。位置参数与 `code=` 关键字两种写法都收。
    """
    codes: set[str] = set()
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            if getattr(node.func, "id", None) != "Diagnostic":
                continue
            value = None
            if node.args and isinstance(node.args[0], ast.Constant):
                value = node.args[0].value
            else:
                for kw in node.keywords:
                    if kw.arg == "code" and isinstance(kw.value, ast.Constant):
                        value = kw.value.value
            if isinstance(value, str) and value:
                codes.add(value)
    return codes


def _do_node(**extra: object) -> dict:
    node = {
        "id": "d1",
        "kind": "do",
        "adapter": "ha",
        "action": "lock.lock",
        "params": {"entity_id": "lock.front"},
    }
    node.update(extra)
    return node


def _scan(do_node: dict):
    data = {
        "ir_version": "0.2.1",
        "id": "catalog",
        "name": "目录判据",
        "version": 1,
        "mode": "single",
        "nodes": [
            {"id": "a1", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
            do_node,
            {"id": "p1", "kind": "pass"},
        ],
        "edges": [
            {"from": "a1", "to": "d1", "kind": "then"},
            {"from": "d1", "to": "p1", "kind": "then"},
        ],
    }
    return StaticScanner(graph=Graph([load_automation(data)])).scan()


# ── 通用判据：发得出的码必在目录里 ──

def test_emitted_scope_is_not_silently_empty():
    """射程自证：解析器真从仓里读出了码，否则下面两条会假绿。"""
    emitted = _emitted_literal_codes()
    assert len(emitted) >= 30, f"字面量诊断码只解析出 {len(emitted)} 枚，射程塌了"
    assert "L2_NEEDS_CANARY" in emitted, "扫描器仍在发这枚码（本批判据的前提）"


def test_every_emitted_diagnostic_code_is_registered_in_CHECKS():
    """B.8 的防复发本体：`Diagnostic("X")` 的 X 必须能在 `CHECKS` 里查到。"""
    missing = sorted(_emitted_literal_codes() - set(CHECKS))
    assert not missing, f"这些诊断码会发出却不在检查目录里：{missing}"


def test_every_emitted_diagnostic_code_carries_a_non_empty_hint():
    """第二层后果：漏登记 `CODE_HINT` ⇒ 那条 ERROR 到 Agent 手上 `hint=""`，自修正脸静默。"""
    empty = sorted(k for k in _emitted_literal_codes() if not CODE_HINT.get(k, "").strip())
    assert not empty, f"这些码发得出却没有修法文案（hint 会是空串）：{empty}"


def test_catalog_keys_are_all_emittable_from_named_inventory():
    """反向棘轮：目录里除那 7 枚非字面量发出的键之外，不许有"谁也发不出"的幽灵键。

    新增键若不红就不该红——它必须**被发出**（进 `_emitted_literal_codes`）或**进名单并写明发出点**。
    """
    ghosts = set(CHECKS) - _emitted_literal_codes() - _NON_LITERAL_KEYS
    assert not ghosts, f"CHECKS 里有无人发出的键（要么发出、要么点名写出位置）：{sorted(ghosts)}"
    assert set(CHECKS) - _emitted_literal_codes() == _NON_LITERAL_KEYS, (
        "非字面量发出的键名单漂了：核对 af_scanner.py 的 `code = \"…\"` 与 LIVE_* 常量后更新名单"
    )


# ── 本批那枚键：按名字钉死，两份目录都要有 ──

def test_l2_needs_canary_is_in_both_catalogs_by_name():
    assert "L2_NEEDS_CANARY" in CHECKS
    assert "L2_NEEDS_CANARY" in CODE_HINT
    assert "canary" in CODE_HINT["L2_NEEDS_CANARY"], "修法必须真的指向 canary，不能是一句套话"


def test_canary_diagnostic_reaches_the_agent_with_a_hint():
    """行为面：L2 + requires_confirm 无 canary ⇒ ERROR 一条，且 hint 真从目录补上（非空、与目录同文）。"""
    diag = [d for d in _scan(_do_node(requires_confirm=True)).diagnostics if d.code == "L2_NEEDS_CANARY"]
    assert len(diag) == 1, f"期望恰好一条 L2_NEEDS_CANARY，实际：{[d.code for d in _scan(_do_node(requires_confirm=True)).diagnostics]}"
    assert diag[0].level == ERROR
    assert diag[0].hint == CODE_HINT["L2_NEEDS_CANARY"] != ""
    assert "canary" in str(diag[0]), "对外那行 `__str__` 里要看得见建议"


def test_confirm_and_canary_are_two_distinct_branches():
    """两枚码各守一分支：没标 requires_confirm 只报 L2_NEEDS_CONFIRM；补齐 canary 则两条都不报。"""
    without_confirm = {d.code for d in _scan(_do_node()).diagnostics}
    assert "L2_NEEDS_CONFIRM" in without_confirm
    assert "L2_NEEDS_CANARY" not in without_confirm

    with_canary = {d.code for d in _scan(
        _do_node(requires_confirm=True, canary={"duration": "5m", "auto_rollback": True})
    ).diagnostics}
    assert "L2_NEEDS_CANARY" not in with_canary
    assert "L2_NEEDS_CONFIRM" not in with_canary


def test_catalog_size_reading_is_pinned():
    """目录枚数现读（文档里那句"43 项"的对账位）：漂了就红，逼记账同步。"""
    assert len(CHECKS) == 43, f"CHECKS 现读 {len(CHECKS)} 枚，两份架构文档那句读数要跟着改"
    assert set(CHECKS) == set(CODE_HINT), "两份目录的键集要同源：只进一份就是半张脸"
