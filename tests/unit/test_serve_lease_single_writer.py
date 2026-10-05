"""裁定 20261004 §一 1 A：单写者租约从"只装 HTTP 面"扩到**进程外**的真机写入口。

起因：`forge serve` 启动时在 `{store_root}/.serve.lock` 上 `try_acquire()`，拿不到就把整个 HTTP
面降级只读（`af_api._readonly_guard` 对所有写/live 端点回 503）。但 **MCP 面从来不碰这把锁**：
`forge mcp` 与生产 serve 指向同一个 store 根目录时，人点 WebUI 被 503 拒、Agent 走 `af_live_run`
照样把真机写了。同一个 store、两套写权限，正是本仓反复判红的"闸门只装一面"族
（`test_af_live_entry_seams.py` 钉的 Tier-0 设备保护是同一形状的另一个洞）。

裁定的三条口径各自要有判据：
- **只把真机写纳入**：`live_run` 受约，`af_store`/`af_persist` 的既有锁不顺带动；
- **只 check 不 acquire**：`held_by_other()` 探测不抢锁、不改持有者诊断（盖 sidecar 就把 serve
  的持有者信息覆盖成了自己，事后无法归因）；
- **MCP 文本以固定前缀 `READONLY_DEGRADED:` 开头**：下游（DB）靠这个前缀判别降级态，
  前缀必须在文本开头，套上"工具执行出错："那层壳就等于没有前缀。

持锁方必须是**真子进程**：flock/`msvcrt.locking` 挂在"打开文件描述"上，同进程第二条句柄会被
自己挡住——所以 `af_flock._LOCAL_HELD` 认出"是本进程"这一条也得钉住，否则 serve 自己的每次
真机下发都会被自己的租约 503（那是把闸门装反，不是补缺口）。
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from autoforge.af_auth import SCOPES as _ALL_SCOPES

# 裁定 20261004 §一 Q2=B：MCP 面「无身份」改成默认拒绝，本文件的调用点因此逐条显式给身份；
# 「不给身份」那一档只由 test_dcd_20261004_mcp_default_deny.py 钉成"拒绝"。
_ALL = {"subject": "test-all", "scopes": sorted(_ALL_SCOPES)}
from fastapi.testclient import TestClient

from autoforge import af_service as svc
from autoforge.af_adapters import CallResult
from autoforge.af_api import build_app
from autoforge.af_flock import SERVE_LOCK_NAME, FileLock, serve_lock_path
from autoforge.af_mcp import dispatch
from autoforge.af_store import GraphStore

_SRC = str(Path(svc.__file__).resolve().parent.parent)

LIVE_IR: dict = {
    "automations": [
        {
            "ir_version": "0.2.1",
            "id": "a_lease",
            "name": "a_lease",
            "version": 1,
            "mode": "restart",
            "nodes": [
                {
                    "id": "t1",
                    "kind": "on",
                    "trigger": {"type": "state", "entity_id": "binary_sensor.hall", "to": "on"},
                },
                {
                    "id": "d1",
                    "kind": "do",
                    "adapter": "ha",
                    "action": "light.turn_off",
                    "params": {"entity_id": "light.study"},
                    "on_error": {"default": "pass"},
                    "expect": {"entity_id": "light.study", "state": "off"},
                },
            ],
            "edges": [{"from": "t1", "to": "d1", "kind": "then"}],
        }
    ]
}

ARGS: dict[str, Any] = {
    "ir": LIVE_IR,
    "live_allow": ["light.study"],
    "events": [{"entity_id": "binary_sensor.hall", "state": "on"}],
    "confirm": True,
}

# 前缀是裁定登记的对外口径，不是内部常量名：测试里写死字面量，改常量名没同步这条文案就该判红。
PREFIX = "READONLY_DEGRADED:"

_HOLDER = """
import sys
from autoforge.af_flock import FileLock

lock = FileLock(sys.argv[1], timeout=20.0)
lock.acquire()
sys.stdout.write("HELD\\n")
sys.stdout.flush()
sys.stdin.read()
"""


class FakeTransport:
    def __init__(self):
        self.states = {"light.study": ("on", {}), "binary_sensor.hall": ("off", {})}
        self.dispatched: list[tuple[str, dict]] = []

    def __call__(self, action, params):
        return self.call(action, params)

    def call(self, action, params):
        self.dispatched.append((action, dict(params)))
        return CallResult.ok({"action": action, "result": []})

    def all_states(self):
        return dict(self.states)

    def get_state(self, entity_id):
        found = self.states.get(entity_id)
        return found[0] if found else None


@pytest.fixture()
def gate(monkeypatch):
    """开真机闸门 + 记录每一次实际构造的传输层（= 真的碰过设备）。"""
    monkeypatch.setenv("AF_ALLOW_NOAUTH", "1")
    monkeypatch.setenv("AUTOFORGE_LIVE_ENABLED", "1")
    monkeypatch.setenv("AUTOFORGE_HA_TOKEN", "srv-side-token")
    monkeypatch.delenv("AUTOFORGE_UNDO", raising=False)
    transports: list[FakeTransport] = []

    def factory(ha_url, token):
        transports.append(FakeTransport())
        return transports[-1]

    monkeypatch.setattr(svc, "LIVE_TRANSPORT_FACTORY", factory)
    return transports


@contextmanager
def _other_process_holding(store_root: Path):
    """让一个真子进程持住 `store_root` 的租约，退出时关掉它的 stdin 让它放锁。

    就绪判据直接读锁本身（`held_by_other()`）而不是握手输出：握手只证"子进程活着"，
    读锁证的才是"另一个进程的持锁对本进程可见"——那正是本批要装的机制。
    """
    lock_path = serve_lock_path(store_root)
    env = dict(os.environ)
    env["PYTHONPATH"] = _SRC + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(_HOLDER), str(lock_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        deadline = time.monotonic() + 25.0
        while not FileLock(lock_path).held_by_other():
            rc = proc.poll()
            if rc is not None:
                err = (proc.stderr.read() if proc.stderr else "")[:800]
                raise AssertionError(f"持锁子进程提前退出（rc={rc}）：{err}")
            if time.monotonic() > deadline:
                raise AssertionError(f"等不到跨进程持锁：{lock_path}")
            time.sleep(0.1)
        yield proc
    finally:
        if proc.stdin is not None:
            proc.stdin.close()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


# ── `held_by_other`：只 check 不 acquire 的四态 ──────────────────────────


def test_free_lease_is_not_reported_as_held_and_writes_no_sidecar(tmp_path):
    """锁空闲 ⇒ 判"没人持"；探测只读不写：sidecar 一旦被盖，serve 的持有者诊断就被覆盖成探测方。"""
    lock_path = serve_lock_path(tmp_path)

    assert FileLock(lock_path).held_by_other() is False
    assert FileLock(lock_path).holder() == {}
    assert list(tmp_path.glob(SERVE_LOCK_NAME + "*.info")) == []


def test_this_processes_own_lease_is_not_reported_as_another_writer(tmp_path):
    """`_LOCAL_HELD` 那条：本进程持锁时探测必须返回 False，否则 serve 每次真机下发都被自己 503。"""
    lock_path = serve_lock_path(tmp_path)
    held = FileLock(lock_path)
    held.acquire()
    try:
        assert FileLock(lock_path).held_by_other() is False
        # 同一把锁对象的重复探测也判 False（重入分支不许把"我持着"读成"被抢了"）
        assert held.held_by_other() is False
    finally:
        held.release()


def test_another_process_holding_the_lease_is_detected(tmp_path):
    """跨进程可见性——整套机制的地基：别的进程持着，本进程探测就得判"被持有"。"""
    with _other_process_holding(tmp_path):
        assert FileLock(serve_lock_path(tmp_path)).held_by_other() is True


# ── 裁定口径 ①②③：HTTP 503 / MCP 拒收且不构造传输层 / 锁空闲照常下发 ──────


def test_live_run_refused_when_another_process_holds_the_lease(tmp_path, gate):
    """服务层：租约被他进程持有时抛 `ServiceError(status=503)`，且**根本不碰设备**。"""
    with _other_process_holding(tmp_path):
        with pytest.raises(svc.ServiceError) as ei:
            svc.live_run(
                LIVE_IR, ["light.study"], ARGS["events"], confirm=True,
                store=GraphStore(tmp_path),
            )

    assert ei.value.status == 503
    assert str(ei.value).startswith(PREFIX)
    assert gate == [], "拒收的那条路径必须不构造传输层：构造了就是已经连上 HA 才说'不让写'"


def test_http_face_returns_503(tmp_path, gate):
    """HTTP 面：`/api/live/run` 拿到 503（与 `_readonly_guard` 同码），文案带前缀供判别。"""
    with _other_process_holding(tmp_path):
        client = TestClient(build_app(store_root=str(tmp_path)))
        r = client.post("/api/live/run", json=ARGS)

    assert r.status_code == 503, r.text
    assert r.json()["detail"].startswith(PREFIX)
    assert gate == []


def test_mcp_face_text_starts_with_the_prefix(tmp_path, gate):
    """MCP 面：前缀必须在文本**开头**——DB 侧按 `startswith` 判别，中间出现等于判别不上。"""
    store = GraphStore(tmp_path)
    with _other_process_holding(tmp_path):
        content, is_error = dispatch("af_live_run", ARGS, store, _ALL)

    assert is_error is True
    assert content[0]["text"].startswith(PREFIX), content[0]["text"]
    assert gate == []


def test_live_run_dispatches_when_the_lease_is_free(tmp_path, gate):
    """锁空闲（开发机 / 独立 MCP 进程）照常下发：只 check 不 acquire 的另一半是"别把自己人拦光"。"""
    out = svc.live_run(
        LIVE_IR, ["light.study"], ARGS["events"], confirm=True, store=GraphStore(tmp_path)
    )

    assert out["ok"] is True
    assert [a for t in gate for a, _ in t.dispatched] == ["light.turn_off"]


def test_serve_holding_its_own_lease_still_dispatches(tmp_path, gate):
    """生产 serve 自己（持锁方）走 `live_run` 不许被自己的租约拒掉。"""
    lease = FileLock(serve_lock_path(tmp_path))
    lease.acquire()
    try:
        out = svc.live_run(
            LIVE_IR, ["light.study"], ARGS["events"], confirm=True, store=GraphStore(tmp_path)
        )
    finally:
        lease.release()

    assert out["ok"] is True
    assert [a for t in gate for a, _ in t.dispatched] == ["light.turn_off"]


# ── 锁文件名唯一真源 ────────────────────────────────────────────────────


def test_serve_lock_file_name_has_a_single_source():
    """`.serve.lock` 这个字面量只许活在 `af_flock.py` 里。

    租约判据读的路径和 serve 抢的路径必须出自同一个常量：serve 那侧手抄字面量、check 这侧
    改了文件名，两边就各自锁各自的目录，`held_by_other()` 永远判空闲（假绿且无声）。
    """
    src = Path(svc.__file__).resolve().parent
    files = sorted(
        p.name for p in src.glob("*.py") if SERVE_LOCK_NAME in p.read_text(encoding="utf-8")
    )
    assert files == ["af_flock.py"], f"锁文件名出现了第二个抄本：{files}"
