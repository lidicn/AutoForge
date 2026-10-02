"""vhass 的 skip 判据必须问"插件注册了没有"，不是"包装上了没有"。

GitHub CI 上那条永久红就是这个判据问错了对象：`homeassistant` 与
`pytest_homeassistant_custom_component` 都装得上（`[dev]` 里带着），可
`pyproject.toml` 的 `addopts = "-p no:homeassistant"` 把插件整条禁掉，
`hass` 夹具因此不存在 ⇒ 10 条真 vhass 用例在**收集期报错**而不是 skip。
"""
from __future__ import annotations

import conftest  # tests/ 目录由 pytest 放进 sys.path（prepend 导入模式）


class _PM:
    def __init__(self, mods: list[object]) -> None:
        self._mods = mods

    def get_plugins(self) -> list[object]:
        return self._mods


class _Cfg:
    def __init__(self, mods: list[object]) -> None:
        self.pluginmanager = _PM(mods)


def test_installed_but_disabled_plugin_counts_as_unavailable():
    """包装得上、插件没注册 = 没有 `hass` 夹具。判成"可用"就是那条 CI 红。"""
    assert conftest.vhass_plugin_loaded(_Cfg([])) is False


def test_container_flag_that_loads_the_plugin_counts_as_available():
    """`Dockerfile.test` 用 `-p pytest_homeassistant_custom_component.plugins` 显式加载：那条路必须判"可用"，
    否则真 vhass 复核在唯一该跑它的地方也被跳过（把红改成假绿更糟）。"""

    class _Mod:
        __name__ = "pytest_homeassistant_custom_component.plugins"

    assert conftest.vhass_plugin_loaded(_Cfg([_Mod()])) is True


def test_current_session_has_the_plugin_disabled(pytestconfig):
    """本仓默认 addopts 禁插件——当前会话判"不可用"，上面两条才不是空转。"""
    assert conftest.vhass_plugin_loaded(pytestconfig) is False, (
        "pytest-homeassistant 插件被加载了：`-p no:homeassistant` 已失效，"
        "真 vhass 用例会在 CI 上真跑，请复核本判据与 skip 理由是否还成立"
    )
