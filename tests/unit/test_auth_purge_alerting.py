"""G8 判据腿：过期令牌被剔除时必须落 TOKEN_EXPIRED_PURGED 审计事件。

背景（2026-10-24 实测）：`_purge_expired_locked` 此前静默 pop——审计里查不到
"某枚登录令牌何时到期被清"，与"从未签发过"无从区分。修复要求：dead 非空才落，
data 只带 count/subjects/at，绝不带令牌明文。
"""

from __future__ import annotations

import json
import time

from autoforge.af_audit import AuditLog, TOKEN_EXPIRED_PURGED
from autoforge.af_auth import TokenInfo, TokenRegistry


def _reg() -> TokenRegistry:
    return TokenRegistry(audit=AuditLog())


def test_issue_expired_token_emits_one_purge_event():
    reg = _reg()
    tok = reg.issue_for_agent("agent_kaiwen", ttl_s=3600)
    reg._tokens[tok].expires_at = time.time() - 1  # 模拟它已到期
    reg._purge_expired_locked()
    evs = reg._audit.of_type(TOKEN_EXPIRED_PURGED)
    assert len(evs) == 1
    assert evs[0].data["count"] == 1
    assert "agent_kaiwen" in evs[0].data["subjects"]


def test_issue_healthy_token_emits_nothing():
    reg = _reg()
    reg.issue_for_agent("agent_kaiwen", ttl_s=3600)  # 3600s 后才过期，本次无 dead
    assert reg._audit.of_type(TOKEN_EXPIRED_PURGED) == []


def test_one_purge_removing_three_dead_counts_three():
    reg = _reg()
    now = time.time()
    for i in range(3):
        reg._tokens[f"expired_{i}"] = TokenInfo(
            subject=f"subj_{i}", scopes={"read"}, expires_at=now - 1
        )
    reg._purge_expired_locked()
    evs = reg._audit.of_type(TOKEN_EXPIRED_PURGED)
    assert len(evs) == 1
    assert evs[0].data["count"] == 3
    assert set(evs[0].data["subjects"]) == {"subj_0", "subj_1", "subj_2"}


def test_event_is_json_serializable():
    reg = _reg()
    tok = reg.issue_for_agent("agent_kaiwen", ttl_s=3600)
    reg._tokens[tok].expires_at = time.time() - 1
    reg._purge_expired_locked()
    ev = reg._audit.of_type(TOKEN_EXPIRED_PURGED)[0]
    json.dumps(ev.to_dict(), ensure_ascii=False)  # 不抛即过


def test_event_data_never_contains_token_plaintext():
    reg = _reg()
    plain = reg.issue_for_agent("agent_kaiwen", ttl_s=3600)
    reg._tokens[plain].expires_at = time.time() - 1
    reg._purge_expired_locked()
    ev = reg._audit.of_type(TOKEN_EXPIRED_PURGED)[0]
    dumped = json.dumps(ev.to_dict(), ensure_ascii=False)
    assert plain not in dumped
