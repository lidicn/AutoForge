"""tests/test_af_scene.py —— af_scene 场景模式单测（43 条）。

覆盖：契约字段 / 激活 / 关闭 / 互斥 / 非互斥共存 / list_scenes / 持久化与重启 /
边界（未知 id、空场景、幂等）/ 异常路径（executor 失败、抛异常、状态文件损坏）。
所有 executor 均为内存桩，仅记录调用，无外部副作用。
"""

from __future__ import annotations

import json
import os

import pytest

from autoforge.af_scene import PERSIST_VERSION, Scene, SceneManager


# --------------------------------------------------------------------------- 桩
class FakeExecutor:
    """记录 (automation_id, enabled) 调用序列的桩 executor。"""

    def __init__(self, *, log=None, fail=(), raises=()):
        self.log = [] if log is None else log
        self.fail = set(fail)
        self.raises = set(raises)

    def set_automation_enabled(self, automation_id: str, enabled: bool) -> bool:
        self.log.append((automation_id, enabled))
        if automation_id in self.raises:
            raise RuntimeError("executor boom: %s" % automation_id)
        return automation_id not in self.fail


class EnableDisableExecutor(FakeExecutor):
    def enable(self, automation_id: str):
        return self.set_automation_enabled(automation_id, True)

    def disable(self, automation_id: str):
        return self.set_automation_enabled(automation_id, False)


class EnableAutomationExecutor(FakeExecutor):
    def enable_automation(self, automation_id: str):
        return self.set_automation_enabled(automation_id, True)

    def disable_automation(self, automation_id: str):
        return self.set_automation_enabled(automation_id, False)


class BrokenExecutor:
    pass


def _scenes():
    """标准三场景：A/B 同互斥组 g1，C 不参与互斥。"""
    return [
        Scene("a", "离家", ["a1", "a2"], "g1"),
        Scene("b", "回家", ["b1"], "g1"),
        Scene("c", "观影", ["c1", "c2"], None),
    ]


def _manager(tmp_path, executor=None, scenes=None):
    return SceneManager(_scenes() if scenes is None else scenes, executor or FakeExecutor(), str(tmp_path))


# ------------------------------------------------------- Scene 契约与构造边界
def test_scene_defaults():
    s = Scene("x", "空场景")
    assert s.scene_id == "x"
    assert s.name == "空场景"
    assert s.automations == []
    assert s.exclusive_group is None
    assert s.active is False


def test_scene_explicit_fields():
    s = Scene("a", "离家", ["a1"], "g1", True)
    assert (s.automations, s.exclusive_group, s.active) == (["a1"], "g1", True)


def test_manager_copies_automations_list():
    autos = ["a1"]
    mgr = _manager(tmp_path=None, scenes=[Scene("a", "n", autos)], executor=FakeExecutor()) \
        if False else SceneManager([Scene("a", "n", autos)], FakeExecutor(), os.devnull + "-d")
    autos.append("hacked")
    assert mgr.list_scenes()[0].automations == ["a1"]


def test_duplicate_scene_id_rejected(tmp_path):
    with pytest.raises(ValueError):
        SceneManager([Scene("a", "1"), Scene("a", "2")], FakeExecutor(), str(tmp_path))


def test_executor_shape_set_automation_enabled(tmp_path):
    mgr = SceneManager([Scene("a", "n", ["a1"])], FakeExecutor(), str(tmp_path))
    assert mgr.activate("a") is True


def test_executor_shape_enable_disable(tmp_path):
    ex = EnableDisableExecutor()
    mgr = SceneManager([Scene("a", "n", ["a1"])], ex, str(tmp_path))
    assert mgr.activate("a") is True
    assert ex.log == [("a1", True)]


def test_executor_shape_enable_automation(tmp_path):
    ex = EnableAutomationExecutor()
    mgr = SceneManager([Scene("a", "n", ["a1"])], ex, str(tmp_path))
    assert mgr.deactivate("a") is True  # 未激活 → 幂等 True
    assert mgr.activate("a") is True
    assert ex.log == [("a1", True)]


def test_executor_shape_unsupported_raises(tmp_path):
    with pytest.raises(TypeError):
        SceneManager([Scene("a", "n")], BrokenExecutor(), str(tmp_path))


# --------------------------------------------------------------- list/get
def test_list_scenes_returns_all(tmp_path):
    mgr = _manager(tmp_path)
    assert sorted(s.scene_id for s in mgr.list_scenes()) == ["a", "b", "c"]


def test_list_scenes_preserves_declaration_order(tmp_path):
    mgr = _manager(tmp_path)
    assert [s.scene_id for s in mgr.list_scenes()] == ["a", "b", "c"]


def test_list_scenes_returns_copies(tmp_path):
    mgr = _manager(tmp_path)
    got = mgr.list_scenes()
    got[0].automations.append("hacked")
    got[0].active = True
    again = mgr.list_scenes()
    assert again[0].automations == ["a1", "a2"]
    assert again[0].active is False


def test_get_scene_unknown_returns_none(tmp_path):
    mgr = _manager(tmp_path)
    assert mgr.get_scene("nope") is None
    assert mgr.is_active("nope") is False


def test_get_scene_returns_copy(tmp_path):
    mgr = _manager(tmp_path)
    got = mgr.get_scene("a")
    got.active = True
    assert mgr.is_active("a") is False


# ------------------------------------------------------------------ activate
def test_activate_unknown_scene_returns_false(tmp_path):
    ex = FakeExecutor()
    mgr = SceneManager(_scenes(), ex, str(tmp_path))
    assert mgr.activate("ghost") is False
    assert ex.log == []


def test_activate_unknown_scene_touches_nothing(tmp_path):
    ex = FakeExecutor()
    mgr = SceneManager(_scenes(), ex, str(tmp_path))
    mgr.activate("ghost")
    assert [s.active for s in mgr.list_scenes()] == [False, False, False]
    assert ex.log == []


def test_activate_sets_active_flag(tmp_path):
    mgr = _manager(tmp_path)
    assert mgr.activate("a") is True
    assert mgr.is_active("a") is True


def test_activate_enables_all_automations(tmp_path):
    ex = FakeExecutor()
    mgr = SceneManager(_scenes(), ex, str(tmp_path))
    assert mgr.activate("a") is True
    assert ex.log == [("a1", True), ("a2", True)]


def test_activate_empty_automation_list(tmp_path):
    ex = FakeExecutor()
    mgr = SceneManager([Scene("e", "空", [])], ex, str(tmp_path))
    assert mgr.activate("e") is True
    assert mgr.is_active("e") is True
    assert ex.log == []


def test_activate_is_idempotent_noop(tmp_path):
    ex = FakeExecutor()
    mgr = SceneManager(_scenes(), ex, str(tmp_path))
    mgr.activate("a")
    ex.log.clear()
    assert mgr.activate("a") is True
    assert ex.log == []
    assert mgr.is_active("a") is True


def test_activate_after_deactivate_re_enables(tmp_path):
    ex = FakeExecutor()
    mgr = SceneManager(_scenes(), ex, str(tmp_path))
    mgr.activate("a")
    mgr.deactivate("a")
    ex.log.clear()
    assert mgr.activate("a") is True
    assert ex.log == [("a1", True), ("a2", True)]


# ---------------------------------------------------------------- deactivate
def test_deactivate_unknown_scene_returns_false(tmp_path):
    ex = FakeExecutor()
    mgr = SceneManager(_scenes(), ex, str(tmp_path))
    assert mgr.deactivate("ghost") is False
    assert ex.log == []


def test_deactivate_disables_all_automations(tmp_path):
    ex = FakeExecutor()
    mgr = SceneManager(_scenes(), ex, str(tmp_path))
    mgr.activate("a")
    ex.log.clear()
    assert mgr.deactivate("a") is True
    assert ex.log == [("a1", False), ("a2", False)]


def test_deactivate_clears_active_flag(tmp_path):
    mgr = _manager(tmp_path)
    mgr.activate("a")
    assert mgr.deactivate("a") is True
    assert mgr.is_active("a") is False


def test_deactivate_inactive_scene_is_noop(tmp_path):
    ex = FakeExecutor()
    mgr = SceneManager(_scenes(), ex, str(tmp_path))
    assert mgr.deactivate("a") is True
    assert ex.log == []


def test_active_scenes_reflects_state(tmp_path):
    mgr = _manager(tmp_path)
    assert mgr.active_scenes() == []
    mgr.activate("c")
    assert [s.scene_id for s in mgr.active_scenes()] == ["c"]
    assert isinstance(mgr.active_scenes()[0], Scene)


# ---------------------------------------------------------------------- 互斥
def test_exclusive_activation_deactivates_peer(tmp_path):
    mgr = _manager(tmp_path)
    mgr.activate("a")
    assert mgr.activate("b") is True
    assert mgr.is_active("a") is False
    assert mgr.is_active("b") is True


def test_exclusive_peer_automations_disabled(tmp_path):
    ex = FakeExecutor()
    mgr = SceneManager(_scenes(), ex, str(tmp_path))
    mgr.activate("a")
    ex.log.clear()
    mgr.activate("b")
    assert ("a1", False) in ex.log and ("a2", False) in ex.log


def test_exclusive_deactivate_happens_before_enable(tmp_path):
    """互斥组切换的下发顺序：先关旧场景，再开新场景（绝不并存）。"""
    ex = FakeExecutor()
    mgr = SceneManager(_scenes(), ex, str(tmp_path))
    mgr.activate("a")
    ex.log.clear()
    assert mgr.activate("b") is True
    assert ex.log == [("a1", False), ("a2", False), ("b1", True)]


def test_exclusive_group_keeps_single_active(tmp_path):
    mgr = _manager(tmp_path)
    mgr.activate("a")
    mgr.activate("b")
    mgr.activate("a")
    assert [s.scene_id for s in mgr.active_scenes()] == ["a"]
    assert mgr.is_active("b") is False


def test_non_exclusive_scenes_can_coexist(tmp_path):
    ex = FakeExecutor()
    scenes = [Scene("n1", "A", ["x1"], None), Scene("n2", "B", ["x2"], None)]
    mgr = SceneManager(scenes, ex, str(tmp_path))
    assert mgr.activate("n1") is True
    ex.log.clear()
    assert mgr.activate("n2") is True
    assert mgr.is_active("n1") and mgr.is_active("n2")
    assert ex.log == [("x2", True)]


def test_no_group_does_not_touch_others(tmp_path):
    """无互斥组的场景 C 激活时，不得下发任何 disable。"""
    ex = FakeExecutor()
    mgr = SceneManager(_scenes(), ex, str(tmp_path))
    mgr.activate("a")
    ex.log.clear()
    assert mgr.activate("c") is True
    assert all(enabled is True for _, enabled in ex.log)


# -------------------------------------------------------------------- 持久化
def test_persist_file_created(tmp_path):
    mgr = _manager(tmp_path)
    mgr.activate("a")
    assert os.path.isfile(os.path.join(str(tmp_path), "scenes.json"))


def test_persist_payload_shape(tmp_path):
    mgr = _manager(tmp_path)
    mgr.activate("a")
    with open(os.path.join(str(tmp_path), "scenes.json"), encoding="utf-8") as fh:
        payload = json.load(fh)
    assert payload["version"] == PERSIST_VERSION
    assert set(payload["scenes"]) == {"a", "b", "c"}
    assert payload["scenes"]["a"]["active"] is True
    assert payload["scenes"]["b"]["active"] is False
    assert payload["scenes"]["a"]["automations"] == ["a1", "a2"]


def test_state_survives_restart(tmp_path):
    SceneManager(_scenes(), FakeExecutor(), str(tmp_path)).activate("a")
    mgr2 = SceneManager(_scenes(), FakeExecutor(), str(tmp_path))
    assert mgr2.is_active("a") is True
    assert mgr2.is_active("b") is False


def test_deactivate_state_survives_restart(tmp_path):
    mgr = SceneManager(_scenes(), FakeExecutor(), str(tmp_path))
    mgr.activate("c")
    mgr.deactivate("c")
    mgr2 = SceneManager(_scenes(), FakeExecutor(), str(tmp_path))
    assert mgr2.is_active("c") is False
    assert mgr2.active_scenes() == []


def test_new_scene_defaults_inactive_after_restart(tmp_path):
    SceneManager(_scenes(), FakeExecutor(), str(tmp_path)).activate("a")
    scenes = _scenes() + [Scene("d", "新增", ["d1"], None)]
    mgr2 = SceneManager(scenes, FakeExecutor(), str(tmp_path))
    assert mgr2.is_active("d") is False
    assert mgr2.is_active("a") is True


def test_unknown_persisted_ids_ignored(tmp_path):
    path = os.path.join(str(tmp_path), "scenes.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"version": PERSIST_VERSION, "scenes": {"ghost": {"active": True}}}, fh)
    mgr = _manager(tmp_path)
    assert [s.scene_id for s in mgr.list_scenes()] == ["a", "b", "c"]
    assert [s.active for s in mgr.list_scenes()] == [False, False, False]


def test_corrupted_persist_file_fail_open(tmp_path):
    path = os.path.join(str(tmp_path), "scenes.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{not-json!!")
    mgr = _manager(tmp_path)  # 不得抛异常
    assert [s.active for s in mgr.list_scenes()] == [False, False, False]
    assert mgr.activate("a") is True


def test_missing_persist_dir_is_created(tmp_path):
    nested = os.path.join(str(tmp_path), "no", "such", "dir")
    mgr = SceneManager(_scenes(), FakeExecutor(), nested)
    mgr.activate("a")
    assert os.path.isfile(os.path.join(nested, "scenes.json"))
    assert mgr.last_persist_error is None


# ---------------------------------------------------------------- 失败/降级
def test_activate_enable_failure_rolls_back(tmp_path):
    ex = FakeExecutor(fail={"a2"})
    mgr = SceneManager(_scenes(), ex, str(tmp_path))
    assert mgr.activate("a") is False
    assert ex.log == [("a1", True), ("a2", True), ("a1", False)]
    assert mgr.is_active("a") is False


def test_activate_executor_exception_is_failure(tmp_path):
    ex = FakeExecutor(raises={"a1"})
    mgr = SceneManager(_scenes(), ex, str(tmp_path))
    assert mgr.activate("a") is False
    assert mgr.is_active("a") is False


def test_exclusive_deactivate_failure_aborts_activation(tmp_path):
    """同组旧场景关不干净 → 放弃激活新场景（互斥不变量优先）。"""
    ex = FakeExecutor(fail={"a1"})
    mgr = SceneManager(_scenes(), ex, str(tmp_path))
    SceneManager(_scenes(), FakeExecutor(), str(tmp_path))  # 独立实例，不影响断言
    mgr._scenes["a"].active = True  # 模拟 A 已激活
    ex.log.clear()
    assert mgr.activate("b") is False
    assert ex.log == [("a1", False), ("a2", False)]
    assert mgr.is_active("b") is False
    assert mgr.is_active("a") is True  # 关闭失败 → 状态保持激活，可重试


def test_deactivate_failure_reports_false_but_keeps_flag(tmp_path):
    ex = FakeExecutor()
    mgr = SceneManager(_scenes(), ex, str(tmp_path))
    mgr.activate("a")
    ex.fail.add("a1")
    ex.log.clear()
    assert mgr.deactivate("a") is False
    assert ex.log == [("a1", False), ("a2", False)]  # 不短路，全部尝试
    assert mgr.is_active("a") is True
    ex.fail.clear()
    assert mgr.deactivate("a") is True
    assert mgr.is_active("a") is False