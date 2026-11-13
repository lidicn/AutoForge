# -*- coding: utf-8 -*-
"""AF-773 判据 · `sweep_orphans()` 接进 tick 尾（2026-11-13，B3-AF-02 幽灵 ask 会话）。

**缺口**：`NodeExecutor.sweep_orphans()` 早就写好了（终态实例残留的 `pending_asks` 出队），
docstring 写着"定期调用（如每次 tick 后）"——**但生产代码里一个调用方都没有**，只有单测在调。
后果：实例已经走到终态、`pending_asks` 里还挂着那条会话 ⇒ `/api/asks` 与 watch sidecar
一直把一条「问过、但永远不会有人答」的问句摆出来。

**修法**：`Runtime.tick()` 收尾调一次（放在 `expire_stale()` 之后，让超时/过期腿先落定终态）。
纯内存遍历（键数＝在跑 ask 会话数），逐 tick 成本可忽略；⛔ 不改 `sweep_orphans` 本体语义。

**判据**：

- S1 源码钉：`Runtime.tick` 里真有调用，且在 `scheduler.tick()` 之后、`expire_stale()` 之后
- S2 行为：实例被移出管理器（崩溃/手工清理形状）而会话仍在 ⇒ **一次 tick 后清空**、读数归零、
  审计留 `ASK_EXPIRED(reason=sweep)`
- S3 反证：**正常挂起的 ask 跨多 tick ⛔ 不许被误清**（否则等于把"问了在等答"整条腿删掉）
"""

from __future__ import annotations

from typing import Any

from autoforge.af_bus import BusEvent
from autoforge.af_ir import Graph, load_automation
from autoforge.af_runtime import build_runtime

OCC = "binary_sensor.occupancy_main_bedroom"

AUTO: dict[str, Any] = {
    "ir_version": "0.2.1", "id": "af773_ask_ghost", "name": "AF773 幽灵会话",
    "version": 1, "mode": "single",
    "nodes": [
        {"id": "t1", "kind": "on", "name": "人在",
         "trigger": {"type": "state", "entity_id": OCC, "to": "on"}},
        {"id": "q1", "kind": "ask", "name": "问",
         "prompt": "要开净化器吗？", "session": "room", "room": "zhu_wo_shi",
         "timeout": "600s"},
        {"id": "p1", "kind": "pass", "name": "结束"},
    ],
    "edges": [
        {"from": "t1", "to": "q1", "kind": "then"},
        {"from": "q1", "to": "p1", "kind": "yes"},
        {"from": "q1", "to": "p1", "kind": "no"},
        {"from": "q1", "to": "p1", "kind": "on_timeout"},
        {"from": "q1", "to": "p1", "kind": "default"},
    ],
}


def _rt_with_suspended_ask():
    """装配一个已经挂起 ask 的运行时，返回 `(rt, instance)`。"""
    rt = build_runtime(Graph([load_automation(AUTO)]))
    rt.states.set_state(OCC, "off")
    rt.states.set_state(OCC, "on")
    rt.publish(BusEvent.of(OCC, "on"))
    assert len(rt.executor.pending_asks) == 1, "前置：ask 必须挂起"
    inst = next(iter(rt.executor.pending_asks.values()))
    instance = rt.instances.get(inst.instance_id)
    assert instance is not None
    return rt, instance


# ── S1 源码钉：tick 尾真有调用 ────────────────────────────────────────
def test_s1_source_pin_tick_calls_sweep_orphans():
    """判据 a：`Runtime.tick` 里必须有 `sweep_orphans()`，且排在各步之后。

    ⛔ 只钉"调用存在 + 顺序"，不钉行号（行号归可观测性清单管）。
    """
    import inspect

    from autoforge.af_runtime import Runtime

    src = inspect.getsource(Runtime.tick)
    # ⛔ 必须钉**调用表达式**，不能钉裸名字：注释里也写着 `sweep_orphans()`，
    # 裸名判定会让"只留注释、真删调用"的变异照样绿（本卡第一版就这么假绿过一次）。
    CALL = "self.executor.sweep_orphans()"
    assert CALL in src, "tick 必须真调用 sweep_orphans（AF-773 根因：写好了没人按）"
    assert src.index("self.scheduler.tick()") < src.index(CALL), \
        "必须先跑调度 tick 再扫幽灵（否则本 tick 刚终态的实例要等下一轮）"
    assert src.index("expire_stale()") < src.index(CALL), \
        "必须排在 expire_stale 之后：先让超时/过期腿落定终态，再扫漏挂会话"


# ── S2 行为：终态/失踪实例的幽灵会话，一次 tick 清空 ──────────────────
def test_s2_ghost_session_cleared_after_one_tick():
    """判据 b：实例已不在管理器里（≡ 崩溃后未恢复／被清理），会话还在 ⇒ tick 后归零。

    现场症状对照组：不清时 `pending_asks` 永远留着那条会话，前端一直显示待答。
    """
    rt, instance = _rt_with_suspended_ask()
    assert rt.stats()["pending_asks"] == 1, "前置：读数 1"

    # 造幽灵：实例被移出管理器，但会话仍挂在 pending_asks（真实成因之一）
    rt.instances.remove(instance)
    assert len(rt.executor.pending_asks) == 1, "前置：会话还在（这才是幽灵形状）"

    rt.tick()

    assert len(rt.executor.pending_asks) == 0, "一次 tick 后幽灵会话必须清空"
    assert rt.stats()["pending_asks"] == 0, "健康面读数必须归零（/api/asks 读的就是这个）"
    expired = [e for e in rt.audit if str(e.type) == "ask_expired"]
    assert expired, "清理必须留 ASK_EXPIRED 审计（⛔ 不许静默吞掉一条问句）"
    assert expired[-1].data.get("reason") == "sweep", "reason 必须是 sweep（三径可分：answer/timeout/sweep）"


# ── S3 反证：正常挂起的 ask ⛔ 不许被误清 ─────────────────────────────
def test_s3_live_ask_survives_many_ticks():
    """反证腿：实例还在、只是**在等回答** ⇒ 跨多个 tick ⛔ 不许被扫掉。

    没有这条，「一 tick 清空」可以靠"每 tick 把所有会话都删了"蒙成绿——那等于把
    ask 整条腿删掉（用户答回来的会话永远对不上）。
    """
    rt, instance = _rt_with_suspended_ask()
    for _ in range(5):
        rt.tick()

    assert len(rt.executor.pending_asks) == 1, "在等回答的会话⛔ 不许被扫掉"
    assert rt.stats()["pending_asks"] == 1
    assert rt.instances.get(instance.instance_id) is not None, "实例本身也不许动"
    assert not [e for e in rt.audit if str(e.type) == "ask_expired"], \
        "正常挂起不该产生 ask_expired"

    # 对照：这时候人真的答了，腿还通（扫过 5 轮之后仍能应答）
    fired = rt.executor.answer("zhu_wo_shi", "好的")
    assert fired is not None, "扫过之后人工应答仍必须能走通（⛔ 不许把会话扫成孤儿）"