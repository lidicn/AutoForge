"""状态读取抽象 —— 求值段快照（IR §7）。

设计要点：
- **快照 = 求值段**：段内 `entity.*` 全来自同一份快照（不撕裂）；恢复时**新建**快照并丢弃旧的。
- **快照只读**：`do` 的执行结果只能写 `vars.*`，**不回写快照**（IR §7.2）。
  这样"开空调后立刻判断温度是否变化"这类反直觉时序被结构性禁止。
- 生产（`hass.states`）与仿真（vhass）只是 `StateProvider` 的两个实现，
  NodeExecutor 拿快照的代码在两种环境下完全一致。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Protocol, runtime_checkable

__all__ = [
    "UnknownEntity",
    "Snapshot",
    "StateProvider",
    "InMemoryStateProvider",
    "make_resolver",
    "split_namespace",
]


class UnknownEntity(KeyError):
    """实体不存在（漂移）。运行时按 IR §14-9 走 `on_error` 软失效，不直接 failed。"""


@dataclass(frozen=True)
class Snapshot:
    """求值段的只读状态副本。段内原子，跨段新鲜。"""

    values: Mapping[str, str]
    attributes: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    taken_at: float = 0.0

    @classmethod
    def of(cls, states: Mapping[str, str], **kw: Any) -> "Snapshot":
        return cls(values=MappingProxyType(dict(states)), **kw)

    def get(self, entity_id: str) -> str:
        try:
            return self.values[entity_id]
        except KeyError as exc:
            raise UnknownEntity(entity_id) from exc

    def attrs(self, entity_id: str) -> Mapping[str, Any]:
        return self.attributes.get(entity_id, {})

    def has(self, entity_id: str) -> bool:
        return entity_id in self.values

    def __contains__(self, entity_id: object) -> bool:
        return entity_id in self.values

    def __len__(self) -> int:
        return len(self.values)

    def to_dict(self) -> dict[str, Any]:
        """序列化形态（实例上下文用，必须可 JSON 化）。"""
        return {
            "values": dict(self.values),
            "taken_at": self.taken_at,
        }


@runtime_checkable
class StateProvider(Protocol):
    """状态源。生产=HA，仿真=vhass/FakeHA。"""

    def snapshot(self, entity_ids: Iterable[str]) -> Snapshot:
        """一次性读取给定实体的状态，返回只读快照。"""
        ...


@dataclass
class InMemoryStateProvider:
    """内存状态源（单测 / FakeHA 用）。

    提供 `set_state` 便于构造场景；生产实现只读，没有这个方法。
    """

    states: dict[str, str] = field(default_factory=dict)
    attributes: dict[str, dict[str, Any]] = field(default_factory=dict)
    clock_reads: int = 0

    def set_state(self, entity_id: str, state: str, attributes: Mapping[str, Any] | None = None) -> None:
        self.states[entity_id] = str(state)
        if attributes is not None:
            self.attributes[entity_id] = dict(attributes)

    def snapshot(self, entity_ids: Iterable[str]) -> Snapshot:
        self.clock_reads += 1
        wanted = list(entity_ids)
        return Snapshot(
            values=MappingProxyType({e: self.states[e] for e in wanted if e in self.states}),
            attributes=MappingProxyType({e: dict(self.attributes.get(e, {})) for e in wanted}),
        )


# ─────────────────────────────────────────────────────────────────────
# 命名空间解析（IR §3.1）
# ─────────────────────────────────────────────────────────────────────

ENTITY_NS = "entity"
VARS_NS = "vars"
CONTEXT_NS = "context"
_NAMESPACES = (ENTITY_NS, VARS_NS, CONTEXT_NS)


def split_namespace(name: str) -> tuple[str, str]:
    """`entity.sensor.x` → ("entity", "sensor.x")。非法命名空间直接报错（冲突编译报错）。"""
    if "." not in name:
        raise ValueError(f"变量引用必须带命名空间前缀（{ '/'.join(_NAMESPACES) }）：{name!r}")
    ns, _, rest = name.partition(".")
    if ns not in _NAMESPACES:
        raise ValueError(f"未知命名空间 {ns!r}，只允许 {_NAMESPACES}")
    return ns, rest


def make_resolver(
    snapshot: Snapshot,
    vars: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
):
    """构造 `Resolver(name, declared_type) -> value`，供 `af_ir.expr.evaluate` 使用。

    - `entity.*`：只读快照（段内冻结，看不到本段 `do` 造成的外部变化）
    - `vars.*`：实例私有可写
    - `context.*`：系统内置（instance_id / trigger_time 等）
    """
    ctx = dict(context or {})

    def resolve(name: str, declared: str | None = None) -> Any:
        ns, key = split_namespace(name)
        if ns == ENTITY_NS:
            return snapshot.get(key)
        if ns == VARS_NS:
            if key not in vars:
                raise KeyError(f"未初始化的实例变量：vars.{key}")
            return vars[key]
        if key not in ctx:
            raise KeyError(f"未知系统变量：context.{key}")
        return ctx[key]

    return resolve
