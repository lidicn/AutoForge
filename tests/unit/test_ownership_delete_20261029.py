"""归属可见性 + 自主删除 + 启用后监听对账（出资人裁定 2026-10-29）。

裁定原文：普通 agent「自己写的自己的自动化，自己可以删可以改，不能动其他 agent 的
【也看不到】」；开发者 agent「可以管理全部自动化，能查能改能删」。
方案 A（R4-4 假武装）：启用后必须对账「有没有人监听」，起不动要照实回原因。
"""

from __future__ import annotations

import json

from autoforge.af_mcp import dispatch
from autoforge.af_service import approve_pending, submit_pending
from autoforge.af_draft import draft_intent, get_staged
from autoforge.af_spec import graph_to_raw
from autoforge.af_store import GraphStore
from homesdk.adm.errors import ADM_ERR_AUTH_REQUIRED, ADM_ERR_PAYLOAD_INVALID

A = {"subject": "agent-a", "scopes": ["read", "write", "live"]}
B = {"subject": "agent-b", "scopes": ["read", "write", "live"]}
OWNER = {"subject": "owner", "scopes": ["read", "write", "live", "admin"]}


def _ir(name: str) -> dict:
    ref = draft_intent({"name": name, "when": {"entity": "binary_sensor.m", "to": "on"},
                        "do": {"action": "开灯", "target": "light.x"}}, catalog=None)["ref"]
    return {"automations": graph_to_raw(get_staged(ref)["graph"])}


def _call(tool, args, who, store):
    content, _is_err = dispatch(tool, args, store, who)
    return json.loads(content[0]["text"])


def _seed_two(store: GraphStore) -> None:
    for name, who in (("arch-a", A), ("arch-b", B)):
        r = submit_pending(store, "af_save", {"ir": _ir(name), "name": name,
                                              "owner": who["subject"]},
                           submitted_by=who["subject"], authenticated_subject=who["subject"])
        approve_pending(store, r["pending"], reviewer="test")


# ── 看不到别人的（裁定要的是「看不到」，不是「看到但改不了」）──────────

def test_list_scoped_to_own_archives(tmp_path):
    store = GraphStore(str(tmp_path / "s"))
    _seed_two(store)
    la = _call("af_list_graphs", {}, A, store)
    assert [i["name"] for i in la["items"]] == ["arch-a"]
    assert la["hidden_count"] == 1, "必须让人知道自己看的不是全量"
    assert la["scope"] == "owner:agent-a"
    lb = _call("af_list_graphs", {}, B, store)
    assert [i["name"] for i in lb["items"]] == ["arch-b"]


def test_owner_face_sees_everything(tmp_path):
    store = GraphStore(str(tmp_path / "s"))
    _seed_two(store)
    lo = _call("af_list_graphs", {}, OWNER, store)
    assert {i["name"] for i in lo["items"]} == {"arch-a", "arch-b"}
    assert "hidden_count" not in lo, "owner 面没有隐藏项，不许凭空造一个 0 让人以为有隐藏"


def test_read_of_others_is_indistinguishable_from_absent(tmp_path):
    """⛔ 不能回「无权查看」：那等于确认「这条存在、只是不是你的」，
    把列表上刻意藏掉的信息从错误消息里漏回去。"""
    store = GraphStore(str(tmp_path / "s"))
    _seed_two(store)
    mine = _call("af_get_graph", {"name": "arch-a"}, A, store)
    theirs = _call("af_get_graph", {"name": "arch-b"}, A, store)
    ghost = _call("af_get_graph", {"name": "no-such-archive"}, A, store)
    assert mine["ok"] is True
    assert theirs["ok"] is False
    assert (theirs.get("error") or {}).get("code") == "ARCHIVE_NOT_FOUND"
    assert (ghost.get("error") or {}).get("code") == "ARCHIVE_NOT_FOUND"
    assert theirs.get("code") == ghost.get("code"), "与「不存在」同形才算看不到"


# ── 自己的能删、别人的删不掉、删除可恢复 ──────────────────────────────

def test_delete_own_archive(tmp_path):
    store = GraphStore(str(tmp_path / "s"))
    _seed_two(store)
    out = _call("af_delete_graph", {"name": "arch-a", "note": "测试清理"}, A, store)
    assert out["ok"] is True and out["deleted"] is True
    assert out["restorable"] is True and out["quarantined_as"].startswith("arch-a--")
    assert store.list_quarantined(), "隔离区必须看得见，⛔ 删除不能做成第二个黑箱"
    assert [i["name"] for i in _call("af_list_graphs", {}, A, store)["items"]] == []


def test_delete_other_refused_without_touching(tmp_path):
    store = GraphStore(str(tmp_path / "s"))
    _seed_two(store)
    out = _call("af_delete_graph", {"name": "arch-b"}, A, store)
    assert out["ok"] is False and out["code"] == ADM_ERR_AUTH_REQUIRED
    assert (out.get("error") or {}).get("code") == "FORBIDDEN"
    assert store.list_quarantined() == [], "被拒时一个字节都不许动"


def test_delete_absent_says_nothing_was_removed(tmp_path):
    store = GraphStore(str(tmp_path / "s"))
    out = _call("af_delete_graph", {"name": "ghost"}, OWNER, store)
    assert out["ok"] is False and out["code"] == ADM_ERR_PAYLOAD_INVALID
    assert "没删任何东西" in str(out), "删除类回执必须说清什么都没动"


def test_owner_can_delete_others(tmp_path):
    store = GraphStore(str(tmp_path / "s"))
    _seed_two(store)
    out = _call("af_delete_graph", {"name": "arch-b", "note": "清理测试残留"}, OWNER, store)
    assert out["ok"] is True, "开发者 agent（owner 面）可管理全部——裁定原文"


def test_delete_is_quarantine_not_rmtree(tmp_path):
    """可恢复性判据：归档目录消失，但内容完整躺在隔离区里（误删能捞回）。"""
    store = GraphStore(str(tmp_path / "s"))
    _seed_two(store)
    src_dir = store._dir("arch-a")
    assert src_dir.is_dir()
    files_before = sorted(p.name for p in src_dir.rglob("*") if p.is_file())
    _call("af_delete_graph", {"name": "arch-a"}, A, store)
    assert not src_dir.exists(), "原目录必须没了"
    trash = store.root / GraphStore.TRASH_DIR
    moved = sorted(p for p in trash.iterdir() if p.is_dir())
    files_after = sorted(p.name for p in moved[0].rglob("*") if p.is_file())
    assert files_after == files_before, "隔离是搬家不是销毁，文件一个不许少"


# ── 方案 A：启用后必须有人监听 ────────────────────────────────────────

def test_enable_reports_watcher_reconciliation(tmp_path, monkeypatch):
    import autoforge.af_service as svc

    monkeypatch.setattr(svc, "list_watches", lambda root=None: {"ok": True, "watches": []})
    monkeypatch.setattr(svc, "_build_merged_ir", lambda store: None)
    store = GraphStore(str(tmp_path / "s"))
    # AF-401 后启用闸在服务层——这条腿要量的是对账形状，先给它一枚真码过闸
    # （不是给拒收腿配码：那才是致盲哨兵）。
    from pathlib import Path as _P
    from autoforge.af_auth import AuthCodeStore
    _rec = AuthCodeStore(_P(store.root) / ".auth" / "auth_codes.json").create("short", ttl_minutes=30)
    out = svc.enable_by_tag(store, "any", True, allow_bulk=True,
                            auth_code=getattr(_rec, "code", None) or _rec["code"])
    rec = out.get("watch_reconcile")
    assert rec is not None, "启用回执必须带监听对账结果"
    assert rec["action"] in {"reused", "started", "refused", "failed",
                             "probe_failed", "none_to_watch"}, rec


def test_disable_does_not_touch_watcher(tmp_path):
    import autoforge.af_service as svc

    store = GraphStore(str(tmp_path / "s"))
    out = svc.enable_by_tag(store, "any", False, allow_bulk=True)
    assert out["watch_reconcile"] is None, "禁用不该去动监听（也不该假装动了）"


def test_reconcile_refuses_loudly_when_lock_held(tmp_path, monkeypatch):
    """起不动必须回 refused + 原因——静默就等于把「假武装」换成「假修复」。"""
    import autoforge.af_service as svc

    monkeypatch.setattr(svc, "list_watches", lambda root=None: {"watches": []})
    merged = {"automations": [{"id": "x", "name": "n", "version": 1, "mode": "single",
                               "nodes": [], "edges": []}]}
    monkeypatch.setattr(svc, "_build_merged_ir", lambda store: merged)
    # DCD 2026-10-30 拆层后 _ensure_watcher 走免闸实现体（enable 的码闸在先，码只收一次）
    monkeypatch.setattr(svc, "_start_watch_impl",
                        lambda **kw: {"ok": False, "reason": "coord_lock_held_by_other"})
    r = svc._ensure_watcher(GraphStore(str(tmp_path / "s")), enabled=True)
    assert r["action"] == "refused"
    assert "coord_lock" in str(r["reason"])


def test_reconcile_reuses_live_watcher(tmp_path, monkeypatch):
    """已有活监听时不许重启它（会打断正在跑的实例）。"""
    import autoforge.af_service as svc

    calls: list = []
    monkeypatch.setattr(svc, "list_watches",
                        lambda root=None: {"watches": [{"alive": True, "pid": 42}]})
    monkeypatch.setattr(svc, "start_watch",
                        lambda **kw: calls.append("started") or {"ok": True})
    r = svc._ensure_watcher(GraphStore(str(tmp_path / "s")), enabled=True)
    assert r["action"] == "reused" and calls == [], "复用即不得再起"
