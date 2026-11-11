# -*- coding: utf-8 -*-
"""判据 · v1.29.0 第二批：【任务】TaskOrder · 卡名 AF-V129-G2-20261111。

依据：DCD 20261111 Q5 甲（TaskOrder 落 AF）／Q6（一次性 vs 重复）／§2.3 甲（撤销下半行）
＋§四矩阵第 6／8／9 行；设计稿 v5 §二.场景2＋§四.2-6b（output 三通道）。

| T | 矩阵/S 卡 | 判据（这一段钉死的那句话） |
|---|---|---|
| T1 | 字段 | 入册记录含 `order_id/agent/due_at/steps/output/grant_id/status`，**⛔ 无 every/repeat/cron** |
| T2 | 生命周期 | 到点 tick ⇒ 逐步执行 ⇒ `done`；活动池移出；审计有 CREATED＋FINISHED |
| T3 | 红线②（入册期） | 越界步在**创建**就拒（任务到点无人可问，⛔ 整单放行） |
| T4 | 红线②（执行期） | grant 在执行前被撤销 ⇒ 一步都不发起，状态 `cancelled` |
| T5 | **矩阵第 6 行整行** | 撤销打断＝当前步完成、**其后未执行步全部取消**、状态 cancelled、留痕可查 |
| T6 | 终态即销 | done 后活动池空；`order_receipts` 仍能查到（留痕不灭） |
| T7 | output 三通道 | tts 无桥⇒failed(no_bridge)、bark⇒unwired＋fallback_unavailable、mimo⇒readable |
| T8 | 红线③哑任务 | 查询型且全部通道未接线 ⇒ 拒绝创建 |
| T9 | 红线①变相常驻 | 步里出现 `every`/`repeat`/`cron`/`trigger` ⇒ 拒收 |
| T10 | 取消面 | pending 可取消＋留痕；已销再取消 ⇒ 404 |
| T11 | 崩溃续跑 | `running` 卡死超时 ⇒ 从断点续跑，**不重跑已回执的步** |
| T12 | 两面对接 | `af_order` 工具在位；REST 四端点在场 |
"""

from __future__ import annotations

import json
import time

import pytest

from autoforge import af_service as svc
from autoforge.af_audit import DEPLOY_AUDIT_FILE
from autoforge.af_grant import GrantStore
from autoforge.af_service import ServiceError
from autoforge.af_store import GraphStore
from autoforge.af_task_order import ORDER_FILE_REL, TaskOrderError, TaskOrderStore

ROOMS = {"light.lamp": "主卧室", "switch.fan": "主卧室"}


def _room_of(entity_id: str) -> str:
    return ROOMS.get(str(entity_id), "")


class _NoopTransport:
    def __call__(self, action, params):
        from autoforge.af_adapters.base import CallResult
        return CallResult(success=True, data={}, error=None)

    def all_states(self):
        return {}

    def get_state(self, entity_id):
        return None


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOFORGE_LIVE_ENABLED", "1")
    monkeypatch.setenv("AUTOFORGE_HA_TOKEN", "srv-side-token")
    monkeypatch.delenv("HA_LONG_LIVED_TOKEN", raising=False)
    monkeypatch.setattr(svc, "LIVE_TRANSPORT_FACTORY", lambda ha_url, token: _NoopTransport())
    monkeypatch.setattr(svc, "_grant_room_of", lambda store: _room_of)
    store = GraphStore(str(tmp_path / "store"))
    gs = GrantStore(store.root / ".auth" / "grants.json")
    grant = gs.create(agent="agent-a", issued_by="owner", rooms=["主卧室"], action_risk="L1")
    return {"store": store, "gs": gs, "grant": grant, "os": TaskOrderStore(store.root.joinpath(*ORDER_FILE_REL))}


def _steps(n=2):
    return [
        {"adapter": "ha", "action": "light.turn_on",
         "params": {"entity_id": "light.lamp" if i % 2 == 0 else "switch.fan"}}
        for i in range(n)
    ]


def _create(env, *, steps=None, delay_s=0, output=None, grant_id=None, agent="agent-a"):
    return svc.order_create(
        env["store"],
        steps=steps if steps is not None else _steps(),
        agent=agent,
        output=output if output is not None else {"channels": ["mimo"], "text": "好了"},
        grant_id=grant_id if grant_id is not None else env["grant"]["grant_id"],
        delay_s=delay_s,
        caller="agent-a",
    )


def _audit_rows(env):
    path = env["store"].root / DEPLOY_AUDIT_FILE
    if not path.is_file():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


# ── T1 ─────────────────────────────────────────────────────────────────
def test_t1_record_shape_has_no_repeat_fields(env):
    rec = _create(env)
    for field in ("order_id", "agent", "due_at", "steps", "output", "grant_id", "status"):
        assert field in rec, f"任务单缺字段 {field}"
    assert rec["status"] == "pending" and rec["steps"] and rec["due_kind"] == "delay"
    blob = json.dumps(rec, ensure_ascii=False)
    for forbidden in ("every", "repeat", "cron", "trigger"):
        assert f'"{forbidden}"' not in blob, f"⛔ 任务单不许有 {forbidden}（一次性语义）"


# ── T2 ─────────────────────────────────────────────────────────────────
def test_t2_tick_runs_steps_then_expires_from_pool(env):
    rec = _create(env, steps=_steps(2))
    out = svc.order_tick(env["store"], now=rec["due_at"] + 1)
    assert out["ran"] == 1, out
    assert out["results"][0]["status"] == "done", out["results"][0]
    assert svc.order_list(env["store"], caller="agent-a")["count"] == 0, "终态必须移出活动池"
    receipt = out["results"][0]["record"]["receipt"]
    assert len(receipt["steps"]) == 2, receipt
    assert all(s["ok"] for s in receipt["steps"]), receipt
    rows = [r["type"] for r in _audit_rows(env)]
    assert "TASK_ORDER_CREATED" in rows and "TASK_ORDER_FINISHED" in rows, rows


# ── T3：入册期逐动作过 grant ───────────────────────────────────────────
def test_t3_out_of_scope_step_rejected_at_creation(env):
    narrow = env["gs"].create(agent="agent-a", issued_by="owner", rooms=["书房"], action_risk="L1")
    with pytest.raises(ServiceError) as ei:
        _create(env, grant_id=narrow["grant_id"])
    assert ei.value.status == 403 and ("越界" in str(ei.value) or "越过" in str(ei.value))
    assert svc.order_list(env["store"], caller="agent-a")["count"] == 0, "被拒的单子不许入册"


def test_t3b_high_risk_step_rejected_at_creation(env):
    with pytest.raises(ServiceError) as ei:
        _create(env, steps=[{"adapter": "ha", "action": "lock.unlock",
                             "params": {"entity_id": "lock.front_door"}}])
    assert ei.value.status == 403 and "GRANT_DOMAIN_BANNED" in str(ei.value)


# ── T4：执行前 grant 已被撤销 ⇒ 不发起 ────────────────────────────────
def test_t4_revoked_before_tick_runs_nothing(env):
    rec = _create(env)
    env["gs"].revoke(env["grant"]["grant_id"], reason="test")
    out = svc.order_tick(env["store"], now=rec["due_at"] + 1)
    assert out["results"][0]["status"] == "cancelled", out["results"][0]
    assert "TASK_ORDER_CANCELLED" in [r["type"] for r in _audit_rows(env)]
    assert svc.order_list(env["store"], caller="agent-a")["count"] == 0


# ── T5：撤销打断（矩阵第 6 行整行）─────────────────────────────────────
def test_t5_revoke_mid_flight_cancels_remaining_steps(env, monkeypatch):
    real_live_run = svc.live_run
    calls: list[str] = []

    def _revoke_after_first(*args, **kwargs):
        result = real_live_run(*args, **kwargs)
        calls.append("step")
        if len(calls) == 1:
            # 第一步刚落地 ⇒ 用户撤销：其后未执行步必须全部取消
            env["gs"].revoke(env["grant"]["grant_id"], reason="用户撤销")
        return result

    monkeypatch.setattr(svc, "live_run", _revoke_after_first)
    rec = _create(env, steps=_steps(3))
    out = svc.order_tick(env["store"], now=rec["due_at"] + 1)
    result = out["results"][0]
    assert result["status"] == "cancelled", result
    assert len(calls) == 1, f"撤销后⛔ 再发起后续步，实发起 {len(calls)} 步"
    record = result["record"]
    assert len(record["receipt"]["steps"]) == 1, "已完成的当前步回执要保留"
    assert record["result"]["cancelled_from_step"] == 2
    rows = [r for r in _audit_rows(env) if r["type"] == "TASK_ORDER_CANCELLED"]
    assert rows and rows[-1]["data"]["reason"] == "grant_revoked_mid_flight", rows


# ── T6：终态即销，留痕不灭 ─────────────────────────────────────────────
def test_t6_receipts_survive_terminal_state(env):
    rec = _create(env, steps=_steps(1))
    svc.order_tick(env["store"], now=rec["due_at"] + 1)
    assert env["os"].get(rec["order_id"]) is None
    receipts = svc.order_receipts(env["store"], caller="owner", caller_is_owner=True)
    ids = {item["order_id"] for item in receipts["items"]}
    assert rec["order_id"] in ids, receipts


# ── T7：output 三通道如实记态 ─────────────────────────────────────────
def test_t7_output_channels_record_honest_status(env, monkeypatch):
    monkeypatch.setattr("autoforge.af_mqtt_bridge.current_bridge", lambda: None)
    rec = _create(env, steps=_steps(1), output={"channels": ["tts", "bark", "mimo"], "text": "做完了"})
    out = svc.order_tick(env["store"], now=rec["due_at"] + 1)
    outputs = out["results"][0]["record"]["receipt"]["output"]
    by_channel = {o["channel"]: o for o in outputs}
    assert by_channel["tts"]["status"] == "failed" and by_channel["tts"]["error"] == "no_bridge"
    assert by_channel["bark"]["status"] == "fallback_unavailable", "TTS 失败时兜底链必须显式说「未接线」"
    assert by_channel["mimo"]["status"] == "readable"
    # 交付失败不改任务状态：动作已经做完了
    assert out["results"][0]["status"] == "done"


# ── T8：哑任务 ─────────────────────────────────────────────────────────
def test_t8_query_order_needs_a_wired_channel(env):
    with pytest.raises(ServiceError) as ei:
        _create(env, output={"channels": ["bark"], "kind": "query"})
    assert "TASK_ORDER_NEEDS_OUTPUT" in str(ei.value), str(ei.value)


# ── T9：变相常驻 ───────────────────────────────────────────────────────
@pytest.mark.parametrize("key", ["every", "repeat", "cron", "trigger"])
def test_t9_repeat_fields_are_rejected(env, key):
    steps = _steps(1)
    steps[0][key] = "5m"
    with pytest.raises(ServiceError) as ei:
        _create(env, steps=steps)
    assert "TASK_ORDER_NO_TRIGGER_FIELD" in str(ei.value), str(ei.value)


# ── T10：取消面 ────────────────────────────────────────────────────────
def test_t10_cancel_pending_then_not_found(env):
    rec = _create(env, delay_s=3600)
    out = svc.order_cancel(env["store"], order_id=rec["order_id"], caller="agent-a", reason="不做了")
    assert out["status"] == "cancelled" and out["cancel"]["reason"] == "不做了"
    with pytest.raises(ServiceError) as ei:
        svc.order_cancel(env["store"], order_id=rec["order_id"], caller="agent-a")
    assert ei.value.status == 404, "已销单子再取消必须 404（不是 500）"
    assert "TASK_ORDER_CANCELLED" in [r["type"] for r in _audit_rows(env)]


# ── T11：崩溃续跑 ──────────────────────────────────────────────────────
def test_t11_stale_running_order_resumes_from_breakpoint(env):
    rec = _create(env, steps=_steps(3))
    # 模拟"跑到第 1 步崩了"：置 running、写 1 条回执、started_at 拉老
    data = env["os"]._load()
    stored = data["orders"][rec["order_id"]]
    stored["status"] = "running"
    stored["started_at"] = "2026-01-01T00:00:00Z"
    stored["receipt"]["steps"] = [{"index": 1, "action": "light.turn_on", "entity": "light.lamp",
                                   "ok": True, "at": "2026-01-01T00:00:00Z"}]
    env["os"]._save(data)
    out = svc.order_tick(env["store"], now=time.time())
    result = out["results"][0]
    assert result["status"] == "done", result
    steps = result["record"]["receipt"]["steps"]
    assert len(steps) == 3, f"续跑要接着断点跑完，⛔ 重跑第 1 步；实得 {len(steps)} 条回执"
    assert [s["index"] for s in steps] == [1, 2, 3]


# ── T12：两面对接 ──────────────────────────────────────────────────────
def test_t12_surfaces_are_wired():
    from autoforge import af_mcp

    names = [t[0] for t in af_mcp.TOOLS]
    assert "af_order" in names
    tool = next(t for t in af_mcp.TOOLS if t[0] == "af_order")
    assert {"action", "steps", "grant_id", "at", "delay_s", "output"} <= set(tool[2]["properties"])
    api_src = open(svc.__file__.replace("af_service.py", "af_api.py"), encoding="utf-8").read()
    for path in ('"/api/orders"', "/api/orders/receipts", "/cancel"):
        assert path in api_src, f"REST 端点缺 {path}"
