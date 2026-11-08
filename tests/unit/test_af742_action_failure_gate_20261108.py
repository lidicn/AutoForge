# -*- coding: utf-8 -*-
"""AF-742 判据 · 「动作失败零留痕」＋「终态读数分不清干净/短路完成」（2026-11-08）· 卡名 `AF卡_af742_action_failure`。

**现场**（台账 §1.181，v1.28.1/`667e9ce` 线上）：2026-11-08 02:01:44.961 `d2` segment →
02:01:46.500 `complete（actions=1）`，中间 **1.5 秒零 INFO 零 WARNING**；IR 的
`d2 --on_error--> p1` 短路跳过 `e1`（播报节点）⇒ 播报零发布、butler 零收件。HA 侧抓到的铁证是
`xiaomi_home/climate.py:168 async_set_temperature → RuntimeError: 执行错误, 未知错误,
code=-706010002, climate.lumi_cn_84159632_v2, target-temperature`——**设备拒绝写 target-temperature**。

**两个洞**：

1. **动作失败留痕不够持久**：`ACTION_FAILED` 审计确实由 `_do` 写了，但 `AuditLog` 只在内存
   （`af_audit.AUDIT_SCOPE_NOTE`：只有部署仪式台账落盘）；实例 trace 也只在 `persist=true`
   才落盘 ⇒ 事后 **一条都读不到**。而 `watch.log` 是唯一持久面，那里只有生命周期阶段行，
   没有动作级失败行。
2. **终态读数分不清**：`complete actions=1` 看起来像"一次动作就干净收尾"，实际是
   「d1 成功（但没计数）＋ d2 失败 → on_error 短路到 p1」。读数缺一维。

**修法**（两件一笔）：

- `af_executor._do` / `_soft_fail`：失败动作 → `instance.ctx.failed_actions += 1` ＋
  `INSTANCE_LIFECYCLE stage=action_failed`（带 `action=`／`error=`／`dev=`——失败现场的设备态，
  只读快照，钉住 PM 要的"具体触发态"）。
- `_lifecycle` 行统一加 `failed_actions`；`complete` 因此可分「干净完成（0）」与「短路完成（>0）」。
- 顺带两处读数纠偏（同族，不另开号）：① canary 挂起腿在 `return` 前也计数（旧实现挂起后
  `actions += 1`，导致"动作真发了、读数停在 0"——现场把两次调用报成一次）；② `instance_no_action`
  只在 `actions==0 且 failed_actions==0` 时落（否则"试过并失败"会被再读成"什么都没做"）。

**牙**（群规⑧）：A1 钉失败行本身；A2 是**两腿差分**（失败=1／干净=0，防"恒 1"假绿）；
A3 钉挂起腿计数（旧实现必红）；A4 同时钉"有设备态"与"读不到设备态也不改失败判定"；
A7 钉 `no_action` 与失败动作互斥。
"""

from __future__ import annotations

import logging
import tempfile
from typing import Any

import pytest

from autoforge.af_bus import BusEvent
from autoforge.af_ir import Graph, load_automation
from autoforge.af_runtime import build_runtime

ROOM = "书房"
CLIMATE = "climate.x"


class _Adapter:
    """可控适配器：指定动作失败／抛异常。"""

    name = "ha"
    dry_run = False

    def __init__(self, *, fail: tuple[str, ...] = (), raise_on: tuple[str, ...] = ()) -> None:
        self.fail = set(fail)
        self.raise_on = set(raise_on)
        self.calls: list[tuple[str, dict]] = []

    def call(self, action: str, params: dict):
        from autoforge.af_adapters.base import CallResult

        self.calls.append((action, dict(params)))
        if action in self.raise_on:
            raise RuntimeError("HA 连接被重置")
        if action in self.fail:
            return CallResult(
                success=False,
                error=("RuntimeError: 执行错误, 未知错误, "
                       "code=-706010002, target-temperature"),
            )
        return CallResult.ok({})


def _auto(*, d2_canary: bool = True) -> dict[str, Any]:
    """线上 v6 的同形：`on(group-or)` → `if` → `d1` → `d2` → `e1`，两 do 各有 `on_error → p1`。"""
    canary = {"duration": "5m", "auto_rollback": True} if d2_canary else None
    d2: dict[str, Any] = {
        "id": "d2", "kind": "do", "name": "设 26 度", "adapter": "ha",
        "action": "climate.set_temperature",
        "params": {"entity_id": CLIMATE, "temperature": 26}, "room": ROOM,
        "announce": "设 26",
    }
    if canary:
        d2["canary"] = canary
    return {
        "ir_version": "0.2.1", "id": "af742", "name": "AF742", "version": 1, "mode": "single",
        "nodes": [
            {"id": "a1", "kind": "on", "name": "任一变化",
             "trigger": {"type": "group", "op": "or", "sources": [
                 {"type": "state", "entity_id": "sensor.temp"},
                 {"type": "state", "entity_id": "binary_sensor.door"},
             ]}},
            {"id": "i1", "kind": "if", "name": "温度>27.6",
             "expr": {"op": "gt", "left": {"var": "entity.sensor.temp", "type": "numeric"},
                      "right": {"const": 27.6}}},
            {"id": "d1", "kind": "do", "name": "制冷", "adapter": "ha",
             "action": "climate.set_hvac_mode",
             "params": {"entity_id": CLIMATE, "hvac_mode": "cool"}, "room": ROOM,
             "announce": "制冷", "canary": {"duration": "5m", "auto_rollback": True}},
            d2,
            {"id": "e1", "kind": "pass", "name": "广播"},
            {"id": "p1", "kind": "pass", "name": "结束"},
        ],
        "edges": [
            {"from": "a1", "to": "i1", "kind": "then"},
            {"from": "i1", "to": "d1", "kind": "then"},
            {"from": "i1", "to": "p1", "kind": "no"},
            {"from": "d1", "to": "d2", "kind": "then"},
            {"from": "d2", "to": "e1", "kind": "then"},
            {"from": "e1", "to": "p1", "kind": "then"},
            {"from": "d1", "to": "p1", "kind": "on_error"},
            {"from": "d2", "to": "p1", "kind": "on_error"},
        ],
    }


def _runtime(*, adapter: _Adapter, states: dict[str, Any] | None = None, auto=None, store=None):
    rt = build_runtime(Graph([load_automation(auto or _auto())]), store_root=store or tempfile.mkdtemp())
    rt.adapters.register(adapter)
    for eid, val in (states or {}).items():
        if isinstance(val, tuple):
            rt.states.set_state(eid, val[0], val[1])
        else:
            rt.states.set_state(eid, val)
    return rt


def _lifecycle_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if "INSTANCE_LIFECYCLE" in r.getMessage()]


def _run_until_d2(rt):
    """跑到 d2 段（d1 canary 挂起 → resume）。"""
    rt.publish(BusEvent.of("binary_sensor.door", "off"))
    inst = rt.instances.all()[0]
    assert inst.ctx.current_node == "d1" and inst.state == "suspended", \
        f"前置：d1 必须挂在 canary_observe，实得 node={inst.ctx.current_node} state={inst.state}"
    rt.executor.resume(inst, "on_timeout")
    return inst


# ── A1 失败动作必须落 action_failed 生命周期行＋计数＋审计 ────────────────

def test_a1_failed_action_leaves_lifecycle_line(caplog):
    ad = _Adapter(fail=("climate.set_temperature",))
    rt = _runtime(adapter=ad, states={
        "sensor.temp": "28.0", "binary_sensor.door": "off",
        CLIMATE: ("cool", {"hvac_mode": "cool", "temperature": 20.0,
                           "current_temperature": 27.2}),
    })
    with caplog.at_level(logging.INFO, logger="autoforge.executor"):
        inst = _run_until_d2(rt)

    lines = _lifecycle_lines(caplog)
    failed_lines = [ln for ln in lines if "stage=action_failed" in ln]
    assert failed_lines, f"失败动作必须落 action_failed 行（现场零留痕的那个洞）；实得：{lines}"
    line = failed_lines[0]
    assert "node=d2" in line, line
    assert "action=ha.climate.set_temperature" in line, line
    assert "-706010002" in line, f"错误原文要进持久面：{line}"
    assert int(inst.ctx.failed_actions) == 1
    assert "action_failed" in [str(e.type) for e in rt.audit], "审计面同时要有 ACTION_FAILED"


# ── A2 两腿差分：短路完成 vs 干净完成（防"恒 1"假绿）────────────────────

def test_a2_complete_line_distinguishes_short_circuit(caplog):
    # ① 失败腿（线上形状）：complete 行必须是 failed_actions=1
    ad = _Adapter(fail=("climate.set_temperature",))
    rt = _runtime(adapter=ad, states={
        "sensor.temp": "28.0", "binary_sensor.door": "off",
        CLIMATE: ("cool", {"hvac_mode": "cool", "temperature": 20.0}),
    })
    with caplog.at_level(logging.INFO, logger="autoforge.executor"):
        inst = _run_until_d2(rt)
    comp = [ln for ln in _lifecycle_lines(caplog) if "stage=complete" in ln][-1]
    assert "failed_actions=1" in comp, f"短路完成必须可辨：{comp}"
    assert inst.state == "done"

    # ② 干净腿（同一张图，d2 成功且不带观察窗 ⇒ 一路走 e1 → p1）：complete 行必须 failed_actions=0
    caplog.clear()
    ad2 = _Adapter()
    rt2 = _runtime(adapter=ad2, auto=_auto(d2_canary=False), states={
        "sensor.temp": "28.0", "binary_sensor.door": "off",
        CLIMATE: ("cool", {"hvac_mode": "cool", "temperature": 26.0}),
    })
    with caplog.at_level(logging.INFO, logger="autoforge.executor"):
        inst2 = _run_until_d2(rt2)
    comp2 = [ln for ln in _lifecycle_lines(caplog) if "stage=complete" in ln][-1]
    assert "failed_actions=0" in comp2, f"干净完成⛔ 不许报失败：{comp2}"
    assert int(inst2.ctx.failed_actions) == 0


# ── A3 canary 挂起腿也要计数（旧实现挂起后 return，读数停在 0）────────────

def test_a3_canary_suspend_leg_counts_action(caplog):
    ad = _Adapter()
    rt = _runtime(adapter=ad, states={
        "sensor.temp": "28.0", "binary_sensor.door": "off",
        CLIMATE: ("cool", {"hvac_mode": "cool", "temperature": 26.0}),
    })
    with caplog.at_level(logging.INFO, logger="autoforge.executor"):
        rt.publish(BusEvent.of("binary_sensor.door", "off"))
    inst = rt.instances.all()[0]
    assert inst.state == "suspended"
    assert int(inst.ctx.actions) == 1, \
        "d1 动作已真发出去（挂起观察窗）⇒ 读数必须是 1，⛔ 不是 0（旧实现把两次调用报成一次）"
    resume_line = [ln for ln in _lifecycle_lines(caplog) if "stage=spawn" in ln][0]
    assert "actions=0" in resume_line and "failed_actions=0" in resume_line


# ── A4 失败现场设备态（有则钉上；读不到也不许改判定）────────────────────

def test_a4_failure_carries_device_state(caplog):
    ad = _Adapter(fail=("climate.set_temperature",))
    rt = _runtime(adapter=ad, states={
        "sensor.temp": "28.0", "binary_sensor.door": "off",
        CLIMATE: ("cool", {"hvac_mode": "cool", "temperature": 20.0,
                           "current_temperature": 27.2, "fan_mode": "auto"}),
    })
    with caplog.at_level(logging.INFO, logger="autoforge.executor"):
        _run_until_d2(rt)
    line = [ln for ln in _lifecycle_lines(caplog) if "stage=action_failed" in ln][0]
    assert "dev=" in line, f"失败现场要带设备态（PM 要钉的『具体触发态』）：{line}"
    assert "temperature=20" in line, f"target 温度是本次拒绝的承重证据：{line}"
    assert "hvac_mode=cool" in line, line


def test_a4b_missing_state_does_not_change_verdict(caplog):
    """读不到设备态 ⇒ `dev=-`，但失败判定/短路边走法一字不变（诊断不是闸）。"""
    ad = _Adapter(fail=("climate.set_temperature",))
    # 故意**不**给 climate.x 建状态：`_failure_state_note` 会读不到
    rt = _runtime(adapter=ad, states={"sensor.temp": "28.0", "binary_sensor.door": "off"})
    with caplog.at_level(logging.INFO, logger="autoforge.executor"):
        rt.publish(BusEvent.of("binary_sensor.door", "off"))
    inst = rt.instances.all()[0]
    lines = _lifecycle_lines(caplog)
    # 没有 climate.x ⇒ d1 先软失效（实体漂移），仍是 action_failed 且 failed_actions=1
    assert any("stage=action_failed" in ln for ln in lines), lines
    assert int(inst.ctx.failed_actions) >= 1
    assert inst.state in ("done", "failed")


# ── A5 适配器抛异常 ⇒ 收敛成失败动作，⛔ 不穿出 run ────────────────────────

def test_a5_adapter_exception_becomes_failed_action(caplog):
    ad = _Adapter(raise_on=("climate.set_temperature",))
    rt = _runtime(adapter=ad, states={
        "sensor.temp": "28.0", "binary_sensor.door": "off",
        CLIMATE: ("cool", {"hvac_mode": "cool", "temperature": 20.0}),
    })
    with caplog.at_level(logging.INFO, logger="autoforge.executor"):
        inst = _run_until_d2(rt)  # 异常若穿出 run，这里会直接抛
    line = [ln for ln in _lifecycle_lines(caplog) if "stage=action_failed" in ln][0]
    assert "RuntimeError" in line, f"异常类型要进持久面：{line}"
    assert int(inst.ctx.failed_actions) == 1
    assert inst.state == "done", "on_error 短路后应正常收尾，⛔ 不许停在 active"


# ── A6 源码钉：生命周期行带 failed_actions；失败腿走统一入口；恢复侧读计数 ──

def test_a6_source_pins():
    import inspect

    from autoforge import af_executor, af_instance, af_store

    src = inspect.getsource(af_executor.NodeExecutor._lifecycle)
    assert "failed_actions=%d" in src, "生命周期行必须带 failed_actions（终态读数分干净/短路）"

    do_src = inspect.getsource(af_executor.NodeExecutor._do)
    assert "_action_failed_result" in do_src, "失败腿必须走统一收口（不许各写一份）"
    fail_src = inspect.getsource(af_executor.NodeExecutor._action_failed_result)
    assert '"action_failed"' in fail_src, "失败腿必须落 action_failed 生命周期行"
    assert "failed_actions += 1" in fail_src

    soft = inspect.getsource(af_executor.NodeExecutor._soft_fail)
    assert '"action_failed"' in soft and "failed_actions += 1" in soft, \
        "软失效（实体漂移/漂移回滚）同口径计数＋留痕"

    ctx_src = inspect.getsource(af_instance.InstanceContext.to_dict)
    assert '"failed_actions"' in ctx_src, "失败计数要随实例持久化往返"

    restore = inspect.getsource(af_store.restore_context)
    assert 'data.get("failed_actions"' in restore
    assert 'data.get("actions"' in restore, "AF-739 的 actions 恢复侧一直漏读，同批补上"


# ── A7 `no_action` 与失败动作互斥（软失效不再被读成"什么都没做"）──────────

def test_a7_no_action_not_reported_when_action_failed():
    """软失效（动作在发出前就失败）⇒ `actions` 停在 0，但那次 run **不是零动作**。

    这才是那道 guard 的真正射程：没有 `failed_actions==0` 这个前置，`instance_no_action`
    会与 `action_failed` 同时落盘，读者又被带回「什么都没做」的错误归因（现场 02:01 的误读）。
    """
    ad = _Adapter()
    # 故意不给 climate.x 建状态 ⇒ canary 取不到动作前快照 ⇒ `_soft_fail`（动作未发出）
    rt = _runtime(adapter=ad, auto=_auto(d2_canary=False), states={
        "sensor.temp": "28.0", "binary_sensor.door": "off",
    })
    rt.publish(BusEvent.of("binary_sensor.door", "off"))
    inst = rt.instances.all()[0]
    types = [str(e.type) for e in rt.audit]
    assert int(inst.ctx.actions) == 0, "前置：这条腿动作没发出去（actions 应为 0）"
    assert int(inst.ctx.failed_actions) == 1, "前置：失败计数应为 1"
    # 软失效的审计面是 `entity_drift`（`_soft_fail` 的既有口径），持久面是 `action_failed` 行
    assert "entity_drift" in types, types
    assert "instance_no_action" not in types, \
        "动作失败过的 run ⛔ 不许再被标成『零动作』（两个读数并存会把人带回错误归因）"