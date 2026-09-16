"""G5 故障注入（IR §9.5 / §9.4）。

**只做正向验证等于没验证**——家庭里最常见的故障就是传感器掉线与 `unavailable`
（`IR_AND_RUNTIME` §9.5）。本模块统一描述五类故障并提供**注入原语**，
供测试底座（`MockAdapter` / `FakeHA` / `VhassHarness`）构造故障场景。

五类故障（IR §9.5）
--------------------
| 故障 | 注入层 | 语义 | 期望执行器行为 |
|---|---|---|---|
| `UNAVAILABLE` | 状态源 | 实体掉线，状态为 `unavailable` | 表达式求值失败 → `on_error` 软失效 + 漂移告警 |
| `TIMEOUT` | 适配器 | 传输层超时 | 单次失败 → `on_error`（**不重试**） |
| `REORDER` | 事件 | 事件乱序到达/重放 | 去重/熔断兜底，最终状态收敛 |
| `DROP` | 适配器 | 消息丢包，指令未达设备 | 单次失败 → `on_error` |
| `DRIFT` | 状态源 | 动作后状态未达预期 | canary 检测漂移 → 自动回滚 + 审计 |

四类失败（IR §9.4）落执行器断言
--------------------------------
| 失败类型 | 期望落点 | 是否自动恢复 |
|---|---|---|
| 设备故障（unavailable / 报错） | `on_error` 软失效 + `ACTION_FAILED`/`ENTITY_DRIFT` 审计 | ❌ |
| 超时（wait / ask） | `on_timeout` | ✅ |
| 中断取消 | `on_cancel`（**取消不回滚**） | ✅ |
| 业务拒绝（用户 no / 歧义） | `no` / `default` 分支（非失败） | ✅ |

设计边界：本模块**不侵入生产 Runtime 主链路**，故障只改变测试底座的结果，
适配器仍严格"单次下发、无重试、无降级"（KICKOFF 红线）。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence, TypeVar

__all__ = [
    "FaultKind",
    "FaultSpec",
    "FaultPlan",
    "FaultInjectionError",
    "FAULT_META",
    "FOUR_FAILURES",
    "unavailable",
    "timeout",
    "drop",
    "drift",
    "reorder",
    "set_entity_state",
    "inject_unavailable",
    "inject_drift",
    "inject_adapter_fault",
    "reorder_events",
]


class FaultInjectionError(Exception):
    """故障无法注入（目标底座不支持该注入层）。"""


class FaultKind(str, Enum):
    """IR §9.5 五类故障。str 子类便于直接与字符串比较 / 序列化。"""

    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    REORDER = "reorder"
    DROP = "drop"
    DRIFT = "drift"


#: 供 UI/文档展示的静态元数据（只读，不参与注入逻辑）
FAULT_META: dict[str, dict[str, str]] = {
    FaultKind.UNAVAILABLE.value: {
        "label": "实体掉线（unavailable）",
        "inject_layer": "state",
        "expected_handler": "on_error 软失效 + 漂移告警",
    },
    FaultKind.TIMEOUT.value: {
        "label": "网络/传输层超时",
        "inject_layer": "adapter",
        "expected_handler": "on_error（单次失败，不重试）",
    },
    FaultKind.REORDER.value: {
        "label": "事件乱序",
        "inject_layer": "event",
        "expected_handler": "去重/熔断兜底，最终状态收敛",
    },
    FaultKind.DROP.value: {
        "label": "消息丢包",
        "inject_layer": "adapter",
        "expected_handler": "on_error（指令未达设备）",
    },
    FaultKind.DRIFT.value: {
        "label": "状态漂移",
        "inject_layer": "state",
        "expected_handler": "canary 检测漂移 + 自动回滚 + 审计",
    },
}


#: IR §9.4 四类失败 → 期望落点（供验收断言对照）
FOUR_FAILURES: dict[str, dict[str, str]] = {
    "device": {"label": "设备故障", "edge": "on_error", "audit": "action_failed/entity_drift", "recoverable": "no"},
    "timeout": {"label": "超时", "edge": "on_timeout", "audit": "", "recoverable": "yes"},
    "cancel": {"label": "中断取消", "edge": "on_cancel", "audit": "", "recoverable": "yes"},
    "reject": {"label": "业务拒绝", "edge": "no|default", "audit": "", "recoverable": "yes"},
}


@dataclass(frozen=True)
class FaultSpec:
    """一条故障描述。字段按 `kind` 取用。"""

    kind: FaultKind
    entity: str | None = None
    wrong_state: str | None = None
    error: str = ""
    seed: int = 0

    def __post_init__(self) -> None:
        if self.kind in (FaultKind.UNAVAILABLE, FaultKind.DRIFT) and not self.entity:
            raise FaultInjectionError(f"{self.kind.value} 故障必须指定 entity")
        if self.kind is FaultKind.DRIFT and self.wrong_state is None:
            raise FaultInjectionError("drift 故障必须指定 wrong_state")


# ─────────────────────────────────────────────────────────────────────
# 构造器
# ─────────────────────────────────────────────────────────────────────


def unavailable(entity: str) -> FaultSpec:
    """实体掉线（状态置 `unavailable`）。"""
    return FaultSpec(FaultKind.UNAVAILABLE, entity=entity)


def timeout(error: str = "传输层超时（故障注入）") -> FaultSpec:
    """适配器传输层超时。"""
    return FaultSpec(FaultKind.TIMEOUT, error=error)


def drop(error: str = "消息丢包（故障注入）") -> FaultSpec:
    """消息丢包（指令未达设备）。"""
    return FaultSpec(FaultKind.DROP, error=error)


def drift(entity: str, wrong_state: str) -> FaultSpec:
    """动作后实体状态漂移到非预期值（canary 应检测到并回滚）。"""
    return FaultSpec(FaultKind.DRIFT, entity=entity, wrong_state=wrong_state)


def reorder(seed: int = 0) -> FaultSpec:
    """事件乱序（按给定种子确定性重排后重放）。"""
    return FaultSpec(FaultKind.REORDER, seed=seed)


# ─────────────────────────────────────────────────────────────────────
# 注入原语
# ─────────────────────────────────────────────────────────────────────


def set_entity_state(
    states: Any,
    entity_id: str,
    state: str,
    attributes: Mapping[str, Any] | None = None,
) -> None:
    """把实体状态写进任意状态底座。

    兼容 `FakeHA.set` / `InMemoryStateProvider.set_state`；两者签名不同，
    此处做统一派发，避免测试里到处 if-else。
    """
    if hasattr(states, "set"):
        states.set(entity_id, state, attributes)
        return
    if hasattr(states, "set_state"):
        states.set_state(entity_id, state, attributes)
        return
    raise FaultInjectionError(f"状态底座 {type(states).__name__} 不支持写状态，无法注入故障")


def inject_unavailable(states: Any, entity_id: str) -> None:
    """把实体置为 `unavailable`（传感器掉线）。"""
    set_entity_state(states, entity_id, "unavailable")


def inject_drift(states: Any, entity_id: str, wrong_state: str) -> None:
    """把实体置为错误状态，制造"动作后未达预期"以便 canary 检测漂移。"""
    set_entity_state(states, entity_id, wrong_state)


def inject_adapter_fault(adapter: Any, spec: FaultSpec) -> bool:
    """把适配器层故障（timeout / drop / fail）排入适配器队列。

    返回是否注入成功；注入到不支持故障队列的适配器时抛 `FaultInjectionError`。
    """
    method = {
        FaultKind.TIMEOUT: "timeout_next",
        FaultKind.DROP: "drop_next",
        FaultKind.UNAVAILABLE: "unavailable_next",
    }.get(spec.kind)
    if method is None:
        return False
    fn = getattr(adapter, method, None)
    if fn is None:
        raise FaultInjectionError(
            f"适配器 {type(adapter).__name__} 不支持 {spec.kind.value} 注入（缺 {method}）"
        )
    fn(spec.error or f"{spec.kind.value}（故障注入）")
    return True


def reorder_events(events: Sequence[Any], seed: int = 0) -> list[Any]:
    """按种子确定性乱序重排一批事件（事件乱序故障，IR §9.5）。

    只重排、不增删——用于验证 Runtime 在乱序到达下仍收敛（去重/熔断兜底）。
    """
    items = list(events)
    random.Random(seed).shuffle(items)
    return items


# ─────────────────────────────────────────────────────────────────────
# 故障计划（测试装配用）
# ─────────────────────────────────────────────────────────────────────


@dataclass
class FaultPlan:
    """一组故障：可预排，再在恰当时机应用到状态源 / 适配器 / 事件序列。"""

    specs: list[FaultSpec] = field(default_factory=list)

    def add(self, spec: FaultSpec) -> "FaultPlan":
        self.specs.append(spec)
        return self

    def of_kind(self, kind: FaultKind) -> list[FaultSpec]:
        return [s for s in self.specs if s.kind is kind]

    def apply_to_states(self, states: Any) -> list[FaultSpec]:
        """注入状态源类故障（unavailable / drift）。"""
        applied: list[FaultSpec] = []
        for spec in self.specs:
            if spec.kind is FaultKind.UNAVAILABLE:
                inject_unavailable(states, spec.entity or "")
                applied.append(spec)
            elif spec.kind is FaultKind.DRIFT:
                inject_drift(states, spec.entity or "", spec.wrong_state or "")
                applied.append(spec)
        return applied

    def apply_to_adapter(self, adapter: Any) -> list[FaultSpec]:
        """注入适配器类故障（**仅** timeout / drop）。

        `UNAVAILABLE` / `DRIFT` 属状态层（见 `apply_to_states`），不在此重复注入，
        避免同一 spec 被两层各注入一次。
        """
        adapter_kinds = (FaultKind.TIMEOUT, FaultKind.DROP)
        applied: list[FaultSpec] = []
        for spec in self.specs:
            if spec.kind in adapter_kinds and inject_adapter_fault(adapter, spec):
                applied.append(spec)
        return applied

    def reorder(self, events: Iterable[Any]) -> list[Any]:
        """对事件序列应用乱序（取第一条 reorder 故障的种子，缺省 0）。"""
        specs = self.of_kind(FaultKind.REORDER)
        seed = specs[0].seed if specs else 0
        return reorder_events(list(events), seed)
