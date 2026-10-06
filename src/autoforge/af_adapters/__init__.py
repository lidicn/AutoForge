"""适配器层 —— 纯执行层（单次下发，无重试/降级/语义层超时）。"""

from .base import (
    L0_READONLY,
    L1_IDEMPOTENT,
    L2_RISKY,
    L3_DANGEROUS,
    POLICY_PARAMS,
    Adapter,
    AdapterError,
    AdapterRegistry,
    CallResult,
    FaultQueue,
    classify_action,
    is_destructive,
)
from .ha import DEFAULT_HA_URL, HAAdapter, HAStateProvider, HATransport
from .http import HTTPAdapter, guarded_open, host_of
from .mock import MockAdapter

__all__ = [
    "Adapter",
    "AdapterError",
    "AdapterRegistry",
    "CallResult",
    "FaultQueue",
    "POLICY_PARAMS",
    "L0_READONLY",
    "L1_IDEMPOTENT",
    "L2_RISKY",
    "L3_DANGEROUS",
    "classify_action",
    "is_destructive",
    "HAAdapter",
    "HAStateProvider",
    "HATransport",
    "DEFAULT_HA_URL",
    "HTTPAdapter",
    "guarded_open",
    "host_of",
    "MockAdapter",
]
