"""状态源扇出门禁必须"能变红"（铁律 #8）——本仓自己就在这条上翻过车。

写第一版时 `_attr_path()` 把根名解析错了（`runtime.executor.states` 解成 root=`executor`），
门禁对 HEAD 报绿、对**故意删掉一行的变异也报绿**——两次变异都逃过去，靠手敲变异脚本才发现。
所以这里钉的不只是判据语义，还有"这扇门不会又变成一堵墙上的假洞"：
`test_a_lone_top_level_states_assignment_is_flagged` 就是那次假绿的形状。
"""
from __future__ import annotations

import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_states_fanout.py"

FULL_SWAP = """
def _make(graph, provider):
    runtime = build_runtime(graph)
    runtime.states = provider
    runtime.instances.states = provider
    runtime.scheduler.states = provider
    runtime.executor.states = provider
    return runtime
"""


def _module():
    spec = importlib.util.spec_from_file_location("check_states_fanout", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _findings(tmp_path: pathlib.Path, body: str) -> list[str]:
    (tmp_path / "sample.py").write_text(body, encoding="utf-8")
    return _module().check(tmp_path)[0]


def test_complete_swap_is_clean(tmp_path):
    assert _findings(tmp_path, FULL_SWAP) == []


def test_missing_executor_line_is_red(tmp_path):
    """少抄一行是本门的本职：不报错、不崩，只让 executor 继续读旧状态源。"""
    body = FULL_SWAP.replace("    runtime.executor.states = provider\n", "")
    findings = _findings(tmp_path, body)
    assert len(findings) == 1
    assert "executor.states" in findings[0]


def test_a_lone_top_level_states_assignment_is_flagged(tmp_path):
    """第一版的假绿形状：根名解析错时这种写法会悄悄判绿。"""
    body = """
def _make(graph, provider):
    runtime = build_runtime(graph)
    runtime.states = provider
    return runtime
"""
    findings = _findings(tmp_path, body)
    assert len(findings) == 1
    assert "instances.states" in findings[0] and "executor.states" in findings[0]


def test_two_different_sources_are_judged_separately(tmp_path):
    """同一函数换两次源（真机 provider / 仿真 seed）各自都得四条齐。"""
    body = """
def _make(graph, provider, seeded):
    if provider:
        runtime = build_runtime(graph)
        runtime.states = provider
        runtime.instances.states = provider
        runtime.scheduler.states = provider
        runtime.executor.states = provider
    else:
        runtime = build_runtime(graph)
        runtime.states = seeded
        runtime.instances.states = seeded
    return runtime
"""
    findings = _findings(tmp_path, body)
    assert len(findings) == 1
    assert "seeded" in findings[0]


def test_construction_time_injection_is_not_a_swap(tmp_path):
    """`build_runtime(graph, states=...)` 由 `Runtime.__post_init__` 负责扇出，不该判红。"""
    body = """
def _make(graph, ha):
    runtime = build_runtime(graph, states=ha)
    runtime.adapters.register(ha)
    return runtime
"""
    assert _findings(tmp_path, body) == []


def test_non_runtime_objects_are_out_of_scope(tmp_path):
    """`InstanceManager.self.states` 这类同名属性不归本门管，否则满仓误报。"""
    body = """
class InstanceManager:
    def __init__(self, state_provider):
        self.states = state_provider
"""
    assert _findings(tmp_path, body) == []


def test_the_repo_itself_passes():
    """判据对真源码成立才算交付；真源码哪天少抄一行，这里就红。"""
    findings, n_files = _module().check(ROOT / "src")
    assert n_files >= 90
    assert findings == []
