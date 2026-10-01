"""DCD 20261001《DB六格与MA五题》§五 时区归属 —— AF 半边。

裁定要求「各仓不再各自硬编码 +8」「偏移由部署环境单一配置项提供」。`homesdk.time`
那半边尚未落地（0.3.0 源码树里没有 time 模块），所以本仓先把口径收口到
`af_time.house_tz_name()` 这一个读取点，`Asia/Shanghai` / +8 退化为**具名 fallback**。

这里锁的是「注入真的改变整条时间轴」，不是「某个字段变了一行」——MA 在同批回填里
专门点名过 import 时绑定快照那种「注入后毫无反应」的写法。
"""
from __future__ import annotations

import inspect
from datetime import datetime, timezone

from autoforge.af_time import (
    TZ_ENV_KEY,
    TZ_FALLBACK_NAME,
    SystemTimeSource,
    VirtualTimeSource,
    house_tz_name,
    house_tz_status,
    matches_at,
)


def test_unconfigured_uses_named_fallback(monkeypatch):
    monkeypatch.delenv(TZ_ENV_KEY, raising=False)
    assert house_tz_name() == TZ_FALLBACK_NAME
    st = house_tz_status()
    assert st["source"] == "fallback"
    assert st["resolved_by_name"] is True
    assert st["utc_offset"] == 8.0


def test_env_overrides_fallback(monkeypatch):
    monkeypatch.setenv(TZ_ENV_KEY, "America/New_York")
    assert house_tz_name() == "America/New_York"
    st = house_tz_status()
    assert st["source"] == f"env:{TZ_ENV_KEY}"
    assert st["resolved_by_name"] is True
    # 只锁「不再是 +8」；EST/EDT 不进断言，免得夏令时把测试变成随机红。
    assert st["utc_offset"] != 8.0


def test_blank_env_counts_as_unset(monkeypatch):
    """compose 里 `AF_TZ=` 是「未配置」，不是「配成了一个空时区」。"""
    monkeypatch.setenv(TZ_ENV_KEY, "   ")
    assert house_tz_name() == TZ_FALLBACK_NAME


def test_injection_moves_the_whole_timeline(monkeypatch):
    """反例锁：换一个部署时区，整条本地时间轴必须跟着走。"""
    monkeypatch.setenv(TZ_ENV_KEY, "Asia/Tokyo")
    tokyo = SystemTimeSource()
    monkeypatch.setenv(TZ_ENV_KEY, "America/New_York")
    ny = SystemTimeSource()

    assert tokyo.local_now().hour != ny.local_now().hour
    gap = (tokyo.local_now().utcoffset() - ny.local_now().utcoffset()).total_seconds() / 3600
    assert gap in (13.0, 14.0)  # +9 与 -4/-5，夏令时两态都接受


def test_time_trigger_judgement_follows_injection(monkeypatch):
    """time 触发（B3-AF-04 的判定点）在仿真侧同样随注入值移动。"""
    instant = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
    monkeypatch.setenv(TZ_ENV_KEY, "Asia/Tokyo")  # UTC 09:00 → 本地 18:00
    tokyo = VirtualTimeSource(start=instant)
    assert matches_at(tokyo.local_now(), "18:00") is True

    monkeypatch.setenv(TZ_ENV_KEY, "America/New_York")  # 本地凌晨，绝不该命中 18:00
    ny = VirtualTimeSource(start=instant)
    assert matches_at(ny.local_now(), "18:00") is False


def test_explicit_param_beats_env(monkeypatch):
    monkeypatch.setenv(TZ_ENV_KEY, "Asia/Tokyo")
    assert SystemTimeSource("Europe/Berlin").tz_name == "Europe/Berlin"
    assert house_tz_status("Europe/Berlin")["source"] == "param"


def test_unparsable_name_degrades_visibly(monkeypatch):
    """名字写错时行为沿用既有 fallback（不新增抛错面），但退化必须能被读到。"""
    monkeypatch.setenv(TZ_ENV_KEY, "Asia/Shangai")  # 故意笔误
    st = house_tz_status()
    assert st["tz_name"] == "Asia/Shangai"
    assert st["resolved_by_name"] is False


def test_no_repo_site_hardcodes_shanghai_anymore():
    """af_time / af_predict / af_pretrigger 的默认值一律为 None（= 读部署环境）。

     homesdk.time 落地后要把读取点换成机制层，这三行签名不再需要动。
    """
    from autoforge.af_predict import Predictor
    from autoforge.af_pretrigger import PreTriggerService

    for owner in (SystemTimeSource, VirtualTimeSource, Predictor, PreTriggerService):
        param = inspect.signature(owner.__init__).parameters.get("tz_name")
        if param is not None:
            assert param.default is None, f"{owner.__name__} 又写死了 {param.default!r}"
    assert inspect.signature(house_tz_status).parameters["tz_name"].default is None
