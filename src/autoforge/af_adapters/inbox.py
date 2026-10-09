"""收件箱适配器 —— AF 作为 `butler/inbox/*` 的正式投递方（计划 §七 卡1，契约 §1.3/§1.5）。

三条动作：`do` 节点 + `adapter: inbox` + `action: inbox.speak | inbox.notify | inbox.tv`。
DCD 那张卡把它们写成 `inbox_speak`/`inbox_notify`/`inbox_tv`「节点」，而 AF 的 IR 里出向动作只有
`do`+`adapter`+`action` 这一种形态（`af_ir.NODE_KINDS` 不含按业务命名的节点类型；另立一族会同时
破 `ir.schema.json` 和 `classify_action` 的风险分级）。所以落法就是 `adapter: inbox`，一一对应。

**载荷 schema 不在本文件重抄**：必填/可选键直接读 `homesdk.presence.<kind>` 的函数签名
（在 `af_mqtt_bridge.inbox_publish` 里做），长度上限（`text≤500`/`title≤80`/`body≤500`）与
`ts` 的 epoch 口径由库侧把（0.3.2 规格 §三.1：谁定 schema 谁把校验）。本模块只决定"这次调用
对外报什么"。

三道 fail-closed 也都收在 `af_mqtt_bridge.inbox_publish`（贴近线上那一侧，绕不过去）：
载荷侧 `ADM_ERR_PAYLOAD_INVALID`、通道侧 `ADM_ERR_AUTH_REQUIRED`（计划验收点名的"缺凭据拒发"）、
传输侧 `ADM_ERR_BROKER_UNREACHABLE` + retained status 转 degraded。本模块把结果映射成
`CallResult`：失败就失败（走 IR 的 `on_error`，无则实例 failed），绝不把"没送出去"报成 done。

`dry_run=True` 是 AF 的缺省档：只在进程内记一条意图，内容就是库侧真正会上线的那份字节，
一行都不发。仿真面、`stage=dry_run`、`/api/live/run` 的 dry_run 都走这条路。
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from homesdk.adm.errors import ADM_ERR_INTERNAL

from .base import CallResult

__all__ = ["InboxAdapter", "INTENTS_MAX", "kind_of"]

#: 意图环的条数上限。与 `af_adapters/http.py` 同一纪律：常驻服务里每次 dry_run 都记一条，
#: 不封顶就是只增不减（稳定性审计 BUG-01 同族）。
INTENTS_MAX = 200


def kind_of(action: str) -> str:
    """`inbox.speak` 与 `speak` 两种写法都归一到 `speak`。

    这里**不判名单**：可投递主题的白名单真源是 `homesdk.presence.INBOX_TOPICS`，由桥按它判
    （见 `af_mqtt_bridge.INBOX_KINDS`）。适配器再抄一份就等于留下两个可以各自漂移的名单。
    """
    lowered = str(action or "").strip().lower()
    return lowered.rsplit(".", 1)[-1]


class InboxAdapter:
    """单次调用、无重试、无降级——失败只如实带回 `CallResult.fail`（协议见 `base.Adapter`）。"""

    name = "inbox"

    def __init__(
        self,
        dry_run: bool = True,
        *,
        bridge_provider: Callable[[], Any] | None = None,
    ) -> None:
        self.dry_run = dry_run
        # 每次调用现取桥，不在构造期抓引用：`serve` 的顺序是 build_app → 起桥，
        # 装配期抓到的永远是 None（同 `af_mqtt_bridge.current_bridge()` 的说明）。
        self._bridge_provider = bridge_provider
        self.intents: list[dict[str, Any]] = []  # bounded-cache: exempt(dry_run 意图环形清单：唯一写入口 `_remember()` 按 INTENTS_MAX 裁头，读侧是仿真效果展开与测试；与 af_adapters/http.py 的 intents 同形，没有 TTL 腿——dry_run 记的就是"本会发什么"，不该自行过期)

    # ── 调用面 ─────────────────────────────────────────────────────
    def call(self, action: str, params: Mapping[str, Any]) -> CallResult:
        from .. import af_mqtt_bridge  # 局部导入：线上一律从桥模块出去（判据 A 的射程）

        result = af_mqtt_bridge.inbox_publish(
            kind_of(action),
            fields=params or {},
            bridge=self._current_bridge(),
            dry_run=self.dry_run,
        )
        topic = str(result.get("topic") or "")
        trace_id = str(result.get("trace_id") or "")

        if result.get("dry_run"):
            self._remember(result)
            return CallResult.ok(
                {
                    "dry_run": True,
                    "action": action,
                    "topic": topic,
                    "trace_id": trace_id,
                    "payload": result["payload"],
                }
            )
        if not result.get("published"):
            return CallResult.fail(
                str(result.get("error") or "收件箱投递失败"),
                code=str(result.get("code") or ADM_ERR_INTERNAL),
                action=action,
                topic=topic,
            )
        return CallResult.ok(
            {
                "published": True,
                "action": action,
                "topic": topic,
                "trace_id": trace_id,
                "payload": result["payload"],
                "qos": result.get("qos"),
                "retain": result.get("retain"),
            }
        )

    # ── 内部 ───────────────────────────────────────────────────────
    def _current_bridge(self) -> Any:
        if self._bridge_provider is not None:
            return self._bridge_provider()
        from .. import af_mqtt_bridge

        return af_mqtt_bridge.current_bridge()

    def _remember(self, result: Mapping[str, Any]) -> None:
        if len(self.intents) >= INTENTS_MAX:
            del self.intents[0]
        self.intents.append(
            {
                "topic": result["topic"],
                "trace_id": result["trace_id"],
                "payload": result["payload"],
            }
        )
