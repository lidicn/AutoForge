# -*- coding: utf-8 -*-
"""AF-739 判据 · 「spawn 后不执行」的可观测性收口（2026-11-07）· 卡名 `AF卡_af739_lifecycle`。

**现场**（台账 §1.178）：10:19:37 v6 派发记录在案（`last_dispatched_at`），但 ①空调实态 off
②DB 当日播报 0 条 ③trial 停昨晚未动 ⇒ 实例 spawn 后卡在 spawn→d1 之间，而盘上**零痕迹**
（实例只存内存、执行器不落生命周期日志）。

**本卡钉两件事**：

1. **事实层不再撒谎**（根因之一）：旧实现 `_dispatch_allowed` 在判定「电平维持⇒不派发」**之前**
   就 `record_met(met=True)`，而 `record_met` 顺手写 `last_dispatched_at` ⇒ **被抑制的那一轮也
   刷新"已派发"时间戳**。修法＝派发时刻只由唯一真派发点（`_try_fire` spawn 出口）写
   （`RuntimeFacts.record_dispatched`）。
2. **零动作／无边必须可见**：新增审计 `instance_no_action`（有 do 节点却一次动作都没下发）
   ＋ `instance_no_edge`（选不出一条可用边），并给实例加 `actions` 真下发计数；
   另加 `INSTANCE_LIFECYCLE` 阶段日志（spawn/segment/suspend/resume/complete/cancel/fail/no_edge）。

**牙**（群规⑧）：L1 用**递增假钟**做差分（同一微秒下"刷新"与"不刷新"不可分 ⇒ 会变成恒绿）；
L3/L5 是同一张图的两个状态（if 假 ⇒ 必须报零动作；if 真 ⇒ ⛔ 不许报）。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
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


#: group-and 入口（走边沿闸）：两条状态条件同时为真才算 met。
AUTO_EDGE: dict[str, Any] = {
    "ir_version": "0.2.1", "id": "af739_edge", "name": "AF739 边沿用例", "version": 1,
    "mode": "restart",
    "nodes": [
        {"id": "a1", "kind": "on", "name": "门关且开关开",
         "trigger": {"type": "group", "op": "and", "sources": [
             {"type": "state", "entity_id": "binary_sensor.door", "to": "off"},
             {"type": "state", "entity_id": "switch.ac", "to": "on"},
         ]}},
        {"id": "d1", "kind": "do", "name": "开空调", "adapter": "climate", "action": "set_hvac_mode",
         "params": {"entity_id": "climate.x", "hvac_mode": "cool"}, "room": ROOM,
         "announce": "把空调打开"},
        {"id": "p1", "kind": "pass", "name": "结束"},
    ],
    "edges": [
        {"from": "a1", "to": "d1", "kind": "then"},
        {"from": "d1", "to": "p1", "kind": "then"},
    ],
}


def _if_auto() -> dict[str, Any]:
    """`on`(单条件) → `if`(温度>28.5) —then→ do / —no→ pass：v6 的简化同形。"""
    return {
        "ir_version": "0.2.1", "id": "af739_if", "name": "AF739 if 用例", "version": 1,
        "mode": "restart",
        "nodes": [
            {"id": "a1", "kind": "on", "name": "有人",
             "trigger": {"type": "state", "entity_id": "binary_sensor.motion", "to": "on"}},
            {"id": "i1", "kind": "if", "name": "条件",
             "expr": {"op": "gt", "left": {"var": f"entity.{TEMP}", "type": "numeric"},
                      "right": {"const": 28.5}}},
            {"id": "d1", "kind": "do", "name": "开空调", "adapter": "climate", "action": "set_hvac_mode",
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


# ── L1 事实层不撒谎：边沿抑制 ⛔ 不刷新 last_dispatched_at（递增假钟做差分）──

def test_l1_edge_held_does_not_refresh_last_dispatched_at(tmp_path, monkeypatch):
    """真派发写一次；随后同条件再命中（电平维持）⇒ 时间戳**不动**。

    假钟每次 `now()` +60s：若实现回退成"record_met 顺带写派发时刻"，L1 会读到被刷新的时间戳。
    """
    import autoforge.af_runtime_facts as facts_mod

    ticks = {"n": 0}

    class _FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            base = datetime(2026, 11, 7, 10, 19, 37, tzinfo=timezone.utc)
            ticks["n"] += 1
            return base + timedelta(seconds=60 * ticks["n"])

    monkeypatch.setattr(facts_mod, "datetime", _FakeDT)

    rt = _runtime(AUTO_EDGE, store_root=str(tmp_path),
                  states={"binary_sensor.door": "on", "switch.ac": "on"})
    # ① 先"断"：door 开着 ⇒ 条件不满，handle_event 记 met=False
    rt.publish(BusEvent.of("binary_sensor.door", "on"))
    # ② 再"立"：door 关上（状态源同步更新）⇒ 两条都满足 ⇒ 跳变沿 ⇒ 真派发
    rt.states.set_state("binary_sensor.door", "off")
    rt.publish(BusEvent.of("binary_sensor.door", "off"))
    f = RuntimeFacts(str(tmp_path), AUTO_EDGE["id"])
    first = f.read().get("last_dispatched_at")
    assert first, "真派发必须写 last_dispatched_at"
    assert "instance_edge_held" not in _types(rt)

    # ③ 同条件再来一次 ⇒ 电平维持（edge_held）：不派发、⛔ 不刷新派发时刻
    #    ⚠️ 走 scheduler.handle_event 而非 rt.publish：总线对**同一条重复事件**有去重/节流，
    #    这里要量的是**派发闸**的边沿语义，不是总线的去重（那是另一层、另有判据）。
    rt.scheduler.handle_event(BusEvent.of("binary_sensor.door", "off"))
    assert "instance_edge_held" in _types(rt), "第二次命中应被边沿闸抑制（前置条件）"
    assert f.read().get("last_dispatched_at") == first, \
        "被抑制的那一轮⛔ 不许刷新 last_dispatched_at（AF-739 根因：事实层撒谎）"
    # last_met 仍在推进（条件布尔是事实，每轮都记）
    assert f.read().get("last_met") is True


def test_l1b_real_dispatch_updates_last_dispatched_at(tmp_path, monkeypatch):
    """反证腿：真派发那一次**必须**写（否则 L1 会退化成"从不写"的假绿）。"""
    import autoforge.af_runtime_facts as facts_mod

    ticks = {"n": 0}

    class _FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            base = datetime(2026, 11, 7, 10, 19, 37, tzinfo=timezone.utc)
            ticks["n"] += 1
            return base + timedelta(seconds=60 * ticks["n"])

    monkeypatch.setattr(facts_mod, "datetime", _FakeDT)
    rt = _runtime(AUTO_EDGE, store_root=str(tmp_path),
                  states={"binary_sensor.door": "off", "switch.ac": "on"})
    rt.publish(BusEvent.of("binary_sensor.door", "off"))
    stamp = RuntimeFacts(str(tmp_path), AUTO_EDGE["id"]).read().get("last_dispatched_at")
    assert stamp, "真派发必须留下派发时刻"
    assert "instance_no_action" not in _types(rt), "本例 do 真跑了，⛔ 不该报零动作"


# ── L2 生命周期日志：spawn 必须有阶段行（AF-739 的"盘上零痕迹"收口）──────

def test_l2_spawn_and_complete_are_logged(caplog):
    rt = _runtime(AUTO_EDGE, states={"binary_sensor.door": "off", "switch.ac": "on"})
    with caplog.at_level("INFO"):
        rt.publish(BusEvent.of("binary_sensor.door", "off"))
    line = caplog.text
    assert "INSTANCE_LIFECYCLE stage=spawn" in line, f"spawn 阶段必须落日志，实得：{line[:400]}"
    assert "stage=complete" in line, "终态必须落一条 complete（否则又回到「只存内存」）"
    assert "actions=" in line, "生命周期行要带真下发计数（零动作现场一眼可见）"


# ── L3/L5 零动作码：if 假 ⇒ 必须报；if 真 ⇒ ⛔ 不许报 ────────────────────

def test_l3_if_false_reports_no_action_audit():
    """a1 命中但 i1 为假 ⇒ 走 no 边到 p1 收尾：**合法路径、零动作**，必须留一条码。

    这就是 AF-739 现场形状的可复现版：派发过、什么也没发生、此前零痕迹。
    """
    rt = _runtime(_if_auto(), states={"binary_sensor.motion": "off", TEMP: "20.0"})
    rt.publish(BusEvent.of("binary_sensor.motion", "on"))
    assert "instance_no_action" in _types(rt), \
        "有 do 节点却零动作收尾必须落 instance_no_action（AF-739 的观测洞）"
    ev = [e for e in rt.audit if e.type == "instance_no_action"][-1]
    assert ev.instance_id and ev.automation_id == "af739_if"


def test_l5_action_dispatched_does_not_report_no_action():
    """反证腿：同一张图、i1 为真 ⇒ d1 真下发 ⇒ ⛔ 不许报零动作（否则该码恒亮=没牙）。"""
    rt = _runtime(_if_auto(), states={"binary_sensor.motion": "off", TEMP: "30.0"})
    rt.publish(BusEvent.of("binary_sensor.motion", "on"))
    assert "instance_no_action" not in _types(rt), "真下发过动作⛔ 不该报零动作"
    inst = rt.instances.all()[0]
    assert int(inst.ctx.actions) == 1, f"真下发计数应为 1，实得 {inst.ctx.actions}"


# ── L4 选边失败必须可见（此前静默终止）─────────────────────────────────

def test_l4_no_edge_is_audited_instead_of_silent():
    auto = _if_auto()
    # 把 i1 的 no 边摘掉 ⇒ i1 为假时选不出边（旧实现静默 _terminate，盘上零痕迹）
    auto["edges"] = [e for e in auto["edges"]
                     if not (e["from"] == "i1" and e["kind"] == "no")]
    rt = _runtime(auto, states={"binary_sensor.motion": "off", TEMP: "20.0"})
    rt.publish(BusEvent.of("binary_sensor.motion", "on"))
    assert "instance_no_edge" in _types(rt), "选不出边必须落 instance_no_edge（⛔ 不许静默终止）"
    ev = [e for e in rt.audit if e.type == "instance_no_edge"][-1]
    assert ev.node_id == "i1"
    assert "no" in ev.data.get("kinds", []), "证据里要写清当时可选的边类型"


# ── L6 源码钉：派发时刻只有一个写点，且它在 spawn 之后 ──────────────────

def test_l6_single_dispatch_stamp_writer():
    import inspect

    from autoforge import af_runtime_facts, af_scheduler

    src_facts = inspect.getsource(af_runtime_facts)
    assert "def record_dispatched" in src_facts
    # `record_met` ⛔ 不许再写派发时刻（AF-739 根因就在这行）
    met_src = inspect.getsource(af_runtime_facts.RuntimeFacts.record_met)
    assert 'data["last_dispatched_at"] = ' not in met_src, \
        "record_met 又写起了 last_dispatched_at ⇒ 事实层撒谎回归"
    fire_src = inspect.getsource(af_scheduler.Scheduler._try_fire)
    assert "_record_dispatched" in fire_src
    spawn_at = fire_src.find("instances.spawn")
    stamp_at = fire_src.find("_record_dispatched")
    assert 0 <= spawn_at < stamp_at, "派发时刻必须在 spawn 之后写（唯一真派发点）"
