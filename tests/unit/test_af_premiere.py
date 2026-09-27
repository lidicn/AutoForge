"""AutoForge v2 M1 首演码仪式试演期 · 单测。

覆盖验收 §8 八条：6 位码签发、哈希一致消费、掉包拒绝、过期拒绝、重放拒绝、
试演期 assert 失败触发可注入 pusher（不真实封禁）、仅标准库、既有流程无回归。
时间源可注入，做时间旅行测试。
"""

from __future__ import annotations

from autoforge import af_audit
from autoforge.af_premiere import (
    ConsumeResult,
    PremiereStore,
    TrialStore,
    consume,
    enter_trial,
    issue,
    pause_and_notify,
    report_assert_failure,
)


def _sha(n: int) -> str:
    return f"sha-test-{n:04d}"


# ---- 验收 #3 / #4 / #5：签发与消费语义 ----

def test_issue_returns_6_digit_code():
    code = issue(_sha(1))
    assert isinstance(code, str)
    assert len(code) == 6
    assert code.isdigit()


def test_consume_success_same_sha():
    code = issue(_sha(2))
    res = consume(code, _sha(2))
    assert res
    assert res["ok"] is True
    assert res["reason"] == "ok"
    assert res["code"] == code


def test_consume_rejects_different_sha():
    code = issue(_sha(3))
    res = consume(code, _sha(999))
    assert not res
    assert res["reason"] == "sha_mismatch"


def test_consume_rejects_unknown_code():
    res = consume("000000", _sha(4))
    assert not res
    assert res["reason"] == "unknown_code"


def test_consume_rejects_replay():
    code = issue(_sha(5))
    first = consume(code, _sha(5))
    assert first
    second = consume(code, _sha(5))
    assert not second
    assert second["reason"] == "already_consumed"


def test_consume_rejects_expired():
    clock = {"t": 1000.0}
    store = PremiereStore(now=lambda: clock["t"], ttl_s=300)
    code = store.issue(_sha(6))
    clock["t"] = 1000.0 + 301  # 超过 ttl
    res = store.consume(code, _sha(6))
    assert not res
    assert res["reason"] == "expired"


def test_issue_unique_codes():
    store = PremiereStore()
    a = store.issue(_sha(7))
    b = store.issue(_sha(7))
    assert a != b


# ---- ConsumeResult 形状兼容 ----

def test_consume_result_bool_semantics():
    assert bool(ConsumeResult(True))
    assert not bool(ConsumeResult(False))


def test_consume_result_dict_access():
    res = ConsumeResult(False, "expired", code="123456", store_diff_sha256=_sha(8))
    assert res["ok"] is False
    assert res["reason"] == "expired"
    assert res["code"] == "123456"


# ---- 验收 #6：试演期 assert 失败 → 可注入 pusher，不真实封禁 ----

def test_report_assert_failure_triggers_pusher():
    store = TrialStore()
    store.enter_trial(_sha(9), hours=24)
    calls = []
    store.report_assert_failure(_sha(9), pusher=calls.append)
    assert len(calls) == 1
    payload = calls[0]
    assert payload["paused"] is True
    assert payload["store_diff_sha256"] == _sha(9)
    # 试演期只统计不封禁：trial 被标记暂停，但未执行任何封禁动作
    assert store.get_trial(_sha(9)).paused is True


def test_report_assert_failure_no_pusher_no_error():
    store = TrialStore()
    store.enter_trial(_sha(10), hours=24)
    res = store.report_assert_failure(_sha(10))  # 无 pusher 不报错
    assert res["ok"] is True
    assert res["paused"] is True


def test_report_assert_failure_not_in_trial():
    store = TrialStore()
    res = store.report_assert_failure(_sha(11), pusher=lambda p: None)
    assert res["ok"] is False
    assert res["reason"] == "not_in_trial"


def test_pause_and_notify_direct_with_pusher():
    store = TrialStore()
    store.enter_trial(_sha(12), hours=24)
    calls = []
    store.pause_and_notify(pusher=calls.append, store_diff_sha256=_sha(12))
    assert len(calls) == 1
    assert calls[0]["paused"] is True


def test_pause_and_notify_uses_last_trial():
    store = TrialStore()
    store.enter_trial(_sha(13), hours=24)
    store.enter_trial(_sha(14), hours=24)
    calls = []
    # 不传 sha → 作用于最近进入的试演期
    store.pause_and_notify(pusher=calls.append)
    assert calls[0]["store_diff_sha256"] == _sha(14)


# ---- 试演期窗口 ----

def test_trial_in_window_true_then_false():
    clock = {"t": 2000.0}
    store = TrialStore(now=lambda: clock["t"])
    store.enter_trial(_sha(15), hours=1)
    assert store.is_in_trial(_sha(15)) is True
    clock["t"] = 2000.0 + 3601  # 超过 1h
    assert store.is_in_trial(_sha(15)) is False


def test_enter_trial_returns_window():
    res = enter_trial(_sha(16), hours=24)
    assert res["ok"] is True
    assert res["ends_at"] > res["started_at"]


# ---- 验收 #7：仅标准库（静态可查 import）----

def test_module_stdlib_only():
    import ast
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[2] / "src" / "autoforge" / "af_premiere.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    third_party = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                third_party.add(n.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0 and node.module != "__future__":
                third_party.add(node.module.split(".")[0])
    stdlib_ok = {
        "secrets", "hashlib", "json", "threading", "time", "dataclasses",
        "typing", "functools", "abc", "datetime", "os",
    }
    assert third_party <= stdlib_ok


# ---- 验收 #8：既有流程无回归（af_apply 默认不开闸门；af_live 未改动）----

def test_af_apply_apply_backward_compatible_signature():
    from autoforge import af_apply

    # 默认 premiere_code=None：向后兼容既有调用 / 现有 forge run/watch --live 三重闸
    import inspect

    sig = inspect.signature(af_apply.apply)
    assert sig.parameters["premiere_code"].default is None
    # 取码入口存在且可调用
    assert callable(af_apply.issue_premiere)


def test_af_audit_extended_with_premiere_types():
    for t in (
        af_audit.PREMIERE_ISSUED,
        af_audit.PREMIERE_CONSUMED,
        af_audit.PREMIERE_TRIAL_STARTED,
        af_audit.PREMIERE_TRIAL_PAUSED,
        af_audit.TRIAL_ASSERT_FAILED,
    ):
        assert t in af_audit.ALL_EVENT_TYPES
