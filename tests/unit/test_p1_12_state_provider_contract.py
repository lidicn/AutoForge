"""P1-12：StateProvider 契约测试——三个实现对未知实体的行为必须一致。

三个实现：
- InMemoryStateProvider（af_state.py）：未知实体 → raise UnknownEntity
- FakeHA（af_vhass/fake.py）：未知实体 → 编造域默认状态（BUG）
- HAStateProvider（af_adapters/ha.py）：未知实体 → 省略/None

契约：对未知实体，snapshot 必须 raise UnknownEntity（fail-closed），
不允许编造默认状态（那会把"实体不存在"伪装成"实体是 off"，导致条件误判）。
"""

import pytest

from autoforge.af_state import InMemoryStateProvider, UnknownEntity
from autoforge.af_vhass.fake import FakeHA


class TestInMemoryStateProviderContract:
    """InMemoryStateProvider 对未知实体抛 UnknownEntity。"""

    def test_unknown_entity_raises(self):
        sp = InMemoryStateProvider()
        with pytest.raises(UnknownEntity):
            sp.snapshot(["light.does_not_exist"])

    def test_known_entity_returns(self):
        sp = InMemoryStateProvider()
        sp.set_state("light.x", "on")
        snap = sp.snapshot(["light.x"])
        assert snap.values["light.x"] == "on"


class TestFakeHAContract:
    """FakeHA 对未知实体必须抛 UnknownEntity（P1-12 修复前会编造默认状态）。"""

    def test_unknown_entity_raises_not_default(self):
        """P1-12 核心：未知实体不能编造 'off'，必须 raise UnknownEntity。"""
        fake = FakeHA()
        # 修复前：fake.snapshot(["light.does_not_exist"]) 返回 {"light.does_not_exist": "off"}
        # 修复后：raise UnknownEntity
        with pytest.raises(UnknownEntity):
            fake.snapshot(["light.does_not_exist"])

    def test_unknown_sensor_raises(self):
        fake = FakeHA()
        with pytest.raises(UnknownEntity):
            fake.snapshot(["sensor.unknown_temperature"])

    def test_known_entity_returns(self):
        fake = FakeHA()
        fake.set("light.x", "on")
        snap = fake.snapshot(["light.x"])
        assert snap.values["light.x"] == "on"

    def test_mixed_known_unknown_raises(self):
        """只要有一个未知实体，整个 snapshot 必须 raise（不允许部分返回）。"""
        fake = FakeHA()
        fake.set("light.x", "on")
        with pytest.raises(UnknownEntity):
            fake.snapshot(["light.x", "light.does_not_exist"])


class TestStateProviderUniformity:
    """两个实现对同一场景的行为必须一致。"""

    def test_both_raise_on_unknown(self):
        """InMemoryStateProvider 和 FakeHA 对未知实体都必须 raise。"""
        inmem = InMemoryStateProvider()
        fake = FakeHA()
        with pytest.raises(UnknownEntity):
            inmem.snapshot(["light.unknown"])
        with pytest.raises(UnknownEntity):
            fake.snapshot(["light.unknown"])

    def test_both_return_known(self):
        """两个实现对已知实体都返回正确值。"""
        inmem = InMemoryStateProvider()
        inmem.set_state("light.x", "on")
        fake = FakeHA()
        fake.set("light.x", "on")
        assert inmem.snapshot(["light.x"]).values["light.x"] == "on"
        assert fake.snapshot(["light.x"]).values["light.x"] == "on"
