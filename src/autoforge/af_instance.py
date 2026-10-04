"""实例管理器 —— 状态机 / 上下文 / 快照 / vars（KICKOFF §4.4，IR §3）。

状态机：`created → active → suspended → active … → done | cancelled | failed | expired`
**非法迁移直接抛异常**（不静默吞掉，否则图与运行时会悄悄漂移）。

两条硬约束：
1. **上下文必须可 JSON 序列化**——禁止存闭包 / Socket / 生成器（G1 每次状态变更都自检一次）
2. **快照 = 求值段**——恢复时**新建**快照并丢弃旧的；`do` 结果只写 `vars`，不回写快照
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping

from .af_audit import INSTANCE_EXPIRED, AuditEvent, AuditLog
from .af_ir import Automation, VarDecl
from .af_state import Snapshot, StateProvider, UnknownEntity
from .af_time import TimeSource, SystemTimeSource

__all__ = [
    "CREATED",
    "ACTIVE",
    "SUSPENDED",
    "DONE",
    "CANCELLED",
    "FAILED",
    "EXPIRED",
    "TERMINAL_STATES",
    "IllegalTransition",
    "InstanceTimer",
    "InstanceContext",
    "Instance",
    "InstanceManager",
    "DEFAULT_TTL_SECONDS",
]

CREATED = "created"
ACTIVE = "active"
SUSPENDED = "suspended"
DONE = "done"
CANCELLED = "cancelled"
FAILED = "failed"
EXPIRED = "expired"

TERMINAL_STATES = frozenset({DONE, CANCELLED, FAILED, EXPIRED})

#: 状态迁移白名单（IR §3）
_ALLOWED: dict[str, frozenset[str]] = {
    CREATED: frozenset({ACTIVE, CANCELLED, FAILED, EXPIRED}),
    ACTIVE: frozenset({SUSPENDED, DONE, CANCELLED, FAILED, EXPIRED}),
    SUSPENDED: frozenset({ACTIVE, DONE, CANCELLED, FAILED, EXPIRED}),
    DONE: frozenset(),
    CANCELLED: frozenset(),
    FAILED: frozenset(),
    EXPIRED: frozenset(),
}

#: 实例存活上限（IR §3 / §12-6）
DEFAULT_TTL_SECONDS = 24 * 3600


class IllegalTransition(Exception):
    """非法状态迁移。故意抛异常而不是静默忽略。"""


# ─────────────────────────────────────────────────────────────────────
# 上下文
# ─────────────────────────────────────────────────────────────────────


@dataclass
class InstanceTimer:
    """实例级定时器（`wait` / `ask` 超时）。**与 `for` 持续条件是两套原语**（IR §11）。"""

    node_id: str
    due_at: float  # monotonic
    kind: str = "timeout"

    def remaining(self, now: float) -> float:
        return max(0.0, self.due_at - now)

    def to_dict(self, now: float) -> dict[str, Any]:
        return {"node": self.node_id, "remaining_s": round(self.remaining(now), 3), "kind": self.kind}


@dataclass
class InstanceContext:
    """实例上下文——**必须可 JSON 序列化**（IR §3）。"""

    instance_id: str
    automation_id: str
    version: int = 1
    mode: str = "single"
    current_node: str = ""
    state: str = CREATED
    snapshot: dict[str, Any] = field(default_factory=dict)
    vars: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    timers: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = ""
    trace: list[dict[str, Any]] = field(default_factory=list)
    #: v0.9.0 实例归属：创建/持有本实例的进程身份（owner_id()），恢复仲裁用
    owner: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "automation_id": self.automation_id,
            "version": self.version,
            "mode": self.mode,
            "current_node": self.current_node,
            "state": self.state,
            "snapshot": self.snapshot,
            "vars": self.vars,
            "context": self.context,
            "timers": self.timers,
            "created_at": self.created_at,
            "trace": self.trace,
            "owner": self.owner,
        }


@dataclass
class Instance:
    """运行实例 = 上下文 + 活的快照对象 + 定时器。"""

    ctx: InstanceContext
    automation: Automation
    snapshot: Snapshot | None = None
    timer: InstanceTimer | None = None
    created_monotonic: float = 0.0

    @property
    def instance_id(self) -> str:
        return self.ctx.instance_id

    @property
    def state(self) -> str:
        return self.ctx.state

    @property
    def is_terminal(self) -> bool:
        return self.ctx.state in TERMINAL_STATES

    @property
    def current_node_id(self) -> str:
        return self.ctx.current_node

    def trace(self, node_id: str, note: str = "") -> None:
        self.ctx.trace.append(
            {"node": node_id, "note": note, "state": self.ctx.state, "at": _utc_now_iso()}
        )

    def to_dict(self) -> dict[str, Any]:
        """序列化自检：上下文必须可 JSON 序列化（防闭包 / Socket / 生成器混入）。

        P2-3 修复：移除 `default=str`。原写法会把不可序列化对象**静默字符串化**
        （如 canary 挂起的 `(wrapped, adapter)` 元组），上线后序列化"成功"但恢复时
        解包崩溃、回滚静默失效。改为严格序列化——一旦混入非 JSON 对象，立刻抛异常
        （fail-fast），把问题暴露在测试/启动阶段而非运行时。
        """
        return json.loads(json.dumps(self.ctx.to_dict(), ensure_ascii=False))


# ─────────────────────────────────────────────────────────────────────
# 管理器
# ─────────────────────────────────────────────────────────────────────


class InstanceManager:
    """实例的生老病死。配额与 mode 由 `af_scheduler` 决定，这里只做状态迁移。"""

    def __init__(
        self,
        state_provider: StateProvider,
        clock: TimeSource | None = None,
        audit: AuditLog | None = None,
        *,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
    ):
        self.states = state_provider
        self.clock = clock or SystemTimeSource()
        self.audit = audit if audit is not None else AuditLog()
        self.ttl_seconds = ttl_seconds
        self._instances: dict[str, Instance] = {}
        #: 状态变更回调（Runtime 用它把 persist=true 的实例落盘 / 终态清除）
        self.on_change: Callable[[Instance], None] | None = None
        #: 实例创建回调（Runtime 用它累加执行次数）
        self.on_spawn: Callable[[Instance], None] | None = None
        #: 实例终态回调（Runtime 用它累加成功 / 失败；state 为 DONE / FAILED 之一）
        self.on_terminal: Callable[[Instance, str], None] | None = None

    # ── 查询 ──────────────────────────────────────────────────────────
    def get(self, instance_id: str) -> Instance:
        return self._instances[instance_id]

    def all(self) -> list[Instance]:
        return list(self._instances.values())

    def active_of(self, automation_id: str) -> list[Instance]:
        return [
            i
            for i in self._instances.values()
            if i.ctx.automation_id == automation_id and i.ctx.state in (ACTIVE, SUSPENDED)
        ]

    def count_active(self) -> int:
        return sum(1 for i in self._instances.values() if i.ctx.state in (ACTIVE, SUSPENDED))

    def count_active_of(self, automation_id: str) -> int:
        return len(self.active_of(automation_id))

    # ── 生命周期 ──────────────────────────────────────────────────────
    def spawn(self, automation: Automation, trigger_event: Any = None) -> Instance:
        """created → active：拉首份快照，初始化 vars 与 context。"""
        instance_id = uuid.uuid4().hex[:12]
        ctx = InstanceContext(
            instance_id=instance_id,
            automation_id=automation.id,
            version=automation.version,
            mode=automation.mode,
            created_at=_utc_now_iso(),
        )
        instance = Instance(ctx=ctx, automation=automation, created_monotonic=self.clock.monotonic())
        ctx.state = CREATED
        ctx.context = {
            "instance_id": instance_id,
            # v1.7.1：`context.trigger_time` 取**时间源**而非墙钟。
            # 表达式里 `time_hour(context.trigger_time)` 这类时间窗判断依赖它：
            # 用墙钟会让「晚上 20:00-22:00」在仿真/回放里不可复现（同一 IR 换个时刻跑结论就变），
            # 与项目红线「表达式不读墙钟、时间必须可测」相悖。虚拟时间源下现在完全确定。
            "trigger_time": self.clock.now().isoformat(),
            "trigger": _trigger_repr(trigger_event),
        }
        # 初始化声明过的实例变量
        for name, decl in automation.vars.items():
            ctx.vars[name] = _initial_value(decl)

        self._instances[instance_id] = instance
        self._refresh_snapshot(instance)
        self._transition(instance, ACTIVE)
        entry = automation.entry_nodes()[0] if automation.entry_nodes() else None
        ctx.current_node = entry.id if entry else ""
        instance.trace(entry.id if entry else "", note="spawn")
        if self.on_spawn is not None:
            self.on_spawn(instance)
        return instance

    def suspend(
        self, instance: Instance, node_id: str, duration_s: float | None, kind: str = "timeout"
    ) -> None:
        """active → suspended：注册实例级定时器，结束当前求值段。

        `duration_s=None` 表示无限期挂起（如未设 timeout 的 `ask`，等人类应答）。
        """
        instance.timer = (
            None
            if duration_s is None
            else InstanceTimer(node_id=node_id, due_at=self.clock.monotonic() + duration_s, kind=kind)
        )
        instance.ctx.current_node = node_id
        self._transition(instance, SUSPENDED)

    def resume(self, instance: Instance, next_node: str | None = None) -> None:
        """suspended → active：**必须重新取快照**（丢弃旧快照）。"""
        instance.timer = None
        self._refresh_snapshot(instance)
        self._transition(instance, ACTIVE)
        if next_node:
            instance.ctx.current_node = next_node

    def done(self, instance: Instance) -> None:
        self._transition(instance, DONE)
        if self.on_terminal is not None:
            self.on_terminal(instance, DONE)

    def cancel(self, instance: Instance, reason: str = "") -> None:
        """any → cancelled：**不回滚已执行动作**（IR §13-1），清理必须显式写在 on_cancel 分支。"""
        instance.ctx.context["cancel_reason"] = reason
        self._transition(instance, CANCELLED, allow_from_terminal=False)

    def fail(self, instance: Instance, reason: str = "") -> None:
        instance.ctx.context["fail_reason"] = reason
        self._transition(instance, FAILED, allow_from_terminal=False)
        if self.on_terminal is not None:
            self.on_terminal(instance, FAILED)

    def expire(self, instance: Instance, reason: str = "超过实例存活上限") -> None:
        self._transition(instance, EXPIRED, allow_from_terminal=False)
        self.audit.add(
            AuditEvent(
                type=INSTANCE_EXPIRED,
                at=self.clock.now(),
                message=f"实例 {instance.instance_id} {reason}",
                automation_id=instance.ctx.automation_id,
                instance_id=instance.instance_id,
            )
        )

    def remove(self, instance: Instance) -> None:
        self._instances.pop(instance.instance_id, None)
        self._notify(instance)

    def attach(self, instance: Instance) -> Instance:
        """把外部重建的实例挂回管理器（崩溃恢复用）。

        只做"登记 + 重取快照 + 同步定时器"，**不改状态**——恢复后该实例仍处于
        落盘时的 `active`/`suspended`，由后续 `tick()` 推进。重取快照符合 IR §7.1：
        恢复时必须新建快照并丢弃旧快照（`attach` 之前 `instance.snapshot` 为空）。
        """
        self._instances[instance.instance_id] = instance
        self._refresh_snapshot(instance)
        self._sync_timers(instance)
        return instance

    # ── 定时器 ────────────────────────────────────────────────────────
    def due_timers(self) -> list[Instance]:
        """到点的挂起实例（待 Runtime 触发 on_timeout）。"""
        now = self.clock.monotonic()
        return [
            i
            for i in self._instances.values()
            if i.ctx.state == SUSPENDED and i.timer is not None and i.timer.due_at <= now
        ]

    def reap_terminal(self, ttl_seconds: float = 300.0) -> list[Instance]:
        """P1-1 修复：终态实例（done/cancelled/failed/expired）在保留窗口后从内存摘除，
        防止常驻进程内存随实例数单调增长。

        Runtime 可在 tick 中周期性调用；`expire_stale` 也会顺带调用以清理历史终态实例。
        保留窗口默认 300s，保证刚完成/刚失败的实例仍可在可观测窗口内被查询。
        """
        now = self.clock.monotonic()
        dead = [
            i
            for i in self._instances.values()
            if i.is_terminal and (now - i.created_monotonic) >= ttl_seconds
        ]
        for instance in dead:
            self.remove(instance)
        return dead

    def expire_stale(self) -> list[Instance]:
        """超过 TTL（默认 24h）的非终态实例强制销毁，并顺带摘除终态实例（`reap_terminal`）。

        这里**没有**"超配额"分支：配额（`af_scheduler.Quota`）管的是"还能不能再触发新实例"，
        不是"字典里最多留多少条"。原先正文写着"或超配额"，读的人会以为内存上限由配额兜底。
        """
        now = self.clock.monotonic()
        stale = [
            i
            for i in self._instances.values()
            if not i.is_terminal and (now - i.created_monotonic) >= self.ttl_seconds
        ]
        for instance in stale:
            self.expire(instance)
        # P1-1 修复：过期实例立即从内存摘除（已审计 EXPIRED，不可观测为 active）
        for instance in stale:
            self.remove(instance)
        # 顺带清理历史终态实例（保留 300s 可观测窗口）
        self.reap_terminal()
        return stale

    # ── vars ──────────────────────────────────────────────────────────
    def set_var(self, instance: Instance, name: str, value: Any) -> None:
        """写 `vars.*`。类型以 automation 里的声明为准（禁止隐式转换）。"""
        decl = instance.automation.vars.get(name)
        instance.ctx.vars[name] = _coerce_declared(value, decl, name)

    def get_var(self, instance: Instance, name: str, default: Any = None) -> Any:
        return instance.ctx.vars.get(name, default)

    # ── 内部 ──────────────────────────────────────────────────────────
    def _refresh_snapshot(self, instance: Instance) -> None:
        """新建快照并丢弃旧快照（IR §7.1）。

        P1-12：`snapshot()` 对未知实体 raise `UnknownEntity`（四个 `StateProvider` 实现
        同口径，见第七轮审计）。spawn 时不因此阻止实例创建，但**回退形态是空快照**：
        一旦某个读取实体漂移，本实例这一整段都读不到状态，表达式逐条抛
        `UnknownEntity` → 执行器 `on_error` 软失效（`af_executor.py:442`）。
        即"单个实体漂移 = 整段软失效"，不是"只有引用它的那一支失效"——
        仿真侧一直就是这个口径，生产侧第七轮起对齐。代价与备选见
        `docs/audit/审计报告_第七轮_核实与修复.md`。
        """
        from .af_state import UnknownEntity
        entity_ids = sorted(instance.automation.reads())
        known = [e for e in entity_ids if self.states.has(e)] if hasattr(self.states, "has") else entity_ids
        try:
            snapshot = self.states.snapshot(known)
        except UnknownEntity:
            # 双重保险：has() 与 snapshot() 之间状态变化的竞态
            snapshot = self.states.snapshot([])
        instance.snapshot = snapshot
        instance.ctx.snapshot = snapshot.to_dict()

    def _transition(self, instance: Instance, target: str, allow_from_terminal: bool = False) -> None:
        current = instance.ctx.state
        if current in TERMINAL_STATES and not allow_from_terminal:
            raise IllegalTransition(f"实例 {instance.instance_id} 已处于终态 {current}，不能再迁移到 {target}")
        if target not in _ALLOWED[current]:
            raise IllegalTransition(f"非法状态迁移：{current} → {target}（实例 {instance.instance_id}）")
        instance.ctx.state = target
        instance.to_dict()  # 序列化自检
        self._sync_timers(instance)
        self._notify(instance)

    def _notify(self, instance: Instance) -> None:
        if self.on_change is not None:
            self.on_change(instance)

    def _sync_timers(self, instance: Instance) -> None:
        now = self.clock.monotonic()
        if instance.timer is None:
            instance.ctx.timers = []
        else:
            instance.ctx.timers = [instance.timer.to_dict(now)]


# ─────────────────────────────────────────────────────────────────────
# 工具
# ─────────────────────────────────────────────────────────────────────


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _trigger_repr(event: Any) -> Any:
    if event is None:
        return None
    if isinstance(event, Mapping):
        return dict(event)
    entity_id = getattr(event, "entity_id", None)
    state = getattr(event, "state", None)
    if entity_id is not None:
        return {"entity_id": entity_id, "state": state}
    return repr(event)


def _initial_value(decl: VarDecl) -> Any:
    if decl.value is not None:
        return decl.value
    if decl.type == "numeric":
        return 0.0
    if decl.type == "boolean":
        return False
    if decl.type == "enum":
        return decl.enum[0] if decl.enum else ""
    return ""


def _coerce_declared(value: Any, decl: VarDecl | None, name: str) -> Any:
    """按声明类型强制转换。**未声明时保持原值**（不猜）。"""
    if decl is None:
        return value
    if decl.type == "numeric":
        if isinstance(value, bool):
            raise TypeError(f"vars.{name} 声明为 numeric，但拿到布尔值 {value!r}")
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise TypeError(f"vars.{name} 声明为 numeric，但值 {value!r} 无法转成数字") from exc
    if decl.type == "boolean":
        if isinstance(value, bool):
            return value
        if str(value).strip().lower() in ("on", "true", "1", "yes"):
            return True
        if str(value).strip().lower() in ("off", "false", "0", "no"):
            return False
        raise TypeError(f"vars.{name} 声明为 boolean，但值 {value!r} 无法转换")
    if decl.type == "string":
        return str(value)
    if decl.type == "enum":
        text = str(value)
        if decl.enum and text not in decl.enum:
            raise ValueError(f"vars.{name} = {text!r} 不在枚举 {list(decl.enum)} 内")
        return text
    return value
