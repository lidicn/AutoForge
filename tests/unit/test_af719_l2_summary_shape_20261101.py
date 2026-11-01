"""AF-719（v1.15.1 热修批）：`l2_summary_of` 入口形状归一判据。

缺陷（PM 取证 20261101，线上 v1.15.0 双证）：pending 落的是**单条** IR dict
（`af_mcp._t_save` 把 `args["ir"]` 原样入 payload），而 `l2_summary_of` 旧循环
`for auto in ir_payload.get("automations")` 单条进去零次 ⇒ `l2_summary` 恒 `[]`
——漏报方向：恰含 L2 的那条审批时看不见（DCD 裁定 20261031·Q2 甲在生产待批面
不成立，AF-715 运行面因此回退未闭）。

修法（PM 裁定 20261101）：**入口归一单源**——判别只认容器键 `automations`
（⛔ 靠 `nodes` 键判，那是对消费者形状的第二份猜测），单条 ⇒ `[ir_payload]`；
三处调用点（`submit_pending`／`list_pending`／`approve_pending`）⛔ 不许各写一份
判断；⛔ `load_graph`／`af_mcp._t_save` 不动（改调用方迁就消费者会漏 REST 面）。

★ 硬判据：单条形状与包一层形状**产出必须相等**（同一条自动化、同一 `l2_summary`）。
"""

from __future__ import annotations

from autoforge.af_service import l2_summary_of


def _single(action: str, name: str = "空调", *, node_id: str = "d1") -> dict:
    return {
        "ir_version": "0.2.1", "id": "a1", "name": name, "version": 1, "mode": "single",
        "nodes": [{"id": node_id, "kind": "do", "name": "d", "adapter": "ha",
                   "action": action, "params": {"entity_id": "x"}}],
        "edges": [],
    }


def _wrapped(single: dict) -> dict:
    return {"automations": [single]}


def test_single_and_wrapped_shapes_produce_equal_results():
    """★ AF-719 直接回归钉：单条 vs 包一层，结果必须逐条相等（按形状不按行号）。"""
    for action in ("climate.turn_on", "lock.lock", "light.turn_on"):
        assert l2_summary_of(_single(action)) == l2_summary_of(_wrapped(_single(action))), action


def test_production_pending_shape_now_lists_l2():
    """生产形状（单条 dict，线上 pending `a09e3e2afb0d49dd` 的形状）此前恒 []。"""
    out = l2_summary_of(_single("climate.turn_on"))
    assert len(out) == 1, out
    assert out[0]["action"] == "climate.turn_on"
    assert out[0]["alias"] == "打开空调"
    assert out[0]["automation"] == "空调"
    assert out[0]["node"] == "d1"


def test_container_shape_behavior_unchanged():
    """容器形状回归：多条照列（既有判据面，⛔ 归一不许改容器语义）。"""
    ir = _wrapped(_single("climate.turn_on"))
    ir["automations"].append(_single("lock.lock", name="门锁", node_id="d2"))
    out = l2_summary_of(ir)
    assert {o["action"] for o in out} == {"climate.turn_on", "lock.lock"}


def test_edge_shapes_stay_empty():
    assert l2_summary_of({}) == []
    assert l2_summary_of({"automations": []}) == []
    assert l2_summary_of({"automations": None}) == []


def test_pending_chain_single_shape_lists_l2(tmp_path):
    """生产链路回归：submit_pending 单条 IR → 回执与 list_pending 每行都非空且＝1。"""
    import json

    from autoforge.af_service import list_pending, submit_pending
    from autoforge.af_store import GraphStore

    root = tmp_path / "store"
    root.mkdir()
    cat = root / ".catalog" / "catalog.json"
    cat.parent.mkdir(parents=True, exist_ok=True)
    cat.write_text(json.dumps({"version": 1, "freshness": "", "entities": {
        "climate.x": {"state": "off", "domain": "climate"}}}, ensure_ascii=False),
        encoding="utf-8")
    store = GraphStore(str(root))
    single = _single("climate.turn_on")
    single["nodes"][0]["params"] = {"entity_id": "climate.x"}
    # 卡 B 保留的矩阵 3/4 行强制：L2 动作带部署期显眼标记＋canary 才过静态闸
    single["nodes"][0]["requires_confirm"] = True
    single["nodes"][0]["canary"] = {"duration": "15m", "auto_rollback": True}
    single["nodes"].insert(0, {"id": "o1", "kind": "on", "name": "o",
                               "trigger": {"type": "state", "entity_id": "climate.x", "to": "on"}})
    single["edges"] = [{"from": "o1", "to": "d1", "kind": "then"}]
    res = submit_pending(store, "af_save", {"ir": single, "name": "n"})
    item = list_pending(store)["items"][0]
    assert len(res["l2_summary"]) == 1, res.get("l2_summary")
    assert item["l2_summary"] == res["l2_summary"]
    assert item["l2_summary"][0]["action"] == "climate.turn_on"
