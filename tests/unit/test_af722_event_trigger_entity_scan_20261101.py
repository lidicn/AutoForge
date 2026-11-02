"""AF-722 丙案判据（PM 裁定 `plans/AF_裁定_AF722丙案_event触发源产生_20261101.md`，七腿）。

缺陷：`event` 触发的**总线主体名**（`event.<name>`）经 `Trigger.entity_ids()` 混进
`reads()`（为依赖矩阵/环检测覆盖事件边，af_ir docstring 原文「`event` 类型**不是实体**」），
拿它比**设备目录** ⇒ `ENTITY_NOT_FOUND`（ERROR ⇒ 硬拒归档）——仓内正例
`examples/ir/case_lamp_sync.json` 四枚 event 触发全被拦（线上单条归档同拦）。

丙案口径（⛔ 不许改判据迁就实现）：剔除集合＝**由 `type:"event"` 触发源产生的**
总线主体名（`_event_bus_subjects` 单源，`leaf_triggers()` 展开 group）；其余全部
（state 触发/do 参数里的 `event.*` 字面＝HA 真实 `event` 域）⇒ 照常参与实体校验。
⛔ 判别不是「图里 emit 过」（单条归档 `_load_ir` 只扫本次那条，图级集合恒空）、
⛔ 不是「按前缀」（漏报幽灵+真实域，探针坐实）、⛔ 不是「目录里有没有」（随目录漂）。
"""

from __future__ import annotations

import json
from pathlib import Path

from autoforge.af_bus import EVENT_ENTITY_PREFIX
from autoforge.af_ir import Graph, load_automation
from autoforge.af_scanner import ERROR, StaticScanner

REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "examples" / "ir" / "case_lamp_sync.json"

REAL_ENTITIES = {
    "switch.lumi_cn_lumi_158d000239c546_aq1_on_p_2_1",
    "switch.lumi_cn_lumi_158d000239c546_aq1_on_p_3_1",
}


def _load_graph() -> Graph:
    ir = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    return Graph([load_automation(a) for a in ir["automations"]])


def _errors(result) -> list:
    return [d for d in result.diagnostics if d.level == ERROR]


def _single(ir_dict: dict) -> Graph:
    return Graph([load_automation(ir_dict)])


# ── 正例 1：整包（case_lamp_sync 四枚 event 触发） ───────────────────

def test_full_pack_zero_entity_errors():
    result = StaticScanner(graph=_load_graph(), known_entities=REAL_ENTITIES).scan()
    assert _errors(result) == [], [f"{d.code}:{d.message[:70]}" for d in _errors(result)]
    assert result.ok is True


# ── 正例 2：拆面（线上单条归档形状——只扫跟随图那一条，图内无 emitter） ──

def test_split_pack_follow_only_passes():
    ir = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    follow = [load_automation(a) for a in ir["automations"]
              if a["id"] in {"lamp_follow_ceiling", "ceiling_follow_lamp"}]
    result = StaticScanner(graph=Graph(follow), known_entities=REAL_ENTITIES).scan()
    assert _errors(result) == [], [f"{d.code}:{d.message[:70]}" for d in _errors(result)]


# ── 腿 A：state 触发形状的幽灵 event.*（⛔ 不因长得像事件被放过） ──────

def test_ghost_event_via_state_trigger_still_flagged():
    ir = {"ir_version": "0.3.0", "id": "ghost_st", "name": "g", "version": 1, "mode": "single",
          "nodes": [{"id": "o1", "kind": "on", "name": "o",
                     "trigger": {"type": "state", "entity_id": "event.ghost_x", "to": "on"}},
                    {"id": "d1", "kind": "do", "name": "d", "adapter": "ha",
                     "action": "light.turn_on", "params": {"entity_id": "light.x"}}],
          "edges": [{"from": "o1", "to": "d1", "kind": "then"}]}
    result = StaticScanner(graph=_single(ir), known_entities={"light.x"}).scan()
    errs = _errors(result)
    assert any(d.code == "ENTITY_NOT_FOUND" and "event.ghost_x" in d.message
               for d in errs), [f"{d.code}:{d.message[:70]}" for d in errs]


# ── 腿 B：do 参数形状的真实 HA event 域（照常参与校验） ───────────────

def test_real_event_domain_via_do_param_still_checked():
    ir = {"ir_version": "0.3.0", "id": "do_ev", "name": "d", "version": 1, "mode": "single",
          "nodes": [{"id": "o1", "kind": "on", "name": "o",
                     "trigger": {"type": "state", "entity_id": "light.x", "to": "on"}},
                    {"id": "d1", "kind": "do", "name": "d", "adapter": "ha",
                     "action": "input_text.set_value", "params": {"entity_id": "event.doorbell"}}],
          "edges": [{"from": "o1", "to": "d1", "kind": "then"}]}
    result = StaticScanner(graph=_single(ir), known_entities={"light.x"}).scan()
    errs = _errors(result)
    assert any(d.code == "ENTITY_NOT_FOUND" and "event.doorbell" in d.message
               for d in errs), [f"{d.code}:{d.message[:70]}" for d in errs]


# ── 腿 C：真实体写错（剔除不许波及 switch/sensor 族） ─────────────────

def test_real_entity_typo_still_flagged():
    ir = {"ir_version": "0.3.0", "id": "typo", "name": "t", "version": 1, "mode": "single",
          "nodes": [{"id": "o1", "kind": "on", "name": "o",
                     "trigger": {"type": "state", "entity_id": "switch.ghost_typo", "to": "on"}},
                    {"id": "d1", "kind": "do", "name": "d", "adapter": "ha",
                     "action": "light.turn_on", "params": {"entity_id": "light.x"}}],
          "edges": [{"from": "o1", "to": "d1", "kind": "then"}]}
    result = StaticScanner(graph=_single(ir), known_entities={"light.x"}).scan()
    errs = _errors(result)
    assert any(d.code == "ENTITY_NOT_FOUND" and "switch.ghost_typo" in d.message
               for d in errs), [f"{d.code}:{d.message[:70]}" for d in errs]


# ── 腿 D：负控——改回「按前缀整族剔」⇒ 腿 A/B 的幽灵必须漏报（monkeypatch） ──

def test_negative_control_prefix_variant_lets_ghost_through():
    def _buggy(self, auto, out):
        from autoforge.af_scanner import Diagnostic

        reads, writes = self._entity_refs(auto)
        refs = {r for r in reads | writes if not r.startswith(EVENT_ENTITY_PREFIX)}
        if self._known is not None:
            for entity_id in sorted(refs):
                if entity_id not in self._known:
                    out.diagnostics.append(Diagnostic(
                        "ENTITY_NOT_FOUND", ERROR,
                        f"引用了不存在的实体：{entity_id}（可能是实体 ID 漂移）", auto.id))

    ghost = {"ir_version": "0.3.0", "id": "ghost_st", "name": "g", "version": 1, "mode": "single",
             "nodes": [{"id": "o1", "kind": "on", "name": "o",
                        "trigger": {"type": "state", "entity_id": "event.ghost_x", "to": "on"}},
                       {"id": "d1", "kind": "do", "name": "d", "adapter": "ha",
                        "action": "light.turn_on", "params": {"entity_id": "light.x"}}],
             "edges": [{"from": "o1", "to": "d1", "kind": "then"}]}
    do_ev = {"ir_version": "0.3.0", "id": "do_ev", "name": "d", "version": 1, "mode": "single",
             "nodes": [{"id": "o1", "kind": "on", "name": "o",
                        "trigger": {"type": "state", "entity_id": "light.x", "to": "on"}},
                       {"id": "d1", "kind": "do", "name": "d", "adapter": "ha",
                        "action": "input_text.set_value", "params": {"entity_id": "event.doorbell"}}],
             "edges": [{"from": "o1", "to": "d1", "kind": "then"}]}
    original = StaticScanner._check_entities
    try:
        StaticScanner._check_entities = _buggy
        errs = _errors(StaticScanner(graph=_single(ghost), known_entities={"light.x"}).scan())
        assert not any(d.code == "ENTITY_NOT_FOUND" for d in errs), "负控：前缀版必须放过幽灵"
        errs = _errors(StaticScanner(graph=_single(do_ev), known_entities={"light.x"}).scan())
        assert not any(d.code == "ENTITY_NOT_FOUND" for d in errs), "负控：前缀版必须放过真实域"
    finally:
        StaticScanner._check_entities = original
    errs = _errors(StaticScanner(graph=_single(ghost), known_entities={"light.x"}).scan())
    assert any(d.code == "ENTITY_NOT_FOUND" for d in errs), "恢复后必须重新拦（负控腿自证）"


# ── 腿 E：空目录退化面照旧（AF-123 假绿通道的反面） ───────────────────

def test_empty_catalog_degraded_path_unchanged():
    ir = {"ir_version": "0.3.0", "id": "evonly", "name": "e", "version": 1, "mode": "single",
          "nodes": [{"id": "o1", "kind": "on", "name": "o",
                     "trigger": {"type": "event", "event": "whatever"}},
                    {"id": "d1", "kind": "do", "name": "d", "adapter": "ha",
                     "action": "light.turn_on", "params": {"entity_id": "light.x"}}],
          "edges": [{"from": "o1", "to": "d1", "kind": "then"}]}
    result = StaticScanner(graph=_single(ir), known_entities=None).scan()
    codes = {d.code for d in result.diagnostics}
    assert "ENTITY_NOT_FOUND" not in codes
    assert "ENTITY_CHECK_DEGRADED" in codes
    assert any("entity_check=skipped" in d.message
               for d in result.diagnostics if d.code == "ENTITY_CHECK_DEGRADED")


# ── 单源锚：判别集合只从 event 触发源来（⛔ 手抄叶子名） ──────────────

def test_event_bus_subjects_single_source():
    ir = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    scanner = StaticScanner(graph=_load_graph(), known_entities=REAL_ENTITIES)
    follow = next(a for a in scanner.graph if a.id == "lamp_follow_ceiling")
    subjects = scanner._event_bus_subjects(follow)
    assert subjects == {f"{EVENT_ENTITY_PREFIX}ceiling_on",
                        f"{EVENT_ENTITY_PREFIX}ceiling_off"}, subjects
