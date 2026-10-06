"""F14 —— NL↔IR 往返闭环测试。

覆盖：
- §2.1 目标：中文 NL → 合规 IR、IR → NL → IR 结构等价
- 8 种 node kind / 5 种 trigger type 全覆盖
- RUNTIME_ONLY_FIELDS 与 `_non_reversible` 的差异化处理
- fail-closed 的异常路径
"""
from __future__ import annotations

import pathlib
import sys
import unittest

_SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from autoforge.af_ir import IRValidationError, validate_automation          # noqa: E402
from autoforge.af_irreversible import (                                     # noqa: E402
    NON_REVERSIBLE_KEY,
    RUNTIME_ONLY_FIELDS,
)
from autoforge.af_nl import render_automation                               # noqa: E402
from autoforge.af_nl_parse import ParseError, duration_seconds, parse_automation, parse_graph


# ---------------------------------------------------------------- 语义归一
def _norm_value(v):
    if isinstance(v, bool):
        return ("bool", v)
    if isinstance(v, (int, float)):
        return ("num", float(v))
    return ("other", v)


def _norm_dur(v):
    secs = duration_seconds(v)
    return ("dur", secs) if secs is not None else ("raw", v)


def _norm_at(v):
    if v is None:
        return None
    s = str(v)
    if len(s) == 4 and s[1] == ":":
        s = "0" + s
    return s


def _norm_offset(v):
    if not v:
        return None
    s = str(v)
    sign = "-" if s.startswith("-") else "+"
    return (sign, duration_seconds(s))


def _norm_operand(o):
    if not isinstance(o, dict):
        return ("?", repr(o))
    if "const" in o:
        return ("const", _norm_value(o["const"]))
    if "var" in o:
        name = str(o["var"])
        for prefix in ("states.", "state.", "entity.", "entities."):
            if name.startswith(prefix):
                name = name[len(prefix):]
        return ("var", name)
    if "fn" in o:
        return ("fn", o.get("fn"), tuple(_norm_operand(a) for a in o.get("args") or ()))
    return ("?", repr(o))


def _norm_expr(e):
    if not isinstance(e, dict):
        return None
    op = e.get("op")
    if op in ("and", "or"):
        args = []
        for a in e.get("args") or ():
            n = _norm_expr(a)
            if isinstance(n, tuple) and len(n) == 2 and n[0] == op and isinstance(n[1], tuple):
                args.extend(n[1])
            else:
                args.append(n)
        return (op, tuple(args))
    if op == "not":
        inner = _norm_expr((e.get("args") or [None])[0])
        return ("not", inner)
    if op in ("eq", "ne", "lt", "lte", "gt", "gte"):
        return (op, _norm_operand(e.get("left")), _norm_operand(e.get("right")))
    if op in ("not_is_on", "not_is_off"):
        base = "is_on" if op == "not_is_on" else "is_off"
        return ("not", (base, _norm_operand(e.get("value"))))
    return (op, _norm_operand(e.get("value")))


def _norm_trigger(t):
    if not isinstance(t, dict):
        return None
    typ = t.get("type")
    if typ == "state":
        return ("state", t.get("entity_id"), t.get("from"), t.get("to"))
    if typ == "time":
        return ("time", _norm_at(t.get("at")))
    if typ == "sun":
        return ("sun", t.get("event"), _norm_offset(t.get("offset")))
    if typ == "event":
        return ("event", t.get("event"))
    if typ == "group":
        return ("group", t.get("op"), tuple(_norm_trigger(s) for s in t.get("sources") or ()))
    return (typ, repr(sorted(t.items())))


# ---------------------------------------------------------------- 结构指纹
def _params_core(action: str, params: dict) -> tuple:
    keys = {"entity_id"}
    table = {
        "climate.set_temperature": ("temperature",),
        "water_heater.set_temperature": ("temperature",),
        "humidifier.set_humidity": ("humidity",),
        "media_player.volume_set": ("volume_level",),
        "cover.set_cover_position": ("position",),
        "climate.set_hvac_mode": ("hvac_mode",),
        "climate.set_fan_mode": ("fan_mode",),
        "climate.set_preset_mode": ("preset_mode",),
        "notify.notify": ("message",),
        "persistent_notification.create": ("message",),
    }
    keys |= set(table.get(action, ()))
    return tuple(sorted((k, _norm_value(v)) for k, v in (params or {}).items() if k in keys))


def _core_fields(node: dict) -> tuple:
    kind = node.get("kind")
    if kind == "on":
        return ("on", _norm_trigger(node.get("trigger")), _norm_dur(node.get("for")))
    if kind == "do":
        return ("do", node.get("adapter"), node.get("action"),
                _params_core(node.get("action") or "", node.get("params") or {}))
    return (kind,)


def _full_fields(node: dict) -> tuple:
    base = _core_fields(node)
    kind = node.get("kind")
    extra: tuple = ()
    if kind == "if":
        extra = (_norm_expr(node.get("expr")),)
    elif kind == "ask":
        extra = (
            node.get("prompt"),
            node.get("session"),
            node.get("room"),
            _norm_dur(node.get("timeout")),
            tuple(sorted((node.get("ask") or {}).items(), key=repr)),
        )
    elif kind == "wait":
        extra = (_norm_dur(node.get("duration")),)
    elif kind == "set":
        extra = (node.get("var"), _norm_value(node.get("value")))
    elif kind == "group":
        extra = (node.get("mode"),)
    return base + extra


def _canon(ir: dict, node_fn) -> tuple:
    nodes = {n["id"]: n for n in ir["nodes"]}
    out_edges: dict[str, list] = {}
    incoming: set[str] = set()
    for e in ir["edges"]:
        out_edges.setdefault(e["from"], []).append(e)
        incoming.add(e["to"])
    entries = [n["id"] for n in ir["nodes"] if n["id"] not in incoming]
    seen: set[str] = set()

    def walk(nid: str):
        if nid in seen:
            return ("<cycle>", nid)
        seen.add(nid)
        node = nodes[nid]
        kids = []
        for e in sorted(out_edges.get(nid, []), key=lambda x: x["kind"]):
            kids.append((e["kind"], walk(e["to"])))
        head = node_fn(node)
        if node.get("kind") == "group":
            children = tuple(sorted((_canon(c, node_fn) for c in node.get("children") or ()), key=repr))
            head = head + (node.get("mode"), children)
        return (head, tuple(kids))

    roots = tuple(sorted((walk(e) for e in entries), key=repr))
    if len(seen) != len(nodes):
        raise AssertionError(f"有节点游离在边之外：{sorted(set(nodes) - seen)}")
    return roots


def assert_core_equal(ir1: dict, ir2: dict) -> None:
    assert len(ir1["nodes"]) == len(ir2["nodes"]), (
        f"node 数不一致：{len(ir1['nodes'])} != {len(ir2['nodes'])}")
    assert len(ir1["edges"]) == len(ir2["edges"]), (
        f"edge 数不一致：{len(ir1['edges'])} != {len(ir2['edges'])}")
    kinds1 = sorted(n["kind"] for n in ir1["nodes"])
    kinds2 = sorted(n["kind"] for n in ir2["nodes"])
    assert kinds1 == kinds2, f"节点 kind 不一致：{kinds1} != {kinds2}"
    c1 = _canon(ir1, _core_fields)
    c2 = _canon(ir2, _core_fields)
    assert c1 == c2, f"核心字段不同构：\nir1={c1}\nir2={c2}"


def assert_full_equal(ir1: dict, ir2: dict) -> None:
    assert_core_equal(ir1, ir2)
    f1 = _canon(ir1, _full_fields)
    f2 = _canon(ir2, _full_fields)
    assert f1 == f2, f"扩展字段不同构：\nir1={f1}\nir2={f2}"


def node_of(ir: dict, kind: str) -> dict:
    for n in ir["nodes"]:
        if n["kind"] == kind:
            return n
    raise AssertionError(f"找不到 kind={kind} 的节点：{[n['kind'] for n in ir['nodes']]}")


# ---------------------------------------------------------------- 种子 IR
def seed(name: str, nodes: list, edges: list, **kw) -> dict:
    return {
        "ir_version": kw.pop("ir_version", "0.2.1"),
        "id": kw.pop("id", name),
        "name": kw.pop("display", name),
        "version": kw.pop("version", 1),
        "mode": kw.pop("mode", "single"),
        "nodes": nodes,
        "edges": edges,
    }


SEEDS: list[dict] = [
    seed("s_state_do",
         [{"id": "t", "kind": "on",
           "trigger": {"type": "state", "entity_id": "binary_sensor.motion", "to": "on"}},
          {"id": "a", "kind": "do", "adapter": "homeassistant", "action": "light.turn_on",
           "params": {"entity_id": "light.living_room"}}],
         [{"from": "t", "to": "a", "kind": "then"}]),
    seed("s_state_from_to_for",
         [{"id": "t", "kind": "on",
           "trigger": {"type": "state", "entity_id": "binary_sensor.door",
                       "from": "off", "to": "on"}, "for": "00:05:00"},
          {"id": "a", "kind": "do", "adapter": "homeassistant", "action": "switch.turn_off",
           "params": {"entity_id": "switch.alarm"}}],
         [{"from": "t", "to": "a", "kind": "then"}]),
    seed("s_time_wait",
         [{"id": "t", "kind": "on", "trigger": {"type": "time", "at": "22:00"}},
          {"id": "a", "kind": "do", "adapter": "homeassistant", "action": "light.turn_off",
           "params": {"entity_id": "light.all"}},
          {"id": "w", "kind": "wait", "duration": "00:05:00"},
          {"id": "b", "kind": "do", "adapter": "homeassistant", "action": "switch.turn_off",
           "params": {"entity_id": "switch.fan"}}],
         [{"from": "t", "to": "a", "kind": "then"},
          {"from": "a", "to": "w", "kind": "then"},
          {"from": "w", "to": "b", "kind": "then"}]),
    seed("s_sun_set",
         [{"id": "t", "kind": "on",
           "trigger": {"type": "sun", "event": "sunrise", "offset": "+00:30:00"}},
          {"id": "s", "kind": "set", "var": "morning", "value": 1}],
         [{"from": "t", "to": "s", "kind": "then"}]),
    seed("s_event_pass",
         [{"id": "t", "kind": "on", "trigger": {"type": "event", "event": "af.test_event"}},
          {"id": "p", "kind": "pass"}],
         [{"from": "t", "to": "p", "kind": "then"}]),
    seed("s_group_trigger",
         [{"id": "t", "kind": "on",
           "trigger": {"type": "group", "op": "and", "sources": [
               {"type": "state", "entity_id": "binary_sensor.motion", "to": "on"},
               {"type": "state", "entity_id": "binary_sensor.door", "to": "off"}]}},
          {"id": "a", "kind": "do", "adapter": "homeassistant", "action": "light.turn_on",
           "params": {"entity_id": "light.hall"}}],
         [{"from": "t", "to": "a", "kind": "then"}]),
    seed("s_if_yes_no",
         [{"id": "t", "kind": "on",
           "trigger": {"type": "state", "entity_id": "binary_sensor.motion", "to": "on"}},
          {"id": "c", "kind": "if",
           "expr": {"op": "eq", "left": {"var": "light.living_room", "type": "string"},
                    "right": {"const": "off"}}},
          {"id": "y", "kind": "do", "adapter": "homeassistant", "action": "light.turn_on",
           "params": {"entity_id": "light.living_room"}},
          {"id": "n", "kind": "do", "adapter": "homeassistant", "action": "light.turn_off",
           "params": {"entity_id": "light.living_room"}}],
         [{"from": "t", "to": "c", "kind": "then"},
          {"from": "c", "to": "y", "kind": "yes"},
          {"from": "c", "to": "n", "kind": "no"}]),
    seed("s_ask_branches",
         [{"id": "t", "kind": "on",
           "trigger": {"type": "state", "entity_id": "binary_sensor.motion", "to": "on"}},
          {"id": "q", "kind": "ask", "prompt": "要开灯吗", "session": "room"},
          {"id": "y", "kind": "do", "adapter": "homeassistant", "action": "light.turn_on",
           "params": {"entity_id": "light.living_room"}},
          {"id": "n", "kind": "do", "adapter": "homeassistant", "action": "light.turn_off",
           "params": {"entity_id": "light.living_room"}},
          {"id": "o", "kind": "do", "adapter": "homeassistant", "action": "light.turn_off",
           "params": {"entity_id": "light.living_room"}}],
         [{"from": "t", "to": "q", "kind": "then"},
          {"from": "q", "to": "y", "kind": "yes"},
          {"from": "q", "to": "n", "kind": "no"},
          {"from": "q", "to": "o", "kind": "on_timeout"}]),
    seed("s_do_on_error",
         [{"id": "t", "kind": "on",
           "trigger": {"type": "state", "entity_id": "binary_sensor.motion", "to": "on"}},
          {"id": "a", "kind": "do", "adapter": "homeassistant", "action": "light.turn_on",
           "params": {"entity_id": "light.a"}},
          {"id": "e", "kind": "do", "adapter": "homeassistant", "action": "light.turn_off",
           "params": {"entity_id": "light.a"}}],
         [{"from": "t", "to": "a", "kind": "then"},
          {"from": "a", "to": "e", "kind": "on_error"}]),
    seed("s_set_chain",
         [{"id": "t", "kind": "on",
           "trigger": {"type": "state", "entity_id": "binary_sensor.motion", "to": "on"}},
          {"id": "s", "kind": "set", "var": "counter", "value": 5},
          {"id": "a", "kind": "do", "adapter": "homeassistant", "action": "light.turn_on",
           "params": {"entity_id": "light.living_room"}}],
         [{"from": "t", "to": "s", "kind": "then"},
          {"from": "s", "to": "a", "kind": "then"}]),
    seed("s_climate_params",
         [{"id": "t", "kind": "on", "trigger": {"type": "time", "at": "07:30"}},
          {"id": "a", "kind": "do", "adapter": "homeassistant",
           "action": "climate.set_temperature",
           "params": {"entity_id": "climate.living_room", "temperature": 26}}],
         [{"from": "t", "to": "a", "kind": "then"}]),
    seed("s_group_container",
         [{"id": "t", "kind": "on",
           "trigger": {"type": "state", "entity_id": "binary_sensor.motion", "to": "on"}},
          {"id": "g", "kind": "group", "mode": "sequence", "children": [
              {"id": "child_1", "name": "开走廊灯", "version": 1, "mode": "single",
               "nodes": [{"id": "c1", "kind": "on",
                          "trigger": {"type": "state", "entity_id": "binary_sensor.door",
                                      "to": "on"}},
                         {"id": "c2", "kind": "do", "adapter": "homeassistant",
                          "action": "light.turn_on", "params": {"entity_id": "light.hall"}}],
               "edges": [{"from": "c1", "to": "c2", "kind": "then"}]}]}],
         [{"from": "t", "to": "g", "kind": "then"}]),
    seed("s_long_chain",
         [{"id": "t", "kind": "on",
           "trigger": {"type": "state", "entity_id": "binary_sensor.motion", "to": "on"}},
          {"id": "a", "kind": "do", "adapter": "homeassistant", "action": "fan.turn_on",
           "params": {"entity_id": "fan.bedroom"}},
          {"id": "w", "kind": "wait", "duration": "00:10:00"},
          {"id": "b", "kind": "do", "adapter": "homeassistant", "action": "fan.turn_off",
           "params": {"entity_id": "fan.bedroom"}},
          {"id": "p", "kind": "pass"}],
         [{"from": "t", "to": "a", "kind": "then"},
          {"from": "a", "to": "w", "kind": "then"},
          {"from": "w", "to": "b", "kind": "then"},
          {"from": "b", "to": "p", "kind": "then"}]),
]


# ================================================================ 测试类
class TestCanonicalDialect(unittest.TestCase):
    """自洽方言用例：NL 与期望 IR 都由本文件给出，验证解析器自身正确性。"""

    def test_state_trigger_turns_on_light(self):
        ir = parse_automation("当 binary_sensor.motion 变为「on」\n然后：打开 light.living_room")
        on = node_of(ir, "on")
        self.assertEqual(on["trigger"],
                         {"type": "state", "entity_id": "binary_sensor.motion", "to": "on"})
        do = node_of(ir, "do")
        self.assertEqual(do["action"], "light.turn_on")
        self.assertEqual(do["params"], {"entity_id": "light.living_room"})
        self.assertEqual(do["adapter"], "homeassistant")

    def test_state_trigger_from_to_and_for(self):
        ir = parse_automation("当 binary_sensor.door 从「off」变为「on」，持续 5 分钟\n"
                              "然后：关闭 switch.alarm")
        on = node_of(ir, "on")
        self.assertEqual(on["trigger"], {"type": "state", "entity_id": "binary_sensor.door",
                                         "from": "off", "to": "on"})
        self.assertEqual(duration_seconds(on["for"]), 300.0)
        self.assertEqual(node_of(ir, "do")["action"], "switch.turn_off")

    def test_time_trigger(self):
        ir = parse_automation("每天 7:30\n然后：打开 switch.coffee")
        self.assertEqual(node_of(ir, "on")["trigger"], {"type": "time", "at": "07:30"})

    def test_sun_trigger_with_offset(self):
        ir = parse_automation("日出后 30 分钟\n然后：打开 light.kitchen")
        trig = node_of(ir, "on")["trigger"]
        self.assertEqual(trig["type"], "sun")
        self.assertEqual(trig["event"], "sunrise")
        self.assertEqual(duration_seconds(trig["offset"]), 1800.0)

    def test_event_trigger(self):
        ir = parse_automation("收到事件「af.morning_started」\n然后：运行脚本 script.morning")
        self.assertEqual(node_of(ir, "on")["trigger"],
                         {"type": "event", "event": "af.morning_started"})
        self.assertEqual(node_of(ir, "do")["action"], "script.turn_on")

    def test_group_trigger_and(self):
        ir = parse_automation("当 binary_sensor.motion 变为「on」 且 binary_sensor.door 变为「off」\n"
                              "然后：打开 light.hall")
        trig = node_of(ir, "on")["trigger"]
        self.assertEqual(trig["type"], "group")
        self.assertEqual(trig["op"], "and")
        self.assertEqual([s["entity_id"] for s in trig["sources"]],
                         ["binary_sensor.motion", "binary_sensor.door"])

    def test_group_trigger_or(self):
        ir = parse_automation("当 binary_sensor.a 变为「on」 或 binary_sensor.b 变为「on」\n"
                              "然后：打开 light.x")
        self.assertEqual(node_of(ir, "on")["trigger"]["op"], "or")

    def test_do_maps_verb_plus_domain(self):
        cases = [
            ("打开 switch.a", "switch.turn_on"),
            ("关闭 light.a", "light.turn_off"),
            ("切换 fan.a", "fan.toggle"),
            ("打开 cover.a", "cover.open_cover"),
            ("上锁 lock.a", "lock.lock"),
            ("开始清扫 vacuum.a", "vacuum.start"),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                ir = parse_automation(f"当 binary_sensor.x 变为「on」\n然后：{text}")
                self.assertEqual(node_of(ir, "do")["action"], expected)

    def test_do_numeric_param(self):
        ir = parse_automation("每天 07:30\n然后：把空调设为 26 度 climate.living_room")
        do = node_of(ir, "do")
        self.assertEqual(do["action"], "climate.set_temperature")
        self.assertEqual(do["params"], {"entity_id": "climate.living_room", "temperature": 26})

    def test_do_verb_only_action(self):
        ir = parse_automation("每天 08:00\n然后：发送通知「该吃药了」")
        do = node_of(ir, "do")
        self.assertEqual(do["action"], "notify.notify")
        self.assertEqual(do["params"], {"message": "该吃药了"})

    def test_do_explicit_action_ref(self):
        ir = parse_automation("每天 08:00\n然后：动作 custom.do_thing，对 light.a")
        do = node_of(ir, "do")
        self.assertEqual(do["action"], "custom.do_thing")
        self.assertEqual(do["params"], {"entity_id": "light.a"})

    def test_if_comparison_expr(self):
        ir = parse_automation("当 binary_sensor.motion 变为「on」\n"
                              "然后：如果 temperature.sensor 等于「20」")
        expr = node_of(ir, "if")["expr"]
        self.assertEqual(expr["op"], "eq")
        self.assertEqual(expr["left"], {"var": "temperature.sensor", "type": "string"})
        self.assertEqual(expr["right"], {"const": "20"})

    def test_if_unary_and_not(self):
        ir = parse_automation("当 binary_sensor.motion 变为「on」\n然后：如果 light.a 为开")
        self.assertEqual(node_of(ir, "if")["expr"],
                         {"op": "is_on", "value": {"var": "light.a"}})
        ir2 = parse_automation("当 binary_sensor.motion 变为「on」\n然后：如果并非 light.a 为开")
        self.assertEqual(node_of(ir2, "if")["expr"],
                         {"op": "not", "args": [{"op": "is_on", "value": {"var": "light.a"}}]})

    def test_if_logic_operators(self):
        ir = parse_automation("当 binary_sensor.motion 变为「on」\n"
                              "然后：如果 light.a 为开 且 light.b 为关")
        expr = node_of(ir, "if")["expr"]
        self.assertEqual(expr["op"], "and")
        self.assertEqual(len(expr["args"]), 2)

    def test_if_numeric_needs_typed_operand(self):
        ir = parse_automation("当 binary_sensor.motion 变为「on」\n"
                              "然后：如果 sensor.temp 高于 27")
        expr = node_of(ir, "if")["expr"]
        self.assertEqual(expr["op"], "gt")
        self.assertEqual(expr["left"], {"var": "sensor.temp", "type": "numeric"})
        self.assertEqual(expr["right"], {"const": 27})

    def test_ask_prompt_and_session(self):
        ir = parse_automation("当 binary_sensor.motion 变为「on」\n"
                              "然后：询问：要开灯吗（会话 room，超时 5 分钟）")
        ask = node_of(ir, "ask")
        self.assertEqual(ask["prompt"], "要开灯吗")
        self.assertEqual(ask["session"], "room")
        self.assertEqual(duration_seconds(ask["timeout"]), 300.0)

    def test_ask_structured_choice(self):
        ir = parse_automation("当 binary_sensor.motion 变为「on」\n"
                              "然后：询问：开哪个灯（选项：客厅、卧室）")
        self.assertEqual(node_of(ir, "ask")["ask"],
                         {"kind": "choice", "options": ["客厅", "卧室"]})

    def test_ask_structured_threshold(self):
        ir = parse_automation("当 binary_sensor.motion 变为「on」\n"
                              "然后：询问：调到几度（数值范围 16 到 30，单位 度）")
        spec = node_of(ir, "ask")["ask"]
        self.assertEqual(spec["kind"], "threshold")
        self.assertEqual((spec["min"], spec["max"], spec["unit"]), (16.0, 30.0, "度"))

    def test_wait_duration(self):
        ir = parse_automation("每天 22:00\n然后：等待 5 分钟")
        self.assertEqual(duration_seconds(node_of(ir, "wait")["duration"]), 300.0)

    def test_set_var_and_value(self):
        ir = parse_automation("当 binary_sensor.motion 变为「on」\n然后：设置 counter 为 5")
        st = node_of(ir, "set")
        self.assertEqual(st["var"], "counter")
        self.assertEqual(st["value"], 5)

    def test_pass_node(self):
        ir = parse_automation("收到事件「af.test」\n然后：什么都不做")
        self.assertEqual(node_of(ir, "pass")["kind"], "pass")

    def test_group_container_with_child(self):
        nl = (
            "当 binary_sensor.motion 变为「on」\n"
            "然后：编组（sequence）\n"
            "  子自动化「开走廊灯」\n"
            "    当 binary_sensor.door 变为「on」\n"
            "    然后：打开 light.hall"
        )
        ir = parse_automation(nl)
        grp = node_of(ir, "group")
        self.assertEqual(grp["mode"], "sequence")
        self.assertEqual(len(grp["children"]), 1)
        child = grp["children"][0]
        self.assertEqual(child["name"], "开走廊灯")
        self.assertEqual([n["kind"] for n in child["nodes"]], ["on", "do"])
        self.assertEqual(child["edges"][0]["kind"], "then")

    def test_edge_prefixes_all_seven(self):
        nl = (
            "当 binary_sensor.motion 变为「on」\n"
            "然后：询问：好吗\n"
            "  如果你同意：打开 light.a\n"
            "  如果你拒绝：关闭 light.a\n"
            "  如果没听清你的回答：关闭 light.a\n"
            "  如果超时：关闭 light.a\n"
            "  如果中途被打断：关闭 light.a\n"
            "  如果执行失败：关闭 light.a"
        )
        ir = parse_automation(nl)
        # 注：ask 的 default 分支同样是真实边，全局边 kind 集合应含 'default'，
        #     与下方 ask 出边 kind 断言保持一致。
        self.assertEqual(
            sorted({e["kind"] for e in ir["edges"]}),
            ["default", "no", "on_cancel", "on_error", "on_timeout", "then", "yes"],
        )
        ask = node_of(ir, "ask")
        kinds = sorted(e["kind"] for e in ir["edges"] if e["from"] == ask["id"])
        self.assertEqual(kinds, ["default", "no", "on_cancel", "on_error", "on_timeout", "yes"])

    def test_branch_then_chain(self):
        nl = (
            "当 binary_sensor.motion 变为「on」\n"
            "然后：如果 light.a 为开\n"
            "  如果你同意：打开 light.b\n"
            "    然后：等待 1 分钟\n"
            "  如果你拒绝：关闭 light.b"
        )
        ir = parse_automation(nl)
        kinds = sorted(e["kind"] for e in ir["edges"])
        self.assertEqual(kinds, ["no", "then", "then", "yes"])
        self.assertEqual(len(ir["nodes"]), 5)

    def test_emit_on_pass(self):
        ir = parse_automation("每天 08:00\n然后：发布事件「af.tick」")
        self.assertEqual(node_of(ir, "pass")["emit"], {"event": "af.tick"})


class TestCoverage(unittest.TestCase):
    def test_all_eight_node_kinds_parsed(self):
        nl = (
            "当 binary_sensor.motion 变为「on」\n"
            "然后：如果 light.a 为开\n"
            "  如果你同意：询问：好吗\n"
            "    如果你同意：打开 light.b\n"
            "    如果你拒绝：等待 1 分钟\n"
            "  如果你拒绝：设置 counter 为 1\n"
            "    然后：什么都不做\n"
            "    然后：编组（parallel）\n"
            "      子自动化「子一」\n"
            "        当 binary_sensor.door 变为「on」\n"
            "        然后：关闭 light.c"
        )
        ir = parse_automation(nl)
        kinds = {n["kind"] for n in ir["nodes"]}
        self.assertEqual(kinds, {"on", "if", "ask", "do", "wait", "set", "pass", "group"})
        # 子自动化里再确认一遍 group 的 children 合法
        self.assertEqual(len(node_of(ir, "group")["children"]), 1)

    def test_all_five_trigger_types_parsed(self):
        cases = {
            "state": "当 binary_sensor.x 变为「on」",
            "sun": "日落时",
            "time": "每天 21:00",
            "event": "收到事件「af.x」",
            "group": "当 binary_sensor.x 变为「on」 且 binary_sensor.y 变为「off」",
        }
        for expected, line in cases.items():
            with self.subTest(trigger=expected):
                ir = parse_automation(f"{line}\n然后：打开 light.a")
                self.assertEqual(node_of(ir, "on")["trigger"]["type"], expected)


class TestIrreversible(unittest.TestCase):
    def test_runtime_note_marks_non_reversible(self):
        nl = "当 binary_sensor.motion 变为「on」\n然后：打开 light.a（[运行时] 字段：stage，非 NL 可逆核心）"
        ir = parse_automation(nl)
        do = node_of(ir, "do")
        self.assertTrue(do.get(NON_REVERSIBLE_KEY))
        self.assertNotIn("stage", do)

    def test_parser_never_writes_runtime_fields(self):
        nl = "每天 08:00\n然后：打开 light.a\n（[运行时] 字段：stage、diff_sha，非 NL 可逆核心）"
        ir = parse_automation(nl)
        for node in ir["nodes"]:
            for key in RUNTIME_ONLY_FIELDS:
                self.assertNotIn(key, node)
        self.assertTrue(node_of(ir, "do").get(NON_REVERSIBLE_KEY))

    def test_keep_runtime_from_preserves_fields(self):
        src = {
            "nodes": [
                {"id": "t", "kind": "on", "trigger": {"type": "time", "at": "08:00"},
                 "stage": "sim"},
                {"id": "a", "kind": "do", "adapter": "homeassistant",
                 "action": "light.turn_on", "params": {"entity_id": "light.a"},
                 NON_REVERSIBLE_KEY: True},
            ]
        }
        ir = parse_automation("每天 08:00\n然后：打开 light.a", keep_runtime_from=src)
        self.assertEqual(ir["nodes"][0].get("stage"), "sim")
        self.assertTrue(ir["nodes"][1].get(NON_REVERSIBLE_KEY))

    def test_runtime_fields_rejected_without_optin(self):
        # 反向保护：不给 keep_runtime_from 时，输出里绝不出现运行时字段
        ir = parse_automation("每天 08:00\n然后：打开 light.a")
        blob = repr(ir)
        for key in RUNTIME_ONLY_FIELDS:
            self.assertNotIn(f"'{key}'", blob)


class TestValidationAndErrors(unittest.TestCase):
    def test_output_passes_schema_validation(self):
        samples = [
            "当 binary_sensor.motion 变为「on」\n然后：打开 light.a",
            "每天 07:30\n然后：等待 5 分钟",
            "日出时\n然后：设置 x 为 1",
            "收到事件「af.x」\n然后：询问：好吗\n  如果你同意：打开 light.a",
        ]
        for nl in samples:
            with self.subTest(nl=nl):
                ir = parse_automation(nl)
                validate_automation(ir)   # 不抛 IRValidationError 即通过

    def test_every_seed_validates(self):
        for s in SEEDS:
            with self.subTest(seed=s["id"]):
                validate_automation(s)

    def test_error_empty_input(self):
        with self.assertRaises(ParseError):
            parse_automation("   \n  \n")

    def test_error_unknown_statement(self):
        with self.assertRaises(ParseError):
            parse_automation("当 binary_sensor.x 变为「on」\n然后：这是一句无法识别的废话内容")

    def test_error_duplicate_edge_kind(self):
        nl = (
            "当 binary_sensor.motion 变为「on」\n"
            "然后：询问：好吗\n"
            "  如果你同意：打开 light.a\n"
            "  如果你同意：打开 light.b"
        )
        with self.assertRaises(ParseError):
            parse_automation(nl)

    def test_error_ask_without_prompt(self):
        with self.assertRaises(ParseError):
            parse_automation("每天 08:00\n然后：询问：")

    def test_error_group_without_children(self):
        with self.assertRaises(ParseError):
            parse_automation("每天 08:00\n然后：编组（parallel）")

    def test_error_orphan_branch_prefix(self):
        with self.assertRaises(ParseError):
            parse_automation("如果你同意：打开 light.a")

    def test_parse_graph_multiple(self):
        nl = (
            "自动化「甲」\n当 binary_sensor.a 变为「on」\n然后：打开 light.a\n"
            "自动化「乙」\n每天 08:00\n然后：关闭 light.b"
        )
        graphs = parse_graph(nl)
        self.assertEqual(len(graphs), 2)
        self.assertEqual([g["name"] for g in graphs], ["甲", "乙"])


class TestRoundTrip(unittest.TestCase):
    def test_roundtrip_core_seeds(self):
        for s in SEEDS:
            with self.subTest(seed=s["id"]):
                nl = render_automation(_load(s)).text
                ir2 = parse_automation(nl)
                validate_automation(ir2)
                assert_core_equal(s, ir2)

    def test_roundtrip_node_and_edge_counts(self):
        for s in SEEDS:
            with self.subTest(seed=s["id"]):
                ir2 = parse_automation(render_automation(_load(s)).text)
                self.assertEqual(len(ir2["nodes"]), len(s["nodes"]))
                self.assertEqual(len(ir2["edges"]), len(s["edges"]))

    def test_roundtrip_group_container(self):
        s = next(x for x in SEEDS if x["id"] == "s_group_container")
        nl = render_automation(_load(s)).text
        ir2 = parse_automation(nl)
        assert_core_equal(s, ir2)

    def test_roundtrip_full_fields(self):
        rich = [
            next(x for x in SEEDS if x["id"] == "s_ask_branches"),
            next(x for x in SEEDS if x["id"] == "s_time_wait"),
            next(x for x in SEEDS if x["id"] == "s_set_chain"),
            next(x for x in SEEDS if x["id"] == "s_if_yes_no"),
        ]
        for s in rich:
            with self.subTest(seed=s["id"]):
                ir2 = parse_automation(render_automation(_load(s)).text)
                assert_full_equal(s, ir2)

    def test_roundtrip_idempotent_on_reparsed_ir(self):
        """parse(render(parse(nl))) == parse(nl)：二次往返不漂移。"""
        nl = ("当 binary_sensor.motion 变为「on」\n"
              "然后：如果 light.a 为开\n"
              "  如果你同意：打开 light.b\n"
              "  如果你拒绝：等待 2 分钟")
        ir1 = parse_automation(nl)
        ir2 = parse_automation(render_automation(_load(ir1)).text)
        assert_full_equal(ir1, ir2)


def _load(ir: dict):
    from autoforge.af_ir import load_automation

    return load_automation(ir)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()