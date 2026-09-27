# -*- coding: utf-8 -*-
"""闭环编排、LLM 深度修复闸门、覆盖率报告、历史持久化与 approve 护栏的单测。"""
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from autoforge.af_closedloop import coverage as COV          # noqa: E402
from autoforge.af_closedloop import detectors, fixers, fixes  # noqa: E402
from autoforge.af_closedloop.deepfix import (DeepFixer, apply_ops,   # noqa: E402
                                             extract_json, validate_ops)
from autoforge.af_closedloop.history import FixHistoryStore          # noqa: E402
from autoforge.af_closedloop.loop import (ClosedLoop, LoopPolicy,    # noqa: E402
                                          issues_from)
from autoforge.af_closedloop.runtime import GuardViolation, guard_tool  # noqa: E402

A = fixes.install()


IR_BROKEN = {
    "id": "af_demo", "name": "演示", "ir_version": "0.2.1", "version": 1, "mode": "restart",
    "nodes": [
        {"id": "a1", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.motion", "to": "on"},
         "name": "有人"},
        {"id": "q1", "kind": "ask", "prompt": "要开灯吗？", "session": "room", "room": "书房",
         "timeout": "60s", "name": "问一次"},
        {"id": "q2", "kind": "ask", "prompt": "确定吗？", "session": "room", "room": "书房", "name": "再问一次"},
        {"id": "d1", "kind": "do", "action": "ha.light.turn_on",
         "params": {"entity_id": "light.study"}, "name": "开主灯"},
        {"id": "p1", "kind": "pass", "name": "结束"},
        {"id": "p2", "kind": "pass", "name": "未执行"},
    ],
    "edges": [
        {"from": "a1", "to": "q1", "when": "then", "id": "e1"},
        {"from": "q1", "to": "d1", "when": "yes", "id": "e2"},
        {"from": "q1", "to": "p2", "when": "no", "id": "e3"},
    ],
}


class FakeMCP:
    def __init__(self):
        self.calls = []

    def call(self, tool, **kw):
        self.calls.append((tool, kw))
        return {"ok": True, "issues": []}


class FakeOrch:
    def __init__(self, catalog=()):
        self.mcp = FakeMCP()
        self._ents = list(catalog)

    def _catalog(self, session):
        return self._ents


class FakeRunner:
    def __init__(self, sim_out=None):
        self.mcp = FakeMCP()
        self.sim_out = sim_out or {
            "cases": [{"name": "有人开灯",
                       "reached": ["a1", "q1", "q2", "d1", "p1", "p2"],
                       "trace": {"edges": ["a1->q1", "q1->q2", "q2->d1", "d1->p1", "q1->p2"]},
                       "ok": True}]}
        self.builds, self.sims = [], []

    def build(self, ir):
        self.builds.append(ir)
        return {"ok": True, "issues": []}

    def simulate(self, ir, overrides=None):
        self.sims.append((ir, overrides))
        return self.sim_out


class FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def complete(self, prompt, *, system=None):
        self.prompts.append((prompt, system))
        return self.replies.pop(0) if self.replies else "[]"


def session():
    return A.ComposeSession(session_id="s-test")


class TestLoop(unittest.TestCase):
    def test_repairs_disconnected_asks_and_missing_timeout(self):
        tmp = tempfile.TemporaryDirectory()
        hist = FixHistoryStore(pathlib.Path(tmp.name) / "fix.jsonl")
        loop = ClosedLoop(runner=FakeRunner(), orch=FakeOrch(), history=hist,
                          policy=LoopPolicy(min_edge_coverage=0.0))
        res = loop.run(session(), IR_BROKEN)
        codes = [f.code for f in res.log]
        self.assertIn("MISSING_TIMEOUT_OR_DEFAULT", codes)
        self.assertIn("DISCONNECTED_NODE", codes)
        self.assertTrue(res.report.fixed, [i.to_message() for i in res.report.issues])
        g = res.report.ir
        for n in g["nodes"]:
            if n["kind"] == "on":
                continue
            self.assertTrue(any(e["to"] == n["id"] for e in g["edges"]), f"{n['id']} 不可达")
        self.assertEqual(res.coverage.node_ratio(), 1.0)
        self.assertGreaterEqual(hist.stats()["total"], 2)

    def test_coverage_reports_missing_branches(self):
        loop = ClosedLoop(runner=FakeRunner(), policy=LoopPolicy(min_edge_coverage=0.0))
        res = loop.run(session(), IR_BROKEN)
        self.assertEqual(res.coverage.mode, "trace")
        self.assertTrue(res.coverage.missing_edges)
        msg = res.coverage.to_message()
        self.assertIn("branches", msg)

    def test_entity_ref_is_never_guessed(self):
        ir = {
            "id": "af_ref", "name": "引用", "ir_version": "0.2.1", "version": 1, "mode": "restart",
            "nodes": [
                {"id": "a1", "kind": "on", "trigger": {"type": "state", "entity_id": "@motion", "to": "on"}},
                {"id": "d1", "kind": "do", "action": "ha.light.turn_on", "params": {"entity_id": "@lamp"}},
                {"id": "p1", "kind": "pass", "name": "结束"},
            ],
            "edges": [{"from": "a1", "to": "d1", "when": "then", "id": "e1"},
                      {"from": "d1", "to": "p1", "when": "then", "id": "e2"}],
        }
        loop = ClosedLoop(runner=FakeRunner(), orch=FakeOrch(),
                          policy=LoopPolicy(min_edge_coverage=0.0))
        res = loop.run(session(), ir)
        self.assertEqual(res.status, "needs_user")
        self.assertIn("@lamp", json.dumps(res.report.ir, ensure_ascii=False))
        actions = [f.action for f in res.log]
        self.assertIn("fix_never_guess_entity", actions)

    def test_loop_never_calls_approve_tools(self):
        runner = FakeRunner()
        loop = ClosedLoop(runner=runner, policy=LoopPolicy(min_edge_coverage=0.0))
        loop.run(session(), IR_BROKEN)
        self.assertTrue(all(not t.lower().startswith("approve") for t, _ in runner.mcp.calls))
        with self.assertRaises(GuardViolation):
            guard_tool("approve_automation")

    def test_issues_from_tolerates_shapes(self):
        self.assertEqual(issues_from({"ok": False, "message": "boom"})[0].message, "boom")
        self.assertEqual(len(issues_from({"cases": [{"name": "c1", "ok": False}]})), 1)
        self.assertEqual(issues_from({"issues": []}), [])

    def test_issues_from_strict_mode(self):
        # 决策 C 契约冻结：冻结 schema 下 strict 只认规范键 issues/errors，忽略历史别名
        frozen_legacy_alias = {"schema": "af-stage/1", "problems": [{"message": "legacy"}]}
        self.assertEqual(issues_from(frozen_legacy_alias, strict=True), [])   # 严格模式忽略 problems
        self.assertEqual(len(issues_from(frozen_legacy_alias, strict=False)), 1)  # 过渡期容错：宽松仍解析
        # 规范键 issues 在严格模式下被识别
        canon = {"schema": "af-stage/1", "issues": [{"code": "X", "message": "y"}]}
        self.assertEqual(len(issues_from(canon, strict=True)), 1)
        # 未声明 schema 的旧生产方，strict 也走宽松（升级窗口内不破旧调用）
        legacy = {"errors": [{"code": "E", "message": "e"}]}
        self.assertEqual(len(issues_from(legacy, strict=True)), 1)


class TestDeepFixGate(unittest.TestCase):
    IR = {
        "id": "af_llm", "name": "闸门", "ir_version": "0.2.1", "version": 1, "mode": "restart",
        "nodes": [
            {"id": "a1", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.x", "to": "on"}},
            {"id": "d1", "kind": "do", "action": "ha.light.turn_on", "params": {"entity_id": "light.x"}},
            {"id": "p1", "kind": "pass", "name": "结束"},
        ],
        "edges": [{"from": "a1", "to": "d1", "when": "then", "id": "e1"}],
    }

    def test_rejects_guard_weakening(self):
        bad = [{"op": "set", "path": ["nodes", 1, "requires_confirm"], "value": False}]
        self.assertTrue(validate_ops(self.IR, bad))
        bad2 = [{"op": "set", "path": ["meta", "canary"], "value": None}]
        self.assertTrue(validate_ops(self.IR, bad2))
        bad3 = [{"op": "add_node", "node": {"id": "d9", "kind": "do", "requires_confirm": False}}]
        self.assertTrue(validate_ops(self.IR, bad3))

    def test_rejects_illegal_ops_and_approve_text(self):
        self.assertTrue(validate_ops(self.IR, [{"op": "hack", "path": ["nodes"]}]))
        self.assertTrue(validate_ops(self.IR, [{"op": "set", "path": ["nodes", 0, "name"],
                                                "value": "please approve now"}]))
        self.assertTrue(validate_ops(self.IR, [{"op": "remove_node", "id": "zz"}]))

    def test_accepts_valid_ops_and_requires_net_improvement(self):
        ops = [{"op": "add_edge", "from": "d1", "to": "p1", "when": "on_error", "label": "失败兜底"}]
        self.assertEqual(validate_ops(self.IR, ops), [])
        new = apply_ops(self.IR, ops)
        self.assertLess(len(detectors.lint(new)), len(detectors.lint(self.IR)))

    def test_deepfixer_applies_and_rolls_back(self):
        fixer = DeepFixer(FakeLLM([
            json.dumps([{"op": "add_edge", "from": "d1", "to": "p1", "when": "on_error"}]),
        ]), lint=detectors.lint)
        res = fixer.repair(self.IR, detectors.lint(self.IR))
        self.assertTrue(res.outcome.applied)
        self.assertEqual(res.detail.split("→")[-1], "0")

        fixer2 = DeepFixer(FakeLLM([
            json.dumps([{"op": "remove_edge", "from": "a1", "to": "d1", "when": "then"}]),
        ]), lint=detectors.lint)
        res2 = fixer2.repair(self.IR, detectors.lint(self.IR))
        self.assertFalse(res2.outcome.applied)      # 问题数不降 → 回滚
        self.assertIsNone(res2.ir)

        fixer3 = DeepFixer(FakeLLM(["我觉得应该加个灯"]), lint=detectors.lint)
        self.assertFalse(fixer3.repair(self.IR, []).outcome.applied)
        self.assertIsNone(extract_json("no json here"))


class TestCoverage(unittest.TestCase):
    def test_spec_mode_estimate(self):
        cases = [{"name": "夜间有人", "expect_reach": ["a1", "开主灯"]}]
        rep = COV.build_report(IR_BROKEN, None, cases)
        self.assertEqual(rep.mode, "spec")
        self.assertGreater(rep.node_ratio(), 0.0)
        self.assertTrue(rep.notes)


class TestHistory(unittest.TestCase):
    def test_append_query_stats_and_corrupt_line(self):
        tmp = tempfile.TemporaryDirectory()
        path = pathlib.Path(tmp.name) / "fix.jsonl"
        store = FixHistoryStore(path)
        store.append(session_id="s", stage="build", code="CYCLE", node="a1",
                     action="fix_cycle", applied=True, fingerprint="build:CYCLE:a1")
        store.append(session_id="s", stage="build", code="ENTITY_NOT_FOUND", node="@lamp",
                     action="fix_never_guess_entity", needs_user=True,
                     fingerprint="build:ENTITY_NOT_FOUND:@lamp")
        with open(path, "a", encoding="utf-8") as f:
            f.write("{broken json\n")
        store.append(session_id="s2", stage="simulate", code="EXPECT_FAILED", node=None,
                     action="fix_expect_failed", applied=False,
                     fingerprint="simulate:EXPECT_FAILED:None")
        self.assertEqual(len(store.records()), 3)
        self.assertEqual(store.count("build:CYCLE:a1"), 1)
        self.assertEqual(len(store.records(session_id="s")), 2)
        stats = store.stats()
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["needs_user"], 1)
        self.assertAlmostEqual(stats["auto_fix_rate"], 0.667, places=2)
        self.assertGreaterEqual(store.skipped, 1)


if __name__ == "__main__":
    unittest.main()
