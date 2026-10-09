"""裁定 20261009 §二 2.2：`requires_confirm` 节点缺"拒绝出口"时的编译期 WARN。

背景（裁定原文）：Q2 裁的是**甲**——人答"不要"/无人应答时，运行期**照既有边纪律选路**
（写了 `no`/`on_timeout`/`default` 就沿边走，没写就 `done`），与 `ask` 被答"不要"且无 `no` 边
时的行为**完全一致**（`af_executor.py:333` 同一处 `pick_edge`）。所以"落 done"不是缺陷。
真正的缺口是另一头：作者写了 `requires_confirm` 却没写拒绝出口时，这条走向**只在运行期静默发生**，
图里读不出来 ⇒ 裁定追加一条 Scanner WARN，把"边不完整"从运行期挪到编译期喊出来。

判据射程（缺一格就是这条 WARN 白建）：
1. 该码**注册进 `CHECKS` + `CODE_HINT`**——裁定点名不许再造 `L2_NEEDS_CANARY` 那种"发诊断却不注册"
   的第二枚缺口（不注册 ⇒ `Diagnostic.__post_init__` 取不到 hint ⇒ 到 Agent 手里是 `hint=""`）。
2. 真发得出来，且**恰好一条**、级别是 `WARNING`（不是 ERROR——甲裁定不拦发布）。
3. 三枚出口名各自都能压掉诊断（少一枚 ⇒ 合法 IR 被误喊；多一枚 ⇒ 静默豁免回来了）。
4. 没标 `requires_confirm` 的节点一律不喊（否则是给所有 `do` 加税）。
5. WARN 不进 `ScanResult.ok` 的判红（`.ok` 只看 errors ⇒ 演练场/现网都不被这条诊断卡住）。
6. 编译期认的三枚出口名 = 运行期拒绝分支真正会走的那三枚词汇（跟着 `af_executor.py` 的
   `pick_edge({kind, "default"})` / `resume(instance, "on_timeout")` / `{"no", "default"}` 钉）：
   执行器若新增/改名拒绝词汇，这条腿先红，而不是让 WARN 悄悄落后于运行期。

反例族（红才说明判据有牙）：删 CHECKS 项、删 CODE_HINT 项、把 `no` 从出口集里摘掉、
把级别改成 ERROR、给整个方法加 `return`（不发诊断）、执行器拒绝词汇改名。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from autoforge.af_ir import Graph, load_automation
from autoforge.af_ir.models import EDGE_KINDS
from autoforge.af_scanner import CHECKS, CODE_HINT, Diagnostic, StaticScanner
from autoforge.af_scanner import WARNING

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "autoforge"

CODE = "CONFIRM_WITHOUT_DENY_PATH"
#: 裁定原文点名的三枚出口（顺序即 §二 2.2 原文顺序，改名/增删都会撞上第 6 条腿）
DENY_KINDS = {"no", "on_timeout", "default"}


def _ir(do_node: dict, extra_edges: list[dict] | None = None) -> dict:
    """`on → do → pass` 骨架；`extra_edges` 用来挂拒绝出口。"""
    edges = [
        {"from": "a1", "to": "d1", "kind": "then"},
        {"from": "d1", "to": "p1", "kind": "then"},
    ]
    if extra_edges:
        edges.extend(extra_edges)
    return {
        "ir_version": "0.2.1",
        "id": "test",
        "name": "测试",
        "version": 1,
        "mode": "single",
        "nodes": [
            {"id": "a1", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
            do_node,
            {"id": "p1", "kind": "pass"},
            {"id": "x1", "kind": "pass"},
        ],
        "edges": edges,
    }


def _confirm_do(**kw: object) -> dict:
    node = {
        "id": "d1", "kind": "do", "adapter": "ha",
        "action": "light.turn_on", "params": {"entity_id": "light.study"},
        "requires_confirm": True,
    }
    node.update(kw)
    return node


def _scan(data: dict):
    return StaticScanner(graph=Graph([load_automation(data)])).scan()


def _hits(result) -> list[Diagnostic]:
    return [d for d in result.diagnostics if d.code == CODE]


def _confirm_exit_set() -> set[str]:
    """从 `_check_confirm_exit` 的 AST 里取它认的出口集合（不靠散文正则数引号）。"""
    tree = ast.parse((SRC / "af_scanner.py").read_text(encoding="utf-8"))
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef) and fn.name == "_check_confirm_exit":
            for node in ast.walk(fn):
                # `if kinds & {"no", "on_timeout", "default"}:` —— 集合在 BinOp 右操作数上
                if (isinstance(node, ast.Set) and node.elts
                        and all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts)):
                    return {e.value for e in node.elts}
    raise AssertionError("读不到 _check_confirm_exit 的出口集合字面量（射程塌了，不是没问题）")


class TestRegistration:
    """裁定点名：必须注册进 CHECKS，不许造第二枚"发诊断却不注册"。"""

    def test_code_is_in_both_catalogs_by_name(self):
        assert CODE in CHECKS, f"{CODE} 未注册进 CHECKS（Agent 侧点不到这条诊断的修法）"
        assert CODE in CODE_HINT, f"{CODE} 未注册进 CODE_HINT（hint 会静默塌成空串）"

    def test_hint_is_non_empty_and_bilingual(self):
        hint = CODE_HINT[CODE].strip()
        assert hint, "CODE_HINT 值是空白串——注册了等于没注册"
        assert "no" in hint and "on_timeout" in hint and "default" in hint, \
            f"hint 没把三枚出口名写给 Agent：{hint!r}"


class TestEmission:
    def test_warns_exactly_once_when_no_deny_exit(self):
        result = _scan(_ir(_confirm_do()))
        hits = _hits(result)
        assert len(hits) == 1, f"应恰好一条 {CODE}，实际 {[d.level for d in hits]}：{result.render()}"
        diag = hits[0]
        assert diag.level == WARNING, f"Q2 裁甲＝不拦发布，级别必须是 WARNING，实际 {diag.level}"
        assert diag.node_id == "d1"
        # 注册的第二面：真发出来的这一条必须自带 hint，且等于目录里的原文（不是空串、不是别码）
        assert diag.hint == CODE_HINT[CODE] != ""
        assert CODE in str(diag)

    @pytest.mark.parametrize("kind", ["no", "on_timeout", "default"])
    def test_each_deny_exit_suppresses_the_warning(self, kind):
        result = _scan(_ir(_confirm_do(), [{"from": "d1", "to": "x1", "kind": kind}]))
        assert _hits(result) == [], f"写了 {kind} 出口还喊 {CODE}＝误喊，合法 IR 被加税"

    def test_exit_vocabulary_is_expressible_in_ir(self):
        # 三枚出口名必须是 IR 真实词汇，否则这条 WARN 要求作者写出一条进不了图的边
        assert DENY_KINDS <= set(EDGE_KINDS)

    def test_compiler_vocabulary_matches_executor_denypath(self):
        # 编译期认的出口集
        assert _confirm_exit_set() == DENY_KINDS
        # 运行期拒绝分支真正会走的三枚词汇（改名/增删 ⇒ 这条先红，WARN 不会落后于执行器）
        ex = (SRC / "af_executor.py").read_text(encoding="utf-8")
        assert re.search(r"pick_edge\(node\.id,\s*\{kind, \"default\"\}\)", ex), \
            "af_executor 的拒绝选路边变了，§二 2.2 的三枚出口名要跟着重核"
        assert 'resume(instance, "on_timeout")' in ex, "超时出口 on_timeout 不再是执行器的超时词汇"
        assert '{"no", "default"}' in ex, "拒绝出口 no 不再是执行器的拒绝词汇"

    def test_no_warning_without_requires_confirm(self):
        node = {"id": "d1", "kind": "do", "adapter": "ha",
                "action": "light.turn_on", "params": {"entity_id": "light.study"}}
        result = _scan(_ir(node))
        assert _hits(result) == [], "未标 requires_confirm 也被喊＝给所有 do 节点加税"

    def test_yes_exit_alone_still_warns(self):
        # 只有 yes（答"要"有路）＝答"不要"仍静默落 done ⇒ 该喊。
        # 这也是 Q1 裁甲的口径边界：预演档可以跳过挂起闸，但**编译期这条边不完整的诊断不跟着跳**，
        # 否则预演练的图和真机练的图不是同一份诊断口径。
        result = _scan(_ir(_confirm_do(), [{"from": "d1", "to": "x1", "kind": "yes"}]))
        assert len(_hits(result)) == 1, f"只有 yes 出口时仍应喊 {CODE}"


class TestDoesNotBlock:
    """Q2 裁甲的另一面：这条诊断不拦发布（`ScanResult.ok` 只看 errors）。"""

    def test_warning_keeps_scan_ok(self):
        result = _scan(_ir(_confirm_do()))
        assert len(_hits(result)) == 1
        assert not any(d.level == "error" and d.code == CODE for d in result.diagnostics)
        assert all(d.code != CODE for d in result.errors)
        assert any(d.code == CODE for d in result.warnings)
