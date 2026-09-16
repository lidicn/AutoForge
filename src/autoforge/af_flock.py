"""v0.9.0 跨进程原语：进程身份 + 跨进程文件锁（零外部依赖）。

- `owner_id()`：进程唯一标识（hostname-pid-uuid 片段），用于实例归属 / 写者署名 / 协调锁。
- `FileLock`： advisory 文件锁，POSIX 用 `fcntl.flock`，Windows 用 `msvcrt.locking`；
  进程崩溃 / 退出时内核自动释放（无陈旧锁问题），锁文件内容记录最后持有者信息供诊断。

设计边界：锁是**建议性**的（advisory）——所有写方（GraphStore / PersistStore /
watch 协调）都经本模块拿锁才能互斥；绕过者不受保护。
"""

from __future__ import annotations

import json
import os
import socket
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = ["owner_id", "FileLock"]

try:  # POSIX
    import fcntl as _fcntl

    _POSIX = True
except ImportError:  # Windows
    _fcntl = None
    import msvcrt as _msvcrt

    _POSIX = False

_OWNER: str | None = None


def owner_id() -> str:
    """本进程唯一身份（进程内缓存）：`{hostname}-{pid}-{uuid8}`。"""
    global _OWNER
    if _OWNER is None:
        _OWNER = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    return _OWNER


class FileLock:
    """跨进程互斥锁（阻塞式 `acquire` 带超时；`try_acquire` 非阻塞）。

    用法：
        with FileLock(path):        # 超时抛 TimeoutError
            ...
    """

    def __init__(
        self,
        path: str | Path,
        timeout: float = 10.0,
        poll_s: float = 0.05,
        info: "Mapping[str, Any] | None" = None,
    ):
        self.path = Path(path)
        self.timeout = float(timeout)
        self.poll_s = max(poll_s, 0.001)
        self.info = dict(info) if info else {}
        self._fd: int | None = None

    # ── 非阻塞 ──
    def try_acquire(self) -> bool:
        """尝试拿锁；成功 True，被占用 False。"""
        if self._fd is not None:
            return True  # 已持有（同一线程内重入语义：宽松处理）
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT)
        try:
            if _POSIX:
                _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            else:
                os.lseek(fd, 0, os.SEEK_SET)
                _msvcrt.locking(fd, _msvcrt.LK_NBLCK, 1)
        except (BlockingIOError, OSError):
            os.close(fd)
            return False
        self._fd = fd
        self._stamp()
        return True

    # ── 阻塞（带超时）──
    def acquire(self) -> None:
        deadline = time.monotonic() + self.timeout
        while True:
            if self.try_acquire():
                return
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"获取文件锁超时（{self.timeout}s）：{self.path}，持有者：{self.holder()}"
                )
            time.sleep(self.poll_s)

    def release(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            if _POSIX:
                _fcntl.flock(fd, _fcntl.LOCK_UN)
            else:
                os.lseek(fd, 0, os.SEEK_SET)
                _msvcrt.locking(fd, _msvcrt.LK_UNLCK, 1)
        finally:
            os.close(fd)

    # ── 诊断 ──
    @property
    def _info_path(self) -> Path:
        """持有者信息 sidecar（Windows 字节区间锁会拒绝其他句柄读锁文件本体，
        故写入独立文件；进程崩溃后残留的是最后持有者，仅作诊断）。"""
        return self.path.with_suffix(self.path.suffix + ".info")

    def holder(self) -> dict[str, Any]:
        """最后持有者信息（诊断用；未取到返回 {}）。"""
        try:
            data = json.loads(self._info_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _stamp(self) -> None:
        """写持有者信息到 sidecar（best effort；失败不影响锁语义）。"""
        try:
            tmp = self._info_path.with_suffix(".info.tmp")
            tmp.write_text(
                json.dumps(
                    {
                        "owner": owner_id(),
                        "acquired_at": datetime.now(timezone.utc).isoformat(),
                        **self.info,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            os.replace(tmp, self._info_path)
        except OSError:
            pass

    def __enter__(self) -> "FileLock":
        self.acquire()
        return self

    def __exit__(self, *_exc) -> None:
        self.release()
