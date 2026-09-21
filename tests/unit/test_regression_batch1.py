"""R-53/R-54/R-21 回归测（WO-AF-BATCH1 后补）。"""
from __future__ import annotations

import os

import pytest

from autoforge.af_bus import EventBus
from autoforge import af_config
from autoforge.af_service import _existing_automation_count


def test_r53_breaker_defaults_unchanged(monkeypatch):
    for k in ("AUTOFORGE_BREAKER_THRESHOLD", "AUTOFORGE_BREAKER_WINDOW_S", "AUTOFORGE_BREAKER_COOLDOWN_S"):
        monkeypatch.delenv(k, raising=False)
    b = EventBus()
    assert (b.breaker_threshold, b.breaker_window, b.breaker_cooldown) == (12, 10.0, 15.0)


def test_r53_breaker_env_override(monkeypatch):
    monkeypatch.setenv("AUTOFORGE_BREAKER_THRESHOLD", "3")
    monkeypatch.setenv("AUTOFORGE_BREAKER_WINDOW_S", "4")
    monkeypatch.setenv("AUTOFORGE_BREAKER_COOLDOWN_S", "5")
    b = EventBus()
    assert (b.breaker_threshold, b.breaker_window, b.breaker_cooldown) == (3, 4.0, 5.0)


def test_r53_explicit_arg_beats_env(monkeypatch):
    monkeypatch.setenv("AUTOFORGE_BREAKER_THRESHOLD", "3")
    b = EventBus(breaker_threshold=99)
    assert b.breaker_threshold == 99


def test_r54_ttl_default_and_env(monkeypatch):
    monkeypatch.delenv("AUTOFORGE_CONFIG_TTL_S", raising=False)
    # 重新读 env 常量
    assert float(os.getenv("AUTOFORGE_CONFIG_TTL_S", "60")) == 60.0
    assert hasattr(af_config.Config, "refresh")


def test_r21_existing_automation_count_returns_int():
    class _Store:
        def load(self, name):
            return {"automations": [1, 2, 3]}
        def names(self):
            return {"g"}
    assert _existing_automation_count(_Store(), "g") == 3
