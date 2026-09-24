"""P1-14：truthy 算子用显式 HA 语义真值表，不再 bool(value) 恒真。"""

import pytest

from autoforge.af_ir.expr import _unary


class TestTruthyHASemantics:
    def test_on_string_true(self):
        assert _unary("truthy", "on") is True

    def test_open_string_true(self):
        assert _unary("truthy", "open") is True

    def test_true_string_true(self):
        assert _unary("truthy", "true") is True

    def test_home_string_true(self):
        assert _unary("truthy", "home") is True

    def test_playing_string_true(self):
        assert _unary("truthy", "playing") is True

    def test_off_string_false(self):
        # 修复前 bool("off") = True，这是核心 bug
        assert _unary("truthy", "off") is False

    def test_closed_string_false(self):
        assert _unary("truthy", "closed") is False

    def test_false_string_false(self):
        assert _unary("truthy", "false") is False

    def test_unavailable_false(self):
        assert _unary("truthy", "unavailable") is False

    def test_unknown_false(self):
        assert _unary("truthy", "unknown") is False

    def test_empty_string_false(self):
        assert _unary("truthy", "") is False

    def test_none_false(self):
        assert _unary("truthy", None) is False

    def test_bool_true(self):
        assert _unary("truthy", True) is True

    def test_bool_false(self):
        assert _unary("truthy", False) is False

    def test_nonzero_number_true(self):
        assert _unary("truthy", 1) is True
        assert _unary("truthy", -1) is True
        assert _unary("truthy", 3.14) is True

    def test_zero_number_false(self):
        assert _unary("truthy", 0) is False
        assert _unary("truthy", 0.0) is False

    def test_case_insensitive(self):
        assert _unary("truthy", "ON") is True
        assert _unary("truthy", "Off") is False

    def test_whitespace_trimmed(self):
        assert _unary("truthy", "  on  ") is True
        assert _unary("truthy", "  off  ") is False
