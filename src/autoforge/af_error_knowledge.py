"""v1.5.0 错误知识库（调研 §2.11）：有序正则归因 + 类别化修复建议 + 有界历史。

设计要点（对齐 autoflow）：

- **有序正则**：`RULES` 顺序即优先级，**首个命中即返回**——越具体的规则排越前；
- **每个精确类别都有专门建议**（`CATEGORY_ADVICE[category]` 必非空）——否则回执没有行动价值；
- **兜底方向**：宁可把「上游/网关/网络」类线索归到 `UPSTREAM_HA`/`GATEWAY_HTTP`，
  **不冤枉 agent**（误判成「agent 写错 IR」会把人引到错误修法）；
- **纯规则、零 LLM**：离线可用、可测试；
- **有界存储**：`{root}/error_knowledge.jsonl` append-only，读取取最近 `max_records`（默认 500）。

同时承载「失败回执附历史同类案例」的数据源（`similar()`）。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .af_store import append_jsonl, atomic_write_text, read_jsonl_bounded

__all__ = [
    "CATEGORY_LABEL",
    "CATEGORY_ADVICE",
    "KNOWN_CATEGORIES",
    "RULES",
    "classify",
    "explain",
    "ErrorKnowledge",
    "KNOWLEDGE_FILENAME",
    "DEFAULT_MAX_RECORDS",
]

#: 知识库文件名（放 store 根；append-only JSONL）。
KNOWLEDGE_FILENAME = "error_knowledge.jsonl"
DEFAULT_MAX_RECORDS = 500
#: 单条 message 落库上限（防超长正文把 JSONL 撑爆）。
_MAX_MESSAGE = 600

#: 类别 → 中文标签。
CATEGORY_LABEL: dict[str, str] = {
    "GATEWAY_HTTP": "网关/出站 HTTP 异常",
    "UPSTREAM_HA": "上游 Home Assistant 不可用",
    "TOKEN_EXPIRED": "令牌已过期",
    "SCOPE_DENIED": "权限不足（scope）",
    "PENDING_LIMIT": "待批队列熔断",
    "BLAST_RADIUS": "爆炸半径超限",
    "OWNERSHIP": "归档所有权隔离",
    "WRITE_CONFLICT": "版本冲突（乐观锁）",
    "ENTITY_GUARD_TIER0": "设备保护 Tier-0（必须人审）",
    "HTTP_NOT_WHITELISTED": "出站主机不在白名单",
    "L3_ACTION": "高危动作（L3）",
    "LOW_CONFIDENCE": "低置信度写设备",
    "MISSING_TIMEOUT_OR_DEFAULT": "挂起缺兜底",
    "ENTITY_NOT_FOUND": "引用了不存在的实体",
    "IR_SEMANTIC": "IR 未通过静态扫描",
    "IR_SCHEMA": "IR 结构/Schema 非法",
    "UNKNOWN": "未归类",
}

#: 类别 → **必非空**的修复建议（不落 other）。
CATEGORY_ADVICE: dict[str, str] = {
    "GATEWAY_HTTP": "出站网关/网络异常：先确认服务与网络连通（网关 5xx、连接被拒），再重试；不要改 IR。",
    "UPSTREAM_HA": "上游 HA 不可用：确认 HA 地址/令牌与连通性（`forge credentials show`），恢复后重试；不要改 IR。",
    "TOKEN_EXPIRED": "令牌已过期：更新 `AUTOFORGE_TOKENS` 的 `expires_at` 或换新令牌（`forge credentials update`）。",
    "SCOPE_DENIED": "当前令牌缺少所需 scope：用含 `write`/`live` 的令牌访问（见 v0.8.0 多令牌主体模型）。",
    "PENDING_LIMIT": "该提交者待批条数达上限：先 `forge pending list` 审批/拒绝清理，再提交。",
    "BLAST_RADIUS": "一次归档的自动化条数超爆炸半径：拆小或显式 `allow_bulk=true`（确认是批量操作）。",
    "OWNERSHIP": "该归档归属他人：换名归档，或由归属者提交（所有权隔离不可覆盖）。",
    "WRITE_CONFLICT": "版本冲突（`expect_version` 不符）：先 `forge graphs`/`af_get_graph` 取最新版本再重试。",
    "ENTITY_GUARD_TIER0": "该实体被设备保护标记为 Tier-0（必须人审）：换非受保护实体，或调整 `device_acl.json`。",
    "HTTP_NOT_WHITELISTED": "出站主机不在白名单：加进 `--http-allowed-hosts`，或改用 HA 适配器。",
    "L3_ACTION": "动作属 L3（高危）：换低危替代；确需保留则补 `requires_confirm` + `canary`。",
    "LOW_CONFIDENCE": "置信度 < 0.6 禁止直接写设备：改为出 `ask` 提案，或先提升置信度。",
    "MISSING_TIMEOUT_OR_DEFAULT": "ask/wait 缺 `on_timeout`/`default`：补兜底边，避免永久挂起。",
    "ENTITY_NOT_FOUND": "实体不存在/漂移：先用 `af_resolve_entity`（或 `forge entities resolve`）拿真实 entity_id。",
    "IR_SEMANTIC": "IR 未过静态扫描：按 diagnostics 逐条修（每项带 `hint`），或用 `af_build` 查看明细。",
    "IR_SCHEMA": "IR 结构非法：对照 IR Schema 补必填字段（如边 `kind`、节点 `kind`）；用 `af_build` 定位。",
    "UNKNOWN": "未能归类：请贴完整错误与上下文（幂等键 + IR 片段）以便补充归因规则。",
}

#: 归因规则：**顺序即优先级**（首个命中即返回）。具体/上游规则排前，泛化/IR 规则排后。
#: 兜底口径：宁可多归「网关/上游」，不冤枉 agent。
RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    # ── 上游/网络优先（不冤枉 agent）──
    (
        re.compile(r"(5\d\d\s*)?(bad gateway|service unavailable|gateway timeout|connection refused|"
                   r"econnrefused|连接被拒|网关|502|503|504)", re.I),
        "GATEWAY_HTTP",
    ),
    (
        re.compile(r"(homeassistant|连接\s*HA|HA\s*(不可达|超时|拒绝|请求失败)|无法连接\s*HA|"
                   r"upstream|max retries|timed out|timeout|超时)", re.I),
        "UPSTREAM_HA",
    ),
    # ── 鉴权/治理 ──
    (re.compile(r"(令牌已过期|已过期|token expired)", re.I), "TOKEN_EXPIRED"),
    (re.compile(r"(缺少\s*'?(write|live|read)'?|缺少.{0,4}scope|权限不足|无权|越权|\bdenied\b)", re.I), "SCOPE_DENIED"),
    (re.compile(r"(待批|pending.{0,6}(上限|过多|熔断)|熔断)", re.I), "PENDING_LIMIT"),
    (re.compile(r"(爆炸半径|blast.?radius)", re.I), "BLAST_RADIUS"),
    (re.compile(r"(所有权隔离|归属)", re.I), "OWNERSHIP"),
    (re.compile(r"(版本冲突|乐观锁|expect_version|write.?conflict)", re.I), "WRITE_CONFLICT"),
    (re.compile(r"(tier-?0|entity_guard_tier0|必须人审)", re.I), "ENTITY_GUARD_TIER0"),
    (re.compile(r"(不在白名单|not_whitelisted|白名单)", re.I), "HTTP_NOT_WHITELISTED"),
    # ── IR 语义（具体诊断优先）──
    (re.compile(r"(\bL3\b|高危动作)", re.I), "L3_ACTION"),
    (re.compile(r"(low_conf_writes_device|shadow|置信度.{0,6}[<＜].{0,6}0\.6)", re.I), "LOW_CONFIDENCE"),
    (re.compile(r"(永久挂起|missing_timeout|on_timeout|缺\s*on_timeout|timeout_or_default)", re.I), "MISSING_TIMEOUT_OR_DEFAULT"),
    (re.compile(r"(不存在的实体|entity_not_found|实体.{0,4}漂移)", re.I), "ENTITY_NOT_FOUND"),
    (re.compile(r"(未通过静态扫描|拒绝归档|静态扫描)", re.I), "IR_SEMANTIC"),
    (re.compile(r"(ir\s*校验失败|ir\s*结构非法|schema|required property|is a required|"
                r"is not one of|automations 必须是)", re.I), "IR_SCHEMA"),
)

#: 所有「精确类别」（不含 UNKNOWN）——供「每类必有建议」自检。
KNOWN_CATEGORIES: tuple[str, ...] = tuple(c for c in CATEGORY_ADVICE if c != "UNKNOWN")


def classify(message: str) -> str:
    """**有序正则**归因：首个命中即返回；无命中 → `"UNKNOWN"`。"""
    text = str(message or "")
    if not text:
        return "UNKNOWN"
    for pattern, category in RULES:
        if pattern.search(text):
            return category
    return "UNKNOWN"


def explain(message: str) -> dict[str, Any]:
    """归因 + 类别化建议。`matched` 标明是否命中规则（便于自检归因覆盖率）。"""
    category = classify(message)
    return {
        "category": category,
        "label": CATEGORY_LABEL.get(category, category),
        "advice": CATEGORY_ADVICE.get(category, CATEGORY_ADVICE["UNKNOWN"]),
        "matched": category != "UNKNOWN",
    }


class ErrorKnowledge:
    """失败历史知识库（append-only JSONL + 有界读取）。"""

    def __init__(self, root: str | Path, *, max_records: int = DEFAULT_MAX_RECORDS) -> None:
        self.root = Path(root)
        self.max_records = int(max_records)

    @property
    def path(self) -> Path:
        return self.root / KNOWLEDGE_FILENAME

    def record(
        self,
        message: str,
        *,
        category: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """记录一条失败：自动归因（可显式覆盖）+ 落库（超限自动 prune）。"""
        text = str(message or "")[:_MAX_MESSAGE]
        info = explain(text)
        cat = category or info["category"]
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "category": cat,
            "label": CATEGORY_LABEL.get(cat, cat),
            "advice": CATEGORY_ADVICE.get(cat, CATEGORY_ADVICE["UNKNOWN"]),
            "message": text,
            "context": dict(context or {}),
        }
        append_jsonl(self.path, entry)
        self.prune()
        return entry

    def recent(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """最近 `limit` 条（受 `max_records` 上界约束）。"""
        cap = min(int(limit), self.max_records)
        return read_jsonl_bounded(self.path, cap)[-cap:]

    def similar(self, message: str, *, limit: int = 3) -> list[dict[str, Any]]:
        """**历史同类**（同 category）最近 `limit` 条——供失败回执「写→读闭环」。"""
        cat = classify(message)
        same = [r for r in self.recent(limit=self.max_records) if r.get("category") == cat]
        return same[-limit:]

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for rec in self.recent(limit=self.max_records):
            cat = str(rec.get("category", "UNKNOWN"))
            out[cat] = out.get(cat, 0) + 1
        return out

    def prune(self) -> int:
        """超 `max_records` 时保留最近 `max_records` 行（原子重写）；返回删除条数。"""
        if not self.path.is_file():
            return 0
        try:
            lines = [ln for ln in self.path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        except OSError:
            return 0
        if len(lines) <= self.max_records:
            return 0
        keep = lines[-self.max_records :]
        removed = len(lines) - len(keep)
        atomic_write_text(self.path, "\n".join(keep) + "\n")
        return removed
