import importlib
import importlib.util
import pathlib
import sys
from datetime import datetime


def _load(name: str):
    for dotted in (f"autoforge.{name}", f"af.{name}", name):
        try:
            return importlib.import_module(dotted)
        except ImportError:
            continue
    root = pathlib.Path(__file__).resolve().parents[1]
    anchors = sorted(root.rglob("af_executor.py")) or [root / "_none.py"]
    candidates = [anchors[0].parent / f"{name}.py"] + [
        p for p in sorted(root.rglob(f"{name}.py")) if "tests" not in p.parts
    ]
    for path in candidates:
        if not path.exists():
            continue
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    raise ImportError(f"cannot locate {name}.py")


af_conflict = _load("af_conflict")
ConflictArbiter = af_conflict.ConflictArbiter
RequestDecision = af_conflict.RequestDecision


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = float(start)

    def monotonic(self) -> float:
        return self.t

    def now(self) -> datetime:
        return datetime.fromtimestamp(self.t)

    def advance(self, seconds: float) -> None:
        self.t += float(seconds)


class FakeConf:
    def __init__(self, values=None, band: str = "auto") -> None:
        self.values = dict(values or {})
        self.default_band = band
        self.negatives = []
        self.positives = []

    def get(self, automation_id: str) -> float:
        return float(self.values.get(automation_id, 0.5))

    def band(self, automation_id: str) -> str:
        return self.default_band

    def record_negative(self, automation_id: str) -> None:
        self.negatives.append(automation_id)

    def record_positive(self, automation_id: str) -> None:
        self.positives.append(automation_id)


class BrokenConf(FakeConf):
    def get(self, automation_id: str) -> float:
        raise RuntimeError("conf store down")


def make_arbiter(conf=None, clock=None, **kwargs):
    events, preemptions, ready = [], [], []
    arb = ConflictArbiter(
        conf or FakeConf(),
        clock or FakeClock(),
        on_event=events.append,
        on_preempted=preemptions.append,
        on_pending_ready=ready.append,
        **kwargs,
    )
    return arb, events, preemptions, ready


def kinds(events):
    return [e.kind for e in events]


def test_request_without_lock_creates_lock_and_allows():
    arb, events, _, _ = make_arbiter(FakeConf({"A": 0.9}), FakeClock())
    assert arb.request(["light.study"], "A", "i-1", "light.turn_on", {}) is RequestDecision.ALLOW
    lock = arb.locks()["light.study"]
    assert (lock.automation_id, lock.instance_id, lock.action) == ("A", "i-1", "light.turn_on")
    assert lock.priority == 0.9


def test_same_priority_second_request_is_rejected():
    conf = FakeConf({"A": 0.9, "B": 0.9})
    arb, events, _, _ = make_arbiter(conf, FakeClock())
    arb.request(["light.study"], "A", "i-a", "light.turn_on", {})
    assert arb.request(["light.study"], "B", "i-b", "light.turn_off", {}) is RequestDecision.REJECT
    assert arb.locks()["light.study"].automation_id == "A"
    assert "B" in conf.negatives                       # 被拒绝 → conf 负样本
    assert kinds(events)[-1] == "rejected"


def test_higher_priority_preempts_and_notifies_old_holder():
    conf = FakeConf({"A": 0.85, "B": 0.95})
    arb, events, preemptions, _ = make_arbiter(conf, FakeClock())
    arb.request(["light.study"], "A", "i-a", "light.turn_on", {})
    assert arb.request(["light.study"], "B", "i-b", "light.turn_off", {}) is RequestDecision.ALLOW
    assert arb.locks()["light.study"].automation_id == "B"
    assert kinds(events).count("preempted") == 1
    assert preemptions and preemptions[0].automation_id == "A"
    assert "A" in conf.negatives                        # 被抢占者记负样本


def test_same_automation_request_renews_lock():
    arb, _, _, _ = make_arbiter(FakeConf({"A": 0.9}), FakeClock())
    arb.request(["light.study"], "A", "i-1", "light.turn_on", {})
    first = arb.locks()["light.study"].acquired_at
    clock = arb.clock
    clock.advance(5)
    assert arb.request(["light.study"], "A", "i-2", "light.turn_on", {}) is RequestDecision.ALLOW
    lock = arb.locks()["light.study"]
    assert lock.acquired_at == first + 5                # 续期：TTL 从第二次算起
    clock.advance(6)                                    # 距首次 11s，距续期 6s
    assert arb.request(["light.study"], "B", "i-b", "x", {}) is RequestDecision.REJECT


def test_lock_ttl_expiry_frees_entity():
    conf = FakeConf({"A": 0.9, "B": 0.9})
    clock = FakeClock()
    arb, _, _, _ = make_arbiter(conf, clock, lock_ttl=10.0)
    arb.request(["light.study"], "A", "i-a", "light.turn_on", {})
    clock.advance(10)
    assert arb.request(["light.study"], "B", "i-b", "light.turn_off", {}) is RequestDecision.ALLOW
    assert arb.locks()["light.study"].automation_id == "B"


def test_flicker_detection_pauses_entity():
    conf = FakeConf({"A": 0.9, "B": 0.9})
    clock = FakeClock()
    arb, events, _, _ = make_arbiter(conf, clock, flicker_threshold=4, flicker_window=10.0, flicker_pause=60.0)
    for i in range(4):                                  # 10 秒内翻转 4 次
        assert arb.request(["light.study"], "A", f"i-{i}", "light.toggle", {}) is RequestDecision.ALLOW
        arb.release(["light.study"], "A")
        clock.advance(1)
    assert "flicker" in kinds(events)
    assert arb.request(["light.study"], "B", "i-b", "light.turn_off", {}) is RequestDecision.REJECT
    assert kinds(events)[-1] == "throttled"
    clock.advance(60)
    assert arb.request(["light.study"], "B", "i-b2", "light.turn_off", {}) is RequestDecision.ALLOW


def test_circuit_opens_then_half_open_and_recovers():
    conf = FakeConf({"X": 0.5, "H": 0.95})
    clock = FakeClock()
    arb, events, _, _ = make_arbiter(conf, clock)
    arb.request(["light.study"], "H", "i-h", "light.turn_on", {})
    for i in range(5):                                  # 冲突计数 ≥ 5 → 熔断
        assert arb.request(["light.study"], "X", f"i-{i}", "light.turn_off", {}) is RequestDecision.REJECT
    assert arb.circuit_state("X")["status"] == "open"
    assert arb.request(["light.study"], "X", "i-9", "light.turn_off", {}) is RequestDecision.CIRCUIT_OPEN
    arb.release(["light.study"], "H")
    clock.advance(300)                                  # 5 分钟后半开
    assert arb.request(["light.study"], "X", "i-t1", "light.turn_off", {}) is RequestDecision.ALLOW
    arb.release(["light.study"], "X", success=True)
    assert arb.circuit_state("X")["status"] == "half_open"   # 单次成功不足以恢复
    assert arb.request(["light.study"], "X", "i-t2", "light.turn_off", {}) is RequestDecision.ALLOW
    arb.release(["light.study"], "X", success=True)
    assert arb.circuit_state("X")["status"] == "closed"
    assert "circuit_half_open" in kinds(events) and "circuit_recovered" in kinds(events)


def test_user_override_releases_lock_and_cools_down():
    conf = FakeConf({"A": 0.9, "B": 0.9})
    clock = FakeClock()
    arb, events, _, _ = make_arbiter(conf, clock, cooldown_after_user=30.0)
    arb.request(["light.study"], "A", "i-a", "light.turn_on", {})
    arb.on_user_override("light.study")
    assert arb.locks() == {}                             # 用户操作释放锁
    assert arb.request(["light.study"], "B", "i-b", "light.turn_off", {}) is RequestDecision.REJECT
    assert kinds(events)[-1] == "user_cooldown"
    clock.advance(31)
    assert arb.request(["light.study"], "B", "i-b2", "light.turn_off", {}) is RequestDecision.ALLOW


def test_user_cooldown_beats_circuit():
    conf = FakeConf({"C": 0.5, "H": 0.95})
    clock = FakeClock()
    arb, events, _, _ = make_arbiter(conf, clock)
    arb.request(["light.study"], "H", "i-h", "light.turn_on", {})
    for i in range(5):
        arb.request(["light.study"], "C", f"i-{i}", "light.turn_off", {})
    assert arb.circuit_state("C")["status"] == "open"
    arb.on_user_override("light.study")
    assert arb.request(["light.study"], "C", "i-9", "light.turn_off", {}) is RequestDecision.REJECT
    assert kinds(events)[-1] == "user_cooldown"          # 用户冷却优先于熔断


def test_multi_entity_all_or_nothing():
    conf = FakeConf({"X": 0.5, "H": 0.95, "Y": 0.98})
    arb, _, _, _ = make_arbiter(conf, FakeClock())
    arb.request(["light.a"], "H", "i-h", "light.turn_on", {})
    assert arb.request(["light.a", "light.b"], "X", "i-x", "light.turn_off", {}) is RequestDecision.REJECT
    assert "light.b" not in arb.locks()                  # 任一失败已获取的全部释放
    assert arb.locks()["light.a"].automation_id == "H"
    assert arb.request(["light.b", "light.a"], "Y", "i-y", "light.turn_off", {}) is RequestDecision.ALLOW
    assert sorted(arb.locks()) == ["light.a", "light.b"]


def test_multi_entity_acquires_in_sorted_order():
    arb, _, _, _ = make_arbiter(FakeConf({"A": 0.9}), FakeClock())
    order = []
    original = arb._acquire

    def spy(ids, *args, **kwargs):
        order.append(list(ids))
        return original(ids, *args, **kwargs)

    arb._acquire = spy
    arb.request(["light.b", "light.a", "light.a"], "A", "i-1", "light.turn_on", {})
    assert order == [["light.a", "light.b"]]             # 字典序获取 → 无死锁


def test_aging_lets_low_priority_preempt():
    conf = FakeConf({"A": 0.85, "B": 0.70})
    clock = FakeClock()
    arb, events, preemptions, _ = make_arbiter(conf, clock, aging_rate=0.01, aging_cap=0.20, lock_ttl=100.0)
    arb.request(["light.study"], "A", "i-a", "light.turn_on", {})
    assert arb.request(["light.study"], "B", "i-b", "light.turn_off", {}) is RequestDecision.REJECT
    clock.advance(20)                                   # 20 秒 → +0.20
    assert arb.request(["light.study"], "B", "i-b", "light.turn_off", {}) is RequestDecision.ALLOW
    assert arb.locks()["light.study"].automation_id == "B"
    assert preemptions and preemptions[0].automation_id == "A"


def test_wait_enabled_queues_and_wakes_on_release():
    conf = FakeConf({"A": 0.95, "B": 0.5})
    clock = FakeClock()
    arb, _, _, ready = make_arbiter(conf, clock, wait_enabled=True)
    arb.request(["light.study"], "A", "i-a", "light.turn_on", {})
    assert arb.request(["light.study"], "B", "i-b", "light.turn_off", {}) is RequestDecision.WAIT
    assert not ready
    arb.release(["light.study"], "A")
    assert ready and ready[0].automation_id == "B"       # 锁释放 → 唤醒等待者


def test_internal_error_fails_closed_and_audits():
    events = []
    arb = ConflictArbiter(BrokenConf(), FakeClock(), on_event=events.append)
    assert arb.request(["light.study"], "A", "i-1", "light.turn_on", {}) is RequestDecision.REJECT
    assert kinds(events) == ["degraded"]                 # 降级 REJECT + 记录错误
    # DCD 20261008 裁定§三：仲裁器内层 fail-closed。内层失明 = 守卫本体失明，
    # 放行是"覆盖发生"的 UNSAFE 失败，拒发是"没覆盖"的 SAFE 失败。
    # 与外层（af_conflict_runtime）的两站 fail-closed 同形状。
    assert events[0].details["fail_open"] is False
