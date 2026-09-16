"""v1.3.0 变更可信（diff 签名兜底 + 爆炸半径 + 归档归属）单测。

覆盖：
1. **diff 签名兜底**：Agent 改了 node id（真实痛点）→ 识别为 `renamed_nodes`，
   **不是**「删了旧的 + 加了新的」；相关边也一并归一化，不产生虚假的边增删。
2. 兜底的边界：真·新增/删除仍报 added/removed；签名全空的节点（`pass`）**不参与**兜底防误配。
3. **爆炸半径**：`save_graph` / `enable_by_tag` / `import_store` 超限即拒（400），
   且 `enable_by_tag` 必须**先算影响面再落盘**（不做一半才失败）。
4. **归档归属**：`owner` 落盘并在 `list_graphs` 暴露；已归属他人的归档他人覆盖 → 403；
   归属为空视为公共放行。
"""

from __future__ import annotations

import pytest

from autoforge import af_service as svc
from autoforge.af_ir import load_graph
from autoforge.af_service import DEFAULT_BLAST_RADIUS, ServiceError
from autoforge.af_store import GraphStore, diff_graphs


def _demo(**overrides) -> dict:
    """单条自动化：`on 触发 → do 开灯 → pass`。

    `overrides` 可覆盖任意顶层键（如 `id` / `nodes` / `edges`）；
    用 `target=` 给 `do` 换一个专属实体，避免多自动化写同一实体触发
    `ENTITY_WRITE_CONFLICT` 而被安全闸先拦下（本文件测的是爆炸半径，不是扫描）。
    """
    target = overrides.pop("target", None)
    if target:
        nodes = overrides.get("nodes") or [
            {
                "id": "a1",
                "kind": "on",
                "trigger": {"type": "state", "entity_id": "binary_sensor.study_motion", "to": "on"},
            },
            {
                "id": "d1",
                "kind": "do",
                "adapter": "ha",
                "action": "light.turn_on",
                "params": {"entity_id": target},
            },
            {"id": "p1", "kind": "pass"},
        ]
        overrides["nodes"] = nodes
    base = {
        "ir_version": "0.2.1",
        "id": "demo",
        "name": "demo",
        "version": 1,
        "mode": "single",
        "nodes": [
            {
                "id": "a1",
                "kind": "on",
                "trigger": {"type": "state", "entity_id": "binary_sensor.study_motion", "to": "on"},
            },
            {
                "id": "d1",
                "kind": "do",
                "adapter": "ha",
                "action": "light.turn_on",
                "params": {"entity_id": "light.study_main"},
            },
            {"id": "p1", "kind": "pass"},
        ],
        "edges": [
            {"from": "a1", "to": "d1", "kind": "then"},
            {"from": "d1", "to": "p1", "kind": "then"},
        ],
    }
    base.update(overrides)
    return base


def _renamed_ids() -> dict:
    """同一条自动化，只把节点 id 全改一遍（`a1→on1`, `d1→do1`, `p1→end1`）。"""
    return _demo(
        nodes=[
            {
                "id": "on1",
                "kind": "on",
                "trigger": {"type": "state", "entity_id": "binary_sensor.study_motion", "to": "on"},
            },
            {
                "id": "do1",
                "kind": "do",
                "adapter": "ha",
                "action": "light.turn_on",
                "params": {"entity_id": "light.study_main"},
            },
            {"id": "end1", "kind": "pass"},
        ],
        edges=[
            {"from": "on1", "to": "do1", "kind": "then"},
            {"from": "do1", "to": "end1", "kind": "then"},
        ],
    )


# ─────────────────────────────────────────────────────────────────────
# 1. diff 签名兜底
# ─────────────────────────────────────────────────────────────────────


def test_rename_is_not_added_plus_removed():
    """核心：Agent 重写 IR 改了 node id → 必须是 rename，不是删+加。"""
    result = diff_graphs(load_graph(_demo()), load_graph(_renamed_ids()))
    pairs = {o for o, _ in result.renamed_nodes}
    assert "demo:a1" in pairs and "demo:d1" in pairs
    # d1 与 do1 已配对 → 不应再出现在 added/removed
    assert "demo:d1" not in result.removed_nodes
    assert "demo:do1" not in result.added_nodes


def test_rename_normalizes_edges():
    """节点改名后，边不应被误报成「删了旧边 + 加了新边」。"""
    result = diff_graphs(load_graph(_demo()), load_graph(_renamed_ids()))
    assert result.added_edges == []
    assert result.removed_edges == []


def test_empty_signature_nodes_not_matched():
    """签名全空的节点（如 `pass`）不参与兜底——否则会把两个不同用途的占位节点误配成对。"""
    old = _demo(nodes=[{"id": "p1", "kind": "pass"}, {"id": "p2", "kind": "pass"}], edges=[])
    new = _demo(nodes=[{"id": "z1", "kind": "pass"}, {"id": "z2", "kind": "pass"}], edges=[])
    result = diff_graphs(load_graph(old), load_graph(new))
    assert result.renamed_nodes == []  # 不兜底
    assert len(result.added_nodes) == 2 and len(result.removed_nodes) == 2


def test_real_addition_still_reported_as_added():
    """真·新增动作节点（不同 action/entity）必须报 added，不能被兜底吃掉。"""
    old = _demo()
    new = _demo(
        nodes=_demo()["nodes"]
        + [
            {
                "id": "d2",
                "kind": "do",
                "adapter": "ha",
                "action": "switch.turn_on",
                "params": {"entity_id": "switch.fan"},
            }
        ],
        edges=_demo()["edges"] + [{"from": "d1", "to": "d2", "kind": "then"}],
    )
    result = diff_graphs(load_graph(old), load_graph(new))
    assert "demo:d2" in result.added_nodes
    assert result.renamed_nodes == []


def test_identical_still_empty():
    assert diff_graphs(load_graph(_demo()), load_graph(_demo())).empty


def test_service_diff_exposes_renamed_nodes(tmp_path):
    store = GraphStore(tmp_path)
    store.save(load_graph(_demo()), "demo", note="v1")
    store.save(load_graph(_renamed_ids()), "demo", note="v2")
    out = svc.diff(store, "demo", 1, 2)
    renamed = out["structured"]["renamed_nodes"]
    assert any(r["from"] == "demo:d1" and r["to"] == "demo:do1" for r in renamed)
    assert all(r["matched_by"] == "signature" for r in renamed)


# ─────────────────────────────────────────────────────────────────────
# 3. 爆炸半径
# ─────────────────────────────────────────────────────────────────────


def _bundle(n_autos: int) -> dict:
    """造一个含 n 条自动化的 bundle（不做 checksum，够触发爆炸半径即可）。"""
    entries = []
    for i in range(3):
        entries.append(
            {
                "name": f"g{i}",
                "tags": ["bulk"],
                "versions": [
                    {
                        "version": 1,
                        "saved_at": "t",
                        "note": "",
                        "graph": {
                            "automations": [
                                dict(
                                    _demo(
                                        id=f"a{i}_{j}",
                                        name=f"a{i}_{j}",
                                        target=f"light.g{i}_r{j}",
                                    )
                                )
                                for j in range(n_autos)
                            ]
                        },
                    }
                ],
            }
        )
    return {"format": "autoforge-bundle", "version": 1, "entries": entries, "confs": []}


def test_save_graph_over_blast_radius_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOFORGE_BLAST_RADIUS", "2")
    store = GraphStore(tmp_path)
    big = {
        "automations": [
            dict(_demo(id=f"a{i}", name=f"a{i}", target=f"light.room{i}")) for i in range(5)
        ]
    }
    with pytest.raises(ServiceError) as exc:
        svc.save_graph(store, big, "big", allow_bulk=False)
    assert "爆炸半径" in str(exc.value)
    assert exc.value.status == 400

    # 显式批量 → 放行
    out = svc.save_graph(store, big, "big", allow_bulk=True)
    assert out["ok"] is True
    assert out["blast_radius"]["affected"] == 5


def test_enable_by_tag_over_blast_radius_rejected_without_partial_writes(tmp_path, monkeypatch):
    """★ 必须**先算影响面再落盘**——绝不能改了一半才报错。"""
    monkeypatch.setenv("AUTOFORGE_BLAST_RADIUS", "2")
    store = GraphStore(tmp_path)
    for i in range(3):
        store.save(load_graph(_demo(id=f"a{i}", name=f"a{i}", target=f"light.g{i}")), f"g{i}")
        store.set_tags(f"g{i}", ["bulk"])

    with pytest.raises(ServiceError) as exc:
        svc.enable_by_tag(store, "bulk", False)
    assert "爆炸半径" in str(exc.value)
    # 关键：一个归档都没被改（仍是 v1）
    for i in range(3):
        assert store.latest(f"g{i}") == 1

    out = svc.enable_by_tag(store, "bulk", False, allow_bulk=True)
    assert out["blast_radius"]["affected"] == 3
    for i in range(3):
        assert store.latest(f"g{i}") == 2


def test_import_store_over_blast_radius_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOFORGE_BLAST_RADIUS", "2")
    store = GraphStore(tmp_path)
    with pytest.raises(ServiceError):
        svc.import_store(store, _bundle(2), "skip", allow_bulk=False)
    assert store.names() == []  # 没有落地


def test_default_blast_radius_constant():
    assert DEFAULT_BLAST_RADIUS == 8


# ─────────────────────────────────────────────────────────────────────
# 4. 归档归属
# ─────────────────────────────────────────────────────────────────────


def test_owner_recorded_and_exposed(tmp_path):
    store = GraphStore(tmp_path)
    svc.save_graph(store, _demo(), "demo", owner="agent-a")
    items = svc.list_graphs(store)["items"]
    assert items[0]["owner"] == "agent-a"


def test_ownership_isolation_blocks_other_subject(tmp_path):
    store = GraphStore(tmp_path)
    svc.save_graph(store, _demo(), "demo", owner="agent-a")
    # 他人覆盖 → 403
    with pytest.raises(ServiceError) as exc:
        svc.save_graph(store, _demo(), "demo", owner="agent-b")
    assert exc.value.status == 403
    assert "所有权隔离" in str(exc.value)
    # 本人继续改 → 放行
    out = svc.save_graph(store, _demo(), "demo", owner="agent-a")
    assert out["version"] == 2


def test_public_archive_allows_any_subject(tmp_path):
    """归属为空 = 公共归档，任何主体都可继续改（不误伤既有用法）。"""
    store = GraphStore(tmp_path)
    svc.save_graph(store, _demo(), "demo")  # 无 owner
    out = svc.save_graph(store, _demo(), "demo", owner="agent-b")
    assert out["version"] == 2
