"""`GET /api/mcp/pair-request`（配对 SSE）的鉴权、帧形与静态守卫判据。

这批端点此前**一条测试都没有**（`grep -rn "mcp/pair-request" tests/` 零命中），所以一个每次都
500 的缺陷在生产码里活了很久：处理器是 `async def`，里面却写 `creds = _bearer(request)` ——
`HTTPBearer.__call__` 是协程，直调返回 coroutine 对象且永远为真值，于是 `creds.credentials`
抛 `AttributeError`。真实后果不是"少一个装饰器"的风格问题，而是 **ForgeSight 的配对码弹窗对着
真后端从来没有工作过**（修前连"没令牌"的本地开发档也是 500）。码长现为 8 位数字（安全审计
N-P0-sec：6 位被证实 0.5s 可穷举），多份旧设计文档里"6 位码"那句是过期措辞。

为什么这些判据跑在**真 uvicorn** 上而不是 `TestClient`：`TestClient` 的 `receive()` 在请求体读
完后会先 `await response_complete.wait()` 才返回 `http.disconnect`，而 SSE 生成器要收到断开才
结束 ⇒ 双方互等，任何"读到第一帧"的写法必死锁（本批实测 `timeout 180` ⇒ `RC=124`，两条 403
判据先绿、第三条挂住）。真服务器上 `resp.close()` 会让下一轮 `is_disconnected()` 返回真，
生成器自然收口。
"""

from __future__ import annotations

import ast
import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
uvicorn = pytest.importorskip("uvicorn")

from autoforge.af_api import build_app  # noqa: E402
from autoforge.af_auth import PairCodeStore  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
STREAM_PATH = "/api/mcp/pair-request"
TOKENS = {
    "tok-reporter": {"subject": "reporter", "scopes": ["read"]},
    "tok-bot": {"subject": "bot", "scopes": ["write"]},
}
ENV_KEYS = ("AUTOFORGE_TOKENS", "AF_ALLOW_NOAUTH", "AF_REQUIRE_AUTH")


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class _Live:
    def __init__(self, url: str, store_root: Path) -> None:
        self.url = url
        self.store_root = store_root

    def open_stream(self, token: str | None = None, header: bool = False):
        """返回 (response, content_type) —— 调用方负责 `close()`，生成器随之收口。"""
        if header and token:
            req = urllib.request.Request(self.url + STREAM_PATH,
                                         headers={"Authorization": f"Bearer {token}"})
        else:
            q = f"?token={token}" if token else ""
            req = urllib.request.Request(self.url + STREAM_PATH + q)
        response = urllib.request.urlopen(req, timeout=20)
        return response, response.headers.get("content-type", "")

    def status(self, token: str | None = None) -> int:
        req = urllib.request.Request(self.url + STREAM_PATH)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                return response.status
        except urllib.error.HTTPError as exc:
            return exc.code

    def seed_pair_code(self, hint: str = "ForgeSight-test-agent") -> str:
        """用**另一个** store 实例造码：SSE 侧每轮 `_load()` 重读磁盘，跨实例可见。"""
        path = self.store_root / ".auth" / "pair_codes.json"
        return PairCodeStore(path).create(hint).code


def _serve(tmp_path, env: dict[str, str]):
    saved = {key: os.environ.get(key) for key in ENV_KEYS}
    for key in ENV_KEYS:
        os.environ.pop(key, None)
    for key, value in env.items():
        os.environ[key] = value
    store_root = Path(tmp_path) / "store"
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(build_app(str(store_root)),
                                           host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 20.0
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started, "起桥失败：SSE 判据跑在假服务器上等于没测"
    try:
        yield _Live(f"http://127.0.0.1:{port}", store_root)
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture()
def live(tmp_path):
    yield from _serve(tmp_path, {"AUTOFORGE_TOKENS": json.dumps(TOKENS)})


@pytest.fixture()
def live_noauth(tmp_path):
    """`AF_ALLOW_NOAUTH=1` 的本地逃生舱档：修前这里同样是 500。"""
    yield from _serve(tmp_path, {"AF_ALLOW_NOAUTH": "1"})


def _first_frame(response) -> list[str]:
    lines: list[str] = []
    for raw in response:
        lines.append(raw.decode("utf-8", "replace").rstrip("\r\n"))
        if lines[-1] == "":
            break
    return lines


# ── 1. fail-closed：未授权者建不起长连接 ─────────────────────────────


def test_anonymous_stream_is_refused(live):
    assert live.status() == 403


def test_bad_token_stream_is_refused(live):
    assert live.status(token="tok-nope") == 403


# ── 2/3. 两条合法通路都必须建立起来（修前一律 500）────────────────────


@pytest.mark.parametrize("tok", ["tok-reporter", "tok-bot"])
def test_bearer_header_establishes_the_stream(live, tok):
    """修前读数：`AttributeError: 'coroutine' object has no attribute 'credentials'` ⇒ HTTP 500。"""
    live.seed_pair_code()
    response, content_type = live.open_stream(token=tok, header=True)
    try:
        assert content_type.startswith("text/event-stream")
        assert _first_frame(response)[0] == "event: pair-request"
    finally:
        response.close()


def test_query_token_fallback_establishes_the_stream(live):
    """EventSource 发不了 Authorization 头，`?token=` 是前端唯一可用的通路。"""
    live.seed_pair_code()
    response, content_type = live.open_stream(token="tok-reporter")
    try:
        assert content_type.startswith("text/event-stream")
        assert _first_frame(response)[0] == "event: pair-request"
    finally:
        response.close()


def test_noauth_local_escape_hatch_still_streams(live_noauth):
    live_noauth.seed_pair_code()
    response, content_type = live_noauth.open_stream()
    try:
        assert content_type.startswith("text/event-stream")
        assert _first_frame(response)[0] == "event: pair-request"
    finally:
        response.close()


# ── 4. 帧形对前端解析器负责 ──────────────────────────────────────────


def test_pair_frame_matches_the_frontend_parser(live):
    code = live.seed_pair_code(hint="NAS-SP")
    response, _ = live.open_stream(token="tok-reporter", header=True)
    try:
        lines = _first_frame(response)
    finally:
        response.close()
    assert lines[0] == "event: pair-request"
    assert lines[1].startswith("data: ")
    payload = json.loads(lines[1][len("data: "):])
    assert set(payload) == {"code", "agent_name_hint", "expires_at"}
    assert payload["code"] == code and re.fullmatch(r"\d{8}", payload["code"])
    assert payload["agent_name_hint"] == "NAS-SP"
    # `ui-user-mimo/src/api/http.ts` 的 `toIso(raw.expires_at)`：这里必须是 epoch 秒数字
    assert isinstance(payload["expires_at"], (int, float))
    assert payload["expires_at"] > time.time()


def test_consumed_code_never_reaches_the_stream_again(live):
    """单次性由 `consume` 保证：码一旦被 `af_pair` 兑换掉，SSE 的取源就不再返回它。

    推送标记那一头是 **at-least-once**（实测）：`mark_pushed` 排在 `yield` 之后，客户端读完帧
    就断线时它不落盘，重连会把同一枚码再推一次。这不影响单次性，所以本条判 `consume` 而不判
    `pushed`——后者是个 race-dependent 断言，钉上去只会变成随机红。
    """
    code = live.seed_pair_code()
    path = live.store_root / ".auth" / "pair_codes.json"
    assert PairCodeStore(path).consume(code) is not None
    assert [pc.code for pc in PairCodeStore(path).pending_events()] == []


# ── 5. 静态守卫：HTTPBearer 实例只许待在 Depends(...) 的入参里 ────────


def test_no_bearer_instance_is_ever_called_directly():
    """`_bearer(request)` 这类写法在 async 端点里返回协程 ⇒ 每次请求 500，静态上却很难看见。

    判据不认注释也不认"这里只有一个文件"：AST 扫全 src，凡是绑定过 `HTTPBearer(...)` 的名字，
    出现在任何 `Call.func` 位置即判红。`Depends(_bearer)` 里它是**参数**不是被调用的 func ⇒
    合法形状不会被误伤。
    """
    offenders: list[str] = []
    for py in sorted((ROOT / "src" / "autoforge").rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        bearer_names: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            if getattr(node.value.func, "id", "") != "HTTPBearer":
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bearer_names.add(target.id)
        if not bearer_names:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in bearer_names:
                    rel = py.relative_to(ROOT).as_posix()
                    offenders.append(f"{rel}:{node.lineno} 直接调用 {node.func.id}()")
    assert offenders == [], f"HTTPBearer 实例被当同步函数直调：{offenders}"
