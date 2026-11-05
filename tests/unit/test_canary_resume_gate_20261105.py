# -*- coding: utf-8 -*-
"""判据 D8 · canary 观察期到期后必须真正推进（2026-11-05 真机实证）· 卡名 AF卡_canary_resume。

真机实证（AF-PM `af_live_run` 单发）：`d1 set_hvac_mode` 真落，`d2 set_temperature` **永远卡在
canary**（实例 suspended、动作停在 pending_canary）⇒ 温度写从未到空调。两处根因：
  ① 到期恢复用 `pick_edge({on_timeout, default})`，而 do 节点只有 `then`/`on_error`
     ⇒ 选不出边 ⇒ 实例被 `_terminate`（"观察期结束"被当成"超时"）；
  ② 恢复路径**从不写试演状态** ⇒ 侧车永远停在 `observing`（UI 一直显示"试演中"）。

D 编号带卡名（群规⑭）：
  D1 观察期到期 ⇒ 实例**继续到正常腿**（走 then 到 p1），⛔ 不许卡在 do 节点终止
  D2 无漂移 ⇒ 试演状态落 `verified`
  D3 有漂移 ⇒ 走 `on_error` 腿 + 试演状态落 `rolled_back`
  D4 源码钉：恢复段用 do 的正常边，且 verified/rolled_back 都写试演状态
"""

from __future__ import annotations

from autoforge.af_bus import BusEvent
from autoforge.af_ir import Graph, load_automation
from autoforge.af_runtime import build_runtime

CANARY = {"duration": "1m", "auto_rollback": True}


def _auto(on_error=True):
    edges = [
        {"from": "a1", "to": "d1", "kind": "then"},
        {"from": "d1", "to": "p1", "kind": "then"},
    ]
    if on_error:
        edges.append({"from": "d1", "to": "e1", "kind": "on_error"})
    return {
        "ir_version": "0.2.1", "id": "canary_case", "name": "canary用例", "version": 1,
        "mode": "restart",
        "nodes": [
            {"id": "a1", "kind": "on", "name": "t",
             "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
            {"id": "d1", "kind": "do", "name": "开灯", "adapter": "light",
             "action": "light.turn_on", "params": {"entity_id": "light.x"}, "canary": CANARY},
            {"id": "p1", "kind": "pass", "name": "正常结束"},
            {"id": "e1", "kind": "pass", "name": "失败腿"},
        ],
        "edges": edges,
    }


class _Adapter:
    """模拟真机：`flip=True` 时动作让实体真的变成 on（无漂移），否则不动（判为漂移）。"""

    name = "light"
    dry_run = False

    def __init__(self, states, flip):
        self.states = states
        self.flip = flip
        self.calls = []

    def call(self, action, params):
        from autoforge.af_adapters.base import CallResult
        self.calls.append((action, dict(params)))
        if self.flip:
            self.states.set_state(str(params.get("entity_id")), "on")
        return CallResult(success=True, data={}, error=None)


def _rt(flip, trials):
    rt = build_runtime(Graph([load_automation(_auto())]))
    rt.adapters.register(_Adapter(rt.states, flip))
    rt.executor.runtime_facts = lambda state, automation_id: trials.append(state)
    rt.executor._record_trial = lambda inst, node, state: trials.append(state)  # 抓侧车写入
    return rt


def _fire(rt):
    rt.states.set_state("binary_sensor.m", "off")
    rt.states.set_state("light.x", "off")
    rt.publish(BusEvent.of("binary_sensor.m", "on"))


def _inst(rt):
    vals = list(rt.instances.all())
    assert vals, "应有一个实例"
    return vals[0]


# ── D1 到期后必须推进（走 then 到 p1） ──────────────────────────
def test_d1_resume_advances_via_then_edge():
    trials = []
    rt = _rt(flip=True, trials=trials)
    _fire(rt)
    inst = _inst(rt)
    assert inst.state == "suspended", f"观察期内应挂起，实得 {inst.state}"
    assert inst.ctx.context.get("pending_canary") is not None, "挂起时应带 pending_canary"

    rt.clock.advance(61)
    rt.tick()
    inst = _inst(rt)
    assert inst.state == "done", f"到期后应走完，实得 {inst.state}"
    assert inst.ctx.current_node in ("p1", ""), f"应走 then 腿到 p1，实得 {inst.ctx.current_node!r}"


# ── D2 无漂移 ⇒ verified ─────────────────────────────────────────
def test_d2_verified_recorded():
    trials = []
    rt = _rt(flip=True, trials=trials)
    _fire(rt)
    rt.clock.advance(61)
    rt.tick()
    assert "verified" in trials, f"无漂移应落 verified，实得 {trials}"


# ── D3 有漂移 ⇒ on_error 腿 + rolled_back ───────────────────────
def test_d3_drift_goes_on_error_and_rolled_back():
    trials = []
    rt = _rt(flip=False, trials=trials)
    _fire(rt)
    rt.clock.advance(61)
    rt.tick()
    inst = _inst(rt)
    assert "rolled_back" in trials, f"漂移应落 rolled_back，实得 {trials}"
    assert inst.state in ("done", "failed")
    assert inst.ctx.current_node in ("e1", ""), f"漂移应走 on_error 腿到 e1，实得 {inst.ctx.current_node!r}"


# ── D4 源码钉 ───────────────────────────────────────────────────
def test_d4_source_wiring():
    import inspect

    from autoforge import af_executor

    src = inspect.getsource(af_executor.NodeExecutor.resume)
    assert "canary_edge_kinds" in src, "恢复段必须为 canary 单独选边"
    assert '"verified"' in src and '"rolled_back"' in src, "恢复段必须写试演状态"
    assert 'edge_kinds = canary_edge_kinds if canary_edge_kinds is not None else {kind, "default"}' in src
