"""E2E: 人进书房开灯 vs 全屋关灯的冲突仲裁。"""
from datetime import datetime
from autoforge.af_conflict import ConflictArbiter, RequestDecision
from autoforge.af_conflict_audit import ConflictAuditor
from autoforge.af_conf import ConfidenceStore


class _Clock:
    def monotonic(self): return 1000.0
    def now(self): return datetime.fromtimestamp(1000.0)


conf  = ConfidenceStore(values={"auto_A_person_light": 0.90, "auto_B_night_off": 0.85}, samples={})
clock = _Clock()
audit = ConflictAuditor(persist_dir=".forge")
arb   = ConflictArbiter(conf, clock, on_event=audit.record)
audit.lock_provider = lambda: arb.locks().values()


def test_scenario():
    # A(0.90) 开灯 → ALLOW，持有 light.study
    d1 = arb.request(["light.study"], "auto_A_person_light", "inst-1", "light.turn_on", {})
    assert d1 is RequestDecision.ALLOW

    # B(0.85) 关灯 → REJECT（0.85 < 0.90，不抢占）
    d2 = arb.request(["light.study"], "auto_B_night_off", "inst-2", "light.turn_off", {})
    assert d2 is RequestDecision.REJECT

    # A 完成后释放
    arb.release(["light.study"], "auto_A_person_light", success=True)
    assert "light.study" not in arb.locks()
