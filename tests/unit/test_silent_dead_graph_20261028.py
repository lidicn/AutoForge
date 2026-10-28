"""静默死图收口（体验者 2026-10-28 卡点二：温度条件恒判假、AC 永不点火）。

现场：`if` 里 `var` 写成裸 entity_id（`sensor.duka_cn_...`）而非 `entity.sensor.duka_cn_...`。
两处独立缺陷合起来把它变成**假绿**：
1. 扫描器 `_check_expr` 只核算子/深度，命名空间不查 ⇒ `af_build` 零诊断放行；
2. 运行期 `split_namespace` 抛 ValueError ⇒ `_soft_fail` 返回 `{on_error, default}` ⇒
   挂了 `default` 边的图把"求值失败"当成"条件不成立"走到 **done**，动作永不执行；
   而 `simulate` 的 `ok` 恒 True ⇒ 三道关全绿、自动化是死的。

修法两刀：编译期新增 `EXPR_NS_INVALID`（ERROR）拦下来；运行期 if 求值失败**只认
显式 on_error 边**，无边即实例 `failed`（求值失败是执行失败，不是布尔假值）。
"""

from __future__ import annotations

from autoforge.af_ir.models import Graph, load_automation
from autoforge.af_scanner import StaticScanner
from autoforge.af_service import build, simulate

TEMP = "sensor.duka_x"


def _auto(expr: dict, *, with_default_edge: bool = True) -> dict:
    nodes = [
        {"id": "t1", "kind": "on", "name": "触发",
         "trigger": {"type": "state", "entity_id": "binary_sensor.d", "to": "off"}},
        {"id": "c1", "kind": "if", "name": "条件", "expr": expr},
        {"id": "d1", "kind": "do", "name": "开空调", "adapter": "ha",
         "action": "climate.turn_on", "params": {"entity_id": "climate.ac"}},
        {"id": "p1", "kind": "pass", "name": "结束"},
    ]
    edges = [{"from": "t1", "to": "c1", "kind": "then"}, {"from": "c1", "to": "d1", "kind": "then"}]
    if with_default_edge:
        edges.append({"from": "c1", "to": "p1", "kind": "default"})
    edges.append({"from": "d1", "to": "p1", "kind": "then"})
    return {"ir_version": "0.3.0", "id": "demo", "name": "示例", "version": 1,
            "mode": "single", "nodes": nodes, "edges": edges}


def _scan(data: dict):
    return StaticScanner(Graph([load_automation(data)])).scan()


_BAD = {"op": "gt", "left": {"var": TEMP, "type": "numeric"}, "right": {"const": 28}}
_GOOD = {"op": "gt", "left": {"var": f"entity.{TEMP}", "type": "numeric"}, "right": {"const": 28}}


# ── 第一刀：编译期拦命名空间 ──────────────────────────────────────────

def test_scanner_rejects_bare_entity_id_var():
    """裸 entity_id 的 var 必须编译期报错——过去要跑到运行期才炸、还被吞掉。"""
    scan = _scan(_auto(_BAD))
    bad = [d for d in scan.diagnostics if d.code == "EXPR_NS_INVALID"]
    assert bad, "裸 sensor.duka_x 该被 EXPR_NS_INVALID 拦下"
    assert bad[0].level == "error"
    assert "entity.sensor.duka_x" in bad[0].message, "报错要直接给出可抄的正确写法"
    assert not scan.ok


def test_scanner_accepts_entity_prefixed_var():
    """正路不误伤：`entity.` 前缀形态不得出现 EXPR_NS_INVALID。"""
    scan = _scan(_auto(_GOOD))
    assert not [d for d in scan.diagnostics if d.code == "EXPR_NS_INVALID"]


def test_scanner_accepts_vars_and_context_namespaces():
    scan = _scan(_auto({"op": "and", "args": [
        {"op": "is_on", "value": {"var": "entity.switch.pc"}},
        {"op": "truthy", "value": {"var": "vars.flag"}},
        {"op": "is_on", "value": {"var": "entity.light.x"}},
    ]}))
    assert not [d for d in scan.diagnostics if d.code == "EXPR_NS_INVALID"]


def test_scanner_allows_attribute_path_var():
    """`entity.<id>.<attr>`（v1.7.1 属性读取）是合法命名空间用法，不许误拦。"""
    scan = _scan(_auto({"op": "gt", "left": {"var": "entity.climate.ac.temperature",
                                             "type": "numeric"}, "right": {"const": 26}}))
    assert not [d for d in scan.diagnostics if d.code == "EXPR_NS_INVALID"]


def test_build_gate_refuses_bad_namespace(tmp_path):
    """af_build（第一道闸）必须拒这张图——体验者当时拿到的是 `errors: []`。"""
    from autoforge.af_store import GraphStore
    store = GraphStore(str(tmp_path / "store"))
    out = build(_auto(_BAD), store=store)
    errs = [e for e in (out.get("errors") or []) if "EXPR_NS_INVALID" in str(e)]
    assert out.get("ok") is False and errs, f"build 竟放行了坏命名空间：{str(out)[:200]}"


# ── 第二刀：运行期不许把求值失败当"条件为假" ─────────────────────────

def test_if_eval_failure_marks_instance_failed_not_done(tmp_path):
    """带 default 兜底边也不许走 done：求值失败=执行失败，只认显式 on_error。"""
    from autoforge.af_store import GraphStore
    store = GraphStore(str(tmp_path / "store"))
    r = simulate(_auto(_BAD),
                 seed={"binary_sensor.d": "on", TEMP: "29.2", "climate.ac": "off"},
                 events=[{"entity_id": "binary_sensor.d", "state": "off"}], store=store)
    insts = r.get("instances") or []
    assert insts, "事件匹配触发器，应有实例被记进结果"
    states = {i.get("state") for i in insts}
    assert "failed" in states, f"求值失败被静默吞成 {states}——静默死图回来了"
    drift = [a for a in (r.get("audit") or []) if a.get("type") == "entity_drift"]
    assert drift and "if 节点" in drift[0]["message"], "审计必须写明是 if 求值失败"


def test_if_with_explicit_on_error_still_routes_there():
    """显式挂了 on_error 边的图：求值失败走 on_error（作者意图），不硬失败。"""
    data = _auto(_BAD, with_default_edge=False)
    data["nodes"].append({"id": "x1", "kind": "pass", "name": "错误出口"})
    data["edges"].append({"from": "c1", "to": "x1", "kind": "on_error"})
    r = simulate(data, seed={"binary_sensor.d": "on", TEMP: "29.2"},
                 events=[{"entity_id": "binary_sensor.d", "state": "off"}])
    insts = r.get("instances") or []
    assert insts and all(i.get("state") == "done" for i in insts), \
        f"有 on_error 边时不该硬失败：{[i.get('state') for i in insts]}"


def test_good_namespace_expr_fires_end_to_end(tmp_path):
    """护栏：正确写法整条点火（修复不许把能跑的图改坏）。"""
    from autoforge.af_store import GraphStore
    store = GraphStore(str(tmp_path / "store"))
    r = simulate(_auto(_GOOD),
                 seed={"binary_sensor.d": "on", TEMP: "29.2", "climate.ac": "off"},
                 events=[{"entity_id": "binary_sensor.d", "state": "off"}], store=store)
    assert (r.get("final_states") or {}).get("climate.ac") == "cool", \
        f"正确写法没点火：{str(r)[:200]}"
