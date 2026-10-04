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
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

__all__ = ["owner_id", "FileLock", "SERVE_LOCK_NAME", "serve_lock_path"]

# 单写者租约的文件名只有一个真源：serve 用它抢锁，`live_run` 用它判"别人在写"。
# 抄第二份就是本仓那族"名单手抄"接缝（工具名单 / extras 名 / HTTP 路径）的又一个实例。
SERVE_LOCK_NAME = ".serve.lock"


def serve_lock_path(store_root: str | Path) -> Path:
    return Path(store_root) / SERVE_LOCK_NAME

try:  # POSIX
    import fcntl as _fcntl

    _POSIX = True
except ImportError:  # Windows
    _fcntl = None
    import msvcrt as _msvcrt

    _POSIX = False

_OWNER: str | None = None

# 本进程已持有的锁（按解析后的路径记）。锁挂在"打开文件描述"上，同进程另开一个句柄去探
# 会被**自己**挡下；不记这张表，serve 自己会把"生产写者在线"读成"别人在写"，把 503 打给自己。
_LOCAL_HELD: "set[str]" = set()


def _key(path: Path) -> str:
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


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
        self._owner_thread: int | None = None  # 持有线程 ident，同线程可重入

    # ── 非阻塞 ──
    def try_acquire(self) -> bool:
        """尝试拿锁；成功 True，被占用 False。"""
        cur = threading.get_ident()
        # 同线程内重入语义：宽松处理（同线程重入不重新 flock）
        if self._fd is not None and self._owner_thread == cur:
            _LOCAL_HELD.add(_key(self.path))
            return True
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
        self._owner_thread = cur
        _LOCAL_HELD.add(_key(self.path))
        self._stamp()
        return True

    def held_by_other(self) -> bool:
        """锁是否被**别的过程**拿着：本进程持有 ⇒ False；能拿到并立刻放开 ⇒ False；拿不到 ⇒ True。

        裁定 20261004 §一 1 A："只 check 不 acquire"——MCP 面不抢租约、只读它，所以这里
        探到就放，且**不写 sidecar**（探测者若盖章，会把生产 serve 的持有者诊断覆盖成自己）。
        判据只认内核锁：进程崩溃时内核自动释放，陈旧 sidecar 不参与判定。
        """
        if _key(self.path) in _LOCAL_HELD:
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT)
        try:
            if _POSIX:
                _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            else:
                os.lseek(fd, 0, os.SEEK_SET)
                _msvcrt.locking(fd, _msvcrt.LK_NBLCK, 1)
        except (BlockingIOError, OSError):
            return True
        finally:
            if not _POSIX:
                try:
                    os.lseek(fd, 0, os.SEEK_SET)
                    _msvcrt.locking(fd, _msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            os.close(fd)
        return False

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
        self._owner_thread = None
        _LOCAL_HELD.discard(_key(self.path))
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
            try:
                tmp.chmod(0o600)  # ADM B-14：锁信息文件限权
            except OSError:
                pass
            os.replace(tmp, self._info_path)
        except OSError:
            pass

    def __enter__(self) -> "FileLock":
        self.acquire()
        return self

    def __exit__(self, *_exc) -> None:
        self.release()
