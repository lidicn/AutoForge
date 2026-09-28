"""af_draft — 意图 JSON → IR（服务端起 id、建节点、连边）。

Agent 只传中文实体名 + 触发 + 动作，服务端负责：
1. 解析实体名 → entity_id
2. 生成节点 id（t1/i1/d1/p1...）
3. 自动连边
4. 编译成 IR
5. 返回 ref（不回传 IR 全文）
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from .af_ir import IR_VERSION, load_graph
from .af_spec import SpecError

__all__ = [
    "DraftError",
    "E_UNKNOWN_DOMAIN",
    "DraftResult",
    "draft_intent",
    "StagingStore",
    "ComposeMetrics",
    "compose_metrics_summary",
    "reset_compose_metrics",
]

# ── 封闭词表（v2 M2：意图 JSON 顶层域编译前即拒，并回灌可机读 fix）──
E_UNKNOWN_DOMAIN = "E_UNKNOWN_DOMAIN"
_INTENT_FIELDS = ("name", "mode", "when", "if", "do", "do_list", "ask", "wait")


class DraftError(Exception):
    """意图解析错误。"""

    def __init__(self, code: str, message: str, fix: dict[str, Any] | None = None):
        self.code = code
        self.fix = fix
        super().__init__(message)


# ─────────────────────────────────────────────────────────────────────
# Staging 区（IR 暂存，用 ref 引用）
# ─────────────────────────────────────────────────────────────────────


class StagingStore:
    """会话级 IR 暂存区，带 TTL。"""

    def __init__(self, ttl: int = 3600):
        self._items: dict[str, dict[str, Any]] = {}
        self._ttl = ttl

    def put(self, graph: Any, summary: str, resolved: dict[str, str]) -> str:
        ref = f"af:{uuid.uuid4().hex[:6]}"
        self._items[ref] = {
            "graph": graph,
            "summary": summary,
            "resolved": resolved,
            "created_at": time.time(),
        }
        return ref

    def get(self, ref: str) -> dict[str, Any]:
        self._expire()
        if ref not in self._items:
            raise DraftError("E_REF_NOT_FOUND", f"ref {ref} 不存在或已过期")
        return self._items[ref]

    def _expire(self) -> None:
        now = time.time()
        expired = [k for k, v in self._items.items() if now - v["created_at"] > self._ttl]
        for k in expired:
            del self._items[k]


# 全局 staging（进程级）
_staging = StagingStore()


# ─────────────────────────────────────────────────────────────────────
# 决策门数据采集（F9 / 决策 D）：compose 会话多意图占比
# ─────────────────────────────────────────────────────────────────────
# v2.3 是否真正落地 group 容器节点，取决于「单次 compose 会话产出 ≥2 条自动化
# 的占比」是否 ≥5%（否则推迟）。本采集器由 draft_intent 在 session_id 传入时
# 累加，暴露聚合指标供生产评估决策门；不传 session_id 时零影响（默认无采样）。


class ComposeMetrics:
    """多意图 compose 会话计数器（进程级，线程不安全但仅计数近似）。

    一个 session_id = 一次用户 compose 请求；每次 draft_intent(session_id=...)
    视为该会话产出的 1 条自动化。summary() 给出多意图会话占比，供决策门判定。
    """

    def __init__(self) -> None:
        self._per_session: dict[str, int] = {}

    def record(self, session_id: str) -> None:
        self._per_session[session_id] = self._per_session.get(session_id, 0) + 1

    def summary(self) -> dict[str, object]:
        counts = list(self._per_session.values())
        sessions = len(counts)
        multi = sum(1 for c in counts if c >= 2)
        return {
            "sessions": sessions,
            "multi_intent_sessions": multi,
            "single_intent_sessions": sessions - multi,
            "total_automations": sum(counts),
            "multi_intent_ratio": (multi / sessions) if sessions else 0.0,
        }

    def reset(self) -> None:
        self._per_session.clear()


_compose_metrics = ComposeMetrics()


def compose_metrics_summary() -> dict[str, object]:
    """返回当前进程累计的多意图 compose 会话统计（决策门评估用）。"""
    return _compose_metrics.summary()


def reset_compose_metrics() -> None:
    """仅供测试/运维重置累计（不破坏既有 ref）。"""
    _compose_metrics.reset()


# ─────────────────────────────────────────────────────────────────────
# 实体解析（占位，实际接 af_catalog）
# ─────────────────────────────────────────────────────────────────────


def _resolve_entity(name: str, catalog: Any = None) -> str:
    """把中文名解析成 entity_id。实际应该查 catalog。"""
    # TODO: 接 af_catalog 做真实解析
    return name  # 暂时透传，后续接 catalog


# ─────────────────────────────────────────────────────────────────────
# 意图 JSON → IR
# ─────────────────────────────────────────────────────────────────────


def _gen_id(prefix: str, counter: list[int]) -> str:
    counter[0] += 1
    return f"{prefix}{counter[0]}"


def draft_intent(
    intent: dict[str, Any],
    catalog: Any = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """把意图 JSON 编译成 IR，存 staging 区，返回 ref。

    意图 JSON 格式：
    {
      "name": "开门亮灯",
      "mode": "restart",
      "when": {"type": "state", "entity": "前门", "to": "on"},
      "if": {"lt": {"var": "照度", "const": 200}},
      "do": {"action": "开灯", "target": "客厅灯"}
    }

    session_id：可选。同一用户 compose 请求内多次调用传相同 session_id，
    供 ComposeMetrics 统计多意图会话占比（决策门评估，见 ComposeMetrics）。
    """
    # v2 M2：意图顶层域封闭词表——未知域编译前即拒并回灌可机读 fix
    unknown = [k for k in intent if k not in _INTENT_FIELDS]
    if unknown:
        raise DraftError(
            E_UNKNOWN_DOMAIN,
            f"意图包含未知域: {unknown}",
            fix={"allowed": list(_INTENT_FIELDS), "unknown": unknown},
        )

    name = intent.get("name", "未命名自动化")
    mode = intent.get("mode", "single")
    when = intent.get("when")
    do = intent.get("do")
    do_list = intent.get("do_list")  # 多动作支持
    if_cond = intent.get("if")
    ask = intent.get("ask")
    wait = intent.get("wait")

    if not when:
        raise DraftError("E_MISSING_WHEN", "意图必须包含 when（触发条件）")
    # do 不是必须的：如果有 ask，ask 可以作为终点
    if not do and not do_list and not ask:
        raise DraftError("E_MISSING_DO", "意图必须包含 do（动作）或 ask（询问）")

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    counter = [0]
    resolved: dict[str, str] = {}

    # 1. on 节点
    on_id = _gen_id("t", counter)
    trigger = _resolve_trigger(when, resolved, catalog)
    nodes.append({"id": on_id, "kind": "on", "name": "触发", "trigger": trigger})
    prev = on_id

    # 2. if 节点
    if if_cond:
        if_id = _gen_id("i", counter)
        expr = _resolve_expr(if_cond, resolved, catalog)
        nodes.append({"id": if_id, "kind": "if", "name": "条件", "expr": expr})
        edges.append({"from": prev, "to": if_id, "kind": "then"})
        prev = if_id
        if_entry = if_id
    else:
        if_entry = None

    # 3. ask 节点
    if ask:
        ask_id = _gen_id("a", counter)
        prompt = ask.get("prompt", "要执行吗？")
        room = ask.get("room")
        timeout = ask.get("timeout", "30s")
        ask_node = {"id": ask_id, "kind": "ask", "name": "询问", "prompt": prompt, "timeout": timeout}
        if room:
            ask_node["room"] = room
        nodes.append(ask_node)
        edges.append({"from": prev, "to": ask_id, "kind": "yes" if if_entry else "then"})
        prev = ask_id

    # 4. wait 节点
    if wait:
        wait_id = _gen_id("w", counter)
        nodes.append({"id": wait_id, "kind": "wait", "name": "等待", "duration": wait})
        edges.append({"from": prev, "to": wait_id, "kind": "then"})
        prev = wait_id

    # 5. do 节点（支持单动作和多动作；多动作 list 内可混 wait 步骤）
    raw_actions = []
    if do_list and isinstance(do_list, list):
        raw_actions = do_list
    elif do and isinstance(do, list):
        raw_actions = do
    elif do:
        raw_actions = [do]

    do_ids = []
    for i, item in enumerate(raw_actions):
        edge_kind = "yes" if (i == 0 and if_entry and not ask) else "then"
        if isinstance(item, dict) and "wait" in item:
            nid = _gen_id("w", counter)
            nodes.append({"id": nid, "kind": "wait", "name": "等待", "duration": item["wait"]})
            edges.append({"from": prev, "to": nid, "kind": edge_kind})
            prev = nid
        else:
            nid = _gen_id("d", counter)
            do_node = _resolve_do(item, resolved, catalog)
            do_node["id"] = nid
            do_node["kind"] = "do"
            do_node["name"] = f"动作{i+1}" if len(raw_actions) > 1 else "动作"
            nodes.append(do_node)
            edges.append({"from": prev, "to": nid, "kind": edge_kind})
            prev = nid
            do_ids.append(nid)

    # 6. pass 节点
    pass_id = _gen_id("p", counter)
    nodes.append({"id": pass_id, "kind": "pass", "name": "结束"})

    if do_ids:
        last_do = do_ids[-1]
        edges.append({"from": last_do, "to": pass_id, "kind": "then"})
        # do 失败 → 到 pass
        edges.append({"from": last_do, "to": pass_id, "kind": "on_error"})
    elif ask:
        # 没有 do，ask 同意后直接到 pass
        edges.append({"from": prev, "to": pass_id, "kind": "yes"})
    else:
        edges.append({"from": prev, "to": pass_id, "kind": "then"})

    # if 条件不满足 → 直接到 pass
    if if_entry:
        edges.append({"from": if_entry, "to": pass_id, "kind": "no"})

    # ask 超时/取消 → 到 pass
    if ask:
        edges.append({"from": ask_id, "to": pass_id, "kind": "on_timeout"})
        edges.append({"from": ask_id, "to": pass_id, "kind": "on_cancel"})

    # 组装 IR
    auto_id = f"auto_{uuid.uuid4().hex[:6]}"
    ir = {
        "ir_version": IR_VERSION,
        "id": auto_id,
        "name": name,
        "version": 1,
        "mode": mode,
        "nodes": nodes,
        "edges": edges,
    }

    # 编译成 Graph
    graph = load_graph({"automations": [ir]})

    # 存 staging
    if raw_actions:
        if len(raw_actions) == 1 and isinstance(raw_actions[0], dict):
            action_desc = f"{raw_actions[0].get('action', '')} {raw_actions[0].get('target', '')}"
        else:
            action_desc = f"{len(raw_actions)}个动作"
    else:
        action_desc = "询问"
    summary = f"{when.get('entity', '')} {when.get('to', '')} → {action_desc}"
    ref = _staging.put(graph, summary, resolved)

    # 决策门采样：仅当调用方传入 session_id（同一次 compose 请求内稳定）才累加
    if session_id is not None:
        _compose_metrics.record(session_id)

    return {
        "ok": True,
        "ref": ref,
        "summary": summary,
        "resolved": resolved,
    }


def _resolve_trigger(when: dict[str, Any], resolved: dict[str, str], catalog: Any) -> dict[str, Any]:
    """解析触发条件。"""
    t_type = when.get("type", "state")
    entity_name = when.get("entity", "")
    entity_id = _resolve_entity(entity_name, catalog)
    resolved[entity_name] = entity_id

    trigger = {"type": t_type, "entity_id": entity_id}
    if "to" in when:
        trigger["to"] = when["to"]
    if "at" in when:
        trigger["at"] = when["at"]
    if "event" in when:
        trigger["event"] = when["event"]
    return trigger


def _resolve_expr(expr: dict[str, Any], resolved: dict[str, str], catalog: Any) -> dict[str, Any]:
    """解析条件表达式，支持 and/or 嵌套。"""
    # and/or 组合
    for op in ("and", "or"):
        if op in expr and isinstance(expr[op], list):
            args = [_resolve_expr(sub, resolved, catalog) for sub in expr[op]]
            return {"op": op, "args": args}
    # 单条件：{"lt": {"var": "xxx", "const": 200}}
    for op, val in expr.items():
        if isinstance(val, dict) and "var" in val:
            var_name = val["var"]
            entity_id = _resolve_entity(var_name, catalog)
            resolved[var_name] = entity_id
            vtype = val.get("type", "numeric")
            if var_name.startswith("entity.") or "." in var_name:
                left = {"var": var_name, "type": vtype}
            else:
                left = {"var": f"entity.{entity_id}", "type": vtype}
            right = {"const": val["const"]}
            return {"op": op, "left": left, "right": right}
    raise DraftError("E_INVALID_EXPR", f"无法解析条件表达式: {expr}")


def _resolve_do(do: dict[str, Any], resolved: dict[str, str], catalog: Any) -> dict[str, Any]:
    """解析动作节点。"""
    action = do.get("action", "")
    target = do.get("target", "")
    entity_id = _resolve_entity(target, catalog)
    resolved[target] = entity_id

    # 动作映射：开灯/turn_on → light.turn_on
    action_map = {
        "开灯": ("ha", "light.turn_on"),
        "关灯": ("ha", "light.turn_off"),
        "开空调": ("ha", "climate.turn_on"),
        "关空调": ("ha", "climate.turn_off"),
        "turn_on": ("ha", "light.turn_on"),
        "turn_off": ("ha", "light.turn_off"),
        "light.turn_on": ("ha", "light.turn_on"),
        "light.turn_off": ("ha", "light.turn_off"),
        "climate.turn_on": ("ha", "climate.turn_on"),
        "climate.turn_off": ("ha", "climate.turn_off"),
    }
    adapter, action_name = action_map.get(action, ("ha", action))

    params = {"entity_id": entity_id}
    if "temperature" in do:
        params["temperature"] = do["temperature"]
    if "hvac_mode" in do:
        params["hvac_mode"] = do["hvac_mode"]

    node = {
        "adapter": adapter,
        "action": action_name,
        "params": params,
        "result_var": f"{action_name.replace('.', '_')}_result",
    }
    # 透传安全/策略字段：L2/L3 强制确认、灰度、置信、优先级
    # （否则节点缺 requires_confirm → scanner 误报 L2_NEEDS_CONFIRM 假阳性，且 intent 无法修正）
    for _k in ("requires_confirm", "canary", "conf", "priority"):
        if _k in do:
            node[_k] = do[_k]
    return node


def get_staged(ref: str) -> dict[str, Any]:
    """从 staging 区取 IR。"""
    return _staging.get(ref)
