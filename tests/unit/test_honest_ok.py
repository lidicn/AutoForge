"""门禁假绿整改的反例锁：`ok` 必须来自真实后置条件，咽下的异常必须留下原因。

DCD 20261001 批里 MA 自查披露的那句——"门禁其实根本没执行，绿灯是假的"——在 AF 有
两个同型表现：CI 的门禁三步全挂 `continue-on-error`，以及三处"咽下异常还返回 ok=True"
（gates 规则 `swallow-and-claim-ok`）。整改把这些改成真实判据，本文件锁住改后的行为：
失败要报得出来，没送达不能算成功，回滚没干净不许说"部署为空"。
"""
from __future__ import annotations

import pytest

from autoforge import af_service, af_watch
from autoforge.af_apply import apply_group
from autoforge.af_orchestrator import compose_group
from autoforge.af_premiere import TrialStore

CHILD_A = {
    "id": "a_lamp", "name": "开夜灯", "version": 1, "mode": "single",
    "nodes": [
        {"id": "ona", "kind": "on", "trigger": {"type": "state", "entity_id": "input_boolean.night", "to": "on"}},
        {"id": "da", "kind": "do", "adapter": "ha", "action": "light.turn_on", "params": {"entity_id": "light.a"}},
    ],
    "edges": [{"from": "ona", "to": "da", "kind": "then"}],
}
CHILD_B = {
    "id": "b_lock", "name": "锁前门", "version": 1, "mode": "single",
    "nodes": [
        {"id": "onb", "kind": "on", "trigger": {"type": "state", "entity_id": "input_boolean.night", "to": "on"}},
        {"id": "db", "kind": "do", "adapter": "ha", "action": "lock.lock", "params": {"entity_id": "lock.front"}},
    ],
    "edges": [{"from": "onb", "to": "db", "kind": "then"}],
}


def _group():
    return compose_group("晚安", [CHILD_A, CHILD_B])


def _patch_sim_ok(monkeypatch):
    monkeypatch.setattr(af_service, "simulate", lambda ir, store=None: {"ok": True})


class _Rejects:
    """可控的回滚桩：记录被摘掉的 pending op_id，并可指定哪些摘不掉。"""

    def __init__(self, fail_ids=()):
        self.failed = set(fail_ids)
        self.rolled: list[str] = []

    def __call__(self, store, op_id, reason=""):
        if op_id in self.failed:
            raise RuntimeError(f"persist 锁被占用：{op_id}")
        self.rolled.append(op_id)
        return {"ok": True, "rejected": op_id}


def _reject(monkeypatch, fail=()):
    stub = _Rejects(fail)
    monkeypatch.setattr(af_service, "reject_pending", stub)
    return stub


@pytest.fixture
def submit_fails_on_second(monkeypatch):
    """第一条入队成功、第二条 SUBMIT_FAILED —— 触发已入队部分的回滚分支。"""
    _patch_sim_ok(monkeypatch)
    seen: list[str] = []

    def fake_submit(store, source, payload, submitted_by):
        if seen:
            return {"ok": False}
        seen.append(payload["ir"]["id"])
        return {"ok": True, "pending": f"op-{payload['ir']['id']}"}

    monkeypatch.setattr(af_service, "submit_pending", fake_submit)
    return seen


def test_rollback_failure_reports_residue(monkeypatch, submit_fails_on_second):
    """回滚没干净时不许报 `deployed: []`——队列里还留着条目，那是半部署。"""
    _reject(monkeypatch, fail=["op-a_lamp"])
    res = apply_group(_group(), store=object(), stage="apply")
    assert res["ok"] is False
    assert res["error"]["code"] == "SUBMIT_FAILED"
    assert [f["ref"] for f in res["rollback_failed"]] == ["op-a_lamp"]
    assert [f["child"] for f in res["rollback_failed"]] == ["a_lamp"]
    assert "persist 锁被占用" in res["rollback_failed"][0]["error"]
    assert res["deployed"] == ["a_lamp"]


def test_clean_rollback_still_reports_empty_deployed(monkeypatch, submit_fails_on_second):
    """回滚全部成功 → 维持既有契约（deployed 为空），不因为新字段而变严。"""
    stub = _reject(monkeypatch)
    res = apply_group(_group(), store=object(), stage="apply")
    assert res["ok"] is False
    assert res["rollback_failed"] == []
    assert res["deployed"] == []
    # 摘除用的是 submit 回来的 pending op_id：真实 GraphStore 没有 rollback_pending，
    # 而按 child.id 去删队列条目本来就删不到东西。
    assert stub.rolled == ["op-a_lamp"]


def test_submit_raising_still_rolls_back(monkeypatch):
    """submit_pending 的拒绝形态是 raise（IR 非法/静态扫描不过/待批熔断）。

    不接住它，异常穿出 apply_group，前面已入队的条目留在队列里——「原子部署」当场变半部署。
    """
    _patch_sim_ok(monkeypatch)
    seen: list[str] = []

    def raise_on_second(store, source, payload, submitted_by):
        if seen:
            raise af_service.ServiceError("拒绝归档：IR 未通过静态扫描", status=400)
        seen.append(payload["ir"]["id"])
        return {"ok": True, "pending": f"op-{payload['ir']['id']}"}

    monkeypatch.setattr(af_service, "submit_pending", raise_on_second)
    stub = _reject(monkeypatch)
    res = apply_group(_group(), store=object(), stage="apply")
    assert res["ok"] is False
    assert "ServiceError" in res["error"]["message"]
    assert stub.rolled == ["op-a_lamp"]
    assert res["deployed"] == []


def test_missing_pending_ref_is_not_deployed(monkeypatch):
    """拿不到 pending op_id 就没有回滚把手，不能算已部署（铁律 #5）。"""
    _patch_sim_ok(monkeypatch)
    monkeypatch.setattr(
        af_service, "submit_pending",
        lambda store, source, payload, submitted_by: {"ok": True},
    )
    stub = _reject(monkeypatch)
    res = apply_group(_group(), store=object(), stage="apply")
    assert res["ok"] is False
    assert "未返回 pending op_id" in res["error"]["message"]
    assert stub.rolled == []


def test_apply_ok_is_all_children_queued(monkeypatch):
    """成功路径的 ok 来自"每个 child 都拿到了 pending ref"这条后置条件。"""
    _patch_sim_ok(monkeypatch)
    queued: list[str] = []
    monkeypatch.setattr(
        af_service, "submit_pending",
        lambda store, source, payload, submitted_by: (
            queued.append(payload["name"]) or {"ok": True, "pending": f"op-{payload['ir']['id']}"}
        ),
    )
    res = apply_group(_group(), store=object(), stage="apply")
    assert res["ok"] is True
    assert res["deployed"] == ["a_lamp", "b_lock"]
    assert res["pending_refs"] == {"a_lamp": "op-a_lamp", "b_lock": "op-b_lock"}
    assert len(queued) == 2
    assert res["group_mode"] == "sequence"
    assert any("approve" in c for c in res["caveats"])


def test_simulate_stage_has_no_queue_duty(monkeypatch):
    """stage=simulate 不入库，空集即成立——ok 不能因此变成 False（契约未变）。"""
    _patch_sim_ok(monkeypatch)
    res = apply_group(_group(), store=object(), stage="simulate")
    assert res["ok"] is True
    assert res["deployed"] == []


# ── af_premiere：推送送达与 ok 分离 ────────────────────────────────────

def _sha(n: int) -> str:
    return f"sha-ok-{n:04d}"


def test_pusher_exception_is_surfaced_not_hidden():
    store = TrialStore()
    store.enter_trial(_sha(1), hours=24, risk_level="high")

    def boom(payload):
        raise RuntimeError("MQTT 断了")

    res = store.report_assert_failure(_sha(1), pusher=boom)
    assert res["paused"] is True              # 暂停本身确实生效
    assert res["notified"] is False           # 但"通知到了"不再被冒充
    assert "RuntimeError" in res["notify_error"]


def test_absent_pusher_is_recorded_as_not_notified():
    store = TrialStore()
    store.enter_trial(_sha(2), hours=24, risk_level="high")
    res = store.report_assert_failure(_sha(2))
    assert res["notified"] is False
    assert res["notify_error"] == "no_pusher"


def test_notified_only_records_failure_in_window():
    store = TrialStore()
    store.enter_trial(_sha(3), hours=24, risk_level="low")
    calls: list[dict] = []
    res = store.report_assert_failure(_sha(3), pusher=calls.append)
    assert res["ok"] is True                  # 失败已计入窗口，且按 low risk 不暂停
    assert res["paused"] is False and res["notified"] is True
    assert store.get_trial(_sha(3)).assert_failures == 1


def test_resume_reads_back_real_state():
    store = TrialStore()
    store.enter_trial(_sha(4), hours=24, risk_level="high")
    store.pause_and_notify()
    assert store.resume(_sha(4))["resumed"] is True
    assert store.get_trial(_sha(4)).paused is False
    assert store.resume(_sha(404))["ok"] is False  # 不在试演期就是没恢复


# ── af_shadow：不阻断启动，但原因要留下 ────────────────────────────────

from conftest import (  # noqa: E402
    FakeAutomation, FakeClock, FakeInstance, FakeNode, FakeStates,
    make_conf, make_recorder, make_shadow,
)

from autoforge.af_shadow import Verdict  # noqa: E402


def _shadow(log_path=None):
    clock = FakeClock()
    conf = make_conf({"a1": 0.75})
    states = FakeStates()
    shadow = make_shadow(conf, clock, make_recorder(conf, clock), states, log_path=log_path)
    inst = FakeInstance(automation=FakeAutomation(id="a1"))
    return shadow, inst


class _Executor:
    def _do(self, instance, node):  # pragma: no cover - 仅作为被装饰对象
        return None


def test_corrupt_shadow_log_records_reason_and_still_installs(tmp_path):
    bad = tmp_path / "shadow_log.json"
    bad.write_text("{not json at all", encoding="utf-8")
    shadow, _ = _shadow(log_path=str(bad))
    binding = shadow.install(_Executor())
    assert shadow.replay_load_error is not None
    assert "Error" in shadow.replay_load_error
    binding.restore()


def test_intact_shadow_log_leaves_no_error(tmp_path):
    good = tmp_path / "shadow_log.json"
    good.write_text("[]", encoding="utf-8")   # load() 吃的是记录数组
    shadow, _ = _shadow(log_path=str(good))
    binding = shadow.install(_Executor())
    assert shadow.replay_load_error is None
    binding.restore()


def test_watch_aggregate_failure_is_recorded(monkeypatch):
    """影子结论没进生产证据账时，`watch_record_error` 要留下原因（原先是静默 pass）。"""
    af_watch.reset()
    shadow, inst = _shadow()
    shadow.states.set("light.study", "on")
    node = FakeNode(action="turn_on", entities=["light.study"], expected={"light.study": "on"})
    rec = shadow.run_do(inst, node)
    shadow.clock.advance(301)

    def boom(*args, **kwargs):
        raise RuntimeError("watch 侧写失败")

    monkeypatch.setattr(af_watch, "record_shadow", boom)
    judged = shadow.compare(rec.record_id)
    assert judged.verdict is Verdict.MATCHED          # 比对本身不受聚合失败影响
    assert "watch 侧写失败" in shadow.watch_record_error


def test_watch_aggregate_success_leaves_no_reason(monkeypatch):
    """正常送达时不留陈旧原因——否则"有错误字段"本身会变成新的噪声。"""
    af_watch.reset()
    shadow, inst = _shadow()
    shadow.states.set("light.study", "on")
    node = FakeNode(action="turn_on", entities=["light.study"], expected={"light.study": "on"})
    rec = shadow.run_do(inst, node)
    shadow.clock.advance(301)
    monkeypatch.setattr(af_watch, "record_shadow", lambda *a, **k: None)
    assert shadow.compare(rec.record_id).verdict is Verdict.MATCHED
    assert shadow.watch_record_error is None
