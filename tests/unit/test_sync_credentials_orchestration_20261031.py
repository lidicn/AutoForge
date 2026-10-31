"""AF-714（编排面凭据注入 + 崩循环检出）回归钉，2026-10-31。

出处：NAS 运维转来的 autoforge-sync 崩溃循环——`restart_count=329`/`exit=2`/
日志尾「[监听启动被拒] 真机常驻监听（forge watch） 需要授权码」。根因是编排面
没有凭据注入口：`forge watch` 只认 `--auth-code` 命令行参数，容器编排给不了
（明文写命令行会被 `docker inspect` 一览无余，G24 同族）。⇒ 三件套：

① `af_cli` 授权码取值补 env 口子（`AUTOFORGE_AUTH_CODE`，`_auth_code_of`）：
   run --live / watch / undo 三处入口统一，`--auth-code` 保留人工面回退；
② `docker/docker-compose.sync.yml`：sync 面正式编排定义（env 只写键名，
   ⛔ 真值永不进仓），码账本与 api 服务挂同一 store 卷；
③ `scripts/preflight.sh`：崩循环检出（restarts>5 且 exit≠0 ⇒ FAIL，不能只看
   容器在不在）。

⛔ 不动「开机自起要不要码」的判定（DCD 在裁）；env 只是注入通道，闸仍在。

变异自证（摘掉 env 回退 ⇒ 判据①红）由实现者手工执行并附命令输出。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoforge import af_cli
from autoforge.af_cli import _auth_code_of

REPO = Path(__file__).resolve().parents[2]

#: 与 resync.py / compose 文件同源的键名（键名可进仓，⛔ 真值不进）。
ENV_KEY = "AUTOFORGE_AUTH_CODE"


def test_cli_beats_env():
    """--auth-code 显式给则用之（人工启动面回退不丢）。"""
    assert _auth_code_of("123456") == "123456"


def test_env_fills_when_cli_empty(monkeypatch):
    """CLI 未给、env 有值 ⇒ 取 env（编排面正路）。"""
    monkeypatch.setenv(ENV_KEY, "654321")
    assert _auth_code_of("") == "654321"


def test_env_empty_and_cli_empty_is_empty(monkeypatch):
    """两边都空 ⇒ 空串，由 _auth_code_check 的缺码闸去拒（不是这里静默造值）。"""
    monkeypatch.delenv(ENV_KEY, raising=False)
    assert _auth_code_of("") == ""


@pytest.mark.parametrize("caller", ["watch", "run", "undo"])
def test_three_callers_read_env_code(monkeypatch, caller):
    """三个入口（watch / run --live / undo）都经 `_auth_code_of` 取码。

    用 AST 钉调用形状：直接钉 `_auth_code_check(...)` 的实参是
    `_auth_code_of(auth_code)`（而不是裸 `auth_code`）——裸取就是 AF-714 的
    崩溃循环回归形状（编排面 env 注入了但代码不读）。
    """
    import ast
    import inspect

    fn = {"watch": af_cli.watch, "run": af_cli.run, "undo": af_cli.undo}[caller]
    src = inspect.getsource(fn)
    for node in ast.walk(ast.parse(src)):
        if (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_auth_code_check"):
            arg = node.args[1]
            assert isinstance(arg, ast.Call) and getattr(arg.func, "id", "") == "_auth_code_of", (
                f"{caller} 的授权码实参没有走 _auth_code_of：{ast.dump(arg)[:120]}"
            )
            return
    raise AssertionError(f"{caller} 里没找到 _auth_code_check 调用（闸被摘了？）")


def test_watch_command_still_registered():
    """结构性回归腿：本批实测踩过——helper 插进 `@app.command()` 与 `def watch`
    之间，装饰器把 helper 注册成命令、watch 整个从 CLI 面消失（「No such
    command 'watch'」），而本文件其余判据全绿。这腿钉住三个命令仍在册。
    """
    names = {cmd.name or cmd.callback.__name__ for cmd in af_cli.app.registered_commands}
    assert {"watch", "run", "undo"} <= names, sorted(names)
    assert "_auth_code_of" not in names, "helper 被注册成命令（装饰器插桩位错了）"


def test_sync_compose_never_hardcodes_secrets():
    """② compose 编排：env 只写键名占位，⛔ 真值/真 token 不进仓。"""
    text = (REPO / "docker" / "docker-compose.sync.yml").read_text(encoding="utf-8")
    # 敏感键全部走 ${VAR:-} 占位注入
    for key in ("HA_LONG_LIVED_TOKEN", "AUTOFORGE_AUTH_CODE"):
        assert f"${{{key}:-}}" in text, f"{key} 没走 env 占位注入"
    # 命令行不得出现明文凭据参数（AF-714 的根因形状：docker inspect 一览无余）
    assert "--ha-token" not in text, "命令行不得带 --ha-token（明文凭据面）"
    assert "--auth-code" not in text, "命令行不得带 --auth-code（明文凭据面）"
    # watch 的授权码账本与 api 服务同 store 卷（否则读不到 WebUI 签发的码）
    assert "autoforge-store:/data" in text


def test_sync_compose_guards_restart_shape():
    """② 编排形状钉：watch 是常驻服务，restart 策略与宽限期必须在。"""
    text = (REPO / "docker" / "docker-compose.sync.yml").read_text(encoding="utf-8")
    assert "restart: unless-stopped" in text
    assert "stop_grace_period" in text


def test_preflight_detects_crash_loop():
    """③ preflight：崩循环指纹（restarts>5 且 exit≠0）必须判 FAIL。"""
    text = (REPO / "scripts" / "preflight.sh").read_text(encoding="utf-8")
    assert "崩溃循环" in text, "preflight 没有崩循环检出"
    assert "RestartCount" in text and "ExitCode" in text
    # 阈值判红而不是只打印（旧的 restarts=N 只进 PASS 行）
    assert "-gt" in text or "-gt \"$CRASH_RESTARTS_CAP\"" in text
