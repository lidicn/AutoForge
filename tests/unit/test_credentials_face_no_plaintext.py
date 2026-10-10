"""`/api/credentials` 不出明文——裁定 20261010 §五 Q3 追加的**补充裁定**的硬判据。

裁定原文要求的形状是「owner 面给明文，非 owner 面只给已配置/未配置摘要」，硬判据是
「用一枚非 owner 的 write 令牌调 `/api/credentials` ⇒ 拿不到 `ha_token`/`api_token` 明文」。

现读 HEAD 复测：`af_config.py` 全仓只有**一枚** `describe()`（`:188`），两个 token 键都过
`_mask()`（`:32-36`，形状 `****len=N`，docstring 自述「绝不泄露任何明文片段」）⇒ 判据以
**两面都掩码**这一更严的形状成立，故这里钉的是"更严的那一档不许退回去"。
owner 面要不要开明文属于放宽，**不自决**，偏差登记见执行记录 §二之一百零二。

四条腿都是按"反例先行"写的：先用 monkeypatch 证明摘掉掩码会被抓到，再钉现状。
"""
import pathlib

import pytest

from autoforge import af_config
from autoforge.af_config import Config, _mask, get_config

HA_MARK = "HA-PLAINTEXT-MARKER-9f2c"
API_MARK = "API-PLAINTEXT-MARKER-4b7d"

SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "autoforge"


@pytest.fixture()
def seeded(tmp_path):
    cfg = get_config(tmp_path)
    cfg.update_credentials(ha_token=HA_MARK, api_token=API_MARK)
    return cfg


def test_describe_never_contains_the_raw_tokens(seeded):
    body = seeded.describe()
    blob = repr(body)
    assert HA_MARK not in blob and API_MARK not in blob
    assert body["ha_token"] == f"****len={len(HA_MARK)}"
    assert body["api_token"] == f"****len={len(API_MARK)}"


@pytest.mark.parametrize("token", ["", "x", "abcdefg", "Bearer 1234567890", HA_MARK, API_MARK])
def test_mask_shape_keeps_length_and_leaks_no_plaintext_slice(token):
    """掩码只留长度这一件事，明文里**任意连续 4 字符**都不许出现在掩码值里。"""
    out = _mask(token)
    assert out == ("" if not token else f"****len={len(token)}")
    if not token:
        return
    assert token not in out
    plain_slices = {token[i : i + 4] for i in range(len(token) - 3)}
    masked_slices = {out[i : i + 4] for i in range(len(out) - 3)}
    assert not (plain_slices & masked_slices)


def test_the_leg_fires_if_masking_is_removed(monkeypatch, seeded):
    """自证：这条判据不是永远绿的装饰——把 `_mask` 换成恒等，明文就必须被抓到。"""
    monkeypatch.setattr(af_config, "_mask", lambda token: token)
    body = seeded.describe()
    assert body["ha_token"] == HA_MARK  # 护栏摘掉了 ⇒ 上面那条腿此刻会红
    with pytest.raises(AssertionError):
        assert HA_MARK not in repr(body)


def test_api_face_has_no_raw_token_reader():
    """HTTP 面不许直接读原始令牌：`af_api.py` 里两枚 getter 零命中（现读全仓只有
    适配器/实时链路/CLI 三处内部消费者，都不进响应体）。"""
    api_src = (SRC / "af_api.py").read_text(encoding="utf-8")
    assert "get_ha_token()" not in api_src
    assert "get_api_token()" not in api_src
    # describe() 是 HTTP 面唯一的凭据读数出口
    assert "get_config(store.root).describe()" in api_src
    assert isinstance(Config, type)
