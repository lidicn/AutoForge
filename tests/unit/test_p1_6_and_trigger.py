"""P1-6：AND 复合触发基于状态快照（窗口默认 0，严格同时）。

Bug：group and 用同一个 event 匹配所有子 trigger，一个事件只能匹配一个
子 trigger 的 entity_id，导致 and 永远不成立。

修复：group and 基于状态快照判定——任一子 trigger 事件到达时，检查所有
子 trigger 的实体当前状态是否满足条件。窗口默认 0（严格同时）。
"""

import pytest

from autoforge.af_bus import BusEvent
from autoforge.af_ir import Graph, load_automation
from autoforge.af_runtime import build_runtime


def _and_ir() -> dict:
    return {
        "ir_version": "0.2.1",
        "id": "and_test",
        "name": "AND 复合触发测试",
        "version": 1,
        "mode": "single",
        "nodes": [
            {
                "id": "a1", "kind": "on",
                "trigger": {
                    "type": "group", "op": "and",
                    "sources": [
                        {"type": "state", "entity_id": "binary_sensor.a", "to": "on"},
                        {"type": "state", "entity_id": "binary_sensor.b", "to": "on"},
                    ],
                },
            },
            {"id": "p1", "kind": "pass"},
        ],
        "edges": [{"from": "a1", "to": "p1", "kind": "then"}],
    }


class TestAndTriggerStateSnapshot:
    """AND 复合触发基于状态快照。"""

    def test_and_fires_when_both_already_on(self):
        """P1-6 核心：a 和 b 都已经是 on，a 的事件到达 → and 成立 → 触发。"""
        runtime = build_runtime(Graph([load_automation(_and_ir())]))
        runtime.states.set_state("binary_sensor.a", "on")
        runtime.states.set_state("binary_sensor.b", "on")
        instances = runtime.emit("binary_sensor.a", "on")
        assert len(instances) == 1, f"AND 应该触发，实际 {len(instances)} 个实例"

    def test_and_not_fires_when_other_off(self):
        """a 是 on 但 b 是 off，a 的事件到达 → and 不成立 → 不触发。"""
        runtime = build_runtime(Graph([load_automation(_and_ir())]))
        runtime.states.set_state("binary_sensor.a", "on")
        runtime.states.set_state("binary_sensor.b", "off")
        instances = runtime.emit("binary_sensor.a", "on")
        assert len(instances) == 0, "AND 不应该触发（b 是 off）"

    def test_and_fires_on_second_event(self):
        """a 先 on（b off，不触发），然后 b on → and 成立 → 触发。"""
        runtime = build_runtime(Graph([load_automation(_and_ir())]))
        runtime.states.set_state("binary_sensor.a", "on")
        runtime.states.set_state("binary_sensor.b", "off")
        # a 的事件：b 还 off，不触发
        assert len(runtime.emit("binary_sensor.a", "on")) == 0
        # b 变 on：两个都 on，触发
        runtime.states.set_state("binary_sensor.b", "on")
        instances = runtime.emit("binary_sensor.b", "on")
        assert len(instances) == 1, "AND 应该在第二个事件触发"

    def test_or_trigger_still_works(self):
        """OR 复合触发不受影响：任一子 trigger 满足即触发。"""
        ir = {
            "ir_version": "0.2.1", "id": "or_test", "name": "OR", "version": 1, "mode": "single",
            "nodes": [
                {"id": "a1", "kind": "on", "trigger": {
                    "type": "group", "op": "or",
                    "sources": [
                        {"type": "state", "entity_id": "binary_sensor.a", "to": "on"},
                        {"type": "state", "entity_id": "binary_sensor.b", "to": "on"},
                    ],
                }},
                {"id": "p1", "kind": "pass"},
            ],
            "edges": [{"from": "a1", "to": "p1", "kind": "then"}],
        }
        runtime = build_runtime(Graph([load_automation(ir)]))
        runtime.states.set_state("binary_sensor.a", "on")
        instances = runtime.emit("binary_sensor.a", "on")
        assert len(instances) == 1, "OR 应该触发"
