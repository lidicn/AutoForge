# -*- coding: utf-8 -*-
"""deepfix — LLM 深度修复。LLM 只输出『操作序列』，不输出整段 IR；落地前过两道闸：
   1) 操作白名单 + 安全字段保护（I4）；2) lint 问题数必须严格下降（I5）。
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .graphops import EDGE_WHENS, add_edge, nodes_of
from .runtime import load_module

DEEP_FIX_SYSTEM = """你是 AutoForge 的 IR 修复器，只做结构修复，不做设备猜测。
输入是 AF IR(JSON) 与问题列表。只输出一个 JSON 数组（不要解释），元素是修复操作，最多 {max_ops} 个。
允许的 op（白名单，其他一律非法）：
{{"op":"set","path":["nodes",0,"trigger","to"],"value":"on"}}
{{"op":"add_edge","from":"q1","to":"d1","when":"yes","label":null}}
{{"op":"remove_edge","from":"a1","to":"a1","when":"then"}}
{{"op":"remove_node","id":"d2"}}
{{"op":"add_node","node":{{"id":"d2","kind":"do","action":"ha.light.turn_on","params":{{"entity_id":"@lamp"}},"name":"开灯"}}}}
硬规则：
1) 禁止猜测或写入真实 entity_id，实体占位保持 @ref；
2) 禁止修改/删除 canary、requires_confirm、risk 等安全字段，禁止任何 approve 相关操作；
3) 禁止修改根 id / ir_version / version；
4) when 只能取 then|no|yes|default|on_timeout|on_cancel|on_error。
"""

ALLOWED_OPS = {"set", "add_edge", "remove_edge", "remove_node", "add_node"}
NODE_KINDS = {"on", "if", "do", "ask", "wait", "set", "pass"}
ROOT_FROZEN = {"id", "ir_version", "version"}
_GUARD_TOKENS = ("canary", "requires_confirm", "risk", "approve", "approval", "auto_commit", "pending_id")


@dataclass
class DeepFixResult:
    ir: dict | None
    outcome: Any
    detail: str
    ops: list = field(default_factory=list)


def extract_json(text: str):
    if not text:
        return None
    text = text.strip()
    for candidate in (text,):
        try:
            return json.loads(candidate)
        except Exception:
            pass
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _outcome(applied, description, touched_ir=True, needs_user=False):
    A = load_module()
    return A.FixOutcome(applied=applied, description=description,
                        touched_ir=touched_ir, needs_user=needs_user)


def _parent_exists(root, path) -> bool:
    cur = root
    for p in list(path)[:-1]:
        try:
            cur = cur[p]
        except Exception:
            return False
    return True


def validate_ops(ir, ops, *, max_ops: int = 8) -> list:
    """返回错误列表；空列表 = 合法。安全字段只许调严，不许调松。"""
    if not isinstance(ops, list) or not ops:
        return ["输出必须是非空 JSON 数组"]
    errs = []
    if len(ops) > max_ops:
        errs.append(f"操作数超过上限 {max_ops}")
    planned = {str(n.get("id")) for n in nodes_of(ir)}
    on_count = sum(1 for n in nodes_of(ir) if n.get("kind") == "on")
    for i, op in enumerate(ops):
        tag = f"op[{i}]"
        if not isinstance(op, Mapping):
            errs.append(f"{tag} 不是对象")
            continue
        if "approve" in json.dumps(op, ensure_ascii=False).lower():
            errs.append(f"{tag} 含 approve 相关内容，已拒绝")
        name = str(op.get("op") or "")
        if name not in ALLOWED_OPS:
            errs.append(f"{tag} 非法操作 {name}")
            continue
        if name == "set":
            path = op.get("path")
            if not isinstance(path, list) or not path:
                errs.append(f"{tag} path 非法")
                continue
            toks = [str(p) for p in path]
            for t in toks:
                low = t.lower()
                if any(tok in low for tok in _GUARD_TOKENS):
                    errs.append(f"{tag} 触碰受保护字段 {t}")
            if toks[0] in ROOT_FROZEN:
                errs.append(f"{tag} 禁止修改根字段 {toks[0]}")
            if not _parent_exists(ir, toks):
                errs.append(f"{tag} path 不存在")
        elif name == "add_node":
            node = op.get("node")
            if not isinstance(node, Mapping):
                errs.append(f"{tag} node 非法")
                continue
            nid = str(node.get("id") or "")
            if not re.match(r"^[a-z]{1,2}\d{1,3}$", nid) or nid in planned:
                errs.append(f"{tag} 节点 id 非法或重复: {nid}")
            if str(node.get("kind")) not in NODE_KINDS:
                errs.append(f"{tag} 节点 kind 非法")
            if node.get("requires_confirm") is False or "canary" in node or "risk" in node:
                errs.append(f"{tag} 禁止写入/放松安全字段")
            planned.add(nid)
        elif name == "remove_node":
            nid = str(op.get("id") or "")
            if nid not in planned:
                errs.append(f"{tag} 节点不存在: {nid}")
                continue
            if on_count and not any(str(n.get("id")) != nid and n.get("kind") == "on"
                                    for n in nodes_of(ir)) and \
               any(str(n.get("id")) == nid and n.get("kind") == "on" for n in nodes_of(ir)):
                errs.append(f"{tag} 不能删除唯一的触发节点")
            planned.discard(nid)
        elif name in ("add_edge", "remove_edge"):
            frm, to, when = op.get("from"), op.get("to"), op.get("when")
            if str(frm) not in planned or str(to) not in planned:
                errs.append(f"{tag} 边端点不存在")
            if when not in EDGE_WHENS:
                errs.append(f"{tag} 非法 when: {when}")
            if frm == to:
                errs.append(f"{tag} 不允许自环")
    return errs


def apply_ops(ir, ops) -> dict:
    A = load_module()
    out = copy.deepcopy(dict(ir))
    for op in ops:
        name = op.get("op")
        if name == "set":
            A.set_path(out, tuple(op.get("path") or ()), op.get("value"))
        elif name == "add_node":
            out.setdefault("nodes", []).append(copy.deepcopy(op["node"]))
        elif name == "remove_node":
            nid = op.get("id")
            out["nodes"] = [n for n in out.get("nodes") or [] if n.get("id") != nid]
            out["edges"] = [e for e in out.get("edges") or []
                            if e.get("from") != nid and e.get("to") != nid]
        elif name == "add_edge":
            add_edge(out, op.get("from"), op.get("to"), op.get("when"), op.get("label"))
        elif name == "remove_edge":
            keep = [e for e in out.get("edges") or []
                    if not (e.get("from") == op.get("from") and e.get("to") == op.get("to")
                            and e.get("when") == op.get("when"))]
            out["edges"] = keep
    return out


class DeepFixer:
    def __init__(self, llm, *, lint, max_ops: int = 8):
        self.llm = llm
        self.lint = lint
        self.max_ops = max_ops

    def repair(self, ir, issues: Sequence) -> DeepFixResult:
        from . import detectors
        lint = self.lint or detectors.lint
        try:
            payload = json.dumps(
                {"ir": ir, "issues": [i.to_message() for i in issues]}, ensure_ascii=False)
            prompt = (f"{payload}\n\n只输出 JSON 数组（修复操作）:")
            raw = self.llm.complete(prompt, system=DEEP_FIX_SYSTEM.format(max_ops=self.max_ops))
        except Exception as exc:
            return DeepFixResult(None, _outcome(False, f"LLM 调用失败: {exc}", touched_ir=False),
                                 "llm_error")
        ops = extract_json(raw)
        if not isinstance(ops, list):
            return DeepFixResult(None, _outcome(False, "LLM 输出不是 JSON 数组", touched_ir=False),
                                 "bad_output")
        errs = validate_ops(ir, ops, max_ops=self.max_ops)
        if errs:
            return DeepFixResult(None, _outcome(False, "操作被拒绝: " + "；".join(errs),
                                                touched_ir=False), "rejected", list(ops))
        new_ir = apply_ops(ir, ops)
        before, after = len(lint(ir)), len(lint(new_ir))
        if after >= before:
            return DeepFixResult(None,
                                 _outcome(False, f"未产生净改善（问题 {before}→{after}），回滚",
                                          touched_ir=False), "no_improvement", list(ops))
        return DeepFixResult(new_ir,
                             _outcome(True, f"深度修复 {len(ops)} 个操作，问题 {before}→{after}"),
                             f"问题 {before}→{after}", list(ops))
