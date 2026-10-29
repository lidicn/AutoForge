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
import re
import time
import uuid
from typing import Any

from .af_ir import IR_VERSION, MAX_EXPR_DEPTH, load_graph
from .af_spec import SpecError

__all__ = [
    "DraftError",
    "E_UNKNOWN_DOMAIN",
    "E_INTENT_NOT_OBJECT",
    "draft_intent",
    "StagingStore",
    "ComposeMetrics",
    "compose_metrics_summary",
    "reset_compose_metrics",
]

# ── 封闭词表（v2 M2：意图 JSON 顶层域编译前即拒，并回灌可机读 fix）──
E_UNKNOWN_DOMAIN = "E_UNKNOWN_DOMAIN"
# G5（2026-10-24）：意图必须是 JSON 对象（dict）。收到 str/list/None 时单独成码，
# 而不是逐字符/逐元素迭代成"未知域"——后者会把协议错误伪装成字段名错误，误导 agent。
E_INTENT_NOT_OBJECT = "E_INTENT_NOT_OBJECT"
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
# 实体解析（接 af_catalog，fail-closed）
# ─────────────────────────────────────────────────────────────────────

#: 已经是完整 entity_id 的形态：`域.实体`（`light.x` / `binary_sensor.door`）。
_ENTITY_ID_RE = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z0-9_]+$")


def _var_already_namespaced(var_name: Any) -> bool:
    """`var` 是否已是**带命名空间前缀**的引用（`entity.*` / `vars.*` / `context.*`）。

    G2 修复（实测 2026-10-23）：旧判断 `"." in var_name` 会把合法 entity_id
    （`sensor.xxx`——agent 按目录返回值直填的正常写法）误判成"已带命名空间"，
    生成的 `{"var": "sensor.xxx"}` 在求值时被 `split_namespace` 判成未知命名空间
    直接抛错。真源只有三个命名空间前缀（af_state.py `_NAMESPACES`），按前缀判，
    不按"有没有点"猜。
    """
    if not isinstance(var_name, str):
        return False
    return var_name.startswith(("entity.", "vars.", "context."))


def _resolve_entity(name: str, catalog: Any = None) -> str:
    """把中文名解析成 entity_id；**解析不出来绝不原样透传**。

    原实现是恒等透传（`return name`），于是中文设备名既不会被 `bind_ir` 的
    `?` 占位符闸门接管、又在 `resolved` 里被记成 `{'前门': '前门'}` —— 一个
    看着"已解析"、实际编译成非法 entity_id 的假象（新增审计 BUG-16）。

    现在的口径：
      1. 已经是完整 `域.实体` ⇒ 直接用（不是设备名，不该进目录解析）；
      2. 否则查目录（`af_catalog.resolve_best`），它**只在无歧义时**返回实体；
      3. 查不到 / 有歧义 / 没给 catalog ⇒ 返回 `?名字`，交 `bind_ir` 的
         unresolved 闸门 fail-closed（宁可编译不过，也不要猜一个实体下发）。
    """
    raw = str(name or "").strip()
    if not raw:
        return "?"  # 连名字都没有 ⇒ 交给 bind_ir 判 unresolved
    if _ENTITY_ID_RE.match(raw):
        return raw
    if raw.startswith("?"):  # 已经是占位符，原样带下去
        return raw
    if catalog is not None:
        resolver = getattr(catalog, "resolve_best", None)
        if callable(resolver):
            try:
                hit = resolver(raw)
            except Exception:  # 目录读失败不该把整条草稿带崩，按"未定"处理
                return "?" + raw
            if hit:
                return str(hit)
    # 没能确定 ⇒ 交给 bind_ir 判 unresolved（新增审计 BUG-16）
    return "?" + raw


# ─────────────────────────────────────────────────────────────────────
# 意图 JSON → IR
# ─────────────────────────────────────────────────────────────────────


def _gen_id(prefix: str, counter: list[int]) -> str:
    counter[0] += 1
    return f"{prefix}{counter[0]}"


def _node_kind(node_id: str, nodes: list[dict[str, Any]]) -> str:
    """查图内某节点的 kind；找不到返回空串（调用方按非 ask 处理）。

    G1 修复配套：`_build_edges` 需要按**上游节点种类**决定出边类型
    （if 出 `then`、ask 出 `yes`），此前是拿局部变量猜的，链条里混入
    wait/do 后就会猜错。
    """
    for n in nodes:
        if n.get("id") == node_id:
            return str(n.get("kind") or "")
    return ""


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
    # G5（2026-10-24）：类型闸——意图必须是 dict。传 str/list/None 时若直接落到下面的
    # `for k in intent`，str 会被逐字符迭代、每个汉字成"未知域"，把协议错误伪装成字段名错误。
    if not isinstance(intent, dict):
        raise DraftError(
            E_INTENT_NOT_OBJECT,
            f"意图必须是 JSON 对象（dict），收到 {type(intent).__name__}",
            fix={
                "expected": "object",
                "example": {"name": "...", "when": {}, "do": {}},
            },
        )

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

    # 字段形状闸（体验者崩溃 2026-10-27）：`when`/`if`/`ask`/`wait` 写错形状时，
    # 下游（_resolve_trigger/_resolve_expr/_resolve_do、`wait in item`）会对 str 成员
    # 运算崩成 ADM_ERR_INTERNAL —— G5 在顶层修过同族（intent 本身不许是字符串），
    # 字段级漏网。一律收 DraftError + 可机读 fix + 最小例子。
    if not isinstance(when, dict):
        raise DraftError(
            "E_INVALID_WHEN",
            f"`when` 必须是对象，收到 {type(when).__name__}：{when!r}",
            fix={"example": {"when": {"entity": "书房门", "to": "off"}, "do": {"action": "开空调", "target": "书房空调"}}},
        )
    if if_cond is not None and not isinstance(if_cond, dict):
        raise DraftError(
            "E_INVALID_EXPR",
            f"`if` 必须是条件对象（and/or/not/比较/一元谓词），收到 {type(if_cond).__name__}：{if_cond!r}",
            fix={"example": {"if": {"gt": {"var": "书房温度", "const": 26.5}}}},
        )
    if ask is not None and not isinstance(ask, dict):
        raise DraftError(
            "E_INVALID_ASK",
            f"`ask` 必须是对象（prompt/room/timeout），收到 {type(ask).__name__}：{ask!r}",
            fix={"example": {"ask": {"prompt": "要执行吗？", "room": "书房"}}},
        )
    if wait is not None and not isinstance(wait, str):
        raise DraftError(
            "E_INVALID_WAIT",
            f"`wait` 必须是时长字符串（如 \"10m\"），收到 {type(wait).__name__}：{wait!r}",
        )

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    counter = [0]
    resolved: dict[str, str] = {}

    # 1. on 节点
    on_id = _gen_id("t", counter)
    trigger = _resolve_trigger(when, resolved, catalog)
    on_node: dict[str, Any] = {"id": on_id, "kind": "on", "name": "触发", "trigger": trigger}
    # for（持续条件）/debounce（去抖）属于 on 节点而非 trigger（见 _resolve_trigger 注释），
    # 意图侧写在 when 里，这里提升到节点级，否则会被 Trigger.from_dict 静默丢弃。
    for node_key in ("for", "debounce"):
        if when.get(node_key):
            on_node[node_key] = when[node_key]
    nodes.append(on_node)
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
        # G1（同批修复）：if→ask 的入边也必须是 `then`——执行器对 if 求值 True
        # 返回 `{"then"}`，写 `yes` 同样匹配不上（旧实现 `if_entry` 时写 `yes`）。
        edges.append({"from": prev, "to": ask_id, "kind": "then"})
        prev = ask_id

    # 4. wait 节点
    if wait:
        wait_id = _gen_id("w", counter)
        nodes.append({"id": wait_id, "kind": "wait", "name": "等待", "duration": wait})
        # G1 同族：上游是 ask 时走 yes（应答放行），否则 then（if 求值真/直连）。
        edges.append({
            "from": prev, "to": wait_id,
            "kind": "yes" if _node_kind(prev, nodes) == "ask" else "then",
        })
        prev = wait_id

    # 5. do 节点（支持单动作和多动作；多动作 list 内可混 wait 步骤）
    raw_actions = []
    if do_list and isinstance(do_list, list):
        raw_actions = do_list
    elif do and isinstance(do, list):
        raw_actions = do
    elif do:
        raw_actions = [do]

    # 同上族：动作项形状（`do` 写成字符串、list 里混字符串）——G5 只修了顶层 intent
    # 是 str 的那一档，list 内的 str 项在体验者现场同样崩成 INTERNAL。
    for _i, _item in enumerate(raw_actions):
        if not isinstance(_item, dict):
            raise DraftError(
                "E_INVALID_DO",
                f"动作 #{_i + 1} 必须是对象，收到 {type(_item).__name__}：{_item!r}",
                fix={"action_fields": ["action", "target", "temperature", "hvac_mode", "params",
                                        "requires_confirm", "canary", "conf", "priority"],
                     "known_actions": ["开灯", "关灯", "开空调", "关空调", "turn_on", "turn_off",
                                        "light.turn_on", "light.turn_off", "climate.turn_on",
                                        "climate.turn_off"],
                     "action_format": "裸 <domain>.<service>（如 climate.set_hvac_mode）；⛔ 不带 ha./ha: 前缀——动作映射的兜底分支会原样透传，带前缀会产出坏服务名（体验者现场实测）",
                     "example": {"do": [{"action": "开空调", "target": "书房空调",
                                         "hvac_mode": "cool", "temperature": 26}]}},
            )

    do_ids = []
    # G10 实施（PM 裁决 2026-10-24）：带 canary.duration 的 do 节点，观察期超时恢复时
    # 执行器按 {on_timeout, default} 选边（af_executor resume）。原 af_draft 只给 do 发
    # then/on_error，on_timeout 选边落空 → _terminate，链后半段永不执行。
    # 修法：记下"上一个带 duration 的 do"，等下一个节点 nid 生成后补一条 on_timeout 边指过去；
    # 循环结束后仍挂着的（最后一个 do）指到 pass_id。不改 af_executor。
    pending_canary_do = None
    for i, item in enumerate(raw_actions):
        # G1 修复（实测 2026-10-23）：出边类型按**上游节点种类**分派，两侧口径相反：
        # - 上游是 if（或链上无 ask）：执行器 `_execute` 对 if 求值 True 返回
        #   `{"then"}`（af_executor.py）⇒ 边必须写 `then`；旧实现给「if 后首动作」
        #   写 `yes` ⇒ `pick_edge` 匹配不上 ⇒ 实例静默终止（带 if 的意图自动化
        #   条件成立时永远到不了动作）。
        # - 上游是 ask：应答唤醒走 `resume(kind=verdict)`，边选择集是
        #   `pick_edge({verdict, "default"})`（af_executor.py resume）⇒ 答应
        #   （verdict=yes）时需要 `yes` 边；旧实现写 `then` ⇒ 同样匹配不上 ⇒
        #   ask+do 的图答了 yes 也到不了动作（同族病灶第三处，实测 trace 停在
        #   ask 节点、灯不开、state=done 谎报完成）。
        # - 上游是 wait/do/on：直连 `then`（wait 到点走 `resume_then`）。
        # 参照 graphops.py:126-128 的既有正确口径：if → then/no，ask → yes/no。
        edge_kind = "yes" if _node_kind(prev, nodes) == "ask" else "then"
        is_wait = isinstance(item, dict) and "wait" in item
        if is_wait:
            nid = _gen_id("w", counter)
            nodes.append({"id": nid, "kind": "wait", "name": "等待", "duration": item["wait"]})
            edges.append({"from": prev, "to": nid, "kind": edge_kind})
        else:
            nid = _gen_id("d", counter)
            do_node = _resolve_do(item, resolved, catalog)
            do_node["id"] = nid
            do_node["kind"] = "do"
            do_node["name"] = f"动作{i+1}" if len(raw_actions) > 1 else "动作"
            nodes.append(do_node)
            edges.append({"from": prev, "to": nid, "kind": edge_kind})
            do_ids.append(nid)
        # G10：上一个带 duration 的 do 的 on_timeout 边 → 当前新节点
        if pending_canary_do is not None:
            edges.append({"from": pending_canary_do, "to": nid, "kind": "on_timeout"})
            pending_canary_do = None
        # 当前 do 带 canary.duration → 记下，等下一个节点回填
        if not is_wait and isinstance(do_node.get("canary"), dict) and do_node["canary"].get("duration"):
            pending_canary_do = nid
        prev = nid

    # 6. pass 节点
    pass_id = _gen_id("p", counter)
    nodes.append({"id": pass_id, "kind": "pass", "name": "结束"})

    # G10：最后一个带 duration 的 do，其 on_timeout 指到结束 pass
    if pending_canary_do is not None:
        edges.append({"from": pending_canary_do, "to": pass_id, "kind": "on_timeout"})

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
    """解析触发条件。

    state 触发器有实体；sun/time/event 触发器**没有实体**（sun/time 由时间/太阳几何驱动，
    event 由事件总线驱动）。旧实现对所有类型都调 `_resolve_entity(when.get("entity",""))`，
    无实体时把空名解析成占位，最终 build 报"引用了不存在的实体：?"（2026-10-17 敲打 T07/T08）。
    """
    t_type = when.get("type", "state")

    # 无实体触发器：只透传时间/事件相关字段，不做实体解析。
    if t_type in ("sun", "time", "event"):
        trigger: dict[str, Any] = {"type": t_type}
        for key in ("at", "event", "offset", "before", "after", "tz"):
            if key in when:
                trigger[key] = when[key]
        return trigger

    # group 触发器：递归解析其中的子触发器。
    # 注意输出键必须是 IR 侧的 `sources`（Trigger.from_dict / af_nl_parse 都读 sources），
    # 意图侧入参宽容地接受 triggers/children/sources 三种写法。
    if t_type == "group":
        children = when.get("sources") or when.get("triggers") or when.get("children") or []
        return {
            "type": "group",
            "op": when.get("op", "and"),
            "sources": [_resolve_trigger(c, resolved, catalog) for c in children],
        }

    # state 触发器：必须有实体
    entity_name = when.get("entity", "")
    if not entity_name:
        raise DraftError("E_INVALID_WHEN", f"state 触发器必须提供 entity：{when!r}")
    entity_id = _resolve_entity(entity_name, catalog)
    resolved[entity_name] = entity_id

    trigger = {"type": t_type, "entity_id": entity_id}
    # 注意：`for`（持续条件）/`debounce`（去抖）在 IR 里属于 **on 节点**（node.for_/node.debounce，
    # Trigger dataclass 没有这两个字段，放进 trigger 会被 from_dict 静默丢弃、运行时不生效）。
    # 这里只透传触发源自身的 to/from，for/debounce 由 draft_intent 提升到节点级。
    for key in ("to", "from"):
        if key in when:
            trigger[key] = when[key]
    return trigger


def _resolve_expr(
    expr: dict[str, Any], resolved: dict[str, str], catalog: Any, _depth: int = 0
) -> dict[str, Any]:
    """解析条件表达式，支持 and/or/not 嵌套、比较操作、一元状态谓词。

    支持的意图侧写法：
      - 组合：``{"and": [...]}``、``{"or": [...]}``、``{"not": <expr>}``
      - 比较：``{"lt": {"var": "照度", "const": 200}}``（eq/ne/lt/lte/gt/gte）
      - 一元：``{"is_on": {"var": "书房灯"}}``（is_on/is_off/is_home/is_not_home/
        truthy/not_is_on/not_is_off，**无 const**）；``{"is_on": {"entity": "书房灯"}}``
        同义（`entity` 是 `var` 的起草侧别名，与 when/do 的字段名对齐）

    深度用 `MAX_EXPR_DEPTH` 那份单一真值源，超限抛本模块声明的 DraftError。
    """
    if _depth > MAX_EXPR_DEPTH:
        raise DraftError("E_INVALID_EXPR", f"条件表达式嵌套超过上限 {MAX_EXPR_DEPTH}")

    # 一元状态谓词（is_on/is_off/is_home/is_not_home/truthy/not_is_on/not_is_off）
    from .af_ir.expr import _UNARY_OPS as _UNARY
    for op in _UNARY:
        if op in expr:
            val = expr[op]
            # 体验者 AF-009：意图里 `when`/`do` 用 `{"entity": ...}`，一元谓词却只认
            # `{"var": ...}`——同一份意图两套字段名。这里接受 `entity` 作为别名
            # （语义完全相同：都是"要解析的实体名/ID"），⛔ 不扩 IR 词汇表：
            # 编译器仍只认 `value={"var": ...}`，别名只活在起草入口。
            if not isinstance(val, dict) or ("var" not in val and "entity" not in val):
                raise DraftError(
                    "E_INVALID_EXPR",
                    f"一元条件 `{op}` 需要 {{\"var\": \"实体名\"}}（或 {{\"entity\": \"实体名\"}}），收到 {val!r}",
                )
            var_name = val.get("var") or val.get("entity")
            entity_id = _resolve_entity(var_name, catalog)
            resolved[var_name] = entity_id
            vtype = val.get("type", "string")
            operand = (
                {"var": var_name, "type": vtype}
                if _var_already_namespaced(var_name)
                else {"var": f"entity.{entity_id}", "type": vtype}
            )
            return {"op": op, "value": operand}

    # not：单参数逻辑非
    if "not" in expr:
        inner = expr["not"]
        if isinstance(inner, list):
            if len(inner) != 1:
                raise DraftError("E_INVALID_EXPR", "`not` 只能包一个条件")
            inner = inner[0]
        return {"op": "not", "args": [_resolve_expr(inner, resolved, catalog, _depth + 1)]}

    # and/or 组合
    for op in ("and", "or"):
        if op in expr and isinstance(expr[op], list):
            args = [_resolve_expr(sub, resolved, catalog, _depth + 1) for sub in expr[op]]
            return {"op": op, "args": args}

    # 比较条件：{"lt": {"var": "xxx", "const": 200}}
    for op, val in expr.items():
        if isinstance(val, dict) and "var" in val:
            var_name = val["var"]
            entity_id = _resolve_entity(var_name, catalog)
            resolved[var_name] = entity_id
            vtype = val.get("type", "numeric")
            if _var_already_namespaced(var_name):
                left = {"var": var_name, "type": vtype}
            else:
                left = {"var": f"entity.{entity_id}", "type": vtype}
            if "const" not in val:
                raise DraftError(
                    "E_INVALID_EXPR",
                    f"比较条件 `{op}` 需要 const 右值（一元状态请用 is_on/is_off 等）：{val!r}",
                )
            right = {"const": val["const"]}
            return {"op": op, "left": left, "right": right}
    raise DraftError("E_INVALID_EXPR", f"无法解析条件表达式: {expr}")


def _resolve_do(do: dict[str, Any], resolved: dict[str, str], catalog: Any) -> dict[str, Any]:
    """解析动作节点。"""
    action = do.get("action", "")
    target = do.get("target", "")
    entity_id = _resolve_entity(target, catalog)
    resolved[target] = entity_id

    # 兜底分支会把 action 原样透传成服务名 ⇒ 体验者按 AF-Spec 的习惯写 `ha.`/`ha:`
    # 前缀时，产出 `adapter=ha, action=ha.climate.turn_on` 这种双前缀坏服务名——
    # 静默编译过、运行时才炸。这里把前缀剥掉归一（AF-Spec 侧由 spec 解析自己拆
    # adapter/action，不经此路），剥完再走映射表。
    action_str = action if isinstance(action, str) else ""
    for _pfx in ("ha.", "ha:"):
        if action_str.startswith(_pfx):
            action_str = action_str[len(_pfx):]
            break
    action = action_str

    # 动作映射：开灯/turn_on → light.turn_on
    action_map = {
        "开灯": "light.turn_on",
        "关灯": "light.turn_off",
        "开空调": "climate.turn_on",
        "关空调": "climate.turn_off",
        "turn_on": "light.turn_on",
        "turn_off": "light.turn_off",
        "light.turn_on": "light.turn_on",
        "light.turn_off": "light.turn_off",
        "climate.turn_on": "climate.turn_on",
        "climate.turn_off": "climate.turn_off",
    }
    adapter, action_name = "ha", action_map.get(action, action)
    # 体验者 AF-014（2026-10-29 复验）：旧别名表是**域盲**的——`开灯` 配 `switch.abc`
    # 编出 `light.turn_on` + `entity_id=switch.abc`，真机 HA 直接拒（实体不属于该域），
    # 而仿真器从不比对服务域与实体域、照样把状态翻过去 ⇒ 预演绿、真机死（静默错执行）。
    # 修法：别名只提供缺省域，**真实域跟着目标实体走**——(目标域, 服务) 在效果真值表里
    # 存在才改判（`switch.turn_on` 有 ⇒ 用它；`vacuum.turn_on` 没有 ⇒ 保留缺省域，
    # 让 AF-005 那一站去报"服务不存在"，⛔ 不在这里悄悄换个域把问题挪走）。
    # 只有**别名**（中文动作名 / 裸 turn_on / turn_off）才跟目标域走；显式写
    # `light.turn_on` 是作者的明确决定，⛔ 不"好心"改域（判据 test_explicit_domain_action_not_rewritten 钉住）。
    _ALIAS_KEYS = ("开灯", "关灯", "开空调", "关空调", "turn_on", "turn_off")
    if action in _ALIAS_KEYS and "." in action_name:
        _dom, _svc = action_name.split(".", 1)
        target_domain = entity_id.split(".", 1)[0] if "." in entity_id else ""
        from .af_vhass.fake import SERVICE_STATE  # 效果真值表单一真源，不抄第二份
        if target_domain and target_domain != _dom and (target_domain, _svc) in SERVICE_STATE:
            action_name = f"{target_domain}.{_svc}"

    params = {"entity_id": entity_id}
    if "temperature" in do:
        params["temperature"] = do["temperature"]
    if "hvac_mode" in do:
        params["hvac_mode"] = do["hvac_mode"]
    # 泛用参数透传：显式 params dict 最后合并（同名以意图侧为准）。要 preset_mode
    # 这类字段不必等动作映射扩表；⛔ 非 dict 拒——字符串 params 会一路带到 HA
    # 调用面变 INTERNAL，与上面几族形状闸同一个道理。
    extra = do.get("params")
    if extra is not None:
        if not isinstance(extra, dict):
            raise DraftError(
                "E_INVALID_DO",
                f"动作的 `params` 必须是对象，收到 {type(extra).__name__}：{extra!r}",
                fix={"example": {"action": "climate.set_hvac_mode", "target": "书房空调",
                                 "params": {"hvac_mode": "cool"}}},
            )
        params.update(extra)

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


def stage_group(group_auto: Any, summary: str | None = None) -> str:
    """F9/v2.3：将 group 复合 Automation 暂存为可被 apply(ref) 路由的 IR。

    group 以单自动化 Graph（mode='group'）形式进入既有 staging 管线，apply(ref)
    检测到 group 后短路到 apply_group 原子部署。零模型改动。
    """
    raw = group_auto.raw if hasattr(group_auto, "raw") else group_auto
    graph = load_graph({"automations": [raw]})
    return _staging.put(graph, summary or f"组合：{raw.get('name', '')}", {})
