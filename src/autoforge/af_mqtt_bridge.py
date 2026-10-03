"""AF ↔ MQTT 联动桥 —— ADM 联动执行计划·第 1 步 + 第 2 步。

角色边界（`20260929-联动协议修订-事件流与收件箱`）：

- AF 只发自己的语义主题 `af/automation/fired|failed`，**不 retained**（事件流不回放状态）；
- AF 只读 `ma/insights`：读到后编译候选 → **进审批队列**，绝不自动部署
  （conf 封顶在 ask 档，与 `af_evo.ProposalSink.conf_cap` 同口径 0.59 < ask_max 0.60）；
- AF **不订阅** `butler/inbox/*`——收件箱归 DB，AF 不替 DB 说话；本模块把这条做成
  可断言的常量 `FORBIDDEN_SUBSCRIPTIONS`，而不是注释里的一句"我们不订阅"。

连接与凭据一律交给机制层 `homesdk.mqtt`：缺 `MQTT_HOST` 即抛、缺凭据即抛、不匿名回退。
构造期不联网——`AfMqttBridge` 接受任何鸭子类型 client，所以门禁在没有 broker、
甚至没有 paho 的机器上也能真跑（本机就没有 paho，见 §"paho 缺席是可观测的"）。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

from homesdk import mqtt as _mqtt
from homesdk import presence as _presence
from homesdk.config import MissingEnv

from .af_time import SystemTimeSource, TimeSource, to_house_iso

__all__ = [
    "AfMqttBridge",
    "FIRED_TOPIC",
    "FAILED_TOPIC",
    "INSIGHTS_TOPIC",
    "FORBIDDEN_SUBSCRIPTIONS",
    "INSIGHT_CONF_CAP",
    "PRESENCE_NAME",
    "caps_payload",
    "env_enabled",
    "make_ask_sink",
    "make_client",
    "preflight",
    "start_from_env",
]

logger = logging.getLogger("autoforge.mqtt")

#: 本仓的 ADM 域名标识（`adm/autoforge/*`，只发自己的）。
PRESENCE_NAME = "autoforge"

FIRED_TOPIC = "af/automation/fired"
FAILED_TOPIC = "af/automation/failed"
INSIGHTS_TOPIC = "ma/insights"

#: 绝不订阅的主题族。inbox 的所有权与校验权在 DB（homesdk.presence.INBOX_TOPICS 就是那份白名单）。
FORBIDDEN_SUBSCRIPTIONS: tuple[str, ...] = tuple(sorted(_presence.INBOX_TOPICS)) + (
    "butler/inbox/#",
    "butler/inbox/*",
)

#: MA 洞察入审批时的 conf 上限：落在 ask 档（< `af_proposal.SHADOW_LOW`=0.60），
#: 于是 `ProposalManager.submit` 必然只出提案、不触达 `_auto_deploy`。
INSIGHT_CONF_CAP = 0.59

#: 桥的开关（生产在 compose 里给；未开启时 AF 行为与接入联动前逐字相同）。
ENV_ENABLED = "AUTOFORGE_MQTT"

#: 进程内保留的发布/拒绝记录上限（"只增不减"家族的既有纪律：有界，不是无限流水）。
MAX_HISTORY = 50

#: `af/automation/failed` 的 `error` 封顶。取契约表 §1.3 给展示类文本定的同一个数（500 字），
#: 因为 `fail_reason` 里会拼进异常 repr 与真机回执——不封顶等于把任意长度正文塞进 QoS 1 事件流。
MAX_ERROR_CHARS = 500

#: 执行链没写原因时的诚实占位。**不许退化成状态名**：`error` 恒等于 `"failed"` 时，
#: 这个字段携带的信息量是零，而 DB 侧正是拿它向用户解释"为什么失败"。
NO_FAILURE_REASON = "未记录失败原因（执行链未写入 fail_reason）"

#: `adm/autoforge/caps` 里对外声明的联动版本（ADM v2.5 联动，不是 AF 的发布号）。
PRESENCE_CAPS_VERSION = "2.5"


def env_enabled() -> bool:
    """`AUTOFORGE_MQTT` 是否被显式打开（空串/0/false 视为关）。"""
    raw = (os.environ.get(ENV_ENABLED) or "").strip().lower()
    return raw not in ("", "0", "false", "no", "off")


class BridgeUnavailable(RuntimeError):
    """桥无法安全启动（缺 broker 地址 / 缺凭据 / 无 paho）。"""


def preflight(*, peer: str = PRESENCE_NAME, allow_anonymous: bool = False) -> tuple[str, int, int]:
    """校验 broker 配置，返回 `(host, port, keepalive)`。

    刻意在**构造 paho client 之前**做：paho 没装的环境里，配置错误也必须看得见，
    不能被 `MqttUnavailable` 抢先遮住（否则"缺凭据"永远测不到）。
    """
    try:
        settings = _mqtt.broker_settings()
        _mqtt.resolve_credentials(peer, allow_anonymous=allow_anonymous)
    except MissingEnv as exc:
        raise BridgeUnavailable(f"MQTT 配置不完整（fail-closed）：{exc}") from exc
    return settings


def make_client(
    *,
    peer: str = PRESENCE_NAME,
    allow_anonymous: bool = False,
    qos: int = _mqtt.QOS,
) -> Any:
    """按部署环境构造并**连接** client。缺配置/缺 paho 一律抛，不返回"半成品桥"。"""
    host, port, keepalive = preflight(peer=peer, allow_anonymous=allow_anonymous)
    try:
        client = _mqtt.get_client(
            _mqtt.default_client_id(peer), peer=peer, allow_anonymous=allow_anonymous, qos=qos
        )
    except _mqtt.MqttUnavailable as exc:
        raise BridgeUnavailable(
            f"broker 配置齐了，但这个镜像里没有 paho-mqtt：{exc}"
        ) from exc
    client.connect(host, port, keepalive)
    if hasattr(client, "loop_start"):
        client.loop_start()
    return client


def make_ask_sink(*, clock: TimeSource | None = None, audit: Any = None) -> Any:
    """默认落点：进程内 ask 审批队列（`ProposalManager`，且**不带 deployer**）。

    ⚠️ 进程内 = 重启清零。生产入口现在用 `make_durable_ask_sink()`（DCD 20261002 §三 ④A
    要求"先持久化到独立队列"）；本工厂留给需要 conf 三级分流/审计联动的调用方与测试。

    为什么不是持久化的 `af_pending`：那条队列是**可执行**的（人点 approve 就落盘部署）。
    对端经 MQTT 投来的洞察要不要进那条队列属跨仓安全边界，已交 DCD；在此之前只进这条
    "只出提案、谁都部署不了"的队列（`INSIGHT_CONF_CAP` < ask_max，band 必为 ask）。
    """
    from .af_audit import AuditLog
    from .af_conf import ConfidenceStore
    from .af_feedback import FeedbackRecorder
    from .af_proposal import ProposalManager

    run_clock: TimeSource = clock if clock is not None else SystemTimeSource()
    conf = ConfidenceStore()
    log = audit if audit is not None else AuditLog()
    return ProposalManager(
        conf=conf,
        recorder=FeedbackRecorder(conf=conf, clock=run_clock, audit=log),
        clock=run_clock,
        audit=log,
        deployer=None,
    )


def make_durable_ask_sink(*, store_root: Any, clock: TimeSource | None = None) -> Any:
    """生产落点：MA 洞察进**只落盘、不可部署**的独立队列（`af_insight_queue`）。

    DCD `20261002` §三 ④A 裁的正是这条："先持久化到独立队列，仍不可直接部署
    （approve 之后才进 `af_pending`）"。与 `make_ask_sink` 的差别只有一条：
    进程内的提案重启即清零，磁盘上的不会。
    """
    from .af_insight_queue import InsightQueue, PersistentInsightSink

    return PersistentInsightSink(
        InsightQueue(Path(str(store_root)) / "insight_proposals", clock=clock)
    )


def caps_payload(
    *, tools: tuple[str, ...] | list[str] = (), version: str = "", extra: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """`adm/autoforge/caps` 的载荷。`tools` 由入口层（L2）传入——桥在 L1，不许 import `af_mcp`。"""
    caps: dict[str, Any] = {
        "mcp": bool(tools),
        "tools": list(tools),
        "version": version,
    }
    if extra:
        caps.update(extra)
    return caps


def _graph_doc(graph: Any) -> dict[str, Any] | None:
    """把 staging 里的 Graph 取成 IR 图文档 `{automations: [...]}`；取不到就 None（不猜形状）。"""
    if isinstance(graph, Mapping):
        return dict(graph)
    raw = getattr(graph, "raw", None)
    if isinstance(raw, Mapping):
        return dict(raw)
    automations = getattr(graph, "automations", None)
    if automations:
        docs = []
        for auto in automations:
            body = getattr(auto, "raw", None)
            if not isinstance(body, Mapping):
                return None  # 有一半取不到形状：宁可不带 IR，也不投递半份候选
            docs.append(dict(body))
        return {"automations": docs}
    return None


#: 契约表 `ma/insights` 的载荷形状（裁定 20261002 Q3）：
#: `{trace_id, ts, kind, persons[], room?, summary, evidence[], snapshot_url?}`——**没有 conf 这一项**。
#: 所以"缺 conf"不能当拒收理由（否则 AF 会把每一条按契约发来的洞察丢掉，而注册表才是唯一真源）；
#: 但"报了却报坏"（越界 / NaN / 非数）照旧拒收，且缺报必须记账成 `conf_reported=False`，
#: 让批的人看见"MA 没给置信度"，而不是被一个凭空造的数安慰。
def _conf_of_payload(payload: Mapping[str, Any]) -> tuple[float | None, bool]:
    """返回 `(置信度或 None, MA 有没有报这个数)`；None = 坏报，由调用方拒收。

    两件事分开判：
    - **缺报**：`conf`/`confidence` 两处都没有，或在场但是 `null`——MA 明确发 null 占位与根本不发
      这个键是同一件事，把它当坏报拒收等于让一个占位符实现把每条洞察丢掉（还是判例 1 那个
      静默归零，只是这回由 AF 自己动手）。按 `0.0` 收，必然落 ask 档。
    - **坏报**：报了但不是 [0,1] 的实数（越界 / NaN / 字符串 / 布尔）——对端确实给了一个数却没给对，
      照旧拒并留痕。`bool` 不算数：`True` 是 1 还是"是"，AF 不替对端解释。
    """
    raw = payload.get("conf")
    if raw is None:
        raw = payload.get("confidence")
    if raw is None:
        return 0.0, False
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None, True
    value = float(raw)
    if not 0.0 <= value <= 1.0:  # NaN 与自身比较为假，走不到 return
        return None, True
    return value, True


#: `transport` 记账的边界：载荷来自对端，不封顶就等于让 broker 决定我们的内存和面板宽度。
TRANSPORT_TEXT_LIMIT = 120
TRANSPORT_PERSON_LIMIT = 12
TRANSPORT_EVIDENCE_PREVIEW = 3


def _clip(value: Any, limit: int = TRANSPORT_TEXT_LIMIT) -> str:
    return str(value)[:limit]


def _transport_of(
    payload: Mapping[str, Any],
    *,
    id_key: str,
    conf_reported: bool,
) -> dict[str, Any]:
    """留住契约表 `ma/insights` 里 AF **不消费、但批的人要看**的那几项（有界）。

    这些字段不进 IR、不参与任何部署判定，只随提案落盘：`kind` 是词表分类，`persons[]` 是
    多人同框的当事人，`room`/`evidence` 是证据。`conf_reported` 记的是"MA 到底有没有报置信度"——
    缺报时 AF 按 0.0 落 ask 档，但面板不许把"没报"显示成"报了 0.0"（那会把对端的沉默
    读成对端的否定，也会让人以为这个数有来源）。
    """
    transport: dict[str, Any] = {"id_key": id_key, "conf_reported": conf_reported}
    kind = payload.get("kind")
    if kind is not None:
        transport["kind"] = _clip(kind)
    persons = payload.get("persons")
    if isinstance(persons, (list, tuple)):
        transport["persons"] = [_clip(p) for p in list(persons)[:TRANSPORT_PERSON_LIMIT]]
    room = payload.get("room")
    if room is not None:
        transport["room"] = _clip(room)
    evidence = payload.get("evidence")
    if isinstance(evidence, (list, tuple)):
        transport["evidence_count"] = len(evidence)
        transport["evidence_preview"] = [_clip(e) for e in list(evidence)[:TRANSPORT_EVIDENCE_PREVIEW]]
    ts = payload.get("ts")
    if ts is not None:
        transport["ts"] = _clip(ts)
    if payload.get("snapshot_url") is not None:
        transport["has_snapshot"] = True
    return transport


def _failure_reason(instance: Any) -> str:
    """出向 `error` 取执行链写下的原因：`InstanceManager.fail()` 把它落在 `ctx.context["fail_reason"]`。

    传状态名会让契约字段恒等于 `"failed"`——DB 拿到"失败了"而不是"为什么失败"，
    而 `af_executor` 的每条 `_fail()` 都带了具体原因（失败的动作、软失效的异常、收敛违约）。
    """
    context = getattr(getattr(instance, "ctx", None), "context", None)
    raw = context.get("fail_reason") if isinstance(context, Mapping) else None
    return _clip(str(raw or "").strip() or NO_FAILURE_REASON, MAX_ERROR_CHARS)


class AfMqttBridge:
    """把 AF 的终态事件发出去、把 MA 洞察收进来。client 由调用方注入（生产用 `make_client`）。"""

    def __init__(
        self,
        client: Any,
        *,
        proposal_sink: Any = None,
        clock: TimeSource | None = None,
        subscribe_insights: bool = True,
    ) -> None:
        self.client = client
        self.proposal_sink = proposal_sink
        self.clock: TimeSource = clock if clock is not None else SystemTimeSource()
        self.subscribe_insights = subscribe_insights
        self.started = False
        self.published: list[dict[str, Any]] = []
        self.rejected: list[dict[str, str]] = []
        self.errors: list[str] = []
        self.counts: dict[str, int] = {
            "fired": 0,
            "failed": 0,
            "insights_in": 0,
            "insights_rejected": 0,
            "publish_errors": 0,
            "forbidden_seen": 0,
        }

    # ── 出向：presence + 事件 ─────────────────────────────────────────
    def advertise(self, *, caps: Mapping[str, Any] | None = None, offline: bool = False) -> None:
        """发布 `adm/autoforge/status`（retained）+ LWT；`offline=True` 用于优雅退出。"""
        _presence.advertise(self.client, PRESENCE_NAME, caps=dict(caps) if caps else None, offline=offline)

    def _publish(self, topic: str, payload: dict[str, Any]) -> dict[str, Any]:
        """发一条不 retained 的事件。发布失败**不冒到执行链**，但必须留痕（假绿即缺陷）。"""
        try:
            _mqtt.publish(self.client, topic, payload, retain=False)
        except Exception as exc:  # noqa: BLE001 —— broker 抖动不该让自动化崩
            self.counts["publish_errors"] += 1
            self._keep(self.errors, f"{topic}: {type(exc).__name__}: {exc}")
            logger.warning("[mqtt] 发布 %s 失败（已计入 publish_errors）：%r", topic, exc)
            return {"published": False, "topic": topic, "error": repr(exc)}
        self._keep(self.published, {"topic": topic, "payload": payload})
        return {"published": True, "topic": topic, "payload": payload}

    @staticmethod
    def _keep(bucket: list, item: Any) -> None:
        bucket.append(item)
        if len(bucket) > MAX_HISTORY:
            del bucket[: len(bucket) - MAX_HISTORY]

    def _envelope(self, *, automation_id: str, instance_id: str, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """载荷形态对齐 ADM 主题契约表 §1.2：`{trace_id, ts, automation_id, ref}`。

        `ref` 按裁定 20261002 §三 ②A 定义为**实例 id**（保持本实现，一次部署会 fire 多次，
        所以 deploy ref 语义更错）。`instance_id` 是同值过渡字段，**删除时点 = AF v2.6**
        （改名/删除属破坏性变更，须与停机窗同做），在此之前两边同写、下游任选其一。
        `ts` 走家庭墙钟口径（契约 §四），不是机器时区也不是 UTC。
        """
        payload: dict[str, Any] = {
            "trace_id": uuid4().hex[:12],
            "ts": to_house_iso(self.clock.now()),
            "automation_id": automation_id,
            "ref": instance_id,
            "instance_id": instance_id,
        }
        if extra:
            payload.update({k: v for k, v in extra.items() if v is not None})
        return payload

    def publish_fired(self, *, automation_id: str, instance_id: str, **extra: Any) -> dict[str, Any]:
        return self._publish(FIRED_TOPIC, self._envelope(automation_id=automation_id, instance_id=instance_id, extra=extra))

    def publish_failed(self, *, automation_id: str, instance_id: str, error: str = "", **extra: Any) -> dict[str, Any]:
        payload = self._envelope(automation_id=automation_id, instance_id=instance_id, extra=extra)
        payload["error"] = str(error)
        return self._publish(FAILED_TOPIC, payload)

    def observe_terminal(self, instance: Any, state: str) -> dict[str, Any] | None:
        """`Runtime.add_terminal_observer` 的适配器：done→fired，failed→failed，其余不发。

        标识只取 `instance.instance_id`——审计第一轮那条 P0 就是把 `instance.id` 当标识用。
        """
        automation_id = str(instance.automation.id)
        instance_id = str(instance.instance_id)
        node_id = str(getattr(instance, "current_node_id", "") or "")
        if state == "done":
            self.counts["fired"] += 1
            return self.publish_fired(automation_id=automation_id, instance_id=instance_id, node_id=node_id)
        if state == "failed":
            self.counts["failed"] += 1
            return self.publish_failed(
                automation_id=automation_id,
                instance_id=instance_id,
                node_id=node_id,
                error=_failure_reason(instance),
            )
        return None

    # ── 入向：ma/insights ─────────────────────────────────────────────
    def start(self, *, caps: Mapping[str, Any] | None = None) -> None:
        """上线：presence 广播 + 订阅洞察主题。订阅表在此收口，禁订族先判后订。"""
        for topic in FORBIDDEN_SUBSCRIPTIONS:
            if topic == INSIGHTS_TOPIC:  # 白名单若被误改，宁可启动失败也不越界
                raise BridgeUnavailable(f"{INSIGHTS_TOPIC} 不该出现在禁订清单里（请核对裁定）")
        self.advertise(caps=caps)
        if self.subscribe_insights:
            self.client.on_message = self.handle_message
            self.client.subscribe(INSIGHTS_TOPIC, qos=_mqtt.QOS)
        self.started = True

    def stop(self) -> None:
        """优雅下线：显式发 retained `offline`（LWT 只覆盖异常断连）。"""
        self.advertise(offline=True)
        self.started = False

    def subscribe_topic(self, topic: str) -> bool:
        """唯一订阅入口：禁订族一律拒，并计数留痕。"""
        if topic in FORBIDDEN_SUBSCRIPTIONS or topic.startswith("butler/inbox/"):
            self.counts["forbidden_seen"] += 1
            self._keep(self.rejected, {"topic": topic, "reason": "forbidden_subscription"})
            return False
        self.client.subscribe(topic, qos=_mqtt.QOS)
        return True

    def handle_message(self, _client: Any, _userdata: Any, msg: Any) -> dict[str, Any]:
        """paho 回调：只认 `ma/insights`，别的主题即使被误投也只是计数，不执行任何动作。"""
        topic = msg.topic.decode("utf-8", "replace") if isinstance(msg.topic, bytes) else str(msg.topic)
        if topic != INSIGHTS_TOPIC:
            if topic in FORBIDDEN_SUBSCRIPTIONS or topic.startswith("butler/inbox/"):
                self.counts["forbidden_seen"] += 1
                self._keep(self.rejected, {"topic": topic, "reason": "forbidden_topic_not_handled"})
            else:
                self._keep(self.rejected, {"topic": topic, "reason": "unhandled_topic"})
            return {"handled": False, "reason": "topic_not_mine", "topic": topic}
        try:
            raw = msg.payload
            text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
            payload = json.loads(text)
        except Exception as exc:  # noqa: BLE001 —— 对端发坏 JSON 是常态，拒收并留痕
            return self._reject("undecodable_payload", repr(exc))
        if not isinstance(payload, Mapping):
            return self._reject("payload_not_object", type(payload).__name__)
        self.counts["insights_in"] += 1
        return self.ingest_insight(payload)

    def _reject(self, reason: str, detail: str = "") -> dict[str, Any]:
        self.counts["insights_rejected"] += 1
        self._keep(self.rejected, {"reason": reason, "detail": detail})
        logger.warning("[mqtt] 丢弃 ma/insights：%s %s", reason, detail)
        return {"handled": False, "reason": reason, "detail": detail}

    def ingest_insight(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """洞察 → 候选 IR（有 intent 时经 `af_draft` 编译）→ 审批队列。永远只到 ask 档。

        键名以**契约表**为准：`ma/insights` 发的是 `{trace_id, ts, kind, persons[], room?, summary,
        evidence[], snapshot_url?}`。AF 早先自定的 `hypothesis_id` / `natural_language` 作为别名继续收
        （MA 侧承诺"旧键保留到 DB/AF 迁完"），两个方向都能进来，用的是哪个键记在 `transport` 里。
        """
        hypothesis_id_raw = payload.get("hypothesis_id")
        uses_legacy_id = hypothesis_id_raw is not None and str(hypothesis_id_raw).strip() != ""
        hypothesis_id = str(hypothesis_id_raw or payload.get("trace_id") or "").strip()
        if not hypothesis_id:
            return self._reject("missing_insight_id")
        natural_language = str(
            payload.get("natural_language") or payload.get("insight") or payload.get("summary") or ""
        ).strip()
        intent = payload.get("intent")
        if not natural_language and not isinstance(intent, Mapping):
            return self._reject("missing_natural_language_and_intent")
        conf, conf_reported = _conf_of_payload(payload)
        if conf is None:
            return self._reject("conf_out_of_range", str(payload.get("conf") or payload.get("confidence")))
        if self.proposal_sink is None:
            return self._reject("no_proposal_sink_wired")
        transport = _transport_of(
            payload,
            id_key="hypothesis_id" if uses_legacy_id else "trace_id",
            conf_reported=conf_reported,
        )

        capped = min(conf, INSIGHT_CONF_CAP)
        suggested_ir: dict[str, Any] | None = None
        summary = natural_language
        if isinstance(intent, Mapping):
            from . import af_draft  # 延迟 import：桥不该在 import 期拖进编译管线

            try:
                draft = af_draft.draft_intent(dict(intent), session_id=f"mqtt:{hypothesis_id}")
            except Exception as exc:  # noqa: BLE001 —— 候选编译失败就退回纯 NL 提案，但必须留痕
                self._keep(self.errors, f"draft_intent: {exc!r}")
                logger.warning("[mqtt] intent 编译失败，退回无 IR 提案：%r", exc)
            else:
                summary = str(draft.get("summary") or natural_language)
                staged = af_draft.get_staged(draft["ref"])
                suggested_ir = _graph_doc(staged.get("graph"))
        result = self.proposal_sink.submit(
            hypothesis_id=hypothesis_id,
            natural_language=summary,
            conf=capped,
            suggested_ir=suggested_ir,
            source="ma",
            transport=transport,
        )
        return {
            "handled": True,
            "proposal_id": str(getattr(result, "proposal_id", "") or ""),
            "conf_submitted": capped,
            "conf_reported": conf_reported,
            "had_ir": suggested_ir is not None,
        }

    def stats(self) -> dict[str, Any]:
        return {
            "started": self.started,
            "counts": dict(self.counts),
            "recent_published": list(self.published),
            "recent_rejected": list(self.rejected),
            "recent_errors": list(self.errors),
        }


def start_from_env(
    *,
    tools: tuple[str, ...] | list[str] = (),
    version: str = PRESENCE_CAPS_VERSION,
    proposal_sink: Any = None,
    clock: TimeSource | None = None,
    allow_anonymous: bool = False,
) -> AfMqttBridge | None:
    """按环境变量拉起桥；未开启时返回 None（AF 行为与接入联动前逐字相同）。

    开启但配置不齐 → 抛 `BridgeUnavailable`（fail-closed：宁可拒绝启动，也不静默降级成
    "看起来在跑、其实一句都没说"的假桥）。

    `version` 的默认值是**计划号本身**而不是空串：裁定 20261002 Q7 规定 `caps.version` = 计划号，
    而这条是 retained 发布——空串会被 DB 读成"AF 报了一个没有版本号的 caps"，且要等对端查账才看得见。
    """
    if not env_enabled():
        return None
    client = make_client(allow_anonymous=allow_anonymous)
    bridge = AfMqttBridge(client, proposal_sink=proposal_sink, clock=clock)
    bridge.start(caps=caps_payload(tools=tools, version=version))
    return bridge


_BRIDGES: list[AfMqttBridge] = []


def current_observers() -> tuple[Callable[[Any, str], None], ...]:
    """Runtime 终态旁观者清单（进程级；单写者锁已保证同机单实例）。"""
    return tuple(bridge.observe_terminal for bridge in _BRIDGES)


def attach(bridge: AfMqttBridge) -> None:
    """让每个新 Runtime 的终态都流向这座桥（等价于"自动化一落地就吱一声"）。"""
    if bridge not in _BRIDGES:  # 按桥本体去重：bound method 每次取都是新对象
        _BRIDGES.append(bridge)


def detach(bridge: AfMqttBridge) -> None:
    if bridge in _BRIDGES:
        _BRIDGES.remove(bridge)
