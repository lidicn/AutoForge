"""数值型环境变量的唯一读取路径（fail-safe，而非 fail-crash）。

背景（新增审计 BUG-10）：仓库里正确写法与错误写法并存——

* 正确：`af_bus._env_number()` 把 `float()` 包进 try，解析失败/越界回落默认并告警；
* 错误：5 处**模块级**常量直接 `float(os.getenv(...))` / `int(os.getenv(...))`。

模块级的裸解析尤其致命：它在 `import` 期求值，运维把值写错（`"12s"`、空串、
`None`、超范围）时 `import` 直接抛 ValueError/TypeError，**整个包起不来**，
且报错点离真正的配置现场很远。

本模块把正确做法提成公共路径，模块级常量也经它求值，从而「import 永远不因
配置写错而炸」。

同源纪律见 `af_bus._env_number`（P1-7 修复）；本模块是它的提升版。
"""

from __future__ import annotations

import logging
import os

__all__ = ["env_number", "env_int"]

logger = logging.getLogger("autoforge.env")


def env_number(
    name: str,
    default: float,
    *,
    lo: float | None = None,
    hi: float | None = None,
) -> float:
    """读取数值型环境变量；解析失败 / 越界时回落 `default`（并告警）。

    参数
    ----
    name:
        环境变量名。
    default:
        未设置、为空串、无法解析或越界时返回的默认值。
    lo / hi:
        可选的闭区间上下限；越界同样回落 `default`（不抛）。

    返回
    ----
    float
    """
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        val = float(raw)
    except (ValueError, TypeError):
        logger.warning("%s 解析失败（值=%r），回落默认 %s", name, raw, default)
        return default
    if val != val or val in (float("inf"), float("-inf")):  # NaN / inf
        logger.warning("%s=%r 不是有限数，回落默认 %s", name, raw, default)
        return default
    if lo is not None and val < lo:
        logger.warning("%s=%s 低于下限 %s，回落默认 %s", name, val, lo, default)
        return default
    if hi is not None and val > hi:
        logger.warning("%s=%s 高于上限 %s，回落默认 %s", name, val, hi, default)
        return default
    return val


def env_int(name: str, default: int, *, lo: int | None = None, hi: int | None = None) -> int:
    """`env_number` 的整数版；同样对解析失败/越界 fail-safe。"""
    val = env_number(name, float(default), lo=lo, hi=hi)
    try:
        return int(val)
    except (ValueError, OverflowError):  # pragma: no cover - env_number 已保证是有限数
        logger.warning("%s=%r 无法取整，回落默认 %s", name, val, default)
        return default
