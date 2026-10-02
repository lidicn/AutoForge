"""去抖抑制必须**看得见**，且这条路径以前从没被跑过（P2-4 的续集）。

`af_scheduler._try_fire` 里那行 `AuditEvent(type=INSTANCE_DEBOUNCED...)` 引用了一个
**没 import 进来的名字**——`scripts/check_undefined_names.py` 第一次跑就抓到，
而 `grep -rn debounce tests/` 是 0 命中：这条路径自写下以来从未被执行过。
所以每次真去抖都是 `NameError`，不是"静默不记"。
"""
from __future__ import annotations

from datetime import datetime, timezone

from autoforge.af_audit import INSTANCE_DEBOUNCED
from autoforge.af_draft import draft_intent, get_staged
from autoforge.af_runtime import build_runtime
from autoforge.af_time import VirtualTimeSource


def _runtime_with_debounce(debounce: str = "30s"):
    ref = draft_intent({
        "name": "门铃去抖",
        "when": {"type": "state", "entity": "binary_sensor.doorbell", "to": "on"},
        "do": {"action": "turn_on", "target": "light.hall"},
    })["ref"]
    graph = get_staged(ref)["graph"]
    auto = graph.automations[0]
    entry = auto.entry_nodes()[0]
    # Node 是 frozen dataclass（设计如此：图一旦编译就不该被改）。这里要的不是"改图"，
    # 而是"这张图本来就带去抖"，而 af_draft 的意图 DSL 还没有 debounce 域，所以就地注入。
    object.__setattr__(entry, "debounce", debounce)
    runtime = build_runtime(
        graph, clock=VirtualTimeSource(start=datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc))
    )
    return runtime, auto.id, entry.id


def test_second_trigger_inside_window_is_debounced_and_audited():
    runtime, auto_id, node_id = _runtime_with_debounce()
    sched = runtime.scheduler

    assert sched.trigger(auto_id) is not None
    suppressed = sched.trigger(auto_id)          # 以前：NameError
    assert suppressed is None

    kinds = [(e.type, e.automation_id, e.node_id) for e in runtime.audit]
    assert (INSTANCE_DEBOUNCED, auto_id, node_id) in kinds, f"去抖没有留痕：{kinds}"


def test_debounce_releases_after_the_window():
    runtime, auto_id, _node_id = _runtime_with_debounce(debounce="30s")
    sched = runtime.scheduler
    assert sched.trigger(auto_id) is not None

    runtime.clock.jump(31)                       # 只动墙钟（NTP 校时/夏令时）：不许解去抖
    assert sched.trigger(auto_id) is None, "去抖读的是单调钟，墙钟跳变不该放行"

    runtime.clock.advance(31)                    # 两个时钟同步前进 = 真的过窗
    assert sched.trigger(auto_id) is not None    # 出窗后必须再放行，否则去抖变一次性熔断
