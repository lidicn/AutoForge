"""IR 规模上限（`load_graph` 的 nodes/edges 硬顶）必须有测试钉住。

起因：安全审计 `fp-ir-no-size-cap` 被判 `rejected`（不是漏洞，是 hardening note），建议
「`load_graph` 给 nodes/edges 设可配硬顶 + 仿真 `events` 有条数上限」。HEAD 上两半都已落地
（`af_ir/models.py:639-641` 默认 5000/5000、`af_service.py:131` `MAX_REPLAY_EVENTS=10000`），
但全仓检索 `max_nodes_per_graph` 在 `tests/` 里 **0 命中**：`events` 那半有测试（
`test_af_live_entry_seams.py`），nodes/edges 这半只有实现。审计的 hardening note 常被当成
"没判红所以不用管"，而这里的形状是**判据缺失**——上限将来被谁抬走或去掉，不会有测试红。
"""
from __future__ import annotations

import pytest

from autoforge.af_ir import load_graph


def _auto(nodes: int, edges: int) -> dict:
    ids = [f"n{i}" for i in range(nodes)]
    return {
        "ir_version": "0.2.1", "id": "cap", "name": "cap", "version": 1, "mode": "single",
        "nodes": [{"id": i, "kind": "pass"} for i in ids],
        "edges": [{"from": ids[k % len(ids)], "to": ids[(k + 1) % len(ids)], "kind": "then"}
                  for k in range(edges)],
    }


def test_within_cap_loads():
    """绿色一侧：确认真数过，不是"任何输入都抛"。"""
    g = load_graph(_auto(3, 2))
    assert len(list(g.automations)) == 1


def test_nodes_over_cap_rejected():
    with pytest.raises(ValueError, match="max_nodes_per_graph"):
        load_graph(_auto(4, 0), max_nodes_per_graph=3)


def test_edges_over_cap_rejected():
    with pytest.raises(ValueError, match="max_edges_per_graph"):
        load_graph(_auto(3, 5), max_edges_per_graph=4)


def test_default_cap_is_the_number_in_the_record():
    """默认值钉死：5000 是登记进审计处置记录的那个数，抬它要留痕。"""
    import inspect

    from autoforge.af_ir.models import load_graph as lg

    sig = inspect.signature(lg)
    assert sig.parameters["max_nodes_per_graph"].default == 5000
    assert sig.parameters["max_edges_per_graph"].default == 5000


def test_cap_counts_across_the_automations_container():
    """`{"automations": [...]}` 那一路按**总和**判：多条各 2000 也必须撞上限。"""
    bundle = {"automations": [_auto(2, 1), _auto(2, 1)]}
    with pytest.raises(ValueError, match="max_nodes_per_graph"):
        load_graph(bundle, max_nodes_per_graph=3)
