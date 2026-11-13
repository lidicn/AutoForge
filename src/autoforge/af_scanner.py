"""静态扫描器 —— 第一道闸（编译期，验**安全**）（KICKOFF §4.7，IR §8）。

两道闸串行、**不可调换**：`forge build` 验安全 → `forge sim` 验逻辑。
vhass 只能验逻辑，验不了动作是否高危。

G1 十项（KICKOFF §4.7）+ 与 IR §8.2 十四项的对齐补充，见 `CHECKS` 常量。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Iterable, Mapping

from .af_adapters import POLICY_PARAMS, classify_action, is_destructive, host_of
from .af_adapters.inbox import inbox_overlong_fields
from .af_affordance import domain_of, possible_states
from .af_bus import EVENT_ENTITY_PREFIX
from .af_ir import (
    Automation,
    Graph,
    Node,
    ParamDepthError,
    Trigger,
    TriggerDepthError,
    assert_param_budget,
    check_trigger_depth,
)
from .af_ir.expr import ExprError, check_expr, collect_var_refs
from .af_nl import render_automation
from .af_time import parse_duration

__all__ = ["Diagnostic", "ScanResult", "StaticScanner", "live_preflight", "CHECKS", "CODE_HINT", "GuardRule", "DeviceGuardRegistry"]

ERROR = "error"
WARNING = "warning"

#: 诊断码 → 说明（G1 十项在前）
CHECKS: dict[str, str] = {
    "L3_ACTION": "① 高危动作（L3）",
    "HTTP_NOT_WHITELISTED": "① HTTP 出站主机不在白名单",
    "MISSING_TIMEOUT_OR_DEFAULT": "② ask 缺 on_timeout 或 default（杜绝永久挂起）；wait 到期自动走 then",
    "ENTITY_DEP_CYCLE": "③ 跨自动化实体读写依赖成环",
    "UNRESOLVED_ENTITY": "⑬ 实体引用仍是未解析占位符（`?名字`）——目录里查不到这个实体",
    "SELF_TRIGGER_LOOP": "⑭ 同自动化写回自己的触发源（自激风险按效果表判定，非跨自动化）",
    "STATIC_LOOP": "④ 静态图内循环且无终止条件",
    "CANCEL_SPAWNS_INSTANCE": "⑤ on_cancel 分支产生新实例",
    "HIGH_RISK_AFTER_SUSPEND": "⑥ 无人值守分支（on_timeout/default/on_cancel）内执行高风险动作（ask 的 yes/no 有人在环、不在射程；带 canary 的 do 视同挂起点——裁定 20261031）",
    "SHADOW_WRITES_DEVICE": "⑦ Shadow 模式 do 写设备状态",
    "CROSS_AUTOMATION_VAR": "⑧ 跨自动化读写对方实例私有变量",
    "ADAPTER_POLICY_PARAM": "⑨ 适配器配置含 IR 未定义策略参数",
    "SNAPSHOT_FALSE_MULTI_AND": "⑩ snapshot=false 且多条件 AND（撕裂风险）",
    # ── G2 新增（KICKOFF §4.7 明确延后 G2 的两项 + IR §8.2 补齐）──
    "ENTITY_NOT_FOUND": "⑫ 引用了不存在的实体",
    "ENTITY_WRITE_CONFLICT": "③ 跨自动化写同一实体且无优先级",
    "L2_NEEDS_CONFIRM": "§8.1 L2 动作（门锁/窗帘/空调）强制 canary + 部署期显眼标记（requires_confirm）",
    "L2_NEEDS_CANARY": "§8.1 P1-2 L2 动作只标 requires_confirm 而无 canary＝免费豁免，服务端策略表不放行",
    "LOW_CONF_WRITES_DEVICE": "§10 conf<0.6 只出 ask 提案，禁止写设备",
    "DO_WITHOUT_ON_ERROR": "§6 do 建议有 on_error（缺省直接 failed）",
    "NESTED_SUSPEND_IN_CANCEL": "§5.3 on_cancel 分支内禁止再次挂起（禁止嵌套中断）",
    "ENTITY_ACL_DENIED": "① 实体读写权限不足（ACL）",
    "ENTITY_OFFLINE_NOW": "v1.6.0 联动验证闸：引用了当前不可用实体（防假绿）",
    "TRIGGER_STALE": "触发源实体长期无状态变化（疑似僵尸/断电/离线但未报 unavailable）",
    # IR §8.2 补充项
    "DUPLICATE_EDGE_PRIORITY": "⑭ 同节点同优先级边重复定义",
    "NON_IDEMPOTENT_CONCURRENT": "⑪ 非幂等动作 + restart/parallel",
    "NO_ENTRY_NODE": "自动化没有任何触发节点（kind=on）：永远不会被触发，build 直接判红",
    "RESERVED_NOT_IMPLEMENTED": "G1 保留字段未实现（fn）",
    # ── v1.0.0 表达力收口 ──
    "EXPR_INVALID": "表达式校验失败（未知算子/函数、参数个数、深度/节点上限）",
    "EXPR_NS_INVALID": "表达式 var 命名空间非法（只允许 entity./vars./context.；裸 entity_id 须写 entity.<id>）",
    "TRIGGER_INVALID": "触发源校验失败（group 嵌套超预算或自引用，实体集无法展开）",
    "PARAMS_TOO_DEEP": "动作参数容器嵌套超预算或自引用（运行期遍历腿必然失败，编译期先拒）",
    "INBOX_PARAM_TOO_LONG": "契约 §1.3 收件箱载荷超长（text/title/body/content，上限现读库侧常量）：DB 侧对超长 fail-closed 丢弃 ⇒ 这条投递永远到不了音箱",
    "UNDECLARED_VAR": "引用了未声明的实例变量",
    "NL_COVERAGE": "⑬ NL 覆盖率检查：有节点没出现在自然语言描述里",
    # ── v0.3.0 跨自动化事件·发布侧（IR §4.3）──
    "EMIT_SELF_LOOP": "§4.3 emit 发布成环（含自环）：仅事件、无设备回灌，放行（告警）",
    "CROSS_DEP_CYCLE": "P1-4 跨自动化混合依赖成环（含 emit 解耦边）：运行时收敛，放行（告警）",
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
    # ── v1.14.22 假绿三族收口（审计 AF-123/124/125）──
    "TIME_AT_INVALID": "time 触发的 at 字段不是 HH:MM——秒格式/sunrise/sunset/25:00 这类值 build 照样 ok 是假绿（运行期 matches_at 只认 HH:MM）",
    "FOR_WITHOUT_TO": "`for` 持续条件挂在**没有 to 的入口**上（group 型或变化型 state）——运行期 `_still_holds` 只能 fail-closed，条件是死的",
    "DURATION_INVALID": "`for`／`wait` 的时长字面量过不了运行期同一份 `af_time.parse_duration`（负值／`-00:10:00`／非数字／空壳）——build 绿，运行期要么整轮事件分发抛穿、要么该节点静默软失效",
    "SERVICE_UNKNOWN": "do 动作的服务名在该域不存在（turn_onnn）——build/sim 双绿但真机必 404",
    "ENTITY_CHECK_DEGRADED": "设备目录为空，实体存在性校验静默跳过（entity_check=skipped 但零诊断）——假绿通道",
    # ── AF-707 假绿族（PM 裁定补码，2026-10-31）──
    "EDGE_KIND_MISMATCH": "边 kind 不在该节点 kind 的允许集合（如 if 的真值分支写 yes）——pick_edge 永远选不中，实例静默终止报 done",
    "ASK_BINARY_NO_DENY_PATH": "C-2 G.2①：二元 ask 缺拒绝出口（无 no 边，或 default 直达执行类节点）——听不清不执行的 fail-closed 缺口",
    "ASK_STRUCT_NO_THEN": "C-2 G.2③：结构化 ask 缺 then 边（参数收集无法恢复）或带运行期不可达的 yes/no 死边",
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
    "HIGH_RISK_AFTER_SUSPEND": "把高风险动作移到挂起（ask/wait/canary 观察窗）之前的求值段；ask 的 yes/no 分支有人在环、不在射程（裁定 20261031 一次审批）。",
    "SHADOW_WRITES_DEVICE": "conf<0.85 的 shadow 自动化禁止写设备；改为出 `ask` 提案或提升置信度。",
    "CROSS_AUTOMATION_VAR": "跨自动化只传消息（`emit`），不要读写对方实例变量。",
    "ADAPTER_POLICY_PARAM": "重试/降级/业务超时属于 IR 语义层，适配器参数里去掉 `retry`/`fallback` 等。",
    "SNAPSHOT_FALSE_MULTI_AND": "多条件 AND 下应保持 `snapshot=true`，否则会出现撕裂读。",
    "ENTITY_NOT_FOUND": "先用 `af_resolve_entity`（或 `forge entities resolve`）拿真实 entity_id；实体 ID 可能已漂移。",
    "ENTITY_WRITE_CONFLICT": "两条自动化写同一实体时，必须声明优先级（`conf`）或合并为一条。",
    "L2_NEEDS_CONFIRM": "L2 动作（门锁/窗帘/空调）补 `requires_confirm: true` 与 `canary`。",
    "L2_NEEDS_CANARY": "已标 `requires_confirm` 只算「问过人」，不等于「灰度过」：给该 L2 动作补 `canary`"
                       "（`duration` + `auto_rollback`），否则 P1-2 服务端策略表按免费豁免拒绝。",
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
    "NO_ENTRY_NODE": "至少添加一个 kind=on 的触发节点（state/sun/time/event/group），"
                     "并把它用 then 边接到首个动作；一条没有入口的自动化永远不会运行。",
    "RESERVED_NOT_IMPLEMENTED": "`fn` 仍为保留位；把自定义逻辑改写成内置表达式或拆成多个节点。",
    "EXPR_INVALID": "检查算子名/函数名拼写、参数个数，以及嵌套深度（上限 32）/节点数（上限 256）。",
    "EXPR_NS_INVALID": "把 var 改成 entity.<完整entity_id>（如 entity.sensor.study_temp）；属性读取写 entity.<id>.<attr>。裸写 sensor.x 会在运行期求值失败，而 if 的失败只走兜底边——图上表现为\"条件永不成立\"的死自动化。",
    "UNRESOLVED_ENTITY": "先用 af_resolve_entity(name=\"书房温度\") 拿真实 entity_id（中文名按 friendly_name 子串匹配，命不中就是 0 候选，见体验者 AF-003）；查到多个就选一个把完整 id 写进意图。⛔ 不要指望 `?占位符` 能过闸——它过去只在依赖环那一站被动露出，报的是误导性的「跨自动化成环」。",
    "SELF_TRIGGER_LOOP": "同自动化写回自己的触发源不阻断（合法的自动关停就长这样）。要自查两点：① 动作写入后的状态是否等于触发目标（等于就有自激形态，靠 HA 状态去重与总线熔断收敛，建议加 `for`/`debounce`）；② 若本意是跨自动化协作，请改用 emit 事件解耦。",
    "TRIGGER_INVALID": "把 group 触发源改平（一层 `or`/`and` 列出全部条件），别让 group 套 group，也别自引用。",
    "PARAMS_TOO_DEEP": "把参数摊平成正常层级；嵌套到这种深度，通常是引用写错成了自引用。",
    "INBOX_PARAM_TOO_LONG": "把该字段裁短到上限以内，或把长文拆成多条 `inbox.*` 投递后先做摘要。上限不在 AF 里写死，"
                            "真源是库侧 `homesdk.presence.INBOX_MAX_TEXT`/`INBOX_MAX_TITLE`/`INBOX_MAX_BODY`（本条诊断报的就是现读值）。"
                            "别指望 DB 收下超长载荷——契约 §1.3 写明它对超长 fail-closed 丢弃并审计，音箱那头不会响。",
    "UNDECLARED_VAR": "在 `vars` 里声明该变量，或改用已有变量名。",
    "NL_COVERAGE": "该节点未出现在自然语言描述里——补 `name` 字段，让渲染器能叙述到它。",
    "EMIT_SELF_LOOP": "纯事件环、无设备回灌，运行时安全；当前为放行告警（不阻断编译），如需消除可改名或加条件避免自触发。",
    "CROSS_DEP_CYCLE": "环含 emit 事件解耦边（即 case_lamp_sync 的良性双向同步模式），运行时靠状态收敛+熔断避免死循环，已放行（告警）；若确为无 emit 解耦的直接互写实体，应重构为 emit 解耦写法（否则会被 ENTITY_DEP_CYCLE 拦截）。",
    "EMIT_STORM_LIMIT": "单条自动化 emit 节点过多/同名重复发布；拆分为多条自动化。",
    "LIVE_TOKEN_REQUIRED": "设置 `HA_LONG_LIVED_TOKEN`（旧 `AUTOFORGE_HA_TOKEN`）或传 `--ha-token`。",
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
    "TIME_AT_INVALID": "at 只能写 HH:MM（00-23:00-59）；日落日出请用 `type: \"sun\"` 触发器，别塞进 time.at。",
    "FOR_WITHOUT_TO": "给这条入口补显式 `to`（state 型写目标态），或把 `for` 挪到带 `to` 的入口；group 型入口不能挂 `for`。",
    "DURATION_INVALID": "把时长改成非负、能被 `parse_duration` 解析的形态：`10m`／`90s`／`2h`／`1d`／纯数字秒／HA 原生 `00:10:00`／ISO `PT5S`。⛔ 负值不是「反向等待」的写法，运行期一律 fail-closed 抛错。",
    "SERVICE_UNKNOWN": "服务名打错（turn_onnn）；该域合法服务用 af_resolve_entity 查 services 清单。",
    "ENTITY_CHECK_DEGRADED": "先 af_catalog 刷新设备目录再 build——空目录时幽灵实体检测不到，这条 ok 不可信。",
    "ASK_BINARY_NO_DENY_PATH": "给二元 ask 补 `no` 边（指向 pass/收尾），或把 `default` 改指非执行下游；⛔ 不要删 no 让 unknown 落到 do。",
    "ASK_STRUCT_NO_THEN": "给结构化 ask 补 `then` 边（结构化应答的恢复通道），并删掉 yes/no 出边（那是二元语义，结构化会话走 then）。",
    "EDGE_KIND_MISMATCH": "把该出边的 kind 改成目标节点 kind 的合法取值（if 真值分支用 then、否值分支用 no/default；yes/no 只属于 ask）。合法集合见 af_scanner.ALLOWED_OUT_EDGE_KINDS。",
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

    def __init__(
        self,
        rules: Iterable[Any] | None = None,
        catalog: Any | None = None,
        deny_all: bool = False,
        reason: str = "",
    ) -> None:
        self.rules: list[GuardRule] = [
            r if isinstance(r, GuardRule) else GuardRule(**r) for r in (rules or [])
        ]
        self.catalog = catalog
        # AF49-R：读不出规则文件时的 fail-closed 形态——一切实体命中 Tier-0，
        # 与「没配保护（rules 为空）」逐字可区分（后者 match_tier 返回 None=放行）。
        self.deny_all = bool(deny_all)
        self.reason = reason

    @classmethod
    def deny_all_registry(cls, reason: str = "") -> "DeviceGuardRegistry":
        """全拒注册表：规则文件读不出/解析失败时返回（AF49-R，fail-closed）。

        与「文件不存在（未启用保护）」不同口径：那条返回 None 表示不启用分级；
        这条表示「保护策略不可信，按最严处理」——所有实体命中 Tier-0 必须人审。
        """
        return cls(rules=[], deny_all=True, reason=reason)

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
        if self.deny_all:
            return 0
        best: int | None = None
        for rule in self.rules:
            if self._hit(rule, entity_id):
                best = rule.tier if best is None else min(best, rule.tier)
        return best

    def match_perm(self, entity_id: str) -> str | None:
        """命中规则的最严 perm（`-` 由 tier-0 表达）；未命中返回 None。"""
        if self.deny_all:
            return "-"
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
            self._check_ask_edge_contract(auto, node, out)
            self._check_high_risk_after_suspend(auto, node, out)
            self._check_cancel_branch(auto, node, out)
            self._check_duplicate_edges(auto, node, out)
            # AF-707：边 kind ↔ 节点 kind 契约（build 期即拦，不靠运行期静默终止）
            self._check_edge_kind_contract(auto, node, out)
            self._check_expr(auto, node, out)
            # 命名空间是 `split_namespace` 的硬前提，也是体验者现场（2026-10-28）的静默
            # 假绿源头：`var` 写成裸 entity_id（`sensor.duka_x`）时 `_check_expr` 放行
            # （它只查算子/深度），运行期 if 求值抛 ValueError → `_soft_fail` 走
            # default 边＝"条件不成立" → 实例正常 done、动作永不执行。build 零诊断、
            # 仿真 ok=True——三道关全绿但自动化是死的。编译期就拦在这里。
            self._check_expr_namespaces(auto, node, out)
            self._check_unresolved(auto, node, out)
            self._check_trigger_depth(auto, node, out)
            # AF-125（假绿三族，v1.14.22）：服务词汇此前零校验
            self._check_service_vocab_node(auto, node, out)
            # 参数容器是**闸门口径**：运行期的泛容器遍历腿（版本投影 / 冲突内省 / NL 渲染）
            # 都接了同一份预算，但闸门原本不查，于是"build 放行、运行期内省必失败"的 IR
            # 能进库；而冲突内省失败会被 dispatch 的 `except Exception` 变成降级放行。
            self._check_param_budget(auto, node, out)
            self._check_inbox_payload_len(auto, node, out)
            self._check_vars(auto, node, declared | assigned, out)

        # `_check_expr` / `_check_trigger_depth` 两站是后面所有"实体依赖腿"的**前提**：
        # `auto.reads()` / `trigger_entities()` 会再走一遍 expr 与 trigger 的递归，超预算时
        # 它们抛的是具名异常，而 scan() 的对外契约是"返回诊断"——那些 IR 已经在上面的站落过
        # ERROR 了，下游只许跳过分析，不许把异常换成整次扫描的崩溃（第二十轮 F2 补的接缝，
        # 与 F6 同族：预算装在腿上、没装在 scan() 的每一根腿上）。
        self._check_snapshot(auto, out)
        self._check_shadow(auto, out)
        self._check_low_confidence(auto, out)
        self._check_entry(auto, out)
        # AF-124（假绿三族，v1.14.22）：time 触发 at 校验（自动化级：按节点扫触发器）
        self._check_time_at(auto, out)
        # AF-780（2026-11-14）：`for` 持续条件必须挂在带 `to` 的入口上（运行期只能 fail-closed）
        self._check_for_target(auto, out)
        # AF-786（2026-11-14）：`for`／`wait` 的时长字面量进构建闸——与运行期同一份 parse_duration
        self._check_durations(auto, out)
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
        # P1-2：L2 动作服务端策略表——L2 分档保留（DCD 裁定 20261031·Q2 甲：语义从
        # "运行期问人"改写为「部署期必须显眼 + 必须配 canary 灰度」）。矩阵第 3/4 行：
        # L2 不写 requires_confirm 仍 ERROR；写了不配 canary 仍 ERROR。
        from .af_adapters.base import unrecognized_service as _unrec
        _unrecognized = _unrec(node.action) if node.kind == "do" else None
        if level == "L2":
            if not node.requires_confirm:
                out.diagnostics.append(
                    Diagnostic(
                        "L2_NEEDS_CONFIRM",
                        ERROR,
                        # 两种 L2 要说两种话：真高危域（部署期显眼标记）vs 动作名认不出
                        # （该改动作名）。语义按裁定 20261031 Q2 甲改写：标记只承载
                        # 「部署期必须显眼」，部署审批摘要机提取 L2 列表，运行期不问人。
                        (f"动作 {node.adapter}.{node.action} 属于 L2（门锁/窗帘/空调），"
                         f"必须显式标记 requires_confirm=true（部署期显眼要求，IR §8.1）"
                         if _unrecognized is None else
                         f"{_unrecognized}；因此按保守档要求标记——"
                         f"⛔ 补 requires_confirm 不是修法，改动作名才是："
                         f"HA 服务写 `<domain>.<service>`（如 switch.turn_off），"
                         f"或用意图别名 开灯/关灯/开空调/关空调"),
                        auto.id,
                        node.id,
                    )
                )
            elif not node.canary:
                # P1-2：requires_confirm=true 但无 canary → 免费豁免，服务端策略表拒绝
                out.diagnostics.append(
                    Diagnostic(
                        "L2_NEEDS_CANARY",
                        ERROR,
                        f"动作 {node.adapter}.{node.action} 属于 L2 且 requires_confirm=true，"
                        f"必须配 canary 灰度（duration + auto_rollback），不可仅凭 requires_confirm 豁免（P1-2 服务端策略表）",
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

    # `_check_confirm_exit`（CONFIRM_WITHOUT_DENY_PATH WARN）已随 DCD 裁定 20261031
    # （Q3 甲）整族撤销——运行期确认闸没了，"人答不要/无人应答"这条走向不再存在；
    # 调用点同批删除，CHECKS/CODE_HINT/pin 同批清，防孤儿。

    # C-2 §G.2（设计稿 af-runtime-ask-semantics，PM 裁落点＝af_scanner 新码，
    # ⛔ 不动 af_ir/models.py 的 AskSpec.__post_init__——G.1 契约零变更）：
    # ① 二元 ask（无 ask 对象）必须有 `no` 边（用户明确拒绝的出口），或其 `default`
    #    边指向**非执行**下游（可达集无 do 节点——"听不清不执行"，D.2：unknown 收紧
    #    为 no 后经 {kind,"default"} 选路，default 若直达 do 就是无人值守执行）；
    # ③ 结构化 ask（带 ask 对象）必须有 `then` 边（参数收集的唯一恢复通道，
    #    af_executor.answer_structured 走 then），且 ⛔ 不得有 yes/no 出边——
    #    yes/no 是二元语义，结构化会话被 classify_answer/then 分流，挂着就是死边。
    # ② （带 timeout 须有 on_timeout/default）由既有 MISSING_TIMEOUT_OR_DEFAULT
    #    覆盖（它对所有 ask 无差别要求 on_timeout/default，比 ② 更严）⇒ 不另开码。
    def _check_ask_edge_contract(self, auto: Automation, node: Node, out: ScanResult) -> None:
        if node.kind != "ask":
            return
        edges = auto.outgoing(node.id)
        kinds = {e.kind for e in edges}
        if node.ask is None:
            # ① 二元 ask：拒绝出口必须存在
            if "no" in kinds:
                return
            default_edges = [e for e in edges if e.kind == "default"]
            if default_edges:
                for e in default_edges:
                    for reachable in _reachable(auto, e.to):
                        if reachable.kind == "do":
                            out.diagnostics.append(
                                Diagnostic(
                                    "ASK_BINARY_NO_DENY_PATH",
                                    ERROR,
                                    f"二元 ask 节点 {node.id} 无 `no` 边，且 `default` 分支"
                                    f"可达执行类节点 {reachable.id}（{reachable.action}）——"
                                    f"unknown/听不清会经 default 无人值守执行（D.2 fail-closed 缺口）",
                                    auto.id,
                                    node.id,
                                )
                            )
                return
            out.diagnostics.append(
                Diagnostic(
                    "ASK_BINARY_NO_DENY_PATH",
                    ERROR,
                    f"二元 ask 节点 {node.id} 既没有 `no` 边也没有 `default` 边——"
                    f"用户明确拒绝时无路可走（D.2：听不清不执行）",
                    auto.id,
                    node.id,
                )
            )
            return
        # ③ 结构化 ask：then 必达、yes/no 禁用
        if "then" not in kinds:
            out.diagnostics.append(
                Diagnostic(
                    "ASK_STRUCT_NO_THEN",
                    ERROR,
                    f"结构化 ask 节点 {node.id} 缺 `then` 边——结构化应答经 then 恢复"
                    f"（answer_structured），没有它参数收集永远无法继续",
                    auto.id,
                    node.id,
                )
            )
        bad = sorted(e.kind for e in edges if e.kind in ("yes", "no"))
        if bad:
            out.diagnostics.append(
                Diagnostic(
                    "ASK_STRUCT_NO_THEN",
                    ERROR,
                    f"结构化 ask 节点 {node.id} 带二元出边 {bad}——yes/no 是二元语义，"
                    f"结构化会话经 then 分流，这些边运行期不可达（死边）",
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
    #    只拦 ask 的 on_timeout/default/on_cancel（无人回答时执行高风险）。
    #    裁定 20261031（取消运行期二次授权）收窄：ask 的 yes/no 分支是**有人在环**
    #    的应答路径，L2 动作已在部署审批摘要列明（矩阵行 2，审批前可见），运行期
    #    不再二次问人 ⇒ 移出本检查射程。P1-3 把 yes/no 纳入是裁定 20261009 运行期
    #    二次授权闸的静态影子，随卡 B 一并收回。L3 不受影响：L3_ACTION 默认拒绝
    #    在一切分支独立生效。
    #    Chief 阻断项（2026-11-01，撤闸后果面）：canary 观察窗是撤闸后运行期**仅剩
    #    的挂起**（af_executor._do 挂 canary_observe，到期 resume("on_timeout")，恢复
    #    处理完漂移检查后按 {kind,"default"} 选边继续 run）——而 is_suspending 只认
    #    ask/wait，带 canary 的 do 节点此前漏在射程外。撤闸前 L2 do 挂 confirm 必被
    #    运行期闸拦下；撤闸后该拦截消失 ⇒ canary 窗到期无人值守继续执行下游 L2 成为
    #    **实际可达**通道。修法：带 canary 的 do 节点视同挂起点，其 on_timeout/default
    #    分支纳入射程；⛔ 不动 canary 本身（Q2 甲保留），只补静态闸射程。
    def _check_high_risk_after_suspend(self, auto: Automation, node: Node, out: ScanResult) -> None:
        suspending = node.is_suspending or node.canary is not None
        if not suspending:
            return
        if node.kind == "wait":
            edge_kinds = ("on_timeout", "default", "on_cancel")
        elif node.kind == "ask":
            edge_kinds = ("on_timeout", "default", "on_cancel")
        elif node.kind == "do" and node.canary is not None:
            # canary 窗到期 = resume("on_timeout")；恢复后按 {kind,"default"} 选边 ⇒
            # 无人值守可达面就这两条（then/on_cancel 不属无人值守恢复面）。
            edge_kinds = ("on_timeout", "default")
        else:
            edge_kinds = ("on_timeout", "default", "on_cancel", "then")
        for edge in auto.outgoing(node.id):
            if edge.kind not in edge_kinds:
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

    # AF-707（假绿族，2026-10-31 PM 裁定补码）：边 kind ↔ 节点 kind 契约。
    # 病灶（台账 AF-707 行，证据 audit_mcp/study_ac_edgekind_proof_20261031.json）：
    # if 节点真值分支的出边 kind 写成 `yes` ⇒ 执行器 pick_edge 匹配不上 ⇒ 实例
    # 静默终止、终态报 done、report 全 inferred，而 build 与 sim 零报错——
    # 自动化看起来部署成功，实际一件事没干。
    #
    # 集合口径（**现读代码定，注释不立法**）：on/set/do 真值面只发 {"then"}；
    # if 发 {"then"} | {"no","default"}；do 失败兜底走 on_error/default（_do 的
    # 返回面，af_executor.py:_execute/_do）；ask/wait 由 resume(kind) 唤醒，
    # classify_answer 只产 yes/no，超时/取消/软失效走 on_timeout/on_cancel/on_error，
    # 结构化应答走 then——resume 侧 `pick_edge(node.id, {kind, "default"})`。
    #: 每种节点 kind 允许的出边 kind 集合（EDGE_KINDS 全集的真子集判定用）。
    #: ClassVar：dataclass 不把它当字段（PM 抓的 import 炸：dict 默认值不能做字段）。
    ALLOWED_OUT_EDGE_KINDS: ClassVar[dict[str, frozenset[str]]] = {
        # 口径＝执行器现读（依据逐条钉在下面，PM 复核）：`_execute` 返回 {"then"}（on/set）、
        # {"then"} / {"no","default"}（if）；`_do` 失败走 on_error/default
        # （af_executor.py:809-813）；`_if_eval_fail` 只认显式 on_error 边
        # （af_executor.py:815-823）；ask 的 resume 走 yes/no/then/default/on_timeout/
        # on_cancel；wait 走 then/default/on_timeout/on_cancel。
        # ⛔ on_cancel 对**任何**节点 kind 都合法：中断路径 cancel() → resume("on_cancel")
        # 是全局的（af_executor.py:610-624），不限节点类型。
        # ⛔ do 额外允许 on_timeout：canary 观察期到期正是 resume("on_timeout")
        # （af_executor.py:270-271），do 节点带 on_timeout 是仓内既有正例形状
        # （书房 v2 三个 L2 节点均带 on_timeout→p1）——漏它会把正例判死。
        # ⛔ yes/no 只属于 ask：if 的真分支必须是 then（AF-707 假绿族的核心，勿放宽）。
        "on": frozenset({"then", "on_error", "on_cancel"}),
        "if": frozenset({"then", "no", "default", "on_error", "on_cancel"}),
        "set": frozenset({"then", "on_error", "on_cancel"}),
        "do": frozenset({"then", "on_error", "default", "on_timeout", "on_cancel"}),
        "ask": frozenset({"yes", "no", "then", "default", "on_timeout", "on_cancel", "on_error"}),
        "wait": frozenset({"then", "default", "on_timeout", "on_cancel", "on_error"}),
        # pass 允许集**以仓内正例为准**（PM 抓的误判死）：case_lamp_sync 等正例
        # 的广播 pass 节点带 then 出边——空集会把仓自己的正例判死。留 then/on_error；
        # 别的 kind（如 no）出现在 pass 出边上时才报。
        "pass": frozenset({"then", "on_error", "on_cancel"}),
    }

    def _check_edge_kind_contract(self, auto: Automation, node: Node, out: ScanResult) -> None:
        allowed = self.ALLOWED_OUT_EDGE_KINDS.get(node.kind)
        if allowed is None:  # 未知节点 kind 由别的腿报（不在这里重复）
            return
        for edge in auto.outgoing(node.id):
            if edge.kind not in allowed:
                out.diagnostics.append(
                    Diagnostic(
                        "EDGE_KIND_MISMATCH",
                        ERROR,
                        f"节点 {node.id}（kind={node.kind}）的出边 kind={edge.kind!r} 不在允许集合 "
                        f"{sorted(allowed)}——运行期 pick_edge 永远选不中这条边，实例会在该节点静默终止",
                        auto.id,
                        node.id,
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
        try:
            refs = collect_var_refs(node.expr)
        except ExprError:
            # 深度/规模预算在 _check_expr 那一站已经记成 EXPR_INVALID；这里重复走只会
            # 把同一个坏表达式再遍历一遍。顺序（_check_expr 先跑）是这条分支的前提。
            return
        for ref in refs:
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

    # 未解析占位符（体验者 AF-013：`?书房不存在的实体` 一路过闸，最后只在依赖环那一站
    # 被动露出，报的还是「跨自动化成环」——既误导排查，又让测试通道成了橡皮图章）
    def _check_unresolved(self, auto: Automation, node: Node, out: ScanResult) -> None:
        refs: set[str] = set()
        if node.trigger is not None:
            try:
                refs |= node.trigger.entity_ids()
            except (TriggerDepthError, ParamDepthError):
                pass  # 深度/预算由 _check_trigger_depth 那一站负责，这里不重复报
        for eid in (node.params.get("entity_id"), node.params.get("target")):
            if isinstance(eid, str):
                refs.add(eid)
        if node.expr is not None:
            try:
                refs |= {r for r in collect_var_refs(node.expr) if r.startswith("entity.")}
            except ExprError:
                pass
        for ref in sorted(r for r in refs if "?" in r):
            out.diagnostics.append(
                Diagnostic(
                    "UNRESOLVED_ENTITY",
                    ERROR,
                    f"实体引用仍是未解析占位符 {ref!r}：目录里查不到这个实体，"
                    f"先用 af_resolve_entity 拿真实 entity_id（多候选要人工选定）",
                    auto.id,
                    node.id,
                )
            )

    # 变量命名空间（体验者 2026-10-28：裸 entity_id 写法运行期被吞成"条件假"→ 假绿死图）
    def _check_expr_namespaces(self, auto: Automation, node: Node, out: ScanResult) -> None:
        if node.expr is None:
            return
        try:
            refs = collect_var_refs(node.expr)
        except ExprError:
            return  # 深度/规模超支：_check_expr 已记 EXPR_INVALID，这里不重复遍历坏表达式
        for ref in sorted(refs):
            ns = ref.split(".", 1)[0]
            if ns in ("entity", "vars", "context"):
                continue
            hint = ""
            if re.match(r"^[a-z_][a-z0-9_]*\.[a-z0-9_]+$", ref):
                hint = f"——形如 entity_id，请写 entity.{ref}"
            out.diagnostics.append(
                Diagnostic(
                    "EXPR_NS_INVALID",
                    ERROR,
                    f"变量 `{{\"var\": \"{ref}\"}}` 的命名空间 {ns!r} 非法"
                    f"（只允许 entity/vars/context）{hint}",
                    auto.id,
                    node.id,
                )
            )

    # trigger group 深度 / 自引用（与 `_check_expr` 同一族：诊断在此落，下游只许跳过）
    def _check_trigger_depth(self, auto: Automation, node: Node, out: ScanResult) -> None:
        tr = node.trigger
        if tr is None:
            return
        try:
            tr.entity_ids()
        except TriggerDepthError as exc:
            out.diagnostics.append(
                Diagnostic(
                    "TRIGGER_INVALID",
                    ERROR,
                    f"触发源校验失败：{exc}",
                    auto.id,
                    node.id,
                )
            )

    # AF-124（假绿三族）：time 触发的 at 从来没人校验——`25:00` 一路绿灯到归档。
    # AF-780（2026-11-14）：口径收到**与运行期 `matches_at` 逐字同一份**——只认 `HH:MM`。
    # 修前这里还放行 `HH:MM:SS` 与 `sunrise/sunset±offset`，可运行期 `_fire_time_triggers`
    # 比的是 `f"{now.hour:02d}:{now.minute:02d}"`：`07:30:00` 永远 != `07:30`、`sunrise`
    # 更永远 != 任何钟点 ⇒ **build 绿、仿真绿、真机上这条自动化一次都不会触发**。
    # 日出日落本来就有专门的 `type: "sun"` 触发器（带 offset），不该塞进 time.at。
    # ⛔ 放宽这条等于把「闸门口径 ≠ 运行期口径」这个假绿通道重新打开。
    _TIME_AT_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")

    def _check_time_at(self, auto: Automation, out: ScanResult) -> None:
        for node in auto.nodes.values():
            tr = getattr(node, "trigger", None)
            if tr is None or getattr(tr, "type", "") != "time":
                continue
            at = str(getattr(tr, "at", "") or "").strip()
            if not at:
                # 缺 at 已有 TRIGGER_INVALID 家族管……吗？不——那是 depth 检查。
                # 缺 at 是「时间触发没时间」，也在这条报（同一枚码，同一族假绿）
                out.diagnostics.append(
                    Diagnostic(
                        "TIME_AT_INVALID",
                        ERROR,
                        f"节点 {node.id} 的 time 触发缺 at 字段：时间触发器没有触发时刻，永远不会触发",
                        auto.id,
                        node.id,
                    )
                )
                continue
            if not self._TIME_AT_RE.match(at):
                out.diagnostics.append(
                    Diagnostic(
                        "TIME_AT_INVALID",
                        ERROR,
                        f"节点 {node.id} 的 time 触发 at={at!r} 不是合法时刻"
                        f"（只能是 HH:MM，00-23:00-59）——运行期 `matches_at` 只认 HH:MM，"
                        f"秒格式/sunrise/sunset 在这里 build 绿、真机永远不触发；"
                        f"日出日落请改用 `type: \"sun\"` 触发器的 offset",
                        auto.id,
                        node.id,
                    )
                )

    def _check_for_target(self, auto: Automation, out: ScanResult) -> None:
        """AF-780（2026-11-14）：`for` 持续条件必须挂在**带 `to` 的入口**上。

        运行期口径（`Scheduler._still_holds`）：`pending.to is None` ⇒ 直接 `False`
        （fail-closed，P1-7 明写"无明确目标态的 for 条件不可靠"）。而 `node.trigger.to`
        只有**单条 state 触发器**才可能有值：
        - `group` 型入口（and/or）没有 `to` 字段 ⇒ 挂 `for` 后条件恒不成立；
        - 变化型 state（既不写 `to` 也不写 `from`）同样没有目标态；
        ⇒ 这两种写法 build 全绿、真机上"等 10 分钟再动"永远不执行。这里在编译期拦下。
        """
        for node in auto.entry_nodes():
            if not node.for_:
                continue
            trig = getattr(node, "trigger", None)
            ttype = str(getattr(trig, "type", "") or "")
            to = getattr(trig, "to", None)
            if ttype == "group" or (ttype == "state" and not to):
                out.diagnostics.append(
                    Diagnostic(
                        "FOR_WITHOUT_TO",
                        ERROR,
                        f"节点 {node.id} 的入口触发（type={ttype or '∅'}）没有 `to`，"
                        f"却挂了 for={node.for_!r}——运行期 `_still_holds` 对无目标态的持续"
                        f"条件只能 fail-closed，这段等待永远不会成立"
                        f"（给 state 入口补 to，或把 for 挪到带 to 的入口）",
                        auto.id,
                        node.id,
                    )
                )

    # AF-786（2026-11-14）：时长字面量进构建闸——**闸门口径就是运行期的那个解析器本身**。
    #
    # 运行期口径（`af_time.parse_duration`，P1-9 起 fail-closed）：负值／inf／nan／空串／
    # `HH:MM:SS` 里非数字一律抛 `ValueError`（`-00:10:00` 在 AF-775 之前还会静默成 600 秒）。
    # 闸门此前**一枚时长字段都不查**，于是两条只在真机上才露的通道一直开着：
    #   · `for`／`debounce` 畸形 ⇒ `Scheduler._update_pending`／`_try_fire` 在 `handle_event`
    #     里直接抛（那两行都不在 `try` 里）⇒ **整轮事件分发停摆**：不是这一条自动化失败，
    #     是同一轮里排在它后面的每一条自动化都不再被处理；
    #   · `wait`／`ask.timeout` 畸形 ⇒ 执行到该节点时 `_suspend` 接住异常走 `_soft_fail`，
    #     实例静默走 on_error/default ⇒ build 绿、仿真绿、真机上这一段永远不发生。
    # ⇒ 不在闸门里另写一份「看起来合法」的正则（AF-780 的 `time.at` 就是这么造出假绿的），
    #   直接调用同一个 `parse_duration`：它抛，就落 `DURATION_INVALID`（ERROR）。
    #
    # 射程（2026-11-14 定，⛔ 别随手加减）：`for`／`debounce`（入口）／`wait.duration`／
    # `ask.timeout`／`emit.delay`——这五格都是「运行期同一个解析器抛、且抛点没有具名码兜底」。
    # 为什么不含另外两格：`canary.duration` 有 `CANARY_DURATION_PARSE_FAILED` 具名码＋
    # 「按无观察期处理」的既裁降级（那是语义选择，不是漏检）；`sun.offset` 走
    # `af_nl_parse` 的入向解析、不在运行期调 `parse_duration`。
    def _check_durations(self, auto: Automation, out: ScanResult) -> None:
        for node in auto.entry_nodes():
            self._duration_diag(auto, node, "for", node.for_, out)
            self._duration_diag(auto, node, "debounce", node.debounce, out)
        for node in auto.nodes.values():
            if node.kind == "wait":
                self._duration_diag(auto, node, "wait", node.duration, out)
            elif node.kind == "ask":
                self._duration_diag(auto, node, "timeout", node.timeout, out)
            emit = getattr(node, "emit", None)
            if emit is not None:
                self._duration_diag(auto, node, "emit.delay", getattr(emit, "delay", None), out)

    #: 时长字段 → 不合法时**运行期实际的后果**（诊断正文里点名，别让人自己猜）
    _DURATION_FALLBACK: ClassVar[dict[str, str]] = {
        "for": "`for` 的抛点在 `Scheduler._update_pending`，它会把**整轮事件分发**打断",
        "debounce": "`debounce` 的抛点在 `Scheduler._try_fire`，同样打断**整轮事件分发**",
        "wait": "`wait` 的抛点被 `_suspend` 吞成 `_soft_fail`，实例静默走 on_error/default",
        "timeout": "`timeout` 的抛点被 `_suspend` 吞成 `_soft_fail`，实例静默走 on_error/default",
        "emit.delay": "`emit.delay` 的抛点在 `Executor._emit_node`，会把实例求值段打断",
    }

    def _duration_diag(
        self, auto: Automation, node: Node, field: str, raw: Any, out: ScanResult
    ) -> None:
        """一个时长字段过一遍运行期解析器；过不了就落一条具名诊断（含后果与合法形态）。"""
        if raw in (None, ""):
            return
        try:
            parse_duration(raw)
        except (TypeError, ValueError) as exc:
            out.diagnostics.append(
                Diagnostic(
                    "DURATION_INVALID",
                    ERROR,
                    f"节点 {node.id} 的 {field}={raw!r} 不是合法时长：{exc}"
                    f"——运行期 `parse_duration` 就在这一格抛，"
                    f"{self._DURATION_FALLBACK.get(field, '该节点会在运行期抛异常')}；"
                    f"合法形态：10m／90s／2h／1d／纯数字秒／HA 原生 00:10:00／ISO PT5S，且必须非负",
                    auto.id,
                    node.id,
                )
            )

    # AF-125（假绿三族）：`light.turn_onnn` 实体是真的、服务名是幽灵——实体存在性
    # 校验管不到这一层，build ok + sim ok + 真机 404。判据用 af_affordance 的
    # 域服务表（DOMAIN_AFFORDANCE）+ `set_state`/`toggle` 全域逃生口（fake.py 同口径）。
    def _check_service_vocab_node(self, auto: Automation, node: Node, out: ScanResult) -> None:
        from .af_affordance import DOMAIN_AFFORDANCE
        if node.kind != "do" or not node.action:
            return
        action = str(node.action)
        if action.count(".") != 1:
            return  # 非 domain.service 形状（automation.trigger 等另有分类管）
        domain, service = action.split(".", 1)
        if service in ("set_state", "toggle"):
            return  # 全域逃生口（状态提供者接口 / 通用翻转）
        spec = DOMAIN_AFFORDANCE.get(domain)
        if spec is None:
            return  # 未登记域不猜词汇（affordance_for 同口径：服务表请查 attributes）
        if service not in (spec.get("services") or {}):
            out.diagnostics.append(
                Diagnostic(
                    "SERVICE_UNKNOWN",
                    ERROR,
                    f"动作 {action!r} 的服务 {service!r} 不在 {domain!r} 域的服务表里"
                    f"（该域已知：{sorted((spec.get('services') or {}).keys())[:8]}…）"
                    f"——build/sim 双绿但真机必 404",
                    auto.id,
                    node.id,
                )
            )

    # 入口节点：一条自动化必须能被触发（2026-10-17 敲打 N02）
    def _check_entry(self, auto: Automation, out: ScanResult) -> None:
        if not auto.entry_nodes():
            out.diagnostics.append(
                Diagnostic(
                    "NO_ENTRY_NODE",
                    ERROR,
                    f"自动化 `{auto.id}` 没有任何 kind=on 的触发节点（state/sun/time/event/group），"
                    "它永远不会被触发。",
                    auto.id,
                    None,
                )
            )

    # ① b params 容器预算（闸门侧，见 `_scan_automation` 里的说明）
    def _check_param_budget(self, auto: Automation, node: Node, out: ScanResult) -> None:
        if not node.params:
            return
        try:
            assert_param_budget(node.params, "scanner.node.params")
        except ParamDepthError as exc:
            out.diagnostics.append(
                Diagnostic(
                    "PARAMS_TOO_DEEP",
                    ERROR,
                    f"节点 {node.id} 的动作参数容器超限：{exc}",
                    auto.id,
                    node.id,
                )
            )

    # §十八 残余 B.9 的后半格：收件箱载荷**超长**在编译期先拒（前半格"长度由库侧把"已交付）。
    # 级别取 ERROR 的理由与 L2_NEEDS_CANARY 同族：契约 §1.3 已写明 DB 对超长载荷 fail-closed 丢弃，
    # 这条动作**发出去也必死**——等到运行期第一次真投递才红，等于把一条永远送不到的 IR 放进了库、
    # 放进了演练台账。上限不在本文件写数字：`inbox_overlong_fields` 现读库侧
    # `homesdk.presence.INBOX_MAX_*`，判超长的数与运行期拒超长的数是同一个数。
    def _check_inbox_payload_len(self, auto: Automation, node: Node, out: ScanResult) -> None:
        if node.kind != "do" or node.adapter != "inbox":
            return
        for kind, field, actual, limit in inbox_overlong_fields(node.action or "", node.params):
            out.diagnostics.append(
                Diagnostic(
                    "INBOX_PARAM_TOO_LONG",
                    ERROR,
                    f"收件箱动作 `{node.action}` 的载荷字段 `{field}` 长 {actual} 字，"
                    f"超过 butler/inbox/{kind} 的库侧上限 {limit} 字：DB 侧对超长载荷 fail-closed 丢弃，"
                    f"这条投递永远到不了音箱",
                    auto.id,
                    node.id,
                )
            )

    # 坏 IR（超预算表达式 / 触发源）的诊断已由上面两站各落一次 ERROR。
    # `reads()` 会下钻 expr 与 trigger，分析不动时按"这条腿看不到实体"处理，
    # `writes()` 不碰那两条腿、照常分析——把异常抛出去等于整次扫描换成崩溃，
    # 而 scan() 的对外契约是"返回 ScanResult"。
    def _entity_refs(self, auto: Automation) -> tuple[set[str], set[str]]:
        try:
            reads = auto.reads()
        except (ExprError, TriggerDepthError):
            reads = set()
        return reads, auto.writes()

    def _trigger_refs(self, auto: Automation) -> set[str]:
        try:
            return auto.trigger_entities()
        except TriggerDepthError:
            return set()

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
            # 直接交给 _triggers_node：旧形状在这里抄了一份 group 分支，
            # 两处各自递归 ⇒ F6 的预算只能装在一条腿上。
            tr = node.trigger
            if tr is None:
                return
            yield from _triggers_node(tr, 0)

        def _triggers_node(tr, _depth: int = 0):
            # F6：group 递归走者之一（第十七轮实测的 cyclic_crash 站点），预算与 IR 层同源。
            if tr is None:
                return
            check_trigger_depth(_depth, "scanner._triggers_node")
            if getattr(tr, "type", "") == "group":
                for sub in (tr.sources or []):
                    yield from _triggers_node(sub, _depth + 1)
            elif getattr(tr, "entity_id", ""):
                yield tr.entity_id

        for node in auto.entry_nodes():
            try:
                # 生成器要在这里才被真正耗尽：超预算/自引用的 group 在这条腿上受控失败，
                # 诊断已由 `_check_trigger_depth` 落过（TRIGGER_INVALID），本腿只跳过。
                entity_ids = list(_triggers(node))
            except TriggerDepthError:
                continue
            for entity_id in entity_ids:
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

    def _event_bus_subjects(self, auto: Automation) -> set[str]:
        """本自动化由 **`event` 触发源产生的**总线主体名集合（AF-722 丙案单源）。

        仓内权威依据：`Trigger.entity_ids()` docstring（af_ir/models.py）——
        「`event` 类型**不是实体**，但在总线中的主体为 `event.<name>`，一并返回——
        这样跨自动化依赖矩阵与 `EMIT_SELF_LOOP` 环检测能覆盖事件边」⇒ 它本就不该
        进**设备目录存在性比对**（判别域不重叠，不是豁免）。判别依据＝「这个名字
        是否由 `type:"event"` 触发源产生」，⛔ 不是「长得像 `event.*`」（那会把
        HA 真实 `event` 域实体与 state 触发里的 `event.*` 引用一起放过，PM 两树
        探针坐实的漏报面）、⛔ 不是「图里 emit 过没有」（线上单条归档 `_load_ir`
        只扫本次提交那条，图级集合对它恒空）、⛔ 不是「目录里有没有」（随目录
        两态漂）。group 触发走 `leaf_triggers()` 展开（深度预算同源），⛔ 手抄。
        """
        out: set[str] = set()
        for node in auto.nodes.values():
            trigger = getattr(node, "trigger", None)
            if trigger is None:
                continue
            try:
                # 生成器要在这里才被真正耗尽：超预算/自引用的 group 触发在这条腿上
                # 受控失败（`TriggerDepthError`）——scan() 的对外契约是「返回诊断」，
                # 不许把异常换成整次扫描的崩溃（与 `_entity_refs`／`_trigger_refs`
                # 同族口径）。诊断已由 `_check_trigger_depth` 落过（TRIGGER_INVALID），
                # 本腿只跳过该节点，不影响其余节点的剔除集合。
                leaves = list(trigger.leaf_triggers())
            except TriggerDepthError:
                continue
            for leaf in leaves:
                if leaf.type == "event" and leaf.event:
                    out.add(f"{EVENT_ENTITY_PREFIX}{leaf.event}")
        return out

    # ⑫ 实体存在性 + ① 设备保护分级（v1.4.0 tier 模型）
    def _check_entities(self, auto: Automation, out: ScanResult) -> None:
        reads, writes = self._entity_refs(auto)
        refs = reads | writes
        # AF-722（2026-11-01 PM 实测，🔴 归档面）：`event` 触发的**总线主体名**
        # （`event.<name>`）经 `Trigger.entity_ids()` 混进 `reads()`——那是为依赖
        # 矩阵与环检测覆盖**事件边**而设的语义（af_ir docstring 原文，⛔ 它不改），
        # 不是 HA 实体；拿它比**设备目录**（只含 HA 实体）必然 `ENTITY_NOT_FOUND`
        # （ERROR ⇒ 硬拒归档），仓内在册正例 case_lamp_sync 四枚 event 触发全被拦。
        # 丙案（PM 裁定 `plans/AF_裁定_AF722丙案_event触发源产生_20261101.md`）：
        # 剔除集合＝**由 `event` 触发源产生的**总线主体名（单源 `_event_bus_subjects`，
        # 判别域＝「event 不是实体」的定义，⛔ 不是「图里 emit 过」/「按前缀」/
        # 「目录里有没有」三版——前两版分别打不穿单条归档路径与放走真实域实体，
        # 探针坐实）；其余（state 触发/do 参数里的 `event.*` 字面）⇒ 照常参与
        # 实体校验（⛔ 不放宽任何真实体校验）。环检测那半边仍用未过滤 reads()
        # （EMIT_SELF_LOOP 照旧，本函数不动它）。
        refs = {r for r in refs if r not in self._event_bus_subjects(auto)}

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
        elif refs:
            # AF-123（假绿三族，v1.14.22）：目录为空 ⇒ 存在性校验静默退化 skipped。
            # 「没刷过目录」与「实体全不存在」在回执里只有一个 entity_check=skipped 字样、
            # 零诊断——审计拿幽灵实体连过 40 问就是这么来的。退化必须显式标注，
            # 不许装作校验过（不拦截：空目录是合法首建态，fail-open 但要说话）。
            out.diagnostics.append(
                Diagnostic(
                    "ENTITY_CHECK_DEGRADED",
                    WARNING,
                    f"自动化 `{auto.id}` 引用了 {len(refs)} 个实体但设备目录为空，"
                    f"存在性校验被跳过（entity_check=skipped）——幽灵实体检测不到，"
                    f"先 af_catalog 刷新目录再 build",
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
        # AF49-R：deny_all 注册表（规则文件读不出）命中时，把"按最严处理"的原因带进
        # 诊断消息——否则运维看到的是「实体 X 属 Tier-0 保护区」，会去查哪条规则命中了，
        # 而实际是 device_acl.json 整个没读出来。
        deny_reason = (
            f"（原因：{self._guard.reason}，已按最严处理）"
            if self._guard.deny_all and self._guard.reason else ""
        )
        for entity_id in sorted(reads):
            if self._guard.match_tier(entity_id) == 0:
                out.diagnostics.append(
                    Diagnostic(
                        "ENTITY_GUARD_TIER0",
                        ERROR,
                        f"实体 {entity_id} 属 Tier-0 保护区，读取须经人工审批{deny_reason}",
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
                            f"实体 {entity_id} 属 Tier-0 保护区，写入须经人工审批{deny_reason}",
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

        reads, writes = self._entity_refs(auto)
        reachable = reads | writes
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
        try:
            result = render_automation(auto)
        except (ExprError, TriggerDepthError):
            # 渲染器走的是同一份 expr/trigger 递归，超预算时它给不出句子；这种 IR 已经被
            # `_check_expr` / `_check_trigger_depth` 判红，这里不再补一条重复诊断，
            # 也不许把异常抛出去（那会让整次扫描变成崩溃）。
            return
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
        # 触发源实体先按自动化各算一次：既省掉重复递归，又让超预算的 trigger 只让**它自己**
        # 那一条依赖边看不见，而不是把整次跨自动化分析换成异常（`_check_trigger_depth`
        # 已经给坏 IR 落过 TRIGGER_INVALID）。
        trig_refs = {a.id: self._trigger_refs(a) for a in autos}
        # A 写 X 且 B 读 X → A→B 依赖边
        deps: dict[str, set[str]] = {a.id: set() for a in autos}
        for a in autos:
            writes = a.writes()
            if not writes:
                continue
            for b in autos:
                # 只看**触发源**依赖：A 写 X 且 B 由 X 触发 → A→B（含自环，自环是真死循环）
                if writes & trig_refs[b.id]:
                    deps[a.id].add(b.id)

        # v0.3.0 发布侧：emit 事件成环——A 发出 `event.X`，B 由 `event.X` 触发 → A→B（含自环）
        emit_deps: dict[str, set[str]] = {a.id: set() for a in autos}
        for a in autos:
            # AF-722 单源：emit 主体名拼装走 `_emitted_bus_subjects` 同一份取法——
            # 这里需要**逐自动化**的 emitted（A 自己发的 ∩ B 触发 = A→B 边），
            # 从图级单源退不出 per-auto，故按同一前缀拼装规则在此取 per-auto 面。
            emitted = {f"{EVENT_ENTITY_PREFIX}{e}" for e in a.emitted_events()}
            if not emitted:
                continue
            for b in autos:
                if emitted & trig_refs[b.id]:
                    emit_deps[a.id].add(b.id)
        # P1-4 收口：实体依赖与事件依赖 union 后统一查环，避免跨图环漏检
        # （A 写实体+emit -> B 触发 -> B 写同一实体，在两张图里各自都不是环，但 union 后是环）
        full_deps: dict[str, set[str]] = {a.id: set() for a in autos}
        for aid in full_deps:
            full_deps[aid] = deps.get(aid, set()) | emit_deps.get(aid, set())
        for cycle in _cycles_in(full_deps):
            has_entity_edge = any(
                b in deps.get(a, set())
                for a, b in zip(cycle, cycle[1:] + [cycle[0]])
            )
            has_emit_edge = any(
                b in emit_deps.get(a, set())
                for a, b in zip(cycle, cycle[1:] + [cycle[0]])
            )
            if has_entity_edge and has_emit_edge:
                # 混合环：环上既有「实体写」边又有「emit 事件」边。
                # emit 解耦侧把状态变化只广播事件（不写实体），跟随侧只写对方实体，
                # 运行时靠 HA 状态收敛（已是该状态不产生 state_changed）+ 总线熔断避免死循环，
                # 属良性双向同步模式（见 examples/ir/case_lamp_sync.json）。
                # 放行（仅告警，不阻断 forge build）。
                code = "CROSS_DEP_CYCLE"
                msg = "跨自动化混合依赖成环（实体写+事件发布，含 emit 解耦）：" + " → ".join(cycle) + "；运行时靠状态收敛安全，已放行"
                level = WARNING
            elif has_emit_edge:
                # 纯事件环：仅 emit 事件、无设备回灌，运行时安全，放行（告警）。
                code = "EMIT_SELF_LOOP"
                msg = "跨自动化事件发布成环（仅事件、无设备回灌）：" + " → ".join(cycle) + "；已放行"
                level = WARNING
            elif len(set(cycle)) <= 1:
                # 体验者 AF-011：单自动化"触发源 = 自己写的实体"被报成 error 级
                # **「跨自动化」**成环、链路写成 `probe1 → probe1`——文案与判级都不实。
                # 这条边能不能真自激，取决于"写入结果状态是否重新满足自己的触发目标"：
                #   触发 to=on + turn_off（写 off）⇒ 不会再触发，是合法的自动关停写法；
                #   触发 to=on + turn_on（写 on）⇒ 状态回灌，真自激风险。
                # 因此降级为 WARNING 并如实写"同自动化"，风险细节由 proof 给出。
                code = "SELF_TRIGGER_LOOP"
                proof = _self_oscillation_proof(next(a for a in autos if a.id == cycle[0]))
                msg = (
                    f"同自动化写回自己的触发源（非跨自动化）：{cycle[0]} → {cycle[0]}"
                    + (f"；{proof}" if proof else "；写入结果状态不重新满足触发目标，不构成自激")
                )
                level = WARNING
            else:
                # 纯实体环：无 emit 解耦，多自动化直接互写同一实体，无状态收敛保障，
                # 静态保守拦截（code hint 引导改用 emit 解耦写法）。
                code = "ENTITY_DEP_CYCLE"
                msg = "跨自动化实体读写依赖成环（无 emit 解耦，直接互写）：" + " → ".join(cycle)
                level = ERROR
            out.diagnostics.append(
                Diagnostic(code, level, msg, cycle[0] if cycle else "")
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
    """找图里的环（DFS 回边，三色标记）。

    R3-01 修复：递归 DFS 改迭代——合法大图（≳950 节点链）会让递归深度超过
    Python 默认限制（1000），静态扫描器直接 RecursionError 崩溃，无法归档。
    迭代版本无递归深度限制。
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in auto.nodes}
    cycles: list[list[str]] = []

    for start in auto.nodes:
        if color[start] != WHITE:
            continue
        color[start] = GRAY
        path: list[str] = [start]
        # 迭代栈：(node_id, outgoing_edges_iterator)
        stack: list[tuple[str, object]] = [(start, iter(auto.outgoing(start)))]
        while stack:
            node, edges = stack[-1]
            try:
                edge = next(edges)
            except StopIteration:
                path.pop()
                color[node] = BLACK
                stack.pop()
                continue
            nxt = edge.to
            if nxt not in color:
                continue
            if color[nxt] == GRAY:
                cycles.append(path[path.index(nxt):] + [nxt])
            elif color[nxt] == WHITE:
                color[nxt] = GRAY
                path.append(nxt)
                stack.append((nxt, iter(auto.outgoing(nxt))))
    return cycles


def _self_oscillation_proof(auto: Automation) -> str:
    """同自动化自激证明：写入结果状态（`SERVICE_STATE` 单一真值源）是否等于触发目标。

    返回人类可读结论；查不到效果（非 turn_* 类服务）时如实说"无法判定"，
    ⛔ 不假装"一定安全"也不假装"一定危险"。
    """
    from .af_vhass.fake import SERVICE_STATE  # 效果真值表：vhass/FakeHA/这里共用一份

    watched: dict[str, set[str | None]] = {}
    for node in auto.entry_nodes():
        if node.trigger is None:
            continue
        try:
            leaves = list(node.trigger.leaf_triggers())
        except (TriggerDepthError, ParamDepthError):
            leaves = []
        for leaf in leaves:
            if leaf.entity_id:
                watched.setdefault(leaf.entity_id, set()).add(leaf.to)
    risky: list[str] = []
    unknown: list[str] = []
    for node in auto.nodes.values():
        if node.kind != "do" or not node.action:
            continue
        eid = str(node.params.get("entity_id") or "")
        if not eid or eid not in watched:
            continue
        domain = eid.split(".", 1)[0]
        service = node.action.split(".", 1)[1] if "." in node.action else node.action
        written = SERVICE_STATE.get((domain, service))
        if written is None:
            unknown.append(f"{eid}←{node.action}")
        elif any(to is None or to == written for to in watched[eid]):
            risky.append(f"{eid} 写入后状态={written}，与自身触发目标一致")
    if risky:
        # 措辞要准：写入同一状态在 HA 侧不产生 state_changed（加上总线去重/熔断），
        # 因此不是"必然死循环"，而是"依赖收敛机制的自激形态"——降 warning 与此判断一致。
        return "自激形态（靠状态去重/熔断收敛，仍需复核）：" + "；".join(risky)
    if unknown:
        return "写入结果状态不在效果表内，无法判定是否自激：" + "；".join(unknown)
    return ""


def _cycles_in(deps: Mapping[str, set[str]]) -> list[list[str]]:
    """通用有向图环检测（返回环路径列表）。

    R3-02 修复：递归 DFS 改迭代，防大图 RecursionError 崩溃（同 R3-01）。
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in deps}
    cycles: list[list[str]] = []

    for start in deps:
        if color[start] != WHITE:
            continue
        color[start] = GRAY
        path: list[str] = [start]
        stack: list[tuple[str, object]] = [(start, iter(sorted(deps.get(start, ()))))]
        while stack:
            node, neighbors = stack[-1]
            try:
                nxt = next(neighbors)
            except StopIteration:
                path.pop()
                color[node] = BLACK
                stack.pop()
                continue
            if nxt not in color:
                continue
            if color[nxt] == GRAY:
                cycles.append(path[path.index(nxt):] + [nxt])
            elif color[nxt] == WHITE:
                color[nxt] = GRAY
                path.append(nxt)
                stack.append((nxt, iter(sorted(deps.get(nxt, ())))))
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
                "真机下发需要 HA 长期访问令牌（--ha-token 或环境变量 HA_LONG_LIVED_TOKEN，旧 AUTOFORGE_HA_TOKEN 回退）",
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
