# -*- coding: utf-8 -*-
"""判据 · room 缺失时按 do 的目标实体反查区域（2026-11-05 真机修）· 卡名 AF卡_room_from_entity。

真机现场（`/data/watch.log` 现读，v1.25.0）：
    AREA_UNRESOLVED 房间解析不到 HA area_id，拒投递 source=do room='' …
正好 2 条，与两次 do 执行（`trial.state=observing` 的两条旁车）对齐 ⇒ T1 的播报腿**被 ② 的
fail-closed 自己焊死**：线上 IR 的 do 节点根本没有 `room` 字段。

修法（⛔ 不退回 fail-open）：room 缺失/解析不到时，用该 do 的**目标实体**去 catalog 反查
`entity → area_id`；查到就用，实体也查不到才拒投递并打 `AREA_UNRESOLVED`。

D 编号带卡名（群规⑭）：
  D1 无 room + 实体在 catalog ⇒ 播出去，且 room=该实体的 area_id（不再拒发）
  D2 无 room + 实体不在 catalog ⇒ 仍拒发（fail-closed 不退）+ 打 AREA_UNRESOLVED
  D3 显式 room 优先于实体反查（room 能解析时以 room 为准）
  D4 `params.target.entity_id` 形态也认（HA 原生 target 形态）
  D5 源码钉：`_speak_for_do` 把实体传给回调、`resolve_room` 有实体兜底腿
"""

from __future__ import annotations

import json

import pytest

from autoforge.af_bus import BusEvent
from autoforge.af_ir import Graph, load_automation
from autoforge.af_runtime import build_runtime

CATALOG = {
    "version": 1,
    "entities": {
        "climate.ac": {"area_id": "shu_fang", "area": "书房"},
        "light.x": {"area_id": "ke_ting", "area": "客厅"},
    },
}

AUTO = {
    "ir_version": "0.2.1", "id": "room_fallback", "name": "房间反查用例", "version": 1,
    "mode": "restart",
    "nodes": [
        {"id": "a1", "kind": "on", "name": "人",
         "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
        # v1.27.1 起播报「显式才出声」⇒ 本卡量的是**房间解析**，必须给显式 announce
        # 才能把 speak 腿跑起来（无文案那格由 do_speak 判据 D4 的静默断言覆盖）。
        {"id": "d1", "kind": "do", "name": "开空调", "adapter": "climate", "action": "set_temp",
         "params": {"entity_id": "climate.ac", "temperature": 26},   # 注意：没有 room
         "announce": "我把空调打开"},
        {"id": "p1", "kind": "pass", "name": "结束"},
    ],
    "edges": [
        {"from": "a1", "to": "d1", "kind": "then"},
        {"from": "d1", "to": "p1", "kind": "then"},
    ],
}


class _Adapter:
    name = "climate"
    dry_run = False

    def call(self, action, params):
        from autoforge.af_adapters.base import CallResult
        return CallResult(success=True, data={}, error=None)


class _FakeBridge:
    def __init__(self):
        self.speaks = []

    def publish_speak(self, *, trace_id, text, room="", source="autoforge", priority=1):
        self.speaks.append({"trace_id": trace_id, "text": text, "room": room})
        return {"published": True}


@pytest.fixture()
def bridge(monkeypatch):
    import autoforge.af_mqtt_bridge as bm

    fake = _FakeBridge()
    monkeypatch.setattr(bm, "current_bridge", lambda: fake)
    return fake


def _store(tmp_path, catalog=None):
    store = tmp_path / "store"
    (store / ".catalog").mkdir(parents=True)
    (store / ".catalog" / "catalog.json").write_text(
        json.dumps(catalog if catalog is not None else CATALOG, ensure_ascii=False),
        encoding="utf-8",
    )
    return store


def _run(auto, store):
    rt = build_runtime(Graph([load_automation(auto)]), store_root=str(store))
    rt.adapters.register(_Adapter())
    rt.states.set_state("binary_sensor.m", "off")
    rt.publish(BusEvent.of("binary_sensor.m", "on"))
    return rt


# ── D1 无 room + 实体在 catalog ⇒ 播 area_id ──────────────────────
def test_d1_entity_area_used_when_room_missing(tmp_path, bridge):
    _run(AUTO, _store(tmp_path))
    assert len(bridge.speaks) == 1, f"应播一次（靠实体反查），实得 {bridge.speaks}"
    assert bridge.speaks[0]["room"] == "shu_fang", f"room 应为实体的 area_id，实得 {bridge.speaks[0]['room']!r}"


# ── D2 无 room + 实体不在 catalog ⇒ 仍拒发 ────────────────────────
def test_d2_unknown_entity_still_refused(tmp_path, bridge, caplog):
    auto = {**AUTO, "nodes": [AUTO["nodes"][0],
                              {**AUTO["nodes"][1], "params": {"entity_id": "climate.unknown"}},
                              AUTO["nodes"][2]]}
    with caplog.at_level("WARNING"):
        _run(auto, _store(tmp_path))
    assert bridge.speaks == [], "实体也查不到时⛔ 不许播（fail-closed 不退）"
    assert any("AREA_UNRESOLVED" in r.message for r in caplog.records), "仍须打 AREA_UNRESOLVED"


# ── D3 显式 room 优先 ────────────────────────────────────────────
def test_d3_explicit_room_wins(tmp_path, bridge):
    auto = {**AUTO, "nodes": [AUTO["nodes"][0],
                              {**AUTO["nodes"][1], "room": "客厅"},
                              AUTO["nodes"][2]]}
    _run(auto, _store(tmp_path))
    assert len(bridge.speaks) == 1
    assert bridge.speaks[0]["room"] == "ke_ting", f"显式 room 应优先，实得 {bridge.speaks[0]['room']!r}"


# ── D4 target.entity_id 形态 ─────────────────────────────────────
def test_d4_target_entity_form(tmp_path, bridge):
    auto = {**AUTO, "nodes": [AUTO["nodes"][0],
                              {**AUTO["nodes"][1], "params": {"target": {"entity_id": "light.x"}}},
                              AUTO["nodes"][2]]}
    _run(auto, _store(tmp_path))
    assert len(bridge.speaks) == 1
    assert bridge.speaks[0]["room"] == "ke_ting", f"target.entity_id 也要认，实得 {bridge.speaks[0]['room']!r}"


# ── D6 emit 播报走同源钩子（AF-PM 裁定 2026-11-05） ──────────────
EMIT_AUTO = {
    "ir_version": "0.2.1", "id": "emit_speak", "name": "emit播报用例", "version": 1,
    "mode": "restart",
    "nodes": [
        {"id": "a1", "kind": "on", "name": "t",
         "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
        {"id": "e1", "kind": "pass", "name": "广播已启动",
         "announce": "书房空调已启动",
         "emit": {"event": "study_ac_started", "data": {"room": "书房", "action": "cool_26"}}},
        {"id": "p1", "kind": "pass", "name": "结束"},
    ],
    "edges": [
        {"from": "a1", "to": "e1", "kind": "then"},
        {"from": "e1", "to": "p1", "kind": "then"},
    ],
}


def test_d6_emit_node_speaks_via_same_path(tmp_path, bridge):
    """emit 节点的 data.room 是中文名 ⇒ 经同一条 housesdk 解析路径落成 area_id 并播出去。"""
    rt = build_runtime(Graph([load_automation(EMIT_AUTO)]), store_root=str(_store(tmp_path)))
    rt.adapters.register(_Adapter())
    rt.states.set_state("binary_sensor.m", "off")
    rt.publish(BusEvent.of("binary_sensor.m", "on"))
    assert len(bridge.speaks) == 1, f"emit 应播一次（同源钩子），实得 {bridge.speaks}"
    assert bridge.speaks[0]["room"] == "shu_fang", f"room 应是 area_id，实得 {bridge.speaks[0]['room']!r}"
    assert "study_ac_started" in bridge.speaks[0]["text"] or bridge.speaks[0]["text"], "文本非空"


def test_d7_emit_without_room_still_refused(tmp_path, bridge, caplog):
    """emit 没写 room、data 里也没有 ⇒ 仍拒发（三腿同一 fail-closed 口径）。"""
    auto = {**EMIT_AUTO, "nodes": [
        EMIT_AUTO["nodes"][0],
        {**EMIT_AUTO["nodes"][1], "emit": {"event": "study_ac_started", "data": {}}},
        EMIT_AUTO["nodes"][2],
    ]}
    rt = build_runtime(Graph([load_automation(auto)]), store_root=str(_store(tmp_path)))
    rt.adapters.register(_Adapter())
    with caplog.at_level("WARNING"):
        rt.states.set_state("binary_sensor.m", "off")
        rt.publish(BusEvent.of("binary_sensor.m", "on"))
    assert bridge.speaks == [], "emit 无房间信息时⛔ 不许播"
    assert any("AREA_UNRESOLVED" in r.message and "emit" in r.message for r in caplog.records), \
        "应打 AREA_UNRESOLVED 且 source=emit"


# ── D5 源码钉 ────────────────────────────────────────────────────
def test_d5_source_wiring():
    import inspect

    from autoforge import af_executor, af_runtime

    src_do = inspect.getsource(af_executor.NodeExecutor._speak_for_do)
    assert "entity_id" in src_do and "target" in src_do, "do 播报必须把目标实体传给回调"
    src_room = inspect.getsource(af_runtime.Runtime.resolve_room)
    assert "_load_entity_area_map" in src_room, "resolve_room 必须有实体反查兜底腿"
