"""v1.4.0 待批队列：部署前写操作先入队，人审后回放落盘。

设计来源：调研 §2.6（PendingOp + 执行/批准物理分离）。

- `PendingOp` 把「执行 payload」「展示 summary」「blast_radius」**同包落盘**，
  批准时回放 payload，避免二次渲染漂移；
- **approve / reject 只在服务层（HTTP / CLI）**，MCP 面绝不注册 approve（agent 不能自批）；
- per-agent 熔断：同一 `submitted_by`（无 subject 时退为 `"anonymous"`）待批数达上限即拒。

落盘形态：`{root}/pending/<op_id>.json`，原子写（`.tmp` + `os.replace`）。
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

__all__ = [
    "DEFAULT_AGENT_LIMIT",
    "PendingOp",
    "PendingStore",
    "PendingLimitExceeded",
]

#: per-agent 待批上限（autoflow 同款默认值）
DEFAULT_AGENT_LIMIT = 20


class PendingLimitExceeded(Exception):
    """某 agent 待批数达上限，拒绝新提交（请先审批 / 拒绝积压）。"""


@dataclass
class PendingOp:
    op_id: str
    tool: str
    payload: dict[str, Any]
    summary: str
    blast_radius: dict[str, Any]
    submitted_by: str
    submitted_at: str
    agent: str = field(default="")


def _op_to_dict(op: PendingOp) -> dict[str, Any]:
    return {
        "op_id": op.op_id,
        "tool": op.tool,
        "payload": op.payload,
        "summary": op.summary,
        "blast_radius": op.blast_radius,
        "submitted_by": op.submitted_by,
        "submitted_at": op.submitted_at,
        "agent": op.agent,
    }


class PendingStore:
    """待批队列存储（JSON 文件，单目录锁串行，pending 规模小足够）。"""

    def __init__(self, root: str | Path, max_per_agent: int = DEFAULT_AGENT_LIMIT) -> None:
        self.root = Path(root)
        self.pending_dir = self.root / "pending"
        self.max_per_agent = int(max_per_agent)
        self._lock = threading.Lock()

    def _path(self, op_id: str) -> Path:
        return self.pending_dir / f"{op_id}.json"

    def _atomic_write(self, path: Path, data: Mapping[str, Any]) -> None:
        # R-19：随机 tmp 名（避免并发写同名 .tmp）；mkstemp 的 fd 必须显式关闭（AF-9 半修）
        import os
        import tempfile
        fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(data, ensure_ascii=False, indent=2))
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
            os_replace(Path(tmp_path), path)
        finally:
            try:
                os.close(fd)  # 兜底关闭描述符：with 已关则吞 EBADF
            except OSError:
                pass
            if os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def _agent_of(self, path: Path) -> str:
        try:
            return str(json.loads(path.read_text(encoding="utf-8")).get("agent") or "anonymous")
        except (OSError, ValueError):
            return "anonymous"

    def submit(
        self,
        tool: str,
        payload: Mapping[str, Any],
        *,
        summary: str,
        blast_radius: Mapping[str, Any],
        submitted_by: str,
        agent_key: str | None = None,
    ) -> PendingOp:
        """入队一条待批操作；per-agent 熔断达上限抛 `PendingLimitExceeded`。"""
        agent = agent_key or submitted_by or "anonymous"
        with self._lock:
            if self.max_per_agent > 0 and self.pending_dir.is_dir():
                count = sum(
                    1 for p in self.pending_dir.glob("*.json") if self._agent_of(p) == agent
                )
                if count >= self.max_per_agent:
                    raise PendingLimitExceeded(
                        f"agent {agent!r} 待批数已达上限 {self.max_per_agent}，"
                        f"拒绝新提交（请先审批 / 拒绝积压）"
                    )
            op = PendingOp(
                op_id=uuid.uuid4().hex[:16],
                tool=tool,
                payload=dict(payload),
                summary=summary,
                blast_radius=dict(blast_radius),
                submitted_by=submitted_by,
                submitted_at=datetime.now(timezone.utc).isoformat(),
                agent=agent,
            )
            self.pending_dir.mkdir(parents=True, exist_ok=True)
            self._atomic_write(self._path(op.op_id), _op_to_dict(op))
            return op

    def list(self, agent: str | None = None) -> list[dict[str, Any]]:
        """列出待批（审批视图：含 summary / blast_radius / 完整 payload 供回放）。"""
        if not self.pending_dir.is_dir():
            return []
        out: list[dict[str, Any]] = []
        for p in sorted(self.pending_dir.glob("*.json"), key=lambda x: x.stat().st_mtime):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if agent is not None and d.get("agent") != agent:
                continue
            out.append(d)
        return out

    def load(self, op_id: str) -> dict[str, Any] | None:
        """读取单条待批（含 payload，供 approve 回放）。"""
        p = self._path(op_id)
        if not p.is_file():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def delete(self, op_id: str) -> bool:
        p = self._path(op_id)
        if p.is_file():
            p.unlink()
            return True
        return False


def os_replace(src: Path, dst: Path) -> None:
    """跨平台原子替换（延迟导入，避免与 store 的 `_atomic_write` 命名冲突）。"""
    import os

    os.replace(src, dst)
