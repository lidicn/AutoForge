# -*- coding: utf-8 -*-
"""AF-786 判据 · `for`／`wait` 的时长字面量进构建闸（2026-11-14）。

**假绿通道**：闸门此前**一枚时长字段都不查**（`for` 只查"有没有 `to`"、`wait` 什么都不查），
而运行期两处都要 `af_time.parse_duration`：

1. `Scheduler._update_pending` 里的 `parse_duration(node.for_ or "0s")` **不在任何 `try` 里**
   ⇒ `for: "-10m"` 这类值一匹配上入口就在 `handle_event` 里抛，**打断整轮事件分发**：
   不是这一条自动化失败，是同一轮里排在它后面的每一条都不再被处理；
2. `Executor._suspend` 里 `parse_duration(node.duration)` 的异常被收成 `_soft_fail`
   ⇒ `wait "0:0:0x"` 这类值 build 绿、仿真绿，真机走到该节点时静默走 on_error/default，
   "等一会儿再动"这一段永远不发生。

**修法**：闸门直接调用**运行期那个解析器本身**（⚔️ 不另写一份宽松正则——AF-780 的 `time.at`
就是这么造出假绿的），抛了就落 `DURATION_INVALID`（ERROR）。运行期兜底一行未动。

**判据**：

- D1 `for` 负值／`-00:10:00`／非数字 ⇒ build 红且落 `DURATION_INVALID`
- D2 `wait` 负值／非数字 ⇒ build 红
- D3 合法形态（`10m`／`00:10:00`／`PT5S`／纯数字）⇒ 不误伤
- D4 后果腿：绕过闸门直接建图，畸形的 `for` 会让 `handle_event` 抛（整轮分发停摆的实证）
- D5 双目录同源：`DURATION_INVALID` 在 `CHECKS` 与 `CODE_HINT` 都在，且 hint 不是套话
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from autoforge.af_bus import BusEvent
from autoforge.af_ir import Graph, load_automation
from autoforge.af_mcp import dispatch
from autoforge.af_runtime import build_runtime
from autoforge.af_store import GraphStore
from autoforge.af_time import VirtualTimeSource

CODE = "DURATION_INVALID"
QA = {"subject": "qa", "scopes": ["read", "write"]}
MOTION = "binary_sensor.af786_motion"


def _build(ir: dict, store: GraphStore) -> dict:
    content, _ = dispatch("af_build", {"ir": ir}, store, QA)
    return json.loads(content[0]["text"])


def _codes(body: dict, key: str = "errors") -> list[str]:
    return [e["code"] for e in body.get(key, [])]


def _ir_for(for_: object) -> dict:
    """入口带 `to` 的 state ＋ `for`——`for` 本身合法与否只看这一格。"""
    return {"ir_version": "0.3.0", "id": "af786_for", "name": "af786_for", "version": 1,
            "mode": "single",
            "nodes": [{"id": "t1", "kind": "on", "name": "有人",
                       "trigger": {"type": "state", "entity_id": MOTION, "to": "on"},
                       "for": for_},
                      {"id": "p1", "kind": "pass", "name": "结束"}],
            "edges": [{"from": "t1", "to": "p1", "kind": "then"}]}


def _ir_wait(duration: object) -> dict:
    ir = {"ir_version": "0.3.0", "id": "af786_wait", "name": "af786_wait", "version": 1,
          "mode": "single",
          "nodes": [{"id": "t1", "kind": "on", "name": "有人",
                     "trigger": {"type": "state", "entity_id": MOTION, "to": "on"}},
                    {"id": "w1", "kind": "wait", "name": "等一会儿"},
                    {"id": "p1", "kind": "pass", "name": "结束"}],
          "edges": [{"from": "t1", "to": "w1", "kind": "then"},
                    {"from": "w1", "to": "p1", "kind": "then"}]}
    ir["nodes"][1]["duration"] = duration
    return ir


def _ir_ask(timeout: object) -> dict:
    return {"ir_version": "0.3.0", "id": "af786_ask", "name": "af786_ask", "version": 1,
            "mode": "single",
            "nodes": [{"id": "t1", "kind": "on", "name": "有人",
                       "trigger": {"type": "state", "entity_id": MOTION, "to": "on"}},
                      {"id": "q1", "kind": "ask", "name": "问一句", "prompt": "要开灯吗",
                       "room": "study", "timeout": timeout},
                      {"id": "p1", "kind": "pass", "name": "结束"}],
            "edges": [{"from": "t1", "to": "q1", "kind": "then"},
                      {"from": "q1", "to": "p1", "kind": "yes"},
                      {"from": "q1", "to": "p1", "kind": "no"},
                      {"from": "q1", "to": "p1", "kind": "default"}]}


def _ir_emit(delay: object) -> dict:
    """`emit` 是**节点字段**（不是第 8 种 kind）——挂在 pass 节点上就够触发渲染/暂停腿。"""
    return {"ir_version": "0.3.0", "id": "af786_emit", "name": "af786_emit", "version": 1,
            "mode": "single",
            "nodes": [{"id": "t1", "kind": "on", "name": "有人",
                       "trigger": {"type": "state", "entity_id": MOTION, "to": "on"},
                       "debounce": "2s"},
                      {"id": "e1", "kind": "pass", "name": "发事件",
                       "emit": {"event": "af786_ping", "delay": delay}},
                      {"id": "p1", "kind": "pass", "name": "结束"}],
            "edges": [{"from": "t1", "to": "e1", "kind": "then"},
                      {"from": "e1", "to": "p1", "kind": "then"}]}


def _ir_debounce(debounce: object) -> dict:
    ir = _ir_for("10m")
    ir["nodes"][0]["debounce"] = debounce
    return ir


# ── D1 `for` 的畸形值 ⇒ build 红 ───────────────────────────────────────
@pytest.mark.parametrize("bad,why", [
    ("-10m", "负值：不是「反向等待」，运行期 fail-closed 直接抛"),
    ("-00:10:00", "AF-775 现场：修前静默成 600 秒，修后抛——闸门也必须拦住"),
    ("abc", "非数字"),
    ("PT", "ISO 空壳（没有任何时长成分）"),
    ("inf", "无穷大"),
])
def test_d1_bad_for_duration_is_rejected(tmp_path, bad, why):
    body = _build(_ir_for(bad), GraphStore(str(tmp_path)))
    assert body["ok"] is False, f"for={bad!r}（{why}）必须 build 失败"
    assert CODE in _codes(body), f"for={bad!r} 要落具名码，实得 {_codes(body)}"
    msg = json.dumps(body, ensure_ascii=False)
    assert "合法形态" in msg, "诊断正文必须给出可写的合法形态（否则 Agent 只能猜）"


# ── D2 `wait` 的畸形值 ⇒ build 红 ──────────────────────────────────────
@pytest.mark.parametrize("bad", ["-00:10:00", "-5m", "0:0:0x", "PT"])
def test_d2_bad_wait_duration_is_rejected(tmp_path, bad):
    body = _build(_ir_wait(bad), GraphStore(str(tmp_path)))
    assert body["ok"] is False, f"wait duration={bad!r} 必须 build 失败"
    assert CODE in _codes(body), f"wait duration={bad!r} 要落具名码，实得 {_codes(body)}"


# ── D3 合法形态不误伤（含 HA 原生 00:10:00 与纯数字） ────────────────────
@pytest.mark.parametrize("good", ["10m", "90s", "2h", "1d", "600", "00:10:00", "PT5S"])
def test_d3_legal_for_duration_passes(tmp_path, good):
    body = _build(_ir_for(good), GraphStore(str(tmp_path)))
    assert body["ok"] is True, f"for={good!r}：合法值不该带出 ERROR，实得 {_codes(body)}"
    assert CODE not in _codes(body), f"for={good!r} 是合法时长，不许误伤：{_codes(body)}"


@pytest.mark.parametrize("good", ["30s", "5m", "00:10:00", "PT5S", "0s"])
def test_d3b_legal_wait_duration_passes(tmp_path, good):
    body = _build(_ir_wait(good), GraphStore(str(tmp_path)))
    assert body["ok"] is True, f"wait duration={good!r}：合法值不该带出 ERROR，实得 {_codes(body)}"
    assert CODE not in _codes(body), f"wait duration={good!r} 是合法时长，不许误伤：{_codes(body)}"


# ── D4 后果腿：闸门放行时运行期真的炸（说明这不是「多一条诊断」） ───────────
def test_d4_bad_for_crashes_event_dispatch_when_gate_bypassed():
    """绕过闸门（直接 `load_automation` 建图）⇒ `handle_event` 抛 `ValueError`。

    这一段钉的是**严重性**：`parse_duration` 的抛点在 `Scheduler._update_pending`，
    不在任何 `try` 里 ⇒ 抛的是整轮分发，不是这一条自动化的失败。闸门不拦，它只在真机露。
    """
    rt = build_runtime(
        Graph([load_automation(_ir_for("-10m"))]),
        clock=VirtualTimeSource(datetime(2026, 11, 14, 9, 0, tzinfo=timezone.utc)),
    )
    rt.states.set_state(MOTION, "off")
    with pytest.raises(ValueError):
        rt.scheduler.handle_event(BusEvent(MOTION, "on", last_changed="2026-11-14T01:00:00Z"))


# ── D5 双目录同源（不登记就是半张脸） ──────────────────────────────────
def test_d5_code_is_registered_in_both_catalogs():
    from autoforge.af_scanner import CHECKS, CODE_HINT

    assert CODE in CHECKS, f"{CODE} 未登记进 CHECKS"
    hint = CODE_HINT.get(CODE, "").strip()
    assert hint, f"{CODE} 的修法是空的——Agent 拿到只剩一张空脸"
    assert "parse_duration" in hint or "非负" in hint, f"修法必须指到口径上：{hint[:60]}"


# ── D6 同族三格（debounce／ask.timeout／emit.delay）也在闸内 ─────────────
@pytest.mark.parametrize("maker,bad,field", [
    (_ir_debounce, "-3s", "debounce"),
    (_ir_ask, "0:0:0x", "timeout"),
    (_ir_emit, "-PT5S", "emit.delay"),
])
def test_d6_sibling_duration_fields_are_gated(tmp_path, maker, bad, field):
    """这三格与 for/wait 同一条失败轴（运行期同一个解析器抛、抛点没有具名码兜底）。

    渲染器改为宽容之后它们不再"build 直接炸"，所以必须有闸——否则就是把「抛异常」
    悄悄换成「build 绿、真机炸」，那比修前更糟。
    """
    body = _build(maker(bad), GraphStore(str(tmp_path)))
    assert body["ok"] is False, f"{field}={bad!r} 必须 build 失败"
    assert CODE in _codes(body), f"{field}={bad!r} 要落具名码，实得 {_codes(body)}"
    assert field in json.dumps(body, ensure_ascii=False), "诊断必须点名是哪一格"


@pytest.mark.parametrize("maker,good", [
    (_ir_debounce, "5s"),
    (_ir_ask, "60s"),
    (_ir_emit, "PT5S"),
])
def test_d6b_sibling_legal_values_pass(tmp_path, maker, good):
    body = _build(maker(good), GraphStore(str(tmp_path)))
    assert body["ok"] is True, f"{good!r}：合法值不该带出 ERROR，实得 {_codes(body)}"
    assert CODE not in _codes(body), f"{good!r} 合法，不许误伤：{_codes(body)}"


# ── D7 渲染器不再被一个坏时长炸掉（否则整份 IR 一条诊断都拿不到） ─────────
def test_d7_nl_render_tolerates_bad_duration():
    from autoforge.af_nl import render_automation

    text = render_automation(load_automation(_ir_for("-10m"))).text
    assert "非法时长" in text, f"渲染面必须把非法值原样叙出来：{text[:80]}"
