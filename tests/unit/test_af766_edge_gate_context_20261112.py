# -*- coding: utf-8 -*-
"""AF-766 判据 · 边沿闸传空 `context` ⇒ 引用 `context.trigger_time` 的自动化**恒定不派发**（2026-11-12）。

**现场**（生产现读，2026-11-12 21:2x +08）：主卧两条主动询问
（`af_ask_purifier`／`af_ask_humidifier`）`trigger_days` 累计 84/24 次、`last_met` 恒 `false`、
`last_dispatched_at` 恒 `null`——触发链是通的（状态上报确实到了、条件每轮都在算），
**卡在条件求值恒 False**。

**根因**：`Scheduler._auto_currently_met` 的「`if` 节点条件层」分支（AF-740 第二段，
`af_scheduler.py:552-570`）调用 `make_resolver(snap, {}, {})`——**第三个参数是空字典**。
真实 IR 的 `if` 首项是 `truthy(time_between(context.trigger_time, "21:00", "23:45"))`；
`make_resolver` 查不到 `context.trigger_time` 时抛 `KeyError`，被下面一行
`except Exception: return False`（fail-closed）吞成 `False` ⇒ `_dispatch_allowed` 判
`edge_held` ⇒ **恒定不派发**。这个 `except` 兜底本身是对的（求值失败必须保守），
错的是**上游没给 `context` 赋值**。

⛔ 与 AF-757（时区错位）**不是同一条**：AF-757 修的是「带时区的时间戳归一到哪个时区」，
本条修的是「时间戳根本没进求值器」。757 修好后本条仍会红——判据 X1 用**已归一的
家庭时区时刻**做正例，正是要证明这一层。

**牙**（群规⑧）：X1 正例（窗口内 ⇒ True）与 X2 反例（窗口外 ⇒ False）必须**同时成立**，
否则单腿会退化成「恒 True」或「恒 False」的假绿；X3 钉住 `context` 真的是**当前时刻**
（推进时钟到窗口外再求值必须翻转，⛔ 不许写死一个常量）；X4 钉 `and` 另两腿没被短路；
X7 是源码钉，防有人把 `context` 又改回空字典。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from autoforge.af_bus import BusEvent
from autoforge.af_ir import Graph, load_automation
from autoforge.af_runtime import build_runtime
from autoforge.af_runtime_facts import RuntimeFacts
from autoforge.af_time import VirtualTimeSource, load_tz

LIGHT = "switch.light_main"
PURIFIER = "switch.purifier_main"
AUTO_ID = "af766"


def _ts(hour: int, minute: int) -> datetime:
    """构造「家庭墙钟 hh:mm」对应的绝对时刻（⛔ 不写死 +8，走 load_tz）。"""
    tz = load_tz()
    return datetime(2026, 11, 12, hour, minute, tzinfo=tz).astimezone(timezone.utc)


#: 与 `af_ask_purifier` 线上 IR **逐字同形**（`truthy` 包 `fn: time_between` ＋ `const` 实参）：
#: `on(group or 两实体) → if(and 夜间 ∧ 灯开 ∧ 净化器关)`——三项任一为假即不派发。
AUTO_MAIN_BEDROOM: dict[str, Any] = {
    "ir_version": "0.2.1", "id": AUTO_ID, "name": "AF766 主卧同形", "version": 1,
    "mode": "single",
    "nodes": [
        {"id": "t1", "kind": "on", "name": "触发（人在进屋）",
         "trigger": {"type": "group", "op": "or", "sources": [
             {"type": "state", "entity_id": LIGHT, "to": "on"},
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
        {"id": "d1", "kind": "do", "name": "开净化器", "adapter": "ha",
         "action": "switch.turn_on", "params": {"entity_id": PURIFIER},
         "room": "zhu_wo_shi"},
        {"id": "p1", "kind": "pass", "name": "条件不满足静默结束"},
    ],
    "edges": [
        {"from": "t1", "to": "i1", "kind": "then"},
        {"from": "i1", "to": "d1", "kind": "then"},
        {"from": "i1", "to": "p1", "kind": "no"},
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


def _runtime(auto, *, at: datetime, store_root=None, states=None):
    rt = build_runtime(
        Graph([load_automation(auto)]),
        clock=VirtualTimeSource(start=at),
        store_root=store_root,
    )
    rt.adapters.register(_Adapter())
    for k, v in (states or {}).items():
        rt.states.set_state(k, v)
    return rt


def _met(rt) -> bool | None:
    return rt.scheduler._auto_currently_met(rt.graph.get(AUTO_ID))


# ── X1/X2 差分自证：同一 IR、同一状态，只有钟点不同，`met` 必须翻转 ────────

def test_x1_in_window_met_true(tmp_path):
    """窗口内（本地 22:00）＋ 灯开 ＋ 净化器关 ⇒ `met=True`。

    旧实现：`make_resolver(snap, {}, {})` 空 context ⇒ `context.trigger_time` 抛 `KeyError`
    ⇒ 被 `except Exception` 吞成 `False` ⇒ 该腿恒 False（生产 84 次触发零派发的直接原因）。
    """
    rt = _runtime(AUTO_MAIN_BEDROOM, at=_ts(22, 0),
                  states={LIGHT: "on", PURIFIER: "off"})
    assert _met(rt) is True, "窗口内且另两条件成立 ⇒ 条件层必须判 True（AF-766 根因）"


def test_x2_out_of_window_met_false(tmp_path):
    """反证腿：本地 14:00（窗口外）⇒ `met=False`。

    ⛔ 没有这条，X1 可以靠「永远返回 True」蒙过去；两条一起才证明 `context.trigger_time`
    真的进了求值器、且 `time_between` 真的在算。
    """
    rt = _runtime(AUTO_MAIN_BEDROOM, at=_ts(14, 0),
                  states={LIGHT: "on", PURIFIER: "off"})
    assert _met(rt) is False, "窗口外 ⇒ 必须判 False（否则 time_between 没真算）"


def test_x3_context_is_current_moment_not_constant(tmp_path):
    """同一实例推进时钟 8 小时（22:00 → 次日 06:00）⇒ `met` 必须从 True 翻到 False。

    钉住 `context` 取的是 `clock.now()` 而**不是**写死的常量：旧实现传空字典时这条会
    「两次都 False」（求值抛异常），若有人改成写死 `"21:00"` 之类的字面量，这条也会红。
    """
    rt = _runtime(AUTO_MAIN_BEDROOM, at=_ts(22, 0),
                  states={LIGHT: "on", PURIFIER: "off"})
    assert _met(rt) is True
    rt.clock.advance(8 * 3600)  # 本地 06:00，窗口外
    assert _met(rt) is False, "时钟推进到窗口外 ⇒ met 必须翻转（context 取当前时刻）"


def test_x4_other_conditions_still_gate(tmp_path):
    """窗口内但另两条件不成立（净化器 on）⇒ `met=False`。

    ⛔ 防「只修时区腿、把 and 的另两腿短路掉」：`and` 三项任一为假都必须 False。
    """
    rt = _runtime(AUTO_MAIN_BEDROOM, at=_ts(22, 0),
                  states={LIGHT: "on", PURIFIER: "on"})
    assert _met(rt) is False, "净化器已开 ⇒ is_off 假 ⇒ and 整体必须 False"


# ── X5/X6 端到端：真派发（不只是 `met` 布尔）────────────────────────────

def test_x5_dispatches_end_to_end(tmp_path):
    """窗口内条件成立 ⇒ 事件进来必须**真派发**（写 `last_dispatched_at` ＋ 真下发动作）。

    这是生产现象的直接反证：现场 `last_dispatched_at` 恒 `null`，本腿必须非空。
    """
    rt = _runtime(AUTO_MAIN_BEDROOM, at=_ts(22, 0), store_root=str(tmp_path),
                  states={LIGHT: "on", PURIFIER: "off"})
    rt.scheduler.handle_event(BusEvent.of(LIGHT, "on"))
    f = RuntimeFacts(str(tmp_path), AUTO_ID).read()
    assert f.get("last_dispatched_at"), "条件成立必须真派发（生产现场此处恒 null）"
    assert f.get("last_met") is True, "条件布尔落盘必须是 True"
    assert "af_action_called" in [str(e.type) for e in rt.audit], "真派发应真下发动作"


def test_x6_out_of_window_does_not_dispatch(tmp_path):
    """反证腿：窗口外 ⇒ ⛔ 不派发（`last_dispatched_at` 为空、留 `edge_held`）。"""
    rt = _runtime(AUTO_MAIN_BEDROOM, at=_ts(14, 0), store_root=str(tmp_path),
                  states={LIGHT: "on", PURIFIER: "off"})
    rt.scheduler.handle_event(BusEvent.of(LIGHT, "on"))
    f = RuntimeFacts(str(tmp_path), AUTO_ID).read()
    assert f.get("last_dispatched_at") is None, "窗口外 ⛔ 不许写派发时刻"
    assert "instance_edge_held" in [str(e.type) for e in rt.audit], "窗口外应被边沿闸抑制"


# ── X7 源码钉：`context` 不许改回空字典 ────────────────────────────

def test_x7_source_pin_context_not_empty():
    import inspect

    from autoforge import af_scheduler as mod

    src = inspect.getsource(mod.Scheduler._auto_currently_met)
    assert "trigger_time" in src, \
        "_auto_currently_met 必须给 context 传 trigger_time（AF-766 根因：空字典恒不派发）"
    assert "make_resolver(snap, {}, {})" not in src, \
        "⛔ 不许再传空 context（KeyError 被 fail-closed 吞成恒 False）"
    # 收口钉（AF-Core 复核 B2）：⛔ 不许再手写 `context` 字典——只补一枚键的写法
    # 会让 `context.trigger_entity_id` 等四枚依旧缺键，同一个坑原地复现。
    assert "make_eval_context(" in src, \
        "⛔ 边沿闸必须走 make_eval_context 唯一入口（手写字典 ⇒ 六枚键必然漂移）"


# ── X8/X9 契约缺口（AF-Core 复核 B2，2026-11-12 收口）──────────────
# AF-766 第一版只补了 `trigger_time` 一枚键，与 `af_instance.spawn` 的六键不同形。
# `context.trigger_entity_id` / `trigger_kind` / `trigger_subject` / `instance_id`
# 是设备健康与在场自动化的**标准写法**，缺键 ⇒ `make_resolver` 抛「未知系统变量」
# ⇒ 被边沿闸的 `except Exception` 吞成 False ⇒ 与 AF-766 根因**同一个坑**。
# 这两枚钉住「一份固定词汇表只有一个构造入口」，防下一次只补一枚又漂移。

def test_x8_eval_context_has_all_six_keys():
    """边沿闸构造的 context 必须与实例侧**同形**（六枚键一枚不缺）。"""
    from autoforge.af_instance import make_eval_context

    ctx = make_eval_context("2026-11-12T22:00:00+08:00")
    assert set(ctx) == {
        "instance_id", "trigger_time", "trigger",
        "trigger_entity_id", "trigger_subject", "trigger_kind",
    }, f"context 键集合与实例侧不同形：{sorted(ctx)}"
    # 缺省值必须与实例侧「无事件」时逐字同形，⛔ 不是另造一套空值
    assert ctx["trigger_entity_id"] == ""
    assert ctx["trigger_subject"] == ""
    assert ctx["trigger_kind"] == ""
    assert ctx["instance_id"] == ""
    assert ctx["trigger"] is None


def test_x9_edge_gate_context_reference_does_not_raise():
    """`context.trigger_*` 三类引用在边沿闸 context 上必须**求得出值**，⛔ 不许抛 KeyError。

    牙（群规⑧）：正例（等值成立 ⇒ True）与反例（不等 ⇒ False）**同时**成立，
    才排除「恒真/恒假」；只断言 `不抛异常` 抓不到 `evaluate` 悄悄返回 False 的退化。
    """
    from autoforge.af_instance import make_eval_context
    from autoforge.af_ir import evaluate
    from autoforge.af_state import make_resolver

    ctx = make_eval_context("2026-11-12T22:00:00+08:00")
    for var, empty in (
        ("context.trigger_entity_id", ""),
        ("context.trigger_kind", ""),
        ("context.trigger_subject", ""),
        ("context.instance_id", ""),
    ):
        eq_empty = {"op": "eq", "left": {"var": var}, "right": {"const": empty}}
        ne_other = {"op": "eq", "left": {"var": var}, "right": {"const": "binary_sensor.x"}}
        assert evaluate(eq_empty, make_resolver({}, {}, ctx)) is True, f"{var} 求值应成立（缺键）"
        assert evaluate(ne_other, make_resolver({}, {}, ctx)) is False, f"{var} 求值应为假（假绿）"
