"""AutoForge v2 M1 首演码仪式试演期 · 单测。

覆盖验收 §8 八条：6 位码签发、哈希一致消费、掉包拒绝、过期拒绝、重放拒绝、
试演期 assert 失败触发可注入 pusher（不真实封禁）、仅标准库、既有流程无回归。
时间源可注入，做时间旅行测试。
"""

from __future__ import annotations

import os

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


# ---- 验收 #6：试演期 assert 失败 → 分级暂停（决策 F） ----

def test_report_assert_failure_triggers_pusher():
    """low risk band：首次失败 notify_only，二次失败 pause + 推。"""
    store = TrialStore()
    store.enter_trial(_sha(9), hours=24)
    calls = []
    store.report_assert_failure(_sha(9), pusher=calls.append)  # 首次：notified_only
    assert len(calls) == 1
    assert calls[0]["paused"] is False
    assert calls[0]["action"] == "notified_only"
    store.report_assert_failure(_sha(9), pusher=calls.append)  # 二次：pause + push
    assert len(calls) == 2
    payload = calls[1]
    assert payload["paused"] is True
    assert payload["store_diff_sha256"] == _sha(9)
    # 试演期被标记暂停，但未执行任何封禁动作（真正封禁由 af_apply 执行闸拦截）
    assert store.get_trial(_sha(9)).paused is True


def test_report_assert_failure_no_pusher_no_error():
    store = TrialStore()
    store.enter_trial(_sha(10), hours=24)
    store.report_assert_failure(_sha(10))  # 首次 low risk：notify_only
    res = store.report_assert_failure(_sha(10))  # 二次：pause
    assert res["ok"] is True
    assert res["paused"] is True


def test_report_assert_failure_high_risk_pauses_on_first():
    """high risk band：首次失败即 pause。"""
    store = TrialStore()
    store.enter_trial(_sha(105), hours=24, risk_level="high")
    calls = []
    res = store.report_assert_failure(_sha(105), pusher=calls.append)
    assert res["ok"] is True
    assert res["paused"] is True
    assert len(calls) == 1
    assert store.get_trial(_sha(105)).paused is True


def test_report_assert_failure_low_risk_first_only_notifies():
    """low risk band：首次失败只 notify，不 pause。"""
    store = TrialStore()
    store.enter_trial(_sha(106), hours=24, risk_level="low")
    res = store.report_assert_failure(_sha(106))
    assert res["ok"] is True
    assert res["paused"] is False
    assert res["action"] == "notified_only"
    assert store.get_trial(_sha(106)).paused is False


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


def test_issue_premiere_runs_and_is_consumable():
    """验收 #8 的正身：过去这条只写 `assert callable(issue_premiere)`，从没调用过。

    于是 `AuditLog.add(event)` 被当类方法用的 TypeError 藏了很久——一取码就崩，
    部署仪式整条路走不通。这里真跑一次取码，并断言事件进了 DEPLOY_AUDIT、
    签出来的码能用同一份 diff 哈希消费掉。
    """
    from autoforge import af_apply
    from autoforge.af_draft import draft_intent

    ref = draft_intent({
        "name": "取码用例",
        "when": {"type": "state", "entity": "前门", "to": "on"},
        "do": {"action": "开灯", "target": "客厅灯"},
    })["ref"]

    before = len(af_audit.DEPLOY_AUDIT)
    out = af_apply.issue_premiere(ref)
    assert out["ok"] is True
    assert len(out["code"]) == 6
    assert len(af_audit.DEPLOY_AUDIT) == before + 1
    assert af_audit.DEPLOY_AUDIT.of_type(af_audit.PREMIERE_ISSUED)

    consumed = consume(out["code"], out["store_diff_sha256"])
    assert bool(consumed) is True, f"签出的码消费不掉：{consumed}"
    # 一次性：同一个码再来一次必须被拒
    assert bool(consume(out["code"], out["store_diff_sha256"])) is False


def test_af_audit_extended_with_premiere_types():
    for t in (
        af_audit.PREMIERE_ISSUED,
        af_audit.PREMIERE_CONSUMED,
        af_audit.PREMIERE_TRIAL_STARTED,
        af_audit.PREMIERE_TRIAL_PAUSED,
        af_audit.TRIAL_ASSERT_FAILED,
    ):
        assert t in af_audit.ALL_EVENT_TYPES


# ---- PR 1.7：premiere 落盘（JSON 写透 + 启动 load）----

def test_premiere_store_persists_and_reloads(tmp_path):
    path = str(tmp_path / "premiere.json")
    s1 = PremiereStore(path=path)
    code = s1.issue(_sha(101))               # 写透
    assert os.path.exists(path)
    # 新实例同一路径 → 恢复既有首演码
    s2 = PremiereStore(path=path)
    assert s2.peek(code) is not None
    assert s2.consume(code, _sha(101))["ok"] is True
    # 消费已写透：重载后应为已消费（重放拒绝），不破防掉包
    s3 = PremiereStore(path=path)
    res = s3.consume(code, _sha(101))
    assert res["ok"] is False
    assert res["reason"] == "already_consumed"


def test_trial_store_persists_and_reloads(tmp_path):
    path = str(tmp_path / "trial.json")
    s1 = TrialStore(path=path)
    s1.enter_trial(_sha(102), hours=24, risk_level="high")  # high risk：首次 pause
    s2 = TrialStore(path=path)
    assert s2.is_in_trial(_sha(102)) is True
    s2.report_assert_failure(_sha(102))       # 暂停写透
    s3 = TrialStore(path=path)
    assert s3.get_trial(_sha(102)).paused is True


def test_premiere_save_atomic_no_tmp(tmp_path):
    path = str(tmp_path / "premiere.json")
    s = PremiereStore(path=path)
    s.issue(_sha(103))
    assert os.path.exists(path)
    assert not os.path.exists(path + ".tmp")   # 原子替换不留半截文件


def test_set_persistence_loads_defaults(tmp_path):
    # 决策 F 前置：启动期把默认仓库指向落盘路径并恢复既有数据
    ppath = str(tmp_path / "premiere.json")
    tpath = str(tmp_path / "trial.json")
    s = PremiereStore(path=ppath)
    code = s.issue(_sha(104))
    t = TrialStore(path=tpath)
    t.enter_trial(_sha(104), hours=24)

    from autoforge.af_premiere import set_persistence, _default_premiere, _default_trial
    set_persistence(premiere_path=ppath, trial_path=tpath)
    # 默认实例已加载既有数据
    assert _default_premiere.peek(code) is not None
    assert _default_trial.is_in_trial(_sha(104)) is True
    # 默认实例消费写透到同一文件，重载仍一致
    assert _default_premiere.consume(code, _sha(104))["ok"] is True

