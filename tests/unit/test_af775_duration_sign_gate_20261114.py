# -*- coding: utf-8 -*-
"""AF-775 判据 · `parse_duration` 的负号在 HH:MM:SS 分支被吃掉（2026-11-14，§1.203 裁定）。

**缺陷**：HH:MM:SS 分支拿**原始 text**（含符号）判冒号并 `split(":")`，第一个字段是 `"-00"`，
`float("-00")` 得 `-0.0` ⇒ `h*3600` 仍是 0 ⇒ `-00:10:00` 解析成 **600 秒**并静默通过负值检查
（`seconds < 0` 为假）。等于"给了个负数、系统当十分钟用"——P1-9 的 fail-closed 被绕过。

**修法**：与 ISO 分支同源——正文用已经剥好符号的 `iso_body`，结果乘 `iso_sign`，
负值照 P1-9 原话抛 ValueError。

**判据**：

- P1 `-00:10:00` ⇒ ValueError（⛔ 不再静默 600）
- P2 `00:10:00`（HA 原生、无符号）⇒ 仍 600（既有钉不回退）
- P3 ISO 分支负值行为不变（`-PT5S` ⇒ ValueError）
- P4 正号 `+00:10:00` ⇒ 600（符号剥干净后不误伤）
"""

from __future__ import annotations

import pytest

from autoforge.af_time import parse_duration


def test_p1_negative_hhmmss_is_rejected():
    """判据 a：负号必须被看见——修前这条返回 600.0，负值语义整个消失。"""
    with pytest.raises(ValueError) as ei:
        parse_duration("-00:10:00")
    assert "非负" in str(ei.value), "文案要说清是负值被拒：%r" % (str(ei.value),)


def test_p2_plain_hhmmss_still_600():
    """判据 b（既有钉）：HA 原生的无符号写法照旧 600。"""
    assert parse_duration("00:10:00") == 600.0


def test_p3_iso_negative_unchanged():
    """判据 c：ISO 分支的负值行为不变（同一条 fail-closed 纪律）。"""
    with pytest.raises(ValueError):
        parse_duration("-PT5S")
    assert parse_duration("PT5S") == 5.0


def test_p4_explicit_plus_sign_is_accepted():
    """判据（防过修）：显式正号不该被误拒——剥符号后正文仍是合法 HH:MM:SS。"""
    assert parse_duration("+00:10:00") == 600.0


def test_p5_negative_other_branches_unchanged():
    """判据（回归）：别的分支的负值仍拒、正例仍通（⛔ 修一处不能带歪邻支）。"""
    with pytest.raises(ValueError):
        parse_duration("-10m")
    assert parse_duration("10m") == 600.0
    assert parse_duration(0) == 0.0
    with pytest.raises(ValueError):
        parse_duration(-1)
