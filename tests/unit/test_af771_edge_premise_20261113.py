# -*- coding: utf-8 -*-
"""AF-771 判据 · 边沿条件缺「触发前提」——离开房间不断沿，再进屋不再问（2026-11-13）。

**现场**（AF-PM 2026-11-13 20:27 用户实测，生产现读）：

| 时刻(UTC) | 事件 |
|---|---|
| 12:27:41.857 | occupancy off→on（用户进屋）——触发到达 |
| 12:27:42.268 | `last_met` 被写成 `True`（派发闸决策点） |
| 12:27:42.703 | 第 137 次触发记账 |
| — | ⛔ `last_dispatched_at` 仍停在 10:49:39，**没有派发** |

**根因**：`_auto_currently_met` 对「group 入口 + `if` 节点」形状**只求值 `if`**
（夜间 ∧ 灯开 ∧ 净化器关），**没有把入口触发自身的前提**（`occupancy to=on` ＝ 人在主卧）
算进去。吸顶灯从 18:49:34 起连开 2 小时，「人在」这一半进了房间就不再掉 ⇒ met 恒 True
⇒ 再进屋只剩「电平维持」`edge_held` ⇒ **第二次进屋永远不问**。

与 AF-770 的关系（⛔ 别混）：AF-770 修的是「**沿被无关事件提前吃掉**」（条件成立但真触发
还没到，met 被刷成 True）；本卡修的是「**沿根本没有断过**」（触发前提从不参与求值）。
两者症状同为「不派发」，成因相反，判据必须各自独立成立。

**牙**（群规⑧）：四条主判据＋两枚附加腿＋七枚既有钉子必须同时成立——

- N1 进屋 ⇒ 派发（前提参与后不该把正常派发弄坏）
- N2 离开 ⇒ `last_met` 断为 `False`（前提掉了要**落盘**）
- N3 **再进屋 ⇒ 再问**（主判据；修前 0 条，这条红＝卡没修）
- N4 on→on 同值重复上报 ⇒ 不重复问（`edge_held`，防 AF-750 族刷屏）
- N5 六枚附加腿：「不参与」口径（event/变化型 source ⛔ 不许当 False 永久压制）、
  多源 `or` 的「任一成立」
- N6 七枚既有钉子文件不回退（af770/af769/af740/af739/af766/cooldown/runtime_facts）
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from autoforge.af_bus import BusEvent
from autoforge.af_ir import Graph, load_automation
from autoforge.af_runtime import build_runtime
from autoforge.af_runtime_facts import RuntimeFacts
from autoforge.af_time import VirtualTimeSource, load_tz

# ── 实体口径：与线上 `af_ask_purifier` 同形（一个入口实体 ＋ 三个条件实体）──────
OCCUPANCY = "binary_sensor.occupancy_main_bedroom"  # 入口触发前提：人在 on
LIGHT = "switch.light_main_bedroom"                 # if 条件②：吸顶灯 on
PURIFIER = "switch.purifier_main_bedroom"           # if 条件③：净化器 off
KITCHEN = "switch.kitchen_light"                    # ⛔ 无关实体
HUMIDITY = "sensor.humidity_main_bedroom"           # ⛔ 无关实体：数值抖动源
DOOR = "binary_sensor.door_main_bedroom"            # 多源 or 附加腿用

AUTO_ID = "af771_ask_purifier"
ASK_TIMEOUT_S = 90


def _ts(hour: int, minute: int) -> datetime:
    """构造「家庭墙钟 hh:mm」对应的绝对时刻（⛔ 不写死 +8，走 load_tz）。"""
    tz = load_tz()
    return datetime(2026, 11, 13, hour, minute, tzinfo=tz).astimezone(timezone.utc)


def _auto(sources: list[dict[str, Any]], *, with_if: bool = True) -> dict[str, Any]:
    """与线上 `af_ask_purifier` 逐字同形的骨架：`on(group or …) → if(条件) → ask`。

    `sources` 可换，用来造「不参与」「多源 or」这类附加腿的形状。
    """
    auto: dict[str, Any] = {
        "ir_version": "0.2.1", "id": AUTO_ID, "name": "AF771 主卧净化器询问同形",
        "version": 1, "mode": "single",
        # 线上 IR 就是 `"cooldown": 0`：冷却兜底不参战，抑制**只**能来自边沿闸。
        "cooldown": 0,
        "nodes": [
            {"id": "t1", "kind": "on", "name": "触发（人在进屋）",
             "trigger": {"type": "group", "op": "or", "sources": sources}},
            {"id": "q1", "kind": "ask", "name": "语音询问净化器",
             "prompt": "要打开主卧室净化器吗？", "session": "room",
             "room": "zhu_wo_shi", "timeout": f"{ASK_TIMEOUT_S}s"},
            {"id": "d1", "kind": "do", "name": "开净化器", "adapter": "ha",
             "action": "switch.turn_on", "params": {"entity_id": PURIFIER},
             "room": "zhu_wo_shi"},
            {"id": "pyes", "kind": "pass", "name": "播报已开",
             "emit": {"event": "af771_ask_purifier_yes", "data": {"room": "zhu_wo_shi"}}},
            {"id": "pno", "kind": "pass", "name": "播报收到不要",
             "emit": {"event": "af771_ask_purifier_no", "data": {"room": "zhu_wo_shi"}}},
            {"id": "pto", "kind": "pass", "name": "超时兜底",
             "emit": {"event": "af771_ask_purifier_timeout", "data": {"reason": "timeout"}}},
            {"id": "pskip", "kind": "pass", "name": "条件不满足静默结束"},
        ],
        "edges": [
            {"from": "t1", "to": "q1", "kind": "then"},
            {"from": "q1", "to": "d1", "kind": "yes"},
            {"from": "d1", "to": "pyes", "kind": "then"},
            {"from": "q1", "to": "pno", "kind": "no"},
            {"from": "q1", "to": "pto", "kind": "on_timeout"},
            {"from": "q1", "to": "pto", "kind": "default"},
        ],
    }
    if with_if:
        auto["nodes"].append({
            "id": "i1", "kind": "if", "name": "夜间 ∧ 灯开 ∧ 净化器关才问",
            "expr": {"op": "and", "args": [
                {"op": "truthy", "value": {"fn": "time_between", "args": [
                    {"var": "context.trigger_time"},
                    {"const": "21:00"},
                    {"const": "23:45"},
                ]}},
                {"op": "is_on", "value": {"var": f"entity.{LIGHT}"}},
                {"op": "is_off", "value": {"var": f"entity.{PURIFIER}"}},
            ]},
        })
        auto["edges"] += [
            {"from": "t1", "to": "i1", "kind": "then"},
            {"from": "i1", "to": "q1", "kind": "then"},
            {"from": "i1", "to": "pskip", "kind": "no"},
        ]
    return auto


#: 线上形状：入口 or 只有一个 state source（带 `to`）
SOURCES_MAIN = [{"type": "state", "entity_id": OCCUPANCY, "to": "on"}]


class _Adapter:
    name = "ha"
    dry_run = False

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def call(self, action: str, params: dict):
        self.calls.append((action, dict(params)))
        from autoforge.af_adapters.base import CallResult

        return CallResult(success=True, data={}, error=None)


def _runtime(*, at: datetime, store_root=None, states=None, sources=None, with_if=True):
    rt = build_runtime(
        Graph([load_automation(_auto(sources or SOURCES_MAIN, with_if=with_if))]),
        clock=VirtualTimeSource(start=at),
        store_root=store_root,
    )
    rt.adapters.register(_Adapter())
    for k, v in (states or {}).items():
        rt.states.set_state(k, v)
    return rt


def _conditions_hold() -> dict[str, str]:
    """全条件成立（含**入口前提**「人在」）：家庭墙钟 22:00 落在 21:00-23:45 窗口内。"""
    return {OCCUPANCY: "on", LIGHT: "on", PURIFIER: "off"}


def _facts(tmp_path) -> dict[str, Any]:
    return RuntimeFacts(str(tmp_path), AUTO_ID).read()


def _types(rt) -> list[str]:
    return [str(e.type) for e in rt.audit]


def _enter(rt):
    """进屋：先落状态再投事件（HA 的真实顺序：状态先变，事件后到）。"""
    rt.states.set_state(OCCUPANCY, "on")
    return rt.scheduler.handle_event(BusEvent.of(OCCUPANCY, "on"))


def _leave(rt):
    rt.states.set_state(OCCUPANCY, "off")
    return rt.scheduler.handle_event(BusEvent.of(OCCUPANCY, "off"))


def _noise(rt, n: int = 3) -> None:
    """模拟「家里每秒几十条无关事件」：厨房灯 + 湿度数值抖动，都不匹配入口触发。"""
    for i in range(n):
        rt.states.set_state(KITCHEN, "on" if i % 2 == 0 else "off")
        rt.scheduler.handle_event(BusEvent.of(KITCHEN, "on" if i % 2 == 0 else "off"))
        rt.states.set_state(HUMIDITY, str(50 + i))
        rt.scheduler.handle_event(BusEvent.of(HUMIDITY, str(50 + i)))


def _tick_clock(monkeypatch) -> None:
    """把 `af_runtime_facts` 的 `datetime.now` 换成逐次前进的假钟。

    ⛔ 不这么做的话，两次派发可能落在同一微秒，`last_dispatched_at` 相等就分不清
    「真的又派发了一次」和「时间戳没刷新」——判据会假绿。
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


# ── N1 进屋 ⇒ 派发 ────────────────────────────────────────────────────
def test_n1_enter_dispatches(tmp_path):
    """判据 N1：人在 + 灯开 + 净化器关 + 在夜间窗口 ⇒ 进屋**必须派发**。

    ⛔ 这条是「前提参与后别把正常派发弄坏」的护栏：若有人把前提写成恒 False
    （例如把所有 source 都当 `from` 型保守 False），本腿立刻红。
    """
    rt = _runtime(at=_ts(22, 0), store_root=str(tmp_path), states={LIGHT: "on", PURIFIER: "off"})
    fired = _enter(rt)
    assert len(fired) == 1, "进屋 + 全条件成立 ⇒ 必须派发 1 条"
    assert len(rt.instances.active_of(AUTO_ID)) == 1, "派发后应挂着 1 个实例（ask 挂起点）"
    assert "instance_edge_held" not in _types(rt), "首次进屋⛔ 不该被边沿闸判电平维持"
    f = _facts(tmp_path)
    assert f.get("last_met") is True, "真派发后 last_met 才该是 True"
    assert f.get("last_dispatched_at"), "真派发必须写 last_dispatched_at"


# ── N2 离开 ⇒ last_met 断为 False ─────────────────────────────────────
def test_n2_leave_breaks_the_edge(tmp_path):
    """判据 N2：离开房间（`occupancy → off`）⇒ `last_met` 必须落盘 `False`。

    修前：入口前提不参与求值，`occupancy → off` 走 else 分支时算出的 met 仍是
    「夜间 ∧ 灯开 ∧ 净化器关」＝ True ⇒ 只在 `current is False` 时落盘的 AF-770 修法
    **一个字都不写** ⇒ `last_met` 停在 True ⇒ 沿根本没断过（本卡根因）。
    """
    rt = _runtime(at=_ts(22, 0), store_root=str(tmp_path), states=_conditions_hold())
    _enter(rt)
    assert _facts(tmp_path).get("last_met") is True, "前置：进屋后 last_met=True"
    rt.advance(ASK_TIMEOUT_S)
    assert len(rt.instances.active_of(AUTO_ID)) == 0, "前置：ask 超时收尾"

    _leave(rt)
    assert _facts(tmp_path).get("last_met") is False, (
        "离开房间＝触发前提掉了 ⇒ 必须落盘 last_met=False（否则下一次进屋只剩电平维持）"
    )


# ── N3 再进屋 ⇒ 再问（主判据）────────────────────────────────────────
def test_n3_reenter_asks_again(tmp_path, monkeypatch):
    """判据 N3（本卡主判据，修前 **0** 条）：进屋→问→离开→**再进屋→再问**。

    现场：18:49:34 灯开之后灯一直开着，用户 20:27 进屋时 `last_met` 已于 12:27:42.268
    被写成 True ⇒ `edge_held` ⇒ `last_dispatched_at` 停在 10:49:39、一问不发。
    """
    _tick_clock(monkeypatch)
    rt = _runtime(at=_ts(22, 0), store_root=str(tmp_path), states=_conditions_hold())

    _enter(rt)
    first = _facts(tmp_path).get("last_dispatched_at")
    assert first, "前置：第一次进屋必须派发"
    rt.advance(ASK_TIMEOUT_S)

    # 屋里待着：灯一直开着，期间照常有无关事件涌入（现场 2 小时就是这个状态）
    _noise(rt, 4)
    _leave(rt)
    rt.advance(ASK_TIMEOUT_S)

    # 再进屋
    fired = _enter(rt)
    assert len(fired) == 1, (
        "再进屋必须再派发 1 条（修前 0 条：last_met 恒 True ⇒ edge_held）"
    )
    f = _facts(tmp_path)
    assert f.get("last_dispatched_at") != first, "第二次派发必须刷新派发时刻"
    assert f.get("last_met") is True


# ── N4 on→on 同值重复上报 ⇒ 不重复问 ─────────────────────────────────
def test_n4_same_value_repeat_does_not_ask_again(tmp_path):
    """判据 N4：用户**在屋里反复走动**（`occupancy on→on` 同值上报）⇒ ⛔ 不重复发问。

    前提参与求值后，同值重复上报时 met=True 且 prev=True ⇒ 仍是电平维持 `edge_held`。
    ⛔ 这条防的是「一修就把沿闸关掉」——AF-750 族反复念叨的形状。
    """
    rt = _runtime(at=_ts(22, 0), store_root=str(tmp_path), states=_conditions_hold())
    _enter(rt)
    first = _facts(tmp_path).get("last_dispatched_at")
    assert first, "前置：第一次进屋必须派发"
    rt.advance(ASK_TIMEOUT_S)
    assert len(rt.instances.active_of(AUTO_ID)) == 0, "前置：ask 超时收尾"

    before = len(rt.instances.all())
    fired = _enter(rt)  # on → on，值没变
    assert len(fired) == 0, "同值重复上报⛔ 不许再派发"
    assert len(rt.instances.all()) == before, "同值重复上报⛔ 不许再 spawn 新实例"
    assert _facts(tmp_path).get("last_dispatched_at") == first, "派发时刻⛔ 不许被电平维持刷新"
    assert "instance_edge_held" in _types(rt), "应被边沿闸判「电平维持」"


# ── N5a 「不参与」口径：event/变化型 source ⛔ 不许当 False 永久压制 ──────
def test_n5a_non_state_source_does_not_participate(tmp_path):
    """附加腿（防「复用 `_group_currently_met` 造成永久压制」）：

    入口 `group or [state 不带 to/from]`——这种 source 是**「实体变化」型触发**，
    没有可比对的目标态，**不参与**前提（卡面 §三 细则 1c）。若照抄
    `_group_currently_met` 的 `return trig.to is None or current == trig.to`，在别的分支
    上会把 event/time/sun 一律塌成 `False` ⇒ met 恒 False ⇒ **永不派发**
    （那正是本卡要消灭的形状，只是换了个成因）。

    ⇒ 本腿要求：这类入口**退回旧行为**（只看 if），首轮派发、同值重复仍 `edge_held`。
    """
    rt = _runtime(
        at=_ts(22, 0), store_root=str(tmp_path),
        states={LIGHT: "on", PURIFIER: "off"},
        sources=[{"type": "state", "entity_id": OCCUPANCY}],  # 无 to/from ＝ 变化型
    )
    rt.states.set_state(OCCUPANCY, "on")
    fired = rt.scheduler.handle_event(BusEvent.of(OCCUPANCY, "on"))
    assert len(fired) == 1, "前提「不参与」⇒ 退回只看 if ⇒ 首轮照常派发（⛔ 不许恒 False 压制）"
    first = _facts(tmp_path).get("last_dispatched_at")
    assert first
    rt.advance(ASK_TIMEOUT_S)

    # 同值重复：met=True 且 prev=True ⇒ 电平维持（行为与修前一致）
    rt.states.set_state(OCCUPANCY, "on")
    again = rt.scheduler.handle_event(BusEvent.of(OCCUPANCY, "on"))
    assert len(again) == 0, "变化型入口同值重复仍应被边沿闸判电平维持"
    assert _facts(tmp_path).get("last_dispatched_at") == first


# ── N5b 多源 or：任一成立即算活跃 ─────────────────────────────────────
def test_n5b_multi_source_or_any_active(tmp_path):
    """附加腿（卡面 §三 细则 2）：`op=or` 入口 ⛔ 不要求全部 source 成立。

    `group or [occupancy to=on, door to=on]`：人在不在看两个信号，任一为 on 即「有人/有动静」。
    ① 只有 door=on ⇒ 前提成立 ⇒ if 成立则派发；
    ② 两个都 off ⇒ 前提不成立 ⇒ `last_met` 断 False。
    """
    rt = _runtime(
        at=_ts(22, 0), store_root=str(tmp_path),
        states={OCCUPANCY: "off", DOOR: "on", LIGHT: "on", PURIFIER: "off"},
        sources=[
            {"type": "state", "entity_id": OCCUPANCY, "to": "on"},
            {"type": "state", "entity_id": DOOR, "to": "on"},
        ],
    )
    fired = rt.scheduler.handle_event(BusEvent.of(DOOR, "on"))
    assert len(fired) == 1, "or 入口：door=on 一条成立即算活跃 ⇒ 必须派发（⛔ 不要求 occupancy 也成立）"
    rt.advance(ASK_TIMEOUT_S)

    # 两个信号都掉 ⇒ 前提不成立 ⇒ 断
    rt.states.set_state(DOOR, "off")
    rt.scheduler.handle_event(BusEvent.of(DOOR, "off"))
    assert _facts(tmp_path).get("last_met") is False, "or 入口全部 source 前提不成立 ⇒ 必须断 False"


# ── N5c 源码钉：前提必须参与 if 求值 ──────────────────────────────────
def test_n5c_source_pin_premise_is_wired(tmp_path):
    """源码钉（防回退）：`has_group_entry` 段必须**先用前提**再求 if。

    ⛔ 只钉形状（调用存在且顺序在 `evaluate(...)` 之前），不钉具体行号——行号由
    `docs/可观测性清单.md` 与插桩表管，这里钉的是「前提被接回派发判定」这一事实。
    """
    import inspect

    from autoforge import af_scheduler

    src = inspect.getsource(af_scheduler.Scheduler._auto_currently_met)
    assert "_entry_premise_met" in src, "⛔ 入口前提必须参与 `_auto_currently_met` 求值（AF-771 根因）"
    assert src.index("_entry_premise_met") < src.index("evaluate(node.expr"), (
        "前提求值必须排在 if 求值之前（短路在 False 前提上）"
    )
    assert "if premise is False:" in src, "前提不成立时必须直接判 met=False"

    helper = inspect.getsource(af_scheduler.Scheduler._premise_met)
    assert "check_trigger_depth" in helper, "递归必须有深度预算（F6 口径）"
    assert "return None" in helper, "必须保留「不参与」第三态（⛔ 不许把求不出的项塌成 False）"
