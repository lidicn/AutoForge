# -*- coding: utf-8 -*-
"""AF-784 判据 · scheduler 快照剔 `event.*` ＋ 两段 except 补具名码（2026-11-14）。

**两处缺口**：

1. `_auto_currently_met` 的 if 段用 `snapshot(sorted(auto.reads()))`——`on event "x"` 的
   `Trigger.entity_ids()` 把事件记成 `event.x`（为跨自动化依赖矩阵看得见事件边），
   它**不属状态域**：送进 `snapshot()` 必抛 `UnknownEntity` ⇒ 整段求值失败 ⇒ met 恒 False
   ⇒ `_dispatch_allowed` 判 `edge_held` ⇒「group 入口 ＋ on event 订阅 ＋ if」形状
   **静默永不派发**。`InstanceManager._refresh_snapshot` 在 AF-781 已按同口径剔掉前缀，
   调度器这一侧漏了（同一个伪实体，两个快照点只修了一个）。
2. `_auto_currently_met` 的两段 `except Exception → return False` 是裸吞：求值炸了与
   「条件真的不成立」在盘上完全同形（都是 met=False）。沿用 AF-771 返工时立的
   `SCHED_PREMISE_EVAL_FAILED`（⛔ 不另开新码——同一条失败轴的第二、第三站）。

⚠️ **仿真掩蔽陷阱（§1.222）**：本文件的状态源是 `InMemoryStateProvider` 手工构造，
⛔ 不走 `af_simulate` fake 轨的 `seed_from_graph`（它会把伪实体注册成合法、掩盖缺陷）。

**判据**：

- H1 group 入口 ＋ on event 订阅 ＋ if ⇒ 修后能派发（修前 met 恒 False、0 条）
- H2 真实实体漂移 ⇒ 仍 fail-closed 整段软失效（语义不回退钉，与 AF-781 同一条口径）
- H3 两段 except 各落恰好 1 条 `SCHED_PREMISE_EVAL_FAILED`（且⛔ 不误报正常路径）
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import pytest

from autoforge.af_bus import BusEvent
from autoforge.af_ir import Graph, load_automation
from autoforge.af_runtime import build_runtime
from autoforge.af_time import VirtualTimeSource

CODE = "SCHED_PREMISE_EVAL_FAILED"
OCC = "binary_sensor.occupancy_main_bedroom"
LIGHT = "switch.light_main_bedroom"
PING = "af784_ping"


def _rt(ir: dict, *, states: dict[str, str] | None = None):
    rt = build_runtime(
        Graph([load_automation(ir)]),
        clock=VirtualTimeSource(datetime(2026, 11, 14, 20, 0, tzinfo=timezone.utc)),
    )
    for k, v in (states or {}).items():
        rt.states.set_state(k, v)
    return rt


def _codes(caplog) -> list[str]:
    return [r.message.split()[0] for r in caplog.records if r.message.startswith(CODE)]


#: group(or) 入口 ＋ if ＋ do —— 线上 AC 类形状；`reads()` 里会含 `event.af784_ping`
#: 只有"事件订阅"那一支进来才有，所以这里用两个入口节点（group-or 与 on event）
GROUP_ENTRY_IR: dict = {
    "ir_version": "0.2.1", "id": "af784_group", "name": "AF784 group 入口",
    "version": 1, "mode": "single",
    "nodes": [
        {"id": "t1", "kind": "on", "name": "人在",
         "trigger": {"type": "group", "op": "or",
                     "sources": [{"type": "state", "entity_id": OCC, "to": "on"},
                                 {"type": "event", "event": PING}]}},
        {"id": "i1", "kind": "if", "name": "灯开着",
         "expr": {"op": "is_on", "value": {"var": f"entity.{LIGHT}"}}},
        {"id": "d1", "kind": "do", "name": "开净化器", "adapter": "ha",
         "action": "switch.turn_on", "params": {"entity_id": "switch.purifier"}},
        {"id": "p1", "kind": "pass", "name": "结束"},
    ],
    "edges": [
        {"from": "t1", "to": "i1", "kind": "then"},
        {"from": "i1", "to": "d1", "kind": "then"},
        {"from": "i1", "to": "p1", "kind": "no"},
        {"from": "d1", "to": "p1", "kind": "then"},
    ],
}


class _Adapter:
    name = "ha"
    dry_run = False

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def call(self, action: str, params: dict):
        self.calls.append((action, dict(params)))
        from autoforge.af_adapters.base import CallResult

        return CallResult(success=True, data={}, error=None)


# ── H1 group 入口 ＋ on event 订阅 ⇒ 修后能派发 ───────────────────────
def test_h1_group_entry_with_event_subscription_dispatches(caplog):
    """判据 a：`reads()` 含 `event.af784_ping` 时，`if` 段快照⛔ 不许因伪实体塌掉。

    修前：`snapshot(sorted(auto.reads()))` 里含 `event.af784_ping` ⇒ 状态源抛
    `UnknownEntity` ⇒ 整段 `except` ⇒ met=False ⇒ `edge_held` ⇒ 派发 0 条。
    """
    caplog.set_level(logging.WARNING)
    rt = _rt(GROUP_ENTRY_IR, states={OCC: "off", LIGHT: "on"})
    rec = _Adapter()
    rt.adapters.register(rec)

    fired = rt.emit(OCC, "on")

    assert len(fired) == 1, "group 入口 ＋ if 成立 ⇒ 必须派发 1 条（修前 0 条）"
    assert rec.calls == [("switch.turn_on", {"entity_id": "switch.purifier"})], (
        "if 必须读到真实实体（灯 on）并下发动作，实得 %r" % (rec.calls,)
    )
    assert [e for e in rt.audit if str(e.type) == "instance_edge_held"] == [], (
        "⛔ 不许再被边沿闸判电平维持（那是 met 恒 False 的症状）"
    )


# ── H2 真实实体漂移仍整段软失效（语义不回退钉）──────────────────────
def test_h2_real_entity_drift_still_fail_closed(caplog):
    """判据 b：剔的只是 `event.*`；真实实体不在状态源里 ⇒ 仍按不成立处理、留痕。

    ⛔ 没有这条，可以把"未知实体静默跳过"当修法——那会把「单个实体漂移＝整段软失效」
    （P1-12／第七轮审计既裁语义）改成"只失效一支"，现场就会出现假绿。
    """
    caplog.set_level(logging.WARNING)
    rt = _rt(GROUP_ENTRY_IR, states={OCC: "off"})  # LIGHT 故意不注册 ≡ 实体被改名/删除
    rec = _Adapter()
    rt.adapters.register(rec)

    fired = rt.emit(OCC, "on")

    assert len(fired) == 0, "真实实体漂移 ⇒ 求值必然失败 ⇒ ⛔ 不许派发"
    assert rec.calls == []
    assert _codes(caplog), "求值失败必须留 SCHED_PREMISE_EVAL_FAILED（⛔ 不许裸吞）"


# ── H3 两段 except 各落一条 ──────────────────────────────────────────
def test_h3_both_except_branches_log_once(caplog):
    """判据 c：group-and 段 与 if 段 的 `except` 各落恰好 1 条，正常路径 0 条。"""
    caplog.set_level(logging.WARNING)

    # ① group-and 段：子条件里塞一个不可解析实体 ⇒ 该段 except
    ir_and = {
        "ir_version": "0.2.1", "id": "af784_and", "name": "group-and 段",
        "version": 1, "mode": "single",
        "nodes": [
            {"id": "t1", "kind": "on", "name": "两条件",
             "trigger": {"type": "group", "op": "and",
                         "sources": [{"type": "state", "entity_id": "binary_sensor.ghost", "to": "on"},
                                     {"type": "state", "entity_id": LIGHT, "to": "on"}]}},
            {"id": "p1", "kind": "pass", "name": "结束"},
        ],
        "edges": [{"from": "t1", "to": "p1", "kind": "then"}],
    }
    rt = _rt(ir_and, states={LIGHT: "on"})
    rt.scheduler._auto_currently_met(rt.graph.get("af784_and"))
    assert len(_codes(caplog)) == 1, f"group-and 段应恰好 1 条，实得 {_codes(caplog)}"
    caplog.clear()

    # ② if 段：if 里引用不可解析实体 ⇒ 该段 except
    ir_if = {
        "ir_version": "0.2.1", "id": "af784_if", "name": "if 段",
        "version": 1, "mode": "single",
        "nodes": [
            {"id": "t1", "kind": "on", "name": "入口",
             "trigger": {"type": "group", "op": "or",
                         "sources": [{"type": "state", "entity_id": OCC, "to": "on"}]}},
            {"id": "i1", "kind": "if", "name": "读不到",
             "expr": {"op": "is_on", "value": {"var": "entity.binary_sensor.ghost_if"}}},
            {"id": "p1", "kind": "pass", "name": "结束"},
        ],
        "edges": [{"from": "t1", "to": "i1", "kind": "then"},
                  {"from": "i1", "to": "p1", "kind": "then"}],
    }
    rt2 = _rt(ir_if, states={OCC: "on"})
    rt2.scheduler._auto_currently_met(rt2.graph.get("af784_if"))
    assert len(_codes(caplog)) == 1, f"if 段应恰好 1 条，实得 {_codes(caplog)}"
    caplog.clear()

    # ③ 正常路径 0 条
    rt3 = _rt(GROUP_ENTRY_IR, states={OCC: "on", LIGHT: "on"})
    rt3.scheduler._auto_currently_met(rt3.graph.get("af784_group"))
    assert _codes(caplog) == [], "正常求值⛔ 不许落痕（否则真异常被噪音淹没）"


# ── H4 源码钉：快照必须剔 event.* ────────────────────────────────────
def test_h4_source_pin_event_prefix_filtered():
    """判据（防回退）：`_auto_currently_met` 的快照集合必须过滤 `event.` 前缀。"""
    import inspect

    from autoforge import af_scheduler

    src = inspect.getsource(af_scheduler.Scheduler._auto_currently_met)
    assert 'startswith("event.")' in src, "⛔ 调度器快照必须剔 event.* 伪实体（AF-784）"
    assert "SCHED_PREMISE_EVAL_FAILED" in src, "两段 except 必须留具名码"
