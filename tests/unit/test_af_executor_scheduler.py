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


def test_wait_timeout():
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
    assert runtime.instances.all()[0].state == DONE
    assert runtime.instances.all()[0].ctx.trace[-1]["node"] == "p2"


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
