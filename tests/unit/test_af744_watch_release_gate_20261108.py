# -*- coding: utf-8 -*-
"""AF-744 判据 · `forge watch` 关停「永不带活写者放租约」（2026-11-08）· 卡名 `AF卡_af744_watch_release`。

**现场**（台账 §1.183，基线 `667e9ce`，`src/autoforge/af_cli.py` 的 `forge watch` 收尾）：

```python
finally:
    stop.set()
    ticker.join(timeout=2.0)          # ← 等 2 秒
    if _reconcile is not None:
        _reconcile.join(timeout=2.0)
    coord.release()                   # ← 不看 is_alive 就放租约
```

tick 卡在 HA/MQTT IO 上超过 2 秒时：租约已经放掉、旧 daemon ticker 还在写 store；
紧接着 `af_save` 热重载拉起的下一个 watch 进程立刻抢到锁 ⇒ **两个写者同写 store**
（违反 `SERVE_LOCK_NAME` 自立的约）。

**修法（选 B，PM 卡面给的二选一）**：join 预算用尽后验活——**任何一个后台写线程还活着就跳过
`release()`**，靠进程退出时 fd 关闭释放内核锁（flock 挂在打开文件描述上，进程退出即释放）。
选 B 不选 A（验活硬门等/抛）的理由：A 在"SIGTERM 后 tick 卡死"时会**卡住关停**，docker 的
宽限期一到就是 SIGKILL，反而把"优雅关闭"退化成崩溃路径；B 在任何情况下都让进程走出去，
且严格满足「不带活写者放租约」这一条不变量。跳过时必须**响一声**（`WATCH_RELEASE_SKIPPED`），
⛔ 不静默。

**牙**（群规⑧）：G1/G3 是"活写者 ⇒ 不释放"的正腿；G2 是反腿（死写者 ⇒ 照常释放），
两条合起来才不是恒 False 的假绿；G4 用**真线程**卡在 Event 上（不靠 mock），把窗口确定化。
"""

from __future__ import annotations

import logging
import threading
import time

import pytest

from autoforge import af_cli


class _FakeThread:
    """假后台线程：可控 is_alive，记录 join 预算。"""

    def __init__(self, name: str, alive: bool) -> None:
        self.name = name
        self._alive = alive
        self.join_calls: list[float] = []

    def join(self, timeout=None):
        self.join_calls.append(timeout)

    def is_alive(self) -> bool:
        return self._alive


class _FakeCoord:
    def __init__(self) -> None:
        self.releases = 0

    def release(self) -> None:
        self.releases += 1


# ── G1 活写者 ⇒ ⛔ 不释放租约（并响一声）──────────────────────────────

def test_g1_live_writer_blocks_lease_release(caplog):
    coord = _FakeCoord()
    ticker = _FakeThread("ticker", alive=True)
    with caplog.at_level(logging.WARNING, logger="autoforge.cli"):
        released = af_cli._release_lease_after_stop(coord, ticker, None)
    assert released is False
    assert coord.releases == 0, "还有活写者时⛔ 不许放租约（会引出双写者）"
    lines = [r.getMessage() for r in caplog.records if "WATCH_RELEASE_SKIPPED" in r.getMessage()]
    assert lines, "跳过释放必须响一声（⛔ 不许静默改成『反正进程要退了』）"
    assert "ticker" in lines[0], lines[0]
    assert ticker.join_calls == [af_cli.WATCH_SHUTDOWN_JOIN_S], ticker.join_calls


# ── G2 反腿：写者都退场 ⇒ 照常释放（防"恒 False"假绿）──────────────────

def test_g2_dead_writers_still_release(caplog):
    coord = _FakeCoord()
    ticker = _FakeThread("ticker", alive=False)
    reconcile = _FakeThread("reconcile", alive=False)
    with caplog.at_level(logging.WARNING, logger="autoforge.cli"):
        released = af_cli._release_lease_after_stop(coord, ticker, reconcile)
    assert released is True
    assert coord.releases == 1, "写者都退场 ⇒ 必须照常放租约（这是正常关停路径）"
    assert not [r for r in caplog.records if "WATCH_RELEASE_SKIPPED" in r.getMessage()]
    assert ticker.join_calls == [af_cli.WATCH_SHUTDOWN_JOIN_S]
    assert reconcile.join_calls == [af_cli.WATCH_SHUTDOWN_JOIN_S]


# ── G3 IR 对账线程也算写者（卡面只写了 ticker，这里钉全）────────────────

def test_g3_live_reconcile_also_blocks_release(caplog):
    coord = _FakeCoord()
    ticker = _FakeThread("ticker", alive=False)
    reconcile = _FakeThread("reconcile", alive=True)
    with caplog.at_level(logging.WARNING, logger="autoforge.cli"):
        released = af_cli._release_lease_after_stop(coord, ticker, reconcile)
    assert released is False
    assert coord.releases == 0, "对账线程同样在写 store 侧车，⛔ 不许当它不存在"
    line = [r.getMessage() for r in caplog.records if "WATCH_RELEASE_SKIPPED" in r.getMessage()][0]
    assert "reconcile" in line, line


# ── G4 真线程确定化：卡在 Event 上的 ticker 必须挡住释放 ────────────────

def test_g4_real_stuck_thread_blocks_release():
    coord = _FakeCoord()
    gate = threading.Event()
    release_gate = threading.Event()

    def _stuck_tick() -> None:
        release_gate.set()          # 已进入 tick 体（模拟卡在 IO 里）
        gate.wait(timeout=10)       # 一直卡着，直到测试放行

    t = threading.Thread(target=_stuck_tick, name="stuck-ticker", daemon=True)
    t.start()
    assert release_gate.wait(timeout=5), "测试前置：ticker 没起来"
    try:
        # 预算给 0.05s：join 会用尽，线程仍活 ⇒ 必须跳过释放
        released = af_cli._release_lease_after_stop(coord, t, None, join_s=0.05)
        assert released is False, "真线程还卡在 IO 里时⛔ 不许放租约"
        assert coord.releases == 0
    finally:
        gate.set()
        t.join(timeout=5)
    # 放行之后线程退场 —— 证明上一步的 False 不是"线程本来就没起"
    assert not t.is_alive()
    assert af_cli._release_lease_after_stop(coord, t, None, join_s=0.05) is True
    assert coord.releases == 1


# ── G5 源码钉：收尾段必须走统一入口，且验活先于释放 ─────────────────────

def test_g5_source_pins():
    import inspect

    src = inspect.getsource(af_cli._release_lease_after_stop)
    i_alive = src.find("is_alive()")
    i_release = src.find("coord.release()")
    assert i_alive != -1 and i_release != -1, src
    assert i_alive < i_release, "验活必须**先于**释放（顺序反了就是旧 bug）"
    assert "WATCH_RELEASE_SKIPPED" in src, "跳过释放必须留具名码"

    watch_src = inspect.getsource(af_cli.watch)
    assert "_release_lease_after_stop(" in watch_src, "watch 收尾必须走统一入口"
    # 逐行判：只禁「裸调 release」这一行代码；docstring/注释里提到旧 bug 的那句不算
    naked = [ln for ln in watch_src.splitlines()
             if ln.strip() == "coord.release()" and not ln.strip().startswith("#")]
    assert not naked, (
        f"watch 里⛔ 不许再裸调 coord.release()（绕过验活就是旧 bug 复现）：{naked}"
    )
