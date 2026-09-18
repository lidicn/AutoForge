"""af_vhass —— 仿真装配与降级实现。

首选：基于 `pytest-homeassistant` 的真 vhass（`harness.py` / `bridge.py`），
语义 100% 对齐 HA（实体模型、事件总线、太阳历、日历全是原生实现）。

降级：当本机装不上 HA 依赖时，启用 `fake.py` 的 FakeHA——
`StateProvider` / `TimeSource` / `Adapter` 三接口保持不变，**Runtime 侧零改动**，
仅太阳历等 HA 原生能力改为本地桩。

切换开关：环境变量 `AUTOFORGE_VHASS=ha|fake`（默认 `ha`）。
"""

from .fake import (
    FakeHA,
    FakeHAAdapter,
    default_state_for,
    is_modeled,
    seed_from_graph,
    service_effect,
)

__all__ = [
    "FakeHA",
    "FakeHAAdapter",
    "default_state_for",
    "is_modeled",
    "seed_from_graph",
    "service_effect",
]
