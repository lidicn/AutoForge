"""v1.1.0 实体事实内建（设备目录 + 解析）单测。

覆盖：
1. `refresh` 落盘 + 摘要（按域统计 / 区域 / freshness）；
2. `resolve` 四级匹配：entity_id 精确 / friendly_name 精确 / 子串 / entity_id 子串；
3. **不过滤域**：同名设备对应 light/switch 时全部返回；
4. **entity_id 形态不模糊扫描**（防 DoS + 防误配）；
5. `resolve_best` 绝不静默猜域：唯一候选/high 才采纳，有歧义返回 None；
6. `list_entities` 强制分页 + 透明截断回报 + limit 上限；
7. `get_state` 实时优先 + 缓存兜底（source 标注）；
8. `af_affordance`：possible_states 含 unavailable/unknown、高危域标记；
9. `af_build` 的 `known_entities` 缺省接目录（空目录静默退化）；
10. MCP 4 工具 + HTTP 4 端点 + CLI。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from autoforge import af_service as svc
from autoforge.af_affordance import GLOBAL_STATES, affordance_for, possible_states
from autoforge.af_api import build_app
from autoforge.af_catalog import MAX_LIST_LIMIT, DeviceCatalog
from autoforge.af_cli import app
from autoforge.af_ir import load_graph
from autoforge.af_mcp import dispatch
from autoforge.af_store import GraphStore

runner = CliRunner()


@pytest.fixture(autouse=True)
def _unreachable_ha(monkeypatch):
    """所有用例都把 HA 指向必然拒绝连接的本机端口 + 极小超时，确保测试不触真实 HA、且有界失败。"""
    monkeypatch.setenv("AUTOFORGE_HA_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("AUTOFORGE_HA_TIMEOUT", "0.3")
    monkeypatch.delenv("AUTOFORGE_HA_TOKEN", raising=False)


#: 测试用「HA 全量状态」假数据：同名设备故意跨域（书房吊灯 → light + switch）
FAKE_STATES: dict[str, tuple[str, dict]] = {
    "light.study_lamp": ("off", {"friendly_name": "书房吊灯", "area": "书房"}),
    "switch.study_lamp_switch": ("on", {"friendly_name": "书房吊灯开关", "area": "书房"}),
    "light.living_main": ("on", {"friendly_name": "客厅主灯", "area": "客厅"}),
    "climate.study_ac": ("cool", {"friendly_name": "书房空调", "area": "书房"}),
    "lock.front_door": ("locked", {"friendly_name": "入户门锁", "area": "玄关"}),
}


def _seed_catalog(root, states: dict | None = None) -> DeviceCatalog:
    """用注入的假数据源预置目录（落盘，供 svc / MCP / API / CLI 读取）。"""
    catalog = DeviceCatalog(root, fetch_all=lambda: dict(states or FAKE_STATES))
    result = catalog.refresh()
    assert result["ok"], result
    return catalog


# ─────────────────────────────────────────────────────────────────────
# 1. refresh + snapshot
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_refresh_persists_and_summarizes(tmp_path):
    catalog = _seed_catalog(tmp_path)
    assert catalog.catalog_path.exists()

    snap = catalog.snapshot()
    assert snap["ok"] is True
    assert snap["total_entities"] == 5
    assert snap["by_domain"]["light"] == 2
    assert snap["areas"] == ["书房", "客厅", "玄关"]
    assert snap["freshness"]

    # 二次全量刷新：内容不变 → added/changed 均为 0
    again = catalog.refresh()
    assert again["ok"] and again["added"] == 0 and again["changed"] == 0


@pytest.mark.integration
def test_refresh_removed_counted_on_full(tmp_path):
    catalog = _seed_catalog(tmp_path)
    # 第二次只用更小的集合 → 应报告 removed
    smaller = {"light.study_lamp": ("off", {"friendly_name": "书房吊灯", "area": "书房"})}
    catalog2 = DeviceCatalog(tmp_path, fetch_all=lambda: dict(smaller))
    result = catalog2.refresh(full=True)
    assert result["total"] == 1 and result["removed"] == 4


@pytest.mark.integration
def test_refresh_narrow_keeps_unmatched(tmp_path):
    catalog = _seed_catalog(tmp_path)
    # 只刷 study 域的 climate：未匹配的旧条目应保留（不把全屋清空）
    narrow = {"climate.study_ac": ("cool", {"friendly_name": "书房空调", "area": "书房"})}
    catalog2 = DeviceCatalog(tmp_path, fetch_all=lambda: dict(narrow))
    result = catalog2.refresh(full=True, domain="climate")
    assert result["total"] == 5  # 4 个旧 light/switch/lock + 本次 climate
    assert catalog.snapshot()["by_domain"]["light"] == 2


@pytest.mark.integration
def test_refresh_unreachable_ha_reports_error(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOFORGE_HA_URL", "http://127.0.0.1:1")
    out = DeviceCatalog(tmp_path).refresh()
    assert out["ok"] is False
    assert "无法连接 HA" in out["error"]


# ─────────────────────────────────────────────────────────────────────
# 2/3/4. resolve
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_resolve_friendly_name_exact_and_no_domain_filter(tmp_path):
    catalog = _seed_catalog(tmp_path)
    result = catalog.resolve("书房吊灯")
    assert result["ok"] and result["count"] == 2
    ids = [c["entity_id"] for c in result["candidates"]]
    # 不过滤域：light 与 switch 都被返回
    assert set(ids) == {"light.study_lamp", "switch.study_lamp_switch"}
    # 精确命中排最前
    assert result["candidates"][0]["entity_id"] == "light.study_lamp"
    assert result["candidates"][0]["confidence"] == "high"
    assert result["candidates"][0]["matched_by"] == "friendly_name_exact"
    # domain 收窄
    only_light = catalog.resolve("书房吊灯", domain="light")
    assert only_light["count"] == 1


@pytest.mark.integration
def test_resolve_substring_and_entity_id_fallback(tmp_path):
    catalog = _seed_catalog(tmp_path)
    sub = catalog.resolve("吊灯")
    assert sub["count"] == 2
    assert all(c["confidence"] == "medium" for c in sub["candidates"])

    low = catalog.resolve("study_ac")
    assert low["count"] == 1
    assert low["candidates"][0]["matched_by"] == "entity_id_substr"
    assert low["candidates"][0]["confidence"] == "low"


@pytest.mark.integration
def test_resolve_entity_id_shape_never_fuzzy_scans(tmp_path):
    """entity_id 形态只做精确命中：不在目录即 0 候选（不模糊扫描，防 DoS + 防误配）。"""
    catalog = _seed_catalog(tmp_path)
    hit = catalog.resolve("light.study_lamp")
    assert hit["count"] == 1 and hit["candidates"][0]["matched_by"] == "entity_id_exact"

    miss = catalog.resolve("light.nonexistent")
    assert miss["ok"] is True and miss["count"] == 0
    assert "不在目录" in miss["note"]


@pytest.mark.integration
def test_resolve_area_hint_not_hard_filter(tmp_path):
    catalog = _seed_catalog(tmp_path)
    # 区域解析成功 → 收窄
    scoped = catalog.resolve("灯", area="书房")
    assert {c["entity_id"] for c in scoped["candidates"]} == {"light.study_lamp", "switch.study_lamp_switch"}
    # 区域不存在 → 放宽全局 + 显式告警（不静默返回空）
    relaxed = catalog.resolve("灯", area="不存在的房间")
    assert relaxed["count"] >= 2 and relaxed["area_warning"]


def test_resolve_empty_catalog_reports_error(tmp_path):
    out = DeviceCatalog(tmp_path).resolve("书房吊灯")
    assert out["ok"] is False and "device_catalog 为空" in out["error"]


# ─────────────────────────────────────────────────────────────────────
# 5. resolve_best —— 绝不静默猜域
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_resolve_best_only_accepts_unambiguous(tmp_path):
    catalog = _seed_catalog(tmp_path)
    # 唯一候选 → 采纳
    assert catalog.resolve_best("客厅主灯") == "light.living_main"
    # top 置信度 high → 采纳
    assert catalog.resolve_best("书房吊灯") == "light.study_lamp"
    # 本体已是 entity_id → 返回自身
    assert catalog.resolve_best("climate.study_ac") == "climate.study_ac"
    # 多候选且无 high → None（逼 agent 显式选）
    assert catalog.resolve_best("吊灯") is None
    # 无候选 → None
    assert catalog.resolve_best("不存在的设备") is None


# ─────────────────────────────────────────────────────────────────────
# 6. list_entities —— 强制分页 + 透明截断
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_list_entities_filters_and_reports(tmp_path):
    catalog = _seed_catalog(tmp_path)

    lights = catalog.list_entities(domain="light")
    assert lights["matched_count"] == 2 and lights["truncated"] is False

    room = catalog.list_entities(area="书房")
    assert room["matched_count"] == 3

    kw = catalog.list_entities(keyword="吊灯")
    assert {e["entity_id"] for e in kw["entities"]} == {"light.study_lamp", "switch.study_lamp_switch"}


@pytest.mark.integration
def test_list_entities_pagination_is_transparent(tmp_path):
    catalog = _seed_catalog(tmp_path)
    page = catalog.list_entities(limit=2, offset=0)
    assert page["returned"] == 2 and page["truncated"] is True and page["next_offset"] == 2
    page2 = catalog.list_entities(limit=2, offset=page["next_offset"])
    assert page2["offset"] == 2
    assert {e["entity_id"] for e in page["entities"]}.isdisjoint({e["entity_id"] for e in page2["entities"]})


@pytest.mark.integration
def test_list_entities_limit_capped(tmp_path):
    catalog = _seed_catalog(tmp_path)
    out = catalog.list_entities(limit=99999)
    assert out["returned"] <= MAX_LIST_LIMIT


def test_list_entities_empty_catalog(tmp_path):
    out = DeviceCatalog(tmp_path).list_entities()
    assert out["ok"] is False and out["truncated"] is False


# ─────────────────────────────────────────────────────────────────────
# 7. get_state —— 实时优先 + 缓存兜底
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_get_state_prefers_live(tmp_path):
    catalog = _seed_catalog(tmp_path)
    live = DeviceCatalog(tmp_path, fetch_all=lambda: {}, fetch_one=lambda eid: "on")
    out = live.get_state("light.study_lamp")
    assert out["ok"] and out["source"] == "live" and out["state"] == "on"


@pytest.mark.integration
def test_get_state_falls_back_to_cache(tmp_path):
    _seed_catalog(tmp_path)
    offline = DeviceCatalog(tmp_path, fetch_one=lambda eid: None)
    out = offline.get_state("light.study_lamp")
    assert out["ok"] and out["source"] == "catalog_cache"
    assert out["state"] == "off"  # 来自缓存
    assert "可能不是最新" in out["note"]


@pytest.mark.integration
def test_get_state_unknown_entity(tmp_path):
    _seed_catalog(tmp_path)
    offline = DeviceCatalog(tmp_path, fetch_one=lambda eid: None)
    out = offline.get_state("light.never_seen")
    assert out["ok"] is False and "不在目录缓存中" in out["error"]


# ─────────────────────────────────────────────────────────────────────
# 8. affordance
# ─────────────────────────────────────────────────────────────────────


def test_affordance_merges_global_states():
    light = affordance_for("light")
    for g in GLOBAL_STATES:
        assert g in light["states"]
    assert "turn_on" in light["services"]
    assert light["high_risk"] is False

    lock = affordance_for("lock")
    assert lock["high_risk"] is True  # 高危域
    assert "jammed" in lock["states"]  # 域特有状态保留

    # 未登记的域仍返回全域隐含状态（不抛）
    unknown = affordance_for("weird_domain")
    assert unknown["states"] == list(possible_states("weird_domain"))


# ─────────────────────────────────────────────────────────────────────
# 9. af_build 缺省接目录（实体存在性校验默认开启）
# ─────────────────────────────────────────────────────────────────────


def _raw(target: str) -> dict:
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


@pytest.mark.integration
def test_build_uses_catalog_by_default(tmp_path):
    _seed_catalog(tmp_path)
    store = GraphStore(tmp_path)
    # 目录里有 light.study_lamp → 但触发实体 binary_sensor.m 不在目录里 → 报 ENTITY_NOT_FOUND
    bad = svc.build(_raw("light.study_lamp"), store=store, use_catalog=True)
    assert bad["entity_check"] == "catalog"
    assert "ENTITY_NOT_FOUND" in {d["code"] for d in bad["errors"]}


def test_build_degrades_when_catalog_empty(tmp_path):
    store = GraphStore(tmp_path)
    out = svc.build(_raw("light.whatever"), store=store, use_catalog=True)
    # 目录为空 → 静默退化为不校验（不把「没刷过目录」误判成「实体全不存在」）
    assert out["entity_check"] == "skipped"


@pytest.mark.integration
def test_build_explicit_known_entities_wins(tmp_path):
    _seed_catalog(tmp_path)
    store = GraphStore(tmp_path)
    out = svc.build(_raw("light.x"), ["binary_sensor.m", "light.x"], store=store, use_catalog=True)
    assert out["entity_check"] == "provided"


# ─────────────────────────────────────────────────────────────────────
# 10. MCP / HTTP / CLI
# ─────────────────────────────────────────────────────────────────────


def _content(result) -> dict:
    return json.loads(result[0][0]["text"])


@pytest.mark.integration
def test_mcp_catalog_tools(tmp_path):
    _seed_catalog(tmp_path)
    store = GraphStore(tmp_path)

    body, err = dispatch("af_resolve_entity", {"name": "书房吊灯"}, store)
    assert err is False and _content((body, err))["count"] == 2

    body, err = dispatch("af_list_entities", {"domain": "light"}, store)
    assert err is False and _content((body, err))["matched_count"] == 2

    body, err = dispatch("af_catalog", {}, store)
    assert err is False and _content((body, err))["total_entities"] == 5

    body, err = dispatch("af_get_entity_state", {"entity_id": "light.study_lamp"}, store, None)
    assert err is False


def test_mcp_tool_names_registered(tmp_path):
    from autoforge.af_mcp import TOOLS

    names = {t[0] for t in TOOLS}
    for expected in ("af_refresh_catalog", "af_resolve_entity", "af_list_entities", "af_get_entity_state", "af_catalog"):
        assert expected in names


@pytest.mark.integration
def test_api_catalog_endpoints(tmp_path):
    _seed_catalog(tmp_path)
    client = TestClient(build_app(str(tmp_path)))

    assert client.get("/api/catalog").json()["total_entities"] == 5

    resolved = client.get("/api/entities/resolve", params={"name": "书房吊灯"}).json()
    assert resolved["count"] == 2

    listed = client.get("/api/entities", params={"domain": "light"}).json()
    assert listed["matched_count"] == 2

    # 单实体状态：实时 HA 不可达 → 缓存兜底
    state = client.get("/api/entities/light.study_lamp/state").json()
    assert state["ok"] is True and state["source"] == "catalog_cache"


@pytest.mark.integration
def test_api_catalog_refresh_write_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOFORGE_HA_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("AF_ALLOW_NOAUTH", "1")
    client = TestClient(build_app(str(tmp_path)))
    out = client.post("/api/catalog/refresh").json()
    assert out["ok"] is False  # 无真实 HA，报错而非静默成功


@pytest.mark.integration
def test_cli_entities_resolve_and_list(tmp_path):
    _seed_catalog(tmp_path)
    r1 = runner.invoke(app, ["entities", "resolve", "书房吊灯", "--root", str(tmp_path)])
    assert r1.exit_code == 0 and "light.study_lamp" in r1.stdout

    r2 = runner.invoke(app, ["entities", "list", "--domain", "light", "--root", str(tmp_path)])
    assert r2.exit_code == 0 and "matched=" in r2.stdout

    r3 = runner.invoke(app, ["entities", "summary", "--root", str(tmp_path)])
    assert r3.exit_code == 0 and "设备总数：5" in r3.stdout


@pytest.mark.integration
def test_cli_entities_refresh_unreachable(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOFORGE_HA_URL", "http://127.0.0.1:1")
    result = runner.invoke(app, ["entities", "refresh", "--root", str(tmp_path)])
    assert result.exit_code != 0
