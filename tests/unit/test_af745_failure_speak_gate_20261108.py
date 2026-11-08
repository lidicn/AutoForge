# -*- coding: utf-8 -*-
"""AF-745 追加判据 · **动作失败必须出声**（出资人 2026-11-08 19:34 亲令）· 卡名 `AF卡_v7_完整自动化` §八。

**现场**：18:29 那轮 `climate.set_temperature` 被 HA 拒收，出资人在屋里**什么都没听到**——
失败只进了 `watch.log` 与内存审计；人不看日志，就等于"自动化没动静"。出资人原话方向：
失败要念出「失败动作＋原因」，⛔ 静默哑掉。

**修法**：`NodeExecutor._speak_for_failure` 在 `_action_failed_result` 里被调（失败收口点，
canary 抛错腿共用），文本＝「`<适配器>.<动作>` 执行失败：`<原因>`」；原因压成单行并截断
（英文栈直接念会断句全乱）；房间/实体解析与 do 腿同源；投递走**同一条** `_speak_deliver`
（取桥→房间→发布→记账），失败仍落 `speak_stats`，⛔ 不另造第二套。

**牙**（群规⑧）：K1 是正腿（失败要念），K2 是反腿（**成功⛔ 不许念**——否则一次派发念三句的
旧病复发）；K3 钉未注入时零副作用；K4 钉播报旁路抛异常⛔ 不许改写失败判定；K5 源码钉防
静默回退；K6 走真机链（假桥）证明它真的到得了 `butler/inbox/speak` 那条腿。
"""

from __future__ import annotations

import tempfile
from typing import Any

import pytest

from autoforge.af_ir import Graph, load_automation
from autoforge.af_runtime import build_runtime

ROOM = "shu_fang"
CLIMATE = "climate.study"


class _Adapter:
    """可控适配器：指定动作失败／抛异常（与 AF-742 判据同形）。"""

    name = "ha"
    dry_run = False

    def __init__(self, *, fail: tuple[str, ...] = (), raise_on: tuple[str, ...] = ()) -> None:
        self.fail = set(fail)
        self.raise_on = set(raise_on)
        self.calls: list[tuple[str, dict]] = []

    def call(self, action: str, params: dict):
        from autoforge.af_adapters.base import CallResult

        self.calls.append((action, dict(params)))
        if action in self.raise_on:
            raise RuntimeError("HA 连接被重置")
        if action in self.fail:
            return CallResult(
                success=False,
                error="RuntimeError: 执行错误, 未知错误,\n code=-706010002, target-temperature",
            )
        return CallResult.ok({})


def _auto(*, action: str = "climate.set_temperature") -> dict[str, Any]:
    """on → do（失败有 on_error 兜底）→ pass。"""
    return {
        "ir_version": "0.2.1", "id": "af745f", "name": "AF745 失败播报", "version": 1,
        "mode": "single",
        "nodes": [
            {"id": "a1", "kind": "on", "name": "有人",
             "trigger": {"type": "state", "entity_id": "binary_sensor.motion"}},
            {"id": "d1", "kind": "do", "name": "设 26 度", "adapter": "ha",
             "action": action, "params": {"entity_id": CLIMATE, "temperature": 26},
             "room": ROOM},
            {"id": "p1", "kind": "pass", "name": "结束"},
        ],
        "edges": [
            {"from": "a1", "to": "d1", "kind": "then"},
            {"from": "d1", "to": "p1", "kind": "then"},
            {"from": "d1", "to": "p1", "kind": "on_error"},
        ],
    }


def _rt(adapter: _Adapter, *, action: str = "climate.set_temperature"):
    rt = build_runtime(Graph([load_automation(_auto(action=action))]),
                       store_root=tempfile.mkdtemp())
    rt.adapters.register(adapter)
    rt.states.set_state("binary_sensor.motion", "on")
    return rt


def _fire(rt) -> None:
    from autoforge.af_bus import BusEvent

    rt.publish(BusEvent.of("binary_sensor.motion", "on"))


class _Spy:
    def __init__(self, raises: Exception | None = None) -> None:
        self.calls: list[tuple[str, str, str, str]] = []
        self.raises = raises

    def __call__(self, trace_id: str, text: str, room: str, entity_id: str = "") -> None:
        self.calls.append((trace_id, text, room, entity_id))
        if self.raises is not None:
            raise self.raises


# ── K1 失败必须出声：文本含「动作＋原因」，单行且截断 ────────────────────────

def test_k1_failed_action_is_spoken():
    spy = _Spy()
    rt = _rt(_Adapter(fail=("climate.set_temperature",)))
    rt.executor.on_fail_speak = spy
    _fire(rt)

    assert len(spy.calls) == 1, f"动作失败了却没播报（现场就是这个形状）：{spy.calls}"
    _tid, text, room, entity = spy.calls[0]
    assert "climate.set_temperature" in text, f"念出来必须点明是哪个动作失败：{text!r}"
    assert "执行失败" in text, f"必须说清是失败，不能只说动作名：{text!r}"
    assert "706010002" in text, f"原因要念出来（不然等于只说'失败了'）：{text!r}"
    assert "\n" not in text, f"原因必须压成单行（换行会让 TTS 断句全乱）：{text!r}"
    assert len(text) <= 200, f"失败文本过长（TTS 念不完）：{len(text)} 字"
    assert room == ROOM, f"房间取法与 do 腿同源（node.room）：{room!r}"
    assert entity == CLIMATE, f"实体要带给 Runtime 做 catalog 反查：{entity!r}"


# ── K2 反腿：成功⛔ 不许念（防"一次派发念三句"旧病复发）───────────────────

def test_k2_success_is_not_spoken():
    spy = _Spy()
    rt = _rt(_Adapter())
    rt.executor.on_fail_speak = spy
    _fire(rt)

    assert spy.calls == [], f"动作成功却走了失败播报（会变成每次派发都念失败）：{spy.calls}"


# ── K3 未注入回调 ⇒ 零副作用，执行链照常 ──────────────────────────────────

def test_k3_no_callback_is_zero_side_effect():
    rt = _rt(_Adapter(fail=("climate.set_temperature",)))
    assert rt.executor.on_fail_speak is not None, "Runtime 必须注入失败播报腿（否则等于没做）"
    rt.executor.on_fail_speak = None  # 单测隔离形态（无桥/无 Runtime）
    _fire(rt)
    inst = rt.instances.all()[0]
    assert inst.ctx.failed_actions == 1, "未注入回调不该影响失败记账"
    assert inst.state == "done", f"on_error 兜底应正常走完：{inst.state}"


# ── K4 播报旁路抛异常 ⇒ ⛔ 不许改写失败判定 ───────────────────────────────

def test_k4_speak_exception_does_not_change_verdict():
    rt = _rt(_Adapter(fail=("climate.set_temperature",)))
    rt.executor.on_fail_speak = _Spy(raises=RuntimeError("广播桥炸了"))
    _fire(rt)

    inst = rt.instances.all()[0]
    assert inst.ctx.failed_actions == 1
    assert inst.state == "done", f"播报抛异常把实例打成 {inst.state} ⇒ 旁路污染了判定"


# ── K5 源码钉：失败收口点必须调它（⛔ 回退成静默）────────────────────────

def test_k5_failure_branch_calls_the_speak_leg():
    import inspect

    from autoforge.af_executor import NodeExecutor

    src = inspect.getsource(NodeExecutor._action_failed_result)
    assert "_speak_for_failure(" in src, (
        "失败收口点没调失败播报 ⇒ 静默哑掉会原样回来（canary 抛错腿也走这个收口）"
    )


# ── K6 真机链：假桥收到失败文本，且记账进 speak_stats ────────────────────

class _FakeBridge:
    def __init__(self) -> None:
        self.spoken: list[dict[str, Any]] = []
        self.degraded = False

    def observe_terminal(self, instance, state) -> None:
        self.terminal = (getattr(instance, "instance_id", ""), state)

    def publish_speak(self, *, trace_id: str, text: str, room: str) -> dict[str, Any]:
        self.spoken.append({"trace_id": trace_id, "text": text, "room": room})
        return {"published": True, "topic": "butler/inbox/speak"}


@pytest.fixture()
def _bridge_registry():
    """§1.187-① 污染纪律：往 `_BRIDGES` 塞桩必须快照+还原（假红长得很像真红）。"""
    from autoforge import af_mqtt_bridge

    saved = list(af_mqtt_bridge._BRIDGES)
    try:
        yield af_mqtt_bridge
    finally:
        af_mqtt_bridge._BRIDGES[:] = saved


def test_k6_runtime_leg_reaches_the_bridge(_bridge_registry):
    from autoforge import af_mqtt_bridge

    bridge = _FakeBridge()
    af_mqtt_bridge._BRIDGES[:] = []
    af_mqtt_bridge.attach(bridge)  # type: ignore[arg-type]

    rt = _rt(_Adapter(fail=("climate.set_temperature",)))
    rt.resolve_room = lambda room, source="", entity_id="": ROOM  # type: ignore[method-assign]
    _fire(rt)

    assert bridge.spoken, "失败播报没到桥（Runtime 腿没接上 / 房间解析把它挡了）"
    text = bridge.spoken[0]["text"]
    assert "climate.set_temperature" in text and "执行失败" in text, text
    assert bridge.spoken[0]["room"] == ROOM

    stats = rt.executor.speak_stats
    assert stats["requested"] >= 1 and stats["published"] >= 1, f"播报记账没跟上：{stats}"
