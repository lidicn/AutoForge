"""AF-747(ii)(iii)（2026-11-09 真机实证）：观察期判失败要记失败数，且「没回滚」的三种原因分开说。

现场背景（v7 `d_on`）：`climate.turn_on` 成功 → canary 5 分钟窗 → 窗末读回 `off`（真实迁移，
不是滞后）⇒ 判漂移 ⇒ 走 `on_error` 直落 `e_noignite`。可终态行是
`actions=1 failed_actions=0`——按失败数巡检的人整类漏掉这条失败；同时证据 detail 里
「没回滚」只写 `auto_rollback=false`，把「restore_call 无映射、fail-closed 跳过」也混成同一句。
"""
from __future__ import annotations

from autoforge import af_watch
from autoforge.af_adapters.mock import MockAdapter
from autoforge.af_audit import ENTITY_DRIFT
from autoforge.af_bus import BusEvent
from autoforge.af_executor import canary_drift_reason
from autoforge.af_ir import load_graph
from autoforge.af_runtime import build_runtime
from autoforge.af_state import InMemoryStateProvider


def _graph(*, auto_rollback: bool):
    return load_graph(
        {
            "ir_version": "0.2.1",
            "id": "af747",
            "name": "AF747",
            "version": 1,
            "mode": "single",
            "confidence": 0.9,  # auto 带：canary 才会生效
            "nodes": [
                {"id": "o", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
                {
                    "id": "d",
                    "kind": "do",
                    "adapter": "mock",  # 返回成功但不改状态 ⇒ 必漂移
                    "action": "light.turn_on",
                    "params": {"entity_id": "light.x"},
                    "canary": {"duration": "5m", "auto_rollback": auto_rollback},
                },
                {"id": "ok", "kind": "pass"},
                {"id": "err", "kind": "pass"},
            ],
            "edges": [
                {"from": "o", "to": "d", "kind": "then"},
                {"from": "d", "to": "ok", "kind": "then"},
                {"from": "d", "to": "err", "kind": "on_error"},
            ],
        }
    )


def _runtime(*, auto_rollback: bool = False):
    states = InMemoryStateProvider()
    states.set_state("binary_sensor.m", "off")
    states.set_state("light.x", "off")
    runtime = build_runtime(_graph(auto_rollback=auto_rollback), states=states)
    return runtime


# ── (iii) 三档口径各说一句话 ─────────────────────────────────────
def test_reason_three_way():
    assert canary_drift_reason([object()], True) == "canary 观察期漂移，已自动回滚"
    assert canary_drift_reason([], False) == "canary 观察期漂移，auto_rollback=false ⇒ 未回滚，留给人判断"
    no_mapping = canary_drift_reason([], True)
    assert "auto_rollback=false" not in no_mapping, "回滚开着却无映射时，不许再写成 auto_rollback=false"
    assert "无可映射" in no_mapping


# ── (ii) 观察期判漂移 ⇒ 走 on_error，且 failed_actions 必须计数 ────
def test_resume_drift_counts_failed_action(monkeypatch):
    feeds: list = []
    monkeypatch.setattr(af_watch, "record_canary", lambda *a, **k: feeds.append((a, k)))

    runtime = _runtime(auto_rollback=False)
    adapter = runtime.adapters.get("mock")
    assert isinstance(adapter, MockAdapter)

    runtime.publish(BusEvent.of("binary_sensor.m", "on"))
    inst = list(runtime.instances.all())[0]
    assert inst.state == "suspended"
    assert inst.ctx.actions == 1, "动作已交出去 ⇒ 计数是事实"
    assert inst.ctx.failed_actions == 0, "挂起期间还没判失败"

    runtime.advance(5 * 60 + 1)

    # 漂移：走失败腿到 err，且 now failed_actions=1（旧实现是 0，与 on_error 自相矛盾）
    assert inst.ctx.current_node in ("err", ""), f"漂移应走 on_error 腿，实得 {inst.ctx.current_node!r}"
    assert inst.ctx.failed_actions == 1, (
        f"观察期判失败必须计入 failed_actions（旧实现=0）；实得 {inst.ctx.failed_actions}"
    )
    assert any(ev.type == ENTITY_DRIFT for ev in runtime.audit)
    assert feeds, "漂移证据一条都没喂"
    _aid, status, _at, detail = feeds[-1][0]
    assert status == "failed"
    assert "auto_rollback=false" in str(detail), f"这档是操作员显式关了回滚，detail 应这么写：{detail!r}"
