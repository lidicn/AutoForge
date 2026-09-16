"""v1.6.0 P2 联动验证闸单测：离线设备被验证标记（**防假绿**）。"""

from __future__ import annotations

import json

from autoforge import af_service as svc
from autoforge.af_ir import load_graph
from autoforge.af_scanner import StaticScanner
from autoforge.af_store import GraphStore


def _meta(entity_id: str, *, state: str = "on", offline: bool = False) -> dict:
    return {
        "entity_id": entity_id,
        "domain": entity_id.split(".", 1)[0],
        "friendly_name": "测试设备",
        "state": state,
        "attributes": {},
        "device_id": "d1",
        "platform": "esphome",
        "integration": "esphome",
        "integration_source": "platform",
        "area_id": "a1",
        "area": "书房",
        "offline_now": offline,
        "last_changed": "2026-09-16T01:00:00+00:00",
    }


ENTITIES = {
    "light.online_lamp": _meta("light.online_lamp"),
    "light.dead_lamp": _meta("light.dead_lamp", state="unavailable", offline=True),
}


def _write_catalog(root, entities: dict) -> None:
    path = root / ".catalog" / "catalog.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": "1", "freshness": "", "entities": entities}), encoding="utf-8"
    )


def _ir(target: str) -> dict:
    return {
        "ir_version": "0.2.1", "id": "vg", "name": "vg", "version": 1, "mode": "single",
        "nodes": [
            {"id": "a", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
            {"id": "d", "kind": "do", "adapter": "ha", "action": "light.turn_on", "params": {"entity_id": target}},
            {"id": "p", "kind": "pass"},
        ],
        "edges": [
            {"from": "a", "to": "d", "kind": "then"},
            {"from": "d", "to": "p", "kind": "then"},
        ],
    }


def test_offline_entity_is_flagged(tmp_path):
    """引用「此刻不可用」实体 → 显式告警（防假绿），但**不拦截**。"""
    _write_catalog(tmp_path, ENTITIES)
    res = svc.build(_ir("light.dead_lamp"), store=GraphStore(str(tmp_path)))
    codes = {w["code"] for w in res["warnings"]}
    assert "ENTITY_OFFLINE_NOW" in codes
    assert res["ok"] is True  # 只告警不拦截（设备可能只是临时离线）
    flagged = [w for w in res["warnings"] if w["code"] == "ENTITY_OFFLINE_NOW"]
    assert flagged and flagged[0]["hint"]


def test_online_entity_not_flagged(tmp_path):
    _write_catalog(tmp_path, ENTITIES)
    res = svc.build(_ir("light.online_lamp"), store=GraphStore(str(tmp_path)))
    assert "ENTITY_OFFLINE_NOW" not in {w["code"] for w in res["warnings"]}


def test_no_catalog_no_false_positive(tmp_path):
    """离线编写 IR（无目录）→ 零误报。"""
    res = svc.build(_ir("light.dead_lamp"), store=GraphStore(str(tmp_path)))
    assert "ENTITY_OFFLINE_NOW" not in {w["code"] for w in res["warnings"]}


def test_scanner_direct_entity_health():
    scan = StaticScanner(
        graph=load_graph(_ir("light.dead_lamp")),
        entity_health={"light.dead_lamp": {"offline_now": True, "connectivity_tier": "local"}},
    ).scan()
    assert "ENTITY_OFFLINE_NOW" in scan.codes()
    assert scan.ok is True  # warning 不拦截
    assert scan.errors == []


def test_save_graph_surfaces_warning(tmp_path):
    """save_graph 同样接入健康视图（归档前就能看到假绿风险）。"""
    _write_catalog(tmp_path, ENTITIES)
    store = GraphStore(str(tmp_path))
    res = svc.save_graph(store, _ir("light.dead_lamp"), "vg_demo")
    assert res["ok"] is True
    assert any(w["code"] == "ENTITY_OFFLINE_NOW" for w in res["warnings"])
