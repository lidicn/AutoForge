"""v1.5.0 失败归因遥测（调研 §2.10 / §2.16）。

- **零侵入**：`attach(result, ...)` 只在返回 dict 上补一个下划线键 `_telemetry`，
  绝不改 `ok`/`errors` 等既有字段，也不改控制流；
- **纯规则归因**：委托 `af_error_knowledge`（有序正则，零 LLM）；
- **token 估算**：按字符数 `/4`（无 tokenizer 依赖）；
- **有界存储**：`{root}/telemetry/usage.jsonl`，`usage()` 按 `retention_days`（默认 30）截断。

`_telemetry` 形状::

    {"ok": bool, "category": str, "label": str, "advice": str,
     "tokens": int, "ts": iso, "history": [同类历史…]}
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .af_error_knowledge import ErrorKnowledge, explain
from .af_store import append_jsonl, atomic_write_text, read_jsonl_bounded

logger = logging.getLogger("autoforge.telemetry")

__all__ = [
    "estimate_tokens",
    "attach",
    "record_failure",
    "TelemetryStore",
    "TELEMETRY_DIR",
    "USAGE_FILENAME",
    "DEFAULT_RETENTION_DAYS",
]

TELEMETRY_DIR = "telemetry"
USAGE_FILENAME = "usage.jsonl"
DEFAULT_RETENTION_DAYS = 30
#: `usage()` 单次读取上限（有界内存）。
_USAGE_READ_CAP = 20000
#: 每追加这么多条做一次过期清理（避免每条都全文件重读）。
_PRUNE_EVERY = 500
_PRUNE_COUNTER: dict[str, int] = {}


def estimate_tokens(*texts: str) -> int:
    """按字符数 `/4` 估算 token（向上取整）。"""
    chars = sum(len(t) for t in texts if t)
    return (chars + 3) // 4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def attach(
    result: dict[str, Any],
    *,
    tool: str,
    root: str | Path | None = None,
    ok: bool | None = None,
    message: str = "",
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """给返回 dict 补 `_telemetry`（零侵入）。

    - `ok` 缺省取 `result.get("ok", True)`；
    - 失败（`ok is False`）且有 `message` 时：归因 + 历史同类（`root` 非空时读知识库）；
    - `tokens` 按 `_telemetry` **之前**的 result 体积估算；
    - `root` 非空时把事件落 `usage.jsonl`，失败同时落 `error_knowledge.jsonl`。
    """
    is_ok = bool(result.get("ok", True)) if ok is None else bool(ok)
    tokens = estimate_tokens(json.dumps(result, ensure_ascii=False, default=str))

    category = label = advice = ""
    history: list[dict[str, Any]] = []
    if not is_ok and message:
        info = explain(message)
        category, label, advice = info["category"], info["label"], info["advice"]
        if root is not None:
            # 遥测/知识库是**旁路**：任何失败都不得把已成功的业务调用拖成错误
            try:
                kb = ErrorKnowledge(root)
                history = kb.similar(message, limit=3)
                kb.record(message, context=context)
            except (OSError, ValueError, KeyError):
                logger.debug("ERROR_KNOWLEDGE_RECORD_SKIPPED", exc_info=True)

    result["_telemetry"] = {
        "ok": is_ok,
        "category": category,
        "label": label,
        "advice": advice,
        "tokens": tokens,
        "ts": _now(),
        "history": history,
    }
    if root is not None:
        try:
            TelemetryStore(root).record(
                {
                    "tool": tool,
                    "ok": is_ok,
                    "category": category,
                    "tokens": tokens,
                    "message": str(message or "")[:300],
                }
            )
        except (OSError, ValueError):
            logger.debug("TELEMETRY_RECORD_SKIPPED", exc_info=True)
    return result


def record_failure(
    root: str | Path,
    *,
    tool: str,
    message: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """记录一条失败（归因 + 落库 + usage）——供异常路径（无返回 dict）调用。

    返回 `_telemetry` 形状的 dict（不含 `history`/`advice` 以外的字段亦可直接外露）。
    """
    info = explain(message)
    kb = ErrorKnowledge(root)
    history = kb.similar(message, limit=3)
    kb.record(message, context=context)
    tokens = estimate_tokens(message)
    TelemetryStore(root).record(
        {
            "tool": tool,
            "ok": False,
            "category": info["category"],
            "tokens": tokens,
            "message": str(message or "")[:300],
        }
    )
    return {
        "ok": False,
        "category": info["category"],
        "label": info["label"],
        "advice": info["advice"],
        "tokens": tokens,
        "ts": _now(),
        "history": history,
    }


class TelemetryStore:
    """token/结果遥测（append-only JSONL + 有界读取 + 时间窗口聚合）。"""

    def __init__(self, root: str | Path, *, retention_days: int = DEFAULT_RETENTION_DAYS) -> None:
        self.root = Path(root)
        self.retention_days = int(retention_days)

    @property
    def path(self) -> Path:
        return self.root / TELEMETRY_DIR / USAGE_FILENAME

    def record(self, event: dict[str, Any]) -> dict[str, Any]:
        entry = {"ts": _now(), **event}
        append_jsonl(self.path, entry)
        # 审计发现：只增不减会让 usage.jsonl 无限增长（retention_days 曾是死变量）
        key = str(self.path)
        _PRUNE_COUNTER[key] = _PRUNE_COUNTER.get(key, 0) + 1
        if _PRUNE_COUNTER[key] >= _PRUNE_EVERY:
            _PRUNE_COUNTER[key] = 0
            self.prune()
        return entry

    def prune(self) -> int:
        """删除早于 `retention_days` 的记录（原子重写）；返回删除条数。"""
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, self.retention_days))
        path = self.path
        if not path.is_file():
            return 0
        try:
            lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        except OSError:
            return 0
        kept: list[str] = []
        removed = 0
        for line in lines:
            try:
                item = json.loads(line)
            except ValueError:
                kept.append(line)  # 坏行保留（不静默丢数据）
                continue
            ts = _parse_ts(item.get("ts")) if isinstance(item, dict) else None
            if ts is not None and ts < cutoff:
                removed += 1
            else:
                kept.append(line)
        if removed:
            atomic_write_text(path, "\n".join(kept) + ("\n" if kept else ""))
        return removed

    def usage(self, *, days: int = DEFAULT_RETENTION_DAYS) -> dict[str, Any]:
        """四维聚合（tool / category / ok / day）+ 总量；只算最近 `days` 天。"""
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))
        by_tool: dict[str, int] = {}
        by_category: dict[str, int] = {}
        by_ok: dict[str, int] = {"ok": 0, "failed": 0}
        by_day: dict[str, int] = {}
        total_tokens = 0
        total = 0
        for rec in read_jsonl_bounded(self.path, _USAGE_READ_CAP):
            ts = _parse_ts(rec.get("ts"))
            if ts is not None and ts < cutoff:
                continue
            total += 1
            tool = str(rec.get("tool", "")) or "(unknown)"
            by_tool[tool] = by_tool.get(tool, 0) + 1
            cat = str(rec.get("category", "")) or ("OK" if rec.get("ok") else "UNKNOWN")
            by_category[cat] = by_category.get(cat, 0) + 1
            by_ok["ok" if rec.get("ok") else "failed"] += 1
            day = str(rec.get("ts", ""))[:10] or "unknown"
            by_day[day] = by_day.get(day, 0) + 1
            try:
                total_tokens += int(rec.get("tokens", 0) or 0)
            except (TypeError, ValueError):
                pass
        return {
            "ok": True,
            "days": int(days),
            "total": total,
            "total_tokens": total_tokens,
            "success_rate": round(by_ok["ok"] / total, 4) if total else None,
            "by_tool": dict(sorted(by_tool.items(), key=lambda kv: -kv[1])),
            "by_category": dict(sorted(by_category.items(), key=lambda kv: -kv[1])),
            "by_ok": by_ok,
            "by_day": dict(sorted(by_day.items())),
        }


def _parse_ts(value: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def summarize_tools(events: Iterable[dict[str, Any]]) -> dict[str, int]:
    """小工具：按 tool 计数（供调用方即席统计）。"""
    out: dict[str, int] = {}
    for ev in events:
        key = str(ev.get("tool", "")) or "(unknown)"
        out[key] = out.get(key, 0) + 1
    return out
