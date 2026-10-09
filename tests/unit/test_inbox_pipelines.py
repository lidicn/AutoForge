"""卡1 验收点名的三管线：编译（DSL→IR→schema→静态扫描）/ 仿真 / NL 渲染。

计划 §七 卡1 的原文是「IR 新增 `inbox_speak`/`inbox_notify`/`inbox_tv` 节点」，而 AF 的 IR 里
出向动作只有 `do` + `adapter` + `action` 一种形态（`NODE_KINDS` 不含按业务命名的节点类型，
另立一族会同时破 `ir.schema.json` 与 `classify_action` 的分级）。所以这里的"三管线"跑的就是
`do d1 inbox.speak {…}`——**同一份能力必须在三套管线上都到位**，缺一面就是"某张脸接不到 DB"。

上线那一半（`mosquitto_sub` 能见）需要 NAS 合并窗与 broker，本文件不假装验过。
"""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from autoforge.af_cli import app
from autoforge.af_ir import Graph, load_automation, load_graph
from autoforge.af_nl import render_graph
from autoforge.af_runtime import build_runtime
from autoforge.af_scanner import StaticScanner
from autoforge.af_service import simulate
from autoforge.af_spec import compile_spec, graph_to_raw, render_spec

_SPEC = """
automation hall_butler
name "走廊有人请播报"
ir_version "0.2.1"
version 1
mode restart

on a1 {"type": "state", "entity_id": "binary_sensor.hall_motion", "to": "on"} name "走廊来人"
do d1 inbox.speak {"text": "走廊有人，请播报欢迎词"}
do d2 inbox.notify {"title": "走廊来人", "body": "已连续触发 3 次"}
pass p1

edge a1 -> d1 then
edge d1 -> d2 then
edge d2 -> p1 then
"""


def _auto(**over) -> dict:
    data = {
        "ir_version": "0.2.1",
        "id": "hall_butler",
        "name": "走廊有人请播报",
        "version": 1,
        "mode": "single",
        "nodes": [
            {
                "id": "a1",
                "kind": "on",
                "trigger": {"type": "state", "entity_id": "binary_sensor.hall_motion", "to": "on"},
            },
            {"id": "d1", "kind": "do", "adapter": "inbox", "action": "speak", "params": {"text": "走廊有人"}},
            {"id": "p1", "kind": "pass"},
        ],
        "edges": [{"from": "a1", "to": "d1", "kind": "then"}, {"from": "d1", "to": "p1", "kind": "then"}],
    }
    data.update(over)
    return data


# ── 管线 1：编译 ───────────────────────────────────────────────────────
def test_dsl_compiles_inbox_actions_without_any_spec_language_change():
    """af_spec 本来就是按 `adapter.action` 切分，`inbox` 不需要新语法就能编译。"""
    graph = compile_spec(_SPEC)
    auto = graph.get("hall_butler")

    assert auto.node("d1").adapter == "inbox" and auto.node("d1").action == "speak"
    assert auto.node("d1").params == {"text": "走廊有人，请播报欢迎词"}
    assert auto.node("d2").action == "notify"


def test_compiled_inbox_graph_passes_schema_and_static_scan():
    """schema 过 + 静态扫描无 ERROR。

    后半句是卡1 的真门槛：`inbox` 若留在"未知 domain ⇒ L2"的缺省档，扫描器会给每个
    inbox 节点报 `L2_NEEDS_CONFIRM`/`L2_NEEDS_CANARY`（ERROR），这条自动化连编译都过不去。
    """
    graph = compile_spec(_SPEC)
    raw = graph_to_raw(graph)[0]
    reloaded = load_graph(raw)
    scan = StaticScanner(reloaded).scan()

    assert scan.ok is True, [d.message for d in scan.diagnostics]
    assert not [d for d in scan.diagnostics if d.code.startswith("L2_")], scan.diagnostics


def test_undotted_action_on_an_unregistered_adapter_still_blocks_compile():
    """反例腿：`inbox` 判 L0 **不等于**"do 节点一律放行"。

    未登记的适配器 + 不带点的动作仍走"未知 domain ⇒ L2"的缺省档，扫描器照报
    `L2_NEEDS_CONFIRM`。少了这条，`classify_action` 里"动作名不带点时退回适配器名"
    那一步就可能是被整体调松（谁都能编译过），而不是只把收件箱接对。
    """
    data = _auto(nodes=[
        _auto()["nodes"][0],
        {"id": "d1", "kind": "do", "adapter": "sms", "action": "send", "params": {"to": "1"}},
        {"id": "p1", "kind": "pass"},
    ])
    scan = StaticScanner(Graph([load_automation(data)])).scan()

    assert "L2_NEEDS_CONFIRM" in {d.code for d in scan.diagnostics}, scan.diagnostics
    assert scan.ok is False


def test_dsl_roundtrip_keeps_inbox_nodes():
    """`compile_spec(render_spec(graph))` 往返：`do … inbox.notify {…}` 不会在渲染里退化成通用调用。"""
    graph = compile_spec(_SPEC)
    rebuilt = compile_spec(render_spec(graph))

    assert rebuilt.get("hall_butler").node("d1").adapter == "inbox"
    assert rebuilt.get("hall_butler").node("d2").params == {"title": "走廊来人", "body": "已连续触发 3 次"}


def test_cli_spec_compile_accepts_inbox_nodes(tmp_path):
    spec_path = tmp_path / "hall_butler.afspec"
    spec_path.write_text(_SPEC, encoding="utf-8")
    out_path = tmp_path / "hall_butler.json"

    result = CliRunner().invoke(app, ["spec", "compile", str(spec_path), "--out", str(out_path)])

    assert result.exit_code == 0, result.output
    assert out_path.exists()


# ── 管线 2：仿真 ───────────────────────────────────────────────────────
def test_simulate_runs_the_inbox_node_to_done_on_the_fake_track():
    """仿真轨跑通：do 节点成功 ⇒ 实例 done。

    失败不会在这里显形为"报错"，而是显形为 `state == "failed"`——适配器把投递失败如实
    带回执行链（不报成 done），所以这条腿是真的能红的。
    """
    out = simulate(_auto(), events=[{"entity_id": "binary_sensor.hall_motion", "state": "on"}])

    assert [i["state"] for i in out["instances"]] == ["done"], out["instances"]
    assert "请音箱播报" in out["nl"], out["nl"]


def test_inbox_adapter_is_registered_on_the_shared_runtime_builder():
    """`build_runtime` 是四面（CLI / HTTP / MCP / 仿真）唯一的适配器收口点。

    只往某一面注册 = 那张脸能播报、别的脸报"未注册的适配器"。这里锁注册本身与缺省 dry_run。
    """
    runtime = build_runtime(Graph([load_automation(_auto())]))
    adapter = runtime.adapters.get("inbox")

    assert "inbox" in runtime.adapters
    assert adapter.dry_run is True
    assert adapter.call("speak", {"text": "走廊有人"}).success is True
    assert adapter.intents[-1]["topic"] == "butler/inbox/speak"


@pytest.mark.parametrize("action", ["speak", "notify", "tv"])
def test_simulation_dry_run_writes_no_bytes_anywhere(action):
    """仿真档（缺省 dry_run）下三条动作都只留意图、不产生任何上线调用。"""
    params = {
        "speak": {"text": "走廊有人"},
        "notify": {"title": "水浸", "body": "厨房"},
        "tv": {"content": "门铃"},
    }[action]
    nodes = [
        _auto()["nodes"][0],
        {"id": "d1", "kind": "do", "adapter": "inbox", "action": action, "params": params},
        {"id": "p1", "kind": "pass"},
    ]
    out = simulate(
        _auto(nodes=nodes),
        events=[{"entity_id": "binary_sensor.hall_motion", "state": "on"}],
    )

    assert [i["state"] for i in out["instances"]] == ["done"], out["instances"]


def _one_do_graph(action: str, params: dict) -> Graph:
    """单条 `on → do inbox.<action> → pass` 的图，给 NL 渲染腿复用。"""
    return Graph([load_automation(_auto(nodes=[
        _auto()["nodes"][0],
        {"id": "d1", "kind": "do", "adapter": "inbox", "action": action, "params": params},
        {"id": "p1", "kind": "pass"},
    ]))])


# ── 管线 3：NL 渲染 ────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "action,params,expected",
    [
        ("speak", {"text": "走廊有人"}, "请音箱播报「走廊有人」"),
        ("notify", {"title": "水浸", "body": "厨房"}, "请手机通知「水浸」"),
        ("tv", {"content": "门铃响了"}, "请电视上屏「门铃响了」"),
    ],
)
def test_nl_renders_inbox_as_a_request_to_the_butler(action, params, expected):
    """中文渲染说的是"请 DB 表达什么"，不是"调用 inbox.speak（无目标实体）"。

    通用模板会把它渲染成故障语——用户在 mimoUI 看到的"这条自动化想干什么"就是错的，
    而收件箱节点天生没有 `entity_id`（它不操作某台设备）。
    """
    text = render_graph(_one_do_graph(action, params)).text

    assert expected in text, text
    assert "无目标实体" not in text, text


def test_nl_still_renders_unknown_inbox_kind_without_crashing():
    """库侧加了第四个动作、AF 的中文映射还没跟上时：渲染降级但不能整屏崩。"""
    text = render_graph(_one_do_graph("future_kind", {"text": "x"})).text

    assert "请投递收件箱" in text, text
