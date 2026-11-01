# -*- coding: utf-8 -*-
"""AF-716 脱敏 fixture 判据（2026-11-01 PM 裁：判据可复跑性缺口，⛔ 不动 QA 计划面真图）。

真图 `plans/study_ac_on_close_hot_light.fixed.json`（QA 计划面，仓内无 plans/ 目录，
不进仓）⇒ CI/全量跑不到；本文件用**同一形状的脱敏图**把三条腿钉进 CI：

  ① 正例腿：三个 do 节点的出边用 on_cancel（撤闸后语义对齐）⇒ build 0 ERROR；
  ② 变异腿：把 if 真值分支 then 改 yes ⇒ 必报 EDGE_KIND_MISMATCH（证闸没被放宽）；
  ③ 反向腿：同一份图保留一枚 if→no 出边 ⇒ 仍 0 ERROR（钉住「no 只在 if 合法」，
     防将来有人为过正例把闸整体放宽）。

静态扫描直跑 StaticScanner（与 af_build 同一判定核，af_cli build 的扫描腿同源）；
变异在字节面做（先确认落进 IR 再断言红，仿 AF-123 自证流程）。
"""

from __future__ import annotations

import copy
import json

from autoforge.af_ir import load_graph
from autoforge.af_scanner import StaticScanner


def _masked_ir() -> dict:
    """脱敏 fixture：形状同书房 v2（t1→c1 if→三个 do 链→p1），实体全假名。"""
    return {
        "ir_version": "0.2.1",
        "id": "af716_masked_shape",
        "name": "af716 脱敏形状",
        "version": 1,
        "mode": "single",
        "nodes": [
            {"id": "t1", "kind": "on", "name": "t", "trigger": {"type": "state", "entity_id": "light.x", "to": "on"}},
            {"id": "c1", "kind": "if", "name": "c",
             "expr": {"op": "gt",
                      "left": {"var": "entity.light.x", "type": "numeric"},
                      "right": {"const": 8}}},
            {"id": "d1", "kind": "do", "name": "d1", "adapter": "mock", "action": "light.turn_on",
             "params": {"entity_id": "light.y"}, "requires_confirm": True,
             "canary": {"duration": "15m", "auto_rollback": True}},
            {"id": "d2", "kind": "do", "name": "d2", "adapter": "mock", "action": "light.turn_off",
             "params": {"entity_id": "light.y"}},
            {"id": "d3", "kind": "do", "name": "d3", "adapter": "mock", "action": "light.turn_on",
             "params": {"entity_id": "light.x"}},
            {"id": "p1", "kind": "pass", "name": "p"},
        ],
        "edges": [
            {"from": "t1", "to": "c1", "kind": "then"},
            {"from": "c1", "to": "d1", "kind": "then"},
            {"from": "c1", "to": "p1", "kind": "no"},
            {"from": "d1", "to": "d2", "kind": "then"},
            {"from": "d1", "to": "p1", "kind": "on_cancel"},
            {"from": "d1", "to": "p1", "kind": "on_timeout"},
            {"from": "d1", "to": "p1", "kind": "on_error"},
            {"from": "d2", "to": "d3", "kind": "then"},
            {"from": "d2", "to": "p1", "kind": "on_cancel"},
            {"from": "d2", "to": "p1", "kind": "on_timeout"},
            {"from": "d2", "to": "p1", "kind": "on_error"},
            {"from": "d3", "to": "p1", "kind": "then"},
            {"from": "d3", "to": "p1", "kind": "on_cancel"},
            {"from": "d3", "to": "p1", "kind": "on_timeout"},
            {"from": "d3", "to": "p1", "kind": "on_error"},
        ],
    }


def _errors(ir: dict) -> list[tuple[str, str]]:
    scan = StaticScanner(load_graph(ir)).scan()
    return [(d.code, d.node_id) for d in scan.diagnostics if d.level == "error"]


def test_af716_leg1_positive_shape_builds_clean():
    """腿①：三个 do 节点带 on_cancel 出边 ⇒ 0 ERROR。"""
    assert _errors(_masked_ir()) == []


def test_af716_leg3_if_no_edge_stays_legal():
    """腿③（反向腿）：同一份图保留 c1(if)→p1 的 no 出边 ⇒ 仍 0 ERROR。

    钉住「no 只在 if 合法」的边界：将来有人为过正例腿把 do 的允许集补 no，
    这条不会跟着红——但若有人把 if 的 no 也判成非法（闸过紧回潮），这条会红。
    """
    ir = _masked_ir()
    no_edges = [e for e in ir["edges"] if e["from"] == "c1" and e["kind"] == "no"]
    assert len(no_edges) == 1, "fixture 必须真带一枚 if→no 出边（反空洞）"
    assert _errors(ir) == []


def test_af716_leg2_mutant_then_to_yes_still_red():
    """腿②：把 if 真值分支 then 改 yes ⇒ 必报 EDGE_KIND_MISMATCH（闸没被放宽）。"""
    ir = _masked_ir()
    ir["edges"] = [dict(e, kind="yes") if (e["from"] == "c1" and e["kind"] == "then")
                   else dict(e) for e in ir["edges"]]
    mutated = [e for e in ir["edges"] if e["from"] == "c1" and e["kind"] == "yes"]
    assert len(mutated) == 1, "变异必须真落进 IR（⛔ 等价变异）"
    errs = _errors(ir)
    assert ("EDGE_KIND_MISMATCH", "c1") in errs, errs


def test_af716_leg2b_do_no_shape_still_rejected():
    """腿②b：do 的 no 出边（撤闸后不可达形状）闸必须仍拦——钉「改正例不等于放行 no」。"""
    ir = _masked_ir()
    target = [e for e in ir["edges"] if e["from"] == "d1" and e["kind"] == "on_cancel"]
    assert len(target) == 1
    target[0]["kind"] = "no"
    assert ("EDGE_KIND_MISMATCH", "d1") in _errors(ir)
