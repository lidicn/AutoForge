"""v1.6.0 实体解析决策智能单测（device 归并 + 集成优选 + 弱信号降权 + 别名沉淀）。"""

from __future__ import annotations

import json

import pytest

from autoforge.af_catalog import DeviceCatalog, _tier_of
from autoforge.af_mcp import dispatch
from autoforge.af_store import GraphStore


def _meta(entity_id: str, **over) -> dict:
    base = {
        "entity_id": entity_id,
        "domain": entity_id.split(".", 1)[0],
        "friendly_name": over.pop("friendly_name", ""),
        "state": over.pop("state", "on"),
        "attributes": {},
        "device_id": over.pop("device_id", ""),
        "platform": over.pop("platform", ""),
        "integration": over.pop("integration", ""),
        "integration_source": "platform",
        "area_id": over.pop("area_id", ""),
        "area": over.pop("area", ""),
        "offline_now": over.pop("offline_now", False),
        "last_changed": over.pop("last_changed", "2026-09-16T01:00:00+00:00"),
    }
    base.update(over)
    return base


#: 同一 device_id 双集成（xiaomi_miot 本地 vs xiaomi_home 云）；玄关灯双路径（一离线一在线）
ENTITIES = {
    "light.study_lamp": _meta(
        "light.study_lamp", friendly_name="书房吊灯", device_id="dev_lamp",
        integration="xiaomi_miot", area_id="a1", area="书房",
    ),
    "switch.study_lamp_cloud": _meta(
        "switch.study_lamp_cloud", friendly_name="书房吊灯", device_id="dev_lamp",
        integration="xiaomi_home", area_id="a1", area="书房",
    ),
    "light.hall": _meta(
        "light.hall", friendly_name="玄关灯", device_id="dev_hall", state="unavailable",
        integration="esphome", area_id="a2", area="玄关", offline_now=True,
    ),
    "light.hall_backup": _meta(
        "light.hall_backup", friendly_name="玄关灯", device_id="dev_hall",
        integration="esphome", area_id="a2", area="玄关", offline_now=False,
    ),
}


def _write_catalog(root, entities: dict) -> None:
    path = root / ".catalog" / "catalog.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": "1", "freshness": "2026-09-16T00:00:00+00:00", "entities": entities}),
        encoding="utf-8",
    )


def test_tier_helper():
    assert _tier_of({"integration": "xiaomi_miot"}) == "local"
    assert _tier_of({"integration": "xiaomi_home"}) == "cloud"
    assert _tier_of({"platform": "template"}) == "polling"
    assert _tier_of({}) == "unknown"


def test_local_integration_preferred_and_device_merged(tmp_path):
    _write_catalog(tmp_path, ENTITIES)
    res = DeviceCatalog(tmp_path).resolve("书房吊灯")
    # 集成优选：本地（xiaomi_miot）排在云（xiaomi_home）之前
    assert res["candidates"][0]["entity_id"] == "light.study_lamp"
    assert res["candidates"][0]["connectivity_tier"] == "local"
    assert res["candidates"][1]["connectivity_tier"] == "cloud"
    # device 归并：同 device_id → 一条设备卡，首条即首选路径
    cards = [d for d in res["devices"] if d["device_id"] == "dev_lamp"]
    assert len(cards) == 1
    assert len(cards[0]["paths"]) == 2
    assert cards[0]["preferred_entity_id"] == "light.study_lamp"
    # entity_id 仍是唯一键（归并只影响展示）
    assert all("entity_id" in c for c in res["candidates"])


def test_offline_demoted_with_advice(tmp_path):
    _write_catalog(tmp_path, ENTITIES)
    res = DeviceCatalog(tmp_path).resolve("玄关灯")
    # 弱信号降权：在线候选排前
    assert res["candidates"][0]["entity_id"] == "light.hall_backup"
    assert res["candidates"][0]["offline_now"] is False
    offline = next(c for c in res["candidates"] if c["entity_id"] == "light.hall")
    assert offline["offline_now"] is True
    assert offline["health_note"]  # 显式标注
    assert "不可用" in res["note"]  # 显式建议优先在线候选


def test_alias_direct_hit(tmp_path):
    _write_catalog(tmp_path, ENTITIES)
    cat = DeviceCatalog(tmp_path)
    # 未沉淀前：无候选
    assert cat.resolve("书房灯")["bucket"] == "none"
    # 沉淀后：直中（high/exact，matched_by=alias）
    out = cat.set_alias("书房灯", "light.study_lamp")
    assert out["ok"] is True and out["total"] == 1
    res = cat.resolve("书房灯")
    assert res["bucket"] == "exact"
    assert res["candidates"][0]["entity_id"] == "light.study_lamp"
    assert res["candidates"][0]["matched_by"] == "alias"
    assert res["candidates"][0]["confidence"] == "high"
    # 列出 + 删除
    assert cat.list_aliases()["aliases"] == {"书房灯": "light.study_lamp"}
    assert cat.remove_alias("书房灯")["ok"] is True
    assert cat.resolve("书房灯")["bucket"] == "none"


def test_set_alias_rejects_unknown_entity(tmp_path):
    _write_catalog(tmp_path, ENTITIES)
    cat = DeviceCatalog(tmp_path)
    assert cat.set_alias("x", "light.nope")["ok"] is False
    assert cat.set_alias("", "light.hall")["ok"] is False


def test_mcp_remember_then_resolve_direct(tmp_path):
    _write_catalog(tmp_path, ENTITIES)
    store = GraphStore(str(tmp_path))
    content, is_error = dispatch(
        "af_remember_entity",
        {"name": "书房灯", "entity_id": "light.study_lamp"},
        store,
        {"subject": "alice", "scopes": ["write"]},
    )
    assert is_error is False
    assert json.loads(content[0]["text"])["ok"] is True

    content, is_error = dispatch("af_resolve_entity", {"name": "书房灯"}, store, None)
    body = json.loads(content[0]["text"])
    assert body["candidates"][0]["matched_by"] == "alias"
    assert body["devices"]


def test_mcp_remember_needs_write_scope(tmp_path):
    _write_catalog(tmp_path, ENTITIES)
    store = GraphStore(str(tmp_path))
    _content, is_error = dispatch(
        "af_remember_entity",
        {"name": "书房灯", "entity_id": "light.study_lamp"},
        store,
        {"subject": "ro", "scopes": ["read"]},
    )
    assert is_error is True


def test_http_alias_endpoints(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from autoforge.af_api import build_app

    _write_catalog(tmp_path, ENTITIES)
    client = TestClient(build_app(str(tmp_path)))

    assert client.get("/api/catalog/aliases").json()["ok"] is True
    resp = client.post("/api/catalog/alias", json={"name": "书房灯", "entity_id": "light.study_lamp"})
    assert resp.status_code == 200 and resp.json()["ok"] is True
    assert client.get("/api/catalog/aliases").json()["aliases"]["书房灯"] == "light.study_lamp"
    resp = client.post("/api/catalog/alias/remove", json={"name": "书房灯"})
    assert resp.json()["ok"] is True
    assert client.get("/api/catalog/aliases").json()["aliases"] == {}


def test_cli_alias_commands(tmp_path):
    from typer.testing import CliRunner

    from autoforge.af_cli import app

    _write_catalog(tmp_path, ENTITIES)
    runner = CliRunner()
    assert runner.invoke(
        app, ["entities", "remember", "书房灯", "light.study_lamp", "--root", str(tmp_path)]
    ).exit_code == 0
    listed = runner.invoke(app, ["entities", "aliases", "--root", str(tmp_path)])
    assert listed.exit_code == 0 and "书房灯" in listed.output
    assert runner.invoke(app, ["entities", "forget", "书房灯", "--root", str(tmp_path)]).exit_code == 0
