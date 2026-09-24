"""P0-9：令牌验证使用恒定时间比较（hmac.compare_digest），防止时序攻击。"""

import pytest

from autoforge.af_auth import TokenRegistry, TokenInfo


@pytest.fixture
def registry(tmp_path):
    reg = TokenRegistry(revoked_path=tmp_path / "revoked.json")
    # 直接注入测试令牌（TokenRegistry 从环境变量加载，测试用直接注入）
    reg._tokens["test-token-abc"] = TokenInfo(subject="tester", scopes={"read"})
    return reg


class TestConstantTimeComparison:
    def test_valid_token_accepted(self, registry):
        info = registry.authenticate("test-token-abc")
        assert info is not None
        assert info.subject == "tester"

    def test_invalid_token_rejected(self, registry):
        assert registry.authenticate("wrong-token") is None

    def test_empty_token_rejected(self, registry):
        assert registry.authenticate("") is None
        assert registry.authenticate(None) is None

    def test_prefix_match_not_accepted(self, registry):
        """前缀相同但不完全匹配的令牌必须被拒绝（compare_digest 不是 startswith）。"""
        assert registry.authenticate("test-token-ab") is None
        assert registry.authenticate("test-token-abcd") is None

    def test_case_sensitive(self, registry):
        """令牌大小写敏感。"""
        assert registry.authenticate("TEST-TOKEN-ABC") is None

    def test_revoked_token_rejected(self, registry):
        registry.revoke("test-token-abc")
        assert registry.authenticate("test-token-abc") is None

    def test_multiple_tokens(self, tmp_path):
        """多令牌时逐个恒定时间比较，都能正确匹配。"""
        reg = TokenRegistry(revoked_path=tmp_path / "revoked2.json")
        reg._tokens["token-one"] = TokenInfo(subject="s1", scopes=set())
        reg._tokens["token-two"] = TokenInfo(subject="s2", scopes=set())
        assert reg.authenticate("token-one").subject == "s1"
        assert reg.authenticate("token-two").subject == "s2"
        assert reg.authenticate("token-three") is None
