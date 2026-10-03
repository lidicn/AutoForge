"""MA 提案接收 + 统一审批队列（难点 4）。

一个队列同时承载两类待办，与现有 af_save 的 PendingOp 语义对齐：

* ``kind="ma"``   —— MA 送来的假设提案。按 conf 分流：
    conf < 0.60          → status=pending，等人在 WebUI 批准；
    0.60 ≤ conf < 0.85   → 自动编译 + 部署为 shadow；
    conf ≥ 0.85          → 自动编译 + 部署为 auto + canary。
* ``kind="ask"``  —— 运行时 ask 档挂起的执行请求（conf < 0.60 的 do 节点），
    批准后由 ``resumer`` 恢复执行。

静态闸与运行时闸的分工（见 StaticGuardPolicy）：部署 shadow / ask 自动化时
IR 的 ``confidence`` 置 None（af_scanner 对 None 早退），真实 conf 写入
ConfidenceStore 由 ShadowRunner 做运行时拦截，并留 ``static_guard_deferred``
审计事件。需要保留编译期硬闸的团队把 mode 设为 "strict"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Sequence
from uuid import uuid4

from autoforge.af_conf import AUTO_MIN, SHADOW_LOW, Band, ConfidenceStore
from autoforge.af_feedback import (
    FeedbackKind, FeedbackRecorder,
    audit_write, clock_now, conf_read,
)

__all__ = [
    "ProposalKind", "ProposalStatus", "Proposal", "DeployPlan",
    "StaticGuardPolicy", "ProposalError", "ProposalManager", "build_ir",
]


class ProposalKind(str, Enum):
    """提案来源类型。"""

    MA = "ma"     # MA 假设提案
    ASK = "ask"   # 运行时 ask 挂起请求


class ProposalStatus(str, Enum):
    """审批队列状态。"""

    PENDING = "pending"
    AUTO_DEPLOYED_SHADOW = "auto_deployed_shadow"
    AUTO_DEPLOYED_CANARY = "auto_deployed_canary"
    APPROVED = "approved"
    REJECTED = "rejected"
    FAILED = "failed"


class ProposalError(RuntimeError):
    """提案编译/部署失败。"""


@dataclass
class Proposal:
    """提案（MA 假设 or 运行时 ask）。"""

    proposal_id: str
    kind: ProposalKind
    conf: float
    natural_language: str
    created_at: float
    source: str = "ma"
    hypothesis_id: str | None = None
    suggested_ir: dict[str, Any] | None = None
    status: ProposalStatus = ProposalStatus.PENDING
    decided_at: float | None = None
    decision: str | None = None          # approved | rejected
    decided_by: str | None = None
    reason: str | None = None
    automation_id: str | None = None
    # kind="ask" 专用
    instance_id: str | None = None
    node_id: str | None = None
    action: str | None = None
    params: dict[str, Any] | None = None
    expected_state: dict[str, Any] | None = None
    prompt: str | None = None
    # 对端元数据（MqttBridge 记账：kind/persons/room/conf_reported），不参与档位判定
    transport: dict[str, Any] = field(default_factory=dict)

    @property
    def feedback_target(self) -> str:
        """负样本/正样本回灌目标：MA 提案归到 hypothesis: 前缀，供 MA 反查。"""
        if self.kind is ProposalKind.MA and self.hypothesis_id:
            return f"hypothesis:{self.hypothesis_id}"
        return self.automation_id or self.proposal_id

    def to_json(self) -> dict[str, Any]:
        """序列化。"""
        return {
            "proposal_id": self.proposal_id, "kind": self.kind.value,
            "source": self.source, "hypothesis_id": self.hypothesis_id,
            "natural_language": self.natural_language, "conf": self.conf,
            "suggested_ir": self.suggested_ir, "status": self.status.value,
            "created_at": self.created_at, "decided_at": self.decided_at,
            "decision": self.decision, "decided_by": self.decided_by,
            "reason": self.reason, "automation_id": self.automation_id,
            "instance_id": self.instance_id, "node_id": self.node_id,
            "action": self.action, "params": self.params,
            "expected_state": self.expected_state, "prompt": self.prompt,
            "transport": self.transport,
        }


@dataclass
class DeployPlan:
    """部署计划：conf → 档位 → 部署策略。"""

    proposal: Proposal
    band: Band
    seed_conf: float
    canary: bool
    ir: dict[str, Any]
    static_confidence: float | None
    status: ProposalStatus


@dataclass
class StaticGuardPolicy:
    """编译期静态闸与运行时闸的分工策略。"""

    mode: str = "defer_to_runtime"       # defer_to_runtime | strict

    def resolve(self, plan: "DeployPlan") -> float | None:
        """返回写进 IR 的 confidence 值（None = 交给运行时 band 拦截）。"""
        if plan.band == "auto":
            return plan.seed_conf
        if self.mode == "strict":
            if _ir_writes_devices(plan.ir):
                raise ProposalError(
                    f"strict 静态闸：confidence={plan.seed_conf} 处于 {plan.band} 档，"
                    f"IR 含写设备的 do 节点，拒绝部署"
                )
            return plan.seed_conf
        return None


def _ir_writes_devices(ir: Mapping[str, Any]) -> bool:
    """判断 IR 文档里是否存在写设备的 do 节点（duck typing dict 结构）。"""
    for auto in ir.get("automations", []) or []:
        nodes = auto.get("nodes", {})
        node_iter = nodes.values() if isinstance(nodes, Mapping) else (nodes or [])
        for node in node_iter:
            if node.get("kind") != "do":
                continue
            entities = node.get("entities") or node.get("target_entities") or []
            if entities:
                return True
    return False


def build_ir(proposal: Proposal) -> dict[str, Any]:
    """生成 IR 文档：优先用 suggested_ir，否则生成「只 ask 不写设备」的安全骨架。"""
    if proposal.suggested_ir:
        return dict(proposal.suggested_ir)
    aid = f"auto_{proposal.proposal_id.replace('-', '_')}"
    return {
        "version": 1,
        "automations": [{
            "id": aid,
            "name": proposal.natural_language[:40] or aid,
            "confidence": None,
            "trigger": {"kind": "manual"},
            "nodes": {
                "ask": {
                    "id": "ask", "kind": "ask",
                    "prompt": proposal.natural_language,
                },
            },
        }],
    }


def _plan_status(band: Band) -> ProposalStatus:
    return (ProposalStatus.AUTO_DEPLOYED_SHADOW if band == "shadow"
            else ProposalStatus.AUTO_DEPLOYED_CANARY)


@dataclass
class ProposalManager:
    """提案接收 + 三级分流 + 审批队列。"""

    conf: ConfidenceStore
    recorder: FeedbackRecorder
    clock: Any
    audit: Any = None
    static_guard: StaticGuardPolicy = field(default_factory=StaticGuardPolicy)
    ir_builder: Callable[[Proposal], dict[str, Any]] = build_ir
    deployer: Callable[[DeployPlan], str] | None = None
    resumer: Callable[[Proposal], Any] | None = None
    ask_max: float = SHADOW_LOW          # conf < 0.60 → ask
    shadow_max: float = AUTO_MIN         # conf < 0.85 → shadow
    proposals: dict[str, Proposal] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)

    # ---- 分流 ------------------------------------------------------------ #

    def band_for(self, conf: float) -> Band:
        """conf → 档位（与 af_conf.decision_for 同口径）。"""
        if conf >= self.shadow_max:
            return "auto"
        if conf >= self.ask_max:
            return "shadow"
        return "ask"

    def plan_for(self, proposal: Proposal) -> DeployPlan:
        """按 conf 生成部署计划。"""
        band = self.band_for(proposal.conf)
        ir = self.ir_builder(proposal)
        plan = DeployPlan(
            proposal=proposal, band=band, seed_conf=float(proposal.conf),
            canary=(band == "auto"), ir=ir,
            static_confidence=None,
            status=_plan_status(band),
        )
        plan.static_confidence = self.static_guard.resolve(plan)
        return plan

    # ---- 接收 ------------------------------------------------------------ #

    def submit(
        self, *, hypothesis_id: str, natural_language: str, conf: float,
        suggested_ir: Mapping[str, Any] | None = None, source: str = "ma",
        proposal_id: str | None = None,
        transport: Mapping[str, Any] | None = None,
    ) -> Proposal:
        """接收 MA 提案并按 conf 分流（难点 4 的主入口）。

        `transport` 是桥记下的对端元数据（签名与 `PersistentInsightSink.submit` 对齐，
        桥侧不需要按落点分支）；只跟提案走，不参与 `band_for` 判定。
        """
        now = clock_now(self.clock)
        proposal = Proposal(
            proposal_id=proposal_id or uuid4().hex[:12],
            kind=ProposalKind.MA, source=source,
            hypothesis_id=str(hypothesis_id),
            natural_language=str(natural_language),
            conf=float(conf),
            suggested_ir=dict(suggested_ir) if suggested_ir else None,
            created_at=now,
            transport=dict(transport or {}),
        )
        self._register(proposal)
        audit_write(
            self.audit, at=now, kind="proposal_received", source=source,
            proposal_id=proposal.proposal_id, hypothesis_id=proposal.hypothesis_id,
            conf=proposal.conf,
        )
        if self.band_for(proposal.conf) == "ask":
            # conf < 0.60：只出提案，以 ask 交付，必须人工确认（IR §10）
            audit_write(
                self.audit, at=now, kind="proposal_waiting_approval",
                proposal_id=proposal.proposal_id,
            )
            return proposal
        self._auto_deploy(proposal)
        return proposal

    def open_ask(
        self, *, automation_id: str, instance_id: str, node: Any,
        expected_state: Mapping[str, Any], prompt: str,
    ) -> Proposal:
        """运行时 ask 档挂起（由 ShadowRunner.ask_handler 调用）。"""
        now = clock_now(self.clock)
        proposal = Proposal(
            proposal_id=uuid4().hex[:12], kind=ProposalKind.ASK,
            source="runtime", conf=conf_read(self.conf, automation_id),
            natural_language=prompt, prompt=prompt, created_at=now,
            automation_id=str(automation_id), instance_id=str(instance_id),
            node_id=str(getattr(node, "id", "")),
            action=str(getattr(node, "action", "")),
            params=dict(getattr(node, "params", None) or {}),
            expected_state=dict(expected_state or {}),
        )
        self._register(proposal)
        audit_write(
            self.audit, at=now, kind="ask_proposal_opened",
            proposal_id=proposal.proposal_id, automation_id=automation_id,
            instance_id=instance_id, node_id=proposal.node_id, action=proposal.action,
        )
        return proposal

    def _register(self, proposal: Proposal) -> None:
        self.proposals[proposal.proposal_id] = proposal
        self.order.append(proposal.proposal_id)

    def _auto_deploy(self, proposal: Proposal) -> None:
        now = clock_now(self.clock)
        try:
            plan = self.plan_for(proposal)
            if self.deployer is None:
                raise ProposalError("未配置 deployer，无法自动部署")
            automation_id = str(self.deployer(plan))
        except Exception as exc:                      # noqa: BLE001 —— 部署失败要落状态
            proposal.status = ProposalStatus.FAILED
            proposal.reason = f"{type(exc).__name__}: {exc}"
            proposal.decided_at = now
            audit_write(
                self.audit, at=now, kind="proposal_deploy_failed",
                proposal_id=proposal.proposal_id, reason=proposal.reason,
            )
            return
        proposal.automation_id = automation_id
        proposal.status = plan.status
        proposal.decided_at = now
        proposal.decision = "auto_deployed"
        self.recorder.seed(
            automation_id, plan.seed_conf,
            proposal_id=proposal.proposal_id, band=plan.band,
            static_confidence=plan.static_confidence,
        )
        if plan.static_confidence is None:
            audit_write(
                self.audit, at=now, kind="static_guard_deferred",
                proposal_id=proposal.proposal_id, automation_id=automation_id,
                band=plan.band, mode=self.static_guard.mode,
                note="IR.confidence=None，由 ShadowRunner 运行时 band 拦截 do",
            )
        audit_write(
            self.audit, at=now, kind="proposal_deployed", proposal_id=proposal.proposal_id,
            automation_id=automation_id, band=plan.band, canary=plan.canary,
            seed_conf=plan.seed_conf, status=proposal.status.value,
        )

    # ---- 审批 ------------------------------------------------------------ #

    def approve(self, proposal_id: str, *, by: str = "human", automation_id: str | None = None) -> Proposal:
        """批准提案：MA 提案走部署，ask 提案走恢复执行。"""
        proposal = self._require(proposal_id)
        if proposal.status not in (ProposalStatus.PENDING, ProposalStatus.FAILED):
            raise ProposalError(f"提案 {proposal_id} 已终态（{proposal.status.value}），不能批准")
        now = clock_now(self.clock)
        proposal.status = ProposalStatus.APPROVED
        proposal.decision = "approved"
        proposal.decided_by = by
        proposal.decided_at = now

        if proposal.kind is ProposalKind.ASK:
            if automation_id:
                proposal.automation_id = automation_id
            if self.resumer is not None:
                self.resumer(proposal)
        else:
            plan = self.plan_for(proposal)
            if self.deployer is None:
                raise ProposalError("未配置 deployer，无法部署已批准提案")
            proposal.automation_id = str(self.deployer(plan))
            self.recorder.seed(
                proposal.automation_id, plan.seed_conf,
                proposal_id=proposal.proposal_id, band=plan.band,
            )
            if plan.canary:
                audit_write(self.audit, at=now, kind="canary_requested",
                            automation_id=proposal.automation_id)

        self.recorder.emit(
            proposal.feedback_target, FeedbackKind.POSITIVE, source="proposal", at=now,
            details={"basis": "human_approved", "proposal_id": proposal.proposal_id,
                     "decided_by": by},
        )
        audit_write(
            self.audit, at=now, kind="proposal_decided", proposal_id=proposal.proposal_id,
            decision="approved", by=by, automation_id=proposal.automation_id,
        )
        return proposal

    def reject(self, proposal_id: str, *, by: str = "human", reason: str | None = None) -> Proposal:
        """拒绝提案：人工拒绝/拒绝询问 → 负样本压低 conf（IR §10）。"""
        proposal = self._require(proposal_id)
        if proposal.status not in (ProposalStatus.PENDING, ProposalStatus.FAILED):
            raise ProposalError(f"提案 {proposal_id} 已终态（{proposal.status.value}），不能拒绝")
        now = clock_now(self.clock)
        proposal.status = ProposalStatus.REJECTED
        proposal.decision = "rejected"
        proposal.decided_by = by
        proposal.decided_at = now
        proposal.reason = reason
        self.recorder.emit(
            proposal.feedback_target, FeedbackKind.USER_REJECT, source="proposal", at=now,
            details={
                "basis": "user_reject", "proposal_id": proposal.proposal_id,
                "decided_by": by, "reason": reason,
                "natural_language": proposal.natural_language,
                "kind": proposal.kind.value,
            },
        )
        audit_write(
            self.audit, at=now, kind="proposal_decided", proposal_id=proposal.proposal_id,
            decision="rejected", by=by, reason=reason,
        )
        return proposal

    # ---- 查询 / 持久化 --------------------------------------------------- #

    def list(self, status: "ProposalStatus | str | None" = None) -> list[Proposal]:
        """按状态列出提案（默认全部，按创建顺序）。"""
        want = None
        if status is not None:
            want = status.value if isinstance(status, ProposalStatus) else str(status)
        out = []
        for pid in self.order:
            proposal = self.proposals[pid]
            if want is None or proposal.status.value == want:
                out.append(proposal)
        return out

    def get(self, proposal_id: str) -> Proposal | None:
        """取单个提案。"""
        return self.proposals.get(proposal_id)

    def dump(self) -> dict[str, Any]:
        """导出 JSON。"""
        return {"order": list(self.order),
                "proposals": {k: v.to_json() for k, v in self.proposals.items()}}

    def load(self, data: Mapping[str, Any]) -> int:
        """从 JSON 恢复。"""
        rows = data.get("proposals", {}) or {}
        for pid, row in rows.items():
            self.proposals[str(pid)] = Proposal(
                proposal_id=str(pid), kind=ProposalKind(row["kind"]),
                conf=float(row["conf"]), natural_language=str(row["natural_language"]),
                created_at=float(row["created_at"]), source=str(row.get("source", "ma")),
                hypothesis_id=row.get("hypothesis_id"), suggested_ir=row.get("suggested_ir"),
                status=ProposalStatus(row.get("status", "pending")),
                decided_at=row.get("decided_at"), decision=row.get("decision"),
                decided_by=row.get("decided_by"), reason=row.get("reason"),
                automation_id=row.get("automation_id"), instance_id=row.get("instance_id"),
                node_id=row.get("node_id"), action=row.get("action"),
                params=row.get("params"), expected_state=row.get("expected_state"),
                prompt=row.get("prompt"),
            )
        self.order = [str(p) for p in (data.get("order") or rows.keys())]
        return len(rows)

    def _require(self, proposal_id: str) -> Proposal:
        proposal = self.proposals.get(proposal_id)
        if proposal is None:
            raise ProposalError(f"提案不存在：{proposal_id}")
        return proposal
