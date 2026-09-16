"""v1.4.0 待批队列单测（调研 §2.6：执行/批准物理分离）。

覆盖：af_save 进 pending → approve 回放落盘 → 删 pending；per-agent 熔断；
MCP 面绝不注册 approve（agent 不能自批）。
"""

import json

import pytest

from autoforge import af_service as svc
from autoforge.af_ir import load_graph
from autoforge.af_mcp import TOOLS, dispatch
from autoforge.af_store import GraphStore

#: 最小合法 IR（结构取自 tests/unit/test_af_conf.py 的 load_graph 用例）。
#: 边 kind 必填，取值见 ir.schema.json：then/yes/no/default/on_timeout/on_cancel/on_error。
RAW_IR = {
    "ir_version": "0.2.1",
    "id": "demo",
    "name": "demo",
    "version": 1,
    "mode": "single",
    "nodes": [
        {"id": "o", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
        {"id": "p", "kind": "pass"},
    ],
    "edges": [{"from": "o", "to": "p", "kind": "then"}],
}


def _payload(name="demo", submitted_by="alice"):
    return {
        "ir": RAW_IR,
        "name": name,
        "note": "",
        "tags": None,
        "expect_version": None,
        "owner": "",
        "allow_bulk": False,
        "_submitted_by": submitted_by,
    }


def test_save_to_pending_and_approve(tmp_path):
    store = GraphStore(str(tmp_path))
    load_graph(RAW_IR)  # 提前暴露 IR 构造错误
    payload = _payload()
    res = svc.submit_pending(store, "af_save", payload, submitted_by="alice")
    assert res["ok"] and res["pending"]
    op_id = res["pending"]
    # 落盘 pending 文件
    assert (tmp_path / "pending" / f"{op_id}.json").is_file()
    # list 可见
    listing = svc.list_pending(store)
    assert any(it["op_id"] == op_id for it in listing["items"])
    # approve 回放落盘 + 删 pending
    approved = svc.approve_pending(store, op_id, reviewer="alice")
    assert approved["ok"] and approved["approved_by"] == "alice"
    assert not (tmp_path / "pending" / f"{op_id}.json").is_file()
    # 落盘生效
    assert store.latest("demo") is not None


def test_mcp_af_save_goes_to_pending(tmp_path):
    store = GraphStore(str(tmp_path))
    # dispatch 签名：(name, args, store, current) -> (content, is_error)
    # content 为 [{"type": "text", "text": "<json>"}]；
    # af_save 属写工具，令牌需带 write scope（MCP 工具层 scope 门）。
    content, is_error = dispatch(
        "af_save",
        {"ir": RAW_IR, "name": "demo"},
        store,
        current={"subject": "alice", "scopes": ["write"]},
    )
    assert not is_error, content
    result = json.loads(content[0]["text"])
    assert result["ok"] and result["pending"], result
    assert "待人工审批" in result.get("note", "")


def test_agent_circuit_breaker(tmp_path):
    store = GraphStore(str(tmp_path))
    payload = _payload(submitted_by="bob")
    for _ in range(20):
        svc.submit_pending(store, "af_save", payload, submitted_by="bob")
    # 第 21 条触发熔断
    with pytest.raises(svc.ServiceError):
        svc.submit_pending(store, "af_save", payload, submitted_by="bob")


def test_no_approve_in_mcp():
    # 执行/批准物理分离：MCP 面绝不注册 approve / reject
    names = {name for (name, *_r) in TOOLS}
    assert "af_approve" not in names
    assert "af_reject" not in names
    assert "af_save" in names
