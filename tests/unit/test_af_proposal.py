"""ProposalManager 单测。"""
import pytest

from autoforge.af_feedback import FeedbackKind
from autoforge.af_proposal import (
    ProposalError, ProposalManager, ProposalStatus, StaticGuardPolicy,
)
from conftest import FakeClock, FakeNode, make_conf, make_recorder


IR_WITH_DO = {
    "version": 1,
    "automations": [{
        "id": "auto_x", "name": "x", "confidence": None,
        # 这份夹具原来抄的是 `entities: [...]`——IR 节点根本没有这枚字段，它是照着
        # `_ir_writes_devices()` 的错误读法长出来的，于是"strict 闸拦住了写设备"这条判据
        # 一直在拿一份真部署里不会出现的 IR 自证。改成 `params.entity_id`（真源见
        # `af_ir/models.py::Node.target_entities`）之后，F-09 那条腿才有东西可判。
        "nodes": {"n1": {"id": "n1", "kind": "do",
                          "params": {"entity_id": "light.study", "service": "turn_on"}}},
    }],
}


def _setup(**kw):
    clock = FakeClock()
    conf = make_conf({})
    rec = make_recorder(conf, clock)
    deployed = []

    def deployer(plan):
        deployed.append(plan)
        return f"auto_{len(deployed)}"

    mgr = ProposalManager(conf=conf, recorder=rec, clock=clock, deployer=deployer, **kw)
    return clock, conf, rec, mgr, deployed


def test_low_conf_goes_to_pending_ask_queue():
    clock, conf, rec, mgr, deployed = _setup()
    p = mgr.submit(hypothesis_id="h1", natural_language="书房>27 开空调", conf=0.45,
                   suggested_ir=IR_WITH_DO)
    assert p.status is ProposalStatus.PENDING
    assert deployed == []


def test_shadow_band_auto_deploys_with_static_confidence_none():
    clock, conf, rec, mgr, deployed = _setup()
    p = mgr.submit(hypothesis_id="h2", natural_language="书房>27 开空调", conf=0.75,
                   suggested_ir=IR_WITH_DO)
    assert p.status is ProposalStatus.AUTO_DEPLOYED_SHADOW
    assert deployed[0].band == "shadow"
    assert deployed[0].static_confidence is None      # 交给运行时 band 拦截
    assert conf.values[p.automation_id] == 0.75
    assert conf.band(p.automation_id) == "shadow"


def test_auto_band_deploys_with_canary():
    clock, conf, rec, mgr, deployed = _setup()
    p = mgr.submit(hypothesis_id="h3", natural_language="离家关灯", conf=0.92,
                   suggested_ir=IR_WITH_DO)
    assert p.status is ProposalStatus.AUTO_DEPLOYED_CANARY
    assert deployed[0].canary is True
    assert deployed[0].static_confidence == 0.92


def test_approve_pending_deploys_and_lifts_conf():
    clock, conf, rec, mgr, deployed = _setup()
    p = mgr.submit(hypothesis_id="h4", natural_language="x", conf=0.40,
                   suggested_ir=IR_WITH_DO)
    decided = mgr.approve(p.proposal_id, by="alice")
    assert decided.status is ProposalStatus.APPROVED
    assert decided.automation_id is not None
    assert rec.events[-1].kind is FeedbackKind.POSITIVE


def test_reject_penalizes_hypothesis():
    clock, conf, rec, mgr, deployed = _setup()
    conf.values["hypothesis:h5"] = 0.5
    p = mgr.submit(hypothesis_id="h5", natural_language="x", conf=0.40,
                   suggested_ir=IR_WITH_DO)
    mgr.reject(p.proposal_id, by="alice", reason="不需要")
    assert rec.events[-1].kind is FeedbackKind.USER_REJECT
    assert conf.values["hypothesis:h5"] == 0.25


def test_ask_proposal_approve_resumes_and_reject_penalizes_automation():
    clock, conf, rec, mgr, deployed = _setup()
    resumed = []
    mgr.resumer = resumed.append
    conf.values["a1"] = 0.35
    node = FakeNode()
    ask = mgr.open_ask(automation_id="a1", instance_id="i1", node=node,
                       expected_state={"light.study": "on"}, prompt="确认？")
    assert ask.status is ProposalStatus.PENDING
    mgr.approve(ask.proposal_id, by="bob")
    assert resumed and resumed[0].proposal_id == ask.proposal_id

    ask2 = mgr.open_ask(automation_id="a1", instance_id="i2", node=node,
                        expected_state={"light.study": "on"}, prompt="确认？")
    before = conf.values["a1"]
    mgr.reject(ask2.proposal_id, by="bob")
    assert conf.values["a1"] < before
    assert rec.events[-1].details["kind"] == "ask"


def test_strict_static_guard_refuses_shadow_do_ir():
    clock, conf, rec, mgr, deployed = _setup(
        static_guard=StaticGuardPolicy(mode="strict"))
    p = mgr.submit(hypothesis_id="h6", natural_language="x", conf=0.75,
                   suggested_ir=IR_WITH_DO)
    assert p.status is ProposalStatus.FAILED
    assert "strict" in p.reason


def test_double_decision_raises():
    clock, conf, rec, mgr, deployed = _setup()
    p = mgr.submit(hypothesis_id="h7", natural_language="x", conf=0.40,
                   suggested_ir=IR_WITH_DO)
    mgr.approve(p.proposal_id)
    with pytest.raises(ProposalError):
        mgr.reject(p.proposal_id)
