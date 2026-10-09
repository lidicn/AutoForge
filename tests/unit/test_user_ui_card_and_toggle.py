"""用户视角卡片（`/api/automations`）与启停：读数必须来自 automation 级，不是容器层。

起因（2026-10-09 现场）：owner 让 deepseek-agent 部署了「书房射灯与显示器挂灯同步」，
mimo 列表页把它渲染成「设备=无设备 / 预演效果空白 / 已启用」，点"停用"再刷新仍是"已启用"。
根因是端点在 `rec["graph"]` 这一层读 `nodes` / `enabled` / `nl`——而 `af_store._graph_raw`
在这一层只写 `{"automations": [...]}`，`nodes` 与 `enabled` 全住在 automation 级。
于是两个读数恒为缺省值（不是"这条自动化没设备"，是"读错了层"），
启停写进容器层的旗子也恒为无读者（`Automation.enabled` 唯一的读者在 af_ir/models.py）。

判据分三层：
  ①原始失败存在性（容器层确实没有那些键）——否则整组用例可能在测一个不存在的前提；
  ②正源对撞（卡片读数 == 模型层读数 == 渲染层读数，不是端点自己抄一份）；
  ③启停落到"模型真的会读的那一级"，且坏形状 fail-closed（不写新版本、不静默无效）。
"""
from __future__ import annotations

import json
import pathlib

import pytest
from fastapi.testclient import TestClient

from autoforge import af_service as svc
from autoforge.af_api import build_app
from autoforge.af_catalog import DeviceCatalog
from autoforge.af_ir import load_graph
from autoforge.af_nl import render_graph
from autoforge.af_store import GraphStore

ROOT = pathlib.Path(__file__).resolve().parents[2]

NAME = "书房射灯与显示器挂灯同步"
TRIGGER = "switch.lumi_cn_lumi_158d000239c546_aq1_on_p_2_1"
TARGET = "light.yeelink_cn_555003624_lamp22_s_2"

CARD_IR: dict = {
    "automations": [
        {
            "ir_version": "0.2.1",
            "id": "study_spot_lamp_sync",
            "name": NAME,
            "version": 1,
            "mode": "single",
            "nodes": [
                {
                    "id": "t1",
                    "kind": "on",
                    "trigger": {"type": "state", "entity_id": TRIGGER, "to": "on"},
                },
                {
                    "id": "a1",
                    "kind": "do",
                    "adapter": "mock",
                    "action": "light.turn_on",
                    "params": {"entity_id": TARGET},
                    "requires_confirm": True,
                },
            ],
            "edges": [{"from": "t1", "to": "a1", "kind": "then"}],
        }
    ]
}

CATALOG_STATES = {
    TRIGGER: ("on", {"friendly_name": "书房墙壁开关 书房射灯 开关", "area": "书房"}),
    TARGET: ("off", {"friendly_name": "米家智能显示器挂灯1S 灯", "area": "书房"}),
}


@pytest.fixture(autouse=True)
def _noauth_and_unreachable_ha(monkeypatch):
    monkeypatch.setenv("AF_ALLOW_NOAUTH", "1")
    monkeypatch.setenv("AUTOFORGE_HA_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("AUTOFORGE_HA_TIMEOUT", "0.3")
    monkeypatch.delenv("AUTOFORGE_HA_TOKEN", raising=False)


def _store(tmp_path, owner: str = "deepseek-agent") -> GraphStore:
    store = GraphStore(tmp_path)
    store.save(load_graph(CARD_IR), NAME, owner=owner)
    return store


def _seed_catalog(tmp_path) -> None:
    assert DeviceCatalog(tmp_path, fetch_all=lambda: dict(CATALOG_STATES)).refresh()["ok"]


def _write_raw_record(tmp_path, name: str, graph: dict, version: int = 1, owner: str = "agent-a") -> None:
    """绕过 store 的校验直接落一条记录——测"归档本身坏掉/形状不认识"这两档现场。"""
    directory = GraphStore(tmp_path)._dir(name)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"v{version}.json").write_text(
        json.dumps(
            {"name": name, "version": version, "saved_at": "2026-10-09T06:00:00+00:00",
             "note": "", "writer": "test", "owner": owner, "graph": graph},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


# ── ①原始失败存在性 ────────────────────────────────────────────────────

def test_container_layer_holds_no_nodes_no_enabled_no_nl(tmp_path):
    """旧端点读的三个键在容器层**一个都不存在**——这是那三个假读数的结构成因。"""
    rec = _store(tmp_path).load_record(NAME)
    container = rec["graph"]
    assert list(container.keys()) == ["automations"], container.keys()
    for absent in ("nodes", "enabled", "nl"):
        assert absent not in container, absent
    # 而它们确实住在 automation 级
    auto_raw = container["automations"][0]
    assert auto_raw["nodes"] and auto_raw["enabled"] is True


# ── ②卡片读数 == 模型层读数 ────────────────────────────────────────────

def test_card_lists_trigger_and_target_with_catalog_names(tmp_path):
    _seed_catalog(tmp_path)
    store = _store(tmp_path)
    card = svc.automation_card(store, NAME, store.load_record(NAME), [], {})
    ids = {d["entity_id"] for d in card["devices"]}
    assert ids == {TRIGGER, TARGET}, card["devices"]
    names = {d["friendly_name"] for d in card["devices"]}
    assert names == {"书房墙壁开关 书房射灯 开关", "米家智能显示器挂灯1S 灯"}, names


def test_card_falls_back_to_entity_id_when_catalog_is_empty(tmp_path):
    """目录还没刷新 ≠ 设备没名字：回退到标识本身，不交空串（空串会被前端渲染成"未知设备"）。"""
    store = _store(tmp_path)
    card = svc.automation_card(store, NAME, store.load_record(NAME), [], {})
    assert {d["friendly_name"] for d in card["devices"]} == {TRIGGER, TARGET}


def test_card_preview_nl_is_the_same_render_as_the_graph_endpoint(tmp_path):
    """预演效果与 `GET /api/graphs/{name}` 的 `nl` 同源（同一个 `render_graph`），端点不另抄一份。"""
    store = _store(tmp_path)
    card = svc.automation_card(store, NAME, store.load_record(NAME), [], {})
    expected = render_graph(load_graph(CARD_IR)).text
    assert card["preview_nl"] == expected
    assert expected.strip() and NAME in expected


@pytest.mark.parametrize(
    "flags,expected_status",
    [((True, True), "enabled"), ((True, False), "disabled"), ((False, False), "disabled")],
)
def test_card_enabled_is_all_of_per_automation_flags(tmp_path, flags, expected_status):
    two = {
        "automations": [
            {**CARD_IR["automations"][0], "id": f"i{i}", "name": f"i{i}", "enabled": flag}
            for i, flag in enumerate(flags)
        ]
    }
    store = GraphStore(tmp_path)
    store.save(load_graph(two), NAME, owner="agent-a")
    card = svc.automation_card(store, NAME, store.load_record(NAME), [], {})
    assert card["enabled"] is all(flags), card
    assert card["status"] == expected_status, card


def test_card_reports_archived_before_enabled(tmp_path):
    store = _store(tmp_path)
    card = svc.automation_card(store, NAME, store.load_record(NAME), ["archived"], {})
    assert card["status"] == "archived" and card["archived"] is True


def test_card_trial_has_no_record_source_so_it_stays_null(tmp_path):
    """旧形状硬写 `state: "auto"`，等于替每条自动化宣布"已走到全自动档"。

    首演/试演台账按 diff 摘要记账（`af_premiere.enter_trial(store_diff_sha)`），不是按归档名；
    触发记录只在 watch 进程内存里。读不出就给 null——前端 `trialMeta(null)` 渲染成不点亮的三档。
    """
    store = _store(tmp_path)
    card = svc.automation_card(store, NAME, store.load_record(NAME), [], {})
    assert card["trial"] is None
    assert card["last_triggered"] is None and card["trigger_7d"] == 0


def test_card_of_broken_archive_names_the_failure_and_stays_readable(tmp_path):
    _write_raw_record(tmp_path, "bad_archive", {"automations": [{"id": "x"}]})
    store = GraphStore(tmp_path)
    card = svc.automation_card(store, "bad_archive", store.load_record("bad_archive"), [], {})
    assert "归档无法解析" in card["preview_nl"], card["preview_nl"]
    assert card["devices"] == [] and card["enabled"] is False
    # 空 automations 不是"读不出"，是"真没有规则"：走正常解析路径，标未启用
    _write_raw_record(tmp_path, "empty_archive", {"automations": []})
    empty = svc.automation_card(store, "empty_archive", store.load_record("empty_archive"), [], {})
    assert empty["enabled"] is False and empty["status"] == "disabled", empty


# ── 端点：一条坏归档不能把整个列表打死 ─────────────────────────────────

def test_automations_endpoint_returns_real_devices(tmp_path):
    _seed_catalog(tmp_path)
    _store(tmp_path)
    client = TestClient(build_app(store_root=str(tmp_path)))
    body = client.get("/api/automations").json()
    item = body["groups"][0]["items"][0]
    assert {d["entity_id"] for d in item["devices"]} == {TRIGGER, TARGET}
    assert body["groups"][0]["agent"] == "deepseek-agent"
    assert item["preview_nl"].strip()


def test_automations_endpoint_survives_one_broken_archive(tmp_path):
    _store(tmp_path)
    _write_raw_record(tmp_path, "bad_archive", {"automations": [{"id": "x"}]})
    client = TestClient(build_app(store_root=str(tmp_path)))
    r = client.get("/api/automations")
    assert r.status_code == 200, r.text
    cards = [c for g in r.json()["groups"] for c in g["items"]]
    broken = [c for c in cards if "归档无法解析" in c["preview_nl"]]
    assert len(broken) == 1 and len(cards) == 2, cards


# ── ③启停落在模型会读的那一级 ──────────────────────────────────────────

@pytest.mark.parametrize("enabled,verb", [(False, "disable"), (True, "enable")])
def test_toggle_lands_where_the_model_reads(tmp_path, enabled, verb):
    store = _store(tmp_path)
    client = TestClient(build_app(store_root=str(tmp_path)))
    r = client.post(f"/api/automations/{NAME}/{verb}")
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 2
    container = store.load_record(NAME)["graph"]
    assert "enabled" not in container, "旗子又写回无人读的容器层"
    assert all(a.enabled is enabled for a in store.load(NAME)), [a.enabled for a in store.load(NAME)]
    card = svc.automation_card(store, NAME, store.load_record(NAME), [], {})
    assert card["enabled"] is enabled and card["status"] == ("enabled" if enabled else "disabled")


def test_toggle_then_card_endpoint_round_trips(tmp_path):
    store = _store(tmp_path)
    client = TestClient(build_app(store_root=str(tmp_path)))
    client.post(f"/api/automations/{NAME}/disable")
    item = client.get(f"/api/automations/{NAME}").json()["automation"]
    assert item["enabled"] is False and item["status"] == "disabled", item


def test_toggle_keeps_the_archive_owner(tmp_path):
    """归属丢了，用户视角就会把这条从 agent 组搬进「未归属/本地」——看起来像归档被重建过。"""
    store = _store(tmp_path)
    client = TestClient(build_app(store_root=str(tmp_path)))
    client.post(f"/api/automations/{NAME}/disable")
    rec = store.load_record(NAME)
    assert rec["owner"] == "deepseek-agent", rec["owner"]
    assert rec["note"] == "" or isinstance(rec["note"], str)


def test_toggle_refuses_unrecognized_container_shape(tmp_path):
    """形状不认识 ⇒ 409 且**不落新版本**：写在容器层会静默无效，静默无效比报错更难查。"""
    _write_raw_record(tmp_path, "legacy", {"automations": []})
    store = GraphStore(tmp_path)
    client = TestClient(build_app(store_root=str(tmp_path)))
    r = client.post("/api/automations/legacy/disable")
    assert r.status_code == 409, (r.status_code, r.text)
    assert store.latest("legacy") == 1, store.latest("legacy")


def test_service_toggle_is_the_only_writer_of_the_flag(tmp_path):
    """启停的正源在服务层；端点不得再自己拼容器层旗子（那正是本批修掉的形状）。"""
    tree_ok = (ROOT / "src" / "autoforge" / "af_api.py").read_text(encoding="utf-8")
    assert "svc.set_automation_enabled" in tree_ok
    assert 'g.__setitem__("enabled"' not in tree_ok, "端点又回到容器层写旗子"
    store = _store(tmp_path)
    assert svc.set_automation_enabled(store, NAME, False) == 2


def test_batch_enable_by_tag_keeps_owner(tmp_path):
    """批量启停同样要带归属：`store.save` 的 owner 缺省是空串，不传等于把别人建的归档改成无归属。"""
    store = _store(tmp_path)
    store.set_tags(NAME, ["night"])
    res = svc.enable_by_tag(store, "night", False, allow_bulk=True)
    assert res["affected"], res
    assert store.load_record(NAME)["owner"] == "deepseek-agent", store.load_record(NAME)
