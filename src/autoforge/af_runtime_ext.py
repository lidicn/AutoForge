"""conf 分级引擎的运行时组装层。

按 4.1 的接口契约，在 Runtime.__post_init__ 之后调用 install(runtime) 即可
把五个新模块挂进 executor / scheduler / SSE 订阅，不改动任何现有模块文件。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

from autoforge.af_atomic import atomic_write_text
from autoforge.af_canary_supervisor import (
    DEFAULT_CANARY_SPEC, CanaryPolicy, CanarySupervisor,
    graph_canary_applier, graph_canary_stripper,
)
from autoforge.af_conf import ConfidenceStore
from autoforge.af_feedback import (
    FeedbackExporter, FeedbackFilter, FeedbackRecorder, FeedbackSource, LaterFn,
    clock_now,
)
from autoforge.af_intervention import InterventionDetector, InterventionPolicy
from autoforge.af_proposal import Proposal, ProposalManager, ProposalStatus, StaticGuardPolicy
from autoforge.af_shadow import ShadowBinding, ShadowPolicy, ShadowRunner
from autoforge.af_pretrigger import PreTriggerService
from autoforge.af_experience import ExperienceStore

__all__ = ["ConfGrading", "install", "make_later", "api_handlers"]


def make_later(scheduler: Any) -> LaterFn | None:
    """把现有 Scheduler 适配成 ``later(seconds, callback)``。"""
    if scheduler is None:
        return None
    for name in ("call_later", "schedule", "add_timer", "later"):
        fn = getattr(scheduler, name, None)
        if not callable(fn):
            continue

        def _later(seconds: float, callback: Callable[[], Any], _fn: Callable = fn) -> Any:
            try:
                return _fn(seconds, callback)
            except TypeError:
                return _fn(delay=seconds, callback=callback)

        return _later
    return None


@dataclass
class ConfGrading:
    """五个新模块的组装句柄。"""

    recorder: FeedbackRecorder
    exporter: FeedbackExporter
    shadow: ShadowRunner
    intervention: InterventionDetector
    canary: CanarySupervisor
    proposals: ProposalManager
    binding: ShadowBinding | None = None
    pretrigger: Any = None
    persist_dir: str | None = None
    restore_corrupt: list[str] = field(default_factory=list)

    # ---- 生命周期 ------------------------------------------------------- #

    def tick(self, hours: float = 1.0, events: int = 0) -> None:
        """定期调用：conf 指数衰减（IR §10「conf 随时间指数衰减 + 新样本回灌」）。"""
        self.shadow.conf.decay_all(hours, events=events)

    def observe(self, entity_id: str, old_state: Any, new_state: Any, at: float | None = None) -> Any:
        """SSE 状态变化事件入口。"""
        return self.intervention.on_state_changed(entity_id, old_state, new_state, at=at)

    def persist(self) -> None:
        """把新模块状态落到 .forge/（JSON，与现有持久化对齐）。"""
        if not self.persist_dir:
            return
        os.makedirs(self.persist_dir, exist_ok=True)
        for name, payload in (
            ("shadow_log.json", self.shadow.log.dump()),
            ("feedback.json", {"events": self.recorder.dump()}),
            ("intervention.json", self.intervention.dump()),
            ("canary_state.json", self.canary.dump()),
            ("proposals.json", self.proposals.dump()),
        ):
            # 走公共原子助手：随机 tmp + fsync + 目录 fsync。裸 `open(..., "w")` 先把目标截断，
            # 崩在 `json.dump` 中间就留半截 JSON —— 而读侧今天按"解析失败当没有这份文件"处理，
            # 半截的影子日志不是"读回上一版"而是"读回空"（新增审计 BUG-03，§二之五十六）。
            atomic_write_text(
                os.path.join(self.persist_dir, name),
                json.dumps(payload, ensure_ascii=False, indent=2),
            )

    def restore(self) -> None:
        """从 .forge/ 恢复。"""
        if not self.persist_dir:
            return

        def _read(name: str, default: Any) -> Any:
            path = os.path.join(self.persist_dir, name)
            if not os.path.exists(path):
                return default
            # 降级 + 留痕：坏文件按"这份没有"继续起，但绝不静默——读侧一旦把半截 JSON 吞成
            # "没有记录"，影子历史/提案队列就直接归零，而服务照旧起得来（新增审计 §四 P0 后半句）。
            # 今天 persist() 已改走原子助手，正常路径不会再产出半截文件；这一档兜的是历史遗留与外部改动。
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    return json.load(fh)
            except (ValueError, OSError, UnicodeDecodeError) as exc:
                logger.warning("%s 读取失败，按『没有这份文件』继续起：%r（原因记进 restore_corrupt）", path, exc)
                self.restore_corrupt.append(name)
                return default

        self.shadow.log.load(_read("shadow_log.json", []))
        self.recorder.load(_read("feedback.json", {}).get("events", []))
        self.canary.load(_read("canary_state.json", {}))
        self.proposals.load(_read("proposals.json", {}))


def install(
    runtime: Any,
    *,
    deployer: Callable[[Any], str] | None = None,
    resumer: Callable[[Proposal], Any] | None = None,
    subscribe: Callable[[Callable[..., Any]], Any] | None = None,
    shadow_policy: ShadowPolicy | None = None,
    intervention_policy: InterventionPolicy | None = None,
    canary_policy: CanaryPolicy | None = None,
    static_guard: StaticGuardPolicy | None = None,
    canary_spec: dict[str, Any] | None = None,
) -> ConfGrading:
    """在 Runtime 上组装 conf 分级引擎。"""
    clock, audit, conf = runtime.clock, runtime.audit, runtime.conf
    states, graph = runtime.states, runtime.graph
    persist_dir = getattr(runtime, "persist_dir", None)
    later = make_later(getattr(runtime, "scheduler", None))

    recorder = FeedbackRecorder(conf=conf, clock=clock, audit=audit)
    exporter = FeedbackExporter(recorder=recorder)

    intervention = InterventionDetector(
        conf=conf, recorder=recorder, clock=clock, audit=audit,
        policy=intervention_policy or InterventionPolicy(), later=later,
    )
    intervention.mark_managed_from_graph(graph)

    canary = CanarySupervisor(
        conf=conf, recorder=recorder, clock=clock, audit=audit,
        policy=canary_policy or CanaryPolicy(),
        strip_canary=graph_canary_stripper(graph),
    )
    apply_canary = graph_canary_applier(graph, canary_spec or DEFAULT_CANARY_SPEC)

    proposals = ProposalManager(
        conf=conf, recorder=recorder, clock=clock, audit=audit,
        static_guard=static_guard or StaticGuardPolicy(),
        deployer=deployer, resumer=resumer,
    )

    shadow = ShadowRunner(
        conf=conf, states=states, recorder=recorder, clock=clock, audit=audit,
        policy=shadow_policy or ShadowPolicy(), later=later,
        ask_handler=proposals.open_ask, intervention=intervention,
    )

    def _on_promote(automation_id: str) -> None:
        # shadow 转正 = 进入 auto 档 → 重新挂 canary 保护并开始观察期
        apply_canary(automation_id)
        canary.begin(automation_id)

    shadow.on_promote = _on_promote
    canary.on_demote = lambda aid: None

    binding = shadow.install(runtime.executor)
    grading = ConfGrading(
        recorder=recorder, exporter=exporter, shadow=shadow,
        intervention=intervention, canary=canary, proposals=proposals,
        binding=binding, persist_dir=persist_dir,
    )
    grading.restore()

    # F12 预测性触发消费闭环：组装 + 记录真实触发（包裹 on_spawn）+ 周期扫描
    # F11② 经验先验：用 persist_dir 创建 ExperienceStore 实例，喂给 PreTriggerService
    experience_store = ExperienceStore(persist_dir) if persist_dir else None
    pretrigger = PreTriggerService(
        runtime,
        conf=conf,
        experience=experience_store,
        threshold=0.8,
        interval_seconds=90.0,
        persist_dir=persist_dir,
    )
    grading.pretrigger = pretrigger

    _prev_spawn = runtime.instances.on_spawn
    def _on_spawn(inst):
        if _prev_spawn is not None:
            _prev_spawn(inst)
        try:
            pretrigger.record_fire(inst)
        except Exception as exc:  # noqa: BLE001
            logger.warning("pretrigger record_fire 失败：%s", exc)
    runtime.instances.on_spawn = _on_spawn

    # SSE 订阅：HA 事件流 → 人工干预检测
    if subscribe is not None:
        subscribe(grading.observe)

    # 定时器：延迟比对 / hold 结算 / 过期清理 / canary 巡检 / conf 衰减
    if later is not None:
        def _tick() -> None:
            now = clock_now(clock)
            shadow.compare_due(at=now)
            intervention.check_holds(at=now)
            intervention.flush_expired(at=now)
            canary.check(at=now)
            grading.tick(hours=1.0)
            grading.persist()
            later(3600.0, _tick)

        later(3600.0, _tick)
        pretrigger.start(later)  # F12 周期扫描（每 interval_seconds 一次）

    return grading


# --------------------------------------------------------------------------- #
# 4.2 / 4.3 接口：HTTP 端点与 MCP 工具（不绑定 Web 框架）
# --------------------------------------------------------------------------- #

def api_handlers(grading: ConfGrading) -> dict[tuple[str, str], Callable[..., Any]]:
    """返回 ``{(METHOD, path): handler}``，由 WebUI/API 层自行挂载。"""

    def _post_proposals(body: dict[str, Any]) -> dict[str, Any]:
        proposal = grading.proposals.submit(
            hypothesis_id=body["hypothesis_id"],
            natural_language=body["natural_language"],
            conf=float(body["conf"]),
            suggested_ir=body.get("suggested_ir"),
        )
        return {"proposal_id": proposal.proposal_id,
                "status": proposal.status.value,
                "automation_id": proposal.automation_id}

    def _list_proposals(status: str | None = None) -> list[dict[str, Any]]:
        return [p.to_json() for p in grading.proposals.list(status)]

    def _approve(proposal_id: str, by: str = "webui") -> dict[str, Any]:
        return grading.proposals.approve(proposal_id, by=by).to_json()

    def _reject(proposal_id: str, by: str = "webui", reason: str | None = None) -> dict[str, Any]:
        return grading.proposals.reject(proposal_id, by=by, reason=reason).to_json()

    def _feedback(**params: Any) -> dict[str, Any]:
        filt = FeedbackFilter(
            from_ts=float(params["from"]) if params.get("from") else None,
            to_ts=float(params["to"]) if params.get("to") else None,
            automation_id=params.get("automation_id"),
            kinds=set(params["kinds"].split(",")) if params.get("kinds") else None,
        )
        return grading.exporter.query(filt)

    def _conf() -> dict[str, Any]:
        conf: ConfidenceStore = grading.shadow.conf
        return {
            aid: {"conf": value, "band": conf.band(aid)}
            for aid, value in conf.values.items()
        }

    def _promote(automation_id: str) -> dict[str, Any]:
        grading.shadow.conf.promote(automation_id)
        return {"automation_id": automation_id,
                "conf": grading.shadow.conf.values.get(automation_id)}

    def _pretrigger_status() -> dict[str, Any]:
        svc = grading.pretrigger
        if svc is None:
            return {"enabled": False}
        return {"enabled": True, "stats": svc.stats(),
                "threshold": svc.threshold, "interval_seconds": svc.interval_seconds}

    return {
        ("POST", "/api/proposals"): _post_proposals,
        ("GET", "/api/proposals"): _list_proposals,
        ("POST", "/api/proposals/{id}/approve"): _approve,
        ("POST", "/api/proposals/{id}/reject"): _reject,
        ("GET", "/api/feedback"): _feedback,
        ("GET", "/api/conf"): _conf,
        ("POST", "/api/conf/{id}/promote"): _promote,
        ("GET", "/api/pretrigger"): _pretrigger_status,
    }
