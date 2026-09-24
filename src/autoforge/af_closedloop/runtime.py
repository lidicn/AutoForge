# -*- coding: utf-8 -*-
"""runtime — 只读加载 af_orchestrator，以及与 Orchestrator._call 同语义的 approve 硬护栏。"""
from __future__ import annotations

import importlib
import pathlib
import sys
from typing import Any

_MODULE = "autoforge.af_orchestrator"
_cache: dict[str, Any] = {}


class GuardViolation(RuntimeError):
    """调用 approve* 工具：与 Orchestrator._call 的硬护栏同语义。"""


def load_module():
    mod = _cache.get("mod")
    if mod is not None:
        return mod
    try:
        mod = importlib.import_module(_MODULE)
    except ImportError:
        here = pathlib.Path(__file__).resolve()
        for cand in (here.parents[2], here.parents[3] / "src", here.parents[1]):
            if (cand / "autoforge" / "af_orchestrator.py").exists():
                sys.path.insert(0, str(cand))
                break
        mod = importlib.import_module(_MODULE)
    _cache["mod"] = mod
    return mod


def guard_tool(tool: str) -> str:
    if str(tool).strip().lower().startswith("approve"):
        raise GuardViolation(f"自修正闭环禁止调用批准类工具: {tool}")
    return tool


def safe_call(mcp, tool: str, **kwargs):
    guard_tool(tool)
    return mcp.call(tool, **kwargs)
