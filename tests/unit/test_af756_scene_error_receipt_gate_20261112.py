# -*- coding: utf-8 -*-
"""判据 · AF-756 场景回执必须带失败原因（＋「执行超时」纳入瞬态重试）。

派卡：`E:\\NAS\\QA\\AF_Tester\\plans\\AF-756_场景回执缺失败原因_派卡_20261112.md`
现场（2026-11-12 21:05:20 +08，`scene_shu_fang_lights_on`，`run_id=r-15c03cb5c107`）：
s2（飞利浦台灯）`done=false`、`skipped_reason=aborted_after_error`，**回执里没有任何错误文本**
⇒ 只能去 HA 容器日志反查。根因两处截断：service 的 `bool(...)` 把 `CallResult.error` 截成
`False`，kit 的 `bool(self.executor(step))` 再截一次、异常路径连类型名都不留；外加
`"执行超时"` 不在 `TRANSIENT_DEVICE_ERROR_MARKERS` 里 ⇒ 一次都不重试。

判据映射（J5 的真机正腿归 AF-Verify；本文件只钉**单元面**的可判定部分）：
- J1 失败原因进回执，且 ≤200 字符；
- J2 异常路径留**类型名＋原文**；
- J3 令牌／绝对路径**脱敏**（构造令牌片段，回执里必须找不到）；
- J4 `执行超时` 判瞬态、`-706010002` 不回归、非瞬态标记仍为 False；
- J5 成功步 `error` 恒 `None`（⛔ 不硬塞错误文本）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from autoforge import af_service as svc
from autoforge.af_adapters import CallResult
from autoforge.af_adapters import ha as ha_mod
from autoforge.af_grant import GrantStore
from autoforge.af_scene_kit import ERROR_TEXT_MAX, SceneDef, SceneRunner


# ── 夹具（与 `tests/test_scene_kit.py` 同形，只留本判据要用的最小场景）──────
@pytest.fixture()
def gs(tmp_path):
    return GrantStore(tmp_path / ".auth" / "grants.json")


def _grant(gs, **kw):
    params = {"agent": "tv-pilot", "issued_by": "owner", "rooms": ["客厅"],
              "action_risk": "L1"}
    params.update(kw)
    return gs.create(**params)


def _scene(n: int = 1) -> SceneDef:
    return SceneDef.from_ir({
        "kind": "scene_def",
        "scene_id": "scene_home_001",
        "name": "回家模式",
        "aliases": ["到家了"],
        "version": 1,
        "owner": "owner",
        "condition": None,
        "created_via": "ask",
        "steps": [
            {"step_id": f"s{i}", "adapter": "hass", "action": "light.turn_on",
             "target": "light.living_room", "params": {}, "on_error": "abort",
             "delay_ms_after": 0}
            for i in range(n)
        ],
    })


def _run(gs, executor, steps: int = 1):
    runner = SceneRunner(gs, executor=executor, room_of=lambda entity: "客厅",
                         now=lambda: 1762867323.0)
    return runner.run(_scene(steps), grant=_grant(gs), agent="tv-pilot")


# ── J1：失败原因进回执 ───────────────────────────────────────────────
def test_j1_failure_reason_reaches_receipt(gs):
    """`CallResult.fail` 的错误原文必须落到 `steps[*].error`（⛔ 只剩 done=false）。"""
    error = ("HA 返回 500：RuntimeError: 执行错误, 执行超时, "
             "light.philips_cn_249518489_rwread_s_2_li,  灯, on")
    receipt = _run(gs, lambda step: CallResult.fail(error))

    entry = receipt["steps"][0]
    assert entry["done"] is False
    assert entry["error"], "回执必须带失败原因（AF-756 的原始缺陷）"
    assert "500" in entry["error"], f"错误原文丢失：{entry['error']!r}"
    assert "执行超时" in entry["error"], f"设备侧原因要能看见：{entry['error']!r}"
    assert len(entry["error"]) <= ERROR_TEXT_MAX, (
        f"error 必须截断到 {ERROR_TEXT_MAX} 字符：{len(entry['error'])}"
    )
    # `code` 维度不变：授权放行就是 ok，⛔ 不把设备失败混成 code=failed
    assert entry["code"] == "ok"


def test_j1_long_error_text_is_truncated(gs):
    """超长错误文本切到 200 字符（回执外发给 agent 还会进知识库历史）。"""
    entry = _run(gs, lambda step: CallResult.fail("x" * 5000))["steps"][0]
    assert len(entry["error"]) == ERROR_TEXT_MAX


# ── J2：异常路径留类型名＋原文 ───────────────────────────────────────
def test_j2_exception_keeps_type_and_message(gs):
    def boom(step):
        raise RuntimeError("boom")

    entry = _run(gs, boom)["steps"][0]
    assert entry["done"] is False
    assert entry["error"] == "RuntimeError: boom", f"异常要留类型名＋原文：{entry['error']!r}"


def test_j2_abort_still_marks_rest_skipped(gs):
    """失败步之后的未执行步照旧记 `aborted_after_error`（⛔ 加了 error 字段不等于改语义）。"""
    def boom(step):
        raise ValueError("灯不可达")

    receipt = _run(gs, boom, steps=2)
    assert receipt["steps"][0]["error"] == "ValueError: 灯不可达"
    # 语义保持原样：失败步本身也带 `aborted_after_error`（这是 AF-756 之前就有的口径）
    assert receipt["steps"][0]["skipped_reason"] == "aborted_after_error"
    assert receipt["steps"][1]["skipped_reason"] == "aborted_after_error"
    assert receipt["steps"][1]["error"] is None, "未执行步没有失败原因（⛔ 不复制上一步的）"


# ── J3：令牌／路径脱敏 ───────────────────────────────────────────────
def test_j3_token_shapes_redacted(gs):
    """构造令牌（与 `scripts/check_secrets.py` 同两族）⇒ 回执里找不到原值。"""
    hex_token = "af_" + "0123456789abcdef" * 2 + "01234567"   # 43 字符，⛔ 不写成字面量
    assert len(hex_token) == 43
    bearer = "Bearer " + "Zx9" * 12

    entry = _run(gs, lambda step: CallResult.fail(
        f"HA 返回 401：unauthorized {hex_token} / {bearer}"))["steps"][0]
    text = entry["error"]
    assert hex_token not in text, f"令牌泄漏进回执：{text!r}"
    assert bearer not in text, f"Bearer 令牌泄漏进回执：{text!r}"
    assert text.count("<令牌已隐去>") >= 2, f"两族令牌都要有占位：{text!r}"


def test_j3_absolute_paths_redacted(gs):
    """绝对路径 → `<路径已隐去>`（回执会进 MCP 会话与知识库历史，事后洗不掉）。"""
    entry = _run(gs, lambda step: CallResult.fail(
        r"无法读取 E:\NAS\AutoForge\.forge\state.json 与 /var/lib/autoforge/grants.json"
    ))["steps"][0]
    text = entry["error"]
    assert "E:\\NAS" not in text, f"Windows 绝对路径泄漏：{text!r}"
    assert "/var/lib/autoforge" not in text, f"POSIX 绝对路径泄漏：{text!r}"
    assert "<路径已隐去>" in text


def test_j3_entity_id_survives_redaction(gs):
    """实体 id 必须留着——脱敏是为了不带凭据，⛔ 不是为了把可诊断信息一起洗掉。"""
    entry = _run(gs, lambda step: CallResult.fail(
        "HA 返回 500：执行超时, light.philips_cn_249518489_rwread_s_2_li"))["steps"][0]
    assert "light.philips_cn_249518489_rwread_s_2_li" in entry["error"]


# ── J4：执行超时纳入瞬态重试 ────────────────────────────────────────
def test_j4_execution_timeout_is_transient():
    """`执行超时`（xiaomi_home 组件抛的设备侧超时）判瞬态；`-706010002` 不回归。"""
    assert ha_mod.is_transient_device_error(
        "HA 返回 500：RuntimeError: 执行错误, 执行超时, light.philips_cn_249518489,  灯, on"
    ) is True
    assert ha_mod.is_transient_device_error("-706010002") is True, "AF-742 的标记⛔ 不回归"
    assert ha_mod.is_transient_device_error("HA 返回 500：code=-706010002, target-temperature") is True


def test_j4_non_transient_still_false():
    """⛔ 不要因为加标记就把鉴权失败／参数非法／网络套接超时一起重试。"""
    assert ha_mod.is_transient_device_error("HA 返回 401：unauthorized") is False
    assert ha_mod.is_transient_device_error("非法动作（应为 domain.service）") is False
    assert ha_mod.is_transient_device_error(None) is False
    assert ha_mod.is_transient_device_error("") is False


# ── J5（单元面）：成功步 error 恒 None ──────────────────────────────
def test_j5_success_step_error_is_none(gs):
    entry = _run(gs, lambda step: CallResult.ok())["steps"][0]
    assert entry["done"] is True
    assert entry["error"] is None, "成功步⛔ 不许硬塞错误文本"


def test_j5_bool_executor_backward_compatible(gs):
    """旧注入面（回 bool）照旧可用：`_normalize` 认三种回值形状。"""
    receipt = _run(gs, lambda step: True, steps=2)
    assert [e["done"] for e in receipt["steps"]] == [True, True]
    assert [e["error"] for e in receipt["steps"]] == [None, None]

    failed = _run(gs, lambda step: False)
    assert failed["steps"][0]["done"] is False
    assert failed["steps"][0]["error"] is None, "bool 回值没有错误文本可留（⛔ 编一个）"


# ── 服务层：`_scene_live_executor._dispatch` 不再 bool 截断 ─────────
def test_service_dispatch_returns_callresult(monkeypatch):
    """主因在 service：`_dispatch` 必须原样回 `CallResult`（`error` 不再被 `bool()` 吃掉）。"""
    monkeypatch.setenv("AUTOFORGE_LIVE_ENABLED", "1")
    monkeypatch.setenv("AUTOFORGE_HA_URL", "http://127.0.0.1:1")

    class _Transport:
        def __call__(self, action, params):
            return CallResult.fail("HA 返回 401：unauthorized", action=action)

    monkeypatch.setattr(svc, "LIVE_TRANSPORT_FACTORY", lambda url, token: _Transport())
    dispatch = svc._scene_live_executor(None)
    step = SimpleNamespace(params={}, target="light.living_room", action="light.turn_on")

    result = dispatch(step)
    assert isinstance(result, CallResult), f"⛔ 不许再 bool 截断：{result!r}"
    assert result.success is False
    assert result.error == "HA 返回 401：unauthorized"
