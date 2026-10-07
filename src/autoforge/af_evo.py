"""AutoForge · 自进化提案生成器（af_evo）。

扫描存在结构缺陷 / 低健康度的自动化，按五种进化策略生成改进提案
（``EvoProposal``），经 sim 验证后送入 ``ProposalManager`` 统一审批队列::

    合并冗余 merge_redundant   触发相似 + 动作相同      → 建议合并
    拆分过大 split_oversized   do 节点数 > 5            → 建议拆分
    调整触发 adjust_trigger    干预率 > 30%             → 收窄触发 / 加 ask
    补兜底   add_fallback      do 节点缺 on_error       → 补兜底
    升档     promote_shadow    shadow 连续命中 >= 3 次  → 建议升 auto

公开接口（与提单契约逐字对齐）::

    EvoScanner.scan() -> list[EvoProposal]
    EvoProposal: {proposal_id, automation_id, strategy, reason,
                  suggested_ir, confidence, status}

设计要点
--------
* **零内部依赖**：只用标准库。``health_engine / proposal_manager / graph /
  executor_stats`` 全部由装配层注入，本模块只用 duck typing 读它们的公开只读
  接口，不 import 任何 autoforge 内部实现（连 af_conf 的常量都用镜像，与
  af_health 的做法一致），也不改任何现有文件。
* **fail-open 扫描 / fail-closed 放行**：任一数据源抛异常、字段缺失、口径不明
  → 该条检测跳过并进 ``warnings``，``scan()`` 永不向上抛，健康度/进化器不许成为
  单点；反过来 sim 缺失、异常、结果无法解读**一律不入队**，机器提案不许未经
  验证就占住审批席位。
* **提案只降不升**：送进 ProposalManager 的 conf 被 ``queue_conf_max``（默认
  0.59 < SHADOW_LOW）封顶，保证落地为 ``pending`` 等人批准，绝不因高 conf 触发
  自动部署写设备（写设备权永远留在人手里）。
* **幂等**：指纹去重（策略 + 目标 id + 提案 IR 的规范形），重复扫描不产生重复
  提案，不重复占审批位。
* **无 IO**：不读写任何文件（"不碰现网配置"），状态仅驻内存。
"""

from __future__ import annotations

import inspect
import math
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

from .af_ir import check_param_depth

__all__ = [
    "AUTO_MIN", "SHADOW_LOW",
    "EvoStrategy", "EvoStatus", "EvoProposal", "EvoPolicy", "SimOutcome",
    "GraphView", "ProposalSink",
    "trigger_similarity", "action_signature", "default_ir_builder",
    "run_sim", "EvoScanner",
]


# ======================================================================
#  阈值镜像（与 af_conf.decision_for / G2 编译期闸门同一套口径）
# ======================================================================

#: af_conf.AUTO_MIN 的镜像常量。改 af_conf 必须同步这里（同 af_health 的做法）。
AUTO_MIN = 0.85
#: af_conf.SHADOW_LOW 的镜像常量。
SHADOW_LOW = 0.60

_MISSING: Any = object()


# ======================================================================
#  小工具（全部 fail-safe：属性缺失给默认值，属性自身抛错则向上冒泡，
#  由检测器的 per-automation try/except 变成 warning —— 真错误必须可见）
# ======================================================================

def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _clamp01(x: float) -> float:
    return _clamp(x, 0.0, 1.0)


def _to_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _get(obj: Any, name: str, default: Any = None) -> Any:
    """读 ``obj.name``（Mapping 优先按键取）；属性自身抛错向上冒泡。"""
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    try:
        return getattr(obj, name)
    except AttributeError:
        pass
    to_json = getattr(obj, "to_json", None)
    if callable(to_json):
        try:
            data = to_json()
        except Exception:
            data = None
        if isinstance(data, Mapping) and name in data:
            return data[name]
    return default


def _as_mapping(obj: Any) -> dict[str, Any]:
    """把 automation / node / trigger 归一成可编辑的 dict（尽力而为）。"""
    if obj is None:
        return {}
    if isinstance(obj, Mapping):
        return dict(obj)
    to_json = getattr(obj, "to_json", None)
    if callable(to_json):
        try:
            data = to_json()
        except Exception:
            data = None
        if isinstance(data, Mapping):
            return dict(data)
    out = getattr(obj, "__dict__", None)
    if isinstance(out, Mapping):
        return {k: v for k, v in out.items() if not str(k).startswith("_")}
    return {"value": obj}


def _as_seq(value: Any) -> list[Any]:
    """归一成列表。注意：单个 Mapping 会被当成「id→节点」映射取 values。"""
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        return [value]
    if isinstance(value, Mapping):
        return list(value.values())
    if isinstance(value, Iterable):
        try:
            return list(value)
        except Exception:
            return [value]
    return [value]


def _canon(value: Any, _depth: int = 0) -> Any:
    """可哈希的规范形，用于指纹 / 去重 / 相似度。"""
    check_param_depth(_depth, "evo._canon")
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, (int, float)):
        return round(float(value), 3)
    if isinstance(value, Mapping):
        return tuple(sorted((str(k), _canon(v, _depth + 1)) for k, v in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_canon(v, _depth + 1) for v in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted(repr(_canon(v, _depth + 1)) for v in value))
    return repr(value)


def _balanced(items: Sequence[Any], parts: int) -> list[list[Any]]:
    """把 items 尽量均分到 parts 份（用于拆分）。"""
    n = len(items)
    parts = max(1, min(parts, n or 1))
    base, extra = divmod(n, parts)
    out: list[list[Any]] = []
    pos = 0
    for k in range(parts):
        size = base + (1 if k < extra else 0)
        out.append(list(items[pos:pos + size]))
        pos += size
    return out


def _new_id() -> str:
    return uuid4().hex[:12]


# ======================================================================
#  图读取（duck typing，只读）
# ======================================================================

#: 参与触发相似度的字段（IR 的 trigger 语义面）
_TRIGGER_KEYS: tuple[str, ...] = (
    "kind", "type", "platform", "entity_id", "entity", "entity_ids",
    "event", "event_type", "topic", "state", "to", "from", "value",
    "at", "hour", "minute", "time", "weekday", "days", "zone", "offset",
    "condition", "conditions", "expr",
)
#: 不参与相似度的易变/装饰字段
_TRIGGER_IGNORE: frozenset[str] = frozenset({
    "id", "trigger_id", "node_id", "name", "alias", "description",
    "enabled", "idx", "index",
})


def _trigger_tokens(trigger: Any) -> frozenset[str]:
    d = _as_mapping(trigger)
    toks: set[str] = set()
    for key in _TRIGGER_KEYS:
        val = d.get(key, None)
        if val is None or val == "" or val == []:
            continue
        toks.add(f"{key}={_canon(val)!r}")
    for key, val in d.items():
        if key in _TRIGGER_KEYS or key in _TRIGGER_IGNORE:
            continue
        if val is None or val == "" or val == []:
            continue
        toks.add(f"{key}={_canon(val)!r}")
    return frozenset(toks)


def _token_sets(value: Any) -> list[frozenset[str]]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        return [_trigger_tokens(value)]
    if isinstance(value, (list, tuple)):
        return [_trigger_tokens(v) for v in value]
    return [_trigger_tokens(value)]


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        # 空 token 集的交集与并集都是 ∅，数学上是 0/0。取 1.0 等于宣布"两条都没有
        # 触发器的自动化完全相同"——而真实不同的两条只有 0.5。共同缺失不是证据，判 0.0；
        # 确实要把"共同缺失"当作相同的地方必须显式表达（EvoPolicy.merge_allow_empty_triggers）。
        return 0.0
    union = len(a | b)
    return (len(a & b) / union) if union else 0.0


def _max_jaccard(sa: Sequence[frozenset[str]], sb: Sequence[frozenset[str]]) -> float:
    best = 0.0
    for x in sa:
        for y in sb:
            best = max(best, _jaccard(x, y))
    return best


def trigger_similarity(a: Any, b: Any) -> float:
    """两个自动化（或触发器）的触发相似度：触发 token 的最大两两 Jaccard。

    任一侧拿不出 trigger 就是 0.0（"没有可比对的东西"），而不是 1.0。
    """
    sa, sb = _token_sets(a), _token_sets(b)
    if not sa or not sb:
        return 0.0
    return _max_jaccard(sa, sb)


def action_signature(node: Any) -> tuple:
    """do 节点的「动作签名」：动作名 + 参数 + 目标（不含 node_id 等易变字段）。"""
    d = _as_mapping(node)
    name = d.get("action", d.get("service", d.get("kind", "")))
    params = d.get("params", d.get("data", {}))
    target = d.get("entity_id", d.get("target", d.get("device_id")))
    return (_canon(name), _canon(params), _canon(target))


def _has_on_error(node: Any, key: str = "on_error") -> bool:
    val = _get(node, key, _MISSING)
    if val is _MISSING or val is None:
        return False
    if isinstance(val, (Mapping, str, list, tuple)):
        return len(val) > 0
    return True


@dataclass
class GraphView:
    """对注入 ``graph`` 的只读鸭子类型适配（af_ir.Graph / dict / 列表都收）。"""

    graph: Any = None
    warnings: list[str] = field(default_factory=list)
    _ids: dict[int, str] = field(default_factory=dict)

    def _warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def automations(self) -> list[Any]:
        g = self.graph
        if g is None:
            return []
        try:
            seq = _get(g, "automations", _MISSING)
            if seq is _MISSING or seq is None:
                seq = _get(g, "autos", _MISSING)
            if seq is not _MISSING and seq is not None:
                if isinstance(seq, Mapping):
                    out = []
                    for key, val in seq.items():
                        self._ids[id(val)] = str(key)
                        out.append(val)
                    return out
                return _as_seq(seq)
            if isinstance(g, Mapping):
                out = []
                for key, val in g.items():
                    self._ids[id(val)] = str(key)
                    out.append(val)
                return out
            return _as_seq(g)
        except Exception as exc:
            self._warn(f"graph.automations 读取失败，本视图为空：{exc!r}")
            return []

    def id_of(self, auto: Any, index: int = 0) -> str:
        for key in ("automation_id", "id", "name"):
            val = _get(auto, key, None)
            if val:
                return str(val)
        key = self._ids.get(id(auto))
        if key:
            return key
        return f"auto-{index}"

    def triggers_of(self, auto: Any) -> list[Any]:
        raw = _get(auto, "trigger", _MISSING)
        if raw is _MISSING or raw is None:
            raw = _get(auto, "triggers", None)
        # 单个 Mapping 是一个 trigger 条目，不是 id→trigger 映射
        if isinstance(raw, Mapping):
            return [raw]
        return _as_seq(raw)

    def do_nodes_of(self, auto: Any) -> list[Any]:
        raw = _get(auto, "do", _MISSING)
        if raw is _MISSING or raw is None:
            out = []
            for node in _as_seq(_get(auto, "nodes", None)):
                if str(_get(node, "kind", "do")) == "do":
                    out.append(node)
            return out
        return _as_seq(raw)

    def node_id(self, node: Any, index: int = 0) -> str:
        for key in ("node_id", "id", "name"):
            val = _get(node, key, None)
            if val:
                return str(val)
        return f"node-{index}"

    def automation_ir(
        self,
        auto: Any,
        *,
        automation_id: str | None = None,
        triggers: Sequence[Any] | None = None,
        do: Sequence[Any] | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """产出可编辑的 automation IR 形态（归一为 ``trigger`` + ``do`` 两段）。

        ⚠️ 真 schema 未内联（见 §7 补读清单），需要严格 IR 的团队请换
        ``EvoScanner.ir_builder`` 或重写本方法。
        """
        base = _as_mapping(auto)
        out: dict[str, Any] = {}
        for key, val in base.items():
            if key in ("trigger", "triggers", "do", "nodes", "automation_id", "id"):
                continue
            out[key] = val
        out["automation_id"] = automation_id or self.id_of(auto)
        trig = self.triggers_of(auto) if triggers is None else list(triggers)
        out["trigger"] = [_as_mapping(t) for t in trig]
        nodes = self.do_nodes_of(auto) if do is None else list(do)
        out["do"] = [_as_mapping(n) for n in nodes]
        if extra:
            out.update(dict(extra))
        return out


def _dedupe_triggers(triggers: Iterable[Any]) -> list[Any]:
    seen: set[Any] = set()
    out: list[Any] = []
    for trig in triggers:
        item = _as_mapping(trig)
        key = _canon(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


# ======================================================================
#  提案模型
# ======================================================================

class EvoStrategy(str, Enum):
    """五种进化策略（值即契约里的策略名）。"""
    MERGE_REDUNDANT = "merge_redundant"
    SPLIT_OVERSIZED = "split_oversized"
    ADJUST_TRIGGER = "adjust_trigger"
    ADD_FALLBACK = "add_fallback"
    PROMOTE_SHADOW = "promote_shadow"


class EvoStatus(str, Enum):
    """提案状态机。"""
    DRAFT = "draft"            # 刚生成，未 sim
    SIM_PASSED = "sim_passed"  # sim 通过，待入队
    QUEUED = "queued"          # 已进 ProposalManager 队列
    REJECTED = "rejected"      # sim 未通过 / sim 抛异常（fail-closed）
    DEFERRED = "deferred"      # 无 sim 可用且要求 sim，暂不放行
    ERROR = "error"            # 入队动作失败（fail-open：提案留存）


@dataclass
class EvoProposal:
    """一条自进化提案（前 7 个字段与提单契约逐字对齐）。"""

    proposal_id: str
    automation_id: str
    strategy: EvoStrategy
    reason: str
    suggested_ir: dict[str, Any]
    confidence: float
    status: EvoStatus = EvoStatus.DRAFT
    created_at: float = 0.0
    related_ids: tuple[str, ...] = ()
    sim: dict[str, Any] | None = None
    queued_id: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "automation_id": self.automation_id,
            "strategy": self.strategy.value,
            "reason": self.reason,
            "suggested_ir": self.suggested_ir,
            "confidence": self.confidence,
            "status": self.status.value,
            "created_at": self.created_at,
            "related_ids": list(self.related_ids),
            "sim": self.sim,
            "queued_id": self.queued_id,
            "meta": self.meta,
            "warnings": list(self.warnings),
        }

    def to_json(self) -> dict[str, Any]:
        return self.to_dict()


@dataclass(frozen=True)
class EvoPolicy:
    """全部阈值可配（默认值即 §0/§3 口径的原型期取值）。"""

    enabled: frozenset[str] = frozenset({s.value for s in EvoStrategy})
    # 合并冗余
    merge_trigger_similarity: float = 0.80
    merge_allow_empty_triggers: bool = False   # 空触发默认不合并（防误吞）
    merge_action_order_sensitive: bool = False
    merge_one_per_automation: bool = True      # 每个自动化一次扫描最多并一次
    merge_min_do: int = 1
    merge_max_automations: int = 400           # O(n²) 上界保护
    # 拆分过大
    split_do_limit: int = 5                    # 严格大于才拆
    split_target_size: int = 5
    # 调整触发
    intervene_rate_threshold: float = 0.30     # 严格大于才提案
    intervene_min_samples: int = 1
    ask_prompt: str = "该自动化干预率偏高，请确认是否继续执行"
    # 补兜底
    on_error_key: str = "on_error"
    default_on_error: dict = field(default_factory=lambda: {
        "kind": "ask", "prompt": "该步骤失败，请确认处置方式",
    })
    fallback_per_node: bool = False            # True = 每个缺口节点单独一条提案
    # 升档
    promote_shadow_hits: int = 3               # 连续命中 >= 3
    require_shadow_band: bool = True           # F13②：默认要求 shadow 档才允许 promote（对齐 G4 band 闸门）
    promote_confidence_target: float = AUTO_MIN
    # 健康度闸门
    low_health_only: bool = False
    low_health_below: float = 60.0
    # sim
    sim_required: bool = True
    sim_none_is_pass: bool = False             # fail-closed
    sim_unknown_is_pass: bool = False          # fail-closed
    # 入队
    queue: bool = True
    queue_conf_max: float = 0.59               # < SHADOW_LOW：只落 pending，不自动部署
    source: str = "evo"
    dedupe: bool = True
    conf_lo: float = 0.05
    conf_hi: float = 0.95
    history_max: int = 4096


# ======================================================================
#  suggested_ir 构造（可换装点）
# ======================================================================

# 真 IR 形态重排（F13①）：把 automation_ir 的 trigger/do 轻量形态
# 对齐 ir.schema.json v0.3.0 的 nodes/edges 文档。
_TRIGGER_FIELDS = ("type", "entity_id", "from", "to", "event", "at", "op", "offset", "sources")


def _clean_node_id(raw: str, fallback: str) -> str:
    """清洗为合法节点 id（schema 顶层要求 ``^[a-z][a-z0-9_]*$``，全小写）。"""
    s = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in (raw or "")).lower()
    if not s or not s[0].isalpha():
        s = "n_" + (s or "")
    return s[:64]


def _clean_trigger(trig: Mapping[str, Any]) -> dict[str, Any]:
    """只保留 schema trigger $def 允许的字段（additionalProperties:false）。

    evo 内部触发条件用 ``kind`` 表示类型，schema 用 ``type``，这里做映射。
    """
    t = dict(trig)
    if "kind" in t and "type" not in t:
        t["type"] = t.pop("kind")
    out: dict[str, Any] = {}
    for k in _TRIGGER_FIELDS:
        if k in t and t[k] is not None:
            out[k] = t[k]
    return out


def _ir_from_automation_ir(
    aid: str, ir: dict[str, Any], *, evo_meta: dict[str, Any]
) -> dict[str, Any]:
    """把单条 ``automation_ir`` 结果重排为合规 IR 文档（v0.3.0）。"""
    name = ir.get("name") or aid
    try:
        version = int(ir.get("version", 1) or 1)
    except (TypeError, ValueError):
        version = 1
    mode = ir.get("mode") or "single"
    _trig = ir.get("trigger")
    if isinstance(_trig, Mapping) and not isinstance(_trig, list):
        _trig = [_trig]          # 容忍单条 dict 触发（standalone ir_builder）
    triggers = [m for m in (_as_mapping(t) for t in (_trig or [])) if m]
    _do = ir.get("do")
    if isinstance(_do, Mapping) and not isinstance(_do, list):
        _do = [_do]              # 容忍单条 dict do（standalone ir_builder）
    do_nodes = [m for m in (_as_mapping(n) for n in (_do or [])) if m]
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    on_ids: list[str] = []
    for i, trig in enumerate(triggers):
        on_id = _clean_node_id(f"{aid}__on{i + 1}", f"on{i + 1}")
        nodes.append({"id": on_id, "kind": "on", "trigger": _clean_trigger(trig)})
        on_ids.append(on_id)
    do_ids: list[str] = []
    for j, dn in enumerate(do_nodes):
        did = _clean_node_id(
            dn.get("node_id") or dn.get("id") or f"{aid}__do{j + 1}", f"do{j + 1}"
        )
        node: dict[str, Any] = {"id": did, "kind": str(dn.get("kind", "do"))}
        for k, v in dn.items():
            if k in ("node_id", "id"):
                continue
            node[k] = v
        if node["kind"] == "do" and "adapter" not in node:
            node["adapter"] = "homeassistant"   # 真 IR 要求 do 节点标明适配器
        nodes.append(node)
        do_ids.append(did)
    if on_ids and do_ids:
        edges.append({"from": on_ids[0], "to": do_ids[0], "kind": "then"})
    if len(do_ids) > 1:
        for k in range(1, len(do_ids)):
            edges.append({"from": do_ids[k - 1], "to": do_ids[k], "kind": "then"})
    meta = dict(ir.get("meta") or {})
    meta["_evo"] = evo_meta
    return {
        "ir_version": "0.3.0",
        "id": _clean_node_id(aid, "evo"),
        "name": name,
        "version": version,
        "mode": mode,
        "nodes": nodes,
        "edges": edges,
        "meta": meta,
    }


def default_ir_builder(payload: Mapping[str, Any]) -> dict[str, Any]:
    """缺省 ir_builder：直接产出真 IR 文档（对齐 ir.schema.json v0.3.0）。

    F13①：不再包 graph-delta 信封，``suggested_ir`` 即为合规 IR——
    单自动化重排为 nodes/edges 文档；多自动化（split）以 ``kind:group``
    节点承载（children 为各 part 合规 IR，对齐 v0.3.0 group 锚）；提案元数据
    （revision_of/remove/changes）移入 ``meta._evo`` 保留审计链路。
    ProposalManager.build_ir 可直接 ``return dict(suggested_ir)`` 消费。
    """
    graph = dict(payload.get("graph", {}) or {})
    strategy = str(payload.get("strategy", ""))
    primary = payload.get("primary_id")
    evo_meta = {
        "strategy": strategy,
        "revision_of": list(payload.get("revision_of", []) or []),
        "remove": list(payload.get("remove", []) or []),
        "changes": list(payload.get("changes", []) or []),
        "detail": dict(payload.get("detail", {}) or {}),
    }
    autos = list(graph.values())
    if len(autos) <= 1:
        return _ir_from_automation_ir(
            primary or "evo", autos[0] if autos else {}, evo_meta=evo_meta
        )
    children = [
        _ir_from_automation_ir(aid, ir, evo_meta=evo_meta) for aid, ir in graph.items()
    ]
    group_node = {
        "id": _clean_node_id(f"{primary or 'evo'}__group", "group"),
        "kind": "group",
        "children": children,
    }
    return {
        "ir_version": "0.3.0",
        "id": _clean_node_id(primary or "evo_group", "evo_group"),
        "name": f"evo-{strategy}",
        "version": 1,
        "mode": "single",
        "nodes": [group_node],
        "edges": [],
        "meta": {"_evo": evo_meta},
    }


# ======================================================================
#  sim 适配（duck typing，fail-closed）
# ======================================================================

@dataclass
class SimOutcome:
    """一次 sim 验证的结论。"""

    ok: bool
    reason: str = ""
    mode: str = "unknown"      # missing | none | bool | number | text | mapping
    raw: Any = None            # sequence | attr | error | unknown

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "mode": self.mode,
            "raw": repr(self.raw) if self.raw is not None else None,
        }


_SIM_PARAM_MAP: dict[str, str] = {
    "ir": "ir", "suggested_ir": "ir", "graph_ir": "ir",
    "automation_ir": "ir", "proposal_ir": "ir",
    "proposal": "proposal", "evo": "proposal", "prop": "proposal",
    "evo_proposal": "proposal",
    "automation_id": "automation_id", "aid": "automation_id",
    "proposal_id": "proposal_id", "pid": "proposal_id",
    "context": "context", "ctx": "context",
}

_SIM_VALUES: dict[str, Callable[[EvoProposal], Any]] = {
    "ir": lambda p: p.suggested_ir,
    "proposal": lambda p: p,
    "automation_id": lambda p: p.automation_id,
    "proposal_id": lambda p: p.proposal_id,
    "context": lambda p: {"strategy": p.strategy.value, "automation_id": p.automation_id},
}


def _invoke_sim(simulator: Callable[..., Any], proposal: EvoProposal) -> Any:
    """按参数名把提案喂给 simulator（签名未定 → 见 §7，做成自适应）。"""
    try:
        sig = inspect.signature(simulator)
    except (TypeError, ValueError):
        return simulator(proposal.suggested_ir)
    args: list[Any] = []
    kwargs: dict[str, Any] = {}
    for name, param in sig.parameters.items():
        if param.kind is param.VAR_POSITIONAL or param.kind is param.VAR_KEYWORD:
            continue
        key = _SIM_PARAM_MAP.get(name, "ir")
        value = _SIM_VALUES[key](proposal)
        if param.kind is param.KEYWORD_ONLY:
            kwargs[name] = value
        else:
            args.append(value)
    return simulator(*args, **kwargs)


def _interpret(result: Any, policy: EvoPolicy, _depth: int = 0) -> tuple[bool, str, str]:
    check_param_depth(_depth, "evo._interpret")
    if result is None:
        return (policy.sim_none_is_pass, "simulator 返回 None", "none")
    if isinstance(result, bool):
        return (result, "simulator 返回布尔值", "bool")
    if isinstance(result, (int, float)):
        return (bool(result), f"simulator 返回数值 {result!r}", "number")
    if isinstance(result, str):
        text = result.strip().lower()
        ok = text in {"ok", "pass", "passed", "true", "1", "valid", "通过"}
        return (ok, f"simulator 返回文本 {result!r}", "text")
    if isinstance(result, Mapping):
        for key in ("ok", "passed", "success", "valid", "result"):
            if key in result:
                val = result[key]
                ok = val if isinstance(val, bool) else bool(val)
                note = result.get("reason") or result.get("message") or ""
                return (ok, str(note) or f"simulator 结果字段 {key}={val!r}", "mapping")
        errors = result.get("errors", result.get("failures"))
        if errors is not None:
            empty = len(list(errors)) == 0
            return (empty, f"simulator errors={errors!r}", "mapping")
        return (policy.sim_unknown_is_pass, "simulator 结果无法解读（mapping）", "unknown")
    if isinstance(result, (tuple, list)) and result:
        ok, reason, _mode = _interpret(result[0], policy, _depth + 1)
        extra = result[1] if len(result) > 1 and isinstance(result[1], str) else ""
        return (ok, str(extra) or reason, "sequence")
    for key in ("ok", "passed", "success", "valid"):
        if hasattr(result, key):
            val = getattr(result, key)
            ok = val if isinstance(val, bool) else bool(val)
            note = getattr(result, "reason", "") or getattr(result, "message", "")
            return (ok, str(note) or f"simulator 结果属性 {key}={val!r}", "attr")
    errors = getattr(result, "errors", None)
    if errors is not None:
        empty = len(list(errors)) == 0
        return (empty, f"simulator errors={errors!r}", "attr")
    return (policy.sim_unknown_is_pass, "simulator 结果无法解读", "unknown")


def run_sim(
    simulator: Callable[..., Any] | None,
    proposal: EvoProposal,
    *,
    policy: EvoPolicy | None = None,
    warnings: list[str] | None = None,
) -> SimOutcome:
    """跑一次 sim 验证。fail-closed：缺失 / 异常 / 无法解读 → 不放行。"""
    policy = policy or EvoPolicy()
    if simulator is None:
        ok = not policy.sim_required
        tail = "" if ok else "（fail-closed：不放行）"
        return SimOutcome(ok=ok, reason=f"未注入 simulator{tail}", mode="missing")
    try:
        result = _invoke_sim(simulator, proposal)
    except Exception as exc:
        if warnings is not None:
            warnings.append(f"[sim] {proposal.proposal_id} simulator 抛异常：{exc!r}")
        return SimOutcome(ok=False, reason=f"simulator 抛异常：{exc!r}", mode="error")
    ok, reason, mode = _interpret(result, policy)
    return SimOutcome(ok=ok, reason=reason, mode=mode, raw=result)


# ======================================================================
#  入队适配（ProposalManager duck typing）
# ======================================================================

class ProposalSink:
    """把 EvoProposal 送进 ProposalManager 统一审批队列。

    只使用 §1 可见的 ``submit(*, hypothesis_id, natural_language, conf,
    suggested_ir, source, proposal_id)`` 签名；找不到 submit 时退而求其次找
    ``enqueue / add / push / offer``。conf 一律被 ``conf_cap`` 封顶（见 §7 决策 5）。
    """

    def __init__(
        self,
        manager: Any,
        *,
        source: str = "evo",
        conf_cap: float = 0.59,
    ) -> None:
        self.manager = manager
        self.source = source
        self.conf_cap = conf_cap

    def submit(self, proposal: EvoProposal) -> Any:
        mgr = self.manager
        if mgr is None:
            raise RuntimeError("proposal_manager 未注入，无法入队")
        fn = _get(mgr, "submit", None)
        if not callable(fn):
            for name in ("enqueue", "add", "push", "offer"):
                fn = _get(mgr, name, None)
                if callable(fn):
                    break
        if not callable(fn):
            raise RuntimeError("proposal_manager 上找不到 submit()/enqueue() 公开入口")
        conf = min(float(proposal.confidence), self.conf_cap)
        proposal.meta["submitted_conf"] = round(conf, 3)
        return fn(
            hypothesis_id=proposal.proposal_id,
            natural_language=proposal.reason,
            conf=conf,
            suggested_ir=proposal.suggested_ir,
            source=self.source,
            proposal_id=proposal.proposal_id,
        )


def _extract_id(ret: Any) -> str | None:
    for key in ("proposal_id", "id"):
        val = _get(ret, key, None)
        if val:
            return str(val)
    return str(ret) if isinstance(ret, str) else None


# ======================================================================
#  扫描器
# ======================================================================

@dataclass
class EvoScanner:
    """自进化提案生成器：扫描 → 五策略检测 → sim → 入队。"""

    health_engine: Any = None
    proposal_manager: Any = None
    graph: Any = None
    executor_stats: Any = None
    policy: EvoPolicy = field(default_factory=EvoPolicy)
    simulator: Callable[..., Any] | None = None
    ir_builder: Callable[[Mapping[str, Any]], Mapping[str, Any]] = default_ir_builder
    sink: Callable[[EvoProposal], Any] | None = None
    clock: Callable[[], float] = time.time
    id_factory: Callable[[], str] = _new_id
    warnings: list[str] = field(default_factory=list)
    proposals: dict[str, EvoProposal] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    _seen: dict[Any, str] = field(default_factory=dict)

    # ---------------- 基础 ----------------

    def _warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def _view(self) -> GraphView:
        return GraphView(self.graph, self.warnings)

    def get(self, proposal_id: str) -> EvoProposal | None:
        return self.proposals.get(proposal_id)

    def list(self, status: "EvoStatus | str | None" = None) -> list[EvoProposal]:
        items = [self.proposals[pid] for pid in self.order]
        if status is None:
            return items
        want = status.value if isinstance(status, EvoStatus) else str(status)
        return [p for p in items if p.status.value == want]

    def dump(self) -> dict[str, Any]:
        return {"order": list(self.order),
                "proposals": [self.proposals[pid].to_dict() for pid in self.order]}

    def load(self, data: Mapping[str, Any]) -> int:
        count = 0
        for item in _as_seq(data.get("proposals")):
            if not isinstance(item, Mapping):
                continue
            try:
                prop = EvoProposal(
                    proposal_id=str(item["proposal_id"]),
                    automation_id=str(item["automation_id"]),
                    strategy=EvoStrategy(item["strategy"]),
                    reason=str(item.get("reason", "")),
                    suggested_ir=dict(item.get("suggested_ir", {}) or {}),
                    confidence=float(item.get("confidence", 0.0)),
                    status=EvoStatus(item.get("status", EvoStatus.DRAFT.value)),
                    created_at=float(item.get("created_at", 0.0)),
                    related_ids=tuple(item.get("related_ids", ()) or ()),
                    sim=item.get("sim"),
                    queued_id=item.get("queued_id"),
                    meta=dict(item.get("meta", {}) or {}),
                    warnings=tuple(item.get("warnings", ()) or ()),
                )
            except Exception as exc:
                self._warn(f"load：提案记录无法还原，已跳过：{exc!r}")
                continue
            self._register(prop)
            count += 1
        return count

    def _register(self, proposal: EvoProposal) -> None:
        self.proposals[proposal.proposal_id] = proposal
        self.order.append(proposal.proposal_id)
        if len(self.order) > self.policy.history_max:
            drop = self.order[:-self.policy.history_max]
            self.order = self.order[-self.policy.history_max:]
            for pid in drop:
                self.proposals.pop(pid, None)

    # ---------------- 数据源读取（fail-open，缺口进 warnings） ----------------

    def _health_of(self, automation_id: str) -> int | None:
        he = self.health_engine
        if he is None:
            return None
        for name in ("health_score", "score"):
            fn = _get(he, name, None)
            if callable(fn):
                try:
                    val = _to_int(fn(automation_id))
                except Exception as exc:
                    self._warn(f"[health] {automation_id} {name}() 读取失败：{exc!r}")
                    return None
                if val is not None:
                    return val
        return None

    def _intervention_rate(self, automation_id: str) -> float | None:
        he = self.health_engine
        if he is not None:
            fn = _get(he, "inputs", None)
            if callable(fn):
                try:
                    inp = fn(automation_id)
                except Exception as exc:
                    self._warn(f"[adjust_trigger] {automation_id} health.inputs() 失败：{exc!r}")
                    inp = None
                if inp is not None:
                    ov = _to_int(_get(inp, "overrides"))
                    total = _to_int(_get(inp, "attributed"))
                    if total is None:
                        total = _to_int(_get(inp, "attempts"))
                    if ov is not None and total and total > 0:
                        return _clamp01(ov / total)
            fn = _get(he, "intervention_rate", None)
            if callable(fn):
                try:
                    val = _to_float(fn(automation_id))
                except Exception as exc:
                    self._warn(f"[adjust_trigger] {automation_id} intervention_rate() 失败：{exc!r}")
                    val = None
                if val is not None:
                    return _clamp01(val)
            fn = _get(he, "health_report", None)
            if callable(fn):
                try:
                    for entry in _as_seq(fn()):
                        if str(_get(entry, "automation_id", "")) != automation_id:
                            continue
                        dims = _get(entry, "dims", {}) or {}
                        dim = dims.get("intervention_rate") if isinstance(dims, Mapping) else None
                        raw = _to_float(_get(dim, "raw", None)) if dim is not None else None
                        if raw is not None:
                            return _clamp01(raw)
                except Exception as exc:
                    self._warn(f"[adjust_trigger] {automation_id} health_report() 失败：{exc!r}")
        es = self.executor_stats
        if es is not None:
            fn = _get(es, "intervention_rate", None)
            if callable(fn):
                try:
                    val = _to_float(fn(automation_id))
                except Exception as exc:
                    self._warn(f"[adjust_trigger] {automation_id} executor_stats 干预率失败：{exc!r}")
                    val = None
                if val is not None:
                    return _clamp01(val)
        return None

    def _shadow_streak(self, automation_id: str) -> int | None:
        es = self.executor_stats
        if es is None:
            self._warn(f"[promote_shadow] {automation_id} 未注入 executor_stats，无法判定连续命中")
            return None
        for name in ("shadow_hits", "shadow_history", "hit_history", "shadow_results"):
            fn = _get(es, name, None)
            if callable(fn):
                try:
                    seq = _as_seq(fn(automation_id))
                except Exception as exc:
                    self._warn(f"[promote_shadow] {automation_id} {name}() 失败：{exc!r}")
                    return None
                return self._trailing_hits(seq, automation_id)
        for name in ("shadow_stats", "stats", "summary"):
            fn = _get(es, name, None)
            if callable(fn):
                try:
                    stat = fn(automation_id)
                except Exception as exc:
                    self._warn(f"[promote_shadow] {automation_id} {name}() 失败：{exc!r}")
                    return None
                for key in ("consecutive_hits", "streak", "shadow_streak", "hits_in_a_row"):
                    val = _to_int(_get(stat, key, None))
                    if val is not None:
                        return max(0, val)
        if isinstance(es, Mapping):
            entry = es.get(automation_id)
            if entry is not None:
                hits = _get(entry, "shadow_hits", _MISSING)
                if hits is not _MISSING and hits is not None:
                    return self._trailing_hits(_as_seq(hits), automation_id)
                for key in ("consecutive_hits", "streak", "shadow_streak"):
                    val = _to_int(_get(entry, key, None))
                    if val is not None:
                        return max(0, val)
        self._warn(f"[promote_shadow] {automation_id} executor_stats 无 shadow 命中口径，跳过")
        return None

    def _trailing_hits(self, seq: Sequence[Any], automation_id: str) -> int:
        streak = 0
        for item in reversed(list(seq)):
            val = self._hit_value(item)
            if val is None:
                self._warn(f"[promote_shadow] {automation_id} 命中记录无法解读，按未命中截断：{item!r}")
                break
            if not val:
                break
            streak += 1
        return streak

    @staticmethod
    def _hit_value(item: Any) -> bool | None:
        if item is None:
            return False
        if isinstance(item, bool):
            return item
        if isinstance(item, (int, float)):
            return bool(item)
        if isinstance(item, str):
            text = item.strip().lower()
            if text in {"hit", "match", "matched", "ok", "pass", "passed", "true", "1", "通过"}:
                return True
            if text in {"miss", "mismatch", "fail", "failed", "false", "0", "未命中"}:
                return False
            return None
        if isinstance(item, Mapping):
            for key in ("hit", "match", "matched", "ok", "passed", "success"):
                if key in item:
                    val = item[key]
                    return val if isinstance(val, bool) else bool(val)
            return None
        for key in ("hit", "match", "matched", "ok", "passed", "success"):
            if hasattr(item, key):
                val = getattr(item, key)
                return val if isinstance(val, bool) else bool(val)
        return None

    def _band_of(self, automation_id: str) -> str | None:
        es = self.executor_stats
        if es is None:
            return None
        for name in ("band", "status", "mode"):
            fn = _get(es, name, None)
            if callable(fn):
                try:
                    val = fn(automation_id)
                except Exception:
                    return None
                return str(val) if val is not None else None
        return None

    # ---------------- 检测 ----------------

    def detect(self) -> list[EvoProposal]:
        """跑五种策略，返回本次新产出的提案（fail-open：永不抛）。"""
        view = self._view()
        try:
            autos = view.automations()
        except Exception as exc:
            self._warn(f"graph 读取失败，本批次无提案：{exc!r}")
            return []
        autos = self._eligible(autos, view)
        detectors: tuple[tuple[EvoStrategy, Callable[..., list[EvoProposal]]], ...] = (
            (EvoStrategy.MERGE_REDUNDANT, self._detect_merge),
            (EvoStrategy.SPLIT_OVERSIZED, self._detect_split),
            (EvoStrategy.ADJUST_TRIGGER, self._detect_trigger),
            (EvoStrategy.ADD_FALLBACK, self._detect_fallback),
            (EvoStrategy.PROMOTE_SHADOW, self._detect_promote),
        )
        out: list[EvoProposal] = []
        for strategy, fn in detectors:
            if strategy.value not in self.policy.enabled:
                continue
            try:
                out.extend(fn(view, autos))
            except Exception as exc:
                self._warn(f"[{strategy.value}] 检测器异常，已跳过：{exc!r}")
        return out

    def _eligible(self, autos: Sequence[Any], view: GraphView) -> list[Any]:
        if not self.policy.low_health_only:
            return list(autos)
        out = []
        for idx, auto in enumerate(autos):
            try:
                aid = view.id_of(auto, idx)
                health = self._health_of(aid)
            except Exception as exc:
                self._warn(f"[health_gate] 自动化读取失败，按未知健康度放行：{exc!r}")
                out.append(auto)
                continue
            if health is None:
                self._warn(f"[health_gate] {aid} 健康分未知，按 fail-open 放行")
                out.append(auto)
            elif health < self.policy.low_health_below:
                out.append(auto)
        return out

    def _emit(
        self,
        strategy: EvoStrategy,
        automation_id: str,
        reason: str,
        confidence: float,
        payload: Mapping[str, Any],
        *,
        related: Sequence[str] = (),
        meta: Mapping[str, Any] | None = None,
        fingerprint_extra: Any = None,
    ) -> EvoProposal | None:
        key = (strategy.value, automation_id, tuple(sorted(related)),
               _canon(payload), fingerprint_extra)
        if self.policy.dedupe and key in self._seen:
            return None
        conf = _clamp(float(confidence), self.policy.conf_lo, self.policy.conf_hi)
        proposal = EvoProposal(
            proposal_id=f"evo-{strategy.value}-{self.id_factory()}",
            automation_id=automation_id,
            strategy=strategy,
            reason=reason,
            suggested_ir=dict(self.ir_builder(payload)),
            confidence=round(conf, 3),
            created_at=float(self.clock()),
            related_ids=tuple(related),
            meta=dict(meta or {}),
        )
        self._register(proposal)
        self._seen[key] = proposal.proposal_id
        return proposal

    # ---- 合并冗余 ----

    def _detect_merge(self, view: GraphView, autos: Sequence[Any]) -> list[EvoProposal]:
        pol = self.policy
        infos: list[tuple[Any, str, list[frozenset[str]], list[str]]] = []
        for idx, auto in enumerate(autos):
            try:
                aid = view.id_of(auto, idx)
                nodes = view.do_nodes_of(auto)
                if len(nodes) < pol.merge_min_do:
                    continue
                tsets = _token_sets(view.triggers_of(auto))
                sigs = [repr(action_signature(n)) for n in nodes]
                infos.append((auto, aid, tsets, sigs))
            except Exception as exc:
                self._warn(f"[merge_redundant] 自动化 #{idx} 读取失败，跳过：{exc!r}")
        if len(infos) > pol.merge_max_automations:
            self._warn(
                f"[merge_redundant] 自动化数 {len(infos)} 超过 {pol.merge_max_automations}，"
                f"本策略跳过（O(n²) 保护）"
            )
            return []
        used: set[str] = set()
        out: list[EvoProposal] = []
        for i in range(len(infos)):
            auto_a, aid_a, tsets_a, sigs_a = infos[i]
            for j in range(i + 1, len(infos)):
                auto_b, aid_b, tsets_b, sigs_b = infos[j]
                if pol.merge_one_per_automation and (aid_a in used or aid_b in used):
                    continue
                if pol.merge_action_order_sensitive:
                    same_actions = sigs_a == sigs_b
                else:
                    same_actions = sorted(sigs_a) == sorted(sigs_b)
                if not same_actions:
                    continue
                if not tsets_a or not tsets_b:
                    if not pol.merge_allow_empty_triggers:
                        continue
                    sim = 1.0
                else:
                    sim = _max_jaccard(tsets_a, tsets_b)
                if sim < pol.merge_trigger_similarity:
                    continue
                (auto_p, aid_p), (auto_s, aid_s) = sorted(
                    ((auto_a, aid_a), (auto_b, aid_b)), key=lambda pair: pair[1]
                )
                merged = view.automation_ir(
                    auto_p,
                    automation_id=aid_p,
                    triggers=_dedupe_triggers(
                        list(view.triggers_of(auto_p)) + list(view.triggers_of(auto_s))
                    ),
                    extra={"_evo": {"strategy": "merge_redundant",
                                    "merged_from": [aid_p, aid_s]}},
                )
                payload = {
                    "strategy": "merge_redundant",
                    "primary_id": aid_p,
                    "revision_of": [aid_p, aid_s],
                    "graph": {aid_p: merged},
                    "remove": [aid_s],
                    "changes": ["merge_triggers", "drop_duplicate_automation"],
                    "detail": {"trigger_similarity": round(sim, 3),
                               "do_count": len(sigs_a)},
                }
                proposal = self._emit(
                    EvoStrategy.MERGE_REDUNDANT, aid_p,
                    f"「{aid_p}」与「{aid_s}」触发条件相似（相似度 {sim:.2f}）且 do 动作完全相同"
                    f"（{len(sigs_a)} 步），属于重复自动化，建议合并以消除冗余与并发冲突。",
                    0.55 + 0.40 * sim, payload,
                    related=(aid_p, aid_s),
                    meta={"trigger_similarity": round(sim, 3), "merged_with": aid_s},
                )
                if proposal is not None:
                    out.append(proposal)
                    used.add(aid_a)
                    used.add(aid_b)
        return out

    # ---- 拆分过大 ----

    def _detect_split(self, view: GraphView, autos: Sequence[Any]) -> list[EvoProposal]:
        pol = self.policy
        out: list[EvoProposal] = []
        for idx, auto in enumerate(autos):
            try:
                aid = view.id_of(auto, idx)
                nodes = view.do_nodes_of(auto)
            except Exception as exc:
                self._warn(f"[split_oversized] 自动化 #{idx} 读取失败，跳过：{exc!r}")
                continue
            if len(nodes) <= pol.split_do_limit:
                continue
            parts = math.ceil(len(nodes) / max(1, pol.split_target_size))
            chunks = _balanced(nodes, parts)
            graph: dict[str, Any] = {}
            part_ids: list[str] = []
            for k, chunk in enumerate(chunks, start=1):
                pid = aid if k == 1 else f"{aid}#part{k}"
                graph[pid] = view.automation_ir(
                    auto, automation_id=pid, do=chunk,
                    extra={"_evo": {"strategy": "split_oversized",
                                    "part": k, "of": parts}},
                )
                part_ids.append(pid)
            payload = {
                "strategy": "split_oversized",
                "primary_id": aid,
                "revision_of": [aid],
                "graph": graph,
                "remove": [],
                "changes": ["split_do_nodes"],
                "detail": {"parts": parts, "do_count": len(nodes),
                           "part_ids": part_ids,
                           "sizes": [len(c) for c in chunks]},
            }
            proposal = self._emit(
                EvoStrategy.SPLIT_OVERSIZED, aid,
                f"「{aid}」含 {len(nodes)} 个 do 节点，超过阈值 {pol.split_do_limit}，职责过重、"
                f"失败面过大，建议拆成 {parts} 个自动化（各带同一触发，人工再收窄）。",
                0.55 + 0.05 * (len(nodes) - pol.split_do_limit), payload,
                meta={"do_count": len(nodes), "parts": parts, "part_ids": part_ids},
            )
            if proposal is not None:
                out.append(proposal)
        return out

    # ---- 调整触发 ----

    def _detect_trigger(self, view: GraphView, autos: Sequence[Any]) -> list[EvoProposal]:
        pol = self.policy
        out: list[EvoProposal] = []
        for idx, auto in enumerate(autos):
            try:
                aid = view.id_of(auto, idx)
                nodes = view.do_nodes_of(auto)
                rate = self._intervention_rate(aid)
            except Exception as exc:
                self._warn(f"[adjust_trigger] 自动化 #{idx} 读取失败，跳过：{exc!r}")
                continue
            if rate is None:
                self._warn(f"[adjust_trigger] {aid} 干预率口径不明/缺数据，跳过（fail-open）")
                continue
            if not (rate > pol.intervene_rate_threshold):
                continue
            ask_node = {
                "node_id": f"{aid}#ask",
                "kind": "ask",
                "prompt": pol.ask_prompt,
            }
            graph = {
                aid: view.automation_ir(
                    auto, automation_id=aid,
                    do=[dict(ask_node)] + [_as_mapping(n) for n in nodes],
                    extra={"_evo": {"strategy": "adjust_trigger",
                                    "intervention_rate": round(rate, 4),
                                    "changes": ["add_ask_gate", "review_trigger"]}},
                )
            }
            payload = {
                "strategy": "adjust_trigger",
                "primary_id": aid,
                "revision_of": [aid],
                "graph": graph,
                "remove": [],
                "changes": ["add_ask_gate", "review_trigger"],
                "detail": {"intervention_rate": round(rate, 4),
                           "threshold": pol.intervene_rate_threshold},
            }
            proposal = self._emit(
                EvoStrategy.ADJUST_TRIGGER, aid,
                f"「{aid}」人工干预率 {rate:.0%}，高于阈值 {pol.intervene_rate_threshold:.0%}："
                f"用户经常推翻执行结果，说明触发条件过宽或该动作本不该自动做，"
                f"建议收窄触发条件并在执行前加 ask 确认。",
                0.55 + (rate - pol.intervene_rate_threshold), payload,
                meta={"intervention_rate": round(rate, 4)},
            )
            if proposal is not None:
                out.append(proposal)
        return out

    # ---- 补兜底 ----

    def _detect_fallback(self, view: GraphView, autos: Sequence[Any]) -> list[EvoProposal]:
        pol = self.policy
        out: list[EvoProposal] = []
        for idx, auto in enumerate(autos):
            try:
                aid = view.id_of(auto, idx)
                nodes = view.do_nodes_of(auto)
            except Exception as exc:
                self._warn(f"[add_fallback] 自动化 #{idx} 读取失败，跳过：{exc!r}")
                continue
            missing: list[str] = []
            for k, node in enumerate(nodes):
                if not _has_on_error(node, pol.on_error_key):
                    missing.append(view.node_id(node, k))
            if not missing:
                continue
            groups = [[nid] for nid in missing] if pol.fallback_per_node else [missing]
            for group in groups:
                new_do = []
                for k, node in enumerate(nodes):
                    item = _as_mapping(node)
                    if view.node_id(node, k) in group:
                        item[pol.on_error_key] = dict(pol.default_on_error)
                    new_do.append(item)
                graph = {
                    aid: view.automation_ir(
                        auto, automation_id=aid, do=new_do,
                        extra={"_evo": {"strategy": "add_fallback",
                                        "patched_nodes": list(group)}},
                    )
                }
                payload = {
                    "strategy": "add_fallback",
                    "primary_id": aid,
                    "revision_of": [aid],
                    "graph": graph,
                    "remove": [],
                    "changes": ["add_on_error"],
                    "detail": {"patched_nodes": list(group),
                               "default_on_error": dict(pol.default_on_error)},
                }
                shown = ", ".join(group[:3]) + ("…" if len(group) > 3 else "")
                proposal = self._emit(
                    EvoStrategy.ADD_FALLBACK, aid,
                    f"「{aid}」的 do 节点 {shown} 缺少 {pol.on_error_key} 兜底：动作失败后没有恢复路径，"
                    f"建议补上默认兜底（{pol.default_on_error.get('kind', 'ask')}）。",
                    0.70 + 0.05 * len(group), payload,
                    related=(aid,),
                    meta={"patched_nodes": list(group)},
                    fingerprint_extra=tuple(group) if pol.fallback_per_node else None,
                )
                if proposal is not None:
                    out.append(proposal)
        return out

    # ---- 升档 ----

    def _detect_promote(self, view: GraphView, autos: Sequence[Any]) -> list[EvoProposal]:
        pol = self.policy
        out: list[EvoProposal] = []
        for idx, auto in enumerate(autos):
            try:
                aid = view.id_of(auto, idx)
                streak = self._shadow_streak(aid)
            except Exception as exc:
                self._warn(f"[promote_shadow] 自动化 #{idx} 读取失败，跳过：{exc!r}")
                continue
            if streak is None:
                continue
            if streak < pol.promote_shadow_hits:
                continue
            if pol.require_shadow_band and self._band_of(aid) != "shadow":
                self._warn(f"[promote_shadow] {aid} 不在 shadow 档（band={self._band_of(aid)!r}），跳过")
                continue
            graph = {
                aid: view.automation_ir(
                    auto, automation_id=aid,
                    extra={"confidence": pol.promote_confidence_target,
                           "_evo": {"strategy": "promote_shadow",
                                    "band_target": "auto",
                                    "confidence_target": pol.promote_confidence_target,
                                    "shadow_streak": streak}},
                )
            }
            payload = {
                "strategy": "promote_shadow",
                "primary_id": aid,
                "revision_of": [aid],
                "graph": graph,
                "remove": [],
                "changes": ["promote_to_auto"],
                "detail": {"shadow_streak": streak, "band_target": "auto",
                           "confidence_target": pol.promote_confidence_target},
            }
            proposal = self._emit(
                EvoStrategy.PROMOTE_SHADOW, aid,
                f"「{aid}」shadow 比对连续命中 {streak} 次（阈值 {pol.promote_shadow_hits}），"
                f"与线上行为持续一致，建议升档为 auto（conf ≥ {pol.promote_confidence_target:.2f}）。",
                0.60 + 0.10 * streak, payload,
                meta={"shadow_streak": streak,
                      "confidence_target": pol.promote_confidence_target},
            )
            if proposal is not None:
                out.append(proposal)
        return out

    # ---------------- sim / 入队 / 编排 ----------------

    def simulate(self, proposals: Sequence[EvoProposal] | None = None) -> list[EvoProposal]:
        items = list(proposals) if proposals is not None else self.list()
        done: list[EvoProposal] = []
        for prop in items:
            if prop.status is not EvoStatus.DRAFT:
                continue
            outcome = run_sim(self.simulator, prop, policy=self.policy, warnings=self.warnings)
            prop.sim = outcome.to_dict()
            if outcome.ok:
                prop.status = EvoStatus.SIM_PASSED
            elif outcome.mode == "missing":
                prop.status = EvoStatus.DEFERRED
            else:
                prop.status = EvoStatus.REJECTED
            done.append(prop)
        return done

    def enqueue(self, proposals: Sequence[EvoProposal] | None = None) -> list[EvoProposal]:
        items = list(proposals) if proposals is not None else self.list()
        if not self.policy.queue:
            return items
        for prop in items:
            if prop.status is not EvoStatus.SIM_PASSED:
                continue
            sink = self.sink
            try:
                if sink is None:
                    sink = ProposalSink(
                        self.proposal_manager,
                        source=self.policy.source,
                        conf_cap=self.policy.queue_conf_max,
                    ).submit
                ret = sink(prop)
            except Exception as exc:
                prop.status = EvoStatus.ERROR
                prop.meta["submit_error"] = repr(exc)
                self._warn(f"[{prop.strategy.value}] 提案 {prop.proposal_id} 入队失败：{exc!r}")
                continue
            prop.queued_id = _extract_id(ret)
            prop.status = EvoStatus.QUEUED
        return items

    def scan(self) -> list[EvoProposal]:
        """扫描 → 生成 → sim → 入队，返回本次新产出的提案（fail-open，永不抛）。"""
        proposals = self.detect()
        self.simulate(proposals)
        self.enqueue(proposals)
        return proposals