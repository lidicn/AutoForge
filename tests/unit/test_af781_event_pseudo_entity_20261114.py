# -*- coding: utf-8 -*-
"""AF-781 判据 · 快照层剔掉 `event.*` 伪实体（2026-11-14）。

**缺陷**：`on event "x"` 的自动化，`Trigger.entity_ids()` 会把事件记成 `event.x`
（为的是跨自动化依赖矩阵能看见**事件边**）。这句伪造实体落进 `reads()` 后，又被
`InstanceManager._refresh_snapshot` 当普通读取实体送进 `states.snapshot()` ⇒ 状态源对它
必抛 `UnknownEntity` ⇒ 回退成**空快照** ⇒ 该实例每个 `if` 都拿不到真实实体状态、
一律 `UnknownEntity` ⇒ **整条 `on event → if → do` 自动化静默软失效**，audit 只留
`entity_drift`，看不出真凶是个伪实体。

**修法（AF-PM 2026-11-14 裁定方案 b，⛔ 不采 a/c）**：快照集合先剔 `event.*`。
(a) 给四个 Provider 补 `has()`、(c) 逐实体容错——两条都会把「真实实体漂移＝整段软失效」
改成"静默排除/只失效一支"，那是既裁语义（P1-12／第七轮审计），⛔ 不许修 bug 顺带改。

**判据**：

- E3 缺陷腿转绿：`on(event "ping")` ＋ `if(真实实体)` ⇒ spawn 后 if 读到真实状态、`do` 真下发；
  且快照里**没有** `event.ping`、audit **没有** `entity_drift`
- R1 语义不回退钉：**真实实体漂移**（改名场景）仍**整段软失效**、audit 含 `entity_drift`
  ——证明修的是伪实体，不是把漂移语义改宽
"""

from __future__ import annotations

from typing import Any

from autoforge.af_bus import BusEvent
from autoforge.af_ir import Graph, load_automation
from autoforge.af_runtime import build_runtime

PING = "af781_ping"
REAL = "binary_sensor.motion_study"     # 真实实体：if 靠它判
LAMP = "switch.desk_lamp"

AUTO: dict[str, Any] = {
    "ir_version": "0.2.1", "id": "af781_event_if", "name": "AF781 事件触发＋条件",
    "version": 1, "mode": "single",
    "nodes": [
        {"id": "t1", "kind": "on", "name": "收到事件",
         "trigger": {"type": "event", "event": PING}},
        {"id": "i1", "kind": "if", "name": "人在书房（读真实实体快照）",
         "expr": {"op": "is_on", "value": {"var": f"entity.{REAL}"}}},
        {"id": "d1", "kind": "do", "name": "开台灯", "adapter": "ha",
         "action": "switch.turn_on", "params": {"entity_id": LAMP}},
        {"id": "p1", "kind": "pass", "name": "结束"},
    ],
    "edges": [
        {"from": "t1", "to": "i1", "kind": "then"},
        {"from": "i1", "to": "d1", "kind": "then"},
        {"from": "i1", "to": "p1", "kind": "no"},
        {"from": "d1", "to": "p1", "kind": "then"},
    ],
}


class _RecorderAdapter:
    name = "ha"
    dry_run = False

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def call(self, action: str, params: dict):
        self.calls.append((action, dict(params)))
        from autoforge.af_adapters.base import CallResult

        return CallResult(success=True, data={}, error=None)


def _runtime(*, states: dict[str, str]):
    rt = build_runtime(Graph([load_automation(AUTO)]))
    rec = _RecorderAdapter()
    rt.adapters.register(rec)
    for k, v in states.items():
        rt.states.set_state(k, v)
    return rt, rec


def _emit_ping(rt):
    return rt.publish(BusEvent.of(f"event.{PING}", PING))


def _types(rt) -> list[str]:
    return [str(e.type) for e in rt.audit]


# ── E3 缺陷腿转绿：事件触发 ＋ if 读真实实体 ─────────────────────────
def test_e3_event_trigger_with_if_reads_real_state():
    """判据 E3：`on(event)` 的自动化，`if` 必须能读到**真实实体**的状态。

    修前（缺陷形状）：`reads()` 含 `event.ping` ⇒ `snapshot()` 抛 ⇒ 空快照 ⇒
    `if` 抛 `UnknownEntity` ⇒ `entity_drift` 软失效 ⇒ `do` 一次都不下发。
    """
    rt, rec = _runtime(states={REAL: "on"})

    fired = _emit_ping(rt)

    assert len(fired) == 1, "前置：事件必须能触发出实例"
    assert rec.calls == [("switch.turn_on", {"entity_id": LAMP})], (
        "if 必须读到真实实体（on）并下发动作；实际 %r ——⛔ 说明快照又塌成空了" % (rec.calls,)
    )
    assert "entity_drift" not in _types(rt), (
        "⛔ 不许再出现 entity_drift 软失效（那正是 AF-781 的现场形状）"
    )

    inst = rt.instances.all()[-1]
    snap = inst.snapshot.to_dict().get("values", {}) if inst.snapshot is not None else {}
    assert REAL in snap, "真实实体必须在快照里"
    assert f"event.{PING}" not in snap, (
        "⛔ event.* 是触发域伪实体，不许进状态快照（AF-781 修法 b）"
    )


# ── R1 语义不回退：真实实体漂移仍整段软失效 ───────────────────────────
def test_r1_real_entity_drift_still_soft_fails():
    """判据 R1（反证腿）：真实实体**漂移**（改名/删除）⇒ 仍整段软失效、audit 记 entity_drift。

    ⛔ 没有这条，可以把 `_refresh_snapshot` 改成"未知实体静默跳过"来让 E3 变绿——
    那会把「单个实体漂移＝整段软失效」（P1-12／第七轮审计的既裁语义）改成"只失效一支"，
    现场就会出现"条件用了不存在的实体、却按不满足继续跑"的假绿。
    本卡只剔**域外伪实体**，不碰域内漂移语义。
    """
    # REAL 故意不放进状态源 ≡ 实体被改名/删除
    rt, rec = _runtime(states={})

    _emit_ping(rt)

    assert rec.calls == [], "真实实体漂移 ⇒ if 求值必然失败 ⇒ ⛔ 不许下发动作"
    assert "entity_drift" in _types(rt), (
        "真实实体漂移必须仍走 entity_drift 软失效（口径不变）"
    )
    inst = rt.instances.all()[-1]
    snap = inst.snapshot.to_dict().get("values", {}) if inst.snapshot is not None else {}
    assert snap == {}, "回退形态仍是**空快照**（整段软失效），不是「只少一支」"


# ── R2 快照集合不变：真实实体一个不少 ─────────────────────────────────
def test_r2_snapshot_still_contains_all_real_entities():
    """反证腿（另一面）：剔的只是 `event.*`，真实实体一个都不许少。

    ⛔ 若把过滤写成"只留 if 里提到的实体"或误删前缀判断，本腿红。
    """
    rt, _ = _runtime(states={REAL: "on", "sensor.humidity": "55", LAMP: "off"})

    fired = _emit_ping(rt)
    assert len(fired) == 1

    inst = rt.instances.all()[-1]
    snap = inst.snapshot.to_dict().get("values", {})
    assert REAL in snap, "触发域之外、被 reads 引用的真实实体必须进快照"
    assert all(not k.startswith("event.") for k in snap), "快照里⛔ 不许残留任何 event.* 键"
