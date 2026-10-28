"""节点执行器 + 调度器单测：4 种 mode、配额、边优先级、快照边界、软失效。"""

from __future__ import annotations

from autoforge.af_audit import ACTION_FAILED, ENTITY_DRIFT
from autoforge.af_instance import ACTIVE, CANCELLED, DONE, FAILED, SUSPENDED
from autoforge.af_ir import Graph, load_automation
from autoforge.af_runtime import build_runtime
from autoforge.af_scheduler import Quota
from autoforge.af_vhass import FakeHA, FakeHAAdapter

ON_M = {"id": "a1", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}}
P1 = {"id": "p1", "kind": "pass"}
P2 = {"id": "p2", "kind": "pass"}


def _ir(mode="single", nodes=None, edges=None, **over) -> dict:
    data = {
        "ir_version": "0.2.1",
        "id": "demo",
        "name": "示例",
        "version": 1,
        "mode": mode,
        "nodes": nodes if nodes is not None else [ON_M, P1],
        "edges": edges if edges is not None else [{"from": "a1", "to": "p1", "kind": "then"}],
    }
    data.update(over)
    return data


def _rt(data, states=None, **kw):
    runtime = build_runtime(Graph([load_automation(data)]), **kw)
    for k, v in (states or {}).items():
        runtime.states.set_state(k, v)
    return runtime


# ── mode ──────────────────────────────────────────────────────────────


def _ask_ir(mode: str) -> dict:
    """入口 → 询问（60s）→ 各分支，用来制造可观察的挂起实例。"""
    return _ir(
        mode=mode,
        nodes=[
            ON_M,
            {"id": "q1", "kind": "ask", "prompt": "要关灯吗", "room": "study", "timeout": "60s"},
            P1,
            P2,
        ],
        edges=[
            {"from": "a1", "to": "q1", "kind": "then"},
            {"from": "q1", "to": "p1", "kind": "yes"},
            {"from": "q1", "to": "p2", "kind": "on_timeout"},
            {"from": "q1", "to": "p2", "kind": "on_cancel"},
        ],
    )


def test_single_mode_ignores_second_trigger():
    runtime = _rt(_ask_ir("single"))
    runtime.emit("binary_sensor.m", "on", last_changed="t1")
    runtime.clock.advance(1)
    runtime.emit("binary_sensor.m", "on", last_changed="t2")

    assert runtime.instances.count_active() == 1
    assert any("single" in r for r in runtime.scheduler.rejections)


def test_restart_mode_triggers_on_cancel_of_old_instance():
    """⚠️ 与 HA 的有意偏离：restart 会先触发旧实例 on_cancel。"""
    runtime = _rt(_ask_ir("restart"))
    runtime.emit("binary_sensor.m", "on", last_changed="t1")
    old = runtime.instances.all()[0]

    runtime.clock.advance(1)
    runtime.emit("binary_sensor.m", "on", last_changed="t2")

    assert old.state == CANCELLED, "旧实例必须走 on_cancel（清理钩子）"
    assert runtime.instances.count_active() == 1


def test_queued_mode_enqueues():
    runtime = _rt(_ask_ir("queued"), quota=Quota(per_automation=1))
    runtime.emit("binary_sensor.m", "on", last_changed="t1")
    runtime.clock.advance(1)
    runtime.emit("binary_sensor.m", "on", last_changed="t2")

    assert runtime.instances.count_active() == 1
    assert len(runtime.scheduler._queues["demo"]) == 1

    runtime.advance(61)  # 第一个超时结束 → 队列出队
    assert len(runtime.scheduler._queues["demo"]) == 0
    assert runtime.instances.count_active() == 1


def test_parallel_mode_allows_multiple():
    runtime = _rt(_ask_ir("parallel"))
    runtime.emit("binary_sensor.m", "on", last_changed="t1")
    runtime.clock.advance(1)
    runtime.emit("binary_sensor.m", "on", last_changed="t2")
    assert runtime.instances.count_active() == 2


def test_global_quota_rejects():
    runtime = _rt(_ask_ir("parallel"), quota=Quota(global_limit=1))
    runtime.emit("binary_sensor.m", "on", last_changed="t1")
    runtime.clock.advance(1)
    runtime.emit("binary_sensor.m", "on", last_changed="t2")

    assert runtime.instances.count_active() == 1
    assert any(e.type == "quota_exceeded" for e in runtime.audit)


# ── 边优先级与失败 ────────────────────────────────────────────────────


def _do_ir(with_on_error: bool) -> dict:
    edges = [{"from": "a1", "to": "d1", "kind": "then"}, {"from": "d1", "to": "p1", "kind": "then"}]
    if with_on_error:
        edges.append({"from": "d1", "to": "p2", "kind": "on_error"})
    return _ir(
        nodes=[
            ON_M,
            {
                "id": "d1",
                "kind": "do",
                "adapter": "mock",
                "action": "light.turn_on",
                "params": {"entity_id": "light.a"},
            },
            P1,
            P2,
        ],
        edges=edges,
    )


def test_do_failure_goes_to_on_error():
    runtime = _rt(_do_ir(True))
    runtime.adapters.get("mock").set_result("light.turn_on", False)
    runtime.emit("binary_sensor.m", "on", last_changed="t1")

    instance = runtime.instances.all()[0]
    assert instance.state == DONE
    assert instance.ctx.trace[-1]["node"] == "p2"
    assert any(e.type == ACTION_FAILED for e in runtime.audit)


def test_do_failure_without_on_error_ends_failed():
    runtime = _rt(_do_ir(False))
    runtime.adapters.get("mock").set_result("light.turn_on", False)
    runtime.emit("binary_sensor.m", "on", last_changed="t1")

    assert runtime.instances.all()[0].state == FAILED


def test_entity_drift_is_soft_fail_not_immediate_fail():
    """实体漂移 → on_error 软失效 + 漂移告警，**不直接 failed**（IR §14-9）。"""
    runtime = _rt(
        _ir(
            nodes=[
                ON_M,
                {
                    "id": "i1",
                    "kind": "if",
                    "expr": {
                        "op": "gt",
                        "left": {"var": "entity.sensor.gone", "type": "numeric"},
                        "right": {"const": 1},
                    },
                },
                P1,
                P2,
            ],
            edges=[
                {"from": "a1", "to": "i1", "kind": "then"},
                {"from": "i1", "to": "p1", "kind": "then"},
                {"from": "i1", "to": "p2", "kind": "on_error"},
            ],
        )
    )
    runtime.emit("binary_sensor.m", "on", last_changed="t1")

    instance = runtime.instances.all()[0]
    assert instance.state == DONE
    assert instance.ctx.trace[-1]["node"] == "p2"
    assert any(e.type == ENTITY_DRIFT for e in runtime.audit)


def test_wait_then_continues():
    """语义拍板 A：`wait` 到点走 `then` 正常继续，而不是 `on_timeout`。"""
    runtime = _rt(
        _ir(
            nodes=[ON_M, {"id": "w1", "kind": "wait", "duration": "5s"}, P1, P2],
            edges=[
                {"from": "a1", "to": "w1", "kind": "then"},
                {"from": "w1", "to": "p1", "kind": "then"},
                {"from": "w1", "to": "p2", "kind": "on_timeout"},
            ],
        )
    )
    runtime.emit("binary_sensor.m", "on", last_changed="t1")
    assert runtime.instances.all()[0].state == SUSPENDED

    runtime.advance(6)
    inst = runtime.instances.all()[0]
    assert inst.state == DONE
    # wait 到点走 then → p1；那条 on_timeout 边不应被命中
    assert inst.ctx.trace[-1]["node"] == "p1"


# ── atomic 与快照边界 ─────────────────────────────────────────────────


def test_atomic_do_defers_cancel():
    """atomic=true 的 do 必须执行完再响应中断。"""
    runtime = _rt(
        _ir(
            nodes=[
                ON_M,
                {
                    "id": "d1",
                    "kind": "do",
                    "adapter": "mock",
                    "action": "light.turn_on",
                    "params": {"entity_id": "light.a"},
                    "atomic": True,
                },
                {"id": "q1", "kind": "ask", "prompt": "?", "timeout": "60s"},
                P1,
                P2,
            ],
            edges=[
                {"from": "a1", "to": "d1", "kind": "then"},
                {"from": "d1", "to": "q1", "kind": "then"},
                {"from": "q1", "to": "p1", "kind": "yes"},
                {"from": "q1", "to": "p2", "kind": "on_cancel"},
            ],
        )
    )
    instance = runtime.instances.spawn(runtime.graph.get("demo"))
    instance.ctx.current_node = "d1"  # 模拟"正在执行原子动作"

    runtime.executor.cancel(instance, "打断")
    assert instance.state == ACTIVE, "原子动作执行完之前不得中断"
    assert instance.ctx.context.get("cancel_pending") is True

    runtime.executor.run(instance)  # 原子动作执行完 → 立即走取消
    assert instance.state == CANCELLED


def test_snapshot_boundary_do_result_does_not_rewrite_snapshot():
    """IR §7.2：段内 `do` 造成的外部状态变化**不回写当前快照**。"""
    runtime = _rt(
        _ir(
            nodes=[
                ON_M,
                {
                    "id": "d1",
                    "kind": "do",
                    "adapter": "ha",
                    "action": "light.turn_on",
                    "params": {"entity_id": "light.main"},
                },
                {
                    "id": "i1",
                    "kind": "if",
                    "expr": {"op": "is_off", "value": {"var": "entity.light.main"}},
                },
                {"id": "s1", "kind": "set", "var": "still_off", "value": 1},
                P1,
            ],
            edges=[
                {"from": "a1", "to": "d1", "kind": "then"},
                {"from": "d1", "to": "i1", "kind": "then"},
                {"from": "i1", "to": "s1", "kind": "then"},
                {"from": "i1", "to": "p1", "kind": "no"},
                {"from": "s1", "to": "p1", "kind": "then"},
            ],
        )
    )
    ha = FakeHA()
    ha.set("light.main", "off").set("binary_sensor.m", "off")
    runtime.states = ha
    runtime.instances.states = ha
    runtime.scheduler.states = ha
    runtime.adapters.register(FakeHAAdapter(ha))

    runtime.emit("binary_sensor.m", "on", last_changed="t1")

    instance = runtime.instances.all()[0]
    assert ha.get("light.main") == "on", "设备真的开了"
    assert instance.ctx.vars.get("still_off") == 1, "同一求值段内仍读到旧快照（灯还是 off）"


# ── 审计缺陷 2026-10-28：`_drain_queues` 出队写死第一个入口节点 ─────────
# 旧实现 `self._try_fire(auto, auto.entry_nodes()[0], event)`：入队时保留的事件
# 到了出队丢了归属 ⇒ 双入口以上的 queued 自动化，排在第二个入口的事件被派到第一个
# 分支（静默执行错误动作）。相邻两条触发路径都是按节点取的（handle_event 用匹配
# 到的 node、_fire_for_due 用 `auto.node(pending.node_id)`），只有这一站用位置索引。

def _two_entry_ir(mode: str = "queued") -> dict:
    """两个入口各挂一个 ask：a1←binary_sensor.m→q1、a2←binary_sensor.n→q2。"""
    return _ir(
        mode=mode,
        nodes=[
            ON_M,
            {"id": "q1", "kind": "ask", "prompt": "M 要开吗", "room": "study", "timeout": "60s"},
            {"id": "a2", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.n", "to": "on"}},
            {"id": "q2", "kind": "ask", "prompt": "N 要开吗", "room": "study", "timeout": "60s"},
            P1,
        ],
        edges=[
            {"from": "a1", "to": "q1", "kind": "then"},
            {"from": "q1", "to": "p1", "kind": "on_timeout"},
            {"from": "a2", "to": "q2", "kind": "then"},
            {"from": "q2", "to": "p1", "kind": "on_timeout"},
        ],
    )


def test_queued_drain_dispatches_to_the_entry_that_matched():
    """排在第二个入口上的事件，出队后必须仍走第二个分支（queued 的核心承诺）。"""
    runtime = _rt(_two_entry_ir(), states={"binary_sensor.m": "off", "binary_sensor.n": "off"},
                  quota=Quota(per_automation=1))
    runtime.emit("binary_sensor.m", "on", last_changed="t1")
    runtime.clock.advance(1)
    runtime.emit("binary_sensor.n", "on", last_changed="t2")   # 并发满 → 进队列

    queue = runtime.scheduler._queues["demo"]
    assert len(queue) == 1 and queue[0].node_id == "a2", "入队时必须记住归属入口节点"

    runtime.advance(61)  # 第一个超时结束 → 出队
    latest = sorted(runtime.instances.all(), key=lambda i: i.created_monotonic)[-1]
    assert latest.ctx.current_node == "q2", (
        f"出队实例被派到 {latest.ctx.current_node}（应为 q2）——静默执行错误分支"
    )


def test_queued_drain_drops_event_with_unknown_entry_visibly():
    """归属节点查不到时要**可见地丢弃**，⛔ 不许退回 entry_nodes()[0] 走错分支。"""
    from autoforge.af_bus import BusEvent
    from autoforge.af_scheduler import QueuedTrigger
    runtime = _rt(_two_entry_ir(), states={"binary_sensor.m": "off"}, quota=Quota(per_automation=1))
    runtime.scheduler._queues["demo"] = __import__("collections").deque(
        [QueuedTrigger("a_gone", BusEvent.of("binary_sensor.m", "on", last_changed="t0"))])
    runtime.advance(1)
    assert runtime.instances.count_active() == 0, "坏归属不该凭位置索引 spawn 出错分支的实例"
    assert any("已不存在" in e.message or "入口节点" in e.message for e in runtime.audit),         "丢弃必须留审计，不能静默"


def test_queued_single_entry_still_works():
    """护栏：单入口 queued 不受影响（旧行为不能因为这次改动退化）。"""
    runtime = _rt(_ask_ir("queued"), states={"binary_sensor.m": "off"}, quota=Quota(per_automation=1))
    runtime.emit("binary_sensor.m", "on", last_changed="t1")
    runtime.clock.advance(1)
    runtime.emit("binary_sensor.m", "on", last_changed="t2")
    assert len(runtime.scheduler._queues["demo"]) == 1
    runtime.advance(61)
    assert len(runtime.scheduler._queues["demo"]) == 0
    assert runtime.instances.count_active() == 1
