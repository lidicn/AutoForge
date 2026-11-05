# -*- coding: utf-8 -*-
"""判据 D1 后半 · 单发模式必须把 canary 观察窗走完（2026-11-05）· 卡名 AF卡_live_run_advance。

真机实证：`af_live_run` 单发跑完即返回，**没有任何 tick** ⇒ 挂在 `canary_observe` 的实例永远等不到
`due_timers()`，L2 动作停在 `pending_canary`（d2 set_temperature 卡死、温度写从未到空调）。
PM 裁定：单发模式与 watch 常驻**都要**推进观察窗。

D 编号带卡名（群规⑭）：
  D1 观察期实例在单发推进后离开 suspended 并落终态（走 D8 的恢复腿）
  D2 没有观察期实例时是 no-op（⛔ 不空转、不改钟）
  D3 时钟不支持 advance（真时钟）⇒ no-op（⛔ 不伪造时间）
  D4 源码钉：live_run 在返回前调用推进
"""

from __future__ import annotations

from autoforge.af_bus import BusEvent
from autoforge.af_ir import Graph, load_automation
from autoforge.af_runtime import build_runtime
from autoforge.af_service import _advance_canary_windows

CANARY = {"duration": "1m", "auto_rollback": True}

AUTO = {
    "ir_version": "0.2.1", "id": "live_adv", "name": "单发推进用例", "version": 1,
    "mode": "restart",
    "nodes": [
        {"id": "a1", "kind": "on", "name": "t",
         "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
        {"id": "d1", "kind": "do", "name": "开灯", "adapter": "light",
         "action": "light.turn_on", "params": {"entity_id": "light.x"}, "canary": CANARY},
        {"id": "p1", "kind": "pass", "name": "结束"},
    ],
    "edges": [{"from": "a1", "to": "d1", "kind": "then"},
              {"from": "d1", "to": "p1", "kind": "then"}],
}


class _Adapter:
    name = "light"
    dry_run = False

    def call(self, action, params):
        from autoforge.af_adapters.base import CallResult
        return CallResult(success=True, data={}, error=None)


def _rt(trials=None):
    rt = build_runtime(Graph([load_automation(AUTO)]))
    rt.adapters.register(_Adapter())
    if trials is not None:
        rt.executor._record_trial = lambda inst, node, state: trials.append(state)
    return rt


def _fire(rt):
    rt.states.set_state("binary_sensor.m", "off")
    rt.states.set_state("light.x", "off")
    rt.publish(BusEvent.of("binary_sensor.m", "on"))


# ── D1 推进后离开 suspended ─────────────────────────────────────
def test_d1_advance_finishes_observation_window():
    trials = []
    rt = _rt(trials)
    _fire(rt)
    inst = list(rt.instances.all())[0]
    assert inst.state == "suspended", f"前置：应挂在观察期，实得 {inst.state}"
    n = _advance_canary_windows(rt)
    assert n >= 1, "应至少推进一轮"
    assert inst.state != "suspended", f"推进后⛔ 不许还挂着，实得 {inst.state}"
    assert trials and trials[-1] in ("verified", "rolled_back", "unmodeled"), f"应落终态，实得 {trials}"


# ── D2 没有观察期实例 ⇒ no-op ───────────────────────────────────
def test_d2_noop_without_canary_instances():
    rt = _rt()
    before = rt.clock.monotonic()
    assert _advance_canary_windows(rt) == 0
    assert rt.clock.monotonic() == before, "没有观察期实例时⛔ 不许动钟"


# ── D3 真时钟 no-op ─────────────────────────────────────────────
def test_d3_noop_without_advance_capability():
    class _RealClock:
        def now(self):
            from datetime import datetime, timezone
            return datetime.now(timezone.utc)

        def monotonic(self):
            return 0.0

    rt = _rt()
    rt.clock = _RealClock()
    assert _advance_canary_windows(rt) == 0, "时钟不能推进时⛔ 不许伪造时间"


# ── D4 源码钉 ───────────────────────────────────────────────────
def test_d4_live_run_calls_advance():
    import inspect

    from autoforge import af_service

    src = inspect.getsource(af_service.live_run)
    assert "_advance_canary_windows(" in src, "live_run 返回前必须推进观察窗"
