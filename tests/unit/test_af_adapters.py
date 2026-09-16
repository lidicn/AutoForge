"""af_adapters 单测：纯执行层契约、风险分级、白名单、dry-run。"""

from __future__ import annotations

import pytest

from autoforge.af_adapters import (
    L0_READONLY,
    L1_IDEMPOTENT,
    L2_RISKY,
    L3_DANGEROUS,
    POLICY_PARAMS,
    AdapterError,
    AdapterRegistry,
    CallResult,
    HAAdapter,
    HTTPAdapter,
    MockAdapter,
    classify_action,
    host_of,
    is_destructive,
)


def test_call_result_ok_and_fail():
    ok = CallResult.ok({"a": 1})
    assert ok.success and ok.data == {"a": 1} and ok.error is None
    bad = CallResult.fail("炸了", code=500)
    assert not bad.success and bad.error == "炸了" and bad.data["code"] == 500


@pytest.mark.parametrize(
    "adapter,action,expected",
    [
        ("ha", "light.turn_on", L1_IDEMPOTENT),
        ("ha", "switch.turn_off", L1_IDEMPOTENT),
        ("ha", "lock.lock", L2_RISKY),
        ("ha", "climate.set_temperature", L2_RISKY),
        ("ha", "cover.open_cover", L2_RISKY),
        ("ha", "notify.notify", L0_READONLY),
        ("ha", "shell_command.run", L3_DANGEROUS),
        ("http", "http.get", L3_DANGEROUS),
        ("ha", "rest_command.delete_all", L3_DANGEROUS),
    ],
)
def test_classify_action(adapter, action, expected):
    assert classify_action(adapter, action) == expected


def test_destructive_detection():
    assert is_destructive("http.post delete_all")
    assert not is_destructive("light.turn_on")


def test_policy_params_are_enumerated():
    assert {"retry", "fallback", "timeout"} <= POLICY_PARAMS


def test_registry_unknown_adapter():
    registry = AdapterRegistry({"mock": MockAdapter()})
    with pytest.raises(AdapterError):
        registry.get("nope")


def test_ha_adapter_dry_run_records_intent_only():
    calls = []
    adapter = HAAdapter(dry_run=True, on_dry_run=lambda a, p: calls.append(a))
    result = adapter.call("light.turn_on", {"entity_id": "light.a"})

    assert result.success and result.data["dry_run"] is True
    assert adapter.intents and calls == ["light.turn_on"]


def test_ha_adapter_requires_transport_for_real_call():
    adapter = HAAdapter(dry_run=False)
    with pytest.raises(AdapterError, match="transport"):
        adapter.call("light.turn_on", {"entity_id": "light.a"})


def test_ha_adapter_delegates_to_transport():
    seen = {}

    def transport(action, params):
        seen["action"] = action
        return CallResult.ok({"ok": True})

    adapter = HAAdapter(transport=transport, dry_run=False)
    assert adapter.call("light.turn_on", {"entity_id": "light.a"}).success
    assert seen["action"] == "light.turn_on"


def test_http_adapter_whitelist():
    adapter = HTTPAdapter(allowed_hosts=("safe.example.com",), dry_run=True)
    assert host_of("https://safe.example.com/x") == "safe.example.com"

    bad = adapter.call("http.post", {"url": "https://evil.example.com/x"})
    assert not bad.success and "白名单" in bad.error

    good = adapter.call("http.post", {"url": "https://safe.example.com/x"})
    assert good.success and good.data["dry_run"] is True


def test_mock_adapter_fault_injection():
    adapter = MockAdapter()
    adapter.fail_next("设备离线")
    first = adapter.call("light.turn_on", {"entity_id": "light.a"})
    second = adapter.call("light.turn_on", {"entity_id": "light.a"})

    assert not first.success and first.error == "设备离线"
    assert second.success
    assert len(adapter.calls) == 2
