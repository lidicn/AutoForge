# -*- coding: utf-8 -*-
"""AF-783 判据 · cancel 路径补 `INSTANCE_CANCELLED` 审计（2026-11-14）。

**缺口**：`Executor.cancel()` 会走 `on_cancel`、会把实例落成 cancelled，但**一行审计都没有**
⇒ 现场问「这条实例怎么没了 / 为什么没跑完」只能靠猜（QA-MCP R6：「audit 翻不到 cancel」）。
两条来源共用这一条路：`mode=restart` 的新触发抢占旧实例（`reason="<auto_id> restart"`）、
人工/编排面 `cancel(...)`（`reason=` 调用方文案）。

**修法**：在**真正落到 `on_cancel`** 的那一格落 INFO 审计（⛔ 不放函数开头——atomic do 的
首轮调用只置 `cancel_pending` 就返回，动作跑完会再进来一次，开头记会重复两笔），
`data` 带 `reason` 与 `channel`。

**判据**：

- X1 restart 抢占 ⇒ 一笔 `instance_cancelled`，`reason` 含 `<auto_id> restart`
- X2 人工取消 ⇒ 一笔，`reason` 是调用方文案
- X3 atomic do 的首轮调用⛔ 不落痕（只在真正取消那一刻记一笔，不重复）
- X4 事件类型在册：`INSTANCE_CANCELLED` 必须是 `ALL_EVENT_TYPES` 的成员（契约枚举真源）
"""

from __future__ import annotations

import json
from typing import Any

from autoforge import af_audit
from autoforge.af_bus import BusEvent
from autoforge.af_ir import Graph, load_automation
from autoforge.af_runtime import build_runtime

M = "binary_sensor.m"
CODE = "instance_cancelled"


def _wait_ir(*, mode: str = "restart") -> dict[str, Any]:
    return {
        "ir_version": "0.2.1", "id": "af783_restart", "name": "AF783 抢占",
        "version": 1, "mode": mode,
        "nodes": [
            {"id": "t1", "kind": "on", "name": "人",
             "trigger": {"type": "state", "entity_id": M, "to": "on"}},
            {"id": "w1", "kind": "wait", "name": "等", "duration": "30s"},
            {"id": "p1", "kind": "pass", "name": "结束"},
        ],
        "edges": [
            {"from": "t1", "to": "w1", "kind": "then"},
            {"from": "w1", "to": "p1", "kind": "then"},
        ],
    }


class _Adapter:
    name = "ha"
    dry_run = False

    def call(self, action: str, params: dict):
        from autoforge.af_adapters.base import CallResult

        return CallResult(success=True, data={}, error=None)


def _rt(ir: dict[str, Any] | None = None):
    rt = build_runtime(Graph([load_automation(ir or _wait_ir())]))
    rt.adapters.register(_Adapter())
    rt.states.set_state(M, "off")
    return rt


def _cancel_audits(rt) -> list:
    return [e for e in rt.audit if str(e.type) == CODE]


# ── X1 restart 抢占：留痕一笔且点名 restart ──────────────────────────
def test_x1_restart_cancel_is_audited():
    """判据 a（QA-MCP R6 腿）：restart 抢占旧实例 ⇒ audit 里能翻到 `instance_cancelled`。"""
    rt = _rt()
    rt.emit(M, "on", last_changed="2026-11-14T01:00:00+08:00")
    assert len(rt.instances.active_of("af783_restart")) == 1, "前置：第一个实例挂起中"

    rt.emit(M, "off", last_changed="2026-11-14T01:00:05+08:00")
    rt.emit(M, "on", last_changed="2026-11-14T01:00:10+08:00")  # 新触发 ⇒ 抢占

    hits = _cancel_audits(rt)
    assert len(hits) == 1, "计划内抢占必须恰好留一笔，实得 %d" % len(hits)
    ev = hits[0]
    assert ev.data.get("reason") and "restart" in ev.data["reason"], (
        "reason 必须说清来源（restart 抢占），实得 %r" % (ev.data.get("reason"),)
    )
    assert ev.instance_id, "必须点名是哪条实例"
    assert ev.automation_id == "af783_restart"
    assert ev.data.get("channel") == "cancel"


# ── X2 人工取消：留痕一笔且带调用方文案 ─────────────────────────────
def test_x2_manual_cancel_is_audited():
    """判据 a（另一半）：人工/编排面 cancel ⇒ 同样留痕，reason 用调用方文案。"""
    rt = _rt()
    rt.emit(M, "on", last_changed="2026-11-14T02:00:00+08:00")
    inst = rt.instances.active_of("af783_restart")[0]

    rt.cancel(inst, reason="出资人叫停")

    hits = _cancel_audits(rt)
    assert len(hits) == 1
    assert hits[0].data["reason"] == "出资人叫停"
    assert str(inst.ctx.state) == "cancelled", "实例本身仍必须落 cancelled"


# ── X3 atomic do 首轮⛔ 不重复记 ────────────────────────────────────
def test_x3_atomic_deferred_cancel_audited_once():
    """判据（防重复）：atomic do 执行中被打断 ⇒ 先置 `cancel_pending`、动作跑完才真取消。

    现场形状：动作还在下发（同步调用里被抢占/被人工叫停），此时 `ctx.state == ACTIVE`、
    当前节点是 `do` 且 `atomic=True` ⇒ `cancel()` 只置 pending 就返回。等动作结束、执行器
    把游标推到下一节点后**再调一次** `cancel()` 才真正落 `on_cancel`。
    ⇒ 留痕必须**只**落在后面那次：⛔ 两笔 `instance_cancelled` 会把"取消了几次"读成两次。
    """
    ir = {
        "ir_version": "0.2.1", "id": "af783_atomic", "name": "AF783 atomic",
        "version": 1, "mode": "restart",
        "nodes": [
            {"id": "t1", "kind": "on", "name": "人",
             "trigger": {"type": "state", "entity_id": M, "to": "on"}},
            {"id": "d1", "kind": "do", "name": "原子动作", "adapter": "ha",
             "action": "switch.turn_on", "params": {"entity_id": "switch.x"}, "atomic": True},
            {"id": "p1", "kind": "pass", "name": "结束"},
        ],
        "edges": [
            {"from": "t1", "to": "d1", "kind": "then"},
            {"from": "d1", "to": "p1", "kind": "then"},
        ],
    }
    holder: dict[str, Any] = {}

    class _ReentrantAdapter(_Adapter):
        """在动作下发**进行中**叫停：复现 atomic do 被抢占的那一瞬间。"""

        def call(self, action: str, params: dict):
            inst = holder.get("inst")
            if inst is not None and not holder.get("fired"):
                holder["fired"] = True
                inst.ctx.context.pop("__noop", None)
                # 此刻 current_node == "d1"、state == ACTIVE ⇒ 走 atomic 延迟分支
                rt.cancel(inst, reason="动作执行中被打断")
                holder["audits_at_call"] = len(_cancel_audits(rt))
            return super().call(action, params)

    rt = build_runtime(Graph([load_automation(ir)]))
    rt.adapters.register(_ReentrantAdapter())
    rt.states.set_state(M, "off")
    # 钩子要在 spawn 之前拿到实例：先建一个空跑拿 id 太重，改用 on_spawn 回调
    rt.instances.on_spawn = lambda inst: holder.__setitem__("inst", inst)

    rt.emit(M, "on", last_changed="2026-11-14T03:00:00+08:00")

    assert holder.get("fired") is True, "前置：动作确实在跑且被中途叫停"
    assert holder.get("audits_at_call") == 0, (
        "atomic 首轮只置 cancel_pending，⛔ 不许提前记一笔（否则同一轮取消记两次）"
    )
    hits = _cancel_audits(rt)
    assert len(hits) == 1, "动作结束后真正落 on_cancel ⇒ 恰好一笔，实得 %d" % len(hits)
    assert hits[0].data["reason"] == "动作执行中被打断"


# ── X4 事件类型在册（契约枚举真源）──────────────────────────────────
def test_x4_event_type_registered():
    """判据：新码必须在 `ALL_EVENT_TYPES` 里——API 契约的枚举就是从这里取的。"""
    assert af_audit.INSTANCE_CANCELLED == CODE
    assert CODE in af_audit.ALL_EVENT_TYPES, "新事件类型必须进注册表（否则契约枚举与盘上不一致）"
    assert len(af_audit.ALL_EVENT_TYPES) == len(set(af_audit.ALL_EVENT_TYPES)), "不许重名"
    # 相邻两个码语义不许混：取消 ≠ 过期
    assert af_audit.INSTANCE_EXPIRED != CODE
    assert json.dumps({"reason": "x", "channel": "cancel"})  # 数据面可 JSON 化（dossier 断言同源）
