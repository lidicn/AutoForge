"""v0.6.0 标签体系 + 批量启停 单测。

覆盖：
- IR 模型 `enabled` 字段（默认 / 显式 false / round-trip）
- 调度器对禁用自动化的静默跳过（非拒绝）
- GraphStore 标签元数据（set/get/all_tags）
- 服务层：按标签筛选 + 批量启停翻转并落新版本
- API 端点：GET /api/graphs?tag=、POST /api/graphs/tags、enable/disable
"""

from __future__ import annotations

from autoforge.af_ir import Graph, load_automation
from autoforge.af_runtime import build_runtime
from autoforge.af_service import enable_by_tag, graphs_by_tag, list_graphs, set_graph_tags
from autoforge.af_store import GraphStore

import pytest

ON_M = {"id": "a1", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}}
P1 = {"id": "p1", "kind": "pass"}


def _ir(enabled=True, **over):
    data = {
        "ir_version": "0.2.1",
        "id": "demo",
        "name": "示例",
        "version": 1,
        "mode": "single",
        "enabled": enabled,
        "nodes": [ON_M, P1],
        "edges": [{"from": "a1", "to": "p1", "kind": "then"}],
    }
    data.update(over)
    return data


def _rt(enabled=True, **kw):
    return build_runtime(Graph([load_automation(_ir(enabled=enabled))]), **kw)


def _graph(enabled=True):
    return Graph([load_automation(_ir(enabled=enabled))])


# ── IR 模型：enabled 字段 ────────────────────────────────────────────────


def test_automation_enabled_default():
    assert load_automation(_ir()).enabled is True


def test_automation_enabled_false_roundtrip():
    auto = load_automation(_ir(enabled=False))
    assert auto.enabled is False
    assert auto.raw.get("enabled") is False
    # 重新序列化后仍带 enabled=False（保证 store 保存后 round-trip 一致）
    assert load_automation(auto.raw).enabled is False


# ── 调度器：跳过禁用自动化（静默，非拒绝）────────────────────────────────


def test_scheduler_skips_disabled():
    runtime = _rt(enabled=False)
    runtime.emit("binary_sensor.m", "on", last_changed="t1")
    assert runtime.instances.count_active() == 0
    assert runtime.scheduler.rejections == []


def test_scheduler_runs_enabled():
    runtime = _rt(enabled=True)
    runtime.emit("binary_sensor.m", "on", last_changed="t1")
    # enabled 自动化应被触发（pass 节点同步完成，故看实例总数 / 无拒绝）
    assert len(runtime.instances.all()) >= 1
    assert runtime.scheduler.rejections == []


# ── GraphStore 标签元数据 ───────────────────────────────────────────────


def test_graphstore_tags(tmp_path):
    store = GraphStore(str(tmp_path / "store"))
    store.set_tags("g1", ["lighting", "night"])
    assert store.get_tags("g1") == ["lighting", "night"]
    store.set_tags("g1", ["lighting"])  # 覆盖式
    assert store.get_tags("g1") == ["lighting"]
    store.set_tags("g1", [])  # 清空
    assert store.get_tags("g1") == []
    assert store.all_tags() == {}


# ── 服务层：标签筛选 + 批量启停 ─────────────────────────────────────────


def test_service_tags_and_enable_by_tag(tmp_path):
    store = GraphStore(str(tmp_path / "store"))
    store.save(_graph(enabled=True), "g1", tags=["lighting"])
    store.save(_graph(enabled=True), "g2", tags=["other"])

    # 按标签筛选
    assert {i["name"] for i in graphs_by_tag(store, "lighting")["items"]} == {"g1"}
    assert {i["name"] for i in graphs_by_tag(store, "nope")["items"]} == set()

    # list_graphs 含 tags 字段
    items = {i["name"]: i for i in list_graphs(store)["items"]}
    assert items["g1"]["tags"] == ["lighting"]

    # 批量禁用 lighting 标签 → 落新版本，g1 自动化全部禁用
    res = enable_by_tag(store, "lighting", False)
    assert res["enabled"] is False
    assert res["affected"][0]["name"] == "g1"
    ver = store.latest("g1")
    assert all(not a.enabled for a in store.load("g1", ver))

    # g2 不受影响
    assert all(a.enabled for a in store.load("g2", store.latest("g2")))

    # 批量启用回来
    enable_by_tag(store, "lighting", True)
    assert all(a.enabled for a in store.load("g1", store.latest("g1")))

    # set_graph_tags 覆盖式设置
    assert set_graph_tags(store, "g1", ["x", "y"])["tags"] == ["x", "y"]
    assert store.get_tags("g1") == ["x", "y"]


# ── API 端点 ────────────────────────────────────────────────────────────

try:
    from fastapi.testclient import TestClient  # noqa: E402

    from autoforge.af_api import build_app  # noqa: E402

    _HAVE_FASTAPI = True
except ImportError:
    _HAVE_FASTAPI = False


def _client(tmp_path):
    store = GraphStore(str(tmp_path / "store"))
    store.save(_graph(enabled=True), "g1", tags=["lighting"])
    store.save(_graph(enabled=True), "g2", tags=["other"])
    return TestClient(build_app(str(tmp_path / "store"))), store


@pytest.mark.skipif(not _HAVE_FASTAPI, reason="fastapi not installed")
def test_api_graphs_tag_filter(tmp_path):
    client, _ = _client(tmp_path)
    items = client.get("/api/graphs?tag=lighting").json()["items"]
    assert {i["name"] for i in items} == {"g1"}
    assert {i["name"] for i in client.get("/api/graphs").json()["items"]} == {"g1", "g2"}


@pytest.mark.skipif(not _HAVE_FASTAPI, reason="fastapi not installed")
def test_api_graphs_tags_set(tmp_path):
    client, store = _client(tmp_path)
    body = client.post("/api/graphs/tags", json={"name": "g1", "tags": ["a", "b"]}).json()
    assert body["ok"] is True
    assert store.get_tags("g1") == ["a", "b"]


@pytest.mark.skipif(not _HAVE_FASTAPI, reason="fastapi not installed")
def test_api_graphs_enable_disable_by_tag(tmp_path):
    client, store = _client(tmp_path)
    client.post("/api/graphs/disable", json={"tag": "lighting"})
    ver = store.latest("g1")
    assert all(not a.enabled for a in store.load("g1", ver))
    client.post("/api/graphs/enable", json={"tag": "lighting"})
    assert all(a.enabled for a in store.load("g1", store.latest("g1")))
