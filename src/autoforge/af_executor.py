"""节点执行器 —— 求值段 / 边优先级 / 挂起 / 中断（KICKOFF §4.5，IR §5-§7）。

执行契约：
1. 激活/恢复 → 拉快照 → 初始化求值上下文
2. 按序执行节点，按**边优先级从高到低**匹配下一条边（不允许按定义顺序 fallback）
3. 遇 `wait`/`ask` → 注册计时器 → 挂起，结束求值段
4. 遇 `pass`/终态 → 终止

其它硬约束：
- `do` 单次调用，不重试不降级；失败走 `on_error`，无 `on_error` 则 `failed`
- 实体漂移（引用不存在的实体）→ `on_error` **软失效** + 漂移告警，**不直接 failed**
- `atomic=true` 的 `do` 必须执行完再响应中断
- 取消**不回滚**已执行动作（IR §13-1），清理必须显式写在 `on_cancel` 分支
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .af_adapters import AdapterRegistry, CallResult
from .af_audit import ACTION_FAILED, ENTITY_DRIFT, EVENT_EMITTED, AuditEvent, AuditLog
from .af_bus import ACCEPTED
from .af_conf import ConfidenceStore
from .af_instance import (
    ACTIVE,
    CANCELLED,
    DONE,
    FAILED,
    SUSPENDED,
    Instance,
    InstanceManager,
)
from .af_ir import Automation, Node, AskSpec, AskAnswer, evaluate
from .af_ir.expr import ExprError
from .af_state import StateProvider, UnknownEntity, make_resolver
from .af_time import TimeSource, SystemTimeSource, parse_duration

import logging

logger = logging.getLogger("autoforge.executor")

__all__ = [
    "AskSession",
    "NodeExecutor",
    "MAX_STEPS_PER_SEGMENT",
    "classify_answer",
    "EMIT_TIMER_KIND",
]

#: 求值段最大步数——静态图死循环的最后一道防线（静态扫描应提前拦住）
MAX_STEPS_PER_SEGMENT = 1000

#: v2 收敛纪律：同一实例连续 ask 轮数上限（一次成功的 `do` 即清零）
MAX_ASK_ROUNDS = 3
#: v2 收敛纪律：同一房间同一时刻挂起问题数上限（跨实例合计）
MAX_ASKS_PER_ROOM = 2

#: 收敛违约 / 结构化应答被拒的审计事件类型
ASK_VIOLATION = "ask_convergence_violation"

#: v0.3.0：`emit` 延迟发布所用的实例定时器 kind（与 `wait`/`ask` 的 timeout 区分）
EMIT_TIMER_KIND = "emit"

# WO-AF-001：同意判定收口到 homesdk.consent，本仓不再持有词表
from homesdk.consent import YES as _HOMESDK_YES, classify_answer as _homesdk_classify


def classify_answer(text: str) -> str:
    """yes / no（IR §5.2）。判定口径来自 homesdk，本仓不再持有词表。

    homesdk 返回 yes / no / unknown；AF 的 IR 只有两条出口，
    unknown 映射到 no（收紧：听不清不执行，而不是走 default 边放行）。
    """
    return "yes" if _homesdk_classify(text) == _HOMESDK_YES else "no"


@dataclass
class AskSession:
    """一个挂起中的 `ask` 会话。按 room 维度匹配，创建时间优先，一次应答仅生效一次。

    v2 M3：携带 `ask_spec`（结构化挂起规格），供运行时渲染原生控件元数据并强制收敛纪律。
    """

    instance_id: str
    node_id: str
    room: str | None
    created_at: float
    prompt: str = ""
    ask_spec: "AskSpec | None" = None


@dataclass
class NodeExecutor:
    """7 节点 / 6 边的求值引擎。"""

    instances: InstanceManager
    adapters: AdapterRegistry
    clock: TimeSource = field(default_factory=SystemTimeSource)
    audit: AuditLog = field(default_factory=AuditLog)
    states: StateProvider | None = None
    conf: ConfidenceStore | None = None
    #: v0.3.0 发布侧：自定义事件经总线发布；未注入总线时只审计（便于单测隔离）
    bus: Any | None = None
    #: v0.4.0 订阅侧：发布成功后的回调（Runtime 注入 `scheduler.handle_event`），
    #: 让 emit 出去的事件能真正驱动订阅它的自动化（发布 → 订阅闭环）
    on_emit: Any | None = None

    def __post_init__(self) -> None:
        self.pending_asks: dict[str, AskSession] = {}
        self.node_visits: list[str] = []

    # ─────────────────────────────────────────────────────────────────
    # 主循环
    # ─────────────────────────────────────────────────────────────────
    def run(self, instance: Instance) -> Instance:
        """执行一个求值段：从 `current_node` 走到挂起或终态。"""
        auto = instance.automation
        steps = 0
        while True:
            steps += 1
            if steps > MAX_STEPS_PER_SEGMENT:
                self._fail(instance, "求值段超过最大步数，疑似静态图死循环")
                return instance

            node_id = instance.ctx.current_node
            if not node_id:
                self._terminate(instance)
                return instance

            node = auto.node(node_id)
            instance.trace(node_id, note="enter")
            self.node_visits.append(f"{auto.id}:{node_id}")

            # v0.3.0 发布侧：`emit` 是节点字段，进入节点时先广播（"发出事件后继续"）
            if node.emit is not None and not self._emit(instance, node):
                return instance  # 已转入延迟发布（挂起）

            if node.kind == "pass":
                self._terminate(instance)
                return instance

            if node.kind in ("ask", "wait"):
                self._suspend(instance, node)
                return instance

            kinds = self._execute(instance, node)
            if kinds is None:  # 已终止（失败且无兜底边）
                return instance

            edge = auto.pick_edge(node.id, kinds)
            if edge is None:
                self._terminate(instance)
                return instance

            instance.ctx.current_node = edge.to

            # atomic do 执行完后才响应中断
            if instance.ctx.context.pop("cancel_pending", None) is not None:
                self.cancel(instance, reason=instance.ctx.context.get("cancel_reason", "atomic 结束后取消"))
                return instance

    # ─────────────────────────────────────────────────────────────────
    # 唤醒：应答 / 超时 / 取消 都走这里，统一按边优先级选边
    # ─────────────────────────────────────────────────────────────────
    def resume(self, instance: Instance, kind: str) -> Instance:
        """用给定的边类型唤醒挂起实例（yes / no / default / on_timeout / on_cancel）。"""
        # B3-AF-02: pop pending_asks BEFORE terminal check (was after, leaving ghost in queue)
        self.pending_asks.pop(instance.instance_id, None)
        if instance.is_terminal:
            return instance
        auto = instance.automation
        node = auto.node(instance.ctx.current_node)

        if instance.state == SUSPENDED:
            self.instances.resume(instance)  # 恢复时重新取快照

        # P1-11：canary 观察期结束 → 检查漂移并回滚
        # P1-4 修复：pending_canary 现在是可序列化字典。崩溃恢复后据此重建 CanaryResult
        # （用实时 states 作为 guard，pre_snapshot 作为回滚基准），避免 default=str 静默字符串化
        # 后 `wrapped, adapter = "<...>"` 解包成字符、回滚静默失效。
        pending_canary = instance.ctx.context.pop("pending_canary", None)
        if pending_canary is not None and kind == "on_timeout":
            try:
                if not isinstance(pending_canary, dict):
                    raise ValueError(f"pending_canary 格式非法（应为 dict，实为 {type(pending_canary).__name__}）：{pending_canary!r}")
                adapter = self.adapters.get(pending_canary.get("adapter") or "")
                if adapter is None:
                    self.audit.add(
                        AuditEvent(
                            type=ENTITY_DRIFT,
                            at=self.clock.now(),
                            message=f"canary 观察期恢复：适配器 {pending_canary.get('adapter')!r} 缺失，跳过漂移回滚（安全失败，不阻断流程）",
                            automation_id=instance.automation.id,
                            instance_id=instance.instance_id,
                            node_id=node.id,
                        )
                    )
                else:
                    from types import SimpleNamespace

                    from .af_canary import CanaryResult

                    wrapped = CanaryResult(
                        action=pending_canary.get("action", ""),
                        params=pending_canary.get("params", {}),
                        result=None,  # 恢复路径不依赖 result，has_drift/rollback 只用到 guard.states 与 pre_snapshot
                        pre_states=pending_canary.get("pre_snapshot", {}),
                        guard=SimpleNamespace(states=self.states),
                        pre_snapshot=pending_canary.get("pre_snapshot", {}),
                    )
                    if wrapped.expected_state() is None:
                        self._feed_canary_evidence(instance, node, "unmodeled", {
                            "reason": "观察期动作无 SERVICE_STATE 映射，漂移不可判",
                        })
                    elif wrapped.has_drift():
                        rolled = wrapped.rollback(adapter)
                        self._feed_canary_evidence(instance, node, "failed", {
                            "reason": "canary 观察期漂移，已自动回滚", "rollback_calls": len(rolled),
                        })
                        self.audit.add(
                            AuditEvent(
                                type=ENTITY_DRIFT,
                                at=self.clock.now(),
                                message=f"canary 观察期检测到漂移 {wrapped.action}，已自动回滚（{len(rolled)} 次反向下发）",
                                automation_id=instance.automation.id,
                                instance_id=instance.instance_id,
                                node_id=node.id,
                                data={"params": dict(wrapped.params)},
                            )
                        )
                    else:
                        self._feed_canary_evidence(instance, node, "verified")
            except Exception:
                logging.getLogger("autoforge.executor").exception("canary 漂移检查失败（已隔离，不阻断流程）")

        edge = auto.pick_edge(node.id, {kind, "default"})
        if edge is None:
            self._terminate(instance)
            return instance
        instance.ctx.current_node = edge.to
        return self.run(instance)

    def resolve_ask(self, ask_id: str | None, room: str | None) -> AskSession | None:
        """WO-AF-002：ask 应答解析器（唯一入口，仓内不留第二份匹配逻辑）。

        ask_id 精确命中字典键（= instance_id）。ask_id 给了却不命中 → None，**绝不 fallback**。
        ask_id 为 None 时才允许按 room 取最旧。

        不变式：一个实例同一时刻只有一个挂起 ask（af_service.py:1063 注释声明）。
        """
        if ask_id:
            return self.pending_asks.get(ask_id)
        candidates = [s for s in self.pending_asks.values() if s.room == room] if room else list(self.pending_asks.values())
        return min(candidates, key=lambda s: s.created_at) if candidates else None

    def answer(self, room: str | None, text: str, ask_id: str | None = None) -> Instance | None:
        """人类应答。WO-AF-002：统一走 resolve_ask，ask_id 不匹配绝不 fallback。"""
        import logging
        logger = logging.getLogger("autoforge.executor")

        session = self.resolve_ask(ask_id, room)
        if session is None:
            if ask_id:
                logger.warning("answer: ask_id=%r 不匹配任何挂起 ask，拒绝 fallback", ask_id)
            else:
                logger.warning("answer: room=%r 无挂起 ask", room)
            return None
        instance = self.instances.get(session.instance_id)
        if instance is None:
            logger.warning("answer: session.instance_id=%r 对应实例不存在", session.instance_id)
            return None
        verdict = classify_answer(text)
        import homesdk
        logger.info(
            "consent resolved: ask verdict=%s text_len=%d text_head=%r homesdk=%s",
            verdict, len(text or ""), (text or "")[:2], getattr(homesdk, "__version__", "?"),
        )
        # B3-AF-02: terminal instance (expired) -> pop ghost, return None so caller keeps evidence file
        # 用 `is True` 而非 truthy：真实 Instance.is_terminal 返回 bool，Mock 对象返回非 bool
        if getattr(instance, "is_terminal", False) is True:
            self.pending_asks.pop(instance.instance_id, None)
            logger.warning("answer: instance %s is terminal, popping ghost ask", instance.instance_id)
            return None
        return self.resume(instance, verdict)

    # ─────────────────────────────────────────────────────────────────
    # v2 M3 结构化 Ask：一等公民结构化应答（自由文本 answer 仍保留）
    # ─────────────────────────────────────────────────────────────────
    def answer_structured(self, room: str | None, payload: "AskAnswer | dict", ask_id: str | None = None) -> Instance | None:
        """结构化应答。payload 为 AskAnswer 或 dict。

        缺省即拒：应答不满足 ask_spec（缺失/越界/类型不符）→ 不脑补默认值，**拒绝并
        保持挂起**（返回 None），让人用合法值重答。不把"填错值"当成"拒绝执行"走 `no`
        边——`no` 是图作者定义的"用户明确不要"语义，二者不能混同。

        校验通过 → 值落 `ctx.vars["ask_answer"]`（业务变量区，下游 `if`/`set` 可引用），
        并以 `then` 边恢复正常流转：结构化值是参数收集（温度/时间段），没有天然的
        yes/no 语义，不该由运行时猜图作者的意图。

        返回 None 有二义：ask 不存在/实例已终态，或校验被拒。调用方若要区分（如
        映射 422），先用 `validate_answer()` 预检。
        """
        session = self.resolve_ask(ask_id, room)
        if session is None:
            return None
        instance = self.instances.get(session.instance_id)
        if instance is None:
            return None
        if isinstance(payload, dict):
            payload = AskAnswer.from_dict(payload)
        if not self._validate_structured(payload, session.ask_spec):
            self.audit.add(
                AuditEvent(
                    type=ASK_VIOLATION,
                    at=self.clock.now(),
                    message=(
                        f"结构化应答未通过校验（缺省即拒，会话保持挂起可重答）："
                        f"spec={session.ask_spec}, answer={payload}"
                    ),
                    automation_id=instance.automation.id,
                    instance_id=instance.instance_id,
                    node_id=session.node_id,
                )
            )
            return None
        instance.ctx.vars["ask_answer"] = {"kind": payload.kind, "value": payload.value}
        return self.resume(instance, "then")

    def validate_answer(self, room: str | None, payload: "AskAnswer | dict", ask_id: str | None = None) -> bool:
        """预检结构化应答是否合规（不消费 ask，不恢复实例）。

        供 service 层在调用 `answer_structured` 前区分"ask 不存在"与"校验被拒"，
        以便前者 404、后者 422。
        """
        session = self.resolve_ask(ask_id, room)
        if session is None:
            return False
        if isinstance(payload, dict):
            payload = AskAnswer.from_dict(payload)
        return self._validate_structured(payload, session.ask_spec)

    @staticmethod
    def _validate_structured(payload: "AskAnswer", spec: "AskSpec | None") -> bool:
        """按 ask_spec 校验结构化应答（缺省即拒）。"""
        if spec is None:
            # 自由文本 ask：应答须为非空文本
            return bool(payload.value)
        if payload.kind != spec.kind:
            return False
        v = payload.value
        if spec.kind == "choice":
            return v in spec.options
        if spec.kind == "threshold":
            if v is None:
                return False
            try:
                fv = float(v)
            except (TypeError, ValueError):
                return False
            if spec.min_ is not None and fv < spec.min_:
                return False
            if spec.max_ is not None and fv > spec.max_:
                return False
            return True
        if spec.kind == "time_range":
            # min/max 与 threshold 同语义：是**应答值域约束**（可选窗口），不只是控件刻度。
            # 单位一律分钟 [0,1439]；控件保证选得出合法值，此处再兜底防御非法客户端。
            if not isinstance(v, (list, tuple)) or len(v) != 2:
                return False
            s, e = v
            if spec.min_ is not None and (s < spec.min_ or e < spec.min_):
                return False
            if spec.max_ is not None and (s > spec.max_ or e > spec.max_):
                return False
            return s <= e
        if spec.kind == "entity":
            return bool(v)
        if spec.kind == "text":
            return bool(v)
        return False

    def timeout(self, instance: Instance) -> Instance:
        """`ask` 计时到点（无人应答）→ `on_timeout` 兜底。"""
        return self.resume(instance, "on_timeout")

    def sweep_orphans(self) -> int:
        """mimo SuspendManager 增量：清理终态实例残留的 pending_asks（防 B3-AF-02 幽灵）。

        定期调用（如每次 tick 后），遍历 pending_asks，对应实例已终态则 pop。
        返回清理的幽灵数量。
        """
        ghosts = []
        for inst_id in list(self.pending_asks.keys()):
            inst = self.instances.get(inst_id)
            if inst is None or getattr(inst, "is_terminal", False) is True:
                ghosts.append(inst_id)
        for inst_id in ghosts:
            self.pending_asks.pop(inst_id, None)
        if ghosts:
            import logging
            logging.getLogger("autoforge.executor").warning(
                "sweep_orphans: cleaned %d ghost pending_asks: %s", len(ghosts), ghosts
            )
        return len(ghosts)

    def resume_then(self, instance: Instance) -> Instance:
        """`wait` 计时到点 → 走 `then` 正常继续（语义拍板 A：wait 是"等一会儿继续"，不是超时）。"""
        return self.resume(instance, "then")

    def cancel(self, instance: Instance, reason: str = "") -> Instance:
        """中断。优先级最高：抢占一切正常流程，走 `on_cancel` 分支。"""
        if instance.is_terminal:
            return instance
        auto = instance.automation
        node = auto.node(instance.ctx.current_node)

        # atomic do：必须执行完再跳转
        if node.kind == "do" and node.atomic and instance.state == ACTIVE:
            instance.ctx.context["cancel_pending"] = True
            instance.ctx.context["cancel_reason"] = reason
            return instance

        instance.ctx.context["pending_terminal"] = CANCELLED
        instance.ctx.context["cancel_reason"] = reason
        return self.resume(instance, "on_cancel")

    # ─────────────────────────────────────────────────────────────────
    # 节点执行
    # ─────────────────────────────────────────────────────────────────
    def _execute(self, instance: Instance, node: Node) -> Iterable[str] | None:
        """执行单个节点，返回可用边类型集合；返回 None 表示实例已终止。"""
        self._reject_reserved(node)

        if node.kind == "on":
            return {"then"}

        if node.kind == "if":
            try:
                value = evaluate(node.expr or {}, self._resolver(instance))
                logger.debug("[IF] %s expr=%s → value=%r", node.id, node.expr, value)
            except (UnknownEntity, ExprError, KeyError, ValueError) as exc:
                logger.warning("[IF] %s ERROR: %r", node.id, exc)
                return self._soft_fail(instance, node, exc)
            return {"then"} if value else {"no", "default"}

        if node.kind == "set":
            value = self._value_of(instance, node)
            self.instances.set_var(instance, node.var, value)  # type: ignore[arg-type]
            return {"then"}

        if node.kind == "do":
            return self._do(instance, node)

        raise ValueError(f"未知节点类型：{node.kind}")

    def _feed_canary_evidence(
        self, instance: Instance, node: Node, status: str, detail: dict[str, Any] | None = None
    ) -> None:
        """F4：把 canary 的生产态结论喂进 af_watch（诚实报告 `verified_in_prod` 分区）。

        聚合失败不得影响执行，但也不许静默咽下——原因要进日志，否则"一条证据没记下"
        看起来和"记好了"一样（与 af_shadow 留 `watch_record_error` 同一口径）。
        """
        try:
            from . import af_watch

            af_watch.record_canary(
                instance.automation.id, status, self.clock.now(),
                {"node_id": node.id, "action": node.action or "", **(detail or {})},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("af_watch 聚合 canary 证据失败（不影响执行）：%r", exc)

    def _do(self, instance: Instance, node: Node) -> Iterable[str] | None:
        """`do`：**单次调用**，不重试不降级。"""
        try:
            adapter = self.adapters.get(node.adapter or "")
        except Exception as exc:  # 适配器未注册
            return self._soft_fail(instance, node, exc)

        # G4 canary：conf 处于 auto 带且节点标了 canary → 走灰度保护
        # ⚠️ dry-run 适配器（G1 默认不写真机）不改状态，会**永远**被判定为漂移，必须跳过
        canary = node.canary
        dry_run = bool(getattr(adapter, "dry_run", False))
        use_canary = (
            canary
            and not dry_run
            and self.conf is not None
            and self.conf.band(instance.automation.id) == "auto"
            and self.states is not None
        )
        if use_canary:
            from .af_canary import CanaryGuard
            from .af_time import parse_duration

            guard = CanaryGuard(
                self.states,
                auto_rollback=bool(canary.get("auto_rollback", True)) if isinstance(canary, dict) else True,
            )
            wrapped = guard.perform(adapter, node.action or "", dict(node.params))
            # P1-11：canary.duration 接入——动作下发后挂起观察 duration，超时恢复时检查漂移
            canary_duration = None
            if isinstance(canary, dict):
                dur_str = canary.get("duration")
                if dur_str:
                    try:
                        canary_duration = parse_duration(dur_str)
                    except (ValueError, TypeError):
                        pass
            if canary_duration and canary_duration > 0:
                # 挂起观察：存可序列化元数据（P1-4 修复：原存 (wrapped, adapter) 对象不可序列化，
                # 崩溃恢复后 default=str 静默字符串化 → 解包成字符 → 回滚静默失效）。恢复路径据此重建。
                instance.ctx.context["pending_canary"] = {
                    "action": node.action or "",
                    "params": dict(node.params),
                    "pre_snapshot": wrapped.pre_snapshot,
                    "adapter": node.adapter or "",
                }
                self.instances.suspend(instance, node.id, canary_duration, kind="canary_observe")
                return None
            # 无 duration → 立即检查漂移（原行为）
            if wrapped.expected_state() is None:
                # F4：动作没有 SERVICE_STATE 映射 → has_drift() 恒 False，**不是**"验过了"，
                # 是"无从验"。记成 verified 就是 铁律 #5 的假证据，单列 unmodeled 一档。
                self._feed_canary_evidence(instance, node, "unmodeled", {
                    "reason": "SERVICE_STATE 无该动作的预期态，漂移不可判",
                })
            elif wrapped.has_drift():
                rolled = guard.check_and_rollback(adapter, wrapped)
                self._feed_canary_evidence(instance, node, "failed", {
                    "reason": "canary 漂移，已自动回滚", "rollback_calls": len(rolled),
                })
                self.audit.add(
                    AuditEvent(
                        type=ENTITY_DRIFT,
                        at=self.clock.now(),
                        message=f"canary 检测到漂移 {node.action}，已自动回滚（{len(rolled)} 次反向下发）",
                        automation_id=instance.automation.id,
                        instance_id=instance.instance_id,
                        node_id=node.id,
                        data={"params": dict(node.params)},
                    )
                )
                return self._soft_fail(instance, node, RuntimeError(f"canary 漂移，已回滚 {node.action}"))
            else:
                self._feed_canary_evidence(instance, node, "verified")
            result = wrapped.result
        else:
            result: CallResult = adapter.call(node.action or "", dict(node.params))

        # 结果只进 vars，不回写快照（IR §7.2）
        if node.result_var:
            instance.ctx.vars[node.result_var] = {
                "success": result.success,
                "data": result.data,
                "error": result.error,
            }

        if result.success:
            # 一次真实动作落地 → 视为新一轮交互上下文，连问计数清零
            instance.ctx.context["_ask_rounds"] = 0
            return {"then"}

        self.audit.add(
            AuditEvent(
                type=ACTION_FAILED,
                at=self.clock.now(),
                message=f"动作 {node.adapter}.{node.action} 失败：{result.error}",
                automation_id=instance.automation.id,
                instance_id=instance.instance_id,
                node_id=node.id,
                data={"params": dict(node.params)},
            )
        )
        kinds = ["on_error", "default"]
        if instance.automation.pick_edge(node.id, kinds) is None:
            self._fail(instance, f"{node.action} 失败且无 on_error/default 兜底：{result.error}")
            return None
        return set(kinds)

    def _soft_fail(self, instance: Instance, node: Node, exc: BaseException) -> Iterable[str] | None:
        """实体漂移 / 表达式错误 → 软失效：走 `on_error`，无则 failed（**不直接 failed**）。"""
        entity_id = exc.args[0] if isinstance(exc, UnknownEntity) and exc.args else ""
        self.audit.add(
            AuditEvent(
                type=ENTITY_DRIFT,
                at=self.clock.now(),
                message=f"节点 {node.id} 求值失败（实体漂移或类型错误）：{exc}",
                automation_id=instance.automation.id,
                instance_id=instance.instance_id,
                node_id=node.id,
                entity_id=str(entity_id),
            )
        )
        kinds = ["on_error", "default"]
        if instance.automation.pick_edge(node.id, kinds) is None:
            self._fail(instance, f"节点 {node.id} 软失效且无 on_error/default 兜底：{exc}")
            return None
        return set(kinds)

    # ─────────────────────────────────────────────────────────────────
    # 挂起
    # ─────────────────────────────────────────────────────────────────
    def _ask_budget_ok(self, instance: Instance, node: Node) -> bool:
        """收敛纪律（v2）：同一实例连续 ask ≤3 轮；同一房间同一时刻挂起 ≤2（跨实例）。

        "一轮多问"在运行时唯一可观测的形态就是**同房间并发挂起**——仓内不变式是
        "一实例同时只挂起一个 ask"，单实例不存在"一轮多问"。违约 → 审计 + 实例
        failed，不挂起（不脑补、不降级）。
        """
        rounds = int(instance.ctx.context.get("_ask_rounds", 0))
        if rounds + 1 > MAX_ASK_ROUNDS:
            self._ask_violation(instance, node, f"连续 ask 超过 {MAX_ASK_ROUNDS} 轮")
            return False
        room = node.room
        if room:
            same_room = sum(1 for s in self.pending_asks.values() if s.room == room)
            if same_room + 1 > MAX_ASKS_PER_ROOM:
                self._ask_violation(instance, node, f"房间 {room} 同时挂起超过 {MAX_ASKS_PER_ROOM} 个 ask")
                return False
        return True

    def _ask_violation(self, instance: Instance, node: Node, reason: str) -> None:
        self.audit.add(
            AuditEvent(
                type=ASK_VIOLATION,
                at=self.clock.now(),
                message=f"收敛纪律违反：{reason}",
                automation_id=instance.automation.id,
                instance_id=instance.instance_id,
                node_id=node.id,
            )
        )
        self._fail(instance, f"收敛纪律违反：{reason}")

    def _suspend(self, instance: Instance, node: Node) -> None:
        # 先判后挂：违约直接 failed，不留"已挂起又 failed"的脏状态
        if node.kind == "ask" and not self._ask_budget_ok(instance, node):
            return

        duration = None
        try:
            if node.kind == "wait":
                duration = parse_duration(node.duration)  # type: ignore[arg-type]
            elif node.timeout:
                duration = parse_duration(node.timeout)
        except (ValueError, TypeError) as exc:
            # P2-1 修复：duration/timeout 非法（如 "abch"）原会冒泡到 run() → tick 失败 →
            # 整个调度循环停摆。此处就地软失效（走 on_error/default），不污染调度循环。
            self._soft_fail(instance, node, exc)
            return

        self.instances.suspend(instance, node.id, duration, kind=node.kind)

        if node.kind == "ask":
            instance.ctx.context["_ask_rounds"] = int(instance.ctx.context.get("_ask_rounds", 0)) + 1
            self.pending_asks[instance.instance_id] = AskSession(
                instance_id=instance.instance_id,
                node_id=node.id,
                room=node.room,
                created_at=self.clock.monotonic(),
                prompt=node.prompt,
                ask_spec=node.ask,
            )

    # ─────────────────────────────────────────────────────────────────
    # 终止
    # ─────────────────────────────────────────────────────────────────
    def _terminate(self, instance: Instance) -> None:
        """走到终点。若之前请求过取消，则落到 cancelled（取消不回滚，只标记状态）。"""
        pending = instance.ctx.context.pop("pending_terminal", None)
        if pending == CANCELLED:
            if instance.state == SUSPENDED:
                self.instances.resume(instance)
            self.instances.cancel(instance, instance.ctx.context.get("cancel_reason", ""))
        else:
            if instance.state == SUSPENDED:
                self.instances.resume(instance)
            self.instances.done(instance)

    def _fail(self, instance: Instance, reason: str) -> None:
        if instance.state == SUSPENDED:
            self.instances.resume(instance)
        self.instances.fail(instance, reason)

    # ─────────────────────────────────────────────────────────────────
    # 工具
    # ─────────────────────────────────────────────────────────────────
    def _resolver(self, instance: Instance):
        snapshot = instance.snapshot
        if snapshot is None:  # 防御：正常流程下 spawn/resume 都会带快照
            snapshot = self.instances.states.snapshot(sorted(instance.automation.reads()))
        return make_resolver(snapshot, instance.ctx.vars, instance.ctx.context)

    def _value_of(self, instance: Instance, node: Node) -> Any:
        """`set` 节点的取值：优先 `from`（变量引用），否则 `value` 字面量。"""
        if node.from_:
            return self._resolver(instance)(node.from_, None)
        return node.value

    @staticmethod
    def _reject_reserved(node: Node) -> None:
        """保留位：读到即报未实现，不静默忽略（v0.3.0 起只剩 `fn`）。"""
        if node.reserved:
            keys = ", ".join(sorted(node.reserved))
            raise NotImplementedError(f"未实现节点保留字段（{keys}）：{node.id}")

    # ─────────────────────────────────────────────────────────────────
    # v0.3.0 跨自动化事件 · 发布侧（IR §4.3）
    # ─────────────────────────────────────────────────────────────────
    def _emit(self, instance: Instance, node: Node) -> bool:
        """发布 `emit` 声明的自定义事件。

        返回 False 表示实例已挂起（带 `delay` 的延迟发布），调用方应结束求值段。
        `delay` 走实例定时器 + `TimeSource`，因此时间旅行可测（禁止 `time.sleep`）。
        """
        emit = node.emit
        if emit is None:
            return True

        if emit.delay:
            delay_s = parse_duration(emit.delay)
            if delay_s > 0:
                # 待发布信息必须可 JSON 序列化（IR §3 红线）
                instance.ctx.context["pending_emit"] = {
                    "node": node.id,
                    "event": emit.event,
                    "data": dict(emit.data),
                }
                self.instances.suspend(instance, node.id, delay_s, kind=EMIT_TIMER_KIND)
                return False

        self._publish_emit(instance, node, emit.event, dict(emit.data))
        return True

    def emit_due(self, instance: Instance) -> Instance:
        """`emit` 的延迟到点：发布事件后按 `then` 继续流转（**不是** `on_timeout`）。"""
        if instance.is_terminal:
            return instance
        auto = instance.automation
        node = auto.node(instance.ctx.current_node)
        pending = instance.ctx.context.pop("pending_emit", None)
        if pending is not None:
            self._publish_emit(
                instance, node, str(pending.get("event", "")), dict(pending.get("data") or {})
            )
        if instance.state == SUSPENDED:
            self.instances.resume(instance)
        edge = auto.pick_edge(node.id, {"then", "default"})
        if edge is None:
            self._terminate(instance)
            return instance
        instance.ctx.current_node = edge.to
        return self.run(instance)

    def _publish_emit(self, instance: Instance, node: Node, event: str, data: dict) -> None:
        """实际发布 + 审计。

        ⚠️ 被限流（`throttled`）或熔断（`breaker_open`）**不视为失败**——
        那是系统保护，不是自动化逻辑错误，因此**不进 `on_error`**，只落审计。
        """
        result = "no_bus"
        if self.bus is not None:
            result = self.bus.publish_custom(event, data)
            # v0.4.0：发布成功后交给调度器，驱动 `on event` 订阅者（emit → on event 闭环）
            if result == ACCEPTED and self.on_emit is not None and self.bus.emitted:
                self.on_emit(self.bus.emitted[-1])
        self.audit.add(
            AuditEvent(
                type=EVENT_EMITTED,
                at=self.clock.now(),
                message=f"发布事件 {event}（结果：{result}）",
                automation_id=instance.automation.id,
                instance_id=instance.instance_id,
                node_id=node.id,
                data={"event": event, "result": result, "keys": sorted(data)},
            )
        )
