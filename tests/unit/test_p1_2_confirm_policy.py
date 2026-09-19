"""P1-2：requires_confirm 服务端策略表——L2 动作不可自我豁免。

漏洞：L2 动作（门锁/窗帘/空调）设 requires_confirm=True 即可通过 scanner，
但执行器完全忽略 requires_confirm——设了也白设，等于免费豁免。

修复：L2 动作 requires_confirm=True 必须配 canary 灰度（服务端策略表强制），
否则 scanner 报错。requires_confirm 不再是免费通行证。
"""

import pytest

from autoforge.af_ir import Graph, load_automation
from autoforge.af_scanner import StaticScanner


def _ir(do_node: dict) -> dict:
    return {
        "ir_version": "0.2.1",
        "id": "test",
        "name": "测试",
        "version": 1,
        "mode": "single",
        "nodes": [
            {"id": "a1", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
            do_node,
            {"id": "p1", "kind": "pass"},
        ],
        "edges": [
            {"from": "a1", "to": "d1", "kind": "then"},
            {"from": "d1", "to": "p1", "kind": "then"},
        ],
    }


def _scan(data: dict):
    scanner = StaticScanner(graph=Graph([load_automation(data)]))
    return scanner.scan()


class TestL2ConfirmPolicy:
    """L2 动作的确认策略。"""

    def test_l2_without_requires_confirm_fails(self):
        """L2 动作不设 requires_confirm → 报错（原有行为）。"""
        data = _ir({
            "id": "d1", "kind": "do", "adapter": "ha",
            "action": "lock.lock", "params": {"entity_id": "lock.front"},
        })
        result = _scan(data)
        assert any(d.code == "L2_NEEDS_CONFIRM" for d in result.diagnostics)

    def test_l2_requires_confirm_without_canary_fails_p1_2(self):
        """P1-2 核心：L2 动作设 requires_confirm=True 但无 canary → 报错（不可免费豁免）。"""
        data = _ir({
            "id": "d1", "kind": "do", "adapter": "ha",
            "action": "lock.lock", "params": {"entity_id": "lock.front"},
            "requires_confirm": True,
            # 没有 canary 配置
        })
        result = _scan(data)
        # P1-2 修复后：应该有 L2_NEEDS_CANARY 或类似错误
        assert any("canary" in d.code.lower() or "CANARY" in d.code for d in result.diagnostics), \
            f"L2 requires_confirm=True 无 canary 应该报错，实际 diagnostics: {[d.code for d in result.diagnostics]}"

    def test_l2_requires_confirm_with_canary_passes(self):
        """L2 动作 requires_confirm=True + canary → 通过。"""
        data = _ir({
            "id": "d1", "kind": "do", "adapter": "ha",
            "action": "climate.turn_on", "params": {"entity_id": "climate.study"},
            "requires_confirm": True,
            "canary": {"duration": "5m", "auto_rollback": True},
        })
        result = _scan(data)
        assert not any(d.code == "L2_NEEDS_CONFIRM" for d in result.diagnostics)
        assert not any("CANARY" in d.code for d in result.diagnostics)

    def test_l1_action_no_confirm_needed(self):
        """L1 动作（灯/开关）不需要 requires_confirm。"""
        data = _ir({
            "id": "d1", "kind": "do", "adapter": "ha",
            "action": "light.turn_on", "params": {"entity_id": "light.study"},
        })
        result = _scan(data)
        assert not any(d.code == "L2_NEEDS_CONFIRM" for d in result.diagnostics)
