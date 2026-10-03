"""双轨对拍（PR 1.2）：同一组自动化在 FakeHA / HiFi 下仿真结果一致性断言。

决策 A 结论：FakeHA 与 HiFi 本就共享 `fake.py` 唯一效果真值表
（`SERVICE_STATE` / `DYNAMIC_SERVICES` / `service_effect` / `is_modeled`），
不存在漂移源——因此"统一真值源"不必作为架构动作，降级为一组**对拍测试**。

唯一已知分歧点：`sun` 触发器。FakeHA 用固定太阳历桩
（`fake.py:52` 日落 18:00 / 日出 06:00），HiFi 用真实太阳几何
（`high_fidelity.solar_events` / `sun_state_at`，默认深圳经纬度）。
对拍时把含 `sun` 触发器的自动化列入白名单，其直接写出的实体与判定
从严格一致性比较中剔除，仅保留真正的分歧。
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..af_ir import load_graph
from ..af_service import simulate_track

__all__ = [
    "sun_trigger_automation_ids",
    "compare_dual_track",
]


def sun_trigger_automation_ids(ir: Mapping[str, Any]) -> list[str]:
    """找出含 `sun` 触发器的自动化 id（双轨对拍的已知分歧点，白名单豁免）。

    `sun` 触发器在归一化后被展开为 `sun.sun` 状态触发（见 `af_scheduler.normalize_trigger`），
    这里直接按原始 `trigger.type == "sun"` 识别（含 group 内的叶子 sun 触发器）。
    """
    graph = load_graph(dict(ir))
    ids: list[str] = []
    for auto in graph:
        for on_node in auto.entry_nodes():
            trig = on_node.trigger
            if trig is None:
                continue
            if any(leaf.type == "sun" for leaf in trig.leaf_triggers()):
                ids.append(auto.id)
                break
    return ids


def _auto_verdict(report: Mapping[str, Any] | None) -> tuple[Any, ...]:
    """把一个自动化的 expect 判定压缩成可比较的元组（target, status 有序对）。"""
    if not report:
        return (("no_report",),)
    items = sorted(
        (str(it.get("target")), str(it.get("status")))
        for it in (report.get("items") or [])
    )
    return (report.get("ok"), report.get("failed"), tuple(items))


def compare_dual_track(
    ir: Mapping[str, Any],
    seed: Mapping[str, str] | None = None,
    events: Sequence[Mapping[str, Any]] | None = None,
    *,
    clock: Any | None = None,
) -> dict[str, Any]:
    """同一组自动化在 FakeHA / HiFi 下各跑一遍，比较仿真结果是否一致。

    返回结构::

        {
          "fake": <simulate_track("fake") 输出>,
          "hifi": <simulate_track("hifi") 输出>,
          "whitelisted": [sun 触发器 automation_id],
          "divergences": [ {kind, ...fake, ...hifi} ],   # 已剔除 sun 白名单
        }

    `divergences` 为空即两轨一致（决策 A 的核心断言）。`sun` 触发器分歧点从
    `divergences` 中剔除，仅保留真正的分歧，避免"已知分歧"污染对拍结论。
    """
    # 两轨对拍是纯仿真面：simulate_track 的 store 只用来回填报告里的 `root` 字段，
    # 这里拿不到也不需要存储根（铁律 #5：留空是"没这一栏"，不是"验过"）。
    fake = simulate_track("fake", ir, seed, events, clock=clock)  # store-injection: exempt(纯仿真对拍，store 只喂 root 读数)
    hifi = simulate_track("hifi", ir, seed, events, clock=clock)  # store-injection: exempt(同上)
    graph = load_graph(dict(ir))
    whitelisted = set(sun_trigger_automation_ids(ir))

    divergences: list[dict[str, Any]] = []

    # 1) final_states 比较：剔除 sun 白名单自动化直接写出的实体
    sun_written = {e for auto in graph if auto.id in whitelisted for e in auto.writes()}
    for entity, fv in fake["final_states"].items():
        if entity in sun_written:
            continue
        hv = hifi["final_states"].get(entity)
        if fv != hv:
            divergences.append(
                {"kind": "final_state", "entity": entity, "fake": fv, "hifi": hv}
            )

    # 2) 逐自动化 expect 判定比较：sun 白名单自动化跳过
    fauto = fake["expect"].get("automations") or {}
    hauto = hifi["expect"].get("automations") or {}
    for auto_id in sorted(set(fauto) | set(hauto)):
        if auto_id in whitelisted:
            continue
        fverdict = _auto_verdict(fauto.get(auto_id))
        hverdict = _auto_verdict(hauto.get(auto_id))
        if fverdict != hverdict:
            divergences.append(
                {
                    "kind": "expect",
                    "automation_id": auto_id,
                    "fake": fverdict,
                    "hifi": hverdict,
                }
            )

    return {
        "fake": fake,
        "hifi": hifi,
        "whitelisted": sorted(whitelisted),
        "divergences": divergences,
    }
