"""af_evo 自进化提案生成器单测（43 条）。

覆盖：五种进化策略各自的正/负/边界、sim 通过入队、sim 失败 rejected、
fail-open / fail-closed 语义、幂等去重、健康度闸门、策略开关。
只依赖标准库 + autoforge.af_evo（零新依赖）。
"""

from __future__ import annotations

from types import SimpleNamespace

from autoforge.af_evo import (
    AUTO_MIN,
    SHADOW_LOW,
    EvoPolicy,
    EvoProposal,
    EvoScanner,
    EvoStatus,
    EvoStrategy,
    action_signature,
    trigger_similarity,
)


# F13①：suggested_ir 已是真 IR 文档（v0.3.0），提案元数据在 meta._evo。
# 以下 helper 从真 IR 形态提取原本 envelope 承载的信息。
def ir_meta(ir):
    return (ir.get("meta") or {}).get("_evo", {})


def ir_do(ir):
    return [n for n in ir.get("nodes", []) if n.get("kind") != "on"]


def ir_on(ir):
    return next((n for n in ir.get("nodes", []) if n.get("kind") == "on"), None)


def ir_children(ir):
    for n in ir.get("nodes", []):
        if n.get("kind") == "group":
            return n.get("children", [])
    return []


# ======================================================================
#  假件（鸭子类型镜像 §1 的公开接口）
# ======================================================================

class FakeHealth:
    """镜像 af_health.HealthEngine 的公开只读口径。"""

    def __init__(self, scores=None, inputs_map=None):
        self.scores = dict(scores or {})
        self.inputs_map = dict(inputs_map or {})

    def health_score(self, automation_id):
        return self.scores.get(automation_id, 50)

    def inputs(self, automation_id):
        return self.inputs_map.get(automation_id)


class BrokenHealth:
    def health_score(self, automation_id):
        raise RuntimeError("health engine down")


class FakeStats:
    """executor_stats 的 duck 形态：shadow 命中史 + 干预率。"""

    def __init__(self, shadow=None, intervention=None):
        self.shadow = dict(shadow or {})
        self.intervention = dict(intervention or {})

    def shadow_hits(self, automation_id):
        return list(self.shadow.get(automation_id, []))

    def intervention_rate(self, automation_id):
        return self.intervention.get(automation_id)


class FakeProposalManager:
    """镜像 af_proposal.ProposalManager.submit 的可见签名。"""

    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def submit(self, *, hypothesis_id, natural_language, conf,
               suggested_ir=None, source="ma", proposal_id=None):
        if self.fail:
            raise RuntimeError("deployer down")
        self.calls.append({
            "hypothesis_id": hypothesis_id,
            "natural_language": natural_language,
            "conf": conf,
            "suggested_ir": suggested_ir,
            "source": source,
            "proposal_id": proposal_id,
        })
        return SimpleNamespace(proposal_id=proposal_id or hypothesis_id,
                               status="pending")


def sim_ok(ir):
    return True


def sim_bad(ir):
    return False


def sim_raises(ir):
    raise RuntimeError("sim crash")


# ======================================================================
#  图构造器
# ======================================================================

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


def scanner(graph, **kw):
    kw.setdefault("proposal_manager", FakeProposalManager())
    kw.setdefault("simulator", sim_ok)
    kw.setdefault("id_factory", (lambda c=iter(range(1000)): lambda: f"{next(c):04d}")())
    return EvoScanner(graph=graph, **kw)


def strategies(proposals):
    return sorted(p.strategy for p in proposals)


# ======================================================================
#  0. 契约面
# ======================================================================

def test_scan_returns_evo_proposal_instances():
    s = scanner([auto("auto.a", do=[node("n1", on_error=None)])])
    out = s.scan()
    assert len(out) == 1
    assert isinstance(out[0], EvoProposal)
    assert out[0].status is EvoStatus.QUEUED
    assert out[0].strategy is EvoStrategy.ADD_FALLBACK


def test_to_dict_contains_contract_keys():
    s = scanner([auto("auto.a", do=[node("n1", on_error=None)])])
    d = s.scan()[0].to_dict()
    for key in ("proposal_id", "automation_id", "strategy", "reason",
                "suggested_ir", "confidence", "status"):
        assert key in d, key
    assert d["strategy"] == "add_fallback"


# ======================================================================
#  1. 合并冗余
# ======================================================================

def test_merge_redundant_similar_trigger_same_action():
    s = scanner([auto("auto.a"), auto("auto.b")])
    merge = [p for p in s.scan() if p.strategy is EvoStrategy.MERGE_REDUNDANT]
    assert len(merge) == 1
    p = merge[0]
    assert p.related_ids == ("auto.a", "auto.b")
    assert set(ir_meta(p.suggested_ir)["revision_of"]) == {"auto.a", "auto.b"}
    assert p.suggested_ir["id"] == "auto_a"


def test_merge_keeps_primary_and_removes_secondary():
    s = scanner([auto("auto.b"), auto("auto.a")])
    p = [x for x in s.scan() if x.strategy is EvoStrategy.MERGE_REDUNDANT][0]
    assert p.automation_id == "auto.a"
    assert ir_meta(p.suggested_ir)["remove"] == ["auto.b"]
    merged = p.suggested_ir                      # 单自动化提案即真 IR 文档
    assert merged["id"] == "auto_a"
    assert ir_on(merged) is not None            # 触发去重后只剩一条 on 节点


def test_merge_skipped_when_actions_differ():
    a = auto("auto.a")
    b = auto("auto.b", do=[node("n1", params={"entity_id": "light.kitchen"}),
                           node("n2", action="notify", params={"msg": "hi"})])
    out = scanner([a, b]).scan()
    assert not [p for p in out if p.strategy is EvoStrategy.MERGE_REDUNDANT]


def test_merge_skipped_when_triggers_dissimilar():
    b = auto("auto.b", trigger={"kind": "time", "at": "07:00"})
    out = scanner([auto("auto.a"), b]).scan()
    assert not [p for p in out if p.strategy is EvoStrategy.MERGE_REDUNDANT]


def test_merge_skipped_when_triggers_missing_by_default():
    a = auto("auto.a", trigger=None)
    b = auto("auto.b", trigger=None)
    out = scanner([a, b]).scan()
    assert not [p for p in out if p.strategy is EvoStrategy.MERGE_REDUNDANT]


def test_merge_allow_empty_triggers_option():
    from autoforge.af_evo import EvoPolicy
    a = auto("auto.a", trigger=None)
    b = auto("auto.b", trigger=None)
    s = scanner([a, b], policy=EvoPolicy(merge_allow_empty_triggers=True))
    out = [p for p in s.scan() if p.strategy is EvoStrategy.MERGE_REDUNDANT]
    assert len(out) == 1
    assert out[0].meta["trigger_similarity"] == 1.0


def test_merge_pairs_are_exclusive():
    s = scanner([auto("auto.a"), auto("auto.b"), auto("auto.c")])
    out = [p for p in s.scan() if p.strategy is EvoStrategy.MERGE_REDUNDANT]
    assert len(out) == 1                     # 每个自动化一次扫描最多并一次


def test_trigger_similarity_metric():
    a = {"kind": "state", "entity_id": "x", "to": "on"}
    b = {"kind": "state", "entity_id": "x", "to": "on", "for": "00:01"}
    c = {"kind": "time", "at": "07:00"}
    assert trigger_similarity(a, a) == 1.0
    assert abs(trigger_similarity(a, b) - 0.75) < 1e-9
    assert trigger_similarity(a, c) == 0.0


def test_missing_triggers_are_not_identical():
    """第六轮缺陷 2：`_jaccard(∅,∅)` 曾返回 1.0，把"共同缺失"读成"完全相同"。

    真实不同的两条（不同房间人体传感器）只有 0.5，若"都没写 trigger"拿到 1.0，
    任何阈值化去重都会把最容易误合并的一对排在最前面。
    """
    from autoforge.af_evo import _jaccard, _trigger_tokens

    assert _jaccard(_trigger_tokens(None), _trigger_tokens({})) == 0.0
    assert trigger_similarity(None, None) == 0.0        # 无证据 ≠ 铁定相同
    assert trigger_similarity({}, {}) == 0.0
    hall = {"kind": "state", "entity_id": "binary_sensor.motion_hall", "to": "on"}
    kitchen = {"kind": "state", "entity_id": "binary_sensor.motion_kitchen", "to": "on"}
    # 对照组：真实不同的两条也只有 0.5，"共同缺失"绝不能高过它
    assert trigger_similarity(hall, kitchen) == 0.5


def test_action_signature_ignores_node_id():
    x = node("n1", params={"entity_id": "light.hall"})
    y = node("totally-different-id", params={"entity_id": "light.hall"})
    assert action_signature(x) == action_signature(y)


# ======================================================================
#  2. 拆分过大
# ======================================================================

def _big(aid, n, with_error=True):
    return auto(aid, do=[node(f"n{k}", on_error={"kind": "log"} if with_error else None)
                         for k in range(n)])


def test_split_triggers_above_five_do_nodes():
    out = scanner([_big("auto.big", 6)]).scan()
    splits = [p for p in out if p.strategy is EvoStrategy.SPLIT_OVERSIZED]
    assert len(splits) == 1
    assert ir_meta(splits[0].suggested_ir)["detail"]["parts"] == 2


def test_split_not_at_five_do_nodes_boundary():
    out = scanner([_big("auto.ok", 5)]).scan()
    assert not [p for p in out if p.strategy is EvoStrategy.SPLIT_OVERSIZED]


def test_split_part_layout_keeps_primary_id():
    p = [x for x in scanner([_big("auto.big", 6)]).scan()
         if x.strategy is EvoStrategy.SPLIT_OVERSIZED][0]
    graph = ir_children(p.suggested_ir)
    assert {c["id"] for c in graph} == {"auto_big", "auto_big_part2"}
    assert [len(ir_do(c)) for c in graph] == [3, 3]
    assert ir_meta(p.suggested_ir)["remove"] == []


def test_split_skipped_without_do_nodes():
    bare = {"automation_id": "auto.bare", "trigger": TRIG, "do": []}
    out = scanner([bare]).scan()
    assert not [p for p in out if p.strategy is EvoStrategy.SPLIT_OVERSIZED]


# ======================================================================
#  3. 调整触发
# ======================================================================

def test_adjust_trigger_above_threshold():
    health = FakeHealth(inputs_map={"auto.hot": SimpleNamespace(overrides=4, attributed=10)})
    s = scanner([auto("auto.hot")], health_engine=health)
    out = [p for p in s.scan() if p.strategy is EvoStrategy.ADJUST_TRIGGER]
    assert len(out) == 1
    assert abs(out[0].meta["intervention_rate"] - 0.4) < 1e-9
    do = ir_do(out[0].suggested_ir)
    assert do[0]["kind"] == "ask" and len(do) == 3


def test_adjust_trigger_boundary_is_strictly_greater():
    health = FakeHealth(inputs_map={"auto.edge": SimpleNamespace(overrides=3, attributed=10)})
    out = scanner([auto("auto.edge")], health_engine=health).scan()
    assert not [p for p in out if p.strategy is EvoStrategy.ADJUST_TRIGGER]


def test_adjust_trigger_unknown_rate_warns_and_skips():
    s = scanner([auto("auto.ghost")], health_engine=BrokenHealth())
    out = s.scan()
    assert not [p for p in out if p.strategy is EvoStrategy.ADJUST_TRIGGER]
    assert [w for w in s.warnings if "干预率" in w]


def test_adjust_trigger_falls_back_to_attempts():
    health = FakeHealth(inputs_map={"auto.a": SimpleNamespace(overrides=4, attributed=None,
                                                             attempts=8)})
    out = scanner([auto("auto.a")], health_engine=health).scan()
    got = [p for p in out if p.strategy is EvoStrategy.ADJUST_TRIGGER]
    assert len(got) == 1 and abs(got[0].meta["intervention_rate"] - 0.5) < 1e-9


# ======================================================================
#  4. 补兜底
# ======================================================================

def test_add_fallback_when_on_error_missing():
    out = scanner([auto("auto.a", do=[node("n1", on_error=None)])]).scan()
    got = [p for p in out if p.strategy is EvoStrategy.ADD_FALLBACK]
    assert len(got) == 1
    assert ir_meta(got[0].suggested_ir)["detail"]["patched_nodes"] == ["n1"]


def test_add_fallback_injects_default_on_error():
    from autoforge.af_evo import EvoPolicy
    pol = EvoPolicy(default_on_error={"kind": "notify", "target": "person.admin"})
    s = scanner([auto("auto.a", do=[node("n1", on_error=None)])], policy=pol)
    p = [x for x in s.scan() if x.strategy is EvoStrategy.ADD_FALLBACK][0]
    got = ir_do(p.suggested_ir)[0]["on_error"]
    assert got == {"kind": "notify", "target": "person.admin"}


def test_add_fallback_not_emitted_when_all_covered():
    out = scanner([auto("auto.safe")]).scan()
    assert not [p for p in out if p.strategy is EvoStrategy.ADD_FALLBACK]


def test_add_fallback_empty_on_error_counts_missing():
    out = scanner([auto("auto.a", do=[node("n1", on_error={})])]).scan()
    assert [p for p in out if p.strategy is EvoStrategy.ADD_FALLBACK]


def test_add_fallback_per_node_mode():
    from autoforge.af_evo import EvoPolicy
    s = scanner([auto("auto.a", do=[node("n1", on_error=None), node("n2", on_error=None)])],
                policy=EvoPolicy(fallback_per_node=True))
    got = [p for p in s.scan() if p.strategy is EvoStrategy.ADD_FALLBACK]
    assert len(got) == 2
    assert {tuple(ir_meta(p.suggested_ir)["detail"]["patched_nodes"]) for p in got} == {("n1",), ("n2",)}


# ======================================================================
#  5. 升档
# ======================================================================

def test_promote_shadow_on_three_consecutive_hits():
    stats = FakeStats(shadow={"auto.s": [1, 1, 1]})
    out = scanner([auto("auto.s")], executor_stats=stats,
                  policy=EvoPolicy(require_shadow_band=False)).scan()
    got = [p for p in out if p.strategy is EvoStrategy.PROMOTE_SHADOW]
    assert len(got) == 1
    assert got[0].meta["shadow_streak"] == 3


def test_promote_shadow_boundary_two_hits():
    stats = FakeStats(shadow={"auto.s": [1, 1]})
    out = scanner([auto("auto.s")], executor_stats=stats,
                  policy=EvoPolicy(require_shadow_band=False)).scan()
    assert not [p for p in out if p.strategy is EvoStrategy.PROMOTE_SHADOW]


def test_promote_shadow_requires_trailing_streak():
    stats = FakeStats(shadow={"auto.s": [1, 1, 1, 0, 1]})
    out = scanner([auto("auto.s")], executor_stats=stats,
                  policy=EvoPolicy(require_shadow_band=False)).scan()
    assert not [p for p in out if p.strategy is EvoStrategy.PROMOTE_SHADOW]


def test_promote_shadow_missing_stats_warns_and_skips():
    s = scanner([auto("auto.s")], executor_stats=object(),
                policy=EvoPolicy(require_shadow_band=False))
    out = s.scan()
    assert not [p for p in out if p.strategy is EvoStrategy.PROMOTE_SHADOW]
    assert [w for w in s.warnings if "shadow" in w]


def test_promote_shadow_targets_auto_min():
    stats = FakeStats(shadow={"auto.s": [1, 1, 1, 1]})
    p = [x for x in scanner([auto("auto.s")], executor_stats=stats,
                             policy=EvoPolicy(require_shadow_band=False)).scan()
         if x.strategy is EvoStrategy.PROMOTE_SHADOW][0]
    detail = ir_meta(p.suggested_ir)["detail"]
    assert detail["confidence_target"] == AUTO_MIN == 0.85
    assert detail["band_target"] == "auto"


# ======================================================================
#  6. sim 验证与入队
# ======================================================================

def _fallback_graph():
    return [auto("auto.a", do=[node("n1", on_error=None)])]


def test_sim_pass_enters_proposal_manager():
    mgr = FakeProposalManager()
    s = scanner(_fallback_graph(), proposal_manager=mgr, simulator=sim_ok)
    p = s.scan()[0]
    assert p.status is EvoStatus.QUEUED
    assert len(mgr.calls) == 1
    call = mgr.calls[0]
    assert call["natural_language"] == p.reason
    assert call["suggested_ir"] is p.suggested_ir
    assert call["source"] == "evo"
    assert call["proposal_id"] == p.proposal_id


def test_sim_fail_marks_rejected_and_never_queues():
    mgr = FakeProposalManager()
    s = scanner(_fallback_graph(), proposal_manager=mgr, simulator=sim_bad)
    p = s.scan()[0]
    assert p.status is EvoStatus.REJECTED
    assert mgr.calls == []


def test_sim_exception_marks_rejected_fail_closed():
    mgr = FakeProposalManager()
    s = scanner(_fallback_graph(), proposal_manager=mgr, simulator=sim_raises)
    p = s.scan()[0]
    assert p.status is EvoStatus.REJECTED
    assert p.sim["mode"] == "error"
    assert mgr.calls == []


def test_sim_missing_marks_deferred():
    mgr = FakeProposalManager()
    s = scanner(_fallback_graph(), proposal_manager=mgr, simulator=None)
    p = s.scan()[0]
    assert p.status is EvoStatus.DEFERRED
    assert mgr.calls == []


def test_sim_missing_allowed_when_not_required():
    from autoforge.af_evo import EvoPolicy
    mgr = FakeProposalManager()
    s = scanner(_fallback_graph(), proposal_manager=mgr, simulator=None,
                policy=EvoPolicy(sim_required=False))
    p = s.scan()[0]
    assert p.status is EvoStatus.QUEUED
    assert len(mgr.calls) == 1


def test_sim_mapping_result_supported():
    mgr = FakeProposalManager()
    ok = scanner(_fallback_graph(), proposal_manager=mgr,
                 simulator=lambda ir: {"ok": True, "failures": []}).scan()[0]
    assert ok.status is EvoStatus.QUEUED
    bad_mgr = FakeProposalManager()
    bad = scanner(_fallback_graph(), proposal_manager=bad_mgr,
                  simulator=lambda ir: {"ok": False, "reason": "写设备"}).scan()[0]
    assert bad.status is EvoStatus.REJECTED
    assert bad_mgr.calls == []


def test_sim_object_result_supported():
    mgr = FakeProposalManager()
    s = scanner(_fallback_graph(), proposal_manager=mgr,
                simulator=lambda ir: SimpleNamespace(ok=True, reason="比对一致"))
    p = s.scan()[0]
    assert p.status is EvoStatus.QUEUED and len(mgr.calls) == 1


def test_sim_single_arg_receives_suggested_ir():
    seen = []
    mgr = FakeProposalManager()
    s = scanner(_fallback_graph(), proposal_manager=mgr,
                simulator=lambda ir: seen.append(ir) or True)
    p = s.scan()[0]
    assert seen and seen[0] is p.suggested_ir


# ======================================================================
#  7. 降级语义 / 幂等 / 闸门
# ======================================================================

def test_submit_failure_marks_error_fail_open():
    s = scanner(_fallback_graph(), proposal_manager=FakeProposalManager(fail=True))
    out = s.scan()                                   # 不抛
    assert out[0].status is EvoStatus.ERROR
    assert "入队失败" in " ".join(s.warnings)


def test_submitted_conf_capped_below_shadow_low():
    stats = FakeStats(shadow={"auto.s": [1] * 20})
    mgr = FakeProposalManager()
    s = scanner([auto("auto.s")], executor_stats=stats, proposal_manager=mgr,
                policy=EvoPolicy(require_shadow_band=False))
    p = [x for x in s.scan() if x.strategy is EvoStrategy.PROMOTE_SHADOW][0]
    assert p.confidence > 0.9
    assert mgr.calls[0]["conf"] == 0.59 < SHADOW_LOW


def test_dedupe_repeated_scan_is_idempotent():
    mgr = FakeProposalManager()
    s = scanner(_fallback_graph(), proposal_manager=mgr)
    first = s.scan()
    second = s.scan()
    assert len(first) == 1 and second == []
    assert len(mgr.calls) == 1


def test_low_health_only_filter():
    from autoforge.af_evo import EvoPolicy
    health = FakeHealth(scores={"auto.a": 20, "auto.b": 90})
    graph = [auto("auto.a", do=[node("n1", on_error=None)]),
             auto("auto.b", do=[node("n1", on_error=None)])]
    s = scanner(graph, health_engine=health, policy=EvoPolicy(low_health_only=True))
    out = s.scan()
    assert {p.automation_id for p in out} == {"auto.a"}


def test_strategy_disable_switch():
    from autoforge.af_evo import EvoPolicy
    graph = [auto("auto.a"), auto("auto.b"),
             auto("auto.c", do=[node("n1", on_error=None)])]
    s = scanner(graph, policy=EvoPolicy(enabled=frozenset({"merge_redundant"})))
    out = s.scan()
    assert strategies(out) == [EvoStrategy.MERGE_REDUNDANT]


def test_scan_fail_open_on_exploding_automation():
    class ExplodingAuto:
        automation_id = "auto.boom"

        @property
        def trigger(self):
            return {"kind": "state"}

        @property
        def do(self):
            raise RuntimeError("boom")

    s = scanner([ExplodingAuto(), auto("auto.ok", do=[node("n1", on_error=None)])])
    out = s.scan()
    assert {p.automation_id for p in out} == {"auto.ok"}
    assert [w for w in s.warnings if "读取失败" in w]


def test_confidence_clamped_to_bounds():
    stats = FakeStats(shadow={"auto.s": [1] * 50})
    graph = [_big("auto.big", 12, with_error=False), auto("auto.s", do=[node("n1", on_error=None)])]
    out = scanner(graph, executor_stats=stats).scan()
    assert out
    for p in out:
        assert 0.05 <= p.confidence <= 0.95


# ======================================================================
#  零依赖 runner：python tests/test_af_evo_strategies.py
# ======================================================================

if __name__ == "__main__":  # pragma: no cover
    import sys
    import traceback

    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception:
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"\ntotal={len(tests)} collectable={len(tests)} ran={len(tests)} failed={failed}")
    sys.exit(1 if failed else 0)