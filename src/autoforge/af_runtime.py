"""Runtime 组装 —— 把所有部件按依赖注入拼起来。

生产与仿真的差异**只体现在注入的对象上**：
- 生产：`HassStateProvider` + `SystemTimeSource` + `HAAdapter`
- 仿真：`hass.states` 包装 + `VirtualTimeSource` + 绑定到 vhass 的适配器

业务代码不感知差异，这是"仿真过了实际就能跑"的前提。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

#: DCD 2026-11-04（T2/T3-AF）：房间口径统一到 HA `area_id`，解析机制**唯一真值**在库侧
#: `homesdk.areas`（0.3.3 起）。缺失 ⇒ 解析一律 None（fail-closed：宁可不出声，不静默投错房间）。
try:  # pragma: no cover - 环境面分支
    from homesdk import areas as _areas
except Exception:  # pragma: no cover - 旧版/未装
    _areas = None  # type: ignore[assignment]

logger = logging.getLogger("autoforge.runtime")

from .af_adapters import HAAdapter, HTTPAdapter, InboxAdapter, MaAdapter, MockAdapter, AdapterRegistry
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
from . import af_mqtt_bridge as _bridge
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
    #: 运行期事实落盘根（PM 卡 2026-11-04）；None = 关闭（旧行为，不落触发/试演事实）。
    #: 非 None 时，触发计数与 canary 试演状态写 `<store_root>/<archive>.runtime.json`。
    store_root: str | None = None
    #: 区域表 `area_id → area_name`（DCD 2026-11-04 裁定 T3-AF：房间口径统一到 HA `area_id`）。
    #: 调用方从自己的 HA 读（AF 走 `.catalog/catalog.json` 的 `area_id`/`area`）；空 ⇒ 解析一律 None，
    #: 按契约拒投递并打 `AREA_UNRESOLVED`（⛔ 静默 accepted）。
    area_map: dict[str, str] = field(default_factory=dict)
    #: 旧中文名 → area_id 的过渡别名表（各仓历史数据自己提供；homesdk 不内置任何固定表）。
    room_aliases: dict[str, str] = field(default_factory=dict)

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
        # ── 运行期事实落盘（PM 卡 2026-11-04）：触发计数 ＋ canary 试演状态 ──
        # 只在给了 store_root 时接线；未给 = 零副作用（旧行为不变）。
        if self.store_root:
            from .af_runtime_facts import RuntimeFacts

            def _facts_for(automation_id: str):
                # 归档名 == 自动化 id 是本项目的既定不变量（§1.133 教训：目录名必须=id，
                # ⛔ 不许别名归档）；运行事实旁车按同一名字落。
                return RuntimeFacts(self.store_root, automation_id)

            def _record_trigger(automation_id, node_id, entity_id, via):
                _facts_for(automation_id).record_trigger(
                    automation_id=automation_id, node_id=node_id,
                    entity_id=entity_id, via=via,
                )

            def _record_trial(state, automation_id):
                _facts_for(automation_id).record_trial(
                    state=state, automation_id=automation_id,
                )

            self.scheduler.runtime_facts = _record_trigger
            self.executor.runtime_facts = _record_trial
        # v0.4.0：`emit` 发布的事件必须驱动 `on event` 订阅者（发布 → 订阅闭环）
        self.executor.on_emit = self.scheduler.handle_event
        # DCD 裁定 2026-11-04 阶段1（乙）：ask 触发时经现有 MQTT 桥向 butler/inbox/speak 额外播报。
        # ⚠️ 桥在 serve 里是 build_app 之后才 attach（见 af_mqtt_bridge.current_bridge 注释）⇒
        # 这里**闭包惰性取** current_bridge()，⛔ 构造期抓引用会永远抓到 None（装配期无桥）。
        # 无桥 ⇒ 直接 no-op（零副作用）。⛔ 不改 ask 核心逻辑，播报失败不冒到执行链。
        def _speak_ask(trace_id: str, text: str, room: str) -> None:
            bridge = _bridge.current_bridge()
            if bridge is None or not text:
                return
            area_id = self.resolve_room(room, source="ask")
            if area_id is None:
                return  # DCD T3-AF 硬判据：解析不到 ⇒ 拒投递（已打 AREA_UNRESOLVED）
            bridge.publish_speak(trace_id=trace_id, text=text, room=area_id)

        self.executor.on_ask_speak = _speak_ask
        # T1（2026-11-04）：非 ask 自动化 do 动作下发前同样经桥播报一声（同一 publish_speak 腿）。
        # 与 ask 腿共用闭包语义：惰性取桥、无桥 no-op、失败只吞 warning，⛔ 不改 do 核心。
        # 2026-11-05 修（真机现读）：线上 IR 的 do 节点**没有 room 字段**，而 ② 的硬判据是
        # 「解析不到 ⇒ 拒投递」⇒ 播报腿被自己的闸焊死（watch.log 实证 2 条 AREA_UNRESOLVED）。
        # 修法：room 缺失时用 do 的**目标实体**反查 catalog 的区域（`entity → area_id`），
        # 查到就用；实体也查不到才拒。⛔ 不退回 fail-open。
        def _speak_do(trace_id: str, text: str, room: str, entity_id: str = "") -> None:
            bridge = _bridge.current_bridge()
            if bridge is None or not text:
                return
            area_id = self.resolve_room(room, source="do", entity_id=entity_id)
            if area_id is None:
                return
            bridge.publish_speak(trace_id=trace_id, text=text, room=area_id)

        self.executor.on_do_speak = _speak_do
        # emit 播报（AF-PM 裁定 2026-11-05）：emit 事件此前不走任何播报钩子 ⇒ `study_ac_started`
        # 这类播报永远不出声。与 do **同源同一闭包语义**：三腿（ask/do/emit）同一闸、fail-closed 一致。
        def _speak_emit(trace_id: str, text: str, room: str, entity_id: str = "") -> None:
            bridge = _bridge.current_bridge()
            if bridge is None or not text:
                return
            area_id = self.resolve_room(room, source="emit", entity_id=entity_id)
            if area_id is None:
                return
            bridge.publish_speak(trace_id=trace_id, text=text, room=area_id)

        self.executor.on_emit_speak = _speak_emit
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

        # 区域表懒加载：给了 store_root 就从 `.catalog/catalog.json` 取 `area_id → area_name`；
        # ⛔ 不内置固定房间表（DCD 2026-11-04 T3-AF：唯一真值是 HA 的 area registry，由各仓自己读）。
        self._area_map_cache: dict[str, str] | None = None
        #: `entity_id → area_id` 兜底表（同一份 catalog，2026-11-05：IR 没写 room 时按实体反查）
        self._entity_area_cache: dict[str, str] | None = None

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
                self.audit.add(AuditEvent(type="conf_grading_enabled", at=self.clock.now(), message="conf grading enabled"))
            except Exception as _e:
                self.audit.add(AuditEvent(type="conf_grading_init_failed", at=self.clock.now(), message=str(_e)))
                self.grading = None

    # ── 房间口径（DCD 2026-11-04 T3-AF：跨仓共享的 room 一律 HA area_id）──────
    def _load_area_map(self) -> dict[str, str]:
        """从 `.catalog/catalog.json` 现读 `area_id → area_name`（懒加载 + 缓存一次）。

        ⛔ 不内置任何固定房间表（`homesdk.areas` 也不内置）：唯一真值是 HA 的 area registry，
        由各仓从自己的 HA 读。读不到/损坏 ⇒ 空表（后续解析一律 None ⇒ 拒投递，fail-closed）。
        """
        if self.area_map:
            return self.area_map
        if self._area_map_cache is not None:
            return self._area_map_cache
        out: dict[str, str] = {}
        if self.store_root:
            path = Path(self.store_root) / ".catalog" / "catalog.json"
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                entities = raw.get("entities") if isinstance(raw, dict) else None
                if isinstance(entities, dict):
                    for meta in entities.values():
                        if not isinstance(meta, dict):
                            continue
                        aid = str(meta.get("area_id") or "").strip()
                        aname = str(meta.get("area") or "").strip()
                        if aid and aname and aid not in out:
                            out[aid] = aname
            except (OSError, ValueError):
                out = {}
        self._area_map_cache = out
        return out

    def _load_entity_area_map(self) -> dict[str, str]:
        """从 catalog 现读 `entity_id → area_id`（与 `_load_area_map` 同一次读盘、同一缓存）。"""
        if self._entity_area_cache is not None:
            return self._entity_area_cache
        out: dict[str, str] = {}
        if self.store_root:
            path = Path(self.store_root) / ".catalog" / "catalog.json"
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                entities = raw.get("entities") if isinstance(raw, dict) else None
                if isinstance(entities, dict):
                    for eid, meta in entities.items():
                        if not isinstance(meta, dict):
                            continue
                        aid = str(meta.get("area_id") or "").strip()
                        if aid:
                            out[str(eid)] = aid
            except (OSError, ValueError):
                out = {}
        self._entity_area_cache = out
        return out

    def resolve_room(self, room_text: str, *, source: str, entity_id: str = "") -> str | None:
        """把房间文本解析成 HA `area_id`；解析不到 ⇒ 打 `AREA_UNRESOLVED` 并返回 None。

        `entity_id` 是**兜底腿**（2026-11-05 真机修）：线上 IR 的 do 节点常常根本不写 `room`，
        此时用该 do 的**目标实体**去 catalog 反查它挂在哪个区域（`entity → area_id`）。
        ⇒ 「IR 没写房间」不再等于「不能出声」，但**仍然 fail-closed**：房间与实体都查不到才拒。

        ★ 契约（DCD 裁定 §2.3 硬判据）：None ⇒ 调用方**拒投递**，⛔ 不许静默 accepted
        （AF→DB 发声因房间名对不上会静默不出声、台账还记 accepted，这正是要堵的洞）。
        """
        area_map = self._load_area_map()
        if _areas is None:
            area_id, why = None, "库侧 homesdk.areas 缺席（需 >=0.3.3）"
        else:
            area_id = _areas.resolve(area_map, room_text, self.room_aliases or None)
            why = "房间文本不在 HA 区域表（area_id/别名/中文名三路都不命中）"
        if area_id is None and entity_id:
            # 兜底：IR 没写（或写错了）房间时，按目标实体反查区域
            derived = self._load_entity_area_map().get(str(entity_id).strip())
            if derived:
                area_id, why = derived, ""
        if area_id is None:
            logger.warning(
                "AREA_UNRESOLVED 房间解析不到 HA area_id，拒投递 source=%s room=%r entity=%r 原因=%s 已知区域=%s",
                source, room_text, entity_id, why, sorted(area_map.keys()),
            )
        return area_id

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
        # §十八 B.14：挂起的 ask/确认会话必须重挂，否则实例在 `/api/asks` 上看不见、谁也答不了
        self.executor.reseed_sessions(restored)
        return restored

    # ── 时间 ──────────────────────────────────────────────────────────
    def advance(self, seconds: float) -> list[Instance]:
        """推进虚拟时钟并触发 tick（时间旅行）。仅 VirtualTimeSource 支持。"""
        if not hasattr(self.clock, "advance"):
            raise TypeError(f"当前时间源 {type(self.clock).__name__} 不支持时间旅行")
        self.clock.advance(seconds)  # type: ignore[attr-defined]
        return self.tick()

    def tick(self) -> list[Instance]:
        """时间推进后的统一处理：实例超时 / `for` 到期 / 定时触发 / 队列出队 / 记账清理。"""
        fired = self.scheduler.tick()
        self.instances.expire_stale()
        # 跨日清理原先**没有任何生产调用方**（`sweep()` 写好了却没人按）⇒ `fire_log.json` 按天只增不减。
        # `sweep()` 自带 3600s 单调节流，逐 tick 调用不会重复扫盘。
        recorder = self.scheduler.fire_recorder
        if recorder is not None:
            recorder.sweep()
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
        # 联动旁观者（MQTT 事件流等）。旁观者自己负责把失败留痕，不许把执行链带崩。
        for observer in _bridge.current_observers():
            observer(instance, state)

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
    ma_endpoint: str = "",
    ma_token: str = "",
    quota: Quota | None = None,
    conf: ConfidenceStore | None = None,
    persist_dir: str | None = None,
    store_root: str | None = None,
    area_map: dict[str, str] | None = None,
    room_aliases: dict[str, str] | None = None,
) -> Runtime:
    """按默认组合装配一个 Runtime。

    默认 `dry_run=True`：HA 适配器只记录下发意图，不写真机。
    传入 `persist_dir` 时启用 P1 实例持久化：构造即恢复上次落盘的活跃实例。
    传入 `store_root` 时启用运行期事实落盘（触发计数/canary 试演写 `<archive>.runtime.json`）。
    `area_map`/`room_aliases` 是房间解析表（缺省 ⇒ 从 `store_root` 的 catalog 现读；仍为空 ⇒ 拒投递）。
    """
    states = states if states is not None else InMemoryStateProvider()
    clock = clock if clock is not None else VirtualTimeSource(datetime(2026, 9, 14, 8, 0, tzinfo=timezone.utc))

    registry = AdapterRegistry()
    registry.register(MockAdapter())
    registry.register(HAAdapter(dry_run=dry_run))
    registry.register(HTTPAdapter(allowed_hosts=http_allowed_hosts, dry_run=dry_run))
    # 收件箱投递：`build_runtime` 是唯一的适配器收口点，仿真 / `_replay` / `/api/live/run` 都从这里拿，
    # 所以注册在这里 = 四面（CLI/HTTP/MCP/仿真）同一份能力，没有"某一面接不到 DB"的分裂。
    registry.register(InboxAdapter(dry_run=dry_run))
    # MA 取数：计划 §七 卡2 那格在卡片语言里叫 `ma_query`，落形是 `adapter: ma` + 四条只读 action
    # （AF 的 kind 词表封闭，出向动作只有 `do`+`adapter`+`action` 这一种形态，见 af_adapters/ma.py 头部）。
    # 与 inbox 同一收口点：四面（CLI/HTTP/MCP/仿真）都从这里拿，没有"某一面接不到 MA"的分裂。
    registry.register(MaAdapter(endpoint=ma_endpoint, token=ma_token, dry_run=dry_run))

    return Runtime(
        graph=graph,
        states=states,
        clock=clock,
        adapters=registry,
        quota=quota or Quota(),
        conf=conf or ConfidenceStore(),
        persist_dir=persist_dir,
        store_root=store_root,
        area_map=dict(area_map or {}),
        room_aliases=dict(room_aliases or {}),
    )
