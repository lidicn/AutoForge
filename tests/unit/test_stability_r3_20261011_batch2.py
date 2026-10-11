"""裁定 20261011《稳定性第三轮残余八问》窗内三件：Q2（乙）／Q5（乙）／Q6 F-17（乙）。

三件共同的形状是"把静默换成会红"：

* Q2 —— `Scheduler` 一直没有 `call_later`，`af_conflict_runtime.py:421` 却在调它，
  `af_runtime_ext.make_later` 按四个名字探测全不落 ⇒ 恒返 `None` ⇒ 冲突等待的 `_retry`
  在事件栈里同步重入（F-07／F-08）。补上接口，并把"三条未审开关默认关"钉成一条会红的判据。
* Q5 —— `group` 是 IR 层第 8 种节点，AF-Spec 文本层没有它的语法，过去落到「未知关键字」，
  读起来像拼写错误（F-16）。改成具名诊断（⛔ 不并 `_NODE_KINDS` 与 `NODE_KINDS`，那是功能面）。
* Q6 F-17 —— `build_ir` 那份兜底骨架三处不合 schema，于是无 `suggested_ir` 的提案"必然失败"，
  而这个必然失败正在替 owner 挡着"一份没审过的 IR 被自动部署到家里"（F-17）。删兜底 ⇒ NEEDS_REVIEW。
"""

import ast
import logging
from datetime import datetime
from pathlib import Path

import pytest

from autoforge import af_runtime_plugins, af_spec
from autoforge.af_ir import Graph, load_automation
from autoforge.af_proposal import (
    Proposal, ProposalError, ProposalManager, ProposalNeedsReview, ProposalStatus, build_ir,
)
from autoforge.af_runtime import build_runtime
from autoforge.af_runtime_ext import make_later
from conftest import FakeClock, make_conf, make_recorder

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src" / "autoforge"

MIN_IR = {
    "ir_version": "0.2.1", "id": "delayed_test", "name": "延迟回调", "version": 1,
    "mode": "single",
    "nodes": [
        {"id": "a1", "kind": "on",
         "trigger": {"type": "state", "entity_id": "binary_sensor.nothing", "to": "on"}},
        {"id": "p1", "kind": "pass"},
    ],
    "edges": [{"from": "a1", "to": "p1", "kind": "then"}],
}


class FakeMonotonic:
    """只喂 `call_later`／`_run_delayed` 需要的两枚读数，外加 `_fire_time_triggers` 要的 `local_now`。"""

    def __init__(self, t: float = 1_000.0) -> None:
        self.t = t

    def monotonic(self) -> float:
        return self.t

    def now(self) -> float:
        return self.t

    def local_now(self) -> datetime:
        return datetime.fromtimestamp(self.t)

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture()
def runtime():
    rt = build_runtime(Graph([load_automation(MIN_IR)]))
    rt.clock = FakeMonotonic()
    rt.scheduler.clock = rt.clock
    rt.instances.clock = rt.clock
    return rt


# ─────────────────────────────────────────────────────────────────────
# Q2 乙：`Scheduler.call_later` 真的存在，并且是"下一次 tick 才跑"
# ─────────────────────────────────────────────────────────────────────


def _scheduler_methods() -> set[str]:
    tree = ast.parse((SRC / "af_scheduler.py").read_text(encoding="utf-8"))
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == "Scheduler")
    return {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}


def test_scheduler_exposes_the_name_conflict_runtime_already_calls():
    """验收标准 3：那枚 grep 从 RC=1 变有命中——用 AST 钉，不靠文本碰运气。"""
    names = _scheduler_methods()
    assert "call_later" in names, f"Scheduler 又不出延迟接口了：{sorted(names)}"
    assert "_run_delayed" in names


def test_make_later_stops_returning_none(runtime):
    """`make_later` 四次探测的落点：过去恒 `None`，现在必须探得到 `call_later`。"""
    later = make_later(runtime.scheduler)
    assert later is not None, "make_later 又返 None ⇒ 冲突等待形同不存在"
    assert callable(later)
    assert make_later(None) is None, "None 调度器仍应给出 None（这一档不是回归）"


def test_call_later_does_not_run_the_callback_in_the_same_stack(runtime):
    """F-07 的正身：排期不等于执行。回调必须在调用栈之外、由 tick 驱动。"""
    seen = []
    runtime.scheduler.call_later(0.0, lambda: seen.append(1))
    assert seen == [], "call_later 同步执行了回调 ⇒ 还是那个重入形状"
    runtime.tick()
    assert seen == [1]


def test_callback_waits_until_the_deadline_passed(runtime):
    seen = []
    runtime.scheduler.call_later(10.0, lambda: seen.append(1))
    runtime.clock.advance(9.0)
    runtime.tick()
    assert seen == [], "未到点就被执行了——延迟语义丢了"
    runtime.clock.advance(1.5)
    runtime.tick()
    assert seen == [1]


def test_requeued_callback_is_not_run_again_in_the_same_tick(runtime):
    """回调里重新 `call_later(0.0, 自己)`：本轮只许跑一次，下一轮才跑第二次。"""
    runs = []

    def _cb():
        runs.append(len(runs) + 1)
        runtime.scheduler.call_later(0.0, _cb)

    runtime.scheduler.call_later(0.0, _cb)
    runtime.tick()
    assert len(runs) == 1, f"同一轮 tick 跑了 {len(runs)} 次 ⇒ 先摘再跑那条腿没生效"
    runtime.tick()
    assert len(runs) == 2


def test_a_failing_callback_does_not_stop_the_tick(runtime, caplog):
    """一条回调抛穿：其余回调照跑、tick 照收，且必须留下具名诊断（不静默）。"""
    tail = []
    with caplog.at_level(logging.ERROR, logger="autoforge"):
        runtime.scheduler.call_later(0.0, lambda: (_ for _ in ()).throw(RuntimeError("坏了")))
        runtime.scheduler.call_later(0.0, lambda: tail.append(1))
        fired = runtime.tick()
    assert fired == []
    assert tail == [1], "前一条回调抛穿就把后面的回调一起停了"
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "SCHED_DELAYED_CALLBACK_FAILED" in text, f"延迟回调抛穿没有具名留痕：{text[:200]}"
    assert caplog.records[0].levelname == "ERROR"


def test_delayed_queue_is_capped_without_dropping_scheduled_work(runtime):
    """上限那一档走"满了即抛"：既不静默淘汰已排的项，也不无界增长（BUG-01 那一族）。"""
    from autoforge.af_scheduler import DELAYED_MAX

    noop = lambda: None  # noqa: E731
    for _ in range(DELAYED_MAX):
        runtime.scheduler.call_later(3600.0, noop)
    assert len(runtime.scheduler._delayed) == DELAYED_MAX
    with pytest.raises(RuntimeError) as exc:
        runtime.scheduler.call_later(3600.0, noop)
    assert "SCHED_DELAYED_QUEUE_FULL" in str(exc.value)
    assert len(runtime.scheduler._delayed) == DELAYED_MAX, "溢出时把已排项挤掉了"


def test_non_callable_callback_is_rejected(runtime):
    with pytest.raises(TypeError):
        runtime.scheduler.call_later(1.0, "不是回调")


# ─────────────────────────────────────────────────────────────────────
# Q2 乙的判据半边：三条未审开关"默认＝关"，而且这条判据会红
# ─────────────────────────────────────────────────────────────────────


def test_unreviewed_runtime_faces_are_off_by_default(runtime):
    """空环境下装一次真装配：conf 分级／shadow／canary／冲突仲裁全不落。

    这一腿是裁定要的那条"会红判据"——哪天有人把默认值翻成开，它就会红。
    """
    ext = af_runtime_plugins.install(runtime, env={})
    assert ext.grading is None, "conf 分级引擎（含 shadow／canary）的默认值被翻成开了"
    assert ext.conflict is None, "冲突仲裁器的默认值被翻成开了"
    assert ext.enabled is False


def test_the_same_face_reacts_when_a_switch_is_flipped():
    """反空集：`_should_enable` 在"关"与"开"两档必须给出**不同**读数，否则上面那条判据是空的。

    ⛔ 这一腿不走 `install()` 的装配面：本仓真 `Runtime` 的审计面是 `AuditLog.add(AuditEvent)`，
    而 `af_runtime_plugins.install()` 里四枚留痕写的是 `runtime.audit.append(dict)`——
    把开关翻成"开"会先撞在那道不存在的脸上（下一腿把这件事钉成已知，不许读成"开关已可用"）。
    """
    assert af_runtime_plugins._should_enable("AUTOFORGE_CONF_GRADING", None,
                                            {"AUTOFORGE_CONF_GRADING": "1"}) is True
    assert af_runtime_plugins._should_enable("AUTOFORGE_CONF_GRADING", None, {}) is False
    assert af_runtime_plugins._env_flag("AUTOFORGE_RUNTIME_EXT", {}) is None


def test_extension_layer_is_not_wired_to_the_real_runtime_audit_face():
    """Q2 乙明确"三条未审面继续关着"——这一腿钉的就是"开着这条路今天走不通"。

    它是本批跑反空集时现读出来的，不是推断：`af_runtime_plugins.py:199/:201/:236/:241` 直调
    `runtime.audit.append(...)`，而 `af_audit.AuditLog` 只有 `add()`。所以把 conf 分级/冲突仲裁
    翻成"开"会在装第一步就 `AttributeError`。钉住两枚读数，将来谁真去审这块面，改完这两条就会红给他看。
    """
    from autoforge.af_audit import AuditLog

    assert hasattr(AuditLog, "append") is False
    assert hasattr(AuditLog, "add") is True
    src = (SRC / "af_runtime_plugins.py").read_text(encoding="utf-8")
    assert "runtime.audit.append(" in src, \
        "af_runtime_plugins 的留痕形状已经改过：这条『这块面未接线』的登记要一起撤、并递复核"


def test_source_defaults_are_written_off_where_they_live():
    """源码面第二腿：三处默认值字面钉住（装配面读的是它们，改一处红一处）。"""
    runtime_src = (SRC / "af_runtime.py").read_text(encoding="utf-8")
    assert '_os.environ.get("AUTOFORGE_CONF_GRADING", "")' in runtime_src, \
        "af_runtime 读 conf 分级开关的默认值不再是空串"

    plugins_src = (SRC / "af_runtime_plugins.py").read_text(encoding="utf-8")
    tree = ast.parse(plugins_src)
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "_should_enable")
    last = fn.body[-1]
    assert isinstance(last, ast.Return) and isinstance(last.value, ast.Constant) \
        and last.value.value is False, "_should_enable 的兜底返回值不再是 False ⇒ 默认档被翻过"

    conflict_src = (SRC / "af_conflict_runtime.py").read_text(encoding="utf-8")
    cls = next(n for n in ast.parse(conflict_src).body
               if isinstance(n, ast.ClassDef) and n.name == "ConflictSettings")
    defaults = {}
    for stmt in cls.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) \
                and stmt.value is not None and isinstance(stmt.value, ast.Constant):
            defaults[stmt.target.id] = stmt.value.value
    assert defaults.get("mode") == "off", f"冲突仲裁默认档漂了：{defaults.get('mode')!r}"
    assert defaults.get("wait_enabled") is False, \
        f"冲突等待默认档漂了：{defaults.get('wait_enabled')!r}"


# ─────────────────────────────────────────────────────────────────────
# Q5 乙：group 在 AF-Spec 入口显式拒绝并给具名诊断
# ────────────────────────────────────────────────────────

def test_group_line_gets_a_named_diagnostic_instead_of_a_typo_hint():
    with pytest.raises(af_spec.SpecError) as exc:
        af_spec.compile_spec('automation a1\nname "a"\ngroup g1 {"mode":"sequence"}\n')
    err = exc.value
    assert err.code == "E_GROUP_UNSUPPORTED", f"group 又落回「未知关键字」：{err.code}"
    assert "AF-Spec 文本层不支持 group" in err.message
    assert err.fix["use"] == "ir_json"
    assert "group" not in err.fix["text_layer_node_kinds"]


def test_group_rejection_is_not_silent_success():
    """⛔ 不静默丢弃：这一行绝不许产出一张少了 group 节点的 Graph。"""
    with pytest.raises(af_spec.SpecError):
        af_spec.compile_spec('automation a1\non n1 {"type":"state","entity_id":"light.x"}\ngroup g1 {}\n')


def test_other_unknown_keywords_still_report_unknown_primitive():
    """新分支不许把通用那一档吃掉（回归腿）。"""
    with pytest.raises(af_spec.SpecError) as exc:
        af_spec.compile_spec('automation a\nname "a"\nbogus 1\n')
    assert exc.value.code == "E_UNKNOWN_PRIMITIVE"


def test_node_kind_drift_is_declared_at_the_entry():
    """7 vs 8 的漂移是**已知并已被点名**的，不是无人知晓的：两表各自成真源，入口报具名诊断。

    将来真在 AF-Spec 层开 group 往返时（裁定说那属功能面、另立项），这一腿会红——
    红了就是把"该同步删掉这段拒绝、并把它接进 `_NODE_KINDS`"的提醒送到人面前。
    """
    from autoforge.af_ir.models import NODE_KINDS

    assert "group" in NODE_KINDS
    assert "group" not in af_spec._NODE_KINDS, \
        "AF-Spec 文本层已经支持 group 了：本条拒绝判据与 compile_spec 的分支要一起撤"
    assert af_spec.E_GROUP_UNSUPPORTED == "E_GROUP_UNSUPPORTED"


# ─────────────────────────────────────────────────────────────────────
# Q6 F-17 乙：删兜底模板 ⇒ NEEDS_REVIEW
# ─────────────────────────────────────────────────────────────────────

IR_WITH_DO = {
    "ir_version": "0.3.0",
    "automations": [{
        "id": "auto_x", "name": "x",
        "nodes": [{"id": "n1", "kind": "do",
                   "params": {"entity_id": "light.study", "service": "turn_on"}}],
    }],
}


def _mgr(**kw):
    clock = FakeClock()
    conf = make_conf({})
    rec = make_recorder(conf, clock)
    deployed = []

    def deployer(plan):
        deployed.append(plan)
        return f"auto_{len(deployed)}"

    return clock, conf, rec, ProposalManager(
        conf=conf, recorder=rec, clock=clock, deployer=deployer, **kw), deployed


def _proposal(**over) -> Proposal:
    fields = {
        "proposal_id": "p1", "kind": "ma", "conf": 0.9,
        "natural_language": "书房>27 开空调", "created_at": 1.0,
    }
    fields.update(over)
    return Proposal(**fields)


def test_build_ir_refuses_to_invent_an_ir():
    assert build_ir(_proposal(suggested_ir=IR_WITH_DO)) == IR_WITH_DO
    with pytest.raises(ProposalNeedsReview):
        build_ir(_proposal(suggested_ir=None))


def test_the_fallback_skeleton_is_physically_gone():
    """兜底骨架三处不合 schema（键名 `version` 非 `ir_version`／`trigger` 挂 automation／`nodes` 是 dict）。
    它一旦"修对"就等于开通自动生成，所以整段删掉——这条腿钉"没回来"。

    判据取 AST 那一面而不是文本：本文件的 docstring 要解释这份兜底长什么样，
    拿散文当哨兵会自踩（仓内判例：散文踩名字哨兵）。
    """
    tree = ast.parse((SRC / "af_proposal.py").read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "build_ir")
    returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return)]
    assert len(returns) == 1, f"build_ir 出现了第二条出口（{len(returns)} 条）⇒ 兜底骨架回来了"
    raises = [n for n in ast.walk(fn) if isinstance(n, ast.Raise)]
    assert raises, "build_ir 缺 `raise` ⇒ 缺 IR 那一档没有出口"
    dicts = [n for n in ast.walk(fn) if isinstance(n, ast.Dict)]
    assert dicts == [], f"build_ir 里又出现手抄 IR 字面量：{ast.dump(dicts[0])[:120]}"
    assert '"trigger": {"kind": "manual"}' not in ast.unparse(tree)


def test_auto_deploy_of_ir_less_proposal_lands_in_needs_review():
    clock, conf, rec, mgr, deployed = _mgr()
    p = mgr.submit(hypothesis_id="h1", natural_language="书房>27 开空调", conf=0.92)
    assert p.status is ProposalStatus.NEEDS_REVIEW
    assert deployed == [], "缺 IR 却被叫起 deployer ⇒ 兜底模板还在替它编 IR"
    assert p.automation_id is None
    assert p.reason.startswith("ProposalNeedsReview:")


def test_needs_review_is_written_into_the_audit_trail():
    events = []

    class _Audit:
        def append(self, item):
            events.append(item)

    clock = FakeClock()
    conf = make_conf({})
    rec = make_recorder(conf, clock)
    mgr = ProposalManager(conf=conf, recorder=rec, clock=clock,
                          deployer=lambda plan: "x", audit=_Audit())
    mgr.submit(hypothesis_id="h2", natural_language="x", conf=0.92)
    kinds = [e.get("kind") for e in events]
    assert "proposal_needs_review" in kinds, f"待复核没有留痕：{kinds}"


def test_ir_less_proposal_does_not_reach_the_deployer():
    clock, conf, rec, mgr, deployed = _mgr()
    mgr.submit(hypothesis_id="h3", natural_language="x", conf=0.75)   # shadow 档也一样
    assert deployed == []


def test_approve_of_an_ir_less_proposal_does_not_pretend_approved():
    """过去 approve 先把状态写成 APPROVED，`plan_for` 才抛 ⇒ 队列里躺着一份"已批准却没部署"。"""
    clock, conf, rec, mgr, deployed = _mgr()
    p = mgr.submit(hypothesis_id="h4", natural_language="x", conf=0.45)
    assert p.status is ProposalStatus.PENDING
    with pytest.raises(ProposalError) as exc:
        mgr.approve(p.proposal_id, by="alice")
    assert "缺 suggested_ir" in str(exc.value)
    assert p.status is ProposalStatus.PENDING, "批准失败之后状态仍被写成了 APPROVED（假绿）"
    assert deployed == []


def test_needs_review_proposal_can_be_rejected_by_a_human():
    clock, conf, rec, mgr, deployed = _mgr()
    p = mgr.submit(hypothesis_id="h5", natural_language="x", conf=0.92)
    assert p.status is ProposalStatus.NEEDS_REVIEW
    out = mgr.reject(p.proposal_id, by="alice", reason="先不采纳")
    assert out.status is ProposalStatus.REJECTED
    assert out.decided_by == "alice"


def test_a_proposal_with_ir_still_deploys_end_to_end():
    """回归：删兜底不许伤到"带着合规 IR 来"的正常路径。"""
    clock, conf, rec, mgr, deployed = _mgr()
    p = mgr.submit(hypothesis_id="h6", natural_language="x", conf=0.92, suggested_ir=IR_WITH_DO)
    assert p.status is ProposalStatus.AUTO_DEPLOYED_CANARY
    assert p.automation_id == "auto_1"
    assert deployed[0].ir == IR_WITH_DO


def test_needs_review_round_trips_through_dump_and_load():
    """持久化面：`needs_review` 是本仓新增的一枚状态字面，坏档不许把它读成别的档。"""
    clock, conf, rec, mgr, deployed = _mgr()
    p = mgr.submit(hypothesis_id="h7", natural_language="x", conf=0.92)
    payload = mgr.dump()
    _, _, _, restored, _ = _mgr()
    assert restored.load(payload) == 1
    assert restored.proposals[p.proposal_id].status is ProposalStatus.NEEDS_REVIEW
