"""v2.0.1 / PR 0.1-b：secret 加载单测。

验证凭据读取优先级：secret 文件 > 环境变量，缺失/空时回退。
"""

from __future__ import annotations

import os

from autoforge.af_secrets import load_secret, load_secret_or_none, secret_path


def test_secret_file_preferred(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOFORGE_SECRET_DIR", str(tmp_path))
    monkeypatch.setenv("AUTOFORGE_HA_TOKEN", "env-value")
    (tmp_path / "AUTOFORGE_HA_TOKEN").write_text("secret-value", encoding="utf-8")
    assert load_secret("AUTOFORGE_HA_TOKEN") == "secret-value"


def test_secret_missing_falls_back_to_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOFORGE_SECRET_DIR", str(tmp_path))
    monkeypatch.setenv("AUTOFORGE_HA_TOKEN", "env-value")
    assert load_secret("AUTOFORGE_HA_TOKEN") == "env-value"


def test_secret_and_env_missing_returns_default(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOFORGE_SECRET_DIR", str(tmp_path))
    monkeypatch.delenv("AUTOFORGE_HA_TOKEN", raising=False)
    assert load_secret("AUTOFORGE_HA_TOKEN", "dflt") == "dflt"
    assert load_secret("AUTOFORGE_HA_TOKEN") == ""


def test_secret_dir_override(tmp_path, monkeypatch):
    custom = tmp_path / "secrets"
    custom.mkdir()
    monkeypatch.setenv("AUTOFORGE_SECRET_DIR", str(custom))
    monkeypatch.delenv("AUTOFORGE_API_TOKEN", raising=False)
    (custom / "AUTOFORGE_API_TOKEN").write_text("x", encoding="utf-8")
    assert load_secret("AUTOFORGE_API_TOKEN") == "x"
    assert secret_path("AUTOFORGE_API_TOKEN") == custom / "AUTOFORGE_API_TOKEN"


def test_secret_empty_falls_back_to_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOFORGE_SECRET_DIR", str(tmp_path))
    monkeypatch.setenv("AUTOFORGE_HA_TOKEN", "env-value")
    (tmp_path / "AUTOFORGE_HA_TOKEN").write_text("   ", encoding="utf-8")
    assert load_secret("AUTOFORGE_HA_TOKEN") == "env-value"


def test_load_secret_or_none(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOFORGE_SECRET_DIR", str(tmp_path))
    monkeypatch.delenv("AUTOFORGE_HA_TOKEN", raising=False)
    assert load_secret_or_none("AUTOFORGE_HA_TOKEN") is None
    (tmp_path / "AUTOFORGE_HA_TOKEN").write_text("v", encoding="utf-8")
    assert load_secret_or_none("AUTOFORGE_HA_TOKEN") == "v"
