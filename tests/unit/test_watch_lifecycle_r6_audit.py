"""watch 生命周期（第六轮审计 BUG-04／05／06／14）：不删锁、不猜 PID、清理失败要看得见。

四格同源于一处代码形状：`stop_watch`/`start_watch` 把"盘上有个数字"当成了"我知道那是谁"。

- BUG-04：`stop_watch` 里 `lock.unlink()` 删的是**别人正持着**的锁文件。锁挂在文件描述符上
  （`af_flock.FileLock`：POSIX `flock`／Windows `msvcrt.locking`），删文件不释放锁，只会让下一个
  watcher 在同一路径新建 inode 并"拿锁成功"→ 两个 watcher 同时监听，且没有任何日志记下这次失效。
  Windows 上更隐蔽：被锁文件删不掉抛 `PermissionError`，旧实现 `except OSError: pass` 吞掉，
  于是"清干净"与"什么都没清"对外逐字相同。
- BUG-06：`os.kill(读来的数字, 15)` 没有身份核验。PID 会复用，命中复用后的无关进程是不可撤销的
  误杀（Windows 上 `os.kill(...,15)` 等价 `TerminateProcess`，目标连忽略的机会都没有）。
  本文件锁的核验来源是系统自己的信号：sidecar 的 `owner` = `{hostname}-{pid}-{uuid8}`。
- BUG-05：探测 sidecar 用固定 15×1 秒——成功路径至少白等 1 秒，失败路径把一个 Starlette 同步
  线程池工位占满 15 秒。锁的是"首次探测提前到亚秒"与"预算数字只有一个真源"。
- BUG-14：`start_watch` 给子进程的日志句柄在父进程侧从不关闭；`Popen` 抛错那条路径同样漏。

`_lock_is_free` 只看**跨进程**持有（`af_flock._LOCAL_HELD` 会让本进程自持的锁短路报"没被占"），
所以"还持着"那一格是把这枚探针按 False 来模拟另一个进程；"已经空"那一格走真文件、真探针。
"""
from __future__ import annotations

import ast
import inspect
import json
import pathlib
import socket
import subprocess
import time
from typing import Any

import pytest

from autoforge import af_service as svc

IR = {"automations": [{"id": "study_spot_lamp_sync", "name": "书房射灯与显示器挂灯同步"}]}
STALE_GRAPH = "/data/tmpze7aycrz/deployed_ir.json"
HOST = socket.gethostname()


def _owner(pid: int, *, host: str = HOST, tail: str = "deadbeef") -> str:
    """`af_flock.owner_id()` 的形状：`{hostname}-{pid}-{uuid8}`。"""
    return f"{host}-{pid}-{tail}"


def _seed_store(
    root: pathlib.Path,
    *,
    owner_pid: int | None = 4242,
    host: str = HOST,
    pid_file_value: str | None = "4242",
    graph: str = STALE_GRAPH,
    with_lock_file: bool = True,
) -> dict[str, pathlib.Path]:
    paths = {
        "lock": root / "watch.lock",
        "info": root / "watch.lock.info",
        "pid": root / "watch.pid",
    }
    owner = _owner(owner_pid, host=host) if owner_pid is not None else "not-a-usable-identity"
    paths["info"].write_text(
        json.dumps({"owner": owner, "acquired_at": "2026-10-10T06:25:21+00:00", "graph": graph}),
        encoding="utf-8",
    )
    if with_lock_file:
        paths["lock"].write_text("", encoding="utf-8")
    if pid_file_value is not None:
        paths["pid"].write_text(pid_file_value, encoding="utf-8")
    return paths


class _FakeProc:
    def __init__(self, pid: int = 777, exit_code: int | None = None):
        self.pid = pid
        self.returncode = exit_code
        self._alive = exit_code is None

    def poll(self):
        return None if self._alive else self.returncode


class _KillSpy:
    def __init__(self, *, raise_: BaseException | None = None):
        self.calls: list[tuple[int, int]] = []
        self.raise_ = raise_

    def __call__(self, pid: int, sig: int) -> None:
        self.calls.append((pid, sig))
        if self.raise_ is not None:
            raise self.raise_


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _s: None)


@pytest.fixture
def root(tmp_path):
    (tmp_path / "watch.log").write_text("", encoding="utf-8")
    return tmp_path


# ── BUG-04：锁文件不是拿来删的 ──────────────────────────────────────────

def test_stop_watch_leaves_the_lock_file_on_disk_even_on_success(root, monkeypatch):
    """停成功也只清诊断件：`watch.lock` 必须原样在盘上（inode 不变才算互斥仍成立）。"""
    paths = _seed_store(root)
    kill = _KillSpy()
    monkeypatch.setattr(svc.os, "kill", kill)
    res = svc.stop_watch(store_root=str(root))

    assert res["ok"] is True, res
    assert kill.calls == [(4242, 15)], kill.calls
    assert paths["lock"].exists(), "stop_watch 不许删协调锁文件（BUG-04）"
    assert not paths["info"].exists() and not paths["pid"].exists(), res
    assert res["cleanup"]["lock_file_kept"] is True, res


def test_stop_watch_source_contains_no_unlink_of_the_lock():
    """反空洞：删锁这件事从 `stop_watch` 这一层彻底消失，不是"改了名字还在删"。

    按 AST 走调用点，不按名字 grep 整段源码——docstring 里那句"不再 unlink `watch.lock`"是说明，
    不是动作，把它算进代码面就成了自己判自己红（本仓 `NameError` 哨兵踩过同一课）。
    """
    tree = ast.parse(inspect.getsource(svc.stop_watch))
    unlinks = [
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "unlink"
    ]
    assert not unlinks, f"收尾一律走 `_cleanup_watch_files`，那里只清诊断件：{unlinks}"

    code_lines = [
        ln.strip()
        for ln in pathlib.Path(svc.__file__).read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    offenders = [ln for ln in code_lines if "watch.lock" in ln and ".unlink(" in ln]
    assert not offenders, f"全模块 CODE 口径不许再出现删协调锁那一步：{offenders}"


def test_cleanup_failure_is_named_instead_of_swallowed(root, monkeypatch):
    """旧实现 `except OSError: pass` 让"清干净"与"什么都没清"对外相同——现在必须具名报出。"""
    _seed_store(root)
    monkeypatch.setattr(svc.os, "kill", _KillSpy())

    def explode(self, *a, **k):
        raise PermissionError(13, "拒绝访问")

    monkeypatch.setattr(pathlib.Path, "unlink", explode)
    res = svc.stop_watch(store_root=str(root))
    assert res["ok"] is True, res
    assert res["cleanup"]["sidecar_removed"] is False, res
    assert res["cleanup"]["errors"], res
    assert any(err.startswith("sidecar:PermissionError") for err in res["cleanup"]["errors"]), res


# ── BUG-06：不按裸 PID 动刀 ────────────────────────────────────────────

def test_pid_file_and_sidecar_must_agree_before_any_kill(root, monkeypatch):
    """PID 复用/漂移的形状：两边数字不一致 ⇒ 一个信号都不发、一个文件都不删。"""
    paths = _seed_store(root, owner_pid=9999, pid_file_value="4242")
    kill = _KillSpy()
    monkeypatch.setattr(svc.os, "kill", kill)
    res = svc.stop_watch(store_root=str(root))

    assert kill.calls == [], "身份没核验过就不许 os.kill"
    assert res["ok"] is False and res["reason"] == "refused_to_kill", res
    assert res["why"].startswith("pid_mismatch_with_sidecar"), res
    assert paths["lock"].exists() and paths["info"].exists() and paths["pid"].exists(), res
    assert res["cleanup"]["sidecar_removed"] is False and res["cleanup"]["pid_file_removed"] is False


def test_pid_from_another_hostname_is_not_killed_locally(root, monkeypatch):
    """共享盘／跨机形态：容器里的小 PID 在宿主机上是别人。主机名对不上就拒杀。"""
    _seed_store(root, owner_pid=4242, host="elsewhere-box")
    kill = _KillSpy()
    monkeypatch.setattr(svc.os, "kill", kill)
    res = svc.stop_watch(store_root=str(root))
    assert kill.calls == [], kill.calls
    assert res["why"].startswith("pid_belongs_to_another_host"), res


def test_hand_watched_process_without_a_pid_file_is_not_killed_by_guesswork(root, monkeypatch):
    """手工 `forge watch` 起的 watcher 没有 PID 文件：拒杀并把该怎么停写进 hint。"""
    paths = _seed_store(root, pid_file_value=None)
    kill = _KillSpy()
    monkeypatch.setattr(svc.os, "kill", kill)
    res = svc.stop_watch(store_root=str(root))
    assert kill.calls == [], kill.calls
    assert res["why"] == "no_pid_file", res
    assert "forge watch" in res["hint"], res
    assert paths["info"].exists() and paths["lock"].exists(), res


@pytest.mark.parametrize(
    "owner, expected",
    [
        ("NAS-Server-4242-abcd1234", ("NAS-Server", 4242)),  # 主机名自带 `-`
        ("box-1-7-ffffffff", ("box-1", 7)),
        ("box-1-7-nothex8", None),        # uuid 片段不是 8 位十六进制
        ("box-onlyone", None),            # 段数不够
        ("box-notanumber-abcd1234", None),  # PID 段不是纯数字
        ("", None),
    ],
)
def test_sidecar_identity_is_parsed_from_the_right(owner, expected):
    """从右往左拆：只有尾两段的形状是稳的，主机名可以任意带 `-`。"""
    assert svc._owner_pid_from_sidecar(owner) == expected, owner


def test_exit_is_reported_unconfirmed_when_the_lock_is_still_held(root, monkeypatch):
    """发完信号不等于停完了：锁还被持着就报 `exit_unconfirmed`，诊断件一律留着。"""
    paths = _seed_store(root)
    monkeypatch.setattr(svc.os, "kill", _KillSpy())
    monkeypatch.setattr(svc, "_lock_is_free", lambda _p: False)
    res = svc.stop_watch(store_root=str(root))

    assert res["ok"] is False and res["reason"] == "exit_unconfirmed", res
    assert res["exit"] == "exit_unconfirmed", res
    assert paths["info"].exists() and paths["pid"].exists() and paths["lock"].exists(), res
    assert f"{svc._WATCH_EXIT_WAIT_TOTAL_S:.1f}" in res["error"], res


def test_permission_error_on_kill_is_named_not_treated_as_stopped(root, monkeypatch):
    paths = _seed_store(root)
    monkeypatch.setattr(svc.os, "kill", _KillSpy(raise_=PermissionError(13, "不允许")))
    res = svc.stop_watch(store_root=str(root))
    assert res["ok"] is False and res["reason"] == "kill_not_permitted", res
    assert paths["info"].exists() and paths["pid"].exists(), res


def test_process_lookup_error_means_already_gone_so_cleanup_is_safe(root, monkeypatch):
    _seed_store(root)
    monkeypatch.setattr(svc.os, "kill", _KillSpy(raise_=ProcessLookupError(3, "没有该进程")))
    res = svc.stop_watch(store_root=str(root))
    assert res["ok"] is True and res["exit"] == "exited", res
    assert not (root / "watch.pid").exists() and not (root / "watch.lock.info").exists(), res


# ── BUG-05：探测预算只有一个真源 ────────────────────────────────────────

def test_first_probe_is_sub_second(root, monkeypatch):
    """绝大多数成功启动在数百毫秒内写出 sidecar：固定 1 秒的粒度既慢又占线程池工位。"""
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        subprocess, "Popen", lambda cmd, **k: captured.update(cmd=cmd) or _FakeProc()
    )
    slept: list[float] = []

    def fake_sleep(delay_s: float):
        slept.append(delay_s)
        if len(slept) == 2:
            (root / "watch.lock.info").write_text(
                json.dumps({"owner": _owner(777), "acquired_at": "x", "graph": captured["cmd"][2]}),
                encoding="utf-8",
            )

    monkeypatch.setattr(time, "sleep", fake_sleep)
    res = svc.start_watch(IR, store_root=str(root), dry_live=True)
    assert res["ok"] is True, res
    assert slept[0] < 0.5, f"首次探测应当是亚秒，实测 {slept[0]}"
    assert len(slept) == 2, slept


def test_probe_schedule_is_monotonic_and_bounded():
    """退避表本身的两条形状：不减（越探越稀）+ 总预算严格小于旧实现的 15 秒。"""
    delays = svc._WATCH_PROBE_DELAYS_S
    assert all(b >= a for a, b in zip(delays, delays[1:])), delays
    assert sum(delays) == pytest.approx(svc._WATCH_PROBE_TOTAL_S), svc._WATCH_PROBE_TOTAL_S
    assert svc._WATCH_PROBE_TOTAL_S < 15.0, svc._WATCH_PROBE_TOTAL_S


def test_unregistered_error_names_the_same_budget_the_schedule_adds_up_to(root, monkeypatch):
    """报错里的秒数不许是手抄的第二份：它必须等于这张表加出来的那个数。"""
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **k: _FakeProc())
    res = svc.start_watch(IR, store_root=str(root), dry_live=True)
    assert res["ok"] is False and res["reason"] == "not_registered", res
    assert f"{svc._WATCH_PROBE_TOTAL_S:.1f} 秒" in res["error"], res["error"]


# ── BUG-06 的另一半脸：start_watch 停旧 watch 也要核验 ──────────────────

def test_start_watch_does_not_kill_an_unverified_previous_pid(root, monkeypatch):
    """重启一条 watch 之前，旧 PID 同样要过核验；核验不过就交给协调锁去仲裁。"""
    _seed_store(root, owner_pid=9999, pid_file_value="4242")
    kill = _KillSpy()
    monkeypatch.setattr(svc.os, "kill", kill)
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **k: _FakeProc())
    res = svc.start_watch(IR, store_root=str(root), dry_live=True)

    assert kill.calls == [], "旧实现会照着 PID 文件数字直接 os.kill"
    assert res["reason"] == "coord_lock_held_by_other", res
    assert res["previous_watch"]["pid"] is None, res
    assert res["previous_watch"]["identity"].startswith("pid_mismatch_with_sidecar"), res


def test_start_watch_kills_a_verified_previous_pid(root, monkeypatch):
    """正例腿：身份对得上时照杀——这条守卫不是把重启功能本身关掉。"""
    _seed_store(root, owner_pid=4242, pid_file_value="4242")
    kill = _KillSpy()
    monkeypatch.setattr(svc.os, "kill", kill)
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **k: _FakeProc(exit_code=2))
    svc.start_watch(IR, store_root=str(root), dry_live=True)
    assert kill.calls == [(4242, 15)], kill.calls


# ── BUG-14：父进程侧那份日志句柄要关掉 ─────────────────────────────────

class _FakeLog:
    def __init__(self, sink: dict):
        self._sink = sink

    def __enter__(self):
        self._sink["entered"] = True
        return self

    def __exit__(self, *exc):
        self._sink["closed"] = True
        return False


@pytest.fixture
def log_probe(monkeypatch):
    """只看 `mode == "ab"` 那一次 open（`read_text`/`write_text` 也走 `Path.open`，不能连它们一起替）。"""
    sink: dict[str, Any] = {"entered": False, "closed": False}
    real_open = pathlib.Path.open

    def fake_open(self, mode="r", *a, **k):
        if mode == "ab" and self.name == "watch.log":
            return _FakeLog(sink)
        return real_open(self, mode, *a, **k)

    monkeypatch.setattr(pathlib.Path, "open", fake_open)
    return sink


def test_start_watch_closes_the_log_handle_after_popen(root, monkeypatch, log_probe):
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **k: _FakeProc())
    svc.start_watch(IR, store_root=str(root), dry_live=True)
    assert log_probe["entered"] is True and log_probe["closed"] is True, log_probe


def test_start_watch_closes_the_log_handle_when_popen_raises(root, monkeypatch, log_probe):
    """异常路径同一条腿：旧写法 `Popen` 抛错时 `logf` 连引用计数回收都走不到。"""

    def boom(cmd, **k):
        raise OSError("forge 不在 PATH")

    monkeypatch.setattr(subprocess, "Popen", boom)
    res = svc.start_watch(IR, store_root=str(root), dry_live=True)
    assert res["ok"] is False and "启动 watch 失败" in res["error"], res
    assert log_probe["closed"] is True, log_probe


# ── 反证：这套核验不能对"什么都不改"放行 ───────────────────────────────

def test_stop_watch_still_returns_a_plain_refusal_for_a_foreign_owner(root):
    """裁定 20261004 §一 那格（按 owner 拒停）语义不能被新形状吃掉。"""
    _seed_store(root)
    res = svc.stop_watch(owner="somebody-else-1-1111aaaa", store_root=str(root))
    assert res["ok"] is False and "与请求" in res["error"], res


# ── BUG-06 的另一半：watcher 进程收到 SIGTERM 要走同一条收尾 ────────────

def test_watch_command_wires_sigterm_to_the_existing_graceful_path():
    """`forge watch` 的优雅收尾（停 ticker、放协调锁）本来就在 `finally` 里，缺的只是"被通知"。

    `os.kill(pid, 15)` 不会变成 KeyboardInterrupt，所以旧实现收到 SIGTERM 直接死在半路。
    这里按 AST 走嵌套定义（按 `tree.body` 直扫会静默漏掉函数体内的函数），断言两件事：
    装了 SIGTERM 处理器，且那个处理器把停机意图与异常同一条路走回 `except KeyboardInterrupt`。
    """
    from autoforge import af_cli

    tree = ast.parse(inspect.getsource(af_cli.watch))
    handlers = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_on_sigterm"
    ]
    assert len(handlers) == 1, [h.name for h in handlers]
    body_src = ast.dump(handlers[0])
    assert "stop" in body_src and "set" in body_src, "处理器要先置停机意图"
    raises = [n for n in ast.walk(handlers[0]) if isinstance(n, ast.Raise)]
    assert any(
        isinstance(n.exc, ast.Name) and n.exc.id == "KeyboardInterrupt" for n in raises
    ), "处理器必须 raise KeyboardInterrupt，才能走 `finally` 那条已存在的收尾"

    installs = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "signal"
    ]
    assert installs, "SIGTERM 处理器要在 watch 命令里真的装上"


# ── tick 自愈的声明与调用流对账（第二轮运行时审计⑨ ＋ 安全与暴露面第三轮"3+1 未接线"）──
_NOT_WIRED = ("没有调用者", "无调用者", "未接线")


def _src_dir() -> pathlib.Path:
    from autoforge import af_live

    return pathlib.Path(af_live.__file__).resolve().parent


def _docstrings_naming(name: str) -> list[tuple[str, str]]:
    """src/ 下所有**点到某枚符号的 docstring**（模块级与函数级都算）。"""
    found: list[tuple[str, str]] = []
    for py in sorted(_src_dir().glob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        docs = [ast.get_docstring(tree)]
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                docs.append(ast.get_docstring(node))
        found += [(py.name, d) for d in docs if d and name in d]
    return found


def _definition_docstring(func_name: str) -> str:
    """符号**自己那处定义**的 docstring（按名字找定义，不要求它在文里复述自己）。"""
    docs = []
    for py in sorted(_src_dir().glob("*.py")):
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
                docs.append(ast.get_docstring(node) or "")
    assert len(docs) == 1, f"`{func_name}` 在 src/ 里有 {len(docs)} 处定义，这条腿判不出唯一真源"
    return docs[0]


def test_every_watchdog_claim_says_the_self_heal_is_not_wired():
    """点到 `tick_watchdog_pass` 的声明，连同它自己的定义，必须同时说清"没人调它"。

    修前形状是 `af_tick_supervisor.health_state` 的 docstring 写着"主线程 watchdog 仅在意外终止时
    重启本线程"——读者据此以为生产链路会自愈，而 `src/` 里一个调用点都没有。这类声明不会随代码
    自己更新，属于"写在源码里的第二份真值"（与 B.7 那格同族，DCD 已追认改声明这一半）。

    定义侧另量一次（`_definition_docstring`）：只按"文里点到这个名字"筛，会把"定义处把这句声明
    删掉、且不再自报名字"那种形状漏成假绿——那正是要拦的改法。
    """
    docs = _docstrings_naming("tick_watchdog_pass")
    assert docs, "没有任何 docstring 再点到 tick_watchdog_pass —— 这条腿会空转成假绿"
    for fname, doc in docs:
        assert any(k in doc for k in _NOT_WIRED), (fname, doc[:200])
    own = _definition_docstring("tick_watchdog_pass")
    assert any(k in own for k in _NOT_WIRED), own[:200]


def test_tick_watchdog_still_has_no_production_caller():
    """接线那天这条会红——那是故意的：红的时候必须**同时**改声明与这条腿，不许只改一边。

    为什么不顺手把 watchdog 接上：`_tick_exit_reason` 是模块级全局，ticker 线程写、主线程读。
    裁定 `20261011-AF第六轮与第二期审计攒批十三问` §3 Q7.3 已把这类跨线程读写钉进
    `af_live._live_state_lock`（RLock），**锁有了，但"接不接线"仍是另一件事**：把 watchdog 接进主循环
    就恰好造出第六轮 BUG-11 描述的那个竞态形状（读线程与读原因之间不再有交错），而"意外死亡自动重启
    vs 停机等人"是运行期语义决策。按裁定 `20261010-AF与DB与DPP十件` §六 Q3，这类"该不该有消费者"归 DCD 结案。
    """
    calls = [
        (py.name, node.lineno)
        for py in sorted(_src_dir().glob("*.py"))
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", getattr(node.func, "attr", "")) == "tick_watchdog_pass"
    ]
    assert calls == [], f"tick 自愈被接上了 {calls}：请连同 docstring 与本腿一起改，并引裁定号"

