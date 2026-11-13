# -*- coding: utf-8 -*-
"""AF-782 判据 · simulate 回执把 cancelled 从 failed 里剥出来（2026-11-14，§1.220 裁定）。

**缺陷**：`simulate_track` 的 verdict 分支把 `cancelled` 与 `failed` 并成同一类
（`state in ("failed", "cancelled")`）⇒ `mode=restart` 的自动化每预演一次就报
`verdict=failed`：第二次触发会先 cancel 第一次的实例（`docs/HA_SEMANTIC_DIFF.md` 明写的
有意偏离），于是一片正常图永远预演红，**真失败反而淹在噪声里**（QA-MCP R6 的 restart
双触发腿就是这么被误判的）。附带一处：被取消实例的 `reason` 读的是 `fail_reason`，
而 cancelled 实例根本没有那个键 ⇒ 回执里恒空串，等于"取消了但不知道为什么"。

**修法**：cancelled 单独进 `cancelled_instances`，`reason` 取 `context.cancel_reason`；
verdict 分三档——存在 failed ⇒ `failed`（`failed_instances` 照旧）；纯 cancelled ⇒
`ran` ＋ `cancelled_instances` 明示；混合 ⇒ `failed` ＋ 两清单。⛔ 不丢信息
（2026-10-29 反假绿意图保持：取消照样列出来，只是不再冒充失败）。

**判据**：

- L1 restart 双触发（QA-MCP R6 同构造）⇒ `verdict=ran`、`ok=True`、`cancelled_instances`
  含旧实例且 `reason` 非空（含 restart）
- L2 纯异常腿 ⇒ 仍 `failed`、`failed_instances` 指明原因、**不出现** `cancelled_instances`
- L3 混合腿 ⇒ `failed` ＋ **两清单都在**（⛔ 不许把取消吞掉）
- L4 诚实档不塌：一次都没跑出实例时仍是 `nothing_triggered`
"""

from __future__ import annotations

import json
from typing import Any

from autoforge.af_service import simulate, simulate_track

M = "binary_sensor.m"
SW = "switch.x"


def _wait_ir(*, mode: str = "restart", auto_id: str = "af782_restart") -> dict[str, Any]:
    """`on(m to=on) → wait(30s) → pass`：restart 模式下第二次触发会 cancel 第一个实例。"""
    return {
        "ir_version": "0.2.1", "id": auto_id, "name": "AF782 抢占腿",
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


def _fail_ir(*, auto_id: str = "af782_fail") -> dict[str, Any]:
    """`on(m to=on) → if(数值比较读到非数值) → pass`：求值失败、无 on_error ⇒ 实例 failed。"""
    return {
        "ir_version": "0.2.1", "id": auto_id, "name": "AF782 失败腿",
        "version": 1, "mode": "single",
        "nodes": [
            {"id": "t1", "kind": "on", "name": "人",
             "trigger": {"type": "state", "entity_id": M, "to": "on"}},
            {"id": "i1", "kind": "if", "name": "数值条件",
             "expr": {"op": "gt", "left": {"var": f"entity.{SW}", "type": "numeric"},
                      "right": {"const": 1}}},
            {"id": "p1", "kind": "pass", "name": "结束"},
        ],
        "edges": [
            {"from": "t1", "to": "i1", "kind": "then"},
            {"from": "i1", "to": "p1", "kind": "then"},
        ],
    }


def _two_triggers() -> list[dict[str, Any]]:
    """同一实体 on → off → on：足够把 restart 的旧实例顶掉。

    ⚠️ 每条都带**不同的 `last_changed`**：总线去重键是 `(entity_id, state, last_changed)`
    （`BusEvent.dedup_key`），同值事件若都写 `None` 会被判重复直接丢掉——那会让本腿
    假绿成"restart 没抢占"（真实 HA 事件本来就带时间戳）。
    """
    return [
        {"entity_id": M, "state": "on", "last_changed": "2026-11-14T01:00:00+08:00"},
        {"advance_s": 5},
        {"entity_id": M, "state": "off", "last_changed": "2026-11-14T01:00:05+08:00"},
        {"entity_id": M, "state": "on", "last_changed": "2026-11-14T01:00:10+08:00"},
    ]


# ── L1 restart 双触发：cancelled ≠ failed ────────────────────────────
def test_l1_restart_double_trigger_reports_ran_with_cancelled():
    """判据 a（QA-MCP R6 同构造）：restart 抢占产生的 cancelled ⛔ 不许申报成 failed。"""
    r = simulate(_wait_ir(), seed={M: "off"}, events=_two_triggers())

    assert r["verdict"] == "ran", "restart 抢占是设计内动作 ⇒ 必须是 ran：%s" % json.dumps(r)[:300]
    assert r["ok"] is True, "纯 cancelled 不许落 ok=False（修前这里恒 False）"
    assert "failed_instances" not in r, "⛔ cancelled 不许再出现在 failed_instances 里"

    cancelled = r.get("cancelled_instances") or []
    assert len(cancelled) == 1, "应恰好一条被取消实例，实得 %r" % (cancelled,)
    row = cancelled[0]
    assert row["instance_id"], "必须点名是哪一条实例"
    assert row["reason"], "reason ⛔ 不许是空串（修前读 fail_reason ⇒ 恒空）"
    assert "restart" in row["reason"], "reason 必须说清来源（restart 抢占）：%r" % (row["reason"],)
    assert "cancelled_instances" in r and r.get("note"), "纯 cancelled 档必须留一句人话说明"


# ── L2 纯异常腿：仍 failed ──────────────────────────────────────────
def test_l2_pure_failure_still_failed():
    """判据 b（反证腿）：真失败⛔ 不许因为这次改动被放行成 ran。"""
    r = simulate(_fail_ir(), seed={M: "off", SW: "off"},
                 events=[{"entity_id": M, "state": "on"}])

    assert r["ok"] is False and r["verdict"] == "failed", json.dumps(r)[:300]
    assert r["failed_instances"], "必须列 failed_instances"
    assert "numeric" in r["failed_instances"][0]["reason"], "reason 必须指明求值失败原因"
    assert "cancelled_instances" not in r, "纯失败档⛔ 不许长出一张空清单"


# ── L3 混合腿：failed ＋ 两清单 ─────────────────────────────────────
def test_l3_mixed_failure_and_cancel_keeps_both_lists():
    """判据 c：既有 cancelled 又有 failed ⇒ `failed` 压档，但**两清单都在**（⛔ 不丢信息）。"""
    graph = {"automations": [_wait_ir(), _fail_ir()]}
    r = simulate_track("fake", graph, seed={M: "off", SW: "off"}, events=_two_triggers())

    assert r["verdict"] == "failed", "有真失败 ⇒ 必须 failed（取消不许把它冲淡）"
    assert r["ok"] is False
    assert r["failed_instances"], "失败清单必须在"
    cancelled = r.get("cancelled_instances") or []
    assert cancelled and cancelled[0]["reason"], (
        "混合档也必须保留取消清单与原因（⛔ 2026-10-29 的反假绿意图不许被这次改动抹掉）"
    )


# ── L4 诚实档不塌 ───────────────────────────────────────────────────
def test_l4_nothing_triggered_still_flagged():
    """判据（回归）：一次实例都没跑出来 ⇒ 仍是 `nothing_triggered`，⛔ 不混成 ran。"""
    r = simulate_track("fake", _wait_ir(), seed={M: "off"}, events=[])

    assert r["verdict"] == "nothing_triggered" and r["ok"] is True
    assert r.get("nothing_verified") is True
    assert "cancelled_instances" not in r and "failed_instances" not in r
