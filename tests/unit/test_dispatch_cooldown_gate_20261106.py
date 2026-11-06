"""判据 D10 · 派发冷却期＋边沿触发（2026-11-06）· 卡名 AF卡_dispatch_cooldown。

来源：AF-PM 派单卡 `plans/AF卡_v127_D10D11_派单卡_20261105.md` §一；
真机背景「329 次触发仅 1 次快降温」＝条件持续满足期间每次实体刷新都重派（小爱反复念叨）。

**语义（卡面定稿：边沿触发为主、冷却兜底）**：
- 边沿主路：自动化带 `group and` 条件层时，只在该条件「不满→满足」的跳变沿派发一次；
  条件持续满足不重派（`INSTANCE_EDGE_HELD` 审计、**计数照记**）。
- 跳变沿：不满→满足 ⇒ 派发一次。
- 先断后立：先不满足再重新满足＝新回合 ⇒ 再派发一次。
- 持久化腿：`last_met` 经 `RuntimeFacts.record_met` 落盘（`<archive>.runtime.json`），重启不丢。
- 冷却兜底：命中冷却期（默认 600s、IR 元数据 `cooldown` 可配）⇒ 不派发（`INSTANCE_COOLDOWNED`）。
- **分层铁律**：计数是事实、派发是决策——抑制时计数照记、⛔ 不从计数反推派发。

⚠️ 非 `group and` 自动化（单条 state/event/time/group-or）无「条件维持」语义 ⇒ 不施加边沿/冷却，
  每次事件命中即派发（旧行为不受影响，见判据 D6 反证牙）。
"""

from __future__ import annotations

import json

import pytest

from autoforge.af_audit import INSTANCE_COOLDOWNED, INSTANCE_EDGE_HELD
from autoforge.af_ir import Graph, load_automation
from autoforge.af_runtime import build_runtime
from autoforge.af_runtime_facts import RuntimeFacts
from autoforge.af_scheduler import DEFAULT_COOLDOWN_S, Scheduler

# 五条件全布尔（模拟书房空调：门关＋电脑开＋灯开＋人在＋温度>28.5）
_COND = {
    "type": "group",
    "op": "and",
    "sources": [
        {"type": "state", "entity_id": "binary_sensor.door", "to": "off"},
        {"type": "state", "entity_id": "binary_sensor.pc", "to": "on"},
        {"type": "state", "entity_id": "light.desk", "to": "on"},
        {"type": "state", "entity_id": "binary_sensor.presence", "to": "on"},
        {"type": "state", "entity_id": "sensor.temp", "to": "hot"},
    ],
}

_MET_STATES = {
    "binary_sensor.door": "off",
    "binary_sensor.pc": "on",
    "light.desk": "on",
    "binary_sensor.presence": "on",
    "sensor.temp": "hot",
}


def _ir(with_conditions: bool = True, mode: str = "single", cooldown=None) -> dict:
    """入口 on（group-and 五条件或单条 state）→ pass。"""
    trig = _COND if with_conditions else {
        "type": "state", "entity_id": "binary_sensor.door", "to": "off",
    }
    data = {
        "ir_version": "0.2.1",
        "id": "ac",
        "name": "书房空调",
        "version": 1,
        "mode": mode,
        "nodes": [
            {"id": "a1", "kind": "on", "trigger": trig},
            {"id": "p1", "kind": "pass"},
        ],
        "edges": [{"from": "a1", "to": "p1", "kind": "then"}],
    }
    if cooldown is not None:
        data["cooldown"] = cooldown
    return data


def _rt(data, states=None, **kw):
    runtime = build_runtime(Graph([load_automation(data)]), **kw)
    for k, v in (states or {}).items():
        runtime.states.set_state(k, v)
    return runtime


def _audit_types(runtime) -> list[str]:
    return [e.type for e in runtime.audit.events]


def _trigger_count(runtime) -> int:
    """从运行期事实旁车读 trigger_7d（计数是事实层）。"""
    from autoforge.af_runtime_facts import RuntimeFacts as RF

    snap = RF(runtime.store_root, "ac").snapshot() if runtime.store_root else {}
    return int(snap.get("trigger_7d") or 0)


# ── D1 边沿主路：条件持续满足 ⇒ 只派发一次，后续电平维持被抑制 ─────────────
def test_d1_edge_dispatches_once_while_level_held(tmp_path):
    rt = _rt(_ir(mode="parallel"), states=_MET_STATES, store_root=str(tmp_path))
    # 第一次命中（条件从无→满足）：派发
    fired = rt.emit("binary_sensor.pc", "on")
    assert len(fired) == 1, "跳变沿（不满→满足）必须派发一次"
    # 条件仍全满足，另一个实体刷新再命中：电平维持 ⇒ 不重派
    rt.clock.advance(1)
    fired2 = rt.emit("light.desk", "on")
    assert fired2 == [], "条件持续满足时不得重复派发（边沿主路）"
    assert INSTANCE_EDGE_HELD in _audit_types(rt), "电平维持抑制必须有审计在案"
    # 计数照记：两次命中都记进计数（计数是事实、派发是决策）
    assert _trigger_count(rt) == 2, "抑制也要照记触发计数（分层铁律）"


# ── D2 跳变沿：不满→满足 ⇒ 派发一次 ──────────────────────────────────────
def test_d2_rising_edge_dispatches(tmp_path):
    rt = _rt(_ir(mode="parallel"), states={**_MET_STATES, "sensor.temp": "cool"}, store_root=str(tmp_path))
    # 条件不满（temp=cool）：命中不派发
    fired = rt.emit("binary_sensor.pc", "on")
    assert fired == [], "条件不满时不得派发"
    # 条件补齐（temp→hot）：跳变沿 ⇒ 派发
    rt.clock.advance(1)
    rt.states.set_state("sensor.temp", "hot")
    fired2 = rt.emit("sensor.temp", "hot")
    assert len(fired2) == 1, "条件从不满→满足必须派发一次"


# ── D3 先断后立：条件回落再重新满足＝新回合 ⇒ 再派发一次 ───────────────────
def test_d3_rearm_after_break_dispatches_again(tmp_path):
    # parallel 模式＋cooldown=0：隔离出「边沿先断后立」这一条，不被 single 并发/冷却干扰
    rt = _rt(_ir(mode="parallel", cooldown=0), states=_MET_STATES, store_root=str(tmp_path))
    assert len(rt.emit("binary_sensor.pc", "on")) == 1
    # 条件回落（temp 变 cool）⇒ 满足态打破
    rt.clock.advance(1)
    rt.states.set_state("sensor.temp", "cool")
    rt.emit("sensor.temp", "cool")
    # 重新满足 ⇒ 新回合，再派发一次
    rt.clock.advance(1)
    rt.states.set_state("sensor.temp", "hot")
    fired = rt.emit("sensor.temp", "hot")
    assert len(fired) == 1, "先断后立＝新回合，必须再派发一次"


# ── D4 持久化腿：last_met 落盘、重启后仍可读、不丢 ────────────────────────
def test_d4_last_met_persisted_across_restart(tmp_path):
    rt = _rt(_ir(), states=_MET_STATES, store_root=str(tmp_path))
    rt.emit("binary_sensor.pc", "on")
    snap = RuntimeFacts(str(tmp_path), "ac").snapshot()
    assert snap["last_met"] is True, "派发后 last_met 必须落盘为 True"
    assert snap["last_dispatched_at"] is not None, "派发时刻必须落盘"
    # 模拟重启：新建 RuntimeFacts 读同一旁车 ⇒ last_met 不丢
    snap2 = RuntimeFacts(str(tmp_path), "ac").snapshot()
    assert snap2["last_met"] is True, "重启后 last_met 不丢（持久化腿）"


# ── D5 冷却兜底：冷却期内再命中 ⇒ 不派发（INSTANCE_COOLDOWNED）─────────────
def test_d5_cooldown_suppresses_dispatch(tmp_path):
    # cooldown 配 600 ⇒ 第一次派发后再立新回合，被冷却拦（计数照记、不派发）
    rt = _rt(_ir(mode="parallel", cooldown=600), states=_MET_STATES, store_root=str(tmp_path))
    assert len(rt.emit("binary_sensor.pc", "on")) == 1
    # 条件回落再立，但 cooldown=600 ⇒ 第二次被冷却拦
    rt.clock.advance(1)
    rt.states.set_state("sensor.temp", "cool")
    rt.emit("sensor.temp", "cool")
    rt.clock.advance(1)
    rt.states.set_state("sensor.temp", "hot")
    fired = rt.emit("sensor.temp", "hot")
    assert fired == [], "冷却期内不得派发（兜底腿）"
    assert INSTANCE_COOLDOWNED in _audit_types(rt), "冷却抑制必须有审计在案"


# ── D5b 冷却过期后恢复派发（冷却窗是真闸、不是一次性熔断）───────────────────
def test_d5b_dispatch_resumes_after_cooldown(tmp_path):
    rt = _rt(_ir(mode="parallel", cooldown=10), states=_MET_STATES, store_root=str(tmp_path))
    assert len(rt.emit("binary_sensor.pc", "on")) == 1
    rt.clock.advance(1)
    rt.states.set_state("sensor.temp", "cool")
    rt.emit("sensor.temp", "cool")
    rt.clock.advance(20)  # 越过 10s 冷却窗
    rt.states.set_state("sensor.temp", "hot")
    fired = rt.emit("sensor.temp", "hot")
    assert len(fired) == 1, "冷却过期后新回合必须恢复派发（否则冷却变一次性熔断）"


# ── D6 反证牙：非 group-and 自动化不受边沿/冷却影响（旧行为不变）──────────
def test_d6_non_group_automation_unaffected(tmp_path):
    """直接测调度器决策层（绕开总线节流：同一实体高频事件会被既有节流拦住，那是另一层）。"""
    rt = _rt(_ir(with_conditions=False, mode="parallel", cooldown=600),
             states=_MET_STATES, store_root=str(tmp_path))
    sched = rt.scheduler
    auto = rt.graph.get("ac")
    # 连判两次：非 group-and ⇒ `_dispatch_allowed` 恒放行（不施加边沿/冷却）
    assert sched._dispatch_allowed(auto) == (True, ""), "非 group-and 首次应放行"
    assert sched._dispatch_allowed(auto) == (True, ""), "非 group-and 再次应放行（旧行为不退化）"
    # 反证牙：同一冷却窗内，group-and 版本会被拦 —— 证明这道闸确实只对条件型生效
    rt2 = _rt(_ir(mode="parallel", cooldown=600), states=_MET_STATES, store_root=str(tmp_path / "g"))
    sched2 = rt2.scheduler
    auto2 = rt2.graph.get("ac")
    assert sched2._dispatch_allowed(auto2) == (True, "")
    sched2._cooldown["ac"] = sched2.clock.monotonic()  # 模拟刚派发过
    assert sched2._dispatch_allowed(auto2)[0] is False, "group-and 同窗内必须被拦（有牙）"


# ── D7 变异牙：改冷却时长 ⇒ 行为随变（证判据钳住真实参数）─────────────────
@pytest.mark.parametrize("cooldown_s", [0, 5])
def test_d7_cooldown_duration_changes_behavior(tmp_path, cooldown_s):
    rt = _rt(_ir(mode="parallel", cooldown=cooldown_s), states=_MET_STATES, store_root=str(tmp_path))
    assert len(rt.emit("binary_sensor.pc", "on")) == 1
    rt.clock.advance(1)
    rt.states.set_state("sensor.temp", "cool")
    rt.emit("sensor.temp", "cool")
    rt.clock.advance(1)
    rt.states.set_state("sensor.temp", "hot")
    fired = rt.emit("sensor.temp", "hot")
    if cooldown_s == 0:
        assert len(fired) == 1, "cooldown=0 ⇒ 无冷却，新回合应派发（变异：改冷却时长行为随变）"
    else:
        assert fired == [], "cooldown>0 且未过窗 ⇒ 冷却拦下"


# ── D9 真实 IR 形状：条件写在 `if` 节点里，边沿照样生效 ───────────────────
def test_d9_if_node_condition_also_edged(tmp_path):
    """线上真实形状是 `on(group or 六实体)` → `if(五条件 and)` → `do`（见 v5test）。

    条件不在入口触发上，而在 `if` 节点里——若不求值它，`_auto_currently_met` 会返 None
    ⇒ 边沿/冷却全部落空 ⇒ 用户照样被反复念叨。本判据钉住这条腿。
    """
    ir = {
        "ir_version": "0.2.1", "id": "if_shape", "name": "if 形状", "version": 1,
        "mode": "parallel",
        "nodes": [
            {"id": "a1", "kind": "on", "trigger": {"type": "group", "op": "or", "sources": [
                {"type": "state", "entity_id": "sensor.temp"},
                {"type": "state", "entity_id": "binary_sensor.pc"},
            ]}},
            {"id": "i1", "kind": "if", "expr": {"op": "and", "args": [
                {"op": "is_on", "value": {"var": "entity.binary_sensor.pc"}},
                {"op": "gt", "left": {"var": "entity.sensor.temp", "type": "numeric"},
                 "right": {"const": 28.5}},
            ]}},
            {"id": "p1", "kind": "pass"},
            {"id": "p2", "kind": "pass"},
        ],
        "edges": [
            {"from": "a1", "to": "i1", "kind": "then"},
            {"from": "i1", "to": "p1", "kind": "yes"},
            {"from": "i1", "to": "p2", "kind": "no"},
        ],
        "cooldown": 0,
    }
    rt = _rt(ir, states={"sensor.temp": "29.0", "binary_sensor.pc": "on"},
             store_root=str(tmp_path))
    auto = rt.graph.get("if_shape")
    assert rt.scheduler._auto_currently_met(auto) is True, "if 节点的条件必须能被求值（不是 None）"
    assert len(rt.emit("sensor.temp", "29.1")) == 1, "五条件全真 ⇒ 跳变沿派发一次"
    rt.clock.advance(1)
    assert rt.emit("sensor.temp", "29.2") == [], "条件持续满足 ⇒ 电平维持不重派（真实 IR 形状同样生效）"
    assert INSTANCE_EDGE_HELD in _audit_types(rt)
# ── D8 源钉：默认冷却值＋闸在正确落点（不碰三层闸）───────────────────────
def test_d8_source_pins():
    import inspect

    src = inspect.getsource(Scheduler)
    assert DEFAULT_COOLDOWN_S == 600.0, "默认冷却必须 10 分钟（卡面）"
    assert "_dispatch_allowed" in src and "_auto_currently_met" in src
    assert "INSTANCE_COOLDOWNED" in src and "INSTANCE_EDGE_HELD" in src
    # ⛔ 不碰装载层：调度器不得自己构建 merged IR（装载闸在 `_build_merged_ir`／service 侧）
    assert "_build_merged_ir" not in src, "D10 只加派发闸，⛔ 不越界改装载层（台账 §1.173 分层）"
    assert "all_tags" not in src, "D10 只加派发闸，⛔ 不在调度器里读归档标签"
