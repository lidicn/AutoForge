"""v1.4.0 凭据热重载单测（调研 §2.9：connection_revision 代数 + 掩码）。

覆盖：describe 只回掩码+长度（连末 4 位都不露）；update 原子写 + 代数自增 +
持久化；HATransport.call 比对代数刷新 token（免重启）。
"""

import pytest

from autoforge.af_adapters import HATransport
from autoforge.af_config import Config, get_config


def test_describe_masked(tmp_path):
    cfg = Config(tmp_path)
    cfg.update_credentials(ha_token="supersecret12345")
    desc = cfg.describe()
    assert desc["ha_token"] == "****len=16"
    # 绝不泄露任何明文片段
    assert "supersecret" not in desc["ha_token"]
    assert "12345" not in desc["ha_token"]
    assert len(desc["ha_token"]) < 20  # 仅掩码 + 长度


def test_revision_bumps_and_persists(tmp_path):
    cfg = Config(tmp_path)
    assert cfg.connection_revision == 0
    cfg.update_credentials(ha_token="abc")
    assert cfg.connection_revision == 1
    # 新实例读取到持久化的代数
    cfg2 = Config(tmp_path)
    assert cfg2.connection_revision == 1


def test_revision_triggers_token_reload(tmp_path):
    cfg = Config(tmp_path)
    cfg.update_credentials(ha_token="OLD")
    t = HATransport(base_url="http://127.0.0.1:1", cfg=cfg)
    assert t.token == "OLD"
    # 改凭据 → 代数自增
    cfg.update_credentials(ha_token="NEW")
    # call 首行比对代数，刷新 token（连接会失败，但不影响 token 刷新断言）
    t.call("light.turn_on", {"entity_id": "light.x"})
    assert t.token == "NEW"


def test_get_config_singleton(tmp_path):
    a = get_config(tmp_path)
    b = get_config(tmp_path)
    assert a is b
    a.update_credentials(ha_token="x")
    # 单例共享代数，热重载生效
    assert b.connection_revision == 1
