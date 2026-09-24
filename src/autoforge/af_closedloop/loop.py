# -*- coding: utf-8 -*-
"""loop — 自修正闭环编排。

顺序：lint + build 反馈 → 确定性 auto-fix →（限次）LLM 深度修复 → 仿真修复 → 覆盖率 → 历史。
产物 RepairReport 严格遵循编排器契约；LoopResult 额外带覆盖率与仿真结果。
"""
from __future__ import annotations

import copy
import hashlib
import json
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from . import coverage as coverage_mod
from . import detectors, fixers
from .deepfix import DeepFixer
from .history import FixHistoryStore
from .runtime import load_module

_ORDER = {
    "MISSING_TIMEOUT_OR_DEFAULT": 0, "MISSING_ON_ERROR": 0,
    "MISSING_CANARY": 0, "MISSING_NUMERIC_TYPE": 0,
    "WAIT_EDGE_MISUSE": 1, "DISCONNECTED_NODE": 1, "CYCLE": 1, "SIM_BRANCH_MISMATCH": 1,
    "UNKNOWN": 2, "SPEC_PARSE_ERROR": 2,
    "ENTITY_NOT_FOUND": 3, "STALE_ENTITY": 3, "EXPECT_FAILED": 3,
}


class StageRunner:
    """编译/仿真端口。可以是编排器适配器，也可以是任何 stub。"""
    def build(self, ir) -> Mapping[str, Any]: ...
    def simulate(self, ir, overrides=None) -> Mapping[str, Any]: ...


class OrchestratorRunner:
    """优先调用编排器既有阶段方法（按名探测），否则回落到 mcp.call 工具名（带 approve 护栏）。

    编排器 build/simulate 的工具名与返回结构在被截断的部分，故按可配置默认值 + 容错解析处理。
    """

    def __init__(self, orch, session=None, *, build_tool="af_build", simulate_tool="af_simulate"):
        self.orch, self.session = orch, session
        self.build_tool, self.simulate_tool = build_tool, simulate_tool
        self.mcp = getattr(orch, "mcp", None)

    def build(self, ir):
        fn = getattr(self.orch, "build_ir", None) or getattr(self.orch, "build", None)
        if callable(fn):
            return _as_mapping(fn(ir))
        return _as_mapping(self.orch.mcp.call(self.build_tool, ir=ir))

    def simulate(self, ir, overrides=None):
        fn = getattr(self.orch, "simulate_ir", None) or getattr(self.orch, "simulate", None)
        if callable(fn):
            try:
                return _as_mapping(fn(ir, overrides or {}))
            except TypeError:
                return _as_mapping(fn(ir))
        return _as_mapping(self.orch.mcp.call(self.simulate_tool, ir=ir, overrides=overrides or {}))


@dataclass
class LoopPolicy:
    max_rounds: int = 3
    max_llm_rounds: int = 2
    max_sim_rounds: int = 2
    stall_threshold: int = 2
    min_edge_coverage: float = 0.7
    enable_llm_fix: bool = True


@dataclass
class LoopResult:
    report: Any                       # RepairReport（契约）
    status: str                       # fixed | partial | needs_user | stalled
    coverage: Any = None
    simulation: dict | None = None
    log: list = field(default_factory=list)

    def to_message(self, session_id: str = "") -> dict:
        A = load_module()
        return {
            "protocol": A.PROTOCOL_VERSION,
            "session_id": session_id,
            "status": self.status,
            "fixed": bool(getattr(self.report, "fixed", False)),
            "issues": [i.to_message() for i in self.report.issues],
            "fix_log": [f.to_message() for f in self.log],
            "coverage": self.coverage.to_message() if self.coverage else None,
            "simulation": self.simulation,
        }


class _NoMCP:
    def call(self, tool, **kwargs):
        raise RuntimeError("当前闭环没有 MCP 端口")


class _PortShim:
    """给 FixContext 用的最小端口（FixContext 只需要 .mcp 与 ._catalog）。"""
    def __init__(self, runner=None, catalog=()):
        self.mcp = getattr(runner, "mcp", None) or _NoMCP()
        self._ents = list(catalog)

    def _catalog(self, session):
        return self._ents


class ClosedLoop:
    def __init__(self, *, runner=None, deep_fixer: DeepFixer | None = None,
                 history: FixHistoryStore | None = None, policy: LoopPolicy | None = None,
                 registry=None, orch=None, catalog=()):
        self.runner = runner
        self.deep_fixer = deep_fixer
        self.history = history
        self.policy = policy or LoopPolicy()
        self.registry = registry
        self.port = orch or _PortShim(runner, catalog)

    # -- 主流程 --------------------------------------------------------
    def run(self, session, ir, *, issues: Sequence = (), cases: Sequence = (),
            sim_overrides=None) -> LoopResult:
        A = load_module()
        policy = self.policy
        cur = copy.deepcopy(dict(ir))
        run_id = uuid.uuid4().hex[:8]
        log, fp_hits, llm_used = [], Counter(), 0
        status = "fixed"
        attempts = getattr(session, "attempts", None)

        for rnd in range(1, policy.max_rounds + 1):
            if isinstance(attempts, dict):
                attempts["build"] = rnd
            found = detectors.lint(cur)
            if issues:
                found = _dedupe(found + list(issues))
            if self.runner is not None:
                try:
                    bout = self.runner.build(cur)
                except Exception as exc:
                    bout = {"ok": False, "message": f"build 调用失败: {exc}"}
                if isinstance(bout, Mapping):
                    session.build_out = dict(bout)
                found = _dedupe(found + issues_from(bout))
            if not found:
                break

            progress = False
            for issue in _ordered(found):
                outcome, action = self._fix(session, cur, issue, run_id, rnd, "build")
                rec = A.FixRecord(stage="build", code=fixers.code_of(issue), node=issue.node,
                                  action=action, detail=outcome.description)
                self._log(session, log, rec, outcome, run_id, rnd)
                progress = progress or outcome.applied
                if outcome.needs_user:
                    status = "needs_user"

            if (not progress and policy.enable_llm_fix and self.deep_fixer is not None
                    and llm_used < policy.max_llm_rounds):
                llm_used += 1
                res = self.deep_fixer.repair(cur, found)
                rec = A.FixRecord(stage="compile", code="LLM_DEEP_FIX", node=None,
                                  action="deep_fix", detail=res.detail)
                self._log(session, log, rec, res.outcome, run_id, rnd)
                if res.outcome.applied and res.ir is not None:
                    cur, progress = res.ir, True

            fp_hits[_sig(cur)] += 1
            if not progress or fp_hits[_sig(cur)] >= policy.stall_threshold:
                if status != "needs_user":
                    status = "stalled"
                break

        sim_out = None
        if self.runner is not None:
            for i in range(1, policy.max_sim_rounds + 1):
                if isinstance(attempts, dict):
                    attempts["simulate"] = i
                try:
                    sim_out = self.runner.simulate(
                        cur, sim_overrides or getattr(session, "sim_overrides", None))
                except Exception as exc:
                    sim_out = {"ok": False, "message": f"simulate 调用失败: {exc}"}
                if isinstance(sim_out, Mapping):
                    session.sim = dict(sim_out)
                sim_issues = issues_from(sim_out, default_code="EXPECT_FAILED")
                if not sim_issues:
                    break
                progress = False
                for issue in _ordered(sim_issues):
                    outcome, action = self._fix(session, cur, issue, run_id, i, "simulate")
                    tail = f"（用例 {issue.case}）" if issue.case else ""
                    rec = A.FixRecord(stage="simulate", code=fixers.code_of(issue),
                                      node=issue.node, action=action,
                                      detail=outcome.description + tail)
                    self._log(session, log, rec, outcome, run_id, i)
                    progress = progress or outcome.applied
                    if outcome.needs_user:
                        status = "needs_user"
                if not progress:
                    if status != "needs_user":
                        status = "needs_user"
                    break

        remaining = detectors.lint(cur)
        cov = coverage_mod.build_report(cur, sim_out, cases)
        if status == "fixed" and cov is not None and cov.edge_ratio() < policy.min_edge_coverage:
            status = "partial"
        report = A.RepairReport(ir=cur, issues=remaining, fix_log=log, fixed=not remaining)
        if isinstance(getattr(session, "fix_log", None), list):
            session.fix_log.extend(log)
        return LoopResult(report=report, status=status, coverage=cov,
                          simulation=sim_out, log=log)

    # -- 内部 ----------------------------------------------------------
    def _fix(self, session, cur, issue, run_id, rnd, stage):
        A = load_module()
        ctx = A.FixContext(orch=self.port, session=session, issue=issue, ir=cur)
        return fixers.apply_fix(ctx, self.registry)

    def _log(self, session, log, rec, outcome, run_id, rnd):
        log.append(rec)
        if self.history is not None:
            self.history.append(
                session_id=getattr(session, "session_id", ""), run_id=run_id, round=rnd,
                stage=rec.stage, code=rec.code, node=rec.node, action=rec.action,
                detail=rec.detail, applied=bool(outcome.applied),
                touched_ir=bool(outcome.touched_ir), needs_user=bool(outcome.needs_user),
                fingerprint=f"{rec.stage}:{rec.code}:{rec.node}",
                ir_version=load_module().IR_VERSION)


def code_of_issue(issue) -> str:
    return fixers.code_of(issue)


def _ordered(issues):
    return sorted(issues, key=lambda i: (_ORDER.get(fixers.code_of(i), 2),
                                         fixers.code_of(i), str(i.node)))


def _sig(ir) -> str:
    return hashlib.sha1(
        json.dumps(ir, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()[:12]


def _as_mapping(obj):
    return obj if isinstance(obj, Mapping) else {"ok": False, "message": str(obj)}


def _dedupe(issues):
    seen, out = set(), []
    for i in issues:
        key = (fixers.code_of(i), str(i.node), str(i.message), str(i.case))
        if key in seen:
            continue
        seen.add(key)
        out.append(i)
    return out


def issues_from(obj, *, default_code: str = "UNKNOWN") -> list:
    """容错解析 build/simulate 返回（结构以补发的契约为准，这里按多形状兜底）。"""
    A = load_module()
    raw = []
    if isinstance(obj, Mapping):
        for k in ("issues", "errors", "problems", "failures"):
            v = obj.get(k)
            if isinstance(v, list):
                raw = list(v)
                break
        if not raw:
            for c in obj.get("cases") or []:
                if isinstance(c, Mapping) and not _case_ok(c):
                    raw.append({"code": "EXPECT_FAILED", "case": c.get("name"),
                                "message": f"用例 {c.get('name')} 未达预期",
                                "raw": c.get("raw") or c})
        if not raw and obj.get("ok") is False:
            raw = [{"code": default_code, "message": str(obj.get("message") or "阶段失败")}]
    elif isinstance(obj, list):
        raw = list(obj)
    out = []
    for it in raw:
        if isinstance(it, A.BuildIssue):
            out.append(it)
            continue
        if isinstance(it, Mapping):
            try:
                code = A.ErrorCode(str(it.get("code") or default_code))
            except Exception:
                code = A.ErrorCode.UNKNOWN
            out.append(A.BuildIssue(code=code, node=it.get("node"),
                                    message=str(it.get("message") or it.get("msg") or it),
                                    line=it.get("line"), suggestions=list(it.get("suggestions") or []),
                                    case=it.get("case"), raw=it.get("raw") or dict(it)))
        else:
            out.append(A.BuildIssue(code=A.ErrorCode(default_code), node=None, message=str(it)))
    return out


def _case_ok(c) -> bool:
    if "ok" in c:
        return bool(c.get("ok"))
    if "passed" in c:
        return bool(c.get("passed"))
    return not bool(c.get("failed"))
