"""静态扫描器 —— 第一道闸（编译期，验**安全**）（KICKOFF §4.7，IR §8）。

两道闸串行、**不可调换**：`forge build` 验安全 → `forge sim` 验逻辑。
vhass 只能验逻辑，验不了动作是否高危。

G1 十项（KICKOFF §4.7）+ 与 IR §8.2 十四项的对齐补充，见 `CHECKS` 常量。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from .af_adapters import POLICY_PARAMS, classify_action, is_destructive, host_of
from .af_affordance import domain_of, possible_states
from .af_bus import EVENT_ENTITY_PREFIX
from .af_ir import Automation, Graph, Node, Trigger
from .af_ir.expr import ExprError, check_expr, collect_var_refs
from .af_nl import render_automation

__all__ = ["Diagnostic", "ScanResult", "StaticScanner", "live_preflight", "CHECKS", "CODE_HINT", "GuardRule", "DeviceGuardRegistry"]

ERROR = "error"
WARNING = "warning"

#: 诊断码 → 说明（G1 十项在前）
CHECKS: dict[str, str] = {
    "L3_ACTION": "① 高危动作（L3）",
    "HTTP_NOT_WHITELISTED": "① HTTP 出站主机不在白名单",
    "MISSING_TIMEOUT_OR_DEFAULT": "② ask 缺 on_timeout 或 default（杜绝永久挂起）；wait 到期自动走 then",
    "ENTITY_DEP_CYCLE": "③ 跨自动化实体读写依赖成环",
    "STATIC_LOOP": "④ 静态图内循环且无终止条件",
    "CANCEL_SPAWNS_INSTANCE": "⑤ on_cancel 分支产生新实例",
    "HIGH_RISK_AFTER_SUSPEND": "⑥ 挂起分支内执行高风险动作",
    "SHADOW_WRITES_DEVICE": "⑦ Shadow 模式 do 写设备状态",
    "CROSS_AUTOMATION_VAR": "⑧ 跨自动化读写对方实例私有变量",
    "ADAPTER_POLICY_PARAM": "⑨ 适配器配置含 IR 未定义策略参数",
    "SNAPSHOT_FALSE_MULTI_AND": "⑩ snapshot=false 且多条件 AND（撕裂风险）",
    # ── G2 新增（KICKOFF §4.7 明确延后 G2 的两项 + IR §8.2 补齐）──
    "ENTITY_NOT_FOUND": "⑫ 引用了不存在的实体",
    "ENTITY_WRITE_CONFLICT": "③ 跨自动化写同一实体且无优先级",
    "L2_NEEDS_CONFIRM": "§8.1 L2 动作（门锁/窗帘/空调）强制 canary + 人工确认",
    "LOW_CONF_WRITES_DEVICE": "§10 conf<0.6 只出 ask 提案，禁止写设备",
    "DO_WITHOUT_ON_ERROR": "§6 do 建议有 on_error（缺省直接 failed）",
    "NESTED_SUSPEND_IN_CANCEL": "§5.3 on_cancel 分支内禁止再次挂起（禁止嵌套中断）",
    "ENTITY_ACL_DENIED": "① 实体读写权限不足（ACL）",
    "ENTITY_OFFLINE_NOW": "v1.6.0 联动验证闸：引用了当前不可用实体（防假绿）",
    "TRIGGER_STALE": "触发源实体长期无状态变化（疑似僵尸/断电/离线但未报 unavailable）",
    # IR §8.2 补充项
    "DUPLICATE_EDGE_PRIORITY": "⑭ 同节点同优先级边重复定义",
    "NON_IDEMPOTENT_CONCURRENT": "⑪ 非幂等动作 + restart/parallel",
    "RESERVED_NOT_IMPLEMENTED": "G1 保留字段未实现（fn）",
    # ── v1.0.0 表达力收口 ──
    "EXPR_INVALID": "表达式校验失败（未知算子/函数、参数个数、深度/节点上限）",
    "UNDECLARED_VAR": "引用了未声明的实例变量",
    "NL_COVERAGE": "⑬ NL 覆盖率检查：有节点没出现在自然语言描述里",
    # ── v0.3.0 跨自动化事件·发布侧（IR §4.3）──
    "EMIT_SELF_LOOP": "§4.3 emit 发布成环（含自环）：A 发出 event.X 且 B 由 event.X 触发",
    "EMIT_STORM_LIMIT": "§4.3 事件风暴风险：emit 节点过多 / 同一事件名重复发布",
    # ── 真机接线预检（forge run --live）──
    "LIVE_TOKEN_REQUIRED": "真机下发缺 HA 令牌",
    "LIVE_CONFIRM_REQUIRED": "真机下发缺二次确认（--confirm）",
    "LIVE_WHITELIST_REQUIRED": "真机下发缺实体白名单（--entities）",
    "LIVE_ENTITY_NOT_WHITELISTED": "真机写目标不在白名单内",
    # ── v1.2.0 断言闭环 ──
    "EXPECT_MISSING": "未声明后置条件（expect）：sim 只能回答「跑完了吗」，回答不了「跑对了吗」",
    "EXPECT_UNREACHABLE": "expect 断言的实体既不在图里读取、也不在图里写入（断言永远验不到）",
    "EXPECT_STATE_INVALID": "expect 断言的状态不是该实体域能取到的值（如灯断言成 open）",
    "ENTITY_GUARD_TIER0": "设备保护 Tier-0：读取/写入须经人工审批",
    # ── v1.7.4 ask 节点需 DB 承接 ──
    "ASK_AT_RUNTIME": "IR 含 ask 节点：运行期挂起等待回答，需豆包管家（DB）对接 TTS/语音或 WebUI 应答",
}

#: 诊断码 → **怎么改**（v1.2.0：让 Agent 一次往返就能自修正，而不是自己推断修法）
#:
#: 只给「有明确修法」的码写 hint；没有的留空，由 `Diagnostic.hint` 回退为空串。
CODE_HINT: dict[str, str] = {
    "L3_ACTION": "把该动作换成低危替代；确需保留则补 `requires_confirm` + `canary`（L3 必须白名单 + 人工确认）。",
    "HTTP_NOT_WHITELISTED": "把出站主机加进 `--http-allowed-hosts` 白名单，或改用 HA 适配器。",
    "MISSING_TIMEOUT_OR_DEFAULT": "给 ask 补 `timeout` 并加 `on_timeout` 边，或给该节点加 `default` 边。",
    "ENTITY_DEP_CYCLE": "用 `emit`/`on event` 解耦：状态变化侧只广播事件（不写实体），跟随侧只写对方实体。",
    "STATIC_LOOP": "补终止条件：加 `if` 分支走 `pass`，或改用 `for` 持续条件而不是自触发。",
    "CANCEL_SPAWNS_INSTANCE": "`on_cancel` 分支里不要写会重新触发本自动化的实体/事件。",
    "HIGH_RISK_AFTER_SUSPEND": "把高风险动作移到挂起（ask/wait）之前的求值段。",
    "SHADOW_WRITES_DEVICE": "conf<0.85 的 shadow 自动化禁止写设备；改为出 `ask` 提案或提升置信度。",
    "CROSS_AUTOMATION_VAR": "跨自动化只传消息（`emit`），不要读写对方实例变量。",
    "ADAPTER_POLICY_PARAM": "重试/降级/业务超时属于 IR 语义层，适配器参数里去掉 `retry`/`fallback` 等。",
    "SNAPSHOT_FALSE_MULTI_AND": "多条件 AND 下应保持 `snapshot=true`，否则会出现撕裂读。",
    "ENTITY_NOT_FOUND": "先用 `af_resolve_entity`（或 `forge entities resolve`）拿真实 entity_id；实体 ID 可能已漂移。",
    "ENTITY_WRITE_CONFLICT": "两条自动化写同一实体时，必须声明优先级（`conf`）或合并为一条。",
    "L2_NEEDS_CONFIRM": "L2 动作（门锁/窗帘/空调）补 `requires_confirm: true` 与 `canary`。",
    "LOW_CONF_WRITES_DEVICE": "conf<0.6 只允许出 `ask` 提案；先提升置信度或改为询问。",
    "DO_WITHOUT_ON_ERROR": "给该 `do` 节点补一条 `on_error` 边（缺省直接 failed，没有兜底）。",
    "NESTED_SUSPEND_IN_CANCEL": "`on_cancel` 分支内不要再放 ask/wait（禁止嵌套中断）。",
    "ENTITY_ACL_DENIED": "该实体不在 ACL 允许范围；换实体或请管理员放开对应读写权限。",
    "ENTITY_OFFLINE_NOW": "该实体当前不可用（unavailable/unknown）：换在线候选或先恢复设备，"
                          "离线设备会让自动化「假绿」（sim 通过、真机不生效）。",
    "TRIGGER_STALE": "触发源 {eid} 自 {last} 起 {age}h 没动过——它可能没电/离线但没报 unavailable。"
                          "先到 HA 里确认它还活着（按房间找、别全屋搜），或换一个活跃的同功能传感器；"
                          "别让一个僵尸传感器写进自动化。",
    "DUPLICATE_EDGE_PRIORITY": "同一节点同一优先级只允许一条边，删掉重复定义。",
    "NON_IDEMPOTENT_CONCURRENT": "非幂等动作不要配 `restart`/`parallel`（会重复执行）。",
    "RESERVED_NOT_IMPLEMENTED": "`fn` 仍为保留位；把自定义逻辑改写成内置表达式或拆成多个节点。",
    "EXPR_INVALID": "检查算子名/函数名拼写、参数个数，以及嵌套深度（上限 32）/节点数（上限 256）。",
    "UNDECLARED_VAR": "在 `vars` 里声明该变量，或改用已有变量名。",
    "NL_COVERAGE": "该节点未出现在自然语言描述里——补 `name` 字段，让渲染器能叙述到它。",
    "EMIT_SELF_LOOP": "自动化发出的事件又触发自己（含自环）；改名或加条件避免自触发。",
    "EMIT_STORM_LIMIT": "单条自动化 emit 节点过多/同名重复发布；拆分为多条自动化。",
    "LIVE_TOKEN_REQUIRED": "设置 `AUTOFORGE_HA_TOKEN` 或传 `--ha-token`。",
    "LIVE_CONFIRM_REQUIRED": "真机下发必须显式传 `--confirm`。",
    "LIVE_WHITELIST_REQUIRED": "传 `--live-allow`（逗号分隔）或 `--entities` 收窄可写白名单。",
    "LIVE_ENTITY_NOT_WHITELISTED": "把该写目标加进 `--live-allow`，或从图里去掉。",
    "EXPECT_MISSING": "补顶层 `expect`（实体形态 `{\"entity_id\":..., \"state\":...}` 或变量形态 `{\"var\":..., \"op\":..., \"value\":...}`）。",
    "EXPECT_UNREACHABLE": "断言里的实体必须在本图 reads 或 writes 里出现——通常是把断言写到了别的自动化的实体上。",
    "EXPECT_STATE_INVALID": "把期望状态换成该域契约里列出的值（如灯用 on/off、门锁用 locked/unlocked）；不确定就用 af_resolve_entity 查 possible_states。",
    "ENTITY_GUARD_TIER0": "该实体被设备保护标记为 Tier-0（必须人审）；把写目标移到非保护区，或请管理员调整 `device_acl`。",
    "ASK_AT_RUNTIME": "ask 节点是运行期挂起点：自动化跑到此处暂停，等豆包管家（DB）通过 TTS 播报"
                          "并接收语音/文本回答，再 POST /api/sessions/{id}/answer 恢复实例。"
                          "确认 DB 已对接此房间（room 字段）；未对接时实例会走 on_timeout 静默兜底。",
}

_SHADOW_LOW = 0.60
_SHADOW_HIGH = 0.85
_AUTO_MIN = 0.85
_HIGH_RISK = ("L2", "L3")

#: v0.3.0 事件风暴上限：单条自动化建议的 emit 节点数（超过即告警；运行期由总线熔断兜底）
_EMIT_NODE_LIMIT = 3

#: ACL 权限字面量：`rw` 可读写｜`r` 只读｜`-` 禁止
_ACL_READ = "r"
_ACL_WRITE = "w"


@dataclass(frozen=True)
class Diagnostic:
    """一条扫描诊断。

    v1.2.0 新增 `hint`（**怎么改**）：未显式给值时自动从 `CODE_HINT` 补
    ——让 Agent 一次往返就能自修正，而不是自己推断修法（对齐前身 autoflow 的
    `DSLError(message, line, code, hint)` 设计）。
    """

    code: str
    level: str
    message: str
    automation_id: str = ""
    node_id: str = ""
    hint: str = ""

    def __post_init__(self) -> None:
        if not self.hint:
            object.__setattr__(self, "hint", CODE_HINT.get(self.code, ""))

    def __str__(self) -> str:
        loc = f" [{self.automation_id}]" if self.automation_id else ""
        loc += f" 节点 {self.node_id}" if self.node_id else ""
        text = f"[{self.level.upper()}] {self.code}{loc}: {self.message}"
        if self.hint:
            text += f"\n    ↳ 建议：{self.hint}"
        return text


@dataclass
class GuardRule:
    """一条设备保护规则。

    v1.4.0（调研 §2.7）：把「实体读写权限」升级为可热更新的注册表 + tier 分级。
    `match` 描述匹配维度（entity / domain / area），`tier` 为分级，
    `perm` 仅 tier-1 生效（`rw` 可读写 / `r` 只读）。
    """

    match: Mapping[str, str]
    tier: int               # 0 = 必须人审；1 = 放行但审计
    perm: str = ""          # tier-1 内的读写细分：rw / r（- 由 tier-0 表达）


class DeviceGuardRegistry:
    """设备保护注册表：可热更新、支持 entity/domain/area 维度、命中取最严。

    与判定引擎（StaticScanner）解耦——规则改文件后下次 `scan()` 自动生效，无需重启。
    兼容旧 `entity_acl`（`from_acl`）：`-` → tier-0（必须人审），`rw`/`r` → tier-1。
    """

    def __init__(self, rules: Iterable[Any] | None = None, catalog: Any | None = None) -> None:
        self.rules: list[GuardRule] = [
            r if isinstance(r, GuardRule) else GuardRule(**r) for r in (rules or [])
        ]
        self.catalog = catalog

    @classmethod
    def from_acl(cls, acl: Mapping[str, str] | None) -> "DeviceGuardRegistry":
        """从旧 `entity_acl`：`{entity_id: "rw"|"r"|"-"}` 构造。"""
        rules = []
        for entity_id, perm in (acl or {}).items():
            tier = 0 if "-" in str(perm) else 1
            rules.append(
                GuardRule(match={"type": "entity", "value": str(entity_id)}, tier=tier, perm=str(perm))
            )
        return cls(rules)

    @classmethod
    def from_file(cls, path: str | Path) -> "DeviceGuardRegistry":
        """从 `device_acl.json` 加载规则（v1.4.0 热更新，改文件后重新加载即生效，无需重启）。

        支持三种形态：
        - **规则数组**：`[{"match": {"type": "entity|domain|area", "value": "…"}, "tier": 0|1, "perm": "rw|r"}]`；
        - **旧 ACL 对象**：`{"light.x": "rw"|"r"|"-"}`（自动 `from_acl`）；
        - 以及 `{"rules": [...]}` 包裹形态。

        调用方每次 `scan()` 前重新 `from_file` 即可让「改文件 → 下次扫描生效」。
        """
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("rules"), list):
            data = data["rules"]
        if isinstance(data, list):
            return cls(data)
        if isinstance(data, dict):
            return cls.from_acl(data)
        raise ValueError("device_acl.json 必须是规则数组或 {entity_id: perm} 对象")

    def _hit(self, rule: GuardRule, entity_id: str) -> bool:
        kind = str(rule.match.get("type", ""))
        value = str(rule.match.get("value", ""))
        if kind == "entity":
            return entity_id == value
        if kind == "domain":
            return entity_id.split(".", 1)[0] == value
        if kind == "area" and self.catalog is not None:
            area_of = getattr(self.catalog, "area_of", None)
            if callable(area_of):
                try:
                    return str(area_of(entity_id)) == value
                except Exception:
                    return False
        return False

    def match_tier(self, entity_id: str) -> int | None:
        """命中规则的最严 tier（0 最严）；未命中返回 None（默认放行）。"""
        best: int | None = None
        for rule in self.rules:
            if self._hit(rule, entity_id):
                best = rule.tier if best is None else min(best, rule.tier)
        return best

    def match_perm(self, entity_id: str) -> str | None:
        """命中规则的最严 perm（`-` 由 tier-0 表达）；未命中返回 None。"""
        best: tuple[int, str] | None = None
        for rule in self.rules:
            if not self._hit(rule, entity_id):
                continue
            if rule.tier == 0:
                return "-"
            if best is None or rule.tier < best[0]:
                best = (rule.tier, rule.perm)
        return best[1] if best else None


@dataclass
class ScanResult:
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.level == ERROR]

    @property
    def warnings(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.level == WARNING]

    @property
    def ok(self) -> bool:
        """无 error 即算通过（warning 不拦截，但必须显示）。"""
        return not self.errors

    def codes(self) -> set[str]:
        return {d.code for d in self.diagnostics}

    def render(self) -> str:
        if not self.diagnostics:
            return "静态扫描通过：0 错误 0 告警"
        lines = [str(d) for d in self.diagnostics]
        lines.append(f"—— 共 {len(self.errors)} 错误 / {len(self.warnings)} 告警")
        return "\n".join(lines)


@dataclass
class StaticScanner:
    """编译期安全闸。只读 Graph，不执行任何动作。"""

    graph: Graph
    http_allowed_hosts: tuple[str, ...] = ()
    #: 已知实体清单（来自 HA / 设备目录）。为 None 时跳过存在性校验（离线编写 IR 的场景）
    known_entities: Iterable[str] | None = None
    #: 设备保护注册表（v1.4.0，调研 §2.7）：可热更新 + tier 分级 + 取最严。
    #: 兼容旧 `entity_acl`：`{entity_id: "rw"|"r"|"-"}` → 自动包装为 tier-1/tier-0 注册表
    #: （`-` → tier-0 必须人审；`rw`/`r` → tier-1 放行）。
    device_guard: "DeviceGuardRegistry | None" = None
    entity_acl: Mapping[str, str] | None = None
    #: v1.6.0 P2 联动验证闸：实体健康视图 `{entity_id: {"offline_now": bool, "connectivity_tier": str}}`。
    #: 由 `af_service` 从设备目录注入；`None`/空 = 不校验（离线编写 IR 场景零误报）。
    entity_health: Mapping[str, Any] | None = None
    #: 触发源「长期无变化」阈值（秒）。默认 24h；0/None 关闭此项检查。
    #: 只查 entry 节点的触发实体（传感器本就该动），不查灯/空调等动作目标。
    trigger_stale_after_s: float = 86400.0

    def __post_init__(self) -> None:
        self._known = frozenset(self.known_entities) if self.known_entities is not None else None
        # 优先 device_guard；旧 entity_acl 旧式用法自动包装为 tier-1/tier-0 注册表
        self._guard = self.device_guard or DeviceGuardRegistry.from_acl(self.entity_acl)
        self._health = self.entity_health or {}

    def scan(self) -> ScanResult:
        result = ScanResult()
        for auto in self.graph:
            self._scan_automation(auto, result)
        self._scan_cross_automation(result)
        return result

    # ─────────────────────────────────────────────────────────────────
    # 单条自动化
    # ─────────────────────────────────────────────────────────────────
    def _scan_automation(self, auto: Automation, out: ScanResult) -> None:
        # `persist` 已于 P1 实现、`emit` 于 v0.3.0 实现，均不再拦截；仅 `fn` 保留位报未实现
        declared = set(auto.vars)
        assigned = {n.var for n in auto.nodes.values() if n.kind == "set" and n.var}

        for node in auto.nodes.values():
            if node.reserved:
                out.diagnostics.append(
                    Diagnostic(
                        "RESERVED_NOT_IMPLEMENTED",
                        ERROR,
                        f"节点 {node.id} 使用了未实现的保留字段：{sorted(node.reserved)}",
                        auto.id,
                        node.id,
                    )
                )
            self._check_risk(auto, node, out)
            self._check_suspension(auto, node, out)
            self._check_high_risk_after_suspend(auto, node, out)
            self._check_cancel_branch(auto, node, out)
            self._check_duplicate_edges(auto, node, out)
            self._check_vars(auto, node, declared | assigned, out)
            self._check_expr(auto, node, out)

        self._check_snapshot(auto, out)
        self._check_shadow(auto, out)
        self._check_low_confidence(auto, out)
        self._check_entities(auto, out)
        self._check_static_loop(auto, out)
        self._check_emit(auto, out)
        self._check_expect(auto, out)
        self._check_nl_coverage(auto, out)

    # ① 高危动作 + 白名单
    def _check_risk(self, auto: Automation, node: Node, out: ScanResult) -> None:
        if node.kind != "do":
            return
        level = classify_action(node.adapter or "", node.action or "", node.params)
        if level == "L3":
            reason = "外网请求" if node.adapter == "http" else ("删除类/非幂等批量" if is_destructive(node.action or "") else "高危 domain")
            out.diagnostics.append(
                Diagnostic(
                    "L3_ACTION",
                    ERROR,
                    f"动作 {node.adapter}.{node.action} 属于 L3（{reason}），默认拒绝，需人工确认",
                    auto.id,
                    node.id,
                )
            )
        if level == "L2" and not node.requires_confirm:
            out.diagnostics.append(
                Diagnostic(
                    "L2_NEEDS_CONFIRM",
                    ERROR,
                    f"动作 {node.adapter}.{node.action} 属于 L2（门锁/窗帘/空调），"
                    f"必须显式标记 requires_confirm=true 并可配 canary 灰度（IR §8.1）",
                    auto.id,
                    node.id,
                )
            )
        if node.adapter == "http":
            url = str(node.params.get("url", ""))
            host = host_of(url)
            if host and host not in self.http_allowed_hosts:
                out.diagnostics.append(
                    Diagnostic(
                        "HTTP_NOT_WHITELISTED",
                        ERROR,
                        f"出站主机 {host} 不在白名单内",
                        auto.id,
                        node.id,
                    )
                )

        # ⑪ 非幂等动作 + restart/parallel
        action = (node.action or "").lower()
        if ("toggle" in action or "increment" in action or "decrement" in action) and auto.mode in ("restart", "parallel"):
            out.diagnostics.append(
                Diagnostic(
                    "NON_IDEMPOTENT_CONCURRENT",
                    WARNING,
                    f"非幂等动作 {node.action} 与 mode={auto.mode} 组合，结果不可预测",
                    auto.id,
                    node.id,
                )
            )

        # do 建议有 on_error（IR §6：缺省则执行失败直接 failed）
        if auto.edge_of(node.id, "on_error") is None:
            out.diagnostics.append(
                Diagnostic(
                    "DO_WITHOUT_ON_ERROR",
                    WARNING,
                    f"do 节点 {node.id} 没有 on_error 边，执行失败会直接落到 failed 终态",
                    auto.id,
                    node.id,
                )
            )

        # ⑨ 适配器策略参数
        for key in node.params:
            if key in POLICY_PARAMS:
                out.diagnostics.append(
                    Diagnostic(
                        "ADAPTER_POLICY_PARAM",
                        ERROR,
                        f"适配器配置含 IR 未定义的策略参数 `{key}`——重试/超时/降级必须由 IR 显式表达"
                        f"（重试 = wait + on_error + 计数变量）",
                        auto.id,
                        node.id,
                    )
                )

    # ② 挂起必须兜底（语义拍板 A：wait 到期自动走 then，不会永久挂起，故不强制 on_timeout；
    #    仅 ask——等人类应答、可能永远无人回答——必须有 on_timeout/default 兜底）
    def _check_suspension(self, auto: Automation, node: Node, out: ScanResult) -> None:
        if not node.is_suspending:
            return
        if node.kind == "wait":
            return
        kinds = {e.kind for e in auto.outgoing(node.id)}
        if not kinds & {"on_timeout", "default"}:
            out.diagnostics.append(
                Diagnostic(
                    "MISSING_TIMEOUT_OR_DEFAULT",
                    ERROR,
                    f"{node.kind} 节点既没有 on_timeout 也没有 default，可能永久挂起",
                    auto.id,
                    node.id,
                )
            )

    # ⑤ on_cancel 分支不得产生新实例
    def _check_cancel_branch(self, auto: Automation, node: Node, out: ScanResult) -> None:
        cancel_edges = [e for e in auto.outgoing(node.id) if e.kind == "on_cancel"]
        if not cancel_edges:
            return
        for edge in cancel_edges:
            target = auto.nodes.get(edge.to)
            if target is None:
                continue
            if target.kind == "on":
                out.diagnostics.append(
                    Diagnostic(
                        "CANCEL_SPAWNS_INSTANCE",
                        ERROR,
                        "on_cancel 分支指向触发节点，等于产生新实例",
                        auto.id,
                        node.id,
                    )
                )
            # 分支可达范围里出现会触发其它自动化的动作（automation.trigger）
            for reachable in _reachable(auto, target.id):
                if reachable.kind == "do" and "automation.trigger" in (reachable.action or ""):
                    out.diagnostics.append(
                        Diagnostic(
                            "CANCEL_SPAWNS_INSTANCE",
                            ERROR,
                            f"on_cancel 分支里的 {reachable.action} 会触发新实例",
                            auto.id,
                            reachable.id,
                        )
                    )
                # ⑱ on_cancel 分支内禁止再次挂起（IR §5.3 禁止嵌套中断）
                if reachable.is_suspending:
                    out.diagnostics.append(
                        Diagnostic(
                            "NESTED_SUSPEND_IN_CANCEL",
                            ERROR,
                            f"on_cancel 分支内的 {reachable.kind} 节点会再次挂起，构成嵌套中断",
                            auto.id,
                            reachable.id,
                        )
                    )

    # ① 挂起分支内不得执行高风险动作
    #    方案 A：wait 到期走 then 是设计行为（等待条件确认后自动执行），不拦 then；
    #    只拦 ask 的 then/default/on_timeout（无人回答时执行高风险）。
    def _check_high_risk_after_suspend(self, auto: Automation, node: Node, out: ScanResult) -> None:
        if not node.is_suspending:
            return
        edge_kinds = ("on_timeout", "default", "on_cancel") if node.kind == "wait" else ("on_timeout", "default", "on_cancel", "then")
        for edge in auto.outgoing(node.id):
            if edge.kind not in edge_kinds:
                continue
                continue
            for reachable in _reachable(auto, edge.to):
                if reachable.kind != "do":
                    continue
                level = classify_action(reachable.adapter or "", reachable.action or "", reachable.params)
                if level in _HIGH_RISK:
                    out.diagnostics.append(
                        Diagnostic(
                            "HIGH_RISK_AFTER_SUSPEND",
                            ERROR,
                            f"{edge.kind} 分支会在无人值守时执行 {level} 动作 {reachable.action}",
                            auto.id,
                            reachable.id,
                        )
                    )

    # ⑭ 同节点同优先级边重复定义
    def _check_duplicate_edges(self, auto: Automation, node: Node, out: ScanResult) -> None:
        seen: dict[int, str] = {}
        for edge in auto.outgoing(node.id):
            if edge.priority in seen:
                out.diagnostics.append(
                    Diagnostic(
                        "DUPLICATE_EDGE_PRIORITY",
                        ERROR,
                        f"同一优先级重复定义边：{edge.kind}（已有一条 {seen[edge.priority]}）",
                        auto.id,
                        node.id,
                    )
                )
            else:
                seen[edge.priority] = edge.kind

    # ⑧ 跨自动化变量 / 未声明变量
    def _check_vars(self, auto: Automation, node: Node, known: set[str], out: ScanResult) -> None:
        if node.expr is None:
            return
        for ref in collect_var_refs(node.expr):
            if not ref.startswith("vars."):
                continue
            name = ref[len("vars.") :]
            if "." in name:  # vars.<其它自动化>.<变量> —— 跨自动化读写
                out.diagnostics.append(
                    Diagnostic(
                        "CROSS_AUTOMATION_VAR",
                        ERROR,
                        f"跨自动化读写对方私有变量：{ref}",
                        auto.id,
                        node.id,
                    )
                )
            elif name not in known:
                out.diagnostics.append(
                    Diagnostic(
                        "UNDECLARED_VAR",
                        WARNING,
                        f"引用了未声明的实例变量：{ref}",
                        auto.id,
                        node.id,
                    )
                )

    # v1.0.0 表达式编译期校验（函数白名单 / 参数个数 / 深度与节点上限）
    def _check_expr(self, auto: Automation, node: Node, out: ScanResult) -> None:
        if node.expr is None:
            return
        try:
            check_expr(node.expr)
        except ExprError as exc:
            out.diagnostics.append(
                Diagnostic(
                    "EXPR_INVALID",
                    ERROR,
                    f"表达式校验失败：{exc}",
                    auto.id,
                    node.id,
                )
            )

    # ⑩ snapshot=false + 多条件 AND
    def _check_snapshot(self, auto: Automation, out: ScanResult) -> None:
        if auto.snapshot:
            return
        for node in auto.nodes.values():
            if node.kind != "if" or not node.expr:
                continue
            args = node.expr.get("args") if node.expr.get("op") in ("and", "or") else None
            if args and node.expr.get("op") == "and" and len(args) >= 2:
                out.diagnostics.append(
                    Diagnostic(
                        "SNAPSHOT_FALSE_MULTI_AND",
                        WARNING,
                        "snapshot=false 且存在多条件 AND，读取可能撕裂",
                        auto.id,
                        node.id,
                    )
                )

    # ⑦ Shadow 模式不得写设备
    def _check_shadow(self, auto: Automation, out: ScanResult) -> None:
        conf = auto.confidence
        if conf is None or not (_SHADOW_LOW <= conf < _SHADOW_HIGH):
            return
        writers = [n for n in auto.nodes.values() if n.kind == "do" and n.target_entities()]
        for node in writers:
            out.diagnostics.append(
                Diagnostic(
                    "SHADOW_WRITES_DEVICE",
                    ERROR,
                    f"confidence={conf} 处于 Shadow 区间，禁止写设备状态（只读比对）",
                    auto.id,
                    node.id,
                )
            )

    # ⑭ conf < 0.6：只出 ask 提案，禁止写设备（IR §10）
    def _check_low_confidence(self, auto: Automation, out: ScanResult) -> None:
        conf = auto.confidence
        if conf is None or conf >= _SHADOW_LOW:
            return
        for node in auto.nodes.values():
            if node.kind == "do" and node.target_entities():
                out.diagnostics.append(
                    Diagnostic(
                        "LOW_CONF_WRITES_DEVICE",
                        ERROR,
                        f"confidence={conf} < {_SHADOW_LOW}：只能出 ask 提案由人确认，"
                        f"禁止直接写设备（把 do 改成 ask）",
                        auto.id,
                        node.id,
                    )
                )

    def _check_trigger_stale(self, auto: Automation, out: ScanResult) -> None:
        """触发源长期无状态变化 → 告警（疑似僵尸传感器/断电但未报 unavailable）。

        只查 entry（on）节点的 trigger.entity_id；group 触发递归展开。
        动作目标（灯/空调）长期不动是正常的，不查。
        """
        if not self.trigger_stale_after_s:
            return
        import datetime as _dt
        now = _dt.datetime.now(_dt.timezone.utc)

        def _triggers(node):
            tr = node.trigger
            if tr is None:
                return
            if getattr(tr, "type", "") == "group":
                for sub in (tr.sources or []):
                    yield from _triggers_node(sub)
            else:
                if getattr(tr, "entity_id", ""):
                    yield tr.entity_id

        def _triggers_node(tr):
            if tr is None:
                return
            if getattr(tr, "type", "") == "group":
                for sub in (tr.sources or []):
                    yield from _triggers_node(sub)
            elif getattr(tr, "entity_id", ""):
                yield tr.entity_id

        for node in auto.entry_nodes():
            for entity_id in _triggers(node):
                health = self._health.get(entity_id)
                if not isinstance(health, Mapping):
                    continue
                lc = str(health.get("last_changed") or "")
                if not lc:
                    continue
                try:
                    # HA last_changed 形如 2026-09-15T03:21:44.438450+00:00
                    dt = _dt.datetime.fromisoformat(lc.replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=_dt.timezone.utc)
                except ValueError:
                    continue
                age_s = (now - dt).total_seconds()
                if age_s <= self.trigger_stale_after_s:
                    continue
                age_h = round(age_s / 3600, 1)
                out.diagnostics.append(
                    Diagnostic(
                        "TRIGGER_STALE",
                        WARNING,
                        f"触发源 {entity_id} 自 {lc} 起约 {age_h}h 无状态变化，"
                        f"疑似僵尸/断电但未报 unavailable；确认它活着或换活跃同功能传感器",
                        auto.id,
                        node.id,
                    )
                )

    # v1.7.3：运行期 ask 节点弃用提示
    def _check_ask_deprecated(self, auto: Automation, out: ScanResult) -> None:
        for node in auto.nodes.values():
            if node.kind == "ask":
                out.diagnostics.append(
                    Diagnostic(
                        "ASK_AT_RUNTIME",
                        WARNING,
                        f"ask 节点 {node.id}（prompt={node.prompt!r}）运行期挂起等待回答；"
                        f"确认豆包管家（DB）已对接 room={node.room}；未对接则走 on_timeout 静默兜底",
                        auto.id,
                        node.id,
                    )
                )

    # ⑫ 实体存在性 + ① 设备保护分级（v1.4.0 tier 模型）
    def _check_entities(self, auto: Automation, out: ScanResult) -> None:
        refs = auto.reads() | auto.writes()

        if self._known is not None:
            for entity_id in sorted(refs):
                if entity_id not in self._known:
                    out.diagnostics.append(
                        Diagnostic(
                            "ENTITY_NOT_FOUND",
                            ERROR,
                            f"引用了不存在的实体：{entity_id}（可能是实体 ID 漂移）",
                            auto.id,
                        )
                    )

        # v1.6.0 P2 联动验证闸：引用了「此刻不可用」的实体 → **显式标注**（防假绿）。
        # 只告警不拦截：设备可能只是临时离线，静默替 Agent 判死是更大的错（fail-closed 纪律）。
        for entity_id in sorted(refs):
            health = self._health.get(entity_id)
            if not isinstance(health, Mapping) or not health.get("offline_now"):
                continue
            out.diagnostics.append(
                Diagnostic(
                    "ENTITY_OFFLINE_NOW",
                    WARNING,
                    f"实体 {entity_id} 当前不可用（unavailable/unknown）："
                    f"自动化可能「假绿」（sim 通过、真机不生效），建议改用在线候选或先恢复设备",
                    auto.id,
                )
            )

        # v1.7.2 僵尸触发源闸
        self._check_trigger_stale(auto, out)
        # v1.7.3 运行期 ask 弃用提示
        self._check_ask_deprecated(auto, out)

        if self._guard is None:
            return
        for entity_id in sorted(auto.reads()):
            if self._guard.match_tier(entity_id) == 0:
                out.diagnostics.append(
                    Diagnostic(
                        "ENTITY_GUARD_TIER0",
                        ERROR,
                        f"实体 {entity_id} 属 Tier-0 保护区，读取须经人工审批",
                        auto.id,
                    )
                )
        for node in auto.nodes.values():
            for entity_id in sorted(node.target_entities()):
                tier = self._guard.match_tier(entity_id)
                if tier == 0:
                    out.diagnostics.append(
                        Diagnostic(
                            "ENTITY_GUARD_TIER0",
                            ERROR,
                            f"实体 {entity_id} 属 Tier-0 保护区，写入须经人工审批",
                            auto.id,
                            node.id,
                        )
                    )
                elif tier == 1 and self._guard.match_perm(entity_id) == "r":
                    # tier-1 内的「只读」实体仍禁止写（保留旧 rw/r 语义）
                    out.diagnostics.append(
                        Diagnostic(
                            "ENTITY_ACL_DENIED",
                            ERROR,
                            f"实体 {entity_id} 为只读（tier-1/r），无权写入",
                            auto.id,
                            node.id,
                        )
                    )

    # v1.2.0 后置条件断言（expect）
    def _check_expect(self, auto: Automation, out: ScanResult) -> None:
        """两条检查：

        - **`EXPECT_MISSING`（warning）**：没声明 `expect` → `sim` 只能回答「跑完了吗」。
          刻意用 warning 而非 error——不破坏既有 IR（零破坏性改动），但持续提醒。
        - **`EXPECT_UNREACHABLE`（error）**：断言里的实体既不在图里读、也不在图里写
          → 该断言**永远验不到**（典型的「断言写到别人的实体上」），属确定性错误。
        """
        expects = auto.expects()
        if not expects:
            out.diagnostics.append(
                Diagnostic(
                    "EXPECT_MISSING",
                    WARNING,
                    "未声明后置条件 expect：forge sim 只能确认「跑完了」，无法确认「跑对了」",
                    auto.id,
                )
            )
            return

        reachable = auto.reads() | auto.writes()
        for item in expects:
            entity_id = item.get("entity_id")
            if entity_id and str(entity_id) not in reachable:
                out.diagnostics.append(
                    Diagnostic(
                        "EXPECT_UNREACHABLE",
                        ERROR,
                        f"expect 断言的实体 {entity_id} 既不在本自动化的 reads 也不在 writes 中，"
                        f"该断言永远无法被本图验证（可达实体：{sorted(reachable)}）",
                        auto.id,
                    )
                )
            if entity_id:
                self._check_expect_state(auto, str(entity_id), item.get("state"), out)

    def _check_expect_state(
        self, auto: Automation, entity_id: str, state: Any, out: ScanResult
    ) -> None:
        """用 v1.1.0 的域契约表校验「期望状态」是否该实体域能取到的值。

        典型错误：「灯应当为 open」——域是 light，open 是 cover 的状态。
        未登记域 / 状态含占位符（`<option>`/`<数值>`）的域（select/number/sensor…）跳过，避免误报。
        """
        if state is None:
            return
        allowed = possible_states(domain_of(entity_id))
        if any(str(s).startswith("<") for s in allowed):
            return  # 占位符域：合法取值在实体 attributes 里，静态无法判定
        wanted = state if isinstance(state, (list, tuple)) else [state]
        for value in wanted:
            if str(value) not in allowed:
                out.diagnostics.append(
                    Diagnostic(
                        "EXPECT_STATE_INVALID",
                        WARNING,
                        f"expect 断言 {entity_id} 为 {value!r}，但 {domain_of(entity_id)} 域的状态契约是 {allowed}",
                        auto.id,
                    )
                )

    # ④ 静态图内循环
    def _check_static_loop(self, auto: Automation, out: ScanResult) -> None:
        for cycle in _find_cycles(auto):
            has_suspend = any(auto.nodes[n].is_suspending for n in cycle)
            if has_suspend:
                out.diagnostics.append(
                    Diagnostic(
                        "STATIC_LOOP",
                        WARNING,
                        f"存在经挂起点的循环（轮询）：{' → '.join(cycle)}，请确认有终止条件",
                        auto.id,
                    )
                )
            else:
                out.diagnostics.append(
                    Diagnostic(
                        "STATIC_LOOP",
                        ERROR,
                        f"存在无终止条件的循环：{' → '.join(cycle)}",
                        auto.id,
                    )
                )

    # ⑬ NL 覆盖率
    def _check_nl_coverage(self, auto: Automation, out: ScanResult) -> None:
        result = render_automation(auto)
        if result.missing:
            out.diagnostics.append(
                Diagnostic(
                    "NL_COVERAGE",
                    WARNING,
                    f"以下节点未出现在自然语言描述中：{sorted(result.missing)}"
                    "（用户批准的句子与实际逻辑可能不一致）",
                    auto.id,
                )
            )

    # ─────────────────────────────────────────────────────────────────
    # 跨自动化
    # ─────────────────────────────────────────────────────────────────
    # ── v0.3.0 事件风暴上限（IR §4.3）────────────────────────────────
    def _check_emit(self, auto: Automation, out: ScanResult) -> None:
        """事件风暴上限（WARNING）：emit 节点过多 / 同一事件名重复发布。

        运行期还有第二道防线——**总线熔断**：同一事件名在窗口内过频会熔断，
        只落审计、**不进 `on_error`**（系统保护 ≠ 业务逻辑失败）。
        """
        nodes = auto.emit_nodes()
        if not nodes:
            return
        if len(nodes) > _EMIT_NODE_LIMIT:
            out.diagnostics.append(
                Diagnostic(
                    "EMIT_STORM_LIMIT",
                    WARNING,
                    f"单条自动化含 {len(nodes)} 个 emit 节点（建议上限 {_EMIT_NODE_LIMIT}），"
                    f"存在事件风暴风险；风暴将触发总线熔断并只落审计",
                    auto.id,
                )
            )
        counts: dict[str, int] = {}
        for n in nodes:
            if n.emit is not None:
                counts[n.emit.event] = counts.get(n.emit.event, 0) + 1
        dup = sorted(e for e, c in counts.items() if c > 1)
        if dup:
            out.diagnostics.append(
                Diagnostic(
                    "EMIT_STORM_LIMIT",
                    WARNING,
                    f"同一事件名被多次 emit（{', '.join(dup)}），易造成事件风暴",
                    auto.id,
                )
            )

    def _scan_cross_automation(self, out: ScanResult) -> None:
        autos = list(self.graph)
        # A 写 X 且 B 读 X → A→B 依赖边
        deps: dict[str, set[str]] = {a.id: set() for a in autos}
        for a in autos:
            writes = a.writes()
            if not writes:
                continue
            for b in autos:
                # 只看**触发源**依赖：A 写 X 且 B 由 X 触发 → A→B（含自环，自环是真死循环）
                if writes & b.trigger_entities():
                    deps[a.id].add(b.id)
        for cycle in _cycles_in(deps):
            out.diagnostics.append(
                Diagnostic(
                    "ENTITY_DEP_CYCLE",
                    ERROR,
                    "跨自动化实体读写依赖成环：" + " → ".join(cycle),
                    cycle[0] if cycle else "",
                )
            )

        # v0.3.0 发布侧：emit 事件成环——A 发出 `event.X`，B 由 `event.X` 触发 → A→B（含自环）
        emit_deps: dict[str, set[str]] = {a.id: set() for a in autos}
        for a in autos:
            emitted = {f"{EVENT_ENTITY_PREFIX}{e}" for e in a.emitted_events()}
            if not emitted:
                continue
            for b in autos:
                if emitted & b.trigger_entities():
                    emit_deps[a.id].add(b.id)
        for cycle in _cycles_in(emit_deps):
            out.diagnostics.append(
                Diagnostic(
                    "EMIT_SELF_LOOP",
                    ERROR,
                    "跨自动化事件发布成环（自触发）：" + " → ".join(cycle),
                    cycle[0] if cycle else "",
                )
            )

        # ③ 跨自动化实体抢占：多条自动化写同一实体且没有优先级区分
        writers_by_entity: dict[str, set[str]] = {}
        for a in autos:
            for entity_id in a.writes():
                writers_by_entity.setdefault(entity_id, set()).add(a.id)
        for entity_id, ids in sorted(writers_by_entity.items()):
            if len(ids) < 2:
                continue
            declared = [a.meta.get("priority") for a in autos if a.id in ids]
            # 全部声明了优先级且互不相同 → 抢占可裁决，放行
            if all(p is not None for p in declared) and len(set(declared)) == len(declared):
                continue
            out.diagnostics.append(
                Diagnostic(
                    "ENTITY_WRITE_CONFLICT",
                    ERROR,
                    f"多条自动化写同一实体 {entity_id}（{sorted(ids)}）且未声明互不相同的 "
                    f"meta.priority，抢占结果不可预测",
                    sorted(ids)[0],
                )
            )


# ─────────────────────────────────────────────────────────────────────
# 图算法
# ─────────────────────────────────────────────────────────────────────


def _reachable(auto: Automation, start: str) -> list[Node]:
    """从 start 出发可达的所有节点（防重复）。"""
    seen: set[str] = set()
    stack = [start]
    out: list[Node] = []
    while stack:
        node_id = stack.pop()
        if node_id in seen or node_id not in auto.nodes:
            continue
        seen.add(node_id)
        node = auto.nodes[node_id]
        out.append(node)
        stack.extend(e.to for e in auto.outgoing(node_id))
    return out


def _find_cycles(auto: Automation) -> list[list[str]]:
    """找图里的环（DFS 回边，三色标记）。"""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in auto.nodes}
    cycles: list[list[str]] = []
    path: list[str] = []

    def dfs(node_id: str) -> None:
        color[node_id] = GRAY
        path.append(node_id)
        for edge in auto.outgoing(node_id):
            nxt = edge.to
            if nxt not in color:
                continue
            if color[nxt] == GRAY:
                cycles.append(path[path.index(nxt) :] + [nxt])
            elif color[nxt] == WHITE:
                dfs(nxt)
        path.pop()
        color[node_id] = BLACK

    for node_id in auto.nodes:
        if color[node_id] == WHITE:
            dfs(node_id)
    return cycles


def _cycles_in(deps: Mapping[str, set[str]]) -> list[list[str]]:
    """通用有向图环检测（返回环路径列表）。"""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in deps}
    cycles: list[list[str]] = []
    path: list[str] = []

    def dfs(node: str) -> None:
        color[node] = GRAY
        path.append(node)
        for nxt in sorted(deps.get(node, ())):
            if nxt not in color:
                continue
            if color[nxt] == GRAY:
                cycles.append(path[path.index(nxt) :] + [nxt])
            elif color[nxt] == WHITE:
                dfs(nxt)
        path.pop()
        color[node] = BLACK

    for node in deps:
        if color[node] == WHITE:
            dfs(node)
    return cycles


# ─────────────────────────────────────────────────────────────────────
# 真机接线预检（forge run --live）
# ─────────────────────────────────────────────────────────────────────

LIVE_TOKEN_REQUIRED = "LIVE_TOKEN_REQUIRED"
LIVE_CONFIRM_REQUIRED = "LIVE_CONFIRM_REQUIRED"
LIVE_WHITELIST_REQUIRED = "LIVE_WHITELIST_REQUIRED"
LIVE_ENTITY_NOT_WHITELISTED = "LIVE_ENTITY_NOT_WHITELISTED"


def live_preflight(
    graph: Graph,
    *,
    known_entities: Iterable[str] | None,
    token: str | None,
    confirm: bool,
    dry_live: bool = False,
) -> ScanResult:
    """真机下发前的安全检查（`forge run --live`）。

    与编译期 `StaticScanner` 互补：
    - `StaticScanner` 拦"动作本身危险"（L3 / shadow / 低置信度写设备）——`run` 已先跑它；
    - 本预检拦"真机接线误伤"：缺令牌、缺二次确认、缺白名单、或写目标越界。

    白名单是**强制性**的（真机先不开放全量写入），缺失即拒绝。
    """
    result = ScanResult()
    if not token:
        result.diagnostics.append(
            Diagnostic(
                LIVE_TOKEN_REQUIRED,
                ERROR,
                "真机下发需要 HA 长期访问令牌（--ha-token 或环境变量 AUTOFORGE_HA_TOKEN）",
            )
        )
    if not confirm:
        result.diagnostics.append(
            Diagnostic(
                LIVE_CONFIRM_REQUIRED,
                ERROR,
                "真机下发会真实操作设备，必须显式二次确认（--confirm）",
            )
        )
    if known_entities is None and not dry_live:
        result.diagnostics.append(
            Diagnostic(
                LIVE_WHITELIST_REQUIRED,
                ERROR,
                "真机下发必须提供实体白名单（--entities），先不开放全量",
            )
        )
        return result

    # dry_live：do 只记意图不下发，不校验写目标白名单
    if dry_live:
        return result

    allowed = set(known_entities)
    for auto in graph:
        for node in auto.nodes.values():
            for entity_id in sorted(node.target_entities()):
                if entity_id not in allowed:
                    result.diagnostics.append(
                        Diagnostic(
                            LIVE_ENTITY_NOT_WHITELISTED,
                            ERROR,
                            f"写目标 {entity_id} 不在真机白名单内，拒绝下发",
                            auto.id,
                            node.id,
                        )
                    )
    return result
