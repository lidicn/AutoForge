"""F15 跨词表覆盖率锁死测试 —— KNOWN_ACTIONS 真源 vs 三个散表

这组测试不合并三个散表（它们服务不同层：仿真/验证/渲染），
但锁死覆盖率：任何新增 domain.service 必须先入 KNOWN_ACTIONS，
再同步到对应下游表（或合法豁免）。
"""
from __future__ import annotations

import pytest

from autoforge.af_actions import (
    KNOWN_ACTIONS, known_services_for, EXEMPT_DOMAINS,
)
from autoforge.af_vhass.fake import SERVICE_STATE, DYNAMIC_SERVICES
from autoforge.af_shadow import DEFAULT_EFFECTS
from autoforge import af_nl


# ── 1. SERVICE_STATE + DYNAMIC_SERVICES 必须覆盖所有非豁免的 KNOWN_ACTIONS ──

def test_fake_modeled_covers_all_known_non_exempt():
    """FakeHA 仿真层必须对 KNOWN_ACTIONS 里所有非豁免域有状态翻转能力。"""
    modeled = SERVICE_STATE.keys() | DYNAMIC_SERVICES  # DYNAMIC_SERVICES 是 frozenset[tuple]
    for domain, service in KNOWN_ACTIONS:
        if domain in EXEMPT_DOMAINS:
            continue  # 副作用域在 fake 里只做"已触发"状态，不要求新态
        assert (domain, service) in modeled, (
            f"KNOWN_ACTIONS 有 ({domain}, {service}) 但 SERVICE_STATE/DYNAMIC_SERVICES 未建模"
        )


def test_exempt_domain_fake_models_as_effect_triggered():
    """豁免域在 fake 里应至少登记在 SERVICE_STATE 里（让仿真能跑通，不走 unmodeled）。"""
    for domain, service in KNOWN_ACTIONS:
        if domain not in EXEMPT_DOMAINS:
            continue
        assert (domain, service) in SERVICE_STATE, (
            f"豁免域 ({domain}, {service}) 未在 SERVICE_STATE 登记，仿真会跳过"
        )


# ── 2. DEFAULT_EFFECTS 必须覆盖所有"能推导期望态"的 service ─────────────────

def test_shadow_default_effects_covers_runnable_services():
    """Shadow 验证层 DEFAULT_EFFECTS 应覆盖所有能推导期望态的 service（或有合理豁免）。"""
    runnable_services = set()
    for domain, service in KNOWN_ACTIONS:
        if domain in EXEMPT_DOMAINS:
            continue  # 豁免域在 shadow 走 EXEMPT verdict，不需要 DEFAULT_EFFECTS
        runnable_services.add(service)

    # DEFAULT_EFFECTS 的 keys 是 service 字符串（domain 无关的简化）
    effect_services = set(DEFAULT_EFFECTS.keys())

    missing = runnable_services - effect_services - {"toggle"}  # toggle 无法静态推导，有 resolver
    assert not missing, (
        f"以下 service 在 KNOWN_ACTIONS 但 DEFAULT_EFFECTS 无法推导期望态（非 toggle）："
        f"{missing} — 需要加或豁免"
    )


# ── 3. _ACTION_VERBS 必须在 KNOWN_ACTIONS 里有对应 (domain, service) ─────────

def test_nl_action_verbs_all_in_known_actions():
    """NL 渲染层 _ACTION_VERBS 里的每个 "domain.service" 都必须在 KNOWN_ACTIONS 里。"""
    for verb_key in af_nl._ACTION_VERBS:
        assert "." in verb_key, f"_ACTION_VERBS key {verb_key!r} 格式错误（缺 domain）"
        domain, _, service = verb_key.partition(".")
        assert (domain, service) in KNOWN_ACTIONS, (
            f"_ACTION_VERBS 有 {verb_key} 但 KNOWN_ACTIONS 未登记 ({domain}, {service})"
        )


def test_known_actions_have_nl_verb_or_are_exempt():
    """KNOWN_ACTIONS 里每个 (domain, service) 必须有 NL 动词映射或合法豁免。"""
    nl_keys = set(af_nl._ACTION_VERBS.keys())
    for domain, service in KNOWN_ACTIONS:
        key = f"{domain}.{service}"
        if key in nl_keys:
            continue
        # 豁免域允许无 NL 动词（它们副作用不可观测，NL 渲染时也会跳过）
        assert domain in EXEMPT_DOMAINS, (
            f"KNOWN_ACTIONS 有 ({domain}, {service}) 但 _ACTION_VERBS 无中文动词，"
            f"且不在 EXEMPT_DOMAINS — 需要加 NL 映射或声明豁免"
        )


# ── 4. KNOWN_ACTIONS 结构一致性 ────────────────────────────────────────────────

def test_known_actions_no_duplicates():
    """KNOWN_ACTIONS 作为 frozenset 去重，确保 _KNOWN_DOMAIN_SERVICES 各域服务不重复。"""
    total = sum(len(svcs) for svcs in [
        known_services_for(d) for d in {domain for domain, _ in KNOWN_ACTIONS}
    ])
    assert len(KNOWN_ACTIONS) == total, (
        f"KNOWN_ACTIONS {len(KNOWN_ACTIONS)} ≠ 域服务展开 {total}（疑似 _KNOWN_DOMAIN_SERVICES 有重复）"
    )


def test_known_domains_are_ha_compatible():
    """所有 domain 必须是已知 HA domain（白名单），不接受随意拼新 domain。"""
    ha_domains = {
        "light", "switch", "fan", "input_boolean",
        "climate", "media_player", "lock", "cover",
        "scene", "script", "notify", "persistent_notification",
    }
    for domain, _ in KNOWN_ACTIONS:
        assert domain in ha_domains, (
            f"KNOWN_ACTIONS 有未知 HA domain {domain!r} — 请核实或加白名单"
        )
