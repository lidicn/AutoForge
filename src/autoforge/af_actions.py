"""af_actions — 动作域单一真源（F15 词表真值源）

这张表是所有合法 `(domain, service)` 对的**权威真源**。
三个下游模块各自有自己的服务层表（语义不同，不直接合并）：

  - ``af_vhass.fake.SERVICE_STATE``   → 仿真层：(domain, service) → 新状态（domain 感知）
  - ``af_shadow.DEFAULT_EFFECTS``      → 验证层：service → 期望态（domain 无关简化）
  - ``af_nl._ACTION_VERBS``            → 渲染层："domain.service" → 中文动词

做法：三个下游表**各自保持独立**，但 ``KNOWN_ACTIONS`` 是它们的**超集锚点**。
任何新增 domain.service 对必须先入 ``KNOWN_ACTIONS``，再同步三个下游表或声明 EXEMPT。
``tests/unit/test_f15_word_table_alignment.py`` 锁死跨表覆盖率不漂移。
"""
from __future__ import annotations

# ── v2.1 F15: 已知 domain.service 对集合（单一真源）───────────────────────────
# 按 domain 分组，便于遍历、扩展、或接入外部 HA services API 审计。
# 格式统一为 frozenset[tuple[str, str]]，与 SERVICE_STATE/DYNAMIC_SERVICES 对齐。

_KNOWN_DOMAIN_SERVICES: dict[str, frozenset[str]] = {
    # 灯/开关/风扇：经典 on/off 二元域
    "light":    frozenset({"turn_on", "turn_off", "toggle"}),
    "switch":   frozenset({"turn_on", "turn_off", "toggle"}),
    "fan":      frozenset({"turn_on", "turn_off", "toggle"}),
    "input_boolean": frozenset({"turn_on", "turn_off", "toggle"}),

    # 空调：复杂动态域
    "climate":  frozenset({
        "turn_on", "turn_off",
        "set_temperature", "set_hvac_mode",
        "set_fan_mode", "set_preset_mode",
    }),

    # 影音：播放控制 + 音量
    "media_player": frozenset({
        "turn_on", "turn_off",
        "media_pause", "media_play", "media_stop", "play_media",
        "volume_set", "volume_mute",
    }),

    # 门锁：二元锁定
    "lock":     frozenset({"lock", "unlock"}),

    # 遮阳帘：多态
    "cover":    frozenset({"open_cover", "close_cover", "stop_cover"}),

    # F1 P1：场景/脚本间接触发域
    "scene":    frozenset({"turn_on"}),
    "script":   frozenset({"turn_on", "turn_off"}),

    # F1 P1：副作用不可观测域（发手机/推送，shadow 走 EXEMPT verdict）
    "notify":                  frozenset({"notify"}),
    "persistent_notification": frozenset({"create"}),

    # F1 P2/P3：高频设备域（扫地机/阀门/热水器）
    "vacuum":        frozenset({"start", "pause", "stop", "return_to_base", "locate"}),
    "valve":         frozenset({"open_valve", "close_valve"}),
    "water_heater":  frozenset({"turn_on", "turn_off", "set_temperature"}),

    # F1 P4：冷门域补全（加湿器/报警面板/自动化/cover 位置）
    "humidifier": frozenset({"turn_on", "turn_off", "set_humidity"}),
    "alarm_control_panel": frozenset({
        "alarm_arm_away", "alarm_arm_home", "alarm_arm_night",
        "alarm_disarm", "alarm_trigger",
    }),
    "automation": frozenset({"turn_on", "turn_off", "trigger"}),
    "group":      frozenset({"turn_on", "turn_off"}),
    "cover":      frozenset({"open_cover", "close_cover", "stop_cover", "set_cover_position"}),
}


#: 单一真源展开：所有合法 (domain, service) 对。
KNOWN_ACTIONS: frozenset[tuple[str, str]] = frozenset(
    (domain, svc)
    for domain, svcs in _KNOWN_DOMAIN_SERVICES.items()
    for svc in svcs
)


def known_actions() -> frozenset[tuple[str, str]]:
    """返回完整的已知 (domain, service) 对集合（只读快照）。"""
    return KNOWN_ACTIONS


def is_known(domain: str, service: str) -> bool:
    """某 (domain, service) 是否在真源表中登记。"""
    return (domain, service) in KNOWN_ACTIONS


def known_services_for(domain: str) -> frozenset[str]:
    """某 domain 下所有已知 service（空集表示未知 domain）。"""
    return _KNOWN_DOMAIN_SERVICES.get(domain, frozenset())


# ── EXEMPT_ACTIONS（副作用不可观测、shadow 不做状态验证）─────────────────────
# 这些域在 shadow 层走 EXEMPT verdict（人审），但仍属于 KNOWN_ACTIONS
#  （仿真能跑通、is_modeled 返回 True）。
# 下游模块引用这份集合，避免把 notify 类塞进 DEFAULT_EFFECTS 导致语义冲突。

EXEMPT_DOMAINS: frozenset[str] = frozenset({
    "notify",
    "persistent_notification",
})
