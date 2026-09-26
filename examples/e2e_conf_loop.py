"""conf 分级引擎端到端验证：MA 提案 → shadow → 转正 → canary 晋升 → 人工干预降级。

运行：python examples/e2e_conf_loop.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from autoforge.af_conf import ConfidenceStore
from autoforge.af_shadow import ShadowRunner, ShadowPolicy
from autoforge.af_intervention import InterventionDetector, InterventionPolicy
from autoforge.af_canary_supervisor import CanarySupervisor, CanaryPolicy
from autoforge.af_proposal import ProposalManager
from autoforge.af_feedback import FeedbackRecorder, FeedbackExporter, FeedbackFilter


class FakeClock:
    def __init__(self):
        self.t = 0.0
    def now(self):
        return self.t
    def advance(self, s):
        self.t += s


class FakeStates:
    def __init__(self):
        self.data = {}
    def get(self, eid):
        return self.data.get(eid)
    def set(self, eid, val):
        self.data[eid] = val


class FakeScheduler:
    def __init__(self, clock):
        self.clock = clock
        self.tasks = []
    def call_later(self, delay, fn):
        self.tasks.append((self.clock.now() + delay, fn))
    def advance(self, seconds):
        self.clock.advance(seconds)
        due = [t for t in self.tasks if t[0] <= self.clock.now()]
        self.tasks = [t for t in self.tasks if t[0] > self.clock.now()]
        for _, fn in due:
            fn()


class FakeAudit:
    def __init__(self):
        self.entries = []
    def append(self, entry):
        self.entries.append(dict(entry))
    def kinds(self):
        return [e.get("kind", "") for e in self.entries]


class FakeNode:
    def __init__(self, action="turn_on", params=None):
        self.kind = "do"
        self.action = action
        self.params = params or {"entity_id": "light.study"}
        self.id = "d1"
        self.adapter = "ha"
        self.canary = None
    def target_entities(self):
        return [self.params.get("entity_id", "")]


class FakeAutomation:
    def __init__(self, aid, conf=0.75):
        self.id = aid
        self.confidence = conf
        self.nodes = {"d1": FakeNode()}


class FakeInstance:
    def __init__(self, aid):
        self.automation = FakeAutomation(aid)
        self.id = "i1"


class FakeGraph:
    def __init__(self):
        self.automations = {}


def main():
    tmp = tempfile.mkdtemp()
    clock = FakeClock()
    conf = ConfidenceStore(values={}, samples={})
    states = FakeStates()
    sched = FakeScheduler(clock)
    audit = FakeAudit()
    graph = FakeGraph()

    print("=" * 60)
    print("conf 分级引擎端到端验证")
    print("=" * 60)

    # 1. FeedbackRecorder
    recorder = FeedbackRecorder(conf=conf, clock=clock, audit=audit, states=states)

    # 2. ShadowRunner
    shadow = ShadowRunner(
        conf=conf, states=states, recorder=recorder, clock=clock, audit=audit,
        policy=ShadowPolicy(compare_after=10.0, streak_to_promote=3),
        later=lambda delay, fn: sched.call_later(delay, fn),
    )

    # 3. InterventionDetector
    intervention = InterventionDetector(
        conf=conf, recorder=recorder, clock=clock, audit=audit,
        policy=InterventionPolicy(window=30.0, hold_seconds=60.0),
        later=lambda delay, fn: sched.call_later(delay, fn),
    )
    intervention.mark_managed(["light.study"])

    # 4. CanarySupervisor
    canary = CanarySupervisor(
        conf=conf, recorder=recorder, clock=clock, audit=audit,
        policy=CanaryPolicy(observation_hours=24.0, max_drift=0),
    )

    # 5. ProposalManager
    deployed = {}
    def deploy_plan(plan):
        """deployer 接收 DeployPlan，返回 automation_id。"""
        aid = f"auto-{plan.proposal.hypothesis_id}"
        deployed[aid] = plan.band
        conf.values[aid] = {"auto": 0.90, "shadow": 0.70, "ask": 0.50}[plan.band]
        conf.samples[aid] = []
        graph.automations[aid] = FakeAutomation(aid, conf.values[aid])
        print(f"  部署 {aid} band={plan.band} conf={conf.values[aid]:.2f}")
        return aid

    proposals = ProposalManager(
        conf=conf, recorder=recorder, clock=clock, audit=audit,
        deployer=deploy_plan,
    )

    # 6. FeedbackExporter
    exporter = FeedbackExporter(recorder=recorder)

    print("\n【第1步】MA 提案 conf=0.75 → 自动部署 shadow")
    p = proposals.submit(
        hypothesis_id="h1",
        natural_language="书房温度>27 且门关着时，用户 80% 会在 5 分钟内开空调",
        conf=0.75,
        suggested_ir={"hypothesis_id": "h1"},
    )
    print(f"  提案状态: {p.status}")
    aid = p.automation_id
    assert aid in deployed, "shadow 应自动部署"
    print(f"  conf={conf.get(aid):.2f} band={conf.band(aid)}")

    print("\n【第2步】shadow 运行：do 被拦截，比对连续命中 3 次 → 转正 auto")
    for i in range(3):
        inst = FakeInstance(aid)
        node = FakeNode()
        shadow.run_do(inst, node)
        states.set("light.study", "on")  # turn_on 的期望状态是 "on"
        sched.advance(15)
        print(f"  第{i+1}次比对: conf={conf.get(aid):.2f} band={conf.band(aid)}")

    assert conf.band(aid) == "auto", f"3次命中后应转正为auto，实际{conf.band(aid)}"
    print(f"  ✅ 转正成功: conf={conf.get(aid):.2f} band={conf.band(aid)}")

    print("\n【第3步】canary 观察 24h 无漂移 → 晋升全量")
    canary.begin(aid)
    clock.advance(24 * 3600 + 1)
    promoted = canary.check()
    print(f"  晋升列表: {promoted}")
    assert aid in promoted, "观察期满无漂移应晋升"
    print(f"  ✅ canary 晋升全量")

    print("\n【第4步】用户手动覆盖 → 负样本降回 shadow")
    # 先模拟 AF 在 auto 模式下执行了开灯动作（登记 pending）
    intervention.note_call(aid, "i1", "d1", "light.study", "turn_on", "on")
    # 状态变化到 on（AF 动作落地）→ 产生 applied state
    intervention.on_state_changed("light.study", "off", "on", at=clock.now())
    # 用户手动关灯（覆盖 AF 的 applied state）→ MANUAL_OVERRIDE 负样本
    before = conf.get(aid)
    intervention.on_state_changed("light.study", "on", "off", at=clock.now())
    after = conf.get(aid)
    print(f"  conf: {before:.2f} → {after:.2f} band={conf.band(aid)}")
    assert conf.band(aid) == "shadow", f"用户干预后应降为shadow，实际{conf.band(aid)}"
    print(f"  ✅ 人工干预降级成功")

    print("\n【第5步】MA 拉取反馈")
    feedback = exporter.to_json()
    for block in feedback:
        if block["automation_id"] == aid:
            types = [e["type"] for e in block["events"]]
            print(f"  自动化 {aid} 的反馈事件: {types}")
            assert "user_override" in types, "应包含 user_override 事件"

    print("\n" + "=" * 60)
    print("✅ E2E 全部通过：shadow → 转正 → canary 晋升 → 干预降级 → 反馈回灌")
    print("=" * 60)


if __name__ == "__main__":
    main()
