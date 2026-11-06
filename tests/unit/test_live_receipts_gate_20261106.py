"""v1.28 R3「最近单发回执后端面」判据（卡名 `AF卡_live_receipts`，2026-11-06）。

**端点**：`GET /api/live/run/receipts?limit=20` → `{ok, count, items:[{at, graph, actions,
verdict, code_mask}], hidden_count?}`
**源**：`<store>/deploy_audit.jsonl` 的 `LIVE_RUN_EXECUTED` 事件（已落盘，⛔ 不新建持久化面）

**归属形状**（PM 2026-11-06 22:5x 核准的 **list 面纪律**，非单对象 404 同形）：
- owner 面播全量、**不报** `hidden_count`；
- 非 owner 面只见 `subject == caller` 的、**必报** `hidden_count`＋`scope`。

**牙（群规⑧）**：两条归属腿用**同一份台账两个不同 subject** 做差分——若实现漏了过滤，
非 owner 腿会看到 2 条；若漏了 hidden_count，那一腿直接 KeyError。掩码腿钉"码掩码形如
`auth_code:xx***`、回读面出现不得是明文码"。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from autoforge import af_service as svc
from autoforge.af_adapters import CallResult
from autoforge.af_api import build_app
from autoforge.af_audit import DEPLOY_AUDIT_FILE
from fastapi.testclient import TestClient

ROOM = "study"

LIVE_IR: dict = {
    "automations": [
        {
            "ir_version": "0.2.1",
            "id": "a_receipt",
            "name": "书房空调单发",
            "version": 1,
            "mode": "restart",
            "nodes": [
                {"id": "t1", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.hall", "to": "on"}},
                {"id": "d1", "kind": "do", "adapter": "ha", "action": "light.turn_off",
                 "params": {"entity_id": "light.study"}, "on_error": {"default": "pass"}},
            ],
            "edges": [{"from": "t1", "to": "d1", "kind": "then"}],
        }
    ]
}


def _event(subject: str, *, at: str, graph: list[str], actions: int, verdict: str = "executed") -> dict[str, Any]:
    return {
        "type": "LIVE_RUN_EXECUTED",
        "at": at,
        "message": "live_run executed",
        "data": {
            "authorized_by": "auth_code:12***",  # 已是脱敏标签（真码从不进事件）
            "writes": ["light.a"] * actions,
            "actions": actions,
            "verdict": {"verdict": verdict, "dispatched": actions, "failed": 0},
            "graph": graph,
            "subject": subject,
        },
    }


def _write_ledger(root: Path, events: list[dict[str, Any]]) -> Path:
    path = root / DEPLOY_AUDIT_FILE
    path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n", encoding="utf-8")
    return path


# ── D1 归属两腿（同一份台账、两个 subject）──────────────────────────────

def test_d1_owner_sees_all_and_no_hidden_count(tmp_path):
    _write_ledger(tmp_path, [
        _event("alice", at="2026-11-06T22:00:00+00:00", graph=["书房空调单发"], actions=2),
        _event("bob", at="2026-11-06T22:05:00+00:00", graph=["客厅灯"], actions=1),
    ])
    out = svc.live_run_receipts(str(tmp_path), caller="alice", caller_is_owner=True)
    assert out["ok"] is True and out["count"] == 2, "owner 面必须全量（含别人的）"
    assert "hidden_count" not in out, "owner 面⛔ 不报 hidden_count（群规⑩正反形状）"
    # 新→旧
    assert [i["graph"][0] for i in out["items"]] == ["客厅灯", "书房空调单发"]


def test_d1_non_owner_sees_only_own_with_hidden_count(tmp_path):
    """反证腿：若漏过滤 ⇒ 看到 2 条；若漏 hidden_count ⇒ 该键缺失。两条都在一条断言里。"""
    _write_ledger(tmp_path, [
        _event("alice", at="2026-11-06T22:00:00+00:00", graph=["书房空调单发"], actions=2),
        _event("bob", at="2026-11-06T22:05:00+00:00", graph=["客厅灯"], actions=1),
    ])
    out = svc.live_run_receipts(str(tmp_path), caller="bob", caller_is_owner=False)
    assert out["count"] == 1, f"非 owner 面只该见自己名下 1 条，实得 {out['count']}"
    assert out["items"][0]["graph"] == ["客厅灯"]
    assert out["hidden_count"] == 1, "缺口必须可见（被人看走 1 条）"
    assert out["scope"] == "owner:bob"


# ── D2 掩码腿（群规⑤：码值不进回读面）─────────────────────────────────

def test_d2_code_mask_is_masked_and_no_raw_code_leaks(tmp_path):
    _write_ledger(tmp_path, [
        _event("alice", at="2026-11-06T22:00:00+00:00", graph=["书房空调单发"], actions=2),
    ])
    out = svc.live_run_receipts(str(tmp_path), caller="alice", caller_is_owner=True)
    mask = out["items"][0]["code_mask"]
    assert mask.startswith("auth_code:") and mask.endswith("***"), f"码掩码形状不对：{mask!r}"
    blob = json.dumps(out, ensure_ascii=False)
    # 反证腿：掩码里只许两位前缀——⛔ 出现完整 6/8 位码
    import re
    assert not re.search(r"\b\d{6,8}\b", blob), "回读面出现了疑似完整授权码"


# ── D3 空列表 / 读失败两格分开 ─────────────────────────────────────────

def test_d3_empty_ledger_is_empty_not_error(tmp_path):
    out = svc.live_run_receipts(str(tmp_path), caller="alice", caller_is_owner=True)
    assert out["ok"] is True and out["items"] == [] and out["count"] == 0
    assert "read_error" not in out, "文件还没生成＝空，⛔ 不是读失败"
    # 无 store 才是"读不了"，且如实报
    assert svc.live_run_receipts(None)["read_error"] == "no_store"


# ── D4 limit 边界（默认 20 / 下限回默认 / 上限钳 100）─────────────────

def test_d4_limit_bounds(tmp_path):
    events = [
        _event("alice", at=f"2026-11-06T22:00:{i:02d}+00:00", graph=[f"图{i}"], actions=1)
        for i in range(30)
    ]
    _write_ledger(tmp_path, events)
    assert svc.live_run_receipts(str(tmp_path), caller="alice", caller_is_owner=True, limit=1)["count"] == 1
    assert svc.live_run_receipts(str(tmp_path), caller="alice", caller_is_owner=True, limit=0)["count"] == 20, \
        "limit<=0 回默认 20（⛔ 不是 0 条）"
    assert svc.live_run_receipts(str(tmp_path), caller="alice", caller_is_owner=True, limit=500)["count"] == 30
    assert svc.LIVE_RECEIPTS_MAX == 100, "上限常量＝100（PM 卡 §四）"


# ── D5 端到端：真机下发事件真的带 graph/subject/actions ─────────────────

@pytest.fixture()
def live_gate(monkeypatch, tmp_path):
    monkeypatch.setenv("AF_ALLOW_NOAUTH", "1")
    monkeypatch.setenv("AUTOFORGE_LIVE_ENABLED", "1")
    monkeypatch.setenv("AUTOFORGE_HA_TOKEN", "srv-side-token")
    monkeypatch.delenv("AUTOFORGE_UNDO", raising=False)

    class _FT:
        def __init__(self) -> None:
            self.states = {"light.study": ("on", {})}
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

    transports: list[_FT] = []

    def factory(ha_url, token):
        transports.append(_FT())
        return transports[-1]

    monkeypatch.setattr(svc, "LIVE_TRANSPORT_FACTORY", factory)
    return transports


def _code(root: Path) -> str:
    from autoforge.af_auth import AuthCodeStore
    rec = AuthCodeStore(root / ".auth" / "auth_codes.json").create("short", ttl_minutes=30)
    return getattr(rec, "code", None) or rec["code"]


def test_d5_rest_live_run_then_receipts_endpoint(tmp_path, live_gate):
    """端到端：真机下发一次 ⇒ 回执端点读得出这条（图名/动作数/verdict/掩码齐全）。"""
    client = TestClient(build_app(store_root=str(tmp_path)))
    r = client.post("/api/live/run", json={
        "ir": LIVE_IR, "live_allow": ["light.study"],
        "events": [{"entity_id": "binary_sensor.hall", "state": "on"}],
        "confirm": True, "auth_code": _code(tmp_path),
    })
    assert r.status_code == 200, r.text
    out = client.get("/api/live/run/receipts").json()
    assert out["ok"] is True and out["count"] == 1, out
    item = out["items"][0]
    assert item["graph"] == ["书房空调单发"], "图名必须进回执（R3 卡点名）"
    assert item["actions"] == 1, "动作数＝写目标数"
    assert item["verdict"] == "executed", "verdict 取 dispatch_result.verdict"
    assert item["code_mask"].endswith("***"), "码掩码原样带回"
    # 事件里确实落了 subject（缺它就是非 owner 过滤与 hidden_count 都无从算起）
    raw = (tmp_path / DEPLOY_AUDIT_FILE).read_text(encoding="utf-8")
    assert '"subject"' in raw, "LIVE_RUN_EXECUTED 事件必须带 subject（卡 §四：缺则同批补上）"
