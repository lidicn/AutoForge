"""`?token=` 不落进 uvicorn 访问日志：掩码 filter 的真服务器判据 + 反空洞对照。

背景（执行记录 §二之四十二 第六节）：配对 SSE 建流只能经 `?token=<码>` 传令牌
（EventSource 发不了自定义头），而 uvicorn 的 access log 原样记整条 URL ⇒ **凭据进磁盘**。
本批落的是登记给 AF 的那一半：`af_cli serve` 挂一条 logging filter，把 `token=` 的值替成
`***`。反代 / nginx 那一半不在 AF 射程（NAS 部署侧），所以这里只判"本进程不泄"。

为什么跑真 uvicorn 而不是只喂 `LogRecord`：只喂自造记录，判据可以在"filter 从没被挂上"的
情况下照样绿（logger 名、args 展开、格式化路径全是猜的）。故第一条腿是真服务器真请求，并带
**对照腿**——同一套捕获路径在不挂 filter 时必须**看得见**明文，否则"看不见明文"那条断言是空的。
"""

from __future__ import annotations

import ast
import contextlib
import logging
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
uvicorn = pytest.importorskip("uvicorn")

from autoforge import af_cli  # noqa: E402
from autoforge.af_api import build_app  # noqa: E402

ACCESS_LOGGER = "uvicorn.access"
SECRET = "SEKRIT-9f2c41be"
CLI_PATH = Path(__file__).resolve().parents[2] / "src/autoforge/af_cli.py"


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class _Collector(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


@contextlib.contextmanager
def _running(port: int, tmp_path: Path, collector: _Collector):
    """起真服务器；collector 只能在 `started` **之后**挂——`uvicorn.Config` 会 dictConfig
    把该 logger 的 handlers 清空，先挂等于没挂。filters 不在它清的范围里，故掩码仍生效。"""
    server = uvicorn.Server(
        uvicorn.Config(build_app(str(tmp_path / "store")), host="127.0.0.1", port=port,
                       log_level="info", access_log=True)
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 20.0
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started, "起桥失败：access log 判据跑在没起来的服务器上等于没测"
    logger = logging.getLogger(ACCESS_LOGGER)
    logger.addHandler(collector)
    try:
        yield
    finally:
        logger.removeHandler(collector)
        server.should_exit = True
        thread.join(timeout=15)


def _get(path: str, port: int) -> int:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=20) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


@pytest.fixture()
def access_log():
    """给出例还原 filter 的收集器（handler 由 `_running` 在服务起来后挂）。"""
    logger = logging.getLogger(ACCESS_LOGGER)
    collector = _Collector()
    saved = list(logger.filters)
    logger.filters.clear()
    try:
        yield collector
    finally:
        logger.filters[:] = saved


def test_access_log_leaks_the_token_without_the_filter(access_log, tmp_path):
    """对照腿（反空洞自证）：不挂 filter 时明文**必须**进日志，否则下面的绿灯不可信。"""
    port = _free_port()
    with _running(port, tmp_path, access_log):
        assert _get(f"/api/health?token={SECRET}", port) == 200
    assert any(SECRET in line for line in access_log.lines), (
        f"对照失败：access log 根本没记下这条 URL ⇒ '已掩码'那条判据是空的。实收：{access_log.lines!r}"
    )


def test_access_log_masks_the_query_token(access_log, tmp_path):
    """挂上 filter 后：同一台服务器、同一个 URL ⇒ 明文一个字节不留，且留下 `token=***`。"""
    assert af_cli.install_access_log_token_mask() is True
    port = _free_port()
    with _running(port, tmp_path, access_log):
        assert _get(f"/api/health?token={SECRET}", port) == 200
        assert _get(f"/api/health?a=1&token={SECRET}&b=2", port) == 200
    assert access_log.lines, "一条访问日志都没收到：这条判据没有射程"
    assert not any(SECRET in line for line in access_log.lines), access_log.lines
    assert sum("token=***" in line for line in access_log.lines) == 2, access_log.lines


def test_install_is_idempotent_and_keeps_one_filter(access_log):
    assert af_cli.install_access_log_token_mask() is True
    assert af_cli.install_access_log_token_mask() is False
    masks = [f for f in logging.getLogger(ACCESS_LOGGER).filters
             if isinstance(f, af_cli.AccessLogTokenMask)]
    assert len(masks) == 1


@pytest.mark.parametrize(
    "raw,expected",
    [
        # uvicorn 的真实调用形：'%s - "%s %s HTTP/%s" %d'，URL 在引号内、后面还跟着状态码
        ('127.0.0.1:6000 - "GET /api/health?token=abc123 HTTP/1.1" 200',
         '127.0.0.1:6000 - "GET /api/health?token=*** HTTP/1.1" 200'),
        ('127.0.0.1:6000 - "GET /api/x?b=2&token=abc123&a=1 HTTP/1.1" 200',
         '127.0.0.1:6000 - "GET /api/x?b=2&token=***&a=1 HTTP/1.1" 200'),
        # 不含 token= 的行一字不动（percent-encoding 里的 % 不能被二次 % 格式化）
        ('127.0.0.1:6000 - "GET /api/search?q=%E4%B8%AD%25d HTTP/1.1" 200',
         '127.0.0.1:6000 - "GET /api/search?q=%E4%B8%AD%25d HTTP/1.1" 200'),
        # 路径里出现 token 但不是查询键 ⇒ 不动，免得把正常 URL 改成读不懂
        ('127.0.0.1:6000 - "GET /api/token=literal HTTP/1.1" 404',
         '127.0.0.1:6000 - "GET /api/token=literal HTTP/1.1" 404'),
    ],
)
def test_filter_rewrites_only_the_token_value(raw, expected):
    record = logging.LogRecord(ACCESS_LOGGER, logging.INFO, __file__, 1, raw, None, None)
    assert af_cli.AccessLogTokenMask().filter(record) is True
    assert record.getMessage() == expected


def test_serve_installs_the_mask_before_uvicorn_run():
    """AST 守卫：`serve` 必须在 `uvicorn.run` 之前调用安装函数（漏了就静默回到明文）。"""
    tree = ast.parse(CLI_PATH.read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "serve")
    installs, runs = [], []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name == "install_access_log_token_mask":
                installs.append(node.lineno)
            elif name == "run":
                runs.append(node.lineno)
    assert installs, "serve 里再没有 install_access_log_token_mask()：日志掩码已失守"
    assert runs and min(installs) < min(runs)
