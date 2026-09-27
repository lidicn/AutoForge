"""v1.6.0 P0 数据源升级 —— **契约测试**（钉住降级语义，防止与 AutoFlow 冻结版漂移）。

> 对应 AutoFlow 侧 `test_contracts_surface` 那套思路：把「平台之间的约定」写成测试，
> 而不是写在文档里口头承诺。本文件每一条都对应一条**已冻结的降级纪律**：

| # | 纪律 | 测试 |
|---|---|---|
| 1 | 注册表抓取失败 → 静默返回空、**不抛异常**；不能只靠 `except`，必须**判空** | `test_fetch_never_raises` / `test_build_snapshot_handles_junk` |
| 2 | **各能力组独立 try**：集成（组 4）失败不得连坐 area / device | `test_config_entry_failure_does_not_take_down_area_and_device` |
| 3 | **绝不把已知值清零**（AutoFlow 2026-07-16 事故：一次 ws 失败把全库 `area=""`） | `test_never_zero_out_known_values` |
| 4 | 缺失 → **空串**，核心 catalog 能力不受损 | `test_missing_yields_empty_string` / `test_resolve_still_works_without_registry` |
| 5 | **area 解析链 `entity.area_id → device.area_id`**（区域常挂在 device 上） | `test_area_resolution_chain` |
| 6 | **`platform ≠ integration`**：真集成名走 `config_entry_id → domain`，无 entry 才回退 platform | `test_platform_is_not_integration` |
| 7 | REST `/api/areas` 兜底；该版本 404 → 空（不抛） | `test_rest_areas_fallback` |

本轮**明确不做**（已在 ROADMAP v1.6.0 章节记录）：`connectivity_tier` 排序启发式（P1）、
长期健康度（归 MA）、遥测/消歧（P2）、强制 binding。故本文件不含这些断言。
"""

from __future__ import annotations

import pytest

from autoforge.af_catalog import DeviceCatalog
from autoforge.af_registry import (
    WEBSOCKETS_MISSING_HINT,
    RegistrySnapshot,
    _build_snapshot,
    fetch_registries,
    rest_areas_fallback,
)


def _raw_states() -> dict[str, tuple[str, dict]]:
    return {
        "light.study_lamp": ("off", {"friendly_name": "书房吊灯"}),
        "switch.study_pc": ("on", {"friendly_name": "书房电脑"}),
        "light.living_main": ("on", {"friendly_name": "客厅主灯", "area": "客厅"}),
    }


def _full_registry() -> RegistrySnapshot:
    """四组齐全的注册表（entity 的 area_id 故意为空，逼出 device 兜底链）。"""
    return RegistrySnapshot(
        entity_index={
            "light.study_lamp": {
                "device_id": "dev_lamp",
                "area_id": "",  # ← 实体没挂区域
                "platform": "xiaomi_miot",
                "config_entry_id": "ce_1",
            },
            "switch.study_pc": {
                "device_id": "dev_pc",
                "area_id": "area_study",
                "platform": "xiaomi_miot",
                "config_entry_id": "ce_1",
            },
            "light.living_main": {
                "device_id": "",
                "area_id": "",
                "platform": "",
                "config_entry_id": "",
            },
        },
        device_area={"dev_lamp": "area_study", "dev_pc": "area_study"},
        area_names={"area_study": "书房", "area_living": "客厅"},
        entry_domains={"ce_1": "xiaomi_home"},  # 真集成名 ≠ platform
    )


def _empty_registry(reason: str = "ws 失败") -> RegistrySnapshot:
    return RegistrySnapshot(available=False, reasons=[reason])


# ─────────────────────────────────────────────────────────────────────
# 纪律 6：platform ≠ integration（坑 1）
# ─────────────────────────────────────────────────────────────────────


def test_platform_is_not_integration():
    reg = _full_registry()
    # entity_registry 的 platform 是「平台实现名」，真集成名来自 config_entry → domain
    assert reg.platform_of("light.study_lamp") == "xiaomi_miot"
    assert reg.integration_of("light.study_lamp") == "xiaomi_home"
    # 无 config_entry_id 时才回退 platform
    assert reg.integration_of("light.living_main") == ""


def test_integration_source_marks_trustworthiness():
    """`integration_source` 标明 integration 是权威还是回退——消费者据此判断可信度。

    ⚠️ 线上事实（HA 2026.9.2 实测）：该版本未注册 config_entry 列表命令，
    故 `entry_domains` 恒空、`integration_source` 恒为 `platform`（回退），不是 bug。
    """
    reg = _full_registry()
    assert reg.integration_source_of("light.study_lamp") == "config_entry"  # 权威
    reg2 = RegistrySnapshot(
        entity_index={"switch.y": {"device_id": "", "area_id": "", "platform": "mqtt", "config_entry_id": "ce_x"}},
        entry_domains={},  # 命令不可用 → 空
    )
    assert reg2.integration_source_of("switch.y") == "platform"  # 回退
    reg3 = RegistrySnapshot(entity_index={"switch.z": {"device_id": "", "area_id": "", "platform": "", "config_entry_id": ""}})
    assert reg3.integration_source_of("switch.z") == ""  # 都没有


def test_platform_fallback_when_no_config_entry():
    reg = RegistrySnapshot(
        entity_index={"switch.x": {"device_id": "", "area_id": "", "platform": "mqtt", "config_entry_id": ""}},
        entry_domains={},
    )
    assert reg.integration_of("switch.x") == "mqtt"  # 回退


# ─────────────────────────────────────────────────────────────────────
# 纪律 5：area 解析链 entity.area_id → device.area_id（坑 2）
# ─────────────────────────────────────────────────────────────────────


def test_area_resolution_chain():
    reg = _full_registry()
    # ① 实体自己没挂区域 → 必须落到 device 上（这是 v1.1.0 的缺陷所在）
    assert reg.area_id_of("light.study_lamp") == "area_study"
    # ② 实体自己挂了区域 → 直接用
    assert reg.area_id_of("switch.study_pc") == "area_study"
    # ③ 都没有 → 空串（不抛）
    assert reg.area_id_of("light.living_main") == ""
    assert reg.area_id_of("light.does_not_exist") == ""
    assert reg.area_name_of("area_study") == "书房"
    assert reg.area_name_of("area_missing") == ""


def test_refresh_populates_area_via_chain(tmp_path, monkeypatch):
    catalog = DeviceCatalog(tmp_path, fetch_all=_raw_states)
    monkeypatch.setattr(DeviceCatalog, "_registry", lambda self: _full_registry())
    catalog.refresh()

    entities = catalog.catalog_path.parent  # 目录已落盘
    assert entities.exists()
    lamp = catalog.resolve("书房吊灯")["candidates"][0]
    assert lamp["area"] == "书房"  # 来自 device 兜底链，而非 attributes
    assert lamp["device_id"] == "dev_lamp"
    assert lamp["integration"] == "xiaomi_home"

    # attributes 兜底仍在（注册表拿不到时）
    living = catalog.resolve("客厅主灯")["candidates"][0]
    assert living["area"] == "客厅"


# ─────────────────────────────────────────────────────────────────────
# 纪律 2：各能力组独立 try（集成失败不得连坐）
# ─────────────────────────────────────────────────────────────────────


def test_config_entry_failure_does_not_take_down_area_and_device():
    """组 4（config_entry）缺失/损坏 → 只影响 `integration`，area / device 必须完好。"""
    raw = {
        1: [
            {
                "entity_id": "light.a",
                "device_id": "dev_a",
                "area_id": "",
                "platform": "xiaomi_miot",
                "config_entry_id": "ce_1",
            }
        ],
        2: [{"id": "dev_a", "area_id": "area_study"}],
        3: [{"area_id": "area_study", "name": "书房"}],
        4: None,  # ← 集成组「失败」：None / 缺失 / 损坏
    }
    snap = _build_snapshot(raw)
    assert snap.entry_domains == {}  # 集成组确实为空
    assert snap.device_area == {"dev_a": "area_study"}  # 未被连坐
    assert snap.area_names == {"area_study": "书房"}  # 未被连坐
    assert snap.area_id_of("light.a") == "area_study"  # area 链路仍完整
    assert snap.integration_of("light.a") == "xiaomi_miot"  # 仅回退为 platform


def test_recv_failures_are_surfaced_not_swallowed():
    """连接级故障（如帧超限被 1009 断连）**必须出现在 reasons**，不能被静默吞成「组为空」。

    NAS 实测教训：2888 实体的 `entity_registry` 超过 websockets 默认 1 MiB 帧上限 →
    服务端 1009 断连；若逐条 `except: continue` 吞掉，症状就变成「四个组全空」，
    看不出真因，极易误判成"没权限/没数据"。
    """
    snap = _build_snapshot({}, ["ws.recv 失败：ConnectionClosedError: message too big"])
    assert snap.available is False
    assert any("ws.recv 失败" in r for r in snap.reasons)


def test_build_snapshot_handles_junk():
    """任何一组是垃圾/缺失 → 该组为空，**永不抛**；且必须判空而非只靠 except。"""
    for raw in ({}, {1: "not-a-list", 2: None, 3: 42, 4: []}, {1: [{"no_entity_id": 1}]}):
        snap = _build_snapshot(raw)
        assert snap.entity_index == {} or snap.entity_index  # 不崩
        assert snap.device_area == {}
        assert snap.area_names == {}
        assert snap.entry_domains == {}
        assert snap.area_id_of("light.a") == ""
        assert snap.integration_of("light.a") == ""


# ─────────────────────────────────────────────────────────────────────
# 纪律 1：抓取失败静默返回空、不抛
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_fetch_never_raises():
    """不可达端口 / 坏令牌 —— 都只返回空快照，**绝不把异常丢给调用方**。

    （刻意只用必然拒绝连接的本机端口：用不可解析主机名会走 DNS 超时，测的是网络不是契约。）
    """
    for url, token in (("http://127.0.0.1:1", ""), ("http://127.0.0.1:1", "bad-token")):
        snap = fetch_registries(url, token, timeout=0.2)
        assert isinstance(snap, RegistrySnapshot)
        assert snap.entity_index == {}
        assert snap.area_id_of("light.a") == ""


def test_missing_websockets_reports_hint(monkeypatch):
    """未装可选依赖 → available=False + 明确安装提示（静默，不抛）。"""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "websockets":
            raise ImportError("no websockets")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    snap = fetch_registries("http://127.0.0.1:1", "", timeout=0.2)
    assert snap.available is False
    assert any(WEBSOCKETS_MISSING_HINT.split("：")[0] in r for r in snap.reasons)


# ─────────────────────────────────────────────────────────────────────
# 纪律 3：绝不把已知值清零（2026-07-16 事故）
# ─────────────────────────────────────────────────────────────────────


def test_never_zero_out_known_values(tmp_path, monkeypatch):
    """★ 核心不变量：这一轮注册表拿不到，**沿用目录里已有的值**，绝不写空串。"""
    catalog = DeviceCatalog(tmp_path, fetch_all=_raw_states)
    # 第一次：注册表齐全 → 写入 device_id / integration / area
    monkeypatch.setattr(DeviceCatalog, "_registry", lambda self: _full_registry())
    catalog.refresh()
    before = catalog.resolve("书房吊灯")["candidates"][0]
    assert before["area"] == "书房"
    assert before["device_id"] == "dev_lamp"
    assert before["integration"] == "xiaomi_home"

    # 第二次：注册表整体失败（模拟一次 ws 崩了）→ 已知值必须原样保留
    monkeypatch.setattr(DeviceCatalog, "_registry", lambda self: _empty_registry())
    catalog.refresh()
    after = catalog.resolve("书房吊灯")["candidates"][0]
    assert after["area"] == "书房", "区域被清零了（2026-07-16 事故复现）"
    assert after["device_id"] == "dev_lamp", "device_id 被清零了"
    assert after["integration"] == "xiaomi_home", "integration 被清零了"


# ─────────────────────────────────────────────────────────────────────
# 纪律 4：缺失 → 空串，核心能力不受损
# ─────────────────────────────────────────────────────────────────────


def test_missing_yields_empty_string(tmp_path, monkeypatch):
    catalog = DeviceCatalog(tmp_path, fetch_all=_raw_states)
    monkeypatch.setattr(DeviceCatalog, "_registry", lambda self: _empty_registry())
    catalog.refresh()
    item = catalog.resolve("书房吊灯")["candidates"][0]
    assert item["device_id"] == ""
    assert item["platform"] == ""
    assert item["integration"] == ""
    assert item["area"] == ""
    assert item["offline_now"] is False


def test_resolve_still_works_without_registry(tmp_path, monkeypatch):
    """无 websockets / 注册表全空 → `resolve` / `list_entities` 照常工作（核心能力不受损）。"""
    catalog = DeviceCatalog(tmp_path, fetch_all=_raw_states)
    monkeypatch.setattr(DeviceCatalog, "_registry", lambda self: _empty_registry())
    result = catalog.refresh()
    assert result["ok"] is True
    assert result["registry"]["available"] is False

    assert catalog.resolve("书房吊灯")["count"] >= 1
    assert catalog.list_entities(domain="light")["matched_count"] == 2
    assert catalog.snapshot()["total_entities"] == 3


# ─────────────────────────────────────────────────────────────────────
# 纪律 7：REST /api/areas 兜底
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_rest_areas_fallback():
    """兜底成功 → 解析出区域；失败（含 404）→ 空 dict，**不抛**。"""
    ok = rest_areas_fallback("http://127.0.0.1:1", "", timeout=0.2)
    assert ok == {}  # 连不上 → 空，不抛
