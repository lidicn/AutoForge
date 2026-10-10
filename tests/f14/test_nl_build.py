"""F14 P2 — NL→IR 正向编译器验收（受限文法，零 LLM）。

每条样本双重校验（P2 强绑 P1）：
1. `af_ir.load_automation(ir)` 不抛异常 → IR 结构合法（schema 通过）；
2. `af_fidelity.verify_roundtrip(ir).ok` 为 True → IR→NL→IR 往返保真。
文法之外的输入必须抛 ValueError（绝不猜测、绝不产出非法 IR）。
"""
from __future__ import annotations

import pytest

from autoforge import af_fidelity
from autoforge.af_fidelity import verify_roundtrip
from autoforge.af_ir import Automation, load_automation
from autoforge.af_nl import render_automation
from autoforge.af_nl_build import build_ir_from_nl

# ── 30 条样本：覆盖 state/time/sun 三类触发 + 条件（单/且/或）+ ≥16 域动作消歧 ──
SAMPLES = [
    # 状态触发 + 直接动作（打开→light.turn_on，由实体域消歧）
    "当 binary_sensor.presence 变为 on 时，打开 light.living",
    # 状态触发（变成…时 形态）+ 关闭→switch.turn_off
    "binary_sensor.door 变成 off 时，关闭 switch.kettle",
    # 定时触发 每天 HH:MM + 触发场景→scene.turn_on
    "每天 07:30，触发场景 scene.morning",
    # 定时触发 每天 HH点MM分 + 运行脚本→script.turn_on
    "每天 8点15分，运行脚本 script.wake",
    # 定时触发 HH:MM 时 + 关闭→fan.turn_off
    "20:00 时，关闭 fan.ceiling",
    # 太阳触发 日落时
    "日落时，打开 light.yard",
    # 太阳触发 日出时 + 关闭→light.turn_off
    "日出时，关闭 light.yard",
    # 非 on/off 状态 → eq 常量比较；动作切换→fan.toggle
    "当 sensor.co2 变为 high 时，打开 fan.ceiling",
    # 单条件（如果 X 为 off → is_off）
    "当 binary_sensor.motion 变为 on 时，如果 light.hall 为 off，打开 light.hall",
    # 条件用 且 连接（两个 is_off）
    "当 binary_sensor.presence 变为 on 时，如果 climate.bed 为 off 且 fan.ceiling 为 off，打开 light.living",
    # 条件用 或 连接（两个 is_on）
    "当 sensor.lux 变为 low 时，如果 binary_sensor.night 为 on 或 media_player.tv 为 on，打开 light.hall",
    # 裸动词唯一映射到 EXEMPT_DOMAINS（notify），可省略实体
    "日落时，发送通知",
    # 冷门域：上锁→lock.lock；状态 unlocked → eq
    "当 lock.front 变为 unlocked 时，上锁 lock.front",
    # 把空调设为→climate.set_temperature（域消歧）
    "当 sensor.temp 变为 hot 时，把空调设为 climate.bed",
    # ── 补到 30 条：扩域覆盖（toggle/多态 cover/影音/扫地/阀门/加湿/热水器/报警面板）──
    "日落时，切换 light.porch",                                        # light.toggle
    "当 input_boolean.mode 变为 on 时，切换 input_boolean.mode",       # input_boolean.toggle
    "每天 06:00，打开空调 climate.master",                             # climate.turn_on
    "每天 23:00，关闭空调 climate.master",                             # climate.turn_off
    "当 cover.attic 变为 opening 时，打开 cover.attic",               # cover.open_cover
    "每天 08:00，关闭 cover.attic",                                    # cover.close_cover
    "当 sensor.wind 变为 strong 时，停止 cover.attic",                 # cover.stop_cover
    "当 lock.garage 变为 locked 时，解锁 lock.garage",                 # lock.unlock
    "每天 19:00，打开 media_player.tv",                                # media_player.turn_on
    "当 media_player.tv 变为 playing 时，暂停 media_player.tv",        # media_player.media_pause
    "每天 21:00，把音量调到 media_player.tv",                          # media_player.volume_set
    "每天 10:00，开始清扫 vacuum.down",                                # vacuum.start
    "当 sensor.soil 变为 dry 时，打开阀门 valve.garden",               # valve.open_valve
    "每天 07:00，把湿度设为 humidifier.nursery",                       # humidifier.set_humidity
    "每天 06:30，打开热水器 water_heater.util",                        # water_heater.turn_on
    "当 alarm_control_panel.home 变为 armed_away 时，撤防 alarm_control_panel.home",  # alarm_disarm
]

# 设计 §4 范例的**规范结构**（触发 → 条件 → 动作）；文档理想文本 "晚上8点后如果有人在家，打开客厅灯"
# 需时窗/房间名→实体的语义推断，超出零-LLM 受限域 → 用规范化 entity_id 书写同一结构。
DESIGN_EXAMPLE = (
    "当 binary_sensor.presence 变为 home 时，如果 binary_sensor.presence 为 home，打开 light.living"
)


@pytest.mark.parametrize("text", SAMPLES + [DESIGN_EXAMPLE])
def test_build_is_valid_and_roundtrip_faithful(text):
    ir = build_ir_from_nl(text)
    load_automation(ir)  # schema 不通过会抛 IRValidationError
    report = verify_roundtrip(ir)
    assert report.ok, f"{text!r} 往返保真失败：{report.detail}"


def test_trigger_kinds_covered():
    kinds = set()
    for text in SAMPLES + [DESIGN_EXAMPLE]:
        ir = build_ir_from_nl(text)
        kinds.add(ir["nodes"][0]["trigger"]["type"])
    assert kinds >= {"state", "time", "sun"}


def test_thirty_samples_broad_domain_coverage():
    """P2 验收门槛：≥30 条 NL 样本，且覆盖 ≥16 个不同设备域的 domain.service。"""
    assert len(SAMPLES) >= 30, f"样本数不足 30：{len(SAMPLES)}"
    domains = set()
    for text in SAMPLES:
        for n in build_ir_from_nl(text)["nodes"]:
            if n["kind"] == "do":
                domains.add(n["action"].partition(".")[0])
    assert len(domains) >= 16, f"域覆盖过窄（{len(domains)}）：{sorted(domains)}"


def test_condition_with_and_builds_if_node():
    ir = build_ir_from_nl(
        "当 binary_sensor.presence 变为 on 时，如果 climate.bed 为 off 且 fan.ceiling 为 off，打开 light.living"
    )
    if_nodes = [n for n in ir["nodes"] if n["kind"] == "if"]
    assert len(if_nodes) == 1
    expr = if_nodes[0]["expr"]
    assert expr["op"] == "and" and len(expr["args"]) == 2
    # 且 → 两个 is_off 子句
    assert {a["op"] for a in expr["args"]} == {"is_off"}


def test_action_domain_disambiguation():
    # 同一动词「打开」按实体域消歧到不同 domain.service
    assert build_ir_from_nl("日落时，打开 light.yard")["nodes"][1]["action"] == "light.turn_on"
    assert build_ir_from_nl("日落时，打开 fan.yard")["nodes"][1]["action"] == "fan.turn_on"
    assert build_ir_from_nl("日落时，打开 switch.yard")["nodes"][1]["action"] == "switch.turn_on"


def test_ir_shape_connected_then_edges():
    ir = build_ir_from_nl(DESIGN_EXAMPLE)
    ids = [n["id"] for n in ir["nodes"]]
    assert ids == ["n_on", "n_if", "n_do", "n_pass"]
    edges = {(e["from"], e["to"]) for e in ir["edges"]}
    assert {("n_on", "n_if"), ("n_if", "n_do"), ("n_do", "n_pass")} <= edges
    assert all(e["kind"] == "then" for e in ir["edges"])
    # 全节点从 on 入口可达（渲染无覆盖率缺失）
    assert not render_automation(Automation.from_dict(ir)).missing


@pytest.mark.parametrize("text", SAMPLES + [DESIGN_EXAMPLE])
def test_property_build_output_passes_verify_roundtrip(text):
    """P2 强绑 P1：任何成功构建的 IR 必然通过 verify_roundtrip。"""
    assert verify_roundtrip(build_ir_from_nl(text)).ok is True


# ── 负例：文法之外 → ValueError（不猜测、不产出非法 IR）──────────────────────
@pytest.mark.parametrize("text", [
    "",
    "今天天气不错",
    "打开 light.living",  # 缺触发片段
    "当 binary_sensor.x 变为 on 时，随便说点什么",  # 动作非已知动词
    "当 a.b 变为 on 时，打开 unknowndomain.x",  # 动词与该域无已知组合
    "当 a.b 变为 on 时，如果 c.d 为 on 且 e.f 为 off 或 g.h 为 on，打开 light.z",  # 且/或 混用
    "当 a.b 变为 on 时，打开 light.z 然后再说",  # 多余尾随文本
])
def test_unsupported_raises_valueerror(text):
    with pytest.raises(ValueError):
        build_ir_from_nl(text)


# ── 散文半边：写在源码里的能力声明不许比代码旧 ────────────────────
def test_p1_scope_note_does_not_claim_the_p2_parser_is_missing():
    """`af_fidelity` 的模块 docstring 是给读者看的"能力地图"，它长期写着 P2 `build_ir_from_nl`
    未实现，而解析器其实早已在树里（本文件就是在测它）。这类声明不会随代码自己更新，所以钉一条：
    docstring 里凡点到这个名字的行都不许带「未实现」，且**必须至少有一行点到它**——否则这条腿
    会因为"空集给的干净"而静默失效。"""
    lines = [ln for ln in (af_fidelity.__doc__ or "").splitlines() if "build_ir_from_nl" in ln]
    assert lines, "af_fidelity 的 P1 范围约束段应当点到 P2 解析器的名字"
    assert all("未实现" not in ln for ln in lines)
    assert callable(build_ir_from_nl)
