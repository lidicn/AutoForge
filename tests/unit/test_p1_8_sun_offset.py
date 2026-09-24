"""P1-8：normalize_trigger 归一化 sun 触发时保留 offset 字段（不丢弃）。"""

import pytest

from autoforge.af_ir.models import Trigger
from autoforge.af_scheduler import normalize_trigger


class TestSunOffsetPassthrough:
    def test_sunset_with_offset_preserved(self):
        """P1-8：日落前 15 分钟 → 归一化后 offset 仍为 -15m。"""
        trig = Trigger(type="sun", event="sunset", offset="-15m")
        norm = normalize_trigger(trig)
        assert norm.type == "state"
        assert norm.entity_id == "sun.sun"
        assert norm.to == "below_horizon"
        assert norm.offset == "-15m"

    def test_sunrise_with_offset_preserved(self):
        """日出后 30 分钟 → 归一化后 offset 仍为 30m。"""
        trig = Trigger(type="sun", event="sunrise", offset="30m")
        norm = normalize_trigger(trig)
        assert norm.type == "state"
        assert norm.to == "above_horizon"
        assert norm.offset == "30m"

    def test_sun_without_offset_none(self):
        """无 offset 时归一化后 offset 为 None。"""
        trig = Trigger(type="sun", event="sunset")
        norm = normalize_trigger(trig)
        assert norm.offset is None

    def test_non_sun_trigger_unchanged(self):
        """非 sun 触发不受影响。"""
        trig = Trigger(type="state", entity_id="switch.test", to="on")
        norm = normalize_trigger(trig)
        assert norm is trig
        assert norm.type == "state"
