"""P1-9：parse_duration 拒绝 inf/nan/负值，支持 HA 原生 HH:MM:SS。"""

import math

import pytest

from autoforge.af_time import parse_duration


class TestParseDurationValid:
    def test_seconds_suffix(self):
        assert parse_duration("90s") == 90.0

    def test_minutes_suffix(self):
        assert parse_duration("10m") == 600.0

    def test_hours_suffix(self):
        assert parse_duration("2h") == 7200.0

    def test_days_suffix(self):
        assert parse_duration("1d") == 86400.0

    def test_pure_number(self):
        assert parse_duration(120) == 120.0
        assert parse_duration(120.5) == 120.5

    def test_pure_number_string(self):
        assert parse_duration("120") == 120.0

    def test_ha_hhmmss(self):
        # P1-9：HA 原生 00:10:00 格式
        assert parse_duration("00:10:00") == 600.0
        assert parse_duration("01:30:00") == 5400.0
        assert parse_duration("00:00:30") == 30.0

    def test_zero(self):
        assert parse_duration("0s") == 0.0
        assert parse_duration(0) == 0.0


class TestParseDurationRejectsInfNan:
    def test_reject_inf_string(self):
        with pytest.raises(ValueError, match="非负有限"):
            parse_duration("inf")

    def test_reject_nan_string(self):
        with pytest.raises(ValueError, match="非负有限"):
            parse_duration("nan")

    def test_reject_inf_float(self):
        with pytest.raises(ValueError, match="非负有限"):
            parse_duration(float("inf"))

    def test_reject_nan_float(self):
        with pytest.raises(ValueError, match="非负有限"):
            parse_duration(float("nan"))

    def test_reject_negative_inf(self):
        with pytest.raises(ValueError, match="非负有限"):
            parse_duration(float("-inf"))


class TestParseDurationRejectsNegative:
    def test_reject_negative_number(self):
        with pytest.raises(ValueError, match="非负有限"):
            parse_duration(-10)

    def test_reject_negative_string(self):
        with pytest.raises(ValueError, match="非负有限"):
            parse_duration("-10s")

    def test_reject_negative_hhmmss(self):
        with pytest.raises(ValueError, match="非负有限"):
            parse_duration("-01:00:00")


class TestParseDurationRejectsInvalid:
    def test_reject_empty(self):
        with pytest.raises(ValueError):
            parse_duration("")

    def test_reject_bad_hhmmss_parts(self):
        with pytest.raises(ValueError, match="三个部分"):
            parse_duration("10:00")

    def test_reject_garbage(self):
        with pytest.raises(ValueError):
            parse_duration("abc")
