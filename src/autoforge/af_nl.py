"""自然语言渲染器 —— Graph → 中文描述，且**确定性**（IR §1，§14-13）。

为什么它属于 G1：
1. 用户的第一诉求就是"我只想看到自然语言，不要 YAML/连线"
2. **NL 覆盖率检查**：每个节点必须至少在渲染文本里出现一次，缺失即告警
   ——这是防止"批准的与跑的不一致"的关键校验（"看到即跑的"）
3. 它是检验 IR 是否真的可理解的最快手段

覆盖率靠**渲染器自报节点集合**与图节点集合比对（不是字符串搜索，避免误判）。
"""

from __future__ import annotations

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
        return f"{_SUN_TEXT.get(trig.event or '', trig.event or '')}时"
    if trig.type == "time":
        return f"每天 {trig.at}"
    # state
    entity = trig.entity_id or "?"
    if trig.to:
        text = f"{entity} 变为「{trig.to}」"
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
        return f"{verb} {target_text}"
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

    # v2.3/F9：group 容器节点渲染为复合段，子自动化逐一展开（覆盖率检查计入）
    for gnode in auto.nodes.values():
        if gnode.kind != "group":
            continue
        lines.append(
            f"【组合 {gnode.name or gnode.id}】（{len(gnode.children)} 条子自动化 · "
            f"{'依次按序下发' if (gnode.mode or 'sequence') == 'sequence' else '声明为互不依赖、可并行下发'} · "
            "原子部署单元：任一条不过则整组不入队）"
        )
        covered.add(gnode.id)
        for i, child in enumerate(gnode.children, 1):
            res = render_automation(child)
            child_lines = res.text.split("\n")
            for j, ln in enumerate(child_lines):
                prefix = f"  {i}. " if j == 0 else "     "
                lines.append(prefix + ln)
            covered |= {f"{gnode.id}:{child.id}:{n}" for n in res.covered}
            warnings += [f"{gnode.id}:{w}" for w in res.warnings]

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
        # 组合节点由下方【组合】段统一展开。这里只留一个指针：既保证覆盖率计入，
        # 又不会出现"未知节点 group + 下面又完整展开一遍"的双重叙述。
        lines.append(_indent(depth) + prefix
                     + f"组合「{node.name or node.id}」（{len(node.children)} 条子自动化，见下方组合段）")
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
        room = f"（在 {node.room} 应答）" if node.room else ""
        timeout = f"，{format_duration(parse_duration(node.timeout))}内没回应则按超时处理" if node.timeout else ""
        return f"询问：“{node.prompt}”{room}{timeout}"
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
