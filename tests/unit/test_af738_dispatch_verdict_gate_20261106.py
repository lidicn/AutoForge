# -*- coding: utf-8 -*-
"""判据 AF-738 · 单发回执不许盲报「没发」（2026-11-06）· 卡名 AF卡_live_dispatch_verdict。

**缺陷（AF-738，台账 §1.176）**：`live_run` 的回执 `dispatch_result.verdict` 在**动作真的下发之后**
仍报 `nothing_dispatched`。根因不是 canary 卡住，而是**回执读的是一个在本路径不存在的事实源**：
`_dispatch_verdict` 只认 `af_action_dispatched`，而该事件由**影子/干预层**（`af_shadow.py`→
`intervention.note_node`）写，`live_run` 的 runtime **没有接影子层** ⇒ 回执恒盲。
实测（修前）：adapter 被真调用 1 次，audit 全空，verdict＝`nothing_dispatched`。

**修法**：执行器在**真调用适配器那一刻**写 `af_action_called`（watch 与单发两条路都有，
且不随实例终止而消失）；`_dispatch_verdict` 优先认它，`af_action_dispatched` 降为回退。

**红线**（卡面）：⛔ 不动 DCD canary 强制语义、⛔ 不削弱 watch 路径、⛔ 回执不许假绿/假红。

覆盖：
  D1 条件满足＋canary ⇒ 动作真调用 且 verdict=`executed`（修前的假红已消失）
  D2 条件不满足 ⇒ verdict=`nothing_dispatched`（真负控；与 D1 同构差分，证明不是恒绿）
  D3 审计在案：调用点写 `af_action_called`，带 automation/node/instance
  D4 canary 观察窗进入后动作**已经发生**（⛔ 不许把"观察中"读成"没发"）
  D5 失败路径 ⇒ verdict=`failed`（`ACTION_FAILED` 仍生效，⛔ 不被新码掩盖）
  D6 源码钉：`_do` 的**两个**调用点都留痕；verdict 优先读 `af_action_called`
"""

from __future__ import annotations

import pytest

from autoforge.af_bus import BusEvent
from autoforge.af_ir import Graph, load_automation
from autoforge.af_runtime import build_runtime
from autoforge.af_service import _advance_canary_windows, _dispatch_verdict
from autoforge.af_time import SystemTimeSource, VirtualTimeSource

IR = {
    "ir_version": "0.2.1", "id": "af738", "name": "回执探针", "version": 1, "mode": "parallel",
    "nodes": [
        {"id": "a1", "kind": "on",
         "trigger": {"type": "state", "entity_id": "binary_sensor.pc", "to": "on"}},
        {"id": "d1", "kind": "do", "adapter": "climate", "action": "set_hvac_mode",
         "params": {"entity_id": "climate.x", "hvac_mode": "cool"},
         "canary": {"duration": "5m", "auto_rollback": True}},
        {"id": "p1", "kind": "pass"},
    ],
    "edges": [{"from": "a1", "to": "d1", "kind": "then"},
              {"from": "d1", "to": "p1", "kind": "then"}],
}


class _FakeAdapter:
    name = "climate"
    dry_run = False

    def __init__(self, ok: bool = True):
        self.calls: list[tuple[str, dict]] = []
        self.ok = ok

    def call(self, action, params):
        from autoforge.af_adapters.base import CallResult
        self.calls.append((action, dict(params)))
        return CallResult(success=self.ok, data={}, error=None if self.ok else "boom")


def _rt(ok: bool = True):
    rt = build_runtime(Graph([load_automation(IR)]),
                       clock=VirtualTimeSource(SystemTimeSource().now()))
    ad = _FakeAdapter(ok=ok)
    rt.adapters.register(ad)
    rt.states.set_state("binary_sensor.pc", "off")
    rt.states.set_state("climate.x", "off")
    return rt, ad


# ── D1 条件满足 ⇒ 真调用 + verdict=executed（修前是 nothing_dispatched）────────
def test_d1_dispatch_visible_in_verdict():
    rt, ad = _rt()
    rt.emit("binary_sensor.pc", "on")
    assert len(ad.calls) == 1, "条件满足时动作必须真调用"
    v = _dispatch_verdict(rt)
    assert v["verdict"] == "executed", f"动作真发了，回执必须报 executed（AF-738 假红），实得 {v}"
    assert v["dispatched"] == 1, f"dispatched 计数必须是真值，实得 {v}"


# ── D2 条件不满足 ⇒ nothing_dispatched（真负控，与 D1 差分）──────────────────
def test_d2_no_dispatch_reports_nothing():
    rt, ad = _rt()
    rt.emit("binary_sensor.pc", "off")  # 不匹配 `to: on`
    assert ad.calls == [], "条件不满足时不得调用适配器"
    v = _dispatch_verdict(rt)
    assert v["verdict"] == "nothing_dispatched", f"真没发才许报 nothing_dispatched，实得 {v}"


# ── D3 调用点留痕在案 ─────────────────────────────────────────────────
def test_d3_action_called_audited():
    rt, _ad = _rt()
    rt.emit("binary_sensor.pc", "on")
    types = [e.type for e in rt.audit.events]
    assert "af_action_called" in types, f"真调用点必须留痕，现有 {types}"
    ev = [e for e in rt.audit.events if e.type == "af_action_called"][0]
    assert ev.automation_id == "af738" and ev.node_id == "d1" and ev.instance_id
    assert ev.data.get("action") == "climate.set_hvac_mode"


# ── D4 canary 观察窗：动作已发生，⛔ 不许读成「没发」──────────────────────────
def test_d4_canary_window_does_not_hide_dispatch():
    rt, ad = _rt()
    rt.emit("binary_sensor.pc", "on")
    # 进入观察窗：实例 suspended 且带 pending_canary
    assert "af_action_called" in [e.type for e in rt.audit.events], "进入观察窗前动作已调用"
    assert _dispatch_verdict(rt)["verdict"] == "executed"
    # 有界推进把窗口走完，verdict 不得翻转成「没发」
    _advance_canary_windows(rt)
    assert _dispatch_verdict(rt)["verdict"] == "executed", "观察窗走完不许把已发生的下发抹掉"


# ── D5 失败路径不被新码掩盖 ────────────────────────────────────────────
def test_d5_failure_still_reported():
    rt, ad = _rt(ok=False)
    rt.emit("binary_sensor.pc", "on")
    assert len(ad.calls) == 1
    v = _dispatch_verdict(rt)
    assert v["verdict"] == "failed", f"适配器失败必须报 failed，实得 {v}"
    assert v["failed"] == 1


# ── D6 源码钉：两个调用点都留痕；verdict 优先新码 ──────────────────────
def test_d6_source_pins():
    import inspect

    from autoforge.af_executor import NodeExecutor
    from autoforge.af_service import _dispatch_verdict as vd

    do_src = inspect.getsource(NodeExecutor._do)
    assert do_src.count("_audit_action_called(") == 2, "canary 与非 canary 两个调用点都必须留痕"
    assert "guard.perform(" in do_src and "adapter.call(" in do_src
    vd_src = inspect.getsource(vd)
    assert 'a.get("type") == "af_action_called"' in vd_src, "verdict 必须把执行器留痕作为首选读点"
    assert "called or [" in vd_src, "新码为首选、影子层旧码为回退（⛔ 反过来会让 live_run 继续盲报）"
