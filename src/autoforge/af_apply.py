"""af_apply — 一次走完 校验→仿真→入队。

从 staging 区取 IR，依次执行：
1. build（安全闸校验）
2. simulate（仿真回放）
3. save（入待批队列）
"""

from __future__ import annotations

from typing import Any

from .af_draft import get_staged, DraftError
from .af_spec import graph_to_raw
from . import af_service


def apply(ref: str, stage: str = "save", store: Any = None) -> dict[str, Any]:
    """一次走完 校验→仿真→入队。

    Args:
        ref: af_draft 返回的 ref
        stage: 执行到哪一步（check / simulate / save）
        store: GraphStore 实例

    Returns:
        {ok, summary, build_report, sim_report, save_result}
    """
    # 1. 从 staging 取 IR
    staged = get_staged(ref)
    graph = staged["graph"]
    summary = staged["summary"]
    ir_list = graph_to_raw(graph)
    ir_payload = {"automations": ir_list}

    result: dict[str, Any] = {"ok": True, "ref": ref, "summary": summary}

    # 2. build（安全闸）
    build_result = af_service.build(ir_payload, store=store, use_catalog=True)
    result["build"] = build_result
    if not build_result.get("ok"):
        result["ok"] = False
        result["stage"] = "build"
        return result

    if stage == "check":
        return result

    # 3. simulate（仿真回放）
    sim_result = af_service.simulate(graph.raw, store=store)
    result["simulate"] = sim_result
    if not sim_result.get("ok"):
        result["ok"] = False
        result["stage"] = "simulate"
        return result

    if stage == "simulate":
        return result

    # 4. save（入待批队列）
    save_result = af_service.submit_pending(
        store,
        graph.raw,
        submitted_by="af_apply",
    )
    result["save"] = save_result
    result["stage"] = "save"

    return result
