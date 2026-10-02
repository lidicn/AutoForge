"""F14 P1 — Fidelity Verifier 往返保真验收（DCD 20261001《AF 三题》·F，方案 C + 30 样本）。

判据：核心字段 L0 完全相等；params 语义相等（键序无关）；condition L1 结构等价（归一化）。
每条样本：IR→NL（render 确定性 + 全节点覆盖）→IR（结构化投影）必须分层保真。
"""
from __future__ import annotations

import json

from autoforge.af_fidelity import fidelity_equal, project_automation, verify_roundtrip
from autoforge.af_ir import Automation

# ── IR 构造助手：保证图连通（每个节点可从 on 入口经 then 边抵达）────────────
def _on(nid, entity_id, to="on", ttype="state"):
    return {"id": nid, "kind": "on", "trigger": {"type": ttype, "entity_id": entity_id, "to": to}}


def _edge(f, t, kind="then"):
    return {"from": f, "to": t, "kind": kind}


def _cmp(op, var, const):
    return {"op": op, "left": {"var": var}, "right": {"const": const}}


def _is(op, var):
    """一元判定（is_on/is_off/is_home/is_not_home/truthy）：{"op","value"} 形态。"""
    return {"op": op, "value": {"var": var}}


def _and(*a):
    return {"op": "and", "args": list(a)}


def _or(*a):
    return {"op": "or", "args": list(a)}


def _not(a):
    return {"op": "not", "args": [a]}


def _auto(nodes, edges, *, aid="a1", mode="single", ir_version="0.2.1", name=None):
    return {
        "ir_version": ir_version,
        "id": aid,
        "name": name or aid,
        "version": 1,
        "mode": mode,
        "nodes": nodes,
        "edges": edges,
    }


def _chain(on_id, do_id, action, params, *, cond=None, ir_version="0.2.1", aid="a1", extra_nodes=None, extra_edges=None):
    """on → [if(cond)] → do → pass 线性链；extra_nodes 挂在 on 之前无意义，故仅支持 if 前置。"""
    nodes = [_on("n_on", params.get("entity_id") if False else "binary_sensor.presence")]
    edges = []
    prev = "n_on"
    if cond is not None:
        nodes.append({"id": "n_if", "kind": "if", "expr": cond})
        edges.append(_edge(prev, "n_if"))
        prev = "n_if"
    if extra_nodes:
        for en, eid in extra_nodes:
            nodes.append(en)
            edges.append(_edge(prev, eid))
            prev = eid
    nodes.append({"id": do_id, "kind": "do", "adapter": "ha", "action": action, "params": params})
    edges.append(_edge(prev, do_id))
    nodes.append({"id": "n_pass", "kind": "pass"})
    edges.append(_edge(do_id, "n_pass"))
    if extra_edges:
        edges.extend(extra_edges)
    return _auto(nodes, edges, ir_version=ir_version, aid=aid)


# ── 30 条样本：覆盖节点种类 / 条件形态 / 冷门域 / ask / group / http ──────────
def _samples():
    S = []
    # 1-6 简单触发链 + 各域动作
    S.append(_chain("n_on", "n_do", "light.turn_on", {"entity_id": "light.living"}))
    S.append(_chain("n_on", "n_do", "switch.turn_off", {"entity_id": "switch.kettle"}))
    S.append(_chain("n_on", "n_do", "climate.set_temperature", {"entity_id": "climate.bed", "temperature": 24}))
    S.append(_chain("n_on", "n_do", "cover.open_cover", {"entity_id": "cover.blind"}))
    S.append(_chain("n_on", "n_do", "fan.toggle", {"entity_id": "fan.ceiling"}))
    S.append(_chain("n_on", "n_do", "humidifier.set_humidity", {"entity_id": "humidifier.nursery", "humidity": 55}))
    # 7-10 条件（比较 / and / or / not）
    S.append(_chain("n_on", "n_do", "light.turn_on", {"entity_id": "light.a"}, cond=_cmp("gt", "entity.temp", 30)))
    S.append(_chain("n_on", "n_do", "light.turn_on", {"entity_id": "light.b"},
                    cond=_and(_cmp("gt", "entity.temp", 30), _cmp("lt", "entity.humidity", 80))))
    S.append(_chain("n_on", "n_do", "light.turn_on", {"entity_id": "light.c"},
                    cond=_or(_cmp("eq", "entity.mode", "away"), _cmp("eq", "entity.mode", "sleep"))))
    S.append(_chain("n_on", "n_do", "light.turn_on", {"entity_id": "light.d"},
                    cond=_is("is_on", "entity.occupied")))
    # 11-12 冷门域动词 + target 形式
    S.append(_chain("n_on", "n_do", "alarm_control_panel.alarm_arm_away", {"entity_id": "alarm.home"}))
    S.append(_chain("n_on", "n_do", "media_player.volume_set", {"target": {"entity_id": "media.tv"}, "volume_level": 0.4}))
    # 13-14 多重布尔（分配律变体，验证投影保持同一 expr 对象）
    S.append(_chain("n_on", "n_do", "light.turn_on", {"entity_id": "light.e"},
                    cond=_or(_and(_cmp("eq", "a", 1), _cmp("eq", "b", 2)), _cmp("eq", "c", 3))))
    S.append(_chain("n_on", "n_do", "light.turn_on", {"entity_id": "light.f"},
                    cond=_and(_or(_cmp("eq", "a", 1), _cmp("eq", "c", 3)), _or(_cmp("eq", "b", 2), _cmp("eq", "c", 3)))))
    # 15 wait 节点链
    S.append(_auto([_on("n_on", "binary_sensor.door", to="off"),
                    {"id": "n_wait", "kind": "wait", "duration": "5m"},
                    {"id": "n_do", "kind": "do", "adapter": "ha", "action": "notify.notify", "params": {"message": "hi"}},
                    {"id": "n_pass", "kind": "pass"}],
                   [_edge("n_on", "n_wait"), _edge("n_wait", "n_do"), _edge("n_do", "n_pass")]))
    # 16 set 变量链
    S.append(_auto([_on("n_on", "sensor.lux"),
                    {"id": "n_set", "kind": "set", "var": "level", "value": 3},
                    {"id": "n_do", "kind": "do", "adapter": "ha", "action": "light.turn_on", "params": {"entity_id": "light.hall"}},
                    {"id": "n_pass", "kind": "pass"}],
                   [_edge("n_on", "n_set"), _edge("n_set", "n_do"), _edge("n_do", "n_pass")]))
    # 17 ask 节点（yes/no 分支，M3 AskSpec）
    S.append(_auto([_on("n_on", "binary_sensor.motion"),
                    {"id": "n_ask", "kind": "ask", "prompt": "开灯吗？", "room": "客厅",
                     "ask": {"kind": "choice", "options": ["是", "否"]}},
                    {"id": "n_do", "kind": "do", "adapter": "ha", "action": "light.turn_on", "params": {"entity_id": "light.l"}},
                    {"id": "n_pass", "kind": "pass"}],
                   [_edge("n_on", "n_ask"), _edge("n_ask", "n_do", "yes"),
                    _edge("n_ask", "n_pass", "no"), _edge("n_do", "n_pass")]))
    # 18 http adapter 外网请求
    S.append(_auto([_on("n_on", "binary_sensor.button"),
                    {"id": "n_do", "kind": "do", "adapter": "http", "action": "http.post",
                     "params": {"url": "https://example.com/hook"}},
                    {"id": "n_pass", "kind": "pass"}],
                   [_edge("n_on", "n_do"), _edge("n_do", "n_pass")]))
    # 19 emit 跨自动化事件
    S.append(_auto([_on("n_on", "binary_sensor.presence"),
                    {"id": "n_do", "kind": "do", "adapter": "ha", "action": "switch.turn_on", "params": {"entity_id": "switch.x"},
                     "emit": {"event": "welcome", "data": {"who": "guest"}}},
                    {"id": "n_pass", "kind": "pass"}],
                   [_edge("n_on", "n_do"), _edge("n_do", "n_pass")]))
    # 20 sun 触发
    S.append(_auto([{"id": "n_on", "kind": "on", "trigger": {"type": "sun", "event": "sunset"}},
                    {"id": "n_do", "kind": "do", "adapter": "ha", "action": "light.turn_on", "params": {"entity_id": "light.yard"}},
                    {"id": "n_pass", "kind": "pass"}],
                   [_edge("n_on", "n_do"), _edge("n_do", "n_pass")]))
    # 21 time 触发
    S.append(_auto([{"id": "n_on", "kind": "on", "trigger": {"type": "time", "at": "07:30"}},
                    {"id": "n_do", "kind": "do", "adapter": "ha", "action": "scene.turn_on", "params": {"entity_id": "scene.morning"}},
                    {"id": "n_pass", "kind": "pass"}],
                   [_edge("n_on", "n_do"), _edge("n_do", "n_pass")]))
    # 22 trigger group（多源 and）
    S.append(_auto([{"id": "n_on", "kind": "on",
                     "trigger": {"type": "group", "op": "and", "sources": [
                         {"type": "state", "entity_id": "binary_sensor.presence", "to": "on"},
                         {"type": "sun", "event": "sunset"}]}},
                    {"id": "n_do", "kind": "do", "adapter": "ha", "action": "light.turn_on", "params": {"entity_id": "light.g"}},
                    {"id": "n_pass", "kind": "pass"}],
                   [_edge("n_on", "n_do"), _edge("n_do", "n_pass")]))
    # 23 for 持续条件
    S.append(_auto([{"id": "n_on", "kind": "on", "for": "10m",
                     "trigger": {"type": "state", "entity_id": "binary_sensor.co", "to": "on"}},
                    {"id": "n_do", "kind": "do", "adapter": "ha", "action": "alarm_control_panel.alarm_trigger", "params": {"entity_id": "alarm.h"}},
                    {"id": "n_pass", "kind": "pass"}],
                   [_edge("n_on", "n_do"), _edge("n_do", "n_pass")]))
    # 24 vacuum 域
    S.append(_chain("n_on", "n_do", "vacuum.return_to_base", {"entity_id": "vacuum.robot"}))
    # 25 water_heater 域
    S.append(_chain("n_on", "n_do", "water_heater.set_temperature", {"entity_id": "water_heater.boiler", "temperature": 60}))
    # 26 多 do 并联（on→if→两个 do 都到 pass）
    S.append(_auto([_on("n_on", "binary_sensor.presence"),
                    {"id": "n_if", "kind": "if", "expr": _is("is_on", "entity.p")},
                    {"id": "n_do1", "kind": "do", "adapter": "ha", "action": "light.turn_on", "params": {"entity_id": "light.p1"}},
                    {"id": "n_do2", "kind": "do", "adapter": "ha", "action": "fan.turn_on", "params": {"entity_id": "fan.p1"}},
                    {"id": "n_pass", "kind": "pass"}],
                   [_edge("n_on", "n_if"), _edge("n_if", "n_do1"), _edge("n_if", "n_do2"),
                    _edge("n_do1", "n_pass"), _edge("n_do2", "n_pass")]))
    # 27 group 容器节点（v2.3/F9，ir_version 0.3.0）
    child = {"id": "child1", "name": "child1", "version": 1, "mode": "single",
             "nodes": [_on("c_on", "binary_sensor.x"),
                       {"id": "c_do", "kind": "do", "adapter": "ha", "action": "light.turn_on", "params": {"entity_id": "light.c"}},
                       {"id": "c_pass", "kind": "pass"}],
             "edges": [_edge("c_on", "c_do"), _edge("c_do", "c_pass")]}
    S.append(_auto([_on("n_on", "binary_sensor.presence"),
                    {"id": "n_group", "kind": "group", "name": "compound", "mode": "sequence", "children": [child]},
                    {"id": "n_pass", "kind": "pass"}],
                   [_edge("n_on", "n_group"), _edge("n_group", "n_pass")],
                   ir_version="0.3.0"))
    # 28-30 条件嵌套 + entity 引用比较
    S.append(_chain("n_on", "n_do", "lock.lock", {"entity_id": "lock.front"},
                    cond=_and(_or(_cmp("gt", "entity.temp", 28), _cmp("lt", "entity.hum", 30)), _is("is_on", "entity.night"))))
    S.append(_chain("n_on", "n_do", "cover.close_cover", {"entity_id": "cover.bedroom"},
                    cond=_cmp("gte", "entity.lux", 800)))
    S.append(_chain("n_on", "n_do", "persistent_notification.create", {"message": "提醒", "title": "T"},
                    cond=_not(_and(_is("is_on", "entity.a"), _is("is_on", "entity.b")))))
    return S


def test_30_samples_roundtrip_fidelity():
    samples = _samples()
    assert len(samples) == 30, f"须恰好 30 条样本，实得 {len(samples)}"
    for i, ir in enumerate(samples, 1):
        report = verify_roundtrip(ir)
        assert report.ok, f"样本 #{i} ({ir['id']}) 往返保真失败：{report.detail}"


def test_deterministic_render_and_projection_is_idempotent():
    for i, ir in enumerate(_samples(), 1):
        auto = Automation.from_dict(ir)
        proj = project_automation(auto)
        rebuilt = Automation.from_dict(proj)
        assert fidelity_equal(auto, rebuilt), f"样本 #{i} 投影非保真"
        # 投影两次仍保真（幂等）
        assert fidelity_equal(rebuilt, Automation.from_dict(project_automation(rebuilt)))


# ── 分层判据的判别力：正反例各锁一条 ──────────────────────────────────────
def test_core_field_mutation_fails_l0():
    """改核心字段（action）必须判**失败**——否则保真无意义。"""
    ir = _chain("n_on", "n_do", "light.turn_on", {"entity_id": "light.a"})
    a = Automation.from_dict(ir)
    ir2 = _chain("n_on", "n_do", "light.turn_off", {"entity_id": "light.a"})
    b = Automation.from_dict(ir2)
    assert fidelity_equal(a, b) is False


def test_edge_mutation_fails_l0():
    ir = _chain("n_on", "n_do", "light.turn_on", {"entity_id": "light.a"})
    a = Automation.from_dict(ir)
    ir2 = dict(ir)
    ir2["edges"] = ir["edges"][:-1]  # 去掉 do→pass
    b = Automation.from_dict(ir2)
    assert fidelity_equal(a, b) is False


def test_condition_boolean_variant_is_l1_equivalent():
    """condition 的分配律变形（结构等价）必须判**通过**——这是 L1 语义。"""
    A, B, C = _cmp("eq", "a", 1), _cmp("eq", "b", 2), _cmp("eq", "c", 3)
    ir1 = _chain("n_on", "n_do", "light.turn_on", {"entity_id": "light.x"}, cond=_or(_and(A, B), C))
    ir2 = _chain("n_on", "n_do", "light.turn_on", {"entity_id": "light.x"}, cond=_and(_or(A, C), _or(B, C)))
    assert fidelity_equal(Automation.from_dict(ir1), Automation.from_dict(ir2)) is True


def test_params_key_order_irrelevant():
    """params 键序不同 → 语义相等，判通过。"""
    p1 = {"entity_id": "light.a", "brightness": 200}
    p2 = {"brightness": 200, "entity_id": "light.a"}
    a = Automation.from_dict(_chain("n_on", "n_do", "light.turn_on", p1))
    b = Automation.from_dict(_chain("n_on", "n_do", "light.turn_on", p2))
    assert fidelity_equal(a, b) is True


# ── group 容器：子树必须进保真判据（反例实测能变红，铁律 #8）────────────────
def _group_ir(*, mode="sequence", children=None):
    child = {"id": "child1", "name": "child1", "version": 1, "mode": "single",
             "nodes": [_on("c_on", "binary_sensor.x"),
                       {"id": "c_do", "kind": "do", "adapter": "ha", "action": "light.turn_on",
                        "params": {"entity_id": "light.c"}},
                       {"id": "c_pass", "kind": "pass"}],
             "edges": [_edge("c_on", "c_do"), _edge("c_do", "c_pass")]}
    second = {**child, "id": "child2", "name": "child2"}
    return _auto([_on("n_on", "binary_sensor.presence"),
                  {"id": "n_group", "kind": "group", "name": "compound",
                   "mode": mode, "children": list(children if children is not None else [child])},
                  {"id": "n_pass", "kind": "pass"}],
                 [_edge("n_on", "n_group"), _edge("n_group", "n_pass")],
                 ir_version="0.3.0")


def test_group_projection_keeps_children_and_mode():
    """投影丢弃 children/mode 曾是假绿：现在两者必须出现在投影里，且子树可回读。"""
    auto = Automation.from_dict(_group_ir())
    proj = project_automation(auto)
    gnode = next(n for n in proj["nodes"] if n["id"] == "n_group")
    assert gnode["mode"] == "sequence"
    assert [c["id"] for c in gnode["children"]] == ["child1"]
    assert fidelity_equal(auto, Automation.from_dict(proj))


def test_group_child_added_or_removed_fails_l0():
    """多一个/少一个子自动化必须判失败——只比 kind 会两者都判绿。"""
    one = Automation.from_dict(_group_ir())
    two = Automation.from_dict(_group_ir(children=[
        {"id": "child1", "name": "child1", "version": 1, "mode": "single",
         "nodes": [_on("c_on", "binary_sensor.x"),
                   {"id": "c_do", "kind": "do", "adapter": "ha", "action": "light.turn_on",
                    "params": {"entity_id": "light.c"}},
                   {"id": "c_pass", "kind": "pass"}],
         "edges": [_edge("c_on", "c_do"), _edge("c_do", "c_pass")]},
        {"id": "child2", "name": "child2", "version": 1, "mode": "single",
         "nodes": [_on("c_on", "binary_sensor.x"),
                   {"id": "c_do", "kind": "do", "adapter": "ha", "action": "light.turn_on",
                    "params": {"entity_id": "light.c"}},
                   {"id": "c_pass", "kind": "pass"}],
         "edges": [_edge("c_on", "c_do"), _edge("c_do", "c_pass")]},
    ]))
    assert fidelity_equal(one, two) is False


def test_group_mode_mutation_fails_l0():
    assert fidelity_equal(
        Automation.from_dict(_group_ir(mode="sequence")),
        Automation.from_dict(_group_ir(mode="parallel")),
    ) is False


def test_group_child_action_mutation_fails_l0():
    """子自动化里的 action 改动必须红——否则 group 是保真判据的黑洞。"""
    base = {"id": "child1", "name": "child1", "version": 1, "mode": "single",
            "nodes": [_on("c_on", "binary_sensor.x"),
                      {"id": "c_do", "kind": "do", "adapter": "ha", "action": "light.turn_on",
                       "params": {"entity_id": "light.c"}},
                      {"id": "c_pass", "kind": "pass"}],
            "edges": [_edge("c_on", "c_do"), _edge("c_do", "c_pass")]}
    mutated = json.loads(json.dumps(base))
    mutated["nodes"][1]["action"] = "light.turn_off"
    assert fidelity_equal(
        Automation.from_dict(_group_ir(children=[base])),
        Automation.from_dict(_group_ir(children=[mutated])),
    ) is False
