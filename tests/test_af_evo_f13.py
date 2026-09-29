"""F13 验证：af_evo 提案直接产真 IR（v0.3.0）+ require_shadow_band 默认。

覆盖：
- F13②  EvoPolicy.require_shadow_band 默认 True
- F13①  单自动化提案（merge/adjust/fallback/promote）的 suggested_ir 为合规 IR 文档
- F13①  多自动化（split）的 suggested_ir 为 group 形态（kind:group 节点 + children）
-      提案元数据（revision_of/remove/changes/detail）保留在 meta._evo
-      所有节点 id 符合 ^[A-Za-z][A-Za-z0-9_]*$
"""

from __future__ import annotations

import re

from autoforge.af_evo import EvoPolicy, EvoScanner, EvoStrategy
from autoforge.af_ir.models import validate_automation

_NODE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

TRIG = {"kind": "state", "entity_id": "binary_sensor.door", "to": "on"}


def node(nid, action="light.turn_on", params=None, on_error=True):
    n = {"node_id": nid, "action": action,
         "params": params if params is not None else {"entity_id": "light.hall"}}
    if on_error is not None:
        n["on_error"] = {"kind": "log", "message": "step failed"} if on_error is True else on_error
    return n


def auto(aid, trigger=TRIG, do=None):
    return {"automation_id": aid, "trigger": trigger,
            "do": do if do is not None else [node("n1"), node("n2", action="notify",
                                                          params={"msg": "hi"})]}


class _FakeMgr:
    def submit(self, *, hypothesis_id, natural_language, conf,
               suggested_ir=None, source="ma", proposal_id=None):
        return None


def scanner(graph, **kw):
    kw.setdefault("proposal_manager", _FakeMgr())
    kw.setdefault("simulator", lambda ir: True)
    return EvoScanner(graph=graph, **kw)


class _Ns:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _FakeHealth:
    def __init__(self, scores=None, inputs_map=None):
        self.scores = dict(scores or {})
        self.inputs_map = dict(inputs_map or {})

    def health_score(self, automation_id):
        return self.scores.get(automation_id, 50)

    def inputs(self, automation_id):
        return self.inputs_map.get(automation_id)


class _FakeStats:
    def __init__(self, shadow=None, intervention=None):
        self.shadow = dict(shadow or {})
        self.intervention = dict(intervention or {})

    def shadow_hits(self, automation_id):
        return list(self.shadow.get(automation_id, []))

    def intervention_rate(self, automation_id):
        return self.intervention.get(automation_id)


def _all_node_ids(ir):
    ids = [n["id"] for n in ir.get("nodes", [])]
    for n in ir.get("nodes", []):
        if n.get("kind") == "group":
            for c in n.get("children", []):
                ids += _all_node_ids(c)
    return ids


# ----------------------------------------------------------------------
# F13②
# ----------------------------------------------------------------------

def test_require_shadow_band_default_true():
    assert EvoPolicy().require_shadow_band is True


# ----------------------------------------------------------------------
# F13① 单自动化提案 → 合规 IR 文档
# ----------------------------------------------------------------------

def test_merge_produces_compliant_ir():
    p = [x for x in scanner([auto("auto.a"), auto("auto.b")]).scan()
         if x.strategy is EvoStrategy.MERGE_REDUNDANT][0]
    ir = p.suggested_ir
    assert ir["ir_version"] == "0.3.0"
    for k in ("ir_version", "id", "name", "version", "mode", "nodes", "edges"):
        assert k in ir, k
    validate_automation(ir)
    evo = ir["meta"]["_evo"]
    assert set(evo["revision_of"]) == {"auto.a", "auto.b"}
    assert evo["remove"] == ["auto.b"]
    assert all(_NODE_ID.match(i) for i in _all_node_ids(ir))


def test_adjust_fallback_promote_are_compliant_ir():
    health = _FakeHealth(inputs_map={"auto.hot": _Ns(overrides=4, attributed=10)})
    adj = [x for x in scanner([auto("auto.hot")], health_engine=health).scan()
           if x.strategy is EvoStrategy.ADJUST_TRIGGER][0]
    validate_automation(adj.suggested_ir)
    assert adj.suggested_ir["meta"]["_evo"]["detail"]          # 保留审计信息

    fb = [x for x in scanner([auto("auto.a", do=[node("n1", on_error=None)])]).scan()
          if x.strategy is EvoStrategy.ADD_FALLBACK][0]
    validate_automation(fb.suggested_ir)

    stats = _FakeStats(shadow={"auto.s": [1, 1, 1, 1]})
    pr = [x for x in scanner([auto("auto.s")], executor_stats=stats,
                             policy=EvoPolicy(require_shadow_band=False)).scan()
          if x.strategy is EvoStrategy.PROMOTE_SHADOW][0]
    validate_automation(pr.suggested_ir)
    assert pr.suggested_ir["meta"]["_evo"]["detail"]["band_target"] == "auto"

    for ir in (adj.suggested_ir, fb.suggested_ir, pr.suggested_ir):
        assert all(_NODE_ID.match(i) for i in _all_node_ids(ir))


# ----------------------------------------------------------------------
# F13① 多自动化（split）→ group 形态
# ----------------------------------------------------------------------

def test_split_produces_group_ir():
    p = [x for x in scanner(
        [auto("auto.big", do=[node(f"n{k}") for k in range(6)])]).scan()
         if x.strategy is EvoStrategy.SPLIT_OVERSIZED][0]
    ir = p.suggested_ir
    assert ir["ir_version"] == "0.3.0"
    validate_automation(ir)                       # 顶层合规（mode single + nodes/edges）
    groups = [n for n in ir["nodes"] if n.get("kind") == "group"]
    assert len(groups) == 1
    children = groups[0]["children"]
    assert {c["id"] for c in children} == {"auto_big", "auto_big_part2"}
    for c in children:
        validate_automation(c, root_key="child_automation")   # 锚定 v0.3.0 group 子节点
    assert ir["meta"]["_evo"]["detail"]["parts"] == 2
    assert all(_NODE_ID.match(i) for i in _all_node_ids(ir))
