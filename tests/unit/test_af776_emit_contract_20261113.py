# -*- coding: utf-8 -*-
"""emit 契约钉 · 「状态先就位，事件后到达」（AF-776 批第④件，**测试级，⛔ 不改生产代码**）。

**钉的是什么**：`Runtime.emit()` 的 P1-12 契约——**先** `states.set_state()`，**再** `publish()`。
事件到达订阅方时，订阅方在 handler 内取到的快照必须已经是新值；否则订阅方的 `if` 会拿旧状态
判一次（"灯刚开"被判成"灯还关着"），动作静默不发，而现场看是"事件收了、条件不满足"。

**为什么现在才钉**：既有覆盖只测到「emit 发布了事件／载荷对不对／不进实体状态通道」
（`test_af_emit.py` 六条），**没有一条**钉住 `set_state` 与 `publish` 的先后。把 `emit()`
反写成"先 publish 再 set_state"，现有测试全绿——这正是"缺陷没人拦"的形状。

**为什么订阅方用 `state` 触发而不是 `event` 触发**（⛔ 不是笔误）：
`on(event …)` 触发的自动化，其 `reads()` 会含 `event.<name>` 这个**伪实体**，而
`InstanceManager._refresh_snapshot` 的 `hasattr(states,"has")` 守卫在四个状态源上都是 False
（`has()` 只在 `Snapshot` 上有）⇒ spawn 时整份快照塌成空 ⇒ 该实例的每个 `if` 都抛
`UnknownEntity`。那是**另一条缺陷**（已单独报 AF-PM，不属本卡射程），
本钉要测的是 emit 的先后，⛔ 不许被它带偏：所以订阅方走状态边沿，`reads()` 只含真实体。

**判据**：

- E1 顺序正例：`emit(实体, "on")` ⇒ 订阅方 handler 内 `if(is_on(该实体))` 成立、`do` 真下发
- E2 反证腿：绕过 `emit()` 直接 `publish()`（不设状态）⇒ 同一订阅方 `if` 不成立、`do` 不下发
  （证明 E1 的绿**确实来自**状态先就位，不是"任何事件都放行"）
"""

from __future__ import annotations

from typing import Any

from autoforge.af_bus import BusEvent
from autoforge.af_ir import Graph, load_automation
from autoforge.af_runtime import build_runtime

OCC = "binary_sensor.motion_study"
LAMP = "switch.desk_lamp"

#: 订阅方：状态边沿 ⇒ **用状态快照**判条件 ⇒ 成立才下发动作
#: （handler 内取到的那份快照，正是 P1-12 要保证 "已经看见新值" 的那一刻）
AUTO_SUB: dict[str, Any] = {
    "ir_version": "0.2.1", "id": "af776_emit_sub", "name": "AF776 emit 订阅方",
    "version": 1, "mode": "single",
    "nodes": [
        {"id": "t1", "kind": "on", "name": "人进来（状态边沿）",
         "trigger": {"type": "state", "entity_id": OCC, "to": "on"}},
        {"id": "i1", "kind": "if", "name": "读快照：人确实在",
         "expr": {"op": "is_on", "value": {"var": f"entity.{OCC}"}}},
        {"id": "d1", "kind": "do", "name": "开台灯", "adapter": "ha",
         "action": "switch.turn_on", "params": {"entity_id": LAMP}},
        {"id": "p1", "kind": "pass", "name": "结束"},
    ],
    "edges": [
        {"from": "t1", "to": "i1", "kind": "then"},
        {"from": "i1", "to": "d1", "kind": "then"},
        {"from": "i1", "to": "p1", "kind": "no"},
        {"from": "d1", "to": "p1", "kind": "then"},
    ],
}


class _RecorderAdapter:
    """记下每一次下发的动作（E1/E2 共用同一个观察点）。"""

    name = "ha"
    dry_run = False

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def call(self, action: str, params: dict):
        self.calls.append((action, dict(params)))
        from autoforge.af_adapters.base import CallResult

        return CallResult(success=True, data={}, error=None)


def _runtime():
    rt = build_runtime(Graph([load_automation(AUTO_SUB)]))
    rec = _RecorderAdapter()
    rt.adapters.register(rec)
    rt.states.set_state(OCC, "off")
    return rt, rec


# ── E1 顺序正例：emit 之后订阅方 handler 见到新状态 ───────────────────
def test_e1_subscriber_sees_new_state_when_emit_publishes():
    """判据：`emit()` 里 `set_state` 在 `publish` 之前 ⇒ 订阅方 `if` 读到**新值**、动作真下发。

    若把 `emit()` 反成"先 publish 再 set_state"，订阅方会在事件到达那一刻用旧快照
    （`OCC = off`）判 ⇒ `if` 走 no 边 ⇒ `do` 一次都不下发 ⇒ 本腿红。
    """
    rt, rec = _runtime()

    rt.emit(OCC, "on")

    assert rec.calls == [("switch.turn_on", {"entity_id": LAMP})], (
        "订阅方在 handler 内必须见到新状态（on）才会下发；实际下发 %r "
        "——⛔ 说明事件先于状态到达（P1-12 契约被破）" % (rec.calls,)
    )
    # 合同行：emit 之后状态源里确实是新值
    assert rt.states.snapshot([OCC]).get(OCC) == "on"


# ── E2 反证腿：绕过 emit 直接 publish ⇒ 条件不成立、动作不下发 ─────────
def test_e2_publish_without_state_update_does_not_pass_the_if():
    """反证腿：`publish()` 不设状态 ⇒ 订阅方快照仍是旧值 `off` ⇒ `if` 不成立、`do` 不下发。

    ⛔ 没有这条，E1 可以靠"任何事件都放行"蒙成绿——它证明 E1 的绿**确实来自**状态先就位。
    """
    rt, rec = _runtime()

    rt.publish(BusEvent.of(OCC, "on"))  # 事件照样到达（触发匹配用的是事件，不是快照）

    assert rec.calls == [], (
        "状态仍是 off ⇒ if 必须不成立、⛔ 不下发动作；实际 %r" % (rec.calls,)
    )
    assert rt.states.snapshot([OCC]).get(OCC) == "off", "反证前提：状态没被更新"
    assert not [e for e in rt.audit if str(e.type) == "entity_drift"], (
        "反证腿⛔ 不许退化成 entity_drift 软失效——那会让本钉测不到" "边界"
    )
