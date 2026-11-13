# -*- coding: utf-8 -*-
"""AF-769 判据 · 边沿闸「事件不匹配入口触发」≠「全条件不满」（2026-11-13）· 卡名 `AF卡_af769_edge_gate_met_truth`。

**现场**（2026-11-13 08:59 现读 `/data/*.runtime.json`）：

- `af_ask_purifier`：`last_met=false`、`last_dispatched_at=null`、
  `trigger_days` 11-11=52 / 11-12=66 / 11-13=3
- `af_ask_humidifier`：`last_met=false`、`last_dispatched_at=null`、
  `trigger_days` 11-11=20 / 11-12=13 / 11-13=2

即：触发链是通的（计数天天在涨），但**条件布尔恒 False、真派发一次都没有**，用户被反复发问。

**根因**：`Scheduler._record_met_if_applicable` 先算 `_auto_currently_met(auto)`（真实全条件
布尔），随后**丢弃**它，改记调用方传入的 `met`；而唯一调用点 `handle_event` 的 else 分支
（旧 L150）传的是硬编码 `met=False`。可 else 分支的语义是「**本次事件不匹配入口触发**」——
这与「**全条件不满**」是两件事：

| 事件 | 匹配入口触发？ | 全条件成立？ |
|---|---|---|
| 厨房灯变化（`switch.kitchen_light`） | 否 ⇒ 走 else | **是**（夜间 ∧ 灯开 ∧ 净化器关） |
| 自动化自己 emit 的 pass 事件（`af_ask_*_timeout`） | 否 ⇒ 走 else | **是** |
| 主卧灯被关掉 | 否 ⇒ 走 else | **否**（← 只有这一格才该记 `False`） |

把「不匹配」硬写成 `met=False` 的后果：任何一条无关事件都把 `last_met` 抹成 False ⇒
下一次 occupancy **同值重复上报**（on→on，值都没变）时 `_dispatch_allowed` 看到
`met=True / prev=False` ⇒ 判成「不满→满足」的跳变沿 ⇒ 重复派发、重复发问。

**修法**：`_record_met_if_applicable` 记 `_auto_currently_met(auto)` 的**真实求值**
（`met` 参数整枚撤除，调用点同步改；⛔ 不再让调用方覆盖求值结果）。

**牙**（群规⑧）：五条判据必须**同时**成立，缺一条就退化成假绿——

- L1 真跳变 ⇒ `last_met=True` ＋ 真派发（否则 L3/L4/L5 可以靠"从不记录"蒙过去）；
- L2 `ask` 超时 90s 后 tick ⇒ 活动实例=0（**前置**：single 模式下实例不结束，
  重复上报压根到不了派发闸，L4 就变成假绿）；
- L3 无关事件（厨房灯）⇒ `last_met` 仍 `True`（⛔ 未被污染）；
- L4 同值重复上报 ⇒ ⛔ 不重复发问（`last_dispatched_at` 不变）；
- L5 条件**真被打破**（灯关）⇒ `last_met=False`——⛔ 不许为了 L3/L4 把这条反证腿弄坏
  （那会把"边沿闸"退化成"永不记录 False"，先断后立失效）。
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

AUTO_ID = "af769_ask_purifier"
ASK_TIMEOUT_S = 90


def _ts(hour: int, minute: int) -> datetime:
    """构造「家庭墙钟 hh:mm」对应的绝对时刻（⛔ 不写死 +8，走 load_tz）。"""
    tz = load_tz()
    return datetime(2026, 11, 13, hour, minute, tzinfo=tz).astimezone(timezone.utc)


#: 与线上 `af_ask_purifier` **逐字同形**：
#: `on(group or 人在) → if(and 夜间 ∧ 灯开 ∧ 净化器关) → ask(超时 90s)`
#: ——`--no-->` 静默结束、`--yes-->` 开净化器、`--on_timeout-->` 播报超时（**自动化自己 emit**）。
AUTO_ASK: dict[str, Any] = {
    "ir_version": "0.2.1", "id": AUTO_ID, "name": "AF769 主卧净化器询问同形",
    "version": 1, "mode": "single",
    # 线上 IR 就是 `"cooldown": 0`（现读 `/data/af_ask_purifier.json`）：冷却兜底不参战，
    # 抑制**只**能来自边沿闸 ⇒ L4 量的正是"边沿闸有没有认出电平维持"这一件事。
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
         "emit": {"event": "af769_ask_purifier_yes", "data": {"room": "zhu_wo_shi"}}},
        {"id": "pno", "kind": "pass", "name": "播报收到不要",
         "emit": {"event": "af769_ask_purifier_no", "data": {"room": "zhu_wo_shi"}}},
        {"id": "pto", "kind": "pass", "name": "超时兜底",
         "emit": {"event": "af769_ask_purifier_timeout", "data": {"reason": "timeout"}}},
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
    """走调度器入口（⛔ 不经过总线去重：本条量的是**派发闸**的边沿语义，不是总线去重）。"""
    return rt.scheduler.handle_event(BusEvent.of(OCCUPANCY, "on"))


# ── L1 真跳变 ⇒ last_met=True 且真派发（五条判据的"起手式"）─────────────

def test_l1_jump_edge_records_met_true_and_dispatches(tmp_path):
    """判据①：条件全成立（首轮 prev=None）⇒ 跳变沿 ⇒ 派发 ＋ 落盘 `last_met=True`。

    ⛔ 没有这条，L3/L4/L5 可以靠"压根不记录"蒙成绿：`last_met` 从不被写时，
    读出来是 `None` 而不是 `True`，L3 会红；但若有人把三处记录一起删掉，只有本条
    能证明"该记的时候真记了 True、该派发的时候真派发了"。
    """
    rt = _runtime(at=_ts(22, 0), store_root=str(tmp_path), states=_conditions_hold())
    fired = _fire(rt)

    assert len(fired) == 1, "条件全成立 ⇒ 必须真派发（首轮 prev=None 也算跳变沿）"
    assert len(rt.instances.active_of(AUTO_ID)) == 1, "派发后应挂着 1 个实例（ask 挂起点）"
    f = _facts(tmp_path)
    assert f.get("last_met") is True, "条件成立必须落盘 last_met=True（生产现场此处恒 False）"
    assert f.get("last_dispatched_at"), "真派发必须写 last_dispatched_at（生产现场此处恒 null）"
    assert "instance_edge_held" not in _types(rt), "跳变沿那一轮⛔ 不该被边沿闸抑制"


# ── L2 前置：ask 超时 90s 后实例必须结束（否则 L4 到不了派发闸）───────────

def test_l2_ask_timeout_ends_instance_after_90s(tmp_path):
    """判据②：`ask` 超时（90s）到点 tick ⇒ 活动实例归零。

    这条本身不是缺陷判据，是 L4 的**前置条件**：`mode=single` 下实例还在跑时
    `_try_fire` 会直接 `_reject("single 模式已有实例在跑")` 返回，**根本不调
    `_dispatch_allowed`**——那样 L4 会变成"永远不派发的假绿"。所以必须先把实例
    推到超时终态，重复上报才真的走到边沿闸前面。
    """
    rt = _runtime(at=_ts(22, 0), store_root=str(tmp_path), states=_conditions_hold())
    _fire(rt)
    assert len(rt.instances.active_of(AUTO_ID)) == 1, "前置：派发后实例挂起等人类应答"

    rt.advance(ASK_TIMEOUT_S)  # ask 的 timeout=90s 到点
    assert len(rt.instances.active_of(AUTO_ID)) == 0, \
        "ask 超时到点必须走 on_timeout 收尾、活动实例归零（否则单条模式挡住后续上报）"
    assert "ask_expired" in _types(rt), "超时到点必须留 ask_expired（会话未经应答即消亡）"


# ── L3 无关事件 ⇒ last_met 保持 True（⛔ 不被污染）────────────────────

def test_l3_unrelated_event_keeps_last_met_true(tmp_path):
    """判据③：厨房灯变化（既不匹配 occupancy 入口触发、也不在条件里）⇒ `last_met` 仍 `True`。

    旧实现：`handle_event` 的 else 分支把「本次事件不匹配入口触发」硬写成 `met=False`
    ⇒ 任何无关事件都把 `last_met` 抹成 False（生产 `trigger_days` 天天涨、`last_met` 恒 false
    就是这个形状）。修复后记的是 `_auto_currently_met` 的**真实求值**：此刻「夜间 ∧ 灯开 ∧
    净化器关」仍然成立 ⇒ 必须保持 True。
    """
    rt = _runtime(at=_ts(22, 0), store_root=str(tmp_path), states=_conditions_hold())
    _fire(rt)
    first_dispatch = _facts(tmp_path).get("last_dispatched_at")
    assert _facts(tmp_path).get("last_met") is True, "前置：派发那一轮 last_met=True"

    rt.states.set_state(KITCHEN, "on")
    _fire_other = rt.scheduler.handle_event(BusEvent.of(KITCHEN, "on"))
    assert _fire_other == [], "厨房灯不在入口触发里 ⇒ 不该派发任何实例"

    f = _facts(tmp_path)
    assert f.get("last_met") is True, \
        "无关事件⛔ 不许把 last_met 抹成 False（「不匹配入口触发」≠「全条件不满」）"
    assert f.get("last_dispatched_at") == first_dispatch, \
        "无关事件⛔ 不许刷新 last_dispatched_at（没派发就不是派发时刻）"


# ── L4 同值重复上报 ⇒ ⛔ 不重复发问（last_dispatched_at 不变）─────────────

def test_l4_repeated_same_value_does_not_ask_again(tmp_path, monkeypatch):
    """判据④（缺陷的**正向复现**）：occupancy on→on 同值重复上报 ⇒ ⛔ 不再发问。

    现场路径逐字照搬：① 条件成立派发（问一次）→ ② ask 超时收尾、`pto` 的 pass 节点
    **自己 emit** 了 `af769_ask_purifier_timeout`（这条事件同样不匹配入口触发，旧实现
    在这里把 last_met 又抹成 False）→ ③ occupancy 同值重复上报（on→on，值都没变）。

    旧实现：② 之后 `prev=False` ⇒ `_dispatch_allowed` 判「不满→满足」跳变沿 ⇒ **再问一次**，
    `last_dispatched_at` 被刷新。修复后：`prev=True` ⇒ `edge_held` ⇒ 抑制。

    假钟每次 `now()` +60s：若实现回退成"又派发了一次"，时间戳必然不同 ⇒ L4 会红
    （同微秒下"刷新/不刷新"不可分，会变成恒绿）。
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

    # ② ask 超时收尾：实例结束，且 `pto` 的 pass 节点自己 emit 了超时事件（污染源之一）
    rt.advance(ASK_TIMEOUT_S)
    assert len(rt.instances.active_of(AUTO_ID)) == 0, "前置：实例必须已收尾（否则走不到派发闸）"
    assert any("af769_ask_purifier_timeout" in str(e.entity_id) for e in rt.bus.emitted), \
        "前置：超时兜底应真的 emit 了事件（自动化自己 emit 的 pass 事件是污染源）"

    # ③ 同值重复上报（on→on）：条件仍成立，但上轮已满足 ⇒ 电平维持 ⇒ ⛔ 不再问
    before = len(rt.instances.all())
    _fire(rt)
    f = _facts(tmp_path)
    assert f.get("last_dispatched_at") == first, \
        "同值重复上报⛔ 不许重复发问（AF-769 缺陷：last_met 被污染 ⇒ 误判跳变沿）"
    assert len(rt.instances.all()) == before, "同值重复上报⛔ 不许再 spawn 新实例"
    assert "instance_edge_held" in _types(rt), "同值重复上报应被边沿闸判「电平维持」抑制"
    assert f.get("last_met") is True, "条件仍成立 ⇒ last_met 保持 True"


# ── L5 条件真被打破 ⇒ last_met=False（⛔ 反证腿，不许塌）────────────────

def test_l5_condition_break_records_met_false(tmp_path):
    """判据⑤（反证腿）：灯被关掉 ⇒ 全条件**真的**不成立 ⇒ `last_met=False`。

    ⛔ 不许为了 L3/L4 把这条弄坏：若有人把记录改成"永远记 True"（或"从不记 False"），
    「先断后立」就失效了——条件回落不再被记下，下一次满足也就不算跳变沿，
    `tests/unit/test_af740_dispatch_gate_20261107.py::test_l1_met_false_does_not_dispatch`
    会跟着红。本腿与 L3 成对：**同一条 else 分支**，只有"当前全条件"这一个变量在动。
    """
    rt = _runtime(at=_ts(22, 0), store_root=str(tmp_path), states=_conditions_hold())
    _fire(rt)
    first = _facts(tmp_path).get("last_dispatched_at")
    assert _facts(tmp_path).get("last_met") is True, "前置：派发那一轮 last_met=True"

    rt.states.set_state(LIGHT, "off")  # 条件② 被打破
    rt.scheduler.handle_event(BusEvent.of(LIGHT, "off"))

    f = _facts(tmp_path)
    assert f.get("last_met") is False, \
        "条件真被打破必须记 last_met=False（否则「不满→满足」的跳变沿永远判不出来）"
    assert f.get("last_dispatched_at") == first, "条件不满⛔ 不许派发（AF-740 判据）"


# ── L6 源码钉：记录的是真实求值，⛔ 不再是调用方传入的 met ────────────────

def test_l6_source_pin_records_current_evaluation():
    """源码钉（防回退）：`_record_met_if_applicable` 记 `_auto_currently_met` 的真实求值。

    钉两件事：① 方法体内是 `current = self._auto_currently_met(auto)` ＋
    `self._record_met(auto.id, current)`；② 调用点⛔ 不再传 `met=`（「不匹配入口触发」
    不许再被当成「全条件不满」硬写进去）。
    """
    import inspect

    from autoforge import af_scheduler

    src = inspect.getsource(af_scheduler.Scheduler._record_met_if_applicable)
    assert "current = self._auto_currently_met(auto)" in src, \
        "必须现求当前全条件布尔（AF-769 根因：算出来又被丢弃）"
    assert "self._record_met(auto.id, current)" in src, "必须记真实求值"
    assert "self._record_met(auto.id, met)" not in src, \
        "⛔ 不许再让调用方传入的 met 覆盖真实求值"

    call_src = inspect.getsource(af_scheduler.Scheduler.handle_event)
    assert "self._record_met_if_applicable(auto)" in call_src, "调用点必须同步改签名"
    assert "self._record_met_if_applicable(auto, met=" not in call_src, \
        "⛔ else 分支不许再硬写 met=（「不匹配入口触发」≠「全条件不满」）"
