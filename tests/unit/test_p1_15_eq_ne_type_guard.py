"""P1-15：eq/ne 加类型守卫，防止数值/字符串间漂移。"""

import pytest

from autoforge.af_ir.expr import _compare, ExprError


class TestEqNeTypeGuard:
    def test_eq_same_type_numbers(self):
        assert _compare("eq", 255, 255) is True
        assert _compare("eq", 255, 256) is False

    def test_eq_int_float(self):
        """int vs float 允许（同为数值）。"""
        assert _compare("eq", 1, 1.0) is True

    def test_eq_same_type_strings(self):
        assert _compare("eq", "on", "on") is True
        assert _compare("eq", "on", "off") is False

    def test_eq_bool(self):
        assert _compare("eq", True, True) is True
        assert _compare("eq", False, False) is True
        assert _compare("eq", True, False) is False

    def test_eq_none_allowed(self):
        """None 可与任何类型比较。"""
        assert _compare("eq", None, None) is True
        assert _compare("eq", None, "off") is False
        assert _compare("eq", 0, None) is False

    def test_eq_rejects_string_number(self):
        """P1-15：字符串 vs 数字必须拒绝（防止 255 == "255" 漂移）。"""
        with pytest.raises(ExprError, match="类型不一致"):
            _compare("eq", 255, "255")

    def test_eq_rejects_bool_int(self):
        """P1-15：bool vs int 必须拒绝（防止 True == 1）。"""
        with pytest.raises(ExprError, match="bool"):
            _compare("eq", True, 1)

    def test_ne_rejects_string_number(self):
        with pytest.raises(ExprError, match="类型不一致"):
            _compare("ne", 255, "255")

    def test_ne_same_type(self):
        assert _compare("ne", "on", "off") is True
        assert _compare("ne", "on", "on") is False
