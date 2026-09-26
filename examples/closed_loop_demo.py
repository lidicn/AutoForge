# -*- coding: utf-8 -*-
"""端到端演示：草稿 → 图 → 复现 B2 断链 → 闭环自修复 → 覆盖率 + 修复历史。

    python examples/closed_loop_demo.py
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from autoforge.af_closedloop import detectors, fixes                # noqa: E402
from autoforge.af_closedloop.deepfix import DeepFixer               # noqa: E402
from autoforge.af_closedloop.history import FixHistoryStore         # noqa: E402
from autoforge.af_closedloop.loop import ClosedLoop, LoopPolicy     # noqa: E402

A = fixes.install()      # 只读仓：进程内补丁，源文件不动


class DemoRunner:
    """真实环境换成 OrchestratorRunner(orch, session)。"""

    def __init__(self):
        self.mcp = None

    def build(self, ir):
        return {"ok": True, "issues": []}

    def simulate(self, ir, overrides=None):
        ids = [n["id"] for n in ir["nodes"]]
        return {"cases": [{"name": "有人开灯", "reached": ids,
                           "trace": {"edges": [f"{e['from']}->{e['to']}" for e in ir["edges"]]},
                           "ok": True}]}


class DemoLLM:
    def complete(self, prompt, *, system=None):
        return json.dumps([{"op": "add_edge", "from": "d1", "to": "p1",
                            "when": "on_error", "label": "失败兜底"}])


def main():
    # 1) 听懂人话 → 草稿（B1/B4/B5 已在 merge_intent/default_prompt 里修好）
    draft = A.AutomationDraft()
    A.merge_intent(draft, {
        "area": "书房",
        "triggers": [{"type": "state", "entity_id": "@motion", "to": "on", "name": "检测到有人"}],
        "asks": [{"prompt": "要开主灯吗？"}, {"prompt": "确定现在开吗？"}],
        "actions": [{"action": "ha.light.turn_on", "params": {"entity_id": "@lamp"}}],
        "refs": [{"key": "@motion", "name": "书房人体传感器", "role": "trigger", "entity_id": "binary_sensor.motion"},
                 {"key": "@lamp", "name": "书房主灯", "role": "action", "entity_id": "light.study"}],
    })
    print("追问文案:", A.default_prompt(draft))

    # 2) 语义 → 拓扑（B2 断链已在 build_graph 内自愈）
    ir = A.build_graph(draft)
    ir["nodes"] = [dict(n, **({"timeout": n.get("timeout")} if n["kind"] != "ask" else {}))
                   for n in ir["nodes"]]
    print("体检问题:", [i.to_message() for i in detectors.lint(ir)])

    # 3) 闭环：确定性 auto-fix → LLM 深度修复（限次）→ 仿真 → 覆盖率 → 历史
    session = A.ComposeSession(session_id="demo-1")
    history = FixHistoryStore(pathlib.Path(__file__).resolve().parents[1] / ".af" / "fix_history.jsonl")
    loop = ClosedLoop(runner=DemoRunner(), deep_fixer=DeepFixer(DemoLLM(), lint=detectors.lint),
                      history=history, policy=LoopPolicy(min_edge_coverage=0.5))
    result = loop.run(session, ir)

    print(json.dumps(result.to_message(session.session_id), ensure_ascii=False, indent=2))
    print("修复历史:", history.stats())


if __name__ == "__main__":
    main()
