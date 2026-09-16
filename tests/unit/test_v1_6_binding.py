"""v1.6.0（决策 3）可选 binding 单测：设备描述占位符 → 真实 entity_id（fail-closed）。"""

from __future__ import annotations

import json

import pytest

from autoforge import af_service as svc
from autoforge.af_store import GraphStore


def _meta(entity_id: str, friendly_name: str, integration: str) -> dict:
    return {
        "entity_id": entity_id,
        "domain": entity_id.split(".", 1)[0],
        "friendly_name": friendly_name,
        "state": "on",
        "attributes": {},
        "device_id": "d1",
        "platform": integration,
        "integration": integration,
        "integration_source": "platform",
        "area_id": "a1",
        "area": "书房",
        "offline_now": False,
        "last_changed": "2026-09-16T01:00:00+00:00",
    }


ENTITIES = {
    "light.study_lamp": _meta("light.study_lamp", "书房吊灯", "xiaomi_miot"),
    "switch.study_lamp_cloud": _meta("switch.study_lamp_cloud", "书房吊灯", "xiaomi_home"),
    "light.hall": _meta("light.hall", "玄关灯", "esphome"),
}


def _write_catalog(root, entities: dict) -> None:
    path = root / ".catalog" / "catalog.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": "1", "freshness": "", "entities": entities}), encoding="utf-8"
    )


def _ir(target: str) -> dict:
    return {
        "ir_version": "0.2.1", "id": "b", "name": "b", "version": 1, "mode": "single",
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


def _bound_value(out: dict) -> str:
    return out["ir"]["nodes"][1]["params"]["entity_id"]


def test_bind_unique_placeholder(tmp_path):
    """唯一候选 → 回填真实 entity_id。"""
    _write_catalog(tmp_path, ENTITIES)
    out = svc.bind_ir(GraphStore(str(tmp_path)), _ir("?玄关灯"))
    assert out["ok"] is True
    assert out["bound"][0]["to"] == "light.hall"
    assert _bound_value(out) == "light.hall"


def test_bind_with_domain_narrowing(tmp_path):
    """`?light:书房吊灯` 收窄域 → 命中 light 那条。"""
    _write_catalog(tmp_path, ENTITIES)
    out = svc.bind_ir(GraphStore(str(tmp_path)), _ir("?light:书房吊灯"))
    assert out["ok"] is True
    assert _bound_value(out) == "light.study_lamp"


def test_bind_ambiguous_is_not_bound(tmp_path):
    """歧义（多候选且无 high）→ **不回填**（fail-closed），保留占位符交由安全闸拒编译。"""
    _write_catalog(tmp_path, ENTITIES)
    out = svc.bind_ir(GraphStore(str(tmp_path)), _ir("?吊灯"))
    assert out["ok"] is False
    assert out["bound"] == []
    assert out["unresolved"] and "歧义" in out["unresolved"][0]["reason"]
    assert _bound_value(out) == "?吊灯"  # 占位符原样保留


def test_bind_unknown_placeholder(tmp_path):
    """无候选 → 不回填。"""
    _write_catalog(tmp_path, ENTITIES)
    out = svc.bind_ir(GraphStore(str(tmp_path)), _ir("?不存在的设备"))
    assert out["ok"] is False and out["bound"] == []


def test_bind_noop_without_placeholders(tmp_path):
    """IR 里没有占位符 → 零改动（可选 binding 不影响既有 IR）。"""
    _write_catalog(tmp_path, ENTITIES)
    out = svc.bind_ir(GraphStore(str(tmp_path)), _ir("light.hall"))
    assert out["ok"] is True and out["total"] == 0
    assert _bound_value(out) == "light.hall"


def test_cli_build_bind(tmp_path):
    from typer.testing import CliRunner

    from autoforge.af_cli import app

    _write_catalog(tmp_path, ENTITIES)
    ir_path = tmp_path / "ir.json"
    ir_path.write_text(json.dumps(_ir("?玄关灯")), encoding="utf-8")
    result = CliRunner().invoke(
        app, ["build", str(ir_path), "--bind", "--root", str(tmp_path), "--no-nl"]
    )
    assert result.exit_code == 0, result.output
    assert "已绑定" in result.output


def test_http_bind_endpoint(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from autoforge.af_api import build_app

    _write_catalog(tmp_path, ENTITIES)
    client = TestClient(build_app(str(tmp_path)))
    resp = client.post("/api/bind", json={"ir": _ir("?玄关灯")})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True and body["bound"][0]["to"] == "light.hall"
