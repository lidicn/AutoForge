"""G9 判据腿：A 类访问密钥走旧名回退时必须落 TOKEN_ENV_LEGACY_NAME 事件。

背景（2026-10-24）：新名 AF_ACCESS_TOKENS/AF_ACCESS_TOKEN 与旧名
AUTOFORGE_TOKENS/AUTOFORGE_API_TOKEN 并存，新名优先、旧名兜退。但运维看着
"配置齐了"，实际可能跑在旧名上（新名忘填、拼错）——此前零告警。
修复：旧名被读到时落一条 legacy 事件（含 legacy_name/new_name/both_names_set），
data 绝不含令牌值。

⚠️ 环境变量一律走 `monkeypatch.setenv`，⛔ 不用裸 `os.environ[...] = ...`。
2026-10-24 实测教训：本文件初版用裸赋值，autouse fixture 的
`monkeypatch.delenv` 只恢复 monkeypatch 自己做的修改，对裸赋值不负责 ⇒
本文件跑完后 `AUTOFORGE_TOKENS`／`AF_ACCESS_TOKENS` 残留在进程环境里，
后续 test_v0_8_auth / test_v1_4_token_expiry / test_dcd_20261004_auth_limits
共 19 个测试读到"新名优先"而拿不到自己设的旧名令牌 ⇒ authenticate 返回 None
⇒ 全红（单跑全绿，只有按 pytest 收集顺序跑才红——顺序污染的真形状）。
"""

from __future__ import annotations

import json

import pytest

from autoforge.af_audit import AuditLog, TOKEN_ENV_LEGACY_NAME
from autoforge.af_auth import TokenRegistry

_KEYS = (
    "AF_ACCESS_TOKEN",
    "AUTOFORGE_API_TOKEN",
    "AF_ACCESS_TOKENS",
    "AUTOFORGE_TOKENS",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """前置清理：把四个键都抹掉，让每个测试从"什么都没配"起步。"""
    for k in _KEYS:
        monkeypatch.delenv(k, raising=False)
    yield


def _reg() -> TokenRegistry:
    return TokenRegistry(audit=AuditLog())


def test_new_single_name_only_emits_nothing(monkeypatch):
    monkeypatch.setenv("AF_ACCESS_TOKEN", "new-secret-1")
    reg = _reg()
    assert reg._audit.of_type(TOKEN_ENV_LEGACY_NAME) == []


def test_legacy_single_name_emits_event(monkeypatch):
    monkeypatch.setenv("AUTOFORGE_API_TOKEN", "old-secret-1")
    reg = _reg()
    evs = reg._audit.of_type(TOKEN_ENV_LEGACY_NAME)
    assert len(evs) == 1
    assert evs[0].data["legacy_name"] == "AUTOFORGE_API_TOKEN"
    assert evs[0].data["new_name"] == "AF_ACCESS_TOKEN"


def test_both_multi_names_set_flags_both(monkeypatch):
    monkeypatch.setenv(
        "AF_ACCESS_TOKENS", json.dumps({"t1": {"subject": "x", "scopes": ["read"]}})
    )
    monkeypatch.setenv(
        "AUTOFORGE_TOKENS", json.dumps({"t2": {"subject": "y", "scopes": ["read"]}})
    )
    reg = _reg()
    evs = reg._audit.of_type(TOKEN_ENV_LEGACY_NAME)
    assert len(evs) == 1
    assert evs[0].data["legacy_name"] == "AUTOFORGE_TOKENS"
    assert evs[0].data["both_names_set"] is True


def test_no_env_at_all_emits_nothing():
    reg = _reg()
    assert reg._audit.of_type(TOKEN_ENV_LEGACY_NAME) == []


def test_legacy_event_data_never_contains_token_value(monkeypatch):
    secret = "top-secret-legacy-value-12345"
    monkeypatch.setenv("AUTOFORGE_API_TOKEN", secret)
    reg = _reg()
    ev = reg._audit.of_type(TOKEN_ENV_LEGACY_NAME)[0]
    dumped = json.dumps(ev.to_dict(), ensure_ascii=False)
    assert secret not in dumped


def test_legacy_single_multi_pair_emits_one_event_per_pair(monkeypatch):
    monkeypatch.setenv("AUTOFORGE_API_TOKEN", "old-single")
    monkeypatch.setenv(
        "AUTOFORGE_TOKENS", json.dumps({"t": {"subject": "z", "scopes": ["read"]}})
    )
    reg = _reg()
    names = {e.data["legacy_name"] for e in reg._audit.of_type(TOKEN_ENV_LEGACY_NAME)}
    assert names == {"AUTOFORGE_API_TOKEN", "AUTOFORGE_TOKENS"}
