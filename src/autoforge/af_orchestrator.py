# -*- coding: utf-8 -*-
"""af_orchestrator.py — AutoForge NL → 自动化 统一编排层（参考实现）

链路: 自然语言 → AF-Spec → IR(JSON) → build(安全闸) → simulate(仿真) → af_save(待批队列)

职责边界
--------
1. 编排器是 MCP 的**调用方**，不注册、不暴露任何 MCP 工具。
2. **绝不调用 approve**：产物只进 PendingOp 待批队列，批准只能由人在 WebUI 完成。
   ``Orchestrator._call`` 内置硬护栏，任何 "approve*" 工具调用直接抛异常。
3. LLM 只做两件事：听懂人话（意图解析/回答抽取）、说人话（追问措辞/描述润色）。
   draft → 图模板 → AF-Spec → IR → build/sim 编译路径 100% 确定性，可离线复现。
4. 只用标准库；mcp_client / llm_client 均为 Protocol，便于 mock。

协议: af-compose/1   IR: 0.2.1
"""
from __future__ import annotations

import difflib
import json
import re
import time
import uuid
from .af_ir import Automation
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Optional, Protocol, Sequence

PROTOCOL_VERSION = "af-compose/1"
IR_VERSION = "0.2.1"
DEFAULT_TIMEOUT = "60s"


# =====================================================================
# 1. 端口（Protocol）—— 不绑定任何具体 MCP / LLM 实现
# =====================================================================
class MCPClient(Protocol):
    def call(self, tool: str, **kwargs: Any) -> Mapping[str, Any]: ...


class LLMClient(Protocol):
    def complete(self, prompt: str, *, system: str | None = None) -> str: ...


# =====================================================================
# 2. 枚举与异常
# =====================================================================
class Phase(str, Enum):
    INTAKE = "intake"
    RECOMMEND = "recommend"
    CLARIFY = "clarify"
    RESOLVE = "resolve"
    DRAFT = "draft"
    COMPILE = "compile"
    BUILD = "build"
    SIMULATE = "simulate"
    SCORE = "score"
    CONFIRM = "confirm"
    SAVE = "save"
    DONE = "done"
    FAILED = "failed"


class Status(str, Enum):
    NEED_CLARIFICATION = "need_clarification"
    RECOMMENDING = "recommending"
    AWAITING_CONFIRM = "awaiting_confirm"
    SAVED = "saved"
    FAILED = "failed"


class ErrorCode(str, Enum):
    SPEC_PARSE_ERROR = "SPEC_PARSE_ERROR"
    ENTITY_NOT_FOUND = "ENTITY_NOT_FOUND"
    STALE_ENTITY = "STALE_ENTITY"
    MISSING_TIMEOUT_OR_DEFAULT = "MISSING_TIMEOUT_OR_DEFAULT"
    MISSING_CANARY = "MISSING_CANARY"
    MISSING_NUMERIC_TYPE = "MISSING_NUMERIC_TYPE"
    MISSING_ON_ERROR = "MISSING_ON_ERROR"
    DISCONNECTED_NODE = "DISCONNECTED_NODE"
    CYCLE = "CYCLE"
    WAIT_EDGE_MISUSE = "WAIT_EDGE_MISUSE"
    SIM_BRANCH_MISMATCH = "SIM_BRANCH_MISMATCH"
    EXPECT_FAILED = "EXPECT_FAILED"
    UNKNOWN = "UNKNOWN"


class Risk(str, Enum):
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


class SpecSource(str, Enum):
    LLM = "llm"
    TEMPLATE = "template"
    MINIMAL = "minimal"


class OrchestratorError(RuntimeError):
    pass


class UnresolvedRefError(OrchestratorError):
    def __init__(self, key: str):
        self.key = key
        super().__init__(f"未解析的实体引用: {key}")


class ClarificationNeeded(OrchestratorError):
    """修正过程中发现必须由人决定（例如猜设备），中断流水线转追问。"""

    def __init__(self, gap: "Gap"):
        self.gap = gap
        super().__init__(gap.question)


# =====================================================================
# 3. 数据结构
# =====================================================================
@dataclass
class EntityRef:
    """草稿里的实体占位。entity_id 只允许由 resolve 阶段写入。"""
    key: str                      # "@lamp"
    name: str                     # "书房主灯"（自然语言名）
    area: str | None = None
    role: str = "action"          # trigger | condition | action
    entity_id: str | None = None
    candidates: list = field(default_factory=list)
    assumed: bool = False


@dataclass
class AutomationDraft:
    """带 @ref 占位的 IR 前身。语义声明，拓扑交给 build_graph()。"""
    id: str = ""
    name: str = ""
    area: str | None = None
    mode: str = "restart"
    confidence: float = 0.6
    snapshot: bool = True
    meta: dict = field(default_factory=dict)
    expect: list = field(default_factory=list)      # [{"entity_id": "@lamp", "state": "on"}]
    triggers: list = field(default_factory=list)    # IR 形状 on.trigger（entity_id 可为 @ref）
    conditions: list = field(default_factory=list)  # IR 表达式树（var 写作 "entity.@ref"）
    actions: list = field(default_factory=list)     # {"action","params","result","name"}
    asks: list = field(default_factory=list)        # {"prompt","session","room","timeout","name"}
    refs: dict = field(default_factory=dict)        # key -> EntityRef
    assumptions: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    def resolved(self) -> dict:
        return {k: r.entity_id for k, r in self.refs.items() if r.entity_id}

    def labels(self) -> dict:
        return {r.entity_id: r.name for r in self.refs.values() if r.entity_id}

    def assume(self, text: str) -> None:
        if text not in self.assumptions:
            self.assumptions.append(text)


@dataclass
class Gap:
    slot: str
    question: str
    options: list = field(default_factory=list)     # [{"label","value"}]
    priority: int = 1
    kind: str = "text"                              # choice|entity|number|text|confirm
    reason: str = ""
    apply: Callable[[Any], None] | None = None      # 把答案写回草稿

    def to_message(self) -> dict:
        return {
            "slot": self.slot,
            "kind": self.kind,
            "priority": self.priority,
            "question": self.question,
            "options": [{"label": o["label"], "value": o["value"]} for o in self.options],
            "reason": self.reason,
        }


@dataclass
class BuildIssue:
    code: ErrorCode
    node: str | None
    message: str
    line: int | None = None
    suggestions: list = field(default_factory=list)
    case: str | None = None
    raw: Any = None

    def to_message(self) -> dict:
        return {"code": self.code.value, "node": self.node, "message": self.message,
                "line": self.line, "case": self.case, "suggestions": self.suggestions}


@dataclass
class FixRecord:
    stage: str
    code: str
    node: str | None
    action: str
    detail: str = ""

    def to_message(self) -> dict:
        return {"stage": self.stage, "code": self.code, "node": self.node,
                "action": self.action, "detail": self.detail}


@dataclass
class FixOutcome:
    applied: bool
    description: str
    touched_ir: bool = True
    needs_user: bool = False


@dataclass
class FixContext:
    orch: "Orchestrator"
    session: "ComposeSession"
    issue: BuildIssue
    ir: dict

    @property
    def mcp(self) -> MCPClient:
        return self.orch.mcp

    @property
    def catalog(self) -> list:
        return self.orch._catalog(self.session)


@dataclass
class SimCase:
    name: str
    events: list
    expect_reach: list = field(default_factory=list)
    expect_not_reach: list = field(default_factory=list)
    check_engine_expect: bool = False
    note: str = ""


@dataclass
class QualityReport:
    dims: dict
    weights: dict
    total: float
    grade: str
    suggestions: list
    violations: dict

    def to_message(self) -> dict:
        return {"total": round(self.total, 2), "grade": self.grade,
                "dims": {k: round(v, 2) for k, v in self.dims.items()},
                "weights": self.weights, "suggestions": self.suggestions}


@dataclass
class Recommendation:
    key: str
    title: str
    blurb: str
    risk: str
    draft_hint: dict

    def to_message(self) -> dict:
        return {"key": self.key, "title": self.title, "blurb": self.blurb, "risk": self.risk}


@dataclass
class RepairReport:
    ir: dict
    issues: list
    fix_log: list
    fixed: bool


@dataclass
class ComposeSession:
    session_id: str
    phase: Phase = Phase.INTAKE
    draft: AutomationDraft = field(default_factory=AutomationDraft)
    history: list = field(default_factory=list)
    pending_gaps: list = field(default_factory=list)
    catalog: list | None = None
    spec: str = ""
    spec_source: SpecSource = SpecSource.TEMPLATE
    draft_graph: dict | None = None
    ir: dict | None = None
    build_out: dict | None = None
    sim: dict | None = None
    quality: QualityReport | None = None
    nl_description: str = ""
    fix_log: list = field(default_factory=list)
    attempts: dict = field(default_factory=lambda: {"compile": 0, "build": 0, "simulate": 0})
    sim_overrides: dict = field(default_factory=dict)   # case 名 -> 追加事件
    pending_id: str | None = None
    auto_commit: bool = True
    created_at: float = field(default_factory=time.time)

    def add_turn(self, role: str, text: str) -> None:
        self.history.append({"role": role, "text": text})


@dataclass
class ComposeResult:
    status: Status
    session_id: str
    message: str = ""
    gaps: list = field(default_factory=list)
    recommendations: list = field(default_factory=list)
    assumptions: list = field(default_factory=list)
    spec: str = ""
    ir: dict | None = None
    nl_description: str = ""
    quality: QualityReport | None = None
    simulation: dict | None = None
    fix_log: list = field(default_factory=list)
    issues: list = field(default_factory=list)
    pending_id: str | None = None

    def to_message(self) -> dict:
        """标准交互消息：任何 agent 都按这个信封渲染给用户。"""
        return {
            "protocol": PROTOCOL_VERSION,
            "session_id": self.session_id,
            "status": self.status.value,
            "message": self.message,
            "questions": [g.to_message() for g in self.gaps],
            "recommendations": [r.to_message() for r in self.recommendations],
            "assumptions": list(self.assumptions),
            "spec": self.spec,
            "ir": self.ir,
            "nl_description": self.nl_description,
            "quality": self.quality.to_message() if self.quality else None,
            "simulation": self.simulation,
            "fix_log": [f.to_message() for f in self.fix_log],
            "issues": [i.to_message() for i in self.issues],
            "pending_id": self.pending_id,
        }


# =====================================================================
# 4. 小工具
# =====================================================================
KNOWN_DOMAINS = {
    "light", "switch", "fan", "climate", "sensor", "binary_sensor", "cover",
    "media_player", "lock", "alarm_control_panel", "camera", "humidifier",
    "water_heater", "vacuum", "scene", "script", "automation", "input_boolean",
    "number", "select", "remote", "time", "sun", "tts",
}
ENTITY_RE = re.compile(r"^(?P<dom>[a-z][a-z0-9_]*)\.(?P<obj>[a-z0-9_]+)$")
REF_TOKEN_RE = re.compile(r"@([A-Za-z0-9_\u4e00-\u9fff]{1,24})")

DOMAIN_RISK = {
    "lock": Risk.L3, "alarm_control_panel": Risk.L3, "camera": Risk.L3,
    "climate": Risk.L2, "media_player": Risk.L2, "cover": Risk.L2,
    "water_heater": Risk.L2, "humidifier": Risk.L2, "vacuum": Risk.L2, "scene": Risk.L2,
}


def risk_of(action: str) -> Risk:
    parts = (action or "").split(".")
    return DOMAIN_RISK.get(parts[1] if len(parts) >= 3 else "", Risk.L1)


def entity_like(s: Any) -> bool:
    if not isinstance(s, str):
        return False
    m = ENTITY_RE.match(s)
    return bool(m and m.group("dom") in KNOWN_DOMAINS)


def iter_strings(obj: Any) -> Iterable[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, Mapping):
        for v in obj.values():
            yield from iter_strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from iter_strings(v)


def walk_dicts(obj: Any, path: tuple = ()) -> Iterable[tuple]:
    if isinstance(obj, Mapping):
        yield obj, path
        for k, v in obj.items():
            yield from walk_dicts(v, path + (k,))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk_dicts(v, path + (i,))


def find_entities(obj: Any) -> list:
    seen, out = set(), []
    for s in iter_strings(obj):
        if entity_like(s) and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def find_refs(obj: Any) -> list:
    seen, out = set(), []
    for s in iter_strings(obj):
        for m in REF_TOKEN_RE.finditer(s):
            k = "@" + m.group(1)
            if k not in seen:
                seen.add(k)
                out.append(k)
    return out


def substitute_refs(obj: Any, resolved: Mapping[str, str]) -> Any:
    if isinstance(obj, str):
        def _rep(m):
            key = "@" + m.group(1)
            rid = resolved.get(key)
            if not rid:
                return m.group(0)  # 未解析的引用保留原样（草稿预览用），后续 build 会报 ENTITY_NOT_FOUND
            return rid
        return REF_TOKEN_RE.sub(_rep, obj)
    if isinstance(obj, list):
        return [substitute_refs(x, resolved) for x in obj]
    if isinstance(obj, Mapping):
        return {k: substitute_refs(v, resolved) for k, v in obj.items()}
    return obj


def set_path(root: Any, path: tuple, value: Any) -> None:
    cur = root
    for p in path[:-1]:
        cur = cur[p]
    cur[path[-1]] = value


def clamp(x: float, lo: float = 0.0, hi: float = 10.0) -> float:
    return max(lo, min(hi, x))


def _esc(s: Any) -> str:
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _as_list(x: Any) -> list:
    if x is None:
        return []
    return list(x) if isinstance(x, list) else [x]


def _is_yes(text: str) -> bool:
    t = (text or "").strip().lower()
    return t in {"好", "好的", "可以", "行", "嗯", "是", "对", "确认", "同意", "ok", "okay", "yes", "y", "就这样", "保存"}


def _is_no(text: str) -> bool:
    t = (text or "").strip().lower()
    return t in {"不", "不用", "不要", "算了", "别", "否", "取消", "no", "n", "拒绝"}


def _is_cancel(text: str) -> bool:
    return (text or "").strip().lower() in {"取消", "离开", "中断", "cancel", "算了", "下次吧"}


def _parse_time(text: str) -> str | None:
    m = re.search(r"(\d{1,2})\s*[:：点时]\s*(\d{2})?", text or "")
    if not m:
        return None
    hh, mm = int(m.group(1)), int(m.group(2) or 0)
    return f"{hh:02d}:{mm:02d}"


def _parse_number(text: str) -> int | float | None:
    m = re.search(r"-?\d+(?:\.\d+)?", text or "")
    if not m:
        return None
    raw = m.group(0)
    return float(raw) if "." in raw else int(raw)


def _domain_of(eid: str) -> str:
    return (eid or "").split(".")[0]


def _area_of(entity: Mapping[str, Any]) -> str | None:
    attrs = entity.get("attributes") or {}
    return attrs.get("area") or entity.get("area")


def _name_of(entity: Mapping[str, Any]) -> str:
    attrs = entity.get("attributes") or {}
    return str(attrs.get("friendly_name") or entity.get("name") or entity.get("entity_id"))


def _fresh(entity: Mapping[str, Any]) -> bool:
    attrs = entity.get("attributes") or {}
    return not attrs.get("stale") and entity.get("state") != "unavailable"


def _sim(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a or "", b or "").ratio()


# =====================================================================
# 5. 中文渲染（节点命名 / 确定性自然语言描述）
# =====================================================================
VERB_ZH = {
    "turn_on": "开启", "turn_off": "关闭", "toggle": "切换", "set_temperature": "调温",
    "set_brightness": "调亮", "lock": "上锁", "unlock": "解锁", "open": "打开", "close": "关闭",
    "set_humidity": "调湿", "start": "启动",
}


def _label_of_var(var: str | None, labels: Mapping[str, str]) -> str:
    if not var:
        return "?"
    eid = var.split("entity.", 1)[-1]
    return labels.get(eid, eid)


def describe_trigger(t: Mapping[str, Any], labels: Mapping[str, str]) -> str:
    typ = t.get("type")
    if typ == "state":
        return f"{_label_of_var('entity.' + str(t.get('entity_id')), labels)} 变为 {t.get('to', '任意状态')}"
    if typ == "for":
        return f"{_label_of_var('entity.' + str(t.get('entity_id')), labels)} 保持 {t.get('state', 'on')} 达 {t.get('duration', '?')}"
    if typ == "time":
        return f"到 {t.get('at', '?')}"
    if typ == "sun":
        zh = "日出" if t.get("event") == "sunrise" else "日落"
        return zh + (f"（偏移 {t.get('offset')}）" if t.get("offset") else "")
    if typ == "event":
        return f"事件 {t.get('event_type', '?')}"
    return "触发"


def describe_condition(expr: Any, labels: Mapping[str, str]) -> str:
    if not isinstance(expr, Mapping):
        return "?"
    op = expr.get("op")
    if op == "and":
        return "(" + " 且 ".join(describe_condition(a, labels) for a in expr.get("args", [])) + ")"
    if op == "or":
        return "(" + " 或 ".join(describe_condition(a, labels) for a in expr.get("args", [])) + ")"
    if op == "not":
        return "非 " + describe_condition((expr.get("args") or [{}])[0], labels)
    if op in ("is_on", "is_off"):
        v = (expr.get("value") or {}).get("var")
        return f"{_label_of_var(v, labels)} {'开启' if op == 'is_on' else '关闭'}"
    left, right = expr.get("left") or {}, expr.get("right") or {}
    l = _label_of_var(left.get("var"), labels)
    r = right.get("const", "?")
    zh = {"gt": "高于", "lt": "低于", "ge": "不低于", "le": "不高于",
          "eq": "等于", "ne": "不等于", "contains": "含有"}.get(op, op)
    return f"{l} {zh} {r}"


def describe_action(n: Mapping[str, Any], labels: Mapping[str, str]) -> str:
    verb = (n.get("action") or "").split(".")[-1]
    eid = (n.get("params") or {}).get("entity_id")
    return f"{VERB_ZH.get(verb, verb)} {labels.get(eid, eid) if isinstance(eid, str) else eid}"


def render_nl(ir: Mapping[str, Any], labels: Mapping[str, str] | None = None) -> str:
    """确定性自然语言描述（build 未返回 nl_description 时的兜底，也是可复现的验收文本）。"""
    labels = labels or {}
    nodes = {n["id"]: n for n in ir.get("nodes", [])}
    edges = ir.get("edges", [])

    def nm(n):
        return n.get("name") or n["id"]

    trigs = [describe_trigger(n.get("trigger") or {}, labels) for n in nodes.values() if n.get("kind") == "on"]
    conds = [describe_condition(n.get("condition"), labels) for n in nodes.values() if n.get("kind") == "if"]
    dos = [describe_action(n, labels) for n in nodes.values() if n.get("kind") == "do"]
    asks = [n for n in nodes.values() if n.get("kind") == "ask"]

    parts = ["当" + ("、".join(trigs) if trigs else "手动触发") + "时"]
    if conds:
        parts.append("若 " + " 且 ".join(conds))
    if asks:
        q = asks[0]
        parts.append(f"先问一句「{q.get('prompt', '')}」")
        branch = {}
        for e in edges:
            if e.get("from") == q["id"]:
                branch.setdefault(e.get("when"), nodes.get(e.get("to")))
        zh = {"yes": "回答肯定则", "no": "回答否定则", "default": "回答含糊则",
              "on_timeout": "超时未答则", "on_cancel": "用户取消则"}
        for when in ("yes", "no", "default", "on_timeout", "on_cancel"):
            tgt = branch.get(when)
            if tgt is None:
                continue
            tail = "静默结束" if tgt.get("kind") == "pass" else describe_action(tgt, labels) if tgt.get("kind") == "do" else nm(tgt)
            parts.append(f"{zh[when]}{tail}")
    elif dos:
        parts.append("执行 " + "、".join(dos))
    return "，".join(parts) + "。"


# =====================================================================
# 6. 图模板 + AF-Spec 渲染
# =====================================================================
def _auto_name(kind: str, payload: Mapping[str, Any], labels: Mapping[str, str]) -> str:
    if kind == "on":
        return describe_trigger(payload, labels)
    if kind == "if":
        return describe_condition(payload, labels)
    if kind == "do":
        return describe_action(payload, labels)
    return ""


def build_graph(draft: AutomationDraft) -> dict:
    """语义声明 → 标准拓扑 IR。这是消灭'边写错'类 build 失败的关键一步。"""
    resolved = draft.resolved()
    labels = draft.labels()
    sub = lambda o: substitute_refs(o, resolved)          # noqa: E731
    nodes: list = []
    edges: list = []
    counter = {"e": 0}

    def add_edge(frm: str, to: str, when: str, label: str | None = None):
        counter["e"] += 1
        e = {"from": frm, "to": to, "when": when, "id": f"e{counter['e']}"}
        if label:
            e["label"] = label
        edges.append(e)

    trig_ids, cond_ids, ask_ids, do_ids = [], [], [], []
    for i, t in enumerate(draft.triggers, 1):
        nid = f"a{i}"
        trigger = {k: sub(v) for k, v in t.items() if k != "name" and v is not None}
        nodes.append({"id": nid, "kind": "on", "trigger": trigger,
                      "name": t.get("name") or _auto_name("on", trigger, labels)})
        trig_ids.append(nid)
    for i, c in enumerate(draft.conditions, 1):
        nid = f"i{i}"
        cond = sub(c)
        nodes.append({"id": nid, "kind": "if", "condition": cond,
                      "name": c.get("_name") or _auto_name("if", cond, labels)})
        cond_ids.append(nid)
    for i, a in enumerate(draft.asks, 1):
        nid = f"q{i}"
        nodes.append({"id": nid, "kind": "ask",
                      "prompt": a.get("prompt") or "",
                      "session": a.get("session", "room"),
                      "room": a.get("room") or draft.area or "",
                      "timeout": a.get("timeout") or DEFAULT_TIMEOUT,
                      "name": a.get("name") or "询问用户"})
        ask_ids.append(nid)

    needs_guard = False
    for i, act in enumerate(draft.actions, 1):
        nid = f"d{i}"
        node = {"id": nid, "kind": "do",
                "action": act.get("action") or "ha.script.turn_on",
                "params": sub(act.get("params") or {}),
                "name": act.get("name") or _auto_name("do", act, labels)}
        if act.get("result"):
            node["result"] = act["result"]
        if risk_of(node["action"]) != Risk.L1:
            node["requires_confirm"] = True
            node["canary"] = act.get("canary") or {"percent": 10, "window": "24h"}
            needs_guard = True
        nodes.append(node)
        do_ids.append(nid)

    use_p2 = bool(cond_ids or ask_ids)
    nodes.append({"id": "p1", "kind": "pass", "name": "结束"})
    if use_p2:
        nodes.append({"id": "p2", "kind": "pass", "name": "未执行"})

    seq = cond_ids + ask_ids + do_ids
    head = seq[0] if seq else "p1"
    for t in trig_ids:
        add_edge(t, head, "then")
    kind_of = lambda nid: next(n["kind"] for n in nodes if n["id"] == nid)   # noqa: E731
    for idx, nid in enumerate(seq):
        kind = kind_of(nid)
        nxt = seq[idx + 1] if idx + 1 < len(seq) else "p1"
        if kind == "if":
            add_edge(nid, nxt, "then")
            add_edge(nid, "p2", "no")
        elif kind == "ask":
            add_edge(nid, do_ids[0] if do_ids else "p1", "yes")
            add_edge(nid, "p2", "no")
            add_edge(nid, "p2", "on_cancel", label="用户拒绝")
        elif kind == "do":
            add_edge(nid, nxt, "then")
            add_edge(nid, "p1", "on_error", label="失败兜底")
        else:
            add_edge(nid, nxt, "then")
    if ask_ids:
        for q in ask_ids:
            add_edge(q, "p1", "on_timeout")
            add_edge(q, "p1", "default")

    graph = {
        "id": draft.id or "draft_" + uuid.uuid4().hex[:6],
        "name": draft.name or "未命名自动化",
        "ir_version": IR_VERSION,
        "version": 1,
        "mode": draft.mode or "restart",
        "confidence": round(float(draft.confidence), 2),
        "snapshot": bool(draft.snapshot),
        "nodes": nodes,
        "edges": edges,
    }
    if draft.meta:
        graph["meta"] = sub(dict(draft.meta))
    if needs_guard:
        graph.setdefault("meta", {})["canary"] = graph.get("meta", {}).get("canary") or {"percent": 10, "window": "24h"}
    if draft.expect:
        graph["expect"] = sub([dict(x) for x in draft.expect])
    return graph


def compose_group(name: str, children: list, group_id: str | None = None,
                  ir_version: str = IR_VERSION) -> "Automation":
    """把多条已构建自动化（Automation 或 IR dict）组合成一个 group 复合 IR（v2.3/F9）。

    返回的 Automation 顶层只有一个 group 节点，children 为各子自动化的完整 IR；
    作为原子部署单元（单 ref 回滚）与组合冲突预检（F10②）的载体。
    """
    gid = group_id or ("grp_" + uuid.uuid4().hex[:6])
    child_dicts = [c.raw if isinstance(c, Automation) else c for c in children]
    group_node = {"id": "g1", "kind": "group", "name": name or "组合", "children": child_dicts}
    return Automation.from_dict({
        "ir_version": ir_version,
        "id": gid,
        "name": name or "组合",
        "version": 1,
        "mode": "group",
        "nodes": [group_node],
        "edges": [],
    })


def render_node(n: Mapping[str, Any]) -> str:
    nid, kind = n["id"], n["kind"]
    tail = f' name "{_esc(n["name"])}"' if n.get("name") else ""
    if kind == "on":
        extra = ""
        for k in ("to", "at", "offset", "for", "duration", "state"):
            if k in (n.get("trigger") or {}) and k not in ("type", "entity_id", "event_type"):
                pass  # 全部内联在 trigger JSON 里
        return f'on {nid} {_dumps(n["trigger"])}{extra}{tail}'
    if kind == "if":
        return f'if {nid} {_dumps(n["condition"])}{tail}'
    if kind == "wait":
        return f'wait {nid} {n.get("duration", "30s")}{tail}'
    if kind == "ask":
        p = [f'ask {nid} "{_esc(n.get("prompt", ""))}"',
             f'session {n.get("session", "room")}',
             f'room "{_esc(n.get("room", ""))}"',
             f'timeout "{_esc(n.get("timeout", DEFAULT_TIMEOUT))}"']
        return " ".join(p) + tail
    if kind == "do":
        p = [f'do {nid} {n.get("action", "")} {_dumps(n.get("params") or {})}']
        if n.get("result"):
            p.append(f'result {n["result"]}')
        if n.get("requires_confirm"):
            p.append('requires_confirm true')
        if n.get("canary"):
            p.append(f'canary {_dumps(n["canary"])}')
        return " ".join(p) + tail
    if kind == "set":
        return f'set {nid} var {n.get("var", "v")} {_dumps(n.get("value") or {})}{tail}'
    return f'pass {nid}{tail}'


def render_edge(e: Mapping[str, Any]) -> str:
    s = f'edge {e["from"]} -> {e["to"]} {e["when"]}'
    if e.get("id"):
        s += f' id {e["id"]}'
    if e.get("label"):
        s += f' label "{_esc(e["label"])}"'
    return s


def render_spec(graph: Mapping[str, Any], *, minimal: bool = False) -> str:
    """IR 形状 → AF-Spec 文本（附录 A 语法）。minimal 用于 parse 失败后的降级重渲染。"""
    L = [f'automation {graph.get("id", "draft")}',
         f'name "{_esc(graph.get("name", ""))}"',
         f'ir_version "{graph.get("ir_version", IR_VERSION)}"',
         f'version {int(graph.get("version", 1))}',
         f'mode {graph.get("mode", "single")}']
    if not minimal:
        if graph.get("confidence") is not None:
            L.append(f'confidence {graph["confidence"]}')
        if graph.get("snapshot") is not None:
            L.append(f'snapshot {"true" if graph.get("snapshot") else "false"}')
        for k, v in (graph.get("meta") or {}).items():
            L.append(f'meta {_dumps({k: v})}')
        for k, v in (graph.get("vars") or {}).items():
            L.append(f'var {k} {_dumps(v)}')
    for n in graph.get("nodes", []):
        L.append(render_node(n))
    for e in graph.get("edges", []):
        L.append(render_edge(e))
    if not minimal:
        for x in graph.get("expect") or []:
            L.append(f'expect {_dumps(x)}')
    return "\n".join(L) + "\n"


# =====================================================================
# 7. 意图解析（LLM 抽象 + 启发式兜底）
# =====================================================================
INTENT_SYSTEM = """你是 AutoForge 的意图解析器。把用户自然语言转成 JSON，只输出 JSON。
schema:
{"intent_kind": "build|recommend|smalltalk",
 "area": "房间或 null",
 "id": "自动化英文 id（可空）",
 "name": "中文名称",
 "mode": "single|restart|queued|parallel 或 null",
 "triggers": [IR 形状 on.trigger，实体写 @ref 占位，如 {"type":"state","entity_id":"@motion","to":"on","name":"检测到有人"}],
 "conditions": [IR 条件表达式树，var 写 "entity.@ref"，如 {"op":"gt","left":{"var":"entity.@temp","type":"numeric"},"right":{"const":27}}],
 "actions": [{"action":"ha.<domain>.<verb>","params":{"entity_id":"@ref"},"result":null,"name":"中文动作名"}],
 "asks": [{"prompt":"问句","session":"room","room":"房间","timeout":"60s","name":"中文名"}],
 "expect": [{"entity_id":"@ref","state":"on"}],
 "refs": [{"key":"@ref","name":"中文设备名","area":"房间","role":"trigger|condition|action"}]}
硬规则：
1) 实体一律用 @ref 占位，**禁止**写真实 entity_id；
2) 数值比较必须给 var 加 "type":"numeric"；
3) 信息不足就把字段留空/null，编排器会追问，不要猜；
4) 用户只是想要"某个房间的自动化"而没说清做什么 → intent_kind=recommend。"""

AREA_WORDS = ["书房", "客厅", "主卧", "次卧", "儿童房", "卧室", "厨房", "卫生间", "阳台", "玄关", "餐厅"]
DEVICE_PATTERNS = [
    (r"人体|有人|人来| motion", "motion"), (r"温度|温度计", "temp"), (r"光照|亮度|太暗", "illum"),
    (r"门|窗", "door"), (r"空调", "climate"), (r"灯", "light"), (r"锁", "lock"), (r"湿度", "hum"),
]


@dataclass
class ParsedIntent:
    data: dict
    source: str = "llm"


def _extract_json(text: str) -> dict | None:
    if not text:
        return None
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


class HeuristicIntentParser:
    """无 LLM 时的兜底解析器：只覆盖常见口语模式，覆盖不了就当 recommend。"""

    def parse(self, text: str, catalog: Sequence[Mapping[str, Any]] = ()) -> dict:
        out = {"intent_kind": "build", "area": None, "triggers": [], "conditions": [],
               "actions": [], "asks": [], "refs": [], "expect": []}
        for w in AREA_WORDS:
            if w in text:
                out["area"] = w
                break
        kinds = [k for pat, k in DEVICE_PATTERNS if re.search(pat, text)]
        if not kinds:
            out["intent_kind"] = "recommend"
            return out
        if "light" in kinds and re.search(r"开|亮", text):
            out["actions"].append({"action": "ha.light.turn_on", "params": {"entity_id": "@lamp"},
                                   "name": "开灯"})
            out["refs"].append({"key": "@lamp", "name": (out["area"] or "") + "主灯",
                                "area": out["area"], "role": "action"})
        if "climate" in kinds and re.search(r"开|制冷|空调", text):
            out["actions"].append({"action": "ha.climate.turn_on", "params": {"entity_id": "@ac"},
                                   "name": "开空调"})
            out["refs"].append({"key": "@ac", "name": (out["area"] or "") + "空调",
                                "area": out["area"], "role": "action"})
        if "motion" in kinds:
            out["triggers"].append({"type": "state", "entity_id": "@motion", "to": "on", "name": "检测到有人"})
            out["refs"].append({"key": "@motion", "name": (out["area"] or "") + "人体传感器",
                                "area": out["area"], "role": "trigger"})
        if "temp" in kinds:
            out["triggers"].append({"type": "state", "entity_id": "@temp", "name": "温度变化"})
            out["refs"].append({"key": "@temp", "name": (out["area"] or "") + "温度",
                                "area": out["area"], "role": "trigger"})
        if re.search(r"要不要|问一句|问我|确认", text):
            out["asks"].append({"prompt": "要执行吗？", "session": "room", "timeout": DEFAULT_TIMEOUT,
                                "name": "询问用户"})
        out["name"] = text[:16]
        return out


class IntentParser:
    def __init__(self, llm: LLMClient | None):
        self.llm = llm
        self.fallback = HeuristicIntentParser()

    def parse(self, text: str, catalog: Sequence[Mapping[str, Any]] = (), history: Sequence[Mapping] = ()) -> dict:
        if self.llm is not None:
            names = [{"entity_id": e.get("entity_id"), "name": _name_of(e), "area": _area_of(e),
                      "domain": _domain_of(str(e.get("entity_id")))} for e in list(catalog)[:120]]
            hist = "\n".join(f'{h.get("role")}: {h.get("text")}' for h in list(history)[-6:])
            prompt = (f"设备目录（供识别设备名，不要输出 entity_id）:\n{_dumps(names, )}\n\n"
                      f"对话上下文:\n{hist}\n\n用户说: {text}\n\n只输出 JSON:")
            try:
                raw = self.llm.complete(prompt, system=INTENT_SYSTEM)
                data = _extract_json(raw)
                if isinstance(data, dict):
                    data.setdefault("intent_kind", "build")
                    return data
            except Exception:
                pass
        return self.fallback.parse(text, catalog)


# =====================================================================
# 8. 草稿合并 / 缺口检测
# =====================================================================
def normalize_var_paths(obj: Any) -> Any:
    """把 {"var": "@temp"} 规范成 {"var": "entity.@temp"}。"""
    if isinstance(obj, Mapping):
        out = {}
        for k, v in obj.items():
            if k == "var" and isinstance(v, str) and v.startswith("@"):
                out[k] = "entity." + v
            else:
                out[k] = normalize_var_paths(v)
        return out
    if isinstance(obj, list):
        return [normalize_var_paths(x) for x in obj]
    return obj


def derive_expect(draft: AutomationDraft) -> list:
    out = []
    for act in draft.actions:
        verb = (act.get("action") or "").split(".")[-1]
        eid = (act.get("params") or {}).get("entity_id")
        if verb in ("turn_on", "turn_off") and isinstance(eid, str):
            out.append({"entity_id": eid, "state": "on" if verb == "turn_on" else "off"})
    return out


def default_prompt(draft: AutomationDraft) -> str:
    if draft.actions:
        a = draft.actions[0]
        verb = (a.get("action") or "").split(".")[-1]
        who = a.get("name") or describe_action({"action": a.get("action"), "params": a.get("params")}, draft.labels())
        return f"要{VERB_ZH.get(verb, verb)}{who}吗？"
    return "要执行这个自动化吗？"


def merge_intent(draft: AutomationDraft, intent: Mapping[str, Any], *, overwrite: bool = False) -> None:
    """把 LLM/模板/答案解析出的意图碎片并入草稿（槽位累积）。"""
    if not intent:
        return
    for k in ("id", "name", "area", "mode"):
        v = intent.get(k)
        if v not in (None, "") and (overwrite or not getattr(draft, k)):
            setattr(draft, k, v)
    if intent.get("confidence") is not None:
        draft.confidence = max(draft.confidence, float(intent["confidence"]))
    if intent.get("meta"):
        draft.meta.update(intent["meta"])

    for r in intent.get("refs") or []:
        key = r.get("key") or r.get("ref")
        if not key:
            continue
        key = key if str(key).startswith("@") else "@" + str(key)
        ref = draft.refs.get(key) or EntityRef(key=key, name=str(r.get("name") or key.lstrip("@")),
                                              area=r.get("area") or draft.area, role=r.get("role", "action"))
        if r.get("name"):
            ref.name = r["name"]
        if r.get("area"):
            ref.area = r["area"]
        if r.get("role"):
            ref.role = r["role"]
        if r.get("entity_id"):
            ref.entity_id = r["entity_id"]
        draft.refs[key] = ref

    for t in _as_list(intent.get("triggers") or intent.get("trigger")):
        t = normalize_var_paths(dict(t))
        if overwrite:
            draft.triggers = [t]
        else:
            draft.triggers.append(t)
    for c in _as_list(intent.get("conditions") or intent.get("condition")):
        c = normalize_var_paths(dict(c))
        (draft.conditions.clear() if overwrite else None)
        draft.conditions.append(c)
    for a in _as_list(intent.get("actions") or intent.get("action")):
        a = dict(a)
        a.setdefault("params", {})
        keys = [k for k, r in draft.refs.items() if r.role == "action" and not r.entity_id]
        if "entity_id" not in a["params"] and len(keys) == 1:
            a["params"]["entity_id"] = keys[0]
        (draft.actions.clear() if overwrite else None)
        draft.actions.append(a)
    for q in _as_list(intent.get("asks") or intent.get("ask")):
        q = dict(q)
        (draft.asks.clear() if overwrite else None)
        draft.asks.append(q)
    for x in _as_list(intent.get("expect")):
        draft.expect.append(normalize_var_paths(dict(x)))

    # 自动发现 @ref（LLM 忘写 refs 时兜底）
    for key in find_refs({"t": draft.triggers, "c": draft.conditions,
                          "a": draft.actions, "q": draft.asks, "e": draft.expect}):
        draft.refs.setdefault(key, EntityRef(key=key, name=key.lstrip("@"), area=draft.area, role="action"))

    _apply_defaults(draft)


def _apply_defaults(draft: AutomationDraft) -> None:
    """ASSUMPTION_RULES 的落地：可假设的填默认并记账，不可假设的留给 detect_gaps。"""
    if not draft.id:
        draft.id = "af_" + uuid.uuid4().hex[:8]
    if not draft.name:
        draft.name = (draft.area or "") + (default_prompt(draft)[:10] or "自动化")
    if not draft.triggers:
        draft.mode = draft.mode or "single"
    else:
        typ = draft.triggers[0].get("type")
        draft.mode = draft.mode or ("single" if typ == "time" else "restart")
        draft.assume(f"mode 默认 {draft.mode}")
    for q in draft.asks:
        if not q.get("session"):
            q["session"] = "room"
            draft.assume("ask session 默认 room（按房间应答）")
        if not q.get("timeout"):
            q["timeout"] = DEFAULT_TIMEOUT
            draft.assume(f"ask 超时默认 {DEFAULT_TIMEOUT}")
        if not q.get("room"):
            q["room"] = draft.area or ""
        if not q.get("prompt"):
            q["prompt"] = default_prompt(draft)
            draft.assume(f"询问文案自动生成：「{q['prompt']}」")
    # L2/L3 默认先问人 + canary（用户没明说"直接执行"）
    risky = [a for a in draft.actions if risk_of(a.get("action", "")) != Risk.L1]
    if risky and not draft.asks:
        draft.asks.append({"prompt": default_prompt(draft), "session": "room",
                           "room": draft.area or "", "timeout": DEFAULT_TIMEOUT,
                           "name": "高风险动作先确认"})
        draft.assume("L2/L3 动作默认先问人（ask），并加 requires_confirm + canary")
    # 自动推导验收断言
    if not draft.expect:
        draft.expect = derive_expect(draft)
        if draft.expect:
            draft.assume("验收断言由动作自动推导（turn_on/off → 最终状态）")


# ---- 缺口检测 --------------------------------------------------------
def _iter_expr_nodes(expr: Any, path: tuple = ()):
    if isinstance(expr, Mapping):
        yield expr, path
        for k in ("args",):
            for i, a in enumerate(expr.get(k) or []):
                yield from _iter_expr_nodes(a, path + (k, i))
        for k in ("left", "right", "value"):
            if isinstance(expr.get(k), Mapping):
                yield from _iter_expr_nodes(expr[k], path + (k,))


def detect_gaps(draft: AutomationDraft, catalog: Sequence[Mapping[str, Any]] = ()) -> list:
    """信息缺口检测：返回按优先级排序的追问列表。空列表 = 信息齐了。"""
    gaps: list = []
    ents = [e for e in catalog if not draft.area or _area_of(e) in (draft.area, draft.area_alias
                                                                    if hasattr(draft, "area_alias") else draft.area)]
    if draft.area:
        ents = [e for e in catalog if _area_of(e) and draft.area and
                (draft.area in _name_of(e) or _area_of(e) == draft.area)]
        if not ents:
            ents = [e for e in catalog if draft.area and draft.area in _name_of(e)]
    ents = ents or list(catalog)

    # --- P0 动作 ---
    if not draft.actions:
        opts = []
        for e in ents:
            dom = _domain_of(str(e.get("entity_id")))
            nm = _name_of(e)
            if dom in ("light", "switch", "fan", "climate", "humidifier", "cover", "media_player"):
                for verb, zh in (("turn_on", "开"), ("turn_off", "关")):
                    opts.append({"label": f"{zh}{nm}",
                                 "value": {"actions": [{"action": f"ha.{dom}.{verb}",
                                                        "params": {"entity_id": e.get("entity_id")},
                                                        "name": f"{zh}{nm}"}]}})
            if dom == "climate":
                opts.append({"label": f"问一句要不要开{nm}",
                             "value": {"actions": [{"action": "ha.climate.turn_on",
                                                    "params": {"entity_id": e.get("entity_id")},
                                                    "name": f"开{nm}"}],
                                       "asks": [{"prompt": f"要开{nm}吗？", "session": "room",
                                                 "timeout": DEFAULT_TIMEOUT, "name": "询问用户"}]}})
        def _apply_action(value):
            merge_intent(draft, value if isinstance(value, Mapping) else {})
        gaps.append(Gap(slot="action", question="你想让哪个设备做什么？（例：开书房主灯 / 问一句要不要开空调）",
                        options=opts[:6], priority=10, kind="choice", reason="缺少动作", apply=_apply_action))

    # --- P0 设备引用 ---
    for key, ref in list(draft.refs.items()):
        if ref.entity_id:
            continue
        if ref.candidates:
            gaps.append(Gap(slot=f"entity:{key}",
                            question=f"「{ref.name}」有多个候选设备，用哪个？",
                            options=[{"label": f"{i + 1}. {c}", "value": c}
                                     for i, c in enumerate(ref.candidates)],
                            priority=9, kind="entity", reason="设备歧义",
                            apply=lambda v, r=ref: setattr(r, "entity_id", v)))
        else:
            # 先尝试自动匹配：同名/同 area 唯一候选 → 自动采纳（记 assumption）
            auto = [e for e in ents
                    if ref.name and (ref.name in _name_of(e) or _name_of(e) in ref.name)]
            exact = [e for e in auto if ref.name == _name_of(e)]
            if len(exact) == 1:
                ref.entity_id = str(exact[0].get("entity_id"))
                ref.assumed = True
                draft.assume(f"「{ref.name}」自动匹配为 {ref.entity_id}")
                continue
            scored = sorted(ents, key=lambda e: -_sim(_name_of(e), ref.name or ""))
            guess = [{"label": f"{i + 1}. {_name_of(e)} ({e.get('entity_id')})", "value": e.get("entity_id")}
                     for i, e in enumerate(scored[:2])]
            gaps.append(Gap(slot=f"entity:{key}",
                            question=f"没找到叫「{ref.name}」的设备，你指的是哪个？",
                            options=guess, priority=9, kind="entity", reason="设备不存在",
                            apply=lambda v, r=ref: setattr(r, "entity_id", v)))

    # --- P1 触发 ---
    if not draft.triggers:
        opts = []
        for e in ents:
            dom = _domain_of(str(e.get("entity_id")))
            attrs = e.get("attributes") or {}
            dev = attrs.get("device_class", "")
            nm = _name_of(e)
            if dom == "binary_sensor" and (dev == "motion" or "motion" in str(e.get("entity_id"))):
                opts.append({"label": f"{nm}检测到有人",
                             "value": {"triggers": [{"type": "state", "entity_id": e.get("entity_id"),
                                                     "to": "on", "name": f"{nm}检测到有人"}]}})
            if dom == "sensor":
                opts.append({"label": f"{nm}数值变化",
                             "value": {"triggers": [{"type": "state", "entity_id": e.get("entity_id"),
                                                     "name": f"{nm}数值变化"}]}})
        opts.append({"label": "每天定时", "value": {"triggers": [{"type": "time", "name": "定时触发"}]}})
        opts.append({"label": "日落/日出", "value": {"triggers": [{"type": "sun", "event": "sunset",
                                                                "offset": "-30m", "name": "日落前 30 分钟"}]}})

        def _apply_trigger(value):
            merge_intent(draft, value if isinstance(value, Mapping) else {})
        gaps.append(Gap(slot="trigger", question="什么情况下触发？",
                        options=opts[:6], priority=8, kind="choice", reason="缺少触发",
                        apply=_apply_trigger))

    for t in draft.triggers:
        if t.get("type") == "time" and not t.get("at"):
            gaps.append(Gap(slot="trigger.at", question="每天几点触发？（例：早上 7 点 / 07:00）",
                            priority=5, kind="text", reason="定时缺少时刻",
                            apply=lambda v, tt=t: tt.__setitem__("at", _parse_time(str(v)) or "07:00")))
        if t.get("type") == "for" and not t.get("duration"):
            gaps.append(Gap(slot="trigger.duration", question="状态持续多久算数？（例：5m / 1h）",
                            priority=5, kind="text", reason="for 触发缺少时长",
                            apply=lambda v, tt=t: tt.__setitem__("duration", str(v).strip())))

    # --- P2 阈值 ---
    for idx, cond in enumerate(draft.conditions):
        for node, path in _iter_expr_nodes(cond):
            if node.get("op") in ("gt", "lt", "ge", "le", "eq", "contains"):
                right = node.get("right")
                if not isinstance(right, Mapping) or right.get("const") in (None, "", "?"):
                    subj = _label_of_var((node.get("left") or {}).get("var"), draft.labels())
                    zh = {"gt": "高于", "lt": "低于", "ge": "不低于", "le": "不高于",
                          "eq": "等于", "contains": "包含"}.get(node.get("op"), "取值")
                    root = draft.conditions[idx]
                    gaps.append(Gap(slot=f"threshold:{idx}:{len(gaps)}",
                                    question=f"「{subj}」{zh}多少才触发？",
                                    priority=5, kind="number", reason="缺少阈值",
                                    apply=lambda v, r=root, p=path: set_path(r, p + ("right",), {"const": v})))

    # --- P3 房间 ---
    if not draft.area and not any(_area_of(e) for e in ents):
        areas = sorted({_area_of(e) for e in catalog if _area_of(e)})
        gaps.append(Gap(slot="area", question="是哪个房间的自动化？",
                        options=[{"label": a, "value": a} for a in areas] or
                                [{"label": w, "value": w} for w in AREA_WORDS[:6]],
                        priority=3, kind="choice", reason="缺少房间",
                        apply=lambda v: setattr(draft, "area", v)))

    gaps.sort(key=lambda g: -g.priority)
    return gaps


# =====================================================================
# 9. 推荐方案（"我想要个书房自动化"）
# =====================================================================
def capability_profile(entities: Sequence[Mapping[str, Any]]) -> set:
    caps = set()
    for e in entities:
        eid = str(e.get("entity_id", ""))
        dom = _domain_of(eid)
        attrs = e.get("attributes") or {}
        dev = str(attrs.get("device_class", ""))
        caps.add(dom)
        if "motion" in eid or dev == "motion":
            caps.add("motion")
        if dev in ("door", "window") or "door" in eid or "window" in eid:
            caps.add("door")
        if "temp" in eid or dev == "temperature":
            caps.add("temp")
        if "hum" in eid or dev == "humidity":
            caps.add("hum")
        if "illumin" in eid or dev == "illuminance" or "illum" in eid:
            caps.add("illum")
    return caps


def _pick(entities: Sequence[Mapping[str, Any]], pred) -> Mapping[str, Any] | None:
    for e in entities:
        if pred(e):
            return e
    return None


def build_recommendations(draft: AutomationDraft, entities: Sequence[Mapping[str, Any]]) -> list:
    caps = capability_profile(entities)
    area = draft.area or ""
    out: list = []
    lamp = _pick(entities, lambda e: _domain_of(str(e.get("entity_id"))) == "light")
    motion = _pick(entities, lambda e: "motion" in str(e.get("entity_id")) or
                   (e.get("attributes") or {}).get("device_class") == "motion")
    temp = _pick(entities, lambda e: "temp" in str(e.get("entity_id")) or
                 (e.get("attributes") or {}).get("device_class") == "temperature")
    door = _pick(entities, lambda e: (e.get("attributes") or {}).get("device_class") in ("door", "window"))
    ac = _pick(entities, lambda e: _domain_of(str(e.get("entity_id"))) == "climate")
    illum = _pick(entities, lambda e: "illum" in str(e.get("entity_id")))

    def hint(name, triggers, conditions, actions, asks=None, expect=None):
        return {"intent_kind": "build", "area": area, "name": name,
                "triggers": triggers, "conditions": conditions or [],
                "actions": actions, "asks": asks or [], "expect": expect or [],
                "refs": [], "confidence": 0.7}

    if motion and lamp:
        out.append(Recommendation(
            key="motion_light", title="有人来补光",
            blurb=f"{_name_of(motion)}检测到人、光照偏低时自动开{_name_of(lamp)}，没人管关不关。",
            risk="L1",
            draft_hint=hint("有人来补光",
                            [{"type": "state", "entity_id": motion.get("entity_id"), "to": "on", "name": "检测到有人"}],
                            ([{"op": "gt", "left": {"var": f"entity.{illum.get('entity_id')}", "type": "numeric"},
                               "right": {"const": 200}}] if illum else []),
                            [{"action": "ha.light.turn_on", "params": {"entity_id": lamp.get("entity_id")},
                              "name": f"开{_name_of(lamp)}"}])))
    if temp and ac:
        out.append(Recommendation(
            key="stuffiness_ac", title="闷了问一句要不要开空调",
            blurb=f"{_name_of(temp)}偏高且门窗关着时，先问你一句再开{_name_of(ac)}。",
            risk="L2",
            draft_hint=hint("闷了问一句要不要开空调",
                            [{"type": "state", "entity_id": temp.get("entity_id"), "name": "温度变化"}],
                            [{"op": "and", "args": [
                                {"op": "gt", "left": {"var": f"entity.{temp.get('entity_id')}", "type": "numeric"},
                                 "right": {"const": 27}}]
                              + ([{"op": "is_off", "value": {"var": f"entity.{door.get('entity_id')}"}}] if door else [])}],
                            [{"action": "ha.climate.turn_on", "params": {"entity_id": ac.get("entity_id")},
                              "name": f"开{_name_of(ac)}"}],
                            [{"prompt": f"{_name_of(temp)}有点高，要开{_name_of(ac)}吗？", "session": "room",
                              "timeout": DEFAULT_TIMEOUT, "name": "询问是否开空调"}])))
    if motion and lamp and illum:
        out.append(Recommendation(
            key="night_light", title="夜里起夜小灯",
            blurb="夜里检测到人且很暗时，只开小灯不打扰。",
            risk="L1",
            draft_hint=hint("夜里起夜小灯",
                            [{"type": "state", "entity_id": motion.get("entity_id"), "to": "on", "name": "夜间有人"}],
                            [{"op": "lt", "left": {"var": f"entity.{illum.get('entity_id')}", "type": "numeric"},
                              "right": {"const": 30}}],
                            [{"action": "ha.light.turn_on", "params": {"entity_id": lamp.get("entity_id")},
                              "name": f"开{_name_of(lamp)}"}])))
    if door:
        out.append(Recommendation(
            key="door_alert", title="门窗异常问一句",
            blurb=f"{_name_of(door)}状态变化时先问你要不要处理，不直接动作。",
            risk="L2",
            draft_hint=hint("门窗异常问一句",
                            [{"type": "state", "entity_id": door.get("entity_id"), "name": "门窗状态变化"}],
                            [],
                            ([{"action": "ha.light.turn_on", "params": {"entity_id": lamp.get("entity_id")},
                               "name": f"开{_name_of(lamp)}"}] if lamp else []),
                            [{"prompt": "门窗状态变了，要开灯看一眼吗？", "session": "room",
                              "timeout": DEFAULT_TIMEOUT, "name": "询问用户"}])))
    return out[:3]


# =====================================================================
# 10. 错误归一化
# =====================================================================
_CODE_PATTERNS = [
    (ErrorCode.MISSING_TIMEOUT_OR_DEFAULT, re.compile(r"missing_timeout|on_timeout|兜底", re.I)),
    (ErrorCode.ENTITY_NOT_FOUND, re.compile(r"entity_not_found|unknown entity|no such entity|不存在", re.I)),
    (ErrorCode.STALE_ENTITY, re.compile(r"stale|unavailable|失效", re.I)),
    (ErrorCode.MISSING_CANARY, re.compile(r"canary|requires_confirm|灰度", re.I)),
    (ErrorCode.MISSING_NUMERIC_TYPE, re.compile(r"numeric|字符串比较|type.*numeric", re.I)),
    (ErrorCode.MISSING_ON_ERROR, re.compile(r"on_error|失败兜底", re.I)),
    (ErrorCode.DISCONNECTED_NODE, re.compile(r"orphan|disconnected|isolated|孤立", re.I)),
    (ErrorCode.CYCLE, re.compile(r"cycle|loop|死循环|环", re.I)),
    (ErrorCode.WAIT_EDGE_MISUSE, re.compile(r"wait.*timeout|wait.*then", re.I)),
    (ErrorCode.SPEC_PARSE_ERROR, re.compile(r"spec_parse|parse error|语法", re.I)),
    (ErrorCode.SIM_BRANCH_MISMATCH, re.compile(r"branch|mismatch|分支|未到达", re.I)),
    (ErrorCode.EXPECT_FAILED, re.compile(r"expect|断言", re.I)),
]


def code_from_text(text: str) -> ErrorCode:
    t = str(text or "")
    for code in ErrorCode:
        if code.value.lower() in t.lower():
            return code
    for code, pat in _CODE_PATTERNS:
        if pat.search(t):
            return code
    return ErrorCode.UNKNOWN


def _issue_from(item: Any, raw: Any) -> BuildIssue:
    if isinstance(item, Mapping):
        code = code_from_text(str(item.get("code") or item.get("message") or item.get("error") or ""))
        return BuildIssue(code=code, node=item.get("node"), message=str(item.get("message") or item.get("error") or item),
                          line=item.get("line"), suggestions=list(item.get("suggestions") or []), raw=raw)
    return BuildIssue(code=code_from_text(str(item)), node=None, message=str(item), raw=raw)


def normalize_output(stage: str, raw: Any) -> list:
    """把 compile/build/simulate 的三种返回形态统一成 BuildIssue 列表。"""
    if raw is None:
        return [BuildIssue(ErrorCode.UNKNOWN, None, "空响应")]
    if isinstance(raw, str):
        raw = {"ok": False, "errors": [raw]}
    issues: list = []
    if stage == "compile" and not raw.get("ok", True):
        msg = str(raw.get("message") or raw.get("error") or "SPEC_PARSE_ERROR")
        return [BuildIssue(code_from_text(msg) if code_from_text(msg) != ErrorCode.UNKNOWN else ErrorCode.SPEC_PARSE_ERROR,
                           None, msg, line=raw.get("line"), raw=raw)]
    for item in raw.get("errors") or []:
        issues.append(_issue_from(item, raw))
    if not issues and not raw.get("ok", True):
        issues.append(_issue_from(raw.get("error") or "failed", raw))
    return issues


# =====================================================================
# 11. 修正策略库（FIXERS）
# =====================================================================
ASK_WHENS = {"yes", "no", "default", "on_timeout", "on_cancel"}
IF_WHENS = {"then", "no"}
DO_WHENS = {"then", "on_error"}
WAIT_WHENS = {"then", "on_error"}


def _next_edge_id(ir: Mapping[str, Any]) -> str:
    used = {e.get("id") for e in ir.get("edges", [])}
    i = len(used) + 1
    while f"e{i}" in used:
        i += 1
    return f"e{i}"


def _out_edges(ir: Mapping[str, Any], nid: str) -> list:
    return [e for e in ir.get("edges", []) if e.get("from") == nid]


def _degree(ir: Mapping[str, Any]) -> tuple:
    ids = {n["id"] for n in ir.get("nodes", [])}
    ind = {i: 0 for i in ids}
    outd = {i: 0 for i in ids}
    for e in ir.get("edges", []):
        if e.get("from") in ids:
            outd[e["from"]] += 1
        if e.get("to") in ids:
            ind[e["to"]] += 1
    return ind, outd


def _terminal_pass(ir: dict) -> str:
    _, outd = _degree(ir)
    for n in ir.get("nodes", []):
        if n.get("kind") == "pass" and outd.get(n["id"], 0) == 0:
            return n["id"]
    nid = f"p{len(ir.get('nodes', [])) + 1}"
    ir["nodes"].append({"id": nid, "kind": "pass", "name": "兜底结束"})
    return nid


def fix_missing_timeout(ctx: FixContext) -> FixOutcome:
    ir, added = ctx.ir, 0
    for n in list(ir.get("nodes", [])):
        if n.get("kind") != "ask":
            continue
        have = {e.get("when") for e in _out_edges(ir, n["id"])}
        target = _terminal_pass(ir)
        if "on_timeout" not in have:
            ir["edges"].append({"from": n["id"], "to": target, "when": "on_timeout", "id": _next_edge_id(ir)})
            added += 1
        if "default" not in have:
            ir["edges"].append({"from": n["id"], "to": target, "when": "default", "id": _next_edge_id(ir)})
            added += 1
    return FixOutcome(added > 0, f"为 ask 节点补 on_timeout/default 兜底边 ×{added}")


def fix_missing_canary(ctx: FixContext) -> FixOutcome:
    ir, changed = ctx.ir, 0
    for n in ir.get("nodes", []):
        if n.get("kind") == "do" and risk_of(n.get("action", "")) != Risk.L1:
            n["requires_confirm"] = True
            n.setdefault("canary", {"percent": 10, "window": "24h"})
            changed += 1
    if changed:
        ir.setdefault("meta", {}).setdefault("canary", {"percent": 10, "window": "24h"})
    return FixOutcome(changed > 0, f"为 {changed} 个 L2/L3 动作补 requires_confirm + canary")


def fix_numeric_type(ctx: FixContext) -> FixOutcome:
    changed = 0
    for n in ctx.ir.get("nodes", []):
        if n.get("kind") != "if":
            continue
        for node, _path in _iter_expr_nodes(n.get("condition")):
            if node.get("op") in ("gt", "lt", "ge", "le"):
                for side in ("left", "right"):
                    s = node.get(side)
                    if isinstance(s, Mapping) and "var" in s and "type" not in s:
                        s["type"] = "numeric"
                        changed += 1
    return FixOutcome(changed > 0, f"为数值比较补 \"type\":\"numeric\" ×{changed}")


def fix_missing_on_error(ctx: FixContext) -> FixOutcome:
    ir, added = ctx.ir, 0
    for n in ir.get("nodes", []):
        if n.get("kind") != "do":
            continue
        if not any(e.get("when") == "on_error" for e in _out_edges(ir, n["id"])):
            ir["edges"].append({"from": n["id"], "to": _terminal_pass(ir), "when": "on_error",
                                "id": _next_edge_id(ir), "label": "失败兜底"})
            added += 1
    return FixOutcome(added > 0, f"为 do 节点补 on_error 兜底边 ×{added}")


def fix_wait_edge(ctx: FixContext) -> FixOutcome:
    ir, changed = ctx.ir, 0
    kinds = {n["id"]: n.get("kind") for n in ir.get("nodes", [])}
    for e in ir.get("edges", []):
        if kinds.get(e.get("from")) == "wait" and e.get("when") == "on_timeout":
            e["when"] = "then"
            changed += 1
    return FixOutcome(changed > 0, f"wait 到期改走 then 边 ×{changed}（坑 #5）")


def fix_disconnected(ctx: FixContext) -> FixOutcome:
    ir = ctx.ir
    ind, _ = _degree(ir)
    orphans = [n for n in ir.get("nodes", []) if n.get("kind") != "on" and ind.get(n["id"], 0) == 0]
    if not orphans:
        return FixOutcome(False, "无孤立节点")
    target = _terminal_pass(ir)
    preds = [e for e in ir["edges"] if e.get("to") == target and e.get("when") == "then"]
    desc = []
    for n in list(orphans):
        if n.get("kind") == "pass":
            ir["nodes"] = [x for x in ir["nodes"] if x["id"] != n["id"]]
            ir["edges"] = [e for e in ir["edges"] if e.get("from") != n["id"] and e.get("to") != n["id"]]
            desc.append(f"删除孤立 pass {n['id']}")
            continue
        if preds:
            pred = preds[-1]["from"]
            ir["edges"] = [e for e in ir["edges"] if not (e.get("from") == pred and e.get("to") == target)]
            ir["edges"].append({"from": pred, "to": n["id"], "when": "then", "id": _next_edge_id(ir)})
            preds = [e for e in ir["edges"] if e.get("to") == target and e.get("when") == "then"]
        else:
            on = next((x["id"] for x in ir["nodes"] if x.get("kind") == "on"), None)
            if on:
                ir["edges"].append({"from": on, "to": n["id"], "when": "then", "id": _next_edge_id(ir)})
        ir["edges"].append({"from": n["id"], "to": target, "when": "then", "id": _next_edge_id(ir)})
        desc.append(f"把孤立节点 {n['id']} 插入主干")
    return FixOutcome(True, "；".join(desc))


def fix_cycle(ctx: FixContext) -> FixOutcome:
    ir = ctx.ir
    graph = defaultdict(list)
    for e in ir.get("edges", []):
        graph[e["from"]].append(e)
    stack, onstack, found = [], set(), []

    def dfs(u):
        stack.append(u)
        onstack.add(u)
        for e in graph.get(u, []):
            v = e["to"]
            if v in onstack:
                found.append(list(stack[stack.index(v):]) + [v])
                return True
            if v not in onstack and dfs(v):
                return True
        stack.pop()
        onstack.discard(u)
        return False

    for n in ir.get("nodes", []):
        if n["id"] not in onstack and dfs(n["id"]):
            break
    if not found:
        return FixOutcome(False, "未检测到环")
    cyc = found[0]
    kinds = {n["id"]: n.get("kind") for n in ir.get("nodes", [])}
    victims = [e for e in ir["edges"]
               if e["from"] in cyc and e["to"] in cyc and kinds.get(e["from"]) in ("do", "wait") and e.get("when") == "then"]
    victim = victims[-1] if victims else next((e for e in ir["edges"] if e["from"] in cyc and e["to"] in cyc), None)
    if victim is None:
        return FixOutcome(False, "环无法定位回边")
    ir["edges"].remove(victim)
    return FixOutcome(True, f"删除回边 {victim['from']}→{victim['to']}（破除死循环）")


def fix_edge_labels(ctx: FixContext) -> FixOutcome:
    kinds = {n["id"]: n.get("kind") for n in ctx.ir.get("nodes", [])}
    fixed = 0
    for e in ctx.ir.get("edges", []):
        kind = kinds.get(e.get("from"))
        allowed = {"ask": ASK_WHENS, "if": IF_WHENS, "do": DO_WHENS, "wait": WAIT_WHENS}.get(kind)
        if allowed and e.get("when") not in allowed:
            old = e["when"]
            _PREFERRED = {"ask": "yes", "if": "then", "do": "then", "wait": "then"}
            e["when"] = _PREFERRED.get(kind, sorted(allowed)[0])
            fixed += 1
            ctx.issue.message += f" [边 {e['from']}→{e['to']} 的 when={old} 非法，改为 {e['when']}]"
    return FixOutcome(fixed > 0, f"修正非法边名 ×{fixed}")


def _pick_replacement(ctx: FixContext, bad: str, node: Mapping[str, Any]) -> str | None:
    catalog = ctx.catalog
    pool: list = []
    out = ctx.orch._call_safe("af_resolve_entity", name=str(node.get("name") or bad))
    if isinstance(out, Mapping) and out.get("ok") and out.get("entity_id"):
        pool.append(out["entity_id"])
    pool.extend(ctx.issue.suggestions or [])
    pool.extend(e.get("entity_id") for e in catalog)
    pool = [p for p in pool if p and (not catalog or any(str(e.get("entity_id")) == p for e in catalog))]
    if not pool:
        return None
    scored = sorted({p: max(_sim(p, bad), _sim(str(node.get("name") or ""), p)) for p in pool}.items(),
                    key=lambda kv: -kv[1])
    top_id, top = scored[0]
    second = scored[1][1] if len(scored) > 1 else 0.0
    if top >= 0.75 or (ctx.issue.suggestions and top_id in ctx.issue.suggestions and top - second > 0.05):
        return top_id
    return None


def fix_entity_not_found(ctx: FixContext) -> FixOutcome:
    ir = ctx.ir
    node = next((n for n in ir.get("nodes", []) if n["id"] == ctx.issue.node), None)
    if node is None:
        return FixOutcome(False, "定位不到出错节点")
    bad = next((s for s in find_entities(node) if not any(str(e.get("entity_id")) == s for e in ctx.catalog)), None)
    if bad is None:
        return FixOutcome(False, "节点内未发现未知实体")
    new = _pick_replacement(ctx, bad, node)
    if not new:
        guess = [str(e.get("entity_id")) for e in ctx.catalog if _domain_of(str(e.get("entity_id"))) == _domain_of(bad)][:5]
        raise ClarificationNeeded(Gap(slot=f"entity:{ctx.issue.node}",
                                      question=f"「{bad}」不在设备目录里，你要的是哪个？",
                                      options=[{"label": f"{i + 1}. {c}", "value": c} for i, c in enumerate(guess)],
                                      priority=9, kind="entity", reason="build: ENTITY_NOT_FOUND"))
    def _rep(obj):
        if isinstance(obj, str):
            return new if obj == bad else obj
        if isinstance(obj, Mapping):
            return {k: _rep(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_rep(x) for x in obj]
        return obj
    node_id = node["id"]
    for n in ir["nodes"]:
        if n["id"] == node_id:
            n.update(_rep(n))
    return FixOutcome(True, f"{node_id} 的实体 {bad} → {new}")


def fix_stale_entity(ctx: FixContext) -> FixOutcome:
    ir = ctx.ir
    node = next((n for n in ir.get("nodes", []) if n["id"] == ctx.issue.node), None)
    if node is None:
        return FixOutcome(False, "定位不到 STALE 节点")
    bad = next((s for s in find_entities(node)
                if any(str(e.get("entity_id")) == s and not _fresh(e) for e in ctx.catalog)), None)
    if bad is None:
        return FixOutcome(False, "未发现失效实体")
    bad_dev = next((e.get("attributes", {}).get("device_class") for e in ctx.catalog if str(e.get("entity_id")) == bad), None)
    cands = [str(e.get("entity_id")) for e in ctx.catalog
             if _fresh(e) and _domain_of(str(e.get("entity_id"))) == _domain_of(bad)
             and (bad_dev is None or e.get("attributes", {}).get("device_class") == bad_dev)]
    cands = sorted(cands, key=lambda c: -_sim(c, bad))
    if not cands:
        raise ClarificationNeeded(Gap(slot=f"entity:{ctx.issue.node}",
                                      question=f"触发设备 {bad} 长期无数据（STALE），换哪个设备？",
                                      options=[{"label": f"{i + 1}. {c}", "value": c}
                                               for i, c in enumerate([str(e.get('entity_id')) for e in ctx.catalog][:5])],
                                      priority=9, kind="entity", reason="build: STALE_ENTITY"))
    if len(cands) > 1 and _sim(cands[0], bad) - _sim(cands[1], bad) < 0.1:
        raise ClarificationNeeded(Gap(slot=f"entity:{ctx.issue.node}",
                                      question=f"{bad} 失效了，换哪个设备？",
                                      options=[{"label": f"{i + 1}. {c}", "value": c} for i, c in enumerate(cands[:5])],
                                      priority=9, kind="entity", reason="build: STALE_ENTITY"))
    new = cands[0]
    txt = json.dumps(ir, ensure_ascii=False).replace(bad, new)
    ir.clear()
    ir.update(json.loads(txt))
    return FixOutcome(True, f"STALE 设备 {bad} → 替代 {new}")


def fix_spec_parse(ctx: FixContext) -> FixOutcome:
    sess = ctx.session
    if sess.spec_source == SpecSource.LLM:
        sess.spec_source = SpecSource.TEMPLATE
        sess.spec = render_spec(sess.draft_graph or ctx.ir)
        return FixOutcome(True, "LLM 生成的 AF-Spec 语法错误 → 降级为确定性模板重渲染")
    if sess.spec_source == SpecSource.TEMPLATE and sess.draft_graph:
        sess.spec_source = SpecSource.MINIMAL
        sess.spec = render_spec(sess.draft_graph, minimal=True)
        return FixOutcome(True, "模板 spec 语法错误 → 降级 minimal 模板重渲染")
    return FixOutcome(False, "已是最小模板，无法继续降级")


def _repair_condition_types(ctx: FixContext) -> FixOutcome:
    return fix_numeric_type(ctx)


def fix_sim_issue(ctx: FixContext) -> FixOutcome:
    """仿真不符合预期：①条件类型 ②边名合法性 ③仿真脚本补应答 ④追问人。"""
    out = _repair_condition_types(ctx)
    if out.applied:
        ctx.issue.code = ErrorCode.MISSING_NUMERIC_TYPE
        return FixOutcome(True, out.description + "（仿真分支未走到的根因：字符串比较）")
    out = fix_edge_labels(ctx)
    if out.applied:
        return out
    case = ctx.issue.case or "main"
    if case in ("main", "decline", "vague", "cancel"):
        answer = {"main": "好", "decline": "不用", "vague": "再说吧", "cancel": "取消"}[case]
        room = ""
        for n in ctx.ir.get("nodes", []):
            if n.get("kind") == "ask":
                room = n.get("room") or ""
        ctx.session.sim_overrides.setdefault(case, []).append({"answer": answer, "room": room})
        return FixOutcome(False, f"为用例 {case} 注入应答事件（只改仿真脚本，不动 IR）", touched_ir=False)
    raise ClarificationNeeded(Gap(slot="condition_review",
                                  question="仿真没走到预期分支，条件或阈值是不是写反了？（例：把 27 改成 26 / 把'高于'改成'低于'）",
                                  options=[], priority=6, kind="text", reason="simulate: 分支不符"))


FIXERS: dict = {
    ErrorCode.SPEC_PARSE_ERROR: fix_spec_parse,
    ErrorCode.ENTITY_NOT_FOUND: fix_entity_not_found,
    ErrorCode.STALE_ENTITY: fix_stale_entity,
    ErrorCode.MISSING_TIMEOUT_OR_DEFAULT: fix_missing_timeout,
    ErrorCode.MISSING_CANARY: fix_missing_canary,
    ErrorCode.MISSING_NUMERIC_TYPE: fix_numeric_type,
    ErrorCode.MISSING_ON_ERROR: fix_missing_on_error,
    ErrorCode.DISCONNECTED_NODE: fix_disconnected,
    ErrorCode.CYCLE: fix_cycle,
    ErrorCode.WAIT_EDGE_MISUSE: fix_wait_edge,
    ErrorCode.SIM_BRANCH_MISMATCH: fix_sim_issue,
    ErrorCode.EXPECT_FAILED: fix_sim_issue,
    ErrorCode.UNKNOWN: lambda ctx: FixOutcome(False, "未知错误，不做自动修正"),
}


# =====================================================================
# 12. 仿真计划（多用例 + 对抗事件值）
# =====================================================================
def _probe_number(c: float, op: str, satisfy: bool) -> str:
    """数值满足 op、但字典序与数值序相反的对抗值 → 专门暴露漏写 type:numeric。"""
    if not satisfy:
        return str(int(c)) if float(c).is_integer() else str(c)
    digits = len(str(int(abs(c)))) if float(c).is_integer() else len(str(c).split(".")[0])
    if op in ("gt", "ge"):
        return str(int(c) + 10 ** (digits + 1))
    return str(max(0, 10 ** max(digits - 1, 1) - 1))


def _ent(var: str | None) -> str:
    return (var or "").split("entity.", 1)[-1]


def _probe_expr(expr: Any, states: dict, satisfy: bool) -> None:
    if not isinstance(expr, Mapping):
        return
    op = expr.get("op")
    args = expr.get("args") or []
    if op == "and":
        for i, a in enumerate(args):
            _probe_expr(a, states, satisfy if satisfy else (i != 0))
        return
    if op == "or":
        for i, a in enumerate(args):
            _probe_expr(a, states, (i == 0) if satisfy else False)
        return
    if op == "not":
        if args:
            _probe_expr(args[0], states, not satisfy)
        return
    if op in ("is_on", "is_off"):
        want_on = (op == "is_on") == satisfy
        states[_ent((expr.get("value") or {}).get("var"))] = "on" if want_on else "off"
        return
    left, right = expr.get("left") or {}, expr.get("right") or {}
    var = _ent(left.get("var"))
    if not var:
        return
    c = right.get("const")
    if op in ("gt", "lt", "ge", "le") and isinstance(c, (int, float)):
        states[var] = _probe_number(float(c), op, satisfy)
    elif op in ("eq", "ne", "contains"):
        states[var] = str(c) if satisfy else f"{c}__other__"


def derive_events(ir: Mapping[str, Any], satisfy: bool = True) -> list:
    states: dict = {}
    for n in ir.get("nodes", []):
        if n.get("kind") == "on":
            t = n.get("trigger") or {}
            if t.get("type") == "state":
                states[str(t.get("entity_id"))] = str(t.get("to") or "on")
            elif t.get("type") == "for":
                states[str(t.get("entity_id"))] = str(t.get("state") or "on")
        elif n.get("kind") == "if":
            _probe_expr(n.get("condition"), states, satisfy)
    return [{"entity_id": k, "state": v} for k, v in states.items() if k]


def build_sim_plan(ir: Mapping[str, Any], *, max_cases: int = 6, overrides: Mapping | None = None) -> list:
    overrides = overrides or {}
    do_ids = [n["id"] for n in ir.get("nodes", []) if n.get("kind") == "do"]
    asks = [n for n in ir.get("nodes", []) if n.get("kind") == "ask"]
    has_cond = any(n.get("kind") == "if" for n in ir.get("nodes", []))
    base = derive_events(ir, satisfy=True)
    cases: list = []
    if asks:
        room = asks[0].get("room") or ""
        cases.append(SimCase("main", base + [{"answer": "好", "room": room}], expect_reach=do_ids,
                             check_engine_expect=True, note="肯定分支"))
        cases.append(SimCase("decline", base + [{"answer": "不用", "room": room}], expect_not_reach=do_ids,
                             note="否定分支"))
        cases.append(SimCase("vague", base + [{"answer": "再说吧", "room": room}], expect_not_reach=do_ids,
                             note="含糊 → default"))
        cases.append(SimCase("timeout", list(base), expect_not_reach=do_ids, note="超时 → on_timeout"))
        cases.append(SimCase("cancel", base + [{"answer": "取消", "room": room}], expect_not_reach=do_ids,
                             note="取消 → on_cancel"))
    else:
        cases.append(SimCase("main", list(base), expect_reach=do_ids, check_engine_expect=True, note="主路径"))
    if has_cond:
        cases.append(SimCase("guard_off", derive_events(ir, satisfy=False), expect_not_reach=do_ids,
                             note="条件不满足"))
    for c in cases:
        c.events = list(c.events) + list(overrides.get(c.name, []))
    return cases[:max_cases]


def check_cases(plan: Sequence[SimCase], results: Sequence[Mapping[str, Any]]) -> list:
    issues: list = []
    for case, res in zip(plan, results):
        trace = list(res.get("trace") or [])
        if case.check_engine_expect and res.get("expect_passed") is False:
            issues.append(BuildIssue(ErrorCode.EXPECT_FAILED, None,
                                     f"用例 {case.name}：expect 断言未通过，final_state={res.get('final_state')}",
                                     case=case.name, raw=res))
        for nid in case.expect_reach:
            if nid not in trace:
                issues.append(BuildIssue(ErrorCode.SIM_BRANCH_MISMATCH, nid,
                                         f"用例 {case.name}：预期到达 {nid}，实际 trace={trace}", case=case.name, raw=res))
        for nid in case.expect_not_reach:
            if nid in trace:
                issues.append(BuildIssue(ErrorCode.SIM_BRANCH_MISMATCH, nid,
                                         f"用例 {case.name}：不应到达 {nid}，实际 trace={trace}", case=case.name, raw=res))
        if not res.get("ok", True):
            issues.append(_issue_from(res.get("error") or "simulate failed", res))
    return issues


# =====================================================================
# 13. 质量评分
# =====================================================================
WEIGHTS = {"complexity": 0.15, "robustness": 0.25, "maintainability": 0.15,
           "safety": 0.25, "coverage": 0.20}

SUGGESTION = {
    "on_error": "为 {node} 补一条 `on_error` 兜底边到结束节点",
    "on_timeout": "为 {node} 补 `on_timeout`（以及 `default`）出边，避免问了没下文",
    "numeric": "数值比较给 var 补 `\"type\":\"numeric\"`，否则按字符串比较（\"9\">\"10\"）",
    "canary": "{node} 是 L2/L3 动作，补 `requires_confirm` + canary 灰度",
    "terminal": "图里没有终结 pass 节点，补一个统一出口",
    "filter": "{node} 触发没有 `to`/`for` 过滤，容易被抖动刷屏",
    "parallel": "parallel/queued 叠加 L2/L3 动作有重入风险，建议改 restart",
    "complexity": "图偏复杂（{n} 个节点），考虑拆成两个自动化或用 var 归并分支",
    "names": "节点命名率低，给每个节点补 `name`，便于 trace 阅读和日后维护",
    "acceptance": "补 `meta.acceptance` 与 `expect` 断言，让验收可回归",
    "copy": "多个动作参数高度雷同，建议抽取公共 var 或复用 scene",
    "coverage": "以下分支未被完整覆盖：{edges}（`on_error` 需故障注入，af_simulate 暂不支持）",
    "untested": "尚未仿真，覆盖率计 0；先跑一遍 simulate",
    "wildcard": "动作参数出现通配实体（* / all），误触发面过大",
    "security": "触发落在锁/安防实体上，请确认这是预期行为",
}


class QualityScorer:
    def __init__(self, weights: Mapping[str, float] | None = None):
        self.weights = dict(weights or WEIGHTS)

    # ---- 各维度 ----
    def _complexity(self, ir):
        nodes = ir.get("nodes", [])
        edges = ir.get("edges", [])
        n_ask = sum(1 for n in nodes if n.get("kind") == "ask")
        n_act = sum(1 for n in nodes if n.get("kind") == "do")
        depth = 0
        for n in nodes:
            if n.get("kind") == "if":
                depth = max(depth, self._cond_depth(n.get("condition")))
        s = 10.0
        s -= max(0, len(nodes) - 6) * 0.5
        s -= max(0, len(edges) - int(len(nodes) * 1.6)) * 0.5
        s -= max(0, n_ask - 1) * 1.5
        s -= max(0, n_act - 3) * 0.5
        s -= max(0, depth - 3) * 1.0
        viol = []
        if s < 8:
            viol.append(("complexity", f"nodes={len(nodes)}, edges={len(edges)}, ask={n_ask}, depth={depth}"))
        return clamp(s), viol

    @staticmethod
    def _cond_depth(expr) -> int:
        if not isinstance(expr, Mapping):
            return 0
        args = expr.get("args") or []
        return 1 + max((QualityScorer._cond_depth(a) for a in args), default=0)

    def _robustness(self, ir, expect_ok: bool | None):
        viol = []
        s = 10.0
        outs = defaultdict(list)
        for e in ir.get("edges", []):
            outs[e["from"]].append(e.get("when"))
        kinds = {n["id"]: n.get("kind") for n in ir.get("nodes", [])}
        for n in ir.get("nodes", []):
            nid, kind = n["id"], n.get("kind")
            if kind == "do" and "on_error" not in outs.get(nid, []):
                s -= 2.0
                viol.append(("on_error", nid))
            if kind == "ask":
                if "on_timeout" not in outs.get(nid, []):
                    s -= 3.0
                    viol.append(("on_timeout", nid))
                if "default" not in outs.get(nid, []):
                    s -= 1.0
            if kind == "if":
                for node, _p in _iter_expr_nodes(n.get("condition")):
                    if node.get("op") in ("gt", "lt", "ge", "le"):
                        for side in ("left", "right"):
                            sd = node.get(side)
                            if isinstance(sd, Mapping) and "var" in sd and sd.get("type") != "numeric":
                                s -= 1.5
                                viol.append(("numeric", nid))
            if kind == "on":
                t = n.get("trigger") or {}
                if t.get("type") == "state" and not (t.get("to") or t.get("for") or t.get("duration")):
                    s -= 1.0
                    viol.append(("filter", nid))
        if not any(n.get("kind") == "pass" for n in ir.get("nodes", [])):
            s -= 2.0
            viol.append(("terminal", "-"))
        if ir.get("mode") in ("parallel", "queued") and any(
                risk_of(n.get("action", "")) != Risk.L1 for n in ir.get("nodes", []) if n.get("kind") == "do"):
            s -= 1.5
            viol.append(("parallel", "-"))
        if expect_ok is False:
            s -= 1.0
        return clamp(s), viol

    def _maintainability(self, ir):
        nodes = ir.get("nodes", [])
        named = [n for n in nodes if n.get("name")]
        ratio = len(named) / max(1, len(nodes))
        s = 4.0 + 3.0 * ratio
        viol = []
        if ratio >= 0.99 and all(len(str(n.get("name"))) >= 2 for n in named):
            s += 1.0
        else:
            viol.append(("names", f"命名率 {int(ratio * 100)}%"))
        if (ir.get("meta") or {}).get("acceptance") and ir.get("expect"):
            s += 1.0
        else:
            viol.append(("acceptance", "-"))
        params = [json.dumps(n.get("params") or {}, sort_keys=True, ensure_ascii=False)
                  for n in nodes if n.get("kind") == "do"]
        if len(params) >= 2 and len(set(params)) == 1:
            s -= 1.0
            viol.append(("copy", "-"))
        return clamp(s), viol

    def _safety(self, ir):
        s = 10.0
        viol = []
        asks = sum(1 for n in ir.get("nodes", []) if n.get("kind") == "ask")
        for n in ir.get("nodes", []):
            if n.get("kind") != "do":
                continue
            risk = risk_of(n.get("action", ""))
            guarded = n.get("requires_confirm") and (n.get("canary") or (ir.get("meta") or {}).get("canary"))
            if risk == Risk.L2 and not guarded:
                s -= 4.0
                viol.append(("canary", n["id"]))
            if risk == Risk.L3:
                if not guarded:
                    s -= 6.0
                    viol.append(("canary", n["id"]))
                if not asks:
                    s -= 2.0
            for v in iter_strings(n.get("params") or {}):
                if v in ("*", "all"):
                    s -= 2.0
                    viol.append(("wildcard", n["id"]))
        for n in ir.get("nodes", []):
            if n.get("kind") == "on":
                t = n.get("trigger") or {}
                eid = str(t.get("entity_id") or "")
                if eid.startswith(("lock.", "alarm_control_panel.")):
                    s -= 1.5
                    viol.append(("security", n["id"]))
        return clamp(s), viol

    def _coverage(self, ir, traces):
        edges = ir.get("edges", [])
        viol = []
        if traces is None:
            return 0.0, [("untested", "-")]
        pairs = defaultdict(int)
        for e in edges:
            pairs[(e["from"], e["to"])] += 1
        covered = set()
        for tr in traces or []:
            for a, b in zip(tr, tr[1:]):
                covered.add((a, b))
        hit, partial = 0.0, []
        for e in edges:
            key = (e["from"], e["to"])
            mult = max(1, pairs[key])
            if key in covered:
                hit += 1.0 / mult
                if mult > 1:
                    partial.append(f"{e['from']}→{e['to']}({e.get('when')})")
            else:
                partial.append(f"{e['from']}→{e['to']}({e.get('when')})")
        score = 10.0 * hit / max(1, len(edges))
        if partial:
            viol.append(("coverage", "、".join(sorted(set(partial)))))
        return clamp(score), viol

    # ---- 汇总 ----
    def score_ir(self, ir: Mapping[str, Any], *, traces: Sequence[Sequence[str]] | None = None,
                 expect_ok: bool | None = None) -> QualityReport:
        dims, viol = {}, {}
        dims["complexity"], viol["complexity"] = self._complexity(ir)
        dims["robustness"], viol["robustness"] = self._robustness(ir, expect_ok)
        dims["maintainability"], viol["maintainability"] = self._maintainability(ir)
        dims["safety"], viol["safety"] = self._safety(ir)
        dims["coverage"], viol["coverage"] = self._coverage(ir, traces)
        if traces is not None and expect_ok:
            dims["robustness"] = clamp(dims["robustness"] + 0.5)
        total = sum(dims[k] * self.weights.get(k, 0.0) for k in dims)
        grade = "A" if total >= 8.5 else "B" if total >= 7 else "C" if total >= 5.5 else "D"
        sugg = []
        for dim, items in viol.items():
            for code, detail in items:
                tpl = SUGGESTION.get(code)
                if tpl:
                    sugg.append(f"[{dim}] " + tpl.format(node=detail if detail != "-" else "相关节点",
                                                         n=detail, edges=detail))
        return QualityReport(dims=dims, weights=self.weights, total=round(total, 2),
                             grade=grade, suggestions=sugg, violations=viol)


# =====================================================================
# 14. 会话存储（单用户内网，进程内即可）
# =====================================================================
class SessionStore:
    def __init__(self):
        self._s: dict = {}

    def get_or_create(self, sid: str | None = None) -> ComposeSession:
        sid = sid or "cs_" + uuid.uuid4().hex[:8]
        if sid not in self._s:
            self._s[sid] = ComposeSession(session_id=sid)
        return self._s[sid]

    def get(self, sid: str) -> ComposeSession:
        if sid not in self._s:
            raise OrchestratorError(f"会话不存在: {sid}")
        return self._s[sid]


# =====================================================================
# 15. 编排器
# =====================================================================
class Orchestrator:
    """NL→自动化的统一编排器。任何 agent 都可以用它完成标准流程。"""

    def __init__(self, mcp_client: MCPClient, llm_client: LLMClient | None = None, *,
                 max_fix_attempts: int = 3, max_sim_cases: int = 6,
                 max_questions_per_turn: int = 3,
                 auto_accept_unique_suggestion: bool = True,
                 session_store: SessionStore | None = None):
        self.mcp = mcp_client
        self.llm = llm_client
        self.parser = IntentParser(llm_client)
        self.scorer = QualityScorer()
        self.sessions = session_store or SessionStore()
        self.max_fix_attempts = max_fix_attempts
        self.max_sim_cases = max_sim_cases
        self.max_questions_per_turn = max_questions_per_turn
        self.auto_accept_unique_suggestion = auto_accept_unique_suggestion

    # ---------- MCP 薄封装（含 approve 硬护栏） ----------
    def _call(self, tool: str, **kw: Any) -> dict:
        if "approve" in str(tool).lower():
            raise OrchestratorError("安全红线：编排器绝不调用 approve，批准只能由人在 WebUI 完成")
        res = self.mcp.call(tool, **kw)
        return dict(res) if isinstance(res, Mapping) else {"ok": bool(res)}

    def _call_safe(self, tool: str, **kw: Any) -> dict:
        try:
            return self._call(tool, **kw)
        except Exception as exc:                              # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def _catalog(self, session: ComposeSession) -> list:
        if session.catalog is None:
            res = self._call_safe("af_list_entities")
            ents = list(res.get("entities") or [])
            if not ents:
                raw = self._call_safe("af_catalog")
                ents = list(raw.get("entities") or raw.get("catalog") or
                            ([{"entity_id": k, **(v if isinstance(v, Mapping) else {"state": v})}
                              for k, v in raw.items() if entity_like(str(k))]))
            session.catalog = ents
        return session.catalog

    def _in_catalog(self, eid: str, catalog: Sequence[Mapping[str, Any]]) -> bool:
        return any(str(e.get("entity_id")) == eid for e in catalog)

    # ---------- 对外 API ----------
    def compose(self, user_message: str, conversation_history: list | None = None, *,
                session_id: str | None = None, auto_commit: bool = True) -> ComposeResult:
        """主入口：用户自然语言 → 待批准自动化。"""
        session = self.sessions.get_or_create(session_id)
        session.auto_commit = auto_commit
        for h in (conversation_history or []):
            if isinstance(h, Mapping):
                session.history.append(dict(h))
        session.add_turn("user", user_message)

        health = self._call_safe("af_health")
        if not health.get("ok", True) or health.get("store_ok") is False:
            return self._result(session, Status.FAILED, "AF 存储不可用（store_ok=false），先修 store 再编排")

        catalog = self._catalog(session)
        intent = self.parser.parse(user_message, catalog, session.history)
        return self._drive(session, intent)

    def continue_compose(self, session_id: str, user_answer: str) -> ComposeResult:
        """用户回答追问 / 选推荐 / 确认后继续编排。"""
        session = self.sessions.get(session_id)
        session.add_turn("user", user_answer)

        if session.phase == Phase.CONFIRM:
            if _is_yes(user_answer):
                return self.commit(session_id)
            intent = self.parser.parse(user_answer, self._catalog(session), session.history)
            merge_intent(session.draft, intent, overwrite=False)
            return self._advance(session)

        if session.phase == Phase.RECOMMEND:
            pick = self._pick_recommendation(session, user_answer)
            if pick is None:
                return self._recommend_result(session, "没听懂选哪个，回复 1/2/3，或者说说你想要的效果。")
            merge_intent(session.draft, pick.draft_hint)
            session.draft.assume(f"套用推荐方案「{pick.title}」，细节可继续改")
            return self._advance(session)

        gaps = list(session.pending_gaps)
        pairs = parse_answers(gaps, user_answer)
        needs_nlu = any(g.kind == "text" for g in gaps) or not gaps
        if needs_nlu:
            catalog = self._catalog(session)
            intent = self.parser.parse(user_answer, catalog, session.history)
            if intent.get("intent_kind") == "build":
                merge_intent(session.draft, intent)
        for gap, value in pairs:
            apply_answer(session.draft, gap, value)
        _apply_defaults(session.draft)
        return self._advance(session)

    def recommend(self, area: str | None = None, session_id: str | None = None) -> ComposeResult:
        session = self.sessions.get_or_create(session_id)
        if area:
            session.draft.area = area
        return self._recommend_result(session, None)

    def commit(self, session_id: str) -> ComposeResult:
        """提交到 PendingOp 待批队列 —— 这不是批准。"""
        session = self.sessions.get(session_id)
        if not session.ir:
            return self._result(session, Status.FAILED, "还没有可提交的 IR")
        out = self._call("af_save", ir=session.ir, name=session.draft.name)
        session.pending_id = out.get("id") or session.ir.get("id")
        session.phase = Phase.DONE
        msg = (f"已生成《{session.draft.name}》并入待批队列（id={session.pending_id}）。"
               f"请在 WebUI 审阅并批准后才会真正生效——agent 不能自己批准。")
        return self._result(session, Status.SAVED, msg)

    def repair_ir(self, ir: dict, *, stage: str = "build", max_attempts: int | None = None) -> RepairReport:
        """免编排修复外部 IR（af_import 进来的、或 agent 自己拼的图）。"""
        session = self.sessions.get_or_create()
        session.ir = ir
        session.draft_graph = ir
        raw, issues = self._fix_loop(session, stage, lambda: self._call("af_build", ir=ir),
                                     max_attempts=max_attempts)
        return RepairReport(ir=ir, issues=issues, fix_log=session.fix_log, fixed=not issues)

    def report_outcome(self, automation_id: str, outcome: str, note: str = "") -> None:
        self._call_safe("af_experience", automation_id=automation_id, outcome=outcome, note=note)

    def observe(self, automation_id: str, duration: int = 60) -> dict:
        return self._call_safe("af_live", id=automation_id, duration=duration)

    # ---------- 内部流程 ----------
    def _drive(self, session: ComposeSession, intent: Mapping[str, Any]) -> ComposeResult:
        vague = (intent.get("intent_kind") == "recommend"
                 or (not _as_list(intent.get("actions") or intent.get("action"))
                     and not _as_list(intent.get("triggers") or intent.get("trigger"))))
        if vague:
            return self._recommend_result(session, None)
        merge_intent(session.draft, intent)
        return self._advance(session)

    def _advance(self, session: ComposeSession) -> ComposeResult:
        catalog = self._catalog(session)
        gaps = detect_gaps(session.draft, catalog)[: self.max_questions_per_turn]
        if gaps:
            return self._clarify_result(session, gaps)
        res = self._resolve_entities(session)
        if res:
            return self._clarify_result(session, res[: self.max_questions_per_turn])
        return self._pipeline(session)

    def _resolve_entities(self, session: ComposeSession) -> list:
        """把 @ref 换成真实 entity_id。找不到/多候选 → 返回追问列表（禁止猜）。"""
        draft, catalog = session.draft, self._catalog(session)
        gaps = []
        for key, ref in list(draft.refs.items()):
            if ref.entity_id and self._in_catalog(ref.entity_id, catalog):
                continue
            if ref.entity_id and not self._in_catalog(ref.entity_id, catalog):
                self._call_safe("af_refresh_catalog")
                session.catalog = None
                catalog = self._catalog(session)
                if self._in_catalog(ref.entity_id, catalog):
                    continue
                ref.candidates = [ref.entity_id]
                ref.entity_id = None
            if not ref.entity_id:
                kw = {"name": ref.name}
                if ref.area:
                    kw["area"] = ref.area
                out = self._call_safe("af_resolve_entity", **kw)
                eid = out.get("entity_id") if out.get("ok") else None
                sugg = [s for s in (out.get("suggestions") or []) if self._in_catalog(s, catalog)] or \
                       list(out.get("suggestions") or [])
                if eid and self._in_catalog(eid, catalog):
                    ref.entity_id = eid
                elif len(sugg) == 1 and self.auto_accept_unique_suggestion:
                    ref.entity_id = sugg[0]
                    ref.assumed = True
                    draft.assume(f"「{ref.name}」自动取唯一候选 {sugg[0]}")
                elif sugg:
                    ref.candidates = sugg
                else:
                    guess = [str(e.get("entity_id")) for e in catalog
                             if _sim(_name_of(e), ref.name) >= 0.5][:2]
                    gaps.append(Gap(slot=f"entity:{key}",
                                    question=f"没找到叫「{ref.name}」的设备，你指的是哪个？",
                                    options=[{"label": f"{i + 1}. {c}", "value": c} for i, c in enumerate(guess)],
                                    priority=9, kind="entity", reason="设备不存在",
                                    apply=lambda v, r=ref: setattr(r, "entity_id", v)))
                    continue
            if ref.entity_id and not self._in_catalog(ref.entity_id, catalog):
                ref.candidates = [ref.entity_id]
                ref.entity_id = None
            if ref.candidates and not ref.entity_id:
                gaps.append(Gap(slot=f"entity:{key}",
                                question=f"「{ref.name}」有多个候选设备，用哪个？",
                                options=[{"label": f"{i + 1}. {c}", "value": c}
                                         for i, c in enumerate(ref.candidates)],
                                priority=9, kind="entity", reason="设备歧义",
                                apply=lambda v, r=ref: setattr(r, "entity_id", v)))
        # 字面量 entity_id 也要过目录（坑 #1：禁止凭记忆编造）
        for eid in find_entities({"t": draft.triggers, "c": draft.conditions, "a": draft.actions, "e": draft.expect}):
            if not self._in_catalog(eid, catalog):
                gaps.append(Gap(slot=f"literal:{eid}",
                                question=f"你提到的 {eid} 不在设备目录里，是不是写错了？",
                                options=[{"label": f"{i + 1}. {c}", "value": c}
                                         for i, c in enumerate([str(e.get('entity_id')) for e in catalog][:5])],
                                priority=9, kind="entity", reason="编造的 entity_id"))
        return gaps

    # ---- Compose Pipeline ----
    def _pipeline(self, session: ComposeSession) -> ComposeResult:
        try:
            return self._pipeline_inner(session)
        except ClarificationNeeded as exc:
            return self._clarify_result(session, [exc.gap])

    def _pipeline_inner(self, session: ComposeSession) -> ComposeResult:
        draft = session.draft
        session.phase = Phase.DRAFT
        graph = build_graph(draft)
        session.draft_graph = graph
        session.spec_source = SpecSource.TEMPLATE
        session.spec = render_spec(graph)

        # Step 4: compile
        session.phase = Phase.COMPILE
        raw, issues = self._fix_loop(session, "compile", lambda: self._call("af_compile_spec", spec=session.spec))
        if issues:
            return self._fail(session, "AF-Spec 解析失败", issues)
        session.ir = raw.get("ir") or graph

        # Step 5: build（安全闸 + auto-fix）
        session.phase = Phase.BUILD
        build_out, issues = self._fix_loop(session, "build", lambda: self._call("af_build", ir=session.ir))
        session.build_out = build_out
        if issues:
            return self._fail(session, "安全闸校验未通过", issues)

        # Step 6: simulate（多用例 + auto-fix）
        session.phase = Phase.SIMULATE
        ok, sim = self._sim_loop(session)
        session.sim = sim
        if not ok:
            return self._fail(session, "仿真验证未通过", sim.get("issues", []))

        # Step 7: 评分
        session.phase = Phase.SCORE
        session.quality = self.scorer.score_ir(session.ir, traces=sim["traces"],
                                               expect_ok=sim.get("expect_passed"))
        session.nl_description = (build_out or {}).get("nl_description") or render_nl(session.ir, draft.labels())

        # Step 8/9: 确认 / 提交待批
        if session.auto_commit:
            return self.commit(session.session_id)
        session.phase = Phase.CONFIRM
        return self._result(session, Status.AWAITING_CONFIRM,
                            f"编排完成，请确认：{session.nl_description}\n回复「确认」我就把它送进待批队列（仍需你在 WebUI 批准）。")

    def _fix_loop(self, session: ComposeSession, stage: str, run: Callable[[], Mapping[str, Any]],
                  max_attempts: int | None = None):
        budget = max_attempts or self.max_fix_attempts
        attempts = 0
        raw, issues = {}, []
        while True:
            raw = run() or {}
            issues = normalize_output(stage, raw)
            if not issues:
                return raw, []
            attempts += 1
            session.attempts[stage] = attempts
            if attempts >= budget:
                return raw, issues
            applied = 0
            for issue in list(issues):
                fixer = FIXERS.get(issue.code)
                if not fixer:
                    continue
                outcome = fixer(FixContext(self, session, issue, session.ir or {}))
                session.fix_log.append(FixRecord(stage=stage, code=issue.code.value, node=issue.node,
                                                 action=outcome.description, detail=issue.message))
                if outcome.needs_user:
                    raise ClarificationNeeded(Gap(slot="manual", question=issue.message, priority=7, kind="text"))
                if outcome.applied:
                    applied += 1
            # applied==0 时不提前退出：继续重试到预算耗尽（处理间歇性失败）
            if session.ir:
                session.spec = render_spec(session.ir)

    def _sim_loop(self, session: ComposeSession):
        attempts = 0
        while True:
            plan = build_sim_plan(session.ir, max_cases=self.max_sim_cases,
                                  overrides=session.sim_overrides)
            results = [self._call("af_simulate", ir=session.ir, events=case.events) for case in plan]
            issues = check_cases(plan, results)
            sim = {
                "cases": [{"name": c.name, "note": c.note, "trace": list(r.get("trace") or []),
                           "final_state": r.get("final_state") or {},
                           "expect_passed": r.get("expect_passed")} for c, r in zip(plan, results)],
                "traces": [list(r.get("trace") or []) for r in results],
                "final_state": (results[-1].get("final_state") if results else {}) or {},
                "expect_passed": all(r.get("expect_passed", True) for r in results),
                "issues": issues,
            }
            if not issues:
                return True, sim
            attempts += 1
            session.attempts["simulate"] = attempts
            if attempts >= self.max_fix_attempts:
                return False, sim
            applied = 0
            for issue in list(issues):
                fixer = FIXERS.get(issue.code)
                if not fixer:
                    continue
                outcome = fixer(FixContext(self, session, issue, session.ir))
                session.fix_log.append(FixRecord(stage="simulate", code=issue.code.value, node=issue.node,
                                                 action=outcome.description, detail=issue.message))
                if outcome.needs_user:
                    raise ClarificationNeeded(Gap(slot="condition_review", question=issue.message,
                                                  priority=6, kind="text"))
                if outcome.applied:
                    applied += 1
            if applied == 0 and not session.sim_overrides:
                return False, sim
            if applied:
                self._call_safe("af_build", ir=session.ir)      # 改过图就重新过闸

    # ---- 结果装配 ----
    def _result(self, session: ComposeSession, status: Status, message: str, **kw) -> ComposeResult:
        return ComposeResult(
            status=status, session_id=session.session_id, message=message,
            assumptions=list(session.draft.assumptions), spec=session.spec,
            ir=session.ir, nl_description=session.nl_description,
            quality=session.quality, simulation=session.sim,
            fix_log=list(session.fix_log), pending_id=session.pending_id, **kw)

    def _clarify_result(self, session: ComposeSession, gaps: list) -> ComposeResult:
        session.phase = Phase.CLARIFY
        session.pending_gaps = list(gaps)
        session.add_turn("assistant", "；".join(g.question for g in gaps))
        return self._result(session, Status.NEED_CLARIFICATION,
                            "还差一点信息：" + "；".join(g.question for g in gaps), gaps=list(gaps))

    def _recommend_result(self, session: ComposeSession, message: str | None) -> ComposeResult:
        session.phase = Phase.RECOMMEND
        catalog = self._catalog(session)
        area = session.draft.area
        ents = [e for e in catalog if not area or area in _name_of(e) or _area_of(e) == area] or list(catalog)
        recs = build_recommendations(session.draft, ents)
        session.pending_gaps = []
        listing = "、".join(_name_of(e) for e in ents[:12])
        msg = message or (f"{area or '这个房间'}我能想到这些设备：{listing}。"
                          f"推荐 {len(recs)} 个方案，回复 1/2/3 选一个，或者直接说你想要的效果。")
        return self._result(session, Status.RECOMMENDING, msg, recommendations=recs)

    def _pick_recommendation(self, session: ComposeSession, text: str):
        catalog = self._catalog(session)
        area = session.draft.area
        ents = [e for e in catalog if not area or area in _name_of(e) or _area_of(e) == area] or list(catalog)
        recs = build_recommendations(session.draft, ents)
        t = (text or "").strip()
        if t.isdigit() and 1 <= int(t) <= len(recs):
            return recs[int(t) - 1]
        for r in recs:
            if r.title in t or r.key in t:
                return r
        best = max(recs, key=lambda r: _sim(t, r.title), default=None)
        return best if best and _sim(t, best.title) >= 0.5 else None

    def _fail(self, session: ComposeSession, message: str, issues: list) -> ComposeResult:
        session.phase = Phase.FAILED
        detail = "；".join(f"{i.code.value}@{i.node or '-'}: {i.message}" for i in issues[:4])
        return self._result(session, Status.FAILED,
                            f"{message}（已自动重试 {session.attempts} 次仍失败）：{detail}", issues=list(issues))


# =====================================================================
# 16. 回答解析（选项 / 编号 / slot=value 批量 / 自由文本）
# =====================================================================
def coerce_value(gap: Gap, text: str) -> Any:
    t = (text or "").strip()
    if gap.kind == "number":
        return _parse_number(t)
    if gap.kind == "confirm":
        return _is_yes(t)
    if gap.options:
        if t.isdigit() and 1 <= int(t) <= len(gap.options):
            return gap.options[int(t) - 1]["value"]
        for opt in gap.options:
            label = str(opt["label"])
            if t == label or t in label or label in t:
                return opt["value"]
        best = max(gap.options, key=lambda o: _sim(t, str(o["label"])))
        if _sim(t, str(best["label"])) >= 0.5:
            return best["value"]
    return t


def parse_answers(gaps: Sequence[Gap], text: str) -> list:
    """支持 '2' / 'slot=value; slot=value' / 自由文本（应用到最高优先级缺口）。"""
    text = (text or "").strip()
    if not gaps:
        return []
    kv = {}
    for chunk in re.split(r"[;；\|\n]+", text):
        m = re.match(r"^\s*([\w:@\.\-]+)\s*[=:：]\s*(.+)$", chunk.strip())
        if m:
            kv[m.group(1).lower()] = m.group(2).strip()
    out = []
    for gap in gaps:
        if gap.slot.lower() in kv:
            out.append((gap, coerce_value(gap, kv.pop(gap.slot.lower()))))
    if out:
        return out
    return [(gaps[0], coerce_value(gaps[0], text))]


def apply_answer(draft: AutomationDraft, gap: Gap, value: Any) -> None:
    if gap.apply is not None:
        gap.apply(value)
        return
    if gap.slot == "area":
        draft.area = str(value)
    elif gap.slot.startswith("literal:"):
        draft.notes.append(f"用户澄清字面量实体 {gap.slot.split(':', 1)[1]} → {value}")

