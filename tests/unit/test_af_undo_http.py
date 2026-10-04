"""F7 残留（WebUI 撤销按钮）：撤销的 HTTP 服务面 + 真机下发落快照。

覆盖口径：
- 决策 E：撤销 = 对真实设备**再下发一次**，闸门与 `/api/live/run` 同源
  （live scope + 服务端 `AUTOFORGE_LIVE_ENABLED` / `AUTOFORGE_HA_TOKEN`，令牌绝不经请求体）。
- 铁律 6：单写者租约降级为只读的实例不得写设备 → 503（拦在鉴权之前，原因要说得出口）。
- 单一真值源：时间窗 / 风险域 confirm / 未知 deploy_id 的判定全在 `af_undo.UndoStore`，
  HTTP 层只转发，不自己算第二套。
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from autoforge import af_service as svc
from autoforge import af_undo
from autoforge.af_adapters import CallResult
from autoforge.af_api import build_app
from autoforge.af_undo import UndoStore

LIVE_IR: dict = {
    "automations": [
        {
            "ir_version": "0.2.1",
            "id": "a_undo",
            "name": "a_undo",
            "version": 1,
            "mode": "restart",
            "nodes": [
                {
                    "id": "t1",
                    "kind": "on",
                    "trigger": {"type": "state", "entity_id": "binary_sensor.hall", "to": "on"},
                },
                {
                    "id": "d1",
                    "kind": "do",
                    "adapter": "ha",
                    "action": "light.turn_off",
                    "params": {"entity_id": "light.study"},
                    "on_error": {"default": "pass"},
                    "expect": {"entity_id": "light.study", "state": "off"},
                },
            ],
            "edges": [{"from": "t1", "to": "d1", "kind": "then"}],
        }
    ]
}


class FakeTransport:
    """假 HA 传输层：记录下发、可按预置状态供动作前快照。"""

    def __init__(self, states: dict[str, tuple[str, dict]] | None = None):
        self.states = states or {"light.study": ("on", {"brightness": 120})}
        self.dispatched: list[tuple[str, dict]] = []

    def __call__(self, action, params):
        return self.call(action, params)

    def call(self, action, params):
        self.dispatched.append((action, dict(params)))
        return CallResult.ok({"action": action, "result": []})

    def all_states(self):
        return dict(self.states)

    def get_state(self, entity_id):
        found = self.states.get(entity_id)
        return found[0] if found else None


@pytest.fixture()
def noauth(monkeypatch):
    """本地逃生舱：不配令牌时放行鉴权依赖，让闸门测试测的是闸门本身。"""
    monkeypatch.setenv("AF_ALLOW_NOAUTH", "1")
    monkeypatch.delenv("AUTOFORGE_LIVE_ENABLED", raising=False)
    monkeypatch.delenv("AUTOFORGE_HA_TOKEN", raising=False)
    monkeypatch.delenv("AUTOFORGE_UNDO", raising=False)


def test_undo_available_empty_and_preview_unknown(tmp_path, noauth):
    client = TestClient(build_app(store_root=str(tmp_path)))
    r = client.get("/api/undo/available")
    assert r.status_code == 200
    body = r.json()
    assert "ok" not in body, "只读盘点不许带字面量 ok（AST 门禁 fake-ok-const）"
    assert body["items"] == [] and body["window_s"] > 0

    r2 = client.get("/api/undo/dep-nope")
    assert r2.status_code == 200, "盘点端点要返回 exists=false + reason，而不是笼统 404"
    assert r2.json()["exists"] is False
    assert r2.json()["reason"] == "unknown_deploy_id"


def test_undo_preview_reports_window_and_risk_domain(tmp_path, noauth):
    store = UndoStore(str(tmp_path))
    store.record("dep-risk", {"cover.bedroom": {"state": "closed", "attributes": {"current_position": 0}}})
    client = TestClient(build_app(store_root=str(tmp_path)))

    body = client.get("/api/undo/dep-risk").json()
    assert body["exists"] is True and body["undoable"] is True
    assert body["risk_entities"] == ["cover.bedroom"]
    assert body["confirm_required"] is True, "风险域要在此刻就告诉前端需要二次确认"
    assert body["domain_mapped"] == ["cover.bedroom"]


def test_undo_preview_marks_unmapped_domain(tmp_path, noauth):
    """sensor 域没有恢复映射：能盘点出来，但绝不谎称"可撤销"。"""
    store = UndoStore(str(tmp_path))
    store.record("dep-sensor", {"sensor.temp": {"state": "21.5", "attributes": {}}})
    client = TestClient(build_app(store_root=str(tmp_path)))

    body = client.get("/api/undo/dep-sensor").json()
    assert body["domain_mapped"] == []
    assert body["domain_unmapped"] == ["sensor.temp"]


def test_undo_post_is_gated_by_live_switch_and_token(tmp_path, noauth, monkeypatch):
    """未开真机闸门 → 403；开了闸门但服务端无令牌 → 403。令牌只能来自服务端。"""
    client = TestClient(build_app(store_root=str(tmp_path)))
    r = client.post("/api/undo/dep-x", json={"confirm": True})
    assert r.status_code == 403
    assert "AUTOFORGE_LIVE_ENABLED" in r.json()["detail"]

    monkeypatch.setenv("AUTOFORGE_LIVE_ENABLED", "1")
    r2 = client.post("/api/undo/dep-x", json={"confirm": True})
    assert r2.status_code == 403
    assert "AUTOFORGE_HA_TOKEN" in r2.json()["detail"]


def test_undo_post_token_never_comes_from_body(tmp_path, noauth):
    """请求体里塞 token 不改变闸门结果——缺服务端令牌照样 403（P0-4 口径）。"""
    client = TestClient(build_app(store_root=str(tmp_path)))
    r = client.post("/api/undo/dep-x", json={"confirm": True, "token": "attacker-token"})
    assert r.status_code == 403


def test_readonly_degraded_instance_refuses_device_writes(tmp_path):
    """铁律 6：租约被别的实例持有时，本实例连真机下发/撤销都不许走 → 503。

    拦在鉴权之前：只读降级本身就是拒绝理由，不该先问令牌。
    """
    client = TestClient(build_app(store_root=str(tmp_path), readonly=True))
    r = client.post("/api/live/run", json={"ir": LIVE_IR, "live_allow": ["light.study"], "confirm": True})
    assert r.status_code == 503
    r2 = client.post("/api/undo/dep-x", json={"confirm": True})
    assert r2.status_code == 503
    # 只读诊断不拦：前端要能渲染"为什么不能下发"
    assert client.get("/api/undo/available").status_code == 403


def test_live_run_records_pre_snapshot_and_undo_restores_it(tmp_path, noauth, monkeypatch):
    """端到端：WebUI 真机下发 → 落动作前快照 → 窗口内撤销 → 设备回到下发前状态。"""
    monkeypatch.setenv("AUTOFORGE_LIVE_ENABLED", "1")
    monkeypatch.setenv("AUTOFORGE_HA_TOKEN", "srv-side-token")
    transports: list[FakeTransport] = []

    def factory(ha_url, token):
        assert token == "srv-side-token", "令牌必须来自服务端环境，不是请求体"
        t = FakeTransport()
        transports.append(t)
        return t

    monkeypatch.setattr(svc, "LIVE_TRANSPORT_FACTORY", factory)
    client = TestClient(build_app(store_root=str(tmp_path)))

    r = client.post(
        "/api/live/run",
        json={
            "ir": LIVE_IR,
            "live_allow": ["light.study"],
            "events": [{"entity_id": "binary_sensor.hall", "state": "on"}],
            "confirm": True,
            "undo": True,
        },
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    deploy_id = body["undo_deploy_id"]
    assert deploy_id, "undo=true 必须把撤销 ID 交回前端，否则按钮无从指向"
    assert [a for a, _ in transports[0].dispatched] == ["light.turn_off"]
    # 第七轮审计：`StateProvider` 统一 fail-closed 后，触发实体不在 HA 里会抛。
    # 这是**报告**读取点（动作已下发、撤销句柄已落盘），必须显式列出缺口而不是 500
    # ——500 会让操作员连 undo_deploy_id 都拿不到。
    assert body["missing_entities"] == ["binary_sensor.hall"]
    assert body["final_states"]["binary_sensor.hall"] is None
    assert body["final_states"]["light.study"] == "on"

    u = client.post(f"/api/undo/{deploy_id}", json={"confirm": True})
    assert u.status_code == 200, u.json()
    body = u.json()
    assert body["restored"] == ["light.study"]
    assert body["fully_restored"] is True
    # 撤销走的是"开灯 + 还原亮度"，不是只会关灯
    action, params = transports[-1].dispatched[-1]
    assert action == "light.turn_on" and params["entity_id"] == "light.study"
    assert params["brightness"] == 120


def test_live_run_without_undo_flag_advertises_nothing(tmp_path, noauth, monkeypatch):
    """未开 undo：不谎报可撤销（`undo_deploy_id=None`），也不落快照。"""
    monkeypatch.setenv("AUTOFORGE_LIVE_ENABLED", "1")
    monkeypatch.setenv("AUTOFORGE_HA_TOKEN", "t")
    monkeypatch.setattr(svc, "LIVE_TRANSPORT_FACTORY", lambda ha_url, token: FakeTransport())
    client = TestClient(build_app(store_root=str(tmp_path)))

    r = client.post(
        "/api/live/run",
        json={
            "ir": LIVE_IR,
            "live_allow": ["light.study"],
            "events": [{"entity_id": "binary_sensor.hall", "state": "on"}],
            "confirm": True,
        },
    )
    assert r.status_code == 200, r.json()
    assert r.json()["undo_enabled"] is False
    assert r.json()["undo_deploy_id"] is None
    assert client.get("/api/undo/available").json()["items"] == []


def test_undo_refuses_expired_window_via_http(tmp_path, noauth, monkeypatch):
    """窗口过期由 UndoStore 判定（服务端 `AUTOFORGE_UNDO_WINDOW_S`），HTTP 层原样回传
    reason，不自创第二套口径。"""
    monkeypatch.setenv("AUTOFORGE_LIVE_ENABLED", "1")
    monkeypatch.setenv("AUTOFORGE_HA_TOKEN", "t")
    monkeypatch.setattr(svc, "LIVE_TRANSPORT_FACTORY", lambda ha_url, token: FakeTransport())
    # 服务端窗口设成 0：任何一条快照都算过期（窗口是服务端配置，不随记录走）
    monkeypatch.setattr(af_undo, "DEFAULT_WINDOW_S", 0.001)

    store = UndoStore(str(tmp_path))
    store.record("dep-old", {"light.study": {"state": "on", "attributes": {"brightness": 10}}})
    time.sleep(0.02)

    client = TestClient(build_app(store_root=str(tmp_path)))
    preview = client.get("/api/undo/dep-old").json()
    assert preview["expired"] is True and preview["undoable"] is False

    r = client.post("/api/undo/dep-old", json={"confirm": True})
    assert r.status_code == 200
    assert r.json()["ok"] is False and r.json()["reason"] == "expired"


# ── 撤销清单的服务面口径（前端 10s 定时刷新依赖这两条，改一条页面就读到假数）──

#: `ui/src/types/api.ts:UndoAvailableResponse` + `LiveView.vue` 逐键渲染的集合
UNDO_AVAILABLE_KEYS = {"window_s", "items"}
UNDO_ITEM_KEYS = {"deploy_id", "age_s", "entities"}


def test_expired_deploy_leaves_the_available_list(tmp_path, noauth, monkeypatch):
    """过期项**不在清单里**——不是"列出来但标过期"。

    `LiveView.vue` 每 10s 重拉一次清单，靠这条口径让过窗的行自己消失。若哪天改成
    含过期项返回，页面会一直挂着"窗口内可撤销 N 条"的读数，而点下去必被 `expired` 拒。
    """
    monkeypatch.setattr(af_undo, "DEFAULT_WINDOW_S", 0.001)
    store = UndoStore(str(tmp_path))
    store.record("dep-gone", {"light.study": {"state": "on", "attributes": {}}})
    client = TestClient(build_app(store_root=str(tmp_path)))

    body = client.get("/api/undo/available").json()
    assert set(body) == UNDO_AVAILABLE_KEYS
    time.sleep(0.02)
    assert client.get("/api/undo/available").json()["items"] == []


def test_available_items_carry_the_keys_the_ui_renders(tmp_path, noauth, monkeypatch):
    """窗口内的行必须给全 `deploy_id`/`age_s`/`entities`：页面用 `age_s.toFixed(1)` 渲染年龄、
    用 `entities.join('、')` 渲染回滚目标，少一个键就是浏览器里当场炸。"""
    monkeypatch.setattr(af_undo, "DEFAULT_WINDOW_S", 60.0)
    store = UndoStore(str(tmp_path))
    store.record(
        "dep-live",
        {
            "switch.fan": {"state": "on", "attributes": {}},
            "light.study": {"state": "on", "attributes": {"brightness": 10}},
        },
    )
    client = TestClient(build_app(store_root=str(tmp_path)))

    items = client.get("/api/undo/available").json()["items"]
    assert len(items) == 1
    assert set(items[0]) == UNDO_ITEM_KEYS
    assert items[0]["deploy_id"] == "dep-live"
    assert items[0]["entities"] == ["light.study", "switch.fan"]
    age = items[0]["age_s"]
    assert isinstance(age, (int, float)) and not isinstance(age, bool)
