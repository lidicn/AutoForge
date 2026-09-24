"""IR §10 端到端闭环：MA 提案 → shadow → 转正 → canary → 全量 → 人工覆盖 → 降回 shadow。"""
from autoforge.af_canary_supervisor import (
    CanaryPolicy, CanarySupervisor, graph_canary_applier,
)
from autoforge.af_feedback import FeedbackKind
from autoforge.af_proposal import ProposalManager, ProposalStatus
from autoforge.af_shadow import ShadowPolicy
from conftest import (
    FakeAutomation, FakeCanaryResult, FakeClock, FakeExecutor, FakeGraph,
    FakeInstance, FakeNode, FakeStates, make_conf, make_intervention, make_recorder,
    make_shadow,
)

IR = {"version": 1, "automations": [{
    "id": "auto_book", "name": "书房降温", "confidence": None,
    "nodes": {"n1": {"id": "n1", "kind": "do", "action": "turn_on",
                     "entities": ["light.study"], "canary": None,
                     "expected": {"light.study": "on"}}},
}]}


def test_full_closed_loop():
    clock = FakeClock()
    conf = make_conf({})
    states = FakeStates()
    rec = make_recorder(conf, clock)
    node = FakeNode(action="turn_on", entities=["light.study"],
                    expected={"light.study": "on"}, canary=None)
    auto = FakeAutomation(id="auto_book", nodes={"n1": node})
    graph = FakeGraph(automations={"auto_book": auto})
    inst = FakeInstance(automation=auto, id="i1")

    canary = CanarySupervisor(
        conf=conf, recorder=rec, clock=clock,
        policy=CanaryPolicy(observation_hours=24.0),
        strip_canary=lambda aid: setattr(auto.nodes["n1"], "canary", None) or 1,
    )
    shadow = make_shadow(conf, clock, rec, states,
                         policy=ShadowPolicy(compare_after=300.0, streak_to_promote=3))
    shadow.on_promote = lambda aid: (graph_canary_applier(graph)(aid), canary.begin(aid))
    det = make_intervention(conf, clock, rec, states)
    det.mark_managed_from_graph(graph)

    mgr = ProposalManager(conf=conf, recorder=rec, clock=clock,
                          deployer=lambda plan: plan.proposal.hypothesis_id and "auto_book")

    # 1) MA 提案 conf=0.75 → 自动部署为 shadow
    p = mgr.submit(hypothesis_id="book", natural_language="书房>27 且门关 → 开空调",
                   conf=0.75, suggested_ir=IR)
    assert p.status is ProposalStatus.AUTO_DEPLOYED_SHADOW
    assert conf.band("auto_book") == "shadow"

    # 2) shadow 比对命中 3 次 → 自动转正为 auto（并自动挂 canary）
    states.set("light.study", "on")
    for _ in range(3):
        rec_node = shadow.run_do(inst, node)
        clock.advance(301)
        shadow.compare(rec_node.record_id)
    assert conf.band("auto_book") == "auto"
    assert auto.nodes["n1"].canary == {"duration": "24h", "auto_rollback": True}
    assert canary.record("auto_book").status == "observing"

    # 3) canary 观察 24h 无漂移 → 全量运行（去掉 canary 保护）
    det.note_call("auto_book", "i1", "n1", "light.study", "turn_on", "on")
    states.set("light.study", "on")
    det.on_state_changed("light.study", "off", "on")
    canary.on_canary_result("auto_book", FakeCanaryResult(drift=False))
    clock.advance(24 * 3600 + 1)
    assert canary.check() == ["auto_book"]
    assert auto.nodes["n1"].canary is None
    assert canary.record("auto_book").status == "promoted"

    # 4) 用户手动覆盖 → 负样本 → 降回 shadow
    det.on_state_changed("light.study", "on", "off")
    assert rec.events[-1].kind is FeedbackKind.USER_OVERRIDE
    assert conf.band("auto_book") == "shadow"
    assert canary.check() == ["auto_book"]
    assert canary.record("auto_book").status == "demoted"


def test_ask_band_end_to_end():
    """conf < 0.60：触发不执行 do，生成 ask 提案，人确认后才执行。"""
    clock = FakeClock()
    conf = make_conf({"auto_book": 0.45})
    states = FakeStates()
    rec = make_recorder(conf, clock)
    node = FakeNode()
    auto = FakeAutomation(id="auto_book", nodes={"n1": node})
    inst = FakeInstance(automation=auto)
    resumed = []
    mgr = ProposalManager(conf=conf, recorder=rec, clock=clock, resumer=resumed.append)
    shadow = make_shadow(conf, clock, rec, states)
    shadow.ask_handler = mgr.open_ask
    executor = FakeExecutor()
    shadow.install(executor)

    assert executor._do(inst, node) is None
    assert executor.calls == []
    pending = mgr.list("pending")
    assert len(pending) == 1 and pending[0].action == "turn_on"
