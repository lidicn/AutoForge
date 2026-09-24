# -*- coding: utf-8 -*-
"""test_orchestrator.py — 编排器单元测试（stdlib unittest，无第三方依赖）。

FakeMCP 内置三个迷你实现，保证测试不依赖真实 AF/HA，但仍然"真的"校验：
  * mini_compile  —— AF-Spec（附录 A 语法）的最小解析器，用来验证 render_spec 往返一致
  * mini_build    —— 实现附录里的安全闸（实体存在 / STALE / ask 兜底 / canary / 连通性）
  * mini_simulate —— FakeHA 回放器，**如实复现坑 #2**：不写 type:numeric 就按字符串比较
"""
import json
import re
import unittest
from collections import defaultdict

from autoforge.af_orchestrator import (
    BuildIssue, ComposeSession, ErrorCode, FIXERS, FixContext, Orchestrator,
    QualityScorer, Status, build_graph, render_spec, render_nl,
)


# ----------------------------------------------------------------------
# 迷你 AF-Spec 解析器
# ----------------------------------------------------------------------
def _tokens(s: str):
    out, i, n = [], 0, len(s)
    while i < n:
        c = s[i]
        if c.isspace():
            i += 1
            continue
        if c == '"':
            j, buf = i + 1, []
            while j < n and s[j] != '"':
                if s[j] == "\\" and j + 1 < n:
                    buf.append(s[j + 1]); j += 2
                    continue
                buf.append(s[j]); j += 1
            out.append(("str", "".join(buf))); i = j + 1
        elif c == "{":
            depth, j = 0, i
            while j < n:
                if s[j] == '"':
                    j += 1
                    while j < n and s[j] != '"':
                        j += 2 if s[j] == "\\" else 1
                elif s[j] == "{":
                    depth += 1
                elif s[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            out.append(("json", json.loads(s[i:j + 1]))); i = j + 1
        else:
            j = i
            while j < n and not s[j].isspace():
                j += 1
            out.append(("word", s[i:j])); i = j
    return out


def mini_compile(spec: str) -> dict:
    ir = {"nodes": [], "edges": []}
    for raw in spec.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        head, _, rest = line.partition(" ")
        toks = _tokens(rest)
        if head == "automation":
            ir["id"] = toks[0][1]
        elif head in ("name", "ir_version", "mode"):
            ir[head] = toks[0][1]
        elif head == "version":
            ir["version"] = int(toks[0][1])
        elif head == "confidence":
            ir["confidence"] = float(toks[0][1])
        elif head in ("snapshot", "persist"):
            ir[head] = toks[0][1] == "true"
        elif head == "meta":
            ir.setdefault("meta", {}).update(toks[0][1])
        elif head == "var":
            ir.setdefault("vars", {})[toks[0][1]] = toks[1][1]
        elif head == "expect":
            ir.setdefault("expect", []).append(toks[0][1])
        elif head in ("on", "if", "wait", "ask", "do", "set", "pass"):
            node = {"id": toks[0][1], "kind": head}
            t = toks[1:]
            if head == "on":
                node["trigger"] = t[0][1]; t = t[1:]
            elif head == "if":
                node["condition"] = t[0][1]; t = t[1:]
            elif head == "wait":
                node["duration"] = t[0][1]; t = t[1:]
            elif head == "ask":
                node["prompt"] = t[0][1]; t = t[1:]
            elif head == "do":
                node["action"] = t[0][1]; node["params"] = t[1][1]; t = t[2:]
            elif head == "set":
                node["var"] = t[1][1]; node["value"] = t[2][1]; t = t[3:]
            k = 0
            while k < len(t):
                key = t[k][1]
                if key in ("name", "result", "session", "room", "timeout", "canary"):
                    node[key] = t[k + 1][1]; k += 2
                elif key == "requires_confirm":
                    node[key] = str(t[k + 1][1]).lower() in ("true", "1", "yes"); k += 2
                elif key in ("to", "at", "offset", "for"):
                    node["trigger"][key] = t[k + 1][1]; k += 2
                else:
                    k += 1
            ir["nodes"].append(node)
        elif head == "edge":
            words = [t[1] for t in toks]
            e = {"from": words[0], "to": words[2], "when": words[3]}
            t, k = toks[4:], 0
            while k < len(t):
                if t[k][1] in ("id", "label"):
                    e[t[k][1]] = t[k + 1][1]; k += 2
                else:
                    k += 1
            ir["edges"].append(e)
    return ir


# ----------------------------------------------------------------------
# 迷你安全闸（build）
# ----------------------------------------------------------------------
DOMAINS = {"light", "switch", "fan", "climate", "sensor", "binary_sensor", "cover",
           "media_player", "lock", "alarm_control_panel", "camera", "humidifier",
           "water_heater", "vacuum", "scene", "script", "number", "select"}
ENT_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z0-9_]+$")
RISKY3 = ("lock", "alarm_control_panel", "camera")
RISKY2 = ("climate", "media_player", "cover", "water_heater", "humidifier", "vacuum", "scene")


def _strings(o):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for v in o.values():
            yield from _strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from _strings(v)


def mini_build(ir: dict, catalog) -> dict:
    ids = {e["entity_id"] for e in catalog}
    errors, warnings = [], []
    node_ids = {n["id"] for n in ir.get("nodes", [])}
    ind = {i: 0 for i in node_ids}
    outd = {i: 0 for i in node_ids}
    for e in ir.get("edges", []):
        if e["from"] not in node_ids or e["to"] not in node_ids:
            errors.append({"code": "DISCONNECTED_NODE", "node": e["from"], "message": "边指向未知节点"})
        else:
            outd[e["from"]] += 1
            ind[e["to"]] += 1
    by_id = {n["id"]: n for n in ir.get("nodes", [])}

    # 1) 实体存在性 + 2) STALE
    for n in ir.get("nodes", []):
        for s in _strings(n):
            if ENT_RE.match(s) and s.split(".")[0] in DOMAINS:
                if s not in ids:
                    same = [e["entity_id"] for e in catalog if e["entity_id"].split(".")[0] == s.split(".")[0]]
                    errors.append({"code": "ENTITY_NOT_FOUND", "node": n["id"],
                                   "message": f"实体不存在: {s}", "suggestions": same[:3]})
                else:
                    ent = next(e for e in catalog if e["entity_id"] == s)
                    if (ent.get("attributes") or {}).get("stale") or ent.get("state") == "unavailable":
                        errors.append({"code": "STALE_ENTITY", "node": n["id"], "message": f"触发/引用实体长期无数据: {s}"})
    # 3) ask 必须有 on_timeout / default
    for n in ir.get("nodes", []):
        if n["kind"] != "ask":
            continue
        whens = {e["when"] for e in ir["edges"] if e["from"] == n["id"]}
        if "on_timeout" not in whens and "default" not in whens:
            errors.append({"code": "MISSING_TIMEOUT_OR_DEFAULT", "node": n["id"], "message": "ask 缺 on_timeout/default 兜底"})
    # 4) L2/L3 强制 requires_confirm + canary
    for n in ir.get("nodes", []):
        if n["kind"] != "do":
            continue
        dom = n["action"].split(".")[1] if n["action"].count(".") >= 2 else ""
        if dom in RISKY2 + RISKY3:
            if not (n.get("requires_confirm") and (n.get("canary") or (ir.get("meta") or {}).get("canary"))):
                errors.append({"code": "MISSING_CANARY", "node": n["id"], "message": "L2/L3 动作缺 requires_confirm + canary"})
        if "on_error" not in {e["when"] for e in ir["edges"] if e["from"] == n["id"]}:
            warnings.append({"code": "MISSING_ON_ERROR", "node": n["id"], "message": "do 缺 on_error 兜底"})
    # 5) wait 出边 when 必须是 then（坑 #5）
    for e in ir.get("edges", []):
        if by_id.get(e["from"], {}).get("kind") == "wait" and e["when"] == "on_timeout":
            errors.append({"code": "WAIT_EDGE_MISUSE", "node": e["from"], "message": "wait 到期应走 then 边"})
    # 6) 连通性 + 环
    for n in ir.get("nodes", []):
        if n["kind"] != "on" and ind[n["id"]] == 0:
            errors.append({"code": "DISCONNECTED_NODE", "node": n["id"], "message": "孤立节点"})
    g = defaultdict(list)
    for e in ir["edges"]:
        g[e["from"]].append(e["to"])
    seen, stack = set(), set()

    def dfs(u):
        seen.add(u); stack.add(u)
        for v in g.get(u, []):
            if v in stack:
                return True
            if v not in seen and dfs(v):
                return True
        stack.discard(u)
        return False
    if any(dfs(n["id"]) for n in ir["nodes"] if n["id"] not in seen):
        errors.append({"code": "CYCLE", "node": None, "message": "图中存在死循环"})
    # 7) 数值比较缺 type → 警告（build 不拦，由 simulate 抓）
    def walk(o):
        if isinstance(o, dict):
            if o.get("op") in ("gt", "lt"):
                for side in ("left", "right"):
                    sd = o.get(side)
                    if isinstance(sd, dict) and "var" in sd and "type" not in sd:
                        warnings.append({"code": "MISSING_NUMERIC_TYPE", "node": None, "message": "数值比较缺 type:numeric"})
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    for n in ir["nodes"]:
        if n["kind"] == "if":
            walk(n["condition"])
    return {"ok": not errors, "errors": errors, "warnings": warnings, "nl_description": None}


# ----------------------------------------------------------------------
# 迷你 FakeHA（simulate）—— 复现"不写 type:numeric 就按字符串比较"
# ----------------------------------------------------------------------
YES = {"好", "可以", "开", "行", "嗯", "是", "对", "yes", "y", "ok"}
NO = {"不", "不用", "不要", "算了", "别", "否", "no", "n"}
CANCEL = {"取消", "离开", "中断", "cancel"}


def _val(x):
    if isinstance(x, dict):
        if "const" in x:
            return x["const"]
        if "var" in x:
            return x.get("value")
    return x


def _cmp(op, l, r, numeric):
    if numeric:
        try:
            l, r = float(l), float(r)
        except Exception:
            pass
    else:
        l, r = ("" if l is None else str(l)), ("" if r is None else str(r))
    return {"gt": l > r, "lt": l < r, "eq": l == r, "ne": l != r}[op]


def eval_expr(e, states):
    if not isinstance(e, dict):
        return False
    op = e.get("op")
    args = e.get("args") or []
    if op == "and":
        return all(eval_expr(a, states) for a in args)
    if op == "or":
        return any(eval_expr(a, states) for a in args)
    if op == "not":
        return not eval_expr(args[0], states) if args else False
    if op in ("is_on", "is_off"):
        eid = (e.get("value") or {}).get("var", "").split("entity.", 1)[-1]
        return (states.get(eid) == "on") == (op == "is_on")
    left, right = e.get("left") or {}, e.get("right") or {}
    lv = states.get(left.get("var", "").split("entity.", 1)[-1], _val(left))
    rv = _val(right)
    numeric = left.get("type") == "numeric" or right.get("type") == "numeric"
    if op in ("gt", "lt", "eq", "ne"):
        return _cmp(op, lv, rv, numeric)
    if op == "contains":
        return str(rv) in str(lv)
    return False


def mini_simulate(ir: dict, events) -> dict:
    states, answers, final = {}, [], {}
    for ev in events or []:
        if "answer" in ev:
            answers.append(ev)
        else:
            states[ev["entity_id"]] = ev["state"]
    final.update(states)
    by_id = {n["id"]: n for n in ir["nodes"]}
    outs = defaultdict(list)
    for e in ir["edges"]:
        outs[e["from"]].append(e)
    on = next((n for n in ir["nodes"] if n["kind"] == "on"), None)
    trace = []
    if on is None:
        return {"ok": True, "trace": trace, "final_state": final, "expect_passed": False}
    t = on.get("trigger") or {}
    fired = True
    if t.get("type") == "state":
        fired = any(ev.get("entity_id") == t.get("entity_id") and
                    (not t.get("to") or ev.get("state") == t.get("to")) for ev in (events or []))
    if not fired:
        return {"ok": True, "trace": trace, "final_state": final, "expect_passed": False}
    cur = next((e["to"] for e in outs[on["id"]] if e["when"] == "then"), None)
    steps = 0
    while cur and steps < 40:
        steps += 1
        node = by_id[cur]
        trace.append(cur)
        kind = node["kind"]
        if kind == "pass":
            break
        if kind == "if":
            branch = "then" if eval_expr(node["condition"], states) else "no"
            cur = next((e["to"] for e in outs[cur] if e["when"] == branch), None)
            continue
        if kind == "ask":
            if answers:
                a = str(answers.pop(0).get("answer", ""))
                a = a.strip().lower()
                branch = "on_cancel" if a in CANCEL else "yes" if a in YES else "no" if a in NO else "default"
            else:
                branch = "on_timeout"
            cur = next((e["to"] for e in outs[cur] if e["when"] == branch), None)
            continue
        if kind == "do":
            eid = (node.get("params") or {}).get("entity_id")
            verb = node["action"].split(".")[-1]
            if verb in ("turn_on", "turn_off"):
                final[eid] = "on" if verb == "turn_on" else "off"
                states[eid] = final[eid]
            cur = next((e["to"] for e in outs[cur] if e["when"] == "then"), None)
            continue
        cur = next((e["to"] for e in outs[cur] if e["when"] == "then"), None)
    expect_ok = True
    for x in ir.get("expect") or []:
        if final.get(x.get("entity_id")) != x.get("state"):
            expect_ok = False
    return {"ok": True, "trace": trace, "final_state": final, "expect_passed": expect_ok}


# ----------------------------------------------------------------------
# Fake MCP / Fake LLM
# ----------------------------------------------------------------------
CATALOG = [
    {"entity_id": "binary_sensor.study_motion", "state": "off",
     "attributes": {"friendly_name": "书房人体传感器", "device_class": "motion", "area": "study"}},
    {"entity_id": "sensor.study_temp", "state": "26.4",
     "attributes": {"friendly_name": "书房温度", "device_class": "temperature", "area": "study"}},
    {"entity_id": "sensor.study_illum", "state": "150",
     "attributes": {"friendly_name": "书房光照", "device_class": "illuminance", "area": "study"}},
    {"entity_id": "binary_sensor.study_door", "state": "off",
     "attributes": {"friendly_name": "书房门", "device_class": "door", "area": "study"}},
    {"entity_id": "light.study_main", "state": "off",
     "attributes": {"friendly_name": "书房主灯", "area": "study"}},
    {"entity_id": "light.study_desk", "state": "off",
     "attributes": {"friendly_name": "书房台灯", "area": "study"}},
    {"entity_id": "climate.study", "state": "off",
     "attributes": {"friendly_name": "书房空调", "area": "study"}},
    {"entity_id": "lock.front_door", "state": "locked",
     "attributes": {"friendly_name": "大门锁", "area": "entry"}},
]
AREAS = {"书房": "study", "study": "study", "entry": "entry", "玄关": "entry"}


def default_resolver(name, area=None):
    cand = [e for e in CATALOG
            if (not area or AREAS.get(area, area) == (e["attributes"].get("area")))
            and (name in e["attributes"]["friendly_name"] or e["attributes"]["friendly_name"] in name)]
    if not cand:
        cand = sorted(CATALOG, key=lambda e: -difflib_ratio(name, e["attributes"]["friendly_name"]))[:2]
        cand = [c for c in cand if difflib_ratio(name, c["attributes"]["friendly_name"]) >= 0.6]
    if len(cand) == 1:
        return {"ok": True, "entity_id": cand[0]["entity_id"], "state": cand[0]["state"],
                "attributes": cand[0]["attributes"]}
    return {"ok": False, "error": "ENTITY_NOT_FOUND" if not cand else "AMBIGUOUS",
            "suggestions": [c["entity_id"] for c in cand]}


def difflib_ratio(a, b):
    import difflib
    return difflib.SequenceMatcher(None, a or "", b or "").ratio()


class FakeMCP:
    def __init__(self, entities=None, resolver=None, build_hook=None):
        self.entities = [dict(e) for e in (entities or CATALOG)]
        self.resolver = resolver or default_resolver
        self.build_hook = build_hook
        self.calls = []
        self.saved = []

    def call(self, tool, **kw):
        self.calls.append((tool, dict(kw)))
        return getattr(self, "_t_" + tool)(**kw)

    def _t_af_health(self, **kw):
        return {"ok": True, "version": "0.1.0", "store_ok": True}

    def _t_af_list_entities(self, **kw):
        ents = self.entities
        if kw.get("area"):
            ents = [e for e in ents if AREAS.get(kw["area"], kw["area"]) == e["attributes"].get("area")]
        return {"ok": True, "entities": ents}

    def _t_af_catalog(self, **kw):
        return {"ok": True, "entities": self.entities}

    def _t_af_refresh_catalog(self, **kw):
        return {"ok": True}

    def _t_af_resolve_entity(self, **kw):
        return self.resolver(kw.get("name"), kw.get("area"))

    def _t_af_compile_spec(self, **kw):
        try:
            return {"ok": True, "ir": mini_compile(kw["spec"])}
        except Exception as exc:                                    # noqa: BLE001
            return {"ok": False, "error": "SPEC_PARSE_ERROR", "line": 1, "message": str(exc)}

    def _t_af_build(self, **kw):
        out = mini_build(kw["ir"], self.entities)
        return self.build_hook(kw["ir"], out) if self.build_hook else out

    def _t_af_simulate(self, **kw):
        return mini_simulate(kw["ir"], kw.get("events") or [])

    def _t_af_save(self, **kw):
        self.saved.append(kw["ir"])
        return {"ok": True, "id": kw["ir"].get("id"), "pending": True, "version": 1}

    def _t_af_experience(self, **kw):
        return {"ok": True}

    def _t_af_live(self, **kw):
        return {"ok": True, "events": [], "matches": []}

    def tools_called(self):
        return [t for t, _ in self.calls]


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)

    def complete(self, prompt, *, system=None):
        if not self.responses:
            raise AssertionError("LLM 被多调了一次")
        return json.dumps(self.responses.pop(0), ensure_ascii=False)


# ----------------------------------------------------------------------
# 意图样例
# ----------------------------------------------------------------------
def intent_motion_light():
    return {"intent_kind": "build", "area": "书房", "name": "书房有人来开灯", "mode": "restart",
            "triggers": [{"type": "state", "entity_id": "@motion", "to": "on", "name": "检测到有人"}],
            "conditions": [],
            "actions": [{"action": "ha.light.turn_on", "params": {"entity_id": "@lamp"}, "name": "开书房主灯"}],
            "asks": [],
            "refs": [{"key": "@motion", "name": "书房人体传感器", "area": "书房", "role": "trigger"},
                     {"key": "@lamp", "name": "书房主灯", "area": "书房", "role": "action"}]}


def intent_ask_ac(numeric_type=True):
    left = {"var": "entity.@temp"}
    if numeric_type:
        left["type"] = "numeric"
    return {"intent_kind": "build", "area": "书房", "name": "书房闷了问一句要不要开空调", "mode": "restart",
            "triggers": [{"type": "state", "entity_id": "@temp", "name": "书房温度变化"}],
            "conditions": [{"op": "and", "args": [
                {"op": "gt", "left": left, "right": {"const": 27}},
                {"op": "is_off", "value": {"var": "entity.@door"}}]}],
            "actions": [{"action": "ha.climate.turn_on", "params": {"entity_id": "@ac"}, "name": "开书房空调"}],
            "asks": [{"prompt": "书房有点闷，要开空调吗？", "session": "room", "room": "书房",
                      "timeout": "60s", "name": "询问是否开空调"}],
            "expect": [{"entity_id": "@ac", "state": "on"}],
            "refs": [{"key": "@temp", "name": "书房温度", "area": "书房", "role": "trigger"},
                     {"key": "@door", "name": "书房门", "area": "书房", "role": "condition"},
                     {"key": "@ac", "name": "书房空调", "area": "书房", "role": "action"}]}


def hand_ir_without_timeout():
    return {"id": "ask_no_timeout", "name": "缺兜底", "ir_version": "0.2.1", "version": 1,
            "mode": "single",
            "nodes": [{"id": "a1", "kind": "on", "trigger": {"type": "state", "entity_id": "climate.study"}},
                      {"id": "q1", "kind": "ask", "prompt": "要开吗？", "session": "room",
                       "room": "书房", "timeout": "60s", "name": "询问"},
                      {"id": "d1", "kind": "do", "action": "ha.light.turn_on",
                       "params": {"entity_id": "light.study_main"}, "name": "开灯"},
                      {"id": "p1", "kind": "pass", "name": "结束"}],
            "edges": [{"from": "a1", "to": "q1", "when": "then"},
                      {"from": "q1", "to": "d1", "when": "yes"},
                      {"from": "d1", "to": "p1", "when": "then"},
                      {"from": "d1", "to": "p1", "when": "on_error"}]}


# =====================================================================
# 测试
# =====================================================================
class TestComposeHappyPath(unittest.TestCase):
    def test_simple_automation_full_pipeline(self):
        mcp = FakeMCP()
        orch = Orchestrator(mcp, FakeLLM([intent_motion_light()]))
        r = orch.compose("书房有人的时候把主灯打开", [])
        self.assertEqual(r.status, Status.SAVED)
        self.assertEqual(r.pending_id, "af_" + None if False else r.ir["id"])
        kinds = [n["kind"] for n in r.ir["nodes"]]
        self.assertEqual(kinds, ["on", "do", "pass"])
        self.assertIn("书房", r.nl_description)
        self.assertGreaterEqual(r.quality.total, 6.0)
        self.assertEqual(r.fix_log, [])
        self.assertIn("af_save", mcp.tools_called())
        self.assertIn("当", r.nl_description)

    def test_result_envelope_has_protocol(self):
        orch = Orchestrator(FakeMCP(), FakeLLM([intent_motion_light()]))
        r = orch.compose("书房有人开灯", [])
        msg = r.to_message()
        self.assertEqual(msg["protocol"], "af-compose/1")
        self.assertIn(msg["status"], {"saved", "need_clarification", "failed"})


class TestClarification(unittest.TestCase):
    def test_missing_action_asks_user(self):
        intent = {"intent_kind": "build", "area": "书房", "name": "书房自动化",
                  "triggers": [{"type": "state", "entity_id": "@motion", "to": "on"}],
                  "actions": [], "refs": [{"key": "@motion", "name": "书房人体传感器", "area": "书房", "role": "trigger"}]}
        orch = Orchestrator(FakeMCP(), FakeLLM([intent]))
        r = orch.compose("书房有人的时候帮我处理一下", [])
        self.assertEqual(r.status, Status.NEED_CLARIFICATION)
        self.assertEqual(r.gaps[0].slot, "action")
        self.assertTrue(r.gaps[0].options)
        self.assertIsNone(r.pending_id)

    def test_multiturn_answer_completes(self):
        mcp = FakeMCP()
        first = {"intent_kind": "build", "area": "书房", "triggers":
                 [{"type": "state", "entity_id": "@motion", "to": "on"}], "actions": [],
                 "refs": [{"key": "@motion", "name": "书房人体传感器", "area": "书房", "role": "trigger"}]}
        orch = Orchestrator(mcp, FakeLLM([first, intent_motion_light()]))
        r1 = orch.compose("书房有人时做点什么", [])
        self.assertEqual(r1.status, Status.NEED_CLARIFICATION)
        r2 = orch.continue_compose(r1.session_id, "开书房主灯")
        self.assertEqual(r2.status, Status.SAVED)
        self.assertTrue(mcp.saved)

    def test_entity_ambiguity_lists_candidates(self):
        intent = dict(intent_motion_light())
        intent["actions"] = [{"action": "ha.light.turn_on", "params": {"entity_id": "@lamp"}, "name": "开灯"}]
        intent["refs"] = [{"key": "@motion", "name": "书房人体传感器", "area": "书房", "role": "trigger"},
                          {"key": "@lamp", "name": "书房灯", "area": "书房", "role": "action"}]
        mcp = FakeMCP()
        orch = Orchestrator(mcp, FakeLLM([intent]))
        r1 = orch.compose("书房有人时开灯", [])
        self.assertEqual(r1.status, Status.NEED_CLARIFICATION)
        gap = next(g for g in r1.gaps if g.slot.startswith("entity:@lamp"))
        self.assertEqual(len(gap.options), 2)
        r2 = orch.continue_compose(r1.session_id, "2")
        self.assertEqual(r2.status, Status.SAVED)
        self.assertEqual(r2.ir["nodes"][1]["params"]["entity_id"], "light.study_desk")


class TestAutoFix(unittest.TestCase):
    def test_build_stale_entity_autofix(self):
        ents = [dict(e) for e in CATALOG]
        ents[0]["attributes"] = dict(ents[0]["attributes"], stale=True)
        ents.append({"entity_id": "binary_sensor.study_motion_2", "state": "off",
                     "attributes": {"friendly_name": "书房人体传感器备用", "device_class": "motion", "area": "study"}})
        mcp = FakeMCP(entities=ents)
        orch = Orchestrator(mcp, FakeLLM([intent_motion_light()]))
        r = orch.compose("书房有人的时候把主灯打开", [])
        self.assertEqual(r.status, Status.SAVED)
        codes = [f.code for f in r.fix_log]
        self.assertIn("STALE_ENTITY", codes)
        self.assertEqual(r.ir["nodes"][0]["trigger"]["entity_id"], "binary_sensor.study_motion_2")

    def test_repair_ir_adds_timeout_edge(self):
        mcp = FakeMCP()
        orch = Orchestrator(mcp, None)
        rep = orch.repair_ir(hand_ir_without_timeout())
        self.assertTrue(rep.fixed)
        whens = {e["when"] for e in rep.ir["edges"] if e["from"] == "q1"}
        self.assertIn("on_timeout", whens)
        self.assertIn("default", whens)
        self.assertTrue(mini_build(rep.ir, mcp.entities)["ok"])

    def test_repair_ir_replaces_unknown_entity(self):
        ir = hand_ir_without_timeout()
        ir["nodes"][3]["params"] = {"entity_id": "climate.no_such"}
        ir["nodes"][3]["action"] = "ha.climate.turn_on"
        mcp = FakeMCP()
        orch = Orchestrator(mcp, None)
        rep = orch.repair_ir(ir)
        self.assertTrue(rep.fixed)
        self.assertEqual(rep.ir["nodes"][3]["params"]["entity_id"], "climate.study")

    def test_fix_edge_label_repair(self):
        ir = hand_ir_without_timeout()
        for e in ir["edges"]:
            if e["from"] == "q1" and e["when"] == "yes":
                e["when"] = "then"          # 非法：ask 没有 then 出边
        orch = Orchestrator(FakeMCP(), None)
        sess = ComposeSession(session_id="t")
        issue = BuildIssue(ErrorCode.SIM_BRANCH_MISMATCH, "q1", "分支不符", case="main")
        out = FIXERS[ErrorCode.SIM_BRANCH_MISMATCH](FixContext(orch, sess, issue, ir))
        self.assertTrue(out.applied)
        self.assertIn("yes", {e["when"] for e in ir["edges"] if e["from"] == "q1"})

    def test_simulate_failure_numeric_type_autofix(self):
        mcp = FakeMCP()
        orch = Orchestrator(mcp, FakeLLM([intent_ask_ac(numeric_type=False)]))
        r = orch.compose("书房温度高于 27 且门关着时问我开不开空调", [])
        self.assertEqual(r.status, Status.SAVED)
        codes = [f.code for f in r.fix_log]
        self.assertIn("MISSING_NUMERIC_TYPE", codes)
        left = r.ir["nodes"][1]["condition"]["args"][0]["left"]
        self.assertEqual(left.get("type"), "numeric")
        self.assertIn("d1", r.simulation["traces"][0])

    def test_retry_budget_exhausted_reports(self):
        def hook(ir, out):
            doms = ["climate", "climate"]
            return {"ok": False,
                    "errors": [{"code": "ENTITY_NOT_FOUND", "node": "d1", "message": "持续失败",
                                "suggestions": ["climate.study", "climate.study"]}],
                    "warnings": []}
        mcp = FakeMCP(build_hook=hook)
        orch = Orchestrator(mcp, FakeLLM([intent_motion_light()]), max_fix_attempts=3)
        r = orch.compose("书房有人开灯", [])
        self.assertEqual(r.status, Status.FAILED)
        self.assertEqual(orch.sessions.get(r.session_id).attempts["build"], 3)
        self.assertTrue(r.fix_log)
        self.assertIn("重试", r.message)


class TestQuality(unittest.TestCase):
    def setUp(self):
        self.scorer = QualityScorer()

    def test_low_score_dims_and_suggestions(self):
        ir = {"id": "bad", "name": "x", "ir_version": "0.2.1", "version": 1, "mode": "parallel",
              "nodes": [{"id": "a1", "kind": "on", "trigger": {"type": "state", "entity_id": "lock.front_door"}},
                        {"id": "q1", "kind": "ask", "prompt": "?", "timeout": "60s"},
                        {"id": "d1", "kind": "do", "action": "ha.lock.unlock", "params": {"entity_id": "lock.front_door"}}],
              "edges": [{"from": "a1", "to": "q1", "when": "then"},
                        {"from": "q1", "to": "d1", "when": "yes"}]}
        rep = self.scorer.score_ir(ir)
        self.assertLess(rep.dims["safety"], 6.0)
        self.assertLess(rep.dims["robustness"], 7.0)
        self.assertLess(rep.dims["maintainability"], 6.0)
        text = " ".join(rep.suggestions)
        self.assertIn("on_timeout", text)
        self.assertIn("canary", text)
        self.assertIn("on_error", text)
        self.assertEqual(rep.grade in ("C", "D"), True)

    def test_coverage_comes_from_simulation(self):
        mcp = FakeMCP()
        orch = Orchestrator(mcp, FakeLLM([intent_ask_ac()]))
        r = orch.compose("闷了问一句开空调", [])
        self.assertGreater(r.quality.dims["coverage"], 0.0)
        self.assertLess(r.quality.dims["coverage"], 10.0)
        self.assertGreaterEqual(len(r.simulation["cases"]), 5)
        self.assertTrue(any("coverage" in s for s in r.quality.suggestions))

    def test_safety_full_marks_for_guarded_l2(self):
        mcp = FakeMCP()
        orch = Orchestrator(mcp, FakeLLM([intent_ask_ac()]))
        r = orch.compose("闷了问一句开空调", [])
        self.assertGreaterEqual(r.quality.dims["safety"], 8.0)


class TestRecommend(unittest.TestCase):
    def test_vague_intent_offers_options_and_composes(self):
        mcp = FakeMCP()
        intent = {"intent_kind": "recommend", "area": "书房"}
        orch = Orchestrator(mcp, FakeLLM([intent]))
        r1 = orch.compose("我想要一个书房自动化", [])
        self.assertEqual(r1.status, Status.RECOMMENDING)
        self.assertTrue(2 <= len(r1.recommendations) <= 3)
        self.assertIn("书房主灯", r1.message)
        r2 = orch.continue_compose(r1.session_id, "1")
        self.assertIn(r2.status, (Status.SAVED, Status.NEED_CLARIFICATION, Status.AWAITING_CONFIRM))
        self.assertIsNotNone(r2.ir or r2.gaps)


class TestAskAutomation(unittest.TestCase):
    def test_ask_automation_five_branches(self):
        mcp = FakeMCP()
        orch = Orchestrator(mcp, FakeLLM([intent_ask_ac()]))
        r = orch.compose("书房温度高于 27 且门关着时问我开不开空调", [])
        self.assertEqual(r.status, Status.SAVED)
        q = next(n for n in r.ir["nodes"] if n["kind"] == "ask")
        whens = {e["when"] for e in r.ir["edges"] if e["from"] == q["id"]}
        self.assertTrue({"yes", "no", "default", "on_timeout", "on_cancel"} <= whens)
        traces = {c["name"]: c["trace"] for c in r.simulation["cases"]}
        self.assertIn("q1", traces["main"])
        self.assertIn("d1", traces["main"])
        self.assertNotIn("d1", traces.get("decline", []))
        self.assertNotIn("d1", traces.get("timeout", []))
        self.assertIn("问", r.nl_description)


class TestSafetyAndConfirm(unittest.TestCase):
    def test_never_calls_approve(self):
        mcp = FakeMCP()
        orch = Orchestrator(mcp, FakeLLM([intent_motion_light()]))
        orch.compose("书房有人开灯", [])
        self.assertFalse([t for t in mcp.tools_called() if "approve" in t.lower()])

    def test_approve_is_hard_blocked(self):
        orch = Orchestrator(FakeMCP(), None)
        with self.assertRaises(Exception):
            orch._call("af_approve", id="x")

    def test_confirm_then_save(self):
        mcp = FakeMCP()
        orch = Orchestrator(mcp, FakeLLM([intent_motion_light()]))
        r1 = orch.compose("书房有人开灯", [], auto_commit=False)
        self.assertEqual(r1.status, Status.AWAITING_CONFIRM)
        self.assertNotIn("af_save", mcp.tools_called())
        self.assertIsNone(r1.pending_id)
        r2 = orch.continue_compose(r1.session_id, "确认")
        self.assertEqual(r2.status, Status.SAVED)
        self.assertEqual(mcp.tools_called().count("af_save"), 1)

    def test_save_only_creates_pending(self):
        mcp = FakeMCP()
        orch = Orchestrator(mcp, FakeLLM([intent_motion_light()]))
        r = orch.compose("书房有人开灯", [])
        self.assertTrue(r.pending_id)
        self.assertTrue(mcp.saved)


class TestSpecRoundTrip(unittest.TestCase):
    def test_render_spec_roundtrip(self):
        spec = """automation study_day_light
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
edge i1 -> d1 then
edge i1 -> p1 no
edge d1 -> p1 then
edge d1 -> p1 on_error id e2 label "失败兜底"
"""
        ir1 = mini_compile(spec)
        ir2 = mini_compile(render_spec(ir1))
        self.assertEqual(json.dumps(ir1, sort_keys=True), json.dumps(ir2, sort_keys=True))

    def test_build_graph_matches_appendix_shape(self):
        from autoforge.af_orchestrator import AutomationDraft, EntityRef, merge_intent
        d = AutomationDraft()
        merge_intent(d, intent_ask_ac())
        g = build_graph(d)
        kinds = [n["kind"] for n in g["nodes"]]
        self.assertEqual(kinds, ["on", "if", "ask", "do", "pass", "pass"])
        self.assertTrue(mini_build(mini_compile(render_spec(g)), CATALOG)["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

