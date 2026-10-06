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
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

_logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_AGENT_LIMIT",
    "PendingOp",
    "PendingStore",
    "PendingLimitExceeded",
]

#: per-agent 待批上限（autoflow 同款默认值）
DEFAULT_AGENT_LIMIT = 20

#: 待批条目最长存活（秒）——过期自动清理，避免僵尸条目占着 per-agent 名额（审计 Medium）
DEFAULT_TTL_S = 7 * 24 * 3600

#: R4-02：跨实例共享的待批锁（模块级），避免每次 `PendingStore(root)` 新建实例时
#: 实例级 `threading.Lock()` 失效导致 per-agent 熔断被并发绕过。同进程有效；
#: 跨进程彻底的互斥需改用 FileLock（磁盘队列），后续可替换。
_PENDING_LOCK = threading.Lock()


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
        self._lock = _PENDING_LOCK  # R4-02：跨实例共享同一把锁

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
            # R4-01 修复：os.fdopen 已接管 fd 所有权，with 块结束即关闭 fd；
            # 此处严禁再次 os.close(fd)——并发下 fd 号被复用会误关他人文件描述符。
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
            self._sweep_locked(DEFAULT_TTL_S)  # 清掉僵尸条目，避免占名额
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
            except (OSError, ValueError) as exc:
                _logger.warning("pending list: 跳过并隔离损坏条目 %s: %s", p, exc)
                _quarantine_corrupt(p)
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
        except (OSError, ValueError) as exc:
            _logger.warning("pending load: 条目损坏，隔离 %s: %s", p, exc)
            _quarantine_corrupt(p)
            return None

    def delete(self, op_id: str) -> bool:
        p = self._path(op_id)
        if p.is_file():
            p.unlink()
            return True
        return False

    def _sweep_locked(self, max_age_s: float) -> int:
        """调用方持锁。删除过期僵尸待批，返回条数。"""
        if not self.pending_dir.is_dir():
            return 0
        import time
        now = time.time()
        removed = 0
        for p in self.pending_dir.glob("*.json"):
            try:
                sub = datetime.fromisoformat(
                    json.loads(p.read_text(encoding="utf-8"))["submitted_at"].replace("Z", "+00:00")
                ).timestamp()
            except (OSError, ValueError, KeyError) as exc:
                _logger.warning("pending sweep: 损坏条目无法判定 TTL，隔离 %s: %s", p, exc)
                _quarantine_corrupt(p)
                continue
            if now - sub > max_age_s:
                try:
                    p.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed

    def sweep(self, max_age_s: float = DEFAULT_TTL_S) -> int:
        """删除提交时间早于 max_age_s 的僵尸待批，返回清理条数。线程安全。"""
        with self._lock:
            return self._sweep_locked(max_age_s)


def os_replace(src: Path, dst: Path) -> None:
    """跨平台原子替换（延迟导入，避免与 store 的 `_atomic_write` 命名冲突）。"""
    import os

    # fixed-tmp: exempt(这一站只是 os.replace 的跨平台包装，tmp 名由调用方给，本函数无临时文件构造)
    os.replace(src, dst)


def _quarantine_corrupt(p: Path) -> None:
    """把损坏的待批文件重命名为 .corrupt 隔离，避免反复静默失败、留下证据并解除僵尸。"""
    # fixed-tmp: exempt(隔离损坏文件的重命名操作：源→.corrupt，非 tmp→正名的数据写入，同文件系统重命名是原子的)
    try:
        p.rename(p.with_suffix(".corrupt"))
    except OSError:
        pass
