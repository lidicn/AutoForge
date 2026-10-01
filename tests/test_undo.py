"""F7 下发后通用撤销 / 设备态回滚（决策 E）测试。

覆盖：DOMAIN_SETTER 属性感知映射、UndoStore 落盘 + 时间窗 + 风险域 + fail-closed、
canary 属性感知回滚、HAAdapter 下发前快照捕获钩子、forge undo CLI。
全部零网络（用 fake adapter / state provider）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoforge.af_adapters import CallResult
from autoforge.af_undo import RISK_DOMAINS, DOMAIN_SETTER, RestoreCall, UndoStore, restore_call
from autoforge.af_state import Snapshot


# ── fake 底座 ──────────────────────────────────────────────────────────────
class RecordingAdapter:
    """鸭子类型适配器：记录所有 call，返回 ok。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def call(self, action: str, params: Mapping) -> CallResult:  # type: ignore[name-defined]
        self.calls.append((action, dict(params)))
        return CallResult.ok({"action": action})


class FakeStateProvider:
    """StateProvider 鸭子类型：{entity_id: (state, attributes)}。"""

    def __init__(self, states: dict[str, tuple[str, dict]]) -> None:
        self._states = states

    def snapshot(self, entity_ids) -> Snapshot:  # type: ignore[no-untyped-def]
        values = {e: self._states[e][0] for e in entity_ids if e in self._states}
        attributes = {e: self._states[e][1] for e in entity_ids if e in self._states}
        return Snapshot(values=values, attributes=attributes)


# ── DOMAIN_SETTER / restore_call ──────────────────────────────────────────
def test_restore_light_on_with_brightness():
    call = restore_call("light.study", {"state": "on", "attributes": {"brightness": 120}})
    # 期望值用 RestoreCall 写死：gap 必须为空才算"完全恢复"
    assert call == RestoreCall(
        "light.turn_on", {"entity_id": "light.study", "brightness": 120})


def test_restore_light_off():
    call = restore_call("light.study", {"state": "off", "attributes": {}})
    assert call == RestoreCall("light.turn_off", {"entity_id": "light.study"})


def test_restore_switch_and_lock():
    assert restore_call("switch.pump", {"state": "on", "attributes": {}}) == (
        RestoreCall("switch.turn_on", {"entity_id": "switch.pump"}))
    assert restore_call("lock.door", {"state": "locked", "attributes": {}}) == (
        RestoreCall("lock.lock", {"entity_id": "lock.door"}))


def test_restore_cover_with_position():
    call = restore_call("cover.curtain", {"state": "open", "attributes": {"current_position": 40}})
    assert call == RestoreCall(
        "cover.set_cover_position", {"entity_id": "cover.curtain", "position": 40})


def test_restore_cover_without_position_fail_closed():
    # 无位置信息：无法安全恢复 → None
    assert restore_call("cover.curtain", {"state": "open", "attributes": {}}) is None


def test_restore_climate_with_temperature_and_mode():
    call = restore_call(
        "climate.bedroom",
        {"state": "heat", "attributes": {"temperature": 22.5, "hvac_mode": "heat"}},
    )
    assert call == RestoreCall(
        "climate.set_temperature",
        {"entity_id": "climate.bedroom", "temperature": 22.5, "hvac_mode": "heat"},
    )


def test_restore_fan_with_percentage():
    call = restore_call("fan.ceiling", {"state": "on", "attributes": {"percentage": 50}})
    assert call == RestoreCall(
        "fan.turn_on", {"entity_id": "fan.ceiling", "percentage": 50})


def test_restore_unmappable_domain_fail_closed():
    # binary_sensor / sensor / scene 等无 setter → None
    assert restore_call("binary_sensor.motion", {"state": "on", "attributes": {}}) is None
    assert restore_call("sensor.temp", {"state": "20", "attributes": {}}) is None


# ── UndoStore ─────────────────────────────────────────────────────────────
def test_undo_store_record_revert(tmp_path):
    store = UndoStore(str(tmp_path))
    did = "dep-test1"
    store.record(did, {
        "light.a": {"state": "on", "attributes": {"brightness": 100}},
        "switch.b": {"state": "off", "attributes": {}},
    })
    adapter = RecordingAdapter()
    res = store.revert(did, adapter)
    assert res["ok"]
    assert set(res["restored"]) == {"light.a", "switch.b"}
    # 恢复动作参数化正确（pre-state 是什么就还原什么）
    by_entity = {c[1]["entity_id"]: c[0] for c in adapter.calls}
    assert by_entity["light.a"] == "light.turn_on"
    assert by_entity["switch.b"] == "switch.turn_off"  # 预态 off → 还原关
    # 落盘持久化：新实例能读到
    store2 = UndoStore(str(tmp_path))
    assert store2.exists(did)


def test_undo_store_expired_window(tmp_path):
    store = UndoStore(str(tmp_path), window_s=0.05)
    did = "dep-exp"
    store.record(did, {"light.a": {"state": "on", "attributes": {}}})
    import time
    time.sleep(0.1)
    res = store.revert(did, RecordingAdapter())
    assert not res["ok"]
    assert res["reason"] == "expired"


def test_undo_store_risk_domain_requires_confirm(tmp_path):
    store = UndoStore(str(tmp_path))
    did = "dep-risk"
    store.record(did, {"climate.bedroom": {"state": "heat", "attributes": {"temperature": 22}}})
    # 未 confirm → 拒绝
    res = store.revert(did, RecordingAdapter(), confirm=False)
    assert not res["ok"]
    assert res["reason"] == "risk_domain_requires_confirm"
    assert "climate.bedroom" in res["risk_entities"]
    # confirm → 放行
    adapter = RecordingAdapter()
    res2 = store.revert(did, adapter, confirm=True)
    assert res2["ok"]
    assert "climate.bedroom" in res2["restored"]


def test_undo_store_unknown_id(tmp_path):
    store = UndoStore(str(tmp_path))
    res = store.revert("nope", RecordingAdapter())
    assert not res["ok"]
    assert res["reason"] == "unknown_deploy_id"


def test_undo_store_unmappable_skipped_others_restored(tmp_path):
    store = UndoStore(str(tmp_path))
    did = "dep-mix"
    store.record(did, {
        "switch.ok": {"state": "on", "attributes": {}},
        "binary_sensor.bad": {"state": "on", "attributes": {}},  # 不可映射
    })
    adapter = RecordingAdapter()
    res = store.revert(did, adapter)
    assert res["ok"]
    assert res["restored"] == ["switch.ok"]
    assert res["skipped"] == ["binary_sensor.bad"]


def test_undo_store_record_merge_keeps_first_snapshot(tmp_path):
    store = UndoStore(str(tmp_path))
    did = "dep-merge"
    store.record_merge(did, {"light.a": {"state": "on", "attributes": {}}})
    # 同实体二次出现（部署中又动作了一次）→ 保留首次（部署前）快照
    store.record_merge(did, {"light.a": {"state": "off", "attributes": {}}})
    assert store.get(did)["entities"]["light.a"]["state"] == "on"


# ── canary 属性感知回滚 ───────────────────────────────────────────────────
def test_canary_rollback_attribute_aware():
    from autoforge.af_canary import CanaryGuard, CanaryResult

    provider = FakeStateProvider({
        "light.study": ("on", {"brightness": 120}),
    })
    guard = CanaryGuard(states=provider, auto_rollback=True)
    adapter = RecordingAdapter()
    # 模拟"关灯"动作（pre_snapshot 由 guard 在动作前抓取）
    res = guard.perform(adapter, "light.turn_off", {"entity_id": "light.study"})
    assert isinstance(res, CanaryResult)
    assert res.pre_snapshot["light.study"]["state"] == "on"
    assert res.pre_snapshot["light.study"]["attributes"]["brightness"] == 120
    # 回滚应还原到"开 + 亮度 120"
    out = res.rollback(adapter)
    assert out
    # 最后一次 call 是还原亮度的 turn_on
    assert adapter.calls[-1][0] == "light.turn_on"
    assert adapter.calls[-1][1]["brightness"] == 120


# ── HAAdapter 下发前快照捕获钩子 ──────────────────────────────────────────
def test_ha_adapter_undo_recorder_hook():
    from autoforge.af_adapters import HAAdapter

    captured = {}

    class FakeTransport:
        def __call__(self, action, params):
            captured.setdefault("dispatched", []).append((action, dict(params)))
            return CallResult.ok({"action": action})

        def all_states(self):
            return {"light.x": ("on", {"brightness": 200})}

    adapter = HAAdapter(transport=FakeTransport(), dry_run=False)

    def recorder(action, params, pre):
        captured["pre"] = pre

    adapter.undo_recorder = recorder
    adapter.call("light.turn_off", {"entity_id": "light.x"})
    assert captured.get("pre") == {"light.x": {"state": "on", "attributes": {"brightness": 200}}}
    assert captured["dispatched"][0][0] == "light.turn_off"


def test_ha_adapter_undo_recorder_not_fired_in_dry_run():
    from autoforge.af_adapters import HAAdapter

    fired = []

    class FakeTransport:
        def __call__(self, action, params):
            return CallResult.ok({})

        def all_states(self):
            return {"light.x": ("on", {})}

    adapter = HAAdapter(transport=FakeTransport(), dry_run=True)
    adapter.undo_recorder = lambda a, p, pre: fired.append(pre)
    adapter.call("light.turn_off", {"entity_id": "light.x"})
    assert fired == []  # dry_run 不触发真实下发前捕获


# ── CLI forge undo ────────────────────────────────────────────────────────
def test_cli_undo_unknown_id(tmp_path):
    from typer.testing import CliRunner

    from autoforge.af_cli import app

    runner = CliRunner()
    r = runner.invoke(app, ["undo", "dep-nope", "--root", str(tmp_path)])
    assert r.exit_code != 0
    assert "未知 deploy_id" in r.output


def test_cli_undo_risk_requires_confirm(tmp_path):
    from typer.testing import CliRunner

    from autoforge.af_cli import app
    from autoforge.af_undo import UndoStore

    store = UndoStore(str(tmp_path))
    did = "dep-cli-risk"
    store.record(did, {"climate.bedroom": {"state": "heat", "attributes": {"temperature": 22}}})

    runner = CliRunner()
    r = runner.invoke(app, ["undo", did, "--root", str(tmp_path)])
    assert r.exit_code != 0
    assert "risk_domain_requires_confirm" in r.output


def test_cli_undo_happy_path(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    import autoforge.af_cli as cli
    from autoforge.af_cli import app
    from autoforge.af_undo import UndoStore

    store = UndoStore(str(tmp_path))
    did = "dep-cli-ok"
    store.record(did, {"switch.a": {"state": "on", "attributes": {}}})

    rec = RecordingAdapter()
    monkeypatch.setattr("autoforge.af_adapters.HAAdapter", lambda *a, **k: rec)

    runner = CliRunner()
    r = runner.invoke(app, ["undo", did, "--root", str(tmp_path), "--ha-url", "http://127.0.0.1:8123"])
    assert r.exit_code == 0, r.output
    assert "switch.a" in r.output
    assert rec.calls and rec.calls[0][0] == "switch.turn_on"
