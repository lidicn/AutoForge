# -*- coding: utf-8 -*-
"""AF-771 补丁判据 · `SCHED_PREMISE_EVAL_FAILED` 具名码（2026-11-13，AF-PM 裁定「采纳」）。

**为什么需要**：前置前提（AF-771 的入口状态前提）求值失败会被 fail-closed 判成
「前提不成立」⇒ `_auto_currently_met` 恒 False ⇒ `_dispatch_allowed` 判 `edge_held`
⇒ **整条自动化静默不派发**。这个症状与 AF-771 修的缺陷**完全同形**（不进不出、零读数），
现场没人能从日志里分出「真的条件不满足」和「求值炸了」。

⇒ 具名码 `SCHED_PREMISE_EVAL_FAILED`（WARNING，**介入＝需人介入**），**只在异常分支触发**：
`None`（不参与前提）与 `False`（确凿不成立）都是正常结论，⛔ 不许记。

**判据**：

- P1 异常分支：前提实体不可见（`UnknownEntity`）⇒ 恰好 1 条 `SCHED_PREMISE_EVAL_FAILED`，
  且仍按 fail-closed 不派发（留痕⛔ 不许改变判定）
- P2 正常分支不落痕：前提成立 / 前提不成立 / 无前提（`None`）三种正常路径 ⇒ 0 条
"""

from __future__ import annotations

import logging
from typing import Any

from autoforge.af_bus import BusEvent
from autoforge.af_ir import Graph, load_automation
from autoforge.af_runtime import build_runtime

CODE = "SCHED_PREMISE_EVAL_FAILED"
GHOST = "binary_sensor.this_entity_does_not_exist"  # 前提实体不可见 ⇒ snapshot 抛 UnknownEntity


def _auto(premise_entity: str, *, to: str | None = "on") -> dict[str, Any]:
    trig: dict[str, Any] = {"type": "state", "entity_id": premise_entity}
    if to is not None:
        trig["to"] = to
    return {
        "ir_version": "0.2.1", "id": "af771_premise_eval", "name": "AF771 前提求值留痕",
        "version": 1, "mode": "single",
        "nodes": [
            {"id": "t1", "kind": "on", "name": "人在",
             "trigger": {"type": "group", "op": "or", "sources": [trig]}},
            {"id": "i1", "kind": "if", "name": "真",
             "expr": {"op": "truthy", "value": {"var": "entity.switch.light"}}},
            {"id": "p1", "kind": "pass", "name": "结束"},
        ],
        "edges": [
            {"from": "t1", "to": "i1", "kind": "then"},
            {"from": "i1", "to": "p1", "kind": "then"},
        ],
    }


def _rt(*, premise_entity: str, to: str | None = "on", states: dict[str, str] | None = None):
    rt = build_runtime(Graph([load_automation(_auto(premise_entity, to=to))]))
    for k, v in (states or {}).items():
        rt.states.set_state(k, v)
    return rt


def _codes(caplog) -> list[str]:
    return [r.message.split()[0] for r in caplog.records if r.message.startswith(CODE)]


# ── P1 异常分支：留痕 1 条，且判定不变（仍不派发）────────────────────
def test_p1_unknown_entity_logs_named_code_once(caplog):
    """判据：前提实体不可见 ⇒ 留痕恰好 1 条；且 fail-closed 判定一个字不变。"""
    caplog.set_level(logging.WARNING)
    rt = _rt(premise_entity=GHOST, states={"switch.light": "on"})

    rt.states.set_state(GHOST, "on")  # 事件匹配入口（触发可达），但快照查不到这个实体
    rt.states.states.pop(GHOST, None)  # 让它从状态源里消失 ⇒ snapshot 抛 UnknownEntity
    fired = rt.scheduler.handle_event(BusEvent.of(GHOST, "on"))

    assert _codes(caplog) == [CODE], "异常分支必须恰好留 1 条具名码，实得 %r" % (_codes(caplog),)
    rec = next(r for r in caplog.records if r.message.startswith(CODE))
    assert rec.levelno == logging.WARNING, "级别必须是 WARNING"
    assert GHOST in rec.getMessage(), "必须点名是哪个实体（否则现场无从查起）"
    assert len(fired) == 0, "留痕⛔ 不许改变判定：前提求值失败仍按不成立处理、不派发"


# ── P2 正常路径不落痕 ─────────────────────────────────────────────────
def test_p2_normal_paths_do_not_log(caplog):
    """反证腿：`None`／`True`／`False` 三种**正常**结论⛔ 一条都不许记。

    没有这条，可以在函数入口无条件 warning 蒙成"有留痕"——那等于把日志变成噪音，
    真正的异常反而看不见。
    """
    caplog.set_level(logging.WARNING)

    # ① 前提成立（entity == "on"）
    rt = _rt(premise_entity="binary_sensor.occ", states={"binary_sensor.occ": "on", "switch.light": "on"})
    rt.scheduler.handle_event(BusEvent.of("binary_sensor.occ", "on"))
    # ② 前提不成立（entity == "off"）
    rt.states.set_state("binary_sensor.occ", "off")
    rt.scheduler.handle_event(BusEvent.of("binary_sensor.occ", "off"))
    # ③ 无前提（变化型触发：不带 to/from ⇒ 不参与）
    rt2 = _rt(premise_entity="binary_sensor.occ", to=None, states={"binary_sensor.occ": "on", "switch.light": "on"})
    rt2.scheduler.handle_event(BusEvent.of("binary_sensor.occ", "on"))

    assert _codes(caplog) == [], "正常结论⛔ 不许落痕（否则真异常被噪音淹没）"
