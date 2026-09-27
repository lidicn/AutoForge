"""af_apply — 一次走完 校验→仿真→入队。

从 staging 区取 IR，依次执行：
1. build（安全闸校验）
2. simulate（仿真回放）
3. save（入待批队列）
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from .af_draft import get_staged, DraftError
from .af_spec import graph_to_raw
from . import af_audit, af_premiere
from . import af_service


def _store_diff_sha(ref: str) -> str:
    """store diff 的规范化 SHA256：首演码绑定此哈希以防伪（验码后掉包即拒）。

    规范化采用 sort_keys + 紧凑分隔符，保证 issue 与 consume 两侧算出同一哈希。
    """
    staged = get_staged(ref)
    graph = staged["graph"]
    ir_list = graph_to_raw(graph)
    ir_payload = {"automations": ir_list}
    canonical = json.dumps(
        ir_payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def issue_premiere(ref: str, store: Any = None, ttl_s: int = 300) -> dict[str, Any]:
    """为一次部署签发首演码（部署仪式第一步）。

    计算 store diff SHA256 并签发 6 位一次性码；返回码与哈希供运维在部署时回传。
    """
    store_diff_sha = _store_diff_sha(ref)
    code = af_premiere.issue(store_diff_sha, ttl_s=ttl_s)
    af_audit.AuditLog.add(
        af_audit.AuditEvent(
            af_audit.PREMIERE_ISSUED,
            at=datetime.now(timezone.utc),
            message=f"premiere code issued for diff {store_diff_sha[:12]}",
            data={"store_diff_sha256": store_diff_sha, "code": code},
        )
    )
    return {"ok": True, "code": code, "store_diff_sha256": store_diff_sha}


def apply(
    ref: str,
    stage: str = "save",
    store: Any = None,
    premiere_code: str | None = None,
) -> dict[str, Any]:
    """一次走完 校验→仿真→入队（v2 M1：可选首演码闸门）。

    Args:
        ref: af_draft 返回的 ref
        stage: 执行到哪一步（check / simulate / save）
        store: GraphStore 实例
        premiere_code: 部署前签发的首演码；提供则先消费闸门（哈希一致 + 未过期 +
            未重放）才放行，失败即中止并返回 ok=False。为 None 时不开闸门
            （向后兼容既有调用 / 现有 forge run/watch --live 三重闸）。

    Returns:
        {ok, summary, build_report, sim_report, save_result}
    """
    # 1. 从 staging 取 IR
    staged = get_staged(ref)
    graph = staged["graph"]
    summary = staged["summary"]
    ir_list = graph_to_raw(graph)
    ir_payload = {"automations": ir_list}

    # 1.5 首演码闸门（可选）：绑定 store diff SHA256 防掉包 + 过期 + 原子防重放
    store_diff_sha = _store_diff_sha(ref)
    if premiere_code is not None:
        res = af_premiere.consume(premiere_code, store_diff_sha)
        af_audit.AuditLog.add(
            af_audit.AuditEvent(
                af_audit.PREMIERE_CONSUMED,
                at=datetime.now(timezone.utc),
                message=(
                    "premiere consume ok"
                    if res
                    else f"premiere consume rejected: {res['reason']}"
                ),
                data={"store_diff_sha256": store_diff_sha, "reason": res["reason"], "ok": bool(res)},
            )
        )
        if not res:
            return {
                "ok": False,
                "stage": "premiere",
                "reason": res["reason"],
                "ref": ref,
                "summary": summary,
            }

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
    sim_result = af_service.simulate(ir_payload, store=store)
    result["simulate"] = sim_result
    if not sim_result.get("ok"):
        result["ok"] = False
        result["stage"] = "simulate"
        return result

    if stage == "simulate":
        return result

    # 4. save（入待批队列）
    save_payload = {"ir": ir_payload, "name": staged.get("summary", "")}
    save_result = af_service.submit_pending(
        store,
        "af_apply",
        save_payload,
        submitted_by="af_apply",
    )
    result["save"] = save_result
    result["stage"] = "save"

    # 4.5 部署落地 → 自动进入试演期（默认 24h，只统计不封禁）
    if save_result.get("ok"):
        trial = af_premiere.enter_trial(store_diff_sha, hours=24)
        result["trial"] = trial
        af_audit.AuditLog.add(
            af_audit.AuditEvent(
                af_audit.PREMIERE_TRIAL_STARTED,
                at=datetime.now(timezone.utc),
                message=f"trial period started for diff {store_diff_sha[:12]}",
                data={"store_diff_sha256": store_diff_sha},
            )
        )

    return result
