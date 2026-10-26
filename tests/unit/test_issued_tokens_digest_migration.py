"""第四批 · 批 2 判据腿：存储层明文键 → SHA-256 摘要 + principal_kind。

11 腿（路线图 §3.4）。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from autoforge.af_auth import TokenRegistry, _digest


def _reg(tmp_path: Path) -> TokenRegistry:
    auth = tmp_path / ".auth"
    auth.mkdir()
    return TokenRegistry(
        revoked_path=auth / "revoked.json",
        issued_path=auth / "issued_tokens.json",
    )


def test_1_persist_writes_digest_key_not_plaintext(tmp_path):
    reg = _reg(tmp_path)
    tok = reg.issue_for_agent("alice")
    on_disk = json.loads((tmp_path / ".auth" / "issued_tokens.json").read_text(encoding="utf-8"))
    keys = list(on_disk.keys())
    assert len(keys) == 1
    # 键必须是 64 位十六进制摘要，不是明文令牌
    assert keys[0] != tok
    assert len(keys[0]) == 64 and all(c in "0123456789abcdef" for c in keys[0])
    # 值里不得回写明文令牌
    assert tok not in json.dumps(on_disk)


def test_2_authenticate_plaintext_still_hits(tmp_path):
    reg = _reg(tmp_path)
    tok = reg.issue_for_agent("bob")
    info = reg.authenticate(tok)
    assert info is not None and info.subject == "bob"


def test_3_revoke_persists_digest_not_plaintext(tmp_path):
    reg = _reg(tmp_path)
    tok = reg.issue_for_agent("carol")
    reg.revoke(tok)
    rev = json.loads((tmp_path / ".auth" / "revoked.json").read_text(encoding="utf-8"))
    assert rev == [_digest(tok)]
    assert tok not in rev


def test_4_after_revoke_authenticate_returns_none(tmp_path):
    reg = _reg(tmp_path)
    tok = reg.issue_for_agent("dave")
    reg.revoke(tok)
    assert reg.authenticate(tok) is None


def test_5_login_issued_principal_kind_is_login(tmp_path):
    reg = _reg(tmp_path)
    tok = reg.issue_for_agent("owner", principal_kind="login")
    info = reg.authenticate(tok)
    assert info.principal_kind == "login"


def test_6_pair_issued_principal_kind_is_agent(tmp_path):
    reg = _reg(tmp_path)
    tok = reg.issue_for_agent("pair-agent", ttl_s=0)
    info = reg.authenticate(tok)
    assert info.principal_kind == "agent"


def test_7_legacy_record_subject_owner_infers_login(tmp_path):
    auth = tmp_path / ".auth"
    auth.mkdir(exist_ok=True)
    # 存量记录：无 principal_kind 字段，subject=owner（旧登录令牌）
    (auth / "issued_tokens.json").write_text(
        json.dumps({_digest("af_oldlogin"): {"subject": "owner", "scopes": ["read", "write", "live"]}}),
        encoding="utf-8",
    )
    reg = TokenRegistry(revoked_path=auth / "revoked.json", issued_path=auth / "issued_tokens.json")
    info = reg.authenticate("af_oldlogin")
    assert info is not None and info.principal_kind == "login"


def test_8_legacy_record_subject_agent_infers_agent(tmp_path):
    auth = tmp_path / ".auth"
    auth.mkdir(exist_ok=True)
    (auth / "issued_tokens.json").write_text(
        json.dumps({_digest("af_oldpair"): {"subject": "some-agent", "scopes": ["read"]}}),
        encoding="utf-8",
    )
    reg = TokenRegistry(revoked_path=auth / "revoked.json", issued_path=auth / "issued_tokens.json")
    info = reg.authenticate("af_oldpair")
    assert info is not None and info.principal_kind == "agent"


def _run_migrate(store_root: Path, apply: bool):
    cmd = [sys.executable, "scripts/migrate_issued_tokens_digest.py",
           "--store-root", str(store_root)]
    if apply:
        cmd.append("--apply")
    return subprocess.run(cmd, capture_output=True, text=True, cwd=str(Path(__file__).resolve().parents[2]),
                         env={**__import__("os").environ, "PYTHONPATH": "src"})


def test_9_migrate_dry_run_writes_nothing(tmp_path):
    auth = tmp_path / ".auth"
    auth.mkdir()
    (auth / "issued_tokens.json").write_text(
        json.dumps({"af_plain_old": {"subject": "owner", "scopes": ["read"]}}), encoding="utf-8"
    )
    before = (auth / "issued_tokens.json").read_text(encoding="utf-8")
    r = _run_migrate(tmp_path, apply=False)
    assert r.returncode == 0, r.stdout + r.stderr
    after = (auth / "issued_tokens.json").read_text(encoding="utf-8")
    assert after == before  # dry-run 不改文件
    assert "af_plain_old" in after  # 仍是明文键（未迁移）


def test_10_migrate_corrupt_file_refuses_and_preserves(tmp_path):
    auth = tmp_path / ".auth"
    auth.mkdir()
    (auth / "issued_tokens.json").write_text("{ not json ", encoding="utf-8")
    before = (auth / "issued_tokens.json").read_text(encoding="utf-8")
    r = _run_migrate(tmp_path, apply=True)
    assert r.returncode != 0
    # 原文件未被改坏
    assert (auth / "issued_tokens.json").read_text(encoding="utf-8") == before


def test_11_issue_for_agent_rejects_developer_self_escalation(tmp_path):
    reg = _reg(tmp_path)
    with pytest.raises(ValueError):
        reg.issue_for_agent("owner", principal_kind="developer")
    # developer 档令牌不得经此入口落库
    assert reg.subjects() == []


def test_issued_index_capped_without_reads(tmp_path):
    """BOUNDED_CACHES 腿：_issued 纯写不读也受 MAX_ISSUED 封顶（最旧被淘汰）。"""
    reg = _reg(tmp_path)
    cap = reg.MAX_ISSUED
    # 写 cap+5 枚（短 TTL 让它们都可被按过期时间排序淘汰）；全程不读/authenticate。
    for i in range(cap + 5):
        reg.issue_for_agent(f"subject_{i}", ttl_s=100)
    assert len(reg._issued) <= cap
    # 最旧的那几枚已被淘汰：subject_0..4 不在索引里。
    for i in range(5):
        assert all(info.subject != f"subject_{i}" for info in reg._issued.values())
