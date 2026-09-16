"""服务层单测：FastAPI 只读端点（对齐 UI 开工令附录 A 契约）。

装了 fastapi 才跑；未装则整文件 skip（保持内核测试可在无 Web 依赖下运行）。
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from autoforge.af_api import build_app  # noqa: E402
from autoforge.af_ir import load_graph  # noqa: E402
from autoforge.af_store import GraphStore  # noqa: E402


@pytest.fixture
def client(tmp_path, examples_dir):
    app = build_app(str(tmp_path / "store"), str(examples_dir))
    return TestClient(app)


def test_health(client):
    body = client.get("/api/health").json()
    assert body["ok"] is True and body["readonly"] is True
    assert "G5" in body["milestones"] and body["contract_version"] == "1.0"


def test_graphs_lists_bootstrapped_examples(client):
    items = client.get("/api/graphs").json()["items"]
    names = {i["name"] for i in items}
    assert "case01_day_light" in names
    assert all(i["latest_version"] >= 1 for i in items)

    case01 = next(i for i in items if i["name"] == "case01_day_light")
    assert case01["automation_ids"] == ["study_day_light"]
    assert case01["mode"] == "restart"


def test_conf_all_aggregates_across_archives(client):
    body = client.get("/api/conf/_all").json()
    ids = {i["automation_id"] for i in body["items"]}
    assert "study_day_light" in ids and len(ids) >= 5
    assert body["thresholds"] == {"auto": 0.85, "shadow_low": 0.6}
    # 别名 `all`
    assert client.get("/api/conf/all").json()["items"]


def test_intervene_via_all_locates_archive(client):
    after = client.post("/api/conf/_all/intervene", json={"automation_id": "study_day_light"}).json()
    assert after["name"] == "case01_day_light"  # 落实到了对应归档
    assert after["items"][0]["confidence"] < 1.0


def test_graph_detail_has_ir_nl_diagnostics(client):
    body = client.get("/api/graphs/case01_day_light").json()
    assert body["ok"] is True and body["version"] >= 1
    assert body["ir"]["id"] == "study_day_light"
    assert body["nl"]  # 后端确定性渲染，非空
    assert isinstance(body["diagnostics"], list)


def test_graph_detail_unknown_returns_404(client):
    assert client.get("/api/graphs/does_not_exist").status_code == 404


def test_build_ok_and_l3_rejected(client, examples_dir):
    ok_ir = json.loads((examples_dir / "case01_day_light.json").read_text(encoding="utf-8"))
    good = client.post("/api/build", json={"ir": ok_ir}).json()
    assert good["ok"] is True and good["errors"] == [] and good["nl"]

    bad_ir = json.loads((examples_dir / "invalid_case06_delete_all.json").read_text(encoding="utf-8"))
    bad = client.post("/api/build", json={"ir": bad_ir}).json()
    assert bad["ok"] is False
    assert "L3_ACTION" in {d["code"] for d in bad["errors"]}


def test_sim_replays_case01(client, examples_dir):
    ir = json.loads((examples_dir / "case01_day_light.json").read_text(encoding="utf-8"))
    body = client.post(
        "/api/sim",
        json={
            "ir": ir,
            "seed": {"sensor.study_illum": "80", "light.study_main": "off", "binary_sensor.study_motion": "off"},
            "events": [{"entity_id": "binary_sensor.study_motion", "state": "on"}],
        },
    ).json()

    assert body["ok"] is True
    assert body["final_states"]["light.study_main"] == "on"
    assert len(body["instances"]) == 1
    assert body["bus"]["counts"]["accepted"] >= 1
    assert isinstance(body["audit"], list)


def test_conf_and_intervene(client):
    before = client.get("/api/conf/case01_day_light").json()
    assert before["thresholds"] == {"auto": 0.85, "shadow_low": 0.6}
    item = before["items"][0]
    assert item["band"] == "auto" and item["confidence"] == 1.0

    after = client.post(
        "/api/conf/case01_day_light/intervene", json={"automation_id": item["automation_id"]}
    ).json()
    assert after["items"][0]["confidence"] < item["confidence"]

    # 干预已落盘：重新读取应保持
    reread = client.get("/api/conf/case01_day_light").json()
    assert reread["items"][0]["confidence"] == after["items"][0]["confidence"]


def test_intervene_unknown_automation_404(client):
    resp = client.post("/api/conf/case01_day_light/intervene", json={"automation_id": "nope"})
    assert resp.status_code == 404


def test_diff_between_versions(client, tmp_path):
    store = GraphStore(str(tmp_path / "store"))

    def ir(mode: str, extra_node: bool = False) -> dict:
        nodes = [
            {"id": "o", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
            {"id": "p", "kind": "pass"},
        ]
        edges = [{"from": "o", "to": "p", "kind": "then"}]
        if extra_node:
            nodes.insert(1, {"id": "s", "kind": "set", "var": "flag", "value": 1})
            edges = [
                {"from": "o", "to": "s", "kind": "then"},
                {"from": "s", "to": "p", "kind": "then"},
            ]
        data = {
            "ir_version": "0.2.1",
            "id": "demo",
            "name": "demo",
            "version": 1,
            "mode": mode,
            "nodes": nodes,
            "edges": edges,
        }
        return data

    store.save(load_graph(ir("single")), "demo", note="v1")
    store.save(load_graph(ir("restart", extra_node=True)), "demo", note="v2")

    body = client.get("/api/diff", params={"name": "demo", "old": 1, "new": 2}).json()
    assert body["ok"] is True
    assert any(n["node_id"] == "demo:s" for n in body["structured"]["added_nodes"])
    assert any(m["field"] == "mode" for m in body["structured"]["meta_changes"])
    assert any(m["key"] == "demo.mode" for m in body["structured"]["meta_changes"])
    assert any(not isinstance(e, str) and e["from"] for e in body["structured"]["added_edges"])
    assert any(line.startswith(("+", "-", "~")) for line in body["render"].splitlines())
    assert body["notes"] == {"old": "v1", "new": "v2"}, "应回传两版本的归档备注"


def test_diff_notes_surface_when_graph_identical(client, tmp_path):
    """图内容相同、仅归档备注不同 → `render` 为「无差异」，但 `notes` 回传两版备注。

    v0.2.0：`note` 属归档记录元数据（`GraphStore.save(note=...)`），**不在 Graph 内**，
    故不参与 `diff_graphs` 图内容比对；单独回传以免前端把"只改了备注"误读为漏比对。
    """
    store = GraphStore(str(tmp_path / "store"))  # 与 `client` fixture 的 store root 一致
    ir = {
        "ir_version": "0.2.1",
        "id": "demo",
        "name": "demo",
        "version": 1,
        "mode": "single",
        "nodes": [
            {"id": "o", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
            {"id": "p", "kind": "pass"},
        ],
        "edges": [{"from": "o", "to": "p", "kind": "then"}],
    }
    store.save(load_graph(ir), "demo", note="bootstrap")
    store.save(load_graph(ir), "demo", note="verify v2")

    body = client.get("/api/diff", params={"name": "demo", "old": 1, "new": 2}).json()
    assert body["render"] == "无差异", "图内容相同应判无差异"
    assert body["notes"] == {"old": "bootstrap", "new": "verify v2"}


def test_spec_render_and_compile(client):
    rendered = client.get("/api/spec/case01_day_light").json()
    assert rendered["ok"] is True and "automation study_day_light" in rendered["spec"]

    compiled = client.post("/api/spec/compile", json={"text": rendered["spec"]}).json()
    assert compiled["ok"] is True and compiled["ir"]["id"] == "study_day_light" and compiled["nl"]

    bad = client.post("/api/spec/compile", json={"text": "bogus line"}).json()
    assert bad["ok"] is False and bad["error"]["code"]


def test_faults_metadata(client):
    body = client.get("/api/faults").json()
    assert {k["value"] for k in body["kinds"]} == {"unavailable", "timeout", "reorder", "drop", "drift"}
    assert {f["key"] for f in body["failures"]} == {"device", "timeout", "cancel", "reject"}
    assert all(isinstance(f["recoverable"], bool) for f in body["failures"])
    assert all(k["inject_layer"] in {"state", "adapter", "event"} for k in body["kinds"])


def test_openapi_available(client):
    schema = client.get("/openapi.json").json()
    assert "/api/health" in schema["paths"]
    assert "/api/spec/compile" in schema["paths"]
    assert "/api/sessions" in schema["paths"]
    assert "/api/live/run" in schema["paths"]


# ─────────────────────────────────────────────────────────────────────
# Round 2-A：会话（ask 审批的人机回路）
# ─────────────────────────────────────────────────────────────────────

_ASK_SEED = {"sensor.study_temp": "28", "binary_sensor.study_door": "off", "climate.study": "off"}


def _ask_ir(examples_dir):
    return json.loads((examples_dir / "case04_ask_timeout.json").read_text(encoding="utf-8"))


def _create_ask_session(client, examples_dir):
    return client.post(
        "/api/sessions",
        json={
            "ir": _ask_ir(examples_dir),
            "seed": _ASK_SEED,
            "events": [{"entity_id": "sensor.study_temp", "state": "28.5"}],
        },
    ).json()


def test_session_ask_answer_yes(client, examples_dir):
    created = _create_ask_session(client, examples_dir)
    sid = created["session_id"]
    assert len(created["asks"]) == 1
    ask = created["asks"][0]
    assert ask["room"] == "study" and ask["prompt"] and ask["node_id"]

    answered = client.post(
        f"/api/sessions/{sid}/answer", json={"ask_id": ask["ask_id"], "text": "好"}
    ).json()
    assert answered["asks"] == []
    assert answered["final_states"]["climate.study"] == "cool"

    assert client.delete(f"/api/sessions/{sid}").json()["deleted"] is True
    assert client.get(f"/api/sessions/{sid}").status_code == 404


def test_session_answer_no_branch(client, examples_dir):
    created = _create_ask_session(client, examples_dir)
    sid = created["session_id"]
    answered = client.post(f"/api/sessions/{sid}/answer", json={"room": "study", "text": "不用了"}).json()
    assert answered["asks"] == []
    assert answered["final_states"]["climate.study"] == "off"


def test_session_tick_triggers_timeout(client, examples_dir):
    created = _create_ask_session(client, examples_dir)
    sid = created["session_id"]

    ticked = client.post(f"/api/sessions/{sid}/tick", json={"advance_s": 61}).json()
    assert ticked["asks"] == []
    assert ticked["instances"][0]["state"] == "done"
    assert ticked["final_states"]["climate.study"] == "off"


def test_session_cancel(client, examples_dir):
    created = _create_ask_session(client, examples_dir)
    sid = created["session_id"]

    cancelled = client.post(f"/api/sessions/{sid}/cancel", json={"reason": "人离开"}).json()
    assert cancelled["asks"] == []
    assert cancelled["instances"][0]["state"] == "cancelled"


def test_session_list_and_unknown_404(client, examples_dir):
    created = _create_ask_session(client, examples_dir)
    assert created["session_id"] in client.get("/api/sessions").json()["session_ids"]
    assert client.get("/api/sessions/nope").status_code == 404
    assert client.post("/api/sessions/nope/answer", json={"text": "好"}).status_code == 404


# ─────────────────────────────────────────────────────────────────────
# Round 2-B：真机下发（三重闸）
# ─────────────────────────────────────────────────────────────────────


def _live_ir() -> dict:
    return {
        "ir_version": "0.2.1",
        "id": "live_demo",
        "name": "真机下发自检",
        "version": 1,
        "mode": "single",
        "confidence": 0.9,
        "nodes": [
            {"id": "o", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
            {
                "id": "d",
                "kind": "do",
                "adapter": "ha",
                "action": "light.turn_on",
                "params": {"entity_id": "light.x"},
                "canary": {"duration": "10s", "auto_rollback": True},
            },
            {"id": "p", "kind": "pass"},
        ],
        "edges": [
            {"from": "o", "to": "d", "kind": "then"},
            {"from": "d", "to": "p", "kind": "then"},
            {"from": "d", "to": "p", "kind": "on_error"},
        ],
    }


def test_live_status_and_gate_disabled_by_default(client):
    status = client.get("/api/live/status").json()
    assert status["ok"] is True and status["enabled"] is False
    assert status["allowlist_required"] is True and status["confirm_required"] is True
    assert status["reasons"]

    resp = client.post("/api/live/run", json={"ir": _live_ir(), "live_allow": ["light.x"], "confirm": True})
    assert resp.status_code == 403


def test_live_run_triple_gate_and_success(client, monkeypatch):
    from autoforge import af_service
    from autoforge.af_adapters import CallResult

    class FakeTransport:
        def __init__(self) -> None:
            self.states = {"light.x": ("off", {})}

        def __call__(self, action, params):
            entity_id = str((params or {}).get("entity_id"))
            if action.endswith("turn_on"):
                self.states[entity_id] = ("on", {})
            elif action.endswith("turn_off"):
                self.states[entity_id] = ("off", {})
            return CallResult.ok({"action": action, "params": dict(params or {})})

        def all_states(self):
            return dict(self.states)

    monkeypatch.setenv("AUTOFORGE_LIVE_ENABLED", "1")
    monkeypatch.setenv("AUTOFORGE_HA_TOKEN", "tok")
    monkeypatch.setattr(af_service, "LIVE_TRANSPORT_FACTORY", lambda url, token: FakeTransport())

    assert client.get("/api/live/status").json()["enabled"] is True

    ir = _live_ir()
    # 闸①：缺二次确认
    assert client.post("/api/live/run", json={"ir": ir, "live_allow": ["light.x"]}).status_code == 403
    # 闸②：写目标越界（白名单不含 light.x）
    assert client.post(
        "/api/live/run", json={"ir": ir, "live_allow": ["light.other"], "confirm": True}
    ).status_code == 400
    # 闸③：白名单为空
    assert client.post("/api/live/run", json={"ir": ir, "live_allow": [], "confirm": True}).status_code == 400

    # 全通过：真实下发（此处为注入的假 transport）
    body = client.post(
        "/api/live/run",
        json={
            "ir": ir,
            "live_allow": ["light.x"],
            "confirm": True,
            "events": [{"entity_id": "binary_sensor.m", "state": "on"}],
        },
    ).json()
    assert body["live"] is True and body["mode"] == "live"
    assert body["writes"] == ["light.x"] and body["allowlist"] == ["light.x"]
    assert body["final_states"]["light.x"] == "on"


# ── 可选鉴权（默认关闭，仅保护写操作 + 真机下发）────────────────────


def _token_client(tmp_path, examples_dir, monkeypatch, token):
    monkeypatch.setenv("AUTOFORGE_API_TOKEN", token)
    return TestClient(build_app(str(tmp_path / "store"), str(examples_dir)))


def test_auth_disabled_by_default(client):
    """未设 AUTOFORGE_API_TOKEN 时，受保护端点（含真机下发）不强制鉴权。"""
    # 真机下发状态端点：默认 200（非 403）
    assert client.get("/api/live/status").status_code == 200
    # 会话写端点同样不强制
    body = client.post("/api/sessions", json={"ir": {}})
    assert body.status_code != 403  # 可能因 IR 校验而 400，但绝不是鉴权拒绝


def test_auth_required_when_token_set(tmp_path, examples_dir, monkeypatch):
    """设令牌后：受保护端点无令牌返回 403，带正确令牌放行。"""
    client = _token_client(tmp_path, examples_dir, monkeypatch, "secret")

    # 无令牌 → 403（真机下发状态 + 会话写）
    assert client.get("/api/live/status").status_code == 403
    assert client.post("/api/sessions", json={"ir": {}}).status_code == 403

    # 带正确令牌 → 放行（非 403；IR 校验失败是 400 不是鉴权问题）
    headers = {"Authorization": "Bearer secret"}
    assert client.get("/api/live/status", headers=headers).status_code == 200
    assert client.post("/api/sessions", json={"ir": {}}, headers=headers).status_code != 403

    # 错误令牌 → 仍 403
    assert (
        client.get("/api/live/status", headers={"Authorization": "Bearer wrong"}).status_code
        == 403
    )


def test_auth_does_not_block_read_endpoints(tmp_path, examples_dir, monkeypatch):
    """读端点（会话列表/详情、图表、健康）不受鉴权影响，始终开放。"""
    client = _token_client(tmp_path, examples_dir, monkeypatch, "secret")

    # 无令牌也开放
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/graphs").status_code == 200
    assert client.get("/api/sessions").status_code == 200
    # 详情端点（即使会话存在与否都不应被 403 拦截）
    assert client.get("/api/sessions/nope").status_code != 403
