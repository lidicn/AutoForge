"""第四期审计 7 条 medium 缺陷的回归测试（AF54 / AF57 / AF58 / AF58b / AF59 / AF64 / AF71c）。

设计原则（与任务书一致）：
- 失败必须如实传播——不能 ok=True 掩盖失败；
- 批量操作单点失败不应拖垮整体——部分成功 + 列明失败项；
- 关键路径必须有日志留痕。
"""
from __future__ import annotations

import json
import logging
import pathlib
from datetime import datetime

import pytest

from autoforge.af_conflict import ConflictArbiter, RequestDecision
from autoforge.af_conflict_audit import ConflictAuditor


# --------------------------------------------------------------------------- #
# 测试替身（与 tests/test_af_conflict_core.py 同口径）
# --------------------------------------------------------------------------- #
class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = float(start)

    def monotonic(self) -> float:
        return self.t

    def now(self) -> datetime:
        return datetime.fromtimestamp(self.t)

    def advance(self, seconds: float) -> None:
        self.t += float(seconds)


class FakeConf:
    def __init__(self, values=None) -> None:
        self.values = dict(values or {})
        self.negatives = []

    def get(self, automation_id: str) -> float:
        return float(self.values.get(automation_id, 0.5))

    def band(self, automation_id: str) -> str:
        return "auto"

    def record_negative(self, automation_id: str) -> None:
        self.negatives.append(automation_id)


def _arbiter(conf=None, clock=None, **kw):
    events, pre, ready = [], [], []
    arb = ConflictArbiter(
        conf or FakeConf(), clock or FakeClock(),
        on_event=events.append, on_preempted=pre.append, on_pending_ready=ready.append,
        **kw,
    )
    return arb, events


def _open_circuit(arb, clock):
    """让 automation X 的熔断进入 open（H 持有锁，X 连续被拒 5 次）。"""
    arb.request(["light.study"], "H", "i-h", "light.turn_on", {})
    for i in range(5):
        arb.request(["light.study"], "X", f"i-{i}", "light.turn_off", {})
    arb.release(["light.study"], "H")
    assert arb.circuit_state("X")["status"] == "open"


# --------------------------------------------------------------------------- #
# AF54：半开试探无超时 ⇒ 回执丢失时无限期拒绝
# --------------------------------------------------------------------------- #
def test_half_open_trial_without_receipt_times_out_and_self_heals():
    conf = FakeConf({"X": 0.5, "H": 0.95})
    clock = FakeClock()
    arb, events = _arbiter(conf, clock, circuit_half_open_timeout=20.0)
    _open_circuit(arb, clock)

    clock.advance(300)                       # open 冷却结束 → 半开放一次试探
    assert arb.request(["light.study"], "X", "i-t1", "light.turn_off", {}) is RequestDecision.ALLOW
    st = arb.circuit_state("X")
    assert st["status"] == "half_open" and st["trial_inflight"] is True

    # 回执丢失：不 release。试探在途期间后续请求被挡（half_open_busy）。
    assert arb.request(["light.study"], "X", "i-t2", "light.turn_off", {}) is RequestDecision.CIRCUIT_OPEN

    # 越过回执超时仍没 release：旧实现这里 trial_inflight 永远为 True ⇒ 永久 CIRCUIT_OPEN。
    clock.advance(25)
    assert arb.request(["light.study"], "X", "i-t3", "light.turn_off", {}) is RequestDecision.CIRCUIT_OPEN
    st = arb.circuit_state("X")
    # 超时自愈：试探被判失败、退回 open，trial_inflight 清掉（不再卡死在 half_open_busy）。
    assert st["status"] == "open" and st["trial_inflight"] is False
    assert any(e.details.get("phase") == "half_open_trial_timeout" for e in events)

    # 有界重试：再走一个冷却周期后，允许新的半开试探（自愈闭环）。
    clock.advance(301)
    assert arb.request(["light.study"], "X", "i-t4", "light.turn_off", {}) is RequestDecision.ALLOW


# --------------------------------------------------------------------------- #
# AF57：隔离失败静默（af_pending._quarantine_corrupt 返回值）
# --------------------------------------------------------------------------- #
def test_quarantine_reports_failure_instead_of_silent_pass(tmp_path, monkeypatch, caplog):
    from autoforge import af_pending

    target = tmp_path / "op1.json"
    target.write_text("not json", encoding="utf-8")

    # 成功隔离返回 True
    assert af_pending._quarantine_corrupt(target) is True
    assert not target.exists()
    assert (tmp_path / "op1.corrupt").exists()

    # 重命名失败时返回 False 并留痕（不再裸 pass）
    target2 = tmp_path / "op2.json"
    target2.write_text("x", encoding="utf-8")

    def boom_rename(self, *_a, **_k):
        raise OSError("permission denied")

    monkeypatch.setattr(pathlib.Path, "rename", boom_rename)
    with caplog.at_level(logging.WARNING, logger="autoforge.af_pending"):
        result = af_pending._quarantine_corrupt(target2)
    assert result is False
    assert target2.exists()                  # 文件留在原位（下轮还能重试）
    assert any("隔离失败" in r.message for r in caplog.records)


# --------------------------------------------------------------------------- #
# AF59：af_conflict_audit 全模块无 logger（落盘失败 / 坏行跳过）
# --------------------------------------------------------------------------- #
def test_audit_persist_failure_is_logged(tmp_path, monkeypatch, caplog):
    import os

    auditor = ConflictAuditor(persist_dir=str(tmp_path / "audit"), autoload=True)

    # _persist 先 os.makedirs 再 open(...)。让建目录抛 OSError ⇒ 落盘分支进 except。
    def boom_makedirs(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(os, "makedirs", boom_makedirs)
    with caplog.at_level(logging.WARNING, logger="autoforge.af_conflict_audit"):
        auditor.record(_event())
    assert any("落盘失败" in r.message for r in caplog.records)


def test_audit_reload_bad_line_is_logged(tmp_path, caplog):
    d = tmp_path / "audit"
    d.mkdir()
    p = d / "conflict_audit.json"
    p.write_text('{"event_id":"ok"}\n{broken json}\n', encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="autoforge.af_conflict_audit"):
        auditor = ConflictAuditor(persist_dir=str(d), autoload=True)
    assert len(auditor.events) == 1          # 好行加载，坏行跳过
    assert any("坏行" in r.message for r in caplog.records)


def _event():
    from autoforge.af_conflict import ConflictEvent
    return ConflictEvent(
        event_id="e1", entity_id="light.study", kind="rejected",
        requester_id="X", holder_id="H", timestamp=0.0, details={},
    )


# --------------------------------------------------------------------------- #
# AF58 / AF58b：start_watch 的 PID 文件写入失败 / 凭据读不出
# --------------------------------------------------------------------------- #
class _FakeProc:
    def __init__(self, pid: int = 4242) -> None:
        self.pid = pid
        self.returncode = None

    def poll(self):
        return None


def _setup_watch(root, monkeypatch, proc=None):
    """复制 test_start_watch_identity 的装配：fake Popen + no-op sleep。"""
    import subprocess
    import time

    monkeypatch.setattr(time, "sleep", lambda _s: None)
    proc = proc or _FakeProc()
    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return proc

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    return captured, proc


def _spawn_my_sidecar(root, captured, monkeypatch):
    """第一次 sleep 时把 sidecar 写成"本次这份"。"""
    import time
    mine_written = {}

    def spawn(_s):
        if "mine" in mine_written:
            return
        cmd = captured["cmd"]
        mine = cmd[2]
        mine_written["mine"] = mine
        (root / "watch.lock.info").write_text(
            json.dumps({"owner": "me-16-abc", "graph": mine, "acquired_at": "t"}),
            encoding="utf-8",
        )

    monkeypatch.setattr(time, "sleep", spawn)


IR = {"automations": [{"id": "a1", "name": "x"}]}


def test_pid_file_write_failure_is_not_reported_as_success(tmp_path, monkeypatch, caplog):
    from autoforge import af_service as svc

    root = tmp_path
    (root / "watch.log").write_text("", encoding="utf-8")
    captured, _proc = _setup_watch(root, monkeypatch)
    _spawn_my_sidecar(root, captured, monkeypatch)

    # 让 PID 文件原子写失败
    from autoforge.af_atomic import atomic_write_text as _real
    import autoforge.af_service as svc_mod

    def boom(path, *_a, **_k):
        if str(path).endswith("watch.pid"):
            raise OSError("no permission")
        return _real(path, *_a, **_k)

    monkeypatch.setattr(svc_mod, "atomic_write_text", boom)

    with caplog.at_level(logging.WARNING, logger="autoforge.service"):
        res = svc.start_watch(IR, store_root=str(root), dry_live=True)

    # watcher 实际已注册，但 PID 文件写不进去 ⇒ 不能裸 ok=True
    assert res["ok"] is False, res
    assert res["reason"] == "pid_file_write_failed", res
    assert res["pid_file_written"] is False
    assert res["pid"] == 4242                    # 仍如实告知那个孤儿进程的 pid
    assert any("PID 文件" in r.message for r in caplog.records)


def test_corrupt_credentials_file_is_logged_not_silent(tmp_path, monkeypatch, caplog):
    from autoforge import af_service as svc

    root = tmp_path
    (root / "watch.log").write_text("", encoding="utf-8")
    (root / "credentials.json").write_text("{ broken json", encoding="utf-8")
    captured, _proc = _setup_watch(root, monkeypatch)
    _spawn_my_sidecar(root, captured, monkeypatch)

    with caplog.at_level(logging.WARNING, logger="autoforge.service"):
        res = svc.start_watch(IR, store_root=str(root), dry_live=True)

    assert res["ok"] is True                       # dry_live 不需要真令牌，照常启动
    assert res["credentials"] == "unreadable", res   # 但凭据状态必须可见
    assert any("凭据文件存在但读不出" in r.message for r in caplog.records)


# --------------------------------------------------------------------------- #
# AF64：批量启停部分失败要列明失败项、整体 ok=False
# --------------------------------------------------------------------------- #
def test_enable_by_tag_reports_per_item_failures(tmp_path, monkeypatch):
    from autoforge.af_ir import Graph, load_automation
    from autoforge.af_service import enable_by_tag
    from autoforge.af_store import GraphStore

    def _g(aid):
        return Graph([load_automation({
            "ir_version": "0.2.1", "id": aid, "name": aid, "version": 1,
            "mode": "single", "enabled": True,
            "nodes": [{"id": "n1", "kind": "pass"}],
            "edges": [],
        })])

    store = GraphStore(str(tmp_path / "store"))
    store.save(_g("g1"), "g1", tags=["night"])
    store.save(_g("g2"), "g2", tags=["night"])

    # g2 在落盘阶段抛错（模拟单条 save 失败）
    real_save = store.save

    def flaky_save(graph, name, *a, **k):
        if name == "g2":
            raise OSError("disk full on g2")
        return real_save(graph, name, *a, **k)

    monkeypatch.setattr(store, "save", flaky_save)

    res = enable_by_tag(store, "night", False, allow_bulk=True)
    assert res["ok"] is False                      # 有失败项 ⇒ 整体不能 ok
    assert [x["name"] for x in res["affected"]] == ["g1"]
    assert [x["name"] for x in res["failed"]] == ["g2"]
    assert res["failed"][0]["reason"].startswith("save_failed")


def test_enable_by_tag_ok_true_when_all_succeed(tmp_path):
    from autoforge.af_ir import Graph, load_automation
    from autoforge.af_service import enable_by_tag
    from autoforge.af_store import GraphStore

    def _g(aid):
        return Graph([load_automation({
            "ir_version": "0.2.1", "id": aid, "name": aid, "version": 1,
            "mode": "single", "enabled": True, "nodes": [{"id": "n1", "kind": "pass"}],
            "edges": [],
        })])

    store = GraphStore(str(tmp_path / "store"))
    store.save(_g("g1"), "g1", tags=["night"])
    res = enable_by_tag(store, "night", False, allow_bulk=True)
    assert res["ok"] is True
    assert res["failed"] == []


# --------------------------------------------------------------------------- #
# AF71c：list_watches 单条坏 sidecar 不拖垮整表
# --------------------------------------------------------------------------- #
def test_list_watches_skips_malformed_sidecar(tmp_path, caplog):
    from autoforge import af_service as svc

    root = tmp_path
    # 一条合法 sidecar（指向不存在的 IR 也没关系：_graph_summary 已容错）
    (root / "watch.lock.info").write_text(
        json.dumps({"owner": "host-1-abc12345", "graph": "/tmp/x/deployed_ir.json",
                    "acquired_at": "t", "ha_url": "http://h"}),
        encoding="utf-8",
    )
    # 子目录里一条形态损坏的 sidecar：合法 JSON 但不是 dict
    sub = root / "other"
    sub.mkdir()
    (sub / "watch.lock.info").write_text("[1, 2, 3]", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="autoforge.service"):
        res = svc.list_watches(store_root=str(root))

    assert res["ok"] is True
    assert res["total"] == 1                       # 坏 sidecar 被跳过，没把整表打死
    assert res["watches"][0]["owner"] == "host-1-abc12345"
    assert any("无法解析的 sidecar" in r.message for r in caplog.records)

