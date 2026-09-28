"""AutoForge IR 数据模型。

铁律（IR_AND_RUNTIME §1）：**Graph 是唯一真相**。
- 序列化的唯一真相是 `schema/ir.schema.json`（JSON Schema）
- 本模块的 dataclass 只是它的 **Python 投影**，方便运行时操作
- 增删字段必须**先改 schema 再改这里**，反过来即违反铁律

已实现（原 P1 预留）：`emit`（v0.3.0 跨自动化事件·**发布侧**，节点字段形态，见 `EmitDecl`）、
`persist`（实例持久化与崩溃恢复，见 `af_persist`）。

仍为保留位：`fn` —— 允许出现在 IR 中但 Runtime 读到即报未实现（属 v1.0.0 评估范围）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

import jsonschema
from jsonschema import Draft202012Validator

__all__ = [
    "IR_VERSION",
    "GROUP_IR_VERSION",
    "SUPPORTED_IR_VERSIONS",
    "is_supported_ir_version",
    "NODE_KINDS",
    "EDGE_KINDS",
    "EDGE_PRIORITY",
    "VAR_TYPES",
    "IRValidationError",
    "EmitDecl",
    "AskSpec",
    "AskAnswer",
    "Trigger",
    "Node",
    "Edge",
    "VarDecl",
    "Automation",
    "Graph",
    "load_automation",
    "load_graph",
    "validate_automation",
]

IR_VERSION = "0.2.1"

#: IR 版本枚举单一真值源（决策 D：v2.3 引入 group 容器节点时在此追加 "0.3.0"，
#: schema 的 ir_version.enum 必须与之保持同步——灰度兼容旧 IR，旧版本不被拒绝）。
SUPPORTED_IR_VERSIONS: tuple[str, ...] = ("0.2.1", "0.3.0")

#: group 容器节点（v2.3/F9，决策 D）落地版本：compose_group 产出的复合 IR 打此版本，
#: 与 SUPPORTED_IR_VERSIONS / schema 的 ir_version.enum 同源。勿用 0.2.1 承载 group
#: ——旧版本消费者无法理解 mode='group'，版本号必须诚实反映所用能力。
GROUP_IR_VERSION = "0.3.0"


def is_supported_ir_version(version: str) -> bool:
    """该 IR 版本是否被当前运行时接受（与 schema 的 ir_version.enum 同源）。"""
    return version in SUPPORTED_IR_VERSIONS

#: 7 种节点（fn 为远期预留，G1 不实现）
NODE_KINDS = ("on", "if", "do", "ask", "wait", "set", "pass", "group")

#: 6 种边（`on_error` 是 v0.2 新增的第 6 种）
EDGE_KINDS = ("then", "yes", "no", "default", "on_timeout", "on_cancel", "on_error")

#: 边优先级（冲突消解，强制）：中断 > 失败 > 超时 > 明确应答/正常流转 > 兜底
#: 注意 `yes`/`no` 与 `then` 同层；`default` 永远最低
EDGE_PRIORITY: tuple[str, ...] = (
    "on_cancel",
    "on_error",
    "on_timeout",
    "yes",
    "no",
    "then",
    "default",
)

VAR_TYPES = ("numeric", "boolean", "string", "enum")

SCHEMA_PATH = Path(__file__).parent / "schema" / "ir.schema.json"

# 运行时读到即报未实现的保留字段（`emit` 已于 v0.3.0 实现，`persist` 于 P1 实现）
_RESERVED_NODE_KEYS = ("fn",)


class IRValidationError(Exception):
    """IR 未通过 JSON Schema 校验。"""

    def __init__(self, message: str, errors: Sequence[Any] = ()):
        super().__init__(message)
        self.errors = list(errors)


# ─────────────────────────────────────────────────────────────────────
# 组件
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Trigger:
    """`on` 节点的触发源。

    类型：
        state —— 实体状态跃变（`to`/`from`），可叠加 `for` 做持续条件
        sun   —— 太阳历事件（sunrise/sunset，可带 offset）
        time  —— 定时（`at` = "HH:MM"）
        group —— 多源组合（and/or），避免图膨胀
    """

    type: str
    entity_id: str | None = None
    from_: str | None = None
    to: str | None = None
    event: str | None = None
    offset: str | None = None
    at: str | None = None
    op: str | None = None
    sources: tuple["Trigger", ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Trigger":
        return cls(
            type=data["type"],
            entity_id=data.get("entity_id"),
            from_=data.get("from"),
            to=data.get("to"),
            event=data.get("event"),
            offset=data.get("offset"),
            at=data.get("at"),
            op=data.get("op"),
            sources=tuple(cls.from_dict(s) for s in data.get("sources", ())),
        )

    def entity_ids(self) -> set[str]:
        """该触发源引用的全部实体（含 group 递归）。

        v0.4.0：`event` 类型不是实体，但在总线中的主体为 `event.<name>`，
        一并返回——这样跨自动化依赖矩阵与 `EMIT_SELF_LOOP` 环检测能覆盖**事件边**。
        """
        out: set[str] = set()
        if self.type == "event" and self.event:
            from ..af_bus import EVENT_ENTITY_PREFIX  # 局部导入：避免 af_ir 模块级依赖 af_bus

            out.add(f"{EVENT_ENTITY_PREFIX}{self.event}")
        if self.entity_id:
            out.add(self.entity_id)
        for sub in self.sources:
            out |= sub.entity_ids()
        return out

    def leaf_triggers(self) -> Iterator["Trigger"]:
        """展开 group，yield 出所有叶子触发源。"""
        if self.type == "group":
            for sub in self.sources:
                yield from sub.leaf_triggers()
        else:
            yield self


@dataclass(frozen=True)
class EmitDecl:
    """跨自动化事件·**发布侧**声明（v0.3.0，IR §4.3）。

    只传消息，**禁止跨自动化读写对方私有变量**——`data` 只接受可 JSON 序列化的值。
    `delay` 非空时走实例定时器（与 `wait` 同口径、经 `TimeSource`），保证时间旅行可测。
    """

    event: str
    data: dict[str, Any] = field(default_factory=dict)
    delay: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EmitDecl":
        return cls(
            event=data["event"],
            data=dict(data.get("data") or {}),
            delay=data.get("delay"),
        )


@dataclass(frozen=True)
class AskSpec:
    """v2 M3 结构化 Ask 规格：ask 节点的一等公民结构化挂起。

    带病规格**构造期即拒**（IRValidationError），不允许进运行时；运行时据此渲染
    原生控件元数据并强制收敛纪律（连问≤3轮 / 同房间同时≤2问 / 缺省即拒）。

    `prompt` 不由 IR 的 `ask` 对象携带——它是 ask 节点的节点级字段（schema 已强制
    必填），由 `Node.from_dict` 投影进来，避免两处真相。
    """

    KINDS = ("choice", "entity", "time_range", "threshold", "text")

    kind: str
    prompt: str = ""
    options: tuple[str, ...] = ()
    min_: float | None = None
    max_: float | None = None
    unit_: str | None = None
    entity_domain: str = ""

    def __post_init__(self) -> None:
        if self.kind not in self.KINDS:
            raise IRValidationError(f"AskSpec.kind 非法: {self.kind!r}（合法: {self.KINDS}）")
        if self.kind == "choice" and len(self.options) < 2:
            raise IRValidationError("AskSpec kind=choice 至少需要 2 个 options")
        if self.kind in ("threshold", "time_range"):
            if self.min_ is None or self.max_ is None:
                raise IRValidationError(f"AskSpec kind={self.kind} 必须显式给出 min/max")
            if float(self.min_) > float(self.max_):
                raise IRValidationError("AskSpec min 必须 ≤ max")
        if self.kind == "entity" and not self.entity_domain:
            raise IRValidationError("AskSpec kind=entity 必须给出 entity_domain")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], prompt: str = "") -> "AskSpec":
        return cls(
            kind=data["kind"],
            prompt=prompt,  # 投影自 ask 节点的节点级 prompt，不从 ask 对象读
            options=tuple(str(o) for o in data.get("options") or ()),
            min_=float(data["min"]) if data.get("min") is not None else None,
            max_=float(data["max"]) if data.get("max") is not None else None,
            unit_=data.get("unit"),
            entity_domain=data.get("entity_domain", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        """回写为 IR 形态（`min/max/unit` 无下划线，与 schema 一致）。"""
        out: dict[str, Any] = {"kind": self.kind}
        if self.options:
            out["options"] = list(self.options)
        if self.min_ is not None:
            out["min"] = self.min_
        if self.max_ is not None:
            out["max"] = self.max_
        if self.unit_ is not None:
            out["unit"] = self.unit_
        if self.entity_domain:
            out["entity_domain"] = self.entity_domain
        return out

    def control(self) -> dict[str, Any]:
        """前端控件元数据：kind → 原生控件的一一映射。

        映射只此一处（后端是单一真相），前端退化成纯渲染器：新增 kind 或接其他
        客户端（MCP/CLI）都不必再复制这份映射。
        """
        meta: dict[str, Any] = {"widget": "input", "kind": self.kind, "prompt": self.prompt}
        if self.kind == "choice":
            meta.update(widget="select", options=list(self.options))
        elif self.kind == "threshold":
            meta.update(widget="slider", min=self.min_, max=self.max_, unit=self.unit_)
        elif self.kind == "time_range":
            # 分钟域 [0,1439]：前端按 HH:MM 呈现，提交值仍是分钟
            meta.update(widget="time_range", min=self.min_, max=self.max_, unit="min")
        elif self.kind == "entity":
            meta.update(widget="entity_picker", entity_domain=self.entity_domain)
        return meta


@dataclass(frozen=True)
class AskAnswer:
    """v2 M3 结构化 Ask 的人类应答（运行时持有，驱动收敛纪律与校验）。"""

    kind: str
    value: Any = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AskAnswer":
        return cls(kind=data["kind"], value=data.get("value"))


@dataclass(frozen=True)
class Node:
    """7 种节点之一。字段按 kind 取用，未用到的为 None。"""

    id: str
    kind: str
    name: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    # on
    trigger: Trigger | None = None
    for_: str | None = None
    debounce: str | None = None
    # if
    expr: dict[str, Any] | None = None
    # do
    adapter: str | None = None
    action: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    # v2.1 1.1 补域：scene/script 等「间接触发」动作声明的间接实体效果
    #（如 scene.activate 点亮哪些灯）。仿真据此展开，使 expect 可验证而非 unverified。
    effects: tuple[dict[str, Any], ...] = ()
    atomic: bool = False
    result_var: str | None = None
    requires_confirm: bool = False
    canary: dict[str, Any] | None = None
    # ask
    prompt: str = ""
    session: str = "room"
    room: str | None = None
    timeout: str | None = None
    # IR 字段名为 `ask`；类型名 AskSpec 只出现在定义里
    ask: "AskSpec | None" = None
    # wait
    duration: str | None = None
    # set
    var: str | None = None
    value: Any = None
    from_: str | None = None
    # emit（v0.3.0 跨自动化事件·发布侧）
    emit: EmitDecl | None = None
    # 保留位
    reserved: dict[str, Any] = field(default_factory=dict)
    # group 容器节点（v2.3/F9）：子自动化列表，原子部署单元
    children: tuple["Automation", ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Node":
        return cls(
            id=data["id"],
            kind=data["kind"],
            name=data.get("name", ""),
            raw=dict(data),
            trigger=Trigger.from_dict(data["trigger"]) if data.get("trigger") else None,
            for_=data.get("for"),
            debounce=data.get("debounce"),
            expr=data.get("expr"),
            adapter=data.get("adapter"),
            action=data.get("action"),
            params=dict(data.get("params") or {}),
            effects=tuple(data.get("effects") or ()),
            atomic=bool(data.get("atomic", False)),
            result_var=data.get("result_var"),
            requires_confirm=bool(data.get("requires_confirm", False)),
            canary=data.get("canary"),
            prompt=data.get("prompt", ""),
            session=data.get("session", "room"),
            room=data.get("room"),
            timeout=data.get("timeout"),
            ask=AskSpec.from_dict(data["ask"], prompt=data.get("prompt", "")) if data.get("ask") else None,
            duration=data.get("duration"),
            var=data.get("var"),
            value=data.get("value"),
            from_=data.get("from"),
            emit=EmitDecl.from_dict(data["emit"]) if data.get("emit") else None,
            children=tuple(Automation.from_dict(c, child=True) for c in data.get("children") or ()),
            reserved={k: data[k] for k in _RESERVED_NODE_KEYS if k in data},
        )

    @property
    def address(self) -> str:
        """可寻址形式：`kind:node_id`（跨 automation 时用 `automation_id:node_id`）。"""
        return f"{self.kind}:{self.id}"

    @property
    def is_suspending(self) -> bool:
        """是否挂起点（ask/wait）。"""
        return self.kind in ("ask", "wait")

    def target_entities(self) -> set[str]:
        """`do` 节点写入的实体（`params.entity_id` 与 `params.target.entity_id`，支持 str 与 list）。

        安全约定：必须同时解析 `entity_id` 与 `target.{entity_id,device_id,area_id}`，
        否则扫描器/爆炸半径/ACL 会漏掉 `target:` 形式的写目标（P0-5 绕过）。
        `device_id`/`area_id` 需服务端查 HA 展开，此处先标记为待展开（前缀 `device:`/`area:`）。
        """
        if self.kind != "do":
            return set()
        result: set[str] = set()

        # 1. params.entity_id（传统形式）
        raw = self.params.get("entity_id")
        if raw is not None:
            if isinstance(raw, str):
                result.add(raw)
            elif isinstance(raw, (list, tuple)):
                result.update(str(x) for x in raw)

        # 2. params.target.{entity_id, device_id, area_id}（HA 原生 target 形式）
        target = self.params.get("target")
        if isinstance(target, dict):
            for key in ("entity_id", "device_id", "area_id"):
                val = target.get(key)
                if val is None:
                    continue
                prefix = "" if key == "entity_id" else f"{key}:"
                if isinstance(val, str):
                    result.add(f"{prefix}{val}")
                elif isinstance(val, (list, tuple)):
                    result.update(f"{prefix}{x}" for x in val)

        return result


@dataclass(frozen=True)
class Edge:
    """6 种边之一。同节点同优先级边不可重复定义（扫描器检查）。"""

    from_: str
    to: str
    kind: str
    id: str = ""
    label: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Edge":
        return cls(
            from_=data["from"],
            to=data["to"],
            kind=data["kind"],
            id=data.get("id", ""),
            label=data.get("label", ""),
        )

    @property
    def priority(self) -> int:
        """数值越小优先级越高（见 EDGE_PRIORITY）。"""
        return EDGE_PRIORITY.index(self.kind)


@dataclass(frozen=True)
class VarDecl:
    """实例变量声明：类型必须显式，禁止隐式转换（IR §3.1 / §14-10）。"""

    type: str
    value: Any = None
    enum: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "VarDecl":
        return cls(
            type=data["type"],
            value=data.get("value"),
            enum=tuple(data.get("enum", ())),
        )


@dataclass
class Automation:
    """静态模板（Graph 描述的规则）。无状态、可版本化。"""

    id: str
    name: str
    version: int
    mode: str
    nodes: dict[str, Node]
    edges: tuple[Edge, ...]
    snapshot: bool = True
    confidence: float | None = None
    persist: bool = False
    enabled: bool = True
    vars: dict[str, VarDecl] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    # ── 构造 ──────────────────────────────────────────────────────────
    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, child: bool = False) -> "Automation":
        validate_automation(data, root_key="child_automation" if child else None)
        nodes = {n["id"]: Node.from_dict(n) for n in data["nodes"]}
        _check_unique_ids(data.get("id", "?"), data["nodes"], nodes)
        edges = tuple(Edge.from_dict(e) for e in data.get("edges", ()))
        raw = dict(data)
        raw["enabled"] = bool(data.get("enabled", True))  # 显式落位，保证 round-trip 一致
        auto = cls(
            id=data["id"],
            name=data["name"],
            version=int(data.get("version", 1)),
            mode=data.get("mode", "single"),
            nodes=nodes,
            edges=edges,
            snapshot=bool(data.get("snapshot", True)),
            confidence=data.get("confidence"),
            persist=bool(data.get("persist", False)),
            enabled=bool(data.get("enabled", True)),
            vars={k: VarDecl.from_dict(v) for k, v in (data.get("vars") or {}).items()},
            meta=dict(data.get("meta") or {}),
            raw=raw,
        )
        _check_edges(auto)
        return auto

    # ── 图查询 ────────────────────────────────────────────────────────
    def node(self, node_id: str) -> Node:
        return self.nodes[node_id]

    def entry_nodes(self) -> list[Node]:
        """入口节点 = 所有 `on` 节点。"""
        return [n for n in self.nodes.values() if n.kind == "on"]

    def outgoing(self, node_id: str) -> list[Edge]:
        return [e for e in self.edges if e.from_ == node_id]

    def edge_of(self, node_id: str, kind: str) -> Edge | None:
        for e in self.outgoing(node_id):
            if e.kind == kind:
                return e
        return None

    def pick_edge(self, node_id: str, kinds: Iterable[str]) -> Edge | None:
        """在给定可用边类型中，按 EDGE_PRIORITY 取优先级最高的一条。

        **不允许按定义顺序 fallback**——永远按优先级从高到低匹配。
        """
        wanted = set(kinds)
        candidates = [e for e in self.outgoing(node_id) if e.kind in wanted]
        if not candidates:
            return None
        return min(candidates, key=lambda e: (e.priority, e.to))

    # ── 实体依赖（跨自动化环检测用）────────────────────────────────────
    def reads(self) -> set[str]:
        """读到的实体：触发源 + 表达式里的 `entity.*`（用于快照预取）。"""
        from .expr import collect_entity_refs  # 局部导入避免循环

        out: set[str] = set()
        for node in self.nodes.values():
            if node.trigger is not None:
                out |= node.trigger.entity_ids()
            if node.expr is not None:
                out |= collect_entity_refs(node.expr)
        return out

    def trigger_entities(self) -> set[str]:
        """**触发源**引用的实体——跨自动化依赖矩阵只看这个。

        为什么不用 `reads()`：条件里读同一个实体（如"灯是关的"）不会造成隐式循环，
        只有"状态变了会重新触发对方"才会。用 reads() 会把 case01 这类正常自动化误判成环。
        """
        out: set[str] = set()
        for node in self.entry_nodes():
            if node.trigger is not None:
                out |= node.trigger.entity_ids()
        return out

    def writes(self) -> set[str]:
        """写到的实体：所有 `do` 节点的目标。"""
        out: set[str] = set()
        for node in self.nodes.values():
            out |= node.target_entities()
        return out

    def emitted_events(self) -> set[str]:
        """`emit` 发布的事件名（v0.3.0；供自触发环检测与事件风暴上限使用）。"""
        return {n.emit.event for n in self.nodes.values() if n.emit is not None}

    def emit_nodes(self) -> list[Node]:
        """所有携带 `emit` 的节点。"""
        return [n for n in self.nodes.values() if n.emit is not None]

    # ── v1.2.0 后置条件断言 ────────────────────────────────────────────
    def expects(self) -> tuple[Mapping[str, Any], ...]:
        """IR 顶层 `expect`：自动化自己声明的「跑完之后应该是什么」。"""
        return tuple(self.raw.get("expect") or ())

    def expect_entities(self) -> set[str]:
        """`expect` 里实体形态断言引用的实体（供静态扫描校验可达性）。"""
        out: set[str] = set()
        for item in self.expects():
            entity_id = item.get("entity_id")
            if entity_id:
                out.add(str(entity_id))
        return out

    def address_of(self, node_id: str) -> str:
        return f"{self.id}:{node_id}"


@dataclass
class Graph:
    """多个 Automation 的集合——跨自动化分析（依赖环、并发配额）的入口。"""

    automations: list[Automation] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._by_id: dict[str, Automation] = {a.id: a for a in self.automations}

    def get(self, automation_id: str) -> Automation:
        return self._by_id[automation_id]

    def __iter__(self) -> Iterator[Automation]:
        return iter(self.automations)

    def __len__(self) -> int:
        return len(self.automations)


# ─────────────────────────────────────────────────────────────────────
# 校验与加载
# ─────────────────────────────────────────────────────────────────────


def _schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_automation(data: Mapping[str, Any], *, root_key: str | None = None) -> None:
    """按 JSON Schema 校验单条 Automation，失败抛 IRValidationError。

    root_key 非空时校验 `#/$defs/<root_key>`（如 `child_automation`），用于 group
    子自动化——子自动化不需顶层 `ir_version`。
    """
    schema = _schema()
    validator = Draft202012Validator(schema)
    if root_key:
        # evolve 保留全文档 resolver，使子 $def 内的 `#/$defs/node` 等引用可正确解析
        validator = validator.evolve(schema=schema["$defs"][root_key])
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    if errors:
        detail = "; ".join(
            f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}" for e in errors[:10]
        )
        raise IRValidationError(f"IR 校验失败：{detail}", errors)


def load_automation(data: Mapping[str, Any]) -> Automation:
    return Automation.from_dict(data)


def load_graph(source: str | Path | Mapping[str, Any]) -> Graph:
    """从路径或 dict 加载 Graph。

    支持两种形态：
    - 单条 automation：`{"id": ..., "nodes": [...]}`
    - 多条容器：`{"automations": [ ... ]}`（跨自动化分析用，如依赖环检测）
    """
    if isinstance(source, (str, Path)):
        data = json.loads(Path(source).read_text(encoding="utf-8"))
    else:
        data = dict(source)

    if "automations" in data:
        return Graph([Automation.from_dict(a) for a in data["automations"]])
    return Graph([Automation.from_dict(data)])


def collect_asks(auto: "Automation") -> list[dict[str, Any]]:
    """v2 M3：收集自动化中所有 ask 节点的控件元数据（供 /api/asks 暴露原生控件）。"""
    out: list[dict[str, Any]] = []
    for node in auto.nodes.values():
        if node.kind == "ask" and node.ask is not None:
            out.append(
                {
                    "node_id": node.id,
                    "prompt": node.prompt,
                    "spec": node.ask.to_dict(),
                    "control": node.ask.control(),
                }
            )
    return out


# ─────────────────────────────────────────────────────────────────────
# 内部结构自检（schema 之外的语义约束）
# ─────────────────────────────────────────────────────────────────────


def _check_unique_ids(auto_id: str, raw_nodes: Any, nodes: dict[str, Node]) -> None:
    if len(raw_nodes) != len(nodes):
        raise IRValidationError(f"automation `{auto_id}` 存在重复 node id")


def _check_edges(auto: Automation) -> None:
    for e in auto.edges:
        if e.from_ not in auto.nodes:
            raise IRValidationError(f"automation `{auto.id}` 的边 {e.from_}→{e.to} 起点不存在")
        if e.to not in auto.nodes:
            raise IRValidationError(f"automation `{auto.id}` 的边 {e.from_}→{e.to} 终点不存在")
