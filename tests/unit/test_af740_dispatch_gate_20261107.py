# -*- coding: utf-8 -*-
"""AF-740 判据 · 「条件不满仍派发」＋「生命周期日志部署面零出声」收口（2026-11-07）· 卡名 `AF卡_af740_dispatch_gate`。

**现场**（台账 §1.180，v1.28.0/`e87e646` 线上）：`study_ac_smart_v6.runtime.json` 的
`last_dispatched_at` 今晚 20:17:58→20:28:32→20:40:38 反复推进，但当时 `last_met=false`
（TH2 26.5<28.5）、inbox 零新行、watch.log `INSTANCE_LIFECYCLE` 计数=0——与「`record_dispatched`
唯一调用点＝spawn 出口」「spawn 必落 lifecycle」两条契约直接矛盾。

**根因（两腿）**：

1. **`_dispatch_allowed` 漏「持续不满」抑制**：D10 边沿语义是「不满→满足的跳变沿才派发」，
   但代码只写 `if met and prev: return edge_held`（持续满足抑制），**漏了 `met=False` 分支**。
   `met=False`（条件不满）时返回 `(True,"")` 放行 ⇒ `_try_fire` 照 spawn、`last_dispatched_at`
   照写、executor 跑 `i1` 走 no 边零动作收尾（`instance_no_action`）。v6 是
   `on(group or 六实体任一变化) → if(and 五条件)` 形状，`_auto_currently_met` 走 if 节点求值
   返回 bool ⇒ 条件不满也触发 spawn。
2. **watch 子进程 logging 无 handler**：`_lifecycle` 用 `logger.info`（INFO 级），但 watch 子进程
   （`forge watch`，由 `af_service.start_watch` 以 `stdout/stderr → watch.log` 起）root logger
   无 handler ⇒ INFO 被丢弃（Python 默认只在 WARNING+ 走 lastResort）。spawn 真发生了但
   `INSTANCE_LIFECYCLE` 日志没落盘。

**牙**（群规⑧）：L1 用「首次不满不派发」做差分——旧实现 `met=False` 也写 `last_dispatched_at`
（复现实证），修复后不写；L2/L3 是跳变沿/持续满足两态，钉「真沿必派发、维持必抑制」不互相
塌缩成恒绿；L5 用 caplog capture stderr 验证 `_lifecycle` 配 handler 后真出声（⛔ 不只要内存
logger，要真产出字符串行）；L4/L6 是源码钉。
"""

from __future__ import annotations

import sys
from typing import Any

import pytest

from autoforge.af_bus import BusEvent
from autoforge.af_ir import Graph, load_automation
from autoforge.af_runtime import build_runtime
from autoforge.af_runtime_facts import RuntimeFacts

TEMP = "sensor.temp"
ROOM = "书房"


class _Adapter:
    name = "climate"
    dry_run = False

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def call(self, action: str, params: dict):
        self.calls.append((action, dict(params)))
        from autoforge.af_adapters.base import CallResult

        return CallResult(success=True, data={}, error=None)


#: v6 同形：`on(group-or 任一变化) → if(温度>28.5)` —then→ do / —no→ pass。
#: `_auto_currently_met` 对 group-or 入口走 if 节点求值返回 bool（不是 None）。
AUTO_V6: dict[str, Any] = {
    "ir_version": "0.2.1", "id": "af740", "name": "AF740 v6 同形", "version": 1,
    "mode": "single",
    "nodes": [
        {"id": "a1", "kind": "on", "name": "任一变化",
         "trigger": {"type": "group", "op": "or", "sources": [
             {"type": "state", "entity_id": "binary_sensor.door"},
             {"type": "state", "entity_id": TEMP},
         ]}},
        {"id": "i1", "kind": "if", "name": "温度>28.5",
         "expr": {"op": "gt", "left": {"var": f"entity.{TEMP}", "type": "numeric"},
                  "right": {"const": 28.5}}},
        {"id": "d1", "kind": "do", "name": "开空调", "adapter": "climate",
         "action": "set_hvac_mode",
         "params": {"entity_id": "climate.x", "hvac_mode": "cool"}, "room": ROOM,
         "announce": "把空调打开"},
        {"id": "p1", "kind": "pass", "name": "结束"},
    ],
    "edges": [
        {"from": "a1", "to": "i1", "kind": "then"},
        {"from": "i1", "to": "d1", "kind": "then"},
        {"from": "i1", "to": "p1", "kind": "no"},
        {"from": "d1", "to": "p1", "kind": "then"},
    ],
}


def _runtime(auto, *, store_root=None, states=None):
    rt = build_runtime(Graph([load_automation(auto)]), store_root=store_root)
    rt.adapters.register(_Adapter())
    for k, v in (states or {}).items():
        rt.states.set_state(k, v)
    return rt


def _types(rt) -> list[str]:
    return [str(e.type) for e in rt.audit]


# ── L1 腿①红→绿：条件不满（met=False）⛔ 不派发、不写 last_dispatched_at ──

def test_l1_met_false_does_not_dispatch(tmp_path):
    """v6 同形：温度 26<28.5（met=False）触发 ⇒ ⛔ 不 spawn、不写派发时刻。

    旧实现（AF-740 根因）：`met=False` 时 `_dispatch_allowed` 返回 True 放行 ⇒
    `last_dispatched_at` 被写、executor 走 no 边零动作（instance_no_action）。修复后：
    抑制（instance_edge_held）、`last_dispatched_at` 为 None。
    """
    rt = _runtime(AUTO_V6, store_root=str(tmp_path),
                  states={"binary_sensor.door": "on", TEMP: "26"})
    rt.scheduler.handle_event(BusEvent.of("binary_sensor.door", "on"))
    f = RuntimeFacts(str(tmp_path), AUTO_V6["id"]).read()
    assert f.get("last_dispatched_at") is None, \
        "条件不满 ⛔ 不许写 last_dispatched_at（AF-740 根因：不满也派发）"
    assert f.get("last_met") is False, "条件布尔仍要记（不满是事实）"
    assert "instance_edge_held" in _types(rt), "条件不满应被边沿闸抑制（电平维持）"
    assert "instance_no_action" not in _types(rt), \
        "不满不 spawn ⇒ 不该有零动作审计（那是真派发后才有的）"


def test_l2_edge_rise_dispatches(tmp_path):
    """反证腿：跳变沿（不满→满足）**必须**派发——否则 L1 退化成"从不派发"的假绿。"""
    rt = _runtime(AUTO_V6, store_root=str(tmp_path),
                  states={"binary_sensor.door": "on", TEMP: "26"})
    # ① 首次不满：不派发（前置状态，让 prev=False 落盘）
    rt.scheduler.handle_event(BusEvent.of("binary_sensor.door", "on"))
    assert RuntimeFacts(str(tmp_path), AUTO_V6["id"]).read().get("last_dispatched_at") is None
    # ② 跳变沿：温度升到 30 > 28.5 ⇒ 必须派发
    rt.states.set_state(TEMP, "30")
    rt.scheduler.handle_event(BusEvent.of(TEMP, "30"))
    f = RuntimeFacts(str(tmp_path), AUTO_V6["id"]).read()
    assert f.get("last_dispatched_at"), "跳变沿（不满→满足）必须写派发时刻"
    assert f.get("last_met") is True
    assert "af_action_called" in _types(rt), "真派发应真下发动作"


def test_l3_edge_held_does_not_refresh_dispatch(tmp_path):
    """持续满足（电平维持）⇒ 抑制、⛔ 不刷新派发时刻（与 AF-739 L1 同口径的另一半）。"""
    rt = _runtime(AUTO_V6, store_root=str(tmp_path),
                  states={"binary_sensor.door": "on", TEMP: "30"})
    rt.scheduler.handle_event(BusEvent.of("sensor.temp", "30"))
    first = RuntimeFacts(str(tmp_path), AUTO_V6["id"]).read().get("last_dispatched_at")
    assert first, "首次满足应派发（前置）"
    # 持续满足再触发 ⇒ edge_held，派发时刻不动
    rt.scheduler.handle_event(BusEvent.of("sensor.temp", "30"))
    second = RuntimeFacts(str(tmp_path), AUTO_V6["id"]).read().get("last_dispatched_at")
    assert second == first, "持续满足 ⛔ 不许刷新 last_dispatched_at"
    assert "instance_edge_held" in _types(rt)


# ── L4 源码钉：_dispatch_allowed 必须有"不满不派发"语句 ──

def test_l4_dispatch_allowed_has_not_met_guard():
    import inspect

    from autoforge import af_scheduler
    src = inspect.getsource(af_scheduler.Scheduler._dispatch_allowed)
    # 不满（met 为 False）必须返回抑制，⛔ 不许漏掉 while 落回"放行"
    assert "if not met:" in src, "_dispatch_allowed 必须显式拦条件不满（AF-740 腿①）"
    assert 'return False, "edge_held"' in src


# ── L5 腿②红→绿：配 handler 后 _lifecycle 真出声（stderr 捕获）──

def test_l5_lifecycle_emits_to_stderr_with_handler(caplog):
    """`_lifecycle`（logger.info，INFO）在配了 handler 后必须真出声。

    ⛔ 不只是"logger 有对象"——要真产出 `INSTANCE_LIFECYCLE` 的 record（配了 INFO handler
    才算出声）。这是"部署面真文件出声"的本地等价腿：watch 子进程把 stderr 重定向进
    watch.log，caplog 捕获的就是那条会被落盘的行。
    """
    import logging
    rt = _runtime(AUTO_V6, states={"binary_sensor.door": "on", TEMP: "30"})
    with caplog.at_level("INFO", logger="autoforge.executor"):
        rt.scheduler.handle_event(BusEvent.of("sensor.temp", "30"))
    hits = [r for r in caplog.records
            if "INSTANCE_LIFECYCLE" in (r.getMessage() if r.getMessage() else "")]
    assert hits, \
        "_lifecycle 配 INFO handler 后必须真出声（否则 watch.log 部署面零痕迹）"


# ── L6 源码钉：forge watch 命令体必须给 logging 配 stderr handler ──

def test_l6_forge_watch_configures_logging():
    import inspect

    from autoforge import af_cli
    src = inspect.getsource(af_cli.watch)
    assert "logging.basicConfig" in src, "forge watch 必须配 logging（AF-740 腿②）"
    assert "stream=sys.stderr" in src, "handler 必须接 stderr（由 start_watch 重定向进 watch.log）"
    assert "level=logging.INFO" in src, "必须 INFO 级（否则 INSTANCE_LIFECYCLE 仍被丢）"