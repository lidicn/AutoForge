"""MA 洞察提案的**只落盘、不可部署**队列。

裁定依据：`20261002-homesdk记账与AF-DB-DPP六件-裁定` §三 ④A——
"先持久化到独立队列，仍不可直接部署（新增只落盘的 ask 档，approve 之后才进 `af_pending`）"。

为什么不是原来的进程内 `ProposalManager`：那条队列**重启即清零**，MA 投来的洞察
在无人应答时随进程一起消失，"投过 / 没投过"事后无从查证。
为什么不是 `af_pending`：那是**可执行**队列（人点 approve 就落盘部署）。
本模块刻意不持有 deployer，也不 import 任何部署链——对端来源与人工来源在部署面上
不同权，是裁定的安全边界，不是实现细节。

分层：本模块在 L1，只依赖标准库、`af_time` 与 `af_feedback` 的时间垫片；`approve` 后的交接
由 L2 显式调用 `af_service.submit_pending` 完成（反向依赖会被 `scripts/check_imports.py` 判红）。
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .af_feedback import clock_now
from .af_store import atomic_write_text
from .af_time import SystemTimeSource

__all__ = ["InsightRecord", "InsightQueue", "PersistentInsightSink", "InsightQueueFull"]

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"

DEFAULT_LIMIT = 500


class InsightQueueFull(RuntimeError):
    """队列满：**拒绝**入队并报错，不静默丢消息（丢了就等于 MA 从没投过）。"""


@dataclass(frozen=True)
class InsightRecord:
    """一条落盘的洞察提案。`suggested_ir` 可为 None——那代表 MA 只给了自然语言，
    approve 时不得凭空造 IR（要造就走 `af_draft`，那是另一次人批）。"""

    proposal_id: str
    hypothesis_id: str
    natural_language: str
    conf: float
    source: str
    received_at: float
    suggested_ir: dict[str, Any] | None = None
    status: str = STATUS_PENDING
    decided_at: float | None = None
    decided_by: str = ""
    reason: str = ""
    transport: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    """落盘一条记录：随机 tmp 名 + fsync（`af_store.atomic_write_text`）。

    原来是固定名 `path.with_suffix(".tmp")` + 裸 `write_text`：同一个 proposal_id
    被并发复写（pending 与 decided 同名、或同一记录二次提交）时两边写同一个 tmp，
    交错内容被最后一次 `os.replace` 装上，读侧按坏 JSON 跳过 ⇒ 这条提案静默消失。
    """
    atomic_write_text(
        path, json.dumps(dict(payload), ensure_ascii=False, sort_keys=True)
    )


class InsightQueue:
    """文件队列：`{root}/pending/{ms}-{id}.json`，判定后整体移到 `{root}/decided/`。"""

    def __init__(
        self,
        root: Path | str,
        *,
        clock: Any | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> None:
        self.root = Path(root)
        self.pending_dir = self.root / "pending"
        self.decided_dir = self.root / "decided"
        self.clock = clock if clock is not None else SystemTimeSource()
        self.limit = int(limit)
        self.unreadable: list[str] = []

    # ---- 写 ---- #

    def append(self, record: InsightRecord) -> InsightRecord:
        self.pending_dir.mkdir(parents=True, exist_ok=True)
        if len(self._paths(self.pending_dir)) >= self.limit:
            raise InsightQueueFull(
                f"洞察提案队列已满（{self.limit} 条）：请先清理 {self.pending_dir} 或调高 limit，"
                f"本条未入队（不静默丢弃）"
            )
        _atomic_write(self._path(record, self.pending_dir), record.to_dict())
        return record

    def move_to(self, record: InsightRecord, *, status: str, decided_by: str, reason: str = "") -> InsightRecord:
        """把一条提案改判并移出 pending。返回带判定字段的新记录（原记录不可变）。"""
        src = self._find(record.proposal_id, self.pending_dir)
        if src is None:
            raise KeyError(f"pending 里没有提案 {record.proposal_id}")
        decided = InsightRecord(
            **{
                **record.to_dict(),
                "status": status,
                "decided_at": clock_now(self.clock),
                "decided_by": decided_by,
                "reason": reason,
            }
        )
        self.decided_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write(self._path(decided, self.decided_dir), decided.to_dict())
        src.unlink()
        return decided

    # ---- 读 ---- #

    def list_pending(self) -> list[InsightRecord]:
        return self._read_all(self.pending_dir)

    def list_decided(self) -> list[InsightRecord]:
        return self._read_all(self.decided_dir)

    def get(self, proposal_id: str) -> InsightRecord | None:
        for directory in (self.pending_dir, self.decided_dir):
            path = self._find(proposal_id, directory)
            if path is not None:
                return self._load(path)
        return None

    def stats(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "pending": len(self._paths(self.pending_dir)),
            "decided": len(self._paths(self.decided_dir)),
            "limit": self.limit,
            "unreadable": list(self.unreadable),
        }

    # ---- 内部 ---- #

    @staticmethod
    def _paths(directory: Path) -> list[Path]:
        return sorted(p for p in directory.glob("*.json")) if directory.is_dir() else []

    @staticmethod
    def _path(record: InsightRecord, directory: Path) -> Path:
        return directory / f"{int(record.received_at * 1000):013d}-{record.proposal_id}.json"

    def _find(self, proposal_id: str, directory: Path) -> Path | None:
        for path in self._paths(directory):
            if path.name.endswith(f"-{proposal_id}.json"):
                return path
        return None

    def _load(self, path: Path) -> InsightRecord | None:
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
            return InsightRecord(**body)
        except (OSError, ValueError, TypeError) as exc:
            # 记账而不是咽下：坏文件会让"这条投过"变成"这条没投过"，必须看得见
            self.unreadable.append(f"{path.name}: {type(exc).__name__}: {exc}")
            return None

    def _read_all(self, directory: Path) -> list[InsightRecord]:
        out = []
        for path in self._paths(directory):
            rec = self._load(path)
            if rec is not None:
                out.append(rec)
        return out


class PersistentInsightSink:
    """`af_mqtt_bridge` 的 `proposal_sink`：只做"接收 + 落盘"，**没有任何部署把手**。

    `submit()` 的签名与 `ProposalManager.submit()` 对齐，桥侧无需分支；返回 `InsightRecord`
    （桥只读 `proposal_id`）。
    """

    def __init__(self, queue: InsightQueue) -> None:
        self.queue = queue
        self.errors: list[str] = []

    def submit(
        self,
        *,
        hypothesis_id: str,
        natural_language: str,
        conf: float,
        suggested_ir: Mapping[str, Any] | None = None,
        source: str = "ma",
        proposal_id: str | None = None,
        transport: Mapping[str, Any] | None = None,
    ) -> InsightRecord:
        pid = proposal_id or f"ins-{os.getpid():x}-{time.time_ns():x}"
        record = InsightRecord(
            proposal_id=pid,
            hypothesis_id=str(hypothesis_id),
            natural_language=str(natural_language),
            conf=float(conf),
            source=str(source),
            received_at=clock_now(self.queue.clock),
            suggested_ir=dict(suggested_ir) if suggested_ir else None,
            transport=dict(transport or {}),
        )
        self.queue.append(record)
        return record

    def stats(self) -> dict[str, Any]:
        return {**self.queue.stats(), "submit_errors": list(self.errors)}
