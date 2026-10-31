"""v1.15.0（DCD 裁定 20261031·常驻监听启动面 §2.1 第五面）：免码判据＝对象已部署。

- `forge watch <已部署 IR>` ⇒ 免码启动（按 file 里 automation id **逐条**查 store
  已 approve 归档；⛔ 不许"任一命中即免码"）；
- `forge watch <未部署文件>` ⇒ 走要码路径（拒绝「需要授权码」）；
- store 打不开 / 查不到 ⇒ fail-closed 走要码路径。
"""

from __future__ import annotations

from autoforge.af_cli import _watch_targets_deployed
from autoforge.af_ir import Graph, load_automation
from autoforge.af_store import GraphStore


def _graph(aid: str = "auto_book") -> Graph:
    return Graph([load_automation({
        "ir_version": "0.2.1", "id": aid, "name": aid, "version": 1, "mode": "single",
        "nodes": [
            {"id": "a1", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
            {"id": "d1", "kind": "do", "adapter": "mock", "action": "light.turn_on",
             "params": {"entity_id": "light.x"}},
        ],
        "edges": [{"from": "a1", "to": "d1", "kind": "then"}],
    })])


def test_deployed_object_passes_free_code_gate(tmp_path):
    store = GraphStore(str(tmp_path / "s"))
    store.save(_graph("auto_book"), "auto_book")
    assert _watch_targets_deployed(store, _graph("auto_book")) is True


def test_undeployed_object_fails_gate(tmp_path):
    store = GraphStore(str(tmp_path / "s"))
    assert _watch_targets_deployed(store, _graph("auto_book")) is False


def test_partial_match_fails_gate(tmp_path):
    """⛔ 不许"任一命中即免码"：graph 两条、store 只部署一条 ⇒ False。"""
    store = GraphStore(str(tmp_path / "s"))
    store.save(_graph("auto_book"), "auto_book")
    extra = load_automation({
        "ir_version": "0.2.1", "id": "auto_other", "name": "auto_other", "version": 1, "mode": "single",
        "nodes": [
            {"id": "a1", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
            {"id": "d1", "kind": "do", "adapter": "mock", "action": "light.turn_on",
             "params": {"entity_id": "light.x"}},
        ],
        "edges": [{"from": "a1", "to": "d1", "kind": "then"}],
    })
    g = Graph([load_automation({
        "ir_version": "0.2.1", "id": "auto_book", "name": "auto_book", "version": 1, "mode": "single",
        "nodes": [
            {"id": "a1", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
            {"id": "d1", "kind": "do", "adapter": "mock", "action": "light.turn_on",
             "params": {"entity_id": "light.x"}},
        ],
        "edges": [{"from": "a1", "to": "d1", "kind": "then"}],
    }), extra])
    assert _watch_targets_deployed(store, g) is False


def test_empty_graph_fails_gate(tmp_path):
    store = GraphStore(str(tmp_path / "s"))
    assert _watch_targets_deployed(store, Graph([])) is False


def test_broken_store_fails_closed(tmp_path):
    """store 打不开 / 抛异常 ⇒ fail-closed False（走要码路径）。"""
    class Boom:
        def load(self, name):
            raise RuntimeError("disk gone")

    assert _watch_targets_deployed(Boom(), _graph("auto_book")) is False
