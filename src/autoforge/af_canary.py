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
from .af_undo import restore_call

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
    """返回动作的反向动作（用于回滚）；无反向则 None。

    P1-10 注：此函数基于仿真逆推，已不再用于 live 路径的 rollback。
    保留仅为向后兼容（外部可能引用）。
    """
    return INVERSE_ACTION.get(action)


# 注：动作前真实状态 → 恢复动作 的推导已统一收口到 `af_undo.restore_call`
# （DOMAIN_SETTER，属性感知 + fail-closed），本模块回滚直接复用，不再各自维护映射。


@dataclass
class CanaryResult:
    """一次 canary 动作的结果，携带动作前快照，供漂移判定与回滚。

    `pre_states`：仅状态（向后兼容）。
    `pre_snapshot`：状态 + 属性（F7 属性感知恢复用，如 light 的 brightness）。
    """

    action: str
    params: dict[str, Any]
    result: CallResult
    pre_states: dict[str, str | None]
    guard: "CanaryGuard"
    pre_snapshot: dict[str, dict[str, Any]] = field(default_factory=dict)

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
        """P1-10 + F7：依据动作前真实快照（pre_snapshot）恢复每个目标实体。

        不用仿真逆推（inverse_action）——只依据变更前抓取的真实状态 + 属性。
        复用 `af_undo.restore_call`（DOMAIN_SETTER）做属性感知恢复：
        light 还原 brightness/color_temp、cover 还原 position、climate 还原
        temperature/hvac_mode、fan 还原 percentage，switch/lock 还原 on/off。
        无法映射（域无 setter / 快照读不出方向）→ 跳过该实体（fail-closed，不猜）。
        方向回放了但属性读不出 → 照常下发，`RestoreCall.gaps` 记 warn（部分恢复不静默）。
        """
        import logging
        logger = logging.getLogger("autoforge.canary")
        out: list[CallResult] = []
        for entity_id in self._targets():
            snap = self.pre_snapshot.get(entity_id) or {
                "state": self.pre_states.get(entity_id),
                "attributes": {},
            }
            call = restore_call(entity_id, snap)
            if call is None:
                logger.warning(
                    "canary 回滚跳过 %s：无法映射恢复动作（fail-closed）", entity_id,
                )
                continue
            if call.gaps:
                logger.warning(
                    "canary 回滚 %s 部分恢复：%s 读不出、未回放", entity_id, "/".join(call.gaps)
                )
            out.append(adapter.call(call.action, call.params))
        return out


@dataclass
class CanaryGuard:
    """canary 执行保护器：动作前快照 + 漂移检测 + 自动回滚。"""

    states: StateProvider
    auto_rollback: bool = True

    def perform(self, adapter: Adapter, action: str, params: Mapping[str, Any]) -> CanaryResult:
        """执行动作并在动作前抓取目标实体快照（状态 + 属性）。"""
        targets = params.get("entity_id")
        if isinstance(targets, str):
            targets = [targets]
        snap = self.states.snapshot(targets or [])
        pre_states = {e: snap.values.get(e) for e in (targets or [])}
        pre_snapshot = {
            e: {"state": snap.values.get(e), "attributes": snap.attributes.get(e, {})}
            for e in (targets or [])
        }
        result = adapter.call(action, dict(params))
        return CanaryResult(
            action=action, params=dict(params), result=result,
            pre_states=pre_states, guard=self, pre_snapshot=pre_snapshot,
        )

    def check_and_rollback(self, adapter: Adapter, res: CanaryResult) -> list[CallResult]:
        """漂移 + 自动回滚：返回回滚产生的调用结果（无漂移则为空）。"""
        if not self.auto_rollback or not res.has_drift():
            return []
        return res.rollback(adapter)
