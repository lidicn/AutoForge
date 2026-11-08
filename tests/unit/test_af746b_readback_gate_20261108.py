# -*- coding: utf-8 -*-
"""AF-746b 判据 · 写后回读的**诚实两档**（v1.28.6，出资人 2026-11-08 22:01 A+ 裁决）· 卡名 `AF卡_v7_完整自动化` §十.4.1。

**为什么只有两档**：`xiaomi_home` 的 `set_property_async` 写完即 `_prop_value_map=value` ＋
`async_write_ha_state()`（自回显），与设备真实推送在 HA REST 上**完全同形**，`last_updated`
也被自回显刷新 ⇒ 没有可作"设备真值"证据的新鲜度信号。所以：
- `mismatch`（读到 ≠ 写入值）⇒ 有牙，记 `ACTION_FAILED` ＋ 计数；
- `unverified`（读到 = 写入值）⇒ **⛔ 永不 passed**（N4c 就是钉这条）。

**牙**：N4a 读到 20 ⇒ 必 mismatch（变异「删回读」必红）；N4b 读到 26 ⇒ 只断言"不误报失败"
且机器面落 `unverified`；N4c 变异「把 unverified 当 passed」/「删标记」⇒ 必红。
"""

from __future__ import annotations

import json
import tempfile
from typing import Any

import pytest

from autoforge.af_adapters.base import CallResult
from autoforge.af_ir import Graph, load_automation
from autoforge.af_time import VirtualTimeSource
from autoforge.af_runtime import build_runtime

CLIMATE = "climate.lumi_cn_84159632_v2"


class _Adapter:
    name = "ha"
    dry_run = False

    def __init__(self, *, ok: bool = True) -> None:
        self.ok = ok
        self.calls: list[tuple[str, dict]] = []

    def call(self, action: str, params: dict):
        self.calls.append((action, dict(params)))
        return CallResult.ok({}) if self.ok else CallResult.fail("HA 500")


def _auto(*, readback: dict | None = None, wait: str = "3s") -> dict[str, Any]:
    """on → do(带 readback) → wait → pass：wait 就是"等一会儿再回读"的落形。"""
    node: dict[str, Any] = {
        "id": "d1", "kind": "do", "name": "设 26 度", "adapter": "ha",
        "action": "climate.set_temperature",
        "params": {"entity_id": CLIMATE, "temperature": 26},
    }
    if readback is not None:
        node["readback"] = readback
    return {
        "ir_version": "0.2.1", "id": "af746b", "name": "AF746b 回读", "version": 1,
        "mode": "single",
        "nodes": [
            {"id": "a1", "kind": "on", "name": "有人",
             "trigger": {"type": "state", "entity_id": "binary_sensor.motion"}},
            node,
            {"id": "w1", "kind": "wait", "name": "等回报", "duration": wait},
            {"id": "p1", "kind": "pass", "name": "结束"},
        ],
        "edges": [
            {"from": "a1", "to": "d1", "kind": "then"},
            {"from": "d1", "to": "w1", "kind": "then"},
            {"from": "w1", "to": "p1", "kind": "then"},
            {"from": "d1", "to": "p1", "kind": "on_error"},
        ],
    }


def _rt(tmp_path, *, readback: dict | None = None, temp_after: Any = 26.0, ok: bool = True):
    """假传输层 + 可控的"设备回报值"（用 states provider 的 attributes 承载）。"""
    clock = VirtualTimeSource()
    rt = build_runtime(Graph([load_automation(_auto(readback=readback))]),
                       clock=clock, store_root=str(tmp_path))
    rt.adapters.register(_Adapter(ok=ok))
    rt.states.set_state("binary_sensor.motion", "on")
    rt.states.set_state(CLIMATE, "cool", {"temperature": temp_after})
    return rt


def _fire(rt) -> None:
    from autoforge.af_bus import BusEvent

    rt.publish(BusEvent.of("binary_sensor.motion", "on"))


def _settle(rt, seconds: float = 4.0) -> None:
    """推进虚拟钟再 tick：回读的 `due_at` 是时钟比，不睡觉也能量。"""
    rt.advance(seconds)
    rt.tick()


# ── N4a 读到别的值 ⇒ mismatch＋ACTION_FAILED（有牙）───────────────────────

def test_n4a_readback_mismatch_is_recorded(tmp_path):
    rb = {"attribute": "temperature", "equals": 26, "wait": "3s"}
    rt = _rt(tmp_path, readback=rb, temp_after=20.0)
    _fire(rt)
    _settle(rt)

    stats = rt.executor.readback_stats
    assert stats["checks"] == 1, f"回读没结账：{stats}"
    assert stats["mismatches"] == 1, f"读到 20 与期望 26 不符，必须记 mismatch：{stats}"
    assert stats["last_verdict"] == "mismatch"
    assert str(stats["last_actual"]) in ("20", "20.0"), f"实际读数要如实记：{stats}"

    inst = rt.instances.all()[0]
    assert inst.ctx.context["readback"]["verdict"] == "mismatch"
    types = [e.type for e in rt.audit if hasattr(e, "type")]
    assert "action_failed" in types, f"回读不符必须落 ACTION_FAILED：{types}"


# ── N4b 读到写入值 ⇒ 只"不误报失败"，且机器面落 unverified（⛔ 不落 passed）────

def test_n4b_match_is_unverified_never_passed(tmp_path):
    rb = {"attribute": "temperature", "equals": 26, "wait": "3s"}
    rt = _rt(tmp_path, readback=rb, temp_after=26.0)
    _fire(rt)
    _settle(rt)

    stats = rt.executor.readback_stats
    assert stats["checks"] == 1 and stats["mismatches"] == 0, f"读到 26 不许误报失败：{stats}"
    assert stats["last_verdict"] == "unverified", (
        f"读到写入值只能 unverified（HA 回显≠设备真值，N4b/N4c）：{stats}"
    )
    inst = rt.instances.all()[0]
    assert inst.ctx.context["readback"]["verdict"] == "unverified"
    assert "passed" not in json.dumps(inst.ctx.context["readback"]).lower(), "unverified⛔ 不许伪装成 passed"


# ── N4c 未声明 readback 的 do ⇒ 一行回读都不许凭空产生（防"到处都 unverified"噪声）──

def test_n4c_without_spec_no_readback_at_all(tmp_path):
    rt = _rt(tmp_path, readback=None, temp_after=20.0)
    _fire(rt)
    _settle(rt)

    stats = rt.executor.readback_stats
    assert stats["checks"] == 0, f"没声明 readback 却产生了回读：{stats}"
    inst = rt.instances.all()[0]
    assert "readback" not in inst.ctx.context


# ── N4d 读到 None（读不到属性）⇒ unverified，⛔ 不许当 mismatch 也不许当 passed ──

def test_n4d_missing_attribute_is_unverified(tmp_path):
    rb = {"attribute": "temperature", "equals": 26, "wait": "3s"}
    rt = _rt(tmp_path, readback=rb, temp_after=None)
    _fire(rt)
    _settle(rt)

    stats = rt.executor.readback_stats
    assert stats["checks"] == 1 and stats["mismatches"] == 0, f"读不到值不许判失败：{stats}"
    assert stats["last_verdict"] == "unverified"


# ── N4e 源码钉：干掉回读登记/结账任一半都必须红（变异点位即此处）────────────

def test_n4e_source_pinned(tmp_path):
    import inspect

    from autoforge.af_executor import NodeExecutor

    src_do = inspect.getsource(NodeExecutor._do)
    assert "_schedule_readback(" in src_do, "do 成功分支必须登记回读（删了＝A+ 失效）"
    src_run = inspect.getsource(NodeExecutor.run)
    assert "_maybe_check_readback(" in src_run, "resume/segment 起点必须结账到点回读"
    src_rt = inspect.getsource(build_runtime)
    assert "check_due_readbacks" in src_rt or True  # build_runtime 本身不接线，tick 里结账
    from autoforge.af_runtime import Runtime

    assert "check_due_readbacks(" in inspect.getsource(Runtime.tick), "tick 必须结账回读"

# ── N4f canary 节点：do 的成功在"观察窗通过"那一刻才算数，回读登记要跟到那儿 ──────

def test_n4f_canary_node_schedules_readback_after_verified(tmp_path):
    clock = VirtualTimeSource()
    auto = _auto(readback={"attribute": "temperature", "equals": 26, "wait": "3s"})
    for n in auto["nodes"]:
        if n["id"] == "d1":
            n["canary"] = {"duration": "30s", "auto_rollback": False}
    rt = build_runtime(Graph([load_automation(auto)]), clock=clock, store_root=str(tmp_path))
    rt.adapters.register(_Adapter())
    rt.states.set_state("binary_sensor.motion", "on")
    rt.states.set_state(CLIMATE, "cool", {"temperature": 20.0})
    _fire(rt)

    # 观察窗未过 ⇒ 还没登记回读（挂在 canary 上，不是动作一发出就回读）
    assert rt.executor.readback_stats["checks"] == 0
    rt.advance(31.0)   # 观察窗到点：verified ⇒ 登记回读
    rt.tick()
    rt.advance(5.0)    # 越过 3s 回读等待
    rt.tick()

    stats = rt.executor.readback_stats
    assert stats["checks"] == 1, f"canary 通过后回读没结账：{stats}"
    assert stats["last_verdict"] == "mismatch", f"设备回报 20 必须判 mismatch：{stats}"
