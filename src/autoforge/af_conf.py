"""MA 置信度分级自主（G4，IR §10 / KICKOFF §2-11）。

三级自主（阈值）：
- conf >= 0.85 (AUTO_MIN)  → **自动部署**（build → canary → 自动转正）
- 0.60 <= conf < 0.85       → **Shadow**（只读比对，禁止写设备；G2 已编译期拦截）
- conf < 0.60               → **只出 ask 提案**，必须人工确认（G2 已拦截写设备）

置信度随时间**指数衰减**；用户手动干预（取消/修改）作为**负样本**回灌，拉低 conf；
自动执行后被确认无误（正样本）缓慢提升 conf。

> 与编译期约束的关系：G2 的 `SHADOW_WRITES_DEVICE` / `LOW_CONF_WRITES_DEVICE` 是**静态闸门**，
> 本模块是**运行期决策**——二者口径必须一致（共享同一组阈值）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from .af_ir import Graph

#: 三级自主阈值（与 af_scanner 共用，改一处必须同步）
AUTO_MIN = 0.85
SHADOW_LOW = 0.60

#: 衰减与样本回灌系数（原型期数值，后续按真实数据校准）
DECAY_PER_DAY = 0.02      # 每天自然衰减
EVENT_PENALTY = 0.005     # 每个未被采纳的事件额外衰减
POSITIVE_LIFT = 0.03      # 正样本（执行后确认无误）提升
NEGATIVE_DROP = 0.25      # 负样本（用户干预/手动修改）下降

Band = Literal["auto", "shadow", "ask"]


def decision_for(conf: float) -> Band:
    """把置信度映射成自主级别。"""
    if conf >= AUTO_MIN:
        return "auto"
    if conf >= SHADOW_LOW:
        return "shadow"
    return "ask"


def decay(conf: float, hours: float, events: int = 0) -> float:
    """指数衰减 + 事件微调，结果钳制到 [0, 1]。

    `hours`：距上次校准经过的小时数；`events`：期间发生的未采纳事件数。
    """
    h = max(0.0, float(hours))
    factor = (1.0 - DECAY_PER_DAY) ** (h / 24.0)
    new = conf * factor - EVENT_PENALTY * max(0, events)
    return min(1.0, max(0.0, new))


@dataclass
class ConfidenceStore:
    """每个 automation 一份置信度，带衰减与样本回灌。

    原型期**全内存**（持久化属 G6）。决策口径与 G2 编译期闸门完全一致。
    """

    values: dict[str, float] = field(default_factory=dict)
    samples: dict[str, list[tuple[str, float]]] = field(default_factory=dict)

    def seed(self, graph: "Graph") -> "ConfidenceStore":
        """用 Graph 里声明的 confidence 初始化（缺省视为 1.0，即默认全自治）。"""
        for auto in graph:
            if auto.id not in self.values:
                self.values[auto.id] = auto.confidence if auto.confidence is not None else 1.0
        return self

    def get(self, automation_id: str) -> float:
        return self.values.get(automation_id, 1.0)

    def band(self, automation_id: str) -> Band:
        return decision_for(self.get(automation_id))

    def decay_all(self, hours: float, events: int = 0) -> None:
        for key in list(self.values):
            self.values[key] = decay(self.values[key], hours, events)

    def record_positive(self, automation_id: str) -> None:
        """正样本：自动执行后被确认无误 → 缓慢提升。"""
        self.values[automation_id] = min(1.0, self.get(automation_id) + POSITIVE_LIFT)
        self.samples.setdefault(automation_id, []).append(("positive", self.get(automation_id)))

    def record_negative(self, automation_id: str) -> None:
        """负样本：用户干预 / 手动修改 → 显著拉低。"""
        self.values[automation_id] = max(0.0, self.get(automation_id) - NEGATIVE_DROP)
        self.samples.setdefault(automation_id, []).append(("negative", self.get(automation_id)))

    def promote(self, automation_id: str) -> None:
        """人工确认无误 → 直接拉满（可用于"手动转正"）。"""
        self.values[automation_id] = 1.0
        self.samples.setdefault(automation_id, []).append(("promote", 1.0))
