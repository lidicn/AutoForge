"""《审计报告-稳定性与功能性缺陷》（对象 `f0184de`，2026-10-06 落进 docs/audit）两条确证的判据。

BUG-01 的第一半：`NodeExecutor.node_visits` 是**挂在长驻执行器上的无界 list**，且全仓**只写不读**
（HEAD 实测：`src/` 内仅两处命中＝定义 + `append`；`tests/` 零命中）。审计给的三个方案里本批取
"删掉"：一个从未被读取的容器，改成 `deque(maxlen=N)` 只是给死代码加个上限。它在
`scripts/check_bounded_caches.py` 的基线名单里挂着——那是"知道它无界、尚未收"的登记，
不是"已核验有界"（EXEMPT ≠ VERIFIED），基线只减不增，所以本批把它摘掉。

BUG-01 的第二半（`resume()` 段间无步数防护）**不在本批改**：
`af_scanner.py` 对"经挂起点的循环"只出 `WARNING`（`_check_static_loop`），所以轮询环是**合法可构造**
的 IR；而 `MAX_STEPS_PER_SEGMENT` 是 `run()` 的局部计数，每次 `resume()`/`resume_then()`/`timeout()`
回 `run()` 都从 0 重算 ⇒ 单段有界、单实例累计无界。封顶值与超限动作（fail / cancel / 只告警）
会直接改变现网长命实例的存活判定，属产品裁定 ⇒ 投 DCD（含实测复现读数），AF 不自签一个魔法数。

BUG-02：`_fix_loop` 的 `budget = max_attempts or self.max_fix_attempts` 把显式 `0`
（"跑一次不重试"）悄悄换成默认预算。当前无调用方传参 ⇒ 未触发，但这是个等待被调用的陷阱。

反例族：`node_visits` 回到 src（红）、执行器热路径再出现任何 `self.X.append(`（红）、基线里
留着已删容器的名字（红，且这条同时被门禁 D 腿管着）、跑 200 段后执行器容器长度发生变化（红）、
`max_attempts=0` 退回默认预算（红）、以及 **CONTROL**（`max_attempts=None` 走默认、不传参照旧）。
"""

from __future__ import annotations

import ast
import re
from collections import deque
from pathlib import Path

import pytest

from autoforge import af_orchestrator
from autoforge.af_orchestrator import ComposeSession, Orchestrator
from autoforge.af_runtime import build_runtime
from autoforge.af_state import InMemoryStateProvider

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "autoforge"
GATE = ROOT / "scripts" / "check_bounded_caches.py"

GRAPH = {
    "ir_version": "0.2.1",
    "id": "demo",
    "name": "demo",
    "version": 1,
    "mode": "single",
    "nodes": [
        {"id": "o", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
        {"id": "d", "kind": "do", "adapter": "mock", "action": "light.turn_on", "params": {"entity_id": "light.x"}},
    ],
    "edges": [{"from": "o", "to": "d", "kind": "then"}],
}


def _py_files() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def _literal_frozenset(name: str) -> set[str]:
    """从门禁脚本里按 AST 取一个字面量集合（不 import 被测码）。"""
    tree = ast.parse(GATE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    call = node.value
                    if isinstance(call, ast.Call):
                        call = call.args[0]
                    return {e.value for e in call.elts if isinstance(e, ast.Constant)}
    raise AssertionError(f"门禁脚本里读不到 {name}（射程塌了，不是没问题）")


# ── BUG-01 第一半：死代码 + 无界容器已摘干净 ────────────────────────────────


def test_node_visits_is_gone_from_the_entire_source_tree():
    hits = [p.name for p in _py_files() if "node_visits" in p.read_text(encoding="utf-8")]
    assert hits == [], f"节点访问累积容器回来了：{hits}"


def test_executor_hot_path_has_no_self_level_appending_container():
    """钉的是形状而不是名字：热路径上再长出任何一个 `self.<容器>.append(` 都判红。

    审计的第一半之所以成立，是因为"每次进节点就往执行器级容器里塞一条"这个动作本身
    没有任何生命周期。换个名字（`_visited`/`_trace_ids`）绕过判据是最可能的回归方式。
    """
    src = (SRC / "af_executor.py").read_text(encoding="utf-8")
    offenders = re.findall(r"self\.(\w+)\.append\(", src)
    assert offenders == [], f"af_executor.py 又出现执行器级追加：{offenders}"


def test_gate_baseline_no_longer_carries_the_removed_container():
    baseline = _literal_frozenset("BASELINE")
    assert baseline, "基线读成空＝射程塌了（门禁本体不许这样绿）"
    assert "af_executor.py::NodeExecutor.node_visits" not in baseline
    assert "af_executor.py::NodeExecutor.pending_asks" in baseline, "只摘该摘的那一条"


def test_running_200_segments_does_not_change_any_executor_container(tmp_path):
    """行为侧：200 段跑完之后，执行器上不得多出/增长任何容器。

    驱动方式刻意走 `spawn + run` 而不是 `runtime.emit`：后者被 EventBus 的节流按实体去重，
    20 次 emit 实测只放行 **1** 次（`MockAdapter.calls == 1`），拿它当"跑了 200 段"就是假绿。
    所以这里把"每段真的执行过 do 节点"写成断言（`calls` 增量必须等于段数），防空洞。

    `pending_asks` 允许存在（按 instance_id 建、resume 时 pop），但它必须**不增长**——这条同时
    把"执行器级 = 跨实例共享"这个作用域错误钉住：新加的累积只可能长在这里。
    """
    from autoforge.af_ir import load_graph

    states = InMemoryStateProvider()
    states.set_state("binary_sensor.m", "off")
    runtime = build_runtime(load_graph(GRAPH), states=states)
    ex = runtime.executor
    auto = list(load_graph(GRAPH))[0]
    mock = ex.adapters.get("mock")

    def sizes() -> dict[str, int]:
        return {
            k: len(v)
            for k, v in vars(ex).items()
            if isinstance(v, (list, dict, set, deque))
        }

    before, calls_before = sizes(), len(mock.calls)
    for _ in range(200):
        ex.run(runtime.instances.spawn(auto))
    after, calls_after = sizes(), len(mock.calls)

    assert calls_after - calls_before == 200, (
        f"200 段里 do 节点只被执行 {calls_after - calls_before} 次＝这条判据在空转"
    )
    assert after == before, f"执行器级容器在段之间增长：{before} -> {after}"
    assert not any(isinstance(v, list) for v in vars(ex).values()), "长驻执行器上不许挂 list"


# ── BUG-02：`max_attempts=0` 必须真的是 0 ──────────────────────────────────


@pytest.fixture()
def always_failing(monkeypatch):
    """把 `normalize_output` 钉成"永远一条 UNKNOWN"，FIXERS 里没有它的修复器 ⇒ 只能耗预算。"""
    issue = af_orchestrator.BuildIssue(af_orchestrator.ErrorCode.UNKNOWN, None, "永远修不好")
    monkeypatch.setattr(af_orchestrator, "normalize_output", lambda stage, raw: [issue])
    calls: list[int] = []

    def run() -> dict:
        calls.append(1)
        return {"ok": False, "errors": ["boom"]}

    return calls, run


def _fix(orch: Orchestrator, run, max_attempts):
    session = ComposeSession(session_id="s-1")
    raw, issues = orch._fix_loop(session, "build", run, max_attempts=max_attempts)
    return session, raw, issues


def test_zero_means_one_attempt_not_the_default(always_failing):
    calls, run = always_failing
    orch = Orchestrator(object(), None, max_fix_attempts=3)
    session, _raw, issues = _fix(orch, run, 0)
    assert len(calls) == 1, f"max_attempts=0 却被换成默认预算：跑了 {len(calls)} 次"
    assert issues and session.attempts["build"] == 1


def test_control_default_still_applies_when_argument_is_none(always_failing):
    calls, run = always_failing
    orch = Orchestrator(object(), None, max_fix_attempts=3)
    _fix(orch, run, None)
    assert len(calls) == 3, "None 才该回落默认；这一条绿才证明上一条的红来自计数器而不是偶然"


@pytest.mark.parametrize("budget,expected", [(1, 1), (2, 2), (5, 5)])
def test_explicit_budget_is_honoured_verbatim(always_failing, budget, expected):
    calls, run = always_failing
    orch = Orchestrator(object(), None, max_fix_attempts=3)
    _fix(orch, run, budget)
    assert len(calls) == expected


def test_success_on_first_run_returns_before_any_retry(always_failing, monkeypatch):
    """反例的另一头：预算为 0 时"一次就过"必须照常返回空 issues（收紧不许变成"永不修复"）。"""
    monkeypatch.setattr(af_orchestrator, "normalize_output", lambda stage, raw: [])
    calls, run = always_failing
    orch = Orchestrator(object(), None, max_fix_attempts=3)
    _session, _raw, issues = _fix(orch, run, 0)
    assert issues == [] and len(calls) == 1
