"""F14 P1 —— 中文自然语言 → AutoForge IR 解析器（`af_nl` 的逆）。

目标（F14 §2.1）：把 af_nl 渲染出的中文自动化描述解析回合规 IR，与 af_nl
组成「IR → NL → IR」往返稳定闭环。

硬约束（F14 §2.2，本模块逐条守住）：
- **不改** `af_ir/schema/ir.schema.json` / `af_ir/models.py`：只产出 dict，
  字段名逐字取自 schema 的 node / trigger / edge / expr / ask_spec 词表。
- **绝不写运行时字段**：`stage` / `diff_sha` / `simulate_track` / `honest_report`
  一个都不落盘；NL 里的 `[运行时]` 占位只还原 `_non_reversible: true`，
  不假装能还原其值（af_irreversible.NL_RUNTIME_PLACEHOLDER）。
- **零新依赖**：标准库 + 既有 `af_ir` / `af_irreversible` / `af_time`。

解析策略（关键设计决策见 §7）：
1. 行优先切段（支持 `；` 与「行中边前缀」二次切分），逐段归类成语句。
2. **结构归位**（语句 -> 节点）双规则：
   a. 树规则：语句 indent 严格大于上一条 -> 父节点就是上一条；
   b. 栈规则：弹掉 indent >= 当前 indent 的分支块，再自顶向下找
      「能挂这条边、且还没挂过同 kind 边」的最近节点。
   两条规则对 af_nl 的缩进输出与纯平输出都能归位；纯平嵌套歧义处
   按「同缩进 = 同父」裁定（见 §7-D4）。
3. **fail-closed**：识别不出的语句抛 `ParseError`；产出后再过
   `af_ir.validate_automation`，零错误才返回。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .af_ir import GROUP_IR_VERSION, IR_VERSION, validate_automation
from .af_irreversible import (
    NL_RUNTIME_PLACEHOLDER,
    NON_REVERSIBLE_KEY,
    RUNTIME_ONLY_FIELDS,
)

__all__ = [
    "ParseError",
    "parse_automation",
    "parse_graph",
    "duration_seconds",
    "EDGE_PREFIX_TEXT",
]


class ParseError(ValueError):
    """NL 文本无法解析成合规 IR（fail-closed：宁可拒收，不猜）。"""


# ---------------------------------------------------------------------------
# §0 / §1 可见词表：能从 af_nl 取就取，取不到用 §1 内联快照兜底。
# 这样只要 af_nl 换了动词表，逆映射自动跟着走，不会两处漂移。
# ---------------------------------------------------------------------------
try:  # pragma: no cover - 取决于 af_nl 是否导出该私有表
    from .af_nl import _ACTION_VERBS as _SHARED_ACTION_VERBS
except Exception:  # pragma: no cover
    _SHARED_ACTION_VERBS = {}

try:  # pragma: no cover
    from .af_nl import _CMP_SYMBOL as _SHARED_CMP_SYMBOL
except Exception:  # pragma: no cover
    _SHARED_CMP_SYMBOL = {}

try:  # pragma: no cover
    from .af_nl import _SUN_TEXT as _SHARED_SUN_TEXT
except Exception:  # pragma: no cover
    _SHARED_SUN_TEXT = {}


_FALLBACK_ACTION_VERBS: dict[str, str] = {
    "light.turn_on": "打开", "light.turn_off": "关闭", "light.toggle": "切换",
    "switch.turn_on": "打开", "switch.turn_off": "关闭", "switch.toggle": "切换",
    "fan.turn_on": "打开", "fan.turn_off": "关闭", "fan.toggle": "切换",
    "input_boolean.turn_on": "打开", "input_boolean.turn_off": "关闭",
    "input_boolean.toggle": "切换",
    "climate.turn_on": "打开空调", "climate.turn_off": "关闭空调",
    "climate.set_temperature": "把空调设为",
    "climate.set_hvac_mode": "把空调模式设为",
    "climate.set_fan_mode": "把风扇模式设为",
    "climate.set_preset_mode": "把空调预设设为",
    "cover.open_cover": "打开", "cover.close_cover": "关闭",
    "cover.stop_cover": "停止", "cover.set_cover_position": "把遮阳帘位置设为",
    "lock.lock": "上锁", "lock.unlock": "解锁",
    "media_player.turn_on": "打开", "media_player.turn_off": "关闭",
    "media_player.media_pause": "暂停", "media_player.media_play": "播放",
    "media_player.media_stop": "停止", "media_player.play_media": "播放媒体",
    "media_player.volume_set": "把音量调到", "media_player.volume_mute": "静音",
    "scene.turn_on": "触发场景",
    "script.turn_on": "运行脚本", "script.turn_off": "停止脚本",
    "notify.notify": "发送通知",
    "persistent_notification.create": "弹一条通知",
    "vacuum.start": "开始清扫", "vacuum.pause": "暂停清扫",
    "vacuum.stop": "停止清扫", "vacuum.return_to_base": "回基站",
    "vacuum.locate": "定位扫地机",
    "valve.open_valve": "打开阀门", "valve.close_valve": "关闭阀门",
    "water_heater.turn_on": "打开热水器", "water_heater.turn_off": "关闭热水器",
    "water_heater.set_temperature": "把热水器温度设为",
    "humidifier.turn_on": "打开加湿器", "humidifier.turn_off": "关闭加湿器",
    "humidifier.set_humidity": "把湿度设为",
    "alarm_control_panel.alarm_arm_away": "布防（离家）",
    "alarm_control_panel.alarm_arm_home": "布防（在家）",
    "alarm_control_panel.alarm_arm_night": "夜间布防",
    "alarm_control_panel.alarm_disarm": "撤防",
    "alarm_control_panel.alarm_trigger": "触发报警",
    "automation.turn_on": "启用自动化", "automation.turn_off": "禁用自动化",
    "automation.trigger": "手动触发",
    "group.turn_on": "打开组", "group.turn_off": "关闭组",
}

ACTION_VERBS: dict[str, str] = dict(_SHARED_ACTION_VERBS or _FALLBACK_ACTION_VERBS)

_CMP_SYMBOL: dict[str, str] = dict(_SHARED_CMP_SYMBOL or {
    "eq": "等于", "ne": "不等于", "lt": "低于",
    "lte": "不高于", "gt": "高于", "gte": "不低于",
})
_SUN_TEXT: dict[str, str] = dict(_SHARED_SUN_TEXT or {"sunset": "日落", "sunrise": "日出"})

# 动词 -> action 的反查：(动词, 实体 domain) -> action。
# §0 明确「打开 light.x -> light.turn_on；switch.x -> switch.turn_off」这种
# 「动词 + 目标实体 domain 前缀」的多对一反解，所以反查键必须带 domain。
_REVERSE_ACTION: dict[tuple[str, str], str] = {}
# 动词在整张表里唯一时，允许无实体直接落动作（notify / scene / script / alarm ...）
_VERB_ONLY_ACTION: dict[str, str] = {}
_by_verb: dict[str, set[str]] = {}
for _action, _verb in ACTION_VERBS.items():
    _domain = _action.split(".", 1)[0]
    _REVERSE_ACTION[(_verb, _domain)] = _action
    _by_verb.setdefault(_verb, set()).add(_action)
for _verb, _acts in _by_verb.items():
    if len(_acts) == 1:
        _VERB_ONLY_ACTION[_verb] = next(iter(_acts))

_VERB_RE = re.compile(
    "|".join(re.escape(v) for v in sorted(set(ACTION_VERBS.values()), key=len, reverse=True))
) if ACTION_VERBS else re.compile(r"(?!)")

# 参数键表：af_nl 只印值，键名由「动作」推回。数值型参数先抓数字，
# 字符串型参数先抓「」。这是 §0「多对一动词」的必然代价，见 §7-D5。
_PARAM_KEYS: dict[str, tuple[str, ...]] = {
    "climate.set_temperature": ("temperature",),
    "water_heater.set_temperature": ("temperature",),
    "humidifier.set_humidity": ("humidity",),
    "media_player.volume_set": ("volume_level",),
    "cover.set_cover_position": ("position",),
    "climate.set_hvac_mode": ("hvac_mode",),
    "climate.set_fan_mode": ("fan_mode",),
    "climate.set_preset_mode": ("preset_mode",),
    "notify.notify": ("message",),
    "persistent_notification.create": ("message",),
}
_NUMERIC_PARAMS = {"temperature", "humidity", "volume_level", "position", "brightness"}

# ---------------------------------------------------------------------------
# 边前缀方言（§0 逐字）：必须按长度降序匹配，否则「如果」会抢掉「如果你同意」
# ---------------------------------------------------------------------------
EDGE_PREFIX_TEXT: dict[str, str] = {
    "then": "然后：",
    "yes": "如果你同意：",
    "no": "如果你拒绝：",
    "default": "如果没听清你的回答：",
    "on_timeout": "如果超时：",
    "on_cancel": "如果中途被打断：",
    "on_error": "如果执行失败：",
}
_EDGE_PREFIXES: tuple[tuple[str, str], ...] = tuple(
    sorted(
        (
            ("如果没听清你的回答", "default"),
            ("如果中途被打断", "on_cancel"),
            ("如果执行失败", "on_error"),
            ("如果你同意", "yes"),
            ("如果你拒绝", "no"),
            ("如果超时", "on_timeout"),
            ("然后", "then"),
        ),
        key=lambda kv: len(kv[0]),
        reverse=True,
    )
)
_SEG_SPLIT_RE = re.compile(
    r"[，,；;]\s*(?=(?:如果没听清你的回答|如果中途被打断|如果执行失败|"
    r"如果你同意|如果你拒绝|如果超时|然后)\s*[：:])"
)

# 每种节点 kind 允许挂的出边（同节点同优先级边不可重复 —— DCD 锁定）
_ALL_KINDS = frozenset({"on", "if", "do", "ask", "wait", "set", "pass", "group"})
_ADMITS: dict[str, frozenset[str]] = {
    "then": _ALL_KINDS,
    "yes": frozenset({"if", "ask"}),
    "no": frozenset({"if", "ask"}),
    "default": frozenset({"ask", "wait"}),
    "on_timeout": frozenset({"ask", "wait"}),
    "on_cancel": frozenset({"ask", "wait"}),
    "on_error": _ALL_KINDS - {"on"},
}

# ---------------------------------------------------------------------------
# 时长 / 时间归一（比较用秒，落盘用原文 —— 见 §7-D6）
# ---------------------------------------------------------------------------
_UNIT_SECONDS: dict[str, float] = {
    "秒": 1.0, "s": 1.0, "sec": 1.0, "secs": 1.0, "second": 1.0, "seconds": 1.0,
    "分": 60.0, "分钟": 60.0, "m": 60.0, "min": 60.0, "mins": 60.0,
    "minute": 60.0, "minutes": 60.0,
    "小时": 3600.0, "时": 3600.0, "h": 3600.0, "hr": 3600.0, "hrs": 3600.0,
    "hour": 3600.0, "hours": 3600.0,
    "天": 86400.0, "日": 86400.0, "d": 86400.0, "day": 86400.0, "days": 86400.0,
}


def duration_seconds(value: Any) -> float | None:
    """把任意时长表示归一成秒；认不出来返回 None（调用方自行 fail-closed）。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    sign = 1.0
    if s[0] in "+-":
        sign = -1.0 if s[0] == "-" else 1.0
        s = s[1:].strip()
    # HH:MM:SS / MM:SS（两段按「分:秒」读，与时长直觉一致）
    m = re.fullmatch(r"(\d+):(\d{1,2})(?::(\d{1,2}(?:\.\d+)?))?", s)
    if m:
        if m.group(3) is None:
            total = int(m.group(1)) * 60.0 + float(m.group(2))
        else:
            total = int(m.group(1)) * 3600.0 + int(m.group(2)) * 60.0 + float(m.group(3))
        return sign * total
    total = 0.0
    hit = False
    for num, unit in re.findall(r"(\d+(?:\.\d+)?)\s*([A-Za-z\u4e00-\u9fff]+)", s):
        if unit in _UNIT_SECONDS:
            total += float(num) * _UNIT_SECONDS[unit]
            hit = True
    if hit:
        return sign * total
    if re.fullmatch(r"\d+(?:\.\d+)?", s):
        return sign * float(s)
    try:
        from .af_time import parse_duration

        return sign * float(parse_duration(s))
    except Exception:
        return None


def _dur_text(seconds: float) -> str:
    try:
        from .af_time import format_duration

        return format_duration(seconds)
    except Exception:
        return f"{seconds:g}秒"


def _norm_at(value: Any) -> str | None:
    if value is None:
        return None
    m = re.fullmatch(r"\s*(\d{1,2})\s*[:：]\s*(\d{1,2})\s*", str(value))
    if not m:
        return str(value)
    return f"{int(m.group(1)):02d}:{int(m.group(2)):02d}"


# ---------------------------------------------------------------------------
# 文本预处理
# ---------------------------------------------------------------------------
_QUOTE_SUBS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\u201c([^\u201d]*)\u201d"), r"「\1」"),
    (re.compile(r"\u2018([^\u2019]*)\u2019"), r"「\1」"),
    (re.compile(r'"([^"]*)"'), r"「\1」"),
)
_BULLET_RE = re.compile(r"^(?:[-*·•>+]|—|–|\d+\s*[.、)）])\s*")
_EID = r"[a-z][a-z0-9_]*\.[a-z0-9_]+"
_ENTITY_RE = re.compile(rf"(?<![A-Za-z0-9_.])({_EID})(?![A-Za-z0-9_.])")
_ACTION_REF_RE = re.compile(
    rf"(?:动作|action)\s*[：:=]?\s*「?(?P<action>[a-z][a-z0-9_]*\.[a-z_][a-z0-9_]*)」?"
)


def _normalize(body: str) -> str:
    s = body
    for pat, rep in _QUOTE_SUBS:
        s = pat.sub(rep, s)
    s = s.replace("：", "：").replace(" :", ":")
    return s.strip()


def _indent_of(line: str) -> tuple[int, str]:
    n = 0
    for ch in line:
        if ch == " ":
            n += 1
        elif ch == "\t":
            n += 4
        elif ch == "\u3000":
            n += 2
        else:
            break
    return n, line[n:]


def _split_segments(body: str) -> list[str]:
    out: list[str] = []
    for chunk in re.split(r"[；;]", body):
        out.extend(_SEG_SPLIT_RE.split(chunk))
    return [s.strip() for s in out if s.strip()]


def _match_edge_prefix(text: str) -> tuple[str, str] | None:
    for prefix, kind in _EDGE_PREFIXES:
        if not text.startswith(prefix):
            continue
        rest = text[len(prefix):]
        m = re.match(r"^\s*[：:]\s*", rest)
        if m:
            return kind, rest[m.end():].strip()
        if not rest.strip():
            return kind, ""
        if rest[:1].isspace():
            return kind, rest.strip()
    return None


# ---------------------------------------------------------------------------
# 语句分类
# ---------------------------------------------------------------------------
_GROUP_RE = re.compile(
    r"^(?:编组|组合|群组|分组|子编排|group)\s*[（(]?\s*"
    r"(?P<mode>sequence|parallel|顺序|并行|同时|依次|串行)?\s*[）)]?\s*[：:]?\s*(?P<rest>.*)$"
)
_GROUP_RUN_RE = re.compile(r"^(?:同时|并行)\s*地?\s*(?:执行|进行|做|运行|跑)")
_GROUP_SEQ_RE = re.compile(r"^(?:按顺序|依次|顺序|串行)\s*地?\s*(?:执行|进行|做|运行|跑)")
_ASK_RE = re.compile(
    r"^(?:向\s*[\w\u4e00-\u9fff]{1,8}?\s*)?"
    r"(?:询问|提问|征询|发问|请教|问(?!题))"
    r"(?:用户|大家)?\s*(?:一下)?\s*[：:]?\s*(?P<rest>.+)$"
)
_WAIT_RE = re.compile(
    r"^(?:请|麻烦)?\s*(?:等待|等一下|稍等|延迟|延时|暂停|等个|等)\s*[：:]?\s*(?P<rest>.+)$"
)
_SET_RES: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"^(?:请)?\s*(?:设置|设定|赋值|更新|设)\s+"
        r"(?P<var>[A-Za-z_][A-Za-z0-9_]*)\s*(?:为|等于|成|至|到|=|:)\s*(?P<val>.+)$"
    ),
    re.compile(
        r"^(?:把|将)\s+(?P<var>[A-Za-z_][A-Za-z0-9_]*)\s*"
        r"(?:设为|设置为|置为|设成|赋为)\s*(?P<val>.+)$"
    ),
    re.compile(r"^(?P<var>[A-Za-z_][A-Za-z0-9_]*)\s*(?:设为|赋值为|:=|=)\s*(?P<val>.+)$"),
    re.compile(r"^(?:记下|记录|记)\s+(?P<var>[A-Za-z_][A-Za-z0-9_]*)\s*[=：:]\s*(?P<val>.+)$"),
)
_PASS_RE = re.compile(
    r"^(?:什么都不做|无操作|不做任何事|不做任何操作|跳过|直接通过|忽略|通过|结束|no[_ ]?op|pass)$"
)
_IF_RE = re.compile(
    r"^(?:如果|若|假如|假设|倘若|当条件|条件是|判断|检查)\s*[：:]?\s*(?P<expr>.+)$"
)
_EMIT_RE = re.compile(
    r"^(?:发布|广播|emit)\s*(?:一条)?\s*(?:事件)?\s*[：:]?\s*"
    r"(?:「(?P<q>[^」]*)」|(?P<b>[A-Za-z_][A-Za-z0-9_.]*))"
)
_CHILD_RE = re.compile(
    r"^(?:子自动化|子流程|子任务|子规则|child)\s*(?:[「『])?(?P<name>[^」』:：）)]*)"
)
_HEADER_RE = re.compile(r"^(?:自动化|规则|场景|automation)\s*(?P<rest>.*)$")
_TRIGGER_START_RE = re.compile(
    r"^(?:当|每当|一旦|每逢|每次|每天|每日|日出|日落|收到|监听|订阅|捕获|"
    r"在\s*\d{1,2}\s*[:：]\s*\d{2}|\d{1,2}\s*[:：]\s*\d{2}|\d{1,2}\s*点)"
)
_SUN_RE = re.compile(r"^日(?P<w>出|落)(?:时)?\s*(?:(?P<rel>后|前)\s*(?P<off>.+))?$")
_TIME_RE = re.compile(
    r"^(?:每天|每日|每)?\s*(?:在|于)?\s*(?P<h>\d{1,2})\s*[:：]\s*(?P<m>\d{2})\s*(?:时|整)?$"
)
_TIME_CN_RE = re.compile(
    r"^(?:每天|每日|每)?\s*(?:在|于)?\s*(?P<h>\d{1,2})\s*点\s*"
    r"(?:(?P<m>\d{1,2})\s*分?|半)?\s*(?:时|整)?$"
)
_EVENT_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(?:收到|监听|订阅|捕获)\s*(?:一条|一个)?\s*事件?\s*[：:]?\s*「(?P<ev>[^」]*)」"),
    re.compile(r"^(?:事件|event)\s*[：:]?\s*「(?P<ev>[^」]*)」"),
    re.compile(
        r"^(?:收到|监听|订阅|捕获)\s*(?:一条|一个)?\s*事件?\s*[：:]?\s*"
        r"(?P<ev>[A-Za-z_][A-Za-z0-9_.]*)$"
    ),
)
_STATE_RES: tuple[re.Pattern[str], ...] = (
    re.compile(
        rf"^(?P<eid>{_EID})\s*(?:的\s*(?:状态|值))?\s*从\s*「(?P<fr>[^」]*)」\s*"
        rf"(?:变更|变化|变)?\s*为\s*「(?P<to>[^」]*)」\s*$"
    ),
    re.compile(
        rf"^(?P<eid>{_EID})\s*(?:的\s*(?:状态|值))?\s*从\s*(?P<fr>\S+?)\s*"
        rf"(?:变更|变化|变)?\s*为\s*「(?P<to>[^」]*)」\s*$"
    ),
    re.compile(
        rf"^(?P<eid>{_EID})\s*(?:的\s*(?:状态|值))?\s*(?:变更|变化|变)?\s*"
        rf"为\s*「(?P<to>[^」]*)」\s*$"
    ),
    re.compile(
        rf"^(?P<eid>{_EID})\s*(?:的\s*(?:状态|值))?\s*(?:变更|变化|变)?\s*"
        rf"为\s*(?P<to>[A-Za-z0-9_.]+)\s*$"
    ),
    re.compile(rf"^(?P<eid>{_EID})\s*(?:的\s*(?:状态|值))?\s*从\s*「(?P<fr>[^」]*)」\s*$"),
    re.compile(
        rf"^(?P<eid>{_EID})\s*(?:的)?\s*(?:状态|值)?\s*(?:发生)?(?:任何)?\s*"
        rf"(?:变化|改变|变更)\s*$"
    ),
    re.compile(rf"^(?P<eid>{_EID})\s*$"),
)
_NOTE_STRIP_RE = re.compile(r"[（(]?\s*\[运行时\][^）)]*[）)]?")


def _parse_value(text: str) -> Any:
    s = text.strip().strip("。；;")
    if len(s) >= 2 and s.startswith("「") and s.endswith("」"):
        s = s[1:-1].strip()
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    if re.fullmatch(r"-?\d+\.\d+", s):
        return float(s)
    if s in ("true", "True", "真"):
        return True
    if s in ("false", "False", "假"):
        return False
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*(?:度|%|℃|级|档)?", s)
    if m:
        v = m.group(1)
        return float(v) if "." in v else int(v)
    return s


def _split_logic(text: str, seps: Sequence[str]) -> tuple[list[str], list[str]]:
    """按「且 / 或」在引号外切分；返回 (片段, 算子)。"""
    parts: list[str] = []
    ops: list[str] = []
    buf: list[str] = []
    depth = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "「":
            depth += 1
        elif ch == "」":
            depth = max(0, depth - 1)
        hit = None
        if depth == 0:
            for s in seps:
                if not text.startswith(s, i):
                    continue
                prev_ch = text[i - 1] if i > 0 else ""
                next_ch = text[i + len(s)] if i + len(s) < n else ""
                if s == "且" and prev_ch == "而":
                    continue
                if s == "或" and next_ch == "者":
                    continue
                hit = s
                break
        if hit is not None:
            parts.append("".join(buf))
            buf = []
            ops.append("and" if hit == "且" else "or")
            i += len(hit)
            continue
        buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return parts, ops


def _inside_quotes(text: str, pos: int) -> bool:
    return text[:pos].count("「") > text[:pos].count("」")


# ---------------------------------------------------------------------------
# 触发器
# ---------------------------------------------------------------------------
def _trigger_leaf(text: str) -> dict[str, Any] | None:
    t = text.strip().rstrip("。；;：：")
    if not t:
        return None
    m = _SUN_RE.match(t)
    if m:
        trig: dict[str, Any] = {
            "type": "sun",
            "event": "sunrise" if m.group("w") == "出" else "sunset",
        }
        off = (m.group("off") or "").strip()
        if off:
            secs = duration_seconds(off)
            if secs is None:
                return None
            trig["offset"] = ("-" if m.group("rel") == "前" else "+") + _dur_text(abs(secs))
        return trig
    m = _TIME_RE.match(t) or _TIME_CN_RE.match(t)
    if m:
        minute = m.groupdict().get("m")
        if minute is None:
            minute = "30" if "半" in t else "00"
        return {"type": "time", "at": f"{int(m.group('h')):02d}:{int(minute):02d}"}
    for pat in _EVENT_RES:
        m = pat.match(t)
        if m:
            return {"type": "event", "event": m.group("ev")}
    for pat in _STATE_RES:
        m = pat.match(t)
        if m:
            gd = m.groupdict()
            trig = {"type": "state", "entity_id": gd["eid"]}
            if gd.get("fr"):
                trig["from"] = gd["fr"].strip("「」") or None
            if gd.get("to"):
                trig["to"] = gd["to"].strip("「」") or None
            return trig
    return None


def _extract_for(t: str) -> tuple[str | None, str]:
    """从触发行抽取「持续 X」时长，并返回去掉该片段后的触发文本。

    支持三种形式：
      - roundtrip 后缀：，并且这个状态**持续保持 X**
      - 括号内：      （持续 X）
      - 行尾：        持续 X / 保持 X / for X
    """
    m = re.search(r"，并且这个状态\**\s*(?:持续保持|持续|保持|for)\s*\**\s*(?P<d>[^*]+?)\s*\**\s*[：:]?\s*$", t)
    if m:
        return m.group("d").strip(), t[: m.start()].strip()
    m = re.search(r"[（(]\s*(?:持续|保持|并保持|for)\s*(?P<d>[^）)]+)[）)]\s*$", t)
    if m:
        return m.group("d").strip(), t[: m.start()].strip()
    m = re.search(r"(?:持续保持|并保持|持续|保持|for)\s*\*{0,2}\s*(?P<d>[^*\n]+?)\s*\*{0,2}\s*[：:]?\s*$", t)
    if m:
        return m.group("d").strip(), t[: m.start()].strip()
    return None, t


def _parse_trigger(text: str) -> tuple[dict[str, Any], str | None] | None:
    t = text.strip().rstrip("。；;")
    for_text, t = _extract_for(t)
    t = t.rstrip("，,、")
    t = re.sub(r"^(?:当|每当|一旦|每逢|每次)\s*", "", t)
    t = re.sub(r"(?:的时候|之时|时|[：:])\s*$", "", t)
    if not t:
        return None
    parts, ops = _split_logic(t, ("且", "或"))
    if len(parts) > 1:
        leaves = [_trigger_leaf(p.strip()) for p in parts]
        if any(x is None for x in leaves):
            return None
        cast = [x for x in leaves if x is not None]
        if all(o == ops[0] for o in ops):
            trig = {"type": "group", "op": ops[0], "sources": cast}
        else:
            trig = cast[0]
            for op, leaf in zip(ops, cast[1:]):
                trig = {"type": "group", "op": op, "sources": [trig, leaf]}
        return trig, for_text
    leaf = _trigger_leaf(t)
    return (leaf, for_text) if leaf else None


# ---------------------------------------------------------------------------
# 表达式
# ---------------------------------------------------------------------------
_CMP_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("不等于", "ne"), ("不是", "ne"), ("≠", "ne"),
    ("不高于", "lte"), ("不超过", "lte"), ("不大于", "lte"), ("小于等于", "lte"), ("≤", "lte"),
    ("不低于", "gte"), ("不小于", "gte"), ("不小于", "gte"), ("大于等于", "gte"), ("≥", "gte"),
    ("等于", "eq"), ("＝", "eq"), ("==", "eq"),
    ("低于", "lt"), ("小于", "lt"), ("＜", "lt"), ("<", "lt"),
    ("高于", "gt"), ("大于", "gt"), ("＞", "gt"), (">", "gt"),
    ("是", "eq"), ("为", "eq"),
)
_UNARY_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^(?P<val>.+?)\s*(?:并非|不是)?\s*(?:为开|是开的|是开启状态|开着|已开启|处于开启状态)$"), "is_on"),
    (re.compile(r"^(?P<val>.+?)\s*(?:并非|不是)?\s*(?:为关|是关的|是关闭状态|关着|已关闭|处于关闭状态)$"), "is_off"),
    (re.compile(r"^(?P<val>.+?)\s*(?:是)?\s*(?:在家|已回家)$"), "is_home"),
    (re.compile(r"^(?P<val>.+?)\s*(?:是)?\s*(?:不在家|离家|已离家)$"), "is_not_home"),
    (re.compile(r"^(?P<val>.+?)\s*(?:是)?\s*(?:为真|是真的|成立)$"), "truthy"),
    (re.compile(r"^(?P<val>.+?)\s*(?:并非|不是)\s*(?:为开|开的|开启)$"), "not_is_on"),
    (re.compile(r"^(?P<val>.+?)\s*(?:并非|不是)\s*(?:为关|关的|关闭)$"), "not_is_off"),
)
_OPERAND_RE = re.compile(
    r"^(?:「(?P<q>[^」]*)」|(?P<num>-?\d+(?:\.\d+)?)|"
    r"(?P<bool>true|false|True|False|真|假)|"
    r"(?P<var>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*))$"
)


def _parse_operand(s: str) -> dict[str, Any] | None:
    s = s.strip().rstrip("。；;")
    if not s:
        return None
    if len(s) >= 2 and s.startswith("「") and s.endswith("」"):
        return {"const": s[1:-1]}
    if re.fullmatch(r"-?\d+", s):
        return {"const": int(s)}
    if re.fullmatch(r"-?\d+\.\d+", s):
        return {"const": float(s)}
    if s in ("true", "True", "真"):
        return {"const": True}
    if s in ("false", "False", "假"):
        return {"const": False}
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*", s):
        return {"var": s}
    return None


def _infer_types(left: dict[str, Any], right: dict[str, Any]) -> None:
    """数值比较必须显式 typed（expr.py 红线），按对侧 const 推断。"""
    for a, b in ((left, right), (right, left)):
        if "var" in a and "const" in b and "type" not in a:
            v = b["const"]
            if isinstance(v, bool):
                a["type"] = "boolean"
            elif isinstance(v, (int, float)):
                a["type"] = "numeric"
            elif isinstance(v, str):
                a["type"] = "string"


def _expr_atom(text: str) -> dict[str, Any] | None:
    t = text.strip().rstrip("。；;")
    if not t:
        return None
    for pat, op in _UNARY_PATTERNS:
        m = pat.match(t)
        if not m:
            continue
        val = _parse_operand(m.group("val"))
        if val is None:
            continue
        return {"op": op, "value": val}
    for kw, op in _CMP_KEYWORDS:
        for m in re.finditer(re.escape(kw), t):
            i = m.start()
            if _inside_quotes(t, i):
                continue
            left_s, right_s = t[:i].strip(), t[i + len(kw):].strip()
            if not left_s or not right_s:
                continue
            left, right = _parse_operand(left_s), _parse_operand(right_s)
            if left is None or right is None:
                continue
            _infer_types(left, right)
            return {"op": op, "left": left, "right": right}
    m = re.match(r"^(?:并非|不是|非|没有)\s*[（(]?(?P<rest>.+?)[）)]?$", t)
    if m:
        inner = _expr_atom(m.group("rest"))
        if inner is not None:
            return {"op": "not", "args": [inner]}
    bare = _parse_operand(t)
    if bare is not None:
        return {"op": "truthy", "value": bare}
    return None


def _parse_expr(text: str) -> dict[str, Any] | None:
    t = text.strip().rstrip("。；;")
    t = re.sub(r"^(?:条件是|判断|检查)\s*[：:]?\s*", "", t)
    if not t:
        return None
    parts, ops = _split_logic(t, ("且", "或"))
    if len(parts) > 1:
        nodes = [_expr_atom(p.strip()) for p in parts]
        if any(x is None for x in nodes):
            return None
        cast = [x for x in nodes if x is not None]
        if all(o == ops[0] for o in ops):
            return {"op": ops[0], "args": cast}
        acc = cast[0]
        for op, node in zip(ops, cast[1:]):
            acc = {"op": op, "args": [acc, node]}
        return acc
    return _expr_atom(t)


# ---------------------------------------------------------------------------
# do / ask / wait / set / group
# ---------------------------------------------------------------------------
def _residual(text: str, verb: str | None, entity_id: str | None) -> str:
    t = text
    if verb:
        t = t.replace(verb, " ", 1)
    if entity_id:
        t = t.replace(entity_id, " ", 1)
    t = _ACTION_REF_RE.sub(" ", t)
    t = re.sub(r"参数\s*[：:]?\s*\S+", " ", t)
    t = re.sub(r"[（(][^）)]*[）)]", " ", t)
    return re.sub(r"\s+", " ", t).strip(" ，,。；;：:")


def _value_from(residual: str, key: str) -> Any:
    if key in _NUMERIC_PARAMS:
        m = re.search(r"-?\d+(?:\.\d+)?", residual)
        if m:
            s = m.group(0)
            return float(s) if "." in s else int(s)
    m = re.search(r"「([^」]*)」", residual)
    if m:
        return m.group(1)
    if key not in _NUMERIC_PARAMS:
        tok = re.sub(r"^(?:是|为|设为|到|成|至)\s*", "", residual).strip()
        tok = re.sub(r"\s*(?:度|%|℃|级|档)$", "", tok).strip()
        if tok:
            return tok
    m = re.search(r"-?\d+(?:\.\d+)?", residual)
    if m:
        s = m.group(0)
        return float(s) if "." in s else int(s)
    return None


def _parse_do(text: str) -> dict[str, Any] | None:
    t = text.strip()
    explicit = _ACTION_REF_RE.search(t)
    entity = _ENTITY_RE.search(t)
    action: str | None = None
    verb: str | None = None
    if explicit:
        action = explicit.group("action")
        # 显式动作引用时，实体应在动作引用之后检索（避免把 custom.do_thing 当成实体）
        entity = _ENTITY_RE.search(t[explicit.end():])
    else:
        vm = _VERB_RE.search(t)
        if not vm:
            return None
        verb = vm.group(0)
        if entity:
            domain = entity.group(1).split(".", 1)[0]
            action = _REVERSE_ACTION.get((verb, domain))
            if action is None:
                suffix = {
                    "打开": "turn_on", "关闭": "turn_off", "切换": "toggle", "停止": "stop",
                    "上锁": "lock", "解锁": "unlock", "暂停": "media_pause",
                }.get(verb)
                action = f"{domain}.{suffix}" if suffix else None
        else:
            action = _VERB_ONLY_ACTION.get(verb)
    if action is None:
        return None
    params: dict[str, Any] = {}
    entity_id = entity.group(1) if entity else None
    if entity_id:
        params["entity_id"] = entity_id
    residual = _residual(t, verb, entity_id)
    explicit_params = re.search(r"参数\s*[：:]?\s*(?P<p>.+)$", t)
    if explicit_params:
        for pair in re.split(r"[，,；;]", explicit_params.group("p")):
            kv = re.match(r"\s*(?P<k>[A-Za-z_][A-Za-z0-9_]*)\s*[=:＝]\s*(?P<v>.+?)\s*$", pair)
            if kv:
                params[kv.group("k")] = _parse_value(kv.group("v"))
    for key in _PARAM_KEYS.get(action, ()):
        if key in params:
            continue
        val = _value_from(residual, key)
        if val is not None:
            params[key] = val
    node: dict[str, Any] = {"kind": "do", "adapter": "homeassistant", "action": action}
    if params:
        node["params"] = params
    return node


def _apply_meta(body: str, meta: dict[str, Any]) -> None:
    m = re.search(r"(?:超时|timeout)\s*[：:=]?\s*(\d+(?:\.\d+)?\s*[A-Za-z一-鿿]*)", body)
    if m:
        meta["timeout"] = m.group(1).strip("。；;")
    m = re.search(r"(?:会话|session)\s*[：:=]?\s*(room|device|user|global)", body)
    if m:
        meta["session"] = m.group(1)
    m = re.search(r"(?:房间|room)\s*[：:=]?\s*([^\s，,：:）)]+)", body)
    if m:
        meta["room"] = m.group(1).strip("。；;")
    m = re.search(r"(?:选项|choices?)\s*[：:=]?\s*(.+)$", body)
    if m:
        opts = [x.strip() for x in re.split(r"[、,，/|]", m.group(1)) if x.strip()]
        if opts:
            meta["ask"] = {"kind": "choice", "options": opts}
    m = re.search(
        r"(?:范围|区间|threshold)\s*[：:=]?\s*(-?[\d.]+)\s*(?:到|~|至|—|-|–)\s*(-?[\d.]+)", body
    )
    if m:
        spec = dict(meta.get("ask") or {"kind": "threshold"})
        spec["kind"] = "threshold"
        spec["min"] = float(m.group(1))
        spec["max"] = float(m.group(2))
        meta["ask"] = spec
    m = re.search(r"(?:单位|unit)\s*[：:=]?\s*(\S+)", body)
    if m:
        spec = dict(meta.get("ask") or {})
        spec["unit"] = m.group(1).strip("。；;")
        spec.setdefault("kind", "threshold")
        meta["ask"] = spec
    m = re.search(r"(?:实体域|实体|entity)\s*[：:=]?\s*(\S+)", body)
    if m:
        meta["ask"] = {"kind": "entity", "entity_domain": m.group(1).strip("。；;")}
    if re.search(r"时间段|时间范围|time_range", body):
        meta["ask"] = {"kind": "time_range"}
    if re.search(r"自由文本|任意文本|文本|text", body):
        meta["ask"] = {"kind": "text"}


def _parse_ask(text: str) -> dict[str, Any] | None:
    m = _ASK_RE.match(text.strip())
    if not m:
        return None
    rest = m.group("rest").strip()
    meta: dict[str, Any] = {}
    rest = re.sub(r"[（(]([^）)]*)[）)]", lambda mm: (_apply_meta(mm.group(1), meta) or " "), rest)
    prompt = rest.strip().strip(" ：:，,「」")
    if not prompt:
        return None
    node: dict[str, Any] = {"kind": "ask", "prompt": prompt}
    for key in ("session", "room", "timeout"):
        if key in meta:
            node[key] = meta[key]
    if "ask" in meta:
        node["ask"] = meta["ask"]
    return node


def _parse_wait(text: str) -> dict[str, Any] | None:
    m = _WAIT_RE.match(text.strip())
    if not m:
        return None
    rest = m.group("rest").strip().rstrip("。；;")
    secs = duration_seconds(rest)
    if secs is None:
        return None
    total = int(round(secs))
    h, rem = divmod(total, 3600)
    mi, sec = divmod(rem, 60)
    # 归一为 HH:MM:SS，保证渲染端 parse_duration 可再次解析（roundtrip 幂等）
    return {"kind": "wait", "duration": f"{h:02d}:{mi:02d}:{sec:02d}"}


def _parse_set(text: str) -> dict[str, Any] | None:
    for pat in _SET_RES:
        m = pat.match(text.strip())
        if m:
            return {"kind": "set", "var": m.group("var"), "value": _parse_value(m.group("val"))}
    return None


def _parse_emit(text: str) -> dict[str, Any] | None:
    m = _EMIT_RE.match(text.strip())
    if not m:
        return None
    return {"kind": "pass", "emit": {"event": m.group("q") or m.group("b")}}


def _classify(text: str) -> dict[str, Any]:
    t = text.strip()
    note = False
    if NL_RUNTIME_PLACEHOLDER in t:
        note = True
        t = _NOTE_STRIP_RE.sub("", t).strip()
    if not t:
        return {"kind": "note"} if note else {"kind": "empty"}

    m = _CHILD_RE.match(t)
    if m:
        return {"kind": "child_header", "name": (m.group("name") or "").strip()}

    m = _GROUP_RE.match(t)
    if m:
        mode_raw = (m.group("mode") or "").lower()
        mode = "parallel" if mode_raw in ("parallel", "并行", "同时") else "sequence"
        return {"kind": "group", "mode": mode, "rest": (m.group("rest") or "").strip(), "note": note}
    if _GROUP_RUN_RE.match(t):
        return {"kind": "group", "mode": "parallel", "rest": "", "note": note}
    if _GROUP_SEQ_RE.match(t):
        return {"kind": "group", "mode": "sequence", "rest": "", "note": note}

    info = _parse_ask(t)
    if info is not None:
        info["note"] = note
        return info

    info = _parse_wait(t)
    if info is not None:
        info["note"] = note
        return info

    if _VERB_RE.search(t) or _ACTION_REF_RE.search(t):
        info = _parse_do(t)
        if info is not None:
            info["note"] = note
            return info

    info = _parse_set(t)
    if info is not None:
        info["note"] = note
        return info

    if _PASS_RE.match(t):
        return {"kind": "pass", "note": note}

    m = _IF_RE.match(t)
    if m:
        expr = _parse_expr(m.group("expr"))
        if expr is not None:
            return {"kind": "if", "expr": expr, "note": note}

    info = _parse_emit(t)
    if info is not None:
        info["note"] = note
        return info

    if _TRIGGER_START_RE.match(t):
        trig = _parse_trigger(t)
        if trig is not None:
            return {"kind": "trigger", "trigger": trig[0], "for": trig[1], "note": note}

    return {"kind": "unknown", "text": t}


# ---------------------------------------------------------------------------
# 语句切分
# ---------------------------------------------------------------------------
@dataclass
class _Stmt:
    indent: int
    edge: str | None
    text: str
    line_no: int


def _try_header(body: str) -> dict[str, Any] | None:
    if body.startswith("#"):
        return {"name": body.lstrip("#").strip()}
    # 真实 af_nl 渲染头行：【name】（id：xxx，模式：yyy）
    m = re.match(r"^【(?P<name>[^】\s]+)】\s*(?P<rest>.*)$", body)
    if m:
        info: dict[str, Any] = {"name": m.group("name")}
        rest = m.group("rest")
        idm = re.search(r"id\s*[：:=]\s*(?P<id>[a-z][a-z0-9_]*)", rest)
        if idm:
            info["id"] = idm.group("id")
        mm = re.search(r"模式\s*[：:=]\s*(?P<mode>single|restart|queued|parallel|group)", rest)
        if mm:
            info["mode"] = mm.group("mode")
        return info
    m = _HEADER_RE.match(body)
    if m:
        rest = (m.group("rest") or "").strip()
        info: dict[str, Any] = {}
        q = re.search(r"「(?P<name>[^」]*)」", rest)
        if q:
            info["name"] = q.group("name")
        else:
            info["name"] = re.split(r"[（(]", rest, 1)[0].strip(" ：:，,")
        idm = re.search(r"(?:id|标识|编号)\s*[：:=]\s*(?P<id>[a-z][a-z0-9_]*)", rest)
        if idm:
            info["id"] = idm.group("id")
        mm = re.search(r"(?:模式|mode)\s*[：:=]\s*(?P<mode>single|restart|queued|parallel|group)", rest)
        if mm:
            info["mode"] = mm.group("mode")
        vm = re.search(r"(?:版本|version)\s*[：:=]\s*(?P<v>\d+)", rest)
        if vm:
            info["version"] = int(vm.group("v"))
        im = re.search(r"(?P<ir>0\.\d+\.\d+)", rest)
        if im:
            info["ir_version"] = im.group("ir")
        return info
    return None


def _tokenize(text: str) -> tuple[dict[str, Any] | None, list[_Stmt]]:
    header: dict[str, Any] | None = None
    stmts: list[_Stmt] = []
    pending_edge: str | None = None
    pending_indent: int | None = None
    for line_no, raw in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n"), 1):
        indent, body = _indent_of(raw)
        # 跳过 bullet / 预期块 等装饰行（真实 af_nl 渲染产物：· 触发策略 / · 预期（...）/    - ...）
        if body[:1] in ("·", "-", "•", "*", "—", "–"):
            continue
        body = _BULLET_RE.sub("", body, count=1)
        body = _normalize(body)
        if not body:
            continue
        hdr = _try_header(body)
        if hdr is not None:
            header = hdr
            continue
        for seg in _split_segments(body):
            edge, rest = (None, seg)
            hit = _match_edge_prefix(seg)
            if hit is not None:
                edge, rest = hit
            rest = rest.strip()
            if not rest:
                if edge:
                    pending_edge, pending_indent = edge, indent
                continue
            info = _classify(rest)
            if info["kind"] == "unknown":
                raise ParseError(f"第 {line_no} 行无法解析：{rest!r}")
            if info["kind"] in ("child_header",):
                stmts.append(_Stmt(indent, edge, rest, line_no))
                continue
            eff_edge = edge or pending_edge
            eff_indent = min(indent, pending_indent) if pending_indent is not None else indent
            pending_edge = pending_indent = None
            stmts.append(_Stmt(eff_indent, eff_edge, rest, line_no))
    return header, stmts


# ---------------------------------------------------------------------------
# 节点装配
# ---------------------------------------------------------------------------
@dataclass
class _Ctx:
    indent: int
    nodes: list[tuple[str, int]] = field(default_factory=list)


class _Builder:
    def __init__(self, id_prefix: str = "n") -> None:
        self.nodes: list[dict[str, Any]] = []
        self.edges: list[dict[str, Any]] = []
        self._stack: list[_Ctx] = [_Ctx(indent=-(10 ** 9))]
        self._by_id: dict[str, dict[str, Any]] = {}
        self._counter = 0
        self._prefix = id_prefix
        self._prev: tuple[str, int] | None = None
        self._last: str | None = None

    # -- id ---------------------------------------------------------------
    def _new_id(self) -> str:
        self._counter += 1
        return f"{self._prefix}{self._counter}"

    # -- 查询 -------------------------------------------------------------
    def _has_edge(self, node_id: str, kind: str) -> bool:
        return any(e["from"] == node_id and e["kind"] == kind for e in self.edges)

    def _admits(self, node_id: str, kind: str) -> bool:
        return self._by_id[node_id]["kind"] in _ADITS_LOOKUP(kind)

    def _find_parent(self, kind: str, indent: int) -> str | None:
        # 树规则
        if self._prev is not None:
            cand, cand_indent = self._prev
            if indent > cand_indent and self._admits(cand, kind) and not self._has_edge(cand, kind):
                return cand
        # 栈规则：先找严格祖先（nindent < indent），找不到再退回同缩进节点。
        # 否则分支边（如 on_error）会误挂到「上一个兄弟分支」而非真正的 ask/if 宿主。
        while len(self._stack) > 1 and self._stack[-1].indent >= indent:
            self._stack.pop()
        for allow_same in (False, True):
            for ctx in reversed(self._stack):
                for nid, nindent in reversed(ctx.nodes):
                    if nindent > indent:
                        continue
                    if not allow_same and nindent == indent:
                        continue
                    if self._admits(nid, kind) and not self._has_edge(nid, kind):
                        return nid
        return None

    # -- 装配 -------------------------------------------------------------
    def add_trigger(self, info: dict[str, Any], stmt: _Stmt) -> str:
        node: dict[str, Any] = {"id": self._new_id(), "kind": "on", "trigger": info["trigger"]}
        if info.get("for"):
            node["for"] = info["for"]
        self.nodes.append(node)
        self._by_id[node["id"]] = node
        self._stack = [_Ctx(indent=-(10 ** 9), nodes=[(node["id"], stmt.indent)])]
        self._prev = (node["id"], stmt.indent)
        self._last = node["id"]
        if info.get("note"):
            node[NON_REVERSIBLE_KEY] = True
        return node["id"]

    def add(self, info: dict[str, Any], stmt: _Stmt) -> str:
        node = self._make_node(info)
        node["id"] = self._new_id()
        kind = stmt.edge or "then"
        if not self.nodes:
            # 首节点：仅 then 合法（独立入口）；分支边无父可挂 → 孤儿分支报错
            if kind != "then":
                raise ParseError(f"第 {stmt.line_no} 行找不到可挂载的父节点（边 {kind}）：{stmt.text!r}")
            self.nodes.append(node)
            self._by_id[node["id"]] = node
            self._stack[-1].nodes.append((node["id"], stmt.indent))
            self._prev = (node["id"], stmt.indent)
            self._last = node["id"]
            return node["id"]
        parent = self._find_parent(kind, stmt.indent)
        if parent is None:
            raise ParseError(f"第 {stmt.line_no} 行找不到可挂载的父节点（边 {kind}）：{stmt.text!r}")
        self.edges.append({"from": parent, "to": node["id"], "kind": kind})
        self.nodes.append(node)
        self._by_id[node["id"]] = node
        self._stack[-1].nodes.append((node["id"], stmt.indent))
        self._prev = (node["id"], stmt.indent)
        self._last = node["id"]
        if kind != "then":
            self._stack.append(_Ctx(indent=stmt.indent, nodes=[(node["id"], stmt.indent)]))
        return node["id"]

    @staticmethod
    def _make_node(info: dict[str, Any]) -> dict[str, Any]:
        kind = info["kind"]
        node: dict[str, Any] = {"id": "", "kind": kind}
        if kind == "if":
            node["expr"] = info["expr"]
        elif kind == "do":
            node["adapter"] = info["adapter"]
            node["action"] = info["action"]
            if info.get("params"):
                node["params"] = info["params"]
        elif kind == "ask":
            node["prompt"] = info["prompt"]
            for key in ("session", "room", "timeout"):
                if key in info:
                    node[key] = info[key]
            if info.get("ask"):
                node["ask"] = info["ask"]
        elif kind == "wait":
            node["duration"] = info["duration"]
        elif kind == "set":
            node["var"] = info["var"]
            node["value"] = info["value"]
        elif kind == "pass":
            if info.get("emit"):
                node["emit"] = info["emit"]
        if info.get("note"):
            node[NON_REVERSIBLE_KEY] = True
        return node


def _ADITS_LOOKUP(kind: str) -> frozenset[str]:
    return _ADMITS.get(kind, frozenset())


# ---------------------------------------------------------------------------
# 子自动化（group 容器）
# ---------------------------------------------------------------------------
def _collect_group_block(stmts: Sequence[_Stmt], i: int, base_indent: int) -> tuple[list[_Stmt], int]:
    block: list[_Stmt] = []
    j = i + 1
    while j < len(stmts) and stmts[j].indent > base_indent:
        block.append(stmts[j])
        j += 1
    return block, j


def _split_children(block: Sequence[_Stmt]) -> list[tuple[str, list[_Stmt]]]:
    groups: list[tuple[str, list[_Stmt]]] = []
    current_name: str | None = None
    current: list[_Stmt] = []
    for s in block:
        info = _classify(s.text)
        if info["kind"] == "child_header":
            if current:
                groups.append((current_name or "", current))
            current_name = info.get("name") or ""
            current = []
            continue
        current.append(s)
    if current:
        groups.append((current_name or "", current))
    return groups


class _ChildCounter:
    def __init__(self) -> None:
        self.n = 0

    def next_id(self) -> str:
        self.n += 1
        return f"child_{self.n}"


def _build(
    stmts: Sequence[_Stmt], counter: _ChildCounter | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    counter = counter or _ChildCounter()
    builder = _Builder()
    i = 0
    while i < len(stmts):
        s = stmts[i]
        info = _classify(s.text)
        if info["kind"] == "child_header":
            raise ParseError(f"第 {s.line_no} 行：子自动化标题必须位于 group 节点块内")
        if info["kind"] == "empty":
            i += 1
            continue
        if info["kind"] == "note":
            if builder._last:
                builder._by_id[builder._last][NON_REVERSIBLE_KEY] = True
            i += 1
            continue
        if info["kind"] == "trigger":
            builder.add_trigger(info, s)
            i += 1
            continue
        if info["kind"] == "group":
            node = {"id": builder._new_id(), "kind": "group", "mode": info["mode"], "children": []}
            if info.get("note"):
                node[NON_REVERSIBLE_KEY] = True
            block, i = _collect_group_block(stmts, i, s.indent)
            children = _split_children(block)
            if not children:
                raise ParseError(f"第 {s.line_no} 行：group 节点没有任何子自动化（空 group 会假绿）")
            for name, body in children:
                if not body:
                    continue
                nodes, edges = _build(body, counter)
                if not nodes:
                    continue
                node["children"].append(
                    {
                        "id": counter.next_id(),
                        "name": name or f"子任务 {len(node['children']) + 1}",
                        "version": 1,
                        "mode": "single",
                        "nodes": nodes,
                        "edges": edges,
                    }
                )
            if not node["children"]:
                raise ParseError(f"第 {s.line_no} 行：group 节点的子自动化均为空")
            builder.nodes.append(node)
            builder._by_id[node["id"]] = node
            kind = s.edge or "then"
            parent = builder._find_parent(kind, s.indent) if builder.nodes[:-1] else None
            if parent is not None:
                builder.edges.append({"from": parent, "to": node["id"], "kind": kind})
            builder._stack[-1].nodes.append((node["id"], s.indent))
            builder._prev = (node["id"], s.indent)
            builder._last = node["id"]
            if kind != "then" and parent is not None:
                builder._stack.append(_Ctx(indent=s.indent, nodes=[(node["id"], s.indent)]))
            continue
        builder.add(info, s)
        i += 1
    return builder.nodes, builder.edges


# ---------------------------------------------------------------------------
# 对外 API
# ---------------------------------------------------------------------------
def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")
    if not s:
        return "auto"
    if not re.match(r"[a-z]", s):
        s = "a" + s
    return s


def _assert_no_runtime_fields(obj: Any, path: str = "$") -> None:
    if isinstance(obj, Mapping):
        for key, val in obj.items():
            if key in RUNTIME_ONLY_FIELDS:
                raise ParseError(f"parser 禁止写入运行时字段 {key}（{path}）")
            _assert_no_runtime_fields(val, f"{path}.{key}")
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            _assert_no_runtime_fields(item, f"{path}[{idx}]")


def _carry_runtime(source: Mapping[str, Any], target: Mapping[str, Any]) -> None:
    """IR→NL→IR 的「原样保留」：按节点位次把运行时字段搬回来（opt-in）。

    只搬运 `RUNTIME_ONLY_FIELDS` 与 `_non_reversible`，不改任何核心字段。
    """
    src_nodes = list(source.get("nodes") or [])
    dst_nodes = list(target.get("nodes") or [])
    for idx, dst in enumerate(dst_nodes):
        if idx >= len(src_nodes):
            break
        src = src_nodes[idx]
        for key in RUNTIME_ONLY_FIELDS:
            if key in src:
                dst[key] = src[key]
        if src.get(NON_REVERSIBLE_KEY) is True:
            dst[NON_REVERSIBLE_KEY] = True
        if isinstance(src, Mapping) and isinstance(dst, Mapping):
            sc = list(src.get("children") or [])
            dc = list(dst.get("children") or [])
            for j, (s_child, d_child) in enumerate(zip(sc, dc)):
                _carry_runtime(s_child, d_child)


def parse_automation(
    nl: str,
    *,
    automation_id: str | None = None,
    name: str | None = None,
    keep_runtime_from: Mapping[str, Any] | None = None,
    validate: bool = True,
) -> dict[str, Any]:
    """把中文自动化描述解析成 IR dict。

    返回值保证能过 `af_ir.validate_automation`（零错误）。解析不出来一律
    `ParseError`；schema 不合规则抛 `af_ir.IRValidationError`（fail-closed）。

    `keep_runtime_from`：显式 opt-in 才会把上游 IR 的运行时字段按节点位次
    原样搬回（F14 §2.2「IR→NL→IR 原样保留」）；不给它时本函数**绝不**写入
    `RUNTIME_ONLY_FIELDS` 中的任何键。
    """
    if not isinstance(nl, str):
        raise ParseError("nl 必须是字符串")
    if not nl.strip():
        raise ParseError("NL 文本为空")
    header, stmts = _tokenize(nl)
    header = dict(header or {})
    if name:
        header["name"] = name
    if automation_id:
        header["id"] = automation_id
    if not header.get("name"):
        header["name"] = "未命名自动化"

    nodes, edges = _build(stmts)
    if not nodes:
        raise ParseError("没有解析出任何节点")

    has_group = any(n["kind"] == "group" for n in _iter_nodes(nodes))
    ir_version = header.get("ir_version")
    if not ir_version or (has_group and ir_version < GROUP_IR_VERSION):
        ir_version = GROUP_IR_VERSION if has_group else IR_VERSION

    ir: dict[str, Any] = {
        "ir_version": ir_version,
        "id": header.get("id") or _slugify(header["name"]),
        "name": header["name"],
        "version": int(header.get("version") or 1),
        "mode": header.get("mode") or "single",
        "nodes": nodes,
        "edges": edges,
    }
    if keep_runtime_from is None:
        _assert_no_runtime_fields(ir)
    else:
        _carry_runtime(keep_runtime_from, ir)
    if validate:
        validate_automation(ir)
    return ir


def _iter_nodes(nodes: Sequence[Mapping[str, Any]]):
    for node in nodes:
        yield node
        for child in node.get("children") or ():
            yield from _iter_nodes(child.get("nodes") or ())


def parse_graph(nl: str, **kwargs: Any) -> list[dict[str, Any]]:
    """把一份含多段自动化的 NL 文本解析成 IR dict 列表（`render_graph` 的逆）。"""
    lines = nl.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    starts: list[int] = []
    for idx, raw in enumerate(lines):
        _, body = _indent_of(raw)
        body = _normalize(_BULLET_RE.sub("", body, count=1))
        if _HEADER_RE.match(body):
            starts.append(idx)
    if not starts:
        return [parse_automation(nl, **kwargs)]
    out: list[dict[str, Any]] = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(lines)
        chunk = "\n".join(lines[start:end])
        if chunk.strip():
            out.append(parse_automation(chunk, **kwargs))
    return out