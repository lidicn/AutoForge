# -*- coding: utf-8 -*-
"""AF-770 判据 · 主卧双图「永不派发」——跳变沿被无关事件吃掉（2026-11-13）。

**现场**（AF-PM 2026-11-13 14:20 现读）：

| 自动化 | 累计触发 | last_dispatched_at | last_met |
|---|---|---|---|
| `af_ask_purifier` | 135 | **None** | False |
| `af_ask_humidifier` | 40 | **None** | False |

触发照记、一次都没派发过。

**根因**：`Scheduler.handle_event` 的 else 分支（本次事件**不匹配入口触发**）调用
`_record_met_if_applicable(auto)`；AF-769 把它改成记 `_auto_currently_met(auto)` 的
**真实求值**——**包括 `True`**。于是条件成立期间，家里每秒几十条无关事件（温湿度、
功率、button…）不停把 `last_met` 刷成 `True`；等真正的 `occupancy → on` 到达时
`_dispatch_allowed` 看到 `prev is True` ⇒ 判成「电平维持」`edge_held` ⇒ **永不派发**。

AF-769 只修掉了「硬写 False」，把「写 True」这条路留下了——从「重复发问」翻成「永不发问」。

**修法（卡面 §三 方案 A）**：`_record_met_if_applicable` **只在「断」这一刻落盘**
（`current is False` ⇒ 记 `False`）；`current is True` ⇒ **不落盘**，「立」这一半
留给真正走到派发闸的 `_dispatch_allowed` 自己 `_record_met(True)`。

**牙**（群规⑧）：五条判据必须同时成立——

- W1 干扰事件（场景 A）⛔ 不许把沿提前吃掉 ⇒ 随后真触发**必须**派发（缺陷正向复现）
- W2 无干扰（场景 B，对照腿）⇒ 真触发照常派发（⛔ 防「恒不派发」冒充修复）
- W3 重复触发不重复发问（1 → 0）
- W4 先断后立：灯关记 False → 灯开不动沿 → 真触发仍算跳变（⛔ 防修成「永不记录」）
- W5 源码钉：只写 False 分支
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from autoforge.af_bus import BusEvent
from autoforge.af_ir import Graph, load_automation
from autoforge.af_runtime import build_runtime
from autoforge.af_runtime_facts import RuntimeFacts
from autoforge.af_time import VirtualTimeSource, load_tz

# ── 实体口径：与线上 `af_ask_purifier` 同形（三个条件实体 ＋ 一个入口实体）──────
OCCUPANCY = "binary_sensor.occupancy_main_bedroom"  # 入口触发：人在 on
LIGHT = "switch.light_main_bedroom"                 # 条件②：吸顶灯 on
PURIFIER = "switch.purifier_main_bedroom"           # 条件③：净化器 off
KITCHEN = "switch.kitchen_light"                    # ⛔ 无关实体：不在触发、不在条件里
HUMIDITY = "sensor.humidity_main_bedroom"           # ⛔ 无关实体：数值抖动源

AUTO_ID = "af770_ask_purifier"
ASK_TIMEOUT_S = 90


def _ts(hour: int, minute: int) -> datetime:
    """构造「家庭墙钟 hh:mm」对应的绝对时刻（⛔ 不写死 +8，走 load_tz）。"""
    tz = load_tz()
    return datetime(2026, 11, 13, hour, minute, tzinfo=tz).astimezone(timezone.utc)


#: 与线上 `af_ask_purifier` **逐字同形**：
#: `on(group or 人在) → if(and 夜间 ∧ 灯开 ∧ 净化器关) → ask(超时 90s)`
AUTO_ASK: dict[str, Any] = {
    "ir_version": "0.2.1", "id": AUTO_ID, "name": "AF770 主卧净化器询问同形",
    "version": 1, "mode": "single",
    # 线上 IR 就是 `"cooldown": 0`：冷却兜底不参战，抑制**只**能来自边沿闸。
    "cooldown": 0,
    "nodes": [
        {"id": "t1", "kind": "on", "name": "触发（人在进屋）",
         "trigger": {"type": "group", "op": "or", "sources": [
             {"type": "state", "entity_id": OCCUPANCY, "to": "on"},
         ]}},
        {"id": "i1", "kind": "if", "name": "夜间 ∧ 灯开 ∧ 净化器关才问",
         "expr": {"op": "and", "args": [
             {"op": "truthy", "value": {"fn": "time_between", "args": [
                 {"var": "context.trigger_time"},
                 {"const": "21:00"},
                 {"const": "23:45"},
             ]}},
             {"op": "is_on", "value": {"var": f"entity.{LIGHT}"}},
             {"op": "is_off", "value": {"var": f"entity.{PURIFIER}"}},
         ]}},
        {"id": "q1", "kind": "ask", "name": "语音询问净化器",
         "prompt": "要打开主卧室净化器吗？", "session": "room",
         "room": "zhu_wo_shi", "timeout": f"{ASK_TIMEOUT_S}s"},
        {"id": "d1", "kind": "do", "name": "开净化器", "adapter": "ha",
         "action": "switch.turn_on", "params": {"entity_id": PURIFIER},
         "room": "zhu_wo_shi"},
        {"id": "pyes", "kind": "pass", "name": "播报已开",
         "emit": {"event": "af770_ask_purifier_yes", "data": {"room": "zhu_wo_shi"}}},
        {"id": "pno", "kind": "pass", "name": "播报收到不要",
         "emit": {"event": "af770_ask_purifier_no", "data": {"room": "zhu_wo_shi"}}},
        {"id": "pto", "kind": "pass", "name": "超时兜底",
         "emit": {"event": "af770_ask_purifier_timeout", "data": {"reason": "timeout"}}},
        {"id": "pskip", "kind": "pass", "name": "条件不满足静默结束"},
    ],
    "edges": [
        {"from": "t1", "to": "i1", "kind": "then"},
        {"from": "i1", "to": "q1", "kind": "then"},
        {"from": "i1", "to": "pskip", "kind": "no"},
        {"from": "q1", "to": "d1", "kind": "yes"},
        {"from": "d1", "to": "pyes", "kind": "then"},
        {"from": "q1", "to": "pno", "kind": "no"},
        {"from": "q1", "to": "pto", "kind": "on_timeout"},
        {"from": "q1", "to": "pto", "kind": "default"},
    ],
}


class _Adapter:
    name = "ha"
    dry_run = False

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def call(self, action: str, params: dict):
        self.calls.append((action, dict(params)))
        from autoforge.af_adapters.base import CallResult

        return CallResult(success=True, data={}, error=None)


def _runtime(*, at: datetime, store_root=None, states=None):
    rt = build_runtime(
        Graph([load_automation(AUTO_ASK)]),
        clock=VirtualTimeSource(start=at),
        store_root=store_root,
    )
    rt.adapters.register(_Adapter())
    for k, v in (states or {}).items():
        rt.states.set_state(k, v)
    return rt


def _conditions_hold() -> dict[str, str]:
    """全条件成立的那一组状态（家庭墙钟 22:00 落在 21:00-23:45 窗口内）。"""
    return {OCCUPANCY: "on", LIGHT: "on", PURIFIER: "off"}


def _facts(tmp_path) -> dict[str, Any]:
    return RuntimeFacts(str(tmp_path), AUTO_ID).read()


def _types(rt) -> list[str]:
    return [str(e.type) for e in rt.audit]


def _fire(rt):
    return rt.scheduler.handle_event(BusEvent.of(OCCUPANCY, "on"))


def _noise(rt, n: int = 5) -> None:
    """模拟「家里每秒几十条无关事件」：厨房灯 + 湿度数值抖动，都不匹配入口触发。"""
    for i in range(n):
        rt.states.set_state(KITCHEN, "on" if i % 2 == 0 else "off")
        rt.scheduler.handle_event(BusEvent.of(KITCHEN, "on" if i % 2 == 0 else "off"))
        rt.states.set_state(HUMIDITY, str(50 + i))
        rt.scheduler.handle_event(BusEvent.of(HUMIDITY, str(50 + i)))


# ── W1 场景 A：干扰事件先到 ⇒ ⛔ 不许把沿吃掉，真触发仍要派发 ─────────────
def test_w1_unrelated_events_do_not_eat_the_edge(tmp_path):
    """判据①（缺陷正向复现）：条件成立 → 先来一批无关事件 → 再真触发。

    旧实现：无关事件走 else 分支，`_auto_currently_met` 此刻为 `True` ⇒ 被写进
    `last_met`。真 `occupancy → on` 到达时 `prev is True` ⇒ `edge_held` ⇒ **派发 0 条**
    （现场 135 次触发 / 0 次派发就是这个形状）。
    """
    rt = _runtime(at=_ts(22, 0), store_root=str(tmp_path), states=_conditions_hold())
    _noise(rt, 5)

    # A1：沿还没被吃掉——无关事件期间 `last_met` 必须仍是「无历史」
    assert _facts(tmp_path).get("last_met") is None, (
        "无关事件⛔ 不许把 last_met 刷成 True（那会把跳变沿提前吃掉，真触发到达时只剩电平维持）"
    )

    # A2：真触发必须派发
    fired = _fire(rt)
    assert len(fired) == 1, "条件成立 + 真触发 ⇒ 必须派发 1 条（现场此处 0 条）"
    assert len(rt.instances.active_of(AUTO_ID)) == 1, "派发后应挂着 1 个实例（ask 挂起点）"

    # A3：这一轮⛔ 不能是边沿闸抑制
    assert "instance_edge_held" not in _types(rt), "真触发那一轮⛔ 不该被边沿闸判电平维持"
    f = _facts(tmp_path)
    assert f.get("last_met") is True, "真派发后 last_met 才该是 True"
    assert f.get("last_dispatched_at"), "真派发必须写 last_dispatched_at（现场此处恒 null）"


# ── W2 场景 B（对照腿）：无干扰 ⇒ 照常派发 ─────────────────────────────
def test_w2_without_noise_still_dispatches(tmp_path):
    """判据②对照腿：全新运行时直接 `occupancy → on` ⇒ 派发 1 条。

    ⛔ 没有这条，W1 可以靠「干脆永不派发」蒙成绿（那正是 AF-770 的现场形状）。
    """
    rt = _runtime(at=_ts(22, 0), store_root=str(tmp_path), states=_conditions_hold())
    fired = _fire(rt)
    assert len(fired) == 1, "无干扰 ⇒ 首轮 prev=None 也算跳变沿，必须派发"
    assert _facts(tmp_path).get("last_dispatched_at"), "对照腿：必须真有派发时刻"


# ── W3 重复触发不重复发问（1 → 0）─────────────────────────────────────
def test_w3_repeated_trigger_does_not_ask_twice(tmp_path, monkeypatch):
    """判据③：第一次派发后，ask 超时收尾，再同值重复上报 ⇒ ⛔ 不再问。

    与 W1 合起来才是完整语义：**该派的派（W1）、该抑的抑（W3）**。
    """
    import autoforge.af_runtime_facts as facts_mod

    ticks = {"n": 0}

    class _FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            base = datetime(2026, 11, 13, 14, 0, 0, tzinfo=timezone.utc)
            ticks["n"] += 1
            return base + timedelta(seconds=60 * ticks["n"])

    monkeypatch.setattr(facts_mod, "datetime", _FakeDT)

    rt = _runtime(at=_ts(22, 0), store_root=str(tmp_path), states=_conditions_hold())
    _fire(rt)
    first = _facts(tmp_path).get("last_dispatched_at")
    assert first, "前置：第一次真派发必须写派发时刻"

    rt.advance(ASK_TIMEOUT_S)
    assert len(rt.instances.active_of(AUTO_ID)) == 0, "前置：ask 超时必须收尾（否则 single 模式挡住后续上报）"

    _noise(rt, 3)  # 期间无关事件照常涌入
    before = len(rt.instances.all())
    _fire(rt)

    f = _facts(tmp_path)
    assert f.get("last_dispatched_at") == first, "同值重复上报⛔ 不许重复发问"
    assert len(rt.instances.all()) == before, "同值重复上报⛔ 不许再 spawn 新实例"
    assert "instance_edge_held" in _types(rt), "同值重复上报应被边沿闸判「电平维持」"


# ── W4 先断后立：灯关 → 灯开 → 真触发，跳变沿成立 ────────────────────
def test_w4_break_then_restore_then_trigger_still_fires(tmp_path):
    """判据④（反证腿）：⛔ 不许把修法做成「永不记录」——

    路径：派发一次 → 灯关（条件**真**被打破 ⇒ 记 False）→ 灯开（条件恢复，
    但「立」这一半⛔ 不落盘）→ 真触发 ⇒ 仍算「不满→满足」跳变沿 ⇒ **再派发一次**。

    旧实现（写 True）：灯开那条无关事件就把 `last_met` 刷成 True ⇒ 真触发时 `edge_held`
    ⇒ 第二次永远问不出来。若有人把记录整个删掉（「永不记录」），灯关那一格也记不成
    False ⇒ 本腿同样红。
    """
    rt = _runtime(at=_ts(22, 0), store_root=str(tmp_path), states=_conditions_hold())
    _fire(rt)
    first = _facts(tmp_path).get("last_dispatched_at")
    assert first, "前置：第一次真派发"
    rt.advance(ASK_TIMEOUT_S)
    assert len(rt.instances.active_of(AUTO_ID)) == 0, "前置：ask 超时收尾"

    # ① 断：灯关（条件真被打破）
    rt.states.set_state(LIGHT, "off")
    rt.scheduler.handle_event(BusEvent.of(LIGHT, "off"))
    assert _facts(tmp_path).get("last_met") is False, "条件真被打破必须记 last_met=False（先断后立的基础）"

    # ② 立：灯开（条件恢复）——「立」⛔ 不落盘，停在 False
    rt.states.set_state(LIGHT, "on")
    rt.scheduler.handle_event(BusEvent.of(LIGHT, "on"))
    assert _facts(tmp_path).get("last_met") is False, (
        "条件恢复⛔ 不许把 last_met 刷回 True（那会把这一次的跳变沿提前吃掉）"
    )

    # ③ 真触发：必须再派发一次
    fired = _fire(rt)
    assert len(fired) == 1, "先断后立：真触发必须算跳变沿、再派发一次"
    f = _facts(tmp_path)
    assert f.get("last_dispatched_at") != first, "第二次派发必须刷新派发时刻"
    assert f.get("last_met") is True


# ── W5 源码钉：只写 False 分支 ────────────────────────────────────────
def test_w5_source_pin_only_records_false():
    """判据⑤源码钉（防回退）：`_record_met_if_applicable` ⛔ 不许写 True。

    AF-770 根因就是那一行 `self._record_met(auto.id, current)`——`current` 为 True 时
    把沿提前写掉。钉住「只在 `current is False` 时落盘」，并钉调用点不再传 `met=`。
    """
    import inspect

    from autoforge import af_scheduler

    src = inspect.getsource(af_scheduler.Scheduler._record_met_if_applicable)
    assert "current = self._auto_currently_met(auto)" in src
    assert "if current is False:" in src and "self._record_met(auto.id, False)" in src
    assert "self._record_met(auto.id, current)" not in src, \
        "⛔ 不许再写 True（AF-770 根因：无关节事件把跳变沿提前吃掉）"

    call_src = inspect.getsource(af_scheduler.Scheduler.handle_event)
    assert "self._record_met_if_applicable(auto)" in call_src
    assert "self._record_met_if_applicable(auto, met=" not in call_src
