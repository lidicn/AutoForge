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
from .af_ir import Automation, load_graph


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

    # 1.3 group 复合 IR 短路到原子部署（F9/v2.3，并入 F10②）
    # premiere 闸门已在上方消费；此处直接走 apply_group（全成功或全回滚，单 group ref 回滚单位）。
    # group 以单自动化 Graph（mode='group'）形式进入既有 staging 管线，零模型改动。
    if ir_list and ir_list[0].get("mode") == "group":
        group_auto = Automation.from_dict(ir_list[0])
        return apply_group(group_auto, store, stage=stage)

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


# ─────────────────────────────────────────────────────────────────────────────
# v2.3 / F9 group 复合部署（决策 D 方案 B，并入 F10②）
# ─────────────────────────────────────────────────────────────────────────────

def _intent_of_action(action: str) -> str:
    a = (action or "").lower()
    if "turn_off" in a:
        return "off"
    if "turn_on" in a:
        return "on"
    if a.endswith(".lock"):
        return "lock"
    if a.endswith(".unlock"):
        return "unlock"
    if "set" in a:
        return "set"
    return "act"


def _child_entity_ops(child: "Automation") -> list[tuple[str, str]]:
    ops: list[tuple[str, str]] = []
    for n in child.nodes.values():
        if n.kind == "on":
            trig = n.trigger
            e = getattr(trig, "entity_id", None) if trig is not None else None
            if e:
                ops.append((e, "trigger"))
        elif n.kind == "do":
            params = n.params if isinstance(n.params, dict) else {}
            e = params.get("entity_id")
            if e:
                ops.append((e, _intent_of_action(n.action)))
    return ops


def cross_automation_conflicts(automations: list["Automation"]) -> list[dict[str, Any]]:
    """F10② 跨自动化一致性校验：检测一组自动化之间的共享 / 相反实体操作。

    既用于 group 子自动化（compose_group → apply_group），也可用于 store 内任意
    自动化集合（check_store_cross_conflicts）。输入为 Automation 列表。
    """
    by_entity: dict[str, list[tuple[str, str]]] = {}
    for c in automations:
        for e, intent in _child_entity_ops(c):
            by_entity.setdefault(e, []).append((c.id, intent))
    findings: list[dict[str, Any]] = []
    for e, ops in by_entity.items():
        if len(ops) <= 1:
            continue
        intents = {i for _, i in ops}
        if ("on" in intents and "off" in intents) or ("lock" in intents and "unlock" in intents):
            findings.append({
                "entity": e, "type": "conflict", "ops": ops,
                "message": f"组合冲突预检：实体 {e} 在子自动化间被相反操作（{sorted(intents)}）",
            })
        else:
            findings.append({
                "entity": e, "type": "shared", "ops": ops,
                "message": f"组合共享实体：{e} 被多子自动化引用（{sorted(intents)}），请确认无相互覆盖",
            })
    return findings


def check_store_cross_conflicts(store: Any) -> list[dict[str, Any]]:
    """F10② 跨自动化一致性校验（store 级）：扫描 store 内全部自动化，检测共享 / 相反实体操作。

    逐条读取归档（history → load_record → load_graph → graph_to_raw → Automation），
    失败项跳过，最终对全部 Automation 跑 cross_automation_conflicts。只读、无副作用。
    """
    autos: list[Automation] = []
    for rec in store.history():
        name = rec.get("name") if isinstance(rec, dict) else None
        if not name:
            continue
        try:
            rec_full = store.load_record(name)
            graph = load_graph(rec_full["graph"])
        except Exception:
            continue
        for ir in graph_to_raw(graph):
            try:
                autos.append(Automation.from_dict(ir))
            except Exception:
                continue
    return cross_automation_conflicts(autos)


def apply_group(group_auto: Any, store: Any = None, *,
                simulate: bool = True, stage: str = "apply") -> dict[str, Any]:
    """原子部署 group 复合自动化（v2.3/F9，决策 D 方案 B 并入 F10②）。

    - 先仿真全部子自动化（无副作用）；任一失败 → 整体 ok=False 且不入队（原子回滚单位 = group ref）。
    - 全量通过 → 依次入待批队列，统一打 group_ref 标签（单 ref 回滚）。
    - 组前做组合冲突预检（F10②）。
    """
    if not isinstance(group_auto, Automation):
        group_auto = Automation.from_dict(group_auto)
    gnode = next((n for n in group_auto.nodes.values() if n.kind == "group"), None)
    if gnode is None:
        return {"ok": False, "error": {"code": "NO_GROUP", "message": "不是 group 复合 IR"}}
    children = list(gnode.children)
    conflicts = cross_automation_conflicts(children)

    # 1. 全量仿真（原子性闸门）
    for child in children:
        if not simulate:
            continue
        sim = af_service.simulate(child.raw, store=store)
        if not sim.get("ok"):
            return {
                "ok": False,
                "stage": "simulate",
                "error": {"code": "CHILD_SIM_FAILED", "message": f"子自动化 {child.id} 仿真失败", "child": child.id, "sim": sim},
                "deployed": [],
                "conflicts": conflicts,
            }

    # 2. 全量入队（单 ref 回滚单位）
    group_ref = group_auto.id
    deployed: list[str] = []
    if stage in ("apply", "save"):
        for child in children:
            save_payload = {"ir": child.raw, "name": child.name, "group_ref": group_ref}
            res = af_service.submit_pending(store, "af_apply", save_payload, submitted_by="af_apply")
            if not res.get("ok"):
                for d in deployed:
                    try:
                        store.rollback_pending(d)
                    except Exception:
                        pass
                return {"ok": False, "stage": "save", "error": {"code": "SUBMIT_FAILED", "child": child.id}, "deployed": [], "conflicts": conflicts}
            deployed.append(child.id)

    return {"ok": True, "ref": group_ref, "children": [c.id for c in children], "deployed": deployed, "conflicts": conflicts, "stage": stage}
