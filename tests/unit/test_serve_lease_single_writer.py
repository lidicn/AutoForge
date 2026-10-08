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
- **MCP 文本以固定前缀 `READONLY_DEGRADED:` 开头**：下游（DB）靠这个前缀判别降级态。
  裁定 20261007 §二 戊A 把 MCP 的异常路径从散文改成 JSON 信封 `{ok, code, message}` 之后，
  前缀的位置从"整段文本开头"挪到"`message` 开头"——套上信封壳等于没有前缀这一条**不变**，
  变的只是判别点；挪动本身已写成给 DB 的读数变化说明（docs/handoff/）。

持锁方必须是**真子进程**：flock/`msvcrt.locking` 挂在"打开文件描述"上，同进程第二条句柄会被
自己挡住——所以 `af_flock._LOCAL_HELD` 认出"是本进程"这一条也得钉住，否则 serve 自己的每次
真机下发都会被自己的租约 503（那是把闸门装反，不是补缺口）。
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import textwrap
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
# 码不手抄：契约 §7.2 那六枚的唯一真源是 homesdk.adm.errors。
from homesdk.adm.errors import ADM_ERR_INTERNAL

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
    """MCP 面：前缀必须在 `message` 字段的**开头**（裁定 20261007 §二 戊A 改了信封形状）。

    戊A 之前 MCP 的异常路径是散文，DB 按整段文本 `startswith` 判别；现在异常路径统一成
    JSON `{ok:false, code, message}`，判别点随之挪进 `message`。**这条挪动本身要交给 DB
    一份读数变化说明**（见 docs/handoff/交接卡_MCP异常路径JSON信封_读数变化_20261008.md）：
    对端若还按老口径判整段文本，`{"ok": false…` 永远不匹配前缀 ⇒ 降级态静默读成"没降级"。
    """
    store = GraphStore(tmp_path)
    with _other_process_holding(tmp_path):
        content, is_error = dispatch("af_live_run", ARGS, store, _ALL)

    assert is_error is True
    text = content[0]["text"]
    payload = json.loads(text)  # 整段仍是 JSON：老口径那条断言在这里就该红
    assert payload["ok"] is False
    assert payload["code"] == ADM_ERR_INTERNAL, payload
    assert payload["message"].startswith(PREFIX), text
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


# ── 裁定 20261008 §一 B：`/api/health` 的 `write_gate` 必须跟着这道闸的真值 ──────────
#
# 旧键 `readonly` 是硬编码 `True`（v1.x 身份声明），语义不动；新键承载运行期档位。
# 判据口径：**读 `/api/health` 的人得到的档位，必须和真打写端点得到的结果一致**——
# 分叉的形状正是"health 报一个值、写面做另一件事"。

def _health_write_gate(client: TestClient) -> str:
    body = client.get("/api/health").json()
    assert body["readonly"] is True, "旧键是身份声明，不许被运行期档位改写（裁定 §一 驳回 A）"
    return body["write_gate"]


def test_control_writable_serve_reads_open_while_readonly_stays_true(tmp_path):
    """CONTROL：锁空闲 + 装配期没降级 ⇒ `open`。现场那条"health true + 写面 200"的矛盾读数
    在这里被拆成两格各说各话：身份声明仍是 true，运行期档位如实 open。"""
    client = TestClient(build_app(store_root=str(tmp_path), readonly=False))
    assert _health_write_gate(client) == svc.WRITE_GATE_OPEN
    assert svc.write_gate(GraphStore(tmp_path)) == svc.WRITE_GATE_OPEN


def test_boot_degraded_serve_reads_blocked_and_the_write_face_refuses(tmp_path):
    """裁定 §一 的主判据：health 报 blocked，且写端点确实 503 —— 两边同读一道闸。"""
    client = TestClient(build_app(store_root=str(tmp_path), readonly=True))
    assert _health_write_gate(client) == svc.WRITE_GATE_BLOCKED
    assert client.post("/api/live/run", json=ARGS).status_code == 503


def test_lease_taken_after_boot_still_reads_blocked(tmp_path, gate):
    """启动后才被别人抢走租约：装配期标志说 open，探测说 blocked ⇒ 必须读 blocked，
    因为这一格的定义是"现在打写端点会怎样"（`live_run` 里 `_single_writer_check` 真会拒）。"""
    store = GraphStore(tmp_path)
    with _other_process_holding(tmp_path):
        client = TestClient(build_app(store_root=str(tmp_path), readonly=False))
        assert _health_write_gate(client) == svc.WRITE_GATE_BLOCKED
        assert client.post("/api/live/run", json=ARGS).status_code == 503
    assert gate == []


def test_nothing_to_probe_reads_no_lease_not_open(tmp_path):
    """`no_lease` 是"没看"，不是"能写"：没有 store 根可探时报第三档，退化成 open 就是本裁定要禁的假读数。"""
    assert svc.health()["write_gate"] == svc.WRITE_GATE_NO_LEASE
    # 但装配期已经知道自己降级 ⇒ 没得探也必须报 blocked（降级不因为探不到就消失）
    assert svc.write_gate(None, readonly=True) == svc.WRITE_GATE_BLOCKED
    assert svc.health(None, readonly=True)["write_gate"] == svc.WRITE_GATE_BLOCKED


def test_this_processes_own_lease_reads_open(tmp_path):
    """本进程持锁不能读成 blocked，否则 serve 每次真机下发都被自己的租约拦（闸门装反）。"""
    lease = FileLock(serve_lock_path(tmp_path))
    lease.acquire()
    try:
        assert svc.write_gate(GraphStore(tmp_path)) == svc.WRITE_GATE_OPEN
    finally:
        lease.release()


def test_write_gate_is_driven_by_the_flag_not_a_constant(tmp_path):
    """反空洞：同一函数、同一 store，两档必须给出两个不同读数。"""
    store = GraphStore(tmp_path)
    hi = svc.health(store, readonly=True)["write_gate"]
    lo = svc.health(store, readonly=False)["write_gate"]
    assert {hi, lo} == {svc.WRITE_GATE_BLOCKED, svc.WRITE_GATE_OPEN}


def test_health_route_hands_the_gate_flag_to_the_readout():
    """结构腿：`/api/health` 必须把 `readonly` 递给 `svc.health`。

    这一格是分叉的根：过去 route 只递 store/presence，`write_gate` 就只能靠探测，
    "启动降级、锁后来空了"的那个实例会一边 503 一边报 open。静态钉住"接线"这件事。
    """
    src = Path(svc.__file__).resolve().parent / "af_api.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and ast.unparse(node.func) == "svc.health"
        and any(k.arg == "readonly" for k in node.keywords)
    ]
    assert calls, "af_api 的 health 调用没把 readonly 递进去（读数与闸会分叉）"
