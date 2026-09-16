"""HA 域「状态契约 + 服务词汇」静态表（token-free，零依赖）。

**为什么需要它**：设备目录只存了实体某一刻的快照 `state`，没有「它可能处于哪些状态、
能调哪些服务」。Agent 写 IR 时只能猜——猜错就是运行期 422 / 分支遗漏。

本表把最常见的域契约硬编码，让 `af_catalog.resolve` / `af_catalog.list_entities`
直接附加 `possible_states` / `services`，Agent 写 `do` 节点与 `if` 分支时立刻有依据。

**与 IR 语义对齐**：
- `GLOBAL_STATES`（`unavailable` / `unknown`）——任何实体都可能离线或未知。
  IR §14-9 规定「实体不存在 → 走 `on_error` 软失效，不直接 failed」；
  本表把这层意思显式告诉 Agent：**写 `if` 分支时必须处理 `unavailable`，不可当成 `off` 的等价物**。
- `high_risk` 标记——与 `af_adapters.classify_action` 的 L2/L3 分级呼应（门锁/水阀等）。

> 表格是「通用契约基线」：**给的是该域共有什么**；实体特有的枚举
> （`select.options` / `climate.hvac_modes`）在实体 `attributes` 里，两者互补。
> 参考前身 autoflow `lib/affordance.py` 的设计，按 AutoForge 的 IR 语义重写。
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "GLOBAL_STATES",
    "DOMAIN_AFFORDANCE",
    "HIGH_RISK_DOMAINS",
    "affordance_for",
    "possible_states",
    "domain_of",
]

#: 任何实体都可能出现的状态（与域无关）——写 `if` 分支时必须处理，不可等同 `off`
GLOBAL_STATES: tuple[str, ...] = ("unavailable", "unknown")

#: 高危域（与 `af_adapters.classify_action` 的 L2/L3 分级呼应；写这些域需 canary + 人工确认）
HIGH_RISK_DOMAINS: frozenset[str] = frozenset(
    {"lock", "valve", "water_heater", "alarm_control_panel", "garage_door", "cover"}
)

#: 域 → 契约。`states` 为该域自有状态（不含 GLOBAL_STATES，由 `affordance_for` 合并）；
#: `services` 为可调服务（value 为参数说明，写 IR 时参考）；`note` 为坑位提示。
DOMAIN_AFFORDANCE: dict[str, dict[str, Any]] = {
    "light": {
        "states": ["on", "off"],
        "services": {
            "turn_on": {"brightness_pct": "0-100", "brightness": "0-255", "transition": "秒"},
            "turn_off": {"transition": "秒"},
            "toggle": {},
        },
        "note": "调光/变色参数取决于实体 capabilities(supported_color_modes)；不支持则 422。",
    },
    "switch": {
        "states": ["on", "off"],
        "services": {"turn_on": {}, "turn_off": {}, "toggle": {}},
        "note": "布尔开关。unavailable=离线，不可等同 off。",
    },
    "fan": {
        "states": ["on", "off"],
        "services": {
            "turn_on": {"percentage": "0-100"},
            "turn_off": {},
            "set_percentage": {"percentage": "0-100"},
            "set_preset_mode": {"preset_mode": "见实体 preset_modes"},
        },
        "note": "是否支持百分比/摆风看实体 capabilities。",
    },
    "climate": {
        "states": ["off", "heat", "cool", "auto", "dry", "fan_only"],
        "services": {
            "turn_on": {},
            "turn_off": {},
            "set_temperature": {"temperature": "°C"},
            "set_hvac_mode": {"hvac_mode": "取值见 states（去掉 unavailable/unknown）"},
            "set_fan_mode": {"fan_mode": "见实体 fan_modes"},
            "set_preset_mode": {"preset_mode": "见实体 preset_modes"},
        },
        "note": "支持的 hvac_modes/fan_modes/preset_modes 在实体 attributes；调用未列出的模式会 422。",
    },
    "cover": {
        "states": ["open", "closed", "opening", "closing", "stopped"],
        "services": {
            "open_cover": {},
            "close_cover": {},
            "stop_cover": {},
            "set_cover_position": {"position": "0-100（开合度）"},
        },
        "note": "部分卷帘支持 set_cover_tilt。属高危域，写操作需 canary + 人工确认。",
    },
    "lock": {
        "states": ["locked", "unlocked", "jammed"],
        "services": {"lock": {}, "unlock": {}, "open": {}},
        "note": "⚠️ 高危域：必须 L3 白名单 + 人工确认。jammed=卡死，不是 unlocked。",
    },
    "media_player": {
        "states": ["playing", "paused", "idle", "off", "standby", "buffering"],
        "services": {
            "turn_on": {},
            "turn_off": {},
            "media_play": {},
            "media_pause": {},
            "media_stop": {},
            "volume_set": {"volume_level": "0-1"},
        },
        "note": "部分实体支持 select_source 等；信号源枚举在实体 attributes（如 source_list）。",
    },
    "select": {
        "states": ["<option>"],
        "services": {"select_option": {"option": "必须 ∈ attributes.options，否则 422"}},
        "note": "⚠️ 选项枚举在实体 attributes.options——写 IR 最常踩的坑，调用不存在的选项会 422。",
    },
    "number": {
        "states": ["<数值>"],
        "services": {"set_value": {"value": "必须在 attributes.min/max 之间，否则 422"}},
        "note": "取值范围在实体 attributes.min/max。",
    },
    "input_boolean": {
        "states": ["on", "off"],
        "services": {"turn_on": {}, "turn_off": {}, "toggle": {}},
        "note": "纯软件开关（非物理设备）。",
    },
    "humidifier": {
        "states": ["on", "off"],
        "services": {"turn_on": {}, "turn_off": {}, "set_humidity": {"humidity": "0-100"}},
        "note": "目标湿度在 attributes.humidity。",
    },
    "vacuum": {
        "states": ["docked", "cleaning", "paused", "idle", "returning", "error"],
        "services": {"start": {}, "pause": {}, "stop": {}, "return_to_base": {}},
        "note": "error=故障态，不是 idle。",
    },
    "scene": {
        "states": ["<激活快照>"],
        "services": {"turn_on": {}},
        "note": "一次性激活快照，无持续状态；`if` 判断 scene 的 state 无意义。",
    },
    "script": {
        "states": ["on", "off"],
        "services": {"turn_on": {}, "turn_off": {}},
        "note": "运行中 state=on。调用后不等待执行完成（非阻塞）。",
    },
    "automation": {
        "states": ["on", "off"],
        "services": {"turn_on": {}, "turn_off": {}, "trigger": {}},
        "note": "仅启停/触发，不改逻辑。",
    },
    "sensor": {
        "states": ["<测量值>"],
        "services": {},
        "note": "只读。单位在 attributes.unit_of_measurement；unavailable=离线/无读数。",
    },
    "binary_sensor": {
        "states": ["on", "off"],
        "services": {},
        "note": "只读。on=触发（有人/开窗/移动），off=未触发。",
    },
}


def domain_of(entity_id: str) -> str:
    """`light.study_main` → `light`。无 `.` 时返回空串。"""
    return entity_id.split(".", 1)[0] if "." in entity_id else ""


def possible_states(domain: str) -> list[str]:
    """某域可能的状态（含全域隐含 `unavailable` / `unknown`）。未知域返回全域隐含状态。"""
    return affordance_for(domain).get("states", list(GLOBAL_STATES))


def affordance_for(domain: str) -> dict[str, Any]:
    """返回某域的契约（`states` 已并入 GLOBAL_STATES）。未知域返回 `{"states": GLOBAL_STATES, ...}`。"""
    spec = DOMAIN_AFFORDANCE.get(domain or "")
    base_states = list(spec.get("states", [])) if spec else []
    # 合并全域隐含状态（去重，保持顺序）
    states = list(base_states)
    for g in GLOBAL_STATES:
        if g not in states:
            states.append(g)
    if not spec:
        return {
            "states": states,
            "services": [],
            "note": f"未登记的域 {domain!r}：仅知全域隐含状态，服务词汇请查实体 attributes。",
            "high_risk": False,
        }
    return {
        "states": states,
        "services": sorted(spec.get("services", {}).keys()),
        "service_params": dict(spec.get("services", {})),
        "note": spec.get("note", ""),
        "high_risk": domain in HIGH_RISK_DOMAINS,
    }
