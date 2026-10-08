import importlib
import importlib.util
import ast
import pathlib
import sys
from dataclasses import dataclass, field
from datetime import datetime
from types import SimpleNamespace


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
af_conflict_audit = _load("af_conflict_audit")
af_conflict_runtime = _load("af_conflict_runtime")

RequestDecision = af_conflict.RequestDecision
ConflictAuditor = af_conflict_audit.ConflictAuditor
ConflictService = af_conflict_runtime.ConflictService
ConflictSettings = af_conflict_runtime.ConflictSettings
ConflictBlocked = af_conflict_runtime.ConflictBlocked
install_api = af_conflict_runtime.install_api


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

    def get(self, automation_id: str) -> float:
        return float(self.values.get(automation_id, 0.5))

    def band(self, automation_id: str) -> str:
        return self.default_band

    def record_negative(self, automation_id: str) -> None:
        self.negatives.append(automation_id)


@dataclass
class CallResult:
    success: bool
    data: dict = field(default_factory=dict)
    error: str | None = None


class FakeAdapter:
    dry_run = False

    def __init__(self, log):
        self.log = log

    def call(self, action, params):
        self.log.append(("call", action))
        return CallResult(success=True, data={})


class FakeExecutor:
    def __init__(self, log):
        self.log = log
        self.adapters = {"light": FakeAdapter(log)}
        self.soft_fail = None
        self.on_call = None

    def _do(self, instance, node):
        self.log.append(("do", node.action))
        if self.on_call is not None:
            self.on_call()
        adapter = self.adapters.get(node.adapter)
        result = adapter.call(node.action or "", dict(node.params))
        return {"then"} if result.success else {"on_error"}

    def _soft_fail(self, instance, node, exc):
        self.soft_fail = exc
        self.log.append(("soft_fail", getattr(exc, "reason", "")))
        return {"on_error"}


class FakeIntervention:
    def __init__(self, kind="af_caused"):
        self.kind = kind
        self.notes = []

    def note_af_action(self, automation_id, entity_id, action, expected_state):
        self.notes.append((automation_id, entity_id, action, expected_state))

    def handle_state_change(self, entity_id, new_state, old_state=None, timestamp=None):
        return SimpleNamespace(kind=self.kind, entity_id=entity_id)


class FakeScheduler:
    def __init__(self):
        self.jobs = []

    def call_later(self, delay, fn):
        self.jobs.append((delay, fn))


class FakeApp:
    def __init__(self):
        self.routes = {}

    def add_api_route(self, path, fn, methods=None):
        for method in methods or ["GET"]:
            self.routes[(method, path)] = fn


def make_node(action="light.turn_on", entity="light.study"):
    return SimpleNamespace(adapter="light", action=action, params={"entity_id": entity}, canary=None)


def make_instance(automation_id="A", instance_id="i-1"):
    return SimpleNamespace(automation=SimpleNamespace(id=automation_id), instance_id=instance_id)


def make_service(mode="enforce", conf=None, intervention=None, scheduler=None, on_resume=None, **settings):
    conf = conf or FakeConf({"A": 0.9, "B": 0.99, "X": 0.5})
    cfg = ConflictSettings(mode=mode, persist_dir=None, **settings)
    return ConflictService(
        conf,
        FakeClock(),
        cfg,
        intervention=intervention,
        scheduler=scheduler,
        on_resume=on_resume,
        auditor=ConflictAuditor(persist_dir=None),
    )


def test_enabled_do_requests_before_adapter_call():
    log = []
    service = make_service()
    order = []
    original_request = service.arbiter.request
    original_release = service.arbiter.release

    def spy_request(*args, **kwargs):
        order.append("request")
        return original_request(*args, **kwargs)

    def spy_release(*args, **kwargs):
        order.append("release")
        return original_release(*args, **kwargs)

    service.arbiter.request = spy_request
    service.arbiter.release = spy_release
    executor = FakeExecutor(log)
    service.attach(executor)
    edges = executor._do(make_instance(), make_node())
    assert edges == {"then"}
    assert order == ["request", "release"]
    assert ("call", "light.turn_on") in log


def test_reject_routes_to_on_error_without_call():
    log = []
    service = make_service()
    executor = FakeExecutor(log)
    service.attach(executor)
    service.arbiter.request(["light.study"], "B", "i-b", "light.turn_off", {})   # 高优先级占锁
    edges = executor._do(make_instance("A", "i-1"), make_node())
    assert edges == {"on_error"}
    assert isinstance(executor.soft_fail, ConflictBlocked)
    assert not any(item[0] == "call" for item in log)                            # adapter.call 未发生
    assert [e.kind for e in service.auditor.events][-1] == "rejected"


def test_allow_executes_then_releases_lock():
    log = []
    service = make_service()
    executor = FakeExecutor(log)
    service.attach(executor)
    edges = executor._do(make_instance("A", "i-1"), make_node())
    assert edges == {"then"}
    assert service.arbiter.locks() == {}                     # 显式 release 已释放
    assert [e.kind for e in service.auditor.events] == []    # 无冲突不产生事件


def test_allow_records_note_af_action():
    intervention = FakeIntervention()
    service = make_service(intervention=intervention)
    executor = FakeExecutor([])
    service.attach(executor)
    executor._do(make_instance("A", "i-1"), make_node())
    assert intervention.notes == [("A", "light.study", "light.turn_on", "on")]


def test_user_override_releases_lock_and_cools_entity():
    service = make_service(intervention=FakeIntervention(kind="user_override"))
    executor = FakeExecutor([])
    service.attach(executor)
    service.arbiter.request(["light.study"], "B", "i-b", "light.turn_off", {})
    assert "light.study" in service.arbiter.locks()
    service.handle_state_change("light.study", "off", "on")     # 用户手动关灯
    assert service.arbiter.locks() == {}
    edges = executor._do(make_instance("A", "i-1"), make_node())
    assert edges == {"on_error"}                                # 冷却期内不被自动控制
    assert [e.kind for e in service.auditor.events][-1] == "user_cooldown"


def test_arbiter_error_refuses_and_records_fail_closed():
    """裁定 20261008 §二 裁 A① 的同族推广：request 抛 = 这次动作根本没拿到锁 = 守卫失明。

    改判前这条腿断言的是 `{"then"}`（「故障优先：降级 ALLOW」）。留着它等于把旧口径
    钉在绿灯里，所以整条换向：不执行 + 走 on_error + 台账记 `fail_open: False`。
    """
    service = make_service()
    log = []
    executor = FakeExecutor(log)
    service.attach(executor)

    def boom(*args, **kwargs):
        raise RuntimeError("arbiter down")

    service.arbiter.request = boom
    edges = executor._do(make_instance("A", "i-1"), make_node())
    assert edges == {"on_error"}
    assert executor.soft_fail.reason == "request_failed:RuntimeError"
    assert not any(item[0] == "call" for item in log)          # adapter.call 未发生
    events = [e for e in service.auditor.events if e.kind == "degraded"]
    assert len(events) == 1
    assert events[0].details["phase"] == "request"
    assert events[0].details["fail_open"] is False


def test_shadow_automation_bypasses_arbitration():
    conf = FakeConf({"S": 0.9}, band="shadow")
    service = make_service(conf=conf)
    executor = FakeExecutor([])
    service.attach(executor)
    calls = []
    original = service.arbiter.request
    service.arbiter.request = lambda *a, **k: calls.append(1) or original(*a, **k)
    edges = executor._do(make_instance("S", "i-1"), make_node())
    assert edges == {"then"}
    assert calls == []                                          # shadow 不参与冲突
    assert service.arbiter.locks() == {}


def test_observe_mode_audits_but_does_not_block():
    service = make_service(mode="observe")
    executor = FakeExecutor([])
    service.attach(executor)
    service.arbiter.request(["light.study"], "B", "i-b", "light.turn_off", {})   # 冲突源
    edges = executor._do(make_instance("A", "i-1"), make_node())
    assert edges == {"then"}                                    # 只观测不拦截
    assert [e.kind for e in service.auditor.events] == ["rejected"]   # 事件不可跳过


def test_preempted_inflight_do_routes_to_on_error():
    log = []
    service = make_service(conf=FakeConf({"A": 0.85, "B": 0.95}))
    executor = FakeExecutor(log)
    service.attach(executor)
    executor.on_call = lambda: service.arbiter.request(
        ["light.study"], "B", "i-b", "light.turn_off", {}
    )
    edges = executor._do(make_instance("A", "i-1"), make_node())
    assert edges == {"on_error"}                                # 被抢占 → on_error 边
    assert executor.soft_fail.reason == "preempted"
    assert service.arbiter.locks()["light.study"].automation_id == "B"


def test_install_api_registers_endpoints_and_handlers_work():
    service = make_service()
    service.arbiter.request(["light.study"], "B", "i-b", "light.turn_off", {})
    app = FakeApp()
    assert install_api(app, service) is True
    assert set(app.routes) == {
        ("GET", "/api/conflicts"),
        ("GET", "/api/conflicts/summary"),
        ("GET", "/api/conflicts/locks"),
        ("POST", "/api/conflicts/{automation_id}/reset"),
        ("DELETE", "/api/conflicts/locks/{entity_id}"),
    }
    assert app.routes[("GET", "/api/conflicts/locks")]()["locks"][0]["entity_id"] == "light.study"
    assert app.routes[("POST", "/api/conflicts/{automation_id}/reset")](automation_id="B")["ok"] is True
    assert app.routes[("DELETE", "/api/conflicts/locks/{entity_id}")](entity_id="light.study")["released"] is True
    assert app.routes[("GET", "/api/conflicts")](limit=10)["count"] == 2


def test_wait_decision_parks_and_resumes_via_scheduler():
    scheduler = FakeScheduler()
    resumed = []
    service = make_service(wait_enabled=True, scheduler=scheduler, on_resume=lambda i, n, e: resumed.append(e))
    conf_holder = service.arbiter
    conf_holder.request(["light.study"], "B", "i-b", "light.turn_off", {})      # 0.99 占锁
    executor = FakeExecutor([])
    service.attach(executor)
    node, instance = make_node(), make_instance("A", "i-1")
    assert executor._do(instance, node) == ()                                   # WAIT → 挂起
    conf_holder.release(["light.study"], "B")
    assert scheduler.jobs                                                       # call_later(0, retry)
    scheduler.jobs[0][1]()
    assert resumed and resumed[0] == {"then"}                                   # 重试成功续跑


# ── F4：争抢落到生产证据（af_watch），但"放行"不算证据 ────────────────────


def _watch_row(automation_id):
    af_watch = _load("af_watch")
    for row in af_watch.verified_in_prod_partition()["automations"]:
        if row["automation_id"] == automation_id:
            return row
    return None


def _reset_watch():
    _load("af_watch").reset()


def test_reject_feeds_conflict_evidence():
    _reset_watch()
    service = make_service()
    executor = FakeExecutor([])
    service.attach(executor)
    service.arbiter.request(["light.study"], "B", "i-b", "light.turn_off", {})
    executor._do(make_instance("A", "i-1"), make_node())

    row = _watch_row("A")
    assert row is not None and row["conflict"] == 1
    # 被仲裁挡下 ≠ 生产验证失败，也绝不算"验过了"（三档各归各栏）
    assert row["verified_in_prod"] == 0 and row["failed_in_prod"] == 0
    _reset_watch()


def test_wait_and_preempted_each_feed_conflict_evidence():
    _reset_watch()
    service = make_service(wait_enabled=True, scheduler=FakeScheduler())
    service.arbiter.request(["light.study"], "B", "i-b", "light.turn_off", {})   # 0.99 占锁
    executor = FakeExecutor([])
    service.attach(executor)
    executor._do(make_instance("A", "i-1"), make_node())                          # WAIT → 排队
    assert _watch_row("A")["conflict"] == 1
    _reset_watch()

    log = []
    service2 = make_service(conf=FakeConf({"A": 0.85, "B": 0.95}))
    executor2 = FakeExecutor(log)
    service2.attach(executor2)
    executor2.on_call = lambda: service2.arbiter.request(
        ["light.study"], "B", "i-b", "light.turn_off", {}
    )
    assert executor2._do(make_instance("A", "i-1"), make_node()) == {"on_error"}   # 在飞被抢占
    row = _watch_row("A")
    assert row["conflict"] == 1
    _reset_watch()


def test_observe_mode_detection_is_still_evidence():
    """试演期不拦截，但"探测到争抢"本身就是生产证据——否则试演期结束时零证据。"""
    _reset_watch()
    service = make_service(mode="observe")
    executor = FakeExecutor([])
    service.attach(executor)
    service.arbiter.request(["light.study"], "B", "i-b", "light.turn_off", {})
    assert executor._do(make_instance("A", "i-1"), make_node()) == {"then"}
    assert _watch_row("A")["conflict"] == 1
    _reset_watch()


def test_plain_allow_feeds_no_production_evidence():
    """ALLOW 只是"没拦住"。喂进聚合器会把仲裁器的存在算成生产验证证据（铁律 #5）。"""
    _reset_watch()
    service = make_service()
    executor = FakeExecutor([])
    service.attach(executor)
    assert executor._do(make_instance("A", "i-1"), make_node()) == {"then"}
    assert _watch_row("A") is None
    assert _load("af_watch").verified_in_prod_partition()["automations"] == []
    _reset_watch()


def test_watch_feed_failure_does_not_block_dispatch():
    """聚合器抛错不得影响下发（与 af_shadow 的 watch_record_error 同口径）。"""
    _reset_watch()
    af_watch = _load("af_watch")
    original = af_watch.record_conflict

    def boom(*args, **kwargs):
        raise RuntimeError("聚合器炸了")

    af_watch.record_conflict = boom
    try:
        service = make_service()
        log = []
        executor = FakeExecutor(log)
        service.attach(executor)
        service.arbiter.request(["light.study"], "B", "i-b", "light.turn_off", {})
        assert executor._do(make_instance("A", "i-1"), make_node()) == {"on_error"}
        assert not any(item[0] == "call" for item in log)                     # 拦截逻辑照旧
    finally:
        af_watch.record_conflict = original
    _reset_watch()


# ── 裁定 20261008 §二 裁 A：守卫失明（内省 / request 自身抛异常）不再放行 ─────────
#
# 两条放行点必须**分开**（裁 A① vs 裁 A②）：
#   ① 内省/request 抛异常 → 锁必然装不上 → fail-closed 拒绝 + 落审计 + Q2 的 owner 可见通知；
#   ② 内省成功却挖不出实体 → 正常形状 → 照旧放行。
# 把 ① 写成 `except Exception: entity_ids = []` 就顺着 ② 溜了，行为看起来仍"对"——
# 那正是本仓反复判红的"修了等于没修"形状，所以除了行为腿还有一条 AST 结构腿钉住两档分离。


def _blind_node(params):
    return SimpleNamespace(adapter="light", action="light.turn_on", params=params, canary=None)


def _deep_params(levels: int = 70):
    """超过 `MAX_PARAM_DEPTH`（64）的嵌套容器 → `extract_entity_ids` 抛 ParamDepthError。"""
    node = {"entity_id": "light.study"}
    for _ in range(levels):
        node = {"target": node}
    return node


def _degraded(service):
    return [e for e in service.auditor.events if e.kind == "degraded"]


def test_introspect_exception_refuses_the_action():
    """裁 A①：内省抛代码 bug 那一档——动作根本不发出。"""
    log = []
    service = make_service()
    executor = FakeExecutor(log)
    service.attach(executor)
    edges = executor._do(make_instance("A", "i-1"), _blind_node(object()))    # dict(object()) 抛 TypeError
    assert edges == {"on_error"}
    assert executor.soft_fail.reason == "introspect_failed:TypeError"
    assert not any(item[0] == "call" for item in log)
    events = _degraded(service)
    assert len(events) == 1
    assert events[0].details["phase"] == "introspect"
    assert events[0].details["fail_open"] is False


def test_param_depth_over_budget_refuses():
    """裁 A①点名的一档：超预算与代码 bug 同处理——后果相同（覆盖发生），只是触发面不同。"""
    log = []
    service = make_service()
    executor = FakeExecutor(log)
    service.attach(executor)
    edges = executor._do(make_instance("A", "i-1"), _blind_node(_deep_params()))
    assert edges == {"on_error"}
    assert executor.soft_fail.reason == "introspect_failed:ParamDepthError"
    assert not any(item[0] == "call" for item in log)
    assert _degraded(service)[-1].details["fail_open"] is False


def test_introspect_success_without_entities_still_executes():
    """裁 A②的 CONTROL 腿：只读/无实体节点必须照旧跑，否则 fail-closed 变成"全体停摆"。"""
    log = []
    service = make_service()
    executor = FakeExecutor(log)
    service.attach(executor)
    edges = executor._do(make_instance("A", "i-1"), _blind_node({"query": "temperature"}))
    assert edges == {"then"}
    assert ("call", "light.turn_on") in log
    assert _degraded(service) == []                     # 放行不是降级：台账不该出现 degraded
    assert service.arbiter.locks() == {}                # 也没给它装锁


def test_observe_mode_observes_even_when_the_guard_is_blind():
    """试演期的承诺是"不改行为"：两档都照旧放行，但要说自己放行了（`fail_open: True`）。

    内省腿用超预算那份参数——执行器自己 `dict(node.params)` 不炸、守卫的深度预算炸，
    这样断言到的才是"守卫瞎了但仍放行"，而不是"节点本身坏到两边都跑不动"。
    """
    log = []
    service = make_service(mode="observe")
    executor = FakeExecutor(log)
    service.attach(executor)
    assert executor._do(make_instance("A", "i-1"), _blind_node(_deep_params())) == {"then"}
    assert ("call", "light.turn_on") in log
    assert _degraded(service)[-1].details["fail_open"] is True

    service2 = make_service(mode="observe")
    executor2 = FakeExecutor([])
    service2.attach(executor2)

    def boom(*args, **kwargs):
        raise RuntimeError("arbiter down")

    service2.arbiter.request = boom
    assert executor2._do(make_instance("A", "i-1"), make_node()) == {"then"}
    assert _degraded(service2)[-1].details["fail_open"] is True


def test_the_two_allow_points_are_separated_in_source():
    """结构腿：裁 A②那条放行必须是 dispatch 函数体顶层，不在内省 except 处理器里。

    钉住的是"最省事的合并写法"：`except Exception: entity_ids = []`——行为上让守卫失明
    顺着空实体档溜走，而所有行为腿在这一刻仍全绿。
    """
    tree = ast.parse(pathlib.Path(af_conflict_runtime.__file__).read_text(encoding="utf-8"))
    svc = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ConflictService")
    dispatch = next(n for n in svc.body if isinstance(n, ast.FunctionDef) and n.name == "dispatch")
    trials = [n for n in ast.walk(dispatch) if isinstance(n, ast.Try)]
    introspect = next(t for t in trials if "extract_entity_ids(" in ast.unparse(t))
    handler = ast.unparse(introspect.handlers[0])
    assert "self._abort(" in handler, "内省异常那档必须真的拒发"
    assert "fail_open=observe" in handler, "台账要记录这一条到底放没放行"
    assert "if not entity_ids or not automation_id" not in handler, "两档被合并了：失明会顺着空实体档溜走"
    top = [ast.unparse(s) for s in dispatch.body]
    assert any("if not entity_ids or not automation_id" in s for s in top), "裁 A②的放行点必须留在函数体顶层"


# ── Q2=是：拒绝要 owner 可见（监护视图常驻指示 + 出向事件）──────────────────


class FakeBridge:
    def __init__(self):
        self.codes = []
        self.published = 0

    def mark_degraded(self, code):
        self.codes.append(code)

    def publish_degraded(self):
        self.published += 1
        return {"published": True}


class ExplodingBridge(FakeBridge):
    def mark_degraded(self, code):
        raise RuntimeError("broker down")


def test_guard_blind_refusal_is_resident_evidence_in_the_monitor_view():
    """常驻指示走 af_watch 的 conflict 列：被挡下 ≠ 生产验证失败，三档各归各栏。"""
    _reset_watch()
    af_watch = _load("af_watch")
    seen = []
    original = af_watch.record_conflict

    def spy(automation_id, status, at, detail=None):
        seen.append((automation_id, status, detail or {}))
        return original(automation_id, status, at, detail)

    af_watch.record_conflict = spy
    try:
        service = make_service()
        executor = FakeExecutor([])
        service.attach(executor)
        executor._do(make_instance("A", "i-1"), _blind_node(_deep_params()))
    finally:
        af_watch.record_conflict = original

    assert len(seen) == 1, "一次失明拒发只该有一条指示，不重复喂"
    automation_id, status, detail = seen[0]
    assert automation_id == "A" and status == "conflict"
    assert detail["reason"] == "guard_blind" and detail["phase"] == "introspect"
    assert "ParamDepthError" in detail["error"]
    row = _watch_row("A")
    assert row is not None and row["conflict"] == 1
    assert row["failed_in_prod"] == 0 and row["verified_in_prod"] == 0
    _reset_watch()


def test_guard_blind_refusal_publishes_the_outbound_degraded_snapshot(monkeypatch):
    """出向事件走 retained status（`af/status`）：`publish_failed` 的唯一生产者仍是 observe_terminal。

    本模块自己开第二个事件写者会撞 `check_mqtt_writers` 判据 B，那条门是 §二之二十二
    "测试测不到、对端却在收"换来的，不该为这一件拆掉。
    """
    from homesdk.adm.errors import ADM_ERR_INTERNAL

    af_mqtt_bridge = _load("af_mqtt_bridge")
    bridge = FakeBridge()
    monkeypatch.setattr(af_mqtt_bridge, "current_bridge", lambda: bridge)
    service = make_service()
    executor = FakeExecutor([])
    service.attach(executor)
    executor._do(make_instance("A", "i-1"), _blind_node(object()))
    assert bridge.codes == [ADM_ERR_INTERNAL]
    assert bridge.published == 1


def test_notification_failure_does_not_change_the_verdict(monkeypatch):
    """通知面（桥未接线 / 桥抛错）不得把拒发变回放行，也不得让异常冒进执行链。"""
    af_mqtt_bridge = _load("af_mqtt_bridge")
    monkeypatch.setattr(af_mqtt_bridge, "current_bridge", lambda: None)
    service = make_service()
    executor = FakeExecutor([])
    service.attach(executor)
    assert executor._do(make_instance("A", "i-1"), _blind_node(object())) == {"on_error"}

    monkeypatch.setattr(af_mqtt_bridge, "current_bridge", lambda: ExplodingBridge())
    service2 = make_service()
    executor2 = FakeExecutor([])
    service2.attach(executor2)
    assert executor2._do(make_instance("A", "i-1"), _blind_node(object())) == {"on_error"}
    assert service2.auditor.events[-1].details["fail_open"] is False
