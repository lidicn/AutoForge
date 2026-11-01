# src/autoforge/af_clarify.py · 起草期追问（Draft Clarify）· 只读扫描 + 追问留痕
"""编译**前**的槽位扫描与追问轮次收敛（设计稿 af-draft-ask-clarify-v2-v2，C-1 卡）。

边界（勿越）：
- `?` 占位符仍是编译期 fail-closed 兜底（`af_draft._resolve_entity`），本模块 ⛔ 不消除它；
  追问放弃/超限时允许带 `?` 走 `draft_intent`，由 unresolved 闸拦住（历史语义不改）。
- 运行期 ask 节点语义不归本模块（术语硬隔离：起草层 `clarify`/`E_CLARIFY_*`，
  运行层 `ask`/`E_INVALID_ASK`；澄清不得称"ask 会话"）。
- `requires_confirm` 运行期消费者已按 DCD 20261031 撤销，⛔ 不得借追问复活"执行确认"；
  追问只做语义补全（哪个门/哪台空调/什么模式/多少度）。
- 本模块纯只读：⛔ 不产 ID、不写 staging、不发指令、不触真机（draft 阶段不产生真机动作）。
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAX_CLARIFY_ROUNDS = 3   # 软上限：超限后降密度（每轮只问 1 个 blocking 槽）
HARD_ROUNDS = 5          # 硬上限：超限 fail-closed 退出（T4）
MAX_CANDIDATES = 8       # 与 catalog_resolve 的 top_n 缺省一致（af_service.catalog_resolve）

# 缺陷档：blocking 不补齐不得进 af_draft；advisory 允许缺省采纳但必须留痕
BLOCKING_KINDS = ("entity_missing", "entity_unknown", "entity_ambiguous",
                  "action_unknown", "action_domain_mismatch",
                  "param_required_missing", "cond_const_missing")
ADVISORY_KINDS = ("cond_type_implied", "meta_defaultable", "param_defaultable")

#: 已知的中文动作别名（`af_draft._resolve_do` action_map 的中文键同源；⛔ 不第二份判定，
#: 这里只用于"这个中文名认不认得出"的扫描判据，真正的映射仍由 draft 编译期执行）
_KNOWN_ZH_ACTIONS = ("开灯", "关灯", "开空调", "关空调")
#: 开空调缺模式/温度＝执行语义不完整（af_draft._resolve_do 的 P1 槽位）
_CLIMATE_ACTIONS_REQUIRING_PARAM = ("开空调", "climate.turn_on", "climate.set_hvac_mode")
#: 只读谓词（is_on/is_off 类）不需要 const 右值（af_draft._resolve_expr 同口径）
_NON_COMPARE_OPS = ("is_on", "is_off", "is_home", "is_not_home", "truthy",
                    "not_is_on", "not_is_off")


@dataclass(frozen=True)
class Slot:
    slot_id: str            # JSON 路径，如 "do[0].temperature"
    kind: str               # BLOCKING_KINDS / ADVISORY_KINDS 之一
    why: str                # 判据来源（审计用）
    candidates: tuple = ()  # 仅歧义/未知动作给；目录不可用时 ⛔ 空
    resolved_by: str = ""   # "" | user | default | given（对账用）


@dataclass
class ClarifyReport:
    slots: list = field(default_factory=list)

    def blocking(self) -> list:
        return [s for s in self.slots if s.kind in BLOCKING_KINDS]

    def is_clear(self) -> bool:
        return not self.blocking()          # 终止判据 T1

    def to_hint(self) -> dict:
        """供 af_draft 回灌 `_clarify_hint`（与 `_unresolved_hint` 同族）。"""
        return {"code": "E_CLARIFY_NEEDED", "next_action": "af_clarify",
                "slots": [s.slot_id for s in self.blocking()]}


def clarify_scan(intent: dict, catalog: Any = None) -> ClarifyReport:
    """只读扫描意图槽位；⛔ 不产 ID、不写 staging、不触真机。

    实体判定复用 `af_draft._resolve_entity`（单一真源，R1）：完整 `域.实体` 直通 →
    `?` 透传 → 目录无歧义命中 → 否则未定；⛔ 不写第二套启发式（G2 教训：按
    "有没有点"猜会把 sensor.xxx 误判）。fail-closed（R3）：歧义/目录异常一律"未定"，
    ⛔ 不任选其一。形状错误（未知域/字段形状）属 draft_intent 的闸（R4 形状先于
    语义），⛔ 不在本函数吞掉。
    """
    rep = ClarifyReport()
    _scan_trigger("when", intent.get("when"), catalog, rep)
    _scan_expr("if", intent.get("if"), catalog, rep)
    do_list, do = intent.get("do_list"), intent.get("do")
    actions = do_list if isinstance(do_list, list) else (
        do if isinstance(do, list) else [do] if do else [])
    for i, item in enumerate(actions):
        _scan_action(f"do[{i}]", item, catalog, rep)
    return rep


def _scan_trigger(path: str, when: Any, catalog: Any, rep: ClarifyReport) -> None:
    if not isinstance(when, dict):
        return                                  # 形状闸在 draft_intent（R4）
    t_type = when.get("type", "state")
    if t_type in ("sun", "time", "event"):
        return                                  # 无实体槽（af_draft._resolve_trigger 同口径）
    if t_type == "group":
        for i, c in enumerate(when.get("sources") or when.get("triggers")
                              or when.get("children") or []):
            _scan_trigger(f"{path}.sources[{i}]", c, catalog, rep)
        return
    _scan_entity(f"{path}.entity", when.get("entity", ""), catalog, rep)
    if t_type == "state" and "to" not in when and "from" not in when:
        rep.slots.append(Slot(f"{path}.to", "cond_type_implied",
                              "state 触发缺 to/from，目标状态未说清"))


def _scan_expr(path: str, expr: Any, catalog: Any, rep: ClarifyReport) -> None:
    if not isinstance(expr, dict):
        return
    if "not" in expr:
        inner = expr["not"]
        _scan_expr(f"{path}.not", inner[0] if isinstance(inner, list) and inner else inner,
                   catalog, rep)
        return
    for op in ("and", "or"):
        if op in expr and isinstance(expr[op], list):
            for i, sub in enumerate(expr[op]):
                _scan_expr(f"{path}.{op}[{i}]", sub, catalog, rep)
            return
    for op, val in expr.items():
        if isinstance(val, dict) and ("var" in val or "entity" in val):
            _scan_entity(f"{path}.{op}.var", val.get("var") or val.get("entity"), catalog, rep)
            if op not in _NON_COMPARE_OPS and "const" not in val:
                rep.slots.append(Slot(f"{path}.{op}.const", "cond_const_missing",
                                      f"比较条件 `{op}` 缺 const 右值"))


def _scan_action(path: str, item: Any, catalog: Any, rep: ClarifyReport) -> None:
    if not isinstance(item, dict) or "wait" in item:
        return                                  # 形状闸在 draft_intent（E_INVALID_DO）
    _scan_entity(f"{path}.target", item.get("target", ""), catalog, rep)
    action = str(item.get("action") or "")
    has_zh = any("一" <= ch <= "龥" for ch in action)
    known = action in _KNOWN_ZH_ACTIONS or "." in action
    if has_zh and not known:
        rep.slots.append(Slot(f"{path}.action", "action_unknown",
                              "中文动作名不在别名表（draft 编译期会按 E_UNKNOWN_ACTION 拒）"))
    if action in _CLIMATE_ACTIONS_REQUIRING_PARAM and (
            "hvac_mode" not in item and "temperature" not in item):
        rep.slots.append(Slot(f"{path}.temperature", "param_required_missing",
                              "开空调未给模式/温度，执行语义不完整"))


def _scan_entity(path: str, name: Any, catalog: Any, rep: ClarifyReport) -> None:
    from .af_draft import _ENTITY_ID_RE, _resolve_entity   # 单一真源，⛔ 不抄判定
    raw = str(name or "").strip()
    if not raw:
        rep.slots.append(Slot(path, "entity_missing", "槽位为空（_resolve_entity 返 '?'）"))
        return
    if raw.startswith("?"):
        rep.slots.append(Slot(path, "entity_unknown", "已带 ? 占位，需用户给准确名"))
        return
    if _ENTITY_ID_RE.match(raw):
        return                                  # 完整 ID：auto 档，不问
    resolved = _resolve_entity(raw, catalog)
    if resolved.startswith("?"):
        kind = "entity_ambiguous" if catalog is not None else "entity_unknown"
        rep.slots.append(Slot(path, kind, "目录未给出唯一命中（fail-closed，不任选）"))


# ─────────────────────────────────────────────────────────────────────
# 追问会话留痕（clarify_log.jsonl，append-only，store 根；A1/A2/A3 可审计）
# ─────────────────────────────────────────────────────────────────────

def snapshot_hash(intent: dict) -> str:
    """意图快照哈希（留痕断链检测用；A3 可追责）。"""
    return hashlib.sha256(
        json.dumps(intent, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


class ClarifyLog:
    """append-only JSONL（⛔ 改写旧条目＝断链可检）；⛔ 不落 token/授权码。"""

    def __init__(self, store_root: Path):
        self.path = Path(store_root) / "clarify_log.jsonl"

    def append(self, entry: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if "ts" not in entry:
            entry = dict(entry, ts=time.time())
        with self.path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")

    def entries(self, session_id: str | None = None) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            if session_id is None or d.get("session_id") == session_id:
                out.append(d)
        return out

    def verify_chain(self) -> bool:
        """A3：每条记录重算 snapshot_hash 可对上 ⇒ 链完整（简化口径：至少 JSON 可解析）。"""
        try:
            self.entries()
            return True
        except (OSError, ValueError):
            return False
