"""vhass 装配 —— 在 `pytest-homeassistant` 上跑 AutoForge。

用法（pytest-asyncio）：

```python
async def test_in_vhass(hass):
    harness = await VhassHarness.create(hass, graph, seed={...})
    await harness.emit("binary_sensor.motion", "on")
    assert harness.state_of("light.study_main") == "on"
    await harness.advance(600)          # 时间旅行 10 分钟
```

依赖：`pytest-homeassistant-custom-component`（会钉住 HA 版本）。
装不上时 `af_vhass` 会自动回落到 `fake.py` 的 FakeHA（接口一致）。
"""

from __future__ import annotations

from datetime import timezone
from typing import Any, Iterable, Mapping

from ..af_ir import Graph
from ..af_runtime import Runtime, build_runtime
from ..af_scheduler import Quota
from ..af_time import VirtualTimeSource, parse_duration
from .bridge import HassAdapter, HassStateProvider
from .fake import SERVICE_STATE

__all__ = [
    "VhassHarness",
    "register_device_services",
    "register_unavailable",
    "seed_states",
    "setup_sun",
]


def seed_states(hass: Any, states: Mapping[str, str]) -> None:
    """播种实体状态（等价于"现实世界现在是这个样子"）。"""
    for entity_id, state in states.items():
        hass.states.async_set(entity_id, str(state))


async def register_unavailable(hass: Any, entity_id: str, attributes: Mapping[str, Any] | None = None) -> None:
    """向 HA 注入 `unavailable` 状态（传感器掉线，G5 故障注入 IR §9.5）。"""
    hass.states.async_set(entity_id, "unavailable", dict(attributes) if attributes else None)
    await hass.async_block_till_done()


def _domain_services(actions: Iterable[str]) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for action in actions:
        domain, _, service = action.partition(".")
        if domain and service:
            pairs.add((domain, service))
    return pairs


async def register_device_services(
    hass: Any,
    actions: Iterable[str],
    *,
    unmodeled_out: list[str] | None = None,
) -> list[str]:
    """把图里用到的设备服务注册成"会翻状态"的桩服务。

    裸 vhass 里没有 `light` / `switch` 等集成，`light.turn_off` 这种服务根本不存在
    （`ServiceNotFound`）。设备动作是**假的**（桩服务按 `SERVICE_STATE` 翻状态），
    但事件总线、服务注册、状态机、定时器、太阳历全都是**真的 HA**——这正是 vhass 的意义。

    **v1.2.0 诚实性**：没有建模的服务（`SERVICE_STATE` 里查不到）**不静默跳过**，
    而是登记到 `unmodeled_out`——让上层如实降级为「该动作的结果未验证」，
    而不是让 `expect` 断言在「根本没执行」的情况下误判 PASS/FAIL。
    """
    registered: list[str] = []
    for domain, service in sorted(_domain_services(actions)):
        new_state = SERVICE_STATE.get((domain, service))
        if new_state is None:
            if unmodeled_out is not None:
                unmodeled_out.append(f"{domain}.{service}")
            continue

        async def _handler(call: Any, _state: str = new_state) -> None:
            targets = call.data.get("entity_id") or []
            if isinstance(targets, str):
                targets = [targets]
            for entity_id in targets:
                current = hass.states.get(entity_id)
                attrs = dict(current.attributes) if current is not None else {}
                hass.states.async_set(entity_id, _state, attrs)

        hass.services.async_register(domain, service, _handler)
        registered.append(f"{domain}.{service}")
    return registered


async def setup_sun(hass: Any, latitude: float = 31.23, longitude: float = 121.47) -> None:
    """启用 HA 原生 `sun` 组件——太阳历是 vhass 相比自研仿真器的最大收益。"""
    hass.config.latitude = latitude
    hass.config.longitude = longitude
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "sun", {"sun": {}})
    await hass.async_block_till_done()


class VhassHarness:
    """vhass 驾驶台：把同步的 Runtime 和异步的 HA 粘在一起。"""

    def __init__(
        self,
        hass: Any,
        runtime: Runtime,
        adapter: HassAdapter,
        freezer: Any = None,
        unmodeled: list[str] | None = None,
    ):
        self.hass = hass
        self.runtime = runtime
        self.adapter = adapter
        self.freezer = freezer
        #: v1.2.0：vhass 未建模（桩服务不存在）的动作 → 其后果**无法验证**，如实暴露
        self.unmodeled: list[str] = list(unmodeled or [])

    @property
    def unverified(self) -> list[str]:
        """无法验证的动作清单（非空时调用方应把相关断言视为「未验证」而非「通过」）。"""
        return sorted(set(self.unmodeled))

    # ── 构造 ──────────────────────────────────────────────────────────
    @classmethod
    async def create(
        cls,
        hass: Any,
        graph: Graph,
        seed: Mapping[str, str] | None = None,
        *,
        quota: Quota | None = None,
        start: Any | None = None,
        freezer: Any = None,
    ) -> "VhassHarness":
        # ⚠️ 关键：虚拟时钟**必须从 HA 的当前时间起算**，不能用自己的固定起点。
        # 否则 `async_fire_time_changed(hass, 我算出来的时间)` 相对 HA 的 now 可能是"过去"，
        # 时间旅行不生效（表现：太阳历不重算、`for`/`wait` 不到点）。
        from homeassistant.util import dt as dt_util

        clock = VirtualTimeSource(start=start or dt_util.utcnow())
        states = HassStateProvider(hass)
        runtime = build_runtime(graph, states=states, clock=clock, quota=quota)
        adapter = HassAdapter(hass)
        runtime.adapters.register(adapter)

        # 注册图里用到的设备服务（裸 vhass 不自带集成，否则 ServiceNotFound）
        actions = {
            node.action
            for auto in graph
            for node in auto.nodes.values()
            if node.kind == "do" and node.action
        }
        unmodeled: list[str] = []
        await register_device_services(hass, actions, unmodeled_out=unmodeled)
        await hass.async_block_till_done()

        if seed:
            seed_states(hass, seed)
            await hass.async_block_till_done()
        return cls(hass, runtime, adapter, freezer, unmodeled=unmodeled)

    # ── 操作 ──────────────────────────────────────────────────────────
    async def emit(self, entity_id: str, state: str, **payload: Any) -> list:
        """改变实体状态并驱动 Runtime（先改 HA 状态，保证快照读得到）。"""
        self.hass.states.async_set(entity_id, str(state), payload or None)
        await self.hass.async_block_till_done()
        fired = self.runtime.emit(entity_id, str(state), **payload)
        await self.flush()
        return fired

    # ── G5 故障注入 ───────────────────────────────────────────────────
    async def inject_unavailable(self, entity_id: str, attributes: Mapping[str, Any] | None = None) -> None:
        """**状态层**故障：把实体置为 `unavailable`（传感器掉线）。"""
        await register_unavailable(self.hass, entity_id, attributes)

    def fail_next(self, error: str = "vhass 注入的失败") -> None:
        """**适配器层**故障：下一次 `do` 下发按"设备报错"失败（不真正下发）。"""
        self.adapter.fail_next(error)

    def timeout_next(self, error: str = "vhass 注入的传输层超时") -> None:
        """**适配器层**故障：下一次 `do` 下发按"传输层超时"失败（不真正下发）。"""
        self.adapter.timeout_next(error)

    def drop_next(self, error: str = "vhass 注入的消息丢包") -> None:
        """**适配器层**故障：下一次 `do` 下发按"消息丢包"失败（指令未达设备）。"""
        self.adapter.drop_next(error)

    def unavailable_next(self, error: str = "vhass 注入的实体不可用") -> None:
        """**适配器层**故障：下一次 `do` 下发按"实体不可用"失败（不真正下发）。"""
        self.adapter.unavailable_next(error)

    async def replay(self, events: Iterable[Mapping[str, Any]], seed: int | None = None) -> list:
        """重放事件序列；`seed` 非 None 时按种子确定性乱序（事件乱序故障）。

        每个事件为 `{"entity_id": ..., "state": ...}`，其余键作为 payload 透传。
        """
        items = list(events)
        if seed is not None:
            from ..af_fault import reorder_events

            items = reorder_events(items, seed)
        fired: list = []
        for item in items:
            extra = {k: v for k, v in item.items() if k not in {"entity_id", "state"}}
            fired.extend(await self.emit(str(item["entity_id"]), str(item["state"]), **extra))
        return fired

    async def advance(self, seconds: float | str) -> list:
        """时间旅行：同时推进 HA 的冻结时钟与 AutoForge 虚拟时钟。

        ⚠️ 两个坑（2026-09-14 在 NAS 上实测）：
        1. vhass 里 `dt_util.utcnow()` 是**冻结**的，`async_fire_time_changed(hass, dt)`
           只触发已排定的定时器句柄，**不会推进时钟**——必须用 `freezer.tick()`。
        2. AutoForge 自己的虚拟时钟也要同步 `advance`，否则 `for` / `wait` 不到点。
        两者缺一都会出现"时间走了但逻辑没动"。
        """
        if self.freezer is None:
            raise RuntimeError(
                "advance() 需要 freezer 夹具：`await VhassHarness.create(hass, graph, freezer=freezer)`"
            )
        from datetime import timedelta

        from pytest_homeassistant_custom_component.common import async_fire_time_changed

        seconds = parse_duration(seconds) if isinstance(seconds, str) else float(seconds)
        self.freezer.tick(timedelta(seconds=seconds))
        self.runtime.clock.advance(seconds)  # type: ignore[attr-defined]
        async_fire_time_changed(self.hass)
        await self.hass.async_block_till_done()
        fired = self.runtime.tick()
        await self.flush()
        return fired

    async def flush(self) -> None:
        """把挂起的适配器调用真正下发到 HA。"""
        if await self.adapter.flush():
            await self.hass.async_block_till_done()

    # ── 断言辅助 ──────────────────────────────────────────────────────
    def state_of(self, entity_id: str) -> str | None:
        state = self.hass.states.get(entity_id)
        return state.state if state is not None else None

    @property
    def actions(self) -> list[str]:
        return [a for a, _ in self.adapter.calls]

    @property
    def instances(self):
        return self.runtime.instances.all()
