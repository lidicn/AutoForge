"""v1.0.1 MCP stdio server 单测。

覆盖：dispatch 工具功能 / scope 门（v0.8.0 凭证复用）/ 整轮 stdio 协议（initialize→tools/list→tools/call）。
不依赖 `mcp` 外部包（零依赖实现），纯子进程 + 进程内 dispatch 双路径验证。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from autoforge.af_ir import Graph, load_graph
from autoforge.af_mcp import dispatch
from autoforge.af_store import GraphStore

_DEMO_IR = {
    "ir_version": "0.2.1", "id": "demo", "name": "demo", "version": 1, "mode": "single",
    "nodes": [
        {"id": "a", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
        {"id": "d", "kind": "do", "adapter": "mock", "action": "light.turn_on", "params": {"entity_id": "light.x"}},
        {"id": "p", "kind": "pass"},
    ],
    "edges": [
        {"from": "a", "to": "d", "kind": "then"},
        {"from": "d", "to": "p", "kind": "then"},
    ],
}

_L3_IR = {
    "ir_version": "0.2.1", "id": "bad", "name": "bad", "version": 1, "mode": "single",
    "nodes": [
        {"id": "a", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
        {"id": "d", "kind": "do", "adapter": "http", "action": "get", "params": {"url": "https://x"}},
        {"id": "p", "kind": "pass"},
    ],
    "edges": [
        {"from": "a", "to": "d", "kind": "then"},
        {"from": "d", "to": "p", "kind": "then"},
    ],
}

_READ_ONLY = {"subject": "ro", "scopes": ["read"]}
_WRITE = {"subject": "rw", "scopes": ["read", "write"]}


def _seed(store: GraphStore) -> None:
    g = load_graph(_DEMO_IR)
    store.save(g, "demo", note="v1")
    store.save(g, "demo", note="v2")
    store.set_tags("demo", ["lighting", "test"])


def _load(content: str) -> dict:
    return json.loads(content)


# ── dispatch 功能 ─────────────────────────────────────────────────────


def test_dispatch_health_no_auth(tmp_path):
    store = GraphStore(tmp_path)
    ok, err = dispatch("af_health", {}, store, None)
    assert err is False
    assert _load(ok[0]["text"])["ok"] is True


def test_dispatch_health_probes_the_store_it_serves(tmp_path):
    """Agent 面的 health 读数必须探它自己服务的那个 store。

    `_t_health` 一度写成 `svc.health()`：`store` 形参带默认值 ⇒ 漏传不报错、不崩，
    只是 `store_ok` 永远是 `null`——"这一栏我没看"被下游读成"这一栏没问题"（铁律 #5）。
    HTTP 面（`af_api`）早就递了 store，两面对不上是本缺陷的形状；`scripts/check_store_injection.py` 常驻判这一族。
    """
    store = GraphStore(tmp_path)
    ok, err = dispatch("af_health", {}, store, None)
    assert err is False
    assert _load(ok[0]["text"])["store_ok"] is True


def test_dispatch_build_accepts_valid_ir(tmp_path):
    store = GraphStore(tmp_path)
    content, is_error = dispatch("af_build", {"ir": _DEMO_IR}, store, None)
    assert is_error is False
    assert _load(content[0]["text"])["ok"] is True


def test_dispatch_build_rejects_l3(tmp_path):
    store = GraphStore(tmp_path)
    content, is_error = dispatch("af_build", {"ir": _L3_IR}, store, None)
    assert is_error is False  # 业务结果，非协议错误
    body = _load(content[0]["text"])
    assert body["ok"] is False
    assert any(d["code"] == "L3_ACTION" for d in body.get("errors", []))


def test_dispatch_read_tools_touch_store(tmp_path):
    store = GraphStore(tmp_path)
    _seed(store)
    content, _ = dispatch("af_list_graphs", {}, store, None)
    assert _load(content[0]["text"])["items"][0]["name"] == "demo"

    content, _ = dispatch("af_get_graph", {"name": "demo", "version": 2}, store, None)
    assert _load(content[0]["text"])["version"] == 2

    content, _ = dispatch("af_graphs_by_tag", {"tag": "lighting"}, store, None)
    assert _load(content[0]["text"])["items"][0]["name"] == "demo"

    content, _ = dispatch("af_diff", {"name": "demo", "old": 1, "new": 2}, store, None)
    assert "render" in _load(content[0]["text"])


def test_dispatch_export_import_roundtrip(tmp_path):
    store = GraphStore(tmp_path)
    _seed(store)
    exported, _ = dispatch("af_export_store", {}, store, None)
    body = _load(exported[0]["text"])
    assert body["names"] == ["demo"]
    bundle = body["bundle"]
    # 导入到新 store（rename 避免碰撞）
    other = GraphStore(tmp_path / "other")
    imported, _ = dispatch("af_import_store", {"bundle": bundle, "strategy": "rename"}, other, None)
    report = _load(imported[0]["text"])
    assert report["imported"] and len(report["imported"]) >= 1


def test_dispatch_whoami_reflects_auth_state(tmp_path):
    store = GraphStore(tmp_path)
    content, _ = dispatch("af_whoami", {}, store, _READ_ONLY)
    assert _load(content[0]["text"])["scopes"] == ["read"]
    content, _ = dispatch("af_whoami", {}, store, None)
    assert _load(content[0]["text"])["auth_enabled"] is False


def test_dispatch_save_queues_valid_ir_to_pending(tmp_path):
    """v1.4.0：af_save 改为**先入待批队列**（部署前人审），MCP 面不直接落盘。

    落盘只在服务层 approve 后发生（见 test_v1_4_pending.py::test_save_to_pending_and_approve）。
    """
    store = GraphStore(tmp_path)
    content, is_error = dispatch(
        "af_save",
        {"name": "living_light", "ir": _DEMO_IR, "note": "首个版本", "tags": ["lighting"]},
        store,
        None,
    )
    assert is_error is False
    body = _load(content[0]["text"])
    assert body["ok"] is True and body["pending"]  # 入队成功，返回 op_id
    assert "待人工审批" in body["note"]
    # 尚未落盘（要人审 approve 后才落盘）
    listed, _ = dispatch("af_list_graphs", {}, store, None)
    assert "living_light" not in {it["name"] for it in _load(listed[0]["text"])["items"]}


def test_dispatch_save_rejects_invalid_ir(tmp_path):
    store = GraphStore(tmp_path)
    content, is_error = dispatch("af_save", {"name": "bad", "ir": _L3_IR}, store, None)
    assert is_error is True  # 拒绝归档 → ServiceError → MCP isError
    assert "拒绝归档" in content[0]["text"]
    # 未落盘
    listed, _ = dispatch("af_list_graphs", {}, store, None)
    assert "bad" not in {it["name"] for it in _load(listed[0]["text"])["items"]}


def test_scope_guard_save_blocked_with_readonly_token(tmp_path):
    store = GraphStore(tmp_path)
    content, is_error = dispatch("af_save", {"name": "x", "ir": _DEMO_IR}, store, _READ_ONLY)
    assert is_error is True
    assert "write" in content[0]["text"]


# ── scope 门（v0.8.0 凭证复用）──────────────────────────────────────


def test_scope_guard_write_blocked_with_readonly_token(tmp_path):
    store = GraphStore(tmp_path)
    content, is_error = dispatch("af_set_tags", {"name": "demo", "tags": ["x"]}, store, _READ_ONLY)
    assert is_error is True
    assert "write" in content[0]["text"]


def test_scope_guard_write_allowed_with_write_token(tmp_path):
    store = GraphStore(tmp_path)
    _seed(store)
    content, is_error = dispatch("af_set_tags", {"name": "demo", "tags": ["y"]}, store, _WRITE)
    assert is_error is False
    assert "y" in _load(content[0]["text"])["tags"]


def test_scope_guard_no_auth_allows_write(tmp_path):
    store = GraphStore(tmp_path)
    content, is_error = dispatch("af_export_store", {}, store, None)
    assert is_error is False


def test_dispatch_unknown_tool(tmp_path):
    store = GraphStore(tmp_path)
    content, is_error = dispatch("af_nope", {}, store, None)
    assert is_error is True and "未知工具" in content[0]["text"]


# ── 整轮 stdio 协议（子进程）──────────────────────────────────────────


class _McpClient:
    def __init__(self, root: str):
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "autoforge.af_mcp"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1, env={**__import__("os").environ, "AUTOFORGE_STORE_ROOT": root},
        )

    def _send(self, msg: dict) -> dict:
        self.proc.stdin.write(json.dumps(msg) + "\n")  # type: ignore[union-attr]
        self.proc.stdin.flush()  # type: ignore[union-attr]
        line = self.proc.stdout.readline()  # type: ignore[union-attr]
        assert line, "MCP server 无响应"
        return json.loads(line)

    def initialize(self):
        return self._send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})

    def tools_list(self):
        return self._send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})

    def call(self, name: str, args: dict):
        return self._send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                           "params": {"name": name, "arguments": args}})

    def close(self):
        self.proc.terminate()  # type: ignore[union-attr]
        try:
            self.proc.wait(timeout=5)  # type: ignore[union-attr]
        except subprocess.TimeoutExpired:
            self.proc.kill()  # type: ignore[union-attr]


def test_stdio_protocol_roundtrip(tmp_path):
    client = _McpClient(str(tmp_path))
    try:
        init = client.initialize()
        assert init["result"]["protocolVersion"] == "2024-11-05"
        assert init["result"]["serverInfo"]["name"] == "autoforge-mcp"

        listing = client.tools_list()
        names = {t["name"] for t in listing["result"]["tools"]}
        # 全部 16 个工具都在，且 inputSchema 齐全
        assert len(names) == len({t[0] for t in __import__("autoforge.af_mcp", fromlist=["TOOLS"]).TOOLS})
        for expected in ("af_health", "af_build", "af_simulate", "af_set_tags", "af_save",
                         "af_import_store", "af_live_run", "af_whoami"):
            assert expected in names
        for tool in listing["result"]["tools"]:
            assert tool["inputSchema"]["type"] == "object"

        resp = client.call("af_health", {})
        assert resp["result"]["isError"] is False
        assert json.loads(resp["result"]["content"][0]["text"])["ok"] is True
    finally:
        client.close()
