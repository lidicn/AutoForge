"""AF-722（v1.16.0 批代码先落）：`event` 触发的总线主体名不进设备目录比对判据。

缺陷（PM 2026-11-01 NAS 容器内现读复现）：`Trigger.entity_ids()` 对 `type:"event"`
返回 `event.<name>`——那是**总线主体名**（跨自动化依赖矩阵与环检测语义，af_ir
docstring 原文，⛔ 本批不改它）；`Automation.reads()` 把它收进「读到的实体」，
`_check_entities` 拿去比**设备目录**（只含 HA 实体）⇒ `ENTITY_NOT_FOUND`（ERROR ⇒
硬拒归档）——仓内在册正例 `examples/ir/case_lamp_sync.json` 整包归档必然被拦。

修法＝`_check_entities` 比对前按既有 `EVENT_ENTITY_PREFIX` 前缀剔除事件主体名
（⛔ 不放宽任何真实体校验、⛔ 不改 `DeviceCatalog.known_entity_ids()`、⛔ 不改
`entity_ids()`；环检测那半边不经过本函数，`EMIT_SELF_LOOP` 照旧）。

三腿（两向都有牙）：
① 正例＝`case_lamp_sync` 整包 0 ERROR（4 条 event 触发全过，真实 switch 照常校验）；
② 变异＝真实体 id 写错 ⇒ **仍报** `ENTITY_NOT_FOUND`（⛔ 不许把真实体校验一起放掉）；
③ 反向＝目录空 ⇒ `ENTITY_CHECK_DEGRADED`（entity_check=skipped）退化路径照旧。
"""

from __future__ import annotations

import json
from pathlib import Path

from autoforge.af_bus import EVENT_ENTITY_PREFIX
from autoforge.af_ir import Graph, load_automation
from autoforge.af_scanner import ERROR, StaticScanner

REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "examples" / "ir" / "case_lamp_sync.json"

#: 正例里的两枚真实体（挂灯／吊灯，switch.*；事件名 event.* 不是实体）
REAL_ENTITIES = {
    "switch.lumi_cn_lumi_158d000239c546_aq1_on_p_2_1",
    "switch.lumi_cn_lumi_158d000239c546_aq1_on_p_3_1",
}


def _load_graph() -> Graph:
    ir = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    return Graph([load_automation(a) for a in ir["automations"]])


def _errors(result) -> list:
    return [d for d in result.diagnostics if d.level == ERROR]


def test_event_trigger_full_pack_builds_without_entity_errors():
    """① 正例腿：整包 0 ERROR——4 条 event 触发不再被误拦，真实 switch 照常过。"""
    result = StaticScanner(graph=_load_graph(), known_entities=REAL_ENTITIES).scan()
    errs = _errors(result)
    assert errs == [], [f"{d.code}:{d.message[:60]}" for d in errs]
    assert result.ok is True


def test_real_entity_typo_still_flagged():
    """② 变异腿：真实体 id 写错 ⇒ 仍报 ENTITY_NOT_FOUND（剔除只对 event 前缀生效）。"""
    graph = _load_graph()
    autos = list(graph)
    mutated = False
    for auto in autos:
        for node in auto.nodes.values():
            params = getattr(node, "params", None)
            if isinstance(params, dict) and "switch.lumi" in str(params.get("entity_id", "")):
                params["entity_id"] = "switch.ghost_af722"
                mutated = True
                break
        if mutated:
            break
    assert mutated, "夹具必须真改到一枚真实体引用"
    result = StaticScanner(graph=Graph(autos), known_entities=REAL_ENTITIES).scan()
    errs = _errors(result)
    assert any(d.code == "ENTITY_NOT_FOUND" and "switch.ghost_af722" in d.message
               for d in errs), [f"{d.code}:{d.message[:60]}" for d in errs]
    assert not any(EVENT_ENTITY_PREFIX in d.message for d in errs), "事件主体名不许再进目录比对"


def test_event_subjects_never_reach_catalog_check():
    """① 补充腿：事件主体名（event.<name>）不出现在任何 ENTITY_NOT_FOUND 消息里。"""
    result = StaticScanner(graph=_load_graph(), known_entities=REAL_ENTITIES).scan()
    blob = "\n".join(d.message for d in result.diagnostics)
    assert f"{EVENT_ENTITY_PREFIX}ceiling_on" not in blob
    assert f"{EVENT_ENTITY_PREFIX}lamp_off" not in blob


def test_empty_catalog_degraded_path_unchanged(tmp_path):
    """③ 反向腿：目录为空 ⇒ ENTITY_CHECK_DEGRADED（entity_check=skipped）照旧，零拦截。"""
    result = StaticScanner(graph=_load_graph(), known_entities=None).scan()
    codes = [d.code for d in result.diagnostics]
    assert "ENTITY_NOT_FOUND" not in codes
    assert "ENTITY_CHECK_DEGRADED" in codes, "空目录必须说话（AF-123 假绿通道的反面）"
    assert any("entity_check=skipped" in d.message
               for d in result.diagnostics if d.code == "ENTITY_CHECK_DEGRADED")


def test_event_only_automation_with_empty_catalog_is_quiet():
    """③ 补充腿：纯 event 触发（无真实体）在空目录下不产生实体面诊断（没有可查对象）。"""
    ir = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    autos = [a for a in ir["automations"]
             if any(n.get("trigger", {}).get("type") == "event"
                    for n in a.get("nodes", []))]
    graph = Graph([load_automation(a) for a in autos])
    result = StaticScanner(graph=graph, known_entities=REAL_ENTITIES).scan()
    assert not [d for d in result.diagnostics if d.code == "ENTITY_NOT_FOUND"]
