"""《homesdk 接入四问》问题 1/2 的 AF 侧接线（DCD 20261001·§五 + ADM 联动执行计划 第 0 步②）。

锁四件事：
1. **主路径真的是机制层**——把 `homesdk.time` 换成桩，AF 的读数必须跟着变（不是"看起来接了"）；
2. **键名优先级 = `HOMESDK_TZ` → `TZ` → `AF_TZ` → `TZ_OFFSET_HOURS`**（裁定甲：IANA 名为准，旧键作过渡别名）；
3. **两条路径给同一个名字**——机制层缺席（老 wheel 里没有 `time` 模块）时，本仓 fallback 必须与
   机制层逐键等价；否则同一份 compose 文件在 NAS 镜像和本机给出两个墙钟，正是裁定要消灭的东西；
4. 退化可观测：`house_tz_status()["mechanism"]` 说得出是谁在供数。
"""
from __future__ import annotations

import pytest

from autoforge import af_time


@pytest.fixture(autouse=True)
def _clean_tz_env(monkeypatch):
    for key in ("HOMESDK_TZ", "TZ", "AF_TZ", "TZ_OFFSET_HOURS"):
        monkeypatch.delenv(key, raising=False)


class _StubTime:
    """替身机制层：只要 AF 真的把读取点交给 homesdk.time，这个名字就会冒出来。"""

    NAME = "America/Denver"

    @staticmethod
    def house_tz_name() -> str:
        return _StubTime.NAME

    @staticmethod
    def house_tz():
        from datetime import timezone

        return timezone.utc


def test_main_path_delegates_to_homesdk_time(monkeypatch):
    monkeypatch.setattr(af_time, "homesdk_time", lambda: _StubTime)
    monkeypatch.setenv("AF_TZ", "Asia/Tokyo")  # 机制层说了算：别名不该赢
    assert af_time.house_tz_name() == _StubTime.NAME
    assert af_time.house_tz_status()["tz_name"] == _StubTime.NAME


def test_mechanism_field_names_the_supplier(monkeypatch):
    assert af_time.house_tz_status()["mechanism"] == "homesdk.time"
    monkeypatch.setattr(af_time, "homesdk_time", lambda: None)
    assert af_time.house_tz_status()["mechanism"] == "af_local"


def test_homesdk_tz_beats_legacy_af_tz(monkeypatch):
    monkeypatch.setenv("AF_TZ", "Asia/Tokyo")
    monkeypatch.setenv("HOMESDK_TZ", "Europe/Berlin")
    assert af_time.house_tz_name() == "Europe/Berlin"
    assert af_time.house_tz_status()["source"] == "env:HOMESDK_TZ"


def test_legacy_af_tz_still_honoured_as_alias(monkeypatch):
    monkeypatch.setenv("AF_TZ", "Asia/Tokyo")
    assert af_time.house_tz_name() == "Asia/Tokyo"
    assert af_time.house_tz_status()["source"] == "env:AF_TZ"


@pytest.mark.parametrize(
    "key,value,expected",
    [
        ("HOMESDK_TZ", "Europe/Paris", "Europe/Paris"),
        ("TZ", "Asia/Singapore", "Asia/Singapore"),
        ("AF_TZ", "America/Chicago", "America/Chicago"),
        ("HOMESDK_TZ", "   ", af_time.TZ_FALLBACK_NAME),  # 空串等同未配置
    ],
)
def test_both_paths_agree_per_key(monkeypatch, key, value, expected):
    """机制层与本仓 fallback 必须给出同一个名字（分叉=事故，裁定 §五 的原话）。"""
    monkeypatch.setenv(key, value)
    with_mechanism = af_time.house_tz_name()
    monkeypatch.setattr(af_time, "homesdk_time", lambda: None)
    without_mechanism = af_time.house_tz_name()
    assert with_mechanism == without_mechanism == expected


def test_offset_hours_alias_converts_to_label(monkeypatch):
    """`TZ_OFFSET_HOURS` 是 MA 旧口径，收编成 IANA 之外的 `UTC±HH:MM` 形态。"""
    monkeypatch.setenv("TZ_OFFSET_HOURS", "-5")
    assert af_time.house_tz_name() == "UTC-05:00"


def test_out_of_range_offset_is_rejected(monkeypatch):
    """80 这种笔误不许留下"配好了"的幻觉——拒收后落回具名 fallback。"""
    monkeypatch.setenv("TZ_OFFSET_HOURS", "80")
    assert af_time.house_tz_name() == af_time.TZ_FALLBACK_NAME
    assert af_time.house_tz_status()["source"] == "fallback"


def test_load_tz_uses_mechanism_when_no_param(monkeypatch):
    from datetime import timezone

    monkeypatch.setattr(af_time, "homesdk_time", lambda: _StubTime)
    assert af_time.load_tz() is timezone.utc
    # 显式传参仍走本仓解析（af_predict / af_pretrigger 的按图覆盖语义不变）
    assert str(af_time.load_tz("Asia/Tokyo")) == "Asia/Tokyo"
