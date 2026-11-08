# -*- coding: utf-8 -*-
"""AF-745 判据 · 单发（live_run）播报不再静默哑掉（2026-11-08）· 卡名 `AF卡_af745_live_speak`。

**现场**（台账 §1.184，基线 `667e9ce`）：出资人亲令三连跑「纯播报 IR」（客厅＋书房 A/B、零设备写入），
实例**全部走到 e1 done**，但收件账零新增、api 进程零日志。判定：watch 路径 TTS 通（10:47 铁证），
**live_run 路径播报静默失败**，且失败被 api 日志黑洞埋住（v1.28.1 的 `basicConfig` 只接了 watch CLI）。

**三处根因面**：

1. 三条播报腿（ask／do／emit）失败时全部"静默旁路"：无桥 ⇒ 直接 `return`；房间解析不到 ⇒
   拒投递；发布异常 ⇒ 只吞 warning。**回执里连"有没有试过"都看不出来**。
2. api／uvicorn 进程没接日志线：uvicorn 只配自己的三个 logger，root 空着；`af_alert` 只收
   ERROR+ 的收件箱腿 ⇒ `autoforge.*` 的 INFO/WARNING 进不了 docker logs。
3. 于是"播报哑掉"这件事在**两个可观测面（日志／回执）上都不存在**。

**修法**：三条腿共用一枚投递体 `_speak_deliver`（取桥→解析房间→发布→记账），把
`requested／published／failed／last_error／last_room` 记进 `executor.speak_stats`；
`live_run` 回执原样带回 `speech`；`serve` 启动时给 `autoforge` 这棵 logger 挂 stderr handler（幂等）。

**牙**（群规⑧）：L1 是"无桥"这条现场最可能的真因（正腿）；L2 是反腿（有桥就真投出去，
防"永远 failed"的假绿）；L3/L4/L5 把三种失败原因**分别**钉成不同的 `last_error`，
防止笼统一个"failed"把原因糊掉。
"""

from __future__ import annotations

import logging
import sys
import tempfile
from typing import Any, Mapping

import pytest

from autoforge.af_bus import BusEvent
from autoforge.af_ir import Graph, load_automation
from autoforge.af_runtime import build_runtime

#: emit 播报形状（与真机 v6 的 e1 同构：pass 节点 + emit + speech_template）。
AUTO_SPEAK: dict[str, Any] = {
    "ir_version": "0.2.1", "id": "af745", "name": "AF745 播报", "version": 1, "mode": "single",
    "nodes": [
        {"id": "a1", "kind": "on", "name": "有人",
         "trigger": {"type": "state", "entity_id": "binary_sensor.motion"}},
        {"id": "e1", "kind": "pass", "name": "播报书房温度",
         "emit": {"event": "study_ac_started", "data": {"room": "shu_fang"}},
         "speech_template": "当前书房温度是 {temperature} 度",
         "speech_entity": "sensor.temp"},
        {"id": "p1", "kind": "pass", "name": "结束"},
    ],
    "edges": [
        {"from": "a1", "to": "e1", "kind": "then"},
        {"from": "e1", "to": "p1", "kind": "then"},
    ],
}


class _FakeBridge:
    """只记录 publish_speak 的桥桩；可配置返回 False 或抛异常。"""

    def __init__(self, *, published: bool = True, raises: BaseException | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.published = published
        self.raises = raises

    def observe_terminal(self, instance, state) -> None:
        """终态观察者钩子：`af_runtime._on_terminal` 会遍历 `_BRIDGES` 取它（缺了会 AttributeError）。"""
        self.terminal = (getattr(instance, "instance_id", ""), state)

    def publish_speak(self, *, trace_id: str, text: str, room: str) -> dict[str, Any]:
        self.calls.append({"trace_id": trace_id, "text": text, "room": room})
        if self.raises is not None:
            raise self.raises
        if not self.published:
            return {"published": False, "code": "ADM_ERR_BROKER_UNREACHABLE"}
        return {"published": True, "topic": "butler/inbox/speak"}


@pytest.fixture(autouse=True)
def _restore_bridge_registry():
    """本文件会往 `af_mqtt_bridge._BRIDGES` 里塞假桥——测完必须还原。

    踩过的坑：假桥没有 `degraded` 等真桥属性，留在全局注册表里会让后面的
    `af_health`（`_linkage_health → linkage_status`）AttributeError 而红——
    那是**测试污染**，不是被测代码的问题。
    """
    from autoforge import af_mqtt_bridge

    saved = list(af_mqtt_bridge._BRIDGES)
    try:
        yield
    finally:
        af_mqtt_bridge._BRIDGES[:] = saved


def _runtime(*, bridge, states: Mapping[str, Any] | None = None, area: str | None = "shu_fang"):
    rt = build_runtime(Graph([load_automation(AUTO_SPEAK)]), store_root=tempfile.mkdtemp())
    for k, v in (states or {"sensor.temp": "27.9", "binary_sensor.motion": "on"}).items():
        rt.states.set_state(k, v)
    # 桥：闭包是**惰性取** current_bridge()，所以运行时注入即可
    from autoforge import af_mqtt_bridge
    af_mqtt_bridge._BRIDGES[:] = []   # 只在本测试内清空；fixture 负责还原
    if bridge is not None:
        af_mqtt_bridge.attach(bridge)  # type: ignore[arg-type]
    # 房间解析：本判据只钉"记账"，房间解析用桩固定（fail-closed 语义另有既存判据）
    rt.resolve_room = lambda room, source="", entity_id="": area  # type: ignore[method-assign]
    return rt


def _fire(rt) -> None:
    rt.publish(BusEvent.of("binary_sensor.motion", "on"))


# ── L1 无桥 ⇒ 必须记成失败（现场最可能的那条真因），⛔ 不许静默 ─────────────

def test_l1_no_bridge_is_recorded_not_silent():
    rt = _runtime(bridge=None)
    _fire(rt)
    stats = rt.executor.speak_stats
    assert stats["requested"] >= 1, f"播报钩子没被调用，或计数没接上：{stats}"
    assert stats["failed"] >= 1, f"无桥时必须记失败，实得 {stats}"
    assert stats["last_error"] == "no_bridge", stats
    assert stats["published"] == 0


# ── L2 反腿：有桥就必须真投出去（防"永远 failed"的假绿）──────────────────

def test_l2_bridge_publishes_and_counts():
    bridge = _FakeBridge()
    rt = _runtime(bridge=bridge)
    _fire(rt)
    stats = rt.executor.speak_stats
    assert bridge.calls, "有桥时 publish_speak 必须被调用"
    assert bridge.calls[0]["room"] == "shu_fang", bridge.calls[0]
    assert "27.9" in bridge.calls[0]["text"] or "当前书房温度" in bridge.calls[0]["text"], bridge.calls[0]
    assert stats["published"] >= 1, stats
    assert stats["failed"] == 0, stats
    assert stats["last_room"] == "shu_fang", stats


# ── L3 房间解析不到 ⇒ 单独一种 last_error（fail-closed 仍拒投递）──────────

def test_l3_area_unresolved_is_its_own_reason():
    bridge = _FakeBridge()
    rt = _runtime(bridge=bridge, area=None)
    _fire(rt)
    stats = rt.executor.speak_stats
    assert stats["last_error"] == "area_unresolved", stats
    assert not bridge.calls, "解析不到 ⇒ ⛔ 不许投递（fail-closed）"


# ── L4 broker 拒收（published=False）⇒ 带码 ──────────────────────────────

def test_l4_publish_refused_carries_code():
    bridge = _FakeBridge(published=False)
    rt = _runtime(bridge=bridge)
    _fire(rt)
    stats = rt.executor.speak_stats
    assert str(stats["last_error"]).startswith("publish:"), stats
    assert "ADM_ERR_BROKER_UNREACHABLE" in str(stats["last_error"]), stats


# ── L5 发布抛异常 ⇒ 不冒到执行链，但记成 publish_raised ──────────────────

def test_l5_publish_exception_is_contained_and_recorded():
    bridge = _FakeBridge(raises=RuntimeError("broker 断了"))
    rt = _runtime(bridge=bridge)
    _fire(rt)  # 异常若穿出，这里会直接抛
    stats = rt.executor.speak_stats
    assert str(stats["last_error"]).startswith("publish_raised:RuntimeError"), stats
    assert rt.instances.all(), "实例仍要正常产生（播报是旁路）"


# ── L6 回执面：live_run 必须把 speech 带回去（源码钉 + 形状）─────────────

def test_l6_live_run_receipt_carries_speech_block():
    import inspect

    from autoforge import af_service

    src = inspect.getsource(af_service.live_run)
    assert '"speech"' in src, "live_run 回执必须带 speech 段（⛔ 不许播报哑掉还报 ok 无解释）"
    assert "speak_stats" in src, "speech 段要取三条腿的事实计数"


# ── L7 api 进程日志接线：幂等、级别、格式、真能出到 stderr ────────────────

def test_l7_serve_logging_wiring(monkeypatch, capsys):
    from autoforge import af_cli

    lg = logging.getLogger("autoforge")
    for h in list(lg.handlers):
        if getattr(h, "_autoforge_stderr", False):
            lg.removeHandler(h)

    af_cli._configure_serve_logging()
    handlers = [h for h in lg.handlers if getattr(h, "_autoforge_stderr", False)]
    assert len(handlers) == 1, f"必须挂且只挂一枚 stderr handler：{lg.handlers}"
    handler = handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    assert handler.formatter is not None and "%(levelname)s" in handler.formatter._fmt
    assert lg.level <= logging.INFO, f"级别必须到 INFO（否则 INFO 级留痕仍被丢）：{lg.level}"

    # 幂等：再调一次不叠加
    af_cli._configure_serve_logging()
    assert len([h for h in lg.handlers if getattr(h, "_autoforge_stderr", False)]) == 1

    # 真能出到 stderr（现场要的就是 docker logs 里能看到）
    logging.getLogger("autoforge.executor").warning("AF745_PROBE 探针")
    captured = capsys.readouterr()
    assert "AF745_PROBE" in captured.err, f"WARNING 没进 stderr：{captured.err!r}"
    # 收尾：把自己挂的 handler 摘掉，别把 handler 计数污染给后续用例
    for h in [h for h in lg.handlers if getattr(h, "_autoforge_stderr", False)]:
        lg.removeHandler(h)


# ── L8 serve 必须在 uvicorn.run 之前接日志线（顺序钉）───────────────────

def test_l8_serve_configures_logging_before_uvicorn():
    import inspect

    from autoforge import af_cli

    src = inspect.getsource(af_cli.serve)
    i_cfg = src.find("_configure_serve_logging(")
    # ⚠️ 不能用 `src.find("uvicorn.run(")`：serve 的注释里先出现过一次 `uvicorn.run()` 字样
    # （SIGTERM 那段说明），会把顺序判反。取真调用点的实参形状。
    i_run = src.find("uvicorn.run(app_")
    assert i_cfg != -1, "serve 里必须调用日志接线（不然 api 进程还是黑洞）"
    assert i_run != -1 and i_cfg < i_run, "日志接线必须在 uvicorn.run 之前"
