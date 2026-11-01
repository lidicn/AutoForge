"""C-1 起草期追问（clarify_scan）判据——T01-T15 骨架的落地面（2026-11-01）。

钉死的契约：
- 只读（R2）：扫描前后 intent 深等价；
- 单一真源（R1）：实体判定复用 `af_draft._resolve_entity`（含其 fail-closed 口径）；
- fail-closed（R3）：目录异常/歧义一律"未定"＋无候选，⛔ 不任选；
- 术语隔离（设计 A.2）：本模块无 ask 字样入口；
- 不复活确认闸（设计 7.3）：文案与槽位不含"确认执行"语义；
- 留痕三判据（A1 可重建 / A2 可对账 / A3 可追责）。
"""
from __future__ import annotations

import copy
import json

import pytest

from autoforge.af_clarify import (
    BLOCKING_KINDS,
    ClarifyLog,
    HARD_ROUNDS,
    MAX_CLARIFY_ROUNDS,
    clarify_scan,
    snapshot_hash,
)


class FakeCatalog:
    """目录替身：hits 决定 resolve_best 唯一命中；boom=True 模拟读失败。"""

    def __init__(self, hits=None, boom=False):
        self.hits, self.boom = hits or {}, boom

    def resolve_best(self, name):
        if self.boom:
            raise RuntimeError("catalog down")
        return self.hits.get(name)      # 无歧义才命中；多候选 ⇒ None（不许任选）


def _intent(**kw):
    base = {"when": {"type": "state", "entity": "前门", "to": "on"},
            "do": [{"action": "开灯", "target": "客厅灯"}]}
    base.update(kw)
    return base


# ── R2 只读 ─────────────────────────────────────────────────────────

def test_t01_scan_is_readonly():
    intent = _intent()
    before = copy.deepcopy(intent)
    clarify_scan(intent, FakeCatalog())
    assert intent == before, "扫描改写了意图＝违反 R2 只读"


# ── 实体槽（E1-E4） ─────────────────────────────────────────────────

def test_t02_entity_missing_unknown_and_clear():
    rep = clarify_scan(_intent(when={"type": "state"}), FakeCatalog())
    assert "entity_missing" in {s.kind for s in rep.slots} and rep.blocking()
    rep2 = clarify_scan(_intent(do=[{"action": "开灯", "target": "?客厅灯"}]), FakeCatalog())
    assert "do[0].target" in {s.slot_id for s in rep2.slots}
    rep3 = clarify_scan(_intent(), FakeCatalog({"前门": "binary_sensor.qm", "客厅灯": "light.kt"}))
    assert rep3.is_clear()


def test_t03_ambiguous_never_picks():
    """多候选（resolve_best 返 None）⇒ 槽位出现且不带"被选中"标记（R3 fail-closed）。"""
    rep = clarify_scan(_intent(), FakeCatalog())  # hits 空 ⇒ 未命中
    s = next(s for s in rep.slots if s.slot_id == "when.entity")
    assert s.kind in ("entity_unknown", "entity_ambiguous")
    assert s.resolved_by == "", "未定槽位不得带 resolved_by（⛔ 任选其一）"


def test_t04_catalog_failure_fails_closed_no_candidates():
    s = next(s for s in clarify_scan(_intent(), FakeCatalog(boom=True)).slots
             if s.slot_id == "when.entity")
    assert s.kind in ("entity_unknown", "entity_ambiguous") and s.candidates == ()


def test_full_entity_id_is_auto_not_asked():
    """已是 `域.实体` 的完整 ID ⇒ auto 档，不出槽（设计 §2 不问清单）。"""
    rep = clarify_scan(_intent(when={"type": "state", "entity": "binary_sensor.qm", "to": "on"}),
                       FakeCatalog())
    assert "when.entity" not in {s.slot_id for s in rep.slots}


# ── 动作/参数槽（A1/A2、P1/P2） ─────────────────────────────────────

def test_t05_unknown_zh_action():
    rep = clarify_scan(_intent(do=[{"action": "关射灯", "target": "客厅灯"}]), FakeCatalog())
    assert any(s.kind == "action_unknown" for s in rep.slots)
    rep2 = clarify_scan(_intent(do=[{"action": "开灯", "target": "客厅灯"}]), FakeCatalog())
    assert not any(s.kind == "action_unknown" for s in rep2.slots)


def test_t06_param_required_missing():
    rep = clarify_scan(_intent(do=[{"action": "开空调", "target": "书房空调"}]), FakeCatalog())
    assert any(s.kind == "param_required_missing" for s in rep.slots)
    rep2 = clarify_scan(_intent(do=[{"action": "开空调", "target": "书房空调",
                                     "temperature": 26}]), FakeCatalog())
    assert not any(s.kind == "param_required_missing" for s in rep2.slots)


# ── 条件槽（C1-C3） ─────────────────────────────────────────────────

def test_t07_time_trigger_no_entity_slot():
    rep = clarify_scan({"when": {"type": "time", "at": "07:00"},
                        "do": [{"action": "开灯", "target": "?灯"}]}, FakeCatalog())
    assert {s.slot_id for s in rep.slots} == {"do[0].target"}


def test_t07b_group_trigger_recurses():
    rep = clarify_scan(_intent(when={"type": "group", "sources": [
        {"type": "state"}, ]}), FakeCatalog())
    assert any(s.slot_id == "when.sources[0].entity" for s in rep.slots)


def test_t08_const_missing():
    rep = clarify_scan(_intent(**{"if": {"lt": {"var": "照度"}}}), FakeCatalog())
    assert any(s.kind == "cond_const_missing" for s in rep.slots)
    rep2 = clarify_scan(_intent(**{"if": {"is_on": {"var": "灯"}}}), FakeCatalog())
    assert not any(s.kind == "cond_const_missing" for s in rep2.slots)


def test_c1_state_trigger_without_to_from_is_advisory():
    rep = clarify_scan({"when": {"type": "state", "entity": "binary_sensor.qm"},
                        "do": [{"action": "开灯", "target": "light.kt"}]}, FakeCatalog())
    kinds = {s.kind for s in rep.slots}
    assert "cond_type_implied" in kinds and rep.is_clear(), "advisory 不算 blocking"


# ── 常量与终止 ──────────────────────────────────────────────────────

def test_budgets_match_design():
    assert MAX_CLARIFY_ROUNDS == 3 and HARD_ROUNDS == 5


def test_hint_shape_and_blocking_only():
    rep = clarify_scan(_intent(when={"type": "state"}), FakeCatalog())
    hint = rep.to_hint()
    assert hint["code"] == "E_CLARIFY_NEEDED" and hint["next_action"] == "af_clarify"
    assert set(hint["slots"]) <= {s.slot_id for s in rep.blocking()}


# ── 术语隔离与红线（设计 A.2 / 7.3） ────────────────────────────────

def test_no_ask_entrypoint_in_module():
    import autoforge.af_clarify as m
    public = [n for n in dir(m) if not n.startswith("_")]
    assert not any("ask" in n.lower() for n in public), public


def test_no_confirm_semantics_in_reasons():
    intent = _intent(do=[{"action": "开空调", "target": "书房空调"}])
    rep = clarify_scan(intent, FakeCatalog())
    for s in rep.slots:
        assert "确认" not in s.why, s.why


# ── 留痕（A1/A2/A3） ────────────────────────────────────────────────

def test_log_append_only_and_chain(tmp_path):
    log = ClarifyLog(tmp_path)
    log.append({"session_id": "s1", "round": 1, "outcome": "clarified",
                "snapshot_hash": snapshot_hash(_intent())})
    log.append({"session_id": "s1", "round": 2, "outcome": "defaulted"})
    assert len(log.entries("s1")) == 2 and log.verify_chain()
    assert all("token" not in json.dumps(e) and "auth" not in json.dumps(e).lower()
               for e in log.entries()), "⛔ 留痕不落 token/授权码"


def test_log_reconciles_with_resolved(tmp_path):
    """A2：log 最终值必须能与 draft_intent 的 resolved 同值对账（形状对齐）。"""
    entry = {"session_id": "s1", "round": 1, "outcome": "clarified",
             "answered": [{"slot_id": "when.entity", "value": "binary_sensor.qm",
                           "source": "user"}],
             "snapshot_hash": snapshot_hash(_intent())}
    ClarifyLog(tmp_path).append(entry)
    got = ClarifyLog(tmp_path).entries("s1")[0]["answered"][0]
    assert got["value"] == "binary_sensor.qm" and got["source"] == "user"


def test_blocking_kinds_are_frozen_surface():
    assert set(BLOCKING_KINDS) == {
        "entity_missing", "entity_unknown", "entity_ambiguous",
        "action_unknown", "action_domain_mismatch",
        "param_required_missing", "cond_const_missing"}
