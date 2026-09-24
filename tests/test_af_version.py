"""af_version 单测（新增测试面；不触碰现有 tests/test_af_version.py）。

覆盖 §8 验收逐条 + 边界 / 异常路径；断言落到底层数据（IR 内容、字段级 changed、
parent 链、文件布局），不做 "result['ok'] is True" 式空断言。
"""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from autoforge.af_version import (
    LABELS,
    Version,
    VersionError,
    VersionManager,
    diff,
    set_default_store,
)


# --------------------------------------------------------------------------- #
# 桩与 IR 构造
# --------------------------------------------------------------------------- #

class _RecordingWriter:
    """ir_writer 桩：记录写回的 (automation_id, ir)，可注入失败。"""

    def __init__(self, fail: bool = False) -> None:
        self.calls = []
        self.fail = fail

    def __call__(self, automation_id, ir):
        self.calls.append((str(automation_id), copy.deepcopy(ir)))
        if self.fail:
            raise RuntimeError("ir_writer boom")


class _RecordingClock:
    """时钟桩：每次取时间前进 step 秒。"""

    def __init__(self, start: float = 1000.0, step: float = 1.0) -> None:
        self.value = float(start)
        self.step = float(step)

    def now(self) -> float:
        current = self.value
        self.value += self.step
        return current


class _Plan:
    """DeployPlan 桩（duck typing：只用 ir / proposal 两个属性）。"""

    def __init__(self, ir, proposal=None) -> None:
        self.ir = ir
        self.proposal = proposal


def make_ir(aid: str = "auto_a", nodes=None, edges=None, trigger=None) -> dict:
    doc = {
        "id": aid,
        "name": aid,
        "trigger": trigger or {"kind": "manual"},
        "nodes": nodes if nodes is not None else {
            "n1": {"id": "n1", "kind": "do", "action": "switch.on", "entities": ["light.kitchen"]}
        },
    }
    if edges is not None:
        doc["edges"] = edges
    return doc


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def manager(self, irs=None, writer=None, clock=None, **kw) -> VersionManager:
        # ir_provider 直接持有调用方 dict 引用，便于测试中途换 IR
        return VersionManager(
            root=self.root,
            ir_provider=irs if irs is not None else {},
            ir_writer=writer,
            clock=clock,
            **kw,
        )


class VersionModelTests(_Base):
    def test_version_json_roundtrip(self) -> None:
        irs = {"auto_a": make_ir()}
        mgr = self.manager(irs, clock=_RecordingClock())
        v = mgr.snapshot("auto_a")
        again = Version.from_json(v.to_json())
        self.assertEqual(again, v)
        self.assertEqual(again.ir_snapshot, irs["auto_a"])


class SnapshotTests(_Base):
    def test_snapshot_stores_full_ir_snapshot(self) -> None:
        irs = {"auto_a": make_ir()}
        mgr = self.manager(irs, clock=_RecordingClock())
        v = mgr.snapshot("auto_a")
        self.assertIsInstance(v, Version)
        self.assertEqual(v.automation_id, "auto_a")
        self.assertEqual(v.ir_snapshot, irs["auto_a"])          # 完整 IR，非摘要

    def test_snapshot_is_deep_copy_of_source_ir(self) -> None:
        irs = {"auto_a": make_ir()}
        mgr = self.manager(irs, clock=_RecordingClock())
        v = mgr.snapshot("auto_a")
        irs["auto_a"]["nodes"]["n1"]["action"] = "switch.off"
        irs["auto_a"]["nodes"]["n2"] = {"id": "n2", "kind": "ask"}
        self.assertEqual(v.ir_snapshot["nodes"]["n1"]["action"], "switch.on")
        self.assertNotIn("n2", v.ir_snapshot["nodes"])

    def test_snapshot_first_version_has_no_parent_or_label(self) -> None:
        mgr = self.manager({"auto_a": make_ir()}, clock=_RecordingClock())
        v = mgr.snapshot("auto_a")
        self.assertIsNone(v.parent_id)
        self.assertIsNone(v.label)

    def test_snapshot_parent_id_links_to_previous_version(self) -> None:
        mgr = self.manager({"auto_a": make_ir()}, clock=_RecordingClock())
        v1 = mgr.snapshot("auto_a")
        v2 = mgr.snapshot("auto_a")
        v3 = mgr.snapshot("auto_a")
        self.assertEqual(v2.parent_id, v1.version_id)
        self.assertEqual(v3.parent_id, v2.version_id)

    def test_snapshot_version_ids_are_unique(self) -> None:
        mgr = self.manager({"auto_a": make_ir()}, clock=lambda: 7.0)
        ids = {mgr.snapshot("auto_a").version_id for _ in range(5)}
        self.assertEqual(len(ids), 5)

    def test_snapshot_accepts_explicit_ir_argument(self) -> None:
        mgr = self.manager({}, clock=_RecordingClock())          # 无 provider 也可
        explicit = make_ir("auto_x", nodes={"only": {"id": "only", "kind": "ask"}})
        v = mgr.snapshot("auto_x", explicit)
        self.assertEqual(v.ir_snapshot, explicit)

    def test_snapshot_reads_ir_from_provider_by_automation_id(self) -> None:
        seen = []

        def provider(aid):
            seen.append(aid)
            return make_ir(aid)

        mgr = VersionManager(root=self.root, ir_provider=provider, clock=_RecordingClock())
        mgr.snapshot("auto_a")
        mgr.snapshot("auto_b")
        self.assertEqual(seen, ["auto_a", "auto_b"])

    def test_snapshot_without_provider_raises(self) -> None:
        mgr = VersionManager(root=self.root, clock=_RecordingClock())
        with self.assertRaises(VersionError):
            mgr.snapshot("auto_a")

    def test_snapshot_unknown_automation_raises(self) -> None:
        irs = {"auto_a": make_ir("auto_a"), "auto_b": make_ir("auto_b")}
        mgr = self.manager(irs, clock=_RecordingClock())
        with self.assertRaises(VersionError):
            mgr.snapshot("auto_z")


class DiffTests(_Base):
    def _pair(self, ir1, ir2):
        mgr = self.manager({"auto_a": ir1}, clock=_RecordingClock())
        v1 = mgr.snapshot("auto_a", ir1)
        mgr._cache["auto_a"]["versions"][-1] = v1                # 明确基线
        irs = self.manager.__self__ if False else None           # (no-op, 保持可读)
        v2 = self.manager({"auto_a": ir2}, clock=_RecordingClock()).snapshot("auto_a", ir2)
        return v1, v2

    def test_diff_detects_added_node(self) -> None:
        ir1 = make_ir()
        ir2 = make_ir(nodes={
            "n1": {"id": "n1", "kind": "do", "action": "switch.on", "entities": ["light.kitchen"]},
            "n2": {"id": "n2", "kind": "ask", "prompt": "确认？"},
        })
        v1, v2 = self._pair(ir1, ir2)
        d = diff(v1, v2)
        self.assertIn("n2", d["nodes"]["added"])
        self.assertEqual(d["nodes"]["added_ids"], ["n2"])
        self.assertEqual(d["summary"]["nodes_added"], 1)
        self.assertEqual(d["nodes"]["deleted_ids"], [])
        self.assertFalse(d["identical"])

    def test_diff_detects_deleted_node(self) -> None:
        ir1 = make_ir(nodes={
            "n1": {"id": "n1", "kind": "do", "action": "switch.on", "entities": ["light.kitchen"]},
            "n2": {"id": "n2", "kind": "ask", "prompt": "确认？"},
        })
        ir2 = make_ir()
        v1, v2 = self._pair(ir1, ir2)
        d = diff(v1, v2)
        self.assertEqual(d["nodes"]["deleted_ids"], ["n2"])
        self.assertIn("n2", d["removed"]["nodes"])
        self.assertEqual(d["summary"]["nodes_deleted"], 1)

    def test_diff_detects_modified_node_with_changed_fields(self) -> None:
        ir1 = make_ir()
        ir2 = make_ir(nodes={"n1": {"id": "n1", "kind": "do", "action": "switch.off",
                                    "entities": ["light.kitchen"]}})
        v1, v2 = self._pair(ir1, ir2)
        d = diff(v1, v2)
        self.assertEqual(d["nodes"]["modified_ids"], ["n1"])
        self.assertEqual(d["nodes"]["modified"]["n1"]["changed"], ["action"])
        self.assertEqual(d["nodes"]["modified"]["n1"]["before"]["action"], "switch.on")
        self.assertEqual(d["nodes"]["modified"]["n1"]["after"]["action"], "switch.off")

    def test_diff_detects_added_edge(self) -> None:
        nodes = {
            "n1": {"id": "n1", "kind": "do", "action": "a"},
            "n2": {"id": "n2", "kind": "ask", "prompt": "p"},
        }
        ir1 = make_ir(nodes=nodes)
        ir2 = make_ir(nodes=nodes, edges=[{"from": "n1", "to": "n2", "kind": "then"}])
        v1, v2 = self._pair(ir1, ir2)
        d = diff(v1, v2)
        self.assertEqual(d["summary"]["edges_added"], 1)
        self.assertTrue(list(d["edges"]["added_ids"])[0].startswith("n1->n2"))
        self.assertEqual(d["summary"]["nodes_added"], 0)

    def test_diff_detects_deleted_edge(self) -> None:
        nodes = {
            "n1": {"id": "n1", "kind": "do", "action": "a"},
            "n2": {"id": "n2", "kind": "ask", "prompt": "p"},
        }
        ir1 = make_ir(nodes=nodes, edges=[{"id": "e1", "from": "n1", "to": "n2", "kind": "then"}])
        ir2 = make_ir(nodes=nodes)
        v1, v2 = self._pair(ir1, ir2)
        d = diff(v1, v2)
        self.assertEqual(d["edges"]["deleted_ids"], ["e1"])
        self.assertEqual(d["deleted_edges"], ["e1"])

    def test_diff_detects_modified_edge(self) -> None:
        nodes = {
            "n1": {"id": "n1", "kind": "do", "action": "a"},
            "n2": {"id": "n2", "kind": "ask", "prompt": "p"},
        }
        ir1 = make_ir(nodes=nodes, edges=[{"id": "e1", "from": "n1", "to": "n2",
                                           "kind": "then", "priority": 1}])
        ir2 = make_ir(nodes=nodes, edges=[{"id": "e1", "from": "n1", "to": "n2",
                                           "kind": "then", "priority": 2}])
        v1, v2 = self._pair(ir1, ir2)
        d = diff(v1, v2)
        self.assertEqual(d["edges"]["modified_ids"], ["e1"])
        self.assertEqual(d["edges"]["modified"]["e1"]["changed"], ["priority"])

    def test_diff_detects_condition_added(self) -> None:
        ir1 = make_ir()
        ir2 = make_ir(nodes={"n1": {"id": "n1", "kind": "do", "action": "switch.on",
                                    "entities": ["light.kitchen"], "when": "x > 1"}})
        v1, v2 = self._pair(ir1, ir2)
        d = diff(v1, v2)
        self.assertEqual(d["conditions"]["added_ids"], ["node::n1"])
        self.assertEqual(d["conditions"]["added"]["node::n1"], "x > 1")
        self.assertEqual(d["added_conditions"], ["node::n1"])

    def test_diff_detects_condition_deleted(self) -> None:
        ir1 = make_ir(nodes={"n1": {"id": "n1", "kind": "do", "action": "switch.on",
                                    "entities": ["light.kitchen"], "when": "x > 1"}})
        ir2 = make_ir()
        v1, v2 = self._pair(ir1, ir2)
        d = diff(v1, v2)
        self.assertEqual(d["conditions"]["deleted_ids"], ["node::n1"])
        self.assertEqual(d["summary"]["conditions_deleted"], 1)

    def test_diff_detects_condition_modified(self) -> None:
        nodes = {
            "n1": {"id": "n1", "kind": "do", "action": "a"},
            "n2": {"id": "n2", "kind": "ask", "prompt": "p"},
        }
        ir1 = make_ir(nodes=nodes, edges=[{"id": "e1", "from": "n1", "to": "n2",
                                           "kind": "then", "when": "a"}])
        ir2 = make_ir(nodes=nodes, edges=[{"id": "e1", "from": "n1", "to": "n2",
                                           "kind": "then", "when": "b"}])
        v1, v2 = self._pair(ir1, ir2)
        d = diff(v1, v2)
        self.assertEqual(list(d["conditions"]["modified"]), ["edge::e1"])
        self.assertEqual(d["conditions"]["modified"]["edge::e1"]["before"], "a")
        self.assertEqual(d["conditions"]["modified"]["edge::e1"]["after"], "b")
        self.assertEqual(d["edges"]["modified"]["e1"]["changed"], ["when"])

    def test_diff_identical_snapshots_have_no_changes(self) -> None:
        ir = make_ir()
        v1, v2 = self._pair(ir, copy.deepcopy(ir))
        d = diff(v1, v2)
        self.assertTrue(d["identical"])
        self.assertEqual(d["summary"]["total_changes"], 0)

    def test_diff_from_empty_marks_all_added(self) -> None:
        v1, v2 = self._pair({}, make_ir())
        d = diff(v1, v2)
        self.assertEqual(d["nodes"]["added_ids"], ["n1"])
        self.assertEqual(d["summary"]["nodes_added"], 1)
        self.assertEqual(d["summary"]["nodes_deleted"], 0)

    def test_diff_accepts_raw_ir_mappings(self) -> None:
        ir1 = make_ir()
        ir2 = make_ir(nodes={
            "n1": {"id": "n1", "kind": "do", "action": "switch.on", "entities": ["light.kitchen"]},
            "n2": {"id": "n2", "kind": "ask", "prompt": "p"},
        })
        d = diff(ir1, ir2)                                        # 模块级纯函数
        self.assertIn("n2", d["nodes"]["added"])
        self.assertIsNone(d["v1"])
        self.assertIsNone(d["v2"])

    def test_diff_accepts_version_ids_via_manager(self) -> None:
        ir1 = make_ir()
        ir2 = make_ir(nodes={"n1": {"id": "n1", "kind": "do", "action": "switch.off",
                                    "entities": ["light.kitchen"]}})
        v1, v2 = self._pair(ir1, ir2)
        mgr = self.manager({"auto_a": ir2}, clock=_RecordingClock())
        mgr.snapshot("auto_a", ir1)
        mgr.snapshot("auto_a", ir2)
        w1, w2 = mgr.history("auto_a")[1], mgr.history("auto_a")[0]
        by_id = mgr.diff(w1.version_id, w2.version_id)
        self.assertEqual(by_id["summary"], diff(v1, v2)["summary"])
        self.assertEqual(by_id["v1"], w1.version_id)
        self.assertEqual(by_id["v2"], w2.version_id)

    def test_diff_views_agree(self) -> None:
        ir1 = make_ir()
        ir2 = make_ir(nodes={
            "n1": {"id": "n1", "kind": "do", "action": "switch.on", "entities": ["light.kitchen"]},
            "n2": {"id": "n2", "kind": "ask", "prompt": "p"},
        })
        v1, v2 = self._pair(ir1, ir2)
        d = diff(v1, v2)
        self.assertIs(d["added"]["nodes"], d["nodes"]["added"])
        self.assertEqual(d["nodes"]["added_ids"], d["added_nodes"])
        self.assertEqual(set(d["nodes"]["added"]), set(d["added_nodes"]))
        self.assertIs(d["deleted"]["nodes"], d["removed"]["nodes"])

    def test_diff_unknown_version_id_raises(self) -> None:
        mgr = self.manager({"auto_a": make_ir()}, clock=_RecordingClock())
        v = mgr.snapshot("auto_a")
        with self.assertRaises(VersionError):
            mgr.diff("nope", v.version_id)


class RollbackTests(_Base):
    def test_rollback_restores_ir_via_writer(self) -> None:
        ir1 = make_ir()
        ir2 = make_ir(nodes={"n1": {"id": "n1", "kind": "do", "action": "switch.off",
                                    "entities": ["light.kitchen"]}})
        writer = _RecordingWriter()
        irs = {"auto_a": ir1}
        mgr = self.manager(irs, writer=writer, clock=_RecordingClock())
        v1 = mgr.snapshot("auto_a")
        irs["auto_a"] = ir2
        mgr.snapshot("auto_a")
        self.assertTrue(mgr.rollback("auto_a", v1.version_id))
        self.assertEqual(writer.calls[-1][0], "auto_a")
        self.assertEqual(writer.calls[-1][1], ir1)               # 写回旧 IR
        self.assertEqual(mgr.current_ir("auto_a"), ir1)

    def test_rollback_creates_new_snapshot_with_restored_ir(self) -> None:
        ir1 = make_ir()
        ir2 = make_ir(nodes={"n1": {"id": "n1", "kind": "do", "action": "switch.off",
                                    "entities": ["light.kitchen"]}})
        irs = {"auto_a": ir1}
        mgr = self.manager(irs, clock=_RecordingClock())
        v1 = mgr.snapshot("auto_a")
        irs["auto_a"] = ir2
        v2 = mgr.snapshot("auto_a")
        self.assertTrue(mgr.rollback("auto_a", v1.version_id))
        hist = mgr.history("auto_a")
        self.assertEqual(len(hist), 3)                            # 自动新快照
        self.assertEqual(hist[0].ir_snapshot, ir1)                # 回滚后生效状态
        self.assertEqual(hist[0].parent_id, v2.version_id)
        self.assertIsNone(hist[0].label)

    def test_rollback_can_be_rolled_back_again(self) -> None:
        ir1 = make_ir()
        ir2 = make_ir(nodes={"n1": {"id": "n1", "kind": "do", "action": "switch.off",
                                    "entities": ["light.kitchen"]}})
        irs = {"auto_a": ir1}
        mgr = self.manager(irs, clock=_RecordingClock())
        v1 = mgr.snapshot("auto_a")
        irs["auto_a"] = ir2
        v2 = mgr.snapshot("auto_a")
        self.assertTrue(mgr.rollback("auto_a", v1.version_id))
        self.assertTrue(mgr.rollback("auto_a", v2.version_id))    # 再次回滚（滚回去）
        self.assertEqual(mgr.current_ir("auto_a"), ir2)
        self.assertEqual(len(mgr.history("auto_a")), 4)

    def test_current_ir_tracks_head_and_falls_back_to_provider(self) -> None:
        irs = {"auto_a": make_ir()}
        mgr = self.manager(irs, clock=_RecordingClock())
        self.assertEqual(mgr.current_ir("auto_a"), irs["auto_a"])  # 无版本 → provider
        mgr.snapshot("auto_a")
        irs["auto_a"] = {"id": "auto_a", "nodes": {}}
        self.assertEqual(mgr.current_ir("auto_a"), make_ir())      # 有版本 → head 快照

    def test_rollback_unknown_version_returns_false(self) -> None:
        writer = _RecordingWriter()
        mgr = self.manager({"auto_a": make_ir()}, writer=writer, clock=_RecordingClock())
        mgr.snapshot("auto_a")
        self.assertIs(mgr.rollback("auto_a", "nope"), False)
        self.assertEqual(writer.calls, [])                        # 不触发写回
        self.assertEqual(len(mgr.history("auto_a")), 1)            # 不追加快照

    def test_rollback_version_of_other_automation_returns_false(self) -> None:
        irs = {"auto_a": make_ir("auto_a"), "auto_b": make_ir("auto_b")}
        mgr = self.manager(irs, clock=_RecordingClock())
        mgr.snapshot("auto_a")
        other = mgr.snapshot("auto_b")
        self.assertIs(mgr.rollback("auto_a", other.version_id), False)
        self.assertEqual(len(mgr.history("auto_a")), 1)

    def test_rollback_writer_failure_is_fail_closed(self) -> None:
        writer = _RecordingWriter(fail=True)
        irs = {"auto_a": make_ir()}
        mgr = self.manager(irs, writer=writer, clock=_RecordingClock())
        v1 = mgr.snapshot("auto_a")
        irs["auto_a"] = {"id": "auto_a", "nodes": {}}
        mgr.snapshot("auto_a")
        self.assertIs(mgr.rollback("auto_a", v1.version_id), False)
        self.assertEqual(len(mgr.history("auto_a")), 2)            # 无半套状态
        self.assertEqual(len(writer.calls), 1)


class TagTests(_Base):
    def test_tag_sets_label(self) -> None:
        mgr = self.manager({"auto_a": make_ir()}, clock=_RecordingClock())
        v = mgr.snapshot("auto_a")
        out = mgr.tag(v.version_id, "stable")
        self.assertEqual(out.label, "stable")
        self.assertEqual(mgr.history("auto_a")[0].label, "stable")

    def test_tag_accepts_all_canonical_labels(self) -> None:
        mgr = self.manager({"auto_a": make_ir()}, clock=_RecordingClock())
        v = mgr.snapshot("auto_a")
        for label in LABELS:
            mgr.tag(v.version_id, label)
            self.assertEqual(mgr.find(v.version_id).label, label)

    def test_tag_rejects_unknown_label(self) -> None:
        mgr = self.manager({"auto_a": make_ir()}, clock=_RecordingClock())
        v = mgr.snapshot("auto_a")
        with self.assertRaises(VersionError):
            mgr.tag(v.version_id, "beta")

    def test_tag_unknown_version_raises(self) -> None:
        mgr = self.manager({"auto_a": make_ir()}, clock=_RecordingClock())
        with self.assertRaises(VersionError):
            mgr.tag("nope", "canary")


class HistoryTests(_Base):
    def test_history_is_reverse_chronological(self) -> None:
        mgr = self.manager({"auto_a": make_ir()}, clock=_RecordingClock(start=1000, step=1))
        v1 = mgr.snapshot("auto_a")
        v2 = mgr.snapshot("auto_a")
        v3 = mgr.snapshot("auto_a")
        self.assertEqual([v.version_id for v in mgr.history("auto_a")],
                         [v3.version_id, v2.version_id, v1.version_id])
        self.assertGreaterEqual(mgr.history("auto_a")[0].created_at,
                                mgr.history("auto_a")[-1].created_at)

    def test_history_ties_break_by_creation_order(self) -> None:
        mgr = self.manager({"auto_a": make_ir()}, clock=lambda: 5.0)   # 冻结时钟
        v1 = mgr.snapshot("auto_a")
        v2 = mgr.snapshot("auto_a")
        v3 = mgr.snapshot("auto_a")
        self.assertEqual([v.created_at for v in mgr.history("auto_a")], [5.0, 5.0, 5.0])
        self.assertEqual([v.version_id for v in mgr.history("auto_a")],
                         [v3.version_id, v2.version_id, v1.version_id])

    def test_history_unknown_automation_is_empty(self) -> None:
        mgr = self.manager({}, clock=_RecordingClock())
        self.assertEqual(mgr.history("auto_nope"), [])


class PersistenceTests(_Base):
    def test_persistence_file_layout(self) -> None:
        mgr = self.manager({"auto_a": make_ir()}, clock=_RecordingClock())
        mgr.snapshot("auto_a")
        path = self.root / ".forge" / "versions" / "auto_a.json"
        self.assertTrue(path.exists())
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["automation_id"], "auto_a")
        self.assertEqual(len(data["versions"]), 1)
        self.assertEqual(data["versions"][0]["automation_id"], "auto_a")
        self.assertIn("ir_snapshot", data["versions"][0])

    def test_persistence_survives_restart(self) -> None:
        irs = {"auto_a": make_ir()}
        mgr = self.manager(irs, clock=_RecordingClock())
        v1 = mgr.snapshot("auto_a")
        v2 = mgr.snapshot("auto_a")
        mgr2 = self.manager(irs, clock=_RecordingClock())          # "重启"：全新实例
        self.assertEqual([v.version_id for v in mgr2.history("auto_a")],
                         [v2.version_id, v1.version_id])
        self.assertEqual(mgr2.history("auto_a")[0].ir_snapshot, irs["auto_a"])

    def test_persistence_preserves_order_and_labels(self) -> None:
        irs = {"auto_a": make_ir()}
        mgr = self.manager(irs, clock=_RecordingClock())
        v1 = mgr.snapshot("auto_a")
        mgr.snapshot("auto_a")
        mgr.tag(v1.version_id, "canary")
        mgr2 = self.manager(irs, clock=_RecordingClock())
        hist = mgr2.history("auto_a")
        self.assertEqual(hist[1].label, "canary")
        self.assertIsNone(hist[0].label)
        self.assertEqual(hist[0].parent_id, hist[1].version_id)

    def test_persistence_corrupt_file_raises(self) -> None:
        irs = {"auto_a": make_ir()}
        mgr = self.manager(irs, clock=_RecordingClock())
        mgr.snapshot("auto_a")
        path = self.root / ".forge" / "versions" / "auto_a.json"
        path.write_text("{ not json", encoding="utf-8")
        mgr2 = self.manager(irs, clock=_RecordingClock())
        with self.assertRaises(VersionError):                      # fail-closed，不吞历史
            mgr2.snapshot("auto_a")

    def test_forge_dir_root_is_honored(self) -> None:
        mgr = VersionManager(root=self.root / ".forge", ir_provider={"auto_a": make_ir()},
                             clock=_RecordingClock())
        mgr.snapshot("auto_a")
        self.assertTrue((self.root / ".forge" / "versions" / "auto_a.json").exists())


class HookAndFacadeTests(_Base):
    def test_wrap_deployer_snapshots_pre_deploy_state(self) -> None:
        ir1 = make_ir()
        ir2 = make_ir(nodes={"n1": {"id": "n1", "kind": "do", "action": "switch.off",
                                    "entities": ["light.kitchen"]}})
        irs = {"auto_a": ir1}
        mgr = self.manager(irs, clock=_RecordingClock())
        calls = []

        def deploy(plan):
            calls.append(plan)
            irs["auto_a"] = ir2                                  # 部署改写 IR
            return "auto_a"

        wrapped = mgr.wrap_deployer(deploy)
        self.assertEqual(wrapped(_Plan(ir2)), "auto_a")
        self.assertEqual(len(calls), 1)
        hist = mgr.history("auto_a")
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0].ir_snapshot, ir1)                # 部署前状态已快照

    def test_wrap_deployer_is_fail_open_without_provider(self) -> None:
        mgr = VersionManager(root=self.root, clock=_RecordingClock())
        calls = []

        def deploy(plan):
            calls.append(plan)
            return "auto_a"

        wrapped = mgr.wrap_deployer(deploy)
        self.assertEqual(wrapped(_Plan(make_ir())), "auto_a")     # 部署不被版本子系统阻断
        self.assertEqual(len(calls), 1)
        self.assertEqual(mgr.history("auto_a"), [])

    def test_module_level_helpers_use_default_store(self) -> None:
        irs = {"auto_a": make_ir()}
        mgr = self.manager(irs, clock=_RecordingClock())
        set_default_store(mgr)
        self.addCleanup(set_default_store, None)
        from autoforge.af_version import history, rollback, snapshot, tag

        v = snapshot("auto_a")
        tag(v.version_id, "canary")
        self.assertEqual(history("auto_a")[0].label, "canary")
        irs["auto_a"] = {"id": "auto_a", "nodes": {}}
        snapshot("auto_a")
        self.assertTrue(rollback("auto_a", v.version_id))
        self.assertEqual(mgr.current_ir("auto_a"), make_ir())


if __name__ == "__main__":       # pragma: no cover
    unittest.main()