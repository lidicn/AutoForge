"""书房吊灯 ↔ 书房射灯 开关状态同步 · FakeHA 验收。

真实 entity_id（用户户型）：
- 吊灯 `switch.lumi_cn_lumi_158d000239c546_aq1_on_p_3_1`
- 书房射灯 `switch.lumi_cn_lumi_158d000239c546_aq1_on_p_2_1`

AF 静态检查**禁止「写自己触发的实体」**（`ENTITY_DEP_CYCLE`，见 af_scanner.py:549），
故双向同步必须用 **emit 事件解耦**：状态变化侧只广播事件，跟随侧只写对方实体；
`if` 读对方快照做防回环（已一致则不下发）。
"""

from __future__ import annotations

CEILING = "switch.lumi_cn_lumi_158d000239c546_aq1_on_p_3_1"
LAMP = "switch.lumi_cn_lumi_158d000239c546_aq1_on_p_2_1"


def test_lamp_sync_bidirectional(make_harness):
    """任一开关变化 → 对方跟随（双向）。"""
    h = make_harness("case_lamp_sync.json", {CEILING: "off", LAMP: "off"})

    # 每次推进 1s：总线对同一实体有 200ms 节流窗口，虚拟时钟不推进时永不失效
    h.fire(CEILING, "on", advance_s=1)  # 吊灯开
    assert h.get(LAMP) == "on", "吊灯开 → 挂灯应跟随开"

    h.fire(LAMP, "off", advance_s=1)  # 挂灯关
    assert h.get(CEILING) == "off", "挂灯关 → 吊灯应跟随关"

    h.fire(LAMP, "on", advance_s=1)  # 挂灯开
    assert h.get(CEILING) == "on", "挂灯开 → 吊灯应跟随开"

    h.fire(CEILING, "off", advance_s=1)  # 吊灯关
    assert h.get(LAMP) == "off", "吊灯关 → 挂灯应跟随关"


def test_lamp_sync_no_pingpong(make_harness):
    """防回环：跟随侧在「已一致」时不重复下发。"""
    h = make_harness("case_lamp_sync.json", {CEILING: "off", LAMP: "off"})
    h.fire(CEILING, "on", advance_s=1)

    actions = [a for a, _ in h.ha_calls]
    assert actions.count("switch.turn_on") == 1, "射灯只应被下发一次"
    assert actions.count("switch.turn_off") == 0, "吊灯已 on，不应被回灌重开"
