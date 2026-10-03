# -*- coding: utf-8 -*-
"""af_closedloop — 自修正闭环包。

符号真身在 `runtime`，这里只做转发：曾经有一份与 `runtime.py` 逐字节相同的副本躺在这里，
两份 `GuardViolation` 不是同一个类，`except` 会漏。
"""
from __future__ import annotations

from .runtime import GuardViolation, load_module, safe_call

__all__ = ["GuardViolation", "load_module", "safe_call"]
