"""MA 取数适配器 —— AF 作为 **MCP 客户端**向 memory-agent 取三路径读数（计划 §七 卡2，链主定档甲）。

计划卡片与 DCD 那张派工单把这一格叫 **`ma_query`**；AF 侧它不是一种节点 kind，而是本适配器
（`do` + `adapter: ma`）。grep `ma_query` 落到本文件就是这一层对应关系，⛔ 不是另立一族。

落形是 `do` + `adapter: ma` + `action: route_question | list_analysis_templates | export_insight |
run_analysis_template`，不是新增一枚节点 kind：AF 的 kind 词表是封闭 8 枚（`af_ir/models.py`
`NODE_KINDS`），未知 kind 在编译期就被 `ir.schema.json` 拒；卡1 的 `inbox_speak` 已经栽过同一格。
设计面（入参/出参绑定规则/失败出口）在 `docs/ADM联动执行计划-AF.md` §7.5，本文件是那一节的落码。

**返回键按 MA 现读，⛔ 不按派工单字面硬套**（定档甲＝AF 按既有形状读、MA 零代码）：
`export_insight` 的三块真身套了一层 ⇒ AF 读 `parsed["insight"]` 之下的 `insight`/`queries`/`implementation`；
`run_analysis_template` 返 `template`/`window`/`entities`/`summary_text`，与"三块"不同形。

**静默空值那一族由本文件挡**（链主 R3 定、DCD 追认的判据）：按顶层读三块**不会崩、只会读空**——
喂 `{"ok": True}`（缺 `insight`）与 `{"ok": True, "insight": {}}`（三块全空）两种形状都抛
`AdapterError`＋`ADM_ERR_PAYLOAD_INVALID`，⛔ 不许返回空 dict 让 `result_var` 静默绑成空值。
上游平铺不是本文件要防的那格（MA 侧契约腿 `tests/contract/test_ma_db_contract.py` 已反向锁住），
这里防的是 **AF 自己读空之后冒充成功**。

`dry_run=True` 是缺省档：只在进程内记一条意图，一个字节都不出网，⛔ 不伪造载荷值——
仿真面拿到的是"这次会调什么、带什么参数"，不是编造出来的读数。

---

**AF×MA 深度联动批一（读）· 三枚只读门面**（DCD 裁定
`E:/NAS/关键决策部/decisions/20261117-链主-AF-MA深度联动预案与契约v1四问-裁定.md`
A＋Q1–Q5＋H1 全甲；契约 `MA-AF联动契约-v1草稿-20261117.md` §二）：

- `get_automation_context`（场景①②）／`get_automation_opportunities`（③前置）／`get_member_habits`（④跨周）；
- **新鲜度闸单点强制**（契约 §2.5）：每读带 `as_of`＋`stale_after_s`，超阈值 ⇒ **⛔ 采信该条件**、
  按**事实**返回降级标记并留痕（不是 error）；阈值配置**只许向下收紧**（放宽即构造期抛）；
- **数据不足与陈旧分开**：`DATA_INSUFFICIENT`（200＋flag，或 `data_quality.sufficient=false`，
  或 `member_habits` 的 `insufficient_data`）＝事实降级；陈旧＝另一枚留痕。两者都不是 error。
  `get_member_habits` 三形状分清：`insufficient_data`（样本 < `min_days=3`，裁定 §2.4）
  ≠ `data: []` ＋ `sufficient: true`（真无规律）≠ 正常——⛔ 把 insufficient 当空数据用。
- **错误分类五态双形状**（契约 §2.1「三族」＋预案 §三，⛔ 混判）：分类顺序**先结构化 `error.code`
  再文本前缀**；同码不同形分流——业务 `NOT_FOUND`（成员不存在）≠ 分发 `NOT_FOUND:`（工具未登记，
  契约漂移缺陷，告警＋计数）；业务结构化 `DENIED`（服务端开关）≠ 分发 `DENIED:`（写作用域不足，
  ⛔ 重试、提示换带 write 令牌）。上述两条分发级都走**各自的**具名留痕＋计数，⛔ 不合并成一条告警。
"""

from __future__ import annotations

import json
import logging
import os
import socket
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request

from homesdk.adm.errors import (
    ADM_ERR_AUTH_REQUIRED,
    ADM_ERR_INTERNAL,
    ADM_ERR_PAYLOAD_INVALID,
    ADM_ERR_PEER_OFFLINE,
    ADM_ERR_UPSTREAM_TIMEOUT,
)

from .base import AdapterError, CallResult
from .http import guarded_open, host_of

logger = logging.getLogger(__name__)

__all__ = [
    "DATA_INSUFFICIENT",
    "DEFAULT_MA_TRANSPORT_TIMEOUT",
    "FRESHNESS_BY_READ",
    "FRESHNESS_HABITS_S",
    "FRESHNESS_PREDICTION_S",
    "FRESHNESS_REALTIME_S",
    "MA_ACTIONS",
    "MA_BUSINESS_ERRORS",
    "MA_BUSINESS_ERROR_TO_ADM",
    "MA_ERR_CONTRACT_DRIFT",
    "MA_ERR_SCOPE_DENIED",
    "MAX_RESPONSE_BYTES",
    "MaAdapter",
    "assess_freshness",
    "parse_as_of",
]

#: 传输层套接超时（秒），与 `http.py` 同一口径：**传输层**超时留在适配器内部（不保留会单进程挂死），
#: 业务超时不归这里——IR 里用 `wait` + `on_timeout` 表达，`params` 里写 `timeout` 会被
#: `POLICY_PARAMS` 静态扫描判 `ADAPTER_POLICY_PARAM`。
DEFAULT_MA_TRANSPORT_TIMEOUT = 10.0

#: 单次响应正文上限（字节）。常驻服务里"对端返多大我吞多大"就是无界读，这里封顶并如实报错。
MAX_RESPONSE_BYTES = 2 * 1024 * 1024

#: dry_run 意图环条数上限（与 `http.py`/`inbox.py` 同一纪律：只写不读的日志必须自己封顶）。
INTENTS_MAX = 200

MCP_PROTOCOL_VERSION = "2024-11-05"
CLIENT_NAME = "autoforge"

#: 端点与凭据的读取名。⛔ 不走 `credentials.json` 三段链——那条链的射程只有 HA/API 两枚键
#: （`af_secrets.py` 头部按安全审计 F-11 订正过），其余名字一律 secret 文件 > 环境变量。
MA_ENDPOINT_ENV = "AUTOFORGE_MA_MCP_URL"
MA_TOKEN_SECRET = "AUTOFORGE_MA_MCP_TOKEN"

#: 四条只读取数动作 ⇒ 允许出现在 IR `params` 里的键名（真源＝MA 侧 `tool_schema.TOOL_SPECS`
#: 与 `mcp_server.py` 的四个 `@mcp.tool`）。名单外的键**不透传**：那是"参数未声明却可用"那一族。
#: `entity_id` 是 AF 侧专用键，不转给 MA——它只用来把 `entities[]` 里那一枚实体的标量挑出来
#: （DSL 的 `resolve_var` 只对 Mapping 下钻，列表下标会读成 MISSING，所以选择在适配器内做一次）。
MA_ACTIONS: dict[str, tuple[str, ...]] = {
    "route_question": ("question", "days"),
    "list_analysis_templates": ("category",),
    "export_insight": ("template_id",),
    "run_analysis_template": ("template_id", "days", "start", "end", "include_timeline"),
    # ── AF×MA 深度联动批一（读）三枚门面（DCD 2026-11-17 全甲；契约 §二 2.1/2.2/2.4）──────
    # 参数名单逐字取自契约输入列；⛔ 名单外的键不透传（"参数未声明却可用"那一族）。
    "get_automation_context": ("room", "member_id", "window_minutes"),
    "get_automation_opportunities": ("room", "top_k", "min_samples"),
    "get_member_habits": ("member_id", "room", "entity", "granularity", "weeks"),
    # ── AF×MA 深度联动批二（写，DCD 裁定 Q2＝甲：本版交付、读先写后）────────────────────
    # 载荷逐字按契约 2.3；`user_response` 是**核心字段**（出资人场景③「记录下用户的选择」），
    # ⛔ 不是可选备注——它缺席＝这次回流记不下"用户怎么回的"，等于本工具没达成目的。
    "report_automation_outcome": (
        "rule_ref", "outcome", "at", "automation_decision",
        "user_response", "context_ref", "evidence", "note",
    ),
}
#: 批二写向动作（与只读门面分开：它要吃"写失败不冒执行链"纪律与 staging 守卫）。
WRITE_ACTIONS = frozenset({"report_automation_outcome"})
#: 写向出网 wire 层字符串化（AF-831 附属件，链主 2026-11-21 裁定）：MA 现网 Pydantic 合同把
#: `at`/`automation_decision`/`user_response` 钉成 str、`evidence` 钉成 list[str]，而契约 2.3
#: 语义层是「对象」。裁定＝**语义层按对象**（IR 作者永远写对象），字符串化**收进本适配器出网前
#: 统一做**（⛔ 让每个调用点自适配）。对象 {k: v} → ["k:v", ...]；已是 str 的值不再二次包装。
_WRITE_WIRE_STRINGIFY = frozenset({"at", "automation_decision", "user_response"})
#: 契约 2.3 的 `outcome` 枚举（逐字）。`asked`＝问了没答是**合法态**，⛔ 不许当缺字段。
OUTCOME_VALUES = ("asked", "executed", "skipped", "failed", "false_positive")
#: 契约 2.3 的 `user_response.choice` 四值（逐字）。
USER_RESPONSE_CHOICES = ("accepted", "declined", "modified", "no_response")
#: 契约 2.3 的 `user_response.via` 三值（逐字）。
USER_RESPONSE_VIA = ("voice", "bark", "webui")
#: 批一新增的三枚**只读门面**（与上面四条老动作分开：它们要吃新鲜度闸与数据不足判定）。
FACADE_READS = frozenset(
    {"get_automation_context", "get_automation_opportunities", "get_member_habits"}
)
AF_ONLY_PARAMS = frozenset({"entity_id"})
_REQUIRED_PARAMS = {"route_question": ("question",), "export_insight": ("template_id",),
                    "run_analysis_template": ("template_id",)}

#: 三条动作"成功载荷该长什么样"的键名单，逐枚现读 MA 入库面（`mcp_server.py` 的四个 `@mcp.tool`
#: 与 `templates.py:693-705`）。`route_question` 是 spec 驱动（全仓无 `def route_question`），
#: 计划体键名由 MA 的 `plan.to_dict()` 决定 ⇒ **AF 不手抄它的键名单**，只判"除 `ok`/`error` 之外至少有一格"。
_REQUIRED_KEYS: dict[str, tuple[str, ...]] = {
    "list_analysis_templates": ("templates",),
    "export_insight": ("insight", "queries", "implementation"),
    "run_analysis_template": ("template", "window", "entities", "summary_text"),
    "route_question": (),
    # 门面三枚（批一）：`as_of` 是**新鲜度闸的入口**，缺了就没法判"这份事实新不新" ⇒ 必列。
    # 其余各块按"至少一键在"判（判据不逐键硬要，否则对端多给/少给一键会被判成形状塌）。
    "get_automation_context": ("as_of", "behavior_state", "presence", "data_quality"),
    "get_automation_opportunities": ("as_of", "gaps", "candidates", "data_gate"),
    "get_member_habits": ("as_of", "habits", "data", "sufficient"),
    # 批二写向：回执两键（契约 2.3 输出列）——`staging` 必须是 True，见 `MA_STAGING_VIOLATION` 那条守卫。
    "report_automation_outcome": ("memory_id", "staging"),
}
#: 门面读的**事实块**键（`as_of` 不算事实）：至少一键在——只带 `as_of` 的空壳不算成功
#: （⛔ 不返回空 dict 让 `result_var` 静默绑成空值，与 `_REQUIRED_KEYS` 同一条纪律）。
_FACADE_FACT_KEYS: dict[str, tuple[str, ...]] = {
    "get_automation_context": ("behavior_state", "presence", "prediction", "intent", "data_quality"),
    "get_automation_opportunities": ("gaps", "candidates", "data_gate"),
    "get_member_habits": ("habits", "data", "insufficient_data", "sufficient"),
}
#: "键都在但值全空"也算形状塌的那两条。⛔ 不把真实空答案误判成形状塌：`templates: []` 是
#: "确实没有模板"这个真值，而三块全空／四键全空是"读空之后冒充成功"。
_ALL_EMPTY_REFUSED = frozenset({"export_insight", "run_analysis_template"})
#: 三块在 `parsed["insight"]` 之下（MCP 包装 `{"ok": True, "insight": data}` 套的那一层）。
_NESTED_BLOCK = {"export_insight": "insight"}

# ── 新鲜度闸（契约 §2.5；DCD 裁定：AF 可收紧不可放宽）────────────────────────
#: 契约给的三档建议值（秒）。**这是默认取值**，各读的落点见 `FRESHNESS_BY_READ`。
FRESHNESS_REALTIME_S = 120.0     # 实时条件（在场／行为状态）
FRESHNESS_PREDICTION_S = 900.0   # 预测／意图
FRESHNESS_HABITS_S = 3600.0      # 习惯／机会类
#: 读动作 → 本层阈值。`get_automation_context` 取**最严档**：①②把它当 DSL 条件用，
#: 里面装的 `behavior_state`／`presence` 是实时面；陈旧面误采信＝拿旧事实当真。
#: 机会／习惯两类天然是跨周统计（契约给 3600s）。
FRESHNESS_BY_READ: dict[str, float] = {
    "get_automation_context": FRESHNESS_REALTIME_S,
    "get_automation_opportunities": FRESHNESS_HABITS_S,
    "get_member_habits": FRESHNESS_HABITS_S,
}
#: 载荷里对端给的建议阈值键（契约 §2.5）。取 `min(本层, 对端)`：对端更严就听对端的，
#: 更宽**不放宽**（"AF 可收紧不可放宽"）。
FRESHNESS_FIELD_LIMIT = "stale_after_s"

#: 结构化"数据不足"码（契约 §2.1／§2.4）：**事实降级**，⛔ 不是 error、⛔ 不抛。
DATA_INSUFFICIENT = "DATA_INSUFFICIENT"

#: 业务结构化错误码全集（契约 §2.1 ①～⑥）。⇒ 映射到 AF 既有 `adm.errors` 族：
#: ⛔ 不新造码（分发级那两条见下面两个 AF 码，它们不是业务错误）。
MA_BUSINESS_ERRORS = frozenset(
    {"NOT_FOUND", "INVALID_PARAM", "UPSTREAM_UNAVAILABLE", "DENIED", "RATE_LIMITED", "INTERNAL"}
)
MA_BUSINESS_ERROR_TO_ADM: dict[str, str] = {
    "NOT_FOUND": ADM_ERR_PAYLOAD_INVALID,       # 业务对象不存在＝这次请求指错了目标（可兜底）
    "INVALID_PARAM": ADM_ERR_PAYLOAD_INVALID,
    "UPSTREAM_UNAVAILABLE": ADM_ERR_PEER_OFFLINE,
    "DENIED": ADM_ERR_AUTH_REQUIRED,            # **业务** DENIED（如服务端开关未开），与分发级不同形
    "RATE_LIMITED": ADM_ERR_INTERNAL,           # 目录里没有限流档；如实带 `ma_error_code` 一起回
    "INTERNAL": ADM_ERR_INTERNAL,
}

#: 分发级（`isError=true` ＋ 纯文本前缀）两条各自的 AF 码——**契约漂移缺陷**与**作用域不足**
#: 是两件事，⛔ 不合并成一条告警（预案 §三钉死）：
#: ① `NOT_FOUND:`（工具未登记／名字写错）⇒ 契约漂移，告警＋计数，⛔ 当降级吞；
#: ② `DENIED:`（调用令牌无 write scope）⇒ 权限问题，告警、⛔ 重试，提示换带 write 令牌。
MA_ERR_CONTRACT_DRIFT = "MA_ERR_CONTRACT_DRIFT"
MA_ERR_SCOPE_DENIED = "MA_ERR_SCOPE_DENIED"
DISPATCH_TEXT_ERRORS: dict[str, str] = {
    "NOT_FOUND": MA_ERR_CONTRACT_DRIFT,
    "DENIED": MA_ERR_SCOPE_DENIED,
}

# ── 批二（写）：staging 守卫 ＋ 写失败纪律 ──────────────────────────────────
#: 契约 2.3 钉死 MA 侧**恒落 staging**（`staging: true`）。AF 只发"报告"、⛔ 不选库：
#: 回执里 `staging` 不是 `True` ⇒ 对端把这条写进了非 staging 面 ⇒ **契约漂移缺陷**，
#: 响亮留痕（⛔ 不许当success收下再让石沉大海）。
MA_STAGING_VIOLATION = "MA_STAGING_VIOLATION"
#: 写向失败留痕（预案 §二.3／卡面红线）：自动化本体已完成 ⇒ 写 MA 失败**⛔ 不判自动化失败**，
#: 但必须留痕＋计数（与桥"发布失败不冒执行链、假绿即缺陷"同族）。⛔ 不重试。
MA_OUTCOME_REPORT_FAILED = "MA_OUTCOME_REPORT_FAILED"


class _UpstreamFailure(Exception):
    """取数没成功（通道/鉴权/对端层面），携带 `adm.errors` 的具名码。

    私有异常：它在本模块内就被映射成 `CallResult.fail`，⛔ 不冒到执行器——
    "MA 没答上"是可兜底的业务结果，走 IR 的 `on_error`；只有**读空冒充成功**才抛 `AdapterError`。
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def parse_as_of(raw: Any) -> datetime | None:
    """把门面返回的 `as_of` 解析成**带时区**的时刻；解不出一律 `None`（⛔ 不猜）。

    - 支持 ISO-8601（含结尾 `Z`）与 epoch 秒（int/float）；
    - **裸时刻（无偏移）按家庭墙钟解释**（`af_time.load_tz()`）。这条方向是刻意选的：
      若对端其实发的是 UTC 而漏了偏移，按墙钟解释会把年龄**算大** ⇒ 判陈旧 ⇒ 降级（fail-closed），
      ⛔ 不会反过来把旧事实当新的采信。
    """
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(float(raw), tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    text = str(raw).strip()
    if not text:
        return None
    candidate = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        from ..af_time import load_tz

        parsed = parsed.replace(tzinfo=load_tz())
    return parsed


def assess_freshness(
    name: str,
    parsed: Mapping[str, Any],
    *,
    now: datetime,
    overrides: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """**新鲜度闸单点实现**（契约 §2.5）：给一份读载荷判"这份事实还新不新"。

    返回 `{as_of, age_s, stale_after_s, stale, reason}`：
    - `stale_after_s` ＝ `min(本层阈值/覆盖值, 对端 stale_after_s)`——只许收紧，⛔ 不放宽；
    - `as_of` 缺失／解不出 ⇒ `stale=True`（`reason=as_of_unreadable`）＝**证不出新鲜就当陈旧**；
    - 未来时刻（时钟回拨）把年龄夹到 0 并留 `clock_skew_s` 事实（⛔ 不静默变负）。
    """
    thresholds = {**FRESHNESS_BY_READ, **(overrides or {})}
    limit = float(thresholds.get(name, FRESHNESS_HABITS_S))
    payload_limit = parsed.get(FRESHNESS_FIELD_LIMIT)
    if isinstance(payload_limit, (int, float)) and not isinstance(payload_limit, bool):
        limit = min(limit, float(payload_limit))
    raw_as_of = parsed.get("as_of")
    moment = parse_as_of(raw_as_of)
    if moment is None:
        return {
            "as_of": raw_as_of,
            "age_s": None,
            "stale_after_s": limit,
            "stale": True,
            "reason": "as_of_unreadable",
        }
    age = (now - moment).total_seconds()
    skew = min(0.0, age)
    age = max(0.0, age)
    return {
        "as_of": raw_as_of,
        "age_s": round(age, 3),
        "stale_after_s": limit,
        "stale": age > limit,
        "reason": "" if age <= limit else "stale",
        "clock_skew_s": round(skew, 3) if skew else 0.0,
    }


def structured_error_code(parsed: Mapping[str, Any]) -> str | None:
    """结构化业务错误码（契约 §2.1 现读形状：`{"ok":false,"error":{"code":...}}`）。

    ⛔ 只认 `error.code` 这一格：`ok:false` 但读不出码的旧形状仍走 `ma.py` 既有兜底路径。
    """
    error = parsed.get("error")
    if not isinstance(error, Mapping):
        return None
    code = str(error.get("code") or "").strip().upper()
    return code or None


def insufficient_reason(name: str, parsed: Mapping[str, Any]) -> str | None:
    """数据不足（**事实降级**）的四种现读形状，⛔ 与"陈旧"分开、⛔ 与 error 分开：

    ① 结构化 `error.code=DATA_INSUFFICIENT`（200＋该码，契约 §2.1）；
    ② `data_quality.sufficient=false`（契约 2.1 输出列）；
    ③ `member_habits` 的显式 `insufficient_data`（裁定 §2.4：样本 < `min_days=3`）；
    ④ 顶层 `sufficient=false`（2.4 的平表写法）。

    ⛔ 不把 `data: []` ＋ `sufficient: true` 当不足——那是"真无规律"这个真值（裁定 §2.4）。
    """
    if structured_error_code(parsed) == DATA_INSUFFICIENT:
        return "error_code"
    quality = parsed.get("data_quality")
    if isinstance(quality, Mapping) and quality.get("sufficient") is False:
        return "data_quality"
    if name == "get_member_habits":
        if parsed.get("insufficient_data") is True:
            return "insufficient_data"
        if parsed.get("sufficient") is False:
            return "sufficient_false"
    return None


def dispatch_failure(text: str) -> "_UpstreamFailure" | None:
    """分发级文本前缀分类（契约 §2.1 ①②）：`NOT_FOUND:`／`DENIED:` ⇒ 各自的 AF 码。

    ⛔ 只认**行首**前缀（`split(":", 1)[0]`）——正文里出现这两个词不算（那是业务描述）。
    """
    head = str(text or "").strip().split(":", 1)[0].strip().upper()
    code = DISPATCH_TEXT_ERRORS.get(head)
    if code is None:
        return None
    return _UpstreamFailure(code, str(text or "").strip()[:200])


def write_payload_defect(name: str, fields: Mapping[str, Any]) -> str | None:
    """批二写向载荷的**逐字段**校验（契约 2.3）；返回缺陷说明或 `None`。

    只在校验不过时返回；⛔ 不做"猜一个默认值补上"——那条路会把"AF 其实没记下用户选择"写进 MA。
    - 必填：`rule_ref`（记的是哪条规则）、`outcome`（枚举逐字）、`automation_decision`（AF 做了什么）、
      `user_response`（核心字段）＋其中 `choice`（四值逐字）；
    - 可选但**给了就要对**：`user_response.via`（三值）、`at`／`user_response.at`（非空字符串）、
      `evidence`（对象）。
    """
    if name not in WRITE_ACTIONS:
        return None
    for key in ("rule_ref", "outcome", "automation_decision", "user_response"):
        if fields.get(key) in (None, "", {}, []):
            return f"缺必填字段 {key!r}（契约 2.3）"
    outcome = str(fields.get("outcome"))
    if outcome not in OUTCOME_VALUES:
        return f"outcome={outcome!r} 不在枚举 {list(OUTCOME_VALUES)}（asked＝问了没答是合法态）"
    response = fields.get("user_response")
    if isinstance(response, str):
        # AF-831 wire 形（链主 2026-11-21 裁定）：已字符串化的 user_response 也认——
        # 解回对象复核语义（choice 枚举）；解析失败按原语义拒，⛔ 静默放行。
        try:
            response = json.loads(response)
        except (ValueError, TypeError):
            return "user_response 已是 wire 字符串但不是合法 JSON"
    if not isinstance(response, Mapping):
        return f"user_response 不是对象（收到 {type(response).__name__}）"
    choice = str(response.get("choice") or "")
    if choice not in USER_RESPONSE_CHOICES:
        return f"user_response.choice={choice!r} 不在枚举 {list(USER_RESPONSE_CHOICES)}"
    via = response.get("via")
    if via is not None and str(via) not in USER_RESPONSE_VIA:
        return f"user_response.via={via!r} 不在枚举 {list(USER_RESPONSE_VIA)}"
    for key, value in (("at", fields.get("at")), ("user_response.at", response.get("at"))):
        if value is not None and not str(value).strip():
            return f"{key} 给了但是空值"
    evidence = fields.get("evidence")
    if evidence is not None and not isinstance(evidence, Mapping):
        return f"evidence 不是对象（收到 {type(evidence).__name__}）"
    return None


def action_of(action: str) -> str:
    """`ma.route_question` 与 `route_question` 两种写法都归一到后者（与 `inbox.kind_of` 同形）。"""
    return str(action or "").strip().lower().rsplit(".", 1)[-1]


def select_entity(parsed: Mapping[str, Any], entity_id: str) -> dict[str, Any] | None:
    """从 `entities[]` 里按 `entity_id` 选一枚实体，只取它的**标量**字段。

    DSL 取不到列表下标（`af_expect.py` 的 `resolve_var` 逐段只对 `Mapping` 生效），所以"把某个设备
    的数值挑出来"这一手必须在适配器内做**一次**，选中的实体以 `entity` 这个 Mapping 回到 `data` 顶层，
    绑法就还是 `vars.<result_var>.data.entity.<字段>`。⛔ 不是给 DSL 加下标语法（那是表达式层扩容，另走 DCD）。
    """
    wanted = str(entity_id or "").strip()
    if not wanted:
        return None
    for row in parsed.get("entities") or []:
        if not isinstance(row, Mapping):
            continue
        for key in ("entity_id", "logical_id", "id", "name"):
            if str(row.get(key) or "").strip() == wanted:
                return {k: v for k, v in row.items() if not isinstance(v, (list, tuple, dict, Mapping))}
    return None


def _decode_envelope(raw: bytes, content_type: str, msg_id: Any) -> dict[str, Any]:
    """把一次 POST 的响应体解成 JSON-RPC 信封；解不出一律 `_UpstreamFailure`（不静默当空答复）。

    MA 的 streamable-http 端点按 `Accept` 可能回 `application/json`，也可能回 SSE 分帧
    （`data:` 行）。两种都在这一处收口，⛔ 不在调用点各写一份解码。
    """
    text = raw.decode("utf-8", errors="replace")
    candidates: list[Any] = []
    if "text/event-stream" in content_type:
        for line in text.splitlines():
            if line.startswith("data:"):
                chunk = line[5:].strip()
                if not chunk or chunk == "[DONE]":
                    continue
                try:
                    candidates.append(json.loads(chunk))
                except (ValueError, TypeError):
                    continue
    else:
        try:
            candidates.append(json.loads(text))
        except (ValueError, TypeError) as exc:
            raise _UpstreamFailure(
                ADM_ERR_PAYLOAD_INVALID, f"MA 响应不是合法 JSON：{type(exc).__name__}"
            ) from exc
    for item in candidates:
        if isinstance(item, dict) and (msg_id is None or item.get("id") == msg_id):
            return item
    if candidates and isinstance(candidates[-1], dict):
        return candidates[-1]
    raise _UpstreamFailure(ADM_ERR_PAYLOAD_INVALID, "MA 响应里没有 JSON-RPC 信封")


class MaAdapter:
    """单次调用、无重试、无降级、无语义层超时（协议见 `base.Adapter`）。

    会话握手（`initialize`）每个适配器实例只做一次；会话过期**不在这里重试**——纯执行层契约
    不允许适配器兜重试，重取数由 IR 用 `wait` + `on_error` 显式表达。
    """

    name = "ma"

    def __init__(
        self,
        endpoint: str = "",
        token: str = "",
        *,
        dry_run: bool = True,
        transport_timeout: float = DEFAULT_MA_TRANSPORT_TIMEOUT,
        allowed_hosts: tuple[str, ...] = (),
        freshness: Mapping[str, float] | None = None,
        clock: Any | None = None,
    ) -> None:
        self._endpoint = str(endpoint or "")
        self._token = str(token or "")
        self.dry_run = dry_run
        self.transport_timeout = float(transport_timeout)
        self._allowed_hosts = tuple(h for h in allowed_hosts if h)
        self.intents: list[dict[str, Any]] = []  # bounded-cache: exempt(dry_run 意图环形清单：唯一写入口 `call()` 按 INTENTS_MAX 裁头，读侧是仿真效果展开与测试；与 af_adapters/inbox.py 的 intents 同形，没有 TTL 腿——dry_run 记的就是"本会发什么"，不该自行过期)
        self._session_id: str | None = None
        self._rpc_id = 0
        #: 新鲜度阈值的**覆盖**（契约 §2.5：只许向下收紧）。放宽＝构造期就抛，⛔ 不留到运行期。
        self.freshness_overrides: dict[str, float] = {}  # bounded-cache: exempt(阈值覆盖表：键集 ⊆ FACADE_READS 三枚（构造期 `frozenset` 校验，未知读直接抛），唯一写入口是 `__init__` 的这段循环，之后只读不再增；没有 TTL 腿——新鲜度阈值是**配置**不是缓存，过期语义没有意义)
        for read, value in dict(freshness or {}).items():
            default = FRESHNESS_BY_READ.get(read)
            if default is None:
                raise AdapterError(
                    f"{ADM_ERR_PAYLOAD_INVALID}: 新鲜度覆盖点名了不认识的读 {read!r}"
                    f"（已定义：{sorted(FRESHNESS_BY_READ)}）"
                )
            if float(value) > float(default):
                raise AdapterError(
                    f"{ADM_ERR_PAYLOAD_INVALID}: ma.{read} 的新鲜度阈值只许收紧："
                    f"覆盖 {float(value)}s > 默认 {float(default)}s（契约 §2.5）"
                )
            self.freshness_overrides[read] = float(value)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        #: 两条分发级缺陷的**留痕计数**（预案 §三：告警＋计数，⛔ 与业务错误混账）。
        #: 用标量而不是容器计数表——本层不需要"按名字增长"的格子，也就不会长出无界容器。
        self.unregistered_tool_count = 0
        self.scope_denied_count = 0
        #: 事实降级两条各自的计数（陈旧／数据不足）：⛔ 都不是 error，但都要能读数。
        self.stale_read_count = 0
        self.insufficient_read_count = 0
        #: 批二写向两条计数：staging 违约（对端把报告写进了非 staging 面）与写向失败
        #: （自动化本体已完成 ⇒ ⛔ 不判自动化失败，但必须留痕＋计数）。
        self.staging_violation_count = 0
        self.write_failure_count = 0

    # ── 调用面 ─────────────────────────────────────────────────────
    def call(self, action: str, params: Mapping[str, Any]) -> CallResult:
        name = action_of(action)
        allowed = MA_ACTIONS.get(name)
        if allowed is None:
            # 名单外的动作名是 AF 侧的写法错，不是 MA 的回答 ⇒ 抛出去（`AdapterError`），
            # 让它在编译/仿真面响亮，而不是静默返回一条"没数据"。
            raise AdapterError(
                f"{ADM_ERR_PAYLOAD_INVALID}: ma 适配器不认的动作 {action!r}"
                f"（已定义：{sorted(MA_ACTIONS)}）"
            )
        fields = dict(params or {})
        unknown = sorted(k for k in fields if k not in allowed and k not in AF_ONLY_PARAMS)
        if unknown:
            return CallResult.fail(
                f"ma.{name} 不接受这些参数：{unknown}（允许：{sorted(allowed)}）",
                code=ADM_ERR_PAYLOAD_INVALID,
                action=name,
            )
        missing = [k for k in _REQUIRED_PARAMS.get(name, ()) if not str(fields.get(k) or "").strip()]
        if missing:
            return CallResult.fail(
                f"ma.{name} 缺少必填参数：{missing}",
                code=ADM_ERR_PAYLOAD_INVALID,
                action=name,
            )
        arguments = {k: fields[k] for k in allowed if k in fields}
        # 批二写向：载荷逐字段校验（契约 2.3）——在出网前拦住"记不下用户选择"的报告。
        defect = write_payload_defect(name, fields)
        if defect is not None:
            return CallResult.fail(
                f"ma.{name} 载荷不合契约 2.3：{defect}",
                code=ADM_ERR_PAYLOAD_INVALID,
                action=name,
                write_payload_defect=defect,
            )

        if self.dry_run:
            intent = {"action": name, "arguments": dict(arguments)}
            if len(self.intents) >= INTENTS_MAX:
                del self.intents[0]
            self.intents.append(intent)
            return CallResult.ok({"dry_run": True, **intent})

        # AF-831 附属件（链主 2026-11-21 裁定）：写向出网 wire 层字符串化——
        # dry_run 意图保持**对象**（语义层真值），只在这条真发路径转 MA 合同的 wire 形。
        if name in WRITE_ACTIONS:
            wire = dict(arguments)
            for key in _WRITE_WIRE_STRINGIFY:
                value = wire.get(key)
                if isinstance(value, str):
                    continue  # 已是 wire 形，幂等（防二次包装）
                wire[key] = json.dumps(value, ensure_ascii=False)
            evidence = arguments.get("evidence")
            if isinstance(evidence, Mapping):
                wire["evidence"] = [f"{k}:{v}" for k, v in evidence.items()]
            arguments = wire

        endpoint = self._resolve_endpoint()
        host = host_of(endpoint)
        if not host:
            return CallResult.fail(
                f"MA 端点未配置或非法（{MA_ENDPOINT_ENV}）：{endpoint!r}",
                code=ADM_ERR_AUTH_REQUIRED,
                action=name,
            )
        token = self._resolve_token()
        if not token:
            return CallResult.fail(
                f"未配置 MA 的 MCP 凭据（{MA_TOKEN_SECRET}），拒发",
                code=ADM_ERR_AUTH_REQUIRED,
                action=name,
            )

        try:
            parsed = self._tools_call(endpoint, host, token, name, arguments)
        except _UpstreamFailure as exc:
            return self._maybe_write_failure(name, self._failure_result(exc, action=name))

        # ① **先认结构化 `error.code`**（契约 §2.1／预案 §三 钉死的分类顺序）：
        #    业务错误走既有 ADM 族；`DATA_INSUFFICIENT` 不是 error，落到下面的事实降级。
        business = structured_error_code(parsed)
        if business is not None and business != DATA_INSUFFICIENT:
            return self._maybe_write_failure(
                name, self._business_failure(name, parsed, business)
            )

        # ② 数据不足＝**事实**（200＋flag／`data_quality.sufficient=false`／`insufficient_data`）。
        #    ⛔ 只对批一三枚门面生效：四条老动作的回执形状与本批无关，改它们＝无谓回归面。
        insufficient = insufficient_reason(name, parsed) if name in FACADE_READS else None

        self._guard_shape(name, parsed)
        if parsed.get("ok") is False and insufficient is None:
            # 对端明确答"没成"＝可兜底的业务结果（`on_error` 边接），⛔ 不是形状塌、也不冒充成功。
            # 目录里没有"上游给了个否答复"这一枚码（⛔ 不新造码），取最贴处置方向的那枚。
            # ⚠️ `DATA_INSUFFICIENT`（200＋该码）**不走这条**——它是事实降级（下面 `insufficient` 那支）。
            return CallResult.fail(
                f"ma.{name} 被 MA 判为失败：{str(parsed.get('error') or '（MA 未给原因）')[:200]}",
                code=ADM_ERR_INTERNAL,
                action=name,
            )
        data: dict[str, Any] = {"action": name, "payload": parsed}
        for key in _REQUIRED_KEYS[name]:
            if key in parsed:
                data[key] = parsed[key]
        if name == "export_insight" and isinstance(parsed.get("insight"), Mapping):
            data["insight"] = parsed["insight"]
        entity = select_entity(parsed, str(fields.get("entity_id") or ""))
        if fields.get("entity_id"):
            if entity is None:
                return CallResult.fail(
                    f"ma.{name} 的 entities[] 里没有 entity_id={fields['entity_id']!r}",
                    code=ADM_ERR_PAYLOAD_INVALID,
                    action=name,
                )
            data["entity"] = entity
        # ③ 批一三枚门面：新鲜度闸（**单点**）＋数据不足事实——两条分开断言、都当事实带回，
        #    ⛔ 都不翻成 error（陈旧／没数据都是自动化要能"看见并降级"的输入）。
        degraded: list[str] = []
        if name in FACADE_READS:
            freshness = assess_freshness(
                name, parsed, now=self._clock(), overrides=self.freshness_overrides
            )
            data["freshness"] = freshness
            if freshness["stale"]:
                self.stale_read_count += 1
                logger.warning(
                    "MA_READ_STALE read=%s age_s=%s stale_after_s=%s reason=%s",
                    name, freshness["age_s"], freshness["stale_after_s"], freshness["reason"],
                )
                degraded.append(f"stale:{freshness['reason']}")
        # 批二写向：把这次报告的身份与语义键回显进 data（审计/排障要能不看 `payload` 就知道
        # 「哪条规则、什么终态、用户怎么回的」——⛔ 不改对端回执，只是本层的回显）。
        if name in WRITE_ACTIONS:
            for key in ("rule_ref", "outcome", "automation_decision", "user_response", "context_ref"):
                if key in fields:
                    data[key] = fields[key]
        # ④ 批二写向：staging 守卫（契约 2.3——AF 只发报告、⛔ 不选库）。
        if name in WRITE_ACTIONS and parsed.get("staging") is not True:
            self.staging_violation_count += 1
            logger.error(
                "MA_STAGING_VIOLATION action=%s staging=%r memory_id=%r",
                name, parsed.get("staging"), parsed.get("memory_id"),
            )
            return self._maybe_write_failure(name, CallResult.fail(
                f"ma.{name} 的回执不是 staging（staging={parsed.get('staging')!r}）"
                "——契约 2.3 钉死 MA 恒落 staging、AF ⛔ 直写 live",
                code=ADM_ERR_PAYLOAD_INVALID,
                action=name,
                defect="staging_not_true",
            ))
        if insufficient is not None:
            self.insufficient_read_count += 1
            logger.info("MA_DATA_INSUFFICIENT read=%s shape=%s", name, insufficient)
            data["data_insufficient"] = True
            data["data_insufficient_shape"] = insufficient
            degraded.append("data_insufficient")
        if degraded:
            data["degraded"] = True
            data["degraded_reasons"] = degraded
        return CallResult.ok(data)

    # ── 失败分类（五态双形状的出口）─────────────────────────────────
    def _maybe_write_failure(self, name: str, result: CallResult) -> CallResult:
        """批二写向失败纪律（预案 §二.3／卡面红线）：自动化本体已完成，写 MA 失败**⛔ 不判自动化失败**。

        落法：给回执标 `automation_impact="none"`（本体不受影响）＋`retryable=False`（⛔ 不重试——
        重试解不了登记/授权/通道问题，还会把同一份终态重复写），计数 `write_failure_count`，
        并留一枚与业务码**分开**的具名 WARNING。⛔ 读向失败不走这条（那条是本次取数没成，
        该由 IR 的 `on_error` 兜）。
        """
        if name not in WRITE_ACTIONS:
            return result
        self.write_failure_count += 1
        result.data["automation_impact"] = "none"
        result.data["retryable"] = False
        logger.warning(
            "MA_OUTCOME_REPORT_FAILED action=%s code=%s", name, result.data.get("code")
        )
        return result

    def _failure_result(self, exc: _UpstreamFailure, *, action: str) -> CallResult:
        """通道/分发级失败 ⇒ `CallResult.fail`；两条分发级**各自**计数＋具名留痕。

        ⛔ 不合并告警：`MA_ERR_CONTRACT_DRIFT`（工具未登记＝契约漂移）与 `MA_ERR_SCOPE_DENIED`
        （写作用域不足）排障方向完全不同（改 MA 登记 vs 换带 write 的令牌），合并会把两件事
        混成一条读数。两条都标 `retryable=False`——重试解不了配置/登记问题。
        """
        data: dict[str, Any] = {"action": action, "code": exc.code}
        if exc.code == MA_ERR_CONTRACT_DRIFT:
            self.unregistered_tool_count += 1
            logger.error("MA_TOOL_UNREGISTERED action=%s detail=%s", action, exc.message)
            data["defect"] = "unregistered_tool"
            data["retryable"] = False
        elif exc.code == MA_ERR_SCOPE_DENIED:
            self.scope_denied_count += 1
            logger.error("MA_WRITE_SCOPE_DENIED action=%s detail=%s", action, exc.message)
            data["defect"] = "write_scope"
            data["retryable"] = False
        return CallResult.fail(exc.message, **data)

    @staticmethod
    def _business_failure(
        name: str, parsed: Mapping[str, Any], code: str
    ) -> CallResult:
        """结构化**业务**错误（契约 §2.1 ①～⑥）⇒ 映射到既有 ADM 族，原码随载荷带回。

        `business_error=True` 是给"同码不同形"分流用的：业务 `DENIED`（服务端开关）走这里，
        分发 `DENIED:`（作用域不足）走 `_failure_result`——两者⛔ 不许混成同一条告警。
        """
        adm = MA_BUSINESS_ERROR_TO_ADM.get(code, ADM_ERR_INTERNAL)
        error = parsed.get("error")
        detail = ""
        if isinstance(error, Mapping):
            detail = str(error.get("message") or error.get("detail") or "")[:200]
        return CallResult.fail(
            f"ma.{name} 被 MA 判为失败（{code}）：{detail or '（MA 未给原因）'}",
            code=adm,
            action=name,
            ma_error_code=code,
            business_error=True,
        )

    # ── 静默空值判据（链主 R3 / DCD 追认）──────────────────────────
    @staticmethod
    def _guard_shape(name: str, parsed: Mapping[str, Any]) -> None:
        """形状塌一律抛 `AdapterError`：不许让 `result_var` 静默绑成空值再冒充成功。"""
        if parsed.get("ok") is False:
            # 对端明确答"没成"——那是业务结果，由 `on_error` 兜，不在本判据射程。
            return
        block: Mapping[str, Any] = parsed
        nested = _NESTED_BLOCK.get(name)
        if nested is not None:
            inner = parsed.get(nested)
            if not isinstance(inner, Mapping):
                raise AdapterError(
                    f"{ADM_ERR_PAYLOAD_INVALID}: ma.{name} 的载荷缺 {nested!r} 或其不是对象"
                )
            block = inner
        required = _REQUIRED_KEYS[name]
        if not required:
            if not any(k not in ("ok", "error") for k in block):
                raise AdapterError(
                    f"{ADM_ERR_PAYLOAD_INVALID}: ma.{name} 除了 ok/error 之外读不到任何内容"
                )
            return
        # 判据取"至少一键在、且不是全空"，⛔ 不逐键硬要求：MA 的 export 面除三块外还带 `metadata`，
        # 键集会演进；逐键硬要会把"对端多给/少给一键"判成塌，而链主 R3 要挡的是**整块读空**。
        present = [k for k in required if k in block]
        if not present:
            raise AdapterError(
                f"{ADM_ERR_PAYLOAD_INVALID}: ma.{name} 的载荷里没有 {list(required)} 中的任何一键"
            )
        if name in _ALL_EMPTY_REFUSED and not any(block.get(k) for k in present):
            raise AdapterError(
                f"{ADM_ERR_PAYLOAD_INVALID}: ma.{name} 的载荷键齐而值全空（读空≠成功）"
            )
        # 门面读（批一）：`as_of` 只是新鲜度入口，不算事实——只带它回来的空壳必须响亮。
        facts = _FACADE_FACT_KEYS.get(name)
        if facts is not None and not any(k in block for k in facts):
            raise AdapterError(
                f"{ADM_ERR_PAYLOAD_INVALID}: ma.{name} 的回执没有 {list(facts)} 中的任何一块"
                "（只带 as_of 的空壳⛔ 不当成功）"
            )

    # ── 传输 ────────────────────────────────────────────────────────
    def _resolve_endpoint(self) -> str:
        endpoint = self._endpoint or os.environ.get(MA_ENDPOINT_ENV, "")
        if not endpoint:
            return ""
        base = str(endpoint).rstrip("/")
        # MA 把 MCP 挂在 /mcp（`memory_agent/app.py::_mcp_dispatcher`）：只给 host:port 时补上那一段。
        parsed = urlparse(base)
        if parsed.scheme and parsed.hostname and parsed.path in ("", "/"):
            base = f"{base}/mcp"
        return base

    def _resolve_token(self) -> str:
        if self._token:
            return self._token
        from ..af_secrets import load_secret

        return str(load_secret(MA_TOKEN_SECRET, ""))

    def _next_id(self) -> int:
        self._rpc_id += 1
        return self._rpc_id

    def _post(self, endpoint: str, host: str, token: str, body: Mapping[str, Any]) -> dict[str, Any]:
        req = Request(
            endpoint,
            data=json.dumps(dict(body)).encode("utf-8"),
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json, text/event-stream")
        req.add_header("Authorization", f"Bearer {token}")
        if self._session_id:
            req.add_header("Mcp-Session-Id", self._session_id)
        hosts = self._allowed_hosts or (host,)
        try:
            with guarded_open(req, allowed_hosts=hosts, timeout=self.transport_timeout) as resp:
                raw = resp.read(MAX_RESPONSE_BYTES + 1)
                content_type = (resp.headers.get("content-type") or "").lower()
                sid = resp.headers.get("mcp-session-id") or self._session_id
        except HTTPError as exc:
            code = getattr(exc, "code", 0)
            if code in (401, 403):
                raise _UpstreamFailure(
                    ADM_ERR_AUTH_REQUIRED, f"MA 拒绝凭据（HTTP {code}）"
                ) from exc
            if code == 408 or code == 504:
                raise _UpstreamFailure(
                    ADM_ERR_UPSTREAM_TIMEOUT, f"MA 超时（HTTP {code}）"
                ) from exc
            if code >= 500:
                raise _UpstreamFailure(
                    ADM_ERR_PEER_OFFLINE, f"MA 侧故障（HTTP {code}）"
                ) from exc
            raise _UpstreamFailure(ADM_ERR_INTERNAL, f"MA 返回 HTTP {code}") from exc
        except socket.timeout as exc:
            raise _UpstreamFailure(
                ADM_ERR_UPSTREAM_TIMEOUT, "MA 未在传输层超时内应答"
            ) from exc
        except (URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise _UpstreamFailure(
                    ADM_ERR_UPSTREAM_TIMEOUT, "MA 未在传输层超时内应答"
                ) from exc
            raise _UpstreamFailure(
                ADM_ERR_PEER_OFFLINE, f"MA 不可达：{type(exc).__name__}: {exc}"
            ) from exc
        self._session_id = sid
        if len(raw) > MAX_RESPONSE_BYTES:
            raise _UpstreamFailure(
                ADM_ERR_PAYLOAD_INVALID,
                f"MA 响应超过上限 {MAX_RESPONSE_BYTES} 字节，拒收（不无界吞）",
            )
        if not raw.strip() and "id" not in body:
            # JSON-RPC 通知（`notifications/initialized`）按规范回 202 空体是**合法**的：
            # ⛔ 不许把"没有信封"判成形状塌（那会把每次握手都判成失败）。
            # （AF-830：MA 联调窗实抓——tv.py:681-685 同款守卫，ma.py 漏了，见 af_adapters/tv.py）
            return {}
        return _decode_envelope(raw, content_type, body.get("id"))

    def _ensure_session(self, endpoint: str, host: str, token: str) -> None:
        if self._session_id is not None:
            return
        msg_id = self._next_id()
        envelope = self._post(
            endpoint,
            host,
            token,
            {
                "jsonrpc": "2.0",
                "id": msg_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": CLIENT_NAME, "version": "1"},
                },
            },
        )
        if "error" in envelope:
            err = envelope["error"]
            raise _UpstreamFailure(
                ADM_ERR_INTERNAL,
                f"MA 拒绝握手：{str(err.get('message') if isinstance(err, Mapping) else err)[:200]}",
            )
        # 握手成功即视为会话可用；`notifications/initialized` 失败只说明对端不收这条通知，
        # 不影响随后的 tools/call，因此它**不参与**本次读数的成败判定，也不在这里重试。
        self._post(
            endpoint,
            host,
            token,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
        )

    def _tools_call(
        self, endpoint: str, host: str, token: str, name: str, arguments: Mapping[str, Any]
    ) -> dict[str, Any]:
        self._ensure_session(endpoint, host, token)
        msg_id = self._next_id()
        envelope = self._post(
            endpoint,
            host,
            token,
            {
                "jsonrpc": "2.0",
                "id": msg_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": dict(arguments)},
            },
        )
        if "error" in envelope:
            err = envelope["error"]
            raise _UpstreamFailure(
                ADM_ERR_INTERNAL,
                f"MA 工具调用出错：{str(err.get('message') if isinstance(err, Mapping) else err)[:200]}",
            )
        result = envelope.get("result")
        if not isinstance(result, Mapping):
            raise _UpstreamFailure(ADM_ERR_PAYLOAD_INVALID, "MA 响应里没有 result 对象")
        return _tool_payload(name, result)


def _tool_payload(name: str, result: Mapping[str, Any]) -> dict[str, Any]:
    """把 MCP `tools/call` 的 result 取成 MA 的业务 dict。

    分类顺序（契约 §2.1／预案 §三 钉死）：**先认结构化 `error.code`，再认文本前缀**。
    所以 `structuredContent` 排在 `isError` 之前——只有"没有结构化载荷"时才拿文本前缀判
    分发级（`NOT_FOUND:`＝工具未登记／`DENIED:`＝作用域不足），两者各有自己的 AF 码。
    """
    structured = result.get("structuredContent")
    if isinstance(structured, Mapping):
        return dict(structured)
    if result.get("isError") is True:
        text = next(
            (
                str(item.get("text") or "")
                for item in (result.get("content") or [])
                if isinstance(item, Mapping)
            ),
            "",
        )
        dispatch = dispatch_failure(text)
        if dispatch is not None:
            raise dispatch
        raise _UpstreamFailure(
            ADM_ERR_INTERNAL, f"ma.{name} 被 MA 判为出错：{text[:200]}"
        )
    for item in result.get("content") or []:
        if not isinstance(item, Mapping) or item.get("type") not in (None, "text"):
            continue
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError) as exc:
            raise _UpstreamFailure(
                ADM_ERR_PAYLOAD_INVALID, f"ma.{name} 的 text 载荷不是合法 JSON：{type(exc).__name__}"
            ) from exc
        if isinstance(parsed, Mapping):
            return dict(parsed)
        raise _UpstreamFailure(ADM_ERR_PAYLOAD_INVALID, f"ma.{name} 的 text 载荷不是对象")
    raise _UpstreamFailure(ADM_ERR_PAYLOAD_INVALID, f"ma.{name} 的 result 里没有可读载荷")
