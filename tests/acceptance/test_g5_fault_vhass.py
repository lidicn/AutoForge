"""G5 故障注入的真 vhass 复核（状态层 / 事件层）。

依赖 `pytest-homeassistant-custom-component`；Windows 上整文件 skip
（HA runner 依赖 `fcntl`），由 NAS Docker 环境执行。
"""

from __future__ import annotations

import importlib.util
import sys

import pytest

from autoforge.af_audit import ACTION_FAILED, ENTITY_DRIFT
from autoforge.af_ir import load_graph

_HA_AVAILABLE = all(
    importlib.util.find_spec(name) is not None
    for name in ("homeassistant", "pytest_homeassistant_custom_component")
)
_POSIX_OK = sys.platform != "win32"

pytestmark = [
    pytest.mark.skipif(
        not (_HA_AVAILABLE and _POSIX_OK),
        reason="真 vhass 需要 POSIX + pytest-homeassistant-custom-component",
    ),
    pytest.mark.vhass,
]


@pytest.fixture
def expected_lingering_timers() -> bool:
    """HA 自身会留平台轮询定时器，显式放行（见 test_vhass_native.py）。"""
    return True


async def test_unavailable_in_real_vhass(hass, examples_dir):
    """实体 unavailable → 条件求值失败 → 软失效，动作不下发。"""
    from autoforge.af_vhass.harness import VhassHarness

    graph = load_graph(examples_dir / "case01_day_light.json")
    harness = await VhassHarness.create(
        hass,
        graph,
        seed={
            "binary_sensor.study_motion": "off",
            "sensor.study_illum": "80",
            "light.study_main": "off",
        },
    )
    await harness.inject_unavailable("sensor.study_illum")
    await harness.emit("binary_sensor.study_motion", "on")

    assert harness.state_of("light.study_main") == "off", "传感器掉线时不应开灯"
    assert any(ev.type == ENTITY_DRIFT for ev in harness.runtime.audit)


async def test_reorder_replay_in_real_vhass(hass):
    """事件乱序重放 → 互不依赖的触发最终状态与顺序无关。"""
    from autoforge.af_vhass.harness import VhassHarness

    def auto(i: str) -> dict:
        return {
            "ir_version": "0.2.1",
            "id": f"auto_{i}",
            "name": f"auto_{i}",
            "version": 1,
            "mode": "single",
            "nodes": [
                {"id": "o", "kind": "on", "trigger": {"type": "state", "entity_id": f"binary_sensor.{i}", "to": "on"}},
                {"id": "d", "kind": "do", "adapter": "ha", "action": "light.turn_on", "params": {"entity_id": f"light.{i}"}},
                {"id": "p", "kind": "pass"},
            ],
            "edges": [
                {"from": "o", "to": "d", "kind": "then"},
                {"from": "d", "to": "p", "kind": "then"},
                {"from": "d", "to": "p", "kind": "on_error"},
            ],
        }

    graph = load_graph({"automations": [auto("a"), auto("b")]})
    harness = await VhassHarness.create(
        hass,
        graph,
        seed={
            "binary_sensor.a": "off",
            "binary_sensor.b": "off",
            "light.a": "off",
            "light.b": "off",
        },
    )

    await harness.replay(
        [
            {"entity_id": "binary_sensor.a", "state": "on"},
            {"entity_id": "binary_sensor.b", "state": "on"},
        ],
        seed=1,
    )

    assert harness.state_of("light.a") == "on"
    assert harness.state_of("light.b") == "on"


# ─────────────────────────────────────────────────────────────────────
# 适配器层故障（v0.2.0 补齐：HassAdapter 此前未接 FaultQueue）
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("fault_method", ["timeout_next", "drop_next", "fail_next", "unavailable_next"])
async def test_adapter_layer_fault_goes_on_error_in_real_vhass(hass, examples_dir, fault_method):
    """适配器层故障 → 单次失败 → on_error 软失效；**指令不真正下发到 HA**。

    对照 `tests/acceptance/test_g5_fault_injection.py` 的 FakeHA 等价用例
    （`test_fault_adapter_timeout_drop_device_error_go_to_on_error`），本用例把同一语义
    搬到真 vhass 路径，补齐"vhass 只覆盖状态层 / 事件层故障"的缺口。
    """
    from autoforge.af_instance import DONE
    from autoforge.af_vhass.harness import VhassHarness

    graph = load_graph(examples_dir / "case01_day_light.json")
    harness = await VhassHarness.create(
        hass,
        graph,
        seed={
            "binary_sensor.study_motion": "off",
            "sensor.study_illum": "80",
            "light.study_main": "off",
        },
    )

    getattr(harness, fault_method)("注入适配器层故障")
    await harness.emit("binary_sensor.study_motion", "on")

    assert harness.state_of("light.study_main") == "off", "故障时指令不应真正下发（灯保持关闭）"
    assert any(ev.type == ACTION_FAILED for ev in harness.runtime.audit), "设备故障应落 action_failed 审计"
    assert harness.instances[0].state == DONE, "有 on_error 兜底 → 软失效而非 failed"
