"""af_apply — 一次走完 校验→仿真→入队。

从 staging 区取 IR，依次执行：
1. build（安全闸校验）
2. simulate（仿真回放）
3. save（入待批队列）

`stage="dry_run"` 只跑 1、2 并且零写入（不消费首演码、不入队、不进试演期）。
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
from .af_ir import (
    Automation,
    GROUP_MODE_SEQUENCE,
    load_graph,
)

#: `apply` 的合法 stage。`dry_run` = 只跑 build+simulate，既不消费首演码也不入队
#: （ADM 联动执行计划·第 4 步①：DB 走的"拟→验→批→部署"里，"验"必须能在零写入前提下跑）。
APPLY_STAGES: tuple[str, ...] = ("check", "simulate", "dry_run", "save")

#: 组部署路径（`apply_group`）沿用的旧名，与 `save` 同义。不进 MCP 工具面，
#: 但必须被 `apply` 接受——否则既有调用会从今天起判"未知 stage"。
STAGE_ALIASES: dict[str, str] = {"apply": "save"}

_ALLOWED_STAGES: tuple[str, ...] = APPLY_STAGES + tuple(STAGE_ALIASES)


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
    af_audit.record_deploy(
        af_audit.AuditEvent(
            af_audit.PREMIERE_ISSUED,
            at=datetime.now(timezone.utc),
            message=f"premiere code issued for diff {store_diff_sha[:12]}",
            data={"store_diff_sha256": store_diff_sha, "code": code},
        ),
        store,
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
        stage: 执行到哪一步（check / simulate / dry_run / save）
        store: GraphStore 实例
        premiere_code: 部署前签发的首演码；提供则先消费闸门（哈希一致 + 未过期 +
            未重放）才放行，失败即中止并返回 ok=False。为 None 时不开闸门
            （向后兼容既有调用 / 现有 forge run/watch --live 三重闸）。dry_run 一律
            不开闸门——一次性码被"看一眼"就烧掉，等于把试演门槛变成消耗品。

    Returns:
        {ok, summary, build_report, sim_report, save_result}
    """
    if stage not in _ALLOWED_STAGES:
        # 未知 stage 过去会一路落到 save：打错一个字母 = 部署。这里改为拒绝。
        return {
            "ok": False,
            "stage": "args",
            "reason": "unknown_stage",
            "error": f"未知 stage {stage!r}，允许的取值：{list(APPLY_STAGES)}",
            "allowed_stages": list(APPLY_STAGES),
            "ref": ref,
        }
    stage = STAGE_ALIASES.get(stage, stage)   # 旧名归一，下游只认 APPLY_STAGES
    dry_run = stage == "dry_run"

    # 1. 从 staging 取 IR
    staged = get_staged(ref)
    graph = staged["graph"]
    summary = staged["summary"]
    ir_list = graph_to_raw(graph)
    ir_payload = {"automations": ir_list}

    # 1.5 试演期暂停闸（决策 F：失败即暂停）—— 放在首演码消费之前，避免浪费一次性码
    # 无 trial 记录 → 不拦截（向后兼容，保证既有测试/调用链不破）
    store_diff_sha = _store_diff_sha(ref)
    if af_premiere.is_paused(store_diff_sha):
        af_audit.record_deploy(
            af_audit.AuditEvent(
                af_audit.PREMIERE_CONSUMED,
                at=datetime.now(timezone.utc),
                message=f"apply blocked: trial paused for diff {store_diff_sha[:12]}",
                data={"store_diff_sha256": store_diff_sha, "reason": "trial_paused"},
            ),
            store,
        )
        return {
            "ok": False,
            "stage": "trial",
            "reason": "trial_paused",
            "ref": ref,
            "summary": summary,
            "store_diff_sha256": store_diff_sha,
        }

    # 1.6 首演码闸门（可选）：绑定 store diff SHA256 防掉包 + 过期 + 原子防重放
    # dry_run 不开闸门：一次性码消费掉就没了，"先看看"不该有代价。
    if premiere_code is not None and not dry_run:
        res = af_premiere.consume(premiere_code, store_diff_sha)
        af_audit.record_deploy(
            af_audit.AuditEvent(
                af_audit.PREMIERE_CONSUMED,
                at=datetime.now(timezone.utc),
                message=(
                    "premiere consume ok"
                    if res
                    else f"premiere consume rejected: {res['reason']}"
                ),
                data={"store_diff_sha256": store_diff_sha, "reason": res["reason"], "ok": bool(res)},
            ),
            store,
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

    if dry_run:
        # dry_run 的正面证据要写进返回值，而不是靠调用方"记得它没入队"：
        # would_enqueue=True 表示真跑 save 就会入队，pending_ref=None 表示此刻队列里什么都没有。
        return {**result, "stage": "dry_run", "dry_run": True,
                "would_enqueue": True, "pending_ref": None}

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
        af_audit.record_deploy(
            af_audit.AuditEvent(
                af_audit.PREMIERE_TRIAL_STARTED,
                at=datetime.now(timezone.utc),
                message=f"trial period started for diff {store_diff_sha[:12]}",
                data={"store_diff_sha256": store_diff_sha},
            ),
            store,
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


def _group_caveats(mode: str, children: list, stage: str, simulated: bool) -> list[str]:
    """诚实报告：group 这次到底验到了什么、没验到什么（第 3 步③）。

    组合的"原子性"目前只覆盖到**入待批队列**这一段；真正写 HA 发生在人工 approve 之后，
    那时每条子自动化是独立的 save_graph，不再有 group 级的回滚把手。这句话必须让签核的
    人看得到，否则 `ok=True` 会被读成"部署失败也能整体撤回"。
    """
    out = [
        f"编排方式 {mode}：" + (
            "子自动化按数组序逐条下发，任一失败即停并回滚已入队项"
            if mode == GROUP_MODE_SEQUENCE
            else "子自动化彼此声明为无依赖，全部尝试完再判定；失败同样整体回滚已入队项"
        ),
        "原子性范围 = 待批队列：approve 之后各子自动化独立落盘，届时撤回走 undo，不是 group 级回滚",
    ]
    if not simulated:
        out.append("本次 simulate=False，子自动化未经仿真即入队（校验覆盖为空）")
    elif stage not in ("apply", "save"):
        out.append(f"stage={stage} 只仿真不入队，未验证入队侧的失败与回滚路径")
    return out


def apply_group(group_auto: Any, store: Any = None, *,
                simulate: bool = True, stage: str = "apply") -> dict[str, Any]:
    """原子部署 group 复合自动化（v2.3/F9，决策 D 方案 B 并入 F10②）。

    - 先仿真全部子自动化（无副作用）；任一失败 → 整体 ok=False 且不入队（原子回滚单位 = group ref）。
    - 全量通过 → 依次入待批队列，统一打 group_ref 标签（单 ref 回滚）。
    - 组前做组合冲突预检（F10②）。
    - 组内编排方式取 group 节点的 `mode`（第 3 步②）：`sequence`（默认）在第一个失败处即停，
      `parallel` 把剩余子自动化也试完再判定。**两者都保证"有失败就不留半部署态"**：
      已入队的条目按各自 pending op_id 逐条 reject 掉。
    """
    if not isinstance(group_auto, Automation):
        group_auto = Automation.from_dict(group_auto)
    gnode = next((n for n in group_auto.nodes.values() if n.kind == "group"), None)
    if gnode is None:
        return {"ok": False, "error": {"code": "NO_GROUP", "message": "不是 group 复合 IR"}}
    children = list(gnode.children)
    if not children:
        return {"ok": False, "error": {"code": "EMPTY_GROUP",
                                       "message": "group 没有子自动化，没有可部署单元"}}
    mode = gnode.mode or GROUP_MODE_SEQUENCE
    conflicts = cross_automation_conflicts(children)

    # 1. 仿真（原子性闸门：此阶段任何失败都不需要回滚，因为一条都还没入队）
    sim_failures: list[dict[str, Any]] = []
    if simulate:
        for child in children:
            sim = af_service.simulate(child.raw, store=store)
            if sim.get("ok"):
                continue
            sim_failures.append({"child": child.id, "sim": sim})
            if mode == GROUP_MODE_SEQUENCE:
                break
    if sim_failures:
        first = sim_failures[0]
        return {
            "ok": False,
            "stage": "simulate",
            "group_mode": mode,
            "error": {"code": "CHILD_SIM_FAILED",
                      "message": f"子自动化 {first['child']} 仿真失败",
                      "child": first["child"], "sim": first["sim"],
                      "failures": [f["child"] for f in sim_failures]},
            "deployed": [],
            "conflicts": conflicts,
        }

    # 2. 全量入队（单 ref 回滚单位）
    group_ref = group_auto.id
    deployed: list[str] = []
    refs: dict[str, str] = {}        # child.id -> pending op_id（回滚要按 op_id 删，不是按 child id）
    submit_failures: list[dict[str, str]] = []
    if stage in ("apply", "save"):
        for child in children:
            save_payload = {"ir": child.raw, "name": child.name, "group_ref": group_ref}
            try:
                # submit_pending 的拒绝形态是 **raise**（ServiceError：IR 非法 / 静态扫描不过 /
                # 待批熔断），不是 ok=False。不接住它，异常会穿出 apply_group——前面已入队的
                # 子自动化就留在队列里，"原子部署"当场变成半部署。
                res = af_service.submit_pending(store, "af_apply", save_payload, submitted_by="af_apply")
            except Exception as exc:  # noqa: BLE001
                submit_failures.append({"child": child.id,
                                        "error": f"{type(exc).__name__}: {exc}"})
                if mode == GROUP_MODE_SEQUENCE:
                    break
                continue
            op_id = res.get("pending") if isinstance(res, dict) else None
            if not res.get("ok") or not op_id:
                # 拿不到 pending op_id 就等于拿不到回滚把手，不能算"已部署"（铁律 #5）
                submit_failures.append({
                    "child": child.id,
                    "error": ("submit 未返回 ok" if not res.get("ok")
                              else "submit 成功但未返回 pending op_id（无法回滚）"),
                })
                if mode == GROUP_MODE_SEQUENCE:
                    break
                continue
            deployed.append(child.id)
            refs[child.id] = str(op_id)

    if submit_failures:
        rollback_failed: list[dict[str, str]] = []
        undone: list[str] = []
        for child_id in deployed:
            op_id = refs[child_id]
            try:
                # reject_pending 是既有唯一"把待批条目干净摘掉"的入口（load + delete + 留痕）。
                # 原先调的 `store.rollback_pending(...)` 在真实 GraphStore 上根本不存在，
                # 且传的是 child.id 而不是 op_id——生产路径上次真出事时报的会是 AttributeError。
                af_service.reject_pending(store, op_id, reason=f"group {group_ref} 原子回滚")
            except Exception as exc:  # noqa: BLE001
                # 回滚失败不能咽：咽下就变成"已回滚、deployed 为空"的假象，
                # 而待批队列里其实还留着条目（铁律 #5：回滚不了要 fail-closed 报出去）。
                rollback_failed.append({"ref": op_id, "child": child_id,
                                        "error": f"{type(exc).__name__}: {exc}"})
                continue
            undone.append(child_id)
        residue = [c for c in deployed if c not in undone]
        first = submit_failures[0]
        return {
            "ok": False,
            "stage": "save",
            "group_mode": mode,
            "error": {"code": "SUBMIT_FAILED", "child": first["child"],
                      "message": first["error"],
                      "failures": [f["child"] for f in submit_failures]},
            "deployed": residue if rollback_failed else [],
            "rollback_failed": rollback_failed,
            "rolled_back": undone,
            "conflicts": conflicts,
        }

    # ok 由"该 stage 应入队的子自动化确实全部拿到了 pending ref"决定，不是字面量：
    # apply/save 轨要求每个 child 都入队；其余 stage 没有入队义务，空集即成立。
    expected = [c.id for c in children] if stage in ("apply", "save") else []
    return {"ok": deployed == expected, "ref": group_ref, "children": [c.id for c in children],
            "deployed": deployed, "pending_refs": refs, "conflicts": conflicts,
            "group_mode": mode, "stage": stage,
            "caveats": _group_caveats(mode, children, stage, simulate)}
