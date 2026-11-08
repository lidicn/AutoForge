# -*- coding: utf-8 -*-
"""AF-743 判据 · flock 释放顺序 / 持有者诊断不可覆盖（2026-11-08）· 卡名 `AF卡_af743_flock_order`。

**两处缺陷**（台账 §1.182，基线 `667e9ce`）：

1. `FileLock.release()` 先 `_LOCAL_HELD.discard(...)`、**后**才 `flock(LOCK_UN)`。那个窗口里
   本进程的锁牌已经摘了、内核 flock 还攥着；此刻**同进程另一个线程**调 `held_by_other()`
   会另开 fd 去 non-blocking 抢锁——flock 的粒度是**打开文件描述**，同进程异 fd 一样互斥 ⇒
   抢不到 ⇒ 读成「别人在写」（serve 租约读侧 / `live_run` 写者判定的偶发自误判）。
   修法：unlock → close → discard，且 discard 落在 `finally`（异常路径同样要对齐内核态）。
2. `_stamp()` 写的是 `{"owner": …, "acquired_at": …, **self.info}` ⇒ 调用方的 `info`
   能**覆盖** `owner`／`acquired_at` 两个诊断字段，`holder()` 于是报出调用方随便写的人/时间。
   修法：`{**self.info, "owner": …, "acquired_at": …}`（info 铺底、诊断字段后置）。

**牙**（群规⑧）：F1 用**可控假内核**（`flock` 以布尔模拟"同一把锁被另一个 fd 占着"）+ 在
`LOCK_UN` 上注入 0.3s 延迟，把窗口从"偶发"变成**确定可命中**：旧顺序下探针必读 True，
新顺序下必读 False。F2 钉异常路径也要摘牌（否则反向失真：永远报「没人写」）。
F3 是覆盖腿（正例：owner/acquired_at 不可盖，普通键仍保留）。F4 是源码顺序钉。
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any

import pytest

from autoforge import af_flock


class _FakeKernel:
    """模拟 flock 的**打开文件描述级**语义：同一把锁被另一个 fd 占着就抢不到。

    `unlock_delay` 用来把 release 的窗口拉长成确定化窗口——真实内核上这个窗口是微秒级，
    单测里抓不住（这正是这条缺陷长期没被发现的原因）。
    """

    LOCK_EX = 1
    LOCK_NB = 2
    LOCK_UN = 4

    def __init__(self, unlock_delay: float = 0.3, unlock_raises: bool = False) -> None:
        self.held = False
        self.unlock_delay = unlock_delay
        self.unlock_raises = unlock_raises
        self.events: list[str] = []

    def flock(self, fd: int, op: int) -> None:
        if op == (self.LOCK_EX | self.LOCK_NB):
            self.events.append("try")
            if self.held:
                raise BlockingIOError(11, "resource temporarily unavailable")
            self.held = True
            return
        if op == self.LOCK_UN:
            self.events.append("unlock-enter")
            time.sleep(self.unlock_delay)  # 窗口：牌已摘/未摘，内核仍持有
            if self.unlock_raises:
                raise OSError("unlock 失败（注入）")
            self.held = False
            self.events.append("unlock-done")
            return
        raise AssertionError(f"意外 op={op}")


@pytest.fixture()
def fake_kernel(monkeypatch):
    def _install(**kw):
        kernel = _FakeKernel(**kw)
        monkeypatch.setattr(af_flock, "_POSIX", True)
        monkeypatch.setattr(af_flock, "_fcntl", kernel)
        return kernel

    return _install


def _local_held(path) -> bool:
    return af_flock._key(path) in af_flock._LOCAL_HELD


# ── F1 释放窗口里 held_by_other() 不许把自己的锁读成「别人在写」 ──────────────

def test_f1_release_window_never_looks_like_other_writer(tmp_path, fake_kernel):
    kernel = fake_kernel(unlock_delay=0.3)
    path = tmp_path / ".serve.lock"
    lock = af_flock.FileLock(path)
    assert lock.try_acquire() is True
    assert kernel.held is True
    assert _local_held(path) is True

    probed: list[bool] = []

    def probe() -> None:
        time.sleep(0.1)  # 落在 release() 的 unlock 窗口里（此时 unlock 还在 sleep）
        probed.append(af_flock.FileLock(path).held_by_other())

    t = threading.Thread(target=probe)
    t.start()
    lock.release()
    t.join(timeout=5)

    assert probed == [False], (
        f"释放窗口里探针读到了 {probed}——旧顺序（先摘牌后解锁）会在这里读成『别人在写』："
        f"events={kernel.events}"
    )
    # 事件序必须真的含一次 unlock（说明不是靠"提前摘牌"蒙过去的）
    assert "unlock-enter" in kernel.events and "unlock-done" in kernel.events, kernel.events
    assert kernel.events.index("unlock-enter") > kernel.events.index("try"), kernel.events
    assert kernel.held is False
    assert _local_held(path) is False, "释放完成后锁牌必须摘掉（否则 held_by_other 永远报没人写）"


def test_f1b_order_is_unlock_then_close_then_discard(tmp_path, fake_kernel):
    """源码顺序钉：discard 必须在 unlock 之后（顺序反了 F1 会红，这条钉住实现别回退）。"""
    import inspect

    src = inspect.getsource(af_flock.FileLock.release)
    i_unlock = src.find("LOCK_UN")
    i_close = src.find("os.close(fd)")
    i_discard = src.find("_LOCAL_HELD.discard(_key(self.path))")
    assert -1 not in (i_unlock, i_close, i_discard), src
    assert i_unlock < i_close < i_discard, (
        f"顺序必须是 unlock → close → discard（实得 unlock={i_unlock} close={i_close} discard={i_discard}）"
    )
    assert "finally" in src[i_unlock:], "discard 必须落在 finally（异常路径也要摘牌）"


# ── F2 异常路径：unlock 抛错也要摘牌（否则反向失真）────────────────────────

def test_f2_unlock_error_still_discards_and_closes(tmp_path, fake_kernel):
    kernel = fake_kernel(unlock_delay=0.0, unlock_raises=True)
    path = tmp_path / ".serve.lock"
    lock = af_flock.FileLock(path)
    assert lock.try_acquire() is True
    with pytest.raises(OSError):
        lock.release()
    assert lock._fd is None, "异常路径也要把 fd 置 None（否则下一次 release 会二次关句柄）"
    assert _local_held(path) is False, (
        "unlock 抛错时也必须摘牌——close 已经让内核释放锁，表里留牌就会永远报『没人写』"
    )


# ── F3 持有者诊断不可被 info 覆盖 ─────────────────────────────────────────

def test_f3_info_cannot_override_diagnostics(tmp_path, fake_kernel):
    fake_kernel(unlock_delay=0.0)
    path = tmp_path / ".serve.lock"
    lock = af_flock.FileLock(path, info={
        "owner": "SPOOFED",
        "acquired_at": "1999-01-01T00:00:00+00:00",
        "graph": "study_ac_smart_v6",
    })
    try:
        assert lock.try_acquire() is True
        holder = lock.holder()
    finally:
        lock.release()
    assert holder.get("owner") == af_flock.owner_id(), (
        f"`info` ⛔ 不许覆盖 owner（实得 {holder.get('owner')!r}）——holder() 会指向错误的人"
    )
    assert holder.get("owner") != "SPOOFED"
    assert holder.get("acquired_at") != "1999-01-01T00:00:00+00:00", (
        "`acquired_at` 同属本模块诊断字段，⛔ 不许被 info 覆盖（否则事故时间线错）"
    )
    assert holder.get("graph") == "study_ac_smart_v6", "调用方的业务键仍要保留"


# ── F4 回归：本进程持有 ⇒ False；释放后可再拿；重入语义不变 ─────────────────

def test_f4_holder_semantics_regression(tmp_path, fake_kernel):
    kernel = fake_kernel(unlock_delay=0.0)
    path = tmp_path / ".serve.lock"
    lock = af_flock.FileLock(path)
    assert lock.try_acquire() is True
    assert af_flock.FileLock(path).held_by_other() is False, "本进程持有 ⇒ 读侧必须 False"

    # 同线程重入（既有语义）
    assert lock.try_acquire() is True
    lock.release()
    assert _local_held(path) is False
    # 另一把锁对象可以立刻拿到（不是"释放后仍被我占着"）
    other = af_flock.FileLock(path)
    assert other.try_acquire() is True
    other.release()
    assert kernel.held is False


def test_f5_sidecar_is_json_with_expected_keys(tmp_path, fake_kernel):
    fake_kernel(unlock_delay=0.0)
    path = tmp_path / ".serve.lock"
    lock = af_flock.FileLock(path, info={"graph": "g"})
    try:
        lock.try_acquire()
        raw = json.loads(lock._info_path.read_text(encoding="utf-8"))
    finally:
        lock.release()
    for key in ("owner", "acquired_at", "graph"):
        assert key in raw, f"sidecar 缺键 {key}：{raw}"
    assert raw["owner"] == af_flock.owner_id()
