# -*- coding: utf-8 -*-
"""T1 判据 · 非 ask 自动化的 do 动作下发前经 MQTT 播报（2026-11-04）· 卡名 AF卡_do_speak。

出资人缺陷：`emit` 节点只走 AF 内部事件总线（af_bus），到不了 butler 的 `butler/inbox/speak`；
ask 路径的播报（467108c）只覆盖 ask 节点 ⇒ 普通自动化条件命中后没有播报。

修法（⛔ 不改 do 核心）：`_do` 在动作下发前调 `on_do_speak(instance_id, text, room)`，
Runtime 注入闭包 → `bridge.publish_speak`。dry-run 适配器不播报；失败只吞 warning。

★ v1.27.1 语义收口（2026-11-06，PM 卡 `AF卡_v1271_热修_播报语义收口_20261106.md`）：
播报改**显式才出声**——`speech_template` 渲出**或** `announce` 非空才播；两者都没有 ⇒ **静默**
（删掉旧机拼兜底「自动化名：节点名」）。D4 因此由"兜底非空"翻成"必须静默"。

覆盖（编号带卡名）：
  D1 do 动作照常执行（开关行）：注入播报回调后 adapter 仍被调用、边选择不变
  D2 do 下发前触发播报（trace_id=instance_id、room=node.room）
  D3 文本取节点 `announce`（IR 扩展字段）原样播报
  D4 无 announce 且无模板 ⇒ 静默（v1.27.1 新契约；旧兜底已删）
  D5 dry-run 适配器不播报（没真动作却出声会误导）
  D6 播报回调抛异常 ⇒ do 照常执行（旁路不许改核心）
  D7 源码钉：`_do` 里播报调用在动作下发之前（防后人挪到动作之后）
"""

from __future__ import annotations

import pytest

from autoforge.af_bus import BusEvent
from autoforge.af_ir import Graph, load_automation
from autoforge.af_runtime import build_runtime

ROOM = "书房"

AUTO = {
    "ir_version": "0.2.1", "id": "do_speak_case", "name": "do播报用例", "version": 1,
    "mode": "restart",
    "nodes": [
        {"id": "a1", "kind": "on", "name": "人",
         "trigger": {"type": "state", "entity_id": "binary_sensor.motion", "to": "on"}},
        {"id": "d1", "kind": "do", "name": "开空调", "adapter": "climate", "action": "set_hvac_mode",
         "params": {"entity_id": "climate.x", "hvac_mode": "cool"}, "room": ROOM},
        {"id": "p1", "kind": "pass", "name": "结束"},
    ],
    "edges": [
        {"from": "a1", "to": "d1", "kind": "then"},
        {"from": "d1", "to": "p1", "kind": "then"},
    ],
}


AUTO_ANNOUNCE = {
    **AUTO,
    "nodes": [
        AUTO["nodes"][0],
        {**AUTO["nodes"][1], "announce": "书房有点热，我把空调打开"},
        AUTO["nodes"][2],
    ],
}


class _FakeAdapter:
    name = "climate"
    dry_run = False

    def __init__(self):
        self.calls = []

    def call(self, action, params):
        self.calls.append((action, dict(params)))
        from autoforge.af_adapters.base import CallResult
        return CallResult(success=True, data={}, error=None)


class _DryAdapter(_FakeAdapter):
    dry_run = True


def _runtime(auto=AUTO, adapter=None):
    rt = build_runtime(Graph([load_automation(auto)]))
    ad = adapter or _FakeAdapter()
    rt.adapters.register(ad)
    return rt, ad


def _fire(rt):
    rt.states.set_state("binary_sensor.motion", "off")
    rt.publish(BusEvent.of("binary_sensor.motion", "on"))


# ── D1 do 照常执行 + D2 播报在动作前触发 ────────────────────────────
def test_d1_d2_do_runs_and_speaks_before_action():
    # v1.27.1：播报改「显式才出声」⇒ 量这两格必须给显式文案（无文案那格归 D4 的静默）
    rt, ad = _runtime(AUTO_ANNOUNCE)
    speaks = []
    rt.executor.on_do_speak = lambda tid, text, room, entity_id="": speaks.append(
        {"trace_id": tid, "text": text, "room": room}
    )
    _fire(rt)
    assert ad.calls, "do 动作必须照常下发（播报是旁路，⛔ 不许吃掉动作）"
    assert len(speaks) == 1, f"do 下发前应播报一次，实得 {len(speaks)}"
    assert speaks[0]["trace_id"], "trace_id 不能为空"
    assert speaks[0]["room"] == ROOM, f"room 应取 node.room={ROOM}，实得 {speaks[0]['room']}"


# ── D3 announce 文本 ──────────────────────────────────────────────
def test_d3_announce_text_used():
    rt, _ad = _runtime(AUTO_ANNOUNCE)
    speaks = []
    rt.executor.on_do_speak = lambda tid, text, room, entity_id="": speaks.append(text)
    _fire(rt)
    assert speaks == ["书房有点热，我把空调打开"], f"应用节点 announce，实得 {speaks}"


# ── D4 无 announce 且无模板 ⇒ 静默（v1.27.1 语义收口）─────────────
def test_d4_no_announce_and_no_template_is_silent():
    """v1.27.1（PM 卡 `AF卡_v1271_热修_播报语义收口_20261106.md`）：**显式才出声**。

    既无 `speech_template`（或渲不出）又无 `announce` ⇒ **静默**，⛔ 不再机拼
    「自动化名：节点名」。反证腿：本用例在 v1.27.0 上是"必须有非空兜底文本"——
    那条兜底正是「一次派发念三句」的第三句来源；实现若把机拼加回去，本腿立刻红。
    """
    rt, ad = _runtime()
    speaks = []
    rt.executor.on_do_speak = lambda tid, text, room, entity_id="": speaks.append(text)
    _fire(rt)
    assert ad.calls, "静默⛔ 不许吃掉 do 动作本身（旁路改语义，核心不动）"
    assert speaks == [], f"无显式文案必须静默，实得 {speaks}"


# ── D5 dry-run 不播报 ─────────────────────────────────────────────
def test_d5_dry_run_does_not_speak():
    rt, ad = _runtime(adapter=_DryAdapter())
    speaks = []
    rt.executor.on_do_speak = lambda tid, text, room, entity_id="": speaks.append(text)
    _fire(rt)
    assert ad.calls, "dry-run 适配器也应被调用"
    assert speaks == [], "dry-run 没真动作，⛔ 不该出声"


# ── D6 播报抛异常不影响 do ────────────────────────────────────────
def test_d6_speak_exception_does_not_break_do():
    rt, ad = _runtime()

    def _boom(tid, text, room, entity_id=""):
        raise RuntimeError("桥抖了")

    rt.executor.on_do_speak = _boom
    _fire(rt)
    assert ad.calls, "播报抛异常时 do 动作必须照常下发"


# ── D7 源码钉：播报在动作下发之前 ─────────────────────────────────
def test_d7_speak_called_before_action():
    import inspect

    from autoforge import af_executor

    src = inspect.getsource(af_executor.NodeExecutor._do)
    i_speak = src.find("_speak_for_do")
    i_call = src.find("adapter.call(")
    assert i_speak != -1, "_do 里必须调 _speak_for_do"
    assert i_call != -1, "找不到 adapter.call"
    assert i_speak < i_call, "播报必须在动作下发之前（T1 口径：条件命中后、do 执行前）"
