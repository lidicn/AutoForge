# -*- coding: utf-8 -*-
"""判据 · v1.29.0 第一批：执行授权（grant）模型 ＋【指令】面 · 卡名 AF-V129-G1-20261111。

依据：DCD `decisions/20261111-AF-对话操控执行授权模型-裁定.md`（六问全甲）§四 验收矩阵 10 行。
D 编号一律带**卡名 + 对象名**（群规⑭）：本文件的 Dn 只在这张表里成立，⛔ 跨卡引用。

| D | 矩阵行 | 判据（这一段钉死的那句话） |
|---|---|---|
| D1 | 10 | MVP 六字段在位；`time_window`/`rate_limit` **不在**（v1.29.1，⛔ 偷偷落档） |
| D2 | 1 | grant 内低危（灯/开关）放行，⛔ 不需要任何码 |
| D3 | 2 | grant 内中危（climate L2）在 `action_risk=L2` 下放行；缺省 L1 下**超授**只走确认 |
| D4 | 3 | 越界＝单次放行票：出票→用一次→第二次失效；**grant 本体一个字段都不改**（⛔ 确认即升级） |
| D5 | 4 | 门锁/燃气阀/热水器/安防面板 **硬拒**，⛔ 确认放行也过不去；排除清单同样硬拒 |
| D6 | 5 | L3（shell/python/hassio）硬拒；`create(action_risk="L3")` 直接拒签 |
| D7 | 4/5 一致性 | `GRANT_BANNED_DOMAINS ⊆ _L2_DOMAINS`（复用分级表，⛔ 另造一张） |
| D8 | 6 | 撤销立即拦新请求；**在途当前步不被半路打掉**（第一批无"其后步"对象，⛔ 假装已有） |
| D9 | 7 | 查询免 grant 但留痕 `QUERY_READ`（who/what/when/which agent，同形；⛔ 不抄状态值） |
| D10 | 1/2 | 服务面 `live_run(grant_id=…)` 全链：回执带 grant、审计带 grant、uses 计次 |
| D11 | 3 | 越界经 `live_run` 返回 `GRANT_CONFIRM_REQUIRED:` + ticket；带票重发才执行，票单次 |
| D12 | — | auth_code 与 grant_id **同传即拒**（⛔ 两把凭据并存的模糊归因） |
| D13 | — | 签发必须记明签发者（⛔ agent 自声明范围）；agent 保留名／空范围拒签 |
| D14 | — | 归属两条腿：owner 面全量⛔ 无 hidden_count；非 owner 面只看自己名下且必报 |
| D15 | — | 账本坏形 fail-closed（⛔ 读空当"没有授权"＝全场放行）；过期硬拒且可续期 |
| D16 | — | 两面对接：`af_grant` 工具在位、`af_live_run` 声明两枚 grant 参数、REST 四端点在场 |

⛔ 边界（判据自己也要诚实）：D8 只证「新请求被拦 + 在途当前步不为撤销中断」。
「取消其后未执行步」在第一批**没有对象**（TaskOrder/Scene 在第二、三批），
⛔ 本文件不假装验过那一行；第二批接入 TaskOrder 时补 D 编号并改写这条边界。
"""

from __future__ import annotations

import json
import time

import pytest

from autoforge import af_service as svc
from autoforge.af_adapters.base import L2_RISKY, _L2_DOMAINS, classify_action
from autoforge.af_audit import DEPLOY_AUDIT_FILE
from autoforge.af_grant import (
    GRANT_BANNED_DOMAINS,
    GRANT_CONFIRM_INVALID,
    GRANT_DEVICE_EXCLUDED,
    GRANT_DOMAIN_BANNED,
    GRANT_EXPIRED,
    GRANT_ISSUER_REQUIRED,
    GRANT_OUT_OF_SCOPE,
    GRANT_RISK_EXCEEDS,
    GRANT_RISK_L3,
    GRANT_REVOKED,
    GRANT_TTL_DAYS_DEFAULT,
    GrantError,
    GrantStore,
)
from autoforge.af_service import ServiceError
from autoforge.af_store import GraphStore

IR = {
    "ir_version": "0.2.1", "id": "grant_lamp", "name": "grant_lamp", "version": 1, "mode": "single",
    "nodes": [
        {"id": "t1", "kind": "on", "name": "t",
         "trigger": {"type": "state", "entity_id": "binary_sensor.presence", "to": "on"}},
        {"id": "d1", "kind": "do", "name": "d", "adapter": "ha", "action": "light.turn_on",
         "params": {"entity_id": "light.lamp"}},
        {"id": "p1", "kind": "pass", "name": "p"},
    ],
    "edges": [{"from": "t1", "to": "d1", "kind": "then"},
              {"from": "d1", "to": "p1", "kind": "then"}],
}

ROOMS = {"light.lamp": "主卧室", "switch.fan": "主卧室", "climate.ac": "主卧室"}


def _room_of(entity_id: str) -> str:
    """注入的区域取值（产品面真源是 `DeviceCatalog.area_of`，那条腿在 D9b 单独验）。"""
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
    monkeypatch.delenv("AUTOFORGE_UNDO", raising=False)
    monkeypatch.setattr(svc, "LIVE_TRANSPORT_FACTORY", lambda ha_url, token: _NoopTransport())
    monkeypatch.setattr(svc, "_grant_room_of", lambda store: _room_of)
    store = GraphStore(str(tmp_path / "store"))
    return {"store": store, "gs": GrantStore(store.root / ".auth" / "grants.json")}


def _grant(env, **kw):
    params = dict(agent="agent-a", issued_by="owner", rooms=["主卧室"])
    params.update(kw)
    return env["gs"].create(**params)


def _check(env, grant, actions, targets, *, agent="agent-a", now=None):
    return env["gs"].check(grant, agent=agent, actions=actions, targets=targets,
                           room_of=_room_of, now=now)


# ── D1 ─────────────────────────────────────────────────────────────────
def test_d1_mvp_six_fields_only(env):
    rec = _grant(env, action_risk="L1")
    for field in ("agent", "rooms", "device_scope", "action_risk", "expires_at", "revoke"):
        assert field in rec, f"MVP 六字段缺 {field}"
    assert rec["device_scope"] == {"include": [], "exclude": []}
    for later in ("time_window", "rate_limit"):
        assert later not in rec, f"{later} 属 v1.29.1，⛔ 第一批偷偷落档"
    assert abs((rec["expires_at"] - time.time()) - GRANT_TTL_DAYS_DEFAULT * 86400.0) < 120


# ── D2 / D3：分级与超授 ────────────────────────────────────────────────
def test_d2_low_risk_allowed_without_code(env):
    grant = _grant(env, action_risk="L1")
    check = _check(env, grant, [("ha", "light.turn_on", {})], ["light.lamp"])
    assert check.ok and not check.needs_confirm, f"grant 内低危必须直接放行：{check.to_dict()}"
    assert check.risk == "L1"


def test_d3_mid_risk_needs_l2_grant_and_degrades_to_confirm(env):
    low = _grant(env, action_risk="L1")
    check = _check(env, low, [("ha", "climate.set_temperature", {})], ["climate.ac"])
    assert not check.ok and check.needs_confirm, "缺省 L1 下中危必须走越界确认（⛔ 自动放行）"
    assert any(d["code"] == GRANT_RISK_EXCEEDS for d in check.denied)

    high = _grant(env, agent="agent-a", action_risk="L2")
    check2 = _check(env, high, [("ha", "climate.set_temperature", {})], ["climate.ac"])
    assert check2.ok and not check2.needs_confirm, f"grant L2 覆盖中危应直接放行：{check2.to_dict()}"
    assert check2.risk == L2_RISKY


# ── D4：越界确认＝单次放行，grant 不变 ─────────────────────────────────
def test_d4_confirm_ticket_is_single_use_and_never_upgrades(env):
    grant = _grant(env, rooms=["主卧室"])
    before = json.dumps(grant, sort_keys=True, ensure_ascii=False)
    check = _check(env, grant, [("ha", "light.turn_on", {})], ["light.lamp", "light.other_room"])
    assert not check.ok and check.needs_confirm
    assert "light.other_room" in check.out_of_scope
    assert any(d["code"] == GRANT_OUT_OF_SCOPE for d in check.denied)

    ticket = env["gs"].issue_confirm(check)
    assert env["gs"].consume_confirm(ticket["ticket"], agent="agent-a",
                                     targets=list(check.out_of_scope),
                                     grant_id=grant["grant_id"]) is True
    assert env["gs"].consume_confirm(ticket["ticket"], agent="agent-a",
                                     targets=list(check.out_of_scope),
                                     grant_id=grant["grant_id"]) is False
    assert json.dumps(env["gs"].get(grant["grant_id"]), sort_keys=True,
                      ensure_ascii=False) == before, "确认⛔ 不得改动 grant 本体"


def test_d4_confirm_requires_matching_scope(env):
    grant = _grant(env)
    check = _check(env, grant, [("ha", "light.turn_on", {})], ["light.other_room"])
    ticket = env["gs"].issue_confirm(check)
    assert env["gs"].consume_confirm(ticket["ticket"], agent="agent-a",
                                     targets=["light.somewhere_else"],
                                     grant_id=grant["grant_id"]) is False
    with pytest.raises(GrantError) as ei:
        env["gs"].issue_confirm(_check(env, grant, [("ha", "light.turn_on", {})], ["light.lamp"]))
    assert ei.value.code == GRANT_CONFIRM_INVALID


# ── D5 / D6 / D7：禁入集 ───────────────────────────────────────────────
@pytest.mark.parametrize("domain", sorted(GRANT_BANNED_DOMAINS))
def test_d5_banned_domains_hard_deny(env, domain):
    grant = _grant(env, action_risk="L2")
    check = _check(env, grant, [("ha", f"{domain}.turn_on", {})], [f"{domain}.dev"])
    assert check.hard_denied, f"{domain} 必须硬拒（⛔ 确认放行）"
    assert any(d["code"] == GRANT_DOMAIN_BANNED for d in check.denied)
    assert not check.needs_confirm, "高危不能落到'确认一次就放行'那一档"


def test_d5_exclude_list_is_hard_deny_not_confirm(env):
    grant = _grant(env, device_scope={"include": [], "exclude": ["light.lamp"]})
    check = _check(env, grant, [("ha", "light.turn_on", {})], ["light.lamp"])
    assert check.hard_denied and any(d["code"] == GRANT_DEVICE_EXCLUDED for d in check.denied)
    assert not check.needs_confirm


@pytest.mark.parametrize("action", ["shell_command.turn_on", "python_script.run", "hassio.restart"])
def test_d6_l3_hard_deny(env, action):
    grant = _grant(env, action_risk="L2")
    check = _check(env, grant, [("ha", action, {})], ["cover.x"])
    assert check.hard_denied and any(d["code"] == GRANT_RISK_L3 for d in check.denied)
    with pytest.raises(GrantError) as ei:
        _grant(env, action_risk="L3")
    assert ei.value.code == GRANT_RISK_L3


def test_d7_banned_set_reuses_the_risk_table():
    assert GRANT_BANNED_DOMAINS <= _L2_DOMAINS, "禁入四元集必须落在上游 L2 表里（⛔ 另造一张）"
    for domain in GRANT_BANNED_DOMAINS:
        assert classify_action("ha", f"{domain}.turn_on") == L2_RISKY


# ── D8：撤销 ───────────────────────────────────────────────────────────
def test_d8_revoke_blocks_new_requests_but_keeps_inflight_step(env):
    grant = _grant(env, action_risk="L1")
    assert _check(env, grant, [("ha", "light.turn_on", {})], ["light.lamp"]).ok, "撤销前应可执行"
    env["gs"].revoke(grant["grant_id"], reason="test")
    with pytest.raises(GrantError) as ei:
        env["gs"].resolve(grant["grant_id"], agent="agent-a")
    assert ei.value.code == GRANT_REVOKED
    after = _check(env, env["gs"].get(grant["grant_id"]),
                   [("ha", "light.turn_on", {})], ["light.lamp"])
    assert after.hard_denied and any(d["code"] == GRANT_REVOKED for d in after.denied)
    # 在途当前步：撤销不清已经发生过的执行记账（⛔ 把发生过的动作从账上抹掉）
    assert env["gs"].bump_use(grant["grant_id"]) == 1


# ── D9：查询留痕 ───────────────────────────────────────────────────────
def test_d9_query_is_free_but_traced(env, monkeypatch):
    monkeypatch.setattr(svc, "_catalog", lambda store: type(
        "C", (), {"get_state": lambda self, eid: {"entity_id": eid, "state": "on"}})())
    out = svc.catalog_state(env["store"], "light.lamp", caller="agent-a")
    assert out["state"] == "on"
    ledger = (env["store"].root / DEPLOY_AUDIT_FILE).read_text(encoding="utf-8")
    rows = [json.loads(line) for line in ledger.splitlines() if line.strip()]
    hits = [r for r in rows if r.get("type") == "QUERY_READ"]
    assert hits, f"查询必须留痕（同形 QUERY_READ），实得：{[r.get('type') for r in rows]}"
    data = hits[-1]["data"]
    assert data["subject"] == "agent-a" and data["entity_id"] == "light.lamp"
    assert "state" not in json.dumps(data, ensure_ascii=False), "⛔ 台账不抄设备状态值"


def test_d9b_room_resolution_reads_catalog_snapshot(tmp_path):
    """真源腿：实体→区域走 `.catalog/catalog.json`（离线可读，⛔ 打网络）。"""
    store = GraphStore(str(tmp_path / "store2"))
    cat = store.root / ".catalog" / "catalog.json"
    cat.parent.mkdir(parents=True, exist_ok=True)
    cat.write_text(json.dumps({"version": 1, "freshness": "",
                               "entities": {"light.lamp": {"area": "主卧室"}}}), encoding="utf-8")
    room_of = svc._grant_room_of(store)
    assert room_of("light.lamp") == "主卧室"
    assert room_of("light.unknown") == ""


# ── D10 / D11 / D12：服务面全链 ────────────────────────────────────────
def test_d10_live_run_grant_channel_end_to_end(env):
    grant = _grant(env, action_risk="L1")
    out = svc.live_run(IR, ["light.lamp"], None, confirm=True, store=env["store"],
                       grant_id=grant["grant_id"], caller="agent-a")
    assert out["ok"] is True
    assert out["authorized_by"] == f"grant:{grant['grant_id']}"
    assert out["grant"]["grant_id"] == grant["grant_id"] and out["grant"]["confirmed"] is False
    assert out["grant"]["uses"] == 1
    ledger = (env["store"].root / DEPLOY_AUDIT_FILE).read_text(encoding="utf-8")
    rows = [json.loads(line) for line in ledger.splitlines() if line.strip()]
    exec_rows = [r for r in rows if r.get("type") == "LIVE_RUN_EXECUTED"]
    assert exec_rows and exec_rows[-1]["data"]["grant"]["grant_id"] == grant["grant_id"], \
        "执行留痕要带 which grant（⛔ 另造 GRANT_EXECUTED 第二套账）"


def test_d10b_code_path_has_no_grant_block(env):
    from autoforge.af_auth import AuthCodeStore

    code = AuthCodeStore(env["store"].root / ".auth" / "auth_codes.json").create("short").code
    out = svc.live_run(IR, ["light.lamp"], None, confirm=True, store=env["store"], auth_code=code)
    assert "grant" not in out, "码通道的回执不该长出 grant 块（形状要能一眼分出通道）"


def test_d11_out_of_scope_returns_ticket_then_executes_once(env):
    grant = _grant(env, rooms=["别的房间"])
    with pytest.raises(ServiceError) as ei:
        svc.live_run(IR, ["light.lamp"], None, confirm=True, store=env["store"],
                     grant_id=grant["grant_id"], caller="agent-a")
    assert ei.value.status == 403
    text = str(ei.value)
    assert text.startswith("GRANT_CONFIRM_REQUIRED: "), f"确认回执要有固定前缀：{text[:60]}"
    payload = json.loads(text.split(": ", 1)[1])
    assert payload["ticket"] and payload["question"] and "light.lamp" in payload["out_of_scope"]

    ok = svc.live_run(IR, ["light.lamp"], None, confirm=True, store=env["store"],
                      grant_id=grant["grant_id"], caller="agent-a",
                      grant_confirm=payload["ticket"])
    assert ok["ok"] is True and ok["grant"]["confirmed"] is True
    with pytest.raises(ServiceError) as ei2:
        svc.live_run(IR, ["light.lamp"], None, confirm=True, store=env["store"],
                     grant_id=grant["grant_id"], caller="agent-a",
                     grant_confirm=payload["ticket"])
    assert "放行票无效" in str(ei2.value)


def test_d11b_banned_domain_never_gets_a_ticket(env):
    grant = _grant(env, action_risk="L2")
    ir = json.loads(json.dumps(IR))
    # 用真实存在的 L2 服务（lock.unlock）+ 显式 requires_confirm：让静态扫描放行，
    # 这样被验的才是 grant 的禁入集本身，而不是扫描器的另一道闸。
    ir["nodes"][1]["action"] = "lock.unlock"
    ir["nodes"][1]["params"] = {"entity_id": "lock.front_door"}
    ir["nodes"][1]["requires_confirm"] = True
    ir["nodes"][1]["canary"] = {"duration": "5s", "auto_rollback": True}
    with pytest.raises(ServiceError) as ei:
        svc.live_run(ir, ["lock.front_door"], None, confirm=True, store=env["store"],
                     grant_id=grant["grant_id"], caller="agent-a")
    assert "GRANT_DOMAIN_BANNED" in str(ei.value)
    assert not str(ei.value).startswith("GRANT_CONFIRM_REQUIRED:"), "高危不给确认这条路"


def test_d12_grant_and_code_together_are_rejected(env):
    grant = _grant(env)
    with pytest.raises(ServiceError) as ei:
        svc.live_run(IR, ["light.lamp"], None, confirm=True, store=env["store"],
                     grant_id=grant["grant_id"], auth_code="123456", caller="agent-a")
    assert ei.value.status == 403 and "不可同传" in str(ei.value)


# ── D13 / D14 / D15：签发面与读面 ──────────────────────────────────────
def test_d13_issuer_required_and_reserved_agent(env):
    with pytest.raises(GrantError) as ei:
        env["gs"].create(agent="agent-a", issued_by="", rooms=["主卧室"])
    assert ei.value.code == GRANT_ISSUER_REQUIRED
    with pytest.raises(GrantError):
        env["gs"].create(agent="owner", issued_by="owner", rooms=["主卧室"])
    with pytest.raises(GrantError) as ei2:
        env["gs"].create(agent="agent-a", issued_by="owner")
    assert ei2.value.code == "GRANT_SCOPE_EMPTY"


def test_d14_list_two_legs_owner_and_agent(env):
    env["gs"].create(agent="agent-a", issued_by="owner", rooms=["主卧室"])
    env["gs"].create(agent="agent-b", issued_by="owner", rooms=["主卧室"])
    owner_view = svc.grant_list(env["store"], caller="owner", caller_is_owner=True)
    assert owner_view["count"] == 2 and "hidden_count" not in owner_view
    agent_view = svc.grant_list(env["store"], caller="agent-a", caller_is_owner=False)
    assert agent_view["count"] == 1 and agent_view["hidden_count"] == 1
    assert agent_view["items"][0]["agent"] == "agent-a"


def test_d15_corrupt_store_fails_closed(env):
    path = env["store"].root / ".auth" / "grants.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ServiceError) as ei:
        svc.grant_list(env["store"], caller="owner", caller_is_owner=True)
    assert ei.value.status == 500 and "fail-closed" in str(ei.value)


def test_d15b_expired_grant_hard_denied_and_renewable(env):
    later = time.time() + 2 * 86400
    grant = _grant(env, ttl_days=1)
    check = _check(env, env["gs"].get(grant["grant_id"]), [("ha", "light.turn_on", {})],
                   ["light.lamp"], now=later)
    assert check.hard_denied and any(d["code"] == GRANT_EXPIRED for d in check.denied)
    renewed = env["gs"].renew(grant["grant_id"], ttl_days=30, by="owner")
    assert renewed["expires_at"] > later


# ── D16：两面对接 ──────────────────────────────────────────────────────
def test_d16_mcp_and_http_surfaces_are_wired():
    from autoforge import af_mcp

    names = [t[0] for t in af_mcp.TOOLS]
    assert "af_grant" in names, "af_grant 工具必须在 TOOLS 里注册"
    live = next(t for t in af_mcp.TOOLS if t[0] == "af_live_run")
    declared = set((live[2].get("properties") or {}))
    assert {"grant_id", "grant_confirm"} <= declared, "grant 两参必须在 inputSchema 里声明（声明即契约）"
    api_src = open(svc.__file__.replace("af_service.py", "af_api.py"), encoding="utf-8").read()
    for path in ('"/api/grants"', "/api/grants/receipts", "/revoke", "/renew"):
        assert path in api_src, f"REST 端点缺 {path}"
