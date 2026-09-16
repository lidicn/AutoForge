"""vhass 桥接层 —— 把 HA（pytest-homeassistant 的 `hass` 实例）接到 AutoForge 的三个接口上。

    StateProvider  ← hass.states
    Adapter        ← hass.services.async_call
    TimeSource     ← VirtualTimeSource（与 async_fire_time_changed 同步推进）

**为什么适配器要"入队 +  flush"**：
AutoForge 的 Runtime 是同步的，HA 的服务调用是异步的，在事件循环里同步 `await` 会死锁。
所以 `HassAdapter.call()` 只把调用**入队并返回成功**，由 harness 在同步段结束后
`await flush()` 真正下发——这样既保留了"单次下发、无重试"的纯执行层语义，又不破坏事件循环。

G5 起与 `af_adapters/{mock,ha}.py` 对齐，接入 `FaultQueue` 支持**适配器层故障**
（`timeout` / `drop` / `fail` / `unavailable`）：命中故障时按"单次失败"返回
`CallResult.fail`，且**不进入 `pending`**（即不真下发），由 IR 的 `on_error` 兜底。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from ..af_adapters import CallResult
from ..af_adapters.base import FaultQueue
from ..af_state import Snapshot, StateProvider

__all__ = ["HassStateProvider", "HassAdapter"]


class HassStateProvider:
    """状态源：读 `hass.states`，产出只读快照。"""

    def __init__(self, hass: Any):
        self.hass = hass

    def snapshot(self, entity_ids: Iterable[str]) -> Snapshot:
        values: dict[str, str] = {}
        attributes: dict[str, dict[str, Any]] = {}
        for entity_id in entity_ids:
            state = self.hass.states.get(entity_id)
            if state is None:
                continue  # 实体不存在 → 运行时按漂移处理（UnknownEntity）
            values[entity_id] = state.state
            attributes[entity_id] = dict(state.attributes)
        return Snapshot(values=values, attributes=attributes)


@dataclass
class HassAdapter:
    """HA 适配器（vhass 版）：单次下发，入队后由 `flush()` 真正执行。"""

    hass: Any
    name: str = "ha"

    def __post_init__(self) -> None:
        self.pending: list[tuple[str, dict[str, Any]]] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.faults = FaultQueue()

    # ── 故障注入（G5，IR §9.5）：故障优先于入队，命中即不下发到 HA ──────
    def fail_next(self, error: str = "vhass 注入的失败") -> None:
        """让下一次调用按"设备报错"失败。"""
        self.faults.push("fail", error)

    def timeout_next(self, error: str = "vhass 注入的传输层超时") -> None:
        """让下一次调用按"传输层超时"失败。"""
        self.faults.push("timeout", error)

    def drop_next(self, error: str = "vhass 注入的消息丢包") -> None:
        """让下一次调用按"消息丢包"失败（指令未达设备）。"""
        self.faults.push("drop", error)

    def unavailable_next(self, error: str = "vhass 注入的实体不可用") -> None:
        """让下一次调用按"实体不可用"失败。"""
        self.faults.push("unavailable", error)

    def call(self, action: str, params: Mapping[str, Any]) -> CallResult:
        """纯执行层：不做重试/降级，只把这一次下发入队。

        G5 起支持适配器层故障注入（IR §9.5）：**故障优先于入队**——命中故障时
        按"单次失败"返回 `CallResult.fail` 且**不进入 `pending`**（即不真下发），
        由 IR 的 `on_error` 兜底；生产契约仍是"单次下发、无重试无降级"。
        """
        fault = self.faults.pop()
        if fault is not None:
            kind, error = fault
            return CallResult.fail(error, fault=kind, action=action, params=dict(params))
        domain, _, service = action.partition(".")
        if not domain or not service:
            return CallResult.fail(f"非法动作格式（应为 domain.service）：{action!r}")
        self.pending.append((action, dict(params)))
        return CallResult.ok({"queued": True, "action": action, "params": dict(params)})

    async def flush(self) -> list[tuple[str, dict[str, Any]]]:
        """把挂起的调用真正下发到 HA（在事件循环里 await）。"""
        executed: list[tuple[str, dict[str, Any]]] = []
        while self.pending:
            action, params = self.pending.pop(0)
            domain, _, service = action.partition(".")
            await self.hass.services.async_call(domain, service, params, blocking=True)
            self.calls.append((action, params))
            executed.append((action, params))
        return executed
