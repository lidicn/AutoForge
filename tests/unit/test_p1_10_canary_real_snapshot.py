"""P1-10 + F7：canary 回滚依据真实快照（pre_snapshot），不用仿真逆推（inverse_action）。

关键区分：
- 旧代码：rollback 用 inverse_action(action)——light.turn_on 的逆是 light.turn_off
- 新代码：rollback 用 pre_snapshot（state + 属性），复用 `af_undo.restore_call`
  做属性感知恢复；如果动作前灯已经是 on，回滚应下发 turn_on（恢复原状），不是 turn_off。
不可映射 / 状态未知 → fail-closed 跳过，绝不猜。
"""

import pytest

from autoforge.af_adapters import Adapter, CallResult
from autoforge.af_canary import CanaryResult
from autoforge.af_state import InMemoryStateProvider
from autoforge.af_undo import RestoreCall, restore_call


class _RecordingAdapter(Adapter):
    """记录所有 call，返回成功。"""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.dry_run = False

    def call(self, action: str, params: dict) -> CallResult:
        self.calls.append((action, dict(params)))
        return CallResult(success=True, data={})


class TestRestoreCall:
    """restore_call（DOMAIN_SETTER）单元测试——统一恢复映射。"""

    def test_on_state_maps_to_turn_on(self):
        assert restore_call("light.x", {"state": "on", "attributes": {}}) == (
            RestoreCall("light.turn_on", {"entity_id": "light.x"}))
        assert restore_call("switch.x", {"state": "open", "attributes": {}}) == (
            RestoreCall("switch.turn_on", {"entity_id": "switch.x"}))

    def test_off_state_maps_to_turn_off(self):
        assert restore_call("light.x", {"state": "off", "attributes": {}}) == (
            RestoreCall("light.turn_off", {"entity_id": "light.x"}))
        assert restore_call("switch.x", {"state": "closed", "attributes": {}}) == (
            RestoreCall("switch.turn_off", {"entity_id": "switch.x"}))

    def test_none_state_returns_none(self):
        # 状态未知：fail-closed，不猜
        assert restore_call("light.x", {"state": None, "attributes": {}}) is None

    def test_unmappable_domain_returns_none(self):
        # climate 无任何可读设定（temperature/hvac_mode 都缺）→ 不猜
        assert restore_call("climate.x", {"state": "heat", "attributes": {}}) is None
        assert restore_call("sensor.x", {"state": "20", "attributes": {}}) is None


class TestRollbackUsesRealSnapshot:
    """rollback 依据 pre_snapshot，不用 inverse_action。"""

    def _make_result(self, action: str, pre_states: dict) -> CanaryResult:
        states = InMemoryStateProvider()
        guard = type("_Guard", (), {"states": states})()
        # 真实路径下 pre_snapshot 由 CanaryGuard.perform 抓取；这里用 pre_states 构造等价快照
        pre_snapshot = {
            e: {"state": s, "attributes": {}} for e, s in pre_states.items()
        }
        return CanaryResult(
            action=action,
            params={"entity_id": "light.x"},
            result=CallResult(success=True, data={}),
            pre_states=pre_states,
            guard=guard,
            pre_snapshot=pre_snapshot,
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
        """pre_state 无法映射（如 climate 的 heat 且无温度属性）→ 跳过。"""
        result = self._make_result(
            action="climate.turn_on",
            pre_states={"climate.x": "heat"},
        )
        adapter = _RecordingAdapter()
        result.rollback(adapter)
        assert adapter.calls == []
