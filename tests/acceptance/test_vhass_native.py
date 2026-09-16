"""在**真 vhass**（pytest-homeassistant）上复核 G1 验收的关键语义。

这是"第二道闸"的原生形态：HA 的实体模型、事件总线、太阳历全是真的，
能杜绝"仿真过了实际跑不通"。

依赖 `pytest-homeassistant-custom-component`；装不上时整文件 skip，
此时 8 条验收仍由 FakeHA 路径覆盖（见 tests/acceptance/test_case0*.py）。
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

# HA 的 `homeassistant.runner` 会 `import fcntl`（POSIX 专有），Windows 上无法加载插件。
# 因此真 vhass 只能在 Linux / WSL / Docker 里跑；Windows 上自动回落到 FakeHA 路径。
_POSIX_OK = sys.platform != "win32"

_SKIP_REASON = (
    "未安装 pytest-homeassistant-custom-component"
    if not _HA_AVAILABLE
    else "真 vhass 需要 POSIX（HA runner 依赖 fcntl），Windows 上请用 WSL/Docker 跑本文件"
)

pytestmark = [
    pytest.mark.skipif(not (_HA_AVAILABLE and _POSIX_OK), reason=_SKIP_REASON),
    pytest.mark.vhass,
]


@pytest.fixture
def case01_graph(examples_dir):
    return load_graph(examples_dir / "case01_day_light.json")


@pytest.fixture
def expected_lingering_timers() -> bool:
    """HA 测试框架会检查"残留定时器"。启用 `sun` 组件会留下平台轮询定时器，
    那是 HA 自身的行为，不是被测代码的问题——显式放行。"""
    return True


async def test_case01_in_real_vhass(hass, case01_graph, examples_dir):
    """用例 1 的真机语义复核：HA 原生实体 + 原生服务调用。"""
    from autoforge.af_vhass.harness import VhassHarness

    harness = await VhassHarness.create(
        hass,
        case01_graph,
        seed={
            "binary_sensor.study_motion": "off",
            "sensor.study_illum": "80",
            "light.study_main": "off",
        },
    )
    await harness.emit("binary_sensor.study_motion", "on")

    assert harness.state_of("light.study_main") == "on"
    assert harness.actions == ["light.turn_on"]


async def test_case03_sun_uses_native_astral(hass, freezer, examples_dir):
    """用例 3 的真机语义复核：`sun.sun` 由 HA 原生太阳历驱动（不是本地桩）。"""
    from autoforge.af_vhass.harness import VhassHarness, setup_sun

    await setup_sun(hass)
    graph = load_graph(examples_dir / "case03_night_sun.json")
    harness = await VhassHarness.create(
        hass,
        graph,
        seed={"switch.desk_pc": "off", "light.desk_lamp": "off"},
        freezer=freezer,
    )

    # 用 HA 原生太阳历推进到真正的夜晚（不依赖固定偏移，避免时区/季节边界）
    night = False
    for _ in range(25):
        if harness.state_of("sun.sun") == "below_horizon":
            night = True
            break
        await harness.advance(3600)
    assert night, "推 24 小时都没到夜晚，说明 HA 的 sun 没随虚拟时间重算"

    await harness.emit("switch.desk_pc", "on")
    assert harness.state_of("light.desk_lamp") == "on"


async def test_for_duration_with_virtual_time(hass, freezer, examples_dir):
    """用例 2 的真机语义复核：虚拟时间推进 10 分钟，`for` 才成立。"""
    from autoforge.af_vhass.harness import VhassHarness

    graph = load_graph(examples_dir / "case02_leave_for_10m.json")
    harness = await VhassHarness.create(
        hass,
        graph,
        seed={"binary_sensor.study_motion": "on", "light.study_main": "on"},
        freezer=freezer,
    )

    await harness.emit("binary_sensor.study_motion", "off")
    await harness.advance(5 * 60)
    assert harness.state_of("light.study_main") == "on", "5 分钟不该关灯"

    await harness.advance(6 * 60)  # 累计 11 分钟
    assert harness.state_of("light.study_main") == "off"
    assert harness.actions == ["light.turn_off"]
