# -*- coding: utf-8 -*-
"""判据 · 门牙二连：路径门「路径＋方法二元组」认领（卡号 AF-V129-G2.6-20261111）。

**为什么单开一枚 gate 命名文件**：门牙批 ⑪ 教训——判据文件名不含 `gate` 会被
`pytest tests/unit -k gate` 族扫漏（族计数"零漂移"是假象）。本文件因此自带 `_gate` 后缀。

**盲区原状**（AF-UI 批票 `3EC2FBEE` 抓出、PM 裁定入池第三例「考官没被考过」）：
`_claimed()` 只比路径形状 ⇒ 一条 `GET /api/grants` 的 UI 调用点把同路径的
`POST /api/grants`（签发）一并认领，后者**从反向名单消失**（反向读数虚减 2）。

钉的三句话：
- **P1 方法维度**：同路径异方法**不被认领**（GET 调用点认不走 POST 路由）。
- **P2 计数面**：`_unused_routes` 在「同路径两条方法」的夹具上留 1 条（⛔ 虚减到 0）。
- **P3 不改判定宽度**：同路径同方法仍被认领（修的是方法维度，⛔ 顺手把认领改宽/改窄）。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GATE = REPO / "scripts" / "check_ui_api_paths.py"


def _mod():
    spec = importlib.util.spec_from_file_location("ui_api_paths_gate_ut", GATE)
    assert spec is not None and spec.loader is not None, "读不出门脚本——射程塌了"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _site(path: str, method: str = "GET", rel: str = "ui-user-mimo/src/api.ts", line: int = 1):
    return {"rel": rel, "line": line, "method": method, "paths": [path], "exempt": False}


def _route(path: str, method: str):
    return {"path": path, "method": method, "rel": "src/autoforge/af_api.py", "line": 1}


# ── P1：方法维度 ───────────────────────────────────────────────────────
def test_p1_same_path_other_method_is_not_claimed():
    mod = _mod()
    # 同路径同方法 ⇒ 认领（正例腿：判据不是恒拒）
    assert mod._claimed("/api/grants", "/api/grants", "GET", "GET") is True
    # 同路径异方法 ⇒ **不认领**（这正是盲区）
    assert mod._claimed("/api/grants", "/api/grants", "POST", "GET") is False
    assert mod._claimed("/api/orders", "/api/orders", "POST", "GET") is False


def test_p1_directional_pair_for_grants_and_orders():
    """两条被误认领的实测面：GET 调用点不许认走 POST 路由。"""
    mod = _mod()
    for path in ("/api/grants", "/api/orders"):
        assert mod._claimed(path, path, "GET", "GET") is True, path
        assert mod._claimed(path, path, "POST", "GET") is False, path


# ── P2：反向名单计数面（虚减 2 的原始形状）────────────────────────────
def test_p2_unused_routes_keeps_the_unclaimed_method():
    mod = _mod()
    sites = [_site("/api/grants", "GET"), _site("/api/orders", "GET")]
    routes = [
        _route("/api/grants", "GET"), _route("/api/grants", "POST"),
        _route("/api/orders", "GET"), _route("/api/orders", "POST"),
    ]
    unused = mod._unused_routes(sites, routes)
    pairs = {(r["method"], r["path"]) for r in unused}
    assert pairs == {("POST", "/api/grants"), ("POST", "/api/orders")}, pairs


def test_p2_count_is_two_not_zero():
    """计数面直接钉：修前这两条会被 GET 调用点吃掉（未调用数 0），修后必须是 2。"""
    mod = _mod()
    sites = [_site("/api/grants", "GET"), _site("/api/orders", "GET")]
    routes = [_route("/api/grants", "GET"), _route("/api/grants", "POST"),
              _route("/api/orders", "GET"), _route("/api/orders", "POST")]
    assert len(mod._unused_routes(sites, routes)) == 2


# ── P3：判定宽度不变（同方法仍认领；无方法信息时退回旧口径）───────────
def test_p3_same_method_still_claims():
    mod = _mod()
    sites = [_site("/api/grants", "POST")]
    routes = [_route("/api/grants", "GET"), _route("/api/grants", "POST")]
    unused = mod._unused_routes(sites, routes)
    assert [r["method"] for r in unused] == ["GET"], unused
    # 反向：GET 调用点只认走 GET 那条，POST 留在名单里（P1 的名单面复述）
    unused2 = mod._unused_routes([_site("/api/grants", "GET")], routes)
    assert [r["method"] for r in unused2] == ["POST"], unused2


def test_p3_missing_method_falls_back_to_path_only():
    """没给方法（老调用形状）⇒ 仍按路径认领：⛔ 因缺信息就把名单涨一截。"""
    mod = _mod()
    assert mod._claimed("/api/grants", "/api/grants") is True
    assert mod._claimed("/api/grants", "/api/grants", "", "GET") is True


def test_p3_transport_wrapper_also_respects_method():
    """传输层包装（`${base}/api/x` 那类）同样带方法维度。"""
    mod = _mod()
    mark = getattr(mod, "MARK", None)
    assert mark, "门里没有 MARK 常量——包装面口径变了，本腿要重写"
    wrapped = f"{mark}/api/grants"
    assert mod._claimed(wrapped, "/api/grants", "GET", "GET") is True
    assert mod._claimed(wrapped, "/api/grants", "POST", "GET") is False
