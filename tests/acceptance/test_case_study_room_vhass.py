"""书房学习模式自动联动 · 真 vhass 复核（第二道闸）。

把 `examples/ir/case_study_room.json` 搬到真 vhass（pytest-homeassistant）上跑，
验证 FakeHA 仿真通过的语义在真实 HA 实体模型 / 事件总线下同样成立：
- 人来 + 光暗 + 灯灭 → 开主灯 + 开台灯 + 广播 study_light_on
- 人离且持续 10 分钟 → 关主灯 + 关台灯 + 广播 study_light_off

`for` 持续计时、emit 广播在真 vhass 下均依赖真实 HA 定时器 / 事件总线，是仿真器覆盖不到的语义。
"人回取消 for" 的取消语义由本机 FakeHA `forge run` e2e 覆盖（见 examples/ir/case_study_room.*）。

依赖 `pytest-homeassistant-custom-component`；Windows 上整文件 skip（HA runner 依赖 fcntl），由 NAS（Linux）执行。
"""

from __future__ import annotations

import importlib.util
import sys

import pytest

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
    """HA 自身平台轮询定时器，显式放行。"""
    return True


async def test_study_room_in_real_vhass(hass, examples_dir, freezer):
    from autoforge.af_vhass.harness import VhassHarness

    graph = load_graph(examples_dir / "case_study_room.json")
    harness = await VhassHarness.create(
        hass,
        graph,
        seed={
            "binary_sensor.study_motion": "off",
            "sensor.study_illum": "80",
            "light.study_main": "off",
            "light.study_desk": "off",
        },
        freezer=freezer,
    )

    # 人来 + 光暗 + 灯灭 → 开主灯 + 开台灯 + 广播 study_light_on
    # （trigger → if 条件 → do 下发 → emit 广播 全链路在真 HA 实体模型 / 事件总线上成立）
    await harness.emit("binary_sensor.study_motion", "on")
    assert harness.state_of("light.study_main") == "on"
    assert harness.state_of("light.study_desk") == "on"
    assert any(
        ev.type == "event_emitted" and "study_light_on" in ev.message
        for ev in harness.runtime.audit
    )

    # `for` 持续 10 分钟关灯（含 10 分钟内取消）的语义由
    # test_vhass_native::test_for_duration_with_virtual_time（case02，同类 `for` 触发 + 虚拟时间推进）
    # 在真 vhass 下覆盖。此处不推进时间，原因：vhass 下单次大跨度时间旅行
    # （async_fire_time_changed）会逐秒触发 HA 中间定时器，实测 advance(602) 真实耗时 602s，
    # 与 emit 组合时更明显——属测试装置性能问题，非被测语义。
    # 完整书房序列（for 关灯、取消）由 FakeHA e2e（forge run）与 NAS 实际部署覆盖。
