"""G2 新增扫描项单测（KICKOFF §4.7 延后 G2 的两项 + IR §8.2 补齐）。

覆盖：⑫ 实体存在性｜① 实体 ACL｜§8.1 L2 强制确认｜§10 低 conf 禁止写设备｜
§6 do 建议 on_error｜§5.3 on_cancel 禁止嵌套挂起｜③ 跨自动化写冲突
"""

from __future__ import annotations

from autoforge.af_ir import Graph, load_automation
from autoforge.af_scanner import ERROR, StaticScanner


def _scan(data: dict, **kw) -> StaticScanner:
    return StaticScanner(Graph([load_automation(data)]), **kw).scan()


def _scan_many(datas: list[dict], **kw) -> StaticScanner:
    return StaticScanner(Graph([load_automation(d) for d in datas]), **kw).scan()


def _base(**over) -> dict:
    data = {
        "ir_version": "0.2.1",
        "id": "demo",
        "name": "示例",
        "version": 1,
        "mode": "single",
        "nodes": [
            {"id": "a1", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
            {
                "id": "d1",
                "kind": "do",
                "adapter": "ha",
                "action": "light.turn_on",
                "params": {"entity_id": "light.a"},
            },
            {"id": "p1", "kind": "pass"},
        ],
        "edges": [
            {"from": "a1", "to": "d1", "kind": "then"},
            {"from": "d1", "to": "p1", "kind": "then"},
        ],
    }
    data.update(over)
    return data


# ── ⑫ 实体存在性校验 ──────────────────────────────────────────────────


def test_entity_not_found_is_error():
    scan = _scan(_base(), known_entities=["light.a", "binary_sensor.m2"])
    assert "ENTITY_NOT_FOUND" in scan.codes()
    assert not scan.ok


def test_entity_exists_passes():
    scan = _scan(_base(), known_entities=["light.a", "binary_sensor.m"])
    assert "ENTITY_NOT_FOUND" not in scan.codes()


def test_entity_check_skipped_without_catalog():
    """离线编写 IR 时没有实体清单 → 跳过该项，不误报。"""
    scan = _scan(_base())
    assert "ENTITY_NOT_FOUND" not in scan.codes()


# ── ① 实体读写权限 ────────────────────────────────────────────────────


def test_acl_forbids_write():
    scan = _scan(_base(), entity_acl={"light.a": "r"})
    codes = scan.codes()
    assert "ENTITY_ACL_DENIED" in codes


def test_acl_allows_rw():
    scan = _scan(_base(), entity_acl={"light.a": "rw"})
    assert "ENTITY_ACL_DENIED" not in scan.codes()


def test_acl_forbids_read():
    # v1.4.0：ACL `-` 映射为 tier-0（必须人审），读命中改报 ENTITY_GUARD_TIER0
    # （设计 §4.2：读检查命中任何 tier-0 → ENTITY_GUARD_TIER0）。
    scan = _scan(_base(), entity_acl={"binary_sensor.m": "-"})
    assert "ENTITY_GUARD_TIER0" in scan.codes()


def test_acl_unlisted_entity_is_unrestricted():
    scan = _scan(_base(), entity_acl={"other.entity": "-"})
    assert "ENTITY_ACL_DENIED" not in scan.codes()


# ── §8.1 L2 强制确认 ──────────────────────────────────────────────────


def _climate_ir(**do_over) -> dict:
    do = {
        "id": "d1",
        "kind": "do",
        "adapter": "ha",
        "action": "climate.turn_on",
        "params": {"entity_id": "climate.study"},
    }
    do.update(do_over)
    return _base(nodes=_base()["nodes"][:1] + [do, {"id": "p1", "kind": "pass"}])


def test_l2_without_confirm_is_error():
    scan = _scan(_climate_ir())
    assert "L2_NEEDS_CONFIRM" in scan.codes()
    assert not scan.ok


def test_l2_with_confirm_passes():
    scan = _scan(_climate_ir(requires_confirm=True, canary={"duration": "15m"}))
    assert "L2_NEEDS_CONFIRM" not in scan.codes()


def test_l1_does_not_need_confirm():
    """L1（灯/开关）不该被 L2 规则误伤。"""
    scan = _scan(_base())
    assert "L2_NEEDS_CONFIRM" not in scan.codes()


# ── §10 置信度分级 ────────────────────────────────────────────────────


def test_low_confidence_cannot_write_device():
    scan = _scan(_base(confidence=0.5))
    assert "LOW_CONF_WRITES_DEVICE" in scan.codes()


def test_shadow_band_forbids_write():
    scan = _scan(_base(confidence=0.7))
    assert "SHADOW_WRITES_DEVICE" in scan.codes()


def test_high_confidence_allows_write():
    scan = _scan(_base(confidence=0.92))
    codes = scan.codes()
    assert "LOW_CONF_WRITES_DEVICE" not in codes
    assert "SHADOW_WRITES_DEVICE" not in codes


# ── §6 do 建议 on_error ───────────────────────────────────────────────


def test_do_without_on_error_warns():
    scan = _scan(_base())
    assert any(d.code == "DO_WITHOUT_ON_ERROR" for d in scan.warnings)
    assert scan.ok, "只是告警，不拦截"


def test_do_with_on_error_has_no_warning():
    data = _base(edges=_base()["edges"] + [{"from": "d1", "to": "p1", "kind": "on_error"}])
    scan = _scan(data)
    assert not any(d.code == "DO_WITHOUT_ON_ERROR" for d in scan.warnings)


# ── §5.3 on_cancel 禁止嵌套挂起 ───────────────────────────────────────


def test_nested_suspend_in_cancel_is_error():
    data = _base(
        nodes=_base()["nodes"]
        + [
            {"id": "q1", "kind": "ask", "prompt": "确认？", "timeout": "60s"},
            {"id": "p2", "kind": "pass"},
        ],
        edges=_base()["edges"]
        + [
            {"from": "d1", "to": "q1", "kind": "on_cancel"},
            {"from": "q1", "to": "p2", "kind": "on_timeout"},
        ],
    )
    scan = _scan(data)
    assert "NESTED_SUSPEND_IN_CANCEL" in scan.codes()


def test_cancel_to_pass_is_fine():
    """对照组：on_cancel 直接落到 pass（不做清理）是允许的。"""
    data = _base(
        nodes=_base()["nodes"] + [{"id": "p2", "kind": "pass"}],
        edges=_base()["edges"] + [{"from": "d1", "to": "p2", "kind": "on_cancel"}],
    )
    scan = _scan(data)
    assert "NESTED_SUSPEND_IN_CANCEL" not in scan.codes()


# ── ③ 跨自动化写冲突 ──────────────────────────────────────────────────


def _writer(aid: str, trigger_entity: str, **over) -> dict:
    data = {
        "ir_version": "0.2.1",
        "id": aid,
        "name": aid,
        "version": 1,
        "mode": "single",
        "nodes": [
            {
                "id": "a1",
                "kind": "on",
                "trigger": {"type": "state", "entity_id": trigger_entity, "to": "on"},
            },
            {
                "id": "d1",
                "kind": "do",
                "adapter": "ha",
                "action": "light.turn_on",
                "params": {"entity_id": "light.hall"},
            },
            {"id": "p1", "kind": "pass"},
        ],
        "edges": [
            {"from": "a1", "to": "d1", "kind": "then"},
            {"from": "d1", "to": "p1", "kind": "then"},
        ],
    }
    data.update(over)
    return data


def test_write_conflict_without_priority():
    scan = _scan_many([_writer("a", "binary_sensor.x"), _writer("b", "binary_sensor.y")])
    assert "ENTITY_WRITE_CONFLICT" in scan.codes()


def test_write_conflict_resolved_by_priority():
    """声明了互不相同优先级的抢占是可裁决的 → 放行。"""
    scan = _scan_many(
        [
            _writer("a", "binary_sensor.x", meta={"priority": 1}),
            _writer("b", "binary_sensor.y", meta={"priority": 2}),
        ]
    )
    assert "ENTITY_WRITE_CONFLICT" not in scan.codes()


def test_single_automation_has_no_conflict():
    scan = _scan_many([_writer("a", "binary_sensor.x")])
    assert "ENTITY_WRITE_CONFLICT" not in scan.codes()


# ── v1.7.2 僵尸触发源闸（TRIGGER_STALE）──────────────────────────────

import datetime as _dt


def _iso_ago(hours: float) -> str:
    return (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=hours)).isoformat()


def test_stale_trigger_flagged():
    """触发源 last_changed 距今 >24h → TRIGGER_STALE warning。"""
    health = {"binary_sensor.m": {"last_changed": _iso_ago(48)}}
    scan = _scan(_base(), entity_health=health)
    assert "TRIGGER_STALE" in scan.codes()


def test_fresh_trigger_not_flagged():
    """触发源近期有变化 → 不误报。"""
    health = {"binary_sensor.m": {"last_changed": _iso_ago(0.1)}}
    scan = _scan(_base(), entity_health=health)
    assert "TRIGGER_STALE" not in scan.codes()


def test_stale_check_disabled():
    """trigger_stale_after_s=0 关闭此项。"""
    health = {"binary_sensor.m": {"last_changed": _iso_ago(999)}}
    scan = _scan(_base(), entity_health=health, trigger_stale_after_s=0)
    assert "TRIGGER_STALE" not in scan.codes()


def test_stale_only_targets_trigger_not_action_target():
    """do 目标实体（灯）长期不动是正常的，不告警；只查触发源。"""
    health = {
        "binary_sensor.m": {"last_changed": _iso_ago(0.1)},
        "light.a": {"last_changed": _iso_ago(999)},
    }
    scan = _scan(_base(), entity_health=health)
    assert "TRIGGER_STALE" not in scan.codes()
