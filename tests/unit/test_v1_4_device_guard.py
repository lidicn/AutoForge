"""v1.4.0 设备保护分级单测（调研 §2.7：注册表 + tier + 取最严）。

覆盖：tier 映射（from_acl）、多命中取最严、domain 匹配、StaticScanner 接线 tier0、热更新（from_file）、
以及 **service 层从 `{store}/device_acl.json` 自动加载**（真实写路径 build/save_graph 生效）。
"""

import json

import pytest

from autoforge import af_service as svc
from autoforge.af_ir import load_graph
from autoforge.af_scanner import DeviceGuardRegistry, GuardRule, StaticScanner
from autoforge.af_store import GraphStore

RAW_IR = {
    "ir_version": "0.2.1",
    "id": "d",
    "name": "d",
    "version": 1,
    "mode": "single",
    "nodes": [
        {"id": "o", "kind": "on", "trigger": {"type": "state", "entity_id": "light.x", "to": "on"}},
        {"id": "c", "kind": "pass"},
    ],
    "edges": [{"from": "o", "to": "c", "kind": "then"}],
}


def test_from_acl_tier_mapping():
    reg = DeviceGuardRegistry.from_acl({"light.x": "-", "light.y": "rw", "light.z": "r"})
    assert reg.match_tier("light.x") == 0  # "-" → 必须人审
    assert reg.match_tier("light.y") == 1  # rw → 放行
    assert reg.match_tier("light.z") == 1  # r → 放行
    assert reg.match_perm("light.z") == "r"  # tier-1 内的只读细分保留
    assert reg.match_tier("light.unknown") is None  # 未命中默认放行


def test_strictest_wins():
    reg = DeviceGuardRegistry(
        [
            GuardRule(match={"type": "entity", "value": "light.x"}, tier=1),
            GuardRule(match={"type": "domain", "value": "light"}, tier=0),
        ]
    )
    # 实体命中两条规则，取最严（0）
    assert reg.match_tier("light.x") == 0


def test_domain_match():
    reg = DeviceGuardRegistry([GuardRule(match={"type": "domain", "value": "light"}, tier=0)])
    assert reg.match_tier("light.anything") == 0
    assert reg.match_tier("switch.other") is None


def test_static_scanner_blocks_tier0():
    reg = DeviceGuardRegistry([GuardRule(match={"type": "entity", "value": "light.x"}, tier=0)])
    scan = StaticScanner(graph=load_graph(RAW_IR), device_guard=reg).scan()
    codes = {d.code for d in scan.diagnostics}
    assert "ENTITY_GUARD_TIER0" in codes


def test_static_scanner_allows_tier1():
    reg = DeviceGuardRegistry([GuardRule(match={"type": "entity", "value": "light.x"}, tier=1, perm="rw")])
    scan = StaticScanner(graph=load_graph(RAW_IR), device_guard=reg).scan()
    codes = {d.code for d in scan.diagnostics}
    assert "ENTITY_GUARD_TIER0" not in codes


def test_hot_reload_from_file(tmp_path):
    """热更新：改 `device_acl.json` 后重新加载即生效（无需重启进程）。

    支持规则数组形态与旧 ACL 对象形态。
    """
    path = tmp_path / "device_acl.json"
    # 规则数组：light.x → tier-0（必须人审）
    path.write_text(
        json.dumps([{"match": {"type": "entity", "value": "light.x"}, "tier": 0}]),
        encoding="utf-8",
    )
    reg = DeviceGuardRegistry.from_file(path)
    assert "ENTITY_GUARD_TIER0" in StaticScanner(graph=load_graph(RAW_IR), device_guard=reg).scan().codes()

    # 改成旧 ACL 对象形态：light.x → rw（tier-1 放行）→ 重新加载后不再拦
    path.write_text(json.dumps({"light.x": "rw"}), encoding="utf-8")
    reg2 = DeviceGuardRegistry.from_file(path)
    assert "ENTITY_GUARD_TIER0" not in StaticScanner(graph=load_graph(RAW_IR), device_guard=reg2).scan().codes()


def test_service_loads_device_acl_from_store(tmp_path):
    """service 层从 `{store}/device_acl.json` 自动加载守卫 → 真实写路径（build/save_graph）生效。

    这是「设备保护分级」真正有用的前提：MCP/HTTP 走的 `svc.build`/`save_graph` 必须接线。
    """
    store = GraphStore(str(tmp_path))
    (tmp_path / "device_acl.json").write_text(
        json.dumps([{"match": {"type": "entity", "value": "light.x"}, "tier": 0}]),
        encoding="utf-8",
    )

    # build：命中 tier-0 → 不通过
    built = svc.build(RAW_IR, store=store)
    assert built["ok"] is False
    assert any(e["code"] == "ENTITY_GUARD_TIER0" for e in built["errors"])

    # save_graph：同样拒绝归档
    with pytest.raises(svc.ServiceError):
        svc.save_graph(store, RAW_IR, "guarded")

    # 热更新：改成 rw（tier-1 放行）→ 重新读数即放行（无需重启）
    (tmp_path / "device_acl.json").write_text(json.dumps({"light.x": "rw"}), encoding="utf-8")
    assert svc.build(RAW_IR, store=store)["ok"] is True


def test_no_device_acl_file_is_noop(tmp_path):
    """未放 `device_acl.json` → 不启用分级保护（向后兼容，既有部署零影响）。"""
    store = GraphStore(str(tmp_path))
    assert svc.load_device_guard(store) is None
    assert svc.build(RAW_IR, store=store)["ok"] is True


def test_api_build_enforces_device_acl(tmp_path):
    """HTTP `/api/build` 也走设备保护（回归：`api_build` 曾漏传 `store`）。"""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from autoforge.af_api import build_app

    store_root = tmp_path / "store"
    store_root.mkdir(parents=True, exist_ok=True)
    (store_root / "device_acl.json").write_text(json.dumps({"light.x": "-"}), encoding="utf-8")

    client = TestClient(build_app(str(store_root)))
    resp = client.post("/api/build", json={"ir": RAW_IR})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert any(e["code"] == "ENTITY_GUARD_TIER0" for e in body["errors"])
