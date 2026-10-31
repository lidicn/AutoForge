"""AF-704（归档面实体校验缺失，假绿族第二面）回归钉，2026-10-31。

出处：QA 审计（🟡，PM 本地实证）——`af_build` 走目录时幽灵实体 IR 被拒
（`entity_check="catalog"`、`ENTITY_NOT_FOUND`），但同一份 IR 走
`svc.submit_pending`（af_save 入口）→ ok=True 直接入队；`save_graph` 直调同样放行。
根因：两处 `StaticScanner(...).scan()` 都不传 `known_entities` ⇒ 存在性校验恒
`skipped`；待批回执也没有 `entity_check` 读数，人审看不出是幽灵。运行期后果：
动作打 HA 404、静默无效。

修法（A+B）：两处扫描白名单取 `DeviceCatalog(store.root).known_entity_ids()`
（空则 None，合法首建态 fail-open 但必须说话）；`submit_pending` 回执带
`entity_check` + `build_warnings`；拒绝消息带具名码（分得出 ENTITY_NOT_FOUND）。

变异自证（摘掉 known_entities ⇒ 判据①红）由实现者在落地批次内手工执行并附
命令输出，不进本文件。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoforge import af_service
from autoforge.af_store import GraphStore


def _seed_catalog(root: Path, entities: set[str]) -> None:
    """在 store 根铺设备目录快照（`DeviceCatalog._load` 的读盘形状）。"""
    cat = root / ".catalog" / "catalog.json"
    cat.parent.mkdir(parents=True, exist_ok=True)
    cat.write_text(
        json.dumps({"version": 1, "freshness": "", "entities": {
            eid: {"state": "off", "domain": eid.split(".", 1)[0]} for eid in entities
        }}, ensure_ascii=False),
        encoding="utf-8",
    )


def _ir(trigger: str, target: str, name: str = "t") -> dict:
    """最小合法 IR：state 触发 → 灯动作（形状同 test_af_apply_owner_backfill）。"""
    return {
        "ir_version": "0.2.1",
        "id": name,
        "name": name,
        "version": 1,
        "mode": "single",
        "nodes": [
            {"id": "o", "kind": "on", "trigger": {"type": "state", "entity_id": trigger, "to": "on"}},
            {"id": "d", "kind": "do", "adapter": "mock", "action": "light.turn_on",
             "params": {"entity_id": target}},
        ],
        "edges": [{"from": "o", "to": "d", "kind": "then"}],
    }


_KNOWN = {"binary_sensor.m", "light.x"}


def _store_with(tmp_path: Path, entities: set[str] | None) -> GraphStore:
    root = tmp_path / "store"
    root.mkdir(parents=True, exist_ok=True)
    if entities:
        _seed_catalog(root, entities)
    return GraphStore(str(root))


# ── 判据① 幽灵实体：入队与归档双闸都必须拒 ──────────────────────────


def test_submit_pending_rejects_ghost_entities(tmp_path):
    """主目录有货：幽灵实体 IR 走 submit_pending ⇒ ServiceError 含 ENTITY_NOT_FOUND。"""
    store = _store_with(tmp_path, set(_KNOWN))
    with pytest.raises(af_service.ServiceError) as ei:
        af_service.submit_pending(
            store, "af_save",
            {"ir": {"automations": [_ir("binary_sensor.ghost99", "light.alsoghost")]}, "name": "g"},
        )
    assert "ENTITY_NOT_FOUND" in str(ei.value), str(ei.value)


def test_save_graph_rejects_ghost_entities(tmp_path):
    """主目录有货：save_graph 直调同一份 IR ⇒ 同样拒（双保险两侧都要有实体闸）。"""
    store = _store_with(tmp_path, set(_KNOWN))
    with pytest.raises(af_service.ServiceError) as ei:
        af_service.save_graph(store, {"automations": [_ir("binary_sensor.ghost99", "light.x")]}, "g")
    assert "ENTITY_NOT_FOUND" in str(ei.value), str(ei.value)


# ── 判据② 真实实体不误伤 ─────────────────────────────────────────────


def test_submit_pending_real_entities_pass_with_catalog_check(tmp_path):
    """主目录有货：真实实体 IR 正常入队，且回执 entity_check=catalog。"""
    store = _store_with(tmp_path, set(_KNOWN))
    res = af_service.submit_pending(
        store, "af_save",
        {"ir": {"automations": [_ir("binary_sensor.m", "light.x")]}, "name": "r"},
    )
    assert res["ok"] is True
    assert res["entity_check"] == "catalog", res.get("entity_check")


def test_save_graph_real_entities_pass(tmp_path):
    """主目录有货：save_graph 直调正常归档。"""
    store = _store_with(tmp_path, set(_KNOWN))
    res = af_service.save_graph(store, {"automations": [_ir("binary_sensor.m", "light.x")]}, "r")
    assert res["ok"] is True


# ── 判据③ 空目录 fail-open 但必须说话 ────────────────────────────────


def test_empty_catalog_fail_open_but_receipt_speaks(tmp_path):
    """主目录空：入队放行（合法首建态），但回执带 skipped + ENTITY_CHECK_DEGRADED。"""
    store = _store_with(tmp_path, None)
    res = af_service.submit_pending(
        store, "af_save",
        {"ir": {"automations": [_ir("binary_sensor.m", "light.x")]}, "name": "e"},
    )
    assert res["ok"] is True, "空目录 fail-open 被误拦"
    assert res["entity_check"] == "skipped", res.get("entity_check")
    codes = {w.get("code") for w in (res.get("build_warnings") or [])}
    assert "ENTITY_CHECK_DEGRADED" in codes, res.get("build_warnings")
