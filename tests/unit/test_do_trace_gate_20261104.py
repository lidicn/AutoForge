# -*- coding: utf-8 -*-
"""T2 判据 · 每次 do 下发结果写进 instance trace（2026-11-04）· 卡名 AF卡_do_trace。

出资人缺陷 T2：v3 已拆两步（set_hvac_mode(cool) → set_temperature(26)）但空调仍 20°C。
现场无法定性，因为**成功路径此前一条记录都没有**（只有失败的 ACTION_FAILED 审计）——
回答不了「这条 do 到底发出去没有、HA 回了什么」。

本卡只加留痕：`_do` 在拿到 CallResult 后 `instance.trace(node.id, note="do <adapter>.<action> ok|failed: …")`。
trace 随实例持久化（`af_instance.to_dict` 的 `trace` 键）⇒ 读 store 里的实例快照即可逐条核对。
⛔ 不改动作、不改边选择、不改结果处理。

D 编号带卡名（群规⑭）：
  D1 成功的 do ⇒ trace 多一条 `do ha.climate.set_temperature ok`
  D2 失败的 do ⇒ trace 记 `failed: <error>`（不是只有审计、trace 里也有）
  D3 留痕不改变执行结果与边选择（成功 do 仍走 then 到 pass）
  D4 牙齿（源码钉）：`_do` 里 trace 调用在 `adapter.call(` 之后
"""

from __future__ import annotations

import pytest

from autoforge.af_bus import BusEvent
from autoforge.af_ir import Graph, load_automation
from autoforge.af_runtime import build_runtime

AUTO = {
    "ir_version": "0.2.1", "id": "do_trace_case", "name": "do留痕用例", "version": 1,
    "mode": "restart",
    "nodes": [
        {"id": "a1", "kind": "on", "name": "人",
         "trigger": {"type": "state", "entity_id": "binary_sensor.motion", "to": "on"}},
        {"id": "d1", "kind": "do", "name": "设温", "adapter": "climate", "action": "set_temperature",
         "params": {"entity_id": "climate.x", "temperature": 26}},
        {"id": "p1", "kind": "pass", "name": "结束"},
    ],
    "edges": [
        {"from": "a1", "to": "d1", "kind": "then"},
        {"from": "d1", "to": "p1", "kind": "then"},
    ],
}


class _Adapter:
    name = "climate"
    dry_run = False

    def __init__(self, ok=True, error=None):
        self.ok = ok
        self.error = error
        self.calls = 0

    def call(self, action, params):
        self.calls += 1
        from autoforge.af_adapters.base import CallResult
        if self.ok:
            return CallResult(success=True, data={"temperature": params.get("temperature")}, error=None)
        return CallResult(success=False, data=None, error=self.error or "HA 返回 400")


def _run(adapter):
    rt = build_runtime(Graph([load_automation(AUTO)]))
    rt.adapters.register(adapter)
    rt.states.set_state("binary_sensor.motion", "off")
    rt.publish(BusEvent.of("binary_sensor.motion", "on"))
    inst = rt.instances.list()[0] if hasattr(rt.instances, "list") else None
    return rt, inst


def _instance(rt):
    # 从 scheduler/instances 拿本次实例：InstanceManager 用 _by_id 私有，按公开面取
    for attr in ("instances",):
        mgr = getattr(rt, attr)
        for name in ("all", "_by_id", "items"):
            v = getattr(mgr, name, None)
            if v is None:
                continue
            try:
                vals = list(v.values()) if isinstance(v, dict) else (v() if callable(v) else list(v))
            except TypeError:
                continue
            if vals:
                return vals[0]
    return None


# ── D1 成功 do 留痕 ───────────────────────────────────────────────
def test_d1_successful_do_is_traced():
    rt, _ = _run(_Adapter(ok=True))
    inst = _instance(rt)
    assert inst is not None, "拿不到实例"
    notes = [t.get("note", "") for t in inst.ctx.trace]
    hits = [n for n in notes if "do climate.set_temperature" in n]
    assert hits, f"成功 do 必须在 trace 留痕，实得 trace={notes}"
    assert hits[-1].endswith("ok"), f"成功留痕应以 ok 结尾，实得 {hits[-1]!r}"


# ── D2 失败 do 留痕带 error ────────────────────────────────────────
def test_d2_failed_do_traces_error():
    rt, _ = _run(_Adapter(ok=False, error="HA 返回 400 Bad Request"))
    inst = _instance(rt)
    assert inst is not None
    notes = [t.get("note", "") for t in inst.ctx.trace]
    hits = [n for n in notes if "do climate.set_temperature" in n]
    assert hits, f"失败 do 同样要留痕，实得 trace={notes}"
    assert "failed:" in hits[-1] and "400" in hits[-1], f"失败留痕应带 error，实得 {hits[-1]!r}"


# ── D3 留痕不改变执行结果 ─────────────────────────────────────────
def test_d3_trace_does_not_change_flow():
    ad = _Adapter(ok=True)
    rt, _ = _run(ad)
    assert ad.calls == 1, "do 应恰好下发一次"
    inst = _instance(rt)
    assert inst is not None
    assert inst.is_terminal, f"成功 do 后实例应到终态，实得 {inst.state}"
    assert inst.state == "done", f"成功应落 done，实得 {inst.state}"


# ── D4 源码钉：留痕在动作之后 ────────────────────────────────────
def test_d4_trace_after_action():
    import inspect

    from autoforge import af_executor

    src = inspect.getsource(af_executor.NodeExecutor._do)
    i_call = src.find("adapter.call(")
    i_trace = src.find('f"do {node.adapter}.{node.action} "')
    assert i_call != -1, "找不到 adapter.call"
    assert i_trace != -1, "_do 里必须留 do 结果 trace"
    assert i_call < i_trace, "留痕必须在动作下发之后（要记的是真实结果）"
