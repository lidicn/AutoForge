# -*- coding: utf-8 -*-
"""AF-820 判据 · grant 签发/续期 consume 先于参数校验（墨鉴 N-10，2026-11-20）。

缺陷形状：`grant_issue`/`grant_renew` 的 `_grant_channel_tag(consume=True)` 旧实现**先
`acs.consume` 计次**，随后 `create`/`renew` 才做参数校验（agent 空/保留名/risk 白名单/
范围空/ttl 越界）——任一抛错 ⇒ 长期码额度已耗、grant 未签（同 AF-817 族）。

修法（AF-PM 卡面）：`_grant_channel_tag` 只做**类型校验**（validate＋kind=="long"，⛔ 不
consume）；真正的 `acs.consume` 拆到调用方在 create/renew **成功之后**单独做
（`_grant_consume_after_success`），失败记 warning、⛔ 不回滚业务。

判据：
- A1 grant_issue 传非法 agent（空）⇒ create 抛 GrantError ⇒ 长期码 use_count 不变
- A2 grant_issue 传非法 action_risk ⇒ 不烧
- A3 grant_issue 成功 ⇒ use_count +1
- A4 grant_renew 成功 ⇒ use_count +1；失败（grant 不存在）⇒ 不烧
- A5 grant_revoke 不耗额度（consume=False）
- A6 源码钉：`_grant_channel_tag` 内不再有 acs.consume（计次挪到 _grant_consume_after_success）
"""

from __future__ import annotations

import inspect

import pytest

from autoforge import af_service as svc
from autoforge.af_auth import AuthCodeStore
from autoforge.af_service import ServiceError
from autoforge.af_store import GraphStore

GRANT_OWNER = "owner"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    store = GraphStore(str(tmp_path / "store"))
    return {"store": store, "acs": AuthCodeStore(store.root / ".auth" / "auth_codes.json")}


def _long_code(env):
    ac = env["acs"].create("long")
    assert ac.kind == "long"
    return ac.code


def _use_count(env, code) -> int:
    recs = env["acs"].list(reveal=True)
    return int(next((r for r in recs if r.get("code") == code), {}).get("use_count") or 0)


def _issue(env, code, **over):
    return svc.grant_issue(
        env["store"],
        agent=over.get("agent", "sp-agent"),
        auth_code=code,
        rooms=over.get("rooms", ["living"]),
        device_scope=over.get("device_scope"),
        action_risk=over.get("action_risk", "L1"),
        ttl_days=over.get("ttl_days", 30.0),
        caller=GRANT_OWNER,
        caller_is_owner=True,
    )


# ── A1：非法 agent ⇒ create 抛错 ⇒ 不烧 ─────────────────────────────────

def test_a1_invalid_agent_does_not_consume(env):
    code = _long_code(env)
    with pytest.raises(ServiceError):
        _issue(env, code, agent="")  # create 校验：agent 空 ⇒ 400
    assert _use_count(env, code) == 0, "create 参数校验失败 ⇒ 长期码⛔ 不该计次（修前先烧）"


# ── A2：非法 action_risk ⇒ 不烧 ────────────────────────────────────────

def test_a2_invalid_action_risk_does_not_consume(env):
    code = _long_code(env)
    with pytest.raises(ServiceError):
        _issue(env, code, action_risk="L7")  # 不在 GRANT_RISK_LEVELS ⇒ 400
    assert _use_count(env, code) == 0, "risk 白名单外 ⇒ 长期码⛔ 不该计次"


# ── A3：issue 成功 ⇒ 计一次 ───────────────────────────────────────────

def test_a3_issue_success_counts_once(env):
    code = _long_code(env)
    rec = _issue(env, code)
    assert rec["grant_id"].startswith("g-")
    assert _use_count(env, code) == 1, "issue 成功 ⇒ 长期码计一次"


# ── A4：renew 成功计次 / 失败不烧 ─────────────────────────────────────

def test_a4_renew_success_counts_but_failure_does_not(env):
    code = _long_code(env)
    rec = _issue(env, code)
    assert _use_count(env, code) == 1

    # renew 成功 ⇒ +1
    svc.grant_renew(env["store"], grant_id=rec["grant_id"], auth_code=code,
                    ttl_days=60.0, caller=GRANT_OWNER, caller_is_owner=True)
    assert _use_count(env, code) == 2, "renew 成功 ⇒ 计一次"

    # renew 不存在 ⇒ 抛错 ⇒ 不烧
    with pytest.raises(ServiceError):
        svc.grant_renew(env["store"], grant_id="g-no-such", auth_code=code,
                        caller=GRANT_OWNER, caller_is_owner=True)
    assert _use_count(env, code) == 2, "renew 目标不存在 ⇒ ⛔ 不该计次"


# ── A5：revoke 不耗额度 ──────────────────────────────────────────────

def test_a5_revoke_does_not_count(env):
    code = _long_code(env)
    rec = _issue(env, code)
    assert _use_count(env, code) == 1
    svc.grant_revoke(env["store"], grant_id=rec["grant_id"], auth_code=code,
                     caller=GRANT_OWNER, caller_is_owner=True)
    assert _use_count(env, code) == 1, "revoke 是缩权动作（consume=False）⇒ ⛔ 不耗额度"


# ── A6：源码钉 ───────────────────────────────────────────────────────

def test_a6_channel_tag_does_not_consume_inline():
    """`_grant_channel_tag` 内不得再有 acs.consume（计次已拆到 _grant_consume_after_success）。"""
    tag_src = inspect.getsource(svc._grant_channel_tag)
    assert "acs.consume(" not in tag_src, "_grant_channel_tag 不该再内联 consume（AF-820 拆出）"
    # 反面自证：计次确实存在（在 _grant_consume_after_success 里）
    consume_src = inspect.getsource(svc._grant_consume_after_success)
    assert ".consume(" in consume_src