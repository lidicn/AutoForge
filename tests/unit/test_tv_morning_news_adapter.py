# -*- coding: utf-8 -*-
"""`tv` 适配器判据腿（AF-PM 卡 2026-11-15 · G1 早间新闻待命件 · 契约 v2.x）。

对齐 TVPilot 现读的真身（`tvpilot/af_morning_news.py` 384 行）：

- `trigger` L240-248：执行中重复调用 ⇒ `{accepted: False, reason: "already_running", request_id, last}`
- `_terminal` L212-238：九键终态（`s3_s4` 只在 `result ∈ (iptv_ok, iptv_fail)` 时非空、`screen_on_after` 硬编码 None）
- `_finish` L271-275：`total_ms` 由它补成数值
- `RESULTS` L37-38：六值（活跃四 + 休眠两）

本文件全部离线：客户端是桩，⛔ 不连 TVPilot／TV／broker、⛔ 不读凭据、⛔ 零网络。
"""

from __future__ import annotations

import socket
import uuid
from urllib.error import HTTPError, URLError

import pytest

from autoforge.af_adapters.base import AdapterError, CallResult
from autoforge.af_adapters.tv import (
    ACTIVE_RESULTS,
    ADM_ERR_AUTH_REQUIRED,
    ADM_ERR_INTERNAL,
    ADM_ERR_PAYLOAD_INVALID,
    ADM_ERR_PEER_OFFLINE,
    ADM_ERR_UPSTREAM_TIMEOUT,
    DEFAULT_TV_TRANSPORT_TIMEOUT,
    DEFECT_DORMANT_RESULT,
    DORMANT_RESULTS,
    PARAMS_ALLOWED,
    RESULTS,
    TERMINAL_KEYS,
    TOOL_MORNING_NEWS,
    TvAdapter,
    action_of,
    defect_of_result,
    map_terminal,
)

#: AF 侧相关键（不是对端终态键）：数据面 = 这两枚 + 九键，⛔ 不多收不少收。
AF_KEYS = ("action", "request_id")


class _StubClient:
    """MCP 客户端桩：记下每次 `call(tool, **kwargs)`，按需返回回执或抛异常。"""

    def __init__(self, payload=None, *, raises: BaseException | None = None) -> None:
        self.payload = payload
        self.raises = raises
        self.calls: list[tuple[str, dict]] = []

    def call(self, tool: str, **kwargs):  # noqa: ANN003, ANN201 - 与 Protocol 同形
        self.calls.append((tool, dict(kwargs)))
        if self.raises is not None:
            raise self.raises
        return self.payload


def _terminal(**overrides):
    """对端 `_terminal` ＋ `_finish` 的 `iptv_ok` 现读形状（九键齐）。"""
    record = {
        "result": "iptv_ok",
        "stage": "done",
        "gap": None,
        "detail": None,
        "af_speech": "新闻已打开",
        "s3_s4": {
            "s3_tv_foreground_app": {"value": "com.android.smart.terminal.iptv", "last_checked": 1.0},
            "s4_tv_current_channel": {"value": "中央一台", "last_checked": 1.0},
        },
        "screen_on_after": None,
        "steps": [
            {"step": "wake", "status": "ok", "evidence": {}, "at": 1.0},
            {"step": "launch_iptv", "status": "ok", "evidence": {}, "at": 2.0},
        ],
        "total_ms": 12.5,
    }
    record.update(overrides)
    return record


def _wake_fail():
    """`wake_fail`：`s3_s4` 为空 dict（对端 L217-218）、`af_speech` 非空。"""
    return _terminal(result="wake_fail", stage="failed", af_speech="电视唤醒失败",
                     s3_s4={}, steps=[{"step": "wake", "status": "failed",
                                       "evidence": {"channels": "miIO/WoL/outlet"}, "at": 1.0}])


def _adapter(payload=None, *, raises=None, dry_run: bool = False):
    client = _StubClient(payload, raises=raises)
    return TvAdapter(client, dry_run=dry_run), client


# ── ① 正常回执映射：九键逐字、不多收不少收 ─────────────────────────────
def test_terminal_receipt_maps_nine_keys_verbatim():
    payload = _terminal()
    adapter, client = _adapter(payload)
    result = adapter.call("morning_news", {"request_id": "req-1"})

    assert isinstance(result, CallResult)
    assert result.success is True
    assert client.calls == [(TOOL_MORNING_NEWS, {"request_id": "req-1"})], "工具名与入参都要对"
    assert set(result.data) == set(TERMINAL_KEYS) | set(AF_KEYS), "⛔ 不多收不少收"
    for key in TERMINAL_KEYS:
        assert result.data[key] == payload[key], f"{key} 必须逐字透传"
    assert result.data["action"] == "morning_news"
    assert result.data["request_id"] == "req-1"


def test_action_prefix_and_tool_name_are_normalised():
    assert action_of("tv.morning_news") == "morning_news"
    assert action_of("  Morning_News ") == "morning_news"
    adapter, client = _adapter(_terminal())
    adapter.call("tv.morning_news", {})
    assert client.calls[0][0] == TOOL_MORNING_NEWS


# ── ② 幂等通道：already_running 原样透传，last 完整带回，⛔ 不重跑 ────────
def test_already_running_passes_last_through_and_never_reruns():
    last = _terminal(result="iptv_fail", stage="failed", detail="zap_failed",
                     af_speech="电视已打开，新闻没能自动打开，请手动观看")
    payload = {"accepted": False, "reason": "already_running", "request_id": "req-2", "last": last}
    adapter, client = _adapter(payload)

    result = adapter.call("morning_news", {"request_id": "req-2"})

    assert result.success is True, "已是合法答复，⛔ 不当失败"
    assert result.data["accepted"] is False
    assert result.data["reason"] == "already_running"
    assert result.data["last"] == last, "上一次完整回执必须原样带回（⛔ 不可只给 result 一枚）"
    assert len(client.calls) == 1, "⛔ 不排队、⛔ 不重跑（只发一次）"


def test_already_running_on_first_ever_run_carries_empty_last():
    """对端首轮 `self._last` 是 `{}`：空对象也是"有"，⛔ 不许判成形状塌。"""
    adapter, _ = _adapter({"accepted": False, "reason": "already_running",
                           "request_id": "r", "last": {}})
    result = adapter.call("morning_news", {})
    assert result.success is True
    assert result.data["last"] == {}


# ── ③ 六值枚举：活跃四值直通，休眠两值判缺陷，枚举外必须抛 ──────────────
@pytest.mark.parametrize("result_value", ACTIVE_RESULTS)
def test_active_results_are_accepted(result_value):
    stage = "done" if result_value in ("iptv_ok", "adb_off_tv_on") else "failed"
    s3_s4 = {} if result_value not in ("iptv_ok", "iptv_fail") else _terminal()["s3_s4"]
    adapter, _ = _adapter(_terminal(result=result_value, stage=stage, s3_s4=s3_s4))
    result = adapter.call("morning_news", {})
    assert result.success is True
    assert result.data["result"] == result_value


@pytest.mark.parametrize("result_value", DORMANT_RESULTS)
def test_dormant_results_are_judged_as_defect_not_terminal(result_value):
    """休眠两值「可识别但不可期待」：收到即判 TVPilot 实现越界（缺陷），⛔ 按正常 failed 终态收尾。"""
    adapter, _ = _adapter(_terminal(result=result_value, stage="failed",
                                    af_speech=None, s3_s4={}))
    result = adapter.call("morning_news", {})

    assert result.success is False, "⛔ 不许当成正常终态收下"
    assert result.data["code"] == ADM_ERR_INTERNAL, "对端实现越界走 ADM_ERR_INTERNAL"
    assert result.data["defect"] == DEFECT_DORMANT_RESULT, "缺陷必须留痕（QA 要能计数）"
    assert result.data["result"] == result_value, "回执里的 result 原样带回便于对端定位"
    assert "实现越界" in result.error
    assert "failed 终态" in result.error, "文案要点明这不是业务失败"


def test_six_value_enum_is_frozen_in_both_directions():
    assert set(RESULTS) == {"iptv_ok", "adb_off_tv_on", "wake_fail", "iptv_fail",
                            "faceid_timeout", "arcface_fail"}
    assert set(ACTIVE_RESULTS) | set(DORMANT_RESULTS) == set(RESULTS)
    adapter, _ = _adapter(_terminal(result="tv_ok"))
    with pytest.raises(AdapterError) as exc:
        adapter.call("morning_news", {})
    assert ADM_ERR_PAYLOAD_INVALID in str(exc.value)


def test_defect_of_result_only_flags_the_two_dormant_values():
    assert defect_of_result("faceid_timeout") == DEFECT_DORMANT_RESULT
    assert defect_of_result("arcface_fail") == DEFECT_DORMANT_RESULT
    for value in ACTIVE_RESULTS + ("tv_ok", None, 42):
        assert defect_of_result(value) is None, f"{value!r} 不该被判缺陷（脏值归形状判据）"


# ── ④ screen_on_after=None（对端硬编码）⛔ 不判失败 ───────────────────────
def test_screen_on_after_null_is_not_a_success_criterion():
    adapter, _ = _adapter(_terminal(screen_on_after=None))
    result = adapter.call("morning_news", {})
    assert result.success is True, "G6 未落码时该值恒 null，⛔ 拿它当成功判据"
    assert result.data["screen_on_after"] is None


# ── ⑤ s3_s4 空对象不崩（两支不同形）────────────────────────────────────
def test_empty_s3_s4_is_passed_through_without_crashing():
    adapter, _ = _adapter(_wake_fail())
    result = adapter.call("morning_news", {})
    assert result.success is True
    assert result.data["s3_s4"] == {}, "非 iptv_* 两支就是空 dict：键常在、值可空"
    assert result.data["detail"] is None


# ── ⑥ 超时 ⇒ ADM_ERR_UPSTREAM_TIMEOUT（三种写法都要认）───────────────────
@pytest.mark.parametrize("exc", [
    socket.timeout("timed out"),
    TimeoutError("timed out"),
    HTTPError("http://tv.invalid/mcp", 504, "Gateway Timeout", {}, None),
])
def test_timeout_maps_to_upstream_timeout(exc):
    adapter, _ = _adapter(raises=exc)
    result = adapter.call("morning_news", {"request_id": "req-3"})
    assert result.success is False
    assert result.data["code"] == ADM_ERR_UPSTREAM_TIMEOUT
    assert "240" in result.error, "错误文案要带对端预算，便于现场判是长调用超时"


def test_other_channel_failures_map_to_their_own_codes():
    auth, _ = _adapter(raises=HTTPError("u", 401, "Unauthorized", {}, None))
    assert auth.call("morning_news", {}).data["code"] == ADM_ERR_AUTH_REQUIRED

    offline, _ = _adapter(raises=URLError(ConnectionRefusedError("refused")))
    assert offline.call("morning_news", {}).data["code"] == ADM_ERR_PEER_OFFLINE


# ── ⑦ request_id：缺省自生成 uuid4，给了就透传 ──────────────────────────
def test_missing_request_id_is_self_generated_uuid4():
    adapter, client = _adapter(_terminal())
    result = adapter.call("morning_news", {})

    rid = client.calls[0][1]["request_id"]
    assert isinstance(rid, str) and rid
    assert uuid.UUID(rid).version == 4, "缺省值必须是 uuid4（幂等键，⛔ 时间戳等可撞值）"
    assert result.data["request_id"] == rid, "AF 侧相关键要与发给对端的一致"


def test_explicit_request_id_is_passed_through_verbatim():
    adapter, client = _adapter(_terminal())
    adapter.call("morning_news", {"request_id": "  2f7c9d1e-ignored-prefix  "})
    assert client.calls[0][1]["request_id"] == "2f7c9d1e-ignored-prefix"


# ── ⑧ 参数／动作白名单：名单外一律响亮 ──────────────────────────────────
def test_unknown_param_is_refused_not_forwarded():
    adapter, client = _adapter(_terminal())
    result = adapter.call("morning_news", {"request_id": "r", "channel": 21})
    assert result.success is False
    assert result.data["code"] == ADM_ERR_PAYLOAD_INVALID
    assert client.calls == [], "名单外参数⛔ 不许下发"
    assert PARAMS_ALLOWED == frozenset({"request_id"}), "对端入参只有它"


def test_unknown_action_raises_loudly():
    adapter, _ = _adapter(_terminal())
    with pytest.raises(AdapterError) as exc:
        adapter.call("reboot_tv", {})
    assert ADM_ERR_PAYLOAD_INVALID in str(exc.value)


# ── ⑨ 只受理没终态：⛔ 不自造终态、⛔ 不重跑 ─────────────────────────────
def test_accepted_true_without_terminal_fails_honestly():
    adapter, client = _adapter({"accepted": True, "request_id": "req-4"})
    result = adapter.call("morning_news", {"request_id": "req-4"})
    assert result.success is False
    assert result.data["code"] == ADM_ERR_PAYLOAD_INVALID
    assert "result" not in result.data, "⛔ 不许自造 result"
    assert len(client.calls) == 1, "⛔ 不重跑一轮补终态"


# ── ⑩ dry_run 缺省不出网；无客户端时拒发 ────────────────────────────────
def test_dry_run_default_never_touches_the_client():
    client = _StubClient(_terminal())
    adapter = TvAdapter(client)
    result = adapter.call("morning_news", {"request_id": "req-5"})
    assert result.success is True
    assert result.data["dry_run"] is True
    assert result.data["tool"] == TOOL_MORNING_NEWS
    assert result.data["request_id"] == "req-5"
    assert client.calls == [], "dry_run 一个字节都不出网"


def test_missing_client_fails_with_auth_code():
    adapter = TvAdapter(None, dry_run=False)
    result = adapter.call("morning_news", {})
    assert result.success is False
    assert result.data["code"] == ADM_ERR_AUTH_REQUIRED


# ── ⑪ 形状塌：读空冒充成功一律抛 ────────────────────────────────────────
@pytest.mark.parametrize("mutate,label", [
    (lambda r: r.pop("steps"), "缺 steps"),
    (lambda r: r.pop("total_ms"), "缺 total_ms"),
    (lambda r: {**r, "total_ms": None}, "total_ms 不是数值"),
    (lambda r: {**r, "s3_s4": []}, "s3_s4 不是对象"),
    (lambda r: {**r, "steps": "wake,launch"}, "steps 不是列表"),
    (lambda r: {**r, "steps": ["wake"]}, "steps 元素不是对象"),
])
def test_shape_rot_raises_instead_of_silent_success(mutate, label):
    adapter, _ = _adapter(mutate(_terminal()))
    with pytest.raises(AdapterError) as exc:
        adapter.call("morning_news", {})
    assert ADM_ERR_PAYLOAD_INVALID in str(exc.value), f"{label} 必须响亮"


def test_non_mapping_payload_raises():
    adapter, _ = _adapter(["not", "a", "mapping"])
    with pytest.raises(AdapterError):
        adapter.call("morning_news", {})


def test_neither_terminal_nor_idempotent_raises():
    adapter, _ = _adapter({"ok": True})
    with pytest.raises(AdapterError):
        adapter.call("morning_news", {})


# ── ⑫ 传输预算常量与 map_terminal 的独立口径 ────────────────────────────
def test_transport_budget_covers_peer_budget_with_margin():
    assert DEFAULT_TV_TRANSPORT_TIMEOUT >= 240.0, "对端全链预算 240s，⛔ 比它短"
    assert DEFAULT_TV_TRANSPORT_TIMEOUT <= 300.0, "余量也不许无限大（单进程不许挂死）"
    adapter, _ = _adapter(_terminal())
    assert adapter.transport_timeout == DEFAULT_TV_TRANSPORT_TIMEOUT


def test_map_terminal_is_pure_and_reusable():
    payload = _terminal()
    data = map_terminal(payload, request_id="r9", action="morning_news")
    assert set(data) == set(TERMINAL_KEYS) | set(AF_KEYS)
    assert payload["steps"] == data["steps"], "⛔ 不改写对端载荷"
