# -*- coding: utf-8 -*-
"""AF-821 判据 · granted_rank 坏值 fail-closed（墨鉴 N-14，2026-11-20）。

缺陷形状：`af_grant.GrantStore.check` 的 `granted_rank=_RISK_ORDER.get(granted,-1)`——
grants.json 手工编辑/跨版本写入让 `action_risk` 不在白名单（如 `"L9"`）时 rank=-1 ⇒
`:512` 的 `risk_exceeds = _RISK_ORDER.get(risk,3) > granted_rank >= 0` 链式比较第二项**恒假**
⇒ L2 越界**不再要求确认**（软拒静默消失；L3 与禁入域仍硬拒）。同文件 `_load`／`create`
都 fail-closed，唯 check 静默降档。

修法（AF-PM 卡面）：check 入口对 action_risk 坏值具名硬拒——`GRANT_RISK_INVALID` 入
`HARD_DENY_CODES`，坏值记录拒绝执行（⛔ 静默 -1）。

判据：
- A1 坏值 action_risk 的 grant ⇒ check.hard_denied 且 denied 含 GRANT_RISK_INVALID
- A2 修前形状不再存在：坏值 grant 上的 L2 请求**不会**放行（needs_confirm=False 且 ok=True 的旧形状）
- A3 `_grant_authorize_execution` 端到端 ⇒ ServiceError 403（具名）
- A4 反面自证：正常 L1 记录的 L1 请求仍放行（⛔ 不是把所有 grant 都判死）
"""

from __future__ import annotations

import json

import pytest

from autoforge import af_service as svc
from autoforge.af_grant import (
    GRANT_RISK_INVALID,
    GrantStore,
)
from autoforge.af_service import ServiceError
from autoforge.af_store import GraphStore

ROOMS = {"light.lamp": "主卧室", "climate.ac": "主卧室"}


def _room_of(entity_id: str) -> str:
    return ROOMS.get(str(entity_id), "")


@pytest.fixture()
def env(tmp_path):
    store = GraphStore(str(tmp_path / "store"))
    gs = GrantStore(store.root / ".auth" / "grants.json")
    return {"store": store, "gs": gs}


def _fresh_grant(env, agent="agent-a", rooms=("主卧室",)) -> dict:
    """先 create 一张**合法** grant，再手工把 action_risk 改成坏值（模拟跨版本写入）。"""
    rec = env["gs"].create(agent=agent, issued_by="owner", rooms=list(rooms),
                           action_risk="L1")
    data = json.loads((env["store"].root / ".auth" / "grants.json").read_text(encoding="utf-8"))
    data["grants"][rec["grant_id"]]["action_risk"] = "L9"  # ⛔ 白名单外的坏值
    (env["store"].root / ".auth" / "grants.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data["grants"][rec["grant_id"]]


# ── A1：坏值 ⇒ 具名硬拒 ──────────────────────────────────────────────

def test_a1_bad_risk_is_hard_denied_with_named_code(env):
    grant = _fresh_grant(env)
    check = env["gs"].check(grant, agent="agent-a",
                            actions=[("ha", "light.turn_on", {})],
                            targets=["light.lamp"], room_of=_room_of)
    assert check.hard_denied, "坏值 action_risk 必须 fail-closed"
    codes = {d["code"] for d in check.denied}
    assert GRANT_RISK_INVALID in codes, f"deny 里必须有 GRANT_RISK_INVALID：{codes}"


# ── A2：修前形状不再存在 ─────────────────────────────────────────────

def test_a2_l2_on_bad_record_never_silently_passes(env):
    """修前：rank=-1 ⇒ risk_exceeds 恒 False ⇒ L2 请求 needs_confirm=False 且硬拒表不命中。
    修后：坏值记录一律硬拒。"""
    grant = _fresh_grant(env)
    check = env["gs"].check(grant, agent="agent-a",
                            actions=[("ha", "climate.set_hvac_mode", {})],  # L2 动作
                            targets=["climate.ac"], room_of=_room_of)
    assert check.ok is False, "坏值记录上 L2 请求⛔ 不许 ok=True（修前的静默放行形状）"
    assert check.needs_confirm is False, "坏值记录⛔ 不许落成『确认放行』（软拒通道）"


# ── A3：服务层端到端 403 ─────────────────────────────────────────────

def test_a3_service_layer_translates_to_named_403(env, monkeypatch):
    monkeypatch.setenv("AUTOFORGE_LIVE_ENABLED", "1")
    monkeypatch.setenv("AUTOFORGE_HA_TOKEN", "srv-side-token")
    monkeypatch.delenv("HA_LONG_LIVED_TOKEN", raising=False)
    monkeypatch.setattr(svc, "_grant_room_of", lambda store: _room_of)
    store = env["store"]
    grant = _fresh_grant(env, agent="sp-agent")
    graph_ir = {
        "ir_version": "0.2.1", "id": "af821", "name": "af821", "version": 1, "mode": "single",
        "nodes": [
            {"id": "t1", "kind": "on", "name": "t",
             "trigger": {"type": "state", "entity_id": "light.lamp", "to": "on"}},
            {"id": "d1", "kind": "do", "name": "d", "adapter": "ha", "action": "light.turn_on",
             "params": {"entity_id": "light.lamp"}},
            {"id": "p1", "kind": "pass", "name": "p"},
        ],
        "edges": [{"from": "t1", "to": "d1", "kind": "then"},
                  {"from": "d1", "to": "p1", "kind": "then"}],
    }
    with pytest.raises(ServiceError) as ei:
        svc.live_run(graph_ir, ["light.lamp"], None, confirm=True, store=store,
                     grant_id=grant["grant_id"], caller="sp-agent")
    assert ei.value.status == 403
    assert "GRANT_RISK_INVALID" in str(ei.value) or "执行授权拒绝" in str(ei.value)


# ── A4：反面自证（⛔ 不是把所有 grant 判死） ──────────────────────────

def test_a4_valid_record_still_passes(env):
    rec = env["gs"].create(agent="agent-a", issued_by="owner", rooms=["主卧室"],
                           action_risk="L1")
    check = env["gs"].check(env["gs"].resolve(rec["grant_id"], agent="agent-a"),
                            agent="agent-a",
                            actions=[("ha", "light.turn_on", {})],
                            targets=["light.lamp"], room_of=_room_of)
    assert check.ok is True, "正常 L1 记录的 L1 请求仍放行（⛔ 误伤回归）"