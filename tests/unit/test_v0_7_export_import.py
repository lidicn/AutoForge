"""v0.7.0 模板导出与备份恢复单测。

覆盖：
1. 导出 → 清空 → 导入，raw 级一致（多版本 + tags + 校验和）；
2. 校验和防损坏（篡改 bundle 被拒）；
3. 导入前 IR Schema 校验（非法版本记入 errors，不落地）；
4. 冲突策略 skip / overwrite / rename；
5. CLI `forge store export` / `import`；
6. API `GET /api/store/export` + `POST /api/store/import`。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from autoforge.af_cli import app
from autoforge.af_ir import load_graph
from autoforge.af_store import GraphStore, _bundle_checksum
from autoforge.af_api import build_app

runner = CliRunner()


def _demo_raw(target: str, version_note: str = "") -> dict:
    return {
        "ir_version": "0.2.1",
        "id": "demo",
        "name": "demo",
        "version": 1,
        "mode": "single",
        "nodes": [
            {"id": "o", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
            {"id": "d", "kind": "do", "adapter": "mock", "action": "light.turn_on", "params": {"entity_id": target}},
            {"id": "p", "kind": "pass"},
        ],
        "edges": [
            {"from": "o", "to": "d", "kind": "then"},
            {"from": "d", "to": "p", "kind": "then"},
        ],
    }


def _build_store(root, *, tags=None) -> GraphStore:
    store = GraphStore(root)
    store.save(load_graph(_demo_raw("light.a")), "demo")
    store.save(load_graph(_demo_raw("light.b")), "demo", note="v2")
    if tags:
        store.set_tags("demo", tags)
    return store


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


# ─────────────────────────────────────────────────────────────────────
# 1. 导出 → 清空 → 导入，raw 级一致
# ─────────────────────────────────────────────────────────────────────


def test_export_import_roundtrip_raw_consistent(tmp_path):
    src = _build_store(tmp_path / "src", tags=["lighting", "daily"])
    bundle = src.export_bundle()
    assert bundle["format"] == "autoforge-bundle"
    assert bundle["entries"][0]["name"] == "demo"
    assert len(bundle["entries"][0]["versions"]) == 2  # 两个版本

    dst = GraphStore(tmp_path / "dst")  # 空 store
    report = dst.import_bundle(bundle, "skip")
    assert report["imported"] == ["demo"]
    assert report["errors"] == []

    # 名字 / 版本 / tags 一致
    assert dst.names() == src.names()
    assert dst.versions("demo") == [1, 2]
    assert dst.get_tags("demo") == ["daily", "lighting"]

    # raw 级一致：逐版本比对 graph dict
    for name in src.names():
        for v in src.versions(name):
            before = src.load_record(name, v)["graph"]
            after = dst.load_record(name, v)["graph"]
            assert _canon(after) == _canon(before), f"{name} v{v} raw 不一致"


# ─────────────────────────────────────────────────────────────────────
# 2. 校验和防损坏
# ─────────────────────────────────────────────────────────────────────


def test_checksum_detects_tamper(tmp_path):
    src = _build_store(tmp_path / "src")
    bundle = src.export_bundle()
    # 篡改 graph（导出形态为 {"automations":[...]} 容器）但不重算 checksum
    bundle["entries"][0]["versions"][0]["graph"]["automations"][0]["nodes"][1]["params"]["entity_id"] = "EVIL"
    dst = GraphStore(tmp_path / "dst")
    with pytest.raises(ValueError):
        dst.import_bundle(bundle, "skip")


def test_checksum_valid_passes(tmp_path):
    src = _build_store(tmp_path / "src")
    bundle = src.export_bundle()
    dst = GraphStore(tmp_path / "dst")
    # 不抛异常即通过
    dst.import_bundle(bundle, "skip")


# ─────────────────────────────────────────────────────────────────────
# 3. 导入前 IR Schema 校验
# ─────────────────────────────────────────────────────────────────────


def test_import_rejects_invalid_version(tmp_path):
    src = _build_store(tmp_path / "src")
    bundle = src.export_bundle()
    # 注入一条非法版本（缺少 nodes，违反 schema）
    bad = {
        "name": "demo",
        "version": 1,
        "saved_at": "2026-01-01T00:00:00+00:00",
        "note": "bad",
        "graph": {"id": "bad", "name": "bad"},  # 无 nodes → 非法
    }
    bundle["entries"][0]["versions"].append(bad)
    bundle["checksum"] = _bundle_checksum(bundle)
    dst = GraphStore(tmp_path / "dst")
    report = dst.import_bundle(bundle, "skip")
    assert any(e["name"] == "demo" for e in report["errors"])
    # 非法版本未落地：demo 仍只有 2 个合法版本
    assert dst.versions("demo") == [1, 2]


def test_import_rejects_non_bundle(tmp_path):
    dst = GraphStore(tmp_path / "dst")
    with pytest.raises(ValueError):
        dst.import_bundle({"foo": "bar"}, "skip")


# ─────────────────────────────────────────────────────────────────────
# 4. 冲突策略
# ─────────────────────────────────────────────────────────────────────


def test_conflict_skip_leaves_existing(tmp_path):
    a = _build_store(tmp_path / "a")
    bundle = a.export_bundle()
    # 目标已有同名归档
    b = _build_store(tmp_path / "b")
    report = b.import_bundle(bundle, "skip")
    assert report["skipped"] == ["demo"]
    assert report["imported"] == []
    # 目标原有版本未被覆盖（仍是 b 自己的 2 个版本，内容来自 b 的 light.a/light.b 是相同 IR，
    # 但 import 没有新增版本）
    assert b.versions("demo") == [1, 2]


def test_conflict_overwrite_replaces(tmp_path):
    a = _build_store(tmp_path / "a", tags=["daily", "lighting"])
    bundle = a.export_bundle()
    b = _build_store(tmp_path / "b")
    report = b.import_bundle(bundle, "overwrite")
    assert report["imported"] == ["demo"]
    assert report["skipped"] == []
    # overwrite 先删除再导入 → 版本重置为 1..2（与源一致）
    assert b.versions("demo") == [1, 2]
    assert b.get_tags("demo") == ["daily", "lighting"]


def test_conflict_rename_imports_under_new_name(tmp_path):
    a = _build_store(tmp_path / "a")
    bundle = a.export_bundle()
    b = _build_store(tmp_path / "b")
    report = b.import_bundle(bundle, "rename")
    assert report["renamed"] == {"demo": "demo_import"}
    assert report["imported"] == ["demo_import"]
    assert "demo" in b.names()
    assert "demo_import" in b.names()
    assert b.versions("demo_import") == [1, 2]


# ─────────────────────────────────────────────────────────────────────
# 5. CLI export / import
# ─────────────────────────────────────────────────────────────────────


def test_cli_export_import(tmp_path):
    src_root = str(tmp_path / "src")
    _build_store(src_root, tags=["cli"])
    out = tmp_path / "bundle.json"

    res = runner.invoke(app, ["store", "export", "--root", src_root, "--out", str(out)])
    assert res.exit_code == 0, res.output
    assert out.is_file()

    dst_root = str(tmp_path / "dst")
    res2 = runner.invoke(app, ["store", "import", str(out), "--root", dst_root, "--strategy", "skip"])
    assert res2.exit_code == 0, res2.output
    assert "imported=" in res2.output

    dst = GraphStore(dst_root)
    assert dst.names() == ["demo"]
    assert dst.get_tags("demo") == ["cli"]


def test_cli_import_bad_bundle(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    res = runner.invoke(app, ["store", "import", str(bad), "--root", str(tmp_path / "x")])
    assert res.exit_code != 0


# ─────────────────────────────────────────────────────────────────────
# 6. API export / import
# ─────────────────────────────────────────────────────────────────────


def test_api_export_import(tmp_path):
    src_root = str(tmp_path / "src")
    _build_store(src_root, tags=["api"])
    app1 = build_app(src_root)
    client = TestClient(app1)

    r = client.get("/api/store/export")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["format"] == "autoforge-bundle"
    bundle = body["bundle"]

    # 导入到另一个空 store
    dst_root = str(tmp_path / "dst")
    app2 = build_app(dst_root)
    client2 = TestClient(app2)
    r2 = client2.post("/api/store/import", json={"bundle": bundle, "strategy": "skip"})
    assert r2.status_code == 200, r2.text
    rep = r2.json()
    assert rep["imported"] == ["demo"]
    assert rep["errors"] == []

    # 校验导入结果 raw 一致
    dst = GraphStore(dst_root)
    assert dst.versions("demo") == [1, 2]
    assert dst.get_tags("demo") == ["api"]
