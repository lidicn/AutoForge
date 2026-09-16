"""Mock 适配器 —— 单测与仿真用，记录调用并可注入失败。"""

from __future__ import annotations

from typing import Any, Mapping

from .base import CallResult, FaultQueue

__all__ = ["MockAdapter"]


class MockAdapter:
    """把调用记下来，返回预设结果；可按需注入失败用于验证 `on_error`。

    G5 起支持四类适配器层故障注入（`fail_next` / `timeout_next` / `drop_next` /
    `unavailable_next`），语义：
    - fail        ：设备报错（业务/设备异常）
    - timeout     ：传输层超时（IR §9.5「网络超时」）
    - drop        ：消息丢包（指令未达设备）
    - unavailable ：目标实体不可用（传感器/设备掉线）
    四者都走同一"单次失败"路径，由 IR 的 `on_error` 兜底，**不重试不降级**。
    """

    name = "mock"

    def __init__(
        self,
        results: Mapping[str, bool] | None = None,
        default_success: bool = True,
    ):
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.results: dict[str, bool] = dict(results or {})
        self.default_success = default_success
        self.faults = FaultQueue()

    # ── 断言辅助 ──────────────────────────────────────────────────────
    @property
    def actions(self) -> list[str]:
        return [a for a, _ in self.calls]

    def called_with(self, action: str) -> list[dict[str, Any]]:
        return [p for a, p in self.calls if a == action]

    def set_result(self, action: str, success: bool) -> None:
        self.results[action] = success

    # ── 故障注入（G5，IR §9.5）────────────────────────────────────────
    def fail_next(self, error: str = "mock 注入的失败") -> None:
        """让下一次调用按"设备报错"失败。"""
        self.faults.push("fail", error)

    def timeout_next(self, error: str = "mock 注入的传输层超时") -> None:
        """让下一次调用按"传输层超时"失败。"""
        self.faults.push("timeout", error)

    def drop_next(self, error: str = "mock 注入的消息丢包") -> None:
        """让下一次调用按"消息丢包"失败（指令未达设备）。"""
        self.faults.push("drop", error)

    def unavailable_next(self, error: str = "mock 注入的实体不可用") -> None:
        """让下一次调用按"实体不可用"失败。"""
        self.faults.push("unavailable", error)

    # ── Adapter 实现 ─────────────────────────────────────────────────
    def call(self, action: str, params: Mapping[str, Any]) -> CallResult:
        self.calls.append((action, dict(params)))
        fault = self.faults.pop()
        if fault is not None:
            kind, error = fault
            return CallResult.fail(error, fault=kind, action=action, params=dict(params))
        success = self.results.get(action, self.default_success)
        if success:
            return CallResult.ok({"action": action, "params": dict(params)})
        return CallResult.fail(f"mock 预设失败：{action}", action=action, params=dict(params))
