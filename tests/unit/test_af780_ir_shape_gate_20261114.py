# -*- coding: utf-8 -*-
"""AF-780 判据 · IR 形状闸：`time.at` 只认 HH:MM ＋ `for` 必须挂在带 `to` 的入口（2026-11-14）。

**两根假绿通道**：

1. `time.at` 的**建图闸口径 ≠ 运行期口径**。闸门放行 `HH:MM:SS` 与 `sunrise/sunset±offset`，
   而运行期 `_fire_time_triggers` 比的是 `f"{now.hour:02d}:{now.minute:02d}"`——
   `07:30:00` 永远不等于 `07:30`，`sunrise` 更不等于任何钟点 ⇒ **build 绿、仿真绿、
   真机上这条自动化一次都不会触发**。现成校验器 `af_time.matches_at` 早就只认 HH:MM，
   闸门却自己维护了第二份宽松正则。
2. `for` 持续条件挂在**没有 `to` 的入口**上：运行期 `_still_holds` 明写 `to is None ⇒ False`
   （fail-closed，P1-7「无明确目标态的 for 条件不可靠」），而 `group` 型入口与「变化型 state」
   都拿不到目标态 ⇒ 「等 10 分钟再动」这段永远不成立，build 却一声不吭。

**修法**：闸门正则收到 `^HH:MM$`（与 `matches_at` 同口径）；新增 `FOR_WITHOUT_TO` 诊断；
运行期 `_fire_time_triggers` 改走 `matches_at`，失配**留具名 warning**（旧归档可能先于闸入库，
二道防线不能静默 `continue`）。

⚠️ **仿真掩蔽陷阱（§1.222）**：判据一律走 `af_build`（真闸），⛔ 不拿 `af_simulate` fake 轨的
`seed_from_graph` 当验证面——它会把伪实体/非法形状注册成合法，掩盖缺陷。

**判据**：

- G1 四种非法 `at`（秒格式／空串／`12:60`／非数字）⇒ build 报 `TIME_AT_INVALID`，且正文说清口径
- G2 合法 `HH:MM` ⇒ 不报（不误伤）
- G3 运行期失配 ⇒ 留 `SCHED_TIME_AT_INVALID` 一条，⛔ 不静默
- G4 历史题面（`docs/reference/AF-Spec语法参考.md` 里的 `at: "22:00:00"` 形状）⇒ 现在 build 必红
- G5 `for` × 入口形状：group 型 / 变化型 state ⇒ `FOR_WITHOUT_TO`；带 `to` 的 state ⇒ 通过
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from autoforge.af_bus import BusEvent
from autoforge.af_ir import Graph, load_automation
from autoforge.af_mcp import dispatch
from autoforge.af_runtime import build_runtime
from autoforge.af_store import GraphStore
from autoforge.af_time import VirtualTimeSource

QA = {"subject": "qa", "scopes": ["read", "write"]}


def _build(ir: dict, store: GraphStore) -> dict:
    content, _ = dispatch("af_build", {"ir": ir}, store, QA)
    return json.loads(content[0]["text"])


def _codes(body: dict, key: str = "errors") -> list[str]:
    return [e["code"] for e in body.get(key, [])]


def _ir_time(at: Any) -> dict:
    return {"ir_version": "0.3.0", "id": "t1", "name": "t1", "version": 1, "mode": "single",
            "nodes": [{"id": "a", "kind": "on", "name": "t",
                       "trigger": {"type": "time", "at": at}},
                      {"id": "p", "kind": "pass"}],
            "edges": [{"from": "a", "to": "p", "kind": "then"}]}


def _ir_for(trigger: dict, *, for_: str = "10m") -> dict:
    return {"ir_version": "0.3.0", "id": "f1", "name": "f1", "version": 1, "mode": "single",
            "nodes": [{"id": "a", "kind": "on", "name": "t", "trigger": trigger, "for": for_},
                      {"id": "p", "kind": "pass"}],
            "edges": [{"from": "a", "to": "p", "kind": "then"}]}


# ── G1 四种非法 at ⇒ build 红 ────────────────────────────────────────
@pytest.mark.parametrize("bad_at,why", [
    ("07:30:00", "秒格式：运行期永远拼不出这串"),
    ("", "空串：时间触发没有时刻"),
    ("12:60", "分钟越界"),
    ("早上七点", "非数字"),
])
def test_g1_illegal_at_is_rejected(tmp_path, bad_at, why):
    body = _build(_ir_time(bad_at), GraphStore(str(tmp_path)))
    assert body["ok"] is False, f"at={bad_at!r}（{why}）必须 build 失败"
    assert "TIME_AT_INVALID" in _codes(body), f"at={bad_at!r} 要落具名码，实得 {_codes(body)}"
    msg = json.dumps(body, ensure_ascii=False)
    assert "HH:MM" in msg, "诊断正文必须说清合法口径"


# ── G2 合法 HH:MM 不误伤 ────────────────────────────────────────────
@pytest.mark.parametrize("good_at", ["00:00", "07:05", "19:30", "23:59"])
def test_g2_legal_hhmm_passes(tmp_path, good_at):
    body = _build(_ir_time(good_at), GraphStore(str(tmp_path)))
    assert "TIME_AT_INVALID" not in _codes(body), f"at={good_at!r} 是合法时刻，不许误伤"


# ── G3 运行期失配留痕，⛔ 不静默 ─────────────────────────────────────
def test_g3_runtime_mismatch_logs_named_code(caplog):
    """旧归档（先于闸入库）里 at 带秒 ⇒ 运行期必须留一条具名 warning。

    ⛔ 修前是裸字符串比对后静默 `continue`：盘上一条永远不响的自动化，零读数。
    """
    caplog.set_level(logging.WARNING)
    ir = _ir_time("07:30:00")
    ir["nodes"][0]["trigger"] = {"type": "time", "at": "07:30:00"}
    rt = build_runtime(Graph([load_automation(ir)]),
                       clock=VirtualTimeSource(__import__("datetime").datetime(
                           2026, 11, 14, 7, 30, tzinfo=__import__("datetime").timezone.utc)))

    fired = rt.scheduler._fire_time_triggers()

    assert fired == [], "不合法的 at ⛔ 不许触发（宁可不动，不许猜成别的时刻）"
    hits = [r for r in caplog.records if r.message.startswith("SCHED_TIME_AT_INVALID")]
    assert len(hits) == 1, f"失配必须留 1 条具名码，实得 {[r.message[:40] for r in caplog.records]}"
    assert hits[0].levelno == logging.WARNING
    assert "07:30:00" in hits[0].getMessage(), "留痕必须点名是哪个 at"


# ── G4 历史题面：带秒的 at 现在 build 必红 ────────────────────────────
def test_g4_historical_quiz_shape_is_now_red(tmp_path):
    """`docs/reference/AF-Spec语法参考.md` 里的 `{"type":"time","at":"22:00:00"}` 形状。

    这是"历史题面照文档写"的现场：那批 IR 在修前 build 全绿，真机一次不响。
    """
    body = _build(_ir_time("22:00:00"), GraphStore(str(tmp_path)))
    assert body["ok"] is False and "TIME_AT_INVALID" in _codes(body)


# ── G5 for 必须挂在带 to 的入口 ──────────────────────────────────────
def test_g5a_for_on_group_entry_is_rejected(tmp_path):
    """`for` 挂在 group 型入口：运行期 `_still_holds` 拿不到目标态 ⇒ 永远不成立。"""
    trig = {"type": "group", "op": "or",
            "sources": [{"type": "state", "entity_id": "binary_sensor.m", "to": "on"}]}
    body = _build(_ir_for(trig), GraphStore(str(tmp_path)))
    assert body["ok"] is False, "group 入口 + for 必须 build 红"
    assert "FOR_WITHOUT_TO" in _codes(body)


def test_g5b_for_on_change_type_state_is_rejected(tmp_path):
    """`for` 挂在变化型 state（不带 to）：同样没有目标态。"""
    body = _build(_ir_for({"type": "state", "entity_id": "binary_sensor.m"}),
                  GraphStore(str(tmp_path)))
    assert body["ok"] is False and "FOR_WITHOUT_TO" in _codes(body)


def test_g5c_for_on_state_with_to_passes(tmp_path):
    """对照腿：带 `to` 的 state 入口 + for ⇒ 不报（合法用法），且真的能挂起。"""
    ir = _ir_for({"type": "state", "entity_id": "binary_sensor.m", "to": "on"})
    store = GraphStore(str(tmp_path))
    body = _build(ir, store)
    assert "FOR_WITHOUT_TO" not in _codes(body), f"合法 for 被误伤：{_codes(body)}"

    # 行为面同证：运行期 et 到时长后真能触发（不是只让闸门闭嘴）
    from datetime import datetime, timezone

    rt = build_runtime(Graph([load_automation(ir)]),
                       clock=VirtualTimeSource(datetime(2026, 11, 14, 8, 0, tzinfo=timezone.utc)))
    rt.states.set_state("binary_sensor.m", "off")
    # ⚠️ 必须用 emit（先落状态再发事件）：`_still_holds` 到期时读的是**快照**，
    # 只 publish 不落状态的话，到期复查会看到 off⇒条件已被破坏⇒实例根本不建。
    rt.emit("binary_sensor.m", "on")
    rt.advance(10 * 60 + 1)
    rt.tick()  # for 到期由 tick 消费（advance 只推进时钟）
    assert rt.instances.all(), "到期后实例必须被建出来（for 真的成立过）"
