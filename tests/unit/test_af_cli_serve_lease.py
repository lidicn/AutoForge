"""第 5 步 ①（裁定 A）的**接缝**判据：serve 抢不到单写者锁 ⇒ `build_app(..., readonly=True)`。

两端各自早有测试（`FileLock.try_acquire` 的跨进程互斥、API 层 `_readonly_guard` 的 503），
中间那根线——serve 把 `not try_acquire()` 传进 `build_app`——一条判据都没有：
把 `readonly=readonly` 改成 `readonly=False`，全量 pytest 照样绿。
"""

import sys

import pytest
from typer.testing import CliRunner

from autoforge import af_api, af_cli, af_flock

runner = CliRunner()


def _readonly_of(args, kwargs):
    if "readonly" in kwargs:
        return kwargs["readonly"]
    return args[3] if len(args) > 3 else False


class _FakeUvicorn:
    def __init__(self):
        self.calls = []

    def run(self, app_, **kwargs):
        self.calls.append((app_, kwargs))


@pytest.fixture
def wired(tmp_path, monkeypatch):
    fake_uvicorn = _FakeUvicorn()
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    captured: dict = {}

    def fake_build_app(*args, **kwargs):
        captured["readonly"] = _readonly_of(args, kwargs)
        return "APP"

    monkeypatch.setattr(af_api, "build_app", fake_build_app)
    monkeypatch.setattr(af_cli, "_start_linkage_bridge", lambda **kwargs: None)
    return captured, fake_uvicorn


class _StubLock:
    def __init__(self, path, timeout=10.0, poll_s=0.05, info=None):
        self.path = path

    def holder(self):
        return {"owner": "other-pid"}


def _lock_that_returns(value):
    class _Lock(_StubLock):
        def try_acquire(self):
            return value

    return _Lock


def test_serve_falls_back_to_readonly_when_lease_is_taken(tmp_path, monkeypatch, wired):
    captured, fake_uvicorn = wired
    monkeypatch.setattr(af_flock, "FileLock", _lock_that_returns(False))

    result = runner.invoke(af_cli.app, ["serve", "--store-root", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert captured["readonly"] is True
    assert fake_uvicorn.calls and fake_uvicorn.calls[0][0] == "APP"


def test_serve_keeps_write_mode_when_it_holds_the_lease(tmp_path, monkeypatch, wired):
    captured, fake_uvicorn = wired
    monkeypatch.setattr(af_flock, "FileLock", _lock_that_returns(True))

    result = runner.invoke(af_cli.app, ["serve", "--store-root", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert captured["readonly"] is False
