"""体验者复验第二轮 2026-10-29 的三刀判据（AF-014 / AF-015 / AF-001）。

登记册出处 `E:/NAS/QA/AF_Tester/AF_bug_register.md` 第二轮。三条都是**本地复现后**才修的：

- **AF-014**（真 bug）：中文动作名是"域盲"的——`开灯` 配 `switch.abc` 编出
  `light.turn_on`+`entity_id=switch.abc`，真机 HA 拒（实体不属于该域），而仿真器
  从不比对服务域与实体域、照样翻状态 ⇒ 预演绿、真机死。
- **AF-015**（真 bug）：测试通道 `draft_intent(intent)` **没传 catalog**，而目录缓存与
  HA 凭据都挂在主 store 根；测试区 `/data/test` 是空目录 ⇒ 中文名零候选 ⇒ 全落
  `?占位符`。同名字段在正式 af_draft 通道能解析——两条通道不一致。
- **AF-001**（非 bug，是预演不可见）：条件为真时 do 正常自动执行；实例停在
  `suspended` 是因为 L2 动作按策略带 `requires_confirm`。缺陷在预演没把这件事说出来，
  调用方只能读成"链断了"。仿真其实支持 `{"answer": "yes"}` 注入应答。
"""

from __future__ import annotations

import json
import tempfile

import pytest

from autoforge.af_apply import apply
from autoforge.af_draft import _staging, draft_intent
from autoforge.af_service import simulate
from autoforge.af_store import GraphStore
from autoforge.af_test import TestChannel


def _do_node(intent: dict):
    r = draft_intent(intent, catalog=None)
    graph = _staging.get(r["ref"])["graph"]
    return next(x for x in graph.automations[0].nodes.values() if x.kind == "do")


# ── AF-014 · 动作域跟着目标实体走 ────────────────────────────────────

@pytest.mark.parametrize("target,expect", [
    ("switch.abc", "switch.turn_on"),   # 体验者的原案：墙壁开关被叫成"灯"
    ("light.x", "light.turn_on"),       # 本来就是灯 ⇒ 不变
    ("fan.y", "fan.turn_on"),
    ("input_boolean.z", "input_boolean.turn_on"),
])
def test_generic_light_alias_follows_target_domain(target, expect):
    n = _do_node({"name": "t", "when": {"entity": "binary_sensor.m", "to": "off"},
                  "do": {"action": "开灯", "target": target}})
    assert n.action == expect, f"目标 {target} 编出 {n.action}"
    # result_var 必须跟着最终动作名走：否则 agent 读到的变量名与服务名脱节（第二份真值）
    assert n.result_var == expect.replace(".", "_") + "_result"
    assert n.params["entity_id"] == target


def test_off_alias_follows_domain_too():
    n = _do_node({"name": "t", "when": {"entity": "binary_sensor.m", "to": "off"},
                  "do": {"action": "关灯", "target": "switch.abc"}})
    assert n.action == "switch.turn_off"


def test_domain_not_in_effect_table_keeps_default_and_says_so():
    """目标域在效果表里没有 turn_on（vacuum）⇒ **不悄悄换域**，保留别名缺省域。

    换域等于在这里替 HA 决定"这个域有这个服务"；不换则由 build 那一站
    （AF-005 待裁）报"服务/实体不匹配"——问题暴露比问题挪走好。
    """
    n = _do_node({"name": "t", "when": {"entity": "binary_sensor.m", "to": "off"},
                  "do": {"action": "开灯", "target": "vacuum.robot"}})
    assert n.action == "light.turn_on"


def test_explicit_domain_action_not_rewritten():
    """显式写 `light.turn_on` 的意图不该被"好心"改域（那是作者的明确决定）。"""
    n = _do_node({"name": "t", "when": {"entity": "binary_sensor.m", "to": "off"},
                  "do": {"action": "light.turn_on", "target": "switch.abc"}})
    assert n.action == "light.turn_on"


def test_climate_alias_still_maps_to_climate():
    n = _do_node({"name": "t", "when": {"entity": "binary_sensor.m", "to": "off"},
                  "do": {"action": "开空调", "target": "climate.ac"}})
    assert n.action == "climate.turn_on"


# ── AF-015 · 测试通道的名称解析走主 store 目录 ───────────────────────

class _StubCatalog:
    """最小目录替身：只实现 af_draft 用到的 `resolve_best`。"""

    def __init__(self):
        self.calls = 0

    def resolve_best(self, name):
        self.calls += 1
        return "light.书房主灯" if name == "书房灯" else None


def test_test_channel_builds_catalog_from_main_store():
    """`_resolve_catalog` 的取材根 = 测试区的**父目录**（主 store，凭据与目录缓存在那）。"""
    with tempfile.TemporaryDirectory() as td:
        ch = TestChannel(f"{td}/test")
        assert ch.test_root.name == "test"
        cat = ch._resolve_catalog()
        assert cat is None or hasattr(cat, "resolve_best"), type(cat)


def test_test_channel_draft_passes_catalog(monkeypatch):
    """AF-015 本体：`_submit_one` 调 draft 时必须带 catalog（旧实现不传 ⇒ 中文名全 ?）。"""
    seen: dict = {}
    import autoforge.af_test as mod

    def fake_draft(intent, catalog=None, session_id=None):
        seen["catalog"] = catalog
        return {"ok": False, "error": {"code": "STOP_HERE"}}   # 走到 draft 即止

    monkeypatch.setattr(mod, "draft_intent", fake_draft)
    monkeypatch.setattr(mod.TestChannel, "_resolve_catalog", lambda self: "CATALOG_SENTINEL")
    ch = mod.TestChannel(str(seen.setdefault("_root", tempfile.mkdtemp())))
    ch._submit_one({"name": "x", "when": {"entity": "书房灯", "to": "on"},
                    "do": {"action": "开灯", "target": "书房灯"}}, "t1")
    assert seen.get("catalog") == "CATALOG_SENTINEL", "测试通道没把目录递给 draft"


def test_draft_with_catalog_resolves_chinese_name():
    """有目录时中文名解析成真实 entity_id；没有才落 `?占位符`（两条通道同一口径）。"""
    n = _do_node_with_catalog({"name": "t", "when": {"entity": "binary_sensor.m", "to": "off"},
                               "do": {"action": "开灯", "target": "书房灯"}})
    assert n.params["entity_id"] == "light.书房主灯", n.params
    assert "?" not in n.params["entity_id"], "带目录仍留占位符＝解析没接上"


def _do_node_with_catalog(intent):
    r = draft_intent(intent, catalog=_StubCatalog())
    graph = _staging.get(r["ref"])["graph"]
    return next(x for x in graph.automations[0].nodes.values() if x.kind == "do")


# ── AF-001 · 预演把"停在确认闸"说出来 ────────────────────────────────

def _confirm_ref(tmp):
    return draft_intent({"name": "空调", "when": {"entity": "binary_sensor.m", "to": "off"},
                         "do": {"action": "开空调", "target": "climate.ac",
                                "requires_confirm": True, "canary": {"duration": "10m"}}})["ref"]


def test_preview_reports_held_at_confirm_not_silence(tmp_path):
    store = GraphStore(str(tmp_path / "store"))
    r = apply(_confirm_ref(tmp_path), stage="dry_run", store=store)
    assert r.get("ok") is True, str(r)[:200]
    held = r.get("held_at_confirm")
    assert held and held[0]["node"], f"停在确认闸却没上报：{r.get('sim_coverage')}"
    note = str(r.get("confirm_note") or "")
    assert "answer" in note, "要给出去哪条路能验完整链的指路（否则调用方只能读成链断了）"


def test_answer_event_completes_the_chain():
    """对照：注入 `answer` 后整条链跑通——证明"卡住"是可解的，不是引擎缺陷。"""
    ir = {"ir_version": "0.3.0", "id": "t", "name": "t", "version": 1, "mode": "single",
          "nodes": [
              {"id": "a1", "kind": "on", "name": "触发",
               "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "off"}},
              {"id": "d1", "kind": "do", "name": "开空调", "adapter": "ha",
               "action": "climate.turn_on", "params": {"entity_id": "climate.ac"},
               "requires_confirm": True},
              {"id": "p1", "kind": "pass", "name": "结束"}],
          "edges": [{"from": "a1", "to": "d1", "kind": "then"}, {"from": "d1", "to": "p1", "kind": "then"}]}
    base = [{"entity_id": "binary_sensor.m", "state": "off"}]
    r_held = simulate(ir, seed={"binary_sensor.m": "on", "climate.ac": "off"}, events=base)
    r_yes = simulate(ir, seed={"binary_sensor.m": "on", "climate.ac": "off"},
                     events=base + [{"answer": "yes"}])
    held_state = {i.get("state") for i in (r_held.get("instances") or [])}
    done_state = {i.get("state") for i in (r_yes.get("instances") or [])}
    assert held_state == {"suspended"}, held_state
    assert (r_held.get("final_states") or {}).get("climate.ac") == "off", "闸前不该下发"
    assert (r_yes.get("final_states") or {}).get("climate.ac") == "cool", "应答后必须跑完整条"
    assert "suspended" not in done_state
