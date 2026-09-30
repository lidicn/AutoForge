"""P1 —— 实例持久化与崩溃恢复（`persist=true`）。

只对 `persist=true` 的自动化的**非终态**实例（`active`/`suspended`）落盘，终态即删除。
落盘形态基于 `InstanceContext.to_dict()`（可序列化红线），额外补两个**墙钟**字段：

- `saved_at_wall`：本次落盘时刻（ISO）
- `due_at_wall`  ：实例级定时器到期的**墙钟**时刻（ISO，可空）

为什么定时器要用墙钟存：`monotonic()` 在进程重启后归零、跨进程不可比；只有墙钟能在
重启后换算回新的 `monotonic()`。这与"计时器一律用 monotonic"并不矛盾——持久化边界
恰恰是唯一必须做时钟换算的地方。

恢复契约（IR §7.1）：**必须重新取快照并丢弃旧快照**——由 `InstanceManager.attach()`
负责；本模块只重建 `Instance` 对象本身。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from .af_flock import owner_id
from .af_instance import Instance, InstanceContext, InstanceTimer
from .af_ir import Automation
from .af_store import restore_context
from .af_time import TimeSource

__all__ = ["PersistStore", "record_instance", "restore_instance", "INSTANCES_SUBDIR"]

#: 落盘记录所在子目录：`{root}/instances/{instance_id}.json`
INSTANCES_SUBDIR = "instances"
_SUFFIX = ".json"

#: B 增强（DCD 裁定二）：落盘记录校验和字段名（对齐 af_store 的 SHA256）。
_SHA256_KEY = "_sha256"
_logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# 墙钟工具
# ─────────────────────────────────────────────────────────────────────


def _iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()


def _parse_iso(text: Any) -> datetime | None:
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(str(text))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _safe(name: Any) -> str:
    cleaned = "".join(c if c.isalnum() or c in "-_." else "_" for c in str(name)).strip("_")
    return cleaned or "instance"


# ─────────────────────────────────────────────────────────────────────
# 记录完整性（B 增强：SHA256 校验和，对齐 af_store）
# ─────────────────────────────────────────────────────────────────────

def _record_checksum(record: Mapping[str, Any]) -> str:
    """对记录内容（不含校验和字段本身）算 SHA256，用于读时验证完整性。"""
    return hashlib.sha256(
        json.dumps(dict(record), ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _verify_record(record: Mapping[str, Any]) -> bool:
    """校验 `_sha256`；无校验和字段（旧格式）视为通过（向后兼容）。"""
    if _SHA256_KEY not in record:
        return True
    stored = record[_SHA256_KEY]
    stripped = {k: v for k, v in record.items() if k != _SHA256_KEY}
    return _record_checksum(stripped) == stored


# ─────────────────────────────────────────────────────────────────────
# 记录 ⇄ 实例
# ─────────────────────────────────────────────────────────────────────


def record_instance(instance: Instance, clock: TimeSource) -> dict[str, Any]:
    """把实例转成可落盘记录（上下文 + 墙钟到期边界）。"""
    now_wall = clock.now()
    due_at_wall: str | None = None
    if instance.timer is not None:
        remaining = max(0.0, instance.timer.due_at - clock.monotonic())
        due_at_wall = _iso(now_wall + timedelta(seconds=remaining))
    record = instance.to_dict()
    record["saved_at_wall"] = _iso(now_wall)
    record["due_at_wall"] = due_at_wall
    return record


def restore_instance(
    record: Mapping[str, Any], automation: Automation, clock: TimeSource
) -> Instance:
    """从落盘记录重建**可运行**实例（定时器换算回当前单调时钟）。

    到期时刻已过 → `due_at` 落在过去，`InstanceManager.due_timers()` 会立即命中，
    下一次 `Runtime.tick()` 即触发 `on_timeout`（这就是"崩溃期间错过的超时"）。
    """
    ctx: InstanceContext = restore_context(record)
    now_wall = clock.now()
    mono = clock.monotonic()

    created_mono = mono
    created_at = _parse_iso(ctx.created_at)
    if created_at is not None:
        created_mono = mono - (now_wall - created_at).total_seconds()

    instance = Instance(ctx=ctx, automation=automation, created_monotonic=created_mono)

    due_at = _parse_iso(record.get("due_at_wall"))
    if due_at is not None:
        kind = "timeout"
        if ctx.timers and isinstance(ctx.timers[0], Mapping):
            kind = str(ctx.timers[0].get("kind", "timeout"))
        instance.timer = InstanceTimer(
            node_id=ctx.current_node,
            due_at=mono + (due_at - now_wall).total_seconds(),
            kind=kind,
        )
    return instance


# ─────────────────────────────────────────────────────────────────────
# 存储
# ─────────────────────────────────────────────────────────────────────


class PersistStore:
    """目录级实例持久化：`{root}/instances/{instance_id}.json`，原子写。

    v0.9.0 实例归属与租约仲裁：
    - 每条落盘记录带 `owner`（进程身份）与 `lease_until_wall`（租约到期墙钟）；
    - `claims(record, clock)`：记录无主 / 本进程所有 / 租约已过期 → 可接管；
      租约仍在其他进程手上 → 不可恢复（避免双进程同时驱动同一实例）。
    """

    #: 租约时长（秒）；持有进程每次落盘都会续租
    DEFAULT_LEASE_S = 60.0

    def __init__(self, root: str | Path, owner: str | None = None, lease_s: float = DEFAULT_LEASE_S):
        self.root = Path(root)
        self.owner = owner or owner_id()
        self.lease_s = float(lease_s)

    @property
    def directory(self) -> Path:
        return self.root / INSTANCES_SUBDIR

    def _path(self, instance_id: str) -> Path:
        return self.directory / f"{_safe(instance_id)}{_SUFFIX}"

    def save(self, instance: Instance, clock: TimeSource) -> Path:
        """落盘一个实例（原子替换：崩溃时不会留半截文件），同时续租归属。"""
        self.directory.mkdir(parents=True, exist_ok=True)
        record = record_instance(instance, clock)
        record["owner"] = self.owner
        record["lease_until_wall"] = _iso(clock.now() + timedelta(seconds=self.lease_s))
        # B 增强：SHA256 校验和（对齐 af_store），读时校验 + 损坏段跳过
        record[_SHA256_KEY] = _record_checksum(record)
        path = self._path(instance.instance_id)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            tmp.chmod(0o600)  # ADM B-14：敏感实例文件限权
        except OSError:
            pass
        os.replace(tmp, path)
        return path

    def claims(self, record: Mapping[str, Any], clock: TimeSource) -> bool:
        """租约仲裁：本进程可否接管这条落盘实例。

        无主（旧版本记录）/ 本进程所有 / 租约已过期 → True；
        租约仍在其他进程手上 → False（恢复时应跳过且**不删文件**）。
        """
        owner = str(record.get("owner", ""))
        if not owner or owner == self.owner:
            return True
        lease = _parse_iso(record.get("lease_until_wall"))
        if lease is None:
            return True
        return clock.now() >= lease

    def remove(self, instance_id: str) -> bool:
        try:
            self._path(instance_id).unlink()
            return True
        except FileNotFoundError:
            return False

    def load(self, instance_id: str) -> dict[str, Any] | None:
        path = self._path(instance_id)
        if not path.is_file():
            return None
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return None
        if not _verify_record(record):
            _logger.warning("af_persist: 落盘记录校验和不匹配，拒收（%s）", path)
            return None
        return record

    def records(self) -> list[dict[str, Any]]:
        """读取全部落盘记录；坏文件/校验和不匹配跳过，不让一条损坏拖垮整轮恢复。"""
        if not self.directory.is_dir():
            return []
        out: list[dict[str, Any]] = []
        for path in sorted(self.directory.glob(f"*{_SUFFIX}")):
            if path.name.endswith(".tmp"):
                continue
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            if not _verify_record(record):
                _logger.warning("af_persist: 落盘记录校验和不匹配，跳过（%s）", path)
                continue
            out.append(record)
        return out

    def clear(self) -> int:
        """删除全部落盘记录，返回删除数量（测试/运维用）。"""
        if not self.directory.is_dir():
            return 0
        removed = 0
        for path in self.directory.glob("*"):
            if path.is_file():
                path.unlink()
                removed += 1
        return removed
