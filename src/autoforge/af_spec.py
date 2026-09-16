"""G7 —— AF-Spec：面向 Agent 的文本语法，编译到**同一份 JSON IR**。

铁律（`IR_AND_RUNTIME` §1）：**Spec 是序列化格式，Graph 才是模型**。
AF-Spec 只是 JSON IR 的**投影**，`compile_spec()` 产出的仍是 JSON IR（交给 `load_graph`
按同一份 Schema 校验），绝不允许把 AF-Spec 当 IR 直接跑。

语法（行式，一行一个节点/边；结构化子对象用内联 JSON）：

```
automation study_day_light
name "书房白天人来补光"
ir_version "0.2.1"
version 1
mode restart
confidence 0.9
snapshot true
meta {"acceptance": "G1 用例 1"}
var flag {"type": "boolean", "value": false}
expect {"entity_id": "light.study_main", "state": "on"}

on a1 {"type": "state", "entity_id": "binary_sensor.study_motion", "to": "on"} name "书房检测到人"
if i1 {"op": "lt", "left": {"var": "entity.sensor.study_illum", "type": "numeric"}, "right": {"const": 200}}
do d1 ha.light.turn_on {"entity_id": "light.study_main"} result turn_on_result
pass p1 name "结束"

edge a1 -> i1 then
edge i1 -> p1 no
edge d1 -> p1 on_error id e2 label "失败兜底"
```

设计要点：
- **有损为零**：渲染器只写出原始 IR 里**出现过**的字段；解析器只在对应 token 出现时才填该字段。
  因此 `compile_spec(render_spec(g))` 与 `g` 语义（甚至 raw 结构）一致。
- 覆盖全部 7 种节点；`reserved`（仅剩 `fn` 保留位）节点拒绝渲染（属未实现，不该出现在可跑图里）。
- v0.3.0 起支持 `emit` 节点字段（跨自动化事件·发布侧）的无损往返。
- v1.2.0 起支持顶层 `expect` 后置条件断言的无损往返（一行一条，可重复）。
"""

from __future__ import annotations

import json
from typing import Any

from .af_ir import IR_VERSION, Graph, load_graph

__all__ = ["SpecError", "compile_spec", "render_spec", "graph_to_raw"]

_NODE_KINDS = ("on", "if", "do", "ask", "wait", "set", "pass")


class SpecError(Exception):
    """AF-Spec 语法错误。"""


# ─────────────────────────────────────────────────────────────────────
# 分词
# ─────────────────────────────────────────────────────────────────────


def _tokenize(line: str) -> list[str]:
    """把一行切成 token：JSON 对象/数组 / JSON 字符串 / 裸词。"""
    tokens: list[str] = []
    i, n = 0, len(line)
    while i < n:
        ch = line[i]
        if ch.isspace():
            i += 1
            continue
        if ch in "{[":
            depth = 0
            start = i
            while i < n:
                c = line[i]
                if c == '"':
                    i += 1
                    while i < n and line[i] != '"':
                        if line[i] == "\\":
                            i += 1
                        i += 1
                    i += 1
                    continue
                if c in "{[":
                    depth += 1
                elif c in "}]":
                    depth -= 1
                i += 1
                if depth == 0:
                    break
            tokens.append(line[start:i])
            continue
        if ch == '"':
            start = i
            i += 1
            while i < n and line[i] != '"':
                if line[i] == "\\":
                    i += 1
                i += 1
            i += 1
            tokens.append(line[start:i])
            continue
        start = i
        while i < n and not line[i].isspace():
            i += 1
        tokens.append(line[start:i])
    return tokens


def _json(token: str) -> Any:
    try:
        return json.loads(token)
    except (ValueError, TypeError) as exc:
        raise SpecError(f"期望 JSON，实际是 {token!r}") from exc


def _str(token: str) -> str:
    """取字符串：JSON 字符串按 JSON 解析，裸词原样返回。"""
    if token.startswith('"'):
        return str(_json(token))
    return token


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))


# ─────────────────────────────────────────────────────────────────────
# 编译（AF-Spec → JSON IR → Graph）
# ─────────────────────────────────────────────────────────────────────


def compile_spec(text: str) -> Graph:
    """把 AF-Spec 文本编译成 Graph（经 JSON IR + Schema 校验）。"""
    automations: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        tokens = _tokenize(line)
        keyword = tokens[0]

        if keyword == "automation":
            if len(tokens) < 2:
                raise SpecError(f"第 {lineno} 行：automation 缺 id")
            # version / mode 是 Schema 必填；未显式给出时补默认，后续 option 行可覆盖。
            current = {
                "id": tokens[1],
                "ir_version": IR_VERSION,
                "version": 1,
                "mode": "single",
                "nodes": [],
                "edges": [],
            }
            automations.append(current)
            continue

        if current is None:
            raise SpecError(f"第 {lineno} 行：{keyword!r} 出现在任何 automation 之前")

        if keyword in _NODE_KINDS:
            current["nodes"].append(_parse_node(keyword, tokens, lineno))
        elif keyword == "edge":
            current["edges"].append(_parse_edge(tokens, lineno))
        else:
            _parse_option(current, keyword, tokens, lineno)

    if not automations:
        raise SpecError("AF-Spec 为空：至少需要一条 automation")
    return load_graph({"automations": automations})


def _parse_node(kind: str, tokens: list[str], lineno: int) -> dict[str, Any]:
    if len(tokens) < 2:
        raise SpecError(f"第 {lineno} 行：{kind} 节点缺 id")
    node_id = tokens[1]
    data: dict[str, Any] = {"id": node_id, "kind": kind}
    i = 2

    def take(expect: str) -> str:
        nonlocal i
        if i >= len(tokens):
            raise SpecError(f"第 {lineno} 行：{kind} 节点期望 {expect}，但已到行尾")
        token = tokens[i]
        i += 1
        return token

    def maybe_name() -> None:
        """尾部通用 token：v0.3.0 起除 `name` 外还支持 `emit`（节点字段，各 kind 通用）。"""
        nonlocal i
        while i < len(tokens):
            tok = tokens[i]
            if tok == "name":
                i += 1
                data["name"] = _str(take("name 值"))
            elif tok == "emit":
                i += 1
                data["emit"] = _json(take("emit"))
            else:
                break

    if kind == "on":
        data["trigger"] = _json(take("trigger"))
        while i < len(tokens):
            tok = tokens[i]
            if tok == "for":
                i += 1
                data["for"] = _str(take("for 时长"))
            elif tok == "debounce":
                i += 1
                data["debounce"] = _str(take("debounce 时长"))
            elif tok == "emit":  # v0.3.0 跨自动化事件·发布侧
                i += 1
                data["emit"] = _json(take("emit"))
            elif tok == "name":
                i += 1
                data["name"] = _str(take("name 值"))
            else:
                raise SpecError(f"第 {lineno} 行：on 节点多余 token {tok!r}")
    elif kind == "if":
        data["expr"] = _json(take("expr"))
        maybe_name()
    elif kind == "do":
        adapter_action = take("adapter.action")
        data["adapter"], _, data["action"] = adapter_action.partition(".")
        if i < len(tokens) and tokens[i].startswith("{"):
            data["params"] = _json(take("params"))
        while i < len(tokens):
            tok = tokens[i]
            if tok == "atomic":
                i += 1
                data["atomic"] = True
            elif tok in ("confirm", "requires_confirm"):
                i += 1
                data["requires_confirm"] = True
            elif tok == "canary":
                i += 1
                data["canary"] = _json(take("canary"))
            elif tok == "result":
                i += 1
                data["result_var"] = _str(take("result 变量名"))
            elif tok == "emit":  # v0.3.0 跨自动化事件·发布侧
                i += 1
                data["emit"] = _json(take("emit"))
            elif tok == "name":
                i += 1
                data["name"] = _str(take("name 值"))
            else:
                raise SpecError(f"第 {lineno} 行：do 节点多余 token {tok!r}")
    elif kind == "ask":
        data["prompt"] = _str(take("prompt"))
        while i < len(tokens):
            tok = tokens[i]
            if tok in ("room", "timeout", "session"):
                i += 1
                data[tok] = _str(take(f"{tok} 值"))
            elif tok == "emit":  # v0.3.0 跨自动化事件·发布侧
                i += 1
                data["emit"] = _json(take("emit"))
            elif tok == "name":
                i += 1
                data["name"] = _str(take("name 值"))
            else:
                raise SpecError(f"第 {lineno} 行：ask 节点多余 token {tok!r}")
    elif kind == "wait":
        data["duration"] = _str(take("duration"))
        maybe_name()
    elif kind == "set":
        if take("var") != "var":
            raise SpecError(f"第 {lineno} 行：set 节点语法应为 `set <id> var <名> value|from <值>`")
        data["var"] = _str(take("变量名"))
        which = take("value|from")
        if which == "value":
            data["value"] = _json(take("value"))
        elif which == "from":
            data["from"] = _str(take("from"))
        else:
            raise SpecError(f"第 {lineno} 行：set 节点期望 value 或 from，实际 {which!r}")
        maybe_name()
    elif kind == "pass":
        maybe_name()

    return data


def _parse_edge(tokens: list[str], lineno: int) -> dict[str, Any]:
    if len(tokens) < 5:
        raise SpecError(f"第 {lineno} 行：edge 语法应为 `edge <from> -> <to> <kind>`")
    if tokens[2] != "->":
        raise SpecError(f"第 {lineno} 行：edge 缺少 `->`")
    data: dict[str, Any] = {"from": tokens[1], "to": tokens[3], "kind": tokens[4]}
    i = 5
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("id", "label"):
            i += 1
            if i >= len(tokens):
                raise SpecError(f"第 {lineno} 行：edge {tok} 缺值")
            data[tok] = _str(tokens[i])
            i += 1
        else:
            raise SpecError(f"第 {lineno} 行：edge 多余 token {tok!r}")
    return data


def _parse_option(current: dict[str, Any], keyword: str, tokens: list[str], lineno: int) -> None:
    def value() -> str:
        if len(tokens) < 2:
            raise SpecError(f"第 {lineno} 行：{keyword} 缺值")
        return tokens[1]

    if keyword == "name":
        current["name"] = _str(value())
    elif keyword == "ir_version":
        current["ir_version"] = _str(value())
    elif keyword == "version":
        current["version"] = int(_json(value()))
    elif keyword == "mode":
        current["mode"] = _str(value())
    elif keyword == "confidence":
        current["confidence"] = float(_json(value()))
    elif keyword == "snapshot":
        current["snapshot"] = bool(_json(value()))
    elif keyword == "persist":
        current["persist"] = bool(_json(value()))
    elif keyword == "meta":
        current["meta"] = _json(value())
    elif keyword == "var":
        if len(tokens) < 3:
            raise SpecError(f"第 {lineno} 行：var 语法应为 `var <名> <声明 JSON>`")
        current.setdefault("vars", {})[_str(tokens[1])] = _json(tokens[2])
    elif keyword == "expect":
        # v1.2.0：后置条件断言，一条一行（可重复出现，按出现顺序保留）
        if len(tokens) < 2:
            raise SpecError(f"第 {lineno} 行：expect 语法应为 `expect <断言 JSON>`")
        current.setdefault("expect", []).append(_json(tokens[1]))
    else:
        raise SpecError(f"第 {lineno} 行：未知关键字 {keyword!r}")


# ─────────────────────────────────────────────────────────────────────
# 渲染（Graph → AF-Spec）
# ─────────────────────────────────────────────────────────────────────


def render_spec(graph: Graph) -> str:
    """把 Graph 渲染成 AF-Spec 文本（与 `compile_spec` 互逆）。"""
    blocks = [_render_automation(auto) for auto in graph]
    return "\n\n".join(blocks) + "\n"


def _render_automation(auto: Any) -> str:
    raw = dict(auto.raw)
    lines = [f"automation {auto.id}", f"name {_dumps(raw.get('name', auto.name))}"]
    if "ir_version" in raw:
        lines.append(f"ir_version {_dumps(raw['ir_version'])}")
    for key in ("version", "mode", "confidence", "snapshot", "persist"):
        if key in raw:
            lines.append(f"{key} {_dumps(raw[key])}")
    if "meta" in raw:
        lines.append(f"meta {_dumps(raw['meta'])}")
    for name, decl in (raw.get("vars") or {}).items():
        lines.append(f"var {_dumps(name)} {_dumps(decl)}")
    # v1.2.0 后置条件断言：一行一条（结构化对象用内联 JSON，与 var/meta 同一风格）
    for item in raw.get("expect") or ():
        lines.append(f"expect {_dumps(item)}")
    lines.append("")
    for node in auto.nodes.values():
        lines.append(_render_node(node))
    lines.append("")
    for edge in auto.edges:
        lines.append(_render_edge(edge))
    return "\n".join(lines)


def _render_node(node: Any) -> str:
    if node.reserved:
        raise SpecError(f"节点 {node.id} 使用未实现保留字段 {sorted(node.reserved)}，AF-Spec 拒绝渲染")
    raw = node.raw
    parts: list[str] = [node.kind, node.id]

    if node.kind == "on":
        parts.append(_dumps(raw["trigger"]))
        if "for" in raw:
            parts += ["for", _dumps(raw["for"])]
        if "debounce" in raw:
            parts += ["debounce", _dumps(raw["debounce"])]
    elif node.kind == "if":
        parts.append(_dumps(raw["expr"]))
    elif node.kind == "do":
        parts.append(f"{raw['adapter']}.{raw['action']}")
        if "params" in raw:
            parts.append(_dumps(raw["params"]))
        if raw.get("atomic"):
            parts.append("atomic")
        if raw.get("requires_confirm"):
            parts.append("confirm")
        if "canary" in raw:
            parts += ["canary", _dumps(raw["canary"])]
        if "result_var" in raw:
            parts += ["result", _dumps(raw["result_var"])]
    elif node.kind == "ask":
        parts.append(_dumps(raw.get("prompt", "")))
        for key in ("room", "timeout", "session"):
            if key in raw:
                parts += [key, _dumps(raw[key])]
    elif node.kind == "wait":
        parts.append(_dumps(raw["duration"]))
    elif node.kind == "set":
        parts += ["var", _dumps(raw["var"])]
        if "from" in raw:
            parts += ["from", _dumps(raw["from"])]
        elif "value" in raw:
            parts += ["value", _dumps(raw["value"])]
    elif node.kind == "pass":
        pass
    else:  # pragma: no cover - 7 种节点已全覆盖
        raise SpecError(f"未知节点类型：{node.kind}")

    # v0.3.0：`emit` 是节点字段，各 kind 通用
    if node.emit is not None:
        parts += ["emit", _dumps(raw["emit"])]

    if "name" in raw:
        parts += ["name", _dumps(raw["name"])]
    return " ".join(parts)


def _render_edge(edge: Any) -> str:
    parts = ["edge", edge.from_, "->", edge.to, edge.kind]
    if edge.id:
        parts += ["id", _dumps(edge.id)]
    if edge.label:
        parts += ["label", _dumps(edge.label)]
    return " ".join(parts)


# ─────────────────────────────────────────────────────────────────────
# 调试：Graph → 原始 JSON（往返比对用）
# ─────────────────────────────────────────────────────────────────────


def graph_to_raw(graph: Graph) -> list[dict[str, Any]]:
    """把 Graph 还原成 IR 原始 dict 列表（含 nodes/edges 的原始结构）。"""
    out: list[dict[str, Any]] = []
    for auto in graph:
        record = {k: v for k, v in auto.raw.items() if k not in ("nodes", "edges")}
        record["nodes"] = [dict(node.raw) for node in auto.nodes.values()]
        record["edges"] = [_edge_to_raw(edge) for edge in auto.edges]
        out.append(record)
    return out


def _edge_to_raw(edge: Any) -> dict[str, Any]:
    record: dict[str, Any] = {"from": edge.from_, "to": edge.to, "kind": edge.kind}
    if edge.id:
        record["id"] = edge.id
    if edge.label:
        record["label"] = edge.label
    return record
