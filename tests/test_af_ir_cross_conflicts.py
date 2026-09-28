"""F10② 跨自动化一致性校验（F9 group 决策 D 方案 B 并入 F10）。

覆盖：
- cross_automation_conflicts：任意一组自动化之间的共享/相反实体冲突（不限于 group 子自动化）
- check_store_cross_conflicts：store 级扫描全部自动化的一致性校验（只读、无副作用）
"""

from autoforge.af_ir import Automation
from autoforge.af_apply import check_store_cross_conflicts, cross_automation_conflicts

A_OFF = {
    "ir_version": "0.3.0", "id": "off_lights", "name": "关所有灯", "version": 1, "mode": "single",
    "nodes": [
        {"id": "on1", "kind": "on", "trigger": {"type": "state", "entity_id": "input_boolean.good_night", "to": "on"}},
        {"id": "d1", "kind": "do", "adapter": "ha", "action": "light.turn_off", "params": {"entity_id": "light.all"}},
    ],
    "edges": [{"from": "on1", "to": "d1", "kind": "then"}],
}
A_ON = {
    "ir_version": "0.3.0", "id": "on_lights", "name": "开夜灯", "version": 1, "mode": "single",
    "nodes": [
        {"id": "onx", "kind": "on", "trigger": {"type": "state", "entity_id": "input_boolean.good_night", "to": "on"}},
        {"id": "dx", "kind": "do", "adapter": "ha", "action": "light.turn_on", "params": {"entity_id": "light.all"}},
    ],
    "edges": [{"from": "onx", "to": "dx", "kind": "then"}],
}
A_LOCK = {
    "ir_version": "0.3.0", "id": "lock_door", "name": "锁门", "version": 1, "mode": "single",
    "nodes": [
        {"id": "on2", "kind": "on", "trigger": {"type": "state", "entity_id": "input_boolean.good_night", "to": "on"}},
        {"id": "d2", "kind": "do", "adapter": "ha", "action": "lock.lock", "params": {"entity_id": "lock.front_door"}},
    ],
    "edges": [{"from": "on2", "to": "d2", "kind": "then"}],
}


def _auto(d):
    return Automation.from_dict(d)


def test_cross_automation_conflicts_opposite():
    conflicts = cross_automation_conflicts([_auto(A_OFF), _auto(A_ON)])
    c = [x for x in conflicts if x["type"] == "conflict"]
    assert c, "应检出 light.all 相反操作冲突"
    assert c[0]["entity"] == "light.all"


def test_cross_automation_conflicts_disjoint_no_false_positive():
    # 不同实体（light.all vs lock.front_door）→ 无冲突误报
    conflicts = cross_automation_conflicts([_auto(A_OFF), _auto(A_LOCK)])
    assert [x for x in conflicts if x["type"] == "conflict"] == []


def test_check_store_cross_conflicts_fake_store():
    class FakeStore:
        def __init__(self, graphs):
            self._g = graphs

        def history(self):
            return [{"name": n} for n in self._g]

        def load_record(self, name):
            return {"graph": self._g[name]}

    store = FakeStore({"a": A_OFF, "b": A_ON})
    conflicts = check_store_cross_conflicts(store)
    c = [x for x in conflicts if x["type"] == "conflict"]
    assert c and c[0]["entity"] == "light.all"
