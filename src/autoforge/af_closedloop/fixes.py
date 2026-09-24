# -*- coding: utf-8 -*-
"""fixes — 只读仓 af_orchestrator 的 5 个确定性补丁（B1–B5）。

E:/NAS/AutoForge 只读 ⇒ 不改源文件，在进程内替换 5 个模块级函数，其余逻辑全部复用原实现。
install() 幂等；uninstall() 完整回滚。补丁点即上游可直接回写源内的最小改动。
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Callable, Mapping

from .graphops import relink_chain
from .runtime import load_module

PATCHES = {
    "merge_intent":     "B1 覆盖模式丢条目（clear/replace 在循环体内）+ B4 mode 槽位被默认值堵死",
    "build_graph":      "B2 多 ask 断链（第二个 ask 无入边）",
    "detect_gaps":      "B3 阈值答案未数值化 + threshold slot 不稳定",
    "default_prompt":   "B5 追问文案泄漏 @ref 且动词重复",
    "_apply_defaults":  "B4 假设误记账（mode 明说仍记 'mode 默认 X'）",
}
_NAMES = tuple(PATCHES)
ORIGINALS: dict[str, Callable] = {}
_FLAG = "_af_closedloop_fixed"

_LIST_KEYS = ("triggers", "trigger", "conditions", "condition",
              "actions", "action", "asks", "ask", "expect")
_SCALAR_KEYS = ("id", "name", "area", "mode")
_SLOT_RE = re.compile(r"^threshold:(\d+):(\d+)$")


def _capture(mod) -> None:
    for name in _NAMES:
        fn = getattr(mod, name, None)
        if fn is not None and not getattr(fn, _FLAG, False):
            ORIGINALS.setdefault(name, fn)


def install(module=None):
    """打补丁（幂等）。返回被打补丁的 af_orchestrator 模块。"""
    mod = module or load_module()
    _capture(mod)
    mod.merge_intent = merge_intent
    mod.build_graph = build_graph
    mod.detect_gaps = detect_gaps
    mod.default_prompt = default_prompt
    mod._apply_defaults = defaults_fixed
    return mod


def uninstall(module=None):
    mod = module or load_module()
    for name in _NAMES:
        if name in ORIGINALS:
            setattr(mod, name, ORIGINALS[name])
    return mod


# ---- 小工具 ----------------------------------------------------------
def _as_list(x):
    return [] if x is None else (list(x) if isinstance(x, list) else [x])


def _iter_pieces(intent: Mapping):
    """把一份意图拆成『单条目碎片』：原实现一次调用里对多条目做 clear/replace 会丢条目。"""
    base = {k: v for k, v in intent.items() if k not in _LIST_KEYS}
    if base:
        yield base
    for sing, plur in (("trigger", "triggers"), ("condition", "conditions"),
                       ("action", "actions"), ("ask", "asks")):
        for item in _as_list(intent.get(plur) or intent.get(sing)):
            yield {plur: [dict(item)]}
    for item in _as_list(intent.get("expect")):
        yield {"expect": [dict(item)]}


def _noop_defaults(draft):
    return None


def _drop_new(draft, before, text):
    if text in draft.assumptions and text not in before:
        draft.assumptions.remove(text)


def _device_label(draft, eid):
    if not isinstance(eid, str) or not eid:
        return None
    ref = (getattr(draft, "refs", None) or {}).get(eid)
    if ref is not None and getattr(ref, "name", ""):
        return ref.name
    label = draft.labels().get(eid)
    return label or (eid.lstrip("@") or None)


# ---- B1 + B4(mode 槽位) ----------------------------------------------
def merge_intent(draft, intent, *, overwrite=False):
    """B1: overwrite=True 不再丢条目（原实现循环内 clear/replace，多条只剩最后一条）。

    同时修 B4 的 mode 槽位：意图里带 mode 就采纳（原实现被 dataclass 默认值
    `mode: str = "restart"` 堵死，LLM/用户给的 mode 永远进不来）。
    """
    if not intent:
        return
    mod = load_module()
    _capture(mod)
    if overwrite:
        draft.triggers.clear()
        draft.conditions.clear()
        draft.actions.clear()
        draft.asks.clear()
        draft.expect.clear()
        for k in _SCALAR_KEYS:
            v = intent.get(k)
            if k != "mode" and v not in (None, ""):
                setattr(draft, k, v)
    mode = intent.get("mode")
    if mode not in (None, ""):
        draft.mode = str(mode)
        setattr(draft, "_af_mode_given", True)
    saved = mod.__dict__.get("_apply_defaults")
    mod._apply_defaults = _noop_defaults          # 默认值只在最后统一落地一次
    try:
        for piece in _iter_pieces(intent):
            ORIGINALS["merge_intent"](draft, piece, overwrite=False)
    finally:
        mod._apply_defaults = saved if saved is not None else ORIGINALS["_apply_defaults"]
    defaults_fixed(draft)


# ---- B4(记账) + 恢复被堵死的 mode 默认规则 ---------------------------
def defaults_fixed(draft):
    """B4: 只有真正被默认出来的 mode 才记 'mode 默认 X' 假设。

    同时让原规则（有触发且首个是 time → single）真正生效：`draft.mode or (...)`
    在 `mode: str = "restart"` 下恒不触发。mode 一旦被显式给定就不再自动重算。
    """
    before = list(draft.assumptions)
    explicit = bool(getattr(draft, "_af_mode_given", False))
    auto = bool(getattr(draft, "_af_mode_defaulted", False))
    cls_default = getattr(type(draft), "mode", None)
    defaulted = False
    if not explicit and (auto or draft.mode == cls_default):
        if draft.triggers:
            typ = (draft.triggers[0] or {}).get("type")
            draft.mode = "single" if typ == "time" else (cls_default or "restart")
        else:
            draft.mode = "single"
        setattr(draft, "_af_mode_defaulted", True)
        defaulted = True
    ORIGINALS["_apply_defaults"](draft)
    if not defaulted:
        _drop_new(draft, before, f"mode 默认 {draft.mode}")


# ---- B2 --------------------------------------------------------------
def build_graph(draft):
    graph = ORIGINALS["build_graph"](draft)
    return relink_chain(graph)


# ---- B3 --------------------------------------------------------------
def detect_gaps(draft, catalog=()):
    mod = load_module()
    _capture(mod)
    gaps = ORIGINALS["detect_gaps"](draft, catalog)
    seen: dict = defaultdict(int)
    out = []
    for g in gaps:
        m = _SLOT_RE.match(str(getattr(g, "slot", "") or ""))
        if m:
            idx = m.group(1)
            g.slot = f"threshold:{idx}:{seen[idx]}"   # 原实现用 len(gaps)，跨草稿不稳定
            seen[idx] += 1
        if getattr(g, "kind", "") == "number" and callable(g.apply):
            g.apply = _numeric_apply(g.apply)        # 原实现 _parse_number 从未被调用
        out.append(g)
    return out


def _numeric_apply(fn):
    def _wrap(v, _fn=fn):
        n = load_module()._parse_number(str(v))
        return _fn(n if n is not None else v)
    return _wrap


# ---- B5 --------------------------------------------------------------
def default_prompt(draft):
    """B5: 追问文案用设备中文名（不再泄漏 @ref），且不重复动词（'要开开灯吗' → '要开灯吗'）。"""
    mod = load_module()
    _capture(mod)
    if draft.actions:
        a = draft.actions[0] or {}
        verb = str(a.get("action") or "").split(".")[-1]
        zh = mod.VERB_ZH.get(verb, verb)
        who = a.get("name") or _device_label(draft, (a.get("params") or {}).get("entity_id")) or "该设备"
        phrase = who if str(who).startswith(zh) else f"{zh}{who}"
        return f"要{phrase}吗？"
    return "要执行这个自动化吗？"


for _fn in (merge_intent, defaults_fixed, build_graph, detect_gaps, default_prompt):
    setattr(_fn, _FLAG, True)
