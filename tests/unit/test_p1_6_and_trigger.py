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


# ── and 里套 group（§5.3 第 16 件②）──────────────────────────────────────
# `_group_sub_satisfied` 原本只认 state/event 两支，嵌套 group 落到末尾"非事件驱动类型 ⇒ False"，
# 于是 `and[ or(a,b), c ]` 这种合法 IR 的条件**永远不成立**（静默 False，不报错）。


def _nested_and_ir(sources: list) -> dict:
    return {
        "ir_version": "0.2.1",
        "id": "nested_and",
        "name": "and 里套复合",
        "version": 1,
        "mode": "single",
        "nodes": [
            {"id": "a1", "kind": "on", "trigger": {"type": "group", "op": "and", "sources": sources}},
            {"id": "p1", "kind": "pass"},
        ],
        "edges": [{"from": "a1", "to": "p1", "kind": "then"}],
    }


_OR_AB = {"type": "group", "op": "or", "sources": [
    {"type": "state", "entity_id": "binary_sensor.a", "to": "on"},
    {"type": "state", "entity_id": "binary_sensor.b", "to": "on"},
]}
_C_ON = {"type": "state", "entity_id": "binary_sensor.c", "to": "on"}


class TestNestedGroupInAnd:
    def test_or_inside_and_fires(self):
        """a 的事件让内层 or 成立、c 的快照成立 ⇒ 外层 and 成立（修之前这里恒不触发）。"""
        runtime = build_runtime(Graph([load_automation(_nested_and_ir([_OR_AB, _C_ON]))]))
        runtime.states.set_state("binary_sensor.a", "on")
        runtime.states.set_state("binary_sensor.c", "on")
        assert len(runtime.emit("binary_sensor.a", "on")) == 1

    def test_or_inside_and_still_needs_the_sibling_branch(self):
        """反空洞①：内层 or 成立但兄弟支 c 是 off ⇒ 仍然不成立（不是"认嵌套就一律放行"）。"""
        runtime = build_runtime(Graph([load_automation(_nested_and_ir([_OR_AB, _C_ON]))]))
        runtime.states.set_state("binary_sensor.a", "on")
        runtime.states.set_state("binary_sensor.c", "off")
        assert len(runtime.emit("binary_sensor.a", "on")) == 0

    def test_nested_branch_is_actually_evaluated(self):
        """反空洞②：把 naive `True` 逼红的档——内层 or 两支都不成立，事件来自第三方实体。"""
        runtime = build_runtime(Graph([load_automation(_nested_and_ir([_OR_AB, _C_ON]))]))
        runtime.states.set_state("binary_sensor.a", "off")
        runtime.states.set_state("binary_sensor.b", "off")
        runtime.states.set_state("binary_sensor.c", "on")
        assert len(runtime.emit("sensor.z", "on")) == 0

    def test_and_inside_and_is_evaluated(self):
        """嵌套的层数不止 or：and[ and(a,b), c ] 三支都成立才触发。"""
        inner_and = {"type": "group", "op": "and", "sources": [
            {"type": "state", "entity_id": "binary_sensor.a", "to": "on"},
            {"type": "state", "entity_id": "binary_sensor.b", "to": "on"},
        ]}
        runtime = build_runtime(Graph([load_automation(_nested_and_ir([inner_and, _C_ON]))]))
        for eid in ("binary_sensor.a", "binary_sensor.b", "binary_sensor.c"):
            runtime.states.set_state(eid, "on")
        assert len(runtime.emit("binary_sensor.a", "on")) == 1
        runtime.states.set_state("binary_sensor.b", "off")
        assert len(runtime.emit("binary_sensor.a", "on")) == 0

    def test_over_budget_nesting_raises_named_error_on_this_leg(self):
        """F6 的预算在这根运行期腿上：手工构造的超深树抛具名异常，不是栈耗尽。"""
        from autoforge.af_ir import MAX_TRIGGER_DEPTH, Trigger, TriggerDepthError

        deep = Trigger(type="state", entity_id="binary_sensor.a", to="on")
        for _ in range(MAX_TRIGGER_DEPTH + 5):
            deep = Trigger(type="group", op="and", sources=[deep])
        auto = load_automation(_nested_and_ir([_C_ON]))
        # 载入后才注入超深树：`Trigger.from_dict` 在边界就会拒，走者这一档要单独钉。
        object.__setattr__(auto.nodes["a1"], "trigger", deep)
        runtime = build_runtime(Graph([auto]))
        with pytest.raises(TriggerDepthError):
            runtime.emit("binary_sensor.a", "on")

