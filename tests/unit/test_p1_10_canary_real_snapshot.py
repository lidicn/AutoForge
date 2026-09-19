"""P1-10：canary 回滚依据真实快照（pre_states），不用仿真逆推（inverse_action）。

关键区分：
- 旧代码：rollback 用 inverse_action(action)——light.turn_on 的逆是 light.turn_off
- 新代码：rollback 用 pre_states——如果动作前灯已经是 on，回滚应下发 turn_on（恢复原状），不是 turn_off
"""

import pytest

from autoforge.af_adapters import Adapter, CallResult
from autoforge.af_canary import CanaryResult, _state_to_action
from autoforge.af_state import InMemoryStateProvider


class _RecordingAdapter(Adapter):
    """记录所有 call，返回成功。"""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.dry_run = False

    def call(self, action: str, params: dict) -> CallResult:
        self.calls.append((action, dict(params)))
        return CallResult(success=True, data={})


class TestStateToAction:
    """_state_to_action 单元测试。"""

    def test_on_state_maps_to_turn_on(self):
        assert _state_to_action("light", "on") == "light.turn_on"
        assert _state_to_action("switch", "open") == "switch.turn_on"

    def test_off_state_maps_to_turn_off(self):
        assert _state_to_action("light", "off") == "light.turn_off"
        assert _state_to_action("switch", "closed") == "switch.turn_off"

    def test_none_state_returns_none(self):
        assert _state_to_action("light", None) is None

    def test_unknown_state_returns_none(self):
        assert _state_to_action("climate", "heat") is None
        assert _state_to_action("light", "255") is None


class TestRollbackUsesRealSnapshot:
    """rollback 依据 pre_states，不用 inverse_action。"""

    def _make_result(self, action: str, pre_states: dict) -> CanaryResult:
        states = InMemoryStateProvider()
        guard = type("_Guard", (), {"states": states})()
        return CanaryResult(
            action=action,
            params={"entity_id": "light.x"},
            result=CallResult(success=True, data={}),
            pre_states=pre_states,
            guard=guard,
        )

    def test_rollback_restores_to_pre_state_on_not_inverse(self):
        """P1-10 核心：动作前灯是 on，动作 turn_on 后漂移 → 回滚应下发 turn_on（恢复原状），不是 inverse 的 turn_off。"""
        result = self._make_result(
            action="light.turn_on",
            pre_states={"light.x": "on"},  # 动作前已经是 on
        )
        adapter = _RecordingAdapter()
        result.rollback(adapter)

        # 新代码：恢复到 pre_state "on" → 下发 turn_on
        assert ("light.turn_on", {"entity_id": "light.x"}) in adapter.calls
        # 旧代码（inverse_action）会下发 turn_off——这是错误的
        assert ("light.turn_off", {"entity_id": "light.x"}) not in adapter.calls

    def test_rollback_restores_to_pre_state_off(self):
        """动作前灯是 off，动作 turn_on 后漂移 → 回滚应下发 turn_off（恢复原状）。"""
        result = self._make_result(
            action="light.turn_on",
            pre_states={"light.x": "off"},
        )
        adapter = _RecordingAdapter()
        result.rollback(adapter)
        assert ("light.turn_off", {"entity_id": "light.x"}) in adapter.calls

    def test_rollback_skips_unknown_pre_state(self):
        """pre_state 未知 → 跳过（fail-closed，不猜）。"""
        result = self._make_result(
            action="light.turn_on",
            pre_states={"light.x": None},
        )
        adapter = _RecordingAdapter()
        result.rollback(adapter)
        assert adapter.calls == []  # 不回滚

    def test_rollback_skips_unmappable_state(self):
        """pre_state 无法映射（如 climate 的 heat）→ 跳过。"""
        result = self._make_result(
            action="climate.turn_on",
            pre_states={"climate.x": "heat"},
        )
        adapter = _RecordingAdapter()
        result.rollback(adapter)
        assert adapter.calls == []
