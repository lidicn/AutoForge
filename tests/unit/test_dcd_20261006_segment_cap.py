"""DCD 裁定 `20261006-AF配对与段间封顶与DPP四件与MA三件-裁定.md` §二 的落地判据。

问题一（A+B 组合）：跨段累计 **S=1000 段 / T=20000 步** 越档先告警（WARNING + `AuditLog` +
监护视图常驻指示，每实例只发一次），到 **2×** 才 `_fail`。
问题二（B）：trace 保最近 **N=1000** 条，被丢的条数记进 `trace_dropped`。

这三条阈值由 DCD 定、AF 不自签，所以阈值本身也被钉进测试（下一条改阈值的人要同时改裁定）。
"""

from unittest.mock import MagicMock

import pytest

from autoforge import af_watch
from autoforge.af_executor import (
    CAP_WARNING,
    HARD_CAP_MULTIPLIER,
    MAX_SEGMENTS_PER_INSTANCE,
    MAX_STEPS_PER_INSTANCE,
    NodeExecutor,
)
from autoforge.af_instance import MAX_TRACE_ENTRIES, Instance, InstanceContext
from autoforge.af_store import restore_context

#: 钉死裁定原文的三个数——"顺手把阈值调松一点"必须先把裁定改了才做得动。
def test_thresholds_are_the_ones_dcd_signed():
    assert (MAX_SEGMENTS_PER_INSTANCE, MAX_STEPS_PER_INSTANCE) == (1000, 20000)
    assert HARD_CAP_MULTIPLIER == 2
    assert MAX_TRACE_ENTRIES == 1000


def _wait_node():
    node = MagicMock()
    node.kind = "wait"
    node.emit = None
    node.id = "w1"
    return node


def _instance(automation_id: str = "auto_cap") -> Instance:
    """`wait → (唤醒) → wait` 那种经挂起点的环：每次 `run()` 走一步就挂起，段数往上走。"""
    auto = MagicMock()
    auto.id = automation_id
    auto.node.return_value = _wait_node()
    ctx = InstanceContext(instance_id=f"inst-{automation_id}", automation_id=automation_id, current_node="w1")
    return Instance(ctx=ctx, automation=auto)


def _executor() -> NodeExecutor:
    return NodeExecutor(instances=MagicMock(), adapters=None, states=MagicMock(), audit=MagicMock())


# ── 问题一 · 警戒档 ────────────────────────────────────────────────────

def test_inside_the_caps_is_silent():
    """CONTROL 档：什么都不改 ⇒ 不告警、不审计、不动状态机。"""
    executor, instance = _executor(), _instance()
    instance.ctx.segments, instance.ctx.steps = MAX_SEGMENTS_PER_INSTANCE, MAX_STEPS_PER_INSTANCE
    assert executor._over_cap(instance) is False
    assert instance.ctx.cap_warned is False
    executor.audit.add.assert_not_called()
    executor.instances.fail.assert_not_called()


def test_crossing_the_cap_warns_once_without_touching_the_state_machine():
    executor, instance = _executor(), _instance()
    instance.ctx.segments = MAX_SEGMENTS_PER_INSTANCE + 1
    assert executor._over_cap(instance) is False, "警戒档不许终止实例（纯等待型自动化合法地转很多段）"
    assert instance.ctx.cap_warned is True
    assert executor.audit.add.call_count == 1
    assert executor.audit.add.call_args.args[0].type == CAP_WARNING
    executor.instances.fail.assert_not_called()


def test_second_segment_past_the_cap_does_not_re_warn():
    """告警只发一次：否则 1000 段会刷出 1000 条 WARNING + 1000 条审计事件，运维读数反而不可用。"""
    executor, instance = _executor(), _instance()
    instance.ctx.cap_warned = True
    instance.ctx.segments = MAX_SEGMENTS_PER_INSTANCE + 500
    assert executor._over_cap(instance) is False
    executor.audit.add.assert_not_called()


def test_steps_cap_warns_too():
    executor, instance = _executor(), _instance()
    instance.ctx.steps = MAX_STEPS_PER_INSTANCE + 1
    assert executor._over_cap(instance) is False
    message = executor.audit.add.call_args.args[0].message
    assert f"步数 {MAX_STEPS_PER_INSTANCE + 1}>{MAX_STEPS_PER_INSTANCE}" in message


@pytest.mark.parametrize("kind", ["segments", "steps"])
def test_hard_cap_fails_the_instance(kind):
    executor, instance = _executor(), _instance()
    if kind == "segments":
        instance.ctx.segments = HARD_CAP_MULTIPLIER * MAX_SEGMENTS_PER_INSTANCE + 1
    else:
        instance.ctx.steps = HARD_CAP_MULTIPLIER * MAX_STEPS_PER_INSTANCE + 1
    assert executor._over_cap(instance) is True
    executor.instances.fail.assert_called_once()
    reason = executor.instances.fail.call_args.args[1]
    assert "硬上限" in reason and "段间循环" in reason


# ── 问题一 · 跨段这件事本身（原缺陷的形状）────────────────────────────

def test_counters_accumulate_across_segments_not_within_one():
    """这一条是原缺陷的反证：`steps` 是 `run()` 的局部量、每段归零，段间封顶必须记在实例上。

    2001 段真跑一遍（每段一步即挂起），复现裁定里那个读数：段数一路涨、告警只在第一次越档发、
    到 2× 才终止。
    """
    executor = _executor()
    instance = _instance(automation_id="auto_cap_loop")
    for _ in range(MAX_SEGMENTS_PER_INSTANCE * 3):
        executor.run(instance)
        if executor.instances.fail.called:
            break
    assert instance.ctx.segments > HARD_CAP_MULTIPLIER * MAX_SEGMENTS_PER_INSTANCE
    # 每段一步 ⇒ 两个累计数同速涨；硬档那一段只加段数不加步数（封顶闸在段入口，先于这一步）
    assert instance.ctx.steps == instance.ctx.segments - 1
    assert executor.instances.fail.called
    # 每段的挂起本身就留审计事件，所以数的是**这一型**：越警戒档只留一条
    cap_events = [c.args[0] for c in executor.audit.add.call_args_list if c.args[0].type == CAP_WARNING]
    assert len(cap_events) == 1, f"告警应当只发一次，实际发了 {len(cap_events)} 条"
    assert instance.ctx.cap_warned is True


def test_supervision_view_carries_a_resident_cap_indicator():
    """裁定要的是"监护视图常驻指示"，不是只打在日志里就完了。"""
    agg = af_watch.WatchAggregator()
    agg.record_cap_warning("auto_cap_watch", 1001, 4000, 3001, at=1.0)
    report = agg.verified_in_prod()
    row = next(a for a in report["automations"] if a["automation_id"] == "auto_cap_watch")
    assert row["cap_warnings"] == 1
    assert report["summary"]["automations_with_cap_warning"] == 1
    # 常驻指示不能伪装成生产失败：这一档没动状态机
    assert row["failed_in_prod"] == 0
    assert row["cap_warnings"] != row["verified_in_prod"]


# ── 问题二 · trace 截断 + trace_dropped ────────────────────────────────

def test_trace_keeps_the_latest_n_and_counts_what_it_dropped():
    instance = _instance()
    for i in range(MAX_TRACE_ENTRIES + 500):
        instance.trace(f"n{i}")
    assert len(instance.ctx.trace) == MAX_TRACE_ENTRIES
    assert instance.ctx.trace_dropped == 500
    assert instance.ctx.trace[-1]["node"] == f"n{MAX_TRACE_ENTRIES + 499}", "留下的必须是最近那 N 条"
    assert instance.ctx.trace[0]["node"] == "n500"


def test_trace_is_a_list_not_a_deque_so_the_oldest_can_still_be_deleted():
    """裁定驳回 A 档（`deque(maxlen)`）的理由之一是那边 `del [0]` 会抛——把实现形状钉住。"""
    instance = _instance()
    instance.trace("x")
    assert isinstance(instance.ctx.trace, list)
    del instance.ctx.trace[0]
    assert instance.ctx.trace == []


def test_trace_counters_survive_the_persist_roundtrip():
    """`trace` 随实例持久化往返，不是纯内存尾巴——丢了 k 条这件事也得跟着活过一次读写。"""
    instance = _instance()
    instance.ctx.segments, instance.ctx.steps, instance.ctx.cap_warned = 1234, 20500, True
    for i in range(MAX_TRACE_ENTRIES + 7):
        instance.trace(f"n{i}")
    data = instance.to_dict()
    restored = restore_context(data)
    assert restored.trace_dropped == 7
    assert restored.segments == 1234 and restored.steps == 20500
    assert restored.cap_warned is True
    assert len(restored.trace) == MAX_TRACE_ENTRIES


def test_restore_context_defaults_for_records_written_before_this_batch():
    """旧盘上的实例没有这四个键 ⇒ 还原必须照常成功（缺省 0/False），不是 KeyError。"""
    legacy = {
        "instance_id": "i-1",
        "automation_id": "a-1",
        "state": "suspended",
        "trace": [{"node": "w1"}],
    }
    ctx = restore_context(legacy)
    assert (ctx.segments, ctx.steps, ctx.trace_dropped, ctx.cap_warned) == (0, 0, 0, False)
