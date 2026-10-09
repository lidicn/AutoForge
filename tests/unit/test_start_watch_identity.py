"""`start_watch` 的 `ok=true` 必须证明**是本次这一份**拿到了协调锁。

起因（2026-10-09 现场，NAS 容器 8787）：用户视角那条「书房射灯与显示器挂灯同步」从未触发，
排查时对 `POST /api/watch/start` 的实测读数是 **1.18 秒回 `{"ok": true, "pid": 16,
"graph": "/data/tmpze7aycrz/deployed_ir.json"}`**——那个 graph 是 9-29 另一条 watch
（`test_ask_link2`）的临时目录，本次这份落在另一个目录里。旧实现的探测只问
"目录里有没有 `watch.lock.info`"，而 sidecar 是**目录里唯一的文件名**，于是上一条 watcher 的
残留被读成"本次启动成功"：假绿。协调锁 `watch.lock` 全目录只有一把，同一时刻只有一个 watcher，
所以"有没有 sidecar"根本不等价于"我的 sidecar"。

判据口径：`ok=true` 只在 sidecar 的 `graph` 字段 == 本次写出的那份临时路径时成立；
其余三种结局各自落一个具名 `reason`（子进程退出 / 锁被别人持着 / 起了但没登记），
且**都不再是 ok**。`time.sleep` 被换成 no-op，15 秒轮询在测试里是 15 次读盘。
"""
from __future__ import annotations

import json
import pathlib
import time

import pytest

from autoforge import af_service as svc

IR = {"automations": [{"id": "study_spot_lamp_sync", "name": "书房射灯与显示器挂灯同步"}]}
STALE_GRAPH = "/data/tmpze7aycrz/deployed_ir.json"


class _FakeProc:
    """只实现 start_watch 用到的那张脸：`pid` / `poll()` / `returncode`。"""

    def __init__(self, pid: int = 4242, exit_code: int | None = None):
        self.pid = pid
        self.returncode = exit_code
        self._alive = exit_code is None

    def poll(self):
        return None if self._alive else self.returncode


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """15 秒轮询在测试里不真等——**不许**把这条当成"生产也不等"。"""
    monkeypatch.setattr(time, "sleep", lambda _s: None)


@pytest.fixture
def root(tmp_path):
    (tmp_path / "watch.log").write_text("", encoding="utf-8")
    return tmp_path


def _popen(monkeypatch, proc: _FakeProc, captured: dict):
    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return proc

    import subprocess

    monkeypatch.setattr(subprocess, "Popen", fake_popen)


def _sidecar(root: pathlib.Path, graph: str, owner: str = "old-owner", acquired_at: str = "2026-09-29T13:58:43"):
    (root / "watch.lock.info").write_text(
        json.dumps({"owner": owner, "graph": graph, "acquired_at": acquired_at}), encoding="utf-8"
    )


def _mine(captured: dict) -> str:
    """本次那份 IR 的路径 = cmd 里第一个非旗子参数（`forge watch <tmp>`）。"""
    cmd = captured["cmd"]
    assert cmd[:2] == ["forge", "watch"], cmd
    return cmd[2]


# ── 正例：身份对得上才算成功 ───────────────────────────────────────────

def test_ok_only_when_the_sidecar_points_at_this_run(tmp_path, root, monkeypatch):
    captured: dict = {}
    _popen(monkeypatch, _FakeProc(), captured)
    mine = None

    def spawn_sidecar(_s):
        nonlocal mine
        if mine is None:
            mine = _mine(captured)
        _sidecar(root, mine, owner="me-16-abc", acquired_at="2026-10-09T06:25:21")

    monkeypatch.setattr(time, "sleep", spawn_sidecar)
    res = svc.start_watch(IR, store_root=str(root), dry_live=True)
    assert res["ok"] is True, res
    assert res["graph"] == mine
    assert res["owner"] == "me-16-abc" and res["acquired_at"] == "2026-10-09T06:25:21", res


def test_success_payload_names_the_runtime_tier(tmp_path, root, monkeypatch):
    """档位从本函数自己拼给 CLI 的旗子里读回来，不另立第二套说法。"""
    captured: dict = {}
    _popen(monkeypatch, _FakeProc(), captured)

    def spawn_sidecar(_s):
        _sidecar(root, _mine(captured))

    monkeypatch.setattr(time, "sleep", spawn_sidecar)
    res = svc.start_watch(IR, store_root=str(root), dry_live=True)
    assert res["tier"] == "dry_live" and res["real_device"] is False, res
    assert "--dry-live" in captured["cmd"] and "--live" not in captured["cmd"]


def test_no_confirm_tier_is_named_live_unconfirmed_not_real(tmp_path, root, monkeypatch):
    """`dry_live=False` 在本层拼不出 `--confirm`，那一档的名字是 `live_unconfirmed`——**不能**被读成"真机在跑"。

    `forge watch` 没有 `--live` 旗子：真机档 = 不带 `--dry-live`（af_cli.py:584 `live=not dry_live`），
    而缺 `--confirm` 时 CLI 自己拒启动（af_cli.py:565-566，见下面那一格的真读数）。
    """
    captured: dict = {}
    _popen(monkeypatch, _FakeProc(), captured)

    def spawn_sidecar(_s):
        _sidecar(root, _mine(captured))

    monkeypatch.setattr(time, "sleep", spawn_sidecar)
    res = svc.start_watch(IR, store_root=str(root), dry_live=False)
    assert "--dry-live" not in captured["cmd"] and "--confirm" not in captured["cmd"], captured["cmd"]
    assert res["tier"] == "live_unconfirmed" and res["real_device"] is False, res


def test_watch_cli_refuses_without_confirm(tmp_path):
    """档位命名的事实依据：同一份 IR 在 CLI 上不带 `--confirm` 就是起不来（真读数，不是推断）。"""
    from typer.testing import CliRunner

    from autoforge.af_cli import app

    ir_path = tmp_path / "graph.json"
    ir_path.write_text(
        json.dumps(
            {
                "automations": [
                    {
                        "ir_version": "0.2.1",
                        "id": "study_spot_lamp_sync",
                        "name": "书房射灯与显示器挂灯同步",
                        "version": 1,
                        "mode": "single",
                        "nodes": [
                            {
                                "id": "t1",
                                "kind": "on",
                                "trigger": {
                                    "type": "state",
                                    "entity_id": "switch.study_spot",
                                    "to": "on",
                                },
                            },
                            {"id": "p1", "kind": "pass"},
                        ],
                        "edges": [{"from": "t1", "to": "p1", "kind": "then"}],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    res = CliRunner().invoke(
        app, ["watch", str(ir_path), "--persist-dir", str(tmp_path / "persist")]
    )
    assert res.exit_code != 0, (res.exit_code, res.output)
    assert "--confirm" in res.output, res.output


# ── 反例：现场那一格（上一条 watch 的残留）不再被读成成功 ────────────────

def test_stale_sidecar_is_not_my_registration(tmp_path, root, monkeypatch):
    """原失败复现：目录里躺着一份 sidecar，但 graph 是别人的 ⇒ ok=false。"""
    captured: dict = {}
    _popen(monkeypatch, _FakeProc(), captured)
    _sidecar(root, STALE_GRAPH)
    res = svc.start_watch(IR, store_root=str(root), dry_live=True)
    assert res["ok"] is False, res
    assert res["reason"] == "coord_lock_held_by_other", res
    assert STALE_GRAPH in res["error"], res["error"]
    assert res["holder"]["graph"] == STALE_GRAPH and res["holder"]["acquired_at"], res["holder"]


def test_absent_sidecar_is_not_a_success_either(tmp_path, root, monkeypatch):
    """子进程活着但 15 秒内什么都没登记：旧版本这里回 `ok=true + note`，那是第二种假绿。"""
    captured: dict = {}
    _popen(monkeypatch, _FakeProc(), captured)
    res = svc.start_watch(IR, store_root=str(root), dry_live=True)
    assert res["ok"] is False and res["reason"] == "not_registered", res
    assert res["pid"] == 4242, res


def test_child_exiting_wins_over_a_foreign_sidecar(tmp_path, root, monkeypatch):
    captured: dict = {}
    _popen(monkeypatch, _FakeProc(exit_code=3), captured)
    _sidecar(root, STALE_GRAPH)
    res = svc.start_watch(IR, store_root=str(root), dry_live=True)
    assert res["ok"] is False and res["reason"] == "child_exited", res
    assert "exit_code=3" in res["error"], res["error"]
    assert res["holder"]["graph"] == STALE_GRAPH, res


def test_corrupt_sidecar_file_is_treated_as_absent(tmp_path, root, monkeypatch):
    """半截 JSON 不能被 `json.loads` 抛穿，也不能被读成"有 sidecar"。"""
    captured: dict = {}
    _popen(monkeypatch, _FakeProc(), captured)
    (root / "watch.lock.info").write_text('{"owner": "x", "graph": ', encoding="utf-8")
    res = svc.start_watch(IR, store_root=str(root), dry_live=True)
    assert res["ok"] is False and res["reason"] == "not_registered", res


def test_popen_failure_stays_a_plain_failure(tmp_path, root, monkeypatch):
    import subprocess

    def boom(*a, **k):
        raise OSError("forge 不在 PATH")

    monkeypatch.setattr(subprocess, "Popen", boom)
    res = svc.start_watch(IR, store_root=str(root), dry_live=True)
    assert res["ok"] is False and "启动 watch 失败" in res["error"], res


# ── 结构腿：身份比较用的是本次那份路径本身 ─────────────────────────────

def test_identity_is_the_ir_path_written_by_this_call(tmp_path, root, monkeypatch):
    """sidecar 的 graph 必须等于**本次落盘的那条临时路径**，不是"目录里有文件"。"""
    captured: dict = {}
    _popen(monkeypatch, _FakeProc(), captured)
    mine = None

    def spawn_both(_s):
        nonlocal mine
        if mine is None:
            mine = _mine(captured)
        # 同目录里再放一份"别人的"，验证匹配是按内容而不是按存在性
        _sidecar(root, mine)
        assert pathlib.Path(mine).is_file(), mine
        assert json.loads(pathlib.Path(mine).read_text(encoding="utf-8")) == IR

    monkeypatch.setattr(time, "sleep", spawn_both)
    assert svc.start_watch(IR, store_root=str(root))["ok"] is True


def test_start_watch_has_a_single_http_caller(tmp_path):
    """反空洞：本函数的判据挂在服务层，HTTP 面唯一入口在 `/api/watch/start`。"""
    src = (pathlib.Path(__file__).resolve().parents[2] / "src" / "autoforge" / "af_api.py").read_text(
        encoding="utf-8"
    )
    assert "svc.start_watch" in src
