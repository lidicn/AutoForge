"""F14 P2 — NL→IR 正向编译器（受限文法，零 LLM）。

设计依据 v2.5/F14 §3.2 / §4 / §6：只覆盖 ``KNOWN_ACTIONS`` 已登记的 domain.service 组合，
**不做开放式 NL**（§6 零 LLM 红线 / §7 风险缓解：解析规则爆炸 → 只做受限域）。
解析规则与渲染层 ``af_nl._ACTION_VERBS`` 的中文动词严格对称（反转 ``_ACTION_VERBS`` 得到
"中文动词 + 实体域 → domain.service"），产出的 IR 必须通过 ``af_ir.load_automation``，
并使 ``af_fidelity.verify_roundtrip``（P1）判为保真。

受限文法（句子 = 触发片段 +（可选）条件片段 + 动作片段，片段间用逗号/顿号/空格分隔）：
  触发（trigger）：
    状态  当 <entity_id> 变为 <state> [时]      或   <entity_id> 变成 <state> 时
    定时  每天 HH:MM / 每天 HH点MM分 / HH:MM 时
    太阳  日落时 / 日出时
  条件（if，可选，多个子句须用同一种连接词 且/或 串联）：
    如果 <entity_id> 为 <state> [且|或 <entity_id> 为 <state>]...
    其中 <state> 为 on/开 → is_on，off/关 → is_off，其余 → 等于该字符串（eq 常量）。
  动作（do）：
    <中文动词> <entity_id>   —— 由 entity_id 的域在 _ACTION_VERBS 反转表里消歧到 domain.service
    <中文动词>               —— 仅当该动词唯一映射到 EXEMPT_DOMAINS（如 notify）时可省略实体
  询问（ask，P3 跨层复用）：
    确认 "<prompt>"                      —— choice(是/否)
    询问 "<prompt>" [选项 A / B / C]      —— 有选项→choice，无选项→text（产 af_ir.models.AskSpec）

文法边界（明确不支持，遇到即抛 ValueError，绝不猜测、绝不产出非法 IR）：
  - 模糊语义解析："晚上 8 点后"（时间窗）、"客厅灯"（房间名→实体）等 §4 理想示例里的
    开放式表达，需要语义推断，超出零-LLM 受限域 → 不支持。§4 的**结构**（触发→条件→动作）
    由上述文法覆盖，用规范化的 entity_id 书写。
  - 混合连接词（同一条件里同时出现 且 与 或）：优先级需语义判定 → 不支持。
  - 一条文本里的多个动作 / 多个触发源：仅产出 on → [if] → do → pass 单链。

实体 id 须为 HA 规范形态 ``domain.object_id``（小写域 + 点 + 对象名），与 IR schema 一致。
"""
from __future__ import annotations

import re
from typing import Any

from .af_actions import KNOWN_ACTIONS, EXEMPT_DOMAINS, known_services_for
from .af_ir import AskSpec
from .af_nl import _ACTION_VERBS

__all__ = ["build_ir_from_nl"]

# HA 实体 id：小写域.对象名（与 IR 触发/参数中的 entity_id 一致）
_ENTITY = r"[a-z][a-z0-9_]*\.[a-zA-Z0-9_]+"
_CN = r"[一-鿿]"

# 反转 _ACTION_VERBS：中文动词 → {域: 服务}，仅保留 KNOWN_ACTIONS 已登记的 (域, 服务) 对，
# 使正向编译器绝不产出真源之外的动作（§6：只做 KNOWN_ACTIONS 覆盖的域-服务组合）。
_VERB_DOMAIN: dict[str, dict[str, str]] = {}
for _action, _verb in _ACTION_VERBS.items():
    _domain, _, _service = _action.partition(".")
    if (_domain, _service) in KNOWN_ACTIONS:
        _VERB_DOMAIN.setdefault(_verb, {})[_domain] = _service


def build_ir_from_nl(text: str) -> dict[str, Any]:
    """受限文法 NL → 合法 IR dict（零 LLM）。不匹配文法即抛 ValueError。"""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("build_ir_from_nl 需要非空字符串输入")

    rest = _normalize(text)
    trigger, rest = _parse_trigger(rest)
    rest = _trim_seps(rest)

    condition = None
    if rest.startswith("如果"):
        condition, rest = _parse_condition(rest)
        rest = _trim_seps(rest)

    # P3：终止片段可为 ask（询问/确认，产 AskSpec）或 do（动作）
    if rest.startswith("询问") or rest.startswith("确认"):
        terminal, rest = _parse_ask(rest)
    else:
        terminal, rest = _parse_action(rest)
    rest = rest.strip()
    if rest:
        raise ValueError(f"存在无法解析的多余文本：{rest!r}")

    return _assemble(trigger, condition, terminal)


# ─────────────────────────────────────────────────────────────────────
# 片段解析
# ─────────────────────────────────────────────────────────────────────


def _normalize(text: str) -> str:
    s = re.sub(r"\s+", " ", text).strip()
    return s.rstrip("。.！!？?").strip()


def _trim_seps(rest: str) -> str:
    return re.sub(r"^[，,、\s]+", "", rest)


def _fmt_time(hour: str, minute: str) -> str:
    h, m = int(hour), int(minute)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"非法时间 {hour}:{minute}")
    return f"{h:02d}:{m:02d}"


def _parse_trigger(rest: str) -> tuple[dict[str, Any], str]:
    m = re.match(r"^(日落|日出)时", rest)
    if m:
        event = "sunset" if m.group(1) == "日落" else "sunrise"
        return {"type": "sun", "event": event}, rest[m.end():]

    m = re.match(rf"^每天\s*(\d{{1,2}}):(\d{{2}})", rest)
    if m:
        return {"type": "time", "at": _fmt_time(m.group(1), m.group(2))}, rest[m.end():]

    m = re.match(r"^每天\s*(\d{1,2})点(\d{1,2})分?", rest)
    if m:
        return {"type": "time", "at": _fmt_time(m.group(1), m.group(2))}, rest[m.end():]

    m = re.match(r"^(\d{1,2}):(\d{2})\s*时", rest)
    if m:
        return {"type": "time", "at": _fmt_time(m.group(1), m.group(2))}, rest[m.end():]

    m = re.match(rf"^当\s*({_ENTITY})\s*变为\s*([^\s，,、时]+)\s*时?", rest)
    if m:
        return {"type": "state", "entity_id": m.group(1), "to": m.group(2)}, rest[m.end():]

    m = re.match(rf"^({_ENTITY})\s*变成\s*([^\s，,、时]+)\s*时", rest)
    if m:
        return {"type": "state", "entity_id": m.group(1), "to": m.group(2)}, rest[m.end():]

    raise ValueError(f"无法识别触发片段：{rest!r}")


def _parse_condition(rest: str) -> tuple[dict[str, Any], str]:
    rest = re.sub(r"^如果\s*", "", rest)
    clauses: list[dict[str, Any]] = []
    connectors: list[str] = []
    while True:
        m = re.match(rf"^({_ENTITY})\s*为\s*([^\s，,、且或]+)", rest)
        if not m:
            raise ValueError(f"无法解析条件子句：{rest!r}")
        clauses.append(_clause_expr(m.group(1), m.group(2)))
        rest = rest[m.end():]
        k = re.match(r"^\s*(且|或)\s*", rest)
        if not k:
            break
        connectors.append(k.group(1))
        rest = rest[k.end():]

    if len(set(connectors)) > 1:
        raise ValueError("条件不支持混合连接词（且/或 混用需语义判定优先级）")
    if len(clauses) == 1:
        return clauses[0], rest
    op = "and" if connectors[0] == "且" else "or"
    return {"op": op, "args": clauses}, rest


def _clause_expr(entity_id: str, state: str) -> dict[str, Any]:
    """一个「<entity> 为 <state>」子句 → is_on/is_off/eq。"""
    value = {"var": f"entity.{entity_id}"}
    if state in ("on", "开"):
        return {"op": "is_on", "value": value}
    if state in ("off", "关"):
        return {"op": "is_off", "value": value}
    return {"op": "eq", "left": value, "right": {"const": state}}


def _parse_action(rest: str) -> tuple[dict[str, Any], str]:
    if not rest:
        raise ValueError("缺少动作片段")

    m = re.match(rf"^({_CN}+)\s*({_ENTITY})", rest)
    if m:
        verb, entity_id = m.group(1), m.group(2)
        domain = entity_id.split(".", 1)[0]
        service = _VERB_DOMAIN.get(verb, {}).get(domain)
        if service is None or service not in known_services_for(domain):
            raise ValueError(f"动作「{verb}」与实体域「{domain}」不构成已知 domain.service 组合")
        do_node = {
            "id": "n_do",
            "kind": "do",
            "adapter": "ha",
            "action": f"{domain}.{service}",
            "params": {"entity_id": entity_id},
        }
        return do_node, rest[m.end():]

    # 裸动词（无实体）：仅当唯一映射到 EXEMPT_DOMAINS（副作用域，如 notify）时可接受
    m = re.fullmatch(rf"({_CN}+)", rest)
    if m:
        candidates = _VERB_DOMAIN.get(m.group(1), {})
        if len(candidates) == 1:
            (domain, service), = candidates.items()
            if domain in EXEMPT_DOMAINS:
                return {"id": "n_do", "kind": "do", "adapter": "ha",
                        "action": f"{domain}.{service}", "params": {}}, ""

    raise ValueError(f"无法解析动作片段：{rest!r}")


# ─────────────────────────────────────────────────────────────────────
# 图装配：on → [if] → do → pass，确定性 id，then 边连通
# ─────────────────────────────────────────────────────────────────────


def _parse_ask(rest: str) -> tuple[dict[str, Any], str]:
    """P3：`确认 "<prompt>"` / `询问 "<prompt>" [选项 A/B/C]` → ask 节点（产 AskSpec）。

    控件映射与渲染层共用**同一个** `af_ir.models.AskSpec` 类（跨层单对象，非复制）：
    - 确认 → choice(是/否)（对应 input_boolean 型 yes/no 控件）；
    - 询问 + 选项 → choice(给定选项)；
    - 询问 无选项 → text（自由输入）。
    """
    rest = rest.strip()
    head = "确认" if rest.startswith("确认") else "询问"
    body = rest[len(head):].strip().lstrip("：:").strip()
    options: tuple[str, ...] = ()
    if " 选项 " in body:
        prompt_part, _, opt_part = body.partition(" 选项 ")
        options = tuple(o.strip() for o in opt_part.split("/") if o.strip())
        body = prompt_part.strip()
    prompt = body.strip("“”\"' ")
    if not prompt:
        raise ValueError("询问/确认缺少提示文本")
    if head == "确认":
        spec = AskSpec(kind="choice", options=("是", "否"), prompt=prompt)
    elif len(options) >= 2:
        spec = AskSpec(kind="choice", options=options, prompt=prompt)
    else:
        spec = AskSpec(kind="text", prompt=prompt)
    return {"id": "n_ask", "kind": "ask", "prompt": prompt, "ask": spec.to_dict()}, ""


def _assemble(trigger: dict[str, Any], condition: dict[str, Any] | None,
              terminal: dict[str, Any]) -> dict[str, Any]:
    tid = terminal["id"]
    nodes: list[dict[str, Any]] = [{"id": "n_on", "kind": "on", "trigger": trigger}]
    edges: list[dict[str, str]] = []
    prev = "n_on"
    if condition is not None:
        nodes.append({"id": "n_if", "kind": "if", "expr": condition})
        edges.append({"from": prev, "to": "n_if", "kind": "then"})
        prev = "n_if"
    nodes.append(terminal)
    edges.append({"from": prev, "to": tid, "kind": "then"})
    nodes.append({"id": "n_pass", "kind": "pass"})
    edges.append({"from": tid, "to": "n_pass", "kind": "then"})

    return {
        "ir_version": "0.2.1",
        "id": "nl_built",
        "name": "NL 构建的自动化",
        "version": 1,
        "mode": "single",
        "nodes": nodes,
        "edges": edges,
    }
