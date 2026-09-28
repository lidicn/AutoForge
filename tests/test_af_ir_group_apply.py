"""F9/v2.3 group 复合部署（决策 D 方案 B，并入 F10②）。

覆盖 DCD 裁定验收 §7.2/§7.3 + F10② 组合冲突预检：
- 晚安模式（多条子自动化组成 group）原子部署，单 group ref 回滚单位
- 编排后仿真中途失败 → 已部署部分全回滚（无半部署）
- 组合冲突预检：跨子自动化相反实体操作告警
"""

import pytest

from autoforge.af_ir import Automation
from autoforge.af_orchestrator import compose_group
from autoforge import af_service
from autoforge.af_apply import apply_group

CHILD_OFF = {
    "id": "off_lights", "name": "关所有灯", "version": 1, "mode": "single",
    "nodes": [
        {"id": "on1", "kind": "on", "trigger": {"type": "state", "entity_id": "input_boolean.good_night", "to": "on"}},
        {"id": "d1", "kind": "do", "adapter": "ha", "action": "light.turn_off", "params": {"entity_id": "light.all"}},
    ],
    "edges": [{"from": "on1", "to": "d1", "kind": "then"}],
}
CHILD_LOCK = {
    "id": "lock_door", "name": "锁前门", "version": 1, "mode": "single",
    "nodes": [
        {"id": "on2", "kind": "on", "trigger": {"type": "state", "entity_id": "input_boolean.good_night", "to": "on"}},
        {"id": "d2", "kind": "do", "adapter": "ha", "action": "lock.lock", "params": {"entity_id": "lock.front_door"}},
    ],
    "edges": [{"from": "on2", "to": "d2", "kind": "then"}],
}
CHILD_AC = {
    "id": "ac_off", "name": "关空调", "version": 1, "mode": "single",
    "nodes": [
        {"id": "on3", "kind": "on", "trigger": {"type": "state", "entity_id": "input_boolean.good_night", "to": "on"}},
        {"id": "d3", "kind": "do", "adapter": "ha", "action": "climate.turn_off", "params": {"entity_id": "climate.bedroom"}},
    ],
    "edges": [{"from": "on3", "to": "d3", "kind": "then"}],
}


@pytest.fixture
def fake_pending(monkeypatch):
    rec: list[dict] = []

    def fake_sim(ir, store=None):
        return {"ok": True, "id": ir.get("id")}

    def fake_submit(store, source, payload, submitted_by):
        rec.append(payload)
        return {"ok": True}

    monkeypatch.setattr(af_service, "simulate", fake_sim)
    monkeypatch.setattr(af_service, "submit_pending", fake_submit)
    return rec


def _group():
    return compose_group("晚安模式", [CHILD_OFF, CHILD_LOCK, CHILD_AC])


def test_compose_group_builds_wrapper(fake_pending):
    g = _group()
    gnode = next(n for n in g.nodes.values() if n.kind == "group")
    assert len(gnode.children) == 3


def test_group_atomic_deploy_all_children(fake_pending):
    res = apply_group(_group(), store=object(), stage="apply")
    assert res["ok"]
    assert res["deployed"] == ["off_lights", "lock_door", "ac_off"]
    assert res["ref"] == "grp_placeholder" or res["ref"].startswith("grp_")
    # 单 group ref 回滚单位：所有入队项打同一 group_ref
    assert len(fake_pending) == 3
    assert all(p.get("group_ref") == res["ref"] for p in fake_pending)


def test_group_atomic_rollback_on_sim_failure(fake_pending, monkeypatch):
    def fail_ac(ir, store=None):
        if ir.get("id") == "ac_off":
            return {"ok": False, "reason": "sim boom"}
        return {"ok": True}

    monkeypatch.setattr(af_service, "simulate", fail_ac)
    res = apply_group(_group(), store=object(), stage="apply")
    assert not res["ok"]
    assert res["stage"] == "simulate"
    assert res["error"]["child"] == "ac_off"
    # 原子性：仿真失败 → 任何子自动化都未入队（无半部署）
    assert fake_pending == []


def test_group_conflict_precheck(fake_pending):
    # 两条子自动化对同一灯相反操作 → 组合冲突预检告警
    a = dict(CHILD_OFF)
    b = {
        "id": "on_lights", "name": "开夜灯", "version": 1, "mode": "single",
        "nodes": [
            {"id": "onx", "kind": "on", "trigger": {"type": "state", "entity_id": "input_boolean.good_night", "to": "on"}},
            {"id": "dx", "kind": "do", "adapter": "ha", "action": "light.turn_on", "params": {"entity_id": "light.all"}},
        ],
        "edges": [{"from": "onx", "to": "dx", "kind": "then"}],
    }
    g = compose_group("灯冲突", [a, b])
    res = apply_group(g, store=object(), stage="simulate")
    conflicts = [c for c in res["conflicts"] if c["type"] == "conflict"]
    assert conflicts, "应检出 light.all 相反操作冲突"
    assert conflicts[0]["entity"] == "light.all"
