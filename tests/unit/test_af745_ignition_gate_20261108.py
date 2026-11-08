# -*- coding: utf-8 -*-
"""AF-745 追加判据 · 回执面把「未点火」与「执行失败」分开报（2026-11-08）· 卡名 `AF卡_af745_live_speak`。

**现场**（台账 §1.184 三发实测，PM 亲测其一）：`live_run` 被以**零 events** 调起时，
`_replay_live` 什么都不发布 ⇒ `instances=[]`、**图根本没跑**；而当时回执里只有
`instances: []` 与 `dispatch_result.verdict = nothing_dispatched` 两个读数。这一个档同时罩住
"没点火"、"点了没匹配到条件"、"跑了但没动作"三种情形——操作员分不清「我的调用方式错了」
还是「设备/下发失败」。PM 裁定：回执面必须把「未点火」与「执行失败」分开报。

**修法**：`_ignition_of` 单出一枚 `ignition`（`ignited`／`no_events`／`events_no_trigger`／
`events`／`instances`），回执＋审计流水各带一份，且与 `dispatch_result` **正交**；列表面
`_receipt_of` 透出同一档（老流水缺键 ⇒ 空串＝未知）。

**牙**（群规⑧）：I1/I2/I3 是三个**不同真值**（零 events／有 events 未触发／真点火），必须出
不同读数——⛔ 用一个 `failed` 糊三档；I4 钉"点火成功＋执行失败"仍报 `ignited=true`（⛔ 失败被
说成未点火）；I5 源码钉防回执段被删回去；I6 钉列表面与老流水的未知档（⛔ 乐观回退成 ignited）。
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from autoforge import af_service as svc
from autoforge.af_adapters import CallResult

#: 一条最小真机 IR：门外有人 ⇒ 关书房的灯（动作成功/失败都可控，便于 I3/I4 分档）。
LIVE_IR: dict[str, Any] = {
    "automations": [
        {
            "ir_version": "0.2.1",
            "id": "a_ignition",
            "name": "a_ignition",
            "version": 1,
            "mode": "restart",
            "nodes": [
                {
                    "id": "t1",
                    "kind": "on",
                    "trigger": {"type": "state", "entity_id": "binary_sensor.hall", "to": "on"},
                },
                {
                    "id": "d1",
                    "kind": "do",
                    "adapter": "ha",
                    "action": "light.turn_off",
                    "params": {"entity_id": "light.study"},
                    "on_error": {"default": "pass"},
                    "expect": {"entity_id": "light.study", "state": "off"},
                },
            ],
            "edges": [{"from": "t1", "to": "d1", "kind": "then"}],
        }
    ]
}

HALL_ON = [{"entity_id": "binary_sensor.hall", "state": "on"}]
#: 状态**不匹配**触发条件（触发要 `to: on`）⇒ 有 events 但一个实例都不该起。
HALL_OFF = [{"entity_id": "binary_sensor.hall", "state": "off"}]


class FakeTransport:
    """回放用假传输层：记录下发，可切换"动作失败"。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.states = {"light.study": ("on", {})}
        self.dispatched: list[tuple[str, dict[str, Any]]] = []
        self.fail = fail

    def __call__(self, action: str, params: dict[str, Any]) -> CallResult:
        return self.call(action, params)

    def call(self, action: str, params: dict[str, Any]) -> CallResult:
        self.dispatched.append((action, dict(params)))
        if self.fail:
            return CallResult.fail("HA_TIMEOUT: 假传输层拒收")
        return CallResult.ok({"action": action, "result": []})

    def all_states(self) -> dict[str, Any]:
        return dict(self.states)

    def get_state(self, entity_id: str) -> Any:
        found = self.states.get(entity_id)
        return found[0] if found else None


@pytest.fixture()
def live(monkeypatch):
    """真机路径的三道闸 + 假传输层；`live["fail"]=True` 让动作失败（I4 用）。"""
    monkeypatch.setenv("AUTOFORGE_LIVE_ENABLED", "1")
    monkeypatch.setenv("AUTOFORGE_HA_TOKEN", "srv-side-token")
    monkeypatch.delenv("HA_LONG_LIVED_TOKEN", raising=False)
    monkeypatch.delenv("AUTOFORGE_UNDO", raising=False)
    box: dict[str, Any] = {"fail": False}

    def _factory(ha_url: str, token: str) -> FakeTransport:
        transport = FakeTransport(fail=bool(box.get("fail")))
        box["transport"] = transport
        return transport

    monkeypatch.setattr(svc, "LIVE_TRANSPORT_FACTORY", _factory)
    return box


def _coded(ir, allow, events, **kw):
    """薄壳：短期码签在**本次 store 的同根** `.auth` 上（码闸口径另有套件钉，本文件只量点火面）。"""
    from pathlib import Path

    from autoforge.af_auth import AuthCodeStore

    store = kw["store"]
    rec = AuthCodeStore(Path(store.root) / ".auth" / "auth_codes.json").create("short", ttl_minutes=30)
    kw["auth_code"] = getattr(rec, "code", None) or rec["code"]
    return svc.live_run(ir, allow, events, **kw)


# ── I1 零 events ⇒ 未点火（⛔ 不许写成下发失败）─────────────────────────────

def test_i1_zero_events_reports_not_ignited(tmp_path, live):
    out = _coded(LIVE_IR, ["light.study"], [], confirm=True, store=svc.GraphStore(str(tmp_path)))

    ign = out["ignition"]
    assert ign["ignited"] is False, f"零 events 图没跑，回执却报点火了：{ign}"
    assert ign["reason"] == "no_events", f"未点火的原因必须点到调用方式：{ign}"
    assert ign["events"] == 0 and ign["instances"] == 0
    assert out["instances"] == [], "零 events 居然跑出了实例 —— 前提变了，本判据要重写"

    # ⛔ 关键分档：未点火 ≠ 执行失败。旧读数正是在这一档上把两件事糊成一个 nothing_dispatched。
    assert out["dispatch_result"]["verdict"] == "nothing_dispatched"
    assert out["dispatch_result"]["failed"] == 0, "未点火被记成失败 ⇒ 又糊回旧形状"
    assert out["ok"] is True, "未点火不是下发失败（ok 语义不动）"
    assert not live["transport"].dispatched, "零 events 还碰了设备＝反向越界"


# ── I2 有 events 但触发条件未满足 ⇒ 自己一档（⛔ 与零 events 混同）──────────

def test_i2_events_without_trigger_match_are_their_own_bucket(tmp_path, live):
    out = _coded(LIVE_IR, ["light.study"], HALL_OFF, confirm=True, store=svc.GraphStore(str(tmp_path)))

    ign = out["ignition"]
    assert ign["ignited"] is False, f"条件没匹配却报点火：{ign}"
    assert ign["reason"] == "events_no_trigger", (
        f"有 events 却没起实例 ⇒ 要指向触发条件，而不是说成'没给 events'：{ign}"
    )
    assert ign["events"] == 1 and ign["instances"] == 0
    assert out["instances"] == []


# ── I3 真点火 ⇒ ignited=True，动作照常执行（反腿：防"永远未点火"的假绿）─────

def test_i3_real_ignition_reports_ignited(tmp_path, live):
    out = _coded(LIVE_IR, ["light.study"], HALL_ON, confirm=True, store=svc.GraphStore(str(tmp_path)))

    ign = out["ignition"]
    assert ign["ignited"] is True, f"事件真喂进去了却报未点火：{ign}｜instances={out['instances']}"
    assert ign["reason"] == "", f"点火成功不该带失败原因：{ign}"
    assert ign["instances"] >= 1
    assert out["dispatch_result"]["verdict"] == "executed", "反腿：动作没真下发，I3 就是假绿"


# ── I4 点火成功＋动作失败 ⇒ 两面各自如实报（⛔ 失败被说成未点火）───────────

def test_i4_ignited_stays_true_when_action_fails(tmp_path, live):
    live["fail"] = True
    out = _coded(LIVE_IR, ["light.study"], HALL_ON, confirm=True, store=svc.GraphStore(str(tmp_path)))

    assert out["ignition"]["ignited"] is True, "动作失败不等于没点火（图确实跑了）"
    assert out["dispatch_result"]["verdict"] == "failed", (
        f"动作失败必须留在 dispatch_result 里如实报：{out['dispatch_result']}"
    )


# ── I5 源码钉：回执/流水两面都得带（⛔ 段落被删回去就静默退回旧形状）────────

def test_i5_receipt_and_ledger_carry_ignition_source_pinned():
    src = inspect.getsource(svc.live_run)
    assert '"ignition": ignition' in src, "回执面必须带 ignition 段"
    assert "_ignition_of(" in src, "ignition 必须由 _ignition_of 算（⛔ 就地手搓两处口径会分叉）"
    assert '"ignition": str(ignition["reason"] or "ignited")' in src, "审计流水也要带点火档，列表面才分得开"


# ── I6 列表面：新流水报点火档；老流水缺键 ⇒ 未知（⛔ 不许乐观回退成 ignited）──

def test_i6_receipts_list_face_and_legacy_unknown(tmp_path, live):
    root = str(tmp_path)
    _coded(LIVE_IR, ["light.study"], [], confirm=True, store=svc.GraphStore(root))

    listed = svc.live_run_receipts(root, caller="", caller_is_owner=True, limit=5)
    assert listed["ok"] is True and listed["items"], f"流水没落下来：{listed}"
    assert listed["items"][0]["ignition"] == "no_events", (
        f"列表面读不出'未点火'，UI/事后追查仍然分不清两件事：{listed['items'][0]}"
    )

    # 老流水（本批之前的 LIVE_RUN_EXECUTED 没有这枚键）⇒ 空串＝未知。
    legacy = svc._receipt_of({"at": "2026-11-01T00:00:00+00:00",
                              "data": {"verdict": {"verdict": "executed"}, "actions": 1}})
    assert legacy["ignition"] == "", (
        f"查不出点火档就必须报未知，⛔ 不许回退成 ignited 把'不知道'说成'跑过'：{legacy}"
    )
