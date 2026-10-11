#!/usr/bin/env python3
"""`af_cli.reap_watch_child` 的行为腿（裁定 20261011 §3 Q7.1／Q7.2，执行记录 §二之一百一十七）。

静态门（`check_process_model.py` 判据 D/F）只能证明「那枚钩子指名了那把收子进程的手」，证不出
「退出时子进程**真的**死了、文件真的清干净了、收不完时那句话真的打得出来」。所以这里用**真子进程**
跑八格：

1. 写档（拿到单写者锁的那一侧）退出：watcher 真的退场，sidecar／PID 文件真的被清；
2. 只读降级档：**一个信号都不发**，别人的 watcher 必须还活着；
3. 两条腿（`finally` 与 `atexit`）只真收一次：第二次的返回值就是证据（≈4.4 秒预算不重复付）；
4. 手工 `forge watch` 起的（没有 PID 文件）：不碰、不收、也不假装收过；
5. PID 与 sidecar 对不上：`stop_watch` 拒杀 ⇒ 这里如实报「没收干净」，一个无关 PID 都不许碰；
6. 收不完（`exit_unconfirmed`）：那句话必须打到 stderr——serve 的 stderr 就是容器日志；
7. 收尾链自己炸了：异常既不能打崩退出路径，也不能被吞成"什么都没发生"；
8. `serve` 真的把那枚钩子注册进 `atexit`，且注册的是那把收子进程的手、参数带着 store。

每格都自己清场：子进程在 fixture 的 `finally` 里兜底 terminate，绝不把孤儿留给下一轮。
"""

from __future__ import annotations

import atexit
import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from autoforge import af_cli, af_service

#: sidecar 的 owner 形状是 `{hostname}-{pid}-{uuid8}`（`af_flock.owner_id`）——尾段必须是 8 位十六进制，
#: 否则 `_owner_pid_from_sidecar` 拆不出身份，`stop_watch` 会拒杀（那是另一格，本文件不混进来）。
TAIL = "cafe1234"


def spawn_watcher_stub(tmp_path: Path) -> subprocess.Popen:
    """起一枚会活到被收为止的子进程，充当 watcher。

    它不参与协调锁（锁文件不存在＝`_lock_is_free` 直接读真），本文件测的是**收**这条链的身份核验
    与收尾动作，不是锁本身。
    """
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time\nwhile True: time.sleep(0.2)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _LIVE.append(proc)
    return proc


_LIVE: list[subprocess.Popen] = []


@pytest.fixture(autouse=True)
def _clear_children():
    _LIVE.clear()
    af_cli._SERVE_REAP_DONE.clear()
    yield
    for proc in _LIVE:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    af_cli._SERVE_REAP_DONE.clear()


def fabric(tmp_path: Path, pid: int | None, *, sidecar: bool = True) -> Path:
    """在 store 目录上摆出「watcher 正在跑」的现场：PID 文件 + sidecar。"""
    root = tmp_path / "store"
    root.mkdir(exist_ok=True)
    if pid is not None:
        (root / "watch.pid").write_text(str(pid), encoding="utf-8", newline="\n")
    if sidecar:
        owner = f"{socket.gethostname()}-{pid}-{TAIL}" if pid is not None else "manual"
        (root / "watch.lock.info").write_text(
            json.dumps({"owner": owner, "graph": "g-demo", "pid": pid}), encoding="utf-8", newline="\n"
        )
    return root


# ── 1. 写档：真的收掉 ──

def test_reap_kills_the_child_and_clears_files(tmp_path: Path, capfd):
    proc = spawn_watcher_stub(tmp_path)
    root = fabric(tmp_path, proc.pid)

    result = af_cli.reap_watch_child(str(root), False, where="finally")

    assert result is not None and result["ok"] is True, result
    assert result["pid"] == proc.pid
    assert result["identity"] == "verified"
    proc.wait(timeout=5)
    assert proc.poll() is not None                      # 不是"发了信号就算数"：进程真的没了
    assert not (root / "watch.pid").exists()
    assert not (root / "watch.lock.info").exists()
    out = capfd.readouterr()
    # 两张脸分开钉：收干净是例行信息（stdout），收不干净才进 stderr——serve 的 stderr 就是容器日志，
    # 把例行话也打进去会让真正的"没停成"淹没在噪音里。
    assert "已收掉 watcher" in out.out and "走 finally 这条腿" in out.out
    assert out.err == ""


# ── 2. 只读降级档：一发都不发 ──

def test_readonly_never_reaps(tmp_path: Path):
    proc = spawn_watcher_stub(tmp_path)
    root = fabric(tmp_path, proc.pid)

    assert af_cli.reap_watch_child(str(root), True) is None
    assert proc.poll() is None                          # 别人的 watcher 还活着——这是这一格的全部意义
    assert (root / "watch.pid").exists()
    assert (root / "watch.lock.info").exists()
    assert not af_cli._SERVE_REAP_DONE.is_set()          # 只读档没"认领过"，写档仍可继续收


# ── 3. 两条腿只真收一次 ──

def test_second_leg_is_a_no_op(tmp_path: Path):
    proc = spawn_watcher_stub(tmp_path)
    root = fabric(tmp_path, proc.pid)

    first = af_cli.reap_watch_child(str(root), False, where="finally")
    second = af_cli.reap_watch_child(str(root), False, where="atexit")

    assert first is not None and first["ok"] is True
    assert second is None                               # Event 短路：≈4.4 秒的等待预算不重复付
    assert af_cli._SERVE_REAP_DONE.is_set()


# ── 4. 没有 PID 文件：不碰、不收、不装 ──

def test_no_pid_file_leaves_everything_alone(tmp_path: Path):
    proc = spawn_watcher_stub(tmp_path)
    root = fabric(tmp_path, None)                       # 手工 `forge watch`：只有 sidecar，没有 PID 文件

    assert af_cli.reap_watch_child(str(root), False) is None
    assert proc.poll() is None
    assert (root / "watch.lock.info").exists()
    assert not af_cli._SERVE_REAP_DONE.is_set()


def test_identity_mismatch_is_refused_not_forced(tmp_path: Path, capfd):
    """PID 文件与 sidecar 对不上＝身份未知：`stop_watch` 拒杀，这里把拒杀如实报出来，不升 SIGKILL。"""
    proc = spawn_watcher_stub(tmp_path)
    root = fabric(tmp_path, proc.pid)
    (root / "watch.pid").write_text(str(proc.pid + 1), encoding="utf-8", newline="\n")

    result = af_cli.reap_watch_child(str(root), False)

    assert result is not None and result["ok"] is False
    assert result["reason"] == "refused_to_kill"
    assert result["why"].startswith("pid_mismatch_with_sidecar")
    assert proc.poll() is None                          # 一个无关 PID 都没被碰
    assert (root / "watch.pid").exists() and (root / "watch.lock.info").exists()
    assert "没把 watcher 收干净" in capfd.readouterr().err


# ── 5. 收不完：那句话必须看得见 ──

def test_exit_unconfirmed_is_reported_and_files_kept(tmp_path: Path, monkeypatch, capfd):
    proc = spawn_watcher_stub(tmp_path)
    root = fabric(tmp_path, proc.pid)
    # 锁始终读成"还被持着" ⇒ 走完退避表就是 exit_unconfirmed。空表让这一格不必真等 4.4 秒，
    # 而 `for…else` 的落点（预算花完）与真实现同一条代码路径。
    monkeypatch.setattr(af_service, "_WATCH_EXIT_WAIT_S", ())

    result = af_cli.reap_watch_child(str(root), False)

    assert result is not None and result["ok"] is False
    assert result["reason"] == "exit_unconfirmed"
    assert (root / "watch.pid").exists() and (root / "watch.lock.info").exists()
    assert result["cleanup"]["lock_file_kept"] is True
    err = capfd.readouterr().err
    assert "没把 watcher 收干净" in err and "exit_unconfirmed" in err


def test_reap_never_breaks_the_exit_path(tmp_path: Path, monkeypatch, capfd):
    """收尾路径上的异常既不能把 serve 的退出打崩，也不能被吞成"什么都没发生"。"""
    proc = spawn_watcher_stub(tmp_path)
    root = fabric(tmp_path, proc.pid)

    def boom(*a, **kw):
        raise RuntimeError("sidecar 读一半炸了")

    monkeypatch.setattr(af_service, "stop_watch", boom)

    result = af_cli.reap_watch_child(str(root), False)   # 不抛＝这一格的一半
    assert result is None
    err = capfd.readouterr().err
    assert "收 watcher 失败" in err and "RuntimeError" in err and "sidecar 读一半炸了" in err
    assert proc.poll() is None                          # 没收成功也如实：没人替它假装收过


# ── 6. 钩子真的挂在 serve 的退出路径上（现场跑一次，不靠静态门）──

def test_serve_installs_the_hook_on_a_real_store(tmp_path: Path, monkeypatch):
    """`serve` 里那句 `atexit.register` 不是修辞：现跑一次退出路径，注册对象与参数都读得出。

    这一格补的是静态门读不出的那一面：`atexit.register(reap_watch_child, …)` 写在 `serve` 体内，
    一旦参数顺序或名字改错，静态门还能绿（它只查首参指名），退出时却收不到东西。
    真起 uvicorn 没必要，所以把四张外部脸（桥／日志 filter／建 app／单写者锁）换成 fake，
    `uvicorn.run` 换成直接返回——等价于 Ctrl+C 之后的正常收尾。
    """
    import types
    from autoforge import af_flock

    calls: list[tuple[str, bool, str]] = []
    registered: list[tuple] = []

    def fake_reap(store_root: str, readonly: bool, where: str = "") -> None:
        calls.append((store_root, readonly, where))
        return None

    class FakeFileLock:
        def __init__(self, path, **kw):
            self.path = path

        def try_acquire(self) -> bool:
            return True

        def holder(self) -> dict:
            return {"owner": "fake"}

    def fake_register(fn, *args, **kw):
        registered.append((fn, args, kw))

    fake_api = types.ModuleType("autoforge.af_api")
    fake_api.build_app = lambda *a, **kw: object()
    fake_uvicorn = types.ModuleType("uvicorn")
    fake_uvicorn.run = lambda *a, **kw: None

    monkeypatch.setattr(af_cli, "reap_watch_child", fake_reap)
    monkeypatch.setattr(af_cli, "_start_linkage_bridge", lambda **kw: None)
    monkeypatch.setattr(af_cli, "install_access_log_token_mask", lambda: None)
    monkeypatch.setattr(af_flock, "FileLock", FakeFileLock)
    monkeypatch.setattr(atexit, "register", fake_register)   # serve 里那句 `import atexit` 走 sys.modules，patch 真身才拦得住
    monkeypatch.setitem(sys.modules, "autoforge.af_api", fake_api)
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)

    root = tmp_path / "store"
    root.mkdir()

    af_cli.serve(store_root=str(root), examples="", ui_dir="", ui_user_dir="")

    assert len(registered) == 1, registered                     # 钩子只注册一枚，不重复挂
    fn, args, _kw = registered[0]
    assert fn is fake_reap                                     # 注册对象＝那把收子进程的手（serve 全局名解析到的那枚）
    assert list(args) == [str(root), False]                     # store 跟着走；写档（try_acquire 成功）才挂
    assert calls == [(str(root), False, "finally")]             # finally 那条腿同步调到，参数一致
