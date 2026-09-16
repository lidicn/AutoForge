"""灰度（canary）执行保护（G4，IR §10）。

canary 配置示例（IR `do` 节点）：
```json
"canary": { "duration": "15m", "auto_rollback": true }
```

语义：动作下发后，在 `duration` 内监控目标实体；若实体状态**没有**变成预期（漂移），
自动回滚到动作前的状态（通过下发"反向动作"实现）。

设计要点：
- 与具体适配器无关——只依赖 `Adapter` 协议（`call`）和 `StateProvider`（`snapshot`）。
- 动作→新状态 与 反向动作 都复用 `af_vhass.fake.SERVICE_STATE`，保证与仿真口径一致。
- `CanaryGuard` 是同步、可立即测的单元；"duration 后监控"的异步调度由 Runtime 负责（G4 此处只提供 `perform` + `rollback` 能力）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .af_adapters import Adapter, CallResult
from .af_state import StateProvider
from .af_vhass.fake import SERVICE_STATE

__all__ = ["CanaryGuard", "CanaryResult", "inverse_action"]


def _inverse_map() -> dict[str, str]:
    """从 SERVICE_STATE 推导反向动作表：同域、目标状态不同的服务互为反向。"""
    inv: dict[str, str] = {}
    by_domain: dict[str, list[tuple[str, str]]] = {}
    for (domain, service), state in SERVICE_STATE.items():
        by_domain.setdefault(domain, []).append((service, state))
    for domain, services in by_domain.items():
        for svc, st in services:
            for svc2, st2 in services:
                if svc != svc2 and st != st2:
                    inv[f"{domain}.{svc}"] = f"{domain}.{svc2}"
                    break
    return inv


INVERSE_ACTION = _inverse_map()


def inverse_action(action: str) -> str | None:
    """返回动作的反向动作（用于回滚）；无反向则 None。"""
    return INVERSE_ACTION.get(action)


@dataclass
class CanaryResult:
    """一次 canary 动作的结果，携带动作前快照，供漂移判定与回滚。"""

    action: str
    params: dict[str, Any]
    result: CallResult
    pre_states: dict[str, str | None]
    guard: "CanaryGuard"

    def expected_state(self) -> str | None:
        domain, _, service = self.action.partition(".")
        return SERVICE_STATE.get((domain, service))

    def _targets(self) -> list[str]:
        raw = self.params.get("entity_id")
        if isinstance(raw, str):
            return [raw]
        if isinstance(raw, (list, tuple)):
            return [str(x) for x in raw]
        return []

    def has_drift(self) -> bool:
        """动作成功后，目标实体状态是否没变成预期 → 漂移。"""
        exp = self.expected_state()
        if exp is None:
            return False
        for entity_id in self._targets():
            actual = self.guard.states.snapshot([entity_id]).values.get(entity_id)
            if actual != exp:
                return True
        return False

    def rollback(self, adapter: Adapter) -> list[CallResult]:
        """对每一个目标实体下发反向动作，恢复到动作前状态。"""
        inv = inverse_action(self.action)
        if inv is None:
            return []
        out: list[CallResult] = []
        for entity_id in self._targets():
            out.append(adapter.call(inv, {"entity_id": entity_id}))
        return out


@dataclass
class CanaryGuard:
    """canary 执行保护器：动作前快照 + 漂移检测 + 自动回滚。"""

    states: StateProvider
    auto_rollback: bool = True

    def perform(self, adapter: Adapter, action: str, params: Mapping[str, Any]) -> CanaryResult:
        """执行动作并在动作前抓取目标实体快照。"""
        targets = params.get("entity_id")
        if isinstance(targets, str):
            targets = [targets]
        pre = {
            e: self.states.snapshot([e]).values.get(e)
            for e in (targets or [])
        }
        result = adapter.call(action, dict(params))
        return CanaryResult(action=action, params=dict(params), result=result, pre_states=pre, guard=self)

    def check_and_rollback(self, adapter: Adapter, res: CanaryResult) -> list[CallResult]:
        """漂移 + 自动回滚：返回回滚产生的调用结果（无漂移则为空）。"""
        if not self.auto_rollback or not res.has_drift():
            return []
        return res.rollback(adapter)
