"""Runtime 组装 —— 把所有部件按依赖注入拼起来。

生产与仿真的差异**只体现在注入的对象上**：
- 生产：`HassStateProvider` + `SystemTimeSource` + `HAAdapter`
- 仿真：`hass.states` 包装 + `VirtualTimeSource` + 绑定到 vhass 的适配器

业务代码不感知差异，这是"仿真过了实际就能跑"的前提。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from .af_adapters import HAAdapter, HTTPAdapter, MockAdapter, AdapterRegistry
from .af_audit import (
    INSTANCE_LEASE_HELD,
    INSTANCE_RESTORE_DROPPED,
    INSTANCE_RESTORED,
    AuditEvent,
    AuditLog,
)
from .af_bus import ACCEPTED, BusEvent, EventBus
from .af_conf import ConfidenceStore
from .af_executor import NodeExecutor
from .af_instance import TERMINAL_STATES, Instance, InstanceManager
from .af_ir import Graph
from .af_persist import PersistStore, restore_instance
from .af_scheduler import Quota, Scheduler
from .af_fire_recorder import FireRecorder, JsonFireStore
from .af_state import InMemoryStateProvider, StateProvider
from .af_time import SystemTimeSource, TimeSource, VirtualTimeSource

__all__ = ["Runtime", "build_runtime"]


@dataclass
class Runtime:
    """Runtime 运行体。默认单进程内存态；传入 `persist_dir` 时启用 P1 实例持久化与崩溃恢复。"""

    graph: Graph
    states: StateProvider
    clock: TimeSource = field(default_factory=SystemTimeSource)
    audit: AuditLog = field(default_factory=AuditLog)
    adapters: AdapterRegistry = field(default_factory=AdapterRegistry)
    quota: Quota = field(default_factory=Quota)
    conf: ConfidenceStore = field(default_factory=ConfidenceStore)
    #: P1 实例持久化目录；None = 关闭（默认，与原型期内存态行为一致）
    persist_dir: str | None = None
    #: conf 分级引擎（mimo #19）；None = 未启用，由环境变量 AUTOFORGE_CONF_GRADING=1 激活
    grading: Any = None

    def __post_init__(self) -> None:
        self.bus = EventBus(self.clock, self.audit)
        self.instances = InstanceManager(self.states, self.clock, self.audit)
        # v0.5.0：执行计数（每自动化 runs/success/failed），供指标聚合回灌 MA
        self._exec_stats: dict[str, dict[str, int]] = {}
        self.instances.on_spawn = self._on_spawn
        self.instances.on_terminal = self._on_terminal
        self.executor = NodeExecutor(
            self.instances, self.adapters, self.clock, self.audit,
            states=self.states, conf=self.conf, bus=self.bus,
        )
        self.scheduler = Scheduler(
            graph=self.graph,
            instances=self.instances,
            executor=self.executor,
            states=self.states,
            clock=self.clock,
            audit=self.audit,
            quota=self.quota,
        )
        # v0.4.0：`emit` 发布的事件必须驱动 `on event` 订阅者（发布 → 订阅闭环）
        self.executor.on_emit = self.scheduler.handle_event
        self.conf.seed(self.graph)

        # ── P1 实例持久化与崩溃恢复 ──────────────────────────────────
        self.persist: PersistStore | None = (
            PersistStore(self.persist_dir) if self.persist_dir else None
        )
        # mimo FireRecorder: 当日一次记账落盘，防重启重放（与 persist 同目录）
        if self.persist_dir is not None:
            self.scheduler.fire_recorder = FireRecorder(
                JsonFireStore(self.persist_dir), self.clock,
                max_attempts_per_day=3, lease_s=30.0, keep_days=3,
            )
        self.restored: list[Instance] = []
        self.instances.on_change = self._on_instance_change
        if self.persist is not None:
            self.restored = self.restore_persisted()

        # ── mimo #19: conf 分级引擎（可选，默认关闭） ──────────────────
        # 环境变量 AUTOFORGE_CONF_GRADING=1 启用；启用后 ShadowRunner 装饰
        # executor._do，按 conf band 路由（auto 透传 / shadow 只读比对 / ask 挂起），
        # InterventionDetector 监听人工干预，CanarySupervisor 观察期自动晋升/降级。
        # SSE 事件接入由 af_live.py 在启动时调用 runtime.grading.observe()。
        import os as _os
        if _os.environ.get("AUTOFORGE_CONF_GRADING", "").lower() in ("1", "true", "yes"):
            try:
                from autoforge.af_runtime_ext import install as _install_grading
                self.grading = _install_grading(self)
                self.audit.append({"kind": "conf_grading_enabled", "at": self.clock.now()})
            except Exception as _e:
                self.audit.append({"kind": "conf_grading_init_failed", "error": str(_e), "at": self.clock.now()})
                self.grading = None

    # ── 事件 ──────────────────────────────────────────────────────────
    def publish(self, event: BusEvent) -> list[Instance]:
        """发布事件（经过总线去重/节流/熔断），返回本次触发的实例。"""
        outcome = self.bus.publish(event)
        if outcome != ACCEPTED:
            return []
        return self.scheduler.handle_event(event)

    def emit(
        self,
        entity_id: str,
        state: str,
        *,
        source: str = "ha",
        last_changed: str | None = None,
        **payload: Any,
    ) -> list[Instance]:
        """便捷方法：构造事件并发布（测试与 FakeHA 场景常用）。

        P1-12：先更新状态源再发布事件——事件到达时状态必须已就绪，
        否则 spawn 实例时 _refresh_snapshot 会因未知实体 raise UnknownEntity。
        """
        if hasattr(self.states, "set_state"):
            self.states.set_state(entity_id, state)
        return self.publish(BusEvent.of(entity_id, state, source=source, last_changed=last_changed, **payload))

    # ── 持久化（P1：实例持久化与崩溃恢复）───────────────────────────────
    def _on_instance_change(self, instance: Instance) -> None:
        """实例状态变更回调：`persist=true` 的实例非终态落盘、终态清除。"""
        if self.persist is None or not instance.automation.persist:
            return
        if instance.is_terminal:
            self.persist.remove(instance.instance_id)
        else:
            self.persist.save(instance, self.clock)

    def restore_persisted(self) -> list[Instance]:
        """崩溃恢复：把落盘实例挂回运行时。

        丢弃规则（丢弃即删文件 + 审计，**不静默**）：自动化已不在当前图 / `persist` 已关 /
        落盘状态为终态。恢复的实例**保持落盘时的状态**，由随后的 `tick()` 推进
        （含"崩溃期间已错过的 `on_timeout`"）。
        """
        if self.persist is None:
            return []
        restored: list[Instance] = []
        for record in self.persist.records():
            automation_id = str(record.get("automation_id", ""))
            instance_id = str(record.get("instance_id", ""))
            try:
                automation = self.graph.get(automation_id)
            except KeyError:
                self.persist.remove(instance_id)
                self.audit.add(
                    AuditEvent(
                        type=INSTANCE_RESTORE_DROPPED,
                        at=self.clock.now(),
                        message=f"恢复丢弃实例 {instance_id}：自动化 {automation_id} 不在当前图中",
                        automation_id=automation_id,
                        instance_id=instance_id,
                    )
                )
                continue
            if not automation.persist or str(record.get("state", "")) in TERMINAL_STATES:
                self.persist.remove(instance_id)
                continue
            # v0.9.0 租约仲裁：实例租约仍属其他进程 → 跳过（不删文件、不双跑）
            if not self.persist.claims(record, self.clock):
                self.audit.add(
                    AuditEvent(
                        type=INSTANCE_LEASE_HELD,
                        at=self.clock.now(),
                        message=(
                            f"跳过恢复实例 {instance_id}：租约仍属"
                            f" {record.get('owner', '?')}（至 {record.get('lease_until_wall', '?')}）"
                        ),
                        automation_id=automation_id,
                        instance_id=instance_id,
                    )
                )
                continue
            instance = restore_instance(record, automation, self.clock)
            self.instances.attach(instance)
            restored.append(instance)
            self.audit.add(
                AuditEvent(
                    type=INSTANCE_RESTORED,
                    at=self.clock.now(),
                    message=f"恢复实例 {instance_id}（{automation_id}，state={instance.state}）",
                    automation_id=automation_id,
                    instance_id=instance_id,
                )
            )
        return restored

    # ── 时间 ──────────────────────────────────────────────────────────
    def advance(self, seconds: float) -> list[Instance]:
        """推进虚拟时钟并触发 tick（时间旅行）。仅 VirtualTimeSource 支持。"""
        if not hasattr(self.clock, "advance"):
            raise TypeError(f"当前时间源 {type(self.clock).__name__} 不支持时间旅行")
        self.clock.advance(seconds)  # type: ignore[attr-defined]
        return self.tick()

    def tick(self) -> list[Instance]:
        """时间推进后的统一处理：实例超时 / `for` 到期 / 定时触发 / 队列出队。"""
        fired = self.scheduler.tick()
        self.instances.expire_stale()
        return fired

    # ── 交互 ──────────────────────────────────────────────────────────
    def answer(self, room: str | None, text: str) -> Instance | None:
        """人类应答（按 room 维度匹配，创建时间优先，一次应答仅生效一次）。"""
        return self.executor.answer(room, text)

    def cancel(self, instance: Instance, reason: str = "") -> Instance:
        return self.executor.cancel(instance, reason)

    # ── 观测 ──────────────────────────────────────────────────────────
    def stats(self) -> dict[str, Any]:
        return {
            "automations": [a.id for a in self.graph],
            "active_instances": self.instances.count_active(),
            "pending_asks": len(self.executor.pending_asks),
            "restored_instances": len(self.restored),
            "bus": self.bus.stats(),
            "audit": [e.to_dict() for e in self.audit],
        }

    # ── v0.5.0：执行计数（供指标聚合回灌 MA）───────────────────────────────
    def _on_spawn(self, instance: Any) -> None:
        auto_id = instance.automation.id
        bucket = self._exec_stats.setdefault(
            auto_id, {"runs": 0, "success": 0, "failed": 0}
        )
        bucket["runs"] += 1

    def _on_terminal(self, instance: Any, state: str) -> None:
        auto_id = instance.automation.id
        bucket = self._exec_stats.setdefault(
            auto_id, {"runs": 0, "success": 0, "failed": 0}
        )
        if state == "done":
            bucket["success"] += 1
        elif state == "failed":
            bucket["failed"] += 1

    @property
    def exec_stats(self) -> dict[str, dict[str, int]]:
        """每自动化的执行计数（深拷贝，避免外部篡改内部状态）。"""
        return {k: dict(v) for k, v in self._exec_stats.items()}


def build_runtime(
    graph: Graph,
    states: StateProvider | None = None,
    *,
    clock: TimeSource | None = None,
    dry_run: bool = True,
    http_allowed_hosts: tuple[str, ...] = (),
    quota: Quota | None = None,
    conf: ConfidenceStore | None = None,
    persist_dir: str | None = None,
) -> Runtime:
    """按默认组合装配一个 Runtime。

    默认 `dry_run=True`：HA 适配器只记录下发意图，不写真机。
    传入 `persist_dir` 时启用 P1 实例持久化：构造即恢复上次落盘的活跃实例。
    """
    states = states if states is not None else InMemoryStateProvider()
    clock = clock if clock is not None else VirtualTimeSource(datetime(2026, 9, 14, 8, 0, tzinfo=timezone.utc))

    registry = AdapterRegistry()
    registry.register(MockAdapter())
    registry.register(HAAdapter(dry_run=dry_run))
    registry.register(HTTPAdapter(allowed_hosts=http_allowed_hosts, dry_run=dry_run))

    return Runtime(
        graph=graph,
        states=states,
        clock=clock,
        adapters=registry,
        quota=quota or Quota(),
        conf=conf or ConfidenceStore(),
        persist_dir=persist_dir,
    )
