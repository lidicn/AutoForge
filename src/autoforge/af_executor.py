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

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

_logger = logging.getLogger(__name__)

from .af_adapters import AdapterRegistry, CallResult
from .af_audit import (
    ACTION_CALLED,
    ACTION_FAILED,
    ENTITY_DRIFT,
    EVENT_EMITTED,
    INSTANCE_NO_ACTION,
    INSTANCE_NO_EDGE,
    INSTANCE_SESSION_LOST,
    AuditEvent,
    AuditLog,
)
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
    "MAX_SEGMENTS_PER_INSTANCE",
    "MAX_STEPS_PER_INSTANCE",
    "HARD_CAP_MULTIPLIER",
    "CAP_WARNING",
    "classify_answer",
    "EMIT_TIMER_KIND",
    "SPEECH_TEMPLATE_PLACEHOLDERS",
    "render_speech_template",
]

# ── D11（2026-11-06）：播报文案模板 ─────────────────────────────────────
#: `speech_template` 支持的占位符**白名单**（初始只 `{temperature}`）。
#: ⚠️ 这份清单是 UI 侧编辑器校验的唯一真源：新增占位符必须同批给 AF-UI，
#: ⛔ 两边各编一套（卡面「占位符白名单以 Core 落 DSL 时给的清单为准」）。
SPEECH_TEMPLATE_PLACEHOLDERS = frozenset({"temperature"})

#: 模板占位符语法：`{name}`，name 为标识符。**只认白名单**——未知占位符一律视为"渲不出来"，
#: 由调用方回退静态文案（fail-closed：⛔ 不播占位符原文、⛔ 不播 unknown）。
_SPEECH_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def render_speech_template(template: str, values: Mapping[str, str]) -> str | None:
    """把模板里的白名单占位符换成实时值；渲不出来返回 `None`（调用方回退静态文案）。

    纯函数（不碰状态源）：值由调用方解析好后传入，便于单测与"实体不可读"分支独立验证。
    返回 None 的三种情形：① 出现白名单外的占位符 ② 有占位符缺值 ③ 模板为空/非字符串。
    """
    if not isinstance(template, str) or not template.strip():
        return None
    names = set(_SPEECH_PLACEHOLDER_RE.findall(template))
    if not names or not names <= SPEECH_TEMPLATE_PLACEHOLDERS:
        return None
    for name in names:
        if values.get(name) in (None, ""):
            return None
    return _SPEECH_PLACEHOLDER_RE.sub(lambda m: str(values[m.group(1)]), template)

#: 求值段最大步数——静态图死循环的最后一道防线（静态扫描应提前拦住）
MAX_STEPS_PER_SEGMENT = 1000

# ── DCD 裁定 20261006 §二 问题一（A+B 组合）：跨段累计封顶 ──────────────────
# 阈值与硬倍数由 DCD 定，AF 不自签。`steps` 是 `run()` 的局部量、每段归零，所以段内封顶
# 拦不住"经挂起点的环"——`wait → do → wait` 是合法 IR（`af_ir/models.py:373-376` `is_suspending`
# 只给 WARNING），实测 2000 段 / 4003 条 trace / 2000 次真实下发 / 0 失败 / 0 审计事件。
# 今天这两档的形状：**越警戒档先告警**（WARNING + AuditLog + 监护视图常驻指示，只发一次），
# **到 2× 才 `_fail`**——纯等待型自动化合法地转很多段，不能一越档就杀。
MAX_SEGMENTS_PER_INSTANCE = 1000
MAX_STEPS_PER_INSTANCE = 20000
HARD_CAP_MULTIPLIER = 2

#: 跨段累计超警戒档的审计事件类型（与 `ASK_VIOLATION` 同族：运行期诊断要留痕，不能只打在日志里）
CAP_WARNING = "instance_cumulative_cap_warning"

#: v2 收敛纪律：同一实例连续 ask 轮数上限（一次成功的 `do` 即清零）
MAX_ASK_ROUNDS = 3
#: v2 收敛纪律：同一房间同一时刻挂起问题数上限（跨实例合计）
MAX_ASKS_PER_ROOM = 2

#: 收敛违约 / 交叉通道误用的审计事件类型（C-2 设计稿 §B.3：结构化会话被喂自由文本、
#: 二元会话被喂结构化 payload ⇒ 拒、保持挂起、留这条痕）
ASK_VIOLATION = "ask_convergence_violation"
#: C-2（设计稿 §F.1）：结构化应答被校验拒绝（缺省即拒、保持挂起可重答）。
#: 从 ASK_VIOLATION 拆出：「填错值」≠「收敛纪律违反」，两件事在审计里必须可数分。
ASK_REJECTED = "ask_answer_rejected"
#: C-2：一次会话被成功消费恰一条（answer/answer_structured 校验通过、进入边选路前）。
#: 重复应答不再产生（出队即消费），"一问一答"在审计里对得平。
ASK_RESOLVED = "ask_resolved"
#: C-2：会话未经应答就消亡。reason ∈ {timeout, terminal, sweep}（超时到点 / 终态幽灵 /
#: sweep_orphans 兜底清扫）。与 ASK_RESOLVED 互斥：一条会话要么被答掉，要么过期。
ASK_EXPIRED = "ask_expired"

#: v0.3.0：`emit` 延迟发布所用的实例定时器 kind（与 `wait`/`ask` 的 timeout 区分）
EMIT_TIMER_KIND = "emit"

# WO-AF-001：同意判定收口到 homesdk.consent，本仓不再持有词表
from homesdk.consent import YES as _HOMESDK_YES, classify_answer as _homesdk_classify

# v1.28 R1（DCD 裁定 2026-11-04）：ask 双向桥的来源标与"上游已分类"枚举的唯一真源。
from .af_ask_bridge import DEFAULT_ANSWERED_VIA, VERDICTS, default_ask_timeout_s


def _speech_value_text(val: Any) -> str:
    """播报里的读数文本（v1.27.1 口径，卡 §修法 5）：整数读数不拖 `.0`，其余原样。

    `28.0` ⇒ `28`（出资人听到的是"28 度"，⛔ 不念"二十八点零"）；`28.6` ⇒ `28.6`（照实念）；
    非数值（`"on"`/`"unknown"`/空）原样返回字符串——⛔ 不把非数值当 0 或空吞掉。
    """
    text = str(val)
    try:
        num = float(text)
    except (TypeError, ValueError):
        return text
    if num.is_integer() and abs(num) < 1e15:
        return str(int(num))
    return text


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
    #: v1.28 R1（PM 契约）：最近一次**到达但未消费**的应答来源标（`xiaomi`/`webui`）。
    #: 例：结构化会话被喂自由文本 ⇒ 拒收、保持挂起，但"人确实答过一次"这条事实留着，
    #: 给 UI 在在途卡上显示来源；⛔ 缺失就是 None（UI 不显示标签、不猜）。
    answered_via: str | None = None


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
    #: ask 触发时的额外 MQTT 播报回调（DCD 裁定 2026-11-04 阶段1 乙）：
    #: `(trace_id, text, room) -> None`；未注入（无桥/单测）时零副作用，⛔ 不影响 ask 核心逻辑。
    on_ask_speak: Any | None = None
    #: v1.28 R1：ask 挂起时向 `af/ask/pending` 发布挂起问询（DCD 裁定 2026-11-04 阶段2 C1/C3）：
    #: `(trace_id, text, room, timeout_remaining) -> None`；未注入时零副作用。
    #: ⛔ 与 `on_ask_speak` 一样是旁路：发布失败只记一行，绝不改 ask 的挂起/恢复/超时语义。
    on_ask_pending: Any | None = None
    #: do 节点执行前的额外 MQTT 播报回调（T1，2026-11-04）：非 ask 自动化条件命中后、
    #: do 动作下发前经 `butler/inbox/speak` 播报一声。签名同 on_ask_speak：
    #: `(trace_id, text, room) -> None`；未注入时零副作用，⛔ 不改 do 执行核心逻辑。
    on_do_speak: Any | None = None
    #: emit 发布时的额外 MQTT 播报回调（AF-PM 裁定 2026-11-05）：与 do 同源、同一 room 解析路径。
    #: 签名同 on_do_speak：`(trace_id, text, room, entity_id) -> None`；未注入时零副作用。
    on_emit_speak: Any | None = None
    #: **动作失败**播报回调（出资人 2026-11-08 19:34 亲令：失败必须念出「失败动作＋原因」，
    #: ⛔ 不许静默哑掉）。签名同 on_do_speak；未注入时零副作用。
    #: 与三条播报腿**同一条投递体**（`Runtime._speak_deliver`）：取桥→解析房间→发布→记账，
    #: ⛔ 不另造一套解析（两套口径必分叉）。纯旁路：异常/失败绝不改动作结果与边选择。
    on_fail_speak: Any | None = None
    #: 运行期事实落盘（PM 卡 2026-11-04）：canary 试演状态记录回调
    #: `(state, automation_id, since, until, extra) -> None`；未注入时零副作用。
    runtime_facts: Any | None = None
    #: **写后回读**统计（AF-746b / v1.28.6，2026-11-08 22:2x 出资人 A+ 裁决）。
    #: 口径（卡 §十.4.1，**实现前先读**）：HA 回读**不是**设备真值——
    #: `xiaomi_home` 的 `set_property_async` 写完即 `_prop_value_map=value` ＋ `async_write_ha_state()`，
    #: 自回显与设备推送在 HA REST 上完全同形，`last_updated` 同样被自回显刷新 ⇒ **没有可作证据的新鲜度信号**。
    #: 所以只有两档结论：`mismatch`（读到别的值 ⇒ 有牙，记 failed）与 `unverified`（读到写入值，
    #: **⛔ 永不落 passed**）。⛔ 不许把 unverified 当通过——那是今晚「26/20」坑的假绿形状。
    readback_stats: dict[str, Any] = field(default_factory=lambda: {
        "checks": 0, "mismatches": 0,
        "last_verdict": None, "last_actual": None, "last_expected": None, "last_node": None,
    })
    #: v1.28 R1：ask 应答事实记录回调 `(automation_id, payload: dict) -> None`。
    #: 由 Runtime 注入 `RuntimeFacts.record_ask_answer`；未注入时零副作用（单测隔离）。
    #: ⛔ 记账失败绝不影响应答消费本身（与 `runtime_facts` 同一条纪律）。
    ask_answer_recorder: Any | None = None

    def __post_init__(self) -> None:
        self.pending_asks: dict[str, AskSession] = {}

    # ─────────────────────────────────────────────────────────────────
    # 主循环
    # ─────────────────────────────────────────────────────────────────
    def _lifecycle(self, instance: Instance, stage: str, node_id: str = "", **extra: Any) -> None:
        """AF-739（2026-11-07）：实例生命周期**单一**留痕入口。

        `stage` ∈ spawn／segment／suspend／resume／complete／cancel／fail／no_edge／no_action／
        action_failed（AF-742 新增：动作级失败，见 `_do`）。
        ⛔ 刻意**不做逐节点 INFO**：1000 步的环会刷爆 watch.log；节点级路径已在
        `instance.trace`（有界、随实例持久化）。这条日志是 AF-739 的直接教训——
        实例只存内存、坏了盘上零痕迹，根因只能靠推断；生命周期必须落进 watch.log 可查。

        AF-742（2026-11-08）：行里加 `failed_actions`——否则 `complete actions=1` 分不清
        「一次就干净收尾」与「一次成功＋一次失败→on_error 短路」（现场 02:01 d2
        `climate.set_temperature` 失败短路，读数与干净完成长得一模一样）。
        """
        extra_text = (" " + " ".join(f"{k}={v}" for k, v in extra.items())) if extra else ""
        logger.info(
            "INSTANCE_LIFECYCLE stage=%s auto=%s inst=%s node=%s state=%s actions=%d "
            "failed_actions=%d segments=%d%s",
            stage,
            str(getattr(instance.automation, "id", "")),
            instance.instance_id,
            node_id,
            str(instance.state),
            int(getattr(instance.ctx, "actions", 0)),
            int(getattr(instance.ctx, "failed_actions", 0)),
            int(instance.ctx.segments),
            extra_text,
        )

    #: AF-742（2026-11-08）：动作失败时要钉进 watch.log 的设备态属性（只读快照）。
    #: 现场需要的是「设备当时长什么样」：HA 侧 `xiaomi_home.climate.async_set_temperature`
    #: 抛 `RuntimeError: 执行错误, 未知错误, code=-706010002 ... target-temperature`
    #: 只在某些过渡态出现，没有这份现场态，下次仍只能靠猜。
    FAILURE_STATE_ATTRS = ("hvac_mode", "temperature", "current_temperature", "fan_mode", "swing_mode")

    def _failure_state_note(self, node: Node) -> str:
        """AF-742：失败动作**当时**的设备态（只读快照，⛔ 不重试、⛔ 不写设备）。

        取不到就返回空串——诊断是旁路，⛔ 不许因为读不到状态而改变失败判定或抛出去。
        """
        if self.states is None:
            return ""
        entity_id = str((node.params or {}).get("entity_id") or "")
        if not entity_id:
            return ""
        try:
            snap = self.states.snapshot([entity_id])
        except Exception:  # noqa: BLE001 —— 实体读不到 ⇒ 不写这段诊断
            return ""
        parts: list[str] = []
        try:
            state = snap.get(entity_id)
        except Exception:  # noqa: BLE001
            state = None
        if state not in (None, ""):
            parts.append(f"state={_speech_value_text(state)}")
        for attr in self.FAILURE_STATE_ATTRS:
            try:
                val = snap.get(f"{entity_id}.{attr}")
            except Exception:  # noqa: BLE001 —— 该实体没有这个属性（Snapshot 对缺失属性抛 UnknownEntity）
                continue
            if val not in (None, ""):
                parts.append(f"{attr}={_speech_value_text(val)}")
        return ",".join(parts)

    def run(self, instance: Instance) -> Instance:
        """执行一个求值段：从 `current_node` 走到挂起或终态。"""
        auto = instance.automation
        instance.ctx.segments += 1
        # AF-739：第一段＝spawn 后的真执行起点（调度器 `_try_fire` spawn 完就调本方法）；
        # 之后每段记 segment。这条是"实例到底跑没跑"的第一手读数。
        self._lifecycle(instance, "spawn" if instance.ctx.segments == 1 else "segment",
                        instance.ctx.current_node)
        # AF-746b（v1.28.6）：到点的写后回读先结账，再继续本段——挂起等 3s 的那条腿就是在
        # 「resume 后的第一段」到点的（`due_at` 用时钟比，⛔ 不靠墙钟猜）。
        self._maybe_check_readback(instance)
        if self._over_cap(instance):
            return instance
        steps = 0
        while True:
            steps += 1
            instance.ctx.steps += 1
            if steps > MAX_STEPS_PER_SEGMENT:
                self._fail(instance, "求值段超过最大步数，疑似静态图死循环")
                return instance
            if self._over_cap(instance):
                return instance

            node_id = instance.ctx.current_node
            if not node_id:
                self._terminate(instance)
                return instance

            node = auto.node(node_id)
            instance.trace(node_id, note="enter")

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
                # AF-739：这条路径此前**静默终止**（`_terminate` 不落审计）⇒ 图里缺兜底边时
                # 实例"消失"而无痕迹。现在显式留痕：节点、可用的边类型、该节点实际有哪些出边。
                available = sorted({str(e.kind) for e in auto.outgoing(node.id)})
                self.audit.add(
                    AuditEvent(
                        type=INSTANCE_NO_EDGE,
                        at=self.clock.now(),
                        message=(
                            f"节点 {node.id} 选不出一条可用边 ⇒ 实例终止（可用 kinds={sorted(kinds)}"
                            f"，该节点现有出边={available}）"
                        ),
                        automation_id=instance.automation.id,
                        instance_id=instance.instance_id,
                        node_id=node.id,
                        data={"kinds": sorted(kinds), "available": available,
                              "actions": int(instance.ctx.actions)},
                    )
                )
                self._lifecycle(instance, "no_edge", node.id, kinds=",".join(sorted(kinds)))
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
        self._lifecycle(instance, "resume", instance.ctx.current_node, kind=str(kind))
        # B3-AF-02: pop pending_asks BEFORE terminal check (was after, leaving ghost in queue)
        # C-2 §F.1：ask 的 on_timeout 到点 ⇒ 会话未经应答即消亡 ⇒ ASK_EXPIRED
        # (reason=timeout)。canary 的 on_timeout 不进 pending_asks（timer 挂起不入
        # 会话表），pop 得 None 不落这条——审计里三径（timeout/terminal/sweep）可分。
        expired_session = self.pending_asks.pop(instance.instance_id, None)
        if expired_session is not None and kind == "on_timeout":
            self.audit.add(
                AuditEvent(
                    type=ASK_EXPIRED,
                    at=self.clock.now(),
                    message=(
                        f"ask 超时到点，会话未经应答出队：instance={instance.instance_id} "
                        f"node={expired_session.node_id}"
                    ),
                    automation_id=instance.automation.id,
                    instance_id=instance.instance_id,
                    node_id=expired_session.node_id,
                    data={"reason": "timeout", "channel": "timeout"},
                )
            )
        if instance.is_terminal:
            return instance
        auto = instance.automation
        node = auto.node(instance.ctx.current_node)

        if instance.state == SUSPENDED:
            self.instances.resume(instance)  # 恢复时重新取快照

        # DCD 裁定 20261031（取消运行期二次授权·Q1 甲）：确认唤醒分支整体撤除——
        # 运行期闸没了，不存在"确认会话被唤醒放行"这条路；`ask` 的应答唤醒走
        # 下面的通用边选路。CONFIRM_GRANTED/CONFIRM_DENIED 审计事件随闸撤销（§2.3）。

        # P1-11：canary 观察期结束 → 检查漂移并回滚
        # P1-4 修复：pending_canary 现在是可序列化字典。崩溃恢复后据此重建 CanaryResult
        # （用实时 states 作为 guard，pre_snapshot 作为回滚基准），避免 default=str 静默字符串化
        # 后 `wrapped, adapter = "<...>"` 解包成字符、回滚静默失效。
        pending_canary = instance.ctx.context.pop("pending_canary", None)
        # D8（2026-11-05 真机实证）：canary 观察期到期后，实例必须**按 do 节点的正常边**继续
        # （验证通过走 `then`、漂移已回滚走 `on_error`），⛔ 不能用 `{on_timeout, default}`——
        # do 节点只有 then/on_error，`on_timeout` 选不出边 ⇒ 实例被 `_terminate` 掉，
        # 「观察期结束」被当成「超时」。这里记下该用哪套边，由下面统一的边选段消费。
        canary_edge_kinds: set[str] | None = None
        if pending_canary is not None and kind == "on_timeout":
            # 观察期正常走完 ⇒ 默认按「动作已成功」继续；漂移/回滚会把它改成失败腿。
            canary_edge_kinds = {"then", "default"}
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
                    from .af_canary import CanaryGuard, CanaryResult

                    # `auto_rollback` 随挂起元信息一起带过 suspend→resume 这道接缝：缺键
                    # （改动前落盘的旧快照）按 True，与原行为一致。回滚一律走 CanaryGuard，
                    # 不在恢复分支里另写一套"要不要回滚"的判断。
                    observe_guard = CanaryGuard(
                        states=self.states,
                        auto_rollback=bool(pending_canary.get("auto_rollback", True)),
                    )
                    wrapped = CanaryResult(
                        action=pending_canary.get("action", ""),
                        params=pending_canary.get("params", {}),
                        result=None,  # 恢复路径不依赖 result，has_drift/rollback 只用到 guard.states 与 pre_snapshot
                        pre_states=pending_canary.get("pre_snapshot", {}),
                        guard=observe_guard,
                        pre_snapshot=pending_canary.get("pre_snapshot", {}),
                    )
                    if wrapped.expected_state() is None:
                        # D8：无 SERVICE_STATE 映射 ⇒ 漂移不可判。**必须落终态**（unmodeled），
                        # 否则侧车永远停在 observing、UI 永远显示"试演中"（真机实证）。
                        # AF-746b（v1.28.6）：**正是这种"判不了"的动作最需要回读**——
                        # climate.set_temperature 就落这一档（无 SERVICE_STATE 映射），
                        # 所以这里也登记回读；不登记就等于 A+ 在最需要它的地方失效。
                        _rb_unmodeled = self._readback_spec(node)
                        if _rb_unmodeled:
                            self._schedule_readback(instance, node, _rb_unmodeled)
                        self._record_trial(instance, node, "unmodeled")
                        self._feed_canary_evidence(instance, node, "unmodeled", {
                            "reason": "观察期动作无 SERVICE_STATE 映射，漂移不可判",
                        })
                    elif wrapped.has_drift():
                        rolled = observe_guard.check_and_rollback(adapter, wrapped)
                        # 不回滚不等于没验出问题：漂移照旧记 failed + entity_drift（铁律 #5）
                        reason = (
                            "canary 观察期漂移，已自动回滚"
                            if rolled
                            else "canary 观察期漂移，auto_rollback=false ⇒ 未回滚，留给人判断"
                        )
                        # D8：漂移＝这次 do 验证失败 ⇒ 走失败腿，并把试演状态落成 rolled_back
                        canary_edge_kinds = {"on_error", "default"}
                        self._record_trial(instance, node, "rolled_back")
                        self._feed_canary_evidence(instance, node, "failed", {
                            "reason": reason, "rollback_calls": len(rolled),
                        })
                        self.audit.add(
                            AuditEvent(
                                type=ENTITY_DRIFT,
                                at=self.clock.now(),
                                message=f"canary 观察期检测到漂移 {wrapped.action}：{reason}（{len(rolled)} 次反向下发）",
                                automation_id=instance.automation.id,
                                instance_id=instance.instance_id,
                                node_id=node.id,
                                data={"params": dict(wrapped.params)},
                            )
                        )
                    else:
                        # D8：观察期无漂移 ⇒ 这次 do 验证通过，试演状态落 verified（此前从不落，
                        # 侧车永远停在 observing ⇒ UI 一直显示「试演中」）
                        self._record_trial(instance, node, "verified")
                        self._feed_canary_evidence(instance, node, "verified")
                        # AF-746b（v1.28.6）：canary 节点的 do 成功在**这里**才算数（`_do` 早就
                        # 挂起了，成功分支的回读登记跑不到）⇒ 通过处登记回读。
                        _rb = self._readback_spec(node)
                        if _rb:
                            self._schedule_readback(instance, node, _rb)
            except Exception:
                # MEDIUM-3（内核安全审计）：漂移检查异常不能静默降级为 verified（假绿）。
                # 落 unmodeled 状态 + 审计事件，让可观测性看到这次检查没跑完。
                logging.getLogger("autoforge.executor").exception("canary 漂移检查失败（已隔离，不阻断流程）")
                try:
                    self._feed_canary_evidence(instance, node, "unmodeled", {
                        "reason": "canary 漂移检查抛异常，状态不可判（非 verified）",
                    })
                    self.audit.add(
                        AuditEvent(
                            type=ENTITY_DRIFT,
                            at=self.clock.now(),
                            message="canary 漂移检查异常，落 unmodeled（非 verified）",
                            automation_id=instance.automation.id,
                            instance_id=instance.instance_id,
                            node_id=node.id,
                        )
                    )
                except Exception:
                    logging.getLogger("autoforge.executor").exception(
                        "canary unmodeled 状态落盘也失败（双异常）"
                    )

        # D8：canary 观察期恢复走 do 节点的正常边（verified→then／rolled_back→on_error）；
        # 其余唤醒（ask 应答、wait、取消…）仍按唤醒 kind 选边，行为不变。
        edge_kinds = canary_edge_kinds if canary_edge_kinds is not None else {kind, "default"}
        edge = auto.pick_edge(node.id, edge_kinds)
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

    def answer(
        self,
        room: str | None,
        text: str,
        ask_id: str | None = None,
        answered_via: str = DEFAULT_ANSWERED_VIA,
        verdict: str | None = None,
    ) -> Instance | None:
        """人类应答。WO-AF-002：统一走 resolve_ask，ask_id 不匹配绝不 fallback。

        `verdict` 非空 ⇒ **采信上游已分类的投票**（DCD 裁定 2026-11-04 §2.3 C5：分类归 DB），
        ⛔ 不对 `text` 再跑一遍 `classify_answer`——那一步把 unknown 映成 `no`，会把小爱腿
        已判定的 `yes` 悄悄翻成 `no`。自由文本腿（WebUI／文件回注）仍走本仓分类。
        `answered_via` 是来源标（`xiaomi`/`webui`，PM 卡 §二）：只影响留痕与载荷，
        ⛔ 不改变选边逻辑。
        """
        import logging
        logger = logging.getLogger("autoforge.executor")

        session = self.resolve_ask(ask_id, room)
        if session is None:
            if ask_id:
                logger.warning("answer: ask_id=%r 不匹配任何挂起 ask，拒绝 fallback", ask_id)
            else:
                logger.warning("ANSWER_NO_PENDING_ASK answer: room=%r 无挂起 ask", room)
            return None
        instance = self.instances.get(session.instance_id)
        if instance is None:
            logger.warning("answer: session.instance_id=%r 对应实例不存在", session.instance_id)
            return None
        # C-2 §B.3 交叉通道守卫：结构化会话（带 ask_spec，含 kind=="text"）不吃自由文本
        # 通道——`no` 是"用户明确不要"，文本乱答不得冒充参数收集（kind=="text" 的应答
        # 也必须走 answer_structured 带 kind="text"）。拒、**保持挂起**、落审计。
        if session.ask_spec is not None:
            self.audit.add(
                AuditEvent(
                    type=ASK_VIOLATION,
                    at=self.clock.now(),
                    message=(
                        f"交叉通道误用：结构化会话（spec kind={getattr(session.ask_spec, 'kind', '?')}）"
                        f"被喂自由文本，已拒绝、会话保持挂起（应答请走 answer_structured）："
                        f"text_len={len(text or '')} text_head={(text or '')[:2]!r}"
                    ),
                    automation_id=instance.automation.id,
                    instance_id=instance.instance_id,
                    node_id=session.node_id,
                    data={"reason": "cross_channel", "channel": "text"},
                )
            )
            # v1.28 R1：拒收但保持挂起 ⇒ 记下"来过一次"的来源标（未消费，故不进已应答历史）
            session.answered_via = str(answered_via)
            return None
        if verdict is None:
            verdict = classify_answer(text)
        else:
            # C5：上游（DB）已分类 ⇒ 直通。非法值当场抛，⛔ 不落成"疑似 no"蒙混过关。
            verdict = str(verdict).strip().lower()
            if verdict not in VERDICTS:
                raise ValueError(f"verdict={verdict!r} 不是 yes/no（只认 {sorted(VERDICTS)}）")
        import homesdk
        logger.info(
            "consent resolved: ask verdict=%s text_len=%d text_head=%r via=%s homesdk=%s",
            verdict, len(text or ""), (text or "")[:2], answered_via,
            getattr(homesdk, "__version__", "?"),
        )
        # B3-AF-02: terminal instance (expired) -> pop ghost, return None so caller keeps evidence file
        # 用 `is True` 而非 truthy：真实 Instance.is_terminal 返回 bool，Mock 对象返回非 bool
        if getattr(instance, "is_terminal", False) is True:
            self.pending_asks.pop(instance.instance_id, None)
            logger.warning("answer: instance %s is terminal, popping ghost ask", instance.instance_id)
            # C-2 §F.1：终态幽灵出队未经应答 ⇒ ASK_EXPIRED(reason=terminal)，
            # 与 sweep_orphans 的 reason=sweep 同族两径，审计里分得开。
            self.audit.add(
                AuditEvent(
                    type=ASK_EXPIRED,
                    at=self.clock.now(),
                    message=f"迟到应答命中终态实例，幽灵会话出队（零推进）：instance={instance.instance_id}",
                    automation_id=instance.automation.id,
                    instance_id=instance.instance_id,
                    node_id=session.node_id,
                    data={"reason": "terminal", "channel": "text"},
                )
            )
            return None
        # C-2 §F.1：一次会话至多一条 ASK_RESOLVED——校验已过、即将进边选路（resume
        # 出队即消费）；重复应答在 resolve_ask/出队后到不了这里，不产生第二条。
        self.audit.add(
            AuditEvent(
                type=ASK_RESOLVED,
                at=self.clock.now(),
                message=f"ask 会话被自由文本应答消费：verdict={verdict}",
                automation_id=instance.automation.id,
                instance_id=instance.instance_id,
                node_id=session.node_id,
                data={"ask_id": session.instance_id, "edge": verdict, "room": session.room,
                      "rounds": int(instance.ctx.context.get("_ask_rounds", 0)),
                      "payload_kind": "text", "channel": "text",
                      # v1.28 R1：来源标进审计（`xiaomi`＝小爱腿、`webui`＝界面/文件回注腿）
                      "answered_via": answered_via},
            )
        )
        result = self.resume(instance, verdict)
        self._record_ask_answer(
            instance, node_id=session.node_id, edge=verdict,
            answered_via=answered_via, payload_kind="text", room=session.room,
        )
        return result

    # ─────────────────────────────────────────────────────────────────
    # v2 M3 结构化 Ask：一等公民结构化应答（自由文本 answer 仍保留）
    # ─────────────────────────────────────────────────────────────────
    def answer_structured(self, room: str | None, payload: "AskAnswer | dict", ask_id: str | None = None, answered_via: str = DEFAULT_ANSWERED_VIA) -> Instance | None:
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
        # C-2 §B.3 交叉通道守卫（对称半）：二元会话（ask_spec 为 None）不吃结构化
        # 通道——旧现状 `_validate_structured(payload, None)` 只查非空文本，会把
        # 结构化 payload 误送上 `then` 边。拒、**保持挂起**、落审计；自由文本应
        # 走 answer()（text 会话除外——那类请经本通道带 kind="text"）。
        if session.ask_spec is None:
            self.audit.add(
                AuditEvent(
                    type=ASK_VIOLATION,
                    at=self.clock.now(),
                    message=(
                        "交叉通道误用：二元会话（无 ask_spec）被喂结构化应答，已拒绝、"
                        "会话保持挂起（自由文本请走 answer 通道）"
                    ),
                    automation_id=instance.automation.id,
                    instance_id=instance.instance_id,
                    node_id=session.node_id,
                    data={"reason": "cross_channel", "channel": "structured"},
                )
            )
            return None
        if isinstance(payload, dict):
            payload = AskAnswer.from_dict(payload)
        if not self._validate_structured(payload, session.ask_spec):
            # C-2 §F.1：从 ASK_VIOLATION 拆出 ASK_REJECTED——「填错值」≠「收敛纪律
            # 违反」；同时按 §F.3 脱敏：spec/answer 只记 kind 与值域摘要，⛔ 全文直印。
            _val = getattr(payload, "value", None)
            _pkind = getattr(payload, "kind", "?")
            if _pkind == "entity" and isinstance(_val, str):
                _digest = f"domain={_val.split('.', 1)[0] if '.' in _val else '?'}"
            elif isinstance(_val, str):
                _digest = f"len={len(_val)} head={_val[:2]!r}"
            else:
                _digest = f"value={str(_val)[:40]}"
            self.audit.add(
                AuditEvent(
                    type=ASK_REJECTED,
                    at=self.clock.now(),
                    message=(
                        f"结构化应答未通过校验（缺省即拒，会话保持挂起可重答）："
                        f"spec_kind={getattr(session.ask_spec, 'kind', '?')} "
                        f"answer_kind={_pkind} {_digest}"
                    ),
                    automation_id=instance.automation.id,
                    instance_id=instance.instance_id,
                    node_id=session.node_id,
                    data={"channel": "structured", "answer_kind": _pkind},
                )
            )
            # v1.28 R1：填错值 ⇒ 拒收保持挂起，但来源标留下（在途卡可显示"答过、未通过"）
            session.answered_via = str(answered_via)
            return None
        instance.ctx.vars["ask_answer"] = {"kind": payload.kind, "value": payload.value}
        # C-2 §F.1：一次会话至多一条 ASK_RESOLVED（出队即消费，重复应答到不了这里）。
        self.audit.add(
            AuditEvent(
                type=ASK_RESOLVED,
                at=self.clock.now(),
                message=f"ask 会话被结构化应答消费：payload_kind={payload.kind}",
                automation_id=instance.automation.id,
                instance_id=instance.instance_id,
                node_id=session.node_id,
                data={"ask_id": session.instance_id, "edge": "then", "room": session.room,
                      "rounds": int(instance.ctx.context.get("_ask_rounds", 0)),
                      "payload_kind": payload.kind, "channel": "structured",
                      # v1.28 R1：来源标进审计（结构化腿同样打标）
                      "answered_via": answered_via},
            )
        )
        result = self.resume(instance, "then")
        self._record_ask_answer(
            instance, node_id=session.node_id, edge="then",
            answered_via=answered_via, payload_kind=str(payload.kind), room=session.room,
        )
        return result

    def _record_ask_answer(
        self,
        instance: Instance,
        *,
        node_id: str,
        edge: str,
        answered_via: str,
        payload_kind: str,
        room: str | None,
    ) -> None:
        """v1.28 R1：把"已应答"这一事实记进运行期事实旁车（UI 已应答历史的正源）。

        ⛔ 记账失败绝不冒到应答消费（与 `runtime_facts` 同一条纪律）：宁可少一条历史，
        也不能因为旁车写失败让一次已经收下的应答看起来"没发生过"。
        """
        if self.ask_answer_recorder is None:
            return
        try:
            self.ask_answer_recorder(
                str(instance.automation.id),
                {
                    "ask_id": str(instance.instance_id),
                    "instance_id": str(instance.instance_id),
                    "node_id": str(node_id),
                    "room": room,
                    "edge": str(edge),
                    "answered_via": str(answered_via),
                    "payload_kind": str(payload_kind),
                },
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "ASK_ANSWER_FACT_FAILED 应答事实记账失败（应答本身已消费）：instance=%s",
                instance.instance_id, exc_info=True,
            )

    def validate_answer(self, room: str | None, payload: "AskAnswer | dict", ask_id: str | None = None) -> bool:
        """预检结构化应答是否合规（不消费 ask，不恢复实例）。

        供 service 层在调用 `answer_structured` 前区分"ask 不存在"与"校验被拒"，
        以便前者 404、后者 422。
        """
        session = self.resolve_ask(ask_id, room)
        if session is None:
            return False
        # C-2 §B.3：二元会话（ask_spec 为 None）吃结构化应答＝交叉通道误用，
        # 与 answer_structured 的守卫同形——预检 False ⇒ service 层映 422，不消费。
        if session.ask_spec is None:
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
            session = self.pending_asks.pop(inst_id, None)
            if session is not None:
                # C-2 §F.1：sweep 兜底出队未经应答 ⇒ ASK_EXPIRED(reason=sweep)，
                # 与 answer 的 terminal 腿、resume 的 timeout 腿同族三径可分。
                self.audit.add(
                    AuditEvent(
                        type=ASK_EXPIRED,
                        at=self.clock.now(),
                        message=f"sweep_orphans: 终态实例 {inst_id} 的幽灵会话出队（零推进）",
                        instance_id=inst_id,
                        node_id=session.node_id,
                        data={"reason": "sweep", "channel": "sweep"},
                    )
                )
        if ghosts:
            import logging
            logging.getLogger("autoforge.executor").warning(
                "sweep_orphans: cleaned %d ghost pending_asks: %s", len(ghosts), ghosts
            )
        return len(ghosts)

    def resume_then(self, instance: Instance) -> Instance:
        """`wait` 计时到点 → 走 `then` 正常继续（语义拍板 A：wait 是"等一会儿继续"，不是超时）。"""
        return self.resume(instance, "then")

    def reseed_sessions(self, instances: Iterable[Instance]) -> int:
        """崩溃恢复：把落盘时挂起的 `ask` 会话重挂回 `pending_asks`（§十八 B.14）。

        判据只用重启后仍然成立的两格：`state==suspended`、
        `automation.node(ctx.current_node).kind == "ask"`。
        **不能循 `timer.kind`**——v1.28 之前未设 `timeout` 的 ask 挂起时 `timer` 就是 `None`
        （`af_instance.py` 的 `suspend`）；v1.28 起 ask 有 300s 默认兜底（`_suspend`），
        但**落盘的旧实例**仍可能没有计时器，所以恢复判据不能只看 timer；`wait` /
        `canary_observe` 的节点不是 `ask`，因此天然排除。
        （确认会话的 `pending_confirm` 腿已随 DCD 裁定 20261031·Q1 甲撤除——运行期
        闸不存在，重启后不会再有确认会话要重挂；落盘旧实例若仍带该键，按 ask 之外的
        普通挂起处理，不重挂。）

        **只重挂、绝不放行**：这里不调 `resume`（重启不等于人已批过），实例保持 suspended，
        必须等 `/api/asks`、sidecar 或 inbox 收到人的答复才动。问句一律按**当前图**的节点渲染，
        因为唤醒后真下发的就是当前节点的 `action`——用落盘的旧动作名问人会说谎。
        房间预算（`MAX_ASKS_PER_ROOM`）不在这里复查：那是"一轮多问"的挂起期纪律，恢复只是
        把崩溃前已经问出口的话重新显示出来，不是新问一次。
        """
        reseeded = 0
        for instance in instances:
            if instance.state != SUSPENDED:
                continue
            node_id = instance.ctx.current_node
            try:
                node = instance.automation.node(node_id)
            except KeyError:
                self.audit.add(
                    AuditEvent(
                        type=INSTANCE_SESSION_LOST,
                        at=self.clock.now(),
                        message=(
                            f"恢复不出可应答会话：实例 {instance.instance_id} 挂在节点 "
                            f"{node_id!r}，该节点已不在当前图中（实例继续 suspended，零下发）"
                        ),
                        automation_id=instance.automation.id,
                        instance_id=instance.instance_id,
                        node_id=node_id,
                    )
                )
                continue
            if node.kind == "ask":
                self.pending_asks[instance.instance_id] = AskSession(
                    instance_id=instance.instance_id,
                    node_id=node.id,
                    room=node.room,
                    created_at=self.clock.monotonic(),
                    prompt=node.prompt,
                    ask_spec=node.ask,
                )
            else:
                continue
            reseeded += 1
        return reseeded

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
                logger.warning("IF_NODE_EXEC_ERROR [IF] %s ERROR: %r", node.id, exc)
                return self._if_eval_fail(instance, node, exc)
            return {"then"} if value else {"no", "default"}

        if node.kind == "set":
            value = self._value_of(instance, node)
            self.instances.set_var(instance, node.var, value)  # type: ignore[arg-type]
            return {"then"}

        if node.kind == "do":
            return self._do(instance, node)

        if node.kind == "group":
            # 组合的展开发生在**部署期**（`af_apply.apply_group` 逐子入队），不在运行期实例里内联：
            # 每条子自动化有自己的 trigger 与实例，内联执行等于凭空造 N 个实例。
            raise ValueError(
                f"group 节点 {node.id} 不可在实例内联执行"
                f"（{len(node.children)} 条子自动化各有独立触发），应由 apply_group 在部署期展开"
            )

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

    def _record_trial(self, instance: Instance, node: Node, state: str) -> None:
        """PM 卡 2026-11-04：把 canary 试演状态落盘（供 UI「试演期」显示）。

        与 `_feed_canary_evidence` 分工：那个进 af_watch 的诚实报告分区；这个进
        `<archive>.runtime.json` 供列表展示。记账失败不影响执行。
        """
        if self.runtime_facts is None:
            return
        try:
            self.runtime_facts(state, instance.automation.id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("RUNTIME_FACTS_TRIAL_FAILED 试演状态记账失败（不影响执行）：%r", exc)

    def _do(self, instance: Instance, node: Node) -> Iterable[str] | None:
        """`do`：**单次调用**，不重试不降级。

        DCD 裁定 20261031（取消运行期二次授权·Q1 甲 ＋ AF-710 闸位次序半）：
        `requires_confirm` 的运行期闸**整体撤除**——字段保留但只承载「部署期必须
        显眼」语义（编译期 L2 分档负责），运行期不再挂起、不再开会话、不再等人
        应答（出资人设计模型：一次审批，部署后即生效）。

        撤除面：旧确认挂起块、`resume` 的确认唤醒分支、`_suspend_for_confirm`、
        `reseed_sessions` 的 pending_confirm 腿、CONFIRM_GRANTED/CONFIRM_DENIED
        审计事件与 confirm_skipped_dry_run/confirm_wait 留痕（裁定 §2.1/§2.3）。
        ⛔ 不动：`ask` 节点（业务里"问用户一句话"）、`live_run` 的 confirm=true
        （部署范畴的一次确认）、canary 灰度（部署后兜底，Q2 甲保留并强制）。
        """
        try:
            adapter = self.adapters.get(node.adapter or "")
        except Exception as exc:  # 适配器未注册
            return self._soft_fail(instance, node, exc)

        dry_run = bool(getattr(adapter, "dry_run", False))

        # T1（2026-11-04）：非 ask 自动化条件命中后、do 动作下发前，经 MQTT 桥额外播报一声。
        # ⛔ 不改 do 执行核心逻辑：播报是纯旁路，失败只吞 warning、绝不改变动作结果或流程。
        # dry-run 适配器（G1 默认不写真机）不播报——没真动作却出声会误导。
        self._speak_for_do(instance, node, dry_run=dry_run)

        # G4 canary：conf 处于 auto 带且节点标了 canary → 走灰度保护
        # ⚠️ dry-run 适配器（G1 默认不写真机）不改状态，会**永远**被判定为漂移，必须跳过
        canary = node.canary
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
            self._audit_action_called(instance, node)
            try:
                wrapped = guard.perform(adapter, node.action or "", dict(node.params))
            except UnknownEntity as exc:
                # 取不到动作前快照 = 没有回滚把手（铁律 #5：回滚不了就 fail-closed）。
                # `perform` 在**下发之前**取快照，所以这里一条动作都还没发出去；
                # 异常不许穿出 `step()`——穿出就是整次 tick 陪葬（第二轮审计那一类）。
                return self._soft_fail(instance, node, exc)
            except Exception as exc:  # noqa: BLE001 —— AF-742：调用抛错＝动作失败，⛔ 穿出 run()
                # 适配器/传输层抛错（连接重置、序列化错误…）此前没有任何处置：异常穿到
                # `scheduler._try_fire` 之外，实例停在 active、盘上零痕迹。这里收敛成一次
                # 失败动作，走与 `result.success=False` 完全相同的收口（计数＋持久留痕＋选边）。
                instance.ctx.actions += 1
                instance.trace(
                    node.id,
                    note=f"do {node.adapter}.{node.action} raised: {type(exc).__name__}: {exc}",
                )
                return self._action_failed_result(
                    instance, node, CallResult.fail(f"{type(exc).__name__}: {exc}"),
                )
            # P1-11：canary.duration 接入——动作下发后挂起观察 duration，超时恢复时检查漂移
            canary_duration = None
            if isinstance(canary, dict):
                dur_str = canary.get("duration")
                if dur_str:
                    try:
                        canary_duration = parse_duration(dur_str)
                    except (ValueError, TypeError) as exc:
                        # BUG-02 修复：canary 观察窗口配置解析失败不得静默降级，否则
                        # 无观察期保护直接下发危险动作且不留痕；记录告警后按无观察期处理。
                        _logger.warning(
                            "CANARY_DURATION_PARSE_FAILED canary.duration 解析失败，按无观察期处理（node=%s.%s, dur=%r）: %s",
                            instance.automation.id, node.id, dur_str, exc,
                        )
            # AF-738 补（假绿口子）：**调用本身失败**时不许进观察窗——否则实例白挂一个窗口，
            # 而回执按"已调用"报 executed，读者会以为设备改了。失败直接落到下面的通用失败处理
            # （`result.success` 判 False ⇒ ACTION_FAILED + on_error），⛔ 不对未生效的动作做漂移回滚。
            _call_failed = not bool(getattr(wrapped.result, "success", True))
            # AF-742：动作**已经交出去**了，先计数再决定是否挂起观察窗——旧实现在下面
            # 挂起分支 `return None` 之后才 `actions += 1`，于是"动作真发了、实例挂起观察"
            # 这一轮的读数停在 actions=0（现场 2026-11-08 01:56 d1 就是这个形状，终态
            # 把两次真实调用报成一次）。计数是事实，⛔ 不随挂起与否打折。
            instance.ctx.actions += 1
            if _call_failed:
                result = wrapped.result
            elif canary_duration and canary_duration > 0:
                # 挂起观察：存可序列化元数据（P1-4 修复：原存 (wrapped, adapter) 对象不可序列化，
                # 崩溃恢复后 default=str 静默字符串化 → 解包成字符 → 回滚静默失效）。恢复路径据此重建。
                instance.ctx.context["pending_canary"] = {
                    "action": node.action or "",
                    "params": dict(node.params),
                    "pre_snapshot": wrapped.pre_snapshot,
                    "adapter": node.adapter or "",
                    # 观察期结束时这个旗子还得在场：不带过来，恢复分支就只能无条件回滚
                    "auto_rollback": guard.auto_rollback,
                }
                self.instances.suspend(instance, node.id, canary_duration, kind="canary_observe")
                self._record_trial(instance, node, "observing")
                return None
            # 无 duration → 立即检查漂移（原行为）；AF-738：调用已失败则跳过（⛔ 对未生效的动作回滚）
            if not _call_failed:
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
                    self._record_trial(instance, node, "rolled_back")
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
                    self._record_trial(instance, node, "verified")
            result = wrapped.result
        else:
            self._audit_action_called(instance, node)
            # AF-742：适配器**抛异常**也要落成一次失败动作。旧实现让它直接穿出 `run()`
            # ⇒ 这一轮既没有 `ACTION_FAILED` 也没有 trace（异常路径连审计都到不了），
            # 实例还停在 active，「动作失败零留痕」的另一种形状。
            try:
                result = adapter.call(node.action or "", dict(node.params))
            except Exception as exc:  # noqa: BLE001 —— 调用异常＝动作失败，⛔ 不许穿出 run()
                result = CallResult.fail(f"{type(exc).__name__}: {exc}")
            instance.ctx.actions += 1

        # AF-739（2026-11-07）：真下发计数（wrapped/shadow 两条腿都算"动作已交出去"）。
        # AF-742：计数已上移到两条腿各自的调用点（canary 挂起腿也要算），这里不再加。

        # 结果只进 vars，不回写快照（IR §7.2）
        if node.result_var:
            instance.ctx.vars[node.result_var] = {
                "success": result.success,
                "data": result.data,
                "error": result.error,
            }

        # T2（2026-11-04 climate.set_temperature 不生效）：**每次 do 下发结果留痕**。
        # 成功路径此前一条记录都没有（只有失败的 ACTION_FAILED 审计）⇒ 现场无法回答
        # 「这条 do 到底发出去没有、HA 回了什么」。trace 随实例持久化（af_instance.to_dict），
        # 读 store 里的实例快照即可逐条核对；⛔ 不改动作/边选择，纯留痕。
        instance.trace(
            node.id,
            note=(
                f"do {node.adapter}.{node.action} "
                + ("ok" if result.success else f"failed: {result.error}")
            ),
        )

        if result.success:
            # 一次真实动作落地 → 视为新一轮交互上下文，连问计数清零
            instance.ctx.context["_ask_rounds"] = 0
            # AF-746b（v1.28.6）：声明了 `readback` 的 do，成功后**登记**一次回读（⛔ 不阻塞本段）。
            spec = self._readback_spec(node)
            if spec:
                self._schedule_readback(instance, node, spec)
            return {"then"}

        return self._action_failed_result(instance, node, result)

    def _action_failed_result(
        self, instance: Instance, node: Node, result: CallResult,
    ) -> Iterable[str] | None:
        """AF-742（2026-11-08）：动作失败**统一收口**（`_do` 的正常失败腿与 canary 抛错腿共用）。

        为什么必须持久留痕：运行期 `AuditLog` 只在内存（`af_audit.AUDIT_SCOPE_NOTE`：只有部署
        仪式台账落盘），实例 trace 也只在 `persist=true` 才落盘 ⇒ 现场 2026-11-08 02:01 的 d2
        `climate.set_temperature` 失败，事后**一条都读不到**（watch.log 那 1.5 秒零 INFO 零
        WARNING，只有 stage=segment 与 stage=complete 两行）。这里补三样：
        ① `failed_actions` 计数——终态读数能分「干净完成」与「短路完成」；
        ② `action_failed` 生命周期行——watch.log 是唯一持久面，带动作名、错误原文、现场设备态；
        ③ `ACTION_FAILED` 审计——进程内可查（与既有审计面同口径，不新增码）。
        """
        instance.ctx.failed_actions += 1
        # 失败现场的设备态（只读）。有了它，下一次同类拒绝能直接看出
        # 「当时 hvac_mode / target / current 是什么」——即 PM 要钉的"具体触发态"。
        _dev_note = self._failure_state_note(node)
        self._lifecycle(
            instance, "action_failed", node.id,
            action=f"{node.adapter}.{node.action}",
            # 生命周期行的 extra 是空格分隔的 `k=v`：错误原文压成单 token，别让空格把行长歪
            error="_".join(str(result.error or "").split())[:120] or "unknown",
            dev=_dev_note or "-",
        )
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
        # 出资人 2026-11-08 19:34 亲令：失败必须**出声**（文本走与 do/emit 同一条投递体）。
        # 位置在审计之后、边选择之前：先把事实记下来，再念——念失败也改不了判定。
        self._speak_for_failure(instance, node, result)
        kinds = ["on_error", "default"]
        if instance.automation.pick_edge(node.id, kinds) is None:
            self._fail(instance, f"{node.action} 失败且无 on_error/default 兜底：{result.error}")
            return None
        return set(kinds)

    # ── 写后回读（AF-746b / v1.28.6，出资人 2026-11-08 22:01 A+ 裁决）──────────
    # 口径铁律（卡 §十.4.1）：**HA 回读 ≠ 设备真值**。`xiaomi_home.set_property_async` 写完即
    # `_prop_value_map=value` ＋ `async_write_ha_state()`（自回显），与设备真实推送在 HA REST 上
    # 完全同形；`last_updated` 也被自回显刷新 ⇒ 没有可作证据的新鲜度信号。
    # ⇒ 只允许两档：`mismatch`（读到别的值，有牙）与 `unverified`（读到写入值，⛔ 永不 passed）。
    def _readback_spec(self, node: Node) -> dict[str, Any] | None:
        spec = (node.raw or {}).get("readback")
        return spec if isinstance(spec, dict) else None

    def _schedule_readback(self, instance: Instance, node: Node, spec: dict[str, Any]) -> None:
        """do 成功后登记一次回读：⛔ 不阻塞、⛔ 不改动作结果与边选择。"""
        try:
            wait_s = parse_duration(str(spec.get("wait") or "3s"))
        except Exception:  # noqa: BLE001 —— 配置写坏按 3s 兜底（⛔ 不因此失败）
            wait_s = 3.0
        instance.ctx.context["pending_readback"] = {
            "node": node.id,
            "attribute": str(spec.get("attribute") or "temperature"),
            "equals": spec.get("equals"),
            "due_at": self.clock.monotonic() + max(0.0, float(wait_s)),
        }

    def _readback_actual(self, node: Node, attribute: str) -> Any:
        """回读**HA 侧**该属性的现读值（如实可能为 None；⛔ 不编值）。"""
        entity_id = str((node.params or {}).get("entity_id") or "").strip()
        if not entity_id:
            target = (node.params or {}).get("target")
            if isinstance(target, dict):
                entity_id = str(target.get("entity_id") or "").strip()
        if not entity_id or self.states is None:
            return None
        try:
            snap = self.states.snapshot([entity_id])
        except Exception:  # noqa: BLE001 —— 读不到就 None，⛔ 不许当"读到写入值"
            return None
        return (snap.attributes.get(entity_id) or {}).get(attribute)

    @staticmethod
    def _kv_text(value: Any) -> str:
        """生命周期行是空格分隔的 `k=v`：值里的空格必须压掉，别把行长歪。"""
        return "_".join(str(_speech_value_text(value)).split()) or "-"

    def _maybe_check_readback(self, instance: Instance, *, force: bool = False) -> str | None:
        pending = instance.ctx.context.get("pending_readback")
        if not isinstance(pending, dict):
            return None
        if not force and self.clock.monotonic() < float(pending.get("due_at") or 0.0):
            return None
        node_id = str(pending.get("node") or "")
        attribute = str(pending.get("attribute") or "temperature")
        expected = pending.get("equals")
        try:
            node = instance.automation.node(node_id)
        except KeyError:
            instance.ctx.context.pop("pending_readback", None)
            return None
        actual = self._readback_actual(node, attribute)
        mismatch = (
            actual is not None and expected is not None
            and str(_speech_value_text(actual)) != str(_speech_value_text(expected))
        )
        verdict = "mismatch" if mismatch else "unverified"
        stats = self.readback_stats
        stats["checks"] = int(stats.get("checks") or 0) + 1
        if mismatch:
            stats["mismatches"] = int(stats.get("mismatches") or 0) + 1
        stats["last_verdict"] = verdict
        stats["last_actual"] = actual
        stats["last_expected"] = expected
        stats["last_node"] = node_id
        # 实例面事实（回执/实例快照都能读；`unverified` 就写在脸上，⛔ 不给下游当通过的理由）
        instance.ctx.context["readback"] = {
            "node": node_id, "verdict": verdict, "actual": actual,
            "expected": expected, "source": "ha_state",
        }
        instance.ctx.context.pop("pending_readback", None)
        self._lifecycle(instance, "readback", node_id,
                        verdict=verdict, actual=self._kv_text(actual),
                        expected=self._kv_text(expected), source="ha_state")
        if mismatch:
            self.audit.add(
                AuditEvent(
                    type=ACTION_FAILED,
                    at=self.clock.now(),
                    message=(
                        f"写后回读不符：{attribute} 期望 {expected}、实读 {actual}"
                        f"（HA 回读，⛔ 非设备真值保证）"
                    ),
                    automation_id=instance.automation.id,
                    instance_id=instance.instance_id,
                    node_id=node_id,
                )
            )
        return verdict

    def check_due_readbacks(self, *, force: bool = False) -> int:
        """批量结账到点（或 `force=True`）的回读；返回结了几笔。"""
        done = 0
        for inst in self.instances.all():
            if isinstance(inst.ctx.context.get("pending_readback"), dict):
                if self._maybe_check_readback(inst, force=force) is not None:
                    done += 1
        return done

    def _if_eval_fail(self, instance: Instance, node: Node, exc: BaseException) -> Iterable[str] | None:
        """if 求值抛错：**不许**走 `default`/`no` 冒充"条件不成立"。

        体验者现场（2026-10-28）：`var` 写裸 entity_id（`sensor.duka_x`）⇒
        `split_namespace` ValueError ⇒ 旧口径 `_soft_fail` 返回 {on_error, default}
        ⇒ 挂了 default 边的图把"求值失败"当成"条件为假"继续走 **done**——
        build 零诊断、仿真 ok=True、实例正常终态，动作却永不执行（静默死图）。
        求值失败是**执行失败**不是布尔假值：只认显式 `on_error` 边，无边即 failed
        （实例状态如实 FAILED + 审计留痕，仿真/运维两面都看得见）。
        """
        entity_id = exc.args[0] if isinstance(exc, UnknownEntity) and exc.args else ""
        self.audit.add(
            AuditEvent(
                type=ENTITY_DRIFT,
                at=self.clock.now(),
                message=f"if 节点 {node.id} 求值失败（命名空间/类型/实体缺失）：{exc}",
                automation_id=instance.automation.id,
                instance_id=instance.instance_id,
                node_id=node.id,
                entity_id=str(entity_id),
            )
        )
        if instance.automation.pick_edge(node.id, {"on_error"}) is None:
            self._fail(instance, f"if 节点 {node.id} 求值失败且无 on_error 边：{exc}")
            return None
        return {"on_error"}

    def _soft_fail(self, instance: Instance, node: Node, exc: BaseException) -> Iterable[str] | None:
        """实体漂移 / 表达式错误 → 软失效：走 `on_error`，无则 failed（**不直接 failed**）。"""
        entity_id = exc.args[0] if isinstance(exc, UnknownEntity) and exc.args else ""
        # AF-742（2026-11-08）：软失效＝「这个 do 节点没成」——失败计数与生命周期行同口径，
        # 与 `_do` 里 `result.success=False` 那条腿共用同一个读数面（含 canary 漂移回滚）。
        instance.ctx.failed_actions += 1
        self._lifecycle(
            instance, "action_failed", node.id,
            action=f"{node.adapter}.{node.action or ''}",
            error="_".join(str(exc).split())[:120] or type(exc).__name__,
        )
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
            elif node.kind == "ask":
                # v1.28 R1（PM 卡 §二.1 补钉）：ask 未写 `timeout` 时**不再无限挂起**，用默认
                # 300s（env `AUTOFORGE_ASK_TIMEOUT_S` 可覆盖）。到点仍走图作者自己钉的
                # `on_timeout`/`default` 边——本件只保证"总会有个到点"，⛔ 不替图作者选边。
                duration = parse_duration(node.timeout) if node.timeout else default_ask_timeout_s()
            elif node.timeout:
                duration = parse_duration(node.timeout)
        except (ValueError, TypeError) as exc:
            # P2-1 修复：duration/timeout 非法（如 "abch"）原会冒泡到 run() → tick 失败 →
            # 整个调度循环停摆。此处就地软失效（走 on_error/default），不污染调度循环。
            self._soft_fail(instance, node, exc)
            return

        if node.kind == "ask":
            instance.ctx.context["_ask_rounds"] = int(instance.ctx.context.get("_ask_rounds", 0)) + 1
            # 人工确认会话（`requires_confirm`）默认沿用本实例最近一次 ask 的 room：
            # 房间维度应答（管家/`runtime.answer(room,…)`）因此能真的够到它，不会问而无门。
            instance.ctx.context["_ask_room"] = node.room
        # 落盘发生在状态转换那一刻（`_transition` → `Runtime._on_instance_change` → `persist.save`）：
        # 挂起期要用、且重启后要据此重挂会话的上下文，必须在 `suspend()` **之前**写进 ctx。
        self.instances.suspend(instance, node.id, duration, kind=node.kind)
        # AF-739：挂起点留痕（阶段粒度；节点级路径另有 instance.trace）
        self._lifecycle(instance, "suspend", node.id, kind=str(node.kind))

        if node.kind == "ask":
            self.pending_asks[instance.instance_id] = AskSession(
                instance_id=instance.instance_id,
                node_id=node.id,
                room=node.room,
                created_at=self.clock.monotonic(),
                prompt=node.prompt,
                ask_spec=node.ask,
            )
            # DCD 裁定 2026-11-04 阶段1（乙）：ask 触发（实例暂停＋待答写入后）**额外**发一条
            # MQTT 播报到 butler/inbox/speak。⛔ 不改 ask 核心逻辑（暂停/恢复/超时不动），
            # ⛔ 记不住就吞（旁路留痕，失败不许弄崩 ask）。trace_id=instance_id（DB §2 非空≤64）。
            if self.on_ask_speak is not None:
                try:
                    self.on_ask_speak(
                        str(instance.instance_id), str(node.prompt or ""), str(node.room or ""),
                    )
                except Exception:  # noqa: BLE001 —— 播报旁路失败绝不改 ask 行为
                    logger.warning(
                        "ASK_SPEAK_PUBLISH_FAILED ask 播报旁路异常（不影响 ask 核心） instance=%s",
                        instance.instance_id, exc_info=True,
                    )
            # v1.28 R1（DCD 裁定 2026-11-04 §2.3 C1/C3/C6）：挂起时**额外**发一条
            # `af/ask/pending`（AF 先出 MQTT 版 ask 面＝阶段 2 的前置）。载荷里的
            # `timeout_remaining` 就是本次挂起真正要等的秒数（图作者写多少就发多少，
            # 未写则发默认兜底值），让 DB 不必猜 AF 这一侧等了多久。
            # ⛔ 与上方 speak 同一条纪律：旁路、失败只记一行、绝不改挂起语义。
            if self.on_ask_pending is not None:
                try:
                    self.on_ask_pending(
                        str(instance.instance_id), str(node.prompt or ""), str(node.room or ""),
                        float(duration) if duration else None,
                    )
                except Exception:  # noqa: BLE001 —— 发布旁路失败绝不改 ask 行为
                    logger.warning(
                        "ASK_PENDING_PUBLISH_FAILED af/ask/pending 发布旁路异常（不影响 ask 核心）"
                        " instance=%s",
                        instance.instance_id, exc_info=True,
                    )

    # `_suspend_for_confirm`（确认会话挂起点）已随 DCD 裁定 20261031（Q1 甲）撤除——
    # 运行期闸不存在，确认会话无起点；本函数刻意不留空壳，调用点同批删除。

    # ─────────────────────────────────────────────────────────────────
    # 终止
    # ─────────────────────────────────────────────────────────────────
    def _terminate(self, instance: Instance) -> None:
        """走到终点。若之前请求过取消，则落到 cancelled（取消不回滚，只标记状态）。"""
        pending = instance.ctx.context.pop("pending_terminal", None)
        # AF-739（2026-11-07）：**零动作收尾必须留痕**。这条路径此前完全静默——线上出现
        # 「last_dispatched_at 有、设备没动、播报没有、trial 没动」时，盘上没有任何一行能
        # 说明"实例其实跑完了、只是走了 no 边/空图"。判定：一次动作都没下发，而图里确有 `do`。
        # AF-742（2026-11-08）：再加一条前置——`failed_actions==0`。失败动作（尤其调用前就
        # 软失效的那种）会让 `actions` 停在 0，但那次 run **不是"零动作"，是"动作失败"**；
        # 两个读数并存只会让读者把"试过并失败"又读成"什么都没做"（现场 02:01 正是这么被误读）。
        if (
            int(getattr(instance.ctx, "actions", 0)) == 0
            and int(getattr(instance.ctx, "failed_actions", 0)) == 0
        ):
            has_do = any(n.kind == "do" for n in instance.automation.nodes.values())
            if has_do:
                self.audit.add(
                    AuditEvent(
                        type=INSTANCE_NO_ACTION,
                        at=self.clock.now(),
                        message=(
                            f"实例走到终点但一次动作都没下发（图里有 do 节点）：inst={instance.instance_id} "
                            f"末节点={instance.ctx.current_node or '(空)'} 段数={instance.ctx.segments} "
                            f"步数={instance.ctx.steps}"
                        ),
                        automation_id=instance.automation.id,
                        instance_id=instance.instance_id,
                        node_id=instance.ctx.current_node or "",
                        data={"segments": int(instance.ctx.segments),
                              "steps": int(instance.ctx.steps)},
                    )
                )
                self._lifecycle(instance, "no_action", instance.ctx.current_node)
        if pending == CANCELLED:
            if instance.state == SUSPENDED:
                self.instances.resume(instance)
            self.instances.cancel(instance, instance.ctx.context.get("cancel_reason", ""))
            self._lifecycle(instance, "cancel", instance.ctx.current_node)
        else:
            if instance.state == SUSPENDED:
                self.instances.resume(instance)
            self.instances.done(instance)
            self._lifecycle(instance, "complete", instance.ctx.current_node)

    def _over_cap(self, instance: Instance) -> bool:
        """跨段累计封顶（DCD 裁定 20261006 §二 问题一 · A+B 组合）。返回 True ⇒ 本段已结束。

        两档：**越警戒档（S=1000 段 / T=20000 步）只告警**——WARNING + 一条 `AuditEvent` +
        监护视图常驻指示，且每实例只发一次（否则后面每一段都要刷一条）；**到 2× 才 `_fail`**。
        不是一越档就杀的理由写在裁定里：纯等待型自动化（`wait → do → wait` 轮询）可以合法地
        转很多段，警戒档买的是"运维看得见这个实例已经转了 N 段"，硬档才动状态机。
        """
        ctx = instance.ctx
        exceeded = []
        if ctx.segments > MAX_SEGMENTS_PER_INSTANCE:
            exceeded.append(f"段数 {ctx.segments}>{MAX_SEGMENTS_PER_INSTANCE}")
        if ctx.steps > MAX_STEPS_PER_INSTANCE:
            exceeded.append(f"步数 {ctx.steps}>{MAX_STEPS_PER_INSTANCE}")
        if not exceeded:
            return False
        reason = "、".join(exceeded)
        if (
            ctx.segments > HARD_CAP_MULTIPLIER * MAX_SEGMENTS_PER_INSTANCE
            or ctx.steps > HARD_CAP_MULTIPLIER * MAX_STEPS_PER_INSTANCE
        ):
            self._fail(
                instance,
                f"实例跨段累计超硬上限（{reason}，硬倍数 {HARD_CAP_MULTIPLIER}×）：疑似经挂起点的段间循环",
            )
            return True
        if ctx.cap_warned:
            return False
        ctx.cap_warned = True
        logger.warning(
            "实例 %s 跨段累计超警戒档：%s（到 %s× 才终止）%s",
            ctx.instance_id,
            reason,
            HARD_CAP_MULTIPLIER,
            f" trace 已丢 {ctx.trace_dropped} 条" if ctx.trace_dropped else "",
        )
        self.audit.add(
            AuditEvent(
                type=CAP_WARNING,
                at=self.clock.now(),
                message=f"跨段累计超警戒档：{reason}",
                automation_id=instance.automation.id,
                instance_id=ctx.instance_id,
                node_id=ctx.current_node,
            )
        )
        try:  # 指示只是给运维看的，聚合失败绝不能反过来影响执行（与 _watch_canary 同一纪律）
            from . import af_watch

            af_watch.record_cap_warning(
                instance.automation.id,
                ctx.segments,
                ctx.steps,
                ctx.trace_dropped,
                self.clock.now().timestamp(),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("af_watch 聚合段间封顶告警失败（不影响执行）：%r", exc)
        return False

    def _fail(self, instance: Instance, reason: str) -> None:
        if instance.state == SUSPENDED:
            self.instances.resume(instance)
        self.instances.fail(instance, reason)
        self._lifecycle(instance, "fail", instance.ctx.current_node, reason=reason[:60])

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
    def _audit_action_called(self, instance: Instance, node: Node) -> None:
        """AF-738：**真调用适配器那一刻**留痕（回执的唯一执行真值源）。

        为什么单独一枚码：`af_action_dispatched` 由影子/干预层在"要发"时写，
        而 `live_run` 不接影子层 ⇒ 回执只认它时会恒报 `nothing_dispatched`，
        真实下发在回执里看不见（假红，AF-738 根因）。本码写在**执行器的调用点**，
        watch 与单发两条路都有，且不谈实例是否随后终止（审计留得住）。

        ⛔ 纯留痕：不改动作、不改边选择、不改实例状态；审计是内存 append，无失败路径。
        """
        entities = sorted(str(e) for e in (node.target_entities() or ()))
        self.audit.add(
            AuditEvent(
                type=ACTION_CALLED,
                at=self.clock.now(),
                message=f"调用 {node.adapter}.{node.action}（{', '.join(entities) or '-'}）",
                automation_id=instance.automation.id,
                instance_id=instance.instance_id,
                node_id=node.id,
                data={"action": f"{node.adapter}.{node.action}", "entities": entities},
            )
        )

    def _speech_template_text(self, node: Node, fallback_entity_id: str) -> str | None:
        """D11：渲染节点 `speech_template`（实时值插值）；渲不出来返回 `None`（回退静态文案）。

        实体源顺序：节点 `speech_entity`（显式指定，如杜克温度计）→ `fallback_entity_id`
        （do 的目标实体／emit 载荷里的 entity_id）。读数顺序：先 `<实体>.<占位符>` 属性
        （climate 的 `temperature` 在 attributes 里），再退实体状态值（sensor 的 state 就是读数）。

        ⛔ fail-closed 三禁：不假装读数（读不到就 None）、不播占位符原文、不播「unknown」。
        ⛔ 纯旁路：任何异常都返回 None，绝不改 do/emit 的核心行为。
        """
        raw_tpl = node.raw.get("speech_template")
        if not isinstance(raw_tpl, str) or not raw_tpl.strip():
            return None
        src = str(node.raw.get("speech_entity") or "").strip() or str(fallback_entity_id or "").strip()
        if not src or self.states is None:
            return None
        names = set(_SPEECH_PLACEHOLDER_RE.findall(raw_tpl))
        if not names or not names <= SPEECH_TEMPLATE_PLACEHOLDERS:
            return None
        try:
            snap = self.states.snapshot([src])
        except Exception:  # noqa: BLE001 —— 实体不可读 ⇒ 回退静态（fail-closed）
            return None
        values: dict[str, str] = {}
        for name in names:
            val: Any = None
            try:
                val = snap.get(f"{src}.{name}")  # 属性形态：`climate.x.temperature`
            except Exception:  # noqa: BLE001 —— 没有该属性 ⇒ 退到状态值
                val = None
            if val is None or val == "":
                try:
                    val = snap.get(src)  # 状态值形态：`sensor.x` 的 state 就是读数
                except Exception:  # noqa: BLE001
                    return None
            if val is None or val == "":
                return None
            values[name] = _speech_value_text(val)
        return render_speech_template(raw_tpl, values)

    def _speak_for_emit(self, instance: Instance, node: Node, data: dict) -> None:
        """emit 播报（AF-PM 裁定 2026-11-05；v1.27.1 语义收口 2026-11-06）。

        房间文本：`node.room` → `emit.data.room`（IR 里 emit 常把房间写在 data 里）；
        实体兜底：`emit.data.entity_id`（有则交给 Runtime 走 catalog 反查）。
        ⛔ 纯旁路：未注入回调 / 取不到文本时 no-op；异常只吞 warning，emit 照常发布。

        ★ v1.27.1 新契约（PM 卡 `AF卡_v1271_热修_播报语义收口_20261106.md`）：
        **显式才出声**——`speech_template` 渲染成功**或** `announce` 非空才播；
        两者都没有 ⇒ **静默**（⛔ 不再机拼「自动化名：事件名」兜底）。
        旧兜底正是「一次派发念三句」的第三句：它让 d1/d2 这类没写文案的节点也出声，
        被 canary 观察窗隔成 5 分钟一句，出资人真机反馈的重复感即由此而来。
        """
        if self.on_emit_speak is None:
            return
        entity_id = str((data.get("entity_id") if isinstance(data, dict) else "") or "")
        # D11：模板优先（实时值插值）；渲不出来（实体不可读/未知占位符）⇒ 回退静态 announce
        text = self._speech_template_text(node, entity_id) or ""
        if not text:
            text = str(node.raw.get("announce") or "").strip()
        if not text:
            # fail-closed 静默：既无模板又无 announce ⇒ 不假装读数、不播占位符原文
            return
        room = str(node.room or (data.get("room") if isinstance(data, dict) else "") or "")
        try:
            self.on_emit_speak(str(instance.instance_id), text, room, entity_id)
        except Exception:  # noqa: BLE001 —— 播报旁路失败绝不改 emit 行为
            logger.warning(
                "EMIT_SPEAK_PUBLISH_FAILED emit 播报旁路异常（不影响 emit 核心） instance=%s node=%s",
                instance.instance_id, node.id, exc_info=True,
            )

    def _speak_for_do(self, instance: Instance, node: Node, *, dry_run: bool) -> None:
        """T1：do 节点下发前的额外 MQTT 播报（`butler/inbox/speak`）。

        文本取值顺序（v1.27.1 收口后）：节点级 `speech_template` 渲染成功 → 节点级
        `announce`（IR 扩展字段，node 段 additionalProperties=true）→ **静默**。
        ⛔ 不再机拼「`<自动化名>：<节点名>`」：那条兜底让没写文案的 do 也出声，
        一次派发因此念多句（PM 卡定性：出资人体感「重复」的真因）。
        房间：优先 `node.room`；**缺省时把目标实体传给回调**，由 Runtime 用 catalog 的
        `entity → area_id` 反查（2026-11-05 真机修：线上 IR 普遍不写 room，缺失不等于不能出声）。
        ⛔ 纯旁路：未注入回调 / dry-run / 取不到文本时直接 no-op；异常只吞 warning，
        绝不改变 do 的动作结果、边选择或实例状态（与 `on_ask_speak` 同一纪律）。
        """
        if self.on_do_speak is None or dry_run:
            return
        # 目标实体：HA 原生 `target.{entity_id}` 形态也要认（IR §7.1 安全约定要求两者都解析）
        entity_id = str((node.params or {}).get("entity_id") or "").strip()
        if not entity_id:
            target = (node.params or {}).get("target")
            if isinstance(target, dict):
                entity_id = str(target.get("entity_id") or "").strip()
        # D11：模板优先（实时值插值）；渲不出来 ⇒ 回退静态 announce
        text = self._speech_template_text(node, entity_id) or ""
        if not text:
            text = str(node.raw.get("announce") or "").strip()
        if not text:
            # fail-closed 静默（同上）：显式才出声，⛔ 不机拼
            return
        try:
            self.on_do_speak(
                str(instance.instance_id), text, str(node.room or ""), entity_id,
            )
        except Exception:  # noqa: BLE001 —— 播报旁路失败绝不改 do 行为
            logger.warning(
                "DO_SPEAK_PUBLISH_FAILED do 播报旁路异常（不影响 do 核心） instance=%s node=%s",
                instance.instance_id, node.id, exc_info=True,
            )

    def _speak_for_failure(self, instance: Instance, node: Node, result: CallResult) -> None:
        """**动作失败播报**（出资人 2026-11-08 19:34 亲令，卡名 `AF卡_v7_完整自动化` §八）。

        现场：18:29 那轮 `climate.set_temperature` 被拒，出资人在屋里**什么都没听到**——失败
        只进了 watch.log 与审计，人不看日志就等于"自动化没动静"。这条腿把失败念出来：
        文本＝「`<适配器>.<动作>` 执行失败：`<原因>`」。

        - 原因压成**单行**并截断：错误原文里有空格/换行/英文栈，直接念出来 TTS 断句全乱；
        - 房间/实体取法与 `_speak_for_do` **同源**（`node.room` → 目标实体交 Runtime 反查 catalog），
          ⛔ 不另造一套解析；
        - ⛔ 纯旁路：未注入回调即 no-op；异常只吞一行 warning，绝不改动作结果/边选择/实例状态。
        """
        if self.on_fail_speak is None:
            return
        action = f"{node.adapter}.{node.action}".strip(".")
        reason = " ".join(str(result.error or "").split()) or "未知原因"
        text = f"{action} 执行失败：{reason[:120]}"
        entity_id = str((node.params or {}).get("entity_id") or "").strip()
        if not entity_id:
            target = (node.params or {}).get("target")
            if isinstance(target, dict):
                entity_id = str(target.get("entity_id") or "").strip()
        try:
            self.on_fail_speak(
                str(instance.instance_id), text, str(node.room or ""), entity_id,
            )
        except Exception:  # noqa: BLE001 —— 播报旁路失败绝不改失败判定
            logger.warning(
                "FAIL_SPEAK_PUBLISH_FAILED 失败播报旁路异常（不影响失败判定） instance=%s node=%s",
                instance.instance_id, node.id, exc_info=True,
            )

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
        # emit 播报（AF-PM 裁定 2026-11-05）：emit 事件此前不走任何播报钩子 ⇒ `study_ac_started`
        # 这类播报永远不出声。这里挂**与 do 同源**的钩子，共用同一条 room 反查＋area 解析，
        # 三腿（ask/do/emit）同一闸、fail-closed 语义一致。⛔ 失败不影响 emit 本身。
        self._speak_for_emit(instance, node, data)
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
