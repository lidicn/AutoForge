"""自然语言渲染器 —— Graph → 中文描述，且**确定性**（IR §1，§14-13）。

为什么它属于 G1：
1. 用户的第一诉求就是"我只想看到自然语言，不要 YAML/连线"
2. **NL 覆盖率检查**：每个节点必须至少在渲染文本里出现一次，缺失即告警
   ——这是防止"批准的与跑的不一致"的关键校验（"看到即跑的"）
3. 它是检验 IR 是否真的可理解的最快手段

覆盖率靠**渲染器自报节点集合**与图节点集合比对（不是字符串搜索，避免误判）。
"""

from __future__ import annotations

import re

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .af_ir import Automation, Graph, Node, Trigger
from .af_irreversible import nl_runtime_note
from .af_time import format_duration, parse_duration

__all__ = ["NLResult", "render_automation", "render_graph"]

# 动作 → 中文动词（domain.service 级）
_ACTION_VERBS: dict[str, str] = {
    "light.turn_on": "打开",
    "light.turn_off": "关闭",
    "switch.turn_on": "打开",
    "switch.turn_off": "关闭",
    "fan.turn_on": "打开",
    "fan.turn_off": "关闭",
    "fan.toggle": "切换",
    "input_boolean.turn_on": "打开",
    "input_boolean.turn_off": "关闭",
    "input_boolean.toggle": "切换",
    "light.toggle": "切换",
    "switch.toggle": "切换",
    "climate.turn_on": "打开空调",
    "climate.turn_off": "关闭空调",
    "climate.set_temperature": "把空调设为",
    "climate.set_hvac_mode": "把空调模式设为",
    "climate.set_fan_mode": "把风扇模式设为",
    "climate.set_preset_mode": "把空调预设设为",
    "cover.open_cover": "打开",
    "cover.close_cover": "关闭",
    "cover.stop_cover": "停止",
    "lock.lock": "上锁",
    "lock.unlock": "解锁",
    "media_player.turn_on": "打开",
    "media_player.turn_off": "关闭",
    "media_player.media_pause": "暂停",
    "media_player.media_play": "播放",
    "media_player.media_stop": "停止",
    "media_player.play_media": "播放媒体",
    "media_player.volume_set": "把音量调到",
    "media_player.volume_mute": "静音",
    "scene.turn_on": "触发场景",
    "script.turn_on": "运行脚本",
    "script.turn_off": "停止脚本",
    "notify.notify": "发送通知",
    "persistent_notification.create": "弹一条通知",
    "vacuum.start": "开始清扫",
    "vacuum.pause": "暂停清扫",
    "vacuum.stop": "停止清扫",
    "vacuum.return_to_base": "回基站",
    "vacuum.locate": "定位扫地机",
    "valve.open_valve": "打开阀门",
    "valve.close_valve": "关闭阀门",
    "water_heater.turn_on": "打开热水器",
    "water_heater.turn_off": "关闭热水器",
    "water_heater.set_temperature": "把热水器温度设为",
    # F1 P4：冷门域中文动词
    "humidifier.turn_on": "打开加湿器",
    "humidifier.turn_off": "关闭加湿器",
    "humidifier.set_humidity": "把湿度设为",
    "alarm_control_panel.alarm_arm_away": "布防（离家）",
    "alarm_control_panel.alarm_arm_home": "布防（在家）",
    "alarm_control_panel.alarm_arm_night": "夜间布防",
    "alarm_control_panel.alarm_disarm": "撤防",
    "alarm_control_panel.alarm_trigger": "触发报警",
    "automation.turn_on": "启用自动化",
    "automation.turn_off": "禁用自动化",
    "automation.trigger": "手动触发",
    "group.turn_on": "打开组",
    "group.turn_off": "关闭组",
    "cover.set_cover_position": "把遮阳帘位置设为",
}

_CMP_SYMBOL = {"eq": "等于", "ne": "不等于", "lt": "低于", "lte": "不高于", "gt": "高于", "gte": "不低于"}

_SUN_TEXT = {"sunset": "日落", "sunrise": "日出"}


@dataclass
class NLResult:
    """渲染结果。"""

    text: str
    covered: set[str] = field(default_factory=set)
    missing: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        total = len(self.covered) + len(self.missing)
        return 1.0 if total == 0 else len(self.covered) / total

    @property
    def ok(self) -> bool:
        return not self.missing


# ─────────────────────────────────────────────────────────────────────
# 片段渲染
# ─────────────────────────────────────────────────────────────────────


def _trigger_text(trig: Trigger) -> str:
    if trig.type == "group":
        parts = [_trigger_text(s) for s in trig.sources]
        joiner = " 且 " if trig.op == "and" else " 或 "
        return joiner.join(parts)
    if trig.type == "event":  # v0.4.0 订阅侧
        return f"收到事件「{trig.event or '?'}」"
    if trig.type == "sun":
        s = _SUN_TEXT.get(trig.event or "", trig.event or "") + "时"
        off = trig.offset
        if off:
            om = re.fullmatch(r"\s*([+-]?)(\d+):(\d{1,2}):(\d{1,2})\s*", str(off))
            if om:
                sign = -1.0 if om.group(1) == "-" else 1.0
                secs = sign * (int(om.group(2)) * 3600 + int(om.group(3)) * 60 + int(om.group(4)))
                if secs:
                    s += ("前" if secs < 0 else "后") + format_duration(abs(secs))
        return s
    if trig.type == "time":
        return f"每天 {trig.at}"
    # state
    entity = trig.entity_id or "?"
    if trig.from_ and trig.to:
        text = f"{entity} 从「{trig.from_}」变为「{trig.to}」"
    elif trig.to:
        text = f"{entity} 变为「{trig.to}」"
    elif trig.from_:
        text = f"{entity} 从「{trig.from_}」变为其他"
    else:
        text = f"{entity} 发生变化"
    return text


def _operand_text(operand: Mapping[str, Any]) -> str:
    if "const" in operand:
        value = operand["const"]
        return f"“{value}”" if isinstance(value, str) else str(value)
    name = str(operand.get("var", "?"))
    for prefix in ("entity.", "vars.", "context."):
        if name.startswith(prefix):
            return name[len(prefix) :]
    return name


def _expr_text(expr: Mapping[str, Any] | None) -> str:
    if not expr:
        return "（无条件）"
    op = expr.get("op")
    if op in ("and", "or"):
        joiner = " 且 " if op == "and" else " 或 "
        parts = [_expr_text(a) for a in expr.get("args", ())]
        return "（" + joiner.join(parts) + "）" if len(parts) > 1 else "".join(parts)
    if op == "not":
        args = expr.get("args", ())
        return "非 " + (_expr_text(args[0]) if args else "?")
    if op in _CMP_SYMBOL:
        return f"{_operand_text(expr['left'])} {_CMP_SYMBOL[op]} {_operand_text(expr['right'])}"
    if op in ("is_on", "is_home"):
        return f"{_operand_text(expr['value'])} 是开启状态"
    if op in ("is_off", "is_not_home"):
        return f"{_operand_text(expr['value'])} 是关闭状态"
    if op == "truthy":
        return f"{_operand_text(expr['value'])} 为真"
    return f"未知算子 {op}"


# 渲染时内联到动词与目标之间的数值型参数（保证 roundtrip 可重解析）
_INLINE_NUMERIC = {
    "climate.set_temperature": ("temperature",),
    "climate.set_humidity": ("humidity",),
    "media_player.volume_set": ("volume_level",),
    "cover.set_cover_position": ("position",),
    "light.turn_on": ("brightness",),
}
_UNIT_TEXT = {
    "temperature": " 度",
    "humidity": " %",
    "volume_level": "",
    "position": " %",
    "brightness": "",
}


def _action_text(node: Node) -> str:
    action = node.action or "?"
    params = node.params or {}
    target = params.get("entity_id")
    target_text = ""
    if isinstance(target, str):
        target_text = target
    elif isinstance(target, (list, tuple)) and target:
        target_text = "、".join(str(t) for t in target)

    verb = _ACTION_VERBS.get(action)
    if verb and target_text:
        extra = ""
        for key in _INLINE_NUMERIC.get(action, ()):
            if key in params and params[key] is not None:
                extra = f" {params[key]}{_UNIT_TEXT.get(key, '')}"
                break
        return f"{verb}{extra} {target_text}"
    if verb:
        return verb
    if node.adapter == "http":
        return f"请求外网地址 {params.get('url', '?')}"
    return f"调用 {action}（{target_text or '无目标实体'}）"


# ─────────────────────────────────────────────────────────────────────
# 主渲染：沿边遍历，逐节点吐出一句中文
# ─────────────────────────────────────────────────────────────────────


def render_automation(auto: Automation) -> NLResult:
    """确定性渲染一条自动化。同图必得同文（可 diff、可签核）。"""
    lines: list[str] = []
    covered: set[str] = set()
    warnings: list[str] = []

    lines.append(f"【{auto.name}】（id：{auto.id}，模式：{auto.mode}）")
    _MODE_NOTE = {
        "single": "已在运行时不再重复触发",
        "restart": "重新触发会先执行「取消后」的清理动作（与 HA 不同，见 docs/HA_SEMANTIC_DIFF.md）",
        "queued": "已在运行时排队依次执行",
        "parallel": "允许多实例并发",
    }
    lines.append(f"· 触发策略：{_MODE_NOTE.get(auto.mode, auto.mode)}")

    entries = auto.entry_nodes()
    has_group = any(n.kind == "group" for n in auto.nodes.values())
    if not entries and not has_group:
        warnings.append(f"{auto.id}：没有入口节点（on）")

    for entry in entries:
        _walk(auto, entry, lines, covered, set(), depth=0)
    # 没有 `on` 入口、或者 group 不在任何入口可达处的情形：把没被走到的 group 补渲染。
    # （group 就地展开只发生在 `_walk` 里，而 `_walk` 从入口出发；一条"整条自动化就是
    #  一个 group"的图没有入口，不补这一段就会渲染成空壳——覆盖率检查会报 missing。）
    for gnode in auto.nodes.values():
        if gnode.kind == "group" and gnode.id not in covered:
            _expand_group(gnode, lines, covered, depth=0, prefix="")
    # v1.2.0：后置条件断言必须写进 NL——「跑完应当如何」是给人签核的一部分
    # （同 emit 的理由：批准的与跑的必须一致）
    if auto.expects():
        lines.append("· 预期（跑完之后应当如此）：")
        for item in auto.expects():
            lines.append(f"    - {_expect_text(item)}")

    missing = set(auto.nodes) - covered
    if missing:
        warnings.append(f"{auto.id}：以下节点未出现在自然语言描述中（覆盖率检查失败）：{sorted(missing)}")

    return NLResult(text="\n".join(lines), covered=covered, missing=missing, warnings=warnings)


#: group 的 mode 中文措辞。渲染成「编组（sequence · 依次按序下发）」这种双写形式：
#: 括号里同时给机器可读的 `mode` 与人读的下发语义——解析器取 `mode`，
#: 签核的人看措辞（`test_nl_renders_group_mode_wording` 钉的就是这两个词）。
_GROUP_MODE_TEXT = {
    "sequence": "依次按序下发",
    "parallel": "声明为互不依赖、可并行下发",
}


def _expand_group(
    node: Node,
    lines: list[str],
    covered: set[str],
    *,
    depth: int,
    prefix: str,
) -> None:
    """把一个 group 节点就地展开成「声明 + 子自动化逐一内联」。

    与解析器对齐：`编组（<mode> · <措辞>）` 声明，`子自动化「<name>」` 起一行，
    其后是那条子自动化自己的正文（去掉它自己的【头行】与「· 触发策略」两行）。
    """
    mode = node.mode or "sequence"
    wording = _GROUP_MODE_TEXT.get(mode, _GROUP_MODE_TEXT["sequence"])
    covered.add(node.id)  # 两条调用路径（`_walk` 内联 / 无入口补渲染）都靠它计入覆盖率
    # 名字放在 mode 之后：`_GROUP_RE` 是「先取 mode、再吃掉剩余」，名字插在 mode 前面
    # 会让 parallel 被读成缺省 sequence（往返就错了）。
    label = f"「{node.name or node.id}」" if (node.name or node.id) else ""
    lines.append(_indent(depth) + prefix + f"编组（{mode} · {wording}）{label}")
    for child in node.children:
        lines.append(_indent(depth + 1) + f"子自动化「{child.name or child.id}」")
        cres = render_automation(child)
        covered |= {f"{node.id}:{child.id}:{n}" for n in cres.covered}
        warnings = getattr(cres, "warnings", ())
        for w in warnings:
            lines.append(_indent(depth + 1) + f"  ⚠ {w}")
        for cl in cres.text.split("\n")[2:]:
            lines.append(_indent(depth + 1) + "  " + cl)


def _walk(
    auto: Automation,
    node: Node,
    lines: list[str],
    covered: set[str],
    path: set[str],
    depth: int,
    prefix: str = "",
) -> None:
    """深度优先展开图：每个节点吐一句中文，子节点带边前缀。

    `covered` 在首次渲染时记录节点——**覆盖率检查就靠它与图节点集合比对**。
    """
    if depth > 40:  # 深度保护（静态扫描会另行报循环）
        return
    if node.id in path:  # 循环保护
        lines.append(_indent(depth) + "…（检测到循环，停止展开）")
        return

    if node.id in covered:  # 已渲染过：只标跳转，避免重复叙述
        tail = "结束" if node.kind == "pass" else f"跳转到节点 {node.id}"
        lines.append(_indent(depth) + prefix + tail)
        return

    covered.add(node.id)
    if node.kind == "group":
        # 组合节点就地展开：声明 + 子自动化逐一内联（解析器通过「子自动化「name」」归集）
        _expand_group(node, lines, covered, depth=depth, prefix=prefix)
    else:
        lines.append(_indent(depth) + prefix + _node_text(auto, node))

    for edge in _sorted_outgoing(auto, node.id):
        target = auto.nodes.get(edge.to)
        if target is None:
            continue
        _walk(auto, target, lines, covered, path | {node.id}, depth + 1, _edge_prefix(edge.kind))


def _sorted_outgoing(auto: Automation, node_id: str) -> list[Any]:
    return sorted(auto.outgoing(node_id), key=lambda e: (e.priority, e.to))


def _indent(depth: int) -> str:
    return "  " * depth


def _strip_indent(line: str) -> str:
    return line.lstrip()


def _emit_text(emit: Any) -> str:
    """v0.3.0 发布侧：`emit` 的自然语言。

    必须写进 NL——NL 是给人签核的，"发出什么事件"属于可见行为
    （IR §14-13 覆盖率精神：批准的与跑的必须一致）。
    """
    data = getattr(emit, "data", None) or {}
    payload = f"（附带 {'、'.join(sorted(data))}）" if data else ""
    delay = getattr(emit, "delay", None)
    if delay:
        return f"延迟 {format_duration(parse_duration(delay))}后发出事件「{emit.event}」{payload}"
    return f"发出事件「{emit.event}」{payload}"


def _node_text(auto: Automation, node: Node) -> str:
    """节点文案；节点带 `emit` 时追加"发出事件"描述（v0.3.0）。

    P4：节点携带运行时/不可逆字段（`stage`/`diff_sha` 等）时追加 `[运行时]` 占位说明——
    承认其存在但不假装可逆（F14 §2.2/§2.3）。
    """
    base = _node_text_base(auto, node)
    if node.emit is not None:
        base = f"{base}，并{_emit_text(node.emit)}"
    note = nl_runtime_note(node.raw)
    if note:
        base = f"{base}{note}"
    return base


def _node_text_base(auto: Automation, node: Node) -> str:
    if node.kind == "on":
        text = f"当 {_trigger_text(node.trigger) if node.trigger else '（无触发源）'}"
        if node.for_:
            text += f"，并且这个状态**持续保持 {format_duration(parse_duration(node.for_))}**"
        return text + "："
    if node.kind == "if":
        return f"检查 {_expr_text(node.expr)}"
    if node.kind == "do":
        return _action_text(node)
    if node.kind == "ask":
        bits = []
        if node.session:
            bits.append(f"会话 {node.session}")
        if node.room:
            bits.append(f"在 {node.room} 应答")
        if node.timeout:
            bits.append(f"超时 {format_duration(parse_duration(node.timeout))}")
        suffix = f"（{('，'.join(bits))}）" if bits else ""
        return f"询问：“{node.prompt}”{suffix}"
    if node.kind == "wait":
        return f"等待 {format_duration(parse_duration(node.duration or '0s'))}"
    if node.kind == "set":
        source = f"变量 {node.from_}" if node.from_ else f"“{node.value}”"
        return f"记下 {node.var} = {source}"
    if node.kind == "pass":
        return "结束"
    return f"未知节点 {node.kind}"


def _expect_text(item: Mapping[str, Any]) -> str:
    """`expect` 一条的中文描述（v1.2.0）。"""
    note = f"（{item['note']}）" if item.get("note") else ""
    if item.get("entity_id"):
        expected = item.get("state")
        if isinstance(expected, (list, tuple)):
            target = " 或 ".join(f"“{x}”" for x in expected)
        else:
            target = f"“{expected}”"
        return f"{item['entity_id']} 应当为 {target}{note}"
    var_name = item.get("var", "?")
    op = item.get("op") or "eq"
    symbol = _CMP_SYMBOL.get(op, op)
    if op == "eq":
        return f"{var_name} 应当等于 {item.get('value')!r}{note}"
    return f"{var_name} 应当{symbol} {item.get('value')!r}{note}"


def _edge_prefix(kind: str) -> str:
    if kind == "then":
        return "然后："
    if kind == "yes":
        return "如果你同意："
    if kind == "no":
        return "如果你拒绝："
    if kind == "default":
        return "如果没听清你的回答："
    if kind == "on_timeout":
        return "如果超时："
    if kind == "on_cancel":
        return "如果中途被打断："
    if kind == "on_error":
        return "如果执行失败："
    return ""


def render_graph(graph: Graph) -> NLResult:
    """渲染整个 Graph（多条自动化）。"""
    chunks: list[str] = []
    covered: set[str] = set()
    missing: set[str] = set()
    warnings: list[str] = []
    for auto in graph:
        result = render_automation(auto)
        chunks.append(result.text)
        covered |= {f"{auto.id}:{n}" for n in result.covered}
        missing |= {f"{auto.id}:{n}" for n in result.missing}
        warnings += result.warnings
    return NLResult(text="\n\n".join(chunks), covered=covered, missing=missing, warnings=warnings)
