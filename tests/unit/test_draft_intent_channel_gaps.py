"""G1/G2/G4 修复判据腿（实测自动化缺口 2026-10-23 第一批）。

背景：2026-10-23 配对恢复后跑「房间温度≥28 → 开空调」端到端实测，抓到三个阻断级
缺口（docs/缺口登记_实测自动化_20261023.md）：

- G1：`_build_edges` 给 if 后首动作写 `yes` 边，执行器 if 求值 True 返回 `{"then"}`
  ⇒ `pick_edge` 匹配不上 ⇒ 实例静默终止，仿真却报 `ok: True`。
- G2：`_resolve_expr` 用 `"." in var_name` 判"已带命名空间"，把 agent 按目录返回值
  直填的 entity_id（`sensor.xxx`）误判 ⇒ 求值时 `split_namespace` 抛未知命名空间。
- G4：MCP `_t_draft` 不传 catalog ⇒ 中文设备名全部 `?占位`、别名沉淀无效。

本文件钉的是**行为级**判据（图形状 + 仿真 trace 走到动作节点），不是行号形状——
三条腿对应三个缺口，另带三条回归腿（ask 语义、namespaced 透传、无 catalog 兜底）。
"""

from __future__ import annotations

import pytest

from autoforge.af_draft import DraftError, draft_intent, get_staged
from autoforge.af_spec import graph_to_raw
from autoforge.af_service import simulate


# ── 测试工具 ─────────────────────────────────────────────────────────


class _FakeCatalog:
    """最小 catalog 桩：只实现 `resolve_best`/`resolve`（DeviceCatalog 的两个入口）。

    G4 的判据核心是「中文名 → entity_id」经 catalog 解析后**不再落 `?占位`**，
    故意不建真实 store/目录文件——那会让判据腿依赖盘上状态。
    """

    _MAP = {
        "房间温度计": "sensor.linp_room_temp",
        "房间空调": "climate.room_ac",
    }

    def resolve_best(self, name: str):
        return self._MAP.get(name)

    def resolve(self, name: str, domain: str = "", top_n: int = 8):
        eid = self._MAP.get(name)
        if eid:
            return {"ok": True, "candidates": [{"entity_id": eid, "confidence": "high"}]}
        return {"ok": False, "candidates": []}


def _draft_ir(intent: dict) -> dict:
    """draft → staging → raw IR（单条自动化的 list-of-dict 形态）。"""
    out = draft_intent(intent)
    assert out["ok"] is True
    return {"automations": graph_to_raw(get_staged(out["ref"])["graph"])}


def _first_auto(ir: dict) -> dict:
    return ir["automations"][0]


def _node(auto: dict, kind: str) -> dict:
    return next(n for n in auto["nodes"] if n["kind"] == kind)


# ── G1：if 真分支边必须是 then（执行器口径）─────────────────────────


def test_g1_if_true_edge_is_then_not_yes():
    """意图带 if 时，if→首动作的边必须是 `then`——`yes` 会让 pick_edge 匹配不上，
    实例静默终止（本缺口的直接病灶）。"""
    ir = _draft_ir({
        "name": "g1",
        "when": {"type": "state", "entity": "sensor.temp", "to": "28.3"},
        "if": {"gte": {"var": "sensor.temp", "const": 28, "type": "numeric"}},
        "do": {"action": "set_state", "target": "climate.ac", "state": "cool"},
    })
    auto = _first_auto(ir)
    if_node = _node(auto, "if")
    do_node = _node(auto, "do")
    out_edges = [e for e in auto["edges"] if e["from"] == if_node["id"]]
    kinds = {e["kind"] for e in out_edges}
    # 真分支 then（执行器 if→True 返回 {"then"}），假分支 no
    assert "then" in kinds, f"if 真分支必须是 then 边，实际 {kinds}"
    assert "yes" not in kinds, f"if 出边不许有 yes（G1 病灶）：{kinds}"
    # 真分支要真的连到 do 节点
    assert any(e["kind"] == "then" and e["to"] == do_node["id"] for e in out_edges)


def test_g1_if_entry_to_ask_edge_is_then():
    """if→ask 入边同样是 then（旧实现 `if_entry` 时写 yes，同病灶的第二处）。"""
    ir = _draft_ir({
        "name": "g1b",
        "when": {"type": "state", "entity": "sensor.temp", "to": "on"},
        "if": {"gte": {"var": "sensor.temp", "const": 20, "type": "numeric"}},
        "ask": {"prompt": "执行吗？"},
    })
    auto = _first_auto(ir)
    if_node = _node(auto, "if")
    ask_node = _node(auto, "ask")
    edge = next(
        e for e in auto["edges"] if e["from"] == if_node["id"] and e["to"] == ask_node["id"]
    )
    assert edge["kind"] == "then", f"if→ask 边必须是 then，实际 {edge['kind']}"


def test_g1_simulate_trace_reaches_action_node():
    """端到端判据（本缺口最重的一条）：条件成立时实例必须**走到动作节点**。

    旧实现实例停在 if 节点静默终止（trace 无 do），仿真整体却报 ok——
    这条腿直接钉「跑完了」和「跑对了」的差：trace 里必须出现 do 节点。
    """
    ir = _draft_ir({
        "name": "g1c",
        "when": {"type": "state", "entity": "sensor.temp"},
        "if": {"gte": {"var": "sensor.temp", "const": 28, "type": "numeric"}},
        "do": {"action": "light.turn_on", "target": "light.lamp"},
    })
    out = simulate(
        ir,
        seed={"sensor.temp": "27"},
        events=[{"entity_id": "sensor.temp", "state": "28.3"}],
    )
    assert out["ok"] is True
    assert out["instances"], "条件成立必须产生实例"
    trace_nodes = [t["node"] for t in out["instances"][0]["trace"]]
    do_node = _node(_first_auto(ir), "do")
    assert do_node["id"] in trace_nodes, (
        f"实例必须到达动作节点（G1 病灶：静默终止），trace={trace_nodes}"
    )


def test_g1_simulate_condition_false_takes_no_edge():
    """对称回归：条件不成立走 `no` 边直接结束——不许因为修 G1 把假分支也断了。"""
    ir = _draft_ir({
        "name": "g1d",
        "when": {"type": "state", "entity": "sensor.temp"},
        "if": {"gte": {"var": "sensor.temp", "const": 28, "type": "numeric"}},
        "do": {"action": "light.turn_on", "target": "light.lamp"},
    })
    out = simulate(
        ir,
        seed={"sensor.temp": "25"},
        events=[{"entity_id": "sensor.temp", "state": "26"}],
    )
    if out["instances"]:
        trace_nodes = [t["node"] for t in out["instances"][0]["trace"]]
        do_node = _node(_first_auto(ir), "do")
        assert do_node["id"] not in trace_nodes, "条件不成立不许走到动作节点"


# ── G2：var 直填 entity_id 必须包装 entity. 前缀──────────────────────


def test_g2_var_with_entity_id_gets_entity_prefix():
    """agent 按目录返回值直填 var（`sensor.xxx`）——必须自动包 `entity.` 前缀。

    旧实现 `"." in var_name` 判成"已带命名空间"原样透传 ⇒ split_namespace
    抛「未知命名空间 'sensor'」⇒ IF_NODE_EXEC_ERROR 软失败。
    """
    ir = _draft_ir({
        "name": "g2",
        "when": {"type": "state", "entity": "sensor.temp"},
        "if": {"gte": {"var": "sensor.temp", "const": 28, "type": "numeric"}},
        "do": {"action": "light.turn_on", "target": "light.lamp"},
    })
    if_node = _node(_first_auto(ir), "if")
    assert if_node["expr"]["left"]["var"] == "entity.sensor.temp"


def test_g2_namespaced_var_passthrough():
    """回归：显式 `entity.` 前缀（IR 高级写法）必须原样透传，不许二次包装。"""
    ir = _draft_ir({
        "name": "g2b",
        "when": {"type": "state", "entity": "binary_sensor.door", "to": "on"},
        "if": {"gte": {"var": "entity.sensor.lux", "const": 200, "type": "numeric"}},
        "do": {"action": "开灯", "target": "light.lamp"},
    })
    if_node = _node(_first_auto(ir), "if")
    assert if_node["expr"]["left"]["var"] == "entity.sensor.lux"


def test_g2_vars_namespace_passthrough():
    """回归：`vars.` 前缀同样透传（真源三命名空间：entity/vars/context）。"""
    ir = _draft_ir({
        "name": "g2c",
        "when": {"type": "state", "entity": "binary_sensor.door", "to": "on"},
        "if": {"eq": {"var": "vars.mode", "const": "night"}},
        "do": {"action": "开灯", "target": "light.lamp"},
    })
    if_node = _node(_first_auto(ir), "if")
    assert if_node["expr"]["left"]["var"] == "vars.mode"


def test_g2_unary_predicate_entity_id_prefix():
    """一元谓词（is_on 等）同病灶：var 填 entity_id 也必须包前缀。"""
    ir = _draft_ir({
        "name": "g2d",
        "when": {"type": "state", "entity": "binary_sensor.door", "to": "on"},
        "if": {"is_on": {"var": "light.lamp"}},
        "do": {"action": "light.turn_off", "target": "light.lamp"},
    })
    if_node = _node(_first_auto(ir), "if")
    assert if_node["expr"]["value"]["var"] == "entity.light.lamp"


def test_g2_simulate_no_namespace_error():
    """端到端判据：真 entity_id 直填的意图必须能全程求值（不再 IF_NODE_EXEC_ERROR）。

    求值失败的形态是实例软失败（trace 停在 if、audit 落 soft_fail）；
    这条腿钉「seed 值就能让条件成立」的正路必须通。
    """
    ir = _draft_ir({
        "name": "g2e",
        "when": {"type": "state", "entity": "sensor.temp"},
        "if": {"gte": {"var": "sensor.temp", "const": 28, "type": "numeric"}},
        "do": {"action": "light.turn_on", "target": "light.lamp"},
    })
    out = simulate(
        ir,
        seed={"sensor.temp": "27"},
        events=[{"entity_id": "sensor.temp", "state": "29"}],
    )
    # 不再有「未知命名空间」：条件成立 → 到达动作（与 g1c 同一硬判据）
    trace_nodes = [t["node"] for t in out["instances"][0]["trace"]]
    do_node = _node(_first_auto(ir), "do")
    assert do_node["id"] in trace_nodes


# ── G4：catalog 注入——中文设备名不再落占位──────────────────────────


def test_g4_catalog_resolves_chinese_names():
    """带 catalog 的 draft：中文设备名必须解析成 entity_id（`?占位` 不许再出现）。"""
    out = draft_intent(
        {
            "name": "g4",
            "when": {"type": "state", "entity": "房间温度计"},
            "if": {"gte": {"var": "房间温度计", "const": 28, "type": "numeric"}},
            "do": {"action": "set_state", "target": "房间空调", "state": "cool"},
        },
        catalog=_FakeCatalog(),
    )
    assert out["ok"] is True
    assert out["resolved"] == {
        "房间温度计": "sensor.linp_room_temp",
        "房间空调": "climate.room_ac",
    }, f"中文设备名必须经 catalog 解析：{out['resolved']}"


def test_g4_catalog_var_uses_resolved_entity():
    """catalog 命中时，if 的 var 侧也用**解析后的 entity_id**（G2+G4 联动）。"""
    out = draft_intent(
        {
            "name": "g4b",
            "when": {"type": "state", "entity": "房间温度计"},
            "if": {"gte": {"var": "房间温度计", "const": 28, "type": "numeric"}},
            "do": {"action": "set_state", "target": "房间空调", "state": "cool"},
        },
        catalog=_FakeCatalog(),
    )
    auto = graph_to_raw(get_staged(out["ref"])["graph"])[0]
    if_node = _node(auto, "if")
    assert if_node["expr"]["left"]["var"] == "entity.sensor.linp_room_temp"
    do_node = _node(auto, "do")
    assert do_node["params"]["entity_id"] == "climate.room_ac"


def test_g4_without_catalog_still_fails_closed():
    """回归：无 catalog（CLI/库直调）时照旧 fail-closed——`?占位` 交给 bind 闸门，
    不许因为 G4 改动悄悄退化成"猜一个实体透传"。"""
    out = draft_intent({
        "name": "g4c",
        "when": {"type": "state", "entity": "房间温度计"},
        "do": {"action": "set_state", "target": "房间空调", "state": "cool"},
    })
    assert out["ok"] is True
    assert out["resolved"] == {"房间温度计": "?房间温度计", "房间空调": "?房间空调"}


def test_g4_alias_flow_via_resolve_best():
    """`af_remember_entity` 沉淀的别名对意图通道生效的判据（G4 的原始动机）：
    resolve_best 命中（别名/唯一候选/high 置信）即等价 catalog 解析成功。"""
    out = draft_intent(
        {
            "name": "g4d",
            "when": {"type": "state", "entity": "房间温度计"},
            "do": {"action": "set_state", "target": "房间空调", "state": "cool"},
        },
        catalog=_FakeCatalog(),
    )
    assert "?房间温度计" not in out["resolved"].values()
    assert "?房间空调" not in out["resolved"].values()


# ── ask 语义回归（G1 修复不许误伤）──────────────────────────────────


def test_ask_yes_edge_semantics_preserved():
    """ask 节点的应答边保持不变——`yes` 属于 ask（应答放行），不是 if；
    这条腿防止把 G1 修过头（把 ask 的 yes 也改掉）。

    af_draft 生成的 ask 出边是 yes/on_timeout/on_cancel（拒答走默认落 done，
    没有 no 边——这是既有简化语义，与本批无关）；判据只钉「yes 必须在」。
    """
    ir = _draft_ir({
        "name": "ask-regression",
        "when": {"type": "state", "entity": "binary_sensor.door", "to": "on"},
        "ask": {"prompt": "执行吗？"},
    })
    auto = _first_auto(ir)
    ask_node = _node(auto, "ask")
    kinds = {e["kind"] for e in auto["edges"] if e["from"] == ask_node["id"]}
    assert "yes" in kinds, f"ask 出边必须保留 yes（应答放行）：{kinds}"
    assert {"on_timeout", "on_cancel"} <= kinds, f"ask 超时/取消出边必须保留：{kinds}"


def test_ask_without_if_still_compiles():
    """无 if 的 ask 直连（then 入边）回归。"""
    ir = _draft_ir({
        "name": "ask-plain",
        "when": {"type": "state", "entity": "binary_sensor.door", "to": "on"},
        "ask": {"prompt": "执行吗？"},
        "do": {"action": "开灯", "target": "light.lamp"},
    })
    auto = _first_auto(ir)
    ask_node = _node(auto, "ask")
    # ask→do 的边：ask 应答 yes 后放行到动作
    kinds = [e["kind"] for e in auto["edges"] if e["from"] == ask_node["id"]]
    assert "yes" in kinds


# ── G1 的另一个对称面：requires_confirm 的 do 出边───────────────────


def test_confirm_do_node_edges_unchanged():
    """requires_confirm 的 do 节点照旧 then/on_error 出边（confirm 唤醒走 resume，
    不依赖 yes 边）——G1 修复面只动 if/ask 入边，这条钉住不误伤。"""
    ir = _draft_ir({
        "name": "confirm-regression",
        "when": {"type": "state", "entity": "binary_sensor.door", "to": "on"},
        "do": {
            "action": "set_state",
            "target": "climate.ac",
            "state": "cool",
            "requires_confirm": True,
        },
    })
    auto = _first_auto(ir)
    do_node = _node(auto, "do")
    kinds = {e["kind"] for e in auto["edges"] if e["from"] == do_node["id"]}
    assert "then" in kinds and "on_error" in kinds, f"do 出边保持 then/on_error：{kinds}"


# ── 组合链端到端（G1 修复后最容易回归的全链矩阵）────────────────────


def test_chain_if_ask_do_reaches_action():
    """if+ask+do 三段链：条件成立 + 答应 → 必须到达动作。

    G1 的三处病灶（if 边 / ask 边 / wait 入边）在组合链里各自独立生效——
    这条腿钉「全部修好」的合取。注意事件序：条件实体先抬、触发实体后动
    （if 求值冻结在 spawn 快照——这是语义，不是缺陷）。
    """
    ir = _draft_ir({
        "name": "chain-if-ask-do",
        "when": {"type": "state", "entity": "binary_sensor.door", "to": "on"},
        "if": {"gte": {"var": "sensor.lux", "const": 200, "type": "numeric"}},
        "ask": {"prompt": "开灯吗？"},
        "do": {"action": "light.turn_on", "target": "light.lamp"},
    })
    out = simulate(
        ir,
        seed={"sensor.lux": "100", "binary_sensor.door": "off", "light.lamp": "off"},
        events=[
            {"entity_id": "sensor.lux", "state": "300"},
            {"entity_id": "binary_sensor.door", "state": "on"},
            {"answer": "yes"},
        ],
    )
    trace_nodes = [t["node"] for t in out["instances"][0]["trace"]]
    do_node = _node(_first_auto(ir), "do")
    assert do_node["id"] in trace_nodes, (
        f"if+ask+do 链必须到达动作（G1 三病灶合取判据），trace={trace_nodes}"
    )
    assert out["final_states"]["light.lamp"] == "on"


def test_chain_ask_wait_do_reaches_action():
    """ask+wait+do 链：答应 → 等 5s → 到达动作（ask→wait 的 yes 边 + wait 唤醒）。"""
    ir = _draft_ir({
        "name": "chain-ask-wait-do",
        "when": {"type": "state", "entity": "binary_sensor.door", "to": "on"},
        "ask": {"prompt": "开灯吗？"},
        "wait": "5s",
        "do": {"action": "light.turn_on", "target": "light.lamp"},
    })
    out = simulate(
        ir,
        seed={"binary_sensor.door": "off", "light.lamp": "off"},
        events=[
            {"entity_id": "binary_sensor.door", "state": "on"},
            {"answer": "yes"},
            {"advance_s": 30},
        ],
    )
    trace_nodes = [t["node"] for t in out["instances"][0]["trace"]]
    do_node = _node(_first_auto(ir), "do")
    assert do_node["id"] in trace_nodes, f"ask+wait+do 链必须到达动作，trace={trace_nodes}"
    assert out["final_states"]["light.lamp"] == "on"
