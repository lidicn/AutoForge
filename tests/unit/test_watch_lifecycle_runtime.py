#!/usr/bin/env python3
"""watch 生命周期的**运行期**腿：锁的持有与释放都取内核真值，不取"发过信号就算数"。

起因（第六轮审计 ARCH-10 的最后一格，对账文件 §ARCH-10 表第四行）：
「没有生命周期测试（`stop` 之后收没收干净）——ARCH-06 的门是**静态**面；运行期"stop 后状态真清
干净"的断言腿确实还没有」。现读复核这条成立：`test_watch_lifecycle_r6_audit.py` 全部把
`svc.os.kill` 换成 `_KillSpy()`、把 `time.sleep` 换成 no-op，PID 是编的 4242，**锁文件从头到尾
没有真持有者**（`_lock_is_free` 那句"锁文件不存在也算空"因此恒为真）；
`test_serve_exit_reap.py` 用的是真子进程，但它自己的 docstring 写明"它不参与协调锁"。
⇒ 两文件合起来证的是身份核验与收尾动作，证不出**锁真的随进程死亡而释放、而不是靠删文件**。

本文件补的就是这一格，口径如下：

- 持锁者是 `sys.executable` 起的**真进程**，走 `af_flock.FileLock.acquire()`（POSIX `flock`／
  Windows `msvcrt.locking`），sidecar 由它自己 `_stamp()` 出真身份；
- 判"锁空／不空"只用 `svc._lock_is_free()`，那是另开句柄去探的内核真值，⛔ 不信盘上文本；
- 每腿都有前置自证（precondition）：子进程起好后必须先读出"锁被别的进程持着"，否则这条腿测的是空气；
- 唯一被打桩的是 `os.kill`（D1 那一格）——**信号投递**这一步在测试里不能拿真 PID 做实验，
  而锁的状态一律真值。D2 那一格连信号都不打桩：走 POSIX 真 `fork()` 继承 fd。

D2 只在 POSIX 跑（`os.name == "posix"`）：Windows 上 `os.kill(pid, 15)` 等价 `TerminateProcess`，
"目标还活着但收不到信号"这一形状在本机造不出来，⛔ 不在这里假装两边一样。CI 跑 Linux，所以那格
在远端是真的执行过的。
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import time

import pytest

from autoforge import af_service as svc

SRC = pathlib.Path(__file__).resolve().parents[2] / "src"

#: 子进程脚本：`mode` 决定它是否真去拿协调锁。`hold` 模式拿完锁就活着，直到被收。
_CHILD = r"""
import os, sys, time, pathlib
sys.path.insert(0, sys.argv[3])
from autoforge.af_flock import FileLock

root = pathlib.Path(sys.argv[1])
mode = sys.argv[2]
lock = root / "watch.lock"
fl = FileLock(lock, timeout=20, info={"graph": "runtime-leg"})
if mode == "fork-hold":
    fl.acquire()
    (root / "watch.pid").write_text(str(os.getpid()), encoding="utf-8")
    pid = os.fork()
    if pid > 0:
        # 直子进程退出：`watch.pid` 里那个 PID 从此"已经不在"，
        # 而锁由继承了同一份 fd 的孙进程继续持着——这是内核事实，不是模拟。
        (root / "grand_pid.txt").write_text(str(pid), encoding="utf-8")
        os._exit(0)
    (root / "child_ready.txt").write_text("grandchild", encoding="utf-8")
    while True:
        time.sleep(0.2)
fl.acquire()
if mode == "hold":
    (root / "watch.pid").write_text(str(os.getpid()), encoding="utf-8")
elif mode == "second":
    (root / "second_ready.txt").write_text("acquired-after-stop", encoding="utf-8")
(root / "child_ready.txt").write_text(mode, encoding="utf-8")
while True:
    time.sleep(0.2)
"""

_LIVE: list[subprocess.Popen] = []


def _spawn(root: pathlib.Path, mode: str) -> subprocess.Popen:
    log = (root / f"child-{mode}.log").open("wb")
    proc = subprocess.Popen(
        [sys.executable, "-c", _CHILD, str(root), mode, str(SRC)],
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    _LIVE.append(proc)
    return proc


def _wait_for(path: pathlib.Path, *, deadline: float = 20.0, root: pathlib.Path | None = None) -> None:
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        if path.exists():
            return
        time.sleep(0.05)
    tail = ""
    if root is not None:
        logs = list(root.glob("child-*.log"))
        tail = "\n".join(f.read_text(errors="replace") for f in logs)[:1200]
    raise AssertionError(f"子进程没有写出 {path.name}（{deadline}s 超时）——这一腿根本没开始\n{tail}")


def _kill(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


@pytest.fixture(autouse=True)
def _cleanup_children():
    _LIVE.clear()
    yield
    for proc in _LIVE:
        _kill(proc)


@pytest.fixture
def root(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    (store / "watch.log").write_text("", encoding="utf-8")
    return store


@pytest.fixture
def fast_budget(monkeypatch):
    """缩短"等锁空闲"的退避表，⛔ 不改判定形状（真值仍来自 `_lock_is_free`）。"""
    monkeypatch.setattr(svc, "_WATCH_EXIT_WAIT_S", (0.05, 0.05, 0.05, 0.05))


# ── A. 真持锁进程被收掉之后：内核说空了、诊断件清了、锁文件与 inode 都没动 ──

def test_a_real_lock_holder_is_reaped_and_the_kernel_confirms_the_lock_is_free(root, fast_budget):
    holder = _spawn(root, "hold")
    _wait_for(root / "child_ready.txt", root=root)
    _wait_for(root / "watch.lock.info", root=root)
    lock = root / "watch.lock"
    #: 前置自证：这一腿必须真的有人在持锁，否则"停完之后锁空了"是空气判断
    assert svc._lock_is_free(lock) is False, "子进程没有真的持住协调锁——这条腿测的是空气"
    inode_before = lock.stat().st_ino
    assert int((root / "watch.pid").read_text()) == holder.pid

    res = svc.stop_watch(store_root=str(root))

    assert res["identity"] == "verified", res
    assert res["signal"] == "delivered", res
    assert res["ok"] is True and res["exit"] == "exited", res
    holder.wait(timeout=5)
    assert holder.poll() is not None, "返回了 exited，进程却还活着"
    assert svc._lock_is_free(lock) is True, "子进程已退场，内核仍报锁被持着"
    #: BUG-04 的运行期形态：锁文件必须还在，且**同一枚 inode**（删文件从来不是释放锁的手段）
    assert lock.exists(), "协调锁文件被删掉了（BUG-04）"
    assert lock.stat().st_ino == inode_before, "锁文件被换过 inode——下一个 watcher 会'拿锁成功'而旧持有者还在"
    assert not (root / "watch.lock.info").exists()
    assert not (root / "watch.pid").exists()
    assert res["cleanup"] == {
        "sidecar_removed": True,
        "pid_file_removed": True,
        "lock_file_kept": True,
        "errors": [],
    }, res


# ── B. 释放来自进程死亡，不是来自删文件：下一枚真 watcher 能在同一 inode 上拿到锁 ──

def test_the_next_real_holder_acquires_the_same_inode_without_anything_being_deleted(root, fast_budget):
    first = _spawn(root, "hold")
    _wait_for(root / "child_ready.txt", root=root)
    lock = root / "watch.lock"
    inode_before = lock.stat().st_ino

    assert svc.stop_watch(store_root=str(root))["ok"] is True
    first.wait(timeout=5)

    second = _spawn(root, "second")
    _wait_for(root / "second_ready.txt", root=root)
    _wait_for(root / "child_ready.txt", root=root)
    assert svc._lock_is_free(lock) is False, "第二枚 watcher 没真的拿到锁"
    assert lock.stat().st_ino == inode_before, "锁的 inode 换了——那意味着中间有人删过锁文件"


# ── C. 「发不动」与「PID 早就不在」都不算退场证据：证据只有内核那把锁 ──

def test_a_pid_that_is_already_gone_still_has_to_free_the_lock_before_it_counts(root, fast_budget, monkeypatch):
    """现场形状：诊断件里的 PID 已经不在，而协调锁仍被**另一个活进程**持着。

    ⚠️ 这里被打桩的只有"信号投递"那一步——测试不能拿一个可能被复用过的真 PID 去做 `os.kill`
    实验（Windows 上 `os.kill(pid,15)` 会 `TerminateProcess`，命中复用后的无关进程就是不可撤销的误杀，
    而那正是 BUG-06 禁掉的事）。锁的持与否全程是内核真值（真子进程持着）。

    修前形状（本腿要扫出的那一格）：`ProcessLookupError` 分支直接写 `exit_state="exited"`，于是对外报
    `ok=true` 并把 sidecar／PID 一起删掉——下一个 watcher 起来抢锁撞上"仍被持着"，而诊断件已被这次
    "成功"清空，没人读得懂为什么。
    """
    holder = _spawn(root, "hold")
    _wait_for(root / "child_ready.txt", root=root)
    _wait_for(root / "watch.lock.info", root=root)
    lock = root / "watch.lock"
    assert svc._lock_is_free(lock) is False

    monkeypatch.setattr(svc.os, "kill", _raise(ProcessLookupError(3, "没有该进程")))
    res = svc.stop_watch(store_root=str(root))

    assert holder.poll() is None, "holder 被牵连了——这一腿从头到尾不许动它"
    assert svc._lock_is_free(lock) is False, "锁仍被真进程持着：这是本腿的判据真值"
    assert res["ok"] is False, f"锁还被人持着，却报了成功清场：{res}"
    assert res["exit"] == "exit_unconfirmed" and res["reason"] == "exit_unconfirmed", res
    assert res["signal"] == "already_gone", res
    assert res["cleanup"] == {
        "sidecar_removed": False,
        "pid_file_removed": False,
        "lock_file_kept": True,
        "errors": [],
    }, res
    assert (root / "watch.lock.info").exists() and (root / "watch.pid").exists(), res


def test_a_kill_the_platform_refuses_generically_is_not_reported_as_delivered(root, fast_budget, monkeypatch):
    """Windows 对不存在的 PID 发 15 抛的不是 `ProcessLookupError`，而是别的 `OSError`。

    修前那一格会把异常直接抛出 `stop_watch`（HTTP 面读成 500），而 `start_watch` 早就把同一条链
    收在 `(ProcessLookupError, PermissionError, OSError)` 里——两脸口径不一致。修后：如实记
    `signal=not_delivered(...)`，仍问内核，仍一个诊断件都不许多清。
    """
    holder = _spawn(root, "hold")
    _wait_for(root / "child_ready.txt", root=root)
    _wait_for(root / "watch.lock.info", root=root)
    lock = root / "watch.lock"
    assert svc._lock_is_free(lock) is False

    monkeypatch.setattr(svc.os, "kill", _raise(OSError(22, "Windows 上对失效句柄的写法")))
    res = svc.stop_watch(store_root=str(root))

    assert holder.poll() is None
    assert res["ok"] is False and res["exit"] == "exit_unconfirmed", res
    assert res["signal"].startswith("not_delivered("), res
    assert (root / "watch.lock.info").exists() and (root / "watch.pid").exists(), res


def _raise(exc: BaseException):
    def _kill(_pid: int, _sig: int) -> None:
        raise exc

    return _kill


# ── D. 连信号都不打桩（POSIX）：真发 SIGTERM，锁被继承 fd 的孙进程持着 ──

@pytest.mark.skipif(os.name != "posix", reason="Windows 的 os.kill(15) 等价 TerminateProcess，造不出"
                                              "「目标收不到信号」这一形状；CI 在 Linux 上真跑这一腿")
def test_a_sigterm_that_leaves_the_lock_held_by_an_inheriting_child_is_not_a_clean_stop(root, fast_budget):
    direct = _spawn(root, "fork-hold")
    _wait_for(root / "child_ready.txt", root=root)
    _wait_for(root / "grand_pid.txt", root=root)
    lock = root / "watch.lock"
    assert direct.wait(timeout=10) == 0, "直子进程没退出，形状就不成立"
    grand_pid = int((root / "grand_pid.txt").read_text())
    assert svc._lock_is_free(lock) is False, "孙进程没继承到锁 fd——这一腿的前置塌了"

    try:
        res = svc.stop_watch(store_root=str(root))
        assert svc._lock_is_free(lock) is False
        assert res["ok"] is False and res["exit"] == "exit_unconfirmed", res
        #: 信号**真的发出去了**（直子进程当场死，见上面 `direct.wait()`），而孙进程持着继承来的
        #: 锁 fd 继续活着 ⇒ `delivered` 这一档同样不构成退场证据。
        assert res["signal"] == "delivered", res
        assert (root / "watch.lock.info").exists() and (root / "watch.pid").exists(), res
    finally:
        try:
            os.kill(grand_pid, 9)
        except ProcessLookupError:
            pass
