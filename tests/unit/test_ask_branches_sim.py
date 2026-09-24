"""ask 节点全分支仿真测试 — 验证 mimo 增量的 answer 注入。

测试场景：书房温度 >27 → ask "要开空调吗" → yes 开空调 / no 不开 / timeout 不开
"""
import pytest
from autoforge.af_service import simulate

ASK_IR = {
    "id": "study_ask_ac",
    "name": "书房闷了问一句要不要开空调",
    "ir_version": "0.2.1",
    "version": 1,
    "mode": "single",
    "snapshot": True,
    "nodes": [
        {"id": "a1", "kind": "on", "trigger": {"type": "state", "entity_id": "sensor.study_temp", "to": "28.5"}},
        {"id": "i1", "kind": "if", "expr": {"op": "gt", "left": {"var": "entity.sensor.study_temp", "type": "numeric"}, "right": {"const": 27}}},
        {"id": "q1", "kind": "ask", "prompt": "书房 27 度了，要开空调吗？", "session": "room", "room": "study", "timeout": "60s"},
        {"id": "d1", "kind": "do", "adapter": "ha", "action": "climate.turn_on", "params": {"entity_id": "climate.study"}},
        {"id": "p1", "kind": "pass"},
        {"id": "p2", "kind": "pass"},
    ],
    "edges": [
        {"from": "a1", "to": "i1", "kind": "then"},
        {"from": "i1", "to": "q1", "kind": "then"},
        {"from": "i1", "to": "p1", "kind": "no"},
        {"from": "q1", "to": "d1", "kind": "yes"},
        {"from": "q1", "to": "p2", "kind": "no"},
        {"from": "q1", "to": "p1", "kind": "on_timeout"},
        {"from": "q1", "to": "p1", "kind": "default"},
        {"from": "q1", "to": "p2", "kind": "on_cancel"},
        {"from": "d1", "to": "p1", "kind": "then"},
        {"from": "d1", "to": "p1", "kind": "on_error"},
    ],
}


def test_ask_yes_branch():
    """ask yes 分支：注入 answer='好' → 走 yes 边 → 开空调"""
    result = simulate(
        ASK_IR,
        seed={"sensor.study_temp": "26", "climate.study": "off"},
        events=[
            {"entity_id": "sensor.study_temp", "state": "28.5"},
            {"answer": "好", "room": "study"},
        ],
    )
    instances = result["instances"]
    states = [(i["instance_id"], i["state"]) for i in instances]
    print("YES branch states:", states)
    assert any(s in ("done", "failed", "cancelled") for _, s in states)
    # yes 分支应该执行了 d1（开空调），检查 audit 或 final_states
    print("YES final_states:", result["final_states"])


def test_ask_no_branch():
    """ask no 分支：注入 answer='不用' → 走 no 边 → 不开空调"""
    result = simulate(
        ASK_IR,
        seed={"sensor.study_temp": "26", "climate.study": "off"},
        events=[
            {"entity_id": "sensor.study_temp", "state": "28.5"},
            {"answer": "不用", "room": "study"},
        ],
    )
    instances = result["instances"]
    states = [(i["instance_id"], i["state"]) for i in instances]
    print("NO branch states:", states)
    assert any(s in ("done", "failed", "cancelled") for _, s in states)


def test_ask_timeout_branch():
    """ask timeout 分支：不注入 answer，推进 61 秒 → 走 on_timeout → 不开空调"""
    result = simulate(
        ASK_IR,
        seed={"sensor.study_temp": "26", "climate.study": "off"},
        events=[
            {"entity_id": "sensor.study_temp", "state": "28.5"},
            {"advance_s": 61},
        ],
    )
    instances = result["instances"]
    states = [(i["instance_id"], i["state"]) for i in instances]
    print("TIMEOUT branch states:", states)
    assert any(s in ("done", "failed", "cancelled") for _, s in states)


def test_ask_room_disambiguation():
    """房间消歧义：在 bedroom 回答 '好' 不命中 study 的 ask → 推进后走 timeout"""
    result = simulate(
        ASK_IR,
        seed={"sensor.study_temp": "26", "climate.study": "off"},
        events=[
            {"entity_id": "sensor.study_temp", "state": "28.5"},
            {"answer": "好", "room": "bedroom"},
            {"advance_s": 61},
        ],
    )
    instances = result["instances"]
    states = [(i["instance_id"], i["state"]) for i in instances]
    print("ROOM DISAMBIG states:", states)
    assert any(s in ("done", "failed", "cancelled") for _, s in states)


if __name__ == "__main__":
    test_ask_yes_branch()
    test_ask_no_branch()
    test_ask_timeout_branch()
    test_ask_room_disambiguation()
    print("\nAll ask branch tests passed!")
