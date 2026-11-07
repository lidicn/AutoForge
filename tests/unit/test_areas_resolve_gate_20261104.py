# -*- coding: utf-8 -*-
"""② 判据 · AREA_WORDS 废弃 ＋ `homesdk.areas.resolve` 接入（2026-11-04）· 卡名 AF卡_areas_resolve。

DCD 裁定 `20261104-DB-房间定义统一到HA区域三仓契约-裁定.md` T2/T3-AF：
- 删掉 AF 硬编码的 11 个房间词（含 HA 根本没有的 次卧/儿童房/阳台/玄关/餐厅）；
- 跨仓共享的 `room` 一律 HA `area_id`（pinyin），解析唯一真值在库侧 `homesdk.areas.resolve`；
- `resolve` 返回 None ⇒ **拒投递** ＋ 打 `AREA_UNRESOLVED`（⛔ 静默 accepted）。

D 编号带卡名（群规⑭）：
  D1 源码钉：`af_orchestrator` 不再有硬编码房间词表（`AREA_WORDS = [` 零命中）
  D2 `resolve_room`：中文 room 文本经 area_map 解析到 area_id（书房→shu_fang）
  D3 精确 area_id 直通（shu_fang→shu_fang）
  D4 别名（旧中文名）命中 ⇒ 解析成功（aliases 过渡表）
  D5 解析不到 ⇒ 返回 None（调用方据此拒投递）＋ 打 `AREA_UNRESOLVED`
  D6 空 area_map ⇒ 一律 None（无区域表时不猜房间）
  D7 投递层拦：speak 闭包在解析失败时**不发**（bridge 零调用）；解析成功才发且发的是 area_id
  D8 地图来源：`store_root/.catalog/catalog.json` 的 `area_id`/`area` 现读成表
"""

from __future__ import annotations

import json

import pytest

from autoforge.af_bus import BusEvent
from autoforge.af_ir import Graph, load_automation
from autoforge.af_runtime import build_runtime

MAP = {"shu_fang": "书房", "ke_ting": "客厅"}

AUTO = {
    "ir_version": "0.2.1", "id": "areas_case", "name": "房间解析用例", "version": 1,
    "mode": "restart",
    "nodes": [
        {"id": "a1", "kind": "on", "name": "人",
         "trigger": {"type": "state", "entity_id": "binary_sensor.motion", "to": "on"}},
        # v1.27.1 起播报「显式才出声」⇒ 本卡量的是区域解析，必须给显式 announce 才会发声
        {"id": "d1", "kind": "do", "name": "开灯", "adapter": "light", "action": "turn_on",
         "params": {"entity_id": "light.x"}, "room": "书房", "announce": "把书房的灯打开"},
        {"id": "p1", "kind": "pass", "name": "结束"},
    ],
    "edges": [
        {"from": "a1", "to": "d1", "kind": "then"},
        {"from": "d1", "to": "p1", "kind": "then"},
    ],
}


class _Adapter:
    name = "light"
    dry_run = False

    def call(self, action, params):
        from autoforge.af_adapters.base import CallResult
        return CallResult(success=True, data={}, error=None)


# ── D1 硬编码词表已删 ─────────────────────────────────────────────
def test_d1_no_hardcoded_area_words():
    import inspect

    from autoforge import af_orchestrator

    src = inspect.getsource(af_orchestrator)
    assert "AREA_WORDS = [" not in src, "硬编码房间词表必须删除（DCD T3-AF 硬要求）"
    for w in ("次卧", "儿童房", "阳台", "玄关", "餐厅"):
        assert f'"{w}"' not in src, f"HA 没有的房间名 {w!r} 不得再出现在代码里"


# ── D2/D3/D4/D5/D6 resolve_room ──────────────────────────────────
def _rt(**kw):
    return build_runtime(Graph([load_automation(AUTO)]), **kw)


def test_d2_chinese_name_resolves_to_area_id():
    rt = _rt(area_map=MAP)
    assert rt.resolve_room("书房", source="t") == "shu_fang"


def test_d3_area_id_passthrough():
    rt = _rt(area_map=MAP)
    assert rt.resolve_room("shu_fang", source="t") == "shu_fang"


def test_d4_alias_resolves():
    rt = _rt(area_map=MAP, room_aliases={"凯文房间": "ke_ting"})
    assert rt.resolve_room("凯文房间", source="t") == "ke_ting"


def test_d5_unresolved_returns_none_and_logs(caplog):
    rt = _rt(area_map=MAP)
    with caplog.at_level("WARNING"):
        assert rt.resolve_room("没有这个房间", source="t") is None
    assert any("AREA_UNRESOLVED" in r.message for r in caplog.records), "解析失败必须打 AREA_UNRESOLVED"


def test_d6_empty_map_resolves_nothing():
    rt = _rt()
    assert rt.resolve_room("书房", source="t") is None


# ── D7 投递层拦：成败决定性 ──────────────────────────────────────
class _FakeBridge:
    def __init__(self):
        self.speaks = []

    def publish_speak(self, *, trace_id, text, room="", source="autoforge", priority=1):
        self.speaks.append({"trace_id": trace_id, "text": text, "room": room})
        return {"published": True}


def _fire(rt):
    rt.states.set_state("binary_sensor.motion", "off")
    rt.publish(BusEvent.of("binary_sensor.motion", "on"))


@pytest.fixture()
def _bridge_on(monkeypatch):
    import autoforge.af_mqtt_bridge as bridge_mod

    fake = _FakeBridge()
    monkeypatch.setattr(bridge_mod, "current_bridge", lambda: fake)
    return fake


def test_d7a_unresolved_room_is_not_published(_bridge_on):
    auto = {**AUTO, "nodes": [AUTO["nodes"][0], {**AUTO["nodes"][1], "room": "没有这个房间"}, AUTO["nodes"][2]]}
    rt = build_runtime(Graph([load_automation(auto)]), area_map=MAP)
    rt.adapters.register(_Adapter())
    _fire(rt)
    assert _bridge_on.speaks == [], "解析不到房间 ⇒ ⛔ 不许投递（DCD §2.3 硬判据）"


def test_d7b_resolved_room_publishes_area_id(_bridge_on):
    rt = _rt(area_map=MAP)
    rt.adapters.register(_Adapter())
    _fire(rt)
    assert len(_bridge_on.speaks) == 1, f"解析成功应投递一次，实得 {_bridge_on.speaks}"
    assert _bridge_on.speaks[0]["room"] == "shu_fang", "投递的 room 必须是 HA area_id"


# ── D8 地图来自 catalog 现读 ─────────────────────────────────────
def test_d8_area_map_read_from_catalog(tmp_path):
    store = tmp_path / "store"
    (store / ".catalog").mkdir(parents=True)
    (store / ".catalog" / "catalog.json").write_text(
        json.dumps({"version": 1, "entities": {
            "light.a": {"area_id": "shu_fang", "area": "书房"},
            "light.b": {"area_id": "ke_ting", "area": "客厅"},
            "light.c": {"area_id": "", "area": ""},
        }}, ensure_ascii=False),
        encoding="utf-8",
    )
    rt = _rt(store_root=str(store))
    assert rt.resolve_room("书房", source="t") == "shu_fang"
    assert rt.resolve_room("客厅", source="t") == "ke_ting"
    assert rt.resolve_room("", source="t") is None
