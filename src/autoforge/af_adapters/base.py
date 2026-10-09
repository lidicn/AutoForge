"""适配器纯执行层契约（IR §4.4，最高优先级架构原则）。

**适配器只做"单次指令下发 → 结果返回"**，不得包含超时、重试、降级、缓冲、批处理逻辑。
容错逻辑必须在 IR 图中显式定义：
    重试  = `wait` + `on_error` + 计数变量
    超时  = IR 层计时器

边界划分：
- **适配器负责传输层**：连接、鉴权、序列化、TLS、以及**套接超时**
  （⚠️ 传输层超时必须保留，否则单进程会永久挂死）
- **IR / 语义层负责**：重试、退避、降级、业务超时、补偿

静态扫描会拦截 `params` 里出现的策略参数（见 `POLICY_PARAMS`）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable

__all__ = [
    "AdapterError",
    "CallResult",
    "FaultQueue",
    "Adapter",
    "AdapterRegistry",
    "POLICY_PARAMS",
    "L0_READONLY",
    "L1_IDEMPOTENT",
    "L2_RISKY",
    "L3_DANGEROUS",
    "classify_action",
    "is_destructive",
]

#: IR 未定义的策略参数——出现在适配器配置里即为违反纯执行层契约（静态扫描第 9 项）
POLICY_PARAMS = frozenset(
    {
        "retry",
        "retries",
        "max_attempts",
        "fallback",
        "fallback_action",
        "backoff",
        "retry_delay",
        "on_retry",
        "timeout",  # 语义层超时：IR 里用 wait + on_timeout 表达
    }
)

L0_READONLY = "L0"
L1_IDEMPOTENT = "L1"
L2_RISKY = "L2"
L3_DANGEROUS = "L3"

_L3_DOMAINS = frozenset({"shell_command", "command_line", "python_script", "hassio", "supervisor", "rest_command"})
_L2_DOMAINS = frozenset({"lock", "cover", "climate", "alarm_control_panel", "valve", "water_heater", "number", "select"})
_L1_DOMAINS = frozenset(
    {"light", "switch", "fan", "input_boolean", "humidifier", "media_player", "vacuum", "scene", "script", "siren", "tts"}
)
#: `inbox`（AF 投 DB 公共收件箱）落 L0：它不动家中任何执行器，只是"请 DB 说话/推送/上屏"，
#: 播不播仍由 DB 侧 Sentinel 闸门判（契约 §1.3），与 HA 的 `notify` 同形。
#: 留在未知档会被保守判成 L2 ⇒ 每条播报都要配 canary，`do inbox inbox.speak` 连编译都过不去。
_L0_DOMAINS = frozenset({"notify", "persistent_notification", "logbook", "inbox"})

_DESTRUCTIVE_KEYWORDS = ("delete", "remove", "purge", "wipe", "format", "reset", "drop")


class AdapterError(Exception):
    """适配器层异常。运行时按 IR §5.3 走 `on_error`，无 `on_error` 则 failed。"""


@dataclass(frozen=True)
class CallResult:
    """适配器调用结果（标准化，**不隐式吞错**）。"""

    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @classmethod
    def ok(cls, data: Mapping[str, Any] | None = None) -> "CallResult":
        return cls(success=True, data=dict(data or {}))

    @classmethod
    def fail(cls, error: str, **data: Any) -> "CallResult":
        return cls(success=False, data=dict(data), error=error)


class FaultQueue:
    """适配器层故障队列（G5 故障注入，IR §9.5）。

    适配器本身仍**只做单次下发**——故障注入是**测试底座**能力：把"下一次调用会
    超时/丢包/实体不可用"排队，`call()` 消费一个后按正常"失败"路径返回
    `CallResult.fail`。这样故障不改变生产契约（无重试、无降级），只改变结果。

    `kind` 取值（与 `af_fault.FaultKind` 对齐）：
        fail / timeout / drop / unavailable
    """

    def __init__(self) -> None:
        self._items: list[tuple[str, str]] = []

    def push(self, kind: str, error: str) -> None:
        self._items.append((str(kind), str(error)))

    def pop(self) -> tuple[str, str] | None:
        return self._items.pop(0) if self._items else None

    def empty(self) -> bool:
        return not self._items

    def clear(self) -> None:
        self._items.clear()

    def __len__(self) -> int:
        return len(self._items)


@runtime_checkable
class Adapter(Protocol):
    """适配器协议：单次调用，无重试、无降级、无语义层超时。"""

    name: str

    def call(self, action: str, params: Mapping[str, Any]) -> CallResult:
        """下发一次指令并返回结果。失败通过 CallResult 表达，不抛异常（除非传输层不可达）。"""
        ...


class AdapterRegistry:
    """按名字查找适配器（IR 的 `do.adapter` 字段）。"""

    def __init__(self, adapters: Mapping[str, Adapter] | None = None):
        self._adapters: dict[str, Adapter] = dict(adapters or {})

    def register(self, adapter: Adapter) -> None:
        self._adapters[adapter.name] = adapter

    def get(self, name: str) -> Adapter:
        try:
            return self._adapters[name]
        except KeyError as exc:
            raise AdapterError(f"未注册的适配器：{name!r}（已注册：{sorted(self._adapters)}）") from exc

    def __contains__(self, name: object) -> bool:
        return name in self._adapters

    def values(self) -> list[Adapter]:
        """已注册的全部适配器（v1.7.1）。

        存在的意义：`expect` 断言求值需要问「有没有动作没被仿真底座建模」——
        未建模动作的后果**根本没被验证过**，此时 `fully_verified` 必须为 False。
        没有这个出口，调用方只能按名字猜（`ha`/`mock`），漏掉自定义适配器。
        """
        return list(self._adapters.values())


def is_destructive(action: str) -> bool:
    """删除类 / 非幂等批量动作识别（L3）。"""
    lowered = action.lower()
    return any(k in lowered for k in _DESTRUCTIVE_KEYWORDS)


def classify_action(adapter: str, action: str, params: Mapping[str, Any] | None = None) -> str:
    """动作风险分级（IR §8.1）。未知 domain 保守按 L2。

    L0 只读/通知 ｜ L1 灯/开关/风扇（幂等）｜ L2 门锁/窗帘/空调 ｜ L3 外网、删除类、非幂等批量
    """
    if adapter == "http":
        return L3_DANGEROUS
    lowered = action.lower()
    if is_destructive(lowered):
        return L3_DANGEROUS
    # domain 优先取 action 的 `<domain>.<service>` 前缀（HA 服务名自带实体域）。动作名不带点时
    # 退回**适配器名**：DSL 的 `do d1 inbox.speak {…}` 会 partition 成 `adapter=inbox` +
    # `action=speak`，只按动作名判会让所有非 HA 适配器落进"未知 domain ⇒ L2"的保守缺省档——
    # 于是收件箱这类纯投递动作在扫描阶段就被判 `L2_NEEDS_CONFIRM`/`L2_NEEDS_CANARY`，
    # 连编译都过不去。缺省档本身不动（不带点又不落在适配器名表里仍判 L2）。
    domain = lowered.split(".", 1)[0] if "." in lowered else str(adapter or "").lower()
    if domain in _L3_DOMAINS:
        return L3_DANGEROUS
    if domain in _L2_DOMAINS:
        return L2_RISKY
    if domain in _L1_DOMAINS:
        return L1_IDEMPOTENT
    if domain in _L0_DOMAINS:
        return L0_READONLY
    return L2_RISKY
